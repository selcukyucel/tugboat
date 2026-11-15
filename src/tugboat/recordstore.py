"""The local store of task records, inside an app repo.

A record is written here first, and published later, after the developer
confirms. The store is the folder `.tugboat/records/`. Git ignores it, and
the check before each action keeps an agent's edit tools away from it.

The format is the published one: one JSON file for one record, at the path
it will have on the `records` branch of the team repo. So one validator
serves both, and publishing is a copy.

    .tugboat/records/jira/SHOP-123/20261115T101530Z-3f9a1c2b7d4e.json
    .tugboat/records/github-projects/acme/shop-ios/311/20261115T101530Z-3f9a1c2b7d4e.json

Three rules:

- A record is written once. A second write to the same path is refused,
  whatever it holds: a record is never edited.
- A write is atomic. The record is written to a temporary file beside its
  final place, flushed to disk, and then given its name in one step. A
  crash before that step leaves a temporary file that no reader looks at.
  A crash after it leaves the whole record.
- A reader never guesses. A file that cannot be read, that fails its
  schema, that a newer tool wrote, or that is not at the path its own
  content gives, is reported and left out.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo, records
from tugboat.errors import DataError, TugError
from tugboat.strict_json import pretty_json, read_json_file

FOLDER = "records"
STORE = f"{apprepo.LOCAL_FOLDER}/{FOLDER}"
SUFFIX = ".json"
TEMPORARY_PREFIX = ".writing-"
MAX_RECORD_BYTES = 512 * 1024
MAX_FILES = 20_000  # files looked at in one read of the store
STALE_AFTER = 3600.0  # seconds after which a temporary file is what a crash left behind


class Stored(NamedTuple):
    path: str  # relative to the store, and to the `records` branch
    record: dict[str, Any]


class Unreadable(NamedTuple):
    path: str
    reason: str
    newer: bool  # a newer tool wrote it: updating `tug` reads it


class Listing(NamedTuple):
    records: tuple[Stored, ...]  # oldest first, by their own time
    unreadable: tuple[Unreadable, ...]
    cut_short: bool = False  # the store holds more files than one read looks at


def folder(repo: Path) -> Path:
    return apprepo.local_folder(repo) / FOLDER


def task_path(task: Mapping[str, Any]) -> str:
    """The folder of one story's records. Every record of the story is in it, whichever app repo it is about."""
    tracker, key = task["tracker"], task["key"]
    if tracker == "jira":
        return f"jira/{key}"
    repo, _, number = key.partition("#")
    return f"{tracker}/{repo}/{number}"


def record_path(record: Mapping[str, Any]) -> str:
    """Where a checked record lives, relative to the store."""
    return f"{task_path(record['task'])}/{record['id']}{SUFFIX}"


def _sync_folder(path: Path) -> None:
    """Make the new name itself last through a power cut, where the system allows it."""
    with contextlib.suppress(OSError):
        handle = os.open(path, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def _clear_stale(directory: Path, now: float | None = None) -> None:
    """Remove temporary files that a crashed write left in one folder. A write that is still going on is left alone."""
    moment = time.time() if now is None else now
    with contextlib.suppress(OSError):
        for item in directory.iterdir():
            if item.name.startswith(TEMPORARY_PREFIX) and moment - item.lstat().st_mtime > STALE_AFTER:
                with contextlib.suppress(OSError):
                    item.unlink()


def write(repo: Path, record: object) -> Stored:
    """Check a record and add it to the store. Raises TugError when it is not valid or is already there."""
    checked = records.validate_record(record)
    text = pretty_json(checked)
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise DataError(f"the record is larger than {MAX_RECORD_BYTES // 1024} KB")
    relative = record_path(checked)
    target = folder(repo) / relative
    apprepo.local_folder(repo).mkdir(exist_ok=True)
    apprepo.ensure_excluded(repo, f"/{apprepo.LOCAL_FOLDER}/")
    target.parent.mkdir(parents=True, exist_ok=True)
    _clear_stale(target.parent)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=TEMPORARY_PREFIX, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o444)  # written once, never edited
        try:
            # A hard link gives the record its name in one step, and fails when the name is taken. A rename would replace.
            os.link(temporary, target)
        except FileExistsError as error:
            raise TugError(f"the record {relative} is already in the store. A record is written once and never edited.") from error
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
    _sync_folder(target.parent)
    return Stored(relative, checked)


def _read_one(file: Path, relative: str) -> dict[str, Any]:
    if file.is_symlink() or not file.is_file():
        raise DataError("it is not a plain file")
    checked = records.validate_record(read_json_file(file, max_bytes=MAX_RECORD_BYTES))
    if record_path(checked) != relative:
        # A record that was moved or renamed was edited, as far as the store can tell.
        raise DataError(f"it is not at the path its own content gives ({record_path(checked)})")
    return checked


def read_all(repo: Path, task: Mapping[str, Any] | None = None) -> Listing:
    """Every record in the store, or every record of one story. Never raises for what a file holds."""
    top = folder(repo)
    start = top / task_path(task) if task is not None else top
    found: list[Stored] = []
    unreadable: list[Unreadable] = []
    cut_short = False
    looked_at = 0
    if not start.is_dir() or start.is_symlink():
        return Listing((), ())
    for current, folders, files in os.walk(start):
        folders.sort()
        for name in sorted(files):
            if name.startswith(TEMPORARY_PREFIX):
                continue  # a write that is going on, or one that a crash cut off
            looked_at += 1
            if looked_at > MAX_FILES:
                cut_short = True
                break
            file = Path(current) / name
            relative = file.relative_to(top).as_posix()
            if not name.endswith(SUFFIX):
                unreadable.append(Unreadable(relative, "it is not a record file", False))
                continue
            try:
                found.append(Stored(relative, _read_one(file, relative)))
            except records.NewerSchema as error:
                unreadable.append(Unreadable(relative, str(error), True))
            except DataError as error:
                unreadable.append(Unreadable(relative, str(error).replace(os.fspath(file), relative), False))
        if cut_short:
            break
    found.sort(key=lambda stored: (_moment(stored.record["at"]), stored.record["id"]))
    return Listing(tuple(found), tuple(unreadable), cut_short)


def _moment(stamp: str) -> datetime:
    # Compared as times: as text, a time with a fraction of a second would sort before the same second without one.
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def unpublished(repo: Path) -> tuple[str, ...]:
    """The record files in the store that were not published yet, relative to the store.

    Nothing publishes yet, so today that is every file. When publishing is
    built, a file that is on the `records` branch leaves this list.
    """
    top = folder(repo)
    if not top.is_dir() or top.is_symlink():
        return ()
    return tuple(
        item.relative_to(top).as_posix()
        for item in sorted(top.rglob("*"))
        if item.is_file() and not item.is_symlink() and not item.name.startswith(TEMPORARY_PREFIX)
    )
