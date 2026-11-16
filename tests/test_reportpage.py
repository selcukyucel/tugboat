import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from tugboat import records, reportpage
from tugboat.reportpage import Bullets, Facts, Heading, Link, Table, Text

from test_formats import changed
from test_records import ACCEPTANCE_REPORT, ANALYSIS_REPORT, CHECKS_REPORT, QA_PACK, STORY, nested

# What a tracker or a source file could hold, each one a way to become more than text.
HOSTILE = (
    '<script>alert("x")</script>',
    '<img src=x onerror="alert(1)">',
    "<b>bold</b> & <a href='https://evil.example'>click</a>",
    "</td></tr></table><h1>Approved</h1>",
    "[click me](https://evil.example)",
    "![tracking](https://evil.example/pixel.png)",
    "# A heading of its own",
    "- [x] done already",
    "1. first",
    "> quoted",
    "`code` and **bold** and _italic_ and ~~gone~~",
    "a | b | c",
    "@everyone please approve",
    "<!-- hidden -->",
    "&lt;already escaped&gt; &amp; &copy;",
    "    four spaces in front",
    "---",
)


class Seen(HTMLParser):
    """What a browser would make of a page: its tags, its attributes, and its text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attributes, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes += [(tag, name, value) for name, value in attrs]

    def handle_data(self, data):
        self.text.append(data)


def seen(page):
    parser = Seen()
    parser.feed(page)
    parser.close()
    return parser


def hostile_reports():
    """Every kind of report with one hostile text in each place that comes from outside the tool."""
    for text in HOSTILE:
        one_line = " ".join(text.split())
        yield text, records.validate_record(nested(CHECKS_REPORT, ("checks", 1, "summary"), text)), {**STORY, "title": one_line, "epic": one_line}
        yield text, records.validate_record(nested(nested(ANALYSIS_REPORT, ("findings", 0, "title"), one_line), ("findings", 0, "detail"), text)), None
        yield text, records.validate_record(nested(nested(ACCEPTANCE_REPORT, ("criteria", 0, "text"), text), ("criteria", 0, "evidence"), text)), STORY
        flags = [{"name": one_line[:100], "state": one_line[:100]}]
        yield text, records.validate_record(changed(QA_PACK, changes=[one_line], risks=[one_line], exploratory_tests=[one_line], build=one_line, feature_flags=flags)), STORY


class HtmlTests(unittest.TestCase):
    ALLOWED = {"html", "head", "meta", "title", "style", "body", "h1", "h2", "p", "br", "dl", "dt", "dd", "ul", "li", "div", "table", "thead", "tbody", "tr", "th", "td", "a"}

    def test_a_story_title_with_html_in_it_renders_as_text(self):
        title = '<script>alert("x")</script> & <b>Checkout</b>'
        page = reportpage.report_html(records.validate_record(CHECKS_REPORT), {**STORY, "title": title})
        self.assertNotIn("<script", page)
        self.assertNotIn("<b>", page)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b&gt;Checkout&lt;/b&gt;", page)
        found = seen(page)
        self.assertIn(f"Check results: {title}", "".join(found.text))  # the reader sees the title as it was written
        self.assertEqual(found.tags.count("script"), 0)

    def test_no_text_from_outside_becomes_markup(self):
        for text, report, story in hostile_reports():
            with self.subTest(text=text, report=report["report"]):
                plain = seen(reportpage.report_html(records.validate_record(CHECKS_REPORT), STORY if story else None))
                found = seen(reportpage.report_html(report, story))
                self.assertLessEqual(set(found.tags), self.ALLOWED)
                self.assertNotIn("img", found.tags)
                # The only link is the story's own, and no attribute came from the text.
                links = [value for tag, name, value in found.attributes if tag == "a" and name == "href"]
                self.assertEqual(links, [STORY["url"]] if story else [])
                self.assertEqual({name for _tag, name, _value in found.attributes}, {name for _tag, name, _value in plain.attributes})
                shown = " ".join("".join(found.text).split())
                self.assertIn(" ".join(text.split()), shown)  # and the text is all there, as it was written

    def test_the_page_runs_no_script_and_loads_nothing(self):
        page = reportpage.report_html(records.validate_record(QA_PACK), STORY)
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("""<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">""", page)
        found = seen(page)
        self.assertEqual([value for _tag, name, value in found.attributes if name in {"src", "href"}], [STORY["url"]])
        self.assertNotIn("script", found.tags)
        self.assertNotIn("link", found.tags)

    def test_only_a_plain_https_address_becomes_a_link(self):
        for url in ("javascript:alert(1)", "http://example.com/x", 'https://example.com/"onmouseover="alert(1)', "https://user:pw@example.com/", "https://example.com/a b", "data:text/html,<script>"):
            with self.subTest(url=url):
                found = seen(reportpage.to_html([Facts((("Link", Link(url)),))], "t"))
                self.assertNotIn("a", found.tags)
                self.assertIn(url, "".join(found.text))  # shown as text
                self.assertNotRegex(reportpage.to_markdown([Facts((("Link", Link(url)),))]), r"<[a-z]+:")
        found = seen(reportpage.to_html([Facts((("Link", Link("https://example.atlassian.net/browse/SHOP-123?focus=1#top")),))], "t"))
        self.assertEqual(found.attributes[-2:], [("a", "href", "https://example.atlassian.net/browse/SHOP-123?focus=1#top"), ("a", "rel", "noopener noreferrer")])


class MarkdownTests(unittest.TestCase):
    def test_a_story_title_with_html_in_it_renders_as_text(self):
        title = '<script>alert("x")</script> & <b>Checkout</b>'
        page = reportpage.report_markdown(records.validate_record(CHECKS_REPORT), {**STORY, "title": title})
        self.assertEqual(page.splitlines()[0], '# Check results: &lt;script&gt;alert("x")&lt;/script&gt; &amp; &lt;b&gt;Checkout&lt;/b&gt;')
        self.assertNotIn("<script", page)

    def test_each_mark_is_written_so_that_it_shows_as_itself(self):
        for text, expected in (
            ("<b>bold</b> & more", "&lt;b&gt;bold&lt;/b&gt; &amp; more"),
            ("[click me](https://evil.example)", "\\[click me\\](https://evil.example)"),
            ("![pixel](https://evil.example/p.png)", "!\\[pixel\\](https://evil.example/p.png)"),
            ("`code` **bold** _italic_ ~~gone~~", "\\`code\\` \\*\\*bold\\*\\* \\_italic\\_ \\~\\~gone\\~\\~"),
            ("a | b", "a \\| b"),
            ("# Heading", "\\# Heading"),
            ("- [x] done", "\\- \\[x\\] done"),
            ("+ item", "\\+ item"),
            ("1. first", "1\\. first"),
            ("12) twelfth", "12\\) twelfth"),
            ("> quoted", "&gt; quoted"),
            ("---", "\\---"),
            ("===", "\\==="),
            ("    four spaces in front", "four spaces in front"),
            ("a\\*b", "a\\\\\\*b"),
            ("@everyone please look, says pat@example.com", "`@everyone` please look, says pat@example.com"),
            ("cc @acme/mobile_team and @pat-dev.", "cc `@acme/mobile_team` and `@pat-dev`."),
            ("`@lead` in code of its own", "\\``@lead`\\` in code of its own"),
            ("&lt;script&gt;", "&amp;lt;script&amp;gt;"),
            ("version 2.8.9 is 3 - 1 = 2 # fine", "version 2.8.9 is 3 - 1 = 2 # fine"),  # the same marks inside a line are left alone
        ):
            with self.subTest(text=text):
                self.assertEqual(reportpage.markdown_text(text), expected)

    def test_text_of_several_lines_keeps_its_paragraphs_and_one_line_text_becomes_one_line(self):
        text = "First line.\nSecond line.\n\n\n# Not a heading\n"
        self.assertEqual(reportpage.markdown_text(text), "First line.  \nSecond line.\n\n\\# Not a heading")
        self.assertEqual(reportpage.markdown_text(text, one_line=True), "First line. Second line. \\# Not a heading")

    def test_no_text_from_outside_breaks_the_shape_of_the_page(self):
        samples = {"checks": CHECKS_REPORT, "analysis": ANALYSIS_REPORT, "acceptance": ACCEPTANCE_REPORT, "qa-pack": QA_PACK}
        for text, report, story in hostile_reports():
            with self.subTest(text=text, report=report["report"]):
                page = reportpage.report_markdown(report, story)
                self.assertNotRegex(page, r"<(?!https://github\.com/acme/shop-ios/issues/311>)")  # no tag, and no link but the story's own
                self.assertNotRegex(page, r"(?<!\\)\]\(")  # no Markdown link or image
                self.assertNotRegex(page, r"(?<![A-Za-z0-9`])@[A-Za-z0-9]")  # nobody is notified: a name after "@" is shown as code
                # The same headings, items, and columns as the page of the sample report: the text added no block and no column.
                plain = reportpage.report_markdown(records.validate_record(samples[report["report"]]), STORY if story else None)
                self.assertEqual(self.shape(page), self.shape(plain))

    @staticmethod
    def shape(page):
        shape = []
        for line in page.splitlines():
            if re.match(r"^#{1,6} ", line):
                shape.append("heading")
            elif line.startswith("|"):
                shape.append(("row", len(re.findall(r"(?<!\\)\|", line))))
            elif line.startswith("- "):
                shape.append("item")
        return shape

    def test_a_table_keeps_its_columns_whatever_a_cell_holds(self):
        page = reportpage.to_markdown([Table(("A", "B"), (("a | b\nsecond line", ""), ("x", "y")))])
        self.assertEqual(page, "| A | B |\n|---|---|\n| a \\| b second line |   |\n| x | y |\n")


class PageTests(unittest.TestCase):
    def test_each_kind_of_report_has_its_page(self):
        for report, title, words in (
            (CHECKS_REPORT, "Check results", ["Result: failed.", "| ui-tests | blocked |   | No simulator named iPhone 17. |", "| build | passed | 95 s |   |"]),
            (ANALYSIS_REPORT, "Code analysis", ["Result: failed. 1 finding.", "| high | Force unwrap of the session token | Shop/Auth/Session.swift:88 | IOS-004 | The token can be nil after sign-out. |"]),
            (ACCEPTANCE_REPORT, "Acceptance report", ["1 of 2 criteria met.", "| It works offline. | unverified |   |", '- **Criteria read from:** task-list "Acceptance criteria"']),
            (QA_PACK, "QA handoff pack", ["## What changed", "## Risk areas", "## Suggested exploratory tests", "## Build", "## Feature flags", "| delivery\\_fee\\_line | on for the test group |"]),
        ):
            with self.subTest(title=title):
                page = reportpage.report_markdown(records.validate_record(report), STORY)
                self.assertEqual(page.splitlines()[0], f"# {title}: Show the delivery fee in the checkout total")
                for expected in ["- **Story:** acme/shop-ios#311", "- **Epic:** Checkout", "- **Link:** <https://github.com/acme/shop-ios/issues/311>", "- **Commit:** 1f1f1f1f1f1f", *words]:
                    self.assertIn(expected, page)
                self.assertNotIn("Milestone", page)  # the story names none

    def test_without_the_story_the_page_names_the_task(self):
        page = reportpage.report_markdown(records.validate_record(CHECKS_REPORT))
        self.assertEqual(page.splitlines()[0], "# Check results: acme/shop-ios#311")
        self.assertNotIn("Link", page)

    def test_the_four_results_stay_distinct_and_blocked_is_never_shown_as_passed(self):
        checks = [{"name": name, "status": status, "summary": None, "seconds": None} for name, status in (("a", "passed"), ("b", "failed"), ("c", "blocked"), ("d", "untested"))]
        page = reportpage.report_markdown(records.validate_record(changed(CHECKS_REPORT, checks=checks)))
        for row in ("| a | passed |", "| b | failed |", "| c | blocked |", "| d | untested |"):
            self.assertIn(row, page)
        only_blocked = reportpage.report_markdown(records.validate_record(changed(CHECKS_REPORT, checks=checks[2:3])))
        self.assertIn("Result: blocked.", only_blocked)
        blocked = changed(ACCEPTANCE_REPORT, criteria=[], blocked_reason="The story has no acceptance criteria.")
        page = reportpage.report_markdown(records.validate_record(blocked))
        self.assertIn("Result: blocked. The story has no acceptance criteria.", page)
        self.assertNotIn("met.", page)
        untested = changed(ANALYSIS_REPORT, status="untested", reason="The reviewer agent is not installed.", findings=[])
        self.assertIn("Result: untested. 0 findings.\n\nReason: The reviewer agent is not installed.", reportpage.report_markdown(records.validate_record(untested)))

    def test_a_stale_report_says_so_before_anything_else(self):
        blocks = reportpage.page(records.validate_record(ANALYSIS_REPORT), STORY, stale=("the commit changed", "the team's rules changed"))
        self.assertEqual(blocks[1], Text("This report is stale: the commit changed; the team's rules changed. It is kept as history. It does not describe the code as it is now."))
        self.assertIsInstance(reportpage.page(records.validate_record(ANALYSIS_REPORT), STORY)[1], Facts)

    def test_an_empty_qa_pack_names_what_is_missing(self):
        page = reportpage.report_markdown(records.validate_record(changed(QA_PACK, criteria=[], risks=[], exploratory_tests=[], build=None, feature_flags=[])))
        self.assertEqual(page.count("None named."), 5)

    def test_no_file_of_the_tool_holds_a_character_that_draws_nothing(self):
        # Such a character in source text hides what the code does. It is written as an escape, such as \\u202e.
        root = Path(__file__).resolve().parent.parent
        unseen = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
        files = [*root.glob("src/**/*.py"), *root.glob("src/**/*.md"), *root.glob("tests/*.py"), root / "README.md"]
        self.assertGreater(len(files), 50)
        self.assertEqual([path.relative_to(root).as_posix() for path in files if unseen.search(path.read_text(encoding="utf-8"))], [])

    def test_what_draws_nothing_is_taken_out(self):
        hidden = "safe\u202e\u2066 text\x1b[31m red\x07\u200f"
        self.assertEqual(reportpage.clean(hidden), "safe text[31m red")
        self.assertEqual(reportpage.clean("a\r\nb\rc\td"), "a\nb\nc d")
        self.assertEqual(reportpage.to_markdown([Heading(2, hidden), Bullets((hidden,))]), "## safe text\\[31m red\n\n- safe text\\[31m red\n")
        self.assertNotIn("\u202e", reportpage.to_html([Text(hidden)], hidden))


if __name__ == "__main__":
    unittest.main()
