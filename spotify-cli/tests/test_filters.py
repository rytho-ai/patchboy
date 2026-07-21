"""Filter logic — pure, no network. The `apply` entry point is the whole surface."""

from __future__ import annotations

import pytest

from spotify_cli import filters
from spotify_cli.filters import FilterError
from spotify_cli.models import SavedTrack, Track


def _s(name: str, *, added: str | None = None, release: str | None = None, artists=("A",)) -> SavedTrack:
    return SavedTrack(
        track=Track(id=name, name=name, uri=f"spotify:track:{name}", artists=tuple(artists), release_date=release),
        added_at=added,
    )


ITEMS = [
    _s("old", added="2024-05-10T00:00:00Z", release="2019-03-01", artists=("Nujabes",)),
    _s("mid", added="2026-03-15T12:00:00Z", release="2022-11-20", artists=("Soft Lipa", "9m88")),
    _s("new", added="2026-07-09T08:30:00Z", release="2026-01-05", artists=("HYBS",)),
]


def _names(items) -> list[str]:
    return [s.track.name for s in items]


def test_no_filters_returns_all() -> None:
    assert _names(filters.apply(ITEMS)) == ["old", "mid", "new"]


def test_since_inclusive_by_date() -> None:
    assert _names(filters.apply(ITEMS, since="2026-01-01")) == ["mid", "new"]


def test_until_inclusive_end_of_year() -> None:
    # until="2024" must include an item added 2024-05-10 (year expands to 2024-12-31)
    assert _names(filters.apply(ITEMS, until="2024")) == ["old"]


def test_since_until_window() -> None:
    assert _names(filters.apply(ITEMS, since="2026-01", until="2026-06")) == ["mid"]


def test_added_last_day_of_month_bound_included() -> None:
    item = [_s("edge", added="2026-07-31T23:00:00Z")]
    assert _names(filters.apply(item, until="2026-07")) == ["edge"]  # 2026-07 → 2026-07-31


def test_release_year_single() -> None:
    assert _names(filters.apply(ITEMS, release_year="2022")) == ["mid"]


def test_release_year_range() -> None:
    assert _names(filters.apply(ITEMS, release_year="2019-2022")) == ["old", "mid"]


def test_artist_substring_case_insensitive() -> None:
    assert _names(filters.apply(ITEMS, artist="soft lipa")) == ["mid"]
    assert _names(filters.apply(ITEMS, artist="9m88")) == ["mid"]  # matches a secondary artist


def test_filters_compose_and() -> None:
    assert _names(filters.apply(ITEMS, since="2025-01-01", release_year="2020-2027")) == ["mid", "new"]


def test_missing_data_is_excluded_when_filter_active() -> None:
    no_meta = [_s("x", added=None, release=None)]
    assert filters.apply(no_meta, since="2000-01-01") == []
    assert filters.apply(no_meta, release_year="2000-2030") == []
    # but with no filter on that axis, it stays
    assert _names(filters.apply(no_meta, artist="a")) == ["x"]


@pytest.mark.parametrize("bad", ["20xx", "2026-13-40-1", "notadate", "26"])
def test_bad_date_raises_filtererror(bad: str) -> None:
    with pytest.raises(FilterError):
        filters.apply(ITEMS, since=bad)


@pytest.mark.parametrize("bad", ["20xx", "2019-2020-2021", "abc"])
def test_bad_year_spec_raises_filtererror(bad: str) -> None:
    with pytest.raises(FilterError):
        filters.apply(ITEMS, release_year=bad)
