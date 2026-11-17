"""Messages to the students of chosen sections through Canvas's inbox (requirement, 2026-10-01).

WHO IT REACHES. The instructor ticks current sections. Each ticked section's linked Canvas course is read (its student enrollments,
no lock held) and each student there is matched to Faculty by UIN = Canvas SIS user ID exactly, as Compare roster does:
- an enrollment Canvas does not list as active (invited, inactive, completed): left out, with Canvas's state;
- on the current roster of a ticked section: reached;
- on the current roster of a section NOT ticked (a combined course, or a student Faculty has in another section): left out;
- dropped in Faculty: left out;
- on no roster of the term (a late add Canvas already has, or no SIS ID): reached when every section linking that course is
  ticked, else left out (they may belong to the section not ticked; never guessed).
A ticked section's current students whom Canvas does not list in its course are named as not reached. A student in two of the
courses gets ONE message, in their Faculty section's course (else the lowest course ID). The preview says every reason, and for
each course how many current students its ticked sections have (`roster`) and how many of them get the message (`roster_reached`).

HOW (`web.Client.send_message`). One request per Canvas course, as Canvas's own inbox sends one message to many people. By default
ONE GROUP conversation per course, every recipient in it (requirement, 2026-10-01): students see who else got it and may reply to all. With
`individual` (Canvas's "Send an individual message to each recipient" box, unticked by default) each student gets their OWN
conversation and replies come to the instructor alone. Canvas allows at most 100 people in a group conversation, so a course with
more recipients is sent individually, and the review says so.

RECEIPTS (migration `canvas/005`, WORKSPACE-owned like Mail's sends). The message and one delivery row per course (its recipients)
are committed BEFORE any request leaves; Canvas's answer per course is an outcome (`accepted`, `failed`, `uncertain`). A delivery
without an outcome was interrupted and is uncertain. Faculty never sends a message again: the same request ID returns its record,
and anything else is a new message the instructor writes and approves. Checking delivery reads Canvas and records nothing.

ATTACHMENTS (requirement, 2026-10-09). A message may carry up to MAX_FILES files, MAX_TOTAL bytes together: Faculty files named by the routes that serve them
(as Mail's are, MAIL-ATTACHMENTS: the Assistant's cards name them so, a file attached in its chat included) or files from the
instructor's computer sent with the window's preview and send. The review lists them and its fingerprint covers each one's digest.
Canvas takes a message's files only from the sender's own "conversation attachments" folder (never a course's files), so the send
uploads each file there once (`web.Client.upload_message_file`), recorded per file (migration `canvas/007`), and only when every
file is uploaded is any course sent, each naming the same files. A file Canvas refused, or did not answer for, sends nothing.

IN THE BACKGROUND, AND NEVER CUT OFF BY A RESTART (requirement, 2026-10-01). Once the receipts are committed the courses are sent by a thread of the web process, and the send
answers at once; the window follows the record (a course is `sending` while this process is still sending it). A stop waits for it
(canvas/stopping.py, with every other write to Canvas): new sends are refused (503) and a running send finishes, at most
stopping.STOP_WAIT seconds, before the process stops. A delivery still without an answer when nothing sends it any more (the process
was killed) is uncertain, as before.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from canonical_json import ascii_digest
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect
from seating import student_name, term_label

from . import web
from .service import _link

MAX_SUBJECT = 255     # Canvas's own limit
MAX_BODY = 20000
MAX_SECTIONS = 50
RECENT = 20           # sends listed on the page
PARALLEL = 4          # Canvas courses read at once, each its own use of the kept session
UIN = re.compile(r"\d{9}")
SKEW = timedelta(minutes=2)  # Canvas's clock against Faculty's when a delivery is checked
RESTARTING = "Faculty is restarting. Send the message again in a minute; nothing was sent."
MAX_FILES = 10           # ATTACHMENTS: files one message carries
MAX_TOTAL = 25_000_000   # bytes of all of them together


def fingerprint(subject, body, courses, attachments=()):
    """What the instructor approves: the words and exactly who gets them in which course (Canvas user IDs), and how (group or
    individual), and each attached file (name, type, size and digest; no key without files, so earlier reviews keep theirs)."""
    value = {"subject": subject, "body": body,
             "courses": [[c["course_id"], c["kind"], sorted(r["user_id"] for r in c["recipients"])] for c in courses]}
    if attachments:
        value["attachments"] = [[a["name"], a["type"], a["size"], a["sha256"]] for a in attachments]
    return ascii_digest(value)


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _words(text):
    return " ".join(str(text or "").split())


def _written_since(value, since):
    """A Canvas time at or after the send (less SKEW)."""
    when = _time(value)
    return bool(when and since and when >= since - SKEW)


class CanvasMessages:
    def __init__(self, canvas):
        self.canvas, self.rosters, self.path = canvas, canvas.rosters, canvas.path
        self._send_lock = threading.Lock()
        self.files = None  # ATTACHMENTS: app_files.AppFiles, set by the routes (canvas/api.py)

    # --- what this process is sending (canvas/stopping.py: a restart waits for it) -------------------------------------------
    @staticmethod
    def _key(message_id):
        return f"message:{message_id}"

    def busy(self):
        """The messages this process is sending now."""
        return [key.split(":", 1)[1] for key in self.canvas.writes.busy("message:")]

    def wait_idle(self, timeout):
        """True once no write to Canvas runs, False when `timeout` seconds pass first."""
        return self.canvas.writes.wait_idle(timeout)

    # --- the page ---------------------------------------------------------------------------------------------------------
    def view(self):
        """The current terms' sections with their linked Canvas course and current roster count, and the latest messages sent.
        Faculty's records only: Canvas is not contacted."""
        terms = []
        with self.rosters.lock(), connect(self.path) as db:
            for term in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL AND is_current=1 ORDER BY code, year DESC, term").fetchall():
                sections = []
                for section in db.execute("""SELECT s.id, s.number, (SELECT COUNT(*) FROM roster_current c WHERE c.section_id=s.id) students
                                             FROM course_sections s WHERE s.term_id=? ORDER BY s.number""", (term["id"],)).fetchall():
                    link = _link(db, section["id"])
                    sections.append({"section_id": section["id"], "number": section["number"], "students": section["students"],
                                     "course": {"course_id": link["course_id"], "name": link["name"]} if link else None})
                terms.append({"term_id": term["id"], "code": term["code"] or "", "term_label": term_label(term), "sections": sections})
            sent = [self._record(db, row) for row in db.execute(
                "SELECT * FROM canvas_messages ORDER BY created_at DESC, rowid DESC LIMIT ?", (RECENT,)).fetchall()]
        return {"terms": terms, "sent": sent, "canvas": self.canvas.status()}

    # --- who it reaches ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _request(data):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        subject, body, sections = data.get("subject"), data.get("body"), data.get("sections")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("Write a subject.")
        subject = " ".join(subject.split())  # Canvas shows a subject on one line
        if len(subject) > MAX_SUBJECT:
            raise ValueError(f"Canvas takes a subject of at most {MAX_SUBJECT} characters.")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Write the message.")
        body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(body) > MAX_BODY:
            raise ValueError(f"The message may be at most {MAX_BODY:,} characters.")
        if not isinstance(sections, list) or not sections or not all(isinstance(s, str) and s for s in sections):
            raise ValueError("Choose at least one section.")
        if len(set(sections)) > MAX_SECTIONS:
            raise ValueError(f"Choose at most {MAX_SECTIONS} sections.")
        individual = data.get("individual", False)
        if not isinstance(individual, bool):
            raise ValueError("Say whether each student gets their own message (true or false).")
        return {"subject": subject, "body": body, "sections": sorted(set(sections)), "individual": individual}

    def _faculty(self, section_ids):
        """Faculty's side, under the roster lifecycle lock: the ticked sections, every section of their terms with its linked course,
        and each membership of those terms (UIN, enrollment, dropped) with its name from the verified roster payload."""
        with self.rosters.lock():
            with connect(self.path) as db:
                chosen, term_ids = {}, set()
                for section_id in section_ids:
                    row = db.execute("""SELECT s.id, s.number, s.term_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id
                                        WHERE s.id=? AND t.deleted_at IS NULL AND t.is_current=1""", (section_id,)).fetchone()
                    if not row:
                        raise ValueError("A section you chose is not a current section any more. Open the window again.")
                    chosen[row["id"]] = row["number"]
                    term_ids.add(row["term_id"])
                term_ids = sorted(term_ids)
                terms = {t: db.execute("SELECT * FROM course_terms WHERE id=?", (t,)).fetchone() for t in term_ids}
                numbers, links = {}, {}
                for term_id in term_ids:
                    for section in db.execute("SELECT id, number FROM course_sections WHERE term_id=?", (term_id,)).fetchall():
                        numbers[section["id"]] = section["number"]
                        link = _link(db, section["id"])
                        if link:
                            links[section["id"]] = link
                marks = ",".join("?" * len(term_ids))
                members = [dict(r) for r in db.execute(f"""SELECT m.section_id, m.enrollment_id, e.uin, s.term_id, (d.enrollment_id IS NOT NULL) dropped
                    FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id JOIN student_enrollments e ON e.id=m.enrollment_id
                    LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id
                    WHERE s.term_id IN ({marks})""", term_ids).fetchall()]
            for term_id in {m["term_id"] for m in members}:
                with self.rosters.files(terms[term_id]) as root:
                    payload = self.rosters.read_payload(root, terms[term_id]["roster_initialized"])
                for member in members:
                    if member["term_id"] == term_id:
                        record = payload["sections"].get(member["section_id"], {}).get("students", {}).get(member["enrollment_id"], {})
                        member["name"] = student_name({**record, "id": member["enrollment_id"]})
        return chosen, numbers, links, members

    def _read(self, course_ids):
        """{course id: [student enrollment]} read at once with NO lock held. Signed out is a 401 (the page offers Sign in to Canvas)."""
        def one(course_id):
            with self.canvas.client() as client:
                return client.students(course_id)
        return self.canvas.read_each(sorted(course_ids, key=int), one, PARALLEL)

    def _attachments(self, data, uploads=()):
        """ATTACHMENTS: ([{route | from, name, type, size, sha256}], [bytes]) of the message's files, each read now (a refused one
        refuses the message whole)."""
        from gmail_mail.attachments import collect
        return collect(self.files, data.get("attachments") if isinstance(data, dict) else None, uploads,
                       max_files=MAX_FILES, max_total=MAX_TOTAL, blocked=frozenset(), service="Canvas")

    def plan(self, data, uploads=()):
        """Exactly who the message reaches in each Canvas course, who is left out and why, its files, and its fingerprint. Reads
        Canvas; writes nothing anywhere."""
        request = self._request(data)
        attachments, contents = self._attachments(data, uploads)
        chosen, numbers, links, members = self._faculty(request["sections"])
        label = lambda ids: sorted((numbers[s] for s in ids), key=lambda n: (len(str(n)), str(n)))
        courses = {}
        for section_id, link in links.items():
            course = courses.setdefault(link["course_id"], {"course_id": link["course_id"], "name": link["name"], "all": set(), "ticked": set()})
            course["all"].add(section_id)
            if section_id in chosen:
                course["ticked"].add(section_id)
        courses = {cid: c for cid, c in courses.items() if c["ticked"]}
        unlinked = [{"section": chosen[s], "students": sum(1 for m in members if m["section_id"] == s and not m["dropped"])}
                    for s in chosen if s not in links]
        roster = {}
        for member in members:
            entry = roster.setdefault(member["uin"], {"current": {}, "dropped": {}})
            entry["dropped" if member["dropped"] else "current"][member["section_id"]] = member
        canvas = self._read(courses) if courses else {}

        candidates, left_out, seen = {}, [], set()
        for course_id in sorted(courses, key=int):
            course = courses[course_id]
            people = {}
            for enrollment in canvas[course_id]:  # one entry per student: active when any of their enrollments is
                if not enrollment["user_id"]:
                    continue
                person = people.setdefault(enrollment["user_id"], {**enrollment, "states": set()})
                person["states"].add(enrollment["state"])
            for user_id, person in sorted(people.items(), key=lambda p: (p[1]["name"].casefold(), p[0])):
                uin = person["sis"] if UIN.fullmatch(person["sis"]) else ""
                faculty = roster.get(uin) if uin else None
                mine = {s: m for s, m in (faculty or {}).get("current", {}).items() if s in chosen}
                if mine or faculty:
                    seen.add(uin)
                where = {"course_id": course_id, "name": person["name"] or "A Canvas student", "uin": uin}
                if "active" not in person["states"]:
                    if mine or not faculty:
                        left_out.append({**where, "reason": f"Canvas lists the enrollment as {'/'.join(sorted(person['states'])) or 'not active'}"})
                    continue
                if mine:
                    section_id = min(mine, key=lambda s: str(numbers[s]))
                    candidates.setdefault(uin, []).append({**where, "user_id": user_id, "enrollment_id": mine[section_id]["enrollment_id"],
                                                               "home": links.get(section_id, {}).get("course_id") == course_id})
                elif faculty and faculty["current"]:
                    others = label(faculty["current"])
                    left_out.append({**where, "reason": f"on Section {', '.join(map(str, others))}’s roster, which you did not choose"})
                elif faculty and faculty["dropped"]:
                    left_out.append({**where, "reason": f"dropped from Section {', '.join(map(str, label(faculty['dropped'])))} in Faculty"})
                elif course["ticked"] == course["all"]:
                    candidates.setdefault(uin or f"canvas:{user_id}", []).append({**where, "user_id": user_id, "enrollment_id": "", "home": True, "only_canvas": True})
                else:
                    shared = label(course["all"] - course["ticked"])
                    left_out.append({**where, "reason": f"on no roster here, in a Canvas course shared with Section {', '.join(map(str, shared))}, which you did not choose"})

        reached = {cid: [] for cid in courses}
        # One message per student (by UIN, else Canvas user): in their own section's course when it is one of these.
        for found in candidates.values():
            pick = min(found, key=lambda c: (not c["home"], int(c["course_id"])))
            reached[pick["course_id"]].append(pick)
        uins = {m["enrollment_id"]: m["uin"] for m in members}
        reached_uins = {uins[r["enrollment_id"]] for c in reached.values() for r in c if r["enrollment_id"]}
        # Someone reached through one course is not also "left out" for an enrollment in another.
        left_out = [{k: v for k, v in x.items() if k != "uin"} for x in left_out if not (x["uin"] and x["uin"] in reached_uins)]
        # A ticked section's current students Canvas does not list in its course (an unlinked section is counted under `unlinked`).
        not_reached = sorted(({"name": m["name"], "section": numbers[m["section_id"]], "reason": "not in the section’s Canvas course"}
                              for m in members if m["section_id"] in chosen and m["section_id"] in links and not m["dropped"]
                              and m["uin"] not in reached_uins and m["uin"] not in seen),
                             key=lambda s: (s["name"].casefold(), str(s["section"])))
        # ALL STUDENTS IN A SECTION: each ticked section's current students, so a course can say how many of them the message
        # reaches (in whichever course). A reading says "all students in 507" only when the two counts are equal.
        current = {}
        for member in members:
            if member["section_id"] in chosen and not member["dropped"]:
                current.setdefault(member["section_id"], set()).add(member["uin"])
        planned = []
        for course_id in sorted(courses, key=int):
            recipients = sorted(reached[course_id], key=lambda r: (r["name"].casefold(), r["user_id"]))
            own = set().union(*(current.get(s, set()) for s in courses[course_id]["ticked"]))
            planned.append({"course_id": course_id, "name": courses[course_id]["name"], "sections": label(courses[course_id]["ticked"]),
                            "section_ids": sorted(courses[course_id]["ticked"]),
                            "roster": len(own), "roster_reached": len(own & reached_uins),
                            "recipients": [{"user_id": r["user_id"], "enrollment_id": r["enrollment_id"], "name": r["name"]} for r in recipients],
                            "only_canvas": sum(1 for r in recipients if r.get("only_canvas")),
                            "kind": "individual" if request["individual"] or len(recipients) > web.SYNC_MAX else "group",
                            "too_many_for_group": not request["individual"] and len(recipients) > web.SYNC_MAX,
                            "mode": "sync" if len(recipients) <= web.SYNC_MAX else "async"})
        return {**request, "courses": planned, "unlinked": sorted(unlinked, key=lambda u: str(u["section"])),
                "left_out": sorted(left_out, key=lambda x: (x["name"].casefold(), x["course_id"])), "not_reached": not_reached,
                "attachments": attachments, "contents": contents,
                "fingerprint": fingerprint(request["subject"], request["body"], planned, attachments)}

    def check(self, data, jev=None, people=()):
        """JEV SEND-CHECKS: advice on the message the review shows (its dates against the chosen sections' exams and due dates,
        a student named to everyone); never part of its fingerprint or of Send, and Canvas is not read for it."""
        from jev import send_checks
        request = self._request(data)
        with connect(self.path) as db:
            chosen = [dict(row) for row in db.execute(
                f"SELECT id,number,term_id FROM course_sections WHERE id IN ({','.join('?' * len(request['sections']))})", request["sections"])]
            if len(chosen) != len(request["sections"]):
                raise ValueError("Choose sections of your courses.")
            ids = {row["id"] for row in chosen}
            return send_checks.check(jev, db, owner="canvas:" + hashlib.sha256(json.dumps([request["subject"], request["body"], sorted(ids)]).encode()).hexdigest(),
                                     body=request["subject"] + "\n" + request["body"], terms={row["term_id"] for row in chosen},
                                     sections={row["number"] for row in chosen}, mass=True,
                                     people=[person for person in people if person.get("section_id") in ids])

    def preview(self, data, uploads=()):
        """What a send would do, for the review step. Canvas user IDs and enrollment IDs stay on the server."""
        plan = self.plan(data, uploads)
        names = {c["course_id"]: c["name"] for c in plan["courses"]}
        return {"subject": plan["subject"], "body": plan["body"], "fingerprint": plan["fingerprint"], "individual": plan["individual"],
                "recipients": sum(len(c["recipients"]) for c in plan["courses"]), "group_limit": web.SYNC_MAX,
                "courses": [{"course_id": c["course_id"], "name": c["name"], "sections": c["sections"], "recipients": len(c["recipients"]),
                             "kind": c["kind"], "too_many_for_group": c["too_many_for_group"],
                             "only_canvas": c["only_canvas"], "roster": c["roster"], "roster_reached": c["roster_reached"],
                             "students": [r["name"] for r in c["recipients"]]} for c in plan["courses"]],
                "unlinked": plan["unlinked"], "not_reached": plan["not_reached"],
                "left_out": [{"name": x["name"], "reason": x["reason"], "course": names.get(x["course_id"], "")} for x in plan["left_out"]],
                **({"attachments": [{k: a[k] for k in ("name", "type", "size", "route", "from") if k in a} for a in plan["attachments"]]}
                   if plan["attachments"] else {}),
                "canvas": self.canvas.settings.base}

    # --- sending ----------------------------------------------------------------------------------------------------------
    def send(self, data, actor="workspace", uploads=()):
        """Sends the previewed message: needs its `fingerprint`, a `request_id` and `confirmation: "send"`, checked here. The
        message and every course's recipients are recorded before the first request; Canvas's answer per course after it."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = valid_request_id(data.get("request_id"))
        if data.get("confirmation") != "send":
            raise ValueError("Confirm the send: the message goes only when you choose Send.")
        with self._send_lock:
            with connect(self.path) as db:
                earlier = db.execute("SELECT * FROM canvas_messages WHERE request_id=?", (request_id,)).fetchone()
                if not earlier and self.canvas.writes.closing:
                    raise ServiceUnavailable(RESTARTING)
                if earlier:
                    if earlier["fingerprint"] != data.get("fingerprint"):
                        raise Conflict("This send was already made with a different message. Close the window and write it again.")
                    return self._record(db, earlier)  # Never sent twice, whatever came of the first try.
            plan = self.plan(data, uploads)
            if data.get("fingerprint") != plan["fingerprint"]:
                raise Conflict("Who the message reaches (or its words) changed since you reviewed it. Review it again.")
            return self._start(request_id, plan, actor)

    # --- sent later (docs/decisions/mail.md MAIL-SEND-LATER) ---------------------------------------------------------------
    def approved(self, data):
        """What a message scheduled for later keeps: the reviewed plan (its words, exactly who it reaches in each course, its Faculty
        files by route) once its fingerprint is checked. Reads Canvas as the review does; writes nothing."""
        plan = self.plan(data)
        if data.get("fingerprint") != plan["fingerprint"]:
            raise Conflict("Who the message reaches (or its words) changed since you reviewed it. Review it again.")
        if not any(c["recipients"] for c in plan["courses"]):
            raise ValueError("Nobody in the sections you chose can be reached through Canvas.")
        return {**{key: plan[key] for key in ("subject", "body", "sections", "individual", "courses", "fingerprint")},
                "attachments": [{key: a[key] for key in ("route", "name", "type", "size", "sha256")} for a in plan["attachments"]]}

    def send_approved(self, request_id, kept, actor="workspace"):
        """Sends a message scheduled earlier exactly as it was approved (MAIL-SEND-LATER): to the recipients reviewed then, never read
        again. Each Faculty file is read now; one that changed since refuses the send (409). The same request ID returns its record."""
        request_id = valid_request_id(request_id)
        with self._send_lock:
            with connect(self.path) as db:
                earlier = db.execute("SELECT * FROM canvas_messages WHERE request_id=?", (request_id,)).fetchone()
                if earlier:
                    return self._record(db, earlier)
            if self.canvas.writes.closing:
                raise ServiceUnavailable(RESTARTING)
            routes = [{"route": a["route"], "name": a["name"]} for a in kept["attachments"]]
            attachments, contents = self._attachments({"attachments": routes} if routes else {})
            if fingerprint(kept["subject"], kept["body"], kept["courses"], attachments) != kept["fingerprint"]:
                raise Conflict("An attached file changed since you approved the message, so it was not sent.")
            return self._start(request_id, {**kept, "attachments": attachments, "contents": contents}, actor)

    def by_request(self, request_id):
        """The record of the message sent under this request ID, or None (MAIL-SEND-LATER). Faculty's records only."""
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_messages WHERE request_id=?", (str(request_id),)).fetchone()
            return self._record(db, row) if row else None

    def _start(self, request_id, plan, actor):
        """Records the message and each course's recipients, then sends them in the background (the caller holds the send lock)."""
        courses = [c for c in plan["courses"] if c["recipients"]]
        if not courses:
            raise ValueError("Nobody in the sections you chose can be reached through Canvas.")
        message_id, now = str(uuid.uuid4()), timestamp()
        deliveries = []
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO canvas_messages(id,request_id,fingerprint,subject,body_text,sections_json,actor,created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (message_id, request_id, plan["fingerprint"], plan["subject"], plan["body"], json.dumps(plan["sections"]), actor, now))
            for course in courses:
                delivery = str(uuid.uuid4())
                db.execute("INSERT INTO canvas_message_deliveries(id,message_id,course_id,course_name,sections_json,recipients_json,mode,kind,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (delivery, message_id, course["course_id"], course["name"], json.dumps(course["section_ids"]),
                            json.dumps(course["recipients"]), course["mode"], course["kind"], now))
                deliveries.append((delivery, course))
            for position, meta in enumerate(plan["attachments"]):  # ATTACHMENTS: what was approved, before any upload
                db.execute("INSERT INTO canvas_message_attachments(message_id,position,name,media_type,size,sha256,source) VALUES (?,?,?,?,?,?,?)",
                           (message_id, position, meta["name"], meta["type"], meta["size"], meta["sha256"], meta.get("route") or "computer"))
            # Registered before the commit is seen (the record never shows it interrupted); refused once Faculty is stopping.
            self.canvas.writes.begin(self._key(message_id), "a Canvas message", RESTARTING)
            try:
                db.commit()
            except BaseException:
                self._finished(message_id)
                raise
        try:
            files = list(zip(range(len(plan["attachments"])), plan["attachments"], plan["contents"]))
            threading.Thread(target=self._run, args=(message_id, deliveries, plan["subject"], plan["body"], files),
                             name=f"canvas-message-{message_id[:8]}", daemon=True).start()
        except BaseException:
            self._finished(message_id)
            raise
        return self.record(message_id)

    def _run(self, message_id, deliveries, subject, body, files=()):
        """The files first, then the courses one after another, each answer recorded as it comes (a thread of the web process)."""
        try:
            stopped, attachment_ids = "", []
            if files:
                found = self._upload(message_id, files)
                if isinstance(found, str):
                    stopped = found
                else:
                    attachment_ids = found
            for delivery, course in deliveries:
                if stopped:
                    outcome = ("failed", None, None, "", f"Not sent: {stopped}")
                else:
                    outcome = self._deliver(course, subject, body, attachment_ids)
                    if isinstance(outcome, str):
                        stopped, outcome = outcome, ("failed", 401, None, "", f"Not sent: {outcome}")
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO canvas_message_outcomes(delivery_id,status,http_status,conversations,batch_id,detail,created_at) VALUES (?,?,?,?,?,?,?)",
                               (delivery, *outcome, timestamp()))
                    db.commit()
        finally:
            self._finished(message_id)

    def _finished(self, message_id):
        self.canvas.writes.end(self._key(message_id))

    def _upload(self, message_id, files):
        """ATTACHMENTS: each file into the instructor's "conversation attachments" folder, its answer recorded as it comes: the Canvas
        file IDs, or why the message is not sent (a refusal, no answer, or the session Canvas no longer takes; the files after it are
        not tried). Never tried again: a new send uploads again."""
        def record(position, status, file_id="", http_status=None, detail=""):
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO canvas_message_uploads(message_id,position,status,canvas_file_id,http_status,detail,created_at) VALUES (?,?,?,?,?,?,?)",
                           (message_id, position, status, file_id, http_status, detail[:400], timestamp()))
                db.commit()
        found = []
        try:
            with self.canvas.client() as client:
                for position, meta, content in files:
                    try:
                        file_id = client.upload_message_file(meta["name"], meta["type"], content)
                    except web.SignedOut:
                        record(position, "failed", detail="Canvas asked you to sign in again.")
                        return "Canvas asked you to sign in again."
                    except web.CanvasError as error:
                        refused = error.refused
                        record(position, "failed" if refused else "uncertain", http_status=error.status, detail=str(error))
                        return (f"Canvas did not take the file “{meta['name']}” ({str(error)[:200]})." if refused else
                                f"the file “{meta['name']}” could not be uploaded to Canvas ({str(error)[:200]}).")
                    record(position, "uploaded", file_id)
                    found.append(file_id)
        except web.SignedOut:
            return "Canvas asked you to sign in again."
        except Exception as error:  # noqa: BLE001 - the record must say what happened, whatever it was
            return f"the files could not be uploaded to Canvas ({type(error).__name__})."
        return found

    def _deliver(self, course, subject, body, attachment_ids=()):
        """One course's request: (status, http status, conversations, batch ID, detail), or a str when Canvas refused the session
        (nothing was sent, and the courses after it are not tried)."""
        try:
            with self.canvas.client() as client:
                answer = client.send_message(course["course_id"], [r["user_id"] for r in course["recipients"]], subject, body,
                                             group=course["kind"] == "group", attachment_ids=attachment_ids)
        except web.SignedOut:
            return "Canvas asked you to sign in again."
        except web.CanvasError as error:
            if error.refused:
                return ("failed", error.status, None, "", str(error)[:400])
            return ("uncertain", error.status, None, "", f"{str(error)[:300]} Canvas may or may not have sent it; check its Sent folder.")
        except Exception as error:  # noqa: BLE001 - the record must say what happened, whatever it was
            return ("uncertain", None, None, "", f"Delivery could not be confirmed ({type(error).__name__}).")
        made, wanted = answer["conversations"], len(course["recipients"])
        if course["kind"] == "group":
            # One shared conversation is what was asked; a Canvas that sends instructors' messages one by one makes one per student.
            detail = "" if made in (None, 1) else f"Canvas made {made} conversations instead of one group conversation."
        else:
            detail = "" if made is None or made >= wanted else f"Canvas made {made} of {wanted} conversations."
        return ("accepted", answer["status"], made, answer["batch_id"], detail)

    # --- the record -------------------------------------------------------------------------------------------------------
    def record(self, message_id):
        """One message's record (Faculty's records only; what the window follows while it is sent)."""
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_messages WHERE id=?", (str(message_id),)).fetchone()
            if not row:
                raise NotFound("That Canvas message was not found.")
            return self._record(db, row)

    def _record(self, db, row):
        running = self.canvas.writes.running(self._key(row["id"]))
        courses = []
        for delivery in db.execute("SELECT * FROM canvas_message_deliveries WHERE message_id=? ORDER BY CAST(course_id AS INTEGER)", (row["id"],)).fetchall():
            outcome = db.execute("SELECT * FROM canvas_message_outcomes WHERE delivery_id=? ORDER BY id DESC LIMIT 1", (delivery["id"],)).fetchone()
            recipients = json.loads(delivery["recipients_json"])
            numbers = [r["number"] for r in (db.execute("SELECT number FROM course_sections WHERE id=?", (s,)).fetchone()
                                             for s in json.loads(delivery["sections_json"])) if r]
            courses.append({"course_id": delivery["course_id"], "name": delivery["course_name"], "recipients": len(recipients),
                            "sections": sorted(numbers, key=lambda n: (len(str(n)), str(n))),
                            "status": outcome["status"] if outcome else "sending" if running else "uncertain",
                            "mode": delivery["mode"], "kind": delivery["kind"],
                            "conversations": outcome["conversations"] if outcome else None, "batch": bool(outcome and outcome["batch_id"]),
                            "detail": (outcome["detail"] if outcome else "" if running else
                                       "This send was interrupted; check Canvas's Sent folder. Faculty never sends it again.")})
        files = []
        for meta in db.execute("SELECT * FROM canvas_message_attachments WHERE message_id=? ORDER BY position", (row["id"],)).fetchall():
            upload = db.execute("SELECT status,detail FROM canvas_message_uploads WHERE message_id=? AND position=? ORDER BY id DESC LIMIT 1",
                                (row["id"], meta["position"])).fetchone()
            files.append({"name": meta["name"], "type": meta["media_type"], "size": meta["size"],
                          "from": "computer" if meta["source"] == "computer" else "faculty",
                          "upload": upload["status"] if upload else "uploading" if running else "not_uploaded",
                          **({"detail": upload["detail"]} if upload and upload["detail"] else {})})
        statuses = {c["status"] for c in courses}
        status = ("sending" if "sending" in statuses else "accepted" if statuses == {"accepted"} else "failed" if statuses == {"failed"}
                  else "uncertain" if statuses == {"uncertain"} else "mixed")
        return {"id": row["id"], "request_id": row["request_id"], "subject": row["subject"], "body": row["body_text"], "sent_at": row["created_at"],
                "status": status, "recipients": sum(c["recipients"] for c in courses if c["status"] == "accepted"),
                "planned": sum(c["recipients"] for c in courses), "courses": courses, **({"attachments": files} if files else {})}

    def delivery(self, message_id):
        """For each course Canvas accepted (or did not answer), whether Canvas is still delivering it and how many recipients now
        have THIS message: a sent conversation of its subject holding a message of its words written since the send (by name,
        those who do not). Reads Canvas; records nothing."""
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_messages WHERE id=?", (str(message_id),)).fetchone()
            if not row:
                raise NotFound("That Canvas message was not found.")
            record = self._record(db, row)
            deliveries = {d["course_id"]: (d, db.execute("SELECT * FROM canvas_message_outcomes WHERE delivery_id=? ORDER BY id DESC LIMIT 1",
                                                         (d["id"],)).fetchone())
                          for d in db.execute("SELECT * FROM canvas_message_deliveries WHERE message_id=?", (row["id"],)).fetchall()}
        since = _time(row["created_at"])
        found = []
        try:
            with self.canvas.client() as client:
                running = client.message_batches()
                for course_id, (delivery, outcome) in sorted(deliveries.items(), key=lambda d: int(d[0])):
                    recipients = json.loads(delivery["recipients_json"])
                    entry = {"course_id": course_id, "name": delivery["course_name"], "recipients": len(recipients)}
                    if outcome and outcome["status"] == "failed":
                        found.append({**entry, "state": "not_sent"})
                        continue
                    # Accepted, or uncertain (no answer): what Canvas's Sent folder holds says whether it went.
                    if outcome and outcome["batch_id"] and outcome["batch_id"] in running:
                        found.append({**entry, "state": "sending", "completion": running[outcome["batch_id"]]})
                        continue
                    wanted = {r["user_id"] for r in recipients}
                    # The subject and time only say which conversations could hold it: an earlier message of the same subject, or a
                    # later reply in an older conversation, matches them too (2026-10-01 audit). The message itself decides.
                    could = [c for c in client.sent_conversations(course_id)
                             if c["subject"] == row["subject"] and set(c["audience"]) & wanted and _written_since(c["last_authored_at"], since)]
                    have = self._holding(could, row["body_text"], since)
                    missing = sorted((r["name"] for r in recipients if r["user_id"] not in have), key=str.casefold)
                    delivered = len(recipients) - len(missing)
                    found.append({**entry, "state": "delivered" if not missing else "partly" if delivered else "none",
                                  "delivered": delivered, "missing": missing})
        except web.CanvasError as error:
            self.canvas._canvas(error)
        return {**record, "delivery": found}

    def _holding(self, conversations, body, since):
        """The recipients (Canvas user IDs) of those of `conversations` that hold a message of these words written since the send,
        each conversation's messages read once (never marked read), PARALLEL at a time."""
        words = _words(body)
        def holds(chunk):
            found = set()
            with self.canvas.client() as client:
                for conversation in chunk:
                    messages = client.conversation(conversation["id"])["messages"]
                    if any(_words(m["body"]) == words and _written_since(m["created_at"], since) for m in messages):
                        found.update(conversation["audience"])
            return found
        chunks = [conversations[i::PARALLEL] for i in range(PARALLEL) if conversations[i::PARALLEL]]
        if not chunks:
            return set()
        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            return set().union(*pool.map(holds, chunks))
