import unittest
from pathlib import Path

from tugboat import team_repo

from test_rules import RuleCase

README = Path(__file__).resolve().parents[1] / "README.md"
PAGE = "permissions/hook-limits.md"

# The five limits the documentation must state.
REQUIRED = (
    "A Copilot hook that passes its time limit lets the action through, and no setting changes that.",
    "Copilot CLI loads no hook from a folder it does not trust.",
    "VS Code runs no hook in a workspace it does not trust.",
    "Hooks in VS Code are a preview feature there.",
    "They are not a security boundary.",
)


class LimitsPageTests(unittest.TestCase):
    def setUp(self):
        self.page = team_repo.starter_layout()[PAGE]
        self.readme = README.read_text(encoding="utf-8")

    def test_the_page_states_the_five_limits_and_so_does_the_readme(self):
        for sentence in REQUIRED:
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, self.page)
                self.assertIn(sentence, self.readme)

    def test_the_readme_and_the_team_repo_page_say_the_same(self):
        lines = [line for line in self.page.splitlines() if line.startswith("- ")]
        self.assertGreater(len(lines), 15)
        for line in lines:
            with self.subTest(line=line[:60]):
                self.assertIn(line, self.readme)
        self.assertIn("## Limits of the checks", self.readme)

    def test_every_limit_says_how_it_is_known(self):
        section = self.page.split("## What does give a hard boundary")[0]
        limits = [line for line in section.splitlines() if line.startswith("- ")]
        unmarked = [line for line in limits if not any(mark in line for mark in ("(Seen.)", "(Documented.)", "(By design.)", "(Documented. Seen once")) and "That is intended" not in line]
        self.assertEqual(unmarked, [])

    def test_the_page_names_what_gives_a_hard_boundary(self):
        for expected in ("Branch protection in GitHub", "A secret scan in CI or at push time", "Review of every pull request"):
            self.assertIn(expected, self.page)


class LimitsInTheRepoTests(RuleCase):
    def test_the_page_reaches_the_app_repo_and_the_agent_is_told_not_to_rely_on_the_check(self):
        text = self.instructions(self.ios)
        self.assertTrue((self.ios / ".tugboat/team" / PAGE).is_file())
        section = text[text.index("## Protected paths") :]
        self.assertIn("It is a guard, not a boundary, and it does not see everything: `.tugboat/team/permissions/hook-limits.md` says what it misses.", section)
        self.assertIn("Do not count on it to stop a mistake.", section)
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 0, out)

    def test_a_team_repo_without_the_page_gets_no_pointer_to_it(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        self.git(self.work, "rm", "--quiet", PAGE)
        self.git(self.work, "commit", "--quiet", "-m", "no limits page")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        text = self.instructions(self.ios)
        self.assertNotIn("hook-limits.md", text)
        self.assertIn("## Protected paths", text)


if __name__ == "__main__":
    unittest.main()
