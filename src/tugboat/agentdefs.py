"""The agent registry: the agent definitions the team approved.

A definition is one Markdown file under `agents/` in the team repo. It
states the agent's purpose, the tools it may use, and the rule IDs it
applies. It changes only through a pull request in the team repo.

Sync installs each definition into the app repo's `.claude/agents/`, which
every agent tool reads, and lists them in the instruction file. A definition
that names rules of one platform only is installed in that platform's repos
only, as a skill is.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tugboat import apprepo, formats, frontmatter, teamcopy
from tugboat.errors import DataError

FOLDER = "agents"
INSTALLED = ".claude/agents"
COPY = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}"
MAX_AGENT_BYTES = 512 * 1024
MAX_LISTED = 20
SKIPPED_NAMES = frozenset({"README.md"})


class Agent(NamedTuple):
    name: str
    description: str
    tools: tuple[str, ...]
    rules: tuple[str, ...]
    text: str

    @property
    def target(self) -> str:
        """Where the definition is installed, relative to the app repo."""
        return f"{INSTALLED}/{self.name}.md"

    def installed_text(self) -> str:
        """The file an agent tool reads: the three fields every tool knows, then the instructions."""
        body = self.text
        if self.rules:
            body += f"\n\nRules this agent applies: {', '.join(self.rules)}. Read each one from `{COPY}/policies/`.\n"
        header = {"name": self.name, "description": self.description, "tools": ", ".join(self.tools)}
        return frontmatter.render(header, body)


class Registry(NamedTuple):
    agents: tuple[Agent, ...]  # the approved definitions for this repo
    problems: tuple[str, ...]  # definitions that were not installed, and why


def load(repo: Path, platform: str | None) -> Registry:
    """Read the approved definitions for one app repo from its copy of the team repo."""
    folder = teamcopy.copy_path(repo) / FOLDER
    agents: list[Agent] = []
    problems: list[str] = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() and not folder.is_symlink() else ():
        if path.name in SKIPPED_NAMES or path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_AGENT_BYTES:
                raise DataError(f"is larger than {MAX_AGENT_BYTES // 1024} KB")
            found = formats.validate_agent(*frontmatter.parse(raw.decode("utf-8")))
            if path.name != f"{found['name']}.md":
                raise DataError("its name does not match its file name")
        except (OSError, UnicodeDecodeError, DataError) as error:
            problems.append(f"agent '{path.stem}' was not installed: {error}")
            continue
        wanted = formats.platforms_of_rules(found["rules"])
        if platform in formats.PLATFORMS and wanted and platform not in wanted:
            continue  # its rules are for the other platform, so the agent is too
        agents.append(Agent(found["name"], found["description"], tuple(found["tools"]), tuple(found["rules"]), found["text"]))
    return Registry(tuple(agents), tuple(problems))


def render(registry: Registry) -> str:
    """The part of the instruction file that names the agents the team approved."""
    lines = ["## Approved agents", ""]
    if not registry.agents:
        lines.append("The team repo has no approved agent definition for this repo.")
        return "\n".join(lines) + "\n"
    count = "1 agent definition" if len(registry.agents) == 1 else f"{len(registry.agents)} agent definitions"
    lines += [f"The team approved {count} for this repo. Each is installed in `{INSTALLED}/`.", ""]
    for agent in registry.agents[:MAX_LISTED]:
        rules = f" Rules: {', '.join(agent.rules)}." if agent.rules else ""
        lines.append(f"- `{agent.name}`: {agent.description.rstrip('.')}. Tools: {', '.join(agent.tools)}.{rules}")
    if len(registry.agents) > MAX_LISTED:
        lines.append(f"- and {len(registry.agents) - MAX_LISTED} more, in `{INSTALLED}/`.")
    lines += [
        "",
        "Use an approved agent for the job its description names. Give it what it asks for, and pass its report on to the developer as it is.",
        f"A definition changes only through a pull request in the team repo. Do not edit a file in `{INSTALLED}/` that Tugboat wrote.",
    ]
    return "\n".join(lines) + "\n"
