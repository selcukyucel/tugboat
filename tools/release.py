#!/usr/bin/env python3
"""Tag a release.

Usage:
    uv run python tools/release.py           check, then create the tag
    uv run python tools/release.py --check   check only

The version is read from src/tugboat/__init__.py. The tag is "v" plus the
version. This script creates the tag locally. It never pushes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "tugboat" / "__init__.py"


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        sys.exit(f"release: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def main() -> int:
    check_only = sys.argv[1:] == ["--check"]
    if sys.argv[1:] and not check_only:
        sys.exit(__doc__)

    match = re.search(r'^__version__ = "((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"$', VERSION_FILE.read_text(encoding="utf-8"), re.M)
    if not match:
        sys.exit(f"release: no version like 1.2.3 found in {VERSION_FILE}")
    tag = f"v{match.group(1)}"

    if git("status", "--porcelain"):
        sys.exit("release: the working tree has uncommitted changes. Commit them first.")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        sys.exit(f"release: on branch '{branch}'. Releases are tagged on main.")
    if git("tag", "--list", tag):
        sys.exit(f"release: tag {tag} already exists. Raise the version in {VERSION_FILE.name} first.")

    env = {**os.environ, "PYTHONPATH": os.fspath(ROOT / "src")}
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, env=env, check=False)
    if tests.returncode != 0:
        sys.exit("release: the tests failed. No tag was created.")

    if check_only:
        print(f"release: ready to tag {tag}.")
        return 0
    git("tag", "-a", tag, "-m", f"Tugboat {match.group(1)}")
    print(f"release: created tag {tag}. To publish it, run:")
    print(f"  git push origin main {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
