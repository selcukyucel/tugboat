import json
import os
import unittest

from tugboat import permissions, settingsmerge, team_repo

from test_rules import RuleCase

STARTER = json.loads(team_repo.starter_layout()["permissions/protected-paths.json"])


def protected(*entries):
    """The team repo's file with the starter entries and these (pattern, platform, reason) on top."""
    paths = STARTER["paths"] + [{"pattern": pattern, "platform": platform, "reason": reason} for pattern, platform, reason in entries]
    return "permissions/protected-paths.json", json.dumps({"schema": "tug.protected-paths/1", "paths": paths}, indent=2) + "\n"


class AskRuleTests(unittest.TestCase):
    def test_only_the_rules_tugboat_added_are_taken_out_again(self):
        mine = ["Edit(//repo/fastlane/**)", "Edit(//repo/a.p12)"]
        settings = {"permissions": {"allow": ["Bash(ls:*)"], "ask": ["Edit(//repo/my-own/**)"]}, "x": 1}
        merged, owned = settingsmerge.with_ask_rules(settings, mine, [])
        self.assertEqual(merged["permissions"]["ask"], ["Edit(//repo/my-own/**)", *mine])
        self.assertEqual((owned, merged["permissions"]["allow"], merged["x"]), (mine, ["Bash(ls:*)"], 1))
        self.assertEqual(settings["permissions"]["ask"], ["Edit(//repo/my-own/**)"])  # the input is not changed
        again, owned_again = settingsmerge.with_ask_rules(merged, mine, owned)
        self.assertEqual((again, owned_again), (merged, mine))
        fewer, owned_fewer = settingsmerge.with_ask_rules(merged, mine[:1], owned)
        self.assertEqual(fewer["permissions"]["ask"], ["Edit(//repo/my-own/**)", mine[0]])
        none, owned_none = settingsmerge.with_ask_rules(merged, [], owned)
        self.assertEqual((none, owned_none), (settings, []))

    def test_a_rule_the_developer_already_has_is_not_added_twice_and_is_not_tugboats(self):
        rule = "Edit(//repo/fastlane/**)"
        merged, owned = settingsmerge.with_ask_rules({"permissions": {"ask": [rule]}}, [rule], [])
        self.assertEqual((merged["permissions"]["ask"], owned), ([rule], []))
        cleared, _ = settingsmerge.with_ask_rules(merged, [], owned)
        self.assertEqual(cleared["permissions"]["ask"], [rule])  # still the developer's

    def test_settings_with_no_rules_left_lose_the_empty_keys(self):
        merged, owned = settingsmerge.with_ask_rules({}, ["Edit(//r/a)"], [])
        self.assertEqual(settingsmerge.with_ask_rules(merged, [], owned)[0], {})


class CompileTests(RuleCase):
    def compiled(self, root):
        return permissions.read_compiled(root)

    def patterns(self, root, source):
        return [entry.pattern for entry in self.compiled(root).protected if entry.source == source]

    def settings(self, root):
        return json.loads((root / ".claude/settings.local.json").read_text(encoding="utf-8"))

    def ask(self, root):
        return self.settings(root).get("permissions", {}).get("ask", [])

    def protected(self, root, *paths, cwd=None):
        return self.tug(cwd or root, "protected", *paths)

    def test_init_compiles_the_team_list_for_the_repos_platform_with_tugboats_own_files(self):
        self.instructions(self.ios)
        self.instructions(self.android)
        ios, android = self.patterns(self.ios, "team"), self.patterns(self.android, "team")
        self.assertEqual(ios, [".github/workflows/**", "**/*.mobileprovision", "**/*.p12", "**/ExportOptions.plist", "fastlane/**"])
        self.assertEqual(android, [".github/workflows/**", "fastlane/**", "**/*.jks", "**/*.keystore", "**/keystore.properties"])
        self.assertEqual((self.compiled(self.ios).platform, self.compiled(self.android).platform), ("ios", "android"))
        own = self.patterns(self.ios, "tugboat")
        for expected in (".tugboat/team/**", ".tugboat/*.json", ".claude/rules/tugboat.md", ".claude/skills/tug-capture-notes", ".claude/agents/reviewer.md", ".claude/settings.local.json"):
            self.assertIn(expected, own)
        self.assertFalse(any(pattern.startswith(".tugboat/capture") for pattern in own))
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")

    def test_claude_code_gets_an_ask_rule_with_the_full_path_for_each_protected_path(self):
        self.instructions(self.ios)
        top = os.fspath(self.ios)
        rules = self.ask(self.ios)
        for expected in (
            f"Edit(/{top}/.github/workflows/**)",
            f"Edit(/{top}/**/*.p12)",
            f"Edit(/{top}/**/*.p12/**)",  # a pattern covers what is beneath it too
            f"Edit(/{top}/fastlane/**)",
            f"Edit(/{top}/.tugboat/team/**)",
            f"Edit(/{top}/.claude/rules/tugboat.md)",
            f"Edit(/{top}/.claude/settings.local.json)",
        ):
            self.assertIn(expected, rules)
        self.assertTrue(all(rule.startswith("Edit(//") for rule in rules))
        self.assertNotIn(f"Edit(/{top}/fastlane/**/**)", rules)
        self.assertFalse(any("keystore" in rule or ".jks" in rule for rule in rules))  # Android paths are not this repo's
        self.assertEqual(settingsmerge.read_owned_rules(self.ios), rules)
        self.assertEqual(len(self.settings(self.ios)["hooks"]["SessionStart"]), 1)  # the hook is still there, once

    def test_a_protected_path_added_in_the_team_repo_is_protected_after_the_next_sync_with_no_app_repo_change(self):
        self.instructions(self.ios)
        self.assertIn("Config/Secrets.xcconfig: not protected.", self.protected(self.ios, "Config/Secrets.xcconfig")[1])
        self.adopt(protected(("Config/**", "ios", "Build settings hold keys and endpoints."), ("gradle/**", "android", "Build logic.")))
        text = self.instructions(self.ios)
        self.assertIn("updated the protected path rules in .claude/settings.local.json", self.sync_out)
        self.assertIn("- Permissions changed: protected paths", self.sync_out)
        code, out, err = self.protected(self.ios, "Config/Secrets.xcconfig", "gradle/libs.versions.toml", "Sources/App.swift")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out.splitlines(),
            [
                "Config/Secrets.xcconfig: protected by Config/** (from the team repo). Build settings hold keys and endpoints. Ask the developer before changing it.",
                "gradle/libs.versions.toml: not protected.",
                "Sources/App.swift: not protected.",
            ],
        )
        self.assertIn(f"Edit(/{os.fspath(self.ios)}/Config/**)", self.ask(self.ios))
        section = text[text.index("## Protected paths") :]
        self.assertIn("- `Config/**`: Build settings hold keys and endpoints.", section)
        self.assertNotIn("gradle/**", section)
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")  # nothing for an app repo pull request
        # Taken out of the team repo again, it is no longer protected, and the rule is gone.
        self.adopt(protected())
        self.instructions(self.ios)
        self.assertIn("Config/Secrets.xcconfig: not protected.", self.protected(self.ios, "Config/Secrets.xcconfig")[1])
        self.assertNotIn(f"Edit(/{os.fspath(self.ios)}/Config/**)", self.ask(self.ios))

    def test_a_second_sync_with_nothing_new_writes_nothing(self):
        self.instructions(self.ios)
        watched = [self.ios / ".claude/settings.local.json", self.ios / ".tugboat/permissions.json", self.ios / ".tugboat/settings-rules.json"]
        before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in watched]
        self.instructions(self.ios)
        self.assertEqual([(path.stat().st_ino, path.stat().st_mtime_ns) for path in watched], before)
        self.assertNotIn("protected path rules", self.sync_out)

    def test_the_developers_own_rules_and_approvals_are_kept(self):
        self.instructions(self.ios)
        settings = self.settings(self.ios)
        settings["permissions"]["allow"] = ["Bash(xcodebuild:*)"]
        settings["permissions"]["ask"].insert(0, "Edit(//somewhere/else/**)")
        settings["model"] = "x"
        (self.ios / ".claude/settings.local.json").write_text(json.dumps(settings), encoding="utf-8")
        self.adopt(protected(("Config/**", "ios", "Build settings.")))
        self.instructions(self.ios)
        after = self.settings(self.ios)
        self.assertEqual((after["permissions"]["allow"], after["model"]), (["Bash(xcodebuild:*)"], "x"))
        self.assertEqual(after["permissions"]["ask"][0], "Edit(//somewhere/else/**)")
        self.assertIn(f"Edit(/{os.fspath(self.ios)}/Config/**)", after["permissions"]["ask"])
        self.assertNotIn("Edit(//somewhere/else/**)", settingsmerge.read_owned_rules(self.ios))

    def test_the_agent_is_told_what_is_protected_and_that_it_cannot_change_the_list(self):
        text = self.instructions(self.ios)
        section = text[text.index("## Protected paths") :]
        for expected in (
            "Ask the developer before you create, change, move, or delete a file that one of these patterns covers.",
            "do not get around it with a shell command",
            "- `.github/workflows/**`: CI workflows decide what runs with the project's secrets.",
            "- `**/*.p12`: Signing certificates must not be changed by an agent.",
            "Tugboat's own files are protected too: `.tugboat/` apart from `.tugboat/capture/`",
            "To check a path, run `tug protected <path>`.",
            "You cannot take a path off it, and neither can the developer in a session.",
        ):
            self.assertIn(expected, section)
        self.assertNotIn("keystore", section)

    def test_tugboats_own_files_are_protected_and_the_draft_folder_is_not(self):
        self.instructions(self.ios)
        _, out, _ = self.protected(self.ios, ".tugboat/team/policies/ios/IOS-001.md", ".tugboat/state.json", ".claude/rules/tugboat.md", ".claude/skills/tug-capture-notes/SKILL.md", ".claude/settings.local.json", ".tugboat/capture/knowledge/auth/x.md", ".claude/rules/my-own.md")
        lines = out.splitlines()
        self.assertTrue(all("protected by" in line and "one of Tugboat's own files" in line for line in lines[:5]), out)
        self.assertEqual(lines[5:], [".tugboat/capture/knowledge/auth/x.md: not protected.", ".claude/rules/my-own.md: not protected."])

    def test_with_no_path_the_whole_list_is_printed_and_a_path_outside_the_repo_is_refused(self):
        self.instructions(self.ios)
        code, out, _ = self.protected(self.ios)
        self.assertEqual(code, 0)
        self.assertIn("- fastlane/** (from the team repo): Release automation controls signing and store uploads.", out)
        self.assertIn("- .tugboat/team/** (one of Tugboat's own files):", out)
        code, out, _ = self.protected(self.ios, "../outside.p12", "Signing/dist.p12")
        self.assertEqual(code, 1)
        self.assertIn("../outside.p12: ../outside.p12 is outside this repo", out)
        self.assertIn("Signing/dist.p12: protected by **/*.p12 (from the team repo).", out)

    def test_a_broken_list_in_the_team_repo_keeps_the_last_good_one_in_force(self):
        self.adopt(protected(("Config/**", "ios", "Build settings.")))
        self.instructions(self.ios)
        rules = self.ask(self.ios)
        self.adopt(("permissions/protected-paths.json", '{"schema": "tug.protected-paths/1", "paths": [{"pattern": "x"}]}\n'))
        text = self.instructions(self.ios)
        self.assertIn("Note: the protected paths permissions/protected-paths.json were not loaded:", self.sync_out)
        self.assertIn("protected paths of the last good list stay in force", self.sync_out)
        self.assertEqual(self.ask(self.ios), rules)
        self.assertIn("Config/Secrets.xcconfig: protected by Config/**", self.protected(self.ios, "Config/Secrets.xcconfig")[1])
        self.assertIn("- `Config/**`: Build settings.", text)

    def test_a_repo_whose_platform_is_not_known_gets_every_entry(self):
        compiled = permissions.compile_for(self.ios, None, [], ".claude/settings.local.json")
        team = [entry.pattern for entry in compiled.protected if entry.source == "team"]
        self.assertIn("**/*.p12", team)
        self.assertIn("**/*.jks", team)

    def test_a_tracked_settings_file_is_left_alone_and_the_compiled_list_is_still_written(self):
        other = self.app("tracked", {"Shop.xcodeproj/project.pbxproj": "x\n", ".claude/settings.local.json": "{}\n"}, "acme/shop-ios")
        code, out, err = self.tug(other, "sync", "--team-repo", "git@github.com:acme/team.git", "--app-repo", os.fspath(other))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("is tracked by Git in this repo, so Tugboat did not change it", out)
        self.assertEqual((other / ".claude/settings.local.json").read_text(encoding="utf-8"), "{}\n")
        self.assertIn("**/*.p12", [entry.pattern for entry in permissions.read_compiled(other).protected])
        self.assertEqual(settingsmerge.read_owned_rules(other), [])

    def test_before_the_first_sync_of_a_repo_the_command_says_what_to_do(self):
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "x\n"}, "acme/other")
        code, _, err = self.protected(other, "a.p12")
        self.assertEqual(code, 1)
        self.assertIn("this folder is not set up with Tugboat.", err)


if __name__ == "__main__":
    unittest.main()
