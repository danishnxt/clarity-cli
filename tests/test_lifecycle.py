"""Smoke tests for the item lifecycle. Run: .venv/bin/python -m pytest -q"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clarity import ClarityError, Project  # noqa: E402
from clarity import agents  # noqa: E402
from clarity.render import EPOCH_MARK_END  # noqa: E402


def git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n")
    git(["init", "-q"], root)
    git(["add", "-A"], root)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], root)
    return root


def test_init_creates_layout(tmp_path):
    root = make_repo(tmp_path)
    project = Project.init(root, name="proj", overall="ship it")

    assert (root / "worklog.yaml").exists()
    assert (root / ".clarity" / "config.yaml").exists()
    assert not (root / "STATUS.md").exists()  # the status view is printed, never written
    for name in ("AGENTS.md", "CLAUDE.md"):  # real files now, carrying our block
        assert not (root / name).is_symlink()
        assert agents.START in (root / name).read_text()
    assert not (root / ".clarity" / "RULES.md").exists()  # the block is the only copy
    assert "EPOCHS/*/wt/" in (root / ".gitignore").read_text()
    assert project.worklog.items == []


def test_idea_to_done(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")

    project = Project.find(root)
    item = project.add("flaky test", type_="fix")
    assert item.status == "idea" and item.folder is None

    project.promote(item.id)
    assert Project.find(root).worklog.by_id(item.id).status == "planned"
    assert (root / project.worklog.by_id(item.id).folder).is_dir()

    project.start(item.id)
    started = project.worklog.by_id(item.id)
    assert started.status == "active"
    assert started.branch == f"epoch/{item.id:03d}-flaky-test"
    assert (root / started.folder / "wt" / root.name).is_dir()

    project.note(item.id, "tried a bigger timeout")
    project.block(item.id, "waiting upstream")
    assert project.worklog.by_id(item.id).blocked_reason == "waiting upstream"
    project.unblock(item.id)

    project.close(item.id, outcome="bumped the timeout")
    closed = project.worklog.by_id(item.id)
    assert closed.status == "done" and closed.outcome == "bumped the timeout"
    assert not (root / closed.folder / "wt" / root.name).exists()  # worktree gone
    branches = subprocess.run(
        ["git", "branch", "--list", closed.branch], cwd=str(root),
        capture_output=True, text=True,
    ).stdout
    assert closed.branch in branches  # branch survives


def test_existing_branch_is_symlinked(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)

    item = project.add("bench baseline", type_="experiment", status="planned")
    project.start(item.id, branch="main")  # main is checked out at the root

    link = root / project.worklog.by_id(item.id).folder / "wt" / root.name
    assert link.is_symlink()
    assert link.resolve() == root.resolve()


def test_human_notes_survive_regeneration(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("write docs", status="planned")

    epoch_md = root / project.worklog.by_id(item.id).folder / "EPOCH.md"
    epoch_md.write_text(epoch_md.read_text() + "my own scratch notes\n")

    project.note(item.id, "started drafting")
    text = epoch_md.read_text()
    assert "my own scratch notes" in text
    assert "started drafting" in text
    assert text.index("started drafting") < text.index(EPOCH_MARK_END)


def test_errors_carry_exit_codes(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)

    try:
        project.worklog.by_id(99)
    except ClarityError as err:
        assert err.code == 4
    else:
        raise AssertionError("expected ClarityError")

    try:
        Project.find(tmp_path)
    except ClarityError as err:
        assert err.code == 3
    else:
        raise AssertionError("expected ClarityError")


def test_plans_live_in_the_epoch_and_are_listed(tmp_path):
    """Long-form docs sit with the work, and are listed when asked for — not cached."""
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")

    folder = root / project.worklog.by_id(item.id).folder
    assert (folder / "PLANS").is_dir()

    (folder / "PLANS" / "approach.md").write_text("# how we'll do it\n")
    project.note(item.id, "ring buffer, 2x slower — PLANS/approach.md")

    # listed at read time, not cached into EPOCH.md: PLANS/ is a directory anyone can
    # drop a file into, so no command can know when it changed
    assert project.query("item", str(item.id))["plans"] == ["approach.md"]
    assert "## Plans" not in (folder / "EPOCH.md").read_text()


def test_reopen_returns_a_closed_epoch_to_active(tmp_path):
    """Reopening keeps the old outcome in the trail instead of overwriting it."""
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)
    project.close(item.id, outcome="2x slower, dropped")

    try:
        project.start(item.id)
    except ClarityError as err:
        assert err.code == 4 and "reopen" in str(err)
    else:
        raise AssertionError("a closed epoch must not restart silently")

    project.start(item.id, reopen=True)
    reopened = project.worklog.by_id(item.id)
    assert reopened.status == "active"
    assert reopened.outcome is None and reopened.ended is None
    assert "previous outcome: 2x slower, dropped" in reopened.notes[-1].text
    assert (root / reopened.folder / "wt" / root.name).exists()  # worktree back


def test_reopen_warns_when_the_branch_is_behind(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)
    project.close(item.id, outcome="parked")

    # main moves on while the epoch is closed
    (root / "later.txt").write_text("more\n")
    git(["add", "-A"], root)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "later"], root)

    _, warnings = project.start(item.id, reopen=True)
    assert any("behind main" in w for w in warnings), warnings


def test_refresh_catches_the_branch_up_without_losing_commits(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)

    # the epoch commits something of its own, then main moves on underneath it
    worktree = project.folder_of(item) / "wt" / root.name
    (worktree / "epoch.txt").write_text("mine\n")
    git(["add", "-A"], worktree)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "epoch work"], worktree)
    (root / "later.txt").write_text("more\n")
    git(["add", "-A"], root)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "later"], root)

    _, message = project.refresh(item.id)
    assert "caught up with main" in message, message
    assert (worktree / "later.txt").exists()       # main's commit arrived
    assert (worktree / "epoch.txt").exists()       # the epoch's own work survived
    assert project.refresh(item.id)[1].endswith("up to date with main")


def test_refresh_refuses_a_dirty_worktree(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)
    (root / "later.txt").write_text("more\n")
    git(["add", "-A"], root)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "later"], root)

    worktree = project.folder_of(item) / "wt" / root.name
    (worktree / "scratch.txt").write_text("uncommitted\n")
    try:
        project.refresh(item.id)
    except ClarityError as exc:
        assert exc.code == 4 and "commit or stash" in str(exc)
    else:
        raise AssertionError("a dirty worktree should refuse to refresh")


def test_closed_seq_orders_same_day_closes(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    first = project.add("first", status="planned")
    second = project.add("second", status="planned")
    project.start(first.id)
    project.start(second.id)

    # closed in the order second, first — same calendar day, so `ended` cannot tell
    project.close(second.id, outcome="b")
    project.close(first.id, outcome="a")

    by_id = {i.id: i for i in project.worklog.items}
    assert by_id[second.id].ended == by_id[first.id].ended   # the date really does tie
    assert by_id[second.id].closed_seq < by_id[first.id].closed_seq

    # the status page reads newest-finished first, so the later close leads
    body = project.status_text(show_all=True)
    closed = body.split("Closed")[1]
    assert closed.index("first") < closed.index("second"), closed


def test_reclosing_after_reopen_takes_a_fresh_seq(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    old = project.add("old", status="planned")
    recent = project.add("recent", status="planned")
    project.start(old.id)
    project.close(old.id, outcome="done once")
    project.start(recent.id)
    project.close(recent.id, outcome="done later")

    was = {i.id: i.closed_seq for i in project.worklog.items}[old.id]
    project.start(old.id, reopen=True)
    project.close(old.id, outcome="done again")

    now = {i.id: i.closed_seq for i in project.worklog.items}[old.id]
    assert now > was                     # re-finishing moves it to the top
    assert now > {i.id: i.closed_seq for i in project.worklog.items}[recent.id]


def test_a_non_conflict_git_failure_is_not_called_a_conflict(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)
    worktree = project.folder_of(item) / "wt" / root.name

    from clarity import gitops
    try:
        gitops.merge_into(worktree, "no-such-branch-xyz")
    except ClarityError as exc:
        assert "conflicts" not in str(exc), f"git's own error was relabelled: {exc}"
    else:
        raise AssertionError("merging a missing ref should fail")
