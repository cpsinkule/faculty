"""private_files.write_private_json: a kept sign-in file is whole, compact and 0600, or untouched."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from private_files import write_private_json


class WritePrivateJsonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "session.json"

    def listing(self):
        return sorted(item.name for item in Path(self.directory.name).iterdir())

    def test_the_file_holds_compact_json_with_mode_0600_and_no_temporary_beside_it(self):
        write_private_json(str(self.path), {"b": ["é", 1], "a": None}, ".test-session-")
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"b": ["\\u00e9", 1], "a": null}')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.listing(), ["session.json"])

    def test_a_second_write_replaces_the_first(self):
        write_private_json(str(self.path), {"n": 1}, ".test-session-")
        write_private_json(str(self.path), {"n": 2}, ".test-session-")
        self.assertEqual(json.loads(self.path.read_text()), {"n": 2})
        self.assertEqual(self.listing(), ["session.json"])

    def test_a_failure_leaves_the_earlier_file_and_no_temporary(self):
        write_private_json(str(self.path), {"n": 1}, ".test-session-")
        with self.assertRaises(TypeError):
            write_private_json(str(self.path), {"n": object()}, ".test-session-")
        self.assertEqual(json.loads(self.path.read_text()), {"n": 1})
        self.assertEqual(self.listing(), ["session.json"])

    def test_a_failed_replace_removes_the_temporary(self):
        with patch("private_files.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                write_private_json(str(self.path), {"n": 1}, ".test-session-")
        self.assertEqual(self.listing(), [])

    def test_the_temporary_is_named_by_the_prefix(self):
        seen = []
        real = os.replace

        def spy(source, destination):
            seen.append(Path(source).name)
            real(source, destination)

        with patch("private_files.os.replace", side_effect=spy):
            write_private_json(str(self.path), {}, ".canvas-session-")
        self.assertTrue(seen[0].startswith(".canvas-session-"), seen)


if __name__ == "__main__":
    unittest.main()
