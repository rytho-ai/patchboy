"""atomic_write_text — round-trip, permissions, parent-dir creation."""

from __future__ import annotations

import stat

from patchboy_core.atomicio import atomic_write_text


def test_round_trips_text(tmp_path) -> None:
    path = tmp_path / "file.json"
    atomic_write_text(path, '{"a": 1}')
    assert path.read_text() == '{"a": 1}'


def test_default_mode_is_0600(tmp_path) -> None:
    path = tmp_path / "file.json"
    atomic_write_text(path, "x")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_creates_parent_dirs(tmp_path) -> None:
    path = tmp_path / "nested" / "deeper" / "file.json"
    atomic_write_text(path, "x")
    assert path.exists()
    assert path.read_text() == "x"


def test_custom_mode_is_honored(tmp_path) -> None:
    path = tmp_path / "file.json"
    atomic_write_text(path, "x", mode=0o644)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o644, f"expected 0644, got {oct(mode)}"


def test_overwrites_existing_file(tmp_path) -> None:
    path = tmp_path / "file.json"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")
    assert path.read_text() == "second"


def test_no_tmp_file_left_behind(tmp_path) -> None:
    path = tmp_path / "file.json"
    atomic_write_text(path, "x")
    assert not path.with_suffix(path.suffix + ".tmp").exists()
