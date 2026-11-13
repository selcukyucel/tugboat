"""One sync of an app repo: the team repo clone, the copy, and the agent tool files."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tugboat import hostfiles, sync, teamcopy
from tugboat.address import TeamRepoAddress
from tugboat.errors import TugError

QUICK_FETCH_TIMEOUT = 10.0
QUICK_LOCK_WAIT = 2.0


class AppSync(NamedTuple):
    repo: Path
    sync: sync.SyncReport
    copy: teamcopy.CopyReport | None
    host: hostfiles.HostReport | None
    problem: str | None  # why the copy or the agent tool files could not be brought up to date


def run(repo: Path, address: TeamRepoAddress, *, force: bool = False, quick: bool = False) -> AppSync:
    """Sync everything for one app repo. `quick` is for a session start, where the agent tool is waiting."""
    if quick:
        report = sync.sync_team_repo(address, lock_wait=QUICK_LOCK_WAIT, fetch_timeout=QUICK_FETCH_TIMEOUT)
    else:
        report = sync.sync_team_repo(address)
    if not report.usable:
        return AppSync(repo, report, None, None, None)
    copy = host = None
    problem = None
    try:
        # Neither step needs the network. Both work from what the last good sync fetched.
        copy = teamcopy.refresh_copy(repo, address, force=force)
        host = hostfiles.apply(repo, address)
    except TugError as error:
        problem = str(error)
    return AppSync(repo, report, copy, host, problem)


def render(result: AppSync) -> str:
    lines = [sync.render(result.sync)]
    if result.copy is not None:
        lines.append(teamcopy.render(result.copy, result.repo))
    if result.host is not None:
        lines.append(hostfiles.render(result.host))
    if result.problem:
        lines.append(f"Note: this repo's files were not all brought up to date: {result.problem}")
    return "\n".join(lines)
