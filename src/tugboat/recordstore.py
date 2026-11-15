"""The store of task records: the team repo's `records` branch, checked out on this machine.

Records live in the team repo, on one branch named `records`, and nowhere
else. On a machine that branch is checked out as a Git worktree beside the
clone of the team repo:

    ~/.tugboat/records/github.com/<owner>/<team repo>/

A record is written straight into that folder. Until the developer confirms
a checkpoint it is a file that Git has not committed: that is what
"unpublished" means. Publishing commits and pushes the same file. So there
is one copy of a record on a machine, in one place, and nothing about a
record is ever written into an app repo.

    jira/SHOP-123/20261115T101530Z-3f9a1c2b7d4e.json
    github-projects/acme/shop-ios/311/20261115T101530Z-3f9a1c2b7d4e.json

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

Every app repo, clone, and task worktree on the machine that belongs to the
same team repo shares this one folder. The folder is outside every app
repo, so the check before each action guards it by its place: an agent's
edit tools are kept out of Tugboat's whole folder on the machine.
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

from tugboat import paths, records, sync
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError
from tugboat.gitrun import GitError, git
from tugboat.strict_json import pretty_json, read_json_file

BRANCH = "records"
SUFFIX = ".json"
TEMPORARY_PREFIX = ".writing-"
TEMPORARY_PATTERN = f"{TEMPORARY_PREFIX}*"
MAX_RECORD_BYTES = 512 * 1024
MAX_FILES = 20_000  # files looked at in one read of the store
STALE_AFTER = 3600.0  # seconds after which a temporary file is what a crash left behind
LOCK_WAIT = 15.0


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


def folder(address: TeamRepoAddress) -> Path:
    return paths.records_worktree(address)


def _is_worktree(store: Path) -> bool:
    """True when the folder is a checkout of the `records` branch."""
    if not (store / ".git").exists():
        return False
    try:
        return git("symbolic-ref", "--short", "--quiet", "HEAD", cwd=store) == BRANCH
    except GitError:
        return False


def _has(clone: Path, ref: str) -> bool:
    try:
        git("show-ref", "--verify", "--quiet", ref, cwd=clone)
        return True
    except GitError:
        return False


def _add_empty_branch(clone: Path, store: Path) -> None:
    """Check out a new `records` branch that holds nothing and shares no history with `main`."""
    try:
        git("worktree", "add", "--quiet", "--orphan", "-b", BRANCH, os.fspath(store), cwd=clone)
    except GitError:
        _add_empty_branch_old_git(clone, store)


def _add_empty_branch_old_git(clone: Path, store: Path) -> None:
    # Git before 2.42 has no `worktree add --orphan`: check out `main` without a branch, then leave it for an empty one.
    git("worktree", "add", "--quiet", "--detach", os.fspath(store), cwd=clone)
    git("checkout", "--quiet", "--orphan", BRANCH, cwd=store)
    git("rm", "-r", "--quiet", "--force", "--ignore-unmatch", ".", cwd=store)


def _hide_temporary_files(clone: Path) -> None:
    """Keep the temporary files of a write out of Git's sight, so they can never be published."""
    exclude = Path(git("rev-parse", "--git-common-dir", cwd=clone))
    exclude = (exclude if exclude.is_absolute() else clone / exclude) / "info" / "exclude"
    try:
        lines = exclude.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if TEMPORARY_PATTERN not in lines:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join([*lines, TEMPORARY_PATTERN]) + "\n", encoding="utf-8")


def ensure(address: TeamRepoAddress) -> Path:
    """Make sure the `records` branch is checked out on this machine, and return its folder. Needs no network.

    The branch is taken from what sync fetched, when GitHub has it already.
    Otherwise it starts here, empty, and reaches GitHub with the first
    publish.
    """
    store = folder(address)
    if _is_worktree(store):
        return store
    clone = paths.team_repo_clone(address)
    if not (clone / ".git").exists():
        raise TugError(f"this machine has no copy of the team repo {address.url} yet. Run `tug sync` first.")
    with sync.hold_lock(paths.records_lock_file(address), LOCK_WAIT) as held:
        if not held:
            raise TugError("another `tug` is setting up the task records of this team repo. Try again in a moment.")
        if _is_worktree(store):
            return store  # another `tug` made it while this one waited
        if store.exists() and any(store.iterdir()):
            raise TugError(f"{store} exists and is not the task records of {address.slug}. It was left as it is.")
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            git("worktree", "prune", cwd=clone)  # forget a checkout whose folder somebody deleted
            if _has(clone, f"refs/heads/{BRANCH}"):
                git("worktree", "add", "--quiet", os.fspath(store), BRANCH, cwd=clone)
            elif _has(clone, f"refs/remotes/origin/{BRANCH}"):
                # No tracking: the clone follows `main` only, so Git would refuse. Fetching and pushing name the branch themselves.
                git("worktree", "add", "--quiet", "--no-track", "-b", BRANCH, os.fspath(store), f"origin/{BRANCH}", cwd=clone)
            else:
                _add_empty_branch(clone, store)
            _hide_temporary_files(clone)
        except (GitError, OSError) as error:
            raise TugError(f"the task records of {address.slug} could not be set up at {store} ({error})") from error
    return store


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


def write(address: TeamRepoAddress, record: object) -> Stored:
    """Check a record and add it to the store, unpublished. Raises TugError when it is not valid or is already there."""
    checked = records.validate_record(record)
    text = pretty_json(checked)
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise DataError(f"the record is larger than {MAX_RECORD_BYTES // 1024} KB")
    relative = record_path(checked)
    target = ensure(address) / relative
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


def read_all(address: TeamRepoAddress, task: Mapping[str, Any] | None = None) -> Listing:
    """Every record in the store, published or not, or every record of one story. Never raises for what a file holds."""
    top = folder(address)
    start = top / task_path(task) if task is not None else top
    found: list[Stored] = []
    unreadable: list[Unreadable] = []
    cut_short = False
    looked_at = 0
    if not start.is_dir() or start.is_symlink():
        return Listing((), ())
    for current, folders, files in os.walk(start):
        folders[:] = sorted(name for name in folders if name != ".git")
        for name in sorted(files):
            if name.startswith(TEMPORARY_PREFIX) or (name == ".git" and Path(current) == top):
                continue  # a write that is going on, or one that a crash cut off; and Git's own pointer to the clone
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


def unpublished(address: TeamRepoAddress) -> tuple[str, ...]:
    """The record files that Git has not committed yet, relative to the store. Empty when there is no store."""
    store = folder(address)
    if not _is_worktree(store):
        return ()
    try:
        entries = git("status", "--porcelain", "-z", "--untracked-files=all", cwd=store).split("\0")
    except GitError:
        return ()
    # "??" is a new file, which is all the tool ever makes here. "A " is one that was added and not committed.
    return tuple(sorted(entry[3:] for entry in entries if entry[:2] in {"??", "A "} and entry.endswith(SUFFIX)))
