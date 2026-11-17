"""Single-user document APIs. The public email gateway exposes none of these."""
import io
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from werkzeug.exceptions import NotFound

from request_actor import current as actor
from request_limits import json_object
from .compiler import compile_sources
from .media import DEFAULT_MEDIA


def register(app, service, scans, email_exports, printer_sends=None):
    api = Blueprint("documents", __name__, url_prefix="/api/documents")

    body = json_object

    @api.get("/catalog")
    def catalog():
        return jsonify(service.catalog())

    @api.get("/versions/<version_id>/preview")
    def version_preview(version_id):
        return send_file(io.BytesIO(service.version_preview(version_id)), mimetype="application/pdf",
                         as_attachment=True, download_name=f"preview-{version_id}.pdf")

    # QUIZZES "A COPY BY EMAIL": one student's own stamped copy of a version, made now, whose PDF Mail can attach for them.
    @api.post("/versions/<version_id>/student-copy")
    def student_copy(version_id):
        value = body()
        return jsonify(service.student_copy(version_id, value.get("enrollment_id"), value.get("request_id"), actor=actor()))

    @api.post("/assignments")
    def assignment():
        value = body()
        return jsonify(service.create_assignment(value.get("definition_id"), value.get("key"), value.get("title"))), 201

    @api.post("/assignments/<assignment_id>/rename")
    def rename_assignment(assignment_id):
        return jsonify(service.rename_assignment(assignment_id, body()))

    # Starter files for "Faculty prints it from LaTeX": the facultyassignment class every source must use, and an example
    # main.tex to copy. Course material from this repository; read only.
    STARTERS = {"main.tex": "example.tex", "facultyassignment.cls": "facultyassignment.cls"}

    @api.get("/starter/<name>")
    def starter(name):
        if name not in STARTERS:
            raise NotFound("Starter file not found.")
        path = Path(__file__).parent / "templates" / STARTERS[name]
        return send_file(io.BytesIO(path.read_bytes()), mimetype="text/plain; charset=utf-8", as_attachment=True, download_name=name)

    @api.post("/versions/fixture")
    def fixture():
        value = body()
        paper = value.get("paper")
        if paper not in ("letter", "a4"):
            raise ValueError("Choose the Letter or A4 fixture.")
        root = Path(__file__).parent
        sources = {"main.tex": (root / "fixtures" / f"{paper}.tex").read_bytes(),
                   "build.log": (root / "fixtures" / f"{paper}.build.log").read_bytes(),
                   "facultyassignment.cls": (root / "templates/facultyassignment.cls").read_bytes()}
        import json
        details = json.loads((root / "fixtures" / f"{paper}.build.json").read_text())
        return jsonify(service.register_version(value.get("assignment_id"), value.get("label"),
                       (root / "fixtures" / f"{paper}.pdf").read_bytes(), sources, details)), 201

    @api.post("/versions/compile")
    def compile_version():
        files = request.files.getlist("files")
        if not files or len(files) > 100 or len({f.filename for f in files}) != len(files):
            raise ValueError("Upload source files with distinct relative filenames, including main.tex.")
        sources = {f.filename: f.read(25 * 1024**2 + 1) for f in files}
        pdf, sources, compiler = compile_sources(sources)
        return jsonify(service.register_version(request.form.get("assignment_id"), request.form.get("label"), pdf, sources, compiler)), 201

    @api.post("/batches")
    def batch():
        value = body()
        return jsonify(service.create_batch(value.get("version_id"), value.get("section_id"), value.get("quantity"),
                       value.get("request_id"), value.get("students"), value.get("reprint_of_batch_id"),
                       seating_chart_id=value.get("seating_chart_id"), extras=value.get("extras", 0), seat_marks=value.get("seat_marks", False),
                       term_id=value.get("term_id"), section_ids=value.get("section_ids"), sheet_labels=value.get("sheet_labels", False),
                       fill_names=value.get("fill_names", False))), 202

    @api.get("/batches/<batch_id>")
    def batch_detail(batch_id):
        return jsonify(service.get_batch(batch_id))

    @api.post("/batches/<batch_id>/seat-marks")
    def seat_marks(batch_id):
        return jsonify(service.set_seat_marks(batch_id, body().get("seat_marks")))

    @api.post("/batches/<batch_id>/<operation>")
    def transition(batch_id, operation):
        return jsonify(service.transition(batch_id, operation))

    @api.get("/batches/<batch_id>/email")
    def email_options(batch_id):
        return jsonify(email_exports.options(batch_id))

    @api.post("/batches/<batch_id>/email")
    def email_export(batch_id):
        value = body()
        return jsonify(email_exports.submit(batch_id, value.get("recipient"), value.get("output_sha256"), value.get("request_id"))), 202

    # Send to printer (DOCUMENTS "SEND TO PRINTER"): one job's PDF, a stack's interleaved PDF or a seating chart's room PDF.
    @api.get("/printer")
    def printer_options():
        batch_ids = [item for item in request.args.get("batches", "").split(",") if item]
        return jsonify(printer_sends.options(request.args.get("kind", "job"), batch_ids, request.args.get("chart") or None))

    @api.post("/printer")
    def printer_send():
        value = body()
        return jsonify(printer_sends.submit(value.get("kind"), value.get("batch_ids"), value.get("outputs"), value.get("sides"),
                                            value.get("request_id"), value.get("seating_chart_id") or None, actor=actor())), 202

    @api.get("/batches/<batch_id>/download")
    def download(batch_id):
        return send_file(io.BytesIO(service.download(batch_id)), mimetype="application/pdf", as_attachment=True, download_name=f"print-{batch_id}.pdf")

    @api.get("/stack")
    def stack_download():
        # Every version of a stack printed as one file, the versions interleaved (A1, B1, C1, D1, A2 ...) so neighbouring students
        # get different versions (requirement, 2026-10-01). The Print page's one Print PDF of a quiz printed for the term; nothing is issued.
        batch_ids = [item for item in request.args.get("batches", "").split(",") if item]
        content = service.section_stack(batch_ids)
        title = "".join(c if c.isalnum() else "-" for c in service.get_batch(batch_ids[0])["title"]).strip("-") or "print"
        return send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=f"{title}-all-versions.pdf")

    @api.get("/batches/<batch_id>/manifest")
    def manifest(batch_id):
        return send_file(io.BytesIO(service.download(batch_id, manifest=True)), mimetype="application/json", as_attachment=True, download_name=f"print-{batch_id}.json")

    @api.get("/batches/<batch_id>/labels")
    def labels(batch_id):
        media = request.args.get("media", DEFAULT_MEDIA)
        return send_file(io.BytesIO(service.labels(batch_id, media)), mimetype="application/pdf", as_attachment=True,
                         download_name=f"labels-{media}-{batch_id}.pdf")

    @api.post("/lookup")
    def lookup():
        value = body()
        return jsonify(provenance=service.resolve_token(value.get("token"), authenticated=True, alias=value.get("alias") is True))

    @api.post("/copies/<copy_id>/assign")
    def assign(copy_id):
        value = body()
        return jsonify(service.assign_copy(copy_id, value.get("enrollment_id"), value.get("reason")))

    @api.get("/sections/<section_id>/enrollments")
    def enrollments(section_id):
        # `enrollments` as before; `students` adds each one's roster name for the copy pickers (read under the roster lock).
        return jsonify(service.section_students(section_id))

    @api.get("/terms/<term_id>/enrollments")
    def term_enrollments(term_id):
        # A job printed for the whole term (no section): every section's students, each with their own section.
        return jsonify(service.term_students(term_id))

    @api.get("/messages/<message_id>")
    def message(message_id):
        # ?view=summary leaves out every page row and assembled paper (the stage pages that show neither). The answer carries an
        # ETag, so a page polling an unchanged scan gets a 304 and no body; Cache-Control stays no-store.
        response = jsonify(scans.review_item(message_id, heavy=request.args.get("view") != "summary"))
        response.add_etag()
        return response.make_conditional(request)

    @api.post("/messages/<message_id>/retry")
    def decode_retry(message_id):
        scans.retry(message_id)
        return jsonify(scans.review_item(message_id))

    @api.post("/scans/<scan_id>/recover")
    def recover(scan_id):
        value = body()
        return jsonify(scans.recover(scan_id, value.get("alias"), value.get("reason")))

    @api.post("/scans/<scan_id>/set-aside")
    def set_aside(scan_id):
        # A page in no paper (a TA's section header sheet, a scratch page), set aside with the instructor's reason, or `undo: true`
        # (requirement, 2026-10-02). `scan_id` is the scan page, as for recover. Kept and listed; its scan no longer waits on it.
        return jsonify(scans.set_aside(scan_id, body(), actor()))

    @api.post("/messages/<message_id>/papers/held")
    def group_held(message_id):
        # A paper Papers to check holds, grouped by hand from the sides the instructor ticked (requirement, 2026-10-02). Undone like
        # any grouping (POST .../sequence/<run_id>/undo).
        result = scans.sequence.group_held(message_id, body(), actor())
        # Grouped after the scan passed its review: this paper follows the rest of the scan to grading now (2026-09-28).
        scans.sequence.assemble_reviewed(message_id, "automatic", quiet=True)
        return jsonify(result), 200 if result["repeated"] else 201

    @api.post("/messages/<message_id>/version-sort")
    def version_sort(message_id):
        scans.version_sort.request(message_id, body())
        return jsonify(scans.review_item(message_id)), 202

    @api.post("/messages/<message_id>/version-sort/retry")
    def version_sort_retry(message_id):
        scans.version_sort.retry(message_id)
        return jsonify(scans.review_item(message_id))

    @api.get("/messages/<message_id>/unlabeled")
    def unlabeled(message_id):
        return jsonify(scans.unlabeled.summary(message_id))

    @api.post("/messages/<message_id>/unlabeled")
    def unlabeled_request(message_id):
        return jsonify(scans.unlabeled.request(message_id, body())), 202

    @api.post("/messages/<message_id>/unlabeled/review")
    def unlabeled_review(message_id):
        return jsonify(scans.unlabeled.review(message_id, body()))

    @api.post("/messages/<message_id>/unlabeled/students")
    def unlabeled_students(message_id):
        # POST keeps the typed UIN digits out of URLs and any request log.
        return jsonify(scans.unlabeled.search_students(message_id, body()))

    @api.post("/messages/<message_id>/unlabeled/cancel")
    def unlabeled_cancel(message_id):
        return jsonify(scans.unlabeled.cancel(message_id))

    @api.post("/messages/<message_id>/unlabeled/register")
    def unlabeled_register(message_id):
        return jsonify(scans.unlabeled.register(message_id, body())), 201

    @api.get("/messages/<message_id>/unlabeled/<scan_page_id>/cover.png")
    def unlabeled_cover(message_id, scan_page_id):
        return send_file(io.BytesIO(scans.unlabeled.crop(message_id, scan_page_id)), mimetype="image/png")

    @api.get("/messages/<message_id>/unlabeled/<scan_page_id>/other")
    def unlabeled_other(message_id, scan_page_id):
        return jsonify(scans.unlabeled.other_paper(message_id, scan_page_id))

    # Stamped papers printed for no student (a drafted quiz's unnamed versions): the name read on each, and naming the rest.
    @api.get("/messages/<message_id>/copy-names")
    def copy_names(message_id):
        return jsonify(scans.copy_names.summary(message_id))

    @api.post("/messages/<message_id>/copy-names/assign")
    def copy_names_assign(message_id):
        return jsonify(scans.copy_names.assign(message_id, body()))

    @api.post("/messages/<message_id>/copy-names/students")
    def copy_names_students(message_id):
        # POST keeps the typed UIN digits out of URLs and any request log.
        return jsonify(scans.copy_names.search_students(message_id, body()))

    @api.get("/messages/<message_id>/copy-names/<scan_page_id>/name.png")
    def copy_names_crop(message_id, scan_page_id):
        return send_file(io.BytesIO(scans.copy_names.crop(message_id, scan_page_id)), mimetype="image/png")

    @api.get("/messages/<message_id>/papers-to-check")
    def papers_to_check(message_id):
        return jsonify(scans.papers_to_check(message_id))

    @api.get("/messages/<message_id>/pages/<scan_page_id>/image.png")
    def scan_page_image(message_id, scan_page_id):
        # A thumbnail by default; opened on its own, the page at up to the retained 200 DPI width.
        width = min(max(request.args.get("width", 700, type=int), 200), 1700)
        return send_file(io.BytesIO(scans.page_image(message_id, scan_page_id, width)), mimetype="image/png")

    @api.post("/messages/<message_id>/sequence/preview")
    def sequence_preview(message_id):
        # The instructor's own preview and apply put sheets scanned back side first in order (automatic runs never do).
        return jsonify(scans.sequence.preview(message_id, body().get("sides"), turn=True))

    @api.post("/messages/<message_id>/sequence")
    def sequence_apply(message_id):
        value = body()
        result = scans.sequence.apply(message_id, value.get("sides"), value.get("fingerprint"), turn=True)
        # Grouped after the scan passed its review: these papers follow the rest of the scan to grading now (2026-09-28).
        if scans.sequence.assemble_reviewed(message_id, "automatic", quiet=True)["assembled"]:
            result = scans.review_item(message_id)
        return jsonify(result), 201

    @api.get("/packets/<packet_id>/download")
    def packet_download(packet_id):
        content, name = scans.sequence.packet_pdf(packet_id)
        return send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=name)

    @api.post("/messages/<message_id>/label-mode")
    def label_mode(message_id):
        value = body()
        return jsonify(scans.sequence.set_label_mode(message_id, value.get("batch_ids"), value.get("decision")))

    @api.post("/messages/<message_id>/sequence/<run_id>/undo")
    def sequence_undo(message_id, run_id):
        return jsonify(scans.sequence.undo(message_id, run_id))

    @api.post("/scans/<scan_id>/alias-ocr/retry")
    def alias_ocr_retry(scan_id):
        scans.alias_ocr.retry(scan_id)
        return jsonify(status="pending")

    @api.get("/copies/<copy_id>/assembly")
    def assembly(copy_id):
        return jsonify(scans.assembly(copy_id))

    @api.post("/copies/<copy_id>/assembly")
    def select_assembly(copy_id):
        value = body()
        return jsonify(scans.select_assembly(copy_id, value.get("selections"), value.get("partial", False), value.get("reason"))), 201

    @api.get("/papers")
    def student_papers():
        # One student's papers of one assignment, as page-image routes (the assistant's student_paper). Read only.
        return jsonify(scans.student_papers(request.args.get("enrollment_id"), request.args.get("assignment_id")))

    @api.get("/assemblies/<revision_id>/download")
    def assembly_download(revision_id):
        return send_file(io.BytesIO(scans.download_assembly(revision_id)), mimetype="application/pdf", as_attachment=True, download_name=f"reviewed-{revision_id}.pdf")

    @api.post("/assemblies/<revision_id>/choose")
    def choose(revision_id):
        return jsonify(scans.choose_submission(revision_id, body().get("reason")))

    app.register_blueprint(api)
