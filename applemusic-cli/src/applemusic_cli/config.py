"""Settings — the storefront, where the session lives, and an optional token.

Caller-owned, same principle as sibling spotify-cli/splice-cli: pass explicit
values, or fall back to env vars / .env, or sane per-user defaults. Nothing
reaches for a global singleton, so the same code serves the CLI, an agent, and
tests without stepping on each other's session file.

Unlike spotify-cli there is NO required credential: the developer token is
scraped automatically (auth.fetch_developer_token). The only per-user secret is
the media-user-token, normally supplied via `applemusic login` (→ session), but
`APPLE_MUSIC_USER_TOKEN` lets an agent/CI inject it without the paste flow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


# A home-dir .env so a globally-installed (`uv tool install`) `applemusic`
# picks up an env-injected token / storefront from ANY directory, matching
# spotify-cli's fallback. Precedence below is override=False → first wins.
_GLOBAL_ENV = "~/.applemusic-cli/.env"


@dataclass(frozen=True)
class Settings:
    storefront: str
    session_path: Path
    media_user_token: str | None  # env-injected override; None → use the session's

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            # Precedence: real env vars > project .env (dev) > ~/.applemusic-cli/.env.
            load_dotenv(override=False)
            home_env = Path(_GLOBAL_ENV).expanduser()
            if home_env.is_file():
                load_dotenv(home_env, override=False)

        return cls(
            storefront=os.environ.get("APPLE_MUSIC_STOREFRONT", "us"),
            session_path=_expand(
                os.environ.get("APPLE_MUSIC_SESSION_PATH", "~/.applemusic-cli/session.json")
            ),
            media_user_token=os.environ.get("APPLE_MUSIC_USER_TOKEN") or None,
        )
