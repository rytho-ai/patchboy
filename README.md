# splice-cli

A Splice CLI **for coding agents** — search, download, buy, and build Stacks
from the user's own Splice account. Two backends: Splice's **official MCP
server** (`https://mcp.splice.com/mcp`, OAuth-authorized) is the default for
search/download/buy/stack; Splice's private GraphQL API is kept minimal,
reserved for capability gaps MCP doesn't cover (typed search, "my library"
filter, free preview-audio fetch) and as a break-glass fallback. Library-
first: a clean Python core (`SpliceClient` for GraphQL, `McpClient` for MCP)
with a thin `splice` CLI and a Claude Code skill on top, so an agent
(Digger/TrackMate) can import the same core in-process.

Design commitments:

- **MCP is the sanctioned path, GraphQL is minimal-and-caged.** Splice's
  Terms of Use explicitly forbid scraping (§II.7.j) and reverse-engineering
  (§II.7.a.ii) — remedy is account termination. The GraphQL client exists
  only where MCP genuinely can't (see Usage below), never as a default or a
  way around MCP's rate limit.
- **GraphQL login relies on a human.** Splice's web login is
  Cloudflare-Turnstile-gated; no automation passes it. A person logs into
  splice.com in their browser once; `splice login` lifts that session.
- **MCP login is a standard OAuth flow.** `splice login --mcp` runs Dynamic
  Client Registration + PKCE, opens a browser URL for the user to authorize,
  and catches the redirect on a local loopback port — independent of the
  GraphQL session, and its refresh doesn't touch the browser's own login.
- **No credentials, ever.** The toolkit never sees an account password —
  GraphQL access comes from lifting an already-logged-in browser session;
  MCP access comes from an OAuth token the user explicitly grants.

> **Heads up.** This automates *your own* paid Splice account (licensed
> downloads/purchases spend *your* credits). The MCP path is official and
> sanctioned; the GraphQL path is unofficial and likely violates Splice's ToS
> — kept deliberately minimal for that reason. Keep request rates human;
> don't batch-scrape the library; never route around MCP's 100-download/
> rolling-24h cap by falling back to GraphQL.

## How auth works

**MCP (default backend):**
1. `splice login --mcp` opens a browser URL; the user authorizes once.
2. Access token (~1hr) auto-refreshes headlessly afterward via a refresh
   token scoped to this client — no browser dependency after the first grant.

**GraphQL (capability-gap / break-glass backend):**
1. Log into **splice.com** in your browser, by hand, once.
2. `splice login` lifts that session (cookies + matching User-Agent) from the
   browser's cookie store.
3. Everything after is pure HTTP with the lifted session — **no browser runs**.

**Browsers (GraphQL only):** Firefox is the easy case (unencrypted cookies on
Linux). Chrome, Chromium, Brave, and Edge encrypt cookies via the OS keyring —
supported through `browser_cookie3`, but extraction needs your login keyring
unlocked and may prompt. `splice login` auto-detects; `--browser chrome`
targets one.

## Human-in-the-loop (for the agent)

Moments that need a human; the CLI makes each explicit — it exits **code 2**
with a panel of instructions, which an agent keys off to hand back to the user:

- **First run / no MCP session** — `splice login --mcp`, authorize in the
  browser that opens.
- **First run / no GraphQL session** — log into splice.com, then
  `splice login`.
- **GraphQL session expired** — cf_clearance lasts ~1 year but Splice can
  clear it sooner. Searches then fail with a clear handoff. `splice status`
  also warns when expiry is <2 days away.
- **MCP rate limit hit** (100 downloads/rolling-24h) — `download`/`buy`
  report it and stop; they never silently fall back to GraphQL to route
  around it (that would be throttle evasion). Pass `--backend graphql`
  yourself if you understand that risk.

## Layout

```
src/splice_cli/
├── config.py       # Settings: session paths, download dir (caller-owned)
├── models.py       # Sample / SearchResult / Stack / StackLayer — the stable vocabulary
├── auth.py         # GraphQL Session type, typed errors, browser dispatcher
├── firefox.py      # lift session from Firefox (unencrypted)        ← caged
├── chrome.py       # lift session from Chromium family (encrypted)  ← caged
├── client.py       # Splice private GraphQL API: search/preview/download/buy ← caged
├── session.py      # save/load a lifted GraphQL session
├── mcp_auth.py     # MCP OAuth 2.1 PKCE flow (register/login/refresh)      ← caged
├── mcp_session.py  # save/load an MCP OAuth token set
├── mcp_client.py   # official MCP JSON-RPC client + markdown result parsing ← caged
├── budget.py       # standing spend pool + audit log for purchases (backend-agnostic)
├── render.py       # audition board — self-contained HTML preview player
├── cli.py          # thin `splice` shell
└── *.graphql       # captured queries (samples_search, sample_asset_files,
                    #   asset_by_uuid, purchase_asset, subscription_credits) ← caged
```

Only the caged modules know what Splice/browsers/the MCP protocol look like.
A redesign or schema drift is fixed there; models and CLI don't move. See
`docs/reconnaissance.md`.

The Claude Code skill lives at `.claude/skills/splice/SKILL.md`.

## Setup

```bash
cd packages/splice-cli
uv venv && uv pip install -e .

# MCP (default backend for search/download/buy/stack) — one-time OAuth grant:
splice login --mcp

# GraphQL (capability-gap / break-glass backend) — log into splice.com in
# your browser first, then:
splice login
```

## Testing

```bash
uv pip install -e ".[dev]"
uv run pytest
```

Tests cover the money-adjacent and error-classification logic that doesn't
need a live session: budget boundary checks (`can_afford`), the atomic
save/load round-trip (both GraphQL `session.py` and MCP `mcp_session.py`,
including 0600 permissions), `Sample` field-mapping from raw GraphQL
responses, JWT expiry parsing, the two ways Splice reports an expired GraphQL
session (outer HTTP 401 vs. a 200 whose GraphQL `errors[]` wraps one), PKCE
challenge/verifier correctness for the MCP OAuth flow, and MCP's markdown
result parsing (`_parse_search_markdown`/`_parse_download_markdown`/
`_parse_stack_markdown`) against real captured responses — this last one
matters more than it sounds: 3 real bugs were only caught this way (a
markdown-bold-marker miss in the download filename regex, a header-hack
`\s*` swallowing a newline in stack-layer parsing, and a variable-shadowing
bug where loop-local `bpm`/`key` overwrote the stack-level ones). Anything
needing a real logged-in session or a real spend is validated manually — see
`docs/reconnaissance.md`.

## Usage

Commands split into three authority tiers — this matters if you're driving
`splice-cli` from an agent, not just by hand:

| Tier | Authorization needed | Commands |
|---|---|---|
| **Fresh, per-call approval — never pre-authorized** | You, explicitly, for this exact amount, every time | `budget set` — the only thing that turns real money into agent-accessible balance |
| **Bridge** | A human already passed Turnstile (GraphQL) or completed OAuth consent (MCP) in a real browser | `login`, `login --mcp` — completes what the human started; can't do the browser part itself |
| **Standing-authorized — runs freely** | Covered by an existing session/budget | `status`, `search`, `audition`, `preview`, `download`, `buy`, `budget status`, `budget reconcile`, `stack from/prompt/update/share` |

An agent (or script) *can* invoke `budget set` — the boundary isn't "never
typed by automation," it's "never pre-approved, batched, or inferred." Each
call must be a live, explicit response to you stating the exact number in
that moment — whether you type it yourself or approve the exact tool call,
either is a fresh sign-off. What it must never be is routine.

```bash
# fresh approval each time — never batched or pre-approved
splice budget set 10                   # authorize a standing spend pool

# bridge — one-time grants, independent of each other
splice login --mcp                     # OAuth-authorize the official MCP (default backend)
splice login [--browser chrome]        # lift a GraphQL session (capability-gap backend)

# free to run once a session/budget exist
splice status                          # GraphQL live probe + MCP token status (local check, no spend risk)
splice search "dusty drum break"       # MCP semantic search (default)
splice search "dusty drum break" --bpm-min 80 --bpm-max 100   # BPM range works on BOTH backends
splice search "dusty drum break" --keyword --type all --licensed   # GraphQL: typed + "my library"
splice search "dusty drum break" --keyword --key "c minor" --sort relevance --order ASC
splice audition "soul melody" -n 5 --bpm-min 80 --bpm-max 95   # HTML board of top-N previews, side by side (free, GraphQL)
splice preview "dusty drum break" --type preset   # grab the top hit's preview mp3 (free, GraphQL); also takes --bpm-min/--bpm-max

# download/buy take a catalog_uuid (from search/audition output), not a
# query — pinning the exact asset instead of re-searching and hoping the
# "top hit" is still the same item you meant.
splice download <catalog_uuid>         # full-quality file — only if already licensed
splice budget status [--log]           # see remaining budget / purchase history
splice budget reconcile                # compare our ledger against Splice's live balance
splice buy <catalog_uuid>              # license + download — spends a credit, budget-gated
# both download and buy also write a <filename>.splice.json metadata sidecar
# (BPM/key/pack/tags/etc.) next to the file, automatically, no flag needed
# both take --backend graphql to explicitly break-glass around the MCP default
# (--backend only accepts mcp|graphql — a typo errors clearly, exit 1)

# Stacks — MCP-only, no GraphQL equivalent. Building/updating is free; only
# downloading a printed layer catalog_uuid (via download/buy) can spend.
splice stack from <catalog_uuid> --name "my stack" --bpm 90
splice stack prompt "moody lofi hip hop beat" --bpm-min 80 --bpm-max 95
splice stack update <stack_uuid> --add <catalog_uuid> --remove <layer_uuid>
splice stack update <stack_uuid> --swap <layer_uuid>:<asset_uuid>   # exact 1:1 layer replace
splice stack update <stack_uuid> --add-compatible      # let the MCP pick+add a fitting layer
splice stack update <stack_uuid> --swap-compatible      # let the MCP pick a fitting replacement
splice stack share <stack_uuid>
```

`search`'s flags split by mode, same as `--type`/`--licensed`:
- **Both backends**: `--bpm-min`/`--bpm-max`.
- **`--keyword` (GraphQL) only** — error (exit 1) if passed without `--keyword`: `--type`, `--licensed`, `--key`, `--sort`, `--order`.
- **Default MCP path only**: `--sample-type` (errors if passed with `--keyword`); `--limit`/`-n` is silently accepted by typer but has no effect on the default MCP path (`describe_a_sound` has no limit param) — the command prints a warning rather than pretending it narrowed the result count.

The Claude Code skill (`.claude/skills/splice/SKILL.md`) encodes this same
tiering as operating rules for an agent — this table is the reference it
points back to.

## Spending real money: the budget system

`splice buy` can license a sample the account doesn't own yet — that's a real
purchase. The design, settled deliberately:

- **Standing pool, not per-item confirmation.** `splice budget set <n>`
  authorizes a pool of credits, granted fresh each time you state a number.
  Once set, `splice buy` spends from it autonomously — no confirmation
  prompt per purchase — because the grant itself is the standing consent.
  This is the whole point: it lets an agent actually be useful without you
  babysitting every purchase.
- **The pool never auto-refills.** You top it up manually when it runs dry —
  no monthly-reset surprise that quietly re-opens spending you forgot about.
- **A per-item cap is a separate guard rail** (`--max-item`, default 20
  credits) — stops one unexpectedly expensive asset from draining the whole
  pool in one shot, independent of how much pool remains.
- **Every purchase is logged** (`splice budget status --log`) so you can
  audit what was bought after the fact.
- **`splice budget set` is never pre-authorized or inferred** — the boundary
  between "bounded, human-granted allowance" and "open checkbook" isn't
  about who types the command, it's that every call must be a fresh, explicit
  response to you naming that exact number, never batched or assumed. See
  `.claude/skills/splice/SKILL.md`.
- **Budget gating always uses GraphQL's price** (`asset_prices`) even when
  the actual transfer defaults to MCP — the MCP tools expose no price field
  anywhere, so `buy` checks affordability via GraphQL first, then does the
  purchase+download through MCP's `download_asset` (1 credit is the assumed,
  catalog-standard cost).

## Status

- ✅ **MCP backend** (default) — `login --mcp` (full OAuth 2.1 PKCE flow,
  live-verified end-to-end), `search` (semantic, `describe_a_sound`),
  `download`/`buy` (`download_asset` — live-verified including one real
  1-credit purchase, reconciled against the live credit balance), and all
  4 `stack` commands (`from`/`prompt`/`update`/`share`) — all live-verified
  against `https://mcp.splice.com/mcp` on 2026-07-10. Markdown-result parsing
  is regex-based (MCP returns text only, no `structuredContent`) and covered
  by unit tests pinned against real captured responses.
- ✅ **GraphQL backend** (capability-gap / break-glass) — `login`
  (Firefox + Chrome verified end-to-end, auto-refreshes a stale cached access
  token), `status`, `search --keyword` (all 4 asset types:
  sample/preset/midi/pack, plus `--licensed`), `audition`, `preview` — pure
  HTTP.
- ✅ `download`/`buy` take a **catalog_uuid**, not a query — an exact,
  session-stable pointer resolved via `get_sample()` rather than re-searching
  and hoping the top hit is still the same item. Same convention holds across
  both backends (verified: MCP and GraphQL share the same catalog_uuid
  space).
- ✅ `download` — free re-download of already-owned samples on both backends
  (GraphQL: verified against live credit balance, zero delta; MCP: same rule
  per its own tool description, "Credit Spent" field noted as unreliable for
  already-owned repeat calls — see `docs/reconnaissance.md`). Also writes a
  `<filename>.splice.json` metadata sidecar (BPM/key/pack/tags/catalog_uuid/
  licensed) alongside the downloaded file, unconditionally.
- ✅ `buy` / budget system — purchasing a new license is wired and verified
  on both backends. GraphQL's `PurchaseAsset` mutation was captured from a
  real purchase and confirmed idempotent (re-calling it on an owned asset:
  zero credit delta, no double-charge risk). MCP's `download_asset` purchase
  path was verified with one real 1-credit purchase, budget ledger and
  `budget reconcile` confirmed in agreement afterward. Both gated by the
  same standing-budget system above; `budget.json` writes atomically. Also
  writes the same `<filename>.splice.json` metadata sidecar as `download`,
  including the price paid when a purchase just happened.
- ⚠️ A handful of test stacks created during live verification remain in the
  account permanently — the MCP tool set has no `delete_stack`.
