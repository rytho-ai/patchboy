"""youtube-cli — a thin CLI + agent skill over youtube-toolkit.

All YouTube-facing logic (extraction, fallback chains, Data API access)
lives in youtube-toolkit; this package only adds a command-line shell and
agent-facing rendering on top. Unlike splice-cli, there's no login/session
layer here — search/info/transcript/analyze/download are all public,
unauthenticated operations (Data API search needs a key, not a login).
"""

__version__ = "0.1.0"
