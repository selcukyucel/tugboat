"""`tug init`: set up the app repo it is run in.

The order matters. Everything that can stop init happens before anything is
written into the app repo:

1. Detect the repo, and have the developer confirm it.
2. Get the team repo address and bring its clone up to date.
3. Read the project profile. The first init for a project asks the project
   questions. A later init asks nothing about the project.
4. Propose the new or changed profile as a pull request in the team repo.
5. Write the copy of the team content and the agent tool files into the repo.

Init creates nothing in GitHub except that pull request, and merges nothing.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from collections.abc import Sequence
from typing import Any, NamedTuple

from tugboat import detect, hostfiles, paths, profile, prompts, sync, teamcopy, teampr
from tugboat.address import TeamRepoAddress, parse_team_repo_address
from tugboat.errors import EXIT_ERROR, EXIT_OK, DataError, TugError
from tugboat.gitrun import git
from tugboat.validate import validate_slug
from tugboat.version import installed_version

UNCHANGED = "Nothing was changed in this repo."


class Options(NamedTuple):
    yes: bool = False
    platform: str | None = None
    team_repo: str | None = None
    project: str | None = None
    tracker: str | None = None
    ci: Sequence[str] | None = None
    surfaces: Sequence[str] | None = None
    approval: str | None = None
    change: bool = False
    propose_again: bool = False


def _confirm(options: Options, question: str, without_terminal: str) -> None:
    if options.yes:
        return
    if not prompts.interactive():
        raise TugError(f"{without_terminal} Run it in a terminal, or pass --yes. {UNCHANGED}")
    if not prompts.confirm(question):
        raise TugError(f"stopped. {UNCHANGED}")


def known_team_repos() -> list[TeamRepoAddress]:
    """The team repos this machine already has a clone of."""
    found = []
    for clone in sorted((paths.tug_home() / "team-repos").glob("*/*/*")):
        if not (clone / ".git").exists():
            continue
        with contextlib.suppress(TugError):
            address = parse_team_repo_address(git("config", "--get", "remote.origin.url", cwd=clone))
            if paths.team_repo_clone(address) == clone:
                found.append(address)
    return found


def _team_repo_address(found: detect.Detection, options: Options) -> TeamRepoAddress:
    state = teamcopy.read_state(found.root)
    recorded = state["team_repo"] if state else None
    if options.team_repo:
        address = parse_team_repo_address(options.team_repo)
        if recorded and recorded != address.url:
            raise TugError(f"this repo is already set up for the team repo {recorded}. It was left as it is.")
    elif recorded:
        try:
            address = parse_team_repo_address(recorded)
        except DataError as error:
            raise TugError(f"the team repo address saved in this repo is not valid: {recorded}") from error
    else:
        if options.yes or not prompts.interactive():
            raise TugError(f"init needs the address of the team repo. Pass --team-repo ADDRESS. {UNCHANGED}")
        known = known_team_repos()
        print()
        print("The team repo holds the project's rules, knowledge, and profile. A person creates it in GitHub.")
        answer = prompts.text(
            "Team repo address, as GitHub shows it under 'Code'",
            default=known[0].url if len(known) == 1 else None,
            check=parse_team_repo_address,
        )
        address = parse_team_repo_address(answer)

    if address.host != found.host:
        raise TugError(f"the team repo is on {address.host} and this repo is on {found.host}. Both must be on the same GitHub. {UNCHANGED}")
    if address.slug.lower() == found.github.lower():
        raise TugError(f"{address.slug} is this repo itself. The team repo is a separate repo. {UNCHANGED}")
    return address


def _forget(address: TeamRepoAddress) -> None:
    """Remove what a sync left on this machine for an address that turned out to be no team repo."""
    for file in (paths.sync_state_file(address), paths.sync_lock_file(address)):
        with contextlib.suppress(OSError):
            file.unlink()


def _sync_team_repo(address: TeamRepoAddress) -> sync.SyncReport:
    report = sync.sync_team_repo(address)
    if not report.usable:
        _forget(address)
        raise TugError(
            f"the team repo {address.url} is missing or cannot be reached ({report.error}). "
            f"Tugboat creates no repository: a person creates the team repo in GitHub, and `tug team new` writes its first files. {UNCHANGED}"
        )
    if not report.ok:
        raise TugError(f"the team repo could not be brought up to date, and init needs its newest state ({report.error}). {UNCHANGED}")
    if not profile.is_team_repo(report.clone):
        if report.cloned:
            # Do not keep a clone of a repo that turned out to be something else.
            shutil.rmtree(report.clone, ignore_errors=True)
            _forget(address)
        raise TugError(
            f"{address.url} is not a Tugboat team repo: its `main` has no tugboat.json. "
            f"`tug team new` writes the starter layout for a new team repo. {UNCHANGED}"
        )
    return report


def default_project_name(address: TeamRepoAddress) -> str:
    """A project name from the team repo's name: `Shop-team-repo` gives `shop`."""
    slug = re.sub(r"[^a-z0-9]+", "-", address.name.lower()).strip("-")
    for ending in ("-team-repo", "-team", "-tugboat"):
        if slug.endswith(ending) and len(slug) > len(ending):
            slug = slug[: -len(ending)]
            break
    try:
        return validate_slug(slug, "project", 60)
    except DataError:
        return "project"


def _first_profile(address: TeamRepoAddress, options: Options) -> dict[str, Any]:
    """The first init for a project: ask the project questions, or take them from the options."""
    if options.yes or not prompts.interactive():
        missing = [name for name, value in (("--tracker", options.tracker), ("--approval", options.approval)) if value is None]
        if missing:
            raise TugError(
                f"this is the first init for the project, and its questions have no answer yet. "
                f"Run `tug init` in a terminal without --yes, or pass {' and '.join(missing)}. {UNCHANGED}"
            )
        project = options.project or default_project_name(address)
        tracker, approval = options.tracker, options.approval
        ci = options.ci if options.ci is not None else []
        surfaces = options.surfaces if options.surfaces is not None else [value for value, _ in profile.SURFACES]
    else:
        print("This is the first init for this project. A few questions follow. The answers are saved in the team repo,")
        print("so nobody on the project is asked again. Each one can be changed later with `tug init --change`.")
        project = options.project or prompts.text(
            "\nProject name, in lowercase", default=default_project_name(address), check=lambda value: validate_slug(value, "the project name", 60)
        )
        print()
        tracker = options.tracker or prompts.one("Where do the stories come from?", profile.TRACKERS)
        print()
        ci = options.ci if options.ci is not None else prompts.many("Which optional CI checks does the project use?", profile.CI_FEATURES, default=[])
        print()
        surfaces = (
            options.surfaces
            if options.surfaces is not None
            else prompts.many("Which agent tools does the team use?", profile.SURFACES, default=[value for value, _ in profile.SURFACES], minimum=1)
        )
        print()
        approval = options.approval or prompts.one("Who approves a change to the team repo?", profile.APPROVAL_MODES)
    return profile.new_profile(project=validate_slug(project, "the project name", 60), tracker=tracker, ci_features=ci, surfaces=surfaces, approval_mode=approval)


def _changed_profile(current: dict[str, Any], options: Options) -> dict[str, Any]:
    """A later init that changes a choice, by option or by being asked again."""
    if options.project is not None and options.project != current["project"]:
        raise TugError(f"the project is already named '{current['project']}'. The name is set at the first init. {UNCHANGED}")
    tracker, ci, surfaces, approval = options.tracker, options.ci, options.surfaces, options.approval
    if options.change and not options.yes:
        if not prompts.interactive():
            raise TugError(f"--change asks questions, so it needs a terminal. Without one, pass the choice as an option, such as --tracker. {UNCHANGED}")
        print()
        print("Press Enter to keep a choice as it is.")
        if tracker is None:
            tracker = prompts.one("Where do the stories come from?", profile.TRACKERS, default=current["tracker"]["kind"])
            print()
        if ci is None:
            ci = prompts.many("Which optional CI checks does the project use?", profile.CI_FEATURES, default=current["ci_features"])
            print()
        if surfaces is None:
            surfaces = prompts.many("Which agent tools does the team use?", profile.SURFACES, default=current["surfaces"], minimum=1)
            print()
        if approval is None:
            approval = prompts.one("Who approves a change to the team repo?", profile.APPROVAL_MODES, default=current["approval_mode"])
    return profile.with_choices(current, tracker=tracker, ci_features=ci, surfaces=surfaces, approval_mode=approval)


def _title(approved: dict[str, Any] | None, proposed: dict[str, Any], lines: list[str]) -> str:
    if approved is None:
        return f"Add the project profile for {proposed['project']}"
    added = [line for line in lines if line.startswith("App repo added: ")]
    if len(lines) == 1 and added:
        return f"Add {added[0][len('App repo added: ') :].split(' ')[0]} to the project profile"
    return "Change the project profile"


def run(start: str | os.PathLike[str], options: Options) -> int:
    found = detect.detect(start, platform=options.platform)
    print("Tugboat found this:")
    print(detect.render(found))
    _confirm(options, "Is this right?", "init needs a person to confirm what it found.")

    address = _team_repo_address(found, options)
    print()
    print(sync.render(_sync_team_repo(address)))

    working = profile.load(address)
    if working.note:
        print(working.note)
    current = working.profile
    print()
    if current is None:
        proposed = _first_profile(address, options)
    else:
        if working.pending is None:
            print("Project profile, approved in the team repo:")
        else:
            print(f"Project profile, as this machine proposed it. It waits for its merge: {profile.where(working.pending)}")
            print("If that pull request was closed without a merge, run `tug init --propose-again`.")
        print("\n".join(f"  {line}" for line in profile.describe(current)))
        wants_change = options.change or any(value is not None for value in (options.project, options.tracker, options.ci, options.surfaces, options.approval))
        proposed = _changed_profile(current, options) if wants_change else current
    proposed = profile.with_repo(proposed, github=found.github, default_branch=found.default_branch, platform=found.platform)

    pending = working.pending
    if proposed == working.approved:
        needs_proposal = False
        if pending is not None:
            # The choices were changed back to what is approved.
            profile.clear_pending(address)
            if pending.branch:
                print(f"The profile is the approved one again. Close the earlier proposal: {profile.where(pending)}")
            pending = None
    else:
        needs_proposal = pending is None or pending.branch is None or proposed != pending.profile or options.propose_again

    if needs_proposal and profile.below_minimum(working.approved):
        print()
        print(
            f"Note: this project needs tug {working.approved['minimum_tool_version']} or newer, and this is {installed_version()}. "
            "The profile change was not proposed. Update `tug`, then run `tug init` again."
        )
        needs_proposal, proposed = False, current

    failure: str | None = None
    if needs_proposal:
        lines = profile.changes(working.approved, proposed)
        print()
        print(f"The profile changes only through a pull request in the team repo {address.slug}. Tugboat proposes this:")
        print("\n".join(f"  {line}" for line in lines))
        if pending is not None and pending.branch:
            print(f"It replaces this machine's earlier proposal. Close that one: {profile.where(pending)}")
        _confirm(options, "Open this pull request?", "a pull request needs a person to confirm it.")
        body = f"Proposed by `tug init`, run in {found.github}.\n\n" + "\n".join(f"- {line}" for line in lines)
        proposal = None
        try:
            proposal = teampr.propose(address, title=_title(working.approved, proposed, lines), body=body, changes={profile.FILE: profile.to_bytes(proposed)}, name="profile")
        except TugError as error:
            failure = str(error)
        pending = profile.Pending(
            base=working.approved,
            profile=proposed,
            branch=proposal.branch if proposal else None,
            pull_request=proposal.pull_request if proposal else None,
            open_link=proposal.open_link if proposal else None,
        )
        profile.write_pending(address, pending)
        print()
        print(teampr.render(proposal) if proposal else f"The pull request could not be made: {failure}")

    print()
    print(teamcopy.render(teamcopy.refresh_copy(found.root, address), found.root))
    print(hostfiles.render(hostfiles.apply(found.root, address)))

    print()
    print(f"This repo is set up for the project '{proposed['project']}'.")
    if failure:
        print("The profile is not proposed yet. Tugboat works from its own copy on this machine. Run `tug init` again to propose it.")
    elif pending is not None:
        print("Until a person merges the profile pull request, Tugboat works from its own copy of the profile on this machine.")
    if address.owner.lower() != found.github.split("/")[0].lower():
        print(f"Note: the team repo belongs to {address.owner} and this repo to {found.github.split('/')[0]}. Tugboat expects both under one organisation or account.")
    if proposed["ci_features"]:
        print(f"Note: the CI checks ({profile.names(proposed['ci_features'], profile.CI_FEATURES)}) are saved as a choice. Their workflows are not built yet.")
    if proposed["tracker"]["criteria_source"] is None:
        print("Note: where acceptance criteria are kept is not chosen yet. That step runs in an agent session and is not built yet.")
    print("Next: start a new agent session in this repo. It begins with a Tugboat sync report.")
    return EXIT_ERROR if failure else EXIT_OK
