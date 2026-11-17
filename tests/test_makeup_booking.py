"""Quiz makeups booked by card (ABSENCES "MAKEUP BOOKING", absences/makeup_booking.py; requirement, 2026-10-04) on a scratch workspace,
with fakes for the public card service and the mail relay and a fixed campus clock (dates in 2036, so no real date ever passes them):

- nothing goes out while a term's cards are off; the dry run names each card's times and writes nothing;
- turned on, an excused absence over a quiz's day gets one card with that student's times, published and emailed, never before the
  day its details say they can return nor before now (2026-10-30);
- a pick seats the student in that time's makeup sitting (shared by everyone picking it), once per submission; "None of these work"
  keeps their words and is flagged; "Send a new card" offers the next two days with times after the last card's and is sent by the
  worker;
- an unanswered card is withdrawn when the makeup stops being owed; no answer after 3 business days and no paper after the sitting
  are flagged on the Makeups view and counted on Home without names;
- as soon as a student books, their copy is made (printed for them, their name filled in on the paper: 2026-10-18) and attached to the sitting; while the quiz has no approved makeup
  version the copies wait, and are made once one is approved (2026-10-12; until then, the evening before the sitting);
- a quiz owing a makeup with no makeup version gets one drafted once (a failure is shown, a scrapped draft is never redone)."""
import datetime as dt
import json
import uuid
from pathlib import Path
from unittest import mock

from test_absences import Base
from test_response_card_mail import FakeRelay
from test_response_cards import FakeRemote
from test_storage import HEADERS

from absences import makeup_booking as booking_module
from absences import makeup_slots as slots
from response_cards.mail import MailConfig
from rosters import connect

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"
QUIZ_DAY = "2036-10-03"  # a Friday
SATURDAY = dt.datetime(2036, 10, 4, 12, 0)
MONDAY = "Mon, Oct 6 · 3:00–5:00 pm · BLOC 309 · office hours, come any time"
TUESDAY = "Tue, Oct 7 · 10:00–11:00 am · BLOC 319 · office hours, come any time"  # the small office: never offered (2026-10-05)
AFTERNOON = [f"Tue, Oct 7 · {t} · BLOC 319" for t in ("1:00 pm", "1:30 pm", "2:00 pm", "2:30 pm", "3:00 pm", "3:30 pm", "4:00 pm", "4:30 pm")]


class BookingBase(Base):
    def setUp(self):
        super().setUp()
        self.absences = self.app.extensions["absences"]
        self.booking = self.absences.booking
        self.exams = self.absences.exams
        self.cards = self.app.extensions["response_cards"]
        self.remote = FakeRemote()
        self.cards.remote = self.remote
        self.relay = FakeRelay()
        self.cards.mail.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4)
        self.cards.mail.transport = self.relay
        # GMAIL (2026-10-06): booking cards go only through the instructor's Gmail connected in Response Cards; the fake transport
        # above carries every envelope, so only the connection is faked here.
        self.cards.mail.gmail = mock.Mock(connected=True, email="teacher@example.test")
        self.documents = self.app.extensions["documents"]
        self.assignment = self.documents.create_assignment(self.definition, "quiz-4", "Quiz 4")
        event = self.exams.create({"term_id": self.term, "assignment_id": self.assignment["id"], "section_ids": [self.sections[507], self.sections[508]]})
        self.event = self.exams.save_sitting(event["id"], {"version": event["version"], "label": "Quiz 4", "kind": "main", "date": QUIZ_DAY})
        calendar = self.app.extensions["calendar"]
        for day, start, end, room in (("2036-10-06", "15:00", "17:00", "BLOC 309"), ("2036-10-07", "10:00", "11:00", "BLOC 319"),
                                      ("2036-10-09", "10:00", "11:00", "BLOC 319"), ("2036-10-13", "15:00", "17:00", "BLOC 309"),
                                      ("2036-10-14", "10:00", "11:00", "BLOC 319")):
            calendar.create({"request_id": str(uuid.uuid4()), "title": "MATH 140 · Office hours", "date": day, "time": start, "end_time": end,
                             "all_day": False, "location": room})
        # MEETING HOURS (2026-10-05): the term's Tuesday and Thursday 1–5 pm in BLOC 319 (a card offers Tuesday's first), and the
        # office-hours rooms big enough to seat a makeup (BLOC 309 and BLOC 306; never BLOC 319, the instructor's small office).
        saved = self.client.post(f"/api/student-support/meeting-hours/{self.term}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": 0, "makeup_rooms": ["BLOC 309", "BLOC 306"], "windows": [
                {"weekday": weekday, "start": "13:00", "end": "17:00", "location": "BLOC 319"} for weekday in (3, 1)]})
        self.assertEqual(saved.status_code, 200, saved.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.bob = db.execute("SELECT m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id "
                                  "WHERE e.uin='123456789' AND m.section_id=?", (self.sections[507],)).fetchone()[0]
        self.now = SATURDAY
        for name, value in (("campus_now", lambda: self.now),
                            ("timestamp", lambda: self.now.replace(tzinfo=booking_module.TIME_ZONE).astimezone(dt.timezone.utc).isoformat())):
            patcher = mock.patch.object(booking_module, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- helpers -----------------------------------------------------------------------------------------------------------------
    def excuse(self, enrollment, request_id, first=QUIZ_DAY, last=QUIZ_DAY, **facts):
        created = self.client.post("/api/absences", json={"section_id": self.sections[507], "enrollment_id": enrollment, "first_date": first,
                                                          "last_date": last, "reason": "illness", "request_id": request_id, **facts}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        decided = self.revise(created.json, request_id + "-d", status="excused")
        self.assertEqual(decided.status_code, 200, decided.json)
        return decided.json

    def turn_on(self, revision=0):
        response = self.client.put(f"/api/absences/makeups/terms/{self.term}", json={"request_id": str(uuid.uuid4()), "cards": True, "revision": revision},
                                   headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def drain(self, limit=50):
        done = 0
        self.booking._idle_until = 0.0
        self.booking._failures.clear()
        while self.booking.process_one():
            done += 1
            self.assertLess(done, limit)
        return done

    def rows(self):
        response = self.client.get("/api/absences/makeups")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def row(self, enrollment):
        return next(r for r in self.rows()["makeups"] if r["enrollment_id"] == enrollment)

    def card(self, enrollment, attempt=1):
        with connect(self.app.config["DATABASE"]) as db:
            return dict(db.execute("""SELECT r.*,o.attempt FROM absence_makeup_offers o JOIN response_card_issues i ON json_extract(i.context_json,'$.makeup_offer_id')=o.id
                JOIN response_card_recipients r ON r.issue_id=i.id WHERE o.enrollment_id=? AND o.attempt=?""", (enrollment, attempt)).fetchone())

    def pick(self, card_id, choice, other=None):
        answers = {slots.FIELD: choice, **({slots.OTHER_FIELD: other} if other is not None else {})}
        self.remote.event(card_id, "submitted", submission={"id": "sub-" + card_id, "request_id": "req-" + card_id, "status": "submitted",
                                                             "answers": answers, "submitted_at": "2036-10-04T18:00:00.000Z"})
        self.cards.sync()
        self.drain()

    def count(self, table):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def home(self):
        cards = self.client.get("/api/workspace/overview").json["attention"]
        return next((card for card in cards if card["key"] == "makeups"), None)


class CardTests(BookingBase):
    def test_cards_stay_off_until_turned_on_and_the_dry_run_writes_nothing(self):
        self.excuse(self.alice, "a1")
        self.assertEqual(self.drain(), 0)
        listed = self.rows()
        self.assertEqual({k: listed["booking"]["terms"][self.term][k] for k in ("cards", "revision", "code")}, {"cards": False, "revision": 0, "code": "MATH 140"})
        self.assertEqual(listed["makeups"][0]["booking"]["state"], "manual")
        planned = self.client.get("/api/absences/makeups/plan").json["cards"]
        self.assertEqual([(c["title"], c["week"], c["due"], c["options"]) for c in planned],
                         [("Quiz 4", "2036-10-06", "2036-11-02", [MONDAY, *AFTERNOON])])
        self.assertEqual((self.count("absence_makeup_offers"), self.count("response_card_issues")), (0, 0))
        self.assertEqual(self.home()["label"], "Makeups owed")  # unchanged while every term's cards are off
        stale = self.client.put(f"/api/absences/makeups/terms/{self.term}", json={"request_id": "s1", "cards": True, "revision": 3}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.turn_on(), {"cards": True, "revision": 1})

    def test_times_start_on_the_day_the_student_can_return(self):
        # FROM THEIR RETURN (requirement, 2026-10-30). Her Friday absence's details clear her for Thursday: no Monday or Tuesday time, and 30 days from Thursday.
        absence = self.excuse(self.alice, "a1", details="Doctor's note: she can return to class on October 9.")
        thursday = [f"Thu, Oct 9 · {t} · BLOC 319" for t in ("1:00 pm", "1:30 pm", "2:00 pm", "2:30 pm", "3:00 pm", "3:30 pm", "4:00 pm", "4:30 pm")]
        planned = self.client.get("/api/absences/makeups/plan").json["cards"]
        self.assertEqual([(c["week"], c["due"], c["options"]) for c in planned],
                         [("2036-10-09", "2036-11-08", [*thursday, MONDAY.replace("Oct 6", "Oct 13")])])
        self.turn_on()
        self.drain()
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["offer"]["week"], booking["offer"]["options"][0], booking["due"]), ("2036-10-09", thursday[0], "2036-11-08"))
        # "Send a new card" (and a follow-up's times, the same `times`) keeps to it as well.
        made = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": self.event["id"], "section_id": self.sections[507],
                                                                     "enrollment_id": self.alice, "absence_id": absence["id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        self.assertEqual((made.json["week"], made.json["due"]), ("2036-10-14", "2036-11-08"))

    def test_a_return_already_past_offers_times_from_now(self):
        # "if the followup is after their return date it shouldn't backdate it either": seen on Wednesday, a Monday return starts
        # the times today (nothing Wednesday, so Thursday and the next Monday); its due date still counts from the return.
        self.excuse(self.alice, "a1", details="Back on Monday, October 6.")
        self.now = dt.datetime(2036, 10, 8, 9, 0)
        with connect(self.app.config["DATABASE"]) as db:
            row = next(r for r in self.booking._owed(db) if r["enrollment_id"] == self.alice)
            self.assertEqual(self.booking.returns(db, row), "2036-10-06")
            offered = self.booking.times(db, row)
        self.assertEqual((offered["week"], offered["due"]), ("2036-10-09", "2036-11-05"))
        self.assertEqual(min(o["date"] for o in offered["options"]), "2036-10-09")
        # A clinic note's wording (2026-10-30, Quiz 6): the return named after a few words, and never one on or before the last day.
        from absences.service import return_date
        self.assertEqual(return_date("Note documents return to school without restrictions November 2, 2026.", "2036-11-01"), "2036-11-02")
        self.assertEqual(return_date("Cleared to return to school without restrictions November 1.", "2036-11-01"), "")
        # Without a return in the details, nothing changes: the day after the absence, 30 days from its last day.
        self.excuse(self.bob, "b1")
        self.now = SATURDAY
        planned = {c["enrollment_id"]: c for c in self.client.get("/api/absences/makeups/plan").json["cards"]}
        self.assertEqual((planned[self.bob]["week"], planned[self.bob]["due"], planned[self.bob]["options"]),
                         ("2036-10-06", "2036-11-02", [MONDAY, *AFTERNOON]))

    def test_a_card_waits_while_google_cannot_be_checked(self):
        # SCHEDULING "GOOGLE CHECKED" (2026-10-05): a card offers no time Faculty could not check against the Google calendar.
        self.turn_on()
        self.excuse(self.alice, "a1")
        gap = "Faculty has not read your Google Calendar since Mon, Oct 5 at 3:05 PM"
        with mock.patch.object(self.app.extensions["google_calendar"], "unchecked", return_value=gap):
            self.drain()
            self.assertEqual(self.count("absence_makeup_offers"), 0)
            self.assertEqual((self.row(self.alice)["booking"]["state"], self.row(self.alice)["booking"]["label"]), ("waiting", gap))
            self.assertFalse(self.row(self.alice)["booking"]["flag"])  # nothing for the instructor to do: it goes once Google is read
        self.booking._idle_until = 0.0
        self.assertTrue(self.booking.process_one())  # no back-off: the first turn after Google is read again
        self.assertEqual(self.count("absence_makeup_offers"), 1)

    def test_a_card_sent_by_hand_is_refused_while_google_cannot_be_checked(self):
        # SCHEDULING "GOOGLE CHECKED" (requirement, 2026-10-06) / "Send a new card" checks Google too. Refused
        # with the reason and nothing recorded; once Google is read, the same request sends the card, and a replay returns it.
        absence = self.excuse(self.alice, "a1")
        ask = {"request_id": "by-hand", "event_id": self.event["id"], "section_id": self.sections[507], "enrollment_id": self.alice,
               "absence_id": absence["id"]}
        gap = "Faculty has not read your Google Calendar since Mon, Oct 5 at 3:05 PM"
        with mock.patch.object(self.app.extensions["google_calendar"], "unchecked", return_value=gap):
            refused = self.client.post("/api/absences/makeups/cards", json=ask, headers=HEADERS)
            self.assertEqual(refused.status_code, 409, refused.json)
            self.assertIn(gap + ", so this card's times cannot be checked against it. No card was sent", json.dumps(refused.json))
            self.assertEqual(self.count("absence_makeup_offers"), 0)
        made = self.client.post("/api/absences/makeups/cards", json=ask, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        with mock.patch.object(self.app.extensions["google_calendar"], "unchecked", return_value=gap):
            again = self.client.post("/api/absences/makeups/cards", json=ask, headers=HEADERS)
            self.assertEqual((again.status_code, again.json["id"]), (201, made.json["id"]))  # a lost answer is not a new card
        self.assertEqual(self.count("absence_makeup_offers"), 1)

    def test_an_all_day_event_never_holds_a_card(self):
        # SCHEDULING "ALL-DAY ENTRIES" (requirement, 2026-10-05).
        self.turn_on()
        self.app.extensions["calendar"].create({"request_id": str(uuid.uuid4()), "title": "Student absent (note)", "date": "2036-10-06",
                                                "all_day": True, "location": ""})
        self.excuse(self.alice, "a1")
        self.drain()
        self.assertEqual(self.row(self.alice)["booking"]["state"], "sent")
        self.assertEqual(self.remote.snapshots[self.card(self.alice)["id"]]["schema"]["fields"][0]["options"][:2], [MONDAY, AFTERNOON[0]])

    def test_a_card_goes_out_and_a_pick_seats_the_student_once(self):
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        card = self.card(self.alice)
        snapshot = self.remote.snapshots[card["id"]]
        field = next(f for f in snapshot["schema"]["fields"] if f["id"] == slots.FIELD)
        self.assertEqual(field["options"], [MONDAY, *AFTERNOON, slots.OTHER])
        self.assertNotIn(TUESDAY, field["options"])
        self.assertIn("on Friday, October 3 with an excused absence", snapshot["schema"]["description"])
        self.assertIn("It must be done by Sunday, November 2.", snapshot["schema"]["description"])
        self.assertEqual(snapshot["policy"], "once")
        while self.cards.mail.process_one():
            pass
        self.assertEqual([m["recipient"] for m in self.relay.sent], ["alice@example.test"])
        with connect(self.app.config["DATABASE"]) as db:  # through the instructor's Gmail, never the relay (2026-10-06)
            self.assertEqual(db.execute("SELECT provider, gmail_sender FROM response_card_mail WHERE card_id=?", (card["id"],)).fetchone()[:],
                             ("gmail", "teacher@example.test"))
        # The invitation is worded for a quiz already missed (requirement, 2026-10-06), never "your upcoming Quiz 4", and never
        # mentions the card's "None of these work" choice.
        invitation = self.relay.sent[0]
        self.assertTrue(invitation["subject"].startswith("IMPORTANT: Book your Quiz 4 makeup for "), invitation["subject"])
        self.assertIn("You missed Quiz 4 for ", invitation["text_body"])
        self.assertIn("with an excused absence, so you will take a makeup. Choose when on this short response card:", invitation["text_body"])
        self.assertIn("The card closes when its last time begins", invitation["text_body"])
        for wording in ("upcoming", "replaces", "None of these", "another time"):
            self.assertNotIn(wording, invitation["subject"] + invitation["text_body"])
        self.assertEqual((self.row(self.alice)["booking"]["state"], self.row(self.alice)["booking"]["label"]), ("sent", "Card sent Sat, Oct 4"))
        self.assertIsNone(self.home())  # nothing needs the instructor
        self.assertEqual(self.drain(), 0)  # one card only
        self.pick(card["id"], AFTERNOON[3])
        event = self.exams.get(self.event["id"])
        makeup = [s for s in event["sittings"] if s["kind"] == "makeup"]
        self.assertEqual([(s["label"], s["date"], s["time"], s["location"]) for s in makeup], [("Makeup · Tue, Oct 7 · 2:30 pm", "2036-10-07", "14:30", "BLOC 319")])
        alice = next(p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == (self.sections[507], self.alice))
        self.assertEqual((alice["sitting_id"], alice["expectation"]), (makeup[0]["id"], "expected"))
        # LENGTH (2026-10-05): on the calendar the makeup lasts its student's quiz, 20 minutes without extra time.
        shown = next(e for e in self.client.get("/api/calendar?start=2036-10-07&end=2036-10-07").json["events"] if e["id"] == "exam:" + makeup[0]["id"])
        self.assertEqual((shown["time"], shown["end_time"], shown["makeup"], shown["minutes"]), ("14:30", "14:50", True, 20))
        self.cards.sync()
        self.drain()
        self.assertEqual((self.count("absence_makeup_picks"), len([s for s in self.exams.get(self.event["id"])["sittings"] if s["kind"] == "makeup"])), (1, 1))
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"], booking["flag"]), ("booked", "Booked: Tue, Oct 7 · 2:30 pm · BLOC 319", False))
        self.assertEqual(self.row(self.alice)["given_date"], QUIZ_DAY)  # a makeup never moves the day the quiz was given

    def test_the_follow_up_says_what_the_booking_card_does(self):
        # ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP (requirement, 2026-11-03). The Excuse card said "The student is not told".
        from assistant.followup_effects import BOOKING_CARD, PRIVATE, Describer

        def lines():
            with connect(self.app.config["DATABASE"]) as db:
                return [line["text"] for line in self.booking.followup_cards(db, [self.alice]).get(self.alice, [])]

        def effect(absence, status):
            with connect(self.app.config["DATABASE"]) as db:
                describer = Describer(db, None, {"enrollment_id": self.alice}, {"name": "Alice"}, None, booking=self.booking)
                return describer._absences_revise({"status": status}, {"absence_id": absence})

        created = self.client.post("/api/absences", json={"section_id": self.sections[507], "enrollment_id": self.alice, "first_date": QUIZ_DAY,
                                                          "reason": "illness", "request_id": "said"}, headers=HEADERS).json
        off = effect(created["id"], "excused")  # the term's cards off: nothing goes out by itself
        self.assertEqual(off["audience"], PRIVATE)
        self.assertNotIn("Then", [c["label"] for c in off["changes"]])
        self.assertEqual(lines(), [])
        self.turn_on()
        self.assertEqual(lines(), ["Quiz 4 makeup: Faculty emails them a booking card by itself once the absence is excused"])
        on = effect(created["id"], "excused")
        then = next(c for c in on["changes"] if c["label"] == "Then")
        self.assertTrue(then["show"])  # on the card's face, not folded under Show the whole proposal
        self.assertEqual(then["value"], "Faculty emails them a booking card for Quiz 4 by itself, from your Gmail (teacher@example.test), to pick a makeup time.")
        self.assertEqual(on["audience"], BOOKING_CARD)
        not_excused = effect(created["id"], "not_excused")  # the decision chosen on the card decides what it says
        self.assertEqual(not_excused["audience"], PRIVATE)
        self.assertNotIn("Then", [c["label"] for c in not_excused["changes"]])
        held = self.client.post(f"/api/absences/{created['id']}/makeup-hold", json={"request_id": "said-hold", "held": True, "reason": "their test"}, headers=HEADERS)
        self.assertEqual(held.status_code, 200, held.json)
        self.assertEqual(effect(created["id"], "excused")["audience"], PRIVATE)  # MAKEUP HOLDS: no card goes out
        self.assertEqual(lines(), ["Quiz 4 makeup: no booking card goes out while it is on hold"])
        released = self.client.post(f"/api/absences/{created['id']}/makeup-hold", json={"request_id": "said-release", "held": False, "revision": held.json["revision"]}, headers=HEADERS)
        self.assertEqual(released.status_code, 200, released.json)
        decided = self.revise(created, "said-d", status="excused")
        self.assertEqual(decided.status_code, 200, decided.json)
        self.assertEqual(lines(), ["Quiz 4 makeup: booking card goes out shortly, by itself, from your Gmail"])
        self.drain()
        while self.cards.mail.process_one():
            pass
        self.assertRegex(lines()[0], r"^Quiz 4 makeup: booking card emailed \w{3}, \w{3} \d+, \d+:\d\d [ap]m from teacher@example\.test; no answer yet$")
        self.pick(self.card(self.alice)["id"], MONDAY)
        self.drain()
        self.assertEqual(lines(), ["Quiz 4 makeup: Booked: Mon, Oct 6 · 3:00 pm · BLOC 309"])

    def test_students_picking_one_time_share_its_sitting(self):
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        self.pick(self.card(self.alice)["id"], MONDAY)
        self.pick(self.card(self.bob)["id"], MONDAY)
        event = self.exams.get(self.event["id"])
        makeup = [s for s in event["sittings"] if s["kind"] == "makeup"]
        self.assertEqual([(s["label"], s["time"], s["location"]) for s in makeup], [("Makeup · Mon, Oct 6 · 3:00–5:00 pm", "15:00", "BLOC 309")])
        seated = {p["enrollment_id"] for p in event["participants"] if p["sitting_id"] == makeup[0]["id"]}
        self.assertEqual(seated, {self.alice, self.bob})

    def test_another_time_seated_by_hand_is_settled(self):
        # Requirement, 2026-10-16: a student asked for noon, they made a noon makeup sitting and seated him; the row still read
        # "Needs another time". Seated by hand, the request is answered: the row reads as any sitting set by hand, unflagged.
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.pick(self.card(self.alice)["id"], slots.OTHER, "Around noon on Monday")
        self.assertEqual(self.row(self.alice)["booking"]["state"], "other_time")
        event = self.exams.get(self.event["id"])
        before = {s["id"] for s in event["sittings"]}
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "kind": "makeup", "label": "Makeup",
                                                      "date": "2036-10-06", "time": "12:00", "location": "BLOC 319"})
        sitting = next(s["id"] for s in event["sittings"] if s["id"] not in before)
        person = next(p for p in event["participants"] if p["enrollment_id"] == self.alice)
        self.exams.update_participant(event["id"], person["section_id"], self.alice, {"version": person["version"], "sitting_id": sitting})
        booking = self.row(self.alice)["booking"]
        # BOOKED BY HAND (2026-10-16): a dated sitting set by hand reads Booked, as a pick does.
        self.assertEqual((booking["state"], booking["label"], booking["flag"], booking["pick"]["choice"]),
                         ("booked", "Booked: Mon, Oct 6 · 12:00 pm · BLOC 319", False, "Around noon on Monday"))
        self.assertEqual((self.home() or {"parts": {"flags": 0}})["parts"]["flags"], 0)  # Home asks nothing of you for it

    def test_another_time_is_flagged_and_a_new_card_offers_the_next_two_days_after_it(self):
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.drain()
        first = self.card(self.alice)
        self.pick(first["id"], slots.OTHER, "Only Wednesday mornings")
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["flag"], booking["pick"]["choice"]), ("other_time", True, "Only Wednesday mornings"))
        home = self.home()
        self.assertEqual((home["label"], home["count"], home["parts"]["flags"]), ("Makeups that need you", 1, 1))
        self.assertNotIn("Alice", json.dumps(home))  # Home names no student
        made = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": self.event["id"], "section_id": self.sections[507],
                                                                     "enrollment_id": self.alice, "absence_id": absence["id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        # NEXT TWO DAYS (2026-10-06): the first card offered Monday and Tuesday; the next two days with times after them are
        # Thursday's meeting hours and the next Monday's office hours.
        self.assertEqual((made.json["attempt"], made.json["week"], made.json["options"][0], made.json["options"][-1]),
                         (2, "2036-10-09", "Thu, Oct 9 · 1:00 pm · BLOC 319", MONDAY.replace("Oct 6", "Oct 13")))
        again = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": self.event["id"], "section_id": self.sections[507],
                                                                      "enrollment_id": self.alice, "absence_id": absence["id"]}, headers=HEADERS)
        self.assertEqual(again.json["id"], made.json["id"])  # a lost answer sent again
        self.drain()
        second = self.card(self.alice, 2)
        self.assertEqual(second["publish_status"], "published")
        while self.cards.mail.process_one():
            pass
        update = self.relay.sent[-1]
        self.assertTrue(update["subject"].startswith("UPDATED: Book your Quiz 4 makeup for "), update["subject"])
        self.assertIn("This card offers new times and replaces the one sent earlier. You missed Quiz 4", update["text_body"])
        self.assertNotIn("replaces", self.relay.sent[0]["text_body"])
        self.assertEqual(self.remote.controls, {})  # the first card was answered: nothing to withdraw
        self.assertEqual(self.row(self.alice)["booking"]["state"], "sent")

    # -- BACK TO FOLLOW-UP (requirement, 2026-11-12) ----------------------
    def followups(self, enrollment):
        listed = self.client.get(f"/api/student-support?kind=followup&enrollment_id={enrollment}")
        self.assertEqual(listed.status_code, 200, listed.json)
        return listed.json["items"]

    def test_another_time_comes_back_as_a_follow_up_once_with_their_words(self):
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.assertEqual(self.followups(self.alice), [])
        self.pick(self.card(self.alice)["id"], slots.OTHER, "Fri. Oct 10 at 1PM  Mon. Oct 13 at 1PM")
        self.assertEqual(self.drain(), 0)  # opened in the pick's own round, and only once
        (item,) = self.followups(self.alice)
        self.assertEqual((item["title"], item["state"], item["attention"], item["actor"], item["source_key"], item["next"]),
                         ("Quiz 4 makeup: needs another time", "needs_reply", True, "faculty", "", "Seat them at a time that works, or send a new card"))
        self.assertEqual(item["details"], "On the Quiz 4 makeup booking card they answered that none of its times work (Sat, Oct 4): "
                                          "“Fri. Oct 10 at 1PM Mon. Oct 13 at 1PM”. The card offered Mon, Oct 6 and Tue, Oct 7. "
                                          "The makeup is due by Sun, Nov 2.")
        self.assertEqual(self.row(self.alice)["booking"]["state"], "other_time")  # the Makeups view still flags it
        suggestions = self.app.extensions["assistant"].followups
        with connect(self.app.config["DATABASE"]) as db:
            # The Follow-up page's line says their words, and the preparation is told what they asked for and what stays open.
            self.assertEqual([line["text"] for line in self.booking.followup_cards(db, [self.alice])[self.alice]],
                             ["Quiz 4 makeup: Needs another time: “Fri. Oct 10 at 1PM Mon. Oct 13 at 1PM”"])
            key = "item:" + item["id"]
            source = suggestions.source(db, self.alice, key)
            self.assertTrue(suggestions.eligible(suggestions.index(db, self.alice)[(self.alice, key)], source))  # prepared by itself
            term = db.execute("SELECT * FROM course_terms WHERE id=?", (self.term,)).fetchone()
            run = {"enrollment_id": self.alice, "item_key": key, "item_id": item["id"], "source_key": "", "source_json": json.dumps(source)}
            with mock.patch("scheduling.clock.campus_today", return_value=self.now.date()):  # the class schedule's days are read from today
                facts = suggestions._record_facts(db, run, term)
            other = suggestions._record_facts(db, {**run, "item_key": "item:" + str(uuid.uuid4())}, term)
        self.assertIn("The student sent no message. Faculty opened this follow-up when they answered a quiz makeup booking card Sat, Oct 4, "
                      "12:00 PM campus time, asking for another time", facts)
        self.assertIn('- Quiz 4 (given Fri, Oct 3): they answered its booking card that none of its times work and asked for another time, '
                      'writing: "Fri. Oct 10 at 1PM Mon. Oct 13 at 1PM". The card offered Mon, Oct 6 and Tue, Oct 7; it is answered, so '
                      'never point them back to it. The makeup is due by Sun, Nov 2.', facts)
        self.assertIn("these open times and asks which works best: Thu, Oct 9 · BLOC 319 · a time they choose between 1:00 pm and 5:00 pm; "
                      + MONDAY.replace("Oct 6", "Oct 13"), facts)  # the next two days with times after the card's
        self.assertNotIn("point them to it", facts)
        self.assertEqual(other, "")  # any other follow-up without a message has no facts, as before
        # With an Assistant model chosen, its preparation is queued by itself and carries those facts.
        suggestions.interval = 0
        with mock.patch("assistant.conversations.config.assistant", return_value={"kind": "hosted", "name": "Scripted", "reasoning_choice": "high"}):
            self.assertTrue(suggestions.process_one())
        with connect(self.app.config["DATABASE"]) as db:
            queued = dict(suggestions.latest(db, self.alice, key))
        self.assertEqual((queued["state"], queued["reason"], queued["item_id"]), ("queued", "automatic", item["id"]))
        with mock.patch("scheduling.clock.campus_today", return_value=self.now.date()):
            self.assertIn('asked for another time, writing: "Fri. Oct 10 at 1PM Mon. Oct 13 at 1PM"', suggestions.prompt(queued))

    def test_another_time_answered_outside_its_follow_up_resolves_it(self):
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        self.pick(self.card(self.alice)["id"], slots.OTHER, "Around noon on Monday")
        self.pick(self.card(self.bob)["id"], slots.OTHER)
        self.assertIn("none of its times work (Sat, Oct 4), and named no other time.", self.followups(self.bob)[0]["details"])
        # Seated by hand on the Exams page (2026-10-16): the request is answered, so its follow-up closes with what happened.
        event = self.exams.get(self.event["id"])
        before = {s["id"] for s in event["sittings"]}
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "kind": "makeup", "label": "Makeup · Mon, Oct 6 · 12:00 pm",
                                                      "date": "2036-10-06", "time": "12:00", "location": "BLOC 319"})
        sitting = next(s["id"] for s in event["sittings"] if s["id"] not in before)
        person = next(p for p in event["participants"] if p["enrollment_id"] == self.alice)
        self.exams.update_participant(event["id"], person["section_id"], self.alice, {"version": person["version"], "sitting_id": sitting})
        made = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": self.event["id"], "section_id": self.sections[507],
                                                                     "enrollment_id": self.bob, "absence_id": self.row(self.bob)["absence_id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        self.drain()
        (alice,), (bob,) = self.followups(self.alice), self.followups(self.bob)
        self.assertEqual((alice["state"], alice["attention"]), ("resolved", False))
        self.assertTrue(alice["details"].endswith("\n\nThey are on a makeup sitting: Makeup · Mon, Oct 6 · 12:00 pm. (Faculty, 2036-10-04)"), alice["details"])
        self.assertEqual(bob["state"], "resolved")
        self.assertTrue(bob["details"].endswith("\n\nA new booking card went out. (Faculty, 2036-10-04)"), bob["details"])
        self.assertEqual(self.drain(), 0)
        self.assertEqual(absence["id"], self.row(self.alice)["absence_id"])

    def test_another_time_taken_up_in_follow_up_is_no_longer_flagged_on_the_makeups_view(self):
        # TAKEN UP IN FOLLOW-UP (requirement, 2026-11-12).
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.pick(self.card(self.alice)["id"], slots.OTHER, "Only Wednesday mornings")
        (item,) = self.followups(self.alice)
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["flag"], "followup" in booking), ("other_time", True, False))  # Needs my reply: still yours
        self.assertEqual(self.home()["parts"]["flags"], 1)

        def save(request, state, revision):
            saved = self.client.post(f"/api/student-support/{item['id']}", headers=HEADERS, json={
                "request_id": request, "base_revision": revision, "title": item["title"], "details": item["details"], "state": state,
                "next": "Her answer about Wednesday at 9"})
            self.assertEqual(saved.status_code, 200, saved.json)
            return self.followups(self.alice)[0]

        waiting = save("mine", "waiting", item["revision"])
        with connect(self.app.config["DATABASE"]) as db:
            since = booking_module.campus_day(db.execute("SELECT created_at FROM student_support_revisions WHERE item_id=? AND revision=?",
                                          (item["id"], waiting["revision"])).fetchone()[0])
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"], booking["flag"], booking["pick"]["choice"]),
                         ("other_time", "Needs another time", False, "Only Wednesday mornings"))
        self.assertEqual(booking["followup"], {"id": item["id"], "state": "waiting", "label": f"Waiting on student since {slots.day_label(since)}"})
        self.assertEqual((self.home() or {"parts": {"flags": 0}})["parts"]["flags"], 0)  # Follow-up carries it now, not Home's makeups card
        with connect(self.app.config["DATABASE"]) as db:  # the follow-up's own line keeps their words, unflagged
            self.assertEqual([(line["flag"], line["text"]) for line in self.booking.followup_cards(db, [self.alice])[self.alice]],
                             [(False, "Quiz 4 makeup: Needs another time: “Only Wednesday mornings”")])
        todo = save("mine-2", "todo", waiting["revision"])
        self.assertEqual(self.row(self.alice)["booking"]["followup"]["label"], f"To do since {slots.day_label(since)}")
        # Resolved while nothing answers the request (no sitting, no new card): nothing carries it, so the row is flagged again.
        save("mine-3", "resolved", todo["revision"])
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["flag"], "followup" in booking), ("other_time", True, False))
        self.assertEqual(self.home()["parts"]["flags"], 1)

    def test_another_time_follow_up_the_instructor_took_up_is_theirs_to_settle(self):
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        self.booking.followups, kept = None, self.booking.followups
        self.pick(self.card(self.alice)["id"], slots.OTHER, "Only Wednesday mornings")
        self.pick(self.card(self.bob)["id"], slots.OTHER, "Any Friday")
        self.assertEqual(self.followups(self.alice), [])
        # An answer recorded before this rule (or while the Assistant was not there) gets its follow-up on the next turn.
        self.booking.followups = kept
        self.assertEqual(self.drain(), 2)
        (alice,), (bob,) = self.followups(self.alice), self.followups(self.bob)
        # The instructor saved one (Waiting on student) and approved a step of the other's preparation: seating them by hand
        # afterwards leaves both as they are, so no step still waiting is withdrawn.
        saved = self.client.post(f"/api/student-support/{alice['id']}", headers=HEADERS, json={
            "request_id": "mine", "base_revision": alice["revision"], "title": alice["title"], "details": alice["details"], "state": "waiting",
            "next": "Her answer about Wednesday at 9"})
        self.assertEqual(saved.status_code, 200, saved.json)
        suggestions = self.app.extensions["assistant"].followups
        with connect(self.app.config["DATABASE"]) as db:
            self.assertTrue(suggestions.untouched(db, bob["id"]))
            self.assertFalse(suggestions.untouched(db, alice["id"]))
            key = "item:" + bob["id"]
            item = suggestions.index(db, self.bob)[(self.bob, key)]
            run = suggestions.insert(db, suggestions.target(item), suggestions.source(db, self.bob, key), {"name": "Test"})
            db.commit()
        assistant = self.app.extensions["assistant"]
        with suggestions._session("faculty", "Seat him", _proposal_only=True, _followup_run=run) as session:
            action = assistant._record_action(session, "approve", "exams.sitting", "POST", f"/api/exams/{self.event['id']}/sittings", {}, "Seat him")
        assistant._event(action, "proposed", "assistant", {})
        with connect(self.app.config["DATABASE"]) as db:
            self.assertTrue(suggestions.untouched(db, bob["id"]))  # a step that only waits changes nothing
        assistant._event(action, "approved", "instructor", {})
        with connect(self.app.config["DATABASE"]) as db:
            self.assertFalse(suggestions.untouched(db, bob["id"]))
        event = self.exams.get(self.event["id"])
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "kind": "makeup", "label": "Makeup", "date": "2036-10-08",
                                                      "time": "09:00", "location": "BLOC 319"})
        sitting = next(s["id"] for s in event["sittings"] if s["kind"] == "makeup")
        for enrollment in (self.alice, self.bob):
            event = self.exams.get(self.event["id"])
            person = next(p for p in event["participants"] if p["enrollment_id"] == enrollment)
            self.exams.update_participant(event["id"], person["section_id"], enrollment, {"version": person["version"], "sitting_id": sitting})
        self.drain()
        self.assertEqual([(i["state"], i["revision"]) for i in self.followups(self.alice)], [("waiting", alice["revision"] + 1)])
        self.assertEqual([(i["state"], i["revision"]) for i in self.followups(self.bob)], [("needs_reply", bob["revision"])])

    def test_a_missed_makeup_past_its_due_date_gets_a_new_card_and_a_later_due_date(self):
        # A MISSED MAKEUP (requirement, 2026-10-18): she booked, missed the
        # sitting, and the due date (30 days after the absence) passed; "Send a new card" offers the next two days with times,
        # moves the due date to the card's last day and lets the missed sitting go, so the card goes out and her pick seats her.
        self.turn_on()
        absence = self.excuse(self.bob, "b1")
        self.drain()
        self.pick(self.card(self.bob)["id"], AFTERNOON[0])
        self.now = dt.datetime(2036, 11, 3, 9, 0)  # a Monday, past the due date of Sun, Nov 2
        booking = self.row(self.bob)["booking"]
        self.assertEqual((booking["state"], booking["due"]), ("paper_not_in", "2036-11-02"))
        made = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": self.event["id"], "section_id": self.sections[507],
                                                                     "enrollment_id": self.bob, "absence_id": absence["id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        self.assertEqual((made.json["attempt"], made.json["week"], made.json["due"], made.json["options"][0], made.json["options"][-1]),
                         (2, "2036-11-04", "2036-11-06", "Tue, Nov 4 · 1:00 pm · BLOC 319", "Thu, Nov 6 · 4:30 pm · BLOC 319"))
        row = self.row(self.bob)
        self.assertIsNone(row["scheduled"])
        self.assertEqual((row["booking"]["state"], row["booking"]["due"], row["booking"]["due_day"]), ("sending", "2036-11-06", "Thu, Nov 6"))
        self.drain()
        self.assertEqual(self.card(self.bob, 2)["publish_status"], "published")
        self.assertEqual(self.row(self.bob)["booking"]["state"], "sent")
        self.pick(self.card(self.bob, 2)["id"], "Thu, Nov 6 · 2:00 pm · BLOC 319")
        row = self.row(self.bob)
        self.assertEqual((row["booking"]["state"], row["scheduled"]["date"], row["scheduled"]["time"]), ("booked", "2036-11-06", "14:00"))

    def test_a_card_you_send_offers_two_days_even_when_only_one_is_left_before_the_due_date(self):
        # ALWAYS TWO DAYS (requirement, 2026-11-12): three days before the due date (Sun, Nov 2) only Thursday's meeting hours are left, so the card you
        # send keeps looking past it for its second day, Tuesday, and the due date moves to that day.
        self.turn_on()
        absences = {self.alice: self.excuse(self.alice, "a1"), self.bob: self.excuse(self.bob, "b1")}
        self.drain()
        self.now = dt.datetime(2036, 10, 30, 9, 0)  # a Thursday: its meeting hours, then nothing until the due date
        self.assertEqual(self.row(self.alice)["booking"]["due"], "2036-11-02")
        send = lambda who, request_id: self.client.post("/api/absences/makeups/cards", json={"request_id": request_id, "event_id": self.event["id"],
            "section_id": self.sections[507], "enrollment_id": who, "absence_id": absences[who]["id"]}, headers=HEADERS)
        made = send(self.alice, "n1")
        self.assertEqual(made.status_code, 201, made.json)
        self.assertEqual((made.json["attempt"], made.json["week"], made.json["due"], made.json["options"][0], made.json["options"][-1]),
                         (2, "2036-10-30", "2036-11-04", "Thu, Oct 30 · 1:00 pm · BLOC 319", "Tue, Nov 4 · 4:30 pm · BLOC 319"))
        self.assertEqual(sorted({option[:11].strip() for option in made.json["options"]}), ["Thu, Oct 30", "Tue, Nov 4"])
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["due"], booking["due_day"]), ("2036-11-04", "Tue, Nov 4"))
        # Nothing more within the days it looks ahead: the one day left is still offered, and the due date stands.
        with mock.patch.object(booking_module, "EXTEND", 1):
            only = send(self.bob, "n2")
        self.assertEqual(only.status_code, 201, only.json)
        self.assertEqual(({option[:11] for option in only.json["options"]}, only.json["due"]), ({"Thu, Oct 30"}, "2036-11-02"))

    def test_an_unanswered_card_is_withdrawn_when_the_makeup_stops_being_owed(self):
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.drain()
        card = self.card(self.alice)
        current = self.client.get(f"/api/absences/{absence['id']}").json
        self.assertEqual(self.revise(current, "a1-n", status="not_excused").status_code, 200)
        self.drain()
        self.assertEqual(list(self.remote.controls.values()), ["revoke"])
        self.assertEqual(next(iter(self.remote.controls))[0], card["id"])
        self.assertEqual(self.rows()["makeups"], [])

    def test_booking_business_days_skip_university_holidays(self):
        # SCHEDULING BUSINESS DAYS also governs ABSENCES' three-business-day card response interval.
        self.assertEqual(booking_module.business_days_after(dt.date(2026, 11, 25), 3), dt.date(2026, 12, 2))

    def test_a_pick_rechecks_whether_the_makeup_is_still_owed(self):
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.drain()
        card = self.card(self.alice)
        with connect(self.app.config["DATABASE"]) as db:
            earlier = self.booking._owed(db)
            offer = db.execute("SELECT id FROM absence_makeup_offers WHERE enrollment_id=?", (self.alice,)).fetchone()[0]
        current = self.client.get(f"/api/absences/{absence['id']}").json
        self.assertEqual(self.revise(current, "no-longer-excused", status="not_excused").status_code, 200)
        self.booking._seat({"submission_id": "late-pick", "offer_id": offer, "card_id": card["id"],
                            "submission_json": json.dumps({"answers": {slots.FIELD: AFTERNOON[0]}})}, earlier)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT outcome FROM absence_makeup_picks WHERE submission_id='late-pick'").fetchone()[0], "stale")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_sittings WHERE kind='makeup'").fetchone()[0], 0)

    # -- NO ANSWER, THEN A 0 (requirement, 2026-11-12) ----------------
    def read_mail(self, at):
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("UPDATE comm_capture_state SET status='ok', last_round_at=?, last_success_at=? WHERE channel IN ('gmail','canvas')", (at, at))
            db.commit()

    def wrote(self, enrollment, ident, at):
        from communications.record import record
        with self.app.extensions["rosters"].lock(), connect(self.app.config["DATABASE"]) as db:
            record(db, "gmail", ident, {"sent_at": at, "author_role": "student", "author": {"name": "Student"}, "recipients": [], "subject": "Makeup",
                                        "body_text": "Could I come Thursday instead?", "attachments": [], "context": {"thread_id": ident}},
                   [(enrollment, self.term, "author", "roster_address")], ident)
            db.commit()

    def missed_rows(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [tuple(row) for row in db.execute("SELECT enrollment_id, missed, actor, request_id FROM absence_makeups_missed ORDER BY created_at, revision")]

    def test_an_unanswered_card_is_a_0_one_business_day_after_its_last_time_unless_they_write(self):
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        offer = self.row(self.alice)["booking"]["offer"]["id"]
        sent = "No answer; the card's times passed (sent Sat, Oct 4)"
        self.now = dt.datetime(2036, 10, 7, 17, 0)  # Tuesday, after the card's last time (4:30 pm): they have Wednesday left
        self.read_mail("2036-10-07T22:00:00+00:00")
        self.assertEqual(self.row(self.alice)["booking"]["label"], sent + " · a 0 is recorded after Wed, Oct 8 unless they write to you")
        self.now = dt.datetime(2036, 10, 8, 23, 59)
        self.drain()
        self.assertEqual(self.missed_rows(), [])
        # Thursday: the day has passed, but Gmail and Canvas were last read on Tuesday, so a message sent Wednesday could be unread.
        self.now = dt.datetime(2036, 10, 9, 0, 5)
        self.drain()
        self.assertEqual((self.missed_rows(), self.row(self.alice)["booking"]["label"]), ([], sent + " · a 0 is recorded once Gmail and Canvas have been read"))
        # Read since Wednesday ended: Bob wrote on Monday, Alice never did. Hers is recorded missed, as "Missed makeup, record 0" does.
        self.wrote(self.bob, "bob-1", "2036-10-06T15:00:00Z")
        self.read_mail("2036-10-09T05:02:00+00:00")
        self.drain()
        self.assertEqual(self.missed_rows(), [(self.alice, 1, "automatic", "makeup-no-answer:" + offer)])
        listed = self.rows()["makeups"]
        self.assertEqual([r["enrollment_id"] for r in listed], [self.bob])  # cleared from the Makeups view
        self.assertEqual((listed[0]["booking"]["state"], listed[0]["booking"]["flag"], listed[0]["booking"]["label"]),
                         ("no_answer", True, sent + " · they wrote to you since the card, so no 0 is recorded by itself"))
        card = self.card(self.alice)["id"]
        self.assertEqual(self.remote.controls, {(card, "makeup-revoke-" + card): "revoke"})  # her card is withdrawn, Bob's stands
        work = self.client.get(f"/api/absences/{absence['id']}").json["work"]
        self.assertEqual([(w["state"], w["state_label"]) for w in work], [("missed", "Missed makeup: 0 · no answer to the booking card")])
        # Undo is the instructor's: the makeup is owed again and Faculty never records that card's 0 a second time.
        undone = self.client.post("/api/absences/makeups/missed", headers=HEADERS, json={
            "request_id": "undo-1", "missed": False, "event_id": self.event["id"], "section_id": self.sections[507], "enrollment_id": self.alice})
        self.assertEqual(undone.status_code, 200, undone.json)
        self.drain()
        self.assertEqual([row[:3] for row in self.missed_rows()], [(self.alice, 1, "automatic"), (self.alice, 0, "workspace")])
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"]), ("expired", "Card withdrawn"))

    def score(self, enrollment, points, request_id):
        with connect(self.app.config["DATABASE"]) as db:
            base = db.execute("SELECT id FROM gradebook_scores WHERE enrollment_id=? ORDER BY seq DESC LIMIT 1", (enrollment,)).fetchone()
        written = self.client.post(f"/api/gradebook/terms/{self.term}/scores", headers=HEADERS, json={"request_id": request_id, "changes": [
            {"item_id": self.quiz_column, "row": f"{self.sections[507]}:{enrollment}", "base": base[0] if base else None, "status": "scored", "points": points}]})
        self.assertEqual(written.status_code, 200, written.json)

    def test_a_0_is_never_recorded_over_a_score_and_is_taken_back_when_one_turns_up(self):
        # A SCORE STANDS (requirement, 2026-11-14).
        self.quiz_column = self.setup_gradebook([{"title": "Quiz 4", "points": "10", "assignment_id": self.assignment["id"]}])["Quiz 4"]
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        offer = self.row(self.bob)["booking"]["offer"]["id"]
        sent = "No answer; the card's times passed (sent Sat, Oct 4)"
        self.score(self.alice, "8", "alice-8")  # she took the quiz with her class after all
        self.score(self.bob, "0", "bob-0")  # a 0 is no score to keep
        self.now = dt.datetime(2036, 10, 9, 0, 5)
        self.read_mail("2036-10-09T05:02:00+00:00")
        self.assertEqual(self.row(self.alice)["booking"]["label"], sent + " · they have 8 for it in the gradebook, so no 0 is recorded")
        self.drain()
        self.assertEqual(self.missed_rows(), [(self.bob, 1, "automatic", "makeup-no-answer:" + offer)])
        self.assertEqual(self.row(self.alice)["booking"]["state"], "no_answer")  # still owed, flagged for you, never a 0 by itself
        # A score turns up after the 0 (a late paper's grade, a typed or imported value): Faculty takes its own 0 back, once.
        self.score(self.bob, "6", "bob-6")
        self.drain()
        self.drain()
        self.assertEqual(self.missed_rows(), [(self.bob, 1, "automatic", "makeup-no-answer:" + offer),
                                              (self.bob, 0, "automatic", "makeup-no-answer-scored:" + offer)])
        booking = self.row(self.bob)["booking"]
        self.assertEqual((booking["state"], booking["label"]), ("expired", "Card withdrawn · its 0 was taken back: they have 6 for it in the gradebook"))
        # Your own "Missed makeup, record 0" over a score is yours: Faculty never takes it back.
        made = self.client.post("/api/absences/makeups/missed", headers=HEADERS, json={
            "request_id": "yours", "missed": True, "event_id": self.event["id"], "section_id": self.sections[507], "enrollment_id": self.alice})
        self.assertEqual(made.status_code, 200, made.json)
        self.drain()
        self.assertEqual(self.missed_rows()[-1][:3], (self.alice, 1, "workspace"))

    def test_a_card_that_can_still_be_answered_or_never_went_out_is_never_a_0(self):
        self.cards.mail.gmail.connected = False  # Bob's card never goes out: Gmail is not connected until its times have passed
        self.turn_on()
        self.excuse(self.bob, "b1")
        self.drain()
        self.cards.mail.gmail.connected = True
        self.now = dt.datetime(2036, 10, 9, 9, 0)
        self.excuse(self.alice, "a1")  # Alice's card goes out Thursday; its last time is Monday at 3 pm
        self.drain()
        self.now = dt.datetime(2036, 10, 13, 9, 0)
        self.read_mail("2036-10-20T12:00:00+00:00")
        with mock.patch.object(booking_module, "NO_ANSWER_DAYS", 1):  # flagged after one business day here, a time still to come
            self.drain()
            rows = {r["enrollment_id"]: r["booking"] for r in self.rows()["makeups"]}
        self.assertEqual((rows[self.bob]["state"], rows[self.bob]["label"]), ("no_answer", "The card's times passed before it went out"))
        self.assertEqual((rows[self.alice]["state"], rows[self.alice]["label"]), ("no_answer", "No answer since Thu, Oct 9"))
        self.assertEqual(self.missed_rows(), [])
        self.now = dt.datetime(2036, 10, 14, 23, 0)  # Tuesday is hers to write; from Wednesday it is a 0
        self.drain()
        self.assertEqual(self.missed_rows(), [])
        self.now = dt.datetime(2036, 10, 15, 0, 1)
        self.drain()
        self.assertEqual([row[:3] for row in self.missed_rows()], [(self.alice, 1, "automatic")])

    def test_no_answer_and_no_paper_are_flagged(self):
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        self.now = dt.datetime(2036, 10, 9, 9, 0)  # Thursday: 3 business days after Saturday's card is Wednesday
        self.assertEqual(self.row(self.alice)["booking"]["state"], "no_answer")
        self.now = SATURDAY
        self.pick(self.card(self.bob)["id"], AFTERNOON[0])
        self.now = dt.datetime(2036, 10, 8, 9, 0)
        booking = self.row(self.bob)["booking"]
        self.assertEqual((booking["state"], booking["flag"], booking["label"]), ("paper_not_in", True, "No paper yet from the makeup on Tue, Oct 7"))
        self.assertEqual(self.home()["parts"]["flags"], 2)


class GmailTests(BookingBase):
    """GMAIL (requirement, 2026-10-06): a card waits while Gmail is not connected; a card withdrawn
    before it went out (Piper Hollis's, stuck at publishing) reads Card withdrawn, never goes out, and a new card starts from today."""

    def test_a_card_waits_for_gmail_and_goes_out_once_it_is_connected(self):
        self.cards.mail.gmail.connected = False
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.assertEqual(self.count("response_card_mail"), 0)  # published, never sent another way
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"]), ("sending", "Card waits for Gmail: reconnect Gmail in Response Cards"))
        self.cards.mail.gmail.connected = True
        self.drain()
        while self.cards.mail.process_one():
            pass
        self.assertEqual([m["recipient"] for m in self.relay.sent], ["alice@example.test"])
        self.assertEqual(self.row(self.alice)["booking"]["state"], "sent")

    def test_a_card_withdrawn_before_it_went_out_stays_out_and_a_new_one_starts_from_today(self):
        self.cards.mail.gmail.connected = False
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.drain()
        first = self.card(self.alice)
        options = self.row(self.alice)["booking"]["offer"]["options"]
        with self.absences.rosters.lock(), connect(self.app.config["DATABASE"]) as db:
            db.execute("BEGIN IMMEDIATE")
            self.cards.invalidate_cards(db, [first["id"]], reason="makeup_withdrawn")
        self.cards.mail.gmail.connected = True
        self.drain()
        while self.cards.mail.process_one():
            pass
        self.assertEqual(self.relay.sent, [])
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"], booking["flag"]), ("expired", "Card withdrawn before it went out", True))
        row = self.row(self.alice)
        sent = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": row["event_id"], "section_id": row["section_id"],
                                                                       "enrollment_id": self.alice, "absence_id": absence["id"]}, headers=HEADERS)
        self.assertEqual(sent.status_code, 201, sent.json)
        self.assertEqual(sent.json["options"], options)  # today's two days again, not the days after the withdrawn card's
        self.drain()
        self.assertEqual(self.row(self.alice)["booking"]["state"], "sent")


class HoldTests(BookingBase):
    """MAKEUP HOLDS (requirement, 2026-10-06, of a student told to wait until her concussion is reassessed): no card and no time while
    an absence's makeups are held; a check-back day flags it; a release (or a card sent by hand) lets the next card go out."""

    def hold(self, absence_id, request_id, **body):
        return self.client.post(f"/api/absences/{absence_id}/makeup-hold", json={"request_id": request_id, **body}, headers=HEADERS)

    def test_a_held_absence_gets_no_card_until_released_and_is_flagged_on_its_check_back_day(self):
        self.turn_on()
        created = self.client.post("/api/absences", json={"section_id": self.sections[507], "enrollment_id": self.alice, "first_date": QUIZ_DAY,
                                                          "last_date": QUIZ_DAY, "reason": "illness", "request_id": "a1"}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        absence = created.json
        self.assertEqual(self.row(self.alice)["hold"], {"held": False, "revision": 0})  # an undecided absence can be held too
        held = self.hold(absence["id"], "h1", held=True, reason="her concussion reassessment", check_back="2036-10-08", revision=0)
        self.assertEqual(held.status_code, 200, held.json)
        self.assertEqual({k: held.json[k] for k in ("held", "revision", "reason", "check_back", "check_back_day")},
                         {"held": True, "revision": 1, "reason": "her concussion reassessment", "check_back": "2036-10-08", "check_back_day": "Wed, Oct 8"})
        self.assertEqual(self.hold(absence["id"], "h1", held=True, reason="something else").status_code, 409)  # one request, one change
        self.assertEqual(self.hold(absence["id"], "h1", held=True, reason="her concussion reassessment", check_back="2036-10-08").json["revision"], 1)
        self.assertEqual(self.revise(absence, "a1-d", status="excused").status_code, 200)
        self.assertEqual(self.drain(), 0)
        self.assertEqual((self.count("absence_makeup_offers"), self.count("response_card_issues")), (0, 0))
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"], booking["flag"]), ("held", "her concussion reassessment · check back Wed, Oct 8", False))
        plan = self.client.get("/api/absences/makeups/plan").json
        self.assertEqual((plan["cards"], [(h["title"], h["reason"], h["check_back"]) for h in plan["held"]]),
                         ([], [("Quiz 4", "her concussion reassessment", "2036-10-08")]))
        self.assertFalse(((self.home() or {}).get("parts") or {}).get("flags"))  # nothing to look at until its check-back day
        self.now = dt.datetime(2036, 10, 8, 9, 0)  # its check-back day: flagged, still no card
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"], booking["flag"]), ("check_back", "Check back today: her concussion reassessment", True))
        self.assertEqual(self.home()["parts"]["flags"], 1)
        self.assertEqual(self.drain(), 0)
        # A release names the hold it read: stale or missing is refused; then the card goes out by itself.
        self.assertEqual(self.hold(absence["id"], "r0", held=False).status_code, 400)
        self.assertEqual(self.hold(absence["id"], "r0", held=False, revision=0).status_code, 409)
        released = self.hold(absence["id"], "r1", held=False, revision=1)
        self.assertEqual(released.json["held"], False)
        self.assertEqual(released.json["revision"], 2)
        self.assertEqual(self.row(self.alice)["booking"]["state"], "queued")
        self.drain()
        self.assertEqual((self.card(self.alice)["attempt"], self.row(self.alice)["booking"]["state"]), (1, "sent"))
        self.assertEqual(self.hold(absence["id"], "r2", held=False, revision=2).json["revision"], 2)  # not held: nothing recorded

    def test_a_hold_withdraws_an_unanswered_card_and_its_release_sends_one_from_today(self):
        self.turn_on()
        absence = self.excuse(self.alice, "a1")
        self.drain()
        first = self.card(self.alice)
        options = self.row(self.alice)["booking"]["offer"]["options"]
        self.now = SATURDAY + dt.timedelta(hours=1)
        self.assertEqual(self.hold(absence["id"], "h1", held=True, reason="a doctor's clearance").status_code, 200)
        self.drain()
        self.assertEqual(self.remote.controls, {(first["id"], "makeup-revoke-" + first["id"]): "revoke"})
        self.assertEqual(self.row(self.alice)["booking"]["state"], "held")
        self.now = SATURDAY + dt.timedelta(hours=2)
        self.assertEqual(self.hold(absence["id"], "r1", held=False, revision=1).status_code, 200)
        self.drain()
        second = self.card(self.alice, attempt=2)
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["attempts"], booking["offer"]["options"]), ("sent", 2, options))  # today's two days again
        self.assertEqual(self.drain(), 0)  # one card per release
        self.assertNotEqual(second["id"], first["id"])

    def test_sending_a_card_by_hand_releases_the_hold(self):
        absence = self.excuse(self.alice, "a1")  # this term's cards are off
        self.assertEqual(self.hold(absence["id"], "h1", held=True).status_code, 200)
        row = self.row(self.alice)
        self.assertEqual((row["booking"]["state"], row["booking"]["label"], row["hold"]["held"]), ("held", "Until you release it", True))
        sent = self.client.post("/api/absences/makeups/cards", json={"request_id": "n1", "event_id": row["event_id"], "section_id": row["section_id"],
                                                                       "enrollment_id": self.alice, "absence_id": absence["id"]}, headers=HEADERS)
        self.assertEqual(sent.status_code, 201, sent.json)
        self.assertEqual(self.row(self.alice)["hold"]["held"], False)
        self.drain()
        self.assertEqual(self.row(self.alice)["booking"]["state"], "sent")
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT actor, request_id FROM absence_makeup_holds WHERE revision=2").fetchone()[:], ("workspace", "new-card-release:n1"))

    def test_the_assistant_never_holds_for_a_known_return_and_its_card_goes_out_at_excusal(self):
        # A KNOWN RETURN (requirement, 2026-11-09): the card's times already start from the return,
        # so the assistant's hold for one is refused (409, also from the chat's Approve cards); you may still hold it yourself.
        self.turn_on()
        dated = self.excuse(self.alice, "a1", details="Doctor's note: she can return to class on October 9.")
        open_ended = self.excuse(self.bob, "b1")
        def assistant(absence_id, request_id, reason):
            return self.client.post(f"/api/absences/{absence_id}/makeup-hold", json={"request_id": request_id, "held": True, "reason": reason,
                                    "check_back": "2036-10-09"}, headers=HEADERS, environ_base={"faculty.actor": "assistant"})
        refused = assistant(dated["id"], "h1", "Medical note permits return October 9")
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn("A KNOWN RETURN: the student can return on Thu, Oct 9", refused.json["error"])
        self.assertEqual(assistant(open_ended["id"], "h2", "Provider permits return October 9").status_code, 409)  # named in the reason
        self.assertEqual(assistant(open_ended["id"], "h3", "his uncertain return to class").status_code, 200)
        self.drain()
        alice = self.row(self.alice)
        self.assertEqual((alice["hold"]["held"], alice["booking"]["state"], alice["booking"]["offer"]["week"]), (False, "sent", "2036-10-09"))
        self.assertEqual(self.row(self.bob)["booking"]["state"], "held")
        self.assertEqual(self.hold(dated["id"], "h4", held=True, reason="Medical note permits return October 9").status_code, 200)  # yours


class CopyTests(BookingBase):
    def version(self):
        return self.documents.register_version(self.assignment["id"], "C", (FIXTURES / "letter.pdf").read_bytes(),
                                               {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})

    def approved(self, version):
        """The quiz's newest approved makeup version is `version` (GRADING "MAKEUP VERSIONS" is checked in test_makeup_drafts.py)."""
        real = booking_module.MakeupBooking._owed
        for name, value in (("_owed", lambda booking, db: [{**r, "plan_id": "plan-1"} for r in real(booking, db)]),
                            ("_version_id", lambda booking, db, plan: version["id"])):
            patcher = mock.patch.object(booking_module.MakeupBooking, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def copies(self):
        """[(quantity, term, section, creator, {enrollments it was made for}, the sitting's time)] of the print jobs made, oldest
        first. Every copy is printed for its student again, with their name filled in on the paper (requirement, 2026-10-18); whom a job was made for is also the makeup's own record."""
        with connect(self.app.config["DATABASE"]) as db:
            for b in db.execute("SELECT id FROM print_batches"):
                named = {r[0] for r in db.execute("SELECT enrollment_id FROM printed_copies WHERE print_batch_id=?", (b["id"],))}
                made_for = {r[0] for r in db.execute("SELECT enrollment_id FROM absence_makeup_copies WHERE print_batch_id=?", (b["id"],))}
                self.assertEqual(named, made_for)
                fills = db.execute("""SELECT COUNT(*) FROM printed_copy_name_fills f JOIN printed_copies c ON c.id=f.printed_copy_id
                    WHERE c.print_batch_id=?""", (b["id"],)).fetchone()[0]
                self.assertEqual(fills, len(named))
            return [(b["quantity"], b["term_id"], b["section_id"], b["creator"],
                     {r[0] for r in db.execute("SELECT enrollment_id FROM absence_makeup_copies WHERE print_batch_id=?", (b["id"],))},
                     db.execute("SELECT s.time FROM exam_event_batches e JOIN exam_sittings s ON s.id=e.sitting_id WHERE e.batch_id=?", (b["id"],)).fetchone()[0])
                    for b in db.execute("SELECT * FROM print_batches ORDER BY created_at,rowid")]

    def test_a_student_who_books_gets_a_named_copy_at_once(self):
        # It is Saturday noon; the sitting is Tuesday at 1 pm (until then: from Monday 6 pm).
        self.booking.grading = None  # the quiz's approved makeup version is given below
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        self.approved(self.version())
        self.pick(self.card(self.alice)["id"], AFTERNOON[0])
        self.assertEqual(self.copies(), [(1, self.term, None, "automatic", {self.alice}, "13:00")])
        self.assertEqual(self.drain(), 0)  # once: the copy made is hers
        self.assertIsNone(self.row(self.bob)["booking"].get("copy"))  # not booked yet: no copy
        self.now = SATURDAY + dt.timedelta(hours=3)
        self.pick(self.card(self.bob)["id"], AFTERNOON[0])  # the same sitting, later: a print job of his own
        self.assertEqual(self.copies(), [(1, self.term, None, "automatic", {self.alice}, "13:00"), (1, self.term, None, "automatic", {self.bob}, "13:00")])
        self.assertEqual(self.drain(), 0)
        with connect(self.app.config["DATABASE"]) as db:
            batches = {r["enrollment_id"]: r["print_batch_id"] for r in db.execute("SELECT enrollment_id,print_batch_id FROM absence_makeup_copies")}
        self.assertEqual({r["enrollment_id"]: r["booking"]["copy"]["batch_id"] for r in self.rows()["makeups"]}, batches)
        # Print PDF on the Makeups row (2026-10-12) is offered for a ready job only: a job the instructor superseded has no file
        # left, and its copy says so.
        self.assertEqual({r["booking"]["copy"]["status"] for r in self.rows()["makeups"]}, {"pending"})
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("UPDATE print_batches SET superseded_at='2036-10-05T00:00:00+00:00' WHERE id=?", (batches[self.alice],))
        self.assertEqual({r["enrollment_id"]: r["booking"]["copy"]["status"] for r in self.rows()["makeups"]}, {self.alice: "superseded", self.bob: "pending"})

    def test_a_ready_makeup_copy_prints_at_home_once(self):
        # AUTOMATIC MAKEUP PRINTS (requirement, 2026-10-18): the copy's job waits for the
        # approved version (the test above), then for its build, then goes to the home printer once; a refusal is tried again.
        from document_identity.printer import PrinterConfig
        from document_identity import printer as printer_module
        from test_printer_send import FakePrinter
        fake = FakePrinter()
        self.addCleanup(fake.close)
        sends = self.app.extensions["printer_sends"]
        self.booking.grading = None
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.pick(self.card(self.alice)["id"], AFTERNOON[0])
        self.drain()
        self.assertEqual(self.count("print_batches"), 0)  # no makeup version yet: nothing to print
        self.approved(self.version())
        self.drain()
        self.assertEqual(self.count("print_printer_sends"), 0)  # the job is not built yet, and no printer is set up
        documents = self.app.extensions["documents"]
        while documents.build_one():
            pass
        self.drain()
        self.assertEqual(self.count("print_printer_sends"), 0)  # still no printer set up: nothing is queued
        sends.config = PrinterConfig(fake.uri)
        fake.mode = "refuse"
        self.assertEqual(self.drain(), 1)
        self.assertTrue(sends.process_one())
        self.assertEqual(self.row(self.alice)["booking"]["copy"]["printed"], "failed")
        self.assertEqual(self.drain(), 0)  # refused: tried again only after PRINT_RETRY
        fake.mode = "ok"
        with mock.patch.object(booking_module, "PRINT_RETRY", 0):
            self.assertEqual(self.drain(), 1)
            self.assertTrue(sends.process_one())
            self.assertEqual(self.drain(), 0)  # accepted: never again
        copy = self.row(self.alice)["booking"]["copy"]
        self.assertEqual(copy["printed"], "accepted")
        # NAME FILLED IN (2026-10-18): her name is printed on the paper's "Name" line, to the right of the label and on its baseline.
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(documents.download(copy["batch_id"])) as pdf:
            text = pdf[0].get_textpage()
            name = self.row(self.alice)["name"]
            label = text.search("Name", match_case=True, match_whole_word=True).get_next()
            written = text.search(name, match_case=True).get_next()
            self.assertIsNotNone(label, "the makeup version has a Name line")
            self.assertIsNotNone(written, f"{name} is printed on page 1")
            label_box, written_box = text.get_charbox(label[0]), text.get_charbox(written[0])
            self.assertGreater(written_box[0], label_box[2])
            self.assertLess(abs(written_box[1] - label_box[1]), 4)
        self.assertEqual(fake.documents, [documents.download(copy["batch_id"])])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT approved_by FROM print_printer_sends")}, {"automatic"})

    def test_copies_wait_for_an_approved_makeup_version_and_are_made_once_there_is_one(self):
        # "If a makeup version hasn't been made yet, it should wait until one is and then send the print jobs once it is ready."
        self.booking.grading = None
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.excuse(self.bob, "b1")
        self.drain()
        self.pick(self.card(self.alice)["id"], AFTERNOON[0])
        self.pick(self.card(self.bob)["id"], AFTERNOON[0])
        version = self.version()
        self.now = dt.datetime(2036, 10, 6, 19, 0)  # the evening before the sitting, and still no approved makeup version
        self.drain()
        self.assertEqual(self.count("print_batches"), 0)
        self.assertEqual([r["booking"].get("copy") for r in self.rows()["makeups"]], [None, None])
        self.approved(version)
        self.drain()
        self.assertEqual(self.copies(), [(2, self.term, None, "automatic", {self.alice, self.bob}, "13:00")])  # one job for the sitting's waiting students
        self.assertEqual(self.drain(), 0)
        self.assertEqual(self.count("print_batches"), 1)


    def seat_by_hand(self, enrollment, date, time="15:00"):
        event = self.exams.get(self.event["id"])
        before = {s["id"] for s in event["sittings"]}
        event = self.exams.save_sitting(event["id"], {"version": event["version"], "kind": "makeup", "label": "Makeup",
                                                      "date": date, "time": time, "location": "BLOC 309"})
        sitting = next(s["id"] for s in event["sittings"] if s["id"] not in before)
        person = next(p for p in event["participants"] if p["enrollment_id"] == enrollment)
        self.exams.update_participant(event["id"], person["section_id"], enrollment, {"version": person["version"], "sitting_id": sitting})
        return sitting

    def test_a_sitting_booked_by_hand_reads_booked_and_gets_a_copy(self):
        # 
        self.booking.grading = None
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.approved(self.version())
        self.seat_by_hand(self.alice, "2036-10-07")
        self.drain()
        booking = self.row(self.alice)["booking"]
        self.assertEqual((booking["state"], booking["label"], booking["flag"]), ("booked", "Booked: Tue, Oct 7 · 3:00 pm · BLOC 309", False))
        self.assertEqual(self.copies(), [(1, self.term, None, "automatic", {self.alice}, "15:00")])
        self.assertEqual(booking["copy"]["status"], "pending")
        self.assertEqual(self.drain(), 0)
        self.now = dt.datetime(2036, 10, 8, 12, 0)  # its day passed with no paper: as a pick's would
        self.assertEqual(self.row(self.alice)["booking"]["state"], "paper_not_in")

    def test_an_undated_sitting_does_not_abort_copy_processing(self):
        # ABSENCES MAKEUP BOOKING: an undated hand-set sitting waits and cannot stop other worker turns.
        self.booking.grading = None
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.approved(self.version())
        self.seat_by_hand(self.alice, "")
        with connect(self.app.config["DATABASE"]) as db:
            rows = self.booking._owed(db)
        self.assertFalse(self.booking._copies_one(rows))
        self.assertEqual(self.count("print_batches"), 0)

    def test_clearing_the_date_of_a_sitting_with_a_made_copy_does_not_break_home(self):
        # ABSENCES MAKEUP BOOKING: an undated sitting reads as before. A copy made while the sitting was dated stays hers when the
        # instructor clears the date; Home's makeups card must not parse the empty date (it answered 400 "Invalid isoformat string").
        self.booking.grading = None
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        self.approved(self.version())
        self.pick(self.card(self.alice)["id"], AFTERNOON[0])
        scheduled = self.row(self.alice)["scheduled"]
        self.assertIsNotNone(self.row(self.alice)["booking"].get("copy"))
        event = self.exams.get(self.event["id"])
        sitting = next(s for s in event["sittings"] if s["id"] == scheduled["sitting_id"])
        self.exams.save_sitting(event["id"], {"version": sitting["version"], "date": ""}, sitting["id"])
        overview = self.client.get("/api/workspace/overview")
        self.assertEqual(overview.status_code, 200, overview.json)
        self.assertNotIn("print", (self.home() or {"parts": {}})["parts"])

    def test_a_hand_set_sitting_you_printed_for_gets_no_second_copy(self):
        self.booking.grading = None
        self.turn_on()
        self.excuse(self.alice, "a1")
        self.drain()
        version = self.version()
        self.approved(version)
        sitting = self.seat_by_hand(self.alice, "2036-10-07")
        section = next(p["section_id"] for p in self.exams.get(self.event["id"])["participants"] if p["enrollment_id"] == self.alice)
        batch = self.documents.create_batch(version["id"], None, 1, "by-hand", term_id=self.term, section_ids=[section], actor="workspace")
        current = self.exams.get(self.event["id"])
        self.exams.attach_batch(self.event["id"], {"version": current["version"], "batch_id": batch["id"], "sitting_id": sitting})
        self.drain()
        self.assertEqual(self.count("print_batches"), 1)
        self.assertEqual(self.row(self.alice)["booking"]["state"], "booked")


class DraftTests(BookingBase):
    class FakeGrading:
        """The makeup-draft calls the worker makes (GRADING "MAKEUP VERSIONS" is checked in test_makeup_drafts.py)."""
        def __init__(self, path, definition, fail=None):
            self.path, self.definition, self.fail, self.calls = path, definition, fail, []

        def create_makeup_draft(self, plan, data):
            self.calls.append((plan, data))
            if self.fail:
                raise ValueError(self.fail)
            with connect(self.path) as db:
                db.execute("INSERT INTO quiz_drafts VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), self.definition, data["request_id"], "x",
                                                                         json.dumps({"makeup": {"plan_id": plan}}), "2036-10-04T00:00:00Z"))

        @staticmethod
        def _makeup_rows(db, plan):
            return db.execute("SELECT * FROM quiz_drafts WHERE json_extract(input_json,'$.makeup.plan_id')=?", (plan,)).fetchall()

        @staticmethod
        def _quiz_draft_summary(db, row):
            return {"id": row["id"], "state": "ready", "makeup": {"letter": "C"}}

    def plan(self):
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO frq_assignments(id,event_id,version,created_at,updated_at) VALUES('plan-1',?,1,'x','x')", (self.event["id"],))

    def test_a_quiz_owing_a_makeup_gets_one_draft_and_its_approval_is_not_counted_as_a_makeup(self):
        self.plan()
        fake = self.FakeGrading(self.app.config["DATABASE"], self.definition)
        self.booking.grading = fake
        self.excuse(self.alice, "a1")
        self.drain()
        self.drain()
        self.assertEqual(fake.calls, [("plan-1", {"request_id": "auto-makeup:plan-1"})])
        self.assertEqual(self.rows()["booking"]["versions"]["plan-1"]["state"], "ready")
        self.turn_on()
        # Its approval is a decision of the assignment, counted once by its own card (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED").
        self.assertNotIn("approve", (self.home() or {"parts": {}})["parts"])

    def test_a_draft_that_cannot_start_is_shown_and_retried_later(self):
        self.plan()
        fake = self.FakeGrading(self.app.config["DATABASE"], self.definition, fail="No version of this assignment has rubrics with printed question numbers yet.")
        self.booking.grading = fake
        self.excuse(self.alice, "a1")
        self.drain()
        version = self.rows()["booking"]["versions"]["plan-1"]
        self.assertEqual((version["state"], version["problem"]), ("none", fake.fail))
        self.booking._idle_until = 0.0
        self.booking.process_one()
        self.assertEqual(len(fake.calls), 1)  # waits before trying again
