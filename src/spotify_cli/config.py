"""Settings — the client_id, where the session lives, and the callback port.

Caller-owned, same principle as sibling splice-cli/youtube-cli: pass explicit
values, or fall back to env vars / .env, or sane per-user defaults. Nothing
reaches for a global singleton, so the same code serves the CLI, an agent, and
tests without stepping on each other's session file.

The only credential-shaped value is `client_id` — a public identifier of the
Dashboard-registered app, not a secret (PKCE sends no client_secret). It's
still read from the environment so it isn't hard-coded per user.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .auth import DEFAULT_REDIRECT_PORT


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    client_id: str | None  # SPOTIFY_CLIENT_ID — required for login; None → guide the user
    session_path: Path
    redirect_port: int

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
            session_path=_expand(
                os.environ.get("SPOTIFY_SESSION_PATH", "~/.spotify-cli/session.json")
            ),
            redirect_port=int(os.environ.get("SPOTIFY_REDIRECT_PORT", DEFAULT_REDIRECT_PORT)),
        )
