"""What was seen working inside an agent session, for one app repo.

`tug doctor` reads files, so it knows what is configured. It cannot know
whether an agent tool really runs the hooks: Copilot CLI skips them in a
folder it does not trust, VS Code skips them in a workspace it does not
trust, and neither shows an error. So the hooks leave a mark when they run,
and a check that the agent starts inside a session looks for that mark.

The marks are in `<app repo>/.tugboat/observed.json`. Git ignores the file,
and it is one of Tugboat's protected files, so an agent's edit tools are
kept away from it.

Three things are kept for each agent tool:

- when its session-start hook last ran, and in which session
- when its hook before a tool call last saw the check command, and in which session
- the result of the last check: whether the hooks fired, and what the agent
  reported about its tracker connection and its GitHub connection
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo
from tugboat.errors import DataError
from tugboat.strict_json import pretty_json, read_json_file

FILE = "observed.json"
SCHEMA = "tug.observed/1"
CLAUDE, COPILOT_CLI, COPILOT_VSCODE = "claude-code", "copilot-cli", "copilot-vscode"
SURFACES = (CLAUDE, COPILOT_CLI, COPILOT_VSCODE)
NAMES = {CLAUDE: "Claude Code", COPILOT_CLI: "GitHub Copilot CLI", COPILOT_VSCODE: "GitHub Copilot in VS Code"}
OK, FAILED, NONE = "ok", "failed", "none"
ANSWERS = (OK, FAILED, NONE)
FRESH = timedelta(seconds=120)  # how long before the check command its own hook mark may be
MAX_SESSION = 200


class Check(NamedTuple):
    surface: str
    at: str
    tool_version: str
    hooks_fired: bool  # the hook before a tool call saw the check command
    session_start: bool  # the session-start hook ran in the same session
    tracker: str  # OK, FAILED, or NONE, as the agent reported it
    github: str


def surface_of(payload: dict[str, Any], environment: dict[str, str] | None = None) -> str:
    """Which agent tool is calling a hook.

    Claude Code sends the path of its transcript, and Copilot does not. VS
    Code names itself in the variable AI_AGENT of the hook's environment.
    Both were seen. Copilot CLI sets no such name.
    """
    if isinstance(payload.get("transcript_path"), str):
        return CLAUDE
    agent = (os.environ if environment is None else environment).get("AI_AGENT", "")
    return COPILOT_VSCODE if "vscode" in agent.lower() else COPILOT_CLI


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _time(text: object) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if isinstance(text, str) else None
    except ValueError:
        return None


def _file(repo: Path) -> Path:
    return apprepo.local_folder(repo) / FILE


def read(repo: Path) -> dict[str, dict[str, Any]]:
    """Everything that was seen, as {surface: {...}}. A missing or broken file reads as nothing seen."""
    try:
        data = read_json_file(_file(repo))
    except DataError:
        return {}
    surfaces = data.get("surfaces") if isinstance(data, dict) and data.get("schema") == SCHEMA else None
    if not isinstance(surfaces, dict):
        return {}
    return {name: dict(entry) for name, entry in surfaces.items() if name in SURFACES and isinstance(entry, dict)}


def _write(repo: Path, surfaces: dict[str, dict[str, Any]]) -> None:
    target = _file(repo)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(pretty_json({"schema": SCHEMA, "surfaces": surfaces}))
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _session(value: object) -> str | None:
    return value[:MAX_SESSION] if isinstance(value, str) and value else None


def record_hook(repo: Path, surface: str, event: str, session: object, now: datetime | None = None) -> None:
    """Note that one hook ran. Never raises: a mark that cannot be written must not stop a session or an action."""
    with contextlib.suppress(Exception):
        surfaces = read(repo)
        entry = surfaces.setdefault(surface, {})
        entry[event] = {"at": _stamp(now or datetime.now(timezone.utc)), "session": _session(session)}
        _write(repo, surfaces)


def _mark(entry: dict[str, Any], event: str) -> tuple[datetime | None, str | None]:
    mark = entry.get(event)
    if not isinstance(mark, dict):
        return None, None
    return _time(mark.get("at")), _session(mark.get("session"))


def fresh_probe(repo: Path, now: datetime | None = None) -> tuple[str, str | None, bool] | None:
    """The hook mark the check command just left, as (surface, session, the session-start hook ran in that session).

    None when no hook saw the command: then no hook runs in the session that started it.
    """
    now = now or datetime.now(timezone.utc)
    best: tuple[datetime, str, str | None, bool] | None = None
    for surface, entry in read(repo).items():
        at, session = _mark(entry, "probe")
        if at is None or not timedelta(0) <= now - at <= FRESH:
            continue
        _, started = _mark(entry, "session_start")
        found = (at, surface, session, session is not None and started == session)
        if best is None or at > best[0]:
            best = found
    return best[1:] if best else None


def record_check(repo: Path, check: Check) -> None:
    surfaces = read(repo)
    surfaces.setdefault(check.surface, {})["check"] = {
        "at": check.at,
        "tool_version": check.tool_version,
        "hooks_fired": check.hooks_fired,
        "session_start": check.session_start,
        "tracker": check.tracker,
        "github": check.github,
    }
    _write(repo, surfaces)


def last_check(repo: Path, surface: str) -> Check | None:
    entry = read(repo).get(surface, {}).get("check")
    if not isinstance(entry, dict) or _time(entry.get("at")) is None:
        return None
    tracker, github = entry.get("tracker"), entry.get("github")
    if tracker not in ANSWERS or github not in ANSWERS or not isinstance(entry.get("tool_version"), str):
        return None
    return Check(surface, entry["at"], entry["tool_version"], entry.get("hooks_fired") is True, entry.get("session_start") is True, tracker, github)


def days_since(stamp: str, now: datetime | None = None) -> int:
    """Whole days since a recorded moment. A moment that cannot be read counts as very long ago."""
    then = _time(stamp)
    return max(0, ((now or datetime.now(timezone.utc)) - then).days) if then else 10**6


def stamp_now(now: datetime | None = None) -> str:
    return _stamp(now or datetime.now(timezone.utc))
