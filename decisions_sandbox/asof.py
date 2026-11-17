"""The copy as it stood at a moment T: TEMP tables named like the history tables shadow them (an unqualified name resolves to temp
before main), holding only rows written at or before T, in rowid order (the projections read "latest" by MAX(rowid)). The site
code that opens its own connection (`connect(path)`) is patched to this one, and the follow-up projection's clock reads T."""
from contextlib import contextmanager, ExitStack
from datetime import datetime
from unittest.mock import patch

from scheduling.clock import CAMPUS_ZONE

from .catalog_copy import open_copy

# table: the column that says when its row was written
TIMED = {
    'comm_messages': 'captured_at', 'comm_revisions': 'captured_at', 'comm_links': 'created_at',
    'student_support_items': 'created_at', 'student_support_revisions': 'created_at', 'student_support_requests': 'created_at',
    'student_support_conversation_joins': 'created_at', 'student_support_conversation_mail': 'created_at',
    'student_support_suggestion_runs': 'created_at', 'student_support_suggestion_events': 'created_at',
    'student_support_suggestion_edits': 'created_at', 'student_support_suggestion_undos': 'created_at',
    'student_support_suggestion_replacements': 'created_at', 'student_support_suggestion_notes': 'created_at',
    'student_support_wellbeing_reviews': 'created_at', 'student_support_meeting_hours': 'created_at',
    'student_support_term_policies': 'created_at',
    'assistant_actions': 'created_at', 'assistant_action_events': 'created_at',
    'gmail_mail_sends': 'created_at', 'gmail_mail_outcomes': 'created_at',
    'absences': 'created_at', 'absence_revisions': 'created_at', 'absence_documents': 'created_at',
    'student_notes': 'created_at', 'student_note_revisions': 'created_at',
}


def at(path, moment):
    """A read-only connection to the copy that sees it as of `moment` (an ISO instant)."""
    db = open_copy(path)
    present = {row[0] for row in db.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    for table, column in TIMED.items():
        if table in present:
            db.execute(f'CREATE TEMP TABLE "{table}" AS SELECT * FROM main."{table}" WHERE julianday({column})<=julianday(?) ORDER BY rowid',
                       (moment,))
    if 'student_support_suggestion_actions' in present:
        db.execute('''CREATE TEMP TABLE student_support_suggestion_actions AS SELECT a.* FROM main.student_support_suggestion_actions a
            WHERE a.run_id IN (SELECT id FROM temp.student_support_suggestion_runs) AND a.action_id IN (SELECT id FROM temp.assistant_actions)
            ORDER BY a.rowid''')
    return db


class Held:
    """A connection used as `with connect(path) as db:` by site code: yields the as-of connection, never closes it."""
    def __init__(self, db):
        self.db = db

    @contextmanager
    def __call__(self, path=None):
        yield self.db


def clock(moment):
    value = datetime.fromisoformat(moment.replace('Z', '+00:00')).astimezone(CAMPUS_ZONE)
    return lambda *args, **kwargs: value


@contextmanager
def viewing(db, moment):
    """Site code's own `connect` and the follow-up clock, pointed at `db` as of `moment`."""
    held = Held(db)
    with ExitStack() as stack:
        stack.enter_context(patch('student_support.service.now', clock(moment)))
        stack.enter_context(patch('assistant.followups.connect', held))
        yield db
