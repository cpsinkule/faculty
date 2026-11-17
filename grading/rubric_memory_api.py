"""Routes of a course's rubric memories (GRADING "RUBRIC MEMORIES"): the course page reads them, keeps or drops what a learning pass
proposed, and asks for a pass over the course's certified assignments. Writes pass the app's same-origin checks."""
from functools import partial

from flask import Blueprint, jsonify

from request_limits import json_object


def register(app, service):
    api = Blueprint("frq_rubric_memories", __name__, url_prefix="/api/rubric-memories/<definition_id>")

    body = partial(json_object, silent=True)

    @api.get("")
    def view(definition_id):
        return jsonify(service.view(definition_id))

    @api.post("/decisions")
    def decide(definition_id):
        return jsonify(service.decide(definition_id, body()))

    @api.post("/learn")
    def learn(definition_id):
        return jsonify(service.learn(definition_id, body()))

    app.register_blueprint(api)
