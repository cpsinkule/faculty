"""Enrollment-owned follow-up plans, projected from the existing immutable actions.

ASSISTANT / TEACHING-WORKFLOWS: prepare once; perform work before confirming it
to the student. A successful HTTP request is not proof that mail was delivered.
"""
from .outcomes import of_view


def tracking(action):
    return action['endpoint'] in ('student_support.create', 'student_support.revise') and (
        action['body'].get('kind') == 'followup' or action['endpoint'] == 'student_support.revise'
        and action['body'].get('state') in ('resolved', 'waiting', 'todo', 'snoozed', 'needs_reply'))


def stage(action):
    # ACTION DESK: recording outstanding work is a prerequisite, not an after-send status update.
    if tracking(action) and action['body'].get('commitments') and action['body'].get('state') not in ('waiting', 'resolved'):
        return 0
    return 2 if tracking(action) else 1 if action['endpoint'] == 'gmail_mail.send' else 0


def ordered(actions):
    # Stable within each stage: creation/review order is significant for local edits.
    return sorted(actions, key=stage)


def running(action):
    """Approved and started, and Faculty is still doing it (a Canvas push, a Canvas message, a regrade's Apply): not done, so
    what depends on it waits, and not a problem, so nothing is stopped, held or flagged for it (2026-10-13 review: counted as
    a problem, a push still running stopped its plan's next stage for good)."""
    return not action.get('replaced') and of_view(action) == 'started'


def cancelled(action):
    """The instructor stopped it before anything was done: no longer part of the plan, as a declined step is not."""
    return not action.get('replaced') and of_view(action) == 'cancelled'


def problem(action):
    if action.get('replaced'):
        return action['replaced']['status'] != 'accepted'
    return of_view(action) in ('failed', 'approved', 'uncertain', 'partial')


def unsettled(actions):
    return any(a['pending'] or running(a) or problem(a) for a in actions)


# What the instructor reads when a card is approved while the step before it is still `running`.
STILL_RUNNING = 'ASSISTANT: the step before this one is still running. Approve this once that step shows Done; nothing was submitted for it.'
