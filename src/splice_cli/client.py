"""SpliceClient — talks to Splice's private GraphQL API with a lifted session.

The caged module: Splice's real endpoint, query, and response shape live
here (see docs/reconnaissance.md). The public methods (search / preview /
download) are stable; their innards get rewritten when Splice changes.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import httpx

from .auth import Session, SessionExpired
from .models import Sample, SearchResult

_GRAPHQL_URL = "https://surfaces-graphql.splice.com/graphql"
_SEARCH_OP = "SamplesSearch"
_ASSET_FILES_OP = "SampleAssetFiles"
_PURCHASE_OP = "PurchaseAsset"
_ASSET_BY_UUID_OP = "AssetByUuid"
_CREDITS_OP = "SubscriptionStoreQuery"


class SampleNotFoundError(RuntimeError):
    """No asset exists for the given catalog_uuid (typo, or it was removed)."""


class NotLicensedError(RuntimeError):
    """The account doesn't own this sample yet — download() is read-only.

    Use SpliceClient.purchase() (wired via `splice buy`) to actually buy it —
    spends a credit; the caller — budget.py / cli.py — is responsible for
    confirming that's OK (standing budget + per-item cap) before calling.
    """


def _load_query(name: str) -> str:
    return resources.files("splice_cli").joinpath(f"{name}.graphql").read_text()


def _check_response(resp: httpx.Response) -> dict:
    """Parse a GraphQL response, raising SessionExpired for any auth failure.

    Splice's gateway reports an expired/invalid session two different ways:
    an outer HTTP 401/403 (or an HTML Cloudflare challenge page), OR a plain
    200 whose GraphQL `errors[]` wraps an inner backend 401 (extensions.code
    == "UNAUTHENTICATED", seen on user-scoped calls like purchase/licensed
    search). Both mean the same thing to the caller: re-lift the session.
    """
    if resp.status_code in (401, 403) or "text/html" in resp.headers.get(
        "content-type", ""
    ):
        raise SessionExpired(
            f"Splice rejected the session (status {resp.status_code}). "
            "Re-open splice.com in your browser and run `splice login` again."
        )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        for err in body["errors"]:
            ext = err.get("extensions", {})
            if ext.get("code") == "UNAUTHENTICATED" or ext.get("response", {}).get(
                "status"
            ) in (401, 403):
                raise SessionExpired(
                    "Splice rejected the session (expired access token). "
                    "Re-run `splice login` — if the browser tab has been idle, "
                    "interact with splice.com there first so it refreshes."
                )
        raise RuntimeError(f"Splice GraphQL error: {body['errors']}")
    return body


# Asset types the "all" fan-out covers. `generated_sample` is excluded — it
# 500s with this filter shape, and it's a niche AI-variant type anyway.
ASSET_TYPES = ("sample", "preset", "midi", "pack")


def _to_sample(item: dict) -> Sample:
    """Map one assetsSearch item onto our stable Sample shape."""
    preview = next(
        (
            f["url"]
            for f in item.get("files", [])
            if f.get("asset_file_type_slug") == "preview_mp3"
        ),
        None,
    )
    # name looks like "Pack_Name/path/to/file.wav" — first segment is the pack.
    raw_name = item.get("name") or ""
    pack = raw_name.split("/", 1)[0] if "/" in raw_name else None
    prices = item.get("asset_prices") or []
    price = prices[0] if prices else {}
    # provider{name} only appears on PackAsset items in the query — guard
    # for absence rather than assuming every item type carries one.
    provider = item.get("provider") or {}
    return Sample(
        uuid=item["uuid"],
        catalog_uuid=item.get("catalog_uuid"),
        name=raw_name,
        asset_type=item.get("asset_type_slug") or "sample",
        pack_name=pack,
        provider_name=provider.get("name"),
        bpm=item.get("bpm"),
        key=item.get("key"),
        duration_seconds=item.get("duration"),
        tags=[t["label"] for t in item.get("tags", []) if t.get("label")],
        licensed=bool(item.get("licensed")),
        price=price.get("amount"),
        price_currency=price.get("currency"),
        preview_url=preview,
    )


class SpliceClient:
    """Authenticated Splice client built from a lifted browser session."""

    def __init__(self, session: Session, *, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            cookies=session.cookies,
            timeout=timeout,
            headers={
                # cf_clearance is bound to this UA — must match the browser's.
                "user-agent": session.user_agent,
                "content-type": "application/json",
                "accept": "application/graphql-response+json,application/json;q=0.9",
                "referer": "https://splice.com/",
                # Public search works on cookies alone, but anything user-scoped
                # (licensed-sample checks, download) needs this Bearer token —
                # it's short-lived (~1hr), unlike cf_clearance (~1yr).
                **session.auth_header,
            },
        )
        self._search_query = _load_query("samples_search")
        self._asset_files_query = _load_query("sample_asset_files")
        self._purchase_query = _load_query("purchase_asset")
        self._asset_by_uuid_query = _load_query("asset_by_uuid")
        self._credits_query = _load_query("subscription_credits")

    def get_credits(self) -> float:
        """Live credit balance on the account — for `splice budget reconcile`."""
        resp = self._http.post(
            _GRAPHQL_URL,
            json={"operationName": _CREDITS_OP, "variables": {}, "query": self._credits_query},
        )
        body = _check_response(resp)
        sub = (body.get("data") or {}).get("currentSubscription")
        if not sub:
            raise RuntimeError(
                "No active subscription found — can't read a live credit balance."
            )
        return sub["credits"]

    def get_sample(self, catalog_uuid: str) -> Sample:
        """Look up one asset by its catalog_uuid — the precise counterpart to
        search(). Use this to resolve a uuid the user picked (e.g. from an
        audition board) into a Sample before download()/purchase(), instead
        of re-searching by name and hoping the top hit is the same item.
        """
        payload = {
            "operationName": _ASSET_BY_UUID_OP,
            "variables": {"uuid": catalog_uuid},
            "query": self._asset_by_uuid_query,
        }
        resp = self._http.post(_GRAPHQL_URL, json=payload)
        body = _check_response(resp)
        item = (body.get("data") or {}).get("asset")
        if not item:
            raise SampleNotFoundError(f"No asset found for uuid {catalog_uuid!r}.")
        return _to_sample(item)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        page: int = 1,
        sort: str = "popularity",
        order: str = "DESC",
        asset_type: str = "sample",
        licensed: bool | None = None,
        key: str | None = None,
        bpm: str | None = None,
        min_bpm: int | None = None,
        max_bpm: int | None = None,
        tags: list[str] | None = None,
    ) -> SearchResult:
        """Search the Splice library.

        asset_type: "sample" (default, loops/one-shots), "preset", "midi",
        "pack", or "all" to fan out across every supported type and merge
        results (each item's `asset_type` field tells them apart).
        licensed: True = only samples this account already owns ("my
        library" — a capability the official MCP's search doesn't have,
        verified). None = no filter (default, matches prior behavior).
        key/bpm/min_bpm/max_bpm/tags: GraphQL's own filter variables (see
        samples_search.graphql / docs/reconnaissance.md) — omitted from the
        request entirely when not provided, same as `licensed` above, so a
        default of "no filter" doesn't accidentally become "filter on null".
        """
        if asset_type == "all":
            results = [
                self._search_one(
                    query, limit=limit, page=page, sort=sort, order=order,
                    asset_type=t, licensed=licensed, key=key, bpm=bpm,
                    min_bpm=min_bpm, max_bpm=max_bpm, tags=tags,
                )
                for t in ASSET_TYPES
            ]
            samples = [s for r in results for s in r.samples]
            total = sum(r.total or 0 for r in results)
            return SearchResult(query=query, samples=samples, total=total, page=page)
        return self._search_one(
            query, limit=limit, page=page, sort=sort, order=order,
            asset_type=asset_type, licensed=licensed, key=key, bpm=bpm,
            min_bpm=min_bpm, max_bpm=max_bpm, tags=tags,
        )

    def _search_one(
        self,
        query: str,
        *,
        limit: int,
        page: int,
        sort: str,
        order: str,
        asset_type: str,
        licensed: bool | None = None,
        key: str | None = None,
        bpm: str | None = None,
        min_bpm: int | None = None,
        max_bpm: int | None = None,
        tags: list[str] | None = None,
    ) -> SearchResult:
        payload = {
            "operationName": _SEARCH_OP,
            "variables": {
                "query": query,
                "filepath": query,
                "limit": limit,
                "page": page,
                "sort": sort,
                "order": order,
                "tags": tags if tags is not None else [],
                "asset_type_slug": asset_type,
                **({"licensed": licensed} if licensed is not None else {}),
                **({"key": key} if key is not None else {}),
                **({"bpm": bpm} if bpm is not None else {}),
                **({"min_bpm": min_bpm} if min_bpm is not None else {}),
                **({"max_bpm": max_bpm} if max_bpm is not None else {}),
            },
            "query": self._search_query,
        }
        resp = self._http.post(_GRAPHQL_URL, json=payload)
        body = _check_response(resp)

        search = body["data"]["assetsSearch"]
        total = search.get("response_metadata", {}).get("records")
        samples = [_to_sample(it) for it in search.get("items", [])]
        return SearchResult(query=query, samples=samples, total=total, page=page)

    def download_preview(self, sample: Sample, dest: Path) -> Path:
        """Download the free preview mp3 (no credit spent)."""
        if not sample.preview_url:
            raise ValueError(f"No preview URL for {sample.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.preview_bytes(sample))
        return dest

    def preview_bytes(self, sample: Sample) -> bytes:
        """Fetch the free preview mp3 into memory (no credit, no file)."""
        if not sample.preview_url:
            raise ValueError(f"No preview URL for {sample.name}")
        # Signed S3 URL — plain GET, no cookies needed.
        r = httpx.get(sample.preview_url, timeout=60)
        r.raise_for_status()
        return r.content

    def download(self, sample: Sample, dest: Path) -> Path:
        """Download the full-quality file for a sample the account already owns.

        Free — re-downloading an owned sample doesn't spend a credit (verified:
        checked account credit balance before/after, delta zero). Raises
        NotLicensedError if the account doesn't own it — this method does NOT
        purchase; call `purchase()` (wired via `splice buy`) first if you want
        to license it.
        """
        if not sample.catalog_uuid:
            raise ValueError(f"No catalog_uuid for {sample.name} — can't fetch files.")

        payload = {
            "operationName": _ASSET_FILES_OP,
            "variables": {"assetUuid": sample.catalog_uuid},
            "query": self._asset_files_query,
        }
        resp = self._http.post(_GRAPHQL_URL, json=payload)
        body = _check_response(resp)

        files = (body.get("data") or {}).get("asset", {}).get("files", [])
        source_url = next(
            (f["url"] for f in files if f.get("asset_file_type_slug") == "source"),
            None,
        )
        if not source_url:
            raise NotLicensedError(
                f'"{sample.name}" isn\'t licensed on this account yet. '
                "Use `splice buy` to license it — spends a credit, gated by "
                "your standing budget (see docs/reconnaissance.md)."
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", source_url, timeout=60) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return dest

    def purchase(self, sample: Sample) -> None:
        """License a not-yet-owned sample — SPENDS A CREDIT.

        Caller (budget.py via cli.py) is responsible for checking the spend
        is authorized (within the standing budget and per-item cap) BEFORE
        calling this — this method has no opinion on money, it just performs
        the purchase Splice's own UI performs on a real "Buy"/"License" click.
        Captured from a real purchase click via DevTools → Copy as cURL.
        """
        if not sample.catalog_uuid:
            raise ValueError(f"No catalog_uuid for {sample.name} — can't purchase.")

        payload = {
            "operationName": _PURCHASE_OP,
            "variables": {"uuid": sample.catalog_uuid},
            "query": self._purchase_query,
        }
        resp = self._http.post(_GRAPHQL_URL, json=payload)
        body = _check_response(resp)

        result = (body.get("data") or {}).get("purchaseAsset") or {}
        if not result.get("licensed"):
            raise RuntimeError(
                f'Purchase call for "{sample.name}" returned but licensed=False '
                f"— unexpected response: {result}"
            )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SpliceClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
