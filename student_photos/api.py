"""`/api/student-photos` (AGENTS.md "Student photos"): the private image route and the photo roster import. Writes pass the app's
same-origin checks; nothing here is reachable with Inbox keys or codes. The image is an education record: it carries no cacheable-
forever policy, only a checksum to revalidate against."""
from flask import Blueprint, Response, jsonify, request

from .service import MAX_PDF


def register(app, service):
    api = Blueprint("student_photos", __name__, url_prefix="/api/student-photos")

    def upload():
        file = request.files.get("file")
        if file is None:
            raise ValueError("Choose the photo roster PDF.")
        content = file.read(MAX_PDF + 1)
        return content, file.filename or ""

    @api.get("/<enrollment_id>")
    def photo(enrollment_id):
        content, digest = service.image(enrollment_id)
        response = Response(content, mimetype="image/jpeg")
        response.headers["Content-Disposition"] = "inline"
        response.set_etag(digest)
        return response.make_conditional(request)

    @api.post("/sections/<group_id>/<int:number>/preview")
    def preview(group_id, number):
        content, name = upload()
        return jsonify(service.preview(group_id, number, content, name))

    @api.post("/sections/<group_id>/<int:number>/import")
    def import_photos(group_id, number):
        content, name = upload()
        data = {key: request.form.get(key, "") for key in ("request_id", "fingerprint", "confirmation")}
        return jsonify(service.apply(group_id, number, content, name, data))

    app.register_blueprint(api)
