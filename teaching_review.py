"""TEACHING-WORKFLOWS: read-only term assessment projections, never grade owners.

Teaching evidence follows the latest certification's immutable decisions, question
and rubric revisions. Outward delivery follows the owners' receipts. No network
request or grade write occurs on these reads.
"""
import json
from collections import Counter

from flask import Blueprint, jsonify

from course_storage import timestamp
from gradebook import canvas_export, canvas_posting, common
from gradebook.service import _cells, _setup
from rosters import connect
from teach_inbox.service import PRESENT


class TeachingReviewService:
    def __init__(self, certification, bank):
        self.certification, self.bank = certification, bank
        self.frq, self.rosters, self.path = certification.frq, certification.rosters, certification.path

    def catalog(self):
        with connect(self.path) as db:
            return {'assessments': [dict(row) for row in db.execute('''SELECT f.id,a.title,t.id term_id,d.code,t.is_current,
                t.term,t.term_other,t.year,(SELECT created_at FROM frq_certifications c WHERE c.assignment_id=f.id
                ORDER BY c.rowid DESC LIMIT 1) certified_at FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id
                JOIN assignments a ON a.id=e.assignment_id JOIN course_terms t ON t.id=e.term_id
                JOIN course_definitions d ON d.id=t.definition_id WHERE t.deleted_at IS NULL
                ORDER BY t.is_current DESC,t.year DESC,d.code,t.term,a.title,f.id''')]}

    def insights(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (plan['term_id'],)).fetchone()[0]
            latest = db.execute('SELECT id,created_at FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1', (aid,)).fetchone()
            view = {'plan_id': aid, 'title': plan['title'], 'term_id': plan['term_id'], 'definition_id': definition,
                    'certification': dict(latest) if latest else None, 'questions': [], 'objectives': [],
                    'excluded': {}, 'read_at': timestamp()}
            if not latest:
                return view
            questions = self.bank._latest(db, definition)
            bank_by_frq = {q['frq_question_id']: q for q in questions if q['frq_question_id']}
            bank_by_frq.update({alias: bank_by_frq[original] for alias, original in self.bank._quiz_aliases(db, definition).items()
                                if original in bank_by_frq})
            nodes = {n['id']: n for n in self.bank._outline(db, definition) if n['level'] == 'objective' and not n['retired']}
            # Pinned results, not the current result pointer. One read for the plan.
            evidence = {r['id']: dict(r) for r in db.execute('''SELECT x.id,x.response_id,x.score_json,t.source_json,
                t.question_revision_id,t.rubric_id,q.content_json,r.rules_json FROM frq_results x
                JOIN frq_tasks t ON t.id=x.task_id JOIN frq_responses p ON p.id=x.response_id
                JOIN frq_question_revisions q ON q.id=t.question_revision_id JOIN frq_rubric_revisions r ON r.id=t.rubric_id
                WHERE p.assignment_id=?''', (aid,))}
            reviews = {r['id']: dict(r) for r in db.execute('''SELECT v.id,v.response_id,v.result_id,v.rubric_id,v.score_json,r.rules_json
                FROM frq_reviews v JOIN frq_responses p ON p.id=v.response_id
                JOIN frq_rubric_revisions r ON r.id=v.rubric_id WHERE p.assignment_id=?''', (aid,))}
            page_messages = sorted({part.get('message_id') for row in evidence.values()
                                    for part in json.loads(row['source_json']).get('regions', []) if part.get('message_id')})
            present = {r[0] for r in db.execute(f'''SELECT m.id FROM inbox_messages m
                WHERE m.id IN (SELECT value FROM json_each(?)) AND {PRESENT.format('m.id')}''', (json.dumps(page_messages),))}
            groups, excluded = {}, Counter()
            for student in db.execute('SELECT status,parts_json FROM frq_certification_students WHERE certification_id=?', (latest['id'],)):
                if student['status'] != 'scored':
                    excluded[student['status']] += 1
                    continue
                for qid, score in (json.loads(student['parts_json']).get('frq', {}).get('questions') or {}).items():
                    record = evidence.get(score.get('result_id'))
                    if not record or record['response_id'] != score.get('response_id'):
                        excluded['missing_evidence'] += 1
                        continue
                    regions = json.loads(record['source_json']).get('regions') or []
                    if not regions or any(p.get('message_id') not in present for p in regions):
                        excluded['removed_or_unavailable_source'] += 1
                        continue
                    decision = score.get('decision', '')
                    if decision.startswith('review:'):
                        review = reviews.get(decision.split(':', 1)[1])
                        if not review or review['response_id'] != record['response_id'] or review['result_id'] != record['id']:
                            excluded['missing_evidence'] += 1
                            continue
                        record = {**record, **{k: review[k] for k in ('score_json', 'rubric_id', 'rules_json')}}
                    try:
                        earned, possible = float(score['score']), float(score['max'])
                        if possible <= 0:
                            raise ValueError
                    except (TypeError, ValueError, KeyError):
                        excluded['unscored_question'] += 1
                        continue
                    key = (qid, record['question_revision_id'], record['rubric_id'])
                    if key not in groups:
                        question = json.loads(record['content_json'])
                        bank = bank_by_frq.get(qid)
                        classification = (bank or {}).get('classification')
                        tags = [nodes[n] for n in json.loads(classification['nodes_json']) if n in nodes] if classification else []
                        groups[key] = {'question_id': qid, 'question_revision_id': key[1], 'rubric_id': key[2],
                            'title': question.get('title') or 'Question', 'statement': question.get('statement') or '',
                            'version': db.execute('SELECT exam_version FROM frq_questions WHERE id=?', (qid,)).fetchone()[0],
                            'bank_id': bank['id'] if bank else None, 'classification_revision': classification['revision'] if classification else None,
                            'objectives': [{'id': n['id'], 'title': n['title'], 'revision': n['revision']} for n in tags],
                            'n': 0, 'share': 0., 'below_full': 0, 'selections': Counter(), 'rubric': json.loads(record['rules_json'])}
                    group = groups[key]
                    group['n'] += 1
                    group['share'] += earned / possible
                    group['below_full'] += earned < possible
                    group['selections'].update(set(json.loads(record['score_json']).get('selected') or []))
            objectives = {}
            for group in groups.values():
                group['earned_pct'] = round(100 * group.pop('share') / group['n'], 1)
                selected, rubric = group.pop('selections'), group.pop('rubric')
                group['rubric_lines'] = [{'id': item['id'], 'description': item.get('description', ''), 'points': item.get('points'),
                                         'selected': selected[item['id']]} for item in rubric.get('items', [])]
                for tag in group['objectives']:
                    objective = objectives.setdefault(tag['id'], {**tag, 'observations': 0, 'weighted_pct': 0., 'questions': 0})
                    objective['observations'] += group['n']
                    objective['weighted_pct'] += group['earned_pct'] * group['n']
                    objective['questions'] += 1
            for objective in objectives.values():
                objective['earned_pct'] = round(objective.pop('weighted_pct') / objective['observations'], 1)
            view.update(questions=sorted(groups.values(), key=lambda q: (q['earned_pct'], q['version'], q['title'], q['question_revision_id'])),
                        objectives=sorted(objectives.values(), key=lambda o: (o['earned_pct'], o['title'], o['id'])), excluded=dict(excluded))
            return view

    def delivery(self, aid):
        cert = self.certification.view(aid)
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            setup, _ = _setup(db, plan['term_id'])
            cells = _cells(db, plan['term_id'])
            items = [item for item in setup['items'] if item.get('assignment_id') == plan['source_assignment_id'] and not item.get('archived')]
            # POSTED IN CANVAS (TEACHING-WORKFLOWS, requirement, 2026-10-11): what the gradebook last read of each column's posting in
            # Canvas, the grid's own mark (`canvas_posting.marks`: grades held, how many students do not see, in how many courses,
            # and when); None for a column never read. Catalog only, one query: Canvas is never asked here.
            posting = canvas_posting.marks(db, plan['term_id'])
            columns = []
            for item in items:
                current = cells.get(item['id'], {})
                receipts = {}
                for row in db.execute('SELECT * FROM gradebook_canvas_sent WHERE term_id=? AND item_id=? ORDER BY created_at,rowid', (plan['term_id'], item['id'])):
                    key = (row['course_id'], row['assignment_id'], row['section_id'], row['enrollment_id'])
                    receipts[key] = dict(row)
                counts, recorded = Counter(), set()
                for row in receipts.values():
                    key = common.row_key(row['section_id'], row['enrollment_id'])
                    recorded.add(key)
                    state = row['state']
                    sent, now = json.loads(row['sent_json']), canvas_export.faculty_value(current.get(key))
                    # ZEROS GO TO CANVAS: a 0 sent for an empty cell the gradebook counted as 0 is not a changed grade.
                    if state == 'accepted' and not canvas_export.same(now, sent) and not (now is None and sent and sent.get('zero')):
                        state = 'changed'
                    counts[state] += 1
                live = {key: cell for key, cell in current.items() if cell['status'] != 'cleared'}
                columns.append({'id': item['id'], 'title': item['title'], 'recorded_cells': len(live),
                    'without_receipt': len(set(live) - recorded), 'receipts': dict(counts),
                    'last_receipt': max((r['created_at'] for r in receipts.values()), default=None),
                    'posting': posting.get(item['id'])})
        return {'plan_id': aid, 'term_id': cert['term_id'], 'assignment_id': plan['source_assignment_id'], 'title': cert['title'], 'read_at': timestamp(),
                'certification': cert['certification'], 'counts': cert['counts'], 'gradescope': cert['gradescope'],
                'columns': columns, 'publications': self.certification.gradescope.publish_status(aid)['publications']}


def register(app, service):
    api = Blueprint('teaching_review', __name__, url_prefix='/api/teaching-review')
    api.add_url_rule('', view_func=lambda: jsonify(service.catalog()))
    api.add_url_rule('/<aid>', 'insights', lambda aid: jsonify(service.insights(aid)))
    api.add_url_rule('/<aid>/delivery', 'delivery', lambda aid: jsonify(service.delivery(aid)))
    app.register_blueprint(api)
