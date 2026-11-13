"""Strict JSON reading and writing.

Everything Tugboat reads from a team repo, a record, or another program is
untrusted data. This module refuses input that a lenient parser would accept
silently: duplicate fields, NaN and Infinity, very deep nesting, and very
large files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tugboat.errors import DataError

MAX_DEPTH = 40
MAX_BYTES = 1024 * 1024


def parse_json(text: str) -> Any:
    """Parse JSON text. Raise DataError for anything ambiguous or unsafe."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DataError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def nonfinite(value: str) -> Any:
        raise DataError(f"non-finite JSON value: {value}")

    try:
        decoded = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
    except DataError:
        raise
    except (ValueError, RecursionError) as error:
        raise DataError(f"invalid JSON: {error}") from error

    pending = [(decoded, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, dict):
            children = list(value.values())
        elif isinstance(value, list):
            children = value
        else:
            continue
        if depth > MAX_DEPTH:
            raise DataError(f"JSON nesting exceeds {MAX_DEPTH} levels")
        pending.extend((child, depth + 1) for child in children)
    return decoded


def read_json_file(path: str | os.PathLike[str], *, max_bytes: int = MAX_BYTES) -> Any:
    """Read and parse one JSON file. The size is checked before parsing."""
    file = Path(path)
    try:
        with file.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as error:
        raise DataError(f"cannot read {file}: {error.strerror or error}") from error
    if len(raw) > max_bytes:
        raise DataError(f"{file} is larger than {max_bytes} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DataError(f"{file} is not valid UTF-8") from error
    try:
        return parse_json(text)
    except DataError as error:
        raise DataError(f"{file}: {error}") from error


def canonical_json(value: Any) -> str:
    """One stable text form for a value: sorted keys, no spaces, ASCII only."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise DataError("value must contain only finite JSON data") from error


def inline_json(value: Any) -> str:
    """One line that people read: keeps the given order and non-ASCII letters."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise DataError("value must contain only finite JSON data") from error


def pretty_json(value: Any) -> str:
    """The form written to files that people read: sorted keys, two spaces."""
    try:
        return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    except (TypeError, ValueError) as error:
        raise DataError("value must contain only finite JSON data") from error
