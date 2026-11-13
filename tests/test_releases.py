import json
import os
import stat
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tugboat import hook, paths, releases, update
from tugboat.errors import TugError
from tugboat.gitrun import GitError
from tugboat.version import Version

from test_formats import PROFILE
from test_init import TEAM, InitCase
from test_sync import SyncCase, run

NOON = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)


class ReleaseCase(SyncCase):
    """A local repo with tags stands in for the tool's own repo."""

    TAGS = ("v0.1.0", "v0.9.0", "v0.10.0", "v1.0", "nightly", "v2.0.0-rc1", "0.11.0")

    def setUp(self):
        super().setUp()
        self.tool = self.tmp / "tool.git"
        self.git(self.tmp, "init", "--quiet", "--bare", "-b", "main", os.fspath(self.tool))
        self.git(self.work, "push", "--quiet", os.fspath(self.tool), "HEAD:main")
        for tag in self.TAGS:
            self.release(tag)

    def release(self, tag):
        self.git(self.work, "push", "--quiet", os.fspath(self.tool), f"HEAD:refs/tags/{tag}")

    def installed(self, text, where="tugboat.releases.installed_version"):
        patch = mock.patch(where, return_value=Version(*(int(part) for part in text.split("."))))
        patch.start()
        self.addCleanup(patch.stop)

    def unplug(self):
        os.rename(self.tool, self.tmp / "tool-unplugged.git")

    def check_file(self):
        return json.loads(paths.release_check_file().read_text(encoding="utf-8"))


class ReleaseLookupTests(ReleaseCase):
    def test_only_tags_that_are_exactly_a_version_count_and_ten_is_newer_than_nine(self):
        self.assertEqual([str(version) for version in releases.read_releases()], ["0.1.0", "0.9.0", "0.10.0"])
        self.assertEqual(releases.install_command(Version(0, 10, 0)), ["tool", "install", "--force", "git+https://github.com/selcukyucel/tugboat@v0.10.0"])

    def test_the_repo_is_asked_at_most_once_a_day(self):
        self.assertEqual(str(releases.newest_seen(now=NOON)), "0.10.0")
        self.assertEqual(self.check_file(), {"schema": "tug.release-check/1", "checked_at": "2026-11-13T12:00:00Z", "attempted_at": "2026-11-13T12:00:00Z", "newest": "0.10.0"})
        self.release("v0.11.0")
        with mock.patch.object(releases, "read_releases", side_effect=AssertionError("asked again")):
            self.assertEqual(str(releases.newest_seen(now=NOON + timedelta(hours=23, minutes=59))), "0.10.0")
        self.assertEqual(str(releases.newest_seen(now=NOON + timedelta(hours=24))), "0.11.0")

    def test_a_lookup_that_fails_is_silent_keeps_the_last_answer_and_is_tried_again_an_hour_later(self):
        releases.newest_seen(now=NOON)
        self.unplug()
        later = NOON + timedelta(days=2)
        self.assertEqual(str(releases.newest_seen(now=later)), "0.10.0")
        self.assertEqual((self.check_file()["checked_at"], self.check_file()["attempted_at"]), ("2026-11-13T12:00:00Z", "2026-11-15T12:00:00Z"))
        with mock.patch.object(releases, "read_releases", side_effect=AssertionError("asked too soon")):
            releases.newest_seen(now=later + timedelta(minutes=59))
        with mock.patch.object(releases, "read_releases", side_effect=GitError("ls-remote", "offline")) as asked:
            releases.newest_seen(now=later + timedelta(hours=1))
        self.assertEqual(asked.call_count, 1)

    def test_with_no_answer_yet_and_no_network_nothing_is_known_and_nothing_is_raised(self):
        self.unplug()
        self.assertIsNone(releases.newest_seen(now=NOON))
        self.assertIsNone(releases.newer_release(now=NOON))

    def test_it_never_asks_when_told_not_to_and_a_broken_file_or_a_clock_set_back_asks_again(self):
        self.assertIsNone(releases.newest_seen(ask=False, now=NOON))
        self.assertFalse(paths.release_check_file().exists())
        paths.release_check_file().parent.mkdir(parents=True, exist_ok=True)
        paths.release_check_file().write_text('{"schema": "tug.release-check/1", "newest": "--force"}', encoding="utf-8")
        self.assertEqual(str(releases.newest_seen(now=NOON)), "0.10.0")
        self.release("v0.12.0")
        self.assertEqual(str(releases.newest_seen(now=NOON - timedelta(days=3))), "0.12.0")

    def test_a_newer_release_is_only_one_above_the_installed_tool(self):
        self.installed("0.9.0")
        self.assertEqual(str(releases.newer_release(now=NOON)), "0.10.0")
        self.installed("0.10.0")
        self.assertIsNone(releases.newer_release(now=NOON))
        self.installed("0.11.0")
        self.assertIsNone(releases.newer_release(now=NOON))


class UpdateTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.tools = self.tmp / "uv-tools"
        (self.tools / "tugboat").mkdir(parents=True)
        self.listed = self.tmp / "uv-installed.txt"
        self.listed.write_text("0.9.0", encoding="utf-8")
        self.log = self.tmp / "uv-args.txt"
        self.installed("0.9.0", "tugboat.update.installed_version")
        self.uv(self.fake_uv())
        self.runs_from(self.tools / "tugboat/lib/python3.14/site-packages/tugboat")

    def fake_uv(self, *, fail=False, installs=True):
        script = self.tmp / "uv"
        install = 'echo "error: no network" >&2; exit 2' if fail else (f'echo "${{4##*@v}}" > "{self.listed}"' if installs else "true")
        script.write_text(
            "#!/bin/sh\n"
            f'if [ "$1 $2" = "tool dir" ]; then echo "{self.tools}"; exit 0; fi\n'
            f'if [ "$1 $2" = "tool list" ]; then echo "other v1.0.0"; echo "- other"; echo "tugboat v$(cat "{self.listed}")"; echo "- tug"; exit 0; fi\n'
            f'printf "%s\\n" "$@" > "{self.log}"\n'
            f"{install}\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return os.fspath(script)

    def uv(self, program):
        patch = mock.patch.object(update, "find_uv", return_value=program)
        patch.start()
        self.addCleanup(patch.stop)

    def runs_from(self, folder):
        patch = mock.patch.object(update, "package_folder", return_value=Path(folder))
        patch.start()
        self.addCleanup(patch.stop)

    def test_it_installs_the_newest_tagged_release_with_uv_and_says_so(self):
        code, out, err = run("update")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.log.read_text(encoding="utf-8").split("\n")[:4], ["tool", "install", "--force", "git+https://github.com/selcukyucel/tugboat@v0.10.0"])
        self.assertIn("Updating tug from 0.9.0 to 0.10.0 with: uv tool install --force git+https://github.com/selcukyucel/tugboat@v0.10.0", out)
        self.assertIn("Updated tug to 0.10.0. The next sync in each repo regenerates the agent tool files.", out)
        self.assertEqual(self.check_file()["newest"], "0.10.0")

    def test_with_the_newest_release_installed_nothing_is_run(self):
        for version, message in (("0.10.0", "tug 0.10.0 is the newest release. Nothing to do."), ("0.11.0", "which is newer than the newest release, 0.10.0. Nothing to do.")):
            self.installed(version, "tugboat.update.installed_version")
            code, out, _ = run("update")
            self.assertEqual(code, 0)
            self.assertIn(message, out)
        self.assertFalse(self.log.exists())

    def test_check_only_says_what_is_there_and_installs_nothing(self):
        code, out, _ = run("update", "--check")
        self.assertEqual((code, out), (0, "tug 0.9.0 is installed. The newest release is 0.10.0. `tug update` installs it.\n"))
        self.assertFalse(self.log.exists())

    def test_when_the_releases_cannot_be_read_nothing_is_changed(self):
        self.unplug()
        code, _, err = run("update")
        self.assertEqual(code, 1)
        self.assertIn("the releases of Tugboat could not be read", err)
        self.assertIn("Nothing was changed.", err)
        self.assertFalse(self.log.exists())

    def test_without_uv_the_command_to_run_by_hand_is_given(self):
        self.uv(None)
        code, _, err = run("update")
        self.assertEqual(code, 1)
        self.assertIn("uv was not found, so the update cannot run. With uv installed, the command is: uv tool install --force git+https://github.com/selcukyucel/tugboat@v0.10.0", err)

    def test_a_tug_that_uv_did_not_install_is_left_as_it_is(self):
        self.runs_from(self.tmp / "checkout/src/tugboat")
        code, _, err = run("update")
        self.assertEqual(code, 1)
        self.assertIn("which uv did not install as a tool, so it was left as it is", err)
        self.assertFalse(self.log.exists())

    def test_an_install_that_fails_and_an_install_that_changes_nothing_are_both_reported(self):
        self.uv(self.fake_uv(fail=True))
        code, _, err = run("update")
        self.assertEqual(code, 1)
        self.assertIn("uv could not install the release (no network)", err)
        self.uv(self.fake_uv(installs=False))
        code, out, _ = run("update")
        self.assertEqual(code, 1)
        self.assertIn("uv finished, but it lists tug 0.9.0, not 0.10.0.", out)


class VersionInTheReportTests(InitCase):
    def setUp(self):
        super().setUp()
        self.ok(self.ios, "--yes", "--team-repo", TEAM, "--tracker", "jira", "--approval", "owner")
        self.merge()

    def minimum(self, version):
        self.push("profile.json", json.dumps({**PROFILE, "minimum_tool_version": version}))

    def sync(self):
        code, out, err = self.tug(self.ios, "sync")
        self.assertEqual((code, err), (0, ""))
        return out

    def test_a_machine_below_the_minimum_is_told_first_and_given_the_command(self):
        self.assertNotIn("This project needs tug", self.sync())
        self.minimum("99.0.0")
        out = self.sync()
        self.assertTrue(out.startswith("This project needs tug 99.0.0 or newer, and this machine has "), out)
        self.assertIn("Update it with `tug update`. Until then Tugboat opens no pull request in the team repo.", out.splitlines()[0])
        self.assertIn("Copy in this repo: refreshed", out)  # it still syncs
        text = json.loads(hook.session_start(json.dumps({"cwd": os.fspath(self.ios)})))["additionalContext"]
        self.assertEqual(text.splitlines()[1][:27], "This project needs tug 99.0")

    def test_a_proposal_that_is_not_merged_cannot_lower_the_minimum(self):
        self.minimum("99.0.0")
        self.sync()
        approved = json.loads((self.ios / ".tugboat/team/profile.json").read_text(encoding="utf-8"))
        from tugboat import formats, profile

        checked = formats.validate_profile(approved)
        profile.write_pending(self.address, profile.Pending(checked, {**checked, "minimum_tool_version": "0.1.0"}, None, None, None))
        self.assertTrue(self.sync().startswith("This project needs tug 99.0.0"))

    def test_a_tool_below_the_minimum_opens_no_pull_request_and_a_broken_profile_does_not_block_its_repair(self):
        note = self.tmp / "note.md"
        note.write_text("---\ntitle: T\nowner: pat\nrepo: acme/shop-ios\npaths: [\"Sources/**\"]\nlast_verified: 2026-11-13\n---\n\nA note.\n", encoding="utf-8")
        argv = ("team", "propose", "--yes", "--title", "Add a note", "--file", f"knowledge/n.md={note}")
        before = self.proposals()
        self.minimum("99.0.0")
        code, _, err = self.tug(self.ios, *argv)
        self.assertEqual(code, 1)
        self.assertIn("this project needs tug 99.0.0 or newer", err)
        self.assertIn("Nothing was proposed.", err)
        self.assertEqual(self.proposals(), before)
        self.push("profile.json", "{broken\n")
        self.assertEqual(self.tug(self.ios, *argv)[0], 0)
        self.assertEqual(len(self.proposals()), len(before) + 1)

    def test_a_newer_release_is_named_at_the_end_and_a_lookup_that_fails_shows_nothing(self):
        out = self.sync()
        self.assertNotIn("A newer Tugboat", out)
        self.assertNotIn("release", out.lower().replace("released", ""))
        releases.remember(Version(99, 1, 0))
        out = self.sync()
        self.assertRegex(out.splitlines()[-1], r"^A newer Tugboat is released: 99\.1\.0\. This machine has \d+\.\d+\.\d+\. `tug update` installs it\.$")

    def test_with_the_team_repo_unreachable_the_tool_repo_is_not_asked(self):
        os.rename(self.remote, self.tmp / "unplugged.git")
        with mock.patch.object(releases, "read_releases", side_effect=AssertionError("asked while offline")):
            self.assertIn("Sync did not work", self.sync())


if __name__ == "__main__":
    unittest.main()
