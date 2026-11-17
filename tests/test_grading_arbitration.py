"""The arbitrator (`final-arbitrator-v2`) against local scripted hosts: no live inference.

Requirement, 2026-09-21: the teams run as always; what would then wait for the instructor goes before the team's additional reviewer,
which adopts an earlier assessment, poses its own ticks, or finds that no reliable grade can be determined. Every Cloud batch
freezes it (`standard.freeze`); its optional switch and v1's fourth model were removed on 2026-09-23. These tests freeze the rule
on the older team fixture, so the arbitration machinery is exercised without Cloud's flag reviewer around it.
"""
import json

from grading import arbitration, config
from rosters import connect
from fake_openai import arbitrate_answer, payload_of
import test_grading_teams as teams
from test_grading_pipeline import CORRECT, Pipeline


class FinalArbitrator(Pipeline):
    """The team fixture's helpers without its test cases: those describe the teams with the mode OFF, and run in their own file."""
    legacy_workflow = False
    opinions, requests = teams.GradingTeams.opinions, teams.GradingTeams.requests

    def assert_finalized(self, response, basis):
        result = response['result']
        self.assertEqual((result['state'], result['signals']['risk']['blocks'], result['signals']['risk']['individual'], result['reasons']), ('proposed', [], False, []))
        self.assertEqual((result['signals']['committee']['status'], response['acceptance']['kind'], response['acceptance']['basis']), ('resolved', 'automatic', basis))

    # The three graders of the Standard team tick three different sets: the additional reviewer matches neither, so the team is
    # unresolved and the response waits for the instructor.
    SPLIT = {'alpha-vision': ('R2',), 'beta-text': ('R1',), 'gamma-vision': ('R3',)}

    arbitrating = True

    def queue(self, *args, **kwargs):
        job = super().queue(*args, **kwargs)
        if self.arbitrating:  # Frozen with the batch, exactly as `standard.freeze` freezes it on every Cloud batch.
            with connect(self.path) as db:
                profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
                profile.update(final_arbitrator=True, final_arbitrator_rule=arbitration.RULE)
                db.execute('UPDATE frq_jobs SET profile_json=? WHERE id=?', (json.dumps(profile), job['id']))
        return job

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        s = saved['settings']
        s['tiers']['cloud_standard']['verifier'] = 'gamma-vision'
        self.frq.save_settings({'version': saved['version'], 'settings': s})

    def ruled(self, ruling, team=None, **more):
        """Grade one response with the split team and a scripted ruling. `ruling(payload)` answers the arbitrate prompt."""
        asked = {}
        def script(name, body):
            if name == 'arbitrate':
                asked[json.dumps(payload_of(body), sort_keys=True)] = payload_of(body)  # The fixture consults a script twice per request.
                return ruling(payload_of(body))
            return self.opinions(**(self.SPLIT if team is None else team))(name, body)
        response, names = self.grade(CORRECT, script=script, **more)
        return response, names, list(asked.values())

    def test_only_a_frozen_rule_turns_it_on_and_the_old_switch_is_retired(self):
        # The optional switch was removed on 2026-09-23: a saved one still loads, and is dropped.
        self.assertNotIn('final_arbitrator', config.validate({'final_arbitrator': True, 'final_arbitrator_model': 'x'}))
        self.assertFalse(arbitration.enabled({'final_arbitrator': True}))  # Never without the rule a batch froze.
        frozen = {'final_arbitrator': True, 'final_arbitrator_rule': arbitration.RULE}
        self.assertEqual((arbitration.enabled(frozen), arbitration.reviewer_arbitrates(frozen)), (True, True))
        self.assertEqual(config.removed_option(frozen), '')
        # A batch frozen on v1 (its own fourth model) or on the Jev tie-breaker is refused rather than run by other rules.
        self.assertRegex(config.removed_option({'final_arbitrator': True, 'final_arbitrator_rule': 'final-arbitrator-v1',
                                                'final_arbitrator_model': 'eta-vision'}), '^Final Arbitrator Mode v1, which this batch froze, was removed')
        self.assertRegex(config.removed_option({'jev_tiebreak': True, 'jev_tiebreak_rule': 'jev-tiebreak-v3'}), '^The Jev tie-breaker')

    def test_the_additional_reviewer_is_the_arbitrator_and_is_called_once_only_to_arbitrate(self):
        """requirement, 2026-09-21: no third assessment is bought for an ordinary tie-break; the reviewer arbitrates instead."""
        response, names, seen = self.ruled(lambda payload: arbitrate_answer(payload, adopt='standard.second'))
        # gamma-vision is Cloud Standard's additional reviewer. It answered the arbitrate prompt, and nothing else.
        self.assertEqual(names, ['reconcile', 'grade', 'transcript_grade', 'arbitrate'])
        asked = [r['body']['model'] for r in self.host.requests if r['path'].endswith('/chat/completions')]
        self.assertEqual(asked.count('fixture/gamma-vision'), 1)
        self.assertEqual(response['result']['signals']['arbiter']['model'], 'fixture/gamma-vision')
        # No third assessment exists, so the arbitrator saw the pair only, and the check step says the reviewer was reserved.
        self.assertEqual(sorted(seen[0]['assessments']), ['standard.primary', 'standard.second'])
        with connect(self.path) as db:
            check = json.loads(db.execute("SELECT output_json FROM frq_steps WHERE stage='grade:check'").fetchone()[0])
            self.assertFalse([r[0] for r in db.execute("SELECT stage FROM frq_steps WHERE stage='grade:verifier'")])
        self.assertEqual((check['needs_third'], bool(check['arbitrator_reserved'])), (False, True))

    def test_a_batch_without_the_rule_runs_exactly_as_it_did(self):
        self.arbitrating = False
        response, names, _ = self.ruled(lambda payload: arbitrate_answer(payload))
        self.assertNotIn('arbitrate', names)
        result = response['result']
        self.assertEqual((result['state'], result['signals']['committee']['status']), ('grading_review', 'unresolved'))
        self.assertNotIn('arbiter', result['signals'])
        self.assertNotIn('final_arbitrator', result['signals']['scope'])  # Its scope is the scope it always had.
        with connect(self.path) as db:
            self.assertFalse([r[0] for r in db.execute("SELECT stage FROM frq_steps WHERE stage LIKE 'grade:arbiter%'")])

    def test_a_response_the_team_finalizes_is_never_sent_to_the_arbitrator(self):
        response, names, _ = self.ruled(lambda payload: arbitrate_answer(payload), team={})  # Every grader ticks R1: resolved, all checks pass.
        self.assertNotIn('arbitrate', names)
        self.assert_finalized(response, 'team_consensus')
        with connect(self.path) as db:
            check = json.loads(db.execute("SELECT output_json FROM frq_steps WHERE stage='grade:arbiter:check'").fetchone()[0])
        self.assertEqual((check['needed'], check['flags']), (False, []))  # Why nothing was sent is on the record.

    def test_adopting_an_earlier_assessment_finalizes_that_complete_assessment(self):
        response, names, seen = self.ruled(lambda payload: arbitrate_answer(payload, adopt='standard.second'))
        self.assertEqual(names[-1], 'arbitrate')
        sent, = seen
        # It was shown everything: every earlier assessment, each team's decision, the standing one, and every flag.
        self.assertEqual((sorted(sent['assessments']), sent['standing'], [d['status'] for d in sent['decisions']]), (['standard.primary', 'standard.second'], 'standard.primary', ['unresolved']))
        self.assertTrue(sent['flags'] and all(flag['id'] and flag['text'] and flag['about'] for flag in sent['flags']))
        self.assertEqual([a['rubric_item_id'] for a in sent['assessments']['standard.second']['applications'] if a['applied']], ['R1'])
        self.assertNotIn('provider_response', json.dumps(sent['assessments']))  # Validated assessments, never a vendor's raw answer.
        result = response['result']
        self.assert_finalized(response, 'final_arbitrator')
        self.assertEqual((self.proposal(response), result['score']['score']), (['R1'], 10))
        arbiter = result['signals']['arbiter']
        self.assertEqual((arbiter['outcome'], arbiter['adopted'], arbiter['standing_before'], arbiter['model']), ('adopted', 'standard.second', 'standard.primary', 'fixture/gamma-vision'))
        self.assertTrue(all(flag['resolved'] and flag['note'] for flag in arbiter['flags']))
        # What was flagged stays on the record with each ruling; the team's own decision stays in its panel, as it ran.
        # The disagreement is one flag naming its lines, not three restatements of it (2026-09-22).
        about = [flag['text'] for flag in arbiter['flags'] if 'disagree' in flag['text'] or 'differ on' in flag['text']]
        self.assertEqual(len(about), 1, about)
        self.assertTrue(about[0].startswith(arbitration.DISAGREE_ON + 'R1, R2'), about)
        self.assertIn('arbitrated', result['signals']['risk'])
        self.assertEqual(result['signals']['panels'][0]['decision']['status'], 'unresolved')
        committee = result['signals']['committee']
        self.assertEqual((committee['status'], committee['team_status'], committee['resolved_by'], committee['source']), ('resolved', 'unresolved', 'final_arbitrator', 'standard.second'))
        self.assertEqual(result['signals']['scope']['final_arbitrator']['rule'], arbitration.RULE)
        # The standing grade is the adopted grader's own complete assessment, evidence included: nothing was merged.
        self.assertEqual([e['line_id'] for a in result['applications'] if a['applied'] for e in a['evidence']], ['crop-0-line-4'])

    def test_posing_its_own_ticks_finalizes_them_only_as_a_validated_assessment(self):
        response, names, _ = self.ruled(lambda payload: arbitrate_answer(payload, 'revise', chosen=('R1',)))
        self.assert_finalized(response, 'final_arbitrator')
        result = response['result']
        self.assertEqual((self.proposal(response), result['score']['score'], result['signals']['arbiter']['outcome']), (['R1'], 10, 'revised'))
        self.assertEqual((result['signals']['committee']['source'], response['acceptance']['basis']), ('arbiter', 'final_arbitrator'))
        # Ticks that do not decide every rubric line are no assessment: asked again, and then the response stays where the team left it.
        def partial(payload):
            answer = arbitrate_answer(payload, 'revise', chosen=('R2',))
            return {**answer, 'applications': answer['applications'][:2]}
        response, names, _ = self.ruled(partial)
        self.assertGreaterEqual(names.count('arbitrate'), 2)
        self.assertEqual((response['result']['state'], response['result']['signals']['arbiter']['outcome'], response['acceptance']), ('grading_review', 'failed', None))
        self.assertEqual(self.proposal(response), ['R2'])  # The team's standing proposal (its primary grader's), untouched.

    def test_revised_full_total_does_not_overwrite_a_rejected_full_credit_item(self):
        from frq_fixtures import finalize_rubrics
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Synthetic form requirement',
            'rules': {'mode': 'positive', 'items': [
                {'id': 'R1', 'description': 'Correct Answer: x=6 with a boxed answer and supporting work.', 'points': 10},
                {'id': 'R2', 'description': 'Correct equation setup.', 'points': 4},
                {'id': 'R3', 'description': 'Correct solution with work, without a boxed answer.', 'points': 6},
                {'id': 'R4', 'description': 'No Answer or Completely Incorrect', 'points': 0}]}})
        finalize_rubrics(self.frq, self.plan['id'])
        def revise(payload):
            answer = arbitrate_answer(payload, 'revise', chosen=('R2', 'R3'))
            answer['full_credit_eligible'] = False
            answer['applications'][0]['reason'] = 'The answer is not boxed; R1 does not apply.'
            return answer
        response, _, _ = self.ruled(revise, team={'alpha-vision': ('R1',), 'beta-text': ('R2',)})
        self.assertEqual(self.proposal(response), ['R2', 'R3'])
        self.assertEqual(response['result']['score']['score'], 10)
        self.assertFalse(response['result']['applications'][0]['applied'])
        self.assertIn('not boxed', response['result']['applications'][0]['reason'])
        with connect(self.path) as db:
            retained = json.loads(db.execute("SELECT output_json FROM frq_steps WHERE stage='grade:arbiter'").fetchone()[0])
        self.assertFalse(retained['assessment']['full_credit_eligible'])
        self.assertNotIn('normalized', retained['assessment'])

    def test_no_reliable_grade_keeps_the_response_with_the_instructor(self):
        response, names, _ = self.ruled(lambda payload: arbitrate_answer(payload, 'unreliable', resolve=False, reason='The transcript misses the line that decides credit.'))
        result = response['result']
        self.assertEqual((result['state'], result['signals']['arbiter']['outcome'], response['acceptance']), ('grading_review', 'unreliable', None))
        self.assertTrue(result['signals']['risk']['individual'])
        self.assertTrue(any('no reliable grade can be determined: The transcript misses' in reason for reason in result['reasons']))
        self.assertEqual((self.proposal(response), result['signals']['committee']['status']), (['R2'], 'unresolved'))  # The team's proposal and decision stand.

    def test_a_grade_with_a_flag_left_standing_is_not_a_reliable_grade(self):
        def careless(payload):
            return arbitrate_answer(payload, adopt='standard.second', resolve={payload['flags'][0]['id']})  # One flag ruled, the rest left standing.
        response, _, seen = self.ruled(careless)
        self.assertGreater(len(seen[0]['flags']), 1)
        result = response['result']
        self.assertEqual((result['state'], result['signals']['arbiter']['outcome'], response['acceptance']), ('grading_review', 'unsettled', None))
        self.assertEqual(self.proposal(response), ['R2'])

    def test_a_ruling_never_settles_a_flag_it_was_not_shown(self):
        """Its own ticks go through every deterministic check again: full credit posed for work that ends in another answer is a NEW flag."""
        from test_grading_pipeline import INCONSISTENT
        seen = []
        def script(name, body):
            if name == 'arbitrate':
                seen.append(payload_of(body))
                return arbitrate_answer(payload_of(body), 'revise', chosen=('R1',))
            return self.opinions(**{'alpha-vision': ('R2',), 'beta-text': ('R3',), 'gamma-vision': ('R4',)})(name, body)
        response, _ = self.grade(INCONSISTENT, script=script)
        result = response['result']
        self.assertEqual((result['signals']['arbiter']['outcome'], result['state'], response['acceptance']), ('unsettled', 'grading_review', None))
        self.assertTrue(result['signals']['arbiter']['open_flags'])
        self.assertTrue(set(result['signals']['arbiter']['open_flags']).isdisjoint(flag['text'] for flag in seen[0]['flags']))

    def test_an_arbitrator_that_cannot_answer_leaves_the_response_as_the_team_left_it(self):
        def script(name, body):
            return ('status', 500, {}, {'error': 'unavailable'}) if name == 'arbitrate' else self.opinions(**self.SPLIT)(name, body)
        response, names = self.grade(CORRECT, script=script)
        result = response['result']
        self.assertEqual((result['state'], result['signals']['arbiter']['outcome'], self.proposal(response), response['acceptance']), ('grading_review', 'failed', ['R2'], None))
        self.assertEqual(self.frq.job(self.frq.response(response['id'])['task']['job_id'])['status'], 'review_ready')  # The batch itself never fails for it.

    def test_the_estimate_prices_the_additional_reviewer_as_the_arbitrator(self):
        settings = {**self.frq.settings()['settings'], 'grading_workflow': 'grading-teams-v1'}
        # No fourth model is priced: the additional reviewer is the arbitrator, and it reads more than a third grader would.
        priced = self.frq.estimate(settings, 'cloud_standard', 10)['roles']
        self.assertNotIn('final_arbitrator', priced)
        self.assertEqual(priced['verifier']['model'], 'fixture/gamma-vision')
        # Arbitrating costs more than the third assessment it replaces: it reads every assessment and flag (fixtures are free, so price one).
        costed = {**settings, 'endpoints': [{**e, 'price_in': 1.0} if e['name'] == 'gamma-vision' else e for e in settings['endpoints']]}
        plain = self.frq.estimate(costed, 'cloud_standard', 10)['roles']['verifier']['cost']
        arbitrating = self.frq.estimate({**costed, 'final_arbitrator': True, 'final_arbitrator_rule': arbitration.RULE}, 'cloud_standard', 10)['roles']['verifier']['cost']
        self.assertGreater(arbitrating, plain)


del Pipeline  # Imported test cases are collected again by the loader; this file runs only its own.
