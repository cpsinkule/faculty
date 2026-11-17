"""`/api/absences` (AGENTS.md "Excused absences"): students' absences, their documentation and the instructor's decisions. Writes pass
the app's same-origin checks; nothing here is reachable with Inbox keys or codes, and nothing is sent anywhere. Documents are
downloads (never shown as a page of this origin), checked against their checksums."""
import re
import unicodedata
from functools import partial

from flask import Blueprint, Response, jsonify, request

from request_actor import current as actor
from request_limits import json_object

from .service import MAX_DOCUMENT

FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def download(name, content, kind):
    """A kept document as a download: never rendered here, whatever it claims to be."""
    plain = unicodedata.normalize("NFKD", str(name or "document")).encode("ascii", "ignore").decode()
    safe = FILENAME.sub("_", plain).strip(" .")[:150] or "document"
    response = Response(content, mimetype=kind if re.fullmatch(r"[\w.+-]+/[\w.+-]+", kind or "") else "application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox; frame-ancestors 'none'"
    return response


def register(app, service):
    api = Blueprint("absences", __name__, url_prefix="/api/absences")

    body = partial(json_object, silent=True)

    @api.get("")
    def overview():
        return jsonify(service.overview(request.args.get("scope", "current")))

    @api.post("")
    def create():
        return jsonify(service.create(body(), actor=actor())), 201

    @api.post("/drafts/mail")
    def draft_mail():
        from .drafting import start
        return jsonify(start(app, body())), 202

    @api.post("/<absence_id>/documents/mail")
    def attach_mail(absence_id):
        from .drafting import attach
        return jsonify(attach(app, absence_id, body()))

    @api.get("/makeups")
    def makeups():
        return jsonify(service.makeups(request.args.get("scope", "current")))

    # MAKEUPS "MISSED": "Missed makeup, record 0" (and its Undo): the makeup is owed no more and the empty cell counts as 0.
    @api.post("/makeups/missed")
    def makeup_missed():
        return jsonify(service.makeup_missed(body(), actor=actor()))

    # MAKEUP BOOKING (makeup_booking.py): a term's switch, the dry run of the cards it would send, and "Send a new card".
    def booking():
        if service.booking is None:
            raise ValueError("Makeup booking is not available here.")
        return service.booking

    @api.put("/makeups/terms/<term_id>")
    def makeup_setting(term_id):
        return jsonify(booking().set_cards(term_id, body(), actor=actor()))

    @api.get("/makeups/plan")
    def makeup_plan():
        held = []
        return jsonify(cards=booking().plan(held=held), held=held)

    @api.post("/makeups/cards")
    def makeup_card():
        return jsonify(booking().new_card(body(), actor=actor())), 201

    # MAKEUP HOLDS: an absence's quiz makeups wait for the instructor (no card, no offered time) until released.
    @api.post("/<absence_id>/makeup-hold")
    def makeup_hold(absence_id):
        return jsonify(booking().hold(absence_id, body(), actor=actor()))

    # DEPARTMENT MAKEUP EXAMS (department_makeups.py): the Math department's makeup exam request form, filled out by Faculty for a
    # student whose excused absence covers an exam's day, only on the instructor's approval (confirmation "submit").
    def department():
        if getattr(service, "department", None) is None:
            raise ValueError("Department makeup requests are not available here.")
        return service.department

    @api.get("/department-makeups")
    def department_makeups():
        return jsonify(department().overview())

    @api.put("/department-makeups/terms/<term_id>/form")
    def department_form(term_id):
        return jsonify(department().set_form(term_id, body(), actor=actor()))

    @api.post("/<absence_id>/department-makeup/preview")
    def department_makeup_preview(absence_id):
        return jsonify(department().preview(absence_id, body()))

    @api.post("/<absence_id>/department-makeup")
    def department_makeup(absence_id):
        result = department().submit(absence_id, body(), actor=actor())
        # Google's answer is the status: refused (502, nothing recorded there) or not known (504, never sent again by Faculty).
        return jsonify(result), {"accepted": 200, "failed": 502}.get(result["outcome"], 504)

    @api.get("/totals")
    def totals():
        return jsonify(service.totals(request.args.get("scope", "current")))

    @api.get("/students")
    def students():
        return jsonify(service.students())

    @api.post("/students/search")
    def search():
        return jsonify(service.search(body()))

    @api.get("/students/<section_id>/<enrollment_id>")
    def student(section_id, enrollment_id):
        return jsonify(service.student(section_id, enrollment_id))

    @api.get("/<absence_id>")
    def absence(absence_id):
        return jsonify(service.get(absence_id))

    @api.post("/<absence_id>")
    def revise(absence_id):
        return jsonify(service.revise(absence_id, body(), actor=actor()))

    @api.post("/<absence_id>/documents")
    def upload(absence_id):
        file = request.files.get("file")
        if file is None:
            raise ValueError("Choose the documentation file.")
        return jsonify(service.upload(absence_id, file.read(MAX_DOCUMENT + 1), file.filename or "", request.form.get("request_id", "")))

    @api.post("/<absence_id>/documents/message")
    def attach_message(absence_id):
        return jsonify(service.attach_message(absence_id, body()))

    @api.post("/<absence_id>/documents/<document_id>/<any(remove, restore):action>")
    def document_event(absence_id, document_id, action):
        return jsonify(service.document_event(absence_id, document_id, "removed" if action == "remove" else "restored", body()))

    @api.get("/<absence_id>/documents/<document_id>")
    def document(absence_id, document_id):
        name, kind, content = service.read_document(absence_id, document_id)
        return download(name, content, kind)

    app.register_blueprint(api)
