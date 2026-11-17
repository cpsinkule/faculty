"""The shared print-batch origin rule (document_identity/origins.py) and its agreement with registration."""
import re
import unittest
from pathlib import Path

from document_identity import origins


class PrintOriginTests(unittest.TestCase):
    def test_registration_jobs_never_count_as_printing(self):
        self.assertEqual(origins.batch_origin("unlabeled:run-1:section-2:abcdef0123456789", None), "registration")
        self.assertEqual(origins.batch_origin("unlabeled:run-1:reuse", "chart-1"), "registration")
        self.assertTrue(origins.is_registration("unlabeled:x"))

    def test_seating_and_section_jobs(self):
        self.assertEqual(origins.batch_origin("request-1:section-9", "chart-1"), "seating")
        self.assertEqual(origins.batch_origin("request-1", None), "section")
        self.assertEqual(origins.batch_origin("request-1", ""), "section")
        self.assertEqual(origins.batch_origin(None, None), "section")
        # Only the prefix marks a registration; the same word elsewhere does not.
        self.assertEqual(origins.batch_origin("wizard:unlabeled:1", None), "section")
        self.assertEqual(origins.batch_origin("UNLABELED:1", None), "section")
        self.assertFalse(origins.is_registration(None))

    def test_prefix_matches_what_registration_issues(self):
        source = (Path(__file__).resolve().parents[1] / "document_identity" / "unlabeled.py").read_text()
        prefixes = set(re.findall(r'f"([a-z_]+:)\{run', source))
        self.assertEqual(prefixes, {origins.REGISTRATION_PREFIX})


if __name__ == "__main__":
    unittest.main()
