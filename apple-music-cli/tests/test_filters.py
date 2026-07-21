"""filters.py — client-side filtering over LibrarySong."""

from __future__ import annotations

import pytest

from apple_music_cli import filters
from apple_music_cli.filters import FilterError
from apple_music_cli.models import LibrarySong, Song


def _item(added: str | None = None, release: str | None = None, artists: tuple[str, ...] = ()) -> LibrarySong:
    return LibrarySong(song=Song(id="x", type="songs", name="n", artists=artists, release_date=release), added_at=added)


def test_since_until_keep_in_range() -> None:
    items = [_item("2026-01-05"), _item("2026-07-20"), _item("2027-01-01")]
    got = filters.apply(items, since="2026-06", until="2026-12")
    assert [i.added_at for i in got] == ["2026-07-20"]


def test_missing_added_at_is_excluded_when_time_filtering() -> None:
    assert filters.apply([_item(added=None)], since="2020") == []


def test_release_year_range() -> None:
    items = [_item(release="1999-01-01"), _item(release="2005-06-06"), _item(release="2021")]
    got = filters.apply(items, release_year="2000-2010")
    assert [i.song.release_date for i in got] == ["2005-06-06"]


def test_artist_substring_case_insensitive() -> None:
    items = [_item(artists=("Radiohead",)), _item(artists=("Aphex Twin",))]
    assert filters.apply(items, artist="aphex") == [items[1]]


def test_bad_date_raises() -> None:
    with pytest.raises(FilterError):
        filters.apply([_item("2026-01-01")], since="not-a-date")


def test_bad_year_spec_raises() -> None:
    with pytest.raises(FilterError):
        filters.apply([_item(release="2000")], release_year="20xx")
