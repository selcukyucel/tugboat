"""The current state of a task, worked out from its records.

No file holds the state of a task. It is computed, each time it is needed,
from the records of the story for one app repo. Records are only ever added,
so two machines can never overwrite each other's view of a task.

Four rules:

- A record older than six months is left out on every read, by its own
  time and not by when it was committed. Nothing in a state, a result, or
  a history comes from such a record.
- The newest checkpoint is the base. A checkpoint holds the task's full
  state, so a task that stays open past six months loses nothing when its
  early records expire. Events after the checkpoint are applied on top.
  A checkpoint never hides a result: of each check the newest result in
  the window counts, also one the checkpoint's machine had not seen.
- A result counts only for what it was made for. When the commit changed,
  when the team's rules changed, or when an acceptance criterion changed,
  the result is marked stale, with the reason. It stays in the history.
- What cannot be worked out is not assumed. A comparison that cannot be
  made marks the result stale.

A stale result costs the team one more run of a check or of an agent. So a
change that cannot have changed what a result says leaves it current: a
new commit with the same files (a squash, a reworded message), a rule of
the other platform, a ticked check box in front of a criterion.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import recordstore, records
from tugboat.address import TeamRepoAddress
from tugboat.errors import TugError
from tugboat.gitrun import GitError, git
from tugboat.recordstore import Stored, Unreadable

WINDOW_MONTHS = 6
RULES_PATH = "policies"  # where the team repo keeps its rules, in one folder for each scope
COMPANY_SCOPE = "company"

COMMIT_CHANGED = "the commit changed"
RULES_CHANGED = "the team's rules changed"
RULES_UNKNOWN = "the team repo changed, and whether its rules changed could not be worked out"
CRITERION_CHANGED = "an acceptance criterion changed"
CRITERIA_UNKNOWN = "its report is no longer among the records, so its criteria could not be compared"

# Answers "did the rules change between these two revisions of the team repo?". None when that cannot be told.
RulesChanged = Callable[[str, str], bool | None]
# Answers "do these two commits of the app repo hold the same files?". None when that cannot be told.
SameContent = Callable[[str, str], bool | None]


class Result(NamedTuple):
    """What one check said, and what it was made for."""

    name: str
    status: str  # passed, failed, blocked, or untested
    summary: str | None
    report: str | None  # the ID of the report record with the detail
    kind: str | None  # the kind of that report. None when the result names no report.
    commit: str
    team_repo_revision: str
    at: str
    stale: tuple[str, ...] = ()  # why it no longer counts. Empty for a result that is current.

    @property
    def current(self) -> bool:
        return not self.stale


class Claim(NamedTuple):
    author: str
    machine: str
    at: str


class TaskState(NamedTuple):
    task: dict[str, Any]
    repo: str
    platform: str
    story: dict[str, Any] | None  # title, link, epic, milestone. None when no record in the window says.
    step: str | None  # the step the task reached. None when it was only claimed.
    branch: str | None
    commit: str | None  # where the newest event stood
    team_repo_revision: str | None
    results: tuple[Result, ...]  # the newest result of each check, by name
    pending_choice: str | None  # what the developer was asked at the last checkpoint and has not answered
    next_action: str | None
    claims: tuple[Claim, ...]  # who holds the task now. More than one is a warning, never a lock.
    handover: dict[str, Any] | None  # the note that released the task, when nothing happened after it
    last_at: str  # the time of the newest record
    history: tuple[Stored, ...]  # every record of the task in the window, oldest first


class Loaded(NamedTuple):
    state: TaskState | None  # None when the window holds no record of the task for that app repo
    expired: int  # records of the story that are older than six months and were left out
    newer: tuple[Unreadable, ...]  # records that a newer tool wrote. They were skipped.
    unreadable: tuple[Unreadable, ...]  # files that could not be read as records. They were left out.
    cut_short: bool = False


def _moment(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def cutoff(now: datetime | None = None) -> datetime:
    """The moment six months before now. A record from before it has expired."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    year, month = moment.year, moment.month - WINDOW_MONTHS
    if month < 1:
        year, month = year - 1, month + 12
    # The same day of the month, or the last day of a shorter month.
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def within_window(stored: Iterable[Stored], now: datetime | None = None) -> tuple[list[Stored], int]:
    """The records that have not expired, oldest first, and how many were left out. Used on every read."""
    limit = cutoff(now)
    kept, expired = [], 0
    for item in stored:
        if _moment(item.record["at"]) < limit:
            expired += 1
        else:
            kept.append(item)
    kept.sort(key=lambda item: (_moment(item.record["at"]), item.record["id"]))
    return kept, expired


def rules_changed_in(clone: Path, platform: str | None = None) -> RulesChanged:
    """A check against one clone of the team repo: did a rule change between two revisions?

    With a platform, only the rules an app repo of that platform is judged
    by count: the company's and the platform's own. A changed Android rule
    leaves an iOS analysis as it is. Without one, every rule counts.
    """
    folders = [f"{RULES_PATH}/{COMPANY_SCOPE}", f"{RULES_PATH}/{platform}"] if platform else [RULES_PATH]

    def changed(old: str, new: str) -> bool | None:
        if old == new:
            return False
        try:
            for revision in (old, new):
                git("cat-file", "-e", f"{revision}^{{commit}}", cwd=clone)
            return bool(git("diff", "--name-only", old, new, "--", *folders, cwd=clone))
        except GitError:
            return None  # one of the revisions is not in this clone

    return changed


def same_content_in(app_repo: Path) -> SameContent:
    """A check against one app repo: do two commits hold the same files?

    A squash, an amended message, and an empty commit give a new commit
    with the files of the old one. What was built, tested, and reviewed
    is the same, so a result made for the one holds for the other.
    """
    trees: dict[str, str | None] = {}

    def tree(commit: str) -> str | None:
        if commit not in trees:
            try:
                trees[commit] = git("rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}", cwd=app_repo)
            except GitError:
                trees[commit] = None  # the commit is not in this repo, for one made on another machine and never pushed
        return trees[commit]

    def same(old: str, new: str) -> bool | None:
        if old == new:
            return True
        first, second = tree(old), tree(new)
        return None if first is None or second is None else first == second

    return same


_LIST_MARK = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_CHECK_BOX = re.compile(r"^\[[ xX]\]\s+")


def _criterion_text(text: str) -> str:
    """One criterion as it is compared. What cannot change its meaning is taken off: the list mark, the check box and
    whether it is ticked, capital letters, spacing, and the full stop at the end."""
    line = " ".join(text.split())
    line = _CHECK_BOX.sub("", _LIST_MARK.sub("", line))
    return line.rstrip(" .;,").casefold()


def _criteria_texts(texts: Iterable[str]) -> list[str]:
    """Criteria as they are compared: each as one line, in an order of their own, so a reordering is no change."""
    return sorted(_criterion_text(text) for text in texts)


def _why_stale(
    result: Result,
    reports: Mapping[str, dict[str, Any]],
    commit: str | None,
    team_repo_revision: str | None,
    rules_changed: RulesChanged | None,
    criteria: Sequence[str] | None,
    same_content: SameContent | None,
) -> tuple[str, ...]:
    reasons = []
    # Another commit with the same files is the same candidate. When that cannot be told, the commit counts as changed.
    if commit is not None and result.commit != commit and not (same_content is not None and same_content(result.commit, commit)):
        reasons.append(COMMIT_CHANGED)
    # A change of the rules touches what was judged by the rules: the analysis.
    if result.kind == records.ANALYSIS and team_repo_revision is not None and result.team_repo_revision != team_repo_revision:
        changed = rules_changed(result.team_repo_revision, team_repo_revision) if rules_changed is not None else None
        if changed is None:
            reasons.append(RULES_UNKNOWN)
        elif changed:
            reasons.append(RULES_CHANGED)
    # A change of a criterion touches what was judged by the criteria: the acceptance report and the QA pack.
    if result.kind in {records.ACCEPTANCE, records.QA_PACK} and criteria is not None:
        report = reports.get(result.report or "")
        if report is None:
            reasons.append(CRITERIA_UNKNOWN)
        elif _criteria_texts(item["text"] for item in report["criteria"]) != _criteria_texts(criteria):
            reasons.append(CRITERION_CHANGED)
    return tuple(reasons)


def _same_task(record: Mapping[str, Any], task: Mapping[str, Any], repo: str) -> bool:
    return record["task"] == {"tracker": task["tracker"], "key": task["key"]} and record["repo"].lower() == repo.lower()


def compute(
    stored: Iterable[Stored],
    task: Mapping[str, Any],
    repo: str,
    *,
    commit: str | None = None,
    team_repo_revision: str | None = None,
    rules_changed: RulesChanged | None = None,
    criteria: Sequence[str] | None = None,
    same_content: SameContent | None = None,
) -> TaskState | None:
    """The state of one story in one app repo, from records that are all inside the window.

    `commit` is the commit the app repo is on now, `team_repo_revision` the
    team repo's approved revision now, and `criteria` the story's
    acceptance criteria as the tracker has them now. Each one that is given
    is compared with what the results were made for. One that is left out
    is not judged. Without `same_content` every other commit is a change;
    without `rules_changed` every other team repo revision is one.
    """
    mine = sorted((item for item in stored if _same_task(item.record, task, repo)), key=lambda item: (_moment(item.record["at"]), item.record["id"]))
    if not mine:
        return None
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in records.KINDS}
    for item in mine:
        by_kind[records.kind_of(item.record)].append(item.record)
    reports = {record["id"]: record for record in by_kind[records.REPORT]}
    events = by_kind[records.EVENT]

    story: dict[str, Any] | None = None
    step: str | None = None
    pending: str | None = None
    results: dict[str, Result] = {}
    seeds: list[Claim] = []

    def keep(result: Result) -> None:
        """Of each check the newest result counts. At the same time the later record wins: a checkpoint's saved copy knows as much as the original, or more."""
        held = results.get(result.name)
        if held is None or _moment(result.at) >= _moment(held.at):
            results[result.name] = result

    # The newest checkpoint is the base: the step, the pending choice, the story, and the claims come from it.
    base = next((index for index in range(len(events) - 1, -1, -1) if events[index]["step"] == records.CHECKPOINT), None)
    if base is not None:
        saved = events[base]["state"]
        story, step, pending = saved["story"], saved["step"], saved["pending_choice"]
        seeds = [Claim(**item) for item in saved["claims"]]
    for index, event in enumerate(events):
        if base is None or index > base:
            if event["step"] == records.STARTED:
                story = event["story"]
            step, pending = event["step"], None  # any step after a checkpoint answers what it asked
        elif story is None and event["step"] == records.STARTED:
            story = event["story"]  # the checkpoint's machine had not seen the first event
        # Results are taken from every event in the window, also from before the base. A checkpoint saved on a machine
        # that had not yet seen another machine's records must not hide what those records found.
        if event["step"] == records.CHECKPOINT:
            for item in event["state"]["results"]:
                keep(Result(**item))
        for item in event["results"]:
            report = reports.get(item["report"] or "")
            keep(Result(
                item["name"], item["status"], item["summary"], item["report"], report["report"] if report else None,
                event["commit"], event["team_repo_revision"], event["at"],
            ))  # fmt: skip
    newest_event = events[-1] if events else None

    # Who holds the task: every claim, minus those their own author released with a handover note afterwards.
    moves: list[tuple[datetime, int, str, Claim | None]] = [(_moment(claim.at), 0, claim.author, claim) for claim in seeds]
    moves += [(_moment(record["at"]), 0, record["author"], Claim(record["author"], record["machine"], record["at"])) for record in by_kind[records.CLAIM]]
    moves += [(_moment(record["at"]), 1, record["author"], None) for record in by_kind[records.HANDOVER]]
    holding: dict[tuple[str, str], Claim] = {}
    for _at, _order, author, claim in sorted(moves, key=lambda move: move[:2]):
        if claim is None:
            holding = {key: held for key, held in holding.items() if key[0] != author.lower()}
        else:
            holding[(author.lower(), claim.machine)] = claim

    newest = mine[-1].record
    handover = newest if records.kind_of(newest) == records.HANDOVER else None
    marked = tuple(
        result._replace(stale=_why_stale(result, reports, commit, team_repo_revision, rules_changed, criteria, same_content))
        for result in sorted(results.values(), key=lambda result: (_moment(result.at), result.name))
    )
    return TaskState(
        task={"tracker": task["tracker"], "key": task["key"]},
        repo=newest["repo"],
        platform=newest["platform"],
        story=story,
        step=step,
        branch=newest_event["branch"] if newest_event else None,
        commit=newest_event["commit"] if newest_event else None,
        team_repo_revision=newest_event["team_repo_revision"] if newest_event else None,
        results=marked,
        pending_choice=pending,
        next_action=newest_event["next_action"] if newest_event else None,
        claims=tuple(sorted(holding.values(), key=lambda claim: (_moment(claim.at), claim.author, claim.machine))),
        handover=handover,
        last_at=newest["at"],
        history=tuple(mine),
    )


def checkpoint_state(state: TaskState) -> dict[str, Any]:
    """The `state` of a checkpoint event for this task: everything a later read needs, should every earlier record expire."""
    if state.step is None or state.commit is None or state.next_action is None:
        raise TugError("a task that has no event yet has no state to save in a checkpoint")
    return {
        "step": state.step,
        "commit": state.commit,
        "results": [
            {field: getattr(result, field) for field in ("name", "status", "summary", "report", "kind", "commit", "team_repo_revision", "at")} for result in state.results
        ],
        "pending_choice": state.pending_choice,
        "next_action": state.next_action,
        "story": state.story,
        "claims": [claim._asdict() for claim in state.claims],
    }


def load(
    address: TeamRepoAddress,
    task: Mapping[str, Any],
    repo: str,
    *,
    now: datetime | None = None,
    commit: str | None = None,
    team_repo_revision: str | None = None,
    rules_changed: RulesChanged | None = None,
    criteria: Sequence[str] | None = None,
    same_content: SameContent | None = None,
) -> Loaded:
    """Read the story's records from the store and work out the task's state. Expired records are dropped before anything else."""
    listing = recordstore.read_all(address, task)
    kept, expired = within_window(listing.records, now)
    state = compute(kept, task, repo, commit=commit, team_repo_revision=team_repo_revision, rules_changed=rules_changed, criteria=criteria, same_content=same_content)
    return Loaded(
        state,
        expired,
        tuple(item for item in listing.unreadable if item.newer),
        tuple(item for item in listing.unreadable if not item.newer),
        listing.cut_short,
    )
