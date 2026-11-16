"""`tug hook`: what the agent tools call.

Two events. At session start the repo is synced and the agent gets the
report: whatever goes wrong there is reported in one line, and the session
goes on. Before a tool call the action is checked: whatever goes wrong
there denies the action, because a check that failed has not passed.

The module imports little at the top. The check before a tool call runs on
every tool call, so it must start fast.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tugboat import apprepo, teamcopy
from tugboat.address import parse_team_repo_address
from tugboat.errors import DataError, TugError
from tugboat.strict_json import parse_json

MAX_INPUT_BYTES = 1024 * 1024
# Claude Code documents 10,000 characters for session-start text. Stay well under it.
MAX_CONTEXT = 9000
EVENTS = ("session-start", "pre-tool-use")
EXIT_DENY = 2  # both Claude Code and Copilot block the action on this exit code
REPAIR_COMMAND = "tug sync"


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
    from tugboat import observed

    # A mark that this hook ran, for the setup check inside a session.
    observed.record_hook(repo, observed.surface_of(payload), "session_start", payload.get("session_id"))
    try:
        from tugboat import appsync  # heavy, and only needed at session start

        address = parse_team_repo_address(state["team_repo"])
        report = appsync.render(appsync.run(repo, address, quick=True))
    except Exception as error:  # noqa: BLE001 - a hook must not break the session
        report = f"Tugboat could not sync at session start: {type(error).__name__}: {error}\nWork continues. Run `tug sync` to see more."
    try:
        from tugboat import brief

        task_brief = brief.build(repo, parse_team_repo_address(state["team_repo"])) or ""
    except Exception as error:  # noqa: BLE001 - a hook must not break the session
        task_brief = f"Tugboat could not work out the task brief: {type(error).__name__}: {error}. `tug status` shows more."
    return context_output(session_text(report, task_brief))


def session_text(report: str, task_brief: str) -> str:
    """The sync report and, after it, the task brief. When both do not fit, the sync report is cut: the brief is what a new session cannot get elsewhere."""
    head = "Tugboat sync report, from the start of this session:\n"
    tail = f"\nTeam rules and knowledge are in `{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}/`. `.claude/rules/tugboat.md` says how to use them."
    if task_brief:
        tail += f"\n\n{task_brief}"
    room = MAX_CONTEXT - len(head) - len(tail)
    if len(report) > room:
        note = "\n[The sync report was cut short here. `tug sync` prints all of it.]"
        report = report[: max(room - len(note), 0)].rstrip() + note
    return head + report + tail


def decision_output(action: str, reason: str) -> str:
    """An "ask" or a "deny" in the shapes the agent tools read.

    Claude Code reads the decision under `hookSpecificOutput`. Copilot was
    seen to obey a deny that carries the fields at the top level as well,
    and to show its reason. An "ask" is only ever sent to Claude Code.
    """
    nested = {"hookEventName": "PreToolUse", "permissionDecision": action, "permissionDecisionReason": reason}
    output: dict = {"hookSpecificOutput": nested}
    if action == "deny":
        output = {"permissionDecision": action, "permissionDecisionReason": reason, **output}
    return json.dumps(output, ensure_ascii=False)


def is_check_command(command: str) -> bool:
    """True for a shell command that runs `tug doctor observe`, the setup check inside a session."""
    if "doctor" not in command:
        return False
    import shlex

    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return any(os.path.basename(word) == "tug" and words[index + 1 : index + 3] == ["doctor", "observe"] for index, word in enumerate(words))


def _current_branch(folder: str) -> str | None:
    from tugboat.gitrun import GitError, git

    try:
        return git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=folder, timeout=3.0) or None
    except (GitError, OSError):
        return None


def pre_tool_use(raw: str) -> str:
    """Check one tool call. Returns what to print: "" to allow, or an "ask" or "deny" decision.

    Raises for anything it cannot check. The caller turns that into a denial.
    """
    from tugboat import permissions, pretool

    payload = parse_json(raw)
    if not isinstance(payload, dict):
        raise DataError("the agent tool sent something that is not a JSON object")
    if pretool.kind_of(payload.get("tool_name")) is None:
        return ""  # not a tool that writes or runs a command
    cwd = payload.get("cwd")
    start = cwd if isinstance(cwd, str) and os.path.isabs(cwd) and os.path.isdir(cwd) else os.getcwd()
    try:
        repo: Path = apprepo.find_app_repo(start)
    except TugError:
        return ""  # not in an app repo: nothing of Tugboat's to guard
    if teamcopy.read_state(repo) is None:
        return ""
    compiled = permissions.read_compiled(repo)
    if compiled is None:
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if isinstance(command, str) and command.strip() == REPAIR_COMMAND:
            return ""  # the one command that rebuilds the list must not be held up by its absence
        raise DataError(f"the list of protected paths for this repo ({permissions.COMPILED}) is missing or cannot be read. Run `{REPAIR_COMMAND}`")
    decision = pretool.decide(payload, repo, compiled, _current_branch, compiled.shared_branches)
    if decision.action != pretool.ALLOW:
        return decision_output(decision.action, decision.reason)
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else tool_input
    if isinstance(command, str) and is_check_command(command):
        from tugboat import observed

        # The setup check inside a session looks for this mark: it shows that this hook runs in this agent tool.
        observed.record_hook(repo, observed.surface_of(payload), "probe", payload.get("session_id"))
    return ""


def main(event: str) -> int:
    if event == "pre-tool-use":
        # Nothing may escape from here: an error is a denial, with exit code 2.
        try:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise DataError("the tool call is too large to check")
            output = pre_tool_use(raw.decode("utf-8"))
        except BaseException as error:  # noqa: BLE001 - a check that failed has not passed
            if isinstance(error, KeyboardInterrupt | SystemExit):
                raise
            reason = f"Tugboat could not check this action, so it was not allowed: {type(error).__name__}: {error}. Tell the developer. `{REPAIR_COMMAND}` rebuilds Tugboat's files."
            # A "deny" answer carries the reason on every agent tool. Copilot was seen to drop the text of an exit code 2.
            # The exit code is the fallback, for when even the answer cannot be printed.
            try:
                print(decision_output("deny", reason))
                sys.stdout.flush()
                return 0
            except Exception:  # noqa: BLE001
                print(reason, file=sys.stderr)
                return EXIT_DENY
        if output:
            print(output)
        return 0
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)[:MAX_INPUT_BYTES].decode("utf-8", "replace") if not sys.stdin.isatty() else ""
    if event == "session-start":
        output = session_start(raw)
        if output:
            print(output)
        return 0
    return 2
