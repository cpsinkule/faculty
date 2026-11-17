"""`/api/course-notes` (AGENTS.md "Course notes"): a course's lecture notes, uploaded once and split into their sections. Writes pass
the app's same-origin checks; nothing here is reachable with Inbox keys or codes, and nothing is sent anywhere. The PDFs open in
the browser (inline) and are never cached: they are teaching material, not public assets."""
import io
from functools import partial

from flask import Blueprint, jsonify, request, send_file

from request_actor import current as actor
from request_limits import json_object

from .service import MAX_PDF


def register(app, service):
    api = Blueprint("course_notes", __name__, url_prefix="/api/course-notes")

    def pdf(content, digest, name):
        download = request.args.get("download") == "1"
        response = send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=download, download_name=name, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.set_etag(digest)
        return response

    body = partial(json_object, silent=True)

    @api.get("/definitions/<definition_id>")
    def notes(definition_id):
        return jsonify(service.view(definition_id, summary=request.args.get("summary") == "1"))

    @api.post("/definitions/<definition_id>")
    def upload(definition_id):
        file = request.files.get("file")
        if file is None:
            raise ValueError("Choose the notes PDF.")
        content = file.read(MAX_PDF + 1)
        return jsonify(service.upload(definition_id, content, file.filename or "", request.form.get("request_id", ""), actor=actor()))

    # The last part of each address is the file's name, so a browser tab or a saved file is named after the section.
    @api.get("/<notes_id>/original/<name>.pdf")
    def original(notes_id, name):
        return pdf(*service.read(notes_id))

    @api.get("/<notes_id>/parts/<int:position>/<name>.pdf")
    def part(notes_id, position, name):
        return pdf(*service.read(notes_id, position))

    @api.post("/<notes_id>/remove")
    def remove(notes_id):
        return jsonify(service.remove(notes_id, body(), actor=actor()))

    @api.post("/<notes_id>/restore")
    def restore(notes_id):
        return jsonify(service.restore(notes_id, body(), actor=actor()))

    app.register_blueprint(api)
