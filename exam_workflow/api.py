"""Private instructor APIs. The public listener exposes none of these routes."""
from flask import Blueprint, Response, jsonify, request

from request_limits import json_object

from .overview import ExamOverview


def register(app, exams, grading, requests):
    api = Blueprint('exams', __name__, url_prefix='/api/exams')
    # The lifecycle hub is read-only. Assisted grading is registered after this blueprint, so it is looked up per request.
    overview = ExamOverview(exams, lambda: app.extensions.get('frq_grading'))

    body = json_object

    @api.get('/catalog')
    def catalog():
        return jsonify(exams.catalog())

    @api.get('')
    def listing():
        items = exams.list()
        if request.args.get('summary') == '0':
            return jsonify(exams=items)
        # Each exam's lifecycle, and assessments of current terms that have seating, printing or grading but no exam record.
        lifecycles = overview.listing()
        by_event = {item['event_id']: item for item in lifecycles if item['event_id']}
        for item in items:
            item['summary'] = by_event.get(item['id'])
        return jsonify(exams=items, assessments=[item for item in lifecycles if not item['event_id']])

    @api.get('/lifecycle')
    def lifecycle():
        assignment_id, term_id = request.args.get('assignment_id'), request.args.get('term_id')
        if not assignment_id or not term_id:
            raise ValueError('Choose an assignment and a term.')
        return jsonify(overview.assessment(assignment_id, term_id))

    @api.post('')
    def create():
        return jsonify(exams.create(body())), 201

    @api.get('/<event_id>')
    def detail(event_id):
        return jsonify(exams.get(event_id))

    @api.get('/<event_id>/overview')
    def event_overview(event_id):
        return jsonify(overview.event(event_id))

    @api.patch('/<event_id>')
    def update(event_id):
        return jsonify(exams.update(event_id, body()))

    @api.post('/<event_id>/sittings')
    def sitting(event_id):
        return jsonify(exams.save_sitting(event_id, body())), 201

    @api.patch('/<event_id>/sittings/<sitting_id>')
    def change_sitting(event_id, sitting_id):
        return jsonify(exams.save_sitting(event_id, body(), sitting_id))

    @api.patch('/<event_id>/participants/<section_id>/<enrollment_id>')
    def participant(event_id, section_id, enrollment_id):
        return jsonify(exams.update_participant(event_id, section_id, enrollment_id, body()))

    @api.post('/<event_id>/roster')
    def roster(event_id):
        return jsonify(exams.sync_roster(event_id, body()))

    @api.post('/<event_id>/batches')
    def batch(event_id):
        return jsonify(exams.attach_batch(event_id, body()))

    @api.post('/<event_id>/charts')
    def chart(event_id):
        return jsonify(exams.attach_chart(event_id, body()))

    @api.post('/<event_id>/checkpoint')
    def checkpoint(event_id):
        return jsonify(exams.checkpoint(event_id, body()))

    @api.get('/<event_id>/requests')
    def request_list(event_id):
        return jsonify(requests.listing(event_id))

    @api.post('/<event_id>/requests')
    def create_request(event_id):
        return jsonify(requests.create(event_id, body())), 201

    @api.patch('/<event_id>/requests/<request_id>')
    def decide(event_id, request_id):
        return jsonify(requests.update(event_id, request_id, body()))

    @api.get('/<event_id>/requests/<request_id>/preview')
    def preview(event_id, request_id):
        return jsonify(requests.preview(event_id, request_id, request.args))

    @api.post('/<event_id>/requests/<request_id>/apply')
    def apply_request(event_id, request_id):
        return jsonify(requests.apply(event_id, request_id, body()))

    @api.post('/<event_id>/requests/sync')
    def sync(event_id):
        body()
        return jsonify(requests.sync(event_id))

    @api.post('/<event_id>/request-cards')
    def request_cards(event_id):
        return jsonify(requests.prepare_cards(event_id, body())), 201

    @api.post('/<event_id>/requests/<request_id>/decision-card')
    def decision_card(event_id, request_id):
        return jsonify(requests.prepare_cards(event_id, {**body(), 'exam_request_id': request_id}, 'decision')), 201

    @api.get('/<event_id>/grading')
    def grading_detail(event_id):
        return jsonify(grading.details(event_id, anonymous=request.args.get('anonymous') == '1',
                                       assignment_version_id=request.args.get('assignment_version_id')))

    @api.get('/<event_id>/grading/work')
    def work(event_id):
        return jsonify(grading.work(event_id, request.args.get('section_id'), request.args.get('enrollment_id'),
                                   request.args.get('question_id'), anonymous=request.args.get('anonymous') == '1'))

    @api.put('/<event_id>/grading/rubric')
    def rubric(event_id):
        return jsonify(grading.configure(event_id, body()))

    @api.post('/<event_id>/grading/template')
    def template(event_id):
        return jsonify(grading.apply_template(event_id, body()))

    @api.post('/<event_id>/grading/marks')
    def mark(event_id):
        return jsonify(grading.save_mark(event_id, body()))

    @api.post('/<event_id>/grading/finalize')
    def finalize(event_id):
        return jsonify(grading.finalize(event_id, body()))

    @api.post('/<event_id>/grading/reopen')
    def reopen(event_id):
        return jsonify(grading.reopen(event_id, body()))

    @api.post('/<event_id>/grading/release')
    def release(event_id):
        return jsonify(grading.release(event_id, body()))

    @api.post('/<event_id>/grading/feedback-cards')
    def feedback_cards(event_id):
        return jsonify(requests.prepare_cards(event_id, body(), 'feedback')), 201

    @api.get('/<event_id>/grading/releases/<release_id>')
    def feedback(event_id, release_id):
        return jsonify(grading.feedback(event_id, release_id))

    @api.get('/<event_id>/grading/export')
    def export(event_id):
        return Response(grading.export(event_id), mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename="exam-released-grades.csv"'})

    app.register_blueprint(api)
