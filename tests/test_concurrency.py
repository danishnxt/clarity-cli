"""Locks, leases, and working out which epoch a command means."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clarity import ClarityError, Project  # noqa: E402
from clarity import lease  # noqa: E402

from test_lifecycle import make_repo  # noqa: E402


def two_epochs(root: Path) -> tuple[Project, list[int]]:
    project = Project.find(root)
    first = project.add("cache warmup", type_="experiment", status="planned")
    second = project.add("lto build", type_="experiment", status="planned")
    project.start(first.id)
    project.start(second.id)
    return Project.find(root), [first.id, second.id]


def test_write_lock_keeps_concurrent_adds(tmp_path):
    """Five processes adding at once: none may overwrite another's item."""
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def add(n: int):
        return subprocess.run(
            [sys.executable, "-m", "clarity.cli", "idea", "add", f"idea {n}"],
            cwd=str(root), env=env, capture_output=True, text=True,
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(add, range(5)))

    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    items = Project.find(root).worklog.items
    assert len(items) == 5
    assert len({i.id for i in items}) == 5  # no duplicate ids either


def test_lease_refuses_a_second_holder(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    folder = root / item.folder

    # someone else's live lease
    lease.path_for(folder).write_text(yaml.safe_dump({
        "owner": "someone", "host": "otherbox",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }))

    with pytest.raises(ClarityError) as err:
        project.claim(item.id)
    assert err.value.code == 4
    assert "someone@otherbox" in str(err.value)

    _, held = project.claim(item.id, steal=True)
    assert "someone@otherbox" in held["stolen_from"]
    assert lease.held_by_us(lease.read(folder))


def test_an_old_lease_still_stands(tmp_path):
    """Nothing expires on its own — a years-old lease still refuses, and says how old."""
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    folder = root / item.folder

    lease.path_for(folder).write_text(yaml.safe_dump({
        "owner": "ghost", "host": "otherbox", "at": "2020-01-01T00:00:00+00:00",
    }))

    assert project.leases()[item.id]["age"].endswith("d ago")  # shown, not enforced

    with pytest.raises(ClarityError) as err:
        project.claim(item.id)
    assert err.value.code == 4
    assert "--steal" in str(err.value)

    project.claim(item.id, steal=True)
    assert lease.held_by_us(lease.read(folder))


def test_close_releases_the_lease(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)
    assert lease.read(root / item.folder) is not None

    project.close(item.id, outcome="done with it")
    assert lease.read(root / item.folder) is None


def test_cwd_decides_which_epoch(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project, ids = two_epochs(root)

    with pytest.raises(ClarityError) as err:
        project.resolve_id()  # two active, nothing to disambiguate
    assert err.value.code == 4
    assert "several epochs" in str(err.value)

    monkeypatch.chdir(root / project.worklog.by_id(ids[1]).folder / "wt" / root.name)
    assert Project.find(root).resolve_id() == ids[1]

    monkeypatch.chdir(root / project.worklog.by_id(ids[0]).folder / "LOGS")
    assert Project.find(root).resolve_id() == ids[0]


def test_env_var_decides_when_cwd_is_neutral(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project, ids = two_epochs(root)

    monkeypatch.chdir(root)
    monkeypatch.setenv("CLARITY_EPOCH", str(ids[1]))
    assert Project.find(root).resolve_id() == ids[1]

    exports = Project.find(root).env_exports(ids[0])
    assert exports["CLARITY_EPOCH"] == str(ids[0])
    assert exports["CLARITY_EPOCH_DIR"].endswith(project.worklog.by_id(ids[0]).folder)


def test_active_link_only_when_unambiguous(tmp_path):
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    link = root / "EPOCHS" / ".active"

    project = Project.find(root)
    first = project.add("cache warmup", status="planned")
    project.start(first.id)
    assert link.is_symlink()

    second = project.add("lto build", status="planned")
    project.start(second.id)
    assert not link.exists()  # two active: pointing at either one would be a lie

    project.close(second.id, outcome="dropped", abandoned=True)
    assert link.is_symlink()
    assert link.resolve() == (root / Project.find(root).worklog.by_id(first.id).folder)


def test_our_own_lease_is_not_a_refusal(tmp_path, monkeypatch):
    """Identity is the declared name, not a process — the same agent reclaims freely."""
    monkeypatch.setenv("CLARITY_AGENT", "agent-alpha")
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    folder = root / item.folder

    project.claim(item.id)                       # one command claims it
    assert lease.read(folder)["owner"] == "agent-alpha"
    project.claim(item.id)                       # a later command, a new process
    assert lease.held_by_us(lease.read(folder))

    monkeypatch.setenv("CLARITY_AGENT", "agent-beta")
    with pytest.raises(ClarityError) as err:     # a different agent is refused
        project.claim(item.id)
    assert err.value.code == 4 and "agent-alpha" in str(err.value)


def test_find_skips_a_worktree_copy_of_the_state(tmp_path, monkeypatch):
    """Inside an epoch worktree, commands must reach the real project, not the copy."""
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    project.start(item.id)

    worktree = root / project.worklog.by_id(item.id).folder / "wt" / root.name
    # simulate the state having been committed: the worktree now holds its own copy
    (worktree / ".clarity").mkdir(parents=True, exist_ok=True)
    (worktree / "worklog.yaml").write_text("version: 1\nobjectives: {}\nitems: []\n")

    monkeypatch.chdir(worktree)
    found = Project.find()
    assert found.root == root
    assert found.resolve_id() == item.id


def test_a_refused_lease_leaves_nothing_half_started(tmp_path):
    """The claim happens inside the write lock, so a loser writes no state at all."""
    root = make_repo(tmp_path)
    Project.init(root, name="proj")
    project = Project.find(root)
    item = project.add("cache warmup", status="planned")
    folder = root / item.folder

    lease.path_for(folder).write_text(yaml.safe_dump({
        "owner": "someone", "host": "otherbox",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }))

    with pytest.raises(ClarityError) as err:
        project.start(item.id)
    assert err.value.code == 4

    fresh = Project.find(root).worklog.by_id(item.id)
    assert fresh.status == "planned"          # not active
    assert fresh.started is None
    assert not (folder / "wt").exists()       # and no worktree it may not use
