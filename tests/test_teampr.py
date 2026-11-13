import os
import stat
import unittest
from unittest import mock

from tugboat import frontmatter, paths, sync, team_repo, teampr
from tugboat.errors import TugError

from test_formats import NOTE
from test_sync import run
from test_teamcopy import CopyCase

NOTE_TEXT = frontmatter.render(dict(NOTE), "Tokens refresh on a 401.").encode("utf-8")
IDENTITY = {"GIT_AUTHOR_NAME": "Pat", "GIT_AUTHOR_EMAIL": "pat@example.invalid", "GIT_COMMITTER_NAME": "Pat", "GIT_COMMITTER_EMAIL": "pat@example.invalid"}


class ProposeCase(CopyCase):
    """The remote holds a real starter layout, so the team repo check means something."""

    def setUp(self):
        super().setUp()
        for path, text in team_repo.starter_layout().items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(text, encoding="utf-8")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", "starter")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.main = self.git(self.work, "rev-parse", "HEAD")
        env = mock.patch.dict(os.environ, {**IDENTITY, "TUG_GH": ""})
        env.start()
        self.addCleanup(env.stop)
        self.gh_log = self.tmp / "gh-args.txt"

    def fake_gh(self, *, fail=False):
        script = self.tmp / "gh"
        body = 'printf "%s\\n" "$@" > "' + os.fspath(self.gh_log) + '"\n'
        body += 'echo "boom" >&2; exit 1\n' if fail else 'echo "https://github.com/acme/team/pull/7"\n'
        script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return os.fspath(script)

    def propose(self, changes=None, **options):
        options.setdefault("title", "Add a note on token refresh")
        options.setdefault("body", "Found while fixing the login bug.")
        return teampr.propose(self.address, changes=changes or {"knowledge/auth/tokens.md": NOTE_TEXT}, **options)

    def remote_branches(self):
        return sorted(self.git(self.remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())

    def nothing_left_behind(self):
        clone = paths.team_repo_clone(self.address)
        self.assertEqual(self.git(clone, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self.git(clone, "status", "--porcelain"), "")
        self.assertEqual(len(self.git(clone, "worktree", "list").splitlines()), 1)
        self.assertEqual(self.git(clone, "for-each-ref", "--format=%(refname:short)", "refs/heads"), "main")
        work = paths.tug_home() / "work"
        self.assertEqual(list(work.iterdir()) if work.exists() else [], [])


class ProposeTests(ProposeCase):
    def test_a_branch_with_the_right_files_appears_and_nothing_is_merged(self):
        proposal = self.propose()
        self.assertRegex(proposal.branch, r"^tug/add-a-note-on-token-refresh-\d{8}-\d{6}$")
        self.assertEqual(self.remote_branches(), ["main", proposal.branch])
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), self.main)
        self.assertEqual(self.git(self.remote, "rev-parse", f"{proposal.branch}~1"), self.main)
        self.assertEqual(self.git(self.remote, "show", f"{proposal.branch}:knowledge/auth/tokens.md").encode() + b"\n", NOTE_TEXT)
        self.assertEqual(self.git(self.remote, "diff", "--name-only", "main", proposal.branch), "knowledge/auth/tokens.md")
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%s%n%b%an", proposal.branch), "Add a note on token refresh\nFound while fixing the login bug.\nPat")
        self.assertEqual(proposal.files, ("knowledge/auth/tokens.md",))
        self.nothing_left_behind()

    def test_without_gh_the_developer_gets_the_link_to_open_it(self):
        proposal = self.propose()
        self.assertIsNone(proposal.pull_request)
        self.assertEqual(proposal.open_link, f"https://local.test/acme/team/compare/main...{proposal.branch}?expand=1")
        text = teampr.render(proposal)
        self.assertIn("was not opened for you, because the GitHub command-line tool `gh` is not installed.", text)
        self.assertIn("Nothing was merged.", text)

    def test_with_gh_the_pull_request_is_opened_against_main(self):
        proposal = self.propose(gh=self.fake_gh())
        self.assertEqual(proposal.pull_request, "https://github.com/acme/team/pull/7")
        args = self.gh_log.read_text(encoding="utf-8").split("\n")
        self.assertEqual(args[:8], ["pr", "create", "--repo", "acme/team", "--base", "main", "--head", proposal.branch])
        self.assertEqual(args[8:10], ["--title", "Add a note on token refresh"])
        self.assertIn("Nothing is merged automatically.", "\n".join(args))
        self.assertIn("Pull request: https://github.com/acme/team/pull/7", teampr.render(proposal))

    def test_when_gh_fails_the_branch_is_still_there_and_the_link_is_given(self):
        proposal = self.propose(gh=self.fake_gh(fail=True))
        self.assertIsNone(proposal.pull_request)
        self.assertIn("`gh` could not open it", proposal.note)
        self.assertIn(proposal.branch, self.remote_branches())

    def test_a_file_can_be_changed_and_a_file_can_be_removed(self):
        proposal = self.propose({"policies/ios/IOS-001.md": None, "estate/estate.json": b'{"schema": "tug.estate/1", "apps": []}\n'})
        self.assertEqual(sorted(proposal.files), ["estate/estate.json", "policies/ios/IOS-001.md"])
        self.assertEqual(self.git(self.remote, "diff", "--name-status", "main", proposal.branch), "M\testate/estate.json\nD\tpolicies/ios/IOS-001.md")

    def test_ci_files_and_paths_outside_the_repo_are_refused_before_anything_is_pushed(self):
        for path in (".github/workflows/team-repo-check.yml", "tugboat.json", "README.md", "../outside.md", "/etc/passwd", "knowledge/../.github/x.yml"):
            with self.subTest(path=path), self.assertRaises(TugError):
                self.propose({path: b"x"})
        self.assertEqual(self.remote_branches(), ["main"])

    def test_a_change_that_breaks_the_check_is_refused_with_the_reason(self):
        with self.assertRaisesRegex(TugError, r"does not pass the team repo check.*knowledge/bad\.md: the file must start with a '---' header"):
            self.propose({"knowledge/bad.md": b"no header\n"})
        self.assertEqual(self.remote_branches(), ["main"])
        self.nothing_left_behind()

    def test_a_problem_that_was_already_on_main_does_not_block_the_fix(self):
        self.push("knowledge/old.md", "no header\n")
        proposal = self.propose({"knowledge/old.md": NOTE_TEXT}, title="Fix the old note")
        self.assertIn(proposal.branch, self.remote_branches())

    def test_content_that_is_already_there_is_nothing_to_propose(self):
        current = (self.work / "estate/estate.json").read_bytes()
        with self.assertRaisesRegex(TugError, "already has exactly this content"):
            self.propose({"estate/estate.json": current})
        self.assertEqual(self.remote_branches(), ["main"])
        self.nothing_left_behind()

    def test_offline_nothing_is_proposed_and_nothing_is_left_behind(self):
        sync.sync_team_repo(self.address, lock_wait=0.3)
        os.rename(self.remote, self.tmp / "unplugged.git")
        with self.assertRaisesRegex(TugError, "could not be reached, so nothing was proposed"):
            self.propose()
        os.rename(self.tmp / "unplugged.git", self.remote)
        self.nothing_left_behind()

    def test_local_edits_in_the_clone_stay_out_of_the_proposal_and_stay_in_the_clone(self):
        sync.sync_team_repo(self.address, lock_wait=0.3)
        clone = paths.team_repo_clone(self.address)
        (clone / "knowledge/draft.md").write_text("my private draft\n", encoding="utf-8")
        proposal = self.propose()
        self.assertEqual(self.git(self.remote, "diff", "--name-only", "main", proposal.branch), "knowledge/auth/tokens.md")
        self.assertEqual((clone / "knowledge/draft.md").read_text(encoding="utf-8"), "my private draft\n")

    def test_no_app_repo_is_written_to(self):
        self.refresh()
        before = self.status()
        state = (self.app / ".tugboat/state.json").stat().st_mtime_ns
        self.propose()
        self.assertEqual(self.status(), before)
        self.assertEqual((self.app / ".tugboat/state.json").stat().st_mtime_ns, state)
        self.assertFalse((self.copy / "knowledge/auth/tokens.md").read_bytes() == NOTE_TEXT)

    def test_a_missing_git_identity_is_explained(self):
        with mock.patch.dict(os.environ, {key: "" for key in IDENTITY}):
            for key in IDENTITY:
                os.environ.pop(key)
            with self.assertRaisesRegex(TugError, "Git does not know your name and email"):
                self.propose()
        self.assertEqual(self.remote_branches(), ["main"])
        self.nothing_left_behind()

    def test_title_and_size_limits(self):
        for options in ({"title": " "}, {"title": "two\nlines"}, {"body": "x" * 20001}):
            with self.subTest(options=list(options)), self.assertRaises(TugError):
                self.propose(**options)
        with self.assertRaisesRegex(TugError, "is larger than 512 KB"):
            self.propose({"knowledge/big.md": b"x" * (512 * 1024 + 1)})
        with self.assertRaisesRegex(TugError, "nothing to propose"):
            teampr.propose(self.address, title="t", body="", changes={})

    def test_slugs(self):
        self.assertEqual(teampr.slugify("Add a note: token refresh (iOS)!"), "add-a-note-token-refresh-ios")
        self.assertEqual(teampr.slugify("***"), "change")
        self.assertLessEqual(len(teampr.slugify("word " * 40)), 40)


class ProposeCommandTests(ProposeCase):
    def command(self, *argv, tty=False, answer=""):
        local = self.tmp / "tokens.md"
        local.write_bytes(NOTE_TEXT)
        with mock.patch("tugboat.address.parse_team_repo_address", return_value=self.address), mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", return_value=answer):
            stdin.isatty.return_value = tty
            return run("team", "propose", "--team-repo", "git@github.com:acme/team.git", "--title", "Add a note", "--file", f"knowledge/auth/tokens.md={local}", *argv)

    def test_yes_opens_it_and_prints_where(self):
        code, out, err = self.command("--yes", "--body", "Why.")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Pushed the branch tug/add-a-note-", out)
        self.assertIn("with 1 file: knowledge/auth/tokens.md.", out)
        self.assertEqual(len(self.remote_branches()), 2)

    def test_a_person_is_asked_first_and_can_decline(self):
        code, out, err = self.command(tty=True, answer="n")
        self.assertEqual(code, 1)
        self.assertIn("This opens a pull request in acme/team: Add a note", out)
        self.assertIn("write  knowledge/auth/tokens.md", out)
        self.assertIn("Nothing was proposed.", err)
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertEqual(self.command(tty=True, answer="y")[0], 0)

    def test_without_a_terminal_and_without_yes_nothing_is_proposed(self):
        code, _, err = self.command()
        self.assertEqual(code, 1)
        self.assertIn("needs a person to confirm it", err)
        self.assertEqual(self.remote_branches(), ["main"])

    def test_a_bad_file_option_and_a_missing_file_are_explained(self):
        with mock.patch("tugboat.address.parse_team_repo_address", return_value=self.address):
            code, _, err = run("team", "propose", "--team-repo", "x", "--title", "t", "--yes", "--file", "knowledge/x.md")
            self.assertEqual(code, 1)
            self.assertIn("--file must look like TEAM_PATH=LOCAL_FILE", err)
            code, _, err = run("team", "propose", "--team-repo", "x", "--title", "t", "--yes", "--file", f"knowledge/x.md={self.tmp / 'missing.md'}")
            self.assertEqual(code, 1)
            self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()
