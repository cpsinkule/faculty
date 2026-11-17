"""A loopback stand-in of iClicker Cloud for the tests (everything here is synthetic): the JSON API as instructor.iclicker.com uses it
(iclicker/web.py): sign-in at /authproxy/login answering a bearer token and the user's ID, the instructor's courses, a course's
roster, and its polling sessions with every student's points, paged with a `has-more` header. A token it does not know is a 401.
It records every request, so a test can check that Faculty only ever reads."""
from __future__ import annotations

import secrets
import threading

from flask import Flask, jsonify, request
from waitress.server import create_server

EMAIL, PASSWORD = "instructor@tamu.edu", "correct horse battery"
USER = "0f6c1b52-2d1e-4b8e-9a31-7d6a3f2c1e90"


class FakeIClicker:
    def __init__(self):
        self.tokens = set()
        self.logins = 0
        self.courses, self.rosters, self.sessions = [], {}, {}
        self.requests = []
        self.page_size = None  # when set, sessions are paged at this size whatever Faculty asks for
        self.fail = False      # when true, every session listing answers 500 (iClicker failing, for after-class retries)
        self.ids = 0
        self.app = self._app()
        self.server = None

    def _id(self, prefix):
        self.ids += 1
        return f"{prefix}{self.ids:04d}-0000-4000-8000-000000000000"[:36]

    # --- data -------------------------------------------------------------------------------------------------------------
    def add_course(self, name, term="Fall 2026", archived=False, kind="FULL_COURSE"):
        course_id = self._id("c")
        self.courses.append({"enrollmentId": self._id("e"), "institutionName": "Texas A&M University", "activeSiteLicense": True,
                             "courseId": course_id, "courseRoleType": "INSTRUCTOR", "courseType": kind, "name": name, "description": None,
                             "courseIdentifier": None, "joinCode": None, "term": term, "start": "2026-08-24T05:00:00.000Z",
                             "end": "2026-12-11T05:59:59.000Z", "meetingTimes": [], "archived": "2026-05-24T04:59:59.144Z" if archived else None,
                             "activeMeetingId": None})
        self.rosters[course_id], self.sessions[course_id] = [], []
        return course_id

    def add_student(self, course_id, first, last, student_id, email, removed=False):
        user_id = self._id("u")
        self.rosters[course_id].append({"id": user_id, "firstName": first, "lastName": last, "email": email, "studentId": student_id,
                                        "selfRemoved": removed, "blockedUser": False})
        return user_id

    def add_session(self, course_id, name, started, answer=1.0, correct=1.0, questions=2, kind="POLL", ended=True):
        session_id = self._id("a")
        self.sessions[course_id].append({"activityId": session_id, "activityType": kind, "answerPoints": answer, "correctAnswerPoints": correct,
                                         "courseId": course_id, "started": started, "meetingId": self._id("m"), "name": name,
                                         "pollSettings": {"shareQuestionImages": False, "shareResults": True, "participationPoints": answer,
                                                          "participationThreshold": "", "performanceScoringType": "", "pointsPerActivity": 0.0,
                                                          "enableShortAnswerCaseSensitivity": False},
                                         "created": started, "updated": started,
                                         "activityAggregates": {"totalQuestions": questions, "totalPoints": 0.0, "totalSubmitted": 0, "totalUsers": 0},
                                         "ended": started if ended else None, "userActivities": []})
        return session_id

    def score(self, course_id, session_id, user_id, participation, performance, by_enrollment=False):
        session = next(s for s in self.sessions[course_id] if s["activityId"] == session_id)
        entry = {"_id": secrets.token_hex(12), "activityId": session_id, "userId": "someone-else" if by_enrollment else user_id,
                 "enrollmentId": user_id if by_enrollment else self._id("n"), "created": session["started"], "updated": session["started"],
                 "participationPoints": participation, "performancePoints": performance, "questionsAnswered": 2,
                 "totalPoints": participation + performance, "groups": []}
        session["userActivities"].append(entry)

    def adjust(self, course_id, session_id, user_id, participation, performance=None):
        """A score the instructor changed by hand in iClicker's gradebook for a student who did not answer, shaped as the real site
        answers it (Section 507, 2026-10-08): adjusted points (null where not changed) and no original points or total."""
        session = next(s for s in self.sessions[course_id] if s["activityId"] == session_id)
        session["userActivities"].append({"_id": secrets.token_hex(12), "adjustedParticipationPoints": participation,
                                          "adjustedPerformancePoints": performance, "activityId": session_id, "userId": user_id,
                                          "enrollmentId": self._id("n"), "created": session["started"], "updated": session["started"],
                                          "groups": []})

    # --- server -------------------------------------------------------------------------------------------------------------
    def _app(self):
        app = Flask("fake-iclicker")

        def signed_in():
            header = request.headers.get("Authorization", "")
            return header.startswith("Bearer ") and header[7:] in self.tokens and request.headers.get("Reef-Auth-Type") == "oauth"

        @app.before_request
        def record():
            self.requests.append((request.method, request.path))

        @app.post("/authproxy/login")
        def login():
            if request.content_type != "application/vnd.reef.login-proxy-request-v1+json":
                return jsonify(error={"code": 415, "desc": "unsupported"}), 415
            body = request.get_json(force=True, silent=True) or {}
            if body.get("email") != EMAIL or body.get("password") != PASSWORD:
                return jsonify(error={"code": 401, "desc": "Invalid credentials"}), 401
            self.logins += 1
            token = secrets.token_urlsafe(24)
            self.tokens.add(token)
            return jsonify(access_token=token, expires_in=3600, grantType="password", jti=secrets.token_hex(8), refresh_token="r",
                           scope="read", token_type="bearer", userId=USER)

        @app.get("/v1/users/<user>/views/instructor-courses")
        def courses(user):
            if not signed_in():
                return jsonify(error={"code": 401}), 401
            return jsonify(self.courses if user == USER else [])

        @app.get("/trogon/v4/roster/course/<course_id>")
        def roster(course_id):
            if not signed_in():
                return jsonify(error={"code": 401}), 401
            if course_id not in self.rosters:
                return jsonify(error={"code": 404, "desc": "not found"}), 404
            return jsonify(rosterEntries=self.rosters[course_id])

        @app.get("/v2/courses/<course_id>/activities")
        def activities(course_id):
            if not signed_in():
                return jsonify(error={"code": 401}), 401
            if course_id not in self.sessions:
                return jsonify(message="not found"), 404
            if self.fail:
                return jsonify(message="internal error"), 500
            size = self.page_size or int(request.args.get("recordsPerPage", "10"))
            page = int(request.args.get("pageNumber", "1"))
            rows = self.sessions[course_id][(page - 1) * size: page * size]
            answer = jsonify(rows)
            answer.headers["has-more"] = "true" if page * size < len(self.sessions[course_id]) else "false"
            return answer

        return app

    def start(self):
        self.server = create_server(self.app, host="127.0.0.1", port=0)
        threading.Thread(target=self.server.run, daemon=True).start()
        return self

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.effective_port}"

    def stop(self):
        if self.server:
            self.server.close()
