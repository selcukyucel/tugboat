import io
import json
import os
import unittest
from unittest import mock

from tugboat import hook, permissions, pretool

from test_knowledge import AWS_KEY, GITHUB_TOKEN
from test_permissions import protected
from test_rules import RuleCase
from test_sync import run

PROTECTED = permissions.Compiled(
    "ios",
    (
        permissions.Protected("fastlane/**", "Release automation.", "team"),
        permissions.Protected("**/*.p12", "Signing certificates.", "team"),
        permissions.Protected(".github/workflows/**", "CI workflows.", "team"),
        permissions.Protected(".tugboat/team/**", "The copy.", "tugboat"),
        permissions.Protected(".claude/rules/tugboat.md", "Generated.", "tugboat"),
    ),
)


class ClassifyTests(unittest.TestCase):
    def test_which_tools_are_checked(self):
        for tool, kind in (
            ("Write", "write"), ("Edit", "write"), ("MultiEdit", "write"), ("NotebookEdit", "write"), ("apply_patch", "write"),
            ("mcp__files__write_file", "write"), ("str_replace_editor", "write"), ("create", "write"),
            ("Bash", "shell"), ("shell", "shell"), ("run_in_terminal", "shell"),
            ("Read", None), ("Grep", None), ("Glob", None), ("WebFetch", None), ("Task", None), ("view", None), (None, None), (7, None),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(pretool.kind_of(tool), kind)

    def test_claude_code_is_told_apart_from_copilot_by_its_transcript_path(self):
        self.assertEqual(pretool.surface_of({"transcript_path": "/x/y.jsonl"}), "claude-code")
        self.assertEqual(pretool.surface_of({"timestamp": "2026-11-14T16:58:12.020Z"}), "copilot")

    def test_what_each_write_tool_is_about_to_write(self):
        self.assertEqual(pretool.write_parts({"file_path": "/r/a.swift", "content": "let a = 1\n"}), (["/r/a.swift"], ["let a = 1\n"]))
        self.assertEqual(pretool.write_parts({"file_path": "a", "old_string": "x", "new_string": "y"}), (["a"], ["y"]))  # only the new text
        self.assertEqual(pretool.write_parts({"file_path": "a", "edits": [{"old_string": "x", "new_string": "y"}, {"new_string": "z"}]}), (["a"], ["y", "z"]))
        self.assertEqual(pretool.write_parts({"notebook_path": "n.ipynb", "new_source": "print(1)"}), (["n.ipynb"], ["print(1)"]))
        # Copilot in VS Code, as recorded from a real session: `path` and `file_text`.
        self.assertEqual(pretool.write_parts({"path": "/r/Sources/Ok2.swift", "file_text": "let b = 2\n"}), (["/r/Sources/Ok2.swift"], ["let b = 2\n"]))
        self.assertEqual(pretool.write_parts({"path": "a", "old_str": "x", "new_str": "y"}), (["a"], ["y"]))
        self.assertEqual(pretool.write_parts({"path": "a", "insert_line": 3, "insert_text": "z"}), (["a"], ["z"]))
        # A tool with names nobody has seen yet: every text counts, apart from the path, the old text, and a description.
        self.assertEqual(pretool.write_parts({"path": "a", "oldText": "x", "freshBody": "y", "description": "why", "count": 2}), (["a"], ["y"]))
        self.assertEqual(pretool.write_parts({"path": "a", "changes": [{"old_text": "x", "replacement": "y"}, {"replacement": "z"}]}), (["a"], ["y", "z"]))
        patch = "*** Begin Patch\n*** Add File: fastlane/Fastfile\n+lane :beta\n*** Update File: a.txt\n-old\n+new\n*** Delete File: b.p12\n*** End Patch\n"
        self.assertEqual(pretool.write_parts(patch), (["fastlane/Fastfile", "a.txt", "b.p12"], ["lane :beta\nnew"]))
        self.assertEqual(pretool.write_parts(None), ([], []))
        self.assertEqual(pretool.write_parts(["odd"]), ([], []))


class ShellTests(unittest.TestCase):
    def writes(self, command):
        return pretool.shell_writes(command)

    def test_the_writes_a_shell_command_makes(self):
        for command, expected in (
            ("echo hi > out.txt", [("out.txt", False)]),
            ("echo hi >> fastlane/Fastfile", [("fastlane/Fastfile", False)]),
            ("make 2> err.log", [("err.log", False)]),
            ("make > /dev/null 2>&1", []),
            ("cat a | tee b c", [("b", False), ("c", False)]),
            ("rm -rf fastlane", [("fastlane", True)]),
            ("sudo rm -f dist.p12 && echo done", [("dist.p12", True)]),
            ("FOO=1 touch a b", [("a", False), ("b", False)]),
            ("cp dist.p12 /tmp/copy.p12", [("/tmp/copy.p12", False)]),  # the source is only read
            ("mv a.p12 b.p12", [("a.p12", True), ("b.p12", True)]),
            ("sed -i '' 's/a/b/' fastlane/Fastfile", [("s/a/b/", False), ("fastlane/Fastfile", False)]),  # the script is taken for a path, which harms nothing
            ("sed -n '1p' fastlane/Fastfile", []),
            ("perl -pi -e 's/a/b/' x.yml", [("x.yml", False)]),
            ("dd if=/dev/zero of=key.p12 bs=1", [("key.p12", False)]),
            ("git rm fastlane/Fastfile", [("fastlane/Fastfile", True)]),
            ("git checkout -- .github/workflows/ci.yml", [(".github/workflows/ci.yml", False)]),
            ("git -C sub restore a.txt", [("a.txt", False)]),
            ("cat fastlane/Fastfile; ls .github/workflows; grep -r key .", []),
            ("bundle exec fastlane beta", []),  # running the tool is not a change to its files
            ("echo 'rm -rf fastlane'", []),
            ("chmod +x scripts/run.sh\nrm old.txt", [("+x", False), ("scripts/run.sh", False), ("old.txt", True)]),
            ("echo \"it's open", []),  # an open quote does not crash the check
        ):
            with self.subTest(command=command):
                self.assertEqual(self.writes(command), expected)

    def git(self, command, branch="feature/login", shared=("main",)):
        return pretool.git_problem(command, "/repo", shared, lambda folder: branch)

    def test_force_pushes_and_destructive_resets_on_a_shared_branch_are_named(self):
        for command, branch, expected in (
            ("git push --force origin main", "feature/x", "force-pushes to the shared branch 'main'"),
            ("git push -f", "main", "force-pushes to the shared branch 'main'"),
            ("git push --force-with-lease origin HEAD", "develop", "force-pushes to the shared branch 'develop'"),
            ("git push origin +feature/x:main", "feature/x", "force-pushes to the shared branch 'main'"),
            ("git push -uf origin release/2.1", "feature/x", "force-pushes to the shared branch 'release/2.1'"),
            ("git push origin :main", "feature/x", "deletes the shared branch 'main' on the remote"),
            ("git push --delete origin master", "feature/x", "deletes the shared branch 'master' on the remote"),
            ("git push --mirror --force", "feature/x", "force-pushes every branch"),
            ("cd app && git push --force origin refs/heads/main", "feature/x", "force-pushes to the shared branch 'main'"),
            ("git -C ../other -c user.name=x push --force origin main", "feature/x", "force-pushes to the shared branch 'main'"),
            ("git reset --hard HEAD~3", "main", "hard reset on the shared branch 'main'"),
            ("git reset --hard", "trunk", "hard reset on the shared branch 'trunk'"),
            ("git branch -D main", "feature/x", "deletes the shared branch 'main' by force"),
            ("git branch -f main HEAD~2", "feature/x", "moves the shared branch 'main' to another commit by force"),
            ("git checkout -B main origin/other", "feature/x", "resets the shared branch 'main' to another commit"),
            ("git switch -C develop", "feature/x", "resets the shared branch 'develop' to another commit"),
        ):
            with self.subTest(command=command):
                problem = pretool.git_problem(command, "/repo", ("main", "trunk"), lambda folder, branch=branch: branch)
                self.assertIsNotNone(problem, command)
                self.assertIn(expected, problem)

    def test_ordinary_git_work_passes(self):
        for command in (
            "git push",
            "git push origin feature/login",
            "git push --force origin feature/login",  # a developer's own branch, after a rebase
            "git push --force-with-lease",
            "git push -u origin HEAD",
            "git push --follow-tags origin main",
            "git reset --hard origin/feature/login",
            "git reset --soft HEAD~1",
            "git branch -D feature/old",
            "git branch -d main",  # a plain delete refuses an unmerged branch by itself
            "git checkout main",
            "git checkout -b feature/new main",
            "git commit -m 'push --force origin main'",
            "git log --oneline main",
            "echo git push --force origin main",
            "gitk --all",
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.git(command))
        self.assertIsNone(pretool.git_problem("git reset --hard", "/repo", ("main",), lambda folder: None))  # a detached HEAD is no shared branch


class DecideTests(unittest.TestCase):
    REPO = "/work/shop-ios"

    def decide(self, tool, tool_input, *, claude=True, branch="feature/login", cwd=None):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": cwd or self.REPO}
        if claude:
            payload["transcript_path"] = "/home/.claude/x.jsonl"
        from pathlib import Path

        return pretool.decide(payload, Path(self.REPO), PROTECTED, lambda folder: branch, ("main",))

    def test_a_write_that_holds_a_secret_is_denied_and_the_secret_is_not_repeated(self):
        for tool, tool_input in (
            ("Write", {"file_path": f"{self.REPO}/Sources/Config.swift", "content": f'let token = "{GITHUB_TOKEN}"\n'}),
            ("Edit", {"file_path": f"{self.REPO}/a.swift", "old_string": "x", "new_string": f"key {AWS_KEY}"}),
            ("Edit", f"*** Begin Patch\n*** Add File: a.env\n+TOKEN={GITHUB_TOKEN}\n*** End Patch\n"),
            ("Write", {"path": f"{self.REPO}/Sources/Config.swift", "file_text": f'let token = "{GITHUB_TOKEN}"\n'}),  # the shape VS Code sends
            ("Bash", {"command": f"echo {GITHUB_TOKEN} > token.txt"}),
            ("Bash", {"command": f'curl -H "Authorization: token {GITHUB_TOKEN}" https://api.github.com'}),
        ):
            for claude in (True, False):
                with self.subTest(tool=tool, claude=claude):
                    decision = self.decide(tool, tool_input, claude=claude)
                    self.assertEqual(decision.action, "deny")
                    self.assertIn("holds what looks like a secret", decision.reason)
                    self.assertNotIn(GITHUB_TOKEN, decision.reason)
                    self.assertNotIn(AWS_KEY, decision.reason)
        # The text that is replaced is not the text that is written.
        self.assertEqual(self.decide("Edit", {"file_path": f"{self.REPO}/a.swift", "old_string": GITHUB_TOKEN, "new_string": "readFromKeychain()"}).action, "allow")

    def test_a_protected_path_is_asked_about_on_claude_code_and_denied_on_copilot(self):
        for tool, tool_input in (
            ("Write", {"file_path": f"{self.REPO}/fastlane/Fastfile", "content": "lane :beta\n"}),
            ("Edit", {"file_path": f"{self.REPO}/Signing/dist.p12", "old_string": "a", "new_string": "b"}),
            ("Write", {"file_path": "../.github/workflows/ci.yml", "content": "x"}),
            ("Write", {"path": f"{self.REPO}/fastlane/Fastfile", "file_text": "lane :beta\n"}),  # the shape VS Code sends
            ("Edit", "*** Begin Patch\n*** Update File: fastlane/Fastfile\n+x\n*** End Patch\n"),
            ("Edit", "*** Begin Patch\n*** Delete File: Signing/dist.p12\n*** End Patch\n"),
            ("Bash", {"command": "echo x >> fastlane/Fastfile"}),
            ("Bash", {"command": "rm -rf fastlane"}),
            ("Bash", {"command": "rm Signing/*.p12"}),
            ("Bash", {"command": "sed -i '' 's/a/b/' .claude/rules/tugboat.md"}),
            ("Bash", {"command": "rm -rf .tugboat"}),
            ("Bash", {"command": "git rm -r .github"}),
        ):
            cwd = f"{self.REPO}/Sources" if isinstance(tool_input, dict) and str(tool_input.get("file_path", "")).startswith("..") else None
            with self.subTest(tool_input=str(tool_input)[:50]):
                asked = self.decide(tool, tool_input, claude=True, cwd=cwd)
                self.assertEqual(asked.action, "ask", asked.reason)
                self.assertIn("is a protected path", asked.reason)
                self.assertIn("The developer decides", asked.reason)
                denied = self.decide(tool, tool_input, claude=False, cwd=cwd)
                self.assertEqual(denied.action, "deny")
                self.assertIn("An agent may not change it here.", denied.reason)
        reason = self.decide("Write", {"file_path": f"{self.REPO}/fastlane/Fastfile", "content": "x"}).reason
        self.assertIn("`fastlane/Fastfile` is a protected path (pattern `fastlane/**`, from the team repo's list). Release automation.", reason)

    def test_ordinary_work_is_allowed(self):
        for tool, tool_input in (
            ("Write", {"file_path": f"{self.REPO}/Sources/App.swift", "content": "import SwiftUI\n"}),
            ("Write", {"file_path": f"{self.REPO}/.tugboat/capture/knowledge/auth/x.md", "content": "a note"}),  # the skills' drafts
            ("Write", {"file_path": "/tmp/elsewhere/dist.p12", "content": "x"}),  # outside the repo: not this repo's list
            ("Edit", {"file_path": f"{self.REPO}/README.md", "old_string": "a", "new_string": "The password: field is checked in LoginViewModel."}),
            ("Bash", {"command": "xcodebuild test -scheme Shop"}),
            ("Bash", {"command": "cat fastlane/Fastfile && ls Signing"}),
            ("Bash", {"command": "bundle exec fastlane beta"}),
            ("Bash", {"command": "cp Signing/dist.p12 /tmp/backup.p12"}),
            ("Bash", {"command": "git push --force origin feature/login"}),
            ("Bash", {"command": "tug sync"}),
            ("Bash", {"command": ""}),
            ("Bash", None),
            ("Read", {"file_path": f"{self.REPO}/Signing/dist.p12"}),  # reading is not a change
            ("Grep", {"pattern": GITHUB_TOKEN}),
        ):
            with self.subTest(tool_input=str(tool_input)[:50]):
                self.assertEqual(self.decide(tool, tool_input).action, "allow")
                self.assertEqual(self.decide(tool, tool_input, claude=False).action, "allow")

    def test_a_forbidden_git_command_is_denied_on_both(self):
        for claude in (True, False):
            decision = self.decide("Bash", {"command": "git push --force origin main"}, claude=claude)
            self.assertEqual(decision.action, "deny")
            self.assertIn("this git command is not allowed, because it force-pushes to the shared branch 'main'", decision.reason)
        self.assertEqual(self.decide("Bash", {"command": "git reset --hard HEAD~1"}, branch="main").action, "deny")
        self.assertEqual(self.decide("Bash", {"command": "git reset --hard HEAD~1"}, branch="feature/x").action, "allow")


class HookCommandTests(RuleCase):
    """`tug hook pre-tool-use`, as the agent tools call it."""

    def setUp(self):
        super().setUp()
        self.instructions(self.ios)

    def call(self, payload, root=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        here = os.getcwd()
        os.chdir(root or self.ios)
        try:
            with mock.patch("sys.stdin") as stdin:
                stdin.buffer = io.BytesIO(raw)
                code, out, err = run("hook", "pre-tool-use")
        finally:
            os.chdir(here)
        return code, (json.loads(out) if out.strip() else None), err

    def write(self, path, content="x\n", claude=True, root=None):
        root = root or self.ios
        payload = {"hook_event_name": "PreToolUse", "cwd": os.fspath(root), "tool_name": "Write", "tool_input": {"file_path": os.fspath(root / path), "content": content}}
        if claude:
            payload["transcript_path"] = "/x.jsonl"
        return payload

    def test_an_ordinary_write_prints_nothing_and_exits_zero(self):
        self.assertEqual(self.call(self.write("Sources/App.swift")), (0, None, ""))
        self.assertEqual(self.call({"tool_name": "Read", "tool_input": {"file_path": "x"}, "cwd": os.fspath(self.ios)}), (0, None, ""))

    def test_a_seeded_secret_is_denied_in_the_shape_both_tools_read(self):
        code, output, err = self.call(self.write("Sources/Config.swift", f'let token = "{GITHUB_TOKEN}"\n'))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(output["permissionDecision"], "deny")  # the top-level field, for Copilot
        self.assertEqual(output["permissionDecisionReason"], output["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("GitHub token on line 1", output["permissionDecisionReason"])
        self.assertNotIn(GITHUB_TOKEN, json.dumps(output))

    def test_a_signing_file_asks_on_claude_code_and_is_denied_on_copilot(self):
        code, output, _ = self.call(self.write("Signing/dist.p12"))
        self.assertEqual(code, 0)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertNotIn("permissionDecision", output)  # an "ask" is for Claude Code only
        self.assertIn("pattern `**/*.p12`", output["hookSpecificOutput"]["permissionDecisionReason"])
        code, output, _ = self.call(self.write("Signing/dist.p12", claude=False))
        self.assertEqual((code, output["permissionDecision"]), (0, "deny"))

    def test_a_protected_path_added_in_the_team_repo_is_checked_after_the_next_sync(self):
        self.assertIsNone(self.call(self.write("Config/Secrets.xcconfig"))[1])
        self.adopt(protected(("Config/**", "ios", "Build settings hold keys and endpoints.")))
        self.instructions(self.ios)
        output = self.call(self.write("Config/Secrets.xcconfig", claude=False))[1]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("Build settings hold keys and endpoints.", output["permissionDecisionReason"])

    def test_tugboats_own_files_are_guarded_and_the_draft_folder_is_free(self):
        for path in (".tugboat/team/policies/ios/IOS-001.md", ".claude/rules/tugboat.md", ".claude/settings.local.json", ".tugboat/permissions.json", ".tugboat/records/jira/SHOP-123/20261115T101530Z-3f9a1c2b7d4e.json"):
            with self.subTest(path=path):
                self.assertEqual(self.call(self.write(path, claude=False))[1]["permissionDecision"], "deny")
        self.assertIsNone(self.call(self.write(".tugboat/capture/knowledge/auth/token-refresh.md"))[1])

    def test_a_force_push_to_the_default_branch_is_denied_with_the_real_repo(self):
        payload = {"cwd": os.fspath(self.ios), "tool_name": "Bash", "tool_input": {"command": "git push --force"}, "transcript_path": "/x.jsonl"}
        self.assertIsNone(self.call(payload)[1])  # the test repo is on the branch "work"
        self.git(self.ios, "checkout", "--quiet", "-b", "main")
        output = self.call(payload)[1]
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("force-pushes to the shared branch 'main'", output["permissionDecisionReason"])
        self.assertEqual(permissions.read_compiled(self.ios).shared_branches, ("main",))

    def test_a_check_that_cannot_run_denies_and_says_why(self):
        for raw, expected in ((b"not json", "DataError"), (b"", "DataError"), (b"[1, 2]", "not a JSON object"), (b"\xff\xfe", "UnicodeDecodeError")):
            with self.subTest(raw=raw):
                code, output, _ = self.call(raw)
                self.assertEqual((code, output["permissionDecision"], output["hookSpecificOutput"]["permissionDecision"]), (0, "deny", "deny"))
                self.assertIn("Tugboat could not check this action, so it was not allowed", output["permissionDecisionReason"])
                self.assertIn(expected, output["permissionDecisionReason"])
        with mock.patch("tugboat.pretool.decide", side_effect=RuntimeError("boom")):
            output = self.call(self.write("Sources/App.swift"))[1]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("RuntimeError: boom", output["permissionDecisionReason"])

    def test_when_even_the_answer_cannot_be_printed_the_exit_code_denies(self):
        with mock.patch("tugboat.hook.decision_output", side_effect=RuntimeError("no output")):
            code, output, err = self.call(b"not json")
        self.assertEqual((code, output), (2, None))
        self.assertIn("Tugboat could not check this action, so it was not allowed", err)

    def test_a_broken_list_denies_every_write_until_tug_sync_rebuilds_it(self):
        (self.ios / ".tugboat/permissions.json").write_text("{broken", encoding="utf-8")
        output = self.call(self.write("Sources/App.swift"))[1]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("is missing or cannot be read. Run `tug sync`", output["permissionDecisionReason"])
        shell = lambda command: {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.fspath(self.ios)}  # noqa: E731
        self.assertEqual(self.call(shell("ls"))[1]["permissionDecision"], "deny")
        self.assertEqual(self.call(shell("tug sync && rm -rf fastlane"))[1]["permissionDecision"], "deny")
        self.assertEqual(self.call(shell(" tug sync "))[:2], (0, None))  # the one command that repairs it
        self.assertEqual(self.call({"tool_name": "Read", "cwd": os.fspath(self.ios)})[:2], (0, None))  # a read is not held up
        self.instructions(self.ios)  # the next sync rebuilds the list
        self.assertEqual(self.call(self.write("Sources/App.swift")), (0, None, ""))

    def test_outside_a_repo_that_tugboat_set_up_nothing_is_checked(self):
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "x\n"}, "acme/other")
        self.assertEqual(self.call(self.write("Signing/dist.p12", root=other), root=other), (0, None, ""))
        payload = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x", "content": "x"}, "cwd": os.fspath(self.tmp)}
        self.assertEqual(self.call(payload, root=self.tmp), (0, None, ""))

    def test_the_generated_hook_blocks_on_failure_and_has_a_short_time_limit(self):
        settings = json.loads((self.ios / ".claude/settings.local.json").read_text(encoding="utf-8"))
        entry = settings["hooks"]["PreToolUse"][0]
        self.assertNotIn("matcher", entry)
        self.assertEqual(entry["hooks"], [{"type": "command", "command": "/opt/tug hook pre-tool-use", "timeout": 10, "onFailure": "block"}])
        self.assertNotIn("onFailure", settings["hooks"]["SessionStart"][0]["hooks"][0])  # a failed sync must not block a session

    def test_the_decision_output_shapes(self):
        ask = json.loads(hook.decision_output("ask", "why"))
        self.assertEqual(ask, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "why"}})
        deny = json.loads(hook.decision_output("deny", "why"))
        self.assertEqual((deny["permissionDecision"], deny["permissionDecisionReason"], deny["hookSpecificOutput"]["permissionDecision"]), ("deny", "why", "deny"))


if __name__ == "__main__":
    unittest.main()
