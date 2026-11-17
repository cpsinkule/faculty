"""What holds a response back (requirement, 2026-09-21), after reading by hand all 13 disputed papers of a real 235-response batch.

`disagreement_and_work` keeps only the two checks that were measured to earn their reviews — the grading team did not agree, and
full credit was proposed where the rubric wants work and little is visible — and waives the rest. Every waived check still RUNS
and is RECORDED; only its power to block automatic finalization is removed. Two things are never waived: a selection that cannot
be scored at all, and the instructor's own confirmed rubric finalization, which is applied outside this policy.
"""
from grading import config, risk
from fake_openai import grade_answer, images_of, payload_of
import test_grading_teams as teams
from test_grading_pipeline import BARE, CORRECT, INCONSISTENT, Pipeline


class ReviewPolicy(Pipeline):
    legacy_workflow = False
    opinions, requests, doubtful = teams.GradingTeams.opinions, teams.GradingTeams.requests, teams.GradingTeams.doubtful

    def setUp(self):
        super().setUp()
        self.narrow(True)

    def narrow(self, on):
        saved = self.frq.settings()
        s = saved['settings']
        s['risk'] = {**s['risk'], 'review_policy': 'disagreement_and_work' if on else 'all_checks'}
        self.frq.save_settings({'version': saved['version'], 'settings': s})

    def test_the_policy_is_a_saved_choice_and_every_check_is_still_the_default(self):
        self.assertEqual(config.validate({})['risk']['review_policy'], 'all_checks')
        with self.assertRaises(ValueError):
            config.validate({'risk': {**config.validate({})['risk'], 'review_policy': 'whatever'}})
        # A batch frozen before the choice existed keeps every check.
        self.assertEqual(risk.review_policy({'risk': {}}), 'all_checks')
        self.assertEqual(risk.review_policy({'review_policy': 'disagreement_and_work'}), 'disagreement_and_work')

    def test_a_waived_check_still_runs_and_is_shown_but_no_longer_holds_the_response(self):
        """A doubt that flagged the response yesterday: recorded, visible, and no longer a block."""
        doubt = 'R2 does not say whether the setup must be written as an equation.'
        response, _ = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': self.doubtful(rubric_ambiguities=[doubt])}))
        result = response['result']
        self.assertEqual((result['state'], result['signals']['risk']['blocks'], result['reasons']), ('proposed', [], []))
        self.assertEqual((response['acceptance']['kind'], response['acceptance']['basis']), ('automatic', 'team_consensus'))
        # The check ran: what it found is on the record, under the policy that waived it.
        waived = result['signals']['risk']['waived']
        self.assertEqual(waived['policy'], 'disagreement_and_work')
        self.assertTrue(any('rubric’s wording unclear' in block for block in waived['blocks']), waived)
        self.assertEqual(result['signals']['risk']['policy'], risk.POLICY)
        # With every check, the same response waits for the instructor.
        self.narrow(False)
        again, _ = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': self.doubtful(rubric_ambiguities=[doubt])}))
        self.assertEqual((again['result']['state'], again['acceptance']), ('grading_review', None))
        self.assertNotIn('waived', again['result']['signals']['risk'])

    def test_the_two_checks_that_earn_their_reviews_still_hold_a_response(self):
        # 1. The grading team did not agree.
        split, _ = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': ('R2',), 'beta-text': ('R1',)}))
        result = split['result']
        self.assertEqual((result['state'], result['signals']['committee']['status'], split['acceptance']), ('grading_review', 'unresolved', None))
        self.assertTrue(result['signals']['risk']['blocks'] and result['signals']['risk']['individual'])
        # 2. Full credit where the rubric wants work and little is visible. It was the only flag on both papers read as
        #    genuinely over-awarded in batch 09d6eee3, and it fired on 3 of that batch's 235 responses.
        bare, _ = self.grade(BARE)
        result = bare['result']
        self.assertIn(risk.WORK_BLOCK, result['signals']['risk']['blocks'])
        self.assertIn(risk.WORK_BLOCK, result['reasons'])
        self.assertEqual((result['state'], bare['acceptance'], result['signals']['risk']['individual']), ('grading_review', None, True))

    def test_a_selection_that_cannot_be_scored_is_never_waived(self):
        """Not an opinion: there is no score to finalize, so it stays with the instructor whatever the policy says."""
        def unscorable(name, body):
            payload = payload_of(body)
            answer = grade_answer(payload, ('R1', 'R3'), image=bool(images_of(body)))  # Two lines the approved rules forbid together.
            return answer
        response, _ = self.grade(CORRECT, script=self.opinions(**{'alpha-vision': unscorable, 'beta-text': unscorable}))
        result = response['result']
        if result['score'] is None:
            self.assertIn(risk.UNSCORABLE, result['signals']['risk']['blocks'])
            self.assertEqual((result['state'], response['acceptance']), ('grading_review', None))
        else:  # The fixture rubric allows the pair; the policy is then judged by the checks above.
            self.assertTrue(result['signals']['risk'].get('waived'))

    def test_a_grade_is_never_finalized_on_the_primary_grader_alone(self):
        """The failure this must never reach: one grader's first pass finalized because no second assessment ever arrived.

        Since `third-vote-v3` no additional reviewer is bought for it, so the block has to stand on its own — under the
        narrow policy too, which waives most checks. A team is resolved only by a compared pair, so it cannot be.
        """
        block = 'No completed secondary assessment is available; review this response individually.'
        failed, names = self.grade(CORRECT, script=self.opinions(**{'beta-text': lambda *_: {'invalid': True}}))
        result = failed['result']
        self.assertEqual((result['state'], failed['acceptance']), ('grading_review', None))
        self.assertEqual((result['signals']['committee']['status'], result['signals']['committee']['third_required']), ('unresolved', False))
        self.assertIn(block, result['signals']['risk']['blocks'])
        self.assertIn(block, result['reasons'])
        self.assertTrue(result['signals']['risk']['individual'])
        self.assertTrue(result['signals']['panels'][0]['opinions']['second']['failed'])  # The failed attempt stays on the record.
        self.assertEqual([n for n in names if n in ('adjudicate', 'third_grade')], [])
        # The same when no second grader is configured at all: the primary grades, and the response still waits.
        saved = self.frq.settings()
        saved['settings']['tiers']['cloud_standard']['second'] = ''
        self.frq.save_settings(saved)
        self.host.requests.clear()
        alone, names = self.grade(CORRECT)
        self.assertEqual(names, ['reconcile', 'grade'])
        self.assertEqual((alone['result']['state'], alone['acceptance']), ('grading_review', None))
        self.assertIn(block, alone['result']['signals']['risk']['blocks'])
        self.assertEqual(alone['result']['signals']['risk']['waived']['policy'], 'disagreement_and_work')  # Other checks were waived; this one was not.

    def test_the_narrow_policy_is_named_in_the_acceptance_scope(self):
        response, _ = self.grade(CORRECT)
        self.assertEqual(response['result']['signals']['scope']['risk']['review_policy'], 'disagreement_and_work')
        self.narrow(False)
        plain, _ = self.grade(INCONSISTENT)
        # At the default the key is left out, so a batch that changed nothing keeps the exact scope it always had.
        self.assertNotIn('review_policy', plain['result']['signals']['scope']['risk'])

    def test_a_waived_response_is_finalized_only_once_the_rubric_finalization_allows_it(self):
        """The instructor's own confirmed policy is applied outside this one and is never waived by it."""
        import inspect
        source = inspect.getsource(risk.assess)
        self.assertIn('rubric_quality', inspect.getsource(type(self.frq).scored))
        self.assertNotIn('rubric_quality', source)  # The risk policy cannot reach it, in either direction.


del Pipeline  # Imported test cases are collected again by the loader; this file runs only its own.
