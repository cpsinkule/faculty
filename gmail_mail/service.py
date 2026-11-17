"""Mail in Faculty (requirement, 2026-09-28).

Three things, all through the one Google connection Response Cards made (response_cards/gmail.py), whose sign-in gains the
`gmail.readonly` scope for the account already connected:

  * READ: search the mailbox and open threads, as plain text. Faculty keeps no copy of anything it reads, and reading modifies
    nothing in Gmail (no labels, no read marks, no deletes).
  * STUDENT MAIL: a student's threads with the instructor, found by the address in the section roster (sent from or to it). It
    reads Gmail live, stores nothing, and never guesses: a student with no roster address has no mail here.
  * COMPOSE AND SEND: the instructor writes a message (a reply is built from the thread the SERVER reads) and approves the exact
    message with its fingerprint; nothing is sent by a preview. The send is recorded BEFORE the request leaves and its outcome
    after (`gmail_mail_sends`, `gmail_mail_outcomes`, append-only); a timeout or a Google error that may have delivered is
    `uncertain` and is NEVER sent again, and the same request ID returns what happened instead of sending twice.
  * ATTACHMENTS (2026-10-01): files named by the Faculty routes that serve them (the assistant's drafts and the compose window's
    "From Faculty"), read through the app's own routes when the message is previewed and again when it is sent, and files from the
    instructor's computer sent with the preview and the send; the fingerprint covers each file's digest, and the send record keeps
    references and digests (`gmail_mail_send_attachments`, `gmail_mail_send_uploads`), never a copy (attachments.py, library.py).
  * ARCHIVE AND TRASH: the instructor's explicit actions on one open conversation, both reversible (Undo), both needing the wider
    `gmail.modify` grant asked for only when first chosen. Archive takes off the Inbox label (the mail stays in All mail); Trash is
    Gmail's Trash (kept 30 days). Never a permanent delete, another label or a read mark. Only the thread ID and Google's answer are kept.

Owner: the WORKSPACE (the instructor's own mail belongs to no course, term or section). A roster or TA address is read from its own
owner when the page asks for an audience; nothing is copied. Bcc puts a section in one message nobody sees the list of.

Lock order: roster reads take the roster lifecycle lock inside `rosters.get` and finish before any catalog write; a send takes
only this service's own lock and catalog transactions.
"""
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from canvas import web as canvas_web
from catalog_migrations import apply_sql, migration_transaction
from course_groups import group_courses
from course_storage import live_offerings, timestamp
from response_cards.gmail import MAIL_MANAGE_SCOPES, MAIL_SCOPES
from response_cards.mail import valid_address
from rosters import connect

from . import attachments, message as mail
from student_photos.service import name_key

from .client import GmailMailClient, MailError, valid_id

REQUEST_ID_MAX = 100
# MAIL-SENDER-NAME (requirement, 2026-10-02): the Mail page writes as the instructor themself, as Gmail does. Response Cards and the
# seating mail keep "Professor Sinkule" (response_cards.mail.SIGNATURE), the name of the shared Gmail connection.
SENDER_NAME = "Christopher Sinkule"
QUERY_MAX = 300
SENT_LIMIT = 50
TABS = {"students": "in:inbox", "sent": "in:sent", "all": ""}
STUDENT_WANT = 20    # a Students page keeps scanning Gmail's inbox pages until it holds this many student threads
STUDENT_SCAN = 6     # ... or has read this many pages (the next page token then continues where it stopped)


def _query(value):
    value = " ".join(str(value or "").split())
    if len(value) > QUERY_MAX or any(ord(char) < 32 for char in value):
        raise ValueError("That search is too long.")
    return value


def _field(student, *names):
    fields = {str(k).casefold().strip(): str(v).strip() for k, v in (student.get("fields") or {}).items()}
    return next((fields[name] for name in names if fields.get(name)), "")


def roster_email(student):
    """The address a roster student's record gives, under any of the header spellings the roster import has produced."""
    return _field(student, "email", "email address", "e-mail")


ALIASES = ("email.tamu.edu", "tamu.edu")


def variants(address):
    """The spellings of one mailbox: TAMU delivers name@email.tamu.edu (the roster's form) and name@tamu.edu (how students write) to the
    same student, so those two domains, and only those, with the same part before the @, are one address."""
    local, _, domain = address.casefold().rpartition("@")
    return [f"{local}@{alias}" for alias in ALIASES] if domain in ALIASES and local else [address.casefold()]


CANVAS_DOMAINS = ("instructure.com",)
# Why a reply to a Canvas message goes by email when Faculty's Canvas session is gone; the review then offers Sign in to Canvas.
CANVAS_SIGNED_OUT = "Faculty is signed out of Canvas (sign in to Canvas to post replies there)."
# Where Canvas's notification emails come from and take replies (notifications@instructure.com, reply+…@notifications.canvaslms.com).
CANVAS_MAIL_DOMAINS = ("instructure.com", "canvaslms.com")


def canvas_address(address):
    domain = (address or "").casefold().rpartition("@")[2]
    return any(domain == d or domain.endswith("." + d) for d in CANVAS_MAIL_DOMAINS)
CANVAS_SECTION = re.compile(r"\((?:\d{2}\s+)?[A-Za-z]+\s+([A-Z]{2,5}\s?\d{3}[A-Z]?)\s+(\d{3}):")


def canvas_student(name, subject, people):
    """The one student a Canvas notification is from, or None. Canvas sends every message as "<student's name> <notifications@instructure.com>"
    with the course and section in the subject ("Ann Lee (26 FALL MATH 140 514: TITLE) just sent you a message in Canvas."), so the
    address names nobody. It matches only when the display name has at least two words, every one of them is a word of exactly one
    student's roster name, and, when the subject names a course section, that student is in it. Two students, none or a one-word name
    name nobody."""
    words = set(name_key(name).split())
    if len(words) < 2:
        return None
    where = CANVAS_SECTION.search(subject or "")
    hits = [p for p in people if words <= p["words"]
            and (not where or (p["code"].replace(" ", "") == where.group(1).replace(" ", "") and str(p["section"]) == where.group(2)))]
    return hits[0]["entry"] if len(hits) == 1 else None


def _instant(value):
    """A catalog time as the thread view writes Gmail's ("2026-10-02T17:35:45Z")."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""


def _answered_in_canvas(thread, answers):
    """Whether a reply was posted in Canvas from this thread at or after its latest message (MAIL-CANVAS-POST)."""
    posts = answers.get(str(thread.get("id") or ""))
    messages = [m for m in thread.get("messages") or [] if isinstance(m, dict) and "DRAFT" not in (m.get("labelIds") or [])]
    if not posts or not messages:
        return False
    try:
        last = datetime.fromtimestamp(int(messages[-1].get("internalDate")) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return any(datetime.fromisoformat(str(p["created_at"]).replace("Z", "+00:00")) >= last for p in posts)


def _label(group):
    term = group["term_other"] if group.get("term") == "Other" else group.get("term")
    return f"{term} {group.get('year')}".strip()


class GmailMailService:
    def __init__(self, rosters, google, teaching_assistants=None, photos=None):
        self.rosters, self.path, self.google, self.tas, self.photos = rosters, rosters.path, google, teaching_assistants, photos
        self._index = (0.0, {}, [])
        self.client = GmailMailClient(google)
        self.files = None  # app_files.AppFiles: the app's own file routes, set when the routes are registered (api.register)
        self.canvas = None  # canvas.service.CanvasService: replies posted in a Canvas conversation (MAIL-CANVAS-POST), set by app.py
        # student_support.service.StudentSupportService: a thread a follow-up has settled leaves Students (MAIL-FOLLOW-UP), set by app.py
        self.followups = None
        self._send_lock = threading.Lock()
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "gmail_mail", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Connection --------------------------------------------------------------------------------------------------------
    def ready(self):
        """Faculty's Google connection exists and may read mail (sending needs only the connection Response Cards made)."""
        return self.google.configured and self.google.granted(MAIL_SCOPES)

    def status(self):
        return {"configured": self.google.configured, "connected": self.google.connected, "email": self.google.email or None,
                "permission": self.ready(), "can_trash": self.can_trash(), "project": self.google.project}

    def can_trash(self):
        """The wider grant (gmail.modify) that both Move to Trash and Archive need."""
        return self.google.configured and self.google.granted(MAIL_MANAGE_SCOPES)

    def connect(self, data):
        data = data if isinstance(data, dict) else {}
        if not self.google.configured:
            raise ValueError("Google is not set up on this server: Faculty's Google client file (FACULTY_GMAIL_CLIENT_FILE and "
                             "FACULTY_GMAIL_TOKEN_FILE) is missing.")
        # `manage` asks for the wider grant Archive and Move to Trash need, only when the instructor chooses to allow it.
        return {"url": self.google.start(self.google.email or "", purpose="mail-manage" if data.get("manage") is True else "mail")}

    def _require_read(self):
        if not self.ready():
            raise ValueError("Connect Gmail for reading first: choose Connect Gmail on the Mail page.")

    # -- Reading -----------------------------------------------------------------------------------------------------------
    def threads(self, tab="students", query="", page_token="", step=False):
        """One listing of a mailbox. Students scans up to STUDENT_SCAN of Gmail's pages for STUDENT_WANT student threads; with `step`
        it reads ONE page and names those limits (`scan`), so the page can show each page's students as they arrive and ask for
        the next page itself, ending where this scan would have ended."""
        self._require_read()
        if tab not in TABS:
            raise ValueError("Choose Students, Sent or All mail.")
        base = TABS[tab]
        if tab == "students":
            index, people = self._students()
            # Only mail from the roster's own mail domains and from Canvas can be a student's; the exact sender is checked below.
            domains = sorted({d for address in index for d in (a.rpartition("@")[2] for a in variants(address))} | set(CANVAS_DOMAINS))
            sources = ["@" + d for d in domains if d not in CANVAS_DOMAINS]
            # Canvas mails everything (grades, announcements); only its messages can be a student's.
            base = "in:inbox (" + " OR ".join([f"from:({' OR '.join(sources)})"] * bool(sources) + ['(from:@instructure.com subject:"sent you a message in Canvas")']) + ")"
        search = " ".join(part for part in (base, _query(query)) if part)
        if page_token and (not isinstance(page_token, str) or len(page_token) > 500 or not page_token.replace("-", "").replace("_", "").isalnum()):
            raise ValueError("That page of results is not valid.")
        me = self.google.email
        if tab != "students":
            found, more = self.client.threads(search, page_token)
            return {"threads": [mail.summary(item, me) for item in found], "next": more, "tab": tab, "query": _query(query)}
        rows, more = [], page_token
        answers = self._canvas_answers()
        for scanned in range(1 if step else STUDENT_SCAN):
            try:
                found, more = self.client.threads(search, more)
            except MailError as error:
                # Gmail rate-limited a long scan: what was already found is shown (the threads read are remembered, so a retry goes further).
                if rows and "slow down" in str(error):
                    break
                raise
            page = []
            for item in found:
                entry, via, last = self._thread_student(item, me, index, people)
                if entry and _answered_in_canvas(item, answers):
                    continue  # answered in Canvas from Faculty after its latest message: like a reply in the thread (MAIL-CANVAS-POST)
                if entry:
                    page.append((item, entry, via, str(last.get("id") or "")))
            # MAIL-FOLLOW-UP: a thread whose latest message is part of a follow-up that no longer waits for the instructor's reply
            # leaves Students as an answered one does. One catalog read a Gmail page, and only when it holds students' threads.
            settled = self.followups.settled_mail([(entry.get("enrollment_id"), last) for _, entry, _, last in page]) if self.followups and page else ()
            rows += [{**mail.summary(item, me), "student": self._row_student(entry, via)} for item, entry, via, last in page if last not in settled]
            if not more or len(rows) >= STUDENT_WANT:
                break
        return {"threads": rows, "next": more, "tab": tab, "query": _query(query),
                **({"scan": {"want": STUDENT_WANT, "pages": STUDENT_SCAN}} if step else {})}

    def _thread_student(self, thread, me, index, people):
        """(roster entry, "Canvas" or "", that message) of the one current student a thread's LAST message is from (their roster
        address, or a Canvas message naming them), else (None, "", None).
        A thread whose last message is the instructor's (or anyone else's) is not the student's to answer, so it is only in All mail.
        Drafts are not messages sent. Nothing is guessed: an address two students share, or a Canvas name that fits none or several, names nobody."""
        messages = [m for m in thread.get("messages") or [] if isinstance(m, dict) and "DRAFT" not in (m.get("labelIds") or [])]
        if not messages:
            return None, "", None
        last = messages[-1]
        subject = mail.header((messages[0].get("payload")), "Subject")
        for person in mail.people(mail.header(last.get("payload"), "From")):
            address = person["email"].casefold()
            if not address or (me and address == me.casefold()):
                continue
            hits = index.get(variants(address)[0], [])
            if len(hits) == 1:
                return hits[0], "", last
            if address.rpartition("@")[2] in CANVAS_DOMAINS:
                found = canvas_student(person["name"], subject, people)
                if found:
                    return found, "Canvas", last
        return None, "", None

    @staticmethod
    def _row_student(entry, via=""):
        return {"name": entry["name"], "section": entry["section"], "code": entry["code"], "dropped": entry["dropped"], **({"via": via} if via else {})}

    def attachment(self, message_id, part_id):
        """(file name, media type, bytes) of a file attached to a message, fetched from Gmail when the instructor opens it (the
        Mail page's thread, through the app's file viewer). Read only; nothing is kept (MAIL-ATTACHMENTS-READ)."""
        self._require_read()
        return self.client.attachment(message_id, part_id)

    def thread(self, thread_id):
        self._require_read()
        result = mail.read_thread(self.client.thread(thread_id), self.google.email)
        # A message written from a current student's roster address names that student (and their photo, when there is one).
        index, people = self._students()
        for message in result["messages"]:
            if message["mine"]:
                continue
            for person in message["from"]:
                hits = index.get(variants(person["email"])[0], []) if person["email"] else []
                if len(hits) == 1:
                    message["student"] = hits[0]
                    break
                # A message Canvas sent on a student's behalf: named by its display name and the section in the subject.
                if person["email"].casefold().rpartition("@")[2] in CANVAS_DOMAINS:
                    # MAIL-CANVAS-REPLY: answered at the student's roster address, never through Canvas; nobody when not one student.
                    found, address = self._canvas_sender(person["name"], result["subject"])
                    message.pop("reply_to", None)
                    if address:
                        message["reply_to"] = [{"name": found["name"], "email": address}]
                    if found:
                        message["student"] = {**found, "via": "Canvas"}
                        break
        # Replies posted in Canvas from this thread (MAIL-CANVAS-POST) are shown in it as the instructor's, in time order.
        posts = self._canvas_answers().get(thread_id, [])
        if posts:
            result["messages"] = sorted(result["messages"] + [
                {"id": "canvas-" + post["send_id"], "from": [{"name": "You, in Canvas", "email": ""}], "to": [{"name": post["recipient_name"], "email": ""}],
                 "cc": [], "at": _instant(post["created_at"]), "text": post["body_text"], "files": [], "mine": True, "unread": False,
                 "canvas": {"course": post["course_name"]}} for post in posts], key=lambda m: m["at"] or "")
        # A reply to a Canvas message goes to the student by email, under the subject they gave it in Canvas.
        last = next((m for m in reversed(result["messages"]) if not m["mine"]), None)
        if last and (last.get("student") or {}).get("via") == "Canvas" and mail.canvas_subject(last["text"]):
            result["reply_subject"] = "Re: " + mail.canvas_subject(last["text"])
        return result

    def _canvas_sender(self, name, subject):
        """(the one student a Canvas notification is from, their roster address) or (None, ''); the address is '' unless it names
        only that student (MAIL-CANVAS-REPLY: nothing is guessed)."""
        index, people = self._students()
        found = canvas_student(name, subject, people)
        if not found:
            return None, ""
        address = next((p["address"] for p in people if p["entry"] is found), "")
        return found, (address if address and len(index.get(variants(address)[0], [])) == 1 else "")

    INDEX_TTL = 120

    def _students(self, fresh=False):
        """{address (casefolded): [student]} over every current section's roster, for naming the sender of a message. An address that
        two students share names nobody. Process memory only, rebuilt every INDEX_TTL seconds; a roster that cannot be read is skipped."""
        at, index, people = self._index
        if not fresh and index and time.monotonic() - at < self.INDEX_TTL:
            return index, people
        index, people = {}, []
        for section in self.sections():
            try:
                roster = self._roster(section["group_id"], section["number"])
            except Exception:  # noqa: BLE001 - one unreadable roster must not stop the message from opening
                continue
            photos = self.photos.latest(roster["term_id"]) if self.photos and roster.get("term_id") else {}
            for student in roster["students"]:
                # The term and section IDs let the student's name open their gradebook record (the card the section's gradebook shows).
                entry = {"name": _field(student, "name") or "Student", "section": section["number"], "code": section["code"],
                         "enrollment_id": student.get("id"), "term_id": roster.get("term_id"), "section_id": roster.get("section_id"),
                         "dropped": bool(student.get("dropped"))}
                if student.get("id") in photos:
                    entry["photo"] = photos[student["id"]]
                # Every student is in the name index (a Canvas message names its sender by name), whatever their roster address.
                address = roster_email(student)
                address = address if address and valid_address(address) else ""
                people.append({"words": set(name_key(entry["name"]).split()), "code": section["code"], "section": section["number"], "entry": entry,
                               "address": address})
                if address:
                    index.setdefault(variants(address)[0], []).append(entry)
        self._index = (time.monotonic(), index, people)
        return index, people

    # Each change Faculty may make to the mailbox: (record table, action, undo action, confirmation the server checks or None, what failed).
    # Both need the wider grant (MAIL_MANAGE_SCOPES). Archive is Gmail's one-click action (the mail stays in All mail), so it asks
    # for no confirmation; Trash does.
    CHANGES = {"trash": ("gmail_mail_trash", "trash", "untrash", "trash", "Gmail did not move the conversation."),
               "archive": ("gmail_mail_archive", "archive", "unarchive", None, "Gmail did not archive the conversation.")}

    def trash(self, thread_id, data, undo=False, actor="workspace"):
        """Moves a thread to Trash (`confirmation: "trash"`, checked here) or back (Undo). Recoverable; a request ID already recorded
        returns its record and calls nothing. Records the thread ID and what Google answered, never any of the mail."""
        return self._change("trash", thread_id, data, undo, actor)

    def archive(self, thread_id, data, undo=False, actor="workspace"):
        """Archives a thread (it leaves the Inbox and stays in All mail) or puts it back in the Inbox (Undo, Move to Inbox). A request ID
        already recorded returns its record and calls nothing. Records the thread ID and what Google answered, never any of the mail."""
        return self._change("archive", thread_id, data, undo, actor)

    def _change(self, kind, thread_id, data, undo, actor):
        table, forward, back, confirmation, failed = self.CHANGES[kind]
        data = data if isinstance(data, dict) else {}
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > REQUEST_ID_MAX or not request_id.replace("-", "").replace("_", "").replace(":", "").replace(".", "").isalnum():
            raise ValueError("Send a request ID.")
        if not valid_id(thread_id):
            raise ValueError("That is not a Gmail thread.")
        if confirmation and not undo and data.get("confirmation") != confirmation:
            raise ValueError("Confirm moving the conversation to Trash.")
        if not self.can_trash():
            raise ValueError("Allow Faculty to archive mail and move it to Trash first: choose Allow on the Mail page.")
        action = back if undo else forward
        with self._send_lock:
            with connect(self.path) as db:
                earlier = db.execute(f"SELECT * FROM {table} WHERE request_id=?", (request_id,)).fetchone()
                if earlier:
                    if (earlier["thread_id"], earlier["action"]) != (thread_id, action):
                        raise Conflict("This request was already used for another conversation. Close the window and try again.")
                    if earlier["status"] == "failed":
                        raise MailError(earlier["detail"] or failed)
                    return {k: earlier[k] for k in ("id", "thread_id", "action", "status", "detail")}
            status, detail = "done", ""
            try:
                getattr(self.client, kind)(thread_id, undo)
            except MailError as error:
                status, detail = "failed", str(error)[:400]
            record = {"id": str(uuid.uuid4()), "thread_id": thread_id, "action": action, "status": status, "detail": detail}
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(f"INSERT INTO {table}(id,request_id,thread_id,action,status,detail,actor,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (record["id"], request_id, thread_id, action, status, detail, actor, timestamp()))
                db.commit()
            if status == "failed":
                raise MailError(detail or failed)
            return record

    # -- Sections, their audiences and a student's mail ---------------------------------------------------------------------
    def sections(self):
        """The current sections the compose window can address: [{group_id, number, code, term_label, students}]. Catalog only."""
        with connect(self.path) as db:
            courses = live_offerings(db)
        summary = self.rosters.summary()["groups"]
        found = []
        for group in group_courses(courses):
            entry = summary.get(group["id"])
            if not entry or not group.get("is_current"):
                continue
            for number in group.get("current_sections") or []:
                section = entry["sections"].get(str(number))
                if section:
                    found.append({"group_id": group["id"], "number": number, "code": group["code"], "term_label": _label(group),
                                  "students": section["students"], "section_id": section["section_id"]})
        return sorted(found, key=lambda item: (item["code"], str(item["number"])))

    def _roster(self, group_id, number):
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise ValueError("Name the section.") from None
        return self.rosters.get(group_id, number)

    def audience(self, group_id, number):
        """Who a section's message can go to: its current students (name and address only, no identifiers) and its TAs. A student
        with no usable address, or sharing one, is listed under `skipped` with the reason; nobody is guessed."""
        roster = self._roster(group_id, number)
        students, skipped, seen = [], [], {}
        for student in roster["students"]:
            if student.get("dropped"):
                continue
            name = _field(student, "name") or " ".join(filter(None, [_field(student, "first name"), _field(student, "last name")])) or "Student"
            address = roster_email(student)
            if not address:
                skipped.append({"name": name, "reason": "no email address on the roster"})
            elif not valid_address(address):
                skipped.append({"name": name, "reason": "the roster address is not a valid email address"})
            elif address.casefold() in seen:
                skipped.append({"name": name, "reason": f"shares an address with {seen[address.casefold()]}"})
            else:
                seen[address.casefold()] = name
                students.append({"name": name, "email": address})
        students.sort(key=lambda item: item["name"].casefold())
        tas = []
        if self.tas:
            for contact in self.tas.section(roster["section_id"]).get("contacts") or []:
                if contact.get("email") and valid_address(contact["email"]):
                    tas.append({"name": contact["name"], "email": contact["email"]})
        return {"section_id": roster["section_id"], "section": roster["section"], "students": students, "skipped": skipped, "tas": tas}

    def student(self, group_id, number, student_id):
        """A student's threads with the instructor, found by their roster address (from, to or cc). Nothing is stored."""
        self._require_read()
        roster = self._roster(group_id, number)
        record = next((s for s in roster["students"] if s.get("id") == student_id), None)
        if not record:
            raise NotFound("That student is not on this section's roster.")
        name = _field(record, "name") or "Student"
        address = roster_email(record)
        if not address or not valid_address(address):
            return {"name": name, "email": "", "threads": [], "reason": "This student has no usable email address on the roster."}
        wanted = " OR ".join(f"from:{a} OR to:{a} OR cc:{a}" for a in variants(address))
        # Canvas notifications carry the student's name in the subject ("First Last (26 FALL MATH 140 514: …) just sent you a message").
        family, _, given = name.partition(",")
        words = (name_key(family).split() + name_key(given).split()[:1]) if given else name_key(name).split()[:1] + name_key(name).split()[-1:]
        if len(set(words)) >= 2:
            wanted += f" OR (from:{CANVAS_DOMAINS[0]} subject:({' '.join(words)}))"
        found, _ = self.client.threads(f"({wanted})", "", 15)
        return {"name": name, "email": address, "threads": [mail.summary(item, self.google.email) for item in found], "reason": ""}

    # -- Composing and sending ---------------------------------------------------------------------------------------------
    def _plan(self, data, uploads=()):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        if not self.google.connected:
            raise ValueError("Connect Gmail first: choose Connect Gmail on the Mail page.")
        reply = None
        thread_id = data.get("reply_thread_id") or ""
        if thread_id:
            self._require_read()
            if not valid_id(thread_id):
                raise ValueError("That is not a Gmail thread.")
            context = mail.reply_context(self.client.thread(thread_id), self.google.email)
            canvas = next((p for p in context["sender"] if canvas_address(p["email"])), None)
            student = {}
            if canvas:
                # MAIL-CANVAS-REPLY (requirement, 2026-10-02): a Canvas message is answered by email at the student's roster address.
                # Canvas ignored the replies Faculty sent to its reply+ addresses, so no Canvas address is ever written to here.
                found, address = self._canvas_sender(canvas["name"], context["subject"])
                listed = mail.addresses(data.get("to"), "To") + mail.addresses(data.get("cc"), "Cc") + mail.addresses(data.get("bcc"), "Bcc")
                if not address and (not listed or any(canvas_address(a) for a in listed)):
                    raise ValueError("Faculty can't tell which of your students sent this Canvas message (or they have no roster email), "
                                     "so it can't address the reply. Write the student's email address in To.")
                via = data.get("via", "canvas")
                if via not in ("canvas", "email"):
                    raise ValueError("Choose how the reply goes: canvas or email.")
                # MAIL-CANVAS-POST (requirement, 2026-10-02): posted in the Canvas conversation itself whenever it can be.
                post, why = self._canvas_target(context, canvas, listed, address, data, uploads) if via == "canvas" else (None, "")
                if post:
                    reply = {**context, "thread_id": thread_id, "redirected": [], "student": {}, "post": post}
                    return mail.canvas_plan(data, self.google.email, thread_id, post), reply, []
                if address:
                    student = {"name": found["name"], "email": address, "not_canvas": why}
                context = {**context, "to": [address] if address else [],
                           "redirect": {a.casefold(): [address] for a in listed if address and canvas_address(a)}}
            if not any(data.get(field) for field in ("to", "cc", "bcc")):
                data = {**data, "to": context["to"]}
            data, redirected = mail.redirect_reply(data, context)
            reply = {**context, "thread_id": thread_id, "redirected": [] if canvas else redirected, "student": student}
        # Files are named by the Faculty routes that serve them and read here, never taken from the request (MAIL-ATTACHMENTS).
        listed, contents = attachments.collect(self.files, data.get("attachments"), uploads)
        return mail.plan(data, self.google.email, reply, listed), reply, contents

    def _canvas_target(self, context, sender, listed, address, data, uploads):
        """(where a reply to a Canvas message is posted in Canvas, '') or (None, why it goes by email instead). The conversation is the
        one the notification links to, read now; the reply answers its latest message by someone else, whose author must be the
        student the notification names (every word of Canvas's display name in their Canvas name). Reads Canvas; writes nothing."""
        if data.get("attachments") or uploads:
            return None, "Faculty does not post files in Canvas."
        if any(not canvas_address(a) and a.casefold() != (address or "").casefold() for a in listed) or \
                mail.addresses(data.get("cc"), "Cc") or mail.addresses(data.get("bcc"), "Bcc"):
            return None, "it is addressed to more people than the student."
        if not self.canvas or not self.canvas.settings.configured:
            return None, "Canvas is not set up for this Faculty."
        conversation = mail.canvas_conversation(context.get("text"), self.canvas.settings.host)
        if not conversation:
            return None, "the notification does not link to a Canvas conversation."
        if not self.canvas.settings.status()["signed_in"]:
            return None, CANVAS_SIGNED_OUT
        try:
            with self.canvas.client() as client:
                me, found = client.me(), client.conversation(conversation)
        except canvas_web.SignedOut:
            return None, CANVAS_SIGNED_OUT
        except canvas_web.CanvasError as error:
            return None, f"Canvas could not be read ({error})."
        answered = next((m for m in found["messages"] if m["author_id"] and m["author_id"] != me["id"] and not m["generated"]), None)
        if not answered:  # Canvas lists a conversation's messages newest first
            return None, "the Canvas conversation has no message from the student."
        name = found["participants"].get(answered["author_id"], "")
        if not name or not set(name_key(sender["name"]).split()) <= set(name_key(name).split()):
            return None, "the Canvas conversation's latest message is not from the student this notification names."
        return {"conversation_id": found["id"], "recipient_id": answered["author_id"], "recipient_name": name,
                "answered_id": answered["id"], "course": found["context"]}, ""

    def preview(self, data, uploads=()):
        """The exact message that would be sent, with its fingerprint. Sends nothing and writes nothing. `uploads` are the files the
        compose window sent from the instructor's computer: (filename, media type, bytes)."""
        message, reply, _ = self._plan(data, uploads)
        if message.get("via") == "canvas":
            post = message["canvas"]
            where = f" ({post['course']})" if post["course"] else ""
            notes = [f"It is posted in Canvas, in your conversation with {post['recipient_name']}{where}, as a reply from you to them only, "
                     "as Canvas's own Reply sends it. Canvas notifies them as it does for any message.",
                     "Nothing is sent from Gmail; the Gmail thread stays as it is."]
            return {"message": message, "fingerprint": mail.fingerprint(message), "recipients": 1, "notes": notes,
                    "reply_to_thread": True, "sender": message["sender"]}
        total = len(message["to"]) + len(message["cc"]) + len(message["bcc"])
        notes = []
        if message["bcc"] and not message["to"]:
            notes.append("It is addressed to you; everyone else is on Bcc, so nobody sees who else received it.")
        if message["thread_id"]:
            notes.append("It is sent as a reply in the same Gmail thread.")
        for address, replies in (reply or {}).get("redirected") or []:
            notes.append(f"{address} asks for replies at {', '.join(replies)}, so it is addressed there instead.")
        if (reply or {}).get("student"):
            why = reply["student"].get("not_canvas")
            notes.append(f"This answers a Canvas message: it goes to {reply['student']['name']} by email ({reply['student']['email']}), "
                         + (f"not in Canvas, because {why}" if why else "not in Canvas."))
        if message.get("attachments"):
            count, size = len(message["attachments"]), sum(item["size"] for item in message["attachments"])
            amount = f"{size / 1e6:.1f} MB" if size >= 100_000 else f"{max(1, round(size / 1000))} KB"
            notes.append(f"It carries {count} attachment{'' if count == 1 else 's'} ({amount}). Each is the file as it is now; if one changes "
                         "before the send, the review is stale.")
        # Signed out of Canvas: the review offers Sign in to Canvas, then reviews again (MAIL-CANVAS-POST); said only then.
        signed_out = ((reply or {}).get("student") or {}).get("not_canvas") == CANVAS_SIGNED_OUT
        return {"message": message, "fingerprint": mail.fingerprint(message), "recipients": total, "notes": notes,
                "reply_to_thread": bool(reply), "sender": message["sender"], **({"canvas_sign_in": True} if signed_out else {})}

    def roster_people(self):
        """Every current section's students, {name, enrollment_id, term_id, section, section_id, address} (JEV SEND-CHECKS)."""
        _, people = self._students()
        return [{**person["entry"], "address": person["address"]} for person in people]

    def check(self, data, uploads=(), jev=None):
        """JEV SEND-CHECKS: advice on the message the review shows, beside it; never part of its fingerprint or of Send. The
        student's message being answered is the one read from Gmail here (`reply_context`), never text the page sends."""
        from jev import send_checks
        message, reply, _ = self._plan(data, uploads)
        people = self.roster_people()
        if message.get("via") == "canvas":
            wanted = set(name_key(message["canvas"]["recipient_name"]).split())
            readers = [p for p in people if wanted and set(name_key(p["name"]).split()) == wanted]
        else:
            addresses = {variants(a)[0] for a in message["to"] + message["cc"] + message["bcc"]}
            readers = [p for p in people if p["address"] and variants(p["address"])[0] in addresses]
        terms = {p["term_id"] for p in (readers or people) if p.get("term_id")}
        sections = {p["section"] for p in readers}
        enrollments = {p["enrollment_id"] for p in readers}
        with connect(jev.path if jev else self.path) as db:
            return send_checks.check(jev, db, owner="mail:" + mail.fingerprint(message), body=message.get("text") or "",
                                     source=(reply or {}).get("text") or "", attachments=len(message.get("attachments") or []),
                                     terms=terms, sections=sections, people=people, recipients=enrollments,
                                     enrollment=next(iter(enrollments)) if len(enrollments) == 1 else None)

    @staticmethod
    def _record(db, row):
        outcome = db.execute("SELECT * FROM gmail_mail_outcomes WHERE send_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
        status = outcome["status"] if outcome else "uncertain"
        # The files it carried, as references (a send without any keeps the record it always had).
        files = sorted([(f["position"], {"route": f["route"], "name": f["name"], "type": f["media_type"], "size": f["size"], "sha256": f["sha256"]})
                        for f in db.execute("SELECT * FROM gmail_mail_send_attachments WHERE send_id=?", (row["id"],))]
                       + [(f["position"], {"name": f["name"], "type": f["media_type"], "size": f["size"], "sha256": f["sha256"], "from": "computer"})
                          for f in db.execute("SELECT * FROM gmail_mail_send_uploads WHERE send_id=?", (row["id"],))], key=lambda item: item[0])
        files = [file for _, file in files]
        posted = GmailMailService._posted(db, row["id"])
        interrupted = ("This reply was interrupted; look in the Canvas conversation before writing again. Faculty never posts it again."
                       if posted else "This send was interrupted; check Gmail's Sent folder. Faculty never sends it again.")
        return {"id": row["id"], "request_id": row["request_id"], "subject": row["subject"], "sender": row["sender"],
                "to": json.loads(row["to_json"]), "cc": json.loads(row["cc_json"]), "bcc": json.loads(row["bcc_json"]),
                "recipients": len(json.loads(row["to_json"])) + len(json.loads(row["cc_json"])) + len(json.loads(row["bcc_json"])),
                "sent_at": row["created_at"], "status": status, "reply": bool(row["reply_thread_id"]),
                "thread_id": (outcome["thread_id"] if outcome else "") or row["reply_thread_id"],
                "detail": outcome["detail"] if outcome else interrupted,
                "message_id": outcome["message_id"] if outcome else "",
                **({"attachments": files} if files else {}), **posted}

    @staticmethod
    def _posted(db, send_id):
        """Where a reply posted in Canvas went (MAIL-CANVAS-POST); {} for mail (whose record is as it always was)."""
        post = db.execute("SELECT * FROM gmail_mail_canvas_posts WHERE send_id=?", (send_id,)).fetchone()
        if not post:
            return {}
        return {"via": "canvas", "recipients": 1, "canvas": {"conversation_id": post["conversation_id"], "recipient_name": post["recipient_name"],
                                                             "course": post["course_name"]}}

    def send(self, data, actor="workspace", uploads=()):
        """Sends the previewed message. Needs its `fingerprint`, a `request_id` and `confirmation: "send"`, all checked here, and the
        same files from the instructor's computer that the preview had (`uploads`)."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > REQUEST_ID_MAX or not request_id.replace("-", "").replace("_", "").replace(":", "").replace(".", "").isalnum():
            raise ValueError("Send a request ID.")
        if data.get("confirmation") != "send":
            raise ValueError("Confirm the send: the message is only sent when you choose Send.")
        with self._send_lock:
            with connect(self.path) as db:
                earlier = db.execute("SELECT * FROM gmail_mail_sends WHERE request_id=?", (request_id,)).fetchone()
                if earlier:
                    if earlier["request_hash"] != data.get("fingerprint"):
                        raise Conflict("This send was already made with a different message. Close the window and write it again.")
                    return self._record(db, earlier)  # Never sent twice, whatever came of the first try.
            message, _, contents = self._plan(data, uploads)
            fingerprint = mail.fingerprint(message)
            if data.get("fingerprint") != fingerprint:
                raise Conflict("The message changed since you reviewed it (or the thread has a newer message). Review it again.")
            if message.get("via") == "canvas":
                return self._post(request_id, fingerprint, message, actor)
            raw = mail.compose(message, SENDER_NAME, contents)  # built before anything is recorded or leaves
            send_id = str(uuid.uuid4())
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO gmail_mail_sends(id,request_id,request_hash,sender,to_json,cc_json,bcc_json,subject,body_text,reply_thread_id,actor,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (send_id, request_id, fingerprint, message["sender"], json.dumps(message["to"]), json.dumps(message["cc"]),
                            json.dumps(message["bcc"]), message["subject"], message["text"], message["thread_id"], actor, timestamp()))
                files = list(enumerate(message.get("attachments") or []))
                db.executemany("INSERT INTO gmail_mail_send_attachments(send_id,position,route,name,media_type,size,sha256) VALUES (?,?,?,?,?,?,?)",
                               [(send_id, n, item["route"], item["name"], item["type"], item["size"], item["sha256"]) for n, item in files if "route" in item])
                db.executemany("INSERT INTO gmail_mail_send_uploads(send_id,position,name,media_type,size,sha256) VALUES (?,?,?,?,?,?)",
                               [(send_id, n, item["name"], item["type"], item["size"], item["sha256"]) for n, item in files if "route" not in item])
                db.commit()
            outcome = ("uncertain", "", "", "Delivery could not be confirmed.")
            try:
                answer = self.client.send(raw, message["thread_id"])
                outcome = ("accepted", str(answer["id"]), str(answer.get("threadId") or ""), "")
            except MailError as error:
                outcome = ("uncertain" if error.uncertain else "failed", "", "", str(error)[:400])
            except Exception as error:  # noqa: BLE001 - the record must say what happened, whatever it was
                outcome = ("uncertain", "", "", f"Delivery could not be confirmed ({type(error).__name__}).")
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO gmail_mail_outcomes(send_id,status,message_id,thread_id,detail,created_at) VALUES (?,?,?,?,?,?)",
                           (send_id, *outcome, timestamp()))
                db.commit()
                return self._record(db, db.execute("SELECT * FROM gmail_mail_sends WHERE id=?", (send_id,)).fetchone())

    def _post(self, request_id, fingerprint, message, actor):
        """A reviewed reply posted in its Canvas conversation (MAIL-CANVAS-POST): the intent and where it goes are committed before
        Canvas is asked, Canvas's answer after. Canvas refusing it (4xx, or the session) is `failed`: nothing was posted. No answer, or
        a server error, is `uncertain`, and it is never posted again by itself."""
        post, send_id = message["canvas"], str(uuid.uuid4())
        writes, key = self.canvas.writes, f"post:{send_id}"
        # CANVAS "A STOP WAITS": clear the registration even if recording the intent fails.
        writes.begin(key, "a reply posted in Canvas", "Faculty is restarting. Post it again in a minute; nothing was posted.")
        try:
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                now = timestamp()
                db.execute("INSERT INTO gmail_mail_sends(id,request_id,request_hash,sender,to_json,cc_json,bcc_json,subject,body_text,reply_thread_id,actor,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (send_id, request_id, fingerprint, message["sender"], "[]", "[]", "[]", message["subject"], message["text"],
                            message["thread_id"], actor, now))
                db.execute("INSERT INTO gmail_mail_canvas_posts(send_id,conversation_id,recipient_user_id,recipient_name,answered_message_id,course_name,created_at) "
                           "VALUES (?,?,?,?,?,?,?)", (send_id, post["conversation_id"], post["recipient_id"], post["recipient_name"],
                                                      post["answered_id"], post["course"], now))
                db.commit()
            return self._posting(send_id, post, message)
        finally:
            writes.end(key)

    def _posting(self, send_id, post, message):
        """Asks Canvas to post the recorded reply and records its answer (see _post)."""
        try:
            with self.canvas.client() as client:
                client.add_message(post["conversation_id"], message["text"], [post["recipient_id"]])
            outcome = ("accepted", "")
        except canvas_web.SignedOut:
            outcome = ("failed", "Canvas asked you to sign in again (on the Canvas page); nothing was posted.")
        except canvas_web.CanvasError as error:
            refused = error.status is not None and 400 <= error.status < 500
            outcome = ("failed" if refused else "uncertain",
                       str(error)[:400] if refused else f"{str(error)[:300]} Canvas may or may not have posted it; look in the conversation before writing again.")
        except Exception as error:  # noqa: BLE001 - the record must say what happened, whatever it was
            outcome = ("uncertain", f"Canvas did not confirm the post ({type(error).__name__}); look in the conversation before writing again.")
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO gmail_mail_outcomes(send_id,status,message_id,thread_id,detail,created_at) VALUES (?,?,?,?,?,?)",
                       (send_id, outcome[0], "", post["conversation_id"], outcome[1], timestamp()))
            db.commit()
            return self._record(db, db.execute("SELECT * FROM gmail_mail_sends WHERE id=?", (send_id,)).fetchone())

    def _canvas_answers(self):
        """{Gmail thread ID: [(when, post)]} of the replies posted in Canvas from those threads and accepted (MAIL-CANVAS-POST)."""
        found = {}
        with connect(self.path) as db:
            for row in db.execute("""SELECT s.reply_thread_id, s.body_text, s.created_at, p.recipient_name, p.course_name, p.send_id
                                     FROM gmail_mail_canvas_posts p JOIN gmail_mail_sends s ON s.id=p.send_id
                                     WHERE (SELECT status FROM gmail_mail_outcomes o WHERE o.send_id=s.id ORDER BY o.id DESC LIMIT 1)='accepted'"""):
                found.setdefault(row["reply_thread_id"], []).append(dict(row))
        return found

    def by_request(self, request_id):
        """The record of the send made under this request ID, or None (MAIL-SEND-LATER). Catalog only."""
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM gmail_mail_sends WHERE request_id=?", (str(request_id),)).fetchone()
            return self._record(db, row) if row else None

    def sent(self, limit=SENT_LIMIT):
        """What Faculty sent, newest first, with each outcome. Catalog only."""
        with connect(self.path) as db:
            rows = db.execute("SELECT * FROM gmail_mail_sends ORDER BY created_at DESC, rowid DESC LIMIT ?", (int(limit),)).fetchall()
            return {"sent": [self._record(db, row) for row in rows]}
