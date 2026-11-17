"""REDO FROM CHAT (2026-10-09, requirement, of Jared Zane's follow-up: asked in the Assistant chat to redo it, the chat said it had,
but its new cards stayed in the chat, and approving its Waiting on student card withdrew the follow-up's own cards before any reply
was sent). The chat prepares a follow-up again through the follow-up's own preparation; the follow-up shows the chat's cards still
waiting for its conversation; a chat's Waiting on student waits for a confirmed reply. Synthetic catalogs and fakes only."""
import json
import os
import threading
from unittest.mock import patch

from assistant import policy
from assistant.followups import CHAT_REPLACED, LATEST, REDO_REPLACED, REPLACED_WHILE_PREPARING
from assistant.student_tools import REDO_MOST
from assistant.tool_guidance import MAX_CHECKS, ToolGuidance
from jev import client
from rosters import connect
from test_jev import response
from test_followup_suggestions import MAIL, ScriptedEngine, SuggestionsTests, proposing
from test_gmail_mail import ACCOUNT, message
from test_storage import HEADERS

REDO = '/api/student-support/followups/suggestions/redo'


class RedoTests(SuggestionsTests):
    def thread(self, fake, thread='b1', address='alice@example.test'):
        fake.threads[thread] = [message('m-' + thread, address, ACCOUNT, 'Question', 'Could I meet you?',
                                        extra=[('Message-ID', f'<{thread}@example.test>')])]

    def chat(self):
        return self.assistant.open_session('scripted', 'Chat')

    def tool(self, session, name, args):
        result, _ = self.assistant.run_tool(session, name, args)
        return json.loads(result)

    def card(self, action_id):
        return self.assistant._view(*self.assistant.action(action_id))

    def run_row(self, ident):
        with connect(self.assistant.database) as db:
            return dict(db.execute(LATEST + ' WHERE r.id=?', (ident,)).fetchone())

    def test_a_redo_replaces_the_waiting_steps_on_the_follow_up_at_once(self):
        self.thread(self.fake_mail())
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        waiting = run['actions'][0]
        session = self.chat()
        context = self.tool(session, 'student_context', {'enrollment_id': self.alice, 'source_key': 'gmail:b1'})
        self.assertEqual(context['suggested_actions'], {'state': 'ready', 'waiting': 1, 'page': '#follow-ups'})
        asked = self.tool(session, 'redo_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                                                     'changes': "Ask for a doctor's note."})
        # NO CARD FOR A REDO (requirement, 2026-10-10): it replaces a step still waiting and starts at once all the same.
        self.assertEqual(asked['outcome'], 'done', asked)
        self.assertEqual(asked['result']['page'], '#follow-ups')
        self.assertEqual(self.card(asked['action_id'])['status'], 'succeeded')
        old = self.card(waiting['id'])
        self.assertEqual(old['status'], 'declined')  # replaced at the instructor's request, never their rejection
        self.assertEqual(old['events'][-1]['detail'], {'error': REDO_REPLACED, 'replaced_by_request': True})
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual((shown['state'], shown['reason']), ('queued', 'revise'))
        self.assertNotIn('chat', shown)
        prompts = []

        def respond(c, prompt):
            prompts.append(prompt)
            proposing(MAIL)(c, prompt)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(respond)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertIn("Changes requested: Ask for a doctor's note.", prompts[0])
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual(shown['state'], 'ready')
        self.assertEqual([a['id'] for a in shown['actions'] if a['pending']], [shown['actions'][-1]['id']])
        self.assertNotEqual(shown['actions'][-1]['id'], waiting['id'])

    def test_cailyne_a_redo_while_the_follow_up_is_still_being_prepared_stops_it_and_runs_next(self):
        # A REDO WHILE PREPARING (requirement, 2026-10-21).
        self.thread(self.fake_mail())
        self.incoming()
        self.prepare(proposing(MAIL))
        session = self.chat()
        target = {'enrollment_id': self.alice, 'source_key': 'gmail:b1'}
        first = self.tool(session, 'redo_followup', {**target, 'changes': 'She takes Exam 2 on Monday upon her return.'})
        self.assertEqual(first['outcome'], 'done', first)
        started, release, seen = threading.Event(), threading.Event(), []
        self.addCleanup(release.set)

        def hold(c, prompt):
            proposing(MAIL)(c, prompt)
            started.set()
            release.wait(5)
            seen.append(c.stopping)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(hold)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.assertTrue(started.wait(5))
            proposed = self.run_row(first['result']['id'])
            self.assertEqual(proposed['state'], 'running')
            second = self.tool(session, 'redo_followup', {**target, 'changes': 'I will arrange her makeup for Monday when she returns.'})
            self.assertEqual(second['outcome'], 'done', second)
            self.assertIn('stopped', second['result']['note'])
            release.set()
            self.wait()
        self.assertEqual(seen, [True])
        stopped = self.run_row(first['result']['id'])
        self.assertEqual(stopped['state'], 'superseded')
        with connect(self.assistant.database) as db:
            cards = [r[0] for r in db.execute('SELECT action_id FROM student_support_suggestion_actions WHERE run_id=?', (stopped['id'],))]
        self.assertTrue(cards)
        for card in map(self.card, cards):  # never shown, never the instructor's rejection
            self.assertEqual((card['status'], card['events'][-1]['detail']), ('expired', REPLACED_WHILE_PREPARING))
        queued = self.run_row(second['result']['id'])
        self.assertEqual((queued['state'], queued['parent_id']), ('queued', stopped['id']))
        prompts = []

        def respond(c, prompt):
            prompts.append(prompt)
            proposing(MAIL)(c, prompt)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(respond)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertIn('Changes requested: She takes Exam 2 on Monday upon her return.', prompts[0])
        self.assertIn('Changes requested: I will arrange her makeup for Monday when she returns.', prompts[0])
        self.assertIn('superseded by an instructor request', prompts[0])
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual((shown['id'], shown['state']), (queued['id'], 'ready'))

    def test_jerry_a_chat_waiting_card_waits_for_the_reply_and_a_redo_after_a_change_starts_at_once(self):
        fake = self.fake_mail()
        self.thread(fake)
        self.incoming(body='I had a headache this morning. Could you excuse my absence today?')
        run, _ = self.prepare(proposing(MAIL))
        session = self.chat()
        reply = self.tool(session, 'draft_student_reply', {'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                                                           'subject': 'Re: Question', 'body': "Please send me a doctor's note."})
        track = self.tool(session, 'track_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'state': 'waiting',
                                                      'next': "Her doctor's note"})
        self.assertEqual(track['outcome'], 'waiting_for_approval', track)
        # Both show with the follow-up, in the order the chat made them.
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual([c['id'] for c in shown['chat']], [reply['action_id'], track['action_id']])
        # The tracking card waits for the reply: approved first, it is refused and the follow-up's own card is untouched.
        refused = self.approve(self.card(track['action_id']))
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn('send the reply first', refused.json['error'])
        self.assertTrue(self.card(track['action_id'])['pending'])
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'ready')
        self.assertEqual(fake.sent, [])
        sent = self.approve(self.card(reply['action_id']))
        self.assertEqual(sent.status_code, 200, sent.json)
        self.assertEqual(sent.json['result']['status'], 'accepted')
        tracked = self.approve(self.card(track['action_id']))  # Mail's accepted receipt counts before Gmail capture shows it
        self.assertEqual(tracked.status_code, 200, tracked.json)
        self.assertEqual(tracked.json['action']['status'], 'succeeded')
        # Tracking not made by the follow-up's own cards: its proposals no longer apply. Asked again now, the chat prepares the
        # follow-up again at once (nothing waits there to replace), and the new steps appear on the follow-up.
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual(shown['state'], 'superseded')
        self.assertNotIn('chat', shown)
        redo = self.tool(session, 'redo_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                                                    'changes': 'Thank her for the note when it comes.'})
        self.assertEqual(redo['outcome'], 'done', redo)
        self.assertEqual(redo['result']['page'], '#follow-ups')
        new = self.run_row(redo['result']['id'])
        self.assertEqual((new['state'], new['reason'], new['parent_id'], new['item_key']),
                         ('queued', 'revise', run['id'], 'conversation:gmail:b1'))

    def test_a_chat_that_only_prepares_changes_for_review_still_asks_before_a_redo(self):
        # A review-only chat does nothing by itself: there the redo is a card, shown with the follow-up it would prepare again.
        self.thread(self.fake_mail())
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        session = self.chat()
        session['_review_only'] = True
        asked = self.tool(session, 'redo_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'changes': 'Shorter.'})
        self.assertEqual(asked['outcome'], 'waiting_for_approval', asked)
        shown = self.listing()['items'][0]['suggestions']
        self.assertTrue(shown['actions'][0]['pending'])  # the follow-up's own step is untouched until the card is approved
        self.assertEqual([c['id'] for c in shown['chat']], [asked['action_id']])
        self.assertEqual(shown['chat'][0]['effect']['title'], 'Prepare the suggestions again')
        approved = self.approve(self.card(asked['action_id']))
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(self.card(run['actions'][0]['id'])['events'][-1]['detail'], {'error': REDO_REPLACED, 'replaced_by_request': True})

    # STILL NO CARD (requirement, 2026-10-12). On the live
    # record every chat redo since NO CARD FOR A REDO had become an Approve card all the same: the tool-use check called each
    # one uncertain ("The intent or supporting context of this change needs your review.").

    def checked(self, nouls=None, evaluate=None):
        """A chat whose turn has the tool-use check on, answered by a fake decision service; one step of the follow-up waits."""
        self.thread(self.fake_mail())
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'FACULTY_ASSISTANT_TOOL_GUIDANCE': '1'}).start()
        patch('jev.client.available', return_value=True).start()
        patch('jev.client.evaluate', side_effect=evaluate or (lambda state, questions: response(questions, nouls=nouls))).start()
        session = self.chat()
        session['_tool_guidance'] = ToolGuidance(self.assistant, session, "Change Alice's follow-up: ask for a doctor's note.")
        return session, run['actions'][0]

    def redo(self, session, changes="Ask for a doctor's note."):
        return self.tool(session, 'redo_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'changes': changes})

    def event(self, session, title):
        return self.tool(session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events', 'summary': 'Add it',
            'body': {'title': title, 'date': '2026-10-14', 'all_day': True, 'request_id': '$new_request_id'}})

    def test_an_uncertain_tool_use_check_makes_no_card_of_a_redo(self):
        session, waiting = self.checked({'intent_matches': .7, 'context_sufficient': .5})
        asked = self.redo(session)
        self.assertEqual(asked['outcome'], 'done', asked)
        self.assertEqual(self.card(asked['action_id'])['status'], 'succeeded')
        self.assertEqual(self.card(waiting['id'])['events'][-1]['detail'], {'error': REDO_REPLACED, 'replaced_by_request': True})
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual((shown['state'], shown['reason']), ('queued', 'revise'))
        self.assertNotIn('chat', shown)
        guide = session['_tool_guidance']
        self.assertEqual(guide.calls, 1)  # the check was made: it can still hold a redo
        self.assertEqual(guide.findings, [])  # and nothing is said to wait for review in the chat
        # Any other automatic change the same check is unsure of still waits for Approve.
        other = self.event(session, 'Office hours')
        self.assertEqual(other['outcome'], 'waiting_for_approval', other)
        self.assertIn('needs your review', other['why_approval'])

    def test_an_unavailable_or_spent_tool_use_check_makes_no_card_of_a_redo(self):
        def outage(state, questions):
            raise client.DecisionError('Synthetic outage')
        session, _ = self.checked(evaluate=outage)
        asked = self.redo(session)
        self.assertEqual(asked['outcome'], 'done', asked)
        self.assertIn('unavailable', self.event(session, 'Office hours')['why_approval'])
        spent = self.chat()
        spent['_tool_guidance'] = guide = ToolGuidance(self.assistant, spent, 'Change the follow-up again.')
        guide.calls = MAX_CHECKS - 2
        again = self.redo(spent, 'Shorter.')
        self.assertEqual(again['outcome'], 'done', again)
        self.assertEqual(guide.findings, [])  # never "waited for your approval"

    def test_a_redo_the_check_finds_unasked_for_is_held_and_nothing_is_replaced(self):
        session, waiting = self.checked({'intent_matches': .05})
        held = self.redo(session)
        self.assertEqual(held['outcome'], 'needs_context', held)
        self.assertIn('match', held['error'])
        self.assertNotIn('action_id', held)
        self.assertTrue(self.card(waiting['id'])['pending'])
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'ready')

    def test_a_corrective_pass_makes_no_card_of_a_redo(self):
        # The pass finishes what the instructor asked for in that message; its other new changes are cards as before.
        session, waiting = self.checked()
        session['_tool_followthrough'] = True
        asked = self.redo(session)
        self.assertEqual(asked['outcome'], 'done', asked)
        self.assertFalse(self.card(waiting['id'])['pending'])
        other = self.event(session, 'Office hours')
        self.assertEqual(other['outcome'], 'waiting_for_approval', other)
        self.assertEqual(other['why_approval'], 'This review prepares changes for your approval.')

    def test_a_preparation_you_ask_for_replaces_the_chats_unsent_cards_for_that_conversation_only(self):
        fake = self.fake_mail()
        self.thread(fake)
        self.thread(fake, 'b2', 'bob@example.test')
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        self.incoming(ident='a2', thread='b2', enrollment=self.bob)
        session = self.chat()
        mine = self.tool(session, 'draft_student_reply', {'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                                                          'subject': 'Re: Question', 'body': 'Yes, come on Monday.'})
        other = self.tool(session, 'draft_student_reply', {'enrollment_id': self.bob, 'source_key': 'gmail:b2',
                                                           'subject': 'Re: Question', 'body': 'Yes, come on Tuesday.'})
        retried = self.request(run, 'retry')  # the page's own Retry suggestions
        self.assertEqual(retried.status_code, 202, retried.json)
        replaced = self.card(mine['action_id'])
        self.assertEqual(replaced['status'], 'expired')
        self.assertEqual(replaced['events'][-1]['detail']['error'], CHAT_REPLACED)
        self.assertTrue(self.card(other['action_id'])['pending'])
        alice = next(i for i in self.listing()['items'] if i['enrollment_id'] == self.alice)['suggestions']
        self.assertNotIn('chat', alice)

    # SEVERAL AT ONCE (requirement, 2026-10-12). One brief read finds them; each is asked through its own redo; the chat shows one card for them all.

    def two(self):
        """Alice's follow-up with one step waiting and Bob's with nothing prepared yet, and a chat."""
        fake = self.fake_mail()
        self.thread(fake)
        self.thread(fake, 'b2', 'bob@example.test')
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, text='Alice asks to meet. I drafted a reply offering Monday.'))
        self.incoming(ident='a2', thread='b2', enrollment=self.bob, body='Could I take the quiz on Friday instead?')
        return self.chat(), run['actions'][0]

    def both(self):
        return [{'enrollment_id': self.alice, 'source_key': 'gmail:b1'}, {'enrollment_id': self.bob, 'source_key': 'gmail:b2'}]

    def many(self, session, followups=None, changes="Ask for a doctor's note."):
        return self.tool(session, 'redo_followups', {'followups': followups or self.both(), 'changes': changes})

    def groups(self):
        return self.client.get('/api/assistant/actions').json['batches']

    def suggested(self):
        return {item['enrollment_id']: item['suggestions'] for item in self.listing()['items']}

    def test_the_open_follow_ups_are_read_in_brief(self):
        session, _ = self.two()
        brief = self.tool(session, 'open_followups', {})
        self.assertEqual((brief['count'], brief['open'], brief['page']), (2, 2, '#follow-ups'))
        alice, bob = (next(row for row in brief['followups'] if row['enrollment_id'] == who) for who in (self.alice, self.bob))
        self.assertEqual((alice['student'], alice['source_key'], alice['title'], alice['state']),
                         ('Example, Alice', 'gmail:b1', 'Question', 'needs_reply'))
        self.assertEqual(alice['suggested_actions'], {'state': 'ready', 'waiting': 1, 'steps': ['Reply to the question']})
        self.assertEqual(alice['explanation'], 'Alice asks to meet. I drafted a reply offering Monday.')
        self.assertEqual((bob['student'], bob['source_key'], bob['suggested_actions']['waiting'], bob['suggested_actions']['steps']),
                         ('Example, Bob', 'gmail:b2', 0, []))  # nothing prepared for him yet
        self.assertNotIn('explanation', bob)
        # Far smaller than the page's own read, which carries every card whole.
        self.assertLess(len(json.dumps(brief)), len(json.dumps(self.listing())) / 3)
        found = self.tool(session, 'open_followups', {'find': 'offering  monday'})
        self.assertEqual(([row['enrollment_id'] for row in found['followups']], found['count'], found['open']), ([self.alice], 1, 2))
        self.assertEqual(self.tool(session, 'open_followups', {'find': 'no such words'})['followups'], [])
        # A resolved follow-up is left out.
        item = next(i for i in self.listing(False)['items'] if i['enrollment_id'] == self.bob)
        saved = self.client.post('/api/student-support', json={'request_id': 'settle-bob', 'kind': 'followup', 'enrollment_id': self.bob,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Quiz on Friday', 'state': 'resolved'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        left = self.tool(session, 'open_followups', {})
        self.assertEqual(([row['enrollment_id'] for row in left['followups']], left['open']), ([self.alice], 1))
        self.assertTrue(self.assistant.tools['open_followups'].read)  # a read: it may run in a selected area's first analysis

    def test_several_follow_ups_are_prepared_again_at_once_and_shown_as_one_card(self):
        session, waiting = self.two()
        result, event = self.assistant.run_tool(session, 'redo_followups', {'followups': self.both(), 'changes': "Ask for a doctor's note."})
        asked = json.loads(result)
        self.assertEqual([asked[k] for k in ('outcome', 'asked', 'started', 'waiting_for_approval', 'not_started')], ['done', 2, 2, 0, 0], asked)
        self.assertEqual([(r['student'], r['outcome']) for r in asked['results']], [('Example, Alice', 'done'), ('Example, Bob', 'done')])
        ids = [r['action_id'] for r in asked['results']]
        cards = [self.card(ident) for ident in ids]
        # NO CARD: each started at once, as one redo does, also where a step still waited.
        self.assertEqual([c['status'] for c in cards], ['succeeded', 'succeeded'])
        self.assertEqual([c['summary'] for c in cards], ['Prepare “Question” again with your changes: Example, Alice',
                                                         'Prepare “Question” again with your changes: Example, Bob'])
        self.assertEqual([(c['endpoint'], c['body']['text']) for c in cards], [('student_support.suggestion_redo', "Ask for a doctor's note.")] * 2)
        self.assertEqual(self.card(waiting['id'])['events'][-1]['detail'], {'error': REDO_REPLACED, 'replaced_by_request': True})
        self.assertEqual([(s['state'], s['reason']) for s in map(self.suggested().get, (self.alice, self.bob))], [('queued', 'revise')] * 2)
        # ONE CARD FOR ONE CHANGE: the receipts are one group, and the chat's line names its first, where the group's card is drawn.
        group, = self.groups()
        self.assertEqual((group['title'], group['route'], group['action_ids'], group['states'], group['pending']),
                         ("Prepare 2 follow-ups again: “Ask for a doctor's note.”", 'student_support.suggestion_redo', ids, {'succeeded': 2}, 0))
        self.assertEqual((asked['action_id'], event['action'], event['ok']), (ids[0], {'action_id': ids[0], 'outcome': 'done'}, True))
        # Each is prepared in its turn, with the same changes.
        prompts = []

        def respond(c, prompt):
            prompts.append(prompt)
            c.emit('assistant', text='Nothing to propose.')
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(respond)):
            for _ in ids:
                self.assertTrue(self.suggestions.dispatch_one())
                self.wait()
        self.assertEqual([("Changes requested: Ask for a doctor's note." in prompt) for prompt in prompts], [True, True])
        # Without changes it is each follow-up's plain Retry, and one follow-up alone makes no group.
        again = self.tool(session, 'redo_followups', {'followups': self.both()[:1]})
        self.assertEqual((again['outcome'], self.card(again['action_id'])['summary']), ('done', 'Prepare “Question” again: Example, Alice'))
        self.assertEqual(self.suggested()[self.alice]['reason'], 'retry')
        self.assertEqual(len(self.groups()), 1)

    def test_a_follow_up_that_cannot_be_prepared_again_leaves_the_others_started(self):
        session, waiting = self.two()
        item = next(i for i in self.listing(False)['items'] if i['enrollment_id'] == self.bob)
        saved = self.client.post('/api/student-support', json={'request_id': 'settle-bob', 'kind': 'followup', 'enrollment_id': self.bob,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Quiz on Friday', 'state': 'resolved'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        asked = self.many(session, [self.both()[1], *self.both(), {'enrollment_id': self.alice, 'source_key': 'gmail:b1'}])
        # A follow-up named twice is asked once; the resolved one is refused by the redo's own rule, with its words.
        self.assertEqual([asked[k] for k in ('outcome', 'asked', 'started', 'not_started')], ['done', 2, 1, 1], asked)
        bob, alice = asked['results']
        self.assertEqual((bob['student'], bob['outcome']), ('Example, Bob', 'failed'))
        self.assertIn('Only an open follow-up', bob['error'])
        self.assertEqual(alice['outcome'], 'done')
        self.assertFalse(self.card(waiting['id'])['pending'])
        group, = self.groups()
        self.assertEqual(group['states'], {'failed': 1, 'succeeded': 1})
        # When none can be, the answer says so and the step reads as failed.
        result, event = self.assistant.run_tool(session, 'redo_followups', {'followups': self.both()[1:]})
        self.assertIn('None of these follow-ups', json.loads(result)['error'])
        self.assertFalse(event['ok'])

    def test_a_review_only_chat_gets_one_card_with_approve_all(self):
        session, waiting = self.two()
        session['_review_only'] = True
        asked = self.many(session)
        self.assertEqual([asked[k] for k in ('outcome', 'started', 'waiting_for_approval')], ['waiting_for_approval', 0, 2], asked)
        self.assertTrue(self.card(waiting['id'])['pending'])  # nothing is replaced until the instructor approves
        ids = [r['action_id'] for r in asked['results']]
        group, = self.groups()
        self.assertEqual((group['action_ids'], group['pending'], group['route']), (ids, 2, 'student_support.suggestion_redo'))
        approved = self.client.post(f"/api/assistant/batches/{group['id']}/approve",
                                    json={'seen': {ident: self.card(ident)['seen'] for ident in ids}}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual([self.card(ident)['status'] for ident in ids], ['succeeded', 'succeeded'])
        self.assertEqual(self.card(waiting['id'])['events'][-1]['detail'], {'error': REDO_REPLACED, 'replaced_by_request': True})

    def guided(self, session, evaluate):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'FACULTY_ASSISTANT_TOOL_GUIDANCE': '1'}).start()
        patch('jev.client.available', return_value=True).start()
        patch('jev.client.evaluate', side_effect=evaluate).start()
        session['_tool_guidance'] = guide = ToolGuidance(self.assistant, session, "For every follow-up asking to meet, ask for a doctor's note.")
        return guide

    def test_the_tool_use_check_holds_only_the_follow_up_it_finds_unasked_for(self):
        session, waiting = self.two()
        guide = self.guided(session, lambda state, questions: response(
            questions, nouls={'intent_matches': .05} if self.bob in json.dumps(state['proposed']) else {'intent_matches': .6}))
        asked = self.many(session)
        alice, bob = asked['results']
        # Alice's check is unsure: no card of a redo (STILL NO CARD). Bob's is a clear mismatch: held, nothing recorded for him.
        self.assertEqual((alice['outcome'], bob['outcome']), ('done', 'needs_context'), asked)
        self.assertIn('match', bob['error'])
        self.assertNotIn('action_id', bob)
        self.assertEqual([asked[k] for k in ('outcome', 'started', 'not_started', 'action_id')], ['done', 1, 1, alice['action_id']])
        self.assertEqual(guide.calls, 2)
        self.assertEqual(self.groups(), [])  # one receipt is its own card
        self.assertEqual(self.suggested()[self.alice]['state'], 'queued')
        self.assertNotIn('reason', self.suggested()[self.bob])  # no run of his was started

    def test_follow_ups_past_the_tasks_last_tool_use_check_start_unchecked(self):
        # A task has a fixed number of checks. One redo the check cannot be asked about starts (STILL NO CARD); so does each of several.
        session, waiting = self.two()
        asked_of = []
        guide = self.guided(session, lambda state, questions: asked_of.append(state['proposed']['body']) or response(questions))
        guide.calls = MAX_CHECKS - 3  # one check is left for changes
        asked = self.many(session)
        self.assertEqual([r['outcome'] for r in asked['results']], ['done', 'done'], asked)
        self.assertEqual(len(asked_of), 1)
        self.assertIn(self.alice, asked_of[0])  # the first was checked, the second was not
        self.assertEqual(guide.findings, [])  # and nothing is said to wait for review
        self.assertEqual(self.groups()[0]['states'], {'succeeded': 2})

    def test_a_stop_a_limit_or_a_preparation_starts_none(self):
        session, waiting = self.two()
        refused = self.tool(session, 'redo_followups', {'followups': [{'enrollment_id': self.alice, 'source_key': f'gmail:x{n}'}
                                                                      for n in range(REDO_MOST + 1)]})
        self.assertIn(f'at most {REDO_MOST} follow-ups', refused['error'])
        for unnamed in ([], [{'enrollment_id': self.alice}], [{'source_key': 'gmail:b1'}], 'gmail:b1'):
            self.assertIn('enrollment_id with source_key', self.tool(session, 'redo_followups', {'followups': unnamed})['error'])
        self.assertEqual(self.groups(), [])
        # A follow-up preparation never starts others.
        answers = []

        def respond(c, prompt):
            result, _ = c.service.run_tool(c.session, 'redo_followups', {'followups': self.both()})
            answers.append(result)
            c.emit('assistant', text='Nothing to propose.')
        self.assertTrue(self.suggestions.process_one())
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(respond)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertIn('background work cannot start another preparation', answers[0])
        # Stopped while the first one is checked: that one is not made and the rest are not started.
        stopped = []
        guide = self.guided(session, lambda state, questions: stopped.append(1) or response(questions))
        guide.cancelled = lambda: bool(stopped)
        asked = self.many(session)
        self.assertEqual([r['outcome'] for r in asked['results']], ['needs_context', 'not_started'], asked)
        self.assertIn('stopped', asked['results'][1]['error'])
        self.assertEqual((asked['outcome'], asked['started']), ('failed', 0))
        self.assertTrue(self.card(waiting['id'])['pending'])
        self.assertEqual(self.groups(), [])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assistant_actions WHERE endpoint='student_support.suggestion_redo'").fetchone()[0], 0)

    def test_a_tracked_follow_up_whose_conversation_was_joined_into_another_is_prepared_again_by_its_own_key(self):
        # (requirement, 2026-10-15)
        self.incoming('w1', thread='ae', at='2026-10-05T16:47:00Z', body='I will miss class today.')
        self.incoming('w2', thread='ae', at='2026-10-05T20:41:00Z', author='instructor', body='Thanks for letting me know.')
        self.incoming('f1', thread='fd', at='2026-10-06T19:41:00Z', body='I will also miss Friday.')
        [item] = self.listing(False)['items']
        tracked = self.client.post('/api/student-support', json={'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:fd',
            'observed': item['observed'], 'title': 'Friday', 'state': 'todo', 'request_id': 'track'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        joined = self.client.post('/api/student-support/followups/join', json={'enrollment_id': self.alice, 'source_key': 'gmail:ae',
            'with_key': 'gmail:fd', 'request_id': 'join'}, headers=HEADERS)
        self.assertEqual(joined.json['into_key'], 'gmail:ae', joined.json)
        self.assertEqual([i['source_key'] for i in self.listing(False)['items']], ['gmail:fd'])
        # Its unsent chat reply, in the other thread of the joined conversation, is the follow-up's and a redo replaces it (the
        # requirement, 2026-10-15: Fiona's preparation proposed nothing while her old chat email still waited unseen).
        self.incoming('w3', thread='ae', at='2026-10-07T09:00:00Z', body='Where do I take the quiz?')
        fake = self.fake_mail()
        self.thread(fake, 'ae')
        self.thread(fake, 'fd')
        reply = self.tool(self.chat(), 'draft_student_reply', {'enrollment_id': self.alice, 'source_key': 'gmail:ae',
                                                                'subject': 'Re: Question', 'body': 'Come after class on Friday.'})
        self.assertEqual(self.card(reply['action_id'])['body']['reply_thread_id'], 'ae')
        for key in ('gmail:fd', 'gmail:ae'):
            asked = self.client.post(REDO, json={'enrollment_id': self.alice, 'source_key': key, 'text': 'Offer Tuesday.',
                                                 'request_id': 'redo-' + key}, headers=HEADERS)
            self.assertEqual(asked.status_code, 202, asked.json)
            self.assertEqual(self.run_row(asked.json['id'])['item_key'], 'conversation:gmail:fd')
        self.assertEqual(self.card(reply['action_id'])['events'][-1]['detail']['error'], CHAT_REPLACED)

    def test_the_route_is_idempotent_and_only_chat_may_use_it(self):
        self.assertEqual(policy.base_rule('student_support.suggestion_redo').tier, 'auto')
        self.assertTrue(policy.base_rule('student_support.suggestion_redo').request_id)
        self.assertEqual(policy.base_rule('student_support.suggestion_request').tier, 'never')  # the page's own controls stay yours
        self.incoming()
        body = {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'text': 'Offer Tuesday instead.', 'request_id': 'redo-1'}
        first = self.client.post(REDO, json=body, headers=HEADERS)
        self.assertEqual(first.status_code, 202, first.json)
        self.assertEqual(self.client.post(REDO, json=body, headers=HEADERS).json['id'], first.json['id'])
        self.assertEqual(self.client.post(REDO, json={**body, 'text': 'Something else.'}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.post(REDO, json={**body, 'source_key': '', 'request_id': 'redo-2'}, headers=HEADERS).status_code, 400)
        # A follow-up preparation never starts another one.
        answers = []

        def respond(c, prompt):
            result, event = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': REDO, 'summary': 'Prepare again',
                'body': {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'request_id': '$new_request_id'}})
            answers.append(result)
            c.emit('assistant', text='Nothing to propose.')
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(respond)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertIn('background work cannot start another preparation', answers[0])


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(RedoTests(name) for name in RedoTests.__dict__ if name.startswith('test_'))
