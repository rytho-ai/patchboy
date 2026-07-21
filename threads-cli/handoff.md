# threads-cli — handoff (⏸ PARKED)

**Status:** parked 2026-07-21 by the owner's call — deliberately deferred, not
abandoned. The code is complete and green; it has just never run against a real
Meta Threads app. This file is the "pick it up later" checklist. (Consumer-facing
"don't rely on it yet" warning lives in `README.md`; the design rationale + the
rejected-unofficial-backend decision live in `CLAUDE.md`.)

## Done
- Full member: `setup / login / status / me / posts / post / replies / insights`.
- Auth: Authorization-Code OAuth, **manual paste-back** (no PKCE, no loopback —
  Meta doesn't allow a localhost redirect), 3-hop `code → short-lived →
  long-lived (~60d)` + guarded refresh. Session saved 0600 via `patchboy-core`.
- Gate green: ruff + mypy clean, **57 tests** — but ALL network is mocked.
- Decision recorded: no unofficial/private-API backend (see `CLAUDE.md` →
  "Unofficial … REJECTED" — probed, the whole 2023 surface is dead).

## Not done
- **Never executed against a real Meta app.** Several wire shapes are best-guess
  Meta-standard patterns, marked `# verify-live` in `auth.py` / `client.py`.

## To resume — do this, in order
1. **Register the Meta app** (one-time, only the owner can): run `threads setup`
   and follow it — create a Meta app with the "Threads" use case, grab the
   Threads-specific App ID/Secret, register an https redirect URI, add yourself
   as a **Threads Tester** (App roles → Roles). Set `THREADS_APP_ID`,
   `THREADS_APP_SECRET`, `THREADS_REDIRECT_URI` (+ optional `~/.threads-cli/.env`).
2. `threads login` — open the printed URL, approve, paste the redirected URL back.
3. `threads status` / `threads me` / `threads posts` — confirm reads work.
4. Try `threads post "..."` (a real write — use a throwaway post), `threads
   insights`, `threads replies <id>`.

## The `# verify-live` checklist — confirm each against a REAL response, fix if wrong
These are the specific best-guesses that could differ from Meta's actual API:
- **Redirect URI (login-UX crux):** does Meta accept `https://localhost/` as a
  registered redirect, or must it be a real https URL you control? If the former
  is rejected, update `auth.DEFAULT_REDIRECT_URI` + the `setup` walkthrough.
- **Long-lived exchange** (`auth._exchange_long_lived`): endpoint
  `GET graph.threads.net/access_token`, params `grant_type=th_exchange_token`,
  `client_secret`, `access_token`; response field `access_token` + `expires_in`.
- **Refresh** (`auth.refresh`): `GET /refresh_access_token`,
  `grant_type=th_refresh_token`; and the ≥24h-old / not-expired constraint.
- **Token-exchange response fields:** `access_token`, `user_id`, `expires_in`.
- **Auth transport** (`ThreadsSession.params`): `access_token` as a QUERY param
  (assumed) vs an `Authorization: Bearer` header.
- **Insights value shape** (`client` / `render`): `values[].value` vs
  `total_value.value`.
- **Error classification** (`client._handle_response`): Graph tags rate-limits
  (codes 4/17/32) as `type: "OAuthException"` too — the rate-limit code is
  checked BEFORE the auth-type on purpose; confirm the real error envelope
  matches, so a rate-limit doesn't misroute to the exit-2 login handoff.

## Where things live if they break
- Token/OAuth issues → `src/threads_cli/auth.py`.
- Endpoint shapes / error routing → `src/threads_cli/client.py`.
- Command wiring / exit codes → `src/threads_cli/cli.py`; output → `render.py`.
- Config/env + session path → `config.py` / `session.py`.
