"""Verified HDD snapshots and an NVMe working copy for current courses.

Saved-offering payloads use ``CourseStorage.course_files``. Canonical term payloads
use the ``TermStorage`` specialization in rosters.py with its own table and paths.
Cross-process locks exclude backups, moves, and other writers. Follow the course /
term / section ownership rules in AGENTS.md; close database connections before
releasing a context. Snapshot directories are immutable.
"""

import ctypes
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from werkzeug.exceptions import Conflict
from course_codes import split_course_code

LOG = logging.getLogger(__name__)
MARKER = ".faculty-storage.json"
MANIFEST = ".manifest.json"
LIFECYCLE_LOCK = "34788c84-daa9-55b8-8748-aad54da0e241"  # the roster lifecycle lock id (AGENTS.md "STORAGE")
PENDING = {"restore_pending", "sync_pending", "archive_pending", "purge_pending"}
# NVMe working copies are hashed on this many threads (hashlib releases the GIL); HDD copies are read one file at a time.
HASHERS = min(8, os.cpu_count() or 1)
# Snapshot retention (requirement, 2026-09-27). Catalog backups: every one for RETAIN_DAYS, then the newest of each ISO week
# until WEEKLY_DAYS (about three months), and the newest of each calendar month for good (UTC dates). Payload snapshots: every
# one the live catalog or a kept catalog backup records, and every one younger than RETAIN_DAYS. A catalog backup restores
# only with the snapshots it records, so the two are pruned together. `manage_storage.py prune` shows the plan; only
# `prune --apply <fingerprint>` of that plan deletes. Names that are not Faculty's own timestamps are never touched.
RETAIN_DAYS = 14
WEEKLY_DAYS = 92
CATALOG_NAME = re.compile(r"\d{8}T\d{12}Z")
SNAPSHOT_NAME = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{8}")
OWNER_TABLES = {"courses": "courses", "terms": "course_terms", "inbox": "inbox_messages",
                "definitions": "course_definitions", "sections": "course_sections", "communications": "comm_bundles",
                "recordings": "recording_bundles"}
_HDD_DIRECTORIES = ("catalog", *OWNER_TABLES, "staging", "locks", "workspace-backups")
_NVME_DIRECTORIES = ("catalog", "locks", *OWNER_TABLES, "staging")
_GUARDED_PATHS = (*OWNER_TABLES, "staging", "locks", "workspace-backups", "catalog", "catalog/faculty.sqlite3")
METADATA_FIELDS = (
    "id", "name", "code", "term", "term_other", "year", "crn", "sections",
    "multiple_sections", "is_current", "version", "created_at", "updated_at", "deleted_at",
    "definition_id", "code_prefix", "code_number",
)


class StorageError(Exception):
    pass


@contextmanager
def exclusive(storage, lock_id, fallback, busy):
    """One round at a time: the storage manager's non-blocking lock (busy -> Conflict), else a flock on `fallback` (tests only)."""
    if storage:
        try:
            with storage.lock(lock_id, blocking=False):
                yield
        except StorageError as error:
            if isinstance(error.__cause__, BlockingIOError):
                raise Conflict(busy) from error
            raise
        return
    descriptor = os.open(fallback, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Conflict(busy) from error
        yield
    finally:
        os.close(descriptor)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def digest_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise StorageError(f"Unsupported file in course data: {path.name}")
        digest = hashlib.sha256()
        # Plain reads of up to 8 MB: few GIL hand-offs per file, so HASHERS threads scale (8.2 GB in 28,000 files hashed
        # in 1.7 s on 8 threads, 3.3 s through a buffered reader; 2026-09-27).
        while block := os.read(descriptor, 8 * 1024 * 1024):
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise StorageError("A course file changed during verification. Its working copy was retained.")
    return {"kind": "file", "size": after.st_size, "sha256": digest.hexdigest()}


def inventory(root, skip_manifest=False, workers=1):
    if root.is_symlink() or not root.is_dir():
        raise StorageError("The course data directory is missing or is a symbolic link.")
    result, files = {}, []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if skip_manifest and relative == MANIFEST:
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                result[relative] = {"kind": "directory"}
            elif stat.S_ISREG(mode):
                files.append((relative, path))
            else:
                raise StorageError(f"Course data contains a link or special file: {relative}. No NVMe data was removed.")
    if workers > 1 and len(files) > 1:
        with ThreadPoolExecutor(workers) as pool:
            result.update(zip((relative for relative, _ in files), pool.map(digest_file, (path for _, path in files))))
    else:
        result.update((relative, digest_file(path)) for relative, path in files)
    return result


def sync_filesystem(anchor):
    """syncfs(2) the filesystem holding the open descriptor `anchor`; False where there is no syncfs.

    Linux 5.8+ reports every writeback error on that filesystem since `anchor` was opened, so one call makes
    everything written after opening it durable, or fails."""
    try:
        syncfs = ctypes.CDLL(None, use_errno=True).syncfs
    except (OSError, AttributeError):
        return False
    if syncfs(anchor) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return True


def copy_file(source, destination):
    """Copy a regular file into a NEW file: never write through an existing name."""
    try:
        reader = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        if os.path.islink(source):
            raise StorageError("A source file changed into a symbolic link while copying.") from error
        raise
    with os.fdopen(reader, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise StorageError(f"Unsupported file in course data: {source.name}")
        writer = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(writer, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            shutil.copyfileobj(handle, output, 1024 * 1024)


def read_manifest_at(path, identifier):
    manifest_path = path / MANIFEST
    if manifest_path.is_symlink():
        raise StorageError("The HDD backup manifest is a symbolic link.")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1 or manifest.get("course_id") != identifier:
        raise StorageError("The HDD backup manifest does not match this course.")
    return manifest


def verify_snapshot_at(path, identifier):
    manifest = read_manifest_at(path, identifier)
    if inventory(path, skip_manifest=True) != manifest["files"]:
        raise StorageError("HDD backup verification failed. The NVMe copy will not be removed.")
    return manifest


def backup_time(name):
    """When a catalog backup or payload snapshot was made, from its name."""
    return datetime.strptime(name[:22], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)


def kept_catalog_backups(names, now):
    """The catalog backups the retention rule keeps: all for RETAIN_DAYS, the newest of each ISO week until WEEKLY_DAYS, and
    the newest of each month at any age. Newest-of-week and newest-of-month are settled once the week or month is over, which
    happens before a backup leaves RETAIN_DAYS, so nothing deleted would be kept by a later run."""
    dated = {name: backup_time(name) for name in names}
    newest = {}
    for name, when in dated.items():
        for bucket in (("week", *when.isocalendar()[:2]), ("month", when.year, when.month)):
            if bucket not in newest or when > dated[newest[bucket]]:
                newest[bucket] = name
    kept = set()
    for name, when in dated.items():
        age = now - when
        if (age < timedelta(days=RETAIN_DAYS) or newest[("month", when.year, when.month)] == name
                or (age < timedelta(days=WEEKLY_DAYS) and newest[("week", *when.isocalendar()[:2])] == name)):
            kept.add(name)
    return kept


def recorded_snapshots(db):
    """(directory, owner id, snapshot) for every snapshot a catalog records, in catalogs of any age."""
    found = set()
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for directory, table in OWNER_TABLES.items():
        if table in tables and "storage_snapshot" in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
            found.update((directory, row[0], row[1]) for row in db.execute(
                f"SELECT id, storage_snapshot FROM {table} WHERE storage_snapshot != ''"))
    return found


def tree_bytes(path):
    return sum((Path(directory) / name).lstat().st_size for directory, _, names in os.walk(path) for name in names)


def copy_inventory(source, destination, entries):
    # One syncfs for the whole copy instead of an fsync per file: copying 30,600 Inbox files to the backup HDD with an fsync
    # each took 20 minutes a night, on one minute of CPU (2026-09-27). Without syncfs every file is fsynced as before.
    anchor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        copied = []
        for relative, info in sorted(entries.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
            path = destination / relative
            if info["kind"] == "directory":
                path.mkdir(mode=0o700, exist_ok=True)
            else:
                copy_file(source / relative, path)
                copied.append(path)
        if not sync_filesystem(anchor):
            for path in copied:
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
    finally:
        os.close(anchor)
    for directory, _, _ in os.walk(destination, topdown=False):
        fsync_dir(directory)


def decode_course(row):
    result = dict(row)
    result["sections"] = json.loads(result["sections"])
    result["code_prefix"], result["code_number"] = split_course_code(result["code"]) or ("", "")
    for field in ("is_current", "multiple_sections", "storage_initialized"):
        result[field] = bool(result[field])
    return result


def live_offerings(db):
    """Every course offering not soft-deleted, decoded, in the catalog's scan order (no ORDER BY, as the callers always read it)."""
    return [decode_course(row) for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL")]


class CourseStorage:
    table = "courses"
    directory = "courses"
    term_storage = None
    inbox_storage = None
    definition_storage = None
    section_storage = None
    communication_storage = None
    recording_storage = None
    hot_states = ("active",)
    stage_archived_writes = False

    def lifecycle_lock(self, blocking=True):
        # Shared by course mutations, roster writes, and cross-owner backups; manage_storage.migrate_catalog takes the id directly.
        return self.lock(LIFECYCLE_LOCK, blocking) if self.term_storage else nullcontext()

    def owner_managers(self):
        """The registered owner storage managers in reconcile and backup order (terms, Inbox, definitions, sections, communications,
        recordings)."""
        return [manager for manager in (self.term_storage, self.inbox_storage, self.definition_storage,
                                        self.section_storage, self.communication_storage, self.recording_storage) if manager]

    def __init__(self, config, enforce_mounts=True):
        self.config = config
        self.hdd = Path(config["hdd_root"])
        self.nvme = Path(config["nvme_root"])
        self.enforce_mounts = enforce_mounts
        if (not self.hdd.is_absolute() or not self.nvme.is_absolute()
                or self.hdd == self.nvme or self.hdd in self.nvme.parents or self.nvme in self.hdd.parents):
            raise StorageError("HDD and NVMe storage must be separate absolute directories.")
        self.working_root = self.nvme if config.get("catalog_on_nvme", False) else self.hdd
        self.database = self.working_root / "catalog" / "faculty.sqlite3"
        self.wake = threading.Event()
        self._thread = None
        self.check_hdd()
        self.check_nvme()
        for root, names in ((self.hdd, _HDD_DIRECTORIES), (self.nvme, _NVME_DIRECTORIES)):
            for name in names:
                path = root / name
                if path.is_symlink():
                    raise StorageError(f"Storage directory cannot be a symbolic link: {path}")
                path.mkdir(mode=0o700, exist_ok=True)
            fsync_dir(root)
        if (config.get("catalog_on_nvme") and not self.database.is_file()
                and (self.hdd / "catalog" / "faculty.sqlite3").exists()):
            raise StorageError("The NVMe catalog is missing. Restore or migrate the catalog before starting Faculty; the retained HDD catalog was not opened as live data.")

    def _guard(self, root, role):
        if root.resolve() != root or not root.is_dir():
            raise StorageError(f"The {role} storage directory is missing or redirected: {root}")
        if self.enforce_mounts:
            mount = Path(self.config[f"{role}_mount"])
            if not mount.is_mount() or (root != mount and mount not in root.parents) or root.stat().st_dev != mount.stat().st_dev:
                raise StorageError(f"The expected {role} drive is not mounted at {mount}. Storage changes are paused.")
        marker = root / MARKER
        if marker.is_symlink():
            raise StorageError("The storage identity marker cannot be a symbolic link.")
        # Every storage lock checks twice, so the parsed marker is kept per file and read again whenever its stat changes (ctime
        # changes with any rewrite, even one that keeps the mtime), and always while it changed in the last two seconds, when two
        # rewrites could share one timestamp.
        known = vars(self).setdefault("_marker_identities", {})
        try:
            status = os.stat(marker)
            key = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)
            cached = known.get(str(marker))
            if cached and cached[0] == key:
                identity = cached[1]
            else:
                identity = json.loads(marker.read_text())
                if time.time_ns() - status.st_ctime_ns > 2_000_000_000:
                    known[str(marker)] = (key, identity)
        except (OSError, ValueError) as error:
            raise StorageError(f"Cannot verify the {role} drive identity at {root}.") from error
        if identity != {"deployment_id": self.config["deployment_id"], "role": role}:
            raise StorageError(f"The {role} drive does not match this Faculty workspace.")
        # The root resolved to itself above, so a path under it is redirected exactly when one of its own components is a
        # symbolic link: one lstat per component instead of a realpath walk from /. A missing component ends the walk, as it
        # ends resolve()'s: nothing below it can be a link. Where there is a link, resolve() still decides, so a loop of links
        # raises its RuntimeError as it did.
        for name in _GUARDED_PATHS:
            path, walked = root / name, str(root)
            for part in name.split("/"):
                walked = os.path.join(walked, part)
                try:
                    mode = os.lstat(walked).st_mode
                except OSError:
                    break
                if stat.S_ISLNK(mode):
                    if path.resolve() != path:
                        raise StorageError(f"A storage path is redirected: {path}")
                    break

    def check_hdd(self):
        self._guard(self.hdd, "hdd")

    def check_nvme(self):
        self._guard(self.nvme, "nvme")
        if self.enforce_mounts and self.nvme.stat().st_dev == self.hdd.stat().st_dev:
            raise StorageError("The NVMe and HDD directories must be on different drives.")

    @staticmethod
    def _identifier(identifier):
        if str(uuid.UUID(identifier)) != identifier:
            raise StorageError("Invalid course storage identifier.")
        return identifier

    def hot_path(self, identifier):
        self.check_nvme()
        path = self.nvme / self.directory / self._identifier(identifier)
        if path.resolve() != path:
            raise StorageError("The NVMe course path is redirected. No data was removed.")
        return path

    @contextmanager
    def lock(self, identifier, blocking=True):
        self.check_nvme() if self.working_root == self.nvme else self.check_hdd()
        name = self._identifier(identifier) if identifier != "nightly-backup" else identifier
        descriptor = os.open(self.working_root / "locks" / f"{name}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as error:
                raise StorageError("This course is being backed up or moved. Please try again when it finishes.") from error
            self.check_nvme() if self.working_root == self.nvme else self.check_hdd()
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def connect(self):
        self.check_nvme() if self.working_root == self.nvme else self.check_hdd()
        if not self.database.is_file():
            raise StorageError("The live workspace catalog is missing. Refusing to create an empty replacement.")
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def row(self, identifier):
        with self.connect() as db:
            row = db.execute(f"SELECT * FROM {self.table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise StorageError("The course no longer exists.")
        return decode_course(row)

    def rows(self):
        with self.connect() as db:
            return [decode_course(row) for row in db.execute(f"SELECT * FROM {self.table} ORDER BY created_at, id")]

    def update(self, identifier, **values):
        allowed = {"storage_status", "storage_snapshot", "storage_verified_at", "storage_initialized", "storage_hot_required", "storage_error"}
        if not set(values) <= allowed:
            raise StorageError("Invalid storage update.")
        with self.connect() as db:
            db.execute(f"UPDATE {self.table} SET {', '.join(key + '=?' for key in values)} WHERE id=?",
                       (*values.values(), identifier))

    @staticmethod
    def metadata(course):
        return {key: course[key] for key in METADATA_FIELDS if key in course}

    def snapshot_path(self, identifier, snapshot):
        self.check_hdd()
        if not snapshot or Path(snapshot).name != snapshot or snapshot in (".", ".."):
            raise StorageError("No valid HDD backup is recorded for this course.")
        path = self.hdd / self.directory / self._identifier(identifier) / "snapshots" / snapshot
        if path.resolve() != path:
            raise StorageError("A backup path is redirected through a symbolic link.")
        return path

    def read_manifest(self, identifier, snapshot):
        """A snapshot's manifest, checked for its owner; its files are not read (verify_snapshot reads them)."""
        return read_manifest_at(self.snapshot_path(identifier, snapshot), identifier)

    def verify_snapshot(self, identifier, snapshot):
        return verify_snapshot_at(self.snapshot_path(identifier, snapshot), identifier)

    def hashers(self, path):
        return HASHERS if path and self.nvme in Path(path).parents else 1

    def check_source(self, course, source, files):
        """Owner-specific checks of a source against the catalog before it is backed up; `files` is its inventory."""

    @staticmethod
    def payload_sha256(source, files, relative):
        """sha256 of data/<relative>, from the inventory `files` taken of `source` under the same locks when it lists it."""
        entry = files.get(f"data/{relative}")
        if entry and entry.get("kind") == "file":
            return entry["sha256"]
        return digest_file(source / "data" / relative)["sha256"]

    def changed(self, source, files):
        try:
            return inventory(source, skip_manifest=True, workers=self.hashers(source)) != files
        except (OSError, StorageError):
            return True

    def snapshot(self, course, source=None, source_files=None):
        """Publish only a flushed, read-back-verified snapshot of a stable source.

        `source_files` is the caller's own inventory of `source`, when it has one; the source is inventoried again after
        copying, so a source that changed since is refused."""
        self.check_hdd()
        identifier = self._identifier(course["id"])
        if source and source_files is None:
            source_files = inventory(source, skip_manifest=True, workers=self.hashers(source))
        source_files = source_files if source else {}
        self.check_source(course, source, source_files)
        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
        stage = self.hdd / "staging" / f"{identifier}-{uuid.uuid4().hex}"
        stage.mkdir(mode=0o700)
        if source:
            try:
                copy_inventory(source, stage, source_files)
                failure = None
            except (OSError, StorageError) as error:
                failure = error
            if self.changed(source, source_files):
                # A copy of a source that was changing says nothing about the HDD, so it is not kept; a failed copy of a
                # stable source is kept for inspection.
                shutil.rmtree(stage, ignore_errors=True)
                raise StorageError("The course changed while copying to the HDD. Its working data was retained.") from failure
            if failure:
                raise failure
            if inventory(stage) != source_files:
                raise StorageError("The HDD copy failed verification. Its working data was retained.")
        else:
            (stage / "data").mkdir(mode=0o700)
            fsync_dir(stage / "data")
        atomic_json(stage / "course.json", self.metadata(course))
        files = inventory(stage)
        manifest = {"schema_version": 1, "course_id": identifier, "created_at": timestamp(),
                    "files": files, "source_files": source_files}
        atomic_json(stage / MANIFEST, manifest)
        parent = self.hdd / self.directory / identifier / "snapshots"
        if parent.resolve() != parent:
            raise StorageError("The course backup directory is redirected.")
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        for directory in (parent, parent.parent, parent.parent.parent):
            fsync_dir(directory)
        self.check_hdd()
        os.rename(stage, parent / snapshot_id)
        fsync_dir(parent)
        self.verify_snapshot(identifier, snapshot_id)
        return snapshot_id

    def _restore(self, course):
        target = self.hot_path(course["id"])
        snapshot = course["storage_snapshot"]
        if snapshot:
            manifest = self.verify_snapshot(course["id"], snapshot)
        elif course["storage_initialized"]:
            raise StorageError("This course has no verified HDD backup to restore. No replacement data was created.")
        else:
            manifest = None
        if target.exists() or target.is_symlink():
            # Resume a restore interrupted after its atomic rename, never replace an unknown tree.
            expected = dict(manifest["files"]) if manifest else {"data": {"kind": "directory"}}
            existing = inventory(target)
            expected.pop("course.json", None)
            existing.pop("course.json", None)
            if existing != expected:
                raise StorageError("An unexpected NVMe working directory exists. It was left untouched.")
        else:
            # A fixed private staging path makes interrupted restores recoverable.
            # This contains only expendable copies; the verified HDD source is retained.
            stage = self.nvme / "staging" / course["id"]
            if stage.resolve() != stage:
                raise StorageError("The restore staging directory is redirected.")
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(mode=0o700)
            if manifest:
                source = self.snapshot_path(course["id"], snapshot)
                copy_inventory(source, stage, manifest["files"])
                if inventory(stage) != manifest["files"]:
                    raise StorageError("The restored NVMe copy failed verification. The HDD backup was retained.")
            else:
                (stage / "data").mkdir(mode=0o700)
            atomic_json(stage / "course.json", self.metadata(course))
            fsync_dir(stage / "data")
            self.check_nvme()
            os.rename(stage, target)
            fsync_dir(target.parent)
        atomic_json(target / "course.json", self.metadata(course))
        self.update(course["id"], storage_status="active", storage_initialized=1, storage_hot_required=1, storage_error="")

    def _purge(self, course):
        """The committed purge_pending state permits recovery from partial deletion."""
        manifest = self.verify_snapshot(course["id"], course["storage_snapshot"])
        target = self.hot_path(course["id"])
        if target.exists() or target.is_symlink():
            remaining = inventory(target)
            if any(manifest["source_files"].get(name) != value for name, value in remaining.items()):
                raise StorageError("NVMe data changed after the final backup. Purging stopped; the remaining data was retained.")
            self.check_hdd()
            self.check_nvme()
            shutil.rmtree(target)
            fsync_dir(target.parent)
        self.update(course["id"], storage_status="deleted" if course["deleted_at"] else "archived", storage_hot_required=0, storage_error="")

    def reconcile_course(self, identifier):
        with self.lock(identifier):
            course = self.row(identifier)
            status = course["storage_status"]
            if status == "restore_pending":
                self._restore(course)
            elif status in ("sync_pending", "active"):
                target = self.hot_path(identifier)
                if not target.is_dir() or target.is_symlink():
                    raise StorageError("The NVMe working copy is missing. The HDD backup has not been changed.")
                atomic_json(target / "course.json", self.metadata(course))
                self.update(identifier, storage_status="active", storage_error="")
            elif status == "archive_pending":
                source = self.hot_path(identifier)
                if not source.exists() and not source.is_symlink():
                    if course["storage_hot_required"]:
                        raise StorageError("The NVMe working copy is missing. Archiving stopped to avoid accepting an older backup as final.")
                    elif course["storage_snapshot"]:
                        self.verify_snapshot(identifier, course["storage_snapshot"])
                        source = self.snapshot_path(identifier, course["storage_snapshot"])
                    elif course["storage_initialized"]:
                        raise StorageError("The course working copy and its backup are missing. Archiving was stopped.")
                    else:
                        source = None
                snapshot = self.snapshot(course, source)
                # Persist the exact verified backup before any NVMe deletion begins.
                self.update(identifier, storage_snapshot=snapshot, storage_verified_at=timestamp(),
                            storage_initialized=1, storage_status="purge_pending", storage_error="")
                self._purge(self.row(identifier))
            elif status == "purge_pending":
                self._purge(course)

    def reconcile_all(self):
        with self.lifecycle_lock():
            return self._reconcile_all()

    def _reconcile_all(self):
        failures = []
        for course in self.rows():
            if course["storage_status"] not in PENDING:
                continue
            try:
                self.reconcile_course(course["id"])
            except Exception as error:
                LOG.exception("Course storage operation failed for %s", course["id"])
                failures.append(course["id"])
                try:
                    self.update(course["id"], storage_error=str(error))
                except Exception:
                    LOG.exception("Could not persist the storage failure")
        for manager in self.owner_managers():
            failures.extend(manager.reconcile_all())
        return failures

    def start_worker(self):
        if self._thread:
            return
        def work():
            while True:
                try:
                    self.reconcile_all()
                except Exception:
                    LOG.exception("Course storage worker is unavailable")
                self.wake.wait(60)
                self.wake.clear()
        self._thread = threading.Thread(target=work, name="faculty-course-storage", daemon=True)
        self._thread.start()

    @contextmanager
    def course_files(self, identifier, write=False):
        """Module integration: yield this course's data directory under its lock.

        Saved-offering and term writes to archived courses stage on HDD.
        Inbox overrides stage_archived_writes to restore to NVMe and defer HDD
        publication to the nightly coordinator.
        A read context must not modify files. Close all handles before returning.
        """
        with self.lock(identifier):
            course = self.row(identifier)
            if course["deleted_at"] or course["storage_status"] not in (*self.hot_states, "archived"):
                raise StorageError("Course storage is not ready. Wait for its pending move to finish.")
            if course["storage_status"] == "archived" and write and self.stage_archived_writes:
                self._restore(course)
                course = self.row(identifier)
            if course["storage_status"] in self.hot_states:
                path = self.hot_path(identifier) / "data"
                if not path.is_dir() or path.is_symlink():
                    raise StorageError("The course data directory is missing or redirected.")
                yield path
            else:
                manifest = self.verify_snapshot(identifier, course["storage_snapshot"])
                source = self.snapshot_path(identifier, course["storage_snapshot"])
                if not write:
                    yield source / "data"
                else:
                    stage = self.hdd / "staging" / f"edit-{identifier}-{uuid.uuid4().hex}"
                    stage.mkdir(mode=0o700)
                    copy_inventory(source, stage, manifest["files"])
                    if inventory(stage) != manifest["files"]:
                        raise StorageError("The HDD working copy failed verification.")
                    yield stage / "data"
                    snapshot = self.snapshot(course, stage)
                    self.update(identifier, storage_snapshot=snapshot, storage_verified_at=timestamp(), storage_error="")
                    shutil.rmtree(stage)

    def backup_catalog(self):
        return self.publish_catalog(*self.capture_catalog())

    def capture_catalog(self):
        """A consistent copy of the live catalog on NVMe staging: the only catalog step the nightly backup holds locks for."""
        self.check_nvme()
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        capture = self.nvme / "staging" / f"catalog-backup-{uuid.uuid4().hex}.sqlite3"
        try:
            target = sqlite3.connect(capture)
            try:
                with self.connect() as source:
                    source.backup(target)
                # Publish a standalone database with no WAL sidecar dependency.
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                target.close()
        except BaseException:
            capture.unlink(missing_ok=True)
            raise
        return name, capture

    def publish_catalog(self, name, capture):
        """Integrity-check a captured catalog and publish a read-back-verified copy on the HDD; the capture is removed."""
        try:
            self.check_hdd()
            check = sqlite3.connect(f"{capture.as_uri()}?mode=ro", uri=True)
            try:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise StorageError("Workspace database backup failed its integrity check.")
            finally:
                check.close()
            expected = digest_file(capture)
            stage = self.hdd / "staging" / f"catalog-{uuid.uuid4().hex}"
            stage.mkdir(mode=0o700)
            destination = stage / "faculty.sqlite3"
            copy_file(capture, destination)
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            if digest_file(destination) != expected:
                raise StorageError("Workspace backup read-back verification failed.")
            atomic_json(stage / MANIFEST, {"created_at": timestamp(), "files": {"faculty.sqlite3": expected}})
            final = self.hdd / "workspace-backups" / name
            os.rename(stage, final)
            fsync_dir(final.parent)
            if digest_file(final / "faculty.sqlite3") != expected:
                raise StorageError("Workspace backup read-back verification failed.")
            return name
        finally:
            capture.unlink(missing_ok=True)

    def backup_eligible(self, course):
        return bool(course["is_current"] and not course["deleted_at"] and course["storage_status"])

    def finish_backup(self, course):
        """Owner-specific retention after a verified nightly snapshot."""

    def kept_snapshot(self, owner, files, damaged):
        """The owner's recorded snapshot when it holds exactly `files` and verifies now, else None.

        A recorded snapshot that cannot be read or fails verification is never reused; it is reported in `damaged`."""
        name = owner["storage_snapshot"]
        if not name:
            return None
        try:
            manifest = self.read_manifest(owner["id"], name)
            if manifest.get("files") != files or manifest.get("source_files") != files:
                return None
            self.verify_snapshot(owner["id"], name)
        except (OSError, ValueError, StorageError) as error:
            LOG.error("The recorded HDD backup %s of %s failed verification and was not reused", name, owner["id"], exc_info=True)
            damaged[owner["id"], name] = (f"{owner['id']}: its recorded HDD backup {name} failed verification and was not reused "
                                          f"({error})")
            return None
        return name

    def prepare_backup(self, owner, damaged):
        """Ahead of the nightly capture and holding no lifecycle lock: a verified snapshot equal to the working copy.

        The recorded snapshot when the working copy still matches it (read back from the HDD now), else a new one,
        published but not recorded. The owner lock is held only to refresh course.json. capture_backup decides."""
        with self.lock(owner["id"]):
            current = self.row(owner["id"])
            if not self.backup_eligible(current) or current["storage_status"] not in self.hot_states:
                return None
            source = self.hot_path(current["id"])
            atomic_json(source / "course.json", self.metadata(current))
        files = inventory(source, skip_manifest=True, workers=HASHERS)
        snapshot = self.kept_snapshot(current, files, damaged) or self.snapshot(current, source, files)
        return {"snapshot": snapshot, "files": files}

    def capture_backup(self, current, source, prepared, damaged):
        """Under the lifecycle and owner locks: the verified snapshot holding exactly the working copy now, and how.

        The working copy is hashed in full; a prepared snapshot counts only if it holds exactly that. Returns
        (snapshot, "unchanged" | "copied_ahead" | "copied_while_locked")."""
        files = inventory(source, skip_manifest=True, workers=HASHERS)
        self.check_source(current, source, files)
        if prepared and prepared["files"] == files:
            try:
                manifest = self.read_manifest(current["id"], prepared["snapshot"])
            except (OSError, ValueError, StorageError):
                LOG.warning("The snapshot prepared for %s cannot be read; copying it now", current["id"], exc_info=True)
                manifest = {}
            if manifest.get("files") == files and manifest.get("source_files") == files:
                return prepared["snapshot"], "unchanged" if prepared["snapshot"] == current["storage_snapshot"] else "copied_ahead"
        if not prepared:
            kept = self.kept_snapshot(current, files, damaged)
            if kept:
                return kept, "unchanged"
        return self.snapshot(current, source, files), "copied_while_locked"

    def backup_all(self):
        """The nightly backup: verify ahead, then hold the lifecycle lock only for a consistent capture.

        Every current working copy is compared with its recorded snapshot and either that snapshot is read back from the
        HDD or a new one is copied and verified, with no lifecycle lock held. Then, under the lifecycle lock, each working
        copy is hashed again; a snapshot is recorded only if it holds exactly that, otherwise one is copied there and
        then. The catalog is copied to NVMe inside the same hold and verified and published on the HDD after it."""
        with self.lock("nightly-backup", blocking=False):
            state = {"status": "running", "started_at": timestamp(), "finished_at": None, "backed_up": 0,
                     "unchanged": 0, "copied_ahead": 0, "copied_while_locked": 0, "locked_seconds": None, "errors": []}
            atomic_json(self.hdd / "backup-status.json", state)
            managers = [self, *self.owner_managers()]
            try:
                for stale in (self.nvme / "staging").glob("catalog-backup-*"):
                    stale.unlink(missing_ok=True)  # An interrupted run's catalog capture; this run holds the nightly lock.
                failed = self.reconcile_all()
                state["errors"].extend(f"Pending storage operation failed for {identifier}" for identifier in failed)
                prepared, damaged = {}, {}
                for manager in managers:
                    for course in manager.rows():
                        if manager.backup_eligible(course):
                            try:
                                prepared[manager.directory, course["id"]] = manager.prepare_backup(course, damaged)
                            except Exception:
                                LOG.warning("Nightly backup of %s will be copied under its locks", course["id"], exc_info=True)
                with self.lifecycle_lock():
                    locked = time.monotonic()
                    for manager in managers:
                        for course in manager.rows():
                            if not manager.backup_eligible(course):
                                continue
                            try:
                                with manager.lock(course["id"]):
                                    current = manager.row(course["id"])
                                    if not manager.backup_eligible(current):
                                        continue
                                    if current["storage_status"] not in manager.hot_states:
                                        raise StorageError("The current course's NVMe storage is not ready.")
                                    source = manager.hot_path(current["id"])
                                    atomic_json(source / "course.json", manager.metadata(current))
                                    snapshot, how = manager.capture_backup(current, source, prepared.get((manager.directory, current["id"])), damaged)
                                    manager.update(current["id"], storage_snapshot=snapshot, storage_verified_at=timestamp(), storage_error="")
                                    manager.finish_backup(manager.row(current["id"]))
                                    state["backed_up"] += 1
                                    state[how] += 1
                            except Exception as error:
                                LOG.exception("Nightly backup failed for course %s", course["id"])
                                state["errors"].append(f"{course['id']}: {error}")
                                manager.update(course["id"], storage_error=str(error))
                    capture = self.capture_catalog()
                    state["locked_seconds"] = round(time.monotonic() - locked, 3)
                state["errors"].extend(damaged.values())
                state["catalog_snapshot"] = self.publish_catalog(*capture)
            except Exception as error:
                state["errors"].append(str(error))
            state["status"] = "failed" if state["errors"] else "complete"
            state["finished_at"] = timestamp()
            self.check_hdd()
            atomic_json(self.hdd / "backup-status.json", state)
            if state["errors"]:
                raise StorageError("Nightly backup needs attention: " + "; ".join(state["errors"]))
            return state

    def status(self):
        self.check_hdd()
        path = self.hdd / "backup-status.json"
        backup = json.loads(path.read_text()) if path.exists() else {"status": "not_run"}
        if backup["status"] == "running":
            try:
                with self.lock("nightly-backup", blocking=False):
                    backup = {**backup, "status": "interrupted"}
            except StorageError:
                pass
        courses = [course for manager in [self, *self.owner_managers()] for course in manager.rows()]
        return {"enabled": True, "backup_time": "4:00 am Central time", "last_backup": backup,
                "pending_count": sum(course["storage_status"] in PENDING for course in courses),
                "issues": [{"course_id": course["id"], "name": course["name"], "message": course["storage_error"]}
                           for course in courses if course["storage_error"]]}

    def retention_plan(self, now=None):
        """What the retention rule would delete now (see RETAIN_DAYS). Reads only; deletes nothing.

        A kept catalog backup that cannot be read stops the plan: the snapshots it records would be unknown."""
        now = now or datetime.now(timezone.utc)
        self.check_hdd()
        backups = self.hdd / "workspace-backups"
        names = sorted(path.name for path in backups.iterdir()
                       if CATALOG_NAME.fullmatch(path.name) and path.is_dir() and not path.is_symlink())
        kept = kept_catalog_backups(names, now)
        with self.connect() as db:
            needed = recorded_snapshots(db)
        for name in sorted(kept):
            try:
                catalog = sqlite3.connect(f"{(backups / name / 'faculty.sqlite3').as_uri()}?mode=ro&immutable=1", uri=True)
                try:
                    needed |= recorded_snapshots(catalog)
                finally:
                    catalog.close()
            except sqlite3.Error as error:
                raise StorageError(f"The kept catalog backup {name} cannot be read, so the snapshots it records are unknown. "
                                   "Nothing was pruned.") from error
        snapshots, kept_snapshots = [], 0
        for directory in OWNER_TABLES:
            for owner in sorted((self.hdd / directory).iterdir()):
                parent = owner / "snapshots"
                try:
                    self._identifier(owner.name)
                except (ValueError, StorageError):
                    continue
                if owner.is_symlink() or parent.is_symlink() or not parent.is_dir():
                    continue
                for path in sorted(parent.iterdir()):
                    if not SNAPSHOT_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                        continue
                    if (directory, owner.name, path.name) in needed or now - backup_time(path.name) < timedelta(days=RETAIN_DAYS):
                        kept_snapshots += 1
                    else:
                        snapshots.append({"kind": directory, "owner": owner.name, "snapshot": path.name, "bytes": tree_bytes(path)})
        catalogs = [{"name": name, "bytes": tree_bytes(backups / name)} for name in names if name not in kept]
        key = json.dumps({"catalog_backups": [item["name"] for item in catalogs],
                          "snapshots": [[item["kind"], item["owner"], item["snapshot"]] for item in snapshots]}, sort_keys=True)
        return {"rule": {"retain_days": RETAIN_DAYS, "weekly_days": WEEKLY_DAYS, "evaluated_at": now.isoformat()},
                "catalog_backups": {"kept": len(kept), "delete": catalogs},
                "snapshots": {"kept": kept_snapshots, "delete": snapshots},
                "bytes": sum(item["bytes"] for item in catalogs + snapshots),
                "fingerprint": hashlib.sha256(key.encode()).hexdigest()}

    def retention_log(self, entry):
        """Append one line to the HDD's record of what retention planned and deleted."""
        descriptor = os.open(self.hdd / "retention-log.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def prune(self, fingerprint, now=None):
        """Delete exactly the plan retention_plan showed under `fingerprint`, only after a complete nightly backup.

        Catalog backups go first (they record snapshots, never the other way round), once the newest one reads back. An
        owner's snapshots go under its owner lock, once the snapshot the live catalog records for it reads back; an owner
        whose recorded snapshot does not is skipped and reported. Every run is written to retention-log.jsonl."""
        with self.lock("nightly-backup", blocking=False):
            self.check_hdd()
            status = self.hdd / "backup-status.json"
            last = json.loads(status.read_text()).get("status") if status.exists() else "not_run"
            if last != "complete":
                raise StorageError(f"Snapshots are pruned only after a complete nightly backup; the last one is {last}. Nothing was deleted.")
            plan = self.retention_plan(now)
            if plan["fingerprint"] != fingerprint:
                raise StorageError("The retention plan changed since it was shown. Nothing was deleted; show the plan again and apply its fingerprint.")
            self.retention_log({"event": "planned", "at": timestamp(), **plan})
            result = {"fingerprint": fingerprint, "deleted_catalog_backups": [], "deleted_snapshots": [], "bytes": 0, "errors": []}
            backups = self.hdd / "workspace-backups"
            doomed = {item["name"] for item in plan["catalog_backups"]["delete"]}
            if doomed:
                try:
                    newest = max(path.name for path in backups.iterdir() if CATALOG_NAME.fullmatch(path.name) and path.name not in doomed)
                    manifest = json.loads((backups / newest / MANIFEST).read_text())
                    if digest_file(backups / newest / "faculty.sqlite3") != manifest["files"]["faculty.sqlite3"]:
                        raise StorageError(f"The newest catalog backup {newest} failed verification.")
                    for item in plan["catalog_backups"]["delete"]:
                        path = backups / item["name"]
                        if path.resolve() != path:
                            raise StorageError(f"The catalog backup {item['name']} is redirected.")
                        shutil.rmtree(path)
                        fsync_dir(backups)
                        result["deleted_catalog_backups"].append(item["name"])
                        result["bytes"] += item["bytes"]
                except Exception as error:
                    LOG.exception("Catalog backup retention stopped")
                    result["errors"].append(f"catalog backups: {error}")
                    # STORAGE: an undeleted catalog still needs every snapshot it records, including ones in this plan.
                    self.retention_log({"event": "done", "at": timestamp(), **result})
                    return result
            owners = {}
            for item in plan["snapshots"]["delete"]:
                owners.setdefault((item["kind"], item["owner"]), []).append(item)
            for (kind, owner), items in owners.items():
                try:
                    with self.lock(owner):
                        with self.connect() as db:
                            row = db.execute(f"SELECT storage_snapshot FROM {OWNER_TABLES[kind]} WHERE id=?", (owner,)).fetchone()
                        recorded = row[0] if row else ""
                        parent = self.hdd / kind / owner / "snapshots"
                        if parent.resolve() != parent:
                            raise StorageError("The backup directory is redirected.")
                        if recorded:
                            verify_snapshot_at(parent / recorded, owner)
                        for item in items:
                            if item["snapshot"] == recorded:
                                continue
                            shutil.rmtree(parent / item["snapshot"])
                            fsync_dir(parent)
                            result["deleted_snapshots"].append(item)
                            result["bytes"] += item["bytes"]
                except Exception as error:
                    LOG.exception("Snapshot retention skipped %s/%s", kind, owner)
                    result["errors"].append(f"{kind}/{owner}: {error}")
            self.retention_log({"event": "done", "at": timestamp(), **result})
            return result
