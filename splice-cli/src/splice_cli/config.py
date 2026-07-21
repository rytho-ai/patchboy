"""Settings — where the session and downloads live.

Session path and download dir are caller-owned: pass them in, or fall back
to env vars, or to sensible per-user defaults. Nothing here reaches for a
global singleton, so the same code serves the CLI, an agent, and tests
without stepping on each other's session file.

There are no credentials here — Splice's login is Turnstile-gated, so you
log in by hand in a browser and we lift the session (see firefox.py/chrome.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    session_path: Path
    download_dir: Path
    # Standing spend pool for auto-purchases. Only `splice budget set` (a
    # human-run command) writes to this file — the skill must never call it.
    budget_path: Path
    # OAuth token set for the official Splice MCP (search/download/stacks
    # default backend) — independent of session_path's cookie-lifted session.
    mcp_session_path: Path
    # Default for `download`/`buy`'s --confirm/--no-confirm: print the sample's
    # splice.com page + (for buy) its price and ask before proceeding. Off by
    # default — preserves the existing automatic, agent-driven behavior; a
    # human who wants the listen-first gate opts in via env or the per-call flag.
    confirm_before_download: bool

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            session_path=_expand(
                os.environ.get("SPLICE_SESSION_PATH", "~/.splice-cli/session.json")
            ),
            download_dir=_expand(
                os.environ.get("SPLICE_DOWNLOAD_DIR", "~/Downloads/splice")
            ),
            budget_path=_expand(
                os.environ.get("SPLICE_BUDGET_PATH", "~/.splice-cli/budget.json")
            ),
            mcp_session_path=_expand(
                os.environ.get("SPLICE_MCP_SESSION_PATH", "~/.splice-cli/mcp-session.json")
            ),
            confirm_before_download=os.environ.get(
                "SPLICE_CONFIRM_BEFORE_DOWNLOAD", ""
            ).strip().lower() in ("1", "true", "yes"),
        )
