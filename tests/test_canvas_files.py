"""Files for Canvas modules (canvas FILES; canvas/README.md "Files"), offline against the loopback FakeCanvas: a file kept by the
term, the item that names it, the push that puts it in each course's Files before the item, the file published with its item, a
corrected file taking its place, a file somebody else put in Canvas, receipts, and a section's direct edit naming the term's file."""
import copy
import json
import io
import tempfile
import time
import unittest
from unittest import mock

from test_canvas_content import ContentBase, rid
from browser_course_pages import seed_course
from test_storage import HEADERS, temporary_storage

from app import create_app
from assistant import policy
from assistant.followup_effects import Describer
from canvas import content as model
from canvas import web
from course_storage import StorageError, inventory
from rosters import connect

PDF = b"%PDF-1.4 review sheet " + b"x" * 300
FIXED = b"%PDF-1.4 review sheet, corrected " + b"y" * 400


class FilesBase(ContentBase):
    def keep_file(self, content=PDF, filename="Review 2.pdf", name=None, status=200):
        data = {"file": (io.BytesIO(content), filename)}
        if name:
            data["name"] = name
        answer = self.client.post(f"{self.base}/files", data=data, headers=HEADERS, content_type="multipart/form-data")
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def add_item(self, kept, folder="course files/Handouts", published=True, sections=None):
        def change(content):
            content["modules"][1]["items"].append({"type": "File", "title": "Review for Exam 2", "indent": 0, "published": published,
                                                   "sections": sections or [], "content": {"name": f"{folder}/{kept['name']}", "file": kept["id"]}})
        return self.save(change)

    def uploads(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM canvas_content_file_uploads ORDER BY id")]

    def file_item(self, course):
        week = next(m for m in self.fake.modules[course] if m["name"] == "Week 1")
        return next((i for i in week["items"] if i["type"] == "File"), None)


class FilesTests(FilesBase):
    def test_a_file_is_kept_once_and_comes_back_as_a_download(self):
        self.start()
        kept = self.keep_file()
        self.assertEqual((kept["name"], kept["size"], kept["media_type"]), ("Review 2.pdf", len(PDF), "application/pdf"))
        self.assertEqual(self.keep_file()["id"], kept["id"])  # the same file again is the same kept file
        self.assertNotEqual(self.keep_file(FIXED)["id"], kept["id"])
        self.assertEqual([f["name"] for f in self.client.get(self.base).json["files"]], ["Review 2.pdf", "Review 2.pdf"])
        answer = self.client.get(kept["url"])
        self.assertEqual((answer.status_code, answer.data, answer.headers["Cache-Control"]), (200, PDF, "no-store"))
        self.assertIn("attachment", answer.headers["Content-Disposition"])
        self.assertEqual(self.client.get(kept["url"].replace(kept["id"], "cf_0000000000000000")).status_code, 404)
        # A path, an empty file and a nameless one are refused; a folder in the name is dropped.
        self.assertEqual(self.keep_file(filename="..\\notes/../Plan.pdf")["name"], "Plan.pdf")
        self.keep_file(b"", status=400)
        self.keep_file(filename="...", status=400)
        self.assertFalse(any(r["method"] != "GET" for r in self.fake.requests if "/files" in r["path"]))  # nothing reached Canvas

    def test_the_kept_file_is_verified_with_the_terms_storage(self):
        self.start()
        kept = self.keep_file()
        with connect(self.app.config["DATABASE"]) as db:
            row = db.execute("SELECT * FROM canvas_content_files WHERE id=?", (kept["id"],)).fetchone()
            with self.assertRaises(Exception):
                db.execute("DELETE FROM canvas_content_files")
            self.assertEqual(row["term_id"], self.term)
        stored = self.app.extensions["canvas_content"].rosters.path.parent / "terms" / self.term / "data" / row["path"]
        self.assertEqual(stored.read_bytes(), PDF)
        stored.write_bytes(PDF + b"!")  # damaged on disk: never served or sent
        self.assertNotEqual(self.client.get(kept["url"]).status_code, 200)

    def test_an_item_must_name_a_kept_file_at_its_own_name(self):
        self.start()
        kept = self.keep_file()
        view = self.client.get(self.base).json

        def refused(content_field):
            content = copy.deepcopy(view["content"])
            content["modules"][1]["items"].append({"type": "File", "title": "Review", "indent": 0, "published": True, "content": content_field})
            answer = self.client.post(self.base, json={"base_revision": view["revision"], "request_id": rid(), "content": content}, headers=HEADERS)
            self.assertEqual(answer.status_code, 400, answer.json)
            return answer.json["error"]

        self.assertIn("does not keep", refused({"name": "course files/Review 2.pdf", "file": "cf_0000000000000000"}))
        self.assertIn("its file's name", refused({"name": "course files/Other.pdf", "file": kept["id"]}))
        self.assertIn("its file's name", refused({"name": "elsewhere/Review 2.pdf", "file": kept["id"]}))
        self.assertIn("its file's name", refused({"name": "course files/../Review 2.pdf", "file": kept["id"]}))
        saved = self.add_item(kept)
        item = saved["content"]["modules"][1]["items"][-1]
        self.assertEqual(item["content"], {"name": "course files/Handouts/Review 2.pdf", "file": kept["id"]})
        # A save that does not know the kept files (a gradebook column joining the copy) keeps an item's file as it was.
        self.assertEqual(model.normalize(saved["content"], saved["content"])["modules"][1]["items"][-1], item)
        # The upload is the instructor's own (a form, never JSON); the assistant neither sends a file nor reads one back.
        self.assertEqual(self.client.post(f"{self.base}/files", json={"name": "x"}, headers=HEADERS).status_code, 415)
        self.assertEqual(policy.base_rule("canvas.content_file_add").tier, "never")
        self.assertIn("canvas.content_file", policy.FILE_BLOCKED)
        self.assertEqual(Describer._canvas_step({"op": "upload_file", "label": "File Handouts/Review 2.pdf"})[0],
                         "Put the file “Handouts/Review 2.pdf” in Canvas’s Files")

    def test_a_push_puts_the_file_in_each_course_then_its_item_published(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept)
        shown = self.preview()
        for course in (self.a, self.b):
            plan = self.course(shown, course)
            self.assertEqual((plan["counts"].get("upload_file"), plan["counts"].get("create_item"), plan["blocked"]), (1, 1, []))
            self.assertIn({"key": f"file:{kept['id']}", "kind": "file", "label": "File Handouts/Review 2.pdf", "canvas_id": "", "verdict": "new",
                           "write": True, "how": "", "ticked": False}, plan["new"])
        self.assertEqual(self.fake.course_uploads, [])  # a preview writes nothing
        push = self.push()
        self.assertEqual([c["failed"] for c in push["progress"]["courses"].values()], [0, 0])
        self.assertEqual([(u["course"], u["name"], u["folder"], u["content"], u["cookies"]) for u in self.fake.course_uploads],
                         [(c, "Review 2.pdf", "course files/Handouts", PDF, {}) for c in (self.a, self.b)])  # no session at the file service
        for course in (self.a, self.b):
            row = self.fake.file_at(course, "course files/Handouts", "Review 2.pdf")
            item = self.file_item(course)
            self.assertEqual((item["content_id"], item["published"], row["locked"], item["title"]), (row["id"], True, False, "Review for Exam 2"))
        receipts = self.uploads()
        self.assertEqual([(r["status"], r["course_id"], r["canvas_path"], r["replaced"], r["section_id"]) for r in receipts],
                         [("uploaded", c, "course files/Handouts/Review 2.pdf", 0, None) for c in (self.a, self.b)])
        # In step: the next preview has nothing to do, and nothing is uploaded twice.
        again = self.preview()
        self.assertEqual([c["ops"] for c in again["courses"]], [[], []])
        self.assertEqual(len(self.fake.course_uploads), 2)

    def test_an_unpublished_item_keeps_its_file_unpublished_until_it_is_published(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept, published=False)
        self.push(courses=[self.a])
        row, item = self.fake.file_at(self.a, "course files/Handouts", "Review 2.pdf"), self.file_item(self.a)
        self.assertEqual((row["locked"], item["published"]), (True, False))
        lock = next(i for i, w in enumerate(self.fake.writes) if w[1] == f"/api/v1/files/{row['id']}")
        made = next(i for i, w in enumerate(self.fake.writes) if w[0] == "POST" and w[1].endswith("/items") and (w[2] or {}).get("type") == "File")
        self.assertLess(lock, made)  # unpublished as it arrives, before its item exists
        self.assertEqual(self.course(self.preview(courses=[self.a]), self.a)["ops"], [])

        def publish(content):
            content["modules"][1]["items"][-1]["published"] = True
        self.save(publish)
        plan = self.course(self.preview(courses=[self.a]), self.a)
        self.assertEqual([(op["op"], op.get("fields")) for op in plan["ops"]], [("update_item", {"published": True})])
        self.push(courses=[self.a])
        self.assertEqual((row["locked"], self.file_item(self.a)["published"]), (False, True))

    def test_a_file_only_some_sections_get_goes_only_to_their_course(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept, sections=[self.sections[508]])
        shown = self.preview()
        self.assertEqual([self.course(shown, c)["counts"].get("upload_file") for c in (self.a, self.b)], [None, 1])
        self.push()
        self.assertEqual([u["course"] for u in self.fake.course_uploads], [self.b])

    def test_a_corrected_file_takes_the_place_of_the_one_faculty_put_there(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept)
        self.push()
        old = {c: self.fake.file_at(c, "course files/Handouts", "Review 2.pdf")["id"] for c in (self.a, self.b)}
        fixed = self.keep_file(FIXED, filename="Review 2 corrected.pdf", name="Review 2.pdf")
        self.assertEqual(fixed["name"], "Review 2.pdf")

        def replace(content):
            content["modules"][1]["items"][-1]["content"]["file"] = fixed["id"]
        self.save(replace)
        shown = self.preview()
        for course in (self.a, self.b):
            plan = self.course(shown, course)
            self.assertEqual(([op["op"] for op in plan["ops"]], plan["attention"]), (["upload_file"], []))  # Faculty's own file: no question
        self.push()
        for course in (self.a, self.b):
            row = self.fake.file_at(course, "course files/Handouts", "Review 2.pdf")
            self.assertNotEqual(row["id"], old[course])
            self.assertNotIn(old[course], self.fake.files[course])
            self.assertEqual((self.file_item(course)["content_id"], row["size"]), (row["id"], len(FIXED)))  # one item, on the new file
        self.assertEqual([u["replaced"] for u in self.fake.course_uploads[2:]], [old[self.a], old[self.b]])
        self.assertEqual([r["replaced"] for r in self.uploads()], [0, 0, 1, 1])
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])

    def test_a_top_folder_file_canvas_keeps_in_unfiled_is_found_there_and_never_uploaded_again(self):
        """canvas FILES (canvas.tamu.edu, 2026-10-16): an upload naming no folder lands in Canvas's "unfiled" folder; every later
        preview had called the Week 8 Take Home Assignment PDF missing, and each push uploaded it again as "…-1.pdf"."""
        self.start()
        kept = self.keep_file()
        self.add_item(kept, folder="course files")
        push = self.push()
        self.assertEqual([c["failed"] for c in push["progress"]["courses"].values()], [0, 0])
        for course in (self.a, self.b):
            row = self.fake.file_at(course, "course files/unfiled", "Review 2.pdf")
            self.assertEqual(self.file_item(course)["content_id"], row["id"])
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])
        self.assertEqual(len(self.fake.course_uploads), 2)
        # A file of that name in the top folder itself is the one meant.
        top = self.fake.add_file(self.a, "course files", "Review 2.pdf", size=len(PDF))
        self.assertEqual(model.files_by_path({"1": "course files/unfiled/Review 2.pdf", top: "course files/Review 2.pdf"})["course files/Review 2.pdf"], top)

    def test_a_file_canvas_already_has_is_left_alone_or_asked_about(self):
        self.start()
        kept = self.keep_file()
        same = self.fake.add_file(self.a, "course files/Handouts", "Review 2.pdf", size=len(PDF))    # already there, alike
        other = self.fake.add_file(self.b, "course files/Handouts", "Review 2.pdf", size=len(PDF) + 7)  # somebody else's
        self.add_item(kept)
        shown = self.preview()
        first, second = self.course(shown, self.a), self.course(shown, self.b)
        self.assertEqual(([op["op"] for op in first["ops"]], first["attention"]), (["create_item"], []))
        entry, = second["attention"]
        self.assertEqual((entry["key"], entry["verdict"], entry["write"], entry["canvas_id"]), (f"file:{kept['id']}", "differs", False, other))
        self.assertEqual(entry["differences"], [{"field": "size", "faculty": model.size_text(len(PDF)), "canvas": model.size_text(len(PDF) + 7),
                                                 "truncated": False}])
        self.assertEqual([op["op"] for op in second["ops"]], ["create_item"])  # the item shows Canvas's file unless Replace is ticked
        ticked = self.course(self.preview({self.b: {"replace": [entry["key"]]}}), self.b)
        self.assertEqual(sorted(op["op"] for op in ticked["ops"]), ["create_item", "upload_file"])
        self.push({self.b: {"replace": [entry["key"]]}})
        self.assertEqual([(u["course"], u["replaced"]) for u in self.fake.course_uploads], [(self.b, other)])
        self.assertEqual(self.file_item(self.a)["content_id"], same)
        self.assertEqual(self.file_item(self.b)["content_id"], self.fake.file_at(self.b, "course files/Handouts", "Review 2.pdf")["id"])
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])
        # Replaced in Canvas by someone since Faculty put its file there: asked about again, never overwritten silently.
        mine = self.fake.file_at(self.b, "course files/Handouts", "Review 2.pdf")
        del self.fake.files[self.b][mine["id"]]
        theirs = self.fake.add_file(self.b, "course files/Handouts", "Review 2.pdf", size=5)
        self.file_item(self.b)["content_id"] = theirs
        entry, = self.course(self.preview(), self.b)["attention"]
        self.assertEqual((entry["verdict"], entry["write"]), ("edited_in_canvas", False))

    def test_two_files_of_one_name_in_canvas_hold_the_item_back(self):
        self.start()
        kept = self.keep_file()
        for _ in range(2):
            self.fake.add_file(self.a, "course files/Handouts", "Review 2.pdf")
        self.add_item(kept)
        plan = self.course(self.preview(courses=[self.a]), self.a)
        self.assertEqual((plan["ops"], [b["reasons"] for b in plan["blocked"]]), ([], [["more than one file is course files/Handouts/Review 2.pdf"]]))

    def test_a_server_error_upload_is_uncertain_and_its_item_is_not_made(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept)
        self.fake.fail_writes.add(("POST", f"/courses/{self.a}/files"))
        push = self.push(courses=[self.a])
        errors = push["progress"]["courses"][self.a]["errors"]
        self.assertEqual([e["label"] for e in errors], ["File Handouts/Review 2.pdf", "Week 1 › Review for Exam 2"])
        self.assertIn("could not be put in this course's Files", errors[1]["message"])
        self.assertEqual([(r["status"], r["http_status"], r["canvas_file_id"]) for r in self.uploads()], [("uncertain", 500, "")])
        self.assertIsNone(self.file_item(self.a))
        self.fake.fail_writes.clear()
        self.push(courses=[self.a])  # the instructor pushes again: the preview read Canvas, the file was not there
        self.assertEqual([r["status"] for r in self.uploads()], ["uncertain", "uploaded"])
        self.assertIsNotNone(self.file_item(self.a))

    def test_an_upload_canvas_refused_is_failed_with_its_status_and_its_item_is_not_made(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept)

        def refused(client, *args):
            raise web.CanvasError("Canvas refused the file (unprocessable).", status=422)
        with mock.patch.object(web.Client, "_post_elsewhere", refused):
            push = self.push(courses=[self.a])
        self.assertEqual(push["progress"]["courses"][self.a]["failed"], 2)
        self.assertEqual([(r["status"], r["http_status"], r["canvas_file_id"]) for r in self.uploads()], [("failed", 422, "")])
        self.assertIsNone(self.file_item(self.a))
        self.push(courses=[self.a])  # a refused write is a new action for the instructor: the next push uploads it
        self.assertEqual([r["status"] for r in self.uploads()], ["failed", "uploaded"])
        self.assertIsNotNone(self.file_item(self.a))

    def test_an_upload_canvas_never_answered_is_uncertain_and_never_sent_twice(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept)
        sent = web.Client._post_elsewhere

        def lost(client, *args):
            sent(client, *args)  # the file service stores the file; its answer is lost
            raise web.CanvasError("Canvas's file service could not be reached (TimeoutError).")
        with mock.patch.object(web.Client, "_post_elsewhere", lost):
            push = self.push(courses=[self.a])
        self.assertEqual(push["progress"]["courses"][self.a]["failed"], 2)
        self.assertEqual([(r["status"], r["http_status"]) for r in self.uploads()], [("uncertain", None)])
        # The next preview reads Canvas: the file is there, so only its item is made.
        plan = self.course(self.preview(courses=[self.a]), self.a)
        self.assertEqual([op["op"] for op in plan["ops"]], ["create_item"])
        self.push(courses=[self.a])
        self.assertEqual(len(self.fake.course_uploads), 1)

    def test_canvas_keeping_a_file_unpublished_is_said(self):
        self.start()
        kept = self.keep_file()
        self.add_item(kept)
        self.fake.usage_rights.add(self.a)
        push = self.push(courses=[self.a])
        error, = push["progress"]["courses"][self.a]["errors"]
        self.assertIn("kept it unpublished", error["message"])
        self.assertEqual((self.file_item(self.a)["published"], self.fake.file_at(self.a, "course files/Handouts", "Review 2.pdf")["locked"]), (False, True))

    def test_an_existing_item_moves_to_a_file_this_push_brings(self):
        # An item that pointed at one of Canvas's files is given a kept file under another name: the file arrives, then the item
        # is replaced by one pointing at it (what an item points to cannot change in Canvas).
        for course in (self.a, self.b):
            notes = next(k for k, v in self.fake.files[course].items() if v["display_name"] == "notes.pdf")
            self.fake.add_item(course, self.ids[course]["week"], "File", "Notes", content_id=notes)
        self.start()
        kept = self.keep_file()

        def swap(content):
            item = next(i for i in content["modules"][1]["items"] if i["type"] == "File")
            item["content"] = {"name": "course files/Template/Review 2.pdf", "file": kept["id"]}
        self.save(swap)
        plan = self.course(self.preview(courses=[self.a]), self.a)
        self.assertEqual(sorted(op["op"] for op in plan["ops"]), ["replace_item", "upload_file"])
        self.push(courses=[self.a])
        self.assertEqual(self.file_item(self.a)["content_id"], self.fake.file_at(self.a, "course files/Template", "Review 2.pdf")["id"])
        self.assertIsNotNone(self.fake.file_at(self.a, "course files/Template", "notes.pdf"))  # Faculty never deletes a Canvas file


class SectionFilesTests(FilesBase):
    def direct(self, path="", **kwargs):
        route = f"/api/canvas/sections/{self.sections[508]}/content{path}"
        return self.client.post(route, headers=HEADERS, **kwargs) if kwargs else self.client.get(route)

    def load(self):
        answer = self.direct()
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def preview_direct(self, opened, draft, status=200):
        answer = self.direct("/preview", json={"draft": draft, "base": opened["base"], "choices": {}})
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def push_direct(self, opened, draft):
        shown = self.preview_direct(opened, draft)
        push = self.direct("/push", json={"draft": draft, "base": opened["base"], "choices": shown["choices"], "fingerprint": shown["fingerprint"],
                                          "request_id": rid(), "confirmation": "push"}).json
        for _ in range(200):
            push = self.direct(f"/pushes/{push['id']}").json
            if push["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(push["status"], "done", push)
        return push

    def test_a_sections_direct_edit_names_the_terms_kept_file(self):
        kept = self.keep_file()
        opened = self.load()
        draft = copy.deepcopy(opened["content"])
        draft["modules"][1]["items"].append({"type": "File", "title": "Review for Exam 2", "indent": 0, "published": True,
                                             "content": {"name": "course files/Review 2.pdf", "file": kept["id"]}})
        shown = self.preview_direct(opened, draft)
        self.assertEqual(sorted(op["op"] for op in shown["courses"][0]["ops"]), ["create_item", "upload_file"])
        self.push_direct(opened, draft)
        upload, = self.fake.course_uploads
        self.assertEqual((upload["course"], upload["folder"], upload["content"]), (self.b, "course files/unfiled", PDF))  # where Canvas puts it
        row = self.fake.file_at(self.b, "course files/unfiled", "Review 2.pdf")
        self.assertEqual((self.file_item(self.b)["content_id"], self.file_item(self.b)["published"]), (row["id"], True))
        receipt, = self.uploads()
        self.assertEqual((receipt["status"], receipt["section_id"], receipt["course_id"]), ("uploaded", self.sections[508], self.b))
        # Read again: the item is the course's own file item, and nothing is left to do.
        again = self.load()
        item = again["content"]["modules"][1]["items"][-1]
        self.assertEqual(item["content"], {"name": "course files/unfiled/Review 2.pdf"})  # where Canvas keeps it
        self.assertEqual(self.preview_direct(again, again["content"])["courses"][0]["ops"], [])
        # A file Faculty does not keep is refused.
        draft = copy.deepcopy(again["content"])
        draft["modules"][1]["items"][-1]["content"]["file"] = "cf_0000000000000000"
        self.assertIn("does not keep", self.preview_direct(again, draft, status=400)["error"])


class StorageTests(unittest.TestCase):
    def test_the_nightly_backup_checks_a_kept_file_against_its_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = temporary_storage(folder)
            app = create_app(storage=storage)
            seed_course(app.test_client(), sections=[507, 508])
            service = app.extensions["canvas_content"]
            with connect(service.path) as db:
                term = dict(db.execute("SELECT * FROM course_terms").fetchone())
            kept = service.files.add(term["id"], PDF, "Review 2.pdf")
            self.assertEqual(service.files.read(term["id"], kept["id"])[1], PDF)
            with connect(service.path) as db:
                path = db.execute("SELECT path FROM canvas_content_files").fetchone()[0]
                term = dict(db.execute("SELECT * FROM course_terms").fetchone())
            manager = storage.term_storage
            with service.rosters.lock(), service.rosters.files(term, write=True) as root:
                manager.check_source(manager.row(term["id"]), root.parent, inventory(root.parent))
                (root / path).write_bytes(b"damaged")
                with self.assertRaises(StorageError):
                    manager.check_source(manager.row(term["id"]), root.parent, inventory(root.parent))
                (root / path).write_bytes(PDF)
            self.assertEqual(storage.backup_all()["status"], "complete")


class AssistantTests(FilesBase):
    """FROM THE ASSISTANT: a file attached in the chat is placed in a module of the copy at once (a draft), and the push that sends
    it to Canvas waits on an Approve card."""

    def setUp(self):
        super().setUp()
        self.start()
        self.assistant = self.app.extensions["assistant"]
        self.session = self.assistant.open_session("test")
        self.week = next(m for m in self.client.get(f"{self.base}/outline").json["modules"] if m["name"] == "Week 1")

    def attach(self, name="Assessment 1.pdf", content=PDF):
        answer = self.client.post("/api/assistant/uploads", data={"file": [(io.BytesIO(content), name, "application/pdf")]}, headers=HEADERS,
                                  content_type="multipart/form-data")
        self.assertEqual(answer.status_code, 201, answer.json)
        return answer.json["files"][0]

    def tool(self, name, **args):
        text, event = self.assistant.run_tool(self.session, name, args)
        return json.loads(text), event

    def place(self, status=200, **fields):
        body = {"request_id": rid(), "base_revision": self.client.get(f"{self.base}/outline").json["revision"], "module": self.week["id"], **fields}
        answer = self.client.post(f"{self.base}/files/place", json=body, headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json, body

    def revisions(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT revision, actor, request_id FROM canvas_content_revisions ORDER BY revision")]

    def test_the_outline_is_the_copy_without_its_page_bodies(self):
        outline = self.client.get(f"{self.base}/outline").json
        view = self.client.get(self.base).json
        self.assertEqual((outline["revision"], outline["files"], [c["course_id"] for c in outline["courses"]]), (view["revision"], [], [self.a, self.b]))
        self.assertEqual([(m["id"], m["name"]) for m in outline["modules"]], [(m["id"], m["name"]) for m in view["content"]["modules"]])
        self.assertEqual(self.week["items"], [{"type": "Assignment", "title": "Sect. 1.1 Homework", "published": True},
                                              {"type": "Quiz", "title": "Chapter 1 Quiz", "published": True}])
        self.assertNotIn("BLOC 309", json.dumps(outline))  # a page's body
        self.assertFalse(any(r["method"] != "GET" for r in self.fake.requests[-0:] if "/outline" in r["path"]))

    def test_the_assistant_places_a_chat_file_at_once_and_the_push_waits_for_approval(self):
        rule = policy.base_rule("canvas.content_file_place")
        self.assertEqual((rule.tier, rule.category, rule.request_id), ("auto", "Content drafts", True))
        self.assertEqual(policy.base_rule("canvas.content_push").tier, "approve")
        attached = self.attach()
        read, _ = self.tool("faculty_get", path=f"{self.base}/outline")
        before = len(self.fake.requests)
        placed, event = self.tool("faculty_write", method="POST", path=f"{self.base}/files/place", summary="Add the assessment to Week 1",
                                  body={"source": attached["route"], "module": self.week["id"], "base_revision": read["revision"],
                                        "title": "Core Curriculum Assessment 1", "folder": "Assessments"})
        self.assertTrue(event["ok"], placed)
        result = placed["result"]
        self.assertEqual((result["revision"], result["module"]["name"], result["item"]["title"], result["item"]["published"]),
                         (read["revision"] + 1, "Week 1", "Core Curriculum Assessment 1", True))
        self.assertEqual(result["item"]["content"], {"name": "course files/Assessments/Assessment 1.pdf", "file": result["file"]["id"]})
        self.assertEqual(self.revisions()[-1]["actor"], "assistant")
        self.assertEqual(self.client.get(result["file"]["url"]).data, PDF)  # kept by the term: the chat's copy may go
        self.assertEqual((len(self.fake.requests), self.fake.course_uploads, self.actions()["pending"]), (before, [], []))  # a draft: no card, no Canvas
        # The push is the instructor's: a card from the preview, and nothing in Canvas until they approve it.
        asked, _ = self.tool("faculty_write", method="POST", path=f"{self.base}/push", summary="Push the assessment to Section 507",
                             body={"choices": {}, "courses": [self.a]})
        card, = self.actions()["pending"]
        self.assertEqual((asked["outcome"], card["path"], card["endpoint"], self.fake.course_uploads),
                         ("waiting_for_approval", f"{self.base}/push", "canvas.content_push", []))
        self.assertEqual(asked["preview"]["result"]["courses"][0]["counts"], {"create_item": 1, "upload_file": 1})
        self.assertEqual(self.assistant.approve(card["id"], card["seen"])["status"], 200)
        for _ in range(200):
            if not self.app.extensions["canvas_content"]._active:
                break
            time.sleep(0.05)
        row = self.fake.file_at(self.a, "course files/Assessments", "Assessment 1.pdf")
        item = self.file_item(self.a)
        self.assertEqual((item["title"], item["content_id"], item["published"], row["locked"]), ("Core Curriculum Assessment 1", row["id"], True, False))
        self.assertEqual([u["course"] for u in self.fake.course_uploads], [self.a])

    def test_a_request_is_made_once_and_only_a_chat_attachment_or_a_kept_file_is_placed(self):
        attached = self.attach()
        placed, body = self.place(source=attached["route"])
        count = len(self.revisions())
        self.assertEqual((placed["item"]["title"], placed["item"]["content"]["name"]), ("Assessment 1.pdf", "course files/Assessment 1.pdf"))
        # The same request again answers what it did; another change under its ID, or the same file in the same module again, is refused.
        again = self.client.post(f"{self.base}/files/place", json=body, headers=HEADERS)
        self.assertEqual((again.status_code, again.json, len(self.revisions())), (200, placed, count))
        self.assertEqual(self.client.post(f"{self.base}/files/place", json={**body, "title": "Other"}, headers=HEADERS).status_code, 409)
        self.assertIn("already shows this file", self.place(409, source=attached["route"])[0]["error"])
        # A file the term already keeps goes into another module by its ID, unpublished and for one section when asked.
        other = next(m for m in self.client.get(f"{self.base}/outline").json["modules"] if m["name"] == "Course Materials")
        second, _ = self.place(file=placed["file"]["id"], module=other["id"], published=False, sections=[self.sections[508]])
        self.assertEqual((second["module"]["name"], second["item"]["published"], second["item"]["sections"]), ("Course Materials", False, [self.sections[508]]))
        # Refused, with nothing kept or saved: any other Faculty file, a stale read, an unknown module, both or neither file, a bad folder.
        count, files = len(self.revisions()), len(self.client.get(f"{self.base}/outline").json["files"])
        self.assertIn("attached in the chat", self.place(400, source=placed["file"]["url"])[0]["error"])
        self.assertIn("attached in the chat", self.place(400, source="/api/canvas/overview")[0]["error"])
        self.assertIn("saved since it was read", self.place(409, source=self.attach("Late.pdf", FIXED)["route"], base_revision=1)[0]["error"])
        self.assertIn("no module with that ID", self.place(400, source=attached["route"], module="mod_0000000000000000")[0]["error"])
        self.assertIn("Name the file once", self.place(400)[0]["error"])
        self.assertIn("Name the file once", self.place(400, source=attached["route"], file=placed["file"]["id"])[0]["error"])
        self.assertIn("inside Canvas's Files", self.place(400, source=attached["route"], folder="../Private")[0]["error"])
        self.assertEqual((len(self.revisions()), len(self.client.get(f"{self.base}/outline").json["files"])), (count, files))
        self.assertEqual(self.fake.course_uploads, [])

    def actions(self):
        return self.client.get("/api/assistant/actions").json
