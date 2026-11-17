"""A loopback stand-in of Canvas for the tests (everything here is synthetic): the REST API as Faculty uses it with a browser
session (cookies, `while(1);` before JSON, a renewed `_csrf_token` cookie, X-CSRF-Token on writes, Link-header pagination, 401
for a session it does not know) and, for the sign-in test, a small TAMU sign-in with Duo in front of it."""
from __future__ import annotations

import json
import secrets
import threading
import urllib.parse
from datetime import datetime, timezone

from flask import Flask, make_response, redirect, request
from waitress.server import create_server

NETID, EMAIL, PASSWORD = "instructor", "instructor@tamu.edu", "correct horse battery"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page(body, script=""):
    return f"<!doctype html><html><head><style>body{{margin:0;font:16px sans-serif}}</style></head><body>{body}{script}</body></html>"


class FakeCanvas:
    def __init__(self):
        self.sessions = set()          # canvas_session values Canvas accepts
        self.courses, self.sections, self.enrollments = {}, {}, {}
        self.groups, self.submissions = {}, {}  # course -> [assignment group]; course -> {(user, assignment): submission}
        self.requests, self.writes = [], []
        # Modules and pages: course -> [module {..., items: [...]}], course -> [page], course -> {id: quiz title} ...
        self.modules, self.pages, self.quizzes, self.discussions, self.files, self.folders = {}, {}, {}, {}, {}, {}
        self.ids = 60000
        self.fail_writes = set()
        self.ignore_clears = False  # Canvas taking `"posted_grade": ""` without taking the grade out (Sync with Canvas reads back)
        # (method, path fragment) refused 401 to a session Canvas accepts: Canvas's "user not authorized to perform that action"
        # (a concluded course, a role without the right), which is not a signed-out session.
        self.refuse = set()
        self.progress = {}  # (method, path fragment) answered 500, for failure tests
        self.throttle = 0  # the next bulk grade updates Canvas's throttle refuses (403 "Rate Limit Exceeded"), before acting on them
        # Canvas slow to apply a bulk grade update (live, 2026-10-10: five updates still queued after two minutes, applied later):
        # while `hold_grades`, an update is taken and its Progress stays "queued"; `apply_held_grades()` applies them.
        self.hold_grades, self.held_grades = False, []
        # POSTING GRADES: Canvas's GraphQL post/hide. `posting_state`: the state its Progress reaches ("completed", "failed", or
        # "running" for one that never finishes); `graphql_answer`: (status, body) to answer the next mutations with instead.
        self.posting_state, self.graphql_answer = "completed", None
        # An assignment's posting policy (setAssignmentPostPolicy): `policy_answer` (status, body) answers the next ones instead, and
        # `course_manual` {course: True} makes an assignment created there post by hand (the course's own posting policy).
        self.policy_answer, self.course_manual = None, {}
        # The communications record: inbox conversations, comments on submissions and the files attached to either.
        self.conversations, self.comments, self.blobs, self.marked_read, self.file_requests = {}, {}, {}, [], []
        self.failing = set()  # files whose file service answers 500 (an attachment that cannot be kept now)
        # Bulk messages (canvas/messages.py): every POST /api/v1/conversations as sent, and async batches until finish_batches().
        self.sent_messages, self.batches = [], {}
        self.added_messages = []    # every POST /api/v1/conversations/:id/add_message (replies posted from the Mail page)
        self.message_answer = None  # (status, body) to answer the next sends with instead, for failure tests
        # Announcements (canvas/announcements.py): every POST of a discussion topic with is_announcement, as posted, and
        # `announcement_answer` {course: (status, body)} to answer a course's posts with instead, for failure tests.
        self.announcements, self.announcement_answer = [], {}
        self.hold_messages = None   # a threading.Event a send waits for (Canvas slow to answer)
        # Files for messages (canvas MESSAGES ATTACHMENTS): Canvas's three-step upload into the user's "conversation attachments"
        # folder. `upload_mode`: the file service answers "created" (201 with the file) or "redirect" (a 3XX to confirm on Canvas);
        # `upload_answer` (status, body) answers the next declarations instead, for failure tests.
        self.message_files, self.upload_tickets, self.upload_mode, self.upload_answer = {}, {}, "created", None
        # A course's Files (canvas FILES): `course_uploads` every file uploaded into a course, as sent ({course, id, name, folder,
        # content, replaced}); `usage_rights` {course}: courses whose files stay unpublished until they have usage rights.
        self.course_uploads, self.usage_rights = [], set()
        self.user = {"id": "7001", "name": "Pat Instructor", "short_name": "Pat"}
        self.seen = {"netids": [], "approved": False, "code": "482", "stayed": None, "trusted": None}
        self.app = self._app()
        self.server = None

    # --- data -------------------------------------------------------------------------------------------------------------
    def session(self):
        value = secrets.token_hex(16)
        self.sessions.add(value)
        return {"canvas_session": value, "_csrf_token": urllib.parse.quote(secrets.token_urlsafe(24) + "+/="), "log_session_id": "abc"}

    def add_course(self, name, code, term, state="available", sis=""):
        course_id = str(10000 + len(self.courses) + 1)
        self.courses[course_id] = {"id": course_id, "name": name, "course_code": code, "workflow_state": state, "sis_course_id": sis,
                                   "term": {"name": term}}
        self.sections[course_id], self.enrollments[course_id] = [], []
        return course_id

    def add_section(self, course_id, name):
        section_id = str(20000 + sum(len(v) for v in self.sections.values()) + 1)
        self.sections[course_id].append({"id": section_id, "name": name, "sis_section_id": ""})
        return section_id

    def add_student(self, course_id, section_id, name, sis, state="active"):
        user_id = str(30000 + sum(len(v) for v in self.enrollments.values()) + 1)
        self.enrollments[course_id].append({"user_id": user_id, "course_section_id": section_id, "enrollment_state": state,
                                            "sis_user_id": sis or None, "user": {"id": user_id, "sortable_name": name, "sis_user_id": sis or None}})
        return user_id

    def add_assignment(self, course_id, name, points=10, group="Assignments", due_at=None, grading_type="points", published=True, tool=None,
                       quiz=False, post_manually=False):
        """`tool`: the address of the outside tool the assignment hands its work to (WebAssign's is Cengage's gateway); `quiz`: a
        classic Canvas quiz (Canvas grades it itself); `post_manually`: Canvas's manual posting policy (grades stay hidden)."""
        groups = self.groups.setdefault(course_id, [])
        found = next((g for g in groups if g["name"] == group), None)
        if not found:
            found = {"id": str(40000 + sum(len(v) for v in self.groups.values()) + 1), "name": group, "position": len(groups) + 1, "assignments": []}
            groups.append(found)
        assignment_id = str(50000 + sum(len(g["assignments"]) for v in self.groups.values() for g in v) + 1)
        found["assignments"].append({"id": assignment_id, "name": name, "points_possible": points, "due_at": due_at, "published": published,
                                     "grading_type": grading_type, "workflow_state": "published" if published else "unpublished",
                                     "description": "", "submission_types": ["on_paper"], "assignment_group_id": found["id"],
                                     "unlock_at": None, "lock_at": None, "omit_from_final_grade": False, "post_manually": post_manually})
        if tool:
            found["assignments"][-1].update(submission_types=["external_tool"], external_tool_tag_attributes={"url": tool, "new_tab": False})
        if quiz:
            found["assignments"][-1].update(submission_types=["online_quiz"])
        return assignment_id

    def assignment(self, course_id, assignment_id):
        return next((a for g in self.groups.get(course_id, []) for a in g["assignments"] if a["id"] == assignment_id), None)

    def grade(self, course_id, assignment_id, user_id, score=None, *, excused=False, missing=False, state=None, posted=True, entered=None):
        """`entered`: the grade as entered when Canvas's late penalty makes `score` lower (Canvas's `entered_score`)."""
        self.submissions.setdefault(course_id, {})[(user_id, assignment_id)] = {
            "assignment_id": assignment_id, "user_id": user_id, "score": score, "entered_score": score if entered is None else entered, "excused": excused,
            "missing": missing, "late": False, "workflow_state": state or ("graded" if score is not None or excused else "unsubmitted"),
            "posted_at": "2026-09-20T12:00:00Z" if posted else None}

    def apply_grades(self, course_id, assignment_id, data):
        """One bulk grade update applied: {user: {"posted_grade" | "excuse"}}."""
        row = self.assignment(course_id, assignment_id)
        for user, grade in data.items():
            # A grade Canvas posts by hand stays hidden until posted; one already posted stays posted.
            shown = bool((self.submissions.get(course_id, {}).get((user, assignment_id)) or {}).get("posted_at")) or not row["post_manually"]
            if grade.get("excuse"):
                self.grade(course_id, assignment_id, user, None, excused=True, posted=shown)
            elif grade.get("posted_grade") == "":
                # Taking a grade out (Sync with Canvas): the submission is ungraded again, unless this Canvas ignores it.
                if not self.ignore_clears:
                    self.grade(course_id, assignment_id, user, None, posted=shown)
            else:
                # The strict reading of Canvas (its documentation does not say): a grade on an excused submission takes the excuse away
                # only with `"excuse": false`, which Faculty sends.
                old = self.submissions.get(course_id, {}).get((user, assignment_id)) or {}
                if old.get("excused") and grade.get("excuse") is not False:
                    continue
                self.grade(course_id, assignment_id, user, float(grade["posted_grade"]), posted=shown)

    def apply_held_grades(self):
        """Canvas catches up: every held bulk grade update is applied and its Progress completed."""
        held, self.held_grades = self.held_grades, []
        for progress, course_id, assignment_id, data in held:
            self.apply_grades(course_id, assignment_id, data)
            self.progress[progress]["workflow_state"] = "completed"
        return len(held)

    def next_id(self):
        self.ids += 1
        return str(self.ids)

    def add_module(self, course_id, name, published=True, unlock_at=None, items=()):
        modules = self.modules.setdefault(course_id, [])
        module = {"id": self.next_id(), "name": name, "position": len(modules) + 1, "published": published, "unlock_at": unlock_at,
                  "require_sequential_progress": False, "items": []}
        modules.append(module)
        for item in items:
            self.add_item(course_id, module["id"], **item)
        return module["id"]

    def add_item(self, course_id, module_id, type, title, indent=0, published=True, content_id="", page_url="", external_url="", new_tab=False):
        module = next(m for m in self.modules[course_id] if m["id"] == module_id)
        item = {"id": self.next_id(), "module_id": module_id, "type": type, "title": title, "indent": indent, "published": published,
                "position": len(module["items"]) + 1, "content_id": content_id or None, "page_url": page_url or None,
                "external_url": external_url or None, "new_tab": new_tab}
        module["items"].append(item)
        return item["id"]

    def add_page(self, course_id, title, body="", published=True, front_page=False):
        pages = self.pages.setdefault(course_id, [])
        url = self._slug(course_id, title)
        page = {"page_id": self.next_id(), "url": url, "title": title, "body": body, "published": published, "front_page": front_page}
        pages.append(page)
        return page

    def _slug(self, course_id, title, own=None):
        base = "-".join("".join(c.lower() if c.isalnum() else " " for c in title).split()) or "page"
        taken = {p["url"] for p in self.pages.get(course_id, []) if p is not own}
        url, n = base, 1
        while url in taken:
            n += 1
            url = f"{base}-{n}"
        return url

    def add_file(self, course_id, folder, name, size=1000, locked=False):
        folders = self.folders.setdefault(course_id, {})
        folder_id = folders.setdefault(folder, self.next_id())
        file_id = self.next_id()
        self.files.setdefault(course_id, {})[file_id] = {"id": file_id, "folder_id": folder_id, "display_name": name, "size": size, "locked": locked}
        return file_id

    def file_at(self, course_id, folder, name):
        """The course's file of a folder and name, or None."""
        folder_id = self.folders.get(course_id, {}).get(folder)
        return next((f for f in self.files.get(course_id, {}).values() if f["folder_id"] == folder_id and f["display_name"] == name), None)

    def add_quiz(self, course_id, title):
        quiz_id = self.next_id()
        self.quizzes.setdefault(course_id, {})[quiz_id] = title
        return quiz_id

    # --- the communications record ----------------------------------------------------------------------------------------
    def add_blob(self, name, content, kind="application/pdf"):
        """A file attached to a message or comment: served at /files/<id>/download, which redirects to the file service."""
        blob = self.next_id()
        self.blobs[blob] = {"id": blob, "display_name": name, "content-type": kind, "size": len(content), "content": content}
        return blob

    def add_conversation(self, course_id, subject, participants, archived=False):
        """An inbox conversation of the teacher (fake.user) with `participants` (user IDs), in a course."""
        conversation = self.next_id()
        self.conversations[conversation] = {"id": conversation, "course": course_id, "subject": subject, "archived": archived,
                                            "participants": [self.user["id"], *participants], "messages": []}
        return conversation

    def add_message(self, conversation, author, body, created_at, attachments=(), generated=False):
        found = self.conversations[conversation]
        message = {"id": self.next_id(), "created_at": created_at, "body": body, "author_id": author, "generated": generated,
                   "participating_user_ids": list(found["participants"]), "attachments": list(attachments), "media_comment": None}
        found["messages"].insert(0, message)  # Canvas lists a conversation's messages newest first
        return message["id"]

    def deliver(self, course_id, recipients, subject, body, created_at, attachments=()):
        """One conversation per recipient, as Canvas makes them for a bulk message: [conversation IDs]."""
        made = []
        for user_id in recipients:
            conversation = self.add_conversation(course_id, subject, [user_id])
            self.add_message(conversation, self.user["id"], body, created_at, attachments)
            made.append(conversation)
        return made

    def finish_batches(self):
        """Canvas delivering every async batch it holds."""
        for batch in list(self.batches.values()):
            self.deliver(batch["course"], batch["recipients"], batch["subject"], batch["body"], now(), batch.get("attachments", ()))
        self.batches.clear()

    def add_comment(self, course_id, assignment_id, user_id, author, author_name, comment, created_at, edited_at=None, attachments=()):
        key = (user_id, assignment_id)
        if key not in self.submissions.setdefault(course_id, {}):
            self.grade(course_id, assignment_id, user_id)
        found = {"id": self.next_id(), "author_id": author, "author_name": author_name, "comment": comment, "created_at": created_at,
                 "edited_at": edited_at, "attachments": list(attachments), "media_comment": None}
        self.comments.setdefault((course_id, user_id, assignment_id), []).append(found)
        return found

    def _names(self):
        names = {self.user["id"]: self.user["name"]}
        for rows in self.enrollments.values():
            for e in rows:
                names[e["user_id"]] = e["user"]["sortable_name"]
        return names

    def _renumber(self, rows):
        for n, row in enumerate(rows, 1):
            row["position"] = n

    def _place(self, rows, row, position):
        rows.remove(row)
        rows.insert(max(0, min(int(position) - 1, len(rows))), row)
        self._renumber(rows)

    # --- server -----------------------------------------------------------------------------------------------------------
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

    def _app(self):
        app, fake = Flask("fake_canvas"), self

        def answer(value, status=200, headers=None):
            response = make_response("while(1);" + json.dumps(value), status)
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            for key, item in (headers or {}).items():
                response.headers[key] = item
            response.set_cookie("_csrf_token", urllib.parse.quote(secrets.token_urlsafe(24)))  # Canvas renews it on every answer
            return response

        def paged(rows):
            per_page = int(request.args.get("per_page", 10))
            page_no = int(request.args.get("page", 1))
            chunk = rows[(page_no - 1) * per_page: page_no * per_page]
            headers = {}
            if page_no * per_page < len(rows):
                query = request.args.to_dict(flat=False)
                query["page"] = [str(page_no + 1)]
                headers["Link"] = f'<{request.host_url.rstrip("/")}{request.path}?{urllib.parse.urlencode(query, doseq=True)}>; rel="next"'
            return answer(chunk, headers=headers)

        @app.before_request
        def record():
            fake.requests.append({"method": request.method, "path": request.path, "query": request.query_string.decode(),
                                  "cookies": dict(request.cookies), "csrf": request.headers.get("X-CSRF-Token")})
            if request.path.startswith("/api/") and request.cookies.get("canvas_session") not in fake.sessions:
                # As Canvas answers a request with no user (render_json_unauthorized, add_www_authenticate_header).
                return answer({"errors": [{"message": "user authorization required"}], "status": "unauthenticated"}, 401,
                              {"WWW-Authenticate": 'Bearer realm="canvas-lms"'})
            if any(request.method == method and fragment in request.path for method, fragment in fake.refuse):
                return answer({"errors": [{"message": "user not authorized to perform that action"}], "status": "unauthorized"}, 401)
            if request.path.startswith("/api/") and request.method != "GET":
                expected = urllib.parse.unquote(request.cookies.get("_csrf_token", ""))
                if not expected or request.headers.get("X-CSRF-Token") != expected:
                    return answer({"errors": [{"message": "Invalid authenticity token"}]}, 422)
            return None

        @app.get("/api/v1/users/self")
        def me():
            return answer(fake.user)

        @app.get("/api/v1/courses")
        def courses():
            return paged(list(fake.courses.values()))

        @app.get("/api/v1/courses/<course_id>/sections")
        def sections(course_id):
            return paged(fake.sections.get(course_id, []))

        @app.get("/api/v1/courses/<course_id>/enrollments")
        def enrollments(course_id):
            states = request.args.getlist("state[]") or ["active", "invited"]
            return paged([e for e in fake.enrollments.get(course_id, []) if e["enrollment_state"] in states])

        @app.get("/api/v1/courses/<course_id>/assignment_groups")
        def assignment_groups(course_id):
            return paged(fake.groups.get(course_id, []))

        @app.get("/api/v1/courses/<course_id>/students/submissions")
        def student_submissions(course_id):
            rows = list(fake.submissions.get(course_id, {}).values())
            if "submission_comments" in request.args.getlist("include[]"):
                rows = [{**row, "submission_comments": [{**c, "attachments": [attachment_json(b) for b in c["attachments"]]}
                                                        for c in fake.comments.get((course_id, row["user_id"], row["assignment_id"]), [])]}
                        for row in rows]
            return paged(rows)

        def attachment_json(blob):
            found = fake.blobs[blob]
            return {"id": found["id"], "display_name": found["display_name"], "content-type": found["content-type"], "size": found["size"],
                    "url": f"{request.host_url.rstrip('/')}/files/{found['id']}/download?download_frd=1&verifier=v{found['id']}"}

        def conversation_json(found, messages=False):
            names = fake._names()
            last = max((m["created_at"] for m in found["messages"]), default=None)
            authored = max((m["created_at"] for m in found["messages"] if m["author_id"] == fake.user["id"]), default=None)
            value = {"id": found["id"], "subject": found["subject"], "workflow_state": "archived" if found["archived"] else "read",
                     "last_message_at": last, "last_authored_message_at": authored, "message_count": len(found["messages"]),
                     "audience": [u for u in found["participants"] if u != fake.user["id"]], "context_name": fake.courses[found["course"]]["name"],
                     "participants": [{"id": u, "name": names.get(u, "?"), "full_name": names.get(u, "?")} for u in found["participants"]]}
            if messages:
                value["messages"] = [{**m, "attachments": [attachment_json(b) for b in m["attachments"]]} for m in found["messages"]]
            return value

        @app.get("/api/v1/conversations")
        def conversations():
            wanted = [f.removeprefix("course_") for f in request.args.getlist("filter[]")]
            scope = request.args.get("scope", "")
            rows = [c for c in fake.conversations.values() if not wanted or c["course"] in wanted]
            if scope == "archived":
                rows = [c for c in rows if c["archived"]]
            elif scope == "sent":
                rows = [c for c in rows if any(m["author_id"] == fake.user["id"] for m in c["messages"])]
            else:
                rows = [c for c in rows if not c["archived"] and any(m["author_id"] != fake.user["id"] for m in c["messages"])]
            return paged([conversation_json(c) for c in rows])

        @app.post("/api/v1/users/self/files")
        def message_file_declare():
            # Step 1 of Canvas's upload (developerdocs "File Uploads"): where to send the bytes, signed parameters to send with them.
            data = request.get_json(silent=True) or {}
            if fake.upload_answer:
                status, body = fake.upload_answer
                return answer(body, status)
            if data.get("parent_folder_path") != "conversation attachments" or not data.get("name") or not data.get("size"):
                return answer({"errors": [{"message": "a message's file goes in conversation attachments"}]}, 400)
            ticket = secrets.token_hex(8)
            fake.upload_tickets[ticket] = {"name": data["name"], "size": int(data["size"]), "type": data.get("content_type") or ""}
            return answer({"upload_url": f"{request.host_url}file_service/upload?ticket={ticket}",
                           "upload_params": {"filename": data["name"], "content_type": data.get("content_type") or "", "signature": ticket}})

        @app.post("/api/v1/courses/<course_id>/files")
        def course_file_declare(course_id):
            # Step 1 for a course's Files: the folder path is under the course's files; `on_duplicate` overwrite or rename.
            if (bad := failing()):
                return bad
            data = request.get_json(silent=True) or {}
            if fake.upload_answer:
                status, body = fake.upload_answer
                return answer(body, status)
            if not data.get("name") or not data.get("size") or data.get("on_duplicate") not in ("overwrite", "rename"):
                return answer({"errors": [{"message": "name, size and on_duplicate are required"}]}, 400)
            ticket = secrets.token_hex(8)
            # No folder named: Canvas puts the file in the course's "unfiled" folder (seen on canvas.tamu.edu, 2026-10-16).
            folder = "/".join(["course files", *([p for p in str(data.get("parent_folder_path") or "").split("/") if p] or ["unfiled"])])
            fake.upload_tickets[ticket] = {"name": data["name"], "size": int(data["size"]), "type": data.get("content_type") or "",
                                           "course": course_id, "folder": folder, "on_duplicate": data["on_duplicate"]}
            return answer({"upload_url": f"{request.host_url}file_service/upload?ticket={ticket}",
                           "upload_params": {"filename": data["name"], "content_type": data.get("content_type") or "", "signature": ticket}})

        def course_file_json(row):
            return {"id": row["id"], "folder_id": row["folder_id"], "display_name": row["display_name"], "filename": row["display_name"],
                    "size": row["size"], "locked": row["locked"], "content-type": row.get("type", "")}

        def course_file_store(ticket, content):
            """Step 2 for a course's file: stored in its folder. `overwrite` takes the place of the file of that name (its module
            items follow to the new file, its published state is kept, Attachment#handle_duplicates); `rename` keeps both."""
            course, name = ticket["course"], ticket["name"]
            fake.folders.setdefault(course, {}).setdefault(ticket["folder"], fake.next_id())
            old, replaced = fake.file_at(course, ticket["folder"], name), ""
            locked = course in fake.usage_rights
            if old and ticket["on_duplicate"] == "rename":
                stem, dot, ext = name.rpartition(".")
                name = f"{stem}-1{dot}{ext}" if dot else f"{name}-1"
            file_id = fake.add_file(course, ticket["folder"], name, size=len(content), locked=locked)
            if old and ticket["on_duplicate"] == "overwrite":
                replaced = old["id"]
                fake.files[course][file_id]["locked"] = old["locked"]
                for module in fake.modules.get(course, []):
                    for item in module["items"]:
                        if item["type"] == "File" and item.get("content_id") == old["id"]:
                            item["content_id"] = file_id
                del fake.files[course][old["id"]]
            fake.files[course][file_id]["type"] = ticket["type"]
            fake.course_uploads.append({"course": course, "id": file_id, "name": name, "folder": ticket["folder"], "content": content,
                                        "replaced": replaced, "cookies": dict(request.cookies)})
            return file_id

        @app.post("/file_service/upload")
        def message_file_upload():
            # Step 2, on the file service: no session, every signed parameter exactly as given, the file last.
            ticket = fake.upload_tickets.pop(request.args.get("ticket", ""), None)
            if not ticket or request.form.get("signature") != request.args.get("ticket") or request.form.get("filename") != ticket["name"]:
                return answer({"message": "signature mismatch"}, 400)
            part = request.files.get("file")
            content = part.read() if part else b""
            if len(content) != ticket["size"]:
                return answer({"message": "size mismatch"}, 400)
            if "course" in ticket:
                file_id = course_file_store(ticket, content)
                if fake.upload_mode == "redirect":
                    return redirect(f"/api/v1/files/{file_id}/create_success?uuid={secrets.token_hex(4)}", 302)
                return answer(course_file_json(fake.files[ticket["course"]][file_id]), 201)
            file_id = fake.add_blob(ticket["name"], content, ticket["type"] or "application/octet-stream")
            fake.message_files[file_id] = {"name": ticket["name"], "content": content, "cookies": dict(request.cookies)}
            if fake.upload_mode == "redirect":  # Step 3 on Canvas: the Location confirms it with the session
                return redirect(f"/api/v1/files/{file_id}/create_success?uuid={secrets.token_hex(4)}", 302)
            return answer(attachment_json(file_id), 201)

        @app.get("/api/v1/files/<file_id>/create_success")
        def message_file_confirm(file_id):
            for rows in fake.files.values():
                if file_id in rows:
                    return answer(course_file_json(rows[file_id]))
            return answer(attachment_json(file_id)) if file_id in fake.message_files else answer({"errors": [{"message": "not found"}]}, 404)

        @app.route("/api/v1/files/<file_id>", methods=["GET", "PUT"])
        def file_update(file_id):
            if request.method == "PUT" and (bad := failing()):
                return bad
            fields = request.get_json(silent=True) or {}
            for course, rows in fake.files.items():
                if file_id in rows:
                    if request.method == "GET":
                        return answer(course_file_json(rows[file_id]))
                    fake.writes.append(("PUT", request.path, fields))
                    if "locked" in fields:
                        # A file a course's usage rights hold back stays unpublished (set_publish_state_for_usage_rights).
                        rows[file_id]["locked"] = bool(fields["locked"]) or course in fake.usage_rights
                    return answer(course_file_json(rows[file_id]))
            return answer({"errors": [{"message": "not found"}]}, 404)

        @app.post("/api/v1/conversations")
        def conversation_create():
            # As conversations_controller#create (read 2026-10-01): recipients a teacher may message in the context course, the
            # 100-recipient cap unless bulk_message, async answered 202 [] with X-Conversation-Batch-Id, sync 201 with what was made.
            data = request.get_json(silent=True) or {}
            fake.sent_messages.append(data)
            if fake.hold_messages is not None:
                fake.hold_messages.wait(20)
            if fake.message_answer:
                return answer(fake.message_answer[1], fake.message_answer[0])
            course = str(data.get("context_code", "")).removeprefix("course_")
            students = {e["user_id"] for e in fake.enrollments.get(course, []) if e["enrollment_state"] == "active"}
            recipients = [u for u in data.get("recipients") or [] if u in students]
            if not data.get("body"):
                return answer([{"attribute": "body", "message": "blank"}], 400)
            if not recipients:
                return answer([{"attribute": "recipients", "message": "invalid"}], 400)
            attached = [str(a) for a in data.get("attachment_ids") or []]
            if any(a not in fake.message_files for a in attached):  # only files in the sender's conversation attachments folder
                return answer([{"attribute": "attachment_ids", "message": "invalid"}], 400)
            individually = bool(data.get("group_conversation") and data.get("bulk_message"))
            if not individually and len(recipients) > 100:
                return answer([{"attribute": "recipients", "message": "too many for group conversation"}], 400)
            if data.get("group_conversation") and not individually:  # ONE conversation every recipient shares
                conversation = fake.add_conversation(course, data.get("subject", ""), recipients)
                fake.add_message(conversation, fake.user["id"], data["body"], now(), attached)
                return answer([conversation_json(fake.conversations[conversation])], 201)
            if data.get("mode") == "async":
                batch = str(fake.next_id())
                fake.batches[batch] = {"course": course, "recipients": recipients, "subject": data.get("subject", ""), "body": data["body"],
                                       "attachments": attached}
                return answer([], 202, headers={"X-Conversation-Batch-Id": batch})
            made = fake.deliver(course, recipients, data.get("subject", ""), data["body"], now(), attached)
            return answer([conversation_json(fake.conversations[c]) for c in made], 201)

        @app.post("/api/v1/conversations/<conversation_id>/add_message")
        def conversation_add_message(conversation_id):
            # As the documented conversations API: a message in an existing conversation, to `recipients` among its participants
            # (default all); 404 for a conversation the instructor is not in; answered with the conversation holding the new message.
            data = request.get_json(silent=True) or {}
            fake.added_messages.append({"conversation": conversation_id, **data})
            if fake.message_answer:
                return answer(fake.message_answer[1], fake.message_answer[0])
            found = fake.conversations.get(conversation_id)
            if not found:
                return answer({"errors": [{"message": "not found"}]}, 404)
            if not str(data.get("body") or "").strip():
                return answer([{"attribute": "body", "message": "blank"}], 400)
            if any(r not in found["participants"] for r in data.get("recipients") or []):
                return answer([{"attribute": "recipients", "message": "invalid"}], 400)
            message = fake.add_message(conversation_id, fake.user["id"], data["body"], now())
            value = conversation_json(found)
            value["messages"] = [m for m in found["messages"] if m["id"] == message]
            return answer(value, 201)

        @app.get("/api/v1/conversations/batches")
        def conversation_batches():
            return paged([{"id": b, "subject": v["subject"], "workflow_state": "sending", "completion": 0.5} for b, v in fake.batches.items()])

        @app.get("/api/v1/conversations/<conversation_id>")
        def conversation_one(conversation_id):
            found = fake.conversations.get(conversation_id)
            if not found:
                return answer({"errors": [{"message": "not found"}]}, 404)
            if request.args.get("auto_mark_as_read") != "false":
                fake.marked_read.append(conversation_id)  # what Faculty must never cause
            return answer(conversation_json(found, messages=True))

        # As canvas.tamu.edu answered on 2026-09-30: the download redirects to the file service on ANOTHER host, which redirects
        # again to where the file is stored. Here the other host is `localhost` beside Canvas's 127.0.0.1 (the same server).
        def elsewhere():
            return f"http://localhost:{request.host.rsplit(':', 1)[1]}"

        @app.get("/files/<blob>/download")
        def file_download(blob):
            fake.file_requests.append({"path": request.path, "cookies": dict(request.cookies), "host": request.host})
            if request.cookies.get("canvas_session") not in fake.sessions or blob not in fake.blobs:
                return answer({"errors": [{"message": "unauthorized"}]}, 401)
            return redirect(f"{elsewhere()}/fileservice/{blob}?token=t{blob}", 302)

        @app.get("/fileservice/<blob>")
        def file_service(blob):
            fake.file_requests.append({"path": request.path, "cookies": dict(request.cookies), "host": request.host})
            if blob in fake.failing:
                return make_response("busy", 500)
            if blob not in fake.blobs or request.args.get("token") != f"t{blob}":
                return make_response("no", 403)
            return redirect(f"{elsewhere()}/storage/{blob}?signature=s{blob}", 302)

        @app.get("/storage/<blob>")
        def file_storage(blob):
            fake.file_requests.append({"path": request.path, "cookies": dict(request.cookies), "host": request.host})
            found = fake.blobs.get(blob)
            if not found or request.args.get("signature") != f"s{blob}":
                return make_response("no", 403)
            response = make_response(found["content"])
            response.headers["Content-Type"] = found["content-type"]
            return response

        def item_content(course_id, item):
            """What a Page or Assignment item points to: Canvas publishes the two together (ContentTag#update_asset_workflow_state!)."""
            if item["type"] == "Page":
                return next((p for p in fake.pages.get(course_id, []) if p["url"] == item.get("page_url")), None)
            if item["type"] == "Assignment":
                return fake.assignment(course_id, item.get("content_id"))
            return None

        def set_published(course_id, item, published):
            if item["type"] == "File" and item.get("content_id") in fake.files.get(course_id, {}):
                # A File item and its file go together too: unpublishing the item locks the file, publishing unlocks it unless
                # the course's usage rights hold it back, in which case neither is published.
                row = fake.files[course_id][item["content_id"]]
                row["locked"] = not published or course_id in fake.usage_rights
                published = not row["locked"]
            item["published"] = published
            content = item_content(course_id, item)
            if content is not None:
                content["published"] = published
                if "workflow_state" in content:
                    content["workflow_state"] = "published" if published else "unpublished"

        def item_json(item, course_id):
            shown = {k: v for k, v in item.items() if v is not None or k in ("content_id",)}
            content = item_content(course_id, item)
            if content is not None:
                shown["published"] = bool(content["published"])
            return shown

        def module_json(module):
            return {k: v for k, v in module.items() if k != "items"} | {"items_count": len(module["items"])}

        def failing():
            for method, fragment in fake.fail_writes:
                if request.method == method and fragment in request.path:
                    return answer({"errors": [{"message": "boom"}]}, 500)
            return None

        def module_of(course_id, module_id):
            return next((m for m in fake.modules.get(course_id, []) if m["id"] == module_id), None)

        @app.route("/api/v1/courses/<course_id>/modules", methods=["GET", "POST"])
        def modules(course_id):
            if request.method == "GET":
                return paged([module_json(m) for m in fake.modules.get(course_id, [])])
            if (bad := failing()):
                return bad
            fields = request.get_json()["module"]
            fake.writes.append(("POST", request.path, fields))
            rows = fake.modules.setdefault(course_id, [])
            module = {"id": fake.next_id(), "name": fields["name"], "position": len(rows) + 1, "published": False,
                      "unlock_at": fields.get("unlock_at"), "require_sequential_progress": bool(fields.get("require_sequential_progress")), "items": []}
            rows.append(module)
            if fields.get("position"):
                fake._place(rows, module, fields["position"])
            return answer(module_json(module))

        @app.route("/api/v1/courses/<course_id>/modules/<module_id>", methods=["PUT", "DELETE"])
        def module(course_id, module_id):
            if (bad := failing()):
                return bad
            found = module_of(course_id, module_id)
            if not found:
                return answer({"errors": [{"message": "not found"}]}, 404)
            rows = fake.modules[course_id]
            if request.method == "DELETE":
                fake.writes.append(("DELETE", request.path, None))
                rows.remove(found)
                fake._renumber(rows)
                return answer(module_json(found))
            fields = request.get_json()["module"]
            fake.writes.append(("PUT", request.path, fields))
            for key in ("name", "published", "unlock_at", "require_sequential_progress"):
                if key in fields:
                    found[key] = fields[key]
            if "published" in fields and not fields.get("skip_content_tags"):
                # Canvas publishes (or unpublishes) every item of the module with it, and what they point to (publish_items!).
                for row in found["items"]:
                    set_published(course_id, row, bool(fields["published"]))
            if fields.get("position"):
                fake._place(rows, found, fields["position"])
            return answer(module_json(found))

        @app.route("/api/v1/courses/<course_id>/modules/<module_id>/items", methods=["GET", "POST"])
        def items(course_id, module_id):
            found = module_of(course_id, module_id)
            if not found:
                return answer({"errors": [{"message": "not found"}]}, 404)
            if request.method == "GET":
                return paged([item_json(i, course_id) for i in found["items"]])
            if (bad := failing()):
                return bad
            fields = request.get_json()["module_item"]
            fake.writes.append(("POST", request.path, fields))
            # A new item of a file starts as its file is (ContextModule#add_item: the asset's state): published unless locked.
            row = fake.files.get(course_id, {}).get(fields.get("content_id")) if fields["type"] == "File" else None
            item = {"id": fake.next_id(), "module_id": module_id, "type": fields["type"], "title": fields.get("title", ""),
                    "indent": fields.get("indent", 0), "published": fields["type"] in ("Assignment", "Quiz", "Page") or bool(row and not row["locked"]),
                    "position": len(found["items"]) + 1, "content_id": fields.get("content_id"), "page_url": fields.get("page_url"),
                    "external_url": fields.get("external_url"), "new_tab": bool(fields.get("new_tab"))}
            found["items"].append(item)
            return answer(item_json(item, course_id))

        @app.route("/api/v1/courses/<course_id>/modules/<module_id>/items/<item_id>", methods=["PUT", "DELETE"])
        def item(course_id, module_id, item_id):
            if (bad := failing()):
                return bad
            found = module_of(course_id, module_id)
            row = next((i for i in (found or {}).get("items", []) if i["id"] == item_id), None)
            if not row:
                return answer({"errors": [{"message": "not found"}]}, 404)
            if request.method == "DELETE":
                fake.writes.append(("DELETE", request.path, None))
                found["items"].remove(row)
                fake._renumber(found["items"])
                return answer(item_json(row, course_id))
            fields = request.get_json()["module_item"]
            fake.writes.append(("PUT", request.path, fields))
            for key in ("title", "indent", "external_url", "new_tab"):
                if key in fields:
                    row[key] = fields[key]
            if "published" in fields:
                set_published(course_id, row, bool(fields["published"]))
            if fields.get("module_id") and fields["module_id"] != module_id:
                target = module_of(course_id, fields["module_id"])
                found["items"].remove(row)
                fake._renumber(found["items"])
                row["module_id"] = target["id"]
                target["items"].append(row)
                fake._renumber(target["items"])
            elif fields.get("position"):
                fake._place(found["items"], row, fields["position"])
            return answer(item_json(row, course_id))

        @app.route("/api/v1/courses/<course_id>/pages", methods=["GET", "POST"])
        def pages(course_id):
            rows = fake.pages.setdefault(course_id, [])
            if request.method == "GET":
                include = "body" in request.args.getlist("include[]")
                return paged([p if include else {k: v for k, v in p.items() if k != "body"} for p in rows])
            if (bad := failing()):
                return bad
            fields = request.get_json()["wiki_page"]
            fake.writes.append(("POST", request.path, fields))
            page = fake.add_page(course_id, fields["title"], fields.get("body", ""), fields.get("published", False), False)
            if fields.get("front_page"):
                for other in rows:
                    other["front_page"] = other is page
            return answer(page)

        @app.route("/api/v1/courses/<course_id>/pages/<page_id>", methods=["GET", "PUT", "DELETE"])
        def page_one(course_id, page_id):
            rows = fake.pages.get(course_id, [])
            found = next((p for p in rows if p["page_id"] == page_id or p["url"] == page_id), None)
            if not found:
                return answer({"errors": [{"message": "not found"}]}, 404)
            if request.method == "GET":
                return answer(found)
            if (bad := failing()):
                return bad
            if request.method == "DELETE":
                fake.writes.append(("DELETE", request.path, None))
                rows.remove(found)
                return answer(found)
            fields = request.get_json()["wiki_page"]
            fake.writes.append(("PUT", request.path, fields))
            if "title" in fields and fields["title"] != found["title"]:
                found["title"], found["url"] = fields["title"], fake._slug(course_id, fields["title"], own=found)
            for key in ("body", "published"):
                if key in fields:
                    found[key] = fields[key]
            if fields.get("front_page"):
                for other in rows:
                    other["front_page"] = other is found
            return answer(found)

        @app.route("/api/v1/courses/<course_id>/assignments", methods=["GET", "POST"])
        def assignments(course_id):
            if request.method == "GET":
                return paged([a for g in fake.groups.get(course_id, []) for a in g["assignments"]])
            if (bad := failing()):
                return bad
            fields = request.get_json()["assignment"]
            fake.writes.append(("POST", request.path, fields))
            groups = fake.groups.setdefault(course_id, [])
            group = next((g for g in groups if g["id"] == str(fields.get("assignment_group_id"))), None) or (groups[0] if groups else None)
            if group is None:
                fake.add_assignment(course_id, "placeholder")
                group = fake.groups[course_id][0]
                group["assignments"].clear()
            assignment_id = fake.next_id()
            row = {"id": assignment_id, "name": fields["name"], "points_possible": fields.get("points_possible", 0), "due_at": fields.get("due_at"),
                   "published": bool(fields.get("published")), "grading_type": fields.get("grading_type", "points"),
                   "description": fields.get("description", ""), "submission_types": fields.get("submission_types") or ["none"],
                   "assignment_group_id": group["id"], "unlock_at": fields.get("unlock_at"), "lock_at": fields.get("lock_at"),
                   "omit_from_final_grade": bool(fields.get("omit_from_final_grade")), "post_manually": bool(fake.course_manual.get(course_id))}
            group["assignments"].append(row)
            return answer(row)

        @app.route("/api/v1/courses/<course_id>/assignments/<assignment_id>", methods=["PUT", "DELETE"])
        def assignment_one(course_id, assignment_id):
            if (bad := failing()):
                return bad
            row = fake.assignment(course_id, assignment_id)
            if not row:
                return answer({"errors": [{"message": "not found"}]}, 404)
            if request.method == "DELETE":
                fake.writes.append(("DELETE", request.path, None))
                for g in fake.groups[course_id]:
                    if row in g["assignments"]:
                        g["assignments"].remove(row)
                return answer(row)
            fields = request.get_json()["assignment"]
            fake.writes.append(("PUT", request.path, fields))
            for key in ("name", "points_possible", "due_at", "published", "grading_type", "description", "submission_types", "unlock_at",
                        "lock_at", "omit_from_final_grade"):
                if key in fields:
                    row[key] = fields[key]
            if fields.get("assignment_group_id") and str(fields["assignment_group_id"]) != row["assignment_group_id"]:
                for g in fake.groups[course_id]:
                    if row in g["assignments"]:
                        g["assignments"].remove(row)
                target = next(g for g in fake.groups[course_id] if g["id"] == str(fields["assignment_group_id"]))
                target["assignments"].append(row)
                row["assignment_group_id"] = target["id"]
            return answer(row)

        @app.post("/api/v1/courses/<course_id>/assignment_groups")
        def new_group(course_id):
            fields = request.get_json()
            fake.writes.append(("POST", request.path, fields))
            groups = fake.groups.setdefault(course_id, [])
            group = {"id": fake.next_id(), "name": fields["name"], "position": len(groups) + 1, "assignments": []}
            groups.append(group)
            return answer({k: v for k, v in group.items() if k != "assignments"})

        def current(course_id):
            """Students with an active or invited enrollment: the ones Canvas lists for one assignment and posts for. The course's
            full listing (`students/submissions?student_ids[]=all`) keeps the others' submissions too (live, 2026-10-07: 88
            graded submissions in it, 86 listed for the assignment)."""
            return {e["user_id"] for e in fake.enrollments.get(course_id, []) if e["enrollment_state"] in ("active", "invited")}

        @app.get("/api/v1/courses/<course_id>/assignments/<assignment_id>/submissions")
        def assignment_submissions(course_id, assignment_id):
            listed = current(course_id)
            return paged([v for (user, a), v in fake.submissions.get(course_id, {}).items() if a == assignment_id and user in listed])

        @app.post("/api/v1/courses/<course_id>/assignments/<assignment_id>/submissions/update_grades")
        def update_grades(course_id, assignment_id):
            if (bad := failing()):
                return bad
            if fake.throttle:
                fake.throttle -= 1
                return make_response("403 Forbidden (Rate Limit Exceeded)\n", 403)
            data = request.get_json()["grade_data"]
            fake.writes.append(("POST", request.path, data))
            row = fake.assignment(course_id, assignment_id)
            if not row or not row["published"]:
                return answer({"errors": [{"message": "assignment is not published"}]}, 400)
            progress = fake.next_id()
            if fake.hold_grades:  # taken, applied later (apply_held_grades): its Progress stays queued
                fake.held_grades.append((progress, course_id, assignment_id, data))
                fake.progress[progress] = {"id": progress, "workflow_state": "queued", "completion": 0}
                return answer(fake.progress[progress])
            fake.apply_grades(course_id, assignment_id, data)
            fake.progress[progress] = {"id": progress, "workflow_state": "completed", "completion": 100}
            return answer(fake.progress[progress])

        @app.post("/api/graphql")
        def graphql():
            """Canvas's postAssignmentGrades / hideAssignmentGrades (POSTING GRADES): every submission of the assignment posted, or
            none, and a Progress in `fake.posting_state`."""
            if (bad := failing()):
                return bad
            data = request.get_json() or {}
            query, variables = str(data.get("query") or ""), data.get("variables") or {}
            if "setAssignmentPostPolicy" in query:
                assignment = str(variables.get("assignmentId") or "")
                fake.writes.append(("POST", request.path, {"mutation": "setAssignmentPostPolicy", "assignmentId": assignment,
                                                            "postManually": variables.get("postManually")}))
                if fake.policy_answer:
                    status, body = fake.policy_answer
                    return answer(body, status)
                row = next((a for groups in fake.groups.values() for g in groups for a in g["assignments"] if a["id"] == assignment), None)
                if row is None:
                    return answer({"data": {"setAssignmentPostPolicy": {"postPolicy": None, "errors": [{"attribute": "assignmentId", "message": "not found"}]}}})
                row["post_manually"] = bool(variables.get("postManually"))
                return answer({"data": {"setAssignmentPostPolicy": {"postPolicy": {"postManually": row["post_manually"]}, "errors": None}}})
            name = "postAssignmentGrades" if "postAssignmentGrades" in query else "hideAssignmentGrades" if "hideAssignmentGrades" in query else ""
            if not name:
                return answer({"errors": [{"message": "unknown query"}]})
            fake.writes.append(("POST", request.path, {"mutation": name, "assignmentId": variables.get("assignmentId")}))
            if fake.graphql_answer:
                status, body = fake.graphql_answer
                return answer(body, status)
            assignment = str(variables.get("assignmentId") or "")
            course = next((c for c, groups in fake.groups.items() for g in groups for a in g["assignments"] if a["id"] == assignment), None)
            if course is None:
                return answer({"data": {name: {"progress": None, "errors": [{"attribute": "assignmentId", "message": "not found"}]}}})
            if fake.posting_state != "failed":
                listed = current(course)
                for (user, a), submission in fake.submissions.get(course, {}).items():
                    if a == assignment and user in listed:
                        submission["posted_at"] = "2026-10-06T12:00:00Z" if name == "postAssignmentGrades" else None
            progress = fake.next_id()
            fake.progress[progress] = {"id": progress, "workflow_state": fake.posting_state, "completion": 100, "message": None}
            return answer({"data": {name: {"progress": {"_id": progress, "state": "queued", "message": None}, "errors": None}}})

        @app.get("/api/v1/progress/<progress_id>")
        def progress(progress_id):
            return answer(fake.progress.get(progress_id) or {"errors": [{"message": "not found"}]}, 200 if progress_id in fake.progress else 404)

        @app.get("/api/v1/courses/<course_id>/quizzes")
        def quizzes(course_id):
            return paged([{"id": k, "title": v} for k, v in fake.quizzes.get(course_id, {}).items()])

        @app.post("/api/v1/courses/<course_id>/discussion_topics")
        def discussion_create(course_id):
            # As discussion_topics_controller#create: an announcement when `is_announcement`, answered with the topic.
            data = request.get_json(silent=True) or {}
            if course_id in fake.announcement_answer:
                status, value = fake.announcement_answer[course_id]
                return answer(value, status)
            if course_id not in fake.courses:
                return answer({"errors": [{"message": "The specified resource does not exist."}]}, 404)
            topic = str(fake.next_id())
            fake.announcements.append({"course": course_id, "id": topic, **data})
            fake.discussions.setdefault(course_id, {})[topic] = data.get("title", "")
            return answer({"id": topic, "title": data.get("title", ""), "is_announcement": bool(data.get("is_announcement")),
                           "html_url": f"{request.host_url}courses/{course_id}/discussion_topics/{topic}"}, 200)

        @app.get("/api/v1/courses/<course_id>/discussion_topics")
        def discussions(course_id):
            return paged([{"id": k, "title": v} for k, v in fake.discussions.get(course_id, {}).items()])

        @app.get("/api/v1/courses/<course_id>/folders")
        def folders(course_id):
            return paged([{"id": v, "full_name": k} for k, v in fake.folders.get(course_id, {}).items()])

        @app.get("/api/v1/courses/<course_id>/files")
        def files(course_id):
            return paged(list(fake.files.get(course_id, {}).values()))

        # --- the sign-in, as far as Faculty meets it -------------------------------------------------------------------------
        def signed_in():
            return request.cookies.get("canvas_session") in fake.sessions

        @app.get("/")
        def dashboard():
            return page(f"<h1>Dashboard</h1><p>{fake.user['name']}</p>") if signed_in() else redirect("/login/saml")

        # TAMU's sign-in lives on another host than Canvas (here: localhost beside 127.0.0.1), and hands back a one-time ticket.
        @app.get("/login/saml")
        def saml():
            return redirect(f"http://localhost:{fake.server.effective_port}/ms/login")

        @app.get("/login/saml/callback")
        def callback():
            ticket = request.args.get("ticket", "")
            if ticket not in fake.seen.setdefault("tickets", set()):
                return redirect("/login/saml")
            fake.seen["tickets"].discard(ticket)
            response = redirect("/")
            for name, value in fake.session().items():
                response.set_cookie(name, value)
            return response

        @app.route("/ms/login", methods=["GET", "POST"])
        def email():
            if request.method == "POST":
                fake.seen["netids"].append(request.form.get("loginfmt"))
                if request.form.get("loginfmt") == EMAIL:
                    return redirect("/ms/password")
            return page('<h1>Sign in</h1><form method="post"><input type="email" name="loginfmt" autofocus><button>Next</button></form>')

        @app.route("/ms/password", methods=["GET", "POST"])
        def password():
            if request.method == "POST" and request.form.get("passwd") == PASSWORD:
                return redirect("/duo")
            return page('<h1>Enter password</h1><form method="post"><input type="password" name="passwd" autofocus><button>Sign in</button></form>')

        @app.get("/duo")
        def duo():
            return page(f'<p>Secured by Duo</p><h1>Enter code in Duo Mobile</h1><div>{fake.seen["code"]}</div><p>Verify it\'s you by entering this verification code.</p>',
                        '<script>setInterval(async () => { if ((await (await fetch("/duo/state")).text()) === "approved") location = "/duo/trust"; }, 300);</script>')

        @app.get("/duo/state")
        def duo_state():
            return "approved" if fake.seen["approved"] else "waiting"

        @app.route("/duo/trust", methods=["GET", "POST"])
        def duo_trust():
            if request.method == "POST":
                fake.seen["trusted"] = request.form.get("trust")
                return redirect("/ms/kmsi")
            return page('<p>Secured by Duo</p><h1>Is this your device?</h1><form method="post"><button name="trust" value="yes">Yes, this is my device</button>'
                        '<button name="trust" value="no">No, other people use this device</button></form>')

        @app.route("/ms/kmsi", methods=["GET", "POST"])
        def kmsi():
            if request.method == "POST":
                fake.seen["stayed"] = request.form.get("stay")
                ticket = secrets.token_hex(8)
                fake.seen.setdefault("tickets", set()).add(ticket)
                return redirect(f"http://127.0.0.1:{fake.server.effective_port}/login/saml/callback?ticket={ticket}")
            return page('<h1>Stay signed in?</h1><form method="post"><button name="stay" value="no">No</button><button name="stay" value="yes">Yes</button></form>')

        return app
