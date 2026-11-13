"""The releases of Tugboat itself.

A release is a tag `v1.2.3` in the tool's own public repo. That address is
fixed here. Nothing a file, an option, or the environment says can point an
update at another place.

The newest release is looked up at most once a day, and the answer is kept
on this machine. A lookup that fails is never an error: work goes on.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from tugboat import paths
from tugboat.errors import DataError
from tugboat.gitrun import GitError, git
from tugboat.strict_json import pretty_json, read_json_file
from tugboat.validate import canonical_timestamp, require_exact_fields, require_object
from tugboat.version import Version, installed_version, parse_tag, parse_version

TOOL_REPO = "https://github.com/selcukyucel/tugboat.git"
INSTALL_SOURCE = "git+https://github.com/selcukyucel/tugboat"
CHECK_SCHEMA = "tug.release-check/1"
CHECK_EVERY = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=1)  # after a lookup that failed
QUICK_TIMEOUT = 5.0  # at a session start, where the agent tool is waiting
TIMEOUT = 20.0


def read_releases(timeout: float = TIMEOUT) -> list[Version]:
    """Every release of the tool, oldest first, asked from the tool's repo. Raises GitError."""
    listed = git("ls-remote", "--tags", "--refs", TOOL_REPO, "v*", timeout=timeout)
    versions = set()
    for line in listed.splitlines():
        _, _, ref = line.partition("\t")
        # A tag that is not exactly v1.2.3 is not a release.
        with contextlib.suppress(DataError):
            versions.add(parse_tag(ref.removeprefix("refs/tags/")))
    return sorted(versions)


def install_command(version: Version) -> list[str]:
    """The uv arguments that install one release. The version is three numbers, so the text is safe."""
    return ["tool", "install", "--force", f"{INSTALL_SOURCE}@{version.tag}"]


def _read_check() -> dict[str, Any]:
    empty = {"checked_at": None, "attempted_at": None, "newest": None}
    try:
        state = require_object(read_json_file(paths.release_check_file()), "release check")
        require_exact_fields(state, required=frozenset({"schema", *empty}), label="release check")
        if state["schema"] != CHECK_SCHEMA:
            return empty
        for key in ("checked_at", "attempted_at"):
            if state[key] is not None:
                canonical_timestamp(state[key], key)
        if state["newest"] is not None:
            parse_version(state["newest"], "newest")
        return {key: state[key] for key in empty}
    except DataError:
        return empty


def _write_check(state: dict[str, Any]) -> None:
    target = paths.release_check_file()
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(pretty_json({"schema": CHECK_SCHEMA, **state}))
            os.replace(temporary, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise


def _time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def remember(newest: Version | None, now: datetime | None = None) -> None:
    """Keep the answer of a lookup that worked."""
    stamp = _stamp(now or datetime.now(timezone.utc))
    _write_check({"checked_at": stamp, "attempted_at": stamp, "newest": str(newest) if newest else None})


def newest_seen(*, ask: bool = True, now: datetime | None = None, timeout: float = QUICK_TIMEOUT) -> Version | None:
    """The newest release this machine knows of. None when it knows none.

    With `ask`, the tool's repo is asked when the last answer is older than a
    day. A lookup that fails changes nothing and is tried again an hour later.
    This never raises.
    """
    now = now or datetime.now(timezone.utc)
    state = _read_check()
    checked, attempted = _time(state["checked_at"]), _time(state["attempted_at"])
    due = checked is None or now - checked >= CHECK_EVERY or checked > now
    waited = attempted is None or now - attempted >= RETRY_AFTER or attempted > now
    if ask and due and waited:
        try:
            versions = read_releases(timeout)
        except GitError:
            _write_check({**state, "attempted_at": _stamp(now)})
        else:
            remember(versions[-1] if versions else None, now)
            state = _read_check()
    return parse_version(state["newest"]) if state["newest"] else None


def newer_release(**options: Any) -> Version | None:
    """A release newer than the installed tool, when this machine knows of one."""
    newest = newest_seen(**options)
    return newest if newest is not None and newest > installed_version() else None
