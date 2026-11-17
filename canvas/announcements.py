"""Announcements in the Canvas courses of chosen sections (requirement, 2026-10-15).

WHERE IT GOES. The instructor (or the Assistant's card) ticks current sections. Each ticked section's linked Canvas course gets ONE
announcement, course-wide, as Canvas's own Announcements page posts it: every student of that course sees it, and Canvas notifies
them by its own notification settings. A Canvas course several sections share is posted once; a section sharing it that was NOT
ticked sees it too, and the preview says so (`also`), never hidden. A ticked section with no linked course gets nothing (`unlinked`).
Canvas is not read for the preview: who sees an announcement is the course's, not a list Faculty chooses.

THE WORDS. A title (Canvas's limit, 255) and the message as plain text: Faculty escapes it and makes paragraphs of blank-line
separated blocks, a line break of each single one. The fingerprint covers the title, the text and the Canvas courses.

RECEIPTS (migration `canvas/009`, WORKSPACE-owned like messages). The announcement and one post row per course are committed BEFORE
any request leaves; Canvas's answer per course is an outcome (`posted` with the topic's ID and link, `failed`, `uncertain`). A post
without an outcome was interrupted and is uncertain. Faculty never posts an announcement again: the same request ID returns its
record, and anything else is a new announcement the instructor approves. Posting runs in the request (one short request per course)
and is registered with canvas/stopping.py, so a restart waits for it.
"""
from __future__ import annotations

import html
import json
import re
import threading
import uuid

from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from canonical_json import ascii_digest
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect
from seating import term_label

from . import web
from .service import _link

MAX_TITLE = 255   # Canvas's own limit
MAX_TEXT = 20000
MAX_SECTIONS = 50
RECENT = 20
RESTARTING = "Faculty is restarting. Post the announcement again in a minute; nothing was posted."


def as_html(text):
    """The plain text as Canvas's rich content: escaped, a paragraph per blank-line separated block, a <br> per line break."""
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    return "".join("<p>" + "<br>".join(html.escape(line) for line in block.split("\n")) + "</p>" for block in blocks)


def fingerprint(title, text, courses):
    value = {"title": title, "text": text, "courses": sorted(c["course_id"] for c in courses)}
    return ascii_digest(value)


def _numbers(values):
    return sorted(values, key=lambda n: (len(str(n)), str(n)))


class CanvasAnnouncements:
    def __init__(self, canvas):
        self.canvas, self.rosters, self.path = canvas, canvas.rosters, canvas.path
        self._post_lock = threading.Lock()

    @staticmethod
    def _key(announcement_id):
        return f"announcement:{announcement_id}"

    # --- the page ---------------------------------------------------------------------------------------------------------
    def view(self):
        """The current terms' sections with their linked Canvas course, and the latest announcements posted. Faculty's records only."""
        terms = []
        with self.rosters.lock(), connect(self.path) as db:
            for term in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL AND is_current=1 ORDER BY code, year DESC, term").fetchall():
                sections = []
                for section in db.execute("SELECT id, number FROM course_sections WHERE term_id=? ORDER BY number", (term["id"],)).fetchall():
                    link = _link(db, section["id"])
                    sections.append({"section_id": section["id"], "number": section["number"],
                                     "course": {"course_id": link["course_id"], "name": link["name"]} if link else None})
                terms.append({"term_id": term["id"], "code": term["code"] or "", "term_label": term_label(term), "sections": sections})
            posted = [self._record(db, row) for row in db.execute(
                "SELECT * FROM canvas_announcements ORDER BY created_at DESC, rowid DESC LIMIT ?", (RECENT,)).fetchall()]
        return {"terms": terms, "posted": posted, "canvas": self.canvas.status()}

    # --- what it would do -------------------------------------------------------------------------------------------------
    @staticmethod
    def _request(data):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        title, text, sections = data.get("title"), data.get("message"), data.get("sections")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Write a title.")
        title = " ".join(title.split())
        if len(title) > MAX_TITLE:
            raise ValueError(f"Canvas takes a title of at most {MAX_TITLE} characters.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Write the announcement.")
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(text) > MAX_TEXT:
            raise ValueError(f"The announcement may be at most {MAX_TEXT:,} characters.")
        if not isinstance(sections, list) or not sections or not all(isinstance(s, str) and s for s in sections):
            raise ValueError("Choose at least one section.")
        if len(set(sections)) > MAX_SECTIONS:
            raise ValueError(f"Choose at most {MAX_SECTIONS} sections.")
        return {"title": title, "message": text, "sections": sorted(set(sections))}

    def plan(self, data):
        """Each Canvas course the announcement is posted in, the ticked sections without a course, and its fingerprint. Writes
        nothing; Canvas is not contacted."""
        request = self._request(data)
        with self.rosters.lock(), connect(self.path) as db:
            chosen = {}
            for section_id in request["sections"]:
                row = db.execute("""SELECT s.id, s.number, s.term_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id
                                    WHERE s.id=? AND t.deleted_at IS NULL AND t.is_current=1""", (section_id,)).fetchone()
                if not row:
                    raise ValueError("A section you chose is not a current section any more. Choose again.")
                chosen[row["id"]] = row
            courses, unlinked = {}, []
            for section_id, row in chosen.items():
                link = _link(db, section_id)
                if not link:
                    unlinked.append(row["number"])
                    continue
                course = courses.setdefault(link["course_id"], {"course_id": link["course_id"], "name": link["name"], "ticked": set(), "also": set()})
                course["ticked"].add(section_id)
            # A section of a current term sharing one of these courses that was not ticked sees the announcement too.
            for section in db.execute("""SELECT s.id, s.number FROM course_sections s JOIN course_terms t ON t.id=s.term_id
                                         WHERE t.deleted_at IS NULL AND t.is_current=1""").fetchall():
                if section["id"] in chosen:
                    continue
                link = _link(db, section["id"])
                if link and link["course_id"] in courses:
                    courses[link["course_id"]]["also"].add(section["number"])
        planned = [{"course_id": c["course_id"], "name": c["name"], "section_ids": sorted(c["ticked"]),
                    "sections": _numbers(chosen[s]["number"] for s in c["ticked"]), "also": _numbers(c["also"])}
                   for c in sorted(courses.values(), key=lambda c: int(c["course_id"]))]
        return {**request, "courses": planned, "unlinked": _numbers(unlinked),
                "fingerprint": fingerprint(request["title"], request["message"], planned)}

    def preview(self, data):
        plan = self.plan(data)
        return {"title": plan["title"], "message": plan["message"], "fingerprint": plan["fingerprint"],
                "courses": [{k: c[k] for k in ("course_id", "name", "sections", "also")} for c in plan["courses"]],
                "unlinked": plan["unlinked"], "canvas": self.canvas.settings.base}

    # --- posting ----------------------------------------------------------------------------------------------------------
    def post(self, data, actor="workspace"):
        """Posts the previewed announcement: needs its `fingerprint`, a `request_id` and `confirmation: "post"`. The announcement
        and every course's post are recorded before the first request; Canvas's answer per course after it."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = valid_request_id(data.get("request_id"))
        if data.get("confirmation") != "post":
            raise ValueError("Confirm the announcement: it is posted only when you choose Post.")
        with self._post_lock:
            with connect(self.path) as db:
                earlier = db.execute("SELECT * FROM canvas_announcements WHERE request_id=?", (request_id,)).fetchone()
                if earlier:
                    if earlier["fingerprint"] != data.get("fingerprint"):
                        raise Conflict("This announcement was already posted with different words. Write it again.")
                    return self._record(db, earlier)  # Never posted twice, whatever came of the first try.
            if self.canvas.writes.closing:
                raise ServiceUnavailable(RESTARTING)
            plan = self.plan(data)
            if data.get("fingerprint") != plan["fingerprint"]:
                raise Conflict("The announcement or the Canvas courses it goes to changed since you reviewed it. Review it again.")
            if not plan["courses"]:
                raise ValueError("None of the sections you chose has a Canvas course linked.")
            announcement_id, now, body = str(uuid.uuid4()), timestamp(), as_html(plan["message"])
            posts = []
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO canvas_announcements(id,request_id,fingerprint,title,message_text,message_html,sections_json,actor,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (announcement_id, request_id, plan["fingerprint"], plan["title"], plan["message"], body,
                            json.dumps(plan["sections"]), actor, now))
                for course in plan["courses"]:
                    post_id = str(uuid.uuid4())
                    db.execute("INSERT INTO canvas_announcement_posts(id,announcement_id,course_id,course_name,sections_json,created_at) VALUES (?,?,?,?,?,?)",
                               (post_id, announcement_id, course["course_id"], course["name"], json.dumps(course["section_ids"]), now))
                    posts.append((post_id, course))
                self.canvas.writes.begin(self._key(announcement_id), "a Canvas announcement", RESTARTING)
                try:
                    db.commit()
                except BaseException:
                    self.canvas.writes.end(self._key(announcement_id))
                    raise
            try:
                stopped = ""
                for post_id, course in posts:
                    outcome = ("failed", None, "", "", f"Not posted: {stopped}") if stopped else self._post(course, plan["title"], body)
                    if isinstance(outcome, str):
                        stopped, outcome = outcome, ("failed", 401, "", "", f"Not posted: {outcome}")
                    with self.rosters.lock(), connect(self.path) as db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("INSERT INTO canvas_announcement_outcomes(post_id,status,http_status,topic_id,url,detail,created_at) VALUES (?,?,?,?,?,?,?)",
                                   (post_id, *outcome, timestamp()))
                        db.commit()
            finally:
                self.canvas.writes.end(self._key(announcement_id))
            return self.record(announcement_id)

    def _post(self, course, title, body):
        """One course's request: (status, http status, topic ID, link, detail), or a str when Canvas refused the session (nothing
        was posted, and the courses after it are not tried)."""
        try:
            with self.canvas.client() as client:
                answer = client.post_announcement(course["course_id"], title, body)
        except web.SignedOut:
            return "Canvas asked you to sign in again."
        except web.CanvasError as error:
            if error.refused:
                return ("failed", error.status, "", "", str(error)[:400])
            return ("uncertain", error.status, "", "", f"{str(error)[:300]} Canvas may or may not have posted it; check the course's Announcements.")
        except Exception as error:  # noqa: BLE001 - the record must say what happened, whatever it was
            return ("uncertain", None, "", "", f"The post could not be confirmed ({type(error).__name__}).")
        return ("posted", answer["status"], answer["id"], answer["url"], "")

    # --- the record -------------------------------------------------------------------------------------------------------
    def record(self, announcement_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_announcements WHERE id=?", (str(announcement_id),)).fetchone()
            if not row:
                raise NotFound("That Canvas announcement was not found.")
            return self._record(db, row)

    def _record(self, db, row):
        running = self.canvas.writes.running(self._key(row["id"]))
        courses = []
        for post in db.execute("SELECT * FROM canvas_announcement_posts WHERE announcement_id=? ORDER BY CAST(course_id AS INTEGER)", (row["id"],)).fetchall():
            outcome = db.execute("SELECT * FROM canvas_announcement_outcomes WHERE post_id=? ORDER BY id DESC LIMIT 1", (post["id"],)).fetchone()
            numbers = [r["number"] for r in (db.execute("SELECT number FROM course_sections WHERE id=?", (s,)).fetchone()
                                             for s in json.loads(post["sections_json"])) if r]
            courses.append({"course_id": post["course_id"], "name": post["course_name"], "sections": _numbers(numbers),
                            "status": outcome["status"] if outcome else "posting" if running else "uncertain",
                            "url": outcome["url"] if outcome else "",
                            "detail": (outcome["detail"] if outcome else "" if running else
                                       "This post was interrupted; check the course's Announcements. Faculty never posts it again.")})
        statuses = {c["status"] for c in courses}
        status = ("posting" if "posting" in statuses else "posted" if statuses == {"posted"} else "failed" if statuses == {"failed"}
                  else "uncertain" if statuses == {"uncertain"} else "mixed")
        return {"id": row["id"], "request_id": row["request_id"], "title": row["title"], "message": row["message_text"],
                "posted_at": row["created_at"], "status": status, "courses": courses}
