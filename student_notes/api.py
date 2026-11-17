"""`/api/student-notes` (AGENTS.md "Student notes"): the instructor's own notes about a student in a term. Writes pass the app's
same-origin checks and name who made them (`request_actor.current`); nothing here is reachable with Inbox keys or codes, and nothing
is sent anywhere."""
from functools import partial

from flask import Blueprint, jsonify

from request_actor import current as actor
from request_limits import json_object


def register(app, service):
    api = Blueprint("student_notes", __name__, url_prefix="/api/student-notes")

    body = partial(json_object, silent=True)

    @api.get("/students")
    def students():
        return jsonify(service.students())

    @api.post("/students/search")
    def search():
        return jsonify(service.search(body()))

    @api.get("/students/<enrollment_id>")
    def student(enrollment_id):
        return jsonify(service.student(enrollment_id))

    @api.post("")
    def create():
        return jsonify(service.create(body(), actor())), 201

    @api.post("/<note_id>")
    def revise(note_id):
        return jsonify(service.revise(note_id, body(), actor()))

    @api.post("/<note_id>/<any(remove, restore):action>")
    def change(note_id, action):
        write = service.remove if action == "remove" else service.restore
        return jsonify(write(note_id, body(), actor()))

    app.register_blueprint(api)
