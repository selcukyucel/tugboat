import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tugboat import apprepo, doctor, formats, frontmatter, hook, observed, releases, sync, team_repo, update
from tugboat.address import parse_team_repo_address
from tugboat.version import Version

from test_init import TEAM
from test_rules import RuleCase

PROGRAMS = {
    "claude": (True, "2.1.325 (Claude Code)"),
    "copilot": (True, "GitHub Copilot CLI 1.0.112"),
    "code": (True, "1.146.0"),
    "xcodebuild": (True, "Xcode 26.3"),
    "java": (True, 'openjdk version "17.0.9" 2023-10-17'),
    "uv": (True, "uv 0.9.13"),
}
SECRET = "figd_NEVER-SHOW-THIS"


class DoctorCase(RuleCase):
    """An iOS repo and an Android repo that are set up, on a machine where every program answers."""

    def setUp(self):
        super().setUp()
        self.userhome = self.tmp / "userhome"
        self.userhome.mkdir()
        self.programs = dict(PROGRAMS)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        tug = self.bin / "tug"
        tug.write_text("#!/bin/sh\n", encoding="utf-8")
        tug.chmod(0o755)
        clean = {key: value for key, value in os.environ.items() if key not in {"JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "COPILOT_HOME", "CLAUDE_CONFIG_DIR", "AI_AGENT"}}
        for patch in (
            mock.patch.dict(os.environ, {**clean, "TUG_COMMAND": os.fspath(tug)}, clear=True),
            mock.patch.object(doctor, "user_home", lambda: self.userhome),
            mock.patch.object(doctor, "find_program", lambda name: f"/fake/{name}" if name in self.programs else None),
            mock.patch.object(doctor, "program_output", lambda argv, timeout=0: self.programs[os.path.basename(argv[0])]),
            mock.patch.object(update, "find_uv", lambda: "/fake/uv" if "uv" in self.programs else None),
            mock.patch.object(update, "installed_by_uv", lambda uv: True),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        # A sync with the real path of `tug` behind the hooks, as on a machine where it is installed.
        for root in (self.ios, self.android):
            self.assertEqual(self.tug(root, "sync")[0], 0)

    def trust(self, *folders, raw=None):
        folder = self.userhome / ".copilot"
        folder.mkdir(exist_ok=True)
        text = raw if raw is not None else "// This file is managed automatically.\n" + json.dumps({"trustedFolders": [os.fspath(folder) for folder in folders]})
        (folder / "config.json").write_text(text, encoding="utf-8")

    def finish_setup(self):
        """The two project steps a fresh project still has open: the criteria source and the AI use policy."""
        self.assertEqual(self.tug(self.ios, "profile", "set-criteria", "--kind", "section", "--name", "Acceptance criteria", "--yes")[0], 0)
        self.merge()
        self.adopt(("policies/ai-use.md", team_repo.starter_layout()["policies/ai-use.md"].replace("status: example", "status: adopted")))
        for root in (self.ios, self.android):
            self.assertEqual(self.tug(root, "sync")[0], 0)

    def doctor(self, root):
        code, out, err = self.tug(root, "doctor")
        self.assertEqual(err, "")
        return code, out

    def group(self, out, title):
        """The lines of one group of the report."""
        lines = out.splitlines()
        start = next((index for index, line in enumerate(lines) if line.startswith(f"{title}: ")), None)
        if start is None:
            return ""
        end = next((index for index in range(start + 1, len(lines)) if not lines[index].strip()), len(lines))
        return "\n".join(lines[start:end])


class ReadyTests(DoctorCase):
    def test_a_repo_that_is_ready_has_no_failure_and_names_what_it_could_not_test(self):
        self.finish_setup()
        self.trust(self.ios)
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 0, out)
        self.assertNotIn("Failed:", out)
        passed = self.group(out, "Passed")
        for expected in (
            "The project needs",
            "uv is installed (uv 0.9.13).",
            "Claude Code 2.1.325 is installed (2.1.295 or newer is needed).",
            "GitHub Copilot CLI is installed (GitHub Copilot CLI 1.0.112).",
            "Copilot CLI trusts this folder, so it loads the hooks.",
            "VS Code is installed (1.146.0).",
            "iOS toolchain: Xcode 26.3 is selected.",
            "The clone of the team repo acme/team is on 'main' at revision",
            "the newest this machine has fetched",
            "under `.claude/` are as Tugboat wrote them.",
            f"the `tug` they call is there ({self.bin / 'tug'}).",
            "Git ignores everything Tugboat generated in this repo.",
            "This repo is in the approved project profile of 'team' as acme/shop-ios (iOS).",
            "No MCP server is configured in the settings files of the project's agent tools.",
            "1 agent definition from the team's agent registry is installed in this repo.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, passed)
        untested = self.group(out, "Untested")
        self.assertIn("Whether VS Code trusts this workspace cannot be read from disk.", untested)
        self.assertIn("MCP servers that an account, a plugin, or an extension adds are not in a settings file", untested)
        session = out[out.index("== Observed in a session") :]
        for name in ("Claude Code", "GitHub Copilot CLI", "GitHub Copilot in VS Code"):
            self.assertIn(f"- {name} has not been checked inside a session in this repo, so whether its hooks really run is not known.", session)
        self.assertEqual(out.splitlines()[-1], "Nothing failed. 5 things could not be tested, so this is not a full pass.")

    def test_the_four_results_are_reported_apart_and_a_failure_comes_first(self):
        (self.ios / ".claude/agents/mine.md").write_text("mine\n", encoding="utf-8")
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        titles = [line.split(":")[0] for line in out.splitlines() if line.split(":")[0] in {"Failed", "Untested", "Advisory", "Passed"}]
        self.assertEqual(titles, ["Failed", "Untested", "Advisory", "Passed", "Untested"])
        self.assertLess(out.index("== On disk"), out.index("== Observed in a session"))
        self.assertIn("Not ready: ", out.splitlines()[-1])
        self.assertIn("Nothing was changed.", out)

    def test_it_changes_no_file(self):
        def snapshot(root):
            return {os.fspath(path): path.stat().st_mtime_ns for path in root.rglob("*") if ".git/" not in os.fspath(path) and path.is_file()}

        before = snapshot(self.ios), snapshot(self.tmp / "home")
        self.doctor(self.ios)
        self.assertEqual((snapshot(self.ios), snapshot(self.tmp / "home")), before)

    def test_outside_an_app_repo_only_the_machine_is_checked(self):
        folder = self.tmp / "elsewhere"
        folder.mkdir()
        code, out = self.doctor(folder)
        self.assertEqual(code, 0, out)
        self.assertIn("No app repo here, so only this machine was checked.", out)
        self.assertIn("Claude Code 2.1.325 is installed", out)
        for absent in ("toolchain", "hooks", "Copilot CLI trusts", "workspace"):
            self.assertNotIn(absent, out)

    def test_a_repo_that_init_never_saw_fails_and_names_init(self):
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "// p\n"}, "acme/other")
        code, out = self.doctor(other)
        self.assertEqual(code, 1)
        self.assertIn("- This repo is not set up with Tugboat.\n  To repair: Run `tug init` in this repo, in a terminal.", out)


class MachineTests(DoctorCase):
    def test_an_old_claude_code_fails_and_a_missing_or_unreadable_one_is_untested(self):
        self.programs["claude"] = (True, "2.1.294 (Claude Code)")
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        self.assertIn("Claude Code 2.1.294 is installed, and Tugboat needs 2.1.295 or newer.", self.group(out, "Failed"))
        self.programs["claude"] = (True, "nonsense")
        self.assertIn("The version of Claude Code could not be read", self.group(self.doctor(self.ios)[1], "Untested"))
        del self.programs["claude"]
        out = self.doctor(self.ios)[1]
        self.assertIn("Claude Code (`claude`) was not found on the PATH. If you use it, it must be 2.1.295 or newer.", self.group(out, "Untested"))
        self.assertNotIn("Claude Code 2.", self.group(out, "Passed") + self.group(out, "Failed"))

    def test_a_tool_below_the_project_minimum_fails_and_the_newest_release_seen_is_named(self):
        releases.remember(Version(99, 1, 0))
        (below,) = doctor.check_tool_version({"minimum_tool_version": "99.0.0"})
        self.assertEqual((below.result, below.fix), (doctor.FAILED, "Run `tug update`."))
        self.assertIn("the project needs 99.0.0 or newer. The newest release this machine has seen is 99.1.0.", below.text)
        (fine,) = doctor.check_tool_version({"minimum_tool_version": "0.1.0"})
        self.assertEqual(fine.result, doctor.PASSED)
        self.assertIn("A newer release exists: `tug update` installs it.", fine.text)

    def test_without_uv_the_update_cannot_run_and_that_is_a_failure(self):
        del self.programs["uv"]
        self.assertIn("uv is not on the PATH, so `tug update` cannot run.", self.group(self.doctor(self.ios)[1], "Failed"))

    def test_a_folder_copilot_does_not_trust_fails_because_no_hook_runs_there(self):
        out = self.doctor(self.ios)[1]
        self.assertIn("Copilot CLI does not trust this folder, so it runs no hook here", self.group(out, "Failed"))
        self.assertIn("add the folder to `trustedFolders` in ~/.copilot/config.json", out)
        self.trust(self.android)
        self.assertIn("Copilot CLI does not trust this folder", self.group(self.doctor(self.ios)[1], "Failed"))
        self.assertIn("Copilot CLI trusts this folder", self.group(self.doctor(self.android)[1], "Passed"))

    def test_a_trusted_parent_folder_and_an_unreadable_config_are_untested_not_passed(self):
        self.trust(self.tmp)
        out = self.doctor(self.ios)[1]
        self.assertIn("Copilot CLI trusts a folder above this one. Whether that covers this folder is not known.", self.group(out, "Untested"))
        self.trust(raw="{ not json")
        out = self.doctor(self.ios)[1]
        self.assertIn("Whether Copilot CLI trusts this folder could not be read", self.group(out, "Untested"))
        self.assertNotIn("Copilot CLI trusts this folder, so", out)

    def test_the_copilot_home_can_be_moved(self):
        moved = self.tmp / "moved"
        moved.mkdir()
        (moved / "config.json").write_text(json.dumps({"trustedFolders": [os.fspath(self.ios)]}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"COPILOT_HOME": os.fspath(moved)}):
            self.assertIn("Copilot CLI trusts this folder", self.group(self.doctor(self.ios)[1], "Passed"))


class ToolchainTests(DoctorCase):
    def test_a_missing_ios_toolchain_is_a_failure_not_a_pass(self):
        del self.programs["xcodebuild"]
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        self.assertIn("iOS toolchain: `xcodebuild` was not found", self.group(out, "Failed"))
        self.programs["xcodebuild"] = (False, "xcode-select: error: tool 'xcodebuild' requires Xcode")
        self.assertIn("iOS toolchain: `xcodebuild -version` did not work: xcode-select: error", self.group(self.doctor(self.ios)[1], "Failed"))

    def test_an_android_repo_needs_the_wrapper_java_and_the_sdk(self):
        del self.programs["java"]
        failed = self.group(self.doctor(self.android)[1], "Failed")
        for expected in ("the repo has no Gradle wrapper `gradlew`", "no Java was found, so `./gradlew` cannot run in a terminal", "no Android SDK is named, in `local.properties` or in ANDROID_HOME"):
            with self.subTest(expected=expected):
                self.assertIn(expected, failed)
        self.assertNotIn("iOS toolchain", self.doctor(self.android)[1])

        self.programs["java"] = PROGRAMS["java"]
        (self.android / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        sdk = self.tmp / "sdk"
        sdk.mkdir()
        (self.android / "local.properties").write_text(f"# a comment\nsdk.dir={sdk}\n", encoding="utf-8")
        passed = self.group(self.doctor(self.android)[1], "Passed")
        for expected in ("the Gradle wrapper `gradlew` is in the repo", 'Java answers (openjdk version "17.0.9" 2023-10-17, from the PATH)', "the Android SDK named in `local.properties` is there"):
            with self.subTest(expected=expected):
                self.assertIn(expected, passed)

    def test_java_home_and_android_home_are_read_and_a_wrong_one_fails(self):
        jdk = self.tmp / "jdk"
        (jdk / "bin").mkdir(parents=True)
        (jdk / "bin/java").write_text("", encoding="utf-8")
        sdk = self.tmp / "sdk"
        sdk.mkdir()
        with mock.patch.dict(os.environ, {"JAVA_HOME": os.fspath(jdk), "ANDROID_HOME": os.fspath(sdk)}):
            passed = self.group(self.doctor(self.android)[1], "Passed")
            self.assertIn("from JAVA_HOME", passed)
            self.assertIn("the Android SDK named in ANDROID_HOME is there", passed)
        with mock.patch.dict(os.environ, {"JAVA_HOME": os.fspath(self.tmp / "none"), "ANDROID_HOME": os.fspath(self.tmp / "none")}):
            failed = self.group(self.doctor(self.android)[1], "Failed")
            self.assertIn("there is no `bin/java` in it", failed)
            self.assertIn("ANDROID_HOME names an Android SDK folder that does not exist", failed)


class RepoTests(DoctorCase):
    def test_a_tug_that_is_gone_behind_the_hooks_fails_and_says_what_it_costs(self):
        (self.bin / "tug").unlink()
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        failed = self.group(out, "Failed")
        self.assertIn(f"The hooks call `{self.bin / 'tug'}`, which is not there. Until this is repaired, every write and every shell command of an agent is denied.", failed)
        self.assertIn("Install Tugboat again, then run `tug sync` in this repo.", failed)

    def test_a_hook_somebody_removed_fails(self):
        file = self.ios / ".claude/settings.local.json"
        settings = json.loads(file.read_text(encoding="utf-8"))
        del settings["hooks"]["PreToolUse"]
        settings["permissions"]["ask"] = settings["permissions"]["ask"][1:]
        file.write_text(json.dumps(settings), encoding="utf-8")
        failed = self.group(self.doctor(self.ios)[1], "Failed")
        self.assertIn("lacks Tugboat's hook for `pre-tool-use`, so that step does not run in any agent tool.", failed)
        self.assertIn("1 protected path rule for Claude Code is missing from `.claude/settings.local.json`.", failed)

    def test_a_generated_file_that_is_gone_or_changed_fails(self):
        rules = self.ios / ".claude/rules/tugboat.md"
        rules.chmod(0o644)
        rules.write_text("changed\n", encoding="utf-8")
        (self.ios / ".claude/agents/reviewer.md").unlink()
        failed = self.group(self.doctor(self.ios)[1], "Failed")
        self.assertIn("2 agent tool files under `.claude/` are missing or changed: .claude/agents/reviewer.md, .claude/rules/tugboat.md.", failed)

    def test_a_copy_that_is_behind_what_the_machine_fetched_fails(self):
        self.push("README.md", "newer\n")
        self.assertTrue(sync.sync_team_repo(parse_team_repo_address(TEAM)).ok)
        failed = self.group(self.doctor(self.ios)[1], "Failed")
        self.assertRegex(failed, r"The copy in `\.tugboat/team/` holds revision [0-9a-f]{7}, and this machine has fetched [0-9a-f]{7}\.\n  To repair: Run `tug sync` in this repo\.")

    def test_a_last_sync_that_did_not_reach_the_team_repo_fails_and_says_when_one_worked(self):
        self.remote.rename(self.tmp / "away.git")
        self.tug(self.ios, "sync")
        failed = self.group(self.doctor(self.ios)[1], "Failed")
        self.assertIn("The last sync did not reach the team repo: ", failed)
        self.assertIn("The last sync that worked was just now.", failed)

    def test_a_missing_clone_and_a_clone_on_another_branch_fail(self):
        clone = self.tmp / "home/team-repos/github.com/acme/team"
        self.git(clone, "switch", "--quiet", "-c", "mine")
        self.assertIn("The clone of the team repo is on the branch 'mine', so sync leaves it alone and it gets no update.", self.group(self.doctor(self.ios)[1], "Failed"))
        clone.rename(self.tmp / "clone-away")
        self.assertIn("This machine has no clone of the team repo acme/team.", self.group(self.doctor(self.ios)[1], "Failed"))

    def test_a_local_edit_in_the_clone_is_advisory(self):
        (self.tmp / "home/team-repos/github.com/acme/team/README.md").write_text("edited\n", encoding="utf-8")
        out = self.doctor(self.ios)[1]
        self.assertIn("The clone of the team repo has 1 local edit.", self.group(out, "Advisory"))

    def test_a_generated_file_that_git_sees_fails(self):
        apprepo.set_excluded_patterns(self.ios, [])
        failed = self.group(self.doctor(self.ios)[1], "Failed")
        self.assertIn("that Tugboat generated", failed)
        self.assertIn(".tugboat/", failed)

    def test_an_open_project_step_is_a_failure_and_so_is_a_repo_outside_the_profile(self):
        failed = self.group(self.doctor(self.ios)[1], "Failed")
        self.assertIn("Setup is not finished: where the project's stories keep their acceptance criteria is not chosen yet.", failed)
        self.assertIn("Setup is not finished: the team has not adopted its AI use policy yet.", failed)
        profile_file = self.work / "profile.json"
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        data = json.loads(profile_file.read_text(encoding="utf-8"))
        data["app_repos"] = [repo for repo in data["app_repos"] if repo["github"] != "acme/shop-android"]
        self.push("profile.json", json.dumps(data, indent=2) + "\n")
        self.tug(self.android, "sync")
        out = self.doctor(self.android)[1]
        self.assertIn("This repo is not in the project profile", self.group(out, "Failed"))
        self.assertIn("The platform toolchain was not checked: this repo is not in the project profile", self.group(out, "Untested"))


class RegisterTests(DoctorCase):
    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_an_unlisted_mcp_server_is_reported_and_a_listed_one_passes_whatever_its_name(self):
        self.write(
            self.userhome / ".claude.json",
            {
                "mcpServers": {
                    "anything": {"type": "http", "url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": f"Bearer {SECRET}"}},
                    "github": {"type": "stdio", "command": "/opt/bin/npx", "args": ["-y", "figma-developer-mcp@1.2.3", f"--figma-api-key={SECRET}"], "env": {"KEY": SECRET}},
                },
                "projects": {os.fspath(self.ios): {"mcpServers": {"local-db": {"command": "db-mcp"}}}, "/another/repo": {"mcpServers": {"other": {"command": "x"}}}},
            },
        )
        self.write(self.ios / ".mcp.json", {"mcpServers": {"jira": {"type": "sse", "url": f"https://mcp.atlassian.com/v1/sse?token={SECRET}"}}})
        self.write(self.userhome / ".copilot/mcp-config.json", {"mcpServers": {"shell": {"type": "local", "command": "bash-mcp", "args": []}}})
        self.write(self.userhome / "Library/Application Support/Code/User/mcp.json", {"servers": {"figma": {"type": "http", "url": "http://127.0.0.1:3845/mcp"}}, "inputs": []})
        self.write(self.ios / ".vscode/mcp.json", {"servers": {"gh": {"url": "https://API.githubcopilot.com/mcp"}}})
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        failed = self.group(out, "Failed")
        for expected in (
            "The MCP server 'github' (Claude Code, your user settings; program npx) is not on the team's list of approved MCP servers.",
            "The MCP server 'local-db' (Claude Code, your settings for this repo; program db-mcp) is not on the team's list",
            "The MCP server 'shell' (Copilot CLI, your user settings; program bash-mcp) is not on the team's list",
            "The MCP server 'figma' (VS Code, your user settings; host 127.0.0.1) is not on the team's list",
            "A server is known by its host or its program, not by its name.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, failed)
        passed = self.group(out, "Passed")
        self.assertIn("3 configured MCP servers are on the team's list: 'anything' (Claude Code, your user settings), 'jira' (Claude Code, `.mcp.json` in this repo), 'gh' (VS Code, `.vscode/mcp.json` in this repo).", passed)
        for absent in (SECRET, "Bearer", "figma-api-key", "'other'", "token="):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, out)

    def test_a_package_the_team_listed_is_matched_by_a_word_of_the_command(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        register = json.loads((self.work / "permissions/ai-tools.json").read_text(encoding="utf-8"))
        register["mcp_servers"].append({"id": "figma", "name": "Figma", "commands": ["figma-developer-mcp"]})
        self.push("permissions/ai-tools.json", json.dumps(register, indent=2) + "\n")
        self.tug(self.ios, "sync")
        self.write(self.userhome / ".claude.json", {"mcpServers": {"design": {"command": "npx", "args": ["-y", "figma-developer-mcp@1.2.3"]}}})
        out = self.doctor(self.ios)[1]
        self.assertIn("1 configured MCP server is on the team's list: 'design' (Claude Code, your user settings).", self.group(out, "Passed"))
        self.assertNotIn("is not on the team's list", out)

    def test_with_no_list_of_servers_nothing_is_compared_and_that_is_untested(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        register = json.loads((self.work / "permissions/ai-tools.json").read_text(encoding="utf-8"))
        register["mcp_servers"] = []
        self.push("permissions/ai-tools.json", json.dumps(register, indent=2) + "\n")
        self.tug(self.ios, "sync")
        self.write(self.userhome / ".claude.json", {"mcpServers": {"design": {"command": "npx"}}})
        out = self.doctor(self.ios)[1]
        self.assertIn("MCP servers were not compared with a list: the team has not listed its approved MCP servers yet.", self.group(out, "Untested"))
        self.assertNotIn("'design'", out)

    def test_a_settings_file_that_cannot_be_read_is_untested_and_a_tool_the_project_does_not_use_is_not_read(self):
        (self.userhome / ".claude.json").write_text("{ broken", encoding="utf-8")
        out = self.doctor(self.ios)[1]
        self.assertIn("The MCP servers of Claude Code, your user settings were not compared: the settings file could not be read.", self.group(out, "Untested"))
        self.write(self.userhome / ".claude.json", {"mcpServers": {"design": {"command": "npx"}}})
        servers, unread = doctor.configured_servers(self.ios, ["copilot-cli"])
        self.assertEqual((servers, unread), ([], []))

    def test_an_agent_definition_outside_the_registry_is_listed_and_does_not_fail_the_check(self):
        self.finish_setup()
        self.trust(self.ios)
        (self.ios / ".claude/agents/mine.md").write_text("---\nname: mine\n---\n", encoding="utf-8")
        (self.ios / ".github/agents").mkdir(parents=True)
        (self.ios / ".github/agents/helper.agent.md").write_text("helper\n", encoding="utf-8")
        (self.ios / ".github/agents/README.md").write_text("not an agent\n", encoding="utf-8")
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 0, out)
        self.assertIn(
            "2 agent definitions in this repo are not in the team's agent registry, so the team repo did not review them: .claude/agents/mine.md, .github/agents/helper.agent.md.",
            self.group(out, "Advisory"),
        )
        self.assertIn("1 agent definition from the team's agent registry is installed in this repo.", self.group(out, "Passed"))


class SessionTests(DoctorCase):
    """What an agent tool's own session shows, beside what is configured on disk."""

    COMMAND = "tug doctor observe --surface {surface} --tracker {tracker} --github {github}"

    def payload(self, root, session="s1", claude=True, **more):
        base = {"cwd": os.fspath(root), "session_id": session, **more}
        return json.dumps({**base, "transcript_path": "/tmp/t.jsonl"} if claude else {**base, "timestamp": "2026-11-14T23:04:39.157Z"})

    def in_session(self, root, surface, *, session="s1", start=True, claude=True, tracker="ok", github="ok"):
        """An agent tool that runs the hooks: the session starts, and then the agent runs the check with its shell tool."""
        command = self.COMMAND.format(surface=surface, tracker=tracker, github=github)
        if start:
            self.assertIn("Tugboat sync report", hook.session_start(self.payload(root, session, claude)))
        self.assertEqual(hook.pre_tool_use(self.payload(root, session, claude, tool_name="Bash", tool_input={"command": command})), "")
        return self.tug(root, *command.split()[1:])

    def session_part(self, root):
        out = self.doctor(root)[1]
        return out[out.index("== Observed in a session") :]

    def test_an_agent_tool_that_runs_the_hooks_is_seen_working(self):
        code, out, err = self.in_session(self.ios, "claude-code")
        self.assertEqual((code, err), (0, ""), out)
        for expected in (
            "Tugboat setup check, inside a session of Claude Code",
            "- Hooks: the check before each action ran for this command, so this agent tool loads Tugboat's hooks.",
            "- The session-start hook ran in this session too.",
            "- Tracker connection: answered, as you reported.",
            "- GitHub connection: answered, as you reported.",
            "This is recorded in `.tugboat/observed.json`.",
            "Do not repair anything yourself.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)
        part = self.session_part(self.ios)
        self.assertIn("- Claude Code: the hooks ran in its session (checked just now, with tug ", part)
        self.assertIn("- Claude Code: the tracker connection answered, as its agent reported.", part)
        self.assertIn("- Claude Code: the GitHub connection answered, as its agent reported.", part)
        self.assertIn("- GitHub Copilot CLI has not been checked inside a session in this repo", part)
        self.assertNotIn("Failed", part)
        self.assertEqual(self.git(self.ios, "status", "--porcelain"), "")

    def test_a_tool_whose_hook_does_not_fire_shows_configured_on_disk_and_not_observed(self):
        self.trust(self.ios)
        code, out, err = self.tug(self.ios, "doctor", "observe", "--surface", "copilot-cli")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("- Hooks: NOT RUNNING. No hook ran before this command.", out)
        self.assertIn("The usual cause is that Copilot CLI does not trust this folder.", out)
        self.assertIn("- Tracker connection: was not tried, as you reported.", out)
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        disk, session = out.split("== Observed in a session")
        self.assertIn("Both hooks are in `.claude/settings.local.json`", disk)
        self.assertIn("Copilot CLI trusts this folder, so it loads the hooks.", disk)
        self.assertIn("- GitHub Copilot CLI: no hook ran in its session (checked just now). So there nothing syncs at the start and no action is checked.", session)
        self.assertIn("  To repair: Start `copilot` in this folder and choose to trust it and remember it. Then run the skill again in a new session.", session)

    def test_each_tool_gets_the_cause_and_the_repair_of_its_own(self):
        for surface, cause, repair in (
            ("claude-code", "`disableAllHooks`", "Start `claude` in this repo's folder"),
            ("copilot-vscode", "VS Code does not trust this workspace", "Workspaces: Manage Workspace Trust"),
        ):
            with self.subTest(surface=surface):
                out = self.tug(self.ios, "doctor", "observe", "--surface", surface)[1]
                self.assertIn(cause, out)
                self.assertIn(repair, out)

    def test_the_hook_says_which_tool_it_is_whatever_the_agent_names(self):
        with mock.patch.dict(os.environ, {"AI_AGENT": "github_copilot_vscode_agent"}):
            code, out, _ = self.in_session(self.ios, "copilot-cli", claude=False)
        self.assertEqual(code, 0, out)
        self.assertIn("Tugboat setup check, inside a session of GitHub Copilot in VS Code", out)
        self.assertIn("You named GitHub Copilot CLI. The hook that ran belongs to GitHub Copilot in VS Code, so the result is recorded for that tool.", out)
        part = self.session_part(self.ios)
        self.assertIn("- GitHub Copilot in VS Code: the hooks ran in its session", part)
        self.assertIn("- GitHub Copilot CLI has not been checked inside a session", part)

    def test_copilot_cli_is_the_copilot_that_does_not_name_itself(self):
        code, out, _ = self.in_session(self.android, "copilot-cli", claude=False)
        self.assertEqual(code, 0, out)
        self.assertIn("inside a session of GitHub Copilot CLI", out)
        self.assertEqual(observed.surface_of({"transcript_path": "/t"}, {"AI_AGENT": "github_copilot_vscode_agent"}), "claude-code")
        self.assertEqual(observed.surface_of({}, {"AI_AGENT": "claude-code_2-1-325_harness"}), "copilot-cli")
        self.assertEqual(observed.surface_of({}, {}), "copilot-cli")

    def test_a_mark_from_another_moment_or_another_command_does_not_count(self):
        observed.record_hook(self.ios, "claude-code", "probe", "s1", datetime.now(timezone.utc) - timedelta(minutes=10))
        self.assertEqual(self.tug(self.ios, "doctor", "observe", "--surface", "claude-code")[0], 1)
        (self.ios / ".tugboat/observed.json").unlink()
        self.assertEqual(hook.pre_tool_use(self.payload(self.ios, tool_name="Bash", tool_input={"command": "ls"})), "")
        self.assertEqual(hook.pre_tool_use(self.payload(self.ios, tool_name="Bash", tool_input={"command": "tug doctor"})), "")
        self.assertFalse((self.ios / ".tugboat/observed.json").exists())
        self.assertIn("NOT RUNNING", self.tug(self.ios, "doctor", "observe", "--surface", "claude-code")[1])

    def test_a_session_that_started_elsewhere_is_advisory_not_a_failure(self):
        hook.session_start(self.payload(self.ios, "earlier"))
        code, out, _ = self.in_session(self.ios, "claude-code", session="later", start=False)
        self.assertEqual(code, 0, out)
        self.assertIn("- The session-start hook was not seen in this session.", out)
        part = self.session_part(self.ios)
        self.assertIn("Advisory: 1\n- Claude Code: the check before each action ran, and the session-start hook was not seen in the same session.", part)

    def test_a_connection_that_did_not_answer_fails_and_one_that_was_not_tried_is_untested(self):
        code, out, _ = self.in_session(self.ios, "claude-code", tracker="failed", github="none")
        self.assertEqual(code, 1)
        self.assertIn("- Tracker connection: did not answer, as you reported.", out)
        code, out = self.doctor(self.ios)
        self.assertEqual(code, 1)
        part = out[out.index("== Observed in a session") :]
        self.assertIn("- Claude Code: the tracker connection did not answer, as its agent reported.\n  To repair: Connect the tracker in that tool, or give its sign-in the permission it lacks, then run the skill again.", part)
        self.assertIn("- Claude Code: the GitHub connection was not tried, as its agent reported.", part)

    def test_a_check_from_long_ago_no_longer_counts(self):
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="seconds").replace("+00:00", "Z")
        observed.record_check(self.ios, observed.Check("claude-code", old, "0.20.0", True, True, "ok", "ok"))
        part = self.session_part(self.ios)
        self.assertIn("- Claude Code was last checked inside a session 45 days ago, which is too long ago to count.", part)
        self.assertNotIn("the hooks ran", part)

    def test_only_the_projects_agent_tools_are_listed_and_outside_a_set_up_repo_the_check_refuses(self):
        self.assertEqual([check.text[:11] for check in doctor.check_sessions(self.ios, ["claude-code"])], ["Claude Code"])
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "// p\n"}, "acme/other")
        code, _, err = self.tug(other, "doctor", "observe", "--surface", "claude-code")
        self.assertEqual(code, 1)
        self.assertIn("this repo is not set up with Tugboat", err)
        code, _, err = self.tug(self.ios, "doctor", "observe")
        self.assertEqual(code, 1)
        self.assertIn("needs --surface", err)

    def test_a_broken_record_reads_as_nothing_seen_and_a_mark_that_cannot_be_written_stops_nothing(self):
        (self.ios / ".tugboat/observed.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(observed.read(self.ios), {})
        self.assertIsNone(observed.last_check(self.ios, "claude-code"))
        observed.record_hook(self.tmp / "no-such-repo", "claude-code", "probe", "s1")
        self.assertIn("has not been checked inside a session", self.session_part(self.ios))

    def test_the_skill_ships_with_the_tool_and_names_the_command_and_the_three_tools(self):
        text = (self.ios / ".claude/skills/tug-setup-check/SKILL.md").read_text(encoding="utf-8")
        fields, body = frontmatter.parse(text)
        self.assertEqual(formats.validate_skill_header(fields)["name"], doctor.SKILL)
        self.assertIn("tug doctor observe --surface <name> --tracker <result> --github <result>", body)
        for name in observed.SURFACES:
            self.assertIn(f"`{name}`", body)
        for answer in observed.ANSWERS:
            self.assertIn(f"`{answer}`", body)
        self.assertIn("Do not change a setting, trust a folder, or install anything yourself.", body)
        self.assertIn("The results can still differ: a sign-in can be allowed to read issues and not pull requests.", body)


class CheckCommandTests(unittest.TestCase):
    def test_the_check_command_is_recognised_however_tug_is_called(self):
        for command in (
            "tug doctor observe --surface claude-code",
            "/Users/pat/.local/bin/tug doctor observe --surface copilot-cli --tracker ok",
            "cd /repo && tug doctor observe --surface copilot-vscode",
        ):
            with self.subTest(command=command):
                self.assertTrue(hook.is_check_command(command))
        for command in ("tug doctor", "echo tug doctor observe > note", "tug sync", "doctor observe", "tug doctor 'observe", "mytug doctor observe"):
            with self.subTest(command=command):
                self.assertEqual(hook.is_check_command(command), command == "echo tug doctor observe > note")


class ProgramTests(unittest.TestCase):
    def test_a_program_that_is_missing_or_fails_never_raises_and_stderr_is_read(self):
        self.assertEqual(doctor.program_output([os.fspath(Path("/no/such/program"))])[0], False)
        worked, line = doctor.program_output(["/bin/sh", "-c", "echo 'version 1.2.3.' >&2"])
        self.assertEqual((worked, line), (True, "version 1.2.3"))
        self.assertEqual(doctor.program_output(["/bin/sh", "-c", "exit 3"]), (False, "no output"))
        self.assertEqual(doctor.program_output(["/bin/sh", "-c", "sleep 5"], timeout=0.2), (False, "gave no answer within 0.2 seconds"))

    def test_versions_are_read_from_the_usual_shapes(self):
        self.assertEqual(doctor._numbers("2.1.325 (Claude Code)"), (2, 1, 325))
        self.assertEqual(doctor._numbers("Xcode 26.3"), (26, 3, 0))
        self.assertIsNone(doctor._numbers("no numbers"))


if __name__ == "__main__":
    unittest.main()
