"""Private instructor routes for quizzes drafted by a model (grading/quiz_drafts.py); browser access follows the existing network boundary."""
import io
import json
import re

from flask import Blueprint, jsonify, request, send_file

from request_actor import current as actor
from request_limits import json_object


def register(app, service):
    api = Blueprint("quiz_drafts", __name__, url_prefix="/api/quiz-drafts")

    body = json_object

    @api.get("")
    def listing():
        return jsonify(drafts=service.quiz_drafts(request.args.get("definition_id") or None))

    @api.get("/options")
    def options():
        return jsonify(service.quiz_draft_options(request.args.get("definition_id") or None))

    @api.post("")
    def create():
        form = request.form
        files = [(f.filename, f.read(25 * 1024 * 1024 + 1)) for f in request.files.getlist("files") if f.filename]
        data = {key: form.get(key) for key in ("request_id", "term_id", "title", "mode", "format", "instructions", "examples", "rubric_choices", "notes_sections", "participation")}
        if form.get("blueprint"):
            try:
                data["blueprint"] = json.loads(form["blueprint"])
            except (ValueError, TypeError):
                raise ValueError("Send the blueprint as a JSON list of question slots.") from None
        for key, cast in (("total", float), ("versions", int)):
            try:
                data[key] = cast(form.get(key, ""))
            except ValueError:
                data[key] = None
        return jsonify(service.create_quiz_draft(data, files)), 201

    @api.get("/<draft_id>")
    def draft(draft_id):
        return jsonify(service.quiz_draft(draft_id))

    @api.post("/<draft_id>/revisions")
    def revise(draft_id):
        return jsonify(service.revise_quiz_draft(draft_id, body())), 202

    @api.post("/<draft_id>/rubric-answers")
    def rubric_answers(draft_id):
        # The instructor's answers to the decisions of the rubric check the draft shows (QUIZZES "A REVISION", 2026-10-11): a new
        # revision that runs only the check's Resolve step; the quiz, its rubrics, keys and files are referenced unchanged.
        return jsonify(service.answer_quiz_rubric_check(draft_id, body())), 202

    @api.get("/<draft_id>/revisions/<revision_id>")
    def revision(draft_id, revision_id):
        return jsonify(service.quiz_revision(draft_id, revision_id))

    @api.post("/<draft_id>/revisions/<revision_id>/cancel")
    def cancel(draft_id, revision_id):
        body()
        return jsonify(service.cancel_quiz_revision(draft_id, revision_id))

    @api.post("/<draft_id>/scrap")
    def scrap(draft_id):
        return jsonify(service.scrap_quiz_draft(draft_id, body(), actor()))

    @api.get("/<draft_id>/revisions/<revision_id>/versions/<letter>/pages/<int:page>.png")
    def page(draft_id, revision_id, letter, page):
        content, media = service.quiz_file(draft_id, revision_id, letter, "page", page)
        return send_file(io.BytesIO(content), mimetype=media, max_age=0)

    @api.get("/<draft_id>/revisions/<revision_id>/versions/<letter>/<kind>")
    def file(draft_id, revision_id, letter, kind):
        content, media = service.quiz_file(draft_id, revision_id, letter, kind)
        names = {"pdf": f"quiz-version-{letter}.pdf", "tex": f"quiz-version-{letter}.tex", "log": f"quiz-version-{letter}-build.log", "sty": "facultyquiz.sty"}
        return send_file(io.BytesIO(content), mimetype=media, as_attachment=request.args.get("download") == "1",
                         download_name=re.sub(r"[^A-Za-z0-9._-]", "_", names[kind]), max_age=0)

    @api.get("/<draft_id>/approval")
    def approval_options(draft_id):
        return jsonify(service.quiz_approval_options(draft_id))

    @api.post("/<draft_id>/approve")
    def approve(draft_id):
        return jsonify(service.approve_quiz_draft(draft_id, body())), 202

    @api.post("/<draft_id>/approvals/<approval_id>/rebuild")
    def rebuild(draft_id, approval_id):
        # Print an approved quiz again in Faculty's current page layout (QUIZZES "REBUILDING AN APPROVED QUIZ").
        return jsonify(service.rebuild_quiz_approval(draft_id, approval_id, body(), actor())), 202

    @api.get("/<draft_id>/approvals/<approval_id>/stack")
    @api.get("/<draft_id>/approvals/<approval_id>/stack/<section_id>")
    def stack(draft_id, approval_id, section_id=None):
        # Without a section: the whole stack printed for the term; with one, a section's stack of an approval before 2026-10-01.
        content, name = service.quiz_section_stack(draft_id, approval_id, section_id)
        return send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=name, max_age=0)

    # QUIZZES "ACCESSIBLE COPY": a screen-readable Word copy of an approved quiz for the DR center, and which quizzes of a term have one.
    @api.get("/accessible-copies")
    def accessible_copies():
        return jsonify(assignments=service.quiz_accessible_copies(request.args.get("term_id") or ""))

    @api.get("/<draft_id>/approvals/<approval_id>/accessible/<letter>")
    def accessible_copy(draft_id, approval_id, letter):
        from document_identity.accessible_quiz import MEDIA_TYPE
        content, name = service.quiz_accessible_copy(draft_id, approval_id, letter)
        return send_file(io.BytesIO(content), mimetype=MEDIA_TYPE, as_attachment=True, download_name=name, max_age=0)

    app.register_blueprint(api)
