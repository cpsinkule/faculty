"""ASSIGNMENT-WORKSPACE: workspace-owned read projections; no retained state or writes.

The course-wide assignment and permanent term identify a workspace. Every operation still
belongs to the service that owns its records. This module does not call outside services.
"""
import logging
from urllib.parse import parse_qs, quote, unquote

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import NotFound

from certified_archive import certified, facts, pair_activity
from course_groups import navigation_id
from document_identity import photocopies
from exam_workflow.overview import ExamOverview, lifecycle
from gradebook.service import _setup
from rosters import connect
from seating import term_label
from teach_inbox.service import PRESENT

log = logging.getLogger(__name__)


def workspace_href(assignment, term, area=None):
    value = f"#assignments/{quote(str(assignment), safe='')}/{quote(str(term), safe='')}"
    return value + f"?area={area}" if area else value


def makeup_item(makeup, assignment, term):
    """A MAKEUP VERSION WAITING TO BE APPROVED (ASSIGNMENT-WORKSPACE): the needs-you item of a `makeups_waiting` entry, its address opening the draft window."""
    return {'key': 'makeup', 'label': f"{'Approve makeup version' if makeup['state'] == 'ready' else 'Try again: makeup draft'} {makeup['letter']}",
            'href': workspace_href(assignment, term, 'create') + '&makeup=1', 'count': None, 'unit': 'setup'}


def work_items(workflow, plan_id):
    """Owner counts retain their units; a rubric decision is never added to a paper total."""
    if not workflow or not plan_id:
        return []
    result = []
    base = f"#grading/{plan_id}"
    if workflow.get('unavailable'):
        return [{'key': 'grading-unavailable', 'label': 'Grading progress is unavailable · open grading',
                 'href': base, 'unit': 'setup', 'count': None}]
    for key, label, tab, unit in (
        ('version_decisions', 'Decide paper versions', 'rubrics&focus=versions', 'papers'),
        ('failed', 'Inspect failed grading', 'review&open=failed', 'responses'),
        ('review_responses', 'Review grades', 'review', 'responses'),
    ):
        if workflow.get(key):
            result.append({'key': key, 'label': label, 'count': workflow[key], 'unit': unit,
                           'href': base + '?tab=' + tab})
    setup = workflow.get('setup') or {}
    quality = setup.get('rubric_quality') or {}
    questions = setup.get('questions', [])
    location_checks = [q for q in questions
                       if (q.get('location_check') or {}).get('status') in ('failed', 'needs_review', 'needs_confirmation', 'waiting_template', 'waiting_templates')]
    unfinished = (quality.get('state') != 'confirmed' or not questions
                  or any(not (q.get('rubric') or {}).get('approved_at') for q in questions))
    # PREPARATION NEEDS YOU ONLY WHEN IT WAITS FOR YOU (2026-10-18, Home's rule, home.js `setup`): not while the pipeline itself works
    # it (a rubric preparation queued or running, the rubric check running, a blank template being read), and not beside "Check
    # answer areas and templates" once every question has its rubric: the answer areas are then the step, and finalization follows.
    preparation = setup.get('preparation') or {}
    working = (bool(preparation.get('pending')) or quality.get('state') in ('pending', 'running')
               or any((q.get('location_check') or {}).get('status') in ('pending', 'running') for q in questions))
    rubrics_in = bool(questions) and all(q.get('rubric') for q in questions) and not preparation.get('failed')
    if unfinished and not working and not (location_checks and rubrics_in):
        # A FINISHED RUBRIC CHECK SAYS SO (2026-11-02): the row and the overview use the words and the address of Home's card and the
        # badge ("Review the rubric check", workspace_overview.grading_attention), not a general "Finish grading preparation".
        check_waits = quality.get('state') == 'needs_review'
        result.append({'key': 'preparation', 'label': 'Review the rubric check' if check_waits else 'Finish grading preparation',
                       'href': base + ('?tab=answer-key&open=quality' if check_waits else '?tab=rubrics&workspace=1'), 'unit': 'setup', 'count': None})
    if location_checks:
        result.append({'key': 'answer-areas', 'label': 'Check answer areas and templates',
                       'href': base + '?tab=locations', 'unit': 'questions', 'count': len(location_checks)})
    review = (workflow.get('progress') or {}).get('review') or {}
    # READY TO CERTIFY MEANS NOTHING IS STILL COMING (2026-10-11): not while a linked scan waits for its scan checks or a grading
    # task runs. Certification refuses both, and the scan's own check or the running work is what the overview lists.
    arriving = workflow.get('awaiting_review') or workflow.get('pending')
    if ((workflow.get('certification') or {}).get('state') != 'current' and review.get('total')
            and review.get('completed') == review['total'] and not arriving):
        # The step is named as its tab is (critique 2026-10-05: one status vocabulary, DESIGN-LANGUAGE §1.11).
        result.append({'key': 'certify', 'label': 'Certify grades',
                       'href': base + '?tab=export', 'unit': 'setup', 'count': None})
    return result


class AssignmentWorkspace:
    def __init__(self, app):
        self.app = app
        self.rosters = app.extensions['rosters']
        self.exams = app.extensions['exams']
        self.frq = app.extensions['frq_grading']
        self.documents = app.extensions['documents']
        self.scans = app.extensions['document_scans']
        self.path = self.exams.path
        self.overview = ExamOverview(self.exams, lambda: self.frq)

    def _context(self, db, assignment, term):
        row = db.execute("""SELECT a.id assignment_id,a.title,a.definition_id,d.code,t.id term_id,
            t.term,t.term_other,t.year,t.is_current,e.id event_id,f.id plan_id
            FROM assignments a JOIN course_definitions d ON d.id=a.definition_id
            JOIN course_terms t ON t.definition_id=a.definition_id
            LEFT JOIN exam_events e ON e.assignment_id=a.id AND e.term_id=t.id
            LEFT JOIN frq_assignments f ON f.event_id=e.id WHERE a.id=? AND t.id=?""", (assignment, term)).fetchone()
        if row is None:
            raise NotFound('ASSIGNMENT-WORKSPACE: assignment not found in this course term.')
        value = dict(row)
        value['term_label'] = term_label(value)
        value['href'] = workspace_href(assignment, term)
        return value

    @staticmethod
    def _gradebook_term(db, parts):
        if parts[0] == 'gradebook' and len(parts) == 2:
            row = db.execute('SELECT id FROM course_terms WHERE id=? AND deleted_at IS NULL', (parts[1],)).fetchone()
            return row[0] if row else None
        if parts[0] != 'course' or len(parts) < 4:
            return None
        tail, number = parts[2:], None
        if tail[:2] == ['home', 'gradebook']:
            tail = tail[2:]
        elif tail[0] == 'section' and tail[1].isascii() and tail[1].isdigit():
            number, tail = tail[1], tail[2:]
            if tail[:1] == ['gradebook']:
                tail = tail[1:]
        else:
            return None
        if tail not in ([], ['setup'], ['changes']):
            return None
        # HIERARCHY: the course URL's group is only navigation; resolve to the
        # existing permanent term and membership before following its column.
        term = next((r for r in db.execute('SELECT id,code,term,term_other,year FROM course_terms WHERE deleted_at IS NULL')
                     if navigation_id(dict(r)) == parts[1]), None)
        if term is None or (number is not None and not db.execute(
                'SELECT 1 FROM course_sections WHERE term_id=? AND number=?', (term['id'], number)).fetchone()):
            return None
        return term['id']

    def resolve(self, route):
        """Resolve old working links without guessing a term or creating records."""
        path, _, query = route.lstrip('#').partition('?')
        parts = [unquote(p) for p in path.split('/')]
        args = parse_qs(query)
        get = lambda key: args.get(key, [None])[0]
        pair = None
        with connect(self.path) as db:
            if parts[0] == 'assignments' and len(parts) == 3:
                pair = parts[1:]
            elif parts[0] in ('grading', 'grade-delivery', 'teaching-review') and len(parts) == 2:
                pair = db.execute('SELECT e.assignment_id,e.term_id FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id WHERE f.id=?', (parts[1],)).fetchone()
            elif parts[0] == 'exams' and len(parts) == 2:
                pair = db.execute('SELECT assignment_id,term_id FROM exam_events WHERE id=?', (parts[1],)).fetchone()
            elif parts[:2] == ['exams', 'assessment'] and len(parts) == 4:
                pair = parts[2:]
            elif parts[0] == 'exam-day' and len(parts) == 3:
                pair = parts[1:]
            elif parts[0] in ('gradebook', 'course') and (get('item') or get('assignment')):
                term = self._gradebook_term(db, parts)
                if term:
                    if get('item'):
                        setup, _ = _setup(db, term)
                        item = next((item for item in setup['items'] if item['id'] == get('item')), None)
                        assignment = item.get('assignment_id') if item else None
                    else:
                        assignment = get('assignment')
                    if assignment and db.execute('''SELECT 1 FROM assignments a JOIN course_terms t
                            ON t.definition_id=a.definition_id WHERE a.id=? AND t.id=?''', (assignment, term)).fetchone():
                        pair = (assignment, term)
            elif parts[0] == 'seating' and len(parts) == 2:
                pair = db.execute('SELECT assignment_id,term_id FROM seating_charts WHERE id=?', (parts[1],)).fetchone()
            elif parts[:2] == ['seating', 'exam'] and len(parts) == 4:
                # An exam's page of rooms (SEATING "One tile per exam"): the assignment its charts belong to, when it has any.
                pair = db.execute('SELECT assignment_id,term_id FROM seating_charts WHERE assignment_id=? AND term_id=? LIMIT 1', parts[2:]).fetchone()
            elif parts[:2] == ['inbox', 'job'] and len(parts) >= 3:
                pair = db.execute(f"""SELECT e.assignment_id,e.term_id FROM inbox_jobs j
                    JOIN frq_scan_links l ON l.message_id=j.message_id JOIN frq_assignments f ON f.id=l.assignment_id
                    JOIN exam_events e ON e.id=f.event_id WHERE j.id=? AND {PRESENT.format('j.message_id')}
                    AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id)""", (parts[2],)).fetchone()
            elif parts[:2] == ['documents', 'quiz'] and len(parts) == 3:
                draft = self.frq.quiz_draft(parts[2])
                approval = draft.get('approved') or {}
                result = approval.get('result') or {}
                if result.get('assignment_id'):
                    return {'context': self._context(db, result['assignment_id'], approval['term_id']), 'draft': draft}
                return {'context': None, 'draft': draft}
            elif parts[0] == 'documents':
                if get('batch'):
                    pair = db.execute('SELECT v.assignment_id,b.term_id FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE b.id=?', (get('batch'),)).fetchone()
                elif get('chart'):
                    pair = db.execute('SELECT assignment_id,term_id FROM seating_charts WHERE id=?', (get('chart'),)).fetchone()
                elif get('assignment') or get('jobs'):
                    assignment = get('assignment') or get('jobs')
                    if get('term'):
                        pair = (assignment, get('term'))
                    else:
                        terms = db.execute('SELECT t.id FROM course_terms t JOIN assignments a ON a.definition_id=t.definition_id WHERE a.id=? AND t.is_current=1', (assignment,)).fetchall()
                        if len(terms) == 1:
                            pair = (assignment, terms[0][0])
            return {'context': self._context(db, *pair) if pair else None}

    def _scan_items(self, scans):
        items = []
        for scan in scans:
            overview = scan['overview']
            for index, step in enumerate(overview.get('steps', [])):
                if step.get('kind') not in ('todo', 'check'):
                    continue
                # INBOX: stages expose their combined detail, not a nested list of steps.
                # The owner's explicit placement takes precedence over its action/tool defaults.
                stage = next((s for s in overview.get('stages', []) if step.get('text') and step['text'] in s.get('detail', '')), None)
                if stage is None:
                    key = self.scans.ACTION_STAGE.get(step.get('action')) or self.scans.TOOL_STAGE.get(step.get('tool')) or 'scan'
                    stage = next((s for s in overview.get('stages', []) if s['key'] == key), {})
                page = stage.get('page')
                href = stage.get('href') or f"#inbox/job/{scan['job_id']}" + (f'/{page}' if page else '')
                items.append({'key': f"scan:{scan['job_id']}:{index}", 'label': step.get('text') or 'Review scan',
                              'href': href, 'unit': 'scan check', 'count': None})
        return items

    @staticmethod
    def _open_regrades(db):
        """{plan id: open Gradescope regrade requests} (REGRADES): Return's Regrade requests, read once per request."""
        from grading.regrade_requests import RegradeRequests
        try:
            return RegradeRequests.open_counts(db)[0]
        except Exception:  # noqa: BLE001 - a projection failure must not hide the assignment
            log.warning('ASSIGNMENT-WORKSPACE: regrade requests unavailable', exc_info=True)
            return {}

    def _makeups_waiting(self, db):
        """{plan id: {letter, state}} of the makeup drafts waiting on the instructor (grading/makeup_drafts.py), read once per request."""
        try:
            return self.frq.makeups_waiting(db)
        except Exception:  # noqa: BLE001 - a projection failure must not hide the assignment
            log.warning('ASSIGNMENT-WORKSPACE: makeup drafts unavailable', exc_info=True)
            return {}

    def _projection(self, db, context, certifications=None, listing=False, regrades=None, makeups=None):
        """One request's counts and attention, shared by list and detail; caller holds the lifecycle lock. `listing`: the list
        discards an archived row's stages and needs, so they are not read for it (PERFORMANCE; the rows are unchanged)."""
        assignment, term, plan_id = context['assignment_id'], context['term_id'], context['plan_id']
        event = self.exams._event(db, context['event_id']) if context['event_id'] else None
        collection = None
        workflow, summary, scans, unavailable = {}, None, [], []
        memo = {}  # This read's memo: the grading summary's scan overviews are not read a second time below.
        if plan_id:
            try:
                plan = self.frq.plan(db, plan_id)
                entry = self.frq.grading_entry(db, plan, shared=memo)
                # PERFORMANCE: the lifecycle and grading counters share this request's
                # verified roster and reconciled collection, just as the owner's overview does.
                participants = self.exams._participants(db, event)
                collection = self.exams._collection(db, event, participants)
                workflow = self.frq.workflow_summary(db, plan, context=(event, participants, collection), shared=memo, export=False)
                summary = {**workflow, 'plan_id': plan_id, 'entry': entry}
            except Exception:  # A projection failure must not hide the other assignments or claim completion.
                log.warning('ASSIGNMENT-WORKSPACE: grading progress unavailable for %s', plan_id, exc_info=True)
                workflow = {'unavailable': True}
                summary = {'plan_id': plan_id, 'unavailable': True}
                unavailable.append('grading')
            scan_rows = workflow.get('scans')
            if scan_rows is None:
                scan_rows = [dict(r) for r in db.execute(f"""SELECT l.message_id,j.id job_id,
                    (SELECT COUNT(*) FROM scan_pages p WHERE p.message_id=l.message_id) pages
                    FROM frq_scan_links l JOIN inbox_jobs j ON j.message_id=l.message_id
                    WHERE l.assignment_id=? AND {PRESENT.format('l.message_id')}
                    AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id)
                    ORDER BY l.id DESC""", (plan_id,))]
            for scan in scan_rows:
                try:
                    overview = memo.get('scan_overviews', {}).get(scan['message_id']) or self.scans.overview(
                        db, scan['message_id'], memo=memo.setdefault('booklets', {}))
                except Exception:
                    log.warning('ASSIGNMENT-WORKSPACE: scan progress unavailable for %s', scan['job_id'], exc_info=True)
                    unavailable.append('scan:' + scan['job_id'])
                    overview = {'unavailable': True, 'headline': 'Scan progress unavailable', 'steps': [
                        {'kind': 'check', 'text': 'Scan progress is unavailable · open the scan', 'action': None, 'tool': None}], 'stages': []}
                scans.append({**scan, 'overview': overview})
        if collection is None and event:
            # Collection remains readable from catalog memberships if a grading roster
            # payload is unavailable, and assignments without a plan never need that payload.
            collection = self.exams._collection(db, event, self.overview._participants(db, event['id']))
        archive = (certified(db) if certifications is None else certifications).get((assignment, term))
        archived = facts(archive, pair_activity(db, assignment, term, plan_id)) if archive else None
        # An open Gradescope regrade request is work after certification (REGRADES, 2026-10-07): it keeps the assignment in view until
        # it is answered, as new work does (CERTIFIED-ARCHIVE "NEW WORK STAYS OUT").
        asked = ((self._open_regrades(db) if regrades is None else regrades).get(plan_id) or 0) if plan_id else 0
        # A MAKEUP VERSION WAITING TO BE APPROVED (ASSIGNMENT-WORKSPACE, 2026-11-05) keeps it in view the same way: a student owes the makeup.
        makeup = ((self._makeups_waiting(db) if makeups is None else makeups).get(plan_id)) if plan_id else None
        if unavailable or asked or makeup or (workflow.get('certification') or {}).get('state') != 'current' or any(
                workflow.get(key) for key in ('awaiting_review', 'pending', 'new_responses', 'review_responses', 'failed')):
            archived = None
        if listing and archived:
            return {'lifecycle': None, 'workflow': workflow, 'scans': scans, 'needs_you': [], 'archived': archived, 'unavailable': unavailable}
        stages = lifecycle(db, assignment, term, event=event, collection=collection, grading=summary, seating=self.exams.seating)
        # The paper is every other step's prerequisite, so it leads (2026-10-18: the index row named "Finish grading preparation"
        # before the missing paper; PREPARE STARTS WITH ITS PREREQUISITE).
        needs = []
        if next(s for s in stages['stages'] if s['key'] == 'paper')['state'] != 'done':
            needs.append({'key': 'paper', 'label': 'Create or upload the assignment paper',
                          'href': workspace_href(assignment, term, 'create'), 'count': None, 'unit': 'setup'})
        needs.extend(work_items(workflow, plan_id))
        if not plan_id:
            needs.append({'key': 'preparation', 'label': 'Prepare grading alongside the paper',
                          'href': workspace_href(assignment, term, 'prepare'), 'count': None, 'unit': 'setup'})
        needs.extend(self._scan_items(scans))
        if asked:
            needs.append({'key': 'regrades', 'label': 'Answer the Gradescope regrade requests', 'href': f"#grade-delivery/{plan_id}?view=regrades",
                          'count': asked, 'unit': 'requests'})
        if makeup:
            needs.append(makeup_item(makeup, assignment, term))
        # THE ZEROS ARE SAID AT CERTIFY (ASSIGNMENT-WORKSPACE, requirement, 2026-11-05): students with no scanned paper are not a needs-you
        # item here; Certify confirms their 0s before anything is recorded.
        collected = next((s for s in stages['stages'] if s['key'] == 'collected'), {})
        # UPLOAD THE SCANS (requirement, 2026-10-11): the assessment's own date has passed, grading is set up for
        # it and no paper has come back (nothing collected, no scan linked), so the scans are the instructor's to upload, on Collect.
        # Never "find or excuse" for the whole class; an assessment scored by hand (no grading plan) never carries it.
        dates = stages.get('dates') or {}
        if (plan_id and not scans and collected.get('state') == 'idle' and (collected.get('counts') or {}).get('expected')
                and dates.get('date') and dates['date'] < dates['today']
                and (workflow.get('certification') or {}).get('state') != 'current'):
            needs.append({'key': 'upload', 'label': 'Upload the scans',
                          'href': workspace_href(assignment, term, 'collect'), 'count': None, 'unit': 'papers'})
        for item in stages.get('readiness', []):
            if not item['ok']:
                needs.append({'key': 'ready:' + item['key'], 'label': item['label'], 'href': item['href'], 'count': None, 'unit': 'setup'})
        if archived:
            needs = []
        return {'lifecycle': stages, 'workflow': workflow, 'scans': scans, 'needs_you': needs,
                'archived': archived, 'unavailable': unavailable}

    def listing(self):
        catalog = self.documents.catalog()
        paper = {(r['assignment_id'], r['term_id']): r for r in catalog['lifecycle']}
        with connect(self.path) as db:
            certifications = certified(db)
            regrades = self._open_regrades(db)
            makeups = self._makeups_waiting(db)
            pairs = set(paper)
            pairs.update(tuple(r) for r in db.execute("""SELECT assignment_id,term_id FROM exam_events
                UNION SELECT assignment_id,term_id FROM seating_charts
                UNION SELECT v.assignment_id,b.term_id FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id"""))
        rows = []
        # PERFORMANCE: one lifecycle lock per assessment, never across the list or any external health probe.
        for assignment, term in sorted(pairs):
            with self.rosters.lock(), connect(self.path) as db:
                context = self._context(db, assignment, term)
                view = self._projection(db, context, certifications=certifications, listing=True, regrades=regrades, makeups=makeups)
            workflow, filters = view['workflow'], []
            if context['event_id'] or view['scans'] or any(s['key'] == 'print' and s['state'] != 'idle' for s in (view['lifecycle'] or {}).get('stages', [])):
                filters.append('collection')
            if any(i['key'] in ('paper', 'preparation', 'answer-areas', 'makeup') or i['key'].startswith('ready:') for i in view['needs_you']):
                filters.append('preparation')
            if workflow.get('unavailable') or any(workflow.get(k) for k in ('new_responses', 'pending', 'failed', 'review_responses', 'version_decisions')):
                filters.append('grading')
            if (workflow.get('certification') or {}).get('state') in ('current', 'changed'):
                filters.append('delivery')
            counts = [f"{workflow[k]} {label}" for k, label in [('new_responses', 'responses ready'), ('pending', 'grading tasks running'),
                      ('review_responses', 'responses to review'), ('awaiting_review', 'scans waiting for scan checks')] if workflow.get(k)]
            # The list carries counts, not every scan filename, rubric or response field.
            compact = {key: workflow[key] for key in ('unavailable', 'new_responses', 'pending', 'failed', 'review_responses',
                       'awaiting_review', 'version_decisions', 'certification', 'collection') if key in workflow}
            summary = 'Some progress could not be read' if view['unavailable'] else ' · '.join(counts) or (
                'Grades certified' if view['archived'] else 'Prepare the paper and grading' if 'preparation' in filters else 'Ready for the next papers')
            rows.append({**context, 'paper': paper.get((assignment, term), {}), 'workflow': compact, 'filters': filters,
                         'summary': summary, 'needs_you': view['needs_you'], 'archived': view['archived'], 'unavailable': view['unavailable']})
        drafts = [d for d in self.frq.quiz_drafts() if not ((d.get('approved') or {}).get('result') or {}).get('assignment_id')]
        return {'assignments': rows, 'drafts': drafts, 'definitions': catalog['definitions']}

    @staticmethod
    def _activity(db, context):
        # ASSIGNMENT-WORKSPACE: bounded owner receipts, never their payloads or student identifiers.
        rows = [dict(r) for r in db.execute(f"""SELECT 'grading:'||id id,kind,created_at FROM frq_events
            WHERE assignment_id=? AND (json_extract(detail_json,'$.message_id') IS NULL
                OR {PRESENT.format("json_extract(detail_json,'$.message_id')")}) ORDER BY frq_events.id DESC LIMIT 12""", (context['plan_id'],))]
        rows.extend(dict(r) for r in db.execute("""SELECT 'paper:'||e.id id,e.kind,e.created_at
            FROM document_events e LEFT JOIN print_batches b ON b.id=e.batch_id
            LEFT JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE (e.assignment_id=? AND e.batch_id IS NULL) OR (v.assignment_id=? AND b.term_id=?)
            ORDER BY e.id DESC LIMIT 12""", (context['assignment_id'], context['assignment_id'], context['term_id'])))
        labels = {'assignment_created': 'Assignment prepared', 'assignment_renamed': 'Assignment renamed',
                  'version_registered': 'Paper version registered', 'batch_allocated': 'Print job queued',
                  'rendering': 'Print job rendering', 'issued': 'Printed copies ready', 'labels_issued': 'Labels ready',
                  'build_failed': 'Print job failed', 'interrupted': 'Print job interrupted',
                  'scan_associated': 'Scan associated with this assignment'}
        return [{**r, 'label': labels.get(r['kind'], r['kind'].replace('_', ' ').capitalize())}
                for r in sorted(rows, key=lambda r: (r['created_at'], r['id'].split(':')[0], int(r['id'].split(':')[1])), reverse=True)[:12]]

    def _auto_grade(self, db, plan_id, workflow):
        """WHY GRADING WAITS (requirement, 2026-10-11): the Grade step's own account of automatic grading (`auto_grade_state`: the
        switch, a cancelled or reset last batch, a batch that could not start, the settle time), read only while responses wait to
        be graded, so the overview can say why. Three indexed reads; a failure says nothing rather than hiding the assignment."""
        if not plan_id or not workflow.get('new_responses'):
            return None
        try:
            return self.frq.auto_grade_state(db, plan_id, self.frq.settings()['settings'])
        except Exception:  # noqa: BLE001 - a projection failure must not hide the assignment
            log.warning('ASSIGNMENT-WORKSPACE: automatic grading state unavailable for %s', plan_id, exc_info=True)
            return None

    def _named_stamps(self, stamps, term):
        """The students' names for `photocopies.shared_stamps`, read from the term's roster only when a stamp was shared."""
        names = {student['enrollment_id']: student['name'] for student in self.documents.term_students(term)['students']} if stamps else {}
        return [{**stamp, 'papers': [{**paper, 'student': names.get(paper['enrollment_id'], '')} for paper in stamp['papers']]} for stamp in stamps]

    def detail(self, assignment, term, delivery=True):
        """One assessment's overview. `delivery=False` (opt-in `?delivery=0`: the overview's 30 s quiet refresh) reads no delivery
        receipts and leaves `delivery` out of the body: they change only through the instructor's own certify, send and publish
        actions, and the page keeps those it read on opening (PERFORMANCE: the certification view was ~1 s of Exam 1's ~1.5 s)."""
        with self.rosters.lock(), connect(self.path) as db:
            context = self._context(db, assignment, term)
            view = self._projection(db, context)
            versions = [dict(r) for r in db.execute('SELECT id,label,page_count FROM assignment_versions WHERE assignment_id=? ORDER BY created_at,rowid', (assignment,))]
            activity = self._activity(db, context)
            auto = self._auto_grade(db, context['plan_id'], view['workflow'])
            # QUIZZES "SHARED STAMPS": only where the approval asked for it, so every other assignment's answer is as it was.
            stamps = photocopies.shared_stamps(db, assignment, term) if (assignment, term) in photocopies.own_copies(db, assignment) else None
        result = {**context, **view, 'activity': activity, 'versions': versions, **({'auto_grade': auto} if auto else {}),
                  **({'shared_stamps': self._named_stamps(stamps, term)} if stamps is not None else {})}
        if not delivery:
            return result
        receipts = None
        if context['plan_id']:
            try:
                receipts = self.app.extensions['teaching_review'].delivery(context['plan_id'])
            except Exception:
                log.warning('ASSIGNMENT-WORKSPACE: delivery progress unavailable for %s', context['plan_id'], exc_info=True)
                receipts = {'unavailable': True}
                view['unavailable'].append('delivery')
        return {**result, 'delivery': receipts}


def register(app):
    service = AssignmentWorkspace(app)
    app.extensions['assignment_workspace'] = service
    api = Blueprint('assignment_workspace', __name__, url_prefix='/api/assignments')
    api.add_url_rule('', view_func=lambda: jsonify(service.listing()))
    api.add_url_rule('/resolve', 'resolve', lambda: jsonify(service.resolve(request.args.get('route', ''))))
    api.add_url_rule('/<assignment>/<term>', 'detail', lambda assignment, term: jsonify(
        service.detail(assignment, term, delivery=request.args.get('delivery') != '0')))
    app.register_blueprint(api)
