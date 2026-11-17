"""Where Recordings keeps what it fetched (docs/decisions/recordings.md, RECORDINGS "STORAGE").

A BUNDLE is the files one fetch of one recording kept (its transcript, outline, marks and Plaud's notes, copied from the Plaud
intake's archive; a bundle kept before audio played from the archive holds the audio too, and a new one never does; a later fetch
that finds a changed transcript keeps only what changed): written into NVMe staging as they arrive, then published at once and never
changed. It is an owner of the verified storage engine (`recording_bundles`, directory `recordings/`), never current, so it follows
the lifecycle COMMUNICATIONS gives `comm_bundles`: staged on NVMe until the nightly coordinator has backed it up and verified the
copy, then kept on the HDD alone and read from there. `recording_files` names each file of a bundle with its checksum; every read
checks it again, and the nightly backup checks every file of a bundle against the catalog before accepting its copy.

Without configured storage (tests, a development copy) bundles are folders beside the catalog, `recordings/<bundle>/data`.
"""
import hashlib
import os
import shutil
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from course_storage import CourseStorage, PENDING, StorageError, atomic_json, copy_file, digest_file, fsync_dir, timestamp
from rosters import connect
from teach_inbox.storage import InboxStorage

ROLES = ("transcript", "outline", "marks", "notes", "audio")
ORPHAN_AGE = 3600   # an unrecorded bundle folder older than this was left by an interrupted round
EXTENSIONS = {"application/json": ".json", "text/plain": ".txt", "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
              "audio/x-m4a": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/opus": ".opus"}


def sha(content):
    return hashlib.sha256(content).hexdigest()


class RecordingStorage(InboxStorage):
    """Bundles of kept recording files on the verified storage engine: never current, never written again once published."""
    table = "recording_bundles"
    directory = "recordings"
    hot_states = ("active", "staged")
    stage_archived_writes = False
    metadata = staticmethod(CourseStorage.metadata)

    @staticmethod
    def _owner(row):
        found = dict(row)
        found.update(name=f"Recordings · {found['created_at'][:19]}", code="", term="", term_other="", year=None, sections=[],
                     multiple_sections=False, is_current=False, deleted_at=None, version=1, updated_at=found["created_at"])
        return found

    def row(self, identifier):
        with connect(self.database) as db:
            row = db.execute("SELECT * FROM recording_bundles WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise StorageError("Recording bundle not found.")
        return self._owner(row)

    def rows(self):
        with connect(self.database) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='recording_bundles'").fetchone():
                return []
            return [self._owner(row) for row in db.execute("SELECT * FROM recording_bundles WHERE storage_status!='' ORDER BY created_at, id")]

    def check_source(self, owner, source, files):
        if source is None:
            raise StorageError("A recording bundle must retain its files.")
        with connect(self.database) as db:
            kept = db.execute("SELECT path, sha256 FROM recording_files WHERE bundle_id=?", (owner["id"],)).fetchall()
        for file in kept:
            if self.payload_sha256(source, files, file["path"]) != file["sha256"]:
                raise StorageError("A kept recording file failed checksum verification. Its working copy was retained.")

    def erase(self, identifier):
        """Remove every copy of one bundle from both drives (RECORDINGS "ERASED WHEN IT DOES NOT BELONG"; STORAGE): its NVMe
        folder, and its HDD folder with every snapshot. The catalog no longer names the bundle when this runs (its rows left in
        the transaction that erased the recording), so nothing is verified first and no final copy is made: not keeping it is
        the point, and the original stays at Plaud. The caller holds the roster lifecycle lock.

        NEVER DURING THE NIGHTLY BACKUP: its first pass copies a bundle to the HDD holding neither the lifecycle lock nor the
        bundle's (`prepare_backup`), so a folder removed meanwhile could be written back by that copy. The backup holds the
        `nightly-backup` lock from start to end; while it does, nothing is removed and the caller tries again later.
        A DRIVE THAT IS AWAY: what is on the drive that is present is removed, and the error says the other still holds its
        copy, so the caller keeps the bundle named and tries again. A redirected folder is refused before anything is removed."""
        identifier = self._identifier(identifier)
        try:
            # The bundle's own lock waits for whoever holds it; only the nightly lock is asked for without waiting.
            with self.lock("nightly-backup", blocking=False), self.lock(identifier):
                found, away = [], []
                for root, check in ((self.nvme, self.check_nvme), (self.hdd, self.check_hdd)):
                    try:
                        check()
                    except StorageError as error:
                        away.append(str(error))
                        continue
                    path = root / self.directory / identifier
                    if path.is_symlink() or path.resolve() != path:
                        raise StorageError("A recording bundle's folder is redirected. Nothing of it was removed.")
                    found.append(path)
                for path in found:
                    if path.exists():
                        shutil.rmtree(path)
                        fsync_dir(path.parent)
                if away:
                    raise StorageError("A drive is away and may still hold this recording's files. " + " ".join(away))
        except StorageError as error:
            if isinstance(error.__cause__, BlockingIOError):
                raise StorageError("The nightly backup is running. The files are removed once it has finished.") from error
            raise


class Bundle:
    """The files one fetch keeps, staged as they arrive and published together by `seal`. One file for each role at most; `add`
    and `add_file` return the file's record (id, role, path, sha256, size, content_type)."""

    def __init__(self, files):
        self.files, self.id = files, str(uuid.uuid4())
        self.stage, self.records = None, {}

    def _data(self):
        if self.stage is None:
            self.stage = self.files.stage_path(self.id)
            (self.stage / "data").mkdir(parents=True, mode=0o700)
        return self.stage / "data"

    def _target(self, role, content_type):
        if role not in ROLES or role in self.records:
            raise StorageError("A recording bundle keeps one file for each role.")
        return self._data() / (role + EXTENSIONS.get(content_type, ".bin"))

    def _record(self, role, target, digest, size, content_type):
        if digest_file(target)["sha256"] != digest:
            raise StorageError("A kept recording file failed its write verification.")
        self.records[role] = {"id": str(uuid.uuid4()), "bundle_id": self.id, "role": role, "path": target.name, "sha256": digest,
                              "size": size, "content_type": content_type[:200]}
        return self.records[role]

    def add(self, role, content, content_type):
        """Keep `content` (bytes) as this bundle's file for `role`."""
        target = self._target(role, content_type)
        with target.open("xb") as handle:
            os.chmod(target, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return self._record(role, target, sha(content), len(content), content_type)

    def add_file(self, role, source, content_type, digest, size):
        """Keep the downloaded file at `source` (its checksum and size as counted while it arrived) as this bundle's file for
        `role`: copied into a new file, flushed, and read back against that checksum."""
        target = self._target(role, content_type)
        copy_file(Path(source), target)
        with target.open("rb") as handle:
            os.fsync(handle.fileno())
        return self._record(role, target, digest, size, content_type)

    def discard(self):
        if self.stage is not None:
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage = None


class RecordingFiles:
    """Publishing and reading bundles. With storage configured the bundle is an owner of RecordingStorage; the caller holds the
    roster lifecycle lock around `seal` (as around any storage write) and the catalog transaction that records the bundle."""

    def __init__(self, rosters):
        self.rosters, self.storage = rosters, rosters.storage
        if self.storage:
            self.storage.recording_storage = RecordingStorage(self.storage.config, self.storage.enforce_mounts)
            self.manager = self.storage.recording_storage
        else:
            self.manager = None

    def bundle(self):
        return Bundle(self)

    def _root(self):
        root = Path(self.rosters.path).parent / "recordings"
        root.mkdir(mode=0o700, exist_ok=True)
        if root.resolve() != root:
            raise StorageError("The recordings directory is redirected.")
        return root

    def stage_path(self, identifier):
        if self.manager:
            self.storage.check_nvme()
            stage = self.storage.nvme / "staging" / f"rec-{identifier}"
        else:
            stage = self._root() / f".staging-{identifier}"
        if stage.resolve() != stage:
            raise StorageError("The recordings staging directory is redirected.")
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
        atomic_json(bundle.stage / "course.json", CourseStorage.metadata({"id": bundle.id, "name": "Recordings", "created_at": created,
                                                                           "is_current": False, "version": 1}))
        fsync_dir(bundle.stage / "data")
        with self._owner_lock(bundle.id):
            final = self.manager.hot_path(bundle.id) if self.manager else self._root() / bundle.id
            os.rename(bundle.stage, final)
            fsync_dir(final.parent)
            bundle.stage = None
            if self.manager:
                db.execute("""INSERT INTO recording_bundles(id, created_at, storage_status, storage_initialized, storage_hot_required)
                              VALUES (?,?,?,1,1)""", (bundle.id, created, "staged"))
            else:
                db.execute("INSERT INTO recording_bundles(id, created_at) VALUES (?,?)", (bundle.id, created))
        db.executemany("""INSERT INTO recording_files(id, bundle_id, role, path, sha256, size, content_type, created_at)
                          VALUES (:id, :bundle_id, :role, :path, :sha256, :size, :content_type, :created_at)""",
                       [{**record, "created_at": created} for record in bundle.records.values()])
        return bundle.id

    def read(self, file):
        """The bytes of one kept file (a `recording_files` row), checked against its checksum. Takes the roster lifecycle lock."""
        with self.rosters.lock():
            if self.manager:
                owner = self.manager.row(file["bundle_id"])
                if owner["storage_status"] in PENDING:
                    self.manager.reconcile_course(owner["id"])
                with self.manager.course_files(file["bundle_id"]) as path:
                    return self._verified(path, file)
            return self._verified(self._root() / file["bundle_id"] / "data", file)

    def read_many(self, files):
        """{file id: bytes} of several kept files (`recording_files` rows), each checked against its checksum; a bundle is opened
        once however many of its files are asked for. Takes the roster lifecycle lock."""
        found, bundles = {}, {}
        for file in files:
            bundles.setdefault(file["bundle_id"], []).append(file)
        with self.rosters.lock():
            for bundle_id, wanted in bundles.items():
                if self.manager:
                    owner = self.manager.row(bundle_id)
                    if owner["storage_status"] in PENDING:
                        self.manager.reconcile_course(owner["id"])
                    with self.manager.course_files(bundle_id) as path:
                        found.update((file["id"], self._verified(path, file)) for file in wanted)
                else:
                    found.update((file["id"], self._verified(self._root() / bundle_id / "data", file)) for file in wanted)
        return found

    @staticmethod
    def _verified(root, file):
        path = root / file["path"]
        if path.resolve() != path or not path.is_file():
            raise StorageError("The kept file is missing or redirected.")
        content = path.read_bytes()
        if sha(content) != file["sha256"]:
            raise StorageError("The kept file failed checksum verification.")
        return content

    def erase(self, bundle_id):
        """Remove the files of one bundle whose rows are gone (the recording was erased: RECORDINGS "ERASED WHEN IT DOES NOT
        BELONG"), wherever they are kept. The caller holds the roster lifecycle lock. Raises StorageError where a drive is
        away or a folder is redirected; nothing is then removed, and the caller tries again."""
        bundle_id = str(uuid.UUID(str(bundle_id)))
        if self.manager:
            self.manager.erase(bundle_id)
            return
        path = self._root() / bundle_id
        if path.is_symlink() or path.resolve() != path:
            raise StorageError("A recording bundle's folder is redirected. Nothing of it was removed.")
        if path.exists():
            shutil.rmtree(path)
            fsync_dir(path.parent)

    def sweep(self):
        """Remove bundle folders an interrupted round published or staged without recording them (nothing references them)."""
        with connect(self.rosters.path) as db:
            known = {row[0] for row in db.execute("SELECT id FROM recording_bundles")}
        roots = [(self.storage.nvme / "recordings", self.storage.nvme / "staging")] if self.manager else [(self._root(), self._root())]
        now = time.time()
        for published, staging in roots:
            for path in list(published.iterdir()) + [p for p in staging.iterdir() if p.name.startswith("rec-")]:
                name = path.name.removeprefix("rec-").removeprefix(".staging-")
                try:
                    uuid.UUID(name)
                except ValueError:
                    continue
                if name in known or path.is_symlink() or not path.is_dir() or now - path.stat().st_mtime < ORPHAN_AGE:
                    continue
                shutil.rmtree(path, ignore_errors=True)
