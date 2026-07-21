# Splice private surface — reconnaissance notes

What we learned by logging in and capturing traffic, 2026-07-10. This is the
ground truth `client.py`, `firefox.py`, and `chrome.py` are built on. When
Splice changes and something breaks, re-run this and update here.

This file also folds in the **official MCP server** findings (§"MCP —
official server" below), verified live the same day — that section is now
the single source of truth for MCP auth/protocol/tool-schema details (the
original research doc, `mcp-implementation-plan.md`, stays as historical
planning context but isn't kept in sync going forward).

## Auth model

- Splice's login page is Cloudflare-Turnstile-gated ("Verify you are human").
  Programmatic clicks (ref-click, synthetic mouse) do **not** satisfy it —
  Turnstile does behavioral analysis. Login must be done by a real human once.
- Once logged in, the session lives in cookies on `.splice.com`. The
  load-bearing ones:
  - `_splice_token_prod` — main auth token (~894 chars, JWT-like)
  - `cf_clearance` — Cloudflare clearance from passing Turnstile
  - `__cf_bm` — Cloudflare bot-management (short-lived)
  - `XSRF-TOKEN`, `auth0`, `ajs_user_id` — session/user identity
- **`cf_clearance` is bound to the User-Agent** that solved the challenge.
  So when replaying requests we MUST send the exact same UA as the browser
  that logged in. Mismatch → Cloudflare re-challenges.

### Where the session comes from

The user logs into Splice in their normal browser. We lift the cookies.

**Firefox (easy):**
- Profile: `~/snap/firefox/common/.mozilla/firefox/<profile>.default/`
- Cookies: `cookies.sqlite` — **unencrypted** (plain SQLite, `moz_cookies`).
- Gotchas found: (1) a cookie name appears on several subdomains; some rows
  are stale — pick the **max-expiry** row per name. (2) this snap profile
  stores `expiry` in **milliseconds**, not the documented seconds — normalize
  anything > 1e11 by dividing by 1000.
- UA: from the Firefox version in `/snap/firefox/**/application.ini`, e.g.
  `Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0`.

**Chrome / Chromium / Brave / Edge (harder):**
- Cookies live in `~/.config/<browser>/Default/Cookies` (SQLite) but the value
  column is **AES-encrypted**; the key is in the OS keyring (Secret Service),
  or a hardcoded `"peanuts"` key if no keyring. We delegate decryption to
  `browser_cookie3`. Needs the login keyring unlocked; may prompt.
- `browser_cookie3` cookie expiry is in **seconds** (unlike Firefox's ms).
- UA: from `google-chrome --version` etc.; Chromium freezes minor to `0.0.0`,
  e.g. `...AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36`.
- Verified end-to-end: lifted a real Chrome 150 Splice login, searched OK.

## Search API

- Endpoint: `POST https://surfaces-graphql.splice.com/graphql`
- Standard Apollo Client, **plain JSON** body (not obfuscated — the scrambled
  `text/plain` bodies in captures are `segapi.splice.com` analytics, ignore).
- Operation: `SamplesSearch` (full query saved in
  `src/splice_cli/samples_search.graphql`).
- Headers that matter: `content-type: application/json`,
  `accept: application/graphql-response+json,application/json;q=0.9`.
  Works with cookies alone.
- Key variables: `query`, `filepath` (both set to the search term),
  `limit`, `page`, `sort` (`popularity`), `order` (`DESC`), `tags` (tag IDs),
  `key`, `bpm`/`min_bpm`/`max_bpm`, `asset_category_slug`.
- Response: `data.assetsSearch.items[]`, plus
  `response_metadata.records` (total) and
  `pagination_metadata.{currentPage,totalPages}`.
- Verified: "drums" → 1,225,767 records; items carry `uuid`, `name`,
  `bpm`, `key`, `tags`, `asset_prices` (1 Credit), `licensed`, `files`.

## Preview (free)

- Each item's `files[]` has an entry with
  `asset_file_type_slug: "preview_mp3"` and a **pre-signed S3 URL** (`url`).
- Fetch it directly — no auth, no credit. Verified: 200, `audio/mp3`.

## Download of already-licensed samples (free — solved)

The user captured a real "Download" click from their own logged-in Chrome via
DevTools → Copy as cURL. The operation was **not** a purchase mutation — it's
a plain read query:

- Operation: `SampleAssetFiles(assetUuid: GUID!)` — a `query`, not a
  `mutation`. Query text saved in `src/splice_cli/sample_asset_files.graphql`.
- **Gotcha**: `assetUuid` must be the item's `catalog_uuid` field, not `uuid`.
  `uuid` from `SamplesSearch` is a 64-char content hash; `catalog_uuid` is the
  real UUID (`xxxxxxxx-xxxx-...`) this query needs. Passing the hash gets a
  400 `invalid UUID length: 64`.
- Requires `Authorization: Bearer <_splice_token_prod>` (see below) — cookies
  alone aren't enough for this user-scoped call.
- Response `files[]` differs by ownership:
  - **Licensed**: 3 entries — `preview_mp3`, `waveform`, and **`source`**
    (a signed S3 URL to the real file — verified: 24-bit/48kHz WAV, 3.7MB,
    vs. the ~200KB compressed preview).
  - **Not licensed**: only 2 entries — `preview_mp3` and `waveform`. No
    `source` entry at all.
- **Verified side-effect-free**: checked `subscription.credits` immediately
  before and after calling this on an unlicensed sample — delta was 0. Calling
  it does not silently purchase anything; the presence/absence of `source` is
  purely a read of current ownership.
- Implemented in `client.py::download()`. Raises `NotLicensedError` when
  `source` is absent.

## Resolving a uuid the user already has: `AssetByUuid`

Search results carry two different ids per item — `uuid` (a 64-char content
hash) and `catalog_uuid` (the real UUID `SampleAssetFiles`/`PurchaseAsset`
need). This caused a real bug: `download`/`buy` used to re-run `search(query,
limit=1)` and grab whatever ranked first, which can silently diverge from
the item the user actually meant (confirmed happening once in practice).

Fix: `asset(uuid: $uuid)` (same field `SampleAssetFiles` uses, just without
`includeSource`) returns the *entire* Sample shape — name, licensed,
asset_prices, tags, files, bpm/key/duration, catalog_uuid — from a
catalog_uuid alone. Query saved as `asset_by_uuid.graphql`, wrapped by
`client.py::get_sample()`. `download`/`buy` now take a catalog_uuid instead
of a query, resolving it via `get_sample()` — deterministic, no re-search.

## Live credit balance: `SubscriptionStoreQuery`

Same query used earlier to verify zero-cost operations (`credits` field on
`currentSubscription`), now wrapped as `client.py::get_credits()` and used by
`splice budget reconcile` to cross-check the local spend ledger against
Splice's actual balance. Needs the `Authorization: Bearer` header like any
other user-scoped call — cookies alone return `currentSubscription: null`.

## Purchasing a NEW license (spends a credit) — solved

The user captured a real purchase click (DevTools → Copy as cURL), same
method as the download case:

- Operation: `PurchaseAsset(uuid: GUID!)` — a `mutation`, unlike the read-only
  `SampleAssetFiles`. Query text in `src/splice_cli/purchase_asset.graphql`.
- `uuid` is the item's `catalog_uuid` (same convention as `SampleAssetFiles`).
- Response: `{ uuid, licensed, __typename }` — `licensed` flips to `true` on
  success.
- **Verified idempotent / safe to retry**: re-called the exact same mutation
  against an asset the account already owned (right after the user's real
  purchase). Checked `subscription.credits` before/after — delta was 0, and
  the call succeeded (`licensed: true` returned, no error). Splice's backend
  evidently checks ownership before charging, so a retried/duplicate call
  from an agent can't double-charge.
- Implemented in `client.py::purchase()`. Gated by `budget.py` — the CLI
  (`splice buy`) checks the standing budget and per-item cap *before* calling
  this; `purchase()` itself has no opinion on money, it just performs the
  mutation.

## Observed gotcha: access token can be stale even right after `splice login`

The `_splice_token_prod` JWT (~1hr expiry, see below) only refreshes while
the browser tab is actively used — Auth0's silent-refresh needs the page to
make requests. A browser tab that's been sitting idle (even if "logged in")
can hand back an **already-expired** token the moment you extract it. Hit
this for real: a Firefox session extracted mid-session had a token that had
expired 49 seconds earlier, while the same account's Chrome tab (used
seconds before for a real purchase) was fine. Practical implication: if
`splice status` or a purchase call gets an unexpected `null`/`401` on a
user-scoped call right after `login`, the fix is "click around in that
browser tab for a second, then re-run `splice login`" — not a bug in this
tool.

## Auth token detail: Authorization header, not just cookies

- `_splice_token_prod` is itself an Auth0-issued JWT (RS256), not just an
  opaque session id. Decoding it (no signature verification needed, we're not
  validating — just reading `exp`) shows:
  - `aud`: `["https://splice.com", "https://splice-production.us.auth0.com/userinfo"]`
  - `exp` is only **~1 hour** after `iat` — much shorter-lived than
    `cf_clearance` (~1 year).
- Cookie-only requests work for **anonymous** search, but any user-scoped call
  (licensed-filter search, `SampleAssetFiles`) needs this JWT sent as
  `Authorization: Bearer <token>` too — cookies alone get a clean 200 with an
  internal `401: Unauthorized — user is required` GraphQL error.
- Practical effect: `splice login` re-lifts a fresh Firefox/Chrome cookie jar
  each time it's run, so the 1-hour expiry mostly self-heals as long as the
  browser itself is still logged in (the browser silently refreshes its own
  copy) — but a *cached* session.json can go stale within the hour for
  user-scoped calls even while `cf_clearance` looks fine. `splice status`
  surfaces both expiries separately now.

## MCP — official server (verified live 2026-07-10)

Splice ships an **official** MCP server, a completely separate surface from
everything above — no scraping, no reverse-engineering, sanctioned by
Splice. It's the default backend for `search`/`download`/`buy`/`stack`
(GraphQL above stays minimal, for capability gaps + break-glass only).

### Auth: OAuth 2.1, Dynamic Client Registration + PKCE

Base host: `https://mcp.splice.com`.

**1. Register a client (once; cache `client_id`):**
```
POST https://mcp.splice.com/oauth/register
Content-Type: application/json
{
  "client_name": "splice-cli",
  "redirect_uris": ["http://localhost:8765/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
→ 201 { "client_id": "<uuid>", ... }   # public client, no secret
```

**2. PKCE pair:**
```python
verifier  = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
state     = secrets.token_urlsafe(16)
```

**3. Authorize in the browser**, via a localhost loopback listener:
```
https://mcp.splice.com/oauth/authorize
  ?client_id=<client_id>
  &redirect_uri=http://localhost:8765/callback
  &response_type=code
  &scope=openid profile email offline_access   (url-encoded)
  &code_challenge=<challenge>
  &code_challenge_method=S256
  &state=<state>
```
Capture `?code=` + `?state=` on a one-shot `http.server.HTTPServer`;
**validate `state`**. Use a long timeout (≥1200s) — a 300s first attempt
timed out on a real user in practice.

**4. Token exchange:**
```
POST https://mcp.splice.com/oauth/token
Content-Type: application/x-www-form-urlencoded
grant_type=authorization_code&client_id=<client_id>&code=<code>
&redirect_uri=http://localhost:8765/callback&code_verifier=<verifier>
→ 200 { "access_token": <opaque, ~64 chars>, "token_type": "bearer",
        "expires_in": 3600, "refresh_token": <opaque> }
```
Access token lives **1 hour**. Unlike the GraphQL web-session JWT, this
refresh token is **independent of the browser's splice.com session** —
refreshing it doesn't risk logging the browser out.

**5. Refresh:**
```
POST https://mcp.splice.com/oauth/token
grant_type=refresh_token&client_id=<client_id>&refresh_token=<refresh_token>
→ 200 { access_token, refresh_token(rotated), expires_in, token_type }
```
Persist the (possibly rotated) refresh_token after **every** refresh —
same rotation discipline as the GraphQL headless path below.

**Constraint:** the loopback redirect requires the browser to be on the
**same machine** as the CLI process. Remote/headless would need a
manual-code-paste variant — not built (same "not worth the fragility for a
local setup" call as GraphQL's remote path below).

### Protocol: JSON-RPC 2.0 over Streamable HTTP (SSE-framed)

Endpoint: `POST https://mcp.splice.com/mcp`. Every response — even a single
reply — comes back **SSE-framed** (lines prefixed `data: `), not plain JSON.

**Common headers:**
```
authorization: Bearer <access_token>
content-type: application/json
accept: application/json, text/event-stream
mcp-session-id: <from initialize response>   # required after handshake
```

**Handshake (once per client instance):**
```
1) POST initialize
   {"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2025-06-18","capabilities":{},
              "clientInfo":{"name":"splice-cli","version":"<ver>"}}}
   → 200; the response HEADER `mcp-session-id` is REQUIRED on every
     subsequent request — omitting it fails (verified live).
2) POST notifications/initialized  (no "id" — it's a notification, not a call)
   {"jsonrpc":"2.0","method":"notifications/initialized"}
```

**Calling a tool:**
```
POST {"jsonrpc":"2.0","id":<n>,"method":"tools/call",
      "params":{"name":"<tool>","arguments":{...}}}
```
Parse the SSE body: the line starting with `data:`, `json.loads` the
remainder → JSON-RPC envelope → `result`.

**Result shape — text/markdown only, no structuredContent.** Tool results
expose `result.content: [{type:"text", text: "<markdown>"}]`. No tool
declares an `outputSchema`, and none return `structuredContent` — verified
across all 6 tools live. There is no cleaner JSON path today; the client
must regex-parse the markdown (see `mcp_client.py`'s `_parse_*_markdown`
functions and the real captured fixtures in `tests/test_mcp_client.py`).
Re-check if the Beta server later adds structured output.

### The six tools

- **`describe_a_sound`** (→ `search`, semantic/embedding-based, NOT keyword
  search): `{query: str (req), bpm_min?: int, bpm_max?: int, type?:
  "loop"|"oneshot"}`. Markdown block per result carries Name/BPM/Key/
  Duration/Type/Pack/Tags/Link/**Asset UUID**. The **Asset UUID** is the
  `catalog_uuid`; the 64-char hash embedded in the Link URL is the `uuid`
  (content hash) — same two-id distinction as GraphQL, and the two id
  spaces are interoperable (a catalog_uuid from MCP works fine passed to
  GraphQL's `SampleAssetFiles`/`PurchaseAsset`, and vice versa).
- **`download_asset`** (→ `download`/`buy`): `{asset_uuid: str (req, UUID
  pattern), download_path?: str}`. Returns a **presigned URL** (does not
  write the file itself) + file name — the client must fetch the URL and
  save it, mirroring GraphQL's `download()`. **Spends 1 credit the first
  time; re-downloads of an owned asset are free** (per the tool's own
  description). The URL expires quickly — fetch promptly, don't cache it.
  The response's `**Credit Spent:** yes/no` field is **not reliable evidence
  of a fresh charge** — observed "yes" on a third repeat call to an
  already-owned asset while the live GraphQL credit balance stayed flat
  (10524→10524) across all three calls. Trust `budget reconcile` against the
  live balance, not this field, if you need to confirm a charge happened.
- **`create_stack`**: `{asset_uuid (req, must be a LOOP), name?, bpm?
  (40–300), generate_public_url?}`. Given a one-shot uuid, the tool's own
  rule is to explain that one-shots can't seed stacks and offer to find
  loops instead — it does not silently search on your behalf.
- **`prompt_to_stack`**: `{prompt: str (req), bpm_min?, bpm_max?,
  generate_public_url?: bool}` — pure text → AI arrangement, no seed uuid
  needed. (`generate_public_url` was missing from the first pass of
  reconnaissance and only confirmed via a second live-verification pass.)
- **`update_stack`**: `{stack_uuid (req), name?, bpm? (40–300),
  add_asset_uuids?: [uuid], remove_layer_uuids?: [uuid],
  add_compatible_layers?, swap_compatible_layers?, swap_layers?:
  [{layer_uuid, asset_uuid}]}`. Max 8 layers; at least one modification is
  required per call. `swap_layers` (exact 1:1 replace with a chosen sample)
  and `swap_compatible_layers` (AI picks a compatible replacement) are
  mutually exclusive — the former is "you picked this exact sample," the
  latter is "let the AI choose." (`swap_layers` was also missing from the
  first recon pass, confirmed on the second live-verification pass.)
- **`share_stack`**: `{stack_uuid (req)}` → a public shareable URL.

### Stack markdown shape (for `_parse_stack_markdown`)

```
## Stack: <name>
**BPM:** 70 | **Key:** e minor

### Layer 1: Strings
**Layer UUID:** 03a18235-b78c-4ed1-b314-a443d67cf8a9
**Sample:** OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav
BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
**Pack:** Origin Sound
**Tags:** strings, hip hop, soul, melody
**Link:** https://splice.com/sounds/sample/<hash>/...
**Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882
...
**Stack UUID:** e41ed722-9e05-4c2d-8ca7-b0719b25ba5f
**Permalink:** https://splice.com/sounds/stacks/e41ed722-...
```
A layer's own `**Asset UUID**` (its `catalog_uuid`, for `download`/`buy`) is
distinct from `**Layer UUID**` (identifies the layer *within this stack*,
for `stack update --remove`) — easy to conflate, verified distinct via a
dedicated test (`test_layer_uuid_distinct_from_asset_catalog_uuid`).

### Price: not exposed anywhere in MCP

Verified live: no `outputSchema` on any of the 6 tools, no price field in
`describe_a_sound` results, and the only "credit" mention anywhere is prose
inside `download_asset`'s own tool description. **As shipped, `buy()` doesn't
rely on that gap** — it always resolves the real per-item price via
GraphQL's `get_sample()` first and gates `can_afford()` on that, regardless
of which backend (`mcp` or `graphql`) actually performs the transfer. The
"1 credit" assumption only applies as a last-resort fallback, when
`sample.price` comes back `None` (observed for some already-licensed items)
— see `buy()` in `cli.py`.

### Rate limit: 100 downloads / rolling 24h — never route around it

`download_asset` enforces Splice's standard download cap. The documented
wording (matched by `_looks_like_rate_limit()` in `mcp_client.py`) triggers
`McpRateLimitError`, which the CLI surfaces and **stops on** — it deliberately
never auto-falls-back to the GraphQL backend, since that would be exactly
the throttle evasion Splice's ToS anti-automation clause targets.

## Toolkit implication

Runtime needs **no browser** — search and preview are pure HTTP with the
lifted cookies + matching UA (GraphQL) or a bearer token (MCP). The browser
is only ever touched for one-time human gates: Turnstile (GraphQL login) and
OAuth consent (MCP login) — both forced by Splice, not by this toolkit.

## Remote / headless operation — VIABLE but not wired (deliberate)

Problem: `firefox.py`/`chrome.py` read the browser's cookie store on the
**local** machine. If Claude Code runs on a different machine than the
browser (cloud sandbox, SSH box, remote agent), `splice login` has nothing to
lift. Everything downstream (`status`/`search`/`preview`/`download`/`buy`) is
pure HTTP and runs fine anywhere — the only gap is getting a valid session
onto the remote box.

The refresh-token path (reverse-engineered 2026-07-10, **works**, not built):

- The Auth0 SPA SDK caches a **refresh token** in the browser's localStorage,
  not in a cookie. On Firefox: `<profile>/storage/default/https+++splice.com/
  ls/data.sqlite`, table `data`, key
  `@@auth0spajs@@::<client_id>::https://splice.com::<scope>`. The JSON value's
  `body` holds `access_token`, `refresh_token`, `client_id`
  (`J5JVCGkJmtoM5eiI8LGkPuma93exQV8H`), `audience` (`https://splice.com`),
  `scope` (`openid profile email offline_access` — the `offline_access` is
  what makes a refresh token exist at all).
- Mint a fresh access token headlessly:
  `POST https://auth.splice.com/oauth/token`, form-encoded
  `grant_type=refresh_token`, `client_id`, `refresh_token`. **Must** send a
  browser UA + `origin: https://splice.com` — the endpoint is Cloudflare-
  fronted and 403s (CF error 1010) a bare request. Verified: returns a new
  ~60-min `access_token` with no browser involved.
- **Catch — refresh-token ROTATION (this is the load-bearing gotcha).** Each
  refresh token is single-use: the response includes a *new* refresh token
  and the one you sent is immediately invalidated (verified: replaying the
  old one → `403 invalid_grant "Unknown or invalid refresh token"`). Two
  consequences for any implementation:
  1. Persist the rotated refresh token after **every** call (into
     session.json), atomically. Miss one write and the lineage is dead —
     back to a browser re-login.
  2. The browser and the toolkit **cannot share one refresh-token lineage.**
     Using the browser's refresh token rotates it out from under the browser;
     its next silent refresh fails, and Auth0 reuse-detection can revoke the
     whole token family (→ browser logged out). A remote deployment must
     *take ownership*: extract the refresh token once, ship it to the remote
     session.json, and let the remote own the lineage from then on. The
     browser becomes a separate session (re-login there if needed).

Design shape if ever built: `splice login --export-refresh` writes a
session.json containing the refresh token; on the remote, the client
auto-refreshes on any expired-token error and re-saves the rotated token.
One-time Turnstile login is still human + local; everything after is headless.

Left unwired on purpose: for local Claude + local browser (the current setup)
this buys nothing and adds rotation fragility + a way to disrupt the browser's
own login. Only worth it for genuinely remote, long-running, unattended runs.
