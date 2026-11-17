"""APPROVALS SIDE BY SIDE (ASSISTANT; requirement, 2026-10-31).

Every Approve used to hold the one approval lock from its first check to its last bookkeeping, previews, sends and a regrade's
25-45 s decision included, so a second follow-up's Approve all waited for the whole of the first. An approval now holds its LANE for
that time and the approval lock only for its short checks and records (`AssistantService.approve`):

- A follow-up's card runs in its student's lane (`student:<enrollment>`): one student's follow-ups still finalize one at a time, in
  order, so one never sees another half done.
- A card whose route is not in `PARALLEL` also takes the `serial` lane, as does every card that is no follow-up's (Assistant chat
  cards, batches, preparations): those still go one at a time among themselves, exactly as before.
- `PARALLEL` routes write only their own student's records, each in its own transaction under the lifecycle lock with a request ID
  that is never applied twice (a Mail send also under Mail's own send lock), so two students' approvals can share no record.

An approval that holds lanes takes its lanes before the approval lock and never asks for a lane it does not already hold (an Approve
all takes the lanes of all its cards at once, in one order), so lanes cannot deadlock."""
from __future__ import annotations

import contextlib
import threading

from rosters import connect

PARALLEL = frozenset({
    "gmail_mail.send",          # Mail's own send lock; a request ID is sent once
    "absences.create", "absences.revise", "absences.makeup_hold",
    "student_support.create", "student_support.revise",
    "student_notes.create",
})
SERIAL = "serial"


def lanes(db, action_ids):
    """The lanes these cards' approvals hold: each follow-up card its student's, plus `serial` for any other route or card."""
    keys = set()
    for action_id in action_ids:
        row = db.execute("""SELECT a.endpoint, r.enrollment_id FROM assistant_actions a
            LEFT JOIN student_support_suggestion_actions s ON s.action_id=a.id
            LEFT JOIN student_support_suggestion_runs r ON r.id=s.run_id WHERE a.id=?""", (action_id,)).fetchone()
        if row and row["enrollment_id"]:
            keys.add("student:" + row["enrollment_id"])
        if not row or not row["enrollment_id"] or row["endpoint"] not in PARALLEL:
            keys.add(SERIAL)
    return keys


class Lanes:
    """Re-entrant locks by key, made on first use and dropped when nobody holds or waits for them."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks = {}  # key -> [RLock, holders and waiters]
        self._held = threading.local()

    def of(self, database, action_ids):
        with connect(database) as db:
            return lanes(db, action_ids)

    @contextlib.contextmanager
    def hold(self, keys):
        held = getattr(self._held, "keys", None)
        if held is None:
            held = self._held.keys = []
        new = sorted(set(keys) - set(held))
        if new and held:
            # Never wait for a lane while holding another: that is how two approvals would wait for each other.
            raise RuntimeError(f"An approval asked for lanes {new} while holding {held}.")
        taken = []
        try:
            for key in new:
                with self._guard:
                    entry = self._locks.setdefault(key, [threading.RLock(), 0])
                    entry[1] += 1
                try:
                    entry[0].acquire()
                except BaseException:
                    self._release(key, entry, acquired=False)
                    raise
                taken.append((key, entry))
                held.append(key)
            yield
        finally:
            for key, entry in reversed(taken):
                held.remove(key)
                self._release(key, entry)

    def _release(self, key, entry, acquired=True):
        if acquired:
            entry[0].release()
        with self._guard:
            entry[1] -= 1
            if not entry[1] and self._locks.get(key) is entry:
                del self._locks[key]
