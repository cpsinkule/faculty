"""Workspace-owned, bounded observation of existing receipts; never a model-quality claim."""
import json
from datetime import datetime, timedelta, timezone
from rosters import connect
from .outcomes import Outcomes


def read(service):
    with connect(service.database) as db:
        rows = list(db.execute('''SELECT a.id,a.endpoint,a.tier,a.created_at,e.kind,e.detail_json,e.created_at resolved_at FROM assistant_actions a
            JOIN assistant_action_events e ON e.id=(SELECT MAX(id) FROM assistant_action_events WHERE action_id=a.id)
            ORDER BY a.created_at DESC,a.rowid DESC LIMIT 500'''))
        reader = Outcomes(service, db)
        outcomes = {r['id']: reader.read(r['endpoint'], r['kind'], r['detail_json']) for r in rows}
        edits = list(db.execute('''SELECT action_id,edited_action_id FROM student_support_suggestion_edits
            ORDER BY created_at DESC,rowid DESC LIMIT 500'''))
        edited, changed = {r[0] for r in edits}, {v for r in edits for v in r}
        questions = sum(bool(json.loads(r[0]).get('question')) for r in db.execute('''SELECT e.detail_json
            FROM student_support_suggestion_events e WHERE e.state='needs_input' ORDER BY e.id DESC LIMIT 200'''))
        # A ready run is unfinished only while a card of it still waits (unexpired: service.EXPIRY) or its next stage waits; a
        # reviewed one has no terminal state of its own (2026-10-09 review).
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        states = [(r[0], bool(r[1])) for r in db.execute('''SELECT e.state,
            EXISTS(SELECT 1 FROM assistant_plan_continuations c WHERE c.run_id=p.id AND c.state='waiting') OR
            EXISTS(SELECT 1 FROM assistant_preparation_actions x JOIN assistant_actions a ON a.id=x.action_id WHERE x.run_id=p.id
                   AND a.tier='approve' AND a.created_at>=? AND (SELECT kind FROM assistant_action_events v WHERE v.action_id=a.id
                   ORDER BY v.id DESC LIMIT 1) IN ('proposed','previewed'))
            FROM assistant_preparations p JOIN assistant_preparation_events e
            ON e.id=(SELECT MAX(id) FROM assistant_preparation_events WHERE run_id=p.id) ORDER BY p.rowid DESC LIMIT 200''', (cutoff,))]
    def hours(stamp, end=None):
        try:
            start = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            finish = datetime.fromisoformat(end.replace('Z', '+00:00')) if end else datetime.now(timezone.utc)
            return max(0, (finish - start).total_seconds() / 3600)
        except (ValueError, TypeError):
            return 0
    # Cards that wait for the instructor. One Faculty is still carrying out (`started`) waits for nobody's review.
    waiting = [r for r in rows if r['kind'] in ('proposed', 'previewed', 'approved')]
    # The instructor's own review time: automatic actions succeed at once and are not reviewed.
    durations = [hours(r['created_at'], r['resolved_at']) for r in rows if r['tier'] == 'approve' and r['kind'] in ('succeeded', 'declined')]
    return {'sample_actions': len(rows), 'approved_unchanged': sum(r['tier'] == 'approve' and outcomes[r['id']] == 'succeeded' and r['id'] not in changed for r in rows),
            'edited': sum(r['id'] in edited for r in rows),
            'declined': sum(r['kind'] == 'declined' and not any(json.loads(r['detail_json']).get(k)
                            for k in ('replaced_by_request', 'replaced_by_edit')) for r in rows),
            'failed_or_uncertain': sum(outcomes[r['id']] in ('failed', 'uncertain', 'partial') for r in rows),
            'waiting': len(waiting),
            'clarifications': questions,
            'waiting_over_day': sum(hours(r['created_at']) >= 24 for r in waiting),
            'average_review_hours': round(sum(durations) / len(durations), 2) if durations else None,
            'unfinished_preparations': sum(s in ('queued', 'running', 'needs_input') or (s == 'ready' and waits) for s, waits in states),
            'stopped_or_failed_preparations': sum(s in ('interrupted', 'failed') for s, _ in states),
            'note': 'Latest 500 action receipts and 200 clarification events. Counts describe review activity, not model accuracy.'}
