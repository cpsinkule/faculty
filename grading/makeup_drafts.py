"""Makeup versions drafted by a model (requirement, 2026-10-03; GRADING "MAKEUP VERSIONS"): for students whose excused absence covers the
day an assignment was given (ABSENCES "MAKEUPS"), a model writes ONE more version of that very assignment, parallel to a version
already given, and adapts that version's rubrics to it line by line.

Asked, the instructor chose (2026-10-03): the makeup is a new VERSION of the same assignment (the next letter, `frq_added_versions`),
graded in the same plan and counted in the same gradebook column; the model writes PARALLEL problems (the same skills, structure,
points and steps, new numbers and contexts); each problem's rubric is the original's adapted line by line (the same line IDs, points
and rules, `rubric_generation.adapted_rubric` / `adapted_rules`); the instructor approves it or asks for changes.

A makeup draft IS a quiz draft (`quiz_drafts`, course-wide authoring tied to one term) whose request names the grading plan it is for
(`input_json.makeup`): the same immutable revisions, revise/cancel/scrap, model-call receipts (`frq_cloud_calls.quiz_revision_id`), the
same compile in the document sandbox (`_quiz_build`, the instructor's quiz layout, nothing printed naming the version), the same check
of the printed pages and fix rounds, and the same reading of each problem's answer key once, with the worked solution in view
(`_quiz_keys`, 2026-10-11, "Carry the key through"), saved with the questions by an approval sent from the window that showed the
keys (`save_keys`). Only the writer's request (`makeup_write`: the original version's problems and rubrics, every version's
statements to avoid), the rubrics (adapted, not drafted) and the approval differ. No table was added.

APPROVING a makeup (a `quiz_draft_approvals` row whose rule is MAKEUP_RULE) is carried out by the worker step by step, each step reusing
what an earlier attempt made: the next version letter added to the plan, the printed version registered on the assignment, one
question per problem pinned in the EXISTING plan (every other question, pin and grade untouched), the makeup's template imported and,
when every other version's template is already confirmed, confirmed with its answer areas from the LaTeX layout, each makeup rubric
confirmed for the rubric review (the review itself and the Key & Rubric Finalization stay the instructor's), one unnamed print job
for the term attached to a makeup sitting of the exam record, and the students the instructor ticked placed on that sitting through the
Exams page's own paths. A certified plan is approved only with the instructor's explicit `certified_ok` (adding a version brings it
back as Certify again; no grade changes). Nothing regrades a paper. Once the version is settled up to grading (`makeup_settled`), the
Gradescope sender adds it to every section's linked set by itself (2026-10-09, GRADESCOPE "A VERSION THE SET LACKS"); nothing else
is sent anywhere."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect
from seating import term_label
from document_identity import quiz_latex

from . import progress as plan_progress
from .cloud import RetryLater, later
from .drafting_tools import definition_of
from .models import digest, encoded, identifier, text, uid
from .providers.base import ProviderError
from .quiz_drafts import (FIX, FIX_ROUNDS, PICTURES, PICTURE_BYTES, RUBRIC, WRITE, after_fix, asked_changes, check_record, fix_request, compiled_from, key_warnings, resolved,
                          signature, statement_text, written)
from .rubric_generation import adapted_rubric, adapted_rules, scoring_mode
from .rubric_quality import PARALLEL

MAKEUP_RULE = 'makeup-draft-v1'
MAKEUP_COMPILER = 'model-drafted-makeup-v1'
MAKEUP_GENERATOR = 'faculty-makeup-draft'
MAX_INSTRUCTIONS = 8000
MAX_STUDENTS = 200
AVOID_TEXT = 4000  # characters kept of each statement the makeup must not reuse
MAKEUP_BY = ('Drafted by a model as makeup Version {letter}, parallel to {source}, its lines adapted from {source}\'s rubric line by line '
             '(the same IDs, points and rules), and approved by the instructor with the makeup. Check the lines in Key & Rubric Finalization '
             'before grading.')


def structure(quiz, references, original):
    """The makeup is the original's problems again, or ValueError (asked once more): as many problems, each worth the same, with the
    same lettered parts and points where the original's own fragments say what they were."""
    if len(quiz['problems']) != len(references):
        raise ValueError(f"Write exactly {len(references)} problems, one for each problem of the original, in the same order.")
    for problem, reference in zip(quiz['problems'], references):
        expected = reference['problem']['points']
        if expected is not None and abs(problem['points'] - expected) > 1e-9:
            raise ValueError(f"Problem {problem['number']} is worth {problem['points']:g} points; the original's problem {reference['problem']['number']} "
                             f"is worth {expected:g}. Keep every problem's points.")
    if original:
        for problem, source in zip(quiz['problems'], original.get('problems') or []):
            if problem['kind'] != source.get('kind'):
                raise ValueError(f"Problem {problem['number']} must be {str(source.get('kind', '')).replace('_', ' ')}, as the original's is.")
            want = [(part.get('label'), part.get('points')) for part in source.get('parts') or []]
            have = [(part['label'], part['points']) for part in problem['parts']]
            if want and want != have:
                raise ValueError(f"Problem {problem['number']} must have the original's parts {', '.join(f'({l}) {p:g} pts' for l, p in want)}.")


def makeup_rules(reference, rubric, points, letter, source):
    """The rules a makeup problem's question is created with: its original's CURRENT rules (`reference`, that question as the plan has
    it) with the adapted wording, line for line (`adapted_rules`). The approval (`_carry_out_makeup`) and the answer-key reading made
    while the makeup is drafted (`run_makeup_revision`) both build them here, so the key is read against the lines the question gets."""
    inherited = [w for w in reference['rubric']['rules']['warnings'] if not w.startswith(('Drafted by a model', 'Adapted by a model'))]
    warnings = [MAKEUP_BY.format(letter=letter, source=source), *inherited]
    accepted = [{'source': line['id'], 'description': line['description'], 'points': line['points']} for line in rubric['items']]
    return adapted_rules(reference['rubric']['rules'], accepted, points, reference['content']['max_points'], warnings)


class MakeupDraftsMixin:
    # Reading ----------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _makeup_rows(db, aid):
        return db.execute("""SELECT * FROM quiz_drafts WHERE json_extract(input_json,'$.makeup.plan_id')=?
            ORDER BY created_at DESC,rowid DESC""", (aid,)).fetchall()

    @staticmethod
    def makeups_waiting(db):
        """{plan: {letter, state}} of the makeup drafts waiting on the instructor (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE
        APPROVED"): state 'ready' to approve, or 'failed' with nothing to approve; `_quiz_draft_summary`'s states, read in one query since
        Home's 30 s read asks too. One open makeup draft per plan. Catalog only."""
        found = {}
        for row in db.execute("""SELECT json_extract(d.input_json,'$.makeup.plan_id') plan_id,json_extract(d.input_json,'$.makeup.letter') letter,
                EXISTS(SELECT 1 FROM quiz_draft_revisions r WHERE r.draft_id=d.id AND r.status IN ('pending','running')) live,
                EXISTS(SELECT 1 FROM quiz_draft_revisions r WHERE r.draft_id=d.id AND r.status='complete' AND NOT r.cancelled) complete
                FROM quiz_drafts d WHERE json_extract(d.input_json,'$.makeup') IS NOT NULL
                AND d.id NOT IN (SELECT draft_id FROM quiz_draft_scraps) AND d.id NOT IN (SELECT draft_id FROM quiz_draft_approvals WHERE status<>'failed')
                ORDER BY d.created_at DESC,d.rowid DESC"""):
            if not row['live']:
                found.setdefault(row['plan_id'], {'letter': row['letter'], 'state': 'ready' if row['complete'] else 'failed'})
        return found

    def approved_makeup_versions(self, db):
        """[(plan_id, letter)] of the current terms' makeup versions whose approval the worker has carried out, oldest first."""
        return [(r[0], r[1]) for r in db.execute("""SELECT DISTINCT json_extract(d.input_json,'$.makeup.plan_id'),json_extract(d.input_json,'$.makeup.letter')
            FROM quiz_drafts d JOIN quiz_draft_approvals a ON a.draft_id=d.id JOIN frq_assignments f ON f.id=json_extract(d.input_json,'$.makeup.plan_id')
            JOIN exam_events e ON e.id=f.event_id JOIN course_terms t ON t.id=e.term_id
            WHERE a.status='complete' AND json_extract(d.input_json,'$.makeup') IS NOT NULL AND t.is_current=1 AND t.deleted_at IS NULL
            ORDER BY a.created_at""") if r[0] and r[1]]

    def makeup_settled(self, db, aid, letter):
        """Whether makeup Version `letter` is settled up to the point grading begins (requirement, 2026-10-09; GRADESCOPE "A VERSION THE SET
        LACKS"): every question of that version passes what a grading batch requires of it (an approved rubric, answer areas confirmed
        for that rubric, a rubric finalization that stands for it and leaves nothing open; `_auto_grade_command`). Catalog only."""
        questions = [self.question(db, aid, r[0]) for r in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=?", (aid,))]
        mine = [q for q in questions if (q["exam_version"] or "") == letter]
        if not mine:
            return False
        standing = self.rubric_quality_status(db, aid).get("standing") or {}
        for q in mine:
            entry = standing.get(q["question_id"])
            if not q["rubric"] or not q["rubric"].get("approved_at") or not entry or entry["rubric_id"] != q["rubric_id"] or entry.get("open"):
                return False
            if self.location_status(db, aid, q)["status"] != "confirmed":
                return False
        return True

    def _makeup_sources(self, db, aid):
        """Each version of the plan whose rubrics a makeup can be based on: its slot and its current questions (`reference_problems`)."""
        found = []
        for slot in self.template_setup(db, aid)['slots']:
            references = self.reference_problems(db, aid, slot)
            if references:
                found.append((slot, references))
        return found

    def _makeup_letter(self, db, aid, plan):
        """The plan's next version letter, or ValueError saying why it has none."""
        letters = sorted(set(self.plan_versions(db, aid)) | set(self.version_letters(db, plan)))
        if not letters:
            raise ValueError('A makeup is added as one more lettered version, and this assignment has no lettered versions (A, B …).')
        if max(letters) >= 'Z':
            raise ValueError('This assignment already has 26 versions.')
        return chr(ord(max(letters)) + 1)

    def makeup_options(self, aid):
        """What Draft makeup version offers for this plan: the versions to base it on, the letter it will be, whether the plan is
        certified, the drafting model, and the makeup drafts made for it (newest first)."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            sources = [{'key': slot['key'], 'label': slot['label'], 'problems': len(references),
                        'points': sum(r['problem']['points'] or 0 for r in references)} for slot, references in self._makeup_sources(db, aid)]
            try:
                letter, problem = self._makeup_letter(db, aid, plan), ''
            except ValueError as error:
                letter, problem = None, str(error)
            if not sources and not problem:
                problem = 'No version of this assignment has rubrics with printed question numbers yet. Set up its rubrics first.'
            certified = plan_progress.latest_certification(db, aid) is not None
            drafts = [self._quiz_draft_summary(db, row) for row in self._makeup_rows(db, aid)]
        try:
            model, model_problem = self._drafting_model()[0]['name'], ''
        except ValueError as error:
            model, model_problem = None, str(error)
        return {'plan_id': aid, 'title': plan['title'], 'event_id': plan['event_id'], 'sources': sources, 'letter': letter, 'problem': problem,
                'certified': certified, 'model': model, 'model_problem': model_problem, 'drafts': drafts}

    # Request ----------------------------------------------------------------------------------------------------------------------
    def create_makeup_draft(self, aid, data):
        """A makeup draft for this plan and its first revision: one more version, parallel to the version `source` (a template slot key;
        default the first with rubrics), with the instructor's `instructions` for the model. One open makeup draft per plan at a time,
        so the letter it was drafted as is still the next one when it is approved."""
        request_id = identifier(data.get('request_id'))
        instructions = text(data.get('instructions', ''), 'Instructions', MAX_INSTRUCTIONS).strip()
        endpoint, settings = self._quiz_writing_model()
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            term = db.execute('SELECT t.*,d.code FROM course_terms t JOIN course_definitions d ON d.id=t.definition_id WHERE t.id=? AND t.deleted_at IS NULL',
                              (plan['term_id'],)).fetchone()
            if not term:
                raise ValueError("This assignment's term is no longer kept.")
            definition_id = definition_of(db, plan['source_assignment_id'])
            sources = self._makeup_sources(db, aid)
            if not sources:
                raise ValueError('No version of this assignment has rubrics with printed question numbers yet. Set up its rubrics first.')
            wanted = data.get('source') or None
            chosen = next(((slot, refs) for slot, refs in sources if slot['key'] == wanted), None) if wanted else sources[0]
            if not chosen:
                raise ValueError('Choose the version the makeup is based on.')
            slot, references = chosen
            letter = self._makeup_letter(db, aid, plan)
            modes = {r['problem']['rubric']['mode'] for r in references}
            if len(modes) != 1:
                raise ValueError(f"{slot['label']}'s rubrics mix positive and negative scoring; a makeup adapts one scoring.")
            prior = db.execute('SELECT * FROM quiz_drafts WHERE request_id=?', (request_id,)).fetchone()
            if not prior:
                for row in self._makeup_rows(db, aid):
                    if db.execute('SELECT 1 FROM quiz_draft_scraps WHERE draft_id=?', (row['id'],)).fetchone():
                        continue
                    if not db.execute("SELECT 1 FROM quiz_draft_approvals WHERE draft_id=? AND status!='failed'", (row['id'],)).fetchone():
                        raise Conflict('A makeup draft of this assignment is still open. Approve it or scrap it before drafting another.')
            avoid = [{'version': s['label'], 'number': r['problem']['number'], 'statement': (r['problem']['statement'] or '')[:AVOID_TEXT]}
                     for s, refs in sources for r in refs if r['problem']['statement']]
            # Drafted in Faculty: the original version exactly as it was written (and its title line), for the makeup to keep its shape.
            original, title_line = None, f"{term['code']} {term_label(term)} {plan['title']}"
            approval = db.execute("""SELECT a.revision_id,d.input_json FROM quiz_draft_approvals a JOIN quiz_drafts d ON d.id=a.draft_id
                WHERE a.status='complete' AND json_extract(a.result_json,'$.plan_id')=? AND json_extract(d.input_json,'$.makeup') IS NULL
                ORDER BY a.created_at DESC LIMIT 1""", (aid,)).fetchone()
            if approval:
                revision = db.execute('SELECT result_json FROM quiz_draft_revisions WHERE id=?', (approval['revision_id'],)).fetchone()
                quiz = json.loads(revision['result_json'])['quiz'] if revision and revision['result_json'] else None
                if quiz and slot['exam_version'] in quiz['versions']:
                    shape = written(quiz)
                    for problem in shape['problems']:
                        problem['versions'] = [v for v in problem['versions'] if v['version'] == slot['exam_version']]
                    original = shape
                    title_line = json.loads(approval['input_json']).get('title_line') or title_line
            template = db.execute('SELECT id,label,page_count,manifest_sha256 FROM assignment_templates WHERE id=?', (slot['template_id'],)).fetchone() \
                if slot.get('template_id') else None
            examples = [{'template_id': template['id'], 'label': template['label'], 'assignment_title': plan['title'],
                         'pages': min(template['page_count'], 4), 'manifest_sha256': template['manifest_sha256']}] if template else []
            context = {'rule': MAKEUP_RULE, 'course': term['code'], 'term': term_label(term), 'term_id': term['id'], 'title': plan['title'],
                       'title_line': title_line, 'versions': [letter], 'total': sum(r['problem']['points'] or 0 for r in references),
                       'mode': next(iter(modes)), 'format': '', 'instructions': instructions, 'examples': examples, 'material': [],
                       'makeup': {'plan_id': aid, 'event_id': plan['event_id'], 'letter': letter,
                                  'source': {k: slot[k] for k in ('key', 'label', 'document_id', 'exam_version')},
                                  'references': references, 'avoid': avoid, 'original_quiz': original}}
            if not context['total']:
                raise ValueError(f"{slot['label']}'s questions carry no points.")
            checksum = digest([definition_id, context])
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another draft.')
                return self.quiz_draft(prior['id'])
            db.execute('BEGIN IMMEDIATE')
            draft_id, now = uid(), timestamp()
            db.execute('INSERT INTO quiz_drafts VALUES(?,?,?,?,?,?)', (draft_id, definition_id, request_id, checksum, encoded(context), now))
            self._record_quiz_revision(db, draft_id, 1, None, f'{request_id}:1', {'rule': MAKEUP_RULE, 'mode': context['mode'], 'feedback': ''}, endpoint, settings)
            self.event(db, aid, 'makeup_draft_created', {'draft_id': draft_id, 'letter': letter, 'source': slot['label'], 'model': endpoint['name']})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    # Worker: one revision ---------------------------------------------------------------------------------------------------------
    def _makeup_write(self, task, endpoint, settings, step, payload, images, draft_context, tools=None):
        """One makeup writer request (`_quiz_write`'s contract, the makeup's prompt and its structure check)."""
        names, previous, makeup = draft_context['versions'], payload.get('previous'), draft_context['makeup']

        def check(output, metadata):
            compiled = compiled_from(metadata, previous, names) if isinstance(output, dict) and output.get('base') == 'compiled' else None
            quiz = quiz_latex.clean(resolved(output, previous, compiled, names), names, title_line=draft_context['title_line'], total=draft_context['total'])
            structure(quiz, makeup['references'], makeup.get('original_quiz'))
            return quiz, {'notes': text(output.get('notes', ''), 'Notes', 4000).strip(), 'changes': text(output.get('changes', ''), 'Changes', 4000).strip(),
                          'rubric_feedback': [text(p.get('rubric_feedback', '') or '', 'Rubric feedback', 4000).strip() for p in quiz['problems']]}
        return self._quiz_ask(task, endpoint, settings, step, 'makeup_write', payload, images, check, faults_key='faults',
                              tools=tools(previous) if tools else None)

    def run_makeup_revision(self, task):
        context, profile = json.loads(task['input_json']), json.loads(task['profile_json'])
        endpoint, settings = profile['endpoint'], profile['settings']
        mode = scoring_mode(context['mode'])
        status, result, metadata, error, after = 'complete', None, None, '', ''
        try:
            with self.rosters.lock(), connect(self.path) as db:
                draft = self._quiz_draft(db, task['draft_id'])
                previous = db.execute('SELECT * FROM quiz_draft_revisions WHERE id=?', (task['based_on'],)).fetchone() if task['based_on'] else None
                asked = asked_changes(db, task)
                draft_context = json.loads(draft['input_json'])
                makeup = draft_context['makeup']
                # CURRENT RUBRICS (requirement, 2026-11-01: Quiz 6's Version E was refused twice because Version A's Question 2 rubric
                # changed after drafting, and revision 2 adapted the draft's saved copy again): each revision reads the original's
                # questions and rubrics as they are now, so a changed rubric is adapted from its new lines and approving accepts it.
                # The saved copy stays only when the original's questions themselves changed (the makeup no longer pairs with them).
                current = self.reference_problems(db, makeup['plan_id'], makeup['source'])
                if [r['question_id'] for r in current] == [r['question_id'] for r in makeup['references']]:
                    makeup['references'] = current
            names = draft_context['versions']
            letter = names[0]
            request = {**{k: draft_context[k] for k in ('course', 'term', 'title', 'total', 'versions')}, 'instructions': draft_context['instructions']}
            example, example_pictures = self._quiz_examples(draft_context)
            images, shown = [], []
            for source, page, picture in example_pictures:
                if len(images) >= PICTURES or sum(map(len, images)) + len(picture) > PICTURE_BYTES:
                    continue
                shown.append({'picture': len(images), 'file': source, 'page': page})
                images.append(picture)
            payload = {'request': request, 'original': [r['problem'] for r in makeup['references']], 'avoid': makeup['avoid'],
                       'example': example, 'pictures': shown}
            if makeup.get('original_quiz'):
                payload['original_quiz'] = makeup['original_quiz']
            earlier = json.loads(previous['result_json']) if previous else None
            if earlier:
                payload.update(previous=written(earlier['quiz']), feedback=context['feedback'])
            tools = self._quiz_writer_tools(draft, draft_context, endpoint)
            (quiz, extra), write_receipt = self._makeup_write(task, endpoint, settings, WRITE, payload, images, draft_context, tools)
            quiz, extra, built, faults = self._quiz_fixed(task, endpoint, settings, '', quiz, extra, [], draft, draft_context, request, tools,
                                                          writer=self._makeup_write, asked=asked)
            if faults:
                raise ValueError('The makeup could not be laid out after ' + str(FIX_ROUNDS) + ' rounds of fixes: ' + ' '.join(faults[:4]))
            pages = self._quiz_pages(built)
            # One look at the printed pages beside the original's, as a quiz draft has; what it finds goes back to the writer once. A
            # problem the revision this one is based on already showed a check exactly as it prints now is not asked about again
            # (`_quiz_checked`, 2026-10-11).
            check, shown = self._quiz_checked(task, endpoint, settings, request, quiz, built, pages, example_pictures, earlier, asked)
            fixed_after_check = False
            if check['issues']:
                payload_fix = fix_request(request, quiz, [f"Problem {i['problem']}: {i['issue']} Fix: {i['fix']}" if i['problem']
                                                          else f"{i['issue']} Fix: {i['fix']}" for i in check['issues']], asked)
                try:
                    (candidate, fixed), _ = self._makeup_write(task, endpoint, settings, FIX + 'check', payload_fix, [], draft_context, tools)
                    candidate_extra = {**extra, 'notes': fixed['notes'] or extra['notes'], 'changes': after_fix(extra, fixed)}
                    candidate, candidate_extra, candidate_built, candidate_faults = self._quiz_fixed(
                        task, endpoint, settings, 'check:', candidate, candidate_extra, [], draft, draft_context, request, tools, writer=self._makeup_write, asked=asked)
                    if not candidate_faults:
                        quiz, extra, built, fixed_after_check = candidate, candidate_extra, candidate_built, True
                        pages = self._quiz_pages(built)
                except ValueError:
                    pass  # the check's findings are shown to the instructor instead
            rubrics_ = self._makeup_rubrics(task, endpoint, settings, quiz, extra, pages, mode, earlier, makeup, letter)
            waits = [r['wait'] for r in rubrics_ if r.get('wait')]
            if waits:
                raise RetryLater(min(waits), 'A rubric request is waiting for its host.')
            # Each problem's answer key, read once with the worked solution in view, as a quiz draft's is (`_quiz_keys`, instructor,
            # 2026-10-11, "Carry the key through"), against the lines its question will get: the original's CURRENT rules with the
            # adapted wording. An original whose rubric changed since the makeup was requested gives no key (its approval is refused).
            with self.rosters.lock(), connect(self.path) as db:
                originals = {}
                for r in rubrics_:
                    try:
                        question = self.question(db, makeup['plan_id'], r['source']['question_id'])
                    except NotFound:
                        question = None
                    originals[r['problem']] = question if question and question['rubric'] and question['rubric_id'] == r['source']['rubric_id'] else None

            def known(entry, problem, version):
                reference = originals[problem['number']]
                if not reference:
                    raise ValueError(f"{entry['source']['label']}'s rubric for {entry['source']['title']} changed after the makeup was requested")
                return (reference['content']['title'] or f"Question {problem['number']}",
                        makeup_rules(reference, entry['rubric'], problem['points'], version, makeup['source']['label']),
                        problem['content'][version]['solution'] or entry['rubric'].get('solution', ''))
            rubrics_ = self._quiz_keys(task, endpoint, settings, quiz, pages, rubrics_, names, known)
            result = self._publish_quiz_files(draft, task, built, pages)
            warnings = [f'Version {letter}: {note}' for note in built[letter]['layout']['notes']]
            if check['issues'] and not fixed_after_check:
                warnings += [f"Check{', problem ' + str(i['problem']) if i['problem'] else ''}: {i['issue']}" for i in check['issues']]
            for r in rubrics_:
                warnings += [f"Problem {r['problem']} rubric: {w}" for w in (r.get('rubric') or {}).get('warnings', [])]
                if (r.get('rubric') or {}).get('notes'):
                    warnings.append(f"Problem {r['problem']} rubric: {r['rubric']['notes']}")
                if r.get('error'):
                    warnings.append(f"Problem {r['problem']}: its rubric could not be adapted ({r['error']}). Ask for changes to adapt it again.")
            warnings += key_warnings(quiz, rubrics_, names)
            result.update(rule=MAKEUP_RULE, mode=mode, quiz=quiz, notes=extra['notes'], changes=extra['changes'] if earlier else '',
                          check=check_record(check, fixed_after_check, shown, self._quiz_printed(request, quiz, built, example_pictures) if fixed_after_check else shown),
                          rubrics=[{k: v for k, v in r.items() if k != 'wait'} for r in rubrics_], warnings=warnings,
                          toolchain={letter: built[letter]['details']}, makeup={'letter': letter, 'source': makeup['source']['label']})
            if not any(r.get('rubric') for r in rubrics_):
                raise ValueError('No rubric could be adapted for this makeup. ' + ' '.join(r.get('error', '') for r in rubrics_[:2]))
            metadata = {'write': {'metadata': write_receipt['metadata']}}
        except RetryLater as waiting:
            status, after = 'pending', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound) as failure:
            status, error = 'failed', f'The makeup could not be drafted: {failure}'
        self._publish_quiz_revision(task, status, result, metadata, error, after, makeup=True)

    def _makeup_rubrics(self, task, endpoint, settings, quiz, extra, pages, mode, earlier, makeup, letter):
        """Each makeup problem's rubric: the original problem's CURRENT rubric adapted to it line by line (`rubric_adapt`, validated by
        `adapted_rubric`: every line once by its ID, the points the original's). The pair is known by construction (problem n is the
        original's n-th), so no pairing is asked. A problem unchanged since the revision it is based on, with nothing asked about its
        grading and the same original rubric, keeps that revision's adaptation."""
        source = makeup['source']['label']
        carried = {(r['signature'], r['source']['rubric_id']): r for r in (earlier or {}).get('rubrics', []) if r.get('rubric') and r.get('signature')}
        writer = {**endpoint, 'max_tokens': max(endpoint['max_tokens'], 16384)}

        def one(index_problem):
            index, problem = index_problem
            reference = makeup['references'][index]
            origin = {'question_id': reference['question_id'], 'rubric_id': reference['rubric_id'], 'number': reference['problem']['number'],
                      'title': reference['problem']['title'], 'label': source}
            sig = signature(problem, mode)
            feedback = extra['rubric_feedback'][index] if index < len(extra['rubric_feedback']) else ''
            if (sig, reference['rubric_id']) in carried and not feedback:
                return {**carried[(sig, reference['rubric_id'])], 'problem': problem['number']}
            content = problem['content'][letter]
            target = {'version': f'Version {letter} (makeup)', 'number': str(problem['number']), 'label': f"{problem['number']}.",
                      'points': problem['points'], 'points_text': quiz_latex.points_text(problem['points']),
                      'parts': [part['label'] for part in problem['parts']], 'statement': statement_text(content)}
            payload = {'reference': {'version': source, **reference['problem']}, 'target': target, 'pictures_show_pages': [problem['page']]}
            if feedback:
                payload['instructor_feedback'] = feedback
            picture = self._quiz_picture(pages[letter][problem['page'] - 1]['png'], 1275)
            try:
                drafted, _ = self._quiz_ask(task, writer, settings, f"{RUBRIC}{problem['number']}", 'rubric_adapt', payload, [picture],
                                            lambda output, _metadata: adapted_rubric(output, reference['problem'], {'points': problem['points'], 'parts': problem['parts'], 'statement': f"{target['statement']}\n{content['solution'] or ''}"}, source))
                return {'problem': problem['number'], 'signature': sig, 'rubric': drafted, 'source': origin}
            except RetryLater as waiting:
                return {'problem': problem['number'], 'wait': waiting.seconds}
            except (ProviderError, ValueError, TypeError, KeyError) as failure:
                return {'problem': problem['number'], 'signature': sig, 'source': origin, 'error': str(failure)}
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(quiz['problems'])))) as pool:
            return list(pool.map(one, enumerate(quiz['problems'])))

    # Approval ---------------------------------------------------------------------------------------------------------------------
    def approve_makeup_draft(self, draft_id, data):
        """The instructor's approval of a finished makeup revision (`confirmation: "approve"`): how many copies to print, the makeup
        sitting to attach them to (an existing one by `id`, a new one by its label, date, time and place, or none), the students to place
        on it, and `certified_ok` when the plan is certified. `save_keys` (2026-10-11, "Carry the key through"): sent by the window
        that shows each problem's answer key, and only then are the keys saved with the makeup's questions; absent, none is saved and
        the approval reads and checksums as before. The worker carries it out (`run_quiz_approval` → `_carry_out_makeup`)."""
        request_id = identifier(data.get('request_id'))
        if data.get('confirmation') != 'approve':
            raise ValueError('Confirm the approval: it adds the makeup version to the assignment, its grading setup and a print job.')
        revision_id = identifier(data.get('revision_id'))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            draft = self._quiz_draft(db, draft_id)
            self._quiz_not_scrapped(db, draft_id)
            context = json.loads(draft['input_json'])
            makeup = context['makeup']
            aid = makeup['plan_id']
            plan = self.plan(db, aid)
            revision = db.execute("SELECT * FROM quiz_draft_revisions WHERE id=? AND draft_id=? AND status='complete' AND cancelled=0",
                                  (revision_id, draft_id)).fetchone()
            if not revision:
                raise ValueError('Approve a finished revision of this makeup.')
            latest = db.execute("SELECT id FROM quiz_draft_revisions WHERE draft_id=? AND status='complete' AND cancelled=0 ORDER BY number DESC LIMIT 1", (draft_id,)).fetchone()
            if latest['id'] != revision['id']:
                raise Conflict('A newer revision of this makeup exists. Open it before approving.')
            missing = [r['problem'] for r in json.loads(revision['result_json'])['rubrics'] if not r.get('rubric')]
            if missing:
                raise Conflict(f"Problem{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} {'have' if len(missing) > 1 else 'has'} no rubric yet. "
                               'Ask for changes so the model adapts it again.')
            copies = data.get('copies')
            if type(copies) is not int or not 0 <= copies <= 500:
                raise ValueError('Give a number of copies from 0 to 500.')
            sitting = data.get('sitting') or None
            if sitting is not None:
                if not isinstance(sitting, dict):
                    raise ValueError('Choose a makeup sitting, or none.')
                if sitting.get('id'):
                    sitting_id = identifier(sitting['id'])
                    row = db.execute("SELECT kind FROM exam_sittings WHERE id=? AND event_id=?", (sitting_id, plan['event_id'])).fetchone()
                    if not row or row['kind'] != 'makeup':
                        raise ValueError("Choose a makeup sitting of this assignment's exam record.")
                    sitting = {'id': sitting_id}
                else:
                    day = sitting.get('date')
                    try:
                        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                            raise ValueError
                    except ValueError:
                        raise ValueError("Give the makeup sitting's date as YYYY-MM-DD.") from None
                    sitting = {'label': ' '.join(text(sitting.get('label') or 'Makeup', 'Sitting name', 280).split()) or 'Makeup', 'date': day,
                               'time': text(sitting.get('time', ''), 'Time', 280).strip(), 'location': text(sitting.get('location', ''), 'Where', 280).strip()}
            asked = data.get('students') or []
            if not isinstance(asked, list) or len(asked) > MAX_STUDENTS:
                raise ValueError(f'Name at most {MAX_STUDENTS} students to place on the makeup sitting.')
            students = []
            sections = {r[0] for r in db.execute('SELECT section_id FROM exam_event_sections WHERE event_id=?', (plan['event_id'],))}
            for pair in asked:
                if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(v, str) for v in pair)) or pair[0] not in sections \
                        or not db.execute('SELECT 1 FROM roster_memberships WHERE section_id=? AND enrollment_id=?', tuple(pair)).fetchone():
                    raise ValueError("Name students of this assignment's sections.")
                if pair not in students:
                    students.append(pair)
            if students and sitting is None:
                raise ValueError('Choose the makeup sitting to place the students on.')
            certified = plan_progress.latest_certification(db, aid) is not None
            if certified and data.get('certified_ok') is not True:
                raise Conflict(f"{plan['title']} is certified. Adding a makeup version brings it back as Certify again; no existing grade changes. "
                               'Confirm that to approve the makeup.')
            approval = {'rule': MAKEUP_RULE, 'revision_id': revision['id'], 'term_id': context['term_id'], 'plan_id': aid, 'letter': makeup['letter'],
                        'title': plan['title'], 'copies': copies, 'sitting': sitting, 'students': students, 'certified_ok': certified,
                        **({'save_keys': True} if data.get('save_keys') is True else {})}
            checksum = digest([draft_id, approval])
            prior = db.execute('SELECT * FROM quiz_draft_approvals WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another approval.')
                return self._quiz_draft_summary(db, draft, details=True)
            if db.execute("SELECT 1 FROM quiz_draft_approvals WHERE draft_id=? AND status!='failed'", (draft_id,)).fetchone():
                raise Conflict('This makeup is already approved.')
            if db.execute("SELECT 1 FROM quiz_draft_revisions WHERE draft_id=? AND status IN ('pending','running')", (draft_id,)).fetchone():
                raise Conflict('The model is still working on a newer revision. Wait for it or stop it before approving.')
            now, approval_id = timestamp(), uid()
            db.execute("""INSERT INTO quiz_draft_approvals(id,draft_id,revision_id,term_id,request_id,request_sha256,input_json,status,actor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'pending','workspace',?,?)""", (approval_id, draft_id, revision['id'], context['term_id'], request_id, checksum, encoded(approval), now, now))
            self.event(db, aid, 'makeup_draft_approved', {'draft_id': draft_id, 'approval_id': approval_id, 'revision_id': revision['id'],
                                                          'letter': makeup['letter'], 'copies': copies, 'students': len(students)})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    def _carry_out_makeup(self, task, approval):
        from document_identity import compiler
        from document_identity.storage import read_verified
        documents = self.exams.documents
        report = lambda step, **detail: self._approval_progress(task['id'], step, **detail)
        with self.rosters.lock(), connect(self.path) as db:
            draft = self._quiz_draft(db, task['draft_id'])
            revision = db.execute('SELECT * FROM quiz_draft_revisions WHERE id=?', (approval['revision_id'],)).fetchone()
            context, drafted = json.loads(draft['input_json']), json.loads(revision['result_json'])
            makeup, letter, aid = context['makeup'], approval['letter'], approval['plan_id']
            plan = self.plan(db, aid)
            # 0. The original's rubrics are still the ones adapted: a rubric changed since is adapted again, never approved unseen.
            current = {}
            for r in drafted['rubrics']:
                try:
                    question = self.question(db, aid, r['source']['question_id'])
                except NotFound:
                    question = None
                if not question or not question['rubric'] or question['rubric_id'] != r['source']['rubric_id']:
                    raise Conflict(f"{r['source']['label']}'s rubric for {r['source']['title']} changed after the makeup was drafted. "
                                   'Ask for changes so the model adapts it again.')
                current[r['problem']] = question
            mine = db.execute("SELECT id,canonical_sha256 FROM assignment_versions WHERE assignment_id=? AND label=?",
                              (plan['source_assignment_id'], letter)).fetchone()
            if letter in self.plan_versions(db, aid) and not mine:
                raise Conflict(f"{plan['title']} has a Version {letter} now. Draft the makeup again: it will be the next letter.")
        quiz = drafted['quiz']
        # 1. The version letter on the plan, and the printed version on the assignment, from the very PDF the instructor approved.
        self.change_added_version(aid, {'exam_version': letter, 'version': plan['version']}, True)
        class_bytes = (compiler.TEMPLATES / 'facultyassignment.cls').read_bytes()
        stored = drafted['versions'][letter]
        if digest(class_bytes) != stored['class_sha256']:
            raise ValueError("Faculty's page class changed since this makeup was compiled. Ask for changes to compile it again, then approve it.")
        with self.rosters.lock(), documents.files.files('definition', draft['definition_id']) as root:
            files = {name: read_verified(root, item['path'], item['sha256']) for name, item in stored['files'].items()}
        if mine:
            if mine['canonical_sha256'] != digest(files['main.pdf']):
                raise Conflict(f"{plan['title']} already has a different Version {letter}. Draft the makeup again.")
            version_id = mine['id']
        else:
            details = {**drafted['toolchain'][letter], 'generator': MAKEUP_GENERATOR, 'draft_id': draft['id'], 'revision_id': revision['id'],
                       'quiz_rule': quiz['rule'], 'makeup_of': aid}
            sources = {'main.tex': files['main.tex'], quiz_latex.STYLE: files[quiz_latex.STYLE], 'facultyassignment.cls': class_bytes, 'build.log': files['build.log']}
            version_id = documents.register_version(plan['source_assignment_id'], letter, files['main.pdf'], sources, details)['id']
        report('versions', assignment_id=plan['source_assignment_id'], versions={letter: version_id})
        # 2. One question per problem on the makeup version, each keeping its original's rubric structure (`adapted_rules`), and, for
        # an approval sent from the window that showed them (`save_keys`), the answer key read for it while the makeup was drafted:
        # saved with the question, before the rubric review below is requested, so the review's reference carries it.
        rubrics_by_problem = {r['problem']: r for r in drafted['rubrics']}
        created = {}
        for problem in quiz['problems']:
            entry, reference = rubrics_by_problem[problem['number']], current[problem['number']]
            rubric, content = entry['rubric'], problem['content'][letter]
            rules = makeup_rules(reference, rubric, problem['points'], letter, makeup['source']['label'])
            lines = [{k: item[k] for k in ('id', 'description', 'points')} for item in rules['items']]
            origin = entry['source']
            source = json.dumps({'mode': rules['mode'], 'items': lines,
                                 'adapted_from': {'version': makeup['source']['label'], 'question': origin['number'], 'rubric_id': origin['rubric_id'],
                                                  'revision': reference['rubric']['revision']},
                                 'notes': f"Makeup Version {letter}, problem {problem['number']}, drafted by a model with makeup draft {draft['id']} revision "
                                          f"{revision['number']}, its rubric adapted from {makeup['source']['label']}'s {origin['title']}, and approved by the instructor."},
                                indent=2, ensure_ascii=False)
            question = self.create_draft_question(aid, filename=f"makeup-question-{problem['number']}-{letter}.json", content=source.encode(), source=source,
                rules=rules, max_points=problem['points'], printed=str(problem['number']), exam_version=letter, key=f"q{problem['number']}-{letter}",
                document_id=version_id, request_id=f"makeup:{revision['id']}:{problem['number']}", title=reference['content']['title'] or f"Question {problem['number']}",
                compiler={'name': MAKEUP_COMPILER, 'rule': MAKEUP_RULE, 'mode': rules['mode'], 'draft_id': draft['id'], 'revision_id': revision['id'],
                          'version': letter, 'problem': f"P{problem['number']}:{letter}",
                          'adapted_from': {'question_id': origin['question_id'], 'rubric_id': origin['rubric_id'],
                                           'exam_version': makeup['source']['exam_version'], 'printed_number': origin['number']}},
                event='makeup_question_created', statement=statement_text(content), solution=content['solution'] or rubric.get('solution', ''),
                detail={'draft_id': draft['id'], 'revision_id': revision['id'], 'approval_id': task['id'], 'adapted_from': origin['question_id']},
                answer_key=((entry.get('keys') or {}).get(letter) or {}).get('key') if approval.get('save_keys') else None)
            created[(problem['number'], letter)] = question['question_id']
        saved = {}
        if approval.get('save_keys'):  # as the catalog has it: a question an earlier attempt created without a key is not counted
            with self.rosters.lock(), connect(self.path) as db:
                saved = {'keys': sum(1 for qid in created.values() if self.question(db, aid, qid)['content'].get('answer_key'))}
        report('questions', count=len(created), **saved)
        # 3. The makeup's template from its own PDF; confirmed with its answer areas only when every other version's template already is,
        # so no other version's template or answer area is ever chosen or changed here.
        template_id = self.templates.import_version(version_id)['id']
        notes, confirmed = [], 0
        try:
            with self.rosters.lock():
                setup = self.assignment_templates(aid)
                slot = next((s for s in setup['slots'] if s['document_id'] == version_id and s['exam_version'] == letter), None)
                others = [s for s in setup['slots'] if s is not slot]
                if slot is None:
                    notes.append(f'Version {letter} has no template slot yet; choose its template in Rubrics & Templates.')
                elif not slot['confirmed']:
                    if all(s['confirmed'] and s['template_id'] for s in others):
                        self.confirm_assignment_templates(aid, {'acknowledge_warning': True, 'input_sha256': setup['input_sha256'], 'selections': [
                            {'key': s['key'], 'template_id': template_id if s is slot else s['template_id']} for s in setup['slots']]})
                    else:
                        notes.append("Another version's template is not confirmed yet, so the makeup's was left for you to confirm in Rubrics & Templates.")
                if slot is not None and not notes:
                    confirmed = self._confirm_quiz_areas(aid, quiz, drafted, created, {letter: template_id}, revision['id'])
        except (ValueError, Conflict) as failure:
            notes.append(f"The makeup's template and answer areas were left for you to confirm in Rubrics & Templates ({getattr(failure, 'description', None) or failure}).")
        report('areas', templates={letter: template_id}, confirmed=confirmed, notes=notes)
        # 4. The makeup's rubrics confirmed for the rubric review (only these questions), and the review requested (instructor,
        # 2026-10-04, ABSENCES "MAKEUP BOOKING": makeups should need as little of them as possible); finalizing stays theirs.
        with self.rosters.lock(), connect(self.path) as db:
            status_ = self.rubric_quality_status(db, aid)
        mine_q = set(created.values())
        readiness = {qid: {'rubric_id': r['rubric_id'], 'comments_sha256': r['comments_sha256']} for qid, r in status_['readiness'].items()
                     if qid in mine_q and not r['ready']}
        if readiness:
            self.confirm_rubric_review(aid, {'questions': readiness})
        requested = False
        try:
            with self.rosters.lock(), connect(self.path) as db:
                current = self.rubric_quality_status(db, aid)
            self.queue_rubric_quality(aid, {'request_id': f"makeup-review:{revision['id']}", 'input_sha256': current['input_sha256']})
            requested = True
        except (ValueError, Conflict) as failure:
            # Another review already running, or another version's rubrics not confirmed for review: the review is left for them.
            notes.append(f"The rubric review was not requested ({getattr(failure, 'description', None) or failure}); request it in Key & Rubric Finalization.")
        report('review', confirmed=len(readiness), requested=requested)
        # 5. The makeup sitting (an existing one, or a new one saved through the Exams page's own path).
        event_id = plan['event_id']
        sitting_id = None
        if approval.get('sitting'):
            sitting_id = approval['sitting'].get('id') or self._makeup_sitting(event_id, approval['sitting'], task['id'])
        # 6. One unnamed print job for the term, attached to the exam record (to the makeup sitting when there is one).
        batches = []
        if approval['copies']:
            with self.rosters.lock(), connect(self.path) as db:
                sections = [r[0] for r in db.execute('SELECT section_id FROM exam_event_sections WHERE event_id=? ORDER BY section_id', (event_id,))]
            batch = documents.create_batch(version_id, None, approval['copies'], f"makeup:{revision['id']}:{letter}", term_id=plan['term_id'], section_ids=sections)
            batches.append({'batch_id': batch['id'], 'version': letter, 'copies': approval['copies']})
            self._attach_quiz_batch(event_id, batch['id'], sitting_id=sitting_id)
        report('print', batches=len(batches), sitting_id=sitting_id)
        # 7. The students the instructor ticked, placed on the makeup sitting (their expectation and grades stay as they are).
        placed = 0
        if sitting_id and approval['students']:
            event = self.exams.get(event_id)
            key = lambda p: (p['section_id'], p['enrollment_id'])
            if any(tuple(pair) not in {key(p) for p in event['participants']} for pair in approval['students']):
                event = self.exams.sync_roster(event_id, {'version': event['version']})
            for section_id, enrollment_id in approval['students']:
                person = next((p for p in event['participants'] if key(p) == (section_id, enrollment_id)), None)
                if not person or person['sitting_id'] == sitting_id:
                    continue
                event = self.exams.update_participant(event_id, section_id, enrollment_id, {'version': person['version'], 'sitting_id': sitting_id})
                placed += 1
        report('students', placed=placed)
        self.inbox.wake.set()
        return {'assignment_id': plan['source_assignment_id'], 'plan_id': aid, 'event_id': event_id, 'letter': letter, 'versions': {letter: version_id},
                'templates': {letter: template_id}, 'questions': {f'{n}:{l}': q for (n, l), q in created.items()}, 'batches': batches,
                'areas_confirmed': confirmed, 'notes': notes, 'sitting_id': sitting_id, 'placed': placed}

    def _makeup_sitting(self, event_id, sitting, approval_id):
        """A new makeup sitting saved through the Exams page's own path (`ExamService.save_sitting`), once: a retried approval finds the
        sitting its earlier attempt saved (named in its progress) instead of saving another."""
        with self.rosters.lock(), connect(self.path) as db:
            progress_ = json.loads(db.execute('SELECT progress_json FROM quiz_draft_approvals WHERE id=?', (approval_id,)).fetchone()[0] or '{}')
            known = (progress_.get('sitting') or {}).get('sitting_id')
            if known and db.execute('SELECT 1 FROM exam_sittings WHERE id=? AND event_id=?', (known, event_id)).fetchone():
                return known
            before = {r[0] for r in db.execute('SELECT id FROM exam_sittings WHERE event_id=?', (event_id,))}
            version = db.execute('SELECT version FROM exam_events WHERE id=?', (event_id,)).fetchone()[0]
        event = self.exams.save_sitting(event_id, {'version': version, 'kind': 'makeup', **sitting})
        sitting_id = next(s['id'] for s in event['sittings'] if s['id'] not in before)
        self._approval_progress(approval_id, 'sitting', sitting_id=sitting_id)
        return sitting_id
