import ast
import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import tugboat
from tugboat import cli
from tugboat.errors import EXIT_ERROR, EXIT_NOT_BUILT, EXIT_OK, DataError

SRC = Path(tugboat.__file__).resolve().parents[1]


def run(*argv):
    """Run the command in this process. Return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as stop:  # argparse exits by itself for --version and usage errors
            code = stop.code
    return code, out.getvalue(), err.getvalue()


class VersionCommandTests(unittest.TestCase):
    def test_version_prints_only_the_version(self):
        code, out, err = run("version")
        self.assertEqual((code, out, err), (EXIT_OK, tugboat.__version__ + "\n", ""))

    def test_version_flag_names_the_command(self):
        code, out, _ = run("--version")
        self.assertEqual((code, out), (0, f"tug {tugboat.__version__}\n"))

    def test_runs_as_a_module_in_a_new_process(self):
        env = {**os.environ, "PYTHONPATH": os.fspath(SRC)}
        done = subprocess.run(
            [sys.executable, "-m", "tugboat", "version"], capture_output=True, text=True, env=env, check=False
        )
        self.assertEqual((done.returncode, done.stdout.strip()), (0, tugboat.__version__))


class DispatchTests(unittest.TestCase):
    def test_no_command_prints_help_that_lists_every_command(self):
        code, out, _ = run()
        self.assertEqual(code, EXIT_OK)
        for command in cli.COMMANDS:
            self.assertIn(command.name, out)

    def test_an_unknown_command_is_a_usage_error_with_the_deny_code(self):
        code, _, err = run("launch")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_a_planned_command_says_it_is_not_built_and_does_nothing(self):
        planned = [command.name for command in cli.COMMANDS if command.handler is None]
        self.assertIn("doctor", planned)
        for name in planned:
            with self.subTest(name=name):
                code, out, err = run(name, "--anything", "extra")
                self.assertEqual((code, out), (EXIT_NOT_BUILT, ""))
                self.assertIn(f"'{name}' is not built yet", err)

    def test_command_names_are_unique(self):
        names = [command.name for command in cli.COMMANDS]
        self.assertEqual(len(names), len(set(names)))


class ErrorHandlingTests(unittest.TestCase):
    def patched(self, handler):
        commands = (cli.Command("version", "Print the installed version", handler),)
        return mock.patch.object(cli, "COMMANDS", commands)

    def test_a_known_error_is_one_line_with_no_trace(self):
        def fail(_args):
            raise DataError("profile is missing fields: tracker")

        with self.patched(fail):
            code, out, err = run("version")
        self.assertEqual((code, out, err), (EXIT_ERROR, "", "tug: profile is missing fields: tracker\n"))

    def test_an_unexpected_error_is_reported_without_a_trace(self):
        def crash(_args):
            raise RuntimeError("boom")

        with self.patched(crash), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TUG_DEBUG", None)
            code, _, err = run("version")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("tug: internal error: RuntimeError: boom", err)
        self.assertNotIn("Traceback", err)

    def test_debug_mode_shows_the_trace(self):
        def crash(_args):
            raise RuntimeError("boom")

        with self.patched(crash), mock.patch.dict(os.environ, {"TUG_DEBUG": "1"}):
            _, _, err = run("version")
        self.assertIn("Traceback", err)


class PackagingTests(unittest.TestCase):
    def test_the_package_imports_nothing_outside_the_standard_library(self):
        allowed = set(sys.stdlib_module_names) | {"tugboat"}
        files = sorted((SRC / "tugboat").rglob("*.py"))
        self.assertTrue(files)
        for file in files:
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    with self.subTest(file=file.name, module=module):
                        self.assertIn(module.split(".")[0], allowed)


if __name__ == "__main__":
    unittest.main()
