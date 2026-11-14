"""Protected paths: one source in the team repo, compiled for each agent tool.

The source is `permissions/protected-paths.json` in the team repo. A change
to it needs a pull request there. At every sync Tugboat compiles it for the
app repo it runs in:

- `.tugboat/permissions.json`: the paths that are protected in this repo,
  with the reason for each. `tug protected` reads it, and so will the hook
  that checks an action before the agent takes it.
- Rules for Claude Code in `.claude/settings.local.json`: an "ask" rule for
  each protected path, so Claude Code asks the developer before an edit.
- A section in the instruction file, for every agent tool.

Copilot takes no allow or deny rule for tools from a file in the repo. There
the same compiled file is enforced by the hook alone.

Tugboat's own generated files are always protected. They are not listed in
the team repo.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo, formats, globmatch, teamcopy
from tugboat.errors import DataError
from tugboat.strict_json import pretty_json, read_json_file

SOURCE = "permissions/protected-paths.json"
COMPILED_FILE = "permissions.json"
COMPILED = f"{apprepo.LOCAL_FOLDER}/{COMPILED_FILE}"
COMPILED_SCHEMA = "tug.compiled-permissions/1"
COPY = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}"
MAX_LISTED = 40  # paths named in the instruction file. The rest are counted.
TEAM, OWN = "team", "tugboat"

# Tugboat's own files inside its folder. The drafts under `.tugboat/capture/` are not among them:
# the capture skills write there.
OWN_PATHS: tuple[tuple[str, str], ...] = (
    (f"{COPY}/**", "The read-only copy of the team repo. Team content changes through a pull request in the team repo."),
    (f"{apprepo.LOCAL_FOLDER}/*.json", "Tugboat's own records for this repo. Only the `tug` command writes them."),
)
GENERATED_REASON = "Tugboat generated this file. The next sync writes it again. Its content changes through the team repo."
SETTINGS_REASON = "This file holds Tugboat's hooks and the protected path rules."


class Protected(NamedTuple):
    pattern: str
    reason: str
    source: str  # "team": from the team repo. "tugboat": one of Tugboat's own files.


class Compiled(NamedTuple):
    platform: str | None
    protected: tuple[Protected, ...]
    problem: str | None = None  # why the team repo's list could not be read
    shared_branches: tuple[str, ...] = ()  # the repo's default branch, for the git checks of the hook


def _team_entries(repo: Path, platform: str | None) -> list[Protected]:
    """The team repo's protected paths that hold in a repo of this platform."""
    file = teamcopy.copy_path(repo) / SOURCE
    if file.is_symlink() or not file.is_file():
        return []
    entries = formats.validate_protected_paths(read_json_file(file))["paths"]
    return [
        Protected(entry["pattern"], entry["reason"], TEAM)
        for entry in entries
        # A repo whose platform is not known yet gets every entry: more is protected, not less.
        if entry["platform"] == "any" or platform not in formats.PLATFORMS or entry["platform"] == platform
    ]


def read_compiled(repo: Path) -> Compiled | None:
    """The compiled file as sync left it. None when there is none, or it cannot be read."""
    file = apprepo.local_folder(repo) / COMPILED_FILE
    if file.is_symlink() or not file.is_file():
        return None
    try:
        data = read_json_file(file)
        if not isinstance(data, dict) or data.get("schema") != COMPILED_SCHEMA or not isinstance(data.get("protected"), list):
            return None
        protected = []
        for entry in data["protected"]:
            pattern, reason, source = entry["pattern"], entry["reason"], entry["source"]
            if not (isinstance(pattern, str) and isinstance(reason, str) and source in {TEAM, OWN}):
                return None
            protected.append(Protected(pattern, reason, source))
        platform = data.get("platform")
        branches = data.get("shared_branches")
        shared = tuple(branch for branch in branches if isinstance(branch, str)) if isinstance(branches, list) else ()
        return Compiled(platform if platform in formats.PLATFORMS else None, tuple(protected), None, shared)
    except (DataError, KeyError, TypeError):
        return None


def compile_for(repo: Path, platform: str | None, generated: list[str], settings_file: str, default_branch: str | None = None) -> Compiled:
    """Work out what is protected in this repo. `generated` are the patterns of the files Tugboat generates.

    When the team repo's list cannot be read, the entries of the last good
    compile are kept: a broken file never takes protection away.
    """
    problem = None
    try:
        team = _team_entries(repo, platform)
    except DataError as error:
        file = teamcopy.copy_path(repo) / SOURCE
        before = read_compiled(repo)
        team = [entry for entry in before.protected if entry.source == TEAM] if before else []
        kept = f"The {len(team)} protected paths of the last good list stay in force" if team else "No protected path from the team repo is in force"
        problem = f"the protected paths {SOURCE} were not loaded: {str(error).replace(str(file), SOURCE)}. {kept}"
    own = [Protected(pattern, reason, OWN) for pattern, reason in OWN_PATHS]
    own += [Protected(pattern, GENERATED_REASON, OWN) for pattern in generated]
    own.append(Protected(settings_file, SETTINGS_REASON, OWN))
    seen: set[str] = set()
    protected = []
    for entry in team + own:
        if entry.pattern not in seen:
            seen.add(entry.pattern)
            protected.append(entry)
    return Compiled(platform, tuple(protected), problem, (default_branch,) if default_branch else ())


def write_compiled(repo: Path, compiled: Compiled) -> bool:
    """Write the compiled file when its content changed. Returns True when it was written."""
    document: dict[str, Any] = {
        "schema": COMPILED_SCHEMA,
        "platform": compiled.platform,
        "shared_branches": list(compiled.shared_branches),
        "protected": [{"pattern": entry.pattern, "reason": entry.reason, "source": entry.source} for entry in compiled.protected],
    }
    text = pretty_json(document)
    folder = apprepo.local_folder(repo)
    file = folder / COMPILED_FILE
    if file.is_file() and not file.is_symlink() and file.read_text(encoding="utf-8") == text:
        return False
    folder.mkdir(exist_ok=True)
    if file.exists() or file.is_symlink():
        file.unlink()
    file.write_text(text, encoding="utf-8")
    return True


def match(compiled: Compiled, path: str) -> Protected | None:
    """The entry that protects a path from the top of the repo, or None. The narrowest pattern is named."""
    best: tuple[tuple[int, int], Protected] | None = None
    for entry in compiled.protected:
        if globmatch.matches(entry.pattern, path):
            rank = globmatch.specificity(entry.pattern)
            if best is None or rank > best[0]:
                best = (rank, entry)
    return best[1] if best else None


def claude_ask_rules(repo: Path, compiled: Compiled) -> list[str] | None:
    """The "ask" rules for Claude Code: one or two for each protected path.

    A rule names the full path on this machine, because a rule in the local
    settings file is otherwise read from the folder the session started in,
    which need not be the top of the repo. A pattern also gets a second rule
    for everything beneath it, as a pattern in the team repo covers both.
    Returns None when the repo's own path has a bracket in it, which the
    rule syntax cannot hold.
    """
    top = os.fspath(repo).rstrip("/")
    if "(" in top or ")" in top:
        return None
    rules: list[str] = []
    for entry in compiled.protected:
        forms = [entry.pattern] if entry.pattern.endswith("/**") else [entry.pattern, f"{entry.pattern}/**"]
        for form in forms:
            rule = f"Edit(/{top}/{form})"
            if rule not in rules:
                rules.append(rule)
    return rules


def render(compiled: Compiled | None) -> str:
    """The part of the instruction file that names the protected paths of this repo."""
    lines = ["## Protected paths", ""]
    if compiled is None:
        lines.append("No list of protected paths was compiled for this repo. Ask the developer before you edit a signing file, a CI workflow, or a file Tugboat generated.")
        return "\n".join(lines) + "\n"
    team = [entry for entry in compiled.protected if entry.source == TEAM]
    lines += [
        "Ask the developer before you create, change, move, or delete a file that one of these patterns covers. "
        "Say which pattern it is and give the reason. Do it only after the developer agrees, and do not get around it with a shell command.",
        "",
    ]
    if team:
        lines.append("From the team repo:")
        lines.append("")
        for entry in team[:MAX_LISTED]:
            lines.append(f"- `{entry.pattern}`: {entry.reason}")
        if len(team) > MAX_LISTED:
            lines.append(f"- and {len(team) - MAX_LISTED} more, in `{COMPILED}`.")
    else:
        lines.append("The team repo lists no protected path for this repo.")
    lines += [
        "",
        f"Tugboat's own files are protected too: `{apprepo.LOCAL_FOLDER}/` apart from `{apprepo.LOCAL_FOLDER}/capture/`, "
        "the files it generated under `.claude/`, and `.claude/settings.local.json`.",
        f"To check a path, run `tug protected <path>`. The full list for this repo is in `{COMPILED}`. A pattern covers what it matches and everything beneath it.",
        "The list changes only through a pull request in the team repo. You cannot take a path off it, and neither can the developer in a session.",
    ]
    if compiled.problem:
        lines += ["", f"Note: {compiled.problem}."]
    return "\n".join(lines) + "\n"
