"""Private instructor routes for Sending grades to Gradescope (AGENTS.md), bubble sheets (upload only) and publishing grades there.
Writes pass the app's same-origin checks; nothing here is reachable with Inbox keys or codes. The preview and the send read Gradescope and can take a while; the page waits for them."""
from functools import partial

from flask import Blueprint, jsonify

from request_limits import json_object


def register(app, service):
    api = Blueprint("frq_gradescope", __name__, url_prefix="/api/grading/gradescope")

    body = partial(json_object, silent=True)

    @api.get("/assignments/<aid>")
    def status(aid):
        return jsonify(service.status(aid))

    @api.post("/assignments/<aid>/preview")
    def preview(aid):
        return jsonify(service.preview(aid, body()))

    @api.post("/assignments/<aid>/send")
    def send(aid):
        return jsonify(service.send(aid, body())), 201

    @api.post("/assignments/<aid>/sends/<send_id>/cancel")
    def cancel(aid, send_id):
        body()
        return jsonify(service.cancel(aid, send_id))

    @api.post("/assignments/<aid>/sends/<send_id>/retry")
    def retry(aid, send_id):
        body()
        return jsonify(service.retry(aid, send_id))

    @api.get("/assignments/<aid>/sends/<send_id>/problems")
    def problems(aid, send_id):
        return jsonify(service.problems(aid, send_id))

    # Making the assignment on Gradescope (`gradescope_create`): a preview that writes nothing, then the approved creation.
    @api.post("/assignments/<aid>/create/preview")
    def create_preview(aid):
        return jsonify(service.create_preview(aid, body()))

    @api.post("/assignments/<aid>/create")
    def create(aid):
        return jsonify(service.create(aid, body())), 201

    # RENAMED ITEMS (GRADESCOPE): an item Faculty made, renamed on Gradescope since, still matches its line.
    @api.post("/assignments/<aid>/item-wordings")
    def item_wordings(aid):
        return jsonify(service.record_item_wordings(aid, body())), 201

    @api.post("/assignments/<aid>/creations/<creation_id>/cancel")
    def create_cancel(aid, creation_id):
        body()
        return jsonify(service.create_cancel(aid, creation_id))

    @api.post("/assignments/<aid>/creations/<creation_id>/retry")
    def create_retry(aid, creation_id):
        body()
        return jsonify(service.create_retry(aid, creation_id))

    # Publishing the plan's grades on Gradescope (`gradescope_publish`; requirement, 2026-10-02): a preview that writes nothing, then
    # the approved publication. GET reads Faculty's records only.
    @api.get("/assignments/<aid>/publications")
    def publications(aid):
        return jsonify(service.publish_status(aid))

    @api.post("/assignments/<aid>/publish/preview")
    def publish_preview(aid):
        return jsonify(service.publish_preview(aid, body()))

    @api.post("/assignments/<aid>/publish")
    def publish(aid):
        return jsonify(service.publish(aid, body())), 201

    @api.post("/assignments/<aid>/publications/<publication_id>/cancel")
    def publish_cancel(aid, publication_id):
        body()
        return jsonify(service.publish_cancel(aid, publication_id))

    @api.post("/assignments/<aid>/publications/<publication_id>/retry")
    def publish_retry(aid, publication_id):
        body()
        return jsonify(service.publish_retry(aid, publication_id))

    # Bubble sheets, upload only (`gradescope_sheets`): the Bubble sheets page's own panel, the same shape as the written exam's.
    @api.get("/assignments/<aid>/sheets")
    def sheet_status(aid):
        return jsonify(service.sheet_status(aid))

    @api.post("/assignments/<aid>/sheets/preview")
    def sheet_preview(aid):
        return jsonify(service.sheet_preview(aid, body()))

    @api.post("/assignments/<aid>/sheets/send")
    def sheet_send(aid):
        return jsonify(service.sheet_send(aid, body())), 201

    @api.post("/assignments/<aid>/sheets/sends/<send_id>/cancel")
    def sheet_cancel(aid, send_id):
        body()
        return jsonify(service.sheet_cancel(aid, send_id))

    @api.post("/assignments/<aid>/sheets/sends/<send_id>/retry")
    def sheet_retry(aid, send_id):
        body()
        return jsonify(service.sheet_retry(aid, send_id))

    @api.get("/assignments/<aid>/sheets/sends/<send_id>/problems")
    def sheet_problems(aid, send_id):
        return jsonify(service.sheet_problems(aid, send_id))

    # THE MC CHECK (`gradescope_mc`; requirement, 2026-10-06): Check MC queues a read of Gradescope's bubble-sheet scores for the sender;
    # it writes nothing to Gradescope. The panels' GET above show the comparison.
    @api.post("/assignments/<aid>/mc-check")
    def mc_check(aid):
        body()
        return jsonify(service.mc_check_start(aid)), 202

    # MC LABELS (`gradescope_mc_labels`; requirement, 2026-10-28): Label answers renames each bubble-sheet answer's rubric item on
    # Gradescope so it says whether it is right and what the answer is, by Faculty's key; the sender does it a moment later.
    @api.post("/assignments/<aid>/mc-labels")
    def mc_labels(aid):
        body()
        return jsonify(service.mc_label_start(aid)), 202

    # Importing rubrics from Gradescope (`gradescope_rubric_import`; requirement, 2026-10-24): reads Gradescope only; the import is the
    # ordinary rubric upload of the CSV Gradescope's export would give for each chosen question.
    @api.post("/assignments/<aid>/rubrics/preview")
    def rubric_import_preview(aid):
        return jsonify(service.rubric_import_preview(aid, body()))

    @api.post("/assignments/<aid>/rubrics/import")
    def rubric_import(aid):
        return jsonify(service.rubric_import(aid, body())), 201

    # A bubble-sheet exam's answer key from Gradescope (2026-10-25), saved as the plan's key revision.
    @api.post("/assignments/<aid>/rubrics/bubble-key")
    def bubble_key_import(aid):
        return jsonify(service.bubble_key_import(aid, body())), 201

    # A section's own Gradescope course, set on its course page: GET reads Faculty only, /courses reads Gradescope's list.
    @api.get("/sections/<section_id>")
    def section_link(section_id):
        return jsonify(service.section_link(section_id))

    @api.get("/sections/<section_id>/courses")
    def section_courses(section_id):
        return jsonify(service.section_courses(section_id))

    @api.post("/sections/<section_id>")
    def link_section(section_id):
        return jsonify(service.link_section(section_id, body()))

    app.register_blueprint(api)
