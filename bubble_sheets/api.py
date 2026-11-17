"""Private instructor routes for bubble sheet readings. Same-origin write checks are applied by the app."""
import io
from functools import partial

from flask import Blueprint, jsonify, send_file

from request_limits import json_object


def register(app, service):
    api = Blueprint("bubble_sheets", __name__, url_prefix="/api/bubble-sheets")

    body = partial(json_object, "The request must be a JSON object.")

    @api.get("/messages/<message_id>")
    def summary(message_id):
        return jsonify(service.summary(message_id))

    @api.post("/messages/<message_id>")
    def read(message_id):
        return jsonify(service.request(message_id, body())), 202

    @api.post("/settings")
    def settings():
        return jsonify(service.set_auto_identify(body().get("auto_identify")))

    @api.get("/sheets/<sheet_id>")
    def sheet(sheet_id):
        return jsonify(service.sheet(sheet_id))

    @api.get("/sheets/<sheet_id>/parts/<part>")
    def part(sheet_id, part):
        return send_file(io.BytesIO(service.part(sheet_id, part)), mimetype="image/png")

    @api.post("/sheets/<sheet_id>/students")
    def students(sheet_id):
        # POST keeps the typed UIN digits out of URLs and any request log.
        return jsonify(service.search_students(sheet_id, body()))

    @api.post("/sheets/<sheet_id>/review")
    def review(sheet_id):
        return jsonify(service.review(sheet_id, body())), 201

    @api.get("/runs/<run_id>/export.csv")
    def export(run_id):
        content, name = service.export_csv(run_id)
        return send_file(io.BytesIO(content), mimetype="text/csv", as_attachment=True, download_name=name)

    app.register_blueprint(api)
