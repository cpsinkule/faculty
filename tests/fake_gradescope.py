"""A loopback stand-in for the parts of Gradescope that `grading.gradescope_web.Session` reads and writes. No network, no
credentials of anyone real: tests start it on 127.0.0.1 port 0 and hand its `url` to `gradescope_web.Settings(base=...)`.

It serves the same pages and answers the same requests as the real site did on 2026-09-25 (sandbox course "Faculty upload test";
see AGENTS.md "Sending grades to Gradescope"), with the SAME shapes: HTML with a `csrf-token` meta tag and React components as
`data-react-class` elements with HTML-escaped JSON `data-react-props` (AssignmentsTable, AssignmentOutline, AssignmentRubric,
SubmissionsManager, AssignmentSubmissionViewer, SubmissionGrader), the /account course boxes under their term headings, the
membership rows with their edit buttons, the sign-in form and its authenticity token, the upload form of Manage Submissions.

    GET  /                                                   sign-in page (form action="/login" with an authenticity_token)
    POST /login                                              302 to /account with a session cookie, or 302 back to /login
    GET  /logout                                             302 to /, the session ends
    GET  /account                                            Instructor Courses, then Student Courses, grouped by term
    GET  /courses/<c>/memberships                            the roster (role 0 = student)
    GET  /courses/<c>/assignments                            AssignmentsTable
    GET  /courses/<c>/assignments/<a>/outline/edit           AssignmentOutline
    GET  /courses/<c>/assignments/<a>/rubric/edit            AssignmentRubric (the real hierarchical positions)
    GET  /courses/<c>/assignments/<a>/submissions            SubmissionsManager and the upload form
    POST /courses/<c>/assignments/<a>/submissions            multipart owner_id + pdf_attachment; ALWAYS answers 302
    GET  /courses/<c>/assignments/<a>/submissions/<s>        AssignmentSubmissionViewer
    GET  /courses/<c>/questions/<q>/submissions/<qs>/grade   SubmissionGrader
    POST /courses/<c>/questions/<q>/submissions/<qs>/save_grade
    PUT  /courses/<c>/assignments/<a>/submissions/<s>/regrade_requests/<r>   the grading page's "Close & Respond" (a regrade reply)
                                                             JSON; 200 JSON when Accept asks for JSON, else 404 HTML AFTER saving
    GET  /courses/<c>/assignments/new                        CreateAssignment (creating an assignment, 2026-09-27)
    POST /courses/<c>/assignments                            multipart title + template_pdf; ALWAYS answers 302 (to the outline)
    PATCH /courses/<c>/assignments/<a>/outline/              JSON question_data: new questions are negative-scored with one
                                                             default item "Correct" (0), exactly as the real site made them
    POST /courses/<c>/questions/<q>/save_grading_options     JSON {question: {scoring_type, floor, ceiling}}
    POST /courses/<c>/questions/<q>/rubric_items             JSON {rubric_item: {...}}: the item, appended at the end
    PUT  /courses/<c>/questions/<q>/rubric_items/<i>         JSON {rubric_item: {description, weight, group_id}}: the item changed
    DELETE /courses/<c>/questions/<q>/rubric_items/<i>
    POST /courses/<c>/assignment_containers                  form title + assignment_ids[]: an exam version set, lettered in order
    GET  /courses/<c>/assignment_containers/<x>/edit         Edit Exam Version Set: its form and gon.selected_assignment_ids /
                                                             gon.unversioned_assignments (adding a version, 2026-10-06)
    GET  /courses/<c>/assignments/<a>/review_grades          Review Grades with its Publish Grades form (publishing, 2026-10-02:
    GET  /courses/<c>/assignment_containers/<x>/scores/csv.zip  Download Grades of a bubble-sheet exam (`score_sheet` sets the scores)
    GET  /courses/<c>/assignment_containers/<x>/review_grades  the action bar's own form, or the modal's when `publish_modal`)
    POST /courses/<c>/assignments/<a>                        _method put + assignment[published]=true publishes it; ALWAYS 302
    POST /courses/<c>/assignment_containers/<x>              _method patch (or put) + assignment_container[published]=true
                                                             publishes the set and every version in it ("Publish All Grades");
                                                             multipart _method patch + assignment_ids (the Edit page's Save) sets
                                                             its versions, lettered in that order; ALWAYS 302
    POST /courses/<c>/assignments                            multipart assignment[bubble_sheet]=true and a title, no template:
                                                             a Bubble Sheet (2026-10-26, sandbox); its row's bubble_sheet is null
                                                             until it has versions, as on the real site
    GET  /courses/<c>/assignments/<a>/bubble_sheet_answer_key/edit         BubbleSheetAnswerKey: one key, "Version A"
    GET  /courses/<c>/assignment_containers/<x>/bubble_sheet_answer_key/edit   every version's key
    POST .../bubble_sheet_answer_key                         Add Version (JSON): the first turns the assignment into Version A of
                                                             a new set and adds Version B; then C, D, E (at most 5)
    PATCH .../bubble_sheet_answer_key                        Save (JSON bubble_sheet_assignment_answer_key): the key(s), kept as
                                                             the version's outline questions (`mc`), as the real outline shows them

Only what was seen on the real pages is served: the grading page (SubmissionGrader) says nothing about whether students see
grades, so a sender has to read that from the assignments list (`is_published`), as the viewer's `grades_visible` also shows.

Faults are scripted per request (`fail`): a dropped connection before or after the write is done, an HTTP status with or without
the write, or an expired session. Every request is recorded (`requests`), and anything that should never happen is recorded apart:
a request naming the course "Math 140 for Instructors" (`forbidden_hits`, answered 403), anything that publishes (`publish_hits`:
a path naming "publish", or a form setting `[published]`, which only the instructor's own Publish grades may send) and any grade
saved with a comment (`comment_hits`).
"""
import email.parser
import email.policy
import html
import io
import json
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMAIL, PASSWORD = "instructor@example.invalid", "fixture password, never real"
INSTRUCTOR = "Chris Instructor"  # the signed-in account's name: "Last Graded By" of whatever Faculty saves
SESSION = "_gradescope_session"


def react(name, props):
    return f'<div data-react-class="{name}" data-react-props="{html.escape(json.dumps(props), quote=True)}"></div>'


def page(title, csrf, body):
    head = f'<meta name="csrf-param" content="authenticity_token" />\n<meta name="csrf-token" content="{csrf}" />' if csrf else ""
    return (f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<title>{html.escape(title)} | Gradescope</title>\n{head}\n</head>\n'
            f'<body class="l-application">\n<main class="l-main">\n{body}\n</main>\n</body>\n</html>\n')


def pdf_pages(data):
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


class FakeGradescope:
    """Course state lives in plain dicts; the helpers below build it. IDs are integers, as Gradescope's props carry them."""

    def __init__(self, port=0):
        self.lock = threading.RLock()
        self.requests, self.forbidden_hits, self.publish_hits, self.comment_hits = [], [], [], []
        self.coordinator_reads = []  # the rubric import's reads of the coordinator's course (its only allowed requests there)
        self.sessions = {}  # cookie -> {"user": email or None, "csrf": ..., "form": ..., "login": ...}
        self.courses, self.members, self.assignments, self.questions = {}, {}, {}, {}
        self.items, self.groups, self.submissions, self.qsubs = {}, {}, {}, {}
        self.faults, self.processing_reads = [], 0
        self.template_reads = 0  # outline reads of a NEW assignment that still say its template is being processed
        self.deleted = []  # rubric items deleted (Faculty may delete only the default items of questions it made)
        self.renamed = []  # rubric items renamed: (id, new description) (MC LABELS, 2026-10-28)
        self.version_set_edits = []  # the Edit Exam Version Set saves Gradescope kept
        self.regrades = {}  # question submission -> [regrade request]: what the Regrade Requests page and the grading page show
        self.publish_modal = False  # Review Grades asks in a modal first (the instructor did not turn its confirmation off)
        self.mc_scores = {}  # (version assignment, user) -> {question number: points} as Gradescope scored a bubble sheet (`score_sheet`)
        self.bubble_sheets_offered = True  # Create Assignment offers the Bubble Sheet type (`featuresAccess.BUBBLE_SHEET`)
        self.key_saves = []  # the answer key saves Gradescope kept (THE BUBBLE-SHEET EXAM TOO)
        self.delay, self.inflight, self.max_inflight = 0.0, 0, 0  # seconds each request takes; how many were ever answered at once
        self._next = 1000
        self.email, self.password = EMAIL, PASSWORD
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def cookie(self):
                for part in (self.headers.get("Cookie") or "").split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == SESSION:
                        return value
                return None

            def send(self, code, body=b"", headers=None, content_type="text/html; charset=utf-8"):
                body = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                for key, value in {"Content-Type": content_type, "Content-Length": str(len(body)), **(headers or {})}.items():
                    if isinstance(value, list):
                        for item in value:
                            self.send_header(key, item)
                    else:
                        self.send_header(key, value)
                try:
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def handle_request(self, method):
                with fake.lock:
                    fake.inflight += 1
                    fake.max_inflight = max(fake.max_inflight, fake.inflight)
                try:
                    if fake.delay:
                        time.sleep(fake.delay)
                    return self.answer(method)
                finally:
                    with fake.lock:
                        fake.inflight -= 1

            def answer(self, method):
                parts = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                content = self.rfile.read(length) if length else b""
                record = {"method": method, "path": parts.path, "query": parts.query, "accept": self.headers.get("Accept", ""),
                          "csrf": self.headers.get("X-CSRF-Token"), "content_type": self.headers.get("Content-Type", ""), "size": len(content)}
                with fake.lock:
                    fake.requests.append(record)
                    fault = fake._fault(method, parts.path)
                # The rubric import may READ the coordinator's course (GRADESCOPE "THE COORDINATOR'S COURSE", 2026-10-25): its assignments
                # list, outlines and rubric pages, GETs only, kept apart in `coordinator_reads`. Anything else there is a forbidden hit.
                read = method == "GET" and re.fullmatch(r"/courses/\d+/(?:assignments(?:/\d+/(?:outline|rubric)/edit)?|assignment_containers/\d+/edit)", parts.path)
                if re.match(r"^/courses/(\d+)", parts.path) and fake._forbidden(int(re.match(r"^/courses/(\d+)", parts.path).group(1))) and read:
                    with fake.lock:
                        fake.coordinator_reads.append(record)
                elif re.match(r"^/courses/(\d+)", parts.path) and fake._forbidden(int(re.match(r"^/courses/(\d+)", parts.path).group(1))):
                    with fake.lock:
                        fake.forbidden_hits.append(record)
                    return self.send(403, page("Forbidden", None, "<h1>Faculty must never come here</h1>"))
                if "publish" in parts.path:
                    with fake.lock:
                        fake.publish_hits.append(record)
                if fault == "expire":
                    with fake.lock:
                        fake.sessions.clear()
                if fault == "drop_before" or (isinstance(fault, tuple) and fault[0] == "status"):
                    if fault == "drop_before":
                        self.close_connection = True
                        self.connection.close()
                        return None
                    return self.send(fault[1], page("Error", None, f"<h1>{fault[1]}</h1>"), fault[2] if len(fault) > 2 else None)
                try:
                    code, body, headers, kind = fake._route(method, parts.path, content, self, record)
                except Exception as error:  # a bug in the fake itself must be visible, not a silent 500
                    with fake.lock:
                        fake.requests.append({"method": "ERROR", "path": parts.path, "error": repr(error)})
                    raise
                if fault == "drop_after":
                    self.close_connection = True
                    self.connection.close()
                    return None
                if isinstance(fault, tuple) and fault[0] == "status_after":
                    code, body, headers, kind = fault[1], page("Error", None, f"<h1>{fault[1]}</h1>"), (fault[2] if len(fault) > 2 else {}), "text/html; charset=utf-8"
                return self.send(code, body, headers, kind)

            def do_GET(self):
                self.handle_request("GET")

            def do_POST(self):
                self.handle_request("POST")

            def do_PATCH(self):
                self.handle_request("PATCH")

            def do_DELETE(self):
                self.handle_request("DELETE")

            def do_PUT(self):
                self.handle_request("PUT")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)  # stops at once

    # --- running ----------------------------------------------------------------------------------------------------------
    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()

    # --- building the course state ------------------------------------------------------------------------------------------
    def _id(self):
        with self.lock:
            self._next += 1
            return self._next

    def add_course(self, short, name, term, *, instructor=True):
        course = self._id()
        self.courses[course] = {"id": course, "short": short, "name": name, "term": term, "instructor": instructor}
        self.members[course] = []
        return course

    def add_member(self, course, name, sid, email, role=0):
        user = self._id()
        first, _, last = name.partition(" ")
        self.members[course].append({"user_id": user, "membership": self._id(), "full_name": name, "first_name": first, "last_name": last,
                                     "sid": sid, "email": email, "role": role})
        return user

    def add_assignment(self, course, title, pages, *, published=False, bubble_sheet=False, parent=None, version=None):
        assignment = self._id()
        self.assignments[assignment] = {"id": assignment, "course": course, "title": title, "pages": pages, "published": published,
                                        "bubble_sheet": bubble_sheet, "parent": parent, "version": version, "container": False}
        return assignment

    def add_container(self, course, title, letters, pages, *, published=False, bubble_sheet=False):
        """An exam with versions: the container and one assignment per version letter. Returns (container, {letter: assignment}).
        A bubble-sheet exam also has the "?" version Gradescope files unread sheets under (not returned), and, as on the real site,
        none of its versions is in the assignments list: its own Manage Submissions page names them."""
        container = self._id()
        self.assignments[container] = {"id": container, "course": course, "title": title, "pages": pages, "published": published,
                                       "bubble_sheet": bubble_sheet, "parent": None, "version": None, "container": True}
        if bubble_sheet:
            self.add_assignment(course, f"{title} Version ?", pages, bubble_sheet=True, parent=container, version="?")
        return container, {letter: self.add_assignment(course, f"{title} Version {letter}", pages, bubble_sheet=bubble_sheet, parent=container,
                                                       version=letter) for letter in letters}

    def add_question(self, assignment, index, title, weight, entries, *, scoring_type="positive", floor=True, ceiling=True, mc=None):
        """`entries` in Gradescope's order: (points, description) for a top-level item, or (group description, [(points,
        description), ...]) for a group. Top-level items and groups share one position sequence; a group's items restart at 0,
        exactly as the AssignmentRubric props number them."""
        question = self._id()
        self.questions[question] = {"id": question, "assignment": assignment, "index": index, "title": title, "weight": weight,
                                    "scoring_type": scoring_type, "floor": floor, "ceiling": ceiling,
                                    "crops": [{"x1": 10.0, "x2": 90.0, "y1": 10.0, "y2": 40.0, "page_number": 1}],
                                    # A bubble-sheet question's key as the real outline holds it: (correct letters, scoring type).
                                    **({"mc": mc} if mc else {})}
        for position, entry in enumerate(entries):
            if isinstance(entry[1], list):
                group = self._id()
                self.groups[group] = {"id": group, "question": question, "position": position, "description": entry[0], "mutually_exclusive": False}
                for inner, (points, description) in enumerate(entry[1]):
                    self._item(question, inner, points, description, group)
            else:
                self._item(question, position, entry[0], entry[1], None)
        return question

    def _item(self, question, position, points, description, group):
        item = self._id()
        self.items[item] = {"id": item, "question": question, "position": position, "weight": points, "description": description, "group": group}
        return item

    def template_pdf(self, assignment):
        """The assignment's template: the PDF it was created with, else blank pages of its page count."""
        found = self.assignments[assignment]
        if found.get("template_pdf"):
            return found["template_pdf"]
        from pypdf import PdfWriter
        writer, output = PdfWriter(), io.BytesIO()
        for _ in range(found["pages"]):
            writer.add_blank_page(width=612, height=792)
        writer.write(output)
        return output.getvalue()

    def rubric_items(self, question):
        """The question's items in the order the evaluation export numbers them (top level by position, a group's inside it)."""
        top = [(i["position"], 0, [i]) for i in self.items.values() if i["question"] == question and not i["group"]]
        top += [(g["position"], 1, sorted((i for i in self.items.values() if i["group"] == g["id"]), key=lambda i: i["position"]))
                for g in self.groups.values() if g["question"] == question]
        return [item for _, _, found in sorted(top, key=lambda entry: entry[:2]) for item in found]

    def score_sheet(self, assignment, user, points):
        """How Gradescope scored the student's bubble sheet in this version: {question number: points}. Unscored sheets are Ungraded.
        Where the version has questions (`add_question`, as on the real site: one rubric item per answer and "Blank"), the sheet's
        question submissions are graded too, with the first item worth those points applied, so its grading page shows the score and a
        point adjustment there changes what Download Grades says (read on the real site 2026-10-22)."""
        self.mc_scores[(assignment, user)] = {int(number): float(value) for number, value in points.items()}
        submission = self.submission_of(assignment, user)
        for question in (q for q in self.questions.values() if q["assignment"] == assignment and submission):
            qsub = submission["questions"].get(question["id"])
            if qsub is None or question["index"] not in self.mc_scores[(assignment, user)]:
                continue
            wanted = self.mc_scores[(assignment, user)][question["index"]]
            item = next((i["id"] for i in self.rubric_items(question["id"]) if float(i["weight"]) == wanted), None)
            self.qsubs[qsub].update(evaluations={i["id"]: i["id"] == item for i in self.rubric_items(question["id"])}, points=None, comments="",
                                    graded=True, by="Gradescope")

    def _mc_points(self, version, user, scored):
        """{question number: points} of a scored sheet: its grading pages' scores where the version has questions, else as scored."""
        submission = self.submission_of(version, user)
        found = dict(scored)
        for question in (q for q in self.questions.values() if q["assignment"] == version and submission):
            qsub = self.qsubs.get(submission["questions"].get(question["id"]))
            if qsub and question["index"] in found:
                # As on the real site (Exam 2, 2026-10-28): Download Grades shows a question nobody graded as 0.
                found[question["index"]] = self.score(qsub) if qsub["graded"] else 0.0
        return found

    def _mc_finished(self, version, user):
        """Whether every question of the sheet is graded there: Download Grades says Ungraded while any is not."""
        submission = self.submission_of(version, user)
        return all(self.qsubs[qsub]["graded"] for qsub in (submission or {}).get("questions", {}).values())

    def _scores_zip(self, container):
        """Review Grades' Download Grades of a bubble-sheet exam, laid out as the real one read 2026-10-06: one CSV per version (the "?"
        version's named "..._Version__scores.csv"), every student of the course in each, and the version set's summary."""
        import csv, io, zipfile
        title = self.assignments[container]["title"].replace(" ", "_")
        course = self.assignments[container]["course"]
        students = [m for m in self.members[course] if m["role"] == 0]
        versions = [a for a in self.assignments.values() if a["parent"] == container]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for version in versions:
                numbers = sorted({n for (a, _), found in self.mc_scores.items() if a == version["id"] for n in found}) or list(range(1, 11))
                text = io.StringIO()
                writer = csv.writer(text)
                writer.writerow(["First Name", "Last Name", "SID", "Email", "Sections", "Total Score", "Max Points", "Status", "Submission ID",
                                 "Submission Time", "Lateness (H:M:S)", "View Count", "Submission Count", *[f"{n}: Question {n} (4.0 pts)" for n in numbers]])
                for member in students:
                    found = next((sub for sub in self.submissions.values() if sub["assignment"] == version["id"] and member["user_id"] in sub["owners"]), None)
                    if not found:
                        writer.writerow([member["first_name"], member["last_name"], member["sid"], member["email"], "", "", f"{4.0 * len(numbers):.1f}", "Missing"])
                        continue
                    scored = self.mc_scores.get((version["id"], member["user_id"]))
                    scored = self._mc_points(version["id"], member["user_id"], scored) if scored else scored
                    writer.writerow([member["first_name"], member["last_name"], member["sid"], member["email"], "", f"{sum(scored.values()):.1f}" if scored else "",
                                     f"{4.0 * len(numbers):.1f}", "Graded" if scored and self._mc_finished(version["id"], member["user_id"]) else "Ungraded", str(found["id"]), "2026-09-25 17:58:22 -0500", "00:00:00", "0", "1",
                                     *[f"{scored.get(n, 0.0):.1f}" if scored else "" for n in numbers]])
                letter = "" if version["version"] == "?" else version["version"]
                archive.writestr(f"{title}_Version_{letter}_scores.csv", text.getvalue())
            archive.writestr(f"{title}_Version_Set_Scores.csv", "First Name,Last Name,SID,Email,Sections,Total Score,Version,Status,Submission ID\n")
        return buffer.getvalue()

    def add_submission(self, assignment, owners, filename="scan.pdf", pages=None, *, uploader="another grader"):
        """A paper Faculty did not upload (a TA's, or the student's own)."""
        return self._submit(assignment, owners, filename, pages if pages is not None else self.assignments[assignment]["pages"], uploader, ready=0)

    def _submit(self, assignment, owners, filename, pages, uploader, ready):
        submission = self._id()
        self.submissions[submission] = {"id": submission, "assignment": assignment, "owners": list(owners), "filename": filename, "pages": pages,
                                        "uploader": uploader, "reads_left": ready, "questions": {}}
        if not ready:
            self._make_question_submissions(submission)
        return submission

    def _make_question_submissions(self, submission):
        found = self.submissions[submission]
        for question in (q for q in self.questions.values() if q["assignment"] == found["assignment"]):
            qsub = self._id()
            found["questions"][question["id"]] = qsub
            self.qsubs[qsub] = {"id": qsub, "question": question["id"], "submission": submission, "evaluations": None, "points": None,
                                "comments": None, "graded": False, "by": None}

    def grade(self, qsub, applied=(), points=None, comments=None, by="Pat Grader"):
        """Someone grading on Gradescope itself (never Faculty): a TA by default, named as the question list names its last grader."""
        found = self.qsubs[qsub]
        found.update(evaluations={i["id"]: i["id"] in applied for i in self.rubric_items(found["question"])}, points=points, comments=comments, graded=True, by=by)

    def add_regrade(self, qsub, student_comment, created_at="2026-09-29T15:00:00.000-05:00", staff_comment=None, closed=False,
                    completed_at=None):
        """A student's regrade request on a question submission (and, when given, the reply). NOT read on the real site: the page
        shapes are the ones communications/gradescope_capture.py documents."""
        request = {"id": self._id(), "created_at": created_at, "student_comment": student_comment, "staff_comment": staff_comment,
                   "closed": closed, "completed_at": completed_at}
        self.regrades.setdefault(qsub, []).append(request)
        return request

    def _regrade_page(self, course, assignment, csrf):
        rows = []
        for qsub, requests in self.regrades.items():
            found = self.qsubs[qsub]
            question = self.questions[found["question"]]
            if question["assignment"] != assignment:
                continue
            owners = self.submissions[found["submission"]]["owners"]
            student = next((m["full_name"] for m in self.members[course] if m["user_id"] in owners), "")
            done = next((r["completed_at"] for r in reversed(requests) if r["closed"] and r["completed_at"]), None)
            rows.append(f"<tr><td>{html.escape(student)}</td><td>Section 507</td><td>{html.escape(str(question['index']))}: {html.escape(question['title'])}</td>"
                        f"<td>Pat Grader</td><td>{html.escape(done or '—')}</td>"
                        f"<td><a class=\"link-gray\" href=\"/courses/{course}/questions/{question['id']}/submissions/{qsub}/grade\">Review</a></td></tr>")
        if not rows:
            return page("Regrade Requests", csrf, "<p>There are no regrade requests yet.</p>")
        table = ("<table class=\"table regradeRequestsTable\"><thead><tr><th>Student</th><th>Sections</th><th>Question</th><th>Grader</th>"
                 f"<th>Completed</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>")
        return page("Regrade Requests", csrf, table)

    def submission_of(self, assignment, user):
        return next((s for s in self.submissions.values() if s["assignment"] == assignment and user in s["owners"]), None)

    def qsub_of(self, assignment, user, question):
        submission = self.submission_of(assignment, user)
        return self.qsubs[submission["questions"][question]] if submission and question in submission["questions"] else None

    def state(self, qsub):
        """(sorted applied item ids, points, comments, score) as the grading page shows them."""
        found = self.qsubs[qsub["id"] if isinstance(qsub, dict) else qsub]
        applied = sorted(i for i, on in (found["evaluations"] or {}).items() if on)
        return applied, found["points"], found["comments"], self.score(found)

    def score(self, qsub):
        if not qsub["graded"]:
            return None
        question = self.questions[qsub["question"]]
        total = sum(self.items[i]["weight"] for i, on in (qsub["evaluations"] or {}).items() if on and i in self.items)
        value = (question["weight"] - total if question["scoring_type"] == "negative" else total) + float(qsub["points"] or 0)
        if question["floor"]:
            value = max(value, 0.0)
        if question["ceiling"]:
            value = min(value, float(question["weight"]))
        return float(value)

    def uploads(self):
        return [r for r in self.requests if r["method"] == "POST" and r["path"].endswith("/submissions")]

    def saves(self):
        return [r for r in self.requests if r["method"] == "POST" and r["path"].endswith("/save_grade")]

    def fail(self, method, pattern, action, times=1, when=None):
        """The next `times` requests of `method` whose path matches `pattern` (and, with `when`, only while `when()` is true) meet
        `action`: "drop_before" (the connection closes and nothing is done), "drop_after" (the write is done, then the connection
        closes unanswered), ("status", code) (that answer and nothing done), ("status_after", code) (done, then that answer), either
        with a third element of response headers (a Retry-After), or
        "expire" (every session ends first, so the request meets the sign-in redirect)."""
        with self.lock:
            self.faults.append([method, re.compile(pattern), action, times, when])

    def after_write(self, kind="upload"):
        """A `when` for the reads that check a write: true once Faculty has sent an upload (or a grade)."""
        return lambda: bool(self.uploads() if kind == "upload" else self.saves())

    def _fault(self, method, path):
        for fault in self.faults:
            if fault[3] > 0 and fault[0] == method and fault[1].search(path) and (fault[4] is None or fault[4]()):
                fault[3] -= 1
                return fault[2]
        return None

    def expire_sessions(self):
        with self.lock:
            self.sessions.clear()

    def _forbidden(self, course):
        found = self.courses.get(course)
        return bool(found) and any(" ".join(value.split()).casefold() == "math 140 for instructors" for value in (found["short"], found["name"]))

    # --- routing ----------------------------------------------------------------------------------------------------------
    def _route(self, method, path, content, handler, record):
        token = handler.cookie()
        with self.lock:
            session = self.sessions.get(token)
            if path == "/" and method == "GET":
                return self._home(session, token)
            if path == "/login" and method == "POST":
                return self._login(session, content, handler)
            if path == "/logout" and method == "GET":
                self.sessions.pop(token, None)
                return 302, "", {"Location": self.url + "/", "Set-Cookie": f"{SESSION}=; path=/; max-age=0"}, "text/html; charset=utf-8"
            # The template's signed link in Gradescope's uploads store: no sign-in, as S3 answers it.
            match = re.fullmatch(r"/uploads/pdf_attachment/file/(\d+)/[^/]+\.pdf", path)
            if match and method == "GET":
                found = next((a for a in self.assignments.values() if a["id"] + 7 == int(match.group(1))), None)
                if not found:
                    return 404, "", {}, "text/plain"
                return 200, self.template_pdf(found["id"]), {}, "application/pdf"
            if not session or not session.get("user"):
                return 302, '<html><body>You are being <a href="/login">redirected</a>.</body></html>', {"Location": self.url + "/login"}, "text/html; charset=utf-8"
            csrf = session["csrf"]
            if path == "/account" and method == "GET":
                return 200, page("Your Courses", csrf, self._account()), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/memberships", path)
            if match and method == "GET":
                return self._need(int(match.group(1))) or (200, page("Roster", csrf, self._roster(int(match.group(1)))), {}, "text/html; charset=utf-8")
            match = re.fullmatch(r"/courses/(\d+)/assignments", path)
            if match and method == "GET":
                course = int(match.group(1))
                return self._need(course) or (200, page("Assignments", csrf, react("AssignmentsTable", self._table(course))), {}, "text/html; charset=utf-8")
            if match and method == "POST":
                return self._need(int(match.group(1))) or self._create(session, int(match.group(1)), content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/assignments/new", path)
            if match and method == "GET":
                course = int(match.group(1))
                props = {"urls": {"createAssignment": f"/courses/{course}/assignments", "newAssignment": path}, "rubricLockingSetting": "all_edit",
                         "featuresAccess": {"EXAM": True, "HOMEWORK": True, "BUBBLE_SHEET": self.bubble_sheets_offered}}
                return self._need(course) or (200, page("Create Assignment", csrf, react("CreateAssignment", props)), {}, "text/html; charset=utf-8")
            match = re.fullmatch(r"/courses/(\d+)/(assignments|assignment_containers)/(\d+)/bubble_sheet_answer_key(/edit)?", path)
            if match:
                course, target = int(match.group(1)), int(match.group(3))
                found = self.assignments.get(target)
                missing = self._need(course, target)
                container = match.group(2) == "assignment_containers"
                if missing or not found["bubble_sheet"] or found["container"] != container or (found["parent"] and match.group(4)):
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                if match.group(4) and method == "GET":
                    return 200, page("Edit Answer Key", csrf, react("BubbleSheetAnswerKey", self._answer_keys(course, target))), {}, "text/html; charset=utf-8"
                if not match.group(4) and method in ("POST", "PATCH"):
                    return self._key_write(session, method, course, target, content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)/outline/", path)
            if match and method == "PATCH":
                course, assignment = int(match.group(1)), int(match.group(2))
                return self._need(course, assignment) or self._save_outline(session, course, assignment, content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/questions/(\d+)/(save_grading_options|rubric_items)(?:/(\d+))?", path)
            if match and method in ("POST", "DELETE", "PUT"):
                course, question = int(match.group(1)), int(match.group(2))
                found = self.questions.get(question)
                if not found or self.assignments[found["assignment"]]["course"] != course or self._need(course):
                    return 404, json.dumps({"error": "Not Found"}), {}, "application/json"
                return self._rubric_write(session, method, found, match.group(3), match.group(4), content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/(assignments|assignment_containers)/(\d+)/review_grades", path)
            if match and method == "GET":
                course, target = int(match.group(1)), int(match.group(3))
                missing = self._need(course, target)
                if missing or self.assignments[target]["container"] != (match.group(2) == "assignment_containers"):
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                return 200, self._review_grades(course, match.group(2), target, csrf), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/(assignments|assignment_containers)/(\d+)", path)
            if match and method == "POST":
                course, target = int(match.group(1)), int(match.group(3))
                missing = self._need(course, target)
                if missing or self.assignments[target]["container"] != (match.group(2) == "assignment_containers"):
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                if match.group(2) == "assignment_containers" and (handler.headers.get("Content-Type") or "").startswith("multipart/"):
                    return self._edit_container(session, course, target, content, handler, record)
                return self._publish(session, course, match.group(2), target, content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/assignment_containers/(\d+)/edit", path)
            if match and method == "GET":
                course, container = int(match.group(1)), int(match.group(2))
                missing = self._need(course, container)
                if missing or not self.assignments[container]["container"]:
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                return 200, self._container_edit(course, container, csrf), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/assignment_containers", path)
            if match and method == "POST":
                return self._need(int(match.group(1))) or self._link(session, int(match.group(1)), content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)/(outline|rubric)/edit", path)
            if match and method == "GET":
                course, assignment = int(match.group(1)), int(match.group(2))
                missing = self._need(course, assignment)
                if missing:
                    return missing
                name, props = ("AssignmentOutline", self._outline(assignment)) if match.group(3) == "outline" else ("AssignmentRubric", self._rubric(assignment))
                return 200, page(name, csrf, react(name, props)), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/assignment_containers/(\d+)/scores/csv\.zip", path)
            if match and method == "GET":
                course, container = int(match.group(1)), int(match.group(2))
                missing = self._need(course, container)
                if missing or not self.assignments[container]["container"]:
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                name = self.assignments[container]["title"].replace(" ", "_")
                return 200, self._scores_zip(container), {"Content-Disposition": f'attachment; filename="{name}_Version_Set_Scores.zip"'}, "application/zip"
            match = re.fullmatch(r"/courses/(\d+)/assignment_containers/(\d+)/submissions", path)
            if match and method == "GET":
                course, container = int(match.group(1)), int(match.group(2))
                missing = self._need(course, container)
                if missing or not self.assignments[container]["container"]:
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                return 200, page("Manage Submissions", csrf, react("SubmissionsManager", self._container_manager(container))), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)/submissions", path)
            if match:
                course, assignment = int(match.group(1)), int(match.group(2))
                missing = self._need(course, assignment)
                if missing:
                    return missing
                if method == "GET":
                    return 200, page("Manage Submissions", csrf, react("SubmissionsManager", self._manager(assignment)) + self._upload_form(session, path)), {}, "text/html; charset=utf-8"
                return self._upload(session, course, assignment, path, content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/questions/(\d+)/submissions", path)
            if match and method == "GET":
                course, question = int(match.group(1)), self.questions.get(int(match.group(2)))
                missing = self._need(course, question["assignment"] if question else None)
                if missing or not question:
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                return 200, self._question_list(course, question, csrf), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)/regrade_requests", path)
            if match and method == "GET":
                course, assignment = int(match.group(1)), int(match.group(2))
                return self._need(course, assignment) or (200, self._regrade_page(course, assignment, csrf), {}, "text/html; charset=utf-8")
            match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)/submissions/(\d+)", path)
            if match and method == "GET":
                course, assignment, submission = (int(g) for g in match.groups())
                missing = self._need(course, assignment)
                if missing or submission not in self.submissions or self.submissions[submission]["assignment"] != assignment:
                    return missing or (404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8")
                return 200, page("Submission", csrf, react("AssignmentSubmissionViewer", self._viewer(course, submission))), {}, "text/html; charset=utf-8"
            match = re.fullmatch(r"/courses/(\d+)/assignments/(\d+)/submissions/(\d+)/regrade_requests/(\d+)", path)
            if match and method == "PUT":
                return self._reply(session, *(int(g) for g in match.groups()), content, handler, record)
            match = re.fullmatch(r"/courses/(\d+)/questions/(\d+)/submissions/(\d+)/(grade|save_grade)", path)
            if match:
                course, question, qsub = (int(g) for g in match.groups()[:3])
                found = self.qsubs.get(qsub)
                if not found or found["question"] != question or self.assignments[self.questions[question]["assignment"]]["course"] != course:
                    return 404, page("Not found", csrf, "<h1>404</h1>"), {}, "text/html; charset=utf-8"
                if match.group(4) == "grade" and method == "GET":
                    return 200, page("Grade Submissions", csrf, react("SubmissionGrader", self._grader(course, found))), {}, "text/html; charset=utf-8"
                if match.group(4) == "save_grade" and method == "POST":
                    return self._save(session, found, content, handler, record)
            return 404, page("Not found", csrf, "<h1>Page not found</h1>"), {}, "text/html; charset=utf-8"

    def _need(self, course, assignment=None):
        found = self.courses.get(course)
        if not found or not found["instructor"] or (assignment is not None and self.assignments.get(assignment, {}).get("course") != course):
            return 401, page("Unauthorized", None, "<h1>You are not authorized to access this page.</h1>"), {}, "text/html; charset=utf-8"
        return None

    def _home(self, session, token):
        if session and session.get("user"):  # already signed in: the site sends you to your courses
            return 302, "", {"Location": self.url + "/account"}, "text/html; charset=utf-8"
        if not session:
            token = secrets.token_hex(16)
            session = self.sessions[token] = {"user": None, "csrf": secrets.token_urlsafe(24), "form": secrets.token_urlsafe(24), "login": secrets.token_urlsafe(24)}
        body = (f'<div class="loginForm"><form class="loginForm" id="login-form" action="/login" accept-charset="UTF-8" method="post">'
                f'<input name="utf8" type="hidden" value="&#x2713;" autocomplete="off" />'
                f'<input type="hidden" name="authenticity_token" value="{session["login"]}" autocomplete="off" />'
                f'<input type="email" name="session[email]" id="session_email" /><input type="password" name="session[password]" id="session_password" />'
                f'<input name="session[remember_me]" type="hidden" value="0" /><input type="submit" name="commit" value="Log In" /></form></div>')
        return 200, page("Gradescope", session["csrf"], body), {"Set-Cookie": f"{SESSION}={token}; path=/; HttpOnly"}, "text/html; charset=utf-8"

    def _login(self, session, content, handler):
        form = {k: v[0] for k, v in urllib.parse.parse_qs(content.decode(), keep_blank_values=True).items()}
        if session and form.get("authenticity_token") == session["login"] and form.get("session[email]") == self.email and form.get("session[password]") == self.password:
            token = secrets.token_hex(16)
            self.sessions.pop(handler.cookie(), None)
            self.sessions[token] = {"user": self.email, "csrf": secrets.token_urlsafe(24), "form": secrets.token_urlsafe(24), "login": ""}
            return 302, "", {"Location": self.url + "/account", "Set-Cookie": f"{SESSION}={token}; path=/; HttpOnly"}, "text/html; charset=utf-8"
        return 302, "", {"Location": self.url + "/login"}, "text/html; charset=utf-8"

    def _account(self):
        def boxes(instructor):
            terms = {}
            for course in self.courses.values():
                if course["instructor"] == instructor:
                    terms.setdefault(course["term"], []).append(course)
            out = []
            for term, courses in terms.items():
                out.append(f'<div class="courseList--term pageSubheading">{html.escape(term)}</div><div class="courseList--coursesForTerm">')
                for course in courses:
                    out.append(f'<a class="courseBox" href="/courses/{course["id"]}"><h3 class="courseBox--shortname">{html.escape(course["short"])}</h3>'
                               f'<div class="courseBox--name">{html.escape(course["name"])}</div><div class="courseBox--assignments">3 assignments</div></a>')
                if instructor:
                    out.append('<button class="courseBox courseBox-new js-createNewCourse" type="button"><span>Create a new course</span></button>')
                out.append("</div>")
            return "".join(out)
        return (f'<div id="account-show"><h1 class="pageHeading">Your Courses</h1><h2 class="pageHeading">Instructor Courses</h2>'
                f'<div class="courseList">{boxes(True)}</div><h2 class="pageHeading">Student Courses</h2><div class="courseList">{boxes(False)}</div></div>')

    def _roster(self, course):
        rows = []
        for m in self.members[course]:
            cm = html.escape(json.dumps({"full_name": m["full_name"], "first_name": m["first_name"], "last_name": m["last_name"], "sid": m["sid"]}), quote=True)
            rows.append(f'<tr class="rosterRow"><td class="rosterCell--primary"><button name="button" type="button" class="js-rosterName" '
                        f'data-url="/courses/{course}/gradebook.json?user_id={m["user_id"]}" data-name="{html.escape(m["full_name"])}">{html.escape(m["full_name"])}</button>'
                        f'<button class="rosterCell--editIcon js-editMember" type="button" data-cm="{cm}" data-email="{html.escape(m["email"])}" '
                        f'data-role="{m["role"]}" data-sections="[]" data-id="{m["membership"]}"><i class="fa fa-pencil"></i></button></td>'
                        f'<td>{html.escape(m["email"])}</td><td>{["Student", "Instructor", "TA", "Reader"][m["role"]]}</td><td>0</td></tr>')
        return f'<table class="js-rosterTable"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Submissions</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'

    def _table(self, course):
        rows = []
        for a in (a for a in self.assignments.values() if a["course"] == course and not (a["parent"] and self.assignments[a["parent"]]["bubble_sheet"])):
            weight = sum(q["weight"] for q in self.questions.values() if q["assignment"] == a["id"])
            row = {"id": f"assignment_container_{a['id']}" if a["container"] else f"assignment_{a['id']}", "type": "assignment_container" if a["container"] else "assignment",
                   "title": a["title"], "total_points": f"{float(weight):.1f}", "parent_id": f"assignment_container_{a['parent']}" if a["parent"] else None,
                   "is_published": a["published"], "is_versioned_assignment": bool(a["container"] or a["parent"]),
                   "num_active_submissions": sum(1 for s in self.submissions.values() if s["assignment"] == a["id"] or self.assignments[s["assignment"]]["parent"] == a["id"]),
                   "grading_progress": a.get("graded", 100.0), "bubble_sheet": a["bubble_sheet"] if not a.get("fresh_bubble") else None}
            if a["parent"]:
                row.update(version_name=f"Version {a['version']}", version_index=ord(a["version"]) - 64)
            if a["bubble_sheet"] and not a["parent"]:
                row["url"] = f"/courses/{course}/assignment_containers/{a['id']}" if a["container"] else f"/courses/{course}/assignments/{a['id']}"
            rows.append(row)
        return {"table_data": rows, "course_id": course}

    def _outline(self, assignment):
        found = self.assignments[assignment]
        status = "complete"
        if found.get("processing"):
            found["processing"] -= 1
            status = "processing"
        return {"assignment": {"id": assignment, "title": found["title"], "type": "PDFAssignment", "variable_length?": False},
                "template": {"id": assignment + 7, "page_count": found["pages"], "status_string": status,
                             "url": f"{self.url}/uploads/pdf_attachment/file/{assignment + 7}/template.pdf?X-Amz-Signature=fake",
                             "filename": "template.pdf"},
                "outline": [{"id": q["id"], "type": "FreeResponseQuestion", "title": q["title"], "parent_id": None, "weight": f"{float(q['weight']):.1f}",
                             "crop_rect_list": q["crops"], "index": q["index"],
                             **({"data": {"mc_autograder_correct_answer_letters": list(q["mc"][0]), "mc_autograder_scoring_type": q["mc"][1]},
                                 "assisted_grading_type": "mc"} if q.get("mc") else {})}
                            for q in sorted((q for q in self.questions.values() if q["assignment"] == assignment), key=lambda q: q["index"])]}

    def _rubric(self, assignment):
        found = self.assignments[assignment]
        questions = sorted((q for q in self.questions.values() if q["assignment"] == assignment), key=lambda q: q["index"])
        ids = {q["id"] for q in questions}
        return {"assignment": {"id": assignment, "title": found["title"], "course_id": found["course"], "type": "PDFAssignment", "variable_length": False},
                "course": {"id": found["course"], "name": self.courses[found["course"]]["name"], "shortname": self.courses[found["course"]]["short"]},
                "has_submissions_and_students": False,
                "questions": [{"id": q["id"], "parent_id": None, "index": q["index"], "type": "FreeResponseQuestion", "title": q["title"],
                               "weight": f"{float(q['weight']):.1f}", "scoring_type": q["scoring_type"], "floor": q["floor"], "ceiling": q["ceiling"],
                               "content": [], "children": None, "full_index": str(q["index"])} for q in questions],
                "rubric_items": [{"id": i["id"], "description": i["description"], "weight": f"{float(i['weight']):.1f}", "position": i["position"],
                                  "group_id": i["group"], "question_id": i["question"], "locked": False} for i in self.items.values() if i["question"] in ids],
                "rubric_item_groups": [{"id": g["id"], "position": g["position"], "description": g["description"], "mutually_exclusive": g["mutually_exclusive"],
                                        "question_id": g["question"]} for g in self.groups.values() if g["question"] in ids],
                "template": {"page_count": found["pages"], "status_string": "complete"}, "rubrics_locked": False,
                # As the real page names them (2026-09-27): "rubric_item" is the plural rubric_items path.
                "urls": {"question_urls": {str(q["id"]): {name: f"/courses/{found['course']}/questions/{q['id']}/{path}" for name, path in
                                                          (("rubric_item", "rubric_items"), ("save_grading_options", "save_grading_options"),
                                                           ("rubric_item_groups", "rubric_item_groups"))} for q in questions}}}

    def _versions(self, assignment):
        """The assignments sharing this one's container (itself alone when it has none)."""
        parent = self.assignments[assignment]["parent"]
        return [a["id"] for a in self.assignments.values() if parent and a["parent"] == parent] or [assignment]

    def _manager(self, assignment):
        siblings = self._versions(assignment)
        found = self.assignments[assignment]
        students = []
        for m in (m for m in self.members[found["course"]] if m["role"] == 0):
            held = next((s for s in self.submissions.values() if s["assignment"] in siblings and m["user_id"] in s["owners"]), None)
            students.append({"id": m["user_id"], "name": m["full_name"], "email": m["email"], "sid": m["sid"], "submission": bool(held),
                             "version_name": self.assignments[held["assignment"]]["version"] if held and self.assignments[held["assignment"]]["version"] else None})
        return {"students": students,
                "submissions": {str(s["id"]): {"id": s["id"], "assignment_id": s["assignment"], "created_at": "2026-09-25T09:00:00-05:00", "active_user_ids": list(s["owners"])}
                                for s in self.submissions.values() if s["assignment"] == assignment},
                "versionNameByAssignmentId": {str(a): self.assignments[a]["version"] for a in siblings if self.assignments[a]["version"]},
                "submissionAllowed": True, "urls": {"submission": f"/courses/{found['course']}/assignments/{assignment}/submissions"}}

    def _container_manager(self, container):
        """An exam's own Manage Submissions page, as a bubble-sheet exam's: its versions, the students and every version's submissions."""
        versions = [a for a in self.assignments.values() if a["parent"] == container]
        found = self._manager(versions[0]["id"]) if versions else {"students": [], "submissions": {}}
        found["submissions"] = {str(s["id"]): {"id": s["id"], "assignment_id": s["assignment"], "created_at": "2026-09-25T09:00:00-05:00", "active_user_ids": list(s["owners"])}
                                for s in self.submissions.values() if s["assignment"] in {a["id"] for a in versions}}
        found.update(isAssignmentContainer=True, assignmentId=container,
                     assignmentContainerVersions={str(a["id"]): {"assignmentId": a["id"], "versionName": a["version"], "id": a["id"] + 1000,
                                                                 "versionIndex": -1 if a["version"] == "?" else ord(a["version"]) - 65} for a in versions},
                     versionNameByAssignmentId={str(a["id"]): a["version"] for a in versions})
        return found

    @staticmethod
    def _upload_form(session, path):
        return (f'<form id="submissions-manager-upload-form" enctype="multipart/form-data" action="{path}" accept-charset="UTF-8" method="post">'
                f'<input name="utf8" type="hidden" value="&#x2713;" autocomplete="off" />'
                f'<input type="hidden" name="authenticity_token" value="{session["form"]}" autocomplete="off" />'
                f'<select name="owner_id"></select><input type="file" name="pdf_attachment" accept="application/pdf" /></form>')

    def _upload(self, session, course, assignment, path, content, handler, record):
        parsed = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + (handler.headers.get("Content-Type") or "").encode() + b"\r\n\r\n" + content)
        form, files = {}, []
        for part in parsed.iter_parts() if parsed.is_multipart() else []:
            name, filename = part.get_param("name", header="content-disposition"), part.get_filename()
            if filename is None:
                form[name] = part.get_content()
            else:
                files.append((name, filename, part.get_payload(decode=True)))
        record.update(form=dict(form), files=[(n, f, len(d)) for n, f, d in files])
        back = {"Location": self.url + path}
        if form.get("authenticity_token") != session["form"] or handler.headers.get("X-CSRF-Token") != session["csrf"]:
            return 422, page("The change you wanted was rejected", None, "<h1>422</h1>"), {}, "text/html; charset=utf-8"
        owner = int(form.get("owner_id") or 0)
        student = next((m for m in self.members[course] if m["user_id"] == owner and m["role"] == 0), None)
        upload = next(((f, d) for n, f, d in files if n == "pdf_attachment"), None)
        if student and upload and not self.submission_of(assignment, owner):
            self._submit(assignment, [owner], upload[0], pdf_pages(upload[1]), self.email, ready=self.processing_reads)
        return 302, f'<html><body>You are being <a href="{self.url + path}">redirected</a>.</body></html>', back, "text/html; charset=utf-8"

    def _question_list(self, course, question, csrf):
        """A question's Grade Submissions list, in the shape read on the real site 2026-10-01: table `question_submissions` with
        User (the grading link), Last Graded By, Sections, Score and Graded? (a check icon); an ungraded row has those cells empty."""
        rows, check = [], '<i class="fa fa-check"></i>'
        for submission in self.submissions.values():
            qsub = submission["questions"].get(question["id"])
            if submission["assignment"] != question["assignment"] or not qsub:
                continue
            found = self.qsubs[qsub]
            member = next((m for m in self.members[course] if m["user_id"] in submission["owners"]), None)
            who = f"{member['full_name']} ({member['email']})" if member else "Unknown"
            link, score = f"/courses/{course}/questions/{question['id']}/submissions/{qsub}/grade", self.score(found)
            rows.append(f'<tr><td>{len(rows) + 1}</td><td class="table--primaryLink"><a href="{link}">{html.escape(who)}</a></td>'
                        f'<td>{html.escape(found["by"] or "") if found["graded"] else ""}</td>'
                        f'<td class="table--secondaryLink table--secondaryLink-small"><div class="sectionsColumnCell"><div class="sectionsColumnCell--section">'
                        f'<span class="sectionsColumnCell--sectionSpan" title="math-140-501"><a href="{link}?section_ids=1278349">math-140-501</a></span></div></div></td>'
                        f'<td>{"" if score is None else f"{score:.1f}"}</td><td>{check if found["graded"] else ""}</td></tr>')
        table = ('<table class="table" id="question_submissions"><thead><tr><th></th><th>User</th><th>Last Graded By</th><th>Sections</th><th>Score</th>'
                 f'<th>Graded?</th></tr></thead>{"".join(rows)}</table>')
        return page("Grade Submissions", csrf, table)

    def _viewer(self, course, submission):
        found = self.submissions[submission]
        if found["reads_left"]:
            found["reads_left"] -= 1
            if not found["reads_left"]:
                self._make_question_submissions(submission)
        assignment = self.assignments[found["assignment"]]
        ready = bool(found["questions"])
        return {"assignment": {"id": assignment["id"], "title": assignment["title"], "submission_format": "fixed_length"},
                "assignment_submission": {"id": submission, "status": "processed" if ready else "processing", "active": True},
                "container": {"title": self.assignments[assignment["parent"]]["title"]} if assignment["parent"] else None,
                "grades_visible": assignment["published"] or bool(assignment["parent"] and self.assignments[assignment["parent"]]["published"]),
                "ownerships": [{"id": submission * 10 + i, "user_id": user, "active": True, "uploader": False} for i, user in enumerate(found["owners"])],
                "pdf_attachment": {"id": submission + 3, "attachable_id": submission, "attachable_type": "AssignmentSubmission", "page_count": found["pages"],
                                   "status": 2 if ready else 1, "status_string": "complete" if ready else "processing", "filename": found["filename"]},
                "question_submissions": [{"question_id": q, "data": {}, "active": True, "answers": {}, "id": qs, "evaluations": [],
                                          "grade_path": f"/courses/{course}/questions/{q}/submissions/{qs}/grade", "annotations": []} for q, qs in found["questions"].items()],
                "paths": {"course_path": f"/courses/{course}"}}

    def _grader(self, course, found):
        question = self.questions[found["question"]]
        items = self.rubric_items(question["id"])
        score = self.score(found)
        return {"urls": {"save_grade": f"/courses/{course}/questions/{question['id']}/submissions/{found['id']}/save_grade",
                         # Read on the real site 2026-09-30: the reply goes to this address plus the request's ID (a JSON PUT).
                         "regrade_request": f"/courses/{course}/assignments/{question['assignment']}/submissions/{found['submission']}/regrade_requests"},
                "question": {"id": question["id"], "index": question["index"], "title": question["title"], "weight": f"{float(question['weight']):.1f}",
                             "scoring_type": question["scoring_type"], "floor": question["floor"], "ceiling": question["ceiling"]},
                "rubric_items": [{"id": i["id"], "description": i["description"], "weight": f"{float(i['weight']):.1f}", "position": i["position"], "group_id": i["group"]}
                                 for i in items],
                "rubric_item_groups": [{"id": g["id"], "description": g["description"], "position": g["position"], "mutually_exclusive": g["mutually_exclusive"]}
                                       for g in self.groups.values() if g["question"] == question["id"]],
                "rubric_item_evaluations": [{"rubric_item_id": i, "present": on} for i, on in (found["evaluations"] or {}).items()],
                # As on the real site (read 2026-09-25): an evaluation is always there; an ungraded question's has id and user_id null.
                "evaluation": {"id": (7000000 + int(found["id"])) if found["graded"] else None, "user_id": 1 if found["graded"] else None,
                               "points": None if found["points"] is None else f"{float(found['points']):.1f}", "comments": found["comments"],
                               "autograded": False, "question_submission_id": found["id"]},
                "submission": {"id": found["id"], "score": score},
                "open_request": next(({k: r[k] for k in ("id", "created_at", "student_comment", "staff_comment")}
                                      for r in self.regrades.get(found["id"], []) if not r["closed"]), None),
                "closed_requests": [{k: r[k] for k in ("id", "created_at", "student_comment", "staff_comment", "completed_at")}
                                    for r in self.regrades.get(found["id"], []) if r["closed"]],
                "pages": [{"number": n, "url": f"/fake/page_{n}.jpg"} for n in range(1, self.submissions[found["submission"]]["pages"] + 1)]}

    def _reply(self, session, course, assignment, submission, request_id, content, handler, record):
        """The grading page's Close & Respond: `{"regrade_request": {"id", "staff_comment"}}` closes the request with the reply (the
        grading bundle read 2026-09-30); editing a closed request's reply is the same call."""
        if handler.headers.get("X-CSRF-Token") != session["csrf"]:
            return 422, page("The change you wanted was rejected", None, "<h1>422</h1>"), {}, "text/html; charset=utf-8"
        found = next((r for qsub, requests in self.regrades.items() for r in requests if r["id"] == request_id
                      and self.qsubs[qsub]["submission"] == submission and self.assignments[self.submissions[submission]["assignment"]]["course"] == course), None)
        if not found or self.submissions[submission]["assignment"] != assignment:
            return 404, json.dumps({"error": "not found"}), {}, "application/json"
        body = json.loads(content or b"{}")
        record["json"] = body
        reply = (body.get("regrade_request") or {}).get("staff_comment")
        if not isinstance(reply, str) or not reply.strip():
            return 422, json.dumps({"error": "comment required"}), {}, "application/json"
        found.update(staff_comment=reply, closed=True, completed_at=found.get("completed_at") or "2026-09-30T12:00:00.000-05:00")
        return 200, json.dumps({"id": found["id"], "staff_comment": reply, "completed": True}), {}, "application/json"

    def _save(self, session, found, content, handler, record):
        if handler.headers.get("X-CSRF-Token") != session["csrf"]:
            return 422, page("The change you wanted was rejected", None, "<h1>422</h1>"), {}, "text/html; charset=utf-8"
        try:
            body = json.loads(content)
        except ValueError:
            return 400, page("Bad request", None, "<h1>400</h1>"), {}, "text/html; charset=utf-8"
        record["json"] = body
        evaluation = body.get("question_submission_evaluation") or {}
        if evaluation.get("comments") not in (None, ""):
            self.comment_hits.append(record)
        known = {i["id"] for i in self.rubric_items(found["question"])}
        chosen = {int(k): v.get("score") == "true" for k, v in (body.get("rubric_items") or {}).items()}
        if set(chosen) - known:
            return 422, json.dumps({"error": "Unknown rubric item"}), {}, "application/json"
        found.update(evaluations={i: chosen.get(i, False) for i in known}, points=evaluation.get("points"), comments=evaluation.get("comments"), graded=True,
                     by=INSTRUCTOR)
        if "application/json" in (handler.headers.get("Accept") or ""):
            return 200, json.dumps({"score": self.score(found), "rubric_items": sorted(i for i, on in found["evaluations"].items() if on)}), {}, "application/json"
        return 404, page("The page you were looking for doesn't exist", None, "<h1>404</h1>"), {}, "text/html; charset=utf-8"

    # --- creating an assignment (2026-09-27): the requests Gradescope's own pages send, as the sandbox answered them -------------
    def _form(self, content, handler):
        kind = handler.headers.get("Content-Type") or ""
        if kind.startswith("multipart/"):
            parsed = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(b"Content-Type: " + kind.encode() + b"\r\n\r\n" + content)
            form, files = {}, []
            for part in parsed.iter_parts() if parsed.is_multipart() else []:
                name, filename = part.get_param("name", header="content-disposition"), part.get_filename()
                if filename is None:
                    form[name] = part.get_content()
                else:
                    files.append((name, filename, part.get_payload(decode=True)))
            return form, files
        return urllib.parse.parse_qs(content.decode(), keep_blank_values=True), []

    def _create(self, session, course, content, handler, record):
        form, files = self._form(content, handler)
        record.update(form=dict(form), files=[(n, f, len(d)) for n, f, d in files])
        template = next((d for n, _, d in files if n == "template_pdf"), None)
        if form.get("authenticity_token") != session["csrf"]:
            return 422, page("The change you wanted was rejected", None, "<h1>422</h1>"), {}, "text/html; charset=utf-8"
        if form.get("assignment[bubble_sheet]") == "true" and form.get("assignment[title]") and template is None \
                and form.get("assignment[student_submission]") == "false":
            # A Bubble Sheet: Gradescope's own two-page sheet is its template. Its row's bubble_sheet is null until it has versions.
            assignment = self.add_assignment(course, form["assignment[title]"], 2, bubble_sheet=True)
            self.assignments[assignment].update(created_by=self.email, fresh_bubble=True)
            return 302, "", {"Location": self.url + f"/courses/{course}/assignments/{assignment}/bubble_sheet_answer_key/edit"}, "text/html; charset=utf-8"
        if not form.get("assignment[title]") or template is None or form.get("assignment[student_submission]") != "false":
            return 302, "", {"Location": self.url + f"/courses/{course}/assignments/new"}, "text/html; charset=utf-8"
        assignment = self.add_assignment(course, form["assignment[title]"], pdf_pages(template))
        self.assignments[assignment]["template_pdf"] = template
        self.assignments[assignment]["processing"] = self.template_reads
        self.assignments[assignment]["created_by"] = self.email
        return 302, "", {"Location": self.url + f"/courses/{course}/assignments/{assignment}/outline/edit"}, "text/html; charset=utf-8"

    def _json(self, session, content, handler, record):
        if handler.headers.get("X-CSRF-Token") != session["csrf"]:
            return None, (422, json.dumps({"error": "Invalid authenticity token"}), {}, "application/json")
        try:
            body = json.loads(content or b"{}")
        except ValueError:
            return None, (400, json.dumps({"error": "Bad request"}), {}, "application/json")
        record["json"] = body
        return body, None

    def _save_outline(self, session, course, assignment, content, handler, record):
        body, refused = self._json(session, content, handler, record)
        if refused:
            return refused
        kept = set()
        for index, entry in enumerate(body.get("question_data") or [], 1):
            if entry.get("id"):
                found = self.questions[int(entry["id"])]
                found.update(title=entry["title"], weight=float(entry["weight"]), index=index, crops=entry.get("crop_rect_list") or [])
                kept.add(found["id"])
                continue
            # A new question, as the real site makes one: negative scoring, bounds on, and a default "Correct" item worth 0.
            question = self.add_question(assignment, index, entry["title"], float(entry["weight"]), [(0.0, "Correct")], scoring_type="negative")
            self.questions[question]["crops"] = entry.get("crop_rect_list") or []
            kept.add(question)
        for question in [q for q in self.questions.values() if q["assignment"] == assignment and q["id"] not in kept]:
            del self.questions[question["id"]]
        return 200, json.dumps({"path": f"/courses/{course}/assignments/{assignment}/submission_batches"}), {}, "application/json"

    def _rubric_write(self, session, method, question, name, item_id, content, handler, record):
        body, refused = self._json(session, content, handler, record)
        if refused:
            return refused
        if name == "save_grading_options" and method == "POST":
            options = body.get("question") or {}
            question.update(scoring_type=options.get("scoring_type"), floor=options.get("floor") == "1", ceiling=options.get("ceiling") == "1")
            return 200, "{}", {}, "application/json"
        if name == "rubric_items" and method == "POST" and not item_id:
            entry = body.get("rubric_item") or {}
            position = max([i["position"] for i in self.items.values() if i["question"] == question["id"] and not i["group"]] + [-1]) + 1
            item = self._item(question["id"], position, float(entry["weight"]), entry["description"], None)
            found = self.items[item]
            return 200, json.dumps({"id": item, "description": found["description"], "weight": f"{found['weight']:.1f}", "position": position,
                                    "question_id": question["id"], "group_id": None}), {}, "application/json"
        if name == "rubric_items" and method == "PUT" and item_id:  # the editor's save of one item it changed (read 2026-10-27)
            found = self.items.get(int(item_id))
            if not found or found["question"] != question["id"]:
                return 404, json.dumps({"error": "Not Found"}), {}, "application/json"
            entry = body.get("rubric_item") or {}
            found.update(description=entry["description"], weight=float(entry["weight"]), group=entry.get("group_id"))
            self.renamed.append((found["id"], found["description"]))
            return 200, json.dumps({"id": found["id"], "description": found["description"], "weight": f"{found['weight']:.1f}", "position": found["position"],
                                    "question_id": question["id"], "group_id": found["group"]}), {}, "application/json"
        if name == "rubric_items" and method == "DELETE" and item_id:
            found = self.items.pop(int(item_id), None)
            if not found or found["question"] != question["id"]:
                return 404, json.dumps({"error": "Not Found"}), {}, "application/json"
            self.deleted.append(found)
            return 200, json.dumps({"id": found["id"], "description": found["description"]}), {}, "application/json"
        return 404, json.dumps({"error": "Not Found"}), {}, "application/json"

    def _link(self, session, course, content, handler, record):
        if handler.headers.get("X-CSRF-Token") != session["csrf"]:
            return 422, json.dumps({"error": "Invalid authenticity token"}), {}, "application/json"
        form, _ = self._form(content, handler)
        record["form"] = {k: v for k, v in form.items()}
        ids = [int(i) for i in form.get("assignment_ids[]", [])]
        title = (form.get("title") or [""])[0]
        if len(ids) < 2 or not title or any(self.assignments.get(i, {}).get("course") != course or self.assignments[i]["parent"] for i in ids):
            return 422, json.dumps({"errors": {"base": ["Cannot create exam version set with these assignments"]}}), {}, "application/json"
        container = self._id()
        self.assignments[container] = {"id": container, "course": course, "title": title, "pages": self.assignments[ids[0]]["pages"], "published": False,
                                       "bubble_sheet": False, "parent": None, "version": None, "container": True}
        for letter, assignment in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", ids):
            self.assignments[assignment].update(parent=container, version=letter)
        return 200, "{}", {}, "application/json"

    # --- adding a version to a set (2026-10-06): its Edit page and Save, as the sandbox served them on 2026-09-27 -----------------
    def _container_edit(self, course, container, csrf):
        found = self.assignments[container]
        versions = sorted((a for a in self.assignments.values() if a["parent"] == container), key=lambda a: a["version"] or "")
        offered = [a for a in self.assignments.values() if a["course"] == course and not a["container"] and not a["bubble_sheet"]
                   and a["parent"] in (None, container)]
        gon = (f'<script>window.gon={{}};gon.unversioned_assignments={json.dumps([{"id": a["id"], "title": a["title"], "settings": {"bubble_sheet": False}} for a in offered])};'
               f'gon.selected_assignment_ids={json.dumps([a["id"] for a in versions])};gon.versions=[];</script>')
        checked = 'checked="checked" ' if found.get("anonymized") else ""
        form = (f'<form class="form js-assignmentContainerForm" id="edit_assignment_container_{container}" enctype="multipart/form-data" '
                f'action="/courses/{course}/assignment_containers/{container}" accept-charset="UTF-8" method="post">'
                '<input name="utf8" type="hidden" value="&#x2713;" /><input type="hidden" name="_method" value="patch" />'
                f'<input type="hidden" name="authenticity_token" value="{csrf}" />'
                f'<input placeholder="Name your assignment container" class="js-assignmentContainerName form--textInput" type="text" '
                f'value="{html.escape(found["title"], quote=True)}" name="assignment_container[title]" id="assignment_container_title" />'
                f'<input type="hidden" name="assignment_ids" id="assignment_ids" value="{",".join(str(a["id"]) for a in versions)}" '
                'class="js-assignmentVersionsField form--textInput" />'
                '<input name="assignment_container[submissions_anonymized]" type="hidden" value="0" />'
                f'<input class="editAssignmentSubmissionAnonymizationToolTip" type="checkbox" value="1" {checked}'
                'name="assignment_container[submissions_anonymized]" id="assignment_container_submissions_anonymized" />'
                '<div class="js-linkAssignmentVersionsTarget"></div>'
                '<button name="button" type="button" class="tiiBtn tiiBtn-destructive js-deleteAssignmentContainerButtonForModalSimple">Delete Exam Version Set</button>'
                '<input type="submit" name="commit" value="Save" class="tiiBtn tiiBtn-primary" data-disable-with="Save" /></form>')
        return page("Edit Exam Version Set", csrf, gon + form)

    def _edit_container(self, session, course, container, content, handler, record):
        form, _ = self._form(content, handler)
        record["form"] = dict(form)
        if form.get("authenticity_token") != session["csrf"] or (form.get("_method") or "").lower() != "patch":
            return 422, page("The change you wanted was rejected", None, "<h1>422</h1>"), {}, "text/html; charset=utf-8"
        edit = {"Location": self.url + f"/courses/{course}/assignment_containers/{container}/edit"}
        try:
            ids = [int(i) for i in (form.get("assignment_ids") or "").split(",") if i.strip()]
        except ValueError:
            return 302, "", edit, "text/html; charset=utf-8"
        if not form.get("assignment_container[title]") or not ids or len(set(ids)) != len(ids) or any(
                self.assignments.get(i, {}).get("course") != course or self.assignments[i]["container"] or self.assignments[i]["bubble_sheet"]
                or self.assignments[i]["parent"] not in (None, container) for i in ids):
            return 302, "", edit, "text/html; charset=utf-8"  # Rails shows the form again, nothing saved
        found = self.assignments[container]
        found.update(title=form["assignment_container[title]"], anonymized=form.get("assignment_container[submissions_anonymized]") == "1")
        for assignment in self.assignments.values():
            if assignment["parent"] == container and assignment["id"] not in ids:
                assignment.update(parent=None, version=None)
        for letter, assignment in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", ids):
            self.assignments[assignment].update(parent=container, version=letter)
        self.version_set_edits.append(record)
        return 302, "", {"Location": self.url + f"/courses/{course}/assignments"}, "text/html; charset=utf-8"

    # --- publishing grades (2026-10-02): Review Grades and its form, as read on the real site ------------------------------------
    def _review_grades(self, course, plural, target, csrf):
        found = self.assignments[target]
        action = f"/courses/{course}/{plural}/{target}"
        field = "assignment_container[published]" if found["container"] else "assignment[published]"
        if found["published"]:  # the action bar offers Unpublish (never sent by Faculty) and Email Students
            bar = (f'<form class="button_to" method="post" action="{action}"><input type="hidden" name="_method" value="put" autocomplete="off" />'
                   f'<button class="tiiBtn tiiBtn-secondary actionBar--action" type="submit">Unpublish Grades</button>'
                   f'<input type="hidden" name="authenticity_token" value="{csrf}" autocomplete="off" /><input type="hidden" name="{field}" value="false" autocomplete="off" /></form>'
                   '<button name="button" type="button" title="Compose Email to Students" class="tiiBtn tiiBtn-primary actionBar--action js-notifyStudents">Email Students</button>')
        elif self.publish_modal:
            bar = '<button name="button" type="button" class="tiiBtn tiiBtn-primary actionBar--action js-publishGrades">Publish Grades</button>'
        else:
            method = "patch" if found["container"] else "put"
            bar = (f'<form class="review-grades-next-button" method="post" action="{action}"><input type="hidden" name="_method" value="{method}" autocomplete="off" />'
                   f'<button title="Publish Grades" class="tiiBtn tiiBtn-primary actionBar--action" type="submit">Publish Grades</button>'
                   f'<input type="hidden" name="authenticity_token" value="{csrf}" autocomplete="off" /><input type="hidden" name="{field}" value="true" autocomplete="off" /></form>')
        students = sum(1 for s in self.submissions.values() if s["assignment"] == target or self.assignments[s["assignment"]]["parent"] == target)
        modal = (f'<div class="modalv2 js-publishGradesModal js-publishAllGradesModal"><div class="modalv2--body">'
                 f'<form class="js-publishGradesForm" action="{action}" accept-charset="UTF-8" method="post"><input name="utf8" type="hidden" value="&#x2713;" autocomplete="off" />'
                 f'<input type="hidden" name="_method" value="put" autocomplete="off" /><input type="hidden" name="authenticity_token" value="{csrf}" autocomplete="off" />'
                 f'<p>You are about to publish grades for <strong>{students} Students.</strong></p><p>Once published, students will be able to view their results.</p>'
                 '<div class="form--group"><input type="hidden" name="assignment[published]" id="assignment_published" value="true" autocomplete="off" />'
                 '<input type="hidden" name="assignment_container[published]" id="assignment_container_published" value="true" autocomplete="off" />'
                 '<input type="checkbox" name="user[settings_ignore_publish_grades_modal]" id="user_settings_ignore_publish_grades_modal" value="1" />'
                 '<label for="user_settings_ignore_publish_grades_modal">Don&#39;t show this message again.</label></div></form>'
                 '<input type="submit" name="commit" value="Publish Grades" class="tiiBtn tiiBtn-primary js-submitPublishGrades" /></div></div>')
        return page("Review Grades", csrf, f'<div class="actionBar">{bar}</div><table class="js-reviewGradesTable"></table>{modal}')

    def _publish(self, session, course, plural, target, content, handler, record):
        form = {k: v[-1] for k, v in urllib.parse.parse_qs(content.decode(), keep_blank_values=True).items()}
        record["form"] = form
        if form.get("authenticity_token") != session["csrf"]:
            return 422, page("The change you wanted was rejected", None, "<h1>422</h1>"), {}, "text/html; charset=utf-8"
        found = self.assignments[target]
        field = "assignment_container[published]" if found["container"] else "assignment[published]"
        back = {"Location": self.url + f"/courses/{course}/{plural}/{target}/review_grades"}
        if form.get("_method", "").lower() not in ("put", "patch") or form.get(field) not in ("true", "false"):
            return 302, "", back, "text/html; charset=utf-8"  # Rails sends you back, whatever it did
        self.publish_hits.append(record)
        published = form[field] == "true"
        found["published"] = published
        for version in (a for a in self.assignments.values() if a["parent"] == target):
            version["published"] = published  # "Publish All Grades": every version of the set
        if "user[settings_ignore_publish_grades_modal]" in form:
            record["setting_changed"] = True  # the instructor's own setting: Faculty must never send it
        return 302, "", back, "text/html; charset=utf-8"

    # --- the bubble-sheet answer key editor (THE BUBBLE-SHEET EXAM TOO, read on the real site 2026-10-26) ----------------------
    def bubble_key(self, assignment):
        """A bubble-sheet version's key as its outline questions hold it: {number: (letters, scoring, weight)}."""
        return {q["index"]: (q["mc"][0], q["mc"][1], q["weight"]) for q in self.questions.values() if q["assignment"] == assignment and q.get("mc")}

    def _answer_key(self, course, assignment):
        return {"answerKey": {str(n): {"answer": list(letters), "scoring_type": scoring, "weight": float(weight)}
                              for n, (letters, scoring, weight) in sorted(self.bubble_key(assignment).items())},
                "saveUrl": f"/courses/{course}/assignments/{assignment}/bubble_sheet_answer_key",
                "updateAssignmentUrl": f"/courses/{course}/bubble_sheet_assignments/{assignment}",
                "editAssignmentUrl": f"/courses/{course}/assignments/{assignment}/edit",
                "templateProperties": {"num_columns_per_page": 4, "num_pages": 2, "num_questions_per_column": 25, "version": "v2", "version_to_switch_to": "v1"},
                "rubricsLocked": False}

    def _answer_keys(self, course, target):
        found = self.assignments[target]
        if not found["container"]:
            return {"answerKeys": {"Version A": self._answer_key(course, target)}, "maxVersions": 5, "flash": None,
                    "addVersionUrl": f"/courses/{course}/assignments/{target}/bubble_sheet_answer_key"}
        versions = sorted((a for a in self.assignments.values() if a["parent"] == target and a["version"] != "?"), key=lambda a: a["version"])
        url = f"/courses/{course}/assignment_containers/{target}/bubble_sheet_answer_key"
        return {"answerKeys": {f"Version {a['version']}": self._answer_key(course, a["id"]) for a in versions}, "maxVersions": 5, "flash": None,
                "addVersionUrl": url, "containerSaveUrl": url}

    def _set_key(self, assignment, entries):
        for question in [q for q in self.questions.values() if q["assignment"] == assignment]:
            del self.questions[question["id"]]
        for number, entry in sorted(entries.items(), key=lambda pair: int(pair[0])):
            self.add_question(assignment, int(number), f"Question {number}", entry["weight"], [], mc=("".join(entry["answer"]), entry["scoring_type"]))

    def _key_write(self, session, method, course, target, content, handler, record):
        body, refused = self._json(session, content, handler, record)
        if refused:
            return refused
        found = self.assignments[target]
        if method == "POST":  # Add Version
            if found["container"]:
                container = target
            elif found["parent"]:
                return 404, json.dumps({"error": "Not Found"}), {}, "application/json"
            else:  # the first Add Version: the assignment becomes Version A of a new set named as it was
                container = self._id()
                self.assignments[container] = {"id": container, "course": course, "title": found["title"], "pages": 2, "published": False,
                                               "bubble_sheet": True, "parent": None, "version": None, "container": True, "created_by": found.get("created_by")}
                found.update(parent=container, version="A", title=f"{found['title']} Version A", fresh_bubble=False)
            versions = sorted(a["version"] for a in self.assignments.values() if a["parent"] == container and a["version"] != "?")
            if len(versions) >= 5:
                return 200, json.dumps({"errors": "At most 5 versions"}), {}, "application/json"
            letter = chr(ord(max(versions)) + 1)
            made = self.add_assignment(course, f"{self.assignments[container]['title']} Version {letter}", 2, bubble_sheet=True, parent=container, version=letter)
            url = f"/courses/{course}/assignment_containers/{container}/bubble_sheet_answer_key"
            return 200, json.dumps({"version": f"Version {letter}", "answerKeyData": self._answer_key(course, made),
                                    **({"containerSaveUrl": url, "redirectTo": url + "/edit"} if container != target else {})}), {}, "application/json"
        sent = (body or {}).get("bubble_sheet_assignment_answer_key")
        if not isinstance(sent, dict):
            return 400, json.dumps({"error": "Bad request"}), {}, "application/json"
        if found["container"]:
            by_name = {f"Version {a['version']}": a["id"] for a in self.assignments.values() if a["parent"] == target and a["version"] != "?"}
            if set(sent) - set(by_name):
                return 422, json.dumps({"error": "Unknown version"}), {}, "application/json"
            for name, entries in sent.items():
                self._set_key(by_name[name], entries)
        else:
            self._set_key(target, sent)
        self.key_saves.append({"target": target, "key": sent})
        return 200, json.dumps({"redirect_to": f"/courses/{course}/{'assignment_containers' if found['container'] else 'assignments'}/{target}"}), {}, "application/json"

    def published(self):
        """What was published by a form (never by building the course state)."""
        return [hit for hit in self.publish_hits if hit.get("form")]

    def created(self):
        """The assignments Faculty made, by title."""
        return {a["title"]: a for a in self.assignments.values() if a.get("created_by")}
