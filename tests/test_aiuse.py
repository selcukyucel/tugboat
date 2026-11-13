import unittest

from tugboat import aiuse, formats, frontmatter, team_repo
from tugboat.errors import DataError

from test_rules import RuleCase

BODY = "How we work.\n\n## Data that must never enter a prompt\n\nNo user data.\n\n## Accounts\n\nCompany accounts only.\n\n## Accountability\n\nThe author answers for the pull request."


def page(status="adopted", body=BODY, **fields):
    return "policies/ai-use.md", frontmatter.render({"title": "How we use AI tools", "status": status, **fields}, body)


class FormatTests(unittest.TestCase):
    def check(self, status="adopted", body=BODY, **fields):
        return formats.validate_ai_use(*frontmatter.parse(page(status, body, **fields)[1]))

    def test_a_page_with_the_three_sections_is_accepted(self):
        self.assertEqual((self.check()["status"], self.check("example")["title"]), ("adopted", "How we use AI tools"))

    def test_each_of_the_three_sections_must_be_there(self):
        for heading in formats.AI_USE_HEADINGS:
            with self.subTest(heading=heading), self.assertRaisesRegex(DataError, f"is missing sections: ## {heading}"):
                self.check(body=BODY.replace(f"## {heading}", "## Something else"))

    def test_it_is_one_page_with_a_known_status_and_no_other_field(self):
        for options, message in (
            ({"body": BODY + "\n" + "x" * formats.AI_USE_MAX}, "text cannot exceed 8000 characters"),
            ({"status": "draft"}, "status must be one of: adopted, example"),
            ({"mode": "required"}, "has unknown fields: mode"),
        ):
            with self.subTest(options=list(options)), self.assertRaisesRegex(DataError, message):
                self.check(**options)

    def test_the_starter_page_is_an_example_that_says_the_three_things(self):
        checked = formats.validate_ai_use(*frontmatter.parse(team_repo.starter_layout()["policies/ai-use.md"]))
        self.assertEqual(checked["status"], "example")
        for said in ("Personal data of real users", "Only company accounts are used", "The developer who opens a pull request is accountable for every line in it", "never approves and never merges"):
            self.assertIn(said, checked["text"])


class PolicyInTheSessionTests(RuleCase):
    NOT_ADOPTED = "Setup is not finished: the team has not adopted its AI use policy yet."

    def test_an_example_page_is_not_put_in_front_of_the_agent_and_the_report_says_what_is_missing(self):
        text = self.instructions(self.ios)
        self.assertIn("## AI use policy\n\nThe team repo holds an example AI use policy that the team has not adopted. It is not in force, so it is not shown here.", text)
        self.assertNotIn("Personal data of real users", text)
        self.assertIn(self.NOT_ADOPTED, self.sync_out)
        self.assertIn("A pull request in the team repo that sets `status: adopted` in `policies/ai-use.md` puts it in force.", self.sync_out)

    def test_an_adopted_page_is_in_the_instruction_file_of_every_repo_whatever_its_platform(self):
        self.adopt(page())
        for root in (self.ios, self.android):
            text = self.instructions(root)
            self.assertIn("The team adopted this policy. It is in force in every session. It is the page `.tugboat/team/policies/ai-use.md`", text)
            self.assertIn("You cannot make an exception to it yourself.", text)
            self.assertIn("### How we use AI tools\n\nHow we work.\n\n### Data that must never enter a prompt\n\nNo user data.", text)
            self.assertIn("### Accountability\n\nThe author answers for the pull request.", text)
            self.assertNotIn("\n## Accounts", text)  # the page's headings sit under the section
            self.assertNotIn("AI use policy", self.sync_out.split("Agent tool files")[1])
        self.assertIn("- AI use policy changed (.tugboat/team/policies/ai-use.md)", self.sync_out)

    def test_a_changed_page_reaches_the_agent_at_the_next_sync_and_a_local_edit_never_does(self):
        self.adopt(page())
        self.instructions(self.ios)
        clone = self.tmp / "home/team-repos/github.com/acme/team"
        (clone / "policies/ai-use.md").write_text(page(body=BODY.replace("No user data.", "Anything goes."))[1], encoding="utf-8")
        self.assertNotIn("Anything goes.", self.instructions(self.ios))
        self.git(clone, "checkout", "--quiet", "--", "policies/ai-use.md")
        self.adopt(page(body=BODY.replace("No user data.", "No user data and no keys.")))
        self.assertIn("No user data and no keys.", self.instructions(self.ios))

    def test_a_broken_page_is_not_loaded_and_is_named(self):
        self.adopt(page(body="No sections here."))
        text = self.instructions(self.ios)
        self.assertIn("No AI use policy is in force. Not loaded: the AI use policy policies/ai-use.md was not loaded: AI use policy is missing sections:", text)
        self.assertIn("Note: the AI use policy policies/ai-use.md was not loaded", self.sync_out)
        self.assertNotIn("No sections here.", text)

    def test_a_team_repo_without_the_page(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        self.git(self.work, "rm", "--quiet", "policies/ai-use.md")
        self.push("README.md", "no policy\n")
        text = self.instructions(self.ios)
        self.assertIn("The team repo has no AI use policy, so none is in force.", text)
        self.assertIn("Setup is not finished: the team repo has no AI use policy.", self.sync_out)
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 0)
        self.assertIn("warning: policies/ai-use.md: is not there. The team has no AI use policy.", out)

    def test_the_team_repo_check_refuses_a_broken_page(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        (self.work / "policies/ai-use.md").write_text(page(body="No sections here.")[1], encoding="utf-8")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 1)
        self.assertIn("policies/ai-use.md: AI use policy is missing sections: ## Data that must never enter a prompt, ## Accounts, ## Accountability", out)


class StateTests(unittest.TestCase):
    def test_only_an_adopted_page_is_in_force(self):
        self.assertTrue(aiuse.Policy("adopted", "t", "x").in_force)
        for state in ("example", "missing", "broken"):
            self.assertFalse(aiuse.Policy(state).in_force)
        self.assertIsNone(aiuse.unfinished(aiuse.Policy("adopted", "t", "x")))
        self.assertIsNone(aiuse.unfinished(aiuse.Policy("broken", problem="p")))  # reported as a problem instead


if __name__ == "__main__":
    unittest.main()
