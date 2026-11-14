import datetime
import json
import textwrap
import unittest
from unittest import mock

from tugboat import capture, formats, frontmatter

from test_knowledge import GITHUB_TOKEN, note
from test_rules import RuleCase

TODAY = datetime.date(2026, 11, 14)


def draft(title, paths, *, repo="acme/shop-ios", owner="@pat", body=None, verified="2026-11-14"):
    header = {"title": title, "owner": owner, "repo": repo, "paths": paths, "last_verified": verified}
    return frontmatter.render(header, body or f"What we found out about {title}.")


class CaptureCase(RuleCase):
    def setUp(self):
        super().setUp()
        clock = mock.patch.object(capture, "today", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        self.instructions(self.ios)

    def write(self, name, text, root=None):
        file = (root or self.ios) / ".tugboat/capture/knowledge" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return file

    def capture(self, *argv, root=None, answers=None):
        return self.tug(root or self.ios, "capture", *argv, answers=answers)

    def status(self, root=None):
        code, out, err = self.capture("status", root=root)
        self.assertEqual((code, err), (0, ""))
        return out

    def lines(self, root=None):
        return {line[2:].split(":")[0]: line.split(": ", 1)[1] for line in self.status(root).split("\n\n")[0].splitlines() if line.startswith("- ")}

    def notes_branch(self):
        return [branch for branch in self.proposals() if branch.startswith("tug/notes")][-1]

    def proposed_files(self, branch=None):
        return self.git(self.remote, "diff", "--name-only", "main", branch or self.notes_branch()).splitlines()


class DraftTests(CaptureCase):
    def test_with_no_draft_the_agent_is_told_where_a_draft_goes(self):
        self.assertEqual(self.status().strip(), "No draft note. A draft is a note file under .tugboat/capture/knowledge/, such as .tugboat/capture/knowledge/auth/token-refresh.md.")

    def test_each_draft_is_listed_with_where_it_stands_and_git_sees_none(self):
        self.write("auth/token-refresh.md", draft("How login tokens are refreshed", ["Sources/Auth/**"]))
        self.write("checkout/tax.md", draft("Tax is rounded per line", ["Sources/Checkout/**"]))
        self.assertEqual(
            self.status().splitlines(),
            [
                "Drafts in .tugboat/capture/knowledge/:",
                '- auth/token-refresh.md: new. Not accepted and not dropped yet. "How login tokens are refreshed" (covers Sources/Auth/**)',
                '- checkout/tax.md: new. Not accepted and not dropped yet. "Tax is rounded per line" (covers Sources/Checkout/**)',
            ],
        )
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")

    def test_a_draft_that_is_not_a_valid_note_is_named_with_the_reason(self):
        self.write("a.md", "no header\n")
        self.write("b.md", draft("Other repo", ["app/**"], repo="acme/shop-android"))
        self.write("c.md", draft("From the future", ["Sources/**"], verified="2026-11-15"))
        self.write("d.md", draft("A leak", ["Sources/**"], body=f"The token is {GITHUB_TOKEN}"))
        self.write("e.md", draft("A chat", ["Sources/**"], body="User: a\nAssistant: b\nUser: c\nAssistant: d"))
        self.write("Bad Name.md", draft("Bad name", ["Sources/**"]))
        self.write("README.md", draft("Readme", ["Sources/**"]))
        self.write("f.txt", "text")
        found = self.lines()
        self.assertEqual(found["a.md"], "not valid. the file must start with a '---' header")
        self.assertEqual(found["b.md"], "not valid. is about the repo acme/shop-android. A note captured here is about acme/shop-ios.")
        self.assertEqual(found["c.md"], "not valid. has last_verified 2026-11-15, which is after today")
        self.assertIn("not valid. note holds what looks like a secret: GitHub token on line 1 of its text.", found["d.md"])
        self.assertIn("not valid. note looks like a raw chat transcript", found["e.md"])
        self.assertIn("not valid. a note's folders and file name use small letters", found["Bad Name.md"])
        self.assertEqual(found["README.md"], "not valid. a note is a .md file, and not a README.md")
        self.assertEqual(found["f.txt"], "not valid. a note is a .md file, and not a README.md")
        self.assertNotIn(GITHUB_TOKEN, self.status())
        code, _, err = self.capture("accept", "d.md")
        self.assertEqual(code, 1)
        self.assertIn("d.md is not valid, so it cannot be accepted", err)

    def test_a_draft_that_the_team_repo_already_has_is_said_to_be_there_and_a_changed_one_is_an_update(self):
        self.adopt(note("auth/token-refresh.md", "How login tokens are refreshed", ["Sources/Auth/**"], body="Tokens refresh on 401."))
        self.instructions(self.ios)
        self.write("auth/token-refresh.md", draft("How login tokens are refreshed", ["Sources/Auth/**"], body="Tokens  refresh on 401.\n", owner="@sam"))
        self.assertIn("the team repo already has this note with the same content. Remove the draft.", self.lines()["auth/token-refresh.md"])
        code, _, err = self.capture("accept", "auth/token-refresh.md")
        self.assertEqual(code, 1)
        self.assertIn("the team repo already has auth/token-refresh.md with this content", err)
        self.write("auth/token-refresh.md", draft("How login tokens are refreshed", ["Sources/Auth/**"], body="Tokens refresh on 401 and on 403."))
        self.assertIn("new. Not accepted and not dropped yet. It changes a note that is in the team repo.", self.lines()["auth/token-refresh.md"])


class DecideTests(CaptureCase):
    def setUp(self):
        super().setUp()
        self.write("auth/token-refresh.md", draft("How login tokens are refreshed", ["Sources/Auth/**"]))
        self.write("checkout/tax.md", draft("Tax is rounded per line", ["Sources/Checkout/**"]))
        self.write("home/feed.md", draft("The feed is cached for a minute", ["Sources/Home/**"]))

    def test_accepted_notes_go_into_one_pull_request_and_nothing_is_merged(self):
        code, out, err = self.capture("accept", "auth/token-refresh.md", "knowledge/checkout/tax.md")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Accepted auth/token-refresh.md. It goes into the next pull request, as it is now.", out)
        self.assertTrue(self.lines()["checkout/tax.md"].startswith("accepted. It goes into the next pull request."))
        main = self.git(self.remote, "rev-parse", "main")
        before = len(self.proposals())
        code, out, err = self.capture("propose", "--yes", "--task", "SHOP-123")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(len(self.proposals()), before + 1)  # one pull request for the task
        branch = self.notes_branch()
        self.assertTrue(branch.startswith("tug/notes-shop-123-"), branch)
        self.assertEqual(self.proposed_files(), ["knowledge/auth/token-refresh.md", "knowledge/checkout/tax.md"])
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), main)
        message = self.git(self.remote, "log", "-1", "--format=%B", branch)
        self.assertIn("Add 2 knowledge notes about acme/shop-ios", message)
        self.assertIn("Task: SHOP-123", message)
        self.assertIn("- Adds `knowledge/auth/token-refresh.md`: How login tokens are refreshed. Covers `Sources/Auth/**`.", message)
        self.assertIn("Nothing was merged. A person reviews and merges it in GitHub.", out)
        self.assertIn("1 draft stayed in .tugboat/capture/knowledge/, because it was not accepted: home/feed.md.", out)
        note_text = self.git(self.remote, "show", f"{branch}:knowledge/checkout/tax.md")
        self.assertEqual(formats.validate_note(*frontmatter.parse(note_text + "\n"))["title"], "Tax is rounded per line")
        # The proposed drafts are gone from the repo. The other one stays.
        self.assertEqual(list(self.lines()), ["home/feed.md"])
        self.assertFalse((self.ios / ".tugboat/capture/knowledge/auth").exists())
        code, _, err = self.capture("propose", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("no draft is accepted, so there is nothing to propose.", err)

    def test_after_the_merge_the_note_is_offered_for_its_paths(self):
        self.capture("accept", "auth/token-refresh.md")
        self.capture("propose", "--yes")
        message = self.git(self.remote, "log", "-1", "--format=%s", self.notes_branch())
        self.assertEqual(message, "Add a knowledge note: How login tokens are refreshed")
        self.merge(self.notes_branch())
        self.instructions(self.ios)
        _, out, _ = self.tug(self.ios, "notes", "Sources/Auth/Login.swift")
        self.assertIn("- How login tokens are refreshed: .tugboat/team/knowledge/auth/token-refresh.md", out)

    def test_a_dropped_note_does_not_come_back_unless_its_content_changes(self):
        code, out, err = self.capture("drop", "checkout/tax.md")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Dropped checkout/tax.md. The draft is removed, and the same content will not be offered again.", out)
        self.assertFalse((self.ios / ".tugboat/capture/knowledge/checkout").exists())
        text = self.status()
        self.assertNotIn("- checkout/tax.md", text)
        self.assertIn('Dropped before in this repo. Do not write these again:\n- "Tax is rounded per line" (covers Sources/Checkout/**)', text)
        # The agent drafts the same thing again: another file name, owner, date, and line breaks, the same content.
        again = draft("Tax is rounded  per line", ["Sources/Checkout/**"], owner="@sam", verified="2026-11-01", body="What we found out\nabout Tax is rounded per line.")
        self.write("checkout/rounding.md", again)
        self.assertIn("dropped before with the same content. Do not offer it again. Remove the draft.", self.lines()["checkout/rounding.md"])
        self.capture("accept", "auth/token-refresh.md")
        self.capture("propose", "--yes")
        self.assertEqual(self.proposed_files(), ["knowledge/auth/token-refresh.md"])
        # With other content it is a new proposal.
        self.write("checkout/rounding.md", draft("Tax is rounded per line", ["Sources/Checkout/**"], body="Tax is rounded per line, half up, in `TaxCalculator`."))
        self.assertTrue(self.lines()["checkout/rounding.md"].startswith("new."))

    def test_a_draft_edited_after_it_was_accepted_must_be_accepted_again(self):
        self.capture("accept", "home/feed.md")
        self.write("home/feed.md", draft("The feed is cached for a minute", ["Sources/Home/**"], body="The feed is cached for 60 seconds in `FeedStore`."))
        self.assertTrue(self.lines()["home/feed.md"].startswith("changed after it was accepted. Accept it again, or drop it."))
        code, _, err = self.capture("propose", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("no draft is accepted, so there is nothing to propose. home/feed.md changed after it was accepted: accept it again.", err)
        self.capture("accept", "home/feed.md")
        code, out, _ = self.capture("propose", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("60 seconds", self.git(self.remote, "show", f"{self.notes_branch()}:knowledge/home/feed.md"))

    def test_the_developer_can_take_a_dropped_note_after_all(self):
        self.capture("drop", "home/feed.md")
        self.write("home/feed.md", draft("The feed is cached for a minute", ["Sources/Home/**"]))
        self.assertTrue(self.lines()["home/feed.md"].startswith("dropped before"))
        code, _, _ = self.capture("accept", "home/feed.md")
        self.assertEqual(code, 0)
        self.assertTrue(self.lines()["home/feed.md"].startswith("accepted."))
        self.assertNotIn("Dropped before", self.status())

    def test_a_note_that_changes_one_in_the_team_repo_is_proposed_as_an_update(self):
        self.adopt(note("home/feed.md", "The feed is cached for a minute", ["Sources/Home/**"], body="Old text."))
        self.instructions(self.ios)
        self.capture("accept", "home/feed.md")
        code, out, _ = self.capture("propose", "--yes")
        self.assertEqual(code, 0, out)
        message = self.git(self.remote, "log", "-1", "--format=%B", self.notes_branch())
        self.assertIn("Update a knowledge note: The feed is cached for a minute", message)
        self.assertIn("- Changes `knowledge/home/feed.md`", message)

    def test_a_name_that_is_no_draft_and_a_pull_request_without_a_person(self):
        code, _, err = self.capture("accept", "nope.md")
        self.assertEqual(code, 1)
        self.assertIn("there is no draft named nope.md in .tugboat/capture/knowledge/.", err)
        code, _, err = self.capture("drop", "auth/token-refresh.md", "nope.md")
        self.assertEqual(code, 1)
        self.assertIn("auth/token-refresh.md", self.lines())  # nothing was dropped
        self.capture("accept", "auth/token-refresh.md")
        before = self.proposals()
        code, out, err = self.capture("propose")
        self.assertEqual(code, 1)
        self.assertIn("This opens one pull request in acme/team with 1 note:\n  knowledge/auth/token-refresh.md", out)
        self.assertIn("a pull request needs a person to confirm it.", err)
        code, _, err = self.capture("propose", answers=["n"])
        self.assertEqual(code, 1)
        self.assertIn("stopped. Nothing was proposed.", err)
        self.assertEqual(self.proposals(), before)
        code, out, _ = self.capture("propose", answers=["y"])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.proposals()), len(before) + 1)

    def test_each_repo_keeps_its_own_drafts_and_its_own_dropped_list(self):
        self.capture("drop", "home/feed.md")
        self.instructions(self.android)
        self.assertTrue(self.status(self.android).startswith("No draft note."))
        self.assertNotIn("Dropped before", self.status(self.android))
        self.write("login.md", draft("Login on Android", ["app/**"], repo="acme/shop-android"), root=self.android)
        self.assertTrue(self.lines(self.android)["login.md"].startswith("new."))

    def test_a_state_file_that_is_broken_is_read_as_empty(self):
        self.capture("drop", "home/feed.md")
        (self.ios / ".tugboat/capture.json").write_text("{not json", encoding="utf-8")
        self.assertNotIn("Dropped before", self.status())
        self.capture("accept", "auth/token-refresh.md")
        state = json.loads((self.ios / ".tugboat/capture.json").read_text(encoding="utf-8"))
        self.assertEqual((state["schema"], list(state["accepted"])), ("tug.capture/1", ["auth/token-refresh.md"]))


class AgentTests(CaptureCase):
    def test_the_agent_gets_the_skill_and_is_told_not_to_stop_the_work_for_a_note(self):
        text = (self.ios / ".claude/rules/tugboat.md").read_text(encoding="utf-8")
        self.assertIn("Do not stop the work to write a note. When the developer asks to capture what was learned, use the skill `tug-capture-notes`", text)
        self.assertIn("The one exception is `.tugboat/capture/`, where the skills `tug-capture-notes` and `tug-decision-record` keep drafts.", text)
        skill = (self.ios / ".claude/skills/tug-capture-notes/SKILL.md").read_text(encoding="utf-8")
        fields, body = frontmatter.parse(skill)
        self.assertEqual(formats.validate_skill_header(fields)["name"], "tug-capture-notes")
        for expected in ("tug capture status", "tug capture accept <name>", "tug capture drop <name>", "tug capture propose --yes", "Do not accept a note for the developer.", ".tugboat/capture/knowledge/"):
            self.assertIn(expected, body)
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")

    def test_the_skills_example_draft_is_a_valid_note(self):
        skill = (self.ios / ".claude/skills/tug-capture-notes/SKILL.md").read_text(encoding="utf-8")
        example = textwrap.dedent(skill.split("```\n")[1])
        formats.validate_note(*frontmatter.parse(example))


if __name__ == "__main__":
    unittest.main()
