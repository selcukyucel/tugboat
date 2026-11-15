"""The formats of the shared task records.

Task history is a set of small JSON files. Each file is one record. A record
is written once and never edited: the current state of a task is worked out
from its records. There are five kinds:

- a claim: this developer, on this machine, took this story
- a task event: one step of the work, with the results of that step and what comes next
- a report: check results, analysis findings, the acceptance report, or the QA pack
- a session summary: what one agent session did, in a few paragraphs
- a handover note: why a task was released, and what to do next

One function per kind. Each takes parsed data, returns the checked form, and
raises DataError with a message that names the field. A missing field is
refused, and so is a field nobody defined.

Every record carries a "schema" field such as "tug.task-event/1". The number
rises only when an older tool could misread the record. A tool that meets a
number higher than it knows raises NewerSchema, so that the caller can skip
the record and say so. It never guesses.

What a record cannot hold: the raw chat of a session, and source code beyond
file paths. No field exists for either, and text that reads like a chat
transcript is refused. The check for secrets is a separate step before a
record is published.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from typing import Any

from tugboat import formats, secretscan
from tugboat.errors import DataError
from tugboat.validate import (
    canonical_timestamp,
    require_bool,
    require_choice,
    require_exact_fields,
    require_int,
    require_list,
    require_object,
    require_text,
    require_unique,
    validate_relative_path,
    validate_repo_ref,
    validate_slug,
)
from tugboat.version import parse_version

CLAIM, EVENT, REPORT, SUMMARY, HANDOVER = "claim", "task-event", "report", "session-summary", "handover"
KINDS = (CLAIM, EVENT, REPORT, SUMMARY, HANDOVER)
# The newest schema number this tool reads and writes, for each kind.
VERSIONS: dict[str, int] = dict.fromkeys(KINDS, 1)

# The steps of a task that leave an event. A release is a handover note, not an event.
STARTED, PLAN_SAVED, CHECKS_RUN, ANALYSIS_DONE, ACCEPTANCE_REPORTED, CHECKPOINT, CHOICE_MADE, RESUMED = (
    "started", "plan-saved", "checks-run", "analysis-done", "acceptance-reported", "checkpoint", "choice-made", "resumed",
)  # fmt: skip
STEPS = frozenset({STARTED, PLAN_SAVED, CHECKS_RUN, ANALYSIS_DONE, ACCEPTANCE_REPORTED, CHECKPOINT, CHOICE_MADE, RESUMED})
# What a check can say. The four stay distinct everywhere: blocked and untested are never shown as passed.
PASSED, FAILED, BLOCKED, UNTESTED = "passed", "failed", "blocked", "untested"
RESULT_STATUSES = frozenset({PASSED, FAILED, BLOCKED, UNTESTED})
# What an acceptance report can say about one criterion.
MET, NOT_MET, UNVERIFIED, NOT_APPLICABLE = "met", "not-met", "unverified", "not-applicable"
CRITERION_STATUSES = frozenset({MET, NOT_MET, BLOCKED, UNVERIFIED, NOT_APPLICABLE})
# What a developer can pick at a checkpoint.
CHOICES = frozenset({"inspect-findings", "fix-findings", "leave-unchanged", "pause", "switch-task"})
CHECKS, ANALYSIS, ACCEPTANCE, QA_PACK = "checks", "analysis", "acceptance", "qa-pack"
REPORT_KINDS = frozenset({CHECKS, ANALYSIS, ACCEPTANCE, QA_PACK})
SEVERITIES = frozenset({"high", "medium", "low"})

_SCHEMA = re.compile(r"^tug\.([a-z][a-z-]{0,40})/([1-9][0-9]{0,5})$")
_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,19}-[1-9][0-9]{0,8}$")
_ISSUE_NUMBER = re.compile(r"^[1-9][0-9]{0,8}$")
_LINK = re.compile(r"^https://[^\s/@]+(?:/[^\s]*)?$")

# The fields every record has, whatever its kind.
_COMMON = frozenset({"schema", "id", "at", "tool_version", "task", "repo", "platform", "author", "machine"})
# Where the work stood, and which session wrote the record.
_POSITION = frozenset({"branch", "commit"})
_SESSION = frozenset({"session", "surface"})


class NewerSchema(DataError):
    """A record that a newer tool wrote. This tool cannot read it, and does not try."""

    def __init__(self, kind: str, version: int) -> None:
        super().__init__(f"this record has the schema tug.{kind}/{version}, and this tool reads up to tug.{kind}/{VERSIONS[kind]}. A newer tug wrote it. Update `tug` to read it.")
        self.kind = kind
        self.version = version


def schema_name(kind: str) -> str:
    """The schema this tool writes for one kind of record."""
    return f"tug.{kind}/{VERSIONS[kind]}"


def schema_of(data: object) -> tuple[str, int]:
    """The kind and the schema number a record says it has."""
    found = data.get("schema") if isinstance(data, dict) else None
    match = _SCHEMA.fullmatch(found) if isinstance(found, str) else None
    if match is None or match.group(1) not in VERSIONS:
        raise DataError(f'a record must have a "schema" such as "{schema_name(EVENT)}" (found {found!r})')
    return match.group(1), int(match.group(2))


def new_id(now: datetime | None = None) -> str:
    """An ID for a new record. It starts with the time, so IDs sort by age, and ends with random letters, so two machines never make the same one."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(6)}"


# ------------------------------------------------------------- small checks


def _line(value: object, label: str, maximum: int) -> str:
    text = require_text(value, label, maximum)
    if "\n" in text or "\r" in text:
        raise DataError(f"{label} must be one line")
    return text


def _prose(value: object, label: str, maximum: int) -> str:
    """Text of a few paragraphs. A chat transcript is refused: a record holds what was done, not what was said."""
    text = require_text(value, label, maximum)
    if secretscan.looks_like_transcript(text):
        raise DataError(f"{label} reads like a chat transcript. A record holds a summary, never the raw chat")
    return text


def _optional(value: object, check: Callable[..., Any], *args: Any) -> Any:
    return None if value is None else check(value, *args)


def _record_id(value: object, label: str) -> str:
    text = require_text(value, label, 40)
    if not _ID.fullmatch(text):
        raise DataError(f"{label} must look like 20261115T101530Z-3f9a1c2b7d4e")
    return text


def _revision(value: object, label: str) -> str:
    text = require_text(value, label, 64)
    if not _REVISION.fullmatch(text):
        raise DataError(f"{label} must be a full Git commit ID")
    return text


def _branch(value: object, label: str) -> str:
    text = require_text(value, label, 200)
    if not _BRANCH.fullmatch(text) or ".." in text:
        raise DataError(f"{label} is not a valid branch name")
    return text


def _link(value: object, label: str) -> str:
    text = require_text(value, label, 500)
    if not _LINK.fullmatch(text):
        raise DataError(f"{label} must be an https address with no sign-in in it")
    return text


def _lines(value: object, label: str, *, maximum: int, length: int, minimum: int = 0) -> list[str]:
    return [_line(item, f"{label}[{index}]", length) for index, item in enumerate(require_list(value, label, maximum=maximum, minimum=minimum))]


def _task(value: object, label: str) -> dict[str, Any]:
    """The story a record belongs to. Both trackers give the same shape."""
    task = require_object(value, label)
    require_exact_fields(task, required=frozenset({"tracker", "key"}), label=label)
    tracker = require_choice(task["tracker"], f"{label} tracker", formats.TRACKERS)
    key = _line(task["key"], f"{label} key", 200)
    if tracker == "jira":
        if not _JIRA_KEY.fullmatch(key):
            raise DataError(f"{label} key must be a Jira issue key such as SHOP-123")
    else:
        repo, separator, number = key.partition("#")
        if not separator or not _ISSUE_NUMBER.fullmatch(number):
            raise DataError(f"{label} key must be a GitHub issue written as owner/name#123")
        validate_repo_ref(repo, f"{label} key")
    return {"tracker": tracker, "key": key}


def _login(value: object, label: str) -> str:
    author = _line(value, label, 39)
    if not _LOGIN.fullmatch(author):
        raise DataError(f"{label} must be a GitHub account name")
    return author


def _common(record: Mapping[str, Any], kind: str, label: str) -> dict[str, Any]:
    found, version = schema_of(record)
    if found != kind:
        raise DataError(f'{label} must have "schema": "{schema_name(kind)}" (found {record["schema"]!r})')
    if version > VERSIONS[kind]:
        raise NewerSchema(kind, version)
    author = _login(record["author"], f"{label} author")
    return {
        "schema": schema_name(kind),
        "id": _record_id(record["id"], f"{label} id"),
        "at": canonical_timestamp(record["at"], f"{label} at"),
        "tool_version": str(parse_version(record["tool_version"], f"{label} tool_version")),
        "task": _task(record["task"], f"{label} task"),
        "repo": validate_repo_ref(record["repo"], f"{label} repo"),
        "platform": require_choice(record["platform"], f"{label} platform", formats.PLATFORMS),
        "author": author,
        "machine": _line(record["machine"], f"{label} machine", 100),
    }


def _position(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    # No branch is a detached HEAD.
    return {"branch": _optional(record["branch"], _branch, f"{label} branch"), "commit": _revision(record["commit"], f"{label} commit")}


def _session(record: Mapping[str, Any], label: str, *, required: bool = False) -> dict[str, Any]:
    """The agent session that wrote the record. Both are empty for a record a person made in a terminal."""
    session = _optional(record["session"], _line, f"{label} session", 200)
    surface = _optional(record["surface"], require_choice, f"{label} surface", formats.SURFACES)
    if required and (session is None or surface is None):
        raise DataError(f"{label} must name its session and its surface")
    if (session is None) != (surface is None):
        raise DataError(f"{label} must give session and surface together, or neither")
    return {"session": session, "surface": surface}


def _results(value: object, label: str) -> list[dict[str, Any]]:
    """What each check said at this step, by name, for the event's own commit. `report` is the ID of the report record that holds the detail."""
    results = []
    for index, raw in enumerate(require_list(value, label, maximum=30)):
        item_label = f"{label}[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"name", "status", "summary", "report"}), label=item_label)
        results.append(
            {
                "name": validate_slug(item["name"], f"{item_label} name", 60),
                "status": require_choice(item["status"], f"{item_label} status", RESULT_STATUSES),
                "summary": _optional(item["summary"], _line, f"{item_label} summary", 300),
                "report": _optional(item["report"], _record_id, f"{item_label} report"),
            }
        )
    require_unique([result["name"] for result in results], label)
    return results


def _criteria(value: object, label: str) -> list[dict[str, Any]]:
    """Acceptance criteria, each with the tracker's own text and one status."""
    criteria = []
    for index, raw in enumerate(require_list(value, label, maximum=100)):
        item_label = f"{label}[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"text", "status", "evidence"}), label=item_label)
        status = require_choice(item["status"], f"{item_label} status", CRITERION_STATUSES)
        evidence = _optional(item["evidence"], _prose, f"{item_label} evidence", 1000)
        if status == MET and evidence is None:
            # A passing build is not evidence of behaviour. Without evidence a criterion stays unverified.
            raise DataError(f"{item_label} is 'met' and names no evidence. Without evidence a criterion is 'unverified'")
        criteria.append({"text": _prose(item["text"], f"{item_label} text", 2000), "status": status, "evidence": evidence})
    return criteria


# --------------------------------------------------------------- the kinds


def validate_claim(data: object) -> dict[str, Any]:
    """A claim: this developer, on this machine, took this story at this time. It is advisory and locks nothing."""
    label = "claim"
    record = require_object(data, label)
    require_exact_fields(record, required=_COMMON, label=label)
    return _common(record, CLAIM, label)


def _story(value: object, label: str) -> dict[str, Any]:
    story = require_object(value, label)
    require_exact_fields(story, required=frozenset({"title", "url", "epic", "milestone"}), label=label)
    return {
        "title": _line(story["title"], f"{label} title", 300),
        "url": _optional(story["url"], _link, f"{label} url"),
        "epic": _optional(story["epic"], _line, f"{label} epic", 200),
        "milestone": _optional(story["milestone"], _line, f"{label} milestone", 200),
    }


def _choice(value: object, label: str) -> dict[str, Any]:
    choice = require_object(value, label)
    require_exact_fields(choice, required=frozenset({"selected", "detail"}), label=label)
    return {
        "selected": require_choice(choice["selected"], f"{label} selected", CHOICES),
        "detail": _optional(choice["detail"], _line, f"{label} detail", 500),
    }


def _state_results(value: object, label: str) -> list[dict[str, Any]]:
    """The results a task has at a checkpoint. Each one keeps what it was made for, so that it can still be judged stale after its own event expired."""
    results = []
    fields = frozenset({"name", "status", "summary", "report", "kind", "commit", "team_repo_revision", "at"})
    for index, raw in enumerate(require_list(value, label, maximum=30)):
        item_label = f"{label}[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=fields, label=item_label)
        results.append(
            {
                "name": validate_slug(item["name"], f"{item_label} name", 60),
                "status": require_choice(item["status"], f"{item_label} status", RESULT_STATUSES),
                "summary": _optional(item["summary"], _line, f"{item_label} summary", 300),
                "report": _optional(item["report"], _record_id, f"{item_label} report"),
                "kind": _optional(item["kind"], require_choice, f"{item_label} kind", REPORT_KINDS),
                "commit": _revision(item["commit"], f"{item_label} commit"),
                "team_repo_revision": _revision(item["team_repo_revision"], f"{item_label} team_repo_revision"),
                "at": canonical_timestamp(item["at"], f"{item_label} at"),
            }
        )
    require_unique([result["name"] for result in results], label)
    return results


def _claims(value: object, label: str) -> list[dict[str, Any]]:
    claims = []
    for index, raw in enumerate(require_list(value, label, maximum=20)):
        item_label = f"{label}[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"author", "machine", "at"}), label=item_label)
        claims.append(
            {
                "author": _login(item["author"], f"{item_label} author"),
                "machine": _line(item["machine"], f"{item_label} machine", 100),
                "at": canonical_timestamp(item["at"], f"{item_label} at"),
            }
        )
    return claims


def _state(value: object, label: str) -> dict[str, Any]:
    """The full current state of a task, kept in a checkpoint so that the task survives the expiry of its early records."""
    state = require_object(value, label)
    require_exact_fields(state, required=frozenset({"step", "commit", "results", "pending_choice", "next_action", "story", "claims"}), label=label)
    step = require_choice(state["step"], f"{label} step", STEPS)
    if step == CHECKPOINT:
        raise DataError(f"{label} step must be the step the task had reached, not 'checkpoint'")
    return {
        "step": step,
        "commit": _revision(state["commit"], f"{label} commit"),
        "results": _state_results(state["results"], f"{label} results"),
        "pending_choice": _optional(state["pending_choice"], _line, f"{label} pending_choice", 500),
        "next_action": _line(state["next_action"], f"{label} next_action", 500),
        # The story and who holds it, because the records that said so first may have expired by now.
        "story": _optional(state["story"], _story, f"{label} story"),
        "claims": _claims(state["claims"], f"{label} claims"),
    }


def _agent(value: object, label: str, surface: str | None) -> dict[str, Any]:
    """Whether an agent made the change, and how many lines it wrote in each file, as the hooks counted them."""
    agent = require_object(value, label)
    require_exact_fields(agent, required=frozenset({"produced", "files"}), label=label)
    produced = require_bool(agent["produced"], f"{label} produced")
    files = []
    for index, raw in enumerate(require_list(agent["files"], f"{label} files", maximum=1000)):
        item_label = f"{label} files[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"path", "lines"}), label=item_label)
        files.append(
            {
                "path": validate_relative_path(item["path"], f"{item_label} path"),
                "lines": require_int(item["lines"], f"{item_label} lines", minimum=1, maximum=1_000_000),
            }
        )
    require_unique([file["path"] for file in files], f"{label} files")
    if files and not produced:
        raise DataError(f"{label} counts lines for an agent and says that no agent made the change")
    if produced and surface is None:
        raise DataError(f"{label} says an agent made the change, and the record names no surface")
    return {"produced": produced, "files": files}


# The fields an event has only at one step.
_STEP_FIELDS = {STARTED: "story", CHOICE_MADE: "choice", CHECKPOINT: "state"}


def validate_event(data: object) -> dict[str, Any]:
    """A task event: one step of the work, with the results of that step and what comes next.

    The results are the ones made at this step, for this event's commit. The
    results a task has in all are worked out from its events.
    """
    label = "task event"
    record = require_object(data, label)
    require_exact_fields(
        record,
        required=_COMMON | _POSITION | _SESSION | frozenset({"team_repo_revision", "step", "results", "next_action", "agent"}),
        optional=frozenset(_STEP_FIELDS.values()),
        label=label,
    )
    step = require_choice(record["step"], f"{label} step", STEPS)
    for owner, field in _STEP_FIELDS.items():
        if (field in record) != (step == owner):
            raise DataError(f"{label} must have '{field}' at the step '{owner}', and at no other step")
    session = _session(record, label)
    checked = {
        **_common(record, EVENT, label),
        **_position(record, label),
        **session,
        "team_repo_revision": _revision(record["team_repo_revision"], f"{label} team_repo_revision"),
        "step": step,
        "results": _results(record["results"], f"{label} results"),
        "next_action": _line(record["next_action"], f"{label} next_action", 500),
        "agent": _agent(record["agent"], f"{label} agent", session["surface"]),
    }
    if step == STARTED:
        checked["story"] = _story(record["story"], f"{label} story")
    elif step == CHOICE_MADE:
        checked["choice"] = _choice(record["choice"], f"{label} choice")
    elif step == CHECKPOINT:
        checked["state"] = _state(record["state"], f"{label} state")
    return checked


def overall(statuses: list[str]) -> str:
    """One status for several checks. Anything short of every check passing is not a pass, and no check at all is untested."""
    for status in (FAILED, BLOCKED, UNTESTED):
        if status in statuses:
            return status
    return PASSED if statuses else UNTESTED


def _checks_report(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    checks = []
    for index, raw in enumerate(require_list(record["checks"], f"{label} checks", maximum=50, minimum=1)):
        item_label = f"{label} checks[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"name", "status", "summary", "seconds"}), label=item_label)
        checks.append(
            {
                "name": validate_slug(item["name"], f"{item_label} name", 60),
                "status": require_choice(item["status"], f"{item_label} status", RESULT_STATUSES),
                "summary": _optional(item["summary"], _prose, f"{item_label} summary", 1000),
                "seconds": _optional(item["seconds"], _seconds, f"{item_label} seconds"),
            }
        )
    require_unique([check["name"] for check in checks], f"{label} checks")
    return {"checks": checks}


def _seconds(value: object, label: str) -> int:
    return require_int(value, label, minimum=0, maximum=7 * 24 * 3600)


def _analysis_report(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    status = require_choice(record["status"], f"{label} status", RESULT_STATUSES)
    reason = _optional(record["reason"], _line, f"{label} reason", 500)
    findings = []
    for index, raw in enumerate(require_list(record["findings"], f"{label} findings", maximum=200)):
        item_label = f"{label} findings[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"severity", "title", "detail", "path", "line", "rule"}), label=item_label)
        findings.append(
            {
                "severity": require_choice(item["severity"], f"{item_label} severity", SEVERITIES),
                "title": _line(item["title"], f"{item_label} title", 200),
                "detail": _prose(item["detail"], f"{item_label} detail", 2000),
                "path": _optional(item["path"], validate_relative_path, f"{item_label} path"),
                "line": _optional(item["line"], _line_number, f"{item_label} line"),
                "rule": _optional(item["rule"], formats.validate_rule_id, f"{item_label} rule"),
            }
        )
    # An analysis that did not run to the end cannot read as clean.
    if status == PASSED and findings:
        raise DataError(f"{label} is 'passed' and lists findings. An analysis with findings is 'failed'")
    if status == FAILED and not findings:
        raise DataError(f"{label} is 'failed' and lists no finding")
    if status in {BLOCKED, UNTESTED} and reason is None:
        raise DataError(f"{label} is '{status}' and gives no reason")
    return {"status": status, "reason": reason, "findings": findings}


def _line_number(value: object, label: str) -> int:
    return require_int(value, label, minimum=1, maximum=10_000_000)


def _acceptance_report(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    source = require_object(record["source"], f"{label} source")
    require_exact_fields(source, required=frozenset({"kind", "name"}), label=f"{label} source")
    criteria = _criteria(record["criteria"], f"{label} criteria")
    blocked_reason = _optional(record["blocked_reason"], _line, f"{label} blocked_reason", 500)
    # A story without criteria gives a blocked report, never an empty one that reads as success.
    if not criteria and blocked_reason is None:
        raise DataError(f"{label} has no criterion and no blocked_reason. A story without criteria gives a blocked report")
    if criteria and blocked_reason is not None:
        raise DataError(f"{label} has criteria and a blocked_reason. A blocked report lists no criterion")
    return {
        "source": {
            "kind": require_choice(source["kind"], f"{label} source kind", formats.CRITERIA_KINDS),
            "name": _line(source["name"], f"{label} source name", 200),
        },
        "criteria": criteria,
        "blocked_reason": blocked_reason,
    }


def _qa_pack(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    flags = []
    for index, raw in enumerate(require_list(record["feature_flags"], f"{label} feature_flags", maximum=50)):
        item_label = f"{label} feature_flags[{index}]"
        item = require_object(raw, item_label)
        require_exact_fields(item, required=frozenset({"name", "state"}), label=item_label)
        flags.append({"name": _line(item["name"], f"{item_label} name", 100), "state": _line(item["state"], f"{item_label} state", 100)})
    return {
        "changes": _lines(record["changes"], f"{label} changes", maximum=50, length=300, minimum=1),
        "criteria": _criteria(record["criteria"], f"{label} criteria"),
        "risks": _lines(record["risks"], f"{label} risks", maximum=30, length=300),
        "exploratory_tests": _lines(record["exploratory_tests"], f"{label} exploratory_tests", maximum=30, length=300),
        "build": _optional(record["build"], _line, f"{label} build", 200),
        "feature_flags": flags,
    }


# Each kind of report, with the fields only it has and the function that checks them.
_REPORTS: dict[str, tuple[frozenset[str], Callable[[Mapping[str, Any], str], dict[str, Any]]]] = {
    CHECKS: (frozenset({"checks"}), _checks_report),
    ANALYSIS: (frozenset({"status", "reason", "findings"}), _analysis_report),
    ACCEPTANCE: (frozenset({"source", "criteria", "blocked_reason"}), _acceptance_report),
    QA_PACK: (frozenset({"changes", "criteria", "risks", "exploratory_tests", "build", "feature_flags"}), _qa_pack),
}


def validate_report(data: object) -> dict[str, Any]:
    """A report, bound to the commit it was made for: check results, analysis findings, the acceptance report, or the QA pack."""
    label = "report"
    record = require_object(data, label)
    base = _COMMON | _POSITION | _SESSION | frozenset({"team_repo_revision", "report"})
    if "report" not in record:
        raise DataError(f"{label} is missing fields: report")
    kind = require_choice(record["report"], f"{label} report", REPORT_KINDS)
    fields, check = _REPORTS[kind]
    label = f"{kind} report"
    require_exact_fields(record, required=base | fields, label=label)
    return {
        **_common(record, REPORT, label),
        **_position(record, label),
        **_session(record, label),
        "team_repo_revision": _revision(record["team_repo_revision"], f"{label} team_repo_revision"),
        "report": kind,
        **check(record, label),
    }


def validate_summary(data: object) -> dict[str, Any]:
    """A session summary: what one agent session did. A few paragraphs, never the chat itself."""
    label = "session summary"
    record = require_object(data, label)
    require_exact_fields(record, required=_COMMON | _POSITION | _SESSION | frozenset({"summary", "files_touched", "decisions", "open_questions"}), label=label)
    files = [validate_relative_path(item, f"{label} files_touched[{index}]") for index, item in enumerate(require_list(record["files_touched"], f"{label} files_touched", maximum=300))]
    return {
        **_common(record, SUMMARY, label),
        **_position(record, label),
        **_session(record, label, required=True),
        "summary": _prose(record["summary"], f"{label} summary", 4000),
        "files_touched": require_unique(files, f"{label} files_touched"),
        "decisions": _lines(record["decisions"], f"{label} decisions", maximum=30, length=500),
        "open_questions": _lines(record["open_questions"], f"{label} open_questions", maximum=30, length=500),
    }


def validate_handover(data: object) -> dict[str, Any]:
    """A handover note: why the task was released, and what whoever resumes it should do next."""
    label = "handover note"
    record = require_object(data, label)
    require_exact_fields(record, required=_COMMON | _POSITION | _SESSION | frozenset({"reason", "next_steps"}), label=label)
    return {
        **_common(record, HANDOVER, label),
        **_position(record, label),
        **_session(record, label),
        "reason": _prose(record["reason"], f"{label} reason", 1000),
        "next_steps": _prose(record["next_steps"], f"{label} next_steps", 2000),
    }


_VALIDATORS: dict[str, Callable[[object], dict[str, Any]]] = {
    CLAIM: validate_claim,
    EVENT: validate_event,
    REPORT: validate_report,
    SUMMARY: validate_summary,
    HANDOVER: validate_handover,
}


def validate_record(data: object) -> dict[str, Any]:
    """Check a record of any kind. Raises NewerSchema for one that a newer tool wrote."""
    kind, version = schema_of(require_object(data, "record"))
    if version > VERSIONS[kind]:
        raise NewerSchema(kind, version)
    return _VALIDATORS[kind](data)


def kind_of(record: Mapping[str, Any]) -> str:
    """The kind of a record that was checked before."""
    return schema_of(record)[0]


def text_values(value: object) -> Iterator[str]:
    """Every piece of text in a record, for the check for secrets that runs before a record is published."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from text_values(item)
