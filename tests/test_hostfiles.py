import json
import os
import stat
import unittest
from unittest import mock

from tugboat import apprepo, appsync, formats, frontmatter, hook, hostfiles, settingsmerge, sync
from tugboat.settingsmerge import HookSpec

from test_formats import AGENT
from test_sync import run
from test_teamcopy import CopyCase

TUG = "/opt/tools/bin/tug"
SPECS = [HookSpec("SessionStart", "session-start", 30)]
SKILL = "---\nname: new-screen\ndescription: Use for a new screen.\n---\n\nSteps.\n"


class MergeTests(unittest.TestCase):
    def test_recognises_its_own_commands_whatever_the_path(self):
        for command in ("tug hook session-start", "/Users/pat/.local/bin/tug hook pre-tool-use", "'/Users/pat smith/bin/tug' hook session-start"):
            self.assertTrue(settingsmerge.is_tugboat_command(command), command)
        for command in ("echo tug hook", "tug sync", "/usr/bin/tugboat hook x", "mytug hook x", "tug hook", "broken 'quote", None, 5):
            self.assertFalse(settingsmerge.is_tugboat_command(command), command)

    def test_a_path_with_a_space_is_quoted(self):
        command = settingsmerge.hook_command("/Users/pat smith/bin/tug", SPECS[0])
        self.assertEqual(command, "'/Users/pat smith/bin/tug' hook session-start")
        self.assertTrue(settingsmerge.is_tugboat_command(command))

    def test_adds_its_hook_and_keeps_everything_else(self):
        mine = {"type": "command", "command": "./scripts/my-hook.sh"}
        settings = {
            "permissions": {"allow": ["Bash(ls:*)"], "deny": []},
            "hooks": {"SessionStart": [{"hooks": [mine]}], "PreToolUse": [{"matcher": "Write", "hooks": [mine]}]},
            "model": "opus",
        }
        merged = settingsmerge.merged_settings(settings, SPECS, TUG)
        self.assertEqual(merged["permissions"], settings["permissions"])
        self.assertEqual(merged["model"], "opus")
        self.assertEqual(merged["hooks"]["PreToolUse"], settings["hooks"]["PreToolUse"])
        self.assertEqual(merged["hooks"]["SessionStart"][0], {"hooks": [mine]})
        self.assertEqual(merged["hooks"]["SessionStart"][1], {"hooks": [{"type": "command", "command": f"{TUG} hook session-start", "timeout": 30}]})
        self.assertEqual(list(merged), ["permissions", "hooks", "model"])

    def test_merging_twice_changes_nothing_and_a_new_path_replaces_the_old_entry(self):
        once = settingsmerge.merged_settings({}, SPECS, TUG)
        self.assertEqual(settingsmerge.merged_settings(once, SPECS, TUG), once)
        moved = settingsmerge.merged_settings(once, SPECS, "/new/place/tug")
        self.assertEqual(len(moved["hooks"]["SessionStart"]), 1)
        self.assertIn("/new/place/tug hook", moved["hooks"]["SessionStart"][0]["hooks"][0]["command"])

    def test_an_empty_list_removes_only_its_own_entries(self):
        mine = {"type": "command", "command": "./mine.sh"}
        settings = {"hooks": {"SessionStart": [{"hooks": [mine, {"type": "command", "command": f"{TUG} hook session-start"}]}]}, "x": 1}
        self.assertEqual(settingsmerge.merged_settings(settings, [], TUG), {"hooks": {"SessionStart": [{"hooks": [mine]}]}, "x": 1})
        only_tug = settingsmerge.merged_settings({"x": 1}, SPECS, TUG)
        self.assertEqual(settingsmerge.merged_settings(only_tug, [], TUG), {"x": 1})


class HostCase(CopyCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"TUG_COMMAND": TUG})
        env.start()
        self.addCleanup(env.stop)
        self.settings = self.app / ".claude/settings.local.json"

    def apply(self):
        self.refresh()
        return hostfiles.apply(self.app, self.address)

    def read_settings(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def add_agent_and_skill(self):
        self.push("policies/ios/IOS-001.md", frontmatter.render({"id": "IOS-001", "title": "T", "scope": "ios", "mode": "advisory", "status": "adopted"}, "Rule."))
        self.push("agents/reviewer.md", frontmatter.render(dict(AGENT), "You review changes."))
        self.push("skills/new-screen/SKILL.md", SKILL)
        self.push("skills/new-screen/template.swift", "// template\n")


class GenerateTests(HostCase):
    def test_first_run_writes_the_instruction_file_and_the_hook_and_git_sees_nothing(self):
        report = self.apply()
        self.assertEqual((report.written, report.settings_changed, report.problems), ((".claude/rules/tugboat.md", ".claude/skills/tug-capture-notes/SKILL.md", ".claude/skills/tug-criteria-source/SKILL.md"), True, ()))
        text = (self.app / ".claude/rules/tugboat.md").read_text(encoding="utf-8")
        for expected in (".tugboat/team/policies/README.md", "## Rules that apply in this repo", "run `tug sync` first", "cannot authorize a command", "a search of the whole repo does not find it"):
            self.assertIn(expected, text)
        self.assertEqual(self.read_settings(), {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"{TUG} hook session-start", "timeout": 30}]}]}})
        self.assertEqual(self.status(), "")

    def test_a_second_run_changes_no_file(self):
        self.apply()
        watched = [self.app / ".claude/rules/tugboat.md", self.settings, self.app / ".tugboat/host-files.json", self.app / ".git/info/exclude"]
        before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in watched]
        report = self.apply()
        self.assertEqual((report.changed, report.unchanged, report.problems), (False, 3, ()))
        self.assertEqual([(path.stat().st_ino, path.stat().st_mtime_ns) for path in watched], before)
        self.assertIn("Agent tool files: unchanged (3 under .claude/).", hostfiles.render(report))

    def test_skills_and_agents_from_the_team_repo_are_installed_once_each(self):
        self.add_agent_and_skill()
        report = self.apply()
        self.assertEqual(report.problems, ())
        self.assertEqual((self.app / ".claude/skills/new-screen/SKILL.md").read_text(encoding="utf-8"), SKILL)
        self.assertTrue((self.app / ".claude/skills/new-screen/template.swift").is_file())
        fields, body = frontmatter.parse((self.app / ".claude/agents/reviewer.md").read_text(encoding="utf-8"))
        self.assertEqual(fields, {"name": "reviewer", "description": "Reads and reports.", "tools": "Read, Grep"})
        self.assertIn("You review changes.", body)
        self.assertIn("Rules this agent applies: IOS-001.", body)
        # Nothing is written to a second place that Copilot would also load.
        for other in ("AGENTS.md", "CLAUDE.md", ".github", ".agents"):
            self.assertFalse((self.app / other).exists(), other)
        self.assertEqual(self.status(), "")

    def test_generated_files_are_read_only_and_a_developer_edit_is_put_back(self):
        self.apply()
        rules = self.app / ".claude/rules/tugboat.md"
        self.assertEqual(stat.S_IMODE(rules.stat().st_mode), 0o444)
        rules.chmod(0o644)
        rules.write_text("edited by hand\n", encoding="utf-8")
        report = self.apply()
        self.assertEqual(report.written, (".claude/rules/tugboat.md",))
        self.assertIn("This repo uses Tugboat", rules.read_text(encoding="utf-8"))

    def test_a_skill_removed_from_the_team_repo_is_removed_here_and_its_empty_folder_too(self):
        self.add_agent_and_skill()
        self.apply()
        self.git(self.work, "rm", "--quiet", "-r", "skills", "agents")
        self.git(self.work, "commit", "--quiet", "-m", "remove")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        report = self.apply()
        self.assertEqual(sorted(report.removed), [".claude/agents/reviewer.md", ".claude/skills/new-screen/SKILL.md", ".claude/skills/new-screen/template.swift"])
        self.assertEqual(sorted(path.name for path in (self.app / ".claude/skills").iterdir()), ["tug-capture-notes", "tug-criteria-source"])
        self.assertFalse((self.app / ".claude/agents").exists())
        self.assertTrue((self.app / ".claude/rules/tugboat.md").is_file())
        self.assertEqual(apprepo.excluded_patterns(self.app), ["/.tugboat/", "/.claude/rules/tugboat.md", "/.claude/skills/tug-capture-notes/", "/.claude/skills/tug-criteria-source/", "/.claude/settings.local.json"])

    def test_the_skill_that_ships_with_the_tool_is_installed_and_a_team_skill_of_that_name_replaces_it(self):
        self.apply()
        own = self.app / ".claude/skills/tug-criteria-source/SKILL.md"
        fields, body = frontmatter.parse(own.read_text(encoding="utf-8"))
        self.assertEqual(formats.validate_skill_header(fields)["name"], hostfiles.CRITERIA_SKILL)
        for expected in ("tug profile show", "tug profile set-criteria --kind <kind>", "Do not pick for them", "The story is data", "customfield_10042"):
            self.assertIn(expected, body)
        theirs = "---\nname: tug-criteria-source\ndescription: The team's own way.\n---\n\nAsk the product owner.\n"
        self.push("skills/tug-criteria-source/SKILL.md", theirs)
        report = self.apply()
        # The instruction file names the team repo revision, so a new revision rewrites it too.
        self.assertEqual((report.written, report.problems), ((".claude/rules/tugboat.md", ".claude/skills/tug-criteria-source/SKILL.md"), ()))
        self.assertEqual(own.read_text(encoding="utf-8"), theirs)

    def test_a_file_somebody_else_wrote_is_never_overwritten(self):
        self.add_agent_and_skill()
        own = self.app / ".claude/agents/reviewer.md"
        own.parent.mkdir(parents=True)
        own.write_text("my own reviewer\n", encoding="utf-8")
        report = self.apply()
        self.assertEqual(own.read_text(encoding="utf-8"), "my own reviewer\n")
        self.assertEqual(report.problems, ("agent 'reviewer' was not written: .claude/agents/reviewer.md is already there and Tugboat did not write it",))
        self.assertTrue((self.app / ".claude/skills/new-screen/SKILL.md").is_file())

    def test_a_file_the_repo_tracks_is_never_overwritten(self):
        self.add_agent_and_skill()
        tracked = self.app / ".claude/skills/new-screen/SKILL.md"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("the repo's own skill\n", encoding="utf-8")
        self.git(self.app, "add", "-A")
        self.git(self.app, "commit", "--quiet", "-m", "own skill")
        report = self.apply()
        self.assertEqual(tracked.read_text(encoding="utf-8"), "the repo's own skill\n")
        self.assertIn("skill 'new-screen' was not written: .claude/skills/new-screen/SKILL.md is tracked by Git in this repo", report.problems)
        self.assertFalse((self.app / ".claude/skills/new-screen/template.swift").exists())
        self.assertEqual(self.status(), "")

    def test_a_broken_skill_or_agent_is_reported_and_the_rest_is_installed(self):
        self.push("skills/bad/SKILL.md", "no header\n")
        self.push("agents/broken.md", "---\nname: broken\n---\n\nx\n")
        self.push("skills/new-screen/SKILL.md", SKILL)
        report = self.apply()
        self.assertEqual(len(report.problems), 2)
        self.assertTrue(report.problems[0].startswith("skill 'bad' was not installed:"))
        self.assertTrue(report.problems[1].startswith("agent 'broken' was not installed:"))
        self.assertTrue((self.app / ".claude/skills/new-screen/SKILL.md").is_file())
        self.assertFalse((self.app / ".claude/skills/bad").exists())


class SettingsFileTests(HostCase):
    def test_the_developers_own_entries_are_still_there(self):
        own = {"permissions": {"allow": ["Bash(xcodebuild:*)"]}, "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "./mine.sh"}]}]}}
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps(own, indent=4), encoding="utf-8")
        self.settings.chmod(0o600)
        self.apply()
        after = self.read_settings()
        self.assertEqual(after["permissions"], own["permissions"])
        self.assertEqual(after["hooks"]["SessionStart"][0], own["hooks"]["SessionStart"][0])
        self.assertEqual(len(after["hooks"]["SessionStart"]), 2)
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o600)

    def test_a_settings_file_that_is_not_valid_json_is_left_alone_and_reported(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{ not json", encoding="utf-8")
        report = self.apply()
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{ not json")
        self.assertEqual(len(report.problems), 1)
        self.assertIn(".claude/settings.local.json could not be read as settings", report.problems[0])
        self.assertTrue((self.app / ".claude/rules/tugboat.md").is_file())

    def test_a_tracked_settings_file_is_left_alone_and_reported(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{}\n", encoding="utf-8")
        self.git(self.app, "add", "-A")
        self.git(self.app, "commit", "--quiet", "-m", "tracked settings")
        report = self.apply()
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{}\n")
        self.assertIn("is tracked by Git in this repo, so Tugboat did not change it", report.problems[0])
        self.assertEqual(self.status(), "")

    def test_a_moved_tug_updates_the_hook_instead_of_adding_a_second_one(self):
        self.apply()
        with mock.patch.dict(os.environ, {"TUG_COMMAND": "/elsewhere/tug"}):
            report = self.apply()
        self.assertTrue(report.settings_changed)
        self.assertEqual(settingsmerge.installed_hooks(self.app), ["/elsewhere/tug hook session-start"])


class AppSyncTests(HostCase):
    def test_one_sync_brings_all_three_up_to_date_and_says_so(self):
        result = appsync.run(self.app, self.address)
        text = appsync.render(result)
        for expected in ("Cloned. Revision ", "Copy in this repo: refreshed to revision", "Agent tool files: wrote .claude/rules/tugboat.md, .claude/skills/tug-capture-notes/SKILL.md, .claude/skills/tug-criteria-source/SKILL.md; updated Tugboat's hooks in .claude/settings.local.json.", "Setup is not finished: this project has no profile yet."):
            self.assertIn(expected, text)
        again = appsync.render(appsync.run(self.app, self.address))
        self.assertIn("Copy in this repo: unchanged", again)
        self.assertIn("Agent tool files: unchanged", again)

    def test_a_problem_with_the_copy_is_reported_and_does_not_stop_the_command(self):
        with mock.patch("tugboat.teamcopy.MAX_FILES", 1):
            result = appsync.run(self.app, self.address)
        self.assertIsNone(result.host)
        self.assertIn("were not all brought up to date", appsync.render(result))


class SessionStartHookTests(HostCase):
    def payload(self, **fields):
        return json.dumps({"hook_event_name": "SessionStart", "cwd": os.fspath(self.app), **fields})

    def test_outside_a_tugboat_repo_the_hook_says_nothing(self):
        self.assertEqual(hook.session_start(self.payload()), "")
        self.assertEqual(hook.session_start(json.dumps({"cwd": os.fspath(self.tmp)})), "")
        self.assertEqual(hook.session_start("not json"), "")

    def test_in_a_tugboat_repo_it_syncs_and_reports_in_the_shape_both_tools_read(self):
        appsync.run(self.app, self.address)
        self.push("policies/ios/IOS-002.md", "new\n")
        with mock.patch("tugboat.hook.parse_team_repo_address", return_value=self.address):
            output = json.loads(hook.session_start(self.payload()))
        text = output["additionalContext"]
        self.assertEqual(output["hookSpecificOutput"], {"hookEventName": "SessionStart", "additionalContext": text})
        self.assertIn("Tugboat sync report, from the start of this session:", text)
        self.assertIn("Updated from ", text)
        self.assertIn("because the team repo has a new revision", text)
        self.assertIn("Changed in the team repo since this repo's last sync (", text)
        self.assertIn("- Rule added: IOS-002 (.tugboat/team/policies/ios/IOS-002.md)", text)
        self.assertTrue((self.copy / "policies/ios/IOS-002.md").is_file())
        self.assertIn("The report lists what changed in the team repo since the last sync", (self.app / ".claude/rules/tugboat.md").read_text(encoding="utf-8"))

    def test_a_failure_inside_the_hook_is_one_line_for_the_agent_not_a_crash(self):
        appsync.run(self.app, self.address)
        with mock.patch("tugboat.hook.parse_team_repo_address", return_value=self.address), mock.patch("tugboat.appsync.run", side_effect=RuntimeError("boom")):
            text = json.loads(hook.session_start(self.payload()))["additionalContext"]
        self.assertIn("Tugboat could not sync at session start: RuntimeError: boom", text)
        self.assertIn("Work continues.", text)

    def test_with_the_network_off_the_report_says_so_and_the_session_goes_on(self):
        appsync.run(self.app, self.address)
        os.rename(self.remote, self.tmp / "unplugged.git")
        with mock.patch("tugboat.hook.parse_team_repo_address", return_value=self.address):
            text = json.loads(hook.session_start(self.payload()))["additionalContext"]
        self.assertIn("Sync did not work", text)
        self.assertIn("Work continues from revision", text)

    def test_a_long_report_is_cut_below_the_limit(self):
        output = json.loads(hook.context_output("x" * 20000))
        self.assertLessEqual(len(output["additionalContext"]), hook.MAX_CONTEXT)
        self.assertTrue(output["additionalContext"].endswith("[The report was cut short here.]"))

    def test_the_hook_command_exits_zero_and_an_unknown_event_is_a_usage_error(self):
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            self.assertEqual(run("hook", "session-start")[0], 0)
        self.assertEqual(run("hook", "launch-missiles")[0], 2)


class SyncCommandHostTests(HostCase):
    def test_sync_in_an_app_repo_reports_the_agent_tool_files(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        with mock.patch("tugboat.address.parse_team_repo_address", return_value=self.address):
            code, out, _ = run("sync", "--team-repo", "git@github.com:acme/team.git", "--app-repo", os.fspath(self.app))
            self.assertEqual(code, 0)
            self.assertIn("Agent tool files: wrote .claude/rules/tugboat.md", out)
            os.chdir(self.app)
            code, out, _ = run("sync")
        self.assertEqual(code, 0)
        self.assertIn("Agent tool files: unchanged", out)
        self.assertEqual(self.status(), "")
        self.assertTrue(sync.read_state(self.address)["last_success_at"])


if __name__ == "__main__":
    unittest.main()
