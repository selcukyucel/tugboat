"""The header at the top of a Markdown file.

Rules, notes, decision records, and agent definitions are Markdown files that
start with a small header between two `---` lines:

    ---
    id: IOS-001
    title: New unit tests use Swift Testing
    paths: ["Sources/Login/**", "Tests/LoginTests/**"]
    ---

Each header line is `key: value`. A value is plain text, or JSON when it
starts with `[`, `{`, or `"`. This is a small part of YAML, chosen so that
GitHub and the agent tools read it the same way Tugboat does. Anything outside
that part is refused, not guessed.
"""

from __future__ import annotations

import re
from typing import Any

from tugboat.errors import DataError
from tugboat.strict_json import inline_json, parse_json

MAX_HEADER_LINES = 60
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
# Plain text that YAML would read as something other than text.
_NOT_PLAIN_START = tuple("[{\"'&*!|>%@`#,]}")


def _check_plain(value: str, key: str) -> None:
    if not value:
        raise DataError(f"header field '{key}' has no value")
    reasons = (
        value.startswith(_NOT_PLAIN_START),
        value.startswith(("- ", "? ")) or value in {"-", "?"},
        ": " in value or value.endswith(":"),
        " #" in value,
    )
    if any(reasons):
        raise DataError(f"header field '{key}' must be written in double quotes")


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown file into its header fields and its body."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        raise DataError("the file must start with a '---' header")
    fields: dict[str, Any] = {}
    for number, raw in enumerate(lines[1:], start=2):
        line = raw.rstrip("\r")
        if line == "---":
            body = "\n".join(lines[number:])
            return fields, body.lstrip("\n")
        if number > MAX_HEADER_LINES or (line == "" and number == len(lines)):
            # Too long, or the file ended before the header did.
            break
        key, separator, rest = line.partition(":")
        if not separator or not _KEY.fullmatch(key):
            raise DataError(f"header line {number} must look like 'key: value'")
        if key in fields:
            raise DataError(f"header field '{key}' appears twice")
        if rest and not rest.startswith(" "):
            raise DataError(f"header line {number} needs a space after the colon")
        value = rest.strip()
        if value.startswith(("[", "{", '"')):
            try:
                fields[key] = parse_json(value)
            except DataError as error:
                raise DataError(f"header field '{key}': {error}") from error
        else:
            _check_plain(value, key)
            fields[key] = value
    raise DataError(f"the header has no closing '---' line within its first {MAX_HEADER_LINES} lines")


def render(fields: dict[str, Any], body: str) -> str:
    """Write header fields and a body as one Markdown file."""
    lines = ["---"]
    for key, value in fields.items():
        if not _KEY.fullmatch(key):
            raise DataError(f"'{key}' is not a valid header field name")
        if isinstance(value, str):
            try:
                _check_plain(value, key)
                plain = "\n" not in value and value == value.strip()
            except DataError:
                plain = False
            lines.append(f"{key}: {value}" if plain else f"{key}: {inline_json(value)}")
        else:
            lines.append(f"{key}: {inline_json(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip("\n") + "\n"
