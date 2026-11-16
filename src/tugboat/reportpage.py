"""A report as a page a person reads: as Markdown, or as one HTML file.

The text in a report comes from outside the tool: a story title and its
acceptance criteria from the tracker, a finding that quotes source code,
the output of a test. All of it is data. On a page it must show as the
text it is. It must never become markup, a script, a link that was not
there, or a mention that notifies somebody.

So a page is built in two steps, and text is escaped in one place only:

- `page` turns a report into blocks: a heading, facts, paragraphs, a
  list, a table. A block holds text as it is in the record.
- `to_markdown` and `to_html` write the blocks. Every piece of text
  passes through `markdown_text` or `html_text` on its way out, and there
  is no way to put text on a page that goes around them.

What stays as it is: a bare web address in Markdown still becomes a link
where the reader's tool makes one, such as GitHub. It was in the tracker
already.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from tugboat import records

TITLES = {
    records.CHECKS: "Check results",
    records.ANALYSIS: "Code analysis",
    records.ACCEPTANCE: "Acceptance report",
    records.QA_PACK: "QA handoff pack",
}
_CRITERION_WORDS = {records.NOT_MET: "not met", records.NOT_APPLICABLE: "not applicable"}


class Link(NamedTuple):
    """A web address that is shown as a link. Any other text is shown as text."""

    url: str


class Heading(NamedTuple):
    level: int
    text: str


class Text(NamedTuple):
    text: str  # paragraphs, with an empty line between them


class Facts(NamedTuple):
    rows: tuple[tuple[str, str | Link], ...]  # a name and its value


class Bullets(NamedTuple):
    items: tuple[str, ...]


class Table(NamedTuple):
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


Block = Heading | Text | Facts | Bullets | Table

# ------------------------------------------------------------------ escaping

# Characters that draw nothing and can change what a reader sees: control characters, and the marks that turn the
# direction of text around. A tab and a line break are kept.
_UNSEEN = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")
_MARKDOWN_MARKS = re.compile(r"([\\`*_\[\]~|])")
_MENTION = re.compile(r"(?<![A-Za-z0-9])@(?=[A-Za-z0-9])")
_LINE_START = re.compile(r"^(?:([#+=-])|([0-9]{1,9})([.)]))")
_SAFE_LINK = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~:/?#\[\]@!$&'*+,;=%-]*)?$")


def clean(text: str) -> str:
    """Text with nothing unseen in it. Also right for text that is printed in a terminal."""
    return _UNSEEN.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).replace("\t", " ")


def _markdown_line(line: str) -> str:
    line = line.strip()  # four spaces in front would make a code block
    line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    line = _MARKDOWN_MARKS.sub(r"\\\1", line)
    # An "@name" would notify that person where the page is posted. A character that draws nothing after the "@" stops it.
    line = _MENTION.sub("@​", line)
    # A heading, a list, a quote, or a rule starts with one of these.
    return _LINE_START.sub(lambda match: f"\\{match.group(1)}" if match.group(1) else f"{match.group(2)}\\{match.group(3)}", line)


def markdown_text(text: str, *, one_line: bool = False) -> str:
    """Text for a Markdown page, so that it shows as the text it is.

    With `one_line`, for a heading, a table cell, or a list item, line
    breaks become spaces. Otherwise an empty line still parts two
    paragraphs, and a line break stays a line break.
    """
    lines = [_markdown_line(line) for line in clean(text).split("\n")]
    if one_line:
        return " ".join(line for line in lines if line)
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line:
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])
    # Two spaces at the end of a line are a line break in Markdown.
    return "\n\n".join("  \n".join(paragraph) for paragraph in paragraphs if paragraph)


def html_text(text: str) -> str:
    """Text for an HTML page, also inside an attribute."""
    return html.escape(clean(text), quote=True)


def _safe_link(url: str) -> bool:
    return bool(_SAFE_LINK.fullmatch(url))


# --------------------------------------------------------------- the writers


def _markdown_value(value: str | Link) -> str:
    if isinstance(value, Link):
        # Between the angle brackets nothing is markup, and the pattern allows no bracket and no space.
        return f"<{value.url}>" if _safe_link(value.url) else markdown_text(value.url, one_line=True)
    return markdown_text(value, one_line=True)


def to_markdown(blocks: Sequence[Block]) -> str:
    """The page as Markdown."""
    parts = []
    for block in blocks:
        if isinstance(block, Heading):
            parts.append(f"{'#' * min(max(block.level, 1), 6)} {markdown_text(block.text, one_line=True)}")
        elif isinstance(block, Text):
            parts.append(markdown_text(block.text))
        elif isinstance(block, Facts):
            parts.append("\n".join(f"- **{markdown_text(name, one_line=True)}:** {_markdown_value(value)}" for name, value in block.rows))
        elif isinstance(block, Bullets):
            parts.append("\n".join(f"- {markdown_text(item, one_line=True)}" for item in block.items))
        else:
            rows = [block.header, *block.rows]
            lines = ["| " + " | ".join(markdown_text(cell, one_line=True) or " " for cell in row) + " |" for row in rows]
            lines.insert(1, "|" + "---|" * len(block.header))
            parts.append("\n".join(lines))
    return "\n\n".join(part for part in parts if part) + "\n"


_STYLE = """
:root { color-scheme: light dark; --ink: #1c1e21; --soft: #5c6470; --line: #d5d9e0; --back: #ffffff; --band: #f4f6f8; }
@media (prefers-color-scheme: dark) { :root { --ink: #e8eaed; --soft: #a3abb8; --line: #3a4049; --back: #16181c; --band: #1f2329; } }
body { margin: 0 auto; padding: 24px 16px 48px; max-width: 920px; background: var(--back); color: var(--ink); font: 16px/1.5 -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 1.5rem; line-height: 1.25; } h2 { font-size: 1.15rem; margin-top: 2rem; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; } dt { color: var(--soft); } dd { margin: 0; overflow-wrap: anywhere; }
.scroll { overflow-x: auto; } table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top; } th { background: var(--band); }
a { color: inherit; }
""".strip()


def _html_value(value: str | Link) -> str:
    if isinstance(value, Link) and _safe_link(value.url):
        return f'<a href="{html_text(value.url)}" rel="noopener noreferrer">{html_text(value.url)}</a>'
    return html_text(value.url if isinstance(value, Link) else value)


def _html_paragraphs(text: str) -> str:
    paragraphs = [part.strip("\n") for part in re.split(r"\n[ ]*\n", clean(text)) if part.strip()]
    return "\n".join("<p>" + "<br>".join(html.escape(line.strip(), quote=True) for line in part.split("\n")) + "</p>" for part in paragraphs)


def to_html(blocks: Sequence[Block], title: str) -> str:
    """The page as one HTML file, with no script and nothing loaded from elsewhere."""
    parts = []
    for block in blocks:
        if isinstance(block, Heading):
            level = min(max(block.level, 1), 6)
            parts.append(f"<h{level}>{html_text(block.text)}</h{level}>")
        elif isinstance(block, Text):
            parts.append(_html_paragraphs(block.text))
        elif isinstance(block, Facts):
            rows = "".join(f"<dt>{html_text(name)}</dt><dd>{_html_value(value)}</dd>" for name, value in block.rows)
            parts.append(f"<dl>{rows}</dl>")
        elif isinstance(block, Bullets):
            parts.append("<ul>" + "".join(f"<li>{html_text(item)}</li>" for item in block.items) + "</ul>")
        else:
            head = "".join(f"<th>{html_text(cell)}</th>" for cell in block.header)
            body = "".join("<tr>" + "".join(f"<td>{html_text(cell)}</td>" for cell in row) + "</tr>" for row in block.rows)
            parts.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
    # The policy is a second lock: should a piece of markup ever get through, the browser still runs no script and loads nothing.
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html_text(title)}</title>\n<style>\n{_STYLE}\n</style>\n</head>\n<body>\n" + "\n".join(part for part in parts if part) + "\n</body>\n</html>\n"
    )


# ------------------------------------------------------------------ the page


def _count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def _criteria_table(criteria: Sequence[Mapping[str, Any]]) -> Table:
    return Table(
        ("Criterion", "Status", "Evidence"),
        tuple((item["text"], _CRITERION_WORDS.get(item["status"], item["status"]), item["evidence"] or "") for item in criteria),
    )


def _list_or_none(title: str, items: Sequence[str]) -> list[Block]:
    return [Heading(2, title), Bullets(tuple(items)) if items else Text("None named.")]


def _body(report: Mapping[str, Any]) -> list[Block]:
    kind = report["report"]
    if kind == records.CHECKS:
        checks = report["checks"]
        rows = tuple((item["name"], item["status"], "" if item["seconds"] is None else f"{item['seconds']} s", item["summary"] or "") for item in checks)
        return [Text(f"Result: {records.overall([item['status'] for item in checks])}."), Table(("Check", "Result", "Time", "What it said"), rows)]
    if kind == records.ANALYSIS:
        found = report["findings"]
        blocks: list[Block] = [Text(f"Result: {report['status']}. {_count(len(found), 'finding', 'findings')}.")]
        if report["reason"]:
            blocks.append(Text(f"Reason: {report['reason']}"))
        if found:
            rows = tuple(
                (item["severity"], item["title"], (item["path"] or "") + (f":{item['line']}" if item["path"] and item["line"] else ""), item["rule"] or "", item["detail"])
                for item in found
            )
            blocks.append(Table(("Severity", "Finding", "Where", "Rule", "Detail"), rows))
        return blocks
    if kind == records.ACCEPTANCE:
        criteria = report["criteria"]
        blocks = [Facts((("Criteria read from", f"{report['source']['kind']} \"{report['source']['name']}\""),))]
        if report["blocked_reason"]:
            blocks.append(Text(f"Result: blocked. {report['blocked_reason']}"))
        else:
            met = sum(item["status"] == records.MET for item in criteria)
            blocks += [Text(f"{met} of {_count(len(criteria), 'criterion', 'criteria')} met."), _criteria_table(criteria)]
        return blocks
    blocks = [Heading(2, "What changed"), Bullets(tuple(report["changes"])), Heading(2, "Acceptance criteria")]
    blocks.append(_criteria_table(report["criteria"]) if report["criteria"] else Text("None named."))
    blocks += _list_or_none("Risk areas", report["risks"])
    blocks += _list_or_none("Suggested exploratory tests", report["exploratory_tests"])
    blocks += [Heading(2, "Build"), Text(report["build"] or "None named.")]
    flags = report["feature_flags"]
    blocks += [Heading(2, "Feature flags"), Table(("Flag", "State"), tuple((flag["name"], flag["state"]) for flag in flags)) if flags else Text("None named.")]
    return blocks


def title_of(report: Mapping[str, Any], story: Mapping[str, Any] | None = None) -> str:
    """The title of a report's page, as text: the kind of report and the story it is about."""
    return f"{TITLES[report['report']]}: {story['title'] if story else report['task']['key']}"


def page(report: Mapping[str, Any], story: Mapping[str, Any] | None = None, stale: Sequence[str] = ()) -> list[Block]:
    """The blocks of one report's page.

    `report` is a checked report record. `story` is the story as the first
    event of the task holds it, when it is known. `stale` holds the
    reasons a result of this report no longer counts: a page for such a
    report says so before anything else.
    """
    blocks: list[Block] = [Heading(1, title_of(report, story))]
    if stale:
        blocks.append(Text(f"This report is stale: {'; '.join(stale)}. It is kept as history. It does not describe the code as it is now."))
    facts: list[tuple[str, str | Link]] = [("Story", report["task"]["key"])]
    if story:
        facts += [(name, value) for name, value in (("Epic", story["epic"]), ("Milestone", story["milestone"])) if value]
        if story["url"]:
            facts.append(("Link", Link(story["url"])))
    facts += [
        ("App repo", f"{report['repo']} ({report['platform']})"),
        ("Branch", report["branch"] or "none (detached)"),
        ("Commit", report["commit"][:12]),
        ("Made", f"{report['at']} by {report['author']} on {report['machine']}"),
        ("Team repo revision", report["team_repo_revision"][:12]),
    ]
    return [*blocks, Facts(tuple(facts)), *_body(report)]


def report_markdown(report: Mapping[str, Any], story: Mapping[str, Any] | None = None, stale: Sequence[str] = ()) -> str:
    return to_markdown(page(report, story, stale))


def report_html(report: Mapping[str, Any], story: Mapping[str, Any] | None = None, stale: Sequence[str] = ()) -> str:
    return to_html(page(report, story, stale), title_of(report, story))
