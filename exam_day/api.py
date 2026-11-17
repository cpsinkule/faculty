"""`/api/exam-day`: an assessment's exam-day plan in one term and one room's packet (AGENTS.md "Exam-day packet"). Writes pass the
app's same-origin checks; nothing here prints, stores a file or sends email."""
from functools import partial

from flask import Blueprint, jsonify

from request_actor import current as actor
from request_limits import json_object


def register(app, service):
    api = Blueprint("exam_day", __name__, url_prefix="/api/exam-day")

    body = partial(json_object, silent=True)

    @api.get("/<assignment_id>/<term_id>")
    def plan(assignment_id, term_id):
        return jsonify(service.plan(assignment_id, term_id))

    @api.post("/<assignment_id>/<term_id>")
    def save(assignment_id, term_id):
        return jsonify(service.save(assignment_id, term_id, body(), actor=actor()))

    @api.get("/charts/<chart_id>/packet")
    def packet(chart_id):
        return jsonify(service.packet(chart_id))

    app.register_blueprint(api)
