"""Exercise real copies and verification failures on isolated temporary disks."""

import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from app import create_app
from manage_storage import migrate_catalog
import course_storage
from course_storage import CourseStorage, StorageError, MARKER, MANIFEST, atomic_json, exclusive, inventory

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


def temporary_storage(directory):
    config = {"deployment_id": str(uuid.uuid4()), "catalog_on_nvme": True}
    for role in ("hdd", "nvme"):
        root = Path(directory) / role
        root.mkdir()
        config[f"{role}_root"] = str(root)
        config[f"{role}_mount"] = str(root)
        atomic_json(root / MARKER, {"deployment_id": config["deployment_id"], "role": role})
    return CourseStorage(config, enforce_mounts=False)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name)
        self.app = create_app(storage=self.storage)
        self.app.testing = True
        self.client = self.app.test_client()

    def add(self, current=True, reconcile=True):
        response = self.client.post("/api/courses", json={
            "name": "Biology", "code": "BIO 101", "term": "Fall", "year": 2026,
            "sections": [1], "is_current": current,
        }, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        course = response.json
        if reconcile:
            self.storage.reconcile_course(course["id"])
            course = self.storage.row(course["id"])
        return course

    def change(self, course, **values):
        current = self.storage.row(course["id"])
        response = self.client.patch(f"/api/courses/{course['id']}", json={
            "version": current["version"], **values,
        }, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def payload(self, course):
        with self.storage.course_files(course["id"], write=True) as path:
            (path / "rosters").mkdir()
            (path / "rosters" / "class.csv").write_bytes(b"name,section\nStudent,1\n")
            (path / "empty folder").mkdir()
            (path / "binary.bin").write_bytes(bytes(range(256)) * 32)
            with sqlite3.connect(path / "roster.sqlite3") as db:
                db.execute("CREATE TABLE students (name TEXT)")
                db.execute("INSERT INTO students VALUES ('Student')")
            return inventory(path)

    def test_nightly_backup_archive_and_reactivate_preserve_every_file(self):
        course = self.add()
        expected = self.payload(course)
        result = self.storage.backup_all()
        self.assertEqual((result["status"], result["backed_up"]), ("complete", 1))
        self.assertTrue(self.storage.hot_path(course["id"]).is_dir())
        # Changes after the nightly copy must be included in the final archive.
        with self.storage.course_files(course["id"], write=True) as path:
            (path / "late.txt").write_text("after the nightly backup")
            expected = inventory(path)
        self.change(course, is_current=False)
        self.storage.reconcile_course(course["id"])
        archived = self.storage.row(course["id"])
        self.assertEqual(archived["storage_status"], "archived")
        self.assertFalse(self.storage.hot_path(course["id"]).exists())
        manifest = self.storage.verify_snapshot(course["id"], archived["storage_snapshot"])
        self.assertIn("data/late.txt", manifest["files"])
        with self.storage.course_files(course["id"]) as path:
            self.assertEqual(inventory(path), expected)
        state = self.client.get("/api/state").json
        self.assertEqual(len(state["courses"]), 1)
        self.assertFalse(state["courses"][0]["is_current"])
        self.change(course, is_current=True)
        self.storage.reconcile_course(course["id"])
        with self.storage.course_files(course["id"]) as path:
            self.assertEqual(inventory(path), expected)
        with sqlite3.connect(path / "roster.sqlite3") as db:
            self.assertEqual(db.execute("SELECT name FROM students").fetchone()[0], "Student")

    def test_non_current_creation_and_edits_stay_on_hdd(self):
        course = self.add(current=False)
        self.assertEqual(course["storage_status"], "archived")
        expected = self.payload(course)
        self.change(course, name="Biology II")
        self.storage.reconcile_course(course["id"])
        updated = self.storage.row(course["id"])
        source = self.storage.snapshot_path(course["id"], updated["storage_snapshot"])
        self.assertEqual(json.loads((source / "course.json").read_text())["name"], "Biology II")
        self.assertEqual(inventory(source / "data"), expected)
        self.assertEqual(list((self.storage.nvme / "courses").iterdir()), [])
        self.assertEqual(list((self.storage.nvme / "staging").iterdir()), [])

    def test_shared_definition_keeps_offering_files_and_archive_lifecycles_separate(self):
        first, second = self.add(), self.add()
        self.assertEqual(first["definition_id"], second["definition_id"])
        with self.storage.course_files(first["id"], write=True) as path:
            (path / "roster.csv").write_text("first offering")
        with self.storage.course_files(second["id"], write=True) as path:
            (path / "roster.csv").write_text("second offering")
        self.change(first, is_current=False)
        self.storage.reconcile_course(first["id"])
        self.assertFalse(self.storage.hot_path(first["id"]).exists())
        self.assertTrue(self.storage.hot_path(second["id"]).exists())
        with self.storage.course_files(first["id"]) as path:
            metadata = json.loads((path.parent / "course.json").read_text())
            self.assertEqual(metadata["definition_id"], second["definition_id"])
            self.assertEqual((path / "roster.csv").read_text(), "first offering")
        with self.storage.course_files(second["id"]) as path:
            self.assertEqual((path / "roster.csv").read_text(), "second offering")

    def test_failed_copy_retains_nvme_and_retries_after_restart(self):
        course = self.add()
        expected = self.payload(course)
        self.change(course, is_current=False)
        with patch("course_storage.copy_inventory", side_effect=OSError("disk full")):
            with self.assertLogs("course_storage", level="ERROR"):
                self.assertEqual(self.storage.reconcile_all(), [course["id"]])
        self.assertEqual(self.storage.row(course["id"])["storage_status"], "archive_pending")
        self.assertEqual(inventory(self.storage.hot_path(course["id"]) / "data"), expected)
        self.assertIn("disk full", self.client.get("/api/state").json["storage"]["issues"][0]["message"])
        restarted = CourseStorage(self.storage.config, enforce_mounts=False)
        self.assertEqual(restarted.reconcile_all(), [])
        self.assertEqual(restarted.row(course["id"])["storage_status"], "archived")

    def test_corrupt_hdd_snapshot_prevents_purge(self):
        course = self.add()
        expected = self.payload(course)
        self.change(course, is_current=False)
        original_verify = self.storage.verify_snapshot

        def corrupt_before_verify(identifier, snapshot):
            target = self.storage.snapshot_path(identifier, snapshot) / "data" / "binary.bin"
            target.write_bytes(b"corrupt")
            return original_verify(identifier, snapshot)

        with patch.object(self.storage, "verify_snapshot", side_effect=corrupt_before_verify):
            with self.assertRaisesRegex(StorageError, "verification failed"):
                self.storage.reconcile_course(course["id"])
        self.assertEqual(inventory(self.storage.hot_path(course["id"]) / "data"), expected)
        self.assertEqual(self.storage.row(course["id"])["storage_status"], "archive_pending")

    def test_partial_purge_resumes_from_committed_backup_without_recopied_partial_data(self):
        course = self.add()
        expected = self.payload(course)
        self.change(course, is_current=False)
        hot = self.storage.hot_path(course["id"])

        def partial_delete(path):
            self.assertEqual(path, hot)
            (path / "data" / "binary.bin").unlink()
            raise OSError("simulated interrupted deletion")

        with patch("course_storage.shutil.rmtree", side_effect=partial_delete):
            with self.assertRaises(OSError):
                self.storage.reconcile_course(course["id"])
        pending = self.storage.row(course["id"])
        self.assertEqual(pending["storage_status"], "purge_pending")
        snapshot = pending["storage_snapshot"]
        restarted = CourseStorage(self.storage.config, enforce_mounts=False)
        restarted.reconcile_course(course["id"])
        self.assertFalse(hot.exists())
        self.assertEqual(restarted.row(course["id"])["storage_snapshot"], snapshot)
        with restarted.course_files(course["id"]) as path:
            self.assertEqual(inventory(path), expected)

    def test_new_data_after_final_verification_stops_purge(self):
        course = self.add()
        self.payload(course)
        self.change(course, is_current=False)
        with patch.object(self.storage, "_purge", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.storage.reconcile_course(course["id"])
        target = self.storage.hot_path(course["id"])
        (target / "data" / "unexpected.txt").write_text("preserve this")
        with self.assertRaisesRegex(StorageError, "changed after"):
            self.storage.reconcile_course(course["id"])
        self.assertTrue((target / "data" / "unexpected.txt").exists())

    def test_uncommitted_snapshot_pointer_prevents_any_purge(self):
        course = self.add()
        self.payload(course)
        self.change(course, is_current=False)
        with patch.object(self.storage, "update", side_effect=sqlite3.OperationalError("commit failed")):
            with self.assertRaises(sqlite3.OperationalError):
                self.storage.reconcile_course(course["id"])
        self.assertTrue((self.storage.hot_path(course["id"]) / "data" / "binary.bin").exists())
        self.assertEqual(self.storage.row(course["id"])["storage_status"], "archive_pending")

    def test_changed_or_missing_drive_identity_blocks_reads_and_mutations(self):
        course = self.add()
        marker = self.storage.hdd / MARKER
        original = marker.read_bytes()
        for value in (b"{}", b"unreadable"):
            marker.write_bytes(value)
            self.assertEqual(self.client.get("/api/state").status_code, 503)
            self.assertEqual(self.client.patch(f"/api/courses/{course['id']}", json={"version": 1, "is_current": False}, headers=HEADERS).status_code, 503)
            self.assertEqual(self.storage.nvme.joinpath("courses", course["id"]).is_dir(), True)
        marker.write_bytes(original)
        # Even valid markers cannot substitute for actual mounted, separate disks.
        with self.assertRaisesRegex(StorageError, "not mounted"):
            CourseStorage(self.storage.config)

    def test_symbolic_links_are_never_archived_or_purged(self):
        course = self.add()
        outside = Path(self.directory.name) / "outside.txt"
        outside.write_text("unrelated data")
        hot = self.storage.hot_path(course["id"])
        (hot / "data" / "link").symlink_to(outside)
        self.change(course, is_current=False)
        with self.assertRaisesRegex(StorageError, "link or special"):
            self.storage.reconcile_course(course["id"])
        self.assertEqual(outside.read_text(), "unrelated data")
        (hot / "data" / "link").unlink()
        courses = self.storage.nvme / "courses"
        moved = self.storage.nvme / "redirected"
        courses.rename(moved)
        courses.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(StorageError, "redirected"):
            self.storage.reconcile_course(course["id"])
        self.assertTrue((moved / course["id"]).exists())

    def test_pending_and_locked_courses_reject_concurrent_edits(self):
        course = self.add(reconcile=False)
        route = f"/api/courses/{course['id']}"
        self.assertEqual(self.client.patch(route, json={"version": 1, "name": "Edited"}, headers=HEADERS).status_code, 409)
        self.storage.reconcile_course(course["id"])
        with self.storage.course_files(course["id"], write=True):
            self.assertEqual(self.client.patch(route, json={"version": 1, "name": "Edited"}, headers=HEADERS).status_code, 503)
        self.assertEqual(self.storage.row(course["id"])["name"], "Biology")

    def test_removal_keeps_final_hdd_copy_and_reports_hidden_course_failures(self):
        course = self.add()
        expected = self.payload(course)
        response = self.client.delete(f"/api/courses/{course['id']}", json={"version": 1}, headers=HEADERS)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/api/state").json["courses"], [])
        with patch.object(self.storage, "snapshot", side_effect=OSError("HDD full")):
            with self.assertLogs("course_storage", level="ERROR"):
                self.storage.reconcile_all()
        self.assertEqual(len(self.client.get("/api/state").json["storage"]["issues"]), 1)
        self.storage.reconcile_course(course["id"])
        deleted = self.storage.row(course["id"])
        self.assertEqual(deleted["storage_status"], "deleted")
        self.assertFalse(self.storage.hot_path(course["id"]).exists())
        path = self.storage.snapshot_path(course["id"], deleted["storage_snapshot"])
        self.assertEqual(inventory(path / "data"), expected)

    def test_missing_active_data_does_not_silently_archive_an_older_backup(self):
        course = self.add()
        self.payload(course)
        self.storage.backup_all()
        hot = self.storage.hot_path(course["id"])
        hot.rename(hot.with_name("temporarily-missing"))
        self.change(course, is_current=False)
        with self.assertRaisesRegex(StorageError, "older backup"):
            self.storage.reconcile_course(course["id"])
        self.assertEqual(self.storage.row(course["id"])["storage_status"], "archive_pending")

    def test_catalog_backup_is_integrity_checked_and_restorable(self):
        self.add()
        result = self.storage.backup_all()
        path = self.storage.hdd / "workspace-backups" / result["catalog_snapshot"]
        with sqlite3.connect(path / "faculty.sqlite3") as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT name FROM courses").fetchone()[0], "Biology")
        manifest = json.loads((path / MANIFEST).read_text())
        self.assertEqual(inventory(path, skip_manifest=True), manifest["files"])

    def test_offline_catalog_migration_preserves_wal_data_and_resumes_before_config_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            blank = temporary_storage(folder)
            config = {**blank.config, 'catalog_on_nvme': False}
            old = CourseStorage(config, enforce_mounts=False)
            create_app(storage=old)
            config_path = Path(folder) / 'deployment.json'
            atomic_json(config_path, config)
            with sqlite3.connect(old.database) as db:
                db.execute("UPDATE settings SET workspace_name='Migration test'")
                db.commit()
                self.assertTrue(old.database.with_name('faculty.sqlite3-wal').exists())
                with patch('manage_storage.atomic_json', side_effect=OSError('interrupted before activation')):
                    with self.assertRaises(OSError):
                        migrate_catalog(config_path, enforce_mounts=False)
                self.assertFalse(json.loads(config_path.read_text())['catalog_on_nvme'])
                migrated = migrate_catalog(config_path, enforce_mounts=False)
            new = CourseStorage(json.loads(config_path.read_text()), enforce_mounts=False)
            self.assertEqual(migrated['status'], 'migrated')
            self.assertEqual(new.database, blank.nvme / 'catalog' / 'faculty.sqlite3')
            self.assertTrue(old.database.exists())
            with new.connect() as db:
                self.assertEqual(db.execute('SELECT workspace_name FROM settings').fetchone()[0], 'Migration test')
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(migrate_catalog(config_path, enforce_mounts=False)['status'], 'already_migrated')
            new.database.rename(new.database.with_suffix('.saved'))
            with self.assertRaisesRegex(StorageError, 'NVMe catalog is missing'):
                CourseStorage(new.config, enforce_mounts=False)

    def test_catalog_migration_never_overwrites_a_different_nvme_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            blank = temporary_storage(folder)
            config = {**blank.config, 'catalog_on_nvme': False}
            old = CourseStorage(config, enforce_mounts=False)
            create_app(storage=old)
            config_path = Path(folder) / 'deployment.json'
            atomic_json(config_path, config)
            destination = blank.nvme / 'catalog' / 'faculty.sqlite3'
            destination.write_bytes(b'Existing unrelated catalog')
            with self.assertRaisesRegex(StorageError, 'neither copy was replaced'):
                migrate_catalog(config_path, enforce_mounts=False)
            self.assertEqual(destination.read_bytes(), b'Existing unrelated catalog')
            self.assertFalse(json.loads(config_path.read_text())['catalog_on_nvme'])

    def snapshots(self, course):
        return sorted(path.name for path in (self.storage.hdd / "courses" / course["id"] / "snapshots").iterdir())

    def lifecycle_free(self):
        try:
            with self.storage.lifecycle_lock(blocking=False):
                return True
        except StorageError:
            return False

    def test_unchanged_working_copy_keeps_its_verified_snapshot(self):
        course = self.add()
        self.payload(course)
        first = self.storage.backup_all()
        self.assertEqual((first["copied_ahead"], first["unchanged"], first["copied_while_locked"]), (1, 0, 0))
        recorded = self.storage.row(course["id"])
        second = self.storage.backup_all()
        self.assertEqual((second["status"], second["unchanged"], second["copied_ahead"]), ("complete", 1, 0))
        kept = self.storage.row(course["id"])
        self.assertEqual(kept["storage_snapshot"], recorded["storage_snapshot"])
        self.assertGreater(kept["storage_verified_at"], recorded["storage_verified_at"])
        self.assertEqual(self.snapshots(course), [recorded["storage_snapshot"]])
        # The catalog backup of that night points at the snapshot and verification it recorded.
        with sqlite3.connect(self.storage.hdd / "workspace-backups" / second["catalog_snapshot"] / "faculty.sqlite3") as db:
            saved = db.execute("SELECT storage_snapshot, storage_verified_at FROM courses WHERE id=?", (course["id"],)).fetchone()
        self.assertEqual(saved, (kept["storage_snapshot"], kept["storage_verified_at"]))
        # A changed working copy gets a new snapshot; the earlier one stays.
        with self.storage.course_files(course["id"], write=True) as path:
            (path / "late.txt").write_text("changed after the first backup")
            expected = inventory(path)
        third = self.storage.backup_all()
        self.assertEqual((third["unchanged"], third["copied_ahead"]), (0, 1))
        changed = self.storage.row(course["id"])
        self.assertEqual(self.snapshots(course), sorted([recorded["storage_snapshot"], changed["storage_snapshot"]]))
        self.storage.verify_snapshot(course["id"], recorded["storage_snapshot"])
        self.assertEqual(inventory(self.storage.snapshot_path(course["id"], changed["storage_snapshot"]) / "data"), expected)

    def test_damaged_recorded_snapshot_is_reported_and_never_reused(self):
        course = self.add()
        expected = self.payload(course)
        self.storage.backup_all()
        damaged = self.storage.row(course["id"])["storage_snapshot"]
        # Fault injection on this test's synthetic snapshot, under the locks a snapshot reader holds.
        with self.storage.lifecycle_lock(), self.storage.lock(course["id"]):
            (self.storage.snapshot_path(course["id"], damaged) / "data" / "binary.bin").write_bytes(b"damaged")
        with self.assertLogs("course_storage", level="ERROR"):
            with self.assertRaisesRegex(StorageError, f"{damaged} failed verification and was not reused"):
                self.storage.backup_all()
        replaced = self.storage.row(course["id"])
        self.assertNotEqual(replaced["storage_snapshot"], damaged)
        self.assertEqual(replaced["storage_error"], "")
        self.assertEqual(self.storage.status()["last_backup"]["status"], "failed")
        path = self.storage.snapshot_path(course["id"], replaced["storage_snapshot"])
        self.storage.verify_snapshot(course["id"], replaced["storage_snapshot"])
        self.assertEqual(inventory(path / "data"), expected)
        # The damaged snapshot is kept as it is; nothing deletes a snapshot.
        self.assertEqual((self.storage.snapshot_path(course["id"], damaged) / "data" / "binary.bin").read_bytes(), b"damaged")
        again = self.storage.backup_all()
        self.assertEqual((again["status"], again["unchanged"]), ("complete", 1))

    def test_changes_after_preparation_are_copied_under_the_lock(self):
        course = self.add()
        self.payload(course)
        self.storage.backup_all()
        with self.storage.course_files(course["id"], write=True) as path:
            (path / "new.txt").write_text("before tonight's backup")
        prepare, published = self.storage.prepare_backup, []

        def prepare_then_write(owner, damaged):
            prepared = prepare(owner, damaged)
            published.append(prepared["snapshot"])
            with self.storage.course_files(owner["id"], write=True) as path:
                (path / "late.txt").write_text("written between preparation and capture")
            return prepared

        with patch.object(self.storage, "prepare_backup", side_effect=prepare_then_write):
            result = self.storage.backup_all()
        self.assertEqual((result["status"], result["copied_while_locked"], result["copied_ahead"]), ("complete", 1, 0))
        recorded = self.storage.row(course["id"])["storage_snapshot"]
        self.assertNotIn(recorded, published)
        self.assertIn("data/late.txt", self.storage.verify_snapshot(course["id"], recorded)["files"])
        # The prepared snapshot is older than the working copy: published, verified, never recorded.
        self.assertNotIn("data/late.txt", self.storage.verify_snapshot(course["id"], published[0])["files"])

    def test_hdd_work_runs_outside_the_lifecycle_lock_and_the_capture_holds_it(self):
        course = self.add()
        self.payload(course)
        self.storage.backup_all()
        seen = []

        def watched(name, function):
            def call(*args, **kwargs):
                seen.append((name, self.lifecycle_free()))
                return function(*args, **kwargs)
            return call

        with patch.object(self.storage, "snapshot", side_effect=watched("snapshot", self.storage.snapshot)), \
                patch.object(self.storage, "verify_snapshot", side_effect=watched("verify", self.storage.verify_snapshot)), \
                patch.object(self.storage, "capture_catalog", side_effect=watched("capture", self.storage.capture_catalog)), \
                patch.object(self.storage, "publish_catalog", side_effect=watched("publish", self.storage.publish_catalog)):
            unchanged = self.storage.backup_all()
            self.assertEqual(seen, [("verify", True), ("capture", False), ("publish", True)])
            seen.clear()
            with self.storage.course_files(course["id"], write=True) as path:
                (path / "new.txt").write_text("changed")
            changed = self.storage.backup_all()
            self.assertEqual(seen, [("snapshot", True), ("verify", True), ("capture", False), ("publish", True)])
        self.assertEqual((unchanged["unchanged"], changed["copied_ahead"]), (1, 1))
        self.assertIsInstance(changed["locked_seconds"], float)

    def test_copy_of_a_changing_source_is_discarded_and_nothing_writes_through_a_name(self):
        course = self.add()
        self.payload(course)
        copy = course_storage.copy_inventory

        def copy_then_change(source, destination, entries):
            copy(source, destination, entries)
            (source / "data" / "binary.bin").write_bytes(b"changed during the copy")

        staging = self.storage.hdd / "staging"
        before = set(staging.iterdir())
        with patch("course_storage.copy_inventory", side_effect=copy_then_change):
            with self.assertRaisesRegex(StorageError, "changed while copying"):
                self.storage.snapshot(self.storage.row(course["id"]), self.storage.hot_path(course["id"]))
        self.assertEqual(set(staging.iterdir()), before)
        folder = Path(self.directory.name)
        (folder / "source").write_bytes(b"new")
        (folder / "kept").write_bytes(b"retained")
        with self.assertRaises(FileExistsError):
            course_storage.copy_file(folder / "source", folder / "kept")
        self.assertEqual((folder / "kept").read_bytes(), b"retained")

    def test_failed_flush_records_no_snapshot_and_fsync_remains_the_fallback(self):
        course = self.add()
        expected = self.payload(course)
        with patch("course_storage.sync_filesystem", side_effect=OSError(5, "Input/output error")):
            with self.assertLogs("course_storage", level="ERROR"):
                with self.assertRaisesRegex(StorageError, "Input/output error"):
                    self.storage.backup_all()
        self.assertEqual(self.storage.row(course["id"])["storage_snapshot"], "")
        with patch("course_storage.sync_filesystem", return_value=False):
            self.assertEqual(self.storage.backup_all()["status"], "complete")
        recorded = self.storage.row(course["id"])["storage_snapshot"]
        self.assertEqual(inventory(self.storage.snapshot_path(course["id"], recorded) / "data"), expected)

    def test_catalog_backup_retention_keeps_days_then_weeks_then_months(self):
        now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        days = {"a": "20260920", "b": "20260910", "c": "20260912", "d": "20260831", "e": "20260901",
                "f": "20260505", "g": "20260520", "h": "20260629", "i": "20260630"}
        names = {key: f"{day}T090000000000Z" for key, day in days.items()}
        kept = course_storage.kept_catalog_backups(names.values(), now)
        # a: under 14 days; c, e: newest of their weeks; d, i, g: newest of their months (i is 89 days old, g 130).
        self.assertEqual(sorted(key for key, name in names.items() if name in kept), ["a", "c", "d", "e", "g", "i"])
        # Pruning every night keeps exactly what pruning once at the end keeps: nothing deleted is wanted later.
        start, existing, every = datetime(2026, 1, 1, 9, tzinfo=timezone.utc), set(), set()
        for day in range(400):
            when = start + timedelta(days=day)
            name = when.strftime("%Y%m%dT%H%M%S%fZ")
            existing.add(name)
            every.add(name)
            existing = course_storage.kept_catalog_backups(existing, when + timedelta(hours=1))
        self.assertEqual(existing, course_storage.kept_catalog_backups(every, when + timedelta(hours=1)))
        self.assertLess(len(existing), 40)

    def backups_with_history(self, nights):
        """Nights of backups of a changing course; returns their results and the snapshot each recorded."""
        course = self.add()
        self.payload(course)
        results, snapshots = [], []
        for night in range(nights):
            if night:
                with self.storage.course_files(course["id"], write=True) as path:
                    (path / f"night-{night}.txt").write_text("changed")
            results.append(self.storage.backup_all())
            snapshots.append(self.storage.row(course["id"])["storage_snapshot"])
        return course, results, snapshots

    def test_retention_prunes_only_what_no_kept_catalog_backup_and_no_owner_records(self):
        course, results, snapshots = self.backups_with_history(3)
        backups = self.storage.hdd / "workspace-backups"
        parent = self.storage.hdd / "courses" / course["id"] / "snapshots"
        (parent / "not-a-snapshot").mkdir()
        now = datetime.now(timezone.utc)
        nothing = self.storage.retention_plan(now)
        self.assertEqual((nothing["catalog_backups"]["delete"], nothing["snapshots"]["delete"]), ([], []))
        # The first night's catalog backup dated 40 days back (this test's history): the only one of its week and month.
        (backups / results[0]["catalog_snapshot"]).rename(backups / (now - timedelta(days=40)).strftime("%Y%m%dT%H%M%S%fZ"))
        later = now + timedelta(days=30)
        plan = self.storage.retention_plan(later)
        self.assertEqual([item["name"] for item in plan["catalog_backups"]["delete"]], [results[1]["catalog_snapshot"]])
        # The first snapshot is kept for the kept old catalog backup, the last because the catalog records it.
        self.assertEqual([item["snapshot"] for item in plan["snapshots"]["delete"]], [snapshots[1]])
        self.assertEqual(plan["snapshots"]["kept"], 2)
        # Only the plan that was shown, only after a complete backup, never during one.
        with self.assertRaisesRegex(StorageError, "plan changed"):
            self.storage.prune(nothing["fingerprint"], later)
        with self.storage.lock("nightly-backup"), self.assertRaisesRegex(StorageError, "being backed up"):
            self.storage.prune(plan["fingerprint"], later)
        status = self.storage.hdd / "backup-status.json"
        complete = json.loads(status.read_text())
        atomic_json(status, {**complete, "status": "failed"})
        with self.assertRaisesRegex(StorageError, "only after a complete nightly backup"):
            self.storage.prune(plan["fingerprint"], later)
        atomic_json(status, complete)
        self.assertTrue((parent / snapshots[1]).is_dir() and (backups / results[1]["catalog_snapshot"]).is_dir())
        result = self.storage.prune(plan["fingerprint"], later)
        self.assertEqual((result["errors"], result["deleted_catalog_backups"]), ([], [results[1]["catalog_snapshot"]]))
        self.assertEqual([item["snapshot"] for item in result["deleted_snapshots"]], [snapshots[1]])
        self.assertEqual(sorted(path.name for path in parent.iterdir()), sorted([snapshots[0], snapshots[2], "not-a-snapshot"]))
        self.assertEqual(len(list(backups.iterdir())), 2)
        self.storage.verify_snapshot(course["id"], snapshots[2])
        log = [json.loads(line) for line in (self.storage.hdd / "retention-log.jsonl").read_text().splitlines()]
        self.assertEqual([entry["event"] for entry in log], ["planned", "done"])
        self.assertEqual(self.storage.retention_plan(later)["snapshots"]["delete"], [])

    def test_retention_skips_an_owner_whose_recorded_snapshot_is_damaged(self):
        course, results, snapshots = self.backups_with_history(2)
        later = datetime.now(timezone.utc) + timedelta(days=30)
        plan = self.storage.retention_plan(later)
        self.assertEqual([item["snapshot"] for item in plan["snapshots"]["delete"]], [snapshots[0]])
        # Fault injection on this test's synthetic snapshot, under the locks a snapshot reader holds.
        with self.storage.lifecycle_lock(), self.storage.lock(course["id"]):
            (self.storage.snapshot_path(course["id"], snapshots[1]) / "data" / "binary.bin").write_bytes(b"damaged")
        with self.assertLogs("course_storage", level="ERROR"):
            result = self.storage.prune(plan["fingerprint"], later)
        self.assertEqual(result["deleted_snapshots"], [])
        self.assertIn("verification failed", result["errors"][0])
        self.assertTrue(self.storage.snapshot_path(course["id"], snapshots[0]).is_dir())
        self.assertEqual(result["deleted_catalog_backups"], [results[0]["catalog_snapshot"]])

    def test_retention_keeps_payloads_when_their_catalog_backup_cannot_be_removed(self):
        # STORAGE: catalog backups go first; a failed deletion still leaves a catalog that needs its snapshots.
        course, results, snapshots = self.backups_with_history(2)
        later = datetime.now(timezone.utc) + timedelta(days=30)
        plan = self.storage.retention_plan(later)
        catalog = self.storage.hdd / "workspace-backups" / results[0]["catalog_snapshot"]
        snapshot = self.storage.snapshot_path(course["id"], snapshots[0])
        remove = shutil.rmtree

        def refuse_catalog(path, *args, **kwargs):
            if Path(path) == catalog:
                raise OSError("Synthetic catalog deletion failure")
            return remove(path, *args, **kwargs)

        with patch("course_storage.shutil.rmtree", side_effect=refuse_catalog), self.assertLogs("course_storage", level="ERROR"):
            result = self.storage.prune(plan["fingerprint"], later)
        self.assertEqual(result["deleted_catalog_backups"], [])
        self.assertEqual(result["deleted_snapshots"], [])
        self.assertIn("catalog deletion failure", result["errors"][0])
        self.assertTrue(catalog.is_dir())
        self.assertTrue(snapshot.is_dir())
        self.storage.verify_snapshot(course["id"], snapshots[0])
        log = [json.loads(line) for line in (self.storage.hdd / "retention-log.jsonl").read_text().splitlines()]
        self.assertEqual(log[-1]["event"], "done")
        self.assertEqual(log[-1]["errors"], result["errors"])

    def test_an_unreadable_kept_catalog_backup_stops_the_plan(self):
        _, results, _ = self.backups_with_history(1)
        catalog = self.storage.hdd / "workspace-backups" / results[0]["catalog_snapshot"] / "faculty.sqlite3"
        catalog.write_bytes(b"not a database" * 100)
        with self.assertRaisesRegex(StorageError, "cannot be read"):
            self.storage.retention_plan(datetime.now(timezone.utc) + timedelta(days=30))

    def test_nightly_failure_remains_visible_and_preserves_working_data(self):
        course = self.add()
        expected = self.payload(course)
        with patch.object(self.storage, "snapshot", side_effect=OSError("HDD full")):
            with self.assertLogs("course_storage", level="ERROR"):
                with self.assertRaises(StorageError):
                    self.storage.backup_all()
        status = self.storage.status()
        self.assertEqual(status["last_backup"]["status"], "failed")
        self.assertEqual(inventory(self.storage.hot_path(course["id"]) / "data"), expected)
        self.storage.backup_all()
        self.assertEqual(self.storage.status()["last_backup"]["status"], "complete")


class ExclusiveRoundTests(unittest.TestCase):
    """`exclusive`: one background round at a time, by the manager's lock or, without a manager, a lock file."""

    class Held:
        """A stand-in manager whose lock is already held, or fails with the given error."""

        def __init__(self, error=None):
            self.error, self.taken = error, []

        @contextmanager
        def lock(self, lock_id, blocking=True):
            self.taken.append((lock_id, blocking))
            if self.error:
                raise self.error
            yield

    def failure(self, cause=None):
        error = StorageError("busy")
        error.__cause__ = cause
        return error

    def test_the_manager_lock_is_taken_without_waiting_and_released(self):
        storage = self.Held()
        with exclusive(storage, "lock-id", Path("unused"), "busy now"):
            pass
        self.assertEqual(storage.taken, [("lock-id", False)])

    def test_a_lock_held_elsewhere_is_a_conflict_with_the_message(self):
        busy = self.failure(BlockingIOError())
        with self.assertRaises(Conflict) as caught:
            with exclusive(self.Held(busy), "lock-id", Path("unused"), "A round is running now."):
                self.fail("the body must not run while the lock is held")
        self.assertEqual(caught.exception.description, "A round is running now.")
        self.assertIs(caught.exception.__cause__, busy)

    def test_any_other_storage_error_passes_through(self):
        other = self.failure()
        with self.assertRaises(StorageError) as caught:
            with exclusive(self.Held(other), "lock-id", Path("unused"), "busy now"):
                pass
        self.assertIs(caught.exception, other)

    def test_a_storage_error_inside_the_body_is_mapped_like_the_acquisition(self):
        storage = self.Held()
        busy = self.failure(BlockingIOError())
        with self.assertRaises(Conflict):
            with exclusive(storage, "lock-id", Path("unused"), "busy now"):
                raise busy

    def test_without_a_manager_a_second_entry_conflicts_until_the_first_leaves(self):
        with tempfile.TemporaryDirectory() as directory:
            fallback = Path(directory) / ".round.lock"
            with exclusive(None, "lock-id", fallback, "busy now"):
                with self.assertRaises(Conflict) as caught:
                    with exclusive(None, "lock-id", fallback, "busy now"):
                        self.fail("the body must not run while the lock is held")
                self.assertEqual(caught.exception.description, "busy now")
                self.assertIsInstance(caught.exception.__cause__, BlockingIOError)
            with exclusive(None, "lock-id", fallback, "busy now"):
                pass
            self.assertEqual(fallback.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
