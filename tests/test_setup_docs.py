"""The setup, removal, and missing-command instructions must match what the tool does."""

import unittest
from pathlib import Path

from tugboat import __version__, apprepo, hostfiles, releases, settingsmerge
from tugboat.address import parse_team_repo_address

README = Path(__file__).resolve().parents[1] / "README.md"
ADDRESS = parse_team_repo_address("git@github.com:acme/team.git")


class SetupDocsTests(unittest.TestCase):
    def setUp(self):
        self.readme = README.read_text(encoding="utf-8")
        self.pointer = hostfiles.pointer_text(ADDRESS)

    def section(self, heading):
        start = self.readme.index(f"\n## {heading}\n")
        end = self.readme.find("\n## ", start + 1)
        return self.readme[start : end if end != -1 else None]

    def test_the_readme_installs_the_release_it_ships_with(self):
        self.assertIn(f"uv tool install {releases.INSTALL_SOURCE}@v{__version__}\n", self.section("Set up"))

    def test_the_setup_steps_name_what_a_new_developer_needs(self):
        setup = self.section("Set up")
        for expected in ("brew install uv", "uv tool update-shell", "git ls-remote", "tug init", "is not trusted", "Tugboat sync report"):
            with self.subTest(expected=expected):
                self.assertIn(expected, setup)

    def test_removal_names_every_hook_and_the_real_marker_lines(self):
        start, end = apprepo._BLOCK_START.split(":")[0], apprepo._BLOCK_END
        for label, text in (("readme", self.section("Remove Tugboat")), ("instruction file", self.pointer)):
            with self.subTest(text=label):
                for spec in hostfiles.HOOKS:
                    self.assertIn(f"`tug hook {spec.arguments}`", text)
                self.assertIn(start, text)
                self.assertIn(end, text)
                self.assertIn(settingsmerge.SETTINGS_FILE, text)

    def test_removal_covers_the_ask_rules_the_machine_folder_and_the_command(self):
        removal = self.section("Remove Tugboat")
        for expected in (".tugboat/settings-rules.json", "rm -rf ~/.tugboat", "uv tool uninstall tugboat", ".tugboat/capture/", "tugboat-secret-scan.yml"):
            with self.subTest(expected=expected):
                self.assertIn(expected, removal)

    def test_a_missing_command_is_explained_in_the_readme_and_in_the_instruction_file(self):
        missing = self.section("When the `tug` command is missing")
        for expected in ('onFailure is "block"', "hook errored", "Install Tugboat again", "`tug sync`", "Remove Tugboat"):
            with self.subTest(expected=expected):
                self.assertIn(expected, missing)
        self.assertIn("## If the `tug` command is missing", self.pointer)
        self.assertIn(f"`uv tool install --force {releases.INSTALL_SOURCE}@v{__version__}`", self.pointer)
        self.assertIn("Then run `tug sync` in this repo.", self.pointer)


if __name__ == "__main__":
    unittest.main()
