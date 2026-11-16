"""The checks a record passes before it leaves the machine.

A record is written on the machine first. Publishing commits and pushes it
to the team repo, where every teammate, and every agent a teammate runs,
reads it. So each unpublished record is checked first, and a record that
fails a check is held back: it stays on the machine, and the reason is
given. Three checks:

- The schema. The file must read as a record of a kind this tool knows,
  with every field valid, at the path its own content gives.
- Secrets. No text in the record may match one of the secret patterns.
  They are the same patterns that guard a write by the agent and a
  knowledge note: the one list in `secretscan`.
- Credentials. That list covers what signs somebody in: a key, a token, a
  password in a URL, an `Authorization` or `Cookie` header. The formats do
  their part before that: a record has no field for a credential, and a
  link with a sign-in in it is refused.

Nothing is written by a check. The reason a record is held back is worked
out again each time, from the file as it is, so it cannot go out of date.

A record that is held back still counts on its own machine: the state of
the task is worked out from published and unpublished records alike. The
way out is a new record without the secret, and `discard` for the old one.
A record is never edited.

A reason names the field and the kind of secret. It never repeats the
secret.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, NamedTuple

from tugboat import recordstore, records, secretscan
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError
from tugboat.recordstore import Stored


class Held(NamedTuple):
    """A record that stays on this machine, and why."""

    path: str  # relative to the store
    reasons: tuple[str, ...]
    newer: bool = False  # a newer tool wrote it: updating `tug` checks it


class Checked(NamedTuple):
    ready: tuple[Stored, ...]  # passed every check, oldest first. Only these may be published.
    held: tuple[Held, ...]  # failed a check. They stay on the machine.
    other: tuple[str, ...] = ()  # new files in the store that are no record files. Never published.
    changed: tuple[str, ...] = ()  # published records that were edited or deleted on this machine. Never published.


def _named_values(value: object, name: str = "") -> Iterator[tuple[str, str]]:
    """A name and its value kept in two fields, as one line: a feature flag named `api_key` whose state is the key."""
    if isinstance(value, dict):
        if isinstance(value.get("name"), str) and isinstance(value.get("state"), str):
            yield name, f"{value['name']}: {value['state']}"
        for key, item in value.items():
            yield from _named_values(item, f"{name}.{key}" if name else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _named_values(item, f"{name}[{index}]")


def _kinds(findings: list[secretscan.Finding], several_lines: bool) -> str:
    if several_lines:
        return secretscan.describe(findings)
    return ", ".join(dict.fromkeys(finding.kind for finding in findings))


def findings(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Why a checked record may not be published, one reason for each field. Empty for a record that may.

    A command that writes a record can show these at once, while the
    session that made the text is still open and can write it again.
    """
    reasons = []
    seen = set()
    for name, text in (*records.text_fields(record), *_named_values(record)):
        found = secretscan.find(text)
        if found and name not in seen:
            seen.add(name)
            kinds = _kinds(found, several_lines="\n" in text)
            reasons.append(f"its field `{name}` holds what looks like a secret ({kinds})")
    return tuple(reasons)


def check_one(address: TeamRepoAddress, path: str) -> Stored | Held:
    """Check one file of the store."""
    try:
        record = recordstore.read_file(address, path)
    except records.NewerSchema as error:
        return Held(path, (str(error),), True)
    except DataError as error:
        return Held(path, (f"it is not a valid record: {error}",))
    reasons = findings(record)
    return Held(path, reasons) if reasons else Stored(path, record)


def check(address: TeamRepoAddress) -> Checked:
    """Check every unpublished record of the store. Reads only, and needs no network."""
    found = recordstore.changes(address)
    ready: list[Stored] = []
    held: list[Held] = []
    for path in found.new:
        result = check_one(address, path)
        if isinstance(result, Stored):
            ready.append(result)
        else:
            held.append(result)
    ready.sort(key=lambda stored: (datetime.fromisoformat(stored.record["at"].replace("Z", "+00:00")), stored.record["id"]))
    return Checked(tuple(ready), tuple(held), found.other, found.changed)


def discard(address: TeamRepoAddress, path: str) -> Held:
    """Delete one unpublished record that was held back, and return what it was held for.

    This is the way out for a record with a secret in it: a record is never
    edited, so a new one is written and the old one is deleted. A record
    that passes the checks is not deleted here, and neither is one that
    was published, or one a newer tool wrote.
    """
    if path not in recordstore.unpublished(address):
        raise TugError(f"{path} is not an unpublished record of {address.slug}. Nothing was deleted.")
    result = check_one(address, path)
    if isinstance(result, Stored):
        raise TugError(f"{path} passes the checks, so it will be published. Only a record that was held back can be discarded.")
    if result.newer:
        raise TugError(f"{path} was written by a newer tug. Update `tug` to check it. Nothing was deleted.")
    file = recordstore.folder(address) / path
    if file.is_symlink() or not file.is_file():
        raise TugError(f"{path} is not a plain file. It was left as it is.")
    try:
        file.unlink()
    except OSError as error:
        raise TugError(f"{path} could not be deleted ({error.strerror or error})") from error
    return result
