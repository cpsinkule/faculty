"""Lecture notes on the course page (requirement, 2026-09-30).

Owner: the COURSE DEFINITION (`course_definitions.id`): the notes serve every term and section of the course code, so they live
once in the definition's verified storage (`notes/<notes id>/original.pdf` and `part-<n>.pdf`), never copied into a term or a
section. An upload is saved at once, as the instructor asked: the PDF is read in the document sandbox, `sections.plan` finds where
each section begins (bookmarks and page headings, both kept and compared), the sandbox cuts each part out page for page, and the
original, the parts and the catalog record (migration `course_notes/001_notes`) are written together. Nothing is sent anywhere.

An EDITION is one PDF split by one rule (`UNIQUE(definition, checksum, rule)`, with an ID derived from them, so an interrupted
upload sent again finishes the same edition); uploading the same file again only records the upload. Every upload, removal and
restoration is an append-only event: an edition stands from its latest upload, a removal hides it until it is restored, and the
course page shows the newest edition that stands, earlier ones listed below it. Nothing is ever deleted.

Lock order: the sandbox runs before any lock; then the roster lifecycle lock, the definition's owner lock (its storage), the files
(immutable, retry-safe publication) and ONE `BEGIN IMMEDIATE` recording them. Reads of the record are catalog-only; a file is read
under the lifecycle and owner locks and checked against its checksum.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from document_identity import process
from document_identity.storage import publish, read_verified
from request_ids import valid_request_id
from rosters import connect

from .sections import RULE, plan

MAX_PDF = 100 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9-]{1,64}")
# Editions are named by their course, file and rule, so a retried upload reuses its own folder and files.
EDITIONS = uuid.UUID("6f0d3c5e-8a51-4a57-9c9e-2f4b7d1c0e33")


def file_name(text, fallback="notes"):
    """A download name that any system keeps: no path separators, controls or characters Windows refuses."""
    plain = unicodedata.normalize("NFKC", str(text or ""))
    plain = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", plain)
    plain = " ".join(plain.split()).strip(" .")[:150]
    return plain or fallback


def slug(text):
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:80] or "notes"


def label(part):
    return f"{part['number']} {part['title']}".strip()


def _events(db, definition_id):
    """Each edition's events, in their existing order, from one catalog read."""
    events = {}
    for row in db.execute("""SELECT e.notes_id, e.action, e.created_at FROM course_notes_events e
            JOIN course_notes n ON n.id=e.notes_id WHERE n.definition_id=? ORDER BY e.created_at, e.rowid""", (definition_id,)):
        events.setdefault(row["notes_id"], []).append(row)
    return events


def current_sections(db, definition_id):
    """{number: {title, chapter, chapter_title, url}} for the sections of the edition the course page shows (the newest that
    stands, as `view` chooses it). Catalog only. A section's NUMBER ("2.3") is the one reference that outlives a new upload of the
    notes (a part's position shifts with every edition), so a reader that names notes sections from outside this module, such as
    a class day's topics (SCHEDULING "CLASS SCHEDULE"), keeps the number and resolves it here at each read."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='course_notes'").fetchone():
        return {}
    shown = None
    events_by_id = _events(db, definition_id)
    for edition in db.execute("SELECT id, created_at FROM course_notes WHERE definition_id=? ORDER BY created_at, id", (definition_id,)).fetchall():
        events = events_by_id.get(edition[0], [])
        hiding = [event["action"] for event in events if event["action"] in ("removed", "restored")]
        if hiding and hiding[-1] == "removed":
            continue
        uploads = [event["created_at"] for event in events if event["action"] == "uploaded"]
        order = (uploads[-1] if uploads else edition[1], edition[1])
        if shown is None or order > shown[0]:
            shown = (order, edition[0])
    if not shown:
        return {}
    return {row["number"]: {"title": row["title"], "chapter": row["chapter"], "chapter_title": row["chapter_title"],
                            "url": f"/api/course-notes/{row['notes_id']}/parts/{row['position']}/{slug(label(row))}.pdf"}
            for row in db.execute("SELECT * FROM course_notes_parts WHERE notes_id=? AND kind='section' ORDER BY position", (shown[1],))}


class CourseNotesService:
    def __init__(self, rosters, documents):
        self.rosters, self.path, self.documents = rosters, rosters.path, documents
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "course_notes", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _definition(db, definition_id):
        if not isinstance(definition_id, str) or not IDENTIFIER.fullmatch(definition_id):
            raise NotFound("Course not found.")
        row = db.execute("SELECT id, code FROM course_definitions WHERE id=?", (definition_id,)).fetchone()
        if not row:
            raise NotFound("Course not found.")
        return row

    def view(self, definition_id, summary=False):
        """The course's notes: the edition shown on the course page (`current`), earlier ones that still stand (`earlier`, newest
        first) and removed ones (`removed`), each with its parts and where to open them. Catalog only. `summary` answers only
        what the course Home's line needs: the shown edition's title, counts and upload time, without parts."""
        with connect(self.path) as db:
            definition = self._definition(db, definition_id)
            editions = [dict(row) for row in db.execute("SELECT * FROM course_notes WHERE definition_id=? ORDER BY created_at, id", (definition_id,))]
            ids = [edition["id"] for edition in editions]
            marks = ",".join("?" * len(ids))
            events = _events(db, definition_id)
            parts = [dict(row) for row in db.execute(f"SELECT * FROM course_notes_parts WHERE notes_id IN ({marks}) ORDER BY notes_id, position", ids)] if ids else []
        parts_by_id = {}
        for part in parts:
            parts_by_id.setdefault(part["notes_id"], []).append(part)
        shown = []
        for edition in editions:
            mine = events.get(edition["id"], [])
            uploads = [event["created_at"] for event in mine if event["action"] == "uploaded"]
            hiding = [event["action"] for event in mine if event["action"] in ("removed", "restored")]
            recorded = json.loads(edition["summary_json"])
            shown.append({
                "id": edition["id"], "title": edition["title"], "source_name": edition["source_name"], "size": edition["source_size"],
                "pages": edition["page_count"], "rule": edition["rule"], "first_uploaded_at": edition["created_at"],
                "uploaded_at": uploads[-1] if uploads else edition["created_at"], "uploads": len(uploads),
                "removed": bool(hiding) and hiding[-1] == "removed", "notes": recorded.get("notes", []),
                "original_url": f"/api/course-notes/{edition['id']}/original/{slug(Path(edition['source_name']).stem)}.pdf",
                "parts": [self._part(part) for part in parts_by_id.get(edition["id"], [])],
            })
        for edition in shown:
            edition["sections"] = sum(1 for part in edition["parts"] if part["kind"] == "section")
        standing = sorted((edition for edition in shown if not edition["removed"]), key=lambda edition: (edition["uploaded_at"], edition["first_uploaded_at"]), reverse=True)
        if summary:
            current = standing[0] if standing else None
            return {"definition_id": definition["id"], "code": definition["code"], "current": current and {
                key: current[key] for key in ("id", "title", "source_name", "pages", "sections", "uploaded_at")}}
        return {"definition_id": definition["id"], "code": definition["code"], "rule": RULE, "max_bytes": MAX_PDF,
                "current": standing[0] if standing else None, "earlier": standing[1:],
                "removed": sorted((edition for edition in shown if edition["removed"]), key=lambda edition: edition["uploaded_at"], reverse=True)}

    @staticmethod
    def _part(row):
        return {"position": row["position"], "kind": row["kind"], "number": row["number"], "chapter": row["chapter"],
                "chapter_title": row["chapter_title"], "title": row["title"], "first_page": row["first_page"], "last_page": row["last_page"],
                "pages": row["last_page"] - row["first_page"] + 1, "found_by": row["found_by"], "topics": json.loads(row["topics_json"]),
                "size": row["size"], "url": f"/api/course-notes/{row['notes_id']}/parts/{row['position']}/{slug(label(row))}.pdf"}

    def read(self, notes_id, position=None):
        """(bytes, sha256, download name) of an edition's original PDF or one of its parts, checked against its checksum."""
        if not isinstance(notes_id, str) or not IDENTIFIER.fullmatch(notes_id):
            raise NotFound("Notes not found.")
        with connect(self.path) as db:
            edition = db.execute("SELECT * FROM course_notes WHERE id=?", (notes_id,)).fetchone()
            if not edition:
                raise NotFound("Notes not found.")
            if position is None:
                path, digest, name = edition["path"], edition["source_sha256"], file_name(edition["source_name"])
            else:
                part = db.execute("SELECT * FROM course_notes_parts WHERE notes_id=? AND position=?", (notes_id, position)).fetchone()
                if not part:
                    raise NotFound("That part of the notes does not exist.")
                path, digest, name = part["path"], part["sha256"], f"{file_name(label(part))}.pdf"
        with self.rosters.lock(), self.documents.files.files("definition", edition["definition_id"]) as root:
            content = read_verified(root, path, digest)
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return content, digest, name

    # -- Upload -------------------------------------------------------------------------------------------------------------
    def upload(self, definition_id, content, filename, request_id, actor="workspace"):
        """Save the notes and split them into their sections. The same request ID with the same file returns what it did (another
        file is a 409); the same file uploaded again records the upload and makes that edition the one shown."""
        valid_request_id(request_id)
        name = file_name(PurePosixPath(str(filename or "").replace("\\", "/")).name, "notes.pdf")[:200]
        if not isinstance(content, bytes) or not content:
            raise ValueError("Choose the notes PDF.")
        if len(content) > MAX_PDF:
            raise ValueError("That file is larger than 100 MB.")
        if not content.startswith(b"%PDF-"):
            if name.lower().endswith((".docx", ".doc")):
                raise ValueError("Upload the notes as a PDF: in Word, choose File → Save As (or Export) → PDF, then upload that file.")
            raise ValueError("Upload the notes as a PDF.")
        source = hashlib.sha256(content).hexdigest()
        with connect(self.path) as db:
            self._definition(db, definition_id)
            done = self._repeated(db, request_id, source, definition_id)
            if done:
                return self._answer(definition_id, done, repeated=True)
            existing = db.execute("SELECT id FROM course_notes WHERE definition_id=? AND source_sha256=? AND rule=?", (definition_id, source, RULE)).fetchone()
        if existing:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                done = self._repeated(db, request_id, source, definition_id)
                if not done:
                    self._event(db, existing["id"], "uploaded", request_id, actor)
                    latest = db.execute("""SELECT action FROM course_notes_events WHERE notes_id=? AND action IN ('removed','restored')
                                           ORDER BY created_at DESC, rowid DESC LIMIT 1""", (existing["id"],)).fetchone()
                    if latest and latest["action"] == "removed":  # the upload shows a removed edition again, as the answer says
                        self._event(db, existing["id"], "restored", str(uuid.uuid5(EDITIONS, f"{request_id}:restored")), actor)
                    db.commit()
            return self._answer(definition_id, existing["id"], repeated=bool(done), again=not done)

        # The sandbox reads and cuts the PDF before anything is locked or written.
        evidence = process.run("notes_outline", content)
        result = plan(evidence)
        title = " ".join(str(evidence.get("title") or "").split())[:200] or file_name(Path(name).stem, "Lecture notes")
        manifest = {"title": title, "parts": [{"position": part["position"], "first_page": part["first_page"], "last_page": part["last_page"],
                                               "label": label(part), "topics": part["topics"]} for part in result["parts"]]}
        outputs = {item["position"]: item for item in process.run("notes_split", content, manifest)}
        if set(outputs) != {part["position"] for part in result["parts"]}:
            raise ValueError("The notes could not be split into their parts.")
        for part in result["parts"]:
            if outputs[part["position"]]["pages"] != part["last_page"] - part["first_page"] + 1:
                raise ValueError("A part of the notes came out with the wrong number of pages.")
        notes_id = str(uuid.uuid5(EDITIONS, f"{definition_id}:{source}:{RULE}"))
        base = f"notes/{notes_id}"
        with self.rosters.lock(), self.documents.files.files("definition", definition_id, write=True) as root:
            publish(root / base / "original.pdf", content)
            stored = {}
            for part in result["parts"]:
                pdf = outputs[part["position"]]["pdf"]
                path = f"{base}/part-{part['position']}.pdf"
                publish(root / path, pdf)
                stored[part["position"]] = (path, hashlib.sha256(pdf).hexdigest(), len(pdf))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                done = self._repeated(db, request_id, source, definition_id)
                if done:
                    return self._answer(definition_id, done, repeated=True)
                if not db.execute("SELECT 1 FROM course_notes WHERE id=?", (notes_id,)).fetchone():
                    summary = {"notes": result["notes"], "sections": sum(1 for part in result["parts"] if part["kind"] == "section"),
                               "bookmarks": len(evidence.get("toc", []))}
                    db.execute("""INSERT INTO course_notes(id,definition_id,source_name,source_sha256,source_size,path,page_count,title,rule,summary_json,actor,created_at)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (notes_id, definition_id, name, source, len(content), f"{base}/original.pdf", result["pages"], title, RULE,
                                json.dumps(summary, separators=(",", ":")), actor, timestamp()))
                    for part in result["parts"]:
                        path, digest, size = stored[part["position"]]
                        db.execute("""INSERT INTO course_notes_parts(notes_id,position,kind,number,chapter,chapter_title,title,first_page,last_page,found_by,topics_json,path,sha256,size)
                                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (notes_id, part["position"], part["kind"], part["number"], part["chapter"], part["chapter_title"], part["title"],
                                    part["first_page"], part["last_page"], part["found_by"], json.dumps(part["topics"], separators=(",", ":")),
                                    path, digest, size))
                self._event(db, notes_id, "uploaded", request_id, actor)
                db.commit()
        return self._answer(definition_id, notes_id)

    @staticmethod
    def _repeated(db, request_id, source, definition_id):
        """The edition an earlier request with this ID uploaded (the same file), or None; another file under the ID is a 409."""
        row = db.execute("""SELECT e.notes_id, e.action, n.source_sha256, n.definition_id FROM course_notes_events e JOIN course_notes n ON n.id=e.notes_id
                            WHERE e.request_id=?""", (request_id,)).fetchone()
        if not row:
            return None
        if row["action"] != "uploaded" or row["source_sha256"] != source or row["definition_id"] != definition_id:
            raise Conflict("This request ID already belongs to another upload. Close the window and upload again.")
        return row["notes_id"]

    @staticmethod
    def _event(db, notes_id, action, request_id, actor):
        db.execute("INSERT INTO course_notes_events(id,notes_id,action,request_id,actor,created_at) VALUES (?,?,?,?,?,?)",
                   (str(uuid.uuid4()), notes_id, action, request_id, actor, timestamp()))

    def _answer(self, definition_id, notes_id, repeated=False, again=False):
        return {**self.view(definition_id), "uploaded": {"id": notes_id, "repeated": repeated, "again": again}}

    # -- Remove and restore ---------------------------------------------------------------------------------------------------
    def _change(self, notes_id, data, action, actor):
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        if action == "removed" and data.get("confirmation") != "remove":
            raise ValueError("Confirm the removal: the notes leave the course page only when you choose Remove.")
        if not isinstance(notes_id, str) or not IDENTIFIER.fullmatch(notes_id):
            raise NotFound("Notes not found.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            edition = db.execute("SELECT id, definition_id FROM course_notes WHERE id=?", (notes_id,)).fetchone()
            if not edition:
                raise NotFound("Notes not found.")
            prior = db.execute("SELECT notes_id, action FROM course_notes_events WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if (prior["notes_id"], prior["action"]) != (notes_id, action):
                    raise Conflict("This request ID already belongs to another change.")
                return {**self.view(edition["definition_id"]), "repeated": True}
            latest = db.execute("""SELECT action FROM course_notes_events WHERE notes_id=? AND action IN ('removed','restored')
                                   ORDER BY created_at DESC, rowid DESC LIMIT 1""", (notes_id,)).fetchone()
            removed = bool(latest) and latest["action"] == "removed"
            if action == "removed" and removed:
                raise Conflict("These notes were already removed from the course page.")
            if action == "restored" and not removed:
                raise Conflict("These notes are on the course page already.")
            self._event(db, notes_id, action, request_id, actor)
            db.commit()
        return {**self.view(edition["definition_id"]), "repeated": False}

    def remove(self, notes_id, data, actor="workspace"):
        """Take an edition off the course page. Its files and record stay, and Restore brings it back."""
        return self._change(notes_id, data, "removed", actor)

    def restore(self, notes_id, data, actor="workspace"):
        return self._change(notes_id, data, "restored", actor)
