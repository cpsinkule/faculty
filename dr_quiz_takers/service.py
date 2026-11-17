"""DR quiz takers (requirement, 2026-10-15; ACCOMMODATIONS "DR QUIZ TAKERS"): one flag per TERM ENROLLMENT, kept as append-only changes
naming who and when (the latest counts). Reads are catalog-only; a write takes the lifecycle lock, then ONE `BEGIN IMMEDIATE`."""
import json
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect
from student_notes.service import StudentNoteService, _request


def states(db, ids):  # each enrollment's flag from one query; none recorded is off at revision 0
    found = {r["enrollment_id"]: {"on": bool(r["taker"]), "revision": r["revision"], "set_at": r["created_at"], "set_by": r["actor"]}
             for r in db.execute("""SELECT * FROM dr_quiz_taker_changes c WHERE enrollment_id IN (SELECT value FROM json_each(?)) AND
                 revision=(SELECT MAX(revision) FROM dr_quiz_taker_changes WHERE enrollment_id=c.enrollment_id)""", (json.dumps(ids),))}
    return {id: found.get(id, {"on": False, "revision": 0, "set_at": None, "set_by": None}) for id in ids}


def offered(db, ids):
    """The enrollments the checkbox is offered to (requirement, 2026-10-16): a granted accommodation on a current record in any of their sections (the `active` rule) or a DR booking."""
    from accommodations import AccommodationService  # accommodations imports this module
    found = {r["enrollment_id"] for r in db.execute("""SELECT a.enrollment_id, r.snapshot_json FROM student_accommodations a
              JOIN accommodation_revisions r ON r.id=a.revision_id WHERE a.enrollment_id IN (SELECT value FROM json_each(?))""",
              (json.dumps(ids),)) if AccommodationService.active(json.loads(r["snapshot_json"]))}
    found |= {r[0] for r in db.execute("SELECT DISTINCT enrollment_id FROM dr_exam_bookings WHERE enrollment_id IN (SELECT value FROM json_each(?))",
                                       (json.dumps(ids),))}
    return found


class DrQuizTakerService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "dr_quiz_takers", migration.stem, migration.read_text(encoding="utf-8"))

    def student(self, enrollment_id, db=None):
        if db is None:
            with connect(self.path) as db:
                return self.student(enrollment_id, db)
        enrollment = StudentNoteService._enrollment(db, enrollment_id)
        return {"enrollment_id": enrollment["id"], "term_id": enrollment["term_id"], "dr_quiz_taker": states(db, [enrollment["id"]])[enrollment["id"]],
                "offered": enrollment["id"] in offered(db, [enrollment["id"]])}

    def save(self, enrollment_id, data, actor="workspace"):
        """Sets (never toggles) the flag: the value it has records nothing; a repeated request ID answers again, 409 for the other value."""
        if not isinstance(data, dict) or set(data) - {"on", "request_id"} or type(data.get("on")) is not bool:
            raise ValueError("Send on (true or false) and a request ID only.")
        on, request_id = data["on"], _request(data.get("request_id"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.student(enrollment_id, db)
            done = db.execute("SELECT taker FROM dr_quiz_taker_changes WHERE enrollment_id=? AND request_id=?", (enrollment_id, request_id)).fetchone()
            if done and bool(done["taker"]) != on:
                raise Conflict("This request ID already recorded the other value. Try again.")
            if not done and current["dr_quiz_taker"]["on"] != on:
                db.execute("INSERT INTO dr_quiz_taker_changes VALUES (?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), current["term_id"], enrollment_id,
                           current["dr_quiz_taker"]["revision"] + 1, int(on), request_id, actor, timestamp()))
            view = self.student(enrollment_id, db)
            db.commit()
        return view
