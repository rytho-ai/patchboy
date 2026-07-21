"""Client-side filters over fetched collection items — pure, no I/O, no wire.

The single owner of "how library filtering works." `cli.py` wires flags to the
one public entry point `apply()`; nothing else here is imported elsewhere. This
is deliberately separate from the caged `client.py`: Apple's own catalog search
filters live in `client.search` — these are *client-side* filters over
already-fetched `LibrarySong`s (library songs / playlist tracks), a different
mechanism with a different home. Ported verbatim in shape from spotify-cli.

All filters key off `LibrarySong`: `added_at` for time range, `song.release_date`
for era, `song.artists` for artist. A `None` argument means "don't filter on this
axis," so `apply` composes them unconditionally. An item missing the data an
active filter needs (no `added_at`, no `release_date`) is excluded — a filter
should never silently keep what it can't verify.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import LibrarySong


class FilterError(ValueError):
    """A malformed filter value (bad date / year spec) — cli turns this into exit 1."""


def apply(
    items: Sequence[LibrarySong],
    *,
    since: str | None = None,
    until: str | None = None,
    release_year: str | None = None,
    artist: str | None = None,
) -> list[LibrarySong]:
    """Filter `items` by every non-None axis (AND-combined). Returns a new list."""
    out = list(items)
    if since or until:
        lo = _norm_start(since) if since else "0000-00-00"
        hi = _norm_end(until) if until else "9999-99-99"
        out = [s for s in out if s.added_at and lo <= s.added_at[:10] <= hi]
    if release_year:
        lo_y, hi_y = _year_range(release_year)
        out = [
            s
            for s in out
            if _year_of(s.song.release_date) is not None
            and lo_y <= _year_of(s.song.release_date) <= hi_y  # type: ignore[operator]
        ]
    if artist:
        needle = artist.lower()
        out = [s for s in out if any(needle in a.lower() for a in s.song.artists)]
    return out


# ---- normalization (kept here so `apply` is the only surface) ----------------


def _norm_start(s: str) -> str:
    """A date bound → the earliest full YYYY-MM-DD it denotes. '2026'→2026-01-01,
    '2026-07'→2026-07-01, '2026-07-09'→itself."""
    y, m, d = _split_date(s)
    return f"{y}-{m or '01'}-{d or '01'}"


def _norm_end(s: str) -> str:
    """A date bound → the latest full YYYY-MM-DD it denotes. '2026'→2026-12-31,
    '2026-07'→2026-07-31 (31 is a safe lexical upper bound), '2026-07-09'→itself."""
    y, m, d = _split_date(s)
    return f"{y}-{m or '12'}-{d or '31'}"


def _split_date(s: str) -> tuple[str, str | None, str | None]:
    parts = s.strip().split("-")
    if not (1 <= len(parts) <= 3) or not all(p.isdigit() for p in parts) or len(parts[0]) != 4:
        raise FilterError(f"Bad date '{s}' — use YYYY, YYYY-MM, or YYYY-MM-DD.")
    y = parts[0]
    m = parts[1].zfill(2) if len(parts) > 1 else None
    d = parts[2].zfill(2) if len(parts) > 2 else None
    return y, m, d


def _year_range(spec: str) -> tuple[int, int]:
    """'2024' → (2024, 2024); '2015-2020' → (2015, 2020)."""
    parts = spec.strip().split("-")
    try:
        if len(parts) == 1:
            y = int(parts[0])
            return y, y
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except ValueError:
        pass
    raise FilterError(f"Bad year spec '{spec}' — use YYYY or YYYY-YYYY.")


def _year_of(release_date: str | None) -> int | None:
    if not release_date or len(release_date) < 4 or not release_date[:4].isdigit():
        return None
    return int(release_date[:4])
