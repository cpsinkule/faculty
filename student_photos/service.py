"""Student photos (requirement, 2026-09-29; AGENTS.md "Student photos").

A printed photo roster (Howdy's "roster with photos" page, one PDF per section) is read in the document sandbox
(`document_identity/process.py` operation `roster_photos`): its embedded photos and the caption under each. Each caption is
matched to the section's roster by NAME, exactly (accents, case, punctuation and word order ignored), and only where it names one
student and no other caption does; anything else is listed and left alone, never guessed. Preview writes nothing; the import names
its fingerprint, a request ID and `confirmation: "import"`, all checked here.

Owner: the student's TERM ENROLLMENT (`student_enrollments.id`); rows are append-only revisions in the catalog (migration
`student_photos/001_photos`) with one receipt per import. Photos are education records: they leave only by the private route
(`GET /api/student-photos/<enrollment id>`), are in no JSON export, and JSON carries only a short checksum prefix so a page can
tell whether a student has one and refresh it when it changes. The source PDF is not kept; its checksum is on the receipt.

Lock order: the roster lifecycle lock, the verified roster read, the sandboxed extraction, then ONE `BEGIN IMMEDIATE` that checks
each enrollment is still a member of the section.
"""
import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from document_identity import process
from request_ids import valid_request_id
from rosters import connect

MAX_PDF = 30 * 1024 * 1024
PREFIX = 12


def name_key(text):
    """A name as words without case, accents or punctuation, in sorted order: "Ames, Renata A." and "Renata A Ames" agree."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", plain).split()))


def _match_key(text):
    """name_key, or for a name with no Latin letters (all non-Latin, digits, punctuation) its own words so that only identical text agrees; '' names nobody."""
    key = name_key(text)
    if key:
        return key
    words = sorted(re.findall(r"\w+", unicodedata.normalize("NFKC", str(text or "")).casefold()))
    return "\0" + " ".join(words) if words else ""


def student_name(record):
    fields = {str(k).casefold().strip(): str(v).strip() for k, v in (record.get("fields") or {}).items()}
    return fields.get("name") or " ".join(filter(None, [fields.get("first name"), fields.get("last name")]))


def match(photos, students):
    """[(photo, student)] for captions naming exactly one roster student that no other caption names; and what did not match:
    ({captions no student has, or that two students share}, {students no caption names}, {students two captions name})."""
    by_name = {}
    for student in students:
        by_name.setdefault(_match_key(student["name"]), []).append(student)
    named = {}
    for photo in photos:
        named.setdefault(_match_key(photo["caption"]), []).append(photo)
    pairs, unmatched, ambiguous = [], [], []
    for photo in photos:
        key = _match_key(photo["caption"])
        owners, same = by_name.get(key, []), named[key]
        if not key:
            unmatched.append(photo["caption"])
        elif len(owners) == 1 and len(same) == 1:
            pairs.append((photo, owners[0]))
        elif len(same) > 1 and owners:
            ambiguous.append(photo["caption"])
        else:
            unmatched.append(photo["caption"])
    matched = {student["id"] for _, student in pairs}
    missing = sorted((s["name"] for s in students if s["id"] not in matched), key=str.casefold)
    return pairs, sorted(unmatched, key=str.casefold), missing, sorted(set(ambiguous), key=str.casefold)


class StudentPhotoService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "student_photos", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ----------------------------------------------------------------------------------------------------------
    def latest(self, term_id, db=None):
        """{enrollment_id: checksum prefix} of every student of the term who has a photo. Catalog only; no image leaves."""
        if db is None:
            with connect(self.path) as db:
                return self.latest(term_id, db)
        return {row[0]: row[1][:PREFIX] for row in db.execute(
            """SELECT p.enrollment_id, p.sha256 FROM student_photos p
               WHERE p.term_id=? AND p.revision=(SELECT MAX(revision) FROM student_photos WHERE enrollment_id=p.enrollment_id)""", (term_id,))}

    def attach(self, response):
        """Adds `photo` (a checksum prefix) to each student of a roster response that has one; used by the roster view."""
        ids = [student.get("id") for student in response.get("students", []) if student.get("id")]
        if not ids or not response.get("term_id"):
            return response
        photos = self.latest(response["term_id"])
        for student in response["students"]:
            if student.get("id") in photos:
                student["photo"] = photos[student["id"]]
        return response

    def image(self, enrollment_id):
        """(bytes, sha256) of the student's latest photo, or NotFound."""
        if not isinstance(enrollment_id, str) or not re.fullmatch(r"[0-9]{10}", enrollment_id):
            raise NotFound("No photo.")
        with connect(self.path) as db:
            row = db.execute("SELECT image, sha256 FROM student_photos WHERE enrollment_id=? ORDER BY revision DESC LIMIT 1", (enrollment_id,)).fetchone()
        if not row:
            raise NotFound("No photo.")
        return bytes(row["image"]), row["sha256"]

    # -- Import --------------------------------------------------------------------------------------------------------------
    def _plan(self, group_id, number, content, filename):
        if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
            raise ValueError("Upload the photo roster as a PDF.")
        if len(content) > MAX_PDF:
            raise ValueError("That PDF is larger than 30 MB.")
        roster = self.rosters.get(group_id, number)  # verified payload read under the lifecycle lock
        students = [{"id": s["id"], "name": student_name(s), "dropped": bool(s.get("dropped"))} for s in roster["students"]]
        photos = process.run("roster_photos", content)
        pairs, unmatched, missing, ambiguous = match(photos, students)
        with connect(self.path) as db:
            latest = {row[0]: row[1] for row in db.execute(
                "SELECT p.enrollment_id, p.sha256 FROM student_photos p WHERE p.term_id=? AND p.revision=(SELECT MAX(revision) FROM student_photos WHERE enrollment_id=p.enrollment_id)",
                (roster["term_id"],))}
        entries = []
        for photo, student in pairs:
            digest = hashlib.sha256(photo["jpeg"]).hexdigest()
            state = "new" if student["id"] not in latest else "same" if latest[student["id"]] == digest else "changed"
            entries.append({"enrollment_id": student["id"], "sha256": digest, "state": state, "photo": photo})
        source = hashlib.sha256(content).hexdigest()
        fingerprint = hashlib.sha256(json.dumps({"source": source, "section": roster["section_id"],
                                                 "photos": sorted((e["enrollment_id"], e["sha256"]) for e in entries)}, separators=(",", ":")).encode()).hexdigest()
        counts = {state: sum(1 for e in entries if e["state"] == state) for state in ("new", "changed", "same")}
        return roster, entries, {"section": roster["section"], "section_id": roster["section_id"], "term_id": roster["term_id"], "source_name": (filename or "photo-roster.pdf")[:200],
                                 "source_sha256": source, "fingerprint": fingerprint, "photos_in_file": len(photos), "matched": len(entries), **counts,
                                 "unmatched_captions": unmatched, "ambiguous_captions": ambiguous, "students_without_photo": missing}

    def preview(self, group_id, number, content, filename=""):
        """What importing this PDF into the section would do. Writes nothing."""
        with self.rosters.lock():
            return self._plan(group_id, number, content, filename)[2]

    def apply(self, group_id, number, content, filename, data, actor="workspace"):
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        if data.get("confirmation") != "import":
            raise ValueError("Confirm the import: photos are only saved when you choose Import.")
        with self.rosters.lock():
            roster, entries, summary = self._plan(group_id, number, content, filename)
            if data.get("fingerprint") != summary["fingerprint"]:
                raise Conflict("The photo roster or the section's students changed since the preview. Preview it again.")
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                done = db.execute("SELECT request_hash, summary_json FROM student_photo_imports WHERE request_id=?", (request_id,)).fetchone()
                if done:
                    if done["request_hash"] != summary["fingerprint"]:
                        raise Conflict("This import was already made with a different file. Close the window and start again.")
                    return {**json.loads(done["summary_json"]), "repeated": True}
                members = {row[0] for row in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=?", (roster["section_id"],))}
                if any(e["enrollment_id"] not in members for e in entries):
                    raise Conflict("A student left the section since the preview. Preview it again.")
                stored = {key: value for key, value in summary.items() if key != "fingerprint"}
                import_id = str(uuid.uuid4())
                db.execute("INSERT INTO student_photo_imports(id,term_id,section_id,request_id,request_hash,source_name,source_sha256,summary_json,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (import_id, roster["term_id"], roster["section_id"], request_id, summary["fingerprint"], summary["source_name"], summary["source_sha256"],
                            json.dumps(stored, separators=(",", ":")), actor, timestamp()))
                for entry in entries:
                    if entry["state"] == "same":
                        continue
                    revision = 1 + (db.execute("SELECT MAX(revision) FROM student_photos WHERE enrollment_id=?", (entry["enrollment_id"],)).fetchone()[0] or 0)
                    photo = entry["photo"]
                    db.execute("INSERT INTO student_photos(id,enrollment_id,term_id,revision,import_id,sha256,content_type,width,height,image,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (str(uuid.uuid4()), entry["enrollment_id"], roster["term_id"], revision, import_id, entry["sha256"], "image/jpeg",
                                photo["width"], photo["height"], photo["jpeg"], timestamp()))
                db.commit()
            return {**stored, "repeated": False}
