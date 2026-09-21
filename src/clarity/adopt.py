"""Bringing a project that already has files in it into clarity.

`init` assumes an empty or tidy folder. `adopt` is for the other case: a
workspace that grew before anyone thought about layout, with checkouts, caches,
results and scripts all sitting at the root. Clarity wants that root for its own
scaffolding, so adoption is a tidying job — everything that is not clarity's
moves under `src/`, and the root is left holding `.clarity/`, `EPOCHS/`,
`LEARNINGS/`, `worklog.yaml` and the agent instructions files.

What adopt deliberately does *not* do is inventory the work. An earlier design
had it reading branches and TODO files and proposing epochs; that is a separate
and much harder job, and getting it wrong fills a fresh worklog with items
someone then has to close. The worklog comes out empty on purpose. Whoever
adopted the project knows what they are working on and can say so in a line.

The moves themselves are proposed, never performed unattended. Moving a
directory can break it — a virtualenv's scripts carry absolute shebangs, a
CMake build tree has absolute paths baked into its cache — and only the human
knows which of those they still need. So the agent brings a table and waits.

`adopt` therefore initialises the project and writes `.clarity/adopt.md`: the
procedure an agent follows to propose that layout. It deletes the file when it
is done, and the file's absence is what "adoption finished" means — there is no
flag to set and nothing to keep in sync.

That also decides where the pointer lives. The block in AGENTS.md loads on
every turn of every session, so a paragraph about a one-time operation would be
paid for forever. It gets one line instead, conditional on the file existing,
which costs nothing once the file is gone.
"""

from __future__ import annotations

from pathlib import Path

from .store import CONFIG_DIR, write_atomic

DOC = "adopt.md"

ADOPT_MD = """# Adopting clarity in this project

Someone ran `clarity adopt` here. The scaffolding exists now, and this project
already had files in it before clarity arrived.

Your job is to tidy the layout, and only that: **everything that is not
clarity's moves under `src/`**, so the root is left holding clarity's own
files. Then delete this file, which is what marks adoption finished.

**Do not create any epochs or ideas.** The worklog stays empty. Reading a
project's branches and TODO files and guessing what its work is takes judgement
you do not have yet, and a fresh worklog full of wrong items costs more to clean
up than it saves. Whoever adopted this project will add their own work
afterwards, in a line each.

## 1 · See what is at the root

`ls -a` in the project root, and nothing deeper yet. Everything there falls into
one of two groups.

**Clarity's own — these stay at the root:**

    .clarity/      EPOCHS/      LEARNINGS/      worklog.yaml
    AGENTS.md      CLAUDE.md    GEMINI.md       QWEN.md       WARP.md
    .windsurfrules      .github/copilot-instructions.md
    .git/          .gitignore

**Everything else moves under `src/`.** Source directories, checkouts,
notebooks, scripts, data, results, virtualenvs, caches, loose files — the rule
is simple on purpose, so that the layout after adoption is predictable rather
than a matter of taste.

Then check each candidate for the one thing a move does not carry with it:
absolute paths baked into generated files.

Nothing in this table is source. Every one of them is an artifact built from
something else, so a move costs time to regenerate, not work. Say that — a
human told "this breaks the build" leaves a 10G checkout at the root forever,
and the same human told "re-run cmake, about twenty minutes" just says yes.

| Look for | Tells you | Cost of moving it |
|---|---|---|
| `pyvenv.cfg`, `bin/python` | a virtualenv | shebangs point at the old path — delete and recreate it, seconds |
| `CMakeCache.txt`, `build/`, `_build/` | a configured build tree | the cache holds absolute source and binary dirs — re-run cmake and rebuild; the source is untouched |
| `.uv-cache`, `.cache`, `node_modules/.cache` | a tool cache | entries key on absolute paths — delete it, it repopulates |
| a symlink pointing outside the project | a link whose target may move | repoint it after, or leave it |

Estimate the cost where you can: a build tree's size, or how long its last build
took if a log says. "Rebuild, roughly twenty minutes" is a decision the human
can make. "Moving breaks the build" is not — it only sounds like one.

You are not deciding these. You are pricing them, so the human can.

## 2 · Propose the layout

Print one table. A row per entry at the root, in this shape:

| Now | After | Note |
|---|---|---|
| `duckdb/` | `src/duckdb/` | 10G checkout with a configured build tree — needs `cmake` + rebuild after |
| `notes/` | `src/notes/` | plain markdown, nothing to redo |
| `.venv/` | `src/.venv/` | virtualenv — delete and recreate after, seconds |
| `EPOCHS/` | — | clarity's |

Put the rows that cost something first, each with what it costs. Then stop.

Ask for approval of the table as a whole, and say that any row can be struck
out. Leaving a big checkout exactly where it is is a normal answer, not a
failure of the plan — but make sure it is being left because the human wants it
there, not because you made a rebuild sound like a catastrophe.

If nothing at the root needs moving, say so and go to step 4. A project that is
already tidy is a valid outcome.

## 3 · Move, once approved

Move only the rows that survived. Two rules:

- **Use `git mv` for anything git tracks**, and a plain `mv` for everything
  else. `git mv` records the rename, so history follows the file instead of
  showing a delete and an add.
- **One entry at a time, checking as you go.** If a move fails, stop and report
  rather than continuing — a half-moved tree is worse than an untidy one.

A nested checkout moves as a whole: its `.git` travels with it and its history
is untouched. Do not open it, and do not try to merge it into the outer repo.

Afterwards, confirm nothing was left behind: `ls -a` the root again and check it
against your table.

## 4 · Set the objectives

These are the two lines `clarity status` leads with, and the first thing every
future session reads:

    clarity objective set --overall "what this project is for"
    clarity objective set "what is being worked on now"

Ask the human for both. Do not infer them from the code — you have only just
rearranged the folders, which tells you nothing about intent. If they would
rather fill them in later, leave them unset and say so.

## 5 · Finish

    clarity status          # objectives set, no items — that is correct here
    rm .clarity/adopt.md    # adoption is over; this file is the only marker

Then tell the human three things: what moved, what you left alone and why, and
what now needs regenerating — the builds to re-run, the virtualenvs to recreate,
the caches that will refill themselves. Give them the commands.

Do not add items on your way out. `clarity idea add "..."` is theirs to run.
"""


def doc_path(root: Path) -> Path:
    return root / CONFIG_DIR / DOC


def pending(root: Path) -> bool:
    """True while adoption is unfinished. The file's absence is the only marker."""
    return doc_path(root).exists()


def write_doc(root: Path) -> Path:
    path = doc_path(root)
    write_atomic(path, ADOPT_MD)
    return path
