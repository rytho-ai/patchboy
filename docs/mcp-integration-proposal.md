# Proposal: official Splice MCP as a first-class backend in splice-cli

Status: proposal (research done, not yet built). 2026-07-10.

## TL;DR

Splice ships an **official MCP server** (`https://mcp.splice.com/mcp`) with
proper OAuth 2.1 (PKCE + dynamic client registration + refresh tokens). It's
the sanctioned way to search/download/build-stacks — but it's **Beta**
(labeled so on Splice's own page), so "official" ≠ "stable/maintained": it's
actually *newer and less battle-tested* than the GraphQL surface splice-cli
already runs in production.

Recommendation (revised after independent research review, 2026-07-10):
make MCP the **default backend** for search/download/stacks, and keep GraphQL
**only for genuine capability gaps** (multi-type search, "my library"
filter, audition-preview fetch) and as a **break-glass fallback if the Beta
MCP breaks** — NOT as an automatic way to route around MCP's 100-downloads/
24h cap. That cap is a deliberately-designed anti-abuse throttle; rerouting
around it via the other channel is textbook throttle-evasion and is exactly
what Splice's ToS anti-automation clause forbids (see Risks). The two paths
interoperate because **both key on the same `catalog_uuid`** (verified) — but
note that interop covers *downloading a known UUID*, not *search-result
parity* (MCP search is almost certainly semantic/embedding-based, our GraphQL
search is keyword — different retrieval systems; see below).

## Research findings (what was actually verified, 2026-07-10)

### The MCP server is real, standard, and self-serve

- Endpoint `https://mcp.splice.com/mcp`; bare request → `401` with
  `WWW-Authenticate: Bearer resource_metadata=.../.well-known/oauth-protected-resource`
  → it's standard **OAuth 2.0 Protected Resource Metadata (RFC 9728)**.
- Authorization server metadata (`/.well-known/oauth-authorization-server`)
  advertises `authorization_code` + `refresh_token` grants, **PKCE (S256)**,
  and a **`registration_endpoint`** → supports **Dynamic Client Registration
  (RFC 7591)**. Verified: POSTing to `/oauth/register` returns `201` with a
  real `client_id` — no manual developer-account application needed.
- Full PKCE loopback flow verified end-to-end: browser consent →
  `authorization_code` → token exchange → `access_token` (1h) +
  **`refresh_token`** (independent of the browser's own web session, so it
  won't rotate the user out of splice.com the way the raw web-JWT refresh
  did — see reconnaissance.md's remote-ops note). This is the same shape as
  `gh auth login` / `gcloud auth login`.
- **Constraint: the OAuth redirect is `http://localhost:8765/callback`** — a
  loopback listener, so the browser consent must happen on the *same machine*
  running the flow. Fine for local use; a remote/headless box needs a
  different redirect strategy (manual code paste, or a hosted redirect).

### The six MCP tools (real schemas, captured via tools/list)

| Tool | What it does | Notes |
|---|---|---|
| `describe_a_sound` | NL search, `type: loop\|oneshot`, `bpm_min/max` | read-only; returns `Asset UUID` = **our `catalog_uuid`** |
| `download_asset` | purchase + return a **presigned URL** (does NOT write the file) | 1 credit first time, re-download free |
| `create_stack` | build a multi-layer stack seeded by one loop UUID | seed must be a loop, not a one-shot |
| `prompt_to_stack` | text prompt → full AI multi-layer arrangement | no UUID input; pure generation |
| `update_stack` | add/remove/swap layers, rename, re-BPM | max 8 layers |
| `share_stack` | stack UUID → public shareable URL | |

### The two paths are interoperable (the load-bearing finding)

MCP's `describe_a_sound` returns the exact **`catalog_uuid`** our GraphQL
`SampleAssetFiles`/`PurchaseAsset` already use, and the content-hash in its
`Link` matches our `uuid` field. So a UUID from either path is usable by the
other. This is what makes a *unified* client possible rather than two
disjoint tools.

## Can every MCP feature be done via GraphQL? (the "unify vs split" question)

| MCP tool | GraphQL equivalent today? | Verdict |
|---|---|---|
| `describe_a_sound` (search) | ✅ `SamplesSearch` (we have it) | **overlap — unify** |
| `download_asset` | ✅ `SampleAssetFiles` + `PurchaseAsset` (we have it) | **overlap — unify** |
| `create_stack` | ⚠️ Stacks ARE a web feature ("Create" is on splice.com, not MCP-only) → a web GraphQL op exists and is *capturable* by the same recon method — but unbuilt, and it's Splice's proprietary AI backend either way | **MCP-only for now; GraphQL possible but real work** |
| `prompt_to_stack` | ⚠️ same as above (AI generation) | **MCP-only for now** |
| `update_stack` / `share_stack` | ⚠️ same (web Stack management) | **MCP-only for now** |

Conclusion: **search + download unify cleanly** (same catalog_uuid, both
sides already built). **Stacks are technically reachable via GraphQL** (they
live in the web app, so there's an op to capture) but it's meaningful
reverse-engineering work for a feature MCP already exposes cleanly with
OAuth — not worth duplicating now. So: unify where it's free, let Stacks be
an MCP-backed capability.

## What can GraphQL do that MCP does NOT? (the reason to keep both)

Verified capabilities our reverse-engineered path has and MCP lacks:

1. **Multi-type search.** MCP `describe_a_sound` only does `loop`/`oneshot`
   samples. Our GraphQL `SamplesSearch` does **preset / MIDI / pack** too
   (Phase where we added `--type all`). MCP has no preset/MIDI/pack search.
2. **"My library" / licensed-only filter.** Our GraphQL `licensed: true`
   lists what the account already owns (free re-download targets). MCP has no
   "list my downloads" tool.
3. **The 100-downloads/24h cap is MCP-specific — confirmed by Splice's own
   docs**, not just inferred. The MCP help-center article states it verbatim
   ("up to 100 samples per rolling 24-hour period"), and it appears only in
   the MCP article, not in general plan/account docs. Ordinary web/app usage
   is gated by *monthly* credit allocation (Sounds+ 100/mo, Creator 200/mo,
   Creator+ 500/mo — which rolls over), with no documented *daily* cap.
   - **Do not conflate** this with a *different* "100/day": the DAW-integration
     (Studio One / Ableton) perk lets Creator+ subscribers license samples
     *without spending credits*, capped at 100/day, scoped "exclusively within
     the DAW." Different mechanism (free-license perk, plan-gated, DAW-only)
     from the MCP cap (credit-spending, all-plans, MCP-channel).
   - **Still UNVERIFIED**: whether GraphQL genuinely has *no* equivalent cap.
     Proving it means downloading >100 in a day = real credits; not tested.
     The MCP-specificity is documented; the GraphQL-bypass is the untested
     inference. **And even if true, using it to bypass the cap is the single
     riskiest behavior in this design — see Risks. Do not build on it.**
4. **audition board + budget ledger + spend caps.** Not API capabilities but
   splice-cli value MCP structurally can't provide — its own docs say "most
   AI tools can't play audio inline; open the link in a browser." Our
   audition HTML board (inline players, loop toggles) is the answer to
   exactly that gap, and the budget/per-item-cap/reconcile system is a
   spend-safety layer MCP doesn't have (MCP just spends against the raw
   credit balance).

## Proposed architecture

A `backend` abstraction on `SpliceClient`, chosen per-operation, not globally:

Route each operation by **capability**, not by a global backend switch, and
never auto-reroute around MCP's throttle:

```
SpliceClient
  ├── search(...)              → MCP describe_a_sound   (semantic; the default find)
  ├── search(type=…|licensed=) → GraphQL SamplesSearch  (capability gap: MCP can't)
  ├── download(uuid)           → MCP download_asset      (default)
  ├── download_owned(uuid)     → GraphQL                 (free re-download of owned)
  ├── audition(...)            → GraphQL preview bytes + render (MCP can't play audio)
  └── stacks.*                 → MCP only (create/prompt/update/share)
```

- **Auth**: `splice login --mcp` runs the PKCE loopback flow, saves the MCP
  token set (access + refresh) to a caller-owned path, mirrors existing
  `splice login`. Access token auto-refreshes via the refresh token (clean,
  independent of the browser session — better than the web-JWT path).
- **No silent rate-limit fallback.** When MCP hits its 100/day cap, `download`
  reports it and stops — it does NOT quietly switch to GraphQL to keep
  spending. GraphQL is reached only for capability-gap ops (multi-type/
  licensed search, audition, owned re-download) or an explicit, warned
  break-glass override if the Beta MCP is down.
- **Search is NOT presented as unified** until a blocking test proves parity
  (it likely won't — semantic vs keyword). Expose MCP semantic search as the
  default `search`, and GraphQL keyword/typed search as a distinct mode.
- **Budget system still wraps `download`** regardless of backend — the
  spend-safety layer is ours and sits above whichever backend performs the buy.
- **Stacks** become a new `splice stack` command group (create/from/update/
  share), MCP-backed.

## Phasing

1. **MCP auth + client** — `splice login --mcp`, an `McpBackend` (PKCE flow,
   token refresh, the JSON-RPC/SSE plumbing verified here). Wire `search` and
   `download` to prefer it.
2. **Backend fallback glue** — `auto` selection + graceful GraphQL fallback
   on MCP rate-limit; keep GraphQL-only ops (multi-type/licensed/audition)
   routed to GraphQL.
3. **Stacks** — `splice stack` command group over the four MCP stack tools.
   Net-new capability splice-cli doesn't have today.

## Open questions / risks

- **⛔ Throttle-evasion is the biggest risk — do not design for it.** Using
  GraphQL to keep downloading after MCP's 100/day cap is hit is textbook
  throttle-evasion: Splice built that cap to throttle exactly this automated
  channel, and routing around it via the other channel with automation is the
  precise behavior the ToS scraping/automated-access clause targets. It's also
  the *only* GraphQL use not justified by a real capability gap. Cut it, or at
  minimum make it an explicit opt-in with a loud warning — never a silent
  `auto` fallback.
- **ToS is explicit, not murky.** Splice ToS forbids automated/scraper access
  (§II.7.j: no "spiders, robots, scrapers, crawlers... to 'scrape' or download
  data") and reverse engineering (§II.7.a.ii), remedy = account termination,
  and banned users may not re-register. This is a bright-line prohibition, not
  a gray area. MCP is sanctioned; the GraphQL path is named-and-forbidden.
  (Also §II.3.1.1.3.h: downloaded Sounds may not train generative-AI models —
  relevant if a future local-embedding-search feature were built on downloads.)
- **MCP is Beta — a real dependency risk, not a footnote.** No SLA, schema/
  params can drift, could change or disappear. Pin behavior against schema
  drift; keeping the GraphQL path healthy as a *break-glass* fallback (if the
  Beta MCP breaks) is a *defensible* reason to keep it — unlike rate-limit
  evasion.
- **Search unification needs a BLOCKING test, not a side-by-side someday.**
  Public docs describe `describe_a_sound` as semantic (audio-embedding, the
  "Similar Sounds"/"Search with Sound" lineage) vs our keyword `SamplesSearch`
  — architecturally different retrieval. So catalog_uuid interop holds for
  *downloading a known UUID*, but "same query → same top hit" search parity is
  near-certain to FAIL. Don't ship a unified `search` that assumes parity;
  confirm empirically first, and likely keep them as *distinct* search modes.
- **Prior art (context, not comfort).** Unofficial Splice clients exist openly
  on GitHub (`splicedd`, `splicerr` + forks, 1–2 yrs, no visible bans) — but
  they hit the *unauthenticated* browse/preview surface only. splice-cli's
  GraphQL path *purchases* (spends credits) and downloads *licensed* files
  using the user's own session — a materially higher-stakes action with no
  found precedent for surviving at scale. "Nobody got banned" covers the
  read-only slice, not ours.
- **Remote/headless** — the loopback redirect needs the browser on the same
  machine. A remote agent needs a manual-code-paste variant (note it in the
  skill, same spirit as the GraphQL remote caveat).
