"""Reading and writing the two YAML files. Atomic, quoted, order-preserving."""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from .model import DEFAULT_CONFIG, Item, Objectives

WORKLOG = "worklog.yaml"
CONFIG_DIR = ".clarity"
CONFIG = "config.yaml"


class ClarityError(Exception):
    """Refused or invalid — carries the exit code the CLI should use."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def write_atomic(path: Path, text: str) -> None:
    """Write via temp file + rename, so an interrupted write can't truncate state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_if_changed(path: Path, text: str) -> bool:
    """write_atomic, but only when the bytes would differ. True if it wrote.

    Rendering costs ~1us and the atomic write ~130us, so comparing first is most of
    the saving. It also stops a no-op rewrite from bumping mtime, which is what keeps
    `git status` quiet and makes watching mtimes viable.
    """
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeDecodeError):
            pass  # unreadable — rewrite it
    write_atomic(path, text)
    return True


@contextmanager
def file_lock(path: Path, timeout: float = 10.0):
    """Exclusive lock held across a read-modify-write, so two writers can't lose an update.

    Advisory (flock), which is enough: every writer is a clarity process and takes it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - started > timeout:
                    raise ClarityError(
                        "another clarity process is writing (lock timed out)", code=4
                    )
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ClarityError(f"{path.name}: expected a mapping at the top level", code=2)
    return data


class Worklog:
    """objectives + every item, idea through archived."""

    def __init__(self, path: Path, objectives: Objectives, items: list[Item], extra: dict):
        self.path = path
        self.objectives = objectives
        self.items = items
        self.extra = extra

    @classmethod
    def load(cls, path: Path) -> "Worklog":
        raw = load_yaml(path)
        items = [Item.from_dict(d) for d in (raw.get("items") or [])]
        extra = {k: v for k, v in raw.items() if k not in ("version", "objectives", "items")}
        return cls(path, Objectives.from_dict(raw.get("objectives")), items, extra)

    @classmethod
    def empty(cls, path: Path) -> "Worklog":
        return cls(path, Objectives(), [], {})

    def save(self) -> None:
        data = {
            "version": 1,
            "objectives": self.objectives.to_dict(),
            "items": [i.to_dict() for i in self.items],
        }
        data.update(self.extra)
        write_atomic(self.path, _dump(data))

    def by_id(self, item_id: int) -> Item:
        for item in self.items:
            if item.id == item_id:
                return item
        raise ClarityError(f"no item with id {item_id}", code=4)

    def next_id(self) -> int:
        return max((i.id for i in self.items), default=0) + 1

    def with_status(self, statuses) -> list[Item]:
        return [i for i in self.items if i.status in statuses]


def load_config(root: Path) -> dict:
    """Project config, with defaults filled in for anything absent."""
    raw = load_yaml(root / CONFIG_DIR / CONFIG)
    config = {**DEFAULT_CONFIG}
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    return config


def save_config(root: Path, config: dict) -> None:
    write_atomic(root / CONFIG_DIR / CONFIG, _dump(config))
