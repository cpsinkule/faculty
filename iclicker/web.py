"""Talking to iClicker Cloud (instructor.iclicker.com) with the instructor's saved sign-in (requirement, 2026-09-28).

instructor.iclicker.com is a web app over iClicker's own JSON API; Faculty asks that API what the app itself asks, READ ONLY:
- sign in: POST https://api.iclicker.com/authproxy/login {email, password} (content type
  `application/vnd.reef.login-proxy-request-v1+json`) answers {access_token, userId, expires_in, ...}; every later request
  sends `Authorization: Bearer <token>` and `Reef-Auth-Type: oauth`;
- the instructor's courses: GET https://api.iclicker.com/v1/users/<userId>/views/instructor-courses;
- a course's roster: GET https://api.iclicker.com/trogon/v4/roster/course/<courseId> ({rosterEntries: [...]});
- a course's polling sessions with every student's points: GET
  https://activity-service.iclicker.com/v2/courses/<courseId>/activities?expandChild=userActivities&active=0&pageNumber=N&recordsPerPage=M,
  paged while the answer's `has-more` header says true. A score the instructor changed by hand in iClicker's gradebook is a
  record with `adjustedParticipationPoints` / `adjustedPerformancePoints` (null where not changed) and no `totalPoints`.
Those shapes were read on the real site once, with the instructor's approval, on 2026-09-28 (field names and counts only); the
adjusted fields on 2026-10-08 (Section 507's Class 1 to 3, approved likewise).

The sign-in is the file FACULTY_ICLICKER_CREDENTIALS_FILE names (the drop-in names the Gradescope sign-in, which iClicker shares),
checked like ~/.config/faculty's other secrets and read only when signing in. The token lives in this process's memory until
iClicker's expiry (never on disk, never logged or returned); a refused token signs in once more. Requests go only to iClicker's
two hosts (or one loopback test server), follow no redirect, and Faculty never visits a logout address. Nothing is ever written
to iClicker.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import stat
import threading
import time
import urllib.parse
from dataclasses import dataclass

CREDENTIALS_ENV = "FACULTY_ICLICKER_CREDENTIALS_FILE"
URL_ENV = "FACULTY_ICLICKER_URL"   # a loopback test server that stands in for both of iClicker's hosts
GATEWAY = "https://api.iclicker.com"
ACTIVITY = "https://activity-service.iclicker.com"
TIMEOUT = 60
LIMIT = 64 * 1024 * 1024           # the most one iClicker answer may hold
PAGES = 200                        # the most pages of sessions one course is read in
PAGE_SIZE = 50
REFRESH = 120                      # seconds before iClicker's expiry that a kept token is no longer used
ID = re.compile(r"[A-Za-z0-9-]{1,64}")
# The app's own client tag (ICLICKER/<client>/<build>/<os>/<os version>/<browser>/<device>/<browser version>), naming Faculty.
CLIENT_TAG = "ICLICKER/INSTRUCTOR-WEB/2026-09-03T07:04:53.959Z/Linux/Unknown/Faculty/Web-Browser/1"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Faculty"


class IClickerError(Exception):
    """iClicker could not be read; the message is for the instructor and never holds the password or a token."""


class SignInRefused(IClickerError):
    """iClicker refused the saved sign-in (never tried again until the sign-in file is saved again: `_REFUSED`)."""


class NotSetUp(IClickerError):
    """No usable sign-in file: iClicker is not set up for this Faculty instance."""


def _loopback(value):
    parts = urllib.parse.urlsplit(value)
    try:
        loopback = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if not loopback or parts.scheme not in ("http", "https") or parts.username or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("iClicker may only be reached at its own hosts (or a loopback test server).")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    credentials_file: str = ""
    gateway: str = GATEWAY
    activity: str = ACTIVITY

    @classmethod
    def environment(cls):
        test = os.environ.get(URL_ENV, "").strip()
        base = _loopback(test) if test else None
        return cls(credentials_file=os.environ.get(CREDENTIALS_ENV, "").strip(), gateway=base or GATEWAY, activity=base or ACTIVITY)

    @classmethod
    def testing(cls, credentials_file, url):
        base = _loopback(url)
        return cls(credentials_file=credentials_file, gateway=base, activity=base)

    @property
    def configured(self):
        return bool(self.credentials_file) and os.path.isabs(self.credentials_file)

    def status(self):
        """Whether Faculty may use iClicker: the sign-in file's presence and permissions only (its content is not read)."""
        if not self.configured:
            return {"configured": False, "problem": "iClicker is not set up for this Faculty instance."}
        try:
            info = os.stat(self.credentials_file, follow_symlinks=False)
        except OSError:
            return {"configured": True, "problem": "The saved sign-in file is missing. Run ~/.config/faculty/save-gradescope.py (iClicker shares that sign-in)."}
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return {"configured": True, "problem": "The saved sign-in file must be a file of this account with mode 0600."}
        return {"configured": True, "problem": ""}


def load_credentials(settings):
    """(email, password) from the saved sign-in: an absolute path to a small regular file of this account, readable by no one
    else, holding a JSON object with an email and a password. Nothing of its content is echoed."""
    path = settings.credentials_file
    if not settings.configured:
        raise NotSetUp("iClicker is not set up for this Faculty instance.")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise NotSetUp("The saved sign-in file cannot be opened (missing, or a link). Run ~/.config/faculty/save-gradescope.py.") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
            raise NotSetUp("The saved sign-in file must be a small file of this account with mode 0600.")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(4097)
    finally:
        os.close(descriptor)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k].strip() for k in ("email", "password")):
        raise NotSetUp("The saved sign-in file does not hold an email and a password. Run ~/.config/faculty/save-gradescope.py again.")
    return data["email"].strip(), data["password"]


# Tokens this process holds: {(gateway, email): (token, user id, monotonic expiry)}. Memory only; one sign-in serves every reader.
_TOKENS = {}
# Sign-ins iClicker refused: {(gateway, email): the sign-in file's stamp then}. A refused sign-in is never tried again, by this or
# any parallel reader or later import, until the file is saved again (docs/decisions/iclicker.md; 2026-10-01 audit: one parallel
# read tried a wrong saved password four times, and the password is the Gradescope sign-in's).
_REFUSED = {}
_TOKEN_LOCK = threading.Lock()
REFUSED = ("iClicker refused the saved sign-in (the Gradescope sign-in, which iClicker shares). Check that you can sign in at "
           "instructor.iclicker.com with it, then run ~/.config/faculty/save-gradescope.py; Faculty tries again only once it is saved again.")


def forget_tokens():
    with _TOKEN_LOCK:
        _TOKENS.clear()
        _REFUSED.clear()


def _file_stamp(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_ino, info.st_mtime_ns, info.st_size


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value, longest=300):
    return value.strip()[:longest] if isinstance(value, str) else ""


class Client:
    """One use of the saved sign-in. Signs in when this process holds no usable token."""

    def __init__(self, settings):
        self.settings = settings
        self._stamp = _file_stamp(settings.credentials_file)  # before reading: a refusal is never pinned on a newer file
        self.email, self._password = load_credentials(settings)
        self.token = self.user_id = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._password = None
        return False

    # --- plumbing ---------------------------------------------------------------------------------------------------------
    def _send(self, method, url, body=None, content_type="application/json", token=None):
        parts = urllib.parse.urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" not in (self.settings.gateway, self.settings.activity):
            raise IClickerError("Faculty only talks to iClicker's own hosts; nothing was sent.")
        headers = {"Accept": "application/json", "Reef-Auth-Type": "oauth", "Client-Tag": CLIENT_TAG, "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), content_type
        if token:
            headers["Authorization"] = "Bearer " + token
        connection = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(parts.netloc, timeout=TIMEOUT)
        path = urllib.parse.urlunsplit(("", "", parts.path, parts.query, ""))
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read(LIMIT + 1)
            status, answer_headers = response.status, {k.lower(): v for k, v in response.getheaders()}
        except (OSError, http.client.HTTPException) as error:
            raise IClickerError(f"iClicker could not be reached ({type(error).__name__}).") from None
        finally:
            connection.close()
        if len(raw) > LIMIT:
            raise IClickerError("iClicker answered with more than Faculty reads at once.")
        return status, answer_headers, raw

    @staticmethod
    def _json(raw):
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise IClickerError("iClicker answered with something Faculty could not read.") from None

    def sign_in(self, fresh=False):
        """A token for this sign-in: the one this process holds, else a new sign-in (a refused one is SignInRefused). `fresh`: this
        client's token was refused; a newer one another reader got meanwhile is used instead of signing in again."""
        key = (self.settings.gateway, self.email.casefold())
        with _TOKEN_LOCK:
            held = _TOKENS.get(key)
            if held and held[2] > time.monotonic() and (not fresh or held[0] != self.token):
                self.token, self.user_id = held[0], held[1]
                return
            if key in _REFUSED and _REFUSED[key] == self._stamp:
                raise SignInRefused(REFUSED)  # iClicker is not asked again
            _TOKENS.pop(key, None)
            status, _, raw = self._send("POST", f"{self.settings.gateway}/authproxy/login", {"email": self.email, "password": self._password},
                                        content_type="application/vnd.reef.login-proxy-request-v1+json")
            if status in (400, 401, 403, 404, 422):
                _REFUSED[key] = self._stamp
                raise SignInRefused(REFUSED)
            if status != 200:
                raise IClickerError(f"iClicker's sign-in answered {status}.")
            data = self._json(raw)
            token, user_id = data.get("access_token") if isinstance(data, dict) else None, data.get("userId") if isinstance(data, dict) else None
            if not isinstance(token, str) or not token or not isinstance(user_id, str) or not ID.fullmatch(user_id):
                raise IClickerError("iClicker's sign-in answered without a session Faculty could use.")
            lifetime = _number(data.get("expires_in")) or 600
            _TOKENS[key] = (token, user_id, time.monotonic() + max(60, lifetime - REFRESH))
            _REFUSED.pop(key, None)
            self.token, self.user_id = token, user_id

    def get(self, url):
        """(JSON, headers) of one GET; a refused token signs in once more and asks again."""
        if not self.token:
            self.sign_in()
        for attempt in (0, 1):
            status, headers, raw = self._send("GET", url, token=self.token)
            if status == 401 and attempt == 0:
                self.sign_in(fresh=True)
                continue
            if status in (301, 302, 303, 307, 308):
                raise IClickerError("iClicker pointed Faculty somewhere else; it was not followed.")
            if status == 404:
                raise IClickerError("iClicker does not know that course (or no longer lists it for you).")
            if status != 200:
                raise IClickerError(f"iClicker answered {status}.")
            return self._json(raw), headers

    @staticmethod
    def _id(value, what="course"):
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise IClickerError(f"That is not an iClicker {what}.")
        return value

    # --- reads --------------------------------------------------------------------------------------------------------------
    def courses(self):
        """The instructor's iClicker courses (events left out): [{id, name, term, start, end, archived}]."""
        if not self.token:
            self.sign_in()
        data, _ = self.get(f"{self.settings.gateway}/v1/users/{self._id(self.user_id, 'user')}/views/instructor-courses")
        if not isinstance(data, list):
            raise IClickerError("iClicker answered without a course list Faculty could read.")
        found = []
        for row in data:
            if not isinstance(row, dict) or row.get("courseType") == "EVENT" or not isinstance(row.get("courseId"), str) or not ID.fullmatch(row["courseId"]):
                continue
            found.append({"id": row["courseId"], "name": _text(row.get("name"), 200) or row["courseId"], "term": _text(row.get("term"), 100),
                          "start": _text(row.get("start"), 40)[:10], "end": _text(row.get("end"), 40)[:10], "archived": bool(row.get("archived"))})
        return found

    def roster(self, course_id):
        """[{id, first, last, email, student_id, removed, blocked}] of the course's roster."""
        data, _ = self.get(f"{self.settings.gateway}/trogon/v4/roster/course/{self._id(course_id)}")
        entries = data.get("rosterEntries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise IClickerError("iClicker answered without a roster Faculty could read.")
        found = []
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not ID.fullmatch(row["id"]):
                continue
            found.append({"id": row["id"], "first": _text(row.get("firstName"), 100), "last": _text(row.get("lastName"), 100),
                          "email": _text(row.get("email"), 254), "student_id": _text(row.get("studentId"), 64),
                          "removed": bool(row.get("selfRemoved")), "blocked": bool(row.get("blockedUser"))})
        return found

    def sessions(self, course_id):
        """Every polling session (activity) of the course with each student's points, in iClicker's order:
        [{id, type, name, started, ended, questions, answer_points, correct_answer_points, participation_points,
          points_per_activity, threshold, scoring, users: [{user_id, enrollment_id, total, participation, performance,
          adjusted_participation, adjusted_performance, answered}]}]."""
        course_id = self._id(course_id)
        found, seen = [], set()
        for page in range(1, PAGES + 1):
            data, headers = self.get(f"{self.settings.activity}/v2/courses/{course_id}/activities?expandChild=userActivities&active=0"
                                     f"&pageNumber={page}&recordsPerPage={PAGE_SIZE}")
            if not isinstance(data, list):
                raise IClickerError("iClicker answered without a session list Faculty could read.")
            rows = data
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("activityId"), str) or not ID.fullmatch(row["activityId"]) or row["activityId"] in seen:
                    continue
                seen.add(row["activityId"])
                settings = row.get("pollSettings") if isinstance(row.get("pollSettings"), dict) else {}
                aggregates = row.get("activityAggregates") if isinstance(row.get("activityAggregates"), dict) else {}
                users = []
                for u in row.get("userActivities") if isinstance(row.get("userActivities"), list) else []:
                    if not isinstance(u, dict):
                        continue
                    users.append({"user_id": u.get("userId") if isinstance(u.get("userId"), str) else "",
                                  "enrollment_id": u.get("enrollmentId") if isinstance(u.get("enrollmentId"), str) else "",
                                  "total": _number(u.get("totalPoints")), "participation": _number(u.get("participationPoints")),
                                  "performance": _number(u.get("performancePoints")),
                                  "adjusted_participation": _number(u.get("adjustedParticipationPoints")),
                                  "adjusted_performance": _number(u.get("adjustedPerformancePoints")), "answered": _number(u.get("questionsAnswered"))})
                found.append({"id": row["activityId"], "type": _text(row.get("activityType"), 40), "name": _text(row.get("name"), 200),
                              "started": _text(row.get("started"), 40), "ended": _text(row.get("ended"), 40),
                              "questions": _number(aggregates.get("totalQuestions")),
                              "answer_points": _number(row.get("answerPoints")), "correct_answer_points": _number(row.get("correctAnswerPoints")),
                              "participation_points": _number(settings.get("participationPoints")),
                              "points_per_activity": _number(settings.get("pointsPerActivity")),
                              "threshold": _text(settings.get("participationThreshold"), 40), "scoring": _text(settings.get("performanceScoringType"), 40),
                              "users": users})
            more = str(headers.get("has-more", "")).strip().lower() == "true"
            if not rows and more:
                raise IClickerError("iClicker returned an empty session page while offering more sessions.")
            if not more:
                return found
        raise IClickerError("iClicker kept offering more sessions than Faculty reads for one course.")
