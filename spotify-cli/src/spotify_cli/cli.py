"""The `spotify` thin shell an agent drives: parse → auth/client → render →
exit code. No Spotify wire knowledge here (that's caged in client.py/auth.py).

Exit-code convention (agents key off it, same as sibling splice-cli):
  0 — ok
  1 — ordinary failure (no results, not found, rate limited, API error)
  2 — a human step is required (login / re-login). Prints a panel with exact
      next steps; relay it verbatim and STOP.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable, Iterator
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from . import auth, filters, render, session
from .auth import SpotifyAuthError
from .client import Client, RateLimited, SpotifyApiError
from .config import Settings

app = typer.Typer(add_completion=False, help="Spotify Web API CLI — catalog search + your account (PKCE OAuth).")
console = Console()

_LOGIN_STEPS = (
    "Run `spotify login`, then open the printed URL in a browser on THIS machine "
    "and approve. The callback returns to a loopback port automatically."
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
    except SpotifyAuthError:
        raise
    except RateLimited as err:
        wait = f" Retry after {err.retry_after}s." if err.retry_after is not None else ""
        console.print(f"[yellow]Spotify rate limit hit.[/]{wait}")
        raise typer.Exit(1) from err
    except SpotifyApiError as err:
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err
    except Exception as err:  # noqa: BLE001 - last-resort clean message, never a traceback
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err


def _needs_human(err: SpotifyAuthError) -> NoReturn:
    console.print(
        Panel(
            f"{escape(str(err))}\n\n{_LOGIN_STEPS}",
            title="[red]Human step needed[/]",
            border_style="red",
        )
    )
    raise typer.Exit(code=2)


def _live_client(settings: Settings) -> Client:
    """Load the saved session, refresh it if the access token is stale (headless,
    no browser), persist the refreshed set, and return a ready Client. Raises
    SpotifyAuthError (→ exit 2) if there's no session or the refresh token is dead."""
    if not settings.session_path.exists():
        raise SpotifyAuthError("Not logged in — no session found.")
    sess = session.load(settings.session_path)
    if sess.expired():
        if not settings.client_id:
            raise SpotifyAuthError("Session expired and SPOTIFY_CLIENT_ID is unset — can't refresh.")
        sess = auth.refresh(sess, client_id=settings.client_id)
        session.save(sess, settings.session_path)
    return Client(sess)


def _clamp_search_limit(limit: int) -> int:
    if limit > 10:
        console.print("[yellow]Spotify caps search limit at 10 (Feb 2026) — clamping.[/]")
        return 10
    return limit


# ---- auth commands ---------------------------------------------------------


@app.command()
def login(
    port: int = typer.Option(None, help="Loopback callback port (default from config)."),
    timeout: int = typer.Option(auth.DEFAULT_LOGIN_TIMEOUT, help="Seconds to wait for the browser."),
    manual: bool = typer.Option(
        False,
        "--no-server/--server",
        "--manual",
        help="Don't run a loopback server; paste the redirect URL back yourself. "
        "For headless/SSH when you can't port-forward.",
    ),
) -> None:
    """Authorize via browser (Authorization Code + PKCE) and save the session.

    Default (server) flow needs the browser on the same machine, or an SSH
    `-L <port>:localhost:<port>` tunnel. Over SSH without forwarding, use
    --no-server and paste the redirect URL back.
    """
    settings = _settings()
    if not settings.client_id:
        console.print(
            Panel(
                "SPOTIFY_CLIENT_ID is not set. Register an app at "
                "https://developer.spotify.com/dashboard, add the redirect URI "
                "[bold]http://127.0.0.1/callback[/] (portless), then put its Client ID "
                "in .env as SPOTIFY_CLIENT_ID.",
                title="[red]Setup needed[/]",
                border_style="red",
            )
        )
        raise typer.Exit(code=2)

    redirect_port = port or settings.redirect_port

    if manual:
        _login_manual(settings, redirect_port)
        return

    def _show(url: str) -> None:
        console.print(
            Panel(
                f"Open this URL in a browser on THIS machine and approve:\n\n[link]{escape(url)}[/]",
                title="Spotify login",
                border_style="green",
            )
        )

    try:
        with _handle():
            sess = auth.login(
                client_id=settings.client_id,
                redirect_port=redirect_port,
                timeout=timeout,
                on_url=_show,
            )
            session.save(sess, settings.session_path)
    except SpotifyAuthError as err:
        # login IS the human step already — a failure here (port busy, no code,
        # state mismatch) is ordinary, not a second handoff. Clean message + exit 1.
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err
    console.print(f"[green]Logged in.[/] Session saved to {settings.session_path}")


def _login_manual(settings: Settings, redirect_port: int) -> None:
    """No-server login: print the URL, let the user paste the redirect back.
    The browser's connection-refused page is expected — the code is in the URL."""
    assert settings.client_id is not None  # checked by caller
    challenge = auth.begin_login(client_id=settings.client_id, redirect_port=redirect_port)
    console.print(
        Panel(
            f"Open this URL in a browser on ANY machine and approve:\n\n"
            f"[link]{escape(challenge.authorize_url)}[/]\n\n"
            "After approving, the browser will try to load a http://127.0.0.1 page and show a "
            "[bold]connection-refused error — that is expected[/], the login still worked. "
            "Copy the [bold]full URL[/] from the address bar and paste it below.",
            title="Spotify login (manual / no-server)",
            border_style="green",
        )
    )
    pasted = typer.prompt("Paste the redirect URL")
    try:
        with _handle():
            sess = auth.complete_login(challenge, pasted, client_id=settings.client_id)
            session.save(sess, settings.session_path)
    except SpotifyAuthError as err:
        console.print(f"[red]{escape(str(err))}[/]")
        raise typer.Exit(1) from err
    console.print(f"[green]Logged in.[/] Session saved to {settings.session_path}")


@app.command()
def status() -> None:
    """Show whether a session exists and whether its access token is still fresh."""
    settings = _settings()
    if not settings.session_path.exists():
        console.print("[yellow]Not logged in.[/] Run `spotify login`.")
        raise typer.Exit(1)
    sess = session.load(settings.session_path)
    state = "expired (will refresh on next call)" if sess.expired() else "fresh"
    console.print(f"[green]Logged in.[/] Access token: {state}. Session: {settings.session_path}")


# ---- catalog commands ------------------------------------------------------


@app.command()
def search(
    query: str = typer.Argument(..., help="Free-text search query."),
    type: str = typer.Option("track", help="Comma-separated: track,album,artist,playlist."),
    limit: int = typer.Option(10, help="Max results per type (Spotify caps at 10)."),
    year: str = typer.Option(None, help="Server-side year filter: YYYY or a YYYY-YYYY range."),
    genre: str = typer.Option(None, help="Server-side genre filter, e.g. 'jazz'."),
) -> None:
    """Search the catalog (tracks/albums/artists/playlists).

    --year/--genre are Spotify's own server-side query filters. You can also put
    them (and more: tag:new, tag:hipster, artist:, album:) straight in the query.
    """
    types = tuple(t.strip() for t in type.split(",") if t.strip())
    results = _with_client(
        lambda c: c.search(query, types, _clamp_search_limit(limit), year=year, genre=genre)
    )
    tables = render.search_tables(results)
    if not tables:
        console.print("[yellow]No results.[/]")
        raise typer.Exit(1)
    for t in tables:
        console.print(t)


@app.command()
def track(track_id: str = typer.Argument(..., help="Spotify track id.")) -> None:
    """Get one track by id."""
    _catalog_one(lambda c: c.track(track_id), lambda r: [render.tracks_table((r,), title="Track")])


@app.command()
def album(album_id: str = typer.Argument(..., help="Spotify album id.")) -> None:
    """Get one album by id."""
    _catalog_one(lambda c: c.album(album_id), lambda r: [render.albums_table((r,), title="Album")])


@app.command()
def artist(artist_id: str = typer.Argument(..., help="Spotify artist id.")) -> None:
    """Get one artist by id."""
    _catalog_one(lambda c: c.artist(artist_id), lambda r: [render.artists_table((r,), title="Artist")])


# Client-side filter flags shared by `saved` and `playlist-get` (over fetched items).
_SINCE = typer.Option(None, "--since", help="Keep items added on/after this date (YYYY, YYYY-MM, YYYY-MM-DD).")
_UNTIL = typer.Option(None, "--until", help="Keep items added on/before this date.")
_RELEASE_YEAR = typer.Option(None, "--release-year", help="Keep tracks released in this year/range (YYYY or YYYY-YYYY).")
_ARTIST = typer.Option(None, "--artist", help="Keep tracks whose artist name contains this (case-insensitive).")


@app.command(name="playlist-get")
def playlist_get(
    playlist_id: str = typer.Argument(..., help="Spotify playlist id."),
    since: str = _SINCE,
    until: str = _UNTIL,
    release_year: str = _RELEASE_YEAR,
    artist: str = _ARTIST,
) -> None:
    """Get a playlist's metadata (and items, if you own/collaborate on it).

    Client-side filters (--since/--until/--release-year/--artist) narrow the items.
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
            console.print("[yellow]Items hidden — Spotify only returns items for playlists you own or collaborate on.[/]")
        return render.playlist_detail_tables(detail, items=items)

    _catalog_one(run, _tables)


# ---- user-account commands -------------------------------------------------


@app.command(name="my-playlists")
def my_playlists(limit: int = typer.Option(20, help="Max playlists.")) -> None:
    """List your own playlists (needs playlist-read-private)."""
    _catalog_one(lambda c: c.my_playlists(limit), lambda r: [render.playlists_table(r, title="My playlists")])


@app.command()
def saved(
    limit: int = typer.Option(20, help="Max tracks to fetch (Spotify caps a page at 50)."),
    since: str = _SINCE,
    until: str = _UNTIL,
    release_year: str = _RELEASE_YEAR,
    artist: str = _ARTIST,
) -> None:
    """List your saved/liked tracks (needs user-library-read).

    Filters apply to the fetched page: --since/--until on when you liked it,
    --release-year on the track's era, --artist on the artist name.
    """
    def run(c: Client):
        items = c.saved_tracks(limit)
        return filters.apply(items, since=since, until=until, release_year=release_year, artist=artist)

    _catalog_one(run, lambda items: [render.saved_tracks_table(items, title="Saved tracks")])


@app.command(name="saved-add")
def saved_add(uris: list[str] = typer.Argument(..., help="Spotify track URIs to save.")) -> None:
    """Save (like) tracks by URI (needs user-library-modify)."""
    _mutate(lambda c: c.save_tracks(tuple(uris)), f"Saved {len(uris)} track(s).")


@app.command(name="saved-remove")
def saved_remove(uris: list[str] = typer.Argument(..., help="Spotify track URIs to unsave.")) -> None:
    """Remove (unlike) saved tracks by URI (needs user-library-modify)."""
    _mutate(lambda c: c.remove_saved_tracks(tuple(uris)), f"Removed {len(uris)} track(s).")


@app.command()
def top(
    type: str = typer.Option("artists", help="artists or tracks."),
    range: str = typer.Option("medium", "--range", help="short (~4wk), medium (~6mo), or long (years)."),
    limit: int = typer.Option(20, help="1–50."),
) -> None:
    """Your most-played artists or tracks (needs user-top-read)."""
    if type not in ("artists", "tracks"):
        console.print(f"[red]Unknown --type '{escape(type)}' — expected 'artists' or 'tracks'.[/]")
        raise typer.Exit(1)

    def run(c: Client):
        return c.top_items(type, time_range=range, limit=limit)

    def _tables(items):
        title = f"Top {type} ({range})"
        table = render.artists_table(items, title=title) if type == "artists" else render.tracks_table(items, title=title)
        return [table]

    _catalog_one(run, _tables)


@app.command(name="playlist-create")
def playlist_create(
    name: str = typer.Argument(..., help="New playlist name."),
    public: bool = typer.Option(False, help="Make it public (default private)."),
    description: str = typer.Option(None, help="Optional description."),
) -> None:
    """Create a playlist (needs playlist-modify-private/-public)."""
    def _run(c: Client):
        pl = c.create_playlist(name, public=public, description=description)
        return pl

    def _tables(pl):
        console.print(f"[green]Created playlist[/] {escape(pl.name)} — id {pl.id}")
        return [render.playlists_table((pl,), title="Created")]

    _catalog_one(_run, _tables)


@app.command(name="playlist-add")
def playlist_add(
    playlist_id: str = typer.Argument(..., help="Target playlist id."),
    uris: list[str] = typer.Argument(..., help="Spotify track URIs to add."),
) -> None:
    """Add tracks to a playlist by URI (needs playlist-modify-*)."""
    _mutate(lambda c: c.add_to_playlist(playlist_id, tuple(uris)), f"Added {len(uris)} track(s) to {playlist_id}.")


@app.command(name="playlist-remove")
def playlist_remove(
    playlist_id: str = typer.Argument(..., help="Target playlist id."),
    uris: list[str] = typer.Argument(..., help="Spotify track URIs to remove."),
) -> None:
    """Remove tracks from a playlist by URI (needs playlist-modify-*)."""
    _mutate(
        lambda c: c.remove_from_playlist(playlist_id, tuple(uris)),
        f"Removed {len(uris)} track(s) from {playlist_id}.",
    )


# ---- shared command bodies -------------------------------------------------


def _with_client(run: Callable[[Client], Any]) -> Any:
    """Run `run(client)` against a live client under the one error policy every
    account/catalog command shares: SpotifyAuthError → exit-2 human handoff,
    everything else → clean exit 1. The single owner of that routing (was copied
    into every command body before). Returns whatever `run` returns."""
    settings = _settings()
    try:
        with _handle():
            return run(_live_client(settings))
    except SpotifyAuthError as err:
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
