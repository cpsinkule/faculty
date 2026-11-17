"""Course-wide canonicals and section print files on the verified storage engine.

Storage columns live on permanent owners, never on saved offering entries.
Both specializations stage every write on NVMe and archive only at nightly backup.
"""
import json
from contextlib import contextmanager
from contextlib import nullcontext
from pathlib import Path, PurePosixPath

from werkzeug.exceptions import Conflict
from catalog_migrations import apply_sql, migration_transaction
from course_storage import CourseStorage, StorageError, PENDING, digest_file, fsync_dir
from rosters import connect, term_key
from teach_inbox.storage import InboxStorage

STORAGE_COLUMNS = {
    "storage_status": "TEXT NOT NULL DEFAULT ''", "storage_snapshot": "TEXT NOT NULL DEFAULT ''",
    "storage_verified_at": "TEXT NOT NULL DEFAULT ''", "storage_initialized": "INTEGER NOT NULL DEFAULT 0",
    "storage_hot_required": "INTEGER NOT NULL DEFAULT 0", "storage_error": "TEXT NOT NULL DEFAULT ''",
}


# Migrations from here on run after the columns initialize adds, each in its own transaction; one that recreates a table names it,
# so that transaction suspends foreign-key enforcement and checks every reference before commit (catalog_migrations).
LATE_MIGRATIONS = "013"
REBUILDS = {"013_term_print_jobs": ("print_batches",), "014_jev_paper_basis": ("scan_unlabeled_plans",)}


def initialize(path):
    migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
    with connect(path) as db, migration_transaction(db):
        legacy = (bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assignments'").fetchone())
                  and not db.execute("SELECT 1 FROM schema_migrations WHERE namespace='document_identity' LIMIT 1").fetchone())
        for table in ("course_definitions", "course_sections"):
            present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, kind in STORAGE_COLUMNS.items():
                if name not in present:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

        for migration in migrations:
            if migration.stem < LATE_MIGRATIONS:
                apply_sql(db, "document_identity", migration.stem, migration.read_text(encoding="utf-8"), adopt=legacy)
        # Seating-linked print jobs and peel-off label output (added after the SQL migrations).
        for table, columns in (("print_batches", {"seating_chart_id": "TEXT REFERENCES seating_charts(id) ON DELETE SET NULL",
                                                   "labels_path": "TEXT", "labels_sha256": "TEXT",
                                                   "seat_marks": "INTEGER NOT NULL DEFAULT 0 CHECK(seat_marks IN (0, 1))",
                                                   "label_mode": "TEXT NOT NULL DEFAULT 'every_page' CHECK(label_mode IN ('every_page', 'first_page'))"}),
                               ("printed_copies", {"seat_label": "TEXT NOT NULL DEFAULT ''", "caption": "TEXT NOT NULL DEFAULT ''"}),
                               # The pages a scan's PDFs hold, counted before inspection starts, so progress reads "N of M".
                               ("document_decode_jobs", {"pages_total": "INTEGER"}),
                               # A run started with an assignment made from its scan: the version letters it looks for, and that it
                               # registers the papers it identified with confidence by itself.
                               ("scan_unlabeled_runs", {"versions": "TEXT NOT NULL DEFAULT ''",
                                                        "register_confident": "INTEGER NOT NULL DEFAULT 0 CHECK(register_confident IN (0, 1))"})):
            present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns.items():
                if name not in present:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        # One retained, verified label file per batch and label media; earlier Avery output is carried over.
        db.execute("""CREATE TABLE IF NOT EXISTS print_batch_labels(
            print_batch_id TEXT NOT NULL REFERENCES print_batches(id), media TEXT NOT NULL, path TEXT NOT NULL,
            sha256 TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(print_batch_id, media))""")
        db.execute("""INSERT OR IGNORE INTO print_batch_labels(print_batch_id, media, path, sha256, created_at)
            SELECT id, 'avery-5160', labels_path, labels_sha256, updated_at FROM print_batches WHERE labels_path IS NOT NULL""")
    for migration in migrations:
        if migration.stem >= LATE_MIGRATIONS:
            with connect(path) as db:
                pending = not db.execute("SELECT 1 FROM schema_migrations WHERE namespace='document_identity' AND version=?",
                                         (migration.stem,)).fetchone()
                with migration_transaction(db, rebuild=REBUILDS.get(migration.stem, ()) if pending else ()):
                    apply_sql(db, "document_identity", migration.stem, migration.read_text(encoding="utf-8"))


class DocumentStorage(InboxStorage):
    # Reuse Inbox's staged/active/nightly-only lifecycle, not its payload schema.
    metadata = staticmethod(CourseStorage.metadata)

    def row(self, identifier):
        with connect(self.database) as db:
            return self._row(db, identifier)

    def _row(self, db, identifier):
        row = db.execute(f"SELECT * FROM {self.table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise StorageError("Document owner not found.")
        result = dict(row)
        if self.table == "course_definitions":
            current = db.execute("SELECT 1 FROM course_terms WHERE definition_id=? AND is_current=1 LIMIT 1", (identifier,)).fetchone()
            result.update(definition_id=identifier, name=result["code"], sections=[], term="", term_other="", year=None)
        else:
            term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (result["term_id"],)).fetchone())
            current = term["is_current"]
            result.update({key: term[key] for key in ("definition_id", "code", "term", "term_other", "year")})
            result.update(name=f"{term['code']} · {term['term']} {term['year']} · Section {result['number']}", sections=[result["number"]])
        result.update(is_current=bool(current), deleted_at=None, multiple_sections=False, version=1)
        return result

    def rows(self):
        # Every owner on one connection; each statement is still its own autocommit read, as with a connection per owner.
        with connect(self.database) as db:
            ids = [row[0] for row in db.execute(f"SELECT id FROM {self.table} WHERE storage_status!=''")]
            return [self._row(db, identifier) for identifier in ids]

    def check_source(self, owner, source, inventory):
        if source is None:
            raise StorageError("Document snapshots require retained payloads.")
        with connect(self.database) as db:
            if self.table == "course_definitions":
                files = db.execute("""SELECT v.canonical_path path,v.canonical_sha256 sha256 FROM assignment_versions v
                    JOIN assignments a ON a.id=v.assignment_id WHERE a.definition_id=?
                    UNION ALL SELECT v.source_path,v.source_manifest_sha256 FROM assignment_versions v
                    JOIN assignments a ON a.id=v.assignment_id WHERE a.definition_id=?""", (owner["id"], owner["id"])).fetchall()
            else:
                files = db.execute("""SELECT output_path path,output_sha256 sha256 FROM print_batches WHERE section_id=? AND status='ready'
                    UNION ALL SELECT manifest_path,manifest_sha256 FROM print_batches WHERE section_id=? AND status='ready'""",
                    (owner["id"], owner["id"])).fetchall()  # a term's own jobs are checked with its roster (rosters.TermStorage)
            if self.table == "course_definitions" and db.execute("SELECT 1 FROM sqlite_master WHERE name='course_notes'").fetchone():
                # The course's lecture notes (course_notes): each uploaded PDF and every part cut from it.
                files = [*files, *db.execute("""SELECT path,source_sha256 sha256 FROM course_notes WHERE definition_id=?
                    UNION ALL SELECT p.path,p.sha256 FROM course_notes_parts p JOIN course_notes n ON n.id=p.notes_id
                    WHERE n.definition_id=?""", (owner["id"], owner["id"])).fetchall()]
            if self.table == "course_definitions" and db.execute("SELECT 1 FROM sqlite_master WHERE name='question_bank_uploads'").fetchone():
                # PDFs read for the course's question bank (question_bank): each upload as it was kept.
                files = [*files, *db.execute("SELECT path,source_sha256 sha256 FROM question_bank_uploads WHERE definition_id=?", (owner["id"],)).fetchall()]
            if self.table == "course_definitions" and db.execute("SELECT 1 FROM sqlite_master WHERE name='question_bank_pictures'").fetchone():
                # Pictures kept with the bank's questions: each file as it was kept.
                files = [*files, *db.execute("""SELECT p.path, p.sha256 FROM question_bank_pictures p JOIN question_bank_questions q ON q.id=p.question_id
                    WHERE q.definition_id=?""", (owner["id"],)).fetchall()]
            if self.table == "course_sections" and db.execute("SELECT 1 FROM sqlite_master WHERE name='absence_documents'").fetchone():
                # Documentation uploaded for a student's absence (absences): each file as it was kept.
                files = [*files, *db.execute("""SELECT d.path, d.sha256 FROM absence_documents d JOIN absences a ON a.id=d.absence_id
                    WHERE a.section_id=? AND d.kind='upload'""", (owner["id"],)).fetchall()]
            templates = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_artifacts'").fetchone():
                column = "definition_id" if self.table == "course_definitions" else "section_id"
                files = [*files, *db.execute(f"SELECT path,sha256 FROM assistant_artifacts WHERE {column}=?", (owner["id"],)).fetchall()]
            if self.table == "course_definitions" and db.execute("SELECT 1 FROM sqlite_master WHERE name='assignment_templates'").fetchone():
                templates = db.execute("""SELECT t.manifest_path path,t.manifest_sha256 sha256 FROM assignment_templates t
                    JOIN assignments a ON a.id=t.assignment_id WHERE a.definition_id=?""", (owner["id"],)).fetchall()
        for file in files:
            if self.payload_sha256(source, inventory, file["path"]) != file["sha256"]:
                raise StorageError("A document artifact failed checksum verification. Its working copy was retained.")
            if file["path"].endswith("sources.json") and not file["path"].startswith("assistant/"):
                manifest_path = source / "data" / file["path"]
                for item in json.loads(manifest_path.read_text()):
                    relative = (PurePosixPath(file["path"]).parent / "sources" / item["path"]).as_posix()
                    if self.payload_sha256(source, inventory, relative) != item["sha256"]:
                        raise StorageError("A retained source input failed verification.")
        for template in templates:
            manifest = json.loads(read_verified(source / "data", template["path"], template["sha256"]))
            for artifact in manifest["files"]:
                # Check the retained hashes, not a newly computed inventory
                # baseline, before permitting a verified copy or archive purge.
                read_verified(source / "data", artifact["path"], artifact["sha256"])


class DefinitionStorage(DocumentStorage):
    table = "course_definitions"
    directory = "definitions"


class SectionStorage(DocumentStorage):
    table = "course_sections"
    directory = "sections"


class DocumentFiles:
    def __init__(self, rosters):
        self.rosters = rosters
        self.storage = rosters.storage
        if self.storage:
            self.storage.definition_storage = DefinitionStorage(self.storage.config, self.storage.enforce_mounts)
            self.storage.section_storage = SectionStorage(self.storage.config, self.storage.enforce_mounts)

    def owner_lock(self, identifier):
        if not isinstance(identifier, str):
            raise ValueError("Choose a permanent document owner.")
        CourseStorage._identifier(identifier)
        return self.storage.lock(identifier) if self.storage else nullcontext()

    @contextmanager
    def files(self, level, identifier, write=False):
        """Caller holds roster lifecycle lock; this context owns the owner lock."""
        CourseStorage._identifier(identifier)
        if level == "term":
            # A print job owned by the term (migration 013) keeps its files in the term's verified storage, beside its roster.
            with connect(self.rosters.path) as db:
                owner = db.execute("SELECT * FROM course_terms WHERE id=?", (identifier,)).fetchone()
            if owner is None:
                raise StorageError("Document owner not found.")
            with self.rosters.files(dict(owner), write=write) as path:
                if path is None:
                    raise StorageError("The document payload is not initialized.")
                yield path
            return
        if level not in ("definition", "section"):
            raise ValueError("Invalid document owner level.")
        if self.storage:
            manager = getattr(self.storage, level + "_storage")
            owner = manager.row(identifier)
            if not owner["storage_status"]:
                if not write:
                    raise StorageError("The document payload is not initialized.")
                manager.update(identifier, storage_status="restore_pending")
            if not owner["storage_status"] or owner["storage_status"] in PENDING:
                manager.reconcile_course(identifier)
            with manager.course_files(identifier, write=write) as path:
                yield path
        else:
            path = self.rosters.path.parent / ("definitions" if level == "definition" else "sections") / identifier / "data"
            if path.resolve() != path:
                raise StorageError("Document storage is redirected.")
            if write:
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
            yield path


def synchronize(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='assignments'").fetchone():
        return
    # The owner's currentness is derived; reconcile its persisted working state.
    for table, query in (("course_definitions", "SELECT EXISTS(SELECT 1 FROM course_terms WHERE definition_id=? AND is_current=1)"),
                         ("course_sections", "SELECT t.is_current FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE s.id=?")):
        for row in db.execute(f"SELECT id,storage_status FROM {table} WHERE storage_status!=''").fetchall():
            desired = bool(db.execute(query, (row["id"],)).fetchone()[0])
            state = row["storage_status"]
            if desired and state in ("archived", "staged", "archive_pending"):
                db.execute(f"UPDATE {table} SET storage_status='restore_pending' WHERE id=?", (row["id"],))
            elif not desired and state in ("active", "sync_pending", "restore_pending"):
                db.execute(f"UPDATE {table} SET storage_status='archive_pending' WHERE id=?", (row["id"],))


def protect_references(db, previous, replacement):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='assignments'").fetchone():
        return
    others = [dict(row) for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL AND id<>?", (previous["id"],))]
    if replacement:
        others.append({**replacement, "sections": json.dumps(replacement["sections"])})
    covered = [row for row in others if row["definition_id"] == previous["definition_id"]]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_artifacts'").fetchone():
        # Generated files have the same retained owners as ordinary teaching documents.
        if not covered and db.execute("SELECT 1 FROM assistant_artifacts WHERE definition_id=?", (previous["definition_id"],)).fetchone():
            raise Conflict("This change would detach teaching files. Keep the course identity and archive it instead.")
        for artifact in db.execute("""SELECT t.*,s.number FROM assistant_artifacts a
                LEFT JOIN course_sections s ON s.id=a.section_id JOIN course_terms t ON t.id=COALESCE(a.term_id,s.term_id)
                WHERE t.definition_id=?""", (previous["definition_id"],)):
            if not any(term_key(row) == term_key(artifact) and (artifact["number"] is None or artifact["number"] in json.loads(row["sections"])) for row in covered):
                raise Conflict("This change would detach teaching files. Keep the term and section identity and archive it instead.")
    if not covered and db.execute("SELECT 1 FROM assignments WHERE definition_id=?", (previous["definition_id"],)).fetchone():
        raise Conflict("This change would detach assignments. Keep the course identity and archive it instead.")
    for batch in db.execute("""SELECT s.number,t.* FROM print_batches b LEFT JOIN course_sections s ON s.id=b.section_id
            JOIN course_terms t ON t.id=b.term_id WHERE t.definition_id=?""", (previous["definition_id"],)):
        # A job owned by the term (no section) needs its term kept; a section's job, its section too.
        if not any(term_key(row) == term_key(batch) and (batch["number"] is None or batch["number"] in json.loads(row["sections"]))
                   for row in covered):
            raise Conflict("This change would detach printed document provenance. Keep its section and archive it instead.")


def publish(path, content):
    """Immutable, retry-safe atomic publication inside an owner context."""
    import os
    import uuid
    from teach_inbox.service import sha, write_bytes
    if path.resolve() != path:
        raise StorageError("Document artifact path is redirected.")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists():
        if digest_file(path)["sha256"] != sha(content):
            raise StorageError("An immutable document artifact already exists with different content.")
        return
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        write_bytes(temporary, content)
        if digest_file(temporary)["sha256"] != sha(content):
            raise StorageError("Document publication failed verification.")
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_verified(root, relative, digest):
    from teach_inbox.service import sha
    path = root / relative
    if path.resolve() != path or not path.is_file():
        raise StorageError("The retained document is missing or redirected.")
    content = path.read_bytes()
    if sha(content) != digest:
        raise StorageError("The retained document failed checksum verification.")
    return content
