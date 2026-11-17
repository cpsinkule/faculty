"""ASSISTANT sweep regressions; isolated records and scripted engines only."""
from types import SimpleNamespace
import json
import threading
import unittest
from unittest.mock import Mock, patch

from werkzeug.exceptions import Conflict

from assistant.conversations import Conversation, MAX_CONVERSATIONS
from assistant.engines import EngineError
from test_assistant import Base
from test_storage import HEADERS


class AssistantSweepTests(Base):
    def test_local_followup_sessions_close_after_success_and_failure(self):
        from assistant.followups import FollowupSuggestions
        followups = FollowupSuggestions.__new__(FollowupSuggestions)
        followups.assistant = self.service
        before = set(self.service._sessions)
        with followups._session('faculty', 'Local proposal', _proposal_only=True) as session:
            self.assertTrue(session['_proposal_only'])
            self.assertIn(session['id'], self.service._sessions)
        self.assertEqual(set(self.service._sessions), before)
        with self.assertRaisesRegex(RuntimeError, 'Failed local proposal'):
            with followups._session('faculty', 'Local proposal'):
                raise RuntimeError('Failed local proposal')
        self.assertEqual(set(self.service._sessions), before)

    def test_bad_message_cursor_is_refused_before_starting_an_answer(self):
        engine = Mock(name='engine', spec=['name', 'turn', 'close', 'stop'])
        engine.name = 'test'
        with patch('assistant.conversations.engine_for', return_value=engine):
            conversation = Conversation(self.service, self.session, {'name': 'test'}, 'http://localhost', '#overview')
        self.service.conversations._items[conversation.id] = conversation
        for value in ({'bad': 1}, ['bad'], 'not-a-cursor', float('inf')):
            with self.subTest(value=value):
                response = self.client.post(f'/api/assistant/conversations/{conversation.id}/messages',
                                            json={'text': 'Read the page', 'after': value}, headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(conversation.transcript, [])
                self.assertFalse(conversation.busy)
        engine.turn.assert_not_called()

    def test_failed_conversation_construction_does_not_keep_a_session(self):
        before = set(self.service._sessions)
        conversations = self.service.conversations
        with patch.object(conversations, '_settings', return_value={}), \
             patch('assistant.conversations.config.assistant', return_value={'name': 'test'}):
            for error in (EngineError('Unavailable test engine'), RuntimeError('Failed construction')):
                with self.subTest(error=type(error).__name__):
                    with self.assertRaises((ValueError, RuntimeError)):
                        conversations.start(factory=Mock(side_effect=error))
                    self.assertEqual(set(self.service._sessions), before)

    def test_full_busy_conversation_pool_closes_the_unused_engine_and_session(self):
        conversations = self.service.conversations
        conversations._items = {str(n): SimpleNamespace(busy=True) for n in range(MAX_CONVERSATIONS)}
        before = set(self.service._sessions)
        created = Mock()
        with patch.object(conversations, '_settings', return_value={}), \
             patch('assistant.conversations.config.assistant', return_value={'name': 'test'}):
            with self.assertRaises(Conflict):
                conversations.start(factory=Mock(return_value=created))
        created.close.assert_called_once_with()
        self.assertEqual(set(self.service._sessions), before)
        self.assertEqual(len(conversations._items), MAX_CONVERSATIONS)

    def test_malformed_retained_owners_are_client_errors(self):
        for level, owner in (({}, 'id'), ([], 'id'), ('term', {}), ('section', [])):
            with self.subTest(level=level, owner=owner):
                response = self.client.post('/api/assistant/artifacts/preview', json={
                    'level': level, 'owner_id': owner, 'title': 'A note', 'filename': 'note.txt', 'content': 'Text.'
                }, headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)


class CodingRoundTests(unittest.TestCase):
    def test_follow_up_does_not_launch_over_a_running_round(self):
        from assistant.coding import CodingService
        coding = CodingService.__new__(CodingService)
        coding._lock = threading.Lock()
        coding._task = Mock(return_value=({'id': 'task'}, []))
        coding._rounds = Mock(return_value=[{'state': 'running', 'number': 1}])
        coding._launch = Mock()
        with self.assertRaisesRegex(Conflict, 'running'):
            coding.follow_up('task', {'text': 'Another change'})
        coding._launch.assert_not_called()

    def test_codex_requests_release_waiters_on_timeout_or_broken_transport(self):
        from assistant.engines import CodexServerEngine
        engine = CodexServerEngine({'model': 'test'})
        for error in (None, BrokenPipeError()):
            with self.subTest(error=error):
                with patch.object(engine, '_send', side_effect=error):
                    with self.assertRaises(EngineError):
                        engine._request('test/request', {}, timeout=0)
                self.assertEqual(engine._waiting, {})

    def test_meeting_evidence_uses_the_same_clock_as_other_arrivals(self):
        from assistant.followup_evidence import _meetings
        from communications.record import utc
        db = Mock()
        db.execute.return_value = [(json.dumps({'date': '2026-10-24', 'time': '12:00', 'end_time': '12:30',
                                                'state': 'scheduled', 'title': 'Office hours'}),)]
        [meeting] = _meetings(db, 'student', '2026-10-23T15:00:00Z', '2026-10-24T13:00')
        self.assertEqual(meeting['when'], utc('2026-10-24T17:30:00Z'))

    def test_calendar_freshness_counts_all_days_of_a_timed_entry(self):
        from assistant.student_context import StudentContext
        event = ['digest', '2026-10-05', 900, 600, False, '2026-10-07']
        for day, start, affected in (('2026-10-05', 840, False), ('2026-10-05', 900, True),
                                      ('2026-10-06', 540, True), ('2026-10-07', 570, True),
                                      ('2026-10-07', 600, False)):
            self.assertEqual(StudentContext._at(event, {(day, start, start + 30)}), affected, (day, start))
        self.assertTrue(StudentContext._at(['old', '2026-10-06', 600, 660, False], {('2026-10-06', 630, 660)}))

    def test_appointment_end_time_change_is_shown_before_approval(self):
        from assistant.followup_effects import Describer
        [change] = Describer._support_lines({'end_time': '14:45'}, {
            'date': '2026-10-24', 'time': '14:00', 'end_time': '14:30'
        })
        self.assertEqual(change['label'], 'When')
        self.assertIn('2:45 PM', change['value'])
        self.assertIn('2:30 PM', change['before'])

    def test_regrade_receipt_description_keeps_zero_grades(self):
        from assistant.followup_effects import Describer
        describer = Describer(None, None, {}, {'name': 'Student'}, None)
        describer.preview = {'from': 0, 'to': 0, 'of': 10,
                             'steps': [{'step': kind, 'from': 0, 'to': 0} for kind in ('gradescope', 'gradebook', 'canvas')]}
        found = describer._frq_regrade_panel_decide({'decision': 'keep', 'reply': 'The grade stays.'}, {})
        self.assertIn('Gradescope 0 → 0', found['notes'][0])
        self.assertIn('the gradebook 0 → 0', found['notes'][0])
        self.assertIn('Canvas 0 → 0', found['notes'][0])


class MemorySweepTests(Base):
    def new_memory(self, content='Keep concise replies.'):
        from rosters import connect
        with self.service.rosters.lock(), connect(self.service.database) as db:
            return self.service.memory._new(db, {'content': content, 'source': 'chat',
                                                  'actor': 'assistant', 'reason': 'kept'})[0]

    def test_restore_cannot_exceed_the_current_memory_limit(self):
        with patch('assistant.memory.CURRENT_LIMIT', 1):
            old = self.new_memory()
            self.service.memory.remove(old)
            current = self.new_memory('Use direct wording.')
            with self.assertRaisesRegex(ValueError, 'at most 1'):
                self.service.memory.restore(old)
            self.assertEqual([m['id'] for m in self.service.memory.preferences()], [current])

    def test_memory_owner_identifiers_are_validated_before_keeping(self):
        for scope, field in (('course', 'definition_id'), ('term', 'term_id')):
            response = self.client.post('/api/assistant/memories', json={
                'scope': scope, field: {}, 'content': 'Keep concise replies.'
            }, headers=HEADERS)
            self.assertEqual(response.status_code, 400, response.json)

    def test_memory_provenance_reads_are_batched_without_changing_views(self):
        from rosters import connect
        identifiers = [self.new_memory(f'Use direct wording {number}.') for number in range(5)]
        with connect(self.service.database) as db:
            owners = self.service.memory._owners(db)
            views = [self.service.memory._view(self.service.memory._latest(db, ident), owners) for ident in identifiers]
            queries = []
            db.set_trace_callback(queries.append)
            try:
                self.assertEqual(self.service.memory._typed(db, views), views)
            finally:
                db.set_trace_callback(None)
        self.assertEqual(len(queries), 1)
        self.assertFalse(any('ruled' in view or 'learned' in view for view in views))
