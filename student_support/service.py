"""TEACHING-WORKFLOWS: enrollment-owned follow-ups and meetings, no outside writes."""
import json
import re
import uuid
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from scheduling.clock import HHMM, campus_now

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import digest as _digest, encoded
from catalog_migrations import apply_sql, migration_transaction
from communications.record import utc
from course_coordinators import service as coordinators
from course_storage import timestamp
from gmail_mail.service import roster_email, variants
from rosters import connect
from seating import term_label
from student_notes.service import StudentNoteService, _date, _request
from .policy_areas import AREAS, MODES, POLICY_LIMIT, legacy_areas, normalize_areas, policy_content
from . import meeting_hours

# TO DO (requirement, 2026-10-04):
# "todo" is the instructor's own action still owed, as "needs_reply" is a reply owed; both need attention.
STATES = {"followup": ("needs_reply", "todo", "waiting", "snoozed", "resolved"),
          "appointment": ("scheduled", "attended", "cancelled", "no_show")}
NEXT_LIMIT = 200
# SAME MATTER: what `conversations` keeps about a conversation for joins and the thanks rules, never shown on a listed suggestion.
PRIVATE = ("started", "parts", "outbound")
# A COORDINATOR'S FOLLOW-UP: the tables migration 018 makes again so that a term's coordinator may own a follow-up.
REBUILDS = {"018_coordinator_owner": ("student_support_items", "student_support_suggestion_runs", "student_support_suggestion_mutes",
                                      "student_support_conversation_mail")}
LATEST = """SELECT i.*,r.revision,r.content_json,r.created_at updated_at,r.actor revision_actor
 FROM student_support_items i JOIN student_support_revisions r ON r.item_id=i.id
 AND r.revision=(SELECT MAX(revision) FROM student_support_revisions WHERE item_id=i.id)
 JOIN course_terms t ON t.id=i.term_id WHERE t.deleted_at IS NULL"""


def now():
    return campus_now()


def text(value, label, limit=500, required=False):
    if not isinstance(value, str) or len(value) > limit or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise ValueError(f"{label} must be text of at most {limit} characters.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"Supply {label.lower()}.")
    return value


def conversation_key(row, context):
    if row["channel"] == "canvas_message" or context.get("canvas_notice"):
        if context.get("conversation_id"):
            return "canvas:" + str(context["conversation_id"])
    return row["channel"] + ":" + (row["thread_key"] or row["external_id"])


def canvas_threads(db, keys):
    """EMAIL REPLY JOINS (requirement, 2026-10-06, of a reply Faculty emailed to a Canvas message that left its follow-up
    open: an email reply to a Canvas message should count as the reply in that Canvas conversation). Faculty answers a Canvas
    message by email when it cannot post in the conversation, in the Gmail thread of Canvas's notice. {"gmail:<thread>":
    "canvas:<conversation>"} for those of `keys` whose Gmail thread holds captured Canvas notices that all name one
    conversation: its other emails (the reply, the student's answer to it) are that conversation. The exact Gmail thread links
    them, never a subject or a name; a thread whose notices name two conversations, or none, joins nothing."""
    threads = sorted({key[len("gmail:"):] for key in keys if key and key.startswith("gmail:")})
    named = {}
    for start in range(0, len(threads), 500):
        part = threads[start:start + 500]
        for thread, conversation in db.execute("""SELECT m.thread_key,json_extract(r.context_json,'$.conversation_id')
                FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id
                WHERE m.channel='gmail' AND m.thread_key IN (""" + ",".join("?" * len(part)) + """)
                AND json_extract(r.context_json,'$.canvas_notice')""", part):
            named.setdefault(thread, set()).add(str(conversation or ""))
    return {"gmail:" + thread: "canvas:" + next(iter(found)) for thread, found in named.items() if len(found) == 1 and "" not in found}


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _chunks(values, size=500):
    values = sorted(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def written_threads(db, enrollments):
    """SAME MATTER, WRITTEN FROM A FOLLOW-UP (2026-10-07, requirement, of Fiona Weaver's reply in the Gmail thread their Write
    to student… email had started: "these two follow ups belong together"): {(enrollment, "gmail:<thread>"): conversation key} for
    the Gmail threads that hold an email written from one of the student's conversations, by the captured copy of that send (its
    Gmail message ID). The exact thread links them, never a subject or a name; a thread holding emails written from two
    conversations joins nothing, and a send in the conversation's own thread joins nothing new."""
    if not enrollments or not _table(db, "gmail_mail_outcomes"):
        return {}
    named = {}
    for part in _chunks(enrollments):
        for row in db.execute("""SELECT DISTINCT w.enrollment_id,w.source_key,m.thread_key FROM student_support_conversation_mail w
                JOIN gmail_mail_outcomes o ON o.send_id=w.send_id AND o.message_id!=''
                JOIN comm_messages m ON m.channel='gmail' AND m.external_id=o.message_id AND m.thread_key!=''
                WHERE w.enrollment_id IN (""" + ",".join("?" * len(part)) + ")", part):
            thread = "gmail:" + row["thread_key"]
            if thread != row["source_key"]:
                named.setdefault((row["enrollment_id"], thread), set()).add(row["source_key"])
    return {key: next(iter(found)) for key, found in named.items() if len(found) == 1}


def join_decisions(db, enrollments):
    """SAME MATTER: {(enrollment, conversation key): latest decision row} (`student_support_conversation_joins`)."""
    if not enrollments or not _table(db, "student_support_conversation_joins"):
        return {}
    found = {}
    for part in _chunks(enrollments):
        for row in db.execute("""SELECT j.* FROM student_support_conversation_joins j WHERE j.enrollment_id IN (""" + ",".join("?" * len(part)) + """)
                AND j.rowid=(SELECT MAX(rowid) FROM student_support_conversation_joins WHERE enrollment_id=j.enrollment_id AND source_key=j.source_key)""", part):
            found[(row["enrollment_id"], row["source_key"])] = dict(row)
    return found


def joins(db, pairs, decisions=None):
    """SAME MATTER: {(enrollment, key): the conversation it reads} for those of `pairs` that read another conversation. In order:
    EMAIL REPLY JOINS (a Gmail thread of one Canvas conversation, `canvas_threads`); the latest decision about the conversation
    (`join` reads it as `into_key`; the instructor's Keep separate stops everything below); WRITTEN THREADS (`written_threads`).
    A joined conversation may itself be joined: chains are followed a few steps; a cycle joins nothing. Records keep the keys they
    were saved with. `decisions` is `join_decisions` when the caller already read it."""
    if not pairs:
        return {}
    enrollments = {enrollment for enrollment, _ in pairs}
    decisions = join_decisions(db, enrollments) if decisions is None else decisions
    written = written_threads(db, enrollments)
    decided = {key: row["into_key"] for key, row in decisions.items() if row["decision"] == "join"}
    separate = {key for key, row in decisions.items() if row["decision"] == "separate"}
    keys = {key for _, key in pairs if key.startswith("gmail:")} | set(decided.values()) | set(written.values())
    canvas = canvas_threads(db, keys)
    found = {}
    # PERFORMANCE: nearly every conversation joins nothing; only those named by one of the three sources are followed.
    follow = {pair for pair in decided.keys() | written.keys() if pair in pairs}
    for enrollment, key in follow | ({pair for pair in pairs if pair[1] in canvas} if canvas else set()):
        current, seen = key, {key}
        for _ in range(6):
            pair = (enrollment, current)
            step = canvas.get(current) or decided.get(pair) or (None if pair in separate else written.get(pair))
            if not step:
                break
            if step in seen:
                current = key  # a cycle joins nothing
                break
            seen.add(step)
            current = step
            if step not in canvas and step.startswith("gmail:"):
                canvas.update(canvas_threads(db, [step]))
        if current != key:
            found[(enrollment, key)] = current
    return found


def conversation_of(db, source_key, enrollment_id=None):
    """The conversation a recorded source key reads (EMAIL REPLY JOINS; with the student's enrollment, SAME MATTER too); records
    keep the key they were saved with."""
    if enrollment_id:
        return joins(db, [(enrollment_id, source_key)]).get((enrollment_id, source_key), source_key)
    return canvas_threads(db, [source_key]).get(source_key, source_key)


def conversations(db, enrollment_id=None, maps=None):
    """One catalog read; identities are explicit comm_links, never name/subject guesses.

    Captured Canvas notifications with the same conversation ID share a thread, and so do the other emails of a Gmail
    thread that is one Canvas conversation (EMAIL REPLY JOINS); a conversation joined to another (SAME MATTER: a Gmail thread an
    email written from a follow-up started, or the instructor's or Jev's join) is read as part of it, its own key and first
    subject kept under `parts`. `started` is the conversation's first message, `outbound` the instructor's latest (when there is
    one). No unrelated channel is merged otherwise. A Gradescope regrade request is a
    conversation too (REGRADES "FOLLOW-UP", requirement, 2026-10-07): one
    question submission's requests and replies, Gradescope's staff reply counting as the instructor's, and the thread answered
    once its latest request is answered or handled (grading/regrade_followup.py).
    """
    rows = db.execute("""SELECT m.id,m.channel,m.external_id,m.thread_key,r.sent_at,r.subject,
        r.author_role,r.context_json,l.enrollment_id,l.term_id,
        MAX(CASE WHEN l.role='author' THEN 1 ELSE 0 END) student_author
        FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id
        AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
        JOIN comm_links l ON l.message_id=m.id JOIN course_terms t ON t.id=l.term_id
        WHERE t.deleted_at IS NULL AND m.channel IN ('gmail','canvas_message','canvas_comment','gradescope_regrade')
        AND """ + ("l.enrollment_id=?" if enrollment_id else "t.is_current=1") + """
        GROUP BY m.id,l.enrollment_id ORDER BY r.sent_at,m.id""", (enrollment_id,) if enrollment_id else ()).fetchall()
    keyed = [(row, conversation_key(row, json.loads(row["context_json"])), row["enrollment_id"]) for row in rows]
    # EMAIL REPLY JOINS and SAME MATTER: a conversation joined to another is read as part of it.
    joined = joins(db, {(enrollment, key) for _, key, enrollment in keyed})
    if maps is not None:
        maps["joined"] = joined  # PERFORMANCE: a caller grouping the same messages reuses the map (one read)
    moved = {key for _, key in joined}
    result = {}
    for row, own, enrollment in keyed:
        key = joined.get((enrollment, own), own) if own in moved else own
        stamp = (utc(row["sent_at"]), row["id"])
        role = correspondent_role(row, enrollment)
        inbound = role == "student" and bool(row["student_author"])
        if role not in ("student", "instructor") or (role == "student" and not inbound):
            continue
        entry = result.get((enrollment, key))
        if entry is None:
            entry = result[(enrollment, key)] = {
                "enrollment_id": enrollment, "term_id": row["term_id"], "source_key": key,
                "latest": ("", ""), "inbound": ("", ""), "student_last": False, "started": stamp, "outbound": ("", ""), "parts": {}}
        if stamp > entry["latest"]:
            entry.update(latest=stamp, student_last=inbound, title=row["subject"] or regrade_title(row) or "Student conversation",
                         message_id=row["id"], channel="canvas" if key.startswith("canvas:") else row["channel"])
        if inbound:
            if stamp > entry["inbound"]:
                entry["inbound"] = stamp
        elif stamp > entry["outbound"]:
            entry["outbound"] = stamp
        if stamp < entry["started"]:
            entry["started"] = stamp
        if own != key:  # SAME MATTER: the joined conversation, with its first subject and when it started
            part = entry["parts"].setdefault(own, {"title": row["subject"] or "Student conversation", "started": stamp})
            if stamp < part["started"]:
                part.update(title=row["subject"] or part["title"], started=stamp)
    regrades = [entry for (_, key), entry in result.items() if key.startswith("gradescope_regrade:") and entry["student_last"]]
    if regrades:
        from grading.regrade_followup import answered
        from grading.regrade_requests import captured
        rows = captured(db)
        for entry in regrades:
            if answered(db, entry["source_key"], rows):
                entry["student_last"] = False  # answered on Gradescope, by Faculty's reply, or marked handled
    return result


def regrade_role(row):
    """A message's author as a conversation counts it: Gradescope's reply to a regrade request (staff) is the instructor's."""
    return "instructor" if row["channel"] == "gradescope_regrade" and row["author_role"] == "staff" else row["author_role"]


def correspondent_role(row, owner):
    """A message's author as its owner's conversation counts it: `student` is whoever the conversation is with. In a
    coordinator's conversation (A COORDINATOR'S FOLLOW-UP: the owner is the term's coordinator key) that is the coordinator, by
    the link that says they wrote it; a student's message that only copied the coordinator is not part of it."""
    if coordinators.owner_term(owner):
        if row["author_role"] == "instructor":
            return "instructor"
        return "student" if row["student_author"] else "other"
    return regrade_role(row)


def regrade_title(row):
    """A regrade request's conversation title: "Regrade request · Common Exam 1 · Question 20"."""
    if row["channel"] != "gradescope_regrade":
        return ""
    context = json.loads(row["context_json"] or "{}")
    return " · ".join(part for part in ("Regrade request", context.get("assignment"), context.get("question")) if part)


def latest_reply(thread, written=()):
    """WAITING ON: [sent_at, message ID] of the instructor's newest message after the student's latest one in this conversation,
    counting mail written from its follow-up wherever Gmail threaded it (WRITTEN FROM A FOLLOW-UP), or None. Such mail the record
    has not captured yet is [sent_at, "", {subject, text}] (WRITTEN AT ONCE)."""
    found = [] if thread["student_last"] else [tuple(thread["latest"])]
    found += [reply for reply in written if tuple(reply[:2]) > tuple(thread["inbound"])]
    return list(max(found, key=lambda reply: tuple(reply[:2]))) if found else None


def answer(item, thread, written=()):
    """WAITING ON: the Follow-up page's `answered` [sent_at, message ID] on a listed conversation, and `answered_mail` {subject,
    text} when it is an email written from it that the record has not captured yet (WRITTEN AT ONCE)."""
    reply = latest_reply(thread, written)
    if reply:
        item["answered"] = reply[:2]
        if len(reply) > 2:
            item["answered_mail"] = reply[2]


CHECK_BACK_DAYS, CHECK_BACK_LONGEST = 7, 60


def check_back(item):
    """CHECK BACK (requirement, 2026-10-22, of 24 follow-ups Waiting on student with no date, many past the days they named; they
    chose a check-back date for every one): the campus day a Waiting on student follow-up with no date of its own comes back to Needs
    me, read from the revision that set it: the day after the latest day still to come that its next step names ("her paper
    Wednesday, October 14"), else a week after it was set, and never more than 60 days on. The day it was set is not to come: "a note
    covering October 30", set on October 30, names the absence, not when the note is expected (requirement, 2026-11-04). Read in code (JEV DATES `mentions`, definite days and spans only); a view, nothing saved.
    An instructor's own date (Edit follow-up…, Wait a week) is the follow-up's `date` and stands instead."""
    from datetime import timedelta
    from jev import dates as jev_dates
    set_on = jev_dates.campus_day(item["updated_at"])
    if set_on is None:
        return ""
    named = [day for found in jev_dates.mentions(str(item.get("next") or ""), item["updated_at"])
             if found["form"] in (jev_dates.DEFINITE, jev_dates.RANGE) for day in found["dates"] if day > set_on.isoformat()]
    day = datetime.fromisoformat(max(named)).date() + timedelta(days=1) if named else set_on + timedelta(days=CHECK_BACK_DAYS)
    return min(day, set_on + timedelta(days=CHECK_BACK_LONGEST)).isoformat()


def effective(row, threads, today, since="", answered=False, joined=None, written=None, db=None):
    item = dict(row)
    item.pop("revision_actor", None)  # read for WHAT YOU DID only; the listed item keeps its fields
    content = json.loads(item.pop("content_json"))
    item.update(content)
    item["saved_state"] = item["state"]
    item["new_reply"] = False
    if item["kind"] == "followup":
        key = (item["enrollment_id"], (joined or {}).get((item["enrollment_id"], item["source_key"]), item["source_key"]))
        thread = threads.get(key)
        if thread and thread["inbound"][0] > since and tuple(thread["inbound"]) > tuple(content.get("observed", ["", ""])):
            # ANSWERED (requirement, 2026-10-13): a newer student message the instructor has written after no longer reopens it. A view,
            # nothing saved; the student writing again reopens it.
            if latest_reply(thread, (written or {}).get(key, ())):
                item["new_reply_answered"] = True
            else:
                item.update(state="needs_reply", new_reply=True)
        if thread:
            item["observed"] = list(thread["inbound"])
            # WAITING ON: the instructor's message that came after the student's latest one, for the Follow-up page's review.
            if answered:
                answer(item, thread, (written or {}).get(key, ()))
        if item["state"] == "waiting" and item["saved_state"] == "waiting" and not item.get("date"):
            item["check_back"] = check_back(item)  # CHECK BACK: a view, nothing saved
        item["attention"] = item["state"] in ("needs_reply", "todo") or (
            item["state"] in ("waiting", "snoozed") and bool(item.get("date")) and item["date"] <= today) or (
            item["state"] == "waiting" and bool(item.get("check_back")) and item["check_back"] <= today)
        if item.get('commitments'):
            from .commitments import project, is_open, needs_attention
            if db is not None:
                from .commitments import heard
                item['commitments'] = heard(db, item['enrollment_id'], project(db, item['commitments']))
            open_tasks = [c for c in item['commitments'] if is_open(c)]
            if open_tasks and item['state'] == 'resolved':
                item['state'] = 'todo'
            if open_tasks:
                item['attention'] = item['new_reply'] or item['state'] == 'needs_reply' or needs_attention(open_tasks, today)
    else:
        # MEETINGS COMPLETE THEMSELVES (requirement, 2026-10-21): a scheduled meeting whose end has passed reads as Attended and needs nothing. A view,
        # nothing saved; Update can still record a cancellation or a no-show, or a note.
        if item["state"] == "scheduled" and f"{item['date']}T{item['end_time']}" <= now().strftime("%Y-%m-%dT%H:%M"):
            item["state"] = "attended"
        item["attention"] = False
    return item


def appointment_title(purpose, name=None):
    """CALENDAR OFFICE HOURS: "Office hours: <name> · <purpose>"; a purpose that only says office hours is left off."""
    purpose = " ".join(str(purpose or "").split())
    shown = purpose if re.sub(r"[^a-z]", "", purpose.casefold()) not in ("", "officehour", "officehours") else ""
    return "Office hours" + (f": {name}" if name else "") + (f" · {shown}" if shown else "")


def calendar_events(db, start, end):
    """CALENDAR OFFICE HOURS: each appointment as an "Office hours" entry with its purpose (requirement, 2026-10-06). The student's name is put on it after the calendar is assembled
    (`StudentSupportService.name_appointments`): names come from the verified roster files, read outside this catalog
    transaction. Private details are never projected."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='student_support_items'").fetchone():
        return []
    rows = db.execute(LATEST + " AND i.kind='appointment' AND json_extract(r.content_json,'$.date') BETWEEN ? AND ? ORDER BY i.id", (start, end))
    events = []
    for row in rows:
        content = json.loads(row["content_json"])
        if content["state"] == "cancelled":
            continue
        # The public projection uses an opaque appointment ID, not the enrollment ID.
        events.append({"id": "office-hours:" + row["id"], "source": "office_hours", "category": "event",
                       "title": appointment_title(content.get("title")), "purpose": content.get("title", ""),
                       "date": content["date"], "end_date": content["date"],
                       "time": content["time"], "end_time": content["end_time"], "all_day": False,
                       "location": content["location"], "notes": "", "definition_id": None,
                       "term_id": row["term_id"], "section_id": None, "code": "", "term_label": "", "sections": [],
                       "href": "#office-hours", "editable": False, "version": row["revision"], "time_basis": "campus"})
    return events


def cancelled_events(db, start, end):
    """The appointments recorded as CANCELLED between two dates, in the calendar's own shape with `cancelled` true. The calendar
    never shows one (`calendar_events`); RECORDINGS "AN APPOINTMENT IS SETTLED ALREADY" reads them only to say that a recording
    began inside one, where nothing else was on the calendar. Nothing is projected anywhere else."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='student_support_items'").fetchone():
        return []
    events = []
    for row in db.execute(LATEST + " AND i.kind='appointment' AND json_extract(r.content_json,'$.date') BETWEEN ? AND ? ORDER BY i.id", (start, end)):
        content = json.loads(row["content_json"])
        if content["state"] == "cancelled":
            events.append({"id": "office-hours:" + row["id"], "source": "office_hours", "category": "event", "cancelled": True,
                           "title": appointment_title(content.get("title")), "date": content["date"], "end_date": content["date"],
                           "time": content["time"], "end_time": content["end_time"], "all_day": False, "term_id": row["term_id"],
                           "section_id": None, "definition_id": None, "time_basis": "campus"})
    return events


def appointments(db, ids):
    """Appointments by their IDs, as another module refers to one by reference (RECORDINGS: a recording filed inside an
    appointment): {id: {enrollment_id, term_id, state (as SAVED: a passed meeting reading as Attended is a view), date, time,
    end_time, title (its purpose)}}. Nothing is copied; an ID that is no appointment is left out."""
    ids = sorted({str(ident) for ident in ids if ident})
    if not ids or not db.execute("SELECT 1 FROM sqlite_master WHERE name='student_support_items'").fetchone():
        return {}
    found = {}
    for row in db.execute(LATEST + f" AND i.kind='appointment' AND i.id IN ({','.join('?' * len(ids))})", ids):
        content = json.loads(row["content_json"])
        found[row["id"]] = {"enrollment_id": row["enrollment_id"], "term_id": row["term_id"], "state": content.get("state", ""),
                            "date": content.get("date", ""), "time": content.get("time", ""), "end_time": content.get("end_time", ""),
                            "title": content.get("title", "")}
    return found


class StudentSupportService:
    def __init__(self, rosters, students, communications):
        self.rosters, self.path = rosters, rosters.path
        self.students, self.communications = students, communications
        self.triage = None
        self.suggestions = None
        migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        with rosters.lock(), connect(self.path) as db:
            applied = set()
            if _table(db, "schema_migrations"):
                applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations WHERE namespace='student_support'")}
            # A migration that makes tables again (REBUILDS) runs with foreign keys suspended and checked, only while it is pending.
            rebuilt = tuple(table for migration in migrations if migration.stem not in applied for table in REBUILDS.get(migration.stem, ()))
            with migration_transaction(db, rebuild=rebuilt):
                for migration in migrations:
                    apply_sql(db, "student_support", migration.stem, migration.read_text(encoding="utf-8"))

    @staticmethod
    def _baseline(db):
        row = db.execute("SELECT request_id,tracking_since FROM student_support_baselines ORDER BY rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else {"request_id": "", "tracking_since": ""}

    def _items(self, db, enrollment_id=None, since=None, answered=False, unconfirmed=(), scoped=False, include_joined=False):
        """`scoped` (`settled_mail` only; every other read is unchanged): the conversation analyses are read for this one
        current-term enrollment instead of the whole term (PERFORMANCE: about 3 ms instead of 300 on the live catalog)."""
        since = self._baseline(db)["tracking_since"] if since is None else since
        threads = conversations(db, enrollment_id)
        today = now().date().isoformat()
        rows = db.execute(LATEST + (" AND i.enrollment_id=?" if enrollment_id else " AND t.is_current=1") + " ORDER BY i.created_at,i.id",
                          (enrollment_id,) if enrollment_id else ()).fetchall()
        written = self._written(db, enrollment_id, unconfirmed)  # ANSWERED: every read asks whether the instructor wrote after
        followups = {(row["enrollment_id"], row["source_key"]) for row in rows if row["kind"] == "followup"}
        decisions = join_decisions(db, {enrollment for enrollment, _ in followups} | {enrollment for enrollment, _ in threads})
        joined = joins(db, followups, decisions)
        items = [effective(row, threads, today, since, answered, joined, written, db=db) for row in rows]
        if answered:
            # WHAT YOU DID (2026-10-13): who saved each follow-up last, for the queue's "You updated it" line (an item's own
            # `actor` is whoever created it). The Follow-up page's read only.
            saved = {row["id"]: row["revision_actor"] for row in rows if row["kind"] == "followup"}
            for item in items:
                if item["kind"] == "followup" and item["id"] in saved:
                    item["updated_by"] = saved[item["id"]]
        # SAME MATTER: two tracked follow-ups that read one conversation are one follow-up: the one saved under that conversation's
        # own key, else the first saved. The other keeps its records and leaves the list, its counts and Needs me.
        owners = {}
        for item in items:
            if item["kind"] == "followup" and item["source_key"]:
                owners.setdefault((item["enrollment_id"], joined.get((item["enrollment_id"], item["source_key"]), item["source_key"])), []).append(item)
        hidden = {id(other) for key, group in owners.items() if len(group) > 1
                  for other in group if other is not next((i for i in group if i["source_key"] == key[1]), group[0])}
        # ACTION DESK: a join keeps promises visible by reference to their original follow-up.
        from .commitments import is_open, needs_attention
        for key, group in owners.items():
            keeper = next((i for i in group if i['source_key'] == key[1]), group[0])
            related = [{'owner_id': other['id'], 'source_key': other['source_key'], 'title': other['title'],
                        'commitments': other['commitments']} for other in group
                       if other is not keeper and other.get('commitments')]
            if related and not include_joined:
                keeper['related_commitments'] = related
                tasks = [c for owner in related for c in owner['commitments'] if is_open(c)]
                if tasks and keeper['state'] == 'resolved':
                    keeper['state'] = 'todo'
                keeper['attention'] = keeper['attention'] or needs_attention(tasks, today)
        items = [item for item in items if include_joined or id(item) not in hidden]
        # A COORDINATOR'S FOLLOW-UP: the coordinator's mail is a suggestion only while the term is coordinated by that address,
        # and only what came after the course card named them (earlier mail of the term is kept as background, never a queue).
        began = {coordinators.owner_key(term): utc(entry["since"]) for term, entry in coordinators.current(db).items()} \
            if any(coordinators.owner_term(owner) for owner, _ in threads) else {}
        for key, thread in threads.items():
            if key in owners or not thread["student_last"] or thread["latest"][0] <= since:
                continue
            if coordinators.owner_term(key[0]) and thread["latest"][0] <= began.get(key[0], "9999"):
                continue
            # A REPLY FROM ITS CARD: answered as a reply in its own thread is. Mail not captured yet (WRITTEN AT ONCE) stays listed.
            reply = latest_reply(thread, written.get(key, ()))
            if reply and len(reply) == 2:
                continue
            items.append({**{k: v for k, v in thread.items() if k not in PRIVATE}, "id": "", "kind": "followup",
                          "revision": 0, "state": "needs_reply", "saved_state": "needs_reply", "attention": True, "new_reply": False,
                          "date": "", "details": "", "updated_at": thread["latest"][0], "observed": list(thread["inbound"])})
            if answered:
                answer(items[-1], thread, written.get(key, ()))
        if answered:
            self._same_matter(items, threads, joined, decisions)
        if self.triage:
            items = self.triage.apply(db, items, since, enrollment_id if scoped else None, threads)
        # JEV WELLBEING/URGENCY: an open possible wellbeing concern first, then what needs attention, the more urgent first.
        return sorted(items, key=lambda row: (not row.get("wellbeing"), not row["attention"],
                                              -((row.get("triage") or {}).get("urgency") or {}).get("score", 0),
                                              row.get("date") or "9999", row["updated_at"], row["id"]))

    @staticmethod
    def _same_matter(items, threads, joined, decisions):
        """SAME MATTER, for the Follow-up page's suggestions=1 read only (other reads stay byte-identical): each follow-up's
        `joins`, the conversations the instructor or Jev joined to it ({source_key, title, started, by, at, id}, oldest first), and on
        a conversation Jev was unsure about, `match`, the follow-up it may continue ({into_key, title, state, id})."""
        conversation = {}
        for item in items:
            if item["kind"] == "followup" and item["source_key"]:
                key = (item["enrollment_id"], joined.get((item["enrollment_id"], item["source_key"]), item["source_key"]))
                conversation[key] = item
        for (enrollment, key), item in conversation.items():
            parts = (threads.get((enrollment, key)) or {}).get("parts") or {}
            found = []
            for part, seen in parts.items():
                row = decisions.get((enrollment, part))
                if row and row["decision"] == "join":
                    found.append({"source_key": part, "title": seen["title"], "started": seen["started"][0], "by": row["decided_by"],
                                  "at": row["created_at"], "id": row["id"]})
            if found:
                item["joins"] = sorted(found, key=lambda j: (j["started"], j["source_key"]))
            row = decisions.get((enrollment, item["source_key"])) if not item["id"] else None
            target = conversation.get((enrollment, row["into_key"])) if row and row["decision"] == "ask" else None
            if target is not None and target is not item:
                item["match"] = {"into_key": row["into_key"], "title": target["title"], "state": target["state"], "id": row["id"]}

    @staticmethod
    def _written(db, enrollment_id=None, unconfirmed=()):
        """WRITTEN FROM A FOLLOW-UP: {(enrollment, conversation key): [(sent_at, message ID)]} of mail written from a follow-up that
        the communications record captured for that same student (a send to anyone else never shows), and of the `unconfirmed`
        ones (`_unconfirmed`) as (sent_at, "", {subject, text}) (WRITTEN AT ONCE). Read on every listing (ANSWERED)."""
        if not _table(db, "gmail_mail_outcomes"):
            return {}
        rows = db.execute("""SELECT DISTINCT w.enrollment_id,w.source_key,m.id,r.sent_at FROM student_support_conversation_mail w
            JOIN gmail_mail_outcomes o ON o.send_id=w.send_id AND o.message_id!=''
            JOIN comm_messages m ON m.channel='gmail' AND m.external_id=o.message_id
            JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
            JOIN comm_links l ON l.message_id=m.id AND l.enrollment_id=w.enrollment_id
            WHERE r.author_role='instructor'""" + (" AND w.enrollment_id=?" if enrollment_id else ""),
                          (enrollment_id,) if enrollment_id else ()).fetchall()
        rows = [*rows, *StudentSupportService._card_replies(db, enrollment_id)]
        pending = [row for row in unconfirmed if not enrollment_id or row["enrollment_id"] == enrollment_id]
        joined = joins(db, {(row["enrollment_id"], row["source_key"]) for row in [*rows, *pending]})
        found = {}
        for row in rows:
            key = (row["enrollment_id"], joined.get((row["enrollment_id"], row["source_key"]), row["source_key"]))
            found.setdefault(key, []).append((utc(row["sent_at"]), row["id"]))
        for row in pending:
            key = (row["enrollment_id"], joined.get((row["enrollment_id"], row["source_key"]), row["source_key"]))
            found.setdefault(key, []).append((utc(row["sent_at"]), "", {"subject": row["subject"], "text": row["text"]}))
        return found

    @staticmethod
    def _card_replies(db, enrollment_id=None):
        """A REPLY FROM ITS CARD (requirement, 2026-11-04): the captured
        copies of replies a follow-up's own card sent, or Mail sent in a card's place, as `_written` reads mail written from it. To a
        Canvas conversation such a reply starts a Gmail thread of its own, so nothing else ties it to the student's message; its
        conversation stays its own (no thread is joined by this). Pinned to the steps' order over the whole term (PERFORMANCE)."""
        if not _table(db, "student_support_suggestion_replacements"):
            return []
        join = "JOIN" if enrollment_id else "CROSS JOIN"
        copy = f"""{join} comm_messages m ON m.channel='gmail' AND m.external_id=o.message_id
            {join} comm_revisions v ON v.message_id=m.id AND v.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
            {join} comm_links k ON k.message_id=m.id AND k.enrollment_id=r.enrollment_id
            WHERE v.author_role='instructor'""" + (" AND r.enrollment_id=?" if enrollment_id else "")
        return db.execute(f"""SELECT r.enrollment_id,r.source_key,m.id,v.sent_at FROM student_support_suggestion_actions l
            {join} assistant_actions a ON a.id=l.action_id AND a.endpoint='gmail_mail.send'
            {join} assistant_action_events e ON e.action_id=a.id AND e.kind='succeeded'
            {join} student_support_suggestion_runs r ON r.id=l.run_id AND r.source_key!=''
            {join} gmail_mail_outcomes o ON o.send_id=json_extract(e.detail_json,'$.result.id') AND o.message_id!=''
            {copy}
            UNION SELECT r.enrollment_id,r.source_key,m.id,v.sent_at FROM student_support_suggestion_replacements p
            {join} student_support_suggestion_runs r ON r.id=p.run_id AND r.source_key!=''
            {join} gmail_mail_outcomes o ON o.send_id=p.send_id AND o.message_id!=''
            {copy}""", (enrollment_id, enrollment_id) if enrollment_id else ()).fetchall()

    def _unconfirmed(self, enrollment_id=None):
        """WRITTEN AT ONCE (2026-10-08, requirement of Dana Velez Archer's follow-up, whose Write to student… email did not
        show on it until the next Gmail capture round, minutes later: "whenever I use that function from the follow up menu, it will
        always mean that it is related to the follow up, so it should be shown"): mail written from a follow-up that Mail sent
        (accepted, with its Gmail message ID), that no Gmail capture round has had the chance to take (the record has no copy and it
        was accepted after the last successful round started), and that went (To, Cc or Bcc) to that student's roster address:
        [{enrollment_id, source_key, sent_at, subject, text}]. Read outside any catalog transaction (it takes the roster lock)."""
        with connect(self.path) as db:
            if not _table(db, "gmail_mail_outcomes"):  # read for the badge too (ANSWERED): a catalog without Mail has none
                return []
            state = db.execute("SELECT last_success_at FROM comm_capture_state WHERE channel='gmail'").fetchone()
            rows = db.execute("""SELECT w.enrollment_id,w.source_key,s.to_json,s.cc_json,s.bcc_json,s.subject,s.body_text,o.created_at
                FROM student_support_conversation_mail w JOIN gmail_mail_sends s ON s.id=w.send_id
                JOIN gmail_mail_outcomes o ON o.send_id=s.id AND o.id=(SELECT MAX(id) FROM gmail_mail_outcomes WHERE send_id=s.id)
                WHERE o.status='accepted' AND o.message_id!=''
                AND NOT EXISTS (SELECT 1 FROM comm_messages m WHERE m.channel='gmail' AND m.external_id=o.message_id)"""
                              + (" AND w.enrollment_id=?" if enrollment_id else ""), (enrollment_id,) if enrollment_id else ()).fetchall()
        since = utc(state["last_success_at"]) if state and state["last_success_at"] else ""
        rows = [row for row in rows if utc(row["created_at"]) >= since]
        if not rows:
            return []
        addresses = self._addresses({row["enrollment_id"] for row in rows})
        found = []
        for row in rows:
            sent_to = {spelling for field in ("to_json", "cc_json", "bcc_json") for value in json.loads(row[field])
                       if isinstance(value, str) for spelling in variants(parseaddr(value)[1] or value)}
            if sent_to & addresses.get(row["enrollment_id"], set()):
                found.append({"enrollment_id": row["enrollment_id"], "source_key": row["source_key"], "sent_at": row["created_at"],
                              "subject": row["subject"], "text": row["body_text"]})
        return found

    def _addresses(self, enrollment_ids):
        """{enrollment: every spelling of the student's roster addresses} in the current terms, from each term's verified roster
        payload (as the communications record links mail), under the roster lifecycle lock."""
        ids, found = sorted(owner for owner in enrollment_ids if not coordinators.owner_term(owner)), {}
        with self.rosters.lock():
            with connect(self.path) as db:
                # A COORDINATOR'S FOLLOW-UP: the coordinator's address is the one the term's record holds now.
                for owner in enrollment_ids:
                    who = coordinators.of_owner(db, owner)
                    if who:
                        found[owner] = set(variants(who["email"]))
                members = db.execute(f"""SELECT m.section_id,m.enrollment_id,s.term_id FROM roster_memberships m
                    JOIN course_sections s ON s.id=m.section_id WHERE m.enrollment_id IN ({','.join('?' * len(ids))})""", ids).fetchall()
                terms = [dict(row) for row in db.execute(f"""SELECT * FROM course_terms WHERE is_current=1 AND deleted_at IS NULL
                    AND id IN ({','.join('?' * len({m['term_id'] for m in members}))})""", sorted({m["term_id"] for m in members}))]
            for term in terms:
                with self.rosters.files(term) as root:
                    payload = self.rosters.read_payload(root, term["roster_initialized"])
                for member in members:
                    if member["term_id"] != term["id"]:
                        continue
                    record = payload["sections"].get(member["section_id"], {}).get("students", {}).get(member["enrollment_id"], {})
                    address = roster_email(record)
                    if address:
                        found.setdefault(member["enrollment_id"], set()).update(variants(address))
        return found

    def written(self, data, actor="workspace"):
        """WRITTEN FROM A FOLLOW-UP (2026-10-07): the Mail send the instructor wrote with a conversation's Write to student…, named
        by the send's request ID once Mail sent it. The follow-up shows it as their reply at once (WRITTEN AT ONCE) and, once it is
        captured for this student, wherever Gmail threaded it; a new record asks for a Gmail capture round now (Capture now). The
        follow-up's status is unchanged. Idempotent by send; nothing is sent here."""
        if not isinstance(data, dict) or set(data) - {"enrollment_id", "source_key", "send_request_id"}:
            raise ValueError("Name the student's conversation and the Mail send written from it.")
        source = text(data.get("source_key"), "Conversation", 500, True)
        send_request = text(data.get("send_request_id"), "Mail send", 100, True)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            enrollment = self._owner(db, data.get("enrollment_id"))
            if not conversations(db, enrollment["id"]).get((enrollment["id"], conversation_of(db, source, enrollment["id"]))):
                raise ValueError("That conversation does not belong to this student's term enrollment.")
            send = db.execute("SELECT id FROM gmail_mail_sends WHERE request_id=?", (send_request,)).fetchone()
            if not send:
                raise NotFound("That Mail send was not found.")
            outcome = db.execute("SELECT status FROM gmail_mail_outcomes WHERE send_id=? ORDER BY id DESC LIMIT 1", (send["id"],)).fetchone()
            if outcome and outcome["status"] == "failed":
                raise ValueError("Mail did not send it.")
            old = db.execute("SELECT enrollment_id,source_key FROM student_support_conversation_mail WHERE send_id=?", (send["id"],)).fetchone()
            if old and (old["enrollment_id"], old["source_key"]) != (enrollment["id"], source):
                raise Conflict("That send was written from another conversation.")
            if not old:
                db.execute("INSERT INTO student_support_conversation_mail VALUES (?,?,?,?,?)", (send["id"], enrollment["id"], source, actor, timestamp()))
            db.commit()
        if not old:
            self.communications.request({"channels": ["gmail"]})
        return {"send_id": send["id"]}

    @staticmethod
    def _decided(db, request_id, digest):
        row = db.execute("SELECT * FROM student_support_conversation_joins WHERE request_id=?", (request_id,)).fetchone()
        if row and row["request_sha256"] != digest:
            raise Conflict("This request ID already recorded a different decision.")
        return {key: row[key] for key in ("id", "source_key", "into_key", "decision")} if row else None

    @staticmethod
    def _decide(db, enrollment_id, source, decision, by, into="", call_id="", request_id="", digest="", actor="workspace"):
        ident = str(uuid.uuid4())
        db.execute("INSERT INTO student_support_conversation_joins VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (ident, enrollment_id, source, into, decision, by, call_id, request_id, digest, actor, timestamp()))
        return {"id": ident, "source_key": source, "into_key": into, "decision": decision}

    @staticmethod
    def _message_conversation(db, enrollment_id, message_id):
        """CONNECT A MESSAGE: the conversation one captured message of this student belongs to, as the follow-up list keys it
        (`conversation_key`). The message is named by its ID in the communications record and is linked to this enrollment there;
        another student's message, or an email the record holds no captured copy of, names nothing."""
        row = db.execute("""SELECT m.channel,m.external_id,m.thread_key,r.context_json FROM comm_messages m JOIN comm_revisions r
            ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
            WHERE m.id=? AND EXISTS (SELECT 1 FROM comm_links l WHERE l.message_id=m.id AND l.enrollment_id=?)""",
                         (message_id, enrollment_id)).fetchone()
        if not row:
            raise ValueError("That message is not in this student's communications record.")
        return conversation_key(row, json.loads(row["context_json"]))

    def _join_pair(self, db, enrollment_id, data):
        """SAME MATTER: the two conversations a join names, as the follow-up list reads them now, in the order named:
        [(started, key, thread)] for `source_key` and for the other one, which is `with_key` or the conversation of the captured
        message `with_message_id` (CONNECT A MESSAGE). Another student's conversation, the same conversation and a regrade request
        are refused, for the join and for its preview alike."""
        if data.get("with_message_id") is not None and data.get("with_key") is not None:
            raise ValueError("Name the other conversation or one of its messages, not both.")
        keys = [text(data.get("source_key"), "Conversation", 500, True),
                self._message_conversation(db, enrollment_id, text(data.get("with_message_id"), "Message", 100, True))
                if data.get("with_message_id") is not None else text(data.get("with_key"), "Conversation", 500, True)]
        threads, found = conversations(db, enrollment_id), []
        for key in keys:
            resolved = conversation_of(db, key, enrollment_id)
            thread = threads.get((enrollment_id, resolved))
            if not thread:
                raise ValueError("That conversation does not belong to this student's term enrollment.")
            if resolved.startswith("gradescope_regrade:"):
                raise ValueError("A regrade request stays its own follow-up.")
            found.append((thread["started"], resolved, thread))
        if found[0][1] == found[1][1]:
            raise Conflict("These conversations are already one follow-up.")
        return found

    def join(self, data, actor="workspace"):
        """SAME MATTER (2026-10-07): the instructor's join of two of a student's conversations into one follow-up. The one that
        started later is read as part of the earlier one, and the follow-up saved under the earlier one keeps its status. Idempotent
        by request ID; nothing is sent; records keep their keys (Keep separate undoes it). CONNECT A MESSAGE (2026-10-10): the other
        conversation may be named by one of its captured messages (`with_message_id`) instead of its key."""
        if not isinstance(data, dict) or set(data) - {"enrollment_id", "source_key", "with_key", "with_message_id", "request_id"}:
            raise ValueError("Name the student's two conversations to join.")
        request_id = _request(data.get("request_id"))
        digest = _digest({"join": data})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            enrollment = StudentNoteService._enrollment(db, data.get("enrollment_id"))
            previous = self._decided(db, request_id, digest)
            if previous:
                return previous
            (_, into, _), (_, source, _) = sorted(self._join_pair(db, enrollment["id"], data), key=lambda found: found[:2])
            result = self._decide(db, enrollment["id"], source, "join", "instructor", into, "", request_id, digest, actor)
            db.commit()
        return result

    def join_preview(self, data):
        """CONNECT A MESSAGE (2026-10-10): what a join of these two conversations would record, checked exactly as the join checks
        it; writes nothing. `source_key` and `into_key` as the join would save them (the later one is read as part of the
        earlier), and for the conversation named by `source_key` (`source`) and the other one (`with`): its key, the subject of
        its first message, when it started, and the title and status of the follow-up saved for it, when there is one."""
        if not isinstance(data, dict) or set(data) - {"enrollment_id", "source_key", "with_key", "with_message_id"}:
            raise ValueError("Name the student's two conversations to join.")
        with connect(self.path) as db:
            enrollment = StudentNoteService._enrollment(db, data.get("enrollment_id"))
            found = self._join_pair(db, enrollment["id"], data)
            saved = {}
            for row in db.execute(LATEST + " AND i.enrollment_id=? AND i.kind='followup' AND i.source_key!='' ORDER BY i.created_at,i.id",
                                  (enrollment["id"],)):
                content = json.loads(row["content_json"])
                saved.setdefault(conversation_of(db, row["source_key"], enrollment["id"]),
                                 {"id": row["id"], "title": content.get("title", ""), "state": content.get("state", "")})
            subjects = dict(db.execute("""SELECT m.id,r.subject FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id
                AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id) WHERE m.id IN (?,?)""",
                                       [started[1] for started, _, _ in found]).fetchall())
        (_, into, _), (_, source, _) = sorted(found, key=lambda entry: entry[:2])
        described = [{"source_key": key, "title": subjects.get(started[1]) or thread["title"], "started": started[0],
                      **({"followup": saved[key]} if key in saved else {})} for started, key, thread in found]
        return {"source_key": source, "into_key": into, "source": described[0], "with": described[1]}

    def separate(self, data, actor="workspace"):
        """SAME MATTER: the instructor's Keep separate. A conversation joined to another follow-up (by them, by Jev, or as the thread
        an email written from that follow-up started) is its own follow-up again, or Jev's question about it is answered no. Jev
        never asks about it again. Idempotent by request ID; nothing is sent."""
        if not isinstance(data, dict) or set(data) - {"enrollment_id", "source_key", "request_id"}:
            raise ValueError("Name the student's conversation to keep separate.")
        request_id = _request(data.get("request_id"))
        source = text(data.get("source_key"), "Conversation", 500, True)
        digest = _digest({"separate": data})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            enrollment = StudentNoteService._enrollment(db, data.get("enrollment_id"))
            previous = self._decided(db, request_id, digest)
            if previous:
                return previous
            row = join_decisions(db, [enrollment["id"]]).get((enrollment["id"], source))
            if not (row and row["decision"] in ("join", "ask")) and (enrollment["id"], source) not in written_threads(db, [enrollment["id"]]):
                raise Conflict("This conversation is not joined to another follow-up. Read the follow-up list again.")
            result = self._decide(db, enrollment["id"], source, "separate", "instructor", "", "", request_id, digest, actor)
            db.commit()
        return result

    def attention(self):
        unconfirmed = self._unconfirmed()  # ANSWERED: the badge counts mail written seconds ago as the page does
        with connect(self.path) as db:
            items = self._items(db, unconfirmed=unconfirmed)
        counts = {kind: sum(item["kind"] == kind and item["attention"] for item in items) for kind in STATES}
        # NEEDS ME (requirement, 2026-10-06, of a sidebar badge of 3 over a Needs me tab of 2): follow-ups count as the page's
        # Needs me tab counts them, each once: those that need attention and those whose suggestions wait for the instructor.
        # A possible wellbeing concern always needs attention (`triage.apply`), so "wellbeing" counts some of these again.
        if self.suggestions:
            counts["followup"] += sum(not item["attention"] for item in self.suggestions.waiting(items))
        return {**counts, "wellbeing": sum(bool(item.get("wellbeing")) for item in items)}

    def settled_mail(self, messages):
        """MAIL-FOLLOW-UP (requirement, 2026-10-10): of `messages`, [(enrollment, Gmail message ID)] naming
        the student's message that ends a thread in Mail's Students list, the Gmail message IDs whose conversation one of that
        student's follow-ups tracks while it shows anything but Needs my reply (Resolved, Waiting on student, Follow up later,
        To do), as the Follow-up page shows it: a newer message that reopens it is not settled, a thanks that leaves it Resolved
        is. The message is in the conversation by the record's captured copy of it, written by that student (its own thread,
        its Canvas conversation, or a conversation joined to that one: SAME MATTER); a message the record has not captured, or
        one no follow-up tracks, is never settled. A catalog read; nothing is saved or changed anywhere."""
        wanted = {(str(enrollment), str(message)) for enrollment, message in messages if enrollment and message}
        if not wanted:
            return set()
        with connect(self.path) as db:
            # PERFORMANCE: nearly every student has no follow-up; only those who have one are read further.
            tracked = {}
            for part in _chunks({enrollment for enrollment, _ in wanted}):
                for row in db.execute("SELECT enrollment_id,source_key FROM student_support_items WHERE kind='followup' AND source_key!='' "
                                      "AND enrollment_id IN (" + ",".join("?" * len(part)) + ")", part):
                    tracked.setdefault(row["enrollment_id"], set()).add(row["source_key"])
            wanted = {pair for pair in wanted if pair[0] in tracked}
            keys = {}
            for part in _chunks({message for _, message in wanted}):
                for row in db.execute("""SELECT m.channel,m.external_id,m.thread_key,r.context_json,l.enrollment_id
                        FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id
                        AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
                        JOIN comm_links l ON l.message_id=m.id AND l.role='author' JOIN course_terms t ON t.id=l.term_id
                        WHERE t.deleted_at IS NULL AND t.is_current=1 AND m.channel='gmail' AND r.author_role='student'
                        AND m.external_id IN (""" + ",".join("?" * len(part)) + ")", part):
                    if (row["enrollment_id"], row["external_id"]) in wanted:
                        keys[(row["enrollment_id"], row["external_id"])] = conversation_key(row, json.loads(row["context_json"]))
            if not keys:
                return set()
            saved = {(enrollment, key) for enrollment in {e for e, _ in keys} for key in tracked[enrollment]}
            joined = joins(db, saved | {(enrollment, key) for (enrollment, _), key in keys.items()})
            followed = {(enrollment, joined.get((enrollment, key), key)) for enrollment, key in saved}
            since, states, settled = self._baseline(db)["tracking_since"], {}, set()
            for (enrollment, message), key in sorted(keys.items()):
                key = joined.get((enrollment, key), key)
                if (enrollment, key) not in followed:
                    continue  # none of the student's follow-ups reads this conversation
                if enrollment not in states:
                    states[enrollment] = {joined.get((enrollment, item["source_key"]), item["source_key"]): item["state"]
                                          for item in self._items(db, enrollment, since, scoped=True)
                                          if item["kind"] == "followup" and item["id"] and item["source_key"]}
                if states[enrollment].get(key, "needs_reply") != "needs_reply":
                    settled.add(message)
        return settled

    def wellbeing_reviewed(self, data, actor="workspace"):
        """JEV WELLBEING: the instructor's "reviewed" of the possible concern Jev raised on ONE analysed conversation state (its
        exact receipt). Changes no follow-up, message, note or outside service. It holds until the student writes again
        (REVIEWED HOLDS, `triage.apply`); a newer message flagged again shows again."""
        if not isinstance(data, dict) or set(data) - {"enrollment_id", "source_key", "call_id", "request_id"}:
            raise ValueError("Choose the conversation and the concern to mark reviewed.")
        request_id = _request(data.get("request_id"))
        enrollment, source, call = (text(data.get(k), k.replace("_", " ").capitalize(), 300, True) for k in ("enrollment_id", "source_key", "call_id"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM student_support_wellbeing_reviews WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if (previous["enrollment_id"], previous["source_key"], previous["call_id"]) != (enrollment, source, call):
                    raise Conflict("This request ID already marked a different concern reviewed.")
                return {"reviewed": call}
            item = next((row for row in self._items(db, enrollment) if row["kind"] == "followup" and row["source_key"] == source), None)
            if not item or (item.get("wellbeing") or {}).get("call_id") != call:
                raise Conflict("This conversation changed or is no longer flagged. Read the follow-up list again.")
            db.execute("INSERT INTO student_support_wellbeing_reviews VALUES (?,?,?,?,?,?)",
                       (request_id, enrollment, source, call, actor, timestamp()))
            db.commit()
        return {"reviewed": call}

    # TERM POLICIES (requirement, 2026-10-03): their own policies per course term, read by every follow-up suggestion for that
    # term's students. Term-owned, immutable revisions, exact request IDs and the revision the page showed.

    @staticmethod
    def term_policy(db, term_id, include_areas=False):
        """The term's latest policy revision as {revision, content, actor, updated_at}; revision 0 when none was saved."""
        row = db.execute("SELECT revision,content,actor,created_at FROM student_support_term_policies WHERE term_id=? ORDER BY revision DESC LIMIT 1",
                         (term_id,)).fetchone()
        result = {"revision": row["revision"], "content": row["content"], "actor": row["actor"], "updated_at": row["created_at"]} if row \
            else {"revision": 0, "content": "", "actor": "", "updated_at": ""}
        if include_areas:
            areas = db.execute("SELECT areas_json FROM student_support_term_policy_areas WHERE term_id=? AND revision=?",
                               (term_id, result["revision"])).fetchone() if row else None
            result["areas"] = json.loads(areas[0]) if areas else legacy_areas(result["content"])
        return result

    def policies(self, include_areas=False):
        """Each current term with its policies (the follow-up page's Term policies window). Catalog only."""
        with connect(self.path) as db:
            terms = db.execute("SELECT * FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY code, year DESC, term").fetchall()
            # The window's read (areas=1) also carries each term's MEETING HOURS; the plain read stays as it was.
            return {"terms": [{"term_id": t["id"], "code": t["code"], "term_label": term_label(t),
                               **self.term_policy(db, t["id"], include_areas),
                               **({"meeting_hours": meeting_hours.read(db, t["id"])} if include_areas else {})} for t in terms],
                    "limit": POLICY_LIMIT, **({"areas": AREAS, "modes": MODES} if include_areas else {})}

    def save_policy(self, term_id, data, actor="workspace"):
        """Records text or per-area guidance against the displayed base revision (0 for none).

        Identical values record nothing; empty values clear the policies. Structured
        handling changes are revisioned even when their text projection is identical.
        """
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = _request(data.get("request_id"))
        structured = "areas" in data
        if structured and "content" in data:
            raise ValueError("Save either individual policy areas or the legacy policy text, not both.")
        areas = normalize_areas(data["areas"]) if structured else None
        content = policy_content(areas) if structured else text(data.get("content"), "Term policies", POLICY_LIMIT)
        if type(data.get("base_revision")) is not int:
            raise ValueError("Name the revision of the policies you edited as base_revision (0 for none).")
        digest = _digest({"term": term_id, "content": content, **({"areas": areas} if structured else {})})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone()
            if not term:
                raise NotFound("That term was not found.")
            earlier = db.execute("SELECT request_sha256 FROM student_support_term_policies WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
            if earlier:
                if earlier[0] != digest:
                    raise Conflict("This request ID already saved different policies.")
                return {"term_id": term_id, **self.term_policy(db, term_id, structured)}
            current = self.term_policy(db, term_id, structured)
            if data["base_revision"] != current["revision"]:
                raise Conflict("These term policies changed elsewhere. Read them again before saving.")
            if content != current["content"] or (structured and areas != current["areas"]):
                db.execute("INSERT INTO student_support_term_policies VALUES (?,?,?,?,?,?,?)",
                           (term_id, current["revision"] + 1, content, request_id, digest, actor, timestamp()))
                if structured:
                    db.execute("INSERT INTO student_support_term_policy_areas VALUES (?,?,?)",
                               (term_id, current["revision"] + 1, encoded(areas)))
            db.commit()
            return {"term_id": term_id, **self.term_policy(db, term_id, structured)}

    def save_meeting_hours(self, term_id, data, actor="workspace"):
        """MEETING HOURS: replace a term's weekly meeting windows, and with `makeup_rooms` the office-hours rooms that can seat a
        makeup (kept as they are when left out), against the revision the page showed (0 for none). Identical values record nothing;
        an empty list clears them. Exact request IDs."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = _request(data.get("request_id"))
        windows = meeting_hours.normalize(data.get("windows"))
        rooms = meeting_hours.normalize_rooms(data["makeup_rooms"]) if "makeup_rooms" in data else None
        if type(data.get("base_revision")) is not int:
            raise ValueError("Name the revision of the meeting hours you edited as base_revision (0 for none).")
        digest = _digest({"term": term_id, "windows": windows, **({"makeup_rooms": rooms} if rooms is not None else {})})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone():
                raise NotFound("That term was not found.")
            earlier = db.execute("SELECT request_sha256 FROM student_support_meeting_hours WHERE term_id=? AND request_id=?",
                                 (term_id, request_id)).fetchone()
            if earlier:
                if earlier[0] != digest:
                    raise Conflict("This request ID already saved different meeting hours.")
                return {"term_id": term_id, **meeting_hours.read(db, term_id)}
            from scheduling.service import current_use
            if current_use(db, term_id):  # SCHEDULING "USE THIS SCHEDULE": the Term schedule holds them now
                raise Conflict("This term's meeting hours are in its Term schedule now (the course page's Schedule). Change them there.")
            current = meeting_hours.read(db, term_id)
            if data["base_revision"] != current["revision"]:
                raise Conflict("These meeting hours changed elsewhere. Read them again before saving.")
            rooms = current["makeup_rooms"] if rooms is None else rooms
            if windows != current["windows"] or rooms != current["makeup_rooms"]:
                db.execute("""INSERT INTO student_support_meeting_hours(term_id,revision,windows_json,request_id,request_sha256,actor,
                    created_at,makeup_rooms_json) VALUES (?,?,?,?,?,?,?,?)""",
                           (term_id, current["revision"] + 1, encoded(windows), request_id, digest, actor, timestamp(), encoded(rooms)))
            db.commit()
            return {"term_id": term_id, **meeting_hours.read(db, term_id)}

    def people(self):
        """{enrollment ID: current student} from the verified roster picker, for student-facing views only."""
        return {student["enrollment_id"]: {**student, "term_id": group["term_id"], "code": group["code"], "term_label": group["term_label"]}
                for group in self.students.students()["groups"] for student in group["students"]}

    @staticmethod
    def _owner(db, owner):
        """Whose conversation a record is about: a student's term enrollment (`StudentNoteService._enrollment`), or for a term's
        coordinator key (A COORDINATOR'S FOLLOW-UP) the same shape for the term itself ({id: the key, term_id, code, ...}).
        Anyone else is not found."""
        term_id = coordinators.owner_term(owner)
        if not term_id:
            return StudentNoteService._enrollment(db, owner)
        row = db.execute("SELECT id term_id,code,term,term_other,year,deleted_at FROM course_terms WHERE id=?", (term_id,)).fetchone()
        if not row or row["deleted_at"]:
            raise NotFound("That coordinator was not found.")
        return {**dict(row), "id": owner}

    @staticmethod
    def _coordinator_people(db, owners):
        """A COORDINATOR'S FOLLOW-UP: {owner key: who the Follow-up page names} for the coordinator keys among `owners`, shaped as
        a roster student is (`people`) with `coordinator` true: the name the term's record holds (its latest coordinated
        revision, so a follow-up kept from a term no longer coordinated still names them), the course and the term, and the
        address only while the term is coordinated now (none to write to once the box is unticked)."""
        found = {}
        for owner in sorted(owner for owner in owners if coordinators.owner_term(owner)):
            term_id = coordinators.owner_term(owner)
            term = db.execute("SELECT id,code,term,term_other,year FROM course_terms WHERE id=?", (term_id,)).fetchone()
            who = db.execute("""SELECT name,email FROM course_term_coordinators WHERE term_id=? AND coordinated=1
                                ORDER BY revision DESC LIMIT 1""", (term_id,)).fetchone() if term else None
            now = coordinators.of_owner(db, owner)
            found[owner] = {"enrollment_id": owner, "coordinator": True, "name": who["name"] if who else "Course coordinator",
                            "email": now["email"] if now else "", "term_id": term_id, "code": term["code"] if term else "",
                            "term_label": term_label(term) if term else "", "sections": []}
        return found

    def name_appointments(self, events):
        """CALENDAR OFFICE HOURS (requirement, 2026-10-06): titles each
        office-hours appointment among assembled calendar `events` "Office hours: <roster name> · <purpose>" (`appointment_title`),
        in place, for the Calendar page, Home and Google. Call it outside a catalog transaction (it takes the roster lock to read
        names). A student no longer on a current roster keeps the title without a name. Returns `events`."""
        prefix = "office-hours:"
        ids = sorted({event["id"][len(prefix):] for event in events if event.get("source") == "office_hours" and str(event.get("id", "")).startswith(prefix)})
        if not ids:
            return events
        with connect(self.path) as db:
            owners = dict(db.execute(f"SELECT id, enrollment_id FROM student_support_items WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall())
        people = self.people()
        for event in events:
            if event.get("source") == "office_hours":
                name = (people.get(owners.get(str(event.get("id", ""))[len(prefix):])) or {}).get("name")
                if name:
                    event["title"] = appointment_title(event.get("purpose"), name)
        return events

    def _appointments(self, db, enrollment_id, since):
        """TEACHING-WORKFLOWS / PERFORMANCE: the same appointment rows and order as _items, without reading conversations."""
        rows = db.execute(LATEST + (" AND i.enrollment_id=?" if enrollment_id else " AND t.is_current=1")
                          + " AND i.kind='appointment' ORDER BY i.created_at,i.id",
                          (enrollment_id,) if enrollment_id else ()).fetchall()
        today = now().date().isoformat()
        items = [effective(row, {}, today, since) for row in rows]
        return sorted(items, key=lambda row: (not row["attention"], row.get("date") or "9999", row["updated_at"], row["id"]))

    def listing(self, kind=None, enrollment_id=None, answered=False):
        """`answered` (the Follow-up page's suggestions=1 read only; other reads stay byte-identical): each tracked conversation
        the instructor wrote in last carries `answered` [sent_at, message ID] of that message (WAITING ON), with `answered_mail`
        while it is an email written from it that the record has not captured yet (WRITTEN AT ONCE)."""
        if kind and kind not in STATES:
            raise ValueError("Choose followup or appointment.")
        unconfirmed = self._unconfirmed(enrollment_id) if answered and kind != "appointment" else []
        with connect(self.path) as db:
            if enrollment_id:
                self._owner(db, enrollment_id)
            since = self._baseline(db)["tracking_since"]
            items = (self._appointments(db, enrollment_id, since) if kind == "appointment"
                     else self._items(db, enrollment_id, since, answered, unconfirmed))
            named = self._coordinator_people(db, {item["enrollment_id"] for item in items})
        people = {**self.people(), **named} if named else self.people()
        return {"items": [{**item, "student": people.get(item["enrollment_id"], {"name": "Student in a past term"})}
                          for item in items if not kind or item["kind"] == kind], "today": now().date().isoformat(),
                "capture": self.communications.status(), **({"tracking_since": since} if since else {}),
                **({"jev": self.triage.jev.status()} if self.triage else {})}

    def _start_now_plan(self, db, data):
        if not isinstance(data, dict) or not isinstance(data.get("through"), str):
            raise ValueError("Supply a cutoff instant with its time zone.")
        try:
            moment = datetime.fromisoformat(data["through"].replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Supply a valid cutoff instant with its time zone.") from None
        if moment.tzinfo is None or moment > datetime.fromisoformat(timestamp()):
            raise ValueError("The cutoff must have a time zone and cannot be in the future.")
        through = utc(data["through"])
        baseline = self._baseline(db)
        if through < baseline["tracking_since"]:
            raise ValueError("The follow-up tracking cutoff cannot move backwards.")
        candidates = [item for item in self._items(db, since=baseline["tracking_since"], include_joined=True)
                      if item["kind"] == "followup" and item["state"] != "resolved"
                      and max(utc(item["updated_at"]), (item.get("observed") or [""])[0]) <= through]
        evidence = [[item.get(key) for key in ("id", "enrollment_id", "source_key", "revision", "state", "observed", "updated_at")]
                    for item in candidates]
        fingerprint = _digest([baseline, through, evidence])
        return {"through": through, "cleared": len(candidates), "tracked": sum(bool(item["id"]) for item in candidates),
                "suggestions": sum(not item["id"] for item in candidates), "fingerprint": fingerprint}, candidates

    def start_now_preview(self, data):
        with self.rosters.lock(), connect(self.path) as db:
            return self._start_now_plan(db, data)[0]

    def start_now(self, data, actor="workspace"):
        """Resolve only previewed old work and retain a cutoff for late historical captures."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = _request(data.get("request_id"))
        digest = _digest(data)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM student_support_baselines WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["request_sha256"] != digest:
                    raise Conflict("This request ID already records a different start-time decision.")
                return json.loads(previous["result_json"])
            if data.get("confirmation") != "start-now":
                raise ValueError("Confirm clearing follow-ups through this instant with start-now.")
            plan, candidates = self._start_now_plan(db, data)
            if data.get("fingerprint") != plan["fingerprint"]:
                raise Conflict("The follow-up queue changed. Preview the cutoff again before clearing it.")
            resolved = []
            for item in candidates:
                if not item["id"]:
                    continue  # Suggestions have no retained item; the cutoff suppresses them.
                row = db.execute("SELECT content_json FROM student_support_revisions WHERE item_id=? AND revision=?",
                                 (item["id"], item["revision"])).fetchone()
                content = {**json.loads(row["content_json"]), "state": "resolved"}
                if content.get('commitments'):
                    content['commitments'] = [{**c, 'state': 'cancelled', 'completion_note': 'Tracking cleared by Start from now.'}
                                              if c['state'] == 'open' else c for c in content['commitments']]
                if item["source_key"]:
                    content["observed"] = item["observed"]
                revision = item["revision"] + 1
                db.execute("INSERT INTO student_support_revisions VALUES (?,?,?,?,?)",
                           (item["id"], revision, encoded(content), actor, timestamp()))
                resolved.append({"id": item["id"], "revision": revision})
            result = {**plan, "resolved": resolved}
            db.execute("INSERT INTO student_support_baselines VALUES (?,?,?,?,?,?)",
                       (request_id, digest, plan["through"], encoded(result), actor, timestamp()))
            db.commit()
        return result

    @staticmethod
    def clean(kind, data):
        state = data.get("state", STATES[kind][0])
        if state not in STATES[kind]:
            raise ValueError("Choose a valid follow-up or appointment state.")
        content = {"title": text(data.get("title", ""), "Purpose", required=True), "state": state,
                   "details": text(data.get("details", ""), "Details", 5000),
                   "date": _date(data["date"]) if data.get("date") else ""}
        if kind == "followup":
            if state == "snoozed" and not content["date"]:
                raise ValueError("Choose when to follow up.")
            # WAITING ON (requirement, 2026-10-04): one line saying what the follow-up waits on. Kept only when given, so records without one are unchanged.
            step = text(data.get("next", ""), "Next step", NEXT_LIMIT)
            if "\n" in step or "\r" in step:
                raise ValueError("Write the next step on one line.")
            if step:
                content["next"] = step
        else:
            if not content["date"]:
                raise ValueError("Choose the appointment date.")
            for key in ("time", "end_time"):
                value = data.get(key)
                if not isinstance(value, str) or not HHMM.fullmatch(value):
                    raise ValueError("Choose start and end times on the campus clock.")
                content[key] = value
            if content["end_time"] <= content["time"]:
                raise ValueError("The appointment must end after it starts on the same day.")
            content["location"] = text(data.get("location", ""), "Location", 240)
        return content

    def save(self, data, item_id=None, actor="workspace"):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = _request(data.get("request_id"))
        digest = _digest({"item": item_id, "data": data})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(LATEST + " AND i.id=?", (item_id,)).fetchone() if item_id else None
            if item_id and not current:
                raise NotFound("That follow-up or appointment was not found.")
            if current and data.get("enrollment_id", current["enrollment_id"]) != current["enrollment_id"]:
                raise ValueError("A record keeps its original student enrollment.")
            if current and data.get("kind", current["kind"]) != current["kind"]:
                raise ValueError("A record keeps its original kind.")
            kind = current["kind"] if current else data.get("kind")
            if not isinstance(kind, str) or kind not in STATES:
                raise ValueError("Choose followup or appointment.")
            enrollment = self._owner(db, current["enrollment_id"] if current else data.get("enrollment_id"))
            if kind == "appointment" and coordinators.owner_term(enrollment["id"]):
                raise ValueError("An office-hours appointment is a meeting with a student.")
            previous = db.execute("SELECT * FROM student_support_requests WHERE term_id=? AND request_id=?", (enrollment["term_id"], request_id)).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise Conflict("This request ID already saved different details.")
                return {"id": previous["item_id"], "revision": previous["revision"]}
            if current and (type(data.get("base_revision")) is not int or data["base_revision"] != current["revision"]):
                raise Conflict("This record changed elsewhere. Read it again before saving.")
            content = self.clean(kind, data)
            source = current["source_key"] if current else text(data.get("source_key", ""), "Conversation", 500)
            # ACTION DESK: commitments are enrollment-owned content revisions. An ordinary edit keeps them.
            if kind == 'followup':
                from .commitments import normalize, project, is_open
                previous_tasks = json.loads(current['content_json']).get('commitments', []) if current else []
                if 'commitments' in data:
                    content['commitments'] = normalize(data['commitments'], previous_tasks, db, enrollment['id'], source, item_id)
                elif previous_tasks:
                    content['commitments'] = previous_tasks
                if content['state'] == 'resolved' and any(is_open(c) for c in project(db, content.get('commitments', []))):
                    raise Conflict('TEACHING-WORKFLOWS: finish or explicitly cancel the open commitments before resolving this follow-up.')
                if content['state'] == 'resolved' and current:
                    shown = next((i for i in self._items(db, enrollment['id']) if i['id'] == item_id), {})
                    if any(is_open(c) for owner in shown.get('related_commitments', []) for c in owner['commitments']):
                        raise Conflict('TEACHING-WORKFLOWS: a joined follow-up still has open commitments. Keep it separate to review them.')
            if kind == "appointment" and source:
                raise ValueError("Appointments cannot be linked to a conversation.")
            if source:
                thread = conversations(db, enrollment["id"]).get((enrollment["id"], conversation_of(db, source, enrollment["id"])))
                if not thread:
                    raise ValueError("That conversation does not belong to this student's term enrollment.")
                if not current:
                    # EMAIL REPLY JOINS / SAME MATTER: a follow-up recorded under a conversation joined to this one already tracks it.
                    keys = [r[0] for r in db.execute("SELECT source_key FROM student_support_items WHERE enrollment_id=? AND kind='followup' AND source_key!=''",
                                                     (enrollment["id"],))]
                    joined = {key[1]: found for key, found in joins(db, [(enrollment["id"], key) for key in keys + [source]]).items()}
                    if any(joined.get(key, key) == joined.get(source, source) for key in keys):
                        raise Conflict("This conversation is already tracked. Read the follow-up list again.")
                if "observed" not in data:
                    raise Conflict("This save did not say which student message it was prepared from. Read the conversation again before saving.")
                if list(thread["inbound"]) != data.get("observed"):
                    raise Conflict("A new student message arrived. Read it before saving this follow-up.")
                content["observed"] = list(thread["inbound"])
            ident, revision = (current["id"], current["revision"]) if current else (str(uuid.uuid4()), 0)
            if not current:
                db.execute("INSERT INTO student_support_items VALUES (?,?,?,?,?,?,?)", (ident, enrollment["term_id"], enrollment["id"], kind, source, actor, timestamp()))
            if not current or current["content_json"] != encoded(content):
                revision += 1
                db.execute("INSERT INTO student_support_revisions VALUES (?,?,?,?,?)", (ident, revision, encoded(content), actor, timestamp()))
            db.execute("INSERT INTO student_support_requests VALUES (?,?,?,?,?,?)", (enrollment["term_id"], request_id, digest, ident, revision, timestamp()))
            db.commit()
        return {"id": ident, "revision": revision}
