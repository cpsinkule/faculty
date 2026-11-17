"""`/api/iclicker`: whether iClicker is set up and each section's iClicker course (AGENTS.md "iClicker"). Writes pass the app's
same-origin checks; nothing here is reachable with Inbox keys or codes. The scores are imported through `/api/gradebook`."""
from functools import partial

from flask import Blueprint, jsonify

from request_actor import current as actor
from request_limits import json_object


def register(app, service):
    api = Blueprint("iclicker", __name__, url_prefix="/api/iclicker")

    body = partial(json_object, silent=True)

    @api.get("/status")
    def status():
        return jsonify(service.status())

    @api.get("/sections/<section_id>")
    def section_link(section_id):
        return jsonify(service.section_link(section_id))

    @api.get("/sections/<section_id>/courses")
    def section_courses(section_id):
        return jsonify(service.section_courses(section_id))

    @api.post("/sections/<section_id>")
    def link_section(section_id):
        return jsonify(service.link_section(section_id, body(), actor=actor()))

    app.register_blueprint(api)
