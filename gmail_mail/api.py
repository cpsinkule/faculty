"""`/api/mail`: the Mail page (AGENTS.md "Gmail in Faculty"). Writes pass the app's same-origin checks; nothing here is reachable
with Inbox keys or codes. Google's own failures answer 502 with the reason (never 401/404/409, which the app reserves); a message
that changed since it was reviewed, or a request ID reused for another message, is the ordinary 409."""
import json
import re
import unicodedata
from functools import partial

from flask import Blueprint, Response, current_app, jsonify, request

from app_files import AppFiles
from request_actor import current as actor
from request_limits import json_object

from .attachments import MAX_TOTAL
from .client import MailError
from .library import FacultyFiles


def register(app, service):
    api = Blueprint("gmail_mail", __name__, url_prefix="/api/mail")
    service.files = AppFiles(app)  # attachments are read through the app's own file routes (MAIL-ATTACHMENTS)
    library = FacultyFiles(app, service)

    body = partial(json_object, silent=True)

    def message():
        """(fields, files from the instructor's computer): JSON, or the compose window's multipart form (the fields as JSON in
        `message`, each file a `file` part, in the order the fields' attachments name them as {upload: n})."""
        if request.mimetype != "multipart/form-data":
            return body(), []
        try:
            value = json.loads(request.form.get("message") or "")
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise ValueError("Supply the message as JSON in the form's message field.")
        return value, [(part.filename or "", part.mimetype or "", part.read(MAX_TOTAL + 1)) for part in request.files.getlist("file")]

    @api.errorhandler(MailError)
    def google_failed(error):
        return jsonify(error=str(error)), 502

    @api.get("")
    def status():
        return jsonify(service.status())

    @api.post("/connect")
    def connect():
        return jsonify(service.connect(body()))

    @api.get("/threads")
    def threads():
        return jsonify(service.threads(request.args.get("tab", "students"), request.args.get("q", ""), request.args.get("page_token", ""),
                                       step=request.args.get("step") == "1"))

    @api.get("/threads/<thread_id>")
    def thread(thread_id):
        return jsonify(service.thread(thread_id))

    @api.get("/messages/<message_id>/parts/<part_id>")
    def attachment(message_id, part_id):
        # A file a sender attached, fetched from Gmail when opened: a download, never rendered as a page here (the app's file
        # viewer shows pictures of it), and never kept.
        name, kind, content = service.attachment(message_id, part_id)
        plain = unicodedata.normalize("NFKD", name or "attachment").encode("ascii", "ignore").decode()
        safe = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|;]', "_", plain).strip(" .")[:150] or "attachment"
        response = Response(content, mimetype=kind if re.fullmatch(r"[\w.+-]+/[\w.+-]+", kind) else "application/octet-stream")
        response.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @api.post("/threads/<thread_id>/trash")
    def trash(thread_id):
        return jsonify(service.trash(thread_id, body()))

    @api.post("/threads/<thread_id>/untrash")
    def untrash(thread_id):
        return jsonify(service.trash(thread_id, body(), undo=True))

    @api.post("/threads/<thread_id>/archive")
    def archive(thread_id):
        return jsonify(service.archive(thread_id, body()))

    @api.post("/threads/<thread_id>/unarchive")
    def unarchive(thread_id):
        return jsonify(service.archive(thread_id, body(), undo=True))

    @api.get("/sections")
    def sections():
        return jsonify(sections=service.sections())

    @api.get("/audience")
    def audience():
        return jsonify(service.audience(request.args.get("group", ""), request.args.get("section", "")))

    @api.get("/student")
    def student():
        return jsonify(service.student(request.args.get("group", ""), request.args.get("section", ""), request.args.get("student", "")))

    @api.post("/preview")
    def preview():
        fields, uploads = message()
        return jsonify(service.preview(fields, uploads))

    @api.post("/check")
    def check():
        # JEV SEND-CHECKS: advice beside the review; the instructor's own control (assistant/policy.py NEVER).
        fields, uploads = message()
        return jsonify(service.check(fields, uploads, current_app.extensions.get("jev")))

    @api.post("/send")
    def send():
        fields, uploads = message()
        return jsonify(service.send(fields, uploads=uploads))

    @api.post("/schedule")
    def schedule():
        # MAIL-SEND-LATER: the reviewed message kept as approved and sent at its time (scheduled_sends/service.py). JSON only
        # (request_limits.py): a file from the computer is never kept until then.
        return jsonify(current_app.extensions["scheduled_sends"].schedule_mail(body(), actor()))

    @api.get("/files")
    def files():
        # The Faculty files the compose window can attach: a student's reviewed papers, lecture notes, the assistant's saved files.
        return jsonify(library.listing(request.args.get("kind", ""), request.args.get("q", "")))

    @api.get("/sent")
    def sent():
        return jsonify(service.sent())

    app.register_blueprint(api)
