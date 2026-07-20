# CLAUDE.md — Development principles for splice-cli

Governance for Claude (or any AI assistant) working on this codebase. Same
kind of discipline as sibling `youtube-toolkit`, adapted to what splice-cli
actually is: a self-contained CLI **and** library for a coding agent to
drive, that spends **real money** and handles **live auth material**. Those
two facts set the whole architecture — read the money + auth rules before
touching anything that moves credits or writes a token.

## Architecture principles

### Caged modules — the one boundary that matters

Only a few modules are allowed to know what Splice, the browsers, or the MCP
protocol actually look like. Everything else speaks in our own typed
vocabulary. A Splice redesign, a browser cookie-store change, or an MCP
schema drift must be fixable **inside a caged module**, without the CLI or
the models moving.

```
┌──────────────────────────────────────────────────────────────┐
│  cli.py — the thin shell an agent drives                      │
│    parse args → call a backend → render → exit-code handoff.  │
│    NO Splice/browser/MCP knowledge. NO money policy beyond    │
│    calling budget.py. If a command grows extraction or        │
│    protocol logic, it's in the wrong file.                    │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  models.py — the stable vocabulary (Sample/SearchResult/      │
│    Stack/StackLayer). Hand-authored, NOT a raw mirror of any  │
│    upstream JSON. A field rename upstream is a one-line map    │
│    in a caged module, invisible here.                         │
│  budget.py — the standing spend pool + audit log. Money-      │
│    safety lives here and NOWHERE else; it has no opinion on   │
│    how a purchase is performed, only whether it's allowed and │
│    recording that it happened.                                │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  CAGED — the only modules that know an external surface:      │
│    client.py .......... Splice private GraphQL API            │
│    firefox.py/chrome.py  browser cookie-store lifting         │
│    mcp_client.py ...... official MCP JSON-RPC + markdown parse │
│    mcp_auth.py ........ OAuth 2.1 PKCE flow                    │
│    *.graphql .......... captured query text                   │
└──────────────────────────────────────────────────────────────┘
```

**Rule:** if you're about to import `browser_cookie3`, `httpx`, an OAuth
detail, or a raw GraphQL/MCP shape *outside* a caged module — stop, it
belongs in one. `cli.py` and `models.py` must stay ignorant of the wire.

### Two backends — MCP is the default, GraphQL is caged-and-minimal

`search`/`download`/`buy`/`stack` default to Splice's **official MCP server**
(OAuth-authorized). GraphQL is the reverse-engineered private API — scraping
and reverse-engineering are **named-and-forbidden** in Splice's Terms of Use
(§II.7.j / §II.7.a.ii; remedy = account termination). GraphQL therefore
exists ONLY for:

- capability gaps MCP genuinely lacks (typed search, `--licensed` "my
  library", the free preview-mp3 fetch behind `audition`/`preview`), and
- an explicit, warned break-glass (`--backend graphql`) if the Beta MCP is
  down.

**Never** reach for GraphQL because it's already wired or marginally faster,
and **never** use it to route around MCP's 100/rolling-24h download cap —
that's the throttle evasion the ToS anti-automation clause targets. There is
no code path that switches backend automatically; keep it that way.

## Money safety — the load-bearing invariants

`splice buy` is the only command that spends real credits. These invariants
are non-negotiable; there are regression tests pinning them, and a review
that finds one violated is a blocker.

1. **Record the spend the instant the charge succeeds, before the file
   transfer.** The charge and the byte-transfer are separate steps; a
   transfer failure after a successful charge must still leave the spend in
   the ledger (`bud.record` + atomic `budget_store.save`). Under-recording
   silently overstates remaining budget — the exact failure the ledger
   exists to prevent. (See `buy()` and `_mcp_buy_download`.)
2. **The budget IS the standing consent.** Within a set pool + per-item cap,
   `buy` just works — no per-purchase confirmation. Fail closed: an unknown
   price coerces to `inf` and is rejected by the cap.
3. **`splice budget set` is a fresh, explicit, per-call human approval.**
   Never pre-approve it, never infer the number, never batch it. Whether the
   human types it or approves the exact tool call is a UI detail — the
   boundary is that it is never routine.
4. **No automatic reroute around the MCP rate limit** (see the two-backends
   rule). `McpRateLimitError` reports and stops.

## Human-in-the-loop — the exit-code convention

Every command follows it; agents key off the code:

- **0** — proceed normally.
- **2** — a human step is required (login, or a budget top-up). The command
  prints a panel with exact instructions. Relay it verbatim and STOP.
- **1** — an ordinary failure (no results, not licensed, a tool error). Clean
  message, no human handoff.

`login` needs a human to pass a browser gate first (Turnstile for GraphQL,
OAuth consent for MCP); the CLI only completes the bridge afterward.

## Identifier discipline — two ids, never mixed

`search`/`audition`/`preview` take a free-text **query** (fuzzy "top hit"
semantics, fine since nothing is committed). `download`/`buy` take an exact
**catalog_uuid** from a prior search — never a query, because a query's top
hit can shift between calls and anything that spends money or writes a file
must pin the exact asset. `stack` layers add a third id, **layer_uuid**
(identifies a layer within a stack, distinct from that layer's own sample
catalog_uuid). Keep these straight in code and in help text.

## Adding a capability — which layer

1. **New external call** (a GraphQL op, an MCP tool, a browser detail) → the
   matching **caged module** (`client.py` / `mcp_client.py` / `mcp_auth.py` /
   `firefox.py` / `chrome.py`), mapping the raw shape onto a `models.py`
   type. Add the captured query as a `*.graphql` if GraphQL.
2. **New user vocabulary** → extend `models.py` (hand-authored, minimal).
3. **New command / flag / rendering** → `cli.py` (+ `render.py` for HTML).
   Surface a capability the caged layer already supports; if it doesn't
   support it yet, do step 1 first.
4. **Anything touching money** → gate it through `budget.py`; never inline a
   spend decision in `cli.py`.

## Code organization

```
src/splice_cli/
├── config.py       # Settings: session paths, download dir (caller-owned)
├── models.py       # stable vocabulary (Sample/SearchResult/Stack/StackLayer)
├── budget.py       # standing spend pool + audit log (money safety, backend-agnostic)
├── render.py       # audition board — self-contained HTML preview player
├── cli.py          # thin `splice` shell (parse → backend → render → exit code)
├── auth.py         # GraphQL Session type + typed errors + browser dispatch
├── session.py / mcp_session.py   # atomic 0600 token/cookie persistence
├── client.py       # ← caged: Splice private GraphQL API
├── firefox.py / chrome.py        # ← caged: browser cookie lifting
├── mcp_auth.py     # ← caged: MCP OAuth 2.1 PKCE
├── mcp_client.py   # ← caged: MCP JSON-RPC + markdown result parsing
└── *.graphql       # ← caged: captured query text
```

## Environment & tooling

- **src layout (required)** — package under `src/splice_cli/`, never at repo
  root, so tests run against the installed package (kills "works locally,
  ImportError after install" bugs).
- **`uv` first** — daily use is `uv tool install ...` then bare `splice`;
  local development is `uv run pytest` plus `uv pip install -e ".[dev]"`.
  Reach for bare `pip` only when `uv` genuinely can't express it.
- **Secrets never touch git or stdout.** Real session/token files live in
  `~/.splice-cli/` and are written atomically at 0600. `.gitignore` also
  guards the package dir. Never print a token, cookie, or refresh token —
  only counts and expiry durations.

## Testing

- Unit-test the money-adjacent and parse logic that needs no live session:
  budget boundary checks, atomic save/load + 0600, `Sample` field mapping,
  PKCE correctness, and the MCP markdown parsers against **real captured
  fixtures** (three real bugs were caught only that way). Mock at the client
  boundary; assert the money invariants above (e.g. spend survives a
  post-charge transfer failure; no reroute on rate limit).
- Live-only checks (full login, one real 1-credit buy, a stack round-trip)
  are manual and documented in `docs/reconnaissance.md`, never in CI.
- Keep the suite green (`uv run pytest`) and add to it with every change.

## Communication

Converse with the user in **Traditional Chinese (Taiwanese phrasing)** —
plain, direct, concrete analogies for anything tricky. Keep code,
identifiers, commit messages, and this file in English.
