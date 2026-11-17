"""The times a quiz makeup booking card offers (ABSENCES "MAKEUP BOOKING", absences/makeup_slots.py; requirement, 2026-10-04,
2026-10-05 and 2026-10-06): the next two days that have an open time, from now or the day after the absence ends, whichever is later
(never before the quiz, nor before the day the student can return, 2026-10-30), or after the last day an earlier card offered; office hours only in the rooms the term ticked as able to seat
a makeup (here BLOC 309 and BLOC 306, never the small office BLOC 319); a start every 30 minutes in the term's meeting hours (here
Tuesdays and Thursdays 1 to 5 pm in BLOC 319) where the whole quiz (20 minutes times extra time) fits and nothing but another makeup is
on the calendar; past, too-soon, absent and past-due times left out. Pure: synthetic calendars only."""
import datetime as dt
import unittest

from absences import makeup_slots as slots


def block(date, start, end, location="BLOC 309", title="MATH 140 · Office hours", **extra):
    return {"id": f"custom:{date}{start}", "source": "custom", "title": title, "date": date, "end_date": date, "time": start,
            "end_time": end, "all_day": False, "location": location, **extra}


def entry(ident, title, date, start, end="", **extra):
    return {"id": ident, "source": ident.split(":")[0], "title": title, "date": date, "end_date": date, "time": start, "end_time": end,
            "all_day": False, "location": "", **extra}


WEEK = [block("2026-10-05", "15:00", "17:00"), block("2026-10-06", "10:00", "11:00", "BLOC 319"),
        block("2026-10-08", "10:00", "11:00", "BLOC 319"), block("2026-10-09", "15:00", "17:00", "BLOC 306"),
        block("2026-10-12", "15:00", "17:00"), block("2026-10-13", "10:00", "11:00", "BLOC 319")]
SATURDAY = dt.datetime(2026, 10, 3, 12, 0)
HOURS = [{"weekday": 1, "start": "13:00", "end": "17:00", "location": "BLOC 319"}, {"weekday": 3, "start": "13:00", "end": "17:00", "location": "BLOC 319"}]
ROOMS = ["BLOC 309", "BLOC 306"]
MONDAY = "Mon, Oct 5 · 3:00–5:00 pm · BLOC 309 · office hours, come any time"
AFTERNOON = [f"Tue, Oct 6 · {t} · BLOC 319" for t in ("1:00 pm", "1:30 pm", "2:00 pm", "2:30 pm", "3:00 pm", "3:30 pm", "4:00 pm", "4:30 pm")]


def offer(given, first, last, events=WEEK, now=SATURDAY, **extra):
    return slots.offer(given, first, last, events, now, **{"windows": HOURS, "rooms": ROOMS, **extra})


class OfferTests(unittest.TestCase):
    def texts(self, offered):
        return [option["text"] for option in offered["options"]]

    def test_a_friday_quiz_seen_on_saturday_offers_monday_office_hours_and_tuesday_meeting_hours_never_the_small_office(self):
        offered = offer("2026-10-02", "2026-10-02", "2026-10-02")
        self.assertEqual((offered["week"], offered["due"]), ("2026-10-05", "2026-11-01"))  # the weekend has no time: Monday is first
        # ROOMS (requirement, 2026-10-05): its Tuesday office hours are gone.
        self.assertEqual(self.texts(offered), [MONDAY, *AFTERNOON])
        first, tuesday = offered["options"][0], offered["options"][1]
        self.assertEqual((first["kind"], first["date"], first["time"], first["end_time"], first["location"]),
                         ("office_hours", "2026-10-05", "15:00", "17:00", "BLOC 309"))
        self.assertEqual((tuesday["kind"], tuesday["time"], tuesday["end_time"], tuesday["location"], tuesday["minutes"]),
                         ("afternoon", "13:00", "13:30", "BLOC 319", 20))
        self.assertEqual(slots.choices(offered["options"])[-1], slots.OTHER)  # "None of these work" always comes last
        self.assertNotIn("Thu", " ".join(self.texts(offered)))  # two days only: Thursday and Friday are not offered

    def test_the_next_two_days_with_times_never_before_the_quiz(self):
        # NEXT TWO DAYS (requirement, 2026-10-06): a Monday quiz excused that morning starts the day after the absence; Wednesday
        # has no time, so the two days are Tuesday and Thursday.
        offered = offer("2026-10-05", "2026-10-05", "2026-10-05", now=dt.datetime(2026, 10, 5, 9, 0))
        self.assertEqual(offered["week"], "2026-10-06")
        self.assertEqual({o["date"] for o in offered["options"]}, {"2026-10-06", "2026-10-08"})
        self.assertEqual(self.texts(offered)[0], AFTERNOON[0])
        # Excused in advance (a Monday, before Thursday and Friday's absence): nothing before the day after the absence, though
        # Tuesday Sep 29's meeting hours are open.
        early = offer("2026-10-02", "2026-10-01", "2026-10-02", now=dt.datetime(2026, 9, 28, 9, 0))
        self.assertEqual(self.texts(early), [MONDAY, *AFTERNOON])

    def test_times_less_than_three_hours_away_are_left_out_and_today_still_counts(self):
        offered = offer("2026-10-02", "2026-10-02", "2026-10-02", now=dt.datetime(2026, 10, 6, 10, 15))
        texts = self.texts(offered)
        self.assertEqual(texts[:7], AFTERNOON[1:])  # 1:15 pm is the earliest: Monday and 1:00 are gone, Tuesday still counts
        self.assertEqual((texts[7], texts[-1]), ("Thu, Oct 8 · 1:00 pm · BLOC 319", "Thu, Oct 8 · 4:30 pm · BLOC 319"))
        # Nothing left today: Thursday's meeting hours and Friday's office hours (never Thursday's office hours in BLOC 319).
        late = offer("2026-10-02", "2026-10-02", "2026-10-02", now=dt.datetime(2026, 10, 6, 14, 0))
        self.assertEqual(late["week"], "2026-10-08")
        texts = self.texts(late)
        self.assertEqual((texts[0], texts[-1]), ("Thu, Oct 8 · 1:00 pm · BLOC 319", "Fri, Oct 9 · 3:00–5:00 pm · BLOC 306 · office hours, come any time"))
        self.assertNotIn("10:00–11:00 am", " ".join(texts))

    def test_a_new_card_offers_the_two_days_after_the_last_cards(self):
        offered = offer("2026-10-02", "2026-10-02", "2026-10-02", after="2026-10-06")
        self.assertEqual(offered["week"], "2026-10-08")
        self.assertEqual({o["date"] for o in offered["options"]}, {"2026-10-08", "2026-10-09"})
        again = offer("2026-10-02", "2026-10-02", "2026-10-02", after="2026-10-09")
        self.assertEqual(self.texts(again)[0], "Mon, Oct 12 · 3:00–5:00 pm · BLOC 309 · office hours, come any time")
        self.assertEqual({o["date"] for o in again["options"]}, {"2026-10-12", "2026-10-13"})

    def test_only_other_makeups_share_a_time_and_the_whole_quiz_must_be_clear(self):
        events = WEEK + [
            entry("google:x", "Meeting", "2026-10-06", "13:15", "13:45"),
            {**entry("custom:out", "Out", "2026-10-06", ""), "all_day": True},
            entry("exam:sitting-1", "Quiz 4 · Makeup", "2026-10-06", "14:00", "14:20", makeup=True, minutes=20),
            # A makeup scheduled by hand before an exact start was asked for: its start is read from the text.
            entry("exam:sitting-3", "Quiz 4 · Makeup", "2026-10-06", "After lecture, around 2 PM", makeup=True, minutes=30),
            entry("office_hours:a", "Office hours", "2026-10-06", "16:00", "16:30"),  # a student's appointment
            entry("custom:noend", "Call", "2026-10-06", "15:00"),
            entry("google:y", "Jordan Example quiz makeup", "2026-10-06", "14:15", "14:45"),
            entry("exam:sitting-2", "Quiz 5 · In class", "2026-10-06", "16:30", "17:00"),
        ]
        offered = offer("2026-10-02", "2026-10-02", "2026-10-02", events, exclude={"exam:sitting-1"})
        times = [t.split(" · ")[1] for t in self.texts(offered)[1:]]
        # 1:00 and 1:30 meet the meeting; 3:00 and 3:30 the call (an hour long without an end); 4:00 the student's appointment
        # (2026-10-05: a makeup never shares a meeting's time); 4:30 the ordinary sitting. Makeups take nothing.
        self.assertEqual(times, ["2:00 pm", "2:30 pm"])
        self.assertEqual([o["date"] for o in offered["options"] if o["kind"] == "office_hours"], ["2026-10-05"])
        self.assertEqual(self.texts(offer("2026-10-02", "2026-10-02", "2026-10-02", events)), self.texts(offered))
        self.assertTrue(slots.shareable({"source": "custom", "title": "Make-up exam for Lee"}))
        self.assertTrue(slots.shareable({"source": "exam", "title": "Quiz 4 · Lee", "makeup": True}))
        self.assertFalse(slots.shareable({"source": "google", "title": "Extra office hours"}))
        self.assertFalse(slots.shareable({"source": "office_hours", "title": "Office hours"}))
        # LENGTH: a 35-minute quiz (1.75x) must end by 5:00, so 4:30 goes; 4:00 stays.
        long = offer("2026-10-02", "2026-10-02", "2026-10-02", minutes=35)
        self.assertEqual(self.texts(long), [MONDAY, *AFTERNOON[:-1]])
        self.assertEqual({o["minutes"] for o in long["options"]}, {35})
        busy = offer("2026-10-02", "2026-10-02", "2026-10-02", WEEK + [entry("google:z", "Meeting", "2026-10-06", "13:50", "14:00")], minutes=30)
        self.assertNotIn("Tue, Oct 6 · 1:30 pm · BLOC 319", self.texts(busy))  # 1:30–2:00 meets 1:50

    def test_time_away_comes_out_of_office_hours_and_nothing_else_does(self):
        # AWAY (requirement, 2026-11-08).
        friday = dt.datetime(2026, 10, 8, 9, 0)
        plain = offer("2026-10-07", "2026-10-07", "2026-10-07", now=friday)
        self.assertIn("Fri, Oct 9 · 3:00–5:00 pm · BLOC 306 · office hours, come any time", self.texts(plain))
        away = offer("2026-10-07", "2026-10-07", "2026-10-07", WEEK + [entry("custom:away", "Unavailable", "2026-10-09", "15:00", "23:59")], now=friday)
        self.assertFalse([o for o in away["options"] if o["date"] == "2026-10-09"])
        self.assertEqual(away["week"], "2026-10-08")  # Thursday's meeting hours, then the next day with a time: Monday
        self.assertIn("2026-10-12", {o["date"] for o in away["options"]})
        # Leaving at 4:30: what is left of the block is offered, when the whole quiz fits in it.
        late = WEEK + [entry("google:proctor", "Away: proctoring", "2026-10-09", "16:30", "18:30")]
        self.assertIn("Fri, Oct 9 · 3:00–4:30 pm · BLOC 306 · office hours, come any time",
                      self.texts(offer("2026-10-07", "2026-10-07", "2026-10-07", late, now=friday)))
        short = WEEK + [entry("google:early", "Out of office", "2026-10-09", "15:15", "18:00")]
        self.assertFalse([o for o in offer("2026-10-07", "2026-10-07", "2026-10-07", short, now=friday)["options"] if o["date"] == "2026-10-09"])
        # Anything else over office hours still leaves them whole: "essentially a free for all" (2026-10-06).
        busy = WEEK + [entry("google:visit", "Department meeting", "2026-10-09", "15:00", "16:00"),
                       entry("office_hours:b", "Office hours", "2026-10-09", "15:00", "15:30")]
        self.assertIn("Fri, Oct 9 · 3:00–5:00 pm · BLOC 306 · office hours, come any time",
                      self.texts(offer("2026-10-07", "2026-10-07", "2026-10-07", busy, now=friday)))

    def test_an_absence_running_into_the_week_moves_its_times(self):
        offered = offer("2026-10-02", "2026-10-02", "2026-10-05")
        self.assertEqual(offered["options"][0]["date"], "2026-10-06")  # Monday falls inside the absence
        whole = offer("2026-10-02", "2026-10-02", "2026-10-07")
        self.assertEqual((whole["week"], whole["options"][0]["date"]), ("2026-10-08", "2026-10-08"))  # then Thursday that week

    def test_nothing_is_offered_after_the_due_date(self):
        offered = offer("2026-10-02", "2026-09-01", "2026-09-04")  # due Oct 4: the weekend has no time
        self.assertEqual((offered["week"], offered["options"], offered["due"]), (None, [], "2026-10-04"))
        tight = offer("2026-10-02", "2026-09-05", "2026-09-05")  # due Oct 5: only Monday, one day
        self.assertEqual(self.texts(tight), [MONDAY])

    def test_times_start_at_the_return_date_never_before_now(self):
        # FROM THEIR RETURN (requirement, 2026-10-30).
        # A Friday absence whose details clear them for Thursday: no Monday or Tuesday, Thursday and Friday instead.
        later = offer("2026-10-02", "2026-10-02", "2026-10-02", returns="2026-10-08")
        self.assertEqual(later["week"], "2026-10-08")
        self.assertEqual({o["date"] for o in later["options"]}, {"2026-10-08", "2026-10-09"})
        self.assertEqual(later["due"], "2026-11-07")  # 30 days from the return, not from the absence's last day (Nov 1)
        # "if the followup is after their return date it shouldn't backdate it either": a return already past starts from now.
        past = offer("2026-10-02", "2026-10-02", "2026-10-02", now=dt.datetime(2026, 10, 9, 9, 0), returns="2026-10-05")
        self.assertEqual(past["week"], "2026-10-09")
        self.assertTrue(all(o["date"] >= "2026-10-09" for o in past["options"]))
        self.assertEqual(past["due"], "2026-11-04")
        # No return date (or one on the absence's own days): unchanged.
        self.assertEqual(offer("2026-10-02", "2026-10-02", "2026-10-02", returns=None), offer("2026-10-02", "2026-10-02", "2026-10-02"))
        # A due date a later card moved stands as given.
        self.assertEqual(offer("2026-10-02", "2026-10-02", "2026-10-02", returns="2026-10-08", due="2026-11-20")["due"], "2026-11-20")

    def test_rooms_and_meeting_hours_are_the_terms_own(self):
        # Without meeting hours: office hours in the ticked rooms only (Monday's, then Friday's); without rooms either (a new term):
        # nothing at all.
        self.assertEqual(self.texts(offer("2026-10-02", "2026-10-02", "2026-10-02", windows=())),
                         [MONDAY, "Fri, Oct 9 · 3:00–5:00 pm · BLOC 306 · office hours, come any time"])
        self.assertEqual(offer("2026-10-02", "2026-10-02", "2026-10-02", windows=(), rooms=())["options"], [])
        self.assertEqual(self.texts(offer("2026-10-02", "2026-10-02", "2026-10-02", WEEK[1:], rooms=["BLOC 319"]))[0],
                         "Tue, Oct 6 · 10:00–11:00 am · BLOC 319 · office hours, come any time")  # a term may tick any room
        monday = [{"weekday": 0, "start": "09:00", "end": "10:00", "location": "BLOC 113"}, *HOURS]
        self.assertEqual(self.texts(offer("2026-10-02", "2026-10-02", "2026-10-02", windows=monday))[:2],
                         ["Mon, Oct 5 · 9:00 am · BLOC 113", "Mon, Oct 5 · 9:30 am · BLOC 113"])
        roomless = offer("2026-10-02", "2026-10-02", "2026-10-02", [], windows=[{**HOURS[0], "end": "14:00", "location": ""}])
        self.assertEqual(self.texts(roomless), ["Tue, Oct 6 · 1:00 pm", "Tue, Oct 6 · 1:30 pm", "Tue, Oct 13 · 1:00 pm", "Tue, Oct 13 · 1:30 pm"])
        self.assertTrue(slots.room_allows(["bloc 309"], " BLOC  309"))
        self.assertFalse(slots.room_allows(ROOMS, ""))

    def test_helpers(self):
        self.assertEqual(slots.due_date("2026-10-02"), "2026-11-01")
        self.assertEqual((slots.due_date("2026-10-02", "2026-10-10"), slots.due_date("2026-10-02", "")), ("2026-11-09", "2026-11-01"))
        self.assertEqual((slots.span(600, 660), slots.span(630, 720), slots.span(900, 1020)), ("10:00–11:00 am", "10:30 am–12:00 pm", "3:00–5:00 pm"))
        self.assertEqual(slots.long_day("2026-10-05"), "Monday, October 5")
        self.assertIsNone(slots.clock("25:00"))
        self.assertEqual([slots.loose_clock(t) for t in ("13:00", "1PM", "After lecture, around 2 PM", "10:20 am", "12:30 p.m.", "noon", None)],
                         [780, 780, 840, 620, 750, None, None])
        self.assertEqual(slots.event_span(entry("exam:s", "Quiz 4 · Makeup", "2026-10-06", "1PM", makeup=True, minutes=30)), (780, 810))
        # SCHEDULING "TYPED TIMES" (stage 1g): any entry's typed clock is read; loose text only a makeup's.
        self.assertEqual(slots.event_span(entry("google:a", "Lunch", "2026-10-06", "1PM")), (780, 840))
        self.assertIsNone(slots.event_span(entry("google:a", "Lunch", "2026-10-06", "After lecture, around 2 PM")))
        self.assertEqual(slots.event_span(entry("google:b", "Call", "2026-10-06", "15:00")), (900, 960))


if __name__ == "__main__":
    unittest.main()
