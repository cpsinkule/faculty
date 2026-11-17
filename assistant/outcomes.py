"""ASSISTANT: one read of an action's outcome, without polling or performing work.

Workspace-owned projection; receipts remain with their existing owners. A route's
successful HTTP response may only acknowledge a start. Record fields such as an
absence's `status: excused` are not operation outcomes.

The words it answers with: `succeeded`; `started` (Faculty is still doing it: not
done, and not a problem); `partial` (some of it was done); `failed`; `uncertain`
(Faculty does not know whether it reached the outside service); `cancelled` (the
instructor stopped it before anything was done); else the event's own kind.
"""
import json
import logging
from contextlib import contextmanager


PUSHES = {'canvas.content_push': 'canvas_content_pushes', 'canvas.section_content_push': 'canvas_section_pushes'}
SENDS = {'gmail_mail.send': ('gmail_mail_outcomes', 'send_id', 'id'),
         'frq_regrades.reply': ('frq_regrade_reply_outcomes', 'reply_id', 'created_at,rowid')}
REGRADES = {'frq_regrade_panel.decide', 'frq_regrade_panel.apply'}
CELLS = {'gradebook.canvas_cell_send', 'gradebook.canvas_quiz_grade'}
# Making an assignment on Gradescope answers `pending`: its sender then makes it course by course (GRADESCOPE).
CREATE = 'frq_gradescope.create'
# An announcement answers once every course's post has its answer (canvas/announcements.py): its receipts say how it ended.
ANNOUNCE = 'canvas.announcement_post'
OPERATIONS = set(PUSHES) | set(SENDS) | REGRADES | CELLS | {'canvas.message_send', ANNOUNCE, CREATE}
# Sync with Canvas answers once its send has ended, or with no send when nothing was owed: it is done unless its send says not.
SYNC = 'gradebook.canvas_sync'
RECEIPTS = OPERATIONS | {SYNC}  # routes whose outcome a local receipt can still change after the action's own event
_logged = set()


def state(value):
    if value in ('accepted', 'posted', 'done', 'complete', 'completed'):
        return 'succeeded'
    if value in ('failed', 'stopped'):
        return 'failed'
    if value in ('cancelled', 'interrupted', 'uncertain'):
        return value if value != 'interrupted' else 'uncertain'
    if value in ('mixed', 'partial'):
        return 'partial'
    return 'started'


def link(data):
    """What a result too long to keep whole (`AssistantService._brief`) must still carry for `read`: the ids and states its
    receipts are found by, never its words. Without them a long result could never be confirmed (2026-10-13 review: a Canvas
    message's result repeats its text)."""
    if not isinstance(data, dict):
        return {}
    kept = {key: data[key] for key in ('id', 'creation_id', 'status') if isinstance(data.get(key), (str, int))}
    for key, fields in (('apply', ('id',)), ('send', ('id', 'status')), ('receipt', ('state',))):
        part = data.get(key)
        found = {field: part[field] for field in fields if isinstance(part.get(field), (str, int))} if isinstance(part, dict) else {}
        if found:
            kept[key] = found
    return kept


def settled(endpoint, recorded):
    """Whether the action's own event already holds its final outcome, so no receipt needs reading: a send Gmail or
    Gradescope accepted as the route answered. Nothing is recorded for that send afterwards."""
    return endpoint in SENDS and recorded == 'succeeded'


class Outcomes:
    def __init__(self, service=None, db=None):
        self.service, self.db = service, db

    def read(self, endpoint, kind, detail=None):
        """How the action ended, in one word. A receipt that cannot be read never fails the page, the search or the look that
        asked (2026-10-13 review): the action then reads as its own event recorded it."""
        try:
            return self._read(endpoint, kind, detail, self.db)
        except Exception:  # noqa: BLE001
            if endpoint not in _logged:  # once a route: this is asked for every card of every page read
                _logged.add(endpoint)
                logger = self.service.app.logger if self.service is not None else logging.getLogger(__name__)
                logger.exception('ASSISTANT: the receipts of %s could not be read; its actions read as recorded', endpoint)
        try:
            return self._read(endpoint, kind, detail, None)
        except Exception:  # noqa: BLE001 - an event that cannot be read either is not known to have finished
            return 'started' if kind == 'succeeded' and endpoint in OPERATIONS else kind

    def _read(self, endpoint, kind, detail, db):
        if kind != 'succeeded':
            return kind
        if isinstance(detail, str):
            detail = json.loads(detail)
        result = (detail or {}).get('result') or {}
        if endpoint == SYNC:
            send = result.get('send') if isinstance(result, dict) else None
            if not isinstance(send, dict) or not (send.get('id') or send.get('status')):
                return kind  # nothing was sent, or a receipt of before `link` that names no send: as recorded
            status = send.get('status')
            if db is not None and send.get('id'):
                row = db.execute('SELECT status FROM gradebook_canvas_sends WHERE id=?', (send['id'],)).fetchone()
                status = row[0] if row else status
            return state(status)
        if endpoint not in OPERATIONS:
            # Preserve explicit failure signals on other routes without treating
            # ordinary record states (pending, excused, etc.) as operation states.
            failure = result.get('status') if isinstance(result, dict) else None
            return failure if failure in ('failed', 'uncertain') else kind
        if not isinstance(result, dict):
            return 'started'
        status, ident = result.get('status'), result.get('id')
        if endpoint in PUSHES:
            progress = result.get('progress') or {}
            if db is not None and ident:
                row = db.execute(f'SELECT status,progress_json FROM {PUSHES[endpoint]} WHERE id=?', (ident,)).fetchone()
                if row:
                    status, progress = row[0], json.loads(row[1] or '{}')
            courses = [c for c in ((progress.get('courses') or {}) if isinstance(progress, dict) else {}).values() if isinstance(c, dict)]
            some, failed = any(c.get('done', 0) for c in courses), any(c.get('failed', 0) for c in courses)
            # Canvas's `done` means the worker ended; some objects may have failed.
            if status == 'done' and failed:
                return 'partial' if some else 'failed'
            # Ended early with objects already made in Canvas: part of it was done, never "failed" or "cancelled" alone.
            if status in ('failed', 'cancelled') and some:
                return 'partial'
        elif endpoint in SENDS and db is not None and ident:
            table, key, order = SENDS[endpoint]
            order = ','.join(f'{column} DESC' for column in order.split(','))
            row = db.execute(f'SELECT status FROM {table} WHERE {key}=? ORDER BY {order} LIMIT 1', (ident,)).fetchone()
            status = row[0] if row else status
        elif endpoint == 'canvas.message_send' and db is not None and ident:
            statuses = [r[0] for r in db.execute('''SELECT (SELECT status FROM canvas_message_outcomes o
                WHERE o.delivery_id=d.id ORDER BY o.id DESC LIMIT 1) FROM canvas_message_deliveries d
                WHERE d.message_id=? ORDER BY d.id''', (ident,))]
            if statuses:
                if None in statuses:
                    status = 'sending'
                elif len(set(statuses)) == 1:
                    status = statuses[0]
                else:
                    # A course Canvas did not confirm is not known to have missed the message: never "partly", which reads
                    # as known and invites a second send by hand.
                    status = 'uncertain' if 'uncertain' in statuses else 'mixed'
        elif endpoint == ANNOUNCE and db is not None and ident:
            statuses = [r[0] for r in db.execute('''SELECT (SELECT status FROM canvas_announcement_outcomes o
                WHERE o.post_id=p.id ORDER BY o.id DESC LIMIT 1) FROM canvas_announcement_posts p
                WHERE p.announcement_id=? ORDER BY p.id''', (ident,))]
            if statuses:
                # A post with no answer was interrupted (the route answers only after every post): uncertain, never resent.
                statuses = ['uncertain' if s is None else s for s in statuses]
                status = statuses[0] if len(set(statuses)) == 1 else 'uncertain' if 'uncertain' in statuses else 'mixed'
        elif endpoint in REGRADES:
            apply = result.get('apply') or result
            panel = self.service.app.extensions.get('regrade_panel') if self.service else None
            if db is not None and panel and apply.get('id'):
                run = db.execute('SELECT * FROM frq_regrade_applies WHERE id=?', (apply['id'],)).fetchone()
                if run:
                    apply = panel._view(db, run)  # The owner's pure projection of its local step receipts.
            steps = [step.get('status') for step in apply.get('steps', [])]
            some = any(s in ('done', 'already') for s in steps)
            if steps and all(s in ('done', 'already') for s in steps):
                return 'succeeded'
            # Cancelled or stopped after a step was done (the grade already on Gradescope) is part done, not nothing done.
            if 'cancelled' in steps:
                return 'partial' if some else 'cancelled'
            if 'stopped' in steps:
                return 'partial' if some else 'failed'
            return 'started'
        elif endpoint == CREATE:
            units, made = [], False
            if db is not None and result.get('creation_id'):
                row = db.execute('SELECT status FROM frq_gradescope_creations WHERE id=?', (result['creation_id'],)).fetchone()
                if row:
                    status = row[0]
                    units = [r[0] for r in db.execute('SELECT status FROM frq_gradescope_creation_units WHERE send_id=?', (result['creation_id'],))]
                    made = 'complete' in units
            # Gradescope's `complete` means the sender ended: a course whose assignment stopped is `failed` until Retry.
            if status == 'complete' and any(unit != 'complete' for unit in units):
                return 'partial' if made else 'failed'
            # Cancelled after a course's assignment was made: Faculty deletes nothing on Gradescope, so part of it was done.
            if status == 'cancelled' and made:
                return 'partial'
        elif endpoint in CELLS:
            receipt = result.get('receipt') or {}
            status = receipt.get('state') or status
            send = result.get('send') or {}
            if db is not None and send.get('id'):
                row = db.execute('''SELECT state FROM gradebook_canvas_sent WHERE send_id=?
                    ORDER BY created_at DESC,rowid DESC LIMIT 1''', (send['id'],)).fetchone()
                status = row[0] if row else status
        return state(status)

    @contextmanager
    def sql(self):
        """Use the same reader while selecting confirmed corrections, before LIMIT.

        No pending candidate can starve later confirmations. The function lives on
        this connection only and reads local receipts on the same snapshot.
        """
        self.db.create_function('assistant_confirmed', 3,
                                lambda endpoint, kind, detail: int(self.read(endpoint, kind, detail) == 'succeeded'))
        try:
            yield 'assistant_confirmed(a.endpoint,e.kind,e.detail_json)=1'
        finally:
            # PERFORMANCE: pooled connections must not retain this reader or its service.
            self.db.create_function('assistant_confirmed', 3, None)


def of_view(action):
    if 'outcome' in action:
        return action['outcome']
    events = action.get('events') or []
    return Outcomes().read(action['endpoint'], action['status'], events[-1].get('detail') if events else None)
