"""Teaching Inbox protocol, queue, authorization, recovery, and physical storage."""
import base64
import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from unittest.mock import patch

from werkzeug.exceptions import Conflict, Forbidden
from app import create_app
from course_storage import StorageError, inventory
from rosters import connect
from teach_inbox.auth import EmailConfig
from teach_inbox.auth import token_hash
from email_gateway import EmailOnly
from werkzeug.test import Client
from werkzeug.wrappers import Response
from teach_inbox.handlers import HANDLERS
from teach_inbox.protocol import MAX_FILE, MAX_INTAKE_FILE, parse_command, validate_files
from test_rosters import csv_bytes
from test_storage import HEADERS, temporary_storage


class ProtocolTests(unittest.TestCase):
    def test_public_listener_never_dispatches_private_paths_or_methods(self):
        calls = []
        def downstream(environ, start_response):
            calls.append(environ['PATH_INFO'])
            start_response('202 Accepted', [('Content-Type', 'text/plain')])
            return [b'accepted']
        client = Client(EmailOnly(downstream), Response)
        for path in ('/', '/api/state', '/api/inbox/jobs', '/static/app.js',
                     '/api/documents/catalog', '/api/accommodations/options',
                     '/api/course-groups/example/sections/507/roster',
                     '/api/inbox/email/', '/api/inbox/email?x=1', '/api/inbox/email/../jobs'):
            self.assertEqual(client.post(path).status_code, 404)
        for method in ('GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'):
            self.assertEqual(client.open('/api/inbox/email', method=method).status_code, 404)
        self.assertEqual(calls, [])
        self.assertEqual(client.post('/api/inbox/email').status_code, 202)
        self.assertEqual(calls, ['/api/inbox/email'])

    def test_safe_yaml_types_duplicates_aliases_and_object_tags(self):
        self.assertEqual(parse_command('action: quiz.ingest\nquiz: 7\nversion: B\npublish: false')['publish'], False)
        for value in ('action: quiz.ingest\naction: grades.publish', 'x: &x [*x]', '!!python/object/apply:os.system ["false"]', 'x: 2026-09-13', 'x: .nan', 'x: ' + '[' * 1000 + ']' * 1000):
            with self.subTest(value=value[:50]), self.assertRaises(ValueError):
                parse_command(value)


class InboxTestCase(unittest.TestCase):
    managed = False

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name) if self.managed else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.inbox = self.app.extensions['inbox']
        self.course = self.add_course()
        self.targets = self.inbox.catalog()['targets']
        self.section = next(target for target in self.targets if target['level'] == 'section')
        self.term = next(target for target in self.targets if target['level'] == 'term')
        self.definition = next(target for target in self.targets if target['level'] == 'course')

    def add_course(self, **extra):
        result = self.client.post('/api/courses', json={'name':'Calculus', 'code':'MATH 140', 'term':'Fall', 'year':2026, 'sections':[501], 'is_current':True, **extra}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        if self.storage:
            self.storage.reconcile_all()
        return result.json

    def command(self, action='document.archive', target=None):
        target = target or self.term
        return {'action':action, 'target':{'level':target['level'], 'id':target['id']}, 'metadata':{}}

    def submit(self, command=None, filename='notes.txt', content=b'Original document', **kw):
        return self.inbox.submit(command or self.command(), [{'filename':filename, 'content':content}], **kw)

    def ready(self):
        if self.storage:
            self.storage.reconcile_all()

    def change_course(self, course, **extra):
        current = next(c for c in self.client.get('/api/state').json['courses'] if c['id'] == course['id'])
        result = self.client.patch('/api/courses/'+course['id'], json={'version':current['version'], **extra}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.ready()


class InboxTests(InboxTestCase):
    def test_intake_is_queued_preserves_bytes_and_registry_dispatches(self):
        job = self.submit()
        self.assertEqual(job['status'], 'pending')
        self.assertEqual(job['attempts'], 0)
        original_name, original = self.inbox.read_original(job['message_id'])
        self.assertIn(b'document.archive', original)
        self.assertEqual(self.inbox.read_artifact(job['artifacts'][0]['id'])[1], b'Original document')
        self.assertTrue(self.inbox.process_one())
        finished = self.inbox.get(job['id'])
        self.assertEqual((finished['status'],finished['attempts']), ('completed',1))
        self.assertEqual(len(finished['artifacts']),2)
        self.assertEqual([event['kind'] for event in finished['events']], ['received','pending','running','completed'])
        self.assertIn('software_version', finished)
        self.assertFalse(self.inbox.process_one())

    def test_roster_handler_uses_existing_student_ids_and_section_owner(self):
        self.client.get('/').close()
        command = self.command('roster.sync', self.section)
        self.submit(command, 'roster.csv', csv_bytes())
        group = self.client.get('/api/state').json['course_groups'][0]
        route = f"/api/course-groups/{group['id']}/sections/501/roster"
        self.assertEqual(self.client.get(route).json['count'],0)
        self.inbox.process_one()
        first = self.client.get(route).json
        self.assertEqual(first['count'],2)
        later = self.submit(command, 'roster.csv', csv_bytes())
        self.inbox.process_one()
        self.assertEqual(self.client.get(route).json['students'], first['students'])
        self.assertEqual(self.inbox.get(later['id'])['result']['updated'],2)

    def test_two_workers_cannot_claim_or_recover_each_others_job(self):
        job = self.submit()
        with self.inbox.worker_lock(), ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(StorageError):
                pool.submit(self.inbox.process_one).result(timeout=5)
        self.assertEqual(self.inbox.get(job['id'])['status'], 'pending')
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['attempts'], 1)

    def test_course_resources_respect_parent_term_and_section_boundaries(self):
        spring = self.add_course(term='Spring')
        other = self.add_course(code='MATH 240')
        self.add_course(sections=[502])
        targets = self.inbox.catalog()['targets']
        second_section = next(item for item in targets if item['label']=='MATH 140 · Fall 2026 · Section 502')
        for name, target in [('shared.txt', self.definition), ('fall.txt', self.term), ('501.txt', self.section), ('502.txt', second_section)]:
            self.submit(self.command(target=target), filename=name)
            self.inbox.process_one()
        # Unprocessed future actions must not appear as completed course documents.
        self.submit(self.command('quiz.ingest'), filename='pending.txt')
        groups = self.client.get('/api/state').json['course_groups']
        fall = next(g for g in groups if g['term']=='Fall' and g['code']=='MATH 140')
        spring = next(g for g in groups if g['term']=='Spring')
        other = next(g for g in groups if g['code']=='MATH 240')
        def names(group, section=501):
            response = self.client.get(f"/api/inbox/course-groups/{group['id']}/resources?section={section}")
            self.assertEqual(response.status_code, 200, response.json)
            return {key:[row['filename'] for row in rows] for key,rows in response.json.items()}
        self.assertEqual(names(fall), {'course':['shared.txt'], 'term':['fall.txt'], 'section':['501.txt']})
        self.assertEqual(names(fall,502)['section'], ['502.txt'])
        self.assertEqual(names(spring), {'course':['shared.txt'], 'term':[], 'section':[]})
        self.assertEqual(names(other), {'course':[], 'term':[], 'section':[]})

    def test_deleted_documents_leave_resources_and_cannot_be_requeued(self):
        # INBOX: removed material never counts as completed work or proceeds until explicitly restored.
        job = self.submit()
        self.inbox.process_one()
        group = self.client.get('/api/state').json['course_groups'][0]
        resources = lambda: self.inbox.resources(group['id'])['term']
        self.assertEqual(len(resources()), 1)
        current = self.inbox.get(job['id'])
        self.inbox.remove(job['id'], current['version'], 'delete')
        self.assertEqual(resources(), [])
        # Use a cancelled job to exercise the path that would otherwise be eligible for Retry.
        pending = self.submit(filename='second.txt')
        cancelled = self.inbox.transition(pending['id'], 'cancel', pending['version'])
        self.inbox.remove(cancelled['id'], cancelled['version'], 'delete')
        with self.assertRaisesRegex(Conflict, 'Restore'):
            self.inbox.transition(cancelled['id'], 'retry', cancelled['version'])
        self.inbox.restore(job['id'])
        self.assertEqual(len(resources()), 1)

    def test_course_resources_name_the_rubric_a_document_is_the_source_of(self):
        rubric = self.submit(self.command(target=self.definition), filename='3.csv', content=b'rubric 3')
        self.inbox.process_one()
        self.submit(self.command(target=self.definition), filename='syllabus.txt', content=b'syllabus')
        self.inbox.process_one()
        documents = self.app.extensions['documents']
        definition = documents.catalog()['definitions'][0]['id']
        assignment = documents.create_assignment(definition, 'test-1', 'Test 1')
        version = documents.register_stamp_pages(assignment['id'], 1)
        now = '2026-09-26T00:00:00+00:00'
        with connect(self.inbox.path) as db:
            artifact = db.execute("SELECT id FROM inbox_artifacts WHERE job_id=? AND role='input'", (rubric['id'],)).fetchone()[0]
            before = db.execute('SELECT COUNT(*) FROM frq_rubric_revisions').fetchone()[0]
            db.execute("INSERT INTO frq_questions(id,assignment_version_id,question_key,created_at,exam_version) VALUES('q3',?,'3',?,'B')", (version['id'], now))
            db.execute("INSERT INTO frq_question_revisions VALUES('qr3','q3',1,?,'x',?)", ('{"title": "Question 3"}', now))
            db.execute("INSERT INTO frq_rubric_revisions VALUES('r3','q3',1,'',?,'[]','x','x','{}',?)", (artifact, now))
        group = next(g for g in self.client.get('/api/state').json['course_groups'] if g['code'] == 'MATH 140')
        rows = self.client.get(f"/api/inbox/course-groups/{group['id']}/resources").json['course']
        cited = next(row for row in rows if row['filename'] == '3.csv')
        plain = next(row for row in rows if row['filename'] == 'syllabus.txt')
        self.assertEqual((cited['assignment_title'], cited['exam_version'], cited['question']), ('Test 1', 'B', 'Question 3'))
        self.assertEqual(cited['assignment_id'], assignment['id'])
        self.assertNotIn('assignment_title', plain)
        with connect(self.inbox.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_revisions').fetchone()[0], before + 1)

    def test_idempotency_conflicts_and_unsupported_jobs_are_retained(self):
        first = self.submit(idempotency_key='one')
        self.assertEqual(self.submit(idempotency_key='one')['id'], first['id'])
        with self.assertRaises(Conflict):
            self.submit(content=b'changed', idempotency_key='one')
        waiting = self.submit(self.command('quiz.ingest'))
        self.assertEqual(waiting['status'],'needs_review')
        self.assertFalse(waiting['handler_available'])
        self.assertIn('not been implemented',waiting['error'])
        self.ready()
        corrected = self.inbox.transition(waiting['id'],'review',waiting['version'], self.command())
        self.assertEqual(corrected['status'],'pending')
        self.assertEqual(self.inbox.read_original(waiting['message_id'])[1].decode().count('quiz.ingest'),1)
        self.inbox.process_one()
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(waiting['id'])['status'],'completed')

    def test_ambiguous_subject_context_and_invalid_metadata_wait_for_review(self):
        self.add_course(term='Spring')
        job = self.submit({'action':'roster.sync','course':'MATH140-501'},'roster.csv',csv_bytes())
        self.assertEqual(job['status'],'needs_review')
        self.assertIn('ambiguous',job['error'])
        corrected = self.inbox.transition(job['id'],'review',job['version'],self.command('roster.sync', self.section))
        self.assertEqual(corrected['status'],'pending')
        invalid = self.submit({'action':{'unexpected':'object'},'metadata':'bad'})
        self.assertEqual(invalid['status'],'needs_review')
        self.assertEqual(invalid['action'],'unclassified')
        self.assertTrue(self.inbox.listing()['jobs'])

    def test_cancellation_keeps_original_and_optimistic_versions(self):
        job = self.submit()
        cancelled = self.inbox.transition(job['id'],'cancel',job['version'])
        self.assertEqual(cancelled['status'],'cancelled')
        self.assertFalse(self.inbox.process_one())
        self.assertEqual(self.inbox.read_artifact(job['artifacts'][0]['id'])[1],b'Original document')
        with self.assertRaises(Conflict):
            self.inbox.transition(job['id'],'retry',job['version'])
        self.ready()
        self.assertEqual(self.inbox.transition(job['id'],'retry',cancelled['version'])['status'],'pending')

    def test_crash_after_roster_write_requires_retry_and_does_not_apply_twice(self):
        job = self.submit(self.command('roster.sync',self.section),'roster.csv',csv_bytes())
        receipt = self.inbox._receipt
        def crash(job, result=None):
            if result is not None:
                raise SystemExit('simulated process exit after roster import')
            return receipt(job)
        with patch.object(self.inbox,'_receipt',side_effect=crash), self.assertRaises(SystemExit):
            self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'],'running')
        self.inbox.process_one()
        interrupted = self.inbox.get(job['id'])
        self.assertEqual(interrupted['status'],'needs_review')
        self.inbox.transition(job['id'],'retry',interrupted['version'])
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'],'completed')
        owner, section = self.app.extensions['rosters'].resolve(self.client.get('/api/state').json['course_groups'][0]['id'],501)
        with self.app.extensions['rosters'].lock(), self.app.extensions['rosters'].files(owner) as path:
            imports = json.loads((path/'roster.json').read_text())['sections'][section['id']]['imports']
        self.assertEqual(len(imports),1)

    def test_result_receipt_survives_crash_before_queue_completion(self):
        job = self.submit()
        with patch.object(self.inbox,'_finish',side_effect=SystemExit('exit after durable result')), self.assertRaises(SystemExit):
            self.inbox.process_one()
        self.inbox.process_one()
        interrupted = self.inbox.get(job['id'])
        self.inbox.transition(job['id'],'retry',interrupted['version'])
        def must_not_run(*args):
            raise AssertionError('Receipt should prevent repeating the handler')
        with patch.dict(HANDLERS,{'document.archive':must_not_run}):
            self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'],'completed')

    def test_consequential_action_requires_separate_exact_command_approval(self):
        calls = []
        def publish(*args):
            calls.append(True)
            return {'message':'Test-only processor ran.'}
        with patch.dict(HANDLERS, {'grades.publish':publish}):
            job = self.submit(self.command('grades.publish',self.section))
            self.assertEqual(job['status'],'needs_review')
            self.assertFalse(self.inbox.process_one())
            self.ready()
            with self.assertRaises(Forbidden):
                self.inbox.transition(job['id'],'approve',job['version'],actor='api-key')
            approved = self.inbox.transition(job['id'],'approve',job['version'])
            self.assertEqual(approved['status'],'pending')
            self.ready()
            changed = self.command('grades.publish',self.section)
            changed['metadata'] = {'note':'Changed command'}
            changed = self.inbox.transition(job['id'],'review',approved['version'],changed)
            self.assertEqual(changed['status'],'needs_review')
            self.assertIsNone(changed['approved_hash'])
            self.ready()
            self.inbox.transition(job['id'],'approve',changed['version'])
            self.inbox.process_one()
            self.assertEqual(len(calls),1)

    def test_web_api_and_one_time_opaque_codes(self):
        response = self.client.post('/api/inbox/submit',json={'command':self.command()},headers={})
        self.assertEqual(response.status_code,403)
        key = self.inbox.create_api_key('Test script')
        payload = {'command':self.command(), 'attachments':[{'filename':'x.txt','content_base64':base64.b64encode(b'abc').decode()}]}
        response = self.client.post('/api/inbox/submit',json=payload,headers={'Authorization':'Bearer '+key['token']})
        self.assertEqual(response.status_code,202,response.json)
        self.assertEqual(response.json['source'],'api')
        created = self.client.post('/api/inbox/codes',json={'command':self.command(),'label':'Documents'},headers=HEADERS)
        self.assertEqual(created.status_code,201,created.json)
        code = created.json
        self.assertTrue(code['token'].startswith('ti:'))
        self.assertNotIn('MATH',code['token'])
        self.assertIn('<svg',code['qr_svg'])
        headers={'X-Teaching-Submission-Code':code['token'],'Idempotency-Key':'code-once'}
        data={'attachments':payload['attachments']}
        first=self.client.post('/api/inbox/submit',json=data,headers=headers)
        self.assertEqual(first.status_code,202,first.json)
        self.assertEqual(set(first.json),{'id','status','received_at'})
        again=self.client.post('/api/inbox/submit',json=data,headers=headers)
        self.assertEqual(again.json['id'],first.json['id'])
        headers['Idempotency-Key']='second'
        self.assertEqual(self.client.post('/api/inbox/submit',json=data,headers=headers).status_code,409)
        self.client.post('/api/inbox/access/keys/'+key['id']+'/revoke',json={},headers=HEADERS)
        self.assertEqual(self.client.post('/api/inbox/submit',json=payload,headers={'Authorization':'Bearer '+key['token']}).status_code,403)
        with connect(self.inbox.path) as db:
            self.assertNotEqual(db.execute('SELECT token_hash FROM inbox_codes WHERE id=?',(code['id'],)).fetchone()[0],code['token'])

    def test_workspace_uploads_a_section_scan_that_api_keys_and_codes_cannot(self):
        # Past the ordinary 10 MB attachment limit, and random so no other test's scan shares its content key.
        content = b'%PDF-1.4\n' + os.urandom(11 * 1024 * 1024)
        upload = lambda body=content, name='Section 501.pdf', headers=HEADERS: self.client.post(
            '/api/inbox/scans', data={'file': (io.BytesIO(body), name)}, content_type='multipart/form-data', headers=headers)
        first = upload()
        self.assertEqual(first.status_code, 202, first.json)
        job = first.json
        self.assertEqual((job['action'], job['source'], job['status'], job['instructor_review_required'], job['existing']),
                         ('scan.receive', 'web', 'needs_review', True, False))
        self.assertEqual(self.inbox.read_artifact(job['artifacts'][0]['id'])[1], content)
        with connect(self.inbox.path) as db:
            self.assertEqual(db.execute('SELECT status FROM document_decode_jobs WHERE message_id=?', (job['message_id'],)).fetchone()[0], 'pending')
        # Sending the same file again retains nothing new and says so; the same bytes under another name are refused.
        again = upload()
        self.assertEqual((again.status_code, again.json['id'], again.json['existing']), (202, job['id'], True))
        renamed = upload(name='copy.pdf')
        self.assertEqual(renamed.status_code, 409)
        self.assertIn('already in the Inbox under another file name', renamed.json['error'])
        # Only the instructor workspace: no API key, no submission code, no cross-site or unmarked write, one PDF per request.
        key = self.inbox.create_api_key('Scanner script')
        self.assertEqual(upload(headers={'Authorization': 'Bearer ' + key['token']}).status_code, 403)
        code = self.client.post('/api/inbox/codes', json={'command': self.command(), 'label': 'Documents'}, headers=HEADERS).json
        self.assertEqual(upload(headers={'X-Teaching-Submission-Code': code['token']}).status_code, 403)
        self.assertEqual(upload(headers={}).status_code, 403)
        self.assertEqual(upload(body=b'PK not a pdf').status_code, 400)
        self.assertEqual(upload(body=content[:5000] + b'\x00' * 70000).status_code, 400)
        two = self.client.post('/api/inbox/scans', data={'file': [(io.BytesIO(content), 'a.pdf'), (io.BytesIO(content), 'b.pdf')]},
                               content_type='multipart/form-data', headers=HEADERS)
        self.assertIn(two.status_code, (400, 413))
        self.assertEqual(self.client.post('/api/inbox/scans', json={}, headers=HEADERS).status_code, 415)
        self.assertEqual(len([row for row in self.inbox.listing(None, 0)['jobs'] if row['action'] == 'scan.receive']), 1)

    def test_scanner_folder_retains_settled_pdfs_and_sets_aside_what_it_refuses(self):
        # SCANNER-INTAKE "SCANNER FOLDER": a network scanner saves into its subfolder; once a PDF stops changing and is
        # complete, the Inbox retains it like an upload (review required) and the folder's copy goes.
        from pathlib import Path
        from teach_inbox.scan_folder import ScanFolder
        now = [1000.0]
        root = Path(self.directory.name) / 'scanner-folder'
        (root / 'brother').mkdir(parents=True)
        folder = ScanFolder(self.inbox, root, settle=10, look_every=0, clock=lambda: now[0])
        self.assertIs(self.inbox.scan_folder.__class__, ScanFolder)  # the app wires one for the worker
        content = b'%PDF-1.4\n' + os.urandom(4096) + b'\n%%EOF\n'
        pdf = root / 'brother' / 'scan_001.pdf'
        pdf.write_bytes(content[:2000])  # still arriving over the network
        self.assertFalse(folder.process_one())
        now[0] += 11
        pdf.write_bytes(content)  # grew: the wait starts again
        self.assertFalse(folder.process_one())
        now[0] += 5
        self.assertFalse(folder.process_one())
        now[0] += 6
        self.assertTrue(folder.process_one())
        self.assertFalse(pdf.exists())
        jobs = [row for row in self.inbox.listing(None, 0)['jobs'] if row['action'] == 'scan.receive']
        self.assertEqual(len(jobs), 1)
        job = self.inbox.get(jobs[0]['id'])
        self.assertEqual((job['source'], job['status'], job['instructor_review_required']), ('cli', 'needs_review', True))
        self.assertEqual(self.inbox.get_by_message(job['message_id'])['artifacts'][0]['filename'], 'scan_001.pdf')
        self.assertEqual(self.inbox.read_artifact(job['artifacts'][0]['id'])[1], content)
        with connect(self.inbox.path) as db:
            self.assertEqual(db.execute('SELECT sender FROM inbox_messages WHERE id=?', (job['message_id'],)).fetchone()[0],
                             'scanner-folder/brother')
        # The same scan saved again (under any name) adds nothing; the copy still leaves the folder.
        (root / 'brother' / 'again.pdf').write_bytes(content)
        folder.process_one(); now[0] += 11
        self.assertTrue(folder.process_one())
        self.assertEqual(len([row for row in self.inbox.listing(None, 0)['jobs'] if row['action'] == 'scan.receive']), 1)
        self.assertEqual(list((root / 'brother').iterdir()), [])
        # A picture instead of a PDF, or a PDF with no ending left for half an hour, moves aside with the reason.
        (root / 'photo.jpg').write_bytes(b'\xff\xd8\xff' + os.urandom(100))
        (root / 'brother' / 'cut.pdf').write_bytes(b'%PDF-1.4\n' + os.urandom(3000))
        (root / '.hidden.pdf').write_bytes(content)
        folder.process_one(); now[0] += 11
        self.assertTrue(folder.process_one())   # the picture
        self.assertFalse(folder.process_one())  # the cut PDF waits for its ending
        now[0] += 31 * 60
        self.assertTrue(folder.process_one())
        refused = sorted(path.name for path in (root / 'not-accepted').iterdir())
        self.assertEqual(refused, ['brother-cut.pdf', 'brother-cut.pdf.txt', 'photo.jpg', 'photo.jpg.txt'])
        self.assertIn('not a PDF', (root / 'not-accepted' / 'photo.jpg.txt').read_text())
        self.assertIn('incomplete', (root / 'not-accepted' / 'brother-cut.pdf.txt').read_text())
        self.assertTrue((root / '.hidden.pdf').exists())
        self.assertFalse(folder.process_one())
        self.assertEqual(len([row for row in self.inbox.listing(None, 0)['jobs'] if row['action'] == 'scan.receive']), 1)

    def test_scanner_keeps_a_new_file_saved_while_the_previous_scan_is_retained(self):
        # SCANNER-INTAKE: a receipt for the old bytes never authorizes deleting the new arrival.
        from pathlib import Path
        from teach_inbox.scan_folder import ScanFolder
        root = Path(self.directory.name) / 'scanner-folder'
        root.mkdir()
        path = root / 'scan.pdf'
        first = b'%PDF-1.4\nfirst scan\n%%EOF\n'
        second = b'%PDF-1.4\nsecond scan\n%%EOF\n'
        path.write_bytes(first)
        submit = self.inbox.submit

        def replacing_submit(*args, **kwargs):
            result = submit(*args, **kwargs)
            path.write_bytes(second)
            return result

        folder = ScanFolder(self.inbox, root)
        with patch.object(self.inbox, 'submit', side_effect=replacing_submit):
            self.assertTrue(folder.retain(path))
        self.assertEqual(path.read_bytes(), second)
        self.assertTrue(folder.retain(path))
        self.assertFalse(path.exists())
        jobs = [job for job in self.inbox.listing(None, 0)['jobs'] if job['action'] == 'scan.receive']
        self.assertEqual(len(jobs), 2)
        retained = {self.inbox.read_artifact(self.inbox.get(job['id'])['artifacts'][0]['id'])[1] for job in jobs}
        self.assertEqual(retained, {first, second})

    def test_scanner_waits_when_file_changes_during_read(self):
        from pathlib import Path
        from teach_inbox.scan_folder import ScanFolder
        root = Path(self.directory.name) / 'scanner-folder'
        root.mkdir()
        path = root / 'scan.pdf'
        path.write_bytes(b'%PDF-1.4\nfirst scan\n%%EOF\n')
        original = Path.read_bytes

        def changing_read(current):
            content = original(current)
            current.write_bytes(content + b'new data')
            return content

        with patch.object(Path, 'read_bytes', changing_read), patch.object(self.inbox, 'submit') as submit:
            self.assertFalse(ScanFolder(self.inbox, root).retain(path))
        submit.assert_not_called()
        self.assertTrue(path.exists())

    def test_scans_combine_into_one_new_scan_and_the_originals_move_to_deleted(self):
        upload = lambda body, name: self.client.post('/api/inbox/scans', data={'file': (io.BytesIO(body), name)},
                                                      content_type='multipart/form-data', headers=HEADERS).json
        first_bytes, second_bytes = b'%PDF-1.4\n' + os.urandom(4096), b'%PDF-1.4\n' + os.urandom(2048)
        first, second = upload(first_bytes, 'doc000487.pdf'), upload(second_bytes, 'doc000488.pdf')
        combine = lambda ids, request='combine-1', confirmation='combine', headers=HEADERS: self.client.post(
            '/api/inbox/scans/combine', json={'job_ids': ids, 'request_id': request, 'confirmation': confirmation}, headers=headers)
        scans = lambda: [job for job in self.inbox.listing(None, 0)['jobs'] if job['action'] == 'scan.receive']
        # Scans the worker is still inspecting wait, and nothing is made.
        self.assertEqual(combine([first['id'], second['id']]).status_code, 409)
        self.assertEqual(len(scans()), 2)
        with connect(self.inbox.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id IN (?,?)", (first['message_id'], second['message_id']))
        # One scan, no confirmation, an API key, or a job that is not a scan: refused.
        self.assertEqual(combine([first['id']]).status_code, 400)
        self.assertEqual(combine([first['id'], second['id']], confirmation='yes').status_code, 400)
        key = self.inbox.create_api_key('Scanner script')
        self.assertEqual(combine([first['id'], second['id']], headers={'Authorization': 'Bearer ' + key['token']}).status_code, 403)
        self.assertEqual(combine([first['id'], self.submit()['id']]).status_code, 400)
        self.assertEqual(len(scans()), 2)
        made = combine([second['id'], first['id']])
        self.assertEqual(made.status_code, 201, made.json)
        job = made.json
        # Every PDF unchanged, each its own file.
        inputs = {item['filename']: item for item in job['artifacts'] if item['role'] == 'input'}
        self.assertEqual({name: self.inbox.read_artifact(item['id'])[1] for name, item in inputs.items()},
                         {'doc000487.pdf': first_bytes, 'doc000488.pdf': second_bytes})
        self.assertEqual((job['action'], job['source'], job['instructor_review_required']), ('scan.receive', 'web', True))
        with connect(self.inbox.path) as db:
            self.assertEqual(db.execute('SELECT status FROM document_decode_jobs WHERE message_id=?', (job['message_id'],)).fetchone()[0], 'pending')
        event = next(item for item in self.inbox.get(job['id'])['events'] if item['kind'] == 'scans_combined')
        self.assertEqual([source['job_id'] for source in json.loads(event['detail'])['sources']], [first['id'], second['id']])
        for source in (first, second):
            self.assertIn(job['id'], self.inbox.get(source['id'])['deleted']['reason'])
        self.assertEqual([item['id'] for item in scans()], [job['id']])
        # The same request again finishes the same combination; the originals, now deleted, cannot be combined anew.
        again = combine([second['id'], first['id']])
        self.assertEqual((again.status_code, again.json['id']), (201, job['id']))
        self.assertEqual(len(scans()), 1)
        self.assertEqual(combine([first['id'], second['id']], request='combine-2').status_code, 409)
        # An original restored comes back as it was.
        self.inbox.restore(first['id'])
        self.assertIsNone(self.inbox.get(first['id'])['deleted'])
        self.assertEqual(len(scans()), 2)

    def test_a_scan_started_over_is_a_new_scan_of_the_same_files_and_the_old_one_moves_to_deleted(self):
        # 
        body = b'%PDF-1.4\n' + os.urandom(4096)
        scan = self.client.post('/api/inbox/scans', data={'file': (io.BytesIO(body), 'doc000487.pdf')}, content_type='multipart/form-data', headers=HEADERS).json
        again = lambda request='restart-1', confirmation='start over', job=scan['id'], headers=HEADERS, **extra: self.client.post(
            f'/api/inbox/jobs/{job}/start-over', json={'request_id': request, 'confirmation': confirmation, **extra}, headers=headers)
        scans = lambda: [job for job in self.inbox.listing(None, 0)['jobs'] if job['action'] == 'scan.receive']
        # Still being inspected, unconfirmed, from an API key, or not a scan: refused, and nothing is made.
        self.assertEqual(again().status_code, 409)
        with connect(self.inbox.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (scan['message_id'],))
        self.assertEqual(again(confirmation='yes').status_code, 400)
        self.assertEqual(again(keep='yes').status_code, 400)
        key = self.inbox.create_api_key('Scanner script')
        self.assertEqual(again(headers={'Authorization': 'Bearer ' + key['token']}).status_code, 403)
        self.assertEqual(again(job=self.submit()['id']).status_code, 400)
        self.assertEqual([item['id'] for item in scans()], [scan['id']])
        made = again()
        self.assertEqual(made.status_code, 201, made.json)
        job = made.json
        # The same file, byte for byte, inspected afresh and needing review; the old scan is in Deleted, naming the new one.
        inputs = [item for item in job['artifacts'] if item['role'] == 'input']
        self.assertEqual([(item['filename'], self.inbox.read_artifact(item['id'])[1]) for item in inputs], [('doc000487.pdf', body)])
        self.assertNotEqual(job['id'], scan['id'])
        self.assertEqual((job['action'], job['instructor_review_required']), ('scan.receive', True))
        with connect(self.inbox.path) as db:
            self.assertEqual(db.execute('SELECT status FROM document_decode_jobs WHERE message_id=?', (job['message_id'],)).fetchone()[0], 'pending')
        self.assertIn(job['id'], self.inbox.get(scan['id'])['deleted']['reason'])
        event = next(item for item in self.inbox.get(job['id'])['events'] if item['kind'] == 'scan_started_over')
        detail = json.loads(event['detail'])
        self.assertEqual((detail['source']['job_id'], detail['keep'], detail['carry']), (scan['id'], True, {}))
        self.assertEqual([item['id'] for item in scans()], [job['id']])
        # The same request again finishes the same start-over; the old scan, now deleted, cannot be started over anew.
        repeat = again()
        self.assertEqual((repeat.status_code, repeat.json['id']), (201, job['id']))
        self.assertEqual(again(request='restart-2').status_code, 409)
        # Restored, the old scan comes back as it was, beside the new one.
        self.inbox.restore(scan['id'])
        self.assertEqual({item['id'] for item in scans()}, {scan['id'], job['id']})

    def test_email_is_disabled_then_checks_signature_sender_secret_and_replays(self):
        self.assertEqual(self.client.post('/api/inbox/email',json={},headers=HEADERS).status_code,503)
        self.inbox.email=EmailConfig('s'*32,'c'*32,{'teacher@example.test'},{'teach+docs@example.test':'document.archive'})
        mail=EmailMessage()
        mail['From']='teacher@example.test';mail['To']='teach+docs@example.test';mail['Subject']='MATH140-501'
        mail.set_content('---\naction: document.archive\nterm: Fall\nyear: 2026\ncommand_token: '+ 'c'*32 +'\n---\nDo not interpret this body as commands.')
        mail.add_attachment(b'hello',maintype='application',subtype='octet-stream',filename='doc.txt')
        def send(raw, delivery='one', valid=True, timestamp=None):
            content=json.dumps({'raw_email_base64':base64.b64encode(raw).decode(),'recipient':'teach+docs@example.test'}).encode()
            stamp=str(timestamp or int(time.time()))
            signed=hmac.new(b's'*32,stamp.encode()+b'.'+delivery.encode()+b'.'+content,hashlib.sha256).hexdigest()
            return self.client.post('/api/inbox/email',data=content,content_type='application/json',headers={'X-Teaching-Timestamp':stamp,'X-Teaching-Delivery':delivery,'X-Teaching-Signature':signed if valid else 'wrong'})
        raw=mail.as_bytes()
        self.assertEqual(send(raw,valid=False).status_code,403)
        self.assertEqual(send(raw,timestamp=int(time.time())-600).status_code,403)
        good=send(raw)
        self.assertEqual(good.status_code,202,good.json)
        self.assertEqual(good.json['status'],'pending')
        self.assertEqual(send(raw).json['id'],good.json['id'])
        self.assertEqual(self.inbox.read_original(self.inbox.get(good.json['id'])['message_id'])[1],raw)
        spoofed=raw.replace(b'c'*32,b'wrong')
        bad=send(spoofed,'two')
        self.assertEqual(bad.status_code,202)
        rejected=self.inbox.get(bad.json['id'])
        self.assertFalse(rejected['authenticated'])
        with self.assertRaises(Forbidden):
            self.inbox.transition(rejected['id'],'approve',rejected['version'])
        self.assertEqual(send(spoofed,'one').status_code,409)
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(good.json['id'])['status'],'completed')


    def scanner(self, body='', sender='scanner@example.test', envelope_sender=None, recipient=None, delivery='scan-one', version=2, review_only=False,
                attachment=b'%PDF-1.4 synthetic scan'):
        destination = 'scan@example.test' if review_only else 'scan-opaque-capability@inbox.example.test'
        recipient = recipient or destination
        self.inbox.email = EmailConfig(webhook_secret='s'*32, intake_routes={
            token_hash(destination): {
                'label': 'Department scanner', 'senders': ['scanner@example.test'], 'review_only': review_only}})
        mail = EmailMessage()
        mail['From'] = sender
        mail['To'] = recipient
        mail['Subject'] = 'grades.publish MATH140-501'
        mail.set_content(body)
        mail.add_attachment(attachment, maintype='application', subtype='pdf', filename='scan.pdf')
        raw = mail.as_bytes()
        value = {'raw_email_base64': base64.b64encode(raw).decode(), 'recipient': recipient}
        if version == 2:
            value.update(schema_version=2, sender=envelope_sender or sender, received_at='2026-09-13T12:00:00Z')
        content = json.dumps(value).encode()
        stamp = str(int(time.time()))
        signed = hmac.new(b's'*32, stamp.encode()+b'.'+delivery.encode()+b'.'+content, hashlib.sha256).hexdigest()
        response = self.client.post('/api/inbox/email', data=content, content_type='application/json', headers={
            'X-Teaching-Timestamp':stamp, 'X-Teaching-Delivery':delivery, 'X-Teaching-Signature':signed})
        return response, raw, content

    def test_scanner_empty_body_retains_reviewable_scan_and_exact_receipt(self):
        response, raw, content = self.scanner()
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(response.json['body_sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(response.json['delivery_id'], 'scan-one')
        job = self.inbox.get(response.json['id'])
        self.assertTrue(job['authenticated'])
        self.assertEqual((job['action'],job['status']), ('scan.receive','needs_review'))
        self.assertFalse(self.inbox.process_one())
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)
        self.assertEqual(self.inbox.read_artifact(job['artifacts'][0]['id'])[1], b'%PDF-1.4 synthetic scan')
        self.assertEqual(len([e for e in job['events'] if e['kind'] == 'gateway']), 1)
        self.ready()
        reviewed = self.inbox.transition(job['id'], 'review', job['version'], self.command())
        self.assertEqual(reviewed['status'], 'pending')
        self.ready()
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'], 'completed')

    def test_scanner_body_cannot_select_action_or_bypass_separate_approval(self):
        response, _, _ = self.scanner('---\naction: grades.publish\ncommand_token: ignored\n---')
        job = self.inbox.get(response.json['id'])
        self.assertEqual(job['parameters'], {'action':'scan.receive','metadata':{}})
        with self.assertRaises(ValueError):
            self.inbox.transition(job['id'], 'approve', job['version'])

    def test_scanner_checks_both_senders_alias_and_envelope_version(self):
        for index, options in enumerate(({'sender':'attacker@example.test'},
                {'envelope_sender':'attacker@example.test'}, {'version':1},
                {'recipient':'scan-guessed@inbox.example.test'})):
            with self.subTest(options=options):
                response, _, _ = self.scanner(delivery=f'bad-scan-{index}', **options)
                self.assertEqual(response.status_code, 202)
                job = self.inbox.get(response.json['id'])
                self.assertFalse(job['authenticated'])
                # A guessed address is no route at all, so nothing checked a sender; the other three failed the sender check.
                self.assertEqual(job['error'], "The envelope recipient has no configured Teaching Inbox route. This email could not be read, "
                                 "so this submission cannot be approved or executed." if 'recipient' in options
                                 else "Sender authentication failed. This submission cannot be approved or executed.")
                with self.assertRaises(Forbidden):
                    self.inbox.transition(job['id'], 'review', job['version'], self.command())

    def test_a_scanner_pdf_past_10_mb_is_kept_up_to_the_gateways_25_mb(self):
        # SCANNER-INTAKE "A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL": the B&W copier's 278- and 284-page scans of
        # 2026-11-14 were 10.2 and 11.1 MiB and were refused as "Sender authentication failed". Random, so no other scan shares it.
        content = b'%PDF-1.4\n' + os.urandom(11 * 1024 * 1024)
        response, raw, _ = self.scanner(review_only=True, attachment=content, delivery='big-scan')
        self.assertEqual(response.status_code, 202, response.json)
        job = self.inbox.get(response.json['id'])
        self.assertTrue(job['authenticated'])
        self.assertEqual((job['action'], job['status'], job['instructor_review_required']), ('scan.receive', 'needs_review', True))
        self.assertEqual(self.inbox.read_artifact(job['artifacts'][0]['id'])[1], content)
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)
        # The intake limit is the gateway's own 25 MiB (MAX_RAW in cloudflare/teach-inbox); authored commands keep 10 MB.
        self.assertEqual(MAX_INTAKE_FILE, 25 * 1024 * 1024)
        validate_files([{'filename': 'scan.pdf', 'content': b'x' * MAX_INTAKE_FILE}], max_file=MAX_INTAKE_FILE)
        with self.assertRaisesRegex(ValueError, '25 MB'):
            validate_files([{'filename': 'scan.pdf', 'content': b'x' * (MAX_INTAKE_FILE + 1)}], max_file=MAX_INTAKE_FILE)
        with self.assertRaisesRegex(ValueError, r'^Each attachment must be nonempty and at most 10 MB\.$'):
            validate_files([{'filename': 'doc.txt', 'content': b'x' * (MAX_FILE + 1)}])

    def test_an_email_that_cannot_be_read_says_why_and_never_blames_the_sender(self):
        # Unreadable scanner email: two senders. It is kept with its original, unauthenticated, and says what refused it.
        response, raw, _ = self.scanner(review_only=True, sender='scanner@example.test, other@example.test', envelope_sender='scanner@example.test',
                                        delivery='two-senders')
        self.assertEqual(response.status_code, 202, response.json)
        job = self.inbox.get(response.json['id'])
        self.assertFalse(job['authenticated'])
        self.assertEqual(job['error'], "An email must contain exactly one sender. This email could not be read, so this submission cannot be approved or executed.")
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)
        with self.assertRaises(Forbidden):
            self.inbox.transition(job['id'], 'review', job['version'], self.command())
        # An authored command keeps the 10 MB attachment limit, from an allowed sender with the right secret: refused, kept, and why.
        self.inbox.email = EmailConfig('s'*32, 'c'*32, {'teacher@example.test'}, {'teach+docs@example.test': 'document.archive'})
        mail = EmailMessage()
        mail['From'] = 'teacher@example.test'; mail['To'] = 'teach+docs@example.test'; mail['Subject'] = 'MATH140-501'
        mail.set_content('---\naction: document.archive\nterm: Fall\nyear: 2026\ncommand_token: ' + 'c'*32 + '\n---')
        mail.add_attachment(os.urandom(MAX_FILE + 1), maintype='application', subtype='octet-stream', filename='notes.bin')
        raw = mail.as_bytes()
        content = json.dumps({'raw_email_base64': base64.b64encode(raw).decode(), 'recipient': 'teach+docs@example.test'}).encode()
        stamp = str(int(time.time()))
        signed = hmac.new(b's'*32, stamp.encode()+b'.too-large.'+content, hashlib.sha256).hexdigest()
        response = self.client.post('/api/inbox/email', data=content, content_type='application/json', headers={
            'X-Teaching-Timestamp': stamp, 'X-Teaching-Delivery': 'too-large', 'X-Teaching-Signature': signed})
        self.assertEqual(response.status_code, 202, response.json)
        job = self.inbox.get(response.json['id'])
        self.assertFalse(job['authenticated'])
        self.assertEqual(job['artifacts'], [])
        self.assertEqual(job['error'], "Each attachment must be nonempty and at most 10 MB. This email could not be read, so this submission cannot be approved or executed.")
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)
        with self.assertRaises(Forbidden):
            self.inbox.transition(job['id'], 'approve', job['version'])

    def test_short_scanner_address_requires_workspace_review_and_retains_original(self):
        response, raw, content = self.scanner(review_only=True)
        self.assertEqual(response.status_code, 202)
        job = self.inbox.get(response.json['id'])
        self.assertTrue(job['instructor_review_required'])
        self.assertEqual(job['status'], 'needs_review')
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)
        self.assertFalse(self.inbox.process_one())
        self.ready()
        for actor, operation in [('api-key','review'), ('api-key','retry'), ('workspace','retry'), ('workspace','approve')]:
            with self.subTest(actor=actor, operation=operation), self.assertRaises(Forbidden):
                self.inbox.transition(job['id'], operation, job['version'], self.command(), actor)
        reviewed = self.inbox.transition(job['id'], 'review', job['version'], self.command())
        self.assertFalse(reviewed['instructor_review_required'])
        self.assertEqual(reviewed['status'], 'pending')
        self.ready()
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'], 'completed')
        stamp = str(int(time.time()))
        signed = hmac.new(b's'*32, stamp.encode()+b'.scan-one.'+content, hashlib.sha256).hexdigest()
        duplicate = self.client.post('/api/inbox/email', data=content, content_type='application/json', headers={
            'X-Teaching-Timestamp':stamp, 'X-Teaching-Delivery':'scan-one', 'X-Teaching-Signature':signed})
        self.assertEqual(duplicate.status_code, 202, duplicate.json)
        self.assertEqual(duplicate.json['id'], job['id'])
        self.assertEqual(duplicate.json['status'], 'completed')

    def test_short_scan_gate_is_rechecked_by_worker(self):
        response, _, _ = self.scanner(review_only=True)
        identifier = response.json['id']
        self.ready()
        # Simulate a future classifier attempting to queue a public-alias scan.
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET status='pending',action='document.archive',parameters_json=? WHERE id=?",
                       (json.dumps(self.command()), identifier))
        with patch.dict(HANDLERS, {'document.archive': lambda *args: self.fail('unreviewed scan executed')}):
            self.inbox.process_one()
        job = self.inbox.get(identifier)
        self.assertEqual(job['status'], 'needs_review')
        self.assertIn('instructor workspace', job['error'])
        self.assertTrue(job['instructor_review_required'])
        self.assertEqual(job['attempts'], 0)
        reviewed = self.inbox.transition(identifier, 'review', job['version'], self.command())
        self.assertEqual(reviewed['status'], 'pending')


class InboxSearchTests(InboxTestCase):
    def search(self, query='', **parameters):
        response = self.client.get('/api/inbox/jobs', query_string={'q': query, **parameters})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_search_matches_display_fields_and_requires_every_term(self):
        job = self.submit(self.command(target=self.section), filename='Résumé Straße.txt',
                          source='email', sender='Élodie@example.test')
        self.submit(self.command('quiz.ingest'), filename='other.txt', source='cli', sender='another')
        for query in (job['id'].upper(), 'DOCUMENT.ARCHIVE', 'documents', '501', 'FALL 2026 501',
                      'ÉLODIE', 'EMAIL', 'RÉSUMÉ STRASSE', '  documents\t501\nélodie  '):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual([item['id'] for item in result['jobs']], [job['id']])
                self.assertEqual(result['total'], 1)
        self.assertEqual(self.search('résumé another')['jobs'], [])
        self.assertEqual(self.search('documents nonexistent')['total'], 0)
        self.assertEqual(self.search('   \t\n')['total'], 2)

    def test_status_counts_cover_all_matches_but_total_uses_selected_status(self):
        completed = self.submit(filename='lecture-completed.txt')
        self.inbox.process_one()
        pending = self.submit(filename='lecture-pending.txt')
        review = self.submit(self.command('quiz.ingest'), filename='lecture-review.txt')
        cancelled = self.submit(filename='lecture-cancelled.txt')
        self.inbox.transition(cancelled['id'], 'cancel', cancelled['version'])
        self.submit(filename='unrelated.txt')
        counts = {'completed': 1, 'pending': 1, 'needs_review': 1, 'cancelled': 1}
        for status, job in (('completed', completed), ('pending', pending), ('needs_review', review), ('cancelled', cancelled)):
            with self.subTest(status=status):
                result = self.search('lecture', status=status)
                self.assertEqual(result['counts'], counts)
                self.assertEqual(result['total'], 1)
                self.assertEqual([item['id'] for item in result['jobs']], [job['id']])
        self.assertEqual(self.search('lecture')['total'], 4)
        history = self.search('lecture', status='history')
        self.assertEqual({item['id'] for item in history['jobs']}, {completed['id'], cancelled['id']})
        self.assertEqual((history['total'], history['counts']), (2, counts))
        self.inbox.remove(completed['id'], self.inbox.get(completed['id'])['version'], 'delete')
        self.assertEqual([item['id'] for item in self.search('lecture', status='history')['jobs']], [cancelled['id']])
        self.inbox.restore(completed['id'])
        empty = self.search('lecture', status='failed')
        self.assertEqual((empty['total'], empty['jobs'], empty['counts']), (0, [], counts))
        self.assertEqual(self.search(status='pending')['total'], 2)

    def test_current_lists_every_unfinished_submission_with_what_needs_you_first(self):
        completed = self.submit(filename='done.txt')
        self.inbox.process_one()
        failed, running, pending = (self.submit(filename=f'{name}.txt') for name in ('failed', 'running', 'pending'))
        review = self.submit(self.command('quiz.ingest'), filename='review.txt')
        self.assertEqual(review['status'], 'needs_review')
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET status='failed',error='Simulated failure' WHERE id=?", (failed['id'],))
            db.execute("UPDATE inbox_jobs SET status='running' WHERE id=?", (running['id'],))
        # Failed, then needs review, then running, then queued; finished work stays in History.
        current = self.search(status='current')
        self.assertEqual([job['id'] for job in current['jobs']], [failed['id'], review['id'], running['id'], pending['id']])
        self.assertEqual((current['total'], current['counts']), (4, {'completed': 1, 'failed': 1, 'needs_review': 1, 'running': 1, 'pending': 1}))
        self.assertEqual(current['jobs'][0]['error'], 'Simulated failure')
        self.assertNotIn(completed['id'], [job['id'] for job in current['jobs']])
        # The former tab filters still answer for scripts and Home.
        for status, job in (('failed', failed), ('needs_review', review), ('running', running), ('pending', pending)):
            with self.subTest(status=status):
                self.assertEqual([item['id'] for item in self.search(status=status)['jobs']], [job['id']])
        self.assertEqual([item['id'] for item in self.search('pending', status='current')['jobs']], [pending['id']])

    def test_submissions_are_listed_apart_from_scans_unfinished_first(self):
        # INBOX "SCANS AND SUBMISSIONS" (2026-10-18): kind=command lists every other submission, unfinished first in Current's
        # order then the rest newest first; kind=scan holds none of them; each kind's counts come with it; needs keeps only
        # what waits for the instructor. A list without kind is unchanged.
        completed = self.submit(filename='done.txt')
        self.inbox.process_one()
        failed, pending = (self.submit(filename=f'{name}.txt') for name in ('failed', 'pending'))
        review = self.submit(self.command('quiz.ingest'), filename='review.txt')
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET status='failed',error='Simulated failure' WHERE id=?", (failed['id'],))
        commands = self.inbox.listing(kind='command')
        self.assertEqual([job['id'] for job in commands['jobs']], [failed['id'], review['id'], pending['id'], completed['id']])
        self.assertEqual(commands['counts'], {'completed': 1, 'failed': 1, 'needs_review': 1, 'pending': 1})
        self.assertEqual(commands['kind_counts'], {'scan': {}, 'command': commands['counts']})
        self.assertEqual(commands['assignments'], [])
        scans = self.inbox.listing('current', kind='scan')
        self.assertEqual((scans['jobs'], scans['counts'], scans['total']), ([], {}, 0))
        self.assertEqual(scans['kind_counts']['command'], commands['counts'])
        self.assertEqual([job['id'] for job in self.inbox.listing('current', kind='command', needs=True)['jobs']], [review['id']])
        self.assertEqual(len(self.inbox.listing('current', kind='all')['jobs']), 3)
        plain = self.inbox.listing('current')
        self.assertFalse({'kind_counts', 'assignments'} & set(plain))
        with self.assertRaises(ValueError):
            self.inbox.listing(kind='paper')

    def test_a_job_read_on_the_callers_connection_leaves_it_open_and_the_list_reads_no_events(self):
        job = self.inbox.submit(self.command(), [{'filename': 'a.txt', 'content': b'alpha'}, {'filename': 'b.txt', 'content': b'beta'}])
        full = self.inbox.get(job['id'])
        self.assertTrue(full['events'])
        with connect(self.inbox.path) as db:
            read = self.inbox.get(job['id'], db=db, events=False, roles='input')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox_jobs').fetchone()[0], 1)  # still the caller's, still open
        self.assertEqual(read, {key: value for key, value in full.items() if key != 'events'})
        listed = self.search()['jobs'][0]
        self.assertEqual(self.search(files='input')['jobs'][0], listed)
        self.assertEqual(listed, {**read, 'listed_as': listed.get('listed_as')} if 'listed_as' in listed else read)

    def test_search_filters_full_history_before_pagination_without_duplicate_artifacts(self):
        first = self.inbox.submit(self.command(), [
            {'filename': 'history-alpha.txt', 'content': b'alpha'},
            {'filename': 'history-beta.txt', 'content': b'beta'},
        ])
        matching = {first['id']}
        matching.update(self.submit(filename=f'history-{index}.txt')['id'] for index in range(54))
        newest = self.submit(filename='unrelated.txt')
        self.assertEqual(self.search()['total'], 56)
        self.assertIn(newest['id'], {job['id'] for job in self.search()['jobs']})
        page_one, page_two = self.search('history'), self.search('history', offset=50)
        self.assertEqual((len(page_one['jobs']), page_one['has_more'], page_one['total']), (50, True, 55))
        self.assertEqual((len(page_two['jobs']), page_two['has_more'], page_two['offset'], page_two['total']), (5, False, 50, 55))
        one, two = ({job['id'] for job in page['jobs']} for page in (page_one, page_two))
        self.assertFalse(one & two)
        self.assertEqual(one | two, matching)
        self.assertEqual(self.search('alpha beta')['total'], 1)
        self.assertEqual(self.search(first['id'])['jobs'][0]['id'], first['id'])
        self.assertEqual(self.search('history', offset=100)['jobs'], [])

    def test_search_treats_wildcards_and_sql_as_literal_text_and_bounds_query(self):
        percent = self.submit(filename='100%complete.txt')
        underscore = self.submit(filename='under_score.txt')
        quote = self.submit(filename="literal'quote.txt")
        self.submit(filename='ordinary.txt')
        for query, expected in (('%', percent), ('_', underscore), ("'", quote)):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual([job['id'] for job in result['jobs']], [expected['id']])
        for query in ("' OR 1=1 --", "'; DROP TABLE inbox_jobs; --", 'not%present', 'not_present'):
            with self.subTest(query=query):
                self.assertEqual(self.search(query)['total'], 0)
        self.assertEqual(self.search()['total'], 4)
        self.assertEqual(self.search('x' * 200)['jobs'], [])
        oversized = self.client.get('/api/inbox/jobs', query_string={'q': 'x' * 201})
        self.assertEqual(oversized.status_code, 400, oversized.json)
        self.assertIn('200 characters', oversized.json['error'])
        with self.assertRaises(ValueError):
            self.inbox.listing(query='x' * 201)

    def test_search_does_not_read_payloads_or_match_private_metadata(self):
        command = self.command()
        command['metadata'] = {'private': 'metadataneedle'}
        code = self.inbox.create_code(command, 'Code label')
        job = self.submit(command, content=b'payloadneedle', original=b'originalneedle', submission_code=code['token'])
        with connect(self.inbox.path) as db:
            db.execute("UPDATE inbox_jobs SET result_json=?,approved_hash=? WHERE id=?",
                       (json.dumps({'message': 'resultneedle'}), 'approvalneedle', job['id']))
            self.inbox.event(db, job['id'], 'review', 'auditneedle')
        with patch.object(self.inbox, 'files', side_effect=AssertionError('Search opened retained payloads')):
            self.assertEqual(self.search('notes.txt')['jobs'][0]['id'], job['id'])
            for query in ('metadataneedle', 'payloadneedle', 'originalneedle', 'resultneedle',
                          'approvalneedle', 'auditneedle', code['token'], token_hash(code['token'])):
                with self.subTest(query=query):
                    result = self.search(query)
                    self.assertEqual((result['jobs'], result['total'], result['counts']), ([], 0, {}))


class ManagedInboxTests(InboxTests):
    managed = True

    def test_unchanged_messages_keep_their_verified_snapshot_through_their_final_purge(self):
        job = self.submit()
        self.inbox.process_one()
        raw = self.inbox.read_original(job['message_id'])[1]
        manager = self.storage.inbox_storage
        self.storage.backup_all()
        recorded = manager.row(job['message_id'])['storage_snapshot']
        again = self.storage.backup_all()
        self.assertEqual((again['status'], again['unchanged']), ('complete', again['backed_up']))
        self.assertEqual(manager.row(job['message_id'])['storage_snapshot'], recorded)
        self.change_course(self.course, is_current=False)
        self.storage.backup_all()
        archived = manager.row(job['message_id'])
        self.assertEqual(archived['storage_status'], 'archived')
        self.assertNotEqual(archived['storage_snapshot'], recorded)
        # Restored to NVMe for a write that changed nothing: its verified final copy is kept and NVMe is purged again.
        with self.app.extensions['rosters'].lock(), manager.course_files(job['message_id'], write=True):
            pass
        self.assertEqual(manager.row(job['message_id'])['storage_status'], 'staged')
        result = self.storage.backup_all()
        final = manager.row(job['message_id'])
        self.assertEqual((final['storage_status'], final['storage_snapshot']), ('archived', archived['storage_snapshot']))
        self.assertGreaterEqual(result['unchanged'], 1)
        self.assertFalse(manager.hot_path(job['message_id']).exists())
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)

    def test_concurrent_intake_and_reconciliation_write_only_nvme_until_nightly_backup(self):
        before = inventory(self.storage.hdd)
        def receive(index):
            return self.inbox.submit({}, [{'filename':f'scan-{index}.txt','content':str(index).encode()}],
                source='email', original=f'Original email {index}'.encode(), problem='Awaiting review')
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = list(pool.map(receive, range(12)))
        self.assertEqual(self.storage.database, self.storage.nvme / 'catalog' / 'faculty.sqlite3')
        self.assertEqual(self.storage.reconcile_all(), [])
        self.assertFalse(self.inbox.process_one())
        self.assertEqual(inventory(self.storage.hdd), before)
        manager = self.storage.inbox_storage
        for job in jobs:
            row = manager.row(job['message_id'])
            self.assertEqual(row['storage_status'], 'staged')
            self.assertEqual(row['storage_snapshot'], '')
            self.assertTrue(manager.hot_path(row['id']).exists())
        result = self.storage.backup_all()
        self.assertEqual(result['status'], 'complete')
        for index, job in enumerate(jobs):
            row = manager.row(job['message_id'])
            self.assertEqual(row['storage_status'], 'archived')
            manager.verify_snapshot(row['id'], row['storage_snapshot'])
            self.assertFalse(manager.hot_path(row['id']).exists())
            self.assertEqual(self.inbox.read_original(row['id'])[1], f'Original email {index}'.encode())
        with connect(self.storage.hdd / 'workspace-backups' / result['catalog_snapshot'] / 'faculty.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM inbox_messages').fetchone()[0], len(jobs))

    def test_staged_mail_can_join_current_course_before_its_first_backup(self):
        response, raw, _ = self.scanner()
        job = self.inbox.get(response.json['id'])
        before = inventory(self.storage.hdd)
        self.inbox.transition(job['id'], 'review', job['version'], self.command())
        self.ready()
        self.inbox.process_one()
        manager = self.storage.inbox_storage
        self.assertEqual(inventory(self.storage.hdd), before)
        self.assertEqual(manager.row(job['message_id'])['storage_status'], 'active')
        self.assertEqual(self.inbox.read_original(job['message_id'])[1], raw)
        self.storage.backup_all()
        self.assertTrue(manager.hot_path(job['message_id']).exists())

    def test_archived_inbox_review_and_results_stage_on_nvme_until_next_backup(self):
        self.change_course(self.course, is_current=False)
        job = self.submit(self.command('quiz.ingest'))
        self.storage.backup_all()
        manager = self.storage.inbox_storage
        archived = manager.row(job['message_id'])
        self.assertEqual(archived['storage_status'], 'archived')
        before = inventory(self.storage.hdd)
        self.inbox.transition(job['id'], 'review', job['version'], self.command())
        self.ready()
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'], 'completed')
        self.assertEqual(inventory(self.storage.hdd), before)
        staged = manager.row(job['message_id'])
        self.assertEqual(staged['storage_status'], 'staged')
        self.assertEqual(staged['storage_snapshot'], archived['storage_snapshot'])
        self.storage.backup_all()
        final = manager.row(job['message_id'])
        self.assertNotEqual(final['storage_snapshot'], archived['storage_snapshot'])
        self.assertFalse(manager.hot_path(job['message_id']).exists())
        self.assertEqual(len(self.inbox.get(job['id'])['artifacts']), 2)

    def test_missing_staged_original_cannot_fall_back_to_an_older_snapshot(self):
        job = self.submit()
        self.storage.backup_all()
        manager = self.storage.inbox_storage
        hot = manager.hot_path(job['message_id'])
        hot.rename(hot.with_name(hot.name + '-missing'))
        manager.update(job['message_id'], storage_status='archive_pending')
        with self.assertRaisesRegex(StorageError, 'prior backup'):
            manager.reconcile_course(job['message_id'])
        self.assertEqual(manager.row(job['message_id'])['storage_status'], 'archive_pending')

    def test_corrupted_original_blocks_execution_and_final_purge(self):
        job = self.submit()
        self.ready()
        manager = self.storage.inbox_storage
        original = manager.hot_path(job['message_id']) / 'data' / 'original.json'
        retained = original.read_bytes()
        original.write_bytes(b'Corrupted original')
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(job['id'])['status'], 'failed')
        with self.assertRaises(StorageError):
            self.inbox.read_original(job['message_id'])
        current = next(c for c in self.client.get('/api/state').json['courses'] if c['id']==self.course['id'])
        response = self.client.patch('/api/courses/'+current['id'], json={'version':current['version'], 'is_current':False}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.storage.reconcile_all()
        with self.assertLogs('course_storage',level='ERROR'):
            with self.assertRaises(StorageError):
                self.storage.backup_all()
        self.assertTrue(original.exists())
        original.write_bytes(retained)
        self.assertEqual(self.storage.backup_all()['status'], 'complete')
        self.assertFalse(original.exists())

    def test_course_wide_originals_stay_hot_until_last_current_term_and_restore(self):
        spring = self.add_course(term='Spring')
        job=self.submit(self.command(target=self.definition))
        self.inbox.process_one()
        manager=self.storage.inbox_storage
        hot=manager.hot_path(job['message_id'])
        self.assertTrue(hot.is_dir())
        raw=self.inbox.read_original(job['message_id'])[1]
        self.assertEqual(self.storage.backup_all()['status'],'complete')
        self.change_course(self.course,is_current=False)
        self.assertTrue(hot.exists())
        self.change_course(spring,is_current=False)
        self.assertTrue(hot.exists())
        self.assertEqual(manager.row(job['message_id'])['storage_status'], 'staged')
        self.storage.backup_all()
        self.assertFalse(hot.exists())
        archived=manager.row(job['message_id'])
        manager.verify_snapshot(job['message_id'],archived['storage_snapshot'])
        self.assertEqual(self.inbox.read_original(job['message_id'])[1],raw)
        self.change_course(spring,is_current=True)
        self.assertTrue(hot.exists())
        self.assertEqual(self.inbox.read_original(job['message_id'])[1],raw)

    def test_failed_final_copy_keeps_originals_on_nvme(self):
        job=self.submit()
        self.inbox.process_one()
        manager=self.storage.inbox_storage
        current=next(row for row in self.client.get('/api/state').json['courses'] if row['id']==self.course['id'])
        result=self.client.patch('/api/courses/'+self.course['id'],json={'version':current['version'],'is_current':False},headers=HEADERS)
        self.assertEqual(result.status_code,200,result.json)
        self.storage.reconcile_all()
        with patch.object(manager,'verify_snapshot',side_effect=StorageError('test checksum failure')), self.assertLogs('course_storage',level='ERROR'):
            with self.assertRaises(StorageError):
                self.storage.backup_all()
        self.assertTrue(manager.hot_path(job['message_id']).exists())
        self.storage.backup_all()
        self.assertFalse(manager.hot_path(job['message_id']).exists())

    def test_references_prevent_detaching_scoped_artifacts(self):
        job=self.submit()
        self.ready()
        response=self.client.patch('/api/courses/'+self.course['id'],json={'version':self.course['version'],'year':2027},headers=HEADERS)
        self.assertEqual(response.status_code,409,response.json)
        self.change_course(self.course,name='Updated name')
        self.assertEqual(self.inbox.get(job['id'])['parameters']['target']['id'],self.term['id'])


if __name__=='__main__':
    unittest.main()
