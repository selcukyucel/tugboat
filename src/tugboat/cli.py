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


class Command(NamedTuple):
    name: str
    summary: str
    handler: Handler | None = None


def _version(_args: argparse.Namespace) -> int:
    print(installed_version())
    return EXIT_OK


COMMANDS: tuple[Command, ...] = (
    Command("version", "Print the installed version", _version),
    Command("init", "Set up the app repo it is run in"),
    Command("sync", "Pull the team repo and refresh the local files"),
    Command("status", "Print the session brief"),
    Command("doctor", "Check that this machine and repo are ready"),
    Command("update", "Update tug to the newest release"),
    Command("expire", "Remove records older than six months, after confirmation"),
    Command("remove", "Take Tugboat out of this repo, or out of every repo on this machine"),
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
