# clarity-ctl

**Project state you can read in one command, and your coding agent can query without crawling the tree.**

clarity keeps track of a project's work in one file, `worklog.yaml`: what the project is
for, the ideas waiting, the work in flight, and what was tried along the way. A small CLI
is the only thing that writes to it. It prints the state for you and serves it as JSON to
an agent. Nothing in it needs a model.

Your code lives in its own repos, beside clarity's files rather than mixed in with them,
so pushing your code never pushes the worklog.

## Install

Requires Python 3.10+ and git.

```sh
uv tool install clarity-ctl         # or: pipx install clarity-ctl
```

## Quick start

A project is a folder. clarity's files sit at the top, and your code goes in
`workspace/`, one repo per folder:

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

`clarity-ctl status` shows where things stand:

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

An **idea** is one line in the worklog. It has no folder and no branch, so writing one
down costs nothing. An **epoch** is an idea you've started: one feature, fix or
experiment, with a folder of its own and a branch in each of your repos.

```
idea ──start──► active ──close──► done
                 │  ▲      ▲        │
             block  unblock └─reopen─┘
```

- `epoch start` creates the branch `epoch/NNN-slug` in every listed repo and checks it
  out as a worktree inside the epoch's folder.
- `note` records what you tried and why a decision changed, where the next person or
  agent will find it.
- `epoch block` parks an epoch with the reason it's waiting. `unblock` picks it back up.
- `epoch refresh` merges `main` into the epoch's branch. It refuses if there is
  uncommitted work.
- `epoch close` removes the worktree and keeps the branch. `--abandon` records the epoch
  as dropped. `epoch reopen` brings it back, and keeps the old outcome as a note.

## What's on disk

```
parser/
├── worklog.yaml            the source of truth
├── .clarity/config.yaml    project settings
├── AGENTS.md, CLAUDE.md    tell agents to use clarity
├── EPOCHS/
│   └── 002_2026-09-21__try-a-ring-buffer-for-the-tokenizer/
│       ├── EPOCH.md        generated summary and notes; yours below the marker
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

`init` writes a short block into `AGENTS.md`, and `CLAUDE.md` imports it. The block
tells any agent to query clarity instead of crawling the tree, to claim an epoch before
working on it, and to write a note when a decision changes. Anything else in those files
is left alone.

```sh
clarity-ctl install --global   # your own agents: ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ...
clarity-ctl install --remove   # take the block back out
```

The global block does nothing in folders without a `worklog.yaml`, so it's safe everywhere.

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

**Commands always take the epoch id.** clarity never guesses it from your current
folder or from there being only one epoch in flight. A guess like that works until a
second epoch starts, and then the same command quietly means something else.

### Several agents at once

Each epoch has its own branch and worktree, so parallel work never shares files. To show
who is on what, an agent claims an epoch first:

```sh
clarity-ctl claim 7
clarity-ctl release 7
```

A claim is a warning, not a lock. A second claim is refused unless it passes `--steal`,
which records who it was taken from. Claims never expire on their own.

## Existing projects

Run `clarity-ctl adopt` in a folder that already has work in it.

**If the folder is a git repo**, adopt moves all of it to `workspace/<folder name>/`,
including history, branches, uncommitted work and ignored files, and sets clarity up
beside it. Anything that recorded an absolute path, like a virtualenv, needs rebuilding.

**If the folder holds several checkouts and loose files**, adopt writes
`.clarity/adopt.md`, a procedure for an agent to follow. The agent asks what the project
is for, then proposes where everything goes:

- **`workspace/`**: repos you change, and the scripts and fixtures around them.
- **`3rd_party/`**: repos you only use, such as a tool or a benchmark harness.
- **A baseline epoch**: logs and results from work already done, closed on arrival.

Every move is proposed as a table and waits for your approval.

## Design principles

1. **The worklog is the only state.** Anything that can change behind clarity's back,
   like who holds a claim or how far a branch trails `main`, is looked up when asked.
2. **Only commands write state.** Editing `worklog.yaml` by hand skips the lock that
   keeps concurrent writers safe.
3. **Nothing is destructive.** Worktrees are removed, but branches never are.
4. **No model required.** Plain YAML, git and deterministic code.

## Development

```sh
uv venv && uv pip install -e . pytest
.venv/bin/python -m pytest -q tests
```

## License

MIT — see [LICENSE](LICENSE).
