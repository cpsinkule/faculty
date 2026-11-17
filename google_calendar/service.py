"""Google Calendar sync (requirement, 2026-09-28).

Faculty → Google. Every Faculty event the Calendar page shows (exam sittings, seating charts, DR bookings, gradebook due dates,
the instructor's own events) goes into the connected account's main calendar as the Calendar page GROUPS it: one exam's rooms at
one time are one Google event, one exam's DR bookings at one time are one Google event ("MATH 140 · Test 1 · DR exams (9)"),
each custom event its own. Only the event types the instructor ticks go. Each Google event carries a Faculty ID of its own
(`google_id`, derived from the account and the group, so a request whose answer was lost is never made twice) and a private
`faculty` property, and `google_calendar_pushed` records it: Faculty changes and deletes ONLY those events, never one it did not
make. An event is written again only when what Faculty would write changes (its content hash); an edit made to it in Google
stays until then, and one deleted in Google comes back only when it changes in Faculty. A group gone from Faculty (inside the
window) or of a type no longer ticked is deleted from Google; one outside the window is left as it is. "Remove Faculty's events"
unticks every type, which deletes them all. Nobody is invited and Google sends no mail (`sendUpdates=none`).

Google → Faculty. The chosen calendars (the main one unless the instructor ticks others) are read every round over the window
and kept in `google_calendar_events`, replaced calendar by calendar, without Faculty's own events, cancelled ones, ones the
instructor declined, or working-location markers. `/api/calendar` shows them read-only (source `google`, times converted to
campus time, a link to open each in Google). Turning sync off clears that copy.

Window: campus today minus BACK to plus AHEAD days, both ways (the Calendar page shows no Google events outside it).

The worker process runs a round every ROUND seconds beside the Inbox loop (never under its execution lock); the page's Sync now
runs one in the web process. Both take LOCK, so two rounds never overlap. A busy or unreachable Google, or a connection that
needs the instructor, pauses the rounds (`pause_until`) and is shown with its reason; one event Google refuses is recorded on
its ledger row and not tried again until it changes. Catalog writes are short `BEGIN IMMEDIATE` transactions (workspace-owned
rows, like rooms: no course lock); no lock is held across a request to Google.
"""
import base64
import hashlib
import html
import json
import logging
import re
import uuid
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from werkzeug.exceptions import Conflict

from canonical_json import ascii_digest
from catalog_migrations import apply_sql, migration_transaction
from course_storage import exclusive, timestamp
from request_ids import valid_request_id
from response_cards.gmail import CALENDAR_SCOPES
import rounds
from rosters import connect
from scheduling.clock import CAMPUS_ZONE, ZONE_NAME, display_clock, parse_clock

from .client import PAUSE, CalendarError, GoogleCalendarClient

LOG = logging.getLogger(__name__)
ZONE = CAMPUS_ZONE
LOCK = "09a8703c-cba8-4547-b0bc-2bb36979e163"  # one sync round at a time, across the web and worker processes
ROUND = 120          # seconds between automatic rounds
BACK, AHEAD = 60, 300  # the sync window around campus today, in days (inside CalendarService.list's 366-day limit)
STALE = timedelta(minutes=15)  # SCHEDULING "GOOGLE CHECKED": a copy read longer ago than this (rounds run every ROUND) vouches for nothing
MAX_WRITES = 250     # Google writes per round; the rest follow in the next round
DEFAULT_MINUTES = 60  # a timed Faculty event without an end time is shown this long (and says so)
# The Calendar page's event types, in its own words; each may be sent or not.
TYPES = {"exam": "Exams", "seating": "Seating charts", "dr": "DR exams", "deadline": "Deadlines", "custom": "Your events"}
# Shown as free time: the instructor is not the one sitting a DR exam, a due date blocks nothing, and neither does an all-day event.
FREE = {"dr", "deadline"}
GOOGLE_LINK = re.compile(r"https://(www\.google\.com/calendar|calendar\.google\.com)/[^\s\"<>]{1,1000}")


def clock(text):
    """Minutes after midnight for a recognized clock ("7:05 PM", "7pm", "19:05"), else None (scheduling.clock "any", the rule of
    calendar-feed.js clockMinutes)."""
    return parse_clock(text, "any")


def kind(event):
    """The Calendar page's type of one feed event (calendar-feed.js eventType)."""
    # A Term schedule's rows go with the instructor's own events ("Your events"), as the per-date events they replace did.
    return ("deadline" if event.get("category") == "deadline" else "custom" if event.get("source") in ("office_hours", "schedule")
            else event.get("source") or "custom")


def campus_today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZONE).date()


def google_id(account, key, generation):
    """A Google event ID of Faculty's own (base32hex, as Google requires), the same for every attempt at one event."""
    digest = hashlib.sha256(f"faculty-calendar\n{account.casefold()}\n{key}\n{generation}".encode()).digest()
    return base64.b32hexencode(digest).decode().lower().rstrip("=")[:40]


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _sections(numbers):
    numbers = sorted({str(number) for number in numbers or []}, key=lambda value: (len(value), value))
    return f"Section{'s' if len(numbers) != 1 else ''} {', '.join(numbers)}" if numbers else ""


def group(events):
    """Faculty's events as Google gets them: the Calendar page's groups (same type, course, term, date, title and times),
    each custom event alone. Returns {ledger key: {"kind", "events"}}."""
    groups = {}
    for event in events:
        type_ = kind(event)
        if event.get("source") in ("custom", "office_hours", "schedule"):
            # One Google event each; a Term schedule's row keeps its id (entry and date), so a time or room edit updates it in place.
            identity = ["custom", event.get("custom_id") or event["id"]]
        else:
            identity = [type_, event.get("definition_id") or "", event.get("term_id") or "", event["date"],
                        str(event.get("title") or "").strip().casefold(), event.get("time") or "", event.get("end_time") or ""]
        key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        groups.setdefault(key, {"kind": type_, "events": []})["events"].append(event)
    return groups


def _timing(first, notes):
    """(start, end) for Google from one Faculty event's wall-clock date and time text, adding a note where Faculty says less."""
    day, last = first["date"], first.get("end_date") or first["date"]
    after = (Date.fromisoformat(last) + timedelta(days=1)).isoformat()
    time_text, end_text = str(first.get("time") or ""), str(first.get("end_time") or "")
    start = None if first.get("all_day") or not time_text else clock(time_text)
    if start is None:
        if time_text and not first.get("all_day"):
            notes.append(f"Time: {time_text}" + (f" to {end_text}" if end_text else ""))
        return {"date": day}, {"date": after}
    begin = datetime.fromisoformat(day) + timedelta(minutes=start)
    end = clock(end_text)
    finish = datetime.fromisoformat(last) + timedelta(minutes=end) if end is not None else None
    if finish is None or finish <= begin:
        finish = begin + timedelta(minutes=DEFAULT_MINUTES)
        notes.append(f"Ends: {end_text} (shown as one hour)." if end_text else "No end time in Faculty (shown as one hour).")
    stamp = lambda moment: {"dateTime": moment.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": ZONE_NAME}  # noqa: E731
    return stamp(begin), stamp(finish)


def _link(base, href):
    if not base or not isinstance(href, str) or not href.startswith("#"):
        return ""
    return f"{base}/{href}"


def body(key, entry, link_base=""):
    """The Google event for one group: its summary, place, times, a description naming what it gathers, and Faculty's marks."""
    events, type_ = entry["events"], entry["kind"]
    first = events[0]
    code, title = first.get("code") or "", str(first.get("title") or "").strip() or "(untitled)"
    lines = [" · ".join(part for part in (code, first.get("term_label") or "") if part)]
    if type_ == "dr":
        bookings = sum(int(event.get("booking_count") or 0) for event in events)
        summary = f"{title} · DR exams" + (f" ({bookings})" if bookings else "")
        lines.append("DR exams: " + "; ".join(
            f"{_sections(event.get('sections'))} ({_plural(int(event.get('booking_count') or 0), 'booking')})"
            for event in sorted(events, key=lambda event: [str(number).zfill(6) for number in event.get("sections") or []])))
        lines.append("Times are the DR center's local time.")
    else:
        summary = title
        rooms = [(event.get("location") or "", _sections(event.get("sections"))) for event in events]
        if type_ in ("exam", "seating") and (len(rooms) > 1 or rooms[0][1]):
            lines.append("Rooms: " + "; ".join(" · ".join(part for part in room if part) for room in rooms))
        elif rooms[0][1]:
            lines.append(rooms[0][1])
    if code:
        summary = f"{code} · {summary}"
    notes = []
    start, end = _timing(first, notes)
    lines += notes
    if first.get("source") == "custom" and first.get("notes"):
        lines.append(str(first["notes"]))
    hrefs = {event.get("exam_href") or event.get("href") for event in events} - {"#calendar", None, ""}
    day = f"#calendar?month={first['date'][:7]}&view=month&date={first['date']}"  # calendar-feed.js calendarHref
    url = _link(link_base, hrefs.pop() if len(hrefs) == 1 else day)
    if url:
        lines.append(f"Open in Faculty: {url}")
    lines.append("Added by Faculty, which replaces this event when its schedule changes. Make changes in Faculty.")
    places = list(dict.fromkeys(str(event.get("location") or "").strip() for event in events if event.get("location")))
    result = {"summary": summary[:300], "description": "\n".join(line for line in lines if line)[:8000], "start": start, "end": end,
              "location": ", ".join(places)[:500], "status": "confirmed", "transparency": "transparent" if type_ in FREE or "date" in start else "opaque",
              "reminders": {"useDefault": True}, "extendedProperties": {"private": {"faculty": "1", "facultyKey": key}}}
    if url:
        result["source"] = {"title": "Faculty", "url": url}
    return result


def content_hash(event):
    return ascii_digest(event)


def _text(value, limit):
    value = str(value or "")
    value = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", value, flags=re.I)
    value = html.unescape(re.sub(r"<[^>]{0,2000}>", "", value))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()[:limit]


def convert(item):
    """One Google event as a row of Faculty's copy, in campus wall-clock time; None for one Faculty does not show."""
    if item.get("status") == "cancelled" or item.get("eventType") == "workingLocation":
        return None
    if (item.get("extendedProperties") or {}).get("private", {}).get("faculty") == "1":
        return None  # Faculty's own event: the Calendar already shows it from Faculty
    if any(isinstance(person, dict) and person.get("self") and person.get("responseStatus") == "declined" for person in item.get("attendees") or []):
        return None
    start, end = item.get("start") or {}, item.get("end") or {}
    try:
        if isinstance(start.get("date"), str):
            day = Date.fromisoformat(start["date"])
            last = Date.fromisoformat(end["date"]) - timedelta(days=1) if isinstance(end.get("date"), str) else day
            timing = {"date": day.isoformat(), "end_date": max(day, last).isoformat(), "time": "", "end_time": "", "all_day": 1}
        elif isinstance(start.get("dateTime"), str):
            begin = datetime.fromisoformat(start["dateTime"]).astimezone(ZONE)
            finish = datetime.fromisoformat(end["dateTime"]).astimezone(ZONE) if isinstance(end.get("dateTime"), str) else begin
            finish = max(begin, finish)
            last, end_time = finish.date(), finish.strftime("%H:%M")
            if finish.hour == 0 and finish.minute == 0 and last > begin.date():
                last, end_time = last - timedelta(days=1), ""  # ends at midnight: the day before is its last
            timing = {"date": begin.date().isoformat(), "end_date": max(begin.date(), last).isoformat(),
                      "time": begin.strftime("%H:%M"), "end_time": end_time if end_time != begin.strftime("%H:%M") or last != begin.date() else "",
                      "all_day": 0}
        else:
            return None
    except (ValueError, KeyError, TypeError):
        return None
    event_id = item.get("id")
    if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
        return None
    link = item.get("htmlLink") if isinstance(item.get("htmlLink"), str) and GOOGLE_LINK.fullmatch(item["htmlLink"]) else ""
    return {"event_id": event_id, "title": _text(item.get("summary"), 300) or "(No title)", **timing,
            "location": _text(item.get("location"), 500), "notes": _text(item.get("description"), 4000), "link": link}


def _settings(db):
    row = db.execute("SELECT * FROM google_calendar_settings ORDER BY revision DESC LIMIT 1").fetchone()
    if not row:
        return {"revision": 0, "enabled": False, "send": list(TYPES), "calendars": ["primary"], "link_base": "", "saved_at": None}
    return {"revision": row["revision"], "enabled": bool(row["enabled"]), "send": json.loads(row["send_json"]),
            "calendars": json.loads(row["calendars_json"]), "link_base": row["link_base"], "saved_at": row["created_at"]}


def _state(db, name="sync"):
    row = db.execute("SELECT value_json FROM google_calendar_state WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else {}


def _put_state(db, value, name="sync"):
    db.execute("""INSERT INTO google_calendar_state(name,value_json,updated_at) VALUES(?,?,?)
        ON CONFLICT(name) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
               (name, json.dumps(value, sort_keys=True), timestamp()))


def _drop_unchosen(db, chosen):
    """Removes Faculty's copy of every Google calendar not in `chosen` (all of them when it is empty)."""
    if chosen:
        db.execute(f"DELETE FROM google_calendar_events WHERE calendar_id NOT IN ({','.join('?' * len(chosen))})", chosen)
    else:
        db.execute("DELETE FROM google_calendar_events")


def _origin(value):
    """The address the instructor opened Faculty at (for links in Google events): scheme and host only, else ''."""
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/") or parts.query or parts.fragment:
        return ""
    return f"{parts.scheme}://{parts.netloc}"[:200]


class GoogleCalendarService:
    def __init__(self, rosters, calendar, google, storage=None):
        self.rosters, self.path, self.calendar, self.google, self.storage = rosters, rosters.path, calendar, google, storage
        self.client = GoogleCalendarClient(google)
        self.names = None  # CALENDAR OFFICE HOURS: StudentSupportService.name_appointments, set where the app is built
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "google_calendar", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ----------------------------------------------------------------------------------------------------------
    def ready(self):
        """Faculty's Google connection exists and carries the calendar permission."""
        return self.google.configured and self.google.granted(CALENDAR_SCOPES)

    def window(self, today=None):
        today = today or campus_today()
        return today - timedelta(days=BACK), today + timedelta(days=AHEAD)

    def summary(self):
        """What the Calendar page needs on every read: whether Google events are shown, from which account, and any problem."""
        with connect(self.path) as db:
            settings, state = _settings(db), _state(db)
        start, end = self.window()
        return {"configured": self.google.configured, "connected": self.google.connected, "permission": self.ready(),
                "account": self.google.email or None, "enabled": settings["enabled"], "error": state.get("error") or None,
                "read_at": state.get("read_at"), "shown_from": start.isoformat(), "shown_to": end.isoformat()}

    def status(self):
        """The Google Calendar dialog: the connection, the instructor's choices, and where the sync stands."""
        account = self.google.email or ""
        with connect(self.path) as db:
            settings, state = _settings(db), _state(db)
            sent = db.execute("SELECT COUNT(*) FROM google_calendar_pushed WHERE account=? AND removed_at IS NULL AND pushed_at IS NOT NULL",
                              (account,)).fetchone()[0]
            shown = db.execute("SELECT COUNT(*) FROM google_calendar_events WHERE account=?", (account,)).fetchone()[0]
            refused = [dict(row) for row in db.execute("""SELECT summary,date,error FROM google_calendar_pushed
                WHERE account=? AND error!='' ORDER BY date LIMIT 20""", (account,))]
        start, end = self.window()
        pause = state.get("pause_until")
        return {"configured": self.google.configured, "connected": self.google.connected, "permission": self.ready(),
                "account": account or None, "project": self.google.project, "settings": settings,
                "types": [{"key": key, "label": label} for key, label in TYPES.items()],
                "sync": {"round_at": state.get("round_at"), "sent_at": state.get("sent_at"), "read_at": state.get("read_at"),
                         "error": state.get("error") or None, "error_at": state.get("error_at"),
                         "paused_until": pause if pause and pause > timestamp() else None,
                         "last": state.get("last") or {}},
                "counts": {"in_google": sent, "shown": shown}, "refused": refused,
                "window": {"from": start.isoformat(), "to": end.isoformat()}, "every_seconds": ROUND}

    def unchecked(self, now=None):
        """SCHEDULING "GOOGLE CHECKED" (requirement, 2026-10-05): why Faculty cannot vouch for the instructor's Google calendar now (not connected, or not read in STALE), or None.
        None too while the sync is off or was never set up: Faculty then reads no Google events, by the instructor's choice."""
        if not self.google.configured:
            return None
        with connect(self.path) as db:
            settings, state = _settings(db), _state(db)
        if not settings["enabled"]:
            return None
        if not self.ready():
            return "Faculty is not connected to your Google Calendar"
        try:
            read = datetime.fromisoformat(state["read_at"]) if state.get("read_at") else None
        except ValueError:
            read = None
        if read is None:
            return "Faculty has not read your Google Calendar yet"
        if (now or datetime.now(timezone.utc)) - read <= STALE:
            return None
        local = read.astimezone(ZONE)
        return f"Faculty has not read your Google Calendar since {local:%a}, {local:%b} {local.day} at {display_clock(local.strftime('%H:%M'))}"

    def feed(self, start, end):
        """The instructor's Google events in [start, end] as Calendar feed rows (read only; `workspace_calendar`)."""
        if not self.ready():
            return []
        account = self.google.email
        with connect(self.path) as db:
            settings = _settings(db)
            if not settings["enabled"]:
                return []
            rows = db.execute("""SELECT * FROM google_calendar_events WHERE account=? AND date<=? AND end_date>=?
                ORDER BY date,time,title""", (account, end, start)).fetchall()
        return [{"id": f"google:{row['calendar_id']}:{row['event_id']}", "source": "google", "title": row["title"],
                 "date": row["date"], "end_date": row["end_date"], "time": row["time"], "end_time": row["end_time"],
                 "all_day": bool(row["all_day"]), "location": row["location"], "notes": row["notes"], "definition_id": None,
                 "term_id": None, "section_id": None, "code": "", "term_label": "", "sections": [], "href": row["link"],
                 "editable": False, "version": None, "time_basis": "campus", "category": "event",
                 "calendar": row["calendar_name"]} for row in rows]

    def calendars(self):
        """The account's calendars, read from Google now (the dialog's list to choose from)."""
        if not self.ready():
            raise ValueError("Connect Google Calendar first.")
        return {"calendars": self.client.calendars()}

    # -- The instructor's choices ------------------------------------------------------------------------------------------
    def _record(self, db, settings, request_id, actor):
        db.execute("""INSERT INTO google_calendar_settings(id,revision,enabled,send_json,calendars_json,link_base,request_id,actor,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), settings["revision"] + 1, int(settings["enabled"]), json.dumps(settings["send"]),
                                           json.dumps(settings["calendars"]), settings["link_base"], request_id, actor, timestamp()))
        # Sync off: nothing of the instructor's calendars stays in Faculty; a calendar no longer chosen leaves at once.
        _drop_unchosen(db, settings["calendars"] if settings["enabled"] else [])

    @staticmethod
    def _request(data):
        return valid_request_id(data.get("request_id"))

    def _change(self, request_id, change, actor, base=None):
        """Records `change(settings)` as the next revision (nothing when it changes nothing). A repeated request ID returns what
        it did; `base` names the revision the page showed (409 when there is a newer one)."""
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = _settings(db)
            earlier = db.execute("SELECT revision FROM google_calendar_settings WHERE request_id=?", (request_id,)).fetchone()
            if earlier:
                return current
            if base is not None and base != current["revision"]:
                raise Conflict("The Google Calendar settings changed since this page read them. Look at them again.")
            wanted = change(dict(current))
            if {key: wanted[key] for key in ("enabled", "send", "calendars", "link_base")} != \
                    {key: current[key] for key in ("enabled", "send", "calendars", "link_base")}:
                self._record(db, wanted, request_id, actor)
            return _settings(db)

    def connect(self, data, origin="", actor="workspace"):
        """Starts Google's sign-in asking for calendar permission for the connected account, and turns the sync on (the Connect
        click is the instructor's choice); the sign-in returns to the Calendar."""
        request_id = self._request(data if isinstance(data, dict) else {})
        if not self.google.configured:
            raise ValueError("Google is not set up on this server: Faculty's Google client file (FACULTY_GMAIL_CLIENT_FILE and "
                             "FACULTY_GMAIL_TOKEN_FILE) is missing.")
        url = self.google.start(self.google.email or "", purpose="calendar")
        link = _origin(origin)
        self._change(request_id, lambda settings: {**settings, "enabled": True, "link_base": link or settings["link_base"]}, actor)
        return {"url": url}

    def save(self, data, actor="workspace"):
        """The dialog's choices: sync on or off, the Faculty event types sent, the Google calendars shown."""
        data = data if isinstance(data, dict) else {}
        request_id = self._request(data)
        base = data.get("base_revision")
        if type(base) is not int or base < 0:
            raise ValueError("Name the settings the page showed as base_revision.")
        changes = {}
        if "enabled" in data:
            if type(data["enabled"]) is not bool:
                raise ValueError("Sync on or off must be true or false.")
            changes["enabled"] = data["enabled"]
        if "send" in data:
            send = data["send"]
            if not isinstance(send, list) or any(not isinstance(item, str) or item not in TYPES for item in send):
                raise ValueError(f"Choose the event types to send from: {', '.join(TYPES)}.")
            changes["send"] = [item for item in TYPES if item in send]
        if "calendars" in data:
            chosen = data["calendars"]
            if not isinstance(chosen, list) or len(chosen) > 50 or any(not isinstance(item, str) or not 1 <= len(item) <= 300
                                                                        or re.search(r"[\x00-\x1f\x7f]", item) for item in chosen):
                raise ValueError("Choose up to 50 Google calendars to show.")
            changes["calendars"] = list(dict.fromkeys(chosen))
        self._change(request_id, lambda settings: {**settings, **changes}, actor, base)
        return self.status()

    def remove(self, data, actor="workspace"):
        """Remove Faculty's events from Google: unticks every type, so the next round deletes each event Faculty put there (and
        only those). Runs that round now unless one is already running; Google's events stay shown while the sync is on."""
        data = data if isinstance(data, dict) else {}
        request_id = self._request(data)
        if data.get("confirmation") != "remove":
            raise ValueError('Confirm with "remove" to delete Faculty\'s events from Google Calendar.')
        self._change(request_id, lambda settings: {**settings, "send": []}, actor)
        return self.sync_now()

    # -- Rounds -------------------------------------------------------------------------------------------------------------
    def lock(self):
        return exclusive(self.storage, LOCK, Path(self.path).parent / ".google-calendar.lock", "A Google Calendar sync is running now.")

    def sync_now(self):
        """Sync now (the dialog): one round at once, whatever the pause. Busy is not an error: the running round finishes."""
        try:
            with self.lock():
                self.round(force=True)
        except Conflict:
            return {**self.status(), "busy": True}
        return self.status()

    def worker(self, stop):
        """Beside the Inbox worker loop: a round every ROUND seconds while the sync is on (or Faculty's events are being removed)."""
        rounds.repeat(stop, self._locked_round, ROUND, LOG, "Google Calendar sync is waiting for storage",
                      "Google Calendar sync stopped unexpectedly")

    def _locked_round(self):
        with self.lock():
            self.round()

    def round(self, force=False, now=None):
        """One round: send what changed in Faculty, then read the chosen Google calendars. Holds LOCK (the caller's)."""
        if not self.ready():
            return None
        account = self.google.email
        today = campus_today(now)
        with connect(self.path) as db:
            settings, state = _settings(db), _state(db)
            removals = db.execute(f"""SELECT COUNT(*) FROM google_calendar_pushed WHERE account=? AND removed_at IS NULL
                AND kind NOT IN ({','.join('?' * len(settings['send'])) or "''"})""", (account, *settings["send"])).fetchone()[0]
        if not settings["enabled"] and not removals:
            return None
        if not force and state.get("pause_until") and state["pause_until"] > timestamp():
            return None
        last = {}
        try:
            last.update(self._push(account, settings, today))
            state["sent_at"] = timestamp()
            if settings["enabled"]:
                last.update(self._pull(account, settings, today))
                state["read_at"] = timestamp()
            state.update(error=None, error_at=None, pause_until=None)
        except CalendarError as error:
            wait = error.retry_after or PAUSE
            state.update(error=str(error), error_at=timestamp(),
                         pause_until=(datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat())
        state.update(round_at=timestamp(), last=last, account=account)
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            _put_state(db, state)
        return last

    def _wanted(self, settings, start, end):
        """Faculty's groups of the ticked types over the window, as Google events."""
        from scheduling.feed import assemble
        with connect(self.path) as db:
            db.execute("BEGIN")
            events = assemble(db, start.isoformat(), end.isoformat(), calendar=self.calendar)
        events = [event for event in events if kind(event) in settings["send"]]
        if self.names is not None:  # each appointment names its student; read after the transaction (LOCK is only ever tried, never waited on)
            self.names(events)
        events.sort(key=lambda event: (event["date"], event.get("time") or "", event["id"]))
        wanted = {}
        for key, entry in group(events).items():
            event = body(key, entry, settings["link_base"])
            wanted[key] = {"kind": entry["kind"], "date": entry["events"][0]["date"], "event": event, "sha": content_hash(event)}
        return wanted

    def _ledger(self, account):
        with connect(self.path) as db:
            return {row["key"]: dict(row) for row in db.execute("SELECT * FROM google_calendar_pushed WHERE account=?", (account,))}

    def _save_row(self, row):
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO google_calendar_pushed(account,key,kind,google_id,generation,date,summary,content_sha256,error,pushed_at,removed_at)
                VALUES(:account,:key,:kind,:google_id,:generation,:date,:summary,:content_sha256,:error,:pushed_at,:removed_at)
                ON CONFLICT(account,key) DO UPDATE SET kind=excluded.kind, google_id=excluded.google_id, generation=excluded.generation,
                date=excluded.date, summary=excluded.summary, content_sha256=excluded.content_sha256, error=excluded.error,
                pushed_at=excluded.pushed_at, removed_at=excluded.removed_at""", row)

    def _push(self, account, settings, today):
        start, end = self.window(today)
        wanted = self._wanted(settings, start, end) if settings["enabled"] else {}
        ledger = self._ledger(account)
        writes, sent, removed, refused = 0, 0, 0, 0
        for key, item in wanted.items():
            row = ledger.get(key)
            if row and row["content_sha256"] == item["sha"] and (row["error"] or (row["removed_at"] is None and row["pushed_at"])):
                continue  # in Google as Faculty would write it, or refused as it stands: nothing to do until it changes
            if writes >= MAX_WRITES:
                break
            writes += 1
            if self._write(account, key, item, row):
                sent += 1
            else:
                refused += 1
        for key, row in ledger.items():
            if row["removed_at"] is not None or key in wanted:
                continue
            if row["kind"] in settings["send"] and (not settings["enabled"] or not start.isoformat() <= row["date"] <= end.isoformat()):
                continue  # outside the window (or the sync is off): left as it is
            if writes >= MAX_WRITES:
                break
            writes += 1
            try:
                self.client.delete(row["google_id"])
            except CalendarError as error:
                if not error.event_only:
                    raise
                self._save_row({**row, "error": str(error)[:500]})
                refused += 1
                continue
            self._save_row({**row, "error": "", "removed_at": timestamp()})
            removed += 1
        return {"sent": sent, "removed": removed, "refused": refused, "more": writes >= MAX_WRITES}

    def _write(self, account, key, item, row):
        """Puts one group in Google under Faculty's own ID: an update where Google has had it, else an insert (a duplicate ID
        means an earlier insert landed unseen, so it is updated). An ID Google no longer accepts moves to the next generation."""
        generation = row["generation"] if row else 0
        update = bool(row and row["pushed_at"])
        record = {"account": account, "key": key, "kind": item["kind"], "date": item["date"], "summary": item["event"]["summary"],
                  "content_sha256": item["sha"], "pushed_at": row["pushed_at"] if row else None, "removed_at": row["removed_at"] if row else None}
        for _ in range(4):
            identifier = google_id(account, key, generation)
            try:
                if update:
                    self.client.update(identifier, {**item["event"], "id": identifier})
                else:
                    self.client.insert({**item["event"], "id": identifier})
            except CalendarError as error:
                if error.duplicate:
                    update = True
                    continue
                if error.gone:
                    generation, update = generation + 1, False
                    continue
                if not error.event_only:
                    raise
                self._save_row({**record, "google_id": identifier, "generation": generation, "error": str(error)[:500]})
                return False
            self._save_row({**record, "google_id": identifier, "generation": generation, "error": "", "pushed_at": timestamp(), "removed_at": None})
            return True
        self._save_row({**record, "google_id": google_id(account, key, generation), "generation": generation,
                        "error": "Google would not take this event under any of Faculty's IDs."})
        return False

    def _pull(self, account, settings, today):
        start, end = self.window(today)
        time_min = datetime.combine(start, datetime.min.time(), ZONE).isoformat()
        time_max = datetime.combine(end + timedelta(days=1), datetime.min.time(), ZONE).isoformat()
        listed = {item["id"]: item for item in self.client.calendars()}
        primary = next((item for item in listed.values() if item["primary"]), None)
        with connect(self.path) as db:
            mine = {row[0] for row in db.execute("SELECT google_id FROM google_calendar_pushed WHERE account=?", (account,))}
        shown, missing = 0, []
        for calendar_id in settings["calendars"]:
            info = primary if calendar_id == "primary" else listed.get(calendar_id)
            if not info:
                missing.append(calendar_id)
                continue
            try:
                items = self.client.events(calendar_id, time_min, time_max)
            except CalendarError as error:
                if not error.gone:
                    raise
                missing.append(calendar_id)
                continue
            rows = [row for row in (convert(item) for item in items) if row and row["event_id"] not in mine]
            name = account if calendar_id == "primary" else info["name"]
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM google_calendar_events WHERE account=? AND calendar_id=?", (account, calendar_id))
                read = timestamp()
                db.executemany("""INSERT OR REPLACE INTO google_calendar_events(account,calendar_id,event_id,calendar_name,title,date,end_date,
                    time,end_time,all_day,location,notes,link,read_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               [(account, calendar_id, row["event_id"], name, row["title"], row["date"], row["end_date"], row["time"],
                                 row["end_time"], row["all_day"], row["location"], row["notes"], row["link"], read) for row in rows])
            shown += len(rows)
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM google_calendar_events WHERE account!=?", (account,))  # another account's copy
            _drop_unchosen(db, [item for item in settings["calendars"] if item not in missing])
        return {"shown": shown, "missing_calendars": missing}
