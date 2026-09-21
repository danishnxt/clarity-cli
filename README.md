# clarity-ctl

**Project state you can read in one command, and your coding agent can query without crawling the tree.**

clarity keeps a project's work in one file, `worklog.yaml`: what the project is for, the
ideas waiting, the work in flight, and what was tried along the way. A small,
deterministic CLI is the only thing that writes to it. It prints the state for you and
serves it as JSON to an agent. Nothing in it needs a model.

- **One source of truth.** Every command reads and writes `worklog.yaml`. Everything
  else is generated from it or computed when asked, so no copy can drift.
- **Epochs, not tickets.** A piece of work gets a folder, a git branch and its own
  worktree when it starts, so several can run side by side without touching each other.
- **A record that outlives the session.** `clarity-ctl note` writes down what was tried and
  why it changed, where the next person or agent will find it.
- **Built for agents.** Every read takes `--json`, exit codes are a contract, and
  commands always name the epoch they act on, so there is nothing to guess.
- **Several repos at once.** A workspace of checkouts branches the ones you change
  and leaves the rest alone.

## Install

Requires Python 3.10+ and git.

```sh
uv tool install clarity-ctl         # or: pipx install clarity-ctl
clarity-ctl --version
```

`uvx clarity-ctl status` runs it without installing. From a clone of this repo:
`uv tool install --editable .`

## Quick start

```sh
cd ~/parser
clarity-ctl init --objective "Make the parser fast enough for 1GB inputs"

clarity-ctl idea add "flaky test on macOS" --type fix
clarity-ctl idea add "try a ring buffer for the tokenizer" --type experiment
clarity-ctl idea add "document the config file" --type chore

clarity-ctl epoch start 2               # branch, worktree and folder for idea 2
clarity-ctl note 2 "ring buffer 2x slower than the deque on small inputs — keeping the deque below 64KB"

clarity-ctl epoch start 1
clarity-ctl epoch block 1 "waiting on the CI image fix upstream"
```

`clarity-ctl status` shows where things stand:

```
OBJECTIVE  Make the parser fast enough for 1GB inputs

In flight (2)
────────────────────────────────────────────────────────────────────────
  ⏸ BLOCKED    1  flaky test on macOS
                  blocked: waiting on the CI image fix upstream   [held by you@laptop, just now]

  ▸ EPOCH      2  try a ring buffer for the tokenizer
                  EPOCHS/002_2026-09-21__try-a-ring-buffer-for-the-tokenizer   [held by you@laptop, just now]

Up next (1)
────────────────────────────────────────────────────────────────────────
  · IDEA       3  document the config file

0 closed or dropped — clarity-ctl status --all
```

When the work is finished:

```sh
clarity-ctl epoch close 2 --outcome "kept the deque; ring buffer only above 64KB"
clarity-ctl epoch reopen 2              # back to active; the old outcome is kept as a note
clarity-ctl rename 2 "tokenizer buffers" # the old name is kept as a note too
```

## How it works

### Ideas and epochs

An **idea** is a line in the worklog, with no folder and no branch. It costs nothing to
write one down. An **epoch** is an idea you've started: one feature, fix or experiment,
with a folder of its own and a branch in every repo it touches.

```
idea ──promote──► planned ──start──► active ──close──► done
                                      │  ▲       ▲       │
                                  block  unblock └─reopen─┘
```

- `promote` gives an idea its folder early, so you can drop plans into it before starting.
- `start` creates the branch `epoch/NNN-slug` and checks it out as a worktree inside the
  epoch's folder. If the branch is already checked out somewhere, it links to that
  checkout instead.
- `block` parks an active epoch with the reason it's waiting. `unblock` picks it back up.
- `close` removes the worktree and keeps the branch. `close --abandon` records the epoch
  as dropped.
- `refresh` merges `main` into an epoch's branch. It refuses if there is uncommitted work.

### What's on disk

```
your_project/
├── worklog.yaml          objectives, repos and every item: the source of truth
├── .clarity/config.yaml  project settings
├── EPOCHS/
│   └── 002_2026-09-21__try-a-ring-buffer-for-the-tokenizer/
│       ├── EPOCH.md      generated summary and notes; yours below the marker
│       ├── PLANS/        long-form docs for this piece of work
│       ├── LOGS/ ANALYSIS/ VIZ/
│       └── wt/<repo>/    the epoch's worktree (gitignored)
├── AGENTS.md             your file; clarity owns one marker block in it
└── CLAUDE.md             imports AGENTS.md
```

Each epoch's `EPOCH.md` is rewritten from the worklog whenever the epoch changes:

```markdown
# 002 · try a ring buffer for the tokenizer

- **type** experiment
- **status** active
- **branch** epoch/002-try-a-ring-buffer-for-the-tokenizer
- **started** 2026-09-21    **ended** —

## Tried
- `2026-09-21` ring buffer 2x slower than the deque on small inputs — keeping the deque below 64KB

<!-- /GENERATED — write your own notes below this line -->
```

## Working with agents

`clarity-ctl init` writes a short marker-fenced block into `AGENTS.md` (and `CLAUDE.md`
imports it). It tells any agent to query clarity instead of crawling the tree, to claim
an epoch before working on it, and to write a note when a decision changes. Your other
content in those files is left alone.

```sh
clarity-ctl install            # this repo: AGENTS.md, CLAUDE.md, plus GEMINI.md etc. if present
clarity-ctl install --global   # your own agents: ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ...
clarity-ctl install --remove   # take the block back out
```

The block disables itself in folders without a `worklog.yaml`, so one global install is
safe in every project you open.

### Querying

Every read takes `--json` and answers with the same envelope:

```sh
clarity-ctl q active --json      # in flight: active and blocked
clarity-ctl q current --json     # what `status` shows
clarity-ctl q item 2 --json      # one item, with its lease and PLANS/ listing
```

```json
{
  "ok": true,
  "command": "q.item",
  "data": {
    "id": 2,
    "name": "try a ring buffer for the tokenizer",
    "type": "experiment",
    "status": "active",
    "folder": "EPOCHS/002_2026-09-21__try-a-ring-buffer-for-the-tokenizer",
    "branch": "epoch/002-try-a-ring-buffer-for-the-tokenizer",
    "started": "2026-09-21",
    "notes": [{"at": "2026-09-21 18:48:00", "text": "ring buffer 2x slower than the deque ..."}],
    "lease": {"owner": "you", "host": "laptop", "age": "just now"}
  },
  "warnings": [],
  "version": "0.1.0"
}
```

Exit codes: `0` ok · `1` error · `2` validation failed · `3` not a clarity project · `4` refused.
The envelope and the exit codes are stable. Human-readable output may change.

The CLI is a thin shell over a Python API, so scripts can use the same code:

```python
from clarity import Project

p = Project.find()
ep = p.add("cache warmup", type_="experiment", status="planned")
p.start(ep.id)
p.note(ep.id, "cold start 40% faster")
p.close(ep.id, outcome="shipped behind a flag")
```

### Several agents at once

Every epoch has its own branch and worktree, so parallel work is isolated on disk. To
show who is on what, an agent claims an epoch before working on it:

```sh
clarity-ctl claim 7              # records who holds epoch 7, on which host, since when
clarity-ctl note 7 "tried X"
clarity-ctl release 7
```

A claim is a warning, not a lock. A second claim is refused unless it passes `--steal`,
which records who it was taken from. Claims never expire on their own. Timeouts and
dead-process checks were both tried, and both guessed wrong.

**Commands always take the epoch id.** clarity never works it out from your current
directory, an environment variable, or there being only one active epoch. That kind of
guess works until a second epoch starts. After that the same command quietly means
something else. With the id in every command, a transcript shows exactly which epoch
each line touched.

## Existing projects

`init` expects an empty or tidy folder. For a workspace that grew before anyone planned
a layout, with checkouts, caches, results and scripts all at the root, use `adopt`:

```sh
cd ~/research
clarity-ctl adopt
```

This sets up clarity and writes `.clarity/adopt.md`, a procedure for an agent to follow.
The agent first asks what the project is for, then proposes where everything goes:

- **`workspace/`**: repos you change, and the scripts, fixtures and virtualenvs around them.
- **`3rd_party/`**: repos you only use, such as a tool, a dependency or a benchmark harness.
- **A baseline epoch**: logs, results and old experiments from work already done,
  created and closed at once.

Each move is proposed in a table, with what it costs to rebuild afterwards, and waits
for your approval. Adopt never guesses what you're working on. Your next epoch is yours
to start.

If the root isn't a git repo, `adopt` makes it one to hold clarity's own files, with
`workspace/`, `3rd_party/` and `EPOCHS/*/LOGS/` ignored. It commits nothing. The
worklog only grows, so commit it whenever you want to keep or share a snapshot.

### A workspace of several repos

When the code lives in checkouts under the root, list the ones you change:

```sh
clarity-ctl repo add workspace/mini-swe-agent
clarity-ctl repo list
```

From then on `epoch start` creates the same branch in every listed repo, with a worktree
at `EPOCHS/NNN_.../wt/<repo>`. A start that fails in one repo undoes what it did in the
others. `close` records each repo's final commit, and `refresh` updates every repo or
none. Repos you only use stay off the list. With nothing listed, epochs branch the root,
so a single-repo project never needs this.

## Design principles

1. **The worklog is the only state.** Generated files are rebuilt from it when their
   inputs change. Anything that can change behind clarity's back — who holds a claim, how
   far a branch trails `main`, what sits in `PLANS/` — is looked up when asked and never
   written down.
2. **Only commands write state.** You can edit `worklog.yaml` by hand, but you lose the
   lock that keeps concurrent writers safe.
3. **Nothing is destructive.** Worktrees are removed, but branches never are.
   Instructions files keep every byte outside clarity's block.
4. **Your notes are yours.** Anything below the marker in `EPOCH.md` survives every
   rewrite.
5. **No model required.** Everything is plain YAML, git and deterministic code.

## Development

```sh
uv venv && uv pip install -e . pytest
.venv/bin/python -m pytest -q tests
```

| Module | Role |
|---|---|
| `model.py` | dataclasses, no I/O |
| `store.py` | YAML, atomic writes, locking |
| `gitops.py` | branches and worktrees |
| `render.py` | generated text |
| `project.py` | the API |
| `cli.py` | parsing and the JSON envelope |
| `agents.py` | the instructions-file block |
| `adopt.py` | the adoption procedure |
| `lease.py` | claims |

## License

MIT — see [LICENSE](LICENSE).
