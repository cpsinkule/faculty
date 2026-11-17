"""Provision course storage, run backups, and inspect verified snapshots."""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from app import ROOT, create_app, initialize
from course_storage import CourseStorage, LIFECYCLE_LOCK, StorageError, MARKER, atomic_json, fsync_dir, digest_file


def migrate_catalog(config_path, enforce_mounts=True):
    """Offline migration. Stop all Faculty services before moving lock domains.

    SQLite backup captures WAL transactions. Keep the old HDD catalog untouched;
    publish the new configuration only after read-back and logical verification.
    """
    config = json.loads(config_path.read_text())
    if config.get("catalog_on_nvme"):
        storage = CourseStorage(config, enforce_mounts)
        return {"status": "already_migrated", "catalog": str(storage.database)}
    old = CourseStorage(config, enforce_mounts)
    destination = old.nvme / "catalog" / "faculty.sqlite3"
    # `old` has no term_storage, so lifecycle_lock() would be a nullcontext; take the id directly to exclude the app's roster and course writers.
    with old.lock("nightly-backup", blocking=False), old.lock(LIFECYCLE_LOCK, blocking=False):
        if not old.database.is_file():
            raise StorageError("The original catalog is missing. Migration was stopped.")
        stage = old.nvme / "staging" / f"catalog-migration-{uuid.uuid4().hex}.sqlite3"
        source = sqlite3.connect(f"{old.database.as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(stage)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StorageError("The copied NVMe catalog failed its integrity check.")
            if list(source.iterdump()) != list(target.iterdump()):
                raise StorageError("The copied NVMe catalog does not match the original.")
        finally:
            target.close()
            source.close()
        os.chmod(stage, 0o600)
        with stage.open("rb") as handle:
            os.fsync(handle.fileno())
        expected = digest_file(stage)
        if destination.exists():
            # Resume only an identical copy left before config activation.
            if digest_file(destination) != expected:
                raise StorageError("An existing NVMe catalog differs from the source; neither copy was replaced.")
            stage.unlink()
        else:
            os.rename(stage, destination)
            fsync_dir(destination.parent)
        if digest_file(destination) != expected:
            raise StorageError("The NVMe catalog failed read-back verification.")
        atomic_json(config_path, {**config, "catalog_on_nvme": True})
    return {"status": "migrated", "catalog": str(destination), "legacy_catalog_retained": str(old.database)}


def provision(config_path, source):
    """Run while Faculty is stopped. Never replace an existing catalog or marker."""
    if config_path.exists():
        raise StorageError("Storage is already configured. Use status, backup, or verify.")
    pending = config_path.with_name(".faculty-storage-setup.json")
    config = json.loads(pending.read_text()) if pending.exists() else {
        "deployment_id": str(uuid.uuid4()),
        "hdd_root": "/mnt/bulkdata2/Teaching", "hdd_mount": "/mnt/bulkdata2",
        "nvme_root": "/mnt/nvme/Teaching", "nvme_mount": "/mnt/nvme",
        "catalog_on_nvme": True,
    }
    for role in ("hdd", "nvme"):
        root, mount = Path(config[f"{role}_root"]), Path(config[f"{role}_mount"])
        if not mount.is_mount() or not root.is_dir() or root.resolve() != root or root.stat().st_dev != mount.stat().st_dev:
            raise StorageError(f"Create the dedicated directory on the mounted drive first: {root}")
        marker = root / MARKER
        if marker.exists():
            if not pending.exists() or marker.is_symlink() or json.loads(marker.read_text()) != {
                "deployment_id": config["deployment_id"], "role": role,
            }:
                raise StorageError(f"Existing storage identity at {root}; it was not overwritten.")
        elif any(root.iterdir()):
            raise StorageError(f"The new storage directory must be empty: {root}")
    if not pending.exists():
        atomic_json(pending, config)
    for role in ("hdd", "nvme"):
        root = Path(config[f"{role}_root"])
        if not (root / MARKER).exists():
            atomic_json(root / MARKER, {"deployment_id": config["deployment_id"], "role": role})
    storage = CourseStorage(config)
    if not storage.database.exists() and source.exists():
        stage = storage.working_root / "staging" / f"migration-{uuid.uuid4().hex}.sqlite3"
        original = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(stage)
        try:
            original.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StorageError("The migrated catalog did not pass its integrity check.")
        finally:
            original.close()
            target.close()
        os.chmod(stage, 0o600)
        with stage.open("rb") as handle:
            os.fsync(handle.fileno())
        os.rename(stage, storage.database)
        fsync_dir(storage.database.parent)
        storage.backup_catalog()
    initialize(storage.database)
    create_app(storage=storage)
    storage.backup_all()
    # Activate only after migration and initial backups have succeeded.
    atomic_json(config_path, config)
    pending.unlink()
    fsync_dir(pending.parent)
    return storage.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "migrate-catalog", "backup", "reconcile", "status", "verify", "prune"))
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("FACULTY_STORAGE_CONFIG", ROOT / ".faculty-storage.json")))
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "faculty.sqlite3", help="Original catalog for init; stop the app first.")
    parser.add_argument("--apply", metavar="FINGERPRINT",
                        help="prune: delete exactly the plan that `prune` (a dry run, which deletes nothing) showed with this fingerprint")
    args = parser.parse_args()
    if args.apply and args.command != "prune":
        parser.error("--apply is for prune")
    try:
        if args.command == "init":
            result = provision(args.config, args.source)
        elif args.command == "migrate-catalog":
            result = migrate_catalog(args.config)
        else:
            if not args.config.is_file():
                raise StorageError("Course storage is not configured. Run init while Faculty is stopped.")
            storage = CourseStorage(json.loads(args.config.read_text()))
            if args.command == "prune":
                # Reads the catalog and backups only; the app is not started, so nothing is migrated or reconciled.
                result = storage.prune(args.apply) if args.apply else storage.retention_plan()
                print(json.dumps(result, indent=2))
                return 1 if result.get("errors") else 0
            create_app(storage=storage)
            if args.command == "backup":
                result = storage.backup_all()
            elif args.command == "reconcile":
                failures = storage.reconcile_all()
                if failures:
                    raise StorageError(f"Storage operations need attention: {', '.join(failures)}")
                result = storage.status()
            elif args.command == "verify":
                verified = []
                with storage.lifecycle_lock():
                    for manager in [storage, *storage.owner_managers()]:
                        for course in manager.rows():
                            if not course["storage_status"]:
                                continue
                            with manager.lock(course["id"]):
                                current = manager.row(course["id"])
                                manager.verify_snapshot(current["id"], current["storage_snapshot"])
                                verified.append(current["id"])
                result = {"verified_courses": verified}
            else:
                result = storage.status()
        print(json.dumps(result, indent=2))
    except (StorageError, OSError, ValueError, sqlite3.Error) as error:
        print(f"Storage operation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
