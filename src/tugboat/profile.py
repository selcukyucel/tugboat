"""The project profile: the choices one project made.

The approved profile is `profile.json` on the team repo's `main`. It changes
only through a pull request that a person merges. Between proposing a change
and its merge, this machine works from its own copy of the proposed profile.
That copy is kept per team repo, so a second app repo on the same machine
sees it too.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import formats, paths, team_repo, teamcopy
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError
from tugboat.gitrun import git
from tugboat.strict_json import parse_json, pretty_json, read_json_file
from tugboat.validate import require_exact_fields, require_object
from tugboat.version import installed_version, parse_version

PENDING_SCHEMA = "tug.pending-profile/1"
FILE = team_repo.PROFILE

# Each choice in the order it is shown, with the words a person reads.
TRACKERS = (("jira", "Jira"), ("github-projects", "GitHub Projects"))
CI_FEATURES = (("secret-scan", "Secret scan"), ("guardrail-pack", "Guardrail pack"), ("dependency-verification", "Dependency verification"))
SURFACES = (("claude-code", "Claude Code"), ("copilot-cli", "GitHub Copilot CLI"), ("copilot-vscode", "GitHub Copilot in VS Code"))
APPROVAL_MODES = (
    ("teammate", "A teammate. `main` of the team repo needs a pull request with one approving review."),
    ("owner", "The owner. For a solo project: the owner merges their own pull request."),
)
PLATFORM_NAMES = {"ios": "iOS", "android": "Android"}


class Pending(NamedTuple):
    """A profile this machine proposed, or tried to propose, and that is not merged yet."""

    base: dict[str, Any] | None  # the approved profile when the proposal was made
    profile: dict[str, Any]
    branch: str | None  # None when the proposal could not be pushed
    pull_request: str | None
    open_link: str | None


class Working(NamedTuple):
    approved: dict[str, Any] | None  # what `main` of the team repo holds
    profile: dict[str, Any] | None  # what this machine works from
    pending: Pending | None  # set when `profile` is a proposal that waits for its merge
    note: str | None  # what became of an earlier proposal


def names(values: Sequence[str], table: Sequence[tuple[str, str]], empty: str = "none") -> str:
    labels = dict(table)
    return ", ".join(labels[value] for value in values) or empty


def ordered(values: Sequence[str], table: Sequence[tuple[str, str]]) -> list[str]:
    """The chosen values in the order of their table, so that the same choice always gives the same file."""
    return [value for value, _ in table if value in values]


def is_team_repo(clone: Path) -> bool:
    revision = teamcopy.approved_revision(clone)
    return team_repo.MARKER in git("ls-tree", "--name-only", revision, cwd=clone).splitlines()


def approved(clone: Path) -> dict[str, Any] | None:
    """The profile on the fetched `main`. None when the project has none yet."""
    revision = teamcopy.approved_revision(clone)
    if FILE not in git("ls-tree", "--name-only", revision, "--", FILE, cwd=clone).splitlines():
        return None
    try:
        return formats.validate_profile(parse_json(git("show", f"{revision}:{FILE}", cwd=clone)))
    except DataError as error:
        raise TugError(
            f"the project profile in the team repo is not valid: {error}. It is fixed by a pull request in the team repo. "
            "If a newer Tugboat wrote it, update `tug`."
        ) from error


# ----------------------------------------------------------- the local copy


def _optional_profile(value: object) -> dict[str, Any] | None:
    return None if value is None else formats.validate_profile(value)


def _optional_text(value: object, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise DataError(f"{label} must be text")
    return value


def read_pending(address: TeamRepoAddress) -> Pending | None:
    """The proposal this machine keeps for one team repo. None when missing or broken."""
    try:
        data = require_object(read_json_file(paths.pending_profile_file(address)), "pending profile")
        require_exact_fields(data, required=frozenset({"schema", "url", "base", "profile", "branch", "pull_request", "open_link"}), label="pending profile")
        if data["schema"] != PENDING_SCHEMA or data["url"] != address.url:
            return None
        return Pending(
            _optional_profile(data["base"]),
            formats.validate_profile(data["profile"]),
            _optional_text(data["branch"], "branch"),
            _optional_text(data["pull_request"], "pull_request"),
            _optional_text(data["open_link"], "open_link"),
        )
    except DataError:
        return None


def write_pending(address: TeamRepoAddress, pending: Pending) -> None:
    target = paths.pending_profile_file(address)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(pretty_json({"schema": PENDING_SCHEMA, "url": address.url, **pending._asdict()}))
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def clear_pending(address: TeamRepoAddress) -> None:
    with contextlib.suppress(FileNotFoundError):
        paths.pending_profile_file(address).unlink()


def where(pending: Pending) -> str:
    """Where a person finds the proposal in GitHub."""
    return pending.pull_request or pending.open_link or "a pull request that was not opened"


def load(address: TeamRepoAddress) -> Working:
    """The profile to work from: the approved one, or this machine's proposal while it waits.

    A proposal is dropped as soon as `main` holds it, and also when `main`
    moved to some other profile: the approved profile always wins.
    """
    current = approved(paths.team_repo_clone(address))
    pending = read_pending(address)
    if pending is None:
        return Working(current, current, None, None)
    if current == pending.profile:
        clear_pending(address)
        return Working(current, current, None, "The profile change that this machine proposed was merged.")
    if current != pending.base:
        clear_pending(address)
        note = "The approved profile changed after this machine proposed its own change, so that proposal was set aside."
        if pending.branch:
            note += f" If its pull request is still open, close it: {where(pending)}"
        return Working(current, current, None, note)
    return Working(current, pending.profile, pending, None)


# ------------------------------------------------------------------ editing


def new_profile(*, project: str, tracker: str, ci_features: Sequence[str], surfaces: Sequence[str], approval_mode: str) -> dict[str, Any]:
    return formats.validate_profile(
        {
            "schema": formats.PROFILE_SCHEMA,
            "project": project,
            "app_repos": [],
            "tracker": {"kind": tracker, "criteria_source": None},
            "ci_features": ordered(ci_features, CI_FEATURES),
            "surfaces": ordered(surfaces, SURFACES),
            "approval_mode": approval_mode,
            # The oldest tool that is known to read this profile is the one that wrote it.
            "minimum_tool_version": str(installed_version()),
            "frameworks": [],
        }
    )


def with_choices(
    profile: dict[str, Any],
    *,
    tracker: str | None = None,
    ci_features: Sequence[str] | None = None,
    surfaces: Sequence[str] | None = None,
    approval_mode: str | None = None,
) -> dict[str, Any]:
    """A copy with some choices changed. None leaves a choice as it is."""
    changed = {**profile, "tracker": dict(profile["tracker"])}
    if tracker is not None and tracker != profile["tracker"]["kind"]:
        # The saved criteria source names a field of the old tracker.
        changed["tracker"] = {"kind": tracker, "criteria_source": None}
    if ci_features is not None:
        changed["ci_features"] = ordered(ci_features, CI_FEATURES)
    if surfaces is not None:
        changed["surfaces"] = ordered(surfaces, SURFACES)
    if approval_mode is not None:
        changed["approval_mode"] = approval_mode
    return formats.validate_profile(changed)


def find_repo(profile: dict[str, Any], github: str) -> dict[str, Any] | None:
    return next((repo for repo in profile["app_repos"] if repo["github"].lower() == github.lower()), None)


def with_repo(profile: dict[str, Any], *, github: str, default_branch: str, platform: str) -> dict[str, Any]:
    """A copy that lists this app repo. An entry for the same repo is replaced in its place."""
    entry = {"github": github, "default_branch": default_branch, "platform": platform}
    repos = [dict(repo) for repo in profile["app_repos"]]
    for index, repo in enumerate(repos):
        if repo["github"].lower() == github.lower():
            repos[index] = entry
            break
    else:
        repos.append(entry)
    return formats.validate_profile({**profile, "app_repos": repos})


def below_minimum(profile: dict[str, Any] | None) -> bool:
    """True when the installed tool is older than the project accepts."""
    return profile is not None and installed_version() < parse_version(profile["minimum_tool_version"])


def to_bytes(profile: dict[str, Any]) -> bytes:
    return pretty_json(formats.validate_profile(profile)).encode("utf-8")


def _repo_words(repo: dict[str, Any]) -> str:
    return f"{repo['github']} ({PLATFORM_NAMES[repo['platform']]}, default branch {repo['default_branch']})"


def _source_words(source: dict[str, Any] | None) -> str:
    return "not chosen yet" if source is None else f"{source['kind']} '{source['name']}'"


def describe(profile: dict[str, Any]) -> list[str]:
    """The profile as the few lines a person reads."""
    return [
        f"Project: {profile['project']}",
        f"Tracker: {names([profile['tracker']['kind']], TRACKERS)}",
        f"Acceptance criteria source: {_source_words(profile['tracker']['criteria_source'])}",
        f"CI features: {names(profile['ci_features'], CI_FEATURES)}",
        f"Surfaces: {names(profile['surfaces'], SURFACES)}",
        f"Approval: {'a teammate' if profile['approval_mode'] == 'teammate' else 'the owner'}",
        f"Minimum tool version: {profile['minimum_tool_version']}",
        f"App repos: {', '.join(_repo_words(repo) for repo in profile['app_repos']) or 'none'}",
    ]


def changes(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """What differs between two profiles, one line per difference."""
    if before is None:
        return [f"New profile. {line}" if index == 0 else line for index, line in enumerate(describe(after))]
    lines = [f"{new}  (was: {old.split(': ', 1)[1]})" for old, new in zip(describe(before)[:-1], describe(after)[:-1]) if old != new]
    old_repos = {repo["github"].lower(): repo for repo in before["app_repos"]}
    new_repos = {repo["github"].lower(): repo for repo in after["app_repos"]}
    for key, repo in new_repos.items():
        if key not in old_repos:
            lines.append(f"App repo added: {_repo_words(repo)}")
        elif old_repos[key] != repo:
            lines.append(f"App repo changed: {_repo_words(repo)}  (was: {_repo_words(old_repos[key])})")
    lines.extend(f"App repo removed: {_repo_words(repo)}" for key, repo in old_repos.items() if key not in new_repos)
    return lines
