"""The check runner must not inherit a production catalog or mail credentials."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manage_checks import environment, scratch_root


class CheckIsolationTests(unittest.TestCase):
    def test_production_environment_is_replaced_by_isolated_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "FACULTY_STORAGE_CONFIG": "/production/catalog.json", "FACULTY_REQUIRE_STORAGE": "1",
            "FACULTY_GMAIL_TOKEN_FILE": "/production/token.json", "FACULTY_RESPONSE_CARDS_SECRET": "do-not-use",
            "FACULTY_RESPONSE_CARDS_URL": "https://production.invalid", "DATA_DIR": "/production/data",
            "FACULTY_GRADESCOPE_CREDENTIALS_FILE": "/production/gradescope.json", "GRADESCOPE_EMAIL": "live", "GRADESCOPE_PASSWORD": "live",
        }):
            env = environment(Path(directory))
            self.assertEqual(env["DATA_DIR"], str(Path(directory) / "fallback-data"))
            self.assertEqual(env["FACULTY_STORAGE_CONFIG"], str(Path(directory) / "unused-storage.json"))
            self.assertNotIn("FACULTY_REQUIRE_STORAGE", env)
            self.assertNotIn("FACULTY_GMAIL_TOKEN_FILE", env)
            self.assertNotIn("FACULTY_RESPONSE_CARDS_SECRET", env)
            self.assertNotIn("FACULTY_RESPONSE_CARDS_URL", env)
            # Sending grades to Gradescope: a check never finds the saved sign-in, nor a Gradescope email or password.
            for name in ("FACULTY_GRADESCOPE_CREDENTIALS_FILE", "GRADESCOPE_EMAIL", "GRADESCOPE_PASSWORD"):
                self.assertNotIn(name, env)
            self.assertEqual(env["TMPDIR"], str(Path(directory) / "tmp"))

    def test_live_provider_credentials_never_reach_a_test_process(self):
        secrets = {"OPENROUTER_API_KEY": "sk-or-live", "DEEPINFRA_API_KEY": "live", "NEBIUS_API_KEY": "live", "MATHPIX_APP_KEY": "live", "MATHPIX_APP_ID": "live",
                   "MODAL_TOKEN_ID": "live", "MODAL_TOKEN_SECRET": "live", "RUNPOD_API_KEY": "live", "ANTHROPIC_API_KEY": "live", "MY_OWN_HOST_API_KEY": "live",
                   "HF_TOKEN": "live", "SOME_SERVICE_SECRET": "live"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {**secrets, "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}):
            env = environment(Path(directory))
        self.assertEqual(set(secrets) & set(env), set())
        self.assertNotIn("live", env.values())
        self.assertIn("PATH", env)
        self.assertEqual(env["HF_HOME"], str(Path(directory) / "cache" / "huggingface"))  # Set by the runner itself, never inherited.

    def test_root_disk_and_relative_scratch_are_refused_before_creating_anything(self):
        for path in ("/var/tmp/faculty-checks", "relative-checks", str(Path.home() / "faculty-checks")):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "/mnt drive"):
                scratch_root(path)
