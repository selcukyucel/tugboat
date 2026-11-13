import unittest

from tugboat import frontmatter
from tugboat.errors import DataError


class ParseTests(unittest.TestCase):
    def test_reads_plain_text_lists_and_quoted_text(self):
        text = '---\nid: IOS-001\ntitle: New unit tests use Swift Testing\npaths: ["a/**", "b/c.swift"]\nnote: "Why: because"\n---\n\nBody line.\n\nMore.\n'
        fields, body = frontmatter.parse(text)
        self.assertEqual(
            fields,
            {"id": "IOS-001", "title": "New unit tests use Swift Testing", "paths": ["a/**", "b/c.swift"], "note": "Why: because"},
        )
        self.assertEqual(body, "Body line.\n\nMore.\n")

    def test_plain_values_stay_text(self):
        fields, _ = frontmatter.parse("---\ndate: 2026-11-13\ncount: 12\nflag: true\n---\nx\n")
        self.assertEqual(fields, {"date": "2026-11-13", "count": "12", "flag": "true"})

    def test_windows_line_endings_are_accepted(self):
        fields, body = frontmatter.parse("---\r\nid: IOS-001\r\n---\r\nBody\r\n")
        self.assertEqual(fields, {"id": "IOS-001"})
        self.assertTrue(body.startswith("Body"))

    def test_a_dashed_line_in_the_body_is_not_a_header_end(self):
        _, body = frontmatter.parse("---\nid: x1\n---\nOne\n---\nTwo\n")
        self.assertEqual(body, "One\n---\nTwo\n")

    def test_refuses_what_it_cannot_read_exactly(self):
        cases = {
            "no header": ("# Title\n", "must start with a '---' header"),
            "empty file": ("", "must start with a '---' header"),
            "no closing line": ("---\nid: x\n", "no closing '---'"),
            "not key value": ("---\njust words\n---\n", "line 2 must look like 'key: value'"),
            "upper case key": ("---\nID: x\n---\n", "line 2 must look like 'key: value'"),
            "indented line": ("---\npaths:\n  - a\n---\n", "has no value"),
            "comment": ("---\n# a comment\n---\n", "must look like 'key: value'"),
            "duplicate": ("---\nid: a\nid: b\n---\n", "'id' appears twice"),
            "no space": ("---\nid:x\n---\n", "needs a space after the colon"),
            "colon inside": ("---\ntitle: Why: because\n---\n", "'title' must be written in double quotes"),
            "hash inside": ("---\ntitle: Use C #1\n---\n", "'title' must be written in double quotes"),
            "starts with symbol": ("---\ntitle: *bold\n---\n", "'title' must be written in double quotes"),
            "single quotes": ("---\ntitle: 'x'\n---\n", "'title' must be written in double quotes"),
            "bad json list": ("---\npaths: [a, b]\n---\n", "header field 'paths': invalid JSON"),
            "duplicate in json": ('---\nx: {"a": 1, "a": 2}\n---\n', "header field 'x': duplicate JSON field"),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(DataError, message):
                frontmatter.parse(text)

    def test_a_header_that_never_ends_is_stopped_early(self):
        text = "---\n" + "".join(f"k{n}: v\n" for n in range(500))
        with self.assertRaisesRegex(DataError, "no closing '---' line within its first 60 lines"):
            frontmatter.parse(text)


class RenderTests(unittest.TestCase):
    def test_what_is_written_reads_back_the_same(self):
        fields = {
            "id": "DEC-0001",
            "title": "Why: a title that needs quotes #1",
            "owner": "Zoë",
            "paths": ["Sources/**", "Tests/é.swift"],
            "empty": [],
            "padded": " keep the spaces ",
            "two_lines": "one\ntwo",
        }
        text = frontmatter.render(fields, "\nThe body.\n\n")
        self.assertEqual(frontmatter.parse(text), (fields, "The body.\n"))
        self.assertIn("owner: Zoë\n", text)
        self.assertIn('title: "Why: a title that needs quotes #1"\n', text)

    def test_refuses_a_bad_field_name(self):
        with self.assertRaises(DataError):
            frontmatter.render({"Bad Name": "x"}, "body")


if __name__ == "__main__":
    unittest.main()
