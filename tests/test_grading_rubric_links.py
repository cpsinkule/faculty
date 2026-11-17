"""LINKED VERSIONS (requirement, 2026-10-02): which rubric lines of one problem correspond across its versions.

Pure checks of how the links are put together (seeds, the reviewer's links, the instructor's decisions) and of the reviewer's
answers that name a finding once for several versions. Synthetic rubrics only."""
import unittest

from grading.rubric_links import effective_sets, known_links, pair_key, seed_sets
from grading.rubric_quality import parallel_links, validate_problem, validate_resolution


def question(qid, version, items, *made):
    return {'question_id': qid, 'exam_version': version, 'rubric_id': 'r' + qid, 'builtin': [], 'origins': list(made),
            'rubric': {'mode': 'positive', 'items': [{'id': i, 'description': f'{i} on {version}', 'points': 1} for i in items]}}


# The instructor's example: one matrix expression, impossible on Version A because the product's inner sizes differ, impossible on
# Version B because matrices of different sizes are added, and possible on Version C (state the dimensions of the result).
DRAFTED = ['family', 'g1', 'P1']
MATRIX = [question('qa', 'A', ['R1', 'R2', 'R3'], DRAFTED), question('qb', 'B', ['R1', 'R2', 'R3'], DRAFTED), question('qc', 'C', ['R1', 'R2', 'R3'], DRAFTED)]
FAMILIES = [['qa', 'qb', 'qc']]


def lines(sets):
    return sorted((s['kind'], s['origin'], tuple((line['question_id'], line['item_id']) for line in s['lines'])) for s in sets)


class LinkSets(unittest.TestCase):
    def test_rubrics_drafted_together_seed_their_shared_item_ids_unverified(self):
        sets = seed_sets(MATRIX, FAMILIES)
        self.assertEqual(len(sets), 3)
        self.assertTrue(all(s['kind'] == 'seed' for s in sets))
        uploaded = [question('ua', 'A', ['R1']), question('ub', 'B', ['R1'])]  # Gradescope uploads say nothing about their making
        self.assertEqual(seed_sets(uploaded, [['ua', 'ub']]), [])

    def test_a_writer_that_drafted_every_version_says_where_its_links_start(self):
        from grading.rubric_links import drafted_kinds
        made = {'name': 'model-drafted-versions-v1', 'family': 'P1', 'generation_id': 'g1', 'line_kinds': {'R1': 'same', 'R2': 'related', 'R3': 'odd'}}
        self.assertEqual(drafted_kinds({'name': 'instructor-edited', 'based_on': {'name': 'instructor-corrected', 'based_on': made}}), {'R1': 'same', 'R2': 'related'})
        self.assertEqual(drafted_kinds({'name': 'gradescope-upload-v3'}), {})
        kinds = [{**q, 'line_kinds': {'R1': 'same', 'R2': 'related' if q['question_id'] == 'qb' else 'same'}} for q in MATRIX]
        sets = effective_sets(kinds, FAMILIES, [], {}, set())
        self.assertEqual(lines(sets), [('related', 'drafted', (('qa', 'R2'), ('qb', 'R2'), ('qc', 'R2'))), ('same', 'drafted', (('qa', 'R1'), ('qb', 'R1'), ('qc', 'R1'))),
                                       ('seed', 'seed', (('qa', 'R3'), ('qb', 'R3'), ('qc', 'R3')))])
        # The reviewer and the instructor still decide over the writer.
        reviewed = [{'kind': 'related', 'origin': 'reviewer', 'lines': [{'question_id': q, 'item_id': 'R1'} for q in ('qa', 'qb', 'qc')]}]
        self.assertIn(('related', 'reviewer', (('qa', 'R1'), ('qb', 'R1'), ('qc', 'R1'))), lines(effective_sets(kinds, FAMILIES, reviewed, {}, {'qa', 'qb', 'qc'})))
        # The check still sends them to the reviewer as candidates to verify, never as decided.
        self.assertTrue(all(link['kind'] == 'seed' for link in known_links(sets, {}, kinds)))

    def test_the_reviewer_replaces_the_seeds_of_a_problem_it_saw_even_where_it_found_no_link(self):
        reviewed = [{'kind': 'same', 'origin': 'reviewer', 'lines': [{'question_id': 'qa', 'item_id': 'R1'}, {'question_id': 'qb', 'item_id': 'R1'}]},
                    {'kind': 'related', 'origin': 'reviewer', 'note': 'Undefined for another reason, or defined.',
                     'lines': [{'question_id': 'qa', 'item_id': 'R2'}, {'question_id': 'qb', 'item_id': 'R2'}, {'question_id': 'qc', 'item_id': 'R2'}]}]
        sets = effective_sets(MATRIX, FAMILIES, reviewed, {}, {'qa', 'qb', 'qc'})
        self.assertEqual(lines(sets), [('related', 'reviewer', (('qa', 'R2'), ('qb', 'R2'), ('qc', 'R2'))), ('same', 'reviewer', (('qa', 'R1'), ('qb', 'R1')))])
        self.assertEqual([s.get('note') for s in sets if s['kind'] == 'related'], ['Undefined for another reason, or defined.'])
        # Without a reviewed check the seeds stand, unverified.
        self.assertEqual({s['kind'] for s in effective_sets(MATRIX, FAMILIES, [], {}, set())}, {'seed'})

    def test_the_instructor_wins_and_an_unlink_is_never_undone_by_another_path(self):
        reviewed = [{'kind': 'same', 'origin': 'reviewer', 'lines': [{'question_id': q, 'item_id': 'R1'} for q in ('qa', 'qb', 'qc')]}]
        unlink_c = {pair_key(('qc', 'R1'), ('qa', 'R1')): 'none', pair_key(('qc', 'R1'), ('qb', 'R1')): 'none'}
        sets = effective_sets(MATRIX, FAMILIES, reviewed, unlink_c, {'qa', 'qb', 'qc'})
        self.assertEqual(lines(sets), [('same', 'reviewer', (('qa', 'R1'), ('qb', 'R1')))])
        # Marking a set related is the instructor's decision on every pair of it.
        related = {pair_key(('qa', 'R1'), ('qb', 'R1')): 'related', pair_key(('qa', 'R1'), ('qc', 'R1')): 'related', pair_key(('qb', 'R1'), ('qc', 'R1')): 'related'}
        self.assertEqual(lines(effective_sets(MATRIX, FAMILIES, reviewed, related, {'qa', 'qb', 'qc'})), [('related', 'instructor', (('qa', 'R1'), ('qb', 'R1'), ('qc', 'R1')))])
        # A link to a line that no longer exists is ignored.
        gone = {pair_key(('qa', 'R9'), ('qb', 'R1')): 'same'}
        self.assertEqual(lines(effective_sets(MATRIX, FAMILIES, [], gone, set())), lines(effective_sets(MATRIX, FAMILIES, [], {}, set())))

    def test_two_lines_of_one_version_in_a_set_are_marked_a_conflict(self):
        both = {pair_key(('qa', 'R1'), ('qb', 'R2')): 'same'}  # qb's R1 is still seeded to qa's R1
        sets = effective_sets(MATRIX, FAMILIES, [], both, set())
        conflicted = [s for s in sets if s.get('conflict')]
        self.assertEqual(len(conflicted), 1)
        self.assertIn({'question_id': 'qb', 'item_id': 'R2'}, conflicted[0]['lines'])

    def test_the_check_is_told_the_instructors_decisions_as_fixed_and_the_rest_as_candidates(self):
        decided = {pair_key(('qa', 'R3'), ('qc', 'R3')): 'none'}
        sets = effective_sets(MATRIX, FAMILIES, [], decided, set())
        told = known_links(sets, decided, MATRIX)
        self.assertEqual(told[0], {'kind': 'none', 'origin': 'instructor', 'lines': [{'question_id': 'qa', 'version': 'A', 'item_id': 'R3'}, {'question_id': 'qc', 'version': 'C', 'item_id': 'R3'}]})
        self.assertTrue(all(link['origin'] == 'seed' and link['kind'] == 'seed' for link in told[1:]))


class ProblemPayload(unittest.TestCase):
    def test_a_problems_request_names_only_its_own_links_by_version(self):
        from grading.rubric_quality import BUILTIN, problem_payload
        other = [question('pa', 'A', ['R1'], ['family', 'g1', 'P2']), question('pb', 'B', ['R1'], ['family', 'g1', 'P2'])]
        family = [{**q, 'max_points': 3, 'title': 'Question 4', 'printed_number': '4', 'reference': {}} for q in MATRIX]
        told = known_links(effective_sets(MATRIX + other, FAMILIES + [['pa', 'pb']], [], {}, set()), {}, MATRIX + other)
        payload = problem_payload({'rules': BUILTIN, 'questions': family, 'known_links': told}, family, [0, 0, 0])
        self.assertEqual(len(payload['problem']['versions']), 3)
        self.assertEqual(len(payload['known_links']), 3)  # R1, R2, R3 of Question 4; Question 5's R1 stays out
        self.assertTrue(all(sorted(line) == ['item_id', 'version'] for link in payload['known_links'] for line in link['lines']))
        self.assertNotIn('known_links', problem_payload({'rules': BUILTIN, 'questions': family}, family, [0, 0, 0]))


class ProblemFindings(unittest.TestCase):
    family = [{**q, 'max_points': 3, 'title': 'Question 4', 'printed_number': '4'} for q in MATRIX]

    def answer(self, **more):
        return {'summary': '', 'versions': [{'version': v, 'summary': '', 'items': [{'item_id': i, 'clarification': '', 'excludes': [], 'reason': ''}
                                                                                  for i in ('R1', 'R2', 'R3')]} for v in 'ABC'],
                'suspected_errors': [], 'consistency_issues': [], **more}

    def test_one_finding_names_only_the_versions_it_arises_on_and_stays_one_row_per_version(self):
        finding = {'versions': ['A', 'B'], 'item_ids': [{'version': 'A', 'item_ids': ['R2']}, {'version': 'B', 'item_ids': ['R2']}],
                   'version_notes': [{'version': 'B', 'note': 'Undefined because a 2×3 and a 3×2 matrix are added.'}],
                   'message': 'How must a student say the expression is undefined?', 'title': 'Saying it is undefined', 'decision': 'Must the reason be given?', 'group': '', 'choices': []}
        found, _, _ = validate_problem(self.answer(suspected_errors=[finding]), self.family)
        a, b, c = found['qa'][1], found['qb'][1], found['qc'][1]
        self.assertEqual(c, [])  # Version C (defined) is left alone.
        self.assertEqual([row['item_ids'] for row in a + b], [['R2'], ['R2']])
        self.assertEqual(a[0]['shared'], b[0]['shared'])
        self.assertEqual((a[0]['versions_named'], a[0]['title']), (['A', 'B'], b[0]['title']))
        self.assertNotIn('version_note', a[0])
        self.assertEqual(b[0]['version_note'], 'Undefined because a 2×3 and a 3×2 matrix are added.')
        # A finding of a single version, or in the older form, has no shared key.
        single = {**finding, 'versions': ['C'], 'item_ids': [{'version': 'C', 'item_ids': ['R3']}], 'version_notes': []}
        older = {'version': 'A', 'item_ids': ['R1'], 'message': 'Check this line.'}
        found, _, _ = validate_problem(self.answer(suspected_errors=[single, older]), self.family)
        self.assertNotIn('shared', found['qc'][1][0])
        self.assertEqual(found['qa'][1], [{'question_id': 'qa', 'item_ids': ['R1'], 'message': 'Check this line.'}])

    def test_the_writers_mark_is_read_and_an_older_or_odd_one_is_left_out(self):
        from grading.rubric_generation import family_rubric, line_kinds
        family = {'id': 'P1', 'versions': [{'version': v, 'number': '1', 'points': 2, 'statement': '', 'parts': []} for v in 'AB']}
        line = lambda points, link=None: {'points': points, 'descriptions': [{'version': v, 'description': f'Line on {v}', 'clarification': ''} for v in 'AB'],
                                          **({'link': link} if link else {})}
        drafted = family_rubric({'points': 2, 'items': [line(2, 'same'), line(1, 'related'), line(0, 'maybe'), line(0)], 'notes': ''}, family, 'positive', ['A', 'B'])
        self.assertEqual([l.get('link') for l in drafted['items']], ['same', 'related', None, None])
        self.assertEqual(line_kinds(drafted['items']), {'R1': 'same', 'R2': 'related'})

    def test_a_finding_naming_an_unknown_version_or_line_is_refused(self):
        for bad in ({'versions': ['D'], 'item_ids': [], 'message': 'x'},
                    {'versions': ['A'], 'item_ids': [{'version': 'A', 'item_ids': ['R9']}], 'message': 'x'},
                    {'versions': ['A'], 'item_ids': [{'version': 'B', 'item_ids': ['R1']}], 'message': 'x'},
                    {'versions': ['A'], 'item_ids': [], 'version_notes': [{'version': 'C', 'note': 'n'}], 'message': 'x'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_problem(self.answer(suspected_errors=[bad]), self.family)

    def test_consistency_findings_keep_each_versions_lines(self):
        issue = {'versions': ['A', 'C'], 'item_ids': [{'version': 'A', 'item_ids': ['R2']}, {'version': 'C', 'item_ids': ['R3']}], 'message': 'C pays the dimensions elsewhere.'}
        _, consistency, _ = validate_problem(self.answer(consistency_issues=[issue]), self.family)
        self.assertEqual(consistency[0]['lines'], {'qa': ['R2'], 'qc': ['R3']})

    def test_parallel_lines_that_cannot_be_used_are_dropped_never_refused(self):
        sets = [{'kind': 'same', 'lines': [{'version': 'A', 'item_id': 'R1'}, {'version': 'B', 'item_id': 'R1'}], 'note': ''},
                {'kind': 'related', 'lines': [{'version': 'A', 'item_id': 'R2'}, {'version': 'B', 'item_id': 'R2'}, {'version': 'C', 'item_id': 'R2'}], 'note': 'Another outcome on C.'},
                {'kind': 'same', 'lines': [{'version': 'A', 'item_id': 'R1'}, {'version': 'C', 'item_id': 'R1'}], 'note': ''},      # A's R1 is already linked
                {'kind': 'same', 'lines': [{'version': 'A', 'item_id': 'R3'}, {'version': 'A', 'item_id': 'R2'}], 'note': ''},      # two of one version
                {'kind': 'similar', 'lines': [{'version': 'B', 'item_id': 'R3'}, {'version': 'C', 'item_id': 'R3'}], 'note': ''},   # unknown kind
                {'kind': 'same', 'lines': [{'version': 'B', 'item_id': 'R9'}, {'version': 'C', 'item_id': 'R3'}], 'note': ''},      # unknown line
                {'kind': 'same', 'lines': [{'version': 'B', 'item_id': 'R3'}], 'note': ''}]                                       # alone
        links, dropped = parallel_links(self.answer(parallel_lines=sets), self.family)
        self.assertEqual(dropped, 5)
        self.assertEqual([(link['kind'], [(line['question_id'], line['item_id']) for line in link['lines']]) for link in links],
                         [('same', [('qa', 'R1'), ('qb', 'R1')]), ('related', [('qa', 'R2'), ('qb', 'R2'), ('qc', 'R2')])])
        self.assertEqual(links[1]['note'], 'Another outcome on C.')
        self.assertEqual(parallel_links(self.answer(), self.family), ([], 0))  # an older reviewer returns none

    def test_a_resolution_says_where_an_answer_did_not_fit(self):
        answer = self.answer(concerns=[{'concern': 0, 'resolved': True, 'how': 'Applied on A and B; C is defined.', 'not_applicable': ['C', 'Z'],
                                        'title': '', 'decision': '', 'group': '', 'choices': []}])
        *_, outcomes = validate_resolution(answer, self.family, [0])
        self.assertEqual(outcomes[0]['not_applicable'], ['C'])
        answer['concerns'][0]['not_applicable'] = []
        *_, outcomes = validate_resolution(answer, self.family, [0])
        self.assertNotIn('not_applicable', outcomes[0])


if __name__ == '__main__':
    unittest.main()
