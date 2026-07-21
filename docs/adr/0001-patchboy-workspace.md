# ADR 0001 — patchboy: one workspace for the external-platform CLIs

**Status:** accepted · **Date:** 2026-07-21

## Context

Rytho AI keeps growing a family of small, agent-native CLIs, each a thin shell
over one *external platform*: `spotify`, `applemusic`, `splice`, `youtube` today;
SoundCloud, TikTok and the social platforms (Threads, Instagram, Facebook) on the
roadmap. They are the outward-facing layer — the one part of the system that
reaches *out* to accounts and catalogs on other services (the rest of the
capability line — audion/tactus/gamut/phonon — operates on our own material).

Each CLI started as **its own git repo**. Two costs compounded with every new one:

1. **Repo sprawl.** N services → N repos to clone, gate, and reason about.
2. **Cross-repo duplication.** The same primitives were copy-pasted across repos —
   most visibly the atomic 0600 secret-file write, which existed byte-for-byte in
   four places. A fix (or a bug) had to be chased across repos, and a change that
   spanned two repos (e.g. youtube-cli depending on youtube-toolkit) meant a
   push-then-bump-the-lock dance.

## Decision

Consolidate the external-platform adapter CLIs into **one `uv` workspace
monorepo, `patchboy`**, where:

- each CLI is an **independently-installable** workspace member (its own
  `[project.scripts]`; `uv tool install ./spotify-cli` gives a bare `spotify`),
- a shared **`patchboy-core`** member holds cross-member primitives, and
- each member keeps its own **caged `client.py`** (the service-specific API wire
  shape) and its own auth/render/exit-code logic.

We explicitly did **not** collapse them into a single unified binary (a
`plug spotify …` / `plug youtube …`).

### Why a workspace, not one binary

A single binary would pull the **union** of every service's dependencies (yt-dlp/
moviepy from youtube alongside everything else), risk hard **version conflicts** in
one shared env, and give every bug/dep a **blast radius** across all services (and
one-version-for-everything releases). The workspace gets the two wins we actually
wanted — kill the duplication, make cross-member changes a single atomic commit —
**without** those costs: each member is its own installable package with its own
resolved deps; only `patchboy-core` is shared, and its deps are the *intersection*
(pure stdlib today), not the union. Per-service commands also read better
(`youtube my-channel`, not `plug youtube my-channel`) and leave the skills
untouched.

### The `core` scope rule (the load-bearing constraint)

`patchboy-core` holds **only** primitives that are **byte-for-byte identical** and
already needed in **≥ 2 members** — not "similar," not "probably useful later."
Auth flows, browser cookie-lift, render tables, and exit-code routing are
genuinely *different* per service; unifying them would be a **wrong abstraction**,
worse than the duplication it removes ("duplication is cheaper than the wrong
abstraction"). If a caller needs even one different step, it stays duplicated in
the member. `core` knows nothing about tokens/sessions/budgets — only how bytes
hit disk. (Enforced in `core/CLAUDE.md`.) Today `core` contains exactly one thing:
`atomic_write_text`.

### Migration & naming

- **History preserved:** each old repo's full per-commit history (original hashes)
  was grafted in via `git subtree`; the standalone repos were then removed.
  patchboy is now the single home.
- **`apple-music-cli` → `applemusic-cli`** (command `applemusic`, module
  `applemusic_cli`) for family consistency — the siblings are all single-token
  (`spotify`/`splice`/`youtube`). The "Apple Music" service name and
  `APPLE_MUSIC_*` env vars are unchanged.
- **Name:** *patchboy* — the studio **patchbay** (the one panel where every
  external device patches in and gets routed), personified. As a *repo/workspace*
  name the studio-gear register is apt and doesn't compete with the `phonon`
  ("器材") capability's positioning — nobody navigates a repo the way they
  navigate a product.

## Consequences

- One repo, one lockfile, one gate; a shared `core` fixed the duplication without a
  publish/version dance (workspace path deps).
- Each member stays independently `uv tool install`-able; the skills (`/spotify`,
  `/applemusic`, `/splice`, `/youtube`) are unchanged.
- **Public-later is clean:** a member is already its own package behind a caged
  boundary, so open-sourcing one means extracting the package + `core`, not
  untangling a binary. (`youtube-toolkit` — a genuine library, not an adapter —
  deliberately stays a separate external dep.)
- A `core` change ripples to all consumers (must re-gate them) — acceptable and
  visible in a workspace, and bounded by the byte-for-byte scope rule.
- The `plug` name (a short umbrella command) is **shelved**, not used: if we ever
  want a single entry point, it can be added later as a thin meta-package that
  shells out to the members — without coupling their environments.

## Alternatives considered

- **Keep the polyrepo** (status quo) — rejected: the sprawl and duplication were
  the whole problem, and they compound with each new platform.
- **One unified `plug` binary** — rejected for the dependency-union / blast-radius
  costs above; the umbrella is deferrable and non-coupling if ever wanted.
- **Fold in the capability/product packages** (tactus/audion/gamut/phonon) —
  rejected: those are a different tier (they operate on our own material), not
  outward-facing adapters. patchboy is scoped to the adapter family only.
