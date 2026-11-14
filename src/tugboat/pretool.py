"""The check before an agent's tool call: `tug hook pre-tool-use`.

The agent tool sends what it is about to do. This module answers allow,
ask, or deny. Three things are checked:

1. A secret. Text the agent is about to write, or a shell command it is
   about to run, that holds a secret pattern is denied.
2. A protected path. A change to a file that the compiled list protects is
   asked about on Claude Code, which can ask the developer, and denied on
   Copilot.
3. Git. A force push to a shared branch, a hard reset on one, and the
   deletion or forced move of one are denied.

A shell command is checked only for the writes this module can recognise:
redirects, and a list of commands that change files. A script that writes a
file by another route is not seen. A hook is a guard, not a security
boundary.

Everything here must be fast and must not raise for odd input. The caller
turns any error into a denial.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import globmatch, permissions, secretscan

ALLOW, ASK, DENY = "allow", "ask", "deny"
CLAUDE, COPILOT = "claude-code", "copilot"

# Tool names, as the agent tools send them. Copilot sends its patch tool as "Edit" and its shell as "Bash".
_WRITE_TOOL = re.compile(r"edit|write|create|patch|replace|notebook", re.IGNORECASE)
_SHELL_TOOL = re.compile(r"bash|shell|terminal|command|exec", re.IGNORECASE)
_PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")
_TEXT_KEYS = ("content", "new_string", "new_source", "new_str", "text")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$")

# Shell commands that change the files they are given.
_WRITES_ALL = frozenset({"rm", "rmdir", "unlink", "touch", "mkdir", "chmod", "chown", "chgrp", "truncate", "mv", "shred"})
_WRITES_LAST = frozenset({"cp", "ln", "install", "rsync", "scp"})
_REMOVES = frozenset({"rm", "rmdir", "unlink", "mv", "shred"})  # these also hit what lies beneath a folder
_GIT_PATH_COMMANDS = frozenset({"rm", "mv", "clean", "restore", "checkout", "apply"})
_PREFIXES = frozenset({"sudo", "command", "env", "time", "nohup", "exec", "builtin"})
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", "(", ")", ";;"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FORCE_SHORT = re.compile(r"^-[A-Za-z]*f[A-Za-z]*$")
SHARED_PREFIXES = ("release/",)
SHARED_ALWAYS = ("main", "master", "develop")


class Decision(NamedTuple):
    action: str  # "allow", "ask", or "deny"
    reason: str = ""


def surface_of(payload: dict[str, Any]) -> str:
    """Which agent tool is calling. Claude Code sends the path of its transcript. Copilot does not."""
    return CLAUDE if isinstance(payload.get("transcript_path"), str) else COPILOT


def kind_of(tool: object) -> str | None:
    """ "write" for a tool that changes files, "shell" for one that runs commands, None for the rest."""
    if not isinstance(tool, str):
        return None
    if _SHELL_TOOL.search(tool):
        return "shell"
    if _WRITE_TOOL.search(tool):
        return "write"
    return None


# ------------------------------------------------------------ what a write holds


def _patch_parts(patch: str) -> tuple[list[str], list[str]]:
    """The files a patch text touches, and the lines it adds."""
    files, added = [], []
    for line in patch.splitlines():
        found = _PATCH_FILE.match(line)
        if found:
            files.append(found.group(1).strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return files, added


def write_parts(tool_input: object) -> tuple[list[str], list[str]]:
    """The paths a write tool is about to change, and the text it is about to write."""
    paths: list[str] = []
    texts: list[str] = []
    if isinstance(tool_input, str):
        files, added = _patch_parts(tool_input)
        return files, ["\n".join(added)] if added else []
    if not isinstance(tool_input, dict):
        return paths, texts
    for key in _PATH_KEYS:
        if isinstance(tool_input.get(key), str):
            paths.append(tool_input[key])
    for key in _TEXT_KEYS:
        if isinstance(tool_input.get(key), str):
            texts.append(tool_input[key])
    edits = tool_input.get("edits")
    for edit in edits if isinstance(edits, list) else ():
        if isinstance(edit, dict):
            texts.extend(edit[key] for key in _TEXT_KEYS if isinstance(edit.get(key), str))
    for key in ("patch", "input", "diff"):
        if isinstance(tool_input.get(key), str) and "*** " in tool_input[key]:
            files, added = _patch_parts(tool_input[key])
            paths.extend(files)
            texts.append("\n".join(added))
    return paths, texts


def repo_relative(repo: Path, cwd: str, given: str) -> str | None:
    """A path as the tool gave it, from the top of the repo. None when it is outside the repo."""
    absolute = os.path.normpath(os.path.join(cwd, os.path.expanduser(given)))
    top = os.fspath(repo)
    for candidate in (absolute, os.path.realpath(absolute)):
        for base in (top, os.path.realpath(top)):
            if candidate == base:
                return ""
            if candidate.startswith(base.rstrip("/") + "/"):
                return candidate[len(base.rstrip("/")) + 1 :]
    return None


# ------------------------------------------------------------ shell commands


def _tokens(command: str) -> list[str]:
    text = command.replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return text.split()  # an open quote: fall back to plain words


def _segments(tokens: list[str]) -> list[list[str]]:
    parts: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            parts.append([])
        else:
            parts[-1].append(token)
    return [part for part in parts if part]


def _is_redirect(token: str) -> bool:
    return ">" in token and all(character in "<>|&" for character in token)


def _command_words(segment: list[str]) -> list[str]:
    """The segment without leading `NAME=value` words and words like `sudo`."""
    words = [token for token in segment]
    while words and (_ASSIGNMENT.match(words[0]) or os.path.basename(words[0]) in _PREFIXES):
        words.pop(0)
    return words


def _operands(words: list[str]) -> list[str]:
    """The words after the command that are not options or redirects."""
    result = []
    skip = False
    for word in words[1:]:
        if skip:
            skip = False
            continue
        if _is_redirect(word):
            skip = True
            continue
        if word == "--":
            continue
        if word.startswith("-") and len(word) > 1:
            continue
        result.append(word)
    return result


def shell_writes(command: str) -> list[tuple[str, bool]]:
    """The paths a shell command writes, as far as this module can tell. The flag is True for a removal."""
    found: list[tuple[str, bool]] = []
    for segment in _segments(_tokens(command)):
        for index, token in enumerate(segment[:-1]):
            if _is_redirect(token) and not token.endswith("&"):
                target = segment[index + 1]
                if target != "/dev/null" and not target.lstrip("&").isdigit() and target != "-":
                    found.append((target, False))
        words = _command_words(segment)
        if not words:
            continue
        name = os.path.basename(words[0])
        operands = _operands(words)
        options = [word for word in words[1:] if word.startswith("-")]
        if name in _WRITES_ALL:
            found.extend((operand, name in _REMOVES) for operand in operands)
        elif name in _WRITES_LAST and operands:
            found.append((operands[-1], False))
        elif name == "tee":
            found.extend((operand, False) for operand in operands)
        elif name in {"sed", "gsed"} and any(option.startswith("-i") or option == "--in-place" or option.startswith("--in-place=") for option in options):
            found.extend((operand, False) for operand in operands[1:] or operands)
        elif name == "perl" and any(re.fullmatch(r"-[A-Za-z0-9]*i[A-Za-z0-9.]*", option) for option in options):
            found.extend((operand, False) for operand in operands[1:] or operands)
        elif name == "dd":
            found.extend((word[3:], False) for word in words[1:] if word.startswith("of="))
        elif name == "git":
            sub, rest = _git_subcommand(words)
            if sub in _GIT_PATH_COMMANDS:
                found.extend((operand, sub in {"rm", "mv", "clean"}) for operand in _operands([sub, *rest]))
    return found


def _git_subcommand(words: list[str]) -> tuple[str | None, list[str]]:
    """The git subcommand and what follows it, with git's own options skipped."""
    index = 1
    while index < len(words):
        word = words[index]
        if word in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
            index += 2
        elif word.startswith("-"):
            index += 1
        else:
            return word, words[index + 1 :]
    return None, []


def _git_folder(words: list[str], cwd: str) -> str:
    for index, word in enumerate(words[:-1]):
        if word == "-C":
            return os.path.normpath(os.path.join(cwd, words[index + 1]))
    return cwd


def is_shared(branch: str | None, shared: tuple[str, ...]) -> bool:
    if not branch:
        return False
    name = branch[len("refs/heads/") :] if branch.startswith("refs/heads/") else branch
    return name in shared or name in SHARED_ALWAYS or name.startswith(SHARED_PREFIXES)


def git_problem(command: str, cwd: str, shared: tuple[str, ...], current_branch: Callable[[str], str | None]) -> str | None:
    """Why a shell command is a forbidden git action on a shared branch, or None."""
    for segment in _segments(_tokens(command)):
        words = _command_words(segment)
        if not words or os.path.basename(words[0]) != "git":
            continue
        sub, rest = _git_subcommand(words)
        folder = _git_folder(words, cwd)
        options = [word for word in rest if word.startswith("-")]
        operands = [word for word in rest if not word.startswith("-")]
        forced = any(option in {"--force", "--mirror", "--force-if-includes"} or option.startswith("--force-with-lease") or _FORCE_SHORT.match(option) for option in options)
        if sub == "push":
            deleting = any(option in {"--delete", "-d"} for option in options)
            if any(option in {"--mirror", "--all"} for option in options) and forced:
                return "it force-pushes every branch, shared branches among them"
            refs = operands[1:]  # the first operand is the remote
            if not refs:
                refs = ["HEAD"]
            for ref in refs:
                plus = ref.startswith("+")
                source, colon, target = ref.lstrip("+").partition(":")
                name = target if colon else source
                if name in {"HEAD", "@"} or (not colon and not name):
                    name = current_branch(folder) or ""
                name = name.removeprefix("refs/heads/")
                if not is_shared(name, shared):
                    continue
                if colon and not source:
                    return f"it deletes the shared branch '{name}' on the remote"
                if deleting:
                    return f"it deletes the shared branch '{name}' on the remote"
                if forced or plus:
                    return f"it force-pushes to the shared branch '{name}'"
        elif sub == "reset" and "--hard" in options:
            branch = current_branch(folder)
            if is_shared(branch, shared):
                return f"it is a hard reset on the shared branch '{branch}'"
        elif sub == "branch":
            plain_delete = any(option in {"-d", "--delete"} for option in options)
            if "-D" in options or (plain_delete and forced):
                for name in operands:
                    if is_shared(name, shared):
                        return f"it deletes the shared branch '{name}' by force"
            elif forced and operands and is_shared(operands[0], shared):
                return f"it moves the shared branch '{operands[0]}' to another commit by force"
        elif sub in {"checkout", "switch"}:
            for index, word in enumerate(rest[:-1]):
                if word in {"-B", "-C", "--force-create"} and is_shared(rest[index + 1], shared):
                    return f"it resets the shared branch '{rest[index + 1]}' to another commit"
    return None


# ------------------------------------------------------------ the decision


def _protected(compiled: permissions.Compiled, path: str, removal: bool = False) -> permissions.Protected | None:
    """The entry that protects a path. A removal also counts when the path is a folder that holds a protected one."""
    probe = path.replace("*", "x").replace("?", "x") if any(sign in path for sign in "*?") else path
    found = permissions.match(compiled, probe) if probe else None
    if found is None and removal:
        for entry in compiled.protected:
            first = entry.pattern.split("/")[0]
            if "*" not in first and "?" not in first and globmatch.reaches(entry.pattern, probe or first):
                return entry
    return found


def _path_decision(surface: str, path: str, entry: permissions.Protected) -> Decision:
    where = "the team repo's list" if entry.source == permissions.TEAM else "Tugboat's own files"
    what = f"Tugboat: `{path}` is a protected path (pattern `{entry.pattern}`, from {where}). {entry.reason}"
    if surface == CLAUDE:
        return Decision(ASK, f"{what} The developer decides whether the agent may change it.")
    return Decision(DENY, f"{what} An agent may not change it here. Tell the developer, who can make the change themselves.")


def _secret_decision(findings: list[secretscan.Finding], where: str) -> Decision:
    return Decision(
        DENY,
        f"Tugboat: {where} holds what looks like a secret ({secretscan.describe(findings)}). "
        "A secret does not go into a file, a commit, or a command. Use a placeholder, or read the value from the keychain or the environment at run time. "
        "If this is not a secret, tell the developer, who can make the change themselves.",
    )


def decide(
    payload: dict[str, Any],
    repo: Path,
    compiled: permissions.Compiled,
    current_branch: Callable[[str], str | None],
    shared: tuple[str, ...] = (),
) -> Decision:
    """Allow, ask, or deny one tool call."""
    kind = kind_of(payload.get("tool_name"))
    if kind is None:
        return Decision(ALLOW)
    surface = surface_of(payload)
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and os.path.isabs(cwd) else os.fspath(repo)
    tool_input = payload.get("tool_input")

    if kind == "write":
        paths, texts = write_parts(tool_input)
        for text in texts:
            findings = secretscan.find(text)
            if findings:
                return _secret_decision(findings, "the text to be written")
        for given in paths:
            path = repo_relative(repo, cwd, given)
            entry = _protected(compiled, path) if path else None
            if entry is not None:
                return _path_decision(surface, path or given, entry)
        return Decision(ALLOW)

    command = tool_input.get("command") if isinstance(tool_input, dict) else tool_input
    if not isinstance(command, str) or not command.strip():
        return Decision(ALLOW)
    findings = secretscan.find(command)
    if findings:
        return _secret_decision(findings, "the shell command")
    problem = git_problem(command, cwd, shared, current_branch)
    if problem:
        return Decision(DENY, f"Tugboat: this git command is not allowed, because {problem}. A shared branch is changed through a pull request. If the developer wants this, they run it themselves.")
    for given, removal in shell_writes(command):
        path = repo_relative(repo, cwd, given)
        if path is None:
            continue
        entry = _protected(compiled, path, removal)
        if entry is not None:
            return _path_decision(surface, path or given, entry)
    return Decision(ALLOW)
