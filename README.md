# patchboy

**The panel where every external platform patches in** — a little rig that jacks
your agents into every account and catalog you have out there. patchboy is a
[uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): a
monorepo of small, **independently-installable** CLIs, one per external service,
each an agent-native thin shell over that service's surface.

Named for the studio patchbay — the one panel where every external device plugs
in and gets routed. (The `-boy` is the persona: the little patch-boy who wires
you into the outside world.)

## Members

Each is its own installable package with its own bare command:

| CLI | command | reaches |
|---|---|---|
| `spotify-cli` | `spotify` | Spotify Web API — catalog + your library |
| `applemusic-cli` | `applemusic` | Apple Music — catalog + your library |
| `splice-cli` | `splice` | Splice samples — search / preview / buy / stacks |
| `youtube-cli` | `youtube` | YouTube — search/download/transcript + your own channel (OAuth) |

More platforms are the whole point of the container — media (SoundCloud, TikTok)
and social (Threads, Instagram, Facebook) are on the roadmap; each arrives as a
new sibling member, not a new repo.

## Install (independently)

Each member installs on its own — you don't get "all of patchboy", you get the
one command you want:

```bash
uv tool install ./spotify-cli      # → a bare `spotify` from anywhere
uv tool install ./youtube-cli      # → a bare `youtube`
```

## Develop (the whole workspace)

```bash
uv sync --all-extras               # one venv, one lockfile, all members editable
cd spotify-cli && uv run pytest    # each member keeps its own tests
```

## Layout

```
patchboy/
├── pyproject.toml     # virtual workspace root (members + shared git sources)
├── spotify-cli/       # each member: own pyproject, [project.scripts], src, tests,
├── applemusic-cli/   #   CLAUDE.md, README, .claude/skills/ — the caged wire
├── splice-cli/        #   knowledge (each service's client.py) stays per-member.
└── youtube-cli/
```

Each member's `client.py` (the caged, service-specific API shape) is deliberately
**not** shared — that's correct information-hiding. What the members duplicate
today (atomic 0600 session save, the exit-code handler, rich render helpers, the
OAuth loopback mechanics) is slated to move into a shared `core` package in a
later phase — the reason a workspace exists at all.

> History note: the members were separate git repos before this workspace; their
> full per-commit history lives in those original checkouts. patchboy starts its
> own history here.
