"""Private instructor routes of Certification and Export, the sixth grading stage (grading/certification.py; GRADING.md
"Certification and Export"). Reads are GETs; the student search, the certify preview and the certify itself are POSTs, so UIN digits
travel only in a request body and every write passes the app's same-origin checks. A batch adjustment of one question's final
grades (grading/batch_adjustments.py) is a preview POST that writes nothing and an apply POST that names its fingerprint. Nothing
here is reachable with Inbox keys or codes. The preview and the certify read every final grade and can take a while; the page waits
for them."""
from functools import partial

from flask import Blueprint, jsonify

from request_limits import json_object

from .certification import Refused


def register(app, service):
    api = Blueprint("frq_certification", __name__, url_prefix="/api/grading/assignments")

    body = partial(json_object, silent=True)

    @api.errorhandler(Refused)
    def refused(error):
        # The checks as they are now, so the dialog can redraw what blocks or what is left to confirm; nothing was written.
        return jsonify(error=error.description, checks=error.checks), 409

    @api.get("/<aid>/certification")
    def view(aid):
        return jsonify(service.view(aid))

    @api.get("/<aid>/certification/questions/<qid>")
    def question(aid, qid):
        return jsonify(service.question(aid, qid))

    @api.get("/<aid>/certification/students/<section_id>/<enrollment_id>")
    def student(aid, section_id, enrollment_id):
        return jsonify(service.student(aid, section_id, enrollment_id))

    @api.get("/<aid>/responses/<rid>/student")
    def response_student(aid, rid):
        # The review desk's Show student: one name and section, fetched only when the instructor clicks (the Review step stays anonymous).
        return jsonify(service.response_student(aid, rid))

    @api.post("/<aid>/certification/students/search")
    def search(aid):
        return jsonify(service.search(aid, body()))

    @api.post("/<aid>/certification/questions/<qid>/adjustments/preview")
    def adjust_preview(aid, qid):
        return jsonify(service.adjust_preview(aid, qid, body()))

    @api.post("/<aid>/certification/questions/<qid>/adjustments")
    def adjust(aid, qid):
        # A batch adjustment (grading/batch_adjustments.py): 201 when it wrote its reviews, 200 for a replay of the same request.
        result = service.adjust(aid, qid, body())
        return jsonify(result), 200 if result["replay"] else 201

    # Final Quality Audit (grading/quality_audit.py): a read of the plan's final grades by the rubric model, reporting what looks
    # inconsistent. Queuing writes only the audit's own record; it changes no grade.
    @api.get("/<aid>/certification/audit")
    def audit(aid):
        return jsonify(service.frq.quality_audit(aid))

    @api.post("/<aid>/certification/audit")
    def queue_audit(aid):
        return jsonify(service.frq.queue_quality_audit(aid, body()))

    @api.post("/<aid>/certification/audit/<audit_id>/cancel")
    def cancel_audit(aid, audit_id):
        return jsonify(service.frq.cancel_quality_audit(aid, audit_id))

    @api.post("/<aid>/certification/preview")
    def preview(aid):
        return jsonify(service.preview(aid, body()))

    @api.post("/<aid>/certification")
    def certify(aid):
        result = service.certify(aid, body())
        # 201 for a new certification, 200 for a replay of the same request (it wrote nothing). The page reads the new state with
        # its own GET: building it here took the lifecycle lock a second time, and a failure of that read would answer 500 for a
        # certification that had been recorded.
        return jsonify(result), 200 if result["replay"] else 201

    app.register_blueprint(api)
