"""SCHEDULING "ONE CLOCK" (scheduling/clock.py, stage 1a, 2026-10-05): every clock reading and the two house formats. The cases live in
tests/fixtures/scheduling/clock_cases.json, which tests/test_clock_cases.mjs holds calendar-feed.js clockMinutes to as well ("any")."""
import json
import unittest
from pathlib import Path

from scheduling import clock

CASES = json.loads((Path(__file__).parent / "fixtures" / "scheduling" / "clock_cases.json").read_text())["cases"]


class ClockTests(unittest.TestCase):
    def test_every_case_in_every_mode(self):
        for case in CASES:
            for mode in clock.MODES:
                self.assertEqual(clock.parse_clock(case["text"], mode), case[mode], (case["text"], mode))
            found = clock.parse_span(case["text"])
            self.assertEqual(list(found) if found else None, case["span"], case["text"])

    def test_the_old_helpers_are_the_one_clock(self):
        # Stage 1a moved each module's parser here; the old names still answer the same.
        from absences import makeup_slots
        from assistant import followup_effects
        from google_calendar import service as google
        from student_support import meeting_hours
        for case in CASES:
            self.assertEqual(makeup_slots.clock(case["text"]), case["hhmm"], case["text"])
            self.assertEqual(makeup_slots.loose_clock(case["text"]), case["loose"], case["text"])
            self.assertEqual(google.clock(case["text"]), case["any"], case["text"])
        self.assertEqual(meeting_hours.minutes("13:30"), 810)
        self.assertEqual(followup_effects.clock("14:00"), "2:00 PM")
        self.assertEqual(followup_effects.clock("after lecture"), "after lecture")
        self.assertEqual(followup_effects.clock(""), "")

    def test_formats(self):
        self.assertEqual((clock.hhmm(545), clock.twelve(780), clock.twelve(0), clock.twelve(720, False)), ("09:05", "1:00 pm", "12:00 am", "12:00"))
        self.assertEqual((clock.span_text(900, 1020), clock.span_text(630, 720)), ("3:00–5:00 pm", "10:30 am–12:00 pm"))
        self.assertEqual((clock.display_clock("00:05"), clock.display_clock("7:05PM"), clock.display_clock(None)), ("12:05 AM", "7:05 PM", ""))
        self.assertEqual(clock.long_day("2026-10-05"), "Monday, October 5")
        self.assertEqual((clock.display_span("15:00", "17:00"), clock.display_span("10:30", "12:00"), clock.display_span("13:00", "")),
                         ("3:00–5:00 PM", "10:30 AM–12:00 PM", "1:00 PM"))
        with self.assertRaises(ValueError):
            clock.parse_clock("13:00", "exact")

    def test_business_days_skip_weekends_and_university_holidays(self):
        # BUSINESS DAYS (2026-10-05, "fix the holidays"): Texas A&M's fiscal year 2027 holidays from the Texas A&M System's list.
        import datetime as dt
        day = dt.date.fromisoformat
        self.assertEqual(len(clock.UNIVERSITY_HOLIDAYS), 14)  # the fourteen holidays of the fiscal year (weekdays only)
        self.assertTrue(all(day(d).weekday() < 5 and d <= clock.HOLIDAYS_THROUGH for d in clock.UNIVERSITY_HOLIDAYS))
        self.assertFalse(clock.business_day(day("2026-11-26")))  # Thanksgiving
        self.assertFalse(clock.business_day(day("2026-10-10")))  # a Saturday
        self.assertTrue(clock.business_day(day("2026-11-25")))  # no class, but offices open: a business day
        self.assertEqual(clock.business_days_after(day("2026-11-25"), 2), day("2026-12-01"))
        self.assertEqual(clock.business_days_after(day("2026-10-02"), 3), day("2026-10-07"))
        self.assertEqual(clock.business_days_after(day("2027-03-17"), 1), day("2027-03-22"))  # Spring Break Thu, Fri

    def test_one_campus_zone(self):
        """Stage 1h: the campus zone is named once on the server (scheduling/clock.py) and once in the browser (calendar-feed.js)."""
        import datetime as dt
        import re
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((clock.ZONE_NAME, clock.CAMPUS_ZONE.key, clock.campus_now().tzinfo), ("America/Chicago", "America/Chicago", clock.CAMPUS_ZONE))
        self.assertEqual(clock.campus_today(), dt.datetime.now(dt.timezone.utc).astimezone(clock.CAMPUS_ZONE).date())
        skip = {".claude", ".venv", ".tools", "tests", "node_modules", ".git"}
        named = sorted(str(path.relative_to(root)) for pattern in ("*.py", "static/*.js") for path in root.rglob(pattern)
                       if not skip & set(path.relative_to(root).parts) and re.search(r"""["']America/Chicago["']""", path.read_text(errors="ignore")))
        self.assertEqual(named, ["scheduling/clock.py", "static/calendar-feed.js"])


if __name__ == "__main__":
    unittest.main()
