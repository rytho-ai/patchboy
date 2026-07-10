"""McpClient — talks to Splice's official MCP server (JSON-RPC over
Streamable HTTP). The second caged module for the MCP path (mcp_auth.py
being the first): every endpoint, header, and result shape here was
verified live against https://mcp.splice.com/mcp on 2026-07-10, twice (see
docs/mcp-implementation-plan.md §2-4 for the reconnaissance trail).

Result shape: verified — every tool returns `content: [{type:"text",
text: "<markdown>"}]`, no `structuredContent`, no `outputSchema` declared on
any tool. So this module parses markdown; there is no cleaner JSON path
today (re-check if the Beta server ever adds structuredContent).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from .mcp_auth import McpSession
from .models import Sample, SearchResult, Stack, StackLayer

_MCP_URL = "https://mcp.splice.com/mcp"
_PROTOCOL_VERSION = "2025-06-18"


class McpRateLimitError(RuntimeError):
    """MCP's 100-downloads/rolling-24h cap was hit.

    Per the design (see mcp-integration-proposal.md's research revision):
    this must NEVER trigger an automatic reroute to the GraphQL backend —
    that would be throttle evasion, the exact behavior Splice's ToS
    anti-automation clause targets. Callers report this and stop.
    """


def _parse_sse(text: str) -> dict:
    """Streamable-HTTP responses are SSE-framed even for single replies.

    A stream can carry more than one `data:` event before the terminal
    JSON-RPC reply (progress notifications, keep-alives) — taking the FIRST
    one blindly can hand `_call_tool` a non-terminal envelope with neither
    `result` nor `error`. Scan every `data:` line, skip ones that aren't
    valid JSON or don't look like a JSON-RPC reply (no `result`/`error`
    key), and prefer the first one that does. Falls back to the last
    JSON-parseable line if none carry result/error, so `_call_tool` still
    gets *something* to raise a clear error about instead of this function
    raising first.
    """
    last_parsed: dict | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[len("data:"):].strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue  # keep-alive / non-JSON comment line — skip, don't crash
        if not isinstance(parsed, dict):
            continue
        last_parsed = parsed
        if "result" in parsed or "error" in parsed:
            return parsed
    if last_parsed is not None:
        return last_parsed
    raise RuntimeError(f"No SSE data line found in MCP response: {text[:200]!r}")


# Matches one "### N. name" block from describe_a_sound's markdown output
# (verified format, see mcp-implementation-plan.md §4a):
#   ### 1. OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav
#   BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
#   **Pack:** Origin Sound
#   **Tags:** strings, hip hop, soul, melody
#   **Link:** https://splice.com/sounds/sample/<contenthash>/...
#   **Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882
_TOTAL_RE = re.compile(r"Showing \d+ of ([\d,]+) total results?")
_BLOCK_HEADER_RE = re.compile(r"^###\s*\d+\.\s*(?P<name>.+)$", re.MULTILINE)
# BPM/Key/Duration/Type used to be ONE all-or-nothing regex requiring every
# field present, in order, on one line — so a single missing sub-field (e.g.
# a preset with no Duration) nulled all four, contradicting the
# "field-independent degradation" this module otherwise promises. Split into
# four independent regexes, each found on its own within a block, so a
# missing/malformed field only nulls itself.
_BPM_FIELD_RE = re.compile(r"\bBPM:\s*(?P<bpm>\S+)")
_KEY_FIELD_RE = re.compile(r"\bKey:\s*(?P<key>[^|\n]+?)(?=\s*(?:\||$))", re.MULTILINE)
_DURATION_FIELD_RE = re.compile(r"\bDuration:\s*(?P<duration>[^|\n]+?)(?=\s*(?:\||$))", re.MULTILINE)
_TYPE_FIELD_RE = re.compile(r"\bType:\s*(?P<type>\S+)")
_PACK_RE = re.compile(r"\*\*Pack:\*\*\s*(.+)")
_TAGS_RE = re.compile(r"\*\*Tags:\*\*\s*(.+)")
_LINK_RE = re.compile(r"\*\*Link:\*\*\s*(\S+)")
_ASSET_UUID_RE = re.compile(r"\*\*Asset UUID:\*\*\s*([0-9a-fA-F-]+)")
_LINK_HASH_RE = re.compile(r"/sample/([0-9a-fA-F]+)/")


def _safe_float(raw: str) -> float | None:
    """Best-effort float parse — a malformed numeric field (Beta-server
    drift, an unexpected "N/A", etc.) degrades to None for just that field
    instead of raising and killing the whole block/page parse."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_bpm_key_duration_type(block: str) -> tuple[int | None, str | None, float | None, str | None]:
    """Extract BPM/Key/Duration/Type independently from one markdown block
    (a search-result item or a stack layer) — shared by
    `_parse_search_markdown` and `_parse_stack_markdown`'s layer parsing.
    """
    bpm = key = duration = asset_type = None
    bpm_m = _BPM_FIELD_RE.search(block)
    if bpm_m:
        raw_bpm = bpm_m.group("bpm")
        bpm = int(raw_bpm) if raw_bpm.isdigit() else None
    key_m = _KEY_FIELD_RE.search(block)
    if key_m:
        key_raw = key_m.group("key").strip()
        key = key_raw if key_raw.upper() != "N/A" else None
    duration_m = _DURATION_FIELD_RE.search(block)
    if duration_m:
        duration = _safe_float(duration_m.group("duration").strip().rstrip("s"))
    type_m = _TYPE_FIELD_RE.search(block)
    if type_m:
        asset_type = type_m.group("type")
    return bpm, key, duration, asset_type


def _parse_search_markdown(text: str) -> tuple[list[Sample], int | None]:
    """Parse describe_a_sound's markdown into Samples. Field-independent
    (each field is found by its own regex within a block) rather than one
    rigid multi-line pattern, so a missing/reordered field degrades to None
    instead of dropping the whole item.
    """
    total_m = _TOTAL_RE.search(text)
    total = int(total_m.group(1).replace(",", "")) if total_m else None

    headers = list(_BLOCK_HEADER_RE.finditer(text))
    samples: list[Sample] = []
    for i, header in enumerate(headers):
        start = header.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]

        bpm, key, duration, asset_type = _parse_bpm_key_duration_type(block)

        pack_m = _PACK_RE.search(block)
        tags_m = _TAGS_RE.search(block)
        link_m = _LINK_RE.search(block)
        uuid_m = _ASSET_UUID_RE.search(block)

        tags = [t.strip() for t in tags_m.group(1).split(",")] if tags_m else []
        content_hash = None
        if link_m:
            hash_m = _LINK_HASH_RE.search(link_m.group(1))
            content_hash = hash_m.group(1) if hash_m else None
        catalog_uuid = uuid_m.group(1) if uuid_m else None

        samples.append(
            Sample(
                uuid=content_hash or catalog_uuid or "",
                catalog_uuid=catalog_uuid,
                name=header.group("name").strip(),
                asset_type=asset_type or "sample",
                pack_name=pack_m.group(1).strip() if pack_m else None,
                bpm=bpm,
                key=key,
                duration_seconds=duration,
                tags=tags,
                # MCP's search output doesn't report licensed/price — those
                # are GraphQL-only fields (see mcp-implementation-plan.md
                # §3d/§5d); leave at model defaults (False / None).
            )
        )
    return samples, total


# download_asset's result format (verified live 2026-07-10 against a real
# owned-sample re-download — see mcp-implementation-plan.md):
#   ## Download Ready
#   **File Name:** OS_UMS_120_vocal_melody_holding_Cm.wav
#   **Asset UUID:** de7a0530-6d9d-43fa-af6f-78cd56c11bbf
#   **Asset Type:** sample
#   **Credit Spent:** yes
#   **Remaining Downloads (24h):** 97
#   **Download URL:** https://spliceproduction.s3.us-west-1.amazonaws.com/...
#
# IMPORTANT — "Credit Spent" is NOT reliable per-call evidence of a charge:
# observed "yes" on a THIRD call to the same already-owned asset, with the
# live credit balance verified unchanged (10524 -> 10524) across the calls.
# Don't use this field to detect an unexpected charge; it appears to just
# mean "this asset has been licensed" (historical), not "billed just now".
_FILE_NAME_RE = re.compile(r"\*\*File Name:\*\*\s*(\S+)")
_DOWNLOAD_URL_RE = re.compile(r"\*\*Download URL:\*\*\s*(\S+)")
_REMAINING_DOWNLOADS_RE = re.compile(r"\*\*Remaining Downloads \(24h\):\*\*\s*(\d+)")


def _parse_download_markdown(text: str) -> tuple[str, str, int | None]:
    """Parse download_asset's result into (url, filename, remaining_24h)."""
    url_m = _DOWNLOAD_URL_RE.search(text)
    if not url_m:
        raise RuntimeError(
            f"Could not find a Download URL in the MCP response: {text[:300]!r}"
        )
    url = url_m.group(1)
    name_m = _FILE_NAME_RE.search(text)
    filename = name_m.group(1) if name_m else url.split("/")[-1].split("?")[0]
    remaining_m = _REMAINING_DOWNLOADS_RE.search(text)
    remaining = int(remaining_m.group(1)) if remaining_m else None
    return url, filename, remaining


# create_stack/update_stack result format — verified live 2026-07-10 (built
# a real stack, renamed it via update_stack, both matched):
#   ## Stack: <name>
#   **BPM:** 70 | **Key:** e minor
#
#   ### Layer 1: Strings
#   **Layer UUID:** 03a18235-b78c-4ed1-b314-a443d67cf8a9
#   **Sample:** OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav
#   BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
#   **Pack:** Origin Sound
#   **Tags:** strings, hip hop, soul, melody
#   **Link:** https://splice.com/sounds/sample/<hash>/...
#   **Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882
#
#   ... more ### Layer N blocks ...
#
#   **Stack UUID:** e41ed722-9e05-4c2d-8ca7-b0719b25ba5f
#   **Permalink:** https://splice.com/sounds/stacks/e41ed722-...
#
# share_stack's format is DIFFERENT (verified live, same session) — no
# layers, its own header, and a distinctly-named link:
#   ## Stack Shared
#   **Stack UUID:** e41ed722-9e05-4c2d-8ca7-b0719b25ba5f
#   **Snapshot UUID:** 3edd3f8d-ea87-43ce-bd51-8af71aa60f5e
#   **Public Share URL:** https://splice.com/sounds/create/share/...
_STACK_HEADER_RE = re.compile(r"^##\s*Stack:\s*(?P<name>.+)$", re.MULTILINE)
_STACK_BPM_KEY_RE = re.compile(r"\*\*BPM:\*\*\s*(?P<bpm>\S+)\s*\|\s*\*\*Key:\*\*\s*(?P<key>[^\n]+)")
_STACK_UUID_RE = re.compile(r"\*\*Stack UUID:\*\*\s*(\S+)")
_PERMALINK_RE = re.compile(r"\*\*Permalink:\*\*\s*(\S+)")
_LAYER_HEADER_RE = re.compile(r"^###\s*Layer\s*\d+:\s*(?P<role>.+)$", re.MULTILINE)
_LAYER_UUID_RE = re.compile(r"\*\*Layer UUID:\*\*\s*(\S+)")
_LAYER_SAMPLE_RE = re.compile(r"\*\*Sample:\*\*\s*(.+)")
_PUBLIC_SHARE_URL_RE = re.compile(r"\*\*Public Share URL:\*\*\s*(\S+)")


def _parse_stack_markdown(text: str) -> Stack:
    """Parse create_stack/prompt_to_stack/update_stack's markdown result."""
    header_m = _STACK_HEADER_RE.search(text)
    bpm_key_m = _STACK_BPM_KEY_RE.search(text)
    uuid_m = _STACK_UUID_RE.search(text)
    permalink_m = _PERMALINK_RE.search(text)
    if not uuid_m:
        raise RuntimeError(f"Could not find a Stack UUID in the MCP response: {text[:300]!r}")

    bpm = key = None
    if bpm_key_m:
        bpm_raw = bpm_key_m.group("bpm")
        bpm = int(bpm_raw) if bpm_raw.isdigit() else None
        key = bpm_key_m.group("key").strip() or None

    layer_headers = list(_LAYER_HEADER_RE.finditer(text))
    layers: list[StackLayer] = []
    for i, lh in enumerate(layer_headers):
        start = lh.end()
        end = layer_headers[i + 1].start() if i + 1 < len(layer_headers) else len(text)
        block = text[start:end]
        # A layer's sample fields (BPM/Key/Duration/Type/Pack/Tags/Link/
        # Asset UUID) use the same field regexes as search results, but the
        # name comes from "**Sample:**" (not a "### N. name" header the way
        # search results have) — parse the fields directly rather than
        # faking a header (tried that first; a header immediately followed
        # by a newline let `\s*` in _BLOCK_HEADER_RE swallow the linebreak
        # and misparse the *next* line as the name — a real, now-fixed bug).
        layer_uuid_m = _LAYER_UUID_RE.search(block)
        name_m = _LAYER_SAMPLE_RE.search(block)
        pack_m = _PACK_RE.search(block)
        tags_m = _TAGS_RE.search(block)
        link_m = _LINK_RE.search(block)
        asset_uuid_m = _ASSET_UUID_RE.search(block)
        if not (layer_uuid_m and name_m):
            continue

        # Deliberately named layer_* here — a previous version reused the
        # bare names `bpm`/`key` (shadowing the stack-level bpm/key computed
        # above), silently overwriting them with the *last* layer's values
        # by the time Stack(bpm=bpm, ...) ran below. Caught by
        # test_mcp_client.py::TestParseStackMarkdown::test_bpm_and_key.
        layer_bpm, layer_key, layer_duration, layer_asset_type = _parse_bpm_key_duration_type(block)
        tags = [t.strip() for t in tags_m.group(1).split(",")] if tags_m else []
        content_hash = None
        if link_m:
            hash_m = _LINK_HASH_RE.search(link_m.group(1))
            content_hash = hash_m.group(1) if hash_m else None
        catalog_uuid = asset_uuid_m.group(1) if asset_uuid_m else None

        layers.append(
            StackLayer(
                role=lh.group("role").strip(),
                layer_uuid=layer_uuid_m.group(1),
                sample=Sample(
                    uuid=content_hash or catalog_uuid or "",
                    catalog_uuid=catalog_uuid,
                    name=name_m.group(1).strip(),
                    asset_type=layer_asset_type or "sample",
                    pack_name=pack_m.group(1).strip() if pack_m else None,
                    bpm=layer_bpm,
                    key=layer_key,
                    duration_seconds=layer_duration,
                    tags=tags,
                ),
            )
        )

    return Stack(
        stack_uuid=uuid_m.group(1),
        name=header_m.group("name").strip() if header_m else None,
        bpm=bpm,
        key=key,
        layers=layers,
        permalink=permalink_m.group(1) if permalink_m else None,
    )


class McpClient:
    """Authenticated client for Splice's official MCP server."""

    def __init__(self, session: McpSession, *, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            headers={
                **session.auth_header,
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            timeout=timeout,
        )
        self._initialized = False
        self._next_id = 1
        # Set by download() from the response's own "Remaining Downloads
        # (24h)" field — lets callers proactively warn before the 100/day
        # cap is actually hit, not just react to the eventual hard error.
        self.last_remaining_downloads: int | None = None

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        resp = self._http.post(
            _MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "splice-cli", "version": "0.1"},
                },
            },
        )
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("MCP initialize response had no mcp-session-id header.")
        self._http.headers["mcp-session-id"] = session_id
        self._http.post(_MCP_URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    def _call_tool(self, name: str, arguments: dict) -> dict:
        self._ensure_initialized()
        resp = self._http.post(
            _MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": self._id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        resp.raise_for_status()
        data = _parse_sse(resp.text)
        if "error" in data:
            message = str(data["error"])
            if _looks_like_rate_limit(message):
                raise McpRateLimitError(
                    "MCP's download limit (100 per rolling 24h) appears to be "
                    f"hit: {message}"
                )
            raise RuntimeError(f"MCP error calling {name!r}: {message}")
        if "result" not in data:
            # A non-terminal SSE event (progress/keep-alive) with neither
            # key — _parse_sse couldn't find a terminal one either. Report
            # clearly instead of a bare KeyError on data["result"].
            raise RuntimeError(
                f"MCP response for {name!r} had neither `result` nor `error` "
                f"in its JSON-RPC envelope: {data!r}"
            )
        result = data["result"]
        if result.get("isError"):
            content = result.get("content", [])
            text = content[0].get("text", "") if content else ""
            if _looks_like_rate_limit(text):
                raise McpRateLimitError(f"MCP's download limit appears to be hit: {text}")
            raise RuntimeError(f"MCP tool {name!r} reported an error: {text}")
        return result

    def search(
        self,
        query: str,
        *,
        sample_type: str | None = None,
        bpm_min: int | None = None,
        bpm_max: int | None = None,
    ) -> SearchResult:
        """Semantic search via describe_a_sound. NOT keyword-equivalent to
        GraphQL's SamplesSearch (verified: different retrieval tech) — don't
        assume the same query returns the same top hit across backends.
        """
        args: dict = {"query": query}
        if sample_type is not None:
            args["type"] = sample_type
        if bpm_min is not None:
            args["bpm_min"] = bpm_min
        if bpm_max is not None:
            args["bpm_max"] = bpm_max

        result = self._call_tool("describe_a_sound", args)
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        samples, total = _parse_search_markdown(text)
        return SearchResult(query=query, samples=samples, total=total, page=1)

    def download_charge(self, sample_or_uuid: Sample | str) -> tuple[str, str, int | None]:
        """The credit-charging half of `download()`: calls download_asset and
        returns (presigned url, filename, remaining_24h) WITHOUT streaming
        any bytes. Split out so a caller that must record a spend can do so
        between this (the charge — already happened server-side once this
        returns) and the byte-transfer below, which can fail independently
        (network blip, disk full) without losing the recorded spend. See
        buy() in cli.py, which records the spend between this call and the
        stream_to_disk() call below.
        """
        catalog_uuid = (
            sample_or_uuid.catalog_uuid if isinstance(sample_or_uuid, Sample) else sample_or_uuid
        )
        if not catalog_uuid:
            raise ValueError("No catalog_uuid to download.")

        result = self._call_tool("download_asset", {"asset_uuid": catalog_uuid})
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        url, filename, remaining = _parse_download_markdown(text)
        self.last_remaining_downloads = remaining
        return url, filename, remaining

    def stream_to_disk(self, url: str, filename: str, dest: Path) -> Path:
        """Stream a presigned download URL (from `download_charge`) to disk.
        Pure byte-transfer, no charging — safe to retry/repeat on its own.
        """
        target = dest if dest.suffix else dest / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", url, timeout=60) as r:
            r.raise_for_status()
            with target.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return target

    def download(self, sample_or_uuid: Sample | str, dest: Path) -> Path:
        """Purchase (if not already owned) + download via download_asset.

        Verified live 2026-07-10 against a real owned sample (re-downloaded
        it twice; confirmed via the live GraphQL credit balance that repeat
        calls don't charge — delta was 0 across both). Sets
        `self.last_remaining_downloads` from the response's own counter.

        Convenience composition of `download_charge` + `stream_to_disk` for
        callers (like the plain `download` command) that don't need a ledger
        write between the two steps.
        """
        url, filename, _remaining = self.download_charge(sample_or_uuid)
        return self.stream_to_disk(url, filename, dest)

    def create_stack(
        self,
        asset_uuid: str,
        *,
        name: str | None = None,
        bpm: int | None = None,
        public: bool = False,
    ) -> Stack:
        """Build a multi-layer stack seeded by one loop's catalog_uuid.

        Free (only downloading a stack's layers spends credits) — verified
        live 2026-07-10. Seed must be a loop, not a one-shot — the tool
        itself explains this rather than silently searching for a substitute
        if given a one-shot uuid.
        """
        args: dict = {"asset_uuid": asset_uuid}
        if name is not None:
            args["name"] = name
        if bpm is not None:
            args["bpm"] = bpm
        if public:
            args["generate_public_url"] = True
        return self._stack_result("create_stack", args)

    def prompt_to_stack(
        self,
        prompt: str,
        *,
        bpm_min: int | None = None,
        bpm_max: int | None = None,
        public: bool = False,
    ) -> Stack:
        """Generate a complete multi-track arrangement from a text prompt
        alone (no seed uuid) — free, same as create_stack."""
        args: dict = {"prompt": prompt}
        if bpm_min is not None:
            args["bpm_min"] = bpm_min
        if bpm_max is not None:
            args["bpm_max"] = bpm_max
        if public:
            args["generate_public_url"] = True
        return self._stack_result("prompt_to_stack", args)

    def update_stack(
        self,
        stack_uuid: str,
        *,
        name: str | None = None,
        bpm: int | None = None,
        add_asset_uuids: list[str] | None = None,
        remove_layer_uuids: list[str] | None = None,
        add_compatible_layers: bool | None = None,
        swap_compatible_layers: bool | None = None,
        swap_layers: list[dict] | None = None,
    ) -> Stack:
        """Modify an existing stack (add/remove/swap layers, rename, re-BPM).

        Verified live 2026-07-10 (renamed a real stack). `swap_layers`
        (exact 1:1 replace, [{layer_uuid, asset_uuid}, ...]) is mutually
        exclusive with `swap_compatible_layers` (AI picks a fit) — the
        former is "the user chose this exact sample," the latter is "let
        the AI choose." Max 8 layers; at least one modification required.
        """
        args: dict = {"stack_uuid": stack_uuid}
        if name is not None:
            args["name"] = name
        if bpm is not None:
            args["bpm"] = bpm
        if add_asset_uuids is not None:
            args["add_asset_uuids"] = add_asset_uuids
        if remove_layer_uuids is not None:
            args["remove_layer_uuids"] = remove_layer_uuids
        if add_compatible_layers is not None:
            args["add_compatible_layers"] = add_compatible_layers
        if swap_compatible_layers is not None:
            args["swap_compatible_layers"] = swap_compatible_layers
        if swap_layers is not None:
            args["swap_layers"] = swap_layers
        return self._stack_result("update_stack", args)

    def share_stack(self, stack_uuid: str) -> str:
        """Get a public shareable URL for an existing stack.

        Verified live 2026-07-10 — share_stack's result format is distinct
        from create/update_stack's (its own "## Stack Shared" header, a
        "Public Share URL" field rather than "Permalink" — the two are
        different links, not aliases of each other).
        """
        result = self._call_tool("share_stack", {"stack_uuid": stack_uuid})
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        url_m = _PUBLIC_SHARE_URL_RE.search(text)
        if not url_m:
            raise RuntimeError(f"Could not find a Public Share URL in the MCP response: {text[:300]!r}")
        return url_m.group(1)

    def _stack_result(self, tool: str, args: dict) -> Stack:
        result = self._call_tool(tool, args)
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        return _parse_stack_markdown(text)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _looks_like_rate_limit(text: str) -> bool:
    """Conservative heuristic — we haven't captured a real rate-limit error
    (recon never hit it), so match on the cap's own documented wording
    rather than assuming a specific error code/shape.
    """
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in ("rate limit", "download limit", "24-hour", "24 hour", "rolling", "quota")
    )
