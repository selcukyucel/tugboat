import os
import unittest

from tugboat import formats, frontmatter, hook, rules, team_repo

from test_init import ANDROID_FILES, FIRST, TEAM, InitCase
import json


def rule(rule_id, title, *, mode="advisory", status="adopted", body=None):
    scope = {"COM": "company", "IOS": "ios", "AND": "android"}[rule_id[:3]]
    text = body or f"What {rule_id} says.\n\n## Why\n\nThe reason for {rule_id}.\n\n## How to check\n\nLook."
    return f"policies/{scope}/{rule_id}.md", frontmatter.render({"id": rule_id, "title": title, "scope": scope, "mode": mode, "status": status}, text)


class RuleCase(InitCase):
    """A project with an iOS repo and an Android repo in its merged profile."""

    def setUp(self):
        super().setUp()
        self.ok(self.ios, "--yes", "--team-repo", TEAM, *FIRST)
        self.merge()
        self.android = self.app("android", ANDROID_FILES, "acme/shop-android")
        self.ok(self.android, "--yes", "--team-repo", TEAM)
        self.merge()

    def adopt(self, *made):
        """A person merges a pull request that adds or changes these rules."""
        for path, text in made:
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(text, encoding="utf-8")
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        return self.push("README.md", f"changed {len(made)} at {len(self.branches())}\n")

    def instructions(self, root):
        code, out, err = self.tug(root, "sync")
        self.assertEqual((code, err), (0, ""), out)
        self.sync_out = out
        return (root / ".claude/rules/tugboat.md").read_text(encoding="utf-8")


class RulesForThePlatformTests(RuleCase):
    def test_an_ios_repo_gets_the_adopted_company_and_ios_rules_only_and_the_revision(self):
        revision = self.adopt(
            rule("COM-005", "Changes are small"),
            rule("IOS-005", "Views are previewed", mode="required"),
            rule("IOS-006", "Not adopted yet", status="example"),
            rule("AND-005", "Screens have a preview"),
        )
        text = self.instructions(self.ios)
        self.assertIn("This is an iOS app repo. The company rules and the iOS rules apply here. Android rules do not apply here.", text)
        self.assertIn(f"The rules come from team repo revision `{revision}`.", text)
        self.assertIn("- `COM-005` (advisory): Changes are small. What COM-005 says. File: `.tugboat/team/policies/company/COM-005.md`", text)
        self.assertIn("- `IOS-005` (required): Views are previewed. What IOS-005 says. File: `.tugboat/team/policies/ios/IOS-005.md`", text)
        for absent in ("AND-005", "IOS-006", "Not adopted yet", "The reason for", "IOS-001", "COM-001"):
            self.assertNotIn(absent, text)
        # The starter's examples in scope: four company rules, four iOS rules, and IOS-006.
        self.assertIn("9 example rules are in the copy. An example rule shows the format. It is not applied", text)
        for line in ("mode `advisory` does not block the work", "mode `required` blocks the work", "do not grant an exception yourself", "name the team repo revision given above"):
            self.assertIn(line, text)
        for path in (".tugboat/team/policies/company/COM-005.md", ".tugboat/team/policies/ios/IOS-005.md"):
            self.assertTrue((self.ios / path).is_file())

    def test_an_android_repo_gets_the_android_rules_instead(self):
        self.adopt(rule("COM-005", "Changes are small"), rule("IOS-005", "Views are previewed"), rule("AND-005", "Screens have a preview"))
        text = self.instructions(self.android)
        self.assertIn("This is an Android app repo.", text)
        self.assertIn("iOS rules do not apply here.", text)
        self.assertIn("- `AND-005` (advisory): Screens have a preview.", text)
        self.assertIn("- `COM-005` (advisory)", text)
        self.assertNotIn("IOS-005", text)

    def test_with_no_adopted_rule_the_agent_is_told_there_is_nothing_to_apply(self):
        text = self.instructions(self.ios)
        self.assertIn("No rule is adopted for this repo yet, so there is no rule to apply.", text)
        self.assertIn("8 example rules are in the copy.", text)

    def test_a_mode_changes_only_through_a_merge_never_through_a_local_edit(self):
        path, text = rule("IOS-005", "Views are previewed")
        self.adopt((path, text))
        self.assertIn("- `IOS-005` (advisory)", self.instructions(self.ios))
        clone = self.tmp / "home/team-repos/github.com/acme/team"
        (clone / path).write_text(text.replace("mode: advisory", "mode: required"), encoding="utf-8")
        self.assertIn("- `IOS-005` (advisory)", self.instructions(self.ios))
        self.git(clone, "checkout", "--quiet", "--", path)
        self.adopt(rule("IOS-005", "Views are previewed", mode="required"))
        self.assertIn("- `IOS-005` (required)", self.instructions(self.ios))
        self.assertIn("- Rule changed: IOS-005 \"Views are previewed\" (.tugboat/team/policies/ios/IOS-005.md)", self.sync_out)

    def test_a_rule_file_that_cannot_be_read_is_reported_and_not_applied(self):
        self.adopt(rule("IOS-005", "Views are previewed"), ("policies/ios/IOS-007.md", "no header\n"), ("policies/ios/IOS-008.md", rule("IOS-009", "Wrong name")[1]))
        text = self.instructions(self.ios)
        self.assertIn("- `IOS-005` (advisory)", text)
        self.assertIn("Not loaded: the rule file policies/ios/IOS-007.md was not loaded: the file must start with a '---' header", text)
        self.assertIn("Not loaded: the rule file policies/ios/IOS-008.md was not loaded: must be named IOS-009.md, after its id", text)
        self.assertNotIn("Wrong name", text)
        self.assertIn("Note: the rule file policies/ios/IOS-007.md was not loaded", self.sync_out)

    def test_a_repo_that_is_not_in_the_profile_gets_the_company_rules_and_is_told_why(self):
        self.adopt(rule("COM-005", "Changes are small"), rule("IOS-005", "Views are previewed"))
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "x\n"}, "acme/not-registered")
        code, _, _ = self.tug(other, "sync", "--team-repo", TEAM, "--app-repo", ".")
        self.assertEqual(code, 0)
        text = (other / ".claude/rules/tugboat.md").read_text(encoding="utf-8")
        self.assertIn("This repo is not in the project profile, so its platform is not known. Only the company rules are listed.", text)
        self.assertIn("- `COM-005` (advisory)", text)
        self.assertNotIn("IOS-005", text)

    def test_the_session_start_report_points_to_the_instruction_file_and_lists_a_new_rule(self):
        self.instructions(self.ios)
        self.adopt(rule("IOS-005", "Views are previewed"))
        text = json.loads(hook.session_start(json.dumps({"cwd": os.fspath(self.ios)})))["additionalContext"]
        self.assertIn('- Rule added: IOS-005 "Views are previewed" (.tugboat/team/policies/ios/IOS-005.md)', text)
        self.assertIn("`.claude/rules/tugboat.md` says how to use them.", text)
        self.assertIn("- `IOS-005` (advisory)", (self.ios / ".claude/rules/tugboat.md").read_text(encoding="utf-8"))


class RuleTextTests(unittest.TestCase):
    def test_the_statement_is_the_text_above_the_first_heading_on_one_line(self):
        self.assertEqual(rules.statement("New tests use\nSwift Testing.\n\nOld ones stay.\n\n## Why\n\nBecause.\n"), "New tests use Swift Testing. Old ones stay.")
        long = rules.statement("word " * 400)
        self.assertEqual((len(long), long[-1]), (rules.MAX_STATEMENT, "…"))

    def test_the_starter_set_has_four_advisory_examples_for_each_scope_and_no_other_scope(self):
        layout = team_repo.starter_layout()
        found = {}
        for path, text in layout.items():
            parts = path.split("/")
            if parts[0] == "policies" and len(parts) == 3 and parts[2] != "README.md":
                checked = formats.validate_rule(*frontmatter.parse(text))
                self.assertEqual((checked["mode"], checked["status"], checked["scope"], f"{checked['id']}.md"), ("advisory", "example", parts[1], parts[2]))
                for heading in ("## Why", "## How to check"):
                    self.assertIn(heading, checked["text"])
                self.assertLess(len(rules.statement(checked["text"])), 400)
                found.setdefault(parts[1], []).append(checked["id"])
        self.assertEqual({scope: len(ids) for scope, ids in found.items()}, {"company": 4, "ios": 4, "android": 4})
        readme = layout["policies/README.md"]
        for expected in ("Every rule here starts as an advisory example.", "set `status: adopted`", "set `mode: required`", "No backend rules"):
            self.assertIn(expected, readme)


if __name__ == "__main__":
    unittest.main()
