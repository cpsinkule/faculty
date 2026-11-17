"""Real retained cloud workflow against local scripted hosts, with no live inference. High Assurance, its blind third grader and
its second team were removed on 2026-09-23; one team runs, and it never escalates."""
import json

from grading import committee, risk
from rosters import connect
from fake_openai import flag_review_answer, grade_answer, images_of, payload_of, prompt_name, reconcile_answer
from test_grading_cloud import endpoint
from test_grading_pipeline import Pipeline, CORRECT, INCONSISTENT, SLIP, BARE


class GradingTeams(Pipeline):
    legacy_workflow = False

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        s = saved['settings']
        s['tiers']['cloud_standard']['verifier'] = 'gamma-vision'
        s['endpoints'].extend([endpoint(self.host, 'delta-vision'), endpoint(self.host, 'epsilon-vision'), endpoint(self.host, 'zeta-vision')])
        self.frq.save_settings({'version': saved['version'], 'settings': s})

    def opinions(self, **models):
        def script(name, body):
            if name in ('reconcile', 'reconcile_literal', 'reconcile_context'):
                return None
            selection = models.get(body['model'].removeprefix('fixture/'), ('R1',))
            if callable(selection):
                return selection(name, body)
            return grade_answer(payload_of(body), selection, image=bool(images_of(body)))
        return script

    def requests(self):
        return [r['body'] for r in self.host.requests if r['path'].endswith('/chat/completions')]

    def assert_resolved(self, response):
        result = response['result']
        self.assertEqual(result['state'], 'proposed', result['reasons'])
        self.assertFalse(result['signals']['risk']['individual'])
        self.assertEqual(result['signals']['risk']['blocks'], [])
        self.assertEqual(result['signals']['committee']['status'], 'resolved')
        self.assertIsNone(risk.selection_disagreement(result['signals']['risk']['components']))
        self.assertNotEqual(self.frq.responses(self.plan['id'])[0]['state'], 'grading_review')
        self.assertEqual(response['acceptance']['basis'], 'team_consensus')
        self.assertEqual(response['acceptance']['kind'], 'automatic')
        self.assertEqual(self.frq.responses(self.plan['id'])[0]['state'], 'auto_accepted')
        self.assertEqual(self.frq.review_queue(self.plan['id'])['waiting'], 0)
        self.assertEqual(self.frq.acceptance_queue(self.plan['id'])['waiting'], [])

    def test_standard_agreement_skips_the_third(self):
        response, names = self.grade(CORRECT)
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assert_resolved(response)
        self.assertEqual(len(response['result']['signals']['panels']), 1)

    def test_the_listing_reads_each_acceptance_as_the_whole_view_gives_it(self):
        # Performance pass, 2026-10-02: the listing reads the acceptances of its own plan's current results only, instead of every
        # plan's; each response's acceptance columns are those of the unfiltered view.
        response, _ = self.grade(CORRECT)
        listed = {r['id']: (r['acceptance'], r['blind'], r['audit'], r['acceptance_active']) for r in self.frq.responses(self.plan['id'])}
        with connect(self.path) as db:
            whole = {row[0]: tuple(row[1:]) for row in db.execute("""SELECT r.id,acc.kind,acc.blind,acc.audit,acc.active FROM frq_responses r
                LEFT JOIN frq_all_acceptances acc ON acc.result_id=r.current_result_id WHERE r.assignment_id=?""", (self.plan['id'],))}
        self.assertEqual(listed, whole)
        self.assertEqual(listed[response['id']][::3], ('automatic', 1))

    def check_wrong_answer_below_full_credit(self, selected, total, mode):
        saved = self.frq.settings()
        # Isolate the key requirement; symbolic errors retain their separate gate.
        saved['settings']['symbolic_enabled'] = False
        self.frq.save_settings(saved)
        response, names = self.grade(SLIP, primary=selected, consistent='no', mode=mode)
        self.assert_resolved(response)
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        result = response['result']
        self.assertEqual(result['score']['score'], total)
        self.assertEqual(result['signals']['answer']['check'], 'mismatch')
        self.assertFalse(result['signals']['risk']['components']['answer']['full_credit'])
        self.assertTrue(result['signals']['risk']['thresholds']['require_key_match'])

    def test_standard_partial_credit_can_finalize_despite_wrong_final_answer(self):
        self.check_wrong_answer_below_full_credit(('R2',), 4, 'cloud_standard')

    def test_zero_credit_can_finalize_despite_wrong_final_answer(self):
        self.check_wrong_answer_below_full_credit(('R4',), 0, 'cloud_standard')

    def test_standard_correct_answer_without_work_can_finalize_no_work_credit(self):
        response, names = self.grade(BARE, primary=('R3',))
        self.assert_resolved(response)
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertEqual(response['result']['score']['score'], 2)
        self.assertEqual(response['result']['signals']['answer']['work'], 'none')
        self.assertTrue(response['result']['signals']['risk']['thresholds']['require_work_shown'])

    def test_wrong_answer_without_work_can_finalize_zero_credit(self):
        response, names = self.grade(['x = 8'], primary=('R4',))
        self.assert_resolved(response)
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertEqual(response['result']['score']['score'], 0)
        self.assertEqual(response['result']['signals']['answer']['work'], 'none')
        self.assertEqual(response['result']['signals']['answer']['check'], 'mismatch')

    def test_full_credit_without_required_work_still_needs_review(self):
        response, _ = self.grade(BARE)
        self.assertIsNone(response['acceptance'])
        self.assertIn('Full credit was proposed, but the rubric requires supporting work and little work is visible.', response['result']['signals']['risk']['blocks'])

    def check_crossed_out_work(self, mode):
        def reader(payload):
            value = reconcile_answer(payload)
            value['transcript']['lines'][3]['elements'][0]['state'] = 'crossed_out'
            return value
        response, names = self.grade([*CORRECT[:-1], 'x = 8', CORRECT[-1]], reader=reader, mode=mode)
        self.assert_resolved(response)
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        result = response['result']
        self.assertEqual(result['transcript']['lines'][3]['elements'][0]['state'], 'crossed_out')
        self.assertEqual(result['signals']['risk']['components']['reading']['crossed_out'], 1)
        self.assertEqual(result['signals']['risk']['escalate'], [])
        self.assertFalse(result['signals']['escalation']['escalated'])
        self.assertEqual({c['result'] for c in result['signals']['symbolic']['checks']}, {'pass'})
        self.assertEqual(result['score']['score'], 10)

    def test_standard_crossed_out_work_with_clear_replacement_finalizes_normally(self):
        self.check_crossed_out_work('cloud_standard')

    def test_standard_informed_tie_break_can_choose_secondary(self):
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': ('R2',)}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade', 'adjudicate'])
        self.assert_resolved(response)
        self.assertEqual(self.proposal(response), ['R1'])
        third = self.requests()[-1]
        payload = payload_of(third)
        self.assertEqual(set(payload['grader_assessments']), {'primary', 'secondary'})
        self.assertTrue(images_of(third))
        original = payload_of(self.requests()[1])
        self.assertEqual({k: payload[k] for k in original}, original)
        self.assertEqual(payload['grader_assessments']['primary']['score']['score'], 4)
        self.assertTrue(payload['grader_assessments']['primary']['applications'][1]['applied'])
        for request in self.requests()[1:3]:
            self.assertNotIn('grader_assessments', payload_of(request))
        self.assertEqual(response['result']['signals']['committee']['supporter'], 'second')
        self.assertEqual(self.frq.answer_groups(self.plan['id'], self.qid)['individual'], 0)

    def test_standard_tie_break_can_choose_primary(self):
        response, _ = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R2',)}))
        self.assert_resolved(response)
        self.assertEqual(response['result']['signals']['committee']['supporter'], 'primary')

    def test_same_score_full_credit_does_not_buy_a_third(self):
        """Both already gave the question maximum; a third cannot change the number."""
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R1', 'R4')}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertNotIn('adjudicate', names)
        self.assertEqual(self.proposal(response), ['R1'])
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertTrue(response['result']['signals']['risk']['individual'])
        self.assertTrue(response['result']['signals']['committee']['pair']['same_score'])
        self.assertEqual(response['result']['signals']['committee']['pair']['primary_score'], 10)
        self.assertFalse(response['result']['signals']['committee']['third_required'])
        self.assertEqual(response['result']['signals']['scope']['third_rule'], committee.THIRD_RULE)
        self.assertEqual(self.frq.answer_groups(self.plan['id'], self.qid)['individual'], 1)

    def test_three_distinct_credit_bearing_selections_require_review(self):
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R2',), 'gamma-vision': ('R3',)}))
        signals = response['result']['signals']
        self.assertEqual(names[-1], 'adjudicate')
        self.assertEqual(self.proposal(response), ['R3'])
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertTrue(signals['risk']['individual'])
        self.assertFalse(signals['escalation']['escalated'])
        self.assertEqual({o['score']['score'] for o in signals['panels'][0]['opinions'].values()}, {10, 4, 2})
        self.assertEqual(self.frq.answer_groups(self.plan['id'], self.qid)['individual'], 1)

    def test_other_checks_cannot_be_cleared_by_selection_agreement(self):
        response, names = self.grade(INCONSISTENT, script=self.opinions())
        self.assertNotIn('adjudicate', names)  # Both graders agree: another vote cannot settle what the mathematics contradicts.
        self.assertNotEqual(response['result']['state'], 'proposed')
        self.assertTrue(response['result']['signals']['risk']['blocks'])
        self.assertIn('Full credit was proposed, but the final answer does not match a confirmed answer key.', response['result']['signals']['risk']['blocks'])
        self.assertFalse(response['result']['signals']['escalation']['escalated'])

    def doubtful(self, **fields):
        def grader(name, body):
            return {**grade_answer(payload_of(body), ('R1',), image=bool(images_of(body))), **fields}
        return grader

    def check_step(self, response):
        with connect(self.path) as db:
            return json.loads(db.execute("SELECT output_json FROM frq_steps WHERE task_id=? AND stage='grade:check'", (response['task']['id'],)).fetchone()[0])

    def check_doubt_is_flagged_without_buying_the_reviewer(self, **doubt):
        """Measured 2026-09-20: 54 of 130 reviews were bought for agreeing pairs, and 33 of the 34 that finished stayed flagged."""
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': self.doubtful(**doubt)}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        result, check = response['result'], self.check_step(response)
        # The pair's agreement is the decision; the doubt still flags it and keeps it from being finalized.
        self.assertEqual((result['signals']['committee']['status'], result['signals']['committee']['third_required']), ('resolved', False))
        self.assertEqual((result['state'], response['acceptance']), ('grading_review', None))
        self.assertEqual((check['needs_third'], check['reasons']), (False, []))
        self.assertTrue(check['noted'])  # What was doubted stays on the record.

    def test_unclear_rubric_wording_flags_the_response_without_buying_the_reviewer(self):
        self.check_doubt_is_flagged_without_buying_the_reviewer(rubric_ambiguities=['R2 does not say whether the setup must be written as an equation.'])

    def test_a_graders_own_request_for_a_look_flags_the_response_without_buying_the_reviewer(self):
        self.check_doubt_is_flagged_without_buying_the_reviewer(review_reasons=['The transcript is unreadable where the final answer is written.'])

    def test_a_reading_problem_the_second_grader_reports_flags_an_agreeing_pair_without_buying_the_reviewer(self):
        """Measured: 30 agreeing pairs bought the reviewer for one; all 15 that finished kept their proposal and stayed flagged."""
        issue = [{'kind': 'layout', 'line_ids': [], 'description': 'The margin work may belong to another attempt.'}]
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': self.doubtful(reading_issues=issue)}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertEqual((response['result']['state'] == 'proposed', response['acceptance']), (False, None))
        self.assertIn('The second grader found an unresolved reading or layout problem.', response['result']['signals']['risk']['blocks'])

    def test_legacy_votes_are_bought_for_disagreement_or_a_missing_second(self):
        graded = lambda lines, lineage, score=1: {'applications': [{'rubric_item_id': r, 'applied': r in lines} for r in ('R1', 'R2', 'R4')], 'score': {'score': score}, 'lineage': lineage}
        self.assertEqual(committee.third_votes(graded(['R1'], 'alpha'), graded(['R1'], 'beta')), [])
        self.assertEqual(committee.third_votes(graded(['R1'], 'alpha'), graded(['R2'], 'beta')), ['The primary and secondary graders differ on R1, R2.'])
        self.assertEqual(committee.third_votes(graded(['R1'], 'alpha'), graded(['R1'], 'Alpha')), ['The two graders share a model family, so their agreement is one vote.'])
        self.assertEqual(committee.third_votes(graded(['R1'], 'alpha'), graded(['R1'], 'Alpha'), families=False), [])  # family-blind-v1
        for missing in (None, {'failed': True}):
            self.assertEqual(committee.third_votes(graded(['R1'], 'alpha'), missing), ['No completed secondary assessment is available.'])
        self.assertEqual(committee.third_votes(graded(['R1'], 'alpha'), {**graded(['R1'], 'beta'), 'score': None}), ['A required primary or secondary assessment is unscorable.'])
        # v1 still buys a same-score full-credit split; v2 does not. Same-score partial credit still buys.
        full = (graded(['R1'], 'alpha', 10), graded(['R2'], 'beta', 10))
        self.assertEqual(committee.third_votes(*full, maximum=10, rule=committee.LEGACY_THIRD_RULE), ['The primary and secondary graders differ on R1, R2.'])
        self.assertEqual(committee.third_votes(*full, maximum=10, rule=committee.CREDIT_THIRD_RULE), [])
        self.assertEqual(committee.third_votes(graded(['R2'], 'alpha', 4), graded(['R2', 'R4'], 'beta', 4), maximum=10, rule=committee.CREDIT_THIRD_RULE),
                         ['The primary and secondary graders differ on R4.'])

    def test_only_a_disagreement_buys_the_reviewer_under_the_current_rule(self):
        """requirement, 2026-09-22: the additional reviewer is called only where the two graders disagree, in both cloud tiers."""
        graded = lambda lines, lineage, score=1: {'applications': [{'rubric_item_id': r, 'applied': r in lines} for r in ('R1', 'R2', 'R4')], 'score': {'score': score}, 'lineage': lineage}
        vote = lambda primary, second, maximum=None: committee.third_votes(primary, second, maximum=maximum, rule=committee.THIRD_RULE)
        self.assertEqual(vote(graded(['R1'], 'alpha'), graded(['R2'], 'beta')), ['The primary and secondary graders differ on R1, R2.'])
        self.assertEqual(vote(graded(['R1'], 'alpha'), graded(['R2'], 'Alpha')), ['The primary and secondary graders differ on R1, R2.'])
        self.assertEqual(vote(graded(['R1'], 'alpha'), graded(['R1'], 'beta')), [])
        for nothing_to_compare in (None, {'failed': True}, {**graded(['R1'], 'beta'), 'score': None}, graded(['R1'], 'Alpha')):
            self.assertEqual(vote(graded(['R1'], 'alpha'), nothing_to_compare), [])  # No disagreement: it is flagged, not bought.
        self.assertEqual(vote(graded(['R1'], 'alpha', 10), graded(['R2'], 'beta', 10), 10), [])  # The v2 full-credit rule still holds.
        # Each of those now says on the record what the pair itself left, since no reviewer was asked.
        self.assertIn('No completed secondary assessment', committee.decide(graded(['R1'], 'alpha'), None)['reason'])
        self.assertIn('unscorable', committee.decide({**graded(['R1'], 'alpha'), 'score': None}, graded(['R1'], 'beta'))['reason'])
        self.assertIn('two model families', committee.decide(graded(['R1'], 'alpha'), graded(['R1'], 'Alpha'))['reason'])  # Before the family rule.
        self.assertEqual(committee.decide(graded(['R1'], 'alpha'), graded(['R1'], 'Alpha'), families=False)['status'], 'resolved')

    def test_a_pair_that_never_compared_is_flagged_without_buying_the_reviewer(self):
        """A secondary that failed is no disagreement: the response waits for the instructor, with no third call."""
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': lambda *_: {'invalid': True}}))
        self.assertEqual([n for n in names if n in ('adjudicate', 'third_grade')], [])  # The reviewer was never asked.
        self.assertEqual((response['result']['state'], response['acceptance']), ('grading_review', None))
        committee_ = response['result']['signals']['committee']
        self.assertEqual((committee_['status'], committee_['third_required']), ('unresolved', False))
        self.assertIn('No completed secondary assessment is available', committee_['reason'])
        self.assertEqual(self.check_step(response)['reasons'], [])
        self.assertIn('No completed secondary assessment is available.', self.check_step(response)['noted'])

    def test_the_third_settles_on_credit_bearing_lines(self):
        rubric = {'items': [{'id': i, 'points': p} for i, p in (('R1', 10), ('R2', 4), ('R4', 0))]}
        graded = lambda lines, lineage, score: {'applications': [{'rubric_item_id': r, 'applied': r in lines} for r in ('R1', 'R2', 'R4')], 'score': {'score': score}, 'lineage': lineage}
        primary, second, third = graded(['R1'], 'alpha', 10), graded(['R2'], 'beta', 4), graded(['R1', 'R4'], 'gamma', 10)
        v1 = committee.decide(primary, second, third, required=True, rubric=rubric, rule=committee.LEGACY_THIRD_RULE)
        self.assertEqual(v1['status'], 'unresolved')
        self.assertFalse(v1['third_comparisons']['primary']['agrees'])
        v2 = committee.decide(primary, second, third, required=True, rubric=rubric, rule=committee.THIRD_RULE)
        self.assertEqual((v2['status'], v2['supporter'], v2['source']), ('resolved', 'verifier', 'primary'))
        self.assertIn('credit-bearing', v2['reason'])

    def test_empty_evidence_from_the_third_is_not_a_failed_quotation(self):
        """Measured 2026-09-21: 153 of 156 quote-fail flags were empty slots on the third, listed as if a snippet had missed the page."""
        def empty_slots(name, body):
            result = grade_answer(payload_of(body), ('R1',))
            for application in result['applications']:
                application['evidence'] = [{}, {}] if application['applied'] else []
            return result
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R2',), 'gamma-vision': empty_slots}))
        self.assertEqual(names[-1], 'adjudicate')
        self.assert_resolved(response)
        self.assertEqual(self.proposal(response), ['R1'])
        text = ' '.join(response['result']['reasons'])
        self.assertNotIn('does not quote', text)
        self.assertNotIn('no supported evidence', text)

    def test_missing_evidence_on_the_standing_grade_still_needs_review(self):
        def empty_slots(name, body):
            if name == 'reconcile':
                return None
            result = grade_answer(payload_of(body), ('R1',))
            for application in result['applications']:
                application['evidence'] = [{}] if application['applied'] else []
            return result
        response, names = self.grade(CORRECT, script=empty_slots)
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertTrue(any('no supported evidence' in r for r in response['result']['reasons']))
        self.assertIsNone(response['acceptance'])

    def test_the_third_can_settle_by_credit_bearing_lines(self):
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R2',), 'gamma-vision': ('R1', 'R4')}))
        self.assertEqual(names[-1], 'adjudicate')
        committee_ = response['result']['signals']['committee']
        self.assertEqual(committee_['status'], 'resolved')
        self.assertEqual(self.proposal(response), ['R1'])  # The matched side, not the third's extra zero-point tick.
        self.assertEqual(response['result']['score']['score'], 10)
        self.assertEqual((committee_['supporter'], committee_['source']), ('verifier', 'primary'))
        self.assertIn('credit-bearing', committee_['reason'])
        self.assertFalse(committee_['third_comparisons']['primary']['agrees'])  # The leftover zero-point line stays on the receipt.

    def test_same_score_partial_credit_still_buys_a_third(self):
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': ('R2',), 'beta-text': ('R2', 'R4')}))
        self.assertEqual(names[-1], 'adjudicate')
        self.assertTrue(response['result']['signals']['committee']['pair']['same_score'])
        self.assertEqual(response['result']['signals']['committee']['pair']['primary_score'], 4)

    def test_a_batch_frozen_on_the_earlier_third_rule_still_buys_same_score_full_credit(self):
        real = self.frq.create_job
        def frozen(aid, command):
            job = real(aid, command)
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile['third_rule'] = committee.LEGACY_THIRD_RULE
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
            return job
        self.frq.create_job = frozen
        response, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R1', 'R4')}))
        self.assertEqual(names[-1], 'adjudicate')
        self.assertEqual(response['result']['signals']['scope']['third_rule'], committee.LEGACY_THIRD_RULE)

    def test_a_batch_submitted_before_the_third_vote_rule_keeps_the_rule_it_ran_with(self):
        real = self.frq.create_job
        def frozen(aid, command):
            job = real(aid, command)
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile.pop('third_rule')
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
            return job
        self.frq.create_job = frozen
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': self.doubtful(review_reasons=['Look at the margin.'])}))
        self.assertEqual(names[-1], 'adjudicate')
        self.assertNotIn('third_rule', response['result']['signals']['scope'])  # Its scope is the scope it always had.

    def test_a_ticked_full_credit_line_is_read_as_that_line_alone_so_stacking_is_no_disagreement(self):
        """Measured 2026-09-21: told the rule, the primary still ticked the full-credit line beside step lines in 25 of 119 answers
        and the second in 8; all three graders gave 8 of 8 with three sets of ticks, and the reviewer "matched neither"."""
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': ('R1', 'R2'), 'beta-text': ('R1',)}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])  # No additional reviewer was bought for it.
        self.assert_resolved(response)
        result = response['result']
        self.assertEqual((self.proposal(response), result['score']['score']), (['R1'], 10))
        self.assertEqual(result['signals']['normalized'], {'rule': 'full-credit-alone-v4', 'line': 'R1', 'dropped': ['R2']})
        self.assertEqual(result['signals']['rubric_quality']['reasons'], [])  # The proposal no longer violates the built-in exclusion.
        self.assertEqual(result['signals']['scope']['full_credit_rule'], 'full-credit-alone-v4')
        # What the model ticked stays on the record: on its application, and in its retained raw answer.
        primary = result['signals']['panels'][0]['opinions']['primary']
        self.assertEqual([(a['rubric_item_id'], a['applied'], a.get('subsumed_by')) for a in primary['applications'] if a['rubric_item_id'] in ('R1', 'R2')], [('R1', True, None), ('R2', False, 'R1')])
        self.assertEqual(response['acceptance']['basis'], 'team_consensus')

    def test_the_full_credit_rule_never_hides_a_contradiction_or_changes_a_score(self):
        from grading.rubric_quality import full_credit_alone
        rubric = {'mode': 'positive', 'floor': 0, 'ceiling': 10, 'groups': [], 'items': [
            {'id': i, 'points': p, 'group': None, 'requires': [], 'excludes': [], 'stackable': True, 'follow_through': False, 'repeat_penalty_for_same_originating_error': True}
            for i, p in (('R1', 10), ('R2', 4), ('R4', 0))]}
        entry = {'builtin': [{'item_id': 'R1', 'rule': 'full_credit'}, {'item_id': 'R4', 'rule': 'no_credit'}]}
        graded = lambda *ticked: {'applications': [{'rubric_item_id': i, 'applied': i in ticked} for i in ('R1', 'R2', 'R4')], 'score': {'score': 10}, 'review_reasons': ['R2: evidence does not quote the retained transcript.', 'Look at the margin.']}
        alone = full_credit_alone(graded('R1', 'R2'), entry, rubric, 10)
        self.assertEqual(([a['applied'] for a in alone['applications']], alone['score']['score'], alone['review_reasons']), ([True, False, False], 10, ['Look at the margin.']))
        for untouched in (graded('R1'), graded('R2'), graded('R1', 'R4'), graded('R1', 'R2', 'R4')):  # Alone already, no full-credit line, or the no-answer line beside it.
            self.assertIs(full_credit_alone(untouched, entry, rubric, 10), untouched)
        stacked = graded('R1', 'R2')
        self.assertIs(full_credit_alone(stacked, None, rubric, 10), stacked)  # A batch without a finalization has no built-in lines.

    def test_step_lines_that_add_up_to_the_full_value_are_read_as_the_full_credit_line(self):
        """Measured 2026-09-21: in 21 of 89 disagreements every grader gave full marks, one by the full-credit line and another by
        every step line, and an additional assessment was bought for a score nobody disputed."""
        from grading.rubric_quality import full_credit_alone
        line = lambda i, p, **more: {'id': i, 'points': p, 'group': None, 'requires': [], 'excludes': [], 'stackable': True, 'follow_through': False, 'repeat_penalty_for_same_originating_error': True, **more}
        rubric = {'mode': 'positive', 'floor': 0, 'ceiling': 10, 'groups': [], 'items': [line('R1', 10), line('R2', 4), line('R3', 6), line('R4', 0), line('R5', 6), line('R6', 0)]}
        entry = {'builtin': [{'item_id': 'R1', 'rule': 'full_credit'}, {'item_id': 'R4', 'rule': 'no_credit'}], 'pairs': [['R3', 'R5']]}
        def graded(*ticked, score=10):
            return {'applications': [{'rubric_item_id': i['id'], 'applied': i['id'] in ticked, 'reason': 'as read', 'evidence': [{'line_id': 'l-' + i['id'], 'student_text': 'x'}] if i['id'] in ticked else []} for i in rubric['items']],
                    'full_credit_eligible': True, 'score': {'score': score}, 'review_reasons': ['Look at the margin.']}
        read = full_credit_alone(graded('R2', 'R3'), entry, rubric, 10)
        self.assertEqual([a['rubric_item_id'] for a in read['applications'] if a['applied']], ['R1'])
        self.assertEqual((read['score']['score'], read['normalized'], read['review_reasons']), (10, {'rule': 'full-credit-alone-v4', 'line': 'R1', 'dropped': ['R2', 'R3'], 'route': 'steps'}, ['Look at the margin.']))
        full = next(a for a in read['applications'] if a['rubric_item_id'] == 'R1')
        self.assertEqual((full['read_from'], [e['line_id'] for e in full['evidence']]), (['R2', 'R3'], ['l-R2', 'l-R3']))  # The steps' evidence is the line's evidence.
        self.assertEqual([(a['rubric_item_id'], a['subsumed_by']) for a in read['applications'] if a.get('subsumed_by')], [('R2', 'R1'), ('R3', 'R1')])
        untouched = (graded('R2', score=4),                 # Below the full value.
                     graded('R2', 'R3', 'R6'),              # A ticked zero line says something the full-credit line does not.
                     graded('R2', 'R3', 'R4'),              # The no-credit line beside them is a contradiction to look at.
                     graded('R2', 'R3', 'R5'),              # 16 of 10: lines that exclude each other were ticked together.
                     graded('R3', 'R5', score=10))          # An exclusive pair, even where the clamped score is the full value.
        for assessment in untouched:
            self.assertIs(full_credit_alone(assessment, entry, rubric, 10), assessment)
        steps = graded('R2', 'R3')
        self.assertIs(full_credit_alone(steps, entry, rubric, 10, 'full-credit-alone-v1'), steps)  # A batch frozen under v1 finishes under v1.
        self.assertIs(full_credit_alone(steps, None, rubric, 10), steps)                            # No finalization, no built-in line.
        deducting = {**rubric, 'mode': 'negative'}
        self.assertIs(full_credit_alone(steps, entry, deducting, 10), steps)

    def test_a_parts_full_credit_line_beside_its_own_step_lines_is_read_as_that_line_alone(self):
        """Measured 2026-09-21: one grader gave 8 of 8 by the full-credit line, the other by "Part A: Correct Answer" (6) beside
        Part A's step lines plus Part B's line, and an additional assessment was bought for a score nobody disputed."""
        from grading.rubric_quality import full_credit_alone, part_lines
        line = lambda i, p, d: {'id': i, 'points': p, 'description': d, 'group': None, 'requires': [], 'excludes': [], 'stackable': True, 'follow_through': False, 'repeat_penalty_for_same_originating_error': True}
        rubric = {'mode': 'positive', 'floor': 0, 'ceiling': 8, 'groups': [], 'items': [
            line('R1', 8, 'Correct Answers: a. M= and b. $6130.10 with work'), line('R2', 6, 'Part A: Correct Answer with labels and values'), line('R3', 1, 'Part A: Answer included the label M='),
            line('R4', 1, 'Part A: Answer is a 2 x 2 matrix.'), line('R5', 0, 'Part A: No Matrix Given or Completely Incorrect'), line('R6', 2, 'Part B: Correct Answer and work to justify answer.'),
            line('R7', 1, 'Part B: Answer is correct, but no work.'), line('R8', 0, 'No Answers for Part a or Part b.')]}
        pairs = [['R2', 'R3'], ['R2', 'R4'], ['R2', 'R5'], ['R3', 'R5'], ['R4', 'R5'], ['R6', 'R7']]
        entry = {'builtin': [{'item_id': 'R1', 'rule': 'full_credit'}, {'item_id': 'R8', 'rule': 'no_credit'}], 'pairs': pairs}
        self.assertEqual(part_lines(rubric, entry, 8), {'R2': ['R3', 'R4', 'R5'], 'R6': ['R7']})
        def graded(*ticked, score):
            return {'applications': [{'rubric_item_id': i['id'], 'applied': i['id'] in ticked, 'reason': 'as read', 'evidence': [{'line_id': 'l-' + i['id'], 'student_text': 'x'}] if i['id'] in ticked else []} for i in rubric['items']],
                    'full_credit_eligible': True, 'score': {'score': score}, 'review_reasons': ['R3: evidence does not quote the retained transcript.', 'Look at the margin.']}
        ticks = lambda found: [a['rubric_item_id'] for a in found['applications'] if a['applied']]
        # Both parts complete: Part A's line alone, and the two parts' lines are then the question's full-credit line.
        read = full_credit_alone(graded('R2', 'R3', 'R4', 'R6', score=8), entry, rubric, 8)
        self.assertEqual((ticks(read), read['score']['score'], read['review_reasons']), (['R1'], 8, ['Look at the margin.']))
        self.assertEqual(read['normalized'], {'rule': 'full-credit-alone-v4', 'parts': [{'line': 'R2', 'dropped': ['R3', 'R4']}], 'line': 'R1', 'dropped': ['R2', 'R6'], 'route': 'steps'})
        self.assertEqual([(a['rubric_item_id'], a['subsumed_by']) for a in read['applications'] if a.get('subsumed_by')], [('R2', 'R1'), ('R3', 'R2'), ('R4', 'R2'), ('R6', 'R1')])
        # Part A complete and Part B not: Part A's line alone, the score as it was.
        partial = full_credit_alone(graded('R2', 'R3', 'R7', score=7), entry, rubric, 8)
        self.assertEqual((ticks(partial), partial['score']['score'], partial['normalized']), (['R2', 'R7'], 7, {'rule': 'full-credit-alone-v4', 'parts': [{'line': 'R2', 'dropped': ['R3']}]}))
        untouched = (graded('R2', 'R3', 'R4', 'R7', score=8),   # 9 of 8 hidden by the ceiling: read alone it would be 7, and code never changes a score.
                     graded('R2', 'R5', 'R6', score=8),         # The part's zero line beside its full-credit line is a contradiction to look at.
                     graded('R3', 'R4', 'R7', score=3),         # Step lines without the part's line say what they say.
                     graded('R2', 'R3', 'R8', score=7))         # The no-credit line beside anything is a contradiction.
        for assessment in untouched:
            self.assertIs(full_credit_alone(assessment, entry, rubric, 8), assessment)
        stacked = graded('R2', 'R3', 'R7', score=7)
        self.assertIs(full_credit_alone(stacked, entry, rubric, 8, 'full-credit-alone-v2'), stacked)  # A batch frozen under v2 finishes under v2.
        # Nothing is guessed: parts that do not add up to the question, a tie for a part's best line, or a line the finalization never paired.
        self.assertEqual(part_lines(rubric, entry, 10), {})
        self.assertEqual(part_lines({**rubric, 'items': rubric['items'] + [line('R9', 2, 'Part B: Another way to the answer.')]}, entry, 8), {})
        self.assertEqual(part_lines(rubric, {**entry, 'pairs': pairs[1:]}, 8), {})
        self.assertEqual(part_lines(rubric, None, 8), {})
        self.assertEqual(part_lines({**rubric, 'mode': 'negative'}, entry, 8), {})

    def test_full_marks_by_the_line_and_by_the_steps_is_no_disagreement(self):
        from frq_fixtures import finalize_rubrics
        rules = {'mode': 'positive', 'items': [
            {'id': 'R1', 'description': 'Correct Answer: $$x=6$$ and work to justify answer.', 'points': 10},
            {'id': 'R2', 'description': 'Set up the equation correctly', 'points': 4},
            {'id': 'R3', 'description': 'Solved the equation for x', 'points': 6},
            {'id': 'R4', 'description': 'No Answer or Completely Incorrect', 'points': 0}]}
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Coordinator rubric', 'rules': rules})
        finalize_rubrics(self.frq, self.plan['id'])
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': lambda name, body: grade_answer(payload_of(body), ('R2', 'R3'), full_credit_eligible=True), 'beta-text': ('R1',)}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])  # Nobody disputes 10 of 10: no additional reviewer is bought.
        self.assert_resolved(response)
        result = response['result']
        self.assertEqual((self.proposal(response), result['score']['score']), (['R1'], 10))
        self.assertEqual(result['signals']['normalized'], {'rule': 'full-credit-alone-v4', 'line': 'R1', 'dropped': ['R2', 'R3'], 'route': 'steps'})
        primary = result['signals']['panels'][0]['opinions']['primary']
        self.assertEqual([(a['rubric_item_id'], a['applied'], a.get('subsumed_by')) for a in primary['applications']],
                         [('R1', True, None), ('R2', False, 'R1'), ('R3', False, 'R1'), ('R4', False, None)])
        self.assertEqual(next(a for a in primary['applications'] if a['rubric_item_id'] == 'R1')['read_from'], ['R2', 'R3'])  # What the model ticked stays on the record.

    def test_a_batch_submitted_before_the_full_credit_rule_keeps_its_stacked_ticks(self):
        real = self.frq.create_job
        def frozen(aid, command):
            job = real(aid, command)
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile.pop('full_credit_rule')
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
            return job
        self.frq.create_job = frozen
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': ('R1', 'R2'), 'beta-text': ('R1',)}))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertEqual(self.proposal(response), ['R1', 'R2'])  # Stacked ticks stay as they were written.
        self.assertNotIn('normalized', response['result']['signals'])
        self.assertNotIn('full_credit_rule', response['result']['signals']['scope'])

    def test_a_grade_that_ticks_no_line_waits_for_the_instructor(self):
        # 2026-09-24: an arbitrator wrote "R1_1 applies alone" and marked every line unapplied, and both graders of another response
        # returned nothing; both were finalized at 0. A real zero ticks the rubric's zero-point line (R4 here), so nothing ticked waits.
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': (), 'beta-text': ()}))
        result = response['result']
        self.assertEqual(self.proposal(response), [])
        self.assertIn(risk.EMPTY_SELECTION, result['signals']['risk']['blocks'])
        self.assertIn(risk.EMPTY_SELECTION, result['reasons'])
        self.assertEqual((result['state'], result['signals']['risk']['individual']), ('grading_review', True))
        self.assertEqual(result['signals']['empty_selection']['zero_lines'], ['R4'])
        self.assertEqual(result['signals']['scope']['empty_selection_rule'], risk.EMPTY_SELECTION_RULE)
        self.assertIsNone(response['acceptance'])
        self.assertEqual(result['score']['score'], 0)  # Held, never rescored.
        self.assertTrue(risk.instructor_only(risk.EMPTY_SELECTION))  # No ruling settles it.
        self.assertEqual(risk.empty_selection({'empty_selection_rule': risk.EMPTY_SELECTION_RULE}, [], {'items': [{'id': 'R1', 'points': 2}]}), [])

    def test_a_negative_rubric_grade_that_ticks_nothing_is_full_credit_nobody_ticked(self):
        # 2026-10-19 (Version C problem 3): on a negative rubric the zero-point line is "Correct", and ticking nothing scores full
        # credit. v1 said the rubric "has a line for no answer" and kept the grade from the flag review; v2 says what happened and
        # sends it there, while the arbitrator and the flag reviewer still never settle it.
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        rules = {'mode': 'negative', 'items': [
            {'id': 'R1', 'description': 'Correct Answer: $$x=6$$ and work to justify answer.', 'points': 0},
            {'id': 'R2', 'description': 'Equation set up incorrectly', 'points': -4},
            {'id': 'R4', 'description': 'No Answer or Completely Incorrect', 'points': -10}]}
        q = self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Negative rubric', 'rules': rules})['questions'][0]
        self.frq.approve_rubric(self.plan['id'], self.qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'], 'acknowledge_rules': True})
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': (), 'beta-text': ()}))
        result = response['result']
        self.assertEqual(self.proposal(response), [])
        self.assertEqual(result['score']['score'], 10)  # Held, never rescored.
        self.assertIn(risk.EMPTY_FULL, result['signals']['risk']['blocks'])
        self.assertIn(risk.EMPTY_FULL, result['reasons'])
        self.assertNotIn(risk.EMPTY_SELECTION, result['reasons'])
        self.assertEqual(result['signals']['empty_selection'], {'rule': risk.EMPTY_SELECTION_RULE, 'zero_lines': ['R1']})
        # Held when nothing else holds it: the projection of the finalization at retention used to put it back to "proposed".
        self.assertEqual((result['state'], result['signals']['risk']['individual']), ('grading_review', True))
        self.assertEqual(result['signals']['rubric_quality']['base_state'], 'grading_review')
        self.assertIsNone(response['acceptance'])
        self.assertTrue(risk.instructor_only(risk.EMPTY_FULL))
        # A batch frozen under v1 finishes as it ran, words included.
        self.assertEqual(risk.empty_reason({'empty_selection_rule': 'empty-selection-v1'}, rules), risk.EMPTY_SELECTION)
        self.assertEqual(risk.empty_selection({'empty_selection_rule': 'empty-selection-v1'}, [], rules), ['R1'])

    def test_a_batch_submitted_before_the_empty_selection_rule_finishes_as_it_ran(self):
        real = self.frq.create_job
        def frozen(aid, command):
            job = real(aid, command)
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile.pop('empty_selection_rule')
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
            return job
        self.frq.create_job = frozen
        response, names = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': (), 'beta-text': ()}))
        self.assertNotIn(risk.EMPTY_SELECTION, response['result']['signals']['risk']['blocks'])
        self.assertNotIn('empty_selection_rule', response['result']['signals']['scope'])

    def test_missing_reviewer_requires_review_without_borrowing_another_model(self):
        saved = self.frq.settings()
        saved['settings']['tiers']['cloud_standard']['verifier'] = ''
        self.frq.save_settings(saved)
        response, names = self.grade(CORRECT, second=('R2',))
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade'])
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertIn('missing', response['result']['signals']['committee']['reason'])

    def test_failed_reviewer_is_not_a_tie_break(self):
        response, _ = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R2',), 'gamma-vision': lambda *_: {'invalid': True}}))
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertTrue(response['result']['signals']['panels'][0]['opinions']['verifier']['failed'])

    def test_scope_pins_the_team_and_the_workflow(self):
        response, _ = self.grade(CORRECT)
        scope = response['result']['signals']['scope']
        self.assertEqual(scope['grading_workflow'], committee.WORKFLOW)
        self.assertEqual(set(scope['teams']), {committee.STANDARD})
        self.assertEqual(scope['teams'][committee.STANDARD]['verifier']['model'], 'fixture/gamma-vision')
        self.assertFalse(scope['escalation_enabled'])
        estimate = self.frq.estimate(self.frq.settings()['settings'], committee.STANDARD, 1)
        self.assertEqual(set(estimate['roles']), {'reader', 'primary', 'second', 'verifier'})

    def test_automatic_finalization_exports_as_automatic_without_inventing_a_benchmark(self):
        response, _ = self.grade(CORRECT)
        self.assert_resolved(response)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_benchmark_reports').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_acceptance_permissions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_team_acceptances').fetchone()[0], 1)
        exported = json.loads(self.frq.export(self.plan['id'], 'audit')[0])
        self.assertEqual(exported['records'][0]['final']['basis'], 'team_consensus')
        self.assertEqual(exported['records'][0]['final']['status'], 'automatic')

    def test_an_instructor_can_overturn_a_team_finalization(self):
        response, _ = self.grade(CORRECT)
        after = self.frq.save_review(response['id'], {'version': response['version'], 'result_id': response['current_result_id'],
            'status': 'approved', 'selected_items': ['R2'], 'comment': 'Instructor correction', 'confirm_review': True})
        self.assertEqual(after['acceptance']['outcome'], 'overturned')
        self.assertEqual(after['review']['score']['score'], 4)
        report = self.frq.acceptance_report(self.plan['id'], self.qid)['reports'][0]
        self.assertEqual(report['team_finalized'], 1)
        self.assertEqual(report['all']['overturned'], 1)
        self.assertEqual(report['blind']['reviewed'], 0)

    def regrade_after_review(self, status='approved', *, frozen=True, script=None):
        """Grade, record the instructor's review, then send the response through the graders again (an ordinary batch of one)."""
        import uuid
        response, _ = self.grade(CORRECT)
        settings = self.frq.settings()
        settings['settings']['tiers']['cloud_standard']['flag_reviewer'] = 'alpha-vision'
        self.frq.save_settings(settings)  # A new regrade uses the replacement Standard schema.
        saved = self.frq.save_review(response['id'], {'version': response['version'], 'result_id': response['current_result_id'],
            'status': status, 'selected_items': ['R2'], 'comment': 'Instructor decision', **({'confirm_review': True} if status != 'draft' else {})})
        if script:
            self.host.script = script
        job = self.frq.regrade_response(response['id'], {'request_id': str(uuid.uuid4()), 'mode': committee.STANDARD, 'operation': 'grade'})
        if not frozen:  # A batch queued before the rule.
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile.pop('review_rule')
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
        self.run_all()
        return self.frq.response(response['id']), saved, job

    def test_a_regrade_that_passes_every_check_replaces_the_instructors_earlier_decision(self):
        """The review stays in the history; only which decision counts moves."""
        for status in ('approved', 'draft'):
            with self.subTest(status=status):
                self.setUp()
                after, saved, job = self.regrade_after_review(status)
                self.assertIsNone(after['review'])
                self.assertEqual((after['acceptance']['kind'], after['acceptance']['basis']), ('automatic', 'team_consensus'))
                with connect(self.path) as db:
                    kept = db.execute('SELECT status FROM frq_reviews WHERE id=?', (saved['review']['id'],)).fetchone()[0]
                    event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='review_superseded_by_regrade'").fetchone()[0])
                self.assertEqual(kept, status)  # Never erased.
                self.assertEqual((event['review_id'], event['result_id'], event['job_id'], event['review_status']),
                                 (saved['review']['id'], after['current_result_id'], job['id'], status))
                exported = json.loads(self.frq.export(self.plan['id'], 'audit')[0])['records'][0]['final']
                self.assertEqual((exported['status'], exported['basis']), ('automatic', 'team_consensus'))

    def test_a_flagged_regrade_leaves_the_instructors_decision_counting(self):
        def flagged(body, host):
            name = prompt_name(body)
            if name == 'flag_review':
                return flag_review_answer(payload_of(body))
            answer = grade_answer(payload_of(body), ('R1',), image=name in ('primary_vision', 'second_vision'))
            return {**answer, 'review_reasons': ['Check the last line.']} if name == 'primary_vision' else answer
        after, saved, _ = self.regrade_after_review(script=flagged)
        self.assertEqual(after['review']['id'], saved['review']['id'])
        self.assertIsNone(after['acceptance'])

    def test_a_batch_from_before_the_rule_keeps_the_decision_until_the_one_time_pass(self):
        after, saved, job = self.regrade_after_review('draft', frozen=False)
        self.assertEqual(after['review']['id'], saved['review']['id'])
        self.assertIsNone(after['acceptance'])
        done = self.frq.refinalize_regraded(job['id'])
        self.assertEqual(done['finalized'], [after['id']])
        again = self.frq.response(after['id'])
        self.assertIsNone(again['review'])
        self.assertEqual(again['acceptance']['basis'], 'team_consensus')
        self.assertEqual(self.frq.refinalize_regraded(job['id'])['finalized'], [])  # Nothing twice.

    def test_the_one_time_pass_never_replaces_a_decision_made_on_the_new_grade(self):
        """Its first run on Quiz 2 replaced four approvals the instructor had given the regraded grade itself: those are their word on it."""
        after, _, job = self.regrade_after_review('draft', frozen=False)
        approved = self.frq.save_review(after['id'], {'version': after['version'], 'result_id': after['current_result_id'],
            'status': 'approved', 'selected_items': ['R1'], 'comment': 'Looked at the new grade', 'confirm_review': True})
        self.assertEqual(self.frq.refinalize_regraded(job['id'])['finalized'], [])
        now = self.frq.response(after['id'])
        self.assertEqual(now['review']['id'], approved['review']['id'])
        self.assertIsNone(now['acceptance'])

    def test_a_grader_cut_off_at_the_gateways_time_limit_fails_the_response_for_the_instructor_to_retry(self):
        """requirement, 2026-09-27: DeepSeek wrote for 13 minutes on one Quiz 2 response until Vercel ended the stream, then was sent
        the same request again. It is not resent: the response shows as failed, and the batch's Retry asks that grader again."""
        from unittest.mock import patch
        from frq_fixtures import page
        from test_grading_pipeline import Reader
        cut = {'on': True}
        def answer(body, host):
            name = prompt_name(body)
            if name == 'reconcile':
                return reconcile_answer(payload_of(body))
            if name == 'transcript_grade' and cut['on']:
                return ('stream_error', 'gateway_stream_timeout')
            return grade_answer(payload_of(body), ('R1',), image=bool(images_of(body)))
        self.host.script = answer
        patch.stopall()
        patch('grading.extraction.extract', return_value={'metadata': {'registered': True, 'crop_bounds': [0, 0, 100, 100]},
            'images': {k: page() for k in ('original', 'grayscale', 'contrast', 'ink', 'cloud')}}).start()
        patch.object(self.frq, 'provider', return_value=Reader(CORRECT)).start()
        self.addCleanup(patch.stopall)
        job = self.queue(mode=committee.STANDARD)
        self.run_all()
        done = self.frq.job(job['id'])
        self.assertEqual((done['status'], done['progress']['failed']), ('failed', 1))
        self.assertIn('could not finish', done['tasks'][0]['error'])
        seconds = lambda: [b for b in self.requests() if prompt_name(b) == 'transcript_grade']
        self.assertEqual(len(seconds()), 1)  # Not resent automatically.
        with connect(self.path) as db:
            call = db.execute("SELECT state,confirmation_json FROM frq_cloud_calls WHERE step='grade:second'").fetchone()
        self.assertEqual((call[0], json.loads(call[1])['stream_duration']), ('failed', True))
        cut['on'] = False
        self.frq.retry_job(job['id'])
        self.run_all()
        self.assertEqual(self.frq.job(job['id'])['status'], 'review_ready')
        self.assertEqual(len(seconds()), 2)  # The retry asked the second grader again.
        self.assert_resolved(self.frq.response(done['tasks'][0]['response_id']))

    def test_recalculating_points_never_replaces_a_decision(self):
        from grading.acceptance import REVIEW_RULE, supersedes
        self.assertTrue(supersedes({'review_rule': REVIEW_RULE}, {'operation': 'grade'}))
        self.assertFalse(supersedes({'review_rule': REVIEW_RULE}, {'operation': 'rescore'}))
        self.assertFalse(supersedes({}, {'operation': 'grade'}))

    def test_suspension_withholds_automatic_results_and_returns_them_to_review(self):
        response, _ = self.grade(CORRECT)
        with self.frq.rosters.lock(), connect(self.path) as db:
            self.frq.suspend_acceptance(db, self.plan['id'], 'Synthetic suspension of the assessment')
        self.assertFalse(self.frq.response(response['id'])['acceptance']['active'])
        self.assertEqual(self.frq.responses(self.plan['id'])[0]['state'], 'grading_review')
        self.assertEqual(self.frq.review_queue(self.plan['id'])['waiting'], 1)

    def test_missing_secondary_cannot_be_silently_replaced_by_the_reviewer(self):
        saved = self.frq.settings()
        saved['settings']['tiers'][committee.STANDARD]['second'] = ''
        self.frq.save_settings(saved)
        response, _ = self.grade(CORRECT)
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertIsNone(response['acceptance'])

    def test_reviewer_request_for_manual_review_survives_matching_selections(self):
        def review(name, body):
            result = grade_answer(payload_of(body), ('R1',))
            result['review_reasons'] = ['The rubric does not say which margin calculation to assess.']
            return result
        response, _ = self.grade(CORRECT, script=self.opinions(**{'beta-text': ('R2',), 'gamma-vision': review}))
        self.assertEqual(response['result']['signals']['committee']['status'], 'resolved')
        self.assertEqual(response['result']['state'], 'grading_review')
        self.assertIsNone(response['acceptance'])
