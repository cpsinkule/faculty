"""Rubric finalization: mandatory before grading, built-in exclusions by rule, clarifications for the graders only.

Synthetic rubric data and loopback models. The rubric, its scores and every export keep the original wording."""
import copy
import json
import time
import sqlite3
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from grading import config
from grading.rubric_quality import builtin_exclusions, decision_fields, grader_rubric, heaviest_combination, normalize_questions, question_payload, review_budget, validate_problem, validate_question, validate_resolution
from rosters import connect
from fake_openai import payload_of, prompt_name
from frq_fixtures import confirm_for_review
from test_grading_pipeline import Pipeline, CORRECT
import test_grading_uploads as uploads
from grading.models import digest
from test_gradescope_rubrics import encode, export_rows


CLEAR = 'Credit the setup when the unknown is isolated on one side, even with an arithmetic slip afterwards.'


class GuidedDecisions(unittest.TestCase):
    """GRADING: presentation and editable feedback only, with a usable open-feedback path when there are no choices."""
    def setUp(self):
        self.question = {'question_id': 'a', 'exam_version': 'A', 'rubric_id': 'ra', 'builtin': [],
                         'rubric': {'items': [{'id': 'line'}]}}
        self.issue = {'item_ids': ['line'], 'message': 'The criterion has two possible readings.'}
        self.presentation = {'title': 'Which reading?', 'decision': 'Should the result alone count?', 'group': 'reading',
            'choices': [{'label': 'Accept the result', 'feedback': 'Accept the correct result without intermediate work.', 'consequence': ''},
                        {'label': 'Require work', 'feedback': 'Require the intermediate work this criterion names.', 'consequence': 'See rubric'}]}

    def answer(self, issue):
        return {'summary': '', 'suspected_errors': [issue], 'items': [{'item_id': 'line', 'clarification': '', 'excludes': [], 'reason': ''}]}

    def test_legacy_output_is_identical_and_no_choices_are_fabricated(self):
        entry, errors = validate_question(self.answer(self.issue), self.question)
        self.assertEqual(errors, [{'question_id': 'a', **self.issue}])
        self.assertEqual(entry['clarifications'], {})
        self.assertEqual(decision_fields({'choices': []}), {})

    def test_supported_choices_are_retained_but_never_become_clarifications(self):
        entry, errors = validate_question(self.answer({**self.issue, **self.presentation}), self.question)
        self.assertEqual(errors, [{'question_id': 'a', **self.issue, **self.presentation}])
        self.assertEqual(entry['clarifications'], {})

    def test_choices_need_distinct_labels_and_standalone_feedback_and_are_bounded(self):
        first = self.presentation['choices'][0]
        for choices in ([first], [first] * 5, [first, first], [first, {'label': 'Other', 'feedback': ''}], [first, {'label': 'Other', 'feedback': first['feedback']}], ['not a choice', first]):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                decision_fields({'choices': choices})
        with self.assertRaises(ValueError):
            decision_fields({'title': 'x' * 121})

    def test_versioned_findings_keep_decision_metadata_and_their_own_item_references(self):
        b = {**self.question, 'question_id': 'b', 'exam_version': 'B', 'rubric_id': 'rb'}
        answer = {'summary': '', 'versions': [{'version': name, 'summary': '', 'items': self.answer(self.issue)['items']} for name in ('A', 'B')],
                  'suspected_errors': [{'version': 'A', **self.issue, **self.presentation}],
                  'consistency_issues': [{'versions': ['A', 'B'], 'message': 'Different criteria', **self.presentation}]}
        found, issues, _ = validate_problem(answer, [self.question, b])
        self.assertEqual(found['a'][1][0]['choices'], self.presentation['choices'])
        self.assertEqual(issues[0]['question_ids'], ['a', 'b'])
        self.assertEqual(issues[0]['decision'], self.presentation['decision'])
        answer['concerns'] = [{'concern': 0, 'resolved': False, 'how': 'Correct the source rubric.', 'title': 'Source correction needed', 'decision': 'Correct the criterion wording.', 'group': '', 'choices': []}]
        *_, outcomes = validate_resolution(answer, [self.question, b], [0])
        self.assertNotIn('choices', outcomes[0]['presentation'])
        self.assertEqual(outcomes[0]['presentation']['title'], 'Source correction needed')


def suggestion(payload):
    """One question's answer: R2 with R3 (and, uselessly, the full-credit R1 with R2, which the built-in rule already covers)."""
    return {'summary': 'Compare alternatives before confirming.',
        'suspected_errors': [{'item_ids': ['R3'], 'message': 'R3 names an answer the key does not accept.'}],
        'items': [{'item_id': item['id'], 'clarification': CLEAR if item['id'] == 'R2' else '', 'excludes': {'R1': ['R2'], 'R2': ['R3']}.get(item['id'], []),
            'reason': 'These describe alternative levels of credit.' if item['id'] in ('R1', 'R2') else ''} for item in payload['question']['rubric']['items']]}


class QualityCase(Pipeline):
    """A plan with one rubric and a scripted reviewer; `check` runs the finalization's check, `confirm` confirms it."""
    def setUp(self):
        super().setUp()
        current = self.frq.settings()
        self.frq.save_settings({'version': current['version'], 'settings': {**current['settings'], 'rubric_reviewer': 'beta-text'}})
        self.host.script = lambda body, host: suggestion(payload_of(body))

    def check(self):
        confirm_for_review(self.frq, self.plan['id'])
        status = self.frq.rubric_quality(self.plan['id'])
        data = {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']}
        queued = self.frq.queue_rubric_quality(self.plan['id'], data)
        self.assertEqual(queued['check']['id'], self.frq.queue_rubric_quality(self.plan['id'], data)['check']['id'])
        self.frq.process_cloud()
        return self.frq.rubric_quality(self.plan['id'])

    def confirm(self, status, pairs=None, clarifications=None):
        rows = copy.deepcopy(status['check']['result']['questions'])
        if pairs is not None:
            rows[0]['pairs'] = pairs
        if clarifications is not None:
            rows[0]['clarifications'] = clarifications
        return self.frq.confirm_rubric_quality(self.plan['id'], {'version': status['version'], 'check_id': status['check']['id'],
            'input_sha256': status['input_sha256'], 'confirm_review': True, 'questions': rows})


class Quality(QualityCase):
    def test_review_is_separate_immutable_and_sent_to_selected_text_model(self):
        before = self.frq.assignment(self.plan['id'])['questions']
        checked = self.check()
        self.assertEqual(checked['state'], 'needs_review')
        self.assertIsNone(checked['review'])
        found = checked['check']['result']['questions'][0]
        # The built-in rules need no model: the line worth the whole question and the no-answer line. The reviewer's own
        # R1/R2 is already covered by them and is not kept as an editable pair; R2/R3 is its one suggestion.
        self.assertEqual(found['builtin'], [{'item_id': 'R1', 'rule': 'full_credit'}, {'item_id': 'R4', 'rule': 'no_credit'}])
        self.assertEqual((found['pairs'], found['clarifications']), ([['R2', 'R3']], {'R2': CLEAR}))
        self.assertEqual(checked['check']['result']['suspected_errors'][0]['item_ids'], ['R3'])
        body = self.host.requests[-1]['body']
        self.assertEqual(body['model'], 'fixture/beta-text')
        self.assertEqual(prompt_name(body), 'rubric_quality')
        self.assertIsInstance(body['messages'][1]['content'], str)
        self.assertNotIn('transcript', payload_of(body))
        self.assertNotIn('source_text', payload_of(body))
        # One question alone in its request, with its own answer key; the key is not part of what a confirmation covers.
        sent = payload_of(body)
        self.assertEqual(sorted(sent), ['exam_version', 'question', 'reference', 'rules'])
        self.assertEqual((sent['question']['title'], sent['reference']['answer_key'], sent['reference']['problem_images']), ('Question 1', [{'label': 'x', 'accept': ['x=6']}], []))
        self.assertNotIn('reference', checked['context']['questions'][0])
        self.assertIn('checked separately', checked['check']['result']['summary'])
        self.assertNotIn('consistency_issues', checked['check']['result'])  # Versions are no longer compared: that needs two questions in one request.
        self.assertEqual(found['summary'], 'Compare alternatives before confirming.')
        current = self.confirm(checked, [['R3', 'R2'], ['R2', 'R3'], ['R1', 'R3']], {'R2': '  Setup counts even after a slip. ', 'R3': ''})
        policy = current['review']['policy']['questions'][0]
        self.assertEqual((policy['pairs'], policy['clarifications'], policy['builtin']), ([['R2', 'R3']], {'R2': 'Setup counts even after a slip.'}, found['builtin']))
        with self.assertRaises(ValueError):
            self.confirm(self.frq.rubric_quality(self.plan['id']), clarifications={'R9': 'No such item.'})
        # Nothing of this is written into the rubric: the record keeps the original wording and rules.
        after = self.frq.assignment(self.plan['id'])['questions']
        self.assertEqual([q['rubric']['rules'] for q in after], [q['rubric']['rules'] for q in before])
        with connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('SELECT state,role FROM frq_cloud_calls').fetchone()[:], ('completed', 'rubric_reviewer'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_quality_checks WHERE input_sha256=?', (current['input_sha256'],)).fetchone()[0], 1)
            for sql in ('UPDATE frq_rubric_quality_checks SET result_json=NULL', 'UPDATE frq_rubric_quality_reviews SET policy_json=\'{}\'', 'DELETE FROM frq_rubric_quality_reviews'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_the_gate_is_mandatory_requires_explicit_confirmation_and_tracks_revisions(self):
        status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual((status['enabled'], status['state']), (True, 'needed'))
        command = {'question_ids': [self.qid], 'mode': 'cloud_standard'}
        for mode in ('cloud_standard', 'local'):
            with self.assertRaisesRegex(Conflict, 'rubric finalization'):
                self.frq.preview(self.plan['id'], {**command, 'mode': mode})
        with self.assertRaisesRegex(Conflict, 'cannot be turned off'):
            self.frq.set_rubric_quality(self.plan['id'], {'version': status['version'], 'enabled': False})
        checked = self.check()
        with self.assertRaises(ValueError):
            self.frq.confirm_rubric_quality(self.plan['id'], {'confirm_review': False})
        confirmed = self.confirm(checked)
        self.assertEqual(self.frq.preview(self.plan['id'], command)['rubric_finalization'], confirmed['review']['id'])
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual(status['state'], 'needed')
        self.assertFalse(status['check']['current'])
        with self.assertRaises(Conflict):
            self.confirm(checked)
        with self.assertRaisesRegex(Conflict, 'rubric finalization'):
            self.frq.preview(self.plan['id'], command)

    def test_one_confirmation_approves_every_rubric_of_the_check(self):
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        self.assertIsNone(self.frq.assignment(self.plan['id'])['questions'][0]['rubric']['approved_at'])
        self.confirm(self.check())
        self.assertTrue(self.frq.assignment(self.plan['id'])['questions'][0]['rubric']['approved_at'])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='rubric_approved' AND json_extract(detail_json,'$.review_id') IS NOT NULL").fetchone()[0], 1)

    def test_the_instructor_corrects_one_lines_wording_as_a_new_revision_that_keeps_the_finalization(self):
        """2026-09-21: a full-credit line said y = -4 where the printed problem gives -5, and nothing could change a rubric once uploaded."""
        aid = self.plan['id']
        confirmed = self.confirm(self.check(), pairs=[['R2', 'R3']], clarifications={'R2': CLEAR})
        before = self.frq.assignment(aid)['questions'][0]
        correct = lambda q, **more: self.frq.correct_rubric_line(aid, self.qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
            'item_id': 'R1', 'description': 'Correct Answer: $$x=6$$ and work to justify the answer.', **more})
        result = correct(before)
        after = result['questions'][0]
        self.assertEqual(result['correction'], {'rubric_id': after['rubric_id'], 'item_id': 'R1', 'finalization_carried': True})
        self.assertNotEqual(after['rubric_id'], before['rubric_id'])
        line = lambda q: next(i for i in q['rubric']['rules']['items'] if i['id'] == 'R1')
        self.assertEqual(line(after)['description'], 'Correct Answer: $$x=6$$ and work to justify the answer.')
        self.assertEqual(line(after)['original_source_text'], line(before)['original_source_text'])  # What the coordinator wrote stays on the line.
        self.assertEqual([(i['id'], i['points']) for i in after['rubric']['rules']['items']], [(i['id'], i['points']) for i in before['rubric']['rules']['items']])
        self.assertEqual((after['rubric']['compiler']['name'], after['rubric']['compiler']['corrected_from'], after['rubric']['compiler']['previous_description']),
                         ('instructor-corrected', before['rubric_id'], line(before)['description']))
        self.assertTrue(after['rubric']['approved_at'])
        # The finalization is carried: same pairs and clarification, now naming the corrected revision; the earlier ones are untouched.
        status = self.frq.rubric_quality(aid)
        entry = status['review']['policy']['questions'][0]
        self.assertEqual((status['state'], entry['rubric_id'], entry['pairs'], entry['clarifications']), ('confirmed', after['rubric_id'], [['R2', 'R3']], {'R2': CLEAR}))
        self.assertNotEqual(status['review']['id'], confirmed['review']['id'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_quality_reviews WHERE check_id=?', (confirmed['review']['check_id'],)).fetchone()[0], 2)  # The confirmation and what carries it.
            self.assertEqual(json.loads(db.execute('SELECT policy_json FROM frq_rubric_quality_reviews WHERE id=?', (confirmed['review']['id'],)).fetchone()[0])['questions'][0]['rubric_id'], before['rubric_id'])
            self.assertEqual(json.loads(db.execute('SELECT rules_json FROM frq_rubric_revisions WHERE id=?', (before['rubric_id'],)).fetchone()[0])['items'][0]['description'], line(before)['description'])
            kinds = [r[0] for r in db.execute("SELECT kind FROM frq_events WHERE kind IN ('rubric_line_corrected','rubric_quality_carried')")]
        self.assertEqual(sorted(kinds), ['rubric_line_corrected', 'rubric_quality_carried'])
        command = {'question_ids': [self.qid], 'mode': 'cloud_standard'}
        self.assertEqual(self.frq.preview(aid, command)['rubric_finalization'], status['review']['id'])  # A batch can start at once, under the corrected rubric.
        # Stale, unchanged or unknown requests change nothing.
        for bad, error in ((lambda: correct(before), Conflict), (lambda: correct(after), ValueError), (lambda: correct(after, item_id='R9'), ValueError), (lambda: correct(after, description='  '), ValueError)):
            with self.assertRaises(error):
                bad()
        # A correction that changes what the built-in rules find is not carried: the finalization is asked for again.
        self.frq.correct_rubric_line(aid, self.qid, {'version': after['version'], 'rubric_id': after['rubric_id'], 'sha256': after['rubric']['sha256'],
            'item_id': 'R4', 'description': 'Part B: No Answer or Completely Incorrect'})
        self.assertEqual(self.frq.rubric_quality(aid)['state'], 'needed')
        with self.assertRaises(Conflict):
            self.frq.preview(aid, command)

    def test_builtin_exclusions_are_decided_by_rule_alone(self):
        def rules(mode, *lines):
            return {'mode': mode, 'items': [{'id': f'R{i + 1}', 'description': text, 'points': points} for i, (points, text) in enumerate(lines)]}
        positive = rules('positive', (6, 'Correct answer with work'), (2, 'Correct slope'), (0, 'No Answer or Completely Incorrect.'),
                         (0, 'No Matrix Given or Completely Incorrect'), (0, 'Part B: no answer'), (0, 'Correct answer, but no work'), (0, ' completely   INCORRECT '))
        self.assertEqual(builtin_exclusions(positive, 6), [{'item_id': 'R1', 'rule': 'full_credit'}, {'item_id': 'R3', 'rule': 'no_credit'}, {'item_id': 'R7', 'rule': 'no_credit'}])
        # A deducting rubric: the line that takes everything excludes the rest; a smaller deduction does not.
        negative = rules('negative', (-4, 'No answer'), (-1, 'Arithmetic error'), (0, 'Correct'))
        self.assertEqual(builtin_exclusions(negative, 4), [{'item_id': 'R1', 'rule': 'no_credit'}])
        self.assertEqual(builtin_exclusions(rules('positive', (0, 'No answer')), 0), [{'item_id': 'R1', 'rule': 'no_credit'}])
        # A line that speaks for every part is the question's no-credit line; a line about one part is that part's zero.
        parts = rules('positive', (4, 'Correct Answers'), (0, 'No Answers for either parts a or b.'), (0, 'No Answers for Part a or Part b.'), (0, 'No answer for any part'),
                      (0, 'Part B: No Answer or Completely Incorrect'), (0, 'No answer for part a'), (0, 'Part A: Neither of the rows or columns are correct'), (0, 'No answer for the second blank'))
        self.assertEqual([f['item_id'] for f in builtin_exclusions(parts, 4)], ['R1', 'R2', 'R3', 'R4'])

    def test_answer_key_edits_do_not_invalidate_the_rubric_check(self):
        current = self.confirm(self.check())
        plan = self.frq.assignment(self.plan['id'])
        self.frq.save_answer_key(plan['id'], {'version': plan['version'], 'questions': [{'question_id': self.qid, 'version': plan['questions'][0]['version'], 'answer_key': [{'label': 'x', 'accept': ['x=7']}]}]})
        self.assertEqual(self.frq.rubric_quality(plan['id'])['review']['id'], current['review']['id'])

    def test_a_violated_exclusion_flags_the_proposal_without_changing_its_score(self):
        current = self.confirm(self.check())
        response, _ = self.grade(CORRECT, primary=['R2', 'R3'])
        result = response['result']
        self.assertEqual(result['score']['score'], 6)  # Scoring is the approved rubric's alone.
        self.assertEqual(self.proposal(response), ['R2', 'R3'])
        self.assertEqual(result['state'], 'grading_review')
        self.assertIn('R2 and R3', '\n'.join(result['reasons']))
        self.assertEqual(result['signals']['scope']['rubric_quality'], current['review']['id'])
        self.assertIsNone(response['acceptance'])
        self.assertEqual(self.frq.answer_groups(self.plan['id'], self.qid)['individual'], 1)
        # Editing a confirmed policy removes only its flags, preserving the stored proposal and numeric score.
        self.confirm(self.frq.rubric_quality(self.plan['id']), [])
        fresh = self.frq.response(response['id'])
        self.assertFalse(fresh['result']['signals']['rubric_quality']['reasons'])
        self.assertEqual(fresh['result']['score'], result['score'])
        self.assertEqual(self.frq.responses(self.plan['id'])[0]['state'], fresh['result']['state'])
        self.confirm(self.frq.rubric_quality(self.plan['id']), [['R2', 'R3']])
        response = self.frq.response(response['id'])
        self.approve(response, selected_items=['R2', 'R3'])
        self.assertEqual(self.frq.responses(self.plan['id'])[0]['state'], 'approved')
        with connect(self.path) as db:
            saved = json.loads(db.execute('SELECT signals_json FROM frq_results WHERE id=?', (response['current_result_id'],)).fetchone()[0])
            self.assertEqual(saved['rubric_quality']['review_id'], current['review']['id'])
            self.assertIsNotNone(db.execute("SELECT 1 FROM frq_events WHERE kind='rubric_quality_manual_review'").fetchone())

    def test_the_check_is_sent_with_the_reviewers_own_reasoning_choice_which_never_touches_a_batch(self):
        facts = {'supported': True, 'mandatory': False, 'efforts': ['low', 'medium', 'high'], 'default_effort': 'medium'}
        current = self.frq.settings()
        settings = {**current['settings'], 'endpoints': [{**e, 'reasoning_options': facts} if e['name'] == 'beta-text' else e for e in current['settings']['endpoints']]}
        command = {'question_ids': [self.qid], 'mode': 'cloud_standard'}
        current = self.frq.save_settings({'version': current['version'], 'settings': settings})  # The catalogue's facts for the reviewer's model.
        self.confirm(self.check())
        before = self.frq.preview(self.plan['id'], command)['fingerprint']
        current = self.frq.save_settings({'version': current['version'], 'settings': {**current['settings'], 'rubric_reviewer_reasoning': 'high'}})
        # The reviewer's effort is rubric preparation, like the reviewer itself: an open batch preview stays valid.
        self.assertEqual(self.frq.preview(self.plan['id'], command)['fingerprint'], before)
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        self.host.requests.clear()
        self.check()
        with connect(self.path) as db:
            profile = json.loads(db.execute('SELECT profile_json FROM frq_rubric_quality_checks ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        self.assertEqual(profile['endpoint']['reasoning_choice'], 'high')
        self.assertEqual(self.host.requests[-1]['body']['reasoning'], {'effort': 'high', 'exclude': False})

    def test_lines_that_add_up_to_more_than_the_question_must_be_resolved_into_alternatives(self):
        """requirement, 2026-09-21. Measured on the first confirmed finalization: 7 of 16 questions still allowed more than they were worth."""
        from pathlib import Path
        for case in json.loads((Path(__file__).parent / 'fixtures' / 'grading' / 'overflow_cases.json').read_text()):
            with self.subTest(case['name']):
                self.assertEqual(heaviest_combination(case['rubric'], case['entry'], case['maximum']), case['expected'])
        # This fixture's rubric: R1 is worth the whole question, R2 (4) and R3 (2) fit under its 10 points together.
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        rules = {**q['rubric']['rules'], 'items': [*q['rubric']['rules']['items'], {'id': 'R5', 'description': 'Correct setup with a sign slip', 'points': 3.5},
                                                   {'id': 'R6', 'description': 'Correct setup in another form', 'points': 4}]}
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Steps worth more than the question', 'rules': rules})
        asked = []
        def reviewer(body, host):
            sent = payload_of(body)
            asked.append(sent.get('unresolved'))
            answer = suggestion(sent)  # R2 with R3 only: R2 + R5 + R6 = 11.5 of 10 can still be combined.
            if sent.get('unresolved'):
                for item in answer['items']:
                    if item['item_id'] == 'R2': item.update(excludes=['R3', 'R5'], reason='Alternative outcomes of the setup.')
            return answer
        self.host.script = reviewer
        checked = self.check()
        # The reviewer was told exactly which lines its pairs still allowed, once, and found the alternatives among them.
        self.assertEqual((asked[0], asked[1]['lines'], asked[1]['points'], asked[1]['maximum'], len(asked)), (None, ['R2', 'R5', 'R6'], 11.5, 10, 2))
        proposed = checked['check']['result']['questions'][0]['pairs']
        self.assertEqual((proposed, checked['check']['result']['unresolved']), ([['R2', 'R3'], ['R2', 'R5']], []))
        # The instructor cannot confirm a finalization that leaves a question open, whoever left it open.
        with self.assertRaisesRegex(ValueError, 'R2, R5, R6 can be selected together for 11.5 of 10 points'):
            self.confirm(checked, [['R2', 'R3']])
        self.assertIsNone(self.frq.rubric_quality(self.plan['id'])['review'])
        with self.assertRaisesRegex(Conflict, 'rubric finalization'):
            self.frq.preview(self.plan['id'], {'question_ids': [self.qid], 'mode': 'cloud_standard'})
        done = self.confirm(checked)
        self.assertEqual((done['state'], done['unresolved']), ('confirmed', []))
        self.frq.preview(self.plan['id'], {'question_ids': [self.qid], 'mode': 'cloud_standard'})
        # A reviewer that leaves it open is not a failed check: what is open is reported for the instructor to decide.
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Again', 'rules': rules})
        self.host.script = lambda body, host: suggestion(payload_of(body))
        left = self.check()
        still, = left['check']['result']['unresolved']
        self.assertEqual((left['state'], still['lines'], still['points']), ('needs_review', ['R2', 'R5', 'R6'], 11.5))
        self.assertIn('1 question still allow more points than they are worth', left['check']['result']['summary'])

    def test_a_confirmation_from_before_the_rule_holds_back_only_its_unresolved_questions(self):
        second = self.second_question()
        self.confirm(self.check())
        self.assertEqual(self.frq.rubric_quality(self.plan['id'])['state'], 'confirmed')
        # Such a confirmation exists from before 2026-09-21: written directly, as the rule now refuses to make one.
        status = self.frq.rubric_quality(self.plan['id'])
        policy = copy.deepcopy(status['review']['policy'])
        with connect(self.path) as db:
            rules = json.loads(db.execute('SELECT rules_json FROM frq_rubric_revisions WHERE id=(SELECT rubric_id FROM frq_assignment_questions WHERE question_id=?)', (second,)).fetchone()[0])
        self.assertIsNone(heaviest_combination(rules, next(q for q in policy['questions'] if q['question_id'] == second), 10))
        from grading.rubric_quality import quality_flags
        open_question = {'question_id': second, 'points': 12, 'maximum': 10, 'lines': ['R2', 'R3']}
        held = {**status, 'state': 'needs_review', 'unresolved': [open_question]}
        ticks = [{'rubric_item_id': 'R2', 'applied': True}]
        mine = next(q for q in policy['questions'] if q['question_id'] == self.qid)
        theirs = next(q for q in policy['questions'] if q['question_id'] == second)
        self.assertIn('R2, R3 can still be selected together for 12 of 10 points', quality_flags(held, second, theirs['rubric_id'], ticks)['reasons'][0])
        self.assertEqual(quality_flags(held, self.qid, mine['rubric_id'], ticks)['reasons'], [])  # The other question of the confirmation stands.

    def test_the_reviewers_answer_has_room_for_every_line_of_its_one_question(self):
        exam = {'questions': [{'rubric': {'items': [{}] * 12}}] * 15 + [{'rubric': {'items': [{}] * 200}}]}
        self.assertEqual(review_budget({'max_tokens': 8192}, exam)['max_tokens'], 2048 + 48 * 200)  # The largest question, never the exam.
        self.assertEqual(review_budget({'max_tokens': 8192}, {'questions': [{'rubric': {'items': [{}] * 12}}] * 16})['max_tokens'], 8192)  # Never less than the connection's own.
        self.assertEqual(review_budget({'max_tokens': 4096}, {'questions': [{'rubric': {'items': [{}] * 400}}]})['max_tokens'], 16384)

    def second_question(self, number=2):
        plan = self.frq.assignment(self.plan['id'])
        plan = self.frq.save_question(plan['id'], {'version': plan['version'], 'question_key': f'q{number}', 'assignment_version_id': self.version_id,
            'content': {'title': f'Question {number}', 'max_points': 10, 'problem': 'Solve 2x = 8.', 'regions': [{'page': 1, 'region': {'x': .1, 'y': .5, 'width': .8, 'height': .4}}]}})
        q = next(q for q in plan['questions'] if q['question_key'] == f'q{number}')
        self.frq.import_rubric(plan['id'], q['question_id'], {'version': q['version'], 'source_text': 'Second rubric', 'rules': {'mode': 'positive', 'items': [
            {'id': 'R1', 'description': 'Correct answer with work', 'points': 10}, {'id': 'R2', 'description': 'Correct setup', 'points': 4}, {'id': 'R3', 'description': 'Sign error', 'points': 2}]}})
        return q['question_id']

    def test_every_question_of_an_exam_is_asked_at_once_and_only_the_connection_paces_them(self):
        """requirement, 2026-09-21: a pool of 8 sent sixteen questions in two waves, and a check of five-minute answers took over ten."""
        import threading
        from grading import rubric_quality
        for number in range(2, 11):
            self.second_question(number)
        def allow(inflight):
            current = self.frq.settings()
            endpoints = [{**e, 'max_inflight': inflight} if e['name'] == 'beta-text' else e for e in current['settings']['endpoints']]
            self.frq.save_settings({'version': current['version'], 'settings': {**current['settings'], 'endpoints': endpoints}})
        allow(16)
        together, gate, seen = threading.Barrier(10, timeout=30), threading.Lock(), []
        def reviewer(body, host):
            sent = payload_of(body)
            with gate:
                seen.append(sent['question']['title'])
            together.wait()  # All ten are in flight at once, or this times out and the check fails: a pool of 8 never gets here.
            return suggestion(sent)
        self.host.script = reviewer
        checked = self.check()
        self.assertEqual((checked['state'], len(seen), len(set(seen))), ('needs_review', 10, 10))
        self.assertGreaterEqual(rubric_quality.PARALLEL, 32)  # A ceiling on threads, never the pace ...
        # ... which is the reviewer connection's own in-flight limit: allowed four, never more than four are out at once.
        allow(4)
        for q in self.frq.assignment(self.plan['id'])['questions']:  # every rubric changes, so every question is asked again
            q = next(row for row in self.frq.assignment(self.plan['id'])['questions'] if row['question_id'] == q['question_id'])
            self.frq.import_rubric(self.plan['id'], q['question_id'], {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        out, most = [0], [0]
        def counted(body, host):
            with gate:
                out[0] += 1
                most[0] = max(most[0], out[0])
            time.sleep(.2)
            with gate:
                out[0] -= 1
            return suggestion(payload_of(body))
        self.host.script = counted
        self.assertEqual(self.check()['state'], 'needs_review')
        self.assertEqual(most[0], 4)

    def test_every_question_is_asked_alone_side_by_side_and_a_retry_sends_only_what_is_missing(self):
        second = self.second_question()
        import threading
        together, gate, seen = threading.Barrier(2, timeout=20), threading.Lock(), []
        def reviewer(body, host):
            sent = payload_of(body)
            with gate:
                seen.append(sent['question']['title'])
            together.wait()  # Both requests are in flight at once, or this times out and the check fails.
            return suggestion(sent)
        self.host.script = reviewer
        checked = self.check()
        self.assertEqual((checked['state'], sorted(seen)), ('needs_review', ['Question 1', 'Question 2']))
        bodies = [payload_of(r['body']) for r in self.host.requests]
        # No request holds another question's rubric, key or problem.
        self.assertTrue(all('questions' not in sent and len(json.dumps(sent).split('"title"')) == 2 for sent in bodies))
        self.assertEqual(sorted(sent['reference']['problem'] for sent in bodies), ['Solve', 'Solve 2x = 8.'])
        self.assertEqual(sorted(q['question_id'] for q in checked['check']['result']['questions']), sorted([self.qid, second]))
        with connect(self.path) as db:
            steps = sorted(r[0] for r in db.execute('SELECT step FROM frq_cloud_calls WHERE rubric_check_id=?', (checked['check']['id'],)))
        self.assertEqual(steps, sorted(f'rubric_quality:{q}' for q in (self.qid, second)))
        # One question's host is briefly unavailable: the other's answer is kept, and the next turn asks only for the missing one.
        for qid in (self.qid, second):  # both rubrics change, so both are asked again (A CHECK ASKS ONLY WHAT CHANGED)
            q = next(row for row in self.frq.assignment(self.plan['id'])['questions'] if row['question_id'] == qid)
            self.frq.import_rubric(self.plan['id'], qid, {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        self.host.requests.clear()
        down = []
        def flaky(body, host):
            sent = payload_of(body)
            if sent['question']['title'] == 'Question 2' and not down:
                down.append(1)
                return ('status', 503, {'Retry-After': '0'}, {'error': {'message': 'Synthetic capacity'}})
            return suggestion(sent)
        self.host.script = flaky
        status = self.check()
        for _ in range(3):
            if status['state'] == 'needs_review': break
            with connect(self.path) as db:
                db.execute("UPDATE frq_rubric_quality_checks SET not_before='' WHERE status='pending'")
            self.frq.process_cloud()
            status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual(status['state'], 'needs_review')
        self.assertEqual(sorted(payload_of(r['body'])['question']['title'] for r in self.host.requests), ['Question 1', 'Question 2', 'Question 2'])  # Question 1 was never sent again.

    def test_a_reviewer_that_reads_images_is_shown_each_problem_as_printed_on_the_blank_exam(self):
        current = self.frq.settings()
        self.frq.save_settings({'version': current['version'], 'settings': {**current['settings'], 'rubric_reviewer': 'gamma-vision'}})
        area = {'template_id': 'blank-exam', 'page': 7, 'region': {'x': .02, 'y': .565, 'width': .96, 'height': .16}}
        reference = self.frq.rubric_reference
        def confirmed(db, aid):  # As after the answer-area step: the question's area is on a confirmed blank exam.
            found = reference(db, aid)
            found[self.qid]['printed'] = [area]
            return found
        read = []
        def page_image(identifier, kind, page):
            read.append((identifier, kind, page))
            return b'blank exam page', 'image/png'
        from fake_openai import images_of
        with patch.object(self.frq, 'rubric_reference', side_effect=confirmed), patch.object(self.frq.templates, 'read', side_effect=page_image), patch('grading.extraction.extract',
                return_value={'metadata': {}, 'images': {k: b'printed problem crop' for k in ('original', 'grayscale', 'contrast', 'ink', 'cloud')}}) as crop:
            checked = self.check()
        self.assertEqual(checked['state'], 'needs_review')
        body = self.host.requests[-1]['body']
        self.assertEqual((len(images_of(body)), payload_of(body)['reference']['problem_images'], read), (1, [0], [('blank-exam', 'image', 7)]))
        self.assertNotIn('printed', payload_of(body)['reference'])  # Where the crop came from is the record's business, not the model's.
        self.assertEqual(crop.call_args.args[1:], (area['region'], None, []))  # A blank exam has no student to mask and nothing to align with.
        self.assertIn('1 read from the blank exam', checked['check']['result']['summary'])
        # An unavailable page or sandbox is never a reason to fail a mandatory check: it goes on without the picture.
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        with patch.object(self.frq, 'rubric_reference', side_effect=confirmed), patch.object(self.frq.templates, 'read', side_effect=OSError('no page')):
            checked = self.check()
        self.assertEqual((checked['state'], payload_of(self.host.requests[-1]['body'])['reference']['problem_images']), ('needs_review', []))
        self.assertIn('no picture of its printed problem', checked['check']['result']['summary'])
        # A reviewer that reads text only is sent none, whatever the question has.
        self.frq.save_settings({'version': self.frq.settings()['version'], 'settings': {**self.frq.settings()['settings'], 'rubric_reviewer': 'beta-text'}})
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Third revision', 'rules': q['rubric']['rules']})
        with patch.object(self.frq, 'rubric_reference', side_effect=confirmed), patch.object(self.frq.templates, 'read', side_effect=page_image) as untouched:
            self.check()
        self.assertEqual((untouched.call_count, images_of(self.host.requests[-1]['body'])), (0, []))

    def test_the_full_credit_line_with_any_other_line_is_flagged_by_rule(self):
        self.confirm(self.check(), [])  # No confirmed pair at all: the built-in rule alone decides.
        response, _ = self.grade(CORRECT, primary=['R1', 'R2'])
        result = response['result']
        self.assertEqual((result['score']['score'], result['state']), (10, 'grading_review'))  # The ceiling hides nothing from the check.
        self.assertIn('R1 is the full-credit line of this question and cannot be combined with R2', '\n'.join(result['reasons']))
        self.assertEqual(result['signals']['rubric_quality']['pairs'], [['R1', 'R2']])
        self.assertIsNone(response['acceptance'])

    def test_graders_read_the_clarifications_and_exclusions_while_the_record_keeps_the_original(self):
        current = self.confirm(self.check())
        response, _ = self.grade(CORRECT)
        self.assertEqual((response['result']['state'], response['result']['reasons']), ('proposed', []))
        sent = [payload_of(r['body']) for r in self.host.requests if prompt_name(r['body']) in ('grade', 'transcript_grade')]
        self.assertEqual(len(sent), 2)
        for payload in sent:
            lines = {i['id']: i for i in payload['rubric']['items']}
            self.assertEqual(lines['R2']['clarification'], CLEAR)
            self.assertEqual(lines['R2']['description'], 'Set up the equation correctly')  # The original wording travels unchanged beside it.
            self.assertEqual((lines['R1']['exclusive_with'], lines['R2']['exclusive_with'], lines['R4']['exclusive_with']), (['R2', 'R3', 'R4'], ['R1', 'R3', 'R4'], ['R1', 'R2', 'R3']))
            self.assertNotIn('clarification', lines['R1'])
            self.assertEqual(payload['rubric']['warnings'], [])
            self.assertNotIn('rubric_guidance', payload)
        # A newer confirmation never changes what a submitted batch reads, and the result names the one it read.
        self.assertEqual(response['result']['signals']['scope']['rubric_quality'], current['review']['id'])
        question = self.frq.assignment(self.plan['id'])['questions'][0]
        self.assertTrue(all('clarification' not in i and 'exclusive_with' not in i for i in question['rubric']['rules']['items']))
        # Every export carries the original rubric, never the internal guidance.
        self.approve(self.frq.response(response['id']), selected_items=['R1'])
        for kind in ('csv', 'entry', 'audit'):
            content = self.frq.export(self.plan['id'], kind)[0]
            self.assertNotIn(CLEAR[:30], content.decode() if isinstance(content, bytes) else json.dumps(content))
        self.assertIsNone(grader_rubric(question['rubric']['rules'], None).get('clarified'))
        self.assertEqual(grader_rubric(question['rubric']['rules'], None), question['rubric']['rules'])  # A batch from before finalizations reads what it always read.

    def test_model_omissions_fail_closed_with_provider_receipt(self):
        def broken(body, host):
            result = suggestion(payload_of(body))
            result['items'].pop()
            return result
        self.host.script = broken
        status = self.check()
        self.assertEqual(status['state'], 'failed')
        self.assertIsNone(status['review'])
        self.assertIn('omitted', status['check']['error'])
        self.assertIn('Question 1', status['check']['error'])  # The failure names the question it is about.
        with connect(self.path) as db:
            # A refused answer is asked once more before the check fails; both answers keep their receipts.
            self.assertEqual([tuple(r) for r in db.execute('SELECT state,task_attempt FROM frq_cloud_calls WHERE rubric_check_id=? ORDER BY rowid', (status['check']['id'],))], [('completed', 0), ('completed', 1)])

    def resolving(self, sent, resolved=True):
        """The reviewer's resolution of one problem: R3 of each version also accepts 4, every listed concern answered."""
        return {'summary': 'R3 now accepts 4.', 'versions': [{'version': v['version'], 'summary': '', 'items': [
                    {'item_id': row['item_id'], 'clarification': f"{row['clarification']} Accept 4 as the key's answer.".strip() if row['item_id'] == 'R3' else row['clarification'],
                     'excludes': row['excludes'], 'reason': 'Alternative levels of credit.' if row['excludes'] else ''} for row in v['current']]} for v in sent['problem']['versions']],
                'concerns': [{'concern': c['concern'], 'resolved': resolved, 'how': 'R3 accepts 4, as the instructor said.' if resolved else 'The key itself must be corrected.'}
                             for c in sent['concerns']],
                'suspected_errors': [], 'consistency_issues': []}

    def resolve(self, status, comments=None, edit=None, request_id=None):
        """Resolve with a comment on each concern named by its number (by default the first: R3's suspected error)."""
        rows = copy.deepcopy(status['check']['result']['questions'])
        for row in rows:
            row.update((edit or {}).get(row['question_id'], {}))
        return self.frq.resolve_rubric_concerns(self.plan['id'], {'check_id': status['check']['id'], 'input_sha256': status['input_sha256'],
            'request_id': request_id or str(uuid.uuid4()), 'questions': rows,
            'responses': [{'concern': n, 'response': text} for n, text in ({0: 'The key accepts 4 for R3.'} if comments is None else comments).items()]})

    @staticmethod
    def resolution_asked(payload):
        return 'problem' in payload and 'concerns' in payload

    def test_the_instructor_comments_on_a_concern_and_the_reviewer_resolves_every_concern(self):
        """requirement, 2026-09-25: a comment box under each concern at the top (collapsible), and a button that asks the reviewer
        again to resolve them by augmenting its clarifications (and, they chose, the exclusions); the concerns without a comment
        are settled by the reviewer itself, consistently with the problem and the instructor's comments (v3)."""
        second = self.second_question()
        sent = []
        def reviewer(body, host):
            payload = payload_of(body)
            if self.resolution_asked(payload):
                sent.append(payload)
                return self.resolving(payload)
            return suggestion(payload)
        self.host.script = reviewer
        checked = self.check()
        listed = [issue['question_id'] for issue in checked['check']['result']['suspected_errors']]
        self.assertEqual(sorted(listed), sorted([self.qid, second]))
        mine, other = listed.index(self.qid), listed.index(second)
        with self.assertRaisesRegex(Conflict, 'does not list'):
            self.resolve(checked, comments={7: 'No such concern.'})
        # The window as the instructor left it: an unconfirmed edit on the question whose concern gets no comment.
        comments = {mine: 'The key accepts 4 for R3.', other: ''}
        queued = self.resolve(checked, comments=comments, edit={second: {'clarifications': {'R1': 'Mine.'}}}, request_id='resolve-1')
        self.assertEqual((queued['state'], queued['check']['resolves']), ('pending', checked['check']['id']))
        with self.assertRaisesRegex(Conflict, 'completed check'):  # The queued resolution is now the latest check.
            self.resolve(checked)
        self.frq.process_cloud()
        status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual((status['state'], status['check']['resolves']), ('needs_review', checked['check']['id']))
        # Every problem a concern names is asked, one request each: the commented concern with its comment, the other without one
        # but with the instructor's comment on the first as their policy elsewhere.
        by_concern = {payload['concerns'][0]['concern']: payload for payload in sent}
        self.assertEqual(sorted(by_concern), sorted([mine, other]))
        self.assertEqual((by_concern[mine]['concerns'][0]['instructor_response'], by_concern[mine]['concerns'][0]['item_ids']), ('The key accepts 4 for R3.', ['R3']))
        self.assertNotIn('instructor_feedback_elsewhere', by_concern[mine])
        self.assertNotIn('instructor_response', by_concern[other]['concerns'][0])
        self.assertEqual(by_concern[other]['instructor_feedback_elsewhere'],
                         [{'message': checked['check']['result']['suspected_errors'][mine]['message'], 'instructor_response': 'The key accepts 4 for R3.'}])
        current = {row['item_id']: row for row in by_concern[mine]['problem']['versions'][0]['current']}
        self.assertEqual((current['R2']['clarification'], current['R2']['excludes'], current['R3']['excludes']), (CLEAR, ['R3'], ['R2']))
        self.assertEqual({row['item_id']: row['clarification'] for row in by_concern[other]['problem']['versions'][0]['current']}['R1'], 'Mine.')
        result = status['check']['result']
        entries = {q['question_id']: q for q in result['questions']}
        self.assertEqual(entries[self.qid]['clarifications'], {'R2': CLEAR, 'R3': "Accept 4 as the key's answer."})
        self.assertEqual(entries[self.qid]['pairs'], [['R2', 'R3']])
        self.assertEqual(entries[second]['clarifications'], {'R1': 'Mine.', 'R3': "Accept 4 as the key's answer."})  # From the window as edited.
        self.assertEqual(result['suspected_errors'], [])
        resolution = result['resolution']
        self.assertEqual((resolution['check_id'], sorted(resolution['asked']), resolution['commented'], resolution['before'][self.qid]['clarifications']),
                         (checked['check']['id'], sorted([self.qid, second]), [mine], {'R2': CLEAR}))
        rows = {row['question_id']: row for row in resolution['resolved']}
        self.assertEqual((rows[self.qid]['comment'], rows[self.qid].get('without_comment')), ('The key accepts 4 for R3.', None))
        self.assertEqual((rows[second].get('comment'), rows[second]['without_comment']), (None, True))
        self.assertIn('2 concerns were sent, one request per problem with all its versions (2 problems): 1 with your comment and 1 for the reviewer '
                      'to settle consistently with the problem and your comments. 2 resolved, 0 still open.', result['summary'])
        # Sending the same request again returns this resolution; nothing is asked twice.
        self.assertEqual(self.resolve(checked, comments=comments, edit={second: {'clarifications': {'R1': 'Mine.'}}}, request_id='resolve-1')['check']['id'],
                         status['check']['id'])
        with self.assertRaisesRegex(Conflict, 'no concerns'):
            self.resolve(status, comments={})
        confirmed = self.confirm(status)
        self.assertEqual(confirmed['state'], 'confirmed')
        policy = {q['question_id']: q for q in confirmed['review']['policy']['questions']}
        self.assertEqual(policy[self.qid]['clarifications']['R3'], "Accept 4 as the key's answer.")

    def test_a_concern_guidance_cannot_settle_stays_listed_and_a_failed_resolution_keeps_the_review(self):
        checked = self.check()
        answers = iter([None, None])  # Two refused answers (no concern answered): the problem fails.
        def reviewer(body, host):
            payload = payload_of(body)
            if not self.resolution_asked(payload):
                return suggestion(payload)
            refused = next(answers, 'answer')
            return {**self.resolving(payload), 'concerns': []} if refused is None else self.resolving(payload, resolved=False)
        self.host.script = reviewer
        self.resolve(checked, comments={0: 'First try.'})
        self.frq.process_cloud()
        status = self.frq.rubric_quality(self.plan['id'])
        # The review it answered stands as it was, with the comments kept for another try.
        self.assertEqual((status['state'], status['check']['id']), ('needs_review', checked['check']['id']))
        self.assertEqual((status['resolution_failed']['responses'], status['resolution_failed']['cancelled']), ([{'concern': 0, 'response': 'First try.'}], False))
        self.assertIn('could not be resolved', status['resolution_failed']['error'])
        with self.assertRaisesRegex(Conflict, 'completed check'):
            self.resolve({**status, 'check': {**status['check'], 'id': status['resolution_failed']['check_id']}})
        self.resolve(status, comments={0: 'The rubric is wrong; I will fix R3.'})
        self.frq.process_cloud()
        status = self.frq.rubric_quality(self.plan['id'])
        [still] = status['check']['result']['suspected_errors']
        self.assertEqual((still['item_ids'], still['note'], still['comment']), (['R3'], 'The key itself must be corrected.', 'The rubric is wrong; I will fix R3.'))
        self.assertEqual(status['check']['result']['resolution']['resolved'], [])
        # A round needs no comment: the concern still open is sent with the comment kept beside it from the round before.
        sent = []
        self.host.script = lambda body, host: (sent.append(payload_of(body)), self.resolving(payload_of(body), resolved=False))[1]
        self.resolve(status, comments={})
        self.frq.process_cloud()
        self.assertEqual(sent[0]['concerns'][0]['instructor_response'], 'The rubric is wrong; I will fix R3.')
        status = self.frq.rubric_quality(self.plan['id'])
        self.host.script = reviewer
        # A cancelled resolution also leaves the review it answered.
        self.resolve(status, comments={0: 'Third try.'})
        self.frq.cancel_rubric_quality(self.plan['id'], {})
        cancelled = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual((cancelled['check']['id'], cancelled['resolution_failed']['cancelled'], cancelled['resolution_failed']['responses']),
                         (status['check']['id'], True, [{'concern': 0, 'response': 'Third try.'}]))

    def test_cancel_stops_a_waiting_check_without_sending(self):
        confirm_for_review(self.frq, self.plan['id'])
        status = self.frq.rubric_quality(self.plan['id'])
        self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
        self.frq.cancel_rubric_quality(self.plan['id'], {})
        self.frq.process_cloud()
        self.assertEqual(len(self.host.requests), 0)
        status = self.frq.cancel_rubric_quality(self.plan['id'], {})  # Nothing is waiting: asking again changes nothing.
        self.assertEqual((status['state'], status['check']['status']), ('needed', 'cancelled'))

    def test_settled_model_result_survives_restart_before_check_publication(self):
        confirm_for_review(self.frq, self.plan['id'])
        status = self.frq.rubric_quality(self.plan['id'])
        self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
        kind, task, _ = self.frq._cloud_claim(1)[0]
        self.assertEqual(kind, 'rubric_quality')
        profile, context = json.loads(task['profile_json']), json.loads(task['input_json'])
        # The question's answer had settled (and was billed) when the process died, before the check could publish its result.
        self.frq.cloud_call({'rubric_check_id': task['id'], 'step': 'rubric_quality:' + self.qid, 'task_attempt': 0}, 'rubric_reviewer', profile['endpoint'],
            {'prompt': 'rubric_quality', 'payload': question_payload(context, context['questions'][0]), 'images': []}, profile['settings'])
        self.assertEqual(len(self.host.requests), 1)
        self.frq._cloud_recover()
        self.frq.process_cloud()
        self.assertEqual(self.frq.rubric_quality(self.plan['id'])['state'], 'needs_review')
        self.assertEqual(len(self.host.requests), 1)

    def test_policy_change_suspends_acceptance_and_refuses_an_old_report(self):
        response, _ = self.grade(CORRECT)
        scope = response['result']['signals']['scope']
        checksum = response['result']['signals']['scope_sha256']
        # Evidence eligibility itself is covered by test_grading_acceptance; isolate the scope boundary here.
        report = {'reports': [{'scope': scope, 'blind': {'eligible': True, 'upper_bound_95': .01, 'reviewed': 500}}]}
        command = {'scope_sha256': checksum, 'confirm_automatic_acceptance': True}
        with patch.object(self.frq, 'acceptance_report', return_value=report):
            self.frq.enable_acceptance(self.plan['id'], self.qid, command)
            with connect(self.path) as db:
                self.assertIsNotNone(self.frq.permission(db, checksum))
            # The instructor confirms another finalization: what graders read has changed, so the permission no longer applies.
            self.confirm(self.frq.rubric_quality(self.plan['id']), clarifications={'R2': 'Another reading.'})
            with connect(self.path) as db:
                self.assertIsNone(self.frq.permission(db, checksum))
            with self.assertRaisesRegex(Conflict, 'rubric quality policy changed'):
                self.frq.enable_acceptance(self.plan['id'], self.qid, command)

    def test_confirmed_provider_failure_retries_with_the_same_check(self):
        def temporary(body, host):
            if len(host.requests) == 1:
                return ('status', 503, {'Retry-After': '0'}, {'error': {'message': 'Synthetic capacity'}})
            return suggestion(payload_of(body))
        self.host.script = temporary
        status = self.check()
        for _ in range(3):
            if status['state'] == 'needs_review': break
            with connect(self.path) as db:
                db.execute("UPDATE frq_rubric_quality_checks SET not_before='' WHERE status='pending'")
            self.frq.process_cloud()
            status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual(status['state'], 'needs_review')
        self.assertEqual(len(self.host.requests), 2)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_quality_checks WHERE input_sha256=?', (status['input_sha256'],)).fetchone()[0], 1)

    def test_validation_rejects_unknown_self_and_missing_items(self):
        context = self.frq.rubric_quality(self.plan['id'])['context']
        question = context['questions'][0]
        raw = suggestion(question_payload(context, question))
        self.assertEqual(validate_question(raw, question)[0]['pairs'], [['R2', 'R3']])
        for kind in ('error', 'item', 'self', 'missing', 'shape'):
            bad = copy.deepcopy(raw)
            if kind == 'error': bad['suspected_errors'][0]['item_ids'] = ['R9']
            elif kind == 'item': bad['items'][1]['excludes'] = ['foreign-item']
            elif kind == 'self': bad['items'][1]['excludes'] = ['R2']
            elif kind == 'missing': bad['items'].pop()
            else: bad = [bad]
            with self.subTest(kind=kind), self.assertRaises(ValueError): validate_question(bad, question)
        with self.assertRaises(ValueError): normalize_questions([], context)
        for value in ([], {}, None, 'not-configured'):
            with self.subTest(value=value), self.assertRaises(ValueError): config.validate({'rubric_reviewer': value})


class ClarifyInReview(QualityCase):
    """requirement, 2026-10-03: a shared rubric concern is settled beside the review desk. One question's clarifications and pairs
    become a new confirmation that keeps everything else of the standing one; only that standing confirmation can be clarified."""
    def clarify(self, status, **extra):
        q = status['context']['questions'][0]
        kept = status['review']['policy']['questions'][0]
        data = {'version': status['version'], 'review_id': status['review']['id'], 'question_id': q['question_id'], 'rubric_id': q['rubric_id'],
                'clarifications': {**kept['clarifications'], 'R2': 'Credit the setup only when the substitution is written out.'},
                'pairs': kept['pairs'], 'confirm_review': True, **extra}
        return self.frq.clarify_question(self.plan['id'], data)

    def test_one_question_is_clarified_as_a_new_confirmation_and_approves_nothing(self):
        self.confirm(self.check())
        status = self.frq.rubric_quality(self.plan['id'])
        before = status['review']
        response, _ = self.grade(CORRECT)
        answer = self.clarify(status)
        self.assertEqual(answer['confirmation']['changed'], 1)
        after = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual(after['state'], 'confirmed')
        self.assertEqual(after['review']['id'], answer['review_id'])
        self.assertNotEqual(after['review']['id'], before['id'])
        self.assertEqual(after['review']['check_id'], before['check_id'])
        entry = after['review']['policy']['questions'][0]
        self.assertEqual(entry['clarifications']['R2'], 'Credit the setup only when the substitution is written out.')
        for key in ('rules', 'consistency_issues', 'suspected_errors'):
            self.assertEqual(after['review']['policy'].get(key), before['policy'].get(key), key)
        # No response was approved by it: no review was recorded for the waiting proposal.
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_reviews WHERE response_id=?', (response['id'],)).fetchone()[0], 0)
            event = db.execute("SELECT detail_json FROM frq_events WHERE kind='rubric_quality_confirmed' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertEqual(json.loads(event)['clarified'], self.qid)

    def test_a_stale_or_waiting_finalization_is_refused_and_nothing_is_recorded(self):
        self.confirm(self.check())
        status = self.frq.rubric_quality(self.plan['id'])
        with connect(self.path) as db:
            reviews = db.execute('SELECT COUNT(*) FROM frq_rubric_quality_reviews').fetchone()[0]
        with self.assertRaises(Conflict):
            self.clarify(status, review_id='an-earlier-confirmation')
        with self.assertRaises(Conflict):
            self.clarify(status, rubric_id='another-revision')
        with self.assertRaises(ValueError):
            self.clarify(status, confirm_review=False)
        with self.assertRaises(ValueError):
            self.clarify(status, clarifications={'NOT-A-LINE': 'x'})
        # A newer check asked for and waiting: what it will propose has not been read, so this question cannot be clarified here.
        self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
        self.assertIsNone(self.frq.rubric_quality(self.plan['id'])['review'])  # asking again unsets the plan's pointer
        with self.assertRaises(Conflict):
            self.clarify(status)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_quality_reviews').fetchone()[0], reviews)

    def test_confirming_in_the_finalization_answers_as_before(self):
        confirmed = self.confirm(self.check())
        self.assertEqual(sorted(confirmed['confirmation']), ['changed', 'kept', 'suspended'])


class QualityUploads(unittest.TestCase):
    managed = False
    document_version = uploads.RubricUploads.document_version
    ready_batch = uploads.RubricUploads.ready_batch
    reviewed_source = uploads.RubricUploads.reviewed_source
    setUp = uploads.RubricUploads.setUp
    upload = uploads.RubricUploads.upload

    def test_unused_coursewide_pdf_is_not_an_extra_term_upload_requirement(self):
        old = self.document_version('Another term’s PDF')
        self.upload(assignment_version_id=self.version_id)
        status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual([d['id'] for d in status['context']['documents']], [self.version_id])
        self.assertEqual(status['missing'], [])
        self.ready_batch(version_id=old)
        status = self.frq.rubric_quality(self.plan['id'])
        self.assertIn('Another term’s PDF', status['missing'])

    def test_versioned_multipart_upload_accepts_all_four_fields(self):
        import io
        from test_gradescope_rubrics import encode, export_rows
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            response = self.client.post(f"/api/grading/assignments/{self.plan['id']}/rubric-uploads", data={
                'file': (io.BytesIO(encode(export_rows()).encode()), '1_Question_18.csv'), 'request_id': str(uuid.uuid4()),
                'assignment_version_id': self.version_id, 'exam_version': 'A'}, headers=uploads.exam_fixture.HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json['exam_version'], 'A')

    def test_requires_all_known_versions_and_never_sends_csv_student_rows(self):
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            self.upload(exam_version='A')
            status = self.frq.rubric_quality(self.plan['id'])
            self.assertEqual(status['state'], 'missing')
            self.assertIn('Version B', status['missing'])
            with self.assertRaisesRegex(Conflict, 'every version'):
                self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
            self.upload(exam_version='B')
            status = self.frq.rubric_quality(self.plan['id'])
            self.assertEqual(status['state'], 'needed')
            self.assertEqual({q['exam_version'] for q in status['context']['questions']}, {'A', 'B'})
            self.assertTrue(all('source_text' not in q for q in status['context']['questions']))
            self.assertNotIn('student', json.dumps(status['context']).lower())


class QualityAcrossVersions(unittest.TestCase):
    """requirement, 2026-09-25: the rubric reviewer sees every version of a problem at once, so everything stays consistent across
    them. Still one PROBLEM per request, never the whole exam."""
    managed = False
    document_version = uploads.RubricUploads.document_version
    ready_batch = uploads.RubricUploads.ready_batch
    reviewed_source = uploads.RubricUploads.reviewed_source
    setUp = uploads.RubricUploads.setUp
    upload = uploads.RubricUploads.upload

    def run_check(self, answer):
        sent = []

        def reviewer(owner, role, endpoint, request, settings):
            sent.append((owner['step'], request['prompt'], request['payload']))
            return {'metadata': {}, 'raw': {}, 'output': answer(request['prompt'], request['payload'])}
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            self.upload(exam_version='A')
            self.upload(exam_version='B')
            confirm_for_review(self.frq, self.plan['id'])
            status = self.frq.rubric_quality(self.plan['id'])
            with patch.object(self.frq, 'cloud_call', side_effect=reviewer), patch.object(self.frq, 'rubric_problem_images', return_value=[]):
                self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
                for _ in range(5):
                    if self.frq.rubric_quality(self.plan['id'])['state'] not in ('pending', 'running'):
                        break
                    self.frq.process_cloud()
            return self.frq.rubric_quality(self.plan['id']), sent

    @staticmethod
    def reviewed(payload, clarify=''):
        versions = payload['problem']['versions']
        last = versions[-1]
        return {'summary': 'Version B pays less for the setup.',
                'suspected_errors': [{'version': last['version'], 'item_ids': [last['question']['rubric']['items'][1]['id']], 'message': 'Check this line.'}],
                'consistency_issues': [{'versions': ['A', 'B'], 'message': 'The setup line is worth less on Version B.'}] if len(versions) > 1 else [],
                'versions': [{'version': v['version'], 'summary': '', 'items': [{'item_id': item['id'], 'clarification': clarify if index == 1 else '',
                    'excludes': [], 'reason': ''} for index, item in enumerate(v['question']['rubric']['items'])]} for v in versions]}

    def test_each_problem_is_one_request_with_every_version_and_its_differences_are_reported(self):
        def answer(prompt, payload):
            if prompt == 'rubric_quality_group':  # Gradescope uploads say nothing about which question is which: the model groups them.
                self.assertEqual(sorted(q['version'] for q in payload['questions']), ['A', 'B'])
                self.assertTrue(all(q['rubric_lines'] for q in payload['questions']))
                return {'groups': [{'refs': [q['ref'] for q in payload['questions']]}], 'notes': []}
            return self.reviewed(payload, 'Credit the setup the same way on both versions.')
        status, sent = self.run_check(answer)
        self.assertEqual(status['state'], 'needs_review')
        self.assertEqual([(step.split(':')[1], prompt) for step, prompt, _ in sent], [('group', 'rubric_quality_group'), ('problem', 'rubric_quality_versions')])
        problem = sent[1][2]
        self.assertEqual(sorted(problem), ['problem', 'rules'])
        self.assertEqual([v['version'] for v in problem['problem']['versions']], ['A', 'B'])
        self.assertTrue(all(sorted(v) == ['question', 'reference', 'version'] for v in problem['problem']['versions']))
        result = status['check']['result']
        ids = {q['exam_version']: q['question_id'] for q in status['context']['questions']}
        self.assertEqual((result['mode'], result['families']), ('per_problem', [[ids['A'], ids['B']]]))
        # Each version's findings stay with its own question, so confirming, grading and the record are unchanged.
        self.assertEqual(sorted(q['question_id'] for q in result['questions']), sorted(ids.values()))
        self.assertTrue(all(list(q['clarifications'].values()) == ['Credit the setup the same way on both versions.'] for q in result['questions']))
        self.assertEqual([issue['question_id'] for issue in result['suspected_errors']], [ids['B']])
        self.assertEqual(result['consistency_issues'], [{'question_ids': [ids['A'], ids['B']], 'message': 'The setup line is worth less on Version B.'}])
        self.assertTrue(result['summary'].startswith('1 problem checked, each with all its versions side by side in one request'), result['summary'])
        confirmed = self.frq.confirm_rubric_quality(self.plan['id'], {'version': status['version'], 'check_id': status['check']['id'],
            'input_sha256': status['input_sha256'], 'confirm_review': True, 'questions': copy.deepcopy(result['questions'])})
        self.assertEqual(confirmed['state'], 'confirmed')
        self.assertEqual(len(confirmed['review']['policy']['consistency_issues']), 1)

    def test_a_grouping_refused_twice_checks_the_unlinked_questions_alone_and_says_so(self):
        def answer(prompt, payload):
            if prompt == 'rubric_quality_group':
                return {'groups': [{'refs': [payload['questions'][0]['ref']]}], 'notes': []}  # leaves a question out: refused
            return self.reviewed(payload)
        status, sent = self.run_check(answer)
        self.assertEqual([prompt for _, prompt, _ in sent], ['rubric_quality_group', 'rubric_quality_group', 'rubric_quality_versions', 'rubric_quality_versions'])
        self.assertTrue(all(len(payload['problem']['versions']) == 1 for _, prompt, payload in sent if prompt == 'rubric_quality_versions'))
        self.assertEqual(status['state'], 'needs_review')
        self.assertIn('could not be grouped', status['check']['result']['summary'])


    def test_linked_versions_one_finding_per_problem_saved_links_and_nothing_goes_stale(self):
        """LINKED VERSIONS (requirement, 2026-10-02): the reviewer names a finding once for every version and returns the parallel
        lines; the instructor's link decisions are saved on the plan, win on the next check, and change no fingerprint."""
        def answer(prompt, payload):
            if prompt == 'rubric_quality_group':
                return {'groups': [{'refs': [q['ref'] for q in payload['questions']]}], 'notes': []}
            versions = payload['problem']['versions']
            ids = [item['id'] for item in versions[0]['question']['rubric']['items']]
            answer = self.reviewed(payload)
            answer['suspected_errors'] = [{'versions': ['A', 'B'], 'item_ids': [{'version': v, 'item_ids': [ids[1]]} for v in 'AB'],
                'version_notes': [{'version': 'B', 'note': 'B names another value.'}], 'message': 'Which values earn this line?',
                'title': 'Which values count?', 'decision': 'Should the key value count?', 'group': '', 'choices': []}]
            answer['parallel_lines'] = [{'kind': 'same', 'lines': [{'version': v, 'item_id': ids[1]} for v in 'AB'], 'note': ''},
                                        {'kind': 'related', 'lines': [{'version': v, 'item_id': ids[2]} for v in 'AB'], 'note': 'Another outcome on B.'}]
            return answer
        status, sent = self.run_check(answer)
        result = status['check']['result']
        ids = {q['exam_version']: q['question_id'] for q in status['context']['questions']}
        items = [item['id'] for item in status['context']['questions'][0]['rubric']['items']]
        rows = result['suspected_errors']
        self.assertEqual(sorted(row['question_id'] for row in rows), sorted(ids.values()))
        self.assertEqual(len({row['shared'] for row in rows}), 1)
        self.assertEqual([row.get('version_note') for row in sorted(rows, key=lambda row: row['question_id'] != ids['B'])][0], 'B names another value.')
        self.assertEqual([(link['kind'], link['origin']) for link in result['line_links']], [('same', 'reviewer'), ('related', 'reviewer')])
        self.assertNotIn('known_links', sent[-1][2])  # uploaded rubrics say nothing about their lines: no candidates to send
        # The plan's links come from the reviewer; the instructor's decisions are saved and change nothing a check or grade reads.
        links = self.frq.rubric_links(self.plan['id'])
        self.assertEqual(links['families'], [[ids['A'], ids['B']]])
        self.assertEqual([s['kind'] for s in links['sets']], ['same', 'related'])
        before = self.frq.rubric_quality(self.plan['id'])
        confirmed = self.frq.confirm_rubric_quality(self.plan['id'], {'version': before['version'], 'check_id': before['check']['id'],
            'input_sha256': before['input_sha256'], 'confirm_review': True, 'questions': copy.deepcopy(result['questions'])})
        self.assertEqual(confirmed['state'], 'confirmed')
        line = lambda version, n: {'question_id': ids[version], 'item_id': items[n]}
        after = self.frq.set_rubric_links(self.plan['id'], {'operations': [{'op': 'unlink', 'line': line('B', 1)}, {'op': 'kind', 'line': line('A', 2), 'kind': 'same'}]})
        self.assertEqual([(s['kind'], s['origin']) for s in after['sets']], [('same', 'instructor')])
        now = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual((now['state'], now['input_sha256'], now['standing'].keys()), ('confirmed', before['input_sha256'], confirmed['standing'].keys()))
        # Linking a line to another version's line takes it out of its own set first.
        relinked = self.frq.set_rubric_links(self.plan['id'], {'operations': [{'op': 'link', 'line': line('B', 1), 'to': line('A', 1), 'kind': 'related'}]})
        self.assertIn(('related', ((ids['A'], items[1]), (ids['B'], items[1]))), [(s['kind'], tuple((l['question_id'], l['item_id']) for l in s['lines'])) for s in relinked['sets']])
        for bad in ({'operations': [{'op': 'link', 'line': line('A', 1), 'to': line('A', 2), 'kind': 'same'}]},
                    {'operations': [{'op': 'kind', 'line': {'question_id': ids['A'], 'item_id': 'nope'}, 'kind': 'same'}]},
                    {'operations': [{'op': 'kind', 'line': line('A', 1), 'kind': 'none'}]}, {'operations': []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.frq.set_rubric_links(self.plan['id'], bad)
        with connect(self.frq.path) as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute('UPDATE frq_rubric_line_links SET kind=?', ('same',))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute('DELETE FROM frq_rubric_line_links')
            with self.assertRaises(sqlite3.DatabaseError):  # both lines must be of questions of the plan
                db.execute('INSERT INTO frq_rubric_line_links VALUES(?,?,?,?,?,?,?,?,?)', ('x', self.plan['id'], ids['A'], 'R1', 'zz-not-a-question', 'R1', 'same', 'workspace', 't'))
        # The next check tells the reviewer the instructor's decisions as fixed links.
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            status = self.frq.rubric_quality(self.plan['id'])
            self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
        with connect(self.frq.path) as db:
            told = json.loads(db.execute('SELECT input_json FROM frq_rubric_quality_checks ORDER BY rowid DESC LIMIT 1').fetchone()[0])['known_links']
        # The later link replaced the unlink of the same pair: the latest decision of a pair is the one that stands.
        self.assertEqual(sorted((link['kind'], tuple((line['version'], line['item_id']) for line in link['lines'])) for link in told),
                         [('related', (('A', items[1]), ('B', items[1]))), ('same', (('A', items[2]), ('B', items[2])))])
        self.assertTrue(all(link['origin'] == 'instructor' for link in told))
        self.frq.cancel_rubric_quality(self.plan['id'], {})

    def test_a_shared_decision_is_resolved_with_the_links_as_they_stand(self):
        sent = []

        def answer(prompt, payload):
            sent.append((prompt, payload))
            if prompt == 'rubric_quality_group':
                return {'groups': [{'refs': [q['ref'] for q in payload['questions']]}], 'notes': []}
            versions = payload['problem']['versions']
            ids = [item['id'] for item in versions[0]['question']['rubric']['items']]
            reply = self.reviewed(payload)
            if prompt == 'rubric_quality_resolve':
                reply.update(concerns=[{'concern': c['concern'], 'resolved': True, 'how': 'Applied on A; B does not ask it.', 'not_applicable': ['B'] if c['versions'] == ['B'] else [],
                                        'title': '', 'decision': '', 'group': '', 'choices': []} for c in payload['concerns']], suspected_errors=[], consistency_issues=[])
                return reply
            reply['suspected_errors'] = [{'versions': ['A', 'B'], 'item_ids': [{'version': v, 'item_ids': [ids[1]]} for v in 'AB'], 'version_notes': [],
                                          'message': 'Which values earn this line?', 'title': 'Which values count?', 'decision': 'Count the key value?', 'group': '', 'choices': []}]
            reply['consistency_issues'] = []
            reply['parallel_lines'] = [{'kind': 'same', 'lines': [{'version': v, 'item_id': ids[1]} for v in 'AB'], 'note': ''}]
            return reply
        status, _ = self.run_check(answer)
        concerns = status['check']['result']['suspected_errors']

        def reviewer(owner, role, endpoint, request, settings):
            return {'metadata': {}, 'raw': {}, 'output': answer(request['prompt'], request['payload'])}
        with patch.object(self.frq, 'cloud_call', side_effect=reviewer), patch.object(self.frq, 'rubric_problem_images', return_value=[]):
            self.frq.resolve_rubric_concerns(self.plan['id'], {'request_id': str(uuid.uuid4()), 'check_id': status['check']['id'], 'input_sha256': status['input_sha256'],
                'questions': copy.deepcopy(status['check']['result']['questions']), 'responses': [{'concern': n, 'response': 'Count the key value.'} for n in range(len(concerns))]})
            for _ in range(5):
                self.frq.process_cloud()
        resolve = [payload for prompt, payload in sent if prompt == 'rubric_quality_resolve'][0]
        self.assertEqual(len({c['shared'] for c in resolve['concerns']}), 1)
        self.assertEqual([link['kind'] for link in resolve['known_links']], ['same'])
        result = self.frq.rubric_quality(self.plan['id'])['check']['result']
        self.assertEqual(len(result['line_links']), 1)  # carried from the check it answered
        resolved = result['resolution']['resolved']
        self.assertEqual(len(resolved), 2)
        self.assertEqual(sorted(row.get('not_applicable', []) for row in resolved), [[], ['B']])
    def test_one_rule_settled_on_every_version_is_one_confirmation(self):
        status, _ = self.run_check(lambda prompt, payload: {'groups': [{'refs': [q['ref'] for q in payload['questions']]}], 'notes': []}
                                   if prompt == 'rubric_quality_group' else self.reviewed(payload))
        self.frq.confirm_rubric_quality(self.plan['id'], {'version': status['version'], 'check_id': status['check']['id'],
            'input_sha256': status['input_sha256'], 'confirm_review': True, 'questions': copy.deepcopy(status['check']['result']['questions'])})
        status = self.frq.rubric_quality(self.plan['id'])
        kept = {e['question_id']: e for e in status['review']['policy']['questions']}
        rows = [{'question_id': q['question_id'], 'rubric_id': q['rubric_id'], 'pairs': kept[q['question_id']]['pairs'],
                 'clarifications': {**kept[q['question_id']]['clarifications'], q['rubric']['items'][0]['id']: f"Own letters count on Version {q['exam_version']}."}}
                for q in status['context']['questions']]
        with connect(self.path) as db:
            before = db.execute('SELECT COUNT(*) FROM frq_rubric_quality_reviews').fetchone()[0]
        answer = self.frq.clarify_question(self.plan['id'], {'version': status['version'], 'review_id': status['review']['id'], 'questions': rows, 'confirm_review': True})
        self.assertEqual((answer['confirmation']['kept'], answer['confirmation']['changed']), (0, 2))
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_quality_reviews').fetchone()[0], before + 1)
        after = self.frq.rubric_quality(self.plan['id'])['review']['policy']['questions']
        for q in status['context']['questions']:
            entry = next(e for e in after if e['question_id'] == q['question_id'])
            self.assertEqual(entry['clarifications'][q['rubric']['items'][0]['id']], f"Own letters count on Version {q['exam_version']}.")
        with self.assertRaises(ValueError):  # each question once
            self.frq.clarify_question(self.plan['id'], {'version': status['version'] + 1, 'review_id': answer['review_id'], 'questions': rows + rows[:1], 'confirm_review': True})

    def test_clarifying_one_version_in_review_keeps_the_other_versions_entry_and_grades(self):
        status, _ = self.run_check(lambda prompt, payload: {'groups': [{'refs': [q['ref'] for q in payload['questions']]}], 'notes': []}
                                   if prompt == 'rubric_quality_group' else self.reviewed(payload))
        self.frq.confirm_rubric_quality(self.plan['id'], {'version': status['version'], 'check_id': status['check']['id'],
            'input_sha256': status['input_sha256'], 'confirm_review': True, 'questions': copy.deepcopy(status['check']['result']['questions'])})
        status = self.frq.rubric_quality(self.plan['id'])
        a, b = sorted(status['context']['questions'], key=lambda q: q['exam_version'])
        b_before = next(e for e in status['review']['policy']['questions'] if e['question_id'] == b['question_id'])
        kept = next(e for e in status['review']['policy']['questions'] if e['question_id'] == a['question_id'])
        line = a['rubric']['items'][0]['id']
        answer = self.frq.clarify_question(self.plan['id'], {'version': status['version'], 'review_id': status['review']['id'], 'question_id': a['question_id'],
            'rubric_id': a['rubric_id'], 'clarifications': {**kept['clarifications'], line: 'Only the written setup counts.'}, 'pairs': kept['pairs'], 'confirm_review': True})
        self.assertEqual((answer['confirmation']['kept'], answer['confirmation']['changed']), (1, 1))
        after = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual(next(e for e in after['review']['policy']['questions'] if e['question_id'] == b['question_id']), b_before)
        self.assertEqual(after['standing'][b['question_id']]['guidance_sha256'], status['standing'][b['question_id']]['guidance_sha256'])
        self.assertNotEqual(after['standing'][a['question_id']]['guidance_sha256'], status['standing'][a['question_id']]['guidance_sha256'])


class VersionGrouping(unittest.TestCase):
    def test_how_a_rubric_was_made_links_the_versions_of_a_problem(self):
        from grading.rubric_quality import linked_groups, origins
        edited = {'name': 'instructor-edited', 'based_on': {'name': 'model-adapted-v1', 'adapted_from': {'question_id': 'a1'}}}
        self.assertEqual(origins(edited), [['question', 'a1']])
        self.assertEqual(origins({'name': 'model-drafted-versions-v1', 'generation_id': 'g', 'family': 'P2'}), [['family', 'g', 'P2']])
        self.assertEqual(origins({'name': 'gradescope-upload-v1'}), [])
        question = lambda qid, version, *made: {'question_id': qid, 'exam_version': version, 'origins': list(made)}
        groups = linked_groups([question('a1', 'A'), question('b1', 'B', ['question', 'a1']), question('c1', 'C', *origins(edited)),
                                question('a2', 'A', ['family', 'g', 'P2']), question('b2', 'B', ['family', 'g', 'P2']),
                                question('a3', 'A'), question('a4', 'A', ['question', 'a3'])])  # two of Version A cannot be one problem
        self.assertEqual(sorted(map(sorted, groups)), [['a1', 'b1', 'c1'], ['a2', 'b2']])


class ProblemFollowUp(unittest.TestCase):
    def test_a_version_whose_lines_still_add_up_to_too_much_is_asked_once_more_about_exactly_those_lines(self):
        from grading.rubric_quality import BUILTIN, RubricQualityMixin, builtin_exclusions
        rubric = {'mode': 'positive', 'items': [{'id': 'R1', 'description': 'Correct answer with supporting work: $$x=2$$', 'points': 4},
                                                {'id': 'R2', 'description': 'Solves by substitution', 'points': 3},
                                                {'id': 'R3', 'description': 'Solves by elimination', 'points': 3},
                                                {'id': 'R4', 'description': 'No answer or completely incorrect', 'points': 0}]}
        family = [{'question_id': f'q{v}', 'rubric_id': f'r{v}', 'exam_version': v, 'title': 'Question 1', 'printed_number': '1', 'max_points': 4,
                   'rubric': rubric, 'builtin': builtin_exclusions(rubric, 4)} for v in 'AB']
        asked = []

        def call(owner, role, endpoint, request, settings):
            asked.append(request['payload'])
            pairs = {'R2': ['R3']} if len(asked) > 1 else {}  # the follow-up pairs the two methods on both versions
            return {'metadata': {}, 'raw': {}, 'output': {'summary': '', 'suspected_errors': [], 'consistency_issues': [], 'versions': [
                {'version': v, 'summary': '', 'items': [{'item_id': i['id'], 'clarification': '', 'excludes': pairs.get(i['id'], []), 'reason': 'alternative methods' if i['id'] in pairs else ''}
                                                        for i in rubric['items']]} for v in 'AB']}}
        reviewer = RubricQualityMixin()
        reviewer.cloud_call = call
        found = reviewer.review_problem({'id': 'check'}, {'endpoint': {'modality': 'text'}, 'settings': {}}, {'rules': BUILTIN}, family)
        self.assertEqual(len(asked), 2)
        self.assertEqual([(entry['version'], entry['lines']) for entry in asked[1]['unresolved']], [('A', ['R2', 'R3']), ('B', ['R2', 'R3'])])
        self.assertEqual({qid: answer[0]['pairs'] for qid, answer in found['answers'].items()}, {'qA': [['R2', 'R3']], 'qB': [['R2', 'R3']]})


class QualityOnlyWhatChanged(unittest.TestCase):
    """A CHECK ASKS ONLY WHAT DOES NOT STAND (requirement, 2026-10-05: Quiz 4's makeup Version E sent every version's rubrics to the
    reviewer again). The confirmed versions still come along, side by side, as the settled reference; only the others are answered
    for, and confirming keeps the settled ones' guidance and grades exactly. A re-run that never finished hides nothing."""
    managed = False
    document_version = uploads.RubricUploads.document_version
    ready_batch = uploads.RubricUploads.ready_batch
    reviewed_source = uploads.RubricUploads.reviewed_source
    upload = uploads.RubricUploads.upload
    reviewed = staticmethod(QualityAcrossVersions.reviewed)

    def setUp(self):
        uploads.RubricUploads.setUp(self)
        self.sent = []

    def answer(self, prompt, payload):
        if prompt == 'rubric_quality_group':  # every uploaded question is one problem
            return {'groups': [{'refs': [q['ref'] for q in payload['questions']]}], 'notes': []}
        if prompt == 'rubric_quality':
            return {'summary': '', 'suspected_errors': [], 'items': [{'item_id': item['id'], 'clarification': 'Asked alone.' if n == 1 else '',
                    'excludes': [], 'reason': ''} for n, item in enumerate(payload['question']['rubric']['items'])]}
        return self.reviewed(payload, 'Credit the setup the same way on every version.')

    def queue(self, request_id=None, answer=None):
        def reviewer(owner, role, endpoint, request, settings):
            self.sent.append((owner['step'], request['prompt'], request['payload']))
            return {'metadata': {}, 'raw': {}, 'output': (answer or self.answer)(request['prompt'], request['payload'])}
        confirm_for_review(self.frq, self.plan['id'])
        status = self.frq.rubric_quality(self.plan['id'])
        with patch.object(self.frq, 'cloud_call', side_effect=reviewer), patch.object(self.frq, 'rubric_problem_images', return_value=[]):
            self.frq.queue_rubric_quality(self.plan['id'], {'request_id': request_id or str(uuid.uuid4()), 'input_sha256': status['input_sha256']})
            if request_id is None:
                for _ in range(5):
                    if self.frq.rubric_quality(self.plan['id'])['state'] not in ('pending', 'running'):
                        break
                    self.frq.process_cloud()
        return self.frq.rubric_quality(self.plan['id'])

    def confirm(self, status):
        return self.frq.confirm_rubric_quality(self.plan['id'], {'version': status['version'], 'check_id': status['check']['id'],
            'input_sha256': status['input_sha256'], 'confirm_review': True, 'questions': copy.deepcopy(status['check']['result']['questions'])})

    def confirmed_versions(self, letters='AB'):
        with patch.object(self.frq, 'chart_versions', return_value=list(letters)):
            for letter in letters:
                self.upload(exam_version=letter)
            first = self.queue()
            self.assertEqual(first['state'], 'needs_review')
            self.assertNotIn('settled', first['check']['result'])
            done = self.confirm(first)
            self.assertEqual(done['state'], 'confirmed')
            return done

    def test_a_version_added_after_confirmation_is_checked_beside_the_confirmed_ones(self):
        before = self.confirmed_versions()
        confirmed = {row['question_id']: row for row in before['review']['policy']['questions']}
        self.assertNotIn('to_check', before)  # every question stands: a check would ask them all again
        self.sent.clear()

        def answer(prompt, payload):
            if prompt != 'rubric_quality_added':
                return self.answer(prompt, payload)
            versions = payload['problem']['versions']
            self.assertEqual([(v['version'], v.get('settled', False)) for v in versions], [('A', True), ('B', True), ('C', False)])
            items = versions[0]['question']['rubric']['items']
            # A settled version arrives with its confirmed guidance line by line, as the reference.
            self.assertEqual([line['item_id'] for line in versions[0]['confirmed']], [item['id'] for item in items])
            self.assertEqual(versions[0]['confirmed'][1]['clarification'], 'Credit the setup the same way on every version.')
            self.assertNotIn('confirmed', versions[2])
            echo = {'version': 'A', 'summary': '', 'items': [{'item_id': item['id'], 'clarification': 'Changed!', 'excludes': [], 'reason': ''} for item in items]}
            asked = {'version': 'C', 'summary': '', 'items': [{'item_id': item['id'], 'clarification': 'As on A and B.' if n == 1 else '', 'excludes': [], 'reason': ''}
                                                          for n, item in enumerate(items)]}
            return {'summary': 'C follows A and B.', 'versions': [echo, asked], 'parallel_lines': [],
                    'suspected_errors': [{'versions': ['A', 'C'], 'item_ids': [{'version': 'A', 'item_ids': [items[2]['id']]}, {'version': 'C', 'item_ids': [items[2]['id']]}],
                                          'message': 'The key and this line disagree.'}],
                    'consistency_issues': [{'versions': ['A', 'C'], 'message': 'C pays the setup differently.'}]}
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B', 'C']):
            self.upload(exam_version='C')
            confirm_for_review(self.frq, self.plan['id'])
            waiting = self.frq.rubric_quality(self.plan['id'])
            ids = {q['exam_version']: q['question_id'] for q in waiting['context']['questions']}
            self.assertEqual((waiting['state'], waiting['to_check']), ('needed', [ids['C']]))
            self.assertEqual(sorted(waiting['standing']), sorted([ids['A'], ids['B']]))
            status = self.queue(answer=answer)
            self.assertEqual([prompt for _, prompt, _ in self.sent], ['rubric_quality_group', 'rubric_quality_added'])
            result = status['check']['result']
            self.assertEqual(result['settled'], sorted([ids['A'], ids['B']]))
            entries = {row['question_id']: row for row in result['questions']}
            # The settled versions keep exactly their confirmed entries; the echoed "Changed!" is never their guidance.
            self.assertEqual({qid: entries[qid] for qid in confirmed}, confirmed)
            self.assertEqual(list(entries[ids['C']]['clarifications'].values()), ['As on A and B.'])
            self.assertEqual(sorted(issue['question_id'] for issue in result['suspected_errors']), sorted([ids['A'], ids['C']]))
            self.assertEqual(result['consistency_issues'][0]['question_ids'], [ids['A'], ids['C']])
            self.assertEqual(result['families'], [[ids['A'], ids['B'], ids['C']]])
            self.assertTrue(result['summary'].startswith('Only the rubrics without a standing confirmation were checked (Version C): 1 problem'), result['summary'])
            self.assertIn('The other 2 questions keep their confirmed clarifications and exclusions', result['summary'])
            # `settled` is no part of what a confirmation covers: the input still reproduces its fingerprint without it.
            with connect(self.path) as db:
                stored = json.loads(db.execute('SELECT input_json FROM frq_rubric_quality_checks WHERE id=?', (status['check']['id'],)).fetchone()[0])
                self.assertEqual(sorted(stored['settled']['questions']), sorted([ids['A'], ids['B']]))
                for key in ('scheme', 'known_links', 'settled'):
                    stored.pop(key, None)
                stored['questions'] = [{k: v for k, v in q.items() if k not in ('reference', 'origins')} for q in stored['questions']]
                self.assertEqual(digest(stored), status['input_sha256'])
            done = self.frq.confirm_rubric_quality(self.plan['id'], {'version': status['version'], 'check_id': status['check']['id'],
                'input_sha256': status['input_sha256'], 'confirm_review': True, 'questions': copy.deepcopy(result['questions'])})
            self.assertEqual(done['state'], 'confirmed')
            self.assertEqual((done['confirmation']['kept'], done['confirmation']['changed'], done['confirmation']['suspended']), (2, 1, 0))
            self.assertEqual(len(done['review']['policy']['consistency_issues']), 1)

    def test_resolving_concerns_of_an_added_version_never_changes_the_settled_ones(self):
        """A SETTLED VERSION STAYS SETTLED (requirement, 2026-10-15: resolving the concerns of Quiz 2's makeup Version E rewrote
        Versions A-D's clarifications, and finalizing sent 612 of their graded answers back to review). The resolution answers for
        every version of the problem, but only the asked version's guidance changes; a concern naming only settled versions stays
        listed, unresolved, and confirming keeps their guidance and grades."""
        before = self.confirmed_versions()
        confirmed = {row['question_id']: row for row in before['review']['policy']['questions']}

        def answer(prompt, payload):
            if prompt not in ('rubric_quality_added', 'rubric_quality_resolve'):
                return self.answer(prompt, payload)
            versions = payload['problem']['versions']
            items = versions[0]['question']['rubric']['items']
            rows = [{'version': v['version'], 'summary': '', 'items': [{'item_id': item['id'], 'clarification': f"Rewritten on {v['version']}.",
                     'excludes': [], 'reason': ''} for item in items]} for v in versions]
            if prompt == 'rubric_quality_added':
                return {'summary': '', 'versions': rows, 'parallel_lines': [], 'consistency_issues': [],
                        'suspected_errors': [{'versions': [name], 'item_ids': [{'version': name, 'item_ids': [items[2]['id']]}],
                                              'message': f'Line 3 of {name} is unclear.'} for name in ('A', 'C')]}
            return {'summary': '', 'versions': rows, 'parallel_lines': [], 'consistency_issues': [], 'suspected_errors': [],
                    'concerns': [{'concern': c['concern'], 'resolved': True, 'how': 'Clarified.'} for c in payload['concerns']]}
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B', 'C']):
            self.upload(exam_version='C')
            confirm_for_review(self.frq, self.plan['id'])
            checked = self.queue(answer=answer)
            ids = {q['exam_version']: q['question_id'] for q in checked['context']['questions']}
            listed = [issue['question_id'] for issue in checked['check']['result']['suspected_errors']]
            self.assertEqual(sorted(listed), sorted([ids['A'], ids['C']]))
            with patch.object(self.frq, 'cloud_call', side_effect=lambda owner, role, endpoint, request, settings: {
                    'metadata': {}, 'raw': {}, 'output': answer(request['prompt'], request['payload'])}), \
                    patch.object(self.frq, 'rubric_problem_images', return_value=[]):
                self.frq.resolve_rubric_concerns(self.plan['id'], {'check_id': checked['check']['id'], 'input_sha256': checked['input_sha256'],
                    'request_id': 'resolve-added', 'questions': copy.deepcopy(checked['check']['result']['questions']),
                    'responses': [{'concern': n, 'response': 'Accept it.'} for n in range(len(listed))]})
                self.frq.process_cloud()
            status = self.frq.rubric_quality(self.plan['id'])
            result = status['check']['result']
            entries = {row['question_id']: row for row in result['questions']}
            self.assertEqual({qid: (entries[qid]['pairs'], entries[qid]['clarifications']) for qid in confirmed},
                             {qid: (row['pairs'], row['clarifications']) for qid, row in confirmed.items()})
            self.assertEqual(set(entries[ids['C']]['clarifications'].values()), {'Rewritten on C.'})
            self.assertEqual(sorted(result['resolution']['before']), [ids['C']])
            self.assertEqual([row['question_id'] for row in result['resolution']['resolved']], [ids['C']])
            self.assertEqual([issue['question_id'] for issue in result['suspected_errors']], [ids['A']])
            self.assertIn('Kept as confirmed', result['suspected_errors'][0]['note'])
            done = self.confirm(status)
            self.assertEqual((done['confirmation']['kept'], done['confirmation']['changed'], done['confirmation']['suspended']), (2, 1, 0))

    def test_nothing_changed_is_a_full_check_and_none_standing_is_too(self):
        self.confirmed_versions()
        self.sent.clear()
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            again = self.queue()
        self.assertEqual([prompt for _, prompt, _ in self.sent], ['rubric_quality_group', 'rubric_quality_versions'])
        self.assertTrue(all('settled' not in v for v in self.sent[1][2]['problem']['versions']))
        self.assertNotIn('settled', again['check']['result'])
        self.assertTrue(again['check']['result']['summary'].startswith('1 problem checked, each with all its versions side by side'), again['check']['result']['summary'])

    def test_a_plan_without_versions_asks_only_its_new_question(self):
        self.upload('1_Question_18.csv', encode(export_rows(question=1)).encode())
        self.upload('2_Question_19.csv', encode(export_rows(question=2)).encode())
        first = self.queue()
        self.assertEqual([prompt for _, prompt, _ in self.sent], ['rubric_quality', 'rubric_quality'])
        self.confirm(first)
        self.sent.clear()
        self.upload('3_Question_20.csv', encode(export_rows(question=3)).encode())
        confirm_for_review(self.frq, self.plan['id'])
        waiting = self.frq.rubric_quality(self.plan['id'])
        new = next(q['question_id'] for q in waiting['context']['questions'] if q['question_id'] not in waiting['standing'])
        self.assertEqual(waiting['to_check'], [new])
        status = self.queue()
        self.assertEqual([step for step, _, _ in self.sent], ['rubric_quality:' + new])
        result = status['check']['result']
        self.assertEqual((result['mode'], len(result['questions']), len(result['settled'])), ('per_question', 3, 2))
        self.assertIn('The other 2 questions keep their confirmed clarifications', result['summary'])
        self.assertEqual(self.confirm(status)['confirmation']['kept'], 2)

    def points_halved(self, qid):
        """One line's points halved on the question `qid`, as the rubric editor saves it: a new revision of that rubric alone."""
        q = next(q for q in self.frq.assignment(self.plan['id'])['questions'] if q['question_id'] == qid)
        item = q['rubric']['rules']['items'][1]
        self.frq.edit_rubric(self.plan['id'], qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                                    'lines': [{'item_id': item['id'], 'points': item['points'] / 2}]})

    def test_a_points_change_after_a_check_asks_only_that_rubric_and_keeps_the_others(self):
        """A CHECK ASKS ONLY WHAT CHANGED (requirement, 2026-10-25: on Exam 2, changing one line's points, the one concern left after
        two rounds of Resolve concerns, asked for a check of all 24 rubrics again). Nothing is confirmed yet: the unchanged questions
        keep the latest check's entries and its concerns still open on them; a fix it made is not listed again."""
        def answer(prompt, payload):
            found = self.answer(prompt, payload)
            items, title = payload['question']['rubric']['items'], payload['question']['title']
            found['suspected_errors'] = [{'item_ids': [items[2]['id']], 'message': f'Line 3 of {title} is unclear.',
                                          **({'fix': {'kind': 'guidance', 'note': 'Clarified.'}} if '19' in title else {})}]
            return found
        for n, number in enumerate((18, 19, 20), 1):
            self.upload(f'{n}_Question_{number}.csv', encode(export_rows(question=n)).encode())
        first = self.queue(answer=answer)
        ids = {q['title'][-2:]: q['question_id'] for q in first['context']['questions']}
        earlier = {row['question_id']: row for row in first['check']['result']['questions']}
        self.assertNotIn('to_check', first)
        self.points_halved(ids['20'])
        confirm_for_review(self.frq, self.plan['id'])
        waiting = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual((waiting['state'], waiting['to_check']), ('needed', [ids['20']]))
        self.assertEqual(waiting['carried'], {'check_id': first['check']['id'], 'questions': sorted([ids['18'], ids['19']])})
        self.assertEqual(waiting['standing'], {})  # carrying confirms nothing
        self.sent.clear()
        status = self.queue(answer=answer)
        self.assertEqual([step for step, _, _ in self.sent], ['rubric_quality:' + ids['20']])
        result = status['check']['result']
        self.assertEqual((result['settled'], result['carried']), (sorted([ids['18'], ids['19']]),) * 2)
        entries = {row['question_id']: row for row in result['questions']}
        self.assertEqual({qid: entries[qid] for qid in (ids['18'], ids['19'])}, {qid: earlier[qid] for qid in (ids['18'], ids['19'])})
        self.assertEqual([issue['question_id'] for issue in result['suspected_errors']], [ids['18'], ids['20']])
        self.assertNotIn('fix', result['suspected_errors'][0])
        self.assertTrue(result['summary'].startswith('Only the rubrics that changed since the last check were checked'), result['summary'])
        self.assertIn('The other 2 questions keep their clarifications and exclusions from the last check', result['summary'])
        done = self.confirm(status)
        self.assertEqual((done['state'], done['confirmation']['kept'], done['confirmation']['changed']), ('confirmed', 0, 3))
        # Once confirmed, a question stands under the confirmation: the next edit asks only it, beside the confirmed ones.
        self.points_halved(ids['18'])
        confirm_for_review(self.frq, self.plan['id'])
        after = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual((after['to_check'], sorted(after['standing'])), ([ids['18']], sorted([ids['19'], ids['20']])))
        self.assertNotIn('carried', after)

    def test_a_points_change_on_one_version_asks_it_beside_the_others_as_they_were_checked(self):
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            self.upload(exam_version='A')
            self.upload(exam_version='B')
            first = self.queue()
            ids = {q['exam_version']: q['question_id'] for q in first['context']['questions']}
            earlier = next(row for row in first['check']['result']['questions'] if row['question_id'] == ids['A'])
            self.points_halved(ids['B'])
            confirm_for_review(self.frq, self.plan['id'])
            waiting = self.frq.rubric_quality(self.plan['id'])
            self.assertEqual((waiting['to_check'], waiting['carried']['questions']), ([ids['B']], [ids['A']]))
            self.sent.clear()
            status = self.queue()
            self.assertEqual([prompt for _, prompt, _ in self.sent], ['rubric_quality_group', 'rubric_quality_added'])
            versions = self.sent[1][2]['problem']['versions']
            self.assertEqual([(v['version'], v.get('settled', False)) for v in versions], [('A', True), ('B', False)])
            result = status['check']['result']
            self.assertEqual(result['carried'], [ids['A']])
            self.assertEqual(next(row for row in result['questions'] if row['question_id'] == ids['A']), earlier)
            self.assertIn('its other versions as the reference', result['summary'])
            self.assertEqual(self.confirm(status)['state'], 'confirmed')

    def test_a_cancelled_re_run_leaves_the_finished_check_of_the_same_rubrics(self):
        from workspace_overview import setup_waiting
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B']):
            self.upload(exam_version='A')
            self.upload(exam_version='B')
            finished = self.queue()
            self.queue(request_id='second')  # queued, never run
            self.frq.cancel_rubric_quality(self.plan['id'], {})
            status = self.frq.rubric_quality(self.plan['id'])
            self.assertEqual((status['state'], status['check']['id']), ('needs_review', finished['check']['id']))
            with connect(self.path) as db:
                second = db.execute("SELECT id FROM frq_rubric_quality_checks WHERE request_id='second'").fetchone()[0]
                self.assertEqual(setup_waiting(db, [self.plan['id']], self.frq)[0], {self.plan['id']: True})
            self.assertEqual(status['rerun_failed'], {'check_id': second, 'cancelled': True, 'error': status['rerun_failed']['error']})
            self.assertEqual(self.confirm(status)['state'], 'confirmed')
        # Once the rubrics change, a cancelled check of the new ones leaves nothing to fall back on.
        with patch.object(self.frq, 'chart_versions', return_value=['A', 'B', 'C']):
            self.upload(exam_version='C')
            self.queue(request_id='third')
            self.frq.cancel_rubric_quality(self.plan['id'], {})
            status = self.frq.rubric_quality(self.plan['id'])
            self.assertEqual(status['state'], 'needed')
            self.assertNotIn('rerun_failed', status)
            with connect(self.path) as db:
                self.assertEqual(setup_waiting(db, [self.plan['id']], self.frq)[0], {})


class CommentsElsewhere(unittest.TestCase):
    """The instructor's line comments on other problems reach each review as their policy (2026-09-28), never the problem's own."""
    @staticmethod
    def question(number, version, comments=None):
        rubric = {'mode': 'negative', 'items': [{'id': 'R1', 'description': f'Problem {number}: no algebraic work shown', 'points': -0.5},
                                                {'id': 'R2', 'description': 'No answer or completely incorrect', 'points': -2}]}
        return {'question_id': f'q{number}{version}', 'rubric_id': f'r{number}{version}', 'exam_version': version, 'title': f'Question {number}',
                'printed_number': str(number), 'max_points': 2, 'rubric': rubric, 'builtin': builtin_exclusions(rubric, 2),
                **({'comments': comments} if comments else {})}

    def test_other_problems_comments_come_along_once_each_with_their_line(self):
        from grading.rubric_quality import comments_elsewhere, problem_payload
        rref = 'A calculator rref counts only when the student shows it was used.'
        context = {'rules': 'rubric-builtin-v1', 'questions': [
            self.question(3, 'A', {'R1': rref}), self.question(3, 'B', {'R1': rref}), self.question(4, 'A'), self.question(4, 'B', {'R2': 'Blank only.'})]}
        four = [q for q in context['questions'] if q['printed_number'] == '4']
        payload = problem_payload(context, four, [0, 0])
        self.assertEqual(payload['instructor_comments_elsewhere'],
                         [{'problem': 'Question 3', 'version': 'A', 'line': 'Problem 3: no algebraic work shown', 'comment': rref}])
        # The problem's own comments stay on its own lines, where the reviewer must carry them in full.
        self.assertEqual(payload['problem']['versions'][1]['question']['instructor_comments'], {'R2': 'Blank only.'})
        self.assertEqual([c['comment'] for c in comments_elsewhere(context, [context['questions'][0], context['questions'][1]])], ['Blank only.'])
        single = question_payload(context, context['questions'][2])
        self.assertEqual([c['comment'] for c in single['instructor_comments_elsewhere']], [rref, 'Blank only.'])

    def test_without_comments_elsewhere_the_payload_is_unchanged(self):
        from grading.rubric_quality import problem_payload
        context = {'rules': 'rubric-builtin-v1', 'questions': [self.question(3, 'A'), self.question(4, 'A', {'R1': 'Only its own.'})]}
        self.assertNotIn('instructor_comments_elsewhere', question_payload(context, context['questions'][1]))
        self.assertNotIn('instructor_comments_elsewhere', problem_payload(context, context['questions'][1:], [0]))
        self.assertNotIn('instructor_comments_elsewhere', problem_payload({'rules': 'rubric-builtin-v1'}, context['questions'][:1], [0]))


class CheckHosts(unittest.TestCase):
    """WHERE A CHECK RUNS (requirement, 2026-10-11): the prompt, the
    payload and the validation are the check's own, whoever runs it. Only who owns the reviewer's requests, where each printed
    problem is read and what the reviewer may call come from its host. The plan's host is exactly what the check always did (every
    other test here runs through it, unchanged); a quiz revision supplies its own before there is a plan
    (`quiz_drafts.DraftCheckHost`, `test_quiz_drafts` `RubricQuestionsAtDraftReview`)."""
    def test_the_plans_host_is_the_check_as_it_was_and_another_host_changes_only_owner_pictures_and_tools(self):
        from grading.rubric_quality import BUILTIN, CheckHost, RubricQualityMixin
        rubric = {'mode': 'positive', 'items': [{'id': 'R1', 'description': 'Correct answer with supporting work: $$x=2$$', 'points': 4},
                                                {'id': 'R2', 'description': 'No answer or completely incorrect', 'points': 0}]}
        family = [{'question_id': f'q{v}', 'rubric_id': f'r{v}', 'exam_version': v, 'title': 'Question 1', 'printed_number': '1', 'max_points': 4,
                   'rubric': rubric, 'builtin': builtin_exclusions(rubric, 4)} for v in 'AB']
        asked = []

        def call(owner, role, endpoint, request, settings):
            asked.append((owner, role, request))
            return {'metadata': {}, 'raw': {}, 'output': {'summary': '', 'suspected_errors': [], 'consistency_issues': [], 'versions': [
                {'version': v, 'summary': '', 'items': [{'item_id': i['id'], 'clarification': '', 'excludes': [], 'reason': ''} for i in rubric['items']]} for v in 'AB']}}
        reviewer = RubricQualityMixin()
        reviewer.cloud_call = call
        reviewer.rubric_problem_images = lambda question: [b'template:' + question['question_id'].encode()]
        profile = {'endpoint': {'modality': 'vision'}, 'settings': {}}
        reviewer.review_problem({'id': 'check'}, profile, {'rules': BUILTIN}, family)
        owner, role, request = asked[0]
        # The plan's check, byte for byte: each call owned by the check under its step, the problems read from the blank templates.
        self.assertEqual((owner, list(owner)), ({'rubric_check_id': 'check', 'step': 'rubric_quality:problem:qA', 'task_attempt': 0}, ['rubric_check_id', 'step', 'task_attempt']))
        self.assertEqual((role, request['prompt'], request['images'], 'tools' in request), ('rubric_reviewer', 'rubric_quality_versions', [b'template:qA', b'template:qB'], False))

        class Revision(CheckHost):
            def owner(self, task, step, attempt):
                return {'quiz_revision_id': task['id'], 'step': step, 'task_attempt': attempt}

            def images(self, question):
                return [b'page:' + question['question_id'].encode()]

            def tools(self, task, endpoint, questions):
                return ['context of', *(q['question_id'] for q in questions)]
        found = reviewer.review_problem({'id': 'revision'}, profile, {'rules': BUILTIN}, family, Revision(reviewer))
        owner, role, hosted = asked[1]
        self.assertEqual(owner, {'quiz_revision_id': 'revision', 'step': 'rubric_quality:problem:qA', 'task_attempt': 0})
        self.assertEqual((hosted['images'], hosted['tools']), ([b'page:qA', b'page:qB'], ['context of', 'qA', 'qB']))
        # The reviewer is asked the very same thing, and its answer is read the very same way.
        self.assertEqual((role, hosted['prompt'], hosted['payload']), ('rubric_reviewer', request['prompt'], request['payload']))
        self.assertEqual((sorted(found['answers']), found['images']), (['qA', 'qB'], {'qA': 1, 'qB': 1}))
        # One question alone, and the dispatcher a revision calls, take the host the same way.
        single = {'summary': '', 'suspected_errors': [], 'items': [{'item_id': i['id'], 'clarification': '', 'excludes': [], 'reason': ''} for i in rubric['items']]}
        reviewer.cloud_call = lambda owner, role, endpoint, request, settings: asked.append((owner, role, request)) or {'metadata': {}, 'raw': {}, 'output': single}
        status, result, metadata, error, after = reviewer.run_rubric_check({'id': 'revision'}, profile, {'rules': BUILTIN, 'questions': family[:1]}, Revision(reviewer))
        self.assertEqual((status, error, result['mode'], asked[2][0]), ('complete', '', 'per_question', {'quiz_revision_id': 'revision', 'step': 'rubric_quality:qA', 'task_attempt': 0}))
        self.assertEqual((asked[2][2]['prompt'], asked[2][2]['images'], metadata['problem_images']), ('rubric_quality', [b'page:qA'], {'qA': 1}))

