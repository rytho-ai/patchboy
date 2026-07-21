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


# A home-dir .env so a globally-installed (`uv tool install`) `spotify` works from
# ANY directory — SPOTIFY_CLIENT_ID is required for login/refresh, and the
# CWD-relative `.env` only covers dev-run-from-the-repo. Unlike siblings we need
# this: splice has no required credential, youtube's key is optional, but our
# client_id is mandatory for token refresh.
_GLOBAL_ENV = "~/.spotify-cli/.env"


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
            # Precedence (override=False → first wins): real env vars > project
            # .env (dev, run from the repo) > ~/.spotify-cli/.env (global use).
            load_dotenv(override=False)
            home_env = Path(_GLOBAL_ENV).expanduser()
            if home_env.is_file():
                load_dotenv(home_env, override=False)

        return cls(
            client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
            session_path=_expand(
                os.environ.get("SPOTIFY_SESSION_PATH", "~/.spotify-cli/session.json")
            ),
            redirect_port=int(os.environ.get("SPOTIFY_REDIRECT_PORT", DEFAULT_REDIRECT_PORT)),
        )
