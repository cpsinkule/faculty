"""SCHEDULING "ONE FEED" (stage 1b, 2026-10-05): the calendar as Faculty knows it, assembled once for the Calendar page (/api/calendar),
makeup cards (absences/makeup_booking.py) and Google sync (google_calendar/service.py `_wanted`), which each built it by hand before.

`assemble` returns, unsorted and in this order: Faculty's own sources (`workspace_calendar.source_events`: sittings, seating charts, DR
bookings, gradebook deadlines, appointments), the instructor's custom events (less those a Term schedule in use replaced), the Term
schedules in use (stage 1d), then, when asked, Faculty's copy of their Google events.
Each caller sorts and filters as it always has."""
from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)


def assemble(db, start, end, *, calendar, google=None, on_google_error=None):
    from workspace_calendar import custom_event, source_events
    from .service import projection
    events = source_events(db, start, end)
    # USE THIS SCHEDULE (stage 1d): a term that uses its Term schedule shows it, and the per-date events it replaced stop showing.
    hidden, scheduled = projection(db, start, end)
    if calendar is not None:
        events.extend(event for event in (custom_event(row) for row in calendar.list(start, end)) if event["id"] not in hidden)
    events.extend(scheduled)
    if google is not None:
        try:
            events.extend(google.feed(start, end))
        except Exception:
            if on_google_error is None:
                raise
            on_google_error()
    return events


def google_gap(google, now=None):
    """SCHEDULING "GOOGLE CHECKED": why the instructor's Google calendar cannot vouch for a time now
    (`GoogleCalendarService.unchecked`), or None when it can or is not in use. Not knowing counts as unchecked."""
    if google is None:
        return None
    try:
        return google.unchecked(now)
    except Exception:
        LOG.exception("Whether Google Calendar was read could not be told")
        return "Faculty could not tell whether your Google Calendar was read"
