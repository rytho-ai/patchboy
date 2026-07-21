---
name: threads
description: Read/publish on the user's OWN Threads account (profile, own posts, publish a text post, replies on own posts, insights) via threads-cli — Meta's official Threads API with Authorization Code OAuth (manual paste-back). Use when the user asks to check their Threads profile/posts, post to Threads, read replies on their own post, or pull Threads insights. NOT for reading a feed, other users' posts, or mentions — the core API doesn't expose those.
---

# Threads — operating manual

`packages/threads-cli` is a thin CLI over Meta's **official Threads API**
(Authorization Code OAuth, manual paste-back — no PKCE, no loopback server).
Sibling of `/spotify` (thin-shell shape) and `/splice` (the manual paste-back
OAuth flow this borrows). This skill is the decision manual; full command
reference + architecture: `packages/threads-cli/README.md` and its `CLAUDE.md`.

## The one thing to get right before anything else: this is NOT a feed reader

**There is no home-timeline / "my feed" read, and no confirmed mentions read**
in the core Threads API. Every read command returns data about the **logged-in
user's own account** — their own profile, their own posts, replies **on their
own posts**, their own insights. If the user asks "what's trending on
Threads" or "show me my mentions" or "what did @someone post", **say plainly
that this CLI can't do that** rather than guessing at a command that doesn't
exist. Don't invent a `feed`/`timeline`/`mentions` command.

## Login — the one gate, and how it works

Every command except `setup` needs a saved session. If there's none (or it's
expired), the command **exits 2** and prints a panel — that's the
human-in-the-loop handoff: relay it and stop, don't guess a workaround.

Two setup layers, check both when `login` says something's missing:

1. **App registration** (one-time, ever) — the user needs a Meta app with the
   Threads use case, an https redirect URI registered, and themselves added
   as a Threads Tester. `threads setup` prints the full walkthrough. If
   `THREADS_APP_ID`/`THREADS_APP_SECRET` aren't set, `threads login` prints
   this same walkthrough and exits 2 — that's the signal setup hasn't
   happened yet, not a login bug.
2. **The login itself** — `threads login` is **manual paste-back ONLY**
   (there is no loopback-server mode; Meta doesn't allow a localhost redirect
   for a Threads app). It prints a URL, the user approves in a browser, gets
   redirected to a page that will likely fail to load (**expected** — the
   code is in the URL regardless), and pastes the full URL back.

**You (the agent) cannot do the browser click** — that needs the user's own
Threads/Meta credentials. You also generally **cannot run `threads login`
yourself and feed it the paste** across separate tool calls, since it blocks
on an interactive prompt. Instead: call `threads_cli.auth.begin_login` /
`complete_login` directly, split across two tool invocations with the
challenge persisted to disk between them — same two-call recipe as
spotify-cli's CLAUDE.md → "Agent-driven login" (only the endpoint names
differ: Threads has no PKCE verifier to persist, just `state` + `redirect_uri`).

## Exit codes (key off these)

- `0` ok · `1` ordinary failure (no results, not found, rate limited, API
  error, a bad login paste) · `2` a human step is required (setup or login) —
  relay the panel verbatim, stop.

## Commands

```
threads setup                                # one-time Meta app walkthrough
threads login                                # manual paste-back OAuth
threads status                               # session present? fresh / near expiry?

threads me                                   # your own profile
threads posts [--limit N]                    # YOUR OWN posts only (paginated)
threads post "<text>"                        # publish a text post — WRITES to the account
threads replies <post-id> [--conversation]   # replies on one of your posts
threads insights [<post-id>]                 # post-level insights, or account-level if omitted
```

## `threads post` writes to the real account — be explicit

`post` publishes immediately, no draft/preview step. If you're driving this
on the user's behalf, confirm the exact text with them first (or quote back
what you're about to post) rather than composing and firing in one motion —
the CLI itself has no confirmation prompt by design (kept simple, matching
the task's "no auto-posting cleverness" scope).

## `replies` — two views of the same post

`--replies` (default) is Threads' flat reply list; `--conversation` is the
threaded view. Both only work on **your own** posts — there's no reading
replies on someone else's post through this CLI.

## Insights — post vs account

`threads insights <post-id>` → that post's metrics (views/likes/replies/
reposts/quotes). `threads insights` with no id → account-level metrics
instead. Don't confuse the two when relaying numbers back to the user.

## Token lifetime — usually invisible, but worth knowing

Sessions use a long-lived token (~60 days). `threads status` reports fresh /
near-expiry / expired. Near-expiry tokens are refreshed automatically,
best-effort, on the next command — you don't need to do anything. An
**expired** token can't be refreshed at all (a Meta-imposed rule, not a CLI
limitation) — only `threads login` again fixes that; the exit-2 panel will
say so.
