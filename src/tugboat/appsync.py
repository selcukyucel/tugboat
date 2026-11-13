"""One sync of an app repo: the team repo clone, the copy, and the agent tool files."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tugboat import hostfiles, profile, sync, teamcopy
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
    unfinished: tuple[str, ...] = ()  # setup steps of the project that are still open
    profile_note: str | None = None  # what became of a profile change this machine proposed


def unfinished_setup(address: TeamRepoAddress) -> tuple[tuple[str, ...], str | None]:
    """What is still missing in the project's setup, and news of this machine's own profile proposal.

    Read from the profile. Needs no network.
    """
    try:
        working = profile.load(address)
    except TugError as error:
        return (str(error),), None
    if working.profile is None:
        return ("this project has no profile yet. `tug init`, run in this repo in a terminal, creates it",), working.note
    if working.profile["tracker"]["criteria_source"] is None:
        step = (
            "where the project's stories keep their acceptance criteria is not chosen yet. "
            f"The skill `{hostfiles.CRITERIA_SKILL}` finds it in an agent session and saves the developer's pick"
        )
        return (step,), working.note
    return (), working.note


def run(repo: Path, address: TeamRepoAddress, *, force: bool = False, quick: bool = False) -> AppSync:
    """Sync everything for one app repo. `quick` is for a session start, where the agent tool is waiting."""
    if quick:
        report = sync.sync_team_repo(address, lock_wait=QUICK_LOCK_WAIT, fetch_timeout=QUICK_FETCH_TIMEOUT)
    else:
        report = sync.sync_team_repo(address)
    if not report.usable:
        return AppSync(repo, report, None, None, None)
    unfinished, profile_note = unfinished_setup(address)
    copy = host = None
    problem = None
    try:
        # Neither step needs the network. Both work from what the last good sync fetched.
        copy = teamcopy.refresh_copy(repo, address, force=force)
        host = hostfiles.apply(repo, address)
    except TugError as error:
        problem = str(error)
    return AppSync(repo, report, copy, host, problem, unfinished, profile_note)


def render(result: AppSync) -> str:
    lines = [sync.render(result.sync)]
    if result.copy is not None:
        lines.append(teamcopy.render(result.copy, result.repo))
    if result.host is not None:
        lines.append(hostfiles.render(result.host))
    if result.problem:
        lines.append(f"Note: this repo's files were not all brought up to date: {result.problem}")
    if result.profile_note:
        lines.append(result.profile_note)
    lines.extend(f"Setup is not finished: {step}." for step in result.unfinished)
    return "\n".join(lines)
