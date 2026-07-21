"""Typed models — the stable vocabulary callers see.

These shapes are deliberately hand-authored, not a raw mirror of Splice's
JSON. The client maps Splice's response fields onto these, so a field rename
upstream is a one-line change in client.py, invisible here.
"""

from __future__ import annotations

from pydantic import BaseModel


class Sample(BaseModel):
    """One Splice asset — a sample, preset, MIDI file, or pack."""

    uuid: str
    # The real UUID (not `uuid`, which search returns as a content hash) that
    # per-asset queries like SampleAssetFiles need. Required for downloads.
    catalog_uuid: str | None = None
    name: str
    # sample | preset | midi | pack — which of Splice's asset types this is.
    asset_type: str = "sample"
    pack_name: str | None = None
    provider_name: str | None = None
    bpm: int | None = None
    key: str | None = None
    duration_seconds: float | None = None
    tags: list[str] = []
    # Whether the account already owns this sample (free re-download).
    licensed: bool = False
    # Cost to license it if not already owned. None if Splice didn't report
    # a price (observed for some already-licensed items).
    price: float | None = None
    price_currency: str | None = None
    # Streamable preview audio (mp3), distinct from the licensed download.
    preview_url: str | None = None
    # The splice.com page a human can open to listen before deciding whether
    # to download/buy. Only populated for asset_type == "sample" (verified
    # live 2026-07-21; other asset types' URL scheme is unconfirmed) and only
    # when the source data lets us build it correctly — see client.py's
    # `_sample_page_url` for why this needs care (Splice's `uuid` field means
    # a different thing depending on which query populated it).
    url: str | None = None


class SearchResult(BaseModel):
    """A page of samples for a query."""

    query: str
    samples: list[Sample]
    total: int | None = None
    page: int = 1


class StackLayer(BaseModel):
    """One layer of an MCP Stack — a role (e.g. "Strings", "Drums") paired
    with the sample filling it."""

    role: str
    layer_uuid: str
    sample: Sample


class Stack(BaseModel):
    """A multi-layer arrangement built by the MCP's create_stack/
    prompt_to_stack/update_stack tools (MCP-only — no GraphQL equivalent
    built; see docs/mcp-integration-proposal.md)."""

    stack_uuid: str
    name: str | None = None
    bpm: int | None = None
    key: str | None = None
    layers: list[StackLayer] = []
    # Present on create_stack/update_stack results; share_stack instead
    # returns a distinct public_share_url (different field, verified live —
    # the two are not the same link).
    permalink: str | None = None
