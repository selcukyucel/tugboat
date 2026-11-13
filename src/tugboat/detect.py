"""Find out what an app repo is, without asking.

`tug init` runs inside an existing app repo. It reads the repo's GitHub
address, its default branch, and whether it is an iOS or an Android project.
The developer confirms what was found. When something cannot be found, init
stops with the reason and changes nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from tugboat import apprepo
from tugboat.address import parse_remote_url
from tugboat.errors import TugError
from tugboat.gitrun import GitError, git

NETWORK_TIMEOUT = 15.0
MAX_GRADLE_FILES = 40
MAX_GRADLE_BYTES = 200 * 1024
# Folders that hold other people's projects, not this repo's own.
VENDORED = frozenset({"Pods", "Carthage", "node_modules", "vendor", ".build", "build", "DerivedData"})
GRADLE_NAMES = frozenset({"settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"})
PLATFORM_NAMES = {"ios": "iOS", "android": "Android"}


class Detection(NamedTuple):
    root: Path
    remote: str  # the name of the Git remote that was read
    host: str
    github: str  # owner/name
    default_branch: str
    branch_source: str  # how the default branch was found
    platform: str  # "ios" or "android"
    evidence: tuple[str, ...]  # the files that show the platform


def _remote(root: Path) -> tuple[str, str]:
    names = [name for name in git("remote", cwd=root).splitlines() if name.strip()]
    if not names:
        raise TugError("this repo has no Git remote, so its GitHub address is not known. Add the GitHub remote first.")
    if "origin" in names:
        name = "origin"
    elif len(names) == 1:
        name = names[0]
    else:
        raise TugError(f"this repo has several remotes and none is called 'origin' ({', '.join(sorted(names))}). Tugboat does not guess which one is GitHub.")
    # Read as written. `git remote get-url` would apply the developer's URL rewrites.
    return name, git("config", "--get", f"remote.{name}.url", cwd=root)


def _default_branch(root: Path, remote: str) -> tuple[str, str]:
    try:
        ref = git("symbolic-ref", "--short", "--quiet", f"refs/remotes/{remote}/HEAD", cwd=root)
        if ref.startswith(f"{remote}/"):
            return ref[len(remote) + 1 :], "from this clone's record of the remote"
    except GitError:
        pass
    try:
        for line in git("ls-remote", "--symref", remote, "HEAD", cwd=root, timeout=NETWORK_TIMEOUT).splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line[len("ref: refs/heads/") : -len("\tHEAD")], "asked from GitHub just now"
    except GitError as error:
        raise TugError(
            f"the default branch of this repo is not recorded in the clone, and GitHub could not be asked ({error.reason}). "
            f"Run this again when online, or run `git remote set-head {remote} --auto`."
        ) from error
    raise TugError(f"GitHub did not say what the default branch of this repo is. Run `git remote set-head {remote} --auto` and try again.")


def _tracked_files(root: Path) -> list[str]:
    return [path for path in git("ls-files", "-z", cwd=root, timeout=60).split("\0") if path]


def _xcode_bundles(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        parts = path.split("/")
        for index, part in enumerate(parts):
            if part in VENDORED:
                break
            if part.endswith((".xcodeproj", ".xcworkspace")):
                bundle = "/".join(parts[: index + 1])
                if bundle not in found:
                    found.append(bundle)
                break  # a workspace inside a project is part of that project
    return found


def _xcode_on_disk(root: Path) -> list[str]:
    """Xcode projects that exist but are not tracked, as when a tool generates them."""
    found = []
    for folder in (root, *(item for item in sorted(root.iterdir()) if item.is_dir() and item.name not in VENDORED and not item.name.startswith("."))):
        for item in sorted(folder.iterdir()):
            if item.is_dir() and item.name.endswith((".xcodeproj", ".xcworkspace")):
                found.append(item.relative_to(root).as_posix())
    return found


def _android_evidence(root: Path, paths: list[str]) -> tuple[list[str], bool]:
    """Return (evidence, has_gradle). Gradle alone is not Android: a backend can use Gradle too."""
    own = [path for path in paths if not VENDORED.intersection(path.split("/")[:-1])]
    gradle = [path for path in own if os.path.basename(path) in GRADLE_NAMES]
    if not gradle:
        return [], False
    evidence = [path for path in own if os.path.basename(path) == "AndroidManifest.xml"][:2]
    catalogs = [path for path in own if path.endswith("gradle/libs.versions.toml")]
    for path in (gradle + catalogs)[:MAX_GRADLE_FILES]:
        try:
            with (root / path).open("rb") as handle:
                if b"com.android." in handle.read(MAX_GRADLE_BYTES):
                    evidence.append(f"{path} (uses the Android Gradle plugin)")
                    break
        except OSError:
            continue
    return evidence, True


def detect(start: str | os.PathLike[str], *, platform: str | None = None) -> Detection:
    """Read what the repo at `start` is. Raise TugError, with the reason, when something cannot be found."""
    root = apprepo.find_app_repo(start)
    remote, url = _remote(root)
    address = parse_remote_url(url)
    if address is None:
        raise TugError(f"the remote '{remote}' of this repo is not a GitHub address that Tugboat can read. It must look like git@github.com:owner/name.git or https://github.com/owner/name.git.")

    paths = _tracked_files(root)
    ios = _xcode_bundles(paths)
    if not ios:
        ios = [f"{item} (on disk, not tracked by Git)" for item in _xcode_on_disk(root)]
    android, has_gradle = _android_evidence(root, paths)

    if ios and android:
        if platform is None:
            raise TugError(
                f"this repo has an iOS project ({ios[0]}) and an Android project ({android[0]}). "
                "Tugboat sets up one platform per repo. Run it again with --platform ios or --platform android."
            )
        evidence = ios if platform == "ios" else android
    elif ios or android:
        found = "ios" if ios else "android"
        if platform is not None and platform != found:
            raise TugError(f"--platform {platform} was given, but this repo only has {PLATFORM_NAMES[found]} project files. Nothing was changed.")
        platform, evidence = found, ios or android
    elif has_gradle:
        raise TugError("this repo has Gradle files, but nothing in them says it is an Android project: no AndroidManifest.xml and no Android Gradle plugin. Tugboat covers iOS and Android code only.")
    else:
        raise TugError("no iOS or Android project was found in this repo. Tugboat looked for an Xcode project or workspace, and for the Gradle files of an Android project.")

    branch, source = _default_branch(root, remote)
    return Detection(root, remote, address.host, address.slug, branch, source, platform, tuple(evidence[:3]))


def render(found: Detection) -> str:
    host = "" if found.host == "github.com" else f" on {found.host}"
    return "\n".join(
        (
            f"Repo folder:     {found.root}",
            f"GitHub address:  {found.github}{host} (from the remote '{found.remote}')",
            f"Default branch:  {found.default_branch} ({found.branch_source})",
            f"Platform:        {PLATFORM_NAMES[found.platform]} (found {', '.join(found.evidence)})",
        )
    )
