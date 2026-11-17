"""Student requests require explicit, versioned instructor decisions and effects."""
import io
import json
import unittest
import uuid
from unittest.mock import patch

from course_storage import timestamp
from rosters import connect
import test_exam_workflow as workflow_fixture
from test_exam_workflow import HEADERS
from test_rooms import chart_bytes


class ExamRequestTests(unittest.TestCase):
    setUp = workflow_fixture.ExamWorkflowTests.setUp
    import_students = workflow_fixture.ExamWorkflowTests.import_students
    reload = workflow_fixture.ExamWorkflowTests.reload

    def request(self, kind='makeup'):
        result = self.client.post(f"/api/exams/{self.event['id']}/requests", json={
            'section_id': self.alice['section_id'], 'enrollment_id': self.alice['enrollment_id'],
            'kind': kind, 'description': 'Please help with the exam.'}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def decide(self, row, status='approved'):
        response = self.client.patch(f"/api/exams/{self.event['id']}/requests/{row['id']}",
            json={'version': row['version'], 'status': status, 'decision': 'Approved after reviewing the circumstances.'}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def sitting(self):
        self.reload()
        result = self.client.post(f"/api/exams/{self.event['id']}/sittings", json={
            'version': self.event['version'], 'label': 'Makeup', 'kind': 'makeup',
            'date': '2026-10-02', 'time': '10:00 AM', 'location': 'Office'}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        return next(s for s in result.json['sittings'] if s['label'] == 'Makeup')

    def test_home_card_opens_the_exam_whose_requests_it_counts(self):
        """ASSIGNMENT-WORKSPACE: a bare #exams opens Assignments, so Home's card names the exam (or the exam records list)."""
        from workspace_overview import register
        if "workspace_overview" not in self.app.blueprints:
            register(self.app)
        card = lambda: next(item for item in self.client.get("/api/workspace/overview").json["attention"] if item["key"] == "exam_requests")
        self.request()
        self.assertEqual((card()["count"], card()["href"]), (1, f"#exams/{self.event['id']}?tab=requests"))
        other = self.exams.create({"term_id": self.term["id"], "section_ids": [self.section["id"]],
                                   "assignment_id": self.documents.create_assignment(self.term["definition_id"], "exam-2", "Exam 2")["id"]})
        self.assertEqual(self.client.post(f"/api/exams/{other['id']}/requests", json={
            'section_id': self.alice['section_id'], 'enrollment_id': self.alice['enrollment_id'],
            'kind': 'makeup', 'description': 'Please help with the second exam.'}, headers=HEADERS).status_code, 201)
        self.assertEqual((card()["count"], card()["href"]), (2, "#exams?view=list"))

    def test_sitting_application_requires_decision_exact_preview_and_is_idempotent(self):
        row, sitting = self.request(), self.sitting()
        base = f"/api/exams/{self.event['id']}/requests/{row['id']}"
        self.assertEqual(self.client.get(base + '/preview', query_string={'sitting_id': sitting['id']}).status_code, 409)
        row = self.decide(row)
        preview = self.client.get(base + '/preview', query_string={'sitting_id': sitting['id']})
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertIn('expectation', preview.json['changes'][1]['message'])
        self.assertIn('reason', preview.json['changes'][1]['message'])
        payload = {'version': row['version'], 'sitting_id': sitting['id'], 'preview_hash': preview.json['preview_hash']}
        self.assertEqual(self.client.post(base + '/apply', json={**payload, 'preview_hash': 'changed'}, headers=HEADERS).status_code, 409)
        first = self.client.post(base + '/apply', json=payload, headers=HEADERS)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json['status'], 'resolved')
        again = self.client.post(base + '/apply', json=payload, headers=HEADERS)
        self.assertEqual(again.status_code, 200, again.json)
        participant = next(p for p in self.reload()['participants'] if p['enrollment_id'] == self.alice['enrollment_id'])
        self.assertEqual(participant['sitting_id'], sitting['id'])
        self.assertEqual(participant['expectation'], 'expected')
        self.assertEqual(self.client.patch(base, json={'version': first.json['version'], 'status': 'open', 'decision': ''}, headers=HEADERS).status_code, 409)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_request_history WHERE request_id=? AND kind='applied'", (row['id'],)).fetchone()[0], 1)

    def test_preview_invalidates_after_sitting_change_and_scope_is_checked(self):
        row, sitting = self.request(), self.sitting()
        row = self.decide(row)
        base = f"/api/exams/{self.event['id']}/requests/{row['id']}"
        preview = self.client.get(base + '/preview', query_string={'sitting_id': sitting['id']}).json
        self.exams.save_sitting(self.event['id'], {**sitting, 'time': '11:00 AM'}, sitting['id'])
        result = self.client.post(base + '/apply', json={'version': row['version'], 'sitting_id': sitting['id'], 'preview_hash': preview['preview_hash']}, headers=HEADERS)
        self.assertEqual(result.status_code, 409, result.json)
        self.assertEqual(self.client.get(f"/api/exams/not-this-exam/requests/{row['id']}/preview", query_string={'sitting_id': sitting['id']}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/exams/{self.event['id']}/requests", json={
            'section_id': 'wrong-section', 'enrollment_id': self.alice['enrollment_id'], 'kind': 'other', 'description': 'x'}, headers=HEADERS).status_code, 400)

    def test_request_cards_are_local_and_synced_responses_enter_review_once(self):
        cards = self.app.extensions['response_cards']
        route = f"/api/exams/{self.event['id']}"
        with patch.object(cards.remote, 'call', side_effect=AssertionError('No outbound publication')):
            response = self.client.post(route + '/request-cards', json={'request_id': 'request-cards-1'}, headers=HEADERS)
            self.assertEqual(response.status_code, 201, response.json)
            repeated = self.client.post(route + '/request-cards', json={'request_id': 'request-cards-1'}, headers=HEADERS)
            self.assertEqual(response.json['issue_id'], repeated.json['issue_id'])
        issue = cards.get_issue(response.json['issue_id'])
        recipient = next(r for r in issue['recipients'] if r['enrollment_id'] == self.alice['enrollment_id'])
        self.assertEqual(recipient['publish_status'], 'pending')
        self.assertEqual(recipient['delivery_status'], 'not_sent')
        submission = {'id': str(uuid.uuid4()), 'answers': {'request_kind': 'Makeup request', 'request_details': '<img src=x> Please reschedule.'},
                      'request_id': 'student-request-1', 'status': 'submitted', 'submitted_at': timestamp()}
        with connect(self.path) as db:
            db.execute('INSERT INTO response_card_submissions VALUES(?,?,?,?,?,?)',
                       (submission['id'], recipient['id'], submission['request_id'], json.dumps(submission), submission['submitted_at'], timestamp()))
        # The listing counts synced responses the next import would add, without importing them.
        waiting = self.client.get(route + '/requests').json
        self.assertEqual((waiting['waiting'], waiting['requests']), (1, []))
        first = self.client.post(route + '/requests/sync', json={}, headers=HEADERS)
        self.assertEqual(first.json, {'added': 1})
        self.assertEqual(self.client.post(route + '/requests/sync', json={}, headers=HEADERS).json, {'added': 0})
        listing = self.client.get(route + '/requests').json
        self.assertEqual(listing['waiting'], 0)
        request = listing['requests'][0]
        self.assertEqual((request['status'], request['kind'], request['source_submission_id']), ('open', 'makeup', submission['id']))
        self.assertEqual(request['description'], submission['answers']['request_details'])
        self.assertEqual(self.reload()['participants'][0]['sitting_id'], None)

    def test_decision_cards_require_review_and_revalidate_before_publication(self):
        row = self.request()
        route = f"/api/exams/{self.event['id']}/requests/{row['id']}/decision-card"
        self.assertEqual(self.client.post(route, json={'request_id': 'decision-1'}, headers=HEADERS).status_code, 400)
        row = self.decide(row, 'declined')
        response = self.client.post(route, json={'request_id': 'decision-1'}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        issue = self.app.extensions['response_cards'].get_issue(response.json['issue_id'])
        self.assertEqual(len(issue['recipients']), 1)
        self.assertIn('Approved after reviewing', issue['recipients'][0]['schema']['fields'][1]['label'])
        self.app.extensions['exam_requests'].validate_publication(issue['id'])
        self.decide(row, 'approved')
        from werkzeug.exceptions import Conflict
        with self.assertRaises(Conflict):
            self.app.extensions['exam_requests'].validate_publication(issue['id'])

    def test_approved_seat_move_uses_existing_seating_and_same_atomic_receipt(self):
        room = self.client.post('/api/rooms', data={'name': 'Exam room', 'file': (io.BytesIO(chart_bytes({'A1': 'A1', 'B1': 'A2', 'C1': 'A3'})), 'room.xlsx')}, headers=HEADERS).json
        chart = self.client.post('/api/seating', json={'assignment_id': self.assignment['id'], 'section_ids': [self.section['id']], 'room_id': room['id']}, headers=HEADERS)
        self.assertEqual(chart.status_code, 201, chart.json)
        main = self.exams.save_sitting(self.event['id'], {'version': self.reload()['version'], 'label': 'Main', 'kind': 'main'})['sittings'][0]
        makeup = self.sitting()
        self.exams.attach_chart(self.event['id'], {'version': self.reload()['version'], 'chart_id': chart.json['id'], 'sitting_id': main['id']})
        participant = next(p for p in self.reload()['participants'] if p['enrollment_id'] == self.alice['enrollment_id'])
        self.exams.update_participant(self.event['id'], participant['section_id'], participant['enrollment_id'], {'version': participant['version'], 'sitting_id': makeup['id']})
        with connect(self.path) as db:
            occupied = {r[0] for r in db.execute('SELECT seat_id FROM seating_assignments WHERE chart_id=?', (chart.json['id'],))}
        seat = next(s for s in room['seats'] if s['id'] not in occupied)
        row = self.decide(self.request('seat'))
        base = f"/api/exams/{self.event['id']}/requests/{row['id']}"
        wrong_sitting = self.client.get(base + '/preview', query_string={'seat_id': seat['id']})
        self.assertEqual(wrong_sitting.status_code, 409, wrong_sitting.json)
        participant = next(p for p in self.reload()['participants'] if p['enrollment_id'] == self.alice['enrollment_id'])
        self.exams.update_participant(self.event['id'], participant['section_id'], participant['enrollment_id'], {'version': participant['version'], 'sitting_id': main['id']})
        preview = self.client.get(base + '/preview', query_string={'seat_id': seat['id']})
        self.assertEqual(preview.status_code, 200, preview.json)
        result = self.client.post(base + '/apply', json={'version': row['version'], 'seat_id': seat['id'], 'preview_hash': preview.json['preview_hash']}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT seat_id FROM seating_assignments WHERE chart_id=? AND enrollment_id=?', (chart.json['id'], self.alice['enrollment_id'])).fetchone()[0], seat['id'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_events WHERE chart_id=? AND kind='move'", (chart.json['id'],)).fetchone()[0], 1)

    def test_private_write_boundary_and_generic_export_excludes_requests(self):
        self.assertEqual(self.client.post(f"/api/exams/{self.event['id']}/requests", json={}).status_code, 403)
        self.request()
        self.assertNotIn('Please help with the exam', self.client.get('/api/export').get_data(as_text=True))


class ManagedExamRequestTests(unittest.TestCase):
    managed = True
    setUp = ExamRequestTests.setUp
    import_students = ExamRequestTests.import_students
    reload = ExamRequestTests.reload
    request = ExamRequestTests.request
    decide = ExamRequestTests.decide
    sitting = ExamRequestTests.sitting
    test_seat_decision_uses_verified_term_lifecycle = ExamRequestTests.test_approved_seat_move_uses_existing_seating_and_same_atomic_receipt
    test_sitting_decision_uses_verified_term_lifecycle = ExamRequestTests.test_sitting_application_requires_decision_exact_preview_and_is_idempotent


if __name__ == '__main__':
    unittest.main()
