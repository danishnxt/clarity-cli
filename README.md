# clarity-ctl

**Project state you can read in one command, and your coding agent can query without crawling the tree.**

A coding agent can easily overtake your own thoughts and plans in a project and run away
with its own reasoning. That leaves you with:

1. Gaps in your understanding.
2. Logs too long to read, that confuse more than they explain.
3. Experiment results, good and bad, that can't be tied back to a cause.
4. Tokens burned on catching up. Every session starts with you re-explaining where
   things stand, and the agent re-reading the tree to find out for itself.

For a small toy project that can be fine. For larger work, where you need a complete
handle on the state of things, it's a real problem.

clarity-ctl is a small CLI and a few simple rules for your agent. They keep you in the
loop with the agent instead of floundering behind it, trying to catch up by reading
extremely long logs. They also keep your work organized, which makes everything else
easier.

All of this is doable with good prompting, but clarity-ctl makes it close to automatic.

The model: each cycle of work is an **epoch**, and an epoch keeps its logs, its
learnings, and its worktrees and branches together, so both you and your agent can
look through them easily. I find it very hard to make sense of a repo after Claude or
Codex has ploughed through it. This is the fix.

Clarity keeps track of a project's work in one file, `worklog.yaml`: what the project is
for, the ideas waiting, the work in flight, and what was tried along the way. A small CLI
is the only thing that writes to it. It prints the state for you and serves it as JSON to
an agent. Nothing in it needs a model.

## Install

Requires Python 3.10+ and git.

```sh
uv tool install clarity-ctl         # or: pipx install clarity-ctl
```

## Quick start

The first thing clarity does is keep its own files out of your code. An agent's
scratch work, plans and notes belong to the project, not to your repo's history, so
your code lives in its own repo under `workspace/`, next to clarity's files and never
mixed in with them. Pushing your code never pushes the worklog.

```sh
mkdir parser && cd parser
clarity-ctl init --objective "Make the parser fast enough for 1GB inputs"
git clone git@github.com:you/parser.git workspace/parser
clarity-ctl repo add workspace/parser
```

Already have a repo? Run `clarity-ctl adopt` inside it instead. See
[Existing projects](#existing-projects).

Write down what you might do, then start something:

```sh
clarity-ctl idea add "flaky test on macOS" --type fix
clarity-ctl idea add "try a ring buffer for the tokenizer" --type experiment

clarity-ctl epoch start 2
clarity-ctl note 2 "ring buffer 2x slower than the deque on small inputs — keeping the deque below 64KB"
clarity-ctl epoch close 2 --outcome "kept the deque; ring buffer only above 64KB"
```

`clarity-ctl status` is the one command that tells you where things stand. It's the
same answer you get after a coffee break or a week away, and the same one your agent
gets:

```
OBJECTIVE  Make the parser fast enough for 1GB inputs

In flight (1)
────────────────────────────────────────────────────────────────────────
  ▸ EPOCH      2  try a ring buffer for the tokenizer
                  EPOCHS/002_2026-09-21__try-a-ring-buffer-for-the-tokenizer   [held by you@laptop, just now]

Up next (1)
────────────────────────────────────────────────────────────────────────
  · IDEA       1  flaky test on macOS

0 closed or dropped — clarity-ctl status --all
```

## Ideas and epochs

Left alone, an agent will start the next thing before the last one is finished, and a
week later nobody knows which change came from which attempt. Ideas and epochs are how
you keep that apart.

An **idea** is one line in the worklog. It has no folder and no branch, so writing one
down costs nothing. It's somewhere to put "we should also…" without derailing the work
in front of you.

An **epoch** is an idea you've started: one feature, fix or experiment. It gets a folder
of its own and a branch in each of your repos, so everything it touches stays with it.

```
idea ──start──► active ──close──► done
                 │  ▲      ▲        │
             block  unblock └─reopen─┘
```

- `epoch start` creates the branch `epoch/NNN-slug` in every listed repo and checks it
  out as a worktree inside the epoch's folder.
- `epoch block` parks an epoch with the reason it's waiting. `unblock` picks it back up.
- `epoch refresh` merges `main` into the epoch's branch. It refuses if there is
  uncommitted work.
- `epoch close` asks for the outcome in one line, removes the worktree and keeps the
  branch. `--abandon` records the epoch as dropped. `epoch reopen` brings it back, and
  keeps the old outcome as a note.

## Notes: the why, not the log

What you lose when an agent runs ahead is rarely *what* it did. The diff shows that. What
you lose is *why*: the approach it tried first and dropped, the number that surprised it,
the reason it changed direction. That's what turns a result into something you can trust
or explain.

```sh
clarity-ctl note 2 "ring buffer 2x slower on small inputs — keeping the deque below 64KB"
```

A note is one line, timestamped, attached to its epoch. The rules clarity gives your
agent tell it to write one the moment a decision changes, not at the end. In a long
session the end never comes, and the notes are what the next session has instead of the
conversation.

## What's on disk

You should be able to open the folder and see what happened, without asking anyone,
including the agent. Everything about an epoch lives in its folder:

```
parser/
├── worklog.yaml            the source of truth
├── .clarity/config.yaml    project settings
├── AGENTS.md, CLAUDE.md    the rules for your agent
├── EPOCHS/
│   └── 002_2026-09-21__try-a-ring-buffer-for-the-tokenizer/
│       ├── EPOCH.md        summary and notes, generated; yours below the marker
│       ├── PLANS/          long-form docs for this piece of work
│       ├── LOGS/ ANALYSIS/ VIZ/
│       └── wt/parser/      the epoch's worktree of workspace/parser
└── workspace/
    └── parser/             your repo, with its own remote
```

The top folder is a git repo too, holding only clarity's files. It ignores `workspace/`,
has no remote unless you add one, and clarity never commits to it. Commit the worklog
when you want a snapshot.

## Working with agents

An agent opening a project usually spends its first minutes, and a pile of tokens,
re-reading the tree to work out what's going on, and often gets it wrong. Clarity gives
it the answer instead.

`init` writes a short block of rules into `AGENTS.md`, and `CLAUDE.md` imports it. The
rules tell the agent to ask clarity for the state rather than crawl for it, to claim an
epoch before working on it, and to write a note when a decision changes. Anything else
in those files is left alone.

```sh
clarity-ctl install --global   # your own agents: ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ...
clarity-ctl install --remove   # take the block back out
```

The global block does nothing in folders without a `worklog.yaml`, so it's safe to
install everywhere.

Every read takes `--json` and answers with the same envelope:

```sh
clarity-ctl q active --json      # in flight
clarity-ctl q item 2 --json      # one item, with its notes, claim and PLANS/ listing
```

```json
{"ok": true, "command": "q.item", "data": {"id": 2, "status": "active", "...": "..."},
 "warnings": [], "version": "0.1.0"}
```

Exit codes: `0` ok · `1` error · `2` validation failed · `3` not a clarity project ·
`4` refused. The envelope and exit codes are stable; the human-readable output may change.

**Commands always take the epoch id.** Clarity never guesses it from your current
folder, or from there being only one epoch in flight. A guess like that works until a
second epoch starts, and then the same command quietly means something else.

### Several agents at once

Once you run two agents, the question becomes who is doing what. Each epoch has its own
branch and worktree, so their work never shares files. To make it visible, an agent
claims an epoch first:

```sh
clarity-ctl claim 7
clarity-ctl release 7
```

A claim is a warning, not a lock. A second claim is refused unless it passes `--steal`,
which records who it was taken from. Claims never expire on their own.

## Existing projects

Most projects worth this already exist, and they're usually the messiest. Run
`clarity-ctl adopt` in one.

**If the folder is a git repo**, adopt moves all of it to `workspace/<folder name>/`,
including history, branches, uncommitted work and ignored files, and sets clarity up
beside it. Anything that recorded an absolute path, like a virtualenv, needs rebuilding.

**If the folder holds several checkouts and loose files**, adopt writes
`.clarity/adopt.md`, a procedure for an agent to follow. The agent asks what the project
is for, then proposes where everything goes:

- **`workspace/`**: repos you change, and the scripts and fixtures around them.
- **`3rd_party/`**: repos you only use, such as a tool or a benchmark harness.
- **A baseline epoch**: logs and results from work already done, closed on arrival.

Every move is proposed as a table and waits for your approval. The agent sorts the
mess; you decide.

## Design principles

1. **The worklog is the only state.** Anything that can change behind clarity's back,
   like who holds a claim or how far a branch trails `main`, is looked up when asked.
2. **Only commands write state.** Editing `worklog.yaml` by hand skips the lock that
   keeps concurrent writers safe.
3. **Nothing is destructive.** Worktrees are removed, but branches never are.
4. **No model required.** Plain YAML, git and deterministic code. Judgment stays with you.

## Development

```sh
uv venv && uv pip install -e . pytest
.venv/bin/python -m pytest -q tests
```

## License

MIT — see [LICENSE](LICENSE).
