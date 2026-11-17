"""Repeatable disaster-recovery exercise using only synthetic, isolated disks.

The nightly catalog is opened read-only, checksum/integrity checked and copied
with SQLite's backup API to a fresh NVMe directory. Payloads are restored by the
existing storage managers under their lifecycle/owner locks, never copied by
this exercise. Neither deployment configuration nor production disks are read.
"""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app import create_app
from course_storage import CourseStorage, MANIFEST, MARKER, StorageError, atomic_json, inventory
from test_rosters import csv_bytes
from test_storage import HEADERS, temporary_storage


class RestoreExerciseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="restore-exercise-")
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name)
        self.app = create_app(storage=self.storage)
        client = self.app.test_client()
        response = client.post("/api/courses", json={"code": "MATH 140", "name": "Synthetic restore exercise", "term": "Fall",
                                                     "year": 2026, "sections": [507], "is_current": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201)
        self.course_id = response.json["id"]
        self.storage.reconcile_all()
        self.group = client.get("/api/state").json["course_groups"][0]
        self.roster_route = f"/api/course-groups/{self.group['id']}/sections/507/roster"
        response = client.post(self.roster_route, data={"file": (io.BytesIO(csv_bytes()), "synthetic.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201)
        self.roster = client.get(self.roster_route).json
        with self.app.extensions["rosters"].lock(), self.storage.course_files(self.course_id, write=True) as path:
            (path / "notes.txt").write_bytes(b"Synthetic course payload\n")
            (path / "empty").mkdir()
            self.course_files = inventory(path)
        inbox = self.app.extensions["inbox"]
        target = next(item for item in inbox.catalog()["targets"] if item["level"] == "term")
        self.job = inbox.submit({"action": "document.archive", "target": {"level": "term", "id": target["id"]}, "metadata": {}},
                                [{"filename": "synthetic.txt", "content": b"Retained original for restore exercise"}],
                                instructor_review_required=True)
        self.backup = self.storage.backup_all()
        self.assertEqual(self.backup["status"], "complete")
        self.catalog_snapshot = self.storage.hdd / "workspace-backups" / self.backup["catalog_snapshot"]

    def fresh_workspace(self):
        """Provision the disposable replacement disk; never change the source."""
        manifest = json.loads((self.catalog_snapshot / MANIFEST).read_text())
        if inventory(self.catalog_snapshot, skip_manifest=True) != manifest["files"]:
            raise StorageError("The synthetic catalog snapshot failed checksum verification.")
        fresh = Path(self.directory.name) / "replacement-nvme"
        fresh.mkdir()
        config = {**self.storage.config, "nvme_root": str(fresh), "nvme_mount": str(fresh)}
        atomic_json(fresh / MARKER, {"deployment_id": config["deployment_id"], "role": "nvme"})
        restored = CourseStorage(config, enforce_mounts=False)
        source = sqlite3.connect(f"{(self.catalog_snapshot / 'faculty.sqlite3').as_uri()}?mode=ro", uri=True)
        destination = sqlite3.connect(restored.database)
        try:
            self.assertEqual(source.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            source.backup(destination)
            self.assertEqual(destination.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            destination.close()
            source.close()
        restored_app = create_app(storage=restored)
        return restored, restored_app

    def restore_owners(self, storage):
        with storage.lifecycle_lock():
            for manager in (storage, storage.term_storage, storage.inbox_storage, storage.definition_storage, storage.section_storage):
                for owner in manager.rows():
                    if not owner["storage_snapshot"] or not owner["is_current"]:
                        continue
                    # This explicit recovery decision is only for these synthetic
                    # snapshots. Ordinary reconciliation must never assume that
                    # a missing active working copy may use an older backup.
                    with manager.lock(owner["id"]):
                        manager.verify_snapshot(owner["id"], owner["storage_snapshot"])
                        manager.update(owner["id"], storage_status="restore_pending", storage_hot_required=0)
                    manager.reconcile_course(owner["id"])

    def test_verified_backup_restores_roster_identity_payloads_and_review_gates_on_fresh_disk(self):
        source_catalog = inventory(self.catalog_snapshot)
        restored, app = self.fresh_workspace()
        self.assertNotEqual(restored.nvme, self.storage.nvme)
        self.assertFalse(restored.hot_path(self.course_id).exists())
        self.restore_owners(restored)
        with app.extensions["rosters"].lock(), restored.course_files(self.course_id) as path:
            self.assertEqual(inventory(path), self.course_files)
        self.assertEqual(app.test_client().get(self.roster_route).json, self.roster)
        inbox = app.extensions["inbox"]
        self.assertEqual(inbox.read_artifact(self.job["artifacts"][0]["id"])[1], b"Retained original for restore exercise")
        restored_job = inbox.get(self.job["id"])
        self.assertEqual(restored_job["status"], self.job["status"])
        self.assertTrue(restored_job["instructor_review_required"])
        self.assertEqual(inventory(self.catalog_snapshot), source_catalog)
        # The replacement can itself complete the normal nightly backup.
        self.assertEqual(restored.backup_all()["status"], "complete")

    def test_corrupt_payload_snapshot_is_rejected_before_restoration(self):
        owner = self.storage.row(self.course_id)
        # Deliberate corruption of this test's synthetic snapshot is fault
        # injection, held under the same locks as a normal snapshot reader.
        with self.storage.lifecycle_lock(), self.storage.lock(self.course_id):
            path = self.storage.snapshot_path(self.course_id, owner["storage_snapshot"])
            (path / "data" / "notes.txt").write_bytes(b"Damaged synthetic backup")
        restored, _ = self.fresh_workspace()
        with self.assertRaisesRegex(StorageError, "verification failed"):
            self.restore_owners(restored)
        self.assertFalse(restored.hot_path(self.course_id).exists())
        with self.app.extensions["rosters"].lock(), self.storage.course_files(self.course_id) as path:
            self.assertEqual(inventory(path), self.course_files)

    def test_corrupt_catalog_snapshot_is_rejected_before_opening_a_replacement(self):
        with (self.catalog_snapshot / "faculty.sqlite3").open("ab") as file:
            file.write(b"synthetic corruption")
        with self.assertRaisesRegex(StorageError, "catalog snapshot failed"):
            self.fresh_workspace()
        self.assertFalse((Path(self.directory.name) / "replacement-nvme").exists())
