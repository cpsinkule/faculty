"""One question's rubric holds back only its own grades (requirement, 2026-09-25).

The rubric finalization is still ONE mandatory confirmation that approves every rubric, but what it decided is judged question by
question: a question STANDS while its input is what the plan's latest confirmation covered (and since 2026-09-28 a batch takes only the
questions that stand), and an automatic grade stays final while the confirmation its graders read gave its question the guidance that
stands now. A suspension of the whole assessment still suspends every automatic grade. Synthetic rubrics and loopback models; nothing leaves the machine."""
import copy
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from grading.rubric_quality import BUILTIN, guidance_key, quality_flags, question_input, question_inputs
from rosters import connect
from fake_openai import grade_answer, payload_of, prompt_name, reconcile_answer
from frq_fixtures import finalize_rubrics, page
import test_grading_pipeline as pipeline
from test_grading_cloud import endpoint


def silent_reviewer(owner, role, endpoint, request, settings):
    """The rubric reviewer as `finalize_rubrics` scripts it: one question per request, nothing to clarify or pair."""
    rubric = request['payload']['question']['rubric']
    return {'metadata': {}, 'raw': {}, 'output': {'summary': 'Nothing to change.', 'suspected_errors': [],
            'items': [{'item_id': item['id'], 'clarification': '', 'excludes': [], 'reason': ''} for item in rubric['items']]}}


CLARIFIED = 'Credit the setup when the unknown is isolated on one side, even with an arithmetic slip afterwards.'


class RubricScope(pipeline.Pipeline):
    """Two questions of one plan, each with the Pipeline rubric and key, graded by a team that agrees: both finalize automatically.
    The grading team is test_grading_teams' (a fixture class without tests of its own is subclassed so none of those run here)."""
    legacy_workflow = False

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        saved['settings']['tiers']['cloud_standard']['verifier'] = 'gamma-vision'
        saved['settings']['endpoints'].extend([endpoint(self.host, 'delta-vision'), endpoint(self.host, 'epsilon-vision'), endpoint(self.host, 'zeta-vision')])
        self.frq.save_settings({'version': saved['version'], 'settings': saved['settings']})
        aid = self.plan['id']
        plan = self.frq.assignment(aid)
        plan = self.frq.save_question(aid, {'version': plan['version'], 'question_key': 'q2', 'assignment_version_id': self.version_id,
            'content': {'title': 'Question 2', 'max_points': 10, 'problem': 'Solve',
                        'regions': [{'page': 1, 'region': {'x': .1, 'y': .5, 'width': .8, 'height': .4}}]}})
        self.q2 = next(q for q in plan['questions'] if q['question_key'] == 'q2')['question_id']
        q = self.question(self.q2)
        q = next(q for q in self.frq.import_rubric(aid, self.q2, {'version': q['version'], 'source_text': 'Second rubric', 'rules': pipeline.RULES})['questions']
                 if q['question_id'] == self.q2)
        self.frq.approve_rubric(aid, self.q2, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'], 'acknowledge_rules': True})
        plan, q = self.frq.assignment(aid), self.question(self.q2)
        self.frq.save_answer_key(aid, {'version': plan['version'], 'questions': [{'question_id': self.q2, 'version': q['version'],
            'answer_key': [{'label': 'x', 'accept': ['x=6']}], 'worked_solution': '3x = 18, so x = 6'}]})

    def queue(self, mode='cloud_standard', questions=None, **extra):
        return super().queue(mode, questions or [self.qid, self.q2], **extra)  # Every batch here grades both questions.

    # Fixtures ---------------------------------------------------------------------------------------------------------------
    def question(self, qid):
        return next(q for q in self.frq.assignment(self.plan['id'])['questions'] if q['question_id'] == qid)

    def edit(self, qid, **data):
        q = self.question(qid)
        return self.frq.edit_rubric(self.plan['id'], qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'], **data})

    def by_question(self):
        found = {}
        for row in self.frq.responses(self.plan['id']):
            self.assertNotIn(row['question_id'], found)  # One paper: one response per question.
            found[row['question_id']] = row
        return found

    def states(self):
        return {qid: row['state'] for qid, row in self.by_question().items()}

    def graded(self):
        """Both questions graded in one batch; each response is final by team agreement."""
        self.grade(pipeline.CORRECT)
        responses = self.by_question()
        self.assertEqual(set(responses), {self.qid, self.q2})
        for qid, row in responses.items():
            self.assertEqual(row['state'], 'auto_accepted', qid)
            self.assertEqual(self.frq.response(row['id'])['acceptance']['basis'], 'team_consensus')
        return responses

    def start(self):
        """A batch of both questions queued under the confirmation of the moment and not yet run (Pipeline.grade's fixtures)."""
        def answer(body, host):
            name = prompt_name(body)
            if name == 'reconcile':
                return reconcile_answer(payload_of(body))
            return grade_answer(payload_of(body), ('R1',), image=name in ('grade', 'primary_vision', 'second_vision'))
        self.host.script = answer
        patch.stopall()
        patch('grading.extraction.extract', return_value={'metadata': {'registered': True, 'crop_bounds': [0, 0, 100, 100]},
            'images': {k: page() for k in ('original', 'grayscale', 'contrast', 'ink', 'cloud')}}).start()
        patch.object(self.frq, 'provider', return_value=pipeline.Reader(pipeline.CORRECT)).start()
        self.addCleanup(patch.stopall)
        return self.queue()

    def queued(self):
        """(responses waiting for review, question -> (waiting, to grade again)) as the Review step counts them."""
        queue = self.frq.review_queue(self.plan['id'])
        return queue['waiting'], {q['question_id']: (q['waiting'], q['regrade']) for q in queue['questions']}

    def suspended(self):
        """The questions of every team acceptance suspended so far, in order."""
        with connect(self.path) as db:
            return [r[0] for r in db.execute("""SELECT a.question_id FROM frq_team_acceptance_events e JOIN frq_team_acceptances a ON a.id=e.acceptance_id
                WHERE e.kind='suspended' ORDER BY e.rowid""")]  # the order they were made in: event IDs are random

    def final(self):
        """(question -> kind of final decision, response -> reason skipped), by the one rule every export uses."""
        with self.frq.rosters.lock(), connect(self.path) as db:
            _, decisions, skipped = self.frq.final_decisions(db, self.plan['id'])
        return {d['row']['question_id']: d['kind'] for d in decisions}, {s['response_id']: s['reason'] for s in skipped}

    def exported(self):
        records = json.loads(self.frq.export(self.plan['id'], 'audit')[0])['records']
        return {r['question_key']: (r['final']['status'], r['final'].get('basis')) for r in records}

    def clarify(self, qid, note=CLARIFIED):
        def edit(questions):
            next(q for q in questions if q['question_id'] == qid)['clarifications'] = {'R2': note}
        return edit

    def confirmed_event(self):
        with connect(self.path) as db:
            return json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='rubric_quality_confirmed' ORDER BY rowid DESC LIMIT 1").fetchone()[0])

    BOTH_FINAL = {'q1': ('automatic', 'team_consensus'), 'q2': ('automatic', 'team_consensus')}


class OneQuestionsRubric(RubricScope):
    def test_editing_one_questions_lines_holds_back_only_that_questions_grades(self):
        aid = self.plan['id']
        before = self.graded()
        first = self.frq.rubric_quality(aid)['review']['id']
        with connect(self.path) as db:  # As before the confirmation for review existed: a question is ready by its finalization alone.
            db.execute('DROP TRIGGER frq_rubric_review_ready_retained')
            db.execute('DELETE FROM frq_rubric_review_ready')
        self.edit(self.qid, lines=[{'item_id': 'R2', 'points': 3}])
        # Only the edited question's automatic grade is suspended, and only it is graded again; the other stays final.
        self.assertEqual(self.suspended(), [self.qid])
        with connect(self.path) as db:
            reasons = {r[0] for r in db.execute("SELECT reason FROM frq_team_acceptance_events")}
        self.assertEqual(reasons, {'The instructor edited rubric lines.'})
        self.assertFalse(self.frq.response(before[self.qid]['id'])['acceptance']['active'])
        self.assertTrue(self.frq.response(before[self.q2]['id'])['acceptance']['active'])
        self.assertEqual(self.states(), {self.qid: 'stale', self.q2: 'auto_accepted'})
        self.assertEqual(self.queued(), (0, {self.qid: (0, 1), self.q2: (0, 0)}))  # Nothing waits for review; the edited question is graded again.
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['state'], status['review']), ('needed', None))
        self.assertEqual((set(status['standing']), status['held']), ({self.q2}, ['Question 1']))
        self.assertEqual((status['standing'][self.q2]['review_id'], status['standing'][self.q2]['rubric_id']), (first, self.question(self.q2)['rubric_id']))
        ready = status['readiness']
        self.assertEqual((ready[self.q2]['ready'], ready[self.q2]['by_finalization']), (True, True))
        self.assertEqual((ready[self.qid]['ready'], status['unconfirmed']), (False, ['Question 1']))
        kinds, skipped = self.final()
        self.assertEqual(kinds, {self.q2: 'automatic'})
        self.assertIn(before[self.qid]['id'], skipped)
        self.assertEqual(self.exported(), {'q2': ('automatic', 'team_consensus')})
        # The finalization gates each question (requirement, 2026-09-28): the untouched question still grades under the confirmation
        # that stands for it, and the edited one is left out, named, until it is finalized again. Alone, it is refused as before.
        for qids in ([self.q2], [self.qid, self.q2]):
            preview = self.frq.preview(aid, {'question_ids': qids, 'mode': 'cloud_standard'})
            self.assertEqual(preview['rubric_finalization'], first)
            self.assertEqual([item['question_id'] for item in preview['held_questions']], [q for q in qids if q == self.qid])
            self.assertEqual({m['question_id'] for m in preview['eligible']} - {self.q2}, set())
        with self.assertRaisesRegex(Conflict, 'rubric finalization'):
            self.frq.preview(aid, {'question_ids': [self.qid], 'mode': 'cloud_standard'})
        # Finalizing again keeps the untouched question as it was: its entry is unchanged, so its grade stays final.
        confirmed = finalize_rubrics(self.frq, aid)
        self.assertEqual(confirmed['confirmation'], {'kept': 1, 'changed': 1, 'suspended': 0})
        self.assertEqual(self.suspended(), [self.qid])
        self.assertEqual(self.states(), {self.qid: 'stale', self.q2: 'auto_accepted'})
        self.assertEqual(self.exported(), {'q2': ('automatic', 'team_consensus')})
        self.assertEqual(self.frq.preview(aid, {'question_ids': [self.q2], 'mode': 'cloud_standard'})['rubric_finalization'], confirmed['review']['id'])

    def test_a_new_interpretation_of_one_rubric_suspends_only_its_question_once(self):
        aid = self.plan['id']
        before = self.graded()

        def reinterpret():
            q = self.question(self.q2)
            rules = json.loads(json.dumps(q['rubric']['rules']))
            item = rules['items'][1]
            item['follow_through'] = not item.get('follow_through')
            item['repeat_penalty_for_same_originating_error'] = not item['follow_through']
            self.frq.import_rubric(aid, self.q2, {'version': q['version'], 'source_text': q['rubric']['source_text'],
                                                  'source_artifact_id': q['rubric']['source_artifact_id'], 'rules': rules})
        with patch.object(self.frq, 'suspend_acceptance', wraps=self.frq.suspend_acceptance) as suspend:
            reinterpret()
            reinterpret()  # The confirmed finalization no longer stood for the replaced revision: nothing more to suspend.
        self.assertEqual(suspend.call_count, 1)
        self.assertEqual(suspend.call_args.kwargs, {'question_ids': [self.q2]})
        self.assertEqual(self.suspended(), [self.q2])
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'stale'})
        self.assertEqual(self.final()[0], {self.qid: 'automatic'})
        self.assertIn(before[self.q2]['id'], self.final()[1])

    def test_a_corrected_line_carries_the_finalization_and_the_other_questions_grades_stay_final(self):
        aid = self.plan['id']
        before = self.graded()
        first = self.frq.rubric_quality(aid)['review']
        q = self.question(self.qid)
        result = self.frq.correct_rubric_line(aid, self.qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                                              'item_id': 'R2', 'description': 'Set up the equation $$3x - 8 = 10$$ correctly'})
        self.assertTrue(result['correction']['finalization_carried'])
        status = self.frq.rubric_quality(aid)
        carried = status['review']
        self.assertEqual(status['state'], 'confirmed')
        self.assertNotEqual(carried['id'], first['id'])
        # The carried confirmation records what it covers, question by question: the corrected question's new input, the other's as before.
        self.assertEqual(carried['policy']['inputs'], question_inputs(status['context']))
        self.assertEqual(carried['policy']['inputs'][self.q2], first['policy']['inputs'][self.q2])
        self.assertNotEqual(carried['policy']['inputs'][self.qid], first['policy']['inputs'][self.qid])
        with connect(self.path) as db:
            self.assertEqual(self.frq.confirmation_record(db, carried['id'])['inputs'], carried['policy']['inputs'])
        # The other question's grade names the older confirmation, which gave it exactly the guidance that stands now.
        self.assertEqual(self.frq.response(before[self.q2]['id'])['result']['signals']['scope']['rubric_quality'], first['id'])
        self.assertEqual(self.suspended(), [self.qid])
        self.assertEqual(self.states(), {self.qid: 'stale', self.q2: 'auto_accepted'})
        self.assertEqual(self.final()[0], {self.q2: 'automatic'})
        self.assertEqual(self.exported(), {'q2': ('automatic', 'team_consensus')})

    def test_a_comment_holds_its_question_without_suspending_anything_and_clearing_it_stands_again(self):
        aid = self.plan['id']
        before = self.graded()
        rubrics = {qid: self.question(qid)['rubric_id'] for qid in (self.qid, self.q2)}
        self.edit(self.qid, comments={'R2': 'Accept 18/3 for x = 6 when the division is shown.'})
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['review'], set(status['standing']), status['held']), (None, {self.q2}, ['Question 1']))
        ticks = {qid: self.frq.response(before[qid]['id'])['result']['applications'] for qid in rubrics}
        self.assertEqual(quality_flags(status, self.qid, rubrics[self.qid], ticks[self.qid])['reasons'],
                         ["Rubric quality check: finish the rubric finalization for this question's current rubric before trusting this proposal."])
        self.assertEqual(quality_flags(status, self.q2, rubrics[self.q2], ticks[self.q2])['reasons'], [])
        # Held, never suspended: a comment is not a rubric revision.
        self.assertEqual(self.suspended(), [])
        self.assertTrue(self.frq.response(before[self.qid]['id'])['acceptance']['active'])
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'auto_accepted'})
        self.assertEqual(self.queued(), (1, {self.qid: (1, 0), self.q2: (0, 0)}))
        kinds, skipped = self.final()
        self.assertEqual((kinds, skipped), ({self.q2: 'automatic'}, {before[self.qid]['id']: 'The current rubric quality policy requires instructor review.'}))
        self.assertEqual(self.exported(), {'q2': ('automatic', 'team_consensus')})
        # Clearing the comment gives back exactly what the confirmation covered: it is current again and both grades are final.
        self.edit(self.qid, comments={'R2': ''})
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['state'], set(status['standing']), status['held']), ('confirmed', {self.qid, self.q2}, []))
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'auto_accepted'})
        self.assertEqual(self.exported(), self.BOTH_FINAL)
        self.assertEqual(self.suspended(), [])


class Confirmations(RubricScope):
    def test_asking_the_reviewer_again_suspends_nothing_and_unchanged_questions_stay_final(self):
        aid = self.plan['id']
        self.graded()
        first = self.frq.rubric_quality(aid)
        queued = self.frq.queue_rubric_quality(aid, {'request_id': str(uuid.uuid4()), 'input_sha256': first['input_sha256']})
        self.assertEqual((queued['state'], queued['review']), ('pending', None))
        self.assertEqual({qid: s['review_id'] for qid, s in queued['standing'].items()}, {self.qid: first['review']['id'], self.q2: first['review']['id']})
        self.assertEqual((self.suspended(), queued['held']), ([], []))
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'auto_accepted'})
        self.assertEqual(self.exported(), self.BOTH_FINAL)
        with patch.object(self.frq, 'cloud_call', side_effect=silent_reviewer), patch.object(self.frq, 'rubric_problem_images', return_value=[]):
            for _ in range(5):
                if self.frq.rubric_quality(aid)['state'] not in ('pending', 'running'):
                    break
                self.frq.process_cloud()
        checked = self.frq.rubric_quality(aid)
        self.assertEqual(checked['state'], 'needs_review')
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'auto_accepted'})  # Still final while the findings wait for the instructor.
        # Confirmed as it stands: every question keeps the guidance it had, so nothing goes back to review.
        confirmed = self.frq.confirm_rubric_quality(aid, {'version': checked['version'], 'check_id': checked['check']['id'], 'input_sha256': checked['input_sha256'],
                                                          'confirm_review': True, 'questions': checked['check']['result']['questions']})
        self.assertEqual(confirmed['confirmation'], {'kept': 2, 'changed': 0, 'suspended': 0})
        self.assertNotEqual(confirmed['review']['id'], first['review']['id'])
        self.assertEqual(self.suspended(), [])
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'auto_accepted'})
        self.assertEqual(self.exported(), self.BOTH_FINAL)

    def test_a_new_confirmation_keeps_the_grades_of_every_question_whose_entry_it_left_unchanged(self):
        aid = self.plan['id']
        before = self.graded()
        result = finalize_rubrics(self.frq, aid, edit=self.clarify(self.qid))
        self.assertEqual(result['confirmation'], {'kept': 1, 'changed': 1, 'suspended': 1})
        event = self.confirmed_event()
        self.assertEqual((event['kept'], event['changed'], event['suspended']), ([self.q2], [self.qid], 1))
        self.assertEqual(self.suspended(), [self.qid])
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'auto_accepted'})
        kinds, skipped = self.final()
        self.assertEqual((kinds, list(skipped)), ({self.q2: 'automatic'}, [before[self.qid]['id']]))
        self.assertEqual(self.exported(), {'q2': ('automatic', 'team_consensus')})
        # The same entries confirmed once more change nothing, and nothing is reinstated: suspensions stay.
        again = finalize_rubrics(self.frq, aid, edit=lambda questions: None)
        self.assertEqual(again['confirmation'], {'kept': 2, 'changed': 0, 'suspended': 0})
        self.assertEqual(self.suspended(), [self.qid])
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'auto_accepted'})

    def test_a_finalization_that_puts_the_guidance_back_puts_back_its_grades(self):
        """GUIDANCE PUT BACK (requirement, 2026-10-15: finalizing Quiz 2's makeup Version E changed Versions A-D's guidance and sent
        612 graded answers back to review). A grade suspended because its question's guidance changed counts again once a later
        finalization gives the question back the guidance it was graded under; a grade the instructor reviewed since is not touched."""
        aid = self.plan['id']
        before = self.graded()
        original = {q['question_id']: copy.deepcopy(q.get('clarifications') or {}) for q in self.frq.rubric_quality(aid)['review']['policy']['questions']}
        self.assertEqual(finalize_rubrics(self.frq, aid, edit=self.clarify(self.qid))['confirmation'], {'kept': 1, 'changed': 1, 'suspended': 1})
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'auto_accepted'})

        def restore(questions):
            next(q for q in questions if q['question_id'] == self.qid)['clarifications'] = original[self.qid]
        again = finalize_rubrics(self.frq, aid, edit=restore)
        self.assertEqual(again['confirmation'], {'kept': 1, 'changed': 1, 'suspended': 0, 'reinstated': 1})
        self.assertEqual(self.confirmed_event()['reinstated'], 1)
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'auto_accepted'})
        self.assertEqual(self.exported(), self.BOTH_FINAL)
        self.assertTrue(self.frq.response(before[self.qid]['id'])['acceptance']['active'])
        # Changed and put back once more: suspended and reinstated again, each suspension reinstated once.
        finalize_rubrics(self.frq, aid, edit=self.clarify(self.qid))
        self.assertEqual(finalize_rubrics(self.frq, aid, edit=restore)['confirmation']['reinstated'], 1)
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'auto_accepted'})

    def test_a_changed_pair_for_one_question_sends_only_its_grades_back_to_review(self):
        aid = self.plan['id']
        before = self.graded()

        def pair(questions):
            next(q for q in questions if q['question_id'] == self.q2)['pairs'] = [['R2', 'R3']]
        result = finalize_rubrics(self.frq, aid, edit=pair)
        self.assertEqual(result['confirmation'], {'kept': 1, 'changed': 1, 'suspended': 1})
        self.assertEqual(self.suspended(), [self.q2])
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'grading_review'})
        kinds, skipped = self.final()
        self.assertEqual(kinds, {self.qid: 'automatic'})
        self.assertEqual(skipped, {before[self.q2]['id']: 'Automatic acceptance for this question is suspended; this score waits for your review.'})
        self.assertEqual(self.exported(), {'q1': ('automatic', 'team_consensus')})

    def test_a_batch_frozen_under_an_earlier_confirmation_finalizes_only_where_its_questions_guidance_is_unchanged(self):
        aid = self.plan['id']
        job = self.start()
        first = self.frq.rubric_quality(aid)['review']['id']
        # Before it runs, the instructor confirms again with another clarification for the first question only.
        second = finalize_rubrics(self.frq, aid, edit=self.clarify(self.qid))
        self.assertEqual(second['confirmation'], {'kept': 1, 'changed': 1, 'suspended': 0})
        self.run_all()
        self.assertEqual(self.frq.job(job['id'])['status'], 'review_ready')
        responses = {qid: self.frq.response(row['id']) for qid, row in self.by_question().items()}
        for response in responses.values():
            self.assertEqual(response['result']['signals']['scope']['rubric_quality'], first)  # Its graders read the confirmation it was frozen under.
            self.assertEqual(response['result']['signals']['committee']['status'], 'resolved')
        self.assertEqual((responses[self.q2]['acceptance']['basis'], responses[self.q2]['acceptance']['kind']), ('team_consensus', 'automatic'))
        self.assertIsNone(responses[self.qid]['acceptance'])  # Graded without the clarification that now stands: it waits for the instructor.
        self.assertEqual(self.states(), {self.qid: 'proposed', self.q2: 'auto_accepted'})
        self.assertEqual(self.exported(), {'q2': ('automatic', 'team_consensus')})
        self.assertEqual(self.suspended(), [])

    def test_a_whole_plan_suspension_still_suspends_every_automatic_grade_of_the_plan(self):
        aid = self.plan['id']
        self.graded()
        with self.frq.rosters.lock(), connect(self.path) as db:
            self.assertEqual(self.frq.suspend_acceptance(db, aid, 'Canary failed on alpha-vision.', question_ids=[self.q2]), 1)
            self.assertEqual(self.frq.suspend_acceptance(db, aid, 'Canary failed on alpha-vision.'), 1)  # Every other one of the plan.
            self.assertEqual(self.frq.suspend_acceptance(db, aid, 'Canary failed on alpha-vision.'), 0)  # Nothing is suspended twice.
        self.assertEqual(self.suspended(), [self.q2, self.qid])
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'grading_review'})
        with self.assertRaisesRegex(Conflict, 'no current approved responses'):
            self.frq.export(aid, 'audit')
        # A confirmation that changes nothing reinstates nothing.
        self.assertEqual(finalize_rubrics(self.frq, aid, edit=lambda questions: None)['confirmation'], {'kept': 2, 'changed': 0, 'suspended': 0})
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'grading_review'})


class CancelledCanary(RubricScope):
    def test_what_a_cancelled_canary_suspended_is_reinstated_by_record_and_nothing_else(self):
        """2026-09-28: a batch cancelled during its canary had the canary settle as failed (the cancel closed its requests) and
        suspend every automatic grade of Quiz 2. The instructor chose to reinstate them: a recorded reinstatement per suspension."""
        aid = self.plan['id']
        self.graded()
        with self.frq.rosters.lock(), connect(self.path) as db:
            jid = db.execute("SELECT id FROM frq_jobs ORDER BY created_at DESC LIMIT 1").fetchone()[0]
            review = db.execute("SELECT id FROM frq_responses LIMIT 1").fetchone()
            db.execute("PRAGMA foreign_keys=OFF")  # the historical rows as the bug left them (their canary review is not needed here)
            db.execute("UPDATE frq_jobs SET cancel_requested=1,status='failed' WHERE id=?", (jid,))
            db.execute("INSERT INTO frq_canary_runs VALUES('run1',?,'alpha','s','failed',NULL,NULL,'{}','t','t')", (jid,))
            db.execute("INSERT INTO frq_canary_items VALUES('item1','run1',?,'none','failed',1,NULL,NULL,'Cancelled with its batch.','t')", (review[0],))
            self.frq.event(db, aid, "canary_failed", {"job_id": jid, "endpoint": "alpha", "agreement": None, "baseline": None}, "worker")
            self.assertEqual(self.frq.suspend_acceptance(db, aid, 'Canary failed on alpha.'), 2)
            db.commit()
        # A later change suspends one question's grade for its own reason: that grade stays with the instructor.
        with self.frq.rosters.lock(), connect(self.path) as db:
            self.assertEqual(self.frq.suspend_acceptance(db, aid, 'Rubric changed.', question_ids=[self.q2]), 0)  # already suspended
            db.execute("INSERT INTO frq_team_acceptance_events(acceptance_id,kind,reason,actor,created_at) SELECT a.id,'suspended','Rubric changed.','worker','2999-01-01T00:00:00+00:00' FROM frq_team_acceptances a WHERE a.question_id=?", (self.q2,))
            db.commit()
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'grading_review'})
        done = self.frq.reinstate_cancelled_canary(jid, 'reinstate-1')
        self.assertEqual((done['reinstated'], [r['why'] for r in done['skipped']]), (1, ['suspended for another reason too']))
        self.assertEqual(self.states(), {self.qid: 'auto_accepted', self.q2: 'grading_review'})
        # Every suspension is still on record. The first two came from ONE whole-plan call, which suspends in no particular order.
        self.assertEqual(sorted(self.suspended()), sorted([self.qid, self.q2, self.q2]))
        self.assertEqual(self.frq.reinstate_cancelled_canary(jid, 'reinstate-1')['replayed'], True)
        self.assertEqual(self.frq.reinstate_cancelled_canary(jid, 'reinstate-2')['reinstated'], 0)  # nothing twice
        self.assertEqual(self.frq.job(jid)['status'], 'cancelled')
        with connect(self.path) as db:
            with self.assertRaisesRegex(Exception, 'immutable'):
                db.execute("UPDATE frq_team_acceptance_reinstatements SET reason='x'")
            events = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='acceptance_reinstated'")]
        self.assertEqual([(e['reinstated'], e['skipped']) for e in events], [(1, {'suspended for another reason too': 1})] * 1 + [(0, {'suspended for another reason too': 1})])
        with self.assertRaisesRegex(Conflict, 'cancelled batch'):
            with connect(self.path) as db:
                db.execute("UPDATE frq_jobs SET cancel_requested=0 WHERE id=?", (jid,)); db.commit()
            self.frq.reinstate_cancelled_canary(jid, 'reinstate-3')


class Records(RubricScope):
    def test_an_earlier_confirmation_is_read_from_its_check_only_where_that_reproduces_its_fingerprint(self):
        aid = self.plan['id']
        before = self.graded()
        status = self.frq.rubric_quality(aid)
        review, expected = status['review'], question_inputs(status['context'])
        self.assertEqual(review['policy']['inputs'], expected)
        legacy_policy = json.dumps({k: v for k, v in review['policy'].items() if k != 'inputs'})  # As confirmed before 2026-09-25.
        tampered, legacy = f'tampered-{uuid.uuid4()}', f'legacy-{uuid.uuid4()}'
        with connect(self.path) as db:
            self.assertEqual(self.frq.confirmation_record(db, review['id']), {'policy': review['policy'], 'inputs': expected})
            self.assertIsNone(self.frq.confirmation_record(db, None))
            self.assertIsNone(self.frq.confirmation_record(db, 'no-such-confirmation'))
            # A confirmation whose check input does not reproduce its fingerprint: nothing is guessed from it.
            db.execute('INSERT INTO frq_rubric_quality_reviews VALUES(?,?,?,?,?,?,?)', (tampered, aid, review['check_id'], 'f' * 64, legacy_policy, 'workspace', 'now'))
            self.assertIsNone(self.frq.confirmation_record(db, tampered)['inputs'])
        # The plan no longer matches its confirmation, and its latest confirmation says nothing per question: the plan waits as before.
        self.edit(self.qid, comments={'R2': 'Accept 18/3.'})
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['review'], status['standing'], sorted(status['held'])), (None, {}, ['Question 1', 'Question 2']))  # Listed by question ID: compared as a set.
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'grading_review'})
        with self.assertRaisesRegex(Conflict, 'no current approved responses'):
            self.frq.export(aid, 'audit')
        self.assertEqual(self.suspended(), [])
        # One from before the inputs were recorded is read from its check, which reproduces its fingerprint exactly.
        with connect(self.path) as db:
            db.execute('INSERT INTO frq_rubric_quality_reviews VALUES(?,?,?,?,?,?,?)', (legacy, aid, review['check_id'], review_input(db, review['id']), legacy_policy, 'workspace', 'now'))
            self.assertEqual(self.frq.confirmation_record(db, legacy)['inputs'], expected)
        status = self.frq.rubric_quality(aid)
        self.assertEqual((set(status['standing']), status['standing'][self.q2]['review_id'], status['held']), ({self.q2}, legacy, ['Question 1']))
        # The grade names the confirmation with inputs; the legacy one gives its question the same guidance, so it is final.
        self.assertEqual(self.states(), {self.qid: 'grading_review', self.q2: 'auto_accepted'})
        self.assertEqual(self.final()[0], {self.q2: 'automatic'})
        self.assertIn(before[self.qid]['id'], self.final()[1])

    def test_question_inputs_and_guidance(self):
        status = self.frq.rubric_quality(self.plan['id'])
        q = status['context']['questions'][0]
        self.assertEqual(question_input({**q, 'reference': {'answer_key': []}, 'origins': ['x']}), question_input(q))  # What a check adds beside it.
        self.assertNotEqual(question_input({**q, 'comments': {'R2': 'A note.'}}), question_input(q))
        self.assertNotEqual(question_input({**q, 'rubric_id': 'another'}), question_input(q))
        policy = {'rules': BUILTIN}
        entry = {'question_id': q['question_id'], 'rubric_id': q['rubric_id'], 'pairs': [['R3', 'R2']], 'builtin': list(reversed(q['builtin'])),
                 'clarifications': {'R2': CLARIFIED, 'R3': ''}, 'reasons': [{'item_id': 'R2', 'reason': 'The model explained itself.'}], 'summary': 'Ignored.'}
        same = {**entry, 'pairs': [['R2', 'R3']], 'builtin': q['builtin'], 'clarifications': {'R2': CLARIFIED}, 'reasons': [], 'summary': ''}
        self.assertEqual(guidance_key(policy, entry), guidance_key(policy, same))  # Order, blank clarifications and the model's notes do not matter.
        for changed in ({**same, 'pairs': []}, {**same, 'clarifications': {}}, {**same, 'rubric_id': 'another'}, {**same, 'builtin': []}):
            self.assertNotEqual(guidance_key(policy, changed), guidance_key(policy, same))
        self.assertNotEqual(guidance_key({'rules': 'another-rule'}, same), guidance_key(policy, same))
        self.assertIsNone(guidance_key(policy, None))


def review_input(db, review_id):
    return db.execute('SELECT input_sha256 FROM frq_rubric_quality_reviews WHERE id=?', (review_id,)).fetchone()[0]


class OneRead(RubricScope):
    """`final_decisions` reads what its grades share (plan, question, location status, event, participant) once per call (performance
    audit, 2026-09-29): what it says is exactly what each grade's own check says, grades held back included."""

    def both(self):
        """(final_decisions with its memo, final_decisions checking every grade afresh), each as plain values, and how often each
        read the exam record."""
        original, counted = self.frq.validate_task, []
        event = self.frq.exams._event

        def plain(found):
            _, decisions, skipped = found
            return json.loads(json.dumps([[{k: dict(v) if k == 'row' else v for k, v in d.items()} for d in decisions], skipped], default=str))
        with self.frq.rosters.lock(), connect(self.path) as db, patch.object(self.frq.exams, '_event', side_effect=lambda *a: counted.append(1) or event(*a)):
            shared = plain(self.frq.final_decisions(db, self.plan['id']))
            reads = len(counted)
            with patch.object(self.frq, 'validate_task', new=lambda db, task, memo=None: original(db, task)):
                alone = plain(self.frq.final_decisions(db, self.plan['id']))
        return shared, alone, (reads, len(counted) - reads)

    def test_the_memo_gives_what_each_grades_own_check_gives_final_and_held_back_alike(self):
        before = self.graded()
        response = self.frq.response(before[self.qid]['id'])
        self.frq.save_review(response['id'], {'version': response['version'], 'result_id': response['current_result_id'], 'status': 'approved',
                                              'confirm_review': True, 'selected_items': ['R1']})
        shared, alone, reads = self.both()
        self.assertEqual(shared, alone)
        self.assertEqual(([(d['kind'], d['row']['question_id']) for d in shared[0]], shared[1]), ([('review', self.qid), ('automatic', self.q2)], []))
        self.assertEqual(reads, (1, 2))  # the exam record once for both grades, against once per grade
        # The decided question's rubric changes: its grade is stale and held back by its own check, the other stays final.
        self.edit(self.qid, lines=[{'item_id': 'R2', 'points': 3}])
        shared, alone, _ = self.both()
        self.assertEqual(shared, alone)
        self.assertEqual([d['row']['question_id'] for d in shared[0]], [self.q2])
        self.assertEqual(shared[1], [{'response_id': before[self.qid]['id'],
                                      'reason': 'The question or rubric changed after this was graded. The next batch grades it again.'}])


class SyntheticStatus(unittest.TestCase):
    """A status built by hand: only `standing` says which questions stand, and a question it lacks is held back."""
    ENTRY = {'question_id': 'q', 'rubric_id': 'r', 'pairs': [['R2', 'R3']], 'builtin': [{'item_id': 'R1', 'rule': 'full_credit'}], 'clarifications': {}}

    @staticmethod
    def ticks(*ids):
        return [{'rubric_item_id': i, 'applied': True} for i in ids] + [{'rubric_item_id': 'R4', 'applied': False}]

    def test_a_status_judged_by_standing_names_its_guidance(self):
        policy = {'rules': BUILTIN, 'questions': [self.ENTRY]}
        guidance = guidance_key(policy, self.ENTRY)
        standing = {'state': 'confirmed', 'review': {'id': 'c1', 'policy': policy}, 'unresolved': [],
                    'standing': {'q': {'review_id': 'c1', 'rubric_id': 'r', 'entry': self.ENTRY, 'guidance_sha256': guidance, 'open': None}}}
        self.assertEqual(quality_flags(standing, 'q', 'r', self.ticks('R2', 'R3')), {'enabled': True, 'review_id': 'c1', 'pairs': [['R2', 'R3']],
            'reasons': ['Rubric quality check: R2 and R3 were both selected but are confirmed as mutually exclusive. Review this grade manually.'],
            'guidance_sha256': guidance})
        self.assertEqual(quality_flags(standing, 'q', 'r', self.ticks('R1', 'R2')),
                         {'enabled': True, 'review_id': 'c1', 'pairs': [['R1', 'R2']], 'guidance_sha256': guidance,
                          'reasons': ['Rubric quality check: R1 is the full-credit line of this question and cannot be combined with R2. Review this grade manually.']})
        self.assertEqual(quality_flags(standing, 'q', 'r', self.ticks('R2')), {'enabled': True, 'review_id': 'c1', 'pairs': [], 'reasons': [], 'guidance_sha256': guidance})
        # Another revision, or a question that does not stand, is held; neither carries a guidance to compare with.
        other = quality_flags(standing, 'q', 'other', self.ticks('R2'))
        self.assertEqual((other['reasons'], other['guidance_sha256']), (['Rubric quality check: this rubric revision has not been checked.'], None))
        missing = quality_flags({**standing, 'standing': {}}, 'q', 'r', self.ticks('R2'))
        self.assertEqual((missing['review_id'], missing['guidance_sha256'], len(missing['reasons'])), (None, None, 1))
        # An open question of the confirmation holds back its own responses only.
        held = {**standing, 'state': 'needs_review', 'unresolved': [{'question_id': 'other', 'points': 12, 'maximum': 10, 'lines': ['R2', 'R3']}]}
        self.assertEqual(quality_flags(held, 'q', 'r', self.ticks('R2'))['reasons'], [])
        self.assertIn('R2, R3 can still be selected together for 12 of 10 points', quality_flags(held, 'other', 'r', self.ticks('R2'))['reasons'][0])


if __name__ == '__main__':
    unittest.main()
