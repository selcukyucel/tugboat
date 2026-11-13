"""The team's AI use policy: one page, in force in every agent session.

The page is `policies/ai-use.md` in the team repo. Like a rule, it counts
only when the team adopted it. An example page shows the format and is not
put in front of the agent as policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tugboat import apprepo, formats, frontmatter, teamcopy
from tugboat.errors import DataError

FILE = "policies/ai-use.md"
COPY_PATH = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}/{FILE}"
MAX_BYTES = 64 * 1024


class Policy(NamedTuple):
    state: str  # "adopted", "example", "missing", or "broken"
    title: str | None = None
    text: str | None = None
    problem: str | None = None  # why a broken page was not loaded

    @property
    def in_force(self) -> bool:
        return self.state == "adopted"


def load(repo: Path) -> Policy:
    """Read the policy from the app repo's copy of the team repo."""
    file = teamcopy.copy_path(repo) / FILE
    if file.is_symlink() or not file.is_file():
        return Policy("missing")
    try:
        with file.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise DataError(f"is larger than {MAX_BYTES // 1024} KB")
        page = formats.validate_ai_use(*frontmatter.parse(raw.decode("utf-8")))
    except (OSError, UnicodeDecodeError, DataError) as error:
        return Policy("broken", problem=f"the AI use policy {FILE} was not loaded: {error}")
    return Policy(page["status"], page["title"], page["text"])


def unfinished(policy: Policy) -> str | None:
    """What is missing before a policy is in force, as a setup step. None when one is."""
    how = "A pull request in the team repo that sets `status: adopted` in `policies/ai-use.md` puts it in force"
    if policy.state == "example":
        return f"the team has not adopted its AI use policy yet. {how}"
    if policy.state == "missing":
        return "the team repo has no AI use policy. `policies/ai-use.md` holds it: the README in `policies/` gives the format"
    return None


def _one_level_down(text: str) -> str:
    """Make the page's headings sit under the section heading of the instruction file."""
    return "\n".join("#" + line if line.startswith("#") else line for line in text.splitlines())


def render(policy: Policy) -> str:
    """The part of the instruction file that puts the policy in front of the agent."""
    lines = ["## AI use policy", ""]
    if policy.in_force:
        lines += [
            f"The team adopted this policy. It is in force in every session. It is the page `{COPY_PATH}`, from the team repo revision named above.",
            "When a request would break it, say so before you act, and leave that part undone. You cannot make an exception to it yourself.",
            "",
            f"### {policy.title}",
            "",
            _one_level_down(policy.text or ""),
        ]
    elif policy.state == "example":
        lines.append(
            "The team repo holds an example AI use policy that the team has not adopted. It is not in force, so it is not shown here. "
            "The team adopts it with a pull request in the team repo that sets `status: adopted`."
        )
    elif policy.state == "broken":
        lines.append(f"No AI use policy is in force. Not loaded: {policy.problem}.")
    else:
        lines.append("The team repo has no AI use policy, so none is in force.")
    return "\n".join(lines) + "\n"
