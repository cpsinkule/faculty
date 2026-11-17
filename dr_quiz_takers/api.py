"""`/api/accommodations/dr-quiz-takers` (ACCOMMODATIONS "DR QUIZ TAKERS"): writes pass the same-origin checks and name their actor."""
from flask import Blueprint, jsonify, request

from request_actor import current as actor


def register(app, service):
    api = Blueprint("dr_quiz_takers", __name__, url_prefix="/api/accommodations/dr-quiz-takers")
    api.add_url_rule("/<enrollment_id>", "student", lambda enrollment_id: jsonify(service.student(enrollment_id)), methods=["GET"])
    api.add_url_rule("/<enrollment_id>", "save", lambda enrollment_id: jsonify(service.save(enrollment_id, request.get_json(silent=True), actor())), methods=["POST"])
    app.register_blueprint(api)
