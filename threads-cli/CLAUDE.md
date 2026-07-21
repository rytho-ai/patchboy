# CLAUDE.md — Development principles for threads-cli

> Part of the **patchboy** workspace (`packages/patchboy/`) — see its README +
> `docs/adr/0001-patchboy-workspace.md` for the monorepo shape. The atomic
> 0600 secret-file write lives in shared **patchboy-core**; this package's own
> auth/wire/render/exit-code logic stays here (core holds only byte-for-byte
> identical cross-member primitives).

Governance for Claude (or any AI assistant) working on this codebase. Same
discipline as siblings `spotify-cli` (thin shell, exit-code contract) and
`splice-cli` (the manual OAuth paste-back shape this borrows). This repo is a
**thin shell over the official Threads API** — the single most important rule
is **stay thin**: wire knowledge stays caged.

## Prime directive: thin shell, caged wire

The CLI owns four things and nothing else:

1. **Argument parsing** (Typer commands + flags).
2. **Calling the caged client** (`client.Client`).
3. **Rendering** the returned models (`render.py`).
4. **Presenting errors + exit codes** (0/1/2 — never a raw traceback).

Everything about Threads' wire — endpoints, JSON shapes, the OAuth token
chain, error/rate-limit classification — lives in the **caged** modules. If
you import `httpx`, an OAuth detail, or a raw Threads JSON shape *outside* a
caged module, stop: it belongs in one.

```
cli.py       thin shell an agent drives (parse → client → render → exit code)
render.py    presentation only (rich tables/panels; markup.escape every upstream string)
models.py    stable hand-authored vocabulary (Profile/Post/Reply/Insights)
config.py    Settings: app id/secret, redirect uri, session path (caller-owned)
── caged ──   the only modules that know Threads' surface:
auth.py      Authorization Code OAuth, MANUAL paste-back only (no PKCE)
session.py   atomic 0600 token persistence
client.py    graph.threads.net calls + JSON→models maps
```

## Auth — the load-bearing details

- **Manual paste-back is the ONLY login flow — there is no loopback-server
  mode.** Meta does not support a localhost/loopback redirect URI for a
  Threads app (only registered https URIs), unlike spotify-cli's default
  127.0.0.1 loopback. `threads login` always: builds the authorize URL
  (`auth.begin_login`), the user approves in a browser and is redirected to
  the registered https URI (that page will typically fail to load — expected,
  the code is in the URL regardless), they paste the full URL back
  (`auth.complete_login`).
- **No PKCE.** Threads uses a plain Authorization Code + `client_secret`
  exchange — no `code_verifier`/`code_challenge`. Both `THREADS_APP_ID` and
  `THREADS_APP_SECRET` are required config values from the Meta app's
  "Threads" use case (distinct from the parent Facebook app's own
  credentials).
- **Three-hop token chain.** `code` → short-lived access_token (~1h, carries
  `user_id`) → long-lived access_token (`th_exchange_token`, ~60 days). Only
  the long-lived token is persisted. `auth.refresh` (`th_refresh_token`)
  extends it another ~60 days, but — per Meta's docs — only works on a token
  that is already **≥24h old and not yet expired**; an *expired* token can
  only be fixed by re-running `threads login`. `_live_client` in cli.py
  respects this: it never attempts to refresh an already-expired session, and
  a refresh failure on a near-expiry (but still valid) token is swallowed —
  the still-valid token keeps working for that call.
- **Secrets never touch git or stdout.** Real tokens live at 0600 in
  `~/.threads-cli/session.json`; print only user_id and freshness state,
  never a token.

### `# verify-live` markers — read before trusting these against a real app

There is no real Meta Threads app to test against yet (the user registers
one later per `threads setup`), so several endpoint/param shapes were
inferred from the documented Meta Graph API conventions rather than
independently re-confirmed live. Search for `# verify-live` in `auth.py` and
`client.py`; as of this writing:

1. **Long-lived token exchange** (`GET /access_token?grant_type=th_exchange_token`)
   and **refresh** (`GET /refresh_access_token?grant_type=th_refresh_token`)
   param names — the *task* that produced this CLI marked these as following
   "the standard Meta pattern," not independently re-confirmed.
2. **Token-exchange response field names** (`access_token`, `user_id`,
   `expires_in`) — assumed from the standard shape, not seen live.
3. **Error envelope classification** in `client.py`
   (`{"error": {"type": "OAuthException", "code": 190}}` → auth failure;
   codes 4/17/32 → rate limit) — the classic Facebook/Instagram Graph API
   error shape, not confirmed against a real Threads response.
4. **Insights value shape** (`values: [{"value": N}]` vs `total_value.value`)
   — both are documented patterns for different metric kinds; which one a
   given metric actually returns wasn't verified live.
5. **`access_token` as a query param** (not a Bearer header) — the standard
   Graph API auth convention, applied here without a live re-check.

Fix any of these **inside `auth.py`/`client.py`** the moment a real app
surfaces a mismatch — `cli.py`/`models.py` must stay ignorant either way.

## The capability gap — don't paper over it

**There is no home-timeline / "my feed" read and no confirmed mentions read
in the core Threads API.** `threads posts` returns only the logged-in user's
OWN posts (`GET /me/threads`) — never other users' posts, never a feed. Do
not add a `feed`/`timeline`/`mentions` command without first confirming the
underlying endpoint actually exists and is in scope for a personal-use app;
the docs surveyed to build this CLI did not surface one. Say this plainly in
any UX/help text that could otherwise imply broader read access than exists.

### Unofficial (private-API) backend — probed 2026-07-21, REJECTED. Don't re-litigate.

The feed / other-users / search / mentions gap above is only fillable via the
UNOFFICIAL private API (log in with Instagram credentials, hit Threads' app
API — the `instagrapi` / `Danie1-threads-api` lineage). We probed whether to
add it as a caged second backend (à la splice-cli's official-default +
break-glass GraphQL). **Verdict: do not add it.** Not merely "too risky" —
**there is nothing alive to build on**, verified with ZERO account risk (a
public, no-login reproduction from a datacenter IP):

- **Threads moved domains: `threads.net` → `threads.com`.** Every 2023 lib
  hardcodes the dead `threads.net`; step 1 (`GET /@user`) 302s to the new host.
- **All the libraries are 2023 corpses** — `Danie1/threads-api`,
  `junhoyeo/threads-api` + `threads-py`, `threads-net` are archived/dead since
  ~2023, several explicitly after a Meta cease-and-desist. `Danie1/threads-api`
  won't even import on a 2026 stack (Pydantic-v1 field names rejected by
  Pydantic 2, which its own `instagrapi` backing forces). `instagrapi`'s
  "threads" is Instagram DMs, not the Threads app.
- **The page shape changed** — the 2023 `user_id`/token scrape patterns no
  longer match `threads.com`, so you can't even reach the GraphQL layer to test
  a (long-since-rotated) `doc_id`.

So the unofficial route is now a **from-scratch reverse-engineering project**
against `threads.com` (rediscover endpoints / `doc_id`s / auth) + perpetual
maintenance vs Meta's rotation + **Med-High account-ban risk that is borne by
the user's Instagram account** (a Threads login IS an Instagram login). That is
a deliberate, separately-scoped, risk-accepted project — NOT a quick backend.
**The official OAuth flow is the only backend; keep it that way** unless the
owner explicitly takes on that reverse-engineering project with eyes open.

## Human-in-the-loop — the exit-code convention

- **0** — ok.
- **2** — a human step is required (setup / login / re-login). Print a panel
  with exact steps (`_needs_human` / `_setup_panel`), relay verbatim, STOP.
- **1** — an ordinary failure (no results, not found, rate limited, API
  error, a bad login paste — login IS the human step already, so its own
  failures are exit 1, not a second exit-2 handoff).

## Adding a capability — which layer

1. **New Threads call / a changed JSON shape** → `client.py` (caged), mapping
   the raw shape onto a `models.py` type. Never leak wire shapes upward.
2. **New user vocabulary** → extend `models.py` (hand-authored, minimal —
   only fields the CLI renders).
3. **New command / flag / rendering** → `cli.py` (+ `render.py`). Surface a
   capability the client already supports; if it doesn't, do step 1 first.
4. Before adding any endpoint not in the "Data endpoints" list this CLI was
   scaffolded against, confirm it actually exists in the Threads API docs —
   don't infer one by analogy to Instagram/Facebook Graph API.

## Environment & tooling

- **src layout (required)** — package under `src/threads_cli/`.
- **`uv` first, two entry paths** — daily use is `uv tool install .` then a
  bare `threads` from anywhere; development is `uv run …` + `uv pip install
  -e ".[dev]"` from the repo. Because `THREADS_APP_ID`/`THREADS_APP_SECRET`
  are **required** for login, `Settings.load()` reads a home-dir
  `~/.threads-cli/.env` as a fallback so global use works from any directory
  without a project `.env`. Precedence: real env vars > project `.env` (dev)
  > `~/.threads-cli/.env` (global). Don't regress that fallback.
- **Scaffolded, not live-tested.** There is no real Meta Threads app yet —
  every test mocks at the `httpx` boundary (`monkeypatch.setattr(client_module.httpx,
  "request", ...)`, same pattern as spotify-cli's `test_client.py`). Do NOT
  attempt a live Threads call from this repo until a real app exists; if you
  add one, gate it behind an explicit opt-in and never run it in CI.
- Worth covering (already covered, keep it that way): the manual paste-back
  redirect-response parser (full URL vs bare code), the three-hop token
  exchange + refresh, session atomic save/load + 0600, JSON→model maps
  against the shapes above, pagination (`paging.next` as an absolute URL, not
  re-prefixed), and the exit-code contract per command.

## Communication

Converse with the user in **Traditional Chinese (Taiwanese phrasing)** —
plain, direct, concrete analogies. Keep code, identifiers, commit messages,
and this file in English.
