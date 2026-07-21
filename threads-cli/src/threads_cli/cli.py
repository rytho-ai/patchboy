"""The `threads` thin shell an agent drives: parse → auth/client → render →
exit code. No Threads wire knowledge here (that's caged in client.py/auth.py).

Exit-code convention (agents key off it, same as sibling spotify-cli):
  0 — ok
  1 — ordinary failure (no results, not found, rate limited, API error)
  2 — a human step is required (setup / login). Prints a panel with exact
      next steps; relay it verbatim and STOP.

Divergence from spotify-cli worth knowing: there is only ONE login mode —
manual paste-back. Meta does not support a localhost/loopback redirect for a
Threads app, so unlike spotify-cli's default loopback-server flow (with
--no-server as a fallback), `threads login` is ALWAYS the manual flow.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from . import auth, render, session
from .auth import ThreadsAuthError
from .client import Client, RateLimited, ThreadsApiError
from .config import Settings

app = typer.Typer(add_completion=False, help="Threads API CLI — your own account (Authorization Code OAuth, manual paste-back).")
console = Console()

_LOGIN_STEPS = (
    "Run `threads login`, open the printed URL in a browser, approve, then paste the "
    "FULL redirected URL back — no server, no localhost callback (Meta doesn't support one "
    "for a Threads app)."
)

_SETUP_STEPS = (
    "Threads needs a Meta app with the [bold]Threads[/] use case before `threads login` works:\n\n"
    "1. Open [link]https://developers.facebook.com/apps/[/] and create an app (or pick an "
    "existing one), then add the [bold]Threads[/] use case — this gives you a Threads-specific "
    "App ID/Secret, distinct from the parent Facebook app's.\n"
    "2. [bold]Register a redirect URI[/]: Threads use case → Settings → add an https redirect URI. "
    "Meta does NOT allow a localhost/loopback redirect — a placeholder like "
    f"[bold]{escape(auth.DEFAULT_REDIRECT_URI)}[/] works fine; the page it points to never needs "
    "to actually load (the login code arrives in the URL regardless).\n"
    "3. [bold]Add yourself as a Threads Tester[/]: App Dashboard → App roles → Roles → "
    "Add People → Threads Tester (using your own Threads account), then accept the invite in "
    "your Threads account's Settings. This unlocks full API access in Development Mode — "
    "no App Review, no Business Verification, for your own account only.\n"
    "4. Set [bold]THREADS_APP_ID[/] and [bold]THREADS_APP_SECRET[/] (from the Threads use "
    "case's settings) in your environment or .env.\n"
    "5. Run [bold]threads login[/]."
)


def _settings() -> Settings:
    return Settings.load()


def _setup_panel() -> Panel:
    return Panel(_SETUP_STEPS, title="Set up Threads API access (one-time)", border_style="cyan")


@contextmanager
def _handle() -> Iterator[None]:
    """Turn ordinary failures into a clean message + exit 1. Auth failures are
    NOT caught here — they propagate to the per-command _needs_human handoff."""
    try:
        yield
    except typer.Exit:
        raise
    except ThreadsAuthError:
        raise
    except RateLimited as err:
        wait = f" Retry after {err.retry_after}s." if err.retry_after is not None else ""
        console.print(f"[yellow]Threads rate limit hit.[/]{wait}")
        raise typer.Exit(1) from err
    except ThreadsApiError as err:
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err
    except Exception as err:  # noqa: BLE001 - last-resort clean message, never a traceback
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err


def _needs_human(err: ThreadsAuthError) -> NoReturn:
    console.print(
        Panel(
            f"{escape(str(err))}\n\n{_LOGIN_STEPS}",
            title="[red]Human step needed[/]",
            border_style="red",
        )
    )
    raise typer.Exit(code=2)


def _live_client(settings: Settings) -> Client:
    """Load the saved session and return a ready Client. Raises ThreadsAuthError
    (→ exit 2) if there's no session or the long-lived token has expired (an
    expired token can't be refreshed — only re-login can). A token nearing its
    ~60-day expiry is opportunistically (best-effort) refreshed and persisted;
    a failed refresh doesn't block the call — the still-valid token is used."""
    if not settings.session_path.exists():
        raise ThreadsAuthError("Not logged in — no session found.")
    sess = session.load(settings.session_path)
    if sess.expired():
        raise ThreadsAuthError(
            "Session expired — an expired long-lived token can't be refreshed, only re-logged-in."
        )
    if sess.near_expiry():
        try:
            sess = auth.refresh(sess)
            session.save(sess, settings.session_path)
        except ThreadsAuthError:
            pass  # best-effort — keep using the still-valid token for this call
    return Client(sess)


# ---- auth commands ---------------------------------------------------------


@app.command()
def setup() -> None:
    """Print the one-time Meta app registration walkthrough for Threads API access."""
    console.print(_setup_panel())


@app.command()
def login() -> None:
    """Authorize via the MANUAL paste-back OAuth flow and save the session.

    There is no loopback-server mode — Meta doesn't allow a localhost redirect
    for a Threads app. Open the printed URL, approve, then paste the full
    redirected URL back (its page failing to load is expected).
    """
    settings = _settings()
    if not settings.app_id or not settings.app_secret:
        console.print(_setup_panel())
        raise typer.Exit(code=2)

    challenge = auth.begin_login(client_id=settings.app_id, redirect_uri=settings.redirect_uri)
    console.print(
        Panel(
            f"Open this URL in a browser and approve:\n\n[link]{escape(challenge.authorize_url)}[/]\n\n"
            f"After approving, you'll be redirected to [bold]{escape(settings.redirect_uri)}[/] — "
            "that page will likely fail to load (connection refused / blank / 404); "
            "[bold]that's expected[/], the login still worked. Copy the [bold]full URL[/] from "
            "the address bar (it has [bold]?code=...&state=...[/]) and paste it below.",
            title="Threads login (manual paste-back)",
            border_style="green",
        )
    )
    pasted = typer.prompt("Paste the redirect URL")
    try:
        with _handle():
            sess = auth.complete_login(
                challenge, pasted, client_id=settings.app_id, client_secret=settings.app_secret
            )
            session.save(sess, settings.session_path)
    except ThreadsAuthError as err:
        # login IS the human step already — a failure here (bad paste, state
        # mismatch, rejected code) is ordinary, not a second handoff. Exit 1.
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err
    console.print(f"[green]Logged in.[/] user_id={sess.user_id}. Session saved to {settings.session_path}")


@app.command()
def status() -> None:
    """Show whether a session exists, its user id, and whether its token is fresh."""
    settings = _settings()
    if not settings.session_path.exists():
        console.print("[yellow]Not logged in.[/] Run `threads login`.")
        raise typer.Exit(1)
    sess = session.load(settings.session_path)
    if sess.expired():
        state = "[red]expired[/] — run `threads login` again"
    elif sess.near_expiry():
        state = "[yellow]near expiry[/] (will attempt a background refresh on next call)"
    else:
        state = "fresh"
    console.print(
        f"[green]Logged in.[/] user_id={sess.user_id}. Token: {state}. Session: {settings.session_path}"
    )


# ---- account commands -------------------------------------------------------


@app.command()
def me() -> None:
    """Your own Threads profile."""
    _catalog_one(lambda c: c.me(), lambda p: [render.profile_panel(p)])


@app.command()
def posts(limit: int = typer.Option(25, help="Max posts to fetch (paginated).")) -> None:
    """List your own posts. NOTE: there is no home-timeline/feed read in the
    Threads API — this returns only YOUR posts, never other users'."""
    _catalog_one(lambda c: c.my_posts(limit), lambda r: [render.posts_table(r, title="Your posts")])


@app.command()
def post(text: str = typer.Argument(..., help="Text to publish.")) -> None:
    """Publish a text post (2-step container → publish). Writes to your account."""

    def _run(c: Client) -> Any:
        return c.publish_text(text)

    def _tables(p: Any) -> list:
        console.print(f"[green]Published.[/] id={p.id}" + (f" — {escape(p.permalink)}" if p.permalink else ""))
        return [render.posts_table((p,), title="Published")]

    _catalog_one(_run, _tables)


@app.command()
def replies(
    post_id: str = typer.Argument(..., help="One of YOUR post's ids."),
    conversation: bool = typer.Option(
        False, "--conversation", help="Use /conversation (threaded view) instead of /replies (flat list)."
    ),
) -> None:
    """Replies on one of your posts."""
    run: Callable[[Client], Any] = (lambda c: c.conversation(post_id)) if conversation else (lambda c: c.replies(post_id))
    _catalog_one(run, lambda r: [render.replies_table(r, title="Replies")])


@app.command()
def insights(
    post_id: str = typer.Argument(None, help="A post id for post-level insights; omit for account-level."),
) -> None:
    """Post-level insights (views/likes/replies/reposts/quotes), or account-level if no id given."""
    if post_id:
        _catalog_one(lambda c: c.post_insights(post_id), lambda r: [render.insights_table(r, title=f"Insights — {post_id}")])
    else:
        _catalog_one(lambda c: c.user_insights(), lambda r: [render.insights_table(r, title="Account insights")])


# ---- shared command bodies ---------------------------------------------------


def _with_client(run: Callable[[Client], Any]) -> Any:
    """Run `run(client)` against a live client under the one error policy every
    account command shares: ThreadsAuthError → exit-2 human handoff, everything
    else → clean exit 1. Returns whatever `run` returns."""
    settings = _settings()
    try:
        with _handle():
            return run(_live_client(settings))
    except ThreadsAuthError as err:
        _needs_human(err)  # NoReturn — raises typer.Exit(2)


def _catalog_one(run: Callable[[Client], Any], to_tables: Callable[[Any], list]) -> None:
    result = _with_client(run)
    for t in to_tables(result):
        console.print(t)


if __name__ == "__main__":
    app()
