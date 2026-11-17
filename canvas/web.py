"""Talking to Canvas (canvas.tamu.edu) with the instructor's KEPT session (requirement, 2026-09-27).

Canvas has an official REST API (`/api/v1/...`), and it accepts the signed-in browser's session cookies exactly as Canvas's own
pages do: reads need only the cookies, writes also send `X-CSRF-Token` (the `_csrf_token` cookie, decoded). The session is
made by the sign-in window (`canvas/signin.py`: NetID, password and Duo in a private browser on this server) and kept in ONE
file, named by FACULTY_CANVAS_SESSION_FILE (no default in code; the systemd drop-in names ~/.config/faculty/canvas-session.json),
written 0600, read with the same checks as the Gradescope sign-in file. The cookies go only to the Canvas host; no redirect is
followed; nothing of them is logged or returned. Canvas refusing the session (401, or a redirect to its login) is `SignedOut`:
the page then offers Sign in to Canvas again. Faculty never visits Canvas's /logout (it may end the instructor's other sessions).
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import re
import stat
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from http.cookies import SimpleCookie

from private_files import write_private_json

LOG = logging.getLogger(__name__)
SESSION_ENV = "FACULTY_CANVAS_SESSION_FILE"
URL_ENV = "FACULTY_CANVAS_URL"
BASE = "https://canvas.tamu.edu"
TIMEOUT = 60
THROTTLE_WAIT = 3  # seconds a patient client waits after Canvas's throttle refused a request, before asking once more
LIMIT = 64 * 1024 * 1024   # the most one Canvas answer may hold
PAGES = 200                # the most pages one listing follows (100 items each)
SUBMISSION_PAGES = 2000    # a course's submissions: 200,000 at most (students x assignments)
FILE_LIMIT = 256 * 1024    # a kept session is a few kilobytes
HOPS = 4                   # the most redirects an attachment's download follows
SYNC_MAX = 100             # Canvas's max_group_conversation_size: its inbox sends to more recipients asynchronously
# WEBASSIGN (requirement, 2026-10-04): an assignment Canvas hands to Cengage's tool is graded in WebAssign, which sends its scores to
# Canvas itself. Read on MATH 140's courses that day: all 26 WebAssign assignments name gateway.cengage.com; Gradescope's own tool
# assignments (www.gradescope.com) share the "external_tool" submission type, so the tool's host decides, not the type.
WEBASSIGN_HOSTS = ("cengage.com", "webassign.net", "webassign.com")


class CanvasError(Exception):
    """Canvas could not be read or written; the message is for the instructor and never holds a cookie."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def refused(self):
        """Canvas answered a write with a 4xx: it did not take it, so a receipt is `failed` (safe to ask again as a new action) and
        not `uncertain` (the instructor must look in Canvas; CANVAS, OUTSIDE SERVICES "Keep a receipt for every write")."""
        return self.status is not None and 400 <= self.status < 500


class SignedOut(CanvasError):
    """No kept session, or Canvas no longer accepts it: sign in to Canvas again."""


def base_url(value):
    """https://canvas.tamu.edu (or another https Canvas host the deployment names), or a loopback URL for a test server."""
    value = (value or BASE).rstrip("/")
    parts = urllib.parse.urlsplit(value)
    try:
        loopback = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if parts.username or parts.query or parts.fragment or parts.path not in ("", "/") or not parts.hostname:
        raise ValueError("The Canvas address must be a plain https://host.")
    if parts.scheme != "https" and not (loopback and parts.scheme == "http"):
        raise ValueError("Canvas may only be reached over https (or a loopback test server).")
    return value


@dataclass(frozen=True)
class Settings:
    session_file: str = ""
    base: str = BASE

    @classmethod
    def environment(cls):
        return cls(session_file=os.environ.get(SESSION_ENV, "").strip(), base=base_url(os.environ.get(URL_ENV, "").strip() or BASE))

    @property
    def configured(self):
        return bool(self.session_file) and os.path.isabs(self.session_file)

    @property
    def host(self):
        return urllib.parse.urlsplit(self.base).hostname

    def status(self):
        """Whether Faculty may use Canvas, and whether a session is kept: the file's presence and permissions only (no read)."""
        if not self.configured:
            return {"configured": False, "signed_in": False, "problem": "Canvas is not set up for this Faculty instance.", "user": None, "saved_at": None}
        try:
            info = os.lstat(self.session_file)
        except OSError:
            return {"configured": True, "signed_in": False, "problem": "", "user": None, "saved_at": None}
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return {"configured": True, "signed_in": False, "user": None, "saved_at": None,
                    "problem": "The kept Canvas session file must be a file of this account with mode 0600. Sign in to Canvas again."}
        try:
            kept = load(self)
        except CanvasError as error:
            return {"configured": True, "signed_in": False, "problem": str(error), "user": None, "saved_at": None}
        return {"configured": True, "signed_in": True, "problem": "", "user": kept.get("user"), "saved_at": kept.get("saved_at")}


_FILE_LOCK = threading.RLock()  # also held across a client's read-check-write of the kept session (Client.close)


def load(settings):
    """The kept session: {cookies: {name: value}, user: {name}, saved_at}. SignedOut when there is none."""
    path = settings.session_file
    if not settings.configured:
        raise SignedOut("Canvas is not set up for this Faculty instance.")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise SignedOut("Sign in to Canvas first.") from None
    except OSError:
        raise CanvasError("The kept Canvas session cannot be opened (a link?). Sign in to Canvas again.") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > FILE_LIMIT:
            raise CanvasError("The kept Canvas session file must be a small file of this account with mode 0600. Sign in to Canvas again.")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(FILE_LIMIT + 1)
    finally:
        os.close(descriptor)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), dict) or data.get("host") != settings.host \
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["cookies"].items()):
        raise SignedOut("The kept Canvas session is not usable. Sign in to Canvas again.")
    return data


def save(settings, cookies, user=None, saved_at=None, sign_in=None):
    """Writes the kept session atomically, 0600, beside where it goes (never through a link). `sign_in` None is a new sign-in
    (a new random ID, which Client.close compares); a client keeping renewed cookies passes the kept one on."""
    if not settings.configured:
        raise CanvasError("Canvas is not set up for this Faculty instance.")
    kept = {"host": settings.host, "cookies": {k: v for k, v in sorted(cookies.items()) if k and v is not None},
            "user": user, "saved_at": saved_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sign_in": uuid.uuid4().hex if sign_in is None else sign_in}
    directory = os.path.dirname(settings.session_file)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with _FILE_LOCK:
        write_private_json(settings.session_file, kept, ".canvas-session-")
    return kept


def forget(settings):
    """Deletes the kept session (Faculty's copy only: Canvas is not told, so the instructor's own browser stays signed in)."""
    if not settings.configured:
        return False
    with _FILE_LOCK:
        try:
            os.unlink(settings.session_file)
            return True
        except FileNotFoundError:
            return False


def strip(body):
    """Canvas prefixes JSON answered to a browser session with `while(1);` (against JSON hijacking)."""
    text = body.decode("utf-8", "replace").lstrip()
    return text[len("while(1);"):] if text.startswith("while(1);") else text


def answer_json(body):
    """The JSON in a Canvas answer (after the `while(1);` prefix), or None when it is not JSON."""
    try:
        return json.loads(strip(body))
    except ValueError:
        return None


def refusal(status, body):
    """Canvas's refusal as a CanvasError: its status and the first of its error messages."""
    message = ""
    try:
        found = json.loads(strip(body))
        errors = found.get("errors") if isinstance(found, dict) else None
        if isinstance(errors, list) and errors:
            message = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors[:3])
        elif isinstance(errors, dict):
            message = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
        elif isinstance(found, dict) and found.get("message"):
            message = str(found["message"])
        elif isinstance(found, list) and found and all(isinstance(e, dict) for e in found):
            # The conversations API refuses as [{attribute, message}] ("recipients: too many for group conversation").
            message = "; ".join(f"{e.get('attribute')}: {e.get('message')}" if e.get("attribute") else str(e.get("message", e)) for e in found[:3])
    except ValueError:
        pass
    return CanvasError(f"Canvas answered {status}{': ' + message[:300] if message else ''}.", status=status)


def next_link(header):
    """The rel="next" URL of Canvas's Link header, or None."""
    for part in (header or "").split(","):
        found = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', part)
        if found:
            return found.group(1)
    return None


class Client:
    """One use of the kept session. Cookies Canvas sets on an answer (it renews its session and CSRF token) are kept again."""

    def __init__(self, settings, patient=False):
        self.settings = settings
        # Canvas's throttle (403 "Rate Limit Exceeded", lib/request_throttle.rb) refuses a request before Canvas acts on it, so a
        # patient client asks once more after THROTTLE_WAIT: Sync with Canvas sends to several courses at once (2026-10-06).
        self.patient = patient
        kept = load(settings)
        self.cookies = dict(kept["cookies"])
        self._kept = kept.get("sign_in") or "", kept.get("saved_at")  # which sign-in this client was opened with
        self._renewed = {}  # what Canvas set (a value) or expired (None) on this client's answers
        self._probing = False  # asking Canvas who the session is, after a 401 that did not say (_unauthenticated)
        parts = urllib.parse.urlsplit(settings.base)
        self._scheme, self._netloc = parts.scheme, parts.netloc

    # --- plumbing ---------------------------------------------------------------------------------------------------------
    def _path(self, path):
        """A path on the Canvas host: a relative /api path, or a full URL Canvas gave (pagination) that must be on the same host."""
        if path.startswith("/"):
            return path
        parts = urllib.parse.urlsplit(path)
        if parts.scheme != self._scheme or parts.netloc != self._netloc:
            raise CanvasError("Canvas pointed Faculty somewhere else; nothing was sent there.")
        return urllib.parse.urlunsplit(("", "", parts.path, parts.query, ""))

    def request(self, method, path, data=None, again=False):
        """(status, headers, body). JSON bodies for `data`."""
        path = self._path(path)
        headers = {"Accept": "application/json+canvas-string-ids, application/json", "User-Agent": "Faculty",
                   "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        body = None
        if method != "GET":
            token = self.cookies.get("_csrf_token")
            if token:
                headers["X-CSRF-Token"] = urllib.parse.unquote(token)
            if data is not None:
                body, headers["Content-Type"] = json.dumps(data).encode(), "application/json"
        connection = (http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection)(self._netloc, timeout=TIMEOUT)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(LIMIT + 1)
            answer = response.status, {k.lower(): v for k, v in response.getheaders()}, raw
            set_cookies = response.headers.get_all("Set-Cookie") or []
        except (OSError, http.client.HTTPException) as error:
            raise CanvasError(f"Canvas could not be reached ({type(error).__name__}).") from None
        finally:
            connection.close()
        if len(raw) > LIMIT:
            raise CanvasError("Canvas answered with more than Faculty reads at once.")
        for line in set_cookies:
            cookie = SimpleCookie()
            try:
                cookie.load(line)
            except Exception:
                continue
            for name, morsel in cookie.items():
                expired = morsel["max-age"] == "0" or (morsel["expires"] and "1970" in morsel["expires"])
                if expired:
                    if self.cookies.pop(name, None) is not None:
                        self._renewed[name] = None
                elif self.cookies.get(name) != morsel.value:
                    self.cookies[name] = self._renewed[name] = morsel.value
        status = answer[0]
        location = answer[1].get("location", "")
        if (status in (301, 302, 303, 307, 308) and "/login" in location) or (status == 401 and self._unauthenticated(answer[1], raw)):
            raise SignedOut("Canvas no longer accepts Faculty's session. Sign in to Canvas again.", status=status)
        if self.patient and not again and status == 403 and b"rate limit exceeded" in raw[:512].lower():
            time.sleep(THROTTLE_WAIT)
            return self.request(method, path, data=data, again=True)
        return answer

    def _unauthenticated(self, headers, raw):
        """Whether a 401 means Canvas no longer knows the session. Canvas also answers 401 to a session it knows that may not do
        something (a concluded course is read-only; `render_json_unauthorized`: status "unauthorized"), and signing in again
        changes nothing then (2026-10-04 review). With no user it says "unauthenticated" and adds WWW-Authenticate; any other 401
        (a file Canvas refuses answers in HTML) is settled by asking Canvas who the session is."""
        if headers.get("www-authenticate"):
            return True
        found = answer_json(raw)
        said = str(found.get("status") or "").lower() if isinstance(found, dict) else ""
        if said in ("unauthenticated", "unauthorized"):
            return said == "unauthenticated"
        if self._probing:
            return True  # Canvas refused even to say who the session is
        self._probing = True
        try:
            status, _, _ = self.request("GET", "/api/v1/users/self")
        except SignedOut:
            return True
        finally:
            self._probing = False
        return status != 200

    def json(self, method, path, data=None):
        status, headers, body = self.request(method, path, data=data)
        if status not in (200, 201):
            raise refusal(status, body)
        try:
            return json.loads(strip(body)), headers
        except ValueError:
            raise CanvasError("Canvas's answer could not be read.", status=status) from None

    def get(self, path):
        return self.json("GET", path)[0]

    def listing(self, path, pages=PAGES):
        """Every item of a paginated listing (per_page=100, following Canvas's Link header on the same host)."""
        separator = "&" if "?" in path else "?"
        url, found = f"{path}{separator}per_page=100", []
        for _ in range(pages):
            page, headers = self.json("GET", url)
            if not isinstance(page, list):
                raise CanvasError("Canvas answered a listing with something else.")
            found.extend(page)
            url = next_link(headers.get("link"))
            if not url:
                return found
        raise CanvasError("Canvas's listing is longer than Faculty reads.")

    def close(self):
        """Keeps the cookies Canvas renewed or expired on this client's answers, and only those, while the kept session is still the
        sign-in this client was opened with: a client open across a new sign-in never puts the old session's cookies over it
        (2026-10-01 audit: they replaced a fresh sign-in). Best effort: Canvas has answered by then, and a file that cannot be
        written (a full disk) must not turn what Canvas did into a failure (2026-10-04 review: a message Canvas had accepted would
        have been recorded as uncertain); the session's next answer renews them again."""
        if not self._renewed:
            return
        renewed, self._renewed = self._renewed, {}
        with _FILE_LOCK:
            try:
                current = load(self.settings)
            except (CanvasError, OSError):
                return
            if (current.get("sign_in") or "", current.get("saved_at")) != self._kept:
                return
            cookies = dict(current["cookies"])
            for name, value in renewed.items():
                if value is None:
                    cookies.pop(name, None)
                else:
                    cookies[name] = value
            try:
                save(self.settings, cookies, user=current.get("user"), saved_at=current.get("saved_at"), sign_in=current.get("sign_in") or "")
            except OSError as error:
                LOG.warning("The renewed Canvas session could not be kept (%s); its next answer renews it again.", type(error).__name__)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # --- reading ----------------------------------------------------------------------------------------------------------
    def me(self):
        found = self.get("/api/v1/users/self")
        return {"id": str(found.get("id", "")), "name": str(found.get("name") or found.get("short_name") or "")}

    def courses(self):
        """The courses where the instructor teaches (published or not), with their term."""
        rows = self.listing("/api/v1/courses?enrollment_type=teacher&state[]=available&state[]=unpublished&state[]=completed&include[]=term")
        return [{"id": str(c["id"]), "name": str(c.get("name") or ""), "code": str(c.get("course_code") or ""),
                 "term": str((c.get("term") or {}).get("name") or ""), "state": str(c.get("workflow_state") or ""),
                 "sis": str(c.get("sis_course_id") or "")} for c in rows if isinstance(c, dict) and c.get("id") is not None]

    def sections(self, course_id):
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/sections")
        return [{"id": str(s["id"]), "name": str(s.get("name") or ""), "sis": str(s.get("sis_section_id") or "")} for s in rows]

    def students(self, course_id):
        """Every student enrollment of the course, with its Canvas section and the student's SIS ID (TAMU: the UIN)."""
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/enrollments?type[]=StudentEnrollment&state[]=active&state[]=invited"
                            "&state[]=inactive&state[]=completed")
        found = []
        for e in rows:
            user = e.get("user") or {}
            found.append({"user_id": str(e.get("user_id") or user.get("id") or ""), "section_id": str(e.get("course_section_id") or ""),
                          "state": str(e.get("enrollment_state") or ""), "sis": str(e.get("sis_user_id") or user.get("sis_user_id") or ""),
                          "name": str(user.get("sortable_name") or user.get("name") or "")})
        return found

    def assignment_groups(self, course_id):
        """The course's assignment groups with their assignments (Canvas's own order): what a gradebook import reads."""
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/assignment_groups?include[]=assignments")
        groups = []
        for g in rows:
            if not isinstance(g, dict) or g.get("id") is None:
                continue
            assignments = []
            for a in g.get("assignments") or []:
                if not isinstance(a, dict) or a.get("id") is None:
                    continue
                assignments.append({"id": str(a["id"]), "name": str(a.get("name") or ""), "points": a.get("points_possible"),
                                    "due_at": str(a.get("due_at") or ""), "published": bool(a.get("published", True)),
                                    "grading_type": str(a.get("grading_type") or "points"),
                                    "state": str(a.get("workflow_state") or "published"), "tool": tool_host(a),
                                    "post_manually": bool(a.get("post_manually")), "quiz": canvas_quiz(a)})
            groups.append({"id": str(g["id"]), "name": str(g.get("name") or ""), "assignments": assignments})
        return groups

    def submissions(self, course_id):
        """Every student's submission of every assignment of the course (one listing; a large course has tens of thousands)."""
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/students/submissions?student_ids[]=all", pages=SUBMISSION_PAGES)
        found = []
        for s in rows:
            if not isinstance(s, dict) or s.get("assignment_id") is None or s.get("user_id") is None:
                continue
            found.append({"assignment_id": str(s["assignment_id"]), "user_id": str(s["user_id"]), "score": s.get("score"),
                          "entered_score": s.get("entered_score"),
                          "excused": bool(s.get("excused")), "missing": bool(s.get("missing")), "late": bool(s.get("late")),
                          "state": str(s.get("workflow_state") or ""), "posted": bool(s.get("posted_at"))})
        return found

    # --- modules and pages (canvas/content.py): what Faculty keeps in step across a term's courses -------------------------
    def modules(self, course_id):
        """The course's modules in Canvas's order, each with its items (read per module: the combined listing leaves out what an
        item points to)."""
        course = int(course_id)
        found = []
        for m in self.listing(f"/api/v1/courses/{course}/modules"):
            if not isinstance(m, dict) or m.get("id") is None:
                continue
            items = self.module_items(course, m["id"])
            found.append({**module_row(m), "items": items})
        found.sort(key=lambda m: m["position"])
        return found

    def module_items(self, course_id, module_id):
        return sorted((item_row(i) for i in self.listing(f"/api/v1/courses/{int(course_id)}/modules/{int(module_id)}/items")
                       if isinstance(i, dict) and i.get("id") is not None), key=lambda i: i["position"])

    def pages(self, course_id):
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/pages?include[]=body")
        return [page_row(p) for p in rows if isinstance(p, dict) and p.get("page_id") is not None]

    def named(self, course_id, kind):
        """{id: name} of the course's assignments, quizzes or discussion topics: what a module item or a page link names."""
        path = {"Assignment": "assignments", "Quiz": "quizzes", "Discussion": "discussion_topics"}[kind]
        key = "name" if kind == "Assignment" else "title"
        return {str(r["id"]): str(r.get(key) or "") for r in self.listing(f"/api/v1/courses/{int(course_id)}/{path}")
                if isinstance(r, dict) and r.get("id") is not None}

    def course_files(self, course_id):
        """({file id: "folder path/name"}, {file id: {size, locked}}) of a course's Files, from one listing: where each file is,
        and what tells a file Faculty keeps from the one Canvas holds under its name (canvas FILES)."""
        course = int(course_id)
        folders = {str(f["id"]): str(f.get("full_name") or "") for f in self.listing(f"/api/v1/courses/{course}/folders")
                   if isinstance(f, dict) and f.get("id") is not None}
        rows = [f for f in self.listing(f"/api/v1/courses/{course}/files") if isinstance(f, dict) and f.get("id") is not None]
        return ({str(f["id"]): f"{folders.get(str(f.get('folder_id')), '')}/{f.get('display_name') or ''}" for f in rows},
                {str(f["id"]): {"size": int(f.get("size") or 0), "locked": bool(f.get("locked"))} for f in rows})

    def upload_course_file(self, course_id, name, media, content, folder="", replace=False):
        """A file into a course's Files (canvas FILES, requirement, 2026-10-10), by Canvas's three documented steps (`_upload`).
        `folder` is its folder under the course's files ("" the top one); Canvas makes a folder that is missing. `replace`: it
        takes the place of the file of that name there (`on_duplicate: overwrite`; Canvas points that file's module items and
        links at the new one). Otherwise a file already of that name is never touched: Canvas keeps this one under another name
        (`rename`), which the answer says. Returns `file_row`, read from Canvas itself once the file is there (the file service's own
        answer is used when that read fails: the file is in the course either way). CanvasError carries Canvas's status; None when
        an answer was lost (the file may be in the course)."""
        fields = {"on_duplicate": "overwrite" if replace else "rename"}
        if folder:
            fields["parent_folder_path"] = folder
        made = self._upload(f"/api/v1/courses/{int(course_id)}/files", fields, name, media, content)
        try:
            return file_row(self.get(f"/api/v1/files/{int(made['id'])}"))
        except (CanvasError, ValueError, TypeError, KeyError):
            return file_row(made)

    def lock_file(self, file_id, locked):
        """A file unpublished (`locked`) or published in Canvas's Files. Returns `file_row`."""
        return file_row(self.write("PUT", f"/api/v1/files/{int(file_id)}", {"locked": bool(locked)}))

    def assignments(self, course_id):
        """Every assignment of the course with what Faculty's copy keeps of it (and whether Canvas lets it be graded here)."""
        return [assignment_row(a) for a in self.listing(f"/api/v1/courses/{int(course_id)}/assignments")
                if isinstance(a, dict) and a.get("id") is not None]

    def groups(self, course_id):
        """{assignment group id: name} of the course."""
        return {str(g["id"]): str(g.get("name") or "") for g in self.listing(f"/api/v1/courses/{int(course_id)}/assignment_groups")
                if isinstance(g, dict) and g.get("id") is not None}

    def update_grades(self, course_id, assignment_id, grades):
        """Canvas's bulk grade update: {user id: {"posted_grade": "9"} | {"excuse": True}}, with `"posted_grade": ""` to take a grade
        out and `"excuse": False` beside a grade for a submission Canvas has excused (Sync with Canvas). Canvas answers with a
        Progress to follow; what it did is read back."""
        return self.write("POST", f"/api/v1/courses/{int(course_id)}/assignments/{int(assignment_id)}/submissions/update_grades",
                          {"grade_data": grades})

    def progress(self, progress_id):
        return self.get(f"/api/v1/progress/{int(progress_id)}")

    # --- posting and hiding an assignment's grades (CANVAS "POSTING GRADES") ------------------------------------------------
    # Canvas posts and hides grades only through its GraphQL API, as its own gradebook's Post and Hide do. Read from Canvas's
    # schema on 2026-10-06 (an introspection read): postAssignmentGrades(input: {assignmentId: ID!, gradedOnly, onlyStudentIds,
    # sectionIds, skipStudentIds}) and hideAssignmentGrades(input: {assignmentId: ID!, onlyStudentIds, sectionIds, skipStudentIds}),
    # each answering {assignment, errors, progress {_id state message}, sections}.
    POST_GRADES = """mutation FacultyPostGrades($assignmentId: ID!) {
  postAssignmentGrades(input: {assignmentId: $assignmentId, gradedOnly: false}) { progress { _id state message } errors { attribute message } }
}"""
    HIDE_GRADES = """mutation FacultyHideGrades($assignmentId: ID!) {
  hideAssignmentGrades(input: {assignmentId: $assignmentId}) { progress { _id state message } errors { attribute message } }
}"""
    # An assignment's posting policy (requirement, 2026-10-08: a column whose category posts by hand is switched to post by hand in
    # Canvas before its grades are sent). Read from Canvas's schema on 2026-10-08 (an introspection read):
    # setAssignmentPostPolicy(input: {assignmentId: ID!, postManually: Boolean!, postGradesAt, postCommentsAt}) answering
    # {postPolicy {_id assignment course id postManually}, errors}; it acts at once (no progress).
    SET_POST_POLICY = """mutation FacultyPostPolicy($assignmentId: ID!, $postManually: Boolean!) {
  setAssignmentPostPolicy(input: {assignmentId: $assignmentId, postManually: $postManually}) { postPolicy { postManually } errors { attribute message } }
}"""

    def graphql(self, query, variables=None):
        """One GraphQL request (POST /api/graphql, the session's cookies and CSRF token like any write): its `data`. Canvas answers
        200 with an `errors` list when it refuses, and a mutation names its own `errors`; either is a CanvasError carrying Canvas's
        words (status 400: Canvas answered, and refused)."""
        answer, _ = self.json("POST", "/api/graphql", data={"query": query, "variables": variables or {}})
        if not isinstance(answer, dict):
            raise CanvasError("Canvas's answer could not be read.", status=502)
        if answer.get("errors"):
            raise CanvasError(_graphql_words(answer["errors"]), status=400)
        return answer.get("data") or {}

    def post_grades(self, assignment_id):
        """Posts every student's grade of the assignment (Canvas's Post grades, "Everyone"): the Progress {_id, state, message}."""
        return self._posting("postAssignmentGrades", self.POST_GRADES, assignment_id)

    def hide_grades(self, assignment_id):
        """Hides every student's grade of the assignment from students (Canvas's Hide grades): the Progress {_id, state, message}."""
        return self._posting("hideAssignmentGrades", self.HIDE_GRADES, assignment_id)

    def _posting(self, name, query, assignment_id):
        found = (self.graphql(query, {"assignmentId": str(int(assignment_id))}).get(name) or {})
        if found.get("errors"):
            raise CanvasError(_graphql_words(found["errors"]), status=400)
        progress = found.get("progress") or {}
        if not str(progress.get("_id") or "").isdigit():
            raise CanvasError("Canvas did not say how it is getting on.", status=None)  # it may have started: read it back
        return {"id": str(progress["_id"]), "state": str(progress.get("state") or ""), "message": str(progress.get("message") or "")}

    def set_post_policy(self, assignment_id, manual=True):
        """Sets whether Canvas posts the assignment's grades by hand (`manual`) or as each is entered: {"post_manually": bool} as
        Canvas answers it. Canvas refusing is a CanvasError (status 400); an answer without the policy is one with no status (it
        may have changed it: read the assignment back)."""
        found = self.graphql(self.SET_POST_POLICY, {"assignmentId": str(int(assignment_id)), "postManually": bool(manual)}).get("setAssignmentPostPolicy") or {}
        if found.get("errors"):
            raise CanvasError(_graphql_words(found["errors"]), status=400)
        policy = found.get("postPolicy")
        if not isinstance(policy, dict) or not isinstance(policy.get("postManually"), bool):
            raise CanvasError("Canvas did not say how it posts the assignment now.", status=None)
        return {"post_manually": policy["postManually"]}

    def assignment_submissions(self, course_id, assignment_id):
        """{user id: submission} of one assignment: what a sync reads and reads back. `entered_score` is the grade as entered, before
        Canvas's late penalty (what Faculty sends); `state` says whether a submission waits for review; `posted` whether its
        student sees it (POSTING GRADES reads it back)."""
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/assignments/{int(assignment_id)}/submissions")
        return {str(s["user_id"]): {"score": s.get("score"), "entered_score": s.get("entered_score"), "excused": bool(s.get("excused")),
                                    "state": str(s.get("workflow_state") or ""), "posted": bool(s.get("posted_at"))} for s in rows
                if isinstance(s, dict) and s.get("user_id") is not None}

    # --- the communications record (read by communications/service.py `_capture_canvas`): reads only, and nothing marked read ---
    def conversations(self, course_id):
        """The instructor's Canvas inbox conversations in one course, from the inbox, the archive and the sent box (listing them
        changes nothing): [{id, subject, last_message_at, message_count, participants: {user id: name}}]."""
        found = {}
        for scope in ("", "&scope=archived", "&scope=sent"):
            for c in self.listing(f"/api/v1/conversations?filter[]=course_{int(course_id)}{scope}"):
                if isinstance(c, dict) and c.get("id") is not None:
                    found[str(c["id"])] = {"id": str(c["id"]), "subject": str(c.get("subject") or ""),
                                           "last_message_at": str(c.get("last_message_at") or c.get("last_authored_message_at") or ""),
                                           "message_count": int(c.get("message_count") or 0),
                                           "participants": {str(p.get("id")): str(p.get("full_name") or p.get("name") or "")
                                                            for p in c.get("participants") or [] if isinstance(p, dict) and p.get("id") is not None}}
        return list(found.values())

    def conversation(self, conversation_id):
        """One conversation with its messages. Read with auto_mark_as_read=false: Canvas's default would mark it read for the
        instructor, and Faculty never changes what they see in Canvas."""
        c = self.get(f"/api/v1/conversations/{int(conversation_id)}?auto_mark_as_read=false")
        messages = []
        for m in c.get("messages") or []:
            if not isinstance(m, dict) or m.get("id") is None:
                continue
            messages.append({"id": str(m["id"]), "created_at": str(m.get("created_at") or ""), "body": str(m.get("body") or ""),
                             "author_id": str(m.get("author_id") or ""), "generated": bool(m.get("generated")),
                             "participants": [str(u) for u in m.get("participating_user_ids") or []],
                             "attachments": [attachment_row(a) for a in m.get("attachments") or [] if isinstance(a, dict)],
                             "media": bool(m.get("media_comment"))})
        return {"id": str(c.get("id") or conversation_id), "subject": str(c.get("subject") or ""), "context": str(c.get("context_name") or ""),
                "participants": {str(p.get("id")): str(p.get("full_name") or p.get("name") or "")
                                 for p in c.get("participants") or [] if isinstance(p, dict) and p.get("id") is not None},
                "messages": messages}

    def submission_comments(self, course_id):
        """Every comment on a student's submission in the course: [{id, assignment_id, user_id (the student), author_id,
        author_name, comment, created_at, edited_at, attachments}]. One listing of every submission, as `submissions` reads."""
        rows = self.listing(f"/api/v1/courses/{int(course_id)}/students/submissions?student_ids[]=all&include[]=submission_comments",
                            pages=SUBMISSION_PAGES)
        found = []
        for s in rows:
            if not isinstance(s, dict) or s.get("user_id") is None or s.get("assignment_id") is None:
                continue
            for c in s.get("submission_comments") or []:
                if not isinstance(c, dict) or c.get("id") is None:
                    continue
                found.append({"id": str(c["id"]), "assignment_id": str(s["assignment_id"]), "user_id": str(s["user_id"]),
                              "author_id": str(c.get("author_id") or ""), "author_name": str(c.get("author_name") or ""),
                              "comment": str(c.get("comment") or ""), "created_at": str(c.get("created_at") or ""),
                              "edited_at": str(c.get("edited_at") or ""),
                              "attachments": [attachment_row(a) for a in c.get("attachments") or [] if isinstance(a, dict)],
                              "media": bool(c.get("media_comment"))})
        return found

    def download(self, url, limit):
        """The bytes of a file attached in Canvas (an attachment's `url`), and its content type. Canvas answers with the file or with
        a redirect, and its file service usually redirects again to where the file is stored (seen on canvas.tamu.edu, 2026-09-30:
        a second 302). At most HOPS redirects are followed: on Canvas's own host with the session (as every request is), anywhere
        else WITHOUT cookies or any header of the session, and only over https (a loopback test server excepted when Canvas itself
        is one). Nothing larger than `limit`."""
        current = url if not url.startswith("/") else self.settings.base + url
        for _ in range(HOPS + 1):
            target = urllib.parse.urlsplit(current)
            if self._offsite_refused(target):
                raise CanvasError("Canvas pointed the attachment somewhere Faculty does not fetch from.")
            if (target.scheme, target.netloc) == (self._scheme, self._netloc):
                status, headers, body = self.request("GET", current)
            else:
                status, headers, body = self._fetch_elsewhere(target, limit)
            if status == 200:
                if len(body) > limit:
                    raise CanvasError("The attachment is larger than Faculty keeps.")
                return body, (headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
            location = headers.get("location", "")
            if status not in (301, 302, 303, 307, 308) or not location:
                raise CanvasError(f"Canvas's file service answered {status}.", status=status)
            current = urllib.parse.urljoin(current, location)
        raise CanvasError("Canvas redirected the attachment too many times.")

    def _offsite_refused(self, target):
        """Whether Faculty will not fetch from or send a file to this address (a split URL): not https (a loopback test server
        excepted when Canvas itself is one), or with credentials in it, or without a host."""
        loopback = self._scheme == "http" and target.scheme == "http" and target.hostname in ("127.0.0.1", "localhost")
        return bool((target.scheme != "https" and not loopback) or target.username or target.password or not target.hostname)

    @staticmethod
    def _offsite(target, method, timeout, limit, body=None, content_type=None):
        """(status, headers, body) of one request to a host that is not Canvas's: only a User-Agent (and the body's type and length)
        is sent, never a cookie or any header of the session, and no redirect is followed here."""
        headers = {"User-Agent": "Faculty"}
        if body is not None:
            headers["Content-Type"], headers["Content-Length"] = content_type, str(len(body))
        connection = (http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection)(target.netloc, timeout=timeout)
        try:
            connection.request(method, urllib.parse.urlunsplit(("", "", target.path or "/", target.query, "")), body=body, headers=headers)
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read(limit + 1)
        except (OSError, http.client.HTTPException) as error:
            raise CanvasError(f"Canvas's file service could not be reached ({type(error).__name__}).") from None
        finally:
            connection.close()

    @staticmethod
    def _fetch_elsewhere(target, limit):
        """(status, headers, body) of one GET outside Canvas's host: no cookie, no header of the session, no redirect followed here."""
        return Client._offsite(target, "GET", TIMEOUT, limit)

    def write(self, method, path, data=None):
        """A write; the answer's JSON (Canvas answers deletes with the deleted object)."""
        return self.json(method, path, data=data)[0]

    # --- messages to students (canvas/messages.py) -------------------------------------------------------------------------
    def upload_message_file(self, name, media, content):
        """A file for a message (canvas MESSAGES ATTACHMENTS, requirement, 2026-10-09), uploaded as Canvas's inbox paperclip does: into
        the instructor's own "conversation attachments" folder (never a course's files), the only place Canvas takes a message's
        `attachment_ids` from. Canvas's documented three steps (developerdocs "File Uploads", read 2026-10-09): declare it (POST
        /api/v1/users/self/files), send the bytes to the address Canvas answers with (its file service, usually another host: no
        cookie or header of the session goes there, https only, a loopback test server excepted), then confirm on Canvas (a 3XX's
        Location, with the session) unless the file service already answered with the file. Returns the file's Canvas ID.
        CanvasError carries Canvas's status; None when an answer was lost (the file may be in that folder; no message names it)."""
        return str(self._upload("/api/v1/users/self/files", {"parent_folder_path": "conversation attachments", "on_duplicate": "rename"},
                                name, media, content)["id"])

    def _upload(self, declare, fields, name, media, content):
        """Canvas's three-step upload: declared at `declare` (a user's or a course's files) with `fields`, the bytes sent where
        Canvas says, then confirmed. Returns the file as Canvas answers it (a dict with `id`)."""
        status, _, raw = self.request("POST", declare, data={"name": name, "size": len(content), "content_type": media, **fields})
        if not 200 <= status < 300:
            raise refusal(status, raw)
        ticket = answer_json(raw)
        url = ticket.get("upload_url") if isinstance(ticket, dict) else None
        params = ticket.get("upload_params") if isinstance(ticket, dict) else None
        if not isinstance(url, str) or not isinstance(params, dict):
            raise CanvasError("Canvas did not say where to upload the file.", status=status)
        target = urllib.parse.urlsplit(url)
        same = (target.scheme, target.netloc) == (self._scheme, self._netloc)
        if self._offsite_refused(target):
            raise CanvasError("Canvas pointed the upload somewhere Faculty does not send files.", status=status)
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in params.items():  # Canvas signs these: sent exactly as given, before the file
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        safe = name.replace('"', "'").replace("\r", " ").replace("\n", " ")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{ticket.get("file_param") or "file"}"; filename="{safe}"\r\n'
                     f"Content-Type: {media}\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, headers, raw = self._post_elsewhere(target, b"".join(parts), f"multipart/form-data; boundary={boundary}")
        found = answer_json(raw) if 200 <= status < 300 else None
        if not (isinstance(found, dict) and found.get("id") is not None):
            location = headers.get("location", "") or (found.get("location", "") if isinstance(found, dict) else "")
            if not (200 <= status < 400) or not location:
                raise refusal(status, raw) if status >= 400 else CanvasError("Canvas did not confirm the uploaded file.", status=status)
            location = urllib.parse.urljoin(url if not same else self.settings.base + "/", location)
            confirm = urllib.parse.urlsplit(location)
            if (confirm.scheme, confirm.netloc) != (self._scheme, self._netloc):
                raise CanvasError("Canvas pointed the upload's confirmation somewhere else; nothing more was sent.", status=status)
            status, _, raw = self.request("GET", location)
            if not 200 <= status < 300:
                raise refusal(status, raw)
            found = answer_json(raw)
        if not isinstance(found, dict) or found.get("id") is None:
            raise CanvasError("Canvas did not name the uploaded file.", status=status)
        return found

    def _post_elsewhere(self, target, body, content_type):
        """(status, headers, body) of one POST of a file's bytes to Canvas's file service: no cookie or header of the session
        (whatever its host), no redirect followed here."""
        return self._offsite(target, "POST", TIMEOUT * 3, LIMIT, body, content_type)

    def send_message(self, course_id, user_ids, subject, body, group=False, attachment_ids=()):
        """ONE message to many students of a course, as Canvas's inbox sends it (its GraphQL createConversation and
        conversations_controller#create, read 2026-10-01). `group`: ONE conversation every recipient shares (the inbox with "Send an
        individual message to each recipient" unticked: everyone sees who got it and may reply to all; Canvas refuses more than
        SYNC_MAX recipients). Otherwise each recipient gets their OWN conversation (`group_conversation` and `bulk_message`, the box
        ticked: replies come to the instructor alone), sync up to SYNC_MAX recipients (201, the conversations made) and async above
        (202, X-Conversation-Batch-Id, delivered by Canvas in the background), Canvas's own threshold. Returns {status,
        conversations (None when async), batch_id}. CanvasError carries Canvas's status, None when Canvas did not answer (the
        message may or may not have gone)."""
        if group and len(user_ids) > SYNC_MAX:
            raise CanvasError(f"Canvas allows at most {SYNC_MAX} people in one group conversation.", status=400)
        data = {"recipients": [str(u) for u in user_ids], "subject": subject, "body": body, "context_code": f"course_{int(course_id)}"}
        if attachment_ids:
            data["attachment_ids"] = [str(a) for a in attachment_ids]  # files in the sender's "conversation attachments" folder
        if group:
            data["group_conversation"] = True
        else:
            data.update(group_conversation=True, bulk_message=True, mode="sync" if len(user_ids) <= SYNC_MAX else "async")
        status, headers, raw = self.request("POST", "/api/v1/conversations", data=data)
        if not 200 <= status < 300:
            raise refusal(status, raw)
        found = answer_json(raw)
        made = len(found) if isinstance(found, list) and status != 202 else None
        return {"status": status, "conversations": made, "batch_id": str(headers.get("x-conversation-batch-id") or "")}

    def post_announcement(self, course_id, title, message_html):
        """ONE published announcement in a course (POST /api/v1/courses/:id/discussion_topics with `is_announcement`, Canvas's
        documented discussion topics API, as its Announcements page posts one). Returns {status, id, url}. CanvasError carries
        Canvas's status, None when Canvas did not answer (the announcement may or may not have been posted). canvas/announcements.py."""
        status, _, raw = self.request("POST", f"/api/v1/courses/{int(course_id)}/discussion_topics",
                                      data={"title": title, "message": message_html, "is_announcement": True, "published": True})
        if not 200 <= status < 300:
            raise refusal(status, raw)
        found = answer_json(raw)
        found = found if isinstance(found, dict) else {}
        return {"status": status, "id": str(found.get("id") or ""), "url": str(found.get("html_url") or "")}

    def add_message(self, conversation_id, body, recipients):
        """The instructor's reply in an existing conversation (POST /api/v1/conversations/:id/add_message, Canvas's documented
        conversations API), to `recipients` only: the author answered, as Canvas's own Reply sends it (never Reply all). Returns the new
        message's ID ('' when the answer does not name it). CanvasError carries Canvas's status, None when Canvas did not answer
        (the message may or may not have been posted). docs/decisions/mail.md MAIL-CANVAS-POST."""
        status, _, raw = self.request("POST", f"/api/v1/conversations/{int(conversation_id)}/add_message",
                                      data={"body": body, "recipients": [str(r) for r in recipients]})
        if not 200 <= status < 300:
            raise refusal(status, raw)
        found = answer_json(raw)
        messages = found.get("messages") if isinstance(found, dict) else None
        first = messages[0] if isinstance(messages, list) and messages else {}
        return str(first.get("id") or "") if isinstance(first, dict) else ""

    def message_batches(self):
        """{batch id: completion (0..1)} of the instructor's bulk messages Canvas is still delivering (finished ones are not listed)."""
        return {str(b["id"]): float(b.get("completion") or 0) for b in self.listing("/api/v1/conversations/batches")
                if isinstance(b, dict) and b.get("id") is not None}

    def sent_conversations(self, course_id):
        """The instructor's sent conversations in one course: [{id, subject, audience (user IDs), last_authored_at}]. Reads only."""
        return [{"id": str(c["id"]), "subject": str(c.get("subject") or ""), "audience": [str(u) for u in c.get("audience") or []],
                 "last_authored_at": str(c.get("last_authored_message_at") or c.get("last_message_at") or "")}
                for c in self.listing(f"/api/v1/conversations?scope=sent&filter[]=course_{int(course_id)}")
                if isinstance(c, dict) and c.get("id") is not None]


def module_row(m):
    return {"id": str(m["id"]), "name": str(m.get("name") or ""), "position": int(m.get("position") or 0),
            "published": bool(m.get("published", True)), "unlock_at": m.get("unlock_at") or None,
            "require_sequential_progress": bool(m.get("require_sequential_progress"))}


def item_row(i):
    return {"id": str(i["id"]), "module_id": str(i.get("module_id") or ""), "type": str(i.get("type") or ""), "title": str(i.get("title") or ""),
            "indent": int(i.get("indent") or 0), "published": bool(i.get("published", True)), "position": int(i.get("position") or 0),
            "content_id": str(i.get("content_id") or ""), "page_url": str(i.get("page_url") or ""),
            "external_url": str(i.get("external_url") or ""), "new_tab": bool(i.get("new_tab"))}


def file_row(f):
    """What Faculty keeps of a file in Canvas's Files: its ID, the name Canvas gave it, its size and whether it is unpublished."""
    return {"id": str(f["id"]), "name": str(f.get("display_name") or f.get("filename") or ""), "size": int(f.get("size") or 0),
            "locked": bool(f.get("locked"))}


def page_row(p):
    return {"id": str(p["page_id"]), "url": str(p.get("url") or ""), "title": str(p.get("title") or ""), "body": p.get("body") or "",
            "published": bool(p.get("published")), "front_page": bool(p.get("front_page"))}


def _graphql_words(errors):
    """Canvas's words from a GraphQL `errors` list ([{message}] or [{attribute, message}])."""
    words = [str(e.get("message") or "") for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []
    return ("Canvas refused: " + "; ".join(w for w in words if w))[:300] if any(words) else "Canvas refused the request."


def assignment_row(a):
    return {"id": str(a["id"]), "name": str(a.get("name") or ""), "description": a.get("description") or "",
            "points": a.get("points_possible"), "grading_type": str(a.get("grading_type") or "points"),
            "submission_types": sorted(str(t) for t in (a.get("submission_types") or [])), "group_id": str(a.get("assignment_group_id") or ""),
            "due_at": a.get("due_at") or None, "unlock_at": a.get("unlock_at") or None, "lock_at": a.get("lock_at") or None,
            "published": bool(a.get("published", True)), "omit_from_final_grade": bool(a.get("omit_from_final_grade")),
            "post_manually": bool(a.get("post_manually")), "has_submissions": bool(a.get("has_submitted_submissions")),
            "graded": bool(a.get("graded_submissions_exist")), "tool": tool_host(a), "quiz": canvas_quiz(a)}


def tool_host(a):
    """The host of the outside tool an assignment hands its work to (submission type `external_tool`), lower case; "" for one
    Canvas takes itself or whose tool address cannot be read."""
    if "external_tool" not in (a.get("submission_types") or []):
        return ""
    tag = a.get("external_tool_tag_attributes")
    url = tag.get("url") if isinstance(tag, dict) else None
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def graded_by_webassign(host):
    """Whether an assignment's tool host (`tool_host`) is WebAssign's (Cengage's)."""
    return bool(host) and any(host == name or host.endswith(f".{name}") for name in WEBASSIGN_HOSTS)


def canvas_quiz(a):
    """Whether Canvas grades the assignment itself as a quiz (requirement, 2026-10-06: Canvas quizzes are Canvas's, like WebAssign):
    a classic quiz (submission type `online_quiz`) or a New Quiz (Canvas's own quiz tool, an `external_tool` on a "quiz-lti" host)."""
    types = a.get("submission_types") or []
    return "online_quiz" in types or ("external_tool" in types and "quiz-lti" in tool_host(a))


def attachment_row(a):
    """What the communications record keeps of a file attached to a Canvas message or comment."""
    return {"id": str(a.get("id") or ""), "name": str(a.get("display_name") or a.get("filename") or "attachment"),
            "type": str(a.get("content-type") or a.get("content_type") or ""), "size": int(a.get("size") or 0), "url": str(a.get("url") or "")}
