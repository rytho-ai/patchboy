# Implementation spec: official MCP backend in splice-cli

Execution-grade companion to `mcp-integration-proposal.md` (the *why* +
research-revised direction). This spec is written so an implementer can build
it without re-doing reconnaissance — every endpoint, request shape, PKCE
detail, MCP protocol step, and tool schema below was **verified first-hand on
2026-07-10**. Grounded against the current module layout (auth · firefox ·
chrome · client · session · config · budget · models · render · cli).

---

## 0. Settled design decisions (do not re-litigate)

1. **MCP default for search / download / stacks. GraphQL only for capability
   gaps** (typed & licensed search, audition-preview fetch, free re-download
   of owned samples) **+ a warned break-glass** if the Beta MCP is down.
   **No silent rate-limit fallback** — MCP 100/day cap → report + stop.
2. **Search = two distinct modes, not unified.** MCP semantic (`search`,
   default) vs GraphQL keyword/typed (`search --keyword [--type …]`). Never
   assume "same query → same top hit" across them.
3. **Reuse existing `Sample` / `SearchResult` models** — MCP results carry the
   same `catalog_uuid`; the uuid-based `download`/`buy` interface works as-is.
4. **Budget layer stays above whichever backend buys.**
5. **Transport = raw httpx** (verified end-to-end in recon; exact shapes
   below). The official `mcp` SDK is an optional later swap, not needed to
   ship — do not block on it.

---

## 1. Dependencies

Add to `pyproject.toml` `dependencies`: nothing new is strictly required
(httpx already present). PKCE uses stdlib `hashlib`/`secrets`/`base64`; the
loopback listener uses stdlib `http.server`. Optional: `mcp>=1.13` only if a
later refactor swaps raw httpx for the SDK.

---

## 2. OAuth 2.1 flow — exact, verified

Base host: `https://mcp.splice.com`. All verified returning the stated codes.

### 2a. Dynamic client registration (once; cache the client_id)

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
Cache `client_id` in the mcp-session file so we register once, not per login.

### 2b. PKCE

```python
verifier  = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
state     = secrets.token_urlsafe(16)
```

### 2c. Authorization (browser) via loopback

Open in the user's browser:
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
Run a localhost:8765 listener (stdlib `http.server`) that captures `?code=` +
`?state=`. **Validate the returned `state` == sent `state`.** Give it a long
timeout (≥1200s) — users are slow; recon's first 300s attempt timed out.

### 2d. Token exchange

```
POST https://mcp.splice.com/oauth/token
Content-Type: application/x-www-form-urlencoded
grant_type=authorization_code
client_id=<client_id>
code=<code>
redirect_uri=http://localhost:8765/callback
code_verifier=<verifier>
→ 200 { "access_token": <opaque, ~64 chars>, "token_type": "bearer",
        "expires_in": 3600, "refresh_token": <opaque> }
```
Access token lives **1 hour**. Refresh token is **independent of the browser
web session** (won't rotate the user out of splice.com — the clean advantage
over the GraphQL web-JWT path).

### 2e. Refresh

```
POST https://mcp.splice.com/oauth/token
grant_type=refresh_token
client_id=<client_id>
refresh_token=<refresh_token>
→ 200 { access_token, refresh_token(rotated), expires_in, token_type }
```
Persist the (possibly rotated) refresh_token after every refresh.

### Constraint to document (SKILL.md + README)
Loopback redirect requires the browser on the **same machine** as the flow.
Remote/headless needs a manual-code-paste variant (out of scope for v1;
note it).

---

## 3. MCP protocol — exact, verified (raw httpx)

Endpoint: `POST https://mcp.splice.com/mcp`. Streamable-HTTP: responses come
back **SSE-framed** (lines prefixed `data: `), even for single replies.

### 3a. Common headers
```
authorization: Bearer <access_token>
content-type: application/json
accept: application/json, text/event-stream
mcp-session-id: <from initialize response>   # after handshake
```

### 3b. Handshake (once per client instance)
```
1) POST initialize
   {"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2025-06-18","capabilities":{},
              "clientInfo":{"name":"splice-cli","version":"<ver>"}}}
   → 200; READ the response header `mcp-session-id` — REQUIRED on all
     subsequent requests (recon: missing it → failures).
2) POST notifications/initialized  (no id — it's a notification)
   {"jsonrpc":"2.0","method":"notifications/initialized"}
```

### 3c. Calling a tool
```
POST {"jsonrpc":"2.0","id":<n>,"method":"tools/call",
      "params":{"name":"<tool>","arguments":{...}}}
```
Parse the SSE body: take the line starting with `data:`, `json.loads` the
remainder → JSON-RPC envelope → `result`.

### 3d. Result shape — RESOLVED (verified live 2026-07-10)
Tool results expose `result.content: [{type:"text", text: "<markdown>"}]`.
**No tool declares an `outputSchema`, and `describe_a_sound` returns NO
`structuredContent`** — it is **text/markdown only**. So the implementer MUST
parse the markdown (regex per §4a); there is no cleaner JSON path today. (Not
"prefer structuredContent" — it isn't sent. Re-check if the Beta server later
adds it.)

---

## 4. The six tools — schemas + captured shapes

### 4a. `describe_a_sound` (→ our `search`, semantic)
Input: `{query: str (req), bpm_min?: int, bpm_max?: int, type?: "loop"|"oneshot"}`.
Captured `content[0].text` markdown (parse target if no structuredContent):
```
## Search results for: "soul melody"
Showing 10 of 18339 total results

### 1. OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav
BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
**Pack:** Origin Sound
**Tags:** strings, hip hop, soul, melody
**Link:** https://splice.com/sounds/sample/<contenthash>/os-hs-70-...
**Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882
```
Map each block → `Sample`: `catalog_uuid` = **Asset UUID**; `uuid` = the
`<contenthash>` in Link; `name`, `bpm`, `key`, `duration_seconds`, `tags`,
`asset_type` from the fields. (Regex per field, or use structuredContent.)

### 4b. `download_asset` (→ our `download`/`buy`)
Input: `{asset_uuid: str (req, UUID pattern), download_path?: str}`.
Returns a **presigned URL** (does NOT write the file) + file name. **Spends 1
credit the first time; re-downloads free.** McpClient must fetch the URL and
save it (mirror GraphQL `download()`). URL "expires quickly" — fetch promptly.
Always surface the file name alongside.

### 4c. `create_stack`
`{asset_uuid (req, must be a LOOP), name?, bpm? (40–300), generate_public_url?}`.
If given a one-shot uuid, the tool's own rule says: explain one-shots can't
seed stacks, offer to find loops — do NOT silently search.

### 4d. `prompt_to_stack`
`{prompt: str (req), bpm_min?, bpm_max?, generate_public_url?: bool}`. Pure
text → AI arrangement, no uuid. (`generate_public_url` confirmed live
2026-07-10 — was missing from the first recon pass.)

### 4e. `update_stack`
`{stack_uuid (req), name?, bpm? (40–300), add_asset_uuids?: [uuid],
  remove_layer_uuids?: [uuid], add_compatible_layers?, swap_compatible_layers?,
  swap_layers?: [{layer_uuid, asset_uuid}]}`.
Max 8 layers; at least one modification required. `swap_layers` (exact 1:1
replace a layer with a chosen sample) is mutually exclusive with
`swap_compatible_layers` (AI picks a compatible one) — expose both: the former
is "user picked this exact sample," the latter is "let the AI choose."
(`swap_layers` confirmed live 2026-07-10 — was missing from the first recon.)

### 4f. `share_stack`
`{stack_uuid (req)}` → public shareable URL.

---

## 5. Module-by-module spec

### 5a. `config.py` — add one field
`Settings.mcp_session_path` (env `SPLICE_MCP_SESSION_PATH`, default
`~/.splice-cli/mcp-session.json`).

### 5b. `mcp_auth.py` (new)
```python
@dataclass(frozen=True)
class McpSession:
    client_id: str
    access_token: str
    refresh_token: str
    access_expiry: float          # unix ts; access_token 1h
    def expired(self) -> bool: ...
    @property
    def auth_header(self) -> dict[str,str]:  # {"authorization": f"Bearer {access_token}"}

class McpAuthError(RuntimeError): ...      # CLI turns into exit-2 human handoff

def register_client() -> str: ...          # POST /oauth/register → client_id
def login(client_id: str | None = None, *, port: int = 8765,
          timeout: int = 1200) -> McpSession: ...   # full PKCE loopback flow
def refresh(session: McpSession) -> McpSession: ...  # grant_type=refresh_token
```

### 5c. `mcp_session.py` (new) — persistence
`save(session, path)` / `load(path)` → JSON, chmod 0600 (mirror
`session.py`). Store client_id + tokens + access_expiry.

### 5d. `mcp_client.py` (new)
```python
class McpRateLimitError(RuntimeError): ...   # the 100/day cap → report+stop
class McpClient:
    def __init__(self, session: McpSession, *, timeout: float = 30.0): ...
    # handshake lazily on first call; stores mcp-session-id
    def search(self, query: str, *, sample_type: str | None = None,
               bpm_min: int | None = None, bpm_max: int | None = None,
               limit: int = 10) -> SearchResult: ...      # describe_a_sound
    def download(self, sample_or_uuid, dest: Path) -> Path: ...  # download_asset + fetch URL
    def create_stack(self, asset_uuid: str, *, name=None, bpm=None,
                     public=False) -> dict: ...
    def prompt_to_stack(self, prompt: str, *, bpm_min=None, bpm_max=None) -> dict: ...
    def update_stack(self, stack_uuid: str, **mods) -> dict: ...
    def share_stack(self, stack_uuid: str) -> str: ...
    def close(self): ...   # + context manager
```
- Map `describe_a_sound` → `_to_sample_from_mcp(...)` → existing `Sample`.
- `download_asset`: read presigned URL from result, `httpx.stream` GET → save.
- Detect the 100/day cap in the tool-call error/result → raise
  `McpRateLimitError` (do NOT fall through to GraphQL).
- Price for budget: **RESOLVED — the MCP exposes no price field anywhere**
  (verified live: no outputSchema on any tool, no price in describe_a_sound
  results; the only "credit" mention is prose in `download_asset`'s
  description). As shipped, `buy()` doesn't lean on that gap: it always
  resolves the REAL per-item price via GraphQL's `get_sample()` first and
  gates `can_afford()` on that, on both backends. The **1 credit** figure is
  only a last-resort fallback when GraphQL reports no price at all
  (`sample.price is None`, observed for some already-licensed items).

### 5e. `client.py` (GraphQL) — unchanged
Stays the GraphQL backend for capability-gap ops. No edits needed beyond
possibly exposing an "owned re-download" helper if not already covered by the
existing `download()` (it is).

### 5f. `cli.py` — routing
- `login`: add `--mcp` flag → `mcp_auth.login` + `mcp_session.save`; print
  access-token expiry (mirror GraphQL login). Auto-refresh via
  `mcp_auth.refresh` when `session.expired()` before any MCP call.
- `search <q>`: **default → MCP** semantic. Flags `--keyword` (+ existing
  `--type`, `--licensed`) route to **GraphQL** `SpliceClient.search`. Render
  both through `render.search_table` (Sample shape is shared).
- `download <uuid>` / `buy <uuid>`: **default → MCP** `download_asset`,
  budget-wrapped exactly like the current GraphQL `buy`. `--backend graphql`
  = explicit, warned break-glass. On `McpRateLimitError` → exit-2 style
  message, **no reroute**.
- `audition <q>`: unchanged (GraphQL preview bytes + render; MCP can't play
  audio).
- New `stack` sub-typer: `from <uuid>`, `prompt "<text>"`, `update <stack_uuid>`,
  `share <stack_uuid>`. Free to build (no budget gate) unless an export
  downloads layers (then budget-wrap).

### 5g. `models.py` — likely no change
`Sample` already has catalog_uuid/name/bpm/key/tags/asset_type/duration. Add a
`stack`-related model only if stack results need typing (a small `Stack`
dataclass: uuid, name, bpm, layers, public_url).

---

## 6. Tests (unit, no live creds)

- `mcp_auth`: PKCE challenge == base64url(sha256(verifier)) sans padding;
  state validation rejects mismatch.
- `mcp_session`: save/load round-trip, 0600, refresh-expiry (`expired()`).
- `mcp_client._to_sample_from_mcp`: parse the captured markdown block (4a) →
  correct catalog_uuid/uuid/bpm/key/tags; and the structuredContent path if
  used.
- routing: `search` default→MCP, `--keyword`→GraphQL; `download` default→MCP,
  `--backend graphql`→GraphQL; `McpRateLimitError` never triggers a GraphQL
  call (assert no fallback).
- SSE parse helper: given a `data: {...}` body → correct JSON-RPC result.

Live-only (manual, documented in reconnaissance.md, not CI): full login,
one real `buy` (1 credit, verify budget ledger + `budget reconcile` agree),
one stack create/update/share.

---

## 7. Docs to update

- `SKILL.md`: MCP-default; the **two search modes**; the `stack` group; hard
  rules — MCP is **Beta** (schema may drift), GraphQL is **ToS-forbidden**
  (scraping §II.7.j / reverse-eng §II.7.a.ii; remedy = termination) and only
  for capability gaps / break-glass, and **NEVER** route around the 100/day
  cap.
- `README.md`: command table (+`--mcp`, `--keyword`, `stack …`), status.
- `reconnaissance.md`: fold in §2–4 of this spec as the single source of truth.

---

## 8. Phasing & stop points

- **Phase 1** — `mcp_auth` + `mcp_session` + `config` field + `login --mcp`.
  Verify: real token set saved; forced-expiry → auto-refresh headless.
- **Phase 2** — `mcp_client` search+download; CLI routing; budget-wrapped buy.
  Verify: MCP search returns catalog_uuids; one real `buy` spends exactly 1
  credit (ledger + reconcile agree); audition still GraphQL.
- **Phase 3** — `stack` command group over the 4 stack tools.
  Verify: build/update/share a stack live.
- **Phase 4** — tests, SKILL/README/reconnaissance docs.
Each phase independently verifiable and stoppable. Phase 1+2 = core value
(official-channel search+buy). Phase 3 = net-new capability. Phase 4 = safety net.

---

## 9. Risks carried from research (honor, don't reopen)

- ToS is bright-line: GraphQL is named-and-forbidden. Keep it minimal; never
  for throttle evasion.
- MCP is Beta: pin against schema drift; GraphQL survival = break-glass
  insurance, not a co-equal backend.
- Purchase-path precedent is thin (surviving unofficial clients are read-only);
  MCP removes that risk for the buy action — the real reason to prefer it.
