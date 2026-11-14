"""Knowledge capture: notes the agent proposes for what it had to discover.

The agent writes each proposed note as a draft in the app repo, under
`.tugboat/capture/knowledge/`. Git does not see that folder. The developer
accepts, edits, or drops each draft. The accepted ones go to the team repo
in one pull request, and a person merges it.

A dropped draft is remembered by its content: the title, the paths, and the
text. The same content is not offered again. A draft that says something
else is a new proposal.

Nothing here runs by itself. The developer asks for a capture, so coding
steps are not interrupted.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import apprepo, formats, frontmatter, teamcopy, teampr
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError
from tugboat.strict_json import pretty_json, read_json_file
from tugboat.validate import require_text

FOLDER = "capture"
STATE_FILE = "capture.json"
STATE_SCHEMA = "tug.capture/1"
NOTES = "knowledge"
DRAFTS = f"{apprepo.LOCAL_FOLDER}/{FOLDER}/{NOTES}"
MAX_DRAFT_BYTES = 512 * 1024
MAX_DRAFTS = 50
MAX_DROPPED = 500  # dropped proposals remembered for one repo. The oldest go first.
MAX_DROPPED_SHOWN = 20
_NAME_PART = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

NEW, ACCEPTED, CHANGED, DROPPED, SAME, INVALID = "new", "accepted", "changed", "dropped", "same", "invalid"


class Draft(NamedTuple):
    name: str  # the note's path under knowledge/, such as auth/token-refresh.md
    status: str
    title: str | None  # None when the draft is not valid
    paths: tuple[str, ...]
    fingerprint: str | None
    replaces: bool  # the team repo has a note at this path, with other content
    problem: str | None  # why the draft is not valid

    @property
    def team_path(self) -> str:
        return f"{NOTES}/{self.name}"

    @property
    def file(self) -> str:
        return f"{DRAFTS}/{self.name}"


class Outcome(NamedTuple):
    proposal: teampr.Proposal
    notes: tuple[Draft, ...]  # what went into the pull request
    left: tuple[Draft, ...]  # drafts that were not accepted, and stay


def today() -> date:
    return date.today()


def drafts_folder(repo: Path) -> Path:
    return apprepo.local_folder(repo) / FOLDER / NOTES


def fingerprint(note: dict[str, Any]) -> str:
    """What a note says: its title, its paths, and its text. The owner, the date, and line breaks do not count."""
    parts = [" ".join(note["title"].split()), "\n".join(sorted(note["paths"])), " ".join(note["text"].split())]
    return hashlib.sha256("\n\0".join(parts).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- state


def _read_state(repo: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {"schema": STATE_SCHEMA, "accepted": {}, "dropped": []}
    file = apprepo.local_folder(repo) / STATE_FILE
    if not file.is_file() or file.is_symlink():
        return empty
    try:
        data = read_json_file(file)
    except DataError:
        return empty
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        return empty
    accepted = data.get("accepted")
    dropped = data.get("dropped")
    if isinstance(accepted, dict):
        empty["accepted"] = {name: mark for name, mark in accepted.items() if isinstance(name, str) and isinstance(mark, str)}
    if isinstance(dropped, list):
        empty["dropped"] = [
            entry
            for entry in dropped
            if isinstance(entry, dict) and isinstance(entry.get("fingerprint"), str) and isinstance(entry.get("title"), str)
        ]
    return empty


def _write_state(repo: Path, state: dict[str, Any]) -> None:
    folder = apprepo.local_folder(repo)
    folder.mkdir(exist_ok=True)
    state["dropped"] = state["dropped"][-MAX_DROPPED:]
    (folder / STATE_FILE).write_text(pretty_json(state), encoding="utf-8")


def dropped_before(repo: Path) -> list[dict[str, Any]]:
    """The proposals dropped in this repo on this machine, newest last."""
    return _read_state(repo)["dropped"]


# ------------------------------------------------------------------ drafts


def _name_problem(name: str) -> str | None:
    parts = name.split("/")
    if not name.endswith(".md") or parts[-1] == "README.md":
        return "a note is a .md file, and not a README.md"
    if len(parts) > 4:
        return "a note can be at most three folders deep"
    if not all(_NAME_PART.fullmatch(part) for part in parts):
        return "a note's folders and file name use small letters, digits, dots, and hyphens, such as auth/token-refresh.md"
    return None


def _read(file: Path, github: str | None) -> dict[str, Any]:
    with file.open("rb") as handle:
        raw = handle.read(MAX_DRAFT_BYTES + 1)
    if len(raw) > MAX_DRAFT_BYTES:
        raise DataError(f"is larger than {MAX_DRAFT_BYTES // 1024} KB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DataError("is not valid UTF-8") from error
    note = formats.validate_note(*frontmatter.parse(text))
    if github is None or note["repo"].lower() != github.lower():
        raise DataError(f"is about the repo {note['repo']}. A note captured here is about {github or 'this repo, which has no GitHub remote'}.")
    if note["last_verified"] > today().isoformat():
        raise DataError(f"has last_verified {note['last_verified']}, which is after today")
    return note


def scan(repo: Path, github: str | None) -> list[Draft]:
    """Every draft in the repo's capture folder, with where it stands."""
    folder = drafts_folder(repo)
    state = _read_state(repo)
    dropped = {entry["fingerprint"] for entry in state["dropped"]}
    copy = teamcopy.copy_path(repo)
    drafts: list[Draft] = []
    if not folder.is_dir() or folder.is_symlink():
        return drafts
    for current, folders, files in os.walk(folder, followlinks=False):
        folders.sort()
        for file_name in sorted(files):
            file = Path(current) / file_name
            name = file.relative_to(folder).as_posix()
            if file_name.startswith("."):
                continue
            if len(drafts) >= MAX_DRAFTS:
                raise TugError(f"{DRAFTS}/ holds more than {MAX_DRAFTS} drafts. Drop or propose some first.")
            problem = "is a symbolic link" if file.is_symlink() else _name_problem(name)
            note = None
            if problem is None:
                try:
                    note = _read(file, github)
                except OSError as error:
                    problem = f"cannot be read: {error.strerror or error}"
                except DataError as error:
                    problem = str(error)
            if note is None:
                drafts.append(Draft(name, INVALID, None, (), None, False, problem))
                continue
            mark = fingerprint(note)
            approved = copy / NOTES / name
            replaces = False
            status = NEW
            if approved.is_file() and not approved.is_symlink():
                try:
                    same = fingerprint(formats.validate_note(*frontmatter.parse(approved.read_text(encoding="utf-8")))) == mark
                except (OSError, UnicodeDecodeError, DataError):
                    same = False
                replaces = not same
                status = SAME if same else NEW
            if status == NEW:
                was = state["accepted"].get(name)
                if was == mark:
                    status = ACCEPTED
                elif was is not None:
                    status = CHANGED
                elif mark in dropped:
                    status = DROPPED
            drafts.append(Draft(name, status, note["title"], tuple(note["paths"]), mark, replaces, None))
    return drafts


def _clean_name(given: str) -> str:
    name = given.replace("\\", "/")
    for prefix in (f"{DRAFTS}/", f"./{DRAFTS}/", f"{NOTES}/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name


def _pick(drafts: list[Draft], given: list[str]) -> list[Draft]:
    by_name = {draft.name: draft for draft in drafts}
    picked = []
    for item in given:
        name = _clean_name(item)
        if name not in by_name:
            raise TugError(f"there is no draft named {name} in {DRAFTS}/. `tug capture status` lists the drafts.")
        if by_name[name] not in picked:
            picked.append(by_name[name])
    return picked


def _prune(folder: Path, stop: Path) -> None:
    while folder != stop and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _remove(repo: Path, draft: Draft) -> None:
    file = drafts_folder(repo) / draft.name
    if file.is_file() or file.is_symlink():
        file.unlink()
    _prune(file.parent, apprepo.local_folder(repo))


def accept(repo: Path, github: str | None, names: list[str]) -> list[Draft]:
    """The developer accepted these drafts as they are now. A later edit must be accepted again."""
    picked = _pick(scan(repo, github), names)
    for draft in picked:
        if draft.status == INVALID:
            raise TugError(f"{draft.name} is not valid, so it cannot be accepted: {draft.problem}. Nothing was accepted.")
        if draft.status == SAME:
            raise TugError(f"the team repo already has {draft.name} with this content, so there is nothing to accept. Nothing was accepted.")
    state = _read_state(repo)
    for draft in picked:
        state["accepted"][draft.name] = draft.fingerprint
        # The developer took it after all, so it is no longer a dropped proposal.
        state["dropped"] = [entry for entry in state["dropped"] if entry["fingerprint"] != draft.fingerprint]
    _write_state(repo, state)
    return picked


def drop(repo: Path, github: str | None, names: list[str]) -> list[Draft]:
    """The developer does not want these. The drafts are removed and their content is remembered."""
    picked = _pick(scan(repo, github), names)
    state = _read_state(repo)
    known = {entry["fingerprint"] for entry in state["dropped"]}
    for draft in picked:
        state["accepted"].pop(draft.name, None)
        if draft.fingerprint and draft.fingerprint not in known:
            state["dropped"].append(
                {"fingerprint": draft.fingerprint, "name": draft.name, "title": draft.title, "paths": list(draft.paths), "date": today().isoformat()}
            )
            known.add(draft.fingerprint)
        _remove(repo, draft)
    _write_state(repo, state)
    return picked


def propose(repo: Path, github: str | None, address: TeamRepoAddress, *, task: str | None = None) -> Outcome:
    """Open one pull request in the team repo with every accepted draft."""
    drafts = scan(repo, github)
    notes = [draft for draft in drafts if draft.status == ACCEPTED]
    changed = [draft.name for draft in drafts if draft.status == CHANGED]
    if not notes:
        hint = f" {', '.join(changed)} changed after it was accepted: accept it again." if changed else ""
        raise TugError(f"no draft is accepted, so there is nothing to propose.{hint} `tug capture status` lists the drafts.")
    label = None
    if task is not None:
        label = require_text(task, "the task", 100)
        if "\n" in label:
            raise TugError("the task must be one line")
    about = github or "this repo"
    if len(notes) == 1:
        title = f"{'Update' if notes[0].replaces else 'Add'} a knowledge note: {notes[0].title}"[:150]
    else:
        title = f"Add {len(notes)} knowledge notes about {about}"
    lines = [f"Knowledge notes about {about}, proposed by an agent for what it had to find out, and accepted by the developer."]
    if label:
        lines.append(f"Task: {label}")
    lines.append("")
    for draft in notes:
        lines.append(f"- {'Changes' if draft.replaces else 'Adds'} `{draft.team_path}`: {draft.title}. Covers {', '.join(f'`{path}`' for path in draft.paths)}.")
    lines += ["", "Check each note against the code before merging."]
    changes: dict[str, bytes | None] = {draft.team_path: (drafts_folder(repo) / draft.name).read_bytes() for draft in notes}
    proposal = teampr.propose(address, title=title, body="\n".join(lines), changes=changes, name=f"notes-{label}" if label else "notes")
    state = _read_state(repo)
    for draft in notes:
        state["accepted"].pop(draft.name, None)
        _remove(repo, draft)
    _write_state(repo, state)
    return Outcome(proposal, tuple(notes), tuple(draft for draft in drafts if draft.status != ACCEPTED))


# ------------------------------------------------------------------ report

_STATUS_WORDS = {
    NEW: "new. Not accepted and not dropped yet",
    ACCEPTED: "accepted. It goes into the next pull request",
    CHANGED: "changed after it was accepted. Accept it again, or drop it",
    DROPPED: "dropped before with the same content. Do not offer it again. Remove the draft",
    SAME: "the team repo already has this note with the same content. Remove the draft",
}


def draft_line(draft: Draft) -> str:
    if draft.status == INVALID:
        return f"- {draft.name}: not valid. {draft.problem}"
    words = _STATUS_WORDS[draft.status]
    replaces = " It changes a note that is in the team repo." if draft.replaces else ""
    return f"- {draft.name}: {words}.{replaces} \"{draft.title}\" (covers {', '.join(draft.paths)})"


def render_status(repo: Path, drafts: list[Draft]) -> str:
    lines = []
    if drafts:
        lines.append(f"Drafts in {DRAFTS}/:")
        lines.extend(draft_line(draft) for draft in drafts)
    else:
        lines.append(f"No draft note. A draft is a note file under {DRAFTS}/, such as {DRAFTS}/auth/token-refresh.md.")
    dropped = dropped_before(repo)
    if dropped:
        lines += ["", "Dropped before in this repo. Do not write these again:"]
        for entry in reversed(dropped[-MAX_DROPPED_SHOWN:]):
            listed = entry.get("paths")
            paths = listed if isinstance(listed, list) else []
            lines.append(f"- \"{entry['title']}\" (covers {', '.join(str(path) for path in paths) or 'no path recorded'})")
        if len(dropped) > MAX_DROPPED_SHOWN:
            lines.append(f"- and {len(dropped) - MAX_DROPPED_SHOWN} older ones.")
    return "\n".join(lines)
