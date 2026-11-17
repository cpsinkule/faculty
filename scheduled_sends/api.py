"""`/api/scheduled-sends`: the messages sent later (docs/decisions/mail.md MAIL-SEND-LATER). Scheduling itself is beside each send:
`POST /api/mail/schedule` (gmail_mail/api.py) and `POST /api/canvas/messages/schedule` (canvas/api.py). Writes pass the app's
same-origin checks."""
from functools import partial

from flask import Blueprint, jsonify, request

from request_actor import current as actor
from request_limits import json_object


def register(app, service):
    api = Blueprint("scheduled_sends", __name__, url_prefix="/api/scheduled-sends")
    body = partial(json_object, silent=True)

    @api.get("")
    def listing():
        return jsonify(service.listing(request.args.get("channel", "")))

    @api.get("/<scheduled_id>")
    def item(scheduled_id):
        return jsonify(service.item(scheduled_id))

    @api.post("/<scheduled_id>/cancel")
    def cancel(scheduled_id):
        return jsonify(service.cancel(scheduled_id, actor()))

    @api.post("/<scheduled_id>/dismiss")
    def dismiss(scheduled_id):
        return jsonify(service.dismiss(scheduled_id, actor()))

    @api.post("/<scheduled_id>/send")
    def send_now(scheduled_id):
        return jsonify(service.send_now(scheduled_id, body(), actor()))

    app.register_blueprint(api)
