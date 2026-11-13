import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import tugboat
from tugboat import cli, frontmatter, team_repo
from tugboat.errors import TugError
from tugboat.strict_json import pretty_json

from test_formats import AGENT, DECISION, DECISION_BODY, NOTE, PROFILE, RULE


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


class TeamRepoCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = Path(holder.name).resolve() / "team"
        team_repo.new_team_repo(self.repo)

    def write(self, path, text):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def write_md(self, path, fields, body="Some text.\n"):
        self.write(path, frontmatter.render(fields, body))

    def problems(self, level="error"):
        report = team_repo.check_team_repo(self.repo)
        return [f"{problem.path}: {problem.message}" for problem in report.problems if problem.level == level]

    def assert_one_error(self, expected):
        errors = self.problems()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(expected, errors[0])


class StarterTests(TeamRepoCase):
    def test_the_starter_layout_passes_the_check(self):
        report = team_repo.check_team_repo(self.repo)
        self.assertEqual(report.errors, [])
        self.assertEqual([problem.path for problem in report.warnings], ["profile.json"])
        self.assertGreaterEqual(report.files_checked, 7)

    def test_the_starter_layout_has_every_folder_and_the_pr_check(self):
        for path in (
            "tugboat.json",
            "README.md",
            "policies/company/COM-001.md",
            "policies/ios/IOS-001.md",
            "policies/android/AND-001.md",
            "permissions/protected-paths.json",
            "permissions/ai-tools.json",
            "knowledge/README.md",
            "decisions/README.md",
            "estate/estate.json",
            "agents/README.md",
            "skills/README.md",
            ".github/workflows/team-repo-check.yml",
        ):
            self.assertTrue((self.repo / path).is_file(), path)
        workflow = (self.repo / ".github/workflows/team-repo-check.yml").read_text(encoding="utf-8")
        self.assertIn(f"tugboat@v{tugboat.__version__} tug team check .", workflow)
        self.assertNotIn("{{", workflow)

    def test_every_starter_rule_is_an_advisory_example(self):
        for path in sorted((self.repo / "policies").glob("*/*.md")):
            fields, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
            self.assertEqual((fields["mode"], fields["status"]), ("advisory", "example"), path.name)

    def test_the_examples_in_the_folder_readmes_are_valid(self):
        """Each README shows its format in a code block. Those blocks must pass the same check."""
        from tugboat import formats
        from tugboat.strict_json import parse_json

        def blocks(path):
            text = (self.repo / path).read_text(encoding="utf-8")
            return [part.split("\n", 1)[1] for part in text.split("```")[1::2]]

        formats.validate_rule(*frontmatter.parse(blocks("policies/README.md")[0]))
        formats.validate_note(*frontmatter.parse(blocks("knowledge/README.md")[0]))
        formats.validate_decision(*frontmatter.parse(blocks("decisions/README.md")[0]))
        formats.validate_agent(*frontmatter.parse(blocks("agents/README.md")[0]))
        formats.validate_estate(parse_json(blocks("estate/README.md")[0]))

    def test_new_writes_into_a_fresh_clone_and_refuses_a_folder_with_files(self):
        with tempfile.TemporaryDirectory() as holder:
            clone = Path(holder) / "clone"
            (clone / ".git").mkdir(parents=True)
            written = team_repo.new_team_repo(clone)
            self.assertIn("tugboat.json", written)
            with self.assertRaisesRegex(TugError, "is not empty"):
                team_repo.new_team_repo(clone)
            self.assertFalse((Path(holder) / "clone" / ".git" / "tugboat.json").exists())


class CheckTests(TeamRepoCase):
    def test_a_folder_without_the_marker_is_not_a_team_repo(self):
        (self.repo / "tugboat.json").unlink()
        report = team_repo.check_team_repo(self.repo)
        self.assertEqual([problem.message for problem in report.errors], ["is missing. This folder is not a Tugboat team repo."])

    def test_a_missing_folder_is_an_error_for_the_caller(self):
        with self.assertRaisesRegex(TugError, "is not a folder"):
            team_repo.check_team_repo(self.repo / "nowhere")

    def test_broken_json_names_the_file_and_the_reason(self):
        self.write("estate/estate.json", '{"schema": "tug.estate/1", "apps": [], "apps": []}')
        self.assert_one_error("estate/estate.json: ")
        self.assertIn("duplicate JSON field: apps", self.problems()[0])

    def test_a_missing_required_file(self):
        (self.repo / "permissions/ai-tools.json").unlink()
        self.assert_one_error("permissions/ai-tools.json: is missing")

    def test_a_valid_profile_removes_the_warning_and_a_broken_one_is_an_error(self):
        self.write("profile.json", pretty_json(PROFILE))
        self.assertEqual((self.problems(), self.problems("warning")), ([], []))
        self.write("profile.json", pretty_json({**PROFILE, "approval_mode": "nobody"}))
        self.assert_one_error("profile.json: profile approval_mode must be one of")

    def test_a_rule_in_the_wrong_folder_or_with_the_wrong_name(self):
        self.write_md("policies/android/IOS-009.md", {**RULE, "id": "IOS-009"})
        self.assert_one_error("policies/android/IOS-009.md: has scope 'ios' and must be in policies/ios/")
        (self.repo / "policies/android/IOS-009.md").unlink()
        self.write_md("policies/ios/swift-testing.md", {**RULE, "id": "IOS-009"})
        self.assert_one_error("policies/ios/swift-testing.md: must be named IOS-009.md, after its id")

    def test_a_folder_for_backend_rules_is_refused(self):
        (self.repo / "policies/backend").mkdir()
        (self.repo / "policies/backend/BE-001.md").write_text("a backend rule\n", encoding="utf-8")
        self.assert_one_error("policies/backend: is not a rule scope. Rules are kept for company, ios, android only.")

    def test_two_rules_with_one_id(self):
        self.write_md("policies/ios/copy.md", dict(RULE))
        errors = self.problems()
        self.assertTrue(any("uses rule id IOS-001, which policies/ios/IOS-001.md already uses" in error for error in errors), errors)

    def test_a_note_with_a_missing_field_and_a_note_in_a_sub_folder(self):
        self.write_md("knowledge/auth/tokens.md", dict(NOTE))
        self.assertEqual(self.problems(), [])
        self.write_md("knowledge/auth/broken.md", {key: value for key, value in NOTE.items() if key != "owner"})
        self.assert_one_error("knowledge/auth/broken.md: note is missing fields: owner")

    def test_a_markdown_file_without_a_header(self):
        self.write("knowledge/plain.md", "# Just a title\n\nText.\n")
        self.assert_one_error("knowledge/plain.md: the file must start with a '---' header")

    def test_a_decision_record(self):
        self.write_md("decisions/DEC-0001-swift-testing.md", dict(DECISION), DECISION_BODY)
        self.assertEqual(self.problems(), [])
        self.write_md("decisions/swift-testing.md", {**DECISION, "id": "DEC-0002"}, DECISION_BODY)
        self.assert_one_error("decisions/swift-testing.md: must have a file name that starts with DEC-0002")

    def test_an_agent_must_name_rules_that_exist(self):
        self.write_md("agents/reviewer.md", dict(AGENT))
        self.assertEqual(self.problems(), [])
        self.write_md("agents/reviewer.md", {**AGENT, "rules": ["IOS-001", "AND-777"]})
        self.assert_one_error("agents/reviewer.md: names rule AND-777, which is not in policies/")

    def test_a_skill_folder(self):
        self.write("skills/new-screen/SKILL.md", "---\nname: new-screen\ndescription: Use for a new screen.\n---\n\nSteps.\n")
        self.assertEqual(self.problems(), [])
        self.write("skills/other/SKILL.md", "---\nname: new-screen\ndescription: Copy.\n---\n\nSteps.\n")
        self.assert_one_error("skills/other/SKILL.md: has name 'new-screen' and must be in a folder with that name")
        (self.repo / "skills/other/SKILL.md").unlink()
        self.assert_one_error("skills/other: has no SKILL.md")

    def test_a_symbolic_link_is_refused_and_not_followed(self):
        outside = self.repo.parent / "outside.md"
        outside.write_text(frontmatter.render(dict(NOTE), "From outside."), encoding="utf-8")
        os.symlink(outside, self.repo / "knowledge" / "link.md")
        self.assert_one_error("knowledge/link.md: is a symbolic link")

    def test_readme_files_are_never_read_as_content(self):
        self.write("policies/ios/README.md", "No header here.\n")
        self.write("knowledge/deep/README.md", "No header here.\n")
        self.assertEqual(self.problems(), [])

    def test_errors_come_before_warnings_and_in_path_order(self):
        self.write("knowledge/b.md", "x")
        self.write("knowledge/a.md", "x")
        report = team_repo.check_team_repo(self.repo)
        self.assertEqual([(p.level, p.path) for p in report.problems], [("error", "knowledge/a.md"), ("error", "knowledge/b.md"), ("warning", "profile.json")])


class CommandTests(TeamRepoCase):
    def test_check_command_passes_on_the_starter_layout(self):
        code, out, err = run("team", "check", os.fspath(self.repo))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("warning: profile.json: is not there yet", out)
        self.assertRegex(out, r"Checked \d+ files: 0 problems, 1 warning\.")

    def test_check_command_fails_with_one_line_per_problem(self):
        self.write("knowledge/plain.md", "no header")
        code, out, _ = run("team", "check", os.fspath(self.repo))
        self.assertEqual(code, 1)
        self.assertIn("knowledge/plain.md: the file must start with a '---' header\n", out)
        self.assertIn("1 problem, 1 warning.", out)

    def test_check_command_defaults_to_the_current_folder(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.repo)
        self.assertEqual(run("team", "check")[0], 0)

    def test_new_command_writes_the_layout_and_says_what_is_left_to_do(self):
        target = self.repo.parent / "fresh"
        code, out, _ = run("team", "new", os.fspath(target))
        self.assertEqual(code, 0)
        self.assertIn("wrote tugboat.json", out)
        self.assertIn("commit and push them yourself", out)
        self.assertEqual(run("team", "check", os.fspath(target))[0], 0)

    def test_new_command_refuses_a_folder_with_files(self):
        code, _, err = run("team", "new", os.fspath(self.repo))
        self.assertEqual(code, 1)
        self.assertIn("is not empty", err)

    def test_team_without_an_action_is_a_usage_error(self):
        self.assertEqual(run("team")[0], 2)

    def test_json_files_in_the_starter_layout_are_plain_json(self):
        for path in sorted(self.repo.rglob("*.json")):
            json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
