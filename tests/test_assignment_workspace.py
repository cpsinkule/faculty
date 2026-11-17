"""ASSIGNMENT-WORKSPACE: projections cannot create, certify, or resurrect deleted work."""
import hashlib
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlencode

from rosters import connect
import assignment_workspace as workspace
import test_exam_workflow as fixture
import test_certified_archive as archive_fixture
import test_gradescope_send as graded
from gradebook import sources

HEADERS = fixture.HEADERS


class AssignmentWorkspaceTests(unittest.TestCase):
    setUp = fixture.ExamWorkflowTests.setUp
    import_students = fixture.ExamWorkflowTests.import_students
    batch = fixture.ExamWorkflowTests.batch
    arrival = fixture.ExamWorkflowTests.arrival

    def url(self):
        return f"/api/assignments/{self.assignment['id']}/{self.term['id']}"

    def listed(self):
        response = self.client.get('/api/assignments')
        self.assertEqual(response.status_code, 200, response.json)
        return next(row for row in response.json['assignments'] if row['assignment_id'] == self.assignment['id'])

    def associate(self, message):
        # The synthetic arrival supplies decoded pages; certify the fixture's inspection as complete.
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))
        current = self.app.extensions['frq_grading'].scan_context(message)
        result = self.client.post(f'/api/grading/scans/{message}/associate', json={
            'assignment_id': self.assignment['id'], 'term_id': self.term['id'],
            'section_ids': [self.section['id']], 'fingerprint': current['fingerprint'], 'request_id': 'workspace:' + message}, headers=HEADERS)
        self.assertIn(result.status_code, (200, 201), result.json)
        return result.json

    def test_read_is_inert_and_preserves_legacy_api(self):
        before = self.client.get('/api/documents/catalog').data
        with connect(self.path) as db:
            counts = [db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('frq_assignments', 'exam_events', 'document_events')]
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(response.json['plan_id'])
        self.assertEqual(response.json['scans'], [])
        self.assertTrue({'paper', 'preparation'} <= {i['key'] for i in response.json['needs_you']})
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.client.get('/api/documents/catalog').data, before)
        with connect(self.path) as db:
            self.assertEqual(counts, [db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                                     for table in ('frq_assignments', 'exam_events', 'document_events')])

    def test_listing_and_legacy_context_use_permanent_pair(self):
        listed = self.client.get('/api/assignments')
        self.assertEqual(listed.status_code, 200, listed.json)
        item = next(i for i in listed.json['assignments'] if i['assignment_id'] == self.assignment['id'])
        self.assertEqual(item['term_id'], self.term['id'])
        for route in (f"#exams/{self.event['id']}", item['href'],
                      f"#documents?assignment={self.assignment['id']}&term={self.term['id']}&step=print"):
            response = self.client.get('/api/assignments/resolve?' + urlencode({'route': route}))
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['context']['href'], item['href'])
        self.assertEqual(self.client.get(self.url().replace(self.term['id'], 'missing-term')).status_code, 404)

    def test_gradebook_item_resolves_its_latest_assignment_reference(self):
        base = f"/api/gradebook/terms/{self.term['id']}/setup"
        saved = self.client.post(base, json={'base_revision': None, 'request_id': str(uuid.uuid4()),
            'setup': {'items': [{'title': 'Exam', 'points': '100', 'assignment_id': self.assignment['id']},
                                {'title': 'Typed scores', 'points': '10'}]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        items = saved.json['setup']['items']

        def resolve(item, route=None, assignment=None):
            query = {'item': item} if item else {}
            if assignment:
                query.update(assignment=assignment, pull='1')
            response = self.client.get('/api/assignments/resolve', query_string={
                'route': (route or f"#gradebook/{self.term['id']}") + ('?' + urlencode(query) if query else '')})
            self.assertEqual(response.status_code, 200, response.json)
            return response.json['context']

        context = resolve(items[0]['id'])
        self.assertEqual((context['assignment_id'], context['term_id']), (self.assignment['id'], self.term['id']))
        self.assertIsNone(resolve(items[1]['id']))
        self.assertIsNone(resolve('missing-item'))
        self.assertIsNone(resolve(None))
        self.assertEqual(resolve(None, assignment=self.assignment['id']), context)
        self.assertIsNone(resolve(None, assignment='missing-assignment'))
        self.assertIsNone(resolve('missing-item', assignment=self.assignment['id']))
        for route in (f"#course/{self.group['id']}/home/gradebook", f"#course/{self.group['id']}/section/501",
                      f"#course/{self.group['id']}/section/501/gradebook"):
            self.assertEqual(resolve(items[0]['id'], route), context)
            self.assertIsNone(resolve(items[1]['id'], route))
            self.assertIsNone(resolve(None, route))
            self.assertEqual(resolve(None, route, assignment=self.assignment['id']), context)
        for route in (f"#course/{self.group['id']}/section/999", f"#course/{self.group['id']}/section/501/roster",
                      '#course/missing-course/home/gradebook'):
            self.assertIsNone(resolve(items[0]['id'], route))
        other = self.documents.create_assignment(self.term['definition_id'], 'other', 'Other assignment')
        self.assertEqual(resolve(items[0]['id'], assignment=other['id']), context)
        self.assertEqual(resolve(None, assignment=other['id'])['assignment_id'], other['id'])
        changed = self.client.post(base, json={'base_revision': saved.json['setup']['revision'], 'request_id': str(uuid.uuid4()),
            'setup': {'items': [{**items[0], 'assignment_id': other['id']}, items[1]]}}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(resolve(items[0]['id'])['assignment_id'], other['id'])
        created = self.client.post('/api/courses', json={'name': 'Other course', 'code': 'MATH 142', 'term': 'Fall',
                                   'year': 2026, 'sections': [501]}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        with connect(self.path) as db:
            definition = db.execute("SELECT id FROM course_definitions WHERE code='MATH 142'").fetchone()[0]
        foreign = self.documents.create_assignment(definition, 'exam', 'Other course exam')
        self.assertIsNone(resolve(None, assignment=foreign['id']))

    def test_setup_reuses_plan_and_projection_shows_it_before_papers(self):
        body = {'assignment_id': self.assignment['id'], 'term_id': self.term['id'], 'section_ids': [self.section['id']]}
        first = self.client.post('/api/grading/assignments', json=body, headers=HEADERS)
        self.assertIn(first.status_code, (200, 201), first.json)
        second = self.client.post('/api/grading/assignments', json=body, headers=HEADERS)
        self.assertEqual(first.json['id'], second.json['id'])
        detail = self.client.get(self.url())
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['plan_id'], first.json['id'])
        self.assertEqual(detail.json['workflow']['certification']['state'], 'none')
        self.assertTrue(any(i['key'] == 'preparation' for i in detail.json['needs_you']))
        self.assertEqual(detail.json['unavailable'], [])
        self.assertEqual(detail.json['delivery']['plan_id'], first.json['id'])
        # ASSIGNMENT-WORKSPACE: both Return sub-tabs keep the same permanent assignment/term context.
        for area in ('teaching-review', 'grade-delivery'):
            resolved = self.client.get('/api/assignments/resolve', query_string={'route': f"#{area}/{first.json['id']}"})
            self.assertEqual(resolved.status_code, 200, resolved.json)
            self.assertEqual(resolved.json['context']['assignment_id'], self.assignment['id'])
            self.assertEqual(resolved.json['context']['term_id'], self.term['id'])

    def test_upload_the_scans_waits_for_you_once_the_date_has_passed_and_no_paper_is_back(self):
        """UPLOAD THE SCANS (requirement, 2026-10-11): grading set up, the assessment's own date passed and no
        paper back is one needs-you item on Collect; never "find or excuse" for the whole class, never without a grading plan (an
        assessment scored by hand), never before the date, and gone once a scan is linked or a paper is collected."""
        def needs():
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 200, response.json)
            return {item['key']: item for item in response.json['needs_you']}

        def given(date, sitting=None):
            event = self.exams.get(self.event['id'])
            if sitting is None:
                return next(s for s in self.exams.save_sitting(event['id'], {'version': event['version'], 'label': 'Main', 'kind': 'main', 'date': date})['sittings']
                            if s['label'] == 'Main')['id']
            current = next(s for s in event['sittings'] if s['id'] == sitting)
            self.exams.save_sitting(event['id'], {'version': current['version'], 'date': date}, sitting)
            return sitting

        main = given('2020-01-15')
        # Given, never scanned, but scored by hand: nothing asks for scans.
        self.assertFalse({'upload', 'collection'} & set(needs()))
        plan = self.client.post('/api/grading/assignments', json={'assignment_id': self.assignment['id'], 'term_id': self.term['id'],
                                                                  'section_ids': [self.section['id']]}, headers=HEADERS)
        self.assertIn(plan.status_code, (200, 201), plan.json)
        waiting = needs()
        self.assertEqual(waiting['upload'], {'key': 'upload', 'label': 'Upload the scans', 'count': None, 'unit': 'papers',
                                             'href': workspace.workspace_href(self.assignment['id'], self.term['id'], 'collect')})
        self.assertNotIn('collection', waiting)  # nobody is "without a scanned paper" before a paper is back
        self.assertEqual(self.listed()['needs_you'], list(waiting.values()))
        # A later makeup sitting does not put the reminder off: the assessment's own date is the main sitting's.
        event = self.exams.get(self.event['id'])
        self.exams.save_sitting(event['id'], {'version': event['version'], 'label': 'Makeup', 'kind': 'makeup', 'date': '2999-01-20'})
        self.assertIn('upload', needs())
        # Not before the date, and not on the day itself.
        for date in ('2999-01-15', workspace.lifecycle.__globals__['campus_today']()):
            given(date, main)
            self.assertNotIn('upload', needs(), date)
        given('2020-01-15', main)
        self.assertIn('upload', needs())
        # Everyone absent or excused: no paper is expected.
        for person in self.exams.get(self.event['id'])['participants']:
            self.exams.update_participant(self.event['id'], person['section_id'], person['enrollment_id'],
                                          {'version': person['version'], 'expectation': 'absent', 'reason': 'fixture'})
        self.assertNotIn('upload', needs())
        for person in self.exams.get(self.event['id'])['participants']:
            self.exams.update_participant(self.event['id'], person['section_id'], person['enrollment_id'],
                                          {'version': person['version'], 'expectation': 'expected', 'reason': ''})
        self.assertIn('upload', needs())
        # A scan linked and still in its Inbox checks: the scans are uploaded; its own check is what waits.
        self.event = self.exams.get(self.event['id'])
        batch = self.batch()
        scan = self.arrival(batch['copies'][0], [1, 2], review=False)
        self.associate(scan['message_id'])
        linked = needs()
        self.assertNotIn('upload', linked)
        self.assertTrue(any(key.startswith('scan:') for key in linked))
        # THE ZEROS ARE SAID AT CERTIFY (requirement, 2026-11-05): a paper is back and the others have none, and that asks nothing here.
        stages = {stage['key']: stage for stage in self.client.get(self.url()).json['lifecycle']['stages']}
        self.assertGreater(stages['collected']['counts']['no_scan'], 0)
        self.assertNotIn('collection', linked)

    def test_listing_reconciles_each_plan_once_and_reuses_only_this_reads_certifications(self):
        other = self.documents.create_assignment(self.term['definition_id'], 'other', 'Other assignment')
        for assignment in (self.assignment, other):
            response = self.client.post('/api/grading/assignments', json={'assignment_id': assignment['id'],
                'term_id': self.term['id'], 'section_ids': [self.section['id']]}, headers=HEADERS)
            self.assertIn(response.status_code, (200, 201), response.json)
        exams = self.app.extensions['exams']
        with patch.object(exams, '_collection', wraps=exams._collection) as collection, \
                patch.object(exams, '_participants', wraps=exams._participants) as participants, \
                patch('assignment_workspace.certified', wraps=workspace.certified) as certifications:
            first = self.client.get('/api/assignments')
            self.assertEqual(first.status_code, 200, first.json)
            self.assertEqual(len(first.json['assignments']), 2)
            self.assertEqual(collection.call_count, 2)
            self.assertEqual(participants.call_count, 2)
            self.assertEqual(certifications.call_count, 1)
            second = self.client.get('/api/assignments')
            self.assertEqual(first.json, second.json)
            self.assertEqual(collection.call_count, 4)
            self.assertEqual(participants.call_count, 4)
            self.assertEqual(certifications.call_count, 2)

    def test_unreadable_roster_keeps_catalog_collection_and_scans_available(self):
        batch = self.batch()
        scan = self.arrival(batch['copies'][0], [1, 2], review=False)
        self.associate(scan['message_id'])
        exams = self.app.extensions['exams']
        before = self.client.get(self.url()).json
        with patch.object(exams, '_participants', side_effect=ValueError('Synthetic unavailable roster')), \
                self.assertLogs('assignment_workspace', level='WARNING'):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['workflow'], {'unavailable': True})
        self.assertEqual(len(response.json['scans']), 1)
        collected = lambda value: next(s for s in value['lifecycle']['stages'] if s['key'] == 'collected')
        self.assertEqual(collected(before), collected(response.json))
        self.assertTrue(any(i['key'].startswith('scan:') for i in response.json['needs_you']))

    def test_deleted_scan_has_no_context_or_progress(self):
        batch = self.batch()
        scan = self.arrival(batch['copies'][0], [1, 2])
        message = scan['message_id']
        with connect(self.path) as db:
            job = db.execute('SELECT id FROM inbox_jobs WHERE message_id=?', (message,)).fetchone()[0]
        # Current-source association goes through the owner's checked route.
        self.associate(message)
        with connect(self.path) as db:
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,'workspace','test','2026-10-03T00:00:00+00:00')", (message,))
        resolved = self.client.get('/api/assignments/resolve?' + urlencode({'route': f'#inbox/job/{job}'}))
        self.assertIsNone(resolved.json['context'])
        detail = self.client.get(self.url())
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['scans'], [])
        self.assertEqual(detail.json['workflow']['awaiting_review'], 0)
        self.assertFalse(any(i['key'].startswith('scan:') for i in self.listed()['needs_you']))
        self.assertFalse(any(i['kind'] == 'scan_associated' for i in detail.json['activity']))

    def test_waiting_scan_is_visible_in_both_views_and_opens_the_owning_stage(self):
        batch = self.batch()
        scan = self.arrival(batch['copies'][0], [1, 2], review=False)
        self.associate(scan['message_id'])
        frq = self.app.extensions['frq_grading']
        detail = self.client.get(self.url()).json
        with frq.rosters.lock(), connect(self.path) as db:
            plan = frq.plan(db, detail['plan_id'])
            self.assertFalse(frq.grading_entry(db, plan)['ready'])
            expected = frq.workflow_summary(db, plan)
        # The owner's counts, without `export`, which no page reads of the assignment (PERFORMANCE "THE ASSIGNMENTS PAGES").
        self.assertNotIn('export', detail['workflow'])
        self.assertEqual(detail['workflow'], {k: v for k, v in expected.items() if k != 'export'})
        # ASSIGNMENT-WORKSPACE: waiting plans are absent from the legacy grading list.
        with patch.object(frq, 'overview', side_effect=AssertionError('Use every term plan, including pending scans')):
            row = self.listed()
        self.assertEqual(row['needs_you'], detail['needs_you'])
        self.assertEqual(row['workflow']['awaiting_review'], 1)
        self.assertNotIn('scans', row['workflow'])
        self.assertTrue(any(i['key'].startswith('scan:') and i['href'].endswith('/review') for i in row['needs_you']))
        self.assertIn('collection', row['filters'])

    def test_quiet_refresh_reads_no_delivery_receipts(self):
        """PERFORMANCE: `?delivery=0` (the overview's 30 s refresh) leaves the receipts out of the body and never reads them."""
        body = {'assignment_id': self.assignment['id'], 'term_id': self.term['id'], 'section_ids': [self.section['id']]}
        plan = self.client.post('/api/grading/assignments', json=body, headers=HEADERS).json
        full = self.client.get(self.url()).json
        self.assertEqual(full['delivery']['plan_id'], plan['id'])
        with patch.object(self.app.extensions['teaching_review'], 'delivery', side_effect=AssertionError('read on a quiet refresh')):
            quiet = self.client.get(self.url() + '?delivery=0')
        self.assertEqual(quiet.status_code, 200, quiet.json)
        self.assertNotIn('delivery', quiet.json)
        self.assertEqual(quiet.json, {key: value for key, value in full.items() if key != 'delivery'})

    def test_unavailable_grading_keeps_other_assignment_and_scan_work_visible(self):
        batch = self.batch()
        scan = self.arrival(batch['copies'][0], [1, 2], review=False)
        self.associate(scan['message_id'])
        other = self.documents.create_assignment(self.term['definition_id'], 'other', 'Other assignment')
        frq = self.app.extensions['frq_grading']
        with patch.object(frq, 'workflow_summary', side_effect=ValueError('Synthetic unreadable source')), self.assertLogs('assignment_workspace', level='WARNING'):
            response = self.client.get('/api/assignments')
            self.assertEqual(response.status_code, 200, response.json)
            row = next(r for r in response.json['assignments'] if r['assignment_id'] == self.assignment['id'])
            unaffected = next(r for r in response.json['assignments'] if r['assignment_id'] == other['id'])
            self.assertEqual(row['unavailable'], ['grading'])
            self.assertFalse(row['archived'])
            self.assertEqual(unaffected['unavailable'], [])
            self.assertTrue(any(i['key'].startswith('scan:') for i in row['needs_you']))
            detail = self.client.get(self.url())
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['delivery'], {'unavailable': True})
        self.assertIn('delivery', detail.json['unavailable'])
        self.assertEqual(next(s for s in detail.json['lifecycle']['stages'] if s['key'] == 'grading')['counts'], {'unavailable': 1})

    def test_activity_includes_creation_and_printing_but_not_another_terms_printing(self):
        self.batch()
        detail = self.client.get(self.url()).json
        self.assertTrue(any(a['kind'] == 'assignment_created' for a in detail['activity']))
        self.assertTrue(any(a['kind'] == 'batch_allocated' and a['label'] == 'Print job queued' for a in detail['activity']))
        self.assertLessEqual(len(detail['activity']), 12)
        response = self.client.post('/api/courses', json={'name': 'Calculus', 'code': 'MATH 140', 'term': 'Spring',
                                    'year': 2027, 'sections': [501]}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        with connect(self.path) as db:
            term = db.execute('SELECT id FROM course_terms WHERE id!=?', (self.term['id'],)).fetchone()[0]
        other = self.client.get(f"/api/assignments/{self.assignment['id']}/{term}")
        self.assertEqual(other.status_code, 200, other.json)
        self.assertFalse(any(a['kind'] == 'batch_allocated' for a in other.json['activity']))


class AssignmentWorkspaceCertificationTests(graded.GradescopeCase):
    certify = archive_fixture.ArchivedAfterCertification.certify

    def setUp(self):
        super().setUp()
        original = sources._links

        def links(db, term_id, assignment_id=None):
            result = original(db, term_id, assignment_id)
            for value in result.values():
                value['parts'] = [part for part in value['parts'] if part == 'frq']
            return result
        self.enterContext(patch('gradebook.sources._links', side_effect=links))

    def test_a_makeup_waiting_keeps_a_certified_assignment_in_view(self):
        """CERTIFIED-ARCHIVE: a makeup version waiting to be approved is work after certification, as an open regrade request is."""
        self.certify()

        def row():
            return next(r for r in self.client.get('/api/assignments').json['assignments'] if r['plan_id'] == self.aid)

        self.assertTrue(row()['archived'])
        with patch.object(self.app.extensions['frq_grading'], 'makeups_waiting', return_value={self.aid: {'letter': 'E', 'state': 'ready'}}):
            waiting = row()
        self.assertIsNone(waiting['archived'])
        self.assertEqual([i['label'] for i in waiting['needs_you'] if i['key'] == 'makeup'], ['Approve makeup version E'])
        self.assertTrue(row()['archived'])

    def test_certified_assignment_returns_for_late_scan_but_not_deleted_material(self):
        self.certify()
        with connect(self.path) as db:
            assignment, term = db.execute('''SELECT e.assignment_id,e.term_id FROM exam_events e
                JOIN frq_assignments f ON f.event_id=e.id WHERE f.id=?''', (self.aid,)).fetchone()

        def row():
            response = self.client.get('/api/assignments')
            self.assertEqual(response.status_code, 200, response.json)
            return next(r for r in response.json['assignments'] if r['assignment_id'] == assignment and r['term_id'] == term)

        before = row()
        self.assertTrue(before['archived'])
        self.assertEqual(before['needs_you'], [])
        self.assertIn('delivery', before['filters'])
        late = self.app.extensions['inbox'].submit({'action': 'scan.receive'},
            [{'filename': 'late.pdf', 'content': b'%PDF-fixture'}], source='email', sender='scanner',
            original=b'retained fixture', idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        with connect(self.path) as db:
            link = db.execute('SELECT * FROM frq_scan_links WHERE assignment_id=? ORDER BY id DESC LIMIT 1', (self.aid,)).fetchone()
            columns = [name for name in link.keys() if name != 'id']
            overrides = {'message_id': late['message_id'], 'request_id': str(uuid.uuid4()), 'created_at': '2999-01-01T00:00:00+00:00'}
            db.execute(f"INSERT INTO frq_scan_links({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       [overrides.get(name, link[name]) for name in columns])
        arriving = row()
        self.assertIsNone(arriving['archived'])
        self.assertGreater(arriving['workflow']['awaiting_review'], 0)
        with connect(self.path) as db:
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,'workspace','fixture','2999-01-02T00:00:00+00:00')", (late['message_id'],))
        restored = row()
        self.assertEqual(restored['archived'], before['archived'])
        self.assertEqual(restored['workflow'], before['workflow'])
        self.assertEqual(restored['needs_you'], [])

    def test_certify_waits_for_a_late_scan_as_certification_itself_does(self):
        """READY TO CERTIFY MEANS NOTHING IS STILL COMING (requirement, 2026-10-11): every response reviewed, then one more scan
        arrives. The overview no longer lists Certify grades or says "ready to certify" while certification's own check blocks
        on that scan; the grading row opens the scan, and both come back when the scan is gone."""
        with connect(self.path) as db:
            assignment, term = db.execute('''SELECT e.assignment_id,e.term_id FROM exam_events e
                JOIN frq_assignments f ON f.event_id=e.id WHERE f.id=?''', (self.aid,)).fetchone()

        def read():
            response = self.client.get(f'/api/assignments/{assignment}/{term}')
            self.assertEqual(response.status_code, 200, response.json)
            grading = next(s for s in response.json['lifecycle']['stages'] if s['key'] == 'grading')
            return response.json, grading, [item['key'] for item in response.json['needs_you']]

        def blocked():
            checks = self.app.extensions['certification'].view(self.aid).get('checks') or []
            return [check['id'] for check in checks if check['id'] == 'inbox_pending']

        ready, grading, needs = read()
        self.assertIn('certify', needs)
        self.assertEqual((grading['detail'], grading['href']), ('All reviewed · ready to certify', f'#grading/{self.aid}?tab=export'))
        self.assertEqual(blocked(), [])
        late = self.app.extensions['inbox'].submit({'action': 'scan.receive'},
            [{'filename': 'late.pdf', 'content': b'%PDF-late-fixture'}], source='email', sender='scanner',
            original=b'retained late fixture', idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        with connect(self.path) as db:
            link = db.execute('SELECT * FROM frq_scan_links WHERE assignment_id=? ORDER BY id DESC LIMIT 1', (self.aid,)).fetchone()
            columns = [name for name in link.keys() if name != 'id']
            overrides = {'message_id': late['message_id'], 'request_id': str(uuid.uuid4()), 'created_at': '2999-01-01T00:00:00+00:00'}
            db.execute(f"INSERT INTO frq_scan_links({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       [overrides.get(name, link[name]) for name in columns])
            job = db.execute('SELECT id FROM inbox_jobs WHERE message_id=?', (late['message_id'],)).fetchone()[0]
        arriving, grading, needs = read()
        review = arriving['workflow']['progress']['review']
        self.assertEqual(review['completed'], review['total'])  # still every response reviewed
        self.assertEqual(arriving['workflow']['awaiting_review'], 1)
        self.assertEqual(blocked(), ['inbox_pending'])
        self.assertNotIn('certify', needs)
        self.assertEqual(grading['state'], 'started')
        self.assertTrue(grading['detail'].endswith('reviewed · waiting for scan checks on 1 scan'), grading['detail'])
        self.assertEqual(grading['href'], f'#inbox/job/{job}')
        self.assertFalse(grading['counts']['certify'])
        self.assertEqual((arriving['lifecycle']['next']['title'], arriving['lifecycle']['next']['href']), ('Finish the scan checks', f'#inbox/job/{job}'))
        # The list says the same, from the same read.
        listed = next(r for r in self.client.get('/api/assignments').json['assignments'] if r['assignment_id'] == assignment and r['term_id'] == term)
        self.assertEqual(listed['needs_you'], arriving['needs_you'])
        with connect(self.path) as db:
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,'workspace','fixture','2999-01-02T00:00:00+00:00')", (late['message_id'],))
        _, grading, needs = read()
        self.assertIn('certify', needs)
        self.assertEqual(grading['detail'], 'All reviewed · ready to certify')

    def test_certify_is_listed_only_when_nothing_is_still_coming(self):
        reviewed = {'progress': {'review': {'completed': 4, 'total': 4}}, 'certification': {'state': 'none'},
                    'setup': {'rubric_quality': {'state': 'confirmed'}, 'questions': [{'rubric': {'approved_at': 'x'}}]}}
        keys = lambda **more: [item['key'] for item in workspace.work_items({**reviewed, **more}, 'p')]
        self.assertEqual(keys(), ['certify'])
        self.assertEqual(keys(awaiting_review=0, pending=0), ['certify'])
        self.assertEqual(keys(awaiting_review=1), [])
        self.assertEqual(keys(pending=2), [])
        self.assertEqual(keys(certification={'state': 'changed'}, awaiting_review=1), [])
        self.assertEqual(keys(certification={'state': 'current'}), [])

    def test_preparation_needs_you_only_when_it_waits_for_you(self):
        """2026-10-18 (Home's rule): not while the pipeline works it, and not beside the answer areas once every rubric is in."""
        def keys(quality='needed', preparation=None, checks=('confirmed',), rubric=True):
            questions = [{'rubric': {'approved_at': None} if rubric else None, 'location_check': {'status': s}} for s in checks]
            setup = {'rubric_quality': {'state': quality}, 'questions': questions, 'preparation': preparation or {}}
            return [item['key'] for item in workspace.work_items({'setup': setup}, 'p')]
        self.assertEqual(keys(), ['preparation'])
        self.assertEqual(keys(quality='running'), [])
        self.assertEqual(keys(quality='pending'), [])
        self.assertEqual(keys(preparation={'pending': 1}), [])
        self.assertEqual(keys(checks=('running', 'confirmed')), [])
        # The answer areas are the step once every question has its rubric; before that both wait for the instructor.
        self.assertEqual(keys(checks=('needs_confirmation', 'confirmed')), ['answer-areas'])
        self.assertEqual(keys(checks=('needs_confirmation',), rubric=False), ['preparation', 'answer-areas'])
        self.assertEqual(keys(preparation={'failed': 1}, checks=('needs_confirmation',)), ['preparation', 'answer-areas'])

    def test_a_finished_rubric_check_says_so(self):
        """2026-11-02: the words and address of Home's card and the badge; the key, and so the Prepare tab's mark, stay."""
        def item(quality):
            setup = {'rubric_quality': {'state': quality}, 'questions': [{'rubric': {'approved_at': None}, 'location_check': {'status': 'confirmed'}}]}
            return workspace.work_items({'setup': setup}, 'p')[0]
        self.assertEqual(item('needs_review'), {'key': 'preparation', 'label': 'Review the rubric check',
                                                'href': '#grading/p?tab=answer-key&open=quality', 'unit': 'setup', 'count': None})
        self.assertEqual(item('needed')['label'], 'Finish grading preparation')
        self.assertEqual(item('needed')['href'], '#grading/p?tab=rubrics&workspace=1')

    def test_graded_reads_are_inert_and_keep_the_owners_counts(self):
        with connect(self.path) as db:
            assignment, term = db.execute('''SELECT e.assignment_id,e.term_id FROM exam_events e
                JOIN frq_assignments f ON f.event_id=e.id WHERE f.id=?''', (self.aid,)).fetchone()
            before = hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
        with patch('socket.create_connection', side_effect=AssertionError('Projection must not reach an outside service')):
            response = self.client.get(f'/api/assignments/{assignment}/{term}')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['unavailable'], [])
        owner = self.frq.assignment(self.aid)['workflow']
        # The assignment editor augments setup with ordered question/template references;
        # the aggregate keeps the same counts and current rubric/location facts.
        # Everything but `export`, which the assignment's read leaves out (PERFORMANCE "THE ASSIGNMENTS PAGES").
        self.assertNotIn('export', response.json['workflow'])
        self.assertEqual({k: v for k, v in response.json['workflow'].items() if k != 'setup'},
                         {k: v for k, v in owner.items() if k not in ('setup', 'export')})
        self.assertEqual({q['question_id']: q for q in response.json['workflow']['setup']['questions']},
                         {q['question_id']: q for q in owner['setup']['questions']})
        self.assertEqual(response.json['delivery']['columns'], self.app.extensions['teaching_review'].delivery(self.aid)['columns'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        with connect(self.path) as db:
            self.assertEqual(before, hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest())

    def test_the_overview_carries_why_grading_waits_only_while_responses_wait(self):
        """WHY GRADING WAITS (requirement, 2026-10-11): the detail read carries the Grade step's own account of automatic grading
        (`auto_grade_state`) only while responses wait to be graded; the body is unchanged otherwise, and the list never has it."""
        with connect(self.path) as db:
            assignment, term = db.execute('''SELECT e.assignment_id,e.term_id FROM exam_events e
                JOIN frq_assignments f ON f.event_id=e.id WHERE f.id=?''', (self.aid,)).fetchone()
        url = f'/api/assignments/{assignment}/{term}'
        settled = self.client.get(url).json
        self.assertEqual(settled['workflow']['new_responses'], 0)
        self.assertNotIn('auto_grade', settled)
        summary = self.frq.workflow_summary
        waiting = lambda *args, **kwargs: {**summary(*args, **kwargs), 'new_responses': 3}
        with patch.object(self.frq, 'workflow_summary', side_effect=waiting):
            reading = self.client.get(url).json
            quiet = self.client.get(url + '?delivery=0').json
            listed = next(r for r in self.client.get('/api/assignments').json['assignments'] if r['assignment_id'] == assignment and r['term_id'] == term)
        with self.frq.rosters.lock(), connect(self.path) as db:
            owner = self.frq.auto_grade_state(db, self.aid, self.frq.settings()['settings'])
        self.assertEqual(reading['auto_grade'], owner)
        self.assertEqual(quiet['auto_grade'], owner)
        self.assertEqual((owner['enabled'], owner['not_started']), (True, None))
        self.assertNotIn('auto_grade', listed)
        self.assertEqual({k: v for k, v in reading.items() if k not in ('auto_grade', 'workflow', 'needs_you', 'archived', 'lifecycle', 'delivery')},
                         {k: v for k, v in settled.items() if k not in ('workflow', 'needs_you', 'archived', 'lifecycle', 'delivery')})
        # The owner's reasons come through as it gives them: a cancelled last batch pauses it.
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET cancel_requested=1 WHERE id=(SELECT id FROM frq_jobs WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1)", (self.aid,))
            jobs = db.execute('SELECT COUNT(*) FROM frq_jobs WHERE assignment_id=?', (self.aid,)).fetchone()[0]
        with patch.object(self.frq, 'workflow_summary', side_effect=waiting):
            paused = self.client.get(url).json['auto_grade']
        self.assertTrue(jobs)
        self.assertEqual(paused['paused'], 'cancelled')
        # A state that cannot be read says nothing; the assignment is still there.
        with patch.object(self.frq, 'workflow_summary', side_effect=waiting), patch.object(self.frq, 'auto_grade_state', side_effect=RuntimeError('fixture')), \
                self.assertLogs('assignment_workspace', level='WARNING'):
            unread = self.client.get(url)
        self.assertEqual(unread.status_code, 200, unread.json)
        self.assertNotIn('auto_grade', unread.json)


if __name__ == '__main__':
    unittest.main()
