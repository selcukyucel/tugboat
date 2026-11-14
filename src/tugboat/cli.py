"""The `tug` command.

Each subcommand is one entry in COMMANDS. A command that is planned and not
written yet says so and exits with its own code. It never pretends to work.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from typing import NamedTuple

from tugboat.errors import EXIT_ERROR, EXIT_NOT_BUILT, EXIT_OK, TugError
from tugboat.version import installed_version

Handler = Callable[[argparse.Namespace], int]
Configure = Callable[[argparse.ArgumentParser], None]


class Command(NamedTuple):
    name: str
    summary: str
    handler: Handler | None = None
    configure: Configure | None = None


def _version(_args: argparse.Namespace) -> int:
    print(installed_version())
    return EXIT_OK


def _configure_init(parser: argparse.ArgumentParser) -> None:
    from tugboat import profile

    def values(table: object) -> str:
        return ", ".join(value for value, _ in table)  # type: ignore[union-attr]

    parser.add_argument("--yes", action="store_true", help="answer yes to every confirmation, and ask no question. A choice with no default must then be given as an option.")
    parser.add_argument("--platform", choices=("ios", "android"), help="only for a repo that holds both an iOS and an Android project")
    parser.add_argument("--team-repo", metavar="ADDRESS", help="the team repo, as git@github.com:owner/name.git or https://github.com/owner/name.git. Asked for when not given.")
    project = parser.add_argument_group("the project's choices", "Asked at the first init for a project, and saved in the team repo. On a later init, an option changes that choice.")
    project.add_argument("--project", metavar="NAME", help="the project's name, in lowercase. Only at the first init.")
    project.add_argument("--tracker", choices=[value for value, _ in profile.TRACKERS], help="where the stories come from")
    project.add_argument("--ci", metavar="LIST", help=f"the optional CI checks, separated by commas, or 'none': {values(profile.CI_FEATURES)}")
    project.add_argument("--surfaces", metavar="LIST", help=f"the agent tools the team uses, separated by commas: {values(profile.SURFACES)}")
    project.add_argument("--approval", choices=[value for value, _ in profile.APPROVAL_MODES], help="who approves a change to the team repo: a teammate, or the owner of a solo project")
    project.add_argument("--change", action="store_true", help="ask the project questions again, to change a choice")
    project.add_argument("--propose-again", action="store_true", help="open a new pull request for a profile change whose pull request was closed without a merge")


def _list_option(text: str | None, table: object, option: str, *, none_allowed: bool) -> list[str] | None:
    if text is None:
        return None
    allowed = [value for value, _ in table]  # type: ignore[union-attr]
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if none_allowed and parts == ["none"]:
        return []
    unknown = [part for part in parts if part not in allowed]
    if unknown or not parts or len(set(parts)) != len(parts):
        raise TugError(f"{option} must be a list of these, separated by commas{', or none' if none_allowed else ''}: {', '.join(allowed)}")
    return parts


def _init(args: argparse.Namespace) -> int:
    from tugboat import initrepo, profile

    options = initrepo.Options(
        yes=args.yes,
        platform=args.platform,
        team_repo=args.team_repo,
        project=args.project,
        tracker=args.tracker,
        ci=_list_option(args.ci, profile.CI_FEATURES, "--ci", none_allowed=True),
        surfaces=_list_option(args.surfaces, profile.SURFACES, "--surfaces", none_allowed=False),
        approval=args.approval,
        change=args.change,
        propose_again=args.propose_again,
    )
    return initrepo.run(".", options)


def _configure_team(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="team_command", metavar="<action>", required=True)
    new = actions.add_parser(
        "new",
        help="Write the starter layout into an empty folder",
        description="Write the starter layout of a team repo into an empty folder. An existing .git folder is fine. "
        "Nothing is committed, pushed, or created in GitHub.",
    )
    new.add_argument("folder", help="the empty folder, usually a fresh clone of the new team repo")
    check = actions.add_parser(
        "check",
        help="Check the files of a team repo",
        description="Check every file Tugboat reads in a team repo. Exits with 1 when a file is not valid.",
    )
    check.add_argument("folder", nargs="?", default=".", help="the team repo folder (default: the current folder)")
    propose = actions.add_parser(
        "propose",
        help="Propose a change to the team repo as a pull request",
        description="Push a branch with the given files and open a pull request in the team repo. "
        "Nothing is merged: a person reviews and merges it in GitHub.",
    )
    propose.add_argument("--title", required=True, help="one line that says what the change is")
    propose.add_argument("--body", default="", help="why the change is proposed")
    propose.add_argument("--body-file", metavar="FILE", help="read the description from a file instead")
    propose.add_argument("--file", action="append", default=[], metavar="TEAM_PATH=LOCAL_FILE", help="put a local file at a path in the team repo. Can be given more than once.")
    propose.add_argument("--remove", action="append", default=[], metavar="TEAM_PATH", help="remove a file from the team repo. Can be given more than once.")
    propose.add_argument("--team-repo", metavar="ADDRESS", help="the team repo. Not needed inside an app repo that Tugboat was set up in.")
    propose.add_argument("--yes", action="store_true", help="open the pull request without being asked")


def _team(args: argparse.Namespace) -> int:
    # Imported here so that `tug version` and `tug hook` start without loading it.
    from tugboat import team_repo

    if args.team_command == "new":
        written = team_repo.new_team_repo(args.folder)
        for path in written:
            print(f"wrote {path}")
        print(f"Wrote {len(written)} files to {args.folder}. Review them, then commit and push them yourself.")
        return EXIT_OK

    if args.team_command == "propose":
        return _team_propose(args)

    report = team_repo.check_team_repo(args.folder)
    for problem in report.problems:
        prefix = "warning: " if problem.level == "warning" else ""
        print(f"{prefix}{problem.path}: {problem.message}")
    errors, warnings = len(report.errors), len(report.warnings)
    counts = f"{errors} problem{'' if errors == 1 else 's'}, {warnings} warning{'' if warnings == 1 else 's'}"
    print(f"Checked {report.files_checked} file{'' if report.files_checked == 1 else 's'}: {counts}.")
    return EXIT_ERROR if errors else EXIT_OK


def _configure_sync(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--team-repo",
        metavar="ADDRESS",
        help="the team repo, as git@github.com:owner/name.git or https://github.com/owner/name.git. "
        "Only needed outside an app repo that `tug init` set up.",
    )
    parser.add_argument(
        "--app-repo",
        metavar="FOLDER",
        help="an app repo to bring up to date, when the command is run from somewhere else",
    )
    parser.add_argument("--force", action="store_true", help="rewrite the copy even when nothing changed")


def _sync(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tugboat import apprepo, appsync, sync, teamcopy
    from tugboat.address import parse_team_repo_address
    from tugboat.errors import DataError

    repo: Path | None = None
    recorded: str | None = None
    if args.app_repo:
        repo = apprepo.find_app_repo(args.app_repo)
    else:
        # Inside an app repo that already has a copy, no option is needed.
        with contextlib.suppress(TugError):
            found = apprepo.find_app_repo(".")
            if teamcopy.read_state(found) is not None:
                repo = found
    if repo is not None:
        state = teamcopy.read_state(repo)
        recorded = state["team_repo"] if state else None

    if args.team_repo:
        address = parse_team_repo_address(args.team_repo)
        if recorded and recorded != address.url:
            raise TugError(f"this repo is set up for the team repo {recorded}. It was left as it is.")
    elif recorded:
        try:
            address = parse_team_repo_address(recorded)
        except DataError as error:
            raise TugError(f"the team repo address saved in this repo is not valid: {recorded}") from error
    else:
        raise TugError("this folder is not set up with Tugboat. Run `tug init` inside the app repo first.")

    if repo is None:
        report = sync.sync_team_repo(address)
        print(sync.render(report))
        usable = report.usable
    else:
        result = appsync.run(repo, address, force=args.force)
        print(appsync.render(result))
        usable = result.sync.usable
    # A sync that did not work still lets work go on, as long as there is a clone to work from.
    return EXIT_OK if usable else EXIT_ERROR


def _configure_hook(parser: argparse.ArgumentParser) -> None:
    from tugboat.hook import EVENTS

    parser.add_argument("event", choices=EVENTS, help="the event the agent tool is reporting")


def _hook(args: argparse.Namespace) -> int:
    from tugboat import hook

    return hook.main(args.event)


def _configure_update(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check", action="store_true", help="only say whether a newer release exists. Install nothing.")


def _update(args: argparse.Namespace) -> int:
    from tugboat import update

    return update.run(check_only=args.check)


def _configure_owner(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", metavar="PATH", help="a file or a folder in this app repo. It need not exist yet.")


def _owner(args: argparse.Namespace) -> int:
    from tugboat import apprepo, detect, estate, teamcopy

    repo = apprepo.find_app_repo(".")
    if teamcopy.read_state(repo) is None:
        raise TugError("this folder is not set up with Tugboat. Run `tug init` inside the app repo first.")
    loaded = estate.load(repo)
    if loaded.apps is None:
        raise TugError(loaded.problem or "the team repo has no estate map, so no owner can be named.")
    remote = detect.remote_address(repo)
    apps = estate.apps_of(loaded.apps, remote.slug if remote else None)
    if not apps:
        name = remote.slug if remote else "this repo"
        raise TugError(f"{name} is not in the estate map ({estate.COPY_PATH}), so no owner can be named. A pull request in the team repo adds it.")
    failed = False
    for given in args.paths:
        try:
            path = estate.repo_path(repo, given)
        except TugError as error:
            print(f"{given}: {error}")
            failed = True
            continue
        for found in estate.owners_of(apps, path):
            print(f"{path}: {estate.owner_words(found)}")
    return EXIT_ERROR if failed else EXIT_OK


def _configure_notes(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", metavar="PATH", help="a file or a folder in this app repo. It need not exist yet. With no path, every note about this repo is listed.")


def _notes(args: argparse.Namespace) -> int:
    from tugboat import apprepo, detect, estate, knowledge, teamcopy

    repo = apprepo.find_app_repo(".")
    if teamcopy.read_state(repo) is None:
        raise TugError("this folder is not set up with Tugboat. Run `tug init` inside the app repo first.")
    loaded = knowledge.load(repo)
    remote = detect.remote_address(repo)
    notes = knowledge.notes_of(loaded.notes, remote.slug if remote else None)
    name = remote.slug if remote else "this repo"
    for problem in loaded.problems:
        print(f"Note: {problem}.")
    if not args.paths:
        if not notes:
            print(f"The team repo has no knowledge note about {name}.")
        for note in notes:
            print(f"- {note.title}: {note.file} (covers {', '.join(note.paths)}; owner {note.owner}; last verified {note.last_verified})")
        return EXIT_OK
    asked: list[tuple[str, bool]] = []
    failed = False
    for given in args.paths:
        try:
            path = estate.repo_path(repo, given)
        except TugError as error:
            print(f"{given}: {error}")
            failed = True
            continue
        asked.append((path, (repo / path).is_dir()))
    found = knowledge.matching(notes, asked)
    for match in found:
        print(knowledge.note_line(match))
    if asked and not found:
        print(f"No knowledge note covers {', '.join(path for path, _ in asked)}. There is none to read for {'it' if len(asked) == 1 else 'them'}.")
    return EXIT_ERROR if failed else EXIT_OK


def _configure_capture(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("status", help="list the draft notes and where each stands", description="List the draft notes in this app repo, and the proposals dropped before.")
    accept = actions.add_parser("accept", help="mark drafts as accepted by the developer", description="Mark drafts as accepted, as they are now. A draft edited later must be accepted again.")
    accept.add_argument("names", nargs="+", metavar="NAME", help="a draft, named by its path under knowledge/, such as auth/token-refresh.md")
    drop = actions.add_parser("drop", help="remove drafts the developer does not want", description="Remove drafts and remember their content, so the same proposal is not offered again.")
    drop.add_argument("names", nargs="+", metavar="NAME", help="a draft, named by its path under knowledge/")
    propose = actions.add_parser("propose", help="open one pull request with the accepted drafts", description="Open one pull request in the team repo with every accepted draft. Nothing is merged: a person reviews and merges it in GitHub.")
    propose.add_argument("--task", metavar="TEXT", help="the task the notes come from, such as a story key. It goes into the pull request.")
    propose.add_argument("--yes", action="store_true", help="open the pull request without being asked")


def _capture(args: argparse.Namespace) -> int:
    from tugboat import apprepo, capture, detect, teamcopy, teampr
    from tugboat.address import parse_team_repo_address

    repo = apprepo.find_app_repo(".")
    state = teamcopy.read_state(repo)
    if state is None:
        raise TugError("this folder is not set up with Tugboat. Run `tug init` inside the app repo first.")
    remote = detect.remote_address(repo)
    github = remote.slug if remote else None
    if args.action == "status":
        print(capture.render_status(repo, capture.scan(repo, github)))
        return EXIT_OK
    if args.action == "accept":
        for draft in capture.accept(repo, github, args.names):
            print(f"Accepted {draft.name}. It goes into the next pull request, as it is now.")
        return EXIT_OK
    if args.action == "drop":
        for draft in capture.drop(repo, github, args.names):
            print(f"Dropped {draft.name}. The draft is removed, and the same content will not be offered again.")
        return EXIT_OK
    address = parse_team_repo_address(state["team_repo"])
    if not args.yes:
        ready = [draft for draft in capture.scan(repo, github) if draft.status == capture.ACCEPTED]
        if not ready:
            raise TugError("no draft is accepted, so there is nothing to propose. `tug capture status` lists the drafts.")
        print(f"This opens one pull request in {address.slug} with {len(ready)} note{'' if len(ready) == 1 else 's'}:")
        for draft in ready:
            print(f"  {draft.team_path}")
        if not sys.stdin.isatty():
            raise TugError("a pull request needs a person to confirm it. Run this in a terminal, or pass --yes. Nothing was proposed.")
        if input("Open this pull request? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise TugError("stopped. Nothing was proposed.")
    outcome = capture.propose(repo, github, address, task=args.task)
    print(teampr.render(outcome.proposal))
    if outcome.left:
        print(f"{len(outcome.left)} draft{'' if len(outcome.left) == 1 else 's'} stayed in {capture.DRAFTS}/, because {'it was' if len(outcome.left) == 1 else 'they were'} not accepted: {', '.join(draft.name for draft in outcome.left)}.")
    return EXIT_OK


def _configure_decision(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("status", help="list the draft decision records and the next free ID", description="List the draft decision records in this app repo, and the next free ID.")
    propose = actions.add_parser("propose", help="open one pull request with draft records", description="Open one pull request in the team repo with the named draft records. Nothing is merged: a person reviews and merges it in GitHub.")
    propose.add_argument("names", nargs="+", metavar="NAME", help="a draft, named by its file name, such as DEC-0003-use-swift-testing.md")
    propose.add_argument("--yes", action="store_true", help="open the pull request without being asked")


def _decision(args: argparse.Namespace) -> int:
    from tugboat import apprepo, decisions, detect, teamcopy, teampr
    from tugboat.address import parse_team_repo_address

    repo = apprepo.find_app_repo(".")
    state = teamcopy.read_state(repo)
    if state is None:
        raise TugError("this folder is not set up with Tugboat. Run `tug init` inside the app repo first.")
    remote = detect.remote_address(repo)
    github = remote.slug if remote else None
    if args.action == "status":
        print(decisions.render_status(repo, decisions.scan(repo, github)))
        return EXIT_OK
    address = parse_team_repo_address(state["team_repo"])
    if not args.yes:
        print(f"This opens one pull request in {address.slug} with: {', '.join(args.names)}")
        if not sys.stdin.isatty():
            raise TugError("a pull request needs a person to confirm it. Run this in a terminal, or pass --yes. Nothing was proposed.")
        if input("Open this pull request? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise TugError("stopped. Nothing was proposed.")
    proposal, _ = decisions.propose(repo, github, address, args.names)
    print(teampr.render(proposal))
    return EXIT_OK


def _configure_protected(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", metavar="PATH", help="a file or a folder in this app repo. It need not exist yet. With no path, every protected pattern is listed.")


def _protected(args: argparse.Namespace) -> int:
    from tugboat import apprepo, estate, permissions, teamcopy

    repo = apprepo.find_app_repo(".")
    if teamcopy.read_state(repo) is None:
        raise TugError("this folder is not set up with Tugboat. Run `tug init` inside the app repo first.")
    compiled = permissions.read_compiled(repo)
    if compiled is None:
        raise TugError(f"no list of protected paths is compiled for this repo yet ({permissions.COMPILED}). Run `tug sync` first.")
    source = {permissions.TEAM: "from the team repo", permissions.OWN: "one of Tugboat's own files"}
    if not args.paths:
        for entry in compiled.protected:
            print(f"- {entry.pattern} ({source[entry.source]}): {entry.reason}")
        return EXIT_OK
    failed = False
    for given in args.paths:
        try:
            path = estate.repo_path(repo, given)
        except TugError as error:
            print(f"{given}: {error}")
            failed = True
            continue
        found = permissions.match(compiled, path)
        if found is None:
            print(f"{path}: not protected.")
        else:
            print(f"{path}: protected by {found.pattern} ({source[found.source]}). {found.reason} Ask the developer before changing it.")
    return EXIT_ERROR if failed else EXIT_OK


def _team_repo_of(option: str | None):  # noqa: ANN202 - the address type is imported late
    """The team repo a command works on: the one given, or the one this app repo is set up for."""
    from tugboat import apprepo, teamcopy
    from tugboat.address import parse_team_repo_address

    if option:
        return parse_team_repo_address(option)
    try:
        state = teamcopy.read_state(apprepo.find_app_repo("."))
    except TugError:
        state = None
    if state is None:
        raise TugError("this folder is not set up with Tugboat. Pass --team-repo ADDRESS.")
    return parse_team_repo_address(state["team_repo"])


def _configure_profile(parser: argparse.ArgumentParser) -> None:
    from tugboat import profile

    actions = parser.add_subparsers(dest="profile_command", metavar="<action>", required=True)
    show = actions.add_parser(
        "show",
        help="Print the project profile this machine works from",
        description="Print the project profile this machine works from: the approved one, or this machine's own proposal while it waits for its merge. Needs no network.",
    )
    criteria = actions.add_parser(
        "set-criteria",
        help="Save where the project's stories keep their acceptance criteria",
        description="Save where the project's stories keep their acceptance criteria. The change goes to the team repo as a pull request that a person merges. "
        "An agent runs this at the end of the skill `tug-criteria-source`, after the developer picked.",
    )
    criteria.add_argument("--kind", required=True, choices=[value for value, _ in profile.CRITERIA_KINDS], help="a tracker field, a headed section in the story's text, or a task list in the story's text")
    criteria.add_argument("--name", required=True, help="the field's name, or the heading's text. For a task list under no heading: body")
    criteria.add_argument("--yes", action="store_true", help="open the pull request without being asked")
    for action in (show, criteria):
        action.add_argument("--team-repo", metavar="ADDRESS", help="the team repo. Not needed inside an app repo that Tugboat was set up in.")


def _profile(args: argparse.Namespace) -> int:
    from tugboat import paths, profile, sync, teampr

    address = _team_repo_of(args.team_repo)
    if args.profile_command == "show":
        if not (paths.team_repo_clone(address) / ".git").exists():
            raise TugError(f"this machine has no copy of the team repo {address.url} yet. Run `tug sync` first.")
        working = profile.load(address)
        if working.note:
            print(working.note)
        if working.profile is None:
            print("This project has no profile yet. `tug init`, run in an app repo in a terminal, creates it.")
            return EXIT_ERROR
        if working.pending is None:
            print(f"Project profile, approved in the team repo {address.slug}:")
        else:
            print(f"Project profile, as this machine proposed it. It waits for its merge: {profile.where(working.pending)}")
        print("\n".join(profile.describe(working.profile)))
        return EXIT_OK

    report = sync.sync_team_repo(address)
    if not report.usable:
        raise TugError(f"the team repo could not be reached, and this machine has no copy of it ({report.error}). Nothing was saved.")
    working = profile.load(address)
    if working.note:
        print(working.note)
    if working.profile is None:
        raise TugError("this project has no profile yet. `tug init`, run in an app repo in a terminal, creates it. Nothing was saved.")
    proposed = profile.with_criteria_source(working.profile, kind=args.kind, name=args.name)
    words = profile.source_words(proposed["tracker"]["criteria_source"])
    needs_proposal, pending, note = profile.plan(address, working, proposed)
    if note:
        print(note)
    if not needs_proposal:
        print(f"Already saved: acceptance criteria are read from {words}.")
        if pending is not None:
            print(f"The change waits for its merge: {profile.where(pending)}")
        return EXIT_OK
    if profile.below_minimum(working.approved):
        raise TugError(f"this project needs tug {working.approved['minimum_tool_version']} or newer, and this is {installed_version()}. Update `tug` first. Nothing was saved.")

    print(f"Acceptance criteria will be read from {words}.")
    print(f"The profile changes only through a pull request in the team repo {address.slug}.")
    if pending is not None and pending.branch:
        print(f"The new pull request replaces this machine's earlier proposal. Close that one: {profile.where(pending)}")
    if not args.yes:
        if not sys.stdin.isatty():
            raise TugError("a pull request needs a person to confirm it. Run this in a terminal, or pass --yes. Nothing was saved.")
        if input("Open this pull request? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise TugError("stopped. Nothing was saved.")
    pending, proposal, failure = profile.propose(address, working, proposed, title="Set where acceptance criteria are read from", origin="Proposed by `tug profile set-criteria`.")
    if proposal is None:
        print(f"The pull request could not be made: {failure}")
        print("The choice is kept on this machine. Run the same command again to propose it.")
        return EXIT_ERROR
    print(teampr.render(proposal))
    print("Until the merge, Tugboat works from its own copy of the profile on this machine.")
    return EXIT_OK


def _team_propose(args: argparse.Namespace) -> int:
    from pathlib import Path

    from tugboat import teampr

    address = _team_repo_of(args.team_repo)

    changes: dict[str, bytes | None] = {}
    for item in args.file:
        team_path, separator, local = item.partition("=")
        if not separator or not team_path or not local:
            raise TugError(f"--file must look like TEAM_PATH=LOCAL_FILE (got '{item}')")
        try:
            changes[team_path] = Path(local).read_bytes()
        except OSError as error:
            raise TugError(f"cannot read {local}: {error.strerror or error}") from error
    for team_path in args.remove:
        changes[team_path] = None
    body = args.body
    if args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise TugError(f"cannot read {args.body_file}: {error}") from error

    if not args.yes:
        print(f"This opens a pull request in {address.slug}: {args.title}")
        for team_path, content in changes.items():
            print(f"  {'remove' if content is None else 'write '} {team_path}")
        if not sys.stdin.isatty():
            raise TugError("a pull request needs a person to confirm it. Run this in a terminal, or pass --yes. Nothing was proposed.")
        if input("Open this pull request? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise TugError("stopped. Nothing was proposed.")
    print(teampr.render(teampr.propose(address, title=args.title, body=body, changes=changes)))
    return EXIT_OK


def _configure_doctor(parser: argparse.ArgumentParser) -> None:
    from tugboat import observed

    parser.add_argument("action", nargs="?", choices=("observe",), help="`observe`: the check an agent runs with its shell tool inside a session, through the skill `tug-setup-check`")
    parser.add_argument("--surface", choices=observed.SURFACES, help="with `observe`: the agent tool the session runs in")
    parser.add_argument("--tracker", choices=observed.ANSWERS, default=observed.NONE, help="with `observe`: whether the tracker connection answered when the agent tried it")
    parser.add_argument("--github", choices=observed.ANSWERS, default=observed.NONE, help="with `observe`: whether the GitHub connection answered when the agent tried it")


def _doctor(args: argparse.Namespace) -> int:
    from tugboat import doctor

    if args.action == "observe":
        if not args.surface:
            raise TugError("`tug doctor observe` needs --surface: claude-code, copilot-cli, or copilot-vscode.")
        worked, text = doctor.observe(".", args.surface, args.tracker, args.github)
        print(text)
        return EXIT_OK if worked else EXIT_ERROR
    report = doctor.run(".")
    print(doctor.render(report))
    return EXIT_ERROR if report.failed else EXIT_OK


COMMANDS: tuple[Command, ...] = (
    Command("version", "Print the installed version", _version),
    Command("init", "Set up the app repo it is run in", _init, _configure_init),
    Command("sync", "Bring the team repo clone, this repo's copy, and the agent tool files up to date", _sync, _configure_sync),
    Command("profile", "Show the project profile, or save where acceptance criteria are kept: `tug profile show`, `set-criteria`", _profile, _configure_profile),
    Command("owner", "Name the owner of a file or folder in this app repo, from the estate map", _owner, _configure_owner),
    Command("notes", "Name the knowledge notes that cover a file or folder in this app repo", _notes, _configure_notes),
    Command("capture", "Keep, accept, drop, and propose the notes an agent drafted: `tug capture status`, `accept`, `drop`, `propose`", _capture, _configure_capture),
    Command("decision", "List and propose the decision records an agent drafted: `tug decision status`, `propose`", _decision, _configure_decision),
    Command("protected", "Say whether a file or folder in this app repo is a protected path, and why", _protected, _configure_protected),
    Command("status", "Print the session brief"),
    Command("doctor", "Check that this machine and this app repo are ready, from what is on disk", _doctor, _configure_doctor),
    Command("update", "Update tug to the newest release", _update, _configure_update),
    Command("expire", "Remove records older than six months, after confirmation"),
    Command("remove", "Take Tugboat out of this repo, or out of every repo on this machine"),
    Command("team", "Start, check, or propose a change to a team repo: `tug team new`, `check`, `propose`", _team, _configure_team),
    Command("hook", "Called by the agent tool's hooks. Not typed by a developer.", _hook, _configure_hook),
)


def _not_built(name: str) -> Handler:
    def handler(_args: argparse.Namespace) -> int:
        print(f"tug: '{name}' is not built yet in version {installed_version()}.", file=sys.stderr)
        return EXIT_NOT_BUILT

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tug",
        description="Tugboat: shared rules, knowledge, and task history for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"tug {installed_version()}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for command in COMMANDS:
        built = command.handler is not None
        summary = command.summary if built else f"{command.summary} (not built yet)"
        sub = subparsers.add_parser(command.name, help=summary, description=summary)
        sub.set_defaults(handler=command.handler or _not_built(command.name))
        if command.configure is not None:
            command.configure(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    planned = {command.name for command in COMMANDS if command.handler is None}
    if arguments and arguments[0] in planned:
        # Answer before parsing, so any arguments give the same message.
        return _not_built(arguments[0])(argparse.Namespace())
    parser = build_parser()
    args = parser.parse_args(arguments)
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_OK
    try:
        return handler(args)
    except TugError as error:
        print(f"tug: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("tug: stopped.", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:  # noqa: BLE001 - last line of defence for the developer
        if os.environ.get("TUG_DEBUG"):
            traceback.print_exc()
        print(f"tug: internal error: {type(error).__name__}: {error}", file=sys.stderr)
        print("tug: set TUG_DEBUG=1 to see the full trace.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
