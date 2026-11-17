"""Capturing Gmail for the communications record (AGENTS.md "Communications record").

Every round searches the connected mailbox for mail from, to, cc or bcc any current student's roster address (both spellings TAMU
delivers to the same student: name@email.tamu.edu and name@tamu.edu), since the opening of each current term's window, and keeps
every message not yet captured: the whole message as Gmail holds it (`format=raw`, attachments included), and its text for the
timeline. Drafts and spam are left out; Trash is searched too, so a message deleted soon after it arrived is still kept. Mail Faculty
sent from the Mail page is also fetched by its own Gmail ID, since its Bcc'd students may not be searchable. Canvas's message
notification emails are kept too, as a backup of the Canvas inbox (`message_notice`, `notice_students`): they name their sender only
by name and section. Canvas's other email is not kept.

Nothing in Gmail is changed: only messages.list and messages.get (reads). Every call goes to Google only (gmail_mail.client).
"""
import base64
import email
import email.policy
import functools
import hashlib
import re
from email.utils import getaddresses

from gmail_mail.client import GmailMailClient, MailError, valid_id
from gmail_mail.message import clean_text, html_text, instant
from gmail_mail.service import CANVAS_DOMAINS, CANVAS_SECTION, variants
from google_calendar.client import http_request
from student_photos.service import name_key

RAW_LIMIT = 48 * 1024 * 1024   # Gmail's largest message (25 MB) as base64 inside JSON
CHUNK = 20                     # addresses per search: the query stays a few kilobytes
PAGES = 50                     # the most pages (500 IDs each) one search follows in a round
MAX_TEXT = 100000


class GmailReader(GmailMailClient):
    """The Mail page's Gmail client with room for raw messages. Reads only."""

    def __init__(self, connection):
        super().__init__(connection)
        self.http = functools.partial(http_request, limit=RAW_LIMIT)

    def ids(self, query, pages=PAGES):
        """Every message ID a search finds (drafts and spam excluded by the query; Trash included)."""
        found, token = [], ""
        for _ in range(pages):
            params = {"q": query, "maxResults": 500, "includeSpamTrash": "true", **({"pageToken": token} if token else {})}
            _, listing = self.call("GET", "/messages", params)
            found.extend(item["id"] for item in listing.get("messages") or [] if isinstance(item, dict) and valid_id(item.get("id")))
            token = str(listing.get("nextPageToken") or "")
            if not token:
                break
        return found

    def raw(self, message_id):
        """{id, thread_id, labels, at, raw bytes} of one message as Gmail holds it."""
        if not valid_id(message_id):
            raise ValueError("That is not a Gmail message.")
        _, payload = self.call("GET", f"/messages/{message_id}", {"format": "raw"}, timeout=120)
        data = payload.get("raw")
        if not isinstance(data, str) or not data:
            raise MailError("Gmail did not return the message.")
        try:
            content = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        except (ValueError, TypeError):
            raise MailError("Gmail returned a message Faculty could not read.") from None
        return {"id": message_id, "thread_id": str(payload.get("threadId") or ""), "labels": [str(x) for x in payload.get("labelIds") or []],
                "at": instant(payload.get("internalDate")), "raw": content}


def query(addresses, after):
    """A Gmail search for mail from, to, cc or bcc any of `addresses` since `after` (a UTC datetime)."""
    group = " OR ".join(addresses)
    fields = " OR ".join(f"{field}:({group})" for field in ("from", "to", "cc", "bcc"))
    return f"after:{int(after.timestamp())} -in:drafts -in:spam ({fields})"


def people(value):
    return [{"name": clean_text(name, 200).strip(), "email": address.strip()} for name, address in getaddresses([value or ""])
            if address and "@" in address][:200]


def _parts(message):
    """Every leaf part of a message in walk order, with whether it is an attachment."""
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        yield part, bool(part.get_filename()) or disposition == "attachment"


def parse(raw):
    """What the timeline shows of a raw message: headers, plain text (the text of the HTML when there is no plain text) and its
    attachments (name, type, size, checksum, by their place among the message's parts). Untrusted content, bounded and cleaned."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    header = lambda name: clean_text(str(message.get(name) or ""), 2000).replace("\n", " ").strip()
    plain, markup, attachments = [], [], []
    for index, (part, attached) in enumerate(_parts(message)):
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001 - a malformed part is listed without content
            payload = b""
        kind = part.get_content_type()
        if attached:
            attachments.append({"part": index, "name": clean_text(part.get_filename() or "attachment", 200), "type": kind[:100],
                                "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "kept": True})
        elif kind in ("text/plain", "text/html"):
            try:
                text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except LookupError:
                # COMMUNICATIONS: an unknown sender-supplied charset must not stop the capture round.
                text = payload.decode("utf-8", "replace")
            (plain if kind == "text/plain" else markup).append(text)
    text = "\n\n".join(plain).strip() or html_text("\n".join(markup))
    return {"from": people(header("From")), "to": people(header("To")), "cc": people(header("Cc")), "bcc": people(header("Bcc")),
            "subject": header("Subject"), "date": header("Date"), "message_id": header("Message-ID"),
            "text": clean_text(text, MAX_TEXT), "attachments": attachments[:100]}


def attachment(raw, part):
    """(name, content type, bytes) of the attachment at `part` of a kept raw message."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    for index, (found, attached) in enumerate(_parts(message)):
        if index == part and attached:
            return found.get_filename() or "attachment", found.get_content_type(), found.get_payload(decode=True) or b""
    raise LookupError("That attachment is not in the kept message.")


def canvas_notice(parsed):
    """Any email Canvas sent (grades, announcements, messages)."""
    return any(person["email"].casefold().rpartition("@")[2] in CANVAS_DOMAINS for person in parsed["from"])


NOTICE_SUBJECT = "sent you a message in Canvas"
NOTICE_REPLY = re.compile(r"^You can reply to this message in Canvas", re.I)
CONVERSATION = re.compile(r"https://[A-Za-z0-9.-]+/conversations/(\d{1,20})\b")
CANVAS_MESSAGE = re.compile(r"location=conversation_message_(\d{1,20})\b")


def notices_query(after):
    """A Gmail search for the emails Canvas sends when someone messages the instructor in Canvas."""
    return f'after:{int(after.timestamp())} -in:drafts -in:spam from:@instructure.com subject:"{NOTICE_SUBJECT}"'


def message_notice(parsed):
    """What a Canvas message notification email says, or None for any other email: the sender's name as Canvas wrote it, the course
    and section its subject names, the conversation's subject and link, and the message itself (the lines between Canvas's
    "Subject:" line and its "You can reply ..." line; the whole text when the email is not laid out that way). As seen on real
    notifications, 2026-09-30: Subject line, the message, the reply line with the conversation's link, attachment links naming the
    Canvas message, a footer."""
    if not canvas_notice(parsed) or NOTICE_SUBJECT.casefold() not in parsed["subject"].casefold():
        return None
    lines = parsed["text"].splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("Subject:")), None)
    end = next((i for i, line in enumerate(lines) if NOTICE_REPLY.match(line.strip())), None)
    subject = lines[start].strip().removeprefix("Subject:").strip() if start is not None else ""
    text = "\n".join(lines[start + 1:end]).strip() if start is not None and end is not None and end > start else parsed["text"]
    where = CANVAS_SECTION.search(parsed["subject"])
    conversation = CONVERSATION.search(parsed["text"])
    message = CANVAS_MESSAGE.search(parsed["text"])
    return {"name": (parsed["from"] or [{}])[0].get("name", ""), "code": where.group(1).replace(" ", "") if where else "",
            "section": where.group(2) if where else "", "subject": subject, "text": text,
            "conversation_id": conversation.group(1) if conversation else "", "canvas_message_id": message.group(1) if message else "",
            "url": conversation.group(0) if conversation else ""}


def notice_students(notice, people):
    """The students a Canvas notification may be from: every current student whose roster name holds every word of the sender's
    name (two words at least) and, when the subject names a course and section, who is in it. One student or none; several name
    nobody. `people` is [{words, places: {(code without spaces, section)}, entry}]."""
    words = set(name_key(notice["name"]).split())
    if len(words) < 2:
        return []
    found = {}
    for person in people:
        if words <= person["words"] and (not notice["section"] or (notice["code"], notice["section"]) in person["places"]):
            found.setdefault(person["entry"]["enrollment_id"], person["entry"])
    return list(found.values())


def links(parsed, index, me, faculty_bcc=()):
    """(found, author_role): found is [(entry, kind, matched_by)] for the students whose roster address is in the message (or in
    `faculty_bcc`, the Bcc addresses of Faculty's own send of it); kind is author, to, cc or bcc, matched_by is roster_address or
    faculty_send, and author_role is instructor, student or other. `index` maps an address's first spelling to the student entries
    it names ({enrollment_id, term_id, uin}); an address shared by two different students (other UINs) names nobody. The term
    window is not applied here (service._gmail_links does that).

    THE COORDINATOR'S MAIL: an entry marked `coordinator` is a coordinated term's coordinator (its enrollment_id is the term's
    owner key). It is found the same way and matched_by coordinator_address wherever its address stands; a message the
    coordinator wrote is no student's, so its author_role stays other."""
    found, role = [], "other"
    me = (me or "").casefold()

    def students(address):
        entries = index.get(variants(address)[0], []) if address else []
        return entries if len({entry["uin"] for entry in entries}) == 1 else []

    for kind, people_of in (("author", parsed["from"]), ("to", parsed["to"]), ("cc", parsed["cc"]), ("bcc", parsed["bcc"])):
        for person in people_of:
            address = person["email"].casefold()
            if me and address == me:
                if kind == "author":
                    role = "instructor"
                continue
            hits = students(address)
            if kind == "author" and hits and not hits[0].get("coordinator"):
                role = "student"
            found.extend((entry, kind, "coordinator_address" if entry.get("coordinator") else "roster_address") for entry in hits)
    for address in faculty_bcc:
        found.extend((entry, "bcc", "coordinator_address" if entry.get("coordinator") else "faculty_send")
                     for entry in students(address.casefold()))
    return found, role
