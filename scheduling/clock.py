"""SCHEDULING "ONE CLOCK": the one reader and writer of campus wall-clock times (stage 1a, 2026-10-05). Before it, nine parsers disagreed,
so a seating chart typed "7:05PM" was a time on the Calendar page but nothing to the makeup and meeting checks.

Pure; times are minutes after midnight, compared as written (campus wall clock, never converted). `parse_clock` has three modes:
- "hhmm": a stored field, exactly "HH:MM" (what forms and the catalog keep);
- "any": a clock as people type it ("19:05", "7:05 PM", "7pm", "7:05p.m."), the same rule as calendar-feed.js `clockMinutes`
  (tests/fixtures/scheduling/clock_cases.json holds both to it);
- "loose": a stored "HH:MM", else the first clock with am/pm inside text ("After lecture, around 2 PM"), for makeup times typed
  before Schedule makeup asked for an exact start.
The formatters keep the two house styles: "3:00–5:00 pm" in scheduling text (`twelve`, `span_text`) and "2:00 PM" in cards
(`display_clock`)."""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

# The campus zone, once (stage 1h, 2026-10-05): before it, fourteen modules and two scripts each named "America/Chicago".
ZONE_NAME = "America/Chicago"
CAMPUS_ZONE = ZoneInfo(ZONE_NAME)

HHMM = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_TWELVE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?", re.I)
_PLAIN = re.compile(r"(\d{1,2}):(\d{2})")
_LOOSE = re.compile(r"(?<![:\d])\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s*m\b\.?", re.I)
_DASH = re.compile(r"\s*(?:–|—|-|\bto\b)\s*", re.I)
MODES = ("hhmm", "any", "loose")


def _hhmm(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":" or not (value[:2] + value[3:]).isdigit():
        return None
    hours, minutes = int(value[:2]), int(value[3:])
    return hours * 60 + minutes if hours < 24 and minutes < 60 else None


def _twelve_hour(hour, minute, meridiem):
    return (int(hour) % 12 + (12 if meridiem.lower() == "p" else 0)) * 60 + int(minute or 0)


def _any(value):
    text = str(value or "").strip()
    twelve = _TWELVE.fullmatch(text)
    if twelve and 1 <= int(twelve[1]) <= 12 and int(twelve[2] or 0) < 60:
        return _twelve_hour(twelve[1], twelve[2], twelve[3])
    plain = _PLAIN.fullmatch(text)
    return int(plain[1]) * 60 + int(plain[2]) if plain and int(plain[1]) < 24 and int(plain[2]) < 60 else None


def _loose(value):
    exact = _hhmm(value)
    if exact is not None or not isinstance(value, str):
        return exact
    ranged = parse_span(value)  # a typed range starts at its start, not at the clock that carries the am/pm
    if ranged and ranged[1] is not None:
        return ranged[0]
    found = _LOOSE.search(value)
    if not found:
        return None
    return _twelve_hour(found.group(1), found.group(2), found.group(3))


def parse_clock(value, mode="hhmm"):
    """Minutes after midnight, or None when `value` is not a clock in that mode."""
    if mode == "hhmm":
        return _hhmm(value)
    if mode == "any":
        return _any(value)
    if mode == "loose":
        return _loose(value)
    raise ValueError(f"Unknown clock mode {mode!r}.")


def parse_span(value):
    """(start, end) minutes of a typed range ("7:05–8:55 PM", "13:00-17:00", "9–10 AM"), a bare start borrowing the end's am/pm when
    that keeps it first ("11:30–1:00 PM" starts at 11:30 am); (start, None) for a single clock; None when nothing can be read."""
    text = str(value or "").strip()
    single = _any(text)
    if single is not None:
        return single, None
    parts = _DASH.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    first, second = parts[0].strip(), parts[1].strip()
    end, start = _any(second), _any(first)
    if end is None:
        return None
    meridiem = re.search(r"([ap])\.?\s*m\.?$", second, re.I)
    bare = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", first)
    if meridiem and bare and 1 <= int(bare[1]) <= 12 and int(bare[2] or 0) < 60:
        borrowed = _twelve_hour(bare[1], bare[2], meridiem[1])
        if borrowed < end:
            start = borrowed
    return (start, end) if start is not None and end > start else None


def hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def twelve(minutes, meridiem=True):
    """780 → "1:00 pm" (scheduling text)."""
    hours, mins = divmod(minutes, 60)
    text = f"{(hours - 1) % 12 + 1}:{mins:02d}"
    return f"{text} {'am' if hours < 12 else 'pm'}" if meridiem else text


def span_text(start, end):
    """3:00–5:00 pm, or 10:30 am–12:00 pm."""
    same = (start < 12 * 60) == (end < 12 * 60)
    return f"{twelve(start, not same)}–{twelve(end)}"


def display_clock(value):
    """"14:00" → "2:00 PM" (cards and Mail); text that is not a clock comes back as written."""
    found = _any(value)
    if found is None:
        return str(value or "")
    hours, mins = divmod(found, 60)
    return f"{hours % 12 or 12}:{mins:02d} {'AM' if hours < 12 else 'PM'}"


def display_span(start, end):
    """"3:00–5:00 PM", or "10:30 AM–12:00 PM" (cards and Canvas text), from two clocks; text that is not a clock as written."""
    first, last = _any(start), _any(end)
    if first is None or last is None:
        return "–".join(part for part in (display_clock(start), display_clock(end)) if part)
    same = (first < 12 * 60) == (last < 12 * 60)
    return f"{display_clock(start).rsplit(' ', 1)[0] if same else display_clock(start)}–{display_clock(end)}"


def campus_now():
    """The campus wall-clock moment now (zone-aware)."""
    return dt.datetime.now(CAMPUS_ZONE)


def campus_today():
    """Today's campus date (the one that matters at 7 pm, when UTC is already tomorrow)."""
    return campus_now().date()


# BUSINESS DAYS (requirement, 2026-10-05): Texas A&M
# University's holidays, as the Texas A&M System lists them for each fiscal year (https://www.tamus.edu/hr/holiday/, System Policy
# 31.04; set by the Board of Regents). Fiscal year 2027: Labor Day; Thanksgiving; Winter Break; Martin Luther King, Jr. Day; Spring
# Break (the Thursday and Friday); Memorial Day. Add each new fiscal year's list here before it starts; until then a later date
# counts Monday to Friday only.
UNIVERSITY_HOLIDAYS = frozenset({
    "2026-09-07", "2026-11-26", "2026-11-27",
    "2026-12-24", "2026-12-25", "2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31", "2027-01-01",
    "2027-01-18", "2027-03-18", "2027-03-19", "2027-05-31",
})
HOLIDAYS_THROUGH = "2027-08-31"  # the last day of the latest fiscal year listed above


def business_day(day):
    """True for a Monday to Friday that is not a university holiday (`day` a date)."""
    return day.weekday() < 5 and day.isoformat() not in UNIVERSITY_HOLIDAYS


def business_days_after(day, count):
    """The date `count` business days after `day` (a date), e.g. Rule 7's "the second business day after the last date"."""
    counted = 0
    while counted < count:
        day += dt.timedelta(days=1)
        counted += business_day(day)
    return day


def day_label(day):
    """2026-10-05 → "Mon, Oct 5"."""
    return f"{day:%a}, {day:%b} {day.day}"


def long_day(value):
    """"2026-10-05" → "Monday, October 5"."""
    day = dt.date.fromisoformat(value)
    return f"{day:%A}, {day:%B} {day.day}"
