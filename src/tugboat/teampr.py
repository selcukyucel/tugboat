"""Propose a change to the team repo as a pull request.

This is the only way Tugboat changes team content. It makes a branch,
commits, pushes the branch, and opens a pull request. It never merges, and
it never pushes to `main`. A person reviews and merges in GitHub.

The work happens in a temporary Git worktree. The clone that sync keeps
stays on `main` and is not touched, and no app repo is written to.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from tugboat import paths, sync, team_repo, teamcopy
from tugboat.address import TeamRepoAddress
from tugboat.errors import TugError
from tugboat.gitrun import GitError, git, run_process
from tugboat.validate import require_text, validate_relative_path
from tugboat.version import installed_version

BRANCH_PREFIX = "tug/"
# The parts of a team repo that a proposal may change. CI workflows are not among them.
PROPOSABLE = tuple(name for name in teamcopy.COPIED if name != "tugboat.json")
MAX_FILES = 200
MAX_FILE_BYTES = 512 * 1024
MAX_BODY = 20_000
PUSH_TIMEOUT = 60.0
GH_TIMEOUT = 60.0


class Proposal(NamedTuple):
    branch: str
    commit: str
    files: tuple[str, ...]
    pull_request: str | None  # the URL, when the pull request was opened
    open_link: str  # where a person can open it by hand
    note: str | None  # why the pull request was not opened for them


def slugify(text: str, maximum: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:maximum].strip("-")
    return slug or "change"


def _check_changes(changes: dict[str, bytes | None]) -> dict[str, bytes | None]:
    if not changes:
        raise TugError("there is nothing to propose")
    if len(changes) > MAX_FILES:
        raise TugError(f"a proposal can change at most {MAX_FILES} files")
    checked: dict[str, bytes | None] = {}
    for raw, content in changes.items():
        path = validate_relative_path(raw, "team repo path")
        if path.split("/")[0] not in PROPOSABLE:
            raise TugError(f"{path} is not in a part of the team repo that Tugboat may propose changes to ({', '.join(PROPOSABLE)})")
        if content is not None and len(content) > MAX_FILE_BYTES:
            raise TugError(f"{path} is larger than {MAX_FILE_BYTES // 1024} KB")
        checked[path] = content
    return checked


def _errors(folder: Path) -> set[tuple[str, str]]:
    return {(problem.path, problem.message) for problem in team_repo.check_team_repo(folder).errors}


def _open_with_gh(address: TeamRepoAddress, branch: str, title: str, body: str, gh: str) -> str:
    env_host = {} if address.host == "github.com" else {"GH_HOST": address.host}
    previous = {key: os.environ.get(key) for key in ("GH_PROMPT_DISABLED", "NO_COLOR", *env_host)}
    os.environ.update({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", **env_host})
    try:
        output = run_process(
            [gh, "pr", "create", "--repo", address.slug, "--base", sync.BRANCH, "--head", branch, "--title", title, "--body", body],
            action="pr create", timeout=GH_TIMEOUT,
        )  # fmt: skip
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    urls = [line.strip() for line in output.splitlines() if line.strip().startswith("https://")]
    if not urls:
        raise GitError("pr create", "gh did not print the address of the pull request")
    return urls[-1]


def propose(
    address: TeamRepoAddress,
    *,
    title: str,
    body: str,
    changes: dict[str, bytes | None],
    name: str | None = None,
    gh: str | None = None,
) -> Proposal:
    """Push a branch with these changes and open a pull request for it.

    `changes` maps a path in the team repo to its new content, or to None to
    remove the file. Raises TugError, and leaves nothing behind, when the
    proposal cannot be made.
    """
    title = require_text(title, "the title", 150)
    if "\n" in title:
        raise TugError("the title must be one line")
    body = body.strip()
    if len(body) > MAX_BODY:
        raise TugError(f"the description cannot exceed {MAX_BODY} characters")
    changes = _check_changes(changes)

    report = sync.sync_team_repo(address)
    if not report.usable:
        raise TugError(f"the team repo could not be reached, so nothing was proposed ({report.error})")
    clone = paths.team_repo_clone(address)
    base = teamcopy.approved_revision(clone)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"{BRANCH_PREFIX}{slugify(name or title)}-{stamp}"
    try:
        listed = git("ls-remote", "origin", f"refs/heads/{sync.BRANCH}", f"refs/heads/{branch}*", cwd=clone, timeout=sync.FETCH_TIMEOUT)
    except GitError as error:
        raise TugError(f"the team repo could not be reached, so nothing was proposed ({error.reason})") from error
    taken = {line.split("\t", 1)[1] for line in listed.splitlines() if "\t" in line}
    if f"refs/heads/{sync.BRANCH}" not in taken:
        raise TugError(f"the team repo has no '{sync.BRANCH}' branch, so nothing was proposed")
    # Two proposals in the same second must not share a branch.
    stem, count = branch, 1
    while f"refs/heads/{branch}" in taken:
        count += 1
        branch = f"{stem}-{count}"
    work = paths.tug_home() / "work"
    work.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(dir=work, prefix="proposal-"))
    tree = holder / "team"
    made_branch = False
    try:
        git("worktree", "prune", cwd=clone)
        git("worktree", "add", "--quiet", "-b", branch, os.fspath(tree), base, cwd=clone)
        made_branch = True
        before = _errors(tree)
        for path, content in changes.items():
            target = tree / path
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        new_problems = sorted(_errors(tree) - before)
        if new_problems:
            listed = "; ".join(f"{path}: {message}" for path, message in new_problems[:5])
            raise TugError(f"the change does not pass the team repo check, so nothing was proposed. {listed}")

        git("add", "--all", "--", *changes, cwd=tree)
        staged = [line for line in git("diff", "--cached", "--name-only", cwd=tree).splitlines() if line]
        if not staged:
            raise TugError("the team repo already has exactly this content, so there is nothing to propose")
        message = f"{title}\n\n{body}\n" if body else f"{title}\n"
        try:
            # Git must not make up a name from the machine's user and host name.
            git("-c", "user.useConfigOnly=true", "commit", "--quiet", "-m", message, cwd=tree)
        except GitError as error:
            if any(sign in error.reason for sign in ("auto-detection", "no email was given", "no name was given", "identity")):
                raise TugError("Git does not know your name and email, so it cannot commit. Set them with `git config --global user.name` and `user.email`.") from error
            raise
        commit = git("rev-parse", "HEAD", cwd=tree)
        # The full names of both ends, and a lease that expects no such branch:
        # this can only ever create the proposal branch, never move an existing one.
        git("push", "--quiet", f"--force-with-lease=refs/heads/{branch}:", "origin", f"refs/heads/{branch}:refs/heads/{branch}", cwd=tree, timeout=PUSH_TIMEOUT)
    finally:
        try:
            if tree.exists():
                git("worktree", "remove", "--force", os.fspath(tree), cwd=clone)
        except GitError:
            pass
        shutil.rmtree(holder, ignore_errors=True)
        try:
            git("worktree", "prune", cwd=clone)
            if made_branch:
                git("branch", "--quiet", "-D", branch, cwd=clone)
        except GitError:
            pass

    footer = f"Opened with Tugboat {installed_version()}. Nothing is merged automatically. A person reviews and merges this."
    link = f"https://{address.host}/{address.slug}/compare/{sync.BRANCH}...{branch}?expand=1"
    if gh is not None:
        program = gh
    elif "TUG_GH" in os.environ:
        program = os.environ["TUG_GH"]  # a path to use instead, or empty to open no pull request
    else:
        program = shutil.which("gh") or ""
    if not program:
        return Proposal(branch, commit, tuple(staged), None, link, "the GitHub command-line tool `gh` is not installed")
    try:
        url = _open_with_gh(address, branch, title, f"{body}\n\n{footer}" if body else footer, program)
    except GitError as error:
        return Proposal(branch, commit, tuple(staged), None, link, f"`gh` could not open it ({error.reason})")
    return Proposal(branch, commit, tuple(staged), url, link, None)


def render(proposal: Proposal) -> str:
    count = len(proposal.files)
    lines = [f"Pushed the branch {proposal.branch} with {count} file{'' if count == 1 else 's'}: {', '.join(proposal.files[:5])}{' and more' if count > 5 else ''}."]
    if proposal.pull_request:
        lines.append(f"Pull request: {proposal.pull_request}")
    else:
        lines.append(f"The pull request was not opened for you, because {proposal.note}.")
        lines.append(f"Open it here: {proposal.open_link}")
    lines.append("Nothing was merged. A person reviews and merges it in GitHub.")
    return "\n".join(lines)
