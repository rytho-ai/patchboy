"""splice-cli — Unofficial Splice automation toolkit for coding agents.

Library-first: `client`, `firefox`, and `chrome` are the only modules that
know what Splice / the browser cookie stores look like. Everything above them
(models, cli) stays stable when Splice ships a redesign — you fix the caged
modules, not the callers.

Login is always a human step (Splice's Cloudflare Turnstile forces it); this
toolkit never handles credentials — it lifts an already-logged-in session
from the user's browser.
"""

__version__ = "0.1.0"
