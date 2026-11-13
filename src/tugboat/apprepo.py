"""An app repo, and the one local folder Tugboat owns inside it.

Everything Tugboat writes for itself in an app repo goes under `.tugboat/`.
The folder is listed in the repo's local exclude file, so Git never sees it
and no tracked file is touched.
"""

from __future__ import annotations

import os
from pathlib import Path

from tugboat.errors import TugError
from tugboat.gitrun import GitError, git

LOCAL_FOLDER = ".tugboat"
_BLOCK_START = "# >>> tugboat: files Tugboat generates. Edit nothing between these lines."
_BLOCK_END = "# <<< tugboat"


def find_app_repo(start: str | os.PathLike[str]) -> Path:
    """The top folder of the Git repo that holds `start`."""
    folder = Path(start)
    if not folder.is_dir():
        raise TugError(f"{folder} is not a folder")
    try:
        top = Path(git("rev-parse", "--show-toplevel", cwd=folder))
    except GitError as error:
        raise TugError(f"{folder} is not inside a Git repo") from error
    if (top / "tugboat.json").exists():
        raise TugError(f"{top} is a team repo, not an app repo. Nothing was written into it.")
    return top


def local_folder(repo: Path) -> Path:
    return repo / LOCAL_FOLDER


def _exclude_file(repo: Path) -> Path:
    # Asked from Git, because in a worktree the file is not under <repo>/.git.
    path = Path(git("rev-parse", "--git-path", "info/exclude", cwd=repo))
    return path if path.is_absolute() else repo / path


def excluded_patterns(repo: Path) -> list[str]:
    """The patterns in Tugboat's block of the local exclude file."""
    try:
        lines = _exclude_file(repo).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if _BLOCK_START not in lines:
        return []
    start = lines.index(_BLOCK_START)
    end = lines.index(_BLOCK_END, start) if _BLOCK_END in lines[start:] else len(lines)
    return [line for line in lines[start + 1 : end] if line.strip()]


def set_excluded_patterns(repo: Path, patterns: list[str]) -> bool:
    """Make Tugboat's block hold exactly these patterns. Every other line stays. Return True when the file changed."""
    file = _exclude_file(repo)
    try:
        before = file.read_text(encoding="utf-8")
    except OSError:
        before = ""
    lines = before.splitlines()
    if _BLOCK_START in lines:
        start = lines.index(_BLOCK_START)
        end = lines.index(_BLOCK_END, start) + 1 if _BLOCK_END in lines[start:] else len(lines)
        del lines[start:end]
    while lines and not lines[-1].strip():
        lines.pop()
    if patterns:
        lines.extend(([""] if lines else []) + [_BLOCK_START, *patterns, _BLOCK_END])
    after = "\n".join(lines) + ("\n" if lines else "")
    if after == before:
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(after, encoding="utf-8")
    return True


def ensure_excluded(repo: Path, pattern: str) -> bool:
    """Add one pattern to Tugboat's block if it is not there."""
    current = excluded_patterns(repo)
    if pattern in current:
        return False
    return set_excluded_patterns(repo, [*current, pattern])
