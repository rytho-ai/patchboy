# patchboy-core

Shared primitives for the patchboy workspace — the ONE genuinely-identical
mechanic duplicated across two or more members, extracted once and reused.

Right now that's exactly one thing: `patchboy_core.atomicio.atomic_write_text`,
the atomic-0600-file-write dance that spotify-cli, applemusic-cli, and
splice-cli's session/budget persistence all implemented byte-for-byte
identically.

See `CLAUDE.md` for the scope rule that keeps this package from accreting
service-specific logic.
