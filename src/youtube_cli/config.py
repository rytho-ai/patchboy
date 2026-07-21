"""Settings — where the API key and download dir come from.

Caller-owned, same principle as splice-cli's config.py: pass explicit values,
or fall back to env vars / .env, or sane per-user defaults. No session file
here — there's nothing to lift from a browser for the operations this CLI
covers.
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
    # Only needed for `search --api` (Data API path) and `comments` (no
    # no-API fallback exists for comments in youtube-toolkit). Every other
    # public command (info, transcript, analyze, download, and default
    # `search`) works with no key at all.
    api_key: str | None
    download_dir: Path
    # Path to the Desktop-app OAuth client_secrets.json (from Google Cloud
    # Console) that `youtube login` needs to authorize the user's OWN account
    # (my-channel/my-playlists/subscriptions). The toolkit reads this same env
    # var itself (YOUTUBE_OAUTH_CLIENT_SECRETS) at login time; it's surfaced
    # here so the CLI can give the "you haven't set this up yet" walkthrough
    # BEFORE handing off to the toolkit. None → not configured. Defaulted so
    # existing call sites/tests that predate OAuth keep constructing cleanly.
    oauth_client_secrets: str | None = None

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            api_key=os.environ.get("YOUTUBE_API_KEY"),
            download_dir=_expand(os.environ.get("YOUTUBE_DOWNLOAD_DIR", "~/Downloads/youtube")),
            oauth_client_secrets=os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRETS") or None,
        )
