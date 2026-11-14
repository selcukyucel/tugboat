"""Decision records: technology and architecture choices, with their reasons.

A record is one Markdown file under `decisions/` in the team repo. It has
four sections: context, options, choice, reason. Its header names the code
paths the choice affects, and may name the one app repo it is about.

Two things happen here. The agent is told which accepted records hold in
its repo, in the instruction file. And when a task makes such a choice, the
agent drafts a record in the app repo, under `.tugboat/capture/decisions/`.
A draft reaches the team repo only as a pull request, which a person merges.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo, formats, frontmatter, teamcopy, teampr
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError

FOLDER = "decisions"
COPY = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}"
DRAFTS = f"{apprepo.LOCAL_FOLDER}/capture/{FOLDER}"
MAX_RECORD_BYTES = 512 * 1024
MAX_LISTED = 30  # records named in the instruction file. The rest are counted.
MAX_PROBLEMS = 3
MAX_DRAFTS = 20
SKIPPED_NAMES = frozenset({"README.md"})
_FILE_NAME = re.compile(r"^(DEC-[0-9]{4})-[a-z0-9][a-z0-9-]{0,79}\.md$")

NEW, SAME, INVALID = "new", "same", "invalid"


class Record(NamedTuple):
    id: str
    title: str
    status: str  # "proposed", "accepted", or "superseded"
    date: str
    repo: str | None  # None when the record holds for the whole project
    paths: tuple[str, ...]
    file: str  # relative to the app repo
    name: str  # the file's name


class Records(NamedTuple):
    records: tuple[Record, ...]  # every record that passed its check
    problems: tuple[str, ...]  # records that were not loaded, and why


class Draft(NamedTuple):
    name: str  # the file's name, such as DEC-0003-use-swift-testing.md
    status: str
    record: Record | None  # None when the draft is not valid
    replaces: bool  # the team repo has this file, with other content
    problem: str | None

    @property
    def team_path(self) -> str:
        return f"{FOLDER}/{self.name}"


def today() -> date:
    return date.today()


def _parse(file: Path, shown: str) -> tuple[Record, str]:
    with file.open("rb") as handle:
        raw = handle.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise DataError(f"is larger than {MAX_RECORD_BYTES // 1024} KB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DataError("is not valid UTF-8") from error
    found = formats.validate_decision(*frontmatter.parse(text))
    if not file.name.startswith(found["id"]):
        raise DataError(f"must have a file name that starts with {found['id']}")
    record = Record(found["id"], found["title"], found["status"], found["date"], found["repo"], tuple(found["paths"]), shown, file.name)
    return record, " ".join(found["text"].split())


def load(repo: Path) -> Records:
    """Read every decision record from the app repo's copy of the team repo."""
    folder = teamcopy.copy_path(repo) / FOLDER
    records: list[Record] = []
    failed: list[str] = []
    if folder.is_dir() and not folder.is_symlink():
        for file in sorted(folder.glob("*.md")):
            if file.name in SKIPPED_NAMES or file.is_symlink() or not file.is_file():
                continue
            try:
                records.append(_parse(file, f"{COPY}/{FOLDER}/{file.name}")[0])
            except (OSError, DataError) as error:
                failed.append(f"the decision record {FOLDER}/{file.name} was not loaded: {error}")
    problems = failed[:MAX_PROBLEMS]
    if len(failed) > MAX_PROBLEMS:
        problems.append(f"{len(failed) - MAX_PROBLEMS} more decision records were not loaded. `tug team check` on the team repo names each one")
    return Records(tuple(records), tuple(problems))


def records_of(records: tuple[Record, ...], github: str | None) -> list[Record]:
    """The records that hold in one app repo: those about it, and those about the whole project."""
    return [record for record in records if record.repo is None or (github and record.repo.lower() == github.lower())]


def next_id(repo: Path, drafts: list[Draft] | None = None) -> str:
    """The first ID after every ID in the team repo's copy and in the drafts."""
    used = [int(record.id[4:]) for record in load(repo).records]
    used += [int(draft.name[4:8]) for draft in drafts or [] if _FILE_NAME.fullmatch(draft.name)]
    return f"DEC-{max(used, default=0) + 1:04}"


def _affects(record: Record) -> str:
    return ", ".join(f"`{path}`" for path in record.paths) if record.paths else "no one place"


def render(loaded: Records, github: str | None) -> str:
    """The part of the instruction file that names the decisions in force, and says when to draft one."""
    lines = ["## Decision records", ""]
    mine = records_of(loaded.records, github)
    accepted = [record for record in mine if record.status == "accepted"]
    if accepted:
        count = "1 accepted decision record holds" if len(accepted) == 1 else f"{len(accepted)} accepted decision records hold"
        lines += [f"{count} in this repo. Each is a technology or architecture choice the team made, with its reason.", ""]
        for record in accepted[:MAX_LISTED]:
            scope = "" if record.repo else " Holds for the whole project."
            lines.append(f"- `{record.id}`: {record.title.rstrip('.')}. Affects: {_affects(record)}.{scope} File: `{record.file}`")
        if len(accepted) > MAX_LISTED:
            lines.append(f"- and {len(accepted) - MAX_LISTED} more, in `{COPY}/{FOLDER}/`.")
        lines += [
            "",
            "Before you work on code that a record affects, read the record. Follow its choice.",
            "If the task needs another choice, stop and tell the developer which record stands in the way. Do not decide against a record yourself.",
        ]
    else:
        lines.append("No accepted decision record holds in this repo yet.")
    other = len(mine) - len(accepted)
    if other:
        lines.append(f"{other} more record{' is' if other == 1 else 's are'} proposed or superseded, and {'is' if other == 1 else 'are'} not in force.")
    lines += [
        "",
        "When a task makes a technology or architecture choice, such as a new library, a new pattern, a data format, or a service, "
        "tell the developer and draft a decision record with the skill `tug-decision-record`. "
        "A record reaches the team repo only as a pull request, which a person merges.",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ drafts


def drafts_folder(repo: Path) -> Path:
    return apprepo.local_folder(repo) / "capture" / FOLDER


def scan(repo: Path, github: str | None) -> list[Draft]:
    """Every draft record in the app repo, with whether it can be proposed."""
    folder = drafts_folder(repo)
    drafts: list[Draft] = []
    if not folder.is_dir() or folder.is_symlink():
        return drafts
    approved = teamcopy.copy_path(repo) / FOLDER
    taken = {record.id: record.name for record in load(repo).records}
    for file in sorted(folder.iterdir(), key=lambda item: item.name):
        if file.name.startswith("."):
            continue
        if len(drafts) >= MAX_DRAFTS:
            raise TugError(f"{DRAFTS}/ holds more than {MAX_DRAFTS} drafts. Propose or remove some first.")
        problem = None
        record = None
        words = ""
        if file.is_symlink() or not file.is_file():
            problem = "is not a plain file"
        elif not _FILE_NAME.fullmatch(file.name):
            problem = "a record's file name is its ID, a hyphen, and a short name in small letters, such as DEC-0003-use-swift-testing.md"
        else:
            try:
                record, words = _parse(file, f"{DRAFTS}/{file.name}")
            except OSError as error:
                problem = f"cannot be read: {error.strerror or error}"
            except DataError as error:
                problem = str(error)
        if record is not None:
            if record.repo and (github is None or record.repo.lower() != github.lower()):
                problem = f"is about the repo {record.repo}. A record drafted here is about {github or 'this repo'}, or names no repo and holds for the whole project."
            elif record.date > today().isoformat():
                problem = f"has the date {record.date}, which is after today"
            elif taken.get(record.id, file.name) != file.name:
                problem = f"uses {record.id}, which {FOLDER}/{taken[record.id]} in the team repo already uses"
        if problem is not None or record is None:
            drafts.append(Draft(file.name, INVALID, None, False, problem))
            continue
        existing = approved / file.name
        status, replaces = NEW, False
        if existing.is_file() and not existing.is_symlink():
            try:
                before, before_words = _parse(existing, "")
                same = before_words == words and before._replace(file="") == record._replace(file="")
            except (OSError, DataError):
                same = False
            status, replaces = (SAME, False) if same else (NEW, True)
        drafts.append(Draft(file.name, status, record, replaces, None))
    return drafts


def _clean_name(given: str) -> str:
    return given.replace("\\", "/").rsplit("/", 1)[-1]


def propose(repo: Path, github: str | None, address: TeamRepoAddress, names: list[str]) -> tuple[teampr.Proposal, list[Draft]]:
    """Open one pull request in the team repo with the named draft records. The drafts are then removed."""
    by_name = {draft.name: draft for draft in scan(repo, github)}
    picked: list[Draft] = []
    for given in names:
        name = _clean_name(given)
        draft = by_name.get(name)
        if draft is None:
            raise TugError(f"there is no draft named {name} in {DRAFTS}/. `tug decision status` lists the drafts. Nothing was proposed.")
        if draft.status == INVALID:
            raise TugError(f"{name} is not valid, so it cannot be proposed: {draft.problem}. Nothing was proposed.")
        if draft.status == SAME:
            raise TugError(f"the team repo already has {name} with this content. Nothing was proposed.")
        if draft not in picked:
            picked.append(draft)
    records: list[Any] = [draft.record for draft in picked]
    if len(picked) == 1:
        title = f"{'Change' if picked[0].replaces else 'Add'} decision record {records[0].id}: {records[0].title}"[:150]
    else:
        title = f"Add {len(picked)} decision records: {', '.join(record.id for record in records)}"[:150]
    lines = ["A technology or architecture choice made during a task, drafted by an agent and reviewed by the developer.", ""]
    for draft, record in zip(picked, records):
        about = f"the repo {record.repo}" if record.repo else "the whole project"
        lines.append(f"- {'Changes' if draft.replaces else 'Adds'} `{draft.team_path}`: {record.title}. Status: {record.status}. About {about}. Affects: {_affects(record)}.")
    lines += ["", "Merging this pull request is the team's agreement with the choice."]
    changes: dict[str, bytes | None] = {draft.team_path: (drafts_folder(repo) / draft.name).read_bytes() for draft in picked}
    proposal = teampr.propose(address, title=title, body="\n".join(lines), changes=changes, name=f"decision-{records[0].id.lower()}")
    for draft in picked:
        file = drafts_folder(repo) / draft.name
        if file.is_file():
            file.unlink()
    folder = drafts_folder(repo)
    while folder != apprepo.local_folder(repo) and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent
    return proposal, picked


def render_status(repo: Path, drafts: list[Draft]) -> str:
    lines = []
    if drafts:
        lines.append(f"Draft decision records in {DRAFTS}/:")
        for draft in drafts:
            if draft.record is None:
                lines.append(f"- {draft.name}: not valid. {draft.problem}")
            elif draft.status == SAME:
                lines.append(f"- {draft.name}: the team repo already has this record with the same content. Remove the draft.")
            else:
                what = "ready to propose. It changes a record that is in the team repo" if draft.replaces else "ready to propose"
                lines.append(f"- {draft.name}: {what}. \"{draft.record.title}\" (status {draft.record.status}; affects {', '.join(draft.record.paths) or 'no one place'})")
    else:
        lines.append(f"No draft decision record. A draft is a file under {DRAFTS}/, such as {DRAFTS}/{next_id(repo)}-short-name.md.")
    lines.append(f"The next free ID is {next_id(repo, drafts)}.")
    return "\n".join(lines)
