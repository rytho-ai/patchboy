"""CAGED — the only module that knows the Threads Graph API's wire shape.

Everything above this speaks models.py's vocabulary. A Threads field rename
or endpoint reshape must be fixable here without cli.py or models.py moving.
Raw httpx (no SDK) — the surface this CLI needs is small and hand-pinned.

Auth travels as an `access_token` query param (the standard Meta Graph API
convention — see `auth.ThreadsSession.params`), not a Bearer header.

Error classification (both `# verify-live` — inferred from the standard Meta
Graph API error envelope `{"error": {"message", "type", "code", ...}}`, not
re-confirmed against a real Threads app response):
  - `type == "OAuthException"` or `code == 190` → the access token is
    invalid/expired → ThreadsAuthError (the CLI turns that into an exit-2
    human handoff).
  - `code` in the classic Graph API rate-limit set (4, 17, 32), or an HTTP
    429 → RateLimited.
  - Anything else 4xx/5xx → ThreadsApiError (ordinary failure, exit 1).

CAVEAT: nothing in this module has been exercised against a live Threads app
yet (none exists — see threads-cli's CLAUDE.md and the top-level task this
was scaffolded from). The JSON→model maps reflect the field names documented
on developers.facebook.com/docs/threads as of 2026-07-21; re-verify each
against real captured responses once a Meta app is registered.
"""

from __future__ import annotations

import httpx

from .auth import ThreadsAuthError, ThreadsSession
from .models import InsightMetric, Insights, Post, Profile, Reply

_BASE = "https://graph.threads.net"

_PROFILE_FIELDS = "id,username,name,threads_profile_picture_url,threads_biography"
_POST_FIELDS = "id,text,media_type,permalink,timestamp"
_REPLY_FIELDS = "id,text,username,permalink,timestamp"

_RATE_LIMIT_CODES = {4, 17, 32}


class RateLimited(RuntimeError):
    """Threads/Graph API signaled a rate limit — retryable, not a login problem."""

    def __init__(self, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Threads rate limit hit — retry after {retry_after}s"
            if retry_after is not None
            else "Threads rate limit hit"
        )


class ThreadsApiError(RuntimeError):
    """An ordinary API failure (not-found, bad request, server error) — exit 1."""


class Client:
    """Thin authed wrapper. Construct with a live (already-refreshed) session."""

    def __init__(self, session: ThreadsSession) -> None:
        self._session = session

    # ---- low-level ----------------------------------------------------------

    def _request(self, method: str, path: str, *, params: dict | None = None, **kw) -> dict:
        merged = {**self._session.params, **(params or {})}
        resp = httpx.request(method, f"{_BASE}{path}", params=merged, timeout=15, **kw)
        return self._handle_response(resp)

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def _get_absolute(self, url: str) -> dict:
        """Follow a `paging.next` URL as-is — Graph API pagination links are
        already-complete URLs (including their own access_token), so no
        `_BASE` prefix or param merge is needed here."""
        resp = httpx.request("GET", url, timeout=15)
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> dict:
        if resp.status_code == 429:
            raise RateLimited(_retry_after(resp))
        if resp.status_code >= 400:
            err = _error_body(resp)
            # Rate-limit codes are unambiguous — check them BEFORE the auth
            # check, since Meta's Graph API confusingly tags rate-limit
            # errors with `type: "OAuthException"` too (the same type used
            # for genuinely expired/invalid tokens); the numeric `code` is
            # the only reliable discriminator between the two.
            if _is_rate_limit(err):
                raise RateLimited(None)
            if _is_auth_failure(resp.status_code, err):
                raise ThreadsAuthError(
                    "Threads rejected the access token (expired or invalid). "
                    "Run `threads login` again."
                )
            raise ThreadsApiError(f"Threads API error (status {resp.status_code}): {resp.text}")
        return resp.json() if resp.content else {}

    # ---- profile --------------------------------------------------------------

    def me(self) -> Profile:
        return _profile(self._get("/me", params={"fields": _PROFILE_FIELDS}))

    # ---- own posts --------------------------------------------------------------

    def my_posts(self, limit: int = 25) -> tuple[Post, ...]:
        """GET /me/threads — the user's OWN posts, paginated via `paging.next`.
        There is no home-timeline / other-users'-feed read in the core API;
        this is deliberately scoped to `/me/threads` only."""
        posts: list[Post] = []
        data = self._get("/me/threads", params={"fields": _POST_FIELDS, "limit": min(limit, 25)})
        while True:
            posts.extend(_post(p) for p in data.get("data", []))
            if len(posts) >= limit:
                break
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                break
            data = self._get_absolute(next_url)
        return tuple(posts[:limit])

    def post(self, post_id: str) -> Post:
        return _post(self._get(f"/{post_id}", params={"fields": _POST_FIELDS}))

    # ---- publish (2-step: create container, then publish) ---------------------

    def publish_text(self, text: str) -> Post:
        """Create a TEXT container then publish it. Returns the published post
        (a follow-up GET for the fields the CLI renders) — `# verify-live`:
        the create/publish response shapes themselves (`{"id": "..."}`) follow
        the documented pattern but weren't re-confirmed live."""
        created = self._request(
            "POST", "/me/threads", data={"text": text, "media_type": "TEXT"}
        )
        creation_id = created.get("id")
        if not creation_id:
            raise ThreadsApiError(f"Container creation returned no id: {created}")
        published = self._request("POST", "/me/threads_publish", data={"creation_id": creation_id})
        post_id = published.get("id")
        if not post_id:
            raise ThreadsApiError(f"Publish returned no id: {published}")
        try:
            return self.post(post_id)
        except ThreadsApiError:
            # Publish succeeded even if the follow-up GET fails (e.g. brief
            # propagation delay) — don't let a read failure mask a real write.
            return Post(id=post_id, text=text, media_type="TEXT")

    # ---- replies ----------------------------------------------------------------

    def replies(self, post_id: str) -> tuple[Reply, ...]:
        data = self._get(f"/{post_id}/replies", params={"fields": _REPLY_FIELDS})
        return tuple(_reply(r) for r in data.get("data", []))

    def conversation(self, post_id: str) -> tuple[Reply, ...]:
        data = self._get(f"/{post_id}/conversation", params={"fields": _REPLY_FIELDS})
        return tuple(_reply(r) for r in data.get("data", []))

    # ---- insights ----------------------------------------------------------------

    def post_insights(self, post_id: str, metrics: tuple[str, ...] = ("views", "likes", "replies", "reposts", "quotes")) -> Insights:
        data = self._get(f"/{post_id}/insights", params={"metric": ",".join(metrics)})
        return _insights(data)

    def user_insights(self, metrics: tuple[str, ...] = ("views", "likes", "replies", "reposts", "quotes")) -> Insights:
        data = self._get("/me/threads_insights", params={"metric": ",".join(metrics)})
        return _insights(data)


# ---- wire → model maps (the caged knowledge) --------------------------------


def _error_body(resp: httpx.Response) -> dict:
    try:
        return resp.json().get("error", {})
    except Exception:
        return {}


def _is_auth_failure(status_code: int, err: dict) -> bool:
    if err.get("type") == "OAuthException":
        return True
    if err.get("code") == 190:
        return True
    return status_code == 401


def _is_rate_limit(err: dict) -> bool:
    return err.get("code") in _RATE_LIMIT_CODES


def _retry_after(resp: httpx.Response) -> int | None:
    ra = resp.headers.get("retry-after")
    return int(ra) if ra and ra.isdigit() else None


def _profile(obj: dict) -> Profile:
    return Profile(
        id=obj.get("id", ""),
        username=obj.get("username", ""),
        name=obj.get("name"),
        profile_picture_url=obj.get("threads_profile_picture_url"),
        biography=obj.get("threads_biography"),
    )


def _post(obj: dict) -> Post:
    return Post(
        id=obj.get("id", ""),
        text=obj.get("text"),
        media_type=obj.get("media_type"),
        permalink=obj.get("permalink"),
        timestamp=obj.get("timestamp"),
    )


def _reply(obj: dict) -> Reply:
    return Reply(
        id=obj.get("id", ""),
        text=obj.get("text"),
        username=obj.get("username"),
        permalink=obj.get("permalink"),
        timestamp=obj.get("timestamp"),
    )


def _insights(data: dict) -> Insights:
    metrics = []
    for m in data.get("data", []):
        # Metric values arrive as a `values: [{"value": N}]` list (time-series
        # shape) for most metrics; total-value metrics use `total_value.value`
        # instead — both are documented shapes. `# verify-live`.
        value = None
        values = m.get("values")
        if values and isinstance(values, list) and values[0].get("value") is not None:
            value = values[0]["value"]
        elif (tv := m.get("total_value")) is not None:
            value = tv.get("value")
        metrics.append(
            InsightMetric(name=m.get("name", ""), value=value, title=m.get("title"), description=m.get("description"))
        )
    return Insights(metrics=tuple(metrics))
