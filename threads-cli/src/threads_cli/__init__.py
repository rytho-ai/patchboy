"""threads-cli — Threads API CLI for coding agents.

Your own account (profile, posts, replies, insights, publish) over Meta's
official Threads API. Authenticates with Authorization Code OAuth via a
MANUAL paste-back flow (no PKCE, no loopback server — Meta doesn't support a
localhost redirect for a Threads app). Thin-shell shape (cli → client →
render) borrowed from sibling spotify-cli; the manual OAuth paste-back
mechanics borrowed from splice-cli's mcp_auth.py.
"""

__version__ = "0.1.0"
