"""Run Git without ever waiting for a person.

Sync runs from a session-start hook, where nobody can answer a prompt. So
every Git call has a time limit, never asks for a password, and ignores Git
variables left behind by whatever started the tool.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tugboat.errors import TugError

DEFAULT_TIMEOUT = 30.0
# Set by Git while it runs a hook. Inherited, they would point this tool's
# own Git calls at the wrong repository.
_INHERITED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_PREFIX")


class GitError(TugError):
    """A Git command failed. `reason` is one short line for the developer."""

    def __init__(self, action: str, reason: str) -> None:
        super().__init__(f"git {action}: {reason}")
        self.action = action
        self.reason = reason


def git_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _INHERITED}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    # Do not replace a transport the developer chose.
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oConnectTimeout=10")
    return env


def _short(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for prefix in ("fatal: ", "error: "):
        for line in lines:
            if line.startswith(prefix):
                return line[len(prefix) :][:300]
    return (lines[-1] if lines else "no message")[:300]


def run_process(argv: Sequence[str], *, action: str, cwd: Path | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run one command. Return its output, or raise GitError with a short reason."""
    try:
        done = subprocess.run(
            list(argv),
            cwd=cwd,
            env=git_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitError(action, f"gave no answer within {timeout:g} seconds") from error
    except OSError as error:
        raise GitError(action, f"could not start: {error.strerror or error}") from error
    if done.returncode != 0:
        raise GitError(action, _short(done.stderr or done.stdout))
    return done.stdout


def git(*args: str, cwd: Path | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run `git <args>` and return what it printed, without the final line break."""
    return run_process(["git", *args], action=args[0], cwd=cwd, timeout=timeout).rstrip("\n")
