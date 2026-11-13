"""The engineering rules that apply in one app repo.

Rules live in the team repo, one file each, in the folder of their scope:
company, ios, or android. An app repo gets the company rules and the rules
of its own platform. A rule counts only when the team adopted it. An example
rule shows the format and is never applied.

Every rule has a mode. `advisory`: the agent follows it and reports a break,
and the work goes on. `required`: a break is a blocker. A mode changes only
in the team repo, through a pull request, because this module reads the copy
of the approved revision and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tugboat import apprepo, formats, frontmatter, teamcopy
from tugboat.errors import DataError

MAX_RULE_BYTES = 512 * 1024
MAX_STATEMENT = 600
PLATFORM_NAMES = {"ios": "iOS", "android": "Android"}
COPY = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}"


class Rule(NamedTuple):
    id: str
    title: str
    scope: str  # "company", "ios", or "android"
    mode: str  # "advisory" or "required"
    status: str  # "example" or "adopted"
    statement: str  # what the rule says, without its reason and its check
    path: str  # the rule's file, relative to the app repo


class RuleSet(NamedTuple):
    platform: str | None  # None when the repo is not in the project profile
    applied: tuple[Rule, ...]  # adopted, and in scope for this repo
    examples: int  # example rules in scope. They are not applied.
    problems: tuple[str, ...]  # rule files that could not be read, so they are not applied


def statement(text: str) -> str:
    """The rule itself: the text above the first `## ` heading, as one paragraph."""
    lines = []
    for line in text.splitlines():
        if line.startswith("## "):
            break
        lines.append(line)
    words = " ".join(" ".join(lines).split())
    return words if len(words) <= MAX_STATEMENT else words[: MAX_STATEMENT - 1].rstrip() + "…"


def _read(file: Path, scope: str) -> Rule:
    with file.open("rb") as handle:
        raw = handle.read(MAX_RULE_BYTES + 1)
    if len(raw) > MAX_RULE_BYTES:
        raise DataError(f"is larger than {MAX_RULE_BYTES // 1024} KB")
    rule = formats.validate_rule(*frontmatter.parse(raw.decode("utf-8")))
    if rule["scope"] != scope:
        raise DataError(f"has scope '{rule['scope']}' and is in the folder of '{scope}'")
    if file.name != f"{rule['id']}.md":
        raise DataError(f"must be named {rule['id']}.md, after its id")
    path = f"{COPY}/policies/{scope}/{file.name}"
    return Rule(rule["id"], rule["title"], scope, rule["mode"], rule["status"], statement(rule["text"]), path)


def load(repo: Path, platform: str | None) -> RuleSet:
    """Read the rules for one app repo from its copy of the team repo.

    Company rules always apply. Platform rules apply for the repo's own
    platform only. A rule file that cannot be read is reported and not
    applied: a broken rule is never guessed at.
    """
    scopes = ["company"] + ([platform] if platform in PLATFORM_NAMES else [])
    applied: list[Rule] = []
    problems: list[str] = []
    examples = 0
    for scope in scopes:
        folder = teamcopy.copy_path(repo) / "policies" / scope
        for file in sorted(folder.glob("*.md")) if folder.is_dir() else ():
            if file.name == "README.md" or file.is_symlink():
                continue
            try:
                rule = _read(file, scope)
            except (OSError, UnicodeDecodeError, DataError) as error:
                problems.append(f"the rule file policies/{scope}/{file.name} was not loaded: {error}")
                continue
            if rule.status == "adopted":
                applied.append(rule)
            else:
                examples += 1
    return RuleSet(platform if platform in PLATFORM_NAMES else None, tuple(applied), examples, tuple(problems))


def render(rules: RuleSet, revision: str | None) -> str:
    """The part of the instruction file that hands the rules to the agent."""
    lines = ["## Rules that apply in this repo", ""]
    if rules.platform:
        other = "Android" if rules.platform == "ios" else "iOS"
        lines.append(
            f"This is an {PLATFORM_NAMES[rules.platform]} app repo. The company rules and the {PLATFORM_NAMES[rules.platform]} rules apply here. "
            f"{other} rules do not apply here. Do not read or apply them."
        )
    else:
        lines.append(
            "This repo is not in the project profile, so its platform is not known. Only the company rules are listed. "
            "`tug init`, run in this repo in a terminal, adds the repo to the profile."
        )
    lines.append(f"The rules come from team repo revision `{revision or 'unknown'}`.")
    lines.append("")
    if rules.applied:
        for rule in rules.applied:
            lines.append(f"- `{rule.id}` ({rule.mode}): {rule.title}. {rule.statement} File: `{rule.path}`")
    else:
        lines.append("No rule is adopted for this repo yet, so there is no rule to apply.")
    lines.append("")
    if rules.examples:
        count = f"{rules.examples} example rule{' is' if rules.examples == 1 else 's are'}"
        lines.append(
            f"{count} in the copy. An example rule shows the format. It is not applied, and a break of it is not reported. "
            "The team adopts a rule with a pull request in the team repo that sets `status: adopted`."
        )
    lines.extend(f"Not loaded: {problem}." for problem in rules.problems)
    lines += [
        "",
        "How to use the rules:",
        "",
        "- Follow every rule listed above when you plan, write, and review code.",
        "- A rule with mode `advisory` does not block the work. When the work breaks it, say so in your report: the rule ID, the file, and the reason.",
        "- A rule with mode `required` blocks the work. When the work would break it, stop and tell the developer before you go on.",
        "- Before you report on a rule, read its file. It gives the reason and how to check.",
        "- When two rules conflict, or the work cannot follow a rule, show the developer the rules and the facts. Do not choose between rules, and do not grant an exception yourself. The developer decides.",
        "- You cannot change a rule or its mode. That is a pull request in the team repo, which a person approves.",
        "- In every report that names a rule, name the team repo revision given above.",
    ]
    return "\n".join(lines) + "\n"
