"""What the instructor already did, read from the copy: the implicit labels of the real cases (ids and states, never text)."""
import json

from communications.record import utc
from student_support.service import conversation_key, joins, regrade_role

TIMELINE = '''SELECT m.id,m.channel,r.revision,r.sent_at,r.author_role,r.context_json,m.external_id,m.thread_key,r.captured_at,
    l.enrollment_id,MAX(CASE WHEN l.role='author' THEN 1 ELSE 0 END) student_author
    FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
    JOIN comm_links l ON l.message_id=m.id JOIN course_terms t ON t.id=l.term_id
    WHERE t.deleted_at IS NULL AND m.channel IN ('gmail','canvas_message','canvas_comment','gradescope_regrade') AND l.enrollment_id=?
    GROUP BY m.id,l.enrollment_id ORDER BY r.sent_at,m.id'''


def timeline(db, enrollment):
    """{conversation key: [{id, revision, role, channel, sent_at, captured_at}]} for one student, grouped as the triage snapshots
    group them (EMAIL REPLY JOINS and SAME MATTER applied), oldest first."""
    rows = db.execute(TIMELINE, (enrollment,)).fetchall()
    keyed = [(row, conversation_key(row, json.loads(row['context_json']))) for row in rows]
    joined = joins(db, {(row['enrollment_id'], key) for row, key in keyed})
    grouped = {}
    for row, key in keyed:
        role = regrade_role(row)
        if role not in ('student', 'instructor') or role == 'student' and not row['student_author']:
            continue
        grouped.setdefault(joined.get((enrollment, key), key), []).append(
            {'id': row['id'], 'revision': row['revision'], 'role': role, 'channel': row['channel'],
             'sent_at': row['sent_at'], 'captured_at': row['captured_at']})
    for messages in grouped.values():
        messages.sort(key=lambda m: (utc(m['sent_at']), m['id']))
    return grouped


def revisions(db, item_id):
    """[{revision, state, title, next, actor, created_at}] of a follow-up, oldest first."""
    found = []
    for row in db.execute('SELECT revision,content_json,actor,created_at FROM student_support_revisions WHERE item_id=? ORDER BY revision',
                          (item_id,)):
        content = json.loads(row['content_json'])
        found.append({'revision': row['revision'], 'state': content.get('state'), 'title': content.get('title') or '',
                      'next': content.get('next') or '', 'actor': row['actor'], 'created_at': row['created_at']})
    return found


def at(history, moment):
    """The latest revision written at or before `moment`, or None."""
    kept = [r for r in history if utc(r['created_at']) <= utc(moment)]
    return kept[-1] if kept else None


def between(history, start, end):
    """Revisions written after `start` and at or before `end` (end None: no bound)."""
    return [r for r in history if utc(r['created_at']) > utc(start) and (end is None or utc(r['created_at']) <= utc(end))]


def strength(actor):
    """The instructor's own change is strong; one the Assistant made (an approved card or Done for you) is medium."""
    return 'strong' if actor == 'workspace' else 'medium'


def final_event(db, action_id):
    row = db.execute('SELECT kind,actor,detail_json,created_at FROM assistant_action_events WHERE action_id=? ORDER BY rowid DESC LIMIT 1',
                     (action_id,)).fetchone()
    return dict(row) if row else None
