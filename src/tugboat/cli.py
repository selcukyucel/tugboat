"""The `tug` command.

Each subcommand is one entry in COMMANDS. A command that is planned and not
written yet says so and exits with its own code. It never pretends to work.
"""

from __future__ import annotations

import argparse
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


def _team(args: argparse.Namespace) -> int:
    # Imported here so that `tug version` and `tug hook` start without loading it.
    from tugboat import team_repo

    if args.team_command == "new":
        written = team_repo.new_team_repo(args.folder)
        for path in written:
            print(f"wrote {path}")
        print(f"Wrote {len(written)} files to {args.folder}. Review them, then commit and push them yourself.")
        return EXIT_OK

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
        "Needed until `tug init` can record it for an app repo.",
    )


def _sync(args: argparse.Namespace) -> int:
    from tugboat import sync
    from tugboat.address import parse_team_repo_address

    if not args.team_repo:
        raise TugError("this folder is not set up yet, and `tug init` is not built. Until then, pass --team-repo ADDRESS.")
    report = sync.sync_team_repo(parse_team_repo_address(args.team_repo))
    print(sync.render(report))
    # A sync that did not work still lets work go on, as long as there is a clone to work from.
    return EXIT_OK if report.usable else EXIT_ERROR


COMMANDS: tuple[Command, ...] = (
    Command("version", "Print the installed version", _version),
    Command("init", "Set up the app repo it is run in"),
    Command("sync", "Bring the local clone of the team repo up to date", _sync, _configure_sync),
    Command("status", "Print the session brief"),
    Command("doctor", "Check that this machine and repo are ready"),
    Command("update", "Update tug to the newest release"),
    Command("expire", "Remove records older than six months, after confirmation"),
    Command("remove", "Take Tugboat out of this repo, or out of every repo on this machine"),
    Command("team", "Start or check a team repo: `tug team new`, `tug team check`", _team, _configure_team),
    Command("hook", "Called by the agent tool's hooks. Not typed by a developer."),
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
