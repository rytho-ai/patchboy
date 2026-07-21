# threads-cli

A thin, agent-drivable CLI over Meta's **official Threads API** — your own
account: profile, your posts, publishing, replies, and insights.
Authenticates with **Authorization Code OAuth**, via a **manual paste-back**
flow (no PKCE, no loopback server — Meta doesn't support a localhost redirect
for a Threads app).

Sibling of `spotify-cli` (thin-shell shape, exit-code contract) and
`splice-cli` (the manual OAuth paste-back mechanics this borrows).

> **Status: scaffolded, not live-tested.** There is no real Meta Threads app
> to test against yet — every endpoint/param shape below reflects the public
> docs as read on 2026-07-21; several are marked `# verify-live` in the code
> and must be re-confirmed once a real app exists. See `CLAUDE.md` for the
> full list.

## What this CLI can't do (read this before assuming otherwise)

**There is no home-timeline / "my feed" read, and no confirmed mentions read**
in the core Threads API. `threads posts` returns only **your own** posts —
never other users' posts, never a feed. If you're looking for "what's
happening on Threads right now" or "what got mentioned", this CLI can't do
that; it's scoped to your own account only.

## Setup

Threads needs a Meta app with the **Threads** use case — there's no
self-service, zero-config path like Apple Music's borrowed-token auth.

```bash
threads setup    # prints the full one-time walkthrough
```

Short version:

1. Create an app at <https://developers.facebook.com/apps/> and add the
   **Threads** use case — this issues a Threads-specific App ID/Secret,
   distinct from the parent Facebook app's.
2. Register an **https** redirect URI (Meta rejects localhost/loopback). A
   placeholder like `https://localhost/` works fine — the page it points to
   never has to actually load.
3. Add yourself as a **Threads Tester**: App Dashboard → App roles → Roles →
   Add People → Threads Tester, then accept the invite in your Threads
   account's Settings. This unlocks full API access in Development Mode —
   **no App Review, no Business Verification** — for your own account.
4. Set `THREADS_APP_ID` / `THREADS_APP_SECRET` (see below for where).

## Install — two entry paths

**Global use** (`threads` available from any directory):

```bash
uv tool install .
mkdir -p ~/.threads-cli
printf 'THREADS_APP_ID=<id>\nTHREADS_APP_SECRET=<secret>\n' >> ~/.threads-cli/.env
```

**Development** (editable, run from the repo):

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env    # put THREADS_APP_ID / THREADS_APP_SECRET here
uv run threads ...
```

Precedence: real env vars > project `.env` (dev) > `~/.threads-cli/.env` (global).

## Login — manual paste-back (the only mode)

```bash
threads login
```

1. Prints an authorize URL — open it in a browser and approve.
2. You're redirected to your registered https redirect URI. That page will
   very likely **fail to load** (connection refused / blank / 404) —
   **that's expected**; the authorization code is in the URL regardless of
   whether the page renders.
3. Copy the **full URL** from the address bar (it has `?code=...&state=...`)
   and paste it back when prompted.

The CLI then exchanges the code for a short-lived token, that for a
long-lived one (~60 days), and saves the session at 0600 in
`~/.threads-cli/session.json`.

**If an AI agent is doing this on your behalf:** the paste-back flow blocks
on an interactive prompt the same way spotify-cli's `--no-server` mode does —
an agent can't feed input into an already-running background process across
separate tool calls. Have the agent call `threads_cli.auth.begin_login` /
`complete_login` directly, split across two tool invocations with the
challenge persisted to disk between them (same recipe as spotify-cli's
CLAUDE.md → "Agent-driven login"; the mechanics transfer, only the endpoint
names differ).

## Usage

```bash
threads setup                          # one-time Meta app walkthrough
threads login                          # manual paste-back OAuth
threads status                         # session present? fresh / near expiry?

threads me                             # your own profile
threads posts [--limit N]              # YOUR OWN posts only (paginated) — no feed
threads post "<text>"                  # publish a text post (writes!)
threads replies <post-id> [--conversation]   # replies on one of your posts
threads insights [<post-id>]           # post-level insights, or account-level if omitted
```

## Exit codes (agents key off these)

- `0` — ok
- `1` — ordinary failure (no results, not found, rate limited, API error, a
  bad login paste)
- `2` — a human step is required (setup / login / re-login). A panel prints
  exact steps; relay it verbatim and stop.

## Scope

Your own Threads account only: profile, your posts, publishing a text post,
replies/conversation on your posts, and insights. **No feed/timeline read, no
mentions read** (not available in the core API — see above). No image/video
publishing wired up yet (the underlying container API supports
`media_type=IMAGE|VIDEO`; `threads post` only exercises the `TEXT` path).

## Development

```bash
uv run pytest -q          # unit tests — all mocked, never hit the network
uv run ruff check .
uv run mypy src
```

See `CLAUDE.md` for the architecture, the `# verify-live` markers, and the
caged-auth rules.
