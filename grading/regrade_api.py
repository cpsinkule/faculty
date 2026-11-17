"""Regrade requests on the Review step (AGENTS.md "Regrade requests"): a plan's captured Gradescope regrade requests, and the reply the
instructor posts to Gradescope from Faculty. Writes pass the app's same-origin checks; nothing here is reachable with Inbox keys or
codes. Gradescope's own failures answer 502 with the reason (never 401/404/409, which the app reserves)."""
from functools import partial

from flask import Blueprint, jsonify, request

from request_limits import json_object
from rosters import connect

from . import gradescope_web as gw
from .regrade_followup import work as placed


def register(app, service):
    api = Blueprint("frq_regrades", __name__, url_prefix="/api/grading/assignments/<aid>/regrade-requests")

    body = partial(json_object, silent=True)

    def gradescope(call):
        try:
            return jsonify(call())
        except gw.GradescopeError as error:
            return jsonify(error=str(error)), 502

    @api.get("")
    def listing(aid):
        return jsonify(service.listing(aid, request.args.get("response_id") or None))

    @api.post("/<message_id>/reply/preview")
    def reply_preview(aid, message_id):
        return gradescope(lambda: service.preview(aid, message_id, body()))

    @api.post("/<message_id>/reply")
    def reply(aid, message_id):
        return gradescope(lambda: service.reply(aid, message_id, body()))

    @api.post("/<message_id>/mark")
    def mark(aid, message_id):
        return jsonify(service.mark(aid, message_id, body()))

    @api.post("/replies/<reply_id>/check")
    def check(aid, reply_id):
        body()
        return gradescope(lambda: service.check(aid, reply_id))

    app.register_blueprint(api)


def register_panel(app, panel, drafts):
    """The Return stage's Regrade requests (REGRADES, 2026-10-07): the list with names, one request's panel, the reply drafted by a
    model, and Apply (that grade on Gradescope, the reply, the gradebook through a certification of just this change, the student's
    Canvas cell). Built after the certification and the gradebook it reaches."""
    api = Blueprint("frq_regrade_panel", __name__, url_prefix="/api/grading/assignments/<aid>/regrade-requests")

    body = partial(json_object, silent=True)

    def gradescope(call):
        try:
            return jsonify(call())
        except gw.GradescopeError as error:
            return jsonify(error=str(error)), 502

    @api.get("/panel")
    def panel_listing(aid):
        return jsonify(panel.listing(aid))

    @api.get("/<message_id>")
    def detail(aid, message_id):
        return jsonify(panel.detail(aid, message_id))

    @api.post("/<message_id>/draft")
    def draft(aid, message_id):
        body()
        return jsonify(drafts.request(aid, message_id))

    @api.post("/<message_id>/apply/preview")
    def apply_preview(aid, message_id):
        return gradescope(lambda: panel.preview(aid, message_id, body()))

    @api.post("/<message_id>/apply")
    def apply(aid, message_id):
        return gradescope(lambda: panel.apply(aid, message_id, body()))

    # A decision (REGRADES "FOLLOW-UP"): a Follow-up card's keep or change with its reply; its preview reads and writes nothing.
    @api.post("/<message_id>/decide/preview")
    def decide_preview(aid, message_id):
        return gradescope(lambda: panel.decide_preview(aid, message_id, body()))

    @api.post("/<message_id>/decide")
    def decide(aid, message_id):
        return gradescope(lambda: panel.decide(aid, message_id, body()))

    @api.get("/applies/<run_id>")
    def run(aid, run_id):
        return jsonify(panel.run(aid, run_id))

    @api.post("/applies/<run_id>/continue")
    def advance(aid, run_id):
        retry = body().get("retry")
        if retry is not None and retry not in ("gradescope", "reply", "gradebook", "canvas"):
            raise ValueError("Name the step to try again.")
        return gradescope(lambda: panel.advance(aid, run_id, retry=retry))

    @api.post("/applies/<run_id>/cancel")
    def cancel(aid, run_id):
        body()
        return jsonify(panel.cancel(aid, run_id))

    app.register_blueprint(api)

    # SEE THEIR WORK (REGRADES "FOLLOW-UP", 2026-10-21): which answer a regrade conversation is about, by its Follow-up key, so
    # Follow-up can show its crop and rubric lines in place. Reads the catalog only.
    work_api = Blueprint("frq_regrade_work", __name__, url_prefix="/api/grading/regrade-work")

    @work_api.get("")
    def work():
        with connect(panel.path) as db:
            return jsonify(placed(db, request.args.get("key") or ""))

    app.register_blueprint(work_api)
