# CLAUDE.md — Development principles for patchboy-core

`patchboy-core` is the shared-primitives package for the patchboy workspace.
It holds **only** cross-member primitives that are genuinely identical — not
similar, not "close enough," byte-for-byte the same mechanic solving the same
problem the same way. Right now that's exactly one thing: `atomicio`.

## The scope rule

The rest of the workspace deliberately does NOT share auth flows, wire
shapes, render tables, or exit-code routing across spotify-cli /
applemusic-cli / splice-cli / youtube-cli — those are genuinely different per
service, and forcing them into one shape would be a wrong abstraction, worse
than the duplication it removes. `core` exists as the pressure-release valve
for the rare case where duplication really is just duplication.

**Before adding anything here, it must pass both:**

1. The thing being added is needed, unchanged, in **≥ 2 members already** —
   not "will probably be needed," not "would be nice to share." A single
   consumer belongs in that member, not here.
2. It is **byte-for-byte the same mechanic** — same steps, same order, same
   edge-case handling — not merely similar-looking code that happens to do
   related things slightly differently. If a caller needs even one different
   step (a different mode, bytes instead of text, an extra step), that
   caller does NOT belong here — leave it duplicated in the member.

If a change here would require also changing what a member's `save()`/`load()`
means — i.e. any per-service payload shape, field names, or business logic —
that logic does NOT belong in `core`. `core` knows nothing about tokens,
sessions, budgets, or JSON payloads; it only knows how bytes hit disk.

## What's here

- `atomicio.py` — `atomic_write_text(path, text, *, mode=0o600)`: write text
  to a temp file, chmod it, then `os.replace()` into place. Atomic on POSIX,
  no world-readable window. Extracted from spotify-cli/applemusic-cli/
  splice-cli's session + budget persistence, which all implemented this
  identically.

## Environment & tooling

Same shape as the other workspace members: `src/patchboy_core/`, `uv run
pytest`, `ruff check .`, `mypy src`. No runtime dependencies — pure stdlib,
and it should stay that way; a dependency here becomes every member's
dependency.
