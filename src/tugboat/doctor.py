"""The setup check, on disk: is this machine and this app repo ready?

`tug doctor` reads files and asks installed programs for their version. It
changes nothing and needs no network. Every check ends in one of four
results, and they are reported apart:

- passed: it was checked and it is right
- failed: it was checked and it is wrong. The line says how to repair it.
- untested: it could not be checked from here. This is never shown as a pass.
- advisory: something to look at. It does not make the machine "not ready".

What happens inside an agent session is not seen from here: whether a hook
really fires, or whether a connection answers. That is the in-session check.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from tugboat import aitools, apprepo, appsync, aiuse, detect, hostfiles, observed, paths, permissions, profile, releases, settingsmerge, sync, teamcopy, update
from tugboat.address import TeamRepoAddress, parse_team_repo_address
from tugboat.errors import DataError, TugError
from tugboat.gitrun import GitError, git
from tugboat.strict_json import parse_json
from tugboat.version import installed_version, parse_version

PASSED, FAILED, UNTESTED, ADVISORY = "passed", "failed", "untested", "advisory"
DISK, SESSION = "disk", "session"  # where a result comes from: files on disk, or what a session showed
SKILL = "tug-setup-check"
OBSERVE = "tug doctor observe"
STALE_DAYS = 30
ORDER = ((FAILED, "Failed"), (UNTESTED, "Untested"), (ADVISORY, "Advisory"), (PASSED, "Passed"))
MIN_CLAUDE_CODE = (2, 1, 295)  # the first version that knows "onFailure" in a hook entry
PROGRAM_TIMEOUT = 15.0
MAX_CONFIG_BYTES = 8 * 1024 * 1024
MAX_NAMED = 8  # files or servers named in one line. The rest are counted.
AGENT_FOLDERS = ((".claude/agents", ".md"), (".github/agents", ".agent.md"))
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


class Check(NamedTuple):
    result: str  # PASSED, FAILED, UNTESTED, or ADVISORY
    text: str  # what was found, in one or two sentences
    fix: str = ""  # what repairs it
    area: str = DISK  # DISK: read from files. SESSION: seen inside an agent session.


class Report(NamedTuple):
    repo: Path | None  # the app repo that was checked. None when the command ran outside one.
    heading: tuple[str, ...]
    checks: tuple[Check, ...]

    @property
    def failed(self) -> int:
        return sum(check.result == FAILED for check in self.checks)


class Server(NamedTuple):
    name: str  # the name the developer gave it. Shown, never used to match.
    where: str  # the settings file it is in, in words
    url: str | None
    command: tuple[str, ...]  # the program and its arguments. Used to match, never shown.

    @property
    def known_by(self) -> str:
        """What identifies the server, without any argument, header, or query that could hold a secret."""
        if self.url:
            return f"host {(urlsplit(self.url).hostname or 'unknown').lower()}"
        return f"program {os.path.basename(self.command[0])}" if self.command else "no address"


# ---- seams that the tests replace


def user_home() -> Path:
    return Path.home()


def find_program(name: str) -> str | None:
    return shutil.which(name)


def program_output(argv: list[str], timeout: float = PROGRAM_TIMEOUT) -> tuple[bool, str]:
    """Run one program that prints its version. Return (it worked, its first line). Never raises."""
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"gave no answer within {timeout:g} seconds"
    except OSError as error:
        return False, f"could not start: {error.strerror or error}"
    lines = [line.strip() for line in (done.stdout + "\n" + done.stderr).splitlines() if line.strip()]
    return done.returncode == 0, (lines[0] if lines else "no output")[:200].rstrip(".")


# ---- small helpers


def _numbers(text: str) -> tuple[int, int, int] | None:
    found = _VERSION.search(text)
    return (int(found[1]), int(found[2]), int(found[3] or 0)) if found else None


def _loose_json(path: Path) -> Any:
    """Read a settings file of another program. Such files may start with comment lines. Raises DataError."""
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise DataError(f"{path.name} is too large to read")
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DataError(f"{path.name} could not be read") from error
    try:
        return parse_json(raw)
    except DataError:
        return parse_json("\n".join("" if line.lstrip().startswith("//") else line for line in raw.splitlines()))


def _named(items: list[str]) -> str:
    more = f", and {len(items) - MAX_NAMED} more" if len(items) > MAX_NAMED else ""
    return ", ".join(items[:MAX_NAMED]) + more


def _count(number: int, word: str) -> str:
    return f"{number} {word}{'' if number == 1 else 's'}"


def _short_home(path: Path) -> str:
    try:
        return f"~/{path.relative_to(user_home())}"
    except ValueError:
        return os.fspath(path)


# ---- the machine


def check_tool_version(approved: dict[str, Any] | None) -> list[Check]:
    installed = installed_version()
    newest = releases.newest_seen(ask=False)
    seen = f"The newest release this machine has seen is {newest}." if newest else "This machine has not looked up the releases yet; a sync does."
    if approved is None:
        return [Check(PASSED, f"tug {installed} is installed. {seen}")]
    minimum = parse_version(approved["minimum_tool_version"])
    if profile.below_minimum(approved):
        return [Check(FAILED, f"tug {installed} is installed, and the project needs {minimum} or newer. {seen}", "Run `tug update`.")]
    newer = f" A newer release exists: `tug update` installs it." if newest is not None and newest > installed else ""
    return [Check(PASSED, f"tug {installed} is installed. The project needs {minimum} or newer. {seen}{newer}")]


def check_uv() -> list[Check]:
    uv = update.find_uv()
    if uv is None:
        return [Check(FAILED, "uv is not on the PATH, so `tug update` cannot run.", "Install uv: https://docs.astral.sh/uv/getting-started/installation/")]
    worked, line = program_output([uv, "--version"])
    checks = [Check(PASSED, f"uv is installed ({line}).") if worked else Check(FAILED, f"`uv --version` did not work: {line}.", "Install uv again.")]
    if worked and not update.installed_by_uv(uv):
        checks.append(Check(ADVISORY, "This `tug` was not installed with `uv tool install`, so `tug update` will not replace it."))
    return checks


def check_claude_code() -> list[Check]:
    needed = ".".join(str(part) for part in MIN_CLAUDE_CODE)
    program = find_program("claude")
    if program is None:
        return [Check(UNTESTED, f"Claude Code (`claude`) was not found on the PATH. If you use it, it must be {needed} or newer.")]
    worked, line = program_output([program, "--version"])
    version = _numbers(line) if worked else None
    if version is None:
        return [Check(UNTESTED, f"The version of Claude Code could not be read (`claude --version`: {line}). It must be {needed} or newer.")]
    found = ".".join(str(part) for part in version)
    if version < MIN_CLAUDE_CODE:
        return [
            Check(
                FAILED,
                f"Claude Code {found} is installed, and Tugboat needs {needed} or newer. An older version lets an action through when the check before it cannot run.",
                "Update Claude Code.",
            )
        ]
    return [Check(PASSED, f"Claude Code {found} is installed ({needed} or newer is needed).")]


def copilot_home() -> Path:
    override = os.environ.get("COPILOT_HOME")
    return Path(override) if override else user_home() / ".copilot"


def check_copilot_cli(repo: Path | None) -> list[Check]:
    program = find_program("copilot")
    if program is None:
        return [Check(UNTESTED, "GitHub Copilot CLI (`copilot`) was not found on the PATH. If you use it, install it.")]
    worked, line = program_output([program, "--version"])
    checks = [Check(PASSED, f"GitHub Copilot CLI is installed ({line.rstrip('.')}).") if worked else Check(UNTESTED, f"`copilot --version` did not work: {line}.")]
    if repo is None:
        return checks
    config = copilot_home() / "config.json"
    fix = f"Start `copilot` in this folder and choose to trust it and remember it. Or add the folder to `trustedFolders` in {_short_home(config)}."
    try:
        data = _loose_json(config) if config.exists() else {}
        folders = data.get("trustedFolders", []) if isinstance(data, dict) else None
        if not isinstance(folders, list):
            raise DataError("trustedFolders is not a list")
    except DataError as error:
        return [*checks, Check(UNTESTED, f"Whether Copilot CLI trusts this folder could not be read ({error}). In a folder it does not trust, Copilot runs no hook.")]
    trusted = {os.path.normpath(folder) for folder in folders if isinstance(folder, str)}
    here = {os.path.normpath(os.fspath(repo)), os.path.normpath(os.fspath(repo.resolve()))}
    if trusted & here:
        return [*checks, Check(PASSED, "Copilot CLI trusts this folder, so it loads the hooks.")]
    parents = {os.fspath(parent) for folder in (repo, repo.resolve()) for parent in folder.parents}
    if trusted & parents:
        return [*checks, Check(UNTESTED, "Copilot CLI trusts a folder above this one. Whether that covers this folder is not known. Ask the agent in Copilot for the Tugboat sync report.")]
    return [*checks, Check(FAILED, "Copilot CLI does not trust this folder, so it runs no hook here: no sync at the start of a session, and no check before an action.", fix)]


def check_vscode(repo: Path | None) -> list[Check]:
    program = find_program("code")
    if program is None:
        checks = [Check(UNTESTED, "The `code` command of VS Code was not found on the PATH, so its version was not read. VS Code may still be installed.")]
    else:
        worked, line = program_output([program, "--version"])
        checks = [Check(PASSED, f"VS Code is installed ({line}).") if worked else Check(UNTESTED, f"`code --version` did not work: {line}.")]
    if repo is not None:
        checks.append(
            Check(
                UNTESTED,
                "Whether VS Code trusts this workspace cannot be read from disk. In a workspace it does not trust, no hook runs and nothing reports it. "
                f"The check inside a session shows it: ask the agent in VS Code to run the skill `{SKILL}`.",
            )
        )
    return checks


def check_toolchain(repo: Path, platform: str | None) -> list[Check]:
    if platform == "ios":
        program = find_program("xcodebuild")
        fix = "Install Xcode, open it once, and select it: `sudo xcode-select -s /Applications/Xcode.app`."
        if program is None:
            return [Check(FAILED, "iOS toolchain: `xcodebuild` was not found, so the app cannot be built or tested from a terminal.", fix)]
        worked, line = program_output([program, "-version"])
        return [Check(PASSED, f"iOS toolchain: {line} is selected.") if worked else Check(FAILED, f"iOS toolchain: `xcodebuild -version` did not work: {line}.", fix)]
    if platform == "android":
        return [_check_gradle_wrapper(repo), _check_java(), _check_android_sdk(repo)]
    return [Check(UNTESTED, "The platform toolchain was not checked: this repo is not in the project profile, so its platform is not known.")]


def _check_gradle_wrapper(repo: Path) -> Check:
    if (repo / "gradlew").is_file():
        return Check(PASSED, "Android toolchain: the Gradle wrapper `gradlew` is in the repo.")
    return Check(FAILED, "Android toolchain: the repo has no Gradle wrapper `gradlew`, so the app cannot be built the same way on every machine.", "Add the wrapper: `gradle wrapper`, then commit it.")


def _check_java() -> Check:
    fix = "Install a JDK, or set JAVA_HOME to the one Android Studio ships with."
    home = os.environ.get("JAVA_HOME")
    program = os.path.join(home, "bin", "java") if home else find_program("java")
    if home and not os.path.isfile(program or ""):
        return Check(FAILED, f"Android toolchain: JAVA_HOME is set to {home}, and there is no `bin/java` in it.", fix)
    if program is None:
        return Check(FAILED, "Android toolchain: no Java was found, so `./gradlew` cannot run in a terminal, where the agent builds.", fix)
    worked, line = program_output([program, "-version"])
    source = "from JAVA_HOME" if home else "from the PATH"
    if not worked:
        return Check(FAILED, f"Android toolchain: `java -version` did not work ({source}): {line}.", fix)
    return Check(PASSED, f"Android toolchain: Java answers ({line}, {source}).")


def _check_android_sdk(repo: Path) -> Check:
    fix = "Open the project once in Android Studio, which writes `local.properties`, or set ANDROID_HOME."
    try:
        lines = (repo / "local.properties").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and name.strip() == "sdk.dir" and value.strip():
            folder = value.strip().replace("\\:", ":").replace("\\\\", "\\")
            if os.path.isdir(folder):
                return Check(PASSED, "Android toolchain: the Android SDK named in `local.properties` is there.")
            return Check(FAILED, "Android toolchain: `local.properties` names an Android SDK folder that does not exist.", fix)
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        folder = os.environ.get(variable)
        if folder:
            if os.path.isdir(folder):
                return Check(PASSED, f"Android toolchain: the Android SDK named in {variable} is there.")
            return Check(FAILED, f"Android toolchain: {variable} names an Android SDK folder that does not exist.", fix)
    return Check(FAILED, "Android toolchain: no Android SDK is named, in `local.properties` or in ANDROID_HOME.", fix)


# ---- the app repo


def check_clone(address: TeamRepoAddress) -> list[Check]:
    clone = paths.team_repo_clone(address)
    if not (clone / ".git").exists():
        return [Check(FAILED, f"This machine has no clone of the team repo {address.slug}.", "Run `tug sync` in this repo.")]
    state = sync.read_state(address)
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=clone)
        revision = teamcopy.approved_revision(clone)
        edits = [line for line in git("status", "--porcelain", cwd=clone).splitlines() if line.strip()]
    except TugError as error:
        return [Check(FAILED, f"The clone of the team repo at {_short_home(clone)} could not be read: {error}.", "Run `tug sync` in this repo.")]
    worked = f"The last sync that worked was {sync.age(state['last_success_at'])}." if state["last_success_at"] else "No sync has worked yet."
    checks = []
    if branch != sync.BRANCH:
        checks.append(Check(FAILED, f"The clone of the team repo is on the branch '{branch}', so sync leaves it alone and it gets no update.", f"In {_short_home(clone)}, switch back: `git switch {sync.BRANCH}`."))
    if state["last_error"]:
        checks.append(Check(FAILED, f"The last sync did not reach the team repo: {state['last_error']}. {worked}", "Check your GitHub access, then run `tug sync`."))
    if not checks:
        checks.append(Check(PASSED, f"The clone of the team repo {address.slug} is on '{sync.BRANCH}' at revision {revision[:7]}. {worked}"))
    if edits:
        checks.append(Check(ADVISORY, f"The clone of the team repo has {_count(len(edits), 'local edit')}. They never reach an app repo, and one in the way of an update stops the clone from moving. Folder: {_short_home(clone)}."))
    return checks


def check_copy(repo: Path, address: TeamRepoAddress, state: dict[str, Any]) -> list[Check]:
    fix = "Run `tug sync` in this repo."
    if not teamcopy.copy_path(repo).is_dir():
        return [Check(FAILED, "The copy of the team content, `.tugboat/team/`, is missing.", fix)]
    try:
        newest = teamcopy.approved_revision(paths.team_repo_clone(address))
    except TugError:
        return [Check(UNTESTED, "Whether the copy in `.tugboat/team/` is the newest could not be told, because the clone of the team repo could not be read.")]
    if state["revision"] != newest:
        return [Check(FAILED, f"The copy in `.tugboat/team/` holds revision {str(state['revision'])[:7]}, and this machine has fetched {newest[:7]}.", fix)]
    if state["tool_version"] != str(installed_version()):
        return [Check(FAILED, f"The copy and the agent tool files were written by tug {state['tool_version']}, and this is tug {installed_version()}.", fix)]
    return [Check(PASSED, f"The copy in `.tugboat/team/` holds revision {newest[:7]}, the newest this machine has fetched.")]


def check_host_files(repo: Path) -> list[Check]:
    fix = "Run `tug sync` in this repo. It writes them again."
    manifest = hostfiles.read_manifest(repo)
    if not manifest:
        return [Check(FAILED, "No agent tool file is recorded for this repo, so the agent tools load nothing of Tugboat's.", fix)]
    wrong = []
    for path, digest in sorted(manifest.items()):
        file = repo / path
        try:
            same = not file.is_symlink() and hashlib.sha256(file.read_bytes()).hexdigest() == digest
        except OSError:
            same = False
        if not same:
            wrong.append(path)
    if wrong:
        return [Check(FAILED, f"{_count(len(wrong), 'agent tool file')} under `.claude/` {'is' if len(wrong) == 1 else 'are'} missing or changed: {_named(wrong)}.", fix)]
    return [Check(PASSED, f"The {_count(len(manifest), 'agent tool file')} under `.claude/` are as Tugboat wrote them.")]


def _settings(repo: Path) -> dict[str, Any] | None:
    try:
        data = parse_json((repo / settingsmerge.SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, DataError):
        return None
    return data if isinstance(data, dict) else None


def check_hooks(repo: Path) -> list[Check]:
    fix = "Run `tug sync` in this repo. It writes the hooks again."
    commands = settingsmerge.installed_hooks(repo)
    missing = [spec.arguments for spec in hostfiles.HOOKS if not any(shlex.split(command)[2:] == spec.arguments.split() for command in commands)]
    if missing:
        return [Check(FAILED, f"`{settingsmerge.SETTINGS_FILE}` lacks Tugboat's hook for {_named([f'`{name}`' for name in missing])}, so that step does not run in any agent tool.", fix)]
    programs = sorted({shlex.split(command)[0] for command in commands})
    gone = [program for program in programs if not (os.path.isfile(program) and os.access(program, os.X_OK))]
    if gone:
        return [
            Check(
                FAILED,
                f"The hooks call `{gone[0]}`, which is not there. Until this is repaired, every write and every shell command of an agent is denied.",
                "Install Tugboat again, then run `tug sync` in this repo. Or take Tugboat out of the repo: see \"Remove Tugboat\" in the README.",
            )
        ]
    return [Check(PASSED, f"Both hooks are in `{settingsmerge.SETTINGS_FILE}`, and the `tug` they call is there ({programs[0]}).")]


def check_protected_paths(repo: Path) -> list[Check]:
    fix = "Run `tug sync` in this repo."
    compiled = permissions.read_compiled(repo)
    if compiled is None:
        return [Check(FAILED, f"The compiled list of protected paths, `{permissions.COMPILED}`, is missing or cannot be read. With no list, the check before each action denies every write.", fix)]
    checks = []
    if compiled.problem:
        checks.append(Check(FAILED, f"The team repo's list of protected paths was not loaded: {compiled.problem}.", "Repair the file in the team repo, through a pull request."))
    owned = settingsmerge.read_owned_rules(repo)
    settings = _settings(repo) or {}
    asked = settings.get("permissions", {}).get("ask", []) if isinstance(settings.get("permissions"), dict) else []
    lacking = [rule for rule in owned if rule not in (asked if isinstance(asked, list) else [])]
    if lacking:
        checks.append(Check(FAILED, f"{_count(len(lacking), 'protected path rule')} for Claude Code {'is' if len(lacking) == 1 else 'are'} missing from `{settingsmerge.SETTINGS_FILE}`.", fix))
    if not checks:
        checks.append(Check(PASSED, f"{_count(len(compiled.protected), 'protected path')} {'is' if len(compiled.protected) == 1 else 'are'} compiled, and Claude Code has its {_count(len(owned), 'rule')} for them."))
    return checks


def check_git_sees_nothing(repo: Path) -> list[Check]:
    generated = [f"{apprepo.LOCAL_FOLDER}/", settingsmerge.SETTINGS_FILE, *sorted(hostfiles.read_manifest(repo))]
    try:
        seen = [line[3:] for line in git("status", "--porcelain", "--", *generated, cwd=repo).splitlines() if line.strip()]
    except GitError as error:
        return [Check(UNTESTED, f"Whether Git ignores Tugboat's files could not be asked: {error}.")]
    if seen:
        return [Check(FAILED, f"Git sees {_count(len(seen), 'file')} that Tugboat generated: {_named(seen)}. They could be committed by mistake.", "Run `tug sync` in this repo. It lists them in the repo's local exclude file.")]
    return [Check(PASSED, "Git ignores everything Tugboat generated in this repo.")]


def check_project(repo: Path, address: TeamRepoAddress, working: profile.Working | None, problem: str | None) -> list[Check]:
    if working is None:
        return [Check(FAILED, f"The project profile could not be read: {problem}.", "Run `tug sync` in this repo.")]
    if working.profile is None:
        return [Check(FAILED, "This project has no profile yet.", "Run `tug init` in this repo, in a terminal.")]
    checks = []
    entry = hostfiles.repo_entry(repo, address)
    if entry is None:
        checks.append(Check(FAILED, "This repo is not in the project profile, so no platform rule, skill, or protected path of its platform applies here.", "Run `tug init` in this repo, in a terminal. It proposes the repo."))
    else:
        approved = working.pending is None or (working.approved is not None and profile.find_repo(working.approved, entry["github"]) is not None)
        words = "the approved project profile" if approved else "the profile this machine proposed"
        checks.append(Check(PASSED, f"This repo is in {words} of '{working.profile['project']}' as {entry['github']} ({detect.PLATFORM_NAMES.get(entry['platform'], entry['platform'])})."))
    if working.pending is not None:
        checks.append(Check(ADVISORY, f"A profile change that this machine proposed waits for its merge: {profile.where(working.pending)}"))
    steps = list(appsync.unfinished_setup(address, repo)[0])
    policy = aiuse.unfinished(aiuse.load(repo))
    for step in [*steps, *([policy] if policy else [])]:
        checks.append(Check(FAILED, f"Setup is not finished: {step}."))
    return checks


# ---- MCP servers and agent definitions


def _servers_in(data: Any, key: str, where: str) -> list[Server]:
    table = data.get(key) if isinstance(data, dict) else None
    found = []
    for name, entry in table.items() if isinstance(table, dict) else ():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") if isinstance(entry.get("url"), str) else None
        command = [entry["command"]] if isinstance(entry.get("command"), str) else []
        command += [word for word in entry.get("args", []) if isinstance(word, str)] if isinstance(entry.get("args"), list) else []
        found.append(Server(str(name), where, url, tuple(command)))
    return found


def configured_servers(repo: Path, surfaces: list[str]) -> tuple[list[Server], list[str]]:
    """The MCP servers in the settings files of the project's agent tools. Returns (servers, files that could not be read)."""
    home = user_home()
    sources: list[tuple[Path, str, str]] = []  # file, key, where
    servers: list[Server] = []
    unread: list[str] = []
    if "claude-code" in surfaces:
        folder = os.environ.get("CLAUDE_CONFIG_DIR")
        user_file = (Path(folder) if folder else home) / ".claude.json"
        sources.append((user_file, "mcpServers", "Claude Code, your user settings"))
        sources.append((repo / ".mcp.json", "mcpServers", "Claude Code, `.mcp.json` in this repo"))
        if user_file.is_file():
            try:
                projects = _loose_json(user_file).get("projects", {})
                for folder_name in {os.fspath(repo), os.fspath(repo.resolve())}:
                    servers += _servers_in(projects.get(folder_name) if isinstance(projects, dict) else None, "mcpServers", "Claude Code, your settings for this repo")
            except (DataError, AttributeError):
                pass  # reported below, when the same file is read for the user settings
    if "copilot-cli" in surfaces:
        sources.append((copilot_home() / "mcp-config.json", "mcpServers", "Copilot CLI, your user settings"))
    if "copilot-vscode" in surfaces:
        sources.append((home / "Library/Application Support/Code/User/mcp.json", "servers", "VS Code, your user settings"))
        sources.append((repo / ".vscode/mcp.json", "servers", "VS Code, `.vscode/mcp.json` in this repo"))
    for file, key, where in sources:
        if not file.is_file():
            continue
        try:
            servers += _servers_in(_loose_json(file), key, where)
        except DataError:
            unread.append(where)
    return servers, unread


def check_mcp_servers(repo: Path, surfaces: list[str]) -> list[Check]:
    register = aitools.load(repo)
    if register.entries is None:
        if register.problem:
            return [Check(FAILED, f"MCP servers were not compared with the team's list: {register.problem}.", "Repair the file in the team repo, through a pull request.")]
        return [Check(UNTESTED, f"MCP servers were not compared with a list: the team repo has no `{aitools.FILE}`.")]
    if not register.entries["mcp_servers"]:
        return [Check(UNTESTED, "MCP servers were not compared with a list: the team has not listed its approved MCP servers yet.")]
    servers, unread = configured_servers(repo, surfaces)
    checks = []
    listed = []
    for server in servers:
        if aitools.match_server(register.entries, url=server.url, command=list(server.command)) is not None:
            listed.append(f"'{server.name}' ({server.where})")
        else:
            checks.append(
                Check(
                    FAILED,
                    f"The MCP server '{server.name}' ({server.where}; {server.known_by}) is not on the team's list of approved MCP servers.",
                    f"Remove it from that settings file, or propose it for `{aitools.FILE}` in the team repo. A server is known by its host or its program, not by its name.",
                )
            )
    if listed:
        checks.append(Check(PASSED, f"{_count(len(listed), 'configured MCP server')} {'is' if len(listed) == 1 else 'are'} on the team's list: {_named(listed)}."))
    elif not checks:
        checks.append(Check(PASSED, "No MCP server is configured in the settings files of the project's agent tools."))
    for where in unread:
        checks.append(Check(UNTESTED, f"The MCP servers of {where} were not compared: the settings file could not be read."))
    checks.append(Check(UNTESTED, "MCP servers that an account, a plugin, or an extension adds are not in a settings file, so they were not seen. Only a check inside an agent session sees them."))
    return checks


def check_agents(repo: Path) -> list[Check]:
    ours = set(hostfiles.read_manifest(repo))
    found = []
    for folder, ending in AGENT_FOLDERS:
        base = repo / folder
        for file in sorted(base.iterdir()) if base.is_dir() else ():
            if file.name.endswith(ending) and file.name != "README.md" and file.is_file():
                found.append(f"{folder}/{file.name}")
    registered = [path for path in found if path in ours]
    extra = [path for path in found if path not in ours]
    checks = []
    if extra:
        verb = "is" if len(extra) == 1 else "are"
        checks.append(Check(ADVISORY, f"{_count(len(extra), 'agent definition')} in this repo {verb} not in the team's agent registry, so the team repo did not review {'it' if len(extra) == 1 else 'them'}: {_named(extra)}."))
    if registered or not extra:
        checks.append(Check(PASSED, f"{_count(len(registered), 'agent definition')} from the team's agent registry {'is' if len(registered) == 1 else 'are'} installed in this repo."))
    return checks


# ---- the whole check


def run(start: str | os.PathLike[str] = ".") -> Report:
    try:
        repo: Path | None = apprepo.find_app_repo(start)
    except TugError:
        repo = None
    state = teamcopy.read_state(repo) if repo is not None else None
    address = working = problem = None
    if state is not None:
        try:
            address = parse_team_repo_address(state["team_repo"])
            working = profile.load(address)
        except TugError as error:
            problem = str(error)
    surfaces = list(working.profile["surfaces"]) if working is not None and working.profile else [name for name, _ in profile.SURFACES]

    checks: list[Check] = []
    checks += check_tool_version(working.approved if working is not None else None)
    checks += check_uv()
    if "claude-code" in surfaces:
        checks += check_claude_code()
    if "copilot-cli" in surfaces:
        checks += check_copilot_cli(repo if state is not None else None)
    if "copilot-vscode" in surfaces:
        checks += check_vscode(repo if state is not None else None)

    if repo is None:
        heading = ("No app repo here, so only this machine was checked. Run `tug doctor` inside an app repo to check the repo too.",)
        return Report(None, heading, tuple(checks))
    if state is None or address is None:
        text = "This repo is not set up with Tugboat." if state is None else f"This repo's Tugboat state names a team repo that cannot be used: {problem}."
        checks.append(Check(FAILED, text, "Run `tug init` in this repo, in a terminal."))
        return Report(repo, (f"Repo: {repo}",), tuple(checks))

    entry = hostfiles.repo_entry(repo, address)
    platform = entry["platform"] if entry else None
    checks += check_toolchain(repo, platform)
    checks += check_clone(address)
    checks += check_copy(repo, address, state)
    checks += check_host_files(repo)
    checks += check_hooks(repo)
    checks += check_protected_paths(repo)
    checks += check_git_sees_nothing(repo)
    checks += check_project(repo, address, working, problem)
    checks += check_mcp_servers(repo, surfaces)
    checks += check_agents(repo)
    checks += check_sessions(repo, surfaces)
    project = f"project '{working.profile['project']}'" if working is not None and working.profile else "no project profile"
    kind = f", {detect.PLATFORM_NAMES[platform]}" if platform in detect.PLATFORM_NAMES else ""
    return Report(repo, (f"Repo: {repo} ({project}{kind})", f"Team repo: {address.url}"), tuple(checks))


def render(report: Report) -> str:
    lines = ["Tugboat setup check", *report.heading]
    parts = (
        (DISK, "On disk", "Read from files and from installed programs. Nothing was changed."),
        (SESSION, "Observed in a session", f"What an agent tool's own session showed, when its agent ran the skill `{SKILL}`. Configured on disk is not the same as seen working."),
    )
    for area, title, explanation in parts:
        found = [check for check in report.checks if check.area == area]
        if not found:
            continue
        lines += ["", f"== {title}", explanation]
        for result, name in ORDER:
            group = [check for check in found if check.result == result]
            if not group:
                continue
            lines += ["", f"{name}: {len(group)}"]
            for check in group:
                lines.append(f"- {check.text}")
                if check.fix:
                    lines.append(f"  To repair: {check.fix}")
    counts = {result: sum(check.result == result for check in report.checks) for result, _ in ORDER}
    lines.append("")
    if counts[FAILED]:
        lines.append(f"Not ready: {_count(counts[FAILED], 'check')} failed.")
    elif counts[UNTESTED]:
        lines.append(f"Nothing failed. {_count(counts[UNTESTED], 'thing')} could not be tested, so this is not a full pass.")
    else:
        lines.append("Ready: every check passed.")
    return "\n".join(lines)


# ---- inside a session

_NO_HOOK_CAUSE = {
    observed.CLAUDE: "Hooks may be switched off in Claude Code's settings (`disableAllHooks`), or the session was started outside this repo.",
    observed.COPILOT_CLI: "The usual cause is that Copilot CLI does not trust this folder. It then prints one warning line at the start and runs no hook.",
    observed.COPILOT_VSCODE: "The usual cause is that VS Code does not trust this workspace. It then runs no hook and reports nothing.",
}
_NO_HOOK_FIX = {
    observed.CLAUDE: "Start `claude` in this repo's folder, check that hooks are not switched off, and run the skill again.",
    observed.COPILOT_CLI: "Start `copilot` in this folder and choose to trust it and remember it. Then run the skill again in a new session.",
    observed.COPILOT_VSCODE: "In VS Code, trust this workspace (command \"Workspaces: Manage Workspace Trust\"). Then run the skill again in a new chat.",
}
_CONNECTION_WORDS = {observed.OK: "answered", observed.FAILED: "did not answer", observed.NONE: "was not tried"}


def check_sessions(repo: Path, surfaces: list[str]) -> list[Check]:
    """What each of the project's agent tools showed in a session of its own."""
    checks = []
    for surface in surfaces:
        if surface not in observed.NAMES:
            continue
        name = observed.NAMES[surface]
        again = f"In a session of that tool in this repo, ask the agent to run the skill `{SKILL}`."
        seen = observed.last_check(repo, surface)
        if seen is None:
            checks.append(Check(UNTESTED, f"{name} has not been checked inside a session in this repo, so whether its hooks really run is not known. {again}", area=SESSION))
            continue
        when = sync.age(seen.at)
        if observed.days_since(seen.at) > STALE_DAYS:
            checks.append(Check(UNTESTED, f"{name} was last checked inside a session {when}, which is too long ago to count. {again}", area=SESSION))
            continue
        if not seen.hooks_fired:
            checks.append(Check(FAILED, f"{name}: no hook ran in its session (checked {when}). So there nothing syncs at the start and no action is checked. {_NO_HOOK_CAUSE[surface]}", _NO_HOOK_FIX[surface], SESSION))
            continue
        checks.append(Check(PASSED, f"{name}: the hooks ran in its session (checked {when}, with tug {seen.tool_version}).", area=SESSION))
        if not seen.session_start:
            checks.append(Check(ADVISORY, f"{name}: the check before each action ran, and the session-start hook was not seen in the same session. The session may have started before this repo was set up, or have been resumed.", area=SESSION))
        for label, answer in (("tracker", seen.tracker), ("GitHub", seen.github)):
            text = f"{name}: the {label} connection {_CONNECTION_WORDS[answer]}, as its agent reported."
            if answer == observed.OK:
                checks.append(Check(PASSED, text, area=SESSION))
            elif answer == observed.FAILED:
                checks.append(Check(FAILED, text, f"Connect {'the tracker' if label == 'tracker' else 'GitHub'} in that tool, or give its sign-in the permission it lacks, then run the skill again.", SESSION))
            else:
                checks.append(Check(UNTESTED, text, area=SESSION))
    return checks


def observe(start: str | os.PathLike[str], surface: str, tracker: str, github: str) -> tuple[bool, str]:
    """The check an agent starts from inside its session. Returns (everything worked, the report for the agent).

    The agent runs `tug doctor observe` with its shell tool. When the agent
    tool runs Tugboat's hooks, the hook before that tool call has just left a
    mark. No fresh mark means that no hook runs in that session.
    """
    repo = apprepo.find_app_repo(start)
    if teamcopy.read_state(repo) is None:
        raise TugError("this repo is not set up with Tugboat. Run `tug init` in it first, in a terminal.")
    probe = observed.fresh_probe(repo)
    lines = []
    started = False
    if probe is not None:
        seen_surface, _session, started = probe
        if seen_surface != surface:
            lines.append(f"You named {observed.NAMES[surface]}. The hook that ran belongs to {observed.NAMES[seen_surface]}, so the result is recorded for that tool.")
        surface = seen_surface
    name = observed.NAMES[surface]
    observed.record_check(repo, observed.Check(surface, observed.stamp_now(), str(installed_version()), probe is not None, started, tracker, github))
    lines.insert(0, f"Tugboat setup check, inside a session of {name}")
    if probe is None:
        lines.append(f"- Hooks: NOT RUNNING. No hook ran before this command. So in this session nothing syncs at the start, and no action is checked. {_NO_HOOK_CAUSE[surface]}")
        lines.append(f"  To repair: {_NO_HOOK_FIX[surface]}")
        lines.append("  This is only true when the agent ran this command with its shell tool, inside a session. Typed by hand in a terminal, no hook runs either.")
    else:
        lines.append("- Hooks: the check before each action ran for this command, so this agent tool loads Tugboat's hooks.")
        if started:
            lines.append("- The session-start hook ran in this session too.")
        else:
            lines.append("- The session-start hook was not seen in this session. The session may have started before this repo was set up, or have been resumed.")
    for label, answer in (("Tracker", tracker), ("GitHub", github)):
        lines.append(f"- {label} connection: {_CONNECTION_WORDS[answer]}, as you reported.")
    lines.append(f"This is recorded in `{apprepo.LOCAL_FOLDER}/{observed.FILE}`. `tug doctor` shows it beside what is configured on disk.")
    lines.append("Tell the developer what this says, as it is. Do not repair anything yourself.")
    return probe is not None and observed.FAILED not in (tracker, github), "\n".join(lines)
