"""The AI tool register: the team's list of approved agent tools, models, and MCP servers.

The register is `permissions/ai-tools.json` in the team repo. It changes
only through a pull request there. This module reads it from the app repo's
copy of the approved revision, hands it to the agent, and answers whether a
configured MCP server is on the list.

One rule for all three groups: a group with entries is the whole list, and
anything else in that group is not approved. An empty group means the team
has not made that list yet, so nothing in it is checked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from tugboat import apprepo, formats, teamcopy
from tugboat.errors import DataError
from tugboat.strict_json import read_json_file

FILE = "permissions/ai-tools.json"
COPY_PATH = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}/{FILE}"
GROUPS = (("agent_tools", "Agent tools"), ("models", "Models"), ("mcp_servers", "MCP servers"))


class Register(NamedTuple):
    entries: dict[str, Any] | None  # the checked register. None when there is none to use.
    problem: str | None = None  # why it could not be read


def load(repo: Path) -> Register:
    """Read the register from the app repo's copy of the team repo."""
    file = teamcopy.copy_path(repo) / FILE
    if file.is_symlink() or not file.is_file():
        return Register(None)
    try:
        return Register(formats.validate_ai_tools(read_json_file(file)))
    except DataError as error:
        return Register(None, f"the AI tool register {FILE} was not loaded: {str(error).replace(str(file), FILE)}")


def match_server(register: dict[str, Any], *, url: str | None = None, command: list[str] | None = None) -> dict[str, Any] | None:
    """The register entry that approves one configured MCP server. None when no entry does.

    A remote server is known by the host of its URL. A local server is known
    by a word of its command line: the program, or a package it runs. A
    version after the package name, as in `name@1.2.3`, is ignored. The name
    a developer gave the server in their settings is not used: anyone can
    call any server "github".
    """
    host = (urlsplit(url).hostname or "").lower() if url else ""
    words = set()
    for word in command or []:
        words.add(word)
        if "@" in word[1:]:
            words.add(word[: word.index("@", 1)])
    for entry in register["mcp_servers"]:
        if host and host in entry.get("hosts", ()):
            return entry
        if words.intersection(entry.get("commands", ())):
            return entry
    return None


def _names(entries: list[dict[str, Any]], *, servers: bool = False) -> str:
    parts = []
    for entry in entries:
        known_by = ", ".join([*entry.get("hosts", ()), *entry.get("commands", ())]) if servers else ""
        parts.append(f"{entry['name']} ({known_by})" if known_by else entry["name"])
    return "; ".join(parts)


def render(register: Register) -> str:
    """The part of the instruction file that hands the register to the agent."""
    lines = ["## Approved AI tools", ""]
    if register.entries is None:
        lines.append(f"No list of approved AI tools is in use. Not loaded: {register.problem}." if register.problem else "The team repo has no list of approved AI tools.")
        return "\n".join(lines) + "\n"
    lines += [f"The team's list of approved AI tools is `{COPY_PATH}`, from the team repo revision named above.", ""]
    for group, label in GROUPS:
        entries = register.entries[group]
        if entries:
            lines.append(f"- {label}: {_names(entries, servers=group == 'mcp_servers')}")
        else:
            lines.append(f"- {label}: the team has not made this list yet, so nothing is ruled out here.")
    lines += [
        "",
        "A list above is the whole list. Do not add, set up, or suggest an agent tool, a model, or an MCP server that is not on it.",
        "A new one is a pull request in the team repo, which a person approves. You cannot approve one.",
    ]
    return "\n".join(lines) + "\n"
