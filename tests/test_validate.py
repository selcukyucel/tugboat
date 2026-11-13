import unittest

from tugboat.errors import DataError
from tugboat.validate import (
    canonical_timestamp,
    require_bool,
    require_choice,
    require_exact_fields,
    require_int,
    require_object,
    require_text,
    utc_now,
    validate_relative_path,
    validate_slug,
)


class ObjectTests(unittest.TestCase):
    def test_require_object_refuses_lists_and_text(self):
        self.assertEqual(require_object({"a": 1}, "profile"), {"a": 1})
        for value in ([], "x", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(DataError, "profile must be a JSON object"):
                require_object(value, "profile")

    def test_exact_fields_names_what_is_missing_and_what_is_unknown(self):
        require_exact_fields({"a": 1, "c": 2}, required=frozenset({"a"}), optional=frozenset({"c"}), label="rule")
        with self.assertRaisesRegex(DataError, "rule is missing fields: a, b"):
            require_exact_fields({}, required=frozenset({"a", "b"}), label="rule")
        with self.assertRaisesRegex(DataError, "rule has unknown fields: x"):
            require_exact_fields({"a": 1, "x": 2}, required=frozenset({"a"}), label="rule")


class ScalarTests(unittest.TestCase):
    def test_text_is_trimmed_and_bounded(self):
        self.assertEqual(require_text("  hello  ", "title", 10), "hello")
        for value, message in (
            (1, "title must be text"),
            ("   ", "title cannot be empty"),
            ("x" * 11, "title cannot exceed 10 characters"),
            ("a\x00b", "title cannot contain control characters"),
            ("a\x1bb", "title cannot contain control characters"),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(DataError, message):
                require_text(value, "title", 10)

    def test_text_keeps_tabs_and_line_breaks(self):
        self.assertEqual(require_text("a\tb\nc", "note", 10), "a\tb\nc")

    def test_bool_refuses_numbers_and_text(self):
        self.assertIs(require_bool(True, "flag"), True)
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaises(DataError):
                require_bool(value, "flag")

    def test_int_refuses_bool_float_and_out_of_range(self):
        self.assertEqual(require_int(5, "count", minimum=0, maximum=10), 5)
        for value in (True, 1.0, "1", -1, 11):
            with self.subTest(value=value), self.assertRaises(DataError):
                require_int(value, "count", minimum=0, maximum=10)

    def test_choice(self):
        choices = frozenset({"jira", "github-projects"})
        self.assertEqual(require_choice("jira", "tracker", choices), "jira")
        with self.assertRaisesRegex(DataError, "tracker must be one of: github-projects, jira"):
            require_choice("trello", "tracker", choices)

    def test_slug(self):
        for good in ("ios", "claude-code", "rule.naming_1"):
            self.assertEqual(validate_slug(good, "id"), good)
        for bad in ("iOS", "-x", "x-", "a b", "a--b", "1a", ""):
            with self.subTest(bad=bad), self.assertRaises(DataError):
                validate_slug(bad, "id")


class PathTests(unittest.TestCase):
    def test_accepts_paths_inside_the_folder(self):
        for good in ("knowledge/login.md", "a/b/c.json", "README.md"):
            self.assertEqual(validate_relative_path(good, "path"), good)

    def test_refuses_paths_that_leave_the_folder(self):
        for bad in ("/etc/passwd", "../x", "a/../../x", "a/./b", "./a", "a//b", "a/", "a\\b", "~root/x/../../y"):
            with self.subTest(bad=bad), self.assertRaises(DataError):
                validate_relative_path(bad, "path")


class TimeTests(unittest.TestCase):
    def test_times_are_returned_in_utc(self):
        self.assertEqual(canonical_timestamp("2026-11-10T21:03:11Z", "at"), "2026-11-10T21:03:11Z")
        self.assertEqual(canonical_timestamp("2026-11-10T23:03:11+02:00", "at"), "2026-11-10T21:03:11Z")

    def test_refuses_a_time_without_a_timezone_and_text_that_is_not_a_time(self):
        with self.assertRaisesRegex(DataError, "at must include a timezone"):
            canonical_timestamp("2026-11-10T21:03:11", "at")
        with self.assertRaisesRegex(DataError, "at must be an ISO 8601 timestamp"):
            canonical_timestamp("yesterday", "at")

    def test_now_is_a_valid_timestamp(self):
        now = utc_now()
        self.assertEqual(canonical_timestamp(now, "now"), now)


if __name__ == "__main__":
    unittest.main()
