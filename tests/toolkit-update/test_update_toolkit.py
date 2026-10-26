import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/toolkit-update/update_toolkit.py"
SPEC = importlib.util.spec_from_file_location("update_toolkit", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

OLD = "1" * 40
NEW = "2" * 40
OLD_TREE = "a" * 40
NEW_TREE = "b" * 40


class UpdateToolkitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous_directory = Path.cwd()
        os.chdir(temporary.name)
        self.addCleanup(os.chdir, previous_directory)
        self.body = Path("body.md")
        self.dependency = {
            "host": "github.com",
            "repo_url": updater.UPSTREAM,
            "virtual_path": updater.TOOLKIT_PATH,
            "resolved_commit": OLD,
        }
        self.write_lock([self.dependency])

    def write_lock(self, dependencies):
        Path("apm.lock.yaml").write_text(yaml.safe_dump({"dependencies": dependencies}))

    def test_lock_selects_only_the_expected_toolkit(self):
        other = {**self.dependency, "virtual_path": "toolkits/universal"}
        self.write_lock([other, self.dependency])
        self.assertEqual(updater.pinned_commit(), OLD)

    def test_invalid_or_ambiguous_lock_fails(self):
        for dependencies in (
            [], [self.dependency, self.dependency],
            [{**self.dependency, "host": "example.com"}],
            [{**self.dependency, "resolved_commit": "main"}],
        ):
            with self.subTest(dependencies=dependencies):
                self.write_lock(dependencies)
                with self.assertRaises(ValueError):
                    updater.pinned_commit()

    def test_malformed_lock_fails(self):
        for contents in ("null", "{}", "dependencies: ["):
            with self.subTest(contents=contents):
                Path("apm.lock.yaml").write_text(contents)
                with self.assertRaises((ValueError, yaml.YAMLError)):
                    updater.pinned_commit()

    @patch.object(updater, "github_api")
    def test_tree_lookup_uses_pinned_revision_and_directory(self, api):
        api.return_value = [
            {"name": "other", "type": "dir", "sha": OLD_TREE},
            {"name": "engineering", "type": "dir", "sha": NEW_TREE},
        ]
        self.assertEqual(updater.toolkit_tree(NEW), NEW_TREE)
        api.assert_called_once_with(
            f"repos/{updater.UPSTREAM}/contents/toolkits/integration-hub?ref={NEW}"
        )

    @patch.object(updater, "github_api")
    def test_missing_or_invalid_tree_fails(self, api):
        for response in (
            {}, [],
            [{"name": "engineering", "type": "file", "sha": NEW_TREE}],
            [{"name": "engineering", "type": "dir", "sha": "invalid"}],
        ):
            with self.subTest(response=response):
                api.return_value = response
                with self.assertRaises(ValueError):
                    updater.toolkit_tree(NEW)

    @patch.object(updater, "github_api")
    def test_invalid_upstream_commit_fails(self, api):
        for response in ([], {}, {"sha": "main"}, {"sha": NEW + "\n"}):
            with self.subTest(response=response):
                api.return_value = response
                with self.assertRaises(ValueError):
                    updater.upstream_commit()

    @patch.object(updater.subprocess, "run")
    def test_github_failure_is_not_treated_as_no_update(self, run):
        for error in (
            subprocess.CalledProcessError(1, ["gh"], stderr="API error"),
            subprocess.TimeoutExpired(["gh"], 60),
            FileNotFoundError("gh"),
        ):
            with self.subTest(error=error):
                run.side_effect = error
                with self.assertRaises(type(error)):
                    updater.github_api("repos/example/repo")
        run.side_effect = None
        run.return_value.stdout = "invalid json"
        with self.assertRaises(json.JSONDecodeError):
            updater.github_api("repos/example/repo")

    @patch.object(updater, "toolkit_tree", return_value=OLD_TREE)
    @patch.object(updater, "upstream_commit", return_value=NEW)
    @patch.object(updater.subprocess, "run")
    def test_unrelated_upstream_commits_do_not_update_lock(self, run, upstream, tree):
        before = Path("apm.lock.yaml").read_bytes()
        self.assertFalse(updater.prepare_update(self.body))
        self.assertEqual(Path("apm.lock.yaml").read_bytes(), before)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [["apm", "install", "--frozen", "--target", "copilot"], ["git", "diff", "--check"]],
        )

    @patch.object(updater, "toolkit_tree", side_effect=[OLD_TREE, NEW_TREE, NEW_TREE, NEW_TREE])
    @patch.object(updater, "upstream_commit", return_value=NEW)
    @patch.object(updater.subprocess, "run")
    def test_changed_toolkit_updates_and_validates_before_writing_body(self, run, upstream, tree):
        def run_command(command, **kwargs):
            if command[:2] == ["apm", "update"]:
                self.write_lock([{**self.dependency, "resolved_commit": NEW}])
        run.side_effect = run_command
        self.assertTrue(updater.prepare_update(self.body))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], [
            "apm", "update", "--yes", "--target", "copilot", updater.PACKAGE,
        ])
        self.assertEqual(commands[1], ["apm", "install", "--frozen", "--target", "copilot"])
        body = self.body.read_text()
        self.assertIn(f"{OLD}...{NEW}", body)
        self.assertIn("## Testing", body)
        self.assertTrue(body.startswith("This pull request body was generated by GitHub Copilot."))

    @patch.object(updater, "toolkit_tree", side_effect=[OLD_TREE, NEW_TREE, OLD_TREE, NEW_TREE])
    @patch.object(updater, "upstream_commit", return_value=NEW)
    @patch.object(updater.subprocess, "run")
    def test_failed_refresh_or_upstream_race_does_not_write_body(self, run, upstream, tree):
        with self.assertRaisesRegex(ValueError, "does not match upstream"):
            updater.prepare_update(self.body)
        self.assertFalse(self.body.exists())

    @patch.object(updater, "toolkit_tree", side_effect=[OLD_TREE, NEW_TREE])
    @patch.object(updater, "upstream_commit", return_value=NEW)
    @patch.object(updater.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["apm"]))
    def test_apm_failure_does_not_write_body(self, run, upstream, tree):
        with self.assertRaises(subprocess.CalledProcessError):
            updater.prepare_update(self.body)
        self.assertFalse(self.body.exists())

    @patch.object(updater, "toolkit_tree", return_value=OLD_TREE)
    @patch.object(updater, "upstream_commit", return_value=NEW)
    @patch.object(updater.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["apm"]))
    def test_invalid_frozen_install_fails_even_without_update(self, run, upstream, tree):
        with self.assertRaises(subprocess.CalledProcessError):
            updater.prepare_update(self.body)
        self.assertFalse(self.body.exists())

    @patch.object(updater.sys, "argv", ["update_toolkit.py", "--check-only"])
    @patch.object(updater, "prepare_update", side_effect=ValueError("Invalid lockfile"))
    @patch.object(updater.sys, "stderr")
    def test_cli_reports_failure_and_returns_nonzero(self, stderr, prepare):
        self.assertEqual(updater.main(), 1)
        message = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("Toolkit update failed: Invalid lockfile", message)

    @patch.object(updater, "toolkit_tree", side_effect=[OLD_TREE, NEW_TREE])
    @patch.object(updater, "upstream_commit", return_value=NEW)
    @patch.object(updater.subprocess, "run")
    def test_check_only_does_not_install_or_write_files(self, run, upstream, tree):
        before = Path("apm.lock.yaml").read_bytes()
        self.assertTrue(updater.prepare_update(None, check_only=True))
        run.assert_not_called()
        self.assertEqual(Path("apm.lock.yaml").read_bytes(), before)
        self.assertFalse(self.body.exists())


if __name__ == "__main__":
    unittest.main()
