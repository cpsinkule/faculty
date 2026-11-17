"""Private template uploads, maps, previews and OCR/VLM reference packages."""
import io
from functools import lru_cache

from flask import Blueprint, jsonify, request, send_file
from PIL import Image

from assignment_templates import MAX_SOURCE


@lru_cache(maxsize=8)
def page_preview(content):
    """Disposable display bytes; callers verify the retained original on every read."""
    with Image.open(io.BytesIO(content)) as source:
        preview = source.convert('RGB')
        preview.thumbnail((1200, 1600), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        preview.save(output, format='WEBP', quality=85)
        return output.getvalue()


def register(app, service):
    api = Blueprint("assignment_templates", __name__, url_prefix="/api/assignment-templates")

    @api.get("")
    def listing():
        return jsonify(templates=service.listing(request.args.get("assignment_id")))

    @api.post("")
    def upload():
        entries = request.files.getlist("files")
        if not 1 <= len(entries) <= 100 or len({item.filename for item in entries}) != len(entries):
            raise ValueError("Upload files with distinct relative filenames; at most 100 files.")
        contents, remaining = {}, MAX_SOURCE
        for entry in entries:
            content = entry.read(remaining + 1)
            remaining -= len(content)
            if remaining < 0:
                raise ValueError("Template source files exceed 25 MB combined.")
            contents[entry.filename] = content
        return jsonify(service.upload(request.form.get("assignment_id"), request.form.get("label"),
                                      request.form.get("request_id"), contents, request.form.get("entrypoint"))), 201

    @api.get("/<identifier>")
    def detail(identifier):
        return jsonify(service.get(identifier, request.args.get("revision_id")))

    @api.put("/<identifier>/mapping")
    def mapping(identifier):
        return jsonify(service.save_mapping(identifier, request.get_json()))

    def download(content, media_type, filename, *, inline=False):
        response = send_file(io.BytesIO(content), mimetype=media_type, download_name=filename,
                             as_attachment=not inline, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.get("/<identifier>/pdf")
    def pdf(identifier):
        content, media_type = service.read(identifier, "pdf")
        return download(content, media_type, "grading-template.pdf", inline=True)

    @api.get("/<identifier>/pages/<int:page>.png")
    def image(identifier, page):
        content, media_type = service.read(identifier, "image", page)
        return download(content, media_type, f"template-page-{page}.png", inline=True)

    @api.get("/<identifier>/pages/<int:page>/preview.webp")
    def preview(identifier, page):
        content, _ = service.read(identifier, "image", page)
        return download(page_preview(content), 'image/webp', f'template-page-{page}.webp', inline=True)

    @api.get("/<identifier>/sources")
    def sources(identifier):
        return download(service.export(identifier, sources_only=True), "application/zip", "template-originals.zip")

    @api.get("/<identifier>/manifest")
    def manifest(identifier):
        return download(service.export(identifier, request.args.get("revision_id"), manifest_only=True),
                        "application/json", "template-map.json")

    @api.get("/<identifier>/package")
    def package(identifier):
        return download(service.export(identifier, request.args.get("revision_id")), "application/zip", "grading-template.zip")

    app.register_blueprint(api)
