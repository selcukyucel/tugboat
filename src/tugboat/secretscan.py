"""Find text that looks like a secret, or like a raw chat transcript.

One list of patterns, used wherever text is about to be shared: a knowledge
note today, a write by the agent and a task record in later tasks. A finding
names the kind of secret and the line. It never repeats the secret.

The patterns are for well-known token shapes, plus one general shape: a name
such as `password` or `api_key`, followed by a value that mixes letters with
two digits or more. A word of prose after `password:` is not a finding, and
neither is a call or a property in a line of code.

A request header that carries a sign-in is a finding too: `Authorization:
Bearer <value>` and `Cookie: name=<value>`. A failing network test prints
them, and the output of a test is what a report quotes.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Finding(NamedTuple):
    kind: str  # what it looks like, such as "GitHub token"
    line: int  # counted from 1


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("Slack token", re.compile(r"\bxox[abeprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b")),
    ("AI provider key", re.compile(r"\bsk-(?:ant|proj|svcacct)-[A-Za-z0-9_-]{20,}")),
    ("signed token (JWT)", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)

# A name that says "this is a secret", then `=` or `:`, then the value.
_NAMED = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|auth[_-]?key)"
    r"[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9+/_=-]{12,})"
)
# A request header with a sign-in in it, as a log or a failing test prints it. Group 1 is the value.
_SIGN_IN_HEADERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sign-in in an Authorization header", re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|token|digest)\s+([A-Za-z0-9+/._~=-]{12,})")),
    ("sign-in in a cookie", re.compile(r"(?i)\b(?:set-)?cookie[\"']?\s*[:=]\s*[\"']?[^=;,\s\"']{1,60}=([A-Za-z0-9+/._~%=-]{16,})")),
)
_URL_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]{3,})@")
_PLACEHOLDER = re.compile(r"(?i)^(?:x+|\*+|\.+|password|passwd|pass|pwd|secret|token|changeme|redacted|example[a-z0-9_-]*|your[a-z0-9_-]*|<.*>|\$\{.*\}|\{\{.*\}\})$")

# A line that opens one turn of a chat: "User: ...", "**Assistant:** ...", or a role field of a saved session.
_SPEAKER = re.compile(r"(?i)^\s*(?:[>#*_-]+\s*)*\**(user|human|assistant|ai|claude|copilot|chatgpt|system)\**\s*:")
_ROLE = re.compile(r"""["']role["']\s*:\s*["'](user|assistant|system|tool)["']""")
TRANSCRIPT_TURNS = 4  # this many turn lines, from two speakers or more, read as a transcript


def _machine_made(value: str) -> bool:
    """True for a value that mixes letters with two digits or more, which a word of prose or a code name seldom does."""
    if _PLACEHOLDER.fullmatch(value):
        return False
    return sum(character.isdigit() for character in value) >= 2 and any(character.isalpha() for character in value)


def _named_secret(line: str) -> bool:
    for match in _NAMED.finditer(line):
        # `token = TokenStore2.shared` and `key = makeKey2024()` are code, not a value.
        if line[match.end(1) : match.end(1) + 1] not in {"(", ".", "["} and _machine_made(match.group(1)):
            return True
    return False


def find(text: str) -> list[Finding]:
    """Every line that looks like it holds a secret, at most one finding for a line."""
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        kind = next((name for name, pattern in _PATTERNS if pattern.search(line)), None)
        if kind is None and _named_secret(line):
            kind = "password or key"
        if kind is None:
            kind = next((name for name, pattern in _SIGN_IN_HEADERS if any(_machine_made(match.group(1)) for match in pattern.finditer(line))), None)
        if kind is None and any(not _PLACEHOLDER.fullmatch(match.group(1)) for match in _URL_PASSWORD.finditer(line)):
            kind = "password in a URL"
        if kind is not None:
            findings.append(Finding(kind, number))
    return findings


def looks_like_transcript(text: str) -> bool:
    """True when the text has the turn lines of a chat or of a saved session."""
    speakers: list[str] = []
    for line in text.splitlines():
        match = _SPEAKER.match(line) or _ROLE.search(line)
        if match:
            speakers.append(match.group(1).lower())
    return len(speakers) >= TRANSCRIPT_TURNS and len(set(speakers)) >= 2


def describe(findings: list[Finding], limit: int = 3) -> str:
    """The findings in words: 'a GitHub token on line 4, a private key on line 9'."""
    parts = [f"{finding.kind} on line {finding.line}" for finding in findings[:limit]]
    if len(findings) > limit:
        parts.append(f"{len(findings) - limit} more")
    return ", ".join(parts)
