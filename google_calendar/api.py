"""`/api/google-calendar`: the Calendar page's Google Calendar dialog (AGENTS.md "Google Calendar sync"). Writes pass the app's
same-origin checks; nothing here is reachable with Inbox keys or codes. Google's own failures answer 502 with the reason (never
404/409, which refresh the whole app); a stale settings save is the ordinary 409."""
from functools import partial

from flask import Blueprint, jsonify, request

from request_limits import json_object

from .client import CalendarError


def register(app, service):
    api = Blueprint("google_calendar", __name__, url_prefix="/api/google-calendar")

    body = partial(json_object, silent=True)

    @api.errorhandler(CalendarError)
    def google_failed(error):
        return jsonify(error=str(error)), 502

    @api.get("")
    def status():
        return jsonify(service.status())

    @api.post("/connect")
    def connect():
        # The address the instructor uses, for links back to Faculty in each Google event (the Origin the write check matched).
        return jsonify(service.connect(body(), request.headers.get("Origin") or request.host_url))

    @api.post("/settings")
    def settings():
        return jsonify(service.save(body()))

    @api.post("/sync")
    def sync():
        body()
        return jsonify(service.sync_now())

    @api.get("/calendars")
    def calendars():
        return jsonify(service.calendars())

    @api.post("/remove")
    def remove():
        return jsonify(service.remove(body()))

    app.register_blueprint(api)
