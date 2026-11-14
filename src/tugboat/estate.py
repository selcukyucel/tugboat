"""The estate map: the project's apps, their modules, and who owns each.

The map is `estate/estate.json` in the team repo. It answers one question
for the agent and for reports: who owns this file? A module names the paths
it covers. A path that no module covers belongs to the owner of the app.
The backend is not in the map.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo, formats, globmatch, teamcopy
from tugboat.errors import DataError, TugError
from tugboat.strict_json import read_json_file

FILE = "estate/estate.json"
COPY_PATH = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}/{FILE}"
MAX_LISTED = 30  # modules named in the instruction file. The rest are counted.


class Estate(NamedTuple):
    apps: list[dict[str, Any]] | None  # None when there is no map to use
    problem: str | None = None  # why it could not be read


class Owner(NamedTuple):
    owner: str
    app: str  # the app's id
    module: str | None  # the module's id. None when the path belongs to the app as a whole.
    pattern: str | None  # the module path that covers the file


def load(repo: Path) -> Estate:
    """Read the map from the app repo's copy of the team repo."""
    file = teamcopy.copy_path(repo) / FILE
    if file.is_symlink() or not file.is_file():
        return Estate(None)
    try:
        return Estate(formats.validate_estate(read_json_file(file))["apps"])
    except DataError as error:
        return Estate(None, f"the estate map {FILE} was not loaded: {str(error).replace(str(file), FILE)}")


def apps_of(apps: list[dict[str, Any]], github: str | None) -> list[dict[str, Any]]:
    """The apps that live in one app repo, named as owner/name."""
    return [app for app in apps if github and app["repo"].lower() == github.lower()]


def owners_of(apps: list[dict[str, Any]], path: str) -> list[Owner]:
    """Who owns one path, among the apps of its repo.

    The narrowest module path that covers the file wins. When no module
    covers it, the file belongs to the app's owner: one answer for each app
    in the repo, which is one answer in the usual case of one app per repo.
    """
    best: tuple[tuple[int, int], Owner] | None = None
    for app in apps:
        for module in app["modules"]:
            for pattern in module["paths"]:
                if not globmatch.matches(pattern, path):
                    continue
                rank = globmatch.specificity(pattern)
                if best is None or rank > best[0]:
                    best = (rank, Owner(module["owner"], app["id"], module["id"], pattern))
    if best is not None:
        return [best[1]]
    return [Owner(app["owner"], app["id"], None, None) for app in apps]


def repo_path(repo: Path, given: str, cwd: str | os.PathLike[str] = ".") -> str:
    """A path as typed, turned into a path from the top of the app repo. The file need not exist."""
    absolute = Path(os.path.normpath(Path(cwd).resolve() / given))
    try:
        relative = absolute.relative_to(repo)
    except ValueError as error:
        raise TugError(f"{given} is outside this repo") from error
    if relative == Path("."):
        raise TugError(f"{given} is the repo itself. Give a file or a folder inside it.")
    return relative.as_posix()


def owner_words(found: Owner) -> str:
    if found.module is None:
        return f"{found.owner} (owner of the app {found.app}. No module covers this path.)"
    return f"{found.owner} (module {found.module} of the app {found.app}, which covers {found.pattern})"


def render(estate: Estate, github: str | None) -> str:
    """The part of the instruction file that says who owns what in this repo."""
    lines = ["## Who owns what", ""]
    if estate.apps is None:
        lines.append(f"No estate map is in use. Not loaded: {estate.problem}." if estate.problem else "The team repo has no estate map, so no owner can be named.")
        return "\n".join(lines) + "\n"
    apps = apps_of(estate.apps, github)
    if not apps:
        lines.append(
            f"This repo is not in the estate map, `{COPY_PATH}`, so no owner can be named for its files. "
            "Say so when someone asks who owns a file. Do not guess an owner. A pull request in the team repo adds the repo to the map."
        )
        return "\n".join(lines) + "\n"
    lines.append(f"The estate map, `{COPY_PATH}`, lists what is in this repo and who owns it.")
    lines.append("")
    listed = 0
    for app in apps:
        environments = ", ".join(app["environments"]) or "none listed"
        lines.append(f"- App `{app['id']}` ({app['name']}): owner {app['owner']}. Environments: {environments}.")
        for module in app["modules"]:
            if listed < MAX_LISTED:
                lines.append(f"  - Module `{module['id']}`: owner {module['owner']}. Paths: {', '.join(f'`{path}`' for path in module['paths'])}")
            listed += 1
    if listed > MAX_LISTED:
        lines.append(f"  - and {listed - MAX_LISTED} more modules, in the file.")
    lines += [
        "",
        "To name the owner of a file, run `tug owner <path>`. It gives the module that covers the path and its owner.",
        "A path that no module covers belongs to the owner of the app. Name the owner the map gives. Do not guess one.",
    ]
    return "\n".join(lines) + "\n"
