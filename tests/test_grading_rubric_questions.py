"""Rubric questions the instructor's own sources answer (requirement, 2026-10-29).

Quiz 6's draft showed six questions, three of them key fixes the reviewer had already made; Exam 2's check asked one points change four
times and the same policy on several problems. Synthetic plans and a scripted reviewer; nothing reaches a real model."""
import json
import unittest
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict

from grading import rubric_choices
from grading.models import encoded
from grading.providers.local_vlm import VERSIONS
from grading.quiz_drafts import answered_comments, check_counts, decisions
from grading.rubric_quality import decision_fields, edit_fields, validate_problem, validate_question
from rosters import connect
from fake_openai import payload_of, prompt_name
from test_grading_rubric_quality import QualityCase, suggestion

PROMPTS = Path(__file__).resolve().parent.parent / "grading" / "prompts"
CONTRACT = 'topic:string,edit:{lines:[{version:string,item_id:string,points:number}]}'


def paragraphs(text):
    return [p.strip() for p in text.split('\n\n') if p.strip()]


class Prompts(unittest.TestCase):
    def test_the_reviewers_add_three_sections_and_two_fields_to_their_answer(self):
        for name, old, new in (('rubric_quality', 12, 13), ('rubric_quality_versions', 8, 9), ('rubric_quality_added', 5, 6), ('rubric_quality_resolve', 10, 11)):
            with self.subTest(prompt=name):
                self.assertEqual(VERSIONS[name], new)
                before, after = ((PROMPTS / f'{name}-v{n}.txt').read_text() for n in (old, new))
                fresh = [p for p in paragraphs(after.replace(',' + CONTRACT, '')) if p not in paragraphs(before)]
                self.assertEqual([p.split('.')[0].split(' (')[0] for p in fresh],
                                 ["SETTLED BY THE INSTRUCTOR'S OWN SOURCES", 'ONE QUESTION ONCE', 'AN EDIT ONLY THE INSTRUCTOR CAN MAKE'])
                self.assertEqual(after.count(CONTRACT), before.count('choices:[{label:string,feedback:string,consequence:string}]'))
                self.assertEqual(after.split('\n')[0], before.split('\n')[0])  # the first sentence names the prompt to the fake host

    def test_the_writers_say_with_supporting_work_only_where_a_line_charges_it(self):
        for name, old, new in (('rubric_write', 9, 10), ('rubric_write_versions', 8, 9), ('quiz_rubric', 9, 10)):
            with self.subTest(prompt=name):
                self.assertEqual(VERSIONS[name], new)
                before, after = ((PROMPTS / f'{name}-v{n}.txt').read_text() for n in (old, new))
                said = '"Correct answer with supporting work: $$...$$"'
                fresh = [p for p in paragraphs(after.replace(said + ' (or "Correct answer: $$...$$", below)', said)) if p not in paragraphs(before)]
                self.assertEqual(len(fresh), 1)
                self.assertTrue(fresh[0].startswith('THE WORDS MATCH THE LINES, AND PARTS ARE GRADED ALIKE'))
                self.assertEqual(after.count('(or "Correct answer: $$...$$", below)'), 2)


class Fields(unittest.TestCase):
    rubric = {'items': [{'id': 'R1', 'points': 2}, {'id': 'R2', 'points': 1}]}
    a = {'question_id': 'qa', 'exam_version': 'A', 'rubric_id': 'ra', 'max_points': 2, 'builtin': [], 'rubric': rubric}
    b = {**a, 'question_id': 'qb', 'exam_version': 'B', 'rubric_id': 'rb'}

    def test_an_edit_keeps_only_real_lines_whose_points_it_changes(self):
        lines = [{'version': 'B', 'item_id': 'R2', 'points': 0.5}, {'version': 'A', 'item_id': 'R2', 'points': 1},  # unchanged: dropped
                 {'version': 'A', 'item_id': 'R9', 'points': 1}, {'version': 'C', 'item_id': 'R1', 'points': 1},  # no such line or version
                 {'version': 'B', 'item_id': 'R1', 'points': 7}, {'version': 'B', 'item_id': 'R2', 'points': 0.25}, {'version': 'A', 'item_id': 'R1', 'points': 'x'}]
        self.assertEqual(edit_fields({'edit': {'lines': lines}}, {'A': self.a, 'B': self.b}), {'edit': [{'question_id': 'qb', 'item_id': 'R2', 'points': 0.5}]})
        self.assertEqual(edit_fields({'edit': {'lines': []}}, {'A': self.a}), {})
        self.assertEqual(edit_fields({'edit': 'R2'}, {'A': self.a}), {})
        self.assertEqual(edit_fields({'edit': {'lines': [{'version': '', 'item_id': 'R1', 'points': 1.5}]}}, {'A': self.a}),
                         {'edit': [{'question_id': 'qa', 'item_id': 'R1', 'points': 1.5}]})  # a lone question needs no version

    def test_a_topic_is_kept_and_a_problems_edit_reaches_its_rows(self):
        self.assertEqual(decision_fields({'topic': ' rounded decimals '})['topic'], 'rounded decimals')
        issue = {'versions': ['A', 'B'], 'item_ids': [{'version': 'A', 'item_ids': ['R2']}, {'version': 'B', 'item_ids': ['R2']}], 'version_notes': [],
                 'message': 'B is worth 1 on R2; you asked for 0.5.', 'title': 'R2 points', 'decision': '', 'group': '', 'choices': [], 'topic': '',
                 'edit': {'lines': [{'version': 'B', 'item_id': 'R2', 'points': 0.5}]}, 'fix': {'kind': 'none', 'note': '', 'keys': []}}
        answer = {'summary': '', 'suspected_errors': [issue], 'consistency_issues': [{**issue, 'versions': ['A', 'B']}],
                  'versions': [{'version': v, 'summary': '', 'items': [{'item_id': i, 'clarification': '', 'excludes': [], 'reason': ''} for i in ('R1', 'R2')]} for v in 'AB']}
        found, consistency, _ = validate_problem(answer, [self.a, self.b])
        self.assertEqual(found['qa'][1][0]['edit'], [{'question_id': 'qb', 'item_id': 'R2', 'points': 0.5}])
        self.assertEqual(found['qb'][1][0]['edit'], found['qa'][1][0]['edit'])
        self.assertEqual(consistency[0]['edit'], found['qa'][1][0]['edit'])
        _, [lone] = validate_question({'summary': '', 'items': [{'item_id': i, 'clarification': '', 'excludes': [], 'reason': ''} for i in ('R1', 'R2')],
                                       'suspected_errors': [{'item_ids': ['R2'], 'message': 'm', 'edit': {'lines': [{'version': '', 'item_id': 'R2', 'points': 0.5}]}}]},
                                      {**self.a, 'exam_version': ''})
        self.assertEqual(lone['edit'], [{'question_id': 'qa', 'item_id': 'R2', 'points': 0.5}])


class DraftCounts(unittest.TestCase):
    def test_fixed_findings_are_no_decision_and_one_topic_is_one(self):
        rows = [{'question_id': 'draft:2:A', 'shared': 's', 'fix': {'kind': 'key'}}, {'question_id': 'draft:2:B', 'shared': 's', 'fix': {'kind': 'key'}},
                {'question_id': 'draft:3:A', 'fix': {'kind': 'guidance'}}, {'question_id': 'draft:3:A', 'topic': 'interval notation'},
                {'question_id': 'draft:4:A', 'topic': 'interval notation'}, {'question_id': 'draft:4:A', 'shared': 't'}, {'question_id': 'draft:4:B', 'shared': 't'}]
        self.assertEqual(decisions(rows), 2)
        counts = check_counts({'status': 'complete', 'result': {'suspected_errors': rows, 'questions': []}})
        self.assertEqual(counts, {'status': 'complete', 'decisions': 2, 'overflow': 0, 'clarifications': 0, 'exclusions': 0, 'answered': 0})

    def test_an_answered_line_takes_the_reviewers_new_clarification_as_its_drafted_comment(self):
        """Quiz 5 (2026-10-15/16): answered "charge it on both R6 and R7", the drafted R7 comment still said otherwise, and the check
        asked twice for the instructor to edit it."""
        line = lambda said: {'descriptions': {'A': 'Part B: value', 'B': 'Part B: value'}, 'clarifications': {'A': said, 'B': said}}
        rubrics = [{'problem': 2, 'rubric': {'items': [line('one'), line('Not charged with no work.'), line('')]}}]
        concerns = [{'kind': 'suspected_error', 'question_id': 'draft:2:A', 'item_ids': ['R2', 'R3'], 'response': 'Charge it on both.'},
                    {'kind': 'suspected_error', 'question_id': 'draft:2:B', 'item_ids': ['R1']}]  # no answer: never rewritten
        resolved = {'questions': [{'question_id': 'draft:2:A', 'clarifications': {'R1': 'one', 'R2': 'Charged on both lines.', 'R3': 'New guidance.'}},
                                  {'question_id': 'draft:2:B', 'clarifications': {'R1': 'changed', 'R2': 'Not charged with no work.'}}],
                    'resolution': {'before': {'draft:2:A': {'clarifications': {'R1': 'one', 'R2': 'Not charged with no work.'}},
                                              'draft:2:B': {'clarifications': {'R1': 'one', 'R2': 'Not charged with no work.'}}}}}
        after, rewritten = answered_comments(rubrics, concerns, resolved)
        self.assertEqual(rewritten, [{'problem': 2, 'version': 'A', 'item_id': 'R2'}])
        self.assertEqual(after[0]['rubric']['items'][1]['clarifications'], {'A': 'Charged on both lines.', 'B': 'Not charged with no work.'})
        self.assertEqual(after[0]['rubric']['items'][2]['clarifications'], {'A': '', 'B': ''})  # drafted without one: it gains none
        self.assertEqual(rubrics[0]['rubric']['items'][1]['clarifications']['A'], 'Not charged with no work.')  # the revision it was based on is untouched


class Plan(QualityCase):
    def script(self, errors):
        self.host.script = lambda body, host: {**suggestion(payload_of(body)), 'suspected_errors': errors} if prompt_name(body) == 'rubric_quality' else suggestion(payload_of(body))

    def items(self):
        with connect(self.path) as db:
            q = self.frq.question(db, self.plan['id'], self.frq.assignment(self.plan['id'])['questions'][0]['question_id'])
        return {item['id']: item['points'] for item in q['rubric']['rules']['items']}, q

    def test_a_points_change_the_instructor_asked_for_is_approved_with_one_click(self):
        points, _ = self.items()
        wanted = points['R3'] / 2 if points['R3'] else -0.5
        self.script([{'item_ids': ['R3'], 'message': 'You answered that R3 should be worth half; it is not yet.', 'title': 'R3 points',
                      'edit': {'lines': [{'version': '', 'item_id': 'R3', 'points': wanted}]}},
                     {'item_ids': ['R2'], 'message': 'Does a decimal count?', 'title': 'Decimals'}])
        checked = self.check()
        concern = checked['check']['result']['suspected_errors'][0]
        self.assertEqual(concern['edit'], [{'question_id': concern['question_id'], 'item_id': 'R3', 'points': wanted}])
        with self.assertRaises(Conflict):  # a decision with nothing to approve
            self.frq.apply_rubric_edit(self.plan['id'], {'check_id': checked['check']['id'], 'concerns': [1]})
        with self.assertRaises(Conflict):
            self.frq.apply_rubric_edit(self.plan['id'], {'check_id': 'another', 'concerns': [0]})
        after = self.frq.apply_rubric_edit(self.plan['id'], {'check_id': checked['check']['id'], 'concerns': [0]})
        points, q = self.items()
        self.assertEqual(points['R3'], wanted)
        self.assertEqual(q['rubric']['compiler']['name'], 'instructor-edited')
        self.assertFalse(after['check']['current'])  # the changed rubric is checked again
        self.assertTrue(after['readiness'][q['question_id']]['ready'])  # already named again for the reviewer
        with connect(self.path) as db:
            kinds = [r[0] for r in db.execute("SELECT kind FROM frq_events WHERE assignment_id=? ORDER BY rowid", (self.plan['id'],))]
        self.assertEqual(kinds[-3:], ['rubric_lines_edited', 'rubric_review_confirmed', 'rubric_edit_approved'])

    def test_a_drafts_key_fix_its_answers_resolved_is_still_made_on_the_plan(self):
        """Quiz 6 (2026-10-29): Problems 2 and 3's key fixes moved to the resolved findings when the instructor sent their answers,
        and approving would have made none of them."""
        checked = self.check()
        result = json.loads(json.dumps(checked['check']['result']))
        qid = result['questions'][0]['question_id']
        fix = {'kind': 'key', 'note': 'Added.', 'keys': [{'part': 'x', 'add': ['6']}]}
        result['suspected_errors'] = []
        result['resolution'] = {'rule': 'r', 'check_id': '', 'commented': [], 'asked': [], 'before': {},
                                'resolved': [{'kind': 'suspected_error', 'question_id': qid, 'item_ids': ['R1'], 'message': 'm', 'fix': fix, 'how': 'Faculty fixed it.'}]}
        cid = str(uuid.uuid4())
        with connect(self.path) as db:
            given = db.execute('SELECT input_json FROM frq_rubric_quality_checks WHERE id=?', (checked['check']['id'],)).fetchone()[0]
            now = '2026-10-29T12:00:00+00:00'
            db.execute("""INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,result_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'complete',?,?,?)""", (cid, self.plan['id'], str(uuid.uuid4()), '0' * 64, checked['input_sha256'], given,
                                                       encoded({'endpoint': {'name': 'beta-text'}, 'settings': {}}), encoded(result), now, now))
            db.commit()
        outcomes = self.frq.apply_rubric_fixes(self.plan['id'], cid)
        self.assertEqual(outcomes, [{'resolved': 0, 'question_id': qid, 'state': 'applied', 'added': [{'part': 'x', 'forms': ['6']}]}])
        _, q = self.items()
        self.assertEqual(q['content']['answer_key'], [{'label': 'x', 'accept': ['x=6', '6']}])
        with connect(self.path) as db:
            self.assertEqual(self.frq.fix_states(db, self.plan['id'], cid, result), {})  # the window lists only open findings

    def test_the_letters_choice_is_offered_and_never_answered_for_the_instructor(self):
        read = self.frq.rubric_choices(self.plan['id'])
        letters = next(c for c in read['options'] if c['key'] == 'letters')
        self.assertEqual([o['value'] for o in letters['options']], ['printed', 'case', 'consistent'])
        self.assertNotIn('letters', read['course'])
        self.assertEqual(rubric_choices.stated({'letters': 'case'})[0]['topic'], 'Letters and notation')
        self.frq.set_rubric_choices(self.plan['id'], {'choices': {'letters': 'printed'}})
        self.check()
        sent = [payload_of(r['body']) for r in self.host.requests if r['path'].endswith('/chat/completions') and prompt_name(r['body']) == 'rubric_quality'][-1]
        self.assertEqual([c['topic'] for c in sent['assignment_choices']], ['Letters and notation'])


if __name__ == '__main__':
    unittest.main()
