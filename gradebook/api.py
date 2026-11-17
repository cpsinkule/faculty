"""Routes of the term gradebook (`/api/gradebook`; README.md's API table). Writes need the workspace's same-origin headers
(`app.protect_writes`): the two import routes are multipart, every other write is JSON. Each write records who made it
(`request_actor.current`: `assistant` for the in-app assistant's own requests, else `workspace`)."""
import io

from flask import Blueprint, current_app, jsonify, request, send_file

from request_actor import current as actor
from roster_import import MAX_UPLOAD

from . import canvas_cell as cell
from . import canvas_posting as posting
from . import grading_check
from .common import encoded
from .service import StaleCells


def register(app, service):
    api = Blueprint("gradebook", __name__, url_prefix="/api/gradebook")

    def body():
        try:
            value = request.get_json()
        except RecursionError:  # Not a ValueError, so Flask's own 400 does not catch it.
            raise ValueError("The request is nested too deeply to read.") from None
        if not isinstance(value, dict):
            raise ValueError("Supply a JSON object.")
        try:
            # Every request is fingerprinted and stored as strict JSON text: NaN, Infinity or a lone surrogate cannot be.
            encoded(value).encode()
        except ValueError:
            raise ValueError("The request holds a number or text JSON cannot store (NaN, Infinity or broken Unicode).") from None
        return value

    def upload(fields):
        """Exactly one file and the named text fields, each at most once."""
        if set(request.files) != {"file"} or len(request.files.getlist("file")) != 1:
            raise ValueError("Choose one CSV or Excel (.xlsx) grade file.")
        if set(request.form) - set(fields) or any(len(request.form.getlist(name)) > 1 for name in request.form):
            raise ValueError("The upload has an unexpected field.")
        file = request.files["file"]
        content = file.read(MAX_UPLOAD + 1)
        if len(content) > MAX_UPLOAD:
            raise ValueError("Choose a grade file of 10 MB or less.")
        return file.filename or "", content, {name: request.form.get(name) for name in fields}

    @api.errorhandler(StaleCells)
    def stale_cells(error):
        # The browser marks exactly these cells; nothing of the batch was saved.
        return jsonify(error=error.description, stale=error.stale), 409

    @api.get("")
    def catalog():
        return jsonify(service.catalog())

    @api.get("/terms/<term_id>")
    def view(term_id):
        # `cells=section` (opt-in, with `section`): only that section's cells, plus each column's term-wide `cell_counts`.
        cells = request.args.get("cells") or ""
        if cells not in ("", "section"):
            raise ValueError("cells is section or left out.")
        return jsonify(service.view(term_id, request.args.get("section") or None, section_cells=cells == "section"))

    @api.post("/terms/<term_id>/setup")
    def save_setup(term_id):
        return jsonify(service.save_setup(term_id, body(), actor=actor()))

    @api.post("/terms/<term_id>/scores")
    def write_scores(term_id):
        return jsonify(service.write_scores(term_id, body(), actor=actor()))

    @api.get("/terms/<term_id>/history")
    def history(term_id):
        return jsonify(service.history(term_id, request.args.get("item"), request.args.get("row")))

    @api.get("/terms/<term_id>/changes")
    def changes(term_id):
        return jsonify(service.changes(term_id, request.args.get("section") or None, request.args.get("before") or None))

    @api.get("/terms/<term_id>/changes/<request_ref>")
    def change_cells(term_id, request_ref):
        return jsonify(service.change_cells(term_id, request_ref, request.args.get("section") or None))

    @api.get("/terms/<term_id>/grading-check")
    def check_grading(term_id):
        # DIFFERS FROM GRADING (GRADEBOOK, 2026-10-07): the cells of linked columns that differ from grading's final grades now.
        return jsonify(grading_check.read(service, term_id, request.args.get("section") or None))

    @api.get('/terms/<term_id>/reconciliation')
    def reconciliation(term_id):
        from .reconciliation import read
        return jsonify(read(service, term_id))

    @api.post("/terms/<term_id>/items/<item_id>/pull/preview")
    def pull_preview(term_id, item_id):
        return jsonify(service.pull_preview(term_id, item_id, body()))

    @api.post("/terms/<term_id>/items/<item_id>/pull")
    def pull(term_id, item_id):
        return jsonify(service.pull(term_id, item_id, body(), actor=actor()))

    @api.post("/terms/<term_id>/items/link")
    def link_assignment(term_id):
        return jsonify(service.link_assignment(term_id, body(), actor=actor()))

    @api.post("/terms/<term_id>/imports/preview")
    def import_preview(term_id):
        filename, content, fields = upload(("options",))
        return jsonify(service.import_preview(term_id, content, filename, fields["options"]))

    @api.post("/terms/<term_id>/imports")
    def import_apply(term_id):
        filename, content, fields = upload(("options", "fingerprint", "request_id"))
        return jsonify(service.import_apply(term_id, content, filename, fields["options"], fields["fingerprint"], fields["request_id"],
                                            actor=actor()))

    # JEV CANVAS-MATCH: Jev's picks only for the instructor's own requests; the assistant's preview (a read) never spends a request.
    def jev():
        return current_app.extensions.get("jev") if actor() == "workspace" else None

    # SYNC WITH CANVAS (docs/decisions/canvas.md; README.md "Syncing with Canvas"): the summary, then the instructor's Sync.
    @api.post("/terms/<term_id>/canvas-sync/preview")
    def canvas_sync_preview(term_id):
        return jsonify(service.canvas_sync_preview(term_id, body(), jev=jev()))

    @api.post("/terms/<term_id>/canvas-sync")
    def canvas_sync(term_id):
        return jsonify(service.canvas_sync(term_id, body(), actor=actor(), jev=jev()))

    # A LOST ANSWER: what became of a Sync whose answer the page could not read (a read; Canvas is not asked).
    @api.get("/terms/<term_id>/canvas-sync/requests/<request_id>")
    def canvas_sync_lookup(term_id, request_id):
        return jsonify(service.canvas_sync_lookup(term_id, request_id, request.args.get("section_id") or None, request.args.get("lost") or None))

    # Canvas assignments left out of every sync from now on (the nightly WebAssign import's "Leave out"); no Canvas read.
    @api.post("/terms/<term_id>/canvas/skip")
    def canvas_skip(term_id):
        return jsonify(service.canvas_skip(term_id, body(), actor=actor()))

    # Scores of each linked section's iClicker course (README.md "Importing from iClicker").
    @api.post("/terms/<term_id>/iclicker/preview")
    def iclicker_preview(term_id):
        return jsonify(service.iclicker_preview(term_id, body()))

    @api.post("/terms/<term_id>/iclicker")
    def iclicker_apply(term_id):
        return jsonify(service.iclicker_apply(term_id, body(), actor=actor()))

    # ICLICKER CREDIT BEFORE THE IMPORT (docs/decisions/iclicker.md): one student's full credit for a class day's polls, given now
    # or once they are imported; and taking it back.
    @api.post("/terms/<term_id>/iclicker/credits")
    def iclicker_credit(term_id):
        return jsonify(service.iclicker_credit(term_id, body(), actor=actor()))

    @api.post("/terms/<term_id>/iclicker/credits/<credit_id>/withdraw")
    def iclicker_credit_withdraw(term_id, credit_id):
        return jsonify(service.iclicker_credit_withdraw(term_id, credit_id, body(), actor=actor()))

    # A sync's send to Canvas: its progress, and stopping it before its next assignment.
    @api.get("/terms/<term_id>/canvas-send/<send_id>")
    def canvas_send_status(term_id, send_id):
        return jsonify(service.canvas_send_status(term_id, send_id))

    @api.post("/terms/<term_id>/canvas-send/<send_id>/cancel")
    def canvas_send_cancel(term_id, send_id):
        body()
        return jsonify(service.canvas_send_cancel(term_id, send_id))

    # POSTING GRADES (docs/decisions/canvas.md): a column's grades posted (shown to students) or hidden in every Canvas course it
    # goes to, by every Sync with Canvas from now on, or back to its category's rule. Nothing is asked of Canvas here; the
    # assistant's choice is an Approve card (its `confirmation`).
    @api.post("/terms/<term_id>/canvas-posting/choice")
    def canvas_posting_choice(term_id):
        return jsonify(posting.choose(service, term_id, body(), actor=actor()))

    @api.get("/terms/<term_id>/canvas-posting/<post_id>")
    def canvas_posting_status(term_id, post_id):
        return jsonify(posting.status(service, term_id, post_id))

    # ONE CELL (docs/decisions/canvas.md; a regrade request's Apply, REGRADES): one student's cell of one column sent to Canvas, beside
    # Sync with Canvas. The preview reads that student's Canvas course and writes nothing.
    @api.post("/terms/<term_id>/canvas-cell/preview")
    def canvas_cell_preview(term_id):
        return jsonify(cell.preview(service, term_id, body()))

    @api.post("/terms/<term_id>/canvas-cell")
    def canvas_cell_send(term_id):
        return jsonify(cell.send(service, term_id, body(), actor=actor()))

    @api.get("/terms/<term_id>/canvas-cell/<send_id>")
    def canvas_cell_status(term_id, send_id):
        return jsonify(cell.status(service, term_id, send_id))

    # A CANVAS QUIZ'S GRADE (docs/decisions/canvas.md ONE CELL): one student's grade on a quiz Canvas grades, set in Canvas itself. The
    # send answers with its receipt; `canvas-cell/<send>` reads it again.
    @api.post("/terms/<term_id>/canvas-quiz-grade/preview")
    def canvas_quiz_grade_preview(term_id):
        return jsonify(cell.quiz_preview(service, term_id, body()))

    @api.post("/terms/<term_id>/canvas-quiz-grade")
    def canvas_quiz_grade(term_id):
        return jsonify(cell.quiz_send(service, term_id, body(), actor=actor()))

    @api.get("/terms/<term_id>/export.csv")
    def export(term_id):
        content, name = service.export_csv(term_id, request.args.get("section"))
        return send_file(io.BytesIO(content), mimetype="text/csv", as_attachment=True, download_name=name)

    app.register_blueprint(api)
