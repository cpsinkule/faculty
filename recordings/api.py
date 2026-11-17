"""`/api/recordings` (docs/decisions/recordings.md, RECORDINGS "ROUTES"). Step 1: the list, Check now, and filing by hand. Step 2:
one recording with its transcript, its kept audio, a keyword search (which writes nothing) and naming a speaker. Step 3: Read
again, what became of an item (done, dismissed, reopened; a ruling accepted or declined at its rubric's finalization), Remember
(the instructor's own press: `assistant/policy.py` gives the Assistant no use of it), proposing an item's calendar entry again,
a term's coordinator, and the rulings held for one graded assignment. Step 4: the students a recording is of, each with their
part, and proposing a student's commitments card or note card again. Step 6: the trainings filed under one course (a read),
a search that also answers by meaning (`meaning`), and Ask the Assistant on a memo's request (the instructor's own press:
`assistant/policy.py` gives the Assistant no use of it). ONLY WHAT IS FACULTY'S IS KEPT: Don't keep (`discard`: the recording
is erased) and Ask me again (`ask-again`: what was not kept is listed again, to be asked about) are the instructor's own
presses too, and the Assistant has no use of either. Writes pass the app's same-origin checks; each takes a
request ID and records who made it. No address carries a student: the student a recording is filed under is named in the
request's body, by enrollment ID.

The audio is the kept file, checked against its checksum on every read, served to the page's own player: `inline`, no-store and
nosniff (the app sets both on every answer), with ranges so a player can start anywhere. It is never a page of this origin and
never read as data by the Assistant (`assistant/policy.py` GET_BLOCKED)."""
import io

from flask import Blueprint, jsonify, request, send_file

from request_actor import current as actor


def register(app, service):
    api = Blueprint("recordings", __name__, url_prefix="/api/recordings")

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def moment(name):
        value = request.args.get(name)
        if value in (None, ""):
            return None
        if not value.isdigit() or len(value) > 12:
            raise ValueError("Give the stretch in milliseconds from the start of the recording.")
        return int(value)

    @api.get("")
    def listing():
        return jsonify(service.listing(course=request.args.get("course") or None))

    @api.post("/check")
    def check():
        return jsonify(service.request(body()))

    @api.post("/search")
    def search():
        return jsonify(service.search(body()))

    @api.get("/rulings")
    def rulings():
        return jsonify(service.reading.rulings(request.args.get("assignment")))

    @api.get("/trainings")
    def trainings():
        return jsonify(service.trainings(request.args.get("definition_id")))

    @api.post("/coordinator")
    def coordinator():
        return jsonify(service.reading.set_coordinator(body(), actor()))

    @api.post("/ask-again")
    def ask_again():
        return jsonify(service.ask_again(body(), actor()))

    @api.get("/<recording_id>")
    def one(recording_id):
        return jsonify(service.recording(recording_id, start=moment("start_ms"), end=moment("end_ms")))

    @api.get("/<recording_id>/audio")
    def audio(recording_id):
        kind, content = service.audio(recording_id)
        response = send_file(io.BytesIO(content), mimetype=kind, max_age=0, conditional=True)
        response.headers["Content-Disposition"] = "inline"
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.post("/<recording_id>/file")
    def file(recording_id):
        return jsonify(service.file(recording_id, body(), actor()))

    @api.post("/<recording_id>/discard")
    def discard(recording_id):
        return jsonify(service.discard(recording_id, body(), actor()))

    @api.post("/<recording_id>/speakers")
    def speakers(recording_id):
        return jsonify(service.name_speaker(recording_id, body(), actor()))

    @api.post("/<recording_id>/students")
    def students(recording_id):
        return jsonify(service.set_students(recording_id, body(), actor()))

    @api.post("/<recording_id>/cards")
    def cards(recording_id):
        return jsonify(service.reading.student_card(recording_id, body(), actor()))

    @api.post("/<recording_id>/read")
    def read(recording_id):
        return jsonify(service.reading.ask(recording_id, body(), actor()))

    @api.post("/<recording_id>/items/<item_id>")
    def item(recording_id, item_id):
        return jsonify(service.reading.settle(recording_id, item_id, body(), actor()))

    @api.post("/<recording_id>/items/<item_id>/remember")
    def remember(recording_id, item_id):
        return jsonify(service.reading.remember(recording_id, item_id, body(), actor()))

    @api.post("/<recording_id>/items/<item_id>/ask")
    def ask(recording_id, item_id):
        return jsonify(service.reading.ask_assistant(recording_id, item_id, body(), actor()))

    @api.post("/<recording_id>/items/<item_id>/card")
    def card(recording_id, item_id):
        return jsonify(service.reading.card(recording_id, item_id, body(), actor()))

    app.register_blueprint(api)
