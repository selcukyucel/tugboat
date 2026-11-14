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
# The permission rules Tugboat itself put into the settings file, so it can take exactly those out again.
RULES_RECORD = ".tugboat/settings-rules.json"
RULES_SCHEMA = "tug.settings-rules/1"
MAX_BYTES = 1024 * 1024


class HookSpec(NamedTuple):
    event: str  # the agent tool's event name, such as "SessionStart"
    arguments: str  # what follows `tug hook`, such as "session-start"
    timeout: int  # seconds
    blocks_on_failure: bool = False  # a crash, a missing command, or a timeout of this hook denies the action


class MergeResult(NamedTuple):
    changed: bool  # Tugboat's hooks in the file changed
    problem: str | None  # why the file was left alone
    rules_changed: bool = False  # Tugboat's permission rules in the file changed


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


def with_ask_rules(settings: dict[str, Any], rules: list[str], previous: list[str]) -> tuple[dict[str, Any], list[str]]:
    """The settings with Tugboat's "ask" rules set to `rules`. Returns them with the rules Tugboat owns now.

    `previous` are the rules Tugboat added last time. Only those are taken
    out. A rule the developer wrote stays, and when it equals one of
    Tugboat's it is not added twice and Tugboat does not count it as its own.
    """
    result = copy.deepcopy(settings)
    permissions = result.get("permissions", {})
    if not isinstance(permissions, dict):
        raise DataError('its "permissions" value is not an object')
    ask = permissions.get("ask", [])
    if not isinstance(ask, list):
        raise DataError('its "permissions.ask" value is not a list')
    ours_before = set(previous)
    kept = [rule for rule in ask if rule not in ours_before]
    added = [rule for rule in rules if rule not in kept]
    if kept or added:
        permissions["ask"] = kept + added
    else:
        permissions.pop("ask", None)
    if permissions:
        result["permissions"] = permissions
    else:
        result.pop("permissions", None)
    return result, added


def read_owned_rules(repo: Path) -> list[str]:
    """The "ask" rules Tugboat added to the settings file last time."""
    try:
        data = parse_json((repo / RULES_RECORD).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, DataError):
        return []
    rules = data.get("ask") if isinstance(data, dict) and data.get("schema") == RULES_SCHEMA else None
    return [rule for rule in rules if isinstance(rule, str)] if isinstance(rules, list) else []


def _write_owned_rules(repo: Path, rules: list[str]) -> None:
    file = repo / RULES_RECORD
    if not rules:
        with contextlib.suppress(OSError):
            file.unlink()
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"schema": RULES_SCHEMA, "ask": rules}, indent=2) + "\n", encoding="utf-8")


def merged_settings(settings: dict[str, Any], specs: list[HookSpec], tug_command: str) -> dict[str, Any]:
    """The settings with Tugboat's hooks set to exactly `specs`. The input is not changed."""
    result = copy.deepcopy(settings)
    hooks = result.get("hooks", {})
    if not isinstance(hooks, dict):
        raise DataError('its "hooks" value is not an object')
    hooks = _without_tugboat(hooks)
    for spec in specs:
        entry: dict[str, Any] = {"type": "command", "command": hook_command(tug_command, spec), "timeout": spec.timeout}
        if spec.blocks_on_failure:
            # Claude Code lets the action through when a hook fails, unless the hook says this. Copilot blocks anyway.
            entry["onFailure"] = "block"
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


def merge_hooks(repo: Path, specs: list[HookSpec], tug_command: str, ask_rules: list[str] | None = None) -> MergeResult:
    """Set Tugboat's hooks, and its "ask" rules, in the local settings file. An empty list removes them.

    With `ask_rules` left out, the permission rules in the file are not touched.
    """
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
    owned_before = read_owned_rules(repo)
    owned = owned_before
    try:
        wanted = merged_settings(settings, specs, tug_command)
        hooks_changed = wanted != settings
        if ask_rules is not None:
            wanted, owned = with_ask_rules(wanted, ask_rules, owned_before)
    except DataError as error:
        return MergeResult(False, f"{SETTINGS_FILE} was left alone because {error}")
    rules_changed = owned != owned_before
    if wanted == settings:
        if rules_changed:
            _write_owned_rules(repo, owned)
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
    if rules_changed:
        _write_owned_rules(repo, owned)
    return MergeResult(hooks_changed, None, rules_changed)


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
