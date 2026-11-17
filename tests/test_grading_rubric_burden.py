"""Fewer rubric questions (requirement, 2026-10-24): GRADING "BOOKKEEPING IS
FIXED, NOT ASKED", "NOTHING ASKED TWICE" and "RUBRIC CHOICES".

Of about 36 questions the rubric reviewer asked since Oct 1, about 10 had one right answer, about 8 repeated a question already
answered, and four recurring policies (work, rounding, spelling, the "all incorrect" line) came back on assignment after assignment.
Synthetic plans and a scripted reviewer; nothing reaches a real model."""
import json
import sqlite3
import unittest
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict

from grading import rubric_choices
from grading.models import encoded
from grading.providers.local_vlm import VERSIONS
from grading.rubric_memory import definition_of_plan
from grading.rubric_quality import fix_fields, validate_problem, validate_question, with_fix_states
from rosters import connect
from fake_openai import payload_of, prompt_name
from test_grading_rubric_quality import QualityCase, suggestion

PROMPTS = Path(__file__).resolve().parent.parent / "grading" / "prompts"
KEY_FIX = {'kind': 'key', 'note': 'The key now accepts the forms R1 accepts.', 'keys': [{'version': '', 'part': 'x', 'add': ['x = 6', '6']}]}


def finding(**more):
    return {'item_ids': ['R1'], 'message': "The key accepts only x=6, though R1 credits any equation solved for x.", 'title': 'Key narrower than R1', **more}


class Fixes(unittest.TestCase):
    """What the reviewer may mark fixed, and what stays a decision."""
    question = {'question_id': 'a', 'exam_version': '', 'rubric_id': 'ra', 'builtin': [], 'rubric': {'items': [{'id': 'R1'}]}}

    def answer(self, issue):
        return {'summary': '', 'suspected_errors': [issue], 'items': [{'item_id': 'R1', 'clarification': '', 'excludes': [], 'reason': ''}]}

    def test_a_fix_is_kept_only_where_the_finding_has_one_right_answer(self):
        _, [kept] = validate_question(self.answer(finding(fix=KEY_FIX)), self.question)
        self.assertEqual(kept['fix'], {'kind': 'key', 'note': KEY_FIX['note'], 'keys': [{'part': 'x', 'add': ['x = 6', '6']}]})
        _, [guided] = validate_question(self.answer(finding(fix={'kind': 'guidance', 'note': 'Carried with this version’s values.', 'keys': []})), self.question)
        self.assertEqual(guided['fix'], {'kind': 'guidance', 'note': 'Carried with this version’s values.'})
        choices = [{'label': 'Accept', 'feedback': 'Accept it.', 'consequence': ''}, {'label': 'Refuse', 'feedback': 'Refuse it.', 'consequence': ''}]
        for issue in (finding(fix={'kind': 'none', 'note': '', 'keys': []}), finding(fix={**KEY_FIX, 'keys': []}), finding(fix={**KEY_FIX, 'keys': 'x'}),
                      finding(fix=KEY_FIX, choices=choices), finding()):
            with self.subTest(issue=issue):
                _, [asked] = validate_question(self.answer(issue), self.question)
                self.assertNotIn('fix', asked)  # a decision, as every finding was before
        self.assertEqual(fix_fields({'fix': {'kind': 'key', 'keys': [{'part': 'x', 'add': ['  ', 7]}]}}), {})

    def test_a_shared_key_fix_reaches_each_version_with_its_own_keys_only(self):
        a, b = ({**self.question, 'question_id': q, 'exam_version': v, 'rubric_id': 'r' + q} for q, v in (('a', 'A'), ('b', 'B')))
        issue = {'versions': ['A', 'B'], 'item_ids': [{'version': 'A', 'item_ids': ['R1']}, {'version': 'B', 'item_ids': ['R1']}], 'version_notes': [],
                 'message': 'Version B’s key lacks the written entry A accepts.', 'title': 'B key', 'decision': '', 'group': '', 'choices': [],
                 'fix': {'kind': 'key', 'note': 'Added.', 'keys': [{'version': 'B', 'part': '', 'add': ['the 3 in row 3']}]}}
        answer = {'summary': '', 'suspected_errors': [issue], 'consistency_issues': [],
                  'versions': [{'version': v, 'summary': '', 'items': [{'item_id': 'R1', 'clarification': '', 'excludes': [], 'reason': ''}]} for v in 'AB']}
        found, _, _ = validate_problem(answer, [a, b])
        self.assertEqual(found['a'][1][0]['fix'], {'kind': 'guidance', 'note': 'Added.'})  # A's key needs nothing
        self.assertEqual(found['b'][1][0]['fix']['keys'], [{'part': '', 'add': ['the 3 in row 3']}])
        self.assertEqual(found['a'][1][0]['shared'], found['b'][1][0]['shared'])

    def test_a_resolution_carries_an_applied_fix_and_drops_one_undone(self):
        concerns = [finding(fix=KEY_FIX), finding(fix={'kind': 'guidance', 'note': ''}), finding(fix=KEY_FIX)]
        states = {0: {'state': 'applied', 'added': [{'part': 'x', 'forms': ['6']}]}, 1: {'state': 'undone'}, 2: {'state': 'skipped'}}
        sent = with_fix_states(concerns, states)
        self.assertEqual(sent[0]['fix']['applied'], [{'part': 'x', 'forms': ['6']}])
        self.assertNotIn('fix', sent[1])
        self.assertNotIn('fix', sent[2])


class Burden(QualityCase):
    def script(self, errors):
        self.host.script = lambda body, host: {**suggestion(payload_of(body)), 'suspected_errors': errors} if prompt_name(body) == 'rubric_quality' else suggestion(payload_of(body))

    def key(self):
        with connect(self.path) as db:
            return self.frq.question(db, self.plan['id'], self.frq.assignment(self.plan['id'])['questions'][0]['question_id'])['content']['answer_key']

    def asked(self):
        return [payload_of(r['body']) for r in self.host.requests if r['path'].endswith('/chat/completions') and prompt_name(r['body']) == 'rubric_quality']

    # -- BOOKKEEPING IS FIXED, NOT ASKED -----------------------------------------------------------------------------------------
    def test_a_key_fix_adds_its_forms_keeps_the_check_current_and_undo_takes_them_back(self):
        self.script([finding(fix=KEY_FIX), finding(message='Does a decimal count?', title='Decimals')])
        checked = self.check()
        self.assertEqual(self.key(), [{'label': 'x', 'accept': ['x=6', 'x = 6', '6']}])
        self.assertTrue(checked['check']['current'])  # a key is no part of what a confirmation covers
        self.assertEqual(checked['state'], 'needs_review')
        fixes = checked['check']['fixes']
        self.assertEqual(fixes, {0: {'kind': 'key', 'note': KEY_FIX['note'], 'state': 'applied', 'added': [{'part': 'x', 'forms': ['x = 6', '6']}]}})
        # Once per check: asking again changes nothing.
        self.assertEqual(self.frq.apply_rubric_fixes(self.plan['id'], checked['check']['id']), [])
        self.assertEqual(self.key(), [{'label': 'x', 'accept': ['x=6', 'x = 6', '6']}])
        with self.assertRaises(Conflict):
            self.frq.undo_rubric_fix(self.plan['id'], {'check_id': checked['check']['id'], 'concerns': [1]})  # a decision, not a fix
        undone = self.frq.undo_rubric_fix(self.plan['id'], {'check_id': checked['check']['id'], 'concerns': [0]})
        self.assertEqual(self.key(), [{'label': 'x', 'accept': ['x=6']}])
        self.assertEqual(undone['check']['fixes'][0]['state'], 'undone')
        with self.assertRaises(Conflict):
            self.frq.undo_rubric_fix(self.plan['id'], {'check_id': checked['check']['id'], 'concerns': [0]})
        with connect(self.path) as db:
            kinds = [r[0] for r in db.execute("SELECT actor FROM frq_events WHERE assignment_id=? AND kind='answer_key_saved' ORDER BY rowid", (self.plan['id'],))]
        self.assertEqual(kinds[-2:], ['automatic', 'workspace'])

    def test_a_key_fix_that_cannot_be_made_is_asked(self):
        self.script([finding(fix={**KEY_FIX, 'keys': [{'version': '', 'part': 'y', 'add': ['y=2']}]})])
        checked = self.check()
        self.assertEqual(self.key(), [{'label': 'x', 'accept': ['x=6']}])
        self.assertEqual(checked['check']['fixes'][0]['state'], 'skipped')
        self.assertIn('no part', checked['check']['fixes'][0]['why'])

    def test_guidance_fixes_are_fixed_without_touching_the_key(self):
        self.script([finding(fix={'kind': 'guidance', 'note': 'The clarification carries it.', 'keys': []})])
        checked = self.check()
        self.assertEqual(checked['check']['fixes'], {0: {'kind': 'guidance', 'note': 'The clarification carries it.', 'state': 'applied'}})
        self.assertEqual(self.key(), [{'label': 'x', 'accept': ['x=6']}])

    # -- NOTHING ASKED TWICE -----------------------------------------------------------------------------------------------------
    def test_a_check_reads_the_latest_eighty_answers(self):
        """2026-10-26 review: Exam 1's 47 answers at the old limit of 40 left its seven earliest out of later checks."""
        checked = self.check()
        with connect(self.path) as db:
            given = json.loads(db.execute('SELECT input_json FROM frq_rubric_quality_checks WHERE id=?', (checked['check']['id'],)).fetchone()[0])
            concern = {**checked['check']['result']['suspected_errors'][0], 'kind': 'suspected_error', 'question_ids': [given['questions'][0]['question_id']]}
            many = [{**concern, 'decision': f'Does R3 accept form {n}?', 'response': f'Form {n} counts.'} for n in range(85)]
            now = '2026-10-26T12:00:00+00:00'
            db.execute("""INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,result_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'complete',?,?,?)""", (str(uuid.uuid4()), self.plan['id'], str(uuid.uuid4()), '0' * 64, checked['input_sha256'],
                                                       encoded({**given, 'resolves': {'concerns': many}}), encoded({'endpoint': {'name': 'beta-text'}, 'settings': {}}),
                                                       encoded(checked['check']['result']), now, now))
            db.commit()
            answers = self.frq.earlier_answers(db, self.plan['id'])
        self.assertEqual((len(answers), answers[0]['answered'], answers[-1]['answered']), (80, 'Form 5 counts.', 'Form 84 counts.'))

    def test_a_check_reads_what_the_instructor_answered_to_this_assignments_earlier_questions(self):
        checked = self.check()
        self.assertNotIn('earlier_answers', self.asked()[-1])  # nothing answered yet: the request it always was
        self.assertNotIn('fixes', checked['check'])  # a check without fixes answers as it always did
        with connect(self.path) as db:
            given = json.loads(db.execute('SELECT input_json FROM frq_rubric_quality_checks WHERE id=?', (checked['check']['id'],)).fetchone()[0])
            concern = {**checked['check']['result']['suspected_errors'][0], 'kind': 'suspected_error', 'question_ids': [given['questions'][0]['question_id']],
                       'decision': 'Does R3 accept x = 6.0?', 'response': 'Yes: a decimal equal to the exact value counts.'}
            now = '2026-10-24T12:00:00+00:00'
            for n in range(2):  # the same answer twice is one ruling
                db.execute("""INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,result_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,'complete',?,?,?)""", (str(uuid.uuid4()), self.plan['id'], str(uuid.uuid4()), '0' * 64, checked['input_sha256'],
                                                               encoded({**given, 'resolves': {'concerns': [concern]}}), encoded({'endpoint': {'name': 'beta-text'}, 'settings': {}}),
                                                               encoded(checked['check']['result']), now, now))
            db.commit()
        self.frq.queue_rubric_quality(self.plan['id'], {'request_id': str(uuid.uuid4()), 'input_sha256': checked['input_sha256']})
        self.frq.process_cloud()
        sent = self.asked()[-1]
        self.assertEqual(sent['earlier_answers'], [{'asked': 'Does R3 accept x = 6.0?', 'answered': 'Yes: a decimal equal to the exact value counts.', 'about': ['Question 1']}])
        status = self.frq.rubric_quality(self.plan['id'])
        self.assertEqual(status['input_sha256'], checked['input_sha256'])  # in no fingerprint
        confirmed = self.confirm(status)
        with connect(self.path) as db:
            self.assertIsNotNone(self.frq.confirmation_record(db, confirmed['review']['id'])['inputs'])
            # Approving rubrics after answering their questions teaches the course's memories now, once.
            runs = db.execute("SELECT trigger,actor,json_extract(input_json,'$.after') FROM frq_rubric_memory_runs WHERE assignment_id=?", (self.plan['id'],)).fetchall()
        self.assertEqual([tuple(r) for r in runs], [('instructor', 'automatic', 'finalization')])
        self.assertIsNone(self.frq.rubric_memory.after_finalization(self.plan['id']))  # one is already waiting

    # -- RUBRIC CHOICES ----------------------------------------------------------------------------------------------------------
    def test_the_instructors_choices_reach_the_check_and_stay_out_of_its_fingerprint(self):
        before = self.frq.rubric_quality(self.plan['id'])['input_sha256']
        read = self.frq.rubric_choices(self.plan['id'])
        self.assertEqual((read['choices'], read['saved']), ({}, False))
        self.assertEqual([c['key'] for c in read['options']], ['work', 'decimals', 'variants', 'letters', 'all_incorrect'])
        saved = self.frq.set_rubric_choices(self.plan['id'], {'choices': {'work': 'expected', 'decimals': '', 'variants': 'accept'}})
        self.assertEqual((saved['choices'], saved['saved']), ({'work': 'expected', 'variants': 'accept'}, True))
        self.frq.set_rubric_choices(self.plan['id'], {'choices': {'variants': 'accept', 'work': 'expected'}})  # the same: nothing written
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_choices').fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE frq_rubric_choices SET actor=?', ('x',))
            # A new draft of the course starts from these.
            self.assertEqual(rubric_choices.of_course(db, definition_of_plan(db, self.plan['id'])), {'work': 'expected', 'variants': 'accept'})
        for bad in ({'work': 'sometimes'}, {'tone': 'kind'}, ['work'], {'work': ['expected']}, {'work': {'a': 1}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.frq.set_rubric_choices(self.plan['id'], {'choices': bad})
        self.assertEqual(self.frq.rubric_quality(self.plan['id'])['input_sha256'], before)
        self.check()
        sent = self.asked()[-1]
        self.assertEqual([c['topic'] for c in sent['assignment_choices']], ['Shown work', 'Spelling of required words'])
        self.assertTrue(sent['assignment_choices'][0]['choice'].startswith('Work is expected for full credit'))


class Prompts(unittest.TestCase):
    """The new prompt versions are their predecessors with only the sections they add (and, for the reviewers, `fix` in the answer)."""
    def test_each_new_prompt_adds_only_its_sections(self):
        added = {'rubric_quality': (11, 12, 2), 'rubric_quality_versions': (7, 8, 2), 'rubric_quality_added': (4, 5, 2), 'rubric_quality_resolve': (9, 10, 3),
                 'rubric_write': (8, 9, 1), 'rubric_write_versions': (7, 8, 1), 'quiz_rubric': (8, 9, 1)}
        for name, (old, new, paragraphs) in added.items():
            with self.subTest(prompt=name):
                self.assertGreaterEqual(VERSIONS[name], new)  # later versions build on these (2026-10-29)
                before, after = ((PROMPTS / f'{name}-v{n}.txt').read_text() for n in (old, new))
                fresh = [p for p in after.split('\n\n') if p.strip() not in [q.strip() for q in before.split('\n\n')]]
                reviewer = name.startswith('rubric_quality')
                self.assertEqual(len(fresh), paragraphs + (1 if reviewer else 0))  # the reviewers' JSON contract paragraph names `fix`
                self.assertIn('`assignment_choices`', after)
                self.assertEqual('fix:{kind:"none"|"guidance"|"key"' in after, reviewer)
                self.assertEqual('`earlier_answers`' in after, reviewer)
                self.assertEqual(after.split('\n')[0], before.split('\n')[0])  # the first sentence names the prompt to the fake host
