import json
import os
import unittest
from unittest import mock

import tugboat
from tugboat import formats, hook, initrepo, paths, profile, prompts, team_repo
from tugboat.address import parse_team_repo_address
from tugboat.errors import TugError

from test_formats import PROFILE
from test_sync import SyncCase, run

TEAM = "git@github.com:acme/team.git"
IDENTITY = {"GIT_AUTHOR_NAME": "Pat", "GIT_AUTHOR_EMAIL": "pat@example.invalid", "GIT_COMMITTER_NAME": "Pat", "GIT_COMMITTER_EMAIL": "pat@example.invalid"}
IOS_FILES = {"Shop.xcodeproj/project.pbxproj": "// project\n", "Shop/App.swift": "// app\n"}
ANDROID_FILES = {"settings.gradle.kts": 'include(":app")\n', "app/src/main/AndroidManifest.xml": "<manifest/>\n"}
FIRST = ("--tracker", "github-projects", "--approval", "owner")


class InitCase(SyncCase):
    """The team repo has a GitHub address, and Git is told to reach it at a local folder."""

    def setUp(self):
        super().setUp()
        for path, text in team_repo.starter_layout().items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(text, encoding="utf-8")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", "starter")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.config = self.tmp / "gitconfig"
        self.redirect(TEAM, self.remote)
        self.address = parse_team_repo_address(TEAM)
        self.use_home("home")
        self.ios = self.app("ios", IOS_FILES, "acme/shop-ios")

    def use_home(self, name, **extra):
        """Stand at one developer's machine."""
        env = mock.patch.dict(os.environ, {"TUG_HOME": os.fspath(self.tmp / name), "GIT_CONFIG_GLOBAL": os.fspath(self.config), "TUG_GH": "", "TUG_COMMAND": "/opt/tug", **IDENTITY, **extra})
        env.start()
        self.addCleanup(env.stop)

    def redirect(self, address, folder):
        with self.config.open("a", encoding="utf-8") as file:
            file.write(f'[url "{folder}"]\n\tinsteadOf = {address}\n')

    def app(self, name, files, github, head="main"):
        root = self.tmp / name
        root.mkdir()
        self.git(root, "init", "--quiet", "-b", "work")
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, encoding="utf-8")
        self.git(root, "add", "-A")
        self.git(root, "commit", "--quiet", "-m", "first")
        self.git(root, "remote", "add", "origin", f"git@github.com:{github}.git")
        self.git(root, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{head}")
        return root

    def tug(self, root, *argv, answers=None):
        """Run `tug` in a folder. With answers, a person sits at a terminal and types them."""
        here = os.getcwd()
        os.chdir(root)
        try:
            with mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", side_effect=list(answers or [])) as asked:
                stdin.isatty.return_value = answers is not None
                code, out, err = run(*argv)
            self.questions = [call.args[0] for call in asked.call_args_list]
            return code, out, err
        finally:
            os.chdir(here)

    def init(self, root, *argv, answers=None):
        return self.tug(root, "init", *argv, answers=answers)

    def ok(self, root, *argv, answers=None):
        code, out, err = self.init(root, *argv, answers=answers)
        self.assertEqual((code, err), (0, ""), out)
        return out

    def branches(self):
        return sorted(self.git(self.remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())

    def proposals(self):
        return [branch for branch in self.branches() if branch != "main"]

    def proposed(self, branch=None):
        return json.loads(self.git(self.remote, "show", f"{branch or self.proposals()[-1]}:profile.json"))

    def merge(self, branch=None):
        """A person merges the pull request in GitHub."""
        self.git(self.work, "fetch", "--quiet", "origin", branch or self.proposals()[-1])
        self.git(self.work, "merge", "--quiet", "--ff-only", "FETCH_HEAD")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")

    def untouched(self, root):
        return self.git(root, "status", "--porcelain") == "" and not (root / ".tugboat").exists() and not (root / ".claude").exists()

    def set_up(self, root):
        return (root / ".tugboat/team/tugboat.json").is_file() and (root / ".claude/rules/tugboat.md").is_file() and self.git(root, "status", "--porcelain") == ""

    def pending(self):
        return profile.read_pending(self.address)


class FirstInitTests(InitCase):
    def test_a_person_answers_the_project_questions_and_the_profile_becomes_a_pull_request(self):
        main = self.git(self.remote, "rev-parse", "main")
        out = self.ok(self.ios, answers=["y", TEAM, "", "2", "", "", "2", "y"])
        self.assertEqual(len(self.questions), 8)
        self.assertIn("Team repo address", self.questions[1])
        self.assertIn("[team]", self.questions[2])
        (branch,) = self.proposals()
        self.assertRegex(branch, r"^tug/profile-\d{8}-\d{6}$")
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), main)
        self.assertEqual(self.git(self.remote, "diff", "--name-only", "main", branch), "profile.json")
        self.assertEqual(
            self.proposed(),
            {
                "schema": "tug.profile/1",
                "project": "team",
                "app_repos": [{"github": "acme/shop-ios", "default_branch": "main", "platform": "ios"}],
                "tracker": {"kind": "github-projects", "criteria_source": None},
                "ci_features": [],
                "surfaces": ["claude-code", "copilot-cli", "copilot-vscode"],
                "approval_mode": "owner",
                "minimum_tool_version": tugboat.__version__,
                "frameworks": [],
            },
        )
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%s", branch), "Add the project profile for team")
        self.assertIn("- App repos: acme/shop-ios (iOS, default branch main)", self.git(self.remote, "log", "-1", "--format=%b", branch))
        self.assertTrue(self.set_up(self.ios))
        self.assertFalse((self.ios / ".tugboat/team/profile.json").exists())
        self.assertEqual(self.pending().branch, branch)
        for text in ("Tracker: GitHub Projects", "Nothing was merged.", "This repo is set up for the project 'team'.", "works from its own copy of the profile", "Next: start a new agent session", "ask the agent to run the skill `tug-criteria-source`"):
            self.assertIn(text, out)

    def test_it_completes_for_jira_and_with_every_ci_feature_from_options_alone(self):
        out = self.ok(self.ios, "--yes", "--team-repo", TEAM, "--project", "shop", "--tracker", "jira", "--approval", "teammate", "--ci", "dependency-verification,secret-scan,guardrail-pack", "--surfaces", "copilot-cli")
        self.assertEqual(self.questions, [])
        found = self.proposed()
        self.assertEqual((found["project"], found["tracker"]["kind"], found["approval_mode"], found["surfaces"]), ("shop", "jira", "teammate", ["copilot-cli"]))
        self.assertEqual(found["ci_features"], ["secret-scan", "guardrail-pack", "dependency-verification"])
        self.assertIn("Their workflows are not built yet.", out)

    def test_each_ci_feature_alone_and_none_give_a_valid_profile(self):
        for features in ([], *([value] for value, _ in profile.CI_FEATURES)):
            for tracker, _ in profile.TRACKERS:
                made = profile.new_profile(project="shop", tracker=tracker, ci_features=features, surfaces=["claude-code"], approval_mode="owner")
                self.assertEqual((made["ci_features"], made["tracker"]["kind"]), (features, tracker))

    def test_with_yes_the_questions_that_have_no_default_must_be_given(self):
        code, _, err = self.init(self.ios, "--yes", "--team-repo", TEAM, "--tracker", "jira")
        self.assertEqual(code, 1)
        self.assertIn("pass --approval. Nothing was changed in this repo.", err)
        self.assertNotIn("--tracker", err)
        self.assertEqual(self.proposals(), [])
        self.assertTrue(self.untouched(self.ios))

    def test_declining_the_pull_request_stops_before_anything_is_written(self):
        code, _, err = self.init(self.ios, answers=["y", TEAM, "shop", "1", "none", "1", "1", "n"])
        self.assertEqual((code, err), (1, "tug: stopped. Nothing was changed in this repo.\n"))
        self.assertEqual(self.proposals(), [])
        self.assertIsNone(self.pending())
        self.assertTrue(self.untouched(self.ios))

    def test_a_wrong_answer_is_asked_again_and_three_wrong_answers_stop(self):
        self.ok(self.ios, answers=["y", "not an address", TEAM, "Shop App", "shop", "9", "jira", "1,7", "1", "0", "all", "1", "y"])
        found = self.proposed()
        self.assertEqual((found["project"], found["ci_features"], found["surfaces"]), ("shop", ["secret-scan"], ["claude-code", "copilot-cli", "copilot-vscode"]))
        code, _, err = self.init(self.app("other", IOS_FILES, "acme/other"), answers=["y", "x", "y", "z"])
        self.assertEqual(code, 1)
        self.assertIn("no usable answer to 'Team repo address", err)

    def test_a_list_option_names_its_choices_when_it_is_wrong(self):
        code, _, err = self.init(self.ios, "--yes", "--team-repo", TEAM, *FIRST, "--ci", "secret-scan,lint")
        self.assertEqual(code, 1)
        self.assertIn("--ci must be a list of these, separated by commas, or none: secret-scan, guardrail-pack, dependency-verification", err)
        self.assertEqual(self.init(self.ios, "--yes", "--team-repo", TEAM, *FIRST, "--surfaces", "none")[0], 1)
        self.assertTrue(self.untouched(self.ios))


class LaterInitTests(InitCase):
    def setUp(self):
        super().setUp()
        self.ok(self.ios, "--yes", "--team-repo", TEAM, *FIRST)
        (self.first,) = self.proposals()

    def test_a_second_run_with_nothing_new_changes_nothing(self):
        files = [self.ios / ".claude/rules/tugboat.md", self.ios / ".tugboat/state.json", paths.pending_profile_file(self.address)]
        before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in files]
        out = self.ok(self.ios, "--yes")
        self.assertEqual(self.proposals(), [self.first])
        self.assertEqual([(path.stat().st_ino, path.stat().st_mtime_ns) for path in files], before)
        self.assertIn("as this machine proposed it. It waits for its merge", out)
        self.assertIn("Copy in this repo: unchanged", out)
        self.assertIn("Agent tool files: unchanged", out)

    def test_after_the_merge_a_second_developer_is_asked_nothing_about_the_project(self):
        self.merge()
        self.use_home("home-of-sam")
        clone = self.app("ios-sam", IOS_FILES, "acme/shop-ios")
        out = self.ok(clone, answers=["y", TEAM])
        self.assertEqual(len(self.questions), 2)
        self.assertEqual(self.proposals(), [self.first])
        self.assertIsNone(self.pending())
        self.assertIn("Project profile, approved in the team repo:", out)
        self.assertIn("Tracker: GitHub Projects", out)
        self.assertNotIn("pull request", out)
        self.assertTrue(self.set_up(clone))
        self.assertEqual(json.loads((clone / ".tugboat/team/profile.json").read_text(encoding="utf-8"))["project"], "team")

    def test_after_the_merge_the_first_machine_drops_its_own_copy(self):
        self.merge()
        out = self.ok(self.ios, "--yes")
        self.assertIn("The profile change that this machine proposed was merged.", out)
        self.assertIsNone(self.pending())
        self.assertEqual(self.proposals(), [self.first])

    def test_a_second_repo_is_added_to_the_profile_by_its_own_pull_request(self):
        self.merge()
        android = self.app("android", ANDROID_FILES, "acme/shop-android", head="develop")
        out = self.ok(android, "--yes", "--team-repo", TEAM)
        self.assertEqual(self.questions, [])
        branch = self.proposals()[-1]
        self.assertNotEqual(branch, self.first)
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%s", branch), "Add acme/shop-android to the project profile")
        self.assertEqual(
            self.proposed(branch)["app_repos"],
            [{"github": "acme/shop-ios", "default_branch": "main", "platform": "ios"}, {"github": "acme/shop-android", "default_branch": "develop", "platform": "android"}],
        )
        self.assertIn("App repo added: acme/shop-android (Android, default branch develop)", out)
        self.assertTrue(self.set_up(android))

    def test_a_second_repo_on_the_same_machine_before_the_merge_uses_the_proposed_profile(self):
        android = self.app("android", ANDROID_FILES, "acme/shop-android")
        out = self.ok(android, answers=["y", "", "y"])
        self.assertIn(f"[{TEAM}]", self.questions[1])  # the one team repo this machine knows is offered
        self.assertEqual(len(self.proposals()), 2)
        self.assertEqual([repo["github"] for repo in self.proposed()["app_repos"]], ["acme/shop-ios", "acme/shop-android"])
        self.assertIn("It replaces this machine's earlier proposal. Close that one:", out)
        self.assertEqual(self.pending().branch, self.proposals()[-1])

    def test_a_choice_is_changed_by_an_option_and_goes_through_a_pull_request(self):
        self.merge()
        out = self.ok(self.ios, "--yes", "--tracker", "jira", "--ci", "secret-scan")
        branch = self.proposals()[-1]
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%s", branch), "Change the project profile")
        found = self.proposed(branch)
        self.assertEqual((found["tracker"]["kind"], found["ci_features"], found["approval_mode"]), ("jira", ["secret-scan"], "owner"))
        self.assertIn("Tracker: Jira  (was: GitHub Projects)", out)
        self.assertIn("CI features: Secret scan  (was: none)", out)

    def test_change_asks_again_and_enter_keeps_every_choice(self):
        self.merge()
        self.ok(self.ios, "--change", answers=["y", "", "", "", ""])
        self.assertEqual(len(self.proposals()), 1)
        out = self.ok(self.ios, "--change", answers=["y", "", "", "1,2", "1", "y"])
        found = self.proposed()
        self.assertEqual((found["surfaces"], found["approval_mode"]), (["claude-code", "copilot-cli"], "teammate"))
        self.assertIn("Approval: a teammate  (was: the owner)", out)

    def test_changing_back_to_the_approved_profile_withdraws_the_local_copy(self):
        self.merge()
        self.ok(self.ios, "--yes", "--tracker", "jira")
        out = self.ok(self.ios, "--yes", "--tracker", "github-projects")
        self.assertIn("The profile is the approved one again. Close the earlier proposal:", out)
        self.assertIsNone(self.pending())
        self.assertEqual(len(self.proposals()), 2)

    def test_the_project_name_cannot_be_changed_by_init(self):
        code, _, err = self.init(self.ios, "--yes", "--project", "other")
        self.assertEqual(code, 1)
        self.assertIn("the project is already named 'team'", err)

    def test_propose_again_opens_a_new_pull_request_for_the_same_profile(self):
        self.ok(self.ios, "--yes", "--propose-again")
        first, second = self.proposals()
        self.assertEqual(self.proposed(first), self.proposed(second))
        self.assertEqual(self.pending().branch, second)

    def test_when_main_gets_another_profile_the_local_proposal_is_set_aside(self):
        other = {**PROFILE, "project": "shop", "minimum_tool_version": "0.1.0"}
        self.push("profile.json", json.dumps(other))
        out = self.ok(self.ios, "--yes")
        self.assertIn("so that proposal was set aside. If its pull request is still open, close it:", out)
        self.assertIn("Project profile, approved in the team repo:", out)
        self.assertEqual(self.proposals(), [self.first])  # acme/shop-ios is in that profile already
        self.assertIsNone(self.pending())

    def test_a_tool_below_the_projects_minimum_proposes_nothing(self):
        self.push("profile.json", json.dumps({**PROFILE, "app_repos": [], "minimum_tool_version": "99.0.0"}))
        out = self.ok(self.ios, "--yes")
        self.assertIn(f"this project needs tug 99.0.0 or newer, and this is {tugboat.__version__}. The profile change was not proposed.", out)
        self.assertEqual(self.proposals(), [self.first])
        self.assertTrue(self.set_up(self.ios))

    def test_another_team_repo_for_a_repo_that_is_set_up_is_refused(self):
        code, _, err = self.init(self.ios, "--yes", "--team-repo", "git@github.com:acme/other-team.git")
        self.assertEqual(code, 1)
        self.assertIn(f"already set up for the team repo {TEAM}", err)


class StopTests(InitCase):
    def stop(self, *argv, message, root=None):
        root = root or self.ios
        code, _, err = self.init(root, "--yes", *argv)
        self.assertEqual(code, 1)
        self.assertRegex(err, message)
        self.assertTrue(self.untouched(root))
        self.assertEqual(self.proposals(), [])

    def test_no_address_and_nobody_to_ask(self):
        self.stop(message="init needs the address of the team repo. Pass --team-repo ADDRESS.")

    def test_a_team_repo_that_is_missing_or_unreachable(self):
        self.redirect("git@github.com:acme/nowhere.git", self.tmp / "nowhere.git")
        self.stop("--team-repo", "git@github.com:acme/nowhere.git", *FIRST, message="is missing or cannot be reached .* Tugboat creates no repository")
        self.assertFalse((self.tmp / "nowhere.git").exists())
        self.assertEqual([path.name for path in (self.tmp / "home").rglob("*nowhere*")], [])

    def test_a_repo_that_is_not_a_team_repo_is_refused_and_its_clone_is_not_kept(self):
        plain = self.tmp / "plain.git"
        self.git(self.tmp, "init", "--quiet", "--bare", "-b", "main", os.fspath(plain))
        self.git(self.ios, "push", "--quiet", os.fspath(plain), "HEAD:main")
        self.redirect("git@github.com:acme/plain.git", plain)
        self.stop("--team-repo", "git@github.com:acme/plain.git", *FIRST, message="is not a Tugboat team repo: its `main` has no tugboat.json")
        self.assertFalse(paths.team_repo_clone(parse_team_repo_address("git@github.com:acme/plain.git")).exists())
        self.assertEqual(initrepo.known_team_repos(), [])
        self.assertEqual([path.name for path in (self.tmp / "home").rglob("*plain*")], [])

    def test_the_app_repo_itself_and_a_repo_on_another_host(self):
        self.stop("--team-repo", "git@github.com:Acme/Shop-iOS.git", *FIRST, message="is this repo itself")
        self.stop("--team-repo", "git@github.example.com:acme/team.git", *FIRST, message="Both must be on the same GitHub")

    def test_a_profile_on_main_that_is_not_valid(self):
        self.push("profile.json", '{"schema": "tug.profile/1"}\n')
        self.stop("--team-repo", TEAM, message="the project profile in the team repo is not valid: profile is missing fields")

    def test_without_a_terminal_and_without_yes(self):
        code, _, err = self.init(self.ios)
        self.assertEqual(code, 1)
        self.assertIn("init needs a person to confirm what it found. Run it in a terminal, or pass --yes.", err)
        self.assertTrue(self.untouched(self.ios))


class ProposalFailureTests(InitCase):
    def test_when_the_pull_request_cannot_be_made_the_repo_is_still_set_up_and_the_next_init_proposes(self):
        nobody = {key: "" for key in IDENTITY}
        with mock.patch.dict(os.environ, nobody):
            for key in IDENTITY:
                os.environ.pop(key)
            code, out, err = self.init(self.ios, "--yes", "--team-repo", TEAM, *FIRST)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("The pull request could not be made: Git does not know your name and email", out)
        self.assertIn("The profile is not proposed yet.", out)
        self.assertEqual(self.proposals(), [])
        self.assertTrue(self.set_up(self.ios))
        self.assertIsNone(self.pending().branch)
        self.assertEqual(self.pending().profile["tracker"]["kind"], "github-projects")

        out = self.ok(self.ios, "--yes")
        self.assertEqual(len(self.proposals()), 1)
        self.assertEqual(self.pending().branch, self.proposals()[0])
        self.assertNotIn("replaces", out)

    def test_a_note_when_the_team_repo_belongs_to_someone_else(self):
        other = self.app("other", IOS_FILES, "pat/shop")
        out = self.ok(other, "--yes", "--team-repo", TEAM, *FIRST)
        self.assertIn("the team repo belongs to acme and this repo to pat", out)


class CriteriaSourceTests(InitCase):
    """The step of the first agent session: the agent passes the developer's pick to `tug`."""

    SECTION = ("profile", "set-criteria", "--kind", "section", "--name", "Acceptance criteria")

    def first_init(self, tracker="github-projects", merge=True):
        self.ok(self.ios, "--yes", "--team-repo", TEAM, "--tracker", tracker, "--approval", "owner")
        if merge:
            self.merge()

    def save(self, *argv, root=None, answers=None):
        code, out, err = self.tug(root or self.ios, *argv, answers=answers)
        self.assertEqual((code, err), (0, ""), out)
        return out

    def hook_text(self):
        return json.loads(hook.session_start(json.dumps({"cwd": os.fspath(self.ios)})))["additionalContext"]

    def test_the_pick_for_a_github_projects_project_becomes_a_pull_request_and_then_the_profile(self):
        self.first_init()
        unfinished = "Setup is not finished: where the project's stories keep their acceptance criteria is not chosen yet. The skill `tug-criteria-source`"
        self.assertIn(unfinished, self.save("sync"))
        self.assertIn(unfinished, self.hook_text())

        out = self.save(*self.SECTION, "--yes")
        branch = self.proposals()[-1]
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%s", branch), "Set where acceptance criteria are read from")
        self.assertEqual(self.git(self.remote, "diff", "--name-only", "main", branch), "profile.json")
        self.assertEqual(self.proposed(branch)["tracker"], {"kind": "github-projects", "criteria_source": {"kind": "section", "name": "Acceptance criteria"}})
        self.assertIn("Acceptance criteria will be read from section 'Acceptance criteria'.", out)
        self.assertIn("Nothing was merged.", out)
        # This machine works from its own copy at once. The approved copy in the repo waits for the merge.
        self.assertNotIn("Setup is not finished", self.save("sync"))
        self.assertIn("as this machine proposed it", self.save("profile", "show"))

        self.merge()
        text = self.hook_text()
        self.assertNotIn("Setup is not finished", text)
        self.assertIn("The profile change that this machine proposed was merged.", text)
        shown = self.save("profile", "show")
        self.assertIn("Project profile, approved in the team repo acme/team:", shown)
        self.assertIn("Acceptance criteria source: section 'Acceptance criteria'", shown)
        self.assertIsNone(self.pending())
        self.save("sync")
        self.assertEqual(json.loads((self.ios / ".tugboat/team/profile.json").read_text(encoding="utf-8"))["tracker"]["criteria_source"]["kind"], "section")

    def test_the_pick_for_a_jira_project_before_the_first_merge_replaces_the_waiting_proposal(self):
        self.first_init("jira", merge=False)
        out = self.save("profile", "set-criteria", "--kind", "field", "--name", "Acceptance Criteria (customfield_10042)", "--yes")
        first, second = self.proposals()
        self.assertIn("The new pull request replaces this machine's earlier proposal. Close that one:", out)
        found = self.proposed(second)
        self.assertEqual(found["tracker"], {"kind": "jira", "criteria_source": {"kind": "field", "name": "Acceptance Criteria (customfield_10042)"}})
        self.assertEqual([repo["github"] for repo in found["app_repos"]], ["acme/shop-ios"])
        self.assertIsNone(self.proposed(first)["tracker"]["criteria_source"])
        self.assertEqual(self.pending().branch, second)

    def test_the_same_pick_again_opens_nothing_and_a_task_list_is_a_third_kind(self):
        self.first_init()
        self.save(*self.SECTION, "--yes")
        out = self.save(*self.SECTION, "--yes")
        self.assertIn("Already saved: acceptance criteria are read from section 'Acceptance criteria'.", out)
        self.assertIn("The change waits for its merge:", out)
        self.assertEqual(len(self.proposals()), 2)
        self.save("profile", "set-criteria", "--kind", "task-list", "--name", "body", "--yes")
        self.assertEqual(self.proposed()["tracker"]["criteria_source"], {"kind": "task-list", "name": "body"})

    def test_a_person_at_a_terminal_is_asked_and_without_one_yes_is_needed(self):
        self.first_init()
        code, _, err = self.tug(self.ios, *self.SECTION)
        self.assertEqual(code, 1)
        self.assertIn("a pull request needs a person to confirm it. Run this in a terminal, or pass --yes. Nothing was saved.", err)
        code, _, err = self.tug(self.ios, *self.SECTION, answers=["n"])
        self.assertEqual((code, err), (1, "tug: stopped. Nothing was saved.\n"))
        self.assertEqual(len(self.proposals()), 1)
        self.assertIsNone(self.pending())
        self.save(*self.SECTION, answers=["y"])
        self.assertEqual(len(self.proposals()), 2)

    def test_a_name_or_a_kind_that_will_not_do_saves_nothing(self):
        self.first_init()
        for name, message in ((" ", "the name cannot be empty"), ("two\nlines", "the name must be one line"), ("x" * 201, "the name cannot exceed 200 characters")):
            code, _, err = self.tug(self.ios, "profile", "set-criteria", "--kind", "field", "--name", name, "--yes")
            self.assertEqual((code, err), (1, f"tug: {message}\n"))
        self.assertEqual(self.tug(self.ios, "profile", "set-criteria", "--kind", "comment", "--name", "x", "--yes")[0], 2)
        self.assertEqual(len(self.proposals()), 1)

    def test_offline_the_pick_is_kept_and_proposed_at_the_next_try(self):
        self.first_init()
        os.rename(self.remote, self.tmp / "unplugged.git")
        code, out, err = self.tug(self.ios, *self.SECTION, "--yes")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("The pull request could not be made: the team repo could not be reached", out)
        self.assertIn("The choice is kept on this machine. Run the same command again to propose it.", out)
        self.assertIsNone(self.pending().branch)
        self.assertIn("Acceptance criteria source: section 'Acceptance criteria'", self.save("profile", "show"))
        os.rename(self.tmp / "unplugged.git", self.remote)
        self.save(*self.SECTION, "--yes")
        self.assertEqual(self.pending().branch, self.proposals()[-1])

    def test_a_project_without_a_profile_and_a_folder_that_is_not_set_up(self):
        code, _, err = self.tug(self.ios, *self.SECTION, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("this folder is not set up with Tugboat. Pass --team-repo ADDRESS.", err)
        code, _, err = self.tug(self.ios, "profile", "show", "--team-repo", TEAM)
        self.assertEqual(code, 1)
        self.assertIn("has no copy of the team repo", err)
        code, _, err = self.tug(self.ios, *self.SECTION, "--yes", "--team-repo", TEAM)
        self.assertEqual(code, 1)
        self.assertIn("this project has no profile yet.", err)
        code, out, _ = self.tug(self.ios, "profile", "show", "--team-repo", TEAM)
        self.assertEqual(code, 1)
        self.assertIn("This project has no profile yet.", out)
        self.assertEqual(self.proposals(), [])

    def test_a_tool_below_the_minimum_saves_nothing(self):
        self.first_init()
        self.push("profile.json", json.dumps({**PROFILE, "tracker": {"kind": "jira", "criteria_source": None}, "minimum_tool_version": "99.0.0"}))
        code, _, err = self.tug(self.ios, *self.SECTION, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("this project needs tug 99.0.0 or newer", err)
        self.assertEqual(len(self.proposals()), 1)


class ProfileTests(unittest.TestCase):
    def test_the_tables_hold_exactly_the_values_the_format_accepts(self):
        for table, allowed in ((profile.TRACKERS, formats.TRACKERS), (profile.CI_FEATURES, formats.CI_FEATURES), (profile.SURFACES, formats.SURFACES), (profile.APPROVAL_MODES, formats.APPROVAL_MODES)):
            self.assertEqual({value for value, _ in table}, set(allowed))

    def test_a_new_tracker_forgets_the_criteria_source_of_the_old_one(self):
        checked = formats.validate_profile(PROFILE)
        self.assertEqual(profile.with_choices(checked, tracker="jira"), checked)
        self.assertEqual(profile.with_choices(checked, tracker="github-projects")["tracker"], {"kind": "github-projects", "criteria_source": None})

    def test_a_repo_is_added_once_and_updated_in_place(self):
        checked = formats.validate_profile(PROFILE)
        same = profile.with_repo(checked, github="acme/shop-ios", default_branch="main", platform="ios")
        self.assertEqual(same, checked)
        moved = profile.with_repo(checked, github="Acme/Shop-iOS", default_branch="trunk", platform="ios")
        self.assertEqual([repo["github"] for repo in moved["app_repos"]], ["Acme/Shop-iOS", "acme/shop-android"])
        self.assertEqual(profile.changes(checked, moved), ["App repo changed: Acme/Shop-iOS (iOS, default branch trunk)  (was: acme/shop-ios (iOS, default branch main))"])
        self.assertEqual(profile.changes(checked, checked), [])

    def test_the_project_name_comes_from_the_team_repo_name(self):
        for name, expected in (("Shop-Team-Repo", "shop"), ("shop_team", "shop"), ("team", "team"), ("42", "project"), ("acme.rules", "acme-rules")):
            self.assertEqual(initrepo.default_project_name(parse_team_repo_address(f"git@github.com:acme/{name}.git")), expected)


class PromptTests(unittest.TestCase):
    def ask(self, answers, function, *args, **options):
        with mock.patch("builtins.input", side_effect=answers), mock.patch("builtins.print"):
            return function(*args, **options)

    def test_one_takes_a_number_a_value_or_the_default(self):
        self.assertEqual(self.ask(["2"], prompts.one, "Q", profile.TRACKERS), "github-projects")
        self.assertEqual(self.ask(["jira"], prompts.one, "Q", profile.TRACKERS), "jira")
        self.assertEqual(self.ask([""], prompts.one, "Q", profile.TRACKERS, default="jira"), "jira")
        with self.assertRaisesRegex(TugError, "no usable answer to 'Q' after 3 tries"):
            self.ask(["", "3", "x"], prompts.one, "Q", profile.TRACKERS)

    def test_many_keeps_the_order_of_the_list(self):
        self.assertEqual(self.ask(["3, 1"], prompts.many, "Q", profile.CI_FEATURES, default=[]), ["secret-scan", "dependency-verification"])
        self.assertEqual(self.ask(["none"], prompts.many, "Q", profile.CI_FEATURES, default=["secret-scan"]), [])
        self.assertEqual(self.ask([""], prompts.many, "Q", profile.CI_FEATURES, default=["secret-scan"]), ["secret-scan"])
        self.assertEqual(self.ask(["none", "2"], prompts.many, "Q", profile.SURFACES, default=[], minimum=1), ["copilot-cli"])

    def test_an_input_that_ends_stops_the_command(self):
        with self.assertRaisesRegex(TugError, "the question got no answer"):
            self.ask(EOFError, prompts.confirm, "Q")


if __name__ == "__main__":
    unittest.main()
