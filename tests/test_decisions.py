import datetime
import textwrap
import unittest
from unittest import mock

from tugboat import decisions, formats, frontmatter
from tugboat.errors import DataError

from test_formats import DECISION, DECISION_BODY
from test_knowledge import GITHUB_TOKEN
from test_rules import RuleCase

TODAY = datetime.date(2026, 11, 14)
BODY = "## Context\n\nXCTest macros are verbose.\n\n## Options\n\nXCTest, or Swift Testing.\n\n## Choice\n\nSwift Testing for new unit tests.\n\n## Reason\n\nShorter tests, and parameters.\n"


def record(number, title, *, status="accepted", repo="acme/shop-ios", paths=("Tests/**",), body=BODY, day="2026-11-14", slug=None):
    header = {"id": f"DEC-{number:04}", "title": title, "date": day, "status": status}
    if repo:
        header["repo"] = repo
    header["paths"] = list(paths)
    name = f"DEC-{number:04}-{slug or title.lower().replace(' ', '-')}.md"
    return name, frontmatter.render(header, body)


def team(made):
    name, text = made
    return f"decisions/{name}", text


class FormatTests(unittest.TestCase):
    def test_a_record_may_name_one_repo_or_none(self):
        self.assertIsNone(formats.validate_decision(dict(DECISION), DECISION_BODY)["repo"])
        self.assertEqual(formats.validate_decision({**DECISION, "repo": "acme/shop-ios"}, DECISION_BODY)["repo"], "acme/shop-ios")
        with self.assertRaisesRegex(DataError, "written as owner/name"):
            formats.validate_decision({**DECISION, "repo": "shop-ios"}, DECISION_BODY)
        with self.assertRaisesRegex(DataError, "unknown fields: owner"):
            formats.validate_decision({**DECISION, "owner": "@pat"}, DECISION_BODY)

    def test_a_record_with_a_secret_pattern_fails_and_the_message_does_not_repeat_it(self):
        with self.assertRaises(DataError) as caught:
            formats.validate_decision(dict(DECISION), DECISION_BODY + f"\nThe key is {GITHUB_TOKEN}\n")
        self.assertIn("decision record holds what looks like a secret (GitHub token).", str(caught.exception))
        self.assertNotIn(GITHUB_TOKEN, str(caught.exception))


class DecisionCase(RuleCase):
    def setUp(self):
        super().setUp()
        clock = mock.patch.object(decisions, "today", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)

    def section(self, root):
        text = self.instructions(root)
        return text[text.index("## Decision records") :]

    def write(self, made, root=None):
        name, text = made
        file = (root or self.ios) / ".tugboat/capture/decisions" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return name

    def decision(self, *argv, root=None, answers=None):
        return self.tug(root or self.ios, "decision", *argv, answers=answers)

    def status(self, root=None):
        code, out, err = self.decision("status", root=root)
        self.assertEqual((code, err), (0, ""))
        return out

    def branch(self):
        return [name for name in self.proposals() if name.startswith("tug/decision-")][-1]


class RecordsInForceTests(DecisionCase):
    def test_the_agent_gets_the_accepted_records_that_hold_in_its_repo(self):
        self.adopt(
            team(record(1, "Use Swift Testing")),
            team(record(2, "Money is an integer of cents", repo=None, paths=())),
            team(record(3, "Use Compose previews", repo="acme/shop-android", paths=("app/**",))),
            team(record(4, "Use Combine", status="superseded")),
            team(record(5, "Maybe use TCA", status="proposed")),
        )
        section = self.section(self.ios)
        for expected in (
            "2 accepted decision records hold in this repo.",
            "- `DEC-0001`: Use Swift Testing. Affects: `Tests/**`. File: `.tugboat/team/decisions/DEC-0001-use-swift-testing.md`",
            "- `DEC-0002`: Money is an integer of cents. Affects: no one place. Holds for the whole project. File: `.tugboat/team/decisions/DEC-0002-money-is-an-integer-of-cents.md`",
            "2 more records are proposed or superseded, and are not in force.",
            "Follow its choice.",
            "Do not decide against a record yourself.",
            "draft a decision record with the skill `tug-decision-record`",
            "A record reaches the team repo only as a pull request",
        ):
            self.assertIn(expected, section)
        for absent in ("DEC-0003", "DEC-0004", "DEC-0005", "XCTest macros"):
            self.assertNotIn(absent, section)
        android = self.section(self.android)
        self.assertIn("- `DEC-0003`: Use Compose previews. Affects: `app/**`.", android)
        self.assertIn("- `DEC-0002`", android)
        self.assertNotIn("DEC-0001", android)
        self.assertIn("- Decision record added: DEC-0001-use-swift-testing", self.sync_out)

    def test_with_no_record_the_agent_is_still_told_when_to_draft_one(self):
        section = self.section(self.ios)
        self.assertIn("No accepted decision record holds in this repo yet.", section)
        self.assertIn("When a task makes a technology or architecture choice", section)

    def test_a_record_that_is_not_valid_is_named_and_not_given_and_the_check_warns_about_an_unknown_repo(self):
        text = record(1, "Use Swift Testing")[1]
        self.adopt(("decisions/DEC-0001-broken.md", text.replace("## Reason", "## Because")), team(record(2, "A typo", repo="acme/shop-iso")))
        section = self.section(self.ios)
        self.assertIn("No accepted decision record holds in this repo yet.", section)
        self.assertIn("Note: the decision record decisions/DEC-0001-broken.md was not loaded: decision record is missing sections: ## Reason.", self.sync_out)
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 1)
        self.assertIn("warning: decisions/DEC-0002-a-typo.md: is about the repo acme/shop-iso, which is not in the project profile. No agent will get it.", out)


class DraftTests(DecisionCase):
    def setUp(self):
        super().setUp()
        self.instructions(self.ios)

    def test_with_no_draft_the_agent_is_told_where_one_goes_and_the_next_free_id(self):
        self.assertEqual(
            self.status().splitlines(),
            ["No draft decision record. A draft is a file under .tugboat/capture/decisions/, such as .tugboat/capture/decisions/DEC-0001-short-name.md.", "The next free ID is DEC-0001."],
        )
        self.adopt(team(record(7, "Use Swift Testing")))
        self.instructions(self.ios)
        self.assertIn("The next free ID is DEC-0008.", self.status())

    def test_a_drafted_record_reaches_the_team_repo_as_a_pull_request_and_nowhere_else(self):
        name = self.write(record(1, "Use Swift Testing"))
        self.assertEqual(
            self.status().splitlines(),
            [
                "Draft decision records in .tugboat/capture/decisions/:",
                '- DEC-0001-use-swift-testing.md: ready to propose. "Use Swift Testing" (status accepted; affects Tests/**)',
                "The next free ID is DEC-0002.",
            ],
        )
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")  # the app repo's Git sees no draft
        main = self.git(self.remote, "rev-parse", "main")
        before = len(self.proposals())
        code, out, err = self.decision("propose", name, "--yes")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(len(self.proposals()), before + 1)
        branch = self.branch()
        self.assertTrue(branch.startswith("tug/decision-dec-0001-"), branch)
        self.assertEqual(self.git(self.remote, "diff", "--name-only", "main", branch), "decisions/DEC-0001-use-swift-testing.md")
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), main)  # nothing was merged
        message = self.git(self.remote, "log", "-1", "--format=%B", branch)
        self.assertIn("Add decision record DEC-0001: Use Swift Testing", message)
        self.assertIn("- Adds `decisions/DEC-0001-use-swift-testing.md`: Use Swift Testing. Status: accepted. About the repo acme/shop-ios. Affects: `Tests/**`.", message)
        self.assertIn("Nothing was merged. A person reviews and merges it in GitHub.", out)
        # The draft is gone, the app repo is clean, and the copy does not have the record before a merge.
        self.assertFalse((self.ios / ".tugboat/capture").exists())
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")
        self.assertFalse((self.ios / ".tugboat/team/decisions" / name).exists())
        self.assertNotIn("DEC-0001", self.section(self.ios))
        # After a person merges it, it is in force.
        self.merge(branch)
        self.assertIn("- `DEC-0001`: Use Swift Testing.", self.section(self.ios))

    def test_a_draft_that_is_not_valid_is_named_with_the_reason_and_is_not_proposed(self):
        self.adopt(team(record(1, "Use Swift Testing")))
        self.instructions(self.ios)
        self.write(record(1, "Use Kingfisher", slug="kingfisher"))
        self.write(record(2, "Other repo", repo="acme/shop-android"))
        self.write(record(3, "From the future", day="2026-11-15"))
        self.write(("DEC-0004-no-reason.md", record(4, "No reason")[1].replace("## Reason", "## Because")))
        self.write(("notes.md", record(5, "Bad name")[1]))
        self.write(("DEC-0006-wrong-id.md", record(7, "Wrong id")[1]))
        self.write(("DEC-0008-leak.md", record(8, "Leak", body=BODY + f"\nToken: {GITHUB_TOKEN}\n")[1]))
        out = self.status()
        for expected in (
            "- DEC-0001-kingfisher.md: not valid. uses DEC-0001, which decisions/DEC-0001-use-swift-testing.md in the team repo already uses",
            "- DEC-0002-other-repo.md: not valid. is about the repo acme/shop-android. A record drafted here is about acme/shop-ios, or names no repo and holds for the whole project.",
            "- DEC-0003-from-the-future.md: not valid. has the date 2026-11-15, which is after today",
            "- DEC-0004-no-reason.md: not valid. decision record is missing sections: ## Reason",
            "- notes.md: not valid. a record's file name is its ID, a hyphen, and a short name in small letters",
            "- DEC-0006-wrong-id.md: not valid. must have a file name that starts with DEC-0007",
            "- DEC-0008-leak.md: not valid. decision record holds what looks like a secret (GitHub token).",
            "The next free ID is DEC-0009.",
        ):
            self.assertIn(expected, out)
        self.assertNotIn(GITHUB_TOKEN, out)
        before = self.proposals()
        code, _, err = self.decision("propose", "DEC-0001-kingfisher.md", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("DEC-0001-kingfisher.md is not valid, so it cannot be proposed", err)
        code, _, err = self.decision("propose", "DEC-0099-nope.md", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("there is no draft named DEC-0099-nope.md", err)
        self.assertEqual(self.proposals(), before)

    def test_a_record_for_the_whole_project_and_two_records_in_one_pull_request(self):
        first = self.write(record(1, "Money is an integer of cents", repo=None, paths=()))
        second = self.write(record(2, "Use Swift Testing"))
        code, out, _ = self.decision("propose", first, f".tugboat/capture/decisions/{second}", "--yes")
        self.assertEqual(code, 0, out)
        branch = self.branch()
        self.assertEqual(self.git(self.remote, "diff", "--name-only", "main", branch).splitlines(), [f"decisions/{first}", f"decisions/{second}"])
        message = self.git(self.remote, "log", "-1", "--format=%B", branch)
        self.assertIn("Add 2 decision records: DEC-0001, DEC-0002", message)
        self.assertIn("About the whole project. Affects: no one place.", message)

    def test_an_older_record_is_superseded_by_a_draft_with_its_own_file_name(self):
        made = record(1, "Use Combine")
        self.adopt(team(made))
        self.instructions(self.ios)
        self.write(made)
        self.assertIn("- DEC-0001-use-combine.md: the team repo already has this record with the same content. Remove the draft.", self.status())
        code, _, err = self.decision("propose", made[0], "--yes")
        self.assertEqual(code, 1)
        self.assertIn("the team repo already has DEC-0001-use-combine.md with this content", err)
        self.write(record(1, "Use Combine", status="superseded"))
        self.assertIn("ready to propose. It changes a record that is in the team repo.", self.status())
        code, out, _ = self.decision("propose", made[0], "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("Change decision record DEC-0001: Use Combine", self.git(self.remote, "log", "-1", "--format=%s", self.branch()))

    def test_a_pull_request_needs_a_person_or_yes(self):
        name = self.write(record(1, "Use Swift Testing"))
        before = self.proposals()
        code, out, err = self.decision("propose", name)
        self.assertEqual(code, 1)
        self.assertIn("This opens one pull request in acme/team with: DEC-0001-use-swift-testing.md", out)
        self.assertIn("a pull request needs a person to confirm it.", err)
        code, _, err = self.decision("propose", name, answers=["n"])
        self.assertEqual(code, 1)
        self.assertEqual(self.proposals(), before)
        self.assertTrue((self.ios / ".tugboat/capture/decisions" / name).is_file())
        code, out, _ = self.decision("propose", name, answers=["yes"])
        self.assertEqual(code, 0, out)

    def test_the_skill_ships_with_the_tool_and_its_example_is_a_valid_record(self):
        skill = (self.ios / ".claude/skills/tug-decision-record/SKILL.md").read_text(encoding="utf-8")
        fields, body = frontmatter.parse(skill)
        self.assertEqual(formats.validate_skill_header(fields)["name"], "tug-decision-record")
        for expected in ("tug decision status", "tug decision propose <file name> --yes", ".tugboat/capture/decisions/", "The developer makes the choice."):
            self.assertIn(expected, body)
        example = formats.validate_decision(*frontmatter.parse(textwrap.dedent(skill.split("```\n")[1])))
        self.assertEqual((example["id"], example["status"], example["repo"]), ("DEC-0003", "accepted", "owner/name"))


if __name__ == "__main__":
    unittest.main()
