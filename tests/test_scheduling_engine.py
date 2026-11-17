"""SCHEDULING "ONE ENGINE" and "ONE FEED" (scheduling/engine.py, scheduling/feed.py, stage 1b, 2026-10-05): what each calendar entry is,
what it does to a new makeup or meeting (SHARING), the busy time, open starts, free stretches and a proposed time's check. Pure;
synthetic calendars only."""
import datetime as dt
import unittest

from scheduling import engine, feed


def event(ident, title, start, end="", date="2026-10-06", **extra):
    return {"id": ident, "source": ident.split(":")[0], "title": title, "date": date, "end_date": extra.pop("end_date", date),
            "time": start, "end_time": end, "all_day": False, "location": extra.pop("location", ""), **extra}


TUESDAY = dt.date(2026, 10, 6)
CALENDAR = [
    event("custom:oh", "MATH 140 · Office hours", "10:00", "11:00", location="BLOC 319"),
    event("custom:oh2", "MATH 140 · Office hours", "15:00", "17:00", location="BLOC 309"),
    event("exam:m", "Quiz 4 · Makeup", "1PM", makeup=True, minutes=30),
    event("office_hours:a", "Office hours", "13:40", "14:00"),
    event("dr:d", "Quiz 4 (DR)", "14:00", "15:00"),
    event("custom:due", "Homework due", "16:00", category="deadline"),
    event("google:x", "Math 664", "11:10", "12:25"),
    {**event("custom:all", "Out", ""), "all_day": True},
]


class EngineTests(unittest.TestCase):
    def test_every_entry_has_one_kind(self):
        kinds = {e["id"]: engine.kind_of(e) for e in CALENDAR}
        self.assertEqual(kinds, {"custom:oh": "office_hours", "custom:oh2": "office_hours", "exam:m": "makeup", "office_hours:a": "appointment",
                                 "dr:d": "dr", "custom:due": "deadline", "google:x": "other", "custom:all": "other"})
        self.assertEqual(engine.kind_of(event("google:y", "Jordan Example quiz makeup", "13:15")), "makeup")
        self.assertEqual(engine.kind_of(event("seating:s", "Exam 1", "7:05PM")), "exam")
        self.assertEqual(engine.kind_of({"source": "gradebook", "title": "Quiz 4"}), "deadline")

    def test_time_away_is_its_own_kind_and_blocks_everything(self):
        # AWAY (requirement, 2026-11-08): a timed entry whose title says the instructor is not there.
        for title in ("Unavailable", "Away: doctor's appointment", "Out of office", "OOO", "Office hours cancelled",
                      "No office hours today", "Unavailable for makeups", "Exam labeling — unavailable for new bookings"):
            self.assertEqual(engine.kind_of(event("custom:a", title, "15:00", "23:59")), "away", title)
            self.assertFalse(engine.is_office_block(event("custom:a", title, "15:00", "17:00")), title)
        for title in ("MATH 140 · Office hours", "Takeaway lunch", "Math 664", "Awayday"):
            self.assertNotEqual(engine.kind_of(event("google:b", title, "15:00")), "away", title)
        self.assertNotEqual(engine.kind_of({**event("custom:c", "Away", ""), "all_day": True}), "away")  # all-day never takes time
        self.assertNotEqual(engine.kind_of(event("office_hours:d", "Office hours: unavailable student", "15:00")), "away")
        gone = engine.entry(event("custom:a", "Unavailable", "15:00", "23:59"))
        self.assertEqual({engine.effect(kind, gone) for kind in engine.SHARING}, {"blocks"})
        self.assertEqual(engine.away(CALENDAR + [event("custom:a", "Unavailable", "15:00", "23:59")], TUESDAY), [(900, 1439)])
        self.assertEqual(engine.away(CALENDAR, TUESDAY), [])

    def test_the_sharing_table(self):
        found = {e["id"]: engine.entry(e) for e in CALENDAR}
        self.assertIsNone(found["custom:all"])  # all-day entries never take time
        self.assertEqual((found["exam:m"].start, found["exam:m"].end), (780, 810))  # "1PM", the student's 30 minutes
        effect = lambda kind, ident, rooms=(): engine.effect(kind, found[ident], rooms)  # noqa: E731
        self.assertEqual([effect("makeup", i) for i in ("exam:m", "office_hours:a", "dr:d", "custom:due", "google:x")],
                         ["shares", "blocks", "free", "free", "blocks"])
        self.assertEqual((effect("makeup", "custom:oh2"), effect("makeup", "custom:oh2", ["BLOC 309"])), ("blocks", "hosts"))
        self.assertEqual((effect("makeup", "custom:oh", ["BLOC 309"])), "blocks")  # the small office never seats one
        self.assertEqual([effect("appointment", i) for i in ("custom:oh", "exam:m", "office_hours:a", "dr:d", "custom:due", "google:x")],
                         ["hosts", "blocks", "shares", "free", "free", "blocks"])  # meetings overlap one another (2026-10-06)

    def test_busy_open_starts_and_free_spans(self):
        # A makeup: the meeting at 13:40 and Math 664 take time; the other makeup, the DR exam and the due date do not.
        self.assertEqual(sorted(engine.busy(CALENDAR, TUESDAY, "makeup")), [(600, 660), (670, 745), (820, 840), (900, 1020)])
        self.assertEqual(sorted(engine.busy(CALENDAR, TUESDAY, "makeup", rooms=["BLOC 309"])), [(600, 660), (670, 745), (820, 840)])
        meeting = engine.busy(CALENDAR, TUESDAY, "appointment")
        # Office hours host a meeting and the 13:40 meeting shares its time; the makeup takes its 30 minutes.
        self.assertEqual(sorted(meeting), [(670, 745), (780, 810)])
        taken = engine.busy(CALENDAR, TUESDAY, "makeup", rooms=["BLOC 309"])
        self.assertEqual(engine.open_starts(780, 1020, taken, length=20), [780, 840, 870, 900, 930, 960, 990])
        self.assertEqual(engine.open_starts(780, 1020, taken, length=35)[-1], 960)  # 16:30 + 35 minutes passes 17:00
        self.assertEqual(engine.open_starts(780, 1020, taken, length=20, allowed=lambda s: s >= 900), [900, 930, 960, 990])
        self.assertEqual(engine.free_spans(780, 1020, meeting), [(810, 1020)])
        self.assertEqual(engine.free_spans(780, 1020, [(790, 800)], minimum=30), [(800, 1020)])  # 13:00–13:10 is too short
        several = event("custom:trip", "Conference", "15:00", "10:00", date="2026-10-05", end_date="2026-10-07")
        self.assertEqual(engine.busy([several], TUESDAY, "makeup"), [(0, 1440)])  # a day between is taken wholly

    def test_checks_count_each_day_of_a_multi_day_timed_event(self):
        """SCHEDULING: time checks and available starts share the same multi-day boundaries."""
        trip = event("custom:trip", "Conference", "15:00", "10:00", date="2026-10-05", end_date="2026-10-07")
        for day, start, blocked in (("2026-10-05", 840, False), ("2026-10-05", 900, True),
                                    ("2026-10-06", 540, True), ("2026-10-07", 570, True), ("2026-10-07", 600, False)):
            found = engine.check([trip], new_kind="appointment", date=day, start=start, end=start + 30)
            self.assertEqual(bool(found["overlaps"]), blocked, (day, start))

    def test_a_proposed_time_is_checked_against_the_calendar(self):
        meeting = engine.check(CALENDAR, new_kind="appointment", date="2026-10-06", start=780, end=840)
        self.assertEqual(([e.id for e in meeting["overlaps"]], [e.id for e in meeting["within"]]),
                         (["exam:m"], ["office_hours:a"]))  # the DR exam is free; another meeting shares the time
        makeup = engine.check(CALENDAR, new_kind="makeup", date="2026-10-06", start=600, end=630, rooms=["BLOC 309"])
        self.assertEqual(([e.id for e in makeup["overlaps"]], makeup["within"]), (["custom:oh"], []))
        shared = engine.check(CALENDAR, new_kind="makeup", date="2026-10-06", start=790, end=800, own="office_hours:a")
        self.assertEqual(([e.id for e in shared["overlaps"]], [e.id for e in shared["within"]]), ([], ["exam:m"]))


class FeedTests(unittest.TestCase):
    def test_one_feed_in_one_order_and_a_google_failure_handled_by_its_caller(self):
        import sqlite3
        from unittest import mock

        class Calendar:
            def list(self, start, end):
                return []

        class Google:
            def feed(self, start, end):
                raise RuntimeError("offline")

        with mock.patch("workspace_calendar.source_events", return_value=[{"id": "exam:1"}]):
            db = sqlite3.connect(":memory:")
            self.assertEqual(feed.assemble(db, "2026-10-05", "2026-10-06", calendar=Calendar()), [{"id": "exam:1"}])
            seen = []
            self.assertEqual(feed.assemble(db, "2026-10-05", "2026-10-06", calendar=None, google=Google(), on_google_error=lambda: seen.append(1)),
                             [{"id": "exam:1"}])
            self.assertEqual(seen, [1])
            with self.assertRaises(RuntimeError):
                feed.assemble(db, "2026-10-05", "2026-10-06", calendar=None, google=Google())


class TypedTimeTests(unittest.TestCase):
    def test_typed_times_and_ranges_take_their_time(self):
        # Stage 1g (2026-10-05): a sitting or chart typed "7:05PM" or "9:00 AM–10:15 AM" is read, with a range's end.
        self.assertEqual(engine.span(event("seating:c", "Exam 1", "7:05PM")), (1145, 1205))
        self.assertEqual(engine.span(event("exam:s", "Exam 1 · Main", "9:00 AM–10:15 AM")), (540, 615))
        self.assertEqual(engine.span(event("exam:t", "Exam 1 · Main", "7:05–8:55 PM")), (1145, 1255))
        self.assertIsNone(engine.span(event("exam:u", "Exam 1 · Main", "After lecture")))
        self.assertEqual(engine.span(event("exam:m", "Quiz 4 · Makeup", "around 2 PM", makeup=True, minutes=30)), (840, 870))
        self.assertEqual(engine.span(event("exam:r", "Quiz 4 · Makeup", "2:00–2:40 PM", makeup=True, minutes=30)), (840, 880))
        self.assertEqual(engine.span(event("google:g", "Call", "15:00", "15:30")), (900, 930))

    def test_the_calendar_shows_a_typed_range_as_a_timed_event(self):
        from workspace_calendar import typed_range
        shown = typed_range({"time": "9:00 AM–10:15 AM", "end_time": "", "all_day": False})
        self.assertEqual((shown["time"], shown["end_time"]), ("09:00", "10:15"))
        for kept in ("7:05PM", "13:00", "After lecture", ""):
            self.assertEqual(typed_range({"time": kept, "end_time": "", "all_day": not kept})["time"], kept)
        self.assertEqual(typed_range({"time": "9–10 AM", "end_time": "11:00", "all_day": False})["time"], "9–10 AM")  # an explicit end stands


class AllDayTests(unittest.TestCase):
    def test_all_day_entries_never_take_time(self):
        # SCHEDULING "ALL-DAY ENTRIES" (requirement, 2026-10-05).
        events = [{"id": "google:a:note", "source": "google", "title": "Student absent (note)", "date": "2026-10-05", "end_date": "2026-10-07", "all_day": True},
                  {"id": "custom:1", "source": "custom", "title": "Deadline: grading", "date": "2026-10-06", "all_day": True},
                  {"id": "google:a:lunch", "source": "google", "title": "Lunch", "date": "2026-10-06", "time": "12:00", "end_time": "13:00", "all_day": False}]
        self.assertEqual(engine.busy(events, dt.date(2026, 10, 6), "appointment"), [(720, 780)])  # only the timed lunch
        self.assertEqual(engine.busy(events, dt.date(2026, 10, 6), "makeup"), [(720, 780)])
        self.assertFalse(hasattr(engine, "all_day"))


if __name__ == "__main__":
    unittest.main()
