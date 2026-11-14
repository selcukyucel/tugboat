import json
import unittest

from tugboat import ciworkflows, team_repo

from test_init import FIRST, TEAM, InitCase

CALLER = ".github/workflows/tugboat-secret-scan.yml"
OPTIONS = ("--yes", "--team-repo", TEAM, "--tracker", "github-projects", "--approval", "owner")


class ReusableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = team_repo.starter_layout()[".github/workflows/secret-scan.yml"]

    def test_the_starter_ships_a_scan_that_other_repos_can_call(self):
        self.assertIn("workflow_call:", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("secrets:", self.text)  # it needs no secret of the project

    def test_the_scanner_is_pinned_and_its_download_is_checked(self):
        self.assertRegex(self.text, r'GITLEAKS_VERSION: "\d+\.\d+\.\d+"')
        self.assertRegex(self.text, r'GITLEAKS_SHA256: "[0-9a-f]{64}"')
        self.assertIn('echo "${GITLEAKS_SHA256}  gitleaks.tar.gz" | sha256sum --check --strict', self.text)
        self.assertLess(self.text.index("sha256sum --check"), self.text.index("tar --extract"))  # checked before it is unpacked

    def test_it_scans_the_commits_of_the_pull_request_and_keeps_a_finding_out_of_the_log(self):
        self.assertIn('--log-opts="${BASE}..${HEAD}"', self.text)
        self.assertIn("--redact", self.text)
        self.assertIn("fetch-depth: 0", self.text)
        # The two commit IDs reach the shell through the environment, not by being pasted into the script.
        script = self.text[self.text.index("run: |", self.text.index("Scan the commits")) :]
        self.assertNotIn("${{", script)


class CallerWorkflowTests(InitCase):
    def caller(self, root):
        return root / CALLER

    def status(self, root):
        return self.git(root, "status", "--porcelain")

    def test_with_no_ci_feature_the_app_repo_has_no_workflow_file_from_the_tool(self):
        out = self.ok(self.ios, "--yes", "--team-repo", TEAM, *FIRST, "--ci", "none")
        self.assertFalse((self.ios / ".github").exists())
        self.assertEqual(self.status(self.ios), "")
        self.assertNotIn("CI secret scan", out)

    def test_selecting_the_scan_writes_the_caller_workflow_and_commits_nothing(self):
        out = self.ok(self.ios, *OPTIONS, "--ci", "secret-scan")
        text = self.caller(self.ios).read_text(encoding="utf-8")
        self.assertIn("uses: acme/team/.github/workflows/secret-scan.yml@main", text)
        self.assertIn("pull_request:", text)
        self.assertIn("contents: read", text)
        self.assertEqual(self.status(self.ios), "?? .github/")  # Git sees it, and nothing is committed
        self.assertEqual(self.git(self.ios, "log", "--oneline").count("\n"), 0)
        for expected in (
            f"CI secret scan: wrote {CALLER}.",
            "Git sees this file, on purpose",
            "Tugboat does not commit it.",
            "If acme/team is a private repo, a person must let this repo call its workflows",
        ):
            self.assertIn(expected, out)
        self.assertNotIn("has no .github/workflows/secret-scan.yml yet", out)  # the starter team repo has the scan
        self.assertEqual(self.proposed()["ci_features"], ["secret-scan"])

    def test_a_second_run_changes_nothing_and_says_whether_it_is_committed(self):
        self.ok(self.ios, *OPTIONS, "--ci", "secret-scan")
        before = self.caller(self.ios).stat().st_mtime_ns
        out = self.ok(self.ios, "--yes")
        self.assertEqual(self.caller(self.ios).stat().st_mtime_ns, before)
        self.assertIn(f"CI secret scan: {CALLER} is written and not committed yet.", out)
        self.git(self.ios, "add", CALLER)
        self.git(self.ios, "commit", "--quiet", "-m", "Add the secret scan")
        out = self.ok(self.ios, "--yes")
        self.assertIn(f"CI secret scan: {CALLER} is in place.", out)
        self.assertEqual(self.status(self.ios), "")

    def test_deselecting_the_scan_removes_what_init_added_and_nothing_else(self):
        self.ok(self.ios, *OPTIONS, "--ci", "secret-scan")
        other = self.ios / ".github/workflows/build.yml"
        other.write_text("name: build\n", encoding="utf-8")
        self.git(self.ios, "add", "-A")
        self.git(self.ios, "commit", "--quiet", "-m", "workflows")
        self.merge()
        out = self.ok(self.ios, "--yes", "--ci", "none")
        self.assertFalse(self.caller(self.ios).exists())
        self.assertTrue(other.is_file())
        self.assertIn(f"so {CALLER} was removed. Git shows the removal. Commit it through this repo's own review.", out)
        self.assertEqual(self.status(self.ios), f"D {CALLER}")  # deleted in the working tree, and not committed
        self.assertEqual(self.proposed()["ci_features"], [])

    def test_a_caller_that_was_never_committed_is_removed_with_its_empty_folders(self):
        self.ok(self.ios, *OPTIONS, "--ci", "secret-scan")
        self.merge()
        out = self.ok(self.ios, "--yes", "--ci", "none")
        self.assertFalse((self.ios / ".github").exists())
        self.assertEqual(self.status(self.ios), "")
        self.assertNotIn("Git shows the removal", out)

    def test_a_caller_somebody_changed_is_left_alone(self):
        self.ok(self.ios, *OPTIONS, "--ci", "secret-scan")
        self.caller(self.ios).write_text(self.caller(self.ios).read_text(encoding="utf-8") + "# pinned by the CI owner\n", encoding="utf-8")
        out = self.ok(self.ios, "--yes")
        self.assertIn(f"{CALLER} is there and is not what Tugboat writes, so it was left alone.", out)
        self.merge()
        out = self.ok(self.ios, "--yes", "--ci", "none")
        self.assertIn("It is not what Tugboat writes, so it was left alone.", out)
        self.assertIn("# pinned by the CI owner", self.caller(self.ios).read_text(encoding="utf-8"))

    def test_the_other_ci_features_are_saved_as_a_choice_only(self):
        out = self.ok(self.ios, *OPTIONS, "--ci", "guardrail-pack,dependency-verification")
        self.assertFalse((self.ios / ".github").exists())
        self.assertIn("Note: the CI checks (Guardrail pack, Dependency verification) are saved as a choice. Their workflows are not built yet.", out)
        out = self.ok(self.android_repo(), "--yes", "--team-repo", TEAM, "--ci", "secret-scan,guardrail-pack")
        self.assertIn("Note: the CI check (Guardrail pack) is saved as a choice. Its workflow is not built yet.", out)

    def android_repo(self):
        from test_init import ANDROID_FILES

        return self.app("android", ANDROID_FILES, "acme/shop-android")

    def test_a_second_repo_of_the_project_is_told_at_sync_and_gets_the_file_at_its_init(self):
        self.ok(self.ios, *OPTIONS, "--ci", "none")
        self.merge()
        android = self.android_repo()
        self.ok(android, "--yes", "--team-repo", TEAM)
        self.merge()
        self.ok(self.ios, "--yes", "--ci", "secret-scan")  # the project turns the scan on, from the iOS repo
        self.merge()
        code, out, _ = self.tug(android, "sync")
        self.assertEqual(code, 0)
        self.assertIn(f"Setup is not finished: the project uses the CI secret scan, and this repo has no {CALLER}. `tug init`, run in this repo in a terminal, writes it.", out)
        self.assertFalse((android / ".github").exists())  # sync changes no file Git sees
        out = self.ok(android, "--yes")
        self.assertIn(f"CI secret scan: wrote {CALLER}.", out)
        code, out, _ = self.tug(android, "sync")
        self.assertIn(f"Setup is not finished: {CALLER} is written and not committed. The CI secret scan runs only once it is committed.", out)
        self.git(android, "add", CALLER)
        self.git(android, "commit", "--quiet", "-m", "Add the secret scan")
        code, out, _ = self.tug(android, "sync")
        self.assertNotIn("CI secret scan", out)
        # The project turns it off again: the repo that still has the file is told.
        self.ok(self.ios, "--yes", "--ci", "none")
        self.merge()
        code, out, _ = self.tug(android, "sync")
        self.assertIn(f"Setup is not finished: the project does not use the CI secret scan any more, and this repo still has {CALLER}.", out)

    def test_a_team_repo_without_the_scan_is_named_at_init_and_by_the_team_repo_check(self):
        self.git(self.work, "rm", "--quiet", ".github/workflows/secret-scan.yml")
        self.git(self.work, "commit", "--quiet", "-m", "an older team repo")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        out = self.ok(self.ios, *OPTIONS, "--ci", "secret-scan")
        self.assertIn("The team repo acme/team has no .github/workflows/secret-scan.yml yet, so the check would fail.", out)
        self.merge()
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 0)
        self.assertIn("warning: .github/workflows/secret-scan.yml: is not there, and the project profile selects the CI secret scan. The check in each app repo would fail.", out)

    def test_the_caller_names_the_team_repo_it_was_set_up_with(self):
        from tugboat.address import parse_team_repo_address

        text = ciworkflows.caller_text(parse_team_repo_address("git@github.com:Some-Org/Team.Repo.git"))
        self.assertIn("uses: Some-Org/Team.Repo/.github/workflows/secret-scan.yml@main", text)
        self.assertEqual(json.dumps(text).count("${{"), 0)  # nothing is filled in at run time


if __name__ == "__main__":
    unittest.main()
