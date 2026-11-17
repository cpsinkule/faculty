"""`/api/communications`: a student's communications record (AGENTS.md "Communications record"). Reads, and Capture now; writes pass
the app's same-origin checks. Kept files are downloads (never shown as a page of this origin), checked against their checksums."""
import re
import unicodedata

from flask import Blueprint, Response, jsonify, request

FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def download(name, content, kind):
    """A kept file as a download: never rendered here, whatever it claims to be."""
    plain = unicodedata.normalize("NFKD", str(name or "attachment")).encode("ascii", "ignore").decode()
    safe = FILENAME.sub("_", plain).strip(" .")[:150] or "attachment"
    response = Response(content, mimetype=kind if re.fullmatch(r"[\w.+-]+/[\w.+-]+", kind or "") else "application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox; frame-ancestors 'none'"
    return response


def register(app, service):
    api = Blueprint("communications", __name__, url_prefix="/api/communications")

    @api.get("/status")
    def status():
        return jsonify(service.status())

    @api.post("/capture")
    def capture():
        return jsonify(service.request(request.get_json(silent=True) or {}))

    @api.post("/drive/connect")
    def drive_connect():
        return jsonify(service.drive_connect())

    @api.get("/students/<enrollment_id>")
    def timeline(enrollment_id):
        return jsonify(service.timeline(enrollment_id))

    @api.get("/coordinators/<term_id>")
    def coordinator_timeline(term_id):
        # THE COORDINATOR'S MAIL: the kept messages with a coordinated term's coordinator, as a student's timeline reads.
        return jsonify(service.coordinator_timeline(term_id))

    @api.get("/messages/<message_id>/revisions")
    def revisions(message_id):
        return jsonify(service.revisions(message_id))

    @api.get("/messages/<message_id>/original")
    def original(message_id):
        name, content = service.original(message_id)
        return download(name, content, "message/rfc822")

    @api.get("/messages/<message_id>/attachments/<int:index>")
    def attachment(message_id, index):
        name, kind, content = service.attachment(message_id, index)
        return download(name, content, kind)

    @api.get("/seat-mail/<mail_id>/chart.pdf")
    def seat_chart(mail_id):
        return download("seating-chart.pdf", service.seat_chart(mail_id), "application/pdf")

    app.register_blueprint(api)
