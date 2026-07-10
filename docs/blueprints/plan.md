# youtube-cli — plan

> 2026-07-11 · status index (one layer, by status). Only "what to do + why".
> youtube-cli is a THIN shell over youtube-toolkit — see `../../CLAUDE.md` for
> the stay-thin / CLI-vs-toolkit discipline. Toolkit-side work is tracked in
> youtube-toolkit's own `docs/blueprints/plan.md`.

## ▶ Next —— audit follow-ups (surfaced 2026-07-11)
- **No-mock integration tests (highest value)** — every current CLI test mocks
  the toolkit at its sub-API boundary, so signature drift is invisible: a flag
  that doesn't thread (`transcript --lang` was dead at every layer), a changed
  sub-API signature (`download.playlist` raised a `TypeError` on `output_path`),
  or a stale-install mismatch all slip past a green suite. Add a handful of
  tests that bind the CLI's calls against the REAL toolkit sub-API signatures
  (no network) so this whole class of bug is caught before runtime.
- **CI (GitHub Actions)** — run `uv run pytest` + `ruff` on push/PR. Prereq: a
  git remote must exist first (tracked separately, deferred) — CI has nowhere
  to run until then.
- **Document the uv git-dependency update workflow** — youtube-toolkit is a git
  dependency tracking `main`, so picking up a toolkit update needs a CLEAN
  reinstall (`--no-cache`, or a fresh venv). `uv pip install --reinstall-package`
  alone can serve a stale build (bit us on 2026-07-11: the install reported the
  new commit hash but shipped the old code). Add a one-line note to
  `CLAUDE.md`/`README.md` so this doesn't recur.

## ⏸ Future —— deferred
> expose when a real need shows up; the toolkit already supports these
- **More toolkit surface as commands** — live-stream download, thumbnails,
  trending, comment replies, filtered/archived/cookie downloads. Each is a thin
  new command over an existing sub-API; add on demand, not speculatively.

## ✅ Shipped
- **2026-07-10/11** — split into its own repo (git dep on youtube-toolkit via
  GitHub `main`); `CLAUDE.md` governance; audit-driven fixes (shared `_handle()`
  clean-error wrapper + exit codes, Rich-markup escaping in cli.py + render.py,
  `--sort` crash fix, `comments` error surfacing, `--sponsorblock` flag-conflict
  warnings, doc consistency); grew 6 → 11 commands (channel, playlist, captions,
  formats, sponsorblock) + quick flags + search filters + `comments --search`;
  `channel --sort` extended to shorts/streams and `playlist --videos` now shows
  full detail once the toolkit follow-ups landed. (40 tests; commits `85c80f3`,
  `56dffbd`.)
