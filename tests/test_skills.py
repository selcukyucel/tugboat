import json
import unittest

from tugboat import formats

from test_rules import RuleCase, rule


def skill(name, body):
    return f"skills/{name}/SKILL.md", f"---\nname: {name}\ndescription: Use it for {name}.\n---\n\n{body}\n"


class RuleIdTests(unittest.TestCase):
    def test_the_rule_ids_a_text_names_each_once_in_order(self):
        text = "Applies `IOS-005`, IOS-006 and COM-001.\nSee IOS-005 again. Not IDs: IOS-05, IOS-0055, XIOS-001, DEC-0001, ios-001, AND-01."
        self.assertEqual(formats.rule_ids_named(text), ["IOS-005", "IOS-006", "COM-001"])
        self.assertEqual(formats.rule_ids_named("No rule here."), [])
        self.assertEqual([formats.rule_scope(rule_id) for rule_id in ("COM-001", "IOS-001", "AND-001")], ["company", "ios", "android"])

    def test_a_skill_is_for_the_one_platform_whose_rules_it_names(self):
        for text, expected in (
            ("IOS-005 and IOS-006", {"ios"}),
            ("IOS-005 and COM-001", {"ios"}),
            ("AND-005", {"android"}),
            ("IOS-007 in the iOS repo, AND-007 in the Android repo", set()),
            ("COM-001 only", set()),
            ("no rule", set()),
        ):
            with self.subTest(text=text):
                self.assertEqual(formats.skill_platforms(text), frozenset(expected))


class SkillsForThePlatformTests(RuleCase):
    def skills(self, root):
        folder = root / ".claude/skills"
        return sorted(item.name for item in folder.iterdir() if not item.name.startswith("tug-"))

    def test_a_skill_is_installed_only_where_its_rules_apply(self):
        self.adopt(
            rule("IOS-005", "Screens the iOS way"),
            rule("AND-005", "Screens the Android way"),
            rule("COM-005", "Changes are small"),
            skill("new-swiftui-screen", "## Rules this skill applies\n\n- `IOS-005`\n- `COM-005`"),
            skill("new-compose-screen", "Applies AND-005."),
            skill("unit-tests", "IOS-005 in the iOS repo, AND-005 in the Android repo."),
            skill("small-changes", "Applies COM-005."),
            skill("no-rules", "Steps."),
        )
        self.instructions(self.ios)
        self.instructions(self.android)
        self.assertEqual(self.skills(self.ios), ["new-swiftui-screen", "no-rules", "small-changes", "unit-tests"])
        self.assertEqual(self.skills(self.android), ["new-compose-screen", "no-rules", "small-changes", "unit-tests"])
        self.assertNotIn("Note:", self.sync_out)  # a skill for the other platform is not a problem
        for root in (self.ios, self.android):
            self.assertEqual(self.git(root, "status", "--porcelain"), "")
            self.assertTrue((root / ".claude/skills/tug-capture-notes/SKILL.md").is_file())  # the tool's own skills go everywhere

    def test_a_skill_that_changes_platform_is_removed_from_the_repo_it_left(self):
        self.adopt(rule("IOS-005", "Screens the iOS way"), rule("AND-005", "Screens the Android way"), skill("new-screen", "Steps."))
        self.instructions(self.ios)
        self.instructions(self.android)
        self.assertEqual((self.skills(self.ios), self.skills(self.android)), (["new-screen"], ["new-screen"]))
        self.adopt(skill("new-screen", "Applies IOS-005."))
        self.instructions(self.ios)
        self.instructions(self.android)
        self.assertEqual(self.skills(self.ios), ["new-screen"])
        self.assertEqual(self.skills(self.android), [])
        self.assertIn("removed .claude/skills/new-screen/SKILL.md", self.sync_out)
        self.assertEqual(self.git(self.android, "status", "--porcelain"), "")

    def check(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        return self.tug(self.work, "team", "check", ".")

    def test_the_check_refuses_a_rule_id_that_does_not_exist_and_warns_about_the_rest(self):
        self.adopt(
            rule("IOS-005", "Screens the iOS way"),
            rule("IOS-006", "Not adopted", status="example"),
            skill("good", "Applies IOS-005."),
            skill("typo", "Applies IOS-050 and IOS-005."),
            skill("example", "Applies IOS-006."),
            skill("none", "Steps."),
        )
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("skills/typo/SKILL.md: names rule IOS-050, which is not in policies/\n", out)
        self.assertIn("warning: skills/example/SKILL.md: names rule IOS-006, which is an example. An example rule is not applied.", out)
        self.assertIn("warning: skills/none/SKILL.md: names no rule ID. A skill names the rules it applies, such as IOS-001.", out)
        self.assertNotIn("skills/good", out)
        self.assertEqual(out.count("skills/typo"), 1)

    def test_the_check_warns_about_a_skill_for_a_platform_the_project_does_not_have(self):
        # Take the Android repo out of the profile, as a project with iOS only would be.
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        profile = json.loads((self.work / "profile.json").read_text(encoding="utf-8"))
        profile["app_repos"] = [repo for repo in profile["app_repos"] if repo["platform"] == "ios"]
        (self.work / "profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        self.adopt(rule("AND-005", "Screens the Android way"), skill("new-compose-screen", "Applies AND-005."))
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("warning: skills/new-compose-screen/SKILL.md: names only android rules, and the project profile has no android repo. No agent will get the skill.", out)


if __name__ == "__main__":
    unittest.main()
