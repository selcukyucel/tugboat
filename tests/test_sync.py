import contextlib
import fcntl
import io
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tugboat import cli, gitrun, paths, sync
from tugboat.address import TeamRepoAddress, parse_team_repo_address
from tugboat.errors import DataError

GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


class AddressTests(unittest.TestCase):
    def test_reads_the_forms_github_shows(self):
        for text, url in (
            ("git@github.com:acme/team-repo.git", "git@github.com:acme/team-repo.git"),
            ("git@github.com:acme/team-repo", "git@github.com:acme/team-repo.git"),
            ("ssh://git@github.com/acme/team-repo.git", "git@github.com:acme/team-repo.git"),
            ("https://github.com/acme/team-repo.git", "https://github.com/acme/team-repo.git"),
            ("  https://GitHub.com/acme/team-repo/  ", "https://github.com/acme/team-repo.git"),
        ):
            with self.subTest(text=text):
                address = parse_team_repo_address(text)
                self.assertEqual((address.host, address.slug, address.url), ("github.com", "acme/team-repo", url))

    def test_refuses_anything_that_could_reach_git_as_something_else(self):
        for text in (
            "",
            "acme/team-repo",
            "--upload-pack=touch /tmp/x",
            "-oProxyCommand=x git@github.com:a/b.git",
            "ext::sh -c 'touch /tmp/x'",
            "file:///etc/passwd",
            "/tmp/some/repo",
            "http://github.com/acme/team-repo.git",
            "https://user:secret@github.com/acme/team-repo.git",
            "https://github.com/acme/team-repo.git?x=1",
            "git@github.com:acme/../../etc.git",
            "git@github.com:acme/team repo.git",
            "git@github.com:acme/team-repo.git\n--help",
            None,
        ):
            with self.subTest(text=text), self.assertRaises(DataError):
                parse_team_repo_address(text)


class SyncCase(unittest.TestCase):
    """A real Git remote in a temporary folder stands in for GitHub."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        env = mock.patch.dict(
            os.environ,
            {
                "TUG_HOME": os.fspath(self.tmp / "home"),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                # Keeps this machine's own global ignore file out of the tests. Without this,
                # a "Git sees nothing" check could pass thanks to the developer's settings.
                "XDG_CONFIG_HOME": os.fspath(self.tmp / "xdg"),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.remote = self.tmp / "remote.git"
        self.work = self.tmp / "work"
        self.git(self.tmp, "init", "--quiet", "--bare", "-b", "main", os.fspath(self.remote))
        self.git(self.tmp, "clone", "--quiet", os.fspath(self.remote), os.fspath(self.work))
        self.push("README.md", "one\n")
        self.address = TeamRepoAddress("local.test", "acme", "team", os.fspath(self.remote))
        self.clone = paths.team_repo_clone(self.address)

    def git(self, cwd, *args):
        done = subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def push(self, name, text):
        """Make one commit in the remote, as a merged pull request would."""
        (self.work / name).parent.mkdir(parents=True, exist_ok=True)
        (self.work / name).write_text(text, encoding="utf-8")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", f"change {name}")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        return self.git(self.work, "rev-parse", "HEAD")

    def sync(self):
        return sync.sync_team_repo(self.address, lock_wait=0.3)


class SyncTests(SyncCase):
    def test_the_clone_has_a_fixed_place_under_the_tug_home(self):
        self.assertEqual(self.clone, self.tmp / "home" / "team-repos" / "local.test" / "acme" / "team")

    def test_first_sync_clones(self):
        head = self.git(self.work, "rev-parse", "HEAD")
        report = self.sync()
        self.assertEqual((report.ok, report.cloned, report.updated, report.revision, report.error), (True, True, False, head, None))
        self.assertEqual((self.clone / "README.md").read_text(encoding="utf-8"), "one\n")
        self.assertIsNotNone(report.last_success_at)
        self.assertRegex(report.revision_date, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_second_sync_with_nothing_new_changes_nothing(self):
        first = self.sync()
        second = self.sync()
        self.assertEqual((second.ok, second.cloned, second.updated, second.revision), (True, False, False, first.revision))
        self.assertIsNone(second.previous_revision)

    def test_an_approved_change_arrives_at_the_next_sync(self):
        first = self.sync()
        head = self.push("policies/ios/IOS-002.md", "new rule\n")
        report = self.sync()
        self.assertEqual((report.ok, report.updated, report.revision, report.previous_revision), (True, True, head, first.revision))
        self.assertTrue((self.clone / "policies/ios/IOS-002.md").is_file())

    def test_with_the_network_off_work_continues_from_the_last_copy(self):
        first = self.sync()
        os.rename(self.remote, self.tmp / "unplugged.git")
        report = self.sync()
        self.assertEqual((report.ok, report.usable, report.revision), (False, True, first.revision))
        self.assertEqual(report.last_success_at, first.last_success_at)
        self.assertIn("git fetch", report.error)
        text = sync.render(report)
        self.assertIn("Sync did not work: git fetch", text)
        self.assertIn(f"Work continues from revision {first.revision[:7]}", text)
        self.assertIn(f"Last sync that worked: {first.last_success_at}", text)
        # When the network is back, the next sync works again.
        os.rename(self.tmp / "unplugged.git", self.remote)
        self.assertTrue(self.sync().ok)

    def test_a_first_sync_that_cannot_reach_the_repo_leaves_nothing_behind(self):
        os.rename(self.remote, self.tmp / "unplugged.git")
        report = self.sync()
        self.assertEqual((report.ok, report.usable, report.revision, report.last_success_at), (False, False, None, None))
        self.assertFalse(self.clone.exists())
        self.assertEqual(list(self.clone.parent.iterdir()), [])
        self.assertIn("There is no local copy of the team repo yet.", sync.render(report))

    def test_local_edits_survive_an_update_and_are_reported(self):
        self.sync()
        (self.clone / "README.md").write_text("my edit\n", encoding="utf-8")
        (self.clone / "draft.md").write_text("my draft\n", encoding="utf-8")
        self.push("other.md", "from a teammate\n")
        report = self.sync()
        self.assertEqual((report.ok, report.updated), (True, True))
        self.assertEqual((self.clone / "README.md").read_text(encoding="utf-8"), "my edit\n")
        self.assertEqual((self.clone / "draft.md").read_text(encoding="utf-8"), "my draft\n")
        self.assertTrue((self.clone / "other.md").is_file())
        self.assertEqual(len(report.notes), 1)
        self.assertRegex(report.notes[0], r"^2 local edits in the clone, kept untouched: .*README\.md")

    def test_a_local_edit_in_the_way_of_an_update_is_kept_and_the_clone_is_not_moved(self):
        first = self.sync()
        (self.clone / "README.md").write_text("my edit\n", encoding="utf-8")
        self.push("README.md", "two\n")
        report = self.sync()
        self.assertEqual((report.ok, report.updated, report.revision), (False, False, first.revision))
        self.assertIn("a local edit is in the way of the update", report.error)
        self.assertEqual((self.clone / "README.md").read_text(encoding="utf-8"), "my edit\n")
        self.assertTrue(report.notes)

    def test_a_local_commit_is_never_rewritten(self):
        first = self.sync()
        (self.clone / "mine.md").write_text("mine\n", encoding="utf-8")
        self.git(self.clone, "add", "-A")
        self.git(self.clone, "commit", "--quiet", "-m", "local")
        local = self.git(self.clone, "rev-parse", "HEAD")
        self.push("other.md", "theirs\n")
        report = self.sync()
        self.assertEqual((report.ok, report.revision), (False, local))
        self.assertIn("1 local commit that GitHub does not have", report.error)
        self.assertNotEqual(local, first.revision)

    def test_a_clone_on_another_branch_is_left_alone(self):
        first = self.sync()
        self.git(self.clone, "switch", "--quiet", "-c", "notes-pr")
        self.push("other.md", "theirs\n")
        report = self.sync()
        self.assertEqual((report.ok, report.revision), (False, first.revision))
        self.assertIn("the clone is on 'notes-pr', not on 'main'", report.error)

    def test_a_clone_of_another_address_is_left_alone(self):
        self.sync()
        other = self.address._replace(url=os.fspath(self.tmp / "elsewhere.git"))
        with mock.patch.object(paths, "team_repo_clone", return_value=self.clone):
            report = sync.sync_team_repo(other, lock_wait=0.3)
        self.assertFalse(report.ok)
        self.assertIn("belongs to a different address", report.error)

    def test_a_folder_that_is_not_a_clone_is_left_alone(self):
        self.clone.mkdir(parents=True)
        (self.clone / "notes.txt").write_text("mine\n", encoding="utf-8")
        report = self.sync()
        self.assertEqual((report.ok, report.usable), (False, False))
        self.assertIn("exists and is not a clone", report.error)
        self.assertEqual((self.clone / "notes.txt").read_text(encoding="utf-8"), "mine\n")

    def test_a_link_in_the_team_repo_is_not_followed(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("outside the repo\n", encoding="utf-8")
        os.symlink(secret, self.work / "link.md")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", "link")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.assertTrue(self.sync().ok)
        self.assertFalse((self.clone / "link.md").is_symlink())
        self.assertNotIn("outside the repo", (self.clone / "link.md").read_text(encoding="utf-8"))

    def test_a_second_sync_at_the_same_moment_steps_aside(self):
        first = self.sync()
        with paths.sync_lock_file(self.address).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            report = self.sync()
        self.assertEqual((report.ok, report.revision, report.error), (False, first.revision, "another sync of this team repo is running"))
        self.assertEqual(report.last_success_at, first.last_success_at)

    def test_git_variables_from_a_hook_do_not_send_sync_to_the_wrong_repo(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": os.fspath(self.tmp / "nowhere"), "GIT_WORK_TREE": os.fspath(self.tmp)}):
            report = self.sync()
        self.assertTrue(report.ok, report.error)

    def test_a_broken_state_file_is_treated_as_no_history(self):
        self.sync()
        paths.sync_state_file(self.address).write_text("{not json", encoding="utf-8")
        self.assertIsNone(sync.read_state(self.address)["last_success_at"])
        self.assertTrue(self.sync().ok)
        self.assertIsNotNone(sync.read_state(self.address)["last_success_at"])


class GitRunTests(unittest.TestCase):
    def test_a_command_that_hangs_is_stopped(self):
        with self.assertRaisesRegex(gitrun.GitError, "git wait: gave no answer within 0.2 seconds"):
            gitrun.run_process(["sleep", "5"], action="wait", timeout=0.2)

    def test_a_missing_program_is_a_short_error(self):
        with self.assertRaisesRegex(gitrun.GitError, "could not start"):
            gitrun.run_process(["tug-no-such-program"], action="start")

    def test_git_is_never_allowed_to_ask_for_a_password(self):
        env = gitrun.git_environment()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def test_a_transport_the_developer_chose_is_kept(self):
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": "ssh -i ~/.ssh/work"}):
            self.assertEqual(gitrun.git_environment()["GIT_SSH_COMMAND"], "ssh -i ~/.ssh/work")


class RenderTests(unittest.TestCase):
    def test_age_reads_like_a_person_would_say_it(self):
        now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        for stamp, words in (
            ("2026-11-13T11:59:30Z", "just now"),
            ("2026-11-13T11:55:00Z", "5 minutes ago"),
            ("2026-11-13T10:59:00Z", "1 hour ago"),
            ("2026-11-11T12:00:00Z", "2 days ago"),
        ):
            self.assertEqual(sync.age(stamp, now), words)


class SyncCommandTests(SyncCase):
    def test_sync_without_an_address_explains_what_is_missing(self):
        code, _, err = run("sync")
        self.assertEqual(code, 1)
        self.assertIn("Run `tug init` inside the app repo first.", err)

    def test_sync_refuses_a_bad_address_before_git_sees_it(self):
        code, _, err = run("sync", "--team-repo", "--upload-pack=x")
        self.assertIn(code, (1, 2))
        self.assertFalse((self.tmp / "home").exists())

    def test_sync_prints_the_report_and_succeeds_when_there_is_a_copy_to_work_from(self):
        with mock.patch("tugboat.address.parse_team_repo_address", return_value=self.address):
            code, out, _ = run("sync", "--team-repo", "git@github.com:acme/team.git")
            self.assertEqual(code, 0)
            self.assertIn("Cloned. Revision ", out)
            os.rename(self.remote, self.tmp / "unplugged.git")
            code, out, _ = run("sync", "--team-repo", "git@github.com:acme/team.git")
            self.assertEqual(code, 0)
            self.assertIn("Sync did not work", out)

    def test_sync_fails_when_there_is_nothing_to_work_from(self):
        os.rename(self.remote, self.tmp / "unplugged.git")
        with mock.patch("tugboat.address.parse_team_repo_address", return_value=self.address):
            code, out, _ = run("sync", "--team-repo", "git@github.com:acme/team.git")
        self.assertEqual(code, 1)
        self.assertIn("There is no local copy of the team repo yet.", out)


if __name__ == "__main__":
    unittest.main()
