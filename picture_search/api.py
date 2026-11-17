"""`/api/picture-search`: read-only. A result is a route Faculty already serves and where the picture is; never bytes.
A picture handed over to search by (`/like`) is embedded on this machine and kept nowhere."""
from flask import Blueprint, jsonify, request

from app_files import AppFiles

from .service import Unavailable

LIKE_BYTES = 25 * 1024**2  # of a picture or PDF handed over to search by


def register(app, service):
    from assistant.policy import SHOW_ONLY
    api = Blueprint("picture_search", __name__, url_prefix="/api/picture-search")
    files = AppFiles(app, use="searched by")

    @api.get("")
    def search():
        try:
            limit = int(request.args.get("limit", "12"))
        except ValueError:
            raise ValueError("limit is a whole number.") from None
        try:
            kinds = [name for name in request.args.get("sources", "").split(",") if name]
            return jsonify(service.search(request.args.get("q", ""), limit, kinds or None))
        except Unavailable as down:
            return jsonify(error=str(down), unavailable=True), 503

    def limits():
        try:
            return int(request.args.get("limit", "12")), int(request.args.get("page", "1"))
        except ValueError:
            raise ValueError("limit and page are whole numbers.") from None

    def like(content):
        limit, page = limits()
        if len(content) > LIKE_BYTES:
            raise ValueError("That file is too large to search by (25 MB at most).")
        kinds = [name for name in request.args.get("sources", "").split(",") if name]
        try:
            return jsonify(service.like(content, limit, kinds or None, page))
        except Unavailable as down:
            return jsonify(error=str(down), unavailable=True), 503

    @api.get("/like")
    def like_route():
        """What looks like a file Faculty already serves (`route`, read the way a page reads it; a PDF by `page`)."""
        route = request.args.get("route", "")
        if files.endpoint(route) in SHOW_ONLY:
            raise ValueError("Student photos are never embedded (PHOTOS), so they cannot be searched by.")
        return like(files.read(route)[2])

    @api.post("/like")
    def like_upload():
        """What looks like a picture the instructor drops in (`file`). Nothing of it is kept."""
        sent = request.files.get("file")
        if sent is None:
            raise ValueError("Choose a picture or a PDF to search by.")
        return like(sent.read(LIKE_BYTES + 1))

    @api.get("/status")
    def status():
        return jsonify(service.status())

    app.register_blueprint(api)
