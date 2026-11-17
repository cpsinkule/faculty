"""SANDBOX (requirement, 2026-10-10): a memory for the Assistant, measured on invented cases. Synthetic only:
no request leaves the machine, no model is loaded, and no catalog is opened."""
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import manage_memory_sandbox as manage
from decisions_sandbox import fakes as hash_fakes
from decisions_sandbox import report as component_report
from decisions_sandbox import runner
from decisions_sandbox.pair import PairService, Store
from jev.service import encoded
from memory_sandbox import cases, components, embedders, embedding, fakes, pipeline, questions, report, runtime, safety, stories, sweep
from memory_sandbox import whole, jevmem

ROOT = Path(__file__).resolve().parents[1]
WORLD = cases.World()


def scripted(folder, world=WORLD, seen=None):
    """A Store answered by the oracle; `seen` collects every state it was asked about."""
    oracle = fakes.Oracle(world)

    def ask(state, asked, images=()):
        if seen is not None:
            seen.append(encoded(state))
        return oracle(state, asked, images)
    return Store(Path(folder), ask_jev=ask, ask_decisions=ask, ask_snap=ask, available=lambda mode: True)


class CaseSetTests(unittest.TestCase):
    def test_the_cases_are_consistent_and_every_class_is_large_enough(self):
        counts = cases.validate(WORLD)
        for category, least in cases.MINIMUM.items():
            self.assertGreaterEqual(counts[category], least, category)
        self.assertGreaterEqual(counts['queries'], 70)
        self.assertGreaterEqual(counts['distractors'], 150)
        self.assertGreaterEqual(sum(1 for q in WORLD.queries.values() if any(WORLD.events[i]['cat'] in cases.SAVED and
                                    WORLD.oracle_memories()[i]['status'] == 'superseded' for i in q['not'] if i in WORLD.oracle_memories())), 13)
        self.assertGreaterEqual(sum(1 for q in WORLD.queries.values() if not q['used']), 10)

    def test_a_family_is_wholly_fit_or_wholly_held_out(self):
        by_family = {}
        for event in WORLD.events.values():
            by_family.setdefault(event['family'], set()).add(event['split'])
        self.assertTrue(all(len(splits) == 1 for splits in by_family.values()))
        self.assertEqual(WORLD.events['U01']['family'], 'P07')
        self.assertEqual(WORLD.events['X01']['family'], 'P07')
        self.assertEqual({event['split'] for event in WORLD.events.values()}, {'fit', 'test'})

    def test_a_statement_precedes_what_restates_or_changes_it(self):
        place = {event['id']: index for index, event in enumerate(WORLD.timeline())}
        self.assertLess(place['P07'], place['U01'])
        self.assertLess(place['R04'], place['U02'])  # the restatement of the 120-word rule comes before its replacement
        self.assertLess(place['U01'], place['X01'])
        self.assertEqual(len(place), len(WORLD.events))

    def test_the_expected_store_replaces_and_never_holds_a_forbidden_class(self):
        memories = WORLD.oracle_memories()
        self.assertEqual(memories['P07']['status'], 'superseded')
        self.assertEqual(memories['P07']['superseded_by'], 'U01')
        self.assertEqual(memories['U01']['status'], 'current')
        self.assertEqual(memories['T06']['owner'], 't140s27')
        self.assertEqual(memories['C08']['owner'], 'c142')
        for ident in memories:
            self.assertNotIn(WORLD.events[ident]['cat'], ('student', 'quoted', 'pasted', 'one_off', 'record_fact', 'restate'))

    def test_a_contradicting_case_is_refused(self):
        world = cases.World()
        world.queries['Y02']['used'] = ['P07']  # replaced by U01
        with self.assertRaisesRegex(ValueError, 'replaced'):
            cases.validate(world)
        world = cases.World()
        world.queries['Y24']['used'] = ['T01']  # another term's fact
        with self.assertRaisesRegex(ValueError, 'another course or term'):
            cases.validate(world)


class NameCheckTests(unittest.TestCase):
    def test_a_uin_or_full_name_is_certain_and_a_single_name_is_not(self):
        roster = WORLD.roster
        self.assertEqual(pipeline.named_in_code('UIN 531004218 gets double time.', roster), 'strong')
        self.assertEqual(pipeline.named_in_code('Remember that petra rosen needs it.', roster), 'strong')
        self.assertEqual(pipeline.named_in_code('Grant extensions only with a note.', roster), 'weak')
        self.assertEqual(pipeline.named_in_code("Volkov's emails go to spam.", roster), 'weak')
        self.assertEqual(pipeline.named_in_code('Students may ask; I will answer; mark it; do not chase them.', roster), '')
        self.assertEqual(pipeline.named_in_code('Section 507 has 5310042189 seats.', roster), '')  # ten digits are not a UIN

    def test_the_strong_rule_leaves_a_single_name_to_the_model(self):
        p = {'names_student': .1}
        self.assertFalse(questions.named(p, questions.GATES, 'weak', 'strong'))
        self.assertTrue(questions.named(p, questions.GATES, 'weak', 'either'))
        self.assertTrue(questions.named(p, questions.GATES, 'strong', 'strong'))
        self.assertTrue(questions.named({'names_student': .9}, questions.GATES, '', 'strong'))
        self.assertFalse(questions.named({'names_student': .9}, questions.GATES, '', 'both'))


class DecisionTests(unittest.TestCase):
    def view(self, **changes):
        p = {'lasting': .97, 'one_off': .03, 'explicit': .5, 'own_words': .97, 'names_student': .03, 'record_fact': .03,
             'kind_confidence': .9, 'scope_confidence': .9, **changes}
        return {'status': 'complete', 'kind': 'preference', 'scope': 'workspace'}, p

    def test_lasting_comes_before_everything_else(self):
        self.assertEqual(questions.decide_capture(*self.view(), questions.GATES, ''), ('candidate', 'workspace'))
        self.assertEqual(questions.decide_capture(*self.view(lasting=.4, names_student=.99), questions.GATES, 'strong')[0], 'skip')
        self.assertEqual(questions.decide_capture(*self.view(own_words=.2), questions.GATES, '')[0], 'skip')
        unsure, p = self.view()
        unsure['kind'] = None  # the two orders disagree on preference or fact: a label, never a reason to drop a statement
        self.assertEqual(questions.decide_capture(unsure, p, questions.GATES, ''), ('candidate', 'workspace'))
        self.assertEqual(questions.kept_kind(unsure), 'preference')
        unsure['kind'] = 'none'
        self.assertEqual(questions.decide_capture(unsure, p, questions.GATES, '')[0], 'skip')
        self.assertEqual(questions.decide_capture(*self.view(names_student=.8), questions.GATES, '')[0], 'refuse_student')
        self.assertEqual(questions.decide_capture(*self.view(record_fact=.9), questions.GATES, '')[0], 'record')
        self.assertEqual(questions.decide_capture({'status': 'unavailable'}, {}, questions.GATES, '')[0], 'unavailable')

    def test_an_unsettled_scope_is_kept_at_the_narrowest_plausible_owner(self):
        outcome, p = self.view(scope_confidence=.2)
        self.assertEqual(questions.decide_capture(outcome, p, questions.GATES, ''), ('candidate', 'workspace'))
        outcome['kind'] = 'fact'
        self.assertEqual(questions.decide_capture(outcome, p, questions.GATES, ''), ('candidate', 'term'))
        outcome['scope'], p['scope_confidence'] = None, 0
        self.assertEqual(questions.decide_capture(outcome, p, questions.GATES, ''), ('candidate', 'term'))

    def test_an_exception_never_replaces_and_a_duplicate_wins_over_a_replacement(self):
        done = {'status': 'complete'}
        base = {'same_0': .1, 'replaces_0': .95, 'narrows_0': .1, 'same_1': .1, 'replaces_1': .1, 'narrows_1': .1}
        self.assertEqual(questions.decide_consolidate(done, base, 2, questions.GATES), ('supersede', 0))
        self.assertEqual(questions.decide_consolidate(done, {**base, 'narrows_0': .8}, 2, questions.GATES), ('save', None))
        self.assertEqual(questions.decide_consolidate(done, {**base, 'same_1': .9}, 2, questions.GATES), ('duplicate', 1))
        self.assertEqual(questions.decide_consolidate({'status': 'failed'}, {}, 2, questions.GATES), ('unavailable', None))
        self.assertEqual(questions.decide_consolidate(done, {}, 0, questions.GATES), ('save', None))

    def test_choices_that_decide_are_asked_in_both_orders(self):
        for name in ('kind', 'scope'):
            self.assertEqual(list(questions.CAPTURE[name]['criteria']), list(reversed(list(questions.CAPTURE[name + '__reversed']['criteria']))))
        self.assertIn('scope__reversed', questions.EPISODE)


class StoryTests(unittest.TestCase):
    """With scripted answers and vectors that put related texts together, the pipeline must leave exactly the expected store."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.seen = []
        cls.store = scripted(cls.temp.name, seen=cls.seen)
        cls.embedders = {'oracle': fakes.OracleEmbedder(WORLD), 'none': None}
        cls.line = stories.one(WORLD, cls.store, 'jev', {'arm': 'flat', 'embedder': 'oracle', 'variant': 'gold'}, cls.embedders)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_message_gets_its_expected_action_and_target(self):
        for result in self.line['writes']:
            expect = WORLD.events[result['event']]['expect']
            self.assertEqual(result['action'], expect['action'], result['event'])
            self.assertEqual(result['target'], expect['supersedes'] or expect['duplicate_of'], result['event'])
            if result['action'] in cases.KEPT:
                self.assertEqual(result['scope'], expect['scope'], result['event'])
            self.assertLessEqual(len(result['calls']), 2)

    def test_the_final_store_is_the_expected_one(self):
        self.assertEqual(self.line['final'], {ident: row['status'] for ident, row in WORLD.oracle_memories().items()})
        counts = report.write_counts(WORLD, self.line)
        self.assertEqual((counts['false_saves'], counts['student_kept'], counts['wrong_replacements'], counts['wrong_duplicates']), (0, 0, 0, 0))
        self.assertEqual((counts['lasting_kept'], counts['duplicates_right'], counts['replaced_right'], counts['exceptions_kept']),
                         (counts['lasting'], 20, 20, 6))
        self.assertEqual((counts['store_missing'], counts['store_extra'], counts['wordname_refused']), (0, 0, 0))

    def test_a_request_is_given_only_memories_it_should_use(self):
        for name, reads in self.line['reads'].items():
            for result in reads:
                query = WORLD.queries[result['query']]
                self.assertLessEqual(set(result['injected']), set(query['used']), (name, query['id']))
                self.assertLessEqual(len(result['injected']), 5)
                self.assertFalse(set(result['passed']) & set(query['not']), (name, query['id']))
                if not query['used']:
                    self.assertEqual(result['injected'], [])
                elif len(query['used']) <= 5:
                    self.assertEqual(set(result['injected']), set(query['used']), (name, query['id']))
        self.assertEqual(set(self.line['reads']), {'own', 'oracle-0', 'oracle-1000'})

    def test_the_revised_wording_asked_one_memory_at_a_time_reaches_the_same_store(self):
        with tempfile.TemporaryDirectory() as temp:
            seen = []
            line = stories.one(WORLD, scripted(temp, seen=seen), 'decisions', {'arm': 'flat', 'embedder': 'oracle', 'variant': 'gold'},
                               self.embedders, capture=2, single=True)
        self.assertEqual(line['final'], {ident: row['status'] for ident, row in WORLD.oracle_memories().items()})
        self.assertTrue(all(result['action'] == WORLD.events[result['event']]['expect']['action'] for result in line['writes']))
        asked = [json.loads(state) for state in seen]
        self.assertTrue(all('roster_matches' in state for state in asked if 'instructor_message' in state and 'new_statement' not in state))
        self.assertTrue(all('candidates' not in state for state in asked))  # one kept memory per request
        self.assertGreater(max(len(result['calls']) for result in line['writes']), 2)
        for result in line['reads']['own']:
            self.assertLessEqual(set(result['injected']), set(WORLD.queries[result['query']]['used']))

    def test_a_mixed_pipeline_sends_each_judgment_to_its_model(self):
        with tempfile.TemporaryDirectory() as temp:
            store = scripted(temp)
            line = stories.one(WORLD, store, 'mixed', {'arm': 'flat', 'embedder': 'oracle', 'variant': 'gold'}, self.embedders,
                               capture=2, single=[questions.CONSOLIDATE_PURPOSE])
            asked = {(entry['purpose'], entry['mode']) for entry in store.entries()}
        self.assertEqual(asked, {('memory-capture', 'jev'), ('memory-consolidate', 'decisions'), ('memory-relevance', 'jev')})
        self.assertEqual(line['final'], {ident: row['status'] for ident, row in WORLD.oracle_memories().items()})
        self.assertTrue(all(result['calls'] == 1 for result in line['reads']['own'] if result['candidates']))

    def test_the_request_is_told_only_the_roster_names_the_instructor_typed(self):
        self.assertEqual(pipeline.roster_matches('Grant extensions only with a note.', WORLD.roster), ['Grant'])
        self.assertEqual(pipeline.roster_matches("Volkov's emails go to spam; so do Petra Rosen's.", WORLD.roster), ['Petra', 'Petra Rosen', 'Rosen', 'Volkov'])
        self.assertEqual(pipeline.roster_matches('Students may ask; I will answer.', WORLD.roster), [])
        state, asked, rule = pipeline.capture_request(WORLD, next(e for e in WORLD.timeline() if e['id'] == 'W01'), 2)
        self.assertEqual((state['roster_matches'], rule), (['Grant'], 'sandbox-memory-capture-v2'))
        self.assertNotIn('unclear', asked['scope']['criteria'])
        self.assertNotIn('roster_matches', pipeline.capture_request(WORLD, WORLD.timeline()[0], 1)[0])

    def test_another_term_s_fact_is_never_visible(self):
        store = pipeline.oracle_store(WORLD)
        spring = {memory.id for memory in store.visible(cases.World.owners(WORLD.queries['Y24']))}
        self.assertIn('T06', spring)
        self.assertFalse({'T05', 'U16', 'T01', 'T02', 'C08'} & spring)

    def test_no_quoted_mail_scan_or_tool_text_is_ever_in_a_request(self):
        quoted = [item['text'] for event in WORLD.events.values() for item in event['untrusted']]
        self.assertGreaterEqual(len(quoted), 25)
        seen = list(self.seen)
        with tempfile.TemporaryDirectory() as temp:
            store = scripted(temp, seen=seen)
            runner.run(store, [case for build in components.BUILDERS.values() for case in build(WORLD)], Path(temp) / 'results-components.jsonl',
                       modes=('jev',), workers=4, say=lambda text: None)
        self.assertGreater(len(seen), 800)
        everything = '\n'.join(seen)
        for text in quoted:
            self.assertNotIn(json.dumps(text, ensure_ascii=False)[1:-1], everything)

    def test_what_is_written_out_holds_ids_and_numbers_only(self):
        written = json.dumps(self.line, ensure_ascii=False)
        for event in WORLD.events.values():
            self.assertNotIn(json.dumps(event['text'], ensure_ascii=False)[1:-1], written, event['id'])


class WholeStoreTests(unittest.TestCase):
    """Every kept memory given to one decision model at once (requirement, 2026-10-13), on scripted answers."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.query = WORLD.queries['Y01']

    def visible(self, pad):
        return sorted(pipeline.oracle_store(WORLD, 'gold', pad).visible(cases.World.owners(self.query)), key=lambda memory: memory.id)

    def test_the_memories_are_the_options_of_as_many_choices_as_the_store_needs_in_both_orders(self):
        for pad, choices in ((0, 1), (1000, 5)):
            memories = self.visible(pad)
            state, asked, names = whole.rank_request(WORLD, self.query, memories)
            self.assertEqual((len(names), len(asked)), (choices, 2 * choices))
            self.assertLessEqual(len(asked), 16)  # one Snap request
            self.assertEqual(sorted(ident for named in names for ident in named.values()), sorted(memory.id for memory in memories))
            for chunk, named in enumerate(names):
                first, second = asked[f'rank_{chunk}']['criteria'], asked[f'rank_{chunk}__reversed']['criteria']
                self.assertLessEqual(len(first), 255)
                self.assertEqual((list(first)[-1], list(second)[0]), (whole.NONE, whole.NONE))
                self.assertEqual(list(first), list(reversed(list(second))))
                self.assertEqual({first[name] for name in named}, {memory.text for memory in memories if memory.id in named.values()})
            self.assertEqual(set(state), {'request', 'where'})
            self.assertFalse(any(memory.id in encoded([state, list(asked)]) for memory in memories))

    def test_a_ranking_orders_by_the_mean_of_both_orders_and_gives_nothing_below_none_of_these(self):
        scores = {'a': .5, 'b': .3, 'c': .01}
        self.assertEqual(whole.ordered(scores), ['a', 'b', 'c'])
        self.assertEqual(whole.given(scores, .1, .02), ['a', 'b'])
        self.assertEqual(whole.given(scores, .4, .02), ['a'])
        self.assertEqual(whole.given(scores, .9, .02), [])
        service = PairService(scripted(self.folder), 'snap')
        found, none, complete = whole.rank(service, WORLD, self.query, self.visible(1000))
        self.assertTrue(complete)
        self.assertEqual(set(whole.ordered(found)[:len(self.query['used'])]), set(self.query['used']))
        nothing = next(query for query in WORLD.queries.values() if not query['used'])
        found, none, _ = whole.rank(service, WORLD, nothing, self.visible(0))
        self.assertGreater(none, max(found.values()))

    def test_every_arm_gives_only_what_a_request_should_use_and_the_report_holds_no_sentence(self):
        seen = []
        whole.run(WORLD, scripted(self.folder, seen=seen), self.folder / 'whole.jsonl', pads=(0,), say=lambda *_: None)
        lines = [json.loads(line) for line in (self.folder / 'whole.jsonl').read_text().splitlines()]
        self.assertEqual([line['arm'] for line in lines], ['rank alone', 'rank, then jev', 'rank, then snap', 'rank, then snap by text', 'each'])
        by_text = [state for state in seen if '"memories"' not in state and '"candidates"' not in state]
        self.assertTrue(by_text and all(set(json.loads(state)) == {'request', 'where'} for state in by_text))
        for line in lines:
            reads = whole.refloored(line['reads'], whole.fitted_floor(WORLD, line['reads'])) if line['arm'] == 'rank alone' else line['reads']
            counts = report.read_counts(WORLD, reads)
            self.assertEqual((counts['given_wrong'], counts['nothing_given'], counts['ranked_12']), (0, 0, counts['used']), line['arm'])
        written = whole.write(self.folder, WORLD).read_text() + (self.folder / 'whole.jsonl').read_text()
        self.assertIn('| snap: rank, then jev | oracle-0 |', written)
        for memory in pipeline.oracle_store(WORLD, 'gold', 0).rows.values():
            self.assertNotIn(memory.text, written)
        self.assertNotIn(self.query['text'], written)

    def test_a_ranking_that_failed_is_asked_again_only_under_a_retry_name(self):
        from decisions_sandbox import decisions_client
        oracle, sent = fakes.Oracle(WORLD), []

        def snap(state, asked, images=()):
            sent.append(1)
            if len(sent) == 1:
                raise decisions_client.DecisionError('Snap could not complete this check (HTTP 429: rate_limit_error).', status=429)
            return oracle(state, asked, images)
        store = Store(self.folder, ask_snap=snap, available=lambda mode: True)
        memories = self.visible(0)
        self.assertFalse(whole.rank(PairService(store, 'snap'), WORLD, self.query, memories)[2])
        self.assertFalse(whole.rank(PairService(store, 'snap'), WORLD, self.query, memories)[2])  # never resent by itself
        self.assertEqual(len(sent), 1)
        found, _, complete = whole.rank(PairService(store, 'snap'), WORLD, self.query, memories, retry='retry-1')
        self.assertTrue(complete and len(sent) == 2 and len(found) == len(memories))
        whole.rank(PairService(store, 'snap'), WORLD, self.query, memories, retry='retry-1')
        self.assertEqual(len(sent), 2)

    def test_the_budget_counts_each_request_a_long_snap_question_list_becomes(self):
        budget = manage.Budget(20)
        ask = budget.wrap(lambda state, asked, images: {}, per=16)
        ask('s', {f'use_{i}': {} for i in range(193)}, ())
        self.assertEqual(budget.sent, 13)
        budget.wrap(lambda state, asked, images: {})('s', {'a': {}, 'b': {}}, ())
        self.assertEqual(budget.sent, 14)

    def test_a_split_request_cannot_exceed_the_remaining_budget(self):
        budget = manage.Budget(2)
        sent = []
        ask = budget.wrap(lambda *args: sent.append(args), per=16)
        ask('s', {'first': {}}, ())
        with self.assertRaises(manage.BudgetExceeded):
            ask('s', {f'use_{i}': {} for i in range(17)}, ())
        self.assertEqual((budget.sent, len(sent)), (1, 1))

    def test_live_graph_runs_cannot_turn_a_small_budget_into_unlimited_children(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(jevmem, 'selftest', return_value=(0, 0)), \
                patch.object(jevmem.runtime, 'run_child') as child:
            with self.assertRaisesRegex(SystemExit, 'one request per selected configuration'):
                jevmem.run(folder, WORLD, live=True, budget=1, held={'TYPESAFE_API_KEY': True}, say=lambda *_: None)
            child.assert_not_called()


class OutageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.embedders = {'oracle': fakes.OracleEmbedder(WORLD), 'none': None}

    def test_when_the_decision_host_does_not_answer_nothing_is_kept_or_replaced(self):
        line = stories.one(WORLD, scripted(self.temp.name), 'jev', {'arm': 'flat-decision-down', 'embedder': 'oracle', 'variant': 'gold'}, self.embedders)
        self.assertEqual({result['action'] for result in line['writes']}, {'unavailable'})
        self.assertEqual(line['final'], {})
        for result in line['reads']['own']:
            self.assertEqual(result['fallback'], 'decision')
            self.assertLessEqual(len(result['injected']), 5)

    def test_a_consolidation_outage_keeps_nothing_and_replaces_nothing(self):
        class Half:
            """Answers capture, then fails: the host going down between the two requests of one message."""
            def __init__(self, inner):
                self.inner, self.used = inner, []

            def ask(self, purpose, *args, **kwargs):
                if purpose == questions.CONSOLIDATE_PURPOSE:
                    return {'status': 'uncertain', 'answers': {}}
                return self.inner.ask(purpose, *args, **kwargs)
        flat = pipeline.Flat(WORLD, self.embedders['oracle'])
        store = pipeline.oracle_store(WORLD)
        store.rows.pop('U01')
        store.rows['P07'].status = 'current'
        event = next(e for e in WORLD.timeline() if e['id'] == 'U01')
        result = flat.write(Half(PairService(scripted(self.temp.name), 'jev')), event, store)
        self.assertEqual(result['action'], 'unavailable')
        self.assertEqual(store.rows['P07'].status, 'current')
        self.assertNotIn('U01', store.rows)

    def test_when_the_embedder_does_not_answer_keywords_stand_in(self):
        line = stories.one(WORLD, scripted(self.temp.name), 'jev', {'arm': 'flat-embedder-down', 'embedder': 'oracle', 'variant': 'gold'}, self.embedders)
        counts = report.write_counts(WORLD, line)
        self.assertEqual((counts['false_saves'], counts['student_kept'], counts['unavailable']), (0, 0, 0))
        self.assertEqual(counts['lasting_kept'], counts['lasting'])
        self.assertTrue(all(result['fallback'] == 'embedder' for result in line['reads']['own'] if result['visible']))

    def test_today_keeps_only_what_was_explicitly_asked_and_pastes_all_of_it(self):
        line = stories.one(WORLD, scripted(self.temp.name), 'none', {'arm': 'today', 'embedder': '', 'variant': 'raw'}, self.embedders)
        kept = {result['event'] for result in line['writes'] if result['action'] == 'save'}
        self.assertEqual(kept, {event['id'] for event in WORLD.events.values() if event['explicit']})
        self.assertTrue(all(not result['calls'] for result in line['writes']))
        workspace = [query for query in line['reads']['own']]
        self.assertTrue(all(len(result['injected']) == len(kept) for result in workspace))


class SweepAndReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        store = scripted(cls.folder)
        built = [case for build in components.ALL.values() for case in build(WORLD)]
        runner.run(store, built, cls.folder / 'results-components.jsonl', modes=('jev', 'decisions'), workers=4, say=lambda text: None)
        cls.rows = [json.loads(line) for line in (cls.folder / 'results-components.jsonl').read_text().splitlines()]
        stories.run(WORLD, store, cls.folder / 'stories.jsonl', ('jev', 'decisions'), {'hash': embedders.HashEmbedder(), 'none': None},
                    variants=('gold',), workers=4, say=lambda text: None)
        (cls.folder / 'lessons-recall.json').write_text(json.dumps(embedding.lessons_recall(WORLD, {'hash': embedders.HashEmbedder()})))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_no_component_case_fails_and_labels_match_scripted_answers(self):
        self.assertEqual([(row['check'], row['id']) for row in self.rows for result in row['results'].values() if result.get('error')], [])
        self.assertEqual({row['check'] for row in self.rows}, {'memory_capture', 'memory_pair_gold', 'memory_pair_raw', 'memory_faithful',
                                                               'memory_relevance', 'memory_episode', 'memory_lessons', 'memory_capture_v2',
                                                               'memory_pair_gold_single', 'memory_pair_raw_single', 'memory_relevance_single'})
        for row in self.rows:
            for name, wanted in row['labels'].items():
                for mode, result in row['results'].items():
                    if name in result['p'] and type(wanted['value']) is int:
                        self.assertEqual(result['p'][name] >= .5, bool(wanted['value']), (row['check'], row['id'], name))
                    elif name in result['outcome']:
                        self.assertEqual(result['outcome'][name], wanted['value'], (row['check'], row['id'], name))

    def test_the_sweep_proposes_gates_with_no_forbidden_outcome(self):
        path = sweep.write(self.folder)
        found = json.loads((self.folder / 'calibration-memory.json').read_text())
        self.assertEqual(set(found), {'jev', 'decisions'})
        for mode in found:
            for split in ('fit', 'test'):
                capture = found[mode]['capture'][split]
                self.assertEqual((capture['false_saves'], capture['student_kept'], capture['wordname_refused']), (0, 0, 0))
                self.assertEqual(capture['lasting_kept'], capture['lasting'])
                pairs = found[mode]['pairs_gold'][split]
                self.assertEqual((pairs['wrong_replacements'], pairs['wrong_duplicates']), (0, 0))
                self.assertEqual((pairs['duplicates_right'], pairs['replaced_right']), (pairs['restatements'], pairs['updates']))
            self.assertEqual(found[mode]['capture']['student_policy'], 'strong')
        chosen = sweep.proposed(self.folder)
        self.assertEqual(set(chosen['jev']['gates']), {'lasting', 'own_words', 'student', 'scope', 'same', 'replaces', 'narrows', 'use'})
        revised = sweep.proposed(self.folder, capture=2, single=('decisions',))
        self.assertEqual(set(revised), {'jev', 'decisions', 'mixed'})
        self.assertEqual(revised['decisions']['student_policy'], 'strong')
        self.assertEqual(set(revised['mixed']['gates']), set(revised['jev']['gates']))
        self.assertIn('held out', path.read_text())

    def test_the_reports_are_written_and_hold_no_sentence(self):
        component_report.write(self.folder)
        path = report.write(self.folder, WORLD)
        text = path.read_text() + (self.folder / 'report.md').read_text() + (self.folder / 'stories.jsonl').read_text() \
            + (self.folder / 'results-components.jsonl').read_text()
        self.assertIn('What each arm kept', text)
        self.assertIn('Finding the right lesson', text)
        for event in WORLD.events.values():
            self.assertNotIn(json.dumps(event['text'], ensure_ascii=False)[1:-1], text, event['id'])
        for query in WORLD.queries.values():
            self.assertNotIn(json.dumps(query['text'], ensure_ascii=False)[1:-1], text, query['id'])
        summary = json.loads((self.folder / 'memory-summary.json').read_text())
        self.assertIn('today · raw', summary['writes'])
        self.assertTrue(any(name.endswith('oracle-1000') for name in summary['reads']))

    def test_a_run_without_the_none_embedder_has_no_flat_everything_arm_and_still_writes_its_lines(self):
        self.assertIn('flat-everything', [config['arm'] for config in stories.configs(['hash', 'none'])])
        self.assertNotIn('flat-everything', [config['arm'] for config in stories.configs(['hash'])])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'stories.jsonl'
            count = stories.run(WORLD, scripted(temp), path, ('jev',), {'hash': embedders.HashEmbedder()}, variants=('gold',), workers=2, say=lambda text: None)
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual((len(lines), count > 0), (count, True))
        self.assertNotIn('flat-everything', {line['arm'] for line in lines})

    def test_arbitrary_answers_never_break_the_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), ask_jev=hash_fakes.model('jev'), ask_decisions=hash_fakes.model('decisions'), available=lambda mode: True)
            line = stories.one(WORLD, store, 'jev', {'arm': 'flat', 'embedder': 'hash', 'variant': 'raw'}, {'hash': embedders.HashEmbedder()})
        self.assertEqual(len(line['writes']), len(WORLD.events))
        self.assertTrue(report.write_counts(WORLD, line)['messages'])


class EmbedderTests(unittest.TestCase):
    def test_the_cache_is_content_addressed_and_truncation_renormalises(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = embedders.VectorCache(Path(temp) / 'vectors.sqlite3')
            texts = ['Always put an answer key at the end.', 'Sign student emails plainly.']
            keys = [embedders.text_key('eg2', 'document', text) for text in texts]
            self.assertEqual(cache.missing(keys), keys)
            generator = np.random.default_rng(1)
            vectors = generator.standard_normal((2, 768)).astype(np.float32)
            cache.put('eg2', list(zip(keys, vectors)))
            cache.put('eg2', [(keys[0], np.zeros(768, dtype=np.float32))])  # a kept key is never changed
            self.assertEqual(cache.missing(keys), [])
            for name, dim in (('eg2-768', 768), ('eg2-256', 256)):
                found = embedders.Cached(name, cache).vectors(texts)
                self.assertEqual(found.shape, (2, dim))
                np.testing.assert_allclose(np.linalg.norm(found, axis=1), 1, rtol=1e-5)
            with self.assertRaisesRegex(KeyError, 'embed command'):
                embedders.Cached('eg2-768', cache).vectors(['never embedded'])
        self.assertNotEqual(embedders.text_key('eg2', 'query', 'x'), embedders.text_key('eg2', 'document', 'x'))
        self.assertNotEqual(embedders.text_key('eg2', 'query', 'x'), embedders.text_key('minilm', 'query', 'x'))

    def test_every_case_text_is_listed_for_embedding(self):
        wanted = embedding.texts(WORLD)
        self.assertIn(WORLD.events['P07']['say'], wanted['document'])
        self.assertIn(WORLD.events['P07']['text'], wanted['document'])
        self.assertIn(WORLD.queries['Y01']['text'], wanted['query'])
        self.assertIn(WORLD.padding(1000)[-1]['text'], wanted['document'])
        self.assertFalse({item['text'] for event in WORLD.events.values() for item in event['untrusted']} & set(wanted['document']))

    def test_a_down_embedder_raises_its_own_error(self):
        with self.assertRaises(embedders.EmbedderDown):
            embedders.Down().vectors(['x'])


class SafetyTests(unittest.TestCase):
    def test_run_folders_stay_under_the_sandbox_scratch_on_mnt(self):
        self.assertEqual(safety.SCRATCH.parts[:2], ('/', 'mnt'))
        self.assertEqual(safety.RUNTIME.parts[:2], ('/', 'mnt'))
        for name in ('../x', 'a/b', '', 'x' * 81):
            with self.assertRaises(ValueError):
                safety.run_folder(name)
        with tempfile.TemporaryDirectory() as temp, patch.object(safety, 'SCRATCH', Path(temp) / 'scratch'):
            if Path(temp).resolve().parts[:2] == ('/', 'mnt'):
                folder = safety.run_folder('run-1')
                self.assertEqual(folder.parent, (Path(temp) / 'scratch').resolve())
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        with patch.object(safety, 'SCRATCH', Path('/var/tmp/memory-sandbox')), self.assertRaisesRegex(ValueError, '/mnt'):
            safety.run_folder('run-1')

    def test_the_app_never_imports_the_sandbox_and_the_parent_never_imports_a_model(self):
        def imported(path):
            tree = ast.parse(path.read_text())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names.add(node.module.split('.')[0])
            return names
        self.assertNotIn('memory_sandbox', imported(ROOT / 'app.py'))
        self.assertNotIn('manage_memory_sandbox', imported(ROOT / 'app.py'))
        for path in sorted((ROOT / 'memory_sandbox').glob('*.py')) + [ROOT / 'manage_memory_sandbox.py']:
            self.assertFalse({'torch', 'sentence_transformers', 'transformers', 'jev_mem', 'typesafe_sdk', 'huggingface_hub', 'app'} & imported(path), path.name)

    def test_a_child_sees_no_home_no_checkout_no_catalog_and_no_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(safety, 'RUNTIME', Path(temp) / 'runtime'):
            (Path(temp) / 'runtime' / 'venv' / 'bin').mkdir(parents=True)
            (Path(temp) / 'runtime' / 'venv' / 'bin' / 'python').symlink_to('/usr/bin/python3')
            (Path(temp) / 'runtime' / 'hf').mkdir()
            argv = runtime.bwrap(['/venv/bin/python', '/children/embed_worker.py'], Path(temp) / 'work')
            live = runtime.bwrap(['x'], Path(temp) / 'work', network=True, environment={'TYPESAFE_API_KEY': 'k'})
        self.assertIn('--unshare-all', argv)
        self.assertNotIn('--share-net', argv)
        self.assertIn('--share-net', live)
        self.assertIn('--clearenv', argv)
        bound = [tuple(argv[i:i + 3]) for i, word in enumerate(argv) if word.startswith('--') and word.endswith(('bind', 'bind-try'))]
        children = ('--ro-bind', str(ROOT / 'memory_sandbox' / 'children'), '/children')
        self.assertIn(children, bound)
        for kind, source, target in bound:
            path = Path(source).resolve()
            if (kind, source, target) != children:  # those scripts are all a child sees of the checkout, wherever the checkout is
                self.assertFalse(source.startswith('/home'), source)
                self.assertFalse(path.is_relative_to(Path.home()) or path.is_relative_to(ROOT), source)
                self.assertNotIn('catalog', source)
            # Never the home folder, the checkout, the live catalog or a key file, nor a folder that holds one of them.
            self.assertFalse([str(kept) for kept in (Path.home(), ROOT, safety.base.LIVE_CATALOG, *safety.KEY_FILES) if kept.is_relative_to(path)], source)
        self.assertEqual([source for kind, source, _ in bound if not kind.startswith('--ro-')], [str(Path(temp) / 'work')])  # only its run folder is writable
        names = [argv[i + 1] for i, word in enumerate(argv) if word == '--setenv']
        self.assertFalse([name for name in names if name.endswith(('_KEY', '_TOKEN', '_SECRET'))])
        self.assertEqual(argv[argv.index('HF_HUB_OFFLINE') + 1], '1')
        self.assertIn('TYPESAFE_API_KEY', [live[i + 1] for i, word in enumerate(live) if word == '--setenv'])
        self.assertNotIn('OPENAI_API_KEY', live)

    def test_weights_that_do_not_match_the_lock_are_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(safety, 'RUNTIME', Path(temp)):
            with self.assertRaisesRegex(runtime.RuntimeMissing, 'not downloaded'):
                runtime.verify('minilm')
            folder = runtime.snapshot('minilm')
            folder.mkdir(parents=True)
            (folder / 'model.safetensors').write_bytes(b'not the model')
            with self.assertRaisesRegex(runtime.RuntimeMissing, 'do not match'):
                runtime.verify('minilm')
        lock = json.loads(runtime.LOCK.read_text())
        self.assertEqual({name: lock[name]['revision'] for name in lock}, {name: spec['revision'] for name, spec in embedders.MODELS.items()})

    def test_a_live_run_is_explicit_counted_and_never_mixed_with_scripted_answers(self):
        budget = manage.Budget(2)
        ask = budget.wrap(lambda state, asked, images: 'sent')
        self.assertEqual([ask(1, 2, 3), ask(1, 2, 3)], ['sent', 'sent'])
        with self.assertRaises(manage.BudgetExceeded):
            ask(1, 2, 3)
        self.assertFalse(issubclass(manage.BudgetExceeded, Exception))
        with tempfile.TemporaryDirectory() as temp:
            manage.kind_of(Path(temp), live=False)
            manage.kind_of(Path(temp), live=False)
            with self.assertRaisesRegex(SystemExit, 'scripted answers'):
                manage.kind_of(Path(temp), live=True)
        with self.assertRaisesRegex(SystemExit, '--run NAME'):
            manage.main(['components', '--live'])

    def test_a_request_stopped_by_the_budget_is_not_recorded_as_asked(self):
        with tempfile.TemporaryDirectory() as temp:
            budget = manage.Budget(1)
            oracle = fakes.Oracle(WORLD)
            store = Store(Path(temp), ask_jev=budget.wrap(oracle), ask_decisions=budget.wrap(oracle), available=lambda mode: True)
            flat = pipeline.Flat(WORLD, None)
            first, second = WORLD.timeline()[:2]
            flat.write(PairService(store, 'jev'), first, pipeline.Store())
            with self.assertRaises(manage.BudgetExceeded):
                flat.write(PairService(store, 'jev'), second, pipeline.Store())
            self.assertEqual(len((Path(temp) / 'calls.jsonl').read_text().splitlines()), 1)
            self.assertFalse(list((Path(temp) / 'pending-calls').glob('*.json')))
            budget.limit = 2
            flat.write(PairService(store, 'jev'), second, pipeline.Store())
            self.assertEqual(len((Path(temp) / 'calls.jsonl').read_text().splitlines()), 2)


class CommandTests(unittest.TestCase):
    def test_the_scripted_commands_run_end_to_end_in_their_own_scratch(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        if Path(temp.name).resolve().parts[:2] != ('/', 'mnt'):
            self.skipTest('checks run with scratch on /mnt (manage_checks.py)')
        with patch.object(safety, 'SCRATCH', Path(temp.name) / 'scratch'), patch.dict(os.environ, {}, clear=False), \
                patch('builtins.print'):
            self.assertEqual(manage.main(['cases']), 0)
            self.assertEqual(manage.main(['components', '--run', 'r', '--checks', 'capture,episodes', '--modes', 'jev']), 0)
            self.assertEqual(manage.main(['sweep', '--run', 'r']), 0)
            self.assertEqual(manage.main(['lessons', '--run', 'r']), 0)
            self.assertEqual(manage.main(['report', '--run', 'r']), 0)
            folder = Path(temp.name) / 'scratch' / 'r'
            self.assertEqual((folder / 'answers-from').read_text().strip(), 'scripted')
            self.assertTrue((folder / 'memory-report.md').exists())
            with self.assertRaisesRegex(SystemExit, 'scripted answers'):
                manage.main(['components', '--run', 'r', '--live', '--budget-calls', '5'])
            with self.assertRaisesRegex(SystemExit, 'budget-calls'):
                manage.main(['components', '--run', 'live-1', '--live'])


if __name__ == '__main__':
    unittest.main()
