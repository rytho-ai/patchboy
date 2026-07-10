# CLAUDE.md — Development principles for youtube-cli

Governance for Claude (or any AI assistant) working on this codebase. Same
discipline as sibling `youtube-toolkit`, but this repo is the opposite kind
of thing: a **thin shell**, not a library. The single most important rule is
therefore **stay thin** — and knowing exactly what "thin" excludes is the
whole job. When in doubt, the logic belongs in the toolkit, not here.

## Prime directive: this is a thin shell over youtube-toolkit

youtube-cli owns four things and nothing else:

1. **Argument parsing** (Typer commands + flags).
2. **Calling a toolkit sub-API** (`toolkit.get/download/search/analyze/stream`).
3. **Rendering** the returned shape (`render.py`).
4. **Presenting errors + exit codes** (catch the toolkit's exception, show a
   clean message, pick 0/1/2 — never let a raw traceback reach the user).

Everything else — extraction, backend selection, fallback between
pytubefix/yt-dlp/API, retries, anti-detection, rate limiting, data shapes —
is **youtube-toolkit's** job. If you find yourself importing `yt_dlp`,
`pytubefix`, a backend, or reaching past a sub-API in this repo, stop: you're
writing toolkit code in the wrong repo.

The dependency is a **git source** (`[tool.uv.sources] youtube-toolkit =
{ git = "…/youtube-toolkit" }`, tracks `main`), so youtube-cli installs
standalone. Its architecture is governed by its own `CLAUDE.md` — read that
before deciding a change belongs upstream.

## The CLI ↔ toolkit boundary — the decision that matters

Before writing a fix or a feature, decide which repo it lands in:

| You want to… | Where it goes |
|---|---|
| Add/rename a command, flag, or default | **CLI** (`cli.py`) |
| Change how output looks (columns, truncation, panels) | **CLI** (`render.py`) |
| Turn a toolkit exception into a clean message + exit code | **CLI** (`cli.py`) |
| Surface a param the sub-API **already accepts** as a new flag | **CLI** — just wire it |
| Add a capability, filter, or param the sub-API **doesn't accept yet** | **toolkit first** (handler → service → sub-API), *then* a CLI flag |
| Fix extraction, fallback, backend, retry, or rate-limit behaviour | **toolkit** — never patch it here |
| Add a whole new domain (playlists, channels, captions export, live) | **toolkit** exposes it on a sub-API; CLI adds a command that calls it |

**Worked example (why this matters):** the `transcript --lang` flag looks
like a CLI concern, but the sub-API accepts `lang` and silently drops it, and
the service/handler don't take it at all (the handler hardcodes a language
preference list). So `--lang` does nothing *at any layer*. Making it real is
a **toolkit** change (thread `lang` through service → handler); the CLI edit
alone would be theatre. Diagnose the layer before you "fix" the flag.

Corollary: a flag that the CLI exposes but the toolkit ignores is worse than
no flag — it's a broken promise. When wiring a flag, verify the value
actually reaches and changes behaviour, don't assume.

## Error presentation — the CLI's real responsibility

The toolkit's fallback primitive raises a **generic `RuntimeError`** when
every backend fails (there are no typed exceptions upstream today). So:

- **Minimum bar:** every command wraps its toolkit call so a failure becomes
  a clean message + `raise typer.Exit(1)`, never a raw traceback.
- **Exit-code convention:** `0` ok · `1` ordinary failure (no results, video
  unavailable, backend error, missing API key with no fallback) · reserve `2`
  for a genuine human-action-required handoff (rare here — there's no login).
- **Known failure to surface, not swallow:** `transcript` can hit YouTube's
  IP rate-limiting ("blocking requests from your IP"). Tell the user
  directly; retrying immediately won't help. Presenting it cleanly is a CLI
  job; *classifying* it (vs a not-found or network error) would need typed
  exceptions upstream — a toolkit enhancement, note it rather than
  string-matching brittle messages here unless that's explicitly chosen.

## Adding a command — the pattern

1. Confirm the capability exists on a toolkit sub-API (read youtube-toolkit's
   `sub_apis.py`). If not, it's a toolkit change first — stop and do that.
2. Add a Typer command in `cli.py`: parse args → call the sub-API → hand the
   result to a `render.py` helper.
3. Put presentation in `render.py`, not inline in the command.
4. Wrap the toolkit call for clean error + exit code.
5. Update `README.md` and the skill (`.claude/skills/youtube/SKILL.md`) —
   keep the command/flag list in sync with the code, defaults included.

## Code organization

```
src/youtube_cli/
├── cli.py       # Typer commands: parse → sub-API → render → exit code
├── render.py    # presentation only (rich tables/panels); no toolkit calls
├── config.py    # Settings: YOUTUBE_API_KEY + download dir from env/.env
└── __init__.py
```

Keep it this small. Business logic showing up here is the signal to push it
upstream.

## Environment & tooling

- **src layout (required)** — package under `src/youtube_cli/`.
- **`uv` first** — `uv venv`, `uv run …`, `uv pip install -e .`. The
  youtube-toolkit dep resolves from GitHub `main`; a fresh `uv pip install -e .`
  pulls it, no sibling checkout needed.
- **Secrets:** the only credential is `YOUTUBE_API_KEY` (a config value, not a
  login). Never print it or let it land in a rendered table or error string.
  `.env` is gitignored; `.env.example` holds names only, never a real key.

## Testing

- Tests in `tests/`, run with `uv run pytest`. Mock the toolkit at its
  sub-API boundary — do NOT hit the network in unit tests.
- Worth covering: the error-presentation wrappers (a toolkit `RuntimeError`
  becomes exit 1 + message, not a traceback), flag→sub-API argument fidelity
  (the value actually reaches the call), and render helpers against
  None/loose-typed fields (upstream sometimes returns numeric strings).

## Communication

Converse with the user in **Traditional Chinese (Taiwanese phrasing)** —
plain, direct, concrete analogies. Keep code, identifiers, commit messages,
and this file in English.
