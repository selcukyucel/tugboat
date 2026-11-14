import os
import stat
import unittest
from unittest import mock

from tugboat import apprepo, frontmatter, sync, teamcopy
from tugboat.teamcopy import Change
from tugboat.errors import TugError
from tugboat.version import Version

from test_sync import SyncCase, run


NOTE_FILE = frontmatter.render(
    {"title": "Token refresh", "owner": "@pat", "repo": "acme/shop-ios", "paths": ["Sources/Auth/**"], "last_verified": "2026-11-13"},
    "Tokens refresh on 401.",
)


class CopyCase(SyncCase):
    """SyncCase gives a team repo remote. This adds team content and an app repo."""

    def setUp(self):
        super().setUp()
        self.push("tugboat.json", '{"schema": "tug.team-repo/1"}\n')
        self.push("policies/ios/IOS-001.md", "rule one\n")
        self.push("knowledge/auth/tokens.md", NOTE_FILE)
        self.push(".github/workflows/check.yml", "name: check\n")
        self.app = self.tmp / "app"
        self.git(self.tmp, "init", "--quiet", "-b", "main", os.fspath(self.app))
        (self.app / "App.swift").write_text("// app\n", encoding="utf-8")
        self.git(self.app, "add", "-A")
        self.git(self.app, "commit", "--quiet", "-m", "app")
        self.copy = self.app / ".tugboat" / "team"

    def refresh(self, **options):
        self.assertTrue(sync.sync_team_repo(self.address, lock_wait=0.3).usable)
        return teamcopy.refresh_copy(self.app, self.address, **options)

    def tree(self):
        """Every file of the copy with its inode and change time, to prove nothing was touched."""
        return {path.relative_to(self.copy).as_posix(): (path.stat().st_ino, path.stat().st_mtime_ns) for path in sorted(self.copy.rglob("*")) if path.is_file()}

    def status(self, repo=None):
        return self.git(repo or self.app, "status", "--porcelain")


class RefreshTests(CopyCase):
    def test_first_refresh_writes_the_copy_and_git_does_not_see_it(self):
        report = self.refresh()
        self.assertEqual((report.refreshed, report.reason, report.files), (True, "this repo had no copy yet", 3))
        self.assertEqual((self.copy / "policies/ios/IOS-001.md").read_text(encoding="utf-8"), "rule one\n")
        self.assertEqual(report.revision, self.git(self.work, "rev-parse", "HEAD"))
        self.assertEqual(self.status(), "")
        readme = (self.app / ".tugboat/README.md").read_text(encoding="utf-8")
        self.assertIn(report.revision, readme)
        self.assertIn("Do not edit it", readme)

    def test_only_the_parts_an_agent_needs_are_copied(self):
        self.refresh()
        self.assertEqual(sorted(path.name for path in self.copy.iterdir()), ["knowledge", "policies", "tugboat.json"])
        self.assertFalse((self.copy / ".github").exists())
        self.assertFalse((self.copy / "README.md").exists())

    def test_an_unchanged_team_repo_causes_no_file_change(self):
        self.refresh()
        before = self.tree()
        state = (self.app / ".tugboat/state.json").stat().st_mtime_ns
        report = self.refresh()
        self.assertEqual((report.refreshed, report.reason, report.files), (False, "nothing changed", 3))
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.app / ".tugboat/state.json").stat().st_mtime_ns, state)

    def test_a_new_revision_refreshes_the_copy(self):
        first = self.refresh()
        self.push("policies/ios/IOS-001.md", "rule one, changed\n")
        self.push("policies/ios/IOS-002.md", "rule two\n")
        self.git(self.work, "rm", "--quiet", "knowledge/auth/tokens.md")
        self.git(self.work, "commit", "--quiet", "-m", "remove note")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        report = self.refresh()
        self.assertEqual((report.refreshed, report.reason, report.previous_revision), (True, "the team repo has a new revision", first.revision))
        self.assertEqual((self.copy / "policies/ios/IOS-001.md").read_text(encoding="utf-8"), "rule one, changed\n")
        self.assertTrue((self.copy / "policies/ios/IOS-002.md").is_file())
        self.assertFalse((self.copy / "knowledge").exists())
        self.assertEqual(self.status(), "")

    def test_a_refresh_lists_what_changed_for_the_agent_with_the_file_to_read(self):
        first = self.refresh()
        rule = {"id": "IOS-002", "title": "New  unit tests\tuse Swift Testing", "scope": "ios", "mode": "advisory", "status": "adopted"}
        files = {
            "policies/ios/IOS-001.md": "rule one, changed\n",
            "policies/ios/IOS-002.md": frontmatter.render(rule, "Use it."),
            "policies/README.md": "about rules\n",
            "permissions/protected-paths.json": "{}\n",
            "knowledge/payments/refunds.md": frontmatter.render({"title": "Refunds"}, "How refunds work."),
            "skills/new-screen/SKILL.md": "steps\n",
            "skills/new-screen/template.swift": "// t\n",
            "decisions/DEC-0001-swift-testing.md": frontmatter.render({"title": "Use Swift Testing"}, "Why."),
            "agents/reviewer.md": "an agent\n",
            "estate/estate.json": "{}\n",
            "profile.json": "{}\n",
            ".github/workflows/check.yml": "name: check again\n",
        }
        for path, text in files.items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(text, encoding="utf-8")
        self.git(self.work, "rm", "--quiet", "knowledge/auth/tokens.md")
        last = self.push("README.md", "two\n")
        report = self.refresh()
        team = ".tugboat/team"
        self.assertEqual(
            report.changes,
            (
                Change("Rule", "changed", "IOS-001", None, f"{team}/policies/ios/IOS-001.md"),
                Change("Rule", "added", "IOS-002", "New unit tests use Swift Testing", f"{team}/policies/ios/IOS-002.md"),
                Change("Permissions", "added", "protected paths", None, f"{team}/permissions/protected-paths.json"),
                Change("Note", "removed", "auth/tokens.md", None, None),
                Change("Note", "added", "payments/refunds.md", "Refunds", f"{team}/knowledge/payments/refunds.md"),
                Change("Skill", "added", "new-screen", None, f"{team}/skills/new-screen/SKILL.md"),
                Change("Decision record", "added", "DEC-0001-swift-testing", "Use Swift Testing", f"{team}/decisions/DEC-0001-swift-testing.md"),
                Change("Agent", "added", "reviewer", None, f"{team}/agents/reviewer.md"),
                Change("Estate map", "added", "", None, f"{team}/estate/estate.json"),
                Change("Profile", "added", "", None, f"{team}/profile.json"),
                Change("Other file", "added", "policies/README.md", None, f"{team}/policies/README.md"),
            ),
        )
        text = teamcopy.render(report, self.app)
        self.assertIn(f"Changed in the team repo since this repo's last sync ({first.revision[:7]} to {last[:7]}):", text)
        for line in (
            '- Rule added: IOS-002 "New unit tests use Swift Testing" (.tugboat/team/policies/ios/IOS-002.md)',
            "- Rule changed: IOS-001 (.tugboat/team/policies/ios/IOS-001.md)",
            "- Note removed: auth/tokens.md\n",
            "- Skill added: new-screen (.tugboat/team/skills/new-screen/SKILL.md)",
            "- Profile added (.tugboat/team/profile.json)",
        ):
            self.assertIn(line, text + "\n")
        for change in report.changes:
            if change.path:
                self.assertTrue((self.app / change.path).is_file(), change.path)

        # A file inside a skill changes the skill. Removing its SKILL.md removes it.
        self.push("skills/new-screen/template.swift", "// t2\n")
        self.assertEqual(self.refresh().changes, (Change("Skill", "changed", "new-screen", None, f"{team}/skills/new-screen/SKILL.md"),))
        self.git(self.work, "rm", "--quiet", "-r", "skills")
        self.git(self.work, "commit", "--quiet", "-m", "remove skill")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.assertEqual(self.refresh().changes, (Change("Skill", "removed", "new-screen", None, None),))
        # A second sync with nothing new lists nothing.
        again = self.refresh()
        self.assertEqual((again.refreshed, again.changes), (False, ()))

    def test_a_first_copy_lists_no_changes(self):
        report = self.refresh()
        self.assertEqual(report.changes, ())
        self.assertNotIn("since this repo's last sync", teamcopy.render(report, self.app))

    def test_only_a_change_outside_what_an_agent_reads(self):
        self.refresh()
        self.push(".github/workflows/check.yml", "name: check again\n")
        report = self.refresh()
        self.assertEqual((report.refreshed, report.changes), (True, ()))
        self.assertIn("Nothing that an agent reads changed in the team repo since this repo's last sync.", teamcopy.render(report, self.app))

    def test_a_previous_revision_that_the_clone_does_not_have_gives_no_list_and_never_reaches_git_as_an_option(self):
        self.refresh()
        state_file = self.app / ".tugboat/state.json"
        for fake in ("0" * 40, "--output=" + os.fspath(self.tmp / "written-by-git")):
            state_file.write_text(state_file.read_text(encoding="utf-8").replace(self.git(self.work, "rev-parse", "HEAD"), fake), encoding="utf-8")
            self.push("policies/ios/IOS-001.md", f"rule one, after {len(fake)}\n")
            report = self.refresh()
            self.assertIsNone(report.changes)
            self.assertIn("could not be listed", teamcopy.render(report, self.app))
        self.assertFalse((self.tmp / "written-by-git").exists())

    def test_a_long_list_is_cut_and_counted(self):
        self.refresh()
        for number in range(teamcopy.MAX_LISTED + 3):
            (self.work / f"knowledge/n{number:02}.md").write_text("a note\n", encoding="utf-8")
        self.push("knowledge/last.md", "a note\n")
        text = teamcopy.render(self.refresh(), self.app)
        self.assertEqual(text.count("- Note added: "), teamcopy.MAX_LISTED)
        self.assertIn("- and 4 more", text)

    def test_a_new_tool_version_refreshes_the_copy(self):
        self.refresh()
        with mock.patch.object(teamcopy, "installed_version", return_value=Version(9, 9, 9)):
            report = self.refresh()
            self.assertEqual((report.refreshed, report.previous_revision), (True, None))
            self.assertRegex(report.reason, r"^the tool changed from version \d+\.\d+\.\d+ to 9\.9\.9$")
            self.assertFalse(self.refresh().refreshed)

    def test_a_missing_copy_or_a_broken_state_file_refreshes(self):
        self.refresh()
        os.rename(self.copy, self.tmp / "gone")
        self.assertEqual(self.refresh().reason, "the copy was missing")
        (self.app / ".tugboat/state.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.refresh().reason, "this repo had no copy yet")
        self.assertEqual(self.refresh(force=True).reason, "a refresh was asked for")

    def test_the_copy_files_are_read_only_and_a_refresh_still_replaces_them(self):
        self.refresh()
        rule = self.copy / "policies/ios/IOS-001.md"
        self.assertEqual(stat.S_IMODE(rule.stat().st_mode), 0o444)
        with self.assertRaises(PermissionError):
            rule.write_text("an agent edit\n", encoding="utf-8")
        self.push("policies/ios/IOS-001.md", "approved change\n")
        self.refresh()
        self.assertEqual(rule.read_text(encoding="utf-8"), "approved change\n")

    def test_the_copy_holds_approved_content_not_local_edits_or_local_commits(self):
        self.refresh()
        (self.clone / "policies/ios/IOS-001.md").write_text("an unapproved local edit\n", encoding="utf-8")
        (self.clone / "policies/ios/IOS-009.md").write_text("an unapproved local rule\n", encoding="utf-8")
        self.git(self.clone, "add", "policies/ios/IOS-009.md")
        self.git(self.clone, "commit", "--quiet", "-m", "local only")
        report = self.refresh(force=True)
        self.assertEqual(report.revision, self.git(self.work, "rev-parse", "HEAD"))
        self.assertEqual((self.copy / "policies/ios/IOS-001.md").read_text(encoding="utf-8"), "rule one\n")
        self.assertFalse((self.copy / "policies/ios/IOS-009.md").exists())

    def test_a_link_in_the_team_repo_is_left_out_and_reported(self):
        os.symlink("/etc/hosts", self.work / "knowledge" / "link.md")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", "link")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        report = self.refresh()
        self.assertEqual(report.skipped, ("knowledge/link.md",))
        self.assertFalse((self.copy / "knowledge/link.md").exists())
        self.assertIn("1 entry was not copied", teamcopy.render(report, self.app))

    def test_content_that_is_too_large_is_refused_and_the_old_copy_stays(self):
        self.refresh()
        self.push("knowledge/more.md", "more\n")
        with mock.patch.object(teamcopy, "MAX_FILES", 2), self.assertRaisesRegex(TugError, "larger than 2 files"):
            self.refresh()
        self.assertEqual((self.copy / "policies/ios/IOS-001.md").read_text(encoding="utf-8"), "rule one\n")
        self.assertFalse((self.copy / "knowledge/more.md").exists())
        self.assertEqual(sorted(path.name for path in (self.app / ".tugboat").iterdir()), ["README.md", "state.json", "team"])

    def test_the_copy_works_with_the_network_off(self):
        self.assertTrue(sync.sync_team_repo(self.address, lock_wait=0.3).ok)
        os.rename(self.remote, self.tmp / "unplugged.git")
        report = self.refresh()
        self.assertEqual((report.refreshed, report.files), (True, 3))

    def test_a_clone_that_never_fetched_is_an_error_not_a_guess(self):
        with self.assertRaisesRegex(TugError, "Run sync first"):
            teamcopy.refresh_copy(self.app, self.address)


class AppRepoTests(CopyCase):
    def test_finds_the_top_of_the_repo_from_a_sub_folder(self):
        (self.app / "Sources/Deep").mkdir(parents=True)
        self.assertEqual(apprepo.find_app_repo(self.app / "Sources/Deep"), self.app)

    def test_refuses_a_folder_outside_git_and_refuses_a_team_repo(self):
        with self.assertRaisesRegex(TugError, "is not inside a Git repo"):
            apprepo.find_app_repo(self.tmp)
        with self.assertRaisesRegex(TugError, "is a team repo, not an app repo"):
            apprepo.find_app_repo(self.work)

    def test_the_exclude_block_is_added_once_and_keeps_the_developers_own_lines(self):
        exclude = self.app / ".git/info/exclude"
        exclude.write_text("# my own\n*.local\n", encoding="utf-8")
        self.assertTrue(apprepo.ensure_excluded(self.app, "/.tugboat/"))
        self.assertFalse(apprepo.ensure_excluded(self.app, "/.tugboat/"))
        self.assertTrue(apprepo.ensure_excluded(self.app, "/.claude/rules/tugboat.md"))
        text = exclude.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# my own\n*.local\n"))
        self.assertEqual(text.count(">>> tugboat"), 1)
        self.assertEqual(apprepo.excluded_patterns(self.app), ["/.tugboat/", "/.claude/rules/tugboat.md"])
        self.assertTrue(apprepo.set_excluded_patterns(self.app, []))
        self.assertEqual(exclude.read_text(encoding="utf-8"), "# my own\n*.local\n")

    def test_the_copy_is_ignored_in_a_worktree_too(self):
        tree = self.tmp / "app-worktree"
        self.git(self.app, "worktree", "add", "--quiet", "-b", "feature", os.fspath(tree))
        sync.sync_team_repo(self.address, lock_wait=0.3)
        teamcopy.refresh_copy(tree, self.address)
        self.assertTrue((tree / ".tugboat/team/tugboat.json").is_file())
        self.assertEqual(self.status(tree), "")


class SyncCommandCopyTests(CopyCase):
    def run_sync(self, *argv, cwd=None):
        here = os.getcwd()
        os.chdir(cwd or self.tmp)
        try:
            with mock.patch("tugboat.address.parse_team_repo_address", return_value=self.address):
                return run("sync", *argv)
        finally:
            os.chdir(here)

    def test_the_first_sync_names_the_app_repo_and_later_ones_need_no_option(self):
        code, out, err = self.run_sync("--team-repo", "git@github.com:acme/team.git", "--app-repo", os.fspath(self.app))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Copy in this repo: refreshed to revision", out)
        self.assertIn("(3 files in .tugboat/team/), because this repo had no copy yet.", out)
        code, out, _ = self.run_sync(cwd=self.app)
        self.assertEqual(code, 0)
        self.assertIn("Copy in this repo: unchanged at revision", out)
        code, out, _ = self.run_sync("--force", cwd=self.app)
        self.assertIn("because a refresh was asked for.", out)

    def test_a_repo_without_a_copy_is_never_written_to_by_accident(self):
        code, out, _ = self.run_sync("--team-repo", "git@github.com:acme/team.git", cwd=self.app)
        self.assertEqual(code, 0)
        self.assertNotIn("Copy in this repo", out)
        self.assertFalse((self.app / ".tugboat").exists())

    def test_a_repo_set_up_for_another_team_repo_is_left_alone(self):
        self.run_sync("--team-repo", "git@github.com:acme/team.git", "--app-repo", os.fspath(self.app))
        other = self.address._replace(url="git@github.com:other/team.git")
        here = os.getcwd()
        os.chdir(self.app)
        try:
            with mock.patch("tugboat.address.parse_team_repo_address", return_value=other):
                code, _, err = run("sync", "--team-repo", "git@github.com:other/team.git")
        finally:
            os.chdir(here)
        self.assertEqual(code, 1)
        self.assertIn("this repo is set up for the team repo", err)

    def test_with_the_network_off_the_copy_is_still_reported(self):
        self.run_sync("--team-repo", "git@github.com:acme/team.git", "--app-repo", os.fspath(self.app))
        os.rename(self.remote, self.tmp / "unplugged.git")
        code, out, _ = self.run_sync(cwd=self.app)
        self.assertEqual(code, 0)
        self.assertIn("Sync did not work", out)
        self.assertIn("Copy in this repo: unchanged at revision", out)


if __name__ == "__main__":
    unittest.main()
