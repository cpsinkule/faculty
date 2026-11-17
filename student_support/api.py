"""TEACHING-WORKFLOWS routes; writes retain the ordinary assistant approval tier."""
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import NotFound

from request_actor import current as actor


def register(app, service):
    api = Blueprint("student_support", __name__, url_prefix="/api/student-support")

    def suggestions():
        if not service.suggestions:
            raise NotFound("Follow-up suggestions are not available here.")
        return service.suggestions

    @api.get("")
    def listing():
        # The Follow-up page's own read (suggestions=1) also names the instructor's latest reply (WAITING ON). Its list read adds
        # brief=1: a resolved follow-up's drafts and evidence wait until it is opened (RESOLVED WHEN OPENED, TEACHING-WORKFLOWS).
        result = service.listing(request.args.get("kind"), request.args.get("enrollment_id"), request.args.get("suggestions") == "1")
        if request.args.get("suggestions") == "1" and service.suggestions:
            result = service.suggestions.decorate(result, request.args.get("enrollment_id"), request.args.get("brief") == "1")
        return jsonify(result)

    # ASSISTANT: enrollment-owned context reads and an explicitly approved watch.
    @api.get('/students/<enrollment_id>/context')
    def student_context(enrollment_id):
        return jsonify(suggestions().context.read(enrollment_id, request.args.get('source_key', '')))

    @api.get('/followups/watch-options/<enrollment_id>')
    def watch_options(enrollment_id):
        return jsonify(suggestions().watches.options(enrollment_id))

    @api.post('/followups/watches/preview')
    def watch_preview():
        return jsonify(suggestions().watches.preview(request.get_json()))

    @api.post('/followups/watches')
    def watch_create():
        return jsonify(suggestions().watches.create(request.get_json())), 201

    @api.post('/followups/watches/<ident>')
    def watch_update(ident):
        return jsonify(suggestions().watches.update(ident, request.get_json()))

    # ASSISTANT: these suggestion controls are instructor-only (assistant/policy.py NEVER).
    # Answers, requested changes, notes and Prepare now never approve a card.
    @api.post("/followups/suggestions/<run_id>/<any(revise, retry, answer):operation>")
    def suggestion_request(run_id, operation):
        return jsonify(suggestions().request(run_id, operation, request.get_json())), 202

    @api.post("/followups/suggestions/<run_id>/approve")
    def suggestion_approve(run_id):
        return jsonify(suggestions().approve_all(run_id, request.get_json()))

    @api.post("/followups/suggestions/<run_id>/replace")
    def suggestion_replace(run_id):
        return jsonify(suggestions().replace(run_id, request.get_json(), actor()))

    @api.post("/followups/suggestions/<run_id>/edit")
    def suggestion_edit(run_id):
        return jsonify(suggestions().edit(run_id, request.get_json(), actor()))

    @api.post("/followups/suggestions/<run_id>/undo")
    def suggestion_undo(run_id):
        return jsonify(suggestions().undo(run_id, request.get_json(), actor()))

    @api.post("/followups/suggestions/<run_id>/note")
    def suggestion_note(run_id):
        return jsonify(suggestions().note(run_id, request.get_json(), actor()))

    @api.post("/followups/suggestions/prepare")
    def suggestion_prepare():
        return jsonify(suggestions().prepare(request.get_json(), actor())), 202

    # REDO FROM CHAT (ASSISTANT): the one suggestion request the Assistant chat may make, at the instructor's word: prepare this
    # follow-up's suggestions again, with their changes. Automatic, also when it replaces steps still waiting (NO CARD FOR A REDO).
    @api.post("/followups/suggestions/redo")
    def suggestion_redo():
        return jsonify(suggestions().redo(request.get_json(), actor())), 202

    @api.post("/followups/suggestions/mute")
    def suggestion_mute():
        return jsonify(suggestions().mute(request.get_json(), actor()))

    @api.post("/followups/suggestions/pause")
    def suggestion_pause():
        return jsonify(suggestions().pause(request.get_json(), actor()))

    def office_rooms():
        """MAKEUP ROOMS: the rooms of the instructor's own office-hours blocks over the next six weeks, for the window to tick (the
        calendar as makeup cards read it; [] when it cannot be read)."""
        from datetime import date, timedelta
        from absences import makeup_slots as slots
        from exam_workflow.overview import campus_today
        from rosters import connect
        booking = getattr(app.extensions.get("absences"), "booking", None)
        if booking is None:
            return []
        today = date.fromisoformat(campus_today())
        try:
            with connect(service.path) as db:
                events = booking.calendar_events(db, today.isoformat(), (today + timedelta(days=42)).isoformat())
        except Exception:  # a convenience for the window: the rooms can still be saved without it
            app.logger.exception("Office-hours rooms could not be read for the Term policies window")
            return []
        return sorted({" ".join(str(e.get("location") or "").split()) for e in events
                       if slots.is_office_block(e) and not slots.is_makeup(e) and str(e.get("location") or "").strip()})

    # TERM POLICIES: the instructor's policies per course term, read by follow-up suggestions (an assistant write is an Approve card).
    @api.get("/policies")
    def policies():
        areas = request.args.get("areas") == "1"
        found = service.policies(include_areas=areas)
        if areas:
            # SCHEDULING "USE THIS SCHEDULE": a term that uses its Term schedule takes its meeting hours and makeup rooms from there.
            from course_groups import navigation_id
            from assistant.followup_guidance import suggestions as suggested_guidance
            from rosters import connect
            from scheduling.service import availability, current_use
            with connect(service.path) as db:
                for term in found["terms"]:
                    term['suggested_guidance'] = suggested_guidance(db, term['term_id'], term['content'])
                    row = db.execute("SELECT * FROM course_terms WHERE id=?", (term["term_id"],)).fetchone()
                    hours = availability(db, term["term_id"])
                    group = navigation_id({"id": row["id"], "code": row["code"], "term": row["term"], "term_other": row["term_other"], "year": row["year"]})
                    term["schedule"] = {"in_use": bool(current_use(db, term["term_id"])), "href": f"#course/{group}/home/schedule",
                                        "windows": hours["windows"], "makeup_rooms": hours["makeup_rooms"]}
        return jsonify({**found, "office_rooms": office_rooms()} if areas else found)

    @api.post("/policies/<term_id>")
    def save_policy(term_id):
        return jsonify(service.save_policy(term_id, request.get_json(), actor()))

    # MEETING HOURS: a term's weekly windows for individual meetings and quiz makeup times (an assistant write is an Approve card).
    @api.post("/meeting-hours/<term_id>")
    def save_meeting_hours(term_id):
        return jsonify(service.save_meeting_hours(term_id, request.get_json(), actor()))

    @api.post("")
    def create():
        return jsonify(service.save(request.get_json(), actor=actor())), 201

    @api.post("/<item_id>")
    def revise(item_id):
        return jsonify(service.save(request.get_json(), item_id, actor()))

    # WRITTEN FROM A FOLLOW-UP: the instructor's own Write to student… records the send it made (assistant/policy.py NEVER).
    @api.post("/followups/written")
    def written():
        return jsonify(service.written(request.get_json(), actor()))

    # SAME MATTER: the instructor's join of two conversations into one follow-up, and Keep separate. CONNECT A MESSAGE
    # (2026-10-10): asked in the chat, the Assistant does either at once (assistant/policy.py AUTO), the join after this
    # preview, which writes nothing.
    @api.post("/followups/join/preview")
    def join_preview():
        return jsonify(service.join_preview(request.get_json()))

    @api.post("/followups/join")
    def join():
        return jsonify(service.join(request.get_json(), actor()))

    @api.post("/followups/separate")
    def separate():
        return jsonify(service.separate(request.get_json(), actor()))

    @api.post("/followups/start-now/preview")
    def start_now_preview():
        return jsonify(service.start_now_preview(request.get_json()))

    @api.post("/wellbeing/reviewed")
    def wellbeing_reviewed():
        # JEV WELLBEING: the instructor's own control; the assistant never clears a concern (assistant/policy.py NEVER).
        return jsonify(service.wellbeing_reviewed(request.get_json(), actor()))

    @api.post("/followups/start-now")
    def start_now():
        return jsonify(service.start_now(request.get_json(), actor()))

    app.register_blueprint(api)
