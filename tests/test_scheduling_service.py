"""SCHEDULING "TERM SCHEDULE" (scheduling/service.py, stage 1c, 2026-10-05): a course term's dates and weekly entries on a scratch
workspace: revisions, exact request IDs, stale saves, stable entry identities, validation, a first revision proposed from the term's
calendar, the course page's group route and the course-edit protection. Nothing here reads the schedule yet (stage 1d)."""
import uuid

from test_absences import Base
from test_storage import HEADERS

from assistant import policy
from rosters import connect

CLASS = {"kind": "class", "weekdays": [0, 2, 4], "start": "08:00", "end": "08:50", "room": "HELD 107"}
OFFICE = {"kind": "office_hours", "weekdays": [0], "start": "15:00", "end": "17:00", "room": "BLOC 309", "seats_makeup": True}
MEETING = {"kind": "meeting_hours", "weekdays": [1, 3], "start": "13:00", "end": "17:00", "room": "BLOC 319"}


class ScheduleBase(Base):
    def save(self, entries, base=0, request=None, first="2026-08-24", last="2026-12-09"):
        return self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={
            "request_id": request or str(uuid.uuid4()), "base_revision": base, "first_day": first, "last_day": last, "entries": entries})


class ScheduleTests(ScheduleBase):

    def test_a_schedule_is_the_terms_own_with_stable_entries(self):
        empty = self.client.get(f"/api/scheduling/groups/{self.group}").json
        self.assertEqual((empty["term_id"], empty["revision"], empty["entries"]), (self.term, 0, []))
        self.assertEqual([s["number"] for s in empty["sections"]], ["507", "508"] if isinstance(empty["sections"][0]["number"], str) else [507, 508])
        saved = self.save([{**CLASS, "section_id": self.sections[507]}, OFFICE, MEETING], request="first")
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["revision"], 1)
        kinds = [(e["kind"], e["section_id"], e["weekdays"], e["seats_makeup"]) for e in saved.json["entries"]]
        self.assertEqual(kinds, [("class", self.sections[507], [0, 2, 4], False), ("office_hours", None, [0], True), ("meeting_hours", None, [1, 3], True)])
        ids = [e["entry"] for e in saved.json["entries"]]
        self.assertTrue(all(len(i) == 16 for i in ids))
        # The same request again answers the same; its ID with another schedule, or a save against an old revision, is refused.
        self.assertEqual(self.save([{**CLASS, "section_id": self.sections[507]}, OFFICE, MEETING], request="first").json, saved.json)
        self.assertEqual(self.save([OFFICE], request="first").status_code, 409)
        self.assertEqual(self.save([OFFICE]).status_code, 409)
        # An edit keeps each entry's identity; an identical schedule records nothing.
        edited = [{**e, "start": "08:05"} if e["kind"] == "class" else e for e in saved.json["entries"]]
        second = self.save([{k: v for k, v in e.items() if k != "section_id" or e["kind"] == "class"} for e in edited], base=1)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(([e["entry"] for e in second.json["entries"]], second.json["revision"]), (ids, 2))
        self.assertEqual(self.save(second.json["entries"], base=2).json["revision"], 2)
        # An entry keeps its kind and section; a new one is added instead.
        moved = [{**second.json["entries"][0], "section_id": self.sections[508]}]
        self.assertIn("keeps its kind", self.save(moved, base=2).json["error"])
        self.assertEqual(policy.base_rule("scheduling.save").tier, "approve")

    def test_what_a_schedule_refuses(self):
        cases = [
            ([{**CLASS}], "names one of this term"),  # a class without a section
            ([{**OFFICE, "section_id": self.sections[507]}], "names one of this term"),
            ([{**CLASS, "section_id": "not-a-section"}], "names one of this term"),
            ([{**OFFICE, "weekdays": []}], "days of the week"),
            ([{**OFFICE, "weekdays": [7]}], "days of the week"),
            ([{**OFFICE, "end": "15:05"}], "at least 10 minutes"),
            ([{**OFFICE, "start": "3 pm"}], "such as 13:00"),
            ([MEETING, {**MEETING, "weekdays": [1], "start": "16:00", "end": "18:00"}], "overlap"),
            ([{**OFFICE, "seats_makeup": "yes"}], "seat a makeup"),
            ([{**OFFICE, "entry": "0000000000000000"}], "not one of this term"),
            ([{**OFFICE, "entry": {}}], "not one of this term"),
            ([{**CLASS, "section_id": ["507"]}], "names one of this term"),
            ([{**OFFICE, "weekdays": [[]]}], "days of the week"),
            ([{**OFFICE, "weekdays": [{}]}], "days of the week"),
        ]
        for entries, why in cases:
            refused = self.save(entries)
            self.assertEqual(refused.status_code, 400, (entries, refused.json))
            self.assertIn(why, refused.json["error"])
        self.assertIn("first and last day", self.save([OFFICE], first="").json["error"])
        self.assertIn("within 200 days", self.save([OFFICE], first="2026-01-01", last="2026-12-31").json["error"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scheduling_revisions").fetchone()[0], 0)

    def test_a_first_revision_is_proposed_from_the_terms_calendar(self):
        calendar = self.app.extensions["calendar"]

        def event(title, day, start, end, room, scope="term", section=None):
            calendar.create({"request_id": str(uuid.uuid4()), "title": title, "date": day, "time": start, "end_time": end, "all_day": False,
                             "location": room, "scope": scope, "definition_id": self.definition, "term_id": self.term,
                             **({"section_id": section} if section else {})})

        for day in ("2026-09-28", "2026-09-30", "2026-10-02", "2026-10-05", "2026-10-07", "2026-10-09"):
            event("MATH 140 · Section 507", day, "08:00", "08:50", "HELD 107", "section", self.sections[507])
        for day in ("2026-09-29", "2026-10-01", "2026-10-06", "2026-10-08"):
            event("MATH 140 · Section 508", day, "09:35", "10:50", "HELD 111", "section", self.sections[508])
        for day in ("2026-09-28", "2026-10-05"):
            event("MATH 140 · Office hours", day, "15:00", "17:00", "BLOC 309")
        event("MATH 140 · Office hours", "2026-10-12", "10:00", "11:00", "BLOC 319")  # seen once: not weekly
        hours = self.client.post(f"/api/student-support/meeting-hours/{self.term}", headers=HEADERS, json={
            "request_id": "hours", "base_revision": 0, "makeup_rooms": ["BLOC 309"],
            "windows": [{"weekday": d, "start": "13:00", "end": "17:00", "location": "BLOC 319"} for d in (1, 3)]})
        self.assertEqual(hours.status_code, 200, hours.json)
        proposal = self.client.get(f"/api/scheduling/terms/{self.term}/import").json
        self.assertEqual((proposal["first_day"], proposal["last_day"]), ("2026-09-28", "2026-10-09"))
        found = [(e["kind"], e["section_id"], e["weekdays"], e["start"], e["end"], e["room"], e["seats_makeup"]) for e in proposal["entries"]]
        self.assertEqual(found, [("class", self.sections[507], [0, 2, 4], "08:00", "08:50", "HELD 107", False),
                                 ("class", self.sections[508], [1, 3], "09:35", "10:50", "HELD 111", False),
                                 ("office_hours", None, [0], "15:00", "17:00", "BLOC 309", True),
                                 ("meeting_hours", None, [1, 3], "13:00", "17:00", "BLOC 319", True)])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scheduling_entries").fetchone()[0], 0)  # a proposal writes nothing
        saved = self.save(proposal["entries"], first=proposal["first_day"], last=proposal["last_day"])
        self.assertEqual((saved.status_code, len(saved.json["entries"])), (200, 4))

    def test_a_course_edit_cannot_detach_a_schedule(self):
        self.assertEqual(self.save([{**CLASS, "section_id": self.sections[508]}]).status_code, 200)
        from scheduling.service import protect_references
        course = next(c for c in self.client.get("/api/state").json["courses"] if c["definition_id"] == self.definition)
        moved = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "term": "Spring"}, headers=HEADERS)
        self.assertEqual(moved.status_code, 409, moved.json)
        self.assertIn("detach a Term schedule", moved.json["error"])
        with connect(self.app.config["DATABASE"]) as db:
            previous = dict(db.execute("SELECT * FROM courses WHERE id=?", (course["id"],)).fetchone())
            previous["sections"] = [507, 508]
            with self.assertRaisesRegex(Exception, "detach a Term schedule"):
                protect_references(db, previous, {**previous, "sections": [507]})  # section 508 has a class meeting
            protect_references(db, previous, {**previous, "sections": [508], "name": "Renamed"})


class UseTests(ScheduleBase):
    def test_using_a_schedule_shows_it_and_hides_what_it_replaces_and_undo_brings_them_back(self):
        # USE THIS SCHEDULE (stage 1d, 2026-10-05): every difference listed and confirmed; nothing deleted; Undo.
        calendar = self.app.extensions["calendar"]

        def event(title, day, start, end, room, scope="term", section=None):
            return calendar.create({"request_id": str(uuid.uuid4()), "title": title, "date": day, "time": start, "end_time": end, "all_day": False,
                                    "location": room, "scope": scope, "definition_id": self.definition, "term_id": self.term,
                                    **({"section_id": section} if section else {})})

        for day in ("2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14"):  # Mon/Wed; the schedule says Mon/Wed/Fri
            event("MATH 140 · Section 507", day, "08:00", "08:50", "HELD 107", "section", self.sections[507])
        event("MATH 140 · Section 507", "2026-10-15", "08:00", "08:50", "HELD 107", "section", self.sections[507])  # a Thursday: not matched
        for day in ("2026-10-05", "2026-10-12"):
            event("MATH 140 · Office hours", day, "15:00", "17:00", "BLOC 309")
        lunch = event("Lunch", "2026-10-06", "12:00", "13:00", "")
        saved = self.save([{**CLASS, "weekdays": [0, 2], "section_id": self.sections[507]}, OFFICE, MEETING], first="2026-10-05", last="2026-10-16")
        self.assertEqual(saved.status_code, 200, saved.json)
        url = f"/api/scheduling/terms/{self.term}/use"
        plan = self.client.get(url).json
        self.assertEqual((plan["matched"], plan["matched_by_kind"], plan["left_count"], plan["added_count"]),
                         (6, {"class": 4, "office_hours": 2}, 1, 0))
        self.assertEqual(plan["left"][0]["date"], "2026-10-15")
        before = self.client.get("/api/calendar?start=2026-10-05&end=2026-10-16").json["events"]
        refused = self.client.post(url, headers=HEADERS, json={"request_id": "u0", "fingerprint": "stale", "confirmation": "use"})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.client.post(url, headers=HEADERS, json={"request_id": "u0", "fingerprint": plan["fingerprint"]}).status_code, 400)
        used = self.client.post(url, headers=HEADERS, json={"request_id": "u1", "fingerprint": plan["fingerprint"], "confirmation": "use"})
        self.assertEqual((used.status_code, used.json["hidden"], used.json["in_use"]), (200, 6, True), used.json)
        self.assertEqual(self.client.post(url, headers=HEADERS, json={"request_id": "u1", "fingerprint": plan["fingerprint"], "confirmation": "use"}).json, used.json)
        self.assertTrue(self.client.get(f"/api/scheduling/terms/{self.term}").json["in_use"])
        after = self.client.get("/api/calendar?start=2026-10-05&end=2026-10-16").json["events"]
        customs = sorted(e["date"] for e in after if e["source"] == "custom")
        self.assertEqual(customs, ["2026-10-06", "2026-10-15"])  # lunch and the unmatched Thursday still show
        rows = [e for e in after if e["source"] == "schedule"]
        self.assertEqual(sorted((e["date"], e["title"]) for e in rows),
                         [("2026-10-05", "Office hours"), ("2026-10-05", "Section 507"), ("2026-10-07", "Section 507"),
                          ("2026-10-12", "Office hours"), ("2026-10-12", "Section 507"), ("2026-10-14", "Section 507")])
        self.assertTrue(all(e["code"] == "MATH 140" and e["href"].endswith("/home/schedule") for e in rows))
        self.assertNotIn("meeting", " ".join(e["title"] for e in rows).casefold())  # meeting hours are never calendar blocks
        # Google: the schedule's rows go as "Your events", one each, keyed by entry and date.
        from google_calendar.service import body, group, kind
        self.assertEqual({kind(e) for e in rows}, {"custom"})
        grouped = group(rows)
        self.assertEqual(len(grouped), 6)
        office = next(body(k, g) for k, g in grouped.items() if g["events"][0]["title"] == "Office hours")
        self.assertEqual(office["summary"], "MATH 140 · Office hours")
        # Availability comes from the schedule now: its meeting hours and its office hours that seat a makeup.
        from scheduling.service import availability
        with connect(self.app.config["DATABASE"]) as db:
            found = availability(db, self.term)
        self.assertEqual((found["source"], found["makeup_rooms"], [w["weekday"] for w in found["windows"]]), ("schedule", ["BLOC 309"], [1, 3]))
        # The engine reads a schedule's office hours by their own flag.
        from scheduling import engine
        office_row = next(e for e in rows if e["title"] == "Office hours")
        self.assertEqual(engine.effect("makeup", engine.entry(office_row)), "hosts")
        # Using twice is refused; Undo restores every hidden event and the schedule leaves the Calendar.
        again = self.client.get(url).json
        self.assertEqual(self.client.post(url, headers=HEADERS, json={"request_id": "u2", "fingerprint": again["fingerprint"], "confirmation": "use"}).status_code, 409)
        undone = self.client.post(url + "/undo", headers=HEADERS, json={"request_id": "x1", "confirmation": "undo"})
        self.assertEqual((undone.status_code, undone.json["restored"], undone.json["in_use"]), (200, 6, False), undone.json)
        restored = self.client.get("/api/calendar?start=2026-10-05&end=2026-10-16").json["events"]
        self.assertEqual(sorted(e["id"] for e in restored), sorted(e["id"] for e in before))
        self.assertEqual(self.client.post(url + "/undo", headers=HEADERS, json={"request_id": "x2", "confirmation": "undo"}).status_code, 409)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM calendar_events WHERE deleted_at IS NULL").fetchone()[0], 8)  # nothing deleted
        self.assertTrue(lunch["id"])
        self.assertEqual(policy.base_rule("scheduling.use").tier, "approve")

    def test_use_needs_a_saved_schedule(self):
        self.assertEqual(self.client.get(f"/api/scheduling/terms/{self.term}/use").status_code, 409)

    def test_days_off_are_found_from_the_calendar_and_kept_free(self):
        # DAYS OFF (2026-10-05 rehearsal: the per-date events skip holidays a weekly schedule alone would fill).
        calendar = self.app.extensions["calendar"]
        for day in ("2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14"):  # Mon/Wed only, though the schedule says Mon/Wed/Fri
            calendar.create({"request_id": str(uuid.uuid4()), "title": "MATH 140 · Section 507", "date": day, "time": "08:00", "end_time": "08:50",
                             "all_day": False, "location": "HELD 107", "scope": "section", "definition_id": self.definition, "term_id": self.term,
                             "section_id": self.sections[507]})
        saved = self.save([{**CLASS, "section_id": self.sections[507]}], first="2026-10-05", last="2026-10-16")
        self.assertEqual(saved.json["days_off"], [])
        found = self.client.get(f"/api/scheduling/terms/{self.term}/days-off").json["days_off"]
        self.assertEqual(found, ["2026-10-09", "2026-10-16"])
        self.assertEqual(self.client.get(f"/api/scheduling/terms/{self.term}/use").json["added_count"], 2)
        bad = self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={"request_id": "bad-off", "base_revision": 1,
                               "first_day": "2026-10-05", "last_day": "2026-10-16", "entries": saved.json["entries"], "days_off": ["2026-11-01"]})
        self.assertEqual(bad.status_code, 400)
        kept = self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={"request_id": "off", "base_revision": 1,
                                "first_day": "2026-10-05", "last_day": "2026-10-16", "entries": saved.json["entries"], "days_off": found})
        self.assertEqual((kept.status_code, kept.json["days_off"], kept.json["revision"]), (200, found, 2), kept.json)
        plan = self.client.get(f"/api/scheduling/terms/{self.term}/use").json
        self.assertEqual((plan["matched"], plan["added_count"], plan["left_count"]), (4, 0, 0))
        proposal = self.client.get(f"/api/scheduling/terms/{self.term}/import").json
        self.assertEqual(proposal["days_off"], [])  # the import's own weekly days are Mon/Wed: nothing is skipped

    def test_meeting_hours_move_to_the_schedule_and_an_appointment_is_checked(self):
        # Stage 1e (2026-10-05): once the term uses its Term schedule, the Term policies' meeting hours are its, read-only there.
        hours = f"/api/student-support/meeting-hours/{self.term}"
        legacy = {"request_id": "h0", "base_revision": 0, "windows": [{"weekday": 0, "start": "09:00", "end": "10:00", "location": "X"}]}
        self.assertEqual(self.client.post(hours, headers=HEADERS, json=legacy).status_code, 200)
        saved = self.save([{**CLASS, "section_id": self.sections[507]}, OFFICE, MEETING], first="2026-10-05", last="2026-10-16")
        self.assertEqual(saved.status_code, 200, saved.json)
        window = next(t for t in self.client.get("/api/student-support/policies?areas=1").json["terms"] if t["term_id"] == self.term)
        self.assertEqual((window["schedule"]["in_use"], [w["weekday"] for w in window["schedule"]["windows"]]), (False, [0]))  # legacy still
        plan = self.client.get(f"/api/scheduling/terms/{self.term}/use").json
        used = self.client.post(f"/api/scheduling/terms/{self.term}/use", headers=HEADERS,
                                json={"request_id": "u", "fingerprint": plan["fingerprint"], "confirmation": "use"})
        self.assertEqual(used.status_code, 200, used.json)
        window = next(t for t in self.client.get("/api/student-support/policies?areas=1").json["terms"] if t["term_id"] == self.term)
        self.assertTrue(window["schedule"]["in_use"])
        self.assertEqual(([w["weekday"] for w in window["schedule"]["windows"]], window["schedule"]["makeup_rooms"]), ([1, 3], ["BLOC 309"]))
        self.assertTrue(window["schedule"]["href"].endswith("/home/schedule"))
        refused = self.client.post(hours, headers=HEADERS, json={**legacy, "request_id": "h1", "base_revision": 1})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Term schedule", refused.json["error"])
        self.assertNotIn("schedule", self.client.get("/api/student-support/policies").json["terms"][0])  # the plain read is unchanged
        # An appointment over Monday's 8:00 class overlaps it; Tuesday at 2:00 is inside meeting hours; Wednesday at 6:00 is outside.
        check = lambda day, start, end: self.client.get(f"/api/scheduling/check?term_id={self.term}&date={day}&time={start}&end_time={end}").json  # noqa: E731
        monday = check("2026-10-05", "08:15", "08:45")
        self.assertEqual([(o["title"], o["time"]) for o in monday["overlaps"]], [("Section 507", "08:00")])
        self.assertEqual(check("2026-10-06", "14:00", "14:30"), {"overlaps": [], "outside_hours": False})
        self.assertEqual(check("2026-10-07", "18:00", "18:30"), {"overlaps": [], "outside_hours": True})
        self.assertEqual(check("2026-10-05", "15:30", "16:00")["outside_hours"], False)  # office hours host it
        self.assertEqual(check("bad", "15:30", "16:00"), {"overlaps": [], "outside_hours": False})
