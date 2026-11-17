"""Current Cloud Standard routing over real retained tasks and a local fake host."""
import copy
import json

from grading import config, standard
from rosters import connect
from fake_openai import arbitrate_answer, flag_review_answer, grade_answer, images_of, payload_of, prompt_name
from grading import arbitration
from test_grading_cloud import endpoint
from test_grading_pipeline import Pipeline, CORRECT, RULES
from test_grading_teams import GradingTeams


class CloudStandard(Pipeline):
    current_standard = True
    legacy_workflow = False
    opinions = GradingTeams.opinions

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        s = saved['settings']
        s['endpoints'].append(endpoint(self.host, 'delta-vision'))
        s['tiers']['cloud_standard'].update(verifier='gamma-vision', flag_reviewer='delta-vision')
        self.frq.save_settings({'version': saved['version'], 'settings': s})

    def scripted(self, *, split=False, team=None, arbiter=None, flags=None, lines=CORRECT, review_reasons=None, **grader):
        self.seen = []
        def script(name, body):
            if name in ('arbitrate', 'flag_review'):
                payload = payload_of(body)
                self.seen.append((name, payload))
                return (arbiter or arbitrate_answer)(payload) if name == 'arbitrate' else (flags or flag_review_answer)(payload)
            if split or team:
                return self.opinions(**(team or {'alpha-vision': ('R2',), 'beta-text': ('R1',)}))(name, body)
            if name == 'primary_vision' and review_reasons:
                return {**grade_answer(payload_of(body), ('R1',)), 'review_reasons': review_reasons}
        return self.grade(lines, script=script, **grader)

    def test_clean_agreement_buys_neither_reviewer(self):
        response, names = self.scripted()
        self.assertEqual(names, ['reconcile', 'primary_vision', 'transcript_grade'])
        self.assertEqual(response['acceptance']['basis'], 'team_consensus')
        self.assertEqual(response['result']['signals']['scope']['cloud_standard']['rule'], standard.RULE)
        self.assertFalse(response['result']['signals']['escalation']['escalated'])

    def test_disagreement_adopts_a_whole_grade_and_skips_flag_review(self):
        response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, adopt='standard.second'))
        self.assertEqual(names, ['reconcile', 'primary_vision', 'transcript_grade', 'arbitrate'])
        self.assertEqual(self.proposal(response), ['R1'])
        self.assertEqual(response['acceptance']['basis'], 'final_arbitrator')
        self.assertEqual(set(self.seen[0][1]['assessments']), {'standard.primary', 'standard.second'})

    def test_equal_scores_with_different_rubric_selections_still_arbitrate(self):
        response, names = self.scripted(team={'alpha-vision': ('R1',), 'beta-text': ('R1', 'R4')},
            arbiter=lambda p: arbitrate_answer(p, adopt='standard.primary'))
        self.assertIn('arbitrate', names)
        self.assertNotIn('flag_review', names)
        self.assertTrue(response['result']['signals']['panels'][0]['decision']['pair']['same_score'])
        self.assertEqual(response['acceptance']['basis'], 'final_arbitrator')

    def test_disagreement_may_finalize_a_new_validated_grade(self):
        q = self.frq.assignment(self.plan['id'])['questions'][0]
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'Synthetic alternative credit',
            'rules': {**RULES, 'items': [*RULES['items'], {'id': 'R5', 'description': 'Correct algebra method.', 'points': 3}]}})
        response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, 'revise', chosen=('R5',)))
        self.assertEqual(self.proposal(response), ['R5'])
        self.assertEqual(response['result']['score']['score'], 3)
        self.assertEqual(response['acceptance']['basis'], 'final_arbitrator')
        self.assertNotIn('flag_review', names)

    def test_new_flag_on_arbitrator_revision_bypasses_fourth_model(self):
        response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, 'revise', chosen=('R3',)))
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['arbiter']['outcome'], 'unsettled')
        self.assertTrue(response['result']['signals']['arbiter']['open_flags'])
        self.assertNotIn('flag_review', names)

    def test_arbitrator_unreliable_bypasses_fourth_model(self):
        response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, 'unreliable', resolve=False))
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['arbiter']['outcome'], 'unreliable')
        self.assertNotIn('flag_review', names)

    def test_arbitrator_unresolved_flag_bypasses_fourth_model(self):
        response, names = self.scripted(split=True, arbiter=lambda p: arbitrate_answer(p, resolve=False))
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['arbiter']['outcome'], 'unsettled')
        self.assertNotIn('flag_review', names)

    def test_arbitrators_own_flag_cannot_be_cleared_by_its_other_rulings(self):
        def conflicted(p):
            return {**arbitrate_answer(p, adopt='standard.second'), 'review_reasons': [p['flags'][0]['text']]}
        response, names = self.scripted(split=True, arbiter=conflicted)
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['arbiter']['outcome'], 'unsettled')
        self.assertTrue(response['result']['signals']['arbiter']['review_required'])
        self.assertNotIn('flag_review', names)

    def test_arbitrator_failure_bypasses_fourth_model(self):
        response, names = self.scripted(split=True, arbiter=lambda p: ('status', 500, {}, {'error': 'offline'}))
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['arbiter']['outcome'], 'failed')
        self.assertNotIn('flag_review', names)
        # The batch completes, and its card says the arbitration failed and why (2026-09-24: a whole batch's rulings were refused unseen).
        with connect(self.path) as db:
            job_id = db.execute('SELECT job_id FROM frq_tasks WHERE response_id=?', (response['id'],)).fetchone()[0]
        failed = self.frq.job(job_id)['progress']['reviewers_failed']
        self.assertEqual((list(failed), failed['arbitrations']['responses']), (['arbitrations'], 1))
        self.assertIn('attempt cap', failed['arbitrations']['reasons'][0]['reason'])

    def test_other_flags_only_go_to_fourth_model_and_can_be_upheld(self):
        response, names = self.scripted(review_reasons=['Check whether the final digit is clear.'])
        self.assertNotIn('arbitrate', names)
        self.assertEqual(names[-1], 'flag_review')
        self.assertEqual(response['result']['signals']['flag_review']['outcome'], 'manual_review')
        self.assertIsNone(response['acceptance'])

    def test_fourth_model_can_finalize_only_the_unchanged_standing_grade(self):
        response, names = self.scripted(review_reasons=['Check whether the final digit is clear.'],
            flags=lambda p: flag_review_answer(p, 'finalize', True))
        self.assertEqual(names[-1], 'flag_review')
        self.assertNotIn('arbitrate', names)
        self.assertEqual(self.proposal(response), ['R1'])
        self.assertEqual(response['acceptance']['basis'], 'flag_review')
        self.assertEqual(response['result']['signals']['flag_review']['outcome'], 'finalized')
        self.assertNotIn('arbiter', response['result']['signals'])
        self.assertTrue(self.seen[0][1]['flags'])
        exported = json.loads(self.frq.export(self.plan['id'], 'audit')[0])['records'][0]['final']
        self.assertEqual(exported['basis'], 'flag_review')
        self.assertIn('unchanged standing grade', exported['comment'])

    def test_unresolved_fourth_model_flag_does_not_finalize(self):
        response, _ = self.scripted(review_reasons=['An unclear digit.'], flags=lambda p: flag_review_answer(p, 'finalize', False))
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['flag_review']['outcome'], 'unsettled')

    def test_fourth_model_failure_preserves_grade_for_manual_review(self):
        response, names = self.scripted(review_reasons=['An unclear digit.'], flags=lambda p: ('status', 500, {}, {'error': 'offline'}))
        self.assertIsNone(response['acceptance'])
        self.assertEqual(self.proposal(response), ['R1'])
        self.assertEqual(response['result']['signals']['flag_review']['outcome'], 'failed')
        self.assertNotIn('arbitrate', names)

    def test_fourth_model_cannot_change_ticks_even_if_provider_ignores_schema(self):
        response, _ = self.scripted(review_reasons=['An unclear digit.'],
            flags=lambda p: {**flag_review_answer(p, 'finalize', True), 'applications': []})
        self.assertIsNone(response['acceptance'])
        self.assertEqual(response['result']['signals']['flag_review']['outcome'], 'failed')
        self.assertEqual(self.proposal(response), ['R1'])

    def test_both_review_models_are_required_and_high_assurance_is_retired(self):
        for role in ('verifier', 'flag_reviewer', 'second'):
            s = copy.deepcopy(self.frq.settings()['settings'])
            s['tiers']['cloud_standard'][role] = ''
            with self.assertRaises(ValueError):
                standard.require_models(s)
        with self.assertRaisesRegex(ValueError, 'Cloud High Assurance was removed on 2026-09-23'):
            self.queue(mode='cloud_high_assurance')
        self.assertEqual(config.validate({'default_tier': 'cloud_high_assurance'})['default_tier'], 'cloud_standard')

    def test_frozen_models_reasoning_and_estimates_include_fourth_role(self):
        s = self.frq.settings()['settings']
        priced = self.frq.estimate(s, 'cloud_standard', 3)['roles']
        self.assertEqual(priced['flag_reviewer']['endpoint'], 'delta-vision')
        self.assertFalse(any(role.startswith('high_') for role in priced))
        job = self.queue()
        with connect(self.path) as db:
            profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
        self.assertEqual(profile['cloud_standard_rule'], standard.RULE)
        self.assertEqual(profile['tiers']['cloud_standard']['flag_reviewer'], 'delta-vision')
        self.assertTrue(profile['final_arbitrator'])
        self.assertFalse(profile['jev_tiebreak'] or profile['escalation_enabled'])
        with connect(self.path) as db:  # No canary runs before a batch since 2026-09-28.
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_canary_runs WHERE job_id=?', (job['id'],)).fetchone()[0], 0)


    # The prompt contracts of 2026-09-22 (tests/test_grading_prompt_contracts.py has the unit tests).
    def requests(self, name):
        return [r["body"] for r in self.host.requests if r["path"].endswith("/chat/completions") and prompt_name(r["body"]) == name]

    def test_the_primary_is_asked_under_its_vision_contract_with_what_graders_are_sent(self):
        response, names = self.scripted()
        self.assertEqual(names, ["reconcile", "primary_vision", "transcript_grade"])
        body, = self.requests("primary_vision")
        self.assertTrue(body["messages"][0]["content"].startswith("You are the PRIMARY GRADER"))
        self.assertTrue(images_of(body))
        self.assertIn("reading_issues", body["response_format"]["json_schema"]["schema"]["required"])
        sent = payload_of(body)
        self.assertEqual(sent["reading_context"]["independent_readings"], 1)
        self.assertTrue(all("original_source_text" not in item for item in sent["rubric"]["items"]))
        self.assertEqual(response["acceptance"]["basis"], "team_consensus")
        self.assertEqual(response["result"]["signals"]["scope"]["cloud_standard"]["primary_rule"], standard.PRIMARY_RULE)

    def test_a_reading_issue_the_primary_reports_holds_the_response_and_reaches_the_flag_reviewer(self):
        issue = "The final line may read x = 8 in the image."
        def script(name, body):
            if name == "primary_vision":
                payload = payload_of(body)
                last = payload["transcript"]["lines"][-1]["id"]
                return {**grade_answer(payload, ("R1",)), "reading_issues": [{"line_ids": [last], "kind": "transcription", "description": issue}]}
            if name == "flag_review":
                self.seen.append((name, payload_of(body)))
                return flag_review_answer(payload_of(body))
        self.seen = []
        response, names = self.grade(CORRECT, script=script)
        self.assertEqual(names[-1], "flag_review")
        self.assertIsNone(response["acceptance"])
        _, shown = self.seen[0]  # The script is consulted twice per request.
        self.assertIn(issue, [flag["text"] for flag in shown["flags"]])

    def test_the_flag_reviewer_is_given_the_key_and_what_faculty_compared(self):
        response, names = self.scripted(review_reasons=["Check whether the final digit is clear."])
        self.assertEqual(names[-1], "flag_review")
        _, shown = self.seen[0]  # The script is consulted twice per request.
        self.assertEqual(shown["answer_key"], [{"label": "x", "accept": ["x=6"]}])
        self.assertEqual(shown["answer_checks"][shown["standing"]]["result"], "match")
        self.assertEqual(shown["answer_checks"][shown["standing"]]["compared"], [{"part": "x", "text": "x = 6"}])

    def test_the_arbitrator_sees_one_disagreement_and_every_assessments_key_comparison(self):
        response, names = self.scripted(split=True)
        _, shown = self.seen[0]  # The script is consulted twice per request.
        about = [flag["text"] for flag in shown["flags"] if "disagree" in flag["text"] or "differ on" in flag["text"]]
        self.assertEqual(len(about), 1, about)
        self.assertTrue(about[0].startswith(arbitration.DISAGREE_ON))
        self.assertEqual(set(shown["answer_checks"]), {"standard.primary", "standard.second"})
        self.assertEqual(shown["answer_key"], [{"label": "x", "accept": ["x=6"]}])

    def test_the_reviewers_are_not_told_which_model_graded(self):
        """requirement, 2026-09-24: the arbitrator and the flag reviewer read each assessment by its label and what its grader could
        see, never which model made it; the model is still recorded on the grade."""
        response, names = self.scripted(split=True)
        _, shown = self.seen[0]
        for label, assessment in shown["assessments"].items():
            self.assertFalse(set(arbitration.IDENTITY) & set(assessment), label)
        self.assertEqual({label: a["input_mode"] for label, a in shown["assessments"].items()},
                         {"standard.primary": "images_and_transcript", "standard.second": "transcript_only"})
        sent = json.dumps(shown)
        for name in ("alpha-vision", "beta-text", "fixture/alpha", "fixture/beta"):
            self.assertNotIn(name, sent)
        self.assertEqual(response["result"]["signals"]["scope"]["cloud_standard"]["identity_rule"], standard.IDENTITY_RULE)
        self.assertIn('"endpoint": "alpha-vision"', json.dumps(response["result"]["signals"]["panels"]))  # Recorded, never sent.
        response, names = self.scripted(review_reasons=["Check whether the final digit is clear."])
        self.assertEqual(names[-1], "flag_review")
        _, shown = self.seen[0]
        self.assertFalse(any(set(arbitration.IDENTITY) & set(a) for a in shown["assessments"].values()))
        self.assertNotIn("alpha-vision", json.dumps(shown))

    def test_a_batch_frozen_before_the_rule_still_shows_the_reviewers_the_models(self):
        real = self.frq.create_job
        def frozen(aid, command):
            job = real(aid, command)
            with connect(self.path) as db:
                profile = json.loads(db.execute("SELECT profile_json FROM frq_jobs WHERE id=?", (job["id"],)).fetchone()[0])
                profile.pop("identity_rule")
                db.execute("UPDATE frq_jobs SET profile_json=? WHERE id=?", (json.dumps(profile), job["id"]))
            return job
        self.frq.create_job = frozen
        response, names = self.scripted(split=True)
        _, shown = self.seen[0]
        self.assertEqual((shown["assessments"]["standard.primary"]["endpoint"], shown["assessments"]["standard.second"]["model"]),
                         ("alpha-vision", "fixture/beta-text"))
        self.assertNotIn("identity_rule", response["result"]["signals"]["scope"]["cloud_standard"])


del Pipeline, GradingTeams
