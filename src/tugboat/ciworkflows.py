"""The optional CI checks: the workflow files `tug init` puts into an app repo.

Today there is one: the secret scan. The scan itself is a reusable workflow
in the team repo, `.github/workflows/secret-scan.yml`. An app repo gets a
short workflow that calls it for every pull request.

This is the one file Tugboat writes into an app repo that Git is meant to
see: a workflow runs only when it is committed. Tugboat writes the file and
never commits it. A person commits it, through the app repo's own review.

The file is written when the project's profile selects the feature, and
removed when the profile no longer does. It is removed only while it is
still exactly what Tugboat wrote. A file somebody changed is left alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tugboat import sync
from tugboat.address import TeamRepoAddress
from tugboat.gitrun import GitError, git

SECRET_SCAN = "secret-scan"
CALLER = ".github/workflows/tugboat-secret-scan.yml"
REUSABLE = ".github/workflows/secret-scan.yml"
ABSENT, OURS, CHANGED = "absent", "ours", "changed"
BUILT = frozenset({SECRET_SCAN})  # the CI features that have a workflow. The others are a saved choice only.


class Change(NamedTuple):
    action: str  # "wrote", "removed", "kept", "none"
    lines: tuple[str, ...]  # what to tell the developer


def caller_text(address: TeamRepoAddress) -> str:
    """The workflow an app repo gets. It only calls the scan in the team repo."""
    return f"""# Tugboat wrote this file with `tug init`, because the project uses the CI secret scan.
# It calls the scan that lives in the team repo. A person commits it through this repo's own review.
# Do not edit it. `tug init` removes it when the project deselects the scan, and only while it is exactly as written.
name: Tugboat secret scan

on:
  pull_request:

permissions:
  contents: read

jobs:
  secret-scan:
    uses: {address.owner}/{address.name}/{REUSABLE}@{sync.BRANCH}
"""


def state(repo: Path, address: TeamRepoAddress) -> str:
    """Whether the caller workflow is absent, is exactly what Tugboat writes, or was changed by someone."""
    file = repo / CALLER
    if not file.exists() and not file.is_symlink():
        return ABSENT
    try:
        same = not file.is_symlink() and file.read_text(encoding="utf-8") == caller_text(address)
    except (OSError, UnicodeDecodeError):
        same = False
    return OURS if same else CHANGED


def is_committed(repo: Path) -> bool:
    """True when the caller workflow is in the commit the repo is on."""
    try:
        return bool(git("ls-tree", "--name-only", "HEAD", "--", CALLER, cwd=repo))
    except GitError:
        return False


def reusable_present(clone: Path) -> bool:
    """True when the team repo's approved revision has the scan the caller needs."""
    try:
        return bool(git("ls-tree", "--name-only", f"refs/remotes/origin/{sync.BRANCH}", "--", REUSABLE, cwd=clone))
    except GitError:
        return False


def apply(repo: Path, address: TeamRepoAddress, wanted: bool, *, team_has_scan: bool = True) -> Change:
    """Bring the caller workflow in line with the profile. Nothing is committed."""
    found = state(repo, address)
    file = repo / CALLER
    if wanted:
        notes = []
        if not team_has_scan:
            notes.append(
                f"The team repo {address.slug} has no {REUSABLE} yet, so the check would fail. "
                "A person adds that file there: it is in the starter layout of `tug team new`."
            )
        notes.append(
            f"If {address.slug} is a private repo, a person must let this repo call its workflows: "
            "in the team repo on GitHub, Settings, Actions, General, Access."
        )
        if found == CHANGED:
            return Change("kept", (f"CI secret scan: {CALLER} is there and is not what Tugboat writes, so it was left alone.", *notes))
        if found == OURS:
            if is_committed(repo):
                return Change("none", (f"CI secret scan: {CALLER} is in place.",))
            return Change("none", (f"CI secret scan: {CALLER} is written and not committed yet. It runs only once it is committed.", *notes))
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(caller_text(address), encoding="utf-8")
        return Change(
            "wrote",
            (
                f"CI secret scan: wrote {CALLER}. It calls the scan in the team repo for every pull request.",
                "Git sees this file, on purpose: a workflow runs only when it is committed. Tugboat does not commit it. "
                "Commit it through this repo's own review, with whoever owns its CI.",
                *notes,
            ),
        )
    if found == ABSENT:
        return Change("none", ())
    if found == CHANGED:
        return Change("kept", (f"CI secret scan: the project does not use it, and {CALLER} is still there. It is not what Tugboat writes, so it was left alone.",))
    committed = is_committed(repo)
    file.unlink()
    for folder in (file.parent, file.parent.parent):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    extra = " Git shows the removal. Commit it through this repo's own review." if committed else ""
    return Change("removed", (f"CI secret scan: the project does not use it any more, so {CALLER} was removed.{extra}",))


def unfinished(repo: Path, address: TeamRepoAddress, features: list[str]) -> str | None:
    """What the sync report says when the caller workflow and the profile disagree. Sync changes no tracked file."""
    found = state(repo, address)
    if SECRET_SCAN in features:
        if found == ABSENT:
            return f"the project uses the CI secret scan, and this repo has no {CALLER}. `tug init`, run in this repo in a terminal, writes it"
        if found == OURS and not is_committed(repo):
            return f"{CALLER} is written and not committed. The CI secret scan runs only once it is committed"
    elif found == OURS:
        return f"the project does not use the CI secret scan any more, and this repo still has {CALLER}. `tug init`, run in this repo in a terminal, removes it"
    return None
