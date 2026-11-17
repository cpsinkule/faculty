"""The communications record in the catalog: recording a captured message, and one student's timeline (AGENTS.md "Communications
record").

`record` is called inside the capture's own catalog transaction. A message is identified by its channel and the channel's own ID; its
content is a revision, and a new revision is written only when the content changed (a Canvas comment edited, a Gradescope reply
added or edited). Links say which term enrollments a message concerns and how that was known; they are only ever added.

`timeline` is a READ of retained records: the captured messages linked to one term enrollment, and, by reference, Faculty's own
records of what it sent to that student (Response Card invitations and receipts, seat-chart email) and the student's Response Card
answers. A message Faculty sent through Gmail is both a Faculty record and a Gmail message: it is shown once, as the Faculty record,
with the Gmail copy as its original. A student's RECORDED MEETING is one more entry by reference (RECORDINGS "Student meetings"):
"Meeting", opening the recording at that student's part; never a file, never sent.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from canonical_json import encoded
from course_storage import timestamp
from gmail_mail.client import valid_id

CHANNELS = {"gmail": "Email", "canvas_message": "Canvas message", "canvas_comment": "Canvas comment",
            "gradescope_regrade": "Gradescope regrade", "faculty_mail": "Email from Faculty", "response_card": "Response Card",
            "meeting": "Meeting"}
ROLES = ("author", "to", "cc", "bcc", "about")
MAX_TEXT = 100000
LIMIT = 1000  # the most entries one timeline shows (a student's record is far smaller)


def content_hash(revision):
    """What makes two captures of a message the same content: everything shown, never when it was captured."""
    keep = {key: revision.get(key) for key in ("sent_at", "author_role", "author", "recipients", "subject", "body_text", "attachments")}
    return hashlib.sha256(encoded(keep).encode()).hexdigest()


def record(db, channel, external_id, revision, links, thread_key=""):
    """Record one captured message in the caller's transaction: {message_id, revision (the one now latest), new (whether a
    revision was written), linked (links added)}. `revision` holds sent_at, author_role, author, recipients, subject, body_text,
    attachments, context and optionally original_file_id; `links` is [(enrollment_id, term_id, role, matched_by)]."""
    now = timestamp()
    row = db.execute("SELECT id FROM comm_messages WHERE channel=? AND external_id=?", (channel, external_id)).fetchone()
    if row:
        message_id = row["id"]
    else:
        message_id = str(uuid.uuid4())
        db.execute("INSERT INTO comm_messages(id, channel, external_id, thread_key, captured_at) VALUES (?,?,?,?,?)",
                   (message_id, channel, external_id, thread_key[:300], now))
    digest = content_hash(revision)
    latest = db.execute("SELECT revision, content_sha256 FROM comm_revisions WHERE message_id=? ORDER BY revision DESC LIMIT 1",
                        (message_id,)).fetchone()
    new = not latest or latest["content_sha256"] != digest
    number = (latest["revision"] if latest else 0) + (1 if new else 0)
    if new:
        db.execute("""INSERT INTO comm_revisions(id, message_id, revision, sent_at, author_role, author_json, recipients_json, subject,
                          body_text, attachments_json, context_json, original_file_id, content_sha256, captured_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (str(uuid.uuid4()), message_id, number, revision["sent_at"], revision["author_role"], encoded(revision["author"]),
                    encoded(revision["recipients"]), (revision.get("subject") or "")[:1000], (revision.get("body_text") or "")[:MAX_TEXT],
                    encoded(revision.get("attachments") or []), encoded(revision.get("context") or {}), revision.get("original_file_id"),
                    digest, now))
    linked = link(db, message_id, links)
    return {"message_id": message_id, "revision": number, "new": new, "linked": linked}


def link(db, message_id, links):
    """Add the links a message does not have yet; returns how many were added."""
    added = 0
    for enrollment_id, term_id, role, matched_by in links:
        if role not in ROLES:
            raise ValueError("Unknown link role.")
        cursor = db.execute("""INSERT OR IGNORE INTO comm_links(id, message_id, enrollment_id, term_id, role, matched_by, created_at)
                               VALUES (?,?,?,?,?,?,?)""", (str(uuid.uuid4()), message_id, enrollment_id, term_id, role, matched_by, timestamp()))
        added += cursor.rowcount
    return added


# -- The timeline -----------------------------------------------------------------------------------------------------------------
def _gmail_ids(receipt):
    """The Gmail message ID a Faculty send's receipt names, when it went through Gmail (the relay's IDs are not Gmail's)."""
    try:
        found = json.loads(receipt or "{}")
    except ValueError:
        return set()
    if not isinstance(found, dict):
        return set()
    return {value for value in (found.get("message_id"), found.get("id")) if isinstance(value, str) and valid_id(value)}


def _attachments(items):
    """What the timeline says of each attachment. A file the student linked from Google Drive (LINKED DRIVE FILES) says so, and
    one not fetched says what it waits on; an attachment the message carried reads exactly as before."""
    return [{"index": index, **{key: item.get(key) for key in ("name", "type", "size", "kept", "reason") if key in item},
             **({"linked": "google_drive", **({"wait": item["wait"]} if item.get("wait") else {})} if item.get("drive_id") else {})}
            for index, item in enumerate(items)]


def _captured(db, enrollment_id):
    rows = db.execute("""
        SELECT m.id, m.channel, m.external_id, m.thread_key, r.revision, r.sent_at, r.author_role, r.author_json, r.recipients_json,
               r.subject, r.body_text, r.attachments_json, r.context_json, r.original_file_id, r.captured_at,
               (SELECT group_concat(role) FROM comm_links x WHERE x.message_id=m.id AND x.enrollment_id=?) roles,
               (SELECT f.body_text != r.body_text OR f.subject != r.subject FROM comm_revisions f WHERE f.message_id=m.id AND f.revision=1) changed
        FROM comm_messages m
        JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
        WHERE m.id IN (SELECT message_id FROM comm_links WHERE enrollment_id=?)
        ORDER BY r.sent_at DESC, m.id LIMIT ?""", (enrollment_id, enrollment_id, LIMIT)).fetchall()
    entries = []
    for row in rows:
        context = json.loads(row["context_json"])
        roles = sorted(set((row["roles"] or "").split(",")) - {""})
        entries.append({
            "key": f"message:{row['id']}", "message_id": row["id"], "channel": row["channel"],
            "channel_label": "Canvas message (email)" if context.get("canvas_notice") else CHANNELS[row["channel"]],
            "at": row["sent_at"], "author_role": row["author_role"], "author": json.loads(row["author_json"]),
            "recipients": json.loads(row["recipients_json"]), "subject": row["subject"], "text": row["body_text"],
            "attachments": _attachments(json.loads(row["attachments_json"])), "context": context, "roles": roles,
            # Edited means the words changed (a Canvas comment edited, a Gradescope reply rewritten), not an attachment kept later.
            "revision": row["revision"], "edited": bool(row["changed"]), "original": bool(row["original_file_id"]),
            "external_id": row["external_id"], "captured_at": row["captured_at"]})
    return entries


def _card_fields(snapshot):
    """{field id: label} of a card's frozen schema, leaving out the fields that verify a student's identity (their UIN)."""
    try:
        schema = json.loads(snapshot or "{}")
    except ValueError:
        return {}, set()
    fields = (schema.get("schema") or schema).get("fields") if isinstance(schema, dict) else None
    labels, hidden = {}, set()
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict) or not isinstance(field.get("id"), str):
            continue
        mode = (field.get("verification") or {}).get("mode") if isinstance(field.get("verification"), dict) else None
        if mode == "uin-digest" or field["id"] == "confirm_uin":
            hidden.add(field["id"])
        else:
            labels[field["id"]] = str(field.get("label") or field["id"])
    return labels, hidden


def _card_title(snapshot):
    try:
        schema = json.loads(snapshot or "{}")
    except ValueError:
        return ""
    return str((schema.get("schema") or schema).get("title") or "") if isinstance(schema, dict) else ""


def _faculty(db, enrollment_id):
    """Faculty's own records of mail it sent to the student and their Response Card answers, by reference. Also the Gmail IDs those
    sends went out under, so the Gmail copies are not shown twice."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    entries, gmail = [], {}
    if {"response_card_mail", "response_card_recipients"} <= tables:
        for row in db.execute("""SELECT m.id, m.kind, m.recipient, m.subject, m.text_body, m.status, m.provider, m.provider_receipt_json,
                                        m.created_at, m.updated_at, r.snapshot_json,
                                        (SELECT COUNT(DISTINCT x.enrollment_id) FROM response_card_recipients x WHERE x.issue_id=r.issue_id) issued
                                 FROM response_card_mail m JOIN response_card_recipients r ON r.id=m.card_id
                                 WHERE r.enrollment_id=? ORDER BY m.created_at DESC LIMIT ?""", (enrollment_id, LIMIT)):
            key = f"card-mail:{row['id']}"
            entries.append({"key": key, "channel": "faculty_mail", "channel_label": CHANNELS["faculty_mail"],
                            "at": row["updated_at"] if row["status"] == "accepted" else row["created_at"], "author_role": "instructor",
                            "author": {"name": "Faculty"}, "recipients": [{"email": row["recipient"], "kind": "to"}],
                            "subject": row["subject"], "text": row["text_body"][:MAX_TEXT], "attachments": [], "roles": ["to"],
                            "status": row["status"], "context": {"source": "Response Card " + ("invitation" if row["kind"] == "invitation" else "receipt"),
                                                                 "card": _card_title(row["snapshot_json"]), "provider": row["provider"] or "relay"},
                            "revision": 1, "edited": False, "original": False,
                            # An invitation goes to everyone the card was issued to; a receipt answers this student alone.
                            **({"mass": {"students": row["issued"]}} if row["kind"] == "invitation" and row["issued"] > MASS else {})})
            for gmail_id in _gmail_ids(row["provider_receipt_json"]):
                gmail[gmail_id] = key
    if "seating_student_mail" in tables:
        for row in db.execute("""SELECT id, recipient, subject, text_body, status, provider, provider_receipt_json, seat, created_at, updated_at,
                                        (SELECT COUNT(DISTINCT x.enrollment_id) FROM seating_student_mail x WHERE x.chart_id=s.chart_id) charted
                                 FROM seating_student_mail s WHERE enrollment_id=? ORDER BY created_at DESC LIMIT ?""", (enrollment_id, LIMIT)):
            key = f"seat-mail:{row['id']}"
            entries.append({"key": key, "channel": "faculty_mail", "channel_label": CHANNELS["faculty_mail"],
                            "at": row["updated_at"] if row["status"] == "accepted" else row["created_at"], "author_role": "instructor",
                            "author": {"name": "Faculty"}, "recipients": [{"email": row["recipient"], "kind": "to"}],
                            "subject": row["subject"], "text": row["text_body"][:MAX_TEXT],
                            "attachments": [{"index": 0, "name": "seating-chart.pdf", "type": "application/pdf", "kept": True}],
                            "roles": ["to"], "status": row["status"], "context": {"source": "Seat chart email", "seat": row["seat"],
                                                                                  "provider": row["provider"]},
                            "revision": 1, "edited": False, "original": False,
                            **({"mass": {"students": row["charted"]}} if row["charted"] > MASS else {})})
            for gmail_id in _gmail_ids(row["provider_receipt_json"]):
                gmail[gmail_id] = key
    if {"response_card_submissions", "response_card_recipients"} <= tables:
        for row in db.execute("""SELECT s.id, s.submission_json, s.submitted_at, r.snapshot_json, r.student_name
                                 FROM response_card_submissions s JOIN response_card_recipients r ON r.id=s.card_id
                                 WHERE r.enrollment_id=? ORDER BY s.submitted_at DESC LIMIT ?""", (enrollment_id, LIMIT)):
            labels, hidden = _card_fields(row["snapshot_json"])
            try:
                submission = json.loads(row["submission_json"])
            except ValueError:
                submission = {}
            answers = submission.get("answers") if isinstance(submission, dict) else None
            lines = []
            for field, value in (answers or {}).items() if isinstance(answers, dict) else []:
                if field in hidden:
                    continue
                shown = "yes" if value is True else "no" if value is False else ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                lines.append(f"{labels.get(field, field)}\n{shown}")
            entries.append({"key": f"card-answer:{row['id']}", "channel": "response_card", "channel_label": CHANNELS["response_card"],
                            "at": row["submitted_at"], "author_role": "student", "author": {"name": row["student_name"]}, "recipients": [],
                            "subject": _card_title(row["snapshot_json"]) or "Response Card", "text": "\n\n".join(lines)[:MAX_TEXT],
                            "attachments": [], "roles": ["author"], "context": {"source": "Response Card answer",
                                                                                 "status": str((submission or {}).get("status") or "")},
                            "revision": 1, "edited": False, "original": False})
    if "recording_links" in tables:
        # A RECORDED MEETING (RECORDINGS "Student meetings", step 4): the recordings filed under this student, each one entry
        # that opens the recording at their own part. Read by reference from the recordings' own reader; nothing is copied.
        from recordings import meetings
        entries += meetings.timeline(db, enrollment_id, LIMIT)
    return entries, gmail


NOTICE_AFTER = 3600  # Canvas emails a message within this many seconds after it was written


def _words(text):
    return " ".join(str(text or "").split()).casefold()


def same_message(notice, message):
    """Whether a Canvas notification email and a message read from Canvas are one message: the email names that Canvas message (its
    attachment links do), or it names the message's conversation, was sent within NOTICE_AFTER after it, by the student who wrote
    it, and holds its words. Only shows them once; nothing recorded changes."""
    notice_context, context = notice["context"], message["context"]
    if notice_context.get("canvas_message_id") and message.get("external_id", "").endswith(":" + notice_context["canvas_message_id"]):
        return True
    if not notice_context.get("conversation_id") or notice_context["conversation_id"] != str(context.get("conversation_id") or ""):
        return False
    if message["author_role"] != "student":
        return False
    try:
        gap = (datetime.fromisoformat(notice["at"].replace("Z", "+00:00")) - datetime.fromisoformat(message["at"].replace("Z", "+00:00"))).total_seconds()
    except (AttributeError, ValueError):
        return False
    words = _words(message["text"])[:200]
    return 0 <= gap <= NOTICE_AFTER and bool(words) and words in _words(notice["text"])


MASS = 5  # a message that reached more students than this is a mass message: shown folded (requirement, 2026-09-30)


def _text_key(author, subject, text):
    """One message whoever it went to: its writer and its words (a message sent to many students one by one, each its own Canvas
    conversation or email, is one message)."""
    author = author if isinstance(author, dict) else {}
    who = str(author.get("canvas_id") or author.get("email") or author.get("name") or "").casefold()
    return hashlib.sha256(f"{who}\0{_words(subject)}\0{_words(text)}".encode()).hexdigest()


def _reach(db):
    """{text key: how many students it reached} of everything the instructor or course staff wrote in the record."""
    reached = {}
    for row in db.execute("""SELECT r.author_json, r.subject, r.body_text, l.enrollment_id FROM comm_revisions r
                              JOIN comm_links l ON l.message_id=r.message_id
                              WHERE r.author_role IN ('instructor','staff') AND l.matched_by!='coordinator_address'
                                AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=r.message_id)"""):
        reached.setdefault(_text_key(json.loads(row["author_json"]), row["subject"], row["body_text"]), set()).add(row["enrollment_id"])
    return {key: len(students) for key, students in reached.items()}


def utc(value):
    """One spelling of an instant for every channel (Faculty writes +00:00, Canvas and the card Worker Z, some with fractions), so
    entries sort by time; text that is not an instant is kept as it is."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"  # milliseconds: what every browser parses


def timeline(db, enrollment_id):
    """Every entry of one term enrollment's record, newest first."""
    captured = _captured(db, enrollment_id)
    faculty, gmail = _faculty(db, enrollment_id)
    for entry in captured + faculty:
        entry["at"] = utc(entry["at"])
    by_key = {entry["key"]: entry for entry in faculty}
    canvas = [entry for entry in captured if entry["channel"] == "canvas_message"]
    shown = []
    for entry in captured:
        if entry["context"].get("canvas_notice"):
            same = [message for message in canvas if same_message(entry, message)]
            if len(same) == 1:
                # Canvas emailed this message and it was also read from Canvas: one entry, the Canvas copy, with the email beside it.
                same[0]["email_copy"] = entry["message_id"]
                continue
        if entry["channel"] == "gmail" and entry["external_id"] in gmail:
            # Faculty sent it through Gmail: one entry, the Faculty record, with the Gmail copy as its original.
            target = by_key[gmail[entry["external_id"]]]
            target.update(message_id=entry["message_id"], original=entry["original"], attachments=entry["attachments"],
                          context={**target["context"], "gmail": entry["context"]})
            continue
        shown.append(entry)
    reach = _reach(db) if any(entry["author_role"] in ("instructor", "staff") for entry in shown) else {}
    for entry in shown:
        students = reach.get(_text_key(entry["author"], entry["subject"], entry["text"]), 0) if entry["author_role"] in ("instructor", "staff") else 0
        if students > MASS:
            entry["mass"] = {"students": students}
    entries = sorted(shown + faculty, key=lambda entry: (entry["at"] or "", entry["key"]), reverse=True)[:LIMIT]
    for entry in entries:
        entry.pop("external_id", None)
    return entries


def revisions(db, message_id):
    """Every revision of one captured message, oldest first (what a changed Canvas comment or Gradescope reply said before)."""
    return [{"revision": row["revision"], "sent_at": row["sent_at"], "subject": row["subject"], "text": row["body_text"],
             "captured_at": row["captured_at"]}
            for row in db.execute("SELECT * FROM comm_revisions WHERE message_id=? ORDER BY revision", (message_id,))]
