"""Bubble sheets sent to Gradescope, upload only (requirement, 2026-09-25). AGENTS.md "Sending grades to Gradescope" is the rule book.

Each sheet Faculty has matched to a student (an exact UIN, a confident match at the instructor's threshold, or the instructor's
review) goes up as that student, found on the section's Gradescope roster by UIN = SID exactly, to the version of the section's
Gradescope bubble-sheet exam that the sheet's own version names (read on the sheet, or recorded in its review). Gradescope reads the
bubbles and scores them with its own answer key: nothing here is a grade, and Faculty's bubble scores stay the instructor's record.
Only scans that cleared Inbox review count, as everywhere a sheet is used. The page goes as scanned, one page (Faculty reads page 1 of
Gradescope's bubble sheet, questions 1 to 100), turned upright when it was read upside down.

Everything else is the written exam's machinery in `gradescope_sync`, which this class is mixed into: the same preview and
fingerprint, one live send per plan, sections left out, a worker that uploads sheets PARALLEL at a time, each on its own connection
of the one sign-in, and reads each back, never over a sheet someone else put there, a busy Gradescope waited for, and an exam with
published grades sent to only once the instructor confirmed it (requirement, 2026-09-26).
"""
import json
import sqlite3

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from document_identity import process
from rosters import connect

from . import gradescope_web as gw
from .gradescope_mc_labels import AFTER_SEND
from .gradescope_publish import PUBLISHING
from .models import digest, uid


def _sync():
    """The written exam's module, which imports this one: its helpers are looked up when used."""
    from . import gradescope_sync
    return gradescope_sync


def sheet_exams(assignments):
    """A course's bubble-sheet exams: containers whose versions Gradescope keeps apart, and single bubble-sheet assignments."""
    return [{"id": row["id"], "title": row["title"], "kind": row["kind"], "published": row["published"]}
            for row in assignments if row["bubble_sheet"] and not row["parent"] and row["kind"] in ("assignment_container", "assignment")]


class SheetsMixin:
    # --- what the page shows without contacting Gradescope ----------------------------------------------------------------
    def sheet_status(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            sends = []
            for row in db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 5", (aid,)):
                counts = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM frq_gradescope_sheet_uploads WHERE send_id=? GROUP BY status", (row["id"],))}
                waiting = db.execute("""SELECT error FROM frq_gradescope_sheet_uploads WHERE send_id=? AND status IN ('pending','uncertain') AND error!='' LIMIT 1""",
                                     (row["id"],)).fetchone()
                sends.append({"id": row["id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                              "plan": json.loads(row["plan_json"]), "counts": {"sheets": counts}, "waiting": waiting[0] if waiting else ""})
            targets = self._sheet_targets(db, plan)
            live = db.execute("SELECT 1 FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone()
            coverage = self._sheet_coverage(db, aid, plan, bool(live))
        return {"assignment_id": aid, **self.settings.status(), "targets": targets, "sends": sends, "coverage": coverage,
                "mc_check": self.mc_check_status(aid)}  # THE MC CHECK (gradescope_mc), from the records only

    def _sheet_matched(self, db, plan):
        """The students Faculty matched a sheet to (scans that cleared Inbox review, a student not in doubt), each with the sheet and
        its latest review as they are now, and how many sheets wait in scans still under Inbox review. The ONE rule `_sheet_coverage`
        and `sheet_coverage_detail` count."""
        reader = getattr(self.inbox, "bubble_sheets", None)
        matched, waiting, memo = {}, 0, {}  # memo: each exam's booklet versions read once for every run
        for run in (self.frq._bubble_runs(db, plan) if reader else []):
            items = reader._sheets(db, run["id"], memo)
            if run["instructor_review_required"]:
                waiting += len(items)
                continue
            for i in items:
                if i["current"]["enrollment_id"] and not i["current"]["flags"]["identity"]:
                    matched[(i["current"]["section_id"], i["current"]["enrollment_id"])] = (i["scan_page_id"], i["current"]["version"])
        return matched, waiting

    def _sheet_coverage(self, db, aid, plan, live=False):
        """What is on Gradescope now across EVERY send, from the receipts alone (read only): the students Faculty matched a sheet to
        (the sheets a send would consider: scans that cleared Inbox review, a student not in doubt) and how many of them have a sheet
        Faculty uploaded (accepted). Sheets of scans still waiting for Inbox review are only counted. While a send is live the
        matched sheets read at its first poll are reused (`_coverage_basis`); the uploads are read fresh every time."""
        def basis():
            matched, waiting = self._sheet_matched(db, plan)
            return set(matched), waiting
        matched, waiting = self._coverage_basis(("sheets", aid), live, basis)
        uploaded = {tuple(r) for r in db.execute("SELECT DISTINCT section_id,enrollment_id FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND status='accepted'",
                                                 (aid,))}
        return {"sheets": {"matched": len(matched), "sent": len(matched & uploaded), "waiting": waiting}}

    def sheet_coverage_detail(self, db, aid, plan):
        """`_sheet_coverage` per student, for Certification and Export (read only, receipts only): {students: {row: uploaded}, missing:
        [row], never: {row: reason}, waiting, totals}. A student whose accepted upload was another scanned sheet, or went to an
        other version, is never uploaded again by Faculty (Gradescope keeps one sheet per student): `never`, with nothing to send."""
        matched, waiting = self._sheet_matched(db, plan)
        uploads = {}
        for r in db.execute("""SELECT section_id,enrollment_id,scan_page_id,exam_version FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND status='accepted'
                ORDER BY created_at,rowid""", (aid,)):
            uploads[(r[0], r[1])] = (r[2], r[3])
        students, never = {}, {}
        for key, (page, version) in matched.items():
            row = f"{key[0]}:{key[1]}"
            students[row] = key in uploads
            # The send's own rule: the same scanned page, and the same version where the upload went to one.
            if key in uploads and (uploads[key][0] != page or (uploads[key][1] and uploads[key][1] != version)):
                never[row] = ("Gradescope has another sheet (or version) that Faculty uploaded for this student earlier; Faculty never replaces it. "
                              "Change it on Gradescope if it is wrong.")
        missing = sorted(row for row, sent in students.items() if not sent)
        return {"students": students, "missing": missing, "never": never, "waiting": waiting,
                "totals": {"matched": len(students), "sent": len(students) - len(missing), "waiting": waiting}}

    def sheet_problems(self, aid, send_id):
        """The sheets of a send that did not go through, with the student's name (never a UIN) and the reason."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            if not db.execute("SELECT 1 FROM frq_gradescope_sheet_sends WHERE id=? AND assignment_id=?", (send_id, aid)).fetchone():
                raise NotFound("That send was not found for this assignment.")
            students = self._term_students(db, plan)
            listed = []
            for row in db.execute("""SELECT u.enrollment_id,u.status,u.error,s.number,sp.source_page_index FROM frq_gradescope_sheet_uploads u
                    JOIN course_sections s ON s.id=u.section_id LEFT JOIN scan_pages sp ON sp.id=u.scan_page_id
                    WHERE u.send_id=? AND u.status NOT IN ('accepted','pending','sending') ORDER BY s.number,u.created_at""", (send_id,)):
                listed.append({"name": students.get(row[0], {}).get("name", ""), "section": row[3], "status": row[1], "question": "", "page": row[4], "reason": row[2]})
        return {"send_id": send_id, "problems": listed}

    # --- preview and send -------------------------------------------------------------------------------------------------
    def sheet_preview(self, aid, data=None):
        return self._sheet_plan(aid, (data or {}).get("targets"))["view"]

    def sheet_send(self, aid, data):
        gs = _sync()
        request_id, fingerprint = gs.request_head(data, gs.CONFIRMATION, no_id="Send needs a request ID from the preview.",
                                                  not_confirmed="Confirm the send: nothing goes to Gradescope without it.")
        requested = data.get("targets") or {}
        confirmed = gs.confirmed_published(data)
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "targets": requested, "kind": "sheets",
                          **({"published": confirmed} if confirmed else {})})
        with self.rosters.lock(), connect(self.path) as db:
            earlier = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE request_id=?", (request_id,)).fetchone()
        if earlier:
            if earlier["request_sha256"] != command or earlier["assignment_id"] != aid:
                raise Conflict("That request ID was already used for a different send.")
            return self._receipt(earlier)
        plan = self._sheet_plan(aid, requested)  # everything read again, Gradescope included
        if plan["view"]["fingerprint"] != fingerprint:
            raise Conflict("Something changed since the preview, in Faculty or on Gradescope. Look at the new preview and send again.")
        if plan["view"]["running"]:
            raise Conflict("A send of bubble sheets to Gradescope is still running for this assignment.")
        if not plan["uploads"]:
            raise Conflict("There is nothing to send.")
        gs.check_published(plan["published"], confirmed)
        try:
            return self._record_sheet_send(aid, plan, request_id, command, fingerprint)
        except sqlite3.IntegrityError:
            raise Conflict("Something changed since the preview (another send recorded some of these). Look at the new preview and send again.") from None

    def _record_sheet_send(self, aid, plan, request_id, command, fingerprint, actor="workspace", automatic=None, after=None):
        """`automatic`, `after` and `actor`: as `_record_send` (GRADESCOPE "AUTOMATIC LATE-PAPER SEND")."""
        now, send_id = timestamp(), uid()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            earlier = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE request_id=?", (request_id,)).fetchone()
            if earlier:
                if earlier["request_sha256"] != command:
                    raise Conflict("That request ID was already used for a different send.")
                return self._receipt(earlier)
            if db.execute("SELECT 1 FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
                raise Conflict("A send of bubble sheets to Gradescope is still running for this assignment.")
            if self._publishing(db, aid):  # a sheet would land on an exam published under it, and stop there
                raise Conflict(PUBLISHING)
            links = self._record_links(db, aid, plan["targets"], now, table="frq_gradescope_sheet_targets")
            summary = {"targets": plan["view"]["sections"], "summary": plan["view"]["summary"]}
            if plan["published"]:
                summary["published"] = _sync().published_record(plan["published"])  # what the sender may write to although its grades are published
            if automatic:
                summary["automatic"] = automatic  # a late paper's sheet Faculty certified and sent by itself
            db.execute("INSERT INTO frq_gradescope_sheet_sends VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                       (send_id, aid, request_id, command, fingerprint, json.dumps(summary, sort_keys=True), actor, now, now))
            for unit in plan["uploads"]:
                db.execute("""INSERT INTO frq_gradescope_sheet_uploads(id,send_id,assignment_id,section_id,enrollment_id,sheet_id,review_id,scan_page_id,content_sha256,
                    rotation,exam_version,course_id,container_id,gradescope_assignment_id,gradescope_user_id,pages,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'pending',?,?)""",
                    (uid(), send_id, aid, unit["section_id"], unit["enrollment_id"], unit["sheet_id"], unit["review_id"], unit["scan_page_id"], unit["content_sha256"],
                     unit["rotation"], unit["version"], unit["course_id"], unit["container_id"], unit["assignment"], unit["user"], now, now))
            self.frq.event(db, aid, "gradescope_sheet_send", {"send_id": send_id, "request_id": request_id, "fingerprint": fingerprint, "links": links,
                                                              "sheets": len(plan["uploads"]),
                                                              **({"published_confirmed": sorted(plan["published"])} if plan["published"] else {}),
                                                              **({"automatic": automatic} if automatic else {})}, actor)
            if after:
                after(db, send_id)
            row = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE id=?", (send_id,)).fetchone()
        self.wake.set()
        return self._receipt(row)

    def sheet_cancel(self, aid, send_id):
        """Stops a send: nothing still waiting is sent. A sheet already on its way finishes and keeps its receipt."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE id=? AND assignment_id=?", (send_id, aid)).fetchone()
            if not row:
                raise NotFound("That send was not found for this assignment.")
            if row["status"] in ("pending", "running"):
                now = timestamp()
                db.execute("UPDATE frq_gradescope_sheet_uploads SET status='cancelled',updated_at=? WHERE send_id=? AND status='pending'", (now, send_id))
                db.execute("UPDATE frq_gradescope_sheet_sends SET status='cancelled',updated_at=? WHERE id=?", (now, send_id))
                self.frq.event(db, aid, "gradescope_sheet_send_cancelled", {"send_id": send_id})
            row = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE id=?", (send_id,)).fetchone()
        return self._receipt(row)

    def sheet_retry(self, aid, send_id):
        """The instructor's explicit Retry: sheets that failed (Gradescope did not keep them, as Faculty read back) are sent again."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE id=? AND assignment_id=?", (send_id, aid)).fetchone()
            if not row:
                raise NotFound("That send was not found for this assignment.")
            if row["status"] == "cancelled":
                raise Conflict("A cancelled send is not retried. Preview and send again.")
            if db.execute("SELECT 1 FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND id!=? AND status IN ('pending','running')", (aid, send_id)).fetchone():
                raise Conflict("Another send of bubble sheets is running for this assignment.")
            if self._publishing(db, aid):
                raise Conflict(PUBLISHING)
            now = timestamp()
            # Gradescope keeps one sheet per student per exam: what a later send has uploaded, or is sending, is not sent again.
            retried = db.execute("""UPDATE frq_gradescope_sheet_uploads SET status='pending',not_before=NULL,error='',updated_at=? WHERE send_id=? AND status='failed'
                AND NOT EXISTS(SELECT 1 FROM frq_gradescope_sheet_uploads o WHERE o.container_id=frq_gradescope_sheet_uploads.container_id
                    AND o.gradescope_user_id=frq_gradescope_sheet_uploads.gradescope_user_id AND o.id!=frq_gradescope_sheet_uploads.id
                    AND o.status IN ('pending','sending','accepted','uncertain'))""", (now, send_id)).rowcount
            if not retried:
                left = db.execute("SELECT COUNT(*) FROM frq_gradescope_sheet_uploads WHERE send_id=? AND status='failed'", (send_id,)).fetchone()[0]
                raise Conflict("What failed in this send was sent again by a later send (or is being sent); preview and send again for anything still missing."
                               if left else "Nothing in this send failed.")
            db.execute("UPDATE frq_gradescope_sheet_sends SET status='running',updated_at=? WHERE id=?", (now, send_id))
            self.frq.event(db, aid, "gradescope_sheet_send_retried", {"send_id": send_id, "units": retried})
            row = db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE id=?", (send_id,)).fetchone()
        self.wake.set()
        return self._receipt(row)

    def _settle_sheet_sends(self, db, now):
        db.execute("""UPDATE frq_gradescope_sheet_uploads SET status='cancelled',updated_at=? WHERE status='pending'
            AND send_id IN (SELECT id FROM frq_gradescope_sheet_sends WHERE status='cancelled')""", (now,))
        for row in db.execute("SELECT id,assignment_id FROM frq_gradescope_sheet_sends WHERE status IN ('pending','running')").fetchall():
            if not db.execute("SELECT 1 FROM frq_gradescope_sheet_uploads WHERE send_id=? AND status IN ('pending','sending','uncertain')", (row["id"],)).fetchone():
                db.execute("UPDATE frq_gradescope_sheet_sends SET status='complete',updated_at=? WHERE id=?", (now, row["id"]))
                self.frq.event(db, row["assignment_id"], "gradescope_sheet_send_complete", {"send_id": row["id"]})
                # MC LABELS (requirement, 2026-10-28): once Gradescope has graded the sheets, their answers'
                # items are labelled with the correct answer.
                from .gradescope_sync import later
                self._mc_label_queue(db, row["assignment_id"], "automatic", later(AFTER_SEND))

    # --- links ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _sheet_targets(db, plan):
        """Each section's bubble-sheet exam as last chosen for this plan, in the section's own Gradescope course."""
        found = []
        for section in db.execute("""SELECT DISTINCT s.id,s.number FROM frq_gradescope_sheet_targets t JOIN course_sections s ON s.id=t.section_id
                WHERE t.assignment_id=? ORDER BY s.number""", (plan["id"],)).fetchall():
            course = _sync().section_course_row(db, section["id"])
            target = db.execute("SELECT * FROM frq_gradescope_sheet_targets WHERE assignment_id=? AND section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                (plan["id"], section["id"])).fetchone()
            same = bool(target and course and target["course_link_id"] == course["id"])
            found.append({"section_id": section["id"], "section": section["number"], "course_id": course["course_id"] if course else "",
                          "course_title": course["course_title"] if course else "", "container_id": target["container_id"] if same else "",
                          "container_title": target["container_title"] if same else ""})
        return found

    def _term_students(self, db, plan):
        """Names, section numbers and UINs of the term's students, read once from the verified roster (lifecycle lock held)."""
        reader = getattr(self.inbox, "bubble_sheets", None)
        sections = [dict(r) for r in db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number", (plan["term_id"],))]
        return reader.seating._students(db, plan["term_id"], sections) if reader and sections else {}

    # --- the plan ---------------------------------------------------------------------------------------------------------
    def _sheet_faculty(self, aid, student=None):
        """Everything Faculty knows for the plan's bubble sheets, read once under the lifecycle lock: each sheet of the cleared scans
        with its present student and version (as the Bubble sheets page and the Inbox see them), the sheets still in the Inbox (only
        counted), the students' UINs (catalog), the recorded links and every earlier receipt. With `student` ((section_id,
        enrollment_id), a late paper Faculty certified), only that student's sheets."""
        reader = getattr(self.inbox, "bubble_sheets", None)
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            event = self.frq.exams._event(db, plan["event_id"])
            sheets, waiting, memo = [], 0, {}  # memo: each exam's booklet versions read once for every run
            for run in (self.frq._bubble_runs(db, plan) if reader else []):
                items = reader._sheets(db, run["id"], memo)
                if run["instructor_review_required"]:
                    waiting += len(items)
                    continue
                for item in items:
                    current = item["current"]
                    page = db.execute("SELECT content_sha256 FROM scan_pages WHERE id=?", (item["scan_page_id"],)).fetchone()
                    sheets.append({"sheet_id": item["id"], "scan_page_id": item["scan_page_id"], "page": item["source_page_index"],
                                   "content_sha256": page["content_sha256"] if page else "", "rotation": int(item["reading"].get("rotation") or 0),
                                   "review_id": str(item["review"]["id"]) if item["review"] else None, "enrollment_id": current["enrollment_id"],
                                   "section_id": current["section_id"], "version": current["version"] or "", "flags": current["flags"], "reasons": current["reasons"],
                                   "repeat": current.get("status") == "repeat"})
            if student:
                sheets = [s for s in sheets if (s["section_id"], s["enrollment_id"]) == tuple(student)]
            students = self._term_students(db, plan) if sheets else {}
            enrolled = {s["enrollment_id"] for s in sheets if s["enrollment_id"]}
            uins = {row[0]: row[1] for row in db.execute(f"SELECT id,uin FROM student_enrollments WHERE id IN ({','.join('?' * len(enrolled))})", tuple(enrolled))} if enrolled else {}
            numbers = {row[0]: row[1] for row in db.execute("SELECT id,number FROM course_sections WHERE term_id=?", (plan["term_id"],))}
            courses = {}
            for section_id in numbers:
                course = _sync().section_course_row(db, section_id)
                if course:
                    courses[section_id] = course["course_id"]
            uploads = {(r["container_id"], r["gradescope_user_id"]): dict(r) for r in db.execute(
                "SELECT * FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND status='accepted' ORDER BY created_at,rowid", (aid,))}
            unsettled = {(r[0], r[1]): r[2] for r in db.execute("""SELECT container_id,gradescope_user_id,status FROM frq_gradescope_sheet_uploads
                             WHERE status IN ('pending','sending','uncertain')""")}
            live = db.execute("SELECT id FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone()
            targets = {t["section_id"]: t for t in self._sheet_targets(db, plan)}
        for sheet in sheets:
            student = students.get(sheet["enrollment_id"], {}) if sheet["enrollment_id"] else {}
            sheet["name"], sheet["section"] = student.get("name", ""), numbers.get(sheet["section_id"], "")
        return {"plan": plan, "event": event, "sheets": sheets, "waiting": waiting, "uins": uins, "numbers": numbers, "courses": courses,
                "uploads": uploads, "unsettled": unsettled, "live": live[0] if live else None, "targets": targets}

    def _sheet_view(self, aid, faculty):
        """The empty view `_sheet_compose` fills in for a send of bubble sheets (the preview, a send after certifying)."""
        return {"assignment_id": aid, **self.settings.status(), "courses": [], "sections": [], "summary": {}, "skipped": [], "warnings": [],
                "published": [], "fingerprint": None, "running": faculty["live"]}

    def _sheet_plan(self, aid, requested):
        faculty = self._sheet_faculty(aid)
        view = self._sheet_view(aid, faculty)
        if not view["configured"]:
            return {"view": view, "uploads": [], "targets": {}, "published": {}}
        requested = requested if isinstance(requested, dict) else {}
        session = self.session_factory()
        try:
            return self._sheet_compose(faculty, requested, session, view)
        except gw.GradescopeError as error:
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            session.close()

    def _sheet_compose(self, faculty, requested, session, view, student=None):
        gs = _sync()
        courses = session.courses()
        by_id = {c["id"]: c for c in courses}
        view["courses"] = [{"id": c["id"], "short": c["short"], "name": c["name"], "term": c["term"]} for c in courses]
        targets, gradescope, left_out = {}, {}, set()
        published = {}  # exam ID -> its title and assignment IDs, for each section sent to an exam whose grades are published
        fingerprint = {"targets": {}, "uploads": [], "skipped": []}
        if student:
            fingerprint["students"] = [student]  # only when asked: a late paper Faculty certified (AUTOMATIC LATE-PAPER SEND)
        sections = sorted({s["section_id"] for s in faculty["sheets"] if s["section_id"] and s["enrollment_id"]}, key=lambda s: faculty["numbers"].get(s, 0))
        for section_id in sections:
            number = faculty["numbers"].get(section_id, "")
            ask = requested.get(section_id) if isinstance(requested.get(section_id), dict) else {}
            saved = faculty["targets"].get(section_id) or {}
            if ask.get("leave_out") is True:
                # Left out of THIS send: its course is not read, nothing of it is sent, no link is recorded.
                left_out.add(section_id)
                fingerprint["targets"][section_id] = {"left_out": True}
                view["sections"].append({"section_id": section_id, "section": number, "course_id": str(ask.get("course_id") or saved.get("course_id") or ""),
                                         "course": "", "proposed": False, "containers": [], "container_id": str(ask.get("container_id") or ""), "container": "",
                                         "versions": {}, "problem": "", "left_out": True})
                continue
            # The section's Gradescope course is the one its written exam was sent to, when there is one: one course per section.
            course_id = str(ask.get("course_id") or saved.get("course_id") or faculty["courses"].get(section_id) or "")
            proposed = False
            if not course_id:
                course = gs.propose_course(courses, number, faculty["event"]["term_label"])
                course_id, proposed = (course["id"], True) if course else ("", False)
            linked = saved.get("course_id") or faculty["courses"].get(section_id) or ""
            entry = {"section_id": section_id, "section": number, "course_id": course_id, "course": "", "proposed": proposed,
                     "linked": bool(linked) and linked == course_id,  # the section's own link: shown as a fact
                     "containers": [], "container_id": "", "container": "", "versions": {}, "problem": ""}
            view["sections"].append(entry)
            if not course_id:
                entry["problem"] = "Choose this section's Gradescope course."
                continue
            if course_id not in by_id:
                entry["problem"] = "That Gradescope course is not one of your courses (or is one Faculty never touches)."
                continue
            course = by_id[course_id]
            entry["course"] = gw.course_label(course)
            exams = sheet_exams(session.assignments(course_id))
            entry["containers"] = [{"id": e["id"], "title": e["title"], "versions": []} for e in exams]
            container_id = str(ask.get("container_id") or (saved.get("container_id") if saved.get("course_id") == course_id else "") or "")
            if not container_id:
                # Proposed only when exactly one bubble-sheet exam carries the assignment's number ("Test 1", "Common Exam 1") and names
                # no other kind of work (`fits_title`).
                fitting = [e for e in exams if gs.fits_title(faculty["plan"]["title"], e["title"])]
                if len(fitting) == 1:
                    container_id, entry["proposed"] = fitting[0]["id"], True
            exam = next((e for e in exams if e["id"] == container_id), None)
            if not exam:
                entry["problem"] = "Choose the Gradescope bubble-sheet exam these sheets go to in this course."
                continue
            entry["container_id"], entry["container"] = exam["id"], exam["title"]
            if exam["kind"] == "assignment_container":
                detail = session.exam(course_id, exam["id"])
            else:
                listing = session.submissions(course_id, exam["id"])
                detail = {"versions": {"": exam["id"]}, "students": listing["students"], "submissions": listing["submissions"]}
            if not detail["versions"]:
                entry["problem"] = f"{exam['title']} has no versions to upload to."
                continue
            entry["versions"] = detail["versions"]
            next(c for c in entry["containers"] if c["id"] == exam["id"])["versions"] = sorted(v for v in detail["versions"] if v)
            targets[section_id] = {"course_id": course_id, "course_title": course["short"] or course["name"], "course_name": course["name"],
                                   "container_id": exam["id"], "container_title": exam["title"], "versions": detail["versions"]}
            if exam["published"]:
                # Sent to only with the instructor's confirmation of this exam (`check_published`); the preview says so, and a change of
                # publication makes the preview stale (the fingerprint names it).
                entry["published"], entry["warning"] = True, gs.published_warning(exam["title"], "sheet")
                targets[section_id]["published"] = True
                published[exam["id"]] = {"title": exam["title"], "ids": [exam["id"], *detail["versions"].values()]}
                view["published"].append({"section_id": section_id, "section": number, "course": entry["course"], "container_id": exam["id"],
                                          "container": exam["title"]})
            fingerprint["targets"][section_id] = targets[section_id]
            by_sid = {}
            for student in session.roster(course_id):
                by_sid.setdefault(student["sid"], []).append(student)
            gradescope[section_id] = {"course": course, "by_sid": by_sid, "exam": detail, "container": exam["id"],
                                      "students": {s["id"]: s for s in detail["students"]},
                                      "owners": {user: sub for sub, users in detail["submissions"].items() for user in users}}
        view["sections"] = sorted(view["sections"], key=lambda s: s["section"] or 0)

        uploads, planned = [], set()
        counts = {"sheets": {"upload": 0, "already": 0, "skipped": 0}, "read": len(faculty["sheets"]), "waiting": faculty["waiting"]}
        if left_out:
            counts["sheets"]["left_out"] = 0

        def skip(sheet, reason):
            counts["sheets"]["skipped"] += 1
            fingerprint["skipped"].append([sheet["sheet_id"], reason])
            if len(view["skipped"]) < gs.LISTED:
                view["skipped"].append({"name": sheet["name"], "section": sheet["section"], "page": sheet["page"], "reason": reason})

        for sheet in faculty["sheets"]:
            if sheet["repeat"]:
                # BUBBLE-SHEETS "A SHEET SCANNED TWICE": the same sheet in a later scan is nobody's; its first copy is the one that goes.
                # Counted, never listed as skipped (requirement, 2026-10-27).
                counts["sheets"]["repeats"] = counts["sheets"].get("repeats", 0) + 1
                continue
            if not sheet["enrollment_id"]:
                skip(sheet, sheet["reasons"][0] if sheet["reasons"] else "No student is matched to this sheet yet.")
                continue
            if sheet["flags"]["identity"]:
                skip(sheet, next((r for r in sheet["reasons"] if "student" in r.lower()), "Its student is in doubt. Review the sheet in the Inbox."))
                continue
            section_id = sheet["section_id"]
            if section_id in left_out:
                counts["sheets"]["left_out"] += 1
                continue
            if section_id not in gradescope:
                problem = next((s["problem"] for s in view["sections"] if s["section_id"] == section_id and s["problem"]), "")
                skip(sheet, f"Section {sheet['section']}: {problem}" if problem else f"Section {sheet['section']} has no Gradescope bubble-sheet exam chosen.")
                continue
            course = gradescope[section_id]
            versions = course["exam"]["versions"]
            if list(versions) == [""]:
                version = ""  # one bubble-sheet assignment without versions: every sheet goes to it
            elif sheet["flags"]["version"] or not sheet["version"]:
                skip(sheet, "No version was read on this sheet. Record it in the sheet's review in the Inbox.")
                continue
            elif sheet["version"] not in versions:
                skip(sheet, f"The Gradescope exam has no version {sheet['version']}.")
                continue
            else:
                version = sheet["version"]
            uin = faculty["uins"].get(sheet["enrollment_id"], "")
            matches = course["by_sid"].get(uin, []) if uin else []
            if len(matches) != 1:
                skip(sheet, f"{'No student' if not matches else 'More than one student'} on the Gradescope roster of {course['course']['short']} has this student's UIN.")
                continue
            user, container = matches[0]["user_id"], course["container"]
            ours = faculty["uploads"].get((container, user))
            if ours:
                if (ours["scan_page_id"], ours["exam_version"], ours["enrollment_id"]) != (sheet["scan_page_id"], version, sheet["enrollment_id"]):
                    skip(sheet, "Gradescope has another sheet (or version) that Faculty uploaded for this student earlier; Faculty never replaces it. "
                                "Change it on Gradescope if it is wrong.")
                elif course["owners"].get(user) != ours["gradescope_submission_id"]:
                    skip(sheet, "Gradescope no longer has the sheet Faculty uploaded for this student. Faculty keeps its receipt and does not upload it "
                                "again; upload it on Gradescope if it is needed.")
                else:
                    counts["sheets"]["already"] += 1
                continue
            if faculty["unsettled"].get((container, user)):
                skip(sheet, "An earlier send still has a sheet for this student waiting or being checked; preview again when it has finished.")
                continue
            if (course["students"].get(user) or {}).get("submission") or user in course["owners"]:
                skip(sheet, "Gradescope already has a sheet for this student that Faculty did not upload; it is left alone.")
                continue
            if (container, user) in planned:
                skip(sheet, "Another sheet going in this send is already this student's.")
                continue
            planned.add((container, user))
            counts["sheets"]["upload"] += 1
            unit = {"section_id": section_id, "enrollment_id": sheet["enrollment_id"], "sheet_id": sheet["sheet_id"], "review_id": sheet["review_id"],
                    "scan_page_id": sheet["scan_page_id"], "content_sha256": sheet["content_sha256"], "rotation": 180 if sheet["rotation"] == 180 else 0,
                    "version": version, "course_id": course["course"]["id"], "container_id": container, "assignment": versions[version], "user": user}
            uploads.append(unit)
            fingerprint["uploads"].append([unit[k] for k in ("sheet_id", "review_id", "scan_page_id", "content_sha256", "rotation", "version", "assignment", "user")])
        view["summary"] = counts
        view["targets"] = {section_id: {"course_id": t["course_id"], "container_id": t["container_id"]} for section_id, t in targets.items()}
        # A published exam asks for the instructor's confirmation only where this send uploads something to it, and says how much.
        writes = {}
        for unit in uploads:
            writes[unit["section_id"]] = writes.get(unit["section_id"], 0) + 1
        view["published"] = [{**p, "sheets": writes[p["section_id"]]} for p in view["published"] if p["section_id"] in writes]
        published = {p["container_id"]: published[p["container_id"]] for p in view["published"]}
        view["fingerprint"] = digest(fingerprint)
        return {"view": view, "uploads": uploads, "targets": targets, "published": published}

    # --- the background sender ------------------------------------------------------------------------------------------
    def _sheet_page(self, upload):
        """The sheet's scanned page as one PDF: still the student's current sheet, with the version and review that were approved,
        from a scan still cleared and read last, the very page read (its checksum), turned upright if it was read upside down."""
        reader = getattr(self.inbox, "bubble_sheets", None)
        if reader is None:
            raise Conflict("Bubble sheets are not available here.")
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("""SELECT b.run_id,sp.page_artifact_id,sp.content_sha256 FROM bubble_sheets b JOIN scan_pages sp ON sp.id=b.scan_page_id
                WHERE b.id=? AND b.scan_page_id=?""", (upload["sheet_id"], upload["scan_page_id"])).fetchone()
            if not row:
                raise Conflict("The sheet approved for sending is gone.")
            plan = self.frq.plan(db, upload["assignment_id"])
            run = next((r for r in self.frq._bubble_runs(db, plan) if r["id"] == row["run_id"]), None)
            if not run or run["instructor_review_required"]:
                raise Conflict("The sheet's scan was read again, deleted or reopened after the send was approved; preview and send again.")
            item = next((i for i in reader._sheets(db, run["id"]) if i["id"] == upload["sheet_id"]), None)
            current = item["current"] if item else {}
            review = str(item["review"]["id"]) if item and item["review"] else None  # the unit keeps it as text
            if not item or current["enrollment_id"] != upload["enrollment_id"] or current["flags"]["identity"] or review != upload["review_id"] \
                    or (upload["exam_version"] and (current["version"] != upload["exam_version"] or current["flags"]["version"])):
                raise Conflict("The sheet's student or version changed after the send was approved; preview and send again.")
            if row["content_sha256"] != upload["content_sha256"]:
                raise Conflict("The sheet's page is not the one approved.")
        _, content = self.inbox.read_artifact(row["page_artifact_id"])
        if _sync().sha(content) != upload["content_sha256"]:
            raise Conflict("The sheet's page does not match the page that was read.")
        return process.merge([(content, upload["rotation"])]) if upload["rotation"] else content

    @staticmethod
    def _exam_published(session, course_id, container_id):
        row = next((r for r in session.assignments(course_id) if r["id"] == str(container_id)), None)
        if row is None:
            raise gw.GradescopeError("The Gradescope exam is gone from its course.")
        return row["published"]
