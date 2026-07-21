"""Stable vocabulary — hand-authored types the CLI and render speak in.

NOT a raw mirror of Threads' Graph API JSON. A field rename or reshape
upstream is absorbed by a one-line map in the caged client.py, invisible
here. Keep these minimal: only the fields the CLI actually renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    id: str
    username: str
    name: str | None = None
    profile_picture_url: str | None = None
    biography: str | None = None


@dataclass(frozen=True)
class Post:
    id: str
    text: str | None = None
    media_type: str | None = None
    permalink: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class Reply:
    id: str
    text: str | None = None
    username: str | None = None
    permalink: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class InsightMetric:
    """One named metric from an insights payload (views/likes/replies/...)."""

    name: str
    value: int | None = None
    title: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class Insights:
    metrics: tuple[InsightMetric, ...] = field(default_factory=tuple)
