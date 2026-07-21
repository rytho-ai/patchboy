# CLAUDE.md — Development principles for spotify-cli

> Part of the **patchboy** workspace (`packages/patchboy/`) — see its README +
> `docs/adr/0001-patchboy-workspace.md` for the monorepo shape. The atomic
> 0600 secret-file write lives in shared **patchboy-core**; this package's own
> auth/wire/render/exit-code logic stays here (core holds only byte-for-byte
> identical cross-member primitives).

Governance for Claude (or any AI assistant) working on this codebase. Same
discipline as siblings `youtube-cli` (thin shell) and `splice-cli` (caged
auth). This repo is a **thin shell over the official Spotify Web API**: the
single most important rule is **stay thin** — wire knowledge stays caged.

## Prime directive: thin shell, caged wire

The CLI owns four things and nothing else:

1. **Argument parsing** (Typer commands + flags).
2. **Calling the caged client** (`client.Client`).
3. **Rendering** the returned models (`render.py`).
4. **Presenting errors + exit codes** (0/1/2 — never a raw traceback).

Everything about Spotify's wire — endpoints, JSON shapes, auth token flow,
rate-limit classification — lives in the **caged** modules. If you import
`httpx`, an OAuth detail, or a raw Spotify JSON shape *outside* a caged module,
stop: it belongs in one.

```
cli.py       thin shell an agent drives (parse → client → render → exit code)
render.py    presentation only (rich tables; markup.escape every upstream string)
models.py    stable hand-authored vocabulary (Track/Album/Artist/Playlist/...)
config.py    Settings: client_id, session path, redirect port (caller-owned)
── caged ──   the only modules that know Spotify's surface:
auth.py      OAuth Authorization Code + PKCE (accounts.spotify.com)
session.py   atomic 0600 token persistence
client.py    api.spotify.com/v1 calls + JSON→models maps
```

## Auth — the load-bearing details

- **PKCE, no client_secret.** `client_id` is a public config value from the
  Dashboard app; there is no dynamic client registration (unlike splice-cli).
- **Redirect URI is `http://127.0.0.1:PORT`, never `localhost`.** Spotify
  rejects a `localhost` redirect with an opaque `INVALID_CLIENT`. Register the
  portless `http://127.0.0.1/callback` in the Dashboard; the runtime supplies a
  free port. Both the `HTTPServer` bind and the `redirect_uri` string use the IP
  literal — an easy, silent-failing thing to regress.
- **Refresh may not return a new refresh token** (Spotify's own caveat) — keep
  the old one when absent (`auth.refresh` already does).
- **Secrets never touch git or stdout.** Real tokens live at 0600 in
  `~/.spotify-cli/session.json`; print only expiry state, never a token.

## Agent-driven login — don't run `spotify login` interactively yourself

If you (an AI assistant) are doing the login on the user's behalf, **neither
of the CLI's two login modes work directly for you**, for different reasons:

- **`spotify login` (default, loopback server).** If you run this via your own
  Bash/tool sandbox, the `HTTPServer` may bind inside a network namespace that
  is NOT the same loopback the user's real browser uses — even if `hostname`/
  `ps` make your sandbox look like the user's machine. Symptom: you print an
  authorize URL, the user logs in, and their browser gets
  `ERR_CONNECTION_REFUSED` on the `127.0.0.1` redirect — because nothing you
  bind is reachable from their actual desktop. Confirmed by direct observation:
  a socket that showed `LISTEN` via `ss` in the tool's own shell was still
  unreachable from the user's browser. **Never assume your Bash tool shares a
  loopback with the user's browser — always use the manual flow instead.**
- **`spotify login --no-server` (manual paste-back).** This is the right
  *mechanism*, but it's built for a human sitting at an interactive terminal —
  it blocks on `typer.prompt("Paste the redirect URL")`, and you have no way to
  feed stdin into an already-running background process across separate tool
  calls.

**What to do instead: call the same underlying primitives yourself, split
across two tool invocations, with the challenge persisted to disk between
them.** Reuse `auth.begin_login`/`auth.complete_login` directly — do not
reimplement PKCE or the token exchange.

1. First call — build the challenge, persist it, print the URL:
   ```bash
   uv run python3 - <<'PYEOF'
   import json
   from pathlib import Path
   from spotify_cli.config import Settings
   from spotify_cli import auth

   settings = Settings.load()
   ch = auth.begin_login(client_id=settings.client_id, redirect_port=settings.redirect_port)
   p = Path.home() / ".spotify-cli" / ".login-challenge.json"
   p.parent.mkdir(parents=True, exist_ok=True)
   p.write_text(json.dumps({"verifier": ch.verifier, "state": ch.state,
                             "redirect_uri": ch.redirect_uri, "authorize_url": ch.authorize_url}))
   p.chmod(0o600)
   print("AUTHORIZE_URL:" + ch.authorize_url)
   PYEOF
   ```
2. Give the user the printed URL. Tell them: open it in their own browser,
   log in, approve — the browser will then show a connection-refused-looking
   error on the `127.0.0.1` redirect, **that's expected**, ignore it. Copy the
   **full** URL from the address bar (must include `?code=...&state=...`) and
   paste it back to you.
3. Second call (only after they reply) — load the persisted challenge,
   complete the exchange, save the session, clean up. Pass their pasted text
   via an env var, not string-interpolated into the script (avoid quoting
   surprises):
   ```bash
   PASTED_URL='<paste exactly what they gave you>' uv run python3 - <<'PYEOF'
   import os, json
   from pathlib import Path
   from spotify_cli.config import Settings
   from spotify_cli import auth, session
   from spotify_cli.auth import LoginChallenge, SpotifyAuthError

   settings = Settings.load()
   p = Path.home() / ".spotify-cli" / ".login-challenge.json"
   data = json.loads(p.read_text())
   ch = LoginChallenge(**data)
   try:
       sess = auth.complete_login(ch, os.environ["PASTED_URL"], client_id=settings.client_id)
   except SpotifyAuthError as err:
       print("LOGIN_FAILED: " + str(err)); raise SystemExit(1)
   session.save(sess, settings.session_path)
   p.unlink(missing_ok=True)
   print(f"LOGIN_OK saved_to={settings.session_path}")
   PYEOF
   ```
4. Verify with `uv run spotify status` before doing anything else.

The one thing you can never do for the user is the actual browser click — that
needs their own Spotify credentials. Everything else above is fair game.

## Human-in-the-loop — the exit-code convention

- **0** — ok.
- **2** — a human step is required (login / re-login). Print a panel with exact
  steps (`_needs_human`), relay verbatim, STOP. Login needs a browser; the CLI
  only completes the loopback bridge afterward.
- **1** — an ordinary failure (no results, not found, rate limit, API error).

## Development Mode is permanent — design to the surviving surface

A personal app never leaves Development Mode. Endpoints unavailable there
(recommendations, audio-features/analysis, related-artists, artist top-tracks,
browse/new-releases, all batch "get several", 30s preview URLs) are **out of
scope** — don't add a command that calls one. Build on: single-item catalog
GETs, `search` (limit ≤ 10), and `/me/*`. Several write-side shapes reflect
Spotify's Feb 2026 restructuring and carry `# verify-live` in `client.py`;
confirm each against a real account before removing the marker.

## Adding a capability — which layer

1. **New Spotify call / a changed JSON shape** → `client.py` (caged), mapping the
   raw shape onto a `models.py` type. Never leak wire shapes upward.
2. **New user vocabulary** → extend `models.py` (hand-authored, minimal — only
   fields the CLI renders).
3. **New command / flag / rendering** → `cli.py` (+ `render.py`). Surface a
   capability the client already supports; if it doesn't, do step 1 first.

## Environment & tooling

- **src layout (required)** — package under `src/spotify_cli/`.
- **`uv` first, two entry paths** — daily use is `uv tool install .` then a bare
  `spotify` from anywhere; development is `uv run …` + `uv pip install -e ".[dev]"`
  from the repo. Both are first-class. Because `SPOTIFY_CLIENT_ID` is **required**
  for login/refresh (unlike splice's no-credential and youtube's optional-key
  configs), `Settings.load()` reads a home-dir `~/.spotify-cli/.env` as a fallback
  so global use works from any directory without a project `.env`. Precedence:
  real env vars > project `.env` (dev) > `~/.spotify-cli/.env` (global). Don't
  regress that fallback — it's what makes `uv tool install` usable.
- Tests mock at the client boundary; do NOT hit the network in unit tests. Worth
  covering: PKCE pair correctness, session atomic save/load + 0600, refresh
  fallback when no new refresh_token, JSON→model maps against captured fixtures,
  and the exit-code contract per command.

## Communication

Converse with the user in **Traditional Chinese (Taiwanese phrasing)** — plain,
direct, concrete analogies. Keep code, identifiers, commit messages, and this
file in English.
