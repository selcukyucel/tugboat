"""Signed commits and verified identity.

A task record must prove which GitHub account wrote it. Tugboat makes no
key and stores no private key. It uses Git's own commit signing, with an
SSH key the developer already has:

- The developer adds the key to their GitHub account as a signing key.
  GitHub then shows their commits as verified.
- The team repo lists the team's public signing keys in one file on
  `main`, named `signers`, each beside the GitHub account it belongs to.
  The file changes only through a pull request, like any other team
  content. It is in the format Git itself reads for SSH signatures, so
  Git checks a signature against it with no network.
- A person turns on "require signed commits" in GitHub for the team
  repo's `main` and `records` branches.

So who a machine is, is not typed in anywhere. It is the account that the
team repo's `signers` file gives for the machine's signing key.

The key is set for Git inside Tugboat's own clone of the team repo only.
The developer's other repos keep whatever signing they have.

SSH keys only. A developer who signs other work with GPG keeps doing so:
the setting here does not reach that work.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from tugboat import paths, sync
from tugboat.address import TeamRepoAddress
from tugboat.errors import DataError, TugError
from tugboat.gitrun import GitError, git, run_process
from tugboat.strict_json import pretty_json, read_json_file

SIGNERS_FILE = "signers"
BRANCHES = (sync.BRANCH, "records")  # the branches that must require signed commits
MAX_SIGNERS_BYTES = 256 * 1024
MAX_SIGNERS = 500
KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "sk-ssh-ed25519@openssh.com",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ecdsa-sha2-nistp256@openssh.com",
        "ssh-rsa",
    }
)
# A key of these types signs only after the person touches the device.
TOUCH_KEY_TYPES = frozenset({"sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"})
CHECK_SCHEMA = "tug.signing-check/1"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # Git knows this tree without its being stored
GH_TIMEOUT = 30.0
SIGN_TIMEOUT = 120.0  # a person may have to touch a key or unlock it

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_OPTION = re.compile(r'^(?:namespaces="git"|valid-(?:after|before)="[0-9]{8}(?:[0-9]{4}(?:[0-9]{2})?)?Z?")$')
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

STARTER_TEXT = """# The team's public signing keys, one line for each key.
#
#   <GitHub account> namespaces="git" <key type> <public key>
#
# Tugboat checks the signature of every task record against this file, so that a
# record proves which account wrote it. Git reads the same file: its format is
# the one of Git's "allowed signers" for SSH signatures.
#
# A key gets in or out by pull request. `tug signing propose` opens the pull
# request for the key of the machine it is run on. To retire a key and keep the
# records it signed, add valid-before="YYYYMMDD" to its line; do not delete it.
"""


class PublicKey(NamedTuple):
    key_type: str
    key: str  # base64, as in a .pub file

    @property
    def text(self) -> str:
        return f"{self.key_type} {self.key}"


class Signer(NamedTuple):
    login: str  # the GitHub account
    key: PublicKey
    options: tuple[str, ...]


class Setup(NamedTuple):
    """What Git, inside the clone of the team repo, would sign with."""

    key: PublicKey | None
    problem: str | None  # why there is no usable key
    source: str | None  # the value of user.signingkey, for a message


# -------------------------------------------------------------- the formats


def _public_key(key_type: str, key: str, label: str) -> PublicKey:
    if key_type not in KEY_TYPES:
        raise DataError(f"{label} has the key type '{key_type[:60]}'. Use one of: {', '.join(sorted(KEY_TYPES))}")
    if not _BASE64.fullmatch(key) or len(key) > 4096:
        raise DataError(f"{label} has a key that is not a public SSH key")
    try:
        blob = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError) as error:
        raise DataError(f"{label} has a key that is not a public SSH key") from error
    # A public key starts with its own type, behind a length of four bytes.
    named = key_type.encode("ascii")
    if blob[:4] != len(named).to_bytes(4, "big") or blob[4 : 4 + len(named)] != named:
        raise DataError(f"{label} has a key that does not match its key type")
    return PublicKey(key_type, key)


def parse_public_key(text: str, label: str = "the public key") -> PublicKey:
    """One public key as a `.pub` file holds it: the type, the key, and perhaps a comment. The comment is dropped."""
    if "PRIVATE KEY" in text:
        raise DataError(f"{label} is a private key. Give the public key, the file that ends in .pub")
    lines = [line for line in text.splitlines() if line.strip()]
    parts = lines[0].split() if len(lines) == 1 else []
    if len(parts) < 2:
        raise DataError(f"{label} is not one public SSH key")
    return _public_key(parts[0], parts[1], label)


def parse_signers(text: str) -> list[Signer]:
    """The team's signing keys. Raises DataError with the line that is wrong."""
    if len(text.encode("utf-8")) > MAX_SIGNERS_BYTES:
        raise DataError(f"signers is larger than {MAX_SIGNERS_BYTES // 1024} KB")
    signers: list[Signer] = []
    owner_of: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = f"signers line {number}"
        parts = line.split()
        login = parts[0]
        if not _LOGIN.fullmatch(login):
            # A pattern such as "*" or a list would let one key sign for many accounts.
            raise DataError(f"{label} must start with one GitHub account name")
        rest = parts[1:]
        options: tuple[str, ...] = ()
        if rest and rest[0] not in KEY_TYPES:
            options = tuple(rest[0].split(","))
            rest = rest[1:]
            for option in options:
                if not _OPTION.fullmatch(option):
                    raise DataError(f'{label} has the option {option[:40]!r}. Only namespaces="git", valid-after, and valid-before are allowed')
        if len(rest) < 2:
            raise DataError(f"{label} must be: account, key type, key")
        key = _public_key(rest[0], rest[1], label)
        if owner_of.setdefault(key.key, login).lower() != login.lower():
            raise DataError(f"{label} lists a key that line above gives to {owner_of[key.key]}. One key belongs to one account")
        signers.append(Signer(login, key, options))
    if len(signers) > MAX_SIGNERS:
        raise DataError(f"signers lists more than {MAX_SIGNERS} keys")
    return signers


def signer_line(login: str, key: PublicKey) -> str:
    """The line for one key. The key works for Git signatures only, and the comment of the key file is left out: it often names a person or a machine."""
    return f'{login} namespaces="git" {key.text}'


def with_signer(text: str, login: str, key: PublicKey) -> str:
    """The signers file with one more key. Raises TugError when the key is there already."""
    if not _LOGIN.fullmatch(login):
        raise TugError(f"'{login}' is not a GitHub account name")
    for signer in parse_signers(text):
        if signer.key.key == key.key:
            raise TugError(f"this key is in the team repo's {SIGNERS_FILE} already, for the account {signer.login}")
    body = text if text.strip() else STARTER_TEXT
    return body.rstrip("\n") + "\n" + signer_line(login, key) + "\n"


def login_of(signers: list[Signer], key: PublicKey) -> str | None:
    """The GitHub account that the team repo gives for a key."""
    return next((signer.login for signer in signers if signer.key.key == key.key), None)


# ------------------------------------------------------------- this machine


def _config(clone: Path, name: str) -> str | None:
    try:
        return git("config", "--get", name, cwd=clone) or None
    except GitError:
        return None


def machine_setup(clone: Path) -> Setup:
    """The signing key that Git would use inside the clone of the team repo. Reads no private key."""
    chosen = _config(clone, "gpg.format")
    value = _config(clone, "user.signingkey")
    if chosen != "ssh":
        found = f"Git signs with '{chosen}' there" if chosen else "no signing is set there"
        return Setup(None, f"Tugboat uses SSH signing keys, and {found}", value)
    if not value:
        return Setup(None, "SSH signing is chosen and no key is named", None)
    try:
        if value.startswith("key::"):
            return Setup(parse_public_key(value[5:]), None, "a key written into the Git setting")
        if value.split()[0] in KEY_TYPES:
            return Setup(parse_public_key(value), None, "a key written into the Git setting")
        path = Path(os.path.expanduser(value))
        public = path if path.suffix == ".pub" else Path(f"{path}.pub")
        return Setup(parse_public_key(public.read_text(encoding="utf-8"), os.fspath(public)), None, os.fspath(path))
    except (OSError, UnicodeDecodeError, IndexError) as error:
        return Setup(None, f"the public key of {value} could not be read ({getattr(error, 'strerror', None) or type(error).__name__})", value)
    except DataError as error:
        return Setup(None, str(error), value)


def use_key(clone: Path, given: str) -> PublicKey:
    """Tell Git, inside the clone of the team repo only, to sign with this SSH key. Reads the public key and never the private one."""
    path = Path(os.path.expanduser(given)).resolve()
    public = path if path.suffix == ".pub" else Path(f"{path}.pub")
    if not public.is_file():
        raise TugError(f"{public} is not a file. Give the public key of an SSH key you have, such as ~/.ssh/id_ed25519.pub.")
    try:
        key = parse_public_key(public.read_text(encoding="utf-8"), os.fspath(public))
    except (OSError, UnicodeDecodeError) as error:
        raise TugError(f"{public} could not be read") from error
    try:
        git("config", "--local", "gpg.format", "ssh", cwd=clone)
        git("config", "--local", "user.signingkey", os.fspath(path), cwd=clone)
    except GitError as error:
        raise TugError(f"the signing key could not be set in {clone} ({error.reason})") from error
    return key


def team_signers(clone: Path) -> list[Signer] | None:
    """The signing keys on the team repo's approved branch, as sync fetched them. None when the team repo has no such file."""
    try:
        text = git("show", f"refs/remotes/origin/{sync.BRANCH}:{SIGNERS_FILE}", cwd=clone)
    except GitError:
        return None
    return parse_signers(text)


def signers_text(clone: Path) -> str:
    try:
        return git("show", f"refs/remotes/origin/{sync.BRANCH}:{SIGNERS_FILE}", cwd=clone) + "\n"
    except GitError:
        return ""


def test_signature(clone: Path) -> tuple[bool, str]:
    """Sign a commit that is on no branch, and check it against the team's keys, as publishing and fetching will.

    Returns whether it worked and one sentence. A person may be asked to
    unlock or touch the key. The commit is not kept on any branch and Git
    removes it in time.
    """
    try:
        commit = git("-c", "user.useConfigOnly=true", "commit-tree", "-S", EMPTY_TREE, "-m", "Tugboat signing test", cwd=clone, timeout=SIGN_TIMEOUT)
    except GitError as error:
        return False, f"Git could not sign with the key: {error.reason}"
    holder = Path(tempfile.mkdtemp(prefix="tug-signers-"))
    try:
        allowed = holder / "allowed_signers"
        allowed.write_text(signers_text(clone), encoding="utf-8")
        state, _, signer = git("-c", f"gpg.ssh.allowedSignersFile={allowed}", "log", "-1", "--format=%G?%n%GS", commit, cwd=clone).partition("\n")
    except GitError as error:
        return False, f"Git could not check the signature: {error.reason}"
    finally:
        shutil.rmtree(holder, ignore_errors=True)
    if state.strip() == "G" and signer.strip():
        return True, f"Git signed a test commit, and the team repo's {SIGNERS_FILE} gives the signature to the account {signer.strip()}"
    return False, f"Git signed a test commit, and the team repo's {SIGNERS_FILE} does not have the key that signed it"


# ------------------------------------------------------------------- GitHub


def gh_program() -> str:
    """The GitHub command-line tool. TUG_GH names another one, or none when it is empty."""
    return os.environ["TUG_GH"] if "TUG_GH" in os.environ else (shutil.which("gh") or "")


def _gh(program: str, *args: str) -> str:
    return run_process([program, *args], action="gh", timeout=GH_TIMEOUT)


def _pattern_covers(pattern: str, branch: str) -> bool:
    import fnmatch

    return fnmatch.fnmatchcase(branch, pattern)


def required_signatures(address: TeamRepoAddress, program: str) -> dict[str, bool]:
    """For each branch that must require signed commits: does GitHub require them? Asks GitHub. Raises GitError when it cannot."""
    from tugboat.strict_json import parse_json

    query = "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){branchProtectionRules(first:100){nodes{pattern requiresCommitSignatures}}}}"
    answer = parse_json(_gh(program, "api", "graphql", "-f", f"query={query}", "-f", f"owner={address.owner}", "-f", f"name={address.name}"))
    try:
        classic = [node for node in answer["data"]["repository"]["branchProtectionRules"]["nodes"] if node["requiresCommitSignatures"]]
    except (KeyError, TypeError) as error:
        raise GitError("gh", "GitHub's answer about branch protection could not be read") from error
    found = {}
    for branch in BRANCHES:
        required = any(_pattern_covers(str(node["pattern"]), branch) for node in classic)
        if not required:
            # A ruleset can require them too. GitHub answers for a branch that does not exist yet.
            rules = parse_json(_gh(program, "api", f"repos/{address.owner}/{address.name}/rules/branches/{branch}"))
            required = isinstance(rules, list) and any(isinstance(rule, dict) and rule.get("type") == "required_signatures" for rule in rules)
        found[branch] = required
    return found


def account_has_key(login: str, key: PublicKey, program: str) -> bool:
    """Is this key a signing key of the GitHub account? The list is public."""
    from tugboat.strict_json import parse_json

    keys = parse_json(_gh(program, "api", f"users/{login}/ssh_signing_keys"))
    if not isinstance(keys, list):
        raise GitError("gh", "GitHub's answer about signing keys could not be read")
    return any(isinstance(item, dict) and str(item.get("key", "")).split()[1:2] == [key.key] for item in keys)


# ----------------------------------------------------- what a check observed


def check_file(address: TeamRepoAddress) -> Path:
    return paths.signing_check_file(address)


def save_check(address: TeamRepoAddress, key: PublicKey, results: dict[str, Any]) -> None:
    file = check_file(address)
    file.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": CHECK_SCHEMA, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "key": key.key, **results}
    temporary = file.with_name(file.name + ".tmp")
    temporary.write_text(pretty_json(data), encoding="utf-8")
    os.replace(temporary, file)


def read_check(address: TeamRepoAddress) -> dict[str, Any] | None:
    """What the last `tug signing check` on this machine saw. None when there was none, or the file cannot be read."""
    try:
        data = read_json_file(check_file(address))
    except DataError:
        return None
    if not isinstance(data, dict) or data.get("schema") != CHECK_SCHEMA or not isinstance(data.get("at"), str):
        return None
    return data
