"""Markdown parsing for the MCP tools — every fixture below is real text
captured live against https://mcp.splice.com/mcp on 2026-07-10 (not
invented), because two real bugs were found this way during implementation
(a markdown-bold-marker miss in the download filename regex, and a `\\s*`
swallowing a newline in a synthetic-header hack for stack layers) — these
tests pin the fix against the actual data that broke it.
"""

from __future__ import annotations

import httpx

from splice_cli.mcp_auth import McpSession
from splice_cli.mcp_client import (
    McpClient,
    _looks_like_rate_limit,
    _parse_download_markdown,
    _parse_search_markdown,
    _parse_sse,
    _parse_stack_markdown,
    _safe_float,
)


def _mcp_client() -> McpClient:
    return McpClient(
        McpSession(client_id="c", access_token="a", refresh_token="r", access_expiry=9e12)
    )

# Real describe_a_sound output (query="soul melody", type="loop").
SEARCH_MARKDOWN = '''## Search results for: "soul melody"
Showing 10 of 18339 total results

### 1. OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav
BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
**Pack:** Origin Sound
**Tags:** strings, hip hop, soul, melody
**Link:** https://splice.com/sounds/sample/21ee17917248b6a19b95fe04461a9d8976a82fbfa69c057249d448e6de65c77d/os-hs-70-em-soulful-melody-layered-strings-wav
**Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882

### 2. BPM140_Em_Dust_Melody_Counter.wav
BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
**Pack:** Prime Loops
**Tags:** synth, soul, melody, rnb, neo soul
**Link:** https://splice.com/sounds/sample/faaa854b724502369b2c6d6ffe59fff23b1f12200c6946289fefd9a8296dcea5/bpm140-em-dust-melody-counter-wav
**Asset UUID:** a163ed33-181b-4d06-a8b6-c18194776b5f
'''

# Real download_asset output (re-download of an already-owned sample).
DOWNLOAD_MARKDOWN = """## Download Ready

**File Name:** OS_UMS_120_vocal_melody_holding_Cm.wav
**Asset UUID:** de7a0530-6d9d-43fa-af6f-78cd56c11bbf
**Asset Type:** sample
**Credit Spent:** yes
**Remaining Downloads (24h):** 97
**Download URL:** https://spliceproduction.s3.us-west-1.amazonaws.com/audio_samples/07896aa0dc58ebe8996ca69e6d7c02d018e43fc71b912da5aa79d5fdc12b523a?X-Amz-Signature=abc123
"""

# Real create_stack output (2 of the 5 real layers, trimmed for the fixture).
STACK_MARKDOWN = """## Stack: splice-cli-test-stack
**BPM:** 70 | **Key:** e minor

### Layer 1: Strings
**Layer UUID:** 03a18235-b78c-4ed1-b314-a443d67cf8a9
**Sample:** OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav
BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop
**Pack:** Origin Sound
**Tags:** strings, hip hop, soul, melody
**Link:** https://splice.com/sounds/sample/21ee17917248b6a19b95fe04461a9d8976a82fbfa69c057249d448e6de65c77d/os-hs-70-em-soulful-melody-layered-strings-wav
**Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882

### Layer 2: Brass-and-woodwinds
**Layer UUID:** da93bded-889e-448c-a989-304ce7a08e87
**Sample:** OS_OG_74_flute_melody_maps_Em.wav
BPM: 74 | Key: e minor | Duration: 13.0s | Type: loop
**Pack:** Origin Sound
**Tags:** hip hop, brass & woodwinds, flute, melody, trap
**Link:** https://splice.com/sounds/sample/91ef64001e4dc4cc4125714c0deada60a4a4bf6a7f3c9d6fa489f7f4aef0e72e/os-og-74-flute-melody-maps-em-wav
**Asset UUID:** 6b7605d9-b987-4428-b373-355f9a48197c

**Stack UUID:** e41ed722-9e05-4c2d-8ca7-b0719b25ba5f
**Permalink:** https://splice.com/sounds/stacks/e41ed722-9e05-4c2d-8ca7-b0719b25ba5f
"""


class TestParseSse:
    def test_extracts_json_from_data_line(self):
        raw = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        assert _parse_sse(raw) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


class TestParseSearchMarkdown:
    def test_total_count(self):
        _, total = _parse_search_markdown(SEARCH_MARKDOWN)
        assert total == 18339

    def test_singular_total_result_is_parsed(self):
        # FIX 2 regression: _TOTAL_RE required the plural "results", so a
        # single-hit response ("1 total result", no trailing s) silently
        # yielded total=None — which then crashed cli.py's `,`-formatted
        # table title on a plain int/None mismatch.
        text = (
            '## Search results for: "obscure query"\n'
            "Showing 1 of 1 total result\n\n"
            "### 1. Only_Hit.wav\n"
            "BPM: 90 | Key: c minor | Duration: 2.0s | Type: oneshot\n"
        )
        samples, total = _parse_search_markdown(text)
        assert total == 1
        assert len(samples) == 1

    def test_no_total_line_yields_none_without_raising(self):
        # A zero-hit response (or any response missing the "Showing X of Y"
        # line) must degrade to total=None, not raise.
        text = '## Search results for: "nothing matches this"\n\nNo results found.\n'
        samples, total = _parse_search_markdown(text)
        assert total is None
        assert samples == []

    def test_parses_both_items(self):
        samples, _ = _parse_search_markdown(SEARCH_MARKDOWN)
        assert len(samples) == 2

    def test_catalog_uuid_vs_content_hash_uuid_are_distinct(self):
        samples, _ = _parse_search_markdown(SEARCH_MARKDOWN)
        first = samples[0]
        assert first.catalog_uuid == "ce27437e-1e67-436e-a1aa-fbd7058ba882"
        assert first.uuid == "21ee17917248b6a19b95fe04461a9d8976a82fbfa69c057249d448e6de65c77d"
        assert first.catalog_uuid != first.uuid

    def test_fields(self):
        samples, _ = _parse_search_markdown(SEARCH_MARKDOWN)
        first = samples[0]
        assert first.name == "OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav"
        assert first.bpm == 70
        assert first.key == "e minor"
        assert first.duration_seconds == 13.7
        assert first.asset_type == "loop"
        assert first.pack_name == "Origin Sound"
        assert first.tags == ["strings", "hip hop", "soul", "melody"]


class TestParseDownloadMarkdown:
    def test_filename_is_the_real_name_not_the_url_hash(self):
        # Regression test: the first implementation matched a "File Name:"
        # regex that didn't account for markdown's "**File Name:**" bold
        # marker, so it fell back to the S3 object key as the filename.
        _, filename, _ = _parse_download_markdown(DOWNLOAD_MARKDOWN)
        assert filename == "OS_UMS_120_vocal_melody_holding_Cm.wav"

    def test_url_extracted(self):
        url, _, _ = _parse_download_markdown(DOWNLOAD_MARKDOWN)
        assert url.startswith("https://spliceproduction.s3.us-west-1.amazonaws.com/")

    def test_remaining_downloads_count(self):
        _, _, remaining = _parse_download_markdown(DOWNLOAD_MARKDOWN)
        assert remaining == 97


class TestParseStackMarkdown:
    def test_stack_uuid_and_name(self):
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        assert stack.stack_uuid == "e41ed722-9e05-4c2d-8ca7-b0719b25ba5f"
        assert stack.name == "splice-cli-test-stack"

    def test_bpm_and_key(self):
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        assert stack.bpm == 70
        assert stack.key == "e minor"

    def test_permalink(self):
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        assert stack.permalink == "https://splice.com/sounds/stacks/e41ed722-9e05-4c2d-8ca7-b0719b25ba5f"

    def test_layer_count_and_roles(self):
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        assert len(stack.layers) == 2
        assert stack.layers[0].role == "Strings"
        assert stack.layers[1].role == "Brass-and-woodwinds"

    def test_layer_sample_name_is_correct_not_the_layer_uuid_line(self):
        # Regression test: the first implementation reused
        # _parse_search_markdown by prepending a synthetic "### 1. " header
        # to each layer block. Because that synthetic header was followed
        # immediately by a newline, the header regex's `\s*` (which matches
        # newlines) swallowed the linebreak and misparsed the *next* line
        # ("**Layer UUID:** ...") as the sample name. Fixed by parsing layer
        # fields directly instead of faking a header.
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        assert stack.layers[0].sample.name == "OS_HS_70_Em_Soulful_Melody_Layered_Strings.wav"
        assert "Layer UUID" not in stack.layers[0].sample.name

    def test_layer_uuid_distinct_from_asset_catalog_uuid(self):
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        layer = stack.layers[0]
        assert layer.layer_uuid == "03a18235-b78c-4ed1-b314-a443d67cf8a9"
        assert layer.sample.catalog_uuid == "ce27437e-1e67-436e-a1aa-fbd7058ba882"
        assert layer.layer_uuid != layer.sample.catalog_uuid

    def test_layer_sample_fields(self):
        stack = _parse_stack_markdown(STACK_MARKDOWN)
        layer = stack.layers[1]
        assert layer.sample.bpm == 74
        assert layer.sample.key == "e minor"
        assert layer.sample.pack_name == "Origin Sound"
        assert "flute" in layer.sample.tags


class TestLooksLikeRateLimit:
    def test_detects_documented_wording(self):
        assert _looks_like_rate_limit("You have hit your download limit for the rolling 24-hour period")

    def test_unrelated_error_not_flagged(self):
        assert not _looks_like_rate_limit("Asset not found")


class TestDownloadChargeStreamSplit:
    """FIX 1 relies on McpClient.download() being splittable into a charging
    half (download_charge — the tools/call that actually spends the credit)
    and a pure byte-transfer half (stream_to_disk), so cli.py's buy() can
    record the ledger write between them. These pin that split's own
    correctness, independent of cli.py's orchestration (covered separately
    in tests/test_buy_ledger.py).
    """

    def test_download_charge_parses_url_filename_remaining_without_streaming(self, monkeypatch):
        client = _mcp_client()
        calls = []
        monkeypatch.setattr(
            client,
            "_call_tool",
            lambda name, args: calls.append((name, args)) or {
                "content": [{"type": "text", "text": DOWNLOAD_MARKDOWN}]
            },
        )
        # Streaming must never be attempted by download_charge alone.
        monkeypatch.setattr(
            httpx, "stream", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not stream"))
        )

        url, filename, remaining = client.download_charge("some-uuid")

        assert calls == [("download_asset", {"asset_uuid": "some-uuid"})]
        assert filename == "OS_UMS_120_vocal_melody_holding_Cm.wav"
        assert remaining == 97
        assert url.startswith("https://spliceproduction.s3.us-west-1.amazonaws.com/")
        assert client.last_remaining_downloads == 97

    def test_stream_to_disk_writes_bytes_without_charging(self, monkeypatch, tmp_path):
        client = _mcp_client()
        monkeypatch.setattr(
            client,
            "_call_tool",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not call a tool")),
        )

        class _FakeResp:
            def raise_for_status(self):
                pass

            def iter_bytes(self):
                yield b"hello-bytes"

        class _FakeStream:
            def __enter__(self):
                return _FakeResp()

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakeStream())

        target = client.stream_to_disk("https://example/file.wav", "file.wav", tmp_path)

        assert target == tmp_path / "file.wav"
        assert target.read_bytes() == b"hello-bytes"

    def test_download_composes_charge_then_stream(self, monkeypatch, tmp_path):
        """download() itself must still work end-to-end (the `download`
        command's behavior must be unaffected by the split)."""
        client = _mcp_client()
        monkeypatch.setattr(
            client,
            "_call_tool",
            lambda name, args: {"content": [{"type": "text", "text": DOWNLOAD_MARKDOWN}]},
        )

        class _FakeResp:
            def raise_for_status(self):
                pass

            def iter_bytes(self):
                yield b"real-bytes"

        class _FakeStream:
            def __enter__(self):
                return _FakeResp()

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakeStream())

        target = client.download("some-uuid", tmp_path)

        assert target == tmp_path / "OS_UMS_120_vocal_melody_holding_Cm.wav"
        assert target.read_bytes() == b"real-bytes"
        assert client.last_remaining_downloads == 97


class TestFieldIndependentDegradation:
    """HARDENING 6 — BPM/Key/Duration/Type used to be one all-or-nothing
    regex; a missing Duration (e.g. on a preset with no duration field)
    nulled all four fields, contradicting the "field-independent
    degradation" the module's own docstring claims. Each field must now be
    found independently so one missing/malformed sub-field only nulls
    itself.
    """

    def test_missing_duration_still_yields_bpm_key_type(self):
        text = (
            '## Search results for: "x"\n'
            "Showing 1 of 1 total result\n\n"
            "### 1. Some_Preset.nmsv\n"
            "BPM: 120 | Key: c minor | Type: preset\n"
            "**Pack:** Foo Pack\n"
            "**Asset UUID:** abc-123\n"
        )
        samples, _ = _parse_search_markdown(text)
        s = samples[0]
        assert s.bpm == 120
        assert s.key == "c minor"
        assert s.asset_type == "preset"
        assert s.duration_seconds is None

    def test_malformed_duration_yields_none_not_a_crash(self):
        text = (
            "### 1. Weird.wav\n"
            "BPM: 120 | Key: c minor | Duration: N/As | Type: oneshot\n"
            "**Asset UUID:** abc-124\n"
        )
        samples, _ = _parse_search_markdown(text)
        s = samples[0]
        assert s.duration_seconds is None
        # the malformed Duration must not have nulled its neighbors either
        assert s.bpm == 120
        assert s.key == "c minor"
        assert s.asset_type == "oneshot"

    def test_safe_float_guards_bad_input(self):
        assert _safe_float("N/A") is None
        assert _safe_float("13.7") == 13.7
        assert _safe_float(None) is None


class TestParseSseMultipleEvents:
    """HARDENING 6 — a Streamable-HTTP response can carry a non-terminal
    `data:` event (progress/keep-alive) before the real JSON-RPC reply;
    taking the first line blindly would hand `_call_tool` an envelope with
    neither `result` nor `error`.
    """

    def test_skips_leading_keepalive_and_finds_the_real_result(self):
        raw = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n'
            'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        )
        assert _parse_sse(raw) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    def test_skips_non_json_keepalive_line(self):
        raw = "data: \ndata: {\"jsonrpc\":\"2.0\",\"id\":2,\"error\":\"boom\"}\n"
        assert _parse_sse(raw) == {"jsonrpc": "2.0", "id": 2, "error": "boom"}


class TestCallToolMissingResultAndError:
    """An envelope with neither `result` nor `error` (only ever a
    non-terminal event, per the SSE robustness fix above) must raise a
    clear RuntimeError instead of a bare KeyError on data["result"].
    """

    def test_raises_clear_runtime_error_not_keyerror(self, monkeypatch):
        client = _mcp_client()
        monkeypatch.setattr(client, "_ensure_initialized", lambda: None)

        class _FakeResp:
            text = 'data: {"jsonrpc":"2.0","id":1,"method":"notifications/progress"}\n'

            def raise_for_status(self):
                pass

        monkeypatch.setattr(client._http, "post", lambda *a, **kw: _FakeResp())

        try:
            client._call_tool("describe_a_sound", {"query": "x"})
        except RuntimeError as err:
            assert not isinstance(err, KeyError)
            assert "result" in str(err) or "error" in str(err)
        else:
            raise AssertionError("expected a RuntimeError")


class TestContentFirstBlockWithoutText:
    """The happy-path `content[0]["text"]` accesses must be defensive
    (`.get("text", "")`) so a non-text first content block doesn't KeyError
    — mirrors the isError branch, which already does this.
    """

    def test_search_tolerates_non_text_first_content_block(self, monkeypatch):
        client = _mcp_client()
        monkeypatch.setattr(
            client,
            "_call_tool",
            lambda name, args: {"content": [{"type": "image", "data": "..."}]},
        )
        result = client.search("x")
        assert result.samples == []

    def test_download_charge_tolerates_non_text_first_content_block(self, monkeypatch):
        client = _mcp_client()
        monkeypatch.setattr(
            client,
            "_call_tool",
            lambda name, args: {"content": [{"type": "image", "data": "..."}]},
        )
        try:
            client.download_charge("some-uuid")
        except RuntimeError as err:
            # Must fail with the "no Download URL" message, not a KeyError
            # on content[0]["text"].
            assert "Download URL" in str(err)
        else:
            raise AssertionError("expected a RuntimeError")
