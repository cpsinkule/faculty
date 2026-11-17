"""GRADEBOOK / ASSISTANT: term read projection of retained source values; no network or writes.

Each source is compared by its owner's own rule (2026-10-09 review): Canvas as Sync with Canvas compares it (the columns Sync maps,
including Faculty's Canvas copy; the grade as entered or after its late penalty; Canvas's own rounding of Faculty's last send;
grades Canvas holds for its own review are unknown), iClicker against its latest imported values (a typed value over one is
marked, an intentional override), and Gradescope only where a section is linked. A grade never sent is counted, not called unknown; unknown is what an uncertain or
unreadable observation leaves open.
"""
import json
import zlib
from collections import Counter

from grading import participation
from rosters import connect
from . import canvas_export, canvas_snapshot, common, grading_check
from .service import _cells, _setup, _visible

UNSURE = ('pending', 'sending', 'uncertain')  # a Gradescope outcome still open: it supersedes what was seen before
SEEN = ('accepted', 'present')


def read(service, term_id):
    differences, unknown, sources = [], [], {}
    with service.rosters.lock():
        context = service._context(term_id)
        with connect(service.path) as db:
            setup, _ = _setup(db, term_id)
            cells = _cells(db, term_id)
            # ZEROS GO TO CANVAS (canvas_export): an empty cell the grid counts as 0 is Faculty's 0, and Sync sends it as one.
            shown = service._results(db, term_id, setup, context['students'], _visible(cells)).get('blanks') or {}
            zeros = {(item, row) for row, marks in shown.items() for item, why in marks.items() if why == 'zero'}
            items = {i['id']: i for i in setup['items'] if not i.get('archived')}
            students = {s['row']: s for s in context['students'] if not s.get('dropped')}

            def record(source, item, row, forms, at, state='observed', last_sent=None, **extra):
                """`forms`: the values the source shows (Canvas: as entered, then after its late penalty)."""
                if item not in items or row not in students:
                    return False
                if items[item].get('sections') and students[row]['section_id'] not in items[item]['sections']:
                    return False
                if source == 'canvas' and items[item].get('canvas') == 'off':
                    return False  # kept off Canvas: Sync's column map leaves it out in both directions (2026-10-09 review)
                cell = cells.get(item, {}).get(row) or {}
                current = canvas_export.faculty_value(cells.get(item, {}).get(row))
                if current is None and source == 'canvas' and (item, row) in zeros:
                    current = dict(canvas_export.ZERO)
                detail = {'source': source, 'item_id': item, 'title': items[item]['title'], 'row': row,
                          'section_id': students[row]['section_id'], 'enrollment_id': students[row]['enrollment_id'],
                          'faculty': current, 'observed': forms[-1] if forms else None, 'observed_at': at,
                          'faculty_source': cell.get('source'), 'faculty_note': cell.get('note'),
                          'kept_off_canvas': items[item].get('canvas') == 'off', **extra}
                if state != 'observed':
                    unknown.append({**detail, 'reason': state})
                elif not any(canvas_export.same(current, form) for form in forms) and not (
                        last_sent and canvas_export.same(last_sent, current) and any(canvas_export.close(current, form) for form in forms)):
                    # A Faculty change after Canvas was observed only waits for the next Sync (counted, not drift to fix).
                    pending = {'faculty_changed_since_sync': bool(canvas_export.owned(items[item]) and at and cell.get('at') and cell['at'] > at)
                               } if source == 'canvas' else {}
                    differences.append({**detail, 'typed_value': cell.get('source') == 'instructor', **pending,
                                        'note': 'Preserve manual values and existing source ownership; comparison only.'})
                return True

            latest = db.execute('''SELECT r.request_id,s.created_at FROM gradebook_canvas_snapshots s
                JOIN gradebook_requests r ON r.id=s.request_ref WHERE s.term_id=? ORDER BY s.created_at DESC,s.rowid DESC LIMIT 1''', (term_id,)).fetchone()
            # The same column ↔ assignment map Sync with Canvas uses, including assignments Faculty's Canvas copy made for a column.
            linked = {tuple(key.split(':', 1)): value['item_id']
                      for key, value in service._remembered(db, term_id, 'canvas', sync=True).items() if value.get('item_id')}
            canvas_rows = {}
            snapshot = None
            if latest:
                try:
                    snapshot = canvas_snapshot.load(db, term_id, latest['request_id'])
                except (ValueError, OSError, EOFError, zlib.error):
                    unknown.append({'source': 'canvas', 'reason': 'The retained Canvas snapshot could not be verified.', 'observed_at': latest['created_at']})
            if snapshot:
                for course, data in snapshot['courses'].items():
                    rows = {str(s['user_id']): s.get('row') for s in data['students'] if s.get('state') in ('active', 'invited')}
                    for sub in data['submissions']:
                        item, row = linked.get((course, str(sub['assignment_id']))), rows.get(str(sub['user_id']))
                        if item and row:
                            waiting = sub.get('state') in canvas_export.WAITING  # Sync leaves these alone too
                            canvas_rows[(item, row, course, str(sub['assignment_id']))] = {
                                'forms': canvas_export.shows(sub), 'at': latest['created_at'],
                                'state': 'Canvas holds this grade for its own review' if waiting else 'observed', 'sent': None}
            # The sync snapshot precedes its writes; their checked receipts are subsequent observations. A failed send changed
            # nothing in Canvas, so it never hides what was seen before (GradebookService._sent); an uncertain one does.
            for r in db.execute('SELECT * FROM gradebook_canvas_sent WHERE term_id=? ORDER BY created_at,rowid', (term_id,)):
                if (latest and r['created_at'] < latest['created_at']) or r['state'] == 'failed':
                    continue
                key = (r['item_id'], common.row_key(r['section_id'], r['enrollment_id']), r['course_id'], r['assignment_id'])
                canvas_rows[key] = {'forms': [json.loads(r['canvas_json'])], 'at': r['created_at'],
                                    'state': 'observed' if r['state'] == 'accepted' else r['state'],
                                    'sent': json.loads(r['sent_json']) if r['state'] == 'accepted' else None}
            compared = sum(record('canvas', item, row, seen['forms'], seen['at'], seen['state'], seen['sent'],
                                  course_id=course, remote_assignment_id=assignment)
                           for (item, row, course, assignment), seen in canvas_rows.items())
            sources['canvas'] = {'state': 'retained' if canvas_rows else 'unavailable',
                                 'observed_at': max((seen['at'] for seen in canvas_rows.values()), default=None), 'compared': compared}

            imported = {}
            for r in db.execute('''SELECT s.* FROM gradebook_scores s JOIN gradebook_requests r ON r.id=s.request_ref
                WHERE s.term_id=? AND json_extract(r.summary_json,'$.source')='iclicker' ORDER BY s.created_at,s.seq,s.id''', (term_id,)):
                imported[(r['item_id'], common.row_key(r['section_id'], r['enrollment_id']))] = dict(r)
            # The instructor's own value over an import (a hand edit, an excused absence's credit) is listed with typed_value,
            # and preserved; the daily review does not count it as drift (workflows.observe).
            compared = sum(record('iclicker', item, row, [canvas_export.faculty_value(value)], value['created_at'])
                           for (item, row), value in imported.items())
            sources['iclicker'] = {'state': 'retained' if imported else 'unavailable',
                                   'observed_at': max((r['created_at'] for r in imported.values()), default=None), 'compared': compared}
            linked_plans = {}
            for r in db.execute('''SELECT DISTINCT t.assignment_id,t.section_id FROM frq_gradescope_targets t
                JOIN frq_assignments f ON f.id=t.assignment_id JOIN exam_events e ON e.id=f.event_id
                WHERE e.term_id=? ORDER BY t.assignment_id,t.section_id''', (term_id,)):
                linked_plans.setdefault(r['assignment_id'], set()).add(r['section_id'])

    gs_count, gs_at, not_sent = 0, None, 0
    if service.frq:
        for plan_id, sections in linked_plans.items():
            # PERFORMANCE: final_decisions takes seconds on a big exam (grading_check). Its lifecycle lock is held for one plan
            # at a time, so the Inbox and grading workers interleave with a daily read or an assistant's question.
            with service.rosters.lock(), connect(service.path) as db:
                # Final-decisions owns source validation, including PRESENT for Inbox-deleted papers.
                # GRADING "GRADED FOR PARTICIPATION": compared as it counts, which is what Gradescope was sent.
                _, decisions, _ = participation.counted_decisions(db, plan_id, service.frq.final_decisions(db, plan_id))
                seen, still_open = {}, {}
                for r in db.execute('''SELECT response_id,status,receipt_json,updated_at FROM frq_gradescope_grades
                    WHERE assignment_id=? ORDER BY created_at,rowid''', (plan_id,)):
                    if r['status'] in SEEN:
                        seen[r['response_id']], still_open[r['response_id']] = dict(r), None
                    elif r['status'] in UNSURE:
                        still_open[r['response_id']] = dict(r)
                    # failed, refused, cancelled: Gradescope still shows what an earlier accepted send left there
            for d in decisions:
                response, final = d['row'], d['final']
                row = common.row_key(response['section_id'], response['enrollment_id'])
                if row not in students or response['section_id'] not in sections:
                    continue
                expected = {'status': 'scored', 'points': str(final['score']['score'])}
                receipt, pending = seen.get(response['id']), still_open.get(response['id'])
                score = json.loads(receipt['receipt_json'] or '{}').get('score') if receipt else None
                # The intended/expected send value is never substituted for a checked observation.
                observed = {'status': 'scored', 'points': str(score)} if score is not None else None
                detail = {'source': 'gradescope', 'plan_id': plan_id, 'response_id': response['id'],
                          'question_id': response['question_id'], 'row': row, 'faculty': expected, 'observed': observed,
                          'observed_at': (pending or receipt or {}).get('updated_at')}
                if pending:
                    unknown.append({**detail, 'reason': pending['status']})
                elif not receipt:
                    not_sent += 1
                    continue
                elif observed is None:
                    unknown.append({**detail, 'reason': 'the receipt holds no score'})
                elif not canvas_export.same(expected, observed):
                    differences.append(detail)
                gs_count += 1
                if detail['observed_at']:
                    gs_at = max(gs_at or '', detail['observed_at'])
    sources['gradescope'] = {'state': 'retained' if gs_at else 'unavailable', 'observed_at': gs_at, 'compared': gs_count,
                             'not_sent': not_sent, 'linked_plans': len(linked_plans)}
    grading = grading_check.read(service, term_id)
    return {'term_id': term_id, 'grading': grading, 'sources': sources,
            'differences': differences, 'unknown': unknown, 'counts': dict(Counter(r['source'] for r in differences)),
            'unknown_counts': dict(Counter(r.get('source') for r in unknown)),
            'note': 'Retained observations only; each date is when Faculty observed that source. No site was contacted. '
                    'Grades never sent to Gradescope are counted under sources, not as unknown.'}
