"""Every version's rubrics drafted at once (requirement, 2026-09-25): one button, so the model writes each problem for all its
versions together, with the same lines and points and each version's own values; nothing is created until the instructor keeps it.

Synthetic template pages and a loopback model host."""
import json
import unittest
import uuid

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect
from fake_openai import ZERO_LINE, group_answer, images_of, payload_of, prompt_name, survey_answer, write_versions_answer
import test_grading_rubric_drafts as drafts_fixture


class RubricVersions(unittest.TestCase):
    managed = False
    Drafts = drafts_fixture.RubricDrafts
    document_version, ready_batch, reviewed_source = Drafts.document_version, Drafts.ready_batch, Drafts.reviewed_source
    upload, uploaded_questions, template = Drafts.upload, Drafts.uploaded_questions, Drafts.template
    configure, calls = Drafts.configure, Drafts.calls

    def setUp(self, versions=2):
        self.Drafts.setUp(self)
        if versions > 1:
            with connect(self.path) as db:
                db.execute('INSERT INTO frq_declared_versions VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), self.plan['id'], versions, 'declared', 'workspace', timestamp()))
        self.group, self.write = group_answer, write_versions_answer
        self.host.script = self.answer

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == 'rubric_survey':
            return survey_answer(('1', '2'))
        if name == 'rubric_group_versions':
            return self.group(payload)
        return self.write(payload)

    def slots(self):
        return self.frq.assignment_templates(self.plan['id'])['slots']

    def draft(self, **extra):
        command = {'all_versions': True, 'templates': [{'key': s['key'], 'template_id': self.blank['id']} for s in self.slots()],
                   'request_id': str(uuid.uuid4()), **extra}
        queued = self.frq.queue_rubric_generation(self.plan['id'], command)
        self.assertEqual((queued['status'], queued['kind'], queued['exam_version']), ('pending', 'versions', '*'))
        self.assertEqual(self.frq.queue_rubric_generation(self.plan['id'], command)['id'], queued['id'])  # The same request is the same draft.
        self.frq.process_cloud()
        return self.frq.rubric_generation(self.plan['id'], queued['id'])

    @staticmethod
    def keep(problem, keep=None, **changes):
        keep = keep or [m['version'] for m in problem['versions']]
        return {'draft': problem['id'], 'max_points': problem['points'], 'items': [{'points': l['points'], 'descriptions': dict(l['descriptions']),
                                                                                   **({'link': l['link']} if l.get('link') else {})} for l in problem['items']],
                'versions': [{'version': m['version'], 'keep': m['version'] in keep, 'printed_number': m['number'], 'title': m['title'],
                              'statement': m['statement'], 'solution': m['solution']} for m in problem['versions']], **changes}

    def test_one_request_per_problem_writes_every_version_with_the_same_lines(self):
        generation = self.draft()
        self.assertEqual((generation['status'], generation['kind'], generation['mode']), ('complete', 'versions', 'positive'))
        self.assertEqual([v['name'] for v in generation['versions']], ['Version A', 'Version B'])
        self.assertEqual(generation['counts'], {'drafted': 2, 'failed': 0})
        self.assertEqual(self.uploaded_questions(), [])  # Drafting creates nothing.
        # Each version surveyed as a drafted one is, one grouping request that sees every version, then one request per problem.
        requests = [r['body'] for r in self.host.requests]
        self.assertEqual(sorted(prompt_name(b) for b in requests), ['rubric_group_versions', 'rubric_survey', 'rubric_survey', 'rubric_write_versions', 'rubric_write_versions'])
        group = next(b for b in requests if prompt_name(b) == 'rubric_group_versions')
        self.assertTrue(group['messages'][0]['content'].startswith('You group the problems of the versions of ONE assignment'))
        self.assertIn('never from printed number or position alone', group['messages'][0]['content'])
        self.assertEqual([v['version'] for v in payload_of(group)['versions']], ['Version A', 'Version B'])
        self.assertEqual(images_of(group), [])
        members = group['response_format']['json_schema']['schema']['properties']['groups']['items']['properties']['members']['items']['properties']
        self.assertEqual(members['version'], {'enum': ['Version A', 'Version B']})
        writes = [b for b in requests if prompt_name(b) == 'rubric_write_versions']
        self.assertTrue(writes[0]['messages'][0]['content'].startswith('You draft the grading rubric for ONE problem of an assignment on EVERY version of it at once'))
        self.assertIn('Each version names ITS OWN correct label and content', writes[0]['messages'][0]['content'])  # multiple choice, pass/fail
        for body in writes:
            payload = payload_of(body)
            self.assertEqual([m['version'] for m in payload['problem']['versions']], ['Version A', 'Version B'])
            self.assertEqual(payload['mode'], 'positive')
            self.assertEqual([(p['version'], p['page']) for p in payload['pictures']], [('Version A', 1), ('Version B', 1)])
            self.assertEqual(len(images_of(body)), 2)
            schema = body['response_format']['json_schema']['schema']['properties']
            descriptions = schema['items']['items']['properties']['descriptions']
            self.assertEqual((descriptions['minItems'], descriptions['items']['properties']['version']), (2, {'enum': ['Version A', 'Version B']}))
            self.assertEqual((schema['items']['items']['properties']['points']['minimum'], schema['items']['items']['properties']['points']['maximum']), (0, 10))
        self.assertEqual({c['state'] for c in self.calls(generation['id'])}, {'completed'})
        first = generation['result']['problems'][0]
        self.assertEqual([(m['version'], m['number']) for m in first['versions']], [('Version A', '1'), ('Version B', '1')])
        self.assertEqual([l['points'] for l in first['items']], [10, 5, 5, 0])
        self.assertEqual(first['items'][0]['descriptions'], {'Version A': 'Correct answer with supporting work: $$x = 6$$',
                                                             'Version B': 'Correct answer with supporting work: $$x = 7$$'})
        self.assertEqual([m['solution'] for m in first['versions']], ['Solved: x = 6.', 'Solved: x = 7.'])
        self.assertEqual((first['warnings'], [m['warnings'] for m in first['versions']]), ([], [[], []]))
        # The blank templates the draft read become each version's default template, still to be confirmed.
        self.assertEqual({s['template_id'] for s in self.slots()}, {self.blank['id']})

        second = generation['result']['problems'][1]
        problem = self.keep(first)
        problem['items'][1]['points'] = 4  # One value for the line on every version.
        problem['items'][2]['points'] = 6
        problem['items'][1]['descriptions']['Version B'] = 'Adds 8 to both sides of $$3x - 8 = 13$$'
        command = {'request_id': str(uuid.uuid4()), 'questions': [problem, self.keep(second, keep=['Version A'])]}
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)
        self.assertEqual([(r['draft'], r['version'], r['status']) for r in result['results']],
                         [('P1', 'Version A', 'imported'), ('P1', 'Version B', 'imported'), ('P2', 'Version A', 'imported')])
        self.assertEqual(result['generation']['created'], ['P1:Version A', 'P1:Version B', 'P2:Version A'])
        again = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)
        self.assertEqual({r['status'] for r in again['results']}, {'already_imported'})
        questions = {(q['exam_version'], q['content']['printed_number']): q for q in self.uploaded_questions()}
        self.assertEqual(sorted(questions), [('A', '1'), ('A', '2'), ('B', '1')])
        a, b = questions['A', '1']['rubric'], questions['B', '1']['rubric']
        self.assertEqual([(i['id'], i['points']) for i in a['rules']['items']], [('R1', 10), ('R2', 4), ('R3', 6), ('R4', 0)])
        self.assertEqual([(i['id'], i['points']) for i in b['rules']['items']], [(i['id'], i['points']) for i in a['rules']['items']])
        self.assertEqual(b['rules']['items'][1]['description'], 'Adds 8 to both sides of $$3x - 8 = 13$$')
        self.assertEqual(a['rules']['items'][0]['description'], 'Correct answer with supporting work: $$x = 6$$')
        self.assertEqual((b['compiler']['name'], b['compiler']['family'], b['compiler']['version'], b['compiler']['edited']),
                         ('model-drafted-versions-v1', 'P1', 'Version B', True))
        # LINKED VERSIONS (2026-10-02): the writer marked each shared line; the review sent the marks back and the rubric keeps them.
        self.assertEqual([l['link'] for l in first['items']], ['same', 'same', 'related', 'same'])
        self.assertEqual(writes[0]['response_format']['json_schema']['schema']['properties']['items']['items']['properties']['link'], {'enum': ['same', 'related']})
        self.assertEqual(b['compiler']['line_kinds'], {'R1': 'same', 'R2': 'same', 'R3': 'related', 'R4': 'same'})
        links = self.frq.rubric_links(self.plan['id'])
        self.assertEqual(sorted((s['kind'], s['origin'], s['lines'][0]['item_id']) for s in links['sets']),
                         [('related', 'drafted', 'R3'), ('same', 'drafted', 'R1'), ('same', 'drafted', 'R2'), ('same', 'drafted', 'R4')])
        self.assertIsNone(b['approved_at'])
        self.assertIn('every version at once', b['rules']['warnings'][0])
        self.assertEqual(questions['B', '1']['question_key'], 'q1-B')
        self.assertEqual(questions['B', '1']['content']['worked_solution'], 'Solved: x = 7.')
        _, original = self.inbox.read_artifact(b['source_artifact_id'])
        self.assertIn('for every version at once', json.loads(original)['notes'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_every_writer_reads_the_courses_kept_memories(self):
        """GRADING "RUBRIC MEMORIES" (2026-10-14): each problem's writer is given the course's kept rules and watches; one only
        proposed goes nowhere, and a course without any sends exactly what it always sent."""
        from frq_fixtures import keep_memory
        with connect(self.path) as db:
            definition = db.execute("""SELECT d.definition_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id
                JOIN assignments d ON d.id=e.assignment_id WHERE a.id=?""", (self.plan['id'],)).fetchone()[0]
        keep_memory(self.path, definition)
        keep_memory(self.path, definition, 2, 'watch', 'Say which near-labels count.', waiting=True)
        self.draft()
        writes = [payload_of(r['body']) for r in self.host.requests if prompt_name(r['body']) in ('rubric_write', 'rubric_write_versions')]
        self.assertTrue(writes)
        self.assertEqual({json.dumps(w['standing_rules']) for w in writes},
                         {json.dumps([{'id': 'M1', 'kind': 'rule', 'rule': 'A value wrong only because of a mistake already charged is not charged again.',
                                       'covers': 'Any later line.', 'not_when': ''}])})
        prompts = [r['body']['messages'][0]['content'] for r in self.host.requests if prompt_name(r['body']) in ('rubric_write', 'rubric_write_versions')]
        self.assertTrue(all("THE INSTRUCTOR'S STANDING RULES" in text for text in prompts))

    def test_a_new_draft_can_replace_an_existing_questions_rubric_one_version_at_a_time(self):
        """requirement, 2026-09-27: a regenerated draft could not be used, since every question already existed. Each version's own
        tick gives that existing question the draft's rubric as a new revision; the question itself stays."""
        from grading.rubric_quality import origins
        first_draft = self.draft()
        problem = first_draft['result']['problems'][0]
        self.frq.accept_rubric_generation(self.plan['id'], first_draft['id'], {'request_id': str(uuid.uuid4()), 'questions': [self.keep(problem)]})
        before = {q['exam_version']: q for q in self.uploaded_questions()}
        target = problem['versions'][0]
        letter = next(v for v, q in before.items() if q['content']['printed_number'] == target['number'] and (not v or target['version'].endswith(v)))
        old = before[letter]
        self.frq.edit_rubric(self.plan['id'], old['question_id'], {'version': old['version'], 'rubric_id': old['rubric_id'], 'sha256': old['rubric']['sha256'],
                                                                   'comments': {'R1': 'Accept the answer boxed or underlined.'}})
        again = self.draft()
        fresh = again['result']['problems'][0]
        def replace(entry, **changes):
            chosen = self.keep(fresh)
            for v in chosen['versions']:
                v['keep'] = False  # (`keep=[]` would mean every version)
            chosen['items'][0]['descriptions'] = {m['version']: 'Complete, correct answer: $$x = 6$$' for m in fresh['versions']}
            for v in chosen['versions']:
                if v['version'] == target['version']:
                    v.update(replace=True, replaces=entry['rubric_id'], **changes)
            return chosen
        current = next(q for q in self.uploaded_questions() if q['question_id'] == old['question_id'])
        command = {'request_id': str(uuid.uuid4()), 'questions': [replace(current)]}
        result = self.frq.accept_rubric_generation(self.plan['id'], again['id'], command)
        self.assertEqual([(r['version'], r['status']) for r in result['results']], [(target['version'], 'replaced')])
        self.assertEqual(result['generation']['created'], [])  # A replaced rubric is no question this draft created.
        after = {q['exam_version']: q for q in self.uploaded_questions()}
        self.assertEqual(sorted(after), sorted(before))  # No question was added.
        rubric = after[letter]['rubric']
        self.assertEqual((rubric['id'], after[letter]['question_id']), (result['results'][0]['rubric_id'], old['question_id']))
        self.assertEqual(rubric['rules']['items'][0]['description'], 'Complete, correct answer: $$x = 6$$')
        self.assertEqual((rubric['compiler']['name'], rubric['compiler']['replaces'], rubric['compiler']['based_on']['family']),
                         ('draft-replaced-v1', current['rubric_id'], 'P1'))
        self.assertEqual(origins(rubric['compiler']), [['family', again['id'], 'P1']])  # The rubric check still knows the problem.
        self.assertEqual(after[letter]['content']['worked_solution'], old['content']['worked_solution'])  # Only the rubric changed.
        with connect(self.path) as db:
            self.assertEqual(self.frq.line_comments(db, self.plan['id'], old['question_id']), {})  # Written about the old lines.
            kinds = [r[0] for r in db.execute('SELECT kind FROM frq_events WHERE assignment_id=? ORDER BY rowid', (self.plan['id'],))]
        self.assertIn('rubric_draft_replaced', kinds)
        for other, q in before.items():
            if other != letter:
                self.assertEqual(after[other]['rubric_id'], q['rubric_id'])  # Its own box was not ticked.
        # The same request replaces nothing twice; the same lines again make no revision.
        self.assertEqual(self.frq.accept_rubric_generation(self.plan['id'], again['id'], command)['results'][0]['status'], 'already_replaced')
        now = next(q for q in self.uploaded_questions() if q['question_id'] == old['question_id'])
        self.assertEqual(self.frq.accept_rubric_generation(self.plan['id'], again['id'], {'request_id': str(uuid.uuid4()), 'questions': [replace(now)]})['results'][0]['status'], 'unchanged')
        self.assertEqual(next(q for q in self.uploaded_questions() if q['question_id'] == old['question_id'])['rubric_id'], now['rubric_id'])
        # Refused, each on its own: a rubric changed since the review opened, other points, both boxes, a question that does not exist.
        for entry, changes, words in ((current, {}, 'changed since you opened'), (now, {'keep': True}, 'not both')):
            refused = self.frq.accept_rubric_generation(self.plan['id'], again['id'], {'request_id': str(uuid.uuid4()), 'questions': [replace(entry, **changes)]})
            self.assertEqual(refused['results'][0]['status'], 'failed')
            self.assertIn(words, refused['results'][0]['error'])
        other_points = {**replace(now), 'max_points': fresh['points'] + 1}
        refused = self.frq.accept_rubric_generation(self.plan['id'], again['id'], {'request_id': str(uuid.uuid4()), 'questions': [other_points]})
        self.assertIn('set this draft to the same points', refused['results'][0]['error'])
        missing = self.keep(again['result']['problems'][1])
        for v in missing['versions']:
            v['keep'] = False
        missing['versions'][0].update(replace=True, replaces=now['rubric_id'])
        refused = self.frq.accept_rubric_generation(self.plan['id'], again['id'], {'request_id': str(uuid.uuid4()), 'questions': [missing]})
        self.assertIn('no rubric to replace', refused['results'][0]['error'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_the_drafts_clarifications_become_the_created_lines_comments_and_never_the_rubric(self):
        """2026-09-28: the drafter writes a clarification per line and version beside the wording Gradescope shows. The instructor edits
        it in the review; accepting saves it as that line's comment, the channel the rubric review carries into the clarifications the
        instructor confirms. The rubric itself never holds one, and a line left out of a version takes no comment there."""
        aid = self.plan['id']
        generation = self.draft()
        problem = generation['result']['problems'][0]
        names = [m['version'] for m in problem['versions']]
        self.assertEqual(problem['items'][-1]['clarifications'], {name: ZERO_LINE for name in names})
        chosen = self.keep(problem)
        for mine, line in zip(chosen['items'], problem['items']):
            mine['clarifications'] = dict(line['clarifications'])
        chosen['items'][2]['clarifications'][names[0]] = 'Check it in the original equation.'  # edited in the review
        for other in names[1:]:
            chosen['items'][1]['descriptions'][other] = ''  # left out of the other versions
        result = self.frq.accept_rubric_generation(aid, generation['id'], {'request_id': str(uuid.uuid4()), 'questions': [chosen]})
        made = {r['version']: r for r in result['results']}
        self.assertEqual({name: made[name]['clarifications_saved'] for name in names}, {name: 3 if name == names[0] else 1 for name in names})
        with connect(self.path) as db:
            comments = {name: self.frq.line_comments(db, aid, made[name]['question_id']).get(made[name]['question_id'], {}) for name in names}
        self.assertEqual(comments[names[0]], {'R2': 'Any equivalent form of x = 6 counts.', 'R3': 'Check it in the original equation.', 'R4': ZERO_LINE})
        for other in names[1:]:
            self.assertEqual(comments[other], {'R4': ZERO_LINE})
        for q in self.uploaded_questions():
            self.assertTrue(all(set(item) >= {'id', 'description', 'points'} and 'clarification' not in item for item in q['rubric']['rules']['items']))
            self.assertNotIn('clarification', q['rubric'].get('source_text') or '')
        # A replacement clears the old lines' comments and gives the new lines the new draft's clarifications, in one transaction.
        again = self.draft()
        fresh = again['result']['problems'][0]
        swap = self.keep(fresh)
        for mine, line in zip(swap['items'], fresh['items']):
            mine['clarifications'] = dict(line['clarifications'])
        swap['items'][0]['descriptions'] = {name: 'Complete, correct answer: $$x = 6$$' for name in names}
        current = next(q for q in self.uploaded_questions() if q['question_id'] == made[names[0]]['question_id'])
        for v in swap['versions']:
            v['keep'] = False
            if v['version'] == names[0]:
                v.update(replace=True, replaces=current['rubric_id'])
        replaced = self.frq.accept_rubric_generation(aid, again['id'], {'request_id': str(uuid.uuid4()), 'questions': [swap]})['results'][0]
        self.assertEqual((replaced['status'], replaced['clarifications_saved']), ('replaced', 2))
        with connect(self.path) as db:
            self.assertEqual(self.frq.line_comments(db, aid, current['question_id']).get(current['question_id']),
                             {'R2': 'Any equivalent form of x = 6 counts.', 'R4': ZERO_LINE})

    def written_rows(self):
        with connect(self.path) as db:
            return {table: [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in (
                'frq_events', 'frq_rubric_review_ready', 'frq_rubric_generations', 'frq_rubric_quality_checks', 'frq_rubric_line_comments', 'frq_rubric_revisions')}

    def confirmed_by(self):
        with connect(self.path) as db:
            return [(r['question_id'], r['actor']) for r in db.execute('SELECT question_id,actor FROM frq_rubric_review_ready WHERE assignment_id=? ORDER BY rowid', (self.plan['id'],))]

    def test_accepting_readies_each_version_it_created_and_never_a_rubric_it_replaced(self):
        """ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): accepting is the confirmation for the rubric review of every
        version's question it creates, made after the drafted clarifications became their comments. A draft given to an existing
        question in place of its rubric is asked again, as after any rubric change, and reopens that question alone."""
        aid = self.plan['id']
        generation = self.draft()
        problem = generation['result']['problems'][0]
        chosen = self.keep(problem)
        for mine, line in zip(chosen['items'], problem['items']):
            mine['clarifications'] = dict(line['clarifications'])
        command = {'request_id': 'accept-1', 'questions': [chosen]}
        result = self.frq.accept_rubric_generation(aid, generation['id'], command)
        made = {r['version']: r['question_id'] for r in result['results']}
        self.assertEqual(({r['status'] for r in result['results']}, result['readied']), ({'imported'}, list(made.values())))
        self.assertEqual(self.confirmed_by(), [(qid, 'draft-acceptance') for qid in made.values()])
        readiness = self.frq.rubric_quality(aid)['readiness']
        self.assertEqual({qid: (readiness[qid]['ready'], sorted(readiness[qid]['comments'])) for qid in made.values()}, {qid: (True, ['R2', 'R4']) for qid in made.values()})
        # Each rubric names its own answer, so its key is the rubric's proposal and none is to be read; the check says what it waits for.
        self.assertEqual((result['keys_to_read'], result['review']),
                         ([], 'The rubric check starts by itself once the answer areas are confirmed and the answer keys are saved.'))
        before = self.written_rows()
        again = self.frq.accept_rubric_generation(aid, generation['id'], command)
        self.assertEqual(({r['status'] for r in again['results']}, again['readied'], again['keys_to_read']), ({'already_imported'}, [], []))
        self.assertEqual(self.written_rows(), before)  # The same request again writes nothing more.
        redrafted = self.draft()
        fresh = redrafted['result']['problems'][0]
        name = fresh['versions'][0]['version']
        swap = self.keep(fresh)
        swap['items'][0]['descriptions'] = {m['version']: 'Complete, correct answer: $$x = 6$$' for m in fresh['versions']}
        current = next(q for q in self.uploaded_questions() if q['question_id'] == made[name])
        for v in swap['versions']:
            v['keep'] = False
            if v['version'] == name:
                v.update(replace=True, replaces=current['rubric_id'])
        replaced = self.frq.accept_rubric_generation(aid, redrafted['id'], {'request_id': str(uuid.uuid4()), 'questions': [swap]})
        self.assertEqual(([r['status'] for r in replaced['results']], sorted(replaced)), (['replaced'], ['generation', 'results']))
        self.assertEqual({qid: state['ready'] for qid, state in self.frq.rubric_quality(aid)['readiness'].items()}, {qid: qid != made[name] for qid in made.values()})
        self.assertEqual(self.confirmed_by(), [(qid, 'draft-acceptance') for qid in made.values()])  # Nothing was confirmed for the new rubric.
        self.assertEqual(self.frq.request_rubric_review_when_ready(aid),
                         'The rubric check starts by itself once every rubric is confirmed for review, the answer areas are confirmed and the answer keys are saved.'
                         if len(made) > 1 else 'The rubric check of these rubrics is started in Key & Rubric Finalization.')

    def test_each_created_versions_key_is_read_from_its_own_blank_once_its_answer_area_is_confirmed(self):
        """A deducting rubric names no key (measured 2026-10-11: 0 of 16 and 0 of 15 questions). Nothing is read at acceptance; once
        a version's answer area is confirmed its key is read without being asked, as the button reads it, from that version's
        confirmed blank, once. It is saved only by the instructor, and the check waits for the readings the last area asked for."""
        from test_grading_locations import confirm_templates
        aid = self.plan['id']
        self.reader.number = '1'
        generation = self.draft(mode='negative')
        command = {'request_id': 'accept-1', 'questions': [self.keep(generation['result']['problems'][0])]}
        result = self.frq.accept_rubric_generation(aid, generation['id'], command)
        made = [r['question_id'] for r in result['results']]
        def readings():
            with connect(self.path) as db:
                return {json.loads(r['input_json'])['question_id']: (r['request_id'].split(':')[0], r['template_id'], r['exam_version'], r['document_id'], r['status'])
                        for r in db.execute("SELECT * FROM frq_rubric_generations WHERE assignment_id=? AND json_extract(input_json,'$.kind')='key'", (aid,))}
        self.assertEqual((result['readied'], result['keys_to_read'], readings()), (made, made, {}))
        self.assertEqual(result['review'], 'The rubric check starts by itself once the answer areas are confirmed and the answer keys, each read once its answer area is confirmed, are saved.')
        before = self.written_rows()
        self.assertEqual(self.frq.accept_rubric_generation(aid, generation['id'], command)['keys_to_read'], made)
        self.assertEqual(self.written_rows(), before)
        confirm_templates(self.frq, aid)
        for _ in range(80):
            self.frq.process_location_one()
            questions = self.uploaded_questions()
            if all(q['location_check']['status'] == 'needs_confirmation' for q in questions):
                break
        else:
            self.fail('The answer areas were not read')
        confirmed = self.frq.confirm_all_locations(aid, {'confirm_locations': True, 'questions': [
            {'question_id': q['question_id'], 'version': q['version'], 'check_id': q['location_check']['id'], 'input_sha256': q['location_check']['input_sha256']}
            for q in questions]})
        questions = {q['question_id']: q for q in confirmed['questions']}
        self.assertEqual(readings(), {qid: ('area-confirmed', self.blank['id'], questions[qid]['exam_version'], questions[qid]['assignment_version_id'], 'pending') for qid in made})
        self.assertTrue(all(q['content']['answer_key'] == [] and q['answer']['read_key']['status'] == 'pending' for q in questions.values()))
        self.assertEqual((confirmed['rubric_quality']['state'], confirmed['rubric_review_waits']), ('needed', 'The rubric check starts by itself once the answer keys are read and saved.'))
        self.assertEqual([g['kind'] for g in self.frq.assignment_templates(aid)['generations']], ['versions'])  # A reading is no draft.

    def test_a_line_cleared_for_one_version_leaves_the_others_their_ids(self):
        generation = self.draft(mode='negative')
        self.assertEqual(generation['mode'], 'negative')
        first = generation['result']['problems'][0]
        problem = self.keep(first)
        problem['items'][1]['descriptions']['Version B'] = ''
        self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()), 'questions': [problem]})
        rubrics = {q['exam_version']: q['rubric']['rules'] for q in self.uploaded_questions()}
        self.assertEqual([i['id'] for i in rubrics['A']['items']], ['R1', 'R2', 'R3'])
        self.assertEqual([i['id'] for i in rubrics['B']['items']], ['R1', 'R3'])
        self.assertEqual({r['mode'] for r in rubrics.values()}, {'negative'})

    def test_answers_that_break_the_shared_lines_are_asked_again_then_reported(self):
        def missing(payload):
            answer = write_versions_answer(payload)
            answer['items'][1]['descriptions'] = answer['items'][1]['descriptions'][:1]  # A line without Version B's wording.
            return answer
        self.write = missing
        generation = self.draft()
        self.assertEqual(generation['status'], 'failed')
        self.assertIn('No problem could be drafted', generation['error'])
        self.assertEqual(len([c for c in self.calls(generation['id']) if c['step'] == 'rubric_draft:versions:problem:P1']), 2)
        # A grouping that leaves a problem out is refused the same way, and nothing is written.
        self.write = write_versions_answer
        self.group = lambda payload: {'groups': group_answer(payload)['groups'][:1], 'notes': []}
        generation = self.draft()
        self.assertEqual(generation['status'], 'failed')
        self.assertIn('left out', generation['error'])
        self.assertEqual(len([c for c in self.calls(generation['id']) if c['step'] == 'rubric_draft:versions:group']), 2)
        self.assertEqual(self.uploaded_questions(), [])

    def test_one_draft_of_every_version_at_a_time(self):
        slots = self.slots()
        with self.assertRaisesRegex(ValueError, 'blank template for Version B'):
            self.frq.queue_rubric_generation(self.plan['id'], {'all_versions': True, 'request_id': str(uuid.uuid4()),
                                                               'templates': [{'key': slots[0]['key'], 'template_id': self.blank['id']}]})
        together = self.frq.queue_rubric_generation(self.plan['id'], {'all_versions': True, 'request_id': str(uuid.uuid4()),
            'templates': [{'key': s['key'], 'template_id': self.blank['id']} for s in slots]})
        with self.assertRaisesRegex(Conflict, 'already being drafted'):
            self.frq.queue_rubric_generation(self.plan['id'], {'key': slots[1]['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        with self.assertRaisesRegex(Conflict, 'already being drafted'):
            self.frq.queue_rubric_generation(self.plan['id'], {'all_versions': True, 'request_id': str(uuid.uuid4()),
                'templates': [{'key': s['key'], 'template_id': self.blank['id']} for s in slots]})
        self.assertEqual(self.frq.cancel_rubric_generation(self.plan['id'], together['id'])['status'], 'cancelled')
        single = self.frq.queue_rubric_generation(self.plan['id'], {'key': slots[1]['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        with self.assertRaisesRegex(Conflict, 'already being drafted'):
            self.frq.queue_rubric_generation(self.plan['id'], {'all_versions': True, 'request_id': str(uuid.uuid4()),
                'templates': [{'key': s['key'], 'template_id': self.blank['id']} for s in slots]})
        self.frq.cancel_rubric_generation(self.plan['id'], single['id'])


class OneVersion(RubricVersions):
    """An assignment without versions uses the same button: its one template is surveyed and each problem written, with no grouping."""
    def setUp(self):
        super().setUp(versions=1)

    def test_one_request_per_problem_writes_every_version_with_the_same_lines(self):
        generation = self.draft()
        self.assertEqual((generation['status'], generation['counts']), ('complete', {'drafted': 2, 'failed': 0}))
        self.assertEqual(sorted(prompt_name(r['body']) for r in self.host.requests), ['rubric_survey', 'rubric_write_versions', 'rubric_write_versions'])
        first = generation['result']['problems'][0]
        name = first['versions'][0]['version']
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()), 'questions': [self.keep(first)]})
        self.assertEqual([r['status'] for r in result['results']], ['imported'])
        [question] = self.uploaded_questions()
        self.assertEqual((question['exam_version'], question['question_key'], question['rubric']['compiler']['version']), ('', 'q1', name))

    test_a_line_cleared_for_one_version_leaves_the_others_their_ids = None
    test_answers_that_break_the_shared_lines_are_asked_again_then_reported = None
    test_one_draft_of_every_version_at_a_time = None


if __name__ == '__main__':
    unittest.main()
