"""The knowledge base: notes about the code, found by the paths they cover.

A note is one Markdown file under `knowledge/` in the team repo. Its header
names an owner, the app repo and the code paths it covers, and the day
someone last checked it against the code.

The agent does not read the whole base. The instruction file carries an
index: for each note of this repo, its paths, its title, and its file. The
command `tug notes <path>...` reads the same index and names the notes that
cover the paths of a task. The agent reads those notes and no others.

A note that fails its check is not in the index. That includes a note that
holds a secret pattern or a raw chat transcript.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from tugboat import apprepo, formats, frontmatter, globmatch, teamcopy
from tugboat.errors import DataError

FOLDER = "knowledge"
COPY = f"{apprepo.LOCAL_FOLDER}/{teamcopy.COPY_FOLDER}"
MAX_NOTE_BYTES = 512 * 1024
MAX_LISTED = 30  # notes named in the instruction file. The rest are counted, and `tug notes` finds them.
MAX_PROBLEMS = 3  # notes that were not loaded, named one by one. The rest are counted.
SKIPPED_NAMES = frozenset({"README.md"})
CAPTURE_ADVICE = (
    "Do not stop the work to write a note. When the developer asks to capture what was learned, use the skill `tug-capture-notes`: "
    "it drafts notes for what you had to find out, and the developer accepts, edits, or drops each one."
)


class Note(NamedTuple):
    file: str  # the note's file, relative to the app repo
    title: str
    owner: str
    repo: str  # the app repo the note is about, as owner/name
    paths: tuple[str, ...]
    last_verified: str


class Knowledge(NamedTuple):
    notes: tuple[Note, ...]  # every note that passed its check, for any repo
    problems: tuple[str, ...]  # notes that were not loaded, and why


class Match(NamedTuple):
    note: Note
    given: tuple[str, ...]  # the asked paths this note covers


def _read(file: Path, name: str) -> Note:
    with file.open("rb") as handle:
        raw = handle.read(MAX_NOTE_BYTES + 1)
    if len(raw) > MAX_NOTE_BYTES:
        raise DataError(f"is larger than {MAX_NOTE_BYTES // 1024} KB")
    note = formats.validate_note(*frontmatter.parse(raw.decode("utf-8")))
    return Note(f"{COPY}/{name}", note["title"], note["owner"], note["repo"], tuple(note["paths"]), note["last_verified"])


def load(repo: Path) -> Knowledge:
    """Read every note from the app repo's copy of the team repo."""
    copy = teamcopy.copy_path(repo)
    folder = copy / FOLDER
    notes: list[Note] = []
    failed: list[str] = []
    if folder.is_dir() and not folder.is_symlink():
        for current, folders, files in os.walk(folder, followlinks=False):
            folders.sort()
            for file_name in sorted(files):
                file = Path(current) / file_name
                if not file_name.endswith(".md") or file_name in SKIPPED_NAMES or file.is_symlink():
                    continue
                name = file.relative_to(copy).as_posix()
                try:
                    notes.append(_read(file, name))
                except (OSError, UnicodeDecodeError, DataError) as error:
                    reason = "is not valid UTF-8" if isinstance(error, UnicodeDecodeError) else str(error)
                    failed.append(f"the note {name} was not loaded: {reason}")
    problems = failed[:MAX_PROBLEMS]
    if len(failed) > MAX_PROBLEMS:
        problems.append(f"{len(failed) - MAX_PROBLEMS} more notes were not loaded. `tug team check` on the team repo names each one")
    return Knowledge(tuple(notes), tuple(problems))


def notes_of(notes: tuple[Note, ...], github: str | None) -> list[Note]:
    """The notes about one app repo, named as owner/name."""
    return [note for note in notes if github and note.repo.lower() == github.lower()]


def _covers(note: Note, path: str, is_folder: bool) -> bool:
    test = globmatch.reaches if is_folder else globmatch.matches
    return any(test(pattern, path) for pattern in note.paths)


def matching(notes: list[Note], paths: list[tuple[str, bool]]) -> list[Match]:
    """The notes that cover at least one of the paths, each note once, in file order.

    Each path comes with a flag: True when it is a folder. A file is covered
    by a note whose pattern matches it. A folder is also covered by a note
    about something inside it, because a task on a folder touches its files.
    """
    found = []
    for note in notes:
        given = tuple(path for path, is_folder in paths if _covers(note, path, is_folder))
        if given:
            found.append(Match(note, given))
    return found


def _paths_words(note: Note) -> str:
    return ", ".join(f"`{path}`" for path in note.paths)


def note_line(match: Match) -> str:
    note = match.note
    return f"- {note.title}: {note.file} (covers {', '.join(match.given)}; owner {note.owner}; last verified {note.last_verified})"


def render(knowledge: Knowledge, github: str | None) -> str:
    """The part of the instruction file that says which notes exist for this repo, and for which paths."""
    lines = ["## Knowledge notes", ""]
    notes = notes_of(knowledge.notes, github)
    if not notes:
        lines += [
            "The team repo has no knowledge note about this repo, so there is none to read. "
            "A note for another repo does not apply here.",
            "",
            CAPTURE_ADVICE,
        ]
        return "\n".join(lines) + "\n"
    count = "1 note" if len(notes) == 1 else f"{len(notes)} notes"
    lines += [
        f"The team repo has {count} about this repo. This is the index: the code paths a note covers, its title, and its file.",
        "",
    ]
    for note in notes[:MAX_LISTED]:
        lines.append(f"- {_paths_words(note)}: {note.title.rstrip('.')}. File: `{note.file}` (last verified {note.last_verified})")
    if len(notes) > MAX_LISTED:
        lines.append(f"- and {len(notes) - MAX_LISTED} more, not listed here. `tug notes <path>` finds them too.")
    lines += [
        "",
        "Before you read or change code for a task, run `tug notes <path>...` with the files or folders of the task. "
        "It names the notes that cover them. Read those notes. Do not read the other notes.",
        "A note says what was true on its last-verified day. When a note and the code disagree, the code is right: "
        "tell the developer that the note is out of date, and name its owner.",
        "",
        CAPTURE_ADVICE,
    ]
    return "\n".join(lines) + "\n"
