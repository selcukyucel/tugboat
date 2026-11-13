"""The address of a team repo."""

from __future__ import annotations

import re
from typing import NamedTuple

from tugboat.errors import DataError

_HOST = r"(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)"
_PATH = r"(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?"
_FORMS = (
    ("ssh", re.compile(rf"^git@{_HOST}:{_PATH}$")),
    ("ssh", re.compile(rf"^ssh://git@{_HOST}/{_PATH}$")),
    ("https", re.compile(rf"^https://{_HOST}/{_PATH}$")),
)


class TeamRepoAddress(NamedTuple):
    host: str
    owner: str
    name: str
    url: str  # rebuilt from the checked parts, never the text as typed

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_team_repo_address(text: object) -> TeamRepoAddress:
    """Read an address as GitHub shows it under "Code": SSH or HTTPS.

    The URL handed to Git is rebuilt from the parts, so the typed text can
    carry no option, no password, and no other transport.
    """
    if not isinstance(text, str) or not text.strip():
        raise DataError("the team repo address is empty")
    value = text.strip()
    for kind, pattern in _FORMS:
        match = pattern.fullmatch(value)
        if not match:
            continue
        host, owner, name = match["host"].lower(), match["owner"], match["name"]
        if name in {".", ".."} or ".." in host:
            break
        url = f"git@{host}:{owner}/{name}.git" if kind == "ssh" else f"https://{host}/{owner}/{name}.git"
        return TeamRepoAddress(host, owner, name, url)
    raise DataError(
        "the team repo address must look like git@github.com:owner/name.git or https://github.com/owner/name.git"
    )
