"""The communications record (requirement, 2026-09-30).

CAPTURE runs beside the Inbox worker loop, one channel at a time under its own lock (never the execution lock), each on its own
schedule, and whenever the instructor asks (Capture now):

  * GMAIL (every 5 minutes): mail from, to, cc or bcc a current student's roster address since their term's window opened
    (communications/gmail_capture.py). The message is kept whole, as Gmail holds it; its text is recorded for the timeline. Canvas's
    message notification emails too, a backup of the Canvas inbox, linked to the one student their name and section fit. A file a
    student linked from Google Drive is fetched and kept with their message's attachments (communications/drive_links.py).
  * CANVAS (every 15 minutes): the instructor's Canvas inbox conversations with students of each linked course (opened without
    marking them read), and the comments the student or the instructor wrote on the student's submissions. Attachments are kept.
  * GRADESCOPE (every 30 minutes): regrade requests and their replies in each linked course (communications/gradescope_capture.py),
    the versions of its linked bubble-sheet exams included (Gradescope keeps them off the assignments list).

Faculty's own records of mail it sent (Response Card and seat-chart email, Mail page sends through Gmail) and students' Response
Card answers are shown by reference (communications/record.py). Nothing is ever sent, changed or marked read anywhere; every read
is a GET. A student is known by their roster address, their Canvas SIS ID or Gradescope SID equal to their UIN, or Faculty's own
record of the send; never by a guess. A channel that cannot run (not connected, signed out, not set up) waits and says why.

Owners: see communications/migrations/001_record.sql. Lock order: the capture lock (one round at a time, across the web and worker
processes), roster reads (lifecycle lock inside `rosters.get`), network reads with no lock held, then for each batch of new messages
the roster lifecycle lock, the new bundle's owner lock and ONE catalog transaction recording the bundle, its files and the messages.
"""
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import StorageError, exclusive, timestamp
from canvas import web as canvas_web
from canvas.web import CanvasError
from gmail_mail.client import MailError, valid_id
from gmail_mail.service import variants, _field, roster_email
from grading import gradescope_web as gw
from student_photos.service import name_key
from response_cards.mail import valid_address
import rounds
from rosters import connect

from . import drive_links, gmail_capture, gradescope_capture, record
from .storage import CommunicationFiles

LOG = logging.getLogger(__name__)
LOCK = "5c1f2f0e-8a3e-4f59-9a55-2f6b8d3e7c41"   # one capture round at a time, across the web and worker processes
CHANNELS = ("gmail", "canvas", "gradescope")
EVERY = {"gmail": 300, "canvas": 900, "gradescope": 1800}
TICK = 20
BATCH = 50                          # new messages recorded per catalog transaction
READERS = 4                         # Canvas conversations read at once (each on its own use of the kept session)
READ_CHUNK = 40                     # ... in chunks, so a failure loses at most one chunk's reading
RUN_ORDER = ("gmail", "gradescope", "canvas")  # a long Canvas round never holds the other two back
READS = 150                         # Gmail messages read in one round; a longer backlog continues RETRY_SOON later
RETRY_SOON = 120
UNLINKABLE_FOR = 3600               # a message that named no single student is read again after this
ATTACHMENT_LIMIT = 25 * 1024 * 1024  # the largest Canvas attachment or linked Drive file kept (Gmail's own limit for a whole message)
DRIVE_READS = 20                    # linked Drive files tried again in one Gmail round (LINKED DRIVE FILES)
LABELS = {"gmail": "Gmail", "canvas": "Canvas", "gradescope": "Gradescope"}


class Waiting(Exception):
    """A channel that cannot run now, and why (not connected, signed out, not set up, busy)."""

    def __init__(self, message, retry=None):
        super().__init__(message)
        self.retry = retry


def iso(moment):
    """One format for every time the capture state holds, so they compare as text (microseconds: a request made during a round's
    first second is still after it began)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(value):
    """A UTC datetime from Canvas's, Gmail's or Faculty's own timestamps, else None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        found = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def opens(term):
    """When a term's window opens: mail about a course starts before its classes do (a fixed rule, stated in AGENTS.md): Fall from
    July 1, Spring from the previous November 1, Summer I and Summer (Full) from April 1, Summer II from May 15, Other from 120 days
    before the term was created in Faculty."""
    year = int(term["year"] or 0)
    starts = {"Fall": (year, 7, 1), "Spring": (year - 1, 11, 1), "Summer I": (year, 4, 1), "Summer (Full)": (year, 4, 1),
              "Summer II": (year, 5, 15)}
    if term["term"] in starts and year:
        return datetime(*starts[term["term"]], tzinfo=timezone.utc)
    created = parse_time(term["created_at"]) or datetime.now(timezone.utc)
    return created - timedelta(days=120)


class CommunicationService:
    def __init__(self, rosters, gmail_mail, canvas):
        self.rosters, self.path = rosters, rosters.path
        self.gmail, self.canvas, self.google = gmail_mail, canvas, gmail_mail.google
        self.storage = rosters.storage
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "communications", migration.stem, migration.read_text(encoding="utf-8"))
        self.files = CommunicationFiles(rosters)
        self.reader = gmail_capture.GmailReader(self.google)
        self.drive = drive_links.DriveReader(self.google)
        self._drive_tried = set()  # Drive files the account could not see when tried in this worker run (tried again in the next)
        # How a Gradescope session is made (tests hand in the fake's settings); read from the environment when first needed.
        self.gradescope_settings = None
        self._unlinkable = set()   # Gmail IDs a search found that name no single student (an address two students share)
        self._unlinkable_at = time.monotonic()
        self._retried = set()      # Canvas messages whose attachment was tried again in this worker run

    # -- State -------------------------------------------------------------------------------------------------------------
    def _states(self, db):
        return {row["channel"]: dict(row) for row in db.execute("SELECT * FROM comm_capture_state")}

    def _set(self, channel, **values):
        values["updated_at"] = timestamp()
        with connect(self.path) as db:
            db.execute(f"UPDATE comm_capture_state SET {', '.join(k + '=?' for k in values)} WHERE channel=?", (*values.values(), channel))

    def _setup(self):
        """Whether each channel can run at all, from configuration only (nothing is contacted)."""
        canvas = self.canvas.settings.status()
        gradescope = (self.gradescope_settings or gw.Settings.environment()).configured
        return {"gmail": {"ready": bool(self.gmail.ready()),
                          "setup": "" if self.gmail.ready() else "Connect Gmail for reading on the Mail page."},
                "canvas": {"ready": bool(canvas.get("configured") and canvas.get("signed_in")),
                           "setup": "" if canvas.get("configured") and canvas.get("signed_in") else
                           ("Sign in to Canvas from a section page." if canvas.get("configured") else "Canvas is not set up for this Faculty instance.")},
                "gradescope": {"ready": bool(gradescope), "setup": "" if gradescope else "Gradescope's saved sign-in is not set up for this Faculty instance."}}

    def status(self):
        with connect(self.path) as db:
            states = self._states(db)
            totals = {row["channel"]: row["n"] for row in db.execute("SELECT channel, COUNT(*) n FROM comm_messages GROUP BY channel")}
        setup = self._setup()
        channels = {}
        for channel in CHANNELS:
            state = states[channel]
            channels[channel] = {"label": LABELS[channel], "status": state["status"], "detail": state["detail"],
                                 "counts": json.loads(state["counts_json"] or "{}"), "last_round_at": state["last_round_at"] or None,
                                 "last_success_at": state["last_success_at"] or None, "next_due_at": state["next_due_at"] or None,
                                 "requested": bool(state["requested_at"] and state["requested_at"] > (state["last_round_at"] or "")),
                                 **setup[channel]}
        channels["gmail"]["kept"] = totals.get("gmail", 0)
        channels["canvas"]["kept"] = totals.get("canvas_message", 0) + totals.get("canvas_comment", 0)
        channels["gradescope"]["kept"] = totals.get("gradescope_regrade", 0)
        return {"channels": channels, "drive": self.drive_status()}

    def drive_status(self):
        """Whether a file a student links from Google Drive can be fetched (LINKED DRIVE FILES), from configuration only."""
        ready = self.drive.ready()
        return {"ready": ready, "connected": bool(self.google.connected),
                "setup": "" if ready else "A file a student links from Google Drive is not fetched: Faculty may not read your Google Drive yet."}

    def drive_connect(self):
        """Starts Google's sign-in asking for permission to read Drive files, for the account already connected. The instructor's
        own click; nothing is read until Google sends them back."""
        if not self.google.connected:
            raise Conflict("Connect Gmail on the Mail page first: Google Drive is read through the same Google account.")
        return {"url": self.google.start(self.google.email or "", purpose="drive")}

    def request(self, data):
        """Capture now: marks the chosen channels (all by default) wanted; the worker runs them within seconds. Nothing else."""
        data = data if isinstance(data, dict) else {}
        chosen = data.get("channels") or list(CHANNELS)
        if not isinstance(chosen, list) or not chosen or any(channel not in CHANNELS for channel in chosen):
            raise ValueError("Choose the channels to capture now: gmail, canvas or gradescope.")
        now = iso(datetime.now(timezone.utc))
        for channel in dict.fromkeys(chosen):
            self._set(channel, requested_at=now)
        return self.status()

    # -- Rounds ------------------------------------------------------------------------------------------------------------
    def lock(self):
        return exclusive(self.storage, LOCK, Path(self.path).parent / ".communications.lock", "A capture round is running now.")

    def due(self, now=None):
        now = now or datetime.now(timezone.utc)
        with connect(self.path) as db:
            states = self._states(db)
        found = []
        for channel in CHANNELS:
            state = states[channel]
            wanted = state["requested_at"] and state["requested_at"] > (state["last_round_at"] or "")
            next_due = parse_time(state["next_due_at"])
            if wanted or next_due is None or next_due <= now:
                found.append(channel)
        return found

    def run_due(self, now=None):
        """Every channel that is due (or asked for), one after another, under the capture lock."""
        with self.lock():
            for channel in sorted(self.due(now), key=RUN_ORDER.index):
                self.round(channel)

    def round(self, channel):
        """One capture round of one channel. Its outcome is the channel's state; nothing raises out of it but the lock's Conflict."""
        started = datetime.now(timezone.utc)
        values = {"last_round_at": iso(started)}
        try:
            counts, detail = getattr(self, f"_capture_{channel}")()
            values.update(status="ok", detail=detail, counts_json=record.encoded(counts), last_success_at=iso(started),
                          next_due_at=iso(started + timedelta(seconds=60 if counts.get("backlog") else EVERY[channel])))
        except Waiting as waiting:
            values.update(status="waiting", detail=str(waiting)[:2000],
                          next_due_at=iso(started + timedelta(seconds=max(waiting.retry or EVERY[channel], 60))))
        except (MailError, CanvasError) as error:
            # Google or Canvas could not be read now (busy, unreachable, a lapsed grant): the next round tries again.
            values.update(status="waiting", detail=str(error)[:2000], next_due_at=iso(started + timedelta(seconds=EVERY[channel])))
        except Exception as error:  # noqa: BLE001 - one channel's failure must not stop the others; the reason is shown
            LOG.exception("Capturing %s for the communications record failed", channel)
            values.update(status="failed", detail=(str(error) or type(error).__name__)[:2000],
                          next_due_at=iso(started + timedelta(seconds=EVERY[channel])))
        self._set(channel, **values)
        return values

    def worker(self, stop):
        """Beside the Inbox worker loop: every TICK seconds, the channels that are due or asked for."""
        try:
            self.files.sweep()
        except Exception:  # noqa: BLE001 - a folder that cannot be swept is left for the next start
            LOG.warning("Unrecorded communication bundles could not be swept", exc_info=True)
        rounds.repeat(stop, lambda: self.run_due(), TICK, LOG, "The communications record is waiting for storage",
                      "The communications record's capture stopped unexpectedly")

    # -- Who the students are ------------------------------------------------------------------------------------------------
    def _terms(self, db):
        return {row["id"]: {**dict(row), "opens": opens(row)} for row in
                db.execute("SELECT id, term, term_other, year, code, created_at FROM course_terms WHERE is_current=1")}

    def _students(self):
        """({enrollment_id: {enrollment_id, term_id, uin, name, emails, places}}, {term_id: term}, [sections whose roster could not be
        read]) over every current section's roster, dropped students included (their mail is still theirs). A roster that cannot be
        read is skipped and named."""
        with connect(self.path) as db:
            terms = self._terms(db)
        students, unread = {}, []
        for section in self.gmail.sections():
            try:
                roster = self.rosters.get(section["group_id"], section["number"])
            except Exception:  # noqa: BLE001 - one unreadable roster must not stop the others
                unread.append(f"{section['code']} Section {section['number']}")
                continue
            if roster["term_id"] not in terms:
                continue
            for student in roster["students"]:
                entry = students.setdefault(student["id"], {"enrollment_id": student["id"], "term_id": roster["term_id"],
                                                            "uin": str(student.get("uin") or ""), "name": _field(student, "name") or "Student",
                                                            "emails": set(), "places": set()})
                entry["places"].add((str(section["code"]).replace(" ", ""), str(section["number"])))
                address = roster_email(student)
                if address and valid_address(address):
                    entry["emails"].add(address.casefold())
        return students, terms, unread

    def _coordinators(self, terms):
        """THE COORDINATOR'S MAIL: [(address, entry)] for the coordinators of the current coordinated `terms`, each entry shaped as
        a student's ({enrollment_id: the term's owner key, term_id, uin: that key, coordinator: True}) so mail is found and linked
        the same way. One address names ONE term: someone who coordinates several current terms is linked under the term they
        coordinated first (then the term ID), so a message of theirs is one conversation, never one per course."""
        from course_coordinators import service as coordinators
        with connect(self.path) as db:
            found = coordinators.current(db, set(terms))
        home = {}
        for entry in sorted(found.values(), key=lambda entry: (entry["since"], entry["term_id"])):
            address = entry["email"].casefold()
            if valid_address(address):
                home.setdefault(variants(address)[0], (address, entry["term_id"]))
        return [(address, {"enrollment_id": coordinators.owner_key(term_id), "term_id": term_id,
                           "uin": coordinators.owner_key(term_id), "coordinator": True}) for address, term_id in home.values()]

    def _linked_courses(self, table, column):
        """{external course id: {term_id}} of the current sections' latest links in `table`."""
        found = {}
        with connect(self.path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                return found
            rows = db.execute(f"""SELECT l.{column} course, s.term_id FROM {table} l
                                  JOIN course_sections s ON s.id=l.section_id JOIN course_terms t ON t.id=s.term_id
                                  WHERE t.is_current=1 AND l.rowid=(SELECT x.rowid FROM {table} x WHERE x.section_id=l.section_id
                                                                   ORDER BY x.created_at DESC, x.rowid DESC LIMIT 1)""").fetchall()
        for row in rows:
            if not row["course"]:
                continue
            found.setdefault(str(row["course"]), {"term_id": row["term_id"]})
        return found

    def _sheet_versions(self, course):
        """The version assignments of the bubble-sheet exams Faculty linked in this Gradescope course (GRADESCOPE "BUBBLE SHEETS ARE
        UPLOAD ONLY"): Gradescope keeps them off the course's assignments list, so their regrade requests are read by these IDs
        (REGRADES "BUBBLE SHEETS"). [{id, title}], each exam's latest link per section, from Faculty's own records."""
        with connect(self.path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='frq_gradescope_sheet_targets'").fetchone():
                return []
            rows = db.execute("""SELECT t.container_title, t.versions_json FROM frq_gradescope_sheet_targets t
                JOIN frq_gradescope_courses c ON c.id=t.course_link_id WHERE c.course_id=? AND t.rowid=(SELECT x.rowid FROM frq_gradescope_sheet_targets x
                WHERE x.assignment_id=t.assignment_id AND x.section_id=t.section_id ORDER BY x.created_at DESC, x.rowid DESC LIMIT 1)
                ORDER BY t.created_at, t.rowid""", (str(course),)).fetchall()
        found = {}
        for row in rows:
            for letter, assignment in sorted((json.loads(row["versions_json"] or "{}") or {}).items()):
                if str(assignment).isdigit():
                    found.setdefault(str(assignment), {"id": str(assignment), "title": f"{row['container_title']} Version {letter}"})
        return list(found.values())

    def _enrollments_by_uin(self, term_id):
        with connect(self.path) as db:
            return {row["uin"]: row["id"] for row in db.execute("SELECT id, uin FROM student_enrollments WHERE term_id=?", (term_id,))}

    def _known(self, channel):
        with connect(self.path) as db:
            return {row["external_id"]: row["id"] for row in db.execute("SELECT id, external_id FROM comm_messages WHERE channel=?", (channel,))}

    def _write(self, bundle, items):
        """Record a batch of captured messages (and publish the bundle holding their kept files) in one transaction."""
        if not items and not bundle.records:
            return 0
        added = 0
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self.files.seal(bundle, db)
                for item in items:
                    result = record.record(db, item["channel"], item["external_id"], item["revision"], item["links"], item.get("thread_key", ""))
                    added += result["new"]
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return added

    # -- Gmail -------------------------------------------------------------------------------------------------------------
    def _faculty_sends(self):
        """{Gmail message ID: {addresses: [its Bcc], at}} of the Mail page's accepted sends: their Bcc is only in Faculty's record (To
        and Cc are in the message itself)."""
        found = {}
        with connect(self.path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='gmail_mail_sends'").fetchone():
                return found
            for row in db.execute("""SELECT s.to_json, s.cc_json, s.bcc_json, s.created_at, o.message_id FROM gmail_mail_sends s
                                     JOIN gmail_mail_outcomes o ON o.send_id=s.id
                                     WHERE o.status='accepted' AND o.id=(SELECT MAX(id) FROM gmail_mail_outcomes WHERE send_id=s.id)"""):
                if valid_id(row["message_id"]):
                    found[row["message_id"]] = {"addresses": [a for a in json.loads(row["bcc_json"]) if isinstance(a, str)], "at": row["created_at"]}
        return found

    def _gmail_links(self, parsed, index, terms, sent_at, sends):
        hits, role = gmail_capture.links(parsed, index, self.google.email, sends)
        moment = parse_time(sent_at)
        found = []
        for entry, kind, matched in hits:
            term = terms.get(entry["term_id"])
            if term and (moment is None or moment >= term["opens"]):
                found.append((entry["enrollment_id"], entry["term_id"], kind, matched))
        return list(dict.fromkeys(found)), role

    def _capture_gmail(self):
        if not self.gmail.ready():
            raise Waiting("Connect Gmail for reading on the Mail page.")
        if time.monotonic() - self._unlinkable_at > UNLINKABLE_FOR:
            # A student added to the roster since, or a name no longer shared, may name someone now: look at those again.
            self._unlinkable, self._unlinkable_at = set(), time.monotonic()
        students, terms, unread = self._students()
        index, addresses = {}, {}
        for student in students.values():
            for address in student["emails"]:
                entry = {"enrollment_id": student["enrollment_id"], "term_id": student["term_id"], "uin": student["uin"]}
                index.setdefault(variants(address)[0], []).append(entry)
                addresses.setdefault(student["term_id"], set()).update(variants(address))
        # THE COORDINATOR'S MAIL: a coordinated term's coordinator is searched for and linked as a student is, under the term.
        for address, entry in self._coordinators(terms):
            index.setdefault(variants(address)[0], []).append(entry)
            addresses.setdefault(entry["term_id"], set()).update(variants(address))
        # Canvas's message notifications name their sender only by name and section (the backup of the Canvas inbox).
        people = [{"words": set(name_key(student["name"]).split()), "places": student["places"],
                   "entry": {"enrollment_id": student["enrollment_id"], "term_id": student["term_id"], "uin": student["uin"]}}
                  for student in students.values()]
        found = {}
        for term_id, term in terms.items():
            listed = sorted(addresses.get(term_id, ()))
            for start in range(0, len(listed), gmail_capture.CHUNK):
                for message_id in self.reader.ids(gmail_capture.query(listed[start:start + gmail_capture.CHUNK], term["opens"])):
                    found.setdefault(message_id, set()).add(term_id)
        sends = self._faculty_sends()
        earliest = min((term["opens"] for term in terms.values()), default=None)
        if earliest:
            for message_id in self.reader.ids(gmail_capture.notices_query(earliest)):
                found.setdefault(message_id, set())
        for message_id, send in sends.items():
            sent = parse_time(send["at"])
            if earliest and sent and sent >= earliest:
                found.setdefault(message_id, set())
        known = self._known("gmail")
        counts = {"found": len(found), "new": 0, "linked": 0, "skipped": 0, "gone": 0, "canvas_notices": 0, "canvas_notices_unmatched": 0,
                  "without_address": sum(1 for s in students.values() if not s["emails"])}
        # Newest first (Gmail's IDs grow with time), at most READS a round: a first capture of a term's mail is read over several
        # rounds a minute apart (Gmail limits how much one account downloads), and the newest mail never waits behind it.
        waiting = sorted((m for m in found if m not in known and m not in self._unlinkable), key=lambda m: (len(m), m), reverse=True)
        counts["backlog"] = max(0, len(waiting) - READS)
        bundle, items = self.files.bundle(), []
        try:
            for message_id in waiting[:READS]:
                try:
                    got = self.reader.raw(message_id)
                except MailError as error:
                    if error.status == 404:
                        counts["gone"] += 1  # deleted for good between the search and the read: nothing left to keep
                        continue
                    # Gmail asked to slow down (or could not be reached): keep what was read, and go on next round.
                    self._link_drive(bundle, items, counts)
                    counts["new"] += self._write(bundle, items)
                    bundle, items = self.files.bundle(), []
                    raise Waiting(f"{error} {counts['new']} message{'s' if counts['new'] != 1 else ''} kept this round; the rest "
                                  "follow in the next rounds.", retry=RETRY_SOON) from None
                parsed = gmail_capture.parse(got["raw"])
                sent_at = got["at"] or timestamp()
                notice = gmail_capture.message_notice(parsed)
                if notice:
                    links = self._notice_links(notice, people, terms, sent_at)
                    counts["canvas_notices" if links else "canvas_notices_unmatched"] += 1
                    role = "student"
                elif gmail_capture.canvas_notice(parsed):
                    links = []  # Canvas's other email (grades, announcements) is no student's message
                else:
                    links, role = self._gmail_links(parsed, index, terms, sent_at, sends.get(message_id, {}).get("addresses", ()))
                if not links:
                    self._unlinkable.add(message_id)
                    counts["skipped"] += 0 if notice else 1
                    continue
                if "SENT" in got["labels"] and not notice:
                    role = "instructor"
                kept = bundle.add(got["raw"], "message/rfc822", parsed["subject"])
                recipients = [{**person, "kind": kind} for kind in ("to", "cc", "bcc") for person in parsed[kind]]
                context = {"thread_id": got["thread_id"], "header_id": parsed["message_id"], "date": parsed["date"]}
                if notice:
                    # A copy of a Canvas message as Canvas emailed it: the message alone is shown, the whole email is the original.
                    context.update(canvas_notice=True, email_subject=parsed["subject"], code=notice["code"], section=notice["section"],
                                   conversation_id=notice["conversation_id"], canvas_message_id=notice["canvas_message_id"], url=notice["url"])
                items.append({"channel": "gmail", "external_id": message_id, "thread_key": got["thread_id"], "links": links, "revision": {
                    "sent_at": sent_at, "author_role": role, "author": (parsed["from"] or [{}])[0], "recipients": recipients,
                    "subject": (notice["subject"] or parsed["subject"]) if notice else parsed["subject"],
                    "body_text": notice["text"] if notice else parsed["text"], "attachments": parsed["attachments"],
                    "context": context, "original_file_id": kept["id"]}, "drive": role == "student" and not notice})
                if len(items) >= BATCH or bundle.full:
                    self._link_drive(bundle, items, counts)
                    counts["new"] += self._write(bundle, items)
                    bundle, items = self.files.bundle(), []
            self._link_drive(bundle, items, counts)
            counts["new"] += self._write(bundle, items)
        except BaseException:
            bundle.discard()
            raise
        counts["linked"] = self._relink_gmail(found, known, index, terms, sends, people)
        later = ""
        try:
            self._linked_later(counts)
        except Exception:  # noqa: BLE001 - the mail itself was kept: a linked file that failed is said and tried again next round
            LOG.exception("Linked Google Drive files could not be fetched this round")
            later = "Files linked from Google Drive could not be fetched this round; the next round tries again."
        detail = " ".join(filter(None, [f"{counts['backlog']} older messages still to read; the next round continues." if counts["backlog"] else "",
                                         f"Rosters not read: {', '.join(unread)}." if unread else "", later]))
        return counts, detail

    # -- Files linked from Google Drive (LINKED DRIVE FILES) ---------------------------------------------------------------
    def _instructor_linked(self, file_id, sent_at, batch=()):
        """A file the instructor linked before the student's message is the instructor's own, whoever's reply carries its link:
        in the record, or among the messages `batch` this round is about to record."""
        if any(item["revision"]["author_role"] == "instructor" and item["revision"]["sent_at"] < sent_at
               and file_id in item["revision"]["body_text"] for item in batch):
            return True
        with connect(self.path) as db:
            return bool(db.execute("""SELECT 1 FROM comm_revisions WHERE author_role IN ('instructor','staff') AND sent_at < ?
                                      AND instr(body_text, ?) LIMIT 1""", (sent_at, file_id)).fetchone())

    def _link_drive(self, bundle, items, counts):
        """Before a batch of new Gmail messages is recorded: each one a student wrote gets the Drive files it links, read now, so
        its first revision already holds them (a follow-up prepared from it sees them)."""
        for item in items:
            if item.pop("drive", False):
                revision = item["revision"]
                revision["attachments"] = self._drive_entries(bundle, revision["body_text"], revision["sent_at"], revision["attachments"], counts, batch=items)

    def _drive_entries(self, bundle, text, sent_at, existing, counts, reads=None, batch=()):
        """A student message's attachment list with the Drive files it links: each one kept in `bundle`, or listed as not kept with
        the reason and what it waits on. `existing` (the attachments Gmail holds, and any linked file already listed) keeps its
        order, so an attachment's place never changes; a linked file is added after them, and one already listed is tried again
        only while something can still change it. `reads` bounds the files tried (None: no bound)."""
        entries = [dict(entry) for entry in existing]
        places = {entry.get("drive_id"): place for place, entry in enumerate(entries) if entry.get("drive_id")}
        for link in drive_links.linked(text):
            place = places.get(link["id"])
            if place is None:
                if self._instructor_linked(link["id"], sent_at, batch):
                    continue
            else:
                wait = entries[place].get("wait")
                if entries[place].get("kept") is not False or wait not in drive_links.WAITS or (wait == "sharing" and link["id"] in self._drive_tried):
                    continue
            if reads is not None:
                if reads[0] <= 0:
                    continue
                reads[0] -= 1
            entry = {"name": link["name"] or drive_links.NAMELESS, "drive_id": link["id"]}
            try:
                try:
                    got = self.drive.fetch(link["id"], ATTACHMENT_LIMIT)
                except drive_links.NotKept:
                    raise
                except Exception:  # noqa: BLE001 - the message is kept whatever became of its linked file
                    LOG.exception("A linked Google Drive file could not be read")
                    raise drive_links.NotKept("Faculty could not read it from Google Drive", "google") from None
                stored = bundle.add(got["content"], got["type"], got["name"])
                entry.update(name=got["name"], type=got["type"], size=len(got["content"]), kept=True, file_id=stored["id"], sha256=stored["sha256"])
                counts["drive_files"] = counts.get("drive_files", 0) + 1
            except drive_links.NotKept as error:
                entry.update(kept=False, reason=str(error)[:300], **({"wait": error.wait} if error.wait else {}))
                if place is not None and entries[place].get("name") not in (None, drive_links.NAMELESS):
                    entry["name"] = entries[place]["name"]
                if error.wait == "sharing":
                    self._drive_tried.add(link["id"])
            if place is None:
                places[link["id"]] = len(entries)
                entries.append(entry)
            else:
                entries[place] = entry
        return entries

    def _linked_later(self, counts):
        """Linked Drive files of messages already kept: one not fetched yet for a reason that may pass (Faculty was not allowed to
        read Drive, the student had not shared the file, Drive did not answer), and the links of a message kept before Faculty
        read Drive at all. Only while Faculty may read Drive, at most DRIVE_READS files a round, students' own messages of the
        current terms; a file the account could not see is tried once per worker run. The message then gets a revision with the
        file ("edited" means only that a message's words changed)."""
        if not self.drive.ready():
            return
        with connect(self.path) as db:
            rows = db.execute("""
                SELECT m.external_id, m.thread_key, r.* FROM comm_messages m
                JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
                WHERE m.channel='gmail' AND r.author_role='student' AND r.body_text LIKE '%google.com/%'
                  AND json_extract(r.context_json,'$.canvas_notice') IS NULL
                  AND EXISTS(SELECT 1 FROM comm_links l JOIN course_terms t ON t.id=l.term_id
                             WHERE l.message_id=m.id AND l.role='author' AND t.is_current=1 AND t.deleted_at IS NULL)
                ORDER BY r.sent_at DESC, m.id""").fetchall()
        bundle, items, reads = self.files.bundle(), [], [DRIVE_READS]
        try:
            for row in rows:
                if reads[0] <= 0:
                    break
                existing = json.loads(row["attachments_json"])
                attachments = self._drive_entries(bundle, row["body_text"], row["sent_at"], existing, counts, reads)
                if attachments == existing:
                    continue
                items.append({"channel": "gmail", "external_id": row["external_id"], "thread_key": row["thread_key"], "links": [], "revision": {
                    "sent_at": row["sent_at"], "author_role": row["author_role"], "author": json.loads(row["author_json"]),
                    "recipients": json.loads(row["recipients_json"]), "subject": row["subject"], "body_text": row["body_text"],
                    "attachments": attachments, "context": json.loads(row["context_json"]), "original_file_id": row["original_file_id"]}})
            self._write(bundle, items)
        except BaseException:
            bundle.discard()
            raise

    def _notice_links(self, notice, people, terms, sent_at):
        """The one student a Canvas message notification is from (`gmail_capture.notice_students`), in a term whose window it falls
        in; none when no student, or more than one, fits."""
        students = gmail_capture.notice_students(notice, people)
        moment = parse_time(sent_at)
        if len(students) != 1:
            return []
        entry = students[0]
        term = terms.get(entry["term_id"])
        if not term or (moment is not None and moment < term["opens"]):
            return []
        return [(entry["enrollment_id"], entry["term_id"], "author", "canvas_notice")]

    def _relink_gmail(self, found, known, index, terms, sends, people=()):
        """Links for messages captured earlier that a search found again for a term they are not linked to yet (a student joined
        the roster, or a new term opened): from the kept addresses, no message read again."""
        with connect(self.path) as db:
            existing = {(row["message_id"], row["enrollment_id"], row["role"]) for row in db.execute(
                "SELECT l.message_id, l.enrollment_id, l.role FROM comm_links l JOIN comm_messages m ON m.id=l.message_id WHERE m.channel='gmail'")}
            latest = {row["message_id"]: row for row in db.execute(
                """SELECT r.message_id, r.author_json, r.recipients_json, r.context_json, r.sent_at FROM comm_revisions r JOIN comm_messages m ON m.id=r.message_id
                   WHERE m.channel='gmail' AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=r.message_id)""")}
            wanted = {}
            for external in found:
                message_id = known.get(external)
                row = latest.get(message_id)
                if not row:
                    continue
                recipients = json.loads(row["recipients_json"])
                context = json.loads(row["context_json"])
                if context.get("canvas_notice"):
                    notice = {"name": json.loads(row["author_json"]).get("name", ""), "code": context.get("code", ""),
                              "section": context.get("section", "")}
                    links = self._notice_links(notice, people, terms, row["sent_at"])
                else:
                    parsed = {"from": [json.loads(row["author_json"])], **{kind: [p for p in recipients if p.get("kind") == kind] for kind in ("to", "cc", "bcc")}}
                    links, _ = self._gmail_links(parsed, index, terms, row["sent_at"], sends.get(external, {}).get("addresses", ()))
                missing = [link for link in links if (message_id, link[0], link[2]) not in existing]
                if missing:
                    wanted[message_id] = missing
            if not wanted:
                return 0
            db.execute("BEGIN IMMEDIATE")
            added = sum(record.link(db, message_id, links) for message_id, links in wanted.items())
            db.commit()
        return added

    # -- Canvas ------------------------------------------------------------------------------------------------------------
    def _capture_canvas(self):
        if not self.canvas.settings.configured:
            raise Waiting("Canvas is not set up for this Faculty instance.")
        courses = self._linked_courses("canvas_course_links", "course_id")
        counts = {"courses": len(courses), "conversations": 0, "new": 0, "not_kept": 0, "files_reused": 0, "other_authors": 0,
                  "unmatched_students": 0}
        if not courses:
            return counts, "No current section is linked to a Canvas course."
        try:
            client = self.canvas.client()
        except canvas_web.SignedOut:
            raise Waiting("Sign in to Canvas from a section page; Faculty's Canvas session has ended.") from None
        base = self.canvas.settings.base
        # Messages and comments whose attachment could not be kept (not for its size) are read again once per worker run: listed
        # once for the whole round, and counted as tried only when the round has gone through every course.
        retry = frozenset(self._canvas_retry() - self._retried)
        try:
            with client:
                me = client.me()["id"]
                # Every submission of a course is one long listing (about 40 pages each for MATH 140): the courses' are read at once.
                comments = self._read_comments(list(courses))
                for course_id, info in courses.items():
                    self._canvas_course(client, base, me, course_id, info, counts, retry, comments[course_id])
        except canvas_web.SignedOut:
            raise Waiting("Sign in to Canvas from a section page; Faculty's Canvas session has ended.") from None
        self._retried |= retry
        return counts, ""

    def _canvas_course(self, client, base, me, course_id, info, counts, retry, comments_listed):
        by_uin = self._enrollments_by_uin(info["term_id"])
        users = {}
        for enrollment in client.students(course_id):
            sis = enrollment["sis"]
            if enrollment["user_id"] and sis in by_uin:
                users[enrollment["user_id"]] = {"enrollment_id": by_uin[sis], "name": enrollment["name"]}
            elif enrollment["user_id"]:
                counts["unmatched_students"] += 1
        known = self._known("canvas_message")
        with connect(self.path) as db:
            latest = {row["thread_key"]: row["at"] for row in db.execute(
                """SELECT m.thread_key, MAX(r.sent_at) at FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id
                   WHERE m.channel='canvas_message' GROUP BY m.thread_key""")}
        # A file Canvas holds once (a message sent to many students one by one, each its own conversation) is kept once: by its ID.
        kept_files = self._kept_canvas_files()
        bundle, items = self.files.bundle(), []

        def flush():
            nonlocal bundle, items
            counts["new"] += self._write(bundle, items)
            bundle, items = self.files.bundle(), []

        def kept_attachments(attachments):
            found = []
            for attachment in attachments[:20]:
                entry = {"name": attachment["name"], "type": attachment["type"], "size": attachment["size"], "canvas_id": attachment["id"]}
                if attachment["id"] and attachment["id"] in kept_files:
                    entry.update(kept=True, **kept_files[attachment["id"]])
                    counts["files_reused"] += 1
                elif not attachment["url"] or attachment["size"] > ATTACHMENT_LIMIT:
                    entry.update(kept=False, reason="larger than Faculty keeps" if attachment["size"] > ATTACHMENT_LIMIT else "Canvas gave no address")
                else:
                    try:
                        content, kind = client.download(attachment["url"], ATTACHMENT_LIMIT)
                        stored = bundle.add(content, attachment["type"] or kind, attachment["name"])
                        entry.update(kept=True, file_id=stored["id"], sha256=stored["sha256"], size=len(content))
                        if attachment["id"]:
                            kept_files[attachment["id"]] = {"file_id": stored["id"], "sha256": stored["sha256"], "size": len(content)}
                    except canvas_web.SignedOut:
                        raise
                    except canvas_web.CanvasError as error:
                        entry.update(kept=False, reason=str(error)[:200])
                        counts["not_kept"] += 1
                found.append(entry)
            return found

        try:
            wanted = []
            for conversation in client.conversations(course_id):
                if not any(user in users for user in conversation["participants"]):
                    continue
                counts["conversations"] += 1
                thread = f"{course_id}:{conversation['id']}"
                retrying = any(external.startswith(f"{conversation['id']}:") for external in retry)
                if not retrying and thread in latest and conversation["last_message_at"] and \
                        (parse_time(conversation["last_message_at"]) or datetime.max.replace(tzinfo=timezone.utc)) <= parse_time(latest[thread]):
                    continue
                wanted.append(conversation)
            for start in range(0, len(wanted), READ_CHUNK):
                chunk = wanted[start:start + READ_CHUNK]
                fulls = self._read_conversations([conversation["id"] for conversation in chunk])
                for conversation in chunk:
                    thread = f"{course_id}:{conversation['id']}"
                    full = fulls[conversation["id"]]
                    names = {**conversation["participants"], **full["participants"]}
                    for message in full["messages"]:
                        external = f"{conversation['id']}:{message['id']}"
                        if message["generated"] or (external in known and external not in retry):
                            continue
                        author = message["author_id"]
                        role = "instructor" if author == me else "student" if author in users else "staff"
                        links = [(users[user]["enrollment_id"], info["term_id"], "author" if user == author else "to", "canvas_sis")
                                 for user in dict.fromkeys(message["participants"] or list(names)) if user in users]
                        if not links:
                            continue
                        items.append({"channel": "canvas_message", "external_id": external, "thread_key": thread, "links": list(dict.fromkeys(links)), "revision": {
                            "sent_at": iso(parse_time(message["created_at"]) or datetime.now(timezone.utc)), "author_role": role,
                            "author": {"name": names.get(author, ""), "canvas_id": author},
                            "recipients": [{"name": names.get(user, ""), "canvas_id": user, "kind": "to"} for user in message["participants"] if user != author],
                            "subject": full["subject"] or conversation["subject"], "body_text": message["body"] + ("\n\n[A media message is attached in Canvas.]" if message["media"] else ""),
                            "attachments": kept_attachments(message["attachments"]),
                            "context": {"course_id": course_id, "course": full["context"], "conversation_id": conversation["id"],
                                        "url": f"{base}/conversations"}}})
                        if len(items) >= BATCH or bundle.full:
                            flush()
            flush()
            assignments = client.named(course_id, "Assignment")
            comments = self._known_revisions("canvas_comment")
            for comment in comments_listed:
                student = users.get(comment["user_id"])
                if not student:
                    continue
                if comment["author_id"] not in (comment["user_id"], me):
                    counts["other_authors"] += 1
                    continue
                external = f"{course_id}:{comment['id']}"
                role = "student" if comment["author_id"] == comment["user_id"] else "instructor"
                text = comment["comment"] + ("\n\n[A media comment is attached in Canvas.]" if comment["media"] else "")
                if external in comments and comments[external] == (text, comment["edited_at"]) and external not in retry:
                    continue
                title = assignments.get(comment["assignment_id"], "")
                items.append({"channel": "canvas_comment", "external_id": external, "thread_key": f"{course_id}:{comment['assignment_id']}:{comment['user_id']}",
                              "links": [(student["enrollment_id"], info["term_id"], "author" if role == "student" else "about", "canvas_sis")], "revision": {
                    "sent_at": iso(parse_time(comment["created_at"]) or datetime.now(timezone.utc)), "author_role": role,
                    "author": {"name": comment["author_name"], "canvas_id": comment["author_id"]}, "recipients": [],
                    "subject": f"Comment on {title}" if title else "Comment on a submission", "body_text": text,
                    "attachments": kept_attachments(comment["attachments"]),
                    "context": {"course_id": course_id, "assignment_id": comment["assignment_id"], "assignment": title, "edited_at": comment["edited_at"],
                                "url": f"{base}/courses/{course_id}/assignments/{comment['assignment_id']}/submissions/{comment['user_id']}"}}})
                if len(items) >= BATCH or bundle.full:
                    flush()
            flush()
        except BaseException:
            bundle.discard()
            raise

    def _read_conversations(self, ids):
        """{conversation id: conversation} read READERS at a time, each reader on its own use of the kept session."""
        def read(part):
            with self.canvas.client() as reader:
                return [(conversation_id, reader.conversation(conversation_id)) for conversation_id in part]
        parts = [part for part in (ids[n::READERS] for n in range(READERS)) if part]
        with ThreadPoolExecutor(max_workers=max(1, len(parts))) as pool:
            return dict(pair for found in pool.map(read, parts) for pair in found)

    def _read_comments(self, course_ids):
        """{course id: its submission comments}, the courses read at once, each on its own use of the kept session."""
        def read(course_id):
            with self.canvas.client() as reader:
                return course_id, reader.submission_comments(course_id)
        with ThreadPoolExecutor(max_workers=max(1, min(READERS, len(course_ids)))) as pool:
            return dict(pool.map(read, course_ids))

    def _kept_canvas_files(self):
        """{Canvas file ID: {file_id, sha256, size}} of every Canvas attachment already kept."""
        with connect(self.path) as db:
            return {str(row["canvas_id"]): {"file_id": row["file_id"], "sha256": row["sha256"], "size": row["size"]} for row in db.execute(
                """SELECT json_extract(a.value,'$.canvas_id') canvas_id, json_extract(a.value,'$.file_id') file_id,
                          json_extract(a.value,'$.sha256') sha256, json_extract(a.value,'$.size') size
                   FROM comm_revisions r JOIN comm_messages m ON m.id=r.message_id, json_each(r.attachments_json) a
                   WHERE m.channel IN ('canvas_message','canvas_comment') AND json_extract(a.value,'$.kept')=1
                     AND json_extract(a.value,'$.file_id') IS NOT NULL AND json_extract(a.value,'$.canvas_id') != ''""")}

    def _canvas_retry(self):
        """External IDs of Canvas messages and comments whose latest revision has an attachment not kept for a reason that may pass
        (not its size, not a missing address)."""
        with connect(self.path) as db:
            return {row[0] for row in db.execute(
                """SELECT DISTINCT m.external_id FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id, json_each(r.attachments_json) a
                   WHERE m.channel IN ('canvas_message','canvas_comment')
                     AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
                     AND json_extract(a.value,'$.kept')=0 AND json_extract(a.value,'$.reason') NOT IN ('larger than Faculty keeps','Canvas gave no address')""")}

    def _known_revisions(self, channel):
        """{external id: (text, edited_at)} of the latest revision of each message of a channel (what a changed comment differs from)."""
        with connect(self.path) as db:
            return {row["external_id"]: (row["body_text"], json.loads(row["context_json"]).get("edited_at", "")) for row in db.execute(
                """SELECT m.external_id, r.body_text, r.context_json FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id
                   WHERE m.channel=? AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)""", (channel,))}

    # -- Gradescope --------------------------------------------------------------------------------------------------------
    def _gradescope_session(self):
        settings = self.gradescope_settings or gw.Settings.environment()
        if not settings.configured:
            raise Waiting("Gradescope's saved sign-in is not set up for this Faculty instance.")
        return gw.Session(settings)

    def _capture_gradescope(self):
        courses = self._linked_courses("frq_gradescope_courses", "course_id")
        counts = {"courses": len(courses), "requests": 0, "new": 0, "unmatched": 0, "pages_not_read": 0}
        session = self._gradescope_session()
        if not courses:
            return counts, "No current section is linked to a Gradescope course."
        with connect(self.path) as db:
            answered = {row["thread_key"] for row in db.execute(
                "SELECT DISTINCT thread_key FROM comm_messages WHERE channel='gradescope_regrade' AND external_id LIKE '%:reply'")}
        reasons, items = [], []
        try:
            for course, info in courses.items():
                by_uin = self._enrollments_by_uin(info["term_id"])
                roster = session.roster(course)
                listed = session.assignments(course)
                ids = {str(a["id"]) for a in listed}
                for assignment in [*listed, *(v for v in self._sheet_versions(course) if v["id"] not in ids)]:
                    try:
                        rows = gradescope_capture.listing(session.get(f"/courses/{session.guard(course)}/assignments/{int(assignment['id'])}/regrade_requests"))
                    except gw.GradescopeBusy:
                        raise
                    except gw.GradescopeError as error:
                        if error.status in (401, 403, 404):
                            continue  # an assignment kind without regrade requests
                        raise
                    for row in rows:
                        counts["requests"] += 1
                        thread = f"{course}:{row['qsub_id']}"
                        if thread in answered and row["completed"]:
                            continue
                        props, question, title, path = gradescope_capture.grading_page(session, course, row["question_id"], row["qsub_id"])
                        sid, reason = gradescope_capture.student_of(row, props, roster)
                        enrollment = by_uin.get(sid) if sid else None
                        if not enrollment:
                            counts["unmatched"] += 1
                            reasons.append(reason or "the student's Gradescope SID is not a UIN of the section's term")
                            continue
                        subject = " · ".join(filter(None, ["Regrade request", title or assignment["title"], question or row["question"]]))
                        context = {"course_id": course, "assignment_id": assignment["id"], "assignment": title or assignment["title"],
                                   "question": question or row["question"], "url": f"{session.base}{path}"}
                        for exchange in gradescope_capture.exchanges(props):
                            key = f"{course}:{row['qsub_id']}:{exchange['key']}"
                            sent = parse_time(exchange["created_at"])
                            items.append({"channel": "gradescope_regrade", "external_id": f"{key}:request", "thread_key": thread,
                                          "links": [(enrollment, info["term_id"], "author", "gradescope_sid")], "revision": {
                                "sent_at": iso(sent) if sent else timestamp(), "author_role": "student", "author": {"name": row["student"]},
                                "recipients": [], "subject": subject, "body_text": exchange["request"], "attachments": [],
                                "context": {**context, "state": exchange["state"]}}})
                            if exchange["reply"]:
                                replied = parse_time(exchange["updated_at"]) or sent
                                items.append({"channel": "gradescope_regrade", "external_id": f"{key}:reply", "thread_key": thread,
                                              "links": [(enrollment, info["term_id"], "about", "gradescope_sid")], "revision": {
                                    "sent_at": iso(replied) if replied else timestamp(), "author_role": "staff",
                                    "author": {"name": exchange["replied_by"] or row["grader"] or "Course staff"}, "recipients": [],
                                    "subject": subject, "body_text": exchange["reply"], "attachments": [], "context": context}})
                        if len(items) >= BATCH:
                            counts["new"] += self._write(self.files.bundle(), items)
                            items = []
            counts["new"] += self._write(self.files.bundle(), items)
        except gw.SignInRefused:
            raise Waiting("Gradescope did not accept the saved sign-in. Run save-gradescope.py again.") from None
        except gw.GradescopeBusy as busy:
            if items:
                counts["new"] += self._write(self.files.bundle(), items)
            raise Waiting("Gradescope is busy; the capture will try again later.", retry=max(busy.retry_after or 0, 300)) from None
        finally:
            session.close()
        detail = ""
        if counts["unmatched"]:
            detail = f"{counts['unmatched']} regrade request{'s' if counts['unmatched'] != 1 else ''} could not be matched to a student ({reasons[0]})."
        return counts, detail

    # -- Reading the record ------------------------------------------------------------------------------------------------
    def coordinator_timeline(self, term_id):
        """THE COORDINATOR'S MAIL: every kept message with the coordinator of a term (its owner key's links), newest first, in the
        timeline's own shape. A term that was never coordinated has none."""
        from course_coordinators import service as coordinators
        with connect(self.path) as db:
            if not isinstance(term_id, str) or not db.execute("SELECT 1 FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone():
                raise NotFound("No such course term.")
            entries = record.timeline(db, coordinators.owner_key(term_id))
            return {"term_id": term_id, "coordinator": coordinators.view(db, term_id), "entries": entries, **self.status()}

    def timeline(self, enrollment_id):
        if not isinstance(enrollment_id, str) or not enrollment_id.isdigit() or len(enrollment_id) != 10:
            raise NotFound("No such student.")
        with connect(self.path) as db:
            if not db.execute("SELECT 1 FROM student_enrollments WHERE id=?", (enrollment_id,)).fetchone():
                raise NotFound("No such student.")
            entries = record.timeline(db, enrollment_id)
        return {"enrollment_id": enrollment_id, "entries": entries, **self.status()}

    def _message(self, db, message_id):
        row = db.execute("""SELECT m.id, m.channel, r.revision, r.attachments_json, r.original_file_id, r.subject FROM comm_messages m
                            JOIN comm_revisions r ON r.message_id=m.id WHERE m.id=? ORDER BY r.revision DESC LIMIT 1""", (message_id,)).fetchone()
        if not row:
            raise NotFound("No such message in the record.")
        return row

    def revisions(self, message_id):
        with connect(self.path) as db:
            self._message(db, message_id)
            return {"message_id": message_id, "revisions": record.revisions(db, message_id)}

    def _file(self, file_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM comm_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            raise NotFound("The kept file is not in the record.")
        return dict(row)

    def original(self, message_id):
        """(file name, bytes) of the message as it was captured (a Gmail message as Gmail held it)."""
        with connect(self.path) as db:
            row = self._message(db, message_id)
        if not row["original_file_id"]:
            raise NotFound("Only email is kept as its original message; this one is shown as it was read.")
        return f"message-{message_id[:8]}.eml", self.files.read(self._file(row["original_file_id"]))

    def attachment(self, message_id, index):
        """(file name, content type, bytes) of one attachment of a captured message, checked against its checksum."""
        with connect(self.path) as db:
            row = self._message(db, message_id)
        attachments = json.loads(row["attachments_json"])
        if not 0 <= index < len(attachments):
            raise NotFound("That attachment is not in the record.")
        item = attachments[index]
        if item.get("kept") is False:
            raise NotFound(f"This attachment was not kept: {item.get('reason') or 'Canvas did not give it'}.")
        if row["channel"] == "gmail" and row["original_file_id"] and not item.get("drive_id"):
            name, kind, content = gmail_capture.attachment(self.files.read(self._file(row["original_file_id"])), int(item["part"]))
            if item.get("sha256") and hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise StorageError("The kept attachment failed checksum verification.")
            return item.get("name") or name, kind, content
        if item.get("file_id"):
            content = self.files.read(self._file(item["file_id"]))
            if item.get("drive_id") and item.get("sha256") and hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise StorageError("The kept attachment failed checksum verification.")
            return item.get("name") or "attachment", item.get("type") or "application/octet-stream", content
        raise NotFound("This attachment was not kept.")

    def seat_chart(self, mail_id):
        """The anonymous seat chart PDF Faculty emailed (seating_student_mail keeps it): shown with that email in the timeline."""
        with connect(self.path) as db:
            row = db.execute("SELECT pdf, output_sha256 FROM seating_student_mail WHERE id=?", (mail_id,)).fetchone()
        if not row or not row["pdf"]:
            raise NotFound("That seat chart email has no PDF.")
        if row["output_sha256"] and hashlib.sha256(row["pdf"]).hexdigest() != row["output_sha256"]:
            raise StorageError("The seat chart PDF failed checksum verification.")
        return bytes(row["pdf"])
