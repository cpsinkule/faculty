"""Rubric lines prepared before the rubric reviewer (requirement, 2026-09-24).

Wording and points the instructor edits are a new immutable revision that graders read and scoring uses. Comments are guidance for
the reviewer, which works each into that line's clarification. Nothing reaches the reviewer before the instructor confirms the exact
revision and comments. Synthetic rubric data and loopback models."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from grading.rubric_quality import question_payload, validate_question
from grading.providers.local_vlm import VERSIONS
from rosters import connect
from fake_openai import payload_of, prompt_name
from frq_fixtures import confirm_for_review
from test_grading_rubric_quality import QualityCase, CLEAR, suggestion


COMMENT = 'Accept x = 6 written as a fraction, 18/3, as long as the division is shown.'


class RubricEdits(QualityCase):
    def question(self):
        return self.frq.assignment(self.plan['id'])['questions'][0]

    def edit(self, q=None, **data):
        q = q or self.question()
        return self.frq.edit_rubric(self.plan['id'], self.qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'], **data})

    def test_wording_and_points_are_a_new_revision_the_graders_read(self):
        before = self.question()
        result = self.edit(before, lines=[{'item_id': 'R2', 'description': 'Set up $$3x - 8 = 10$$ correctly', 'points': 5},
                                          {'item_id': 'R3', 'points': 2}])  # Unchanged points are not a change.
        after = result['questions'][0]
        self.assertEqual(result['edit'], {'rubric_id': after['rubric_id'], 'lines_changed': 1, 'comments_changed': 0})
        self.assertNotEqual(after['rubric_id'], before['rubric_id'])
        line = lambda q, iid: next(i for i in q['rubric']['rules']['items'] if i['id'] == iid)
        self.assertEqual((line(after, 'R2')['description'], line(after, 'R2')['points']), ('Set up $$3x - 8 = 10$$ correctly', 5))
        self.assertEqual(line(after, 'R2')['original_source_text'], line(before, 'R2')['original_source_text'])  # What the coordinator wrote stays.
        self.assertEqual([i['id'] for i in after['rubric']['rules']['items']], [i['id'] for i in before['rubric']['rules']['items']])
        compiler = after['rubric']['compiler']
        self.assertEqual((compiler['name'], compiler['edited_from'], compiler['changes']), ('instructor-edited', before['rubric_id'],
            [{'item_id': 'R2', 'previous_description': line(before, 'R2')['description'], 'description': 'Set up $$3x - 8 = 10$$ correctly', 'previous_points': 4, 'points': 5}]))
        self.assertEqual(after['rubric']['source_text'], before['rubric']['source_text'])
        with connect(self.path) as db:
            self.assertEqual(json.loads(db.execute('SELECT rules_json FROM frq_rubric_revisions WHERE id=?', (before['rubric_id'],)).fetchone()[0])['items'][1]['points'], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='rubric_lines_edited'").fetchone()[0], 1)
        # The graders see the edited rubric and the score is the edited points'.
        self.check()
        confirmed = self.confirm(self.frq.rubric_quality(self.plan['id']))
        self.assertEqual(confirmed['review']['policy']['questions'][0]['rubric_id'], after['rubric_id'])
        response, _ = self.grade(['3x - 8 = 10'], primary=['R2'])
        self.assertEqual(response['result']['score']['score'], 5)
        graded = [payload_of(r['body']) for r in self.host.requests if prompt_name(r['body']) in ('grade', 'primary_vision', 'second', 'second_vision')]
        self.assertTrue(graded)
        self.assertTrue(all('Set up $$3x - 8 = 10$$ correctly' in json.dumps(p['rubric']) for p in graded))

    def test_edits_are_validated_and_stale_ones_refused(self):
        q = self.question()
        maximum = q['content']['max_points']
        for lines, comments in (([{'item_id': 'R9', 'points': 1}], {}), ([{'item_id': 'R2', 'points': 1}, {'item_id': 'R2', 'points': 2}], {}),
                                ([{'item_id': 'R2', 'description': '   '}], {}), ([{'item_id': 'R2', 'points': maximum + 1}], {}),
                                ([], {'R9': 'No such line.'}), ([], {}), ([{'item_id': 'R2', 'points': 4}], {})):
            with self.assertRaises(ValueError, msg=(lines, comments)):
                self.edit(q, lines=lines, comments=comments)
        self.edit(q, lines=[{'item_id': 'R2', 'points': 3}])
        with self.assertRaises(Conflict):  # The page still showed the earlier revision.
            self.edit(q, comments={'R2': COMMENT})
        self.assertEqual(self.frq.rubric_history(self.plan['id'], self.qid)[0]['rules']['items'][1]['points'], 3)

    def test_comments_are_append_only_the_latest_counts_and_empty_clears(self):
        aid = self.plan['id']
        before = self.frq.rubric_quality(aid)['input_sha256']
        result = self.edit(comments={'R1': COMMENT, 'R2': ''})  # An empty comment where there is none is no change.
        self.assertEqual(result['edit'], {'rubric_id': self.question()['rubric_id'], 'lines_changed': 0, 'comments_changed': 1})
        self.assertEqual(result['questions'][0]['rubric_id'], self.question()['rubric_id'])  # A comment is not a rubric revision.
        status = self.frq.rubric_quality(aid)
        self.assertEqual(status['context']['questions'][0]['comments'], {'R1': COMMENT})
        self.assertNotEqual(status['input_sha256'], before)  # What the reviewer reads changed.
        self.edit(comments={'R1': 'Fractions are fine.'})
        self.assertEqual(self.frq.rubric_quality(aid)['context']['questions'][0]['comments'], {'R1': 'Fractions are fine.'})
        self.edit(comments={'R1': ''})
        status = self.frq.rubric_quality(aid)
        self.assertNotIn('comments', status['context']['questions'][0])
        self.assertEqual(status['input_sha256'], before)  # No comments: exactly the fingerprint a plan without this feature has.
        confirm_for_review(self.frq, aid)
        with connect(self.path) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT comment FROM frq_rubric_line_comments ORDER BY rowid')], [COMMENT, 'Fractions are fine.', ''])
            for sql in ("UPDATE frq_rubric_line_comments SET comment='x'", 'DELETE FROM frq_rubric_line_comments',
                        "UPDATE frq_rubric_review_ready SET actor='x'", 'DELETE FROM frq_rubric_review_ready'):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    db.execute(sql)

    def test_the_reviewer_waits_for_the_instructors_confirmation_of_the_exact_rubric_and_comments(self):
        aid = self.plan['id']
        with connect(self.path) as db:  # The shared fixture finalized the rubric it started with.
            confirmations = db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='rubric_review_confirmed'").fetchone()[0]
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['unconfirmed'], status['readiness'][self.qid]['ready']), (['Question 1'], False))
        with self.assertRaisesRegex(Conflict, 'Confirm each rubric for review'):
            self.frq.queue_rubric_quality(aid, {'request_id': 'r1', 'input_sha256': status['input_sha256']})
        self.edit(comments={'R1': COMMENT})
        prep = self.frq.rubric_quality(aid)['readiness'][self.qid]
        with self.assertRaises(Conflict):  # A confirmation names what the page showed: here, the rubric before the comment.
            self.frq.confirm_rubric_review(aid, {'questions': {self.qid: {'rubric_id': prep['rubric_id'], 'comments_sha256': status['readiness'][self.qid]['comments_sha256']}}})
        with self.assertRaises(ValueError):
            self.frq.confirm_rubric_review(aid, {'questions': {'unknown': {'rubric_id': prep['rubric_id'], 'comments_sha256': prep['comments_sha256']}}})
        self.frq.confirm_rubric_review(aid, {'questions': {self.qid: {'rubric_id': prep['rubric_id'], 'comments_sha256': prep['comments_sha256']}}})
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['unconfirmed'], status['readiness'][self.qid]['ready'], status['readiness'][self.qid]['by_finalization']), ([], True, False))
        self.assertTrue(status['readiness'][self.qid]['confirmed_at'])
        # Another comment, or an edited line, needs the confirmation again.
        self.edit(comments={'R2': 'Either order of the terms.'})
        self.assertEqual(self.frq.rubric_quality(aid)['unconfirmed'], ['Question 1'])
        confirm_for_review(self.frq, aid)
        self.edit(lines=[{'item_id': 'R3', 'points': 1}])
        self.assertEqual(self.frq.rubric_quality(aid)['unconfirmed'], ['Question 1'])
        confirm_for_review(self.frq, aid)
        self.assertEqual(self.frq.rubric_quality(aid)['unconfirmed'], [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='rubric_review_confirmed'").fetchone()[0] - confirmations, 3)
            with self.assertRaises(sqlite3.IntegrityError):  # A confirmation names the pinned revision, nothing else.
                db.execute("INSERT INTO frq_rubric_review_ready VALUES('x',?,?,?,'y','workspace','now')", (aid, self.qid, 'another-revision'))
        # The page's progress says the same.
        plan = self.frq.assignment(aid)
        self.assertTrue(plan['rubric_quality']['readiness'][self.qid]['ready'])

    def test_a_current_confirmed_finalization_counts_as_confirmed_for_review(self):
        aid = self.plan['id']
        self.confirm(self.check())
        with connect(self.path) as db:  # A finalization from before this confirmation existed.
            db.execute('DROP TRIGGER frq_rubric_review_ready_retained')
            db.execute('DELETE FROM frq_rubric_review_ready')
        prep = self.frq.rubric_quality(aid)['readiness'][self.qid]
        self.assertEqual((prep['ready'], prep['by_finalization']), (True, True))
        # A step-three correction carries the finalization, comments and all: nothing to confirm again on the first step.
        self.edit(comments={'R2': COMMENT})
        confirm_for_review(self.frq, aid)
        self.confirm(self.check())
        q = self.question()
        self.frq.correct_rubric_line(aid, self.qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
            'item_id': 'R1', 'description': 'Correct answer $$x=6$$ with work.'})
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['state'], status['unconfirmed'], status['readiness'][self.qid]['by_finalization']), ('confirmed', [], True))
        self.assertEqual(status['context']['questions'][0]['comments'], {'R2': COMMENT})

    def test_a_revision_from_edit_interpretation_suspends_acceptance_like_the_line_editor(self):
        aid = self.plan['id']
        self.confirm(self.check())

        def reinterpret():
            q = self.question()
            rules = json.loads(json.dumps(q['rubric']['rules']))
            item = rules['items'][1]
            item['follow_through'] = not item.get('follow_through')
            item['repeat_penalty_for_same_originating_error'] = not item['follow_through']
            self.frq.import_rubric(aid, self.qid, {'version': q['version'], 'source_text': q['rubric']['source_text'],
                                                   'source_artifact_id': q['rubric']['source_artifact_id'], 'rules': rules})

        with patch.object(self.frq, 'suspend_acceptance', wraps=self.frq.suspend_acceptance) as suspend:
            reinterpret()  # Replaces the rubric the confirmed finalization covered.
            self.assertEqual(suspend.call_count, 1)
            reinterpret()  # That finalization no longer covers the plan: nothing more to suspend.
            self.assertEqual(suspend.call_count, 1)
        self.assertFalse(self.frq.rubric_quality(aid)['readiness'][self.qid]['ready'])

    def test_comments_reach_the_reviewer_as_guidance_and_become_clarifications(self):
        self.edit(comments={'R1': COMMENT, 'R3': 'Only a bare answer; any line of work is R2.'})
        checked = self.check()
        sent = payload_of(self.host.requests[-1]['body'])
        self.assertEqual(sent['question']['instructor_comments'], {'R1': COMMENT, 'R3': 'Only a bare answer; any line of work is R2.'})
        self.assertIn('instructor_comments', self.host.requests[-1]['body']['messages'][0]['content'])  # The prompt names them as guidance.
        self.assertEqual(VERSIONS['rubric_quality'], 13)
        found = checked['check']['result']['questions'][0]
        # The scripted reviewer clarified R2 only: the commented lines it left empty carry the comment itself, and say so.
        self.assertEqual(found['clarifications'], {'R1': COMMENT, 'R2': CLEAR, 'R3': 'Only a bare answer; any line of work is R2.'})
        self.assertEqual(found['comment_fallback'], ['R1', 'R3'])
        confirmed = self.confirm(checked)
        self.assertEqual(confirmed['review']['policy']['questions'][0]['clarifications']['R1'], COMMENT)
        # The reviewer's own wording wins where it wrote one.
        self.host.script = lambda body, host: {**suggestion(payload_of(body)), 'items': [
            {'item_id': i['id'], 'clarification': 'Credit 18/3 for x = 6 when the division is shown.' if i['id'] == 'R1' else '', 'excludes': [], 'reason': ''}
            for i in payload_of(body)['question']['rubric']['items']]}
        self.edit(comments={'R3': ''})
        found = self.check()['check']['result']['questions'][0]
        self.assertEqual((found['clarifications'], found.get('comment_fallback')), ({'R1': 'Credit 18/3 for x = 6 when the division is shown.'}, None))

    def test_payload_and_validation_without_comments_are_unchanged(self):
        status = self.frq.rubric_quality(self.plan['id'])
        question = status['context']['questions'][0]
        self.assertNotIn('comments', question)
        payload = question_payload(status['context'], question, 0)
        self.assertNotIn('instructor_comments', payload['question'])
        entry, _ = validate_question(suggestion(payload), question)
        self.assertNotIn('comment_fallback', entry)


if __name__ == '__main__':
    unittest.main()
