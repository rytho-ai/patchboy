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
    # Only needed for `search` (Data API path) and `trending`. Every other
    # command works with no key at all.
    api_key: str | None
    download_dir: Path

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            api_key=os.environ.get("YOUTUBE_API_KEY"),
            download_dir=_expand(os.environ.get("YOUTUBE_DOWNLOAD_DIR", "~/Downloads/youtube")),
        )
