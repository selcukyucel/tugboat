import unittest

import tugboat
from tugboat.errors import DataError
from tugboat.version import Version, installed_version, parse_tag, parse_version


class VersionTests(unittest.TestCase):
    def test_parses_three_numbers(self):
        self.assertEqual(parse_version("1.2.3"), Version(1, 2, 3))
        self.assertEqual(parse_version(" 0.10.0 "), Version(0, 10, 0))

    def test_refuses_anything_else(self):
        for bad in ("1.2", "1.2.3.4", "v1.2.3", "1.2.x", "01.2.3", "1.2.3-rc1", "1.2.3.dev1", "", None, 1):
            with self.subTest(bad=bad), self.assertRaises(DataError):
                parse_version(bad)

    def test_compares_by_number_not_by_text(self):
        self.assertLess(parse_version("0.9.0"), parse_version("0.10.0"))
        self.assertLess(parse_version("1.9.9"), parse_version("2.0.0"))
        self.assertEqual(max(parse_version("0.2.10"), parse_version("0.2.9")), Version(0, 2, 10))

    def test_tag_is_the_version_with_a_leading_v(self):
        self.assertEqual(Version(1, 2, 3).tag, "v1.2.3")
        self.assertEqual(parse_tag("v1.2.3"), Version(1, 2, 3))
        for bad in ("1.2.3", "V1.2.3", "v1.2", "release-1.2.3", None):
            with self.subTest(bad=bad), self.assertRaises(DataError):
                parse_tag(bad)

    def test_the_installed_version_is_valid(self):
        self.assertEqual(str(installed_version()), tugboat.__version__)


if __name__ == "__main__":
    unittest.main()
