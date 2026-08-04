"""Settings — where the API key, download dir, and network policy come from.

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
    # Network policy for official Data API calls, passed straight to
    # YouTubeToolkit(request_timeout_sec=, transport_retries=) (v2.1.0+).
    # Defaults are deliberately opinionated for a CLI: a 30s socket timeout
    # so a dead connection errors instead of hanging forever, and one retry
    # of transient transport failures (SSL / reset — quota and auth errors
    # are never retried upstream). Timeout ≤ 0 in the env var means "no
    # timeout" (None).
    request_timeout_sec: float | None = 30.0
    transport_retries: int = 1

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        timeout = float(os.environ.get("YOUTUBE_REQUEST_TIMEOUT_SEC", "30"))
        return cls(
            api_key=os.environ.get("YOUTUBE_API_KEY"),
            download_dir=_expand(os.environ.get("YOUTUBE_DOWNLOAD_DIR", "~/Downloads/youtube")),
            oauth_client_secrets=os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRETS") or None,
            request_timeout_sec=timeout if timeout > 0 else None,
            transport_retries=int(os.environ.get("YOUTUBE_TRANSPORT_RETRIES", "1")),
        )
