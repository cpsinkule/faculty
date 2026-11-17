"""SCHEDULING "SEND TO AIM" (requirement, 2026-10-27). The one place Faculty
writes to AIM, and only from the instructor's Send to AIM on the DR quizzes tab: Faculty signs in as Import from AIM does (the saved
school sign-in, the instructor's Duo approval, AIM's FERPA page continued as their acknowledgment), then on its own thread:

1. ADDS each ready DR quiz date AIM does not list yet (CLASS SCHEDULE: DR QUIZZES): on its section's List Exam Dates page, type
   Quiz, the quiz's day, the base length (20 minutes; AIM adds each student's extra time) and the time 10 minutes after that
   section's class ends that day (`sitting`; requirement, 2026-10-27). AIM's Exam Dates list read
   again confirms it; a confirmed date (or one AIM already listed) records the quiz's "Prescheduled in AIM" mark, which emails the
   section's DR quiz takers to register (DR QUIZ NOTICES; that email still tells the day only).
2. UPLOADS the quiz's accessible copy (QUIZZES "ACCESSIBLE COPY") to the students' requests AIM lists for that quiz on Upcoming
   Exams (AIM attaches files to requests, not to the date, so a quiz has nothing to upload to until a student registers; the next
   Send uploads to new requests). AIM's count of exams without files, read again, confirms it.
3. EXPORTS the DR bookings and imports them itself (`import_bookings`), as Import from AIM's review would.

A send starts from the instructor: Send to AIM on the DR quizzes tab or Home's card, or by itself on a quiz's draft page right after
its approval (quiz-drafts.js), never on a timer, since TAMU's Duo verified push shows its code only in the window.

Every write is recorded (migration `scheduling/005`) BEFORE it is posted and its outcome after. An uncertain write is never posted
again by itself (AGENTS.md OUTSIDE SERVICES): a later read showing it in AIM settles it, else it waits for the instructor's Retry. A
page that is not as mapped (2026-10-27, read only in the instructor's AIM) stops the send with the reason; nothing is guessed.
Names on AIM's pages stay in the browser's memory: a request is kept only as the sha256 of its row."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
import re
import uuid
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from request_ids import REQUEST
from rosters import connect

from . import clock
from .dr_quiz_notices import _marks, _record, _state
from .class_schedule import TermRead
from .dr_quizzes import _span, term_quizzes
from .service import ScheduleService, _has, request_digest

LOG = logging.getLogger("faculty.aim")
PREFIX = "ctl00$ctl00$ctl00$MainContent$MainContent$MainContent$"
DATES_PAGE = "Testing.aspx?Step=Dates"
UPCOMING_PAGE = "Testing.aspx?Step=Exams"
FORM = PREFIX + "PnlInstruction$GFormExams$"
TYPE, DATE, LENGTH = FORM + "DDLType$DDLInput", FORM + "TxtBoxDate$TxtBoxInput", FORM + "TxtBoxLength$TxtBoxInput"
HOUR, MINUTE = FORM + "DDLTime$GDDLCustomTime$DDLHour", FORM + "DDLTime$GDDLCustomTime$DDLMinute"
SAVE = FORM + "BtnAddType"
UPLOAD = PREFIX + "GFormUpload$"
UPLOAD_TITLE, UPLOAD_FILE, UPLOAD_BUTTON = UPLOAD + "TxtBoxUploadTitle$TxtBoxInput", UPLOAD + "TxtBoxFile$TxtBoxFile", UPLOAD + "BtnUpload"
WITHOUT_FILES = re.compile(r"Number of Exams without Files Uploaded:?\s*(\d+)", re.I)
QUIZ_TYPE = "Quiz"
DATE_COLUMNS, REQUEST_COLUMNS = ("Section", "Type", "Exam Date"), ("Section", "Type", "Exam Date", "Full Name")
UNCERTAIN = "Faculty posted this to AIM but no read of AIM confirmed it. Check AIM, then choose Retry to let the next Send try again."
HELD = "An earlier write of this waits for your Retry."


class Stopped(Exception):
    """AIM's page is not as Faculty knows it, or answered otherwise: the send stops with this reason and posts nothing more."""


# -- AIM's pages ---------------------------------------------------------------------------------------------------------------------
class _Tables(HTMLParser):
    """Every table row of a page: its cells' text, and the links and inputs inside each cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {key: "" if value is None else value for key, value in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "tr":
            self._row = []
            self.rows.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"tag": tag, "text": [], "links": [], "inputs": []}
            self._row.append(self._cell)
        elif tag == "a" and self._cell is not None:
            self._cell["links"].append({"href": a.get("href", ""), "text": []})
        elif tag == "input" and self._cell is not None:
            self._cell["inputs"].append({"name": a.get("name", ""), "type": a.get("type", "text").lower(), "value": a.get("value", "on")})

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            self._cell = None
        elif tag == "tr":
            self._row = self._cell = None

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell["text"].append(data)
            if self._cell["links"]:
                self._cell["links"][-1]["text"].append(data)


def _words(value):
    return " ".join("".join(value).split())


def tables(markup):
    """[[{text, links: [{href, text}], inputs: [{name, type, value}]}]] of every row of every table of `markup`."""
    parser = _Tables()
    parser.feed(markup)
    parser.close()
    return [[{**cell, "text": _words(cell["text"]), "links": [{**link, "text": _words(link["text"])} for link in cell["links"]]}
             for cell in row] for row in parser.rows if row]


def _headed(markup, needed):
    """The rows under the first header row naming every column in `needed`, as dicts by header name ('' columns by position)."""
    rows, found, header = tables(markup), [], None
    for row in rows:
        names = [cell["text"] for cell in row]
        if header is None:
            if set(needed) <= set(names):
                header = names
            continue
        if len(row) != len(header) or all(cell["tag"] == "th" for cell in row):
            continue
        found.append({name or f"#{index}": cell for index, (name, cell) in enumerate(zip(header, row))})
    if header is None:
        raise Stopped(f"AIM's page has no table with {', '.join(needed)} where Faculty expects one.")
    return found


def iso(value):
    """AIM's MM/DD/YYYY as YYYY-MM-DD, or ''."""
    found = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", value or "")
    if not found:
        return ""
    try:
        return dt.date(int(found.group(3)), int(found.group(1)), int(found.group(2))).isoformat()
    except ValueError:
        return ""


def aim_day(day):
    value = dt.date.fromisoformat(day)
    return f"{value:%m/%d/%Y}"


def exam_dates(markup, base):
    """({(section, type casefolded, YYYY-MM-DD)}, {section: its List Exam Dates page}) of AIM's Exam Dates list
    (Testing.aspx?Step=Dates). Each row's Modify, Duplicate and Delete links carry its section's course (CID) and ID, the same for
    every row of a section; that section's List Exam Dates page, with the form that adds a date, is
    InstructionAdd.aspx?Step=ExamTypes&From=Class&CID=..&ID=.. (mapped 2026-10-27)."""
    listed, pages = set(), {}
    for row in _headed(markup, DATE_COLUMNS):
        section, day = row["Section"]["text"], iso(row["Exam Date"]["text"])
        if day:
            listed.add((section, row["Type"]["text"].casefold(), day))
        for cell in row.values():
            for link in cell["links"]:
                query = dict(parse_qsl(urlsplit(html.unescape(link["href"])).query))
                if re.search(r"InstructionAdd\.aspx", link["href"], re.I) and query.get("CID") and query.get("ID"):
                    page = urljoin(base, "InstructionAdd.aspx?" + urlencode({"Step": "ExamTypes", "From": "Class", "CID": query["CID"], "ID": query["ID"]}))
                    if pages.setdefault(section, page) != page:
                        raise Stopped(f"AIM's Exam Dates list names two courses for section {section}; nothing was sent.")
    return listed, pages


def requests(markup):
    """AIM's Upcoming Exams rows: [{box, section, type, date, digest}], `box` the row's checkbox name; `digest` the sha256 of the
    row's section, type, date, name and request day (the name itself is never returned)."""
    found = []
    for row in _headed(markup, REQUEST_COLUMNS):
        boxes = [field["name"] for cell in row.values() for field in cell["inputs"] if field["type"] == "checkbox" and field["name"].endswith("$ChkBox")]
        if len(boxes) != 1:
            continue
        section, kind, date = row["Section"]["text"], row["Type"]["text"], iso(row["Exam Date"]["text"])
        requested = row["Requested On"]["text"] if "Requested On" in row else ""
        digest = hashlib.sha256("|".join((section, kind.casefold(), date, row["Full Name"]["text"].casefold(), requested)).encode()).hexdigest()
        found.append({"box": boxes[0], "section": section, "type": kind, "date": date, "digest": digest})
    return found


def without_files(markup):
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I))).split())
    found = WITHOUT_FILES.search(text)
    return int(found.group(1)) if found else None


class _Options(HTMLParser):
    def __init__(self, name):
        super().__init__(convert_charrefs=True)
        self.name, self.options, self._inside, self._option = name, [], False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "select":
            self._inside = a.get("name") == self.name
        elif tag == "option" and self._inside:
            self._option = [a.get("value"), []]
            self.options.append(self._option)

    def handle_endtag(self, tag):
        if tag == "select":
            self._inside = False
        if tag in ("option", "select"):
            self._option = None

    def handle_data(self, data):
        if self._option is not None:
            self._option[1].append(data)


def options(markup, name):
    """[(value, text)] of the select `name`."""
    parser = _Options(name)
    parser.feed(markup)
    return [(value if value is not None else _words(text), _words(text)) for value, text in parser.options]


# -- Talking to AIM ------------------------------------------------------------------------------------------------------------------
class AimWriter:
    """AIM's pages through `client` (the signed-in browser's request context, as `aim_import.export_one` uses it): reads, and the
    two writes. Every post goes to AIM's own host, back to the form of the page just read, with that page's own fields."""

    def __init__(self, client, base):
        self.client, self.base = client, base

    def get(self, page):
        from aim_import import POLICY, SIGNED_OUT
        address, host = urljoin(self.base, page), urlsplit(self.base).netloc
        response = self.client.get(address, timeout=60000)
        landed = urlsplit(response.url)
        if landed.netloc != host:
            raise Stopped(SIGNED_OUT)
        if landed.path.lower().endswith("/login.aspx"):
            raise Stopped(POLICY)
        if not response.ok:
            raise Stopped(f"AIM answered {response.status} for {urlsplit(address).path.rsplit('/', 1)[-1]}.")
        return response, response.text()

    def post(self, response, markup, target, values, upload=None):
        """Post the form of `markup` back as AIM's own button `target` would, with `values` set; `upload` (name, file name, bytes)
        sends it as multipart, as AIM's upload form does."""
        from aim_import import parse_form
        form = parse_form(markup)
        missing = [name for name in (target, *values) if name not in form.names]
        if missing:
            raise Stopped("AIM's page has no " + ", ".join(name.rsplit("$", 2)[-2] for name in missing) + " where Faculty expects it; nothing was sent.")
        action = urljoin(response.url, form.action or response.url)
        landed, host = urlsplit(response.url), urlsplit(self.base).netloc
        if (urlsplit(action).scheme, urlsplit(action).netloc) != (landed.scheme, host):
            raise Stopped("AIM's page sends its form somewhere else; nothing was sent.")
        fields = [(name, value) for name, value in form.fields if name not in values and name not in ("__EVENTTARGET", "__EVENTARGUMENT")]
        fields = [("__EVENTTARGET", target), ("__EVENTARGUMENT", ""), *fields, *values.items()]
        headers = {"Origin": f"{landed.scheme}://{host}", "Referer": response.url}
        if upload:
            name, filename, content = upload
            multipart = {key: value for key, value in fields}
            multipart[name] = {"name": filename, "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               "buffer": content}
            answer = self.client.post(action, multipart=multipart, headers=headers, timeout=120000)
        else:
            answer = self.client.post(action, data=urlencode(fields), timeout=120000,
                                      headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
        if not answer.ok:
            raise Stopped(f"AIM answered {answer.status}.")
        return answer

    def all_rows(self, page, needed):
        """(response, markup) of a list page with every row on it: when the page shows as many rows (with the `needed` columns)
        as its page-size choice (PgGV$DDLShowLimit) allows, that choice is posted back as the largest, which changes only what
        the page shows."""
        response, markup = self.get(page)
        size = PREFIX + "PgGV$DDLShowLimit"
        choices = [value for value, _ in options(markup, size) if value.isdigit()]
        if not choices:
            return response, markup
        shown = re.search(r'<option[^>]*selected="selected"[^>]*value="(\d+)"', markup.split(f'name="{size}"', 1)[1])
        if shown and len(_headed(markup, needed)) >= int(shown.group(1)) and shown.group(1) != max(choices, key=int):
            answer = self.post(response, markup, size, {size: max(choices, key=int)})
            return answer, answer.text()
        return response, markup

    def dates(self):
        return exam_dates(self.all_rows(DATES_PAGE, DATE_COLUMNS)[1], self.base)

    def add_date(self, page, day, minutes, at):
        """Save one Quiz exam date on a section's List Exam Dates page: the type, the day, the time `at` ("HH:MM") and the length."""
        response, markup = self.get(page)
        quiz = [value for value, text in options(markup, TYPE) if text.casefold() == QUIZ_TYPE.casefold()]
        if len(quiz) != 1:
            raise Stopped("AIM's exam date form has no Quiz type where Faculty expects one; nothing was sent.")
        hour, minute = at.split(":")
        if hour not in {value for value, _ in options(markup, HOUR)} or minute not in {value for value, _ in options(markup, MINUTE)}:
            raise Stopped(f"AIM's exam date form has no time {at} to choose; nothing was sent.")
        self.post(response, markup, SAVE, {TYPE: quiz[0], DATE: aim_day(day), LENGTH: str(minutes), HOUR: hour, MINUTE: minute})

    def upcoming(self):
        return self.all_rows(UPCOMING_PAGE, REQUEST_COLUMNS)

    def upload(self, response, markup, boxes, title, filename, content):
        values = {box: "on" for box in boxes}
        values[UPLOAD_TITLE] = title
        self.post(response, markup, UPLOAD_BUTTON, values, upload=(UPLOAD_FILE, filename, content))


# -- What Send to AIM does ---------------------------------------------------------------------------------------------------------
def _sends(db, term_id):
    """[aim_sends rows with `status` (the latest outcome; none is 'pending') and `detail`] of the term, oldest first."""
    return [dict(row) for row in db.execute("""SELECT s.*, COALESCE(o.status, 'pending') AS status, COALESCE(o.detail, '') AS detail
        FROM aim_sends s LEFT JOIN aim_send_outcomes o ON o.id=(SELECT MAX(x.id) FROM aim_send_outcomes x WHERE x.send_id=s.id)
        WHERE s.term_id=? ORDER BY s.created_at, s.rowid""", (term_id,))]


def _held(send):
    return send["status"] in ("pending", "uncertain")


def plan(db, term_id, copies):
    """{term_id, fingerprint, quizzes}: every ready, upcoming DR quiz of the term (CLASS SCHEDULE: DR QUIZZES) with its accessible copy
    (`copies`: QUIZZES "ACCESSIBLE COPY", by assignment) and what Faculty already sent of it."""
    memo = TermRead(db, term_id)
    found = term_quizzes(db, term_id, memo)
    sends = _sends(db, term_id)
    takers = {section["section_id"]: section["dr_students"] for section in found["sections"]}
    quizzes = []
    for quiz in found["quizzes"]:
        if not quiz["ready"]:
            continue
        at = sitting(memo, quiz)
        mine = [send for send in sends if (send["item_id"], send["section_id"]) == (quiz["item"], quiz["section_id"])]
        dated = [send for send in mine if send["kind"] == "date" and send["date"] == quiz["date"]]
        copy = copies.get(quiz["assignment_id"]) if quiz["assignment_id"] else None
        quizzes.append({"key": quiz["key"], "item": quiz["item"], "title": quiz["title"], "assignment_id": quiz["assignment_id"], "section_id": quiz["section_id"],
                        "section": quiz["section"], "dr_students": takers.get(quiz["section_id"], 0), "date": quiz["date"], "start": quiz["start"],
                        "minutes": quiz["minutes"], "aim_time": at,
                        "copy": copy if copy and copy["ready"] else None,
                        "copy_reason": "" if copy and copy["ready"] else "; ".join(copy["reasons"]) if copy else "Not drafted in Faculty",
                        "date_send": _summary(dated[-1]) if dated else None,
                        "uploads": [_summary(send) for send in mine if send["kind"] == "upload" and send["date"] == quiz["date"]]})
    return {"term_id": term_id, "fingerprint": found["fingerprint"], "quizzes": quizzes}


def _summary(send):
    return {"id": send["id"], "status": send["status"], "detail": send["detail"] or (UNCERTAIN if send["status"] == "pending" else ""),
            "requests": len(json.loads(send["rows"])), "at": send["created_at"]}


AFTER_CLASS = 10  # minutes after the section's class ends that day when its DR quiz takers sit the quiz (requirement, 2026-10-27)


def sitting(memo, quiz):
    """The time AIM is given for a DR quiz, "HH:MM", or "": 10 minutes after the end of that section's class on the quiz's day
    (requirement, 2026-10-27), the section's class meeting that day, else the quiz row's own end;
    rounded up to AIM's five-minute steps, between 6 AM and 11:55 PM (AIM's hours)."""
    meeting = (memo.meetings(quiz["section_id"])[0].get(quiz["date"]) or [None])[0]
    end = _span(meeting["end_time"])[0] if meeting and meeting.get("end_time") else quiz.get("end") or ""
    if not end:
        return ""
    minutes = int(end[:2]) * 60 + int(end[3:]) + AFTER_CLASS
    minutes = -(-minutes // 5) * 5
    return f"{minutes // 60:02d}:{minutes % 60:02d}" if 6 * 60 <= minutes < 24 * 60 else ""


def _runs(db, term_id):
    """[aim_send_runs rows with `state` and `detail` of their end ('' while none)] of the term, oldest first."""
    return [dict(row) for row in db.execute("""SELECT r.*, COALESCE(e.state, '') AS state, COALESCE(e.detail, '') AS detail
        FROM aim_send_runs r LEFT JOIN aim_send_run_ends e ON e.run_id=r.id WHERE r.term_id=? ORDER BY r.created_at, r.rowid""", (term_id,))]


def due(found, runs, today):
    """[{key, title, section, date, why}] of `plan`'s quizzes a Send would act on now: a date AIM has not confirmed and no write of
    it waits for Retry ("date"), or a quiz drafted in Faculty, its date in AIM and still ahead, whose uploads AIM accepted cover
    fewer requests than its section has DR quiz takers, when no send of the term ended sent today ("upload"; instructor,
    2026-10-31: "yes" to the card staying up every day until each DR student's request has its file, since only a Send, after
    their Duo, can see who registered)."""
    sent_today = any(run["day"] == today and run["state"] == "sent" for run in runs)
    found_due = []
    for quiz in found["quizzes"]:
        send = quiz["date_send"]
        if not send or send["status"] in ("failed", "released"):
            why = "date"
        elif send["status"] == "accepted" and quiz["assignment_id"] and not sent_today and today < quiz["date"] \
                and sum(upload["requests"] for upload in quiz["uploads"] if upload["status"] == "accepted") < quiz["dr_students"]:
            why = "upload"
        else:
            continue
        found_due.append({"key": quiz["key"], "title": quiz["title"], "section": quiz["section"], "date": quiz["date"], "why": why})
    return found_due


def attention(db, today):
    """Home's "Send to AIM" card: {count, held, waiting, href} over the current terms, or None. `count` the quizzes due (`due`),
    `held` the writes waiting for Retry, `waiting` True when the latest send of some term ended without sending."""
    if not _has(db, "aim_send_runs"):
        return None
    count = held = 0
    waiting = False
    for (term_id,) in db.execute("SELECT id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY id").fetchall():
        found, runs = plan(db, term_id, {}), _runs(db, term_id)
        count += len(due(found, runs, today))
        held += sum(1 for send in _sends(db, term_id) if _held(send) and send["date"] >= today)
        ended = [run for run in runs if run["state"]]
        waiting = waiting or bool(ended and ended[-1]["state"] in ("not_signed_in", "stopped"))
    if not (count or held):
        return None
    return {"count": count, "held": held, "waiting": waiting, "href": "#accommodations?tab=quizzes"}


class Recorder:
    """The receipts of one Send (`run_id`), written from the browser's thread: a write before it is posted, its outcome after,
    and the "Prescheduled in AIM" mark of a quiz AIM lists."""

    def __init__(self, schedule, term_id, run_id, actor):
        self.schedule, self.term_id, self.run_id, self.actor = schedule, term_id, run_id, actor
        self.ended = False

    def started(self, trigger, quizzes):
        with self.schedule.rosters.lock(), connect(self.schedule.path) as db:
            db.execute("INSERT INTO aim_send_runs(id, term_id, trigger, day, quizzes, actor, created_at) VALUES (?,?,?,?,?,?,?)",
                       (self.run_id, self.term_id, trigger, clock.campus_today().isoformat(), quizzes, self.actor, timestamp()))
            db.commit()

    def end(self, state, detail=""):
        """How the send ended; once."""
        if self.ended:
            return
        self.ended = True
        with self.schedule.rosters.lock(), connect(self.schedule.path) as db:
            db.execute("INSERT OR IGNORE INTO aim_send_run_ends(run_id, state, detail, created_at) VALUES (?,?,?,?)",
                       (self.run_id, state, detail[:500], timestamp()))
            db.commit()

    def sends(self):
        with connect(self.schedule.path) as db:
            return _sends(db, self.term_id)

    def begin(self, quiz, kind, minutes=0, copy=None, rows=()):
        send_id = uuid.uuid4().hex[:16]
        with self.schedule.rosters.lock(), connect(self.schedule.path) as db:
            db.execute("""INSERT INTO aim_sends(id, term_id, item_id, section_id, kind, date, minutes, approval_id, version, file_sha256, rows,
                run_id, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (send_id, self.term_id, quiz["item"], quiz["section_id"], kind, quiz["date"], minutes,
                        copy["approval_id"] if copy else "", copy["version"] if copy else "", copy["sha256"] if copy else "",
                        json.dumps(sorted(rows)), self.run_id, self.actor, timestamp()))
            db.commit()
        return send_id

    def outcome(self, send_id, status, detail=""):
        with self.schedule.rosters.lock(), connect(self.schedule.path) as db:
            db.execute("INSERT INTO aim_send_outcomes(send_id, status, detail, actor, created_at) VALUES (?,?,?,?,?)",
                       (send_id, status, detail[:500], self.actor, timestamp()))
            db.commit()

    def mark(self, quiz):
        """The quiz's "Prescheduled in AIM" mark (DR QUIZ NOTICES), recorded once AIM lists its date: the notices worker then emails
        the section's DR quiz takers. A quiz already marked at this date and start records nothing."""
        with self.schedule.rosters.lock(), connect(self.schedule.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = next((row for row in term_quizzes(db, self.term_id)["quizzes"] if row["key"] == quiz["key"]), None)
            if not current or not current["ready"] or (current["date"], current["start"]) != (quiz["date"], quiz["start"]):
                return False
            if _state(current, _marks(db, self.term_id).get((quiz["item"], quiz["section_id"]))) == "marked":
                return False
            request_id = f"aim-send:{self.run_id}:{quiz['key']}"[:100]
            digest = request_digest({"term": self.term_id, "action": "mark", "key": quiz["key"], "run": self.run_id})
            _record(db, self.term_id, quiz["item"], quiz["section_id"], "mark", quiz["date"], quiz["start"], request_id, digest, self.actor)
            db.commit()
        self.schedule.dr_quiz_notices.wake()
        return True


def run(writer, quizzes, recorder, say=lambda text: None, report=None):
    """Send to AIM: the dates, then the uploads (module docstring). `quizzes` are `plan`'s, each ready copy with its `content`,
    `filename` and `sha256`. Appends to `report` (and returns it) [{key, title, section, date, did, detail}] for the window, one
    line per thing done or held, so a send stopped part way still says what it did."""
    report = [] if report is None else report
    sends = recorder.sends()

    def line(quiz, did, detail=""):
        report.append({"key": quiz["key"], "title": quiz["title"], "section": quiz["section"], "date": quiz["date"], "did": did, "detail": detail})

    say("Reading AIM's exam dates…")
    listed, pages = writer.dates()
    for quiz in quizzes:
        number, wanted = str(quiz["section"]), (str(quiz["section"]), QUIZ_TYPE.casefold(), quiz["date"])
        earlier = [send for send in sends if send["kind"] == "date" and (send["item_id"], send["section_id"], send["date"]) ==
                   (quiz["item"], quiz["section_id"], quiz["date"])]
        if wanted in listed:
            for send in earlier:
                if _held(send):  # a later read of AIM settles an uncertain write
                    recorder.outcome(send["id"], "accepted", "AIM's Exam Dates list shows it.")
            line(quiz, "date_listed")
        elif earlier and _held(earlier[-1]):
            line(quiz, "date_held", HELD)
            continue
        else:
            if not quiz.get("aim_time"):
                line(quiz, "date_failed", f"Faculty has no class end time for section {number} on that day to set the quiz 10 minutes "
                                          "after. Put the class on the schedule, or add it in AIM yourself.")
                continue
            if number not in pages:
                line(quiz, "date_failed", f"AIM lists no exam date of section {number} to find its Testing Agreement by. Add this one in AIM yourself.")
                continue
            say(f"Adding {quiz['title']} for section {number} to AIM…")
            send_id = recorder.begin(quiz, "date", minutes=quiz["minutes"])
            try:
                writer.add_date(pages[number], quiz["date"], quiz["minutes"], quiz["aim_time"])
            except Stopped as error:
                recorder.outcome(send_id, "failed", str(error))
                line(quiz, "date_failed", str(error))
                continue
            except Exception as error:
                recorder.outcome(send_id, "uncertain", f"{type(error).__name__}: {UNCERTAIN}")
                line(quiz, "date_uncertain", UNCERTAIN)
                continue
            listed, pages = writer.dates()
            if wanted not in listed:
                recorder.outcome(send_id, "uncertain", UNCERTAIN)
                line(quiz, "date_uncertain", UNCERTAIN)
                continue
            recorder.outcome(send_id, "accepted", "AIM's Exam Dates list shows it.")
            line(quiz, "date_added")
        if recorder.mark(quiz):
            line(quiz, "marked")

    ready = [quiz for quiz in quizzes if quiz.get("copy") and (str(quiz["section"]), QUIZ_TYPE.casefold(), quiz["date"]) in listed]
    if not ready:
        return report
    say("Reading AIM's upcoming exams…")
    response, markup = writer.upcoming()
    rows = requests(markup)
    sends = recorder.sends()
    for quiz in ready:
        uploads = [send for send in sends if send["kind"] == "upload" and (send["item_id"], send["section_id"], send["date"]) ==
                   (quiz["item"], quiz["section_id"], quiz["date"])]
        covered = {digest for send in uploads if send["status"] in ("accepted", "pending", "uncertain") for digest in json.loads(send["rows"])}
        mine = [row for row in rows if (row["section"], row["type"].casefold(), row["date"]) == (str(quiz["section"]), QUIZ_TYPE.casefold(), quiz["date"])]
        new = [row for row in mine if row["digest"] not in covered]
        if any(_held(send) for send in uploads):
            line(quiz, "upload_held", HELD)
        if not mine:
            line(quiz, "upload_waiting", "No student has registered for it in AIM yet.")
            continue
        if not new:
            continue
        before = without_files(markup)
        say(f"Uploading the accessible copy of {quiz['title']} for section {quiz['section']}…")
        send_id = recorder.begin(quiz, "upload", copy=quiz["copy"], rows=[row["digest"] for row in new])
        try:
            writer.upload(response, markup, [row["box"] for row in new], f"{quiz['title']} (accessible copy)", quiz["copy"]["filename"],
                          quiz["copy"]["content"])
        except Stopped as error:
            recorder.outcome(send_id, "failed", str(error))
            line(quiz, "upload_failed", str(error))
            break  # the page is not as read; read it again on the next Send
        except Exception as error:
            recorder.outcome(send_id, "uncertain", f"{type(error).__name__}: {UNCERTAIN}")
            line(quiz, "upload_uncertain", UNCERTAIN)
            break
        response, markup = writer.upcoming()
        after = without_files(markup)
        if before is not None and after is not None and after == before - len(new):
            recorder.outcome(send_id, "accepted", f"AIM counts {len(new)} fewer exams without files.")
            line(quiz, "uploaded", f"{len(new)} request{'' if len(new) == 1 else 's'}")
        else:
            recorder.outcome(send_id, "uncertain", UNCERTAIN)
            line(quiz, "upload_uncertain", UNCERTAIN)
        rows = requests(markup)
    return report


def retry(schedule, term_id, data, actor="workspace"):
    """Release one uncertain (or unanswered) write so the next Send may post it again: the instructor's Retry after checking AIM."""
    if not isinstance(data, dict) or set(data) - {"request_id", "send_id"} or not REQUEST.fullmatch(str(data.get("request_id") or "")):
        raise ValueError("Send request_id and send_id.")
    with schedule.rosters.lock(), connect(schedule.path) as db:
        db.execute("BEGIN IMMEDIATE")
        ScheduleService._term(db, term_id)
        send = next((send for send in _sends(db, term_id) if send["id"] == data.get("send_id")), None)
        if not send:
            raise ValueError("That AIM write is not one of this term's.")
        if not _held(send):
            raise Conflict("Only a write AIM has not confirmed waits for Retry.")
        db.execute("INSERT INTO aim_send_outcomes(send_id, status, detail, actor, created_at) VALUES (?,?,?,?,?)",
                   (send["id"], "released", f"Released by Retry ({data['request_id']}).", actor, timestamp()))
        db.commit()


# -- The browser -----------------------------------------------------------------------------------------------------------------
def import_bookings(dr_exams, inbox, found, run_id):
    """The DR bookings export imported as Import from AIM's review would (its preview, then the import job against that preview's
    hash), without the review (requirement, 2026-10-27). {imported, counts, job}."""
    from dr_exam_import import parse_dr_exams
    preview = dr_exams.preview(parse_dr_exams(found["content"], found["name"]), None)
    counts = {key: value for key, value in preview["counts"].items() if value}
    if not any(preview["counts"][key] for key in ("added", "updated", "unchanged")):
        return {"imported": False, "counts": counts, "job": None}
    job = inbox.submit({"action": "accommodations.exams.sync", "metadata": {"term": None, "preview_hash": preview["preview_hash"]}},
                       [{"filename": found["name"], "content": found["content"]}], idempotency_key=f"{run_id}:exams")
    return {"imported": True, "counts": counts, "job": job["id"]}


def browser_class():
    """AimSendBrowser, made on first use so that importing this module does not load the sign-in browser."""
    from aim_import import AimBrowser

    class AimSendBrowser(AimBrowser):
        """Import from AIM's sign-in (the saved school sign-in, Duo, AIM's FERPA page) that, once in AIM, runs `run` on the browser's
        own thread, exports the DR bookings and imports them (`importer`); it ends "done" with what it did. When the import cannot
        be made, the export is left for the window's ordinary import review instead."""

        sends = True
        FINAL = ("exported", "done")

        def __init__(self, portal, netid, password, *, recorder, quizzes, importer):
            self._recorder, self._quizzes, self._importer, self._report, self._imported = recorder, quizzes, importer, [], None
            super().__init__(portal, netid, password, kinds=("exams",))

        def status(self):
            with self._lock:
                report, imported = [dict(line) for line in self._report], self._imported
            return {**super().status(), "send": True, "sent": report, "imported": imported}

        def _run(self):
            try:
                super()._run()
            finally:  # a send that never reached AIM (Cancel, a sign-in that ended, the browser stopping) ends closed
                self._recorder.end("closed", "The window closed, or the browser ended, before the send finished.")

        def _on_aim(self, frame, text, inputs, now):
            if self._exported:
                return
            self._exported = True
            self._set(state="exporting", place="Sending to AIM…")
            ended = ("sent", "")
            try:
                run(AimWriter(self._context.request, self.base), self._quizzes, self._recorder,
                    say=lambda words: self._set(place=words), report=self._report)
            except Stopped as error:
                self._report.append({"key": "", "title": "", "section": "", "date": "", "did": "stopped", "detail": str(error)})
                ended = ("stopped", str(error))
            except Exception as error:
                LOG.exception("Send to AIM stopped")
                detail = f"Send to AIM stopped ({type(error).__name__}). {UNCERTAIN}"
                self._report.append({"key": "", "title": "", "section": "", "date": "", "did": "stopped", "detail": detail})
                ended = ("stopped", detail)
            self._recorder.end(*ended)
            self._export(self._kinds)
            found = ((self.result or {}).get("files") or {}).get("exams")
            if not found:
                return
            self._set(place="Importing the DR bookings…")
            try:
                imported = self._importer(found)
            except Exception:
                LOG.exception("The DR bookings of Send to AIM were not imported")
                return  # the export waits for the window's import review
            with self._lock:
                self._imported, self.state, self.place = imported, "done", "Sent to AIM"

    return AimSendBrowser


def read_aim_plan(schedule, frq, term_id):
    """The term's Send to AIM plan, read as the GET, Send and the Follow-up upload all read it (copies first, then one read transaction)."""
    copies = frq.quiz_accessible_copies(term_id)
    with connect(schedule.path) as db:
        db.execute("BEGIN")
        ScheduleService._term(db, term_id)
        return plan(db, term_id, copies)


def start(schedule, frq, sessions, portal, term_id, data, actor="workspace", *, dr_exams=None, inbox=None):
    """Send to AIM, the instructor's click: checked against the `fingerprint` of the DR quizzes read they saw (409 when it changed),
    each ready quiz's accessible copy made now, and the sign-in started with the saved school sign-in (`saved`) or on the page."""
    from aim_import import check_credentials, saved_sign_in
    if not isinstance(data, dict) or set(data) - {"request_id", "fingerprint", "saved", "netid", "password"} \
            or not REQUEST.fullmatch(str(data.get("request_id") or "")):
        raise ValueError("Send request_id and fingerprint, with saved or the NetID and password to sign in with.")
    found = read_aim_plan(schedule, frq, term_id)
    if data.get("fingerprint") != found["fingerprint"]:
        raise Conflict("The DR quizzes changed since you read them. Read them again before sending.")
    if not found["quizzes"]:
        raise ValueError("No DR quiz of this term is ready to send: each needs a date and a start on the class schedule.")
    netid = password = None
    if data.get("saved") is True:
        saved = saved_sign_in()
        if not saved:
            raise ValueError("No sign-in is saved on the server. Sign in on the page instead.")
        netid, password = saved
    elif data.get("netid") or data.get("password"):
        typed = check_credentials(data)
        netid, password = typed["netid"], typed["password"]
    return _launch(schedule, frq, sessions, portal, term_id, found["quizzes"], netid, password, actor, dr_exams, inbox)


def _launch(schedule, frq, sessions, portal, term_id, chosen, netid, password, actor, dr_exams, inbox):
    """The sign-in window's session for a send of `chosen` (plan quizzes), each ready copy made now; its status."""
    quizzes = []
    for quiz in chosen:
        copy = quiz["copy"]
        if copy:
            content, filename = frq.quiz_accessible_copy(copy["draft_id"], copy["approval_id"], copy["version"])
            copy = {**copy, "content": content, "filename": filename, "sha256": hashlib.sha256(content).hexdigest()}
        quizzes.append({**quiz, "copy": copy})
    recorder = Recorder(schedule, term_id, f"run-{uuid.uuid4().hex}", actor)
    recorder.started("instructor", len(quizzes))
    importer = lambda found: import_bookings(dr_exams, inbox, found, recorder.run_id)  # noqa: E731
    session = sessions.install(lambda: browser_class()(portal, netid, password, recorder=recorder, quizzes=quizzes, importer=importer))
    return session.status()


UPLOAD_CONFIRMATION = "upload"  # supplied by the instructor's Approve of the Follow-up card only (assistant/policy.py)
_UPLOADS = {}  # request ID -> the session it started (process memory): a request is started once


def upload_start(schedule, frq, sessions, portal, term_id, data, actor="workspace", *, dr_exams=None, inbox=None):
    """DR REQUEST EMAILS › THE CARD (requirement, 2026-11-01): the
    approved upload of one DR quiz's accessible copy (`key`, a quiz of `plan`) to its section's requests in AIM. It is Send to AIM's
    own send, of that one quiz (its date added too if AIM lacks it), with the saved school sign-in; the page that approved it opens
    the sign-in window on the session it answers, where Duo's code shows. `confirmation` comes only with the instructor's Approve."""
    from aim_import import saved_sign_in
    if not isinstance(data, dict) or set(data) - {"request_id", "key", "enrollment_id", "attempt", "confirmation"} \
            or not REQUEST.fullmatch(str(data.get("request_id") or "")) or not isinstance(data.get("key"), str):
        raise ValueError("Send request_id and the DR quiz's key.")
    if data.get("confirmation") != UPLOAD_CONFIRMATION:
        raise ValueError("Approve this upload on its Follow-up card: AIM's sign-in needs your Duo.")
    if data["request_id"] in _UPLOADS:
        raise Conflict("This upload was already started. Its window shows how it went; Accommodations › DR quizzes shows its receipt.")
    found = read_aim_plan(schedule, frq, term_id)
    quiz = next((quiz for quiz in found["quizzes"] if quiz["key"] == data["key"]), None)
    if not quiz:
        raise Conflict("That DR quiz is not ready to send any more: Accommodations › DR quizzes says why.")
    if not quiz["copy"]:
        raise Conflict(f"{quiz['title']} has no accessible copy to upload: {quiz['copy_reason']}.")
    saved = saved_sign_in()
    if not saved:
        raise ValueError("No sign-in is saved on the server: use Send to AIM on Accommodations › DR quizzes and sign in there.")
    status = _launch(schedule, frq, sessions, portal, term_id, [quiz], *saved, actor, dr_exams, inbox)
    if len(_UPLOADS) >= 200:
        _UPLOADS.pop(next(iter(_UPLOADS)))
    _UPLOADS[data["request_id"]] = status.get("id")
    return {**status, "aim_upload": {"title": quiz["title"], "section": quiz["section"], "date": quiz["date"]}}
