"""Where Tugboat keeps its own files on a machine."""

from __future__ import annotations

import os
from pathlib import Path

from tugboat.address import TeamRepoAddress


def tug_home() -> Path:
    """The one folder Tugboat owns on this machine. TUG_HOME moves it, for tests."""
    override = os.environ.get("TUG_HOME")
    return Path(override) if override else Path.home() / ".tugboat"


def team_repo_clone(address: TeamRepoAddress) -> Path:
    """The fixed local path of the clone of one team repo."""
    return tug_home() / "team-repos" / address.host / address.owner / address.name


def _sync_file(address: TeamRepoAddress, suffix: str) -> Path:
    # Kept in a tree of their own, so a state file can never sit where a clone would go.
    return tug_home() / "sync-state" / address.host / address.owner / (address.name + suffix)


def sync_state_file(address: TeamRepoAddress) -> Path:
    return _sync_file(address, ".json")


def sync_lock_file(address: TeamRepoAddress) -> Path:
    return _sync_file(address, ".lock")


def pending_profile_file(address: TeamRepoAddress) -> Path:
    """A profile change this machine proposed for one team repo and that is not merged yet."""
    return tug_home() / "pending" / address.host / address.owner / (address.name + ".profile.json")
