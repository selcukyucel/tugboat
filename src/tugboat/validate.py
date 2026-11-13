"""Small checks for untrusted data.

Each function returns the checked value or raises DataError with a message
that names the field. Schemas for profiles, rules, and records are built
from these in later tasks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from tugboat.errors import DataError

_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def require_object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DataError(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise DataError(f"{label} field names must be strings")
    return value


def require_exact_fields(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    """Refuse a missing field and refuse a field nobody defined."""
    fields = set(value)
    missing = required - fields
    unknown = fields - required - optional
    if missing:
        raise DataError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise DataError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def require_text(value: object, label: str, maximum: int) -> str:
    """Non-empty text within a length limit, with no control characters."""
    if not isinstance(value, str):
        raise DataError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise DataError(f"{label} cannot be empty")
    if len(normalized) > maximum:
        raise DataError(f"{label} cannot exceed {maximum} characters")
    if _CONTROL.search(normalized):
        raise DataError(f"{label} cannot contain control characters")
    return normalized


def require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise DataError(f"{label} must be true or false")
    return value


def require_int(value: object, label: str, *, minimum: int, maximum: int) -> int:
    # bool is a subclass of int in Python, so it is refused by name.
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError(f"{label} must be a whole number")
    if not minimum <= value <= maximum:
        raise DataError(f"{label} must be between {minimum} and {maximum}")
    return value


def require_choice(value: object, label: str, choices: frozenset[str]) -> str:
    text = require_text(value, label, 100)
    if text not in choices:
        raise DataError(f"{label} must be one of: {', '.join(sorted(choices))}")
    return text


def validate_slug(value: object, label: str, maximum: int = 100) -> str:
    slug = require_text(value, label, maximum)
    if not _SLUG.fullmatch(slug):
        raise DataError(f"{label} must be lowercase words separated by '.', '_', or '-'")
    return slug


def validate_relative_path(value: object, label: str) -> str:
    """A path that stays inside the folder it is relative to."""
    text = require_text(value, label, 2000)
    if "\\" in text:
        raise DataError(f"{label} must use forward slashes")
    # The raw text is split by hand. A path library would quietly drop "." parts
    # and doubled slashes, and this check must see them.
    if any(part in {"", ".", ".."} for part in text.split("/")):
        raise DataError(f"{label} must be a relative path with no empty, '.' or '..' parts")
    return text


def canonical_timestamp(value: object, label: str) -> str:
    """An ISO 8601 time with a timezone, returned in UTC with a trailing Z."""
    text = require_text(value, label, 40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise DataError(f"{label} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
