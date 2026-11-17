"""ASSISTANT AUTOMATION VISIBILITY (2026-10-09 review): what Faculty did by itself, real outcomes of the built-in rows, Student
triage's observed counts and Home's "Assistant changes waiting", all read from synthetic catalog rows (no outward call)."""
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from assistant.automation import changes_waiting, outcomes
from rosters import connect
from test_assistant import settle
from test_storage import HEADERS
import test_exam_workflow as fixture

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(hours=1)).isoformat()
EARLIER = (NOW - timedelta(minutes=30)).isoformat()
OLD = (NOW - timedelta(days=9)).isoformat()
FYI = {'kind': {'choice': 'fyi', 'confidence': .99, 'probabilities': {'fyi': .99}}, 'needs_action': {'noul': .01}, 'wellbeing': {'noul': .01}}
REQUEST = {'kind': {'choice': 'request', 'confidence': .99, 'probabilities': {'request': .99}}, 'needs_action': {'noul': .9}, 'wellbeing': {'noul': .01}}


class AutomationVisibilityTests(unittest.TestCase):
    import_students = fixture.ExamWorkflowTests.import_students

    def setUp(self):
        fixture.ExamWorkflowTests.setUp(self)
        self.service = self.app.extensions['assistant']
        self.plan = str(uuid.uuid4())
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('INSERT INTO frq_assignments(id,event_id,version,created_at,updated_at) VALUES(?,?,1,?,?)',
                       (self.plan, self.event['id'], OLD, OLD))

    def write(self, sql, values):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute(sql, values)

    def certify(self, actor='automatic', rows=('s:e',), at=RECENT):
        ident = str(uuid.uuid4())
        with self.rosters.lock(), connect(self.path) as db:
            latest = db.execute('SELECT id FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1', (self.plan,)).fetchone()
            db.execute('''INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,
                bubble_sha256,participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,
                supersedes_id,actor,created_at) VALUES(?,?,?,'x','x','r','[]','x','x','x','x',?,'[]','{}','{}','{}',?,?,?)''',
                       (ident, self.plan, ident, json.dumps({'automatic': {'rows': list(rows)}}), latest[0] if latest else None, actor, at))
        return ident

    def automatic_send(self, certification, status, error='', at=RECENT, kind='written', who=None):
        who = who or self.alice
        self.write('''INSERT INTO frq_gradescope_automatic_sends(id,certification_id,assignment_id,section_id,enrollment_id,kind,status,send_id,
            error,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'automatic',?,?)''',
                   (str(uuid.uuid4()), certification, self.plan, who['section_id'], who['enrollment_id'], kind, status,
                    'send-1' if status == 'recorded' else None, error, at, at))

    def creation(self, letter='E', actor='automatic', status='complete', at=RECENT):
        self.write('''INSERT INTO frq_gradescope_creations(id,assignment_id,request_id,request_sha256,fingerprint,title,plan_json,status,actor,
            created_at,updated_at) VALUES(?,?,?,'x','x','Exam 1',?,?,?,?,?)''',
                   (str(uuid.uuid4()), self.plan, str(uuid.uuid4()), json.dumps({'automatic': {'letter': letter}} if actor == 'automatic' else {}),
                    status, actor, at, at))

    def frq_event(self, kind, detail, at=RECENT):
        self.write('INSERT INTO frq_events(assignment_id,kind,detail_json,actor,created_at) VALUES(?,?,?,?,?)',
                   (self.plan, kind, json.dumps(detail), 'automatic', at))

    def action(self, tier='auto', kind='succeeded', at=RECENT, endpoint='calendar.create', body=None):
        ident = str(uuid.uuid4())
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('''INSERT INTO assistant_actions(id,session_id,engine,tier,endpoint,method,path,body_json,summary,created_at)
                VALUES(?,'s','test',?,?,'POST','/api/x',?,'Example, Alice: a change',?)''', (ident, tier, endpoint, json.dumps(body or {}), at))
            db.execute("INSERT INTO assistant_action_events(action_id,kind,actor,detail_json,created_at) VALUES(?,'proposed','assistant','{}',?)", (ident, at))
            if kind != 'proposed':
                db.execute("INSERT INTO assistant_action_events(action_id,kind,actor,detail_json,created_at) VALUES(?,?,'faculty','{}',?)", (ident, kind, at))
        return ident

    def automations(self):
        view = self.client.get('/api/assistant/automations')
        self.assertEqual(view.status_code, 200, view.json)
        return view.json

    def row(self, view, ident):
        return next(r for r in view['automations'] if r['id'] == ident)

    def test_done_by_faculty_lists_each_source_with_its_owning_page_and_no_names(self):
        certification = self.certify(rows=('a', 'b'))
        self.certify(actor='workspace')  # the instructor's own certification is not Faculty's
        self.automatic_send(certification, 'recorded')
        self.creation()
        self.creation(actor='workspace')
        self.write("""INSERT INTO gradebook_canvas_nightly_runs(id,term_id,night,kind,status,detail_json,created_at)
            VALUES(?,?,?,'sync','unchanged','{}',?)""", (str(uuid.uuid4()), self.term['id'], RECENT[:10], RECENT))
        self.write("""INSERT INTO gradebook_canvas_nightly_runs(id,term_id,night,kind,status,detail_json,created_at)
            VALUES(?,?,?,'sync','failed','{}',?)""", (str(uuid.uuid4()), self.term['id'], OLD[:10], OLD))
        self.action()
        self.action()
        self.action(tier='approve', kind='proposed')  # waiting for the instructor: not done by Faculty
        self.action(at=OLD)
        done = self.automations()['done']
        texts = [line['text'] for line in done['lines']]
        self.assertIn('Certified 2 late papers · Exam 1', texts)
        self.assertIn('Sent 1 late paper to Gradescope · Exam 1', texts)
        self.assertIn('Added makeup Version E on Gradescope · Exam 1', texts)
        self.assertIn('Morning Canvas sync · MATH 140 · Fall 2026: nothing had changed', texts)
        self.assertIn('The Assistant made 2 changes by itself', texts)
        self.assertEqual(len(texts), 5, texts)
        hrefs = {line['text']: line.get('href') or line.get('tab') for line in done['lines']}
        self.assertEqual(hrefs['Certified 2 late papers · Exam 1'], f'#grading/{self.plan}?tab=export')
        self.assertEqual(hrefs['Sent 1 late paper to Gradescope · Exam 1'], f'#grading/{self.plan}?tab=export&open=gradescope')
        self.assertEqual(hrefs['The Assistant made 2 changes by itself'], 'changes')
        self.assertTrue(hrefs['Morning Canvas sync · MATH 140 · Fall 2026: nothing had changed'].startswith('#gradebook/'))
        self.assertNotIn('Alice', json.dumps(done))
        self.assertEqual(done['days'], 7)

    def test_a_term_named_other_shows_its_own_name_on_the_rows_scope(self):
        # A term saved as "Other" carries its own name (`term_other`); the Automation rows label it as the Done lines do.
        self.write("UPDATE course_terms SET term='Other',term_other='Summer Special',is_current=1 WHERE id=?", (self.term['id'],))
        self.write("""INSERT INTO gradebook_canvas_nightly_runs(id,term_id,night,kind,status,detail_json,created_at)
            VALUES(?,?,?,'sync','unchanged','{}',?)""", (str(uuid.uuid4()), self.term['id'], RECENT[:10], RECENT))
        view = self.automations()
        observed = self.row(view, 'canvas-morning')['observations']
        self.assertEqual([o['scope'] for o in observed], ['MATH 140 \u00b7 Summer Special 2026'])
        self.assertIn('Morning Canvas sync \u00b7 MATH 140 \u00b7 Summer Special 2026: nothing had changed', [line['text'] for line in view['done']['lines']])

    def test_a_quiet_week_adds_no_done_key(self):
        self.action(at=OLD)
        self.assertNotIn('done', self.automations())

    def test_late_paper_row_reports_its_outcome_what_waits_and_what_was_not_sent(self):
        view = self.automations()
        self.assertEqual(self.row(view, 'auto-certify')['last_outcome'], 'No late paper certified by itself yet')
        self.certify(actor='workspace', at=OLD)
        certification = self.certify(rows=('a',))
        self.frq_event('automatic_certification_not_made', {'reason': '', 'students': [{'row': 'x', 'why': 'w'}, {'row': 'y', 'why': 'w'}]})
        self.automatic_send(certification, 'skipped', "This section's Gradescope assignment is not linked.")
        self.automatic_send(certification, 'skipped', 'Already on Gradescope.', kind='sheets')
        bob = next(p for p in self.event['participants'] if p['name'] == 'Example, Bob')
        self.automatic_send(certification, 'pending', 'Waiting: Gradescope is busy', who=bob)
        row = self.row(self.automations(), 'auto-certify')
        self.assertEqual(row['last_outcome'], 'Certified 1 late paper · Exam 1')
        self.assertEqual(row['last_checked_at'], RECENT)
        self.assertEqual(row['waiting'], '2 late papers wait to certify · 1 late paper waits to go to Gradescope')
        self.assertEqual(row['blocker'], "1 late paper not sent to Gradescope by itself: This section's Gradescope assignment is not linked.")
        self.assertEqual(row['href'], f'#grading/{self.plan}?tab=export&open=gradescope')

    def test_makeup_row_follows_gradescope_setup_and_says_why_a_version_was_not_added(self):
        row = self.row(self.automations(), 'makeup-version-gradescope')
        self.assertFalse(row['enabled'])
        self.assertTrue(row['blocker'])
        self.assertEqual(row['href'], '#grading')
        gradescope = self.app.extensions['gradescope']
        with patch.object(gradescope.settings, 'status', return_value={'configured': True, 'problem': ''}):
            self.assertEqual(self.row(self.automations(), 'makeup-version-gradescope')['blocker'], '')
            self.frq_event('gradescope_version_not_added', {'letter': 'E', 'reasons': ['Section 501: not linked']}, at=EARLIER)
            row = self.row(self.automations(), 'makeup-version-gradescope')
            self.assertTrue(row['enabled'])
            self.assertEqual(row['blocker'], 'Faculty could not add makeup Version E of Exam 1 by itself: Section 501: not linked')
            self.assertEqual(row['href'], f'#grading/{self.plan}?tab=export&open=gradescope')
            self.creation(at=(NOW - timedelta(minutes=5)).isoformat())  # a later creation added it
            row = self.row(self.automations(), 'makeup-version-gradescope')
            self.assertEqual(row['blocker'], '')
            self.assertEqual(row['last_outcome'], 'Added makeup Version E · Exam 1')

    def test_triage_row_names_its_provider_and_counts_what_you_did_after_fyi(self):
        enrollment = self.alice['enrollment_id']
        for source, answers in (('gmail:one', FYI), ('gmail:two', FYI), ('gmail:three', REQUEST)):
            self.write('''INSERT INTO jev_calls(id,call_key,purpose,owner_id,enrollment_id,input_sha256,questions_json,model,rule,status,result_json,created_at)
                VALUES(?,?,'student-triage',?,?,'x','{}','jev-1.13.0','student-triage-v2','complete',?,?)''',
                       (str(uuid.uuid4()), str(uuid.uuid4()), enrollment + ':' + source, enrollment, json.dumps({'answers': answers}), EARLIER))
        # The instructor tracked the first conversation after it read as FYI.
        self.write("INSERT INTO student_support_items(id,term_id,enrollment_id,kind,source_key,actor,created_at) VALUES(?,?,?,'followup','gmail:one','workspace',?)",
                   (str(uuid.uuid4()), self.term['id'], enrollment, (NOW - timedelta(minutes=10)).isoformat()))
        self.action(endpoint='student_support.revise', body={'request_id': 'thanks-closes:i:2:m', 'state': 'resolved'})
        row = self.row(self.automations(), 'student-triage')
        self.assertEqual(row['last_outcome'], '2 conversations shown as FYI · 1 you later answered or tracked · 1 follow-up closed on a thanks')
        self.assertEqual(row['outcome_label'], 'Last 30 days')
        self.assertEqual(row['href'], '#follow-ups?filter=fyi')
        self.assertTrue(row['scope'].startswith('Jev · '))
        with patch.dict(os.environ, {'FACULTY_TRIAGE_PROVIDER': 'decisions'}):
            self.assertEqual(self.row(self.automations(), 'student-triage')['scope'], 'OpenAI Decisions · gpt-6-luna')

    def test_triage_row_shows_a_host_pause_as_its_blocker_and_failed_reads_as_waiting(self):
        """JEV HOST FAILURES reach the Automation tab through JevService.triage_status (integration of the 2026-10-09 fixes)."""
        status = {'provider': 'decisions', 'model': 'gpt-6-luna', 'available': True, 'failed_count': 2,
                  'paused': 'Student triage paused: OpenAI Decisions unavailable since 10:42 AM. Retrying every 5 minutes.'}
        jev = self.service.app.extensions.get('jev')
        with patch.object(type(jev), 'triage_status', lambda self: status):
            row = self.row(self.automations(), 'student-triage')
        self.assertEqual(row['blocker'], status['paused'])
        self.assertEqual(row['waiting'], '2 conversations could not be read · Retry all failed on Follow-up')
        with patch.object(type(jev), 'triage_status', lambda self: (_ for _ in ()).throw(RuntimeError('catalog busy'))):
            row = self.row(self.automations(), 'student-triage')
        self.assertEqual((row['blocker'], row['waiting']), ('', ''))

    def test_rule_rows_carry_the_target_prepare_again_posts(self):
        rows = [{'id': 'w', 'kind': 'workflow', 'href': '#gradebook/t1'}, {'id': 'scan-pipeline', 'kind': 'built-in'}]
        with connect(self.path) as db:
            added = outcomes(self.service, db, rows, [{'id': 'w', 'kind': 'reconciliation', 'target': 't1'}])
        self.assertEqual(rows[0]['prepare'], {'kind': 'reconciliation', 'target': 't1'})
        self.assertEqual(rows[0]['href'], '#gradebook/t1')
        self.assertTrue(rows[1]['about'])
        self.assertEqual([r['id'] for r in added], ['student-triage'])

    def test_home_counts_waiting_assistant_changes_as_the_changes_tab_lists_them(self):
        overview = self.client.get('/api/workspace/overview').json
        self.assertNotIn('assistant_changes', overview)
        waiting = self.action(tier='approve', kind='proposed')
        self.action(tier='approve', kind='previewed')
        self.action(tier='approve', kind='declined')
        self.action(tier='approve', kind='proposed', at=(NOW - timedelta(hours=30)).isoformat())  # past EXPIRY
        followup = self.action(tier='approve', kind='proposed')  # a follow-up preparation's card: Student follow-up counts it
        run = str(uuid.uuid4())
        self.write('''INSERT INTO student_support_suggestion_runs(id,enrollment_id,item_key,item_id,source_key,source_sha256,source_json,reason,
            request_id,request_sha256,instruction,model,created_at) VALUES(?,?,'k','i','s','x','{}','automatic',?,'x','','m',?)''',
                   (run, self.alice['enrollment_id'], str(uuid.uuid4()), RECENT))
        self.write("INSERT INTO student_support_suggestion_actions(run_id,action_id,request_sha256) VALUES(?,?,'x')", (run, followup))
        self.assertEqual(self.client.get('/api/workspace/overview').json['assistant_changes'], 2)
        listed = {a['id'] for a in self.service.actions(pending_only=True)['pending']}
        self.assertIn(waiting, listed)
        with connect(self.path) as db:
            self.assertEqual(changes_waiting(db), len(listed - {followup}))


class ReviewOnlyMessageTests(unittest.TestCase):
    def setUp(self):
        fixture.ExamWorkflowTests.setUp(self)
        self.addCleanup(settle)  # the turn and the memory worker are done with the catalog before the folder is removed
    import_students = fixture.ExamWorkflowTests.import_students

    def test_a_review_only_request_says_so_on_its_message(self):
        class Engine:
            name = 'synthetic'
            def close(self): pass
            def stop(self): pass
            def turn(self, c, text): c.emit('assistant', text='Read only.')
        with patch('assistant.conversations.config.assistant', return_value={'name': 'Synthetic', 'model': 'test'}), \
             patch('assistant.conversations.engine_for', side_effect=lambda _: Engine()):
            started = self.client.post('/api/assistant/conversations', json={'page': '#overview'}, headers=HEADERS)
            self.assertEqual(started.status_code, 201, started.json)
            ident = started.json['id']
            sent = self.client.post(f'/api/assistant/conversations/{ident}/messages', json={'text': 'Diagnose why “Workspace health” is blocked.',
                                    'review_only': True, 'review_context': {'kind': 'recovery', 'page': '#overview'}}, headers=HEADERS)
            self.assertEqual(sent.status_code, 202, sent.json)
            settle()
            self.client.post(f'/api/assistant/conversations/{ident}/messages', json={'text': 'Ordinary question.'}, headers=HEADERS)
            settle()
            users = [e for e in self.client.get(f'/api/assistant/conversations/{ident}').json['events'] if e['kind'] == 'user']
        self.assertIs(users[0].get('review_only'), True)
        self.assertNotIn('review_only', users[1])


if __name__ == '__main__':
    unittest.main()
