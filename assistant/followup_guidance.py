"""ASSISTANT LEARN FROM EDITS: term-owned, read-only proposals from retained edits.

Only the instructor's repeated wording is proposed. Reading never saves a policy;
the existing term-policy editor owns adoption and its exact revision receipt.
"""
import difflib
import json
import re
from collections import defaultdict

from .evidence import digest


def sentences(body):
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', body) if s.strip()]


def suggestions(db, term_id, current):
    # Bounded retained-history read, with a pinned order (PERFORMANCE).
    rows = db.execute('''SELECT r.enrollment_id,r.item_key,e.action_id,e.created_at,
        a.body_json before_json,b.body_json after_json
        FROM student_support_suggestion_edits e JOIN student_support_suggestion_runs r ON r.id=e.run_id
        JOIN student_enrollments s ON s.id=r.enrollment_id JOIN assistant_actions a ON a.id=e.action_id
        JOIN assistant_actions b ON b.id=e.edited_action_id
        WHERE s.term_id=? AND a.endpoint='gmail_mail.send'
        ORDER BY e.created_at DESC,e.action_id DESC LIMIT 300''', (term_id,)).fetchall()
    patterns = defaultdict(list)
    for row in rows:
        before = sentences(json.loads(row['before_json']).get('body', ''))
        after = sentences(json.loads(row['after_json']).get('body', ''))
        for tag, i, j, k, l in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
            if tag not in ('replace', 'insert'):
                continue
            replacement = ' '.join(after[k:l])
            if not 20 <= len(replacement) <= 700 or len(replacement.split()) < 4:
                continue
            if re.search(r'@|\b\d{9}\b|https?://', replacement):
                continue
            patterns[replacement].append((row['enrollment_id'], row['item_key'], row['action_id'], ' '.join(before[i:j])[:700]))
    result = []
    for replacement, uses in patterns.items():
        cases = {(u[0], u[1]) for u in uses}
        # Two different students prevent repeated edits of one personal case from
        # looking like general guidance. The proposal still needs human review.
        if len({u[0] for u in uses}) < 2 or replacement in current:
            continue
        result.append({'id': digest([term_id, replacement]), 'wording': replacement,
                       'before': uses[0][3], 'cases': len(cases), 'source_actions': sorted({u[2] for u in uses}),
                       'guidance': 'For similar requests, use this wording: ' + replacement})
    return sorted(result, key=lambda p: (-p['cases'], p['id']))[:8]
