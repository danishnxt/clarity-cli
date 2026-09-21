"""The Project API. The CLI is a thin shell over this, so there is one implementation."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from . import adopt, agents, gitops, lease, render
from .model import (
    CLOSED,
    DEFAULT_CONFIG,
    IN_FLIGHT,
    NEEDS_FOLDER,
    STATUSES,
    TYPES,
    Item,
    Note,
    now as model_now,
    today,
)
from .store import (
    CONFIG_DIR,
    WORKLOG,
    ClarityError,
    Worklog,
    file_lock,
    load_config,
    save_config,
    write_atomic,
    write_if_changed,
)

EPOCHS = "EPOCHS"
LEARNINGS = "LEARNINGS"
EPOCH_SUBDIRS = ("PLANS", "LOGS", "VIZ", "ANALYSIS")
LOCK_FILE = "worklog.lock"

GITIGNORE_LINES = [
    "# clarity",
    "EPOCHS/*/wt/",
    "EPOCHS/*/.lease",
    ".clarity/cache/",
    ".clarity/worklog.lock",
]


def _inside_worktree(path: Path) -> bool:
    """True for a path under EPOCHS/<epoch>/wt/.

    Once the worklog is committed, every epoch worktree contains a checked-out copy of
    the project's state. Walking up from inside one must reach the real project, not
    the copy — otherwise notes and status changes land in a checkout and vanish on close.
    """
    parts = path.parts
    for index, part in enumerate(parts):
        if part == "wt" and index >= 2 and parts[index - 2] == EPOCHS and index < len(parts) - 1:
            return True
    return False


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.worklog = Worklog.load(root / WORKLOG)
        self.config = load_config(root)

    # ---------- locating and creating ----------

    @staticmethod
    def find(start: Path | None = None) -> "Project":
        here = (start or Path.cwd()).resolve()
        for candidate in [here, *here.parents]:
            if not ((candidate / CONFIG_DIR).is_dir() and (candidate / WORKLOG).exists()):
                continue
            if _inside_worktree(candidate):
                continue  # a checked-out copy of the state, not the project itself
            return Project(candidate)
        raise ClarityError(
            "not a clarity project — no .clarity/ here or above\n"
            "  set one up with: clarity init",
            code=3,
        )

    @staticmethod
    def init(root: Path, name: str | None = None, overall: str | None = None,
             current: str | None = None) -> "Project":
        root = root.resolve()
        if (root / CONFIG_DIR).is_dir() and (root / WORKLOG).exists():
            raise ClarityError(f"{root} is already a clarity project", code=4)

        (root / CONFIG_DIR).mkdir(parents=True, exist_ok=True)
        (root / EPOCHS).mkdir(exist_ok=True)
        (root / LEARNINGS).mkdir(exist_ok=True)

        config = {**DEFAULT_CONFIG, "project": {"name": name or root.name}}
        save_config(root, config)

        worklog = Worklog.empty(root / WORKLOG)
        worklog.objectives.overall = overall
        worklog.objectives.current = current
        worklog.save()

        project = Project(root)
        project.install_agents()
        project.render_views()
        project.ensure_gitignore()
        return project

    @staticmethod
    def adopt(root: Path) -> tuple["Project", Path]:
        """init, plus the procedure for accounting for work that predates it.

        Refuses an existing clarity project for the same reason `init` does: the
        worklog already describes this repo, and a second pass over it would
        propose items for work that is already tracked.
        """
        project = Project.init(root)
        return project, adopt.write_doc(project.root)

    # ---------- paths ----------

    @property
    def epochs_dir(self) -> Path:
        return self.root / EPOCHS

    @property
    def lock_path(self) -> Path:
        return self.root / CONFIG_DIR / LOCK_FILE

    def folder_of(self, item: Item) -> Path | None:
        return self.root / item.folder if item.folder else None

    # ---------- writing ----------

    def _snapshot(self) -> dict:
        """What every generated view is a function of, as plain data.

        Built from the same dicts that get saved, so it cannot disagree with what lands
        on disk — unlike a dirty flag every future mutation would have to remember to set.
        """
        return {
            "objectives": self.worklog.objectives.to_dict(),
            "items": {i.id: i.to_dict() for i in self.worklog.items},
        }

    @contextmanager
    def _write(self):
        """Every mutation runs in here: lock, re-read, change, save, re-render.

        Re-reading under the lock is what makes concurrent writers safe — whatever
        another process committed since this one started is picked up first.
        """
        with file_lock(self.lock_path):
            self.worklog = Worklog.load(self.root / WORKLOG)
            before = self._snapshot()
            yield
            self.worklog.save()
            self.render_views(since=before)

    # ---------- which epoch does a command mean ----------

    @staticmethod
    def as_id(value: int | str) -> int:
        """An id, or a refusal. Never a ValueError escaping to the user as a traceback."""
        text = str(value).strip()
        if not text.isdigit():
            raise ClarityError(f"{value!r} is not an id — ids are numbers, like 7", code=4)
        return int(text)

    def resolve_id(self, explicit: int | str | None = None) -> int:
        """The id the command was given. There is no fallback, on purpose.

        Inferring it — from the cwd, an env var, or "the only active epoch" —
        works right up until a second epoch is active, and then the same command
        means something different with nothing on screen to say so. An agent that
        gets a fresh shell per command cannot see any of that state. So the id is
        always in the command, where a transcript shows which epoch was meant.
        """
        if explicit is not None:
            return self.as_id(explicit)

        targetable = [i for i in self.worklog.items if i.status in IN_FLIGHT]
        if not targetable:
            raise ClarityError(
                "this command needs an epoch id, and no epoch is in flight — "
                "start one with `clarity epoch start <id>`",
                code=4,
            )
        listed = "\n".join(f"  {i.id:>3}  {i.name}" for i in targetable)
        raise ClarityError(
            "this command needs an epoch id — pass one:\n" + listed,
            code=4,
        )

    # ---------- generated output ----------

    def render_views(self, since: dict | None = None) -> None:
        """Rewrite the views the worklog owns — only the ones whose inputs moved.

        `since` is a snapshot taken before the mutation, so only the items whose
        serialized form actually changed get re-rendered. That makes a write O(1)
        rather than O(number of epochs). Passing nothing rebuilds everything.
        """
        now = self._snapshot()

        for item in self.worklog.items:
            if since is not None and since["items"].get(item.id) == now["items"].get(item.id):
                continue
            folder = self.folder_of(item)
            if folder and folder.is_dir():
                path = folder / "EPOCH.md"
                existing = path.read_text(encoding="utf-8") if path.exists() else None
                write_if_changed(path, render.epoch_md(item, existing))

    def install_agents(self, scope: str = "local", remove: bool = False) -> list[tuple[Path, str]]:
        """Put the clarity block in this repo's agent files, or in the user's own.

        Global covers the agents a user actually runs — see agents.global_paths —
        so one install serves every project they open; the block carries its own
        precondition, so it stays quiet in projects that never adopted clarity.
        """
        paths = agents.global_paths() if scope == "global" else agents.local_paths(self.root)
        return agents.install(paths, remove_instead=remove, scope=scope)

    @staticmethod
    def _plans(folder: Path) -> list[str]:
        """Long-form docs living with the work. Listed, never parsed."""
        plans = folder / "PLANS"
        if not plans.is_dir():
            return []
        return sorted(f.name for f in plans.iterdir() if f.is_file() and not f.name.startswith("."))

    def path_of(self, item_id: int | None = None) -> Path:
        """Where the work is. `cd $(clarity path)`."""
        item = self.worklog.by_id(self.resolve_id(item_id))
        folder = self.folder_of(item)
        if not folder or not folder.is_dir():
            raise ClarityError(f"item {item.id} has no folder on disk", code=4)
        return folder

    def ensure_gitignore(self) -> bool:
        """Called at init. Matches whole lines, so `!.clarity/cache/` is not a match."""
        path = self.root / ".gitignore"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = existing.splitlines()
        missing = [l for l in GITIGNORE_LINES if l not in lines]
        if not missing:
            return False
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        write_atomic(path, existing + prefix + "\n".join(missing) + "\n")
        return True

    # ---------- leases ----------

    def leases(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for item in self.worklog.items:
            folder = self.folder_of(item)
            if folder and folder.is_dir():
                held = lease.read(folder)
                if held:  # never auto-expires — a lease stands until someone clears it
                    out[item.id] = {**held, "age": lease.age(held)}
        return out

    def claim(self, item_id: int | None = None, steal: bool = False) -> tuple[Item, dict]:
        item = self.worklog.by_id(self.resolve_id(item_id))
        folder = self.folder_of(item)
        if not folder:
            raise ClarityError(f"item {item.id} has no folder to lease", code=4)
        held = lease.claim(folder, steal=steal, label=item.name)
        return item, held

    def release(self, item_id: int | None = None, force: bool = False) -> Item:
        item = self.worklog.by_id(self.resolve_id(item_id))
        folder = self.folder_of(item)
        if folder:
            lease.release(folder, force=force)
        return item

    # ---------- objectives ----------

    def set_objective(self, text: str, overall: bool = False) -> None:
        # Nothing to regenerate: the objective lives in worklog.yaml and is printed by
        # `clarity status`. No file carries a copy of it.
        with self._write():
            if overall:
                self.worklog.objectives.overall = text
            else:
                self.worklog.objectives.current = text

    # ---------- items ----------

    def add(self, name: str, type_: str = "feature", description: str | None = None,
            status: str = "idea", evidence: str | None = None) -> Item:
        if type_ not in TYPES:
            raise ClarityError(f"unknown type {type_!r}; one of {', '.join(TYPES)}", code=4)
        if status not in STATUSES:
            raise ClarityError(f"unknown status {status!r}", code=4)
        with self._write():
            item = Item(
                id=self.worklog.next_id(),
                name=name,
                type=type_,
                status=status,
                description=description,
                evidence=evidence,
            )
            self.worklog.items.append(item)
            if status in NEEDS_FOLDER:
                self._make_folder(item)
        return item

    def _make_folder(self, item: Item) -> Path:
        if item.folder and (self.root / item.folder).is_dir():
            return self.root / item.folder
        folder = self.epochs_dir / item.folder_name
        for sub in EPOCH_SUBDIRS:
            (folder / sub).mkdir(parents=True, exist_ok=True)
        item.folder = str(folder.relative_to(self.root))
        return folder

    def promote(self, item_id: int) -> Item:
        """idea -> planned: it gets a folder, but no branch and no worktree yet."""
        with self._write():
            item = self.worklog.by_id(item_id)
            if item.status != "idea":
                raise ClarityError(f"item {item_id} is {item.status}, not an idea", code=4)
            item.status = "planned"
            self._make_folder(item)
        return item

    def start(self, item_id: int, branch: str | None = None,
              take_lease: bool = True, reopen: bool = False) -> tuple[Item, list[str]]:
        """planned -> active: resolve a branch to a worktree or a symlink, then lease it.

        A closed epoch needs `reopen`, so picking work back up is deliberate rather
        than a typo that quietly reanimates something you finished last month.
        """
        warnings: list[str] = []
        with self._write():
            item = self.worklog.by_id(item_id)
            closed = item.status in CLOSED
            if closed and not reopen:
                raise ClarityError(
                    f"item {item_id} is {item.status} — pick it back up with:\n"
                    f"  clarity epoch reopen {item_id}",
                    code=4,
                )
            if closed:
                # the outcome was true when it was written; keep it in the trail
                if item.outcome:
                    item.notes.append(
                        Note(at=today(), text=f"reopened — previous outcome: {item.outcome}")
                    )
                item.outcome = None
                item.ended = None
            folder = self._make_folder(item)
            # Claim before anything else: _write() saves only if the body succeeds, so a
            # refused lease leaves no half-started epoch and no worktree we may not use.
            if take_lease:
                lease.claim(folder, label=item.name)
            if gitops.is_repo(self.root):
                item.branch, warnings = self._attach_worktree(item, folder, branch)
            item.status = "active"
            item.started = item.started or today()
        return item, warnings

    def _attach_worktree(self, item: Item, folder: Path,
                         branch: str | None) -> tuple[str, list[str]]:
        warnings: list[str] = []
        name = branch or f"epoch/{item.id:03d}-{item.slug}"
        link = folder / "wt" / self.root.name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            # Already attached. Moving it would mean tearing down a checkout someone may
            # be sitting in, so refuse rather than quietly record a branch that isn't
            # the one on disk — which is what this used to do.
            if branch and branch != item.branch:
                raise ClarityError(
                    f"epoch {item.id} is already checked out at {link}\n"
                    f"  on {item.branch or 'an unrecorded branch'}, not {branch}\n"
                    f"  close the epoch, or remove that worktree yourself, to move it",
                    code=4,
                )
            return item.branch or name, warnings

        exists = gitops.branch_exists(self.root, name)
        if exists:
            warnings += self._staleness(name)
        checked_out = gitops.worktree_path_for(self.root, name) if exists else None
        if checked_out:
            # already checked out somewhere (often the main checkout) — point at it
            link.symlink_to(os.path.relpath(checked_out, link.parent))
            borrowers = [
                i.id for i in self.worklog.items
                if i.id != item.id and i.status in IN_FLIGHT and i.branch == name
            ]
            if borrowers:
                warnings.append(
                    f"epoch(s) {', '.join(map(str, borrowers))} are already working "
                    f"{name} in the same checkout — edits will collide"
                )
        else:
            # a worktree can be added from a dirty tree; git carries nothing across
            gitops.add_worktree(self.root, link, name, create=not exists)
        return name, warnings

    def refresh(self, item_id: int | None = None) -> tuple[Item, str]:
        """Bring the base branch into an in-flight epoch's branch.

        Merges rather than recreating: the branch keeps its identity and its commits,
        so a refresh can never lose work. Deleting branches is a separate question.
        """
        item = self.worklog.by_id(self.resolve_id(item_id))
        if item.status not in IN_FLIGHT:
            raise ClarityError(
                f"epoch {item.id} is {item.status} — only an in-flight epoch refreshes",
                code=4,
            )
        if not gitops.is_repo(self.root):
            raise ClarityError("not a git repo — nothing to refresh from", code=4)
        base = gitops.default_branch(self.root)
        if not base:
            raise ClarityError("no main or master branch to refresh from", code=4)
        if not item.branch or item.branch == base:
            raise ClarityError(f"epoch {item.id} is on {base} already", code=4)

        worktree = gitops.worktree_path_for(self.root, item.branch)
        if not worktree:
            raise ClarityError(f"{item.branch} is not checked out anywhere", code=4)
        if worktree == self.root:
            # a shared checkout: merging here would move the tree under everyone else
            raise ClarityError(
                f"{item.branch} shares the main checkout — refresh it there yourself",
                code=4,
            )
        dirty = gitops.is_dirty(worktree)
        if dirty:
            raise ClarityError(
                f"{len(dirty)} uncommitted change(s) in {worktree} — commit or stash first",
                code=4,
            )

        _, behind = gitops.ahead_behind(self.root, item.branch, base)
        if not behind:
            return item, f"epoch {item.id} is already up to date with {base}"
        # Take the lease before moving anyone's tree. claim() already does the right
        # thing: free, stale or ours goes through, someone else's live one refuses.
        folder = self.folder_of(item)
        if folder:
            lease.claim(folder, label=item.name)
        gitops.merge_into(worktree, base)
        return item, (f"epoch {item.id}: {item.branch} caught up with {base} "
                      f"({behind} commit{'s' if behind != 1 else ''})")

    def _behind(self, branch: str) -> tuple[str, int, int] | None:
        """(base, ahead, behind) when `branch` trails its base, else None."""
        base = gitops.default_branch(self.root)
        if not base or base == branch:
            return None
        try:
            ahead, behind = gitops.ahead_behind(self.root, branch, base)
        except ClarityError:
            return None
        return (base, ahead, behind) if behind else None

    def _staleness(self, branch: str) -> list[str]:
        """Reopening gives you the branch as you left it — say so before work resumes."""
        found = self._behind(branch)
        if not found:
            return []
        base, ahead, behind = found
        note = f"{branch} is {behind} commit{'s' if behind != 1 else ''} behind {base}"
        if ahead:
            note += f" and {ahead} ahead"
        return [note]

    def stale_map(self) -> dict[int, str]:
        """Short 'behind' notes for in-flight epochs, for the status page.

        The start-time warning fires once, when the worktree is created; main moves on
        afterwards and nothing said so. This is what keeps saying it.
        """
        if not gitops.is_repo(self.root):
            return {}
        out: dict[int, str] = {}
        for item in self.worklog.items:
            if item.status not in IN_FLIGHT or not item.branch:
                continue
            found = self._behind(item.branch)
            if found:
                base, _, behind = found
                out[item.id] = f"{behind} behind {base}"
        return out

    def note(self, item_id: int | None, text: str) -> Item:
        with self._write():
            item = self.worklog.by_id(self.resolve_id(item_id))
            item.notes.append(Note(at=model_now(), text=text))
        return item

    def block(self, item_id: int | None, reason: str) -> Item:
        with self._write():
            item = self.worklog.by_id(self.resolve_id(item_id))
            item.status = "blocked"
            item.blocked_reason = reason
        return item

    def unblock(self, item_id: int | None) -> Item:
        with self._write():
            item = self.worklog.by_id(self.resolve_id(item_id))
            if item.status != "blocked":
                raise ClarityError(f"item {item.id} is {item.status}, not blocked", code=4)
            item.status = "active"
            item.blocked_reason = None
        return item

    def close(self, item_id: int | None, outcome: str, abandoned: bool = False) -> Item:
        with self._write():
            item = self.worklog.by_id(self.resolve_id(item_id))
            if item.status in CLOSED:
                raise ClarityError(f"item {item.id} is already {item.status}", code=4)
            folder = self.folder_of(item)
            if folder:
                worktree = folder / "wt" / self.root.name
                if worktree.is_symlink():
                    worktree.unlink()  # we only borrowed the path
                elif worktree.exists():
                    gitops.remove_worktree(self.root, worktree)  # branch survives
                lease.release(folder, force=True)
            if item.branch:
                # the epoch's own branch, not whatever the main checkout has checked
                # out — the work being closed lives on the former
                item.extra["end_sha"] = (gitops.branch_sha(self.root, item.branch)
                                         or item.extra.get("end_sha"))
            item.status = "abandoned" if abandoned else "done"
            item.outcome = outcome
            item.ended = today()
            # Derived, not a stored counter: a counter is a second source of truth that
            # can drift from the items it numbers. Safe under _write()'s lock.
            item.closed_seq = max(
                (i.closed_seq or 0 for i in self.worklog.items), default=0
            ) + 1
        return item

    # ---------- reading ----------

    def query(self, name: str, arg: str | None = None) -> dict:
        from .model import FUTURE

        views = {
            "active": IN_FLIGHT,
            "future": FUTURE,
            # what `clarity status` prints: both groups, so the two surfaces agree
            "current": IN_FLIGHT | FUTURE,
            "inactive": CLOSED,
            "all": set(STATUSES),
        }
        if name == "item":
            item = self.worklog.by_id(self.resolve_id(arg))
            data = item.to_dict()
            held = self.leases().get(item.id)
            if held:
                data["lease"] = held
            folder = self.folder_of(item)
            if folder and folder.is_dir():
                plans = self._plans(folder)
                if plans:
                    data["plans"] = plans
            return data
        if name == "objectives":
            return self.worklog.objectives.to_dict()
        if name == "leases":
            return {"leases": self.leases()}
        if name not in views:
            raise ClarityError(
                f"unknown query {name!r}; try: "
                f"{', '.join([*views, 'item', 'objectives', 'leases'])}",
                code=4,
            )
        return {"items": [i.to_dict() for i in self.worklog.with_status(views[name])]}

    def status_text(self, show_all: bool = False) -> str:
        return render.status_text(
            self.worklog.objectives, self.worklog.items, show_all, self.leases(),
            is_repo=gitops.is_repo(self.root), stale=self.stale_map(),
        )
