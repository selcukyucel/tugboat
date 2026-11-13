"""The read-only copy of team repo content inside an app repo.

The agent reads team content only from this copy, so no agent tool has to
read outside its workspace. Three rules:

- The copy is taken from the last approved revision that sync fetched
  (`origin/main`), never from the clone's working files. A local edit or a
  local commit in the clone cannot reach the agent.
- The copy is rewritten only when the team repo revision or the tool
  version changed. Otherwise no file is touched.
- The files are read-only. A change to team content goes to the team repo
  as a pull request.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo, paths
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError
from tugboat.gitrun import GitError, git, git_environment
from tugboat.strict_json import pretty_json, read_json_file
from tugboat.sync import BRANCH
from tugboat.validate import require_exact_fields, require_object, utc_now, validate_relative_path
from tugboat.version import installed_version

STATE_SCHEMA = "tug.app-state/1"
COPY_FOLDER = "team"
STATE_FILE = "state.json"
# The parts of a team repo an agent needs. Anything else stays in the clone.
COPIED = ("tugboat.json", "profile.json", "policies", "permissions", "knowledge", "decisions", "estate", "agents", "skills")
MAX_FILES = 5000
MAX_BYTES = 50 * 1024 * 1024
ARCHIVE_TIMEOUT = 60.0


class CopyReport(NamedTuple):
    path: Path  # where the copy is
    refreshed: bool
    reason: str  # why it was refreshed, or why it was not
    revision: str  # the team repo revision the copy holds now
    previous_revision: str | None  # what it held before, when that differs
    files: int
    skipped: tuple[str, ...]  # entries that were not copied, such as links


def copy_path(repo: Path) -> Path:
    return apprepo.local_folder(repo) / COPY_FOLDER


def read_state(repo: Path) -> dict[str, Any] | None:
    """What the last refresh recorded for this app repo. None when missing or broken."""
    try:
        state = require_object(read_json_file(apprepo.local_folder(repo) / STATE_FILE), "app state")
        require_exact_fields(
            state, required=frozenset({"schema", "team_repo", "revision", "tool_version", "refreshed_at", "files"}), label="app state"
        )
        if state["schema"] != STATE_SCHEMA:
            return None
        return dict(state)
    except DataError:
        return None


def approved_revision(clone: Path) -> str:
    """The newest approved revision that sync has fetched."""
    try:
        return git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{BRANCH}^{{commit}}", cwd=clone)
    except GitError as error:
        raise TugError(f"the clone at {clone} has no fetched '{BRANCH}' yet. Run sync first.") from error


def _archive(clone: Path, revision: str) -> bytes:
    present = set(git("ls-tree", "--name-only", revision, cwd=clone).splitlines())
    wanted = [name for name in COPIED if name in present]
    if not wanted:
        return b""
    try:
        done = subprocess.run(
            ["git", "archive", "--format=tar", revision, "--", *wanted],
            cwd=clone, env=git_environment(), stdin=subprocess.DEVNULL, capture_output=True, timeout=ARCHIVE_TIMEOUT, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired as error:
        raise GitError("archive", f"gave no answer within {ARCHIVE_TIMEOUT:g} seconds") from error
    if done.returncode != 0:
        raise GitError("archive", done.stderr.decode("utf-8", "replace").strip()[:300] or "failed")
    return done.stdout


def _write_copy(archive: bytes, target: Path) -> tuple[int, tuple[str, ...]]:
    """Write the regular files of a Git archive under target. Nothing else is created."""
    count = total = 0
    skipped: list[str] = []
    target.mkdir(parents=True)
    if not archive:
        return 0, ()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar:
            if member.isdir() or member.name == "pax_global_header":
                continue
            if not member.isreg():
                # A link, or anything else that is not plain content, is left out.
                skipped.append(member.name)
                continue
            name = validate_relative_path(member.name, "team repo file path")
            count += 1
            total += member.size
            if count > MAX_FILES or total > MAX_BYTES:
                raise TugError(f"the team repo content is larger than {MAX_FILES} files or {MAX_BYTES // (1024 * 1024)} MB. Nothing was copied.")
            source = tar.extractfile(member)
            if source is None:
                skipped.append(member.name)
                continue
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source, destination.open("wb") as file:
                shutil.copyfileobj(source, file)
            destination.chmod(0o444)
    return count, tuple(sorted(skipped))


def _write_text(path: Path, text: str) -> None:
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _readme(address: TeamRepoAddress, revision: str) -> str:
    return (
        "# .tugboat\n\n"
        "Tugboat generated this folder. Do not edit it. Git ignores it.\n\n"
        f"- `{COPY_FOLDER}/` is a read-only copy of the team repo `{address.url}` at revision `{revision}`.\n"
        "- To change a rule, a note, or a permission, open a pull request in the team repo. An edit here is lost at the next sync.\n"
        f"- `{STATE_FILE}` records which revision and which tool version wrote the copy.\n"
    )


def refresh_copy(repo: Path, address: TeamRepoAddress, *, force: bool = False) -> CopyReport:
    """Bring the copy in one app repo in line with the fetched team repo. Needs no network."""
    clone = paths.team_repo_clone(address)
    revision = approved_revision(clone)
    version = str(installed_version())
    target = copy_path(repo)
    state = read_state(repo)

    if force:
        reason = "a refresh was asked for"
    elif state is None:
        reason = "this repo had no copy yet"
    elif not target.is_dir():
        reason = "the copy was missing"
    elif state["team_repo"] != address.url:
        reason = "the team repo address changed"
    elif state["revision"] != revision:
        reason = "the team repo has a new revision"
    elif state["tool_version"] != version:
        reason = f"the tool changed from version {state['tool_version']} to {version}"
    else:
        return CopyReport(target, False, "nothing changed", revision, None, int(state["files"]), ())

    folder = apprepo.local_folder(repo)
    folder.mkdir(exist_ok=True)
    apprepo.ensure_excluded(repo, f"/{apprepo.LOCAL_FOLDER}/")
    # Build the new copy beside the old one, then swap, so a reader never sees half a copy.
    staging = Path(tempfile.mkdtemp(dir=folder, prefix=COPY_FOLDER + ".", suffix=".new"))
    retired: Path | None = None
    try:
        files, skipped = _write_copy(_archive(clone, revision), staging / COPY_FOLDER)
        if target.exists():
            retired = Path(tempfile.mkdtemp(dir=folder, prefix=COPY_FOLDER + ".", suffix=".old")) / COPY_FOLDER
            os.replace(target, retired)
        try:
            os.replace(staging / COPY_FOLDER, target)
        except BaseException:
            if retired is not None:
                os.replace(retired, target)  # put the old copy back
            raise
    finally:
        # The files are read-only, but the folders are ours, so they can be removed.
        shutil.rmtree(staging, ignore_errors=True)
        if retired is not None:
            shutil.rmtree(retired.parent, ignore_errors=True)

    _write_text(folder / "README.md", _readme(address, revision))
    _write_text(
        folder / STATE_FILE,
        pretty_json(
            {"schema": STATE_SCHEMA, "team_repo": address.url, "revision": revision, "tool_version": version, "refreshed_at": utc_now(), "files": files}
        ),
    )
    previous = state["revision"] if state and state["revision"] != revision else None
    return CopyReport(target, True, reason, revision, previous, files, skipped)


def render(report: CopyReport, repo: Path) -> str:
    where = report.path.relative_to(repo).as_posix()
    short = report.revision[:7]
    if not report.refreshed:
        return f"Copy in this repo: unchanged at revision {short} ({report.files} files in {where}/)."
    lines = [f"Copy in this repo: refreshed to revision {short} ({report.files} files in {where}/), because {report.reason}."]
    if report.skipped:
        lines.append(f"Note: {len(report.skipped)} entr{'y was' if len(report.skipped) == 1 else 'ies were'} not copied because they are not plain files: {', '.join(report.skipped[:3])}.")
    return "\n".join(lines)
