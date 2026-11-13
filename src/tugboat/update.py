"""`tug update`: install the newest release of Tugboat.

The tool never updates itself. It updates when the developer runs this
command, and only to a tagged release from the tool's own repo, installed
with uv, the way the tool was installed the first time.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: F401 - loaded now: after the install, this program's own files are replaced
from pathlib import Path

import tugboat
from tugboat import releases
from tugboat.errors import EXIT_ERROR, EXIT_OK, TugError
from tugboat.gitrun import GitError, run_process
from tugboat.version import Version, installed_version

PACKAGE = "tugboat"
INSTALL_TIMEOUT = 300.0


def find_uv() -> str | None:
    return shutil.which("uv")


def package_folder() -> Path:
    return Path(tugboat.__file__).resolve().parent


def _by_hand(version: Version) -> str:
    return "uv " + " ".join(releases.install_command(version))


def installed_by_uv(uv: str) -> bool:
    """True when this running tool sits in uv's tool folder, so uv can replace it."""
    try:
        tools = Path(run_process([uv, "tool", "dir"], action="uv tool dir").strip()).resolve()
    except GitError:
        return False
    return tools in package_folder().parents


def _listed_version(uv: str) -> str | None:
    """The version uv says is installed now."""
    try:
        listed = run_process([uv, "tool", "list"], action="uv tool list")
    except GitError:
        return None
    for line in listed.splitlines():
        name, _, version = line.strip().partition(" ")
        if name == PACKAGE and version.startswith("v"):
            return version[1:]
    return None


def run(*, check_only: bool = False) -> int:
    installed = installed_version()
    try:
        versions = releases.read_releases()
    except GitError as error:
        raise TugError(f"the releases of Tugboat could not be read from {releases.TOOL_REPO} ({error.reason}). Nothing was changed.") from error
    if not versions:
        raise TugError(f"{releases.TOOL_REPO} has no release yet. Nothing was changed.")
    newest = versions[-1]
    releases.remember(newest)

    if newest == installed:
        print(f"tug {installed} is the newest release. Nothing to do.")
        return EXIT_OK
    if newest < installed:
        print(f"This tug is {installed}, which is newer than the newest release, {newest}. Nothing to do.")
        return EXIT_OK
    if check_only:
        print(f"tug {installed} is installed. The newest release is {newest}. `tug update` installs it.")
        return EXIT_OK

    uv = find_uv()
    if uv is None:
        raise TugError(f"uv was not found, so the update cannot run. With uv installed, the command is: {_by_hand(newest)}")
    if not installed_by_uv(uv):
        raise TugError(
            f"this tug runs from {package_folder()}, which uv did not install as a tool, so it was left as it is. "
            f"To install the release with uv, run: {_by_hand(newest)}"
        )

    print(f"Updating tug from {installed} to {newest} with: {_by_hand(newest)}")
    try:
        run_process([uv, *releases.install_command(newest)], action="uv tool install", timeout=INSTALL_TIMEOUT)
    except GitError as error:
        raise TugError(f"uv could not install the release ({error.reason}). The installed tug may be unchanged. Run `tug version` to see.") from error
    # From here on this program's own files are the new release. Do as little as possible.
    listed = _listed_version(uv)
    if listed != str(newest):
        print(f"uv finished, but it lists tug {listed or 'as not installed'}, not {newest}. Run `tug version` to see what is installed.")
        return EXIT_ERROR
    print(f"Updated tug to {newest}. The next sync in each repo regenerates the agent tool files.")
    return EXIT_OK
