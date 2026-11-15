"""`tug remove`: take Tugboat out of an app repo, or out of every repo on this machine.

The order matters, as it does for init, in reverse:

1. Read what Tugboat left in the repo, and list it.
2. Ask what to do with drafts nobody proposed yet. Wait for a confirmation.
3. Take the hooks out first. A hook that stays while the rest goes would
   call a tool that no longer knows the repo.
4. Delete the generated files, the folder `.tugboat/`, and Tugboat's block
   in the local Git exclude file.
5. Offer the pull request that takes the repo out of the project profile,
   and offer to delete a team repo clone that no repo uses any more. Task
   records live with that clone, not in an app repo, so the ones that were
   not published are asked about here.

Removal deletes only what init and sync generated. A file that Git tracks
is never changed: it is named, and left. Nothing in GitHub changes unless
the developer asks for the profile pull request. The tool does not
uninstall itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from tugboat import apprepo, ciworkflows, detect, hostfiles, initrepo, paths, profile, prompts, recordstore, repolist, settingsmerge, sync, teamcopy, teampr, update
from tugboat.address import TeamRepoAddress, parse_team_repo_address
from tugboat.errors import EXIT_ERROR, EXIT_OK, DataError, TugError
from tugboat.gitrun import GitError, git
from tugboat.strict_json import parse_json

KEEP, DELETE, STOP = "keep", "delete", "stop"
UNPUBLISHED_CHOICES = (KEEP, DELETE)
NOTHING = "Nothing was removed."
CAPTURE = "capture"  # the folder under `.tugboat/` that holds drafts
CAPTURE_STATE = "capture.json"
GENERATED_ROOT = ".claude/"  # every generated file is under it
MAX_NAMED = 4
# What becomes of the workflow that calls the CI secret scan.
CALLER_REMOVE, CALLER_COMMITTED, CALLER_CHANGED = "remove", "committed", "changed"


class Options(NamedTuple):
    yes: bool = False
    everything: bool = False  # every app repo on this machine
    unpublished: str | None = None  # KEEP or DELETE, for drafts nobody proposed yet, and for records not published yet
    profile_pr: bool = False  # open the pull request that takes the repo out of the profile
    delete_clone: bool = False  # delete a team repo clone that no repo uses any more


class Plan(NamedTuple):
    """What Tugboat left in one app repo, read from the repo itself."""

    repo: Path
    address: TeamRepoAddress | None  # the team repo it is set up for, when that can still be read
    github: str | None
    hooks: tuple[str, ...]  # Tugboat's hook commands in the settings file
    rules: int  # the permission rules Tugboat added to the settings file
    settings_tracked: bool  # Git tracks the settings file, so it is not changed
    generated: tuple[str, ...]  # generated files that will be deleted
    edited: tuple[str, ...]  # those of them that were changed after Tugboat wrote them
    committed: tuple[str, ...]  # generated files that Git tracks now. They are left.
    local_folder: bool  # `.tugboat/` is there
    excludes: tuple[str, ...]  # the lines of Tugboat's block in the local exclude file
    caller: str  # "", or one of the CALLER_ values
    drafts: tuple[str, ...]  # drafts nobody proposed yet, from `.tugboat/capture/`

    @property
    def anything(self) -> bool:
        return bool(self.hooks or self.rules or self.generated or self.local_folder or self.excludes or self.caller == CALLER_REMOVE)


class Result(NamedTuple):
    done: tuple[str, ...]
    problems: tuple[str, ...]  # what could not be removed
    kept_drafts: Path | None
    now_visible: tuple[str, ...]  # files Git shows now that the exclude block used to hide


# ------------------------------------------------------------------ reading


def _tracked(repo: Path, candidates: list[str]) -> set[str]:
    if not candidates:
        return set()
    try:
        return set(git("ls-files", "-z", "--", *candidates, cwd=repo).split("\0")) - {""}
    except GitError:
        return set(candidates)  # when Git cannot say, nothing is deleted


def _inside(repo: Path, path: str) -> bool:
    """True for a path that Tugboat could have generated and that really lies inside the repo."""
    parts = path.split("/")
    if not path.startswith(GENERATED_ROOT) or os.path.isabs(path) or any(part in {"", ".", ".."} for part in parts):
        return False
    # A folder on the way that is a link must not lead the deletion out of the repo.
    top = os.path.realpath(repo)
    return os.path.realpath((repo / path).parent).startswith(top.rstrip("/") + "/")


def _from_patterns(repo: Path, patterns: list[str]) -> list[str]:
    """The generated files, read from the exclude block. Used when the list of generated files is gone."""
    found: list[str] = []
    for pattern in patterns:
        path = pattern.strip("/")
        if path in {apprepo.LOCAL_FOLDER, settingsmerge.SETTINGS_FILE} or not path.startswith(GENERATED_ROOT):
            continue
        target = repo / path
        if target.is_dir() and not target.is_symlink():
            found.extend(item.relative_to(repo).as_posix() for item in sorted(target.rglob("*")) if item.is_file() or item.is_symlink())
        else:
            found.append(path)
    return found


def _digest(file: Path) -> str | None:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return None


def _drafts(repo: Path) -> tuple[str, ...]:
    folder = apprepo.local_folder(repo) / CAPTURE
    if not folder.is_dir() or folder.is_symlink():
        return ()
    return tuple(item.relative_to(folder).as_posix() for item in sorted(folder.rglob("*")) if item.is_file() and not item.is_symlink())


def _address(repo: Path) -> TeamRepoAddress | None:
    """The team repo this app repo is set up for: from the repo, or else from this machine's list."""
    state = teamcopy.read_state(repo)
    recorded = [state["team_repo"]] if state else []
    recorded += [entry.team_repo for entry in repolist.read() if entry.path == os.fspath(repo)]
    for text in recorded:
        with contextlib.suppress(DataError):
            return parse_team_repo_address(text)
    return None


def _caller(repo: Path, address: TeamRepoAddress | None) -> str:
    file = repo / ciworkflows.CALLER
    if not file.exists() and not file.is_symlink():
        return ""
    if _tracked(repo, [ciworkflows.CALLER]) or ciworkflows.is_committed(repo):
        return CALLER_COMMITTED
    if address is not None and ciworkflows.state(repo, address) == ciworkflows.OURS:
        return CALLER_REMOVE
    return CALLER_CHANGED


def read_plan(repo: Path) -> Plan:
    """Everything of Tugboat's in one app repo. Nothing is changed."""
    address = _address(repo)
    remote = detect.remote_address(repo)
    excludes = apprepo.excluded_patterns(repo)
    manifest = hostfiles.read_manifest(repo)
    listed: dict[str, str | None] = dict(manifest) if manifest else dict.fromkeys(_from_patterns(repo, excludes))
    present = [path for path in listed if _inside(repo, path) and ((repo / path).is_file() or (repo / path).is_symlink())]
    tracked = _tracked(repo, present)
    generated = [path for path in present if path not in tracked]
    edited = [path for path in generated if listed[path] is not None and _digest(repo / path) != listed[path]]
    return Plan(
        repo=repo,
        address=address,
        github=remote.slug if remote else None,
        hooks=tuple(settingsmerge.installed_hooks(repo)),
        rules=len(_rules_in_file(repo)),
        settings_tracked=bool(_tracked(repo, [settingsmerge.SETTINGS_FILE])),
        generated=tuple(generated),
        edited=tuple(edited),
        committed=tuple(path for path in present if path in tracked),
        local_folder=apprepo.local_folder(repo).exists() or apprepo.local_folder(repo).is_symlink(),
        excludes=tuple(excludes),
        caller=_caller(repo, address),
        drafts=_drafts(repo),
    )


def _settings(repo: Path) -> object:
    try:
        return parse_json((repo / settingsmerge.SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, DataError):
        return None


def _rules_in_file(repo: Path) -> list[str]:
    """The permission rules Tugboat added that are still in the settings file."""
    settings = _settings(repo)
    permissions = settings.get("permissions") if isinstance(settings, dict) else None
    ask = permissions.get("ask") if isinstance(permissions, dict) else None
    owned = set(settingsmerge.read_owned_rules(repo))
    return [rule for rule in ask if rule in owned] if isinstance(ask, list) else []


# ----------------------------------------------------------------- the list


def _count(number: int, one: str, many: str | None = None) -> str:
    return f"{number} {one if number == 1 else many or one + 's'}"


def _named(items: tuple[str, ...] | list[str]) -> str:
    more = len(items) - MAX_NAMED
    return ", ".join(items[:MAX_NAMED]) + (f", and {more} more" if more > 0 else "")


def render_plan(found: Plan) -> str:
    name = f" ({found.github})" if found.github else ""
    team = f", for the team repo {found.address.url}" if found.address else ""
    lines = [f"Tugboat is set up in {found.repo}{name}{team}.", "`tug remove` takes out:"]
    settings = settingsmerge.SETTINGS_FILE
    if (found.hooks or found.rules) and not found.settings_tracked:
        parts = [_count(len(found.hooks), "hook")] if found.hooks else []
        parts += [_count(found.rules, "permission rule")] if found.rules else []
        lines.append(f"- Tugboat's {' and '.join(parts)} in {settings}. Settings of your own in that file stay. The file is deleted when it holds nothing else.")
    if found.generated:
        lines.append(f"- {_count(len(found.generated), 'generated file')} under .claude/: {_named(found.generated)}")
        if found.edited:
            were = "was" if len(found.edited) == 1 else "were"
            lines.append(f"  {len(found.edited)} of them {were} changed after Tugboat wrote {'it' if len(found.edited) == 1 else 'them'}, and {'goes' if len(found.edited) == 1 else 'go'} too: {_named(found.edited)}")
    if found.local_folder:
        lines.append(f"- The folder {apprepo.LOCAL_FOLDER}/, with the read-only copy of the team repo and Tugboat's state for this repo.")
    if found.excludes:
        lines.append(f"- Tugboat's block of {_count(len(found.excludes), 'line')} in this repo's local Git exclude file. Every other line stays.")
    if found.caller == CALLER_REMOVE:
        lines.append(f"- {ciworkflows.CALLER}, which Tugboat wrote and nobody committed.")

    left = []
    if found.settings_tracked and (found.hooks or found.rules):
        left.append(f"- {settings}: Git tracks it in this repo, so Tugboat does not change it. Tugboat's hooks stay in it until a person takes them out.")
    if found.committed:
        left.append(f"- {_named(found.committed)}: Git tracks {'it' if len(found.committed) == 1 else 'them'} now, so {'it belongs' if len(found.committed) == 1 else 'they belong'} to the repo.")
    if found.caller == CALLER_COMMITTED:
        left.append(f"- {ciworkflows.CALLER}: it is committed, so it belongs to the repo. A pull request in this repo removes it.")
    elif found.caller == CALLER_CHANGED:
        left.append(f"- {ciworkflows.CALLER}: it is not what Tugboat writes, so somebody changed it.")
    left.append("- The team repo. The project profile still lists this repo, and what was merged or proposed there stays.")
    lines.append("It leaves:")
    lines.extend(left)
    if found.drafts:
        lines.append(f"{_count(len(found.drafts), 'file')} in {apprepo.LOCAL_FOLDER}/{CAPTURE}/ {'is a draft' if len(found.drafts) == 1 else 'are drafts'} that nobody proposed yet: {_named(found.drafts)}")
    return "\n".join(lines)


# ----------------------------------------------------------------- removing


def _status(repo: Path) -> set[str]:
    try:
        entries = git("status", "--porcelain", "-z", "--untracked-files=all", cwd=repo).split("\0")
    except GitError:
        return set()
    return {entry[3:] for entry in entries if len(entry) > 3}


def _prune_empty(folder: Path, stop: Path) -> None:
    while folder != stop and stop in folder.parents and folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _delete_tree(folder: Path) -> None:
    """Delete a folder of Tugboat's own. Its files are read-only, and Git's are too."""
    if folder.is_symlink() or folder.is_file():
        folder.unlink()
        return
    if not folder.exists():
        return
    for current, _folders, _files in os.walk(folder):
        with contextlib.suppress(OSError):
            os.chmod(current, 0o700)  # a folder must be writable before what it holds can go
    shutil.rmtree(folder)


def _holds_nothing(file: Path) -> bool:
    try:
        return parse_json(file.read_text(encoding="utf-8")) == {}
    except (OSError, UnicodeDecodeError, DataError):
        return False


def _kept_target(name: str, now: datetime | None = None) -> Path:
    """A new folder of its own under Tugboat's folder for what a developer chose to keep."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    holder = paths.kept_folder()
    holder.mkdir(parents=True, exist_ok=True)
    target, count = holder / f"{name}-{stamp}", 1
    while target.exists():
        count += 1
        target = holder / f"{name}-{stamp}-{count}"
    target.mkdir()
    return target


def _keep_drafts(repo: Path) -> Path:
    """Move the drafts out of the repo."""
    target = _kept_target(repo.name)
    local = apprepo.local_folder(repo)
    shutil.move(os.fspath(local / CAPTURE), os.fspath(target / CAPTURE))
    state = local / CAPTURE_STATE
    if state.is_file() and not state.is_symlink():
        shutil.move(os.fspath(state), os.fspath(target / CAPTURE_STATE))
    return target


def execute(found: Plan, unpublished: str | None = None) -> Result:
    """Remove what the plan lists. The hooks go first."""
    repo = found.repo
    done: list[str] = []
    problems: list[str] = []
    settings = settingsmerge.SETTINGS_FILE
    before = _status(repo)

    if (found.hooks or found.rules) and not found.settings_tracked:
        # Reads the record of Tugboat's own rules from `.tugboat/`, so it runs before that folder goes.
        merge = settingsmerge.merge_hooks(repo, [], "tug", ask_rules=[])
        if merge.problem:
            problems.append(merge.problem)
        elif (merge.changed or merge.rules_changed) and _holds_nothing(repo / settings):
            (repo / settings).unlink()
            _prune_empty((repo / settings).parent, repo)
            done.append(f"deleted {settings}, which held nothing but Tugboat's hooks and rules")
        else:
            done.append(f"took Tugboat's hooks and rules out of {settings}")
    left = settingsmerge.installed_hooks(repo)
    if left:
        why = ", which Git tracks in this repo" if found.settings_tracked else ""
        problems.append(f"{_count(len(left), 'Tugboat hook is', 'Tugboat hooks are')} still in {settings}{why}. Delete the entries whose command starts with `tug hook` by hand")

    removed = 0
    for path in found.generated:
        target = repo / path
        try:
            target.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError as error:
            problems.append(f"{path} could not be deleted ({error.strerror or error})")
            continue
        _prune_empty(target.parent, repo)
    if removed:
        done.append(f"deleted {_count(removed, 'generated file')} under .claude/")

    if found.caller == CALLER_REMOVE:
        caller = repo / ciworkflows.CALLER
        with contextlib.suppress(FileNotFoundError):
            caller.unlink()
        _prune_empty(caller.parent, repo)
        done.append(f"deleted {ciworkflows.CALLER}")

    kept: Path | None = None
    local = apprepo.local_folder(repo)
    try:
        if found.drafts and unpublished == KEEP:
            kept = _keep_drafts(repo)
            done.append(f"moved {_count(len(found.drafts), 'draft file')} to {kept}")
        if found.local_folder:
            _delete_tree(local)
            done.append(f"deleted the folder {apprepo.LOCAL_FOLDER}/")
    except OSError as error:
        problems.append(f"the folder {apprepo.LOCAL_FOLDER}/ could not be deleted ({error.strerror or error})")

    if found.excludes:
        try:
            apprepo.set_excluded_patterns(repo, [])
            done.append("took Tugboat's block out of the local Git exclude file")
        except (OSError, GitError) as error:
            problems.append(f"Tugboat's block in the local Git exclude file could not be removed ({error})")

    with contextlib.suppress(OSError):
        repolist.forget(repo)
    return Result(tuple(done), tuple(problems), kept, tuple(sorted(_status(repo) - before)))


def render_result(found: Plan, result: Result) -> str:
    lines = ["Removed from this repo:" if result.done else "Nothing was removed from this repo."]
    lines.extend(f"- {line[0].upper()}{line[1:]}." for line in result.done)
    if result.problems:
        lines.append("Not everything could be removed:")
        lines.extend(f"- {problem}." for problem in result.problems)
    if result.now_visible:
        lines.append(
            f"Git now shows {_count(len(result.now_visible), 'file')} that Tugboat's exclude block used to hide: {_named(result.now_visible)}. "
            "Tugboat did not write what is in there, so it stays."
        )
    if not result.problems:
        lines.append("A new agent session in this repo loads no Tugboat file, and no hook calls `tug`. A session that is open now may keep what it loaded, so start a new one.")
        lines.append("`tug init` sets the repo up again at any time.")
    if result.kept_drafts is not None:
        lines.append(f"To use the kept drafts after a new `tug init`, move what is in {result.kept_drafts} back into {apprepo.LOCAL_FOLDER}/.")
    return "\n".join(lines)


# ------------------------------------------------------------- the questions


def _drafts_choice(found: Plan, options: Options) -> str | None:
    """What to do with drafts nobody proposed yet: KEEP, DELETE, or STOP. None when there are none."""
    if not found.drafts:
        return None
    if options.unpublished is not None:
        return options.unpublished
    print()
    return prompts.one(
        "What should happen to the drafts?",
        (
            (KEEP, f"Keep them. They are moved to {paths.kept_folder()}/, outside the repo."),
            (DELETE, "Delete them with the folder."),
            (STOP, "Stop now, and remove nothing. `tug capture propose` and `tug decision propose` send drafts to the team repo."),
        ),
    )


def _wants(options: Options, flag: bool, question: str) -> bool:
    """An offer. With --yes, or without a terminal, it is taken only when its own option was given."""
    if flag:
        return True
    if options.yes or not prompts.interactive():
        return False
    return prompts.confirm(question)


def _offer_profile_pr(found: Plan, options: Options) -> None:
    """Offer the pull request that takes this repo out of the project profile."""
    address, github = found.address, found.github
    if address is None or github is None or not (paths.team_repo_clone(address) / ".git").exists():
        return
    try:
        working = profile.load(address)
    except TugError:
        return
    if working.profile is None or profile.find_repo(working.profile, github) is None:
        return
    print()
    print(f"The project profile in the team repo {address.slug} still lists {github}. That is right while a teammate still uses Tugboat in that repo.")
    by_hand = f"Nothing in the team repo was changed. To take {github} out later, change `profile.json` through a pull request in the team repo."
    if not _wants(options, options.profile_pr, f"Open a pull request that takes {github} out of the profile, for the whole team?"):
        print(by_hand)
        return
    # The change is made to the newest approved profile, not to what this machine fetched last.
    report = sync.sync_team_repo(address)
    if not report.ok:
        print(f"The team repo could not be brought up to date ({report.error}), so no pull request was opened. {by_hand}")
        return
    try:
        working = profile.load(address)
    except TugError as error:
        print(f"{error} {by_hand}")
        return
    if working.note:
        print(working.note)
    if working.profile is None or profile.find_repo(working.profile, github) is None:
        print(f"The newest profile no longer lists {github}, so there is nothing to propose.")
        return
    if profile.below_minimum(working.approved):
        print(f"This project needs tug {working.approved['minimum_tool_version']} or newer, so this machine opens no pull request. {by_hand}")
        return
    proposed = profile.without_repo(working.profile, github)
    needs_proposal, pending, note = profile.plan(address, working, proposed)
    if note:
        print(note)
    if not needs_proposal:
        print("The approved profile does not list the repo, so there is nothing to propose." if pending is None else f"That change is already proposed: {profile.where(pending)}")
        return
    if pending is not None and pending.branch:
        print(f"The new pull request replaces this machine's earlier proposal. Close that one: {profile.where(pending)}")
    _, proposal, failure = profile.propose(address, working, proposed, title=f"Take {github} out of the project profile", origin=f"Proposed by `tug remove`, run in {github}.")
    if proposal is None:
        # Put back what this machine worked from: a removal that was not proposed must not linger as a local profile.
        if working.pending is not None:
            profile.write_pending(address, working.pending)
        else:
            profile.clear_pending(address)
        print(f"The pull request could not be made: {failure}")
        print(by_hand)
        return
    print(teampr.render(proposal))


def _clone_notes(address: TeamRepoAddress, clone: Path) -> list[str]:
    notes = []
    with contextlib.suppress(GitError):
        edits = [line for line in git("status", "--porcelain", cwd=clone).splitlines() if line.strip()]
        if edits:
            notes.append(f"It holds {_count(len(edits), 'local edit')} that would be lost: {_named([line[3:] for line in edits])}.")
    pending = profile.read_pending(address)
    if pending is not None:
        notes.append(f"A profile change that this machine proposed, and that is not merged yet, goes with it. Its pull request stays in GitHub: {profile.where(pending)}")
    return notes


def _offer_clone(address: TeamRepoAddress, options: Options) -> None:
    """Offer to delete a team repo clone that no app repo on this machine uses any more."""
    clone = paths.team_repo_clone(address)
    if not clone.exists():
        return
    users = [entry.path for entry in repolist.read() if entry.team_repo == address.url]
    print()
    if users:
        print(f"The clone of the team repo {address.slug} stays, because {_count(len(users), 'app repo')} on this machine still {'uses' if len(users) == 1 else 'use'} it: {_named(users)}")
        return
    print(f"No app repo on this machine's list uses the clone of the team repo {address.slug} any more. It is at {clone}.")
    print("A repo that an older tug set up is not on the list until its next sync. If one still uses this clone, its next sync clones the team repo again.")
    for note in _clone_notes(address, clone):
        print(note)
    store = recordstore.folder(address)
    waiting = recordstore.unpublished(address)
    if waiting:
        print(f"Its task records are at {store}. {_count(len(waiting), 'of them was', 'of them were')} not published yet, and would be lost with the clone: {_named(waiting)}")
    if not _wants(options, options.delete_clone, "Delete this clone?"):
        print("The clone was kept. `tug remove --delete-clone`, or deleting the folder by hand, removes it.")
        return
    if waiting:
        choice = _records_choice(options)
        if choice not in UNPUBLISHED_CHOICES:
            print("The clone was kept, because it holds task records that were not published. With --delete-clone, pass --unpublished keep or --unpublished delete.")
            return
        if choice == KEEP:
            try:
                kept = _keep_records(address, waiting)
            except OSError as error:
                print(f"The task records could not be moved ({error.strerror or error}), so the clone was kept.")
                return
            print(f"Moved {_count(len(waiting), 'task record')} to {kept}.")
    try:
        _delete_tree(store)
        _delete_tree(clone)
    except OSError as error:
        print(f"The clone could not be deleted ({error.strerror or error}).")
        return
    for file in (paths.sync_state_file(address), paths.sync_lock_file(address), paths.records_lock_file(address), paths.pending_profile_file(address)):
        with contextlib.suppress(OSError):
            file.unlink()
    for folder in (clone.parent, store.parent, paths.sync_state_file(address).parent, paths.pending_profile_file(address).parent):
        with contextlib.suppress(OSError):
            _prune_empty(folder, paths.tug_home())
    print("The clone was deleted. Nothing in GitHub changed.")


def _records_choice(options: Options) -> str | None:
    """What to do with task records that were not published, before their clone is deleted."""
    if options.unpublished is not None:
        return options.unpublished
    if options.yes or not prompts.interactive():
        return None
    print()
    return prompts.one(
        "What should happen to the task records that were not published?",
        (
            (KEEP, f"Keep them. They are moved to {paths.kept_folder()}/."),
            (DELETE, "Delete them with the clone."),
            (STOP, "Keep the clone. Publishing task records is not built yet."),
        ),
    )


def _keep_records(address: TeamRepoAddress, waiting: tuple[str, ...]) -> Path:
    """Move the unpublished record files out of the store, each at its own path."""
    target = _kept_target(f"{address.name}-records")
    store = recordstore.folder(address)
    for relative in waiting:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(os.fspath(store / relative), os.fspath(destination))
    return target


def _closing() -> str:
    """What is left on the machine, and the command that uninstalls the tool."""
    home = paths.tug_home()
    remaining = repolist.read()
    uninstall = f"uv tool uninstall {update.PACKAGE}"
    if remaining:
        return (
            f"Tugboat is still set up in {_count(len(remaining), 'app repo')} on this machine: {_named([entry.path for entry in remaining])}\n"
            f"Keep the tool installed while {'it uses' if len(remaining) == 1 else 'they use'} it: without `tug`, the hooks there deny every action. "
            f"`tug remove --all` takes Tugboat out of {'it' if len(remaining) == 1 else 'all of them'}."
        )
    lines = [
        "No app repo on this machine's list is set up with Tugboat any more. A repo that an older tug set up joins the list at its next sync, so check any you remember.",
        f"Tugboat does not uninstall itself. To uninstall it: `{uninstall}`",
    ]
    if home.exists():
        lines.append(f"Its folder {home} stays until you delete it. It holds the team repo clones that were kept, profile changes not merged yet, and the drafts and task records you chose to keep.")
    return "\n".join(lines)


# ------------------------------------------------------------------- running


def _targets(start: str | os.PathLike[str], everything: bool) -> list[Plan]:
    """The repos to work on, each with what is in it. A listed repo that is gone leaves the list."""
    if not everything:
        return [read_plan(apprepo.find_app_repo(start))]
    folders: list[Path] = []
    for entry in repolist.read():
        try:
            repo = apprepo.find_app_repo(entry.path)
        except TugError:
            repo = None
        if repo is None or os.fspath(repo) != entry.path:
            repolist.forget(entry.path)
            print(f"{entry.path} is not an app repo any more. It was taken off this machine's list.")
            continue
        folders.append(repo)
    with contextlib.suppress(TugError):
        here = apprepo.find_app_repo(start)
        if here not in folders:
            folders.append(here)  # set up by an older tool, and not synced since
    plans = []
    for repo in folders:
        found = read_plan(repo)
        if found.anything:
            plans.append(found)
        elif repolist.forget(repo):
            print(f"Tugboat is no longer set up in {repo}. It was taken off this machine's list.")
    return plans


def run(start: str | os.PathLike[str], options: Options) -> int:
    if options.unpublished is not None and options.unpublished not in UNPUBLISHED_CHOICES:
        raise TugError(f"--unpublished must be one of: {', '.join(UNPUBLISHED_CHOICES)}")
    plans = _targets(start, options.everything)
    waiting = [found for found in plans if found.anything]
    if not waiting:
        for found in plans:
            repolist.forget(found.repo)
        print("Tugboat is not set up in any app repo on this machine's list." if options.everything else f"Tugboat is not set up in {plans[0].repo}.")
        print(NOTHING)
        _after(plans, options, removed=[])
        return EXIT_OK

    # Everything that can stop the command is settled before the first repo is touched.
    if not options.yes and not prompts.interactive():
        for found in waiting:
            print(render_plan(found))
            print()
        raise TugError(f"removal needs a person to confirm it. Run it in a terminal, or pass --yes. {NOTHING}")
    if options.yes and options.unpublished is None:
        holding = [found for found in waiting if found.drafts]
        if holding:
            print(render_plan(holding[0]))
            raise TugError(f"{holding[0].repo} holds drafts that nobody proposed yet. Pass --unpublished keep or --unpublished delete. {NOTHING}")

    removed: list[Plan] = []
    failed = False
    for index, found in enumerate(waiting):
        if index:
            print()
        print(render_plan(found))
        choice = _drafts_choice(found, options)
        if choice != STOP and not options.yes:
            print()
        if choice == STOP or not (options.yes or prompts.confirm("Remove Tugboat from this repo?")):
            if not options.everything:
                raise TugError(f"stopped. {NOTHING}")
            print(f"Skipped. Nothing was removed from {found.repo}.")
            continue
        result = execute(found, choice)
        print()
        print(render_result(found, result))
        failed = failed or bool(result.problems)
        removed.append(found)
        _offer_profile_pr(found, options)
    _after(plans, options, removed)
    return EXIT_ERROR if failed else EXIT_OK


def _after(plans: list[Plan], options: Options, removed: list[Plan]) -> None:
    """The offers and the closing lines that follow the removals."""
    addresses: dict[str, TeamRepoAddress] = {found.address.url: found.address for found in removed if found.address is not None}
    if options.everything:
        # With every repo done, any clone on this machine may be without a user.
        for address in initrepo.known_team_repos():
            addresses.setdefault(address.url, address)
    for address in addresses.values():
        _offer_clone(address, options)
    print()
    print(_closing())
