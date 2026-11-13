import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tugboat import detect
from tugboat.address import parse_remote_url
from tugboat.errors import TugError

from test_sync import GIT, run

IOS_FILES = {"Shop.xcodeproj/project.pbxproj": "// project\n", "Shop/App.swift": "// app\n"}
ANDROID_FILES = {
    "settings.gradle.kts": 'include(":app")\n',
    "app/build.gradle.kts": 'plugins { id("com.android.application") }\n',
    "app/src/main/AndroidManifest.xml": "<manifest/>\n",
}


class DetectCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "XDG_CONFIG_HOME": os.fspath(self.tmp / "xdg")})
        env.start()
        self.addCleanup(env.stop)

    def git(self, cwd, *args):
        done = subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def repo(self, name, files, *, remote="git@github.com:acme/shop.git", head="main", untracked=()):
        """An app repo with tracked files, a remote, and a recorded default branch."""
        root = self.tmp / name
        root.mkdir()
        self.git(root, "init", "--quiet", "-b", "work")
        for path, text in {"README.md": "readme\n", **files}.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, encoding="utf-8")
        self.git(root, "add", "-A")
        self.git(root, "commit", "--quiet", "-m", "first")
        for path in untracked:
            (root / path).mkdir(parents=True)
        if remote:
            self.git(root, "remote", "add", "origin", remote)
        if remote and head:
            self.git(root, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{head}")
        return root

    def stop(self, root, message, **options):
        with self.assertRaisesRegex(TugError, message):
            detect.detect(root, **options)


class DetectionTests(DetectCase):
    def test_an_ios_repo(self):
        root = self.repo("ios", IOS_FILES, remote="git@github.com:acme/shop-ios.git")
        found = detect.detect(root / "Shop")
        self.assertEqual((found.root, found.remote, found.host, found.github), (root, "origin", "github.com", "acme/shop-ios"))
        self.assertEqual((found.default_branch, found.platform, found.evidence), ("main", "ios", ("Shop.xcodeproj",)))
        text = detect.render(found)
        self.assertIn("GitHub address:  acme/shop-ios (from the remote 'origin')", text)
        self.assertIn("Platform:        iOS (found Shop.xcodeproj)", text)

    def test_an_android_repo(self):
        root = self.repo("android", ANDROID_FILES, remote="https://github.com/acme/shop-android.git", head="develop")
        found = detect.detect(root)
        self.assertEqual((found.github, found.default_branch, found.platform), ("acme/shop-android", "develop", "android"))
        self.assertEqual(found.evidence[0], "app/src/main/AndroidManifest.xml")
        self.assertIn("Platform:        Android", detect.render(found))

    def test_android_is_seen_from_the_plugin_alone_and_from_a_version_catalog(self):
        root = self.repo("plugin", {"settings.gradle": "", "app/build.gradle": "apply plugin: 'com.android.application'\n"})
        self.assertEqual(detect.detect(root).evidence, ("app/build.gradle (uses the Android Gradle plugin)",))
        root = self.repo("catalog", {"settings.gradle.kts": "", "build.gradle.kts": "plugins { alias(libs.plugins.app) }\n", "gradle/libs.versions.toml": 'app = { id = "com.android.application" }\n'})
        self.assertEqual(detect.detect(root).platform, "android")

    def test_a_workspace_inside_a_project_counts_once_and_vendored_projects_do_not_count(self):
        files = {**IOS_FILES, "Shop.xcodeproj/project.xcworkspace/contents.xcworkspacedata": "x\n", "Shop.xcworkspace/contents.xcworkspacedata": "x\n", "Pods/Pods.xcodeproj/project.pbxproj": "x\n"}
        self.assertEqual(detect.detect(self.repo("ws", files)).evidence, ("Shop.xcodeproj", "Shop.xcworkspace"))
        self.stop(self.repo("pods-only", {"Pods/Pods.xcodeproj/project.pbxproj": "x\n"}), "no iOS or Android project was found")

    def test_a_generated_xcode_project_that_git_does_not_track_is_found_on_disk(self):
        root = self.repo("generated", {"Project.swift": "// tuist\n"}, untracked=("Shop.xcodeproj",))
        found = detect.detect(root)
        self.assertEqual((found.platform, found.evidence), ("ios", ("Shop.xcodeproj (on disk, not tracked by Git)",)))

    def test_the_default_branch_is_asked_from_the_remote_when_the_clone_has_no_record(self):
        bare = self.tmp / "remote.git"
        self.git(self.tmp, "init", "--quiet", "--bare", "-b", "trunk", os.fspath(bare))
        root = self.repo("asks", IOS_FILES, head=None)
        self.git(root, "push", "--quiet", os.fspath(bare), "HEAD:trunk")
        # The remote keeps its GitHub address. Git is told to reach it at the local folder.
        self.git(root, "config", f"url.{bare}.insteadOf", "git@github.com:acme/shop.git")
        found = detect.detect(root)
        self.assertEqual((found.github, found.default_branch, found.branch_source), ("acme/shop", "trunk", "asked from GitHub just now"))

    def test_a_token_in_the_remote_is_never_shown(self):
        root = self.repo("token", IOS_FILES, remote="https://x-access-token:SECRET123@github.com/acme/shop.git")
        found = detect.detect(root)
        self.assertEqual(found.github, "acme/shop")
        self.assertNotIn("SECRET123", detect.render(found) + repr(found))
        self.assertIsNone(parse_remote_url("https://github.com/"))


class StopTests(DetectCase):
    def test_a_plain_folder(self):
        self.stop(self.tmp, "is not inside a Git repo")

    def test_a_team_repo(self):
        self.stop(self.repo("team", {"tugboat.json": "{}\n"}), "is a team repo, not an app repo")

    def test_no_remote_and_a_remote_that_is_not_github(self):
        self.stop(self.repo("no-remote", IOS_FILES, remote=None), "has no Git remote")
        self.stop(self.repo("gitlab", IOS_FILES, remote="git@gitlab.com:group/sub/shop.git", head=None), "is not a GitHub address that Tugboat can read")
        self.stop(self.repo("local", IOS_FILES, remote="/srv/git/shop.git", head=None), "is not a GitHub address that Tugboat can read")

    def test_several_remotes_and_none_called_origin(self):
        root = self.repo("remotes", IOS_FILES, remote=None)
        self.git(root, "remote", "add", "upstream", "git@github.com:acme/shop.git")
        self.git(root, "remote", "add", "fork", "git@github.com:pat/shop.git")
        self.stop(root, r"several remotes and none is called 'origin' \(fork, upstream\)")

    def test_no_app_project(self):
        self.stop(self.repo("docs", {"docs/guide.md": "x\n"}), "no iOS or Android project was found")

    def test_a_backend_that_uses_gradle_is_not_android(self):
        files = {"settings.gradle.kts": 'include(":api")\n', "api/build.gradle.kts": "plugins { kotlin(\"jvm\") }\n"}
        self.stop(self.repo("backend", files), "nothing in them says it is an Android project")

    def test_both_platforms_need_a_choice(self):
        root = self.repo("both", {**{f"ios/{k}": v for k, v in IOS_FILES.items()}, **{f"android/{k}": v for k, v in ANDROID_FILES.items()}})
        self.stop(root, "has an iOS project .* and an Android project .* --platform ios or --platform android")
        self.assertEqual(detect.detect(root, platform="android").platform, "android")
        self.assertEqual(detect.detect(root, platform="ios").evidence, ("ios/Shop.xcodeproj",))

    def test_a_platform_that_contradicts_the_files(self):
        self.stop(self.repo("ios-only", IOS_FILES), "--platform android was given, but this repo only has iOS project files", platform="android")

    def test_the_default_branch_cannot_be_found_offline(self):
        root = self.repo("offline", IOS_FILES, head=None)
        self.git(root, "config", f"url.{self.tmp / 'nowhere.git'}.insteadOf", "git@github.com:acme/shop.git")
        self.stop(root, "is not recorded in the clone, and GitHub could not be asked .*git remote set-head origin --auto")


class InitCommandTests(DetectCase):
    def init(self, root, *argv, tty=False, answer=""):
        here = os.getcwd()
        os.chdir(root)
        try:
            with mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", return_value=answer):
                stdin.isatty.return_value = tty
                return run("init", *argv)
        finally:
            os.chdir(here)

    def unchanged(self, root):
        return self.git(root, "status", "--porcelain") == "" and not (root / ".tugboat").exists() and not (root / ".claude").exists()

    def test_yes_shows_what_was_found_and_then_needs_the_team_repo(self):
        root = self.repo("ios", IOS_FILES)
        code, out, err = self.init(root, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Tugboat found this:", out)
        self.assertIn("Default branch:  main", out)
        self.assertIn("init needs the address of the team repo. Pass --team-repo ADDRESS. Nothing was changed in this repo.", err)
        self.assertTrue(self.unchanged(root))

    def test_a_person_who_declines_what_was_found_stops_init(self):
        root = self.repo("ios", IOS_FILES)
        code, _, err = self.init(root, tty=True, answer="")
        self.assertEqual((code, err), (1, "tug: stopped. Nothing was changed in this repo.\n"))
        self.assertTrue(self.unchanged(root))

    def test_without_a_terminal_and_without_yes_it_stops(self):
        code, _, err = self.init(self.repo("ios", IOS_FILES))
        self.assertEqual(code, 1)
        self.assertIn("needs a person to confirm", err)

    def test_a_plain_folder_gives_a_clear_stop_and_changes_nothing(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, err = self.init(plain, "--yes")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("is not inside a Git repo", err)
        self.assertEqual(list(plain.iterdir()), [])

    def test_a_repo_with_no_app_project_gives_a_clear_stop_and_changes_nothing(self):
        root = self.repo("docs", {"docs/guide.md": "x\n"})
        code, _, err = self.init(root, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("no iOS or Android project was found in this repo", err)
        self.assertTrue(self.unchanged(root))


if __name__ == "__main__":
    unittest.main()
