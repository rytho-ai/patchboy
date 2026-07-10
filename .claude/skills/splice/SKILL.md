---
name: splice
description: Search, preview, and manage samples from the user's own Splice account via the unofficial splice-cli (no official API). Use when the user asks to find Splice samples, search Splice by genre/BPM/key, grab a sample preview, or check their Splice session. Handles the human-in-the-loop login that Splice's Cloudflare Turnstile forces.
---

# Splice sample toolkit — operating manual

`packages/splice-cli` is the building-block CLI (session lifting, budget
ledger, GraphQL client) — framework-agnostic, works standalone. This file is
the decision manual for using it autonomously. Full command reference,
architecture, and setup: `packages/splice-cli/README.md`. API reverse-
engineering notes: `packages/splice-cli/docs/reconnaissance.md`.

## Command authority — the one thing to internalize

Every command falls into exactly one tier. Get this wrong and you either
overstep the user's trust or block on something you didn't need to.

| Tier | Authorization needed | Commands |
|---|---|---|
| **Fresh, explicit, per-call approval — never pre-authorized** | A live human sign-off for *this exact invocation and amount*, every time | `splice budget set` |
| **Bridge — run after a prior human action** | The human already passed Turnstile (GraphQL) or completed OAuth (MCP) in a real browser; you complete the rest | `splice login`, `splice login --mcp` |
| **Standing-authorized — run autonomously** | Covered by budget/session already granted; no new approval needed per call | `status`, `search`, `audition`, `preview`, `download`, `buy`, `budget status`, `budget reconcile`, `stack from/prompt/update/share` |

`budget set` is the boundary between "bounded, human-granted allowance" and
an open checkbook. What matters is *never treat it as routine* — never batch
it, never infer the number yourself, never run it without the user having
just told you the exact amount in this conversation. Whether you type it
directly (its own tool-call approval is the sign-off) or the user runs it
themselves is a UI detail, not the security boundary — the boundary is that
it's never pre-approved or inferred, only ever a fresh, explicit "yes, set it
to N" for that specific call. `login`/`login --mcp` are different: each needs
a human to pass a gate in a real browser first (Turnstile for GraphQL, OAuth
consent for MCP) — you only complete the bridge afterward, never the browser
step itself.

## Two backends — MCP is the default, GraphQL is minimal-and-caged

As of the 2026-07-10 MCP integration, `search`/`download`/`buy`/`stack` talk
to Splice's **official MCP server** (`https://mcp.splice.com/mcp`, OAuth-
authorized via `splice login --mcp`) by default. This is deliberate and not
just "whichever backend is convenient":

- **GraphQL (`--keyword`, `--backend graphql`) is Splice's private/undocumented
  API — scraping and reverse-engineering are both named-and-forbidden in
  Splice's Terms of Use** (§II.7.j scraping, §II.7.a.ii reverse-engineering;
  remedy = account termination). It exists in this toolkit only for
  capabilities MCP genuinely lacks (typed search — preset/midi/pack/all,
  `--licensed` "my library" filter, free preview-mp3 fetch for `audition`) and
  as a warned break-glass if the Beta MCP is down. Never reach for it just
  because it's already wired or marginally faster.
- **MCP is Beta** — Anthropic/Splice's schema can drift without notice. If an
  MCP call starts failing in a new way (not the documented rate-limit
  message), that's a signal to check `docs/mcp-implementation-plan.md`/
  `reconnaissance.md` for what changed, not to silently reroute to GraphQL.
- **Never treat MCP's 100-downloads/rolling-24h cap as something to route
  around.** `download`/`buy` raise `McpRateLimitError` and STOP (exit 2) —
  they do NOT auto-fall-back to `--backend graphql` when this happens. Doing
  so would be exactly the throttle evasion Splice's anti-automation clause
  targets. If the user wants to push through anyway, that's their call to
  make explicitly (`--backend graphql`), not yours to make silently.
- **`search`'s two modes are not result-equivalent.** Default (no flag) is
  MCP's semantic search (`describe_a_sound` — embedding-based). `--keyword`
  is GraphQL's keyword/tag search. Same query, different retrieval tech —
  don't expect the same top hit from both, and don't compare their result
  counts as if they're measuring the same thing.
- **`search` filters split by mode.** `--bpm-min`/`--bpm-max` work on BOTH
  backends. `--type`, `--licensed`, `--key`, `--sort`, `--order` are
  `--keyword` (GraphQL)-only — passing any of them without `--keyword` is a
  hard error (exit 1), not a silent no-op, so you don't end up believing you
  filtered results that were never filtered. `--sample-type` is the mirror
  case: MCP-default-only, errors if combined with `--keyword`. `--limit`/`-n`
  is a no-op on the default MCP path (`describe_a_sound` has no limit
  param) — the command warns rather than pretending it narrowed the count.
- **`stack` (from/prompt/update/share) is MCP-only** — there's no GraphQL
  equivalent, so it always uses the MCP session regardless of any backend
  flag. Building/updating a stack is free; only downloading a stack's layers
  (via `download`/`buy` on the printed layer catalog_uuids) can spend a
  credit. `stack update` can also `--swap <layer_uuid>:<asset_uuid>` (exact
  1:1 replace of one layer — you chose the sample), `--add-compatible`
  (MCP picks and adds a fitting layer), or `--swap-compatible` (MCP picks a
  fitting replacement) — on top of the existing `--name`/`--bpm`/`--add`/
  `--remove`.
- **`download`/`buy`'s licensed-check is always GraphQL** (MCP has no
  ownership-check field that doesn't risk a transfer) — only the actual file
  transfer defaults to MCP. This is invisible to you in practice; just know
  both backends are touched per call unless `--backend graphql` is passed.

## Decision rules by exit code

Run `splice status` before any session-dependent work. Its exit code (and
`search`/`buy`'s, which follow the same convention) tells you what to do:

- **0** — proceed normally.
- **2** — a human step is required. The command prints a panel with exact
  instructions (login, or a budget top-up). Relay that panel to the user
  verbatim and STOP. Do not retry, do not try to route around it. Resume only
  after the user confirms they've acted.
- **1** — an ordinary failure (no results, not licensed, etc.) — handle it
  like any other command error, no human handoff needed.

Two situations produce exit 2:

1. **No/expired session.** Tell the user: "Log into splice.com in your
   browser (pass the human-check by hand), then I'll run `splice login`."
   If `--browser auto` didn't find one, ask which browser they used.
2. **Budget stop** (from `splice buy`). This means "top up `splice budget
   set` or raise `--max-item`" — NOT "ask permission for this specific
   item." Relay it the same way.

`splice status` also warns when the Cloudflare clearance cookie is close to
expiring — mention it proactively so a longer task doesn't die partway
through. It now also reports the MCP session's health (a local token-expiry
check only — no network call, so this can never spend a credit or trip the
rate limit just to answer "is this set up"); a missing/stale MCP session
alone never fails the command — GraphQL-only setups are valid — only a
GraphQL problem needing a human step drives the exit code.

## Two kinds of identifier — do not mix them up

`search`/`audition`/`preview` take a free-text **query** (fuzzy, "top hit"
semantics — fine, since nothing is committed yet). `download`/`buy` take a
**catalog_uuid** (exact — from a prior `search`/`audition`'s output), never a
query. This is deliberate, not an inconsistency: a query's "top hit" can shift
between calls (ranking changes, ambiguous terms), so anything that spends
money or writes a file must pin the *exact* asset by uuid, not re-search and
hope. Never call `download`/`buy` with a description instead of a uuid you
actually have in hand from a prior search/audition.

This holds across backends: MCP's `search` results print the same
`catalog_uuid` field as GraphQL's (verified interoperable — MCP and GraphQL
share the same catalog_uuid space), so a uuid from an MCP search works
directly with `download`/`buy` regardless of which backend actually
transfers the file. `stack` layers add a third id, **layer_uuid** — that one
identifies a layer *within a stack* (for `stack update --remove`), and is
distinct from the layer's own sample catalog_uuid (which is what you'd pass
to `download`/`buy` to grab that layer's actual audio file).

## Workflow: helping the user pick a sample

Choosing a sample is inherently "audition several, compare, pick one" — a
single preview is weak for that. So when the user wants you to *find* a
sample (not act on one specific known uuid), default to:

1. `splice audition "<query>" -n 5` — fetches the top N free previews and
   writes one self-contained HTML board (players side by side, with loop
   toggles, BPM/key/pack, and a copy-uuid button per clip — that button
   copies the exact catalog_uuid `download`/`buy` need).
2. Render that HTML as an artifact so the user can listen and compare inline.
3. Let the user pick ("buy #3" / a pasted uuid) — resolve "#3" to that row's
   uuid from the board's own output, then `splice buy <uuid>`.

Prefer `audition` over one-off `preview` whenever the user is deciding among
options. Auditioning is free (previews cost no credits); only the final
`buy` spends — and only once you're calling it with a concrete uuid.
`audition`/`preview` always use GraphQL — MCP has no preview-audio-fetch
tool, so this is one of the genuine capability gaps GraphQL exists for, not
an inconsistency to "fix." Both take `--type/-t` (default `sample`) if the
user is after a preset/midi/pack preview instead of a sample.

## Spending money: the budget system

`splice download` is always free (only re-downloads owned samples).
`splice buy` can spend real credits to license something new — this is the
one place real money moves, so read this section carefully.

**The budget IS the standing consent — do not ask before each purchase.**
The user authorizes a pool once via `splice budget set`; within it, `splice
buy` just works, no "are you sure?" per item. Asking every time would defeat
the entire point of granting a budget — don't add a confirmation step the
design deliberately removed.

- Just run `splice buy` when the user wants something new. It self-enforces
  the remaining pool and a per-item price cap, refusing cleanly (exit 2) if
  either is exceeded — handle that like any other human-step handoff.
- If the user asks you to raise the budget or the per-item cap, that's fine
  to run — but only as a direct, immediate response to them stating the
  exact number in that message. Never infer a number, never bump it
  preemptively "just in case," never fold it into a batch of other actions.
  Each `budget set` call stands alone as its own explicit ask.
- Already-owned samples never touch the budget — `buy` on something already
  licensed silently takes the free-download path instead.
- After a purchase, mention what was bought and the remaining balance (the
  command's own output already has both) — that's the audit trail; no need
  to interrupt the user further.
- If you (or the user) ever suspect the local ledger might be off — a
  purchase made directly in the Splice UI outside this tool, or just
  periodically for peace of mind — `splice budget reconcile` compares it
  against Splice's live balance and reports any drift. Free to run any time.

## Notes

- Chrome/Chromium/Brave/Edge cookies are encrypted; extraction needs the
  user's login keyring unlocked and may prompt. Firefox needs nothing.
- The GraphQL access token backing user-scoped calls (search filters, buy,
  download) expires in ~1hr and only refreshes while the browser tab is
  active. Every command auto-refreshes a stale cached token before calling
  the API, so this is mostly invisible now — but if the *browser's own*
  session is also stale (tab idle a long time), you'll still land on the
  normal exit-2 handoff asking the user to interact with the tab, then
  re-run `login`.
- The MCP session (`splice login --mcp`) is a separate, independent OAuth
  grant — its access token also auto-refreshes (headlessly, via a refresh
  token scoped to this client only), and refreshing it never risks
  disrupting the user's actual splice.com browser login the way the GraphQL
  path's token can. If `_mcp_needs_human` fires, the fix is always
  `splice login --mcp` (a new OAuth consent in the browser), never the
  cookie-lifting `splice login`.
- MCP's `download_asset` response includes a `Credit Spent: yes/no` field —
  **don't trust it as evidence of a fresh charge.** It was observed reporting
  "yes" on a third repeat call to an already-owned asset, with the live
  GraphQL credit balance unchanged across all three calls. If you need to
  confirm whether a purchase actually happened, use `splice budget
  reconcile` against the live balance, not this field.
- Some stacks created while testing/exploring will remain in the user's real
  account permanently — none of the 6 MCP tools include a `delete_stack`.
  Mention this if you create throwaway/test stacks; there's no cleanup path.
