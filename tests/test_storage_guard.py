"""The drive guard every storage lock runs: redirected paths and changed markers are refused, as with resolve() and a fresh read."""
from pathlib import Path
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from course_storage import MARKER, StorageError, atomic_json

from test_storage import temporary_storage

NAMES = ("courses", "terms", "inbox", "definitions", "sections", "staging", "locks", "workspace-backups", "catalog", "catalog/faculty.sqlite3")


def resolved_redirect(root):
    """What the guard did before: refuse the first fixed path whose resolve() is not itself, or raise resolve()'s own error."""
    try:
        path = next((root / name for name in NAMES if (root / name).resolve() != root / name), None)
    except RuntimeError as error:  # a loop of links
        return f"RuntimeError: {error}"
    return path and f"StorageError: A storage path is redirected: {path}"


class StorageGuardTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.storage = temporary_storage(folder.name)
        self.root = self.storage.nvme
        (self.root / "catalog" / "faculty.sqlite3").write_bytes(b"")
        self.outside = self.folder / "outside"
        self.outside.mkdir()

    def outcome(self):
        try:
            self.storage.check_nvme()
        except StorageError as error:
            return str(error)
        return None

    def raised(self):
        try:
            self.storage.check_nvme()
        except (StorageError, RuntimeError) as error:
            return f"{type(error).__name__}: {error}"
        return None

    def test_a_path_is_refused_exactly_when_resolve_found_it_redirected(self):
        def link(name, target):
            path = self.root / name
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink(missing_ok=True)
            path.symlink_to(target)

        def catalog_is_a_file():
            shutil.rmtree(self.root / "catalog")
            (self.root / "catalog").write_bytes(b"not a directory")

        cases = {
            "as provisioned": lambda: None,
            "a missing directory": lambda: shutil.rmtree(self.root / "staging"),
            "a missing catalog file": lambda: (self.root / "catalog" / "faculty.sqlite3").unlink(),
            "a missing catalog directory": lambda: shutil.rmtree(self.root / "catalog"),
            "a catalog that is a file": catalog_is_a_file,
            "a directory linked outside": lambda: link("courses", self.outside),
            "a directory linked inside the root": lambda: link("inbox", self.root / "terms"),
            "a dangling link": lambda: link("sections", self.folder / "nowhere"),
            "a linked catalog file": lambda: link("catalog/faculty.sqlite3", self.outside / "faculty.sqlite3"),
            "a linked catalog directory": lambda: link("catalog", self.outside),
            "a link where nothing is expected": lambda: link("workspace-backups", self.outside),
            "a link to itself": lambda: link("courses", self.root / "courses"),
            "two links in a loop": lambda: (link("inbox", self.root / "sections"), link("sections", self.root / "inbox")),
            "a link into a loop": lambda: ((self.folder / "loop").unlink(missing_ok=True), (self.folder / "loop").symlink_to(self.folder / "loop"),
                                          link("catalog", self.folder / "loop")),
            "a linked catalog file in a loop": lambda: link("catalog/faculty.sqlite3", self.root / "catalog" / "faculty.sqlite3"),
        }
        for label, change in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.root)
                self.root.mkdir()
                atomic_json(self.root / MARKER, {"deployment_id": self.storage.config["deployment_id"], "role": "nvme"})
                for name in ("catalog", "courses", "terms", "inbox", "definitions", "sections", "staging", "locks"):
                    (self.root / name).mkdir()
                (self.root / "catalog" / "faculty.sqlite3").write_bytes(b"")
                change()
                self.assertEqual(self.raised(), resolved_redirect(self.root))

    def test_a_link_to_itself_raises_resolves_loop_error_as_before(self):
        (self.root / "courses").rmdir()
        (self.root / "courses").symlink_to(self.root / "courses")
        with self.assertRaisesRegex(RuntimeError, "Symlink loop from"):
            self.storage.check_nvme()

    def test_a_marker_rewritten_in_place_is_read_again_even_with_its_old_mtime_and_size(self):
        marker = self.root / MARKER
        later = time.time_ns() + 3_000_000_000  # as if the marker was written long ago: its reading is kept
        with patch("course_storage.time.time_ns", return_value=later):
            self.assertIsNone(self.outcome())
            self.assertIn(str(marker), self.storage._marker_identities)
            before, text = os.stat(marker), marker.read_text()
            deployment = json.loads(text)["deployment_id"]
            with open(marker, "r+") as handle:  # in place, the same length: the same inode and size
                handle.write(text.replace(deployment, deployment[::-1]))
            os.utime(marker, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(os.stat(marker).st_size, before.st_size)
            self.assertEqual(os.stat(marker).st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(self.outcome(), "The nvme drive does not match this Faculty workspace.")

    def test_a_marker_changed_moments_ago_is_never_kept(self):
        self.assertIsNone(self.outcome())
        self.assertNotIn(str(self.root / MARKER), self.storage._marker_identities)
        (self.root / MARKER).write_text("unreadable")
        self.assertEqual(self.outcome(), f"Cannot verify the nvme drive identity at {self.root}.")
        (self.root / MARKER).unlink()
        self.assertEqual(self.outcome(), f"Cannot verify the nvme drive identity at {self.root}.")

    def test_a_replaced_marker_is_read_again(self):
        later = time.time_ns() + 3_000_000_000
        with patch("course_storage.time.time_ns", return_value=later):
            self.assertIsNone(self.outcome())
            atomic_json(self.root / MARKER, {"deployment_id": self.storage.config["deployment_id"], "role": "hdd"})
            self.assertEqual(self.outcome(), "The nvme drive does not match this Faculty workspace.")
            (self.root / MARKER).unlink()
            (self.root / MARKER).symlink_to(self.storage.hdd / MARKER)
            self.assertEqual(self.outcome(), "The storage identity marker cannot be a symbolic link.")


if __name__ == "__main__":
    unittest.main()
