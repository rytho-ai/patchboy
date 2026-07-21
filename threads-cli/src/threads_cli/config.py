"""Settings — the app id/secret, redirect URI, and where the session lives.

Caller-owned, same principle as sibling spotify-cli/applemusic-cli: pass
explicit values, or fall back to env vars / .env, or sane per-user defaults.
Nothing reaches for a global singleton, so the same code serves the CLI, an
agent, and tests without stepping on each other's session file.

`app_id`/`app_secret` are the Threads-specific App ID/Secret from the user's
Meta app's "Threads" use case (see `threads setup`) — both required to log
in; neither is bundled or defaulted, since they're per-developer-app values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .auth import DEFAULT_REDIRECT_URI


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


# A home-dir .env so a globally-installed (`uv tool install`) `threads` works
# from ANY directory — mirrors spotify-cli's ~/.spotify-cli/.env fallback.
_GLOBAL_ENV = "~/.threads-cli/.env"


@dataclass(frozen=True)
class Settings:
    app_id: str | None  # THREADS_APP_ID — required for login; None → guide to `threads setup`
    app_secret: str | None  # THREADS_APP_SECRET — required for login
    redirect_uri: str
    session_path: Path

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            # Precedence (override=False → first wins): real env vars > project
            # .env (dev, run from the repo) > ~/.threads-cli/.env (global use).
            load_dotenv(override=False)
            home_env = Path(_GLOBAL_ENV).expanduser()
            if home_env.is_file():
                load_dotenv(home_env, override=False)

        return cls(
            app_id=os.environ.get("THREADS_APP_ID"),
            app_secret=os.environ.get("THREADS_APP_SECRET"),
            redirect_uri=os.environ.get("THREADS_REDIRECT_URI", DEFAULT_REDIRECT_URI),
            session_path=_expand(
                os.environ.get("THREADS_SESSION_PATH", "~/.threads-cli/session.json")
            ),
        )
