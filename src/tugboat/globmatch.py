"""Match a path in a repo against a path pattern from the team repo.

The pattern language is small on purpose, and `validate_glob` checks it:

- `*` matches any run of characters inside one path part.
- `?` matches one character inside one path part.
- `**`, as a whole part, matches any number of parts, also none.
- Everything else matches itself. Letter case counts.

A pattern covers what it matches and everything beneath it. So `Sources/Auth`
covers the folder and each file in it, the same as `Sources/Auth/**`.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=2048)
def _part(pattern: str) -> re.Pattern[str]:
    pieces = []
    for character in pattern:
        pieces.append("[^/]*" if character == "*" else "[^/]" if character == "?" else re.escape(character))
    return re.compile("".join(pieces))


def _match(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return True  # what is left of the path lies beneath the match
    if pattern[0] == "**":
        return any(_match(pattern[1:], path[skip:]) for skip in range(len(path) + 1))
    if not path:
        return False
    return _part(pattern[0]).fullmatch(path[0]) is not None and _match(pattern[1:], path[1:])


def matches(pattern: str, path: str) -> bool:
    """True when the pattern covers the path. Both use forward slashes and are relative to the repo top."""
    return _match(tuple(pattern.split("/")), tuple(path.split("/")))


def _reaches(pattern: tuple[str, ...], folder: tuple[str, ...]) -> bool:
    if not folder or not pattern:
        return True  # the pattern goes on beneath the folder, or it covers the folder
    if pattern[0] == "**":
        return True  # `**` takes the rest of the folder, and what follows it can lie beneath
    return _part(pattern[0]).fullmatch(folder[0]) is not None and _reaches(pattern[1:], folder[1:])


def reaches(pattern: str, folder: str) -> bool:
    """True when the pattern covers the folder, or can match something inside it.

    This is the question for a folder: `Sources/Auth/Tokens/**` does not cover
    the folder `Sources/Auth`, and it does cover files inside it.
    """
    return _reaches(tuple(pattern.split("/")), tuple(folder.split("/")))


def specificity(pattern: str) -> tuple[int, int]:
    """How narrow a pattern is: the parts before its first wildcard, then all its parts. More is narrower."""
    parts = pattern.split("/")
    literal = 0
    for part in parts:
        if "*" in part or "?" in part:
            break
        literal += 1
    return literal, len(parts)
