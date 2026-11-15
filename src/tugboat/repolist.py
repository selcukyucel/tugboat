"""The app repos Tugboat set up on this machine.

`tug remove --all` needs to know every repo that still has Tugboat's hooks,
and removal needs to know when no repo uses a team repo clone any more. So
init and sync write each repo they set up into one list in Tugboat's home
folder. A repo that was set up by a tool older than this list joins it at
its next sync.

The list is a convenience, never a source of truth: what is really in a
repo is read from the repo.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import NamedTuple

from tugboat import paths
from tugboat.errors import DataError
from tugboat.strict_json import pretty_json, read_json_file
from tugboat.validate import utc_now

SCHEMA = "tug.app-repos/1"
MAX_REPOS = 500


class Entry(NamedTuple):
    path: str  # the top folder of the app repo
    team_repo: str  # the address of the team repo it is set up for
    added_at: str


def read() -> list[Entry]:
    """Every listed repo. A missing or broken file reads as an empty list."""
    try:
        data = read_json_file(paths.app_repos_file())
    except DataError:
        return []
    repos = data.get("repos") if isinstance(data, dict) and data.get("schema") == SCHEMA else None
    found: list[Entry] = []
    for item in repos if isinstance(repos, list) else ():
        if not isinstance(item, dict):
            continue
        path, team_repo, added_at = item.get("path"), item.get("team_repo"), item.get("added_at")
        if isinstance(path, str) and os.path.isabs(path) and isinstance(team_repo, str) and isinstance(added_at, str):
            if all(entry.path != path for entry in found):
                found.append(Entry(path, team_repo, added_at))
    return found[:MAX_REPOS]


def _write(entries: list[Entry]) -> None:
    target = paths.app_repos_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(pretty_json({"schema": SCHEMA, "repos": [entry._asdict() for entry in entries]}))
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def record(repo: Path, team_repo: str) -> bool:
    """Put one repo on the list. Returns True when the list changed. An unchanged entry writes nothing."""
    path = os.fspath(repo)
    entries = read()
    for index, entry in enumerate(entries):
        if entry.path == path:
            if entry.team_repo == team_repo:
                return False
            entries[index] = Entry(path, team_repo, entry.added_at)
            break
    else:
        entries.append(Entry(path, team_repo, utc_now()))
    _write(entries)
    return True


def forget(repo: Path | str) -> bool:
    """Take one repo off the list. Returns True when it was on it."""
    path = os.fspath(repo)
    entries = read()
    kept = [entry for entry in entries if entry.path != path]
    if len(kept) == len(entries):
        return False
    _write(kept)
    return True
