"""Google Calendar sync (AGENTS.md "Google Calendar sync"): Faculty's events go into the connected account's main calendar under
Faculty's own IDs, grouped as the Calendar page groups them, written again only when they change and deleted only when they are
Faculty's; the chosen Google calendars come back read-only on the Calendar in campus time. A fake Google answers everything."""
import json
import os
import socket
import tempfile
import unittest
import urllib.parse
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest import mock

from test_storage import HEADERS

from app import create_app
from google_calendar.service import BACK, CALENDAR_SCOPES, body, campus_today, clock, convert, group
from response_cards.gmail import SCOPES, TOKEN_URL, USERINFO_URL
from rosters import connect

ACCOUNT = "prof@tamu.edu"


def chicago(day, clock):
    """An RFC 3339 stamp for a campus wall-clock time with America/Chicago's offset on that day (-05:00 in daylight time, -06:00 after
    it ends), so a fixture's wall-clock time does not depend on the date the check runs."""
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ZoneInfo("America/Chicago")).isoformat()


class FakeCalendar:
    """The Calendar API in memory: calendarList, events list, and insert/update/delete on the main calendar."""

    def __init__(self):
        self.events = {}          # main-calendar events by ID (a deleted one keeps status "cancelled")
        self.purged = set()       # IDs Google no longer knows at all
        self.others = {"team@group.calendar.google.com": []}
        self.calls, self.fail, self.refuse = [], {}, set()
        self.lose_answer = False  # the next write is done, but its answer never arrives

    def own(self, item):
        return {"kind": "calendar#event", "htmlLink": f"https://www.google.com/calendar/event?eid={item['id']}", **item}

    def __call__(self, method, url, body=None, headers=None, timeout=30):
        parts = urllib.parse.urlsplit(url)
        assert parts.hostname == "www.googleapis.com" and headers["Authorization"].startswith("Bearer ")
        path, query = urllib.parse.unquote(parts.path.removeprefix("/calendar/v3")), dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((method, path))
        for key in (method, (method, path)):
            if key in self.fail:
                return self.fail.pop(key)
        if path == "/users/me/calendarList":
            return 200, {"items": [{"id": ACCOUNT, "summary": ACCOUNT, "primary": True, "accessRole": "owner"},
                                   {"id": "team@group.calendar.google.com", "summary": "Math team", "accessRole": "reader"}]}
        if method == "GET" and path.endswith("/events"):
            calendar = path.split("/")[2]
            items = list(self.events.values()) if calendar == "primary" else self.others[calendar]
            return 200, {"items": [self.own(item) for item in items if item.get("status") != "cancelled"]}
        assert query.get("sendUpdates") == "none"
        if method == "POST":
            if body["id"] in self.events or body["id"] in self.purged:
                return 409, {"error": {"code": 409, "message": "The requested identifier already exists.", "errors": [{"reason": "duplicate"}]}}
            if body["summary"] in self.refuse:
                return 400, {"error": {"code": 400, "message": "Invalid start time.", "errors": [{"reason": "invalid"}]}}
            self.events[body["id"]] = self.own(body)
        elif method == "PUT":
            event_id = path.rsplit("/", 1)[1]
            if event_id not in self.events:
                return 404, {"error": {"code": 404, "message": "Not Found"}}
            self.events[event_id] = self.own(body)
        elif method == "DELETE":
            event_id = path.rsplit("/", 1)[1]
            if event_id not in self.events or self.events[event_id].get("status") == "cancelled":
                return 410, {"error": {"code": 410, "message": "Resource has been deleted"}}
            self.events[event_id]["status"] = "cancelled"
            return 204, {}
        if self.lose_answer:
            self.lose_answer = False
            raise socket.timeout("timed out")
        return 200, self.own(body) if body else {}

    def live(self):
        return {key: item for key, item in self.events.items() if item.get("status") != "cancelled"}


class FakeTokens:
    def __init__(self, scope=" ".join(sorted(set(SCOPES) | set(CALENDAR_SCOPES))), email=ACCOUNT):
        self.scope, self.email = scope, email

    def __call__(self, url, data=None, headers=None, timeout=30):
        if url == TOKEN_URL:
            if data.get("grant_type") == "authorization_code":
                return 200, {"access_token": "signed-in", "refresh_token": "refresh-new", "expires_in": 3600, "scope": self.scope}
            return 200, {"access_token": "refreshed", "expires_in": 3600}
        if url == USERINFO_URL:
            return 200, {"email": self.email}
        raise AssertionError(url)


class Base(unittest.TestCase):
    scopes = sorted(set(SCOPES) | set(CALENDAR_SCOPES))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        client_file, self.token_file = os.path.join(self.temp.name, "client.json"), os.path.join(self.temp.name, "token.json")
        with open(client_file, "w") as handle:
            json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret", "project_id": "faculty-mail-1"}}, handle)
        self.write_token(self.scopes)
        with mock.patch.dict(os.environ, {"FACULTY_GMAIL_CLIENT_FILE": client_file, "FACULTY_GMAIL_TOKEN_FILE": self.token_file}):
            self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.sync = self.app.extensions["google_calendar"]
        self.google = FakeCalendar()
        self.sync.client.http = self.google
        self.sync.google.http = FakeTokens()
        self.today = campus_today()
        course = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                       "sections": [507, 508], "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(course.status_code, 201, course.json)
        self.definition = course.json["definition_id"]

    def write_token(self, scopes, email=ACCOUNT):
        with open(self.token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": email, "scopes": scopes, "connected_at": "2026-09-16T00:00:00+00:00"}, handle)

    def day(self, offset):
        return (self.today + timedelta(days=offset)).isoformat()

    def custom(self, **updates):
        data = {"request_id": str(uuid.uuid4()), "scope": "workspace", "category": "event", "title": "Office planning",
                "date": self.day(3), "all_day": True, **updates}
        response = self.client.post("/api/calendar/events", json=data, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def settings(self, **changes):
        status = self.client.get("/api/google-calendar").json
        response = self.client.post("/api/google-calendar/settings", headers=HEADERS,
                                    json={"base_revision": status["settings"]["revision"], "request_id": str(uuid.uuid4()), **changes})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def turn_on(self):
        return self.settings(enabled=True)

    def ledger(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(row) for row in db.execute("SELECT * FROM google_calendar_pushed ORDER BY date,summary")]

    def writes(self):
        return [call for call in self.google.calls if call[0] != "GET"]

    def feed(self, start=None, end=None):
        response = self.client.get("/api/calendar", query_string={"start": start or self.day(-10), "end": end or self.day(40)})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json


class PaginationTests(unittest.TestCase):
    def test_event_pages_must_finish_without_repeating_or_truncating(self):
        """CALENDAR: an incomplete read cannot replace the retained Google events."""
        from google_calendar.client import CalendarError, GoogleCalendarClient
        client = GoogleCalendarClient(mock.Mock())
        client.call = mock.Mock(return_value=(200, {"items": [], "nextPageToken": "again"}))
        with self.assertRaises(CalendarError):
            client.events("primary", "start", "end")
        self.assertEqual(client.call.call_count, 2)
        client.call = mock.Mock(return_value=(200, {"items": [{"id": "a"}, {"id": "b"}]}))
        with self.assertRaises(CalendarError):
            client.events("primary", "start", "end", limit=1)
        client.call = mock.Mock(side_effect=[(200, {"items": [], "nextPageToken": "next"}), (200, {"items": [{"id": "a"}]})])
        self.assertEqual(client.events("primary", "start", "end"), [{"id": "a"}])


class ReadLimitTests(unittest.TestCase):
    """The no-redirect Google call reads at most MAX_BODY bytes unless a caller names a larger `limit`."""

    @staticmethod
    def call(**options):
        from google_calendar.client import http_request
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status, response.read.return_value = 200, b'{"id": "m"}'
        with mock.patch("google_calendar.client.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            result = http_request("GET", "https://www.googleapis.com/x", **options)
        return result, response.read.call_args.args

    def test_the_body_cap_defaults_to_eight_megabytes_and_a_caller_may_raise_it(self):
        from google_calendar.client import MAX_BODY
        self.assertEqual(self.call(), ((200, {"id": "m"}), (MAX_BODY,)))
        self.assertEqual(MAX_BODY, 8 * 1024 * 1024)
        self.assertEqual(self.call(limit=48 * 1024 * 1024), ((200, {"id": "m"}), (48 * 1024 * 1024,)))


class GroupingTests(unittest.TestCase):
    """Faculty's events as Google gets them: the Calendar page's groups, wall-clock times in campus time, and what they gather."""

    def event(self, source, **values):
        base = {"id": f"{source}:{uuid.uuid4()}", "source": source, "title": "Exam 1", "date": "2026-10-22", "end_date": "2026-10-22",
                "time": "7:05PM", "end_time": "", "all_day": False, "location": "", "definition_id": "def-1", "term_id": "term-1",
                "code": "MATH 140", "term_label": "Fall 2026", "sections": [], "href": "#seating/x", "exam_href": "#exams/e1"}
        return {**base, **values}

    def test_an_exams_rooms_are_one_event_and_its_dr_bookings_at_one_time_are_another(self):
        events = [self.event("seating", location="HECC 207", sections=[507, 508]), self.event("seating", location="HECC 105", sections=[514]),
                  *(self.event("dr", title="Test 2", time="19:05", end_time="20:58", location="College Station", sections=[number], booking_count=count,
                               time_basis="dr_center") for number, count in ((507, 2), (508, 3), (514, 1))),
                  self.event("dr", title="Test 2", time="18:30", end_time="20:42", location="College Station", sections=[514], booking_count=1)]
        groups = group(events)
        self.assertEqual(sorted(len(entry["events"]) for entry in groups.values()), [1, 2, 3])
        made = {entry["kind"] + str(len(entry["events"])): body(key, entry, "https://faculty.example") for key, entry in groups.items()}
        rooms = made["seating2"]
        self.assertEqual((rooms["summary"], rooms["location"], rooms["transparency"]), ("MATH 140 · Exam 1", "HECC 207, HECC 105", "opaque"))
        self.assertEqual((rooms["start"], rooms["end"]), ({"dateTime": "2026-10-22T19:05:00", "timeZone": "America/Chicago"},
                                                          {"dateTime": "2026-10-22T20:05:00", "timeZone": "America/Chicago"}))
        self.assertIn("Rooms: HECC 207 · Sections 507, 508; HECC 105 · Section 514", rooms["description"])
        self.assertIn("No end time in Faculty (shown as one hour).", rooms["description"])
        self.assertIn("Open in Faculty: https://faculty.example/#exams/e1", rooms["description"])
        self.assertEqual(rooms["extendedProperties"]["private"]["faculty"], "1")
        dr = made["dr3"]
        self.assertEqual((dr["summary"], dr["transparency"], dr["end"]["dateTime"]), ("MATH 140 · Test 2 · DR exams (6)", "transparent", "2026-10-22T20:58:00"))
        self.assertIn("DR exams: Section 507 (2 bookings); Section 508 (3 bookings); Section 514 (1 booking)", dr["description"])
        for text in (json.dumps(made),):  # a group names sections and counts, never a student
            self.assertNotIn("uin", text.lower())

    def test_times_faculty_cannot_read_become_all_day_with_the_text_kept(self):
        made = body("k", {"kind": "exam", "events": [self.event("exam", time="after class", end_time="")]})
        self.assertEqual((made["start"], made["end"], made["transparency"]), ({"date": "2026-10-22"}, {"date": "2026-10-23"}, "transparent"))
        self.assertIn("Time: after class", made["description"])
        self.assertEqual([clock(text) for text in ("7:05PM", "7 pm", "19:05", "12:00 AM", "24:00", "noon")], [1145, 1140, 1145, 0, None, None])

    def test_google_events_are_read_in_campus_time(self):
        timed = convert({"id": "a", "summary": "Dentist", "start": {"dateTime": "2026-10-01T15:30:00Z"}, "end": {"dateTime": "2026-10-01T16:15:00Z"},
                         "description": "<p>Bring <b>forms</b> &amp; card</p>", "htmlLink": "https://www.google.com/calendar/event?eid=a"})
        self.assertEqual({key: timed[key] for key in ("date", "time", "end_time", "all_day", "notes")},
                         {"date": "2026-10-01", "time": "10:30", "end_time": "11:15", "all_day": 0, "notes": "Bring forms & card"})
        spanning = convert({"id": "b", "summary": "Conference", "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-08"}})
        self.assertEqual((spanning["date"], spanning["end_date"], spanning["all_day"]), ("2026-10-05", "2026-10-07", 1))
        overnight = convert({"id": "c", "start": {"dateTime": "2026-10-09T20:00:00-05:00"}, "end": {"dateTime": "2026-10-10T00:00:00-05:00"}})
        self.assertEqual((overnight["title"], overnight["end_date"], overnight["end_time"]), ("(No title)", "2026-10-09", ""))
        self.assertIsNone(convert({"id": "d", "status": "cancelled", "start": {"date": "2026-10-01"}}))
        self.assertIsNone(convert({"id": "e", "eventType": "workingLocation", "start": {"date": "2026-10-01"}}))
        self.assertIsNone(convert({"id": "f", "attendees": [{"self": True, "responseStatus": "declined"}], "start": {"date": "2026-10-01"}}))
        self.assertIsNone(convert({"id": "g", "extendedProperties": {"private": {"faculty": "1"}}, "start": {"date": "2026-10-01"}}))
        self.assertEqual(convert({"id": "h", "start": {"date": "2026-10-01"}, "htmlLink": "javascript:alert(1)"})["link"], "")


class InertTests(Base):
    def test_nothing_is_sent_or_shown_until_the_instructor_turns_it_on(self):
        self.custom()
        self.assertIsNone(self.sync.round())
        self.assertEqual(self.google.calls, [])
        self.write_token(sorted(SCOPES))  # connected for Response Card mail only
        self.turn_on()
        status = self.client.get("/api/google-calendar").json
        self.assertEqual((status["connected"], status["permission"]), (True, False))
        self.assertIsNone(self.sync.round())
        self.assertEqual(self.google.calls, [])
        self.assertEqual(self.feed()["google"]["permission"], False)


class ConnectTests(Base):
    def test_connect_asks_for_calendar_permission_for_the_connected_account_and_returns_to_the_calendar(self):
        self.write_token(sorted(SCOPES))
        response = self.client.post("/api/google-calendar/connect", json={"request_id": "c1"},
                                    headers={**HEADERS, "Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200, response.json)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(response.json["url"]).query))
        self.assertEqual((query["login_hint"], query["include_granted_scopes"], query["prompt"]), (ACCOUNT, "true", "consent"))
        self.assertTrue(set(CALENDAR_SCOPES) | set(SCOPES) <= set(query["scope"].split()))
        settings = self.client.get("/api/google-calendar").json["settings"]
        self.assertEqual((settings["enabled"], settings["link_base"]), (True, "http://localhost"))
        finished = self.client.get(f"/api/response-cards/gmail/callback?state={query['state']}&code=good")
        self.assertEqual((finished.status_code, finished.headers["Location"]), (302, "/#calendar?google=1"))
        self.assertTrue(self.sync.ready())
        self.assertTrue(self.sync.google.connected)  # Response Card mail keeps its connection and account

    def test_the_calendar_never_switches_the_connected_account(self):
        self.write_token(sorted(SCOPES))
        self.sync.google.http = FakeTokens(email="someone.else@gmail.com")
        url = self.client.post("/api/google-calendar/connect", json={"request_id": "c1"}, headers=HEADERS).json["url"]
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
        refused = self.client.get(f"/api/response-cards/gmail/callback?state={state}&code=good")
        self.assertEqual(refused.status_code, 400)
        self.assertIn(b"Google Calendar was not connected", refused.data)
        self.assertIn(b"prof@tamu.edu", refused.data)
        self.assertEqual((self.sync.google.email, self.sync.ready()), (ACCOUNT, False))

    def test_a_grant_without_calendar_permission_is_refused(self):
        self.write_token(sorted(SCOPES))
        self.sync.google.http = FakeTokens(scope=" ".join(SCOPES))
        url = self.client.post("/api/google-calendar/connect", json={"request_id": "c1"}, headers=HEADERS).json["url"]
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]
        refused = self.client.get(f"/api/response-cards/gmail/callback?state={state}&code=good")
        self.assertEqual(refused.status_code, 400)
        self.assertIn(b"Back to the Calendar", refused.data)
        self.assertFalse(self.sync.ready())

    def test_reconnecting_gmail_keeps_the_calendar_permission(self):
        url = self.client.post("/api/response-cards/gmail/connect", json={}, headers=HEADERS).json["url"]
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual((query["include_granted_scopes"], set(query["scope"].split())), ("true", set(SCOPES)))
        finished = self.client.get(f"/api/response-cards/gmail/callback?state={query['state']}&code=good")
        self.assertEqual(finished.headers["Location"], "/#response-cards")
        self.assertTrue(self.sync.ready())  # Google reports every granted scope back


class PushTests(Base):
    def test_faculty_events_go_in_once_change_in_place_and_leave_when_deleted(self):
        planning = self.custom()
        timed = self.custom(title="Review session", date=self.day(5), all_day=False, time="14:00", end_time="15:30", location="BLOC 160",
                            scope="course", definition_id=self.definition, notes="Bring questions")
        self.turn_on()
        self.assertEqual(self.sync.round()["sent"], 2)
        live = self.google.live()
        self.assertEqual(sorted(item["summary"] for item in live.values()), ["MATH 140 · Review session", "Office planning"])
        review = next(item for item in live.values() if item["summary"].endswith("Review session"))
        self.assertEqual((review["start"]["dateTime"], review["end"]["dateTime"], review["location"]), (f"{self.day(5)}T14:00:00", f"{self.day(5)}T15:30:00", "BLOC 160"))
        self.assertIn("Bring questions", review["description"])
        before = len(self.writes())
        self.assertEqual(self.sync.round()["sent"], 0)  # nothing changed, nothing written
        self.assertEqual(len(self.writes()), before)
        edited = self.client.put(f"/api/calendar/events/{timed['custom_id']}", headers=HEADERS, json={"version": timed["version"], "time": "15:00"})
        self.assertEqual(edited.status_code, 200, edited.json)
        self.sync.round()
        self.assertEqual(self.writes()[-1][0], "PUT")
        self.assertEqual(self.google.events[review["id"]]["start"]["dateTime"], f"{self.day(5)}T15:00:00")
        deleted = self.client.delete(f"/api/calendar/events/{planning['custom_id']}", headers=HEADERS, json={"version": planning["version"]})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.sync.round()["removed"], 1)
        self.assertEqual([item["summary"] for item in self.google.live().values()], ["MATH 140 · Review session"])

    def test_only_faculty_events_are_ever_changed(self):
        self.google.events["mine1"] = {"id": "mine1", "summary": "Dentist", "start": {"date": self.day(4)}, "end": {"date": self.day(5)}}
        self.custom()
        self.turn_on()
        self.sync.round()
        self.settings(send=[])
        self.sync.round()
        self.assertEqual(list(self.google.live()), ["mine1"])
        self.assertTrue(all(path.endswith("/events") or "mine1" not in path for _, path in self.google.calls))

    def test_a_lost_answer_never_makes_the_event_twice(self):
        self.custom()
        self.turn_on()
        self.google.lose_answer = True
        self.sync.round()
        self.assertEqual(len(self.google.live()), 1)
        status = self.client.get("/api/google-calendar").json
        self.assertIn("did not answer", status["sync"]["error"])
        self.assertEqual(self.ledger(), [])
        self.sync.round()  # paused after the lost answer
        self.assertEqual(len([call for call in self.writes() if call[0] == "POST"]), 1)
        self.sync.round(force=True)  # Sync now: the insert finds its own ID and updates it
        self.assertEqual([call[0] for call in self.writes()], ["POST", "POST", "PUT"])
        self.assertEqual(len(self.google.live()), 1)
        self.assertIsNotNone(self.ledger()[0]["pushed_at"])

    def test_an_event_deleted_in_google_stays_deleted_until_it_changes_in_faculty(self):
        event = self.custom()
        self.turn_on()
        self.sync.round()
        google_id = self.ledger()[0]["google_id"]
        self.google.events[google_id]["status"] = "cancelled"  # the instructor deleted it in Google
        self.sync.round()
        self.assertEqual(self.google.live(), {})
        self.client.put(f"/api/calendar/events/{event['custom_id']}", headers=HEADERS, json={"version": event["version"], "title": "Office planning (moved)"})
        self.sync.round()
        self.assertEqual([item["summary"] for item in self.google.live().values()], ["Office planning (moved)"])
        self.google.purged.add(google_id)
        del self.google.events[google_id]  # gone from Google for good: the next generation's ID
        event = self.client.get("/api/calendar", query_string={"start": self.day(0), "end": self.day(10)}).json["events"][0]
        self.client.put(f"/api/calendar/events/{event['custom_id']}", headers=HEADERS, json={"version": event["version"], "title": "Office planning (again)"})
        self.sync.round()
        row = self.ledger()[0]
        self.assertEqual((row["generation"], row["error"]), (1, ""))
        self.assertEqual([item["summary"] for item in self.google.live().values()], ["Office planning (again)"])

    def test_one_refused_event_is_recorded_and_the_others_go_on(self):
        self.custom(title="Broken")
        self.custom(title="Fine", date=self.day(6))
        self.google.refuse.add("Broken")
        self.turn_on()
        self.assertEqual(self.sync.round(), {"sent": 1, "removed": 0, "refused": 1, "more": False, "shown": 0, "missing_calendars": []})
        status = self.client.get("/api/google-calendar").json
        self.assertEqual([(item["summary"], "Invalid start time" in item["error"]) for item in status["refused"]], [("Broken", True)])
        self.assertIsNone(status["sync"]["error"])
        posts = len([call for call in self.writes() if call[0] == "POST"])
        self.sync.round()
        self.assertEqual(len([call for call in self.writes() if call[0] == "POST"]), posts)  # not tried again as it stands

    def test_a_disabled_calendar_api_pauses_with_the_way_to_turn_it_on(self):
        self.custom()
        self.turn_on()
        self.google.fail["POST"] = (403, {"error": {"code": 403, "message": "Google Calendar API has not been used in project 12 before or it is disabled.",
                                                    "errors": [{"reason": "accessNotConfigured"}]}})
        self.sync.round()
        status = self.client.get("/api/google-calendar").json
        self.assertIn("console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=faculty-mail-1", status["sync"]["error"])
        self.assertIsNotNone(status["sync"]["paused_until"])
        calls = len(self.google.calls)
        self.sync.round()
        self.assertEqual(len(self.google.calls), calls)  # paused
        synced = self.client.post("/api/google-calendar/sync", json={}, headers=HEADERS).json  # Sync now tries at once
        self.assertIsNone(synced["sync"]["error"])
        self.assertEqual(synced["counts"]["in_google"], 1)

    def test_unticking_a_type_and_remove_take_faculty_events_out_of_google(self):
        self.custom()
        self.custom(title="Grades due", category="deadline", date=self.day(7))
        self.custom(title="Last spring", date=self.day(-BACK - 30))  # before the window: never sent
        self.turn_on()
        self.sync.round()
        self.assertEqual(sorted(item["summary"] for item in self.google.live().values()), ["Grades due", "Office planning"])
        deadline = next(item for item in self.google.live().values() if item["summary"] == "Grades due")
        self.assertEqual(deadline["transparency"], "transparent")
        self.settings(send=["exam", "seating", "dr", "custom"])
        self.sync.round()
        self.assertEqual([item["summary"] for item in self.google.live().values()], ["Office planning"])
        refused = self.client.post("/api/google-calendar/remove", json={"request_id": "rm1"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400)
        removed = self.client.post("/api/google-calendar/remove", json={"request_id": "rm1", "confirmation": "remove"}, headers=HEADERS).json
        self.assertEqual((removed["settings"]["send"], removed["settings"]["enabled"], removed["counts"]["in_google"]), ([], True, 0))
        self.assertEqual(self.google.live(), {})

    def test_sync_now_says_when_a_round_is_already_running(self):
        self.turn_on()
        with self.sync.lock():
            busy = self.client.post("/api/google-calendar/sync", json={}, headers=HEADERS)
        self.assertEqual((busy.status_code, busy.json["busy"]), (200, True))


class PullTests(Base):
    def test_faculty_says_when_it_cannot_vouch_for_the_google_calendar(self):
        # SCHEDULING "GOOGLE CHECKED" (2026-10-05): off → nothing to vouch for; on → read within 15 minutes, else since when;
        # without the calendar permission → not connected.
        from scheduling.clock import CAMPUS_ZONE
        from scheduling.feed import google_gap
        self.assertIsNone(self.sync.unchecked())
        self.turn_on()
        self.assertEqual(self.sync.unchecked(), "Faculty has not read your Google Calendar yet")
        self.sync.round()
        self.assertIsNone(google_gap(self.sync))
        read = datetime.fromisoformat(self.client.get("/api/google-calendar").json["sync"]["read_at"])
        self.assertIsNone(self.sync.unchecked(read + timedelta(minutes=15)))
        local = read.astimezone(CAMPUS_ZONE)
        self.assertTrue(self.sync.unchecked(read + timedelta(minutes=16)).startswith(
            f"Faculty has not read your Google Calendar since {local:%a}, {local:%b} {local.day} at "))
        self.write_token(SCOPES)  # Gmail only: the calendar permission is gone
        self.assertEqual(self.sync.unchecked(), "Faculty is not connected to your Google Calendar")
        with mock.patch.object(self.sync, "unchecked", side_effect=RuntimeError("catalog busy")):
            self.assertEqual(google_gap(self.sync), "Faculty could not tell whether your Google Calendar was read")
        self.assertIsNone(google_gap(None))

    def test_the_chosen_google_calendars_show_read_only_on_the_calendar(self):
        self.custom(title="Office hours block", date=self.day(2))
        self.google.events["dentist"] = {"id": "dentist", "summary": "Dentist", "location": "Main St",
                                         "start": {"dateTime": chicago(self.day(1), "15:30:00")}, "end": {"dateTime": chicago(self.day(1), "16:30:00")}}
        self.google.events["declined"] = {"id": "declined", "summary": "Meeting I declined", "start": {"date": self.day(1)}, "end": {"date": self.day(2)},
                                          "attendees": [{"email": ACCOUNT, "self": True, "responseStatus": "declined"}]}
        self.google.others["team@group.calendar.google.com"].append({"id": "t1", "summary": "Team meeting", "start": {"date": self.day(3)}, "end": {"date": self.day(4)}})
        self.assertNotIn("google", {event["source"] for event in self.feed()["events"]})
        self.turn_on()
        self.sync.round()
        events = [event for event in self.feed()["events"] if event["source"] == "google"]
        self.assertEqual([(event["title"], event["time"], event["end_time"], event["editable"], event["calendar"]) for event in events],
                         [("Dentist", "15:30", "16:30", False, ACCOUNT)])  # never Faculty's own event, which it already shows
        self.assertEqual(events[0]["href"], "https://www.google.com/calendar/event?eid=dentist")
        self.assertEqual(self.feed()["google"]["account"], ACCOUNT)
        calendars = self.client.get("/api/google-calendar/calendars").json["calendars"]
        self.assertEqual([item["name"] for item in calendars], [ACCOUNT, "Math team"])
        self.settings(calendars=["primary", "team@group.calendar.google.com"])
        self.sync.round()
        self.assertEqual(sorted(event["title"] for event in self.feed()["events"] if event["source"] == "google"), ["Dentist", "Team meeting"])
        self.settings(calendars=["team@group.calendar.google.com"])
        self.assertEqual([event["title"] for event in self.feed()["events"] if event["source"] == "google"], ["Team meeting"])  # at once
        self.settings(enabled=False)
        self.assertEqual([event for event in self.feed()["events"] if event["source"] == "google"], [])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM google_calendar_events").fetchone()[0], 0)  # nothing kept once off


class SettingsTests(Base):
    def test_settings_are_revisions_checked_against_the_page(self):
        first = self.turn_on()
        self.assertEqual(first["settings"]["revision"], 1)
        stale = self.client.post("/api/google-calendar/settings", headers=HEADERS, json={"base_revision": 0, "request_id": "s2", "enabled": False})
        self.assertEqual(stale.status_code, 409)
        same = self.client.post("/api/google-calendar/settings", headers=HEADERS, json={"base_revision": 1, "request_id": "s3", "enabled": True})
        self.assertEqual(same.json["settings"]["revision"], 1)  # changes nothing, records nothing
        bad = self.client.post("/api/google-calendar/settings", headers=HEADERS, json={"base_revision": 1, "request_id": "s4", "send": ["everything"]})
        self.assertEqual(bad.status_code, 400)
        for malformed in ([{}], [[]]):
            bad = self.client.post("/api/google-calendar/settings", headers=HEADERS,
                                   json={"base_revision": 1, "request_id": "malformed-send", "send": malformed})
            self.assertEqual(bad.status_code, 400)
        with connect(self.app.config["DATABASE"]) as db:
            with self.assertRaises(Exception):
                db.execute("UPDATE google_calendar_settings SET enabled=0")


if __name__ == "__main__":
    unittest.main()
