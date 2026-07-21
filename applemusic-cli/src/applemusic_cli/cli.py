"""The `applemusic` thin shell an agent drives: parse → auth/client → render →
exit code. No Apple wire knowledge here (that's caged in client.py/auth.py).

Exit-code convention (agents key off it, same as sibling spotify-cli):
  0 — ok
  1 — ordinary failure (no results, not found, rate limited, API error)
  2 — a human step is required (login: paste a media-user-token). Prints a panel
      with exact next steps; relay it verbatim and STOP.

Divergence from spotify-cli worth knowing: CATALOG commands (search/song/album/
artist/playlist-get) need NO login — `_live_client` scrapes a public developer
token on the fly. Only library / `/me/*` commands need the media-user-token, and
the caged client raises the exit-2 handoff when it's absent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from . import auth, browsers, filters, render, session
from .auth import AppleMusicAuthError, AppleMusicSession
from .client import AppleApiError, Client, RateLimited
from .config import Settings

app = typer.Typer(add_completion=False, help="Apple Music API CLI — catalog search + your library (borrowed-token auth).")
console = Console()

_LOGIN_STEPS = (
    "Run `applemusic login`. It will tell you how to copy your `media-user-token` "
    "from a signed-in music.apple.com browser tab and paste it back. Catalog "
    "commands (search/song/album/artist) work WITHOUT logging in."
)


def _settings() -> Settings:
    return Settings.load()


@contextmanager
def _handle() -> Iterator[None]:
    """Turn ordinary failures into a clean message + exit 1. Auth failures are
    NOT caught here — they propagate to the per-command _needs_human handoff."""
    try:
        yield
    except typer.Exit:
        raise
    except AppleMusicAuthError:
        raise
    except RateLimited as err:
        wait = f" Retry after {err.retry_after}s." if err.retry_after is not None else ""
        console.print(f"[yellow]Apple Music rate limit hit.[/]{wait}")
        raise typer.Exit(1) from err
    except (AppleApiError, filters.FilterError) as err:
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err
    except Exception as err:  # noqa: BLE001 - last-resort clean message, never a traceback
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err


def _needs_human(err: AppleMusicAuthError) -> NoReturn:
    console.print(
        Panel(
            f"{escape(str(err))}\n\n{_LOGIN_STEPS}",
            title="[red]Human step needed[/]",
            border_style="red",
        )
    )
    raise typer.Exit(code=2)


def _live_client(settings: Settings) -> Client:
    """Return a ready Client. Ensures a fresh developer token by re-scraping when
    the stored one is stale (or absent — catalog works with no prior login), then
    layers in a media-user-token from env or the saved session. Persists any
    refreshed token set. Never needs a browser: only the media-user-token is
    human-supplied, and its absence is surfaced later by the caged client (exit-2)."""
    stored: AppleMusicSession | None = None
    if settings.session_path.exists():
        stored = session.load(settings.session_path)

    if stored is None or stored.dev_token_expired():
        token, expiry = auth.fetch_developer_token()
        stored = AppleMusicSession(
            developer_token=token,
            developer_token_expiry=expiry,
            storefront=stored.storefront if stored else settings.storefront,
            media_user_token=stored.media_user_token if stored else None,
        )
        session.save(stored, settings.session_path)

    # An env-injected token (agent/CI) overrides the saved one for this run only.
    if settings.media_user_token:
        stored = AppleMusicSession(
            developer_token=stored.developer_token,
            developer_token_expiry=stored.developer_token_expiry,
            storefront=stored.storefront,
            media_user_token=settings.media_user_token,
        )
    return Client(stored)


# ---- auth commands ---------------------------------------------------------


@app.command()
def login(
    browser: str = typer.Option(
        "auto", "--browser", help="Lift the token from this browser you're signed in on: "
        "auto|firefox|chrome|chromium|brave|edge."
    ),
    token: str = typer.Option(
        None, "--token", help="Paste the media-user-token directly, skipping the browser lift."
    ),
) -> None:
    """Save your media-user-token so library / `/me/*` commands work.

    There is no OAuth here. Just sign in to music.apple.com in your normal
    browser, then run this — it lifts the `media-user-token` cookie straight from
    that browser (no DevTools, no copy-paste). `--token` pastes one directly; the
    developer token is always fetched automatically. Catalog commands need none of
    this.
    """
    settings = _settings()

    # Scrape/refresh the developer token first (no user action).
    try:
        with _handle():
            dev_token, dev_expiry = auth.fetch_developer_token()
    except AppleMusicAuthError as err:
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err

    if token is None:
        try:
            token = browsers.extract_media_user_token(browser)
            console.print(f"[green]Lifted media-user-token from your browser[/] ({browser}).")
        except AppleMusicAuthError as err:
            # Auto-lift failed — fall back to manual paste with instructions.
            console.print(
                Panel(
                    f"Couldn't auto-lift from the browser: {escape(str(err))}\n\n"
                    "Fallback — paste it manually:\n"
                    "1. Open [link]https://music.apple.com[/] and sign in.\n"
                    "2. DevTools (F12) → Application/Storage → Cookies → https://music.apple.com.\n"
                    "3. Copy the [bold]media-user-token[/] value and paste it below.",
                    title="Apple Music login — manual fallback",
                    border_style="yellow",
                )
            )
            token = typer.prompt("Paste your media-user-token").strip()

    if not token:
        console.print("[red]No token given — aborting.[/]")
        raise typer.Exit(1)

    sess = AppleMusicSession(
        developer_token=dev_token,
        developer_token_expiry=dev_expiry,
        storefront=settings.storefront,
        media_user_token=token,
    )
    # Validate the token with one real /me call, and adopt the account's own
    # storefront so catalog queries default to the right region.
    try:
        with _handle():
            sf = Client(sess).my_storefront()
    except AppleMusicAuthError as err:
        # login IS the human step already — a bad paste is ordinary, exit 1.
        console.print(f"[red]That media-user-token was rejected: {escape(str(err))}[/]")
        raise typer.Exit(1) from err
    if sf:
        sess = AppleMusicSession(
            developer_token=sess.developer_token,
            developer_token_expiry=sess.developer_token_expiry,
            storefront=sf,
            media_user_token=sess.media_user_token,
        )
    session.save(sess, settings.session_path)
    console.print(
        f"[green]Logged in.[/] Storefront: {sess.storefront}. Session saved to {settings.session_path}"
    )


@app.command()
def status() -> None:
    """Show whether a session exists, the developer token's freshness, and whether
    a media-user-token (library access) is present."""
    settings = _settings()
    if not settings.session_path.exists():
        console.print(
            "[yellow]No session yet.[/] Catalog commands still work; run `applemusic login` for your library."
        )
        raise typer.Exit(1)
    sess = session.load(settings.session_path)
    dev = "stale (will re-scrape on next call)" if sess.dev_token_expired() else "fresh"
    lib = "yes" if sess.media_user_token else "no (catalog only — run `applemusic login`)"
    console.print(
        f"[green]Session found.[/] Developer token: {dev}. Library access: {lib}. "
        f"Storefront: {sess.storefront}. Session: {settings.session_path}"
    )


# ---- catalog commands (no login required) ----------------------------------


@app.command()
def search(
    query: str = typer.Argument(..., help="Free-text search query."),
    type: str = typer.Option("song", help="Comma-separated: song,album,artist,playlist."),
    limit: int = typer.Option(25, help="Max results per type (Apple caps at 25)."),
) -> None:
    """Search the catalog (songs/albums/artists/playlists). No login needed."""
    types = tuple(t.strip() for t in type.split(",") if t.strip())
    results = _with_client(lambda c: c.search(query, types, limit))
    tables = render.search_tables(results)
    if not tables:
        console.print("[yellow]No results.[/]")
        raise typer.Exit(1)
    for t in tables:
        console.print(t)


@app.command()
def song(song_id: str = typer.Argument(..., help="Apple Music catalog song id.")) -> None:
    """Get one song by catalog id. No login needed."""
    _catalog_one(lambda c: c.song(song_id), lambda r: [render.songs_table((r,), title="Song")])


@app.command()
def album(album_id: str = typer.Argument(..., help="Apple Music catalog album id.")) -> None:
    """Get one album by catalog id. No login needed."""
    _catalog_one(lambda c: c.album(album_id), lambda r: [render.albums_table((r,), title="Album")])


@app.command()
def artist(artist_id: str = typer.Argument(..., help="Apple Music catalog artist id.")) -> None:
    """Get one artist by catalog id. No login needed."""
    _catalog_one(lambda c: c.artist(artist_id), lambda r: [render.artists_table((r,), title="Artist")])


# Client-side filter flags shared by `library` and `playlist-get` (over fetched items).
_SINCE = typer.Option(None, "--since", help="Keep items added on/after this date (YYYY, YYYY-MM, YYYY-MM-DD).")
_UNTIL = typer.Option(None, "--until", help="Keep items added on/before this date.")
_RELEASE_YEAR = typer.Option(None, "--release-year", help="Keep songs released in this year/range (YYYY or YYYY-YYYY).")
_ARTIST = typer.Option(None, "--artist", help="Keep songs whose artist name contains this (case-insensitive).")


@app.command(name="playlist-get")
def playlist_get(
    playlist_id: str = typer.Argument(..., help="Apple Music catalog playlist id (pl.xxxx)."),
    since: str = _SINCE,
    until: str = _UNTIL,
    release_year: str = _RELEASE_YEAR,
    artist: str = _ARTIST,
) -> None:
    """Get a catalog (editorial) playlist and its tracks. No login needed.

    Client-side filters (--since/--until/--release-year/--artist) narrow the tracks;
    --since/--until only apply where a per-track added date exists.
    """

    def run(c: Client):
        detail = c.playlist(playlist_id)
        items = (
            filters.apply(detail.items, since=since, until=until, release_year=release_year, artist=artist)
            if detail.items_visible
            else []
        )
        return detail, items

    def _tables(result):
        detail, items = result
        if not detail.items_visible:
            console.print("[yellow]Tracks not available for this playlist.[/]")
        return render.playlist_detail_tables(detail, items=items)

    _catalog_one(run, _tables)


# ---- library commands (need a media-user-token) ----------------------------


@app.command(name="my-playlists")
def my_playlists(limit: int = typer.Option(25, help="Max playlists.")) -> None:
    """List your own library playlists (needs login)."""
    _catalog_one(lambda c: c.my_playlists(limit), lambda r: [render.playlists_table(r, title="My playlists")])


@app.command()
def library(
    limit: int = typer.Option(25, help="Max songs to fetch (Apple caps a page at 100)."),
    since: str = _SINCE,
    until: str = _UNTIL,
    release_year: str = _RELEASE_YEAR,
    artist: str = _ARTIST,
) -> None:
    """List the songs in your library (the 'saved songs' analog; needs login).

    Filters apply to the fetched page: --since/--until on when you added it,
    --release-year on the song's era, --artist on the artist name.
    """

    def run(c: Client):
        items = c.library_songs(limit)
        return filters.apply(items, since=since, until=until, release_year=release_year, artist=artist)

    _catalog_one(run, lambda items: [render.library_songs_table(items, title="Library songs")])


@app.command(name="library-add")
def library_add(
    song_ids: list[str] = typer.Argument(..., help="Catalog song ids to add to your library."),
) -> None:
    """Add catalog songs to your library by catalog id (needs login).

    Use the catalog ids from a prior `search` — NOT the `i.xxxx` library ids.
    """
    _mutate(lambda c: c.add_to_library(tuple(song_ids)), f"Added {len(song_ids)} song(s) to your library.")


@app.command(name="library-remove")
def library_remove(
    library_song_ids: list[str] = typer.Argument(..., help="LIBRARY song ids (i.xxxx, from `library`)."),
) -> None:
    """Remove songs from your library by LIBRARY id (needs login).

    Takes the `i.xxxx` ids shown by `applemusic library`, not catalog ids.
    """
    _mutate(
        lambda c: c.remove_from_library(tuple(library_song_ids)),
        f"Removed {len(library_song_ids)} song(s) from your library.",
    )


@app.command(name="playlist-create")
def playlist_create(
    name: str = typer.Argument(..., help="New playlist name."),
    description: str = typer.Option(None, help="Optional description."),
    song_id: list[str] = typer.Option(None, "--song", help="Catalog song id to seed with (repeatable)."),
) -> None:
    """Create a library playlist, optionally seeded with catalog songs (needs login)."""

    def _run(c: Client):
        return c.create_playlist(name, description=description, song_ids=tuple(song_id or ()))

    def _tables(pl):
        console.print(f"[green]Created playlist[/] {escape(pl.name)} — id {pl.id}")
        return [render.playlists_table((pl,), title="Created")]

    _catalog_one(_run, _tables)


@app.command(name="playlist-add")
def playlist_add(
    playlist_id: str = typer.Argument(..., help="Target LIBRARY playlist id (from `my-playlists`)."),
    song_ids: list[str] = typer.Argument(..., help="Catalog song ids to add."),
) -> None:
    """Add catalog songs to one of your library playlists (needs login)."""
    _mutate(
        lambda c: c.add_to_playlist(playlist_id, tuple(song_ids)),
        f"Added {len(song_ids)} song(s) to {playlist_id}.",
    )


# ---- behavioral commands (need a media-user-token) -------------------------


@app.command()
def recent(limit: int = typer.Option(25, help="Max songs (Apple caps at ~30).")) -> None:
    """Your recently played songs (needs login)."""
    _catalog_one(lambda c: c.recent_played(limit), lambda r: [render.songs_table(r, title="Recently played")])


@app.command(name="heavy-rotation")
def heavy_rotation(limit: int = typer.Option(25, help="Max items (Apple caps at ~30).")) -> None:
    """Your most-played content — a mix of albums/playlists/songs (needs login)."""
    results = _with_client(lambda c: c.heavy_rotation(limit))
    tables = render.search_tables(results)
    if not tables:
        console.print("[yellow]No heavy-rotation data.[/]")
        raise typer.Exit(1)
    for t in tables:
        console.print(t)


# ---- shared command bodies -------------------------------------------------


def _with_client(run: Callable[[Client], Any]) -> Any:
    """Run `run(client)` against a live client under the one error policy every
    command shares: AppleMusicAuthError → exit-2 human handoff, everything else →
    clean exit 1. The single owner of that routing. Returns whatever `run` returns."""
    settings = _settings()
    try:
        with _handle():
            return run(_live_client(settings))
    except AppleMusicAuthError as err:
        _needs_human(err)  # NoReturn — raises typer.Exit(2)


def _catalog_one(run: Callable[[Client], Any], to_tables) -> None:
    result = _with_client(run)
    for t in to_tables(result):
        console.print(t)


def _mutate(run: Callable[[Client], Any], ok_message: str) -> None:
    _with_client(run)
    console.print(f"[green]{escape(ok_message)}[/]")


if __name__ == "__main__":
    app()
