# patchboy — plan

> 2026-07-21 · status index (one layer, by status). Only "what to do + which doc".
> grounded plans in `plans/`, ADRs in `../adr/`. Each member's own docs live in `<member>/`.

## 🚧 In progress

> nothing mid-flight — the workspace migration + core extraction have shipped.

## ▶ Next —— 接下來

- **More external-platform members** — the container's whole point. Media
  (SoundCloud, TikTok) + social (Instagram). Each arrives as a new member, not
  a new repo. Threads is done-but-parked (see Future).
- **Instagram** — blocked on an account decision, not engineering: the official
  Graph API needs a Business/Creator account (personal read died Dec 2024); the
  unofficial route carries ban risk on the user's IG account. Decide the account
  question before starting.

## ⏸ Future —— deferred

- **⏸ PARKED: threads-cli live-verify** — the member is built + green (57 tests,
  all mocked) but has NEVER run against a real Meta app. Un-park by registering
  the Meta app + confirming the `# verify-live` shapes against real responses.
  Grounded plan: `plans/2026-07-21-threads-live-verify.md`.
- **Unofficial Threads backend — REJECTED (killed, not deferred)** — probed
  2026-07-21: the entire 2023 private-API surface is dead (threads.net →
  threads.com, every lib archived, the page/scrape shape moved). It's now a
  from-scratch reverse-eng project + Med-High ban risk on the user's IG account.
  Rationale pinned in `../../threads-cli/CLAUDE.md` → "Unofficial … REJECTED".
  Don't re-litigate.
- **core phase 2+** — extract the NEXT shared primitive into `patchboy-core`
  ONLY when a second genuinely byte-for-byte-identical one appears across ≥2
  members (rule in `../../core/CLAUDE.md`). Auth flows, render, and `load()`
  stay per-member on purpose.

## ✅ Shipped

- **patchboy workspace (2026-07-21)** — four standalone CLI repos consolidated
  into one uv workspace; original per-commit history preserved via `git
  subtree`; `apple-music-cli` → `applemusic-cli` for family consistency.
  (ADR `../adr/0001-patchboy-workspace.md`)
- **patchboy-core (2026-07-21)** — the atomic-0600 secret-file write (duplicated
  in 5 places) extracted to one shared workspace member; all 5 call sites
  migrated. (`../../core/CLAUDE.md`)
- **Members, all green + independently installable** — `spotify` (OAuth PKCE),
  `applemusic` (borrowed dev token + media-user-token cookie), `splice`
  (cookie-lift + official MCP OAuth + `--paste`/`--no-server`), `youtube` (thin
  shell over youtube-toolkit + Google OAuth for own-account reads), `threads`
  (official-OAuth scaffold — ⏸ parked, see Future). Details in git log + each
  member's README/CLAUDE.
