"""Check a team repo folder, and write the starter layout into a new one."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import formats, frontmatter
from tugboat.errors import DataError, TugError
from tugboat.strict_json import read_json_file
from tugboat.version import installed_version

MAX_MARKDOWN_BYTES = 512 * 1024
MARKER = "tugboat.json"
PROFILE = "profile.json"
# Folder READMEs explain a format. They are never read as a rule or a note.
SKIPPED_NAMES = frozenset({"README.md"})


class Problem(NamedTuple):
    path: str
    message: str
    level: str  # "error" or "warning"


class Report(NamedTuple):
    files_checked: int
    problems: list[Problem]

    @property
    def errors(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.level == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.level == "warning"]


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_MARKDOWN_BYTES + 1)
    except OSError as error:
        raise DataError(f"cannot be read: {error.strerror or error}") from error
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise DataError(f"is larger than {MAX_MARKDOWN_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DataError("is not valid UTF-8") from error
    return frontmatter.parse(text)


def _markdown_files(folder: Path) -> Iterator[Path]:
    if not folder.is_dir():
        return
    for current, folders, files in os.walk(folder, followlinks=False):
        folders.sort()
        for name in sorted(files):
            if name.endswith(".md") and name not in SKIPPED_NAMES:
                yield Path(current) / name


class _Checker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.problems: list[Problem] = []
        self.files_checked = 0

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def error(self, path: Path | str, message: str) -> None:
        self.problems.append(Problem(path if isinstance(path, str) else self.rel(path), message, "error"))

    def warning(self, path: Path | str, message: str) -> None:
        self.problems.append(Problem(path if isinstance(path, str) else self.rel(path), message, "warning"))

    def attempt(self, path: Path, read: Callable[[], Any]) -> Any | None:
        """Run one check on one file. Record a problem instead of stopping."""
        self.files_checked += 1
        if path.is_symlink():
            self.error(path, "is a symbolic link. Links are not allowed in a team repo.")
            return None
        try:
            return read()
        except DataError as error:
            self.error(path, str(error))
            return None

    def json_file(self, name: str, validate: Callable[[object], dict[str, Any]], *, required: bool = True) -> dict[str, Any] | None:
        path = self.root / name
        if not path.exists() and not path.is_symlink():
            if required:
                self.error(name, "is missing")
            return None
        return self.attempt(path, lambda: validate(read_json_file(path)))

    def markdown(self, path: Path, validate: Callable[[dict[str, Any], str], dict[str, Any]]) -> dict[str, Any] | None:
        return self.attempt(path, lambda: validate(*_read_markdown(path)))


def check_team_repo(root: str | os.PathLike[str]) -> Report:
    """Check every file Tugboat reads in a team repo. Never raises for bad content."""
    base = Path(root)
    if not base.is_dir():
        raise TugError(f"{base} is not a folder")
    check = _Checker(base.resolve())
    root_path = check.root

    if check.json_file(MARKER, formats.validate_marker) is None:
        if not check.problems or check.problems[-1].message == "is missing":
            check.problems[-1:] = [Problem(MARKER, "is missing. This folder is not a Tugboat team repo.", "error")]
        return Report(check.files_checked, check.problems)

    if not (root_path / PROFILE).exists():
        check.warning(PROFILE, "is not there yet. `tug init` creates it.")
    else:
        check.json_file(PROFILE, formats.validate_profile)
    check.json_file("permissions/protected-paths.json", formats.validate_protected_paths)
    check.json_file("permissions/ai-tools.json", formats.validate_ai_tools)
    check.json_file("estate/estate.json", formats.validate_estate)

    rule_ids: dict[str, str] = {}
    for scope in formats.RULE_SCOPES:
        for path in _markdown_files(root_path / "policies" / scope):
            rule = check.markdown(path, formats.validate_rule)
            if rule is None:
                continue
            if rule["scope"] != scope:
                check.error(path, f"has scope '{rule['scope']}' and must be in policies/{rule['scope']}/")
            if path.name != f"{rule['id']}.md":
                check.error(path, f"must be named {rule['id']}.md, after its id")
            if rule["id"] in rule_ids:
                check.error(path, f"uses rule id {rule['id']}, which {rule_ids[rule['id']]} already uses")
            else:
                rule_ids[rule["id"]] = check.rel(path)

    for path in _markdown_files(root_path / "knowledge"):
        check.markdown(path, formats.validate_note)

    decision_ids: dict[str, str] = {}
    for path in _markdown_files(root_path / "decisions"):
        decision = check.markdown(path, formats.validate_decision)
        if decision is None:
            continue
        if not path.name.startswith(decision["id"]):
            check.error(path, f"must have a file name that starts with {decision['id']}")
        if decision["id"] in decision_ids:
            check.error(path, f"uses id {decision['id']}, which {decision_ids[decision['id']]} already uses")
        else:
            decision_ids[decision["id"]] = check.rel(path)

    for path in _markdown_files(root_path / "agents"):
        agent = check.markdown(path, formats.validate_agent)
        if agent is None:
            continue
        if path.name != f"{agent['name']}.md":
            check.error(path, f"must be named {agent['name']}.md, after its name")
        for rule_id in agent["rules"]:
            if rule_id not in rule_ids:
                check.error(path, f"names rule {rule_id}, which is not in policies/")

    skills = root_path / "skills"
    if skills.is_dir():
        for folder in sorted(item for item in skills.iterdir() if item.is_dir()):
            path = folder / "SKILL.md"
            if not path.exists():
                check.error(folder, "has no SKILL.md")
                continue
            skill = check.attempt(path, lambda path=path: formats.validate_skill_header(_read_markdown(path)[0]))
            if skill is not None and skill["name"] != folder.name:
                check.error(path, f"has name '{skill['name']}' and must be in a folder with that name")

    order = {"error": 0, "warning": 1}
    return Report(check.files_checked, sorted(check.problems, key=lambda problem: (order[problem.level], problem.path)))


# ------------------------------------------------------------ starter layout


def _starter_files(folder: Traversable, prefix: str = "") -> Iterator[tuple[str, Traversable]]:
    for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
        if item.name.startswith(("__", ".")):
            continue
        # A folder cannot start with a dot inside the package, so ".github" is stored as "dot-github".
        name = "." + item.name[4:] if item.name.startswith("dot-") else item.name
        if item.is_dir():
            yield from _starter_files(item, f"{prefix}{name}/")
        else:
            yield f"{prefix}{name}", item


def starter_layout() -> dict[str, str]:
    """The starter files as {path: text}, with the tool's release tag filled in."""
    tag = installed_version().tag
    source = resources.files("tugboat").joinpath("starter")
    return {path: item.read_text(encoding="utf-8").replace("{{TUG_TAG}}", tag) for path, item in _starter_files(source)}


def new_team_repo(target: str | os.PathLike[str]) -> list[str]:
    """Write the starter layout into an empty folder. An existing .git folder is fine."""
    folder = Path(target)
    if folder.exists() and not folder.is_dir():
        raise TugError(f"{folder} is not a folder")
    if folder.is_dir():
        present = sorted(item.name for item in folder.iterdir() if item.name != ".git")
        if present:
            raise TugError(f"{folder} is not empty (found {present[0]}). The starter layout is only written into an empty folder.")
    files = starter_layout()
    for path, text in files.items():
        destination = folder / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return sorted(files)
