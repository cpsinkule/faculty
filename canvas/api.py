"""`/api/canvas`: the kept session, each section's Canvas course and its roster comparison, the term's Canvas content and messages
to chosen sections (AGENTS.md "Canvas"). Writes pass the
app's same-origin checks; nothing here is reachable with Inbox keys or codes. The sign-in window's routes are `canvas/signin.py`'s."""
import io
from functools import partial

from flask import Blueprint, current_app, jsonify, request, send_file

from request_actor import current as actor
from request_limits import json_object

from . import signin


def register(app, service, content, messages, announcements):
    api = Blueprint("canvas", __name__, url_prefix="/api/canvas")

    body = partial(json_object, silent=True)

    @api.get("/status")
    def status():
        return jsonify(service.status())

    @api.post("/check")
    def check():
        body()
        return jsonify(service.check())

    @api.post("/forget")
    def forget():
        body()
        return jsonify(service.forget())

    @api.get("/sections/<section_id>")
    def section_link(section_id):
        return jsonify(service.section_link(section_id))

    @api.get("/sections/<section_id>/courses")
    def section_courses(section_id):
        return jsonify(service.section_courses(section_id))

    @api.post("/sections/<section_id>")
    def link_section(section_id):
        return jsonify(service.link_section(section_id, body()))

    @api.get("/sections/<section_id>/roster")
    def roster(section_id):
        return jsonify(service.roster(section_id))

    # Modules and pages kept in step across a term's courses (canvas/content_service.py).
    @api.get("/overview")
    def overview():
        return jsonify(content.overview())

    @api.get("/content/group/<group_id>")
    def content_group(group_id):
        return jsonify(content.group_place(group_id, request.args.get("section")))

    @api.get("/content/<term_id>")
    def content_view(term_id):
        return jsonify(content.view(term_id))

    @api.post("/content/<term_id>")
    def content_save(term_id):
        return jsonify(content.save(term_id, body()))

    # SCHEDULING "CANVAS OFFICE HOURS": the copy's Office Hours page beside its draft from the Term schedule, and putting it in.
    @api.get("/content/<term_id>/office-hours")
    def content_office_hours(term_id):
        return jsonify(content.office_hours(term_id))

    @api.post("/content/<term_id>/office-hours")
    def content_office_hours_put(term_id):
        return jsonify(content.put_office_hours(term_id, body()))

    # "Put on Canvas" (canvas/placement.py): one Faculty assignment or gradebook column into the copy, and where it stands.
    @api.get("/content/<term_id>/placement")
    def content_placement_proposal(term_id):
        return jsonify(content.placement.proposal(term_id, request.args.get("assignment_id") or None, request.args.get("item_id") or None))

    @api.post("/content/<term_id>/placement")
    def content_placement(term_id):
        return jsonify(content.placement.place(term_id, body()))

    @api.get("/content/<term_id>/assignments/status")
    def content_assignment_status(term_id):
        return jsonify(content.placement.status(term_id))

    # FILES (canvas/files.py): a file kept for a module item of the term's copy or of a section's direct edit. Saved at once;
    # Canvas receives it only with a push. The kept file comes back as a download, never shown inline or cached.
    @api.post("/content/<term_id>/files")
    def content_file_add(term_id):
        from .files import MAX_FILE
        part = request.files.get("file")
        if part is None:
            raise ValueError("Choose a file.")
        return jsonify(content.files.add(term_id, part.read(MAX_FILE + 1), part.filename or "", part.mimetype or "",
                                         request.form.get("name") or None))

    # FROM THE ASSISTANT: the copy in outline, and one file (attached in the chat, or already kept) placed in one module of the
    # copy as a new revision. A draft: Canvas changes only with the push.
    from app_files import AppFiles
    placed = AppFiles(app, use="placed in a module")

    @api.get("/content/<term_id>/outline")
    def content_outline(term_id):
        return jsonify(content.outline(term_id))

    @api.post("/content/<term_id>/files/place")
    def content_file_place(term_id):
        return jsonify(content.files.place(term_id, body(), placed, actor=actor()))

    @api.get("/content/<term_id>/files/<file_id>")
    def content_file(term_id, file_id):
        row, data = content.files.read(term_id, file_id)
        response = send_file(io.BytesIO(data), mimetype=row["media_type"], as_attachment=True, download_name=row["name"], max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.set_etag(row["sha256"])
        return response

    # One section's Canvas course edited directly (canvas/section_content.py): read, preview, push.
    @api.get("/sections/<section_id>/content")
    def section_content_view(section_id):
        return jsonify(content.section.load(section_id))

    @api.post("/sections/<section_id>/content/preview")
    def section_content_preview(section_id):
        return jsonify(content.section.preview(section_id, body()))

    @api.post("/sections/<section_id>/content/push")
    def section_content_push(section_id):
        return jsonify(content.section.push(section_id, body()))

    @api.get("/sections/<section_id>/content/pushes/<push_id>")
    def section_content_push_status(section_id, push_id):
        return jsonify(content.section.status(section_id, push_id))

    @api.post("/sections/<section_id>/content/pushes/<push_id>/cancel")
    def section_content_push_cancel(section_id, push_id):
        body()
        return jsonify(content.section.cancel(section_id, push_id))

    @api.post("/content/<term_id>/start")
    def content_start(term_id):
        return jsonify(content.start(term_id, body()))

    @api.post("/content/<term_id>/pair")
    def content_pair(term_id):
        return jsonify(content.pair(term_id, body()))

    @api.post("/content/<term_id>/preview")
    def content_preview(term_id):
        return jsonify(content.preview(term_id, body()))

    @api.post("/content/<term_id>/push")
    def content_push(term_id):
        return jsonify(content.push(term_id, body()))

    @api.get("/content/<term_id>/pushes/<push_id>")
    def content_push_status(term_id, push_id):
        return jsonify(content.status(term_id, push_id))

    @api.post("/content/<term_id>/pushes/<push_id>/cancel")
    def content_push_cancel(term_id, push_id):
        body()
        return jsonify(content.cancel(term_id, push_id))

    # Messages to the students of chosen sections through Canvas's inbox (canvas/messages.py).
    from app_files import AppFiles
    messages.files = AppFiles(app)  # ATTACHMENTS: Faculty files are read through their own routes, as Mail's are

    def message():
        """(fields, files from the instructor's computer): JSON, or the window's multipart form (the fields as JSON in `message`,
        each file a `file` part, in the order the fields' attachments name them as {upload: n})."""
        if request.mimetype != "multipart/form-data":
            return body(), []
        import json
        from .messages import MAX_TOTAL
        try:
            value = json.loads(request.form.get("message") or "")
        except ValueError:
            raise ValueError("Send the message's fields as JSON in `message`.") from None
        if not isinstance(value, dict) or set(request.files) - {"file"}:
            raise ValueError("Send the message's fields as JSON in `message` and each file as `file`.")
        return value, [(part.filename or "", part.mimetype or "", part.read(MAX_TOTAL + 1)) for part in request.files.getlist("file")]
    @api.get("/messages")
    def messages_view():
        return jsonify(messages.view())

    @api.post("/messages/preview")
    def message_preview():
        fields, uploads = message()
        return jsonify(messages.preview(fields, uploads))

    @api.post("/messages/check")
    def message_check():
        # JEV SEND-CHECKS: advice beside the review; the instructor's own control (assistant/policy.py NEVER).
        mail = current_app.extensions.get("gmail_mail")
        return jsonify(messages.check(body(), current_app.extensions.get("jev"), mail.roster_people() if mail else ()))

    @api.post("/messages/send")
    def message_send():
        fields, uploads = message()
        return jsonify(messages.send(fields, uploads=uploads))

    @api.post("/messages/schedule")
    def message_schedule():
        # MAIL-SEND-LATER (docs/decisions/mail.md): the reviewed message kept with its recipients, sent at its time. JSON only
        # (request_limits.py): a file from the computer is never kept until then.
        return jsonify(current_app.extensions["scheduled_sends"].schedule_canvas(body(), actor()))

    @api.get("/messages/<message_id>")
    def message_record(message_id):
        return jsonify(messages.record(message_id))

    @api.get("/messages/<message_id>/delivery")
    def message_delivery(message_id):
        return jsonify(messages.delivery(message_id))

    # Announcements in the Canvas courses of chosen sections (canvas/announcements.py).
    @api.get("/announcements")
    def announcements_view():
        return jsonify(announcements.view())

    @api.post("/announcements/preview")
    def announcement_preview():
        return jsonify(announcements.preview(body()))

    @api.post("/announcements/post")
    def announcement_post():
        return jsonify(announcements.post(body(), actor=actor()))

    @api.get("/announcements/<announcement_id>")
    def announcement_record(announcement_id):
        return jsonify(announcements.record(announcement_id))

    app.register_blueprint(api)
    signin.register(app, service)
