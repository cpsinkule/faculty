"""Inbox audit records and scoped payload owners using the verified disk lifecycle."""
import json
import re

from werkzeug.exceptions import Conflict

from course_codes import split_course_code
from course_storage import CourseStorage, PENDING, StorageError, atomic_json
from rosters import connect


class InboxStorage(CourseStorage):
    table = "inbox_messages"
    directory = "inbox"
    hot_states = ("active", "staged")
    stage_archived_writes = True

    def _restore(self, message):
        super()._restore(message)
        if not message["is_current"]:
            self.update(message["id"], storage_status="staged")

    def reconcile_course(self, identifier):
        # Request/background reconciliation may restore/read HDD data, but only
        # the nightly coordinator publishes Inbox snapshots or final archives.
        with self.lock(identifier):
            message = self.row(identifier)
            status = message["storage_status"]
            if status in ("archive_pending", "restore_pending", "sync_pending", *self.hot_states):
                target = self.hot_path(identifier)
                if not target.exists():
                    if message["storage_hot_required"]:
                        raise StorageError("The Inbox NVMe working copy is missing. Its prior backup was not substituted for newer data.")
                    self._restore(message)
                elif not message["storage_hot_required"]:
                    # Validate a restore interrupted just after its atomic rename.
                    self._restore(message)
                if not target.is_dir() or target.is_symlink():
                    raise StorageError("The Inbox NVMe working directory is missing or redirected.")
                atomic_json(target / "course.json", self.metadata(message))
                self.update(identifier, storage_status="active" if message["is_current"] else "staged", storage_error="")
                return
            if status == "purge_pending":
                self._purge(message)  # Resume only an already verified final copy.

    def backup_eligible(self, message):
        return bool(not message["deleted_at"] and message["storage_status"] in (*self.hot_states, *PENDING))

    def finish_backup(self, message):
        if not message["is_current"]:
            self.update(message["id"], storage_status="purge_pending")
            self._purge(self.row(message["id"]))

    def check_source(self, message, source, files):
        if source is None:
            raise StorageError("An Inbox snapshot must retain its original submission.")
        if self.payload_sha256(source, files, message["original_path"]) != message["original_sha256"]:
            raise StorageError("The original submission failed checksum verification. Its working copy was retained.")
        with self.connect() as db:
            artifacts = db.execute("SELECT path,sha256 FROM inbox_artifacts WHERE message_id=?", (message["id"],)).fetchall()
        for artifact in artifacts:
            if self.payload_sha256(source, files, artifact["path"]) != artifact["sha256"]:
                raise StorageError("An Inbox artifact failed checksum verification. Its working copy was retained.")

    @staticmethod
    def metadata(message):
        metadata = CourseStorage.metadata(message)
        if isinstance(metadata.get("sections"), str):
            metadata["sections"] = json.loads(metadata["sections"])
        return {**metadata, **{key: message[key] for key in (
            "source", "sender", "received_at", "scope_level", "scope_id", "term_id", "section_id", "software_version", "original_path", "original_sha256"
        ) if key in message}}


def initialize(path):
    with connect(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS inbox_messages (
                id TEXT PRIMARY KEY NOT NULL, source TEXT NOT NULL, sender TEXT NOT NULL,
                received_at TEXT NOT NULL, software_version TEXT NOT NULL, authenticated INTEGER NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL, original_path TEXT NOT NULL, original_sha256 TEXT NOT NULL,
                scope_level TEXT, scope_id TEXT, definition_id TEXT REFERENCES course_definitions(id),
                term_id TEXT REFERENCES course_terms(id), section_id TEXT REFERENCES course_sections(id),
                name TEXT NOT NULL, code TEXT NOT NULL DEFAULT '', term TEXT NOT NULL DEFAULT '',
                term_other TEXT NOT NULL DEFAULT '', year INTEGER, sections TEXT NOT NULL DEFAULT '[]',
                multiple_sections INTEGER NOT NULL DEFAULT 0, is_current INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
                storage_status TEXT NOT NULL DEFAULT '', storage_snapshot TEXT NOT NULL DEFAULT '',
                storage_verified_at TEXT NOT NULL DEFAULT '', storage_initialized INTEGER NOT NULL DEFAULT 0,
                storage_hot_required INTEGER NOT NULL DEFAULT 0, storage_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS inbox_jobs (
                id TEXT PRIMARY KEY NOT NULL, message_id TEXT NOT NULL UNIQUE REFERENCES inbox_messages(id),
                action TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','needs_review','cancelled')),
                parameters_json TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
                approved_hash TEXT, approved_at TEXT, approved_by TEXT
            );
            CREATE INDEX IF NOT EXISTS inbox_queue ON inbox_jobs(status, created_at, id);
            CREATE TABLE IF NOT EXISTS inbox_artifacts (
                id TEXT PRIMARY KEY NOT NULL, message_id TEXT NOT NULL REFERENCES inbox_messages(id),
                job_id TEXT NOT NULL REFERENCES inbox_jobs(id), filename TEXT NOT NULL, path TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('input','output')), size INTEGER NOT NULL,
                sha256 TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(message_id, path)
            );
            CREATE TABLE IF NOT EXISTS inbox_events (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES inbox_jobs(id),
                at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL, software_version TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS inbox_events_job ON inbox_events(job_id, id);
            -- Removing a submission from the Inbox is a recorded, reversible decision, never an erasure: the retained original,
            -- its artifacts, its audit trail and whatever its processor applied all stay. A message is removed while it has a row
            -- that was not restored.
            CREATE TABLE IF NOT EXISTS inbox_deletions (
                id INTEGER PRIMARY KEY, message_id TEXT NOT NULL REFERENCES inbox_messages(id), actor TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, restored_at TEXT, restored_by TEXT
            );
            CREATE INDEX IF NOT EXISTS inbox_deletions_message ON inbox_deletions(message_id, restored_at);
            CREATE INDEX IF NOT EXISTS inbox_artifacts_job ON inbox_artifacts(job_id);
            CREATE INDEX IF NOT EXISTS inbox_scope ON inbox_messages(definition_id, term_id, section_id);
            CREATE TABLE IF NOT EXISTS inbox_codes (
                id TEXT PRIMARY KEY NOT NULL, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                command_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                used_message_id TEXT REFERENCES inbox_messages(id), revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS inbox_api_keys (
                id TEXT PRIMARY KEY NOT NULL, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS inbox_worker (
                id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL
            );
        """)
        # Workspace-level intake authorization, independent of course ownership.
        # Older receipts retain their original policy; only new public-alias mail
        # starts with this gate. The lifecycle lock serializes additive migration.
        if "instructor_review_required" not in {row["name"] for row in db.execute("PRAGMA table_info(inbox_messages)")}:
            db.execute("ALTER TABLE inbox_messages ADD COLUMN instructor_review_required INTEGER NOT NULL DEFAULT 0")


def scope(db, target, visible=True):
    if not target:
        raise ValueError("Choose the course, term/year, and section that owns this job.")
    level, identifier = target["level"], target["id"]
    section = None
    if level == "course":
        definition = db.execute("SELECT * FROM course_definitions WHERE id=?", (identifier,)).fetchone()
        term = None
    else:
        if level == "section":
            section = db.execute("SELECT * FROM course_sections WHERE id=?", (identifier,)).fetchone()
            if not section:
                raise ValueError("The selected section does not exist.")
            identifier = section["term_id"]
        term = db.execute("SELECT * FROM course_terms WHERE id=?", (identifier,)).fetchone()
        definition = db.execute("SELECT * FROM course_definitions WHERE id=?", (term["definition_id"],)).fetchone() if term else None
    if not definition:
        raise ValueError("The selected course owner does not exist.")
    offerings = db.execute("SELECT * FROM courses WHERE definition_id=? AND deleted_at IS NULL", (definition["id"],)).fetchall()
    if term:
        offerings = [row for row in offerings if (row["term"], row["term_other"], row["year"]) == (term["term"], term["term_other"], term["year"])]
    if section:
        offerings = [row for row in offerings if section["number"] in json.loads(row["sections"])]
    if visible and not offerings:
        raise ValueError("This course owner has no saved offering. Select an existing course and term.")
    term_label = f"{term['term_other'] if term['term'] == 'Other' else term['term']} {term['year']}" if term else "Across all terms"
    label = f"{definition['code']} · {term_label}" + (f" · Section {section['number']}" if section else "")
    return {"level": level, "id": target["id"], "label": label, "definition_id": definition["id"],
            "term_id": term["id"] if term else None, "section_id": section["id"] if section else None,
            "code": definition["code"], "term": term["term"] if term else "", "term_other": term["term_other"] if term else "",
            "year": term["year"] if term else None, "sections": [section["number"]] if section else json.loads(term["sections"]) if term else [],
            "is_current": any(row["is_current"] for row in offerings)}


def resolve(db, command):
    if command.get("target"):
        return scope(db, command["target"])
    code = command.get("course", "")
    if not isinstance(code, str):
        raise ValueError("Course must be a course code or code-section shorthand.")
    section = command.get("section")
    # Accept the user's MATH140-501 shorthand without interpreting arbitrary subjects.
    match = re.fullmatch(r"([A-Za-z]+\s*[0-9]+)-([0-9]+)", code.strip())
    if match:
        code = match[1]
        if section is not None and section != int(match[2]):
            raise ValueError("The course shorthand and section field disagree.")
        section = int(match[2])
    parts = split_course_code(code)
    if not parts:
        raise ValueError("Select a course or supply a complete course code.")
    code = f"{parts[0]} {parts[1]}"
    if section is not None and (type(section) is not int or section < 1):
        raise ValueError("Section must be a positive whole number.")
    year = command.get("year")
    if year is not None and (type(year) is not int or not 1000 <= year <= 9999):
        raise ValueError("Year must be a four-digit whole number.")
    matches = []
    for row in db.execute("SELECT t.* FROM course_terms t JOIN course_definitions d ON d.id=t.definition_id WHERE d.code=?", (code,)):
        if year is not None and row["year"] != year:
            continue
        if command.get("term") is not None and row["term"] != command["term"]:
            continue
        if command.get("term_other") is not None and row["term_other"] != command["term_other"]:
            continue
        target = {"level": "term", "id": row["id"]}
        if section is not None:
            child = db.execute("SELECT id FROM course_sections WHERE term_id=? AND number=?", (row["id"], section)).fetchone()
            if not child:
                continue
            target = {"level": "section", "id": child["id"]}
        try:
            matches.append(scope(db, target))
        except ValueError:
            continue
    # Never silently choose a year/current term when a shorthand is ambiguous.
    if len(matches) != 1:
        raise ValueError("Course context is ambiguous or missing. Select an explicit course, term/year, and section.")
    return matches[0]


def synchronize(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_messages'").fetchone():
        return
    for row in db.execute("SELECT * FROM inbox_messages WHERE scope_id IS NOT NULL").fetchall():
        desired = scope(db, {"level": row["scope_level"], "id": row["scope_id"]}, visible=False)["is_current"]
        if bool(row["is_current"]) == desired:
            continue
        status = row["storage_status"]
        if status in PENDING:
            raise Conflict("Inbox files are being moved. Wait for the move to finish before changing this course.")
        if status:
            status = "restore_pending" if desired else "archive_pending"
        db.execute("UPDATE inbox_messages SET is_current=?,storage_status=?,version=version+1 WHERE id=?", (desired, status, row["id"]))


def protect_references(db, previous, replacement):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_messages'").fetchone():
        return
    messages = db.execute("SELECT * FROM inbox_messages WHERE definition_id=?", (previous["definition_id"],)).fetchall()
    if not messages:
        return
    others = [dict(row) for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL AND id<>?", (previous["id"],))]
    if replacement:
        others.append({**replacement, "sections": json.dumps(replacement["sections"])})
    for message in messages:
        covered = [row for row in others if row["definition_id"] == message["definition_id"]]
        if message["term_id"]:
            covered = [row for row in covered if (row["term"], row["term_other"], row["year"]) == (message["term"], message["term_other"], message["year"])]
        if message["section_id"]:
            number = json.loads(message["sections"])[0]
            covered = [row for row in covered if number in json.loads(row["sections"])]
        if not covered:
            raise Conflict("This change would detach retained Inbox files. Keep their course identity and mark the course as not current to archive it.")
