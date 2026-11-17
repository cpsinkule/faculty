"""SCHEDULING "CLASS SCHEDULE: DR QUIZZES" › DR DATE (requirement, 2026-10-31): the only place that records a DR quizzes row's DR date,
the day its section's DR quiz takers sit the quiz at the DR center when that is not the class's own day (a quiz given in class before
they were flagged, or one they sit on another day). The DR quizzes read (`dr_quizzes.term_quizzes`) then lists the row at that date,
so Send to AIM adds that day and the DR quiz notices name it. The class schedule is unchanged. Owner: the section, naming its term's
planned row. Nothing outside Faculty is contacted."""
from __future__ import annotations

import datetime as dt

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect

from . import clock
from .dr_quizzes import term_quizzes
from .service import ScheduleService, request_digest


def record(service, term_id, data, actor="workspace"):
    """Set (or, with an empty `date`, clear) the DR date of the DR quizzes row `key`: {request_id, key, date}. 400 for a key not on
    the term's DR quizzes or a date before campus today; the same request ID with the same body records nothing again, with another
    body 409. A date equal to the row's current DR date records nothing."""
    if not isinstance(data, dict) or set(data) - {"request_id", "key", "date"}:
        raise ValueError("Send request_id, key and date.")
    request_id, key, date = data.get("request_id"), data.get("key"), data.get("date")
    valid_request_id(request_id)
    if not isinstance(key, str) or ":" not in key:
        raise ValueError("Name the quiz by its key.")
    if not isinstance(date, str):
        raise ValueError("Send the DR date as YYYY-MM-DD, or an empty date to clear it.")
    if date:
        try:
            day = dt.date.fromisoformat(date)
        except ValueError:
            raise ValueError("Send the DR date as YYYY-MM-DD, or an empty date to clear it.") from None
        if day.isoformat() != date:
            raise ValueError("Send the DR date as YYYY-MM-DD, or an empty date to clear it.")
        if date < clock.campus_today().isoformat():
            raise ValueError("A DR date cannot be before today.")
    digest = request_digest({"term": term_id, "key": key, "date": date})
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        ScheduleService._term(db, term_id)
        earlier = db.execute("SELECT request_sha256 FROM dr_quiz_sittings WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier:
            if earlier[0] != digest:
                raise Conflict("This request ID already recorded another DR date.")
            return
        quiz = next((quiz for quiz in term_quizzes(db, term_id)["quizzes"] if quiz["key"] == key), None)
        if not quiz:
            raise ValueError("That quiz is not on this term's DR quizzes. Read them again.")
        if quiz["dr_date"] == date:
            return
        db.execute("""INSERT INTO dr_quiz_sittings(term_id, item_id, section_id, date, request_id, request_sha256, actor, created_at)
            VALUES (?,?,?,?,?,?,?,?)""", (term_id, quiz["item"], quiz["section_id"], date, request_id, digest, actor, timestamp()))
        db.commit()
