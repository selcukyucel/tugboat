"""The file formats of a team repo.

One function per kind of file. Each takes parsed data, returns the checked
form, and raises DataError with a message that names the field.

JSON files carry a "schema" field such as "tug.profile/1". The number rises
only when an older tool could misread the file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from tugboat import secretscan
from tugboat.errors import DataError
from tugboat.validate import (
    require_choice,
    require_exact_fields,
    require_list,
    require_object,
    require_text,
    require_unique,
    validate_date,
    validate_glob,
    validate_repo_ref,
    validate_slug,
)
from tugboat.version import parse_version

MARKER_SCHEMA = "tug.team-repo/1"
PROFILE_SCHEMA = "tug.profile/1"
PROTECTED_PATHS_SCHEMA = "tug.protected-paths/1"
AI_TOOLS_SCHEMA = "tug.ai-tools/1"
ESTATE_SCHEMA = "tug.estate/1"

PLATFORMS = frozenset({"ios", "android"})
TRACKERS = frozenset({"jira", "github-projects"})
CRITERIA_KINDS = frozenset({"field", "section", "task-list"})
CI_FEATURES = frozenset({"secret-scan", "guardrail-pack", "dependency-verification"})
SURFACES = frozenset({"claude-code", "copilot-cli", "copilot-vscode"})
APPROVAL_MODES = frozenset({"teammate", "owner"})
FRAMEWORKS = frozenset({"nist-ai-rmf"})

# Rule scope -> the prefix of its rule IDs. The scope is also the folder name.
RULE_SCOPES = {"company": "COM", "ios": "IOS", "android": "AND"}
RULE_MODES = frozenset({"advisory", "required"})
RULE_STATUSES = frozenset({"example", "adopted"})
DECISION_STATUSES = frozenset({"proposed", "accepted", "superseded"})
DECISION_HEADINGS = ("Context", "Options", "Choice", "Reason")
# What the AI use policy must say something about. One heading for each.
AI_USE_HEADINGS = ("Data that must never enter a prompt", "Accounts", "Accountability")
AI_USE_MAX = 8000  # one page

_RULE_ID = re.compile(r"^(COM|IOS|AND)-[0-9]{3}$")
_DECISION_ID = re.compile(r"^DEC-[0-9]{4}$")
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:*()-]{0,99}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_COMMAND_WORD = re.compile(r"^[@A-Za-z0-9][A-Za-z0-9@/._+-]{0,99}$")
_SECRET_ADVICE = "Take it out. If it is a real secret, replace it where it is used too, because Git keeps what was pushed."


def _schema(data: Mapping[str, Any], expected: str, label: str) -> None:
    found = data.get("schema")
    if found != expected:
        raise DataError(f'{label} must have "schema": "{expected}" (found {found!r})')


def _choices(value: object, label: str, allowed: frozenset[str], *, minimum: int = 0) -> list[str]:
    items = require_list(value, label, maximum=len(allowed), minimum=minimum)
    return require_unique([require_choice(item, f"{label} item", allowed) for item in items], label)


def _body(body: str, label: str, maximum: int = 20_000) -> str:
    text = body.strip()
    if not text:
        raise DataError(f"{label} has no text under its header")
    if len(text) > maximum:
        raise DataError(f"{label} text cannot exceed {maximum} characters")
    return text


def validate_rule_id(value: object, label: str) -> str:
    text = require_text(value, label, 20)
    if not _RULE_ID.fullmatch(text):
        raise DataError(f"{label} must look like COM-001, IOS-001, or AND-001")
    return text


# ---------------------------------------------------------------- JSON files


def validate_marker(data: object) -> dict[str, Any]:
    """tugboat.json: says that this folder is a Tugboat team repo."""
    marker = require_object(data, "tugboat.json")
    require_exact_fields(marker, required=frozenset({"schema"}), label="tugboat.json")
    _schema(marker, MARKER_SCHEMA, "tugboat.json")
    return dict(marker)


def validate_profile(data: object) -> dict[str, Any]:
    """profile.json: the choices one project made at init."""
    label = "profile"
    profile = require_object(data, label)
    require_exact_fields(
        profile,
        required=frozenset(
            {"schema", "project", "app_repos", "tracker", "ci_features", "surfaces", "approval_mode", "minimum_tool_version", "frameworks"}
        ),
        label=label,
    )
    _schema(profile, PROFILE_SCHEMA, label)

    repos = []
    for index, raw in enumerate(require_list(profile["app_repos"], "profile app_repos", maximum=50)):
        item_label = f"profile app_repos[{index}]"
        repo = require_object(raw, item_label)
        require_exact_fields(repo, required=frozenset({"github", "default_branch", "platform"}), label=item_label)
        branch = require_text(repo["default_branch"], f"{item_label} default_branch", 100)
        if not _BRANCH.fullmatch(branch) or ".." in branch:
            raise DataError(f"{item_label} default_branch is not a valid branch name")
        repos.append(
            {
                "github": validate_repo_ref(repo["github"], f"{item_label} github"),
                "default_branch": branch,
                "platform": require_choice(repo["platform"], f"{item_label} platform", PLATFORMS),
            }
        )
    require_unique([repo["github"].lower() for repo in repos], "profile app_repos")

    tracker = require_object(profile["tracker"], "profile tracker")
    require_exact_fields(tracker, required=frozenset({"kind", "criteria_source"}), label="profile tracker")
    source = tracker["criteria_source"]
    if source is not None:
        source = require_object(source, "profile tracker criteria_source")
        require_exact_fields(source, required=frozenset({"kind", "name"}), label="profile tracker criteria_source")
        source = {
            "kind": require_choice(source["kind"], "profile tracker criteria_source kind", CRITERIA_KINDS),
            "name": require_text(source["name"], "profile tracker criteria_source name", 200),
        }

    return {
        "schema": PROFILE_SCHEMA,
        "project": validate_slug(profile["project"], "profile project", 60),
        "app_repos": repos,
        "tracker": {"kind": require_choice(tracker["kind"], "profile tracker kind", TRACKERS), "criteria_source": source},
        "ci_features": _choices(profile["ci_features"], "profile ci_features", CI_FEATURES),
        "surfaces": _choices(profile["surfaces"], "profile surfaces", SURFACES, minimum=1),
        "approval_mode": require_choice(profile["approval_mode"], "profile approval_mode", APPROVAL_MODES),
        "minimum_tool_version": str(parse_version(profile["minimum_tool_version"], "profile minimum_tool_version")),
        "frameworks": _choices(profile["frameworks"], "profile frameworks", FRAMEWORKS),
    }


def validate_protected_paths(data: object) -> dict[str, Any]:
    """permissions/protected-paths.json: files the agent must not edit unasked."""
    label = "protected paths"
    document = require_object(data, label)
    require_exact_fields(document, required=frozenset({"schema", "paths"}), label=label)
    _schema(document, PROTECTED_PATHS_SCHEMA, label)
    paths = []
    for index, raw in enumerate(require_list(document["paths"], f"{label} paths", maximum=500)):
        item_label = f"{label} paths[{index}]"
        entry = require_object(raw, item_label)
        require_exact_fields(entry, required=frozenset({"pattern", "platform", "reason"}), label=item_label)
        paths.append(
            {
                "pattern": validate_glob(entry["pattern"], f"{item_label} pattern"),
                "platform": require_choice(entry["platform"], f"{item_label} platform", PLATFORMS | {"any"}),
                "reason": require_text(entry["reason"], f"{item_label} reason", 300),
            }
        )
    require_unique([f"{path['platform']} {path['pattern']}" for path in paths], f"{label} paths")
    return {"schema": PROTECTED_PATHS_SCHEMA, "paths": paths}


def validate_ai_tools(data: object) -> dict[str, Any]:
    """permissions/ai-tools.json: the approved AI tools, models, and MCP servers."""
    label = "AI tool register"
    groups = ("agent_tools", "models", "mcp_servers")
    document = require_object(data, label)
    require_exact_fields(document, required=frozenset({"schema", *groups}), label=label)
    _schema(document, AI_TOOLS_SCHEMA, label)
    result: dict[str, Any] = {"schema": AI_TOOLS_SCHEMA}
    for group in groups:
        entries = []
        for index, raw in enumerate(require_list(document[group], f"{label} {group}", maximum=100)):
            item_label = f"{label} {group}[{index}]"
            entry = require_object(raw, item_label)
            # Only an MCP server needs a way to be recognised on a machine.
            known_by = frozenset({"hosts", "commands"}) if group == "mcp_servers" else frozenset()
            require_exact_fields(entry, required=frozenset({"id", "name"}), optional=frozenset({"note"}) | known_by, label=item_label)
            checked = {
                "id": validate_slug(entry["id"], f"{item_label} id", 60),
                "name": require_text(entry["name"], f"{item_label} name", 100),
            }
            if "note" in entry:
                checked["note"] = require_text(entry["note"], f"{item_label} note", 300)
            for field, pattern, what in (("hosts", _HOST, "a host name in lowercase, such as mcp.example.com"), ("commands", _COMMAND_WORD, "a program or package name with no spaces")):
                if field not in entry:
                    continue
                words = [require_text(item, f"{item_label} {field} item", 100) for item in require_list(entry[field], f"{item_label} {field}", maximum=10, minimum=1)]
                for word in words:
                    if not pattern.fullmatch(word):
                        raise DataError(f"{item_label} {field} item '{word}' must be {what}")
                checked[field] = require_unique(words, f"{item_label} {field}")
            entries.append(checked)
        require_unique([entry["id"] for entry in entries], f"{label} {group}")
        if group == "mcp_servers":
            # One host or command cannot stand for two servers.
            require_unique([word for entry in entries for word in entry.get("hosts", [])], f"{label} {group} hosts")
            require_unique([word for entry in entries for word in entry.get("commands", [])], f"{label} {group} commands")
        result[group] = entries
    return result


def validate_estate(data: object) -> dict[str, Any]:
    """estate/estate.json: apps, modules, repos, owners, and environments."""
    label = "estate map"
    document = require_object(data, label)
    require_exact_fields(document, required=frozenset({"schema", "apps"}), label=label)
    _schema(document, ESTATE_SCHEMA, label)
    apps = []
    for index, raw in enumerate(require_list(document["apps"], f"{label} apps", maximum=50)):
        app_label = f"{label} apps[{index}]"
        app = require_object(raw, app_label)
        require_exact_fields(
            app, required=frozenset({"id", "name", "platform", "repo", "owner", "environments", "modules"}), label=app_label
        )
        environments = [
            validate_slug(item, f"{app_label} environments item", 40)
            for item in require_list(app["environments"], f"{app_label} environments", maximum=20)
        ]
        modules = []
        for position, raw_module in enumerate(require_list(app["modules"], f"{app_label} modules", maximum=500)):
            module_label = f"{app_label} modules[{position}]"
            module = require_object(raw_module, module_label)
            require_exact_fields(module, required=frozenset({"id", "paths", "owner"}), label=module_label)
            paths = [
                validate_glob(item, f"{module_label} paths item")
                for item in require_list(module["paths"], f"{module_label} paths", maximum=50, minimum=1)
            ]
            modules.append(
                {
                    "id": validate_slug(module["id"], f"{module_label} id", 60),
                    "paths": require_unique(paths, f"{module_label} paths"),
                    "owner": require_text(module["owner"], f"{module_label} owner", 100),
                }
            )
        require_unique([module["id"] for module in modules], f"{app_label} modules")
        apps.append(
            {
                "id": validate_slug(app["id"], f"{app_label} id", 60),
                "name": require_text(app["name"], f"{app_label} name", 100),
                "platform": require_choice(app["platform"], f"{app_label} platform", PLATFORMS),
                "repo": validate_repo_ref(app["repo"], f"{app_label} repo"),
                "owner": require_text(app["owner"], f"{app_label} owner", 100),
                "environments": require_unique(environments, f"{app_label} environments"),
                "modules": modules,
            }
        )
    require_unique([app["id"] for app in apps], f"{label} apps")
    return {"schema": ESTATE_SCHEMA, "apps": apps}


# ------------------------------------------------------------ Markdown files


def validate_rule(fields: Mapping[str, Any], body: str) -> dict[str, Any]:
    """policies/<scope>/<ID>.md: one engineering rule."""
    label = "rule"
    require_exact_fields(fields, required=frozenset({"id", "title", "scope", "mode", "status"}), label=label)
    rule_id = validate_rule_id(fields["id"], "rule id")
    scope = require_choice(fields["scope"], "rule scope", frozenset(RULE_SCOPES))
    if not rule_id.startswith(RULE_SCOPES[scope] + "-"):
        raise DataError(f"rule id {rule_id} does not match scope '{scope}', whose IDs start with {RULE_SCOPES[scope]}-")
    return {
        "id": rule_id,
        "title": require_text(fields["title"], "rule title", 150),
        "scope": scope,
        "mode": require_choice(fields["mode"], "rule mode", RULE_MODES),
        "status": require_choice(fields["status"], "rule status", RULE_STATUSES),
        "text": _body(body, "rule"),
    }


def validate_ai_use(fields: Mapping[str, Any], body: str) -> dict[str, Any]:
    """policies/ai-use.md: the one page that says how the team uses AI tools."""
    label = "AI use policy"
    require_exact_fields(fields, required=frozenset({"title", "status"}), label=label)
    text = _body(body, label, AI_USE_MAX)
    headings = {line[3:].strip() for line in text.splitlines() if line.startswith("## ")}
    missing = [heading for heading in AI_USE_HEADINGS if heading not in headings]
    if missing:
        raise DataError(f"{label} is missing sections: {', '.join('## ' + heading for heading in missing)}")
    return {
        "title": require_text(fields["title"], f"{label} title", 150),
        "status": require_choice(fields["status"], f"{label} status", RULE_STATUSES),
        "text": text,
    }


def validate_note(fields: Mapping[str, Any], body: str) -> dict[str, Any]:
    """knowledge/**/*.md: one note about the code. No secret and no raw chat transcript."""
    label = "note"
    require_exact_fields(fields, required=frozenset({"title", "owner", "repo", "paths", "last_verified"}), label=label)
    paths = [
        validate_glob(item, "note paths item")
        for item in require_list(fields["paths"], "note paths", maximum=50, minimum=1)
    ]
    note = {
        "title": require_text(fields["title"], "note title", 150),
        "owner": require_text(fields["owner"], "note owner", 100),
        "repo": validate_repo_ref(fields["repo"], "note repo"),
        "paths": require_unique(paths, "note paths"),
        "last_verified": validate_date(fields["last_verified"], "note last_verified"),
        "text": _body(body, "note"),
    }
    in_header = secretscan.find(f"{note['title']}\n{note['owner']}")
    if in_header:
        raise DataError(f"note header holds what looks like a secret ({in_header[0].kind}). {_SECRET_ADVICE}")
    in_text = secretscan.find(note["text"])
    if in_text:
        raise DataError(f"note holds what looks like a secret: {secretscan.describe(in_text)} of its text. {_SECRET_ADVICE}")
    if secretscan.looks_like_transcript(note["text"]):
        raise DataError("note looks like a raw chat transcript: it has lines that open a turn, such as 'User:'. Write what was learned, in plain sentences.")
    return note


def validate_decision(fields: Mapping[str, Any], body: str) -> dict[str, Any]:
    """decisions/DEC-NNNN-*.md: one technology or architecture choice.

    `repo` is optional. With it, the record and its paths are about one app
    repo. Without it, the record holds for the whole project.
    """
    label = "decision record"
    require_exact_fields(fields, required=frozenset({"id", "title", "date", "status", "paths"}), optional=frozenset({"repo"}), label=label)
    decision_id = require_text(fields["id"], "decision record id", 20)
    if not _DECISION_ID.fullmatch(decision_id):
        raise DataError("decision record id must look like DEC-0001")
    text = _body(body, "decision record")
    headings = {line[3:].strip() for line in text.splitlines() if line.startswith("## ")}
    missing = [heading for heading in DECISION_HEADINGS if heading not in headings]
    if missing:
        raise DataError(f"decision record is missing sections: {', '.join('## ' + heading for heading in missing)}")
    paths = [
        validate_glob(item, "decision record paths item")
        for item in require_list(fields["paths"], "decision record paths", maximum=50)
    ]
    title = require_text(fields["title"], "decision record title", 150)
    found = secretscan.find(f"{title}\n{text}")
    if found:
        raise DataError(f"decision record holds what looks like a secret ({found[0].kind}). {_SECRET_ADVICE}")
    return {
        "id": decision_id,
        "title": title,
        "date": validate_date(fields["date"], "decision record date"),
        "status": require_choice(fields["status"], "decision record status", DECISION_STATUSES),
        "paths": require_unique(paths, "decision record paths"),
        "repo": validate_repo_ref(fields["repo"], "decision record repo") if "repo" in fields else None,
        "text": text,
    }


def validate_agent(fields: Mapping[str, Any], body: str) -> dict[str, Any]:
    """agents/<name>.md: one approved agent definition."""
    label = "agent definition"
    require_exact_fields(fields, required=frozenset({"name", "description", "tools", "rules"}), label=label)
    tools = []
    for item in require_list(fields["tools"], "agent definition tools", maximum=50, minimum=1):
        tool = require_text(item, "agent definition tools item", 100)
        if not _TOOL_NAME.fullmatch(tool):
            raise DataError(f"agent definition tools item '{tool}' is not a valid tool name")
        tools.append(tool)
    rules = [
        validate_rule_id(item, "agent definition rules item")
        for item in require_list(fields["rules"], "agent definition rules", maximum=200)
    ]
    return {
        "name": validate_slug(fields["name"], "agent definition name", 60),
        "description": require_text(fields["description"], "agent definition description", 300),
        "tools": require_unique(tools, "agent definition tools"),
        "rules": require_unique(rules, "agent definition rules"),
        "text": _body(body, "agent definition"),
    }


def validate_skill_header(fields: Mapping[str, Any]) -> dict[str, Any]:
    """skills/<name>/SKILL.md: only the two fields every agent tool needs."""
    missing = sorted({"name", "description"} - set(fields))
    if missing:
        raise DataError(f"skill is missing fields: {', '.join(missing)}")
    return {
        "name": validate_slug(fields["name"], "skill name", 60),
        "description": require_text(fields["description"], "skill description", 1000),
    }
