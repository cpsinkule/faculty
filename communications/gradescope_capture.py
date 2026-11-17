"""Reading Gradescope regrade requests for the communications record (AGENTS.md "Communications record").

Gradescope has no messages of its own; a regrade request is the one written exchange there: the student's request on a question
and the reply. For each section's linked Gradescope course (never Math 140 for Instructors) every assignment's Regrade Requests page
is read (`/courses/<c>/assignments/<a>/regrade_requests`: a table with the student, the question and a link to the question's grading
page), and for each listed request its grading page (`SubmissionGrader`), whose `open_request` and `closed_requests` hold what the
student wrote and the reply. GET only, through `grading.gradescope_web.Session` (paced, signed in with the saved sign-in, never
/logout).

The student is known by Gradescope's own roster: the name the listing shows must be exactly one student of the course's roster
(names compared without case, accents, punctuation or word order), whose SID must be a UIN of the section's term; when the grading
page names the submission's owners, they must agree. Anything else is counted as not matched and read again next round, never guessed.
These pages were not read on the real site while this was written (the shapes come from the grading page Faculty already reads and
from others' published readers); anything unlike them stops the read of that page with a reason instead of guessing.
"""
import hashlib
import html
import re
from html.parser import HTMLParser

from grading import gradescope_web as gw
from student_photos.service import name_key

GRADE_LINK = re.compile(r"^/courses/(\d+)/questions/(\d+)/submissions/(\d+)/grade")
STUDENT_HEADERS = ("student", "name", "student name")
QUESTION_HEADERS = ("question", "question title")
GRADER_HEADERS = ("grader", "graded by", "last graded by")
COMPLETED_HEADERS = ("completed", "completed at", "closed", "resolved")


class _Table(HTMLParser):
    """The first table of a page: its header cells and each row's cells (text) with the links inside them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.done, self.headers, self.rows = 0, False, [], []
        self._row, self._cell, self._head, self._links = None, None, False, []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self._row, self._links = [], []
        elif self.depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell, self._head = [], tag == "th"
        elif self.depth == 1 and tag == "a" and self._row is not None:
            href = dict(attrs).get("href") or ""
            if href:
                self._links.append(html.unescape(href))

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.done = True
        elif self.depth == 1 and tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append((" ".join("".join(self._cell).split()), self._head))
            self._cell = None
        elif self.depth == 1 and tag == "tr" and self._row is not None:
            if self._row and all(head for _, head in self._row):
                self.headers = [text.casefold() for text, _ in self._row]
            elif self._row:
                self.rows.append(([text for text, _ in self._row], list(self._links)))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def listing(text):
    """The requests a Regrade Requests page lists: [{student, question, grader, completed, course, question_id, qsub_id}]. A page
    with no table lists none; a table whose columns Faculty does not recognize raises (the page changed)."""
    table = _Table()
    table.feed(text)
    if not table.headers and not table.rows:
        return []

    def column(names):
        return next((i for i, header in enumerate(table.headers) if header in names), None)

    student, question, grader, completed = column(STUDENT_HEADERS), column(QUESTION_HEADERS), column(GRADER_HEADERS), column(COMPLETED_HEADERS)
    if student is None or question is None:
        raise gw.GradescopeError("Gradescope's regrade requests page no longer names its student and question columns; nothing was read.")
    found = []
    for cells, links in table.rows:
        link = next((GRADE_LINK.match(href) for href in links if GRADE_LINK.match(href)), None)
        if not link:
            continue
        cell = lambda index: cells[index] if index is not None and index < len(cells) else ""
        found.append({"student": cell(student), "question": cell(question), "grader": cell(grader), "completed": cell(completed),
                      "course": link.group(1), "question_id": link.group(2), "qsub_id": link.group(3)})
    return found


def _text(value):
    return str(value or "").replace("\r\n", "\n").strip() if isinstance(value, (str, int, float)) else ""


def exchanges(props):
    """The requests a question's grading page holds (open first, then closed), each {key, created_at, updated_at, request, reply,
    replied_by, state}. A request without the student's text is not one."""
    found = []
    for state, items in (("open", [props.get("open_request")]), ("closed", props.get("closed_requests") or [])):
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            request = _text(item.get("student_comment"))
            if not request:
                continue
            created = _text(item.get("created_at"))
            key = _text(item.get("id")) or hashlib.sha256(f"{created}\n{request}".encode()).hexdigest()[:24]
            replier = item.get("staff") or item.get("grader") or item.get("staff_user") or {}
            found.append({"key": key, "created_at": created, "updated_at": _text(item.get("updated_at") or item.get("completed_at")),
                          "request": request, "reply": _text(item.get("staff_comment")), "state": state,
                          "replied_by": _text(replier.get("name")) if isinstance(replier, dict) else _text(replier)})
    return found


def owners(props):
    """The Gradescope user IDs the grading page names as the submission's owners, when it names them at all."""
    ids = set()
    for key in ("assignment_submission", "submission"):
        holder = props.get(key)
        if isinstance(holder, dict):
            for owner in holder.get("owners") or holder.get("ownerships") or []:
                if isinstance(owner, dict) and owner.get("user_id") is not None and owner.get("active", True):
                    ids.add(str(owner["user_id"]))
                elif isinstance(owner, (int, str)) and str(owner).isdigit():
                    ids.add(str(owner))
    return ids


def grading_page(session, course, question, qsub):
    """(props of the question's grading page, question title, assignment title, path of the page)."""
    path = f"/courses/{session.guard(course)}/questions/{int(question)}/submissions/{int(qsub)}/grade"
    props = gw.parse(session.get(path)).props("SubmissionGrader")
    question_title = _text((props.get("question") or {}).get("title")) if isinstance(props.get("question"), dict) else ""
    assignment_title = _text((props.get("assignment") or {}).get("title")) if isinstance(props.get("assignment"), dict) else ""
    return props, question_title, assignment_title, path


def student_of(row, props, roster):
    """The one SID the request belongs to, or (None, reason). The listing's name must be exactly one roster student; owners the
    grading page names must agree."""
    by_name = [s for s in roster if name_key(s["name"]) == name_key(row["student"])] if row["student"] else []
    named = owners(props)
    by_owner = [s for s in roster if s["user_id"] in named]
    if named:
        if len(by_owner) != 1:
            return None, "the request's submission has several owners or none on the roster"
        if by_name and by_name != by_owner:
            return None, "the name the request shows is not the submission's owner"
        return by_owner[0]["sid"], ""
    if len(by_name) == 1:
        return by_name[0]["sid"], ""
    return None, "no single student of the course's roster has the name the request shows" if not by_name else "two students share the name"
