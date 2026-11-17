"""`/api/viewer`: the in-app file viewer's reads (AGENTS.md "Faculty assistant", VIEWER). Each names a Faculty file route in `route` and
answers what the viewer shows of it; nothing is written, and every answer is private (no-store, like the file it comes from)."""
import io

from flask import Blueprint, jsonify, request, send_file

from .service import FileViewer


def register(app):
    api = Blueprint("file_viewer", __name__, url_prefix="/api/viewer")
    viewer = FileViewer(app)

    def private(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.get("/file")
    def describe():
        return jsonify(viewer.describe(request.args.get("route", ""), native=request.args.get("native") == "1"))

    @api.get("/pdf")
    def pdf():
        # A PDF Faculty made, for the browser's own PDF viewer in the app's file viewer: inline, private, and (app.py) the one
        # response the app's own pages may frame.
        name, content = viewer.pdf(request.args.get("route", ""), request.args.get("sha256") or None)
        return private(send_file(io.BytesIO(content), mimetype="application/pdf", as_attachment=False, download_name=name, max_age=0))

    @api.get("/page.png")
    def page():
        try:
            number = int(request.args.get("page", ""))
        except ValueError:
            raise ValueError("Name the page.") from None
        content, _ = viewer.page(request.args.get("route", ""), number, request.args.get("sha256") or None)
        return private(send_file(io.BytesIO(content), mimetype="image/png", max_age=0))

    @api.get("/picture")
    def picture():
        media, content = viewer.picture(request.args.get("route", ""))
        return private(send_file(io.BytesIO(content), mimetype=media, max_age=0))

    app.register_blueprint(api)
