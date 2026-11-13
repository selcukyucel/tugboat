"""Questions asked in a terminal.

Every question can also be answered by an option, so init runs without a
terminal too. A question that gets no usable answer after a few tries stops
the command. It never guesses.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Sequence

from tugboat.errors import DataError, TugError

MAX_TRIES = 3
Options = Sequence[tuple[str, str]]  # (value, the words a person reads)


def interactive() -> bool:
    return sys.stdin.isatty()


def _read(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError as error:
        raise TugError("stopped: the question got no answer.") from error


def _gave_up(question: str) -> TugError:
    return TugError(f"stopped: no usable answer to '{question}' after {MAX_TRIES} tries.")


def confirm(question: str) -> bool:
    return _read(f"{question} [y/N] ").lower() in {"y", "yes"}


def text(question: str, *, default: str | None = None, check: Callable[[str], object] | None = None) -> str:
    """Ask for one line. `check` raises DataError for an answer that will not do."""
    for _ in range(MAX_TRIES):
        answer = _read(f"{question} [{default}]: " if default else f"{question}: ") or (default or "")
        if not answer:
            print("An answer is needed.")
            continue
        try:
            if check is not None:
                check(answer)
        except DataError as error:
            print(f"That will not do: {error}.")
            continue
        return answer
    raise _gave_up(question)


def _show(question: str, options: Options) -> None:
    print(question)
    for number, (_, label) in enumerate(options, start=1):
        print(f"  {number}. {label}")


def one(question: str, options: Options, *, default: str | None = None) -> str:
    """Ask for exactly one of the options, by its number."""
    _show(question, options)
    values = [value for value, _ in options]
    hint = f"Type a number from 1 to {len(options)}"
    if default is not None:
        hint += f", or press Enter for {values.index(default) + 1}"
    for _ in range(MAX_TRIES):
        answer = _read(f"{hint}: ")
        if not answer and default is not None:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return values[int(answer) - 1]
        if answer in values:
            return answer
        print("That is not one of the choices.")
    raise _gave_up(question)


def many(question: str, options: Options, *, default: Sequence[str], minimum: int = 0) -> list[str]:
    """Ask for any number of the options, by their numbers. "none" and "all" are understood."""
    _show(question, options)
    values = [value for value, _ in options]
    labels = dict(options)
    usual = ", ".join(labels[value] for value in default) or "none"
    hint = f"Type the numbers, separated by commas{'' if minimum else ', or none'}. Press Enter for: {usual}"
    for _ in range(MAX_TRIES):
        answer = _read(f"{hint}: ").lower()
        if not answer:
            return list(default)
        if answer == "all":
            return values
        parts = [] if answer == "none" else [part for part in re.split(r"[,\s]+", answer) if part]
        if all(part.isdigit() and 1 <= int(part) <= len(options) for part in parts) and len(parts) >= minimum:
            chosen = {values[int(part) - 1] for part in parts}
            return [value for value in values if value in chosen]
        print("Choose at least one." if len(parts) < minimum else "Those are not all numbers from the list.")
    raise _gave_up(question)
