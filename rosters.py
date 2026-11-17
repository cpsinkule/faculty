"""Canonical term/section rosters and the private, workspace-wide UIN registry.

A reserved enrollment ID belongs to (course definition, term/year, UIN). Section
memberships and exported fields refer to it. Registry reservations commit before
payload publication: failed writes may reserve an ID, but retries reuse it and
never replace a published roster with a partially imported file.
"""

import atexit
import fcntl
import functools
import json
import os
import re
import secrets
import signal
import sqlite3
import threading
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from course_groups import group_courses
from course_storage import CourseStorage, PENDING, StorageError, atomic_json, decode_course, live_offerings, timestamp


# Catalog connections are reused. A new connection parses the whole catalog schema (about 3 ms) on its first statement, and the Inbox
# worker opens about thirty per idle turn. A connection goes back to the pool only when it is exactly what a new one would be: its
# block and commit ended without an exception, no transaction is open, no cursor it handed out is still alive (an unfinished
# statement holds a read snapshot), nothing it ran could leave state behind (a PRAGMA setting, a TEMP object, an attached
# database), and its file is still the one at its path. Anything else is closed as before. An idle pooled connection holds no
# snapshot or transaction, but it keeps the catalog open, with a shared lock on the file and on its -shm: the WAL stays while a
# process runs (journal_size_limit bounds it), and anything that needs the catalog to itself (a journal-mode change, the final
# checkpoint that removes the WAL, replacing the file) needs every Faculty process stopped, not merely idle. A process ended by
# SIGTERM or SIGHUP closes its idle connections first (_terminate), so a stopped catalog is complete without its WAL, as before.
POOL_PER_FILE = 8
POOL_TOTAL = 16
_pool_lock = threading.Lock()
_idle = []  # (key, connection), the least recently returned first
_hooked = False  # _terminate is set for SIGTERM and SIGHUP where they had their default action


# Statements that could leave something on their connection for a later block: a TEMP object, an attached database, or a PRAGMA
# that is not a plain read (its name alone, then the end of the statement), except those that only read a table's layout or check
# the file, and foreign_keys, which every checkout sets again. A match only keeps that connection out of the pool, so the test is
# broad (a word in a literal, or a comment inside a PRAGMA, counts too).
_KEEPS = re.compile(r"\b(?:temp|temporary|attach)\b", re.I)
_PRAGMA = re.compile(r"\bpragma\b(?:\s*(?:[\w\"`\[\]]+\s*\.\s*)?([\w\"`\[\]]+)\s*(;|$)?)?", re.I)
_PRAGMA_READS = frozenset({"table_info", "table_xinfo", "table_list", "index_list", "index_info", "index_xinfo", "foreign_key_list",
                           "foreign_key_check", "integrity_check", "quick_check", "foreign_keys"})


@functools.lru_cache(maxsize=4096)
def _leaves_state(sql):
    if _KEEPS.search(sql):
        return True
    for match in _PRAGMA.finditer(sql):
        if match.group(1) is None or (match.group(2) is None and match.group(1).strip('"`[]').lower() not in _PRAGMA_READS):
            return True
    return False


class _PooledConnection(sqlite3.Connection):
    """A catalog connection that records every cursor it hands out and every statement that could leave state on it, so that
    connect() can tell whether it is still what a new connection would be."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cursors = weakref.WeakSet()
        self._owner = threading.get_ident()  # the thread whose block holds it; None while idle in the pool
        self._key = None  # (pid, path, st_dev, st_ino) of the file it opened; None is never pooled
        self._changed = False  # a block changed something a new connection would not have: never pooled

    def _check_open(self):
        if self._owner is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    def _check_thread(self):
        # check_same_thread is off so that a pooled connection may serve another thread later; the thread holding it now keeps it,
        # and once its block has ended it is closed to that block, as it was when every connection was closed after its block.
        self._check_open()
        if threading.get_ident() != self._owner:
            raise sqlite3.ProgrammingError("SQLite objects created in a thread can only be used in that same thread. The object was "
                                           f"created in thread id {self._owner} and this is thread id {threading.get_ident()}.")

    def _note(self, args):
        if not self._changed and args and isinstance(args[0], str) and _leaves_state(args[0]):
            self._changed = True

    def commit(self):
        self._check_thread()
        return super().commit()

    def rollback(self):
        self._check_thread()
        return super().rollback()

    def close(self):
        if self._owner is None:  # its block ended and it went back to the pool: closing it again changes nothing, as before
            return
        self._check_thread()
        super().close()

    def __enter__(self):
        self._check_open()
        return super().__enter__()

    def interrupt(self):
        self._check_open()  # another thread may interrupt the block that holds it, as before
        return super().interrupt()

    def cursor(self, *args, **kwargs):
        self._check_thread()
        if args or "factory" in kwargs:
            self._changed = True  # a cursor of another class runs statements this connection does not see
            cursor = super().cursor(*args, **kwargs)
        else:
            cursor = super().cursor(_PooledCursor)
        self._cursors.add(cursor)
        return cursor

    # PERFORMANCE: route shortcuts through the tracked cursor too. SQLite's shortcuts create a plain cursor in C, whose
    # later statements otherwise bypass both state tracking and the thread guard even after the first statement was checked.
    def execute(self, *args):
        return self.cursor().execute(*args)

    def executemany(self, *args):
        return self.cursor().executemany(*args)

    def executescript(self, *args):
        return self.cursor().executescript(*args)


class _PooledCursor(sqlite3.Cursor):
    """What cursor() hands out: its statements are checked like the connection's own."""

    def execute(self, *args):
        self.connection._check_thread()
        self.connection._note(args)
        return super().execute(*args)

    def executemany(self, *args):
        self.connection._check_thread()
        self.connection._note(args)
        return super().executemany(*args)

    def executescript(self, *args):
        self.connection._check_thread()
        self.connection._note(args)
        return super().executescript(*args)


def _unpooled(name):
    def method(self, *args, **kwargs):
        self._check_thread()
        self._changed = True
        return getattr(sqlite3.Connection, name)(self, *args, **kwargs)
    return method


def _checked(name, base=sqlite3.Connection):
    def method(self, *args, **kwargs):
        connection = self if base is sqlite3.Connection else self.connection
        connection._check_thread()
        return getattr(base, name)(self, *args, **kwargs)
    return method


def _attribute(name, check=None, holder_only=False):
    """A connection attribute behind the checks SQLite made on it; holder_only settings set by any other thread (a stale reference
    whose block ended) are ignored, since they changed only that block's own closed connection before."""
    base = getattr(sqlite3.Connection, name)

    def get(self):
        if check:
            check(self)
        return base.__get__(self)

    def put(self, value):
        if check:
            check(self)
        if holder_only and self._owner != threading.get_ident():
            return
        base.__set__(self, value)
    return property(get, put)


# What a block could leave on its connection for the next one (an open blob also holds a read snapshot). Faculty uses none of
# these; a connection that did is closed after its block. create_function is not here: the Inbox search registers the same
# deterministic functions on every connection it uses, and a trace callback is cleared at each checkout.
for _name in ("blobopen", "create_aggregate", "create_collation", "create_window_function", "deserialize", "enable_load_extension",
              "load_extension", "set_authorizer", "set_progress_handler", "setconfig", "setlimit"):
    setattr(_PooledConnection, _name, _unpooled(_name))
# The rest of what SQLite refuses from another thread or on a closed connection.
for _name in ("__exit__", "backup", "create_function", "getconfig", "getlimit", "iterdump", "serialize", "set_trace_callback"):
    setattr(_PooledConnection, _name, _checked(_name))
for _name in ("__iter__", "__next__", "fetchone", "fetchmany", "fetchall", "close"):
    setattr(_PooledCursor, _name, _checked(_name, sqlite3.Cursor))
_PooledConnection.autocommit = _attribute("autocommit", _PooledConnection._check_thread)
_PooledConnection.in_transaction = _attribute("in_transaction", _PooledConnection._check_open)
_PooledConnection.total_changes = _attribute("total_changes", _PooledConnection._check_open)
_PooledConnection.isolation_level = _attribute("isolation_level", holder_only=True)
_PooledConnection.row_factory = _attribute("row_factory", holder_only=True)
_PooledConnection.text_factory = _attribute("text_factory", holder_only=True)
del _name


def _file_key(path):
    """(pid, path, st_dev, st_ino) of a database file; None for anything else (":memory:", a URI, a missing path)."""
    name = os.fspath(path)
    if name in ("", ":memory:") or (isinstance(name, str) and name.startswith("file:")):
        return None
    try:
        status = os.stat(name)
    except OSError:
        return None
    return (os.getpid(), name, status.st_dev, status.st_ino)


def _checkout(path):
    key, db, stale = _file_key(path), None, []
    if key:
        with _pool_lock:
            for index in range(len(_idle) - 1, -1, -1):
                if _idle[index][0] == key:
                    db = _idle.pop(index)[1]
                    break
            stale = [entry for entry in _idle if entry[0][:2] == key[:2] and entry[0] != key]
            for entry in stale:
                _idle.remove(entry)
    for _, connection in stale:
        sqlite3.Connection.close(connection)  # SQLite neither checkpoints nor removes -wal/-shm once its file has moved (HAS_MOVED)
    if db is None:
        db = sqlite3.connect(path, timeout=15, check_same_thread=False, factory=_PooledConnection)
        opened = _file_key(path)
        db._key = opened if key in (None, opened) else None  # replaced while opening: used once, never pooled
        setup = ("PRAGMA journal_size_limit=67108864", "PRAGMA foreign_keys=ON")
    else:
        db._owner = threading.get_ident()
        db.set_trace_callback(None)
        setup = ("PRAGMA foreign_keys=ON",)
    try:
        db.row_factory = sqlite3.Row
        for statement in setup:
            sqlite3.Connection.execute(db, statement)  # not noted: what every checkout sets
    except BaseException:
        db.close()
        raise
    return db


def _checkin(db):
    """Pool a connection whose block ended cleanly if it is exactly what a new one would be; False means close it."""
    try:
        if (db._key is None or db._changed or db.in_transaction or db._cursors or db._owner != threading.get_ident()
                or db.isolation_level != "" or db.autocommit != sqlite3.LEGACY_TRANSACTION_CONTROL or db.text_factory is not str
                or _file_key(db._key[1]) != db._key):
            return False
    except sqlite3.ProgrammingError:  # closed inside its block
        return False
    evicted = []
    db._owner = None
    with _pool_lock:
        _idle.append((db._key, db))
        same = [entry for entry in _idle if entry[0] == db._key]
        if len(same) > POOL_PER_FILE:
            _idle.remove(same[0])
            evicted.append(same[0][1])
        while len(_idle) > POOL_TOTAL:
            evicted.append(_idle.pop(0)[1])
    for connection in evicted:
        sqlite3.Connection.close(connection)
    if not _hooked:
        _hook_termination()
    return True


@atexit.register
def _close_pool(timeout=-1):
    # A stopped process leaves no WAL behind, as when every connection was closed after its block.
    if not _pool_lock.acquire(timeout=timeout):
        return
    try:
        idle = [db for _, db in _idle]
        _idle.clear()
    finally:
        _pool_lock.release()
    for db in idle:
        sqlite3.Connection.close(db)


def _terminate(signum, frame):
    # A process ended by a signal runs no atexit hook, and its pooled connections would leave the WAL behind (holding commits the
    # catalog file does not have yet) where closing every connection after its block left none. So close the idle ones first (the
    # last connection to close checkpoints and removes the WAL), then end by the same signal as before.
    try:
        _close_pool(timeout=1)  # waits a second at most: this thread may be the one holding the pool lock
    finally:
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)


def _hook_termination():
    global _hooked
    if threading.current_thread() is not threading.main_thread():
        return  # only the main thread may set a signal handler; its next checkin tries
    _hooked = True
    for signum in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(signum) is signal.SIG_DFL:  # a process's own handler, or an ignored signal, is left as it is
            signal.signal(signum, _terminate)


@contextmanager
def connect(path):
    db = _checkout(path)
    clean = False
    try:
        with db:
            yield db
        clean = True
    finally:
        if not (clean and _checkin(db)):
            db.close()


def initialize_rosters(path):
    with connect(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS course_terms (
                id TEXT PRIMARY KEY,
                definition_id TEXT NOT NULL REFERENCES course_definitions(id),
                code TEXT NOT NULL, name TEXT NOT NULL,
                term TEXT NOT NULL, term_other TEXT NOT NULL DEFAULT '', year INTEGER NOT NULL,
                sections TEXT NOT NULL DEFAULT '[]', multiple_sections INTEGER NOT NULL DEFAULT 0,
                is_current INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
                storage_status TEXT NOT NULL DEFAULT '', storage_snapshot TEXT NOT NULL DEFAULT '',
                storage_verified_at TEXT NOT NULL DEFAULT '', storage_initialized INTEGER NOT NULL DEFAULT 0,
                storage_hot_required INTEGER NOT NULL DEFAULT 0, storage_error TEXT NOT NULL DEFAULT '',
                roster_initialized INTEGER NOT NULL DEFAULT 0,
                UNIQUE(definition_id, term, term_other, year)
            );
            CREATE TABLE IF NOT EXISTS course_sections (
                id TEXT PRIMARY KEY, term_id TEXT NOT NULL REFERENCES course_terms(id),
                number INTEGER NOT NULL, UNIQUE(term_id, number)
            );
            CREATE TABLE IF NOT EXISTS student_identities (
                uin TEXT PRIMARY KEY CHECK(length(uin)=9 AND uin NOT GLOB '*[^0-9]*'),
                prefix TEXT NOT NULL CHECK(length(prefix)=5 AND prefix NOT GLOB '*[^0-9]*'
                    AND substr(prefix,1,1) != '0'),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS student_enrollments (
                id TEXT PRIMARY KEY CHECK(length(id)=10 AND id NOT GLOB '*[^0-9]*'
                    AND substr(id,1,1) != '0'),
                term_id TEXT NOT NULL REFERENCES course_terms(id),
                uin TEXT NOT NULL REFERENCES student_identities(uin),
                created_at TEXT NOT NULL, UNIQUE(term_id, uin)
            );
            CREATE TABLE IF NOT EXISTS roster_memberships (
                section_id TEXT NOT NULL REFERENCES course_sections(id),
                enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
                PRIMARY KEY(section_id, enrollment_id)
            );
            CREATE TRIGGER IF NOT EXISTS enrollment_prefix_matches BEFORE INSERT ON student_enrollments
                WHEN substr(NEW.id,1,5) != (SELECT prefix FROM student_identities WHERE uin=NEW.uin)
                BEGIN SELECT RAISE(ABORT, 'Student identifier prefix mismatch'); END;
            CREATE TRIGGER IF NOT EXISTS membership_term_matches BEFORE INSERT ON roster_memberships
                WHEN (SELECT term_id FROM course_sections WHERE id=NEW.section_id) !=
                     (SELECT term_id FROM student_enrollments WHERE id=NEW.enrollment_id)
                BEGIN SELECT RAISE(ABORT, 'Student enrollment term mismatch'); END;
            -- Dropping a student (requirement, 2026-09-27): a recorded, reversible mark on the SECTION MEMBERSHIP, never an erasure.
            -- The membership and everything referencing it stay; the latest drop/restore row counts. `roster_current` is the
            -- roster as current work sees it (new seats, card issues, exams, counts); `roster_dropped` is who is dropped now.
            CREATE TABLE IF NOT EXISTS roster_drops (
                id TEXT PRIMARY KEY,
                section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('drop','restore')),
                source TEXT NOT NULL CHECK(source IN ('roster','canvas')),
                reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 200),
                request_id TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(request_id, section_id, enrollment_id),
                FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
            );
            CREATE INDEX IF NOT EXISTS roster_drops_membership ON roster_drops(section_id, enrollment_id);
            CREATE TRIGGER IF NOT EXISTS roster_drop_immutable BEFORE UPDATE ON roster_drops
                BEGIN SELECT RAISE(ABORT, 'Roster drops are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS roster_drop_retained BEFORE DELETE ON roster_drops
                BEGIN SELECT RAISE(ABORT, 'Roster drops are retained'); END;
            CREATE VIEW IF NOT EXISTS roster_dropped AS
                SELECT d.section_id, d.enrollment_id, d.created_at AS dropped_at, d.source, d.reason FROM roster_drops d
                WHERE d.action='drop' AND d.rowid=(SELECT MAX(x.rowid) FROM roster_drops x
                                                   WHERE x.section_id=d.section_id AND x.enrollment_id=d.enrollment_id);
            -- What the registrar's roster says of a membership at the section's latest import (requirement, 2026-10-15). Replaced for the
            -- section on every import, which also drops each marked student (2026-10-25, `import_roster`). `roster_leaving` leaves
            -- out a membership the instructor ever restored.
            CREATE TABLE IF NOT EXISTS roster_registrar_marks (
                section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
                reason TEXT NOT NULL CHECK(reason IN ('Q drop','Withdrawn','Dropped')), imported_at TEXT NOT NULL,
                PRIMARY KEY(section_id, enrollment_id),
                FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
            );
            CREATE VIEW IF NOT EXISTS roster_leaving AS
                SELECT r.section_id, r.enrollment_id, r.reason FROM roster_registrar_marks r
                WHERE NOT EXISTS(SELECT 1 FROM roster_drops d WHERE d.section_id=r.section_id AND d.enrollment_id=r.enrollment_id
                                 AND d.action='restore');
            CREATE VIEW IF NOT EXISTS roster_current AS
                SELECT m.section_id, m.enrollment_id FROM roster_memberships m
                WHERE NOT EXISTS(SELECT 1 FROM roster_dropped d WHERE d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id);
        """)
        if "roster_initialized" not in {row["name"] for row in db.execute("PRAGMA table_info(course_terms)")}:
            db.execute("ALTER TABLE course_terms ADD COLUMN roster_initialized INTEGER NOT NULL DEFAULT 0")


def leaving_reason(fields):
    """Why the registrar's roster row says the student is leaving, or "". Howdy keeps a Q-dropped or withdrawn student on the
    class roster: Reg Status reads "Q-Drop State Limited" with Final "Q", or "Withdraw After Census Date" with Final "W"
    (MATH 140 rosters of 2026-10-15)."""
    folded = {" ".join(str(k).split()).casefold(): str(v or "").strip() for k, v in fields.items()}
    status = folded.get("reg status", "").casefold()
    grades = {folded.get("final", "").upper(), folded.get("updated final", "").upper()}
    if "q-drop" in status or "q drop" in status or "Q" in grades:
        return "Q drop"
    if "withdr" in status or "W" in grades:
        return "Withdrawn"
    if "drop" in status:
        return "Dropped"
    return ""


def term_key(course):
    return (course["definition_id"], course["term"], course["term_other"] if course["term"] == "Other" else "", course["year"])


def synchronize_owners(db, managed):
    """Retain permanent owners independently of saved entries and navigation IDs."""
    groups = {}
    for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL ORDER BY updated_at, id"):
        course = decode_course(row)
        if not course["definition_id"] or not course["year"] or not course["term"]:
            continue
        key = term_key(course)
        groups.setdefault(key, []).append(course)
    seen = set()
    for key, courses in groups.items():
        latest = courses[-1]
        db.execute("""INSERT INTO course_terms (id, definition_id, term, term_other, year, code, name, created_at, updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(definition_id, term, term_other, year) DO NOTHING""",
                   (str(uuid.uuid4()), *key, latest["code"], latest["name"], timestamp(), timestamp()))
        owner = db.execute("SELECT * FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?", key).fetchone()
        seen.add(owner["id"])
        sections = sorted({section for course in courses for section in course["sections"]})
        for number in sections:
            db.execute("INSERT OR IGNORE INTO course_sections (id,term_id,number) VALUES (?,?,?)", (str(uuid.uuid4()), owner["id"], number))
        desired = any(course["is_current"] for course in courses)
        status = owner["storage_status"]
        if managed and status and bool(owner["is_current"]) != desired:
            if status in PENDING:
                raise Conflict("Roster storage is being moved. Wait for it to finish before changing this course.")
            status = "restore_pending" if desired else "archive_pending"
        db.execute("""UPDATE course_terms SET code=?,name=?,sections=?,multiple_sections=?,is_current=?,
                      storage_status=?,updated_at=? WHERE id=?""",
                   (latest["code"], latest["name"], json.dumps(sections), len(sections) > 1, desired, status,
                    latest["updated_at"], owner["id"]))
    for owner in db.execute("SELECT * FROM course_terms").fetchall():
        if owner["id"] not in seen and owner["is_current"]:
            if managed and owner["storage_status"] in PENDING:
                raise Conflict("Roster storage is being moved. Wait for it to finish before changing this course.")
            db.execute("UPDATE course_terms SET is_current=0,storage_status=? WHERE id=?",
                       ("archive_pending" if managed and owner["storage_status"] else "", owner["id"]))

    from teach_inbox.storage import synchronize
    synchronize(db)
    from document_identity.storage import synchronize as synchronize_documents
    synchronize_documents(db)

def protect_roster_references(db, previous, replacement=None):
    """Reject edits that would leave an imported section without a visible owner."""
    from teach_inbox.storage import protect_references
    protect_references(db, previous, replacement)
    from document_identity.storage import protect_references as protect_documents
    protect_documents(db, previous, replacement)
    from response_cards.service import protect_references as protect_response_cards
    protect_response_cards(db, previous, replacement)
    from exam_workflow.protection import protect_references as protect_exams
    protect_exams(db, previous, replacement)
    from calendar_service import protect_references as protect_calendar
    protect_calendar(db, previous, replacement)
    from scheduling.service import protect_references as protect_schedule
    protect_schedule(db, previous, replacement)
    from gradebook.service import protect_references as protect_gradebook
    protect_gradebook(db, previous, replacement)
    key = term_key(previous)
    owner = db.execute("SELECT id FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?", key).fetchone()
    if not owner:
        return
    used = {row[0] for row in db.execute("""SELECT DISTINCT s.number FROM course_sections s
        JOIN roster_memberships m ON m.section_id=s.id WHERE s.term_id=?""", (owner["id"],))}
    if not used:
        return
    covered = set()
    for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL AND id != ?", (previous["id"],)):
        other = decode_course(row)
        if term_key(other) == key:
            covered.update(other["sections"])
    if replacement and term_key(replacement) == key:
        covered.update(replacement["sections"])
    if used - covered:
        raise Conflict("This change would detach an imported roster. Keep its course code, term/year, and section; mark the course as not current to archive it.")


class TermStorage(CourseStorage):
    table = "course_terms"
    directory = "terms"

    def check_source(self, course, source, files):
        if course["roster_initialized"]:
            if source is None:
                raise StorageError("The saved roster is missing. No replacement backup was accepted.")
            RosterService.read_payload(source / "data", required=True)
        with connect(self.database) as db:
            artifacts = db.execute("SELECT path,sha256 FROM assistant_artifacts WHERE term_id=?", (course["id"],)).fetchall() if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='assistant_artifacts'").fetchone() else []
            if db.execute("SELECT 1 FROM pragma_table_info('print_batches') WHERE name='term_id'").fetchone():
                # Print jobs owned by the term (document_identity migration 013): each issued PDF and manifest as it was kept.
                artifacts = [*artifacts, *db.execute("""SELECT output_path path,output_sha256 sha256 FROM print_batches
                    WHERE term_id=? AND section_id IS NULL AND status='ready' UNION ALL SELECT manifest_path,manifest_sha256
                    FROM print_batches WHERE term_id=? AND section_id IS NULL AND status='ready'""", (course["id"], course["id"])).fetchall()]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='canvas_content_files'").fetchone():
                # Files kept for the term's Canvas modules (canvas/files.py, migration canvas/008): each file as it was kept.
                artifacts = [*artifacts, *db.execute("SELECT path,sha256 FROM canvas_content_files WHERE term_id=?", (course["id"],)).fetchall()]
        for artifact in artifacts:
            if source is None or self.payload_sha256(source, files, artifact["path"]) != artifact["sha256"]:
                raise StorageError("A teaching artifact failed checksum verification. Its working copy was retained.")


class RosterService:
    def __init__(self, path, storage=None):
        self.path = Path(path)
        self.storage = storage
        self._lock_state = threading.local()
        initialize_rosters(path)
        if storage:
            storage.term_storage = TermStorage(storage.config, storage.enforce_mounts)
        with self.lock(), connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            synchronize_owners(db, bool(storage))

    @contextmanager
    def lock(self):
        # A compound operation may call another service sharing this roster
        # instance. Keep the original process-wide flock until its outermost
        # scope exits; another thread/process must still acquire that flock.
        if getattr(self._lock_state, "held", False):
            yield
            return
        if self.storage:
            with self.storage.lifecycle_lock():
                self._lock_state.held = True
                try:
                    yield
                finally:
                    self._lock_state.held = False
        else:
            descriptor = os.open(self.path.parent / ".rosters.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._lock_state.held = True
                try:
                    yield
                finally:
                    self._lock_state.held = False
            finally:
                os.close(descriptor)

    def resolve(self, group_id, number):
        with connect(self.path) as db:
            courses = live_offerings(db)
            group = next((group for group in group_courses(courses) if group["id"] == group_id), None)
            if not group or number not in group["sections"]:
                raise NotFound("This course section no longer exists.")
            owner = db.execute("SELECT * FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?", term_key(group)).fetchone()
            if not owner:
                raise Conflict("Complete this course's code, term, and year before importing its roster.")
            section = db.execute("SELECT * FROM course_sections WHERE term_id=? AND number=?", (owner["id"], number)).fetchone()
            return dict(owner), dict(section)

    def summary(self):
        """Per course page and section number: students, students with active accommodations and DR bookings.

        A read-only projection of catalog rows only (roster memberships, current accommodation records, current DR
        bookings); no roster payload is read. Groups resolve to their term owner exactly like `resolve`. Sections without a
        saved term owner yet (an incomplete code/term/year) are left out. Used by the course list and course pages (UX
        overhaul, 2026-09-26)."""
        from accommodations import AccommodationService
        with connect(self.path) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            courses = live_offerings(db)
            students = {row[0]: row[1] for row in db.execute(
                "SELECT section_id, COUNT(*) FROM roster_current GROUP BY section_id")}
            accommodations = {}
            if {"student_accommodations", "accommodation_revisions"} <= tables:
                for row in db.execute("""SELECT a.section_id, r.snapshot_json FROM student_accommodations a
                                         JOIN accommodation_revisions r ON r.id=a.revision_id"""):
                    if AccommodationService.active(json.loads(row["snapshot_json"])):
                        accommodations[row["section_id"]] = accommodations.get(row["section_id"], 0) + 1
            bookings, booked = {}, {}
            if "dr_exam_bookings" in tables:
                for row in db.execute("""SELECT section_id, COUNT(*) bookings, COUNT(DISTINCT enrollment_id) students
                                         FROM dr_exam_bookings GROUP BY section_id"""):
                    bookings[row["section_id"]], booked[row["section_id"]] = row["bookings"], row["students"]
            # PERFORMANCE: these owner lookups belong to this read, not one query pair for every course group.
            owners = {term_key(row): row["id"] for row in db.execute(
                "SELECT id,definition_id,term,term_other,year FROM course_terms")}
            numbers_by_term = {}
            for row in db.execute("SELECT id,number,term_id FROM course_sections"):
                numbers_by_term.setdefault(row["term_id"], {})[row["number"]] = row["id"]
            groups = {}
            for group in group_courses(courses):
                if not group.get("definition_id") or not group.get("term") or not group.get("year"):
                    continue
                owner = owners.get(term_key(group))
                if not owner:
                    continue
                numbers = numbers_by_term.get(owner, {})
                sections = {}
                for number in group["sections"]:
                    section_id = numbers.get(number)
                    if not section_id:
                        continue
                    sections[str(number)] = {
                        "section_id": section_id, "students": students.get(section_id, 0),
                        "with_accommodations": accommodations.get(section_id, 0),
                        "dr_bookings": bookings.get(section_id, 0), "with_dr_bookings": booked.get(section_id, 0),
                    }
                groups[group["id"]] = {"term_id": owner, "sections": sections}
        return {"groups": groups}

    @contextmanager
    def files(self, owner, write=False):
        if self.storage:
            manager = self.storage.term_storage
            if not owner["storage_status"]:
                if not write:
                    yield None
                    return
                manager.update(owner["id"], storage_status="restore_pending" if owner["is_current"] else "archive_pending")
                manager.reconcile_course(owner["id"])
            elif owner["storage_status"] in PENDING:
                manager.reconcile_course(owner["id"])
            with manager.course_files(owner["id"], write=write) as path:
                yield path
        else:
            path = self.path.parent / "terms" / owner["id"] / "data"
            if path.resolve() != path:
                raise StorageError("The roster directory is redirected.")
            if write:
                path.mkdir(parents=True, mode=0o700, exist_ok=True)
            yield path

    @staticmethod
    def read_payload(path, required=False):
        if path is None:
            if required:
                raise StorageError("The saved roster is missing. Check its verified backup before importing again.")
            return {"schema_version": 1, "sections": {}}
        file = path / "roster.json"
        if file.is_symlink():
            raise StorageError("The roster file is redirected.")
        if not file.exists():
            if required:
                raise StorageError("The saved roster file is missing. Restore it from a verified backup before importing again.")
            return {"schema_version": 1, "sections": {}}
        try:
            payload = json.loads(file.read_text())
            if payload["schema_version"] != 1 or not isinstance(payload["sections"], dict):
                raise ValueError("Unknown roster format")
            return payload
        except (ValueError, KeyError, TypeError) as error:
            raise StorageError("The saved roster cannot be read. It was retained; check its verified backup before importing again.") from error

    @staticmethod
    def response(owner, section, payload):
        data = payload["sections"].get(section["id"], {"headers": [], "students": {}, "imports": []})
        students = sorted(data["students"].values(), key=lambda student: next(
            (str(value).casefold() for name, value in student["fields"].items() if name.casefold() == "name"), ""))
        return {"term_id": owner["id"], "section_id": section["id"], "section": section["number"],
                "students": students, "headers": data["headers"], "count": len(students),
                "last_import": data["imports"][-1] if data["imports"] else None}

    def get(self, group_id, number):
        with self.lock():
            owner, section = self.resolve(group_id, number)
            with self.files(owner) as path:
                return self.decorate(self.marked(self.response(owner, section, self.read_payload(path, owner["roster_initialized"]))))

    def marked(self, response):
        """Marks each dropped student (`dropped`: when, from where, why) and counts only the current ones in `count`. Current
        students the registrar's roster says are leaving are listed in `leaving` (only when there are any), see `leaving`."""
        with connect(self.path) as db:
            dropped = {row["enrollment_id"]: {"at": row["dropped_at"], "source": row["source"], "reason": row["reason"]}
                       for row in db.execute("SELECT * FROM roster_dropped WHERE section_id=?", (response["section_id"],))}
            restored = {row[0] for row in db.execute("SELECT DISTINCT enrollment_id FROM roster_drops WHERE section_id=? AND action='restore'",
                                                     (response["section_id"],))}
        for student in response["students"]:
            student.pop("dropped", None)
            if student["id"] in dropped:
                student["dropped"] = dropped[student["id"]]
        response["dropped_count"] = sum(1 for student in response["students"] if "dropped" in student)
        response["count"] = len(response["students"]) - response["dropped_count"]
        leaving = self.leaving(response, restored)
        if leaving:
            response["leaving"] = leaving
        return response

    @staticmethod
    def leaving(response, restored):
        """Current students the latest roster import says are leaving the section (requirement, 2026-10-15), offered for the roster's own Drop: a student the latest import did not
        list (`last_import.missing`), or a Q drop or withdrawal the registrar marks on the row (`leaving_reason`) that an import
        made before such marks were dropped by the import itself (2026-10-25) left current. A student the instructor restored is
        not offered again."""
        latest = response.get("last_import") or {}
        missing = set(latest.get("missing") or ())
        found = []
        for student in response["students"]:
            if "dropped" in student or student["id"] in restored:
                continue
            reason = leaving_reason(student["fields"]) if student["id"] not in missing else f"Not on the roster imported {latest['at'][:10]}"
            if reason:
                name = next((str(v).strip() for k, v in student["fields"].items() if k.casefold() == "name"), "") or student["id"]
                found.append({"id": student["id"], "name": name, "reason": reason})
        return found

    def set_dropped(self, group_id, number, data, action, actor="workspace"):
        """Drops students from a section, or restores them (the instructor's explicit action). Every change is a retained
        `roster_drops` row keyed by the request ID, so a repeated request changes nothing; a student already in that state is
        left alone. Only the membership's current state changes: its records, the roster payload and every reference stay."""
        if action not in ("drop", "restore"):
            raise ValueError("Choose drop or restore.")
        data = data if isinstance(data, dict) else {}
        ids = data.get("enrollment_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 \
                or not all(isinstance(i, str) and len(i) == 10 and i.isdigit() for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Choose the students by their course IDs.")
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or any(ord(c) < 32 for c in request_id):
            raise ValueError("A request ID is required.")
        source = data.get("source", "roster")
        if source not in ("roster", "canvas"):
            raise ValueError("Unknown source.")
        reason = data.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 200 or any(ord(c) < 32 for c in reason):
            raise ValueError("Keep the reason to one line of 200 characters or fewer.")
        with self.lock():
            owner, section = self.resolve(group_id, number)
            changed = 0
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                for enrollment_id in ids:
                    if not db.execute("SELECT 1 FROM roster_memberships WHERE section_id=? AND enrollment_id=?", (section["id"], enrollment_id)).fetchone():
                        raise ValueError("Every student must be on this section's roster.")
                    if db.execute("SELECT 1 FROM roster_drops WHERE request_id=? AND section_id=? AND enrollment_id=?",
                                  (request_id, section["id"], enrollment_id)).fetchone():
                        continue
                    dropped = db.execute("SELECT 1 FROM roster_dropped WHERE section_id=? AND enrollment_id=?", (section["id"], enrollment_id)).fetchone()
                    if bool(dropped) == (action == "drop"):
                        continue
                    db.execute("INSERT INTO roster_drops VALUES (?,?,?,?,?,?,?,?,?)",
                               (str(uuid.uuid4()), section["id"], enrollment_id, action, source, reason.strip(), request_id, actor, timestamp()))
                    changed += 1
            result = self.get(group_id, number)
            result["changed"] = changed
            return result

    @staticmethod
    def record_marks(db, section_id, data, at):
        """Replaces the section's `roster_registrar_marks` with what its saved roster rows say now (`leaving_reason`). The caller
        holds the roster lock and an open write transaction."""
        db.execute("DELETE FROM roster_registrar_marks WHERE section_id=?", (section_id,))
        for record in data["students"].values():
            reason = leaving_reason(record["fields"])
            if reason:
                db.execute("INSERT INTO roster_registrar_marks VALUES (?,?,?,?)", (section_id, record["id"], reason, at))

    def decorate(self, response):
        response = self.accommodations.attach(response) if hasattr(self, "accommodations") else response
        return self.photos.attach(response) if getattr(self, "photos", None) else response

    @staticmethod
    def reserve_id(db, term_id, uin):
        existing = db.execute("SELECT id FROM student_enrollments WHERE term_id=? AND uin=?", (term_id, uin)).fetchone()
        if existing:
            return existing["id"]
        identity = db.execute("SELECT prefix FROM student_identities WHERE uin=?", (uin,)).fetchone()
        prefix = identity["prefix"] if identity else str(10000 + secrets.randbelow(90000))
        if not identity:
            db.execute("INSERT INTO student_identities VALUES (?,?,?)", (uin, prefix, timestamp()))
        # Unique index enforces uniqueness across the entire workspace. Pick uniformly
        # from remaining suffixes if repeated collisions occur, including a full prefix.
        for attempt in range(33):
            if attempt == 32:
                used = {int(row[0][5:]) for row in db.execute(
                    "SELECT id FROM student_enrollments WHERE id >= ? AND id <= ?", (prefix + "00000", prefix + "99999"))}
                available = [suffix for suffix in range(100000) if suffix not in used]
                if not available:
                    raise ValueError("This student ID prefix has no unused identifiers remaining.")
                suffix = secrets.choice(available)
            else:
                suffix = secrets.randbelow(100000)
            identifier = prefix + f"{suffix:05d}"
            added = db.execute("""INSERT INTO student_enrollments VALUES (?,?,?,?)
                                  ON CONFLICT(id) DO NOTHING""", (identifier, term_id, uin, timestamp())).rowcount
            if added:
                return identifier
        raise RuntimeError("No enrollment identifier was allocated.")

    def import_roster(self, group_id, number, parsed, filename, request_id=None):
        with self.lock():
            owner, section = self.resolve(group_id, number)
            with self.files(owner, write=True) as path:
                payload = self.read_payload(path, owner["roster_initialized"])
                data = payload["sections"].setdefault(section["id"], {"headers": [], "students": {}, "imports": []})
                if request_id:
                    prior = next((item for item in data["imports"] if item.get("request_id") == request_id), None)
                    if prior:
                        result = self.response(owner, section, payload)
                        result["last_import"] = prior
                        return self.decorate(result)
                added, updated = 0, 0
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    for student in parsed["students"]:
                        identifier = self.reserve_id(db, owner["id"], student["uin"])
                        previous = data["students"].get(identifier)
                        added += previous is None
                        updated += previous is not None
                        data["students"][identifier] = {
                            "id": identifier, "uin": student["uin"],
                            "fields": {**(previous["fields"] if previous else {}), **student["fields"]},
                        }
                        db.execute("INSERT OR IGNORE INTO roster_memberships VALUES (?,?)", (section["id"], identifier))
                # Identity reservations are durable before an ID appears in a payload.
                # This also means retries after a lost response retain precisely the same IDs.
                data["headers"] = list(dict.fromkeys([*data["headers"], *parsed["headers"]]))
                imported = {"at": timestamp(), "filename": Path(filename).name[:255], "rows": len(parsed["students"]),
                            "added": added, "updated": updated, "source_headers": parsed["source_headers"]}
                # Students on this section the file no longer lists: offered for Drop on the roster (`leaving`), never dropped here.
                listed = {student["uin"] for student in parsed["students"]}
                missing = sorted(record["id"] for record in data["students"].values() if record["uin"] not in listed)
                if missing:
                    imported["missing"] = missing
                # Students the instructor dropped stay dropped when the file still lists them (requirement, 2026-09-27); the import
                # says how many, and the roster offers Restore.
                with connect(self.path) as db:
                    dropped = {row[0] for row in db.execute("SELECT enrollment_id FROM roster_dropped WHERE section_id=?", (section["id"],))}
                    restored = {row[0] for row in db.execute("SELECT DISTINCT enrollment_id FROM roster_drops WHERE section_id=? AND action='restore'",
                                                             (section["id"],))}
                by_uin = {record["uin"]: record["id"] for record in data["students"].values()}
                still = sum(1 for student in parsed["students"]
                            if by_uin.get(student["uin"]) in dropped and not leaving_reason(student["fields"]))
                if still:
                    imported["still_dropped"] = still
                # A Q drop, withdrawal or drop the registrar marks on a row is a drop (requirement, 2026-10-25, of a Q-dropped student
                # the Assistant counted as current): the import records the roster's own Drop for it (`roster_drops`, source
                # `roster`), named in `registrar_dropped`; a student the instructor ever restored is left as they are.
                leaving = [{"id": record["id"], "reason": reason,
                            "name": next((str(v).strip() for k, v in record["fields"].items() if k.casefold() == "name"), "") or record["id"]}
                           for record in data["students"].values()
                           if (reason := leaving_reason(record["fields"])) and record["id"] not in dropped and record["id"] not in restored]
                leaving.sort(key=lambda student: (student["name"].casefold(), student["id"]))
                if leaving:
                    imported["registrar_dropped"] = leaving
                if request_id:
                    imported["request_id"] = request_id
                data["imports"].append(imported)
                atomic_json(path / "roster.json", payload)
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.record_marks(db, section["id"], data, imported["at"])
                    for student in leaving:
                        db.execute("INSERT OR IGNORE INTO roster_drops VALUES (?,?,?,?,?,?,?,?,?)",
                                   (str(uuid.uuid4()), section["id"], student["id"], "drop", "roster",
                                    f"{student['reason']} on the registrar's roster", f"registrar-{imported['at']}", "roster import", imported["at"]))
                result = self.marked(self.response(owner, section, payload))
            with connect(self.path) as db:
                db.execute("UPDATE course_terms SET roster_initialized=1 WHERE id=?", (owner["id"],))
            return self.decorate(result)
