import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tugboat import ciworkflows, paths, pretool, profile, recordstore, repolist, settingsmerge

from test_capture import draft
from test_formats import changed
from test_init import IOS_FILES, TEAM
from test_pretool import PROTECTED
from test_records import CLAIM, EVENT
from test_rules import RuleCase
from test_sync import run

SETTINGS = ".claude/settings.local.json"
OWN_HOOK = {"hooks": [{"type": "command", "command": "echo mine"}]}


class RemoveCase(RuleCase):
    """An iOS repo and an Android repo, both set up, both in the merged profile."""

    def remove(self, root, *argv, answers=None):
        return self.tug(root, "remove", *argv, answers=answers)

    def removed(self, root, *argv, answers=None):
        code, out, err = self.remove(root, *argv, answers=answers)
        self.assertEqual((code, err), (0, ""), out)
        return out

    def listed(self):
        return [entry.path for entry in repolist.read()]

    def settings(self, root):
        return json.loads((root / SETTINGS).read_text(encoding="utf-8"))

    def exclude_file(self, root):
        return (root / ".git/info/exclude").read_text(encoding="utf-8")

    def hook(self, root, event, payload):
        here = os.getcwd()
        os.chdir(root)
        try:
            with mock.patch("sys.stdin") as stdin:
                stdin.isatty.return_value = False
                stdin.buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))
                return run("hook", event)
        finally:
            os.chdir(here)


class ListAndConfirmTests(RemoveCase):
    def test_without_a_terminal_it_lists_what_it_would_remove_and_removes_nothing(self):
        code, out, err = self.remove(self.ios)
        self.assertEqual(code, 1)
        self.assertIn("removal needs a person to confirm it", err)
        self.assertIn("Nothing was removed.", err)
        for expected in (
            f"Tugboat is set up in {self.ios} (acme/shop-ios), for the team repo {TEAM}.",
            "Tugboat's 2 hooks and ",
            f"permission rules in {SETTINGS}",
            "generated files under .claude/: .claude/agents/reviewer.md, .claude/rules/tugboat.md",
            "The folder .tugboat/",
            "in this repo's local Git exclude file",
            "It leaves:",
            "The team repo. The project profile still lists this repo",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)
        self.assertTrue(self.set_up(self.ios))
        self.assertEqual(len(settingsmerge.installed_hooks(self.ios)), 2)

    def test_a_no_stops_and_removes_nothing(self):
        code, out, err = self.remove(self.ios, answers=["n"])
        self.assertEqual((code, err), (1, "tug: stopped. Nothing was removed.\n"))
        self.assertEqual(self.questions, ["Remove Tugboat from this repo? [y/N] "])
        self.assertTrue(self.set_up(self.ios))
        self.assertIn(os.fspath(self.ios), self.listed())

    def test_a_yes_takes_everything_out_and_git_sees_nothing(self):
        team_before = self.branches()
        out = self.removed(self.ios, answers=["y", "n"])
        self.assertEqual(len(self.questions), 2)
        self.assertIn("Open a pull request that takes acme/shop-ios out of the profile", self.questions[1])
        self.assertTrue(self.untouched(self.ios))  # no .tugboat/, no .claude/, and a clean `git status`
        self.assertNotIn("tugboat", self.exclude_file(self.ios))
        self.assertEqual(settingsmerge.installed_hooks(self.ios), [])
        for expected in (
            "Removed from this repo:",
            f"Deleted {SETTINGS}, which held nothing but Tugboat's hooks and rules.",
            "Deleted the folder .tugboat/.",
            "Took Tugboat's block out of the local Git exclude file.",
            "A new agent session in this repo loads no Tugboat file, and no hook calls `tug`.",
            "Nothing in the team repo was changed.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)
        self.assertEqual(self.branches(), team_before)
        # The Android repo still uses the clone and the tool, so both stay, and the tool is not offered for uninstall.
        self.assertEqual(self.listed(), [os.fspath(self.android)])
        self.assertIn("The clone of the team repo acme/team stays, because 1 app repo on this machine still uses it", out)
        self.assertIn("Tugboat is still set up in 1 app repo on this machine", out)
        self.assertNotIn("uv tool uninstall", out)
        self.assertTrue((paths.team_repo_clone(self.address) / ".git").exists())
        self.assertTrue(self.set_up(self.android))

    def test_a_repo_that_is_not_set_up_is_left_alone(self):
        plain = self.app("plain", IOS_FILES, "acme/plain")
        out = self.removed(plain)
        self.assertIn(f"Tugboat is not set up in {plain}.", out)
        self.assertIn("Nothing was removed.", out)
        self.assertEqual(self.git(plain, "status", "--porcelain"), "")

    def test_after_removal_no_hook_does_anything_and_init_sets_the_repo_up_again(self):
        self.removed(self.ios, "--yes")
        write = {"cwd": os.fspath(self.ios), "tool_name": "Write", "tool_input": {"file_path": os.fspath(self.ios / "Signing/dist.p12"), "content": "x"}}
        self.assertEqual(self.hook(self.ios, "pre-tool-use", write), (0, "", ""))
        self.assertEqual(self.hook(self.ios, "session-start", {"cwd": os.fspath(self.ios)}), (0, "", ""))
        self.assertTrue(self.untouched(self.ios))
        out = self.ok(self.ios, "--yes", "--team-repo", TEAM)
        self.assertIn("This repo is set up for the project", out)
        self.assertTrue(self.set_up(self.ios))
        self.assertEqual(len(settingsmerge.installed_hooks(self.ios)), 2)
        self.assertIn(os.fspath(self.ios), self.listed())


class WhatStaysTests(RemoveCase):
    def test_the_developers_own_settings_stay_and_git_shows_the_file_again(self):
        settings = self.settings(self.ios)
        settings["permissions"]["allow"] = ["Bash(ls:*)"]
        settings["permissions"]["ask"].insert(0, "Bash(git push:*)")
        settings["hooks"]["SessionStart"].insert(0, OWN_HOOK)
        (self.ios / SETTINGS).write_text(json.dumps(settings), encoding="utf-8")
        out = self.removed(self.ios, "--yes")
        self.assertEqual(self.settings(self.ios), {"permissions": {"allow": ["Bash(ls:*)"], "ask": ["Bash(git push:*)"]}, "hooks": {"SessionStart": [OWN_HOOK]}})
        self.assertIn(f"Took Tugboat's hooks and rules out of {SETTINGS}.", out)
        self.assertIn(f"Git now shows 1 file that Tugboat's exclude block used to hide: {SETTINGS}.", out)
        self.assertEqual(self.git(self.ios, "status", "--porcelain", "--untracked-files=all"), f"?? {SETTINGS}")
        self.assertFalse((self.ios / ".tugboat").exists())

    def test_a_settings_file_that_git_tracks_is_not_changed_and_the_command_fails(self):
        self.git(self.ios, "add", "-f", SETTINGS)
        self.git(self.ios, "commit", "--quiet", "-m", "settings")
        before = (self.ios / SETTINGS).read_bytes()
        code, out, err = self.remove(self.ios, "--yes")
        self.assertEqual((code, err), (1, ""))
        self.assertEqual((self.ios / SETTINGS).read_bytes(), before)
        self.assertIn(f"- {SETTINGS}: Git tracks it in this repo, so Tugboat does not change it.", out)
        self.assertIn(f"2 Tugboat hooks are still in {SETTINGS}, which Git tracks in this repo.", out)
        self.assertNotIn("no hook calls `tug`", out)
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")

    def test_a_generated_file_that_was_committed_stays_and_one_that_was_edited_goes(self):
        self.git(self.ios, "add", "-f", ".claude/rules/tugboat.md")
        self.git(self.ios, "commit", "--quiet", "-m", "pointer")
        skill = next(path for path in sorted((self.ios / ".claude/skills").rglob("SKILL.md")))
        skill.chmod(0o644)
        skill.write_text("edited\n", encoding="utf-8")
        extra = skill.parent / "notes-of-my-own.md"
        extra.write_text("mine\n", encoding="utf-8")
        out = self.removed(self.ios, "--yes")
        self.assertIn("- .claude/rules/tugboat.md: Git tracks it now, so it belongs to the repo.", out)
        self.assertIn(f"1 of them was changed after Tugboat wrote it, and goes too: {skill.relative_to(self.ios).as_posix()}", out)
        self.assertTrue((self.ios / ".claude/rules/tugboat.md").is_file())
        self.assertFalse(skill.exists())
        self.assertEqual(extra.read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(self.git(self.ios, "status", "--porcelain", "--untracked-files=all"), f"?? {extra.relative_to(self.ios).as_posix()}")

    def test_the_generated_files_are_found_from_the_exclude_block_when_their_list_is_gone(self):
        (self.ios / ".tugboat/host-files.json").unlink()
        self.removed(self.ios, "--yes")
        self.assertTrue(self.untouched(self.ios))

    def test_the_ci_workflow_goes_only_while_it_is_tugboats_and_uncommitted(self):
        caller = self.ios / ciworkflows.CALLER
        caller.parent.mkdir(parents=True)
        caller.write_text(ciworkflows.caller_text(self.address), encoding="utf-8")
        out = self.removed(self.ios, "--yes")
        self.assertIn(f"- {ciworkflows.CALLER}, which Tugboat wrote and nobody committed.", out)
        self.assertTrue(self.untouched(self.ios))
        self.assertFalse((self.ios / ".github").exists())

        for root, text, words in (
            (self.android, ciworkflows.caller_text(self.address), "it is committed, so it belongs to the repo"),
            (self.ios, "name: mine\n", "it is not what Tugboat writes"),
        ):
            with self.subTest(words=words):
                if root == self.ios:
                    self.ok(root, "--yes", "--team-repo", TEAM)
                caller = root / ciworkflows.CALLER
                caller.parent.mkdir(parents=True, exist_ok=True)
                caller.write_text(text, encoding="utf-8")
                if root == self.android:
                    self.git(root, "add", ciworkflows.CALLER)
                    self.git(root, "commit", "--quiet", "-m", "scan")
                out = self.removed(root, "--yes")
                self.assertIn(f"- {ciworkflows.CALLER}: {words}", out)
                self.assertEqual(caller.read_text(encoding="utf-8"), text)


class DraftTests(RemoveCase):
    def setUp(self):
        super().setUp()
        self.note = self.ios / ".tugboat/capture/knowledge/auth/token-refresh.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text(draft("Token refresh", ["Shop/Auth/**"]), encoding="utf-8")

    def test_with_yes_the_drafts_need_their_own_answer(self):
        code, out, err = self.remove(self.ios, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("holds drafts that nobody proposed yet. Pass --unpublished keep or --unpublished delete. Nothing was removed.", err)
        self.assertIn("1 file in .tugboat/capture/ is a draft that nobody proposed yet: knowledge/auth/token-refresh.md", out)
        self.assertTrue(self.note.is_file())
        self.assertTrue(self.set_up(self.ios))

    def test_a_person_can_stop_to_propose_them_first(self):
        code, out, err = self.remove(self.ios, answers=["3"])
        self.assertEqual((code, err), (1, "tug: stopped. Nothing was removed.\n"))
        self.assertIn("What should happen to the drafts?", out)
        self.assertIn("`tug capture propose` and `tug decision propose`", out)
        self.assertTrue(self.note.is_file())

    def test_kept_drafts_are_moved_out_of_the_repo(self):
        text = self.note.read_text(encoding="utf-8")
        out = self.removed(self.ios, answers=["1", "y", "n"])
        (kept,) = paths.kept_folder().iterdir()
        self.assertTrue(kept.name.startswith("ios-"))
        self.assertEqual((kept / "capture/knowledge/auth/token-refresh.md").read_text(encoding="utf-8"), text)
        self.assertIn(f"Moved 1 draft file to {kept}.", out)
        self.assertIn(f"move what is in {kept} back into .tugboat/", out)
        self.assertTrue(self.untouched(self.ios))

    def test_deleted_drafts_go_with_the_folder(self):
        self.removed(self.ios, "--yes", "--drafts", "delete")  # the older name of --unpublished still works
        self.assertTrue(self.untouched(self.ios))
        self.assertFalse(paths.kept_folder().exists())


class UnpublishedRecordTests(RemoveCase):
    """Task records live beside the team repo clone, so they are asked about when the clone goes."""

    def setUp(self):
        super().setUp()
        self.claim = recordstore.write(self.address, CLAIM)
        self.event = recordstore.write(self.address, changed(EVENT, id="20261115T101600Z-aaaaaaaaaaaa", at="2026-11-15T10:16:00Z"))
        self.clone = paths.team_repo_clone(self.address)
        self.store = paths.records_worktree(self.address)

    def test_removing_tugboat_from_a_repo_does_not_touch_the_records(self):
        out = self.removed(self.ios, "--yes")
        self.assertTrue(self.untouched(self.ios))
        self.assertNotIn("task record", out)  # the Android repo still uses the clone, so nothing is asked
        self.assertEqual(recordstore.read_all(self.address).records, (self.claim, self.event))

    def test_the_clone_is_kept_until_the_records_have_an_answer(self):
        out = self.removed(self.tmp, "--all", "--yes", "--delete-clone")
        self.assertIn(f"Its task records are at {self.store}. 2 of them were not published yet, and would be lost with the clone: github-projects/acme/shop-ios/311/", out)
        self.assertIn("The clone was kept, because it holds task records that were not published. With --delete-clone, pass --unpublished keep or --unpublished delete.", out)
        self.assertTrue((self.clone / ".git").exists())
        self.assertEqual(len(recordstore.read_all(self.address).records), 2)

    def test_kept_records_are_moved_out_as_they_are_before_the_clone_goes(self):
        out = self.removed(self.tmp, "--all", "--yes", "--delete-clone", "--unpublished", "keep")
        (kept,) = paths.kept_folder().iterdir()
        self.assertTrue(kept.name.startswith("team-records-"))
        self.assertIn(f"Moved 2 task records to {kept}.", out)
        for stored in (self.claim, self.event):
            self.assertEqual(json.loads((kept / stored.path).read_text(encoding="utf-8")), stored.record)
        self.assertIn("The clone was deleted. Nothing in GitHub changed.", out)
        self.assertFalse(self.clone.exists())
        self.assertFalse(self.store.exists())
        self.assertFalse((paths.tug_home() / "records").exists())

    def test_deleted_records_go_with_the_clone(self):
        self.removed(self.tmp, "--all", "--yes", "--delete-clone", "--unpublished", "delete")
        self.assertFalse(self.clone.exists())
        self.assertFalse(self.store.exists())
        self.assertFalse(paths.kept_folder().exists())

    def test_a_person_is_asked_and_can_keep_the_clone(self):
        self.removed(self.android, "--yes")
        out = self.removed(self.ios, answers=["y", "n", "y", "3"])
        self.assertEqual(self.questions[2], "Delete this clone? [y/N] ")
        self.assertIn("What should happen to the task records that were not published?", out)
        self.assertIn("Publishing task records is not built yet.", out)
        self.assertIn("The clone was kept, because it holds task records that were not published.", out)
        self.assertTrue((self.clone / ".git").exists())
        self.assertEqual(len(recordstore.read_all(self.address).records), 2)

    def test_published_records_do_not_hold_the_clone_back(self):
        self.git(self.store, "add", "-A")
        self.git(self.store, "commit", "--quiet", "-m", "records")
        out = self.removed(self.tmp, "--all", "--yes", "--delete-clone")
        self.assertNotIn("not published yet", out)
        self.assertFalse(self.clone.exists())
        self.assertFalse(self.store.exists())


class TeamRepoTests(RemoveCase):
    def test_the_profile_pull_request_is_opened_only_when_asked_for(self):
        main = self.git(self.remote, "rev-parse", "main")
        before = set(self.proposals())
        out = self.removed(self.ios, "--yes", "--profile-pr")
        (branch,) = set(self.proposals()) - before
        self.assertRegex(branch, r"^tug/profile-")
        self.assertEqual([repo["github"] for repo in self.proposed(branch)["app_repos"]], ["acme/shop-android"])
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%s", branch), "Take acme/shop-ios out of the project profile")
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), main)
        self.assertIn("Nothing was merged. A person reviews and merges it in GitHub.", out)
        # Until the merge, the other repo on this machine works from the proposed profile.
        self.assertIsNotNone(self.pending())

    def test_a_pull_request_that_cannot_be_made_leaves_no_local_profile_behind(self):
        with mock.patch.object(profile.teampr, "propose", side_effect=profile.TugError("the team repo could not be reached")):
            out = self.removed(self.ios, "--yes", "--profile-pr")
        self.assertIn("The pull request could not be made: the team repo could not be reached", out)
        self.assertIn("change `profile.json` through a pull request in the team repo", out)
        self.assertIsNone(self.pending())
        self.assertTrue(self.untouched(self.ios))


class WholeMachineTests(RemoveCase):
    def test_init_and_sync_keep_the_list_of_repos(self):
        self.assertEqual(self.listed(), [os.fspath(self.ios), os.fspath(self.android)])
        paths.app_repos_file().unlink()  # as if an older tug had set the repos up
        self.assertEqual(self.listed(), [])
        self.instructions(self.android)
        self.assertEqual(self.listed(), [os.fspath(self.android)])
        self.assertEqual(repolist.read()[0].team_repo, TEAM)

    def test_all_asks_for_each_repo_and_a_no_skips_only_that_one(self):
        out = self.removed(self.tmp, "--all", answers=["n", "y", "n"])
        self.assertIn(f"Skipped. Nothing was removed from {self.ios}.", out)
        self.assertTrue(self.set_up(self.ios))
        self.assertTrue(self.untouched(self.android))
        self.assertEqual(self.listed(), [os.fspath(self.ios)])

    def test_all_with_yes_clears_the_machine_and_names_the_uninstall_command(self):
        clone = paths.team_repo_clone(self.address)
        out = self.removed(self.tmp, "--all", "--yes")
        self.assertTrue(self.untouched(self.ios))
        self.assertTrue(self.untouched(self.android))
        self.assertEqual(self.listed(), [])
        # The clone is an offer of its own, so --yes alone keeps it.
        self.assertTrue((clone / ".git").exists())
        self.assertIn(f"No app repo on this machine's list uses the clone of the team repo acme/team any more. It is at {clone}.", out)
        self.assertIn("The clone was kept.", out)
        self.assertIn("Tugboat does not uninstall itself. To uninstall it: `uv tool uninstall tugboat`", out)
        self.assertIn(f"Its folder {paths.tug_home()} stays until you delete it.", out)

        out = self.removed(self.tmp, "--all", "--yes", "--delete-clone")
        self.assertIn("Tugboat is not set up in any app repo on this machine's list.", out)
        self.assertIn("The clone was deleted. Nothing in GitHub changed.", out)
        self.assertFalse(clone.exists())
        self.assertFalse(paths.sync_state_file(self.address).exists())
        self.assertFalse((paths.tug_home() / "team-repos").exists())

    def test_a_person_is_asked_about_the_clone_and_told_what_goes_with_it(self):
        self.removed(self.android, "--yes")
        (paths.team_repo_clone(self.address) / "scratch.txt").write_text("mine\n", encoding="utf-8")
        out = self.removed(self.ios, answers=["y", "n", "y"])
        self.assertEqual(self.questions[2], "Delete this clone? [y/N] ")
        self.assertIn("It holds 1 local edit that would be lost: scratch.txt.", out)
        self.assertFalse(paths.team_repo_clone(self.address).exists())

    def test_a_listed_folder_that_is_gone_leaves_the_list(self):
        repolist.record(self.tmp / "gone", TEAM)
        out = self.removed(self.tmp, "--all", "--yes")
        self.assertIn(f"{self.tmp / 'gone'} is not an app repo any more. It was taken off this machine's list.", out)
        self.assertEqual(self.listed(), [])


class HookGuardTests(unittest.TestCase):
    def test_which_commands_remove_tugboat(self):
        for command in ("tug remove", "/Users/pat/.local/bin/tug remove --yes", "cd app && tug remove --all --yes", "TUG_HOME=/x tug --version; tug remove", "env tug remove"):
            with self.subTest(command=command):
                self.assertTrue(pretool.removes_tugboat(command))
        for command in ("tug sync", "tug capture drop remove.md", "git remote remove origin", "echo tug remove", "tug team propose --remove x.md --title t", "tugx remove"):
            with self.subTest(command=command):
                self.assertFalse(pretool.removes_tugboat(command))

    def test_an_agent_is_asked_on_claude_code_and_denied_on_copilot(self):
        payload = {"tool_name": "Bash", "cwd": "/repo", "tool_input": {"command": "tug remove --yes"}}
        decision = pretool.decide({**payload, "transcript_path": "/x.jsonl"}, Path("/repo"), PROTECTED, lambda _folder: "work")
        self.assertEqual(decision.action, "ask")
        self.assertIn("takes Tugboat's checks out of this repo", decision.reason)
        decision = pretool.decide(payload, Path("/repo"), PROTECTED, lambda _folder: "work")
        self.assertEqual(decision.action, "deny")
        self.assertIn("who can run it themselves in a terminal", decision.reason)


if __name__ == "__main__":
    unittest.main()
