"""Tugboat's hooks inside `.claude/settings.local.json`.

Claude Code, Copilot CLI, and Copilot in VS Code all read the hooks in this
file, so each hook is written once. The file belongs to the developer:
Claude Code saves their own approvals in it. So Tugboat changes only its own
hook entries, which it recognises by their command, and keeps every other
key and every other hook as it found them. When nothing of Tugboat's
changes, the file is not written at all.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from tugboat.errors import DataError
from tugboat.gitrun import GitError, git
from tugboat.strict_json import parse_json

SETTINGS_FILE = ".claude/settings.local.json"
MAX_BYTES = 1024 * 1024


class HookSpec(NamedTuple):
    event: str  # the agent tool's event name, such as "SessionStart"
    arguments: str  # what follows `tug hook`, such as "session-start"
    timeout: int  # seconds


class MergeResult(NamedTuple):
    changed: bool
    problem: str | None  # why the file was left alone


def hook_command(tug_command: str, spec: HookSpec) -> str:
    return f"{shlex.quote(tug_command)} hook {spec.arguments}"


def is_tugboat_command(command: object) -> bool:
    """True for a hook command that runs `tug hook ...`, whatever the path to tug."""
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return len(parts) >= 3 and os.path.basename(parts[0]) == "tug" and parts[1] == "hook"


def _without_tugboat(hooks: dict[str, Any]) -> dict[str, Any]:
    """The hooks with every Tugboat entry taken out. Other entries keep their order."""
    result: dict[str, Any] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            result[event] = groups
            continue
        kept_groups = []
        removed_any = False
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [entry for entry in entries if not (isinstance(entry, dict) and is_tugboat_command(entry.get("command")))]
            if len(kept) == len(entries):
                kept_groups.append(group)
                continue
            removed_any = True
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups or not removed_any:
            result[event] = kept_groups
    return result


def merged_settings(settings: dict[str, Any], specs: list[HookSpec], tug_command: str) -> dict[str, Any]:
    """The settings with Tugboat's hooks set to exactly `specs`. The input is not changed."""
    result = copy.deepcopy(settings)
    hooks = result.get("hooks", {})
    if not isinstance(hooks, dict):
        raise DataError('its "hooks" value is not an object')
    hooks = _without_tugboat(hooks)
    for spec in specs:
        entry = {"type": "command", "command": hook_command(tug_command, spec), "timeout": spec.timeout}
        hooks.setdefault(spec.event, [])
        if not isinstance(hooks[spec.event], list):
            raise DataError(f'its "hooks" value for {spec.event} is not a list')
        hooks[spec.event].append({"hooks": [entry]})
    if hooks:
        result["hooks"] = hooks
    else:
        result.pop("hooks", None)
    return result


def _is_tracked(repo: Path, path: str) -> bool:
    try:
        return bool(git("ls-files", "--", path, cwd=repo))
    except GitError:
        return False


def merge_hooks(repo: Path, specs: list[HookSpec], tug_command: str) -> MergeResult:
    """Set Tugboat's hooks in the local settings file. An empty list removes them."""
    file = repo / SETTINGS_FILE
    if _is_tracked(repo, SETTINGS_FILE):
        return MergeResult(False, f"{SETTINGS_FILE} is tracked by Git in this repo, so Tugboat did not change it")
    existed = file.exists()
    settings: dict[str, Any] = {}
    if existed:
        try:
            raw = file.read_bytes()
            if len(raw) > MAX_BYTES:
                raise DataError("it is too large")
            parsed = parse_json(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise DataError("it is not a JSON object")
            settings = parsed
        except (OSError, UnicodeDecodeError, DataError) as error:
            return MergeResult(False, f"{SETTINGS_FILE} could not be read as settings ({error}), so Tugboat did not change it")
    try:
        wanted = merged_settings(settings, specs, tug_command)
    except DataError as error:
        return MergeResult(False, f"{SETTINGS_FILE} was left alone because {error}")
    if wanted == settings:
        return MergeResult(False, None)
    if not wanted and not existed:
        return MergeResult(False, None)
    file.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=file.parent, prefix=file.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(json.dumps(wanted, indent=2, ensure_ascii=False) + "\n")
        if existed:
            os.chmod(temporary, file.stat().st_mode & 0o777)
        os.replace(temporary, file)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return MergeResult(True, None)


def installed_hooks(repo: Path) -> list[str]:
    """The Tugboat hook commands now in the local settings file."""
    try:
        settings = parse_json((repo / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, DataError):
        return []
    found = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in (hooks or {}).values() if isinstance(hooks, dict) else ():
        for group in groups if isinstance(groups, list) else ():
            for entry in group.get("hooks", []) if isinstance(group, dict) and isinstance(group.get("hooks"), list) else ():
                if isinstance(entry, dict) and is_tugboat_command(entry.get("command")):
                    found.append(entry["command"])
    return found
