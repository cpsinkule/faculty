"""Messages sent later (requirement, 2026-11-07). Asked, they chose the Mail page's new messages and the
Canvas page's messages to sections; recipients exactly as approved; up to two hours late, else held for them; the Assistant through its
Approve card.

WHAT IS KEPT. The review is the ordinary one (Mail's preview, the Canvas message's preview), and Send later… records instead of
sending: the send's own fields and the review's fingerprint, and for Canvas the reviewed plan itself (exactly who gets it in which
course), so nothing is read again to decide who gets it. Faculty files are kept by route and read at the send, where a file that
changed since refuses it (held). A file from the instructor's computer is never kept, so such a message cannot be sent later, nor
can a reply (a newer message in its thread would make it stale).

WHEN IT GOES. A thread of the web process looks every ROUND seconds (app.py `__main__`; never started by the checks, which call
`round` with their own clock). A message whose time has come goes through the ordinary send under the request ID `later-<id>`, so
its receipts are the Mail page's or the Canvas message's own, recorded before anything leaves, and another try returns what the
first did, never a second message (an uncertain one is never sent again). A send to Gmail registers with Canvas's writes
(canvas/stopping.py), so a restart waits for it, as for a Canvas message.

LATE. While Gmail is not connected, Faculty is signed out of Canvas or is restarting, the message waits and is tried every round,
up to LATE after its time; then it is held: not sent, on Home's Needs you, with Send now and Cancel. A send that failed or could
not be confirmed is on Needs you too until the instructor says they have seen it (Dismiss).

Owner: the WORKSPACE, like every send it makes. Faculty's records only, except what the sends themselves read and write.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, HTTPException, NotFound

import rounds
from canvas import web as canvas_web
from catalog_migrations import apply_sql, migration_transaction
from course_storage import exclusive, timestamp
from gmail_mail.client import MailError
from request_ids import valid_request_id
from rosters import connect
from scheduling.clock import CAMPUS_ZONE

LOG = logging.getLogger(__name__)
LOCK = "8d2f6c41-3a9e-4b7d-a5c2-1e6f0b9d7a34"  # one round at a time, across processes
ROUND = 30                                     # seconds between looks at the clock
LATE = timedelta(hours=2)                      # how late a message may still go by itself
SOONEST = timedelta(minutes=1)
FARTHEST = timedelta(days=180)
RECENT = timedelta(days=14)                    # how long a message that went (or was cancelled) stays listed
PREFIX = "later-"                              # the send's request ID: PREFIX + the scheduled message's ID
PROBLEM = ("failed", "uncertain", "mixed")
MAIL_FIELDS = ("to", "cc", "bcc", "subject", "body", "attachments")
CANVAS_FIELDS = ("sections", "subject", "body", "individual", "attachments")
COMPUTER = ("A file from your computer is not kept until the message goes, so it can't be sent later. Attach it from Faculty "
            "instead, or send the message now.")
RESTARTING = "Faculty is restarting."
CHANGED = ("The message would no longer be exactly what you approved (an attached file changed, or Gmail is connected as another "
           "account), so it was not sent.")


def campus_text(when):
    """"Tue, Nov 10, 8:00 AM", campus time."""
    local = when.astimezone(CAMPUS_ZONE)
    return f"{local:%a, %b} {local.day}, {local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def send_time(value, now):
    """The instant a message goes: a campus date and time as the page's fields give it ("2026-11-10T08:00"), or an ISO time with
    its offset. At least SOONEST ahead and at most FARTHEST."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Choose the date and time to send it.")
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("That is not a date and time.") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=CAMPUS_ZONE)
    when = when.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if when < now + SOONEST:
        raise ValueError("Choose a time at least a minute from now. To send it now, choose Send.")
    if when > now + FARTHEST:
        raise ValueError("Choose a time within 180 days.")
    return when


def _no_uploads(value):
    if isinstance(value, list) and any(isinstance(item, dict) and "upload" in item for item in value):
        raise ValueError(COMPUTER)


def _audience(message):
    """Who a planned Mail message goes to, in a few words: "alex@tamu.edu", "87 students on Bcc", "a@x, b@x and 2 more"."""
    named, hidden = message["to"] + message["cc"], len(message["bcc"])
    shown = ", ".join(named) if len(named) <= 2 else f"{named[0]} and {len(named) - 1} more"
    if hidden and not message["to"]:
        return f"{hidden} on Bcc" + (f", Cc {', '.join(message['cc'])}" if message["cc"] else "")
    return shown + (f", {hidden} on Bcc" if hidden else "")


class ScheduledSends:
    def __init__(self, rosters, mail, messages, canvas, storage=None):
        self.rosters, self.path, self.mail, self.messages, self.canvas, self.storage = rosters, rosters.path, mail, messages, canvas, storage
        self._lock = threading.Lock()
        self._running = set()  # scheduled IDs being sent now, in this process
        self._waits = {}       # scheduled ID -> why its last try waited (shown while it waits; said when it is held)
        self._stop = None
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "scheduled_sends", migration.stem, migration.read_text(encoding="utf-8"))

    # -- scheduling ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _request(data, now):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = valid_request_id(data.get("request_id"))
        if data.get("confirmation") != "schedule":
            raise ValueError("Confirm it: the message is scheduled only when you choose Schedule.")
        return request_id, send_time(data.get("send_at"), now)

    def schedule_mail(self, data, actor="workspace", now=None):
        """A new message from the Mail page, reviewed (its `fingerprint`), kept to be sent at `send_at`. Sends nothing."""
        request_id, when = self._request(data, now or datetime.now(timezone.utc))
        if data.get("reply_thread_id"):
            raise ValueError("A reply can't be sent later: the student may write again before then. Send it now.")
        fields = {key: data[key] for key in MAIL_FIELDS if key in data}
        _no_uploads(fields.get("attachments"))
        earlier = self._earlier(request_id, data.get("fingerprint"))
        if earlier:
            return earlier
        preview = self.mail.preview(fields)
        if preview["fingerprint"] != data.get("fingerprint"):
            raise Conflict("The message changed since you reviewed it. Review it again.")
        message = preview["message"]
        return self._insert(request_id, "mail", when, preview["fingerprint"], fields, {}, message["subject"], _audience(message),
                            preview["recipients"], actor)

    def schedule_canvas(self, data, actor="workspace", now=None):
        """A Canvas message to chosen sections, reviewed (its `fingerprint`), kept with exactly the recipients the review found, to be
        sent at `send_at`. Reads Canvas as the review does; sends nothing."""
        request_id, when = self._request(data, now or datetime.now(timezone.utc))
        fields = {key: data[key] for key in CANVAS_FIELDS if key in data}
        _no_uploads(fields.get("attachments"))
        earlier = self._earlier(request_id, data.get("fingerprint"))
        if earlier:
            return earlier
        kept = self.messages.approved({**fields, "fingerprint": data.get("fingerprint")})
        numbers = sorted({n for course in kept["courses"] for n in course["sections"]}, key=lambda n: (len(str(n)), str(n)))
        audience = f"Section{'' if len(numbers) == 1 else 's'} {', '.join(map(str, numbers))}"
        return self._insert(request_id, "canvas", when, kept["fingerprint"], fields, kept, kept["subject"], audience,
                            sum(len(course["recipients"]) for course in kept["courses"]), actor)

    def _earlier(self, request_id, fingerprint):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM scheduled_sends WHERE request_id=?", (request_id,)).fetchone()
            if not row:
                return None
            if row["fingerprint"] != fingerprint:
                raise Conflict("This was already scheduled with a different message. Close the window and write it again.")
            return self._item(db, row)

    def _insert(self, request_id, channel, when, fingerprint, fields, plan, subject, audience, recipients, actor):
        scheduled_id = str(uuid.uuid4())
        with self._lock, connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM scheduled_sends WHERE request_id=?", (request_id,)).fetchone():
                db.rollback()
                raise Conflict("This was already scheduled. Close the window and look in Scheduled.")
            db.execute("INSERT INTO scheduled_sends(id,request_id,channel,send_at,fingerprint,request_json,plan_json,subject,audience,recipients,"
                       "actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (scheduled_id, request_id, channel, when.isoformat(), fingerprint, json.dumps(fields), json.dumps(plan), subject,
                        audience, recipients, actor, timestamp()))
            db.commit()
            return self._item(db, db.execute("SELECT * FROM scheduled_sends WHERE id=?", (scheduled_id,)).fetchone())

    # -- the record ----------------------------------------------------------------------------------------------------------
    def _result(self, db, row):
        """The record of the send made for this message (the Mail page's or the Canvas message's own), or None while none was made."""
        request = PREFIX + row["id"]
        if row["channel"] == "mail":
            found = db.execute("SELECT * FROM gmail_mail_sends WHERE request_id=?", (request,)).fetchone()
            return self.mail._record(db, found) if found else None
        found = db.execute("SELECT * FROM canvas_messages WHERE request_id=?", (request,)).fetchone()
        return self.messages._record(db, found) if found else None

    @staticmethod
    def _events(db, scheduled_id):
        return [dict(e) for e in db.execute("SELECT * FROM scheduled_send_events WHERE scheduled_id=? ORDER BY id", (scheduled_id,))]

    def _item(self, db, row):
        events = self._events(db, row["id"])
        kinds = [event["kind"] for event in events]
        last = events[-1] if events else None
        result = self._result(db, row)
        state = ("cancelled" if kinds and kinds[-1] == "cancelled" else "sent" if result is not None
                 else "sending" if row["id"] in self._running else "held" if kinds and kinds[-1] == "held" else "waiting")
        fields = json.loads(row["request_json"])
        item = {"id": row["id"], "request_id": row["request_id"], "channel": row["channel"], "send_at": row["send_at"],
                "subject": row["subject"], "audience": row["audience"], "recipients": row["recipients"], "actor": row["actor"],
                "created_at": row["created_at"], "state": state, "body": fields.get("body", ""),
                "attachments": [{"name": a.get("name") or a.get("route", ""), "route": a.get("route", "")} for a in fields.get("attachments") or []
                                if isinstance(a, dict)]}
        if row["channel"] == "mail":
            item.update({key: fields.get(key) or [] for key in ("to", "cc", "bcc")})
        if state == "held":
            item["detail"] = last["detail"]
        elif state == "waiting" and row["id"] in self._waits:
            item["waiting"] = self._waits[row["id"]]
        if result is not None:
            status = result["status"]
            item["result"] = {"status": status, "sent_at": result["sent_at"], "detail": result.get("detail") or "",
                              "recipients": result["recipients"], "id": result["id"]}
            late = next((int(e["detail"].split()[0]) for e in events if e["kind"] == "sent" and e["detail"].endswith(" minutes late")), 0)
            if late:
                item["result"]["late_minutes"] = late
            item["problem"] = status in PROBLEM and "acknowledged" not in kinds
        return item

    def item(self, scheduled_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM scheduled_sends WHERE id=?", (str(scheduled_id),)).fetchone()
            if not row:
                raise NotFound("That scheduled message was not found.")
            return self._item(db, row)

    def listing(self, channel="", now=None):
        """Messages still to go (soonest first), held ones, and those that went or were cancelled in the last RECENT (newest first).
        Faculty's records only."""
        if channel not in ("", "mail", "canvas"):
            raise ValueError("Choose mail or canvas.")
        since = ((now or datetime.now(timezone.utc)) - RECENT).replace(microsecond=0).isoformat()
        with connect(self.path) as db:
            rows = db.execute("""SELECT s.* FROM scheduled_sends s
                LEFT JOIN scheduled_send_events e ON e.id=(SELECT MAX(x.id) FROM scheduled_send_events x WHERE x.scheduled_id=s.id)
                WHERE (s.send_at>=? OR e.kind IS NULL OR e.kind='held') AND (?='' OR s.channel=?)
                ORDER BY s.send_at, s.created_at""", (since, channel, channel)).fetchall()
            items = [self._item(db, row) for row in rows]
        coming = [i for i in items if i["state"] in ("waiting", "sending", "held")]
        done = sorted((i for i in items if i["state"] not in ("waiting", "sending", "held")), key=lambda i: i["send_at"], reverse=True)
        return {"scheduled": coming + done, "late_hours": int(LATE.total_seconds() // 3600)}

    def attention(self, db):
        """How many need the instructor (Home's Needs you): held ones, and sends of the last RECENT that failed or could not be
        confirmed and were not dismissed."""
        since = (datetime.now(timezone.utc) - RECENT).replace(microsecond=0).isoformat()
        rows = db.execute("""SELECT s.* FROM scheduled_sends s
            JOIN scheduled_send_events e ON e.id=(SELECT MAX(x.id) FROM scheduled_send_events x WHERE x.scheduled_id=s.id)
            WHERE e.kind='held' OR (e.kind='sent' AND s.send_at>=?)""", (since,)).fetchall()
        items = [self._item(db, row) for row in rows]
        return sum(1 for item in items if item["state"] == "held" or item.get("problem"))

    # -- the instructor's actions --------------------------------------------------------------------------------------------
    def _event(self, scheduled_id, kind, detail="", actor="workspace"):
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO scheduled_send_events(scheduled_id,kind,send_request_id,detail,actor,created_at) VALUES (?,?,?,?,?,?)",
                       (scheduled_id, kind, PREFIX + scheduled_id if kind == "sent" else "", detail[:600], actor, timestamp()))
            db.commit()

    def cancel(self, scheduled_id, actor="workspace"):
        """Never sent: allowed until the send is made. Cancelling a cancelled message changes nothing."""
        with self._lock:
            item = self.item(scheduled_id)
            if item["state"] == "cancelled":
                return item
            if item["state"] == "sending":
                raise Conflict("It is being sent now.")
            if item["state"] == "sent":
                raise Conflict("It has already been sent.")
            self._event(item["id"], "cancelled", actor=actor)
        return self.item(scheduled_id)

    def dismiss(self, scheduled_id, actor="workspace"):
        """The instructor has seen a send that failed or could not be confirmed: it leaves Needs you. Nothing is sent again."""
        with self._lock:
            item = self.item(scheduled_id)
            if not item.get("problem"):
                return item
            self._event(item["id"], "acknowledged", actor=actor)
        return self.item(scheduled_id)

    def send_now(self, scheduled_id, data, actor="workspace"):
        """The instructor's Send now, on one waiting or held: sent as it was approved, at once. A message already sent returns its
        record; a cancelled one is refused."""
        if not isinstance(data, dict) or data.get("confirmation") != "send":
            raise ValueError("Confirm the send: the message goes only when you choose Send now.")
        with self._lock:
            item = self.item(scheduled_id)
            if item["state"] in ("sent", "sending"):
                return item
            if item["state"] == "cancelled":
                raise Conflict("It was cancelled. Write the message again.")
            self._running.add(item["id"])
        try:
            reason = self._blocked(item["channel"])
            if reason:
                raise ValueError(reason)
            with connect(self.path) as db:
                row = db.execute("SELECT * FROM scheduled_sends WHERE id=?", (item["id"],)).fetchone()
            try:
                self._send(row)
            except (canvas_web.CanvasError, MailError) as error:
                raise ValueError(str(error)) from None
            self._event(item["id"], "sent", actor=actor)
        finally:
            with self._lock:
                self._running.discard(item["id"])
                self._waits.pop(item["id"], None)
        return self.item(scheduled_id)

    # -- sending ---------------------------------------------------------------------------------------------------------------
    def _blocked(self, channel):
        """Why a message on this channel cannot go now, or ''."""
        if channel == "mail":
            return "" if self.mail.google.connected else "Gmail is not connected (Connect Gmail on the Mail page)."
        if not self.canvas.settings.configured:
            return "Canvas is not set up for this Faculty."
        if self.canvas.writes.closing:
            return RESTARTING
        if not self.canvas.settings.status()["signed_in"]:
            return "Faculty is signed out of Canvas (sign in on the Canvas page)."
        try:
            with self.canvas.client() as client:
                client.me()
        except canvas_web.SignedOut:
            return "Faculty is signed out of Canvas (sign in on the Canvas page)."
        except canvas_web.CanvasError as error:
            return f"Canvas did not answer ({str(error)[:200]})."
        return ""

    def _send(self, row):
        """The ordinary send, under this message's own request ID (so a second try returns what the first did)."""
        request = PREFIX + row["id"]
        if row["channel"] == "canvas":
            return self.messages.send_approved(request, json.loads(row["plan_json"]), actor=row["actor"])
        writes, key = self.canvas.writes, f"later:{row['id']}"
        writes.begin(key, "a message sent later", RESTARTING)  # a restart waits for the send to Gmail (canvas/stopping.py)
        try:
            return self.mail.send({**json.loads(row["request_json"]), "request_id": request, "fingerprint": row["fingerprint"],
                                   "confirmation": "send"}, actor=row["actor"])
        finally:
            writes.end(key)

    def _due(self, now):
        with connect(self.path) as db:
            return db.execute("""SELECT s.* FROM scheduled_sends s WHERE s.send_at<=?
                AND NOT EXISTS (SELECT 1 FROM scheduled_send_events e WHERE e.scheduled_id=s.id)
                ORDER BY s.send_at, s.created_at""", (now.replace(microsecond=0).isoformat(),)).fetchall()

    def round(self, now=None):
        """Sends each message whose time has come; holds one still waiting LATE after its time."""
        now = now or datetime.now(timezone.utc)
        for row in self._due(now):
            self._attempt(row, now)

    def _attempt(self, row, now):
        scheduled_id = row["id"]
        with self._lock:
            item = self.item(scheduled_id)
            if item["state"] != "waiting":
                if item["state"] == "sent":
                    self._event(scheduled_id, "sent", actor="faculty")  # sent before its record was finished (a restart)
                return
            if now - datetime.fromisoformat(row["send_at"]) > LATE:
                why = self._waits.pop(scheduled_id, "")
                self._event(scheduled_id, "held", f"Faculty could not send it by {campus_text(datetime.fromisoformat(row['send_at']) + LATE)}"
                                                  + (f": {why}" if why else "."), actor="faculty")
                return
        reason = self._blocked(row["channel"])
        with self._lock:
            if reason:
                self._waits[scheduled_id] = reason
                return
            if self.item(scheduled_id)["state"] != "waiting":
                return  # cancelled meanwhile
            self._running.add(scheduled_id)
        held = ""
        try:
            self._send(row)
        except Conflict as error:
            held = CHANGED if row["channel"] == "mail" else error.description
        except HTTPException as error:
            reason = RESTARTING if error.code == 503 else error.description
        except canvas_web.SignedOut:
            reason = "Faculty is signed out of Canvas (sign in on the Canvas page)."
        except (ValueError, canvas_web.CanvasError, MailError) as error:
            reason = str(error)[:300]
        except Exception as error:  # noqa: BLE001 - it waits and is tried again; what happened is logged
            LOG.exception("A message sent later could not be sent")
            reason = f"it could not be sent ({type(error).__name__})."
        else:
            late = int((now - datetime.fromisoformat(row["send_at"])).total_seconds() // 60)
            self._event(scheduled_id, "sent", f"{late} minutes late" if late >= 2 else "", actor="faculty")
        finally:
            with self._lock:
                self._running.discard(scheduled_id)
                if reason:
                    self._waits[scheduled_id] = reason
                else:
                    self._waits.pop(scheduled_id, None)
        if held:
            self._event(scheduled_id, "held", held, actor="faculty")

    # -- the web process's thread ----------------------------------------------------------------------------------------------
    def start(self):
        """The web process's thread (app.py `__main__`): never started by the checks, which call `round` with their own clock."""
        self._stop = threading.Event()
        threading.Thread(target=self.worker, args=(self._stop,), name="scheduled-sends", daemon=True).start()

    def worker(self, stop):
        rounds.repeat(stop, self._locked_round, ROUND, LOG, "Messages sent later are waiting for storage",
                      "Sending messages later stopped unexpectedly")

    def _locked_round(self):
        with exclusive(self.storage, LOCK, Path(self.path).parent / ".scheduled-sends.lock", "Messages sent later are being sent now."):
            self.round()
