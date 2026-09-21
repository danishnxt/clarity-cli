# clarity

Project state you can read in one command, and your agent can query without crawling the tree.

State lives in `worklog.yaml`. A deterministic CLI renders it for you and filters it for an
agent. Nothing here needs a model to work.

## Install

```sh
uv tool install --editable .        # or: uv pip install -e . inside a venv
clarity --version
```

## Use it

```sh
cd ~/some_project
clarity init --objective "Compare parser A and B"

clarity idea add "flaky test on macOS" --type fix
clarity idea list
clarity idea promote 1              # -> planned, gets a folder
clarity epoch start 1               # -> active, branch + worktree

clarity note 1 "ring buffer was 2x slower"
clarity epoch block 1 "waiting on upstream"
clarity epoch unblock 1
clarity epoch close 1 --outcome "bumped the timeout"
clarity epoch reopen 1              # picked it back up; old outcome kept as a note
clarity rename 1 "parser B only"    # scope moved; old name kept as a note

cd $(clarity path 1)                # epoch 1's folder
clarity status                      # in flight + up next
clarity status --all
clarity q active --json             # what an agent calls
```

## Starting from a project that already has work in it

`init` assumes an empty or tidy folder. A workspace that grew before anyone thought
about layout — checkouts, caches, results and scripts all at the root — needs that root
tidied first, and deciding what is safe to move takes judgement, so it is not in the CLI.

```sh
cd ~/some_project
clarity adopt
```

That sets the project up and writes `.clarity/adopt.md`: the procedure an agent
follows. It asks what the project is for first, because that decides the sort:

- **`workspace/`** — repos you change, plus the scripts, fixtures and virtualenvs
  around them.
- **`3rd_party/`** — repos you only use: a tool, a dependency, a harness.
- **a baseline epoch** — logs, results and old experiments from work already done,
  created and immediately closed.

Directories move whole, never reaching inside one. If the root isn't a git repo, the
agent offers to make it one, tracking only clarity's own files.

Every move is proposed as a table and waits for your approval — moving a directory can
break it, and only you know which ones you still need. Point an agent at it; the clarity
block in `AGENTS.md` already tells every agent to read that file first when it exists.

Adopt does *not* try to work out what you're working on — the baseline epoch is the one
item it creates, and it's closed on arrival. Everything up to adoption is epoch 1; the
next thing you do is yours to start.

The last step tells the agent to delete it. Its absence is what "adoption finished"
means; there is no flag to set and no second copy to go stale.

## A workspace of several repos

When the code lives in checkouts under the root rather than in the root itself, list
the ones you change:

```sh
clarity repo add workspace/mini-swe-agent
clarity repo list
```

From then on `clarity epoch start` gives every listed repo the epoch's branch and a
worktree at `EPOCHS/NNN_.../wt/<repo>`, and leaves the root alone. Repos you only use
stay off the list. Close removes each worktree and records each repo's end commit;
refresh catches every repo up, or none if any has uncommitted work. A start that fails
in one repo undoes what it did in the others.

The list is copied onto an epoch when it starts, so changing it later doesn't strand
an epoch already in flight. With nothing listed, epochs branch the root — a single-repo
project never needs this.

## What `init` creates

```
your_project/
├── .clarity/
│   ├── config.yaml       style, behavior, hooks
├── EPOCHS/
│   └── 001_2026-09-17__slug/
│       ├── EPOCH.md      generated block; your notes go below the marker
│       ├── PLANS/        long-form docs for this work; clarity q item lists them
│       ├── wt/<repo>     worktree, or a symlink if the branch is checked out already
│       └── LOGS/ VIZ/ ANALYSIS/
├── LEARNINGS/
├── worklog.yaml          objectives + every item — the only source of truth
├── AGENTS.md             your file; clarity owns one marker block in it
└── CLAUDE.md             one line: @AGENTS.md
```

## Lifecycle

```
idea ──promote──► planned ──start──► active ──close──► done
                                      │  ▲       ▲       │
                                  block  unblock └─reopen─┘
```

`promote` gives an item a folder; `start` puts it in flight with a branch. Branch resolution:
already checked out somewhere → symlink to that path; exists but not checked out →
`git worktree add`; nothing given → create `epoch/NNN-slug`. Closing removes the worktree and
keeps the branch.

## Several epochs at once

Each epoch has its own worktree and branch, so parallel work is already isolated. Which
epoch a command means is always in the command:

```sh
clarity claim 7              # say you're working it
clarity note 7 "tried X"
clarity release 7
```

Clarity never infers the id — not from the directory you're in, not from an env var, not
from there happening to be only one epoch active. Inference works right up until a second
epoch starts, and then the same command means something different with nothing on screen
to say so; an agent that gets a **fresh shell per command** can't see any of that state
anyway. With the id in the command, a transcript shows which epoch each line hit.

A lease is `EPOCHS/NNN_.../.lease` — owner, host, and when it was claimed. Another holder
blocks a claim unless you pass `--steal`, which records who you took it from.

It's a warning, not a lock: nothing stops a second agent editing the worktree, and clarity
doesn't pretend otherwise. Leases never expire and nothing checks whether the holder is
alive — both were tried, both guessed wrong, and a lease that guesses wrong is worse than
none. A stale lease waits for a person to `--steal` it, the way `terraform force-unlock`
does.

## Telling your agents

Agents each read their own instructions file, so clarity writes one marker-fenced
block into whichever ones you have and leaves every other byte alone:

```markdown
<!-- CLARITY:START -->
## clarity
... query it, don't crawl the tree; claim before you work; note as you go
<!-- CLARITY:END -->
```

`init` does this for the repo. To do it again, or to pick a scope:

```sh
clarity install            # this repo: AGENTS.md, CLAUDE.md, + any others present
clarity install --global   # you: ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ...
clarity install --remove   # take the block back out
```

`AGENTS.md` and `CLAUDE.md` are always written; `GEMINI.md`, `QWEN.md`, `WARP.md`,
`.windsurfrules` and `.github/copilot-instructions.md` are joined only if you already
keep one, so clarity never litters a repo for agents nobody there runs. A global
install writes only to agents you have — `~/.codex` existing is what says you run
Codex.

Re-running is idempotent, your own content is preserved, and `--remove` gives the
file back exactly as it was (deleting it only if the block was all it held).

The block is deliberately short and states its own precondition — *no `worklog.yaml`
at the root, no clarity* — so one global install is safe across every project you
open, including the ones that never adopted it. It is a constant: there is nothing
to regenerate and nothing that can fall behind. `AGENTS.md` holds it and `CLAUDE.md`
imports that file rather than keeping a second copy.

The objective is **not** in it. That lives in `worklog.yaml` and is printed by
`clarity status`. A copy in an instructions file would be a second place to keep in
step, and it is the kind that goes stale silently.

## For agents and hooks

Every read command takes `--json`:

```json
{"ok": true, "command": "q.active", "data": {...}, "warnings": [], "version": "0.1.0"}
```

Exit codes: `0` ok · `1` error · `2` validation failed · `3` not a clarity project · `4` refused.
Those two are the contract; human-readable output is free to change.

Python, same implementation the CLI uses:

```python
from clarity import Project
p = Project.find()
p.query("active")
ep = p.add("cache warmup", type_="experiment", status="planned")
p.start(ep.id, branch="perf/cache")
p.close(ep.id, outcome="2x slower, dropped")
```

## Rules the design holds to

1. Only the CLI writes state. Hand edits are legal; `doctor` (step 5) is what catches them.
2. Generated files are never edited — they're rewritten from the worklog whenever their
   inputs move, and left alone when they don't.
3. A generated file caches only a pure function of what clarity owns. Who holds a lease,
   how far a branch trails main, and what sits in `PLANS/` are all answered when asked and
   never written down — so there is no second copy to go stale. It is also why
   `clarity status` prints and never writes.
4. Anything below `<!-- /GENERATED -->` in `EPOCH.md` is yours and is preserved.
5. Nothing is destructive: worktrees are removed, branches never are, and an agent's
   `CLAUDE.md` keeps every byte outside clarity's own marker block.
6. It all works with no model installed.

## Not built yet

`doctor`, `view` (read-only pane), hooks, and everything about logs.

## Where the record lives

Decisions belong to the work that produced them. Each epoch holds its own `PLANS/` for
long-form docs, and `clarity note` writes the one-liners into `EPOCH.md`:

```markdown
## Tried
- `2026-09-18` lease TTL and dead-pid check both guess wrong — PLANS/concurrency.md
```

`EPOCH.md` deliberately does not index `PLANS/`. Anyone can drop a file in there with `mv`,
so no command can know when it changed — a cached listing would be stale exactly when it
mattered. `clarity q item <id>` reads the directory and lists what is actually there.

There is no project-wide plans directory and no `decide` command: a decision is a note that
names its plan file, and long rationale belongs in the commit message, attached to the diff.

## Development

```sh
.venv/bin/python -m pytest -q
```

Modules: `model.py` (dataclasses, no I/O) · `store.py` (YAML, atomic writes) · `gitops.py`
(branches and worktrees) · `render.py` (generated text) · `project.py` (the API) ·
`cli.py` (parsing and envelopes).
