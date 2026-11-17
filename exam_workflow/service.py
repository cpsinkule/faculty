"""Term exams, explicit sittings, collection accounting and preparation checks.

Only catalog records are introduced. Course-wide assignments, section-owned
paper, roster memberships, reviewed assemblies and rooms remain references.
Roster reads use the verified term storage lifecycle. No action here sends mail,
changes existing seating, infers absence, or authorizes Inbox processing.
"""
import json
import uuid
from datetime import date
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import digest, encoded
from course_storage import timestamp
from rosters import connect
from seating import student_name, term_label


STALE = "This exam changed. Reload it before saving."


def text(value, label, limit=280, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f"{label} must be {'nonempty text of ' if required else 'text of '}at most {limit} characters.")
    return value.strip()


class ExamService:
    def __init__(self, rosters, documents, scans, seating, response_cards):
        self.rosters, self.path = rosters, rosters.path
        self.documents, self.scans, self.seating, self.response_cards = documents, scans, seating, response_cards
        with rosters.lock(), connect(self.path) as db:
            schema = Path(__file__).with_name("migration.sql").read_text()
            chart_columns = {r["name"] for r in db.execute("PRAGMA table_info(exam_event_charts)")}
            if chart_columns and "original_chart_id" not in chart_columns:
                # Preserve earlier local links while permitting the existing
                # explicit seating-chart deletion to clear only its live FK.
                db.executescript("""BEGIN IMMEDIATE;
                    DROP TRIGGER IF EXISTS exam_chart_scope;
                    DROP TRIGGER IF EXISTS exam_chart_link_immutable;
                    DROP TRIGGER IF EXISTS exam_chart_link_retained;
                    ALTER TABLE exam_event_charts RENAME TO exam_event_charts_v1;
                    """ + schema + """
                    INSERT INTO exam_event_charts(event_id,chart_id,original_chart_id,sitting_id,created_at)
                        SELECT event_id,chart_id,chart_id,sitting_id,created_at FROM exam_event_charts_v1;
                    DROP TABLE exam_event_charts_v1;
                    COMMIT;""")
            else:
                db.executescript(schema)
            scope = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='exam_batch_scope'").fetchone()
            if scope and "b.term_id" not in scope[0]:
                # A print job owned by the term (document_identity migration 013) attaches to an exam of its term, naming no section.
                db.executescript("BEGIN IMMEDIATE; DROP TRIGGER exam_batch_scope;" + schema + "COMMIT;")

    @staticmethod
    def _event(db, event_id):
        row = db.execute("""SELECT e.*,t.code,t.term,t.term_other,t.year,t.definition_id,a.title assignment_title
            FROM exam_events e JOIN course_terms t ON t.id=e.term_id JOIN assignments a ON a.id=e.assignment_id
            WHERE e.id=?""", (event_id,)).fetchone()
        if not row:
            raise NotFound("Exam not found.")
        return {**dict(row), "term_label": term_label(row)}

    def _version(self, db, event_id, version):
        event = self._event(db, event_id)
        if type(version) is not int or version != event["version"]:
            raise Conflict(STALE)
        return event

    @staticmethod
    def _history(db, event_id, kind, detail):
        db.execute("INSERT INTO exam_history(event_id,kind,detail_json,created_at) VALUES(?,?,?,?)",
                   (event_id, kind, encoded(detail), timestamp()))

    @staticmethod
    def _touch(db, event_id):
        db.execute("UPDATE exam_events SET version=version+1,updated_at=? WHERE id=?", (timestamp(), event_id))

    @staticmethod
    def _sitting(db, event_id, sitting_id):
        if sitting_id is not None and not db.execute("SELECT 1 FROM exam_sittings WHERE id=? AND event_id=?", (sitting_id, event_id)).fetchone():
            raise ValueError("Choose a sitting belonging to this exam.")
        return sitting_id

    @staticmethod
    def _sections(db, event_id):
        return [dict(row) for row in db.execute("""SELECT s.id,s.number,s.term_id FROM exam_event_sections x
            JOIN course_sections s ON s.id=x.section_id WHERE x.event_id=? ORDER BY s.number""", (event_id,))]

    def catalog(self):
        with self.rosters.lock(), connect(self.path) as db:
            terms = [{**dict(row), "term_label": term_label(row)} for row in db.execute(
                "SELECT id,definition_id,code,term,term_other,year,is_current FROM course_terms WHERE deleted_at IS NULL ORDER BY is_current DESC,code,year DESC,term")]
            assignments = [dict(row) for row in db.execute("SELECT a.*,d.code FROM assignments a JOIN course_definitions d ON d.id=a.definition_id ORDER BY d.code,a.title")]
            sections = [dict(row) for row in db.execute("SELECT s.id,s.number,s.term_id,t.code,t.definition_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE t.deleted_at IS NULL ORDER BY t.code,s.number")]
            rooms = [dict(row) for row in db.execute("SELECT id,name FROM rooms ORDER BY name")]
        return {"terms": terms, "assignments": assignments, "sections": sections, "rooms": rooms}

    def list(self):
        with self.rosters.lock(), connect(self.path) as db:
            result = []
            for row in db.execute("SELECT id FROM exam_events ORDER BY created_at DESC,id"):
                event = self._event(db, row[0])
                event["sections"] = self._sections(db, row[0])
                event["participant_count"] = db.execute("SELECT COUNT(*) FROM exam_participants WHERE event_id=?", (row[0],)).fetchone()[0]
                event["sitting_count"] = db.execute("SELECT COUNT(*) FROM exam_sittings WHERE event_id=?", (row[0],)).fetchone()[0]
                # The main sitting's date as a chart page takes it from get(): the first main sitting with a date, in get()'s order
                # ('' when none). The page then need not read the whole exam record for one date.
                event["main_date"] = (db.execute("""SELECT date FROM exam_sittings WHERE event_id=? AND kind='main' AND COALESCE(date,'')!=''
                    ORDER BY date,time,created_at,id LIMIT 1""", (row[0],)).fetchone() or [""])[0]
                result.append(event)
            return result

    def create(self, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            event_id = self._create(db, data)
        return self.get(event_id)

    def _create(self, db, data):
        """Create in an existing lifecycle-locked catalog transaction."""
        section_ids = data.get("section_ids")
        if not isinstance(section_ids, list) or not section_ids or any(not isinstance(s, str) for s in section_ids) or len(set(section_ids)) != len(section_ids):
            raise ValueError("Choose one or more distinct sections for this exam.")
        term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (data.get("term_id"),)).fetchone()
        assignment = db.execute("SELECT * FROM assignments WHERE id=?", (data.get("assignment_id"),)).fetchone()
        if not term or not assignment or term["definition_id"] != assignment["definition_id"]:
            raise ValueError("Choose an assignment and term belonging to the same course.")
        for section_id in section_ids:
            if not db.execute("SELECT 1 FROM course_sections WHERE id=? AND term_id=?", (section_id, term["id"])).fetchone():
                raise ValueError("Every section must belong to the exam's course and term.")
        if db.execute("SELECT 1 FROM exam_events WHERE term_id=? AND assignment_id=?", (term["id"], assignment["id"])).fetchone():
            raise Conflict("This term already has an exam for that assignment. Add a sitting to the existing exam.")
        event_id, when = str(uuid.uuid4()), timestamp()
        title = text(data.get("title") or assignment["title"], "Exam title", required=True)
        db.execute("INSERT INTO exam_events VALUES(?,?,?,?,1,?,?)", (event_id, term["id"], assignment["id"], title, when, when))
        db.executemany("INSERT INTO exam_event_sections VALUES(?,?)", [(event_id, s) for s in section_ids])
        added = self._add_members(db, event_id)
        self._history(db, event_id, "created", {"section_ids": section_ids, "participants_added": added})
        return event_id

    def update(self, event_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            event = self._version(db, event_id, data.get("version"))
            title = text(data.get("title", event["title"]), "Exam title", required=True)
            db.execute("UPDATE exam_events SET title=? WHERE id=?", (title, event_id))
            self._touch(db, event_id)
            self._history(db, event_id, "settings", {"before": event["title"], "title": title})
        return self.get(event_id)

    @staticmethod
    def _add_members(db, event_id):
        when = timestamp()
        return db.execute("""INSERT OR IGNORE INTO exam_participants(event_id,section_id,enrollment_id,created_at,updated_at)
            SELECT x.event_id,m.section_id,m.enrollment_id,?,? FROM exam_event_sections x
            JOIN roster_current m ON m.section_id=x.section_id WHERE x.event_id=?""", (when, when, event_id)).rowcount

    def extend_for_print(self, db, term_id, assignment_id, section_ids, batch_id):
        """Printing extends the exam record (requirement, 2026-10-03). A new print job
        for a section the term's exam record does not include yet adds that section and its current roster, so its papers are
        expected and their scans associate automatically (an automatic association naming a section outside the record is still
        refused, grading/intake.py). Runs in the print job's lifecycle-locked transaction; never removes a section; a term
        without an exam record gets none here. Returns the sections added."""
        event = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (term_id, assignment_id)).fetchone()
        if not event:
            return []
        extra = sorted(set(section_ids) - {row["id"] for row in self._sections(db, event[0])})
        if extra:
            db.executemany("INSERT INTO exam_event_sections VALUES(?,?)", [(event[0], section) for section in extra])
            added = self._add_members(db, event[0])
            self._touch(db, event[0])
            self._history(db, event[0], "print_sections_added", {"print_batch_id": batch_id, "section_ids": extra, "participants_added": added})
        return extra

    def sync_roster(self, event_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._version(db, event_id, data.get("version"))
            added = self._add_members(db, event_id)
            self._touch(db, event_id)
            self._history(db, event_id, "roster_added", {"participants_added": added})
        return self.get(event_id)

    def save_sitting(self, event_id, data, sitting_id=None):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._event(db, event_id)
            before = dict(db.execute("SELECT * FROM exam_sittings WHERE event_id=? AND id=?", (event_id, sitting_id)).fetchone() or {}) if sitting_id else {}
            if sitting_id and not before:
                raise NotFound("Sitting not found.")
            if sitting_id:
                if type(data.get("version")) is not int or data["version"] != before["version"]:
                    raise Conflict("This sitting changed. Reload it before saving.")
            else:
                self._version(db, event_id, data.get("version"))
            fields = {key: text(data.get(key, before.get(key, "")), key.capitalize(), required=key == "label") for key in ("label", "date", "time", "location")}
            if fields["date"]:
                try:
                    if date.fromisoformat(fields["date"]).isoformat() != fields["date"]:
                        raise ValueError()
                except ValueError:
                    raise ValueError("Use an ISO date (YYYY-MM-DD) for the sitting.") from None
            kind = data.get("kind", before.get("kind", "main"))
            if kind not in ("main", "makeup", "dr"):
                raise ValueError("Choose a main, makeup, or DR sitting.")
            room_id = data.get("room_id", before.get("room_id")) or None
            if room_id and not db.execute("SELECT 1 FROM rooms WHERE id=?", (room_id,)).fetchone():
                raise ValueError("Choose a retained room.")
            seated = self._seated(db, event_id, data.get("participants"), bool(sitting_id))
            when = timestamp()
            if sitting_id:
                db.execute("UPDATE exam_sittings SET label=?,kind=?,date=?,time=?,location=?,room_id=?,version=version+1,updated_at=? WHERE id=?",
                           (fields["label"], kind, fields["date"], fields["time"], fields["location"], room_id, when, sitting_id))
            else:
                sitting_id = str(uuid.uuid4())
                db.execute("INSERT INTO exam_sittings VALUES(?,?,?,?,?,?,?,?,1,?,?)", (sitting_id, event_id, fields["label"], kind, fields["date"], fields["time"], fields["location"], room_id, when, when))
            self._touch(db, event_id)
            self._history(db, event_id, "sitting_saved", {"sitting_id": sitting_id, "before": before, "after": {**fields, "kind": kind, "room_id": room_id}})
            for row in seated:
                db.execute("""UPDATE exam_participants SET sitting_id=?,version=version+1,updated_at=? WHERE event_id=? AND section_id=? AND enrollment_id=?""",
                           (sitting_id, when, event_id, row["section_id"], row["enrollment_id"]))
                self._history(db, event_id, "participant_saved", {"section_id": row["section_id"], "enrollment_id": row["enrollment_id"], "before": dict(row),
                                                                  "sitting_id": sitting_id, "expectation": row["expectation"], "reason": row["reason"]})
        return self.get(event_id)

    @staticmethod
    def _seated(db, event_id, participants, editing):
        """WITH PARTICIPANTS (requirement, 2026-10-04: everything a follow-up needs is suggested in one round): the participant rows a
        NEW sitting seats as it is made, each named with the version the caller read, as Save on their row would check it. Their
        expectation and reason stay; only their sitting changes. [] when none are named."""
        if participants in (None, []):
            return []
        if editing:
            raise ValueError("Seat students in an existing sitting from their own row.")
        if not isinstance(participants, list) or len(participants) > 50:
            raise ValueError("Name up to 50 of this exam's students to seat.")
        rows, seen = [], set()
        for entry in participants:
            key = (entry.get("section_id"), entry.get("enrollment_id")) if isinstance(entry, dict) else (None, None)
            row = db.execute("SELECT * FROM exam_participants WHERE event_id=? AND section_id=? AND enrollment_id=?",
                             (event_id, str(key[0]), str(key[1]))).fetchone() if all(isinstance(k, str) for k in key) else None
            if not row:
                raise NotFound("Exam participant not found.")
            if type(entry.get("version")) is not int or entry["version"] != row["version"]:
                raise Conflict("This student's exam plan changed. Reload it before saving.")
            if key not in seen:
                seen.add(key)
                rows.append(dict(row))
        return rows

    def update_participant(self, event_id, section_id, enrollment_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._event(db, event_id)
            row = db.execute("SELECT * FROM exam_participants WHERE event_id=? AND section_id=? AND enrollment_id=?", (event_id, section_id, enrollment_id)).fetchone()
            if not row:
                raise NotFound("Exam participant not found.")
            if type(data.get("version")) is not int or data["version"] != row["version"]:
                raise Conflict("This student's exam plan changed. Reload it before saving.")
            expectation = data.get("expectation", row["expectation"])
            if expectation not in ("expected", "absent", "excused"):
                raise ValueError("Choose expected, absent, or excused.")
            reason = text(data.get("reason", row["reason"]), "Reason", 2000, required=expectation != "expected")
            sitting_id = self._sitting(db, event_id, data.get("sitting_id", row["sitting_id"]) or None)
            db.execute("""UPDATE exam_participants SET sitting_id=?,expectation=?,reason=?,version=version+1,updated_at=?
                WHERE event_id=? AND section_id=? AND enrollment_id=?""", (sitting_id, expectation, reason, timestamp(), event_id, section_id, enrollment_id))
            self._touch(db, event_id)
            self._history(db, event_id, "participant_saved", {"section_id": section_id, "enrollment_id": enrollment_id, "before": dict(row), "sitting_id": sitting_id, "expectation": expectation, "reason": reason})
        return self.get(event_id)

    def attach_batch(self, event_id, data):
        return self._attach(event_id, data, "batch")

    def attach_chart(self, event_id, data):
        return self._attach(event_id, data, "chart")

    def _attach(self, event_id, data, kind):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            event = self._version(db, event_id, data.get("version"))
            sitting_id = self._sitting(db, event_id, data.get("sitting_id") or None)
            reference = data.get(kind + "_id")
            if kind == "batch":
                # A section's job is for one of the exam's sections; a job printed for the whole term (no section, document_identity
                # migration 013) is for the exam's term, its copies each in their student's own section.
                valid = db.execute("""SELECT 1 FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
                    WHERE b.id=? AND v.assignment_id=? AND b.term_id=? AND (b.section_id IS NULL
                    OR EXISTS(SELECT 1 FROM exam_event_sections s WHERE s.section_id=b.section_id AND s.event_id=?))""",
                    (reference, event["assignment_id"], event["term_id"], event_id)).fetchone()
            else:
                valid = db.execute("SELECT 1 FROM seating_charts WHERE id=? AND term_id=? AND assignment_id=?", (reference, event["term_id"], event["assignment_id"])).fetchone()
                if db.execute("""SELECT 1 FROM seating_chart_sections x WHERE chart_id=? AND NOT EXISTS
                    (SELECT 1 FROM exam_event_sections s WHERE s.event_id=? AND s.section_id=x.section_id)""", (reference, event_id)).fetchone():
                    valid = None
            if not valid:
                raise ValueError(f"Choose a {kind} for this assignment, term, and selected sections.")
            table, column = ("exam_event_batches", "batch_id") if kind == "batch" else ("exam_event_charts", "chart_id")
            prior = db.execute(f"SELECT * FROM {table} WHERE {column}=?", (reference,)).fetchone()
            if prior:
                if prior["event_id"] != event_id or prior["sitting_id"] != sitting_id:
                    raise Conflict(f"This {kind} already has a retained exam/sitting link.")
            else:
                if kind == "chart":
                    db.execute("INSERT INTO exam_event_charts(event_id,chart_id,original_chart_id,sitting_id,created_at) VALUES(?,?,?,?,?)",
                               (event_id, reference, reference, sitting_id, timestamp()))
                else:
                    db.execute("INSERT INTO exam_event_batches VALUES(?,?,?,?)", (event_id, reference, sitting_id, timestamp()))
                self._touch(db, event_id)
                self._history(db, event_id, kind + "_attached", {column: reference, "sitting_id": sitting_id})
        return self.get(event_id)

    def _participants(self, db, event):
        """Read names only through verified term storage; caller owns roster lock."""
        term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (event["term_id"],)).fetchone())
        with self.rosters.files(term) as root:
            payload = self.rosters.read_payload(root, term["roster_initialized"])
        result = []
        for row in db.execute("""SELECT p.*,s.number section_number,(d.enrollment_id IS NOT NULL) dropped FROM exam_participants p
                JOIN course_sections s ON s.id=p.section_id
                LEFT JOIN roster_dropped d ON d.section_id=p.section_id AND d.enrollment_id=p.enrollment_id
                WHERE p.event_id=? ORDER BY s.number,p.enrollment_id""", (event["id"],)):
            record = payload["sections"].get(row["section_id"], {}).get("students", {}).get(row["enrollment_id"], {})
            result.append({**dict(row), "name": student_name({**record, "id": row["enrollment_id"]})})
        return result

    @staticmethod
    def _batches(db, event, attached=True):
        join = "JOIN exam_event_batches eb ON eb.batch_id=b.id AND eb.event_id=?" if attached else ""
        select = ",eb.sitting_id" if attached else ",NULL sitting_id"
        args = ((event["id"],) if attached else ()) + (event["assignment_id"], event["term_id"], event["id"])
        # A job printed for the whole term (no section, document_identity migration 013) belongs to every exam of its term.
        return [dict(row) for row in db.execute(f"""SELECT b.id,b.status,b.superseded_at,b.quantity,b.section_id,s.number section_number,
            b.assignment_version_id,v.label version_label,v.page_count,b.seating_chart_id,b.created_at {select}
            FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id LEFT JOIN course_sections s ON s.id=b.section_id
            {join} WHERE v.assignment_id=? AND b.term_id=? AND (b.section_id IS NULL
              OR EXISTS(SELECT 1 FROM exam_event_sections es WHERE es.event_id=? AND es.section_id=b.section_id))
            ORDER BY b.created_at,b.id""", args)]

    @staticmethod
    def _charts(db, event, attached=True):
        join = "JOIN exam_event_charts ec ON ec.chart_id=c.id AND ec.event_id=?" if attached else ""
        select = ",ec.sitting_id" if attached else ",NULL sitting_id"
        args = (event["id"], event["term_id"], event["assignment_id"], event["id"]) if attached else (event["term_id"], event["assignment_id"], event["id"])
        return [dict(row) for row in db.execute(f"""SELECT c.id,c.room_id,r.name room_name,c.exam_date,c.exam_time,c.version,c.confirmed_at {select}
            FROM seating_charts c JOIN rooms r ON r.id=c.room_id {join} WHERE c.term_id=? AND c.assignment_id=? AND NOT EXISTS
            (SELECT 1 FROM seating_chart_sections cs WHERE cs.chart_id=c.id AND NOT EXISTS
            (SELECT 1 FROM exam_event_sections es WHERE es.event_id=? AND es.section_id=cs.section_id)) ORDER BY r.name,c.id""", args)]

    def get(self, event_id):
        with self.rosters.lock(), connect(self.path) as db:
            event = self._event(db, event_id)
            event["sections"] = self._sections(db, event_id)
            event["sittings"] = [dict(row) for row in db.execute("SELECT * FROM exam_sittings WHERE event_id=? ORDER BY date,time,created_at,id", (event_id,))]
            event["participants"] = self._participants(db, event)
            event["batches"], event["charts"] = self._batches(db, event), self._charts(db, event)
            event["deleted_chart_links"] = [dict(row) for row in db.execute("""SELECT original_chart_id,sitting_id,created_at
                FROM exam_event_charts WHERE event_id=? AND chart_id IS NULL ORDER BY created_at,original_chart_id""", (event_id,))]
            event["available_batches"] = [b for b in self._batches(db, event, False) if b["id"] not in {x["id"] for x in event["batches"]}]
            event["available_charts"] = [c for c in self._charts(db, event, False) if c["id"] not in {x["id"] for x in event["charts"]}]
            event["reconciliation"] = self._collection(db, event, event["participants"])
            event["impacts"] = self._impacts(db, event, event["participants"])
            event["history"] = [{**dict(row), "detail": json.loads(row["detail_json"])} for row in db.execute("SELECT * FROM exam_history WHERE event_id=? ORDER BY id DESC LIMIT 100", (event_id,))]
            # The assisted-grading plan that hangs off this exam, if any (read-only; the Grading page owns it).
            plan = db.execute("SELECT id FROM frq_assignments WHERE event_id=?", (event_id,)).fetchone()
            event["grading_plan_id"] = plan[0] if plan else None
            # Requests still waiting for a decision, for the Requests tab's count (the tab lists them).
            event["request_counts"] = {"open": db.execute("SELECT COUNT(*) FROM exam_requests WHERE event_id=? AND status IN ('open','in_review')", (event_id,)).fetchone()[0]}
        # Each student's collection record is sent once, in reconciliation.students (UX overhaul, 2026-09-26: the payload carried
        # every record twice).
        for person in event["participants"]:
            person.pop("collection", None)
        return event

    @staticmethod
    def _copy_reads(db, copy):
        """One copy's scan observations (oldest first), assembly revisions (in order) and the Inbox jobs of its present scans."""
        observations = db.execute("""SELECT sp.id,sp.message_id,p.page_number,sp.content_sha256,sp.status,sp.review_state,
            m.authenticated,m.instructor_review_required,sp.created_at FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
            JOIN inbox_messages m ON m.id=sp.message_id WHERE p.printed_copy_id=? AND NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id=sp.message_id AND gone.restored_at IS NULL) ORDER BY sp.created_at,sp.id""", (copy["id"],)).fetchall()
        revisions = db.execute("SELECT id,revision,partial,selection_json,created_at FROM assembly_revisions WHERE printed_copy_id=? ORDER BY revision", (copy["id"],)).fetchall()
        jobs = [row[0] for row in db.execute("""SELECT DISTINCT j.id FROM inbox_jobs j JOIN scan_pages sp ON sp.message_id=j.message_id
            JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE p.printed_copy_id=? AND NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id=sp.message_id AND gone.restored_at IS NULL) ORDER BY j.id""", (copy["id"],))]
        return observations, revisions, jobs

    @staticmethod
    def _exam_copy_reads(db, event, copies):
        """`_copy_reads` of every copy of the exam's print jobs in two statements for all, and one for their scans' Inbox jobs
        (PERFORMANCE, 2026-10-02: three per copy were 6,400 of the Grading index's 11,700 statements). The observations and revisions are the one-copy queries over the exam's
        copies with the copy named in each row, in the same order, so each copy's rows are exactly its own query's; rows of a copy
        not among `copies` are left out. A copy's jobs are those of its observations' messages (the same present scan pages: every
        scan page's message exists, foreign keys being on, and a message has at most one job), in id order as their query gives."""
        found = {copy["id"]: ([], [], []) for copy in copies}
        if not copies:
            return found
        exam = "exam_event_batches eb JOIN printed_copies c ON c.print_batch_id=eb.batch_id"
        reads = ("""SELECT p.printed_copy_id,sp.id,sp.message_id,p.page_number,sp.content_sha256,sp.status,sp.review_state,
                m.authenticated,m.instructor_review_required,sp.created_at FROM """ + exam + """ JOIN page_stamps p ON p.printed_copy_id=c.id
                JOIN scan_pages sp ON sp.page_stamp_id=p.id JOIN inbox_messages m ON m.id=sp.message_id
                WHERE eb.event_id=? AND NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id=sp.message_id AND gone.restored_at IS NULL) ORDER BY sp.created_at,sp.id""",
                 """SELECT ar.printed_copy_id,ar.id,ar.revision,ar.partial,ar.selection_json,ar.created_at FROM """ + exam + """
                JOIN assembly_revisions ar ON ar.printed_copy_id=c.id WHERE eb.event_id=? ORDER BY ar.printed_copy_id,ar.revision""")
        for index, sql in enumerate(reads):
            for row in db.execute(sql, (event["id"],)):
                lists = found.get(row[0])
                if lists is not None:
                    lists[index].append(row)
        messages = sorted({o["message_id"] for observations, _, _ in found.values() for o in observations})
        job_of = {}
        for start in range(0, len(messages), 500):
            chunk = messages[start:start + 500]
            job_of.update((row[0], row[1]) for row in db.execute(
                f"SELECT message_id,id FROM inbox_jobs WHERE message_id IN ({','.join('?' * len(chunk))})", chunk))
        for observations, _, jobs in found.values():
            jobs.extend(sorted({job_of[o["message_id"]] for o in observations if o["message_id"] in job_of}))
        return found

    def _copies(self, db, event, section_id=None, enrollment_id=None):
        """The exam's printed copies with what their scans and assemblies say, all of them or ONE student's (whose collection
        depends on their own copies only, so it is the same either way). All of them are read in one pass (`_exam_copy_reads`);
        one student's copies each with their own indexed reads."""
        one = section_id is not None
        copies = [dict(row) for row in db.execute(f"""SELECT c.id,c.print_batch_id batch_id,c.copy_number,c.enrollment_id,b.section_id,
            v.page_count,b.label_mode,b.status batch_status,b.superseded_at FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
            JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN exam_event_batches eb ON eb.batch_id=b.id
            WHERE eb.event_id=?{" AND (b.section_id=? OR b.section_id IS NULL) AND c.enrollment_id=?" if one else ""} ORDER BY c.print_batch_id,c.copy_number""",
            (event["id"], *((section_id, enrollment_id) if one else ())))]
        reads = None if one else self._exam_copy_reads(db, event, copies)
        for copy in copies:
            observations, revisions, job_ids = self._copy_reads(db, copy) if one else reads[copy["id"]]
            current = revisions[-1] if revisions else None
            accepted = [o for o in observations if o["status"] in ("resolved", "manual") and o["review_state"] not in ("rejected", "ignored")]
            copy["observed_pages"] = sorted({o["page_number"] for o in accepted})
            # Paper the instructor labeled on the first page only is expected to show that page's label, not one per page.
            expected = [1] if copy["label_mode"] == "first_page" else range(1, copy["page_count"] + 1)
            copy["missing_pages"] = [n for n in expected if n not in copy["observed_pages"]]
            copy["message_ids"] = sorted({o["message_id"] for o in observations})
            copy["job_ids"] = job_ids
            copy["observation_count"] = len(observations)
            copy["latest_observation_at"] = max((o["created_at"] for o in observations), default=None)
            copy["revision_ids"] = [r["id"] for r in revisions]
            copy["latest_revision_id"] = current["id"] if current else None
            copy["partial"] = bool(current and current["partial"])
            copy["active"] = copy["batch_status"] == "ready" and not copy["superseded_at"]
            copy["issues"] = []
            valid_revision = bool(current and copy["active"])
            if current:
                chosen = json.loads(current["selection_json"])
                by_id = {o["id"]: o for o in observations}
                selected_hashes = {p["page_number"]: p["content_sha256"] for p in chosen}
                if any(o["page_number"] not in selected_hashes or selected_hashes[o["page_number"]] != o["content_sha256"] for o in accepted):
                    valid_revision = False
                    copy["issues"].append("New or conflicting scan observations need assembly review.")
                if any(p["scan_page_id"] not in by_id or not by_id[p["scan_page_id"]]["authenticated"] or by_id[p["scan_page_id"]]["instructor_review_required"] for p in chosen):
                    valid_revision = False
                    copy["issues"].append("Selected scan sources still need their scan checks.")
            copy["assembly_revision_id"] = current["id"] if valid_revision else None
            if not copy["active"]:
                copy["issues"].append("Paper is unissued, cancelled, or superseded.")
        return copies

    @staticmethod
    def _submission_choices(db, event):
        """{enrollment_id: the newest submission choice of this exam's assignment}, each exactly the row `_person_collection` reads
        for that student alone (newest `created_at`, then the latest row), in one read for the whole exam."""
        choices = {}
        for row in db.execute("""SELECT assembly_revision_id,created_at,enrollment_id FROM submission_choices WHERE assignment_id=?
                ORDER BY created_at DESC,rowid DESC""", (event["assignment_id"],)):
            choices.setdefault(row["enrollment_id"], row)
        return choices

    def _person_collection(self, db, event, person, copies, choices=None):
        """One participant's submitted paper, from the copies (`_copies`) that are theirs among those given: of their section's jobs,
        or of a job printed for the whole term (no section), whose copy is in the section of the student it was named for.
        `choices`: `_submission_choices` of the exam when the caller read every student's at once."""
        matching = [c for c in copies if c["section_id"] in (None, person["section_id"]) and c["enrollment_id"] == person["enrollment_id"]]
        active = [c for c in matching if c["active"]]
        observed = [c for c in active if c["observation_count"]]
        reviewed = [c for c in active if c["latest_revision_id"]]
        if choices is None:
            choice = db.execute("SELECT assembly_revision_id,created_at FROM submission_choices WHERE enrollment_id=? AND assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (person["enrollment_id"], event["assignment_id"])).fetchone()
        else:
            choice = choices.get(person["enrollment_id"])
        selected, issues = None, []
        if choice:
            selected = next((c for c in reviewed if c["assembly_revision_id"] == choice[0]), None)
            if selected and any(c["id"] != selected["id"] and c["latest_observation_at"] > choice["created_at"] for c in observed):
                selected = None
                issues.append("Another copy arrived after the submission choice. Review the new arrival explicitly.")
            if not selected and (observed or reviewed):
                issues.append("The selected submission is unavailable or changed; review the submission choice.")
        elif len(reviewed) == 1 and len(observed) == 1:
            selected = reviewed[0] if reviewed[0]["assembly_revision_id"] else None
        elif len(reviewed) > 1 or len(observed) > 1:
            issues.append("Multiple submitted copies require an explicit submission choice.")
        if selected:
            status = "partial" if selected["partial"] else "complete"
            if person["expectation"] != "expected":
                issues.append("A received paper conflicts with the recorded absence or excuse.")
        elif issues or any(c["latest_revision_id"] or c["issues"] for c in observed):
            status = "needs_review"
        elif observed:
            status = "missing_pages" if any(c["missing_pages"] for c in observed) else "needs_review"
        else:
            status = person["expectation"] if person["expectation"] != "expected" else "no_scan"
        collection = {"status": status, "assembly_revision_id": selected["assembly_revision_id"] if selected else None,
                      "copies": matching, "missing_pages": selected["missing_pages"] if selected else sorted({p for c in observed for p in c["missing_pages"]}),
                      "issues": issues + [i for c in observed for i in c["issues"]]}
        return collection

    def _collection(self, db, event, participants):
        """Reconcile all linked arrivals, retaining explicit review/choice boundaries."""
        copies, choices = self._copies(db, event), self._submission_choices(db, event)
        named = {}  # each student's copies, in order: a participant's are among those named for them (PERFORMANCE, 2026-10-02)
        for copy in copies:
            named.setdefault(copy["enrollment_id"], []).append(copy)
        students, counts = [], {key: 0 for key in ("complete", "partial", "needs_review", "missing_pages", "no_scan", "absent", "excused")}
        for person in participants:
            collection = self._person_collection(db, event, person, named.get(person["enrollment_id"], ()), choices)
            status = collection["status"]
            person["collection"] = collection
            students.append({"section_id": person["section_id"], "enrollment_id": person["enrollment_id"], "name": person["name"], **collection})
            counts[status] += 1
        assigned_keys = {(p["section_id"], p["enrollment_id"]) for p in participants}
        assigned_students = {p["enrollment_id"] for p in participants}
        unassigned = [c for c in copies if c["active"] and (not c["enrollment_id"] or (
            c["enrollment_id"] not in assigned_students if c["section_id"] is None else (c["section_id"], c["enrollment_id"]) not in assigned_keys))]
        return {"counts": counts, "students": students, "unassigned_copies": unassigned,
                "unused_extra_count": sum(not c["observation_count"] for c in unassigned),
                "unassigned_received_count": sum(bool(c["observation_count"]) for c in unassigned)}

    def _participant_paper(self, db, event, section_id, enrollment_id):
        """ONE participant with their collection, read from the catalog alone: no roster payload and so no name, and no other
        student's paper. Grading checks each of its steps with this (2026-09-24): reading the whole term roster and every copy
        of the exam for each step, three times per step under the lifecycle lock, held a 2,600-response batch to a crawl."""
        row = db.execute("""SELECT p.*,s.number section_number FROM exam_participants p JOIN course_sections s ON s.id=p.section_id
            WHERE p.event_id=? AND p.section_id=? AND p.enrollment_id=?""", (event["id"], section_id, enrollment_id)).fetchone()
        if not row:
            return None
        person = dict(row)
        person["collection"] = self._person_collection(db, event, person, self._copies(db, event, section_id, enrollment_id))
        return person

    def _snapshot(self, db, event, participants):
        state = {}
        def add(kind, key, value):
            state[kind + ":" + key] = {"kind": kind, **value}
        for row in db.execute("""SELECT m.section_id,m.enrollment_id FROM roster_current m JOIN exam_event_sections s
                ON s.section_id=m.section_id WHERE s.event_id=? ORDER BY m.section_id,m.enrollment_id""", (event["id"],)):
            add("roster", row["section_id"] + ":" + row["enrollment_id"], dict(row))
        for p in participants:
            add("participant", p["section_id"] + ":" + p["enrollment_id"], {k: p[k] for k in ("section_id", "enrollment_id", "name", "sitting_id", "expectation", "reason")})
        for row in db.execute("SELECT * FROM exam_sittings WHERE event_id=? ORDER BY id", (event["id"],)):
            add("sitting", row["id"], {k: row[k] for k in ("id", "label", "date", "time", "location", "room_id")} | {"sitting_kind": row["kind"]})
        charts = self._charts(db, event)
        room_ids = {c["room_id"] for c in charts} | {row[0] for row in db.execute("SELECT room_id FROM exam_sittings WHERE event_id=? AND room_id IS NOT NULL", (event["id"],))}
        for chart in charts:
            add("chart", chart["id"], {k: chart[k] for k in ("id", "room_id", "room_name", "exam_date", "exam_time", "sitting_id")})
            for row in db.execute("""SELECT a.section_id,a.enrollment_id,a.seat_id,s.label,s.broken,s.blocked,s.left_handed
                FROM seating_assignments a JOIN room_seats s ON s.id=a.seat_id WHERE a.chart_id=? ORDER BY a.enrollment_id""", (chart["id"],)):
                add("seat", chart["id"] + ":" + row["enrollment_id"], {"chart_id": chart["id"], **dict(row)})
        for room_id in sorted(room_ids):
            row = db.execute("SELECT id,name,review_required,version FROM rooms WHERE id=?", (room_id,)).fetchone()
            if row:
                add("room", room_id, dict(row))
        for row in db.execute("""SELECT b.id,b.section_id,b.enrollment_id,b.revision_id,r.snapshot_json FROM dr_exam_bookings b
            JOIN dr_exam_revisions r ON r.id=b.revision_id JOIN dr_exam_assignment_links l ON l.booking_id=b.id
            JOIN exam_event_sections s ON s.section_id=b.section_id WHERE s.event_id=? AND l.assignment_id=? ORDER BY b.id""", (event["id"], event["assignment_id"])):
            schedule = json.loads(row["snapshot_json"])
            add("dr", row["id"], {k: row[k] for k in ("id", "section_id", "enrollment_id", "revision_id")} | {"schedule": {k: schedule.get(k) for k in ("date", "start_time", "end_time", "duration", "campus", "exam_name")}})
        for batch in self._batches(db, event):
            add("batch", batch["id"], {k: batch[k] for k in ("id", "section_id", "assignment_version_id", "version_label", "status", "superseded_at", "sitting_id")})
            for copy in db.execute("SELECT id,enrollment_id FROM printed_copies WHERE print_batch_id=? ORDER BY id", (batch["id"],)):
                add("copy", copy["id"], {"batch_id": batch["id"], "section_id": batch["section_id"], **dict(copy)})
        for row in db.execute("""SELECT c.id,c.issue_id,c.section_id,c.enrollment_id,c.snapshot_hash,c.status,i.seating_chart_id FROM response_card_recipients c
                JOIN response_card_issues i ON i.id=c.issue_id JOIN exam_event_sections s ON s.section_id=c.section_id
                WHERE s.event_id=? AND i.term_id=? AND i.assignment_id=? ORDER BY c.id""", (event["id"], event["term_id"], event["assignment_id"])):
            card = dict(row)
            # Opening or answering a card is normal progress, not a change to
            # the preparation it describes. Revocation/expiry remains relevant.
            card["status"] = card["status"] if card["status"] in ("revoked", "expired") else "active"
            add("card", row["id"], card)
        return state

    def _impacts(self, db, event, participants):
        state = self._snapshot(db, event, participants)
        checkpoint = db.execute("SELECT * FROM exam_preparation_checks WHERE event_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (event["id"],)).fetchone()
        baseline = json.loads(checkpoint["snapshot_json"]) if checkpoint else state
        labels = {"roster": "Roster membership", "participant": "Student exam plan", "sitting": "Sitting date, time or location", "chart": "Room chart schedule",
                  "seat": "Seat assignment or condition", "room": "Room name or review status", "dr": "DR exam booking", "batch": "Print artifact", "copy": "Printed copy assignment", "card": "Student notice"}
        changes = []
        for key in sorted(set(state) | set(baseline)):
            old, new = baseline.get(key), state.get(key)
            if old == new:
                continue
            value = new or old
            change = {"kind": value["kind"], "message": labels[value["kind"]] + (" added." if old is None else " removed." if new is None else " changed."), "before": old, "after": new}
            change.update({k: value[k] for k in ("section_id", "enrollment_id", "chart_id", "batch_id") if k in value})
            if value["kind"] == "chart":
                change["chart_id"] = value["id"]
                change["enrollment_ids"] = [r[0] for r in db.execute("SELECT enrollment_id FROM seating_assignments WHERE chart_id=?", (value["id"],))]
            if value["kind"] == "sitting":
                change["sitting_id"] = value["id"]
                change["enrollment_ids"] = [p["enrollment_id"] for p in participants if p["sitting_id"] == value["id"]]
            if value["kind"] == "batch":
                change["batch_id"] = value["id"]
            if value["kind"] == "room":
                change["room_id"] = value["id"]
                change["chart_ids"] = [c["id"] for c in self._charts(db, event) if c["room_id"] == value["id"]]
                change["enrollment_ids"] = [s["enrollment_id"] for s in state.values() if s["kind"] == "seat" and s["chart_id"] in change["chart_ids"]]
            changes.append(change)
        represented = {(p["section_id"], p["enrollment_id"]) for p in participants}
        for item in state.values():
            if item["kind"] == "roster" and (item["section_id"], item["enrollment_id"]) not in represented:
                changes.append({"kind": "roster_addition", "message": "Roster student has not been added to this exam. Use Add new roster students.", "section_id": item["section_id"], "enrollment_id": item["enrollment_id"]})
        # Always show live inconsistencies, even after recording a checkpoint.
        for chart in self._charts(db, event):
            if chart["sitting_id"]:
                sitting = db.execute("SELECT * FROM exam_sittings WHERE id=?", (chart["sitting_id"],)).fetchone()
                if sitting and ((sitting["date"] and chart["exam_date"] != sitting["date"]) or (sitting["time"] and chart["exam_time"] != sitting["time"]) or (sitting["room_id"] and chart["room_id"] != sitting["room_id"])):
                    changes.append({"kind": "schedule_mismatch", "message": "The room chart and its sitting have different dates, times or rooms. Review notices and printed materials before using them.", "chart_id": chart["id"], "sitting_id": sitting["id"]})
            for seat in state.values():
                if seat["kind"] == "seat" and seat["chart_id"] == chart["id"] and (seat["broken"] or seat["blocked"]):
                    changes.append({"kind": "unusable_seat", "message": f"Seat {seat['label']} is no longer usable; review the seat and its notice.", "chart_id": chart["id"], "section_id": seat["section_id"], "enrollment_id": seat["enrollment_id"]})
        changes.extend(self._notice_impacts(db, event, state))
        for change in changes:
            enrollments = set(change.get("enrollment_ids", []))
            if change.get("enrollment_id"):
                enrollments.add(change["enrollment_id"])
            change["card_ids"] = sorted({s["id"] for s in state.values() if s["kind"] == "card" and s["enrollment_id"] in enrollments})
            change["issue_ids"] = sorted({s["issue_id"] for s in state.values() if s["kind"] == "card" and s["enrollment_id"] in enrollments})
            change["batch_ids"] = sorted({s["batch_id"] for s in state.values() if s["kind"] == "copy" and s["enrollment_id"] in enrollments} | ({change["batch_id"]} if change.get("batch_id") else set()))
        return {"fingerprint": digest(state), "checkpoint_id": checkpoint["id"] if checkpoint else None,
                "checkpoint_at": checkpoint["created_at"] if checkpoint else None, "changes": changes, "count": len(changes)}

    @staticmethod
    def _notice_impacts(db, event, state):
        """Check retained built-in verification values without returning secrets."""
        from response_cards.schema import render_text, SchemaError
        from response_cards.service import long_date
        changes = []
        latest = {}
        for row in db.execute("""SELECT c.id,c.section_id,c.enrollment_id,c.snapshot_json,c.status,i.seating_chart_id,t.name template_name,v.schema_json
            FROM response_card_recipients c JOIN response_card_issues i ON i.id=c.issue_id
            JOIN response_card_versions v ON v.id=i.version_id JOIN response_card_templates t ON t.id=v.template_id
            JOIN exam_event_sections s ON s.section_id=c.section_id
            WHERE s.event_id=? AND i.term_id=? AND i.assignment_id=? ORDER BY c.created_at,c.rowid""", (event["id"], event["term_id"], event["assignment_id"])):
            latest[(row["section_id"], row["enrollment_id"], row["template_name"])] = dict(row)
        for card in latest.values():
            if card["status"] in ("revoked", "expired"):
                continue
            snapshot_schema = json.loads(card["snapshot_json"]).get("schema", {})
            fields = {f["id"]: (f.get("verification") or {}).get("expected") for f in snapshot_schema.get("fields", [])}
            expected = {}
            mismatches = []
            if card["template_name"] == "DR testing notice (Faculty)":
                bookings = [x for x in state.values() if x["kind"] == "dr" and x["section_id"] == card["section_id"] and x["enrollment_id"] == card["enrollment_id"]]
                if len(bookings) != 1:
                    changes.append({"kind": "stale_notice", "message": "The DR notice no longer has exactly one matching booking. Review it before issuing a replacement.", "card_id": card["id"], "section_id": card["section_id"], "enrollment_id": card["enrollment_id"]})
                    continue
                schedule = bookings[0]["schedule"]
                # The location is never checked (RESPONSE-CARDS "DR LOCATION IS NOT CHECKED"): a moved DR room is no stale notice.
                expected = {"confirm_dr_date": schedule.get("date"), "confirm_dr_time": schedule.get("start_time")}
            elif card["template_name"] == "Exam seat assignment (Faculty)" and card["seating_chart_id"]:
                seat = state.get("seat:" + card["seating_chart_id"] + ":" + card["enrollment_id"])
                chart = state.get("chart:" + card["seating_chart_id"])
                if seat and chart:
                    expected = {"confirm_seat": seat["label"], "confirm_room": chart["room_name"]}
                    variables = {"exam.name": event["assignment_title"], "assignment.name": event["assignment_title"], "course.code": event["code"],
                                 "exam.date": chart["exam_date"], "exam.date_long": long_date(chart["exam_date"]), "exam.time": chart["exam_time"],
                                 "exam.room": chart["room_name"], "seat.number": seat["label"]}
                    try:
                        current_description = render_text(json.loads(card["schema_json"]).get("description", ""), variables)
                        if current_description != snapshot_schema.get("description", ""):
                            mismatches.append("description")
                    except SchemaError:
                        # A custom description may need unrelated variables. Its
                        # immutable snapshot and subsequent source changes remain
                        # visible through the preparation checkpoint comparison.
                        pass
                elif chart:
                    mismatches.append("seat_assignment")
            mismatches.extend(key for key, value in expected.items() if key in fields and fields[key] != value)
            if mismatches:
                changes.append({"kind": "stale_notice", "message": "Issued notice differs from the current " + ("DR schedule" if "confirm_dr_date" in expected else "exam schedule, seat or room") + ". Review and prepare a replacement notice.", "card_id": card["id"], "section_id": card["section_id"], "enrollment_id": card["enrollment_id"], "fields": mismatches})
        return changes

    def checkpoint(self, event_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            event = self._event(db, event_id)
            participants = self._participants(db, event)
            db.execute("BEGIN IMMEDIATE")
            self._version(db, event_id, data.get("version"))
            snapshot = self._snapshot(db, event, participants)
            fingerprint = digest(snapshot)
            if data.get("fingerprint") != fingerprint:
                raise Conflict("Exam sources changed since this preview. Reload and review the changes before recording a checkpoint.")
            checkpoint_id = str(uuid.uuid4())
            db.execute("INSERT INTO exam_preparation_checks VALUES(?,?,?,?,?)", (checkpoint_id, event_id, fingerprint, encoded(snapshot), timestamp()))
            self._touch(db, event_id)
            self._history(db, event_id, "preparation_checked", {"checkpoint_id": checkpoint_id, "fingerprint": fingerprint})
        return self.get(event_id)
