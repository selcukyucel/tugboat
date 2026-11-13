import os
import tempfile
import unittest
from pathlib import Path

from tugboat.errors import DataError
from tugboat.strict_json import MAX_DEPTH, canonical_json, parse_json, pretty_json, read_json_file


class ParseJsonTests(unittest.TestCase):
    def test_accepts_ordinary_json(self):
        self.assertEqual(parse_json('{"a": [1, 2.5, "x", true, null]}'), {"a": [1, 2.5, "x", True, None]})

    def test_refuses_a_duplicate_field(self):
        with self.assertRaisesRegex(DataError, "duplicate JSON field: a"):
            parse_json('{"a": 1, "a": 2}')

    def test_refuses_a_duplicate_field_inside_a_list(self):
        with self.assertRaisesRegex(DataError, "duplicate JSON field: b"):
            parse_json('{"a": [{"b": 1, "b": 2}]}')

    def test_refuses_nan_and_infinity(self):
        for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.subTest(text=text), self.assertRaisesRegex(DataError, "non-finite"):
                parse_json(text)

    def test_refuses_text_that_is_not_json(self):
        for text in ("", "{", "{'a': 1}", '{"a": 1} trailing'):
            with self.subTest(text=text), self.assertRaisesRegex(DataError, "invalid JSON"):
                parse_json(text)

    def test_accepts_nesting_at_the_limit_and_refuses_one_more(self):
        at_limit = "[" * MAX_DEPTH + "]" * MAX_DEPTH
        parse_json(at_limit)
        with self.assertRaisesRegex(DataError, "nesting exceeds"):
            parse_json("[" + at_limit + "]")

    def test_very_deep_nesting_is_an_error_not_a_crash(self):
        with self.assertRaises(DataError):
            parse_json("[" * 100_000 + "]" * 100_000)


class ReadJsonFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "data.json"

    def test_reads_a_file(self):
        self.path.write_text('{"name": "tug"}', encoding="utf-8")
        self.assertEqual(read_json_file(self.path), {"name": "tug"})

    def test_a_missing_file_is_a_data_error_that_names_the_file(self):
        with self.assertRaisesRegex(DataError, "cannot read .*data.json"):
            read_json_file(self.path)

    def test_refuses_a_file_over_the_size_limit_without_parsing_it(self):
        self.path.write_text('{"a": "' + "x" * 200 + '"}', encoding="utf-8")
        with self.assertRaisesRegex(DataError, "larger than 100 bytes"):
            read_json_file(self.path, max_bytes=100)

    def test_refuses_bytes_that_are_not_utf8(self):
        self.path.write_bytes(b'{"a": "\xff"}')
        with self.assertRaisesRegex(DataError, "not valid UTF-8"):
            read_json_file(self.path)

    def test_a_parse_error_names_the_file(self):
        self.path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
        with self.assertRaisesRegex(DataError, "data.json: duplicate JSON field: a"):
            read_json_file(self.path)

    def test_a_folder_is_a_data_error(self):
        with self.assertRaises(DataError):
            read_json_file(os.fspath(self.folder.name))


class WriteFormTests(unittest.TestCase):
    def test_canonical_form_does_not_depend_on_field_order(self):
        self.assertEqual(canonical_json({"b": 1, "a": [2, 3]}), canonical_json({"a": [2, 3], "b": 1}))
        self.assertEqual(canonical_json({"b": 1, "a": "é"}), '{"a":"\\u00e9","b":1}')

    def test_canonical_form_refuses_nan_and_non_json_values(self):
        for value in ({"a": float("nan")}, {"a": {1, 2}}):
            with self.subTest(value=value), self.assertRaises(DataError):
                canonical_json(value)

    def test_pretty_form_round_trips_and_ends_with_a_newline(self):
        value = {"b": [1, {"c": "é"}], "a": None}
        text = pretty_json(value)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(parse_json(text), value)


if __name__ == "__main__":
    unittest.main()
