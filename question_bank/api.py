"""`/api/question-bank` (AGENTS.md "Question bank"): a course's question bank, its outline, and the PDFs read for it. Writes pass the app's
same-origin checks; nothing here is reachable with Inbox keys or codes, and nothing is sent anywhere but to the configured model by the
dispatcher. Uploaded PDFs, their page pictures and the pictures kept with questions open in the browser (inline) and are never cached: they
are teaching material, not public assets."""
import io
from functools import partial

from flask import Blueprint, jsonify, request, send_file

from request_limits import json_object

from .pictures import MAX_PICTURE
from .service import MAX_PDF


def register(app, service):
    api = Blueprint("question_bank", __name__, url_prefix="/api/question-bank")

    body = partial(json_object, silent=True)

    @api.get("/definitions/<definition_id>")
    def bank(definition_id):
        return jsonify(service.view(definition_id, summary=request.args.get("summary") == "1"))

    @api.post("/definitions/<definition_id>/questions")
    def add_question(definition_id):
        return jsonify(service.add_question(definition_id, body()))

    @api.post("/definitions/<definition_id>/nodes")
    def add_node(definition_id):
        return jsonify(service.add_node(definition_id, body()))

    @api.post("/definitions/<definition_id>/tag")
    def tag(definition_id):
        return jsonify(service.tag(definition_id, body()))

    @api.post("/definitions/<definition_id>/uploads")
    def upload(definition_id):
        file = request.files.get("file")
        if file is None:
            raise ValueError("Choose the PDF.")
        return jsonify(service.upload(definition_id, file.read(MAX_PDF + 1), file.filename or "", request.form.get("request_id", "")))

    @api.get("/questions/<question_id>")
    def question(question_id):
        return jsonify(service.question(question_id))

    @api.post("/questions/<question_id>/revise")
    def revise_question(question_id):
        return jsonify(service.revise_question(question_id, body()))

    @api.post("/questions/<question_id>/classify")
    def classify(question_id):
        return jsonify(service.classify(question_id, body()))

    @api.post("/questions/<question_id>/retire")
    def retire(question_id):
        return jsonify(service.retire(question_id, body()))

    @api.post("/questions/<question_id>/restore")
    def restore(question_id):
        return jsonify(service.restore(question_id, body()))

    @api.get("/definitions/<definition_id>/statistics")
    def statistics(definition_id):
        return jsonify(service.statistics(definition_id))

    @api.post("/questions/<question_id>/printed")
    def set_printed(question_id):
        return jsonify(service.set_printed(question_id, body()))

    @api.post("/questions/<question_id>/versions")
    def link_versions(question_id):
        return jsonify(service.link_versions(question_id, body()))

    @api.post("/nodes/<node_id>")
    def revise_node(node_id):
        return jsonify(service.revise_node(node_id, body()))

    @api.get("/uploads/<upload_id>/found")
    def extraction(upload_id):
        return jsonify(service.extraction(upload_id))

    @api.post("/uploads/<upload_id>/accept")
    def accept(upload_id):
        return jsonify(service.accept(upload_id, body()))

    @api.post("/uploads/<upload_id>/read")
    def read_again(upload_id):
        return jsonify(service.read_again(upload_id, body()))

    # The last part of the address is the file's name, so a browser tab or a saved file is named after the upload.
    @api.get("/uploads/<upload_id>/<name>.pdf")
    def upload_file(upload_id, name):
        content, digest, filename = service.read_upload(upload_id)
        response = send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=request.args.get("download") == "1",
                             download_name=filename, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.set_etag(digest)
        return response

    # Pictures that go with a question: an image the instructor uploads, or a region cut from a page of a PDF read for the bank.
    @api.post("/questions/<question_id>/pictures")
    def add_picture(question_id):
        file = request.files.get("file")
        if file is None:
            raise ValueError("Choose the picture.")
        return jsonify(service.add_picture(question_id, file.read(MAX_PICTURE + 1), file.filename or "", request.form.get("caption", ""),
                                           request.form.get("request_id", "")))

    @api.post("/questions/<question_id>/pictures/cut")
    def cut_picture(question_id):
        return jsonify(service.cut_picture(question_id, body()))

    @api.post("/pictures/<picture_id>/remove")
    def remove_picture(picture_id):
        return jsonify(service.remove_picture(picture_id, body()))

    @api.post("/pictures/<picture_id>/restore")
    def restore_picture(picture_id):
        return jsonify(service.restore_picture(picture_id, body()))

    @api.get("/pictures/<picture_id>/picture.<extension>")
    def picture(picture_id, extension):
        content, digest, media_type = service.read_picture(picture_id)
        response = send_file(io.BytesIO(content), mimetype=media_type, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.set_etag(digest)
        return response

    @api.get("/uploads/<upload_id>/pages/<int:page>.jpg")
    def page_picture(upload_id, page):
        content, digest = service.page_picture(upload_id, page)
        response = send_file(io.BytesIO(content), mimetype="image/jpeg", max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.set_etag(digest)
        return response

    @api.post("/runs/<run_id>/retry")
    def retry_run(run_id):
        return jsonify(service.retry_run(run_id, body()))

    @api.post("/runs/<run_id>/cancel")
    def cancel_run(run_id):
        return jsonify(service.cancel_run(run_id, body()))

    app.register_blueprint(api)
