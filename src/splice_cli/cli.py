"""splice — thin CLI shell over the library, meant for a coding agent to drive.

Splice's login is Turnstile-gated, so this CLI can't log you in — a human does
that once in a browser. Everything here is built around that reality: commands
that need a session will, when there isn't a valid one, print exactly what the
human must do (the `_needs_human` handler) and exit 2, instead of failing
opaquely. An agent keys off that exit code to hand back to the person.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import budget as budget_store
from . import mcp_auth
from . import mcp_session as mcp_session_store
from . import render
from . import session as session_store
from .auth import AuthError, NoSessionError, Session, SessionExpired, extract_session
from .client import SampleNotFoundError, SpliceClient
from .config import Settings
from .mcp_auth import McpAuthError, McpSession
from .mcp_client import McpClient, McpRateLimitError

app = typer.Typer(add_completion=False, help="Unofficial Splice automation CLI.")
console = Console()


_VALID_BACKENDS = ("mcp", "graphql")


def _validate_backend(backend: str) -> None:
    """download/buy's `--backend` only accepts "mcp"/"graphql" — anything
    else (a typo like `--backend grapql`) previously fell through to the
    `mcp` path silently, since the routing logic below only ever checks
    `backend == "graphql"`. Exits 1 (an ordinary usage error) rather than
    letting click's own Choice validation exit 2 — that code is reserved
    here for "a human auth step is needed," not a CLI typo.
    """
    if backend not in _VALID_BACKENDS:
        console.print(
            f"[red]--backend {backend!r} isn't valid — must be one of "
            f"{', '.join(_VALID_BACKENDS)}.[/]"
        )
        raise typer.Exit(1)

_LOGIN_STEPS = (
    "1. Open [bold]splice.com[/] in your normal browser and log in "
    "(pass the 'Verify you are human' check by hand).\n"
    "2. Run [bold]splice login[/] to lift that session.\n"
    "   Add [bold]--browser chrome[/] (or firefox/brave/edge) to pick one."
)


def _needs_human(err: AuthError) -> None:
    """Render a human-actionable handoff and exit non-zero (code 2)."""
    console.print(
        Panel(
            f"[yellow]{err}[/]\n\n{_LOGIN_STEPS}",
            title="[red]Human step needed[/]",
            border_style="red",
        )
    )
    raise typer.Exit(code=2)


def _access_token_expired(sess: Session) -> bool:
    if not sess.access_token_expiry:
        return False  # unknown — don't force a refresh over a guess
    return sess.access_token_expiry <= _dt.datetime.now().timestamp()


def _session(settings: Settings, *, browser: str = "auto", refresh: bool = False) -> Session:
    """Load the saved session, or lift a fresh one from a browser.

    Auto-refreshes when the cached access token has already expired (~1hr
    lifetime — this bit the user repeatedly before this existed) rather than
    handing back a token that's guaranteed to fail the next API call. This is
    still just the "bridge" operation (re-lifting cookies already sitting in
    the browser), not a fresh human step — if the browser's own copy is ALSO
    stale, the subsequent call still surfaces SessionExpired normally.
    """
    if not refresh and settings.session_path.exists():
        sess = session_store.load(settings.session_path)
        if not _access_token_expired(sess):
            return sess
        console.print("[dim]Cached access token expired — re-lifting from browser...[/]")
    sess = extract_session(browser)
    session_store.save(sess, settings.session_path)
    return sess


def _mcp_needs_human(err: McpAuthError) -> None:
    """MCP's equivalent of _needs_human — a distinct instruction set since
    the fix is `splice login --mcp`, not the cookie-lifting `splice login`.
    """
    console.print(
        Panel(
            f"[yellow]{err}[/]\n\nRun [bold]splice login --mcp[/] to (re)authorize "
            "the official Splice MCP — it'll print a URL to open in your browser.",
            title="[red]Human step needed (MCP)[/]",
            border_style="red",
        )
    )
    raise typer.Exit(code=2)


def _mcp_call(settings: Settings, fn):
    """Load the MCP session, open a client, and run `fn(client)` — the
    shared shape of every MCP command branch (search/download/buy/stack *),
    translating exceptions into the clean handoffs/exit codes this CLI
    otherwise gives (0=ok, 1=ordinary failure, 2=human-step-needed):

    - McpAuthError -> human handoff, exit 2 (`_mcp_needs_human`).
    - McpRateLimitError -> report the message, exit 2.
    - plain RuntimeError -> an ordinary tool error (e.g. create_stack
      rejecting a one-shot uuid) — report the message, exit 1.

    Never swallows: the underlying message always reaches the user. Order
    matters — McpAuthError and McpRateLimitError are both RuntimeError
    subclasses, so they're caught first.
    """
    try:
        mcp_sess = _mcp_session(settings)
        with McpClient(mcp_sess) as client:
            return fn(client)
    except McpAuthError as err:
        _mcp_needs_human(err)
    except McpRateLimitError as err:
        console.print(f"[red]{err}[/]")
        raise typer.Exit(code=2)
    except RuntimeError as err:
        console.print(f"[red]{err}[/]")
        raise typer.Exit(code=1)


def _mcp_session(settings: Settings) -> McpSession:
    """Load the saved MCP session, auto-refreshing an expired access token.

    Unlike the GraphQL path's `_session`, refreshing here never risks
    disrupting the user's actual splice.com browser login — this OAuth
    refresh token is scoped to this client only (see mcp_auth.py).
    """
    if not settings.mcp_session_path.exists():
        raise McpAuthError("No MCP session yet.")
    sess = mcp_session_store.load(settings.mcp_session_path)
    if sess.expired():
        console.print("[dim]MCP access token expired — refreshing...[/]")
        sess = mcp_auth.refresh(sess)
        mcp_session_store.save(sess, settings.mcp_session_path)
    return sess


def _print_expiry(sess: Session) -> None:
    now = _dt.datetime.now().timestamp()

    # The access token (Bearer, from _splice_token_prod) gates anything
    # user-scoped — licensed-sample checks, download. It's short-lived
    # (~1hr), unlike cf_clearance, and is what actually needs refreshing.
    if sess.access_token_expiry:
        left_min = (sess.access_token_expiry - now) / 60
        if left_min < 0:
            console.print(
                "[red]⚠ Access token expired — re-run `splice login` "
                "(no need to re-log-in in the browser, just re-lift).[/]"
            )
        elif left_min < 10:
            console.print(f"[yellow]⚠ Access token expires in ~{left_min:.0f} min — re-run `splice login` soon.[/]")
        else:
            console.print(f"[dim]Access token good for ~{left_min:.0f} more min.[/]")

    # cf_clearance lasts ~1 year and only matters if Cloudflare clears it
    # early; worth a distinct, quieter warning.
    if sess.clearance_expiry:
        days = (sess.clearance_expiry - now) / 86400
        if days < 0:
            console.print("[red]⚠ Cloudflare clearance already expired — re-login in the browser.[/]")
        elif days < 2:
            console.print(f"[yellow]⚠ Cloudflare clearance expires in ~{days:.1f} days.[/]")


@app.command()
def login(
    browser: str = typer.Option("auto", "--browser", "-b", help="auto|firefox|chrome|chromium|brave|edge"),
    mcp: bool = typer.Option(
        False, "--mcp",
        help="Authorize the official Splice MCP (OAuth) instead of lifting a browser session.",
    ),
    port: int = typer.Option(
        mcp_auth.DEFAULT_REDIRECT_PORT, "--port",
        help="(--mcp only) loopback port for the OAuth redirect — pick another "
             "if the default is already in use (e.g. another login in progress).",
    ),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Log in. Default: lift a cookie session from a browser (GraphQL path).

    --mcp: authorize the official Splice MCP server instead — a standard
    OAuth flow (dynamic client registration + PKCE), the default backend for
    search/download/stacks. Both logins are independent; run each once.
    """
    settings = Settings.load(env_file)
    if mcp:
        _login_mcp(settings, redirect_port=port)
        return
    try:
        sess = _session(settings, browser=browser, refresh=True)
    except AuthError as err:
        _needs_human(err)
    console.print(
        f"[green]✓ Session lifted from {sess.source} "
        f"({len(sess.cookies)} cookies) → {settings.session_path}[/]"
    )
    _print_expiry(sess)


def _login_mcp(settings: Settings, *, redirect_port: int = mcp_auth.DEFAULT_REDIRECT_PORT) -> None:
    def _print_url(url: str) -> None:
        console.print(
            Panel(
                f"Open this URL in your browser to authorize splice-cli:\n\n[bold blue]{url}[/]\n\n"
                "[dim]Must be the same machine — the redirect is a localhost callback.[/]",
                title="MCP login",
            )
        )

    # Reuse a previously-registered OAuth client_id if one exists, instead of
    # calling register_client() again on every re-login — repeated dynamic
    # registration otherwise sprawls a new server-side client per login with
    # no cleanup path. Best-effort: any problem reading the old session just
    # falls back to a fresh registration (mcp_auth.login's default).
    existing_client_id: str | None = None
    if settings.mcp_session_path.exists():
        try:
            existing_client_id = mcp_session_store.load(settings.mcp_session_path).client_id
        except Exception:  # noqa: BLE001 — corrupt/partial old session file; just re-register
            existing_client_id = None

    try:
        sess = mcp_auth.login(
            client_id=existing_client_id, redirect_port=redirect_port, on_url=_print_url,
        )
    except McpAuthError as err:
        console.print(f"[red]{err}[/]")
        raise typer.Exit(1)
    mcp_session_store.save(sess, settings.mcp_session_path)
    left_min = (sess.access_expiry - _dt.datetime.now().timestamp()) / 60
    console.print(f"[green]✓ MCP session saved → {settings.mcp_session_path}[/]")
    console.print(f"[dim]Access token good for ~{left_min:.0f} more min (auto-refreshes after).[/]")


def _print_mcp_status(settings: Settings) -> None:
    """MCP counterpart to the GraphQL live probe below — reports whether an
    MCP session exists and its access token's local expiry.

    Deliberately NO network call: an MCP health check must never risk a
    download/buy-adjacent side effect (spend or rate-limit consumption) just
    to answer "is this set up" — `McpSession.expired()` is a cheap,
    zero-network timestamp comparison, so that's the ceiling of what this
    reports (it can't tell you the server has actually revoked the token).
    """
    console.print()
    if not settings.mcp_session_path.exists():
        console.print(
            "[yellow]No MCP session — run `splice login --mcp` to enable the "
            "default search/download/buy/stack backend.[/]"
        )
        return
    try:
        mcp_sess = mcp_session_store.load(settings.mcp_session_path)
    except Exception as err:  # noqa: BLE001 — a corrupt/unreadable file; report, don't crash status
        console.print(f"[red]MCP session file at {settings.mcp_session_path} is unreadable: {err}[/]")
        return
    console.print(
        f"MCP session at {settings.mcp_session_path} "
        "[dim](local expiry check only — no network call made)[/]"
    )
    left_min = (mcp_sess.access_expiry - _dt.datetime.now().timestamp()) / 60
    if mcp_sess.expired():
        console.print(
            "[yellow]⚠ MCP access token expired (local check) — this "
            "auto-refreshes on the next MCP call; run `splice login --mcp` "
            "if that refresh ever fails.[/]"
        )
    elif left_min < 10:
        console.print(f"[yellow]⚠ MCP access token expires in ~{left_min:.0f} min.[/]")
    else:
        console.print(f"[dim]MCP access token good for ~{left_min:.0f} more min.[/]")


@app.command()
def status(env_file: Path = typer.Option(None, "--env")) -> None:
    """Check whether there's a valid session, and who it belongs to.

    Reports both backends: the GraphQL session (with a live library probe)
    and the MCP session (a cheap local token-expiry check only — no network
    call, so this can't spend a credit or trip the rate limit). Missing MCP
    setup alone doesn't fail the command — GraphQL-only usage is valid; only
    a GraphQL problem that needs a human step exits non-zero.
    """
    settings = Settings.load(env_file)
    if not settings.session_path.exists():
        _print_mcp_status(settings)
        _needs_human(NoSessionError("No saved session yet."))
    sess = session_store.load(settings.session_path)
    console.print(f"Session from [cyan]{sess.source}[/] at {settings.session_path}")
    _print_expiry(sess)
    # Live probe: a cheap search confirms the cookies still work.
    try:
        with SpliceClient(sess) as client:
            r = client.search("drums", limit=1)
        console.print(f"[green]✓ Live check OK — library reachable ({r.total:,} hits for 'drums').[/]")
    except SessionExpired as err:
        _print_mcp_status(settings)
        _needs_human(err)
    except Exception as err:  # noqa: BLE001 — report anything else plainly
        _print_mcp_status(settings)
        console.print(f"[red]Live check failed: {err}[/]")
        raise typer.Exit(1)
    _print_mcp_status(settings)


def _bpm_range_kwargs(bpm_min: int | None, bpm_max: int | None) -> dict:
    """GraphQL search kwargs for a BPM range, each included only when set —
    same "omit rather than send null" rule the client's own filters use."""
    kwargs: dict = {}
    if bpm_min is not None:
        kwargs["min_bpm"] = bpm_min
    if bpm_max is not None:
        kwargs["max_bpm"] = bpm_max
    return kwargs


@app.command()
def search(
    query: str,
    limit: int = typer.Option(20, "--limit", "-n"),
    keyword: bool = typer.Option(
        False, "--keyword",
        help="Use GraphQL keyword search instead of the MCP default — needed "
             "for --type preset/midi/pack/all, --licensed, --key, --sort, "
             "or --order (MCP can't do these).",
    ),
    asset_type: str = typer.Option(
        None, "--type", "-t",
        help="(--keyword only) sample|preset|midi|pack|all — all fans out and merges every type. "
             "Defaults to 'sample' when --keyword is used; errors if passed without --keyword.",
    ),
    licensed: bool = typer.Option(
        False, "--licensed",
        help="(--keyword only) list only samples this account already owns — "
             "MCP's search has no equivalent 'my library' filter.",
    ),
    key: str = typer.Option(
        None, "--key",
        help="(--keyword only) filter by musical key, e.g. 'c minor'.",
    ),
    sort: str = typer.Option(
        None, "--sort",
        help="(--keyword only) GraphQL sort field, e.g. 'popularity' (default) or 'relevance'.",
    ),
    order: str = typer.Option(
        None, "--order",
        help="(--keyword only) ASC|DESC (default DESC).",
    ),
    bpm_min: int = typer.Option(
        None, "--bpm-min", help="Minimum BPM — works on both the default MCP search and --keyword.",
    ),
    bpm_max: int = typer.Option(
        None, "--bpm-max", help="Maximum BPM — works on both the default MCP search and --keyword.",
    ),
    sample_type: str = typer.Option(
        None, "--sample-type",
        help="(MCP default only) loop|oneshot — omit to search both.",
    ),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Search the Splice library.

    Default: the official MCP's semantic search (describe_a_sound) — the
    sanctioned path. Pass --keyword to use GraphQL keyword search instead,
    for capabilities MCP's search doesn't have (preset/midi/pack/all types,
    --licensed, --key, --sort, --order). These are NOT result-equivalent —
    MCP is semantic/embedding search, GraphQL is keyword/tag search (verified
    different retrieval tech) — don't expect the same top hit from both for
    the same query. --bpm-min/--bpm-max are the exception: both backends
    support a BPM range natively, so those two work regardless of --keyword.
    """
    settings = Settings.load(env_file)
    if keyword:
        if sample_type is not None:
            # --sample-type only means anything to describe_a_sound's MCP
            # search; silently accepting it here would make an agent believe
            # it filtered the GraphQL results by loop/oneshot when it didn't.
            console.print(
                "[red]--sample-type only applies to the default MCP search — "
                "it has no effect with --keyword (GraphQL). Drop --sample-type "
                "or drop --keyword.[/]"
            )
            raise typer.Exit(1)
        search_kwargs: dict = {
            "limit": limit,
            "asset_type": asset_type or "sample",
            "licensed": True if licensed else None,
        }
        if key is not None:
            search_kwargs["key"] = key
        if bpm_min is not None:
            search_kwargs["min_bpm"] = bpm_min
        if bpm_max is not None:
            search_kwargs["max_bpm"] = bpm_max
        if sort is not None:
            search_kwargs["sort"] = sort
        if order is not None:
            search_kwargs["order"] = order
        try:
            sess = _session(settings)
            with SpliceClient(sess) as client:
                result = client.search(query, **search_kwargs)
        except AuthError as err:
            _needs_human(err)
    else:
        if asset_type is not None or licensed or key is not None or sort is not None or order is not None:
            # Same failure mode as above, mirrored: an agent forgetting
            # --keyword would otherwise silently get generic MCP semantic
            # search while believing it filtered by type/ownership/key/sort.
            console.print(
                "[red]--type/--licensed/--key/--sort/--order only apply to --keyword "
                "(GraphQL) search — add --keyword, or drop the flag(s) to use the "
                "default MCP search.[/]"
            )
            raise typer.Exit(1)
        # describe_a_sound has no limit param — --limit/-n is always a
        # silent no-op on this path, so say so rather than pretending it
        # narrowed the result count.
        console.print(
            "[yellow]⚠ --limit is ignored on the default MCP search (describe_a_sound "
            "has no limit param) — use --keyword to control result count.[/]"
        )
        result = _mcp_call(
            settings,
            lambda client: client.search(
                query, sample_type=sample_type, bpm_min=bpm_min, bpm_max=bpm_max,
            ),
        )

    total_str = f"{result.total:,}" if result.total is not None else "?"
    table = Table(title=f'Splice: "{query}"  ({total_str} total)')
    table.add_column("Name", max_width=40, overflow="fold")
    table.add_column("Type")
    table.add_column("Pack", max_width=18, overflow="fold")
    table.add_column("BPM", justify="right")
    table.add_column("Key")
    table.add_column("catalog_uuid (pass to download/buy)", style="dim")
    for s in result.samples:
        table.add_row(
            s.name.split("/")[-1], s.asset_type, s.pack_name or "", str(s.bpm or ""),
            s.key or "", s.catalog_uuid or "?",
        )
    console.print(table)


@app.command()
def audition(
    query: str,
    limit: int = typer.Option(5, "--limit", "-n", help="How many previews to board up."),
    asset_type: str = typer.Option("sample", "--type", "-t"),
    bpm_min: int = typer.Option(None, "--bpm-min", help="Minimum BPM."),
    bpm_max: int = typer.Option(None, "--bpm-max", help="Maximum BPM."),
    out: Path = typer.Option(None, "--out", "-o", help="HTML output path."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Build a side-by-side audition board (HTML) of the top N free previews.

    Fetches each preview mp3 (no credit) and inlines them into one
    self-contained page for comparing before you `buy`. Prints the HTML path;
    an agent renders it as an artifact. Always GraphQL (MCP can't fetch
    preview audio) — so --bpm-min/--bpm-max map to GraphQL's BPM range.
    """
    settings = Settings.load(env_file)
    out_path = out or (settings.download_dir / f"audition-{query.replace(' ', '_')}.html")
    bpm_kwargs = _bpm_range_kwargs(bpm_min, bpm_max)
    try:
        sess = _session(settings)
        with SpliceClient(sess) as client:
            result = client.search(query, limit=limit, asset_type=asset_type, **bpm_kwargs)
            playable = [s for s in result.samples if s.preview_url]
            if not playable:
                console.print("[yellow]No previewable results.[/]")
                raise typer.Exit(1)
            items = []
            for s in playable:
                console.print(f"[dim]fetching preview: {s.name.split('/')[-1]}[/]")
                items.append((s, client.preview_bytes(s)))
    except AuthError as err:
        _needs_human(err)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render.audition_html(query, items))
    console.print(f"[green]✓ Audition board ({len(items)} clips) → {out_path}[/]")


@app.command()
def preview(
    query: str,
    dest: Path = typer.Option(None, "--dest", "-o", help="Output dir."),
    asset_type: str = typer.Option("sample", "--type", "-t"),
    bpm_min: int = typer.Option(None, "--bpm-min", help="Minimum BPM."),
    bpm_max: int = typer.Option(None, "--bpm-max", help="Maximum BPM."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Download the free preview mp3 of the top search hit (no credit)."""
    settings = Settings.load(env_file)
    out_dir = dest or settings.download_dir
    bpm_kwargs = _bpm_range_kwargs(bpm_min, bpm_max)
    try:
        sess = _session(settings)
        with SpliceClient(sess) as client:
            result = client.search(query, limit=1, asset_type=asset_type, **bpm_kwargs)
            if not result.samples:
                console.print("[yellow]No results.[/]")
                raise typer.Exit(1)
            sample = result.samples[0]
            target = out_dir / (sample.name.split("/")[-1].rsplit(".", 1)[0] + ".preview.mp3")
            client.download_preview(sample, target)
    except AuthError as err:
        _needs_human(err)
    console.print(f"[green]✓ {target}[/]")


_LOW_DOWNLOADS_WARNING_THRESHOLD = 5


def _sidecar_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".splice.json")


def _write_metadata_sidecar(
    target: Path, sample, *, backend: str, price: float | None = None,
) -> Path:
    """Persist the Sample's metadata alongside the downloaded file — the
    console table it came from disappears once the command exits, but the
    fields (bpm/key/pack/tags/catalog_uuid/licensed) are already in hand
    from the get_sample()/search call, so this is free to write.
    """
    payload = sample.model_dump(mode="json")
    payload["backend"] = backend
    payload["downloaded_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    if price is not None:
        payload["price_paid"] = price
    path = _sidecar_path(target)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def _mcp_download(settings: Settings, sample, out_dir: Path):
    """Fetch via MCP's download_asset — the default transfer path for the
    plain `download` command (which never records a spend, so charge and
    transfer can stay composed in one client.download() call). `buy` uses
    `_mcp_buy_download` below instead, which needs to record the ledger
    write BETWEEN the charge and the transfer. Raises McpAuthError/
    McpRateLimitError for callers to handle (rate-limit must NEVER trigger
    an automatic GraphQL reroute). Proactively warns as the 100/rolling-24h
    counter runs low, using the response's own "Remaining Downloads" field
    rather than waiting for the eventual hard McpRateLimitError.
    """
    mcp_sess = _mcp_session(settings)
    with McpClient(mcp_sess) as client:
        target = client.download(sample, out_dir)
        remaining = client.last_remaining_downloads
        if remaining is not None and remaining < _LOW_DOWNLOADS_WARNING_THRESHOLD:
            console.print(
                f"[yellow]⚠ Only {remaining} MCP downloads left in this rolling "
                "24h window.[/]"
            )
        return target


class _PostChargeTransferError(RuntimeError):
    """Raised when a credit was already spent (and recorded to the ledger)
    but the subsequent byte-transfer failed — see HARDENING 8. Signals
    `buy()` to show a clean "you already paid, re-run `download`" message
    instead of either a raw traceback or (if the underlying error happens to
    be a SessionExpired/AuthError) the exit-2 human-step machinery, which
    would wrongly suggest the *purchase* needs re-authorizing when it's
    already done — only the free re-download afterward needs a working
    session, and `splice download` will surface that normally if so.
    """

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def _report_post_charge_transfer_failure(err: "_PostChargeTransferError", uuid: str) -> None:
    """Clean handoff for HARDENING 8: the purchase already went through
    (and is already in the budget ledger) — only the byte transfer failed.
    Exit 1 (an ordinary failure), deliberately NOT exit 2 (no human auth
    step is needed to fix this; re-running `download` is enough).
    """
    console.print(
        Panel(
            f"[green]✓ Charged & licensed[/], but the file transfer failed: "
            f"{err.original}\n\nRun [bold]splice download {uuid}[/] to fetch "
            "it for free — the credit was already spent, so this won't "
            "charge again.",
            title="[yellow]Transfer failed after charge[/]",
            border_style="yellow",
        )
    )
    raise typer.Exit(1)


def _record_spend(bud, settings: Settings, sample, price: float) -> None:
    """Append the purchase to the budget ledger and persist it immediately
    (atomically, via budget_store.save). Called right after a charge is
    confirmed to have succeeded — see FIX 1's invariant: the credit is spent
    server-side the moment the charging call returns, so the local ledger
    must be written then, independent of whether the file transfer that
    follows succeeds.
    """
    bud.record(
        budget_store.Purchase(
            name=sample.name,
            catalog_uuid=sample.catalog_uuid or "",
            price=price,
            currency=sample.price_currency or "Credits",
        )
    )
    budget_store.save(bud, settings.budget_path)


def _mcp_buy_download(settings: Settings, sample, out_dir: Path, bud, price: float | None):
    """`buy`'s MCP transfer path: charge (download_asset) THEN, if a spend
    happened, record it to the ledger BEFORE streaming bytes to disk — so a
    byte-transfer failure after a successful charge still leaves the spend
    recorded (the sample is licensed either way; `splice download` can
    re-fetch the file for free afterwards). `price` is None for an
    already-licensed sample falling through to this path (free — no record).

    A transfer failure raises `_PostChargeTransferError` ONLY when a fresh
    charge just happened (see HARDENING 8) — an ordinary free re-download's
    transfer failure isn't "you already paid," so it propagates as-is.
    """
    mcp_sess = _mcp_session(settings)
    with McpClient(mcp_sess) as client:
        url, filename, remaining = client.download_charge(sample)
        if remaining is not None and remaining < _LOW_DOWNLOADS_WARNING_THRESHOLD:
            console.print(
                f"[yellow]⚠ Only {remaining} MCP downloads left in this rolling "
                "24h window.[/]"
            )
        charged = price is not None and not sample.licensed
        if charged:
            _record_spend(bud, settings, sample, price)
        try:
            return client.stream_to_disk(url, filename, out_dir)
        except Exception as err:
            if charged:
                raise _PostChargeTransferError(err) from err
            raise


@app.command()
def download(
    uuid: str,
    dest: Path = typer.Option(None, "--dest", "-o", help="Output dir."),
    backend: str = typer.Option(
        "mcp", "--backend",
        help="mcp (default, official/sanctioned) | graphql (explicit break-glass override).",
    ),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Download one exact asset's full-quality file — only if already owned.

    Takes a catalog_uuid (from `search`/`audition` output), not a query — the
    point is to download precisely the item you already identified, not
    whatever a fresh search happens to rank first. Free and safe:
    re-downloading an owned sample doesn't spend a credit, on either backend
    (GraphQL: verified against the live credit balance; MCP: same rule, per
    its own tool description — "Subsequent downloads of the same asset are
    free"). The licensed-check itself is always GraphQL (MCP has no field to
    check ownership without risking a transfer) — only the transfer defaults
    to MCP. If it isn't licensed yet, this stops and tells you — use
    `splice buy` instead.
    """
    settings = Settings.load(env_file)
    _validate_backend(backend)
    out_dir = dest or settings.download_dir
    target = None
    try:
        sess = _session(settings)
        with SpliceClient(sess) as client:
            try:
                sample = client.get_sample(uuid)
            except SampleNotFoundError as err:
                console.print(f"[yellow]{err}[/]")
                raise typer.Exit(1)
            if not sample.licensed:
                console.print(
                    Panel(
                        f'[yellow]"{sample.name}" isn\'t licensed on this account.[/]\n\n'
                        "Use `splice buy` instead if you want to spend a credit "
                        "to license it (only works within your standing budget — "
                        "see `splice budget status`).",
                        title="[yellow]Not licensed[/]",
                    )
                )
                raise typer.Exit(1)
            if backend == "graphql":
                target = out_dir / (sample.name.split("/")[-1])
                client.download(sample, target)
    except AuthError as err:
        _needs_human(err)

    if backend != "graphql":
        try:
            target = _mcp_download(settings, sample, out_dir)
        except McpAuthError as err:
            _mcp_needs_human(err)
        except McpRateLimitError as err:
            console.print(f"[red]{err}[/]\n[dim]Retry with `--backend graphql` if needed.[/]")
            raise typer.Exit(2)
    _write_metadata_sidecar(target, sample, backend=backend if backend == "graphql" else "mcp")
    console.print(f"[green]✓ {target}[/]")


@app.command()
def buy(
    uuid: str,
    dest: Path = typer.Option(None, "--dest", "-o", help="Output dir."),
    backend: str = typer.Option(
        "mcp", "--backend",
        help="mcp (default, official/sanctioned) | graphql (explicit break-glass override).",
    ),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """License + download one exact asset — SPENDS A CREDIT.

    Takes a catalog_uuid (from `search`/`audition` output), not a query —
    buying should never depend on a fresh search happening to rank the same
    item first. Only proceeds if the price is within your standing budget
    (see `splice budget status`) AND under the per-item cap. No per-purchase
    confirmation beyond that — the budget you set IS the standing
    authorization. Every purchase is appended to the budget's audit log
    (`splice budget status --log`) so you can review what was bought without
    having been asked each time.

    Default transfer path is the official MCP (`download_asset`, which
    purchases + downloads in one call). MCP enforces its own 100-downloads/
    rolling-24h cap — if hit, this reports it and STOPS; it never falls back
    to GraphQL automatically. That would be throttle evasion, exactly what
    Splice's ToS anti-automation clause targets (see
    docs/mcp-integration-proposal.md's Risks section). Pass `--backend
    graphql` yourself if you understand that risk and want to anyway.

    Already-licensed samples download for free via `splice download` instead
    — this command is specifically for buying something new.
    """
    _validate_backend(backend)
    settings = Settings.load(env_file)
    out_dir = dest or settings.download_dir
    bud = budget_store.load(settings.budget_path)
    price = None
    target = None
    try:
        sess = _session(settings)
        with SpliceClient(sess) as client:
            try:
                sample = client.get_sample(uuid)
            except SampleNotFoundError as err:
                console.print(f"[yellow]{err}[/]")
                raise typer.Exit(1)

            if sample.licensed:
                console.print(
                    f'[dim]"{sample.name}" is already licensed — downloading '
                    "for free instead of buying.[/]"
                )
                if backend == "graphql":
                    target = out_dir / (sample.name.split("/")[-1])
                    client.download(sample, target)
                    _write_metadata_sidecar(target, sample, backend="graphql")
                    console.print(f"[green]✓ {target}[/]")
                    return
                # else: fall through to the MCP transfer below (still free
                # for an owned sample — same rule on both backends).
            else:
                price = sample.price if sample.price is not None else float("inf")
                ok, reason = bud.can_afford(price)
                if not ok:
                    console.print(
                        Panel(
                            f'[yellow]Can\'t buy "{sample.name}" '
                            f"({price:g} {sample.price_currency or 'Credits'}).[/]\n\n"
                            f"{reason}",
                            title="[red]Budget stop[/]",
                            border_style="red",
                        )
                    )
                    raise typer.Exit(code=2)

                if backend == "graphql":
                    client.purchase(sample)
                    # The credit is spent server-side the instant purchase()
                    # returns — record it NOW, before the download below,
                    # so a transfer failure (SessionExpired, disk full,
                    # network) can't silently lose the spend from the
                    # ledger (FIX 1). The sample is licensed either way at
                    # this point; `splice download` can re-fetch it for
                    # free if the transfer below fails.
                    _record_spend(bud, settings, sample, price)
                    target = out_dir / (sample.name.split("/")[-1])
                    try:
                        client.download(sample, target)
                    except Exception as err:  # noqa: BLE001 — see HARDENING 8: the
                        # charge already happened; give a clean handoff instead
                        # of a raw traceback (or worse, an auth-handoff that
                        # implies the *purchase* needs redoing).
                        raise _PostChargeTransferError(err) from err
    except AuthError as err:
        _needs_human(err)
    except _PostChargeTransferError as err:
        _report_post_charge_transfer_failure(err, uuid)

    if backend != "graphql":
        try:
            # Records the spend itself, between the charge and the byte
            # transfer — see _mcp_buy_download's docstring and FIX 1.
            target = _mcp_buy_download(settings, sample, out_dir, bud, price)
        except McpAuthError as err:
            _mcp_needs_human(err)
        except _PostChargeTransferError as err:
            _report_post_charge_transfer_failure(err, uuid)
        except McpRateLimitError as err:
            console.print(
                Panel(
                    f"[yellow]{err}[/]\n\n"
                    "Not rerouting to GraphQL to work around this — that would be "
                    "throttle evasion, which Splice's ToS forbids. Wait for the "
                    "rolling 24h window, or pass `--backend graphql` yourself if "
                    "you understand that risk.",
                    title="[red]MCP rate limit[/]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=2)

    _write_metadata_sidecar(
        target, sample, backend=backend if backend == "graphql" else "mcp", price=price,
    )
    if price is not None and not sample.licensed:
        console.print(
            f"[green]✓ Bought and downloaded {target} "
            f"({price:g} credits — {bud.remaining:g} remaining)[/]"
        )
    else:
        console.print(f"[green]✓ {target}[/]")


budget_app = typer.Typer(add_completion=False, help="Manage the standing purchase budget.")
app.add_typer(budget_app, name="budget")


@budget_app.command("set")
def budget_set(
    allowance: float = typer.Argument(..., help="Total credits to authorize (replaces, doesn't add)."),
    max_item: float = typer.Option(None, "--max-item", help="Per-purchase cap (credits)."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Set the standing spend pool — a HUMAN-ONLY command.

    This is the sole way `splice buy` gets authorized to spend real credits.
    A coding agent must never run this on your behalf — only you decide how
    much of your account it can draw from.
    """
    settings = Settings.load(env_file)
    bud = budget_store.load(settings.budget_path)
    bud.allowance = allowance
    if max_item is not None:
        bud.per_item_cap = max_item
    if bud.baseline_credits is None:
        # Captured once, ever — the reference point `reconcile` compares
        # against. Best-effort: a session/network hiccup here shouldn't
        # block setting the budget itself.
        try:
            sess = _session(settings)
            with SpliceClient(sess) as client:
                live_credits = client.get_credits()
            # HARDENING 7: if any purchases already happened before this
            # baseline capture ever succeeded (e.g. `buy` ran while the
            # first few `budget set` calls couldn't reach Splice), the live
            # balance already reflects those spends. Add `bud.spent` back so
            # the baseline represents the PRE-spend state — otherwise
            # `reconcile` would double-count already-ledgered purchases as
            # unexplained drift (baseline - live > our own `spent`).
            bud.baseline_credits = live_credits + bud.spent
        except Exception:  # noqa: BLE001
            console.print(
                "[dim](couldn't capture a live-credit baseline — "
                "`budget reconcile` will report nothing until a later "
                "`budget set` succeeds in reaching Splice)[/]"
            )
    budget_store.save(bud, settings.budget_path)
    console.print(
        f"[green]✓ Budget set: {bud.remaining:g} credits available "
        f"(per-item cap {bud.per_item_cap:g}).[/]"
    )


@budget_app.command("reconcile")
def budget_reconcile(env_file: Path = typer.Option(None, "--env")) -> None:
    """Compare our local spend ledger against Splice's real credit balance.

    Catches drift: a purchase made outside this tool (in the Splice UI
    directly), or a bug in our own accounting. Needs a baseline, captured
    automatically the first time `budget set` ran.
    """
    settings = Settings.load(env_file)
    bud = budget_store.load(settings.budget_path)
    if bud.baseline_credits is None:
        console.print(
            "[yellow]No baseline yet — run `splice budget set` again once "
            "you have a working session so it can capture one.[/]"
        )
        raise typer.Exit(1)
    try:
        sess = _session(settings)
        with SpliceClient(sess) as client:
            live = client.get_credits()
    except AuthError as err:
        _needs_human(err)

    expected_spent, drift = bud.reconcile(live)
    console.print(
        f"Baseline: {bud.baseline_credits:g}  |  Live now: {live:g}  |  "
        f"Implied spend: {expected_spent:g}  |  Our ledger: {bud.spent:g}"
    )
    if abs(drift) < 1e-9:
        console.print("[green]✓ Ledger matches Splice's own records.[/]")
    else:
        console.print(
            f"[yellow]⚠ Drift of {drift:g} credits — our ledger and Splice's "
            "balance disagree (a purchase outside `splice buy`, or a bug "
            "here). Treat the live balance as ground truth.[/]"
        )


@budget_app.command("status")
def budget_status(
    log: bool = typer.Option(False, "--log", help="Show the purchase audit trail."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Show the standing budget: allowance, spent, remaining, per-item cap."""
    settings = Settings.load(env_file)
    bud = budget_store.load(settings.budget_path)
    console.print(
        f"Allowance: {bud.allowance:g}  |  Spent: {bud.spent:g}  |  "
        f"Remaining: [bold]{bud.remaining:g}[/]  |  Per-item cap: {bud.per_item_cap:g}"
    )
    if bud.remaining <= 0:
        console.print("[yellow]Budget exhausted — `splice buy` will refuse until you top up.[/]")
    if log:
        if not bud.log:
            console.print("[dim](no purchases yet)[/]")
        else:
            table = Table(title="Purchase log")
            table.add_column("Name", max_width=48, overflow="fold")
            table.add_column("Price", justify="right")
            table.add_column("Remaining after", justify="right")
            for entry in bud.log:
                table.add_row(
                    entry["name"], f'{entry["price"]:g} {entry["currency"]}',
                    f'{entry["remaining_after"]:g}',
                )
            console.print(table)


stack_app = typer.Typer(
    add_completion=False,
    help="Build/manage MCP Stacks (multi-layer arrangements) — MCP-only, no GraphQL equivalent.",
)
app.add_typer(stack_app, name="stack")


def _print_stack(stack) -> None:
    bpm_key = f"  ({stack.bpm} BPM, {stack.key})" if stack.bpm else ""
    console.print(f"[bold]{stack.name or stack.stack_uuid}[/]{bpm_key}  [dim]{stack.stack_uuid}[/]")
    table = Table()
    table.add_column("Layer")
    table.add_column("Sample", max_width=44, overflow="fold")
    table.add_column("BPM", justify="right")
    table.add_column("Key")
    table.add_column("catalog_uuid", style="dim")
    for layer in stack.layers:
        table.add_row(
            layer.role, layer.sample.name.split("/")[-1],
            str(layer.sample.bpm or ""), layer.sample.key or "",
            layer.sample.catalog_uuid or "?",
        )
    console.print(table)
    if stack.permalink:
        console.print(f"[dim]{stack.permalink}[/]")


@stack_app.command("from")
def stack_from(
    uuid: str,
    name: str = typer.Option(None, "--name"),
    bpm: int = typer.Option(None, "--bpm"),
    share: bool = typer.Option(False, "--share", help="Also generate a public share URL."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Build a multi-layer stack seeded by one loop's catalog_uuid.

    Free — only downloading a stack's layers spends credits (via `splice
    buy`/`download` on the layer uuids this prints). Seed must be a loop
    (from `search`/`audition`), not a one-shot.
    """
    settings = Settings.load(env_file)
    stack = _mcp_call(settings, lambda client: client.create_stack(uuid, name=name, bpm=bpm, public=share))
    _print_stack(stack)


@stack_app.command("prompt")
def stack_prompt(
    text: str,
    bpm_min: int = typer.Option(None, "--bpm-min"),
    bpm_max: int = typer.Option(None, "--bpm-max"),
    share: bool = typer.Option(False, "--share", help="Also generate a public share URL."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Generate a complete multi-track arrangement from a text description.

    Free, same as `stack from` — no seed sample needed, just a prompt.
    """
    settings = Settings.load(env_file)
    stack = _mcp_call(
        settings,
        lambda client: client.prompt_to_stack(text, bpm_min=bpm_min, bpm_max=bpm_max, public=share),
    )
    _print_stack(stack)


def _parse_swap_layers(pairs: list[str]) -> list[dict]:
    """Parse repeated `--swap layer_uuid:asset_uuid` values into the
    `[{"layer_uuid":..., "asset_uuid":...}]` shape update_stack's
    `swap_layers` expects. Exits 1 on a malformed value (not "uuid:uuid")
    rather than passing garbage through to the MCP tool call.
    """
    swaps = []
    for pair in pairs:
        parts = pair.split(":")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            console.print(
                f'[red]--swap value {pair!r} isn\'t "layer_uuid:asset_uuid" — '
                "pass exactly one colon separating the two uuids.[/]"
            )
            raise typer.Exit(1)
        layer_uuid, asset_uuid = parts
        swaps.append({"layer_uuid": layer_uuid.strip(), "asset_uuid": asset_uuid.strip()})
    return swaps


@stack_app.command("update")
def stack_update(
    stack_uuid: str,
    name: str = typer.Option(None, "--name"),
    bpm: int = typer.Option(None, "--bpm"),
    add: list[str] = typer.Option(None, "--add", help="catalog_uuid(s) to add as new layers."),
    remove: list[str] = typer.Option(None, "--remove", help="layer_uuid(s) to remove."),
    swap: list[str] = typer.Option(
        None, "--swap",
        help='layer_uuid:asset_uuid (repeatable) — exact 1:1 replace of one layer\'s sample.',
    ),
    add_compatible: bool = typer.Option(
        False, "--add-compatible", help="Let the MCP pick and add a compatible layer.",
    ),
    swap_compatible: bool = typer.Option(
        False, "--swap-compatible",
        help="Let the MCP pick a compatible replacement (vs --swap's exact pick).",
    ),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Modify an existing stack — rename, re-BPM, add/remove/swap layers.

    Free. Max 8 layers (per MCP's own rule); at least one change required.
    `--swap` (you chose the exact replacement sample) and `--swap-compatible`
    (the MCP picks a fitting one) are two different mechanisms — pass
    whichever matches how you want the replacement decided.
    """
    settings = Settings.load(env_file)
    if not any([name, bpm, add, remove, swap, add_compatible, swap_compatible]):
        console.print(
            "[yellow]Nothing to update — pass --name/--bpm/--add/--remove/--swap/"
            "--add-compatible/--swap-compatible.[/]"
        )
        raise typer.Exit(1)
    swap_layers = _parse_swap_layers(list(swap)) if swap else None
    stack = _mcp_call(
        settings,
        lambda client: client.update_stack(
            stack_uuid,
            name=name,
            bpm=bpm,
            add_asset_uuids=list(add) if add else None,
            remove_layer_uuids=list(remove) if remove else None,
            swap_layers=swap_layers,
            add_compatible_layers=True if add_compatible else None,
            swap_compatible_layers=True if swap_compatible else None,
        ),
    )
    _print_stack(stack)


@stack_app.command("share")
def stack_share(
    stack_uuid: str,
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Get a public shareable URL for an existing stack."""
    settings = Settings.load(env_file)
    url = _mcp_call(settings, lambda client: client.share_stack(stack_uuid))
    console.print(f"[green]✓ {url}[/]")


if __name__ == "__main__":
    app()
