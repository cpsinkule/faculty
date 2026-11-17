"""ASSISTANT-MEMORY "LEARNED FROM AN EDIT": the instructor's confirmed corrections of a follow-up's cards, found, compared and
handed to memory. Synthetic catalog; the Assistant's model and Jev are scripted, and nothing leaves the machine."""
import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from assistant import followup_learning
from assistant import memory_decisions as decisions
from assistant.followup_learning import CASES
from rosters import connect
from test_assistant_memory import scripted
from test_followup_suggestions import MAIL, MEETING, SuggestionsTests, ScriptedEngine, proposing
from test_storage import HEADERS


class LearningMigrationTests(unittest.TestCase):
    def test_mail_outcome_upgrade_keeps_recorded_history_and_references(self):
        from catalog_migrations import apply_sql, migration_transaction
        migrations = Path(__file__).resolve().parents[1] / 'student_support' / 'migrations'
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('PRAGMA foreign_keys=ON')
        for table in ('course_terms', 'student_enrollments', 'student_support_suggestion_runs', 'assistant_actions', 'gmail_mail_sends', 'jev_calls'):
            db.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY)')
            db.execute(f"INSERT INTO {table} VALUES('source')")
        db.commit()
        original = (migrations / '016_feedback_learning.sql').read_text()
        with migration_transaction(db):
            apply_sql(db, 'student_support', '016_feedback_learning', original)
            db.execute("INSERT INTO student_support_learning_cases VALUES('case','source','source','source','source','source','edited','strong','fingerprint','now')")
            db.execute("INSERT INTO student_support_learning_events(case_id,state,detail_json,created_at) VALUES('case','ready','{}','now')")
            db.execute("INSERT INTO student_support_learning_lessons VALUES('lesson','case','source','term','now')")
        checksum = db.execute('SELECT sha256 FROM schema_migrations').fetchone()[0]
        with migration_transaction(db):
            apply_sql(db, 'student_support', '017_learning_mail_outcomes', (migrations / '017_learning_mail_outcomes.sql').read_text())
        self.assertEqual(db.execute("SELECT sha256 FROM schema_migrations WHERE version='016_feedback_learning'").fetchone()[0], checksum)
        self.assertEqual(db.execute('SELECT final_action_id,final_send_id FROM student_support_learning_cases').fetchone(), ('source', None))
        self.assertEqual(db.execute('SELECT case_id FROM student_support_learning_events').fetchone()[0], 'case')
        self.assertEqual(db.execute('SELECT case_id FROM student_support_learning_lessons').fetchone()[0], 'case')
        self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        db.execute("INSERT INTO student_support_learning_cases VALUES('mail','source','source','source','source','source','edited_mail','strong','mail-fingerprint','now','source')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE student_support_learning_cases SET strength='supporting'")

DRAFT = 'Thank you for reaching out. Under Student Rule 7 you must send documentation by 10/14. You can ask for help at office hours.'
MINE = 'Thank you for reaching out. Please send me the documentation when you can. You can ask for help at office hours.'
RULE = 'When asking a student for documentation of an absence, ask plainly and do not cite the rule number.'


class LearningTests(SuggestionsTests):
    def setUp(self):
        super().setUp()
        self.learning = self.suggestions.learning
        held = patch.object(followup_learning, 'LEARNS_FROM', '')  # every synthetic correction is after the cutoff unless a test says
        held.start()
        self.addCleanup(held.stop)

    # -- the instructor's corrections ------------------------------------------------------------------------------------------
    def drafted(self, body=DRAFT, student='I was sick on Friday and missed the quiz. Can I make it up?'):
        self.fake_mail()
        self.incoming(body=student)
        run, _ = self.prepare(proposing(('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': body}, 'Reply')))
        return run, run['actions'][0]

    def test_draft_memory_references_survive_the_conversation_and_an_edit_alone_says_nothing_of_a_memory(self):
        from test_assistant_memory import answering
        memory = self.assistant.memory
        with answering():
            kept = memory.write({'content': 'Use a short greeting.'})
        run, action = self.drafted()
        given = [{'id': kept['id'], 'revision': kept['revision']}]
        memory.record_given(given, followup=run['id'])  # same run replayed, no extra exposure
        row = next(m for m in memory.listing()['memories'] if m['id'] == kept['id'])
        self.assertEqual(row['given'], {'drafts': 1, 'again': 0})
        # An edit of the draft is no word on this memory: every draft is given nearly every memory (2026-10-13).
        edited = self.edit(run, action, subject='Re: Question', body=MINE)
        self.edit(run, edited, subject='Re: Question', body=MINE + ' Thank you.')
        self.assertEqual(memory.listing()['memories'][0]['given'], {'drafts': 1, 'again': 0})
        with answering():
            memory.write({'content': 'Use a friendly greeting.', 'revision': kept['revision']}, kept['id'])
        self.assertNotIn('given', memory.listing()['memories'][0])
        history = memory.history(kept['id'])['revisions']
        self.assertEqual(history[-1]['given'], {'drafts': 1, 'again': 0})
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assistant_draft_memories').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM assistant_draft_memories')

    def edit(self, run, action, **fields):
        result = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={
            'action_id': action['id'], 'seen': action['seen'], 'request_id': uuid.uuid4().hex, **fields}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        return self.view(result.json['action_id'])

    def edited(self, body=MINE, sent=True):
        """A drafted reply the instructor rewrote on its card and, unless told, sent."""
        run, action = self.drafted()
        action = self.edit(run, action, subject='Re: Question', body=body)
        if sent:
            self.assertEqual(self.approve(action).status_code, 200)
        return run, action

    def redone(self, asked='Do not cite the rule number. Just ask for the documentation.', body=MINE, approve=True):
        """A round prepared again at the instructor's request, with its redone reply approved unless told."""
        run, _ = self.drafted()
        self.assertEqual(self.request(run, 'revise', text=asked).status_code, 202)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(
                ('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': body}, 'Reply')))):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        again = self.listing()['items'][0]['suggestions']
        if approve:
            self.assertEqual(self.approve(again['actions'][0]).status_code, 200)
        return run, again

    def again(self, case):
        """The same correction as one more queued case: each case is considered once, so each way it can end needs its own."""
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.learning._case(db, case, case['action_id'], case['final_action_id'], case['feedback'], uuid.uuid4().hex, case['final_send_id'])
        return self.cases()[-1]

    def cases(self):
        with connect(self.assistant.database) as db:
            return [dict(row) for row in db.execute(CASES + ' ORDER BY c.created_at,c.id')]

    def compared(self, case):
        with connect(self.assistant.database) as db:
            return self.learning.comparison(db, case, self.learning.names())

    def writer(self, rule=RULE):
        """The Assistant's own model, scripted: (the patches that put it in place, the requests it was sent). None declines."""
        sent = []

        class Provider:
            def submit(self, request):
                sent.append(request)
                if isinstance(rule, Exception):
                    raise rule
                return {'state': 'completed', 'result': {'output': {'resolved': rule is not None, 'rewrite': rule or ''}}}
        return (patch('grading.config.assistant', return_value={'name': 'scripted', 'kind': 'hosted', 'model': 'scripted'}),
                patch.object(self.app.extensions['frq_grading'], 'cloud_provider', return_value=Provider())), sent

    def considered(self, case, rule=RULE, **said):
        """One case considered with the model writing `rule` and Jev answering as `said`: (its last event, what the model was
        sent, what Jev was asked)."""
        (assistant, provider), sent = self.writer(rule)
        judge = scripted(**said)
        with assistant, provider, patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=judge):
            self.learning.consider(case['id'])
        last = next(found for found in self.cases() if found['id'] == case['id'])
        return (last['state'], json.loads(last['detail_json'])), sent, judge.asked

    def standing(self):
        return [row for row in self.assistant.memory.listing()['memories'] if row['status'] == 'current']

    # -- finding ---------------------------------------------------------------------------------------------------------------
    def test_only_a_correction_with_a_confirmed_outcome_gets_a_case(self):
        run, action = self.edited(sent=False)
        self.assertEqual((self.learning.discover(), self.cases()), (0, []))  # an edit not yet sent teaches nothing
        self.assertEqual(self.approve(action).status_code, 200)
        self.assertEqual(self.learning.discover(), 1)
        case, = self.cases()
        self.assertEqual((case['feedback'], case['state'], case['final_action_id'], case['run_id']), ('edited', 'queued', action['id'], run['id']))
        self.assertEqual(case['action_id'], run['actions'][0]['id'])  # the Assistant's own draft
        self.assertEqual(self.learning.discover(), 0)  # found once
        with connect(self.assistant.database) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE student_support_learning_cases SET strength=?', ('other',))
            kept = ' '.join(row[0] for row in db.execute('SELECT detail_json FROM student_support_learning_events'))
        self.assertNotIn('documentation', kept)  # a case copies no words

    def test_an_unchanged_approval_and_a_decline_are_no_correction(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
        self.assertEqual(self.client.post('/api/assistant/actions/' + meeting['id'] + '/decline', json={}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.approve(self.view(mail['id'])).status_code, 200)
        self.assertEqual((self.learning.discover(), self.cases()), (0, []))

    def test_an_edit_of_an_edit_goes_back_to_the_assistants_first_draft(self):
        run, first = self.drafted()
        second = self.edit(run, first, subject='Re: Question', body='Please send the documentation by Friday.')
        third = self.edit(run, second, subject='Re: Question', body=MINE)
        self.assertEqual(self.approve(third).status_code, 200)
        self.assertEqual(self.learning.discover(), 1)
        case, = self.cases()
        self.assertEqual((case['action_id'], case['final_action_id']), (first['id'], third['id']))
        found = self.compared(case)
        self.assertEqual(found['change']['added'], ['Please send me the documentation when you can.'])

    def test_nothing_from_before_this_path_was_live_is_found(self):
        for correct in (self.edited, self.redone):
            with self.subTest(correct=correct.__name__):
                correct()
                with patch.object(followup_learning, 'LEARNS_FROM', '9999'):
                    self.assertEqual((self.learning.discover(), self.cases()), (0, []))
                self.assertEqual(self.learning.discover(), 1)
                # A case queued before the cutoff (the lesson review's, left when it was withdrawn) is never handed on.
                with patch.object(followup_learning, 'LEARNS_FROM', '9999'), patch.object(self.assistant.memory, 'later') as handed:
                    self.learning.hand()
                    handed.assert_not_called()
                self.tearDown_one()

    def tearDown_one(self):
        """Between two corrections of one test: a catalog of its own for the next (the fixture's is rebuilt)."""
        self.doCleanups()
        self.setUp()

    def test_a_round_prepared_again_is_one_correction_whatever_its_cards(self):
        self.fake_mail()
        self.incoming(body='Could I meet you about the quiz I missed?')
        run, _ = self.prepare(proposing(MAIL, MEETING))
        asked = 'Do not offer a set time. Office hours are drop-in.'
        self.assertEqual(self.request(run, 'revise', text=asked).status_code, 202)
        reply = ('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': 'Yes, come by office hours any time.'}, 'Reply')
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(reply, MEETING))):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        again = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.learning.discover(), 0)  # asked for, not yet approved
        for card in again['actions']:
            self.assertEqual(self.approve(self.view(card['id'])).status_code, 200)
            self.learning.discover()
        case, = self.cases()
        self.assertEqual((case['feedback'], case['run_id']), ('requested_change', again['id']))
        found = self.compared(case)
        self.assertEqual((found['kind'], found['own'], found['change']['asked']), ('request', asked, asked))
        self.assertEqual([(card['what'], 'before' in card) for card in found['change']['cards']], [('reply', True)])
        self.assertIn('come by office hours any time', found['change']['cards'][0]['after'])
        self.assertIn('Monday at [value] pm', found['change']['cards'][0]['before'])  # the draft's numbers are taken out

    def test_a_round_asked_for_in_the_chat_is_a_correction_too(self):
        run, _ = self.drafted()
        redo = self.client.post('/api/student-support/followups/suggestions/redo', json={
            'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'text': 'Ask for the documentation without citing the rule.',
            'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(redo.status_code, 202, redo.json)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(
                ('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': MINE}, 'Reply')))):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        again = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.approve(again['actions'][0]).status_code, 200)
        self.assertEqual(self.learning.discover(), 1)
        self.assertEqual(self.cases()[0]['feedback'], 'requested_change')

    def test_a_reply_sent_from_mail_in_an_edited_drafts_place_is_compared_with_the_first_draft(self):
        run, first = self.drafted()
        edited = self.edit(run, first, subject='Re: Question', body='Please send the documentation by Friday.')
        body = {'request_id': uuid.uuid4().hex, 'to': 'alice@example.test', 'subject': 'Re: Question', 'body': MINE}
        preview = self.client.post('/api/mail/preview', json=body, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        sent = self.client.post('/api/mail/send', json={**body, 'fingerprint': preview.json['fingerprint'], 'confirmation': 'send'}, headers=HEADERS)
        self.assertEqual(sent.status_code, 200, sent.json)
        replaced = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/replace", json={
            'action_id': edited['id'], 'seen': edited['seen'], 'send_request_id': body['request_id'], 'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(replaced.status_code, 200, replaced.json)
        self.assertEqual(self.learning.discover(), 1)
        case, = self.cases()
        self.assertEqual((case['feedback'], case['action_id'], case['final_action_id'], bool(case['final_send_id'])),
                         ('edited_mail', first['id'], edited['id'], True))
        self.assertEqual(self.compared(case)['change']['added'], ['Please send me the documentation when you can.'])

    # -- comparing -------------------------------------------------------------------------------------------------------------
    def test_a_reply_is_compared_by_its_sentences_without_names_numbers_or_private_notes(self):
        made = self.client.post('/api/student-notes', json={'enrollment_id': self.alice, 'date': '2026-10-01',
            'text': 'PRIVATE-NOTE-MARKER family matter', 'request_id': 'private-note'}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        run, first = self.drafted(body='Hi Alice, ' + DRAFT, student='This is Alice Example, 123456789. I was sick on Friday.')
        self.assertEqual(self.approve(self.edit(run, first, subject='Re: Question', body='Hi Alice, ' + MINE)).status_code, 200)
        self.learning.discover()
        found = self.compared(self.cases()[0])
        self.assertEqual((found['kind'], found['own']), ('edit', 'Please send me the documentation when you can.'))
        change = found['change']
        self.assertEqual((change['what'], change['added']), ('a reply to the student', ['Please send me the documentation when you can.']))
        self.assertEqual(change['removed'], ['Under Student Rule [value] you must send documentation by [value].'])
        told = json.dumps(found)
        for absent in ('Alice', 'alice@example.test', '123456789', '10/14', 'PRIVATE-NOTE-MARKER', self.alice):
            self.assertNotIn(absent, told)
        self.assertIn('I was sick on Friday', found['about'])  # what the student wrote, for the kind of case alone

    def test_a_changed_decision_is_compared_as_the_two_decisions_and_changed_back_is_nothing(self):
        session = self.session(body='I missed class Friday for my biology lab exam review on Zoom.')
        item = self.listing()['items'][0]
        run = item['suggestions']
        made = self.tool_write(session, '/api/absences', {'section_id': item['student']['section_id'], 'enrollment_id': self.alice,
            'first_date': '2026-10-02', 'reason': 'activity', 'notice_message_id': self.notice(), 'status': 'pending'})
        first = self.view(made['action_id'])
        chosen = self.edit(run, first, status='not_excused')
        self.assertEqual(self.approve(chosen).status_code, 200)
        self.assertEqual(self.learning.discover(), 1)
        found = self.compared(self.cases()[0])
        self.assertEqual((found['kind'], found['own']), ('decision', ''))
        self.assertEqual(found['change'], {'what': 'the decision on an absence', 'suggested': 'Waiting for your decision',
                                           'chosen': 'Not excused', 'absence': 'University-authorized activity', 'documents': False})
        self.assertIn('biology lab exam review', found['about'])
        self.assertNotIn('2026-10-02', json.dumps(found))  # no date becomes a rule
        # Changed and changed back: the Assistant's suggestion stood. Left undecided after all: nothing was decided to learn from.
        with connect(self.assistant.database) as db:
            case = dict(db.execute(CASES).fetchone())
            self.assertEqual(self.learning.comparison(db, {**case, 'final_action_id': first['id']}, []), {'why': 'unchanged'})
            self.assertEqual(self.learning.comparison(db, {**case, 'action_id': chosen['id'], 'final_action_id': first['id']}, []), {'why': 'unexplained'})

    def test_a_regrade_reply_teaches_and_its_grade_never_does(self):
        # The cards are written here as the regrade panel's follow-up writes them: a decision's body holds its grade and its reply.
        run, _ = self.edited()
        self.learning.discover()
        case = self.cases()[0]
        grade = {'decision': 'change', 'lines': ['line-1'], 'adjustment': {'points': 2, 'reason': 'Arithmetic slip only.'}, 'reason': 'The work is right.'}

        def card(reply, **more):
            ident = uuid.uuid4().hex
            with self.rosters_lock(), connect(self.assistant.database) as db:
                db.execute("""INSERT INTO assistant_actions(id,session_id,engine,tier,endpoint,method,path,body_json,summary,created_at)
                    SELECT ?,session_id,engine,'approve','frq_regrade_panel.decide','POST','/api/x',?,'Decide',created_at FROM assistant_actions LIMIT 1""",
                           (ident, json.dumps({**grade, **more, 'reply': reply})))
            return ident
        drafted = card('Thank you for asking. I looked again at problem 3 and your grade stays as it is.')
        reworded = card('I looked again at problem 3 and your grade stays as it is.')
        regraded = card('Thank you for asking. I looked again at problem 3 and your grade stays as it is.', decision='keep')
        found = self.compared({**case, 'action_id': drafted, 'final_action_id': reworded})
        self.assertEqual((found['kind'], found['change']['what'], found['change']['removed']),
                         ('edit', 'a reply to a regrade request', ['Thank you for asking.']))
        for absent in ('line-1', 'Arithmetic', 'The work is right', 'adjustment'):
            self.assertNotIn(absent, json.dumps(found))
        self.assertEqual(self.compared({**case, 'action_id': drafted, 'final_action_id': regraded}), {'why': 'unchanged'})

    def test_a_request_too_long_for_one_rule_teaches_nothing(self):
        self.redone(asked='Please change this. ' * 150)
        self.learning.discover()
        self.assertEqual(self.compared(self.cases()[0]), {'why': 'long'})

    # -- handing on ------------------------------------------------------------------------------------------------------------
    def test_a_correction_becomes_a_memory_once_and_the_case_says_so(self):
        run, action = self.edited()
        self.learning.discover()
        case = self.cases()[0]
        (state, detail), sent, asked = self.considered(case)
        memory, = self.standing()
        self.assertEqual((state, detail), ('remembered', {'memory_id': memory['id'], 'kind': 'edit'}))
        self.assertEqual((memory['content'], memory['source'], memory['actor'], memory['scope'], memory.get('learned')),
                         (RULE, 'followup', 'assistant', 'workspace', True))
        self.assertEqual([request['prompt'] for request in sent], ['memory_edit'])
        self.assertEqual(set(sent[0]['payload']), {'change', 'open_item'})
        with connect(self.assistant.database) as db:
            calls = db.execute('SELECT purpose,owner_id,enrollment_id FROM jev_calls WHERE purpose=?', (decisions.EDIT_PURPOSE,)).fetchall()
        self.assertEqual([tuple(row) for row in calls], [(decisions.EDIT_PURPOSE, case['id'], None)])
        # Nothing is handed twice: a second look at the same case asks no one.
        (state, _), sent, asked = self.considered(case)
        self.assertEqual((state, sent, asked, len(self.standing())), ('remembered', [], [], 1))

    def test_what_teaches_nothing_is_recorded_and_said_nowhere(self):
        self.edited()
        self.learning.discover()
        case = self.cases()[0]
        for rule, said, why in ((None, {}, 'declined'), (RuntimeError('host'), {}, 'unwritten'), (RULE, {'shown': .2}, 'unshown'),
                                (RULE, {'once': .9}, 'unshown'), (RULE, {'record_fact': .9}, 'record'),
                                ('Tell Alice Example to send it.', {}, 'named'), ('Give students 3 days to send documentation.', {}, 'unsaid')):
            with self.subTest(why=why, said=said):
                (state, detail), _, _ = self.considered(case, rule, **said)
                self.assertEqual((state, detail, self.standing()), ('nothing', {'why': why}, []))
                self.assertNotIn('learned', self.listing()['items'][0]['suggestions'])
                case = self.again(case)

    def test_a_rule_already_kept_is_known_and_no_answer_keeps_nothing(self):
        run, _ = self.edited()
        self.learning.discover()
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()):
            kept = self.assistant.memory.write({'content': RULE})
        (state, detail), _, _ = self.considered(self.cases()[0], same_0=.95)
        self.assertEqual((state, detail, len(self.standing())), ('known', {'memory_id': kept['id']}, 1))

    def kept_rule(self, words=RULE):
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()):
            return self.assistant.memory.write({'content': words})

    def test_a_draft_given_a_rule_and_still_corrected_that_way_is_counted_on_that_rule_alone(self):
        memory = self.assistant.memory
        kept, other = self.kept_rule(), self.kept_rule('Sign replies with your first name.')
        self.edited()  # the draft was given both memories, and the instructor still corrected it
        self.learning.discover()
        counts = lambda: {row['id']: row.get('given') for row in memory.listing()['memories']}  # noqa: E731
        self.assertEqual(counts(), {kept['id']: {'drafts': 1, 'again': 0}, other['id']: {'drafts': 1, 'again': 0}})
        # The kept memories are compared in the order they were kept: say "the same" of the rule, wherever it stands in it.
        place = [row['id'] for row in sorted(memory.listing()['memories'], key=lambda row: (row['created_at'], row['id']))].index(kept['id'])
        same = {f'same_{place}': .95}
        (state, detail), _, _ = self.considered(self.cases()[0], **same)
        self.assertEqual((state, detail), ('known', {'memory_id': kept['id']}))
        counted = {kept['id']: {'drafts': 1, 'again': 1}, other['id']: {'drafts': 1, 'again': 0}}
        self.assertEqual(counts(), counted)
        # A correction that teaches nothing is counted on no memory, and a second case of that draft is still the one draft.
        (state, _), _, _ = self.considered(self.again(self.cases()[0]), None)
        self.assertEqual((state, counts()), ('nothing', counted))
        (state, _), _, _ = self.considered(self.again(self.cases()[0]), **same)
        self.assertEqual((state, counts()), ('known', counted))
        # Reworded, the rule starts again: what came up under the old words stays with them in its history.
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()):
            memory.write({'content': RULE + ' Keep it to one sentence.', 'revision': kept['revision']}, kept['id'])
        self.assertNotIn('given', next(row for row in memory.listing()['memories'] if row['id'] == kept['id']))
        self.assertEqual([revision.get('given') for revision in memory.history(kept['id'])['revisions']], [None, {'drafts': 1, 'again': 1}])
        self.assertEqual(memory.history(other['id'])['revisions'][0]['given'], {'drafts': 1, 'again': 0})

    def test_a_round_asked_for_again_counts_for_a_rule_only_when_the_draft_before_it_had_been_given_the_rule(self):
        # Given the rule, the first draft still had to be asked for again in the rule's own way: counted, on the draft it corrects.
        kept = self.kept_rule()
        self.redone()
        self.learning.discover()
        (state, detail), _, _ = self.considered(self.cases()[0], same_0=.95)
        self.assertEqual((state, detail), ('known', {'memory_id': kept['id']}))
        self.assertEqual(self.assistant.memory.listing()['memories'][0]['given'], {'drafts': 2, 'again': 1})

    def test_a_rule_kept_as_the_change_was_asked_for_is_not_counted_against_the_draft_before_it(self):
        """The first case on live (2026-10-13): a change asked for in the chat was kept as a memory there, the round prepared
        again was given it, and that round's own correction was then found "already kept". The draft it corrected had never been
        given the rule, so it says nothing of the rule."""
        run, _ = self.drafted()  # prepared before the rule existed
        kept = self.kept_rule()  # kept in the chat as the change is asked for
        self.assertEqual(self.request(run, 'revise', text='Do not cite the rule number. Just ask for the documentation.').status_code, 202)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(
                ('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': MINE}, 'Reply')))):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertEqual(self.approve(self.listing()['items'][0]['suggestions']['actions'][0]).status_code, 200)
        self.learning.discover()
        (state, detail), _, _ = self.considered(self.cases()[0], same_0=.95)
        self.assertEqual((state, detail), ('known', {'memory_id': kept['id']}))
        self.assertEqual(self.assistant.memory.listing()['memories'][0]['given'], {'drafts': 1, 'again': 0})  # the redone draft alone

    def test_jev_not_answering_keeps_nothing_and_is_not_asked_again(self):
        run, _ = self.edited()
        self.learning.discover()
        case = self.cases()[0]
        (assistant, provider), sent = self.writer()
        with assistant, provider, patch('jev.client.available', return_value=False):
            self.learning.consider(case['id'])
        last = self.cases()[0]
        self.assertEqual((last['state'], json.loads(last['detail_json']), self.standing()), ('nothing', {'why': 'unchecked'}, []))
        (state, _), sent, asked = self.considered(case)
        self.assertEqual((state, sent, asked), ('nothing', [], []))

    def test_a_correction_left_mid_way_is_not_asked_about_again(self):
        self.edited()
        self.learning.discover()
        case = self.cases()[0]
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.learning.event(db, case['id'], 'running')
        self.learning.recover()
        (state, _), sent, _ = self.considered(case)
        self.assertEqual((state, sent, self.standing()), ('interrupted', [], []))

    def test_the_dispatchers_look_runs_only_when_learning_is_on_and_hands_each_case_once(self):
        self.edited()
        handed = []
        with patch.object(self.assistant.memory, 'later', side_effect=handed.append):
            self.learning.turn()  # synthetic checks keep it off (`automatic`)
            self.assertEqual((handed, self.cases()), ([], []))
            self.suggestions.automatic = True
            with patch.dict('os.environ', {'FACULTY_LEARNING_ENABLED': '0'}):
                self.learning.turn()
                self.assertEqual((handed, self.learning.busy()), ([], False))
            self.assertTrue(self.learning.busy())  # woken at start, and again by the approval
            self.learning.turn()
            self.assertEqual((len(handed), [case['state'] for case in self.cases()]), (1, ['queued']))
            self.assertTrue(self.learning.busy())  # handed, not yet considered
            self.learning.wake()
            self.learning.turn()
            self.assertEqual(len(handed), 1)  # the same case is not handed again
        (assistant, provider), _ = self.writer()
        with assistant, provider, patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()):
            handed[0]()
        self.assertEqual(([case['state'] for case in self.cases()], self.learning.busy()), (['remembered'], False))
        self.learning.turn()  # no approval since, and the last look was a moment ago: nothing is read
        with patch.object(self.learning, 'discover') as looked:
            self.learning.turn()
            looked.assert_not_called()
            self.assertEqual(self.approve(self.view(self.listing()['items'][0]['suggestions']['actions'][0]['id'])).status_code, 409)
        self.suggestions.automatic = False

    # -- saying ----------------------------------------------------------------------------------------------------------------
    def test_the_follow_up_says_what_was_remembered_until_it_is_taken_back_or_reworded(self):
        default = self.client.get('/api/student-support?kind=followup').data
        self.edited()
        before = self.client.get('/api/student-support?kind=followup').data
        self.learning.discover()
        self.considered(self.cases()[0])
        memory, = self.standing()
        self.assertEqual(self.client.get('/api/student-support?kind=followup').data, before)  # the default read says nothing of it
        self.assertNotIn(b'learned', default)
        said = self.listing()['items'][0]['suggestions']['learned']
        self.assertEqual([(entry['kind'], entry['memory']) for entry in said], [('edit', {'id': memory['id'], 'content': RULE, 'standing': True})])
        self.assertNotIn('remembering', self.listing()['suggestion_settings'])
        undone = self.client.post(f"/api/assistant/memory/{memory['id']}/undo", json={}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(self.listing()['items'][0]['suggestions']['learned'][0]['memory'], {'id': memory['id'], 'content': RULE, 'standing': False})
        self.assertEqual(self.client.post(f"/api/assistant/memory/{memory['id']}/restore", json={}, headers=HEADERS).status_code, 200)
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()):
            self.assistant.memory.write({'content': 'Ask plainly for documentation.', 'revision': 3}, memory['id'])
        self.assertNotIn('learned', self.listing()['items'][0]['suggestions'])  # their own words now: no longer the Assistant's to say

    def test_a_newer_learned_rule_replaces_an_older_one_and_the_follow_up_says_what_it_replaced(self):
        self.edited()
        self.learning.discover()
        self.considered(self.cases()[0])
        first, = self.standing()
        newer = 'Ask for documentation in one plain sentence, with no rule number and no deadline.'
        (state, detail), _, _ = self.considered(self.again(self.cases()[0]), newer, replaces_0=.95)
        second, = self.standing()
        self.assertEqual((state, detail), ('remembered', {'memory_id': second['id'], 'kind': 'edit', 'replaced': first['id']}))
        said = self.listing()['items'][0]['suggestions']['learned']  # newest first; the one it replaced is no longer said
        self.assertEqual([(entry['memory']['content'], entry.get('replaced')) for entry in said], [(newer, RULE)])
        self.assertEqual(self.client.post(f"/api/assistant/memory/{second['id']}/undo", json={}, headers=HEADERS).json['restored'], [first['id']])
        # The older rule stands again and its follow-up offers Undo again: that Undo takes it back (2026-10-14 review: it
        # answered "This memory changed since it was kept").
        said = self.listing()['items'][0]['suggestions']['learned']
        self.assertEqual([(entry['memory']['id'], entry['memory']['standing']) for entry in said], [(second['id'], False), (first['id'], True)])
        undone = self.client.post(f"/api/assistant/memory/{first['id']}/undo", json={}, headers=HEADERS)
        self.assertEqual((undone.status_code, undone.json), (200, {'undone': True, 'restored': []}))
        self.assertEqual(self.standing(), [])

    def test_a_correction_that_could_not_be_claimed_is_handed_again(self):
        """2026-10-14 review: a case whose claim failed stayed queued and was never handed again until a restart."""
        self.edited()
        self.learning.discover()
        jobs = []
        with patch.object(self.assistant.memory, 'later', side_effect=jobs.append):
            self.learning.hand()
            self.learning.hand()  # waiting for its turn: not handed twice
            self.assertEqual(len(jobs), 1)
            with patch.object(self.learning, 'consider', side_effect=sqlite3.OperationalError('database is locked')), \
                    self.assertRaises(sqlite3.OperationalError):
                jobs.pop()()
            self.assertEqual((self.cases()[0]['state'], self.learning._working), ('queued', 0))
            self.learning.hand()
            (assistant, provider), _ = self.writer()
            with assistant, provider, patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()):
                jobs.pop()()
            self.assertEqual(self.cases()[0]['state'], 'remembered')
            self.learning.hand()  # considered: never again
            self.assertEqual(jobs, [])

    def test_the_lesson_review_is_gone(self):
        for path in (f'/api/student-support/learning/{self.term}', f'/api/student-support/learning/{self.term}/evaluation'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post('/api/student-support/learning/cases/x/adopt', json={}, headers=HEADERS).status_code, 404)
        endpoints = {rule.endpoint for rule in self.app.url_map.iter_rules()}
        self.assertFalse({name for name in endpoints if 'learning' in name})
        self.assertNotIn('learning_review', self.listing()['suggestion_settings'])


def load_tests(loader, tests, pattern):
    # The fixture's own scenarios (inherited from SuggestionsTests) run in their module; here, this module's.
    return unittest.TestSuite([loader.loadTestsFromTestCase(LearningMigrationTests),
                               *(LearningTests(name) for name in LearningTests.__dict__ if name.startswith('test_'))])
