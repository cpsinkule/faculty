"""Where the communications record keeps originals (AGENTS.md "Communications record").

A BUNDLE is the files one capture kept (the Gmail messages it copied as Gmail holds them, the Canvas attachments it downloaded):
written into NVMe staging as they arrive, then published at once and never changed. It is an owner of the verified storage engine
(`comm_bundles`, directory `communications/`), never current, so it follows the Inbox's lifecycle for non-current payloads: staged
on NVMe until the nightly coordinator has backed it up and verified the copy, then kept on the HDD alone and read from there.
`comm_files` names each file of a bundle with its checksum; every read checks it again, and the nightly backup checks every file of
a bundle against the catalog before accepting its copy.

Without configured storage (tests, a development copy) bundles are folders beside the catalog, `communications/<bundle>/data`.
"""
import hashlib
import os
import shutil
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from course_storage import CourseStorage, PENDING, StorageError, atomic_json, digest_file, fsync_dir, timestamp
from rosters import connect
from teach_inbox.storage import InboxStorage

BUNDLE_BYTES = 256 * 1024 * 1024   # a capture seals its bundle and starts another beyond this
BUNDLE_FILES = 500
ORPHAN_AGE = 3600                  # an unrecorded bundle folder older than this was left by an interrupted capture
EXTENSIONS = {"message/rfc822": ".eml", "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
              "image/heic": ".heic", "text/plain": ".txt", "text/html": ".html", "text/csv": ".csv",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx", "application/zip": ".zip"}


def sha(content):
    return hashlib.sha256(content).hexdigest()


class CommunicationStorage(InboxStorage):
    """Bundles of kept originals on the verified storage engine: never current, never written again once published."""
    table = "comm_bundles"
    directory = "communications"
    hot_states = ("active", "staged")
    stage_archived_writes = False
    metadata = staticmethod(CourseStorage.metadata)

    @staticmethod
    def _owner(row):
        found = dict(row)
        found.update(name=f"Communications · {found['created_at'][:19]}", code="", term="", term_other="", year=None, sections=[],
                     multiple_sections=False, is_current=False, deleted_at=None, version=1, updated_at=found["created_at"])
        return found

    def row(self, identifier):
        with connect(self.database) as db:
            row = db.execute("SELECT * FROM comm_bundles WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise StorageError("Communication bundle not found.")
        return self._owner(row)

    def rows(self):
        with connect(self.database) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='comm_bundles'").fetchone():
                return []
            return [self._owner(row) for row in db.execute("SELECT * FROM comm_bundles WHERE storage_status!='' ORDER BY created_at, id")]

    def check_source(self, owner, source, files):
        if source is None:
            raise StorageError("A communication bundle must retain its files.")
        with connect(self.database) as db:
            kept = db.execute("SELECT path, sha256 FROM comm_files WHERE bundle_id=?", (owner["id"],)).fetchall()
        for file in kept:
            if self.payload_sha256(source, files, file["path"]) != file["sha256"]:
                raise StorageError("A kept communication file failed checksum verification. Its working copy was retained.")


class Bundle:
    """The files one capture keeps, staged as they arrive and published together by `seal`. `add` returns the file's record
    (id, path, sha256 ...); the same content added twice is kept once."""

    def __init__(self, files):
        self.files, self.id = files, str(uuid.uuid4())
        self.stage, self.records, self.by_sha, self.bytes = None, [], {}, 0

    def _data(self):
        if self.stage is None:
            self.stage = self.files.stage_path(self.id)
            (self.stage / "data").mkdir(parents=True, mode=0o700)
        return self.stage / "data"

    @property
    def full(self):
        return self.bytes >= BUNDLE_BYTES or len(self.records) >= BUNDLE_FILES

    def add(self, content, content_type, name=""):
        digest = sha(content)
        if digest in self.by_sha:
            return self.by_sha[digest]
        path = digest + EXTENSIONS.get(content_type, ".bin")
        target = self._data() / path
        with target.open("xb") as handle:
            os.chmod(target, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if digest_file(target)["sha256"] != digest:
            raise StorageError("A kept communication file failed its write verification.")
        record = {"id": str(uuid.uuid4()), "bundle_id": self.id, "path": path, "sha256": digest, "size": len(content),
                  "content_type": content_type[:200], "name": str(name or "")[:300]}
        self.records.append(record)
        self.by_sha[digest] = record
        self.bytes += len(content)
        return record

    def discard(self):
        if self.stage is not None:
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage = None


class CommunicationFiles:
    """Publishing and reading bundles. With storage configured the bundle is an owner of CommunicationStorage; the caller holds the
    roster lifecycle lock around `seal` (as around any storage write) and the catalog transaction that records the bundle."""

    def __init__(self, rosters):
        self.rosters, self.storage = rosters, rosters.storage
        if self.storage:
            self.storage.communication_storage = CommunicationStorage(self.storage.config, self.storage.enforce_mounts)
            self.manager = self.storage.communication_storage
        else:
            self.manager = None

    def bundle(self):
        return Bundle(self)

    def _root(self):
        root = Path(self.rosters.path).parent / "communications"
        root.mkdir(mode=0o700, exist_ok=True)
        if root.resolve() != root:
            raise StorageError("The communications directory is redirected.")
        return root

    def stage_path(self, identifier):
        if self.manager:
            self.storage.check_nvme()
            stage = self.storage.nvme / "staging" / f"comm-{identifier}"
        else:
            stage = self._root() / f".staging-{identifier}"
        if stage.resolve() != stage:
            raise StorageError("The communications staging directory is redirected.")
        return stage

    @contextmanager
    def _owner_lock(self, identifier):
        with (self.storage.lock(identifier) if self.manager else nullcontext()):
            yield

    def seal(self, bundle, db):
        """Publish a staged bundle and record it (and its files) in the caller's open transaction `db`. Nothing is published for a
        bundle with no files. The caller commits; an interrupted seal leaves an unrecorded folder that `sweep` removes later."""
        if not bundle.records:
            bundle.discard()
            return None
        created = timestamp()
        atomic_json(bundle.stage / "course.json", CourseStorage.metadata({"id": bundle.id, "name": "Communications", "created_at": created,
                                                                           "is_current": False, "version": 1}))
        fsync_dir(bundle.stage / "data")
        with self._owner_lock(bundle.id):
            final = self.manager.hot_path(bundle.id) if self.manager else self._root() / bundle.id
            os.rename(bundle.stage, final)
            fsync_dir(final.parent)
            bundle.stage = None
            if self.manager:
                db.execute("""INSERT INTO comm_bundles(id, created_at, storage_status, storage_initialized, storage_hot_required)
                              VALUES (?,?,?,1,1)""", (bundle.id, created, "staged"))
            else:
                db.execute("INSERT INTO comm_bundles(id, created_at) VALUES (?,?)", (bundle.id, created))
        db.executemany("""INSERT INTO comm_files(id, bundle_id, path, sha256, size, content_type, name, created_at)
                          VALUES (:id, :bundle_id, :path, :sha256, :size, :content_type, :name, :created_at)""",
                       [{**record, "created_at": created} for record in bundle.records])
        return bundle.id

    def read(self, file):
        """The bytes of one kept file (a `comm_files` row), checked against its checksum. Takes the roster lifecycle lock."""
        with self.rosters.lock():
            if self.manager:
                owner = self.manager.row(file["bundle_id"])
                if owner["storage_status"] in PENDING:
                    self.manager.reconcile_course(owner["id"])
                with self.manager.course_files(file["bundle_id"]) as path:
                    return self._verified(path, file)
            return self._verified(self._root() / file["bundle_id"] / "data", file)

    @staticmethod
    def _verified(root, file):
        path = root / file["path"]
        if path.resolve() != path or not path.is_file():
            raise StorageError("The kept file is missing or redirected.")
        content = path.read_bytes()
        if sha(content) != file["sha256"]:
            raise StorageError("The kept file failed checksum verification.")
        return content

    def sweep(self):
        """Remove bundle folders an interrupted capture published or staged without recording them (nothing references them)."""
        with connect(self.rosters.path) as db:
            known = {row[0] for row in db.execute("SELECT id FROM comm_bundles")}
        roots = [(self.storage.nvme / "communications", self.storage.nvme / "staging")] if self.manager else [(self._root(), self._root())]
        now = time.time()
        for published, staging in roots:
            for path in list(published.iterdir()) + [p for p in staging.iterdir() if p.name.startswith("comm-")]:
                name = path.name.removeprefix("comm-").removeprefix(".staging-")
                try:
                    uuid.UUID(name)
                except ValueError:
                    continue
                if name in known or path.is_symlink() or not path.is_dir() or now - path.stat().st_mtime < ORPHAN_AGE:
                    continue
                shutil.rmtree(path, ignore_errors=True)
