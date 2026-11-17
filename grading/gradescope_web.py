"""Gradescope as the instructor's signed-in browser sees it (requirement, 2026-09-25). AGENTS.md "Sending grades to Gradescope" is the rule book; this module is only the transport and the page reading.

Gradescope has no public API, so this signs in with the instructor's email and password and reads the same pages and sends the
same requests the website does: reading, uploading papers, setting grades, and (since 2026-09-27, `gradescope_create`) creating an
assignment with its template, outline, rubric items and version set, and (since 2026-10-02, `gradescope_publish`, only on the instructor's
explicit request) publishing an assignment's grades, and (since 2026-10-06, `gradescope_mc`) reading a bubble-sheet exam's grades download for
the MC check. Nothing here decides what to send: `gradescope_sync` does, and every write it makes is read back.

- The credentials are a file named ONLY by FACULTY_GRADESCOPE_CREDENTIALS_FILE (no default: HOME is shared by checks, the email
  gateway and timers, which must never find it). The file is read when signing in, never kept, logged or returned.
- Cookies and the password go only to https://www.gradescope.com (a loopback test server may replace it); redirects are never
  followed, so nothing is carried to another host.
- The course "Math 140 for Instructors" (the course coordinator's; requirement, 2026-09-25) is never read or written: it is left
  out of every course list and any request naming it is refused before it is sent.
"""
import csv
import email.utils
import html
import html.parser
import http.client
import http.cookiejar
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .providers.base import KeepAliveHTTPHandler, KeepAliveHTTPSHandler, NoRedirect

BASE = "https://www.gradescope.com"
CREDENTIALS_ENV = "FACULTY_GRADESCOPE_CREDENTIALS_FILE"
FORBIDDEN_COURSES = ("math 140 for instructors",)
USER_AGENT = "Faculty (instructor workspace)"
TIMEOUT = 60
UPLOAD_TIMEOUT = 180
PAGE_LIMIT = 16 * 1024 * 1024
TEMPLATE_LIMIT = 25 * 1024 * 1024  # an Assignment Template's own limit (`assignment_templates.MAX_SOURCE`)
# Where an outline's template link points: Gradescope's uploads bucket on S3, a signed link that needs no cookie.
UPLOADS_HOST = re.compile(r"production-gradescope-uploads\.s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com")
PACE = 0.2  # seconds between requests: a person clicking, not a flood
# "Too many requests" and a busy or overloaded server (requirement, 2026-09-25: wait and try again, and slow down). A 429 or a 503
# answers a request Gradescope did not carry out; after a 502 or 504 from the servers in front of it, a write may still have landed.
BUSY = {429: "asked Faculty to slow down (429 Too Many Requests)", 502: "was overloaded (502 Bad Gateway)",
        503: "was busy or down for maintenance (503 Service Unavailable)", 504: "did not answer in time (504 Gateway Timeout)"}
NOT_CARRIED_OUT = {429, 503}
SLOWEST = 5.0  # seconds between requests: after each busy answer a session waits twice as long between requests, up to this
CAREFUL = 1.0  # ... and never less than this


class GradescopeError(Exception):
    """`outcome` says what may have happened: 'refused' (Gradescope answered no, or Faculty never sent it), 'unavailable' (nothing
    left this machine) or 'unknown' (a write may have landed; read Gradescope before doing anything else)."""

    def __init__(self, message, *, outcome="refused", status=None):
        super().__init__(message)
        self.outcome, self.status = outcome, status



class SignInRefused(GradescopeError):
    """Gradescope refused the saved email and password: the sender pauses instead of trying again for every paper and grade."""


class PublishRefused(GradescopeError):
    """Review Grades shows no Publish Grades form Faculty recognizes (a changed page): nothing was sent, and trying again would not
    help, so the publication stops for the instructor."""


class GradescopeBusy(GradescopeError):
    """Gradescope answered "too many requests" or that it was busy (`BUSY`). Nothing about the request itself was wrong: the sender
    waits (at least `retry_after` seconds when Gradescope said how long) and tries again, reading Gradescope before any write."""

    def __init__(self, status, method, retry_after=None):
        write = method != "GET"
        done = "nothing was changed" if not write or status in NOT_CARRIED_OUT else "whether it kept this is unknown until Faculty reads it again"
        super().__init__(f"Gradescope {BUSY[status]}; {done}.", status=status,
                         outcome="unknown" if write and status not in NOT_CARRIED_OUT else "unavailable")
        self.retry_after = retry_after


def retry_after(headers):
    """Seconds Gradescope asked Faculty to wait (the Retry-After header: seconds or an HTTP date), or None."""
    value = str((headers or {}).get("Retry-After") or "").strip()
    if value.isdigit():
        return int(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((when - datetime.now(timezone.utc)).total_seconds()))


def normalized(title):
    return " ".join(str(title or "").split()).casefold()


def forbidden(*titles):
    """The coordinator's course is never touched (requirement, 2026-09-25). Matched loosely, never only by an exact title: any
    course whose short or full name contains "for instructors", or names both 140 and an instructor, is left alone."""
    for title in titles:
        name = normalized(title)
        if name in FORBIDDEN_COURSES or "for instructors" in name or ("140" in name and "instructor" in name):
            return True
    return False


def course_label(course):
    """How a Gradescope course is named in every dialog and plan: its short name, then its full name."""
    return f"{course['short']} · {course['name']}".strip(" ·")


def load_credentials(path):
    """The saved sign-in, checked the way ~/.config/faculty's other secrets are: an absolute path to a regular file of this
    account, readable by no one else, small, a JSON object with an email and a password. Nothing of its content is echoed."""
    if not path or not os.path.isabs(path):
        raise GradescopeError(f"Gradescope is not set up here: {CREDENTIALS_ENV} must name the saved sign-in file.", outcome="unavailable")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise GradescopeError(f"The Gradescope sign-in file {path} cannot be opened (missing, or a link). Run save-gradescope.py again.", outcome="unavailable") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
            raise GradescopeError(f"The Gradescope sign-in file {path} must be a small file of this account with mode 0600.", outcome="unavailable")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(4097)
    finally:
        os.close(descriptor)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k].strip() for k in ("email", "password")):
        raise GradescopeError(f"The Gradescope sign-in file {path} does not hold an email and a password. Run save-gradescope.py again.", outcome="unavailable")
    return data["email"].strip(), data["password"]


def base_url(value):
    """https://www.gradescope.com, or a loopback http(s) URL for a test server. Anything else is refused."""
    value = (value or BASE).rstrip("/")
    if value == BASE:
        return value
    parts = urllib.parse.urlsplit(value)
    try:
        loopback = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if parts.scheme not in ("http", "https") or not loopback or parts.username or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("Gradescope may only be reached at https://www.gradescope.com (or a loopback test server).")
    return value


@dataclass
class Settings:
    credentials_file: str = ""
    base: str = BASE
    pace: float = PACE

    @classmethod
    def environment(cls):
        return cls(credentials_file=os.environ.get(CREDENTIALS_ENV, "").strip())

    @property
    def configured(self):
        return bool(self.credentials_file)

    def status(self):
        """Whether this Faculty can sign in, WITHOUT reading the password: the file's presence and permissions only."""
        if not self.configured:
            return {"configured": False, "problem": "Gradescope is not set up for this Faculty instance."}
        try:
            info = os.lstat(self.credentials_file)
        except OSError:
            return {"configured": False, "problem": "The saved Gradescope sign-in file is missing. Run save-gradescope.py in a terminal."}
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return {"configured": False, "problem": "The saved Gradescope sign-in file must be a file of this account with mode 0600."}
        return {"configured": True, "problem": ""}


class Page(html.parser.HTMLParser):
    """What Faculty needs from a Gradescope page: its CSRF token, the React components' props, the login form token."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.csrf, self.login_token, self.react, self._form = None, None, {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name") == "csrf-token":
            self.csrf = a.get("content")
        elif tag == "form":
            self._form = a.get("action")
        elif tag == "input" and self._form == "/login" and a.get("name") == "authenticity_token" and self.login_token is None:
            self.login_token = a.get("value")
        if "data-react-class" in a and a["data-react-class"] not in self.react:
            try:
                self.react[a["data-react-class"]] = json.loads(a.get("data-react-props") or "{}")
            except ValueError:
                pass

    def props(self, name):
        if name not in self.react:
            raise GradescopeError(f"Gradescope's page no longer has the {name} Faculty reads. Nothing was changed.")
        return self.react[name]


def parse(text):
    page = Page()
    page.feed(text)
    return page


# A term heading may carry more attributes than its class (role, aria-level ...): only the class is required.
_COURSE_BOX = re.compile(r'<div class="courseList--term[^"]*"[^>]*>(.*?)</div>|<a class="courseBox[^"]*"[^>]*href="/courses/(\d+)"[^>]*>(.*?)</a>', re.S)
_ROW = re.compile(r'<tr[^>]*class="[^"]*rosterRow[^"]*"[^>]*>(.*?)</tr>', re.S)


def course_list(text):
    """Instructor courses from the account page, in page order, with the term heading each sits under."""
    courses, term, instructor = [], "", True
    head = text.find("Student Courses")
    for match in _COURSE_BOX.finditer(text):
        if head >= 0 and match.start() > head:
            instructor = False
        if match.group(1) is not None:
            term = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
            continue
        inner = match.group(3)
        short = re.search(r'courseBox--shortname[^>]*>(.*?)<', inner, re.S)
        name = re.search(r'courseBox--name[^>]*>(.*?)<', inner, re.S)
        if instructor:
            courses.append({"id": match.group(2), "short": html.unescape(short.group(1).strip()) if short else "",
                            "name": html.unescape(name.group(1).strip()) if name else "", "term": term})
    return courses


def roster_rows(text):
    """Students of a course: Gradescope user ID, SID, email and name, from each row's edit button."""
    students = []
    for row in _ROW.findall(text):
        button = re.search(r'<button[^>]*rosterCell--editIcon[^>]*>', row)
        user = re.search(r'js-rosterName[^>]*data-url="[^"]*user_id=(\d+)', row) or re.search(r'data-url="[^"]*user_id=(\d+)[^"]*"[^>]*js-rosterName', row)
        if not button or not user:
            continue
        attrs = {k: html.unescape(v) for k, v in re.findall(r'([a-z-]+)="([^"]*)"', button.group(0))}
        try:
            detail = json.loads(attrs.get("data-cm") or "{}")
        except ValueError:
            detail = {}
        if attrs.get("data-role") != "0":
            continue  # 0 is a student; instructors, TAs and readers are never matched to papers
        students.append({"user_id": user.group(1), "sid": str(detail.get("sid") or "").strip(), "email": attrs.get("data-email", "").strip(),
                         "name": str(detail.get("full_name") or "").strip()})
    return students


_QUESTION_TABLE = re.compile(r'<table[^>]*\bid="question_submissions"[^>]*>(.*?)</table>', re.S)
_TABLE_ROW = re.compile(r"<tr(?:\s[^>]*)?>(.*?)</tr>", re.S)
_TABLE_CELL = re.compile(r"<t([dh])(?:\s[^>]*)?>(.*?)</t\1>", re.S)
QUESTION_COLUMNS = ("user", "last graded by", "score", "graded?")


def _cell_text(cell):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())


def question_rows(text, course_id, question_id):
    """A question's Grade Submissions list, one row per question submission: {question submission id: {graded, by, score}}, `by`
    the name in "Last Graded By" ("" when nobody), `score` a Decimal or None. The page as read on the real site 2026-10-01 (table
    `question_submissions`, columns User, Last Graded By, Sections, Score, Graded?; a graded row has a check icon, an ungraded one
    empty cells; one page, no pagination, for 21 submissions). Read strictly: a missing column, a row without its grading link or a
    score that is not a number raises, so a changed page never makes a graded question look ungraded."""
    table = _QUESTION_TABLE.search(text)
    rows = _TABLE_ROW.findall(table.group(1)) if table else []
    header = [_cell_text(cell).lower() for _, cell in _TABLE_CELL.findall(rows[0])] if rows else []
    if not rows or any(column not in header for column in QUESTION_COLUMNS):
        raise GradescopeError("Gradescope's list of a question's submissions has changed; nothing was sent.")
    at = {column: header.index(column) for column in QUESTION_COLUMNS}
    link = re.compile(rf'href="/courses/{int(course_id)}/questions/{int(question_id)}/submissions/(\d+)/grade"')
    found = {}
    for row in rows[1:]:
        cells = [cell for _, cell in _TABLE_CELL.findall(row)]
        match = link.search(cells[at["user"]]) if len(cells) == len(header) else None
        if not match:
            raise GradescopeError("Gradescope's list of a question's submissions has changed; nothing was sent.")
        score = _cell_text(cells[at["score"]])
        try:
            score = Decimal(score) if score else None
        except ArithmeticError:
            raise GradescopeError("Gradescope's list of a question's submissions shows a score Faculty cannot read; nothing was sent.") from None
        found[match.group(1)] = {"graded": "fa-check" in cells[at["graded?"]], "by": _cell_text(cells[at["last graded by"]]), "score": score}
    return found


_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S)
_INPUT = re.compile(r"<input\b([^>]*)/?>", re.S)
_ATTR = re.compile(r'([\w:\[\]-]+)="([^"]*)"')
# Review Grades' Publish Grades, as read on the real site 2026-10-02: the action bar's button is its own form (class
# review-grades-next-button) when the instructor turned the confirmation off, else it opens a modal whose form is js-publishGradesForm.
PUBLISH_FORMS = ("review-grades-next-button", "js-publishGradesForm")


SCORE_FILE = re.compile(r"_Version_([A-Za-z]?)_scores\.csv$")
SCORE_QUESTION = re.compile(r"(\d+):.*\(\s*-?[\d.]+\s*pts?\)", re.S)
SCORE_COLUMNS = ("sid", "total score", "status", "submission id")


def _score(text):
    """A score cell as Faculty writes points ("4.0" -> "4"); None for an empty cell. A cell that is not a number raises."""
    text = text.strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except ArithmeticError:
        raise GradescopeError("Gradescope's scores download shows a score Faculty cannot read; nothing was compared.") from None
    if not value.is_finite():
        raise GradescopeError("Gradescope's scores download shows a score Faculty cannot read; nothing was compared.")
    return format(value.normalize(), "f") if value != value.to_integral() else str(value.to_integral())


def container_scores(data):
    """A bubble-sheet exam's grades download (Review Grades, Download Grades: `/assignment_containers/<x>/scores/csv.zip`, read on the
    real site 2026-10-06, Common Exam 1 MC of 140-507): one CSV per version ("..._Version_B_scores.csv"; the "?" version Gradescope
    files unread sheets under has no letter, "..._Version__scores.csv") and the version set's summary ("..._Version_Set_Scores.csv",
    not used). Every student of the course is a row of every version's file; only the version holding their submission (a Submission
    ID, a status other than Missing) counts. Columns First Name, Last Name, SID, Email, Sections, Total Score, Max Points, Status,
    Submission ID, ..., then one per question, "6: Question 6 (4.0 pts)". Returns {sid: {version ("?" unread), total, status,
    submission, questions {number: points}}}; a student with submissions in two versions has `several`. A download whose layout
    Faculty does not know raises: nothing is compared from a guess."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        files = [info for info in archive.infolist() if SCORE_FILE.search(info.filename)]
    except zipfile.BadZipFile:
        raise GradescopeError("Gradescope's scores download could not be opened; nothing was compared.") from None
    if not files:
        raise GradescopeError("Gradescope's scores download holds no version's scores; nothing was compared.")
    found = {}
    for info in files:
        if info.file_size > PAGE_LIMIT:
            raise GradescopeError("Gradescope's scores download is larger than Faculty reads; nothing was compared.")
        letter = SCORE_FILE.search(info.filename).group(1).upper() or "?"
        rows = list(csv.reader(io.StringIO(archive.read(info).decode("utf-8-sig", "replace"))))
        head = [cell.strip() for cell in rows[0]] if rows else []
        low = [cell.lower() for cell in head]
        if any(name not in low for name in SCORE_COLUMNS):
            raise GradescopeError("Gradescope's scores download has changed (a column Faculty reads is missing); nothing was compared.")
        at = {name: low.index(name) for name in SCORE_COLUMNS}
        questions = {}
        for index, title in enumerate(head):
            match = SCORE_QUESTION.fullmatch(title)
            if match and index > at["submission id"]:
                questions[index] = match.group(1)
        for row in rows[1:]:
            row = row + [""] * (len(head) - len(row))
            submission, status = row[at["submission id"]].strip(), row[at["status"]].strip()
            if not submission or status.lower() == "missing":
                continue  # no sheet in this version
            sid = row[at["sid"]].strip()
            entry = {"version": letter, "total": _score(row[at["total score"]]), "status": status, "submission": submission,
                     "questions": {number: _score(row[index]) for index, number in questions.items() if row[index].strip()}}
            if sid in found:
                found[sid]["several"] = True
                continue
            found[sid] = entry
    return found


def _attrs(tag):
    return {name: html.unescape(value) for name, value in _ATTR.findall(tag)}


def publish_form(text, course_id, kind, target_id):
    """(action, [(name, value)]) of Review Grades' Publish Grades form for this assignment (`kind` "assignment") or exam version set
    ("assignment_container": Gradescope's "Publish All Grades", which publishes every version), or None. As read on the real site
    2026-10-02 (four real courses, read only): a form posting to the assignment's or version set's own address, with `_method` put
    or patch, the page's `authenticity_token` and a hidden `assignment[published]` (or `assignment_container[published]`) of "true";
    the modal's form also says "true" for the other one. Only hidden fields are kept: the modal's "Don't show this message again"
    checkbox is the instructor's own setting and never sent. Unpublishing (the same fields with "false") is never matched. The
    modal's form is on the page published or not (seen in the sandbox, 2026-10-02), so whether grades are published is read from
    the assignments list, never from this page."""
    action = f"/courses/{int(course_id)}/{'assignment_containers' if kind == 'assignment_container' else 'assignments'}/{int(target_id)}"
    field = "assignment_container[published]" if kind == "assignment_container" else "assignment[published]"
    found = {}
    for tag, inner in _FORM.findall(text):
        attrs = _attrs(tag)
        names = set(attrs.get("class", "").split())
        form = next((name for name in PUBLISH_FORMS if name in names), None)
        if not form or form in found or attrs.get("action") != action or attrs.get("method", "").lower() != "post":
            continue
        fields = [(a["name"], a.get("value", "")) for a in map(_attrs, _INPUT.findall(inner)) if a.get("type") == "hidden" and a.get("name")]
        values = dict(fields)
        if values.get(field) == "true" and values.get("authenticity_token") and values.get("_method", "").lower() in ("put", "patch") \
                and all(value == "true" for name, value in fields if name.endswith("[published]")):
            found[form] = (action, fields)
    return next((found[name] for name in PUBLISH_FORMS if name in found), None)


def _gon(text, name):
    """A `gon.<name>=<JSON>` value of a page's inline script, or None."""
    match = re.search(r"\bgon\.%s=" % re.escape(name), text)
    if not match:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, match.end())[0]
    except ValueError:
        return None


def version_set_form(text, course_id, container_id):
    """The Edit page of an exam version set, or None: its form's fields in page order, its versions' assignment IDs in version order
    (`selected`) and the assignments Gradescope lets it hold (`offered`). As read in the sandbox course 2026-09-27: the form
    `edit_assignment_container_<id>` posts (multipart) to the set's own address with `_method` patch, the page's
    `authenticity_token`, `assignment_container[title]`, `assignment_container[submissions_anonymized]` (a hidden 0, then a checkbox
    1) and `assignment_ids`, which Gradescope's LinkVersions widget fills with the comma-joined IDs of `gon.selected_assignment_ids`
    (the versions, Version A first); `gon.unversioned_assignments` is what that widget offers. Fields keep what the page holds (the
    title, anonymous grading); `assignment_ids` is left empty for the caller."""
    action = f"/courses/{int(course_id)}/assignment_containers/{int(container_id)}"
    for tag, inner in _FORM.findall(text):
        attrs = _attrs(tag)
        if attrs.get("id") != f"edit_assignment_container_{int(container_id)}" or attrs.get("action") != action or attrs.get("method", "").lower() != "post":
            continue
        fields = []
        for raw in _INPUT.findall(inner):
            a = _attrs(raw)
            name, kind = a.get("name"), a.get("type", "text")
            if not name or kind in ("submit", "button"):
                continue
            if name == "assignment_ids":
                fields.append((name, ""))
            elif kind == "hidden" or (kind == "text" and name == "assignment_container[title]"):
                fields.append((name, a.get("value", "")))
            elif kind == "checkbox" and re.search(r"\bchecked\b", raw):
                fields.append((name, a.get("value", "on")))
        values = dict(fields)
        selected, offered = _gon(text, "selected_assignment_ids"), _gon(text, "unversioned_assignments")
        if (values.get("_method", "").lower() != "patch" or not values.get("authenticity_token") or "assignment_ids" not in values
                or "assignment_container[title]" not in values or not isinstance(selected, list) or not isinstance(offered, list)
                or not all(str(i).isdigit() for i in selected)):
            return None
        return {"action": action, "fields": fields, "title": values["assignment_container[title]"],
                "selected": [str(i) for i in selected],
                "offered": [str(int(o["id"])) for o in offered if isinstance(o, dict) and str(o.get("id") or "").isdigit()]}
    return None


def export_order(rubric, question_id):
    """A question's rubric items in the order Gradescope's evaluation export numbers them, which is how Faculty's imported lines
    are numbered (`rubric_number` "q.i"): top-level entries (ungrouped items and groups) by position, a group's items by position.
    Checked against all 890 Test 1 lines of every section on 2026-09-25: every one matched its number, name and points."""
    items = [i for i in rubric.get("rubric_items", []) if str(i.get("question_id")) == str(question_id)]
    groups = {g["id"]: g for g in rubric.get("rubric_item_groups", []) if str(g.get("question_id")) == str(question_id)}
    top = [(i["position"], 0, i, None) for i in items if not i.get("group_id")] + [(g["position"], 1, None, g) for g in groups.values()]
    ordered = []
    for _, _, item, group in sorted(top, key=lambda entry: (entry[0], entry[1])):
        if item is not None:
            ordered.append(item)
        else:
            ordered.extend(sorted((i for i in items if i.get("group_id") == group["id"]), key=lambda i: i["position"]))
    return [{"id": str(i["id"]), "points": Decimal(str(i["weight"])), "group": groups[i["group_id"]]["description"].strip() if i.get("group_id") in groups else "",
             "group_id": str(i["group_id"]) if i.get("group_id") in groups else "",
             "exclusive": bool(groups[i["group_id"]].get("mutually_exclusive")) if i.get("group_id") in groups else False,
             "description": str(i.get("description") or "").strip(),
             "name": ((groups[i["group_id"]]["description"].strip() + ": ") if i.get("group_id") in groups else "") + str(i.get("description") or "").strip()}
            for i in ordered]


def gradescope_score(question, items, chosen, adjustment=Decimal(0)):
    """The score Gradescope gives a question for these rubric items: positive scoring adds the checked items to zero, negative
    scoring takes them from the question's worth; then the point adjustment; then the question's floor (0) and ceiling (its worth)."""
    worth = Decimal(str(question["weight"]))
    total = sum((i["points"] for i in items if i["id"] in chosen), Decimal(0))
    score = (worth - total if question.get("scoring_type") == "negative" else total) + adjustment
    if question.get("floor", True):
        score = max(score, Decimal(0))
    if question.get("ceiling", True):
        score = min(score, worth)
    return score


def multipart(fields, upload=None):
    """A multipart/form-data body as a browser's form sends it: the fields in order, then one PDF (name, filename, bytes) if any."""
    boundary = "----faculty" + uuid.uuid4().hex
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields)
    if upload is None:
        return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
    name, filename, pdf = upload
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
             f"Content-Type: application/pdf\r\n\r\n").encode() + pdf + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def comment(value):
    """A grade's comment as it is compared: line endings as \\n, each row and the whole without trailing space, empty as None (a
    grading page may give back what was saved with other line endings)."""
    if value in (None, ""):
        return None
    text = "\n".join(row.rstrip() for row in str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    return text or None


class Session:
    """One signed-in Gradescope session. Not shared between threads: another thread takes a connection of its own on the same
    sign-in (`share`), as a browser tab makes several requests at once."""

    def __init__(self, settings, *, sleep=time.sleep, share=None):
        self.settings, self.base, self.sleep = settings, base_url(settings.base), sleep
        # A connection on `share`'s sign-in has the same cookies and its own connection. The connections of one sign-in sign in again
        # one at a time and only when none has already (`login`), and once Gradescope refused the saved sign-in none tries again.
        self.shared = share is not None
        self.jar = share.jar if share else http.cookiejar.CookieJar()
        self._group = share._group if share else {"lock": threading.Lock(), "generation": 0, "refused": None}
        self._seen = self._group["generation"]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar), NoRedirect(),
                                                  KeepAliveHTTPHandler(), KeepAliveHTTPSHandler())
        self.signed_in, self._last = bool(share and share.signed_in), 0.0
        self._courses, self._refused = (share._courses, share._refused) if share and share._courses is not None else (None, set())
        self._coordinator = share._coordinator if share and share._courses is not None else []
        self.pace = settings.pace  # grows after each busy answer (`slow_down`) for the rest of this session

    # --- transport --------------------------------------------------------------------------------------------------------
    def _url(self, path):
        if not path.startswith("/") or path.startswith("//"):
            raise GradescopeError("Faculty only follows Gradescope's own paths.")
        return self.base + path

    def _exchange(self, method, path, *, data=None, headers=None, timeout=TIMEOUT):
        wait = self.pace - (time.monotonic() - self._last)
        if wait > 0:
            self.sleep(wait)
        request = urllib.request.Request(self._url(path), data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            try:
                with self.opener.open(request, timeout=timeout) as answer:
                    return answer.status, answer.headers, answer.read(PAGE_LIMIT + 1)
            except urllib.error.HTTPError as answer:
                return answer.code, answer.headers, answer.read(PAGE_LIMIT + 1)
        except urllib.error.URLError as error:
            # Refused or unresolvable before anything was sent: nothing can have changed. A read changes nothing either way.
            if method == "GET" or isinstance(error.reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLCertVerificationError)):
                raise GradescopeError("Gradescope could not be reached; nothing was sent.", outcome="unavailable") from None
            raise GradescopeError("Gradescope did not answer; whether it kept this is unknown until Faculty reads it again.", outcome="unknown") from None
        except (TimeoutError, OSError, http.client.HTTPException):
            raise GradescopeError("Gradescope did not answer; whether it kept this is unknown until Faculty reads it again.",
                                  outcome="unavailable" if method == "GET" else "unknown") from None
        finally:
            self._last = time.monotonic()

    def slow_down(self):
        """After a busy answer: twice as long between requests from now on (at least a second), up to `SLOWEST`."""
        self.pace = min(max(self.pace * 2, CAREFUL), SLOWEST)

    def _refuse_busy(self, method, status, headers):
        if status in BUSY:
            self.slow_down()
            raise GradescopeBusy(status, method, retry_after(headers))

    @staticmethod
    def _to_login(status, headers):
        return status in (301, 302, 303, 307, 308) and "/login" in (headers.get("Location") or "")

    def get(self, path):
        """A page's text. A session that expired signs in again once; any other redirect or error is refused."""
        return self.download(path).decode("utf-8", "replace")

    def download(self, path):
        """What `path` answers, as bytes (a page, or a file such as the grades download). Signs in again once, like `get`."""
        for attempt in (0, 1):
            if not self.signed_in:
                self.login()
            status, headers, body = self._exchange("GET", path)
            if self._to_login(status, headers) and attempt == 0:
                self.signed_in = False
                continue
            self._refuse_busy("GET", status, headers)
            if status != 200 or len(body) > PAGE_LIMIT:
                raise GradescopeError(f"Gradescope answered {status} for a page Faculty reads.", status=status)
            return body
        raise GradescopeError("Gradescope signed Faculty out again at once.")

    def login(self):
        group = self._group
        with group["lock"]:
            if group["refused"]:
                raise SignInRefused(str(group["refused"]), outcome="unavailable")  # never one more attempt per connection
            if group["generation"] != self._seen:  # another connection of this sign-in has just signed in: its cookies are ours
                self._seen, self.signed_in = group["generation"], True
                return
            try:
                self._sign_in()
            except SignInRefused as error:
                group["refused"] = error
                raise
            group["generation"] += 1
            self._seen = group["generation"]

    def _sign_in(self):
        email, password = load_credentials(self.settings.credentials_file)
        status, headers, body = self._exchange("GET", "/")
        self._refuse_busy("GET", status, headers)
        token = parse(body.decode("utf-8", "replace")).login_token if status == 200 else None
        if not token:
            raise GradescopeError("Gradescope's sign-in page has changed; nothing was sent.", outcome="unavailable")
        form = urllib.parse.urlencode({"utf8": "✓", "authenticity_token": token, "session[email]": email, "session[password]": password,
                                       "session[remember_me]": "0", "commit": "Log In"}).encode()
        status, headers, _ = self._exchange("POST", "/login", data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
        self._refuse_busy("GET", status, headers)  # a sign-in Gradescope was too busy to answer is not a refused password (and changes nothing)
        if status not in (301, 302, 303) or "/login" in (headers.get("Location") or ""):
            raise SignInRefused("Gradescope did not accept the saved sign-in. Run save-gradescope.py again.", outcome="unavailable", status=status)
        self.signed_in = True

    def close(self):
        """Forget this session, as closing a browser tab does. Gradescope's /logout is never visited: it signs the instructor out of
        Gradescope everywhere, their own browser included (requirement, 2026-09-25).
        Sessions do not end one another by signing in, so several may be open at once. A connection on another's sign-in forgets
        only itself."""
        if not self.shared:
            self.jar.clear()
        self.signed_in, self._courses = False, None

    # --- reading ----------------------------------------------------------------------------------------------------------
    def courses(self):
        """The instructor's courses, never the forbidden one (whose ID is remembered so nothing can name it)."""
        if self._courses is None:
            listed = course_list(self.get("/account"))
            self._refused = {c["id"] for c in listed if forbidden(c["short"], c["name"])}
            self._courses = [c for c in listed if c["id"] not in self._refused]
            self._coordinator = [c for c in listed if c["id"] in self._refused]
        return list(self._courses)

    def coordinator_courses(self):
        """The coordinator's course(s) the instructor teaches in, which only Import from Gradescope may READ (GRADESCOPE "THE
        COORDINATOR'S COURSE", requirement, 2026-10-25: its rubrics are the ones every instructor uses)."""
        self.courses()
        return list(self._coordinator)

    def guard(self, course_id, *, coordinator_read=False):
        """The course ID when Faculty may use it: one of the instructor's own courses, or, for a READ that names
        `coordinator_read` (the rubric import's assignments, outline and rubric pages only), the coordinator's course too."""
        course_id = str(course_id)
        known = {c["id"] for c in self.courses()}
        if coordinator_read and course_id in {c["id"] for c in self._coordinator}:
            return course_id
        if course_id in self._refused:
            raise GradescopeError("That is the course Faculty never touches (Math 140 for Instructors).")
        if course_id not in known:
            raise GradescopeError("That Gradescope course is not one of the instructor's courses.")
        return course_id

    def roster(self, course_id):
        return roster_rows(self.get(f"/courses/{self.guard(course_id)}/memberships"))

    def assignments(self, course_id, *, coordinator_read=False):
        rows = parse(self.get(f"/courses/{self.guard(course_id, coordinator_read=coordinator_read)}/assignments")).props("AssignmentsTable").get("table_data", [])
        return [{"id": str(r["id"]).rsplit("_", 1)[-1], "kind": r.get("type"), "title": str(r.get("title") or ""), "parent": str(r.get("parent_id") or "").rsplit("_", 1)[-1],
                 "version": r.get("version_name") or "", "points": r.get("total_points"), "submissions": r.get("num_active_submissions") or 0,
                 "published": bool(r.get("is_published")), "bubble_sheet": bool(r.get("bubble_sheet")),
                 # Gradescope's own "% Graded" column (None when the list does not say): publishing warns when it is not 100.
                 "graded": r.get("grading_progress") if isinstance(r.get("grading_progress"), (int, float)) else None} for r in rows]

    def outline(self, course_id, assignment_id, *, coordinator_read=False):
        path = f"/courses/{self.guard(course_id, coordinator_read=coordinator_read)}/assignments/{int(assignment_id)}/outline/edit"
        page = parse(self.get(path))
        props = page.props("AssignmentOutline")
        template = props.get("template") or {}
        return {"type": props.get("assignment", {}).get("type"), "variable_length": bool(props.get("assignment", {}).get("variable_length?")),
                "title": str(props.get("assignment", {}).get("title") or ""),
                "pages": int(template.get("page_count") or 0), "questions": props.get("outline") or [],
                # Gradescope reads a new exam's template into pages after it is uploaded; its outline waits for "complete".
                "template_status": str(template.get("status_string") or ""), "path": path, "csrf": page.csrf,
                # The template PDF's own signed link in Gradescope's uploads store (`template_pdf` reads it; RUBRICS FROM GRADESCOPE).
                "template_id": str(template.get("id") or ""), "template_url": str(template.get("url") or ""),
                "template_filename": str(template.get("filename") or "")}

    def template_pdf(self, outline):
        """The assignment's blank template PDF, read from the signed link its outline gives (GRADESCOPE "IMPORTING RUBRICS FROM
        GRADESCOPE", 2026-10-24). Only Gradescope's own uploads store answers it (or a loopback test server's own path), with no
        cookie and no redirect followed; anything else, a non-PDF or a file over 25 MB is refused. A read: nothing is sent."""
        url = outline.get("template_url") or ""
        parts = urllib.parse.urlsplit(url)
        own = self.base != BASE and url.startswith(self.base + "/")  # a loopback test server only
        if not own and not (parts.scheme == "https" and UPLOADS_HOST.fullmatch(parts.hostname or "")):
            raise GradescopeError("Gradescope gave no template link Faculty may read.")
        wait = self.pace - (time.monotonic() - self._last)
        if wait > 0:
            self.sleep(wait)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT) as answer:
                status, body = answer.status, answer.read(TEMPLATE_LIMIT + 1)
        except urllib.error.HTTPError as answer:
            status, body = answer.code, b""
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise GradescopeError("Gradescope's template could not be read; nothing was sent.", outcome="unavailable") from None
        finally:
            self._last = time.monotonic()
        if status != 200 or not body.startswith(b"%PDF") or len(body) > TEMPLATE_LIMIT:
            raise GradescopeError(f"Gradescope's template could not be read (it answered {status}).")
        return body

    def rubric(self, course_id, assignment_id, *, coordinator_read=False):
        return parse(self.get(f"/courses/{self.guard(course_id, coordinator_read=coordinator_read)}/assignments/{int(assignment_id)}/rubric/edit")).props("AssignmentRubric")

    def rubric_editor(self, course_id, assignment_id):
        """The Edit Rubric page as its editor uses it: the props, each question's own URLs and the page's token."""
        path = f"/courses/{self.guard(course_id)}/assignments/{int(assignment_id)}/rubric/edit"
        page = parse(self.get(path))
        props = page.props("AssignmentRubric")
        return {"props": props, "urls": ((props.get("urls") or {}).get("question_urls") or {}), "csrf": page.csrf, "path": path}

    def submissions(self, course_id, assignment_id):
        """Manage Submissions: every student of the course with whether they have a submission in ANY version of the exam and in
        which, and this version's submissions with their owners. Also the upload form's token."""
        path = f"/courses/{self.guard(course_id)}/assignments/{int(assignment_id)}/submissions"
        text = self.get(path)
        page = parse(text)
        props = page.props("SubmissionsManager")
        token = re.search(r'id="submissions-manager-upload-form".*?name="authenticity_token" value="([^"]+)"', text, re.S)
        return {"students": [{"id": str(s["id"]), "submission": bool(s.get("submission")), "version": s.get("version_name") or "", "sid": str(s.get("sid") or "")}
                             for s in props.get("students", [])],
                "submissions": {str(k): [str(u) for u in (v.get("active_user_ids") or [])] for k, v in (props.get("submissions") or {}).items()},
                "token": token.group(1) if token else None, "csrf": page.csrf, "path": path}

    def question_submissions(self, course_id, question_id):
        """Every question submission of one Gradescope question: whether it is graded, by whom last, and its score (`question_rows`)."""
        course_id = self.guard(course_id)
        return question_rows(self.get(f"/courses/{course_id}/questions/{int(question_id)}/submissions"), course_id, question_id)

    def exam(self, course_id, container_id):
        """A bubble-sheet exam with versions (an assignment container). Its version assignments are not in the assignments list: its
        own Manage Submissions page names them (`assignmentContainerVersions`; "?" is where Gradescope files a sheet whose version it
        could not read, never a version Faculty chooses). Also every student of the course with whether they have a sheet in ANY
        version, and the exam's submissions with their owners."""
        props = parse(self.get(f"/courses/{self.guard(course_id)}/assignment_containers/{int(container_id)}/submissions")).props("SubmissionsManager")
        versions = {}
        for row in (props.get("assignmentContainerVersions") or {}).values():
            letter = re.fullmatch(r"(?:version\s*)?([a-z])", str(row.get("versionName") or "").strip(), re.I)
            if letter and row.get("assignmentId"):
                versions[letter.group(1).upper()] = str(row["assignmentId"])
        return {"versions": versions,
                "students": [{"id": str(s["id"]), "submission": bool(s.get("submission")), "version": s.get("version_name") or "", "sid": str(s.get("sid") or "")}
                             for s in props.get("students", [])],
                "submissions": {str(k): [str(u) for u in (v.get("active_user_ids") or [])] for k, v in (props.get("submissions") or {}).items()}}

    def container_scores(self, course_id, container_id):
        """A bubble-sheet exam's grades as Gradescope scored them (`container_scores`): its Download Grades file, read only."""
        return container_scores(self.download(f"/courses/{self.guard(course_id)}/assignment_containers/{int(container_id)}/scores/csv.zip"))

    def submission(self, course_id, assignment_id, submission_id):
        props = parse(self.get(f"/courses/{self.guard(course_id)}/assignments/{int(assignment_id)}/submissions/{int(submission_id)}")).props("AssignmentSubmissionViewer")
        attachment = props.get("pdf_attachment") or {}
        submission = props.get("assignment_submission") or {}
        return {"owners": [str(o["user_id"]) for o in props.get("ownerships", []) if o.get("active")], "filename": attachment.get("filename") or "",
                # Replaced submissions stay on Gradescope as inactive ones: grades go only onto the active one.
                "active": bool(submission.get("active")) and not props.get("inactive"),
                "pages": int(attachment.get("page_count") or 0), "status": attachment.get("status_string") or "",
                "questions": {str(q["question_id"]): str(q["id"]) for q in props.get("question_submissions", []) if q.get("active", True)}}

    def grader(self, course_id, question_id, question_submission_id):
        path = f"/courses/{self.guard(course_id)}/questions/{int(question_id)}/submissions/{int(question_submission_id)}/grade"
        page = parse(self.get(path))
        props = page.props("SubmissionGrader")
        # Read strictly: what says whether someone graded this question must be there, or nothing is written (a renamed field
        # must never make a graded question look ungraded). Seen on the real site 2026-09-25: an ungraded question has an
        # evaluation whose id is null and no applied items; any saved grade has an evaluation id.
        evaluation, evaluations = props.get("evaluation"), props.get("rubric_item_evaluations")
        if not isinstance(evaluation, dict) or "id" not in evaluation or "points" not in evaluation or not isinstance(evaluations, list) \
                or any(not isinstance(e, dict) or "rubric_item_id" not in e or "present" not in e for e in evaluations) or not isinstance(props.get("rubric_items"), list):
            raise GradescopeError("Gradescope's grading page no longer says how the question is graded; nothing was sent.")
        return {"path": path, "csrf": page.csrf, "save": (props.get("urls") or {}).get("save_grade"), "question": props.get("question") or {},
                "published": bool((props.get("assignment") or {}).get("published")),
                "items": {str(i["id"]): Decimal(str(i["weight"])) for i in props.get("rubric_items", [])},
                "descriptions": {str(i["id"]): str(i.get("description") or "") for i in props.get("rubric_items", [])},
                "applied": sorted(str(e["rubric_item_id"]) for e in evaluations if e.get("present")),
                "evaluation_id": evaluation.get("id"), "points": evaluation.get("points"), "comments": evaluation.get("comments"),
                "score": None if (props.get("submission") or {}).get("score") is None else Decimal(str(props["submission"]["score"]))}

    def regrade_page(self, course_id, question_id, question_submission_id):
        """A question's grading page as a regrade reply needs it (grading/regrade_requests.py): its requests (the open one first,
        each {id, student_comment, staff_comment, created_at, open}), the address a reply goes to (`urls.regrade_request`, read on
        the real site 2026-09-30), the page's token and the question's score there. Read strictly: a page that no longer shows these
        as Faculty reads them sends nothing."""
        path = f"/courses/{self.guard(course_id)}/questions/{int(question_id)}/submissions/{int(question_submission_id)}/grade"
        page = parse(self.get(path))
        props = page.props("SubmissionGrader")
        url, opened, closed = (props.get("urls") or {}).get("regrade_request"), props.get("open_request"), props.get("closed_requests")
        if not isinstance(url, str) or not re.fullmatch(rf"/courses/{re.escape(str(course_id))}/assignments/\d+/submissions/\d+/regrade_requests", url) \
                or not isinstance(closed, list) or (opened is not None and not isinstance(opened, dict)):
            raise GradescopeError("Gradescope's grading page no longer shows its regrade requests as Faculty reads them; nothing was sent.")
        found = [{**item, "open": is_open} for item, is_open in [(opened, True)] + [(item, False) for item in closed] if isinstance(item, dict)]
        evaluation, submission = props.get("evaluation") or {}, props.get("submission") or {}
        return {"path": path, "csrf": page.csrf, "url": url, "requests": found, "question": props.get("question") or {},
                "score": None if submission.get("score") is None else str(submission["score"]),
                "points": None if evaluation.get("points") is None else str(evaluation["points"]), "graded": evaluation.get("id") is not None}

    def reply_regrade(self, page, course_id, request_id, text):
        """The grading page's "Close & Respond": one JSON PUT of `{"regrade_request": {"id", "staff_comment"}}` to the page's
        `regrade_request` address plus the request's ID, which answers the request and closes it (the grading bundle, read
        2026-09-30). Gradescope's answer is not trusted as the outcome: the caller reads the page back."""
        return self._json_write("PUT", f"{page['url']}/{int(request_id)}", {"regrade_request": {"id": int(request_id), "staff_comment": text}},
                                page["csrf"], page["path"], course_id)

    # --- writing ----------------------------------------------------------------------------------------------------------
    def upload(self, course_id, assignment_id, owner_id, pdf, filename):
        """Manage Submissions -> Upload Submission for one student: exactly the dialog's form. Gradescope answers with a redirect
        whether or not it kept the file, so the caller must read the submissions back; this only says whether it was sent."""
        listing = self.submissions(course_id, assignment_id)
        if not listing["token"]:
            raise GradescopeError("Gradescope's upload form has changed; nothing was sent.")
        owner = str(int(owner_id))
        if any(s["id"] == owner and s["submission"] for s in listing["students"]) or any(owner in users for users in listing["submissions"].values()):
            # Checked again on the very page the form comes from: Upload Submission would REPLACE a paper someone just uploaded.
            raise GradescopeError("Gradescope now has a paper for this student; Faculty's was not sent.")
        body, content_type = multipart((("utf8", "✓"), ("authenticity_token", listing["token"]), ("owner_id", str(int(owner_id))), ("commit", "Upload")),
                                       ("pdf_attachment", filename, pdf))
        status, headers, _ = self._exchange("POST", listing["path"], data=body, timeout=UPLOAD_TIMEOUT, headers={
            "Content-Type": content_type, "X-CSRF-Token": listing["csrf"] or "", "Referer": self.base + listing["path"]})
        return self._written(status, headers, "the paper was not uploaded")

    # --- creating an assignment (requirement, 2026-09-27; `gradescope_create` decides, and reads every write back) -----------
    def create_assignment(self, course_id, title, pdf, filename):
        """Create Assignment, uploaded by the instructor with its template (Gradescope's "Exam / Quiz" type: a fixed-length paper of
        any kind), exactly as Gradescope's own form sends it. The answer is a redirect whether or not Gradescope kept it: the caller
        reads the assignments back."""
        course_id = self.guard(course_id)
        form_path = f"/courses/{course_id}/assignments/new"
        page = parse(self.get(form_path))
        props = page.props("CreateAssignment")
        if not page.csrf or (props.get("urls") or {}).get("createAssignment") != f"/courses/{course_id}/assignments":
            raise GradescopeError("Gradescope's Create Assignment page has changed; nothing was sent.")
        body, content_type = multipart((("utf8", "✓"), ("authenticity_token", page.csrf), ("assignment[title]", title),
                                        ("assignment[student_submission]", "false"), ("assignment[submissions_anonymized]", "0"),
                                        ("assignment[when_to_create_rubric]", "while_grading"),
                                        ("assignment[rubric_locking_setting]", str(props.get("rubricLockingSetting") or "all_edit"))),
                                       ("template_pdf", filename, pdf))
        status, headers, _ = self._exchange("POST", f"/courses/{course_id}/assignments", data=body, timeout=UPLOAD_TIMEOUT, headers={
            "Content-Type": content_type, "Referer": self.base + form_path})
        return self._written(status, headers, "the assignment was not created")

    def create_bubble_sheet(self, course_id, title):
        """Create Assignment as a Bubble Sheet (requirement, 2026-10-26), exactly as Gradescope's own form sends that type: no template (Gradescope's own bubble sheet is it), uploaded by
        the instructor. The answer is a redirect whether or not Gradescope kept it: the caller reads the assignments back."""
        course_id = self.guard(course_id)
        form_path = f"/courses/{course_id}/assignments/new"
        page = parse(self.get(form_path))
        props = page.props("CreateAssignment")
        if not page.csrf or (props.get("urls") or {}).get("createAssignment") != f"/courses/{course_id}/assignments" \
                or not (props.get("featuresAccess") or {}).get("BUBBLE_SHEET"):
            raise GradescopeError("Gradescope's Create Assignment page has changed (or offers no Bubble Sheet); nothing was sent.")
        body, content_type = multipart((("utf8", "✓"), ("authenticity_token", page.csrf), ("assignment[bubble_sheet]", "true"),
                                        ("assignment[title]", title), ("assignment[student_submission]", "false"),
                                        ("assignment[submissions_anonymized]", "0"),
                                        ("assignment[rubric_locking_setting]", str(props.get("rubricLockingSetting") or "all_edit"))))
        status, headers, _ = self._exchange("POST", f"/courses/{course_id}/assignments", data=body, timeout=UPLOAD_TIMEOUT, headers={
            "Content-Type": content_type, "Referer": self.base + form_path})
        return self._written(status, headers, "the bubble-sheet exam was not created")

    def bubble_key_editor(self, course_id, kind, target_id):
        """A bubble-sheet exam's Edit Answer Key page (a single bubble-sheet assignment's, or a version set's): each version's key as
        Gradescope holds it ({"Version A": {question number: {"answer", "scoring_type", "weight"}}}, or one key under the
        assignment's own name), the URLs its Save and Add Version use, and the page's token."""
        course_id = self.guard(course_id)
        plural = "assignment_containers" if kind == "assignment_container" else "assignments"
        path = f"/courses/{course_id}/{plural}/{int(target_id)}/bubble_sheet_answer_key/edit"
        page = parse(self.get(path))
        props = page.props("BubbleSheetAnswerKey")
        keys = props.get("answerKeys")
        if not isinstance(keys, dict) or not keys or not page.csrf:
            raise GradescopeError("Gradescope's Edit Answer Key page has changed; nothing was sent.")
        return {"path": path, "csrf": page.csrf, "keys": {str(name): (entry.get("answerKey") or {}) for name, entry in keys.items()},
                "saves": {str(name): str(entry.get("saveUrl") or "") for name, entry in keys.items()},
                "locked": {str(name): bool(entry.get("rubricsLocked")) for name, entry in keys.items()},
                "add_version": str(props.get("addVersionUrl") or ""), "container_save": str(props.get("containerSaveUrl") or ""),
                "max_versions": int(props.get("maxVersions") or 0)}

    def add_bubble_version(self, course_id, editor):
        """Edit Answer Key's Add Version, as its button sends it: one more version, which Gradescope names itself (a single bubble-sheet
        assignment becomes a version set). The caller reads the page again: the answer only says what Gradescope made."""
        return self._json_write("POST", editor["add_version"], {}, editor["csrf"], editor["path"], course_id)

    def save_bubble_key(self, course_id, editor, keys):
        """Edit Answer Key's Save: every version's key at once on a version set's page, else the one key. `keys` maps the page's
        version names to {question number: {"answer": letters, "scoring_type", "weight"}}, as its editor sends them."""
        names = sorted(editor["keys"])
        if sorted(keys) != names:
            raise GradescopeError("The answer key names other versions than Gradescope's page; nothing was sent.")
        sent = {name: {number: {**entry, "rubricsLocked": editor["locked"].get(name, False)} for number, entry in keys[name].items()} for name in names}
        if editor["container_save"]:
            return self._json_write("PATCH", editor["container_save"], {"bubble_sheet_assignment_answer_key": sent}, editor["csrf"], editor["path"], course_id)
        if len(names) != 1:
            raise GradescopeError("Gradescope's Edit Answer Key page has changed; nothing was sent.")
        return self._json_write("PATCH", editor["saves"][names[0]], {"bubble_sheet_assignment_answer_key": sent[names[0]]}, editor["csrf"],
                                editor["path"], course_id)

    def save_outline(self, course_id, assignment_id, questions, csrf):
        """Edit Outline's Save: every question (title, points, answer boxes as page percentages), and no name or ID region."""
        course_id = self.guard(course_id)
        path = f"/courses/{course_id}/assignments/{int(assignment_id)}/outline/"
        payload = {"assignment": {"identification_regions": {"name": None, "sid": None}}, "question_data": questions}
        return self._json_write("PATCH", path, payload, csrf, f"/courses/{course_id}/assignments/{int(assignment_id)}/outline/edit", course_id)

    def save_grading_options(self, course_id, editor, question_id, scoring_type, floor, ceiling):
        """A question's scoring (positive or negative) and its score bounds, as the rubric settings dialog saves them."""
        url = (editor["urls"].get(str(question_id)) or {}).get("save_grading_options")
        payload = {"question": {"id": int(question_id), "ceiling": "1" if ceiling else "0", "floor": "1" if floor else "0", "scoring_type": scoring_type}}
        return self._json_write("POST", url, payload, editor["csrf"], editor["path"], course_id)

    def add_rubric_item(self, course_id, editor, question_id, description, weight):
        """One rubric item at the end of a question's rubric, as the editor's "+" does; Gradescope answers with the item it made."""
        url = (editor["urls"].get(str(question_id)) or {}).get("rubric_item")
        return self._json_write("POST", url, {"rubric_item": {"description": description, "weight": float(weight), "group_id": None}},
                                editor["csrf"], editor["path"], course_id)

    def rename_rubric_item(self, course_id, editor, question_id, item, description):
        """One rubric item's new description, as the editor saves an item it changed: the PUT of the item with its points and group as
        they are (read on the real site 2026-10-27, Exam 2 Q24). The caller reads the rubric back."""
        url = (editor["urls"].get(str(question_id)) or {}).get("rubric_item")
        payload = {"rubric_item": {"description": description, "weight": float(item["weight"]), "group_id": item.get("group_id")}}
        return self._json_write("PUT", f"{url}/{int(item['id'])}" if url else None, payload, editor["csrf"], editor["path"], course_id)

    def delete_rubric_item(self, course_id, editor, question_id, item_id):
        url = (editor["urls"].get(str(question_id)) or {}).get("rubric_item")
        return self._json_write("DELETE", f"{url}/{int(item_id)}" if url else None, None, editor["csrf"], editor["path"], course_id)

    def link_versions(self, course_id, title, assignment_ids):
        """Organize Exam Versions: the assignments, in version order (A, B, ...), become one version set named `title`."""
        course_id = self.guard(course_id)
        page = parse(self.get(f"/courses/{course_id}/assignments"))
        form = urllib.parse.urlencode({"title": title, "assignment_ids[]": [str(int(a)) for a in assignment_ids]}, doseq=True).encode()
        status, headers, _ = self._exchange("POST", f"/courses/{course_id}/assignment_containers", data=form, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": page.csrf or "", "Referer": self.base + f"/courses/{course_id}/assignments"})
        return self._written(status, headers, "the versions were not grouped", ok=(200, 201))

    def version_set(self, course_id, container_id, *, coordinator_read=False):
        """An exam version set's Edit page as `version_set_form` reads it (requirement, 2026-10-06: a makeup version added to it). It
        names a bubble-sheet exam's version assignments too, with no student on it (the rubric import's key read)."""
        course_id = self.guard(course_id, coordinator_read=coordinator_read)
        path = f"/courses/{course_id}/assignment_containers/{int(container_id)}/edit"
        form = version_set_form(self.get(path), course_id, container_id)
        if not form:
            raise GradescopeError("Gradescope's Edit Exam Version Set page has changed; nothing was sent.")
        return {**form, "path": path}

    def save_version_set(self, course_id, form, assignment_ids):
        """That page's Save with `assignment_ids` as the set's versions, in version order (Gradescope letters them A, B ... so), and
        every other field as the page held it. Gradescope answers with a redirect whether or not it kept them: the caller reads the
        assignments back."""
        course_id = self.guard(course_id)
        if not form["action"].startswith(f"/courses/{course_id}/assignment_containers/"):
            raise GradescopeError("That version set is not in this course; nothing was sent.")
        ids = ",".join(str(int(a)) for a in assignment_ids)
        body, content_type = multipart([(name, ids if name == "assignment_ids" else value) for name, value in form["fields"]] + [("commit", "Save")])
        status, headers, _ = self._exchange("POST", form["action"], data=body, headers={
            "Content-Type": content_type, "Referer": self.base + form["path"]})
        return self._written(status, headers, "the version was not added to the set")

    # --- publishing grades (requirement, 2026-10-02) ---
    def publish(self, course_id, kind, target_id):
        """Review Grades' Publish Grades for one assignment or exam version set, exactly as its form sends it (`publish_form`).
        Gradescope answers with a redirect whether or not it published, so the caller reads the assignments list back. Students
        then see their results; Gradescope emails nobody for it (its Email Students is a separate button Faculty never presses)."""
        course_id = self.guard(course_id)
        path = f"/courses/{course_id}/{'assignment_containers' if kind == 'assignment_container' else 'assignments'}/{int(target_id)}/review_grades"
        form = publish_form(self.get(path), course_id, kind, target_id)
        if not form:
            raise PublishRefused("Gradescope's Review Grades page has no Publish Grades form Faculty recognizes; nothing was sent.")
        action, fields = form
        status, headers, _ = self._exchange("POST", action, data=urllib.parse.urlencode(fields).encode(), headers={
            "Content-Type": "application/x-www-form-urlencoded", "Referer": self.base + path})
        return self._written(status, headers, "the grades were not published")

    def _json_write(self, method, path, payload, csrf, referer, course_id):
        """A JSON request of Gradescope's own editors. Only to a path of the course Faculty is working in, with the page's token."""
        if not path or not csrf or not re.fullmatch(rf"/courses/{re.escape(str(course_id))}/[\w/]+/?", path):
            raise GradescopeError("Gradescope's editor page has changed; nothing was sent.")
        data = None if payload is None else json.dumps(payload).encode()
        status, headers, raw = self._exchange(method, path, data=data, headers={
            "Content-Type": "application/json", "Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf, "Referer": self.base + referer})
        self._written(status, headers, "nothing was changed", ok=(200, 201, 204))
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _written(self, status, headers, lost, ok=None):
        """A write's answer: signed out or busy is raised; with `ok`, any other status is Gradescope refusing it."""
        if self._to_login(status, headers):
            self.signed_in = False
            raise GradescopeError(f"Gradescope had signed Faculty out; {lost}.", status=status)
        self._refuse_busy("POST", status, headers)  # any write: after a 502/504 it may have landed
        if ok and status not in ok:
            raise GradescopeError(f"Gradescope refused this ({status}); {lost}.", status=status)
        return status

    def save_grade(self, grader, chosen, points, comments=None):
        """The grading page's own save: every rubric item of the question set on or off, the point adjustment, and the comment: the
        grade's batch lines from Certification and Export (`batch_adjustments.comment_text`, 2026-09-27), else none. No other comment
        is ever sent. Gradescope's answer is not trusted as the outcome: the caller reads the grade back."""
        if not grader["save"] or not grader["csrf"]:
            raise GradescopeError("Gradescope's grading page has changed; nothing was sent.")
        payload = {"rubric_items": {item: {"score": "true" if item in chosen else "false"} for item in grader["items"]},
                   "question_submission_evaluation": {"points": None if points is None else float(points), "comments": None if comments is None else comments}}
        status, headers, _ = self._exchange("POST", grader["save"], data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": grader["csrf"], "Referer": self.base + grader["path"]})
        return self._written(status, headers, "the grade was not saved")
