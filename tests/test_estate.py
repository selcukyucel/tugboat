import json
import os
import unittest

from tugboat import estate, formats, globmatch
from tugboat.errors import TugError

from test_formats import ESTATE, PROFILE
from test_rules import RuleCase

APPS = [
    {
        "id": "shop-ios",
        "name": "Shop for iOS",
        "platform": "ios",
        "repo": "acme/shop-ios",
        "owner": "ios-team",
        "environments": ["dev", "production"],
        "modules": [
            {"id": "checkout", "owner": "@pat", "paths": ["Sources/Checkout/**", "Tests/CheckoutTests"]},
            {"id": "payments", "owner": "@sam", "paths": ["Sources/Checkout/Payments/**"]},
            {"id": "strings", "owner": "@lee", "paths": ["**/*.xcstrings"]},
        ],
    },
    {"id": "shop-android", "name": "Shop for Android", "platform": "android", "repo": "acme/shop-android", "owner": "android-team", "environments": [], "modules": []},
]
MAP = {"schema": "tug.estate/1", "apps": APPS}


class MatchTests(unittest.TestCase):
    def test_what_each_sign_matches(self):
        for pattern, path, expected in (
            ("Sources/Auth/**", "Sources/Auth/Login.swift", True),
            ("Sources/Auth/**", "Sources/Auth/Views/Login.swift", True),
            ("Sources/Auth/**", "Sources/Auth", True),
            ("Sources/Auth/**", "Sources/Authentication/Login.swift", False),
            ("Sources/Auth", "Sources/Auth/Views/Login.swift", True),  # a folder covers what is beneath it
            ("Sources/Auth", "Sources/Auth", True),
            ("Sources/Auth", "Sources/Au", False),
            ("Sources/*.swift", "Sources/App.swift", True),
            ("Sources/*.swift", "Sources/Auth/App.swift", False),  # * stays inside one folder name
            ("**/*.xcstrings", "Localizable.xcstrings", True),
            ("**/*.xcstrings", "a/b/c/Localizable.xcstrings", True),
            ("**/Secrets/**", "App/Secrets/keys.plist", True),
            ("App/**/Generated", "App/Generated/x.swift", True),
            ("App/**/Generated", "App/a/b/Generated/x.swift", True),
            ("File?.swift", "File1.swift", True),
            ("File?.swift", "File12.swift", False),
            ("File?.swift", "File/.swift", False),
            ("sources/auth", "Sources/Auth", False),  # letter case counts
            ("a.b[c]", "a.b[c]", True),  # nothing else is special
            ("a.b[c]", "aXbc", False),
        ):
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(globmatch.matches(pattern, path), expected)

    def test_a_pattern_with_more_fixed_folders_is_narrower(self):
        self.assertGreater(globmatch.specificity("Sources/Checkout/Payments/**"), globmatch.specificity("Sources/Checkout/**"))
        self.assertGreater(globmatch.specificity("Sources/Checkout/**"), globmatch.specificity("**/*.xcstrings"))
        self.assertGreater(globmatch.specificity("**/a/*.swift"), globmatch.specificity("**/*.swift"))


class OwnerTests(unittest.TestCase):
    def owner(self, path, repo="acme/shop-ios"):
        return [(found.owner, found.module) for found in estate.owners_of(estate.apps_of(APPS, repo), path)]

    def test_the_narrowest_module_wins_and_the_app_owner_is_the_fallback(self):
        self.assertEqual(self.owner("Sources/Checkout/Cart.swift"), [("@pat", "checkout")])
        self.assertEqual(self.owner("Sources/Checkout/Payments/Card.swift"), [("@sam", "payments")])
        self.assertEqual(self.owner("Tests/CheckoutTests/CartTests.swift"), [("@pat", "checkout")])
        self.assertEqual(self.owner("Sources/Home/Home.xcstrings"), [("@lee", "strings")])
        self.assertEqual(self.owner("Sources/Checkout/Checkout.xcstrings"), [("@pat", "checkout")])  # the folder is narrower than the file kind
        self.assertEqual(self.owner("Sources/Home/HomeView.swift"), [("ios-team", None)])
        self.assertEqual(self.owner("README.md"), [("ios-team", None)])

    def test_each_repo_sees_only_its_own_apps_whatever_the_letter_case(self):
        self.assertEqual(self.owner("Sources/Checkout/Cart.swift", "Acme/Shop-Android"), [("android-team", None)])
        self.assertEqual(self.owner("Sources/Checkout/Cart.swift", "acme/other"), [])
        self.assertEqual(estate.apps_of(APPS, None), [])

    def test_two_apps_in_one_repo_both_answer_when_no_module_covers_the_path(self):
        widget = {**APPS[0], "id": "shop-widget", "owner": "widget-team", "modules": [{"id": "widget", "owner": "@kim", "paths": ["Widget/**"]}]}
        apps = [APPS[0], widget]
        self.assertEqual([found.owner for found in estate.owners_of(apps, "Widget/Clock.swift")], ["@kim"])
        self.assertEqual([found.owner for found in estate.owners_of(apps, "README.md")], ["ios-team", "widget-team"])


class EstateInTheRepoTests(RuleCase):
    def merge_map(self, estate_map=MAP):
        return self.adopt(("estate/estate.json", json.dumps(estate_map, indent=2) + "\n"))

    def owner(self, root, *paths, cwd=None):
        return self.tug(cwd or root, "owner", *paths)

    def test_given_a_path_the_owner_is_named(self):
        self.merge_map()
        self.instructions(self.ios)
        code, out, err = self.owner(self.ios, "Sources/Checkout/Payments/Card.swift", "Sources/Home/HomeView.swift", "./Tests/CheckoutTests/")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out.splitlines(),
            [
                "Sources/Checkout/Payments/Card.swift: @sam (module payments of the app shop-ios, which covers Sources/Checkout/Payments/**)",
                "Sources/Home/HomeView.swift: ios-team (owner of the app shop-ios. No module covers this path.)",
                "Tests/CheckoutTests: @pat (module checkout of the app shop-ios, which covers Tests/CheckoutTests)",
            ],
        )

    def test_a_path_is_read_from_where_the_command_is_run_and_must_stay_inside_the_repo(self):
        self.merge_map()
        self.instructions(self.ios)
        folder = self.ios / "Sources/Checkout"
        folder.mkdir(parents=True)
        code, out, _ = self.owner(self.ios, "Cart.swift", "../Home/HomeView.swift", os.fspath(self.ios / "Sources/Checkout/Payments/Card.swift"), cwd=folder)
        self.assertEqual(code, 0)
        self.assertEqual([line.split(":")[0] for line in out.splitlines()], ["Sources/Checkout/Cart.swift", "Sources/Home/HomeView.swift", "Sources/Checkout/Payments/Card.swift"])
        code, out, _ = self.owner(self.ios, "../../outside.swift", "Sources/Checkout/Cart.swift")
        self.assertEqual(code, 1)
        self.assertIn("../../outside.swift: ../../outside.swift is outside this repo", out)
        self.assertIn("Sources/Checkout/Cart.swift: @pat", out)
        with self.assertRaisesRegex(TugError, "is the repo itself"):
            estate.repo_path(self.ios, ".", self.ios)

    def test_the_agent_is_told_who_owns_what_in_its_own_repo_only(self):
        self.merge_map()
        text = self.instructions(self.ios)
        section = text[text.index("## Who owns what") :]
        for expected in (
            "- App `shop-ios` (Shop for iOS): owner ios-team. Environments: dev, production.",
            "  - Module `checkout`: owner @pat. Paths: `Sources/Checkout/**`, `Tests/CheckoutTests`",
            "To name the owner of a file, run `tug owner <path>`.",
            "Do not guess one.",
        ):
            self.assertIn(expected, section)
        self.assertNotIn("shop-android", section)
        android = self.instructions(self.android)
        self.assertIn("- App `shop-android` (Shop for Android): owner android-team. Environments: none listed.", android)
        self.assertNotIn("checkout", android[android.index("## Who owns what") :])
        self.assertIn("- Estate map changed (.tugboat/team/estate/estate.json)", self.sync_out)

    def test_a_repo_that_is_not_in_the_map_gets_no_guess(self):
        text = self.instructions(self.ios)  # the starter map lists no app
        self.assertIn("This repo is not in the estate map, `.tugboat/team/estate/estate.json`, so no owner can be named for its files.", text)
        code, _, err = self.owner(self.ios, "README.md")
        self.assertEqual(code, 1)
        self.assertIn("acme/shop-ios is not in the estate map (.tugboat/team/estate/estate.json), so no owner can be named.", err)

    def test_a_map_that_is_not_valid_is_named_and_not_used_and_fails_the_team_repo_check(self):
        broken = {"schema": "tug.estate/1", "apps": [{**APPS[0], "platform": "backend"}]}
        self.merge_map(broken)
        text = self.instructions(self.ios)
        self.assertIn("No estate map is in use. Not loaded: the estate map estate/estate.json was not loaded: estate map apps[0] platform must be one of: android, ios.", text)
        self.assertIn("Note: the estate map estate/estate.json was not loaded", self.sync_out)
        code, _, err = self.owner(self.ios, "README.md")
        self.assertEqual(code, 1)
        self.assertIn("the estate map estate/estate.json was not loaded", err)
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 1)
        self.assertIn("estate/estate.json: estate map apps[0] platform must be one of: android, ios", out)

    def test_the_check_warns_about_an_app_whose_repo_or_platform_does_not_match_the_profile(self):
        wrong = {"schema": "tug.estate/1", "apps": [{**APPS[0], "repo": "acme/shop-iso"}, {**APPS[1], "platform": "ios"}]}
        self.merge_map(wrong)
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 0)
        self.assertIn("warning: estate/estate.json: app 'shop-ios' names the repo acme/shop-iso, which is not in the project profile", out)
        self.assertIn("warning: estate/estate.json: app 'shop-android' has platform ios, and the project profile says acme/shop-android is android", out)

    def test_outside_a_repo_that_tugboat_set_up(self):
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "x\n"}, "acme/other")
        code, _, err = self.owner(other, "README.md")
        self.assertEqual(code, 1)
        self.assertIn("this folder is not set up with Tugboat.", err)


class FormatTests(unittest.TestCase):
    def test_the_example_map_and_the_test_map_are_valid_and_a_backend_app_is_not(self):
        formats.validate_estate(json.loads(json.dumps(ESTATE)))
        formats.validate_estate(json.loads(json.dumps(MAP)))
        self.assertTrue(all(repo["platform"] in {"ios", "android"} for repo in PROFILE["app_repos"]))


if __name__ == "__main__":
    unittest.main()
