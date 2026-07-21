# threads-cli — live-verify (un-park) — plan

> Generated: 2026-07-21 · Board entry: `../plan.md` → ⏸ Future → "PARKED: threads-cli live-verify" ·
> Written to be executable by a Sonnet-class agent (or the owner): exact commands, the
> per-item # verify-live checklist, and where each thing lives if it breaks.

## Context

`threads-cli` (member of the patchboy workspace) is a thin CLI over Meta's
**official Threads API** for the user's own account. It is **complete and green**
— `ruff` + `mypy` clean, **57 tests** — but every test mocks the network, and the
CLI has **never run against a real Meta Threads app**. Several wire shapes are
best-guess Meta-standard patterns, marked `# verify-live` in `auth.py` /
`client.py`. This plan un-parks it: register the app, run it live once, and
confirm (or fix) each guessed shape.

Auth is Authorization-Code OAuth via a **manual paste-back** flow (no PKCE, no
loopback — Meta doesn't allow a localhost redirect for a Threads app). Design
rationale + the (rejected) unofficial-backend decision are in
`../../../threads-cli/CLAUDE.md`.

## Steps — in order

1. **Register the Meta app** (one-time; only the owner can). Run `threads setup`
   and follow it: create a Meta app with the "Threads" use case → grab the
   Threads-specific App ID/Secret → register an https redirect URI → add yourself
   as a **Threads Tester** (App Dashboard → App roles → Roles). Set
   `THREADS_APP_ID`, `THREADS_APP_SECRET`, `THREADS_REDIRECT_URI` (env, or a
   `~/.threads-cli/.env`).
2. `threads login` — open the printed authorize URL, approve, paste the
   redirected URL back.
3. `threads status` / `threads me` / `threads posts` — confirm reads work.
4. `threads insights` / `threads replies <id>` — confirm; then one real
   `threads post "<throwaway>"` to validate the 2-step publish.

## The `# verify-live` checklist — confirm each against a REAL response; fix if wrong

- **Redirect URI (login-UX crux):** does Meta accept `https://localhost/` as a
  registered redirect, or must it be a real https URL you control? If rejected,
  update `auth.DEFAULT_REDIRECT_URI` + the `setup` walkthrough text.
- **Long-lived exchange** (`auth._exchange_long_lived`): `GET
  graph.threads.net/access_token`, params `grant_type=th_exchange_token`,
  `client_secret`, `access_token`; response `access_token` + `expires_in`.
- **Refresh** (`auth.refresh`): `GET /refresh_access_token`,
  `grant_type=th_refresh_token`; and the ≥24h-old / not-expired constraint.
- **Token-exchange response fields:** `access_token`, `user_id`, `expires_in`.
- **Auth transport** (`ThreadsSession.params`): `access_token` as a QUERY param
  (assumed) vs an `Authorization: Bearer` header.
- **Insights value shape** (`client` / `render`): `values[].value` vs
  `total_value.value`.
- **Error classification** (`client._handle_response`): Graph tags rate-limits
  (codes 4/17/32) as `type: "OAuthException"` too — the rate-limit code is
  checked BEFORE the auth-type on purpose; confirm the real error envelope so a
  rate-limit doesn't misroute to the exit-2 login handoff.

## Where things live if they break

- Token / OAuth → `threads-cli/src/threads_cli/auth.py`.
- Endpoint shapes / error routing → `threads-cli/src/threads_cli/client.py`.
- Command wiring / exit codes → `cli.py`; output → `render.py`.
- Config/env + session path → `config.py` / `session.py`.

## Verification

After live-fixing, re-run the gate (`cd threads-cli && uv run --no-sync ruff
check . && uv run --no-sync mypy src && uv run --no-sync pytest -q`), update any
test fixtures whose shape changed, remove the resolved `# verify-live` markers,
lift the ⏸ PARKED banner in `threads-cli/README.md`, and move this entry from
⏸ Future to ✅ Shipped in `../plan.md`.
