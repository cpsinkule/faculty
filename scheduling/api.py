"""SCHEDULING routes: a course term's Term schedule (read, save, a first revision proposed from the calendar). An assistant write is
an Approve card (assistant/policy.py: these routes are not in its automatic tier)."""
import datetime
import re

from flask import Blueprint, jsonify, request

from request_actor import current as actor

from .clock import hhmm


def register(app, service):
    api = Blueprint("scheduling", __name__, url_prefix="/api/scheduling")

    @api.get("/groups/<group_id>")
    def by_group(group_id):
        """The Term schedule of a course page's group (one course code and term)."""
        return jsonify(service.read(service.group_term(group_id)))

    @api.get("/terms/<term_id>")
    def read(term_id):
        return jsonify(service.read(term_id))

    @api.post("/terms/<term_id>")
    def save(term_id):
        return jsonify(service.save(term_id, request.get_json(), actor()))

    @api.get("/terms/<term_id>/import")
    def proposal(term_id):
        return jsonify(service.import_proposal(term_id))

    @api.get("/terms/<term_id>/days-off")
    def days_off(term_id):
        """The saved schedule's days the term's calendar keeps free (DAYS OFF), to add before using it; writes nothing."""
        found = service.read(term_id)
        return jsonify({"term_id": term_id, "days_off": service.days_off(term_id, found["first_day"], found["last_day"], found["entries"])
                        if found["revision"] else []})

    # USE THIS SCHEDULE (stage 1d): the dry run, then Use against its fingerprint, and Undo; each an explicit instructor action.
    @api.get("/terms/<term_id>/use")
    def use_preview(term_id):
        from .service import use_preview
        return jsonify(use_preview(service, term_id))

    @api.post("/terms/<term_id>/use")
    def use(term_id):
        from .service import use
        return jsonify(use(service, term_id, request.get_json(), actor()))

    @api.post("/terms/<term_id>/use/undo")
    def undo(term_id):
        from .service import undo
        return jsonify(undo(service, term_id, request.get_json(), actor()))

    @api.get("/check")
    def check():
        """A proposed office-hours appointment against the calendar (SCHEDULING ONE ENGINE): what it overlaps and whether it falls
        outside the term's office and meeting hours. A warning for the appointment window; saving is never refused by it."""
        from rosters import connect
        from student_support import meeting_hours
        from .clock import parse_clock
        from .engine import check as engine_check
        from .feed import assemble
        from .service import availability
        args = request.args
        day, start, end = args.get("date", ""), parse_clock(args.get("time")), parse_clock(args.get("end_time"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or start is None or end is None or end <= start:
            return jsonify({"overlaps": [], "outside_hours": False})
        with connect(service.path) as db:
            term = args.get("term_id") or next((row[0] for row in db.execute("SELECT term_id FROM student_enrollments WHERE id=?",
                                                                             (args.get("enrollment_id", ""),))), None)
            events = assemble(db, day, day, calendar=app.extensions.get("calendar"), google=app.extensions.get("google_calendar") or None,
                              on_google_error=lambda: None)
            hours = availability(db, term) if term else {"windows": []}
        found = engine_check(events, new_kind="appointment", date=day, start=start, end=end, own=args.get("own") or None)
        weekday = datetime.date.fromisoformat(day).weekday()
        inside = any(e.kind == "office_hours" for e in found["within"]) or meeting_hours.contains(hours["windows"], weekday, start, end)
        view = lambda e: {"title": e.title, "time": hhmm(e.start), "end_time": hhmm(e.end), "source": e.source}  # noqa: E731
        return jsonify({"overlaps": [view(e) for e in found["overlaps"][:5]], "outside_hours": bool(hours["windows"]) and not inside})

    # CLASS SCHEDULE (2026-10-10): a section's dated list (a read over the records that own each date), the term's plan behind it
    # (planned rows and class-day topics), and the planned rows a record being created may follow. Planned rows and topics reach no
    # other route: the Calendar, Home and Google are unchanged.
    def section_of(db, term_id, number):
        from werkzeug.exceptions import NotFound
        row = db.execute("SELECT id FROM course_sections WHERE term_id=? AND number=?", (term_id, str(number))).fetchone()
        if not row:
            raise NotFound("That section is not part of this course term.")
        return row[0]

    @api.get("/sections/<section_id>/class-schedule")
    def class_schedule(section_id):
        """One section's class schedule over ?start&end (both, YYYY-MM-DD); without them two weeks back to four ahead."""
        from rosters import connect
        from .class_schedule import section_schedule
        with connect(service.path) as db:
            db.execute("BEGIN")
            return jsonify(section_schedule(db, section_id, request.args.get("start"), request.args.get("end")))

    @api.get("/groups/<group_id>/sections/<number>/class-schedule")
    def class_schedule_group(group_id, number):
        """The Class schedule page of a course page's section: every date the schedule has, the term's sections and its plan."""
        from rosters import connect
        from .class_schedule import page
        term_id = service.group_term(group_id)
        with connect(service.path) as db:
            db.execute("BEGIN")
            return jsonify(page(db, section_of(db, term_id, number)))

    @api.get("/terms/<term_id>/class-schedule")
    def class_plan(term_id):
        from rosters import connect
        from .class_schedule import read as read_plan
        with connect(service.path) as db:
            db.execute("BEGIN")
            return jsonify(read_plan(db, term_id))

    @api.post("/terms/<term_id>/class-schedule")
    def class_plan_save(term_id):
        from .class_schedule import save as save_plan
        return jsonify(save_plan(service, term_id, request.get_json(), actor()))

    @api.post("/terms/<term_id>/class-schedule/preview")
    def class_plan_preview(term_id):
        """What a change to the plan would leave, checked as a save checks it; writes nothing (an Approve card's preview)."""
        from .class_schedule import preview
        return jsonify(preview(service, term_id, request.get_json()))

    @api.get("/terms/<term_id>/class-schedule/open")
    def class_plan_open(term_id):
        """The planned rows no record follows yet (?kind narrows them), for a form about to create one; writes nothing."""
        from rosters import connect
        from .class_schedule import open_items
        with connect(service.path) as db:
            db.execute("BEGIN")
            return jsonify({"term_id": term_id, "sections": service._sections(db, term_id),
                            "items": open_items(db, term_id, request.args.get("kind") or None)})

    @api.get("/terms/<term_id>/dr-quizzes")
    def dr_quizzes(term_id):
        """CLASS SCHEDULE: DR QUIZZES: the term's upcoming quizzes to preschedule with the DR center, against the imported DR
        bookings; the one read of planned rows outside the class schedule (NOT ELSEWHERE). Writes nothing."""
        from rosters import connect
        from .dr_quizzes import term_quizzes
        with connect(service.path) as db:
            db.execute("BEGIN")
            return jsonify(term_quizzes(db, term_id))

    # DR DATE (2026-10-31): the day a row's DR quiz takers sit the quiz at the DR center, when not the class's day; answers the read.
    @api.post("/terms/<term_id>/dr-quizzes/dr-date")
    def dr_quiz_date(term_id):
        from .dr_quiz_sittings import record
        record(service, term_id, request.get_json(), actor())
        return dr_quizzes(term_id)

    # DR QUIZ NOTICES (2026-10-15): the "Prescheduled in AIM" marks, each DR quiz taker's notice email and its explicit Retry. Each
    # POST answers the status read; the automatic email goes on the notices worker's next turn (dr_quiz_notices.DrQuizNotices).
    @api.get("/terms/<term_id>/aim-prescheduling")
    def aim_prescheduling(term_id):
        from rosters import connect
        from .dr_quiz_notices import status
        people = service.dr_quiz_notices.mail.roster_people()
        with connect(service.path) as db:
            db.execute("BEGIN")
            return jsonify(status(db, term_id, people))

    @api.post("/terms/<term_id>/aim-prescheduling/mark")
    def aim_prescheduling_mark(term_id):
        from .dr_quiz_notices import mark
        mark(service, term_id, request.get_json(), actor())
        return aim_prescheduling(term_id)

    @api.post("/terms/<term_id>/aim-prescheduling/unmark")
    def aim_prescheduling_unmark(term_id):
        from .dr_quiz_notices import unmark
        unmark(service, term_id, request.get_json(), actor())
        return aim_prescheduling(term_id)

    @api.post("/terms/<term_id>/aim-prescheduling/retry")
    def aim_prescheduling_retry(term_id):
        from .dr_quiz_notices import retry
        retry(service.dr_quiz_notices, term_id, request.get_json(), actor())
        return aim_prescheduling(term_id)

    # SEND TO AIM (2026-10-27): what Send to AIM would do, the instructor's Send (the sign-in window then follows it through the AIM
    # session routes and imports the exported bookings), and Retry of a write AIM did not confirm.
    @api.get("/terms/<term_id>/aim-send")
    def aim_send_plan(term_id):
        from flask import current_app
        from .aim_send import read_aim_plan
        return jsonify(read_aim_plan(service, current_app.extensions["frq_grading"], term_id))

    @api.post("/terms/<term_id>/aim-send")
    def aim_send_start(term_id):
        from flask import current_app
        from aim_import import PORTAL
        from .aim_send import start
        found = start(service, current_app.extensions["frq_grading"], current_app.extensions["aim_sessions"],
                      current_app.config.get("AIM_PORTAL", PORTAL), term_id, request.get_json(), actor(),
                      dr_exams=current_app.extensions["accommodations"].dr_exams, inbox=current_app.extensions["inbox"])
        return jsonify(found), 201

    # DR REQUEST EMAILS (2026-11-01): the Follow-up card's upload of one DR quiz's copy, started by the instructor's Approve; the page
    # that approved it follows the session it answers in the sign-in window (Duo).
    @api.post("/terms/<term_id>/aim-send/uploads")
    def aim_upload(term_id):
        from flask import current_app
        from aim_import import PORTAL
        from .aim_send import upload_start
        found = upload_start(service, current_app.extensions["frq_grading"], current_app.extensions["aim_sessions"],
                             current_app.config.get("AIM_PORTAL", PORTAL), term_id, request.get_json(), actor(),
                             dr_exams=current_app.extensions["accommodations"].dr_exams, inbox=current_app.extensions["inbox"])
        return jsonify(found), 201

    @api.post("/terms/<term_id>/aim-send/retry")
    def aim_send_retry(term_id):
        from .aim_send import retry
        retry(service, term_id, request.get_json(), actor())
        return aim_send_plan(term_id)

    @api.post("/terms/<term_id>/class-schedule/links")
    def class_plan_link(term_id):
        from .class_schedule import link
        return jsonify(link(service, term_id, request.get_json(), actor()))

    app.register_blueprint(api)
