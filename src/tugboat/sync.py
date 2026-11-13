"""Sync core: bring the local clone of a team repo up to date.

Three promises:

- A failed sync never stops work. It is reported, with the time of the last
  sync that worked.
- The clone only ever moves forward to what GitHub has. Nothing is merged,
  rebased, or reset.
- Local edits in the clone are never discarded. They are reported.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import paths
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError
from tugboat.gitrun import GitError, git
from tugboat.strict_json import pretty_json, read_json_file
from tugboat.validate import canonical_timestamp, require_exact_fields, require_object, utc_now

STATE_SCHEMA = "tug.sync-state/1"
BRANCH = "main"
FETCH_TIMEOUT = 30.0
CLONE_TIMEOUT = 120.0
LOCK_WAIT = 15.0


class SyncReport(NamedTuple):
    address: TeamRepoAddress
    clone: Path
    ok: bool  # this run reached the team repo and the clone now matches it
    cloned: bool  # this run made the clone
    updated: bool  # this run moved the clone to a newer revision
    revision: str | None  # what the clone is on now. None when there is no clone.
    revision_date: str | None
    previous_revision: str | None
    last_success_at: str | None  # the last sync that worked, this one included
    error: str | None  # why this run did not work
    notes: tuple[str, ...]  # things the developer should know, such as local edits

    @property
    def usable(self) -> bool:
        """True when there is a clone to work from, fresh or not."""
        return self.revision is not None


@contextlib.contextmanager
def _lock(path: Path, wait: float) -> Iterator[bool]:
    """Hold the sync lock of one team repo. Yields False when another sync has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.1)
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_state(address: TeamRepoAddress) -> dict[str, Any]:
    """The saved result of earlier syncs. A missing or broken file reads as empty."""
    empty = {"last_success_at": None, "last_attempt_at": None, "last_error": None, "revision": None}
    try:
        state = require_object(read_json_file(paths.sync_state_file(address)), "sync state")
        require_exact_fields(state, required=frozenset({"schema", "url", *empty}), label="sync state")
        if state["schema"] != STATE_SCHEMA or state["url"] != address.url:
            return empty
        for key in ("last_success_at", "last_attempt_at"):
            if state[key] is not None:
                canonical_timestamp(state[key], key)
        return {key: state[key] for key in empty}
    except DataError:
        return empty


def _write_state(address: TeamRepoAddress, state: dict[str, Any]) -> None:
    target = paths.sync_state_file(address)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(pretty_json({"schema": STATE_SCHEMA, "url": address.url, **state}))
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _is_clone(clone: Path) -> bool:
    return (clone / ".git").exists()


def _head(clone: Path) -> tuple[str | None, str | None]:
    try:
        revision = git("rev-parse", "--verify", "--quiet", "HEAD", cwd=clone)
        date = canonical_timestamp(git("show", "-s", "--format=%cI", "HEAD", cwd=clone), "commit date")
        return revision, date
    except (GitError, DataError):
        return None, None


def _clone(address: TeamRepoAddress, clone: Path) -> None:
    clone.parent.mkdir(parents=True, exist_ok=True)
    # Clone beside the final place and move it in, so a failed clone leaves nothing half made.
    staging = Path(tempfile.mkdtemp(dir=clone.parent, prefix=clone.name + ".", suffix=".cloning"))
    try:
        git(
            "clone", "--quiet", "--branch", BRANCH, "--single-branch", "--no-recurse-submodules",
            # A link in the repo is checked out as a small text file. It can point nowhere.
            "-c", "core.symlinks=false",
            "--", address.url, os.fspath(staging / "repo"),
            timeout=CLONE_TIMEOUT,
        )  # fmt: skip
        os.replace(staging / "repo", clone)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _local_edits(clone: Path) -> tuple[str, ...]:
    lines = [line for line in git("status", "--porcelain", cwd=clone).splitlines() if line.strip()]
    if not lines:
        return ()
    names = ", ".join(line[3:] for line in lines[:3])
    more = f", and {len(lines) - 3} more" if len(lines) > 3 else ""
    count = f"{len(lines)} local edit{'' if len(lines) == 1 else 's'}"
    return (f"{count} in the clone, kept untouched: {names}{more}",)


def _bring_up_to_date(address: TeamRepoAddress, clone: Path, fetch_timeout: float) -> tuple[bool, tuple[str, ...]]:
    """Fetch and move forward. Return (updated, notes). Raise GitError when it cannot."""
    found = git("config", "--get", "remote.origin.url", cwd=clone)
    if found != address.url:
        raise GitError("sync", f"the clone at {clone} belongs to a different address ({found}). It was left as it is.")
    git("fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "origin", BRANCH, cwd=clone, timeout=fetch_timeout)

    notes = _local_edits(clone)
    try:
        branch = git("symbolic-ref", "--short", "--quiet", "HEAD", cwd=clone)
    except GitError:
        branch = "(no branch)"
    if branch != BRANCH:
        raise GitError("sync", f"the clone is on '{branch}', not on '{BRANCH}'. It was left as it is.")

    ahead, behind = (int(part) for part in git("rev-list", "--left-right", "--count", f"HEAD...origin/{BRANCH}", cwd=clone).split())
    if ahead:
        raise GitError("sync", f"the clone has {ahead} local commit{'' if ahead == 1 else 's'} that GitHub does not have. It was left as it is.")
    if not behind:
        return False, notes
    try:
        git("merge", "--ff-only", "--quiet", f"origin/{BRANCH}", cwd=clone)
    except GitError as error:
        raise GitError("sync", f"a local edit is in the way of the update, so the clone was left as it is ({error.reason})") from error
    return True, notes


def sync_team_repo(address: TeamRepoAddress, *, lock_wait: float = LOCK_WAIT, fetch_timeout: float = FETCH_TIMEOUT) -> SyncReport:
    """Clone the team repo if needed, then bring the clone up to date. Never raises for a failed sync."""
    clone = paths.team_repo_clone(address)
    with _lock(paths.sync_lock_file(address), lock_wait) as held:
        state = read_state(address)
        before, _ = _head(clone) if _is_clone(clone) else (None, None)
        cloned = updated = False
        notes: tuple[str, ...] = ()
        error: str | None = None

        if not held:
            error = "another sync of this team repo is running"
        else:
            try:
                if not _is_clone(clone):
                    if clone.exists() and any(clone.iterdir()):
                        raise GitError("sync", f"{clone} exists and is not a clone. It was left as it is.")
                    _clone(address, clone)
                    cloned = True
                else:
                    updated, notes = _bring_up_to_date(address, clone, fetch_timeout)
            except GitError as failure:
                error = failure.reason if failure.action == "sync" else str(failure)
                if _is_clone(clone) and not notes:
                    with contextlib.suppress(GitError):
                        notes = _local_edits(clone)

        revision, revision_date = _head(clone) if _is_clone(clone) else (None, None)
        now = utc_now()
        if held:
            state = {
                "last_attempt_at": now,
                "last_success_at": now if error is None else state["last_success_at"],
                "last_error": error,
                "revision": revision,
            }
            _write_state(address, state)
        return SyncReport(
            address=address,
            clone=clone,
            ok=error is None,
            cloned=cloned,
            updated=updated,
            revision=revision,
            revision_date=revision_date,
            previous_revision=before if before != revision else None,
            last_success_at=state["last_success_at"],
            error=error,
            notes=notes,
        )


def age(timestamp: str, now: datetime | None = None) -> str:
    """How long ago, in words a person reads at a glance."""
    then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    seconds = int(((now or datetime.now(timezone.utc)) - then).total_seconds())
    if seconds < 90:
        return "just now"
    for size, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            return f"{count} {name}{'' if count == 1 else 's'} ago"
    return "just now"


def render(report: SyncReport, now: datetime | None = None) -> str:
    """The sync report as the few lines a developer or an agent reads."""
    lines = [f"Team repo: {report.address.url}"]
    short = report.revision[:7] if report.revision else None
    if report.ok:
        if report.cloned:
            lines.append(f"Cloned. Revision {short}, committed {report.revision_date}.")
        elif report.updated:
            lines.append(f"Updated from {(report.previous_revision or '')[:7]} to {short}, committed {report.revision_date}.")
        else:
            lines.append(f"Already up to date. Revision {short}, committed {report.revision_date}.")
    else:
        lines.append(f"Sync did not work: {report.error}")
        if report.usable:
            lines.append(f"Work continues from revision {short}, committed {report.revision_date}.")
        else:
            lines.append("There is no local copy of the team repo yet.")
    if report.last_success_at:
        lines.append(f"Last sync that worked: {report.last_success_at} ({age(report.last_success_at, now)}).")
    elif not report.ok:
        lines.append("No sync has worked on this machine yet.")
    lines.extend(f"Note: {note}." for note in report.notes)
    return "\n".join(lines)
