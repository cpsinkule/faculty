"""`/api/teaching-assistants`: each section's TA contacts (AGENTS.md "TA contacts"). Writes pass the app's same-origin checks;
nothing here is reachable with Inbox keys or codes, and nothing sends email."""
from functools import partial

from flask import Blueprint, jsonify

from request_actor import current as actor
from request_limits import json_object


def register(app, service):
    api = Blueprint("teaching_assistants", __name__, url_prefix="/api/teaching-assistants")

    body = partial(json_object, silent=True)

    @api.get("/sections/<section_id>")
    def section(section_id):
        return jsonify(service.section(section_id))

    @api.post("/sections/<section_id>")
    def save(section_id):
        return jsonify(service.save(section_id, body(), actor=actor()))

    app.register_blueprint(api)
