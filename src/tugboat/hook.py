"""`tug hook`: what the agent tools call.

A hook must never break a session. Whatever goes wrong at session start is
reported in one line, and the session goes on.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tugboat import apprepo, appsync, teamcopy
from tugboat.address import parse_team_repo_address
from tugboat.errors import DataError, TugError
from tugboat.strict_json import parse_json

MAX_INPUT_BYTES = 1024 * 1024
# Claude Code documents 10,000 characters for session-start text. Stay well under it.
MAX_CONTEXT = 9000
EVENTS = ("session-start",)


def _payload(raw: str) -> dict:
    try:
        value = parse_json(raw) if raw.strip() else {}
    except DataError:
        return {}
    return value if isinstance(value, dict) else {}


def context_output(text: str) -> str:
    """Text for the agent, in the one shape both Claude Code and Copilot accept.

    Claude Code reads `hookSpecificOutput.additionalContext`. Copilot reads the
    top-level `additionalContext`. Each tool was seen to take its own field and
    ignore the other, so the text arrives once.
    """
    if len(text) > MAX_CONTEXT:
        text = text[: MAX_CONTEXT - 40].rstrip() + "\n[The report was cut short here.]"
    return json.dumps(
        {"additionalContext": text, "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}},
        ensure_ascii=False,
    )


def session_start(raw: str) -> str:
    """Sync this repo and return the report for the agent. Returns "" outside a Tugboat repo."""
    payload = _payload(raw)
    cwd = payload.get("cwd")
    start = cwd if isinstance(cwd, str) and os.path.isabs(cwd) and os.path.isdir(cwd) else os.getcwd()
    try:
        repo: Path = apprepo.find_app_repo(start)
        state = teamcopy.read_state(repo)
        if state is None:
            return ""  # not a repo Tugboat was set up in: say nothing
    except TugError:
        return ""
    try:
        address = parse_team_repo_address(state["team_repo"])
        report = appsync.render(appsync.run(repo, address, quick=True))
    except Exception as error:  # noqa: BLE001 - a hook must not break the session
        report = f"Tugboat could not sync at session start: {type(error).__name__}: {error}\nWork continues. Run `tug sync` to see more."
    text = (
        "Tugboat sync report, from the start of this session:\n"
        f"{report}\n"
        f"Team rules and knowledge are in `{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}/`. `.claude/rules/tugboat.md` says how to use them."
    )
    return context_output(text)


def main(event: str) -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)[:MAX_INPUT_BYTES].decode("utf-8", "replace") if not sys.stdin.isatty() else ""
    if event == "session-start":
        output = session_start(raw)
        if output:
            print(output)
        return 0
    return 2
