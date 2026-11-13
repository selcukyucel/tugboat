"""Tool versions.

A version is three whole numbers, MAJOR.MINOR.PATCH. A release tag is the
version with a leading "v". Nothing else is accepted, so two versions can
always be compared.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from tugboat import __version__
from tugboat.errors import DataError

_VERSION = re.compile(r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$")


class Version(NamedTuple):
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self}"


def parse_version(text: object, label: str = "version") -> Version:
    if not isinstance(text, str):
        raise DataError(f"{label} must be text")
    match = _VERSION.fullmatch(text.strip())
    if not match:
        raise DataError(f"{label} must look like 1.2.3")
    return Version(*(int(part) for part in match.groups()))


def parse_tag(text: object, label: str = "release tag") -> Version:
    if not isinstance(text, str) or not text.startswith("v"):
        raise DataError(f"{label} must look like v1.2.3")
    return parse_version(text[1:], label)


def installed_version() -> Version:
    return parse_version(__version__, "installed version")
