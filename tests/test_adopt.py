"""Adopting a project that already has work in it.

Adopt ships a document, not an algorithm, so most of what could go wrong is in
the seams around it: does the file land, does the agent block point at it, does
deleting it actually end adoption, and does the procedure it describes still
match the commands the CLI offers.
Run: .venv/bin/python -m pytest -q
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clarity import Project, adopt, agents  # noqa: E402
from clarity.store import ClarityError  # noqa: E402

from test_lifecycle import make_repo  # noqa: E402


def test_adopt_sets_up_and_leaves_the_procedure(tmp_path):
    root = make_repo(tmp_path)
    project, doc = Project.adopt(root)

    assert (root / "worklog.yaml").exists()
    assert doc == root / ".clarity" / "adopt.md"
    assert doc.exists()
    assert project.worklog.items == []  # adopt proposes nothing by itself


def test_adopt_refuses_an_existing_project(tmp_path):
    """Re-running would propose items for work the worklog already tracks."""
    root = make_repo(tmp_path)
    Project.init(root)

    try:
        Project.adopt(root)
    except ClarityError as exc:
        assert exc.code == 4
    else:
        raise AssertionError("adopting twice should refuse")


def test_pending_is_the_files_presence_and_nothing_else(tmp_path):
    """There is no flag in the worklog: deleting the file is what finishes adoption."""
    root = make_repo(tmp_path)
    Project.adopt(root)
    assert adopt.pending(root)

    adopt.doc_path(root).unlink()
    assert not adopt.pending(root)
    assert Project.find(root).worklog.items == []  # nothing about it was written down


def test_init_alone_leaves_no_procedure(tmp_path):
    """A project with no history to account for should not be told to account for one."""
    root = make_repo(tmp_path)
    Project.init(root)
    assert not adopt.pending(root)


def test_the_agent_block_points_at_the_document(tmp_path):
    root = make_repo(tmp_path)
    Project.adopt(root)

    block = (root / "AGENTS.md").read_text()
    assert ".clarity/adopt.md" in block
    # Both blocks carry it: a global install lands where adoption has not happened yet.
    assert ".clarity/adopt.md" in agents.GLOBAL_BLOCK


def test_the_block_stays_short():
    """It loads every turn of every session. Adopt is allowed one line, not a section."""
    mentions = [line for line in agents.LOCAL_BLOCK.splitlines() if "adopt.md" in line]
    assert len(mentions) == 1


def test_the_procedure_only_names_commands_that_exist():
    """The document is the product. A command it invents is a bug in the product."""
    from clarity.cli import build_parser

    parser = build_parser()
    commands = set(parser._subparsers._group_actions[0].choices)

    # Only invocations: inside backticks, or in an indented code block.
    named = set(re.findall(r"`clarity ([a-z]+)", adopt.ADOPT_MD))
    named |= set(re.findall(r"^ {4}clarity ([a-z]+)", adopt.ADOPT_MD, re.M))
    assert named, "extracted no commands — the test stopped testing anything"
    assert named <= commands, f"adopt.md names commands that do not exist: {named - commands}"


# The rest of this file guards the scope of adoption. It is a tidying job with
# two destinations: working material to workspace/, existing records to a
# baseline epoch that is born closed. An earlier draft inventoried branches and
# TODO files and proposed epochs for in-flight work; that was cut, and these
# keep it cut.


def test_adoption_does_not_inventory_in_flight_work():
    """A fresh worklog full of guessed items costs more to clean up than it saves."""
    assert "Do not inventory the project's in-flight work" in adopt.ADOPT_MD
    assert "Do not add any other items on your way out" in adopt.ADOPT_MD


def test_the_baseline_epoch_is_created_closed():
    """The one item adoption creates describes work already done, so it starts done."""
    assert "clarity epoch new" in adopt.ADOPT_MD
    assert "clarity epoch close" in adopt.ADOPT_MD
    close_at = adopt.ADOPT_MD.index("clarity epoch close")
    new_at = adopt.ADOPT_MD.index("clarity epoch new")
    assert new_at < close_at, "close must follow new — the epoch is never left in flight"
    assert "Do not create an empty epoch" in adopt.ADOPT_MD


def test_both_destinations_are_named():
    assert "`workspace/`" in adopt.ADOPT_MD
    assert "baseline epoch" in adopt.ADOPT_MD
    # The bucket was renamed src/ -> workspace/: in a workspace holding a checkout
    # that has its own src/, "src/duckdb/src/" is ambiguous. The document may still
    # mention a project's own src/ — what it may not do is send anything there.
    assert "under `src/`" not in adopt.ADOPT_MD
    assert "`src/" not in adopt.ADOPT_MD.replace("A `src/` or `lib/`", "")
    for own in ("worklog.yaml", "EPOCHS/", "LEARNINGS/", ".clarity/", "AGENTS.md"):
        assert own in adopt.ADOPT_MD, f"{own} is not listed as staying at the root"


def _step(n: int) -> str:
    doc = adopt.ADOPT_MD
    start = doc.index(f"## {n} ")
    return doc[start:doc.index(f"## {n + 1} ", start)]


def test_the_goal_is_asked_before_anything_is_listed():
    """It decides which repos go to workspace/ — so it comes before the first `ls`."""
    first = _step(0)
    assert "What are you trying to do in this project?" in first
    assert "clarity objective set --overall" in first
    assert "ls -a" not in first
    assert adopt.ADOPT_MD.index("What are you trying to do") < adopt.ADOPT_MD.index("ls -a")


def test_layout_instructions_are_read_before_anything_is_proposed():
    """A move against a written 'do not move X' costs a rebuild to undo."""
    reading = _step(1)
    assert "*PLAN*.md" in reading and "README.md" in reading
    assert adopt.ADOPT_MD.index("## 1 ") < adopt.ADOPT_MD.index("## 2 ")


def test_repos_split_by_whether_the_project_changes_them():
    """Repos you change get branched; repos you only use must never be."""
    assert "`3rd_party/`" in adopt.ADOPT_MD
    assert "one question" in _step(2).lower()
    assert "clarity repo add workspace/" in adopt.ADOPT_MD
    assert "3rd_party/` stay off the list" in adopt.ADOPT_MD


def test_whole_directories_move_together():
    """Lifting runs/ out of a results dir silently breaks the script that reads it."""
    assert "never reach inside one" in adopt.ADOPT_MD
    assert "do not guess" in adopt.ADOPT_MD.lower()


def test_every_file_clarity_owns_is_named_as_staying(tmp_path):
    """If install starts writing a new agent file, adopt must not sweep it into src/."""
    from clarity import agents

    root = make_repo(tmp_path)
    for path in agents.local_paths(root):
        assert path.name in adopt.ADOPT_MD, \
            f"{path.name} is written by install but adopt.md does not list it as clarity's"


def test_moves_are_proposed_before_anything_happens():
    assert "Then stop." in adopt.ADOPT_MD
    assert "any row can be struck" in adopt.ADOPT_MD


def test_move_costs_are_priced_not_forbidden():
    """A build tree is an artifact: moving it costs a rebuild, it does not destroy work.
    Saying 'this breaks the build' leaves a 10G checkout at the root forever."""
    assert "Cost of moving it" in adopt.ADOPT_MD
    assert "the source is untouched" in adopt.ADOPT_MD
    assert "You are pricing them" in adopt.ADOPT_MD


def test_the_things_that_carry_absolute_paths_are_all_named():
    for artifact in ("pyvenv.cfg", "CMakeCache.txt", ".uv-cache", "symlink"):
        assert artifact in adopt.ADOPT_MD


def test_renames_keep_history():
    assert "git mv" in adopt.ADOPT_MD


def test_a_nested_checkout_moves_whole():
    """Its .git travels with it. Opening it or merging it up is out of scope."""
    assert "moves as a whole" in adopt.ADOPT_MD


def test_an_already_tidy_project_is_a_valid_outcome():
    assert "already tidy is a valid outcome" in adopt.ADOPT_MD


def test_objectives_are_asked_for_not_inferred():
    first = _step(0)
    assert "Ask the human; do not infer it from the code" in first
