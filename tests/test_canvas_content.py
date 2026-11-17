"""Canvas modules and pages kept in step across a term's sections (canvas/README.md "Modules and pages"), offline against the
loopback FakeCanvas: starting Faculty's copy from one course, matching the others, rewriting page links per course, the preview's
verdicts and choices, the approved push and what it records."""
import threading
import time
import uuid
from unittest import mock

from test_canvas import Seeded
from test_storage import HEADERS

from canvas import content as model
from rosters import connect


def rid():
    return f"test-{uuid.uuid4().hex[:12]}"


class ContentBase(Seeded):
    def setUp(self):
        super().setUp()
        self.keep()
        fake = self.fake
        self.a = fake.add_course("26 FALL MATH 140 507", "MATH 140 507", "Fall 2026")
        self.b = fake.add_course("26 FALL MATH 140 508", "MATH 140 508", "Fall 2026")
        for number, course in ((507, self.a), (508, self.b)):
            answer = self.client.post(f"/api/canvas/sections/{self.sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS)
            self.assertEqual(answer.status_code, 200, answer.json)
        # The department template, copied into both courses (their own IDs), then edited apart.
        self.ids = {}
        for course in (self.a, self.b):
            banner = fake.add_file(course, "course files/Template", "banner.png")
            fake.add_file(course, "course files/Template", "notes.pdf")
            quiz = fake.add_quiz(course, "Chapter 1 Quiz")
            hw = fake.add_assignment(course, "Sect. 1.1 Homework")
            office = fake.add_page(course, "Office Hours", "<p>BLOC 309</p>")
            fake.add_page(course, "Home", f'<p><img src="/courses/{course}/files/{banner}/preview"></p>'
                                          f'<p><a href="/courses/{course}/pages/{office["url"]}">Office hours</a></p>', front_page=True)
            first = fake.add_module(course, "Course Materials", items=[
                {"type": "SubHeader", "title": "Syllabus"},
                {"type": "Page", "title": "Office Hours", "page_url": office["url"], "indent": 1},
                {"type": "ExternalUrl", "title": "Textbook", "external_url": "https://example.edu/book", "new_tab": True}])
            week = fake.add_module(course, "Week 1", unlock_at="2026-08-23T11:00:00Z", items=[
                {"type": "Assignment", "title": "Sect. 1.1 Homework", "content_id": hw},
                {"type": "Quiz", "title": "Chapter 1 Quiz", "content_id": quiz}])
            self.ids[course] = {"banner": banner, "office": office, "first": first, "week": week, "quiz": quiz, "hw": hw}
        with connect(self.app.config["DATABASE"]) as db:
            self.term = db.execute("SELECT term_id FROM course_sections WHERE id=?", (self.sections[507],)).fetchone()[0]
        self.base = f"/api/canvas/content/{self.term}"

    def start(self, course=None):
        answer = self.client.post(f"{self.base}/start", json={"course_id": course or self.a, "request_id": rid(), "base_revision": None}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def save(self, change):
        view = self.client.get(self.base).json
        content = change(view["content"]) or view["content"]
        answer = self.client.post(self.base, json={"base_revision": view["revision"], "request_id": rid(), "content": content}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def preview(self, choices=None, courses=None):
        body = {"choices": choices or {}}
        if courses is not None:
            body["courses"] = courses
        answer = self.client.post(f"{self.base}/preview", json=body, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def course(self, preview, course):
        return next(c for c in preview["courses"] if c["course_id"] == course)

    def push(self, choices=None, courses=None):
        shown = self.preview(choices, courses)
        body = {"fingerprint": shown["fingerprint"], "choices": shown["choices"], "request_id": rid(), "confirmation": "push"}
        if courses is not None:
            body["courses"] = courses
        answer = self.client.post(f"{self.base}/push", json=body, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        push = answer.json
        for _ in range(200):
            push = self.client.get(f"{self.base}/pushes/{push['id']}").json
            if push["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(push["status"], "done", push)
        return push


class ContentTests(ContentBase):
    def test_conflict_shows_exact_fields_and_rechecks_unselected_remote_edits(self):
        self.start()
        office = next(p for p in self.fake.pages[self.a] if p['title'] == 'Office Hours')
        office['body'] = '<p>Changed remotely</p>'
        shown = self.preview(courses=[self.a])
        entry = next(e for e in self.course(shown, self.a)['attention'] if e['label'] == 'Office Hours')
        difference = next(d for d in entry['differences'] if d['field'] == 'body')
        self.assertIn('Changed remotely', difference['canvas'])
        self.assertIn('BLOC 309', difference['faculty'])
        self.assertFalse(difference['truncated'])
        office['body'] = '<p>Changed remotely again</p>'
        updated = self.preview(courses=[self.a])
        self.assertNotEqual(shown['fingerprint'], updated['fingerprint'])

    def test_the_copy_starts_from_one_course_and_reading_it_never_contacts_canvas(self):
        empty = self.client.get(self.base).json
        self.assertIsNone(empty["content"])
        self.assertEqual([c["course_id"] for c in empty["courses"]], [self.a, self.b])
        self.fake.requests.clear()
        started = self.start()
        content = started["content"]
        self.assertEqual([m["name"] for m in content["modules"]], ["Course Materials", "Week 1"])
        self.assertEqual([i["type"] for i in content["modules"][0]["items"]], ["SubHeader", "Page", "ExternalUrl"])
        week = content["modules"][1]
        self.assertEqual(week["unlock_at"], "2026-08-23T11:00:00Z")
        homework = content["assignments"][0]["id"]
        self.assertEqual([i["content"] for i in week["items"]], [{"assignment": homework}, {"name": "Chapter 1 Quiz"}])
        self.assertTrue(any(r["path"].startswith(f"/api/v1/courses/{self.a}/") for r in self.fake.requests))
        self.assertFalse(any(r["method"] != "GET" for r in self.fake.requests))  # starting reads Canvas, never writes
        self.fake.requests.clear()
        self.client.get(self.base)
        self.assertEqual(self.fake.requests, [])
        # Starting again needs the latest revision as base.
        again = self.client.post(f"{self.base}/start", json={"course_id": self.a, "request_id": rid(), "base_revision": None}, headers=HEADERS)
        self.assertEqual(again.status_code, 409)

    def test_the_other_course_is_matched_by_name_and_page_links_are_rewritten_for_it(self):
        self.start()
        shown = self.preview()
        a, b = self.course(shown, self.a), self.course(shown, self.b)
        self.assertEqual(a["ops"], [])  # the course it came from is already Faculty's copy
        self.assertEqual(b["ops"], [], b)  # the same template: every object matched by name and alike once its links point here
        self.assertEqual(b["matched_by_name"], [])  # recorded when the copy was started: alike there
        with connect(self.app.config["DATABASE"]) as db:
            recorded = db.execute("SELECT COUNT(*) FROM canvas_content_links WHERE course_id=? AND source='matched'", (self.b,)).fetchone()[0]
        self.assertEqual(recorded, 10)  # 2 pages, 2 modules, 5 items, 1 assignment
        self.assertEqual(self.fake.writes, [])
        # Change the home page in Faculty: B receives it with ITS banner and ITS office hours page.
        def edit(content):
            home = next(p for p in content["pages"] if p["title"] == "Home")
            home["body"] += "<p>Welcome!</p>"
        self.save(edit)
        self.push()
        home_b = next(p for p in self.fake.pages[self.b] if p["title"] == "Home")
        self.assertIn(f"/courses/{self.b}/files/{self.ids[self.b]['banner']}/preview", home_b["body"])
        self.assertIn(f"/courses/{self.b}/pages/{self.ids[self.b]['office']['url']}", home_b["body"])
        self.assertNotIn(f"/courses/{self.a}/", home_b["body"])
        self.assertIn("Welcome!", home_b["body"])
        home_a = next(p for p in self.fake.pages[self.a] if p["title"] == "Home")
        self.assertIn(f"/courses/{self.a}/files/{self.ids[self.a]['banner']}", home_a["body"])
        # Pushed and read back: nothing is left to do.
        self.fake.writes.clear()
        again = self.preview()
        self.assertEqual([c["ops"] for c in again["courses"]], [[], []])

    def test_edits_reach_every_course_in_faculty_order_and_new_things_are_made(self):
        self.start()
        def edit(content):
            materials, week = content["modules"]
            week["name"] = "Week 1 — Aug 24–30"
            week["published"] = False
            materials["items"][0]["title"] = "Syllabus (read first)"
            content["pages"].append({"ref": "new-page", "title": "Exam I Information", "body": "<p>Thursday 7:05 PM</p>", "published": True})
            content["modules"].insert(0, {"name": "Start Here", "items": [{"type": "Page", "title": "Exam I Information", "page": "new-page"},
                                                                         {"type": "ExternalUrl", "title": "Calculator rules", "external_url": "https://example.edu/calc"}]})
        self.save(edit)
        shown = self.preview()
        for course in (self.a, self.b):
            counts = self.course(shown, course)["counts"]
            self.assertEqual((counts.get("create_page"), counts.get("create_module"), counts.get("create_item"), counts.get("update_module"),
                              counts.get("update_item")), (1, 1, 2, 1, 1), counts)
        push = self.push()
        self.assertEqual(sum(c["failed"] for c in push["progress"]["courses"].values()), 0, push)
        for course in (self.a, self.b):
            modules = self.fake.modules[course]
            self.assertEqual([m["name"] for m in modules], ["Start Here", "Course Materials", "Week 1 — Aug 24–30"])
            self.assertFalse(modules[2]["published"])
            self.assertTrue(modules[0]["published"])
            self.assertEqual([i["title"] for i in modules[0]["items"]], ["Exam I Information", "Calculator rules"])
            page = next(p for p in self.fake.pages[course] if p["title"] == "Exam I Information")
            self.assertEqual(modules[0]["items"][0]["page_url"], page["url"])
            self.assertEqual(modules[1]["items"][0]["title"], "Syllabus (read first)")
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])

    def test_an_edit_made_in_canvas_is_never_replaced_unless_ticked(self):
        self.start()
        self.push()
        office_b = next(p for p in self.fake.pages[self.b] if p["title"] == "Office Hours")
        office_b["body"] = "<p>BLOC 309 and by appointment</p>"  # someone edits section 508's page in Canvas
        shown = self.course(self.preview(), self.b)
        self.assertEqual(shown["ops"], [])
        self.assertEqual([(e["label"], e["verdict"]) for e in shown["attention"]], [("Office Hours", "edited_in_canvas")])
        def edit(content):
            next(p for p in content["pages"] if p["title"] == "Office Hours")["body"] = "<p>BLOC 319</p>"
        self.save(edit)
        shown = self.preview()
        self.assertEqual([op["op"] for op in self.course(shown, self.a)["ops"]], ["update_page"])
        b = self.course(shown, self.b)
        self.assertEqual(b["ops"], [])
        self.assertEqual(b["attention"][0]["verdict"], "conflict")
        key = b["attention"][0]["key"]
        self.push({self.b: {"replace": [key]}})
        self.assertEqual(office_b["body"], "<p>BLOC 319</p>")

    def test_the_first_push_to_a_course_that_differs_asks_before_replacing(self):
        office_b = next(p for p in self.fake.pages[self.b] if p["title"] == "Office Hours")
        office_b["body"] = "<p>Section 508 hours</p>"
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertEqual([(e["label"], e["verdict"]) for e in b["attention"]], [("Office Hours", "differs")])
        self.assertEqual(b["ops"], [])
        self.push({self.b: {"replace": "all"}})
        self.assertEqual(office_b["body"], "<p>BLOC 309</p>")

    def test_each_course_embeds_its_own_copy_of_a_video_and_title_end_spaces_are_alike(self):
        videos = {course: self.fake.add_file(course, "course files", "BreakEvenPoint.mp4") for course in (self.a, self.b)}
        frame = ('<p><iframe title="Video player for BreakEvenPoint.mp4" data-media-type="video" src="https://canvas.tamu.edu/'
                 'media_attachments_iframe/{}?embedded=true&amp;type=video" loading="lazy"{}></iframe></p>')
        self.fake.add_page(self.a, "Break Even Point", frame.format(videos[self.a], ' data-media-id="m-507"'))
        self.fake.add_page(self.b, "Break Even Point", frame.format(videos[self.b], ""))
        next(i for i in self.fake.modules[self.b][0]["items"] if i["title"] == "Textbook")["title"] = "Textbook "  # a title ending in a space
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertEqual((b["attention"], b["ops"]), ([], []))  # the same page and the same title
        def edit(content):
            next(p for p in content["pages"] if p["title"] == "Break Even Point")["body"] += "<p>Watch it twice.</p>"
        self.save(edit)
        self.push()
        page_b = next(p for p in self.fake.pages[self.b] if p["title"] == "Break Even Point")["body"]
        self.assertIn(f"media_attachments_iframe/{videos[self.b]}?", page_b)  # 508's own copy of the video
        self.assertNotIn(f"media_attachments_iframe/{videos[self.a]}?", page_b)
        self.assertNotIn("m-507", page_b)  # 507's media object is not 508's
        page_a = next(p for p in self.fake.pages[self.a] if p["title"] == "Break Even Point")["body"]
        self.assertIn(f"media_attachments_iframe/{videos[self.a]}?", page_a)
        self.assertIn('data-media-id="m-507"', page_a)
        # A course without that video holds the page back there.
        del self.fake.files[self.b][videos[self.b]]
        def again(content):
            next(p for p in content["pages"] if p["title"] == "Break Even Point")["body"] += "<p>Then try the exercise.</p>"
        self.save(again)
        b = self.course(self.preview(), self.b)
        self.assertEqual([(x["label"], x["reasons"]) for x in b["blocked"]],
                         [("Break Even Point", ["the video or audio file course files/BreakEvenPoint.mp4 is not in this course"])])

    def test_a_page_held_twice_is_the_copy_its_module_item_points_at(self):
        old = self.fake.add_page(self.b, "Office Hours", "<p>Old hours</p>")  # an older copy in 508, in no module
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertEqual(b["ops"], [], b)  # neither a third copy nor an item pointed at one
        self.assertEqual([e["label"] for e in b["extra"] if e["kind"] == "page"], ["Office Hours"])  # the old copy, left alone
        self.assertEqual(next(e for e in b["extra"] if e["kind"] == "page")["canvas_id"], str(old["page_id"]))
        def edit(content):
            next(p for p in content["pages"] if p["title"] == "Office Hours")["body"] = "<p>BLOC 319</p>"
        self.save(edit)
        self.push()
        bodies = {p["url"]: p["body"] for p in self.fake.pages[self.b] if p["title"] == "Office Hours"}
        self.assertEqual(bodies, {self.ids[self.b]["office"]["url"]: "<p>BLOC 319</p>", old["url"]: "<p>Old hours</p>"})

    def test_two_copies_both_linked_are_never_guessed_between(self):
        old = self.fake.add_page(self.b, "Office Hours", "<p>Old hours</p>")
        self.fake.add_item(self.b, self.ids[self.b]["first"], "Page", "Office Hours", page_url=old["url"])  # both copies in one module
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertEqual([e["label"] for e in b["new"] if e["kind"] == "page"], ["Office Hours"])  # "Same as…" pairs it

    def test_removals_are_offered_and_only_what_faculty_removed_is_ticked(self):
        self.fake.add_module(self.b, "Instructor Module (Do Not Publish)", published=False)
        self.start()
        b = self.course(self.preview(), self.b)
        extra = {e["label"]: e for e in b["extra"]}
        self.assertFalse(extra["Instructor Module (Do Not Publish)"]["remove"])
        self.push()
        def edit(content):
            content["modules"] = [m for m in content["modules"] if m["name"] != "Week 1"]
        self.save(edit)
        shown = self.preview()
        for course in (self.a, self.b):
            extra = {e["label"]: e for e in self.course(shown, course)["extra"]}
            self.assertTrue(extra["Week 1"]["remove"])
        self.assertFalse({e["label"]: e for e in self.course(shown, self.b)["extra"]}["Instructor Module (Do Not Publish)"]["remove"])
        # Leaving it in 508: untick it there.
        keep = [k for k in self.course(shown, self.b)["remove"] if k != f"module:{self.ids[self.b]['week']}"]
        self.push({self.b: {"remove": keep}})
        self.assertEqual([m["name"] for m in self.fake.modules[self.a]], ["Course Materials"])
        self.assertEqual([m["name"] for m in self.fake.modules[self.b]], ["Course Materials", "Week 1", "Instructor Module (Do Not Publish)"])
        # Nothing Faculty only read is deleted by default; pages never are.
        self.assertTrue(all(not e["remove"] for e in self.course(self.preview(), self.a)["extra"] if e["kind"] == "page"))

    def test_sections_limit_where_things_go_and_missing_files_hold_a_page_back(self):
        self.start()
        def edit(content):
            content["pages"].append({"title": "Section 508 Review", "body": "<p>Only 508</p>", "sections": [self.sections[508]]})
            notes = self.ids[self.a]
            content["pages"].append({"title": "Notes", "body": f'<a href="/courses/{self.a}/files/{next(k for k, v in self.fake.files[self.a].items() if v["display_name"] == "notes.pdf")}/download">notes</a>'})
            del notes
        self.save(edit)
        # 508 lost its copy of notes.pdf: the Notes page cannot be pushed there.
        missing = next(k for k, v in self.fake.files[self.b].items() if v["display_name"] == "notes.pdf")
        del self.fake.files[self.b][missing]
        shown = self.preview()
        a, b = self.course(shown, self.a), self.course(shown, self.b)
        self.assertEqual([op["label"] for op in a["ops"] if op["op"] == "create_page"], ["Notes"])
        self.assertEqual([op["label"] for op in b["ops"] if op["op"] == "create_page"], ["Section 508 Review"])
        self.assertEqual([(x["label"], x["reasons"]) for x in b["blocked"]], [("Notes", ["the file course files/Template/notes.pdf is not in this course"])])

    def test_the_push_must_be_the_approved_plan(self):
        self.start()
        def edit(content):
            content["modules"][0]["name"] = "Course Materials 2026"
        self.save(edit)
        shown = self.preview()
        body = {"fingerprint": shown["fingerprint"], "choices": shown["choices"], "request_id": rid()}
        self.assertEqual(self.client.post(f"{self.base}/push", json=body, headers=HEADERS).status_code, 400)
        self.fake.modules[self.b][0]["name"] = "Changed in Canvas"
        answer = self.client.post(f"{self.base}/push", json={**body, "confirmation": "push"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(self.client.post(f"{self.base}/push", json={**body, "confirmation": "push"}).status_code, 403)  # the workspace guard

    def add_exam_page(self):
        self.start()
        self.save(lambda content: content["pages"].append({"ref": "exam", "title": "Exam I Information", "body": "<p>Thursday</p>", "published": True}))
        shown = self.preview()
        return lambda: {"fingerprint": shown["fingerprint"], "choices": shown["choices"], "request_id": rid(), "confirmation": "push"}

    def wait_idle(self, service):
        for _ in range(200):
            if not service._active:
                return
            time.sleep(0.05)
        self.fail("a push never finished")

    def exam_pages(self):
        return [len([p for p in self.fake.pages[c] if p["title"] == "Exam I Information"]) for c in (self.a, self.b)]

    def test_two_pushes_approved_together_write_once(self):
        # Two windows push the same preview at once: checking that a course is free and claiming it are one step
        # (content_service.claim); before, both passed and each made the page.
        body, service = self.add_exam_page(), self.app.extensions["canvas_content"]
        original, planned = service._plan, threading.Barrier(2)
        def both_planned(*args, **kwargs):
            found = original(*args, **kwargs)
            planned.wait(timeout=10)  # both plans are read before either push claims its courses
            return found
        answers = []
        def push():
            answers.append(self.app.test_client().post(f"{self.base}/push", json=body(), headers=HEADERS))
        with mock.patch.object(service, "_plan", side_effect=both_planned):
            windows = [threading.Thread(target=push) for _ in range(2)]
            for window in windows:
                window.start()
            for window in windows:
                window.join(timeout=20)
        self.assertEqual(sorted(a.status_code for a in answers), [200, 409], [a.json for a in answers])
        self.wait_idle(service)
        self.assertEqual(self.exam_pages(), [1, 1])

    def test_a_plan_read_before_another_push_wrote_is_refused(self):
        # Another window's push runs to the end while this one reads Canvas: this plan still says "new", so it would make the
        # page a second time; the course was claimed since this push began, and it is refused.
        body, service = self.add_exam_page(), self.app.extensions["canvas_content"]
        original, first = service._plan, []
        def another_push_meanwhile(*args, **kwargs):
            found = original(*args, **kwargs)
            if not first:
                first.append(True)
                self.push()
            return found
        with mock.patch.object(service, "_plan", side_effect=another_push_meanwhile):
            answer = self.client.post(f"{self.base}/push", json=body(), headers=HEADERS)
        self.assertEqual(answer.status_code, 409, answer.json)
        self.assertEqual(self.exam_pages(), [1, 1])
        self.assertEqual(self.push()["status"], "done")  # nothing holds the courses afterwards
        self.assertEqual(self.exam_pages(), [1, 1])

    def test_pairing_by_hand_and_marking_new(self):
        self.fake.modules[self.b][1]["name"] = "Week One"
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertEqual([op["op"] for op in b["ops"]].count("create_module"), 1)  # "Week 1" is not found by name in 508
        week = next(m for m in self.client.get(self.base).json["content"]["modules"] if m["name"] == "Week 1")
        answer = self.client.post(f"{self.base}/pair", json={"course_id": self.b, "kind": "module", "plan_key": week["id"],
                                                             "canvas_id": self.ids[self.b]["week"]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        b = self.course(self.preview(), self.b)
        self.assertNotIn("create_module", [op["op"] for op in b["ops"]])
        self.assertEqual([(e["label"], e["verdict"]) for e in b["attention"]], [("Week 1", "differs")])
        # Marked new in 508: never matched by name again.
        materials = next(m for m in self.client.get(self.base).json["content"]["modules"] if m["name"] == "Course Materials")
        self.client.post(f"{self.base}/pair", json={"course_id": self.b, "kind": "module", "plan_key": materials["id"], "canvas_id": ""}, headers=HEADERS)
        b = self.course(self.preview(), self.b)
        self.assertEqual([op["op"] for op in b["ops"]].count("create_module"), 1)

    def test_saving_checks_the_copy(self):
        self.start()
        view = self.client.get(self.base).json
        for change, status in ((lambda c: c["pages"].append({"id": "pg_invented", "title": "X"}), 400),
                               (lambda c: c["pages"][0].update(title=""), 400),
                               (lambda c: [p.update(front_page=True) for p in c["pages"]], 400),
                               (lambda c: c["modules"][0]["items"].append({"type": "Page", "title": "X", "page": "nope"}), 400),
                               (lambda c: c["modules"][0].update(unlock_at="2026-08-23"), 400)):
            content = __import__("copy").deepcopy(view["content"])
            change(content)
            answer = self.client.post(self.base, json={"base_revision": view["revision"], "request_id": rid(), "content": content}, headers=HEADERS)
            self.assertEqual(answer.status_code, status, answer.json)
        stale = self.client.post(self.base, json={"base_revision": 99, "request_id": rid(), "content": view["content"]}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        same = self.client.post(self.base, json={"base_revision": view["revision"], "request_id": rid(), "content": view["content"]}, headers=HEADERS)
        self.assertEqual(same.json["revision"], view["revision"])  # identical: nothing recorded


class OneSectionFirstTests(ContentBase):
    def overview(self):
        answer = self.client.get("/api/canvas/overview")
        self.assertEqual(answer.status_code, 200, answer.json)
        return next(t for t in answer.json["terms"] if t["term"]["id"] == self.term)

    def test_a_first_push_goes_to_one_course_checked_in_canvas_then_to_the_rest(self):
        self.assertEqual(self.overview()["next"], "start_copy")
        self.fake.requests.clear()
        self.overview()
        self.assertEqual(self.fake.requests, [])  # the hub reads Faculty's records alone
        self.start()
        self.assertEqual(self.client.get(self.base).json["pushed_courses"], [])
        self.assertEqual(self.overview()["next"], "push_first")
        def edit(content):
            content["modules"][0]["name"] = "Course Materials 2026"
        self.save(edit)
        everywhere = self.preview()
        only_b = self.preview(courses=[self.b])
        self.assertEqual([c["course_id"] for c in only_b["courses"]], [self.b])
        self.assertEqual(only_b["only"], [self.b])
        self.assertNotEqual(only_b["fingerprint"], everywhere["fingerprint"])
        # A plan made for every course cannot be pushed to one.
        wrong = self.client.post(f"{self.base}/push", json={"fingerprint": everywhere["fingerprint"], "choices": only_b["choices"],
                                                            "courses": [self.b], "request_id": rid(), "confirmation": "push"}, headers=HEADERS)
        self.assertEqual(wrong.status_code, 409)
        self.push(courses=[self.b])
        self.assertEqual([m["name"] for m in self.fake.modules[self.b]][0], "Course Materials 2026")
        self.assertEqual([m["name"] for m in self.fake.modules[self.a]][0], "Course Materials")  # untouched until its own push
        self.assertTrue(all(f"/courses/{self.b}/" in path for _, path, _ in self.fake.writes))
        self.assertEqual(self.client.get(self.base).json["pushed_courses"], [self.b])
        hub = self.overview()
        self.assertEqual((hub["next"], [(c["course_id"], c["pushed"]) for c in hub["courses"]]), ("push_rest", [(self.a, False), (self.b, True)]))
        rest = self.preview()
        self.assertEqual(self.course(rest, self.b)["ops"], [])
        self.assertEqual([op["op"] for op in self.course(rest, self.a)["ops"]], ["update_module"])
        self.push(courses=[self.a])
        self.assertEqual(self.overview()["next"], "up_to_date")

    def test_one_course_still_gets_links_to_the_others_rewritten(self):
        self.start()
        def edit(content):
            content["pages"].append({"title": "Hours again", "body": f'<a href="/courses/{self.a}/pages/{self.ids[self.a]["office"]["url"]}">hours</a>'})
        self.save(edit)
        self.push(courses=[self.b])
        page = next(p for p in self.fake.pages[self.b] if p["title"] == "Hours again")
        self.assertIn(f"/courses/{self.b}/pages/{self.ids[self.b]['office']['url']}", page["body"])

    def test_the_courses_named_must_be_the_terms(self):
        self.start()
        for courses in ([], ["999999"], "all", [5]):
            answer = self.client.post(f"{self.base}/preview", json={"choices": {}, "courses": courses}, headers=HEADERS)
            self.assertEqual(answer.status_code, 400, (courses, answer.json))



class HubTests(Seeded):
    def test_the_hub_says_to_link_the_sections_first(self):
        terms = self.client.get("/api/canvas/overview").json["terms"]
        self.assertEqual([(t["term"]["code"], t["next"], [(s["number"], s["course"]) for s in t["sections"]]) for t in terms],
                         [("MATH 140", "link_sections", [(507, None), (508, None)])])
        self.assertTrue(terms[0]["group"])


class ColumnAssignmentTests(ContentBase):
    """COLUMN ASSIGNMENTS (requirement, 2026-10-06): a column the gradebook gains gets its
    assignment in Faculty's copy right away; one whose assignment Canvas holds is listed beside the copy, read-only."""

    def setup(self, items):
        saved = self.client.post(f"/api/gradebook/terms/{self.term}/setup", json={"base_revision": None, "request_id": rid(), "setup": {
            "categories": [{"ref": "cfu", "name": "Checks for Understanding Category"}, {"ref": "hw", "name": "Homework"}], "items": items}},
            headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        return saved.json["setup"]

    def test_a_new_column_gets_its_assignment_in_the_copy_right_away(self):
        self.start()
        before = self.client.get(self.base).json["revision"]
        self.fake.requests.clear()
        setup = self.setup([{"title": "Class 2 - Poll", "points": "10", "category_id": "cfu", "due_date": "2026-10-01"},
                            {"title": "Sect. 1.1 Homework", "points": "10", "category_id": "hw"},
                            {"title": "Loose", "points": "5"},
                            {"title": "Kept off", "points": "5", "category_id": "hw", "canvas": "off"}])
        self.assertEqual(self.fake.requests, [])  # Canvas is not contacted
        ids = {item["title"]: item["id"] for item in setup["items"]}
        view = self.client.get(self.base).json
        self.assertEqual(view["revision"], before + 1)  # one revision of the copy
        by_name = {a["name"]: a for a in view["content"]["assignments"]}
        poll = by_name["Class 2 - Poll"]
        self.assertEqual({k: poll[k] for k in ("points", "group", "published", "submission_types", "grading_type", "due_at", "gradebook_item", "sections")},
                         {"points": "10", "group": "Checks for Understanding Category", "published": True, "submission_types": ["none"],
                          "grading_type": "points", "due_at": "2026-10-02T04:59:00Z", "gradebook_item": ids["Class 2 - Poll"], "sections": []})
        # A name the copy already has is linked to the column, never made twice; one without a category or kept off Canvas is not.
        self.assertEqual(by_name["Sect. 1.1 Homework"]["gradebook_item"], ids["Sect. 1.1 Homework"])
        self.assertEqual(sum(1 for a in view["content"]["assignments"] if a["name"] == "Sect. 1.1 Homework"), 1)
        self.assertNotIn("Loose", by_name)
        self.assertNotIn("Kept off", by_name)
        self.assertEqual(view["columns"], {"canvas_only": [], "ungrouped": [{"item_id": ids["Loose"], "title": "Loose"}]})
        # Saving the gradebook again (a due date moved) makes nothing more in the copy.
        items = [{**item, "due_date": "2026-10-03"} if item["title"] == "Loose" else item for item in setup["items"]]
        again = self.client.post(f"/api/gradebook/terms/{self.term}/setup", json={"base_revision": setup["revision"], "request_id": rid(),
                                 "setup": {"categories": setup["categories"], "items": items}}, headers=HEADERS)
        self.assertEqual(again.status_code, 200, again.json)
        self.assertEqual(self.client.get(self.base).json["revision"], before + 1)

    def test_a_column_canvas_holds_is_listed_beside_the_copy_never_copied(self):
        setup = self.setup([{"title": "Chapter 1 Quiz", "points": "2", "category_id": "hw", "due_date": "2026-09-30"},
                            {"title": "Homework 1.1", "points": "10", "category_id": "hw"}])
        ids = {item["title"]: item["id"] for item in setup["items"]}
        quiz = {"id": self.fake.add_assignment(self.a, "Chapter 1 Quiz", 2, group="Homework", quiz=True)}  # the quiz's own assignment
        with connect(self.app.config["DATABASE"]) as db:  # what a sync or an import records when it links a column
            request = rid()
            db.execute("INSERT INTO gradebook_requests(id,term_id,request_id,request_sha256,kind,summary_json,actor,created_at) "
                       "VALUES(?,?,?,?,'import','{}','workspace','2026-10-06T00:00:00Z')", (request, self.term, request, "0" * 64))
            for item, assignment in ((ids["Chapter 1 Quiz"], quiz["id"]), (ids["Homework 1.1"], self.ids[self.a]["hw"])):
                db.execute("INSERT INTO gradebook_canvas_links(id,term_id,course_id,assignment_id,item_id,request_ref,actor,created_at) "
                           "VALUES(?,?,?,?,?,?,'workspace','2026-10-06T00:00:00Z')", (rid(), self.term, self.a, str(assignment), item, request))
        view = self.start()
        names = [a["name"] for a in view["content"]["assignments"]]
        self.assertNotIn("Chapter 1 Quiz", names)  # the quiz keeps its own assignment: never in the copy
        self.assertNotIn("Homework 1.1", names)  # the copy calls it "Sect. 1.1 Homework": the same Canvas assignment
        self.assertEqual(view["columns"]["canvas_only"], [{"item_id": ids["Chapter 1 Quiz"], "title": "Chapter 1 Quiz", "group": "Homework",
                                                           "points": "2", "due_date": "2026-09-30", "kind": "canvas"}])


class PlacementTests(ContentBase):
    """"Put on Canvas": a Faculty assignment or a gradebook column becomes an assignment of the copy, in a module (canvas/placement.py)."""

    def faculty(self, title="Quiz 3", key="quiz-3"):
        with connect(self.app.config["DATABASE"]) as db:
            definition = db.execute("SELECT definition_id FROM course_terms WHERE id=?", (self.term,)).fetchone()[0]
        answer = self.client.post("/api/documents/assignments", json={"definition_id": definition, "key": key, "title": title}, headers=HEADERS)
        self.assertIn(answer.status_code, (200, 201), answer.json)
        return answer.json["id"]

    def column(self, assignment_id=None):
        item = {"title": "Quiz 3", "points": "10", "category_id": "q", "due_date": "2026-10-02"}
        if assignment_id:
            item["assignment_id"] = assignment_id
        saved = self.client.post(f"/api/gradebook/terms/{self.term}/setup", json={"base_revision": None, "request_id": rid(),
                                 "setup": {"categories": [{"ref": "q", "name": "Quizzes"}], "items": [item]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        return saved.json["setup"]["items"][0]["id"]

    def proposal(self, **query):
        answer = self.client.get(f"{self.base}/placement", query_string=query)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def place(self, shown, fields=None, module=None, request_id=None, **link):
        body = {"base_revision": shown["revision"], "request_id": request_id or rid(), "fields": {**shown["fields"], **(fields or {})},
                "module": module, **link}
        return self.client.post(f"{self.base}/placement", json=body, headers=HEADERS)

    def test_a_faculty_assignment_is_placed_in_a_new_module_and_pushed_like_any_part_of_the_copy(self):
        quiz = self.faculty()
        column = self.column(quiz)
        self.assertEqual(self.proposal(assignment_id=quiz)["state"], "not_started")
        self.start()  # COLUMN ASSIGNMENTS: the column's assignment joins the new copy (published, no submission, due 11:59 PM)
        self.fake.requests.clear()
        shown = self.proposal(assignment_id=quiz)
        self.assertEqual(self.fake.requests, [])  # Faculty's records only
        self.assertEqual((shown["state"], shown["existing"]["name"], shown["existing"]["linked"], shown["column"]["id"]), ("ready", "Quiz 3", True, column))
        self.assertEqual({k: shown["fields"][k] for k in ("name", "points", "group", "due_date", "due_time", "submission", "published")},
                         {"name": "Quiz 3", "points": "10", "group": "Quizzes", "due_date": "2026-10-02", "due_time": "23:59",
                          "submission": "none", "published": True})
        self.assertEqual([m["name"] for m in shown["modules"]], ["Course Materials", "Week 1"])
        request = rid()
        placed = self.place(shown, {"published": True}, {"new_name": "Quizzes"}, request, assignment_id=quiz)
        self.assertEqual(placed.status_code, 200, placed.json)
        key = placed.json["key"]
        assignment = next(a for a in placed.json["content"]["assignments"] if a["id"] == key)
        self.assertEqual((assignment["faculty_assignment"], assignment["gradebook_item"], assignment["due_at"], assignment["submission_types"]),
                         (quiz, column, "2026-10-03T04:59:00Z", ["none"]))  # 11:59 PM in College Station is 04:59 UTC the next day
        module = placed.json["content"]["modules"][-1]
        self.assertEqual((module["name"], [(i["type"], i["title"], i["content"]) for i in module["items"]]),
                         ("Quizzes", [("Assignment", "Quiz 3", {"assignment": key})]))
        # The same request again changes nothing.
        again = self.place(shown, {"published": True}, {"new_name": "Quizzes"}, request, assignment_id=quiz)
        self.assertEqual((again.status_code, again.json["key"], again.json["revision"]), (200, key, placed.json["revision"]))
        status = self.client.get(f"{self.base}/assignments/status").json["assignments"]
        self.assertEqual([(a["key"], a["courses"]) for a in status], [(key, {self.a: "not_yet", self.b: "not_yet"})])
        # The proposal now edits the copy's assignment; the ordinary push makes it in the chosen course.
        shown = self.proposal(assignment_id=quiz)
        self.assertEqual((shown["existing"]["key"], shown["fields"]["module"], shown["fields"]["published"]), (key, module["id"], True))
        preview = self.course(self.preview(courses=[self.b]), self.b)
        self.assertEqual({op: preview["counts"].get(op) for op in ("create_group", "create_assignment", "create_module", "create_item")},
                         {"create_group": 1, "create_assignment": 1, "create_module": 1, "create_item": 1})
        self.push(courses=[self.b])
        made = next(x for g in self.fake.groups[self.b] for x in g["assignments"] if x["name"] == "Quiz 3")
        self.assertEqual((made["points_possible"], made["due_at"], made["published"]), (10.0, "2026-10-03T04:59:00Z", True))
        self.assertEqual(self.fake.modules[self.b][-1]["items"][0]["content_id"], made["id"])
        status = self.client.get(f"{self.base}/assignments/status").json["assignments"][0]
        self.assertEqual((status["courses"], status["pushed"], status["total"]), ({self.a: "not_yet", self.b: "pushed"}, 1, 2))
        # Changing it (and moving it to Week 1) is an update where it was pushed.
        week = next(m for m in shown["modules"] if m["name"] == "Week 1")
        changed = self.place(shown, {"points": "12"}, {"id": week["id"]}, assignment_id=quiz)
        self.assertEqual(changed.status_code, 200, changed.json)
        content = changed.json["content"]
        self.assertEqual([m["name"] for m in content["modules"] if any((i.get("content") or {}).get("assignment") == key for i in m["items"])], ["Week 1"])
        self.assertEqual(self.client.get(f"{self.base}/assignments/status").json["assignments"][0]["courses"][self.b], "changed")

    def test_a_column_places_its_own_assignment_and_a_name_links_what_the_copy_has(self):
        self.start()
        column = self.column()
        shown = self.proposal(item_id=column)
        self.assertEqual((shown["assignment"], shown["fields"]["name"]), (None, "Quiz 3"))
        # A Faculty assignment named like one the copy already has takes that one over (no duplicate), with its module.
        homework = self.faculty("Sect. 1.1 Homework", "hw-1-1")
        shown = self.proposal(assignment_id=homework)
        self.assertEqual((shown["existing"]["name"], shown["existing"]["linked"], shown["fields"]["points"]), ("Sect. 1.1 Homework", False, "10"))
        placed = self.place(shown, assignment_id=homework)
        self.assertEqual(placed.status_code, 200, placed.json)
        # The column's own assignment joined the copy when the column was made (COLUMN ASSIGNMENTS).
        self.assertEqual([(a["name"], a["faculty_assignment"], a["gradebook_item"]) for a in placed.json["content"]["assignments"]],
                         [("Sect. 1.1 Homework", homework, None), ("Quiz 3", None, column)])
        # The link is Faculty's own: nothing to push for it; the column's assignment (and its group) is all that is new.
        self.assertEqual([sorted((op["op"], op.get("label", "")) for op in c["ops"]) for c in self.preview()["courses"]],
                         [[("create_assignment", "Quiz 3"), ("create_group", "Assignment Group “Quizzes”")]] * 2)

    def test_a_column_made_for_a_faculty_assignment_on_canvas_is_linked_to_its_grading(self):
        """GRADED IN FACULTY, LINKED (requirement, 2026-11-07, of Quiz 7: approved, put on Canvas, and made a column by the morning
        sync that counted 0 for everyone once due and was posted): the column the copy ties to a Faculty assignment through its
        Canvas assignment is linked to that assignment's grading, once."""
        quiz = self.faculty()
        self.start()
        shown = self.proposal(assignment_id=quiz)
        placed = self.place({**shown, "fields": {**shown["fields"], "group": "Quizzes", "points": "10", "due_date": "2026-10-02"}}, assignment_id=quiz)
        self.assertEqual(placed.status_code, 200, placed.json)
        self.push(courses=[self.a])
        made = next(x for g in self.fake.groups[self.a] for x in g["assignments"] if x["name"] == "Quiz 3")
        # The sync's new column, as Sync makes one from Canvas: title, points, category and due date, no link.
        column = self.column()
        gradebook = self.app.extensions["gradebook"]

        def setup():
            from gradebook.service import _setup
            with connect(self.app.config["DATABASE"]) as db:
                return next(i for i in _setup(db, self.term)[0]["items"] if i["id"] == column)
        self.assertEqual((setup()["assignment_id"], setup()["link"]), (None, None))  # its name alone ties it to nothing
        with connect(self.app.config["DATABASE"]) as db:  # what the sync records when it makes the column
            request = rid()
            db.execute("INSERT INTO gradebook_requests(id,term_id,request_id,request_sha256,kind,summary_json,actor,created_at) "
                       "VALUES(?,?,?,?,'import','{}','workspace','2026-10-06T00:00:00Z')", (request, self.term, request, "0" * 64))
            db.execute("INSERT INTO gradebook_canvas_links(id,term_id,course_id,assignment_id,item_id,request_ref,actor,created_at) "
                       "VALUES(?,?,?,?,?,?,'workspace','2026-10-06T00:00:00Z')", (rid(), self.term, self.a, str(made["id"]), column, request))
        self.assertEqual(gradebook.link_copy_columns(self.term), ["Quiz 3"])
        self.assertEqual((setup()["assignment_id"], setup()["link"]), (quiz, {"kind": "grading", "parts": ["frq"]}))
        self.assertEqual(gradebook.link_copy_columns(self.term), [])  # once
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_setups WHERE term_id=? AND request_id LIKE 'grading-link:%'", (self.term,)).fetchone()[0], 1)

    def test_placing_checks_the_copy_and_the_links(self):
        quiz = self.faculty()
        self.start()
        shown = self.proposal(assignment_id=quiz)
        # No column, so no category: the group is left to choose, and a blank one is refused (Canvas would put the assignment in
        # the course's first group, as it did Quiz 3 on 2026-10-02).
        self.assertEqual(shown["fields"]["group"], "")
        for blank in ("", "  "):
            refused = self.place(shown, {"group": blank}, assignment_id=quiz)
            self.assertEqual(refused.status_code, 400, refused.json)
            self.assertIn("Choose its Assignment Group", refused.json["error"])
        shown = {**shown, "fields": {**shown["fields"], "group": "Quizzes"}}
        self.assertEqual(self.place({**shown, "revision": 99}, assignment_id=quiz).status_code, 409)
        self.assertEqual(self.client.get(f"{self.base}/placement", query_string={"assignment_id": "nope"}).status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/placement").status_code, 400)
        self.assertEqual(self.place(shown, {"due_date": "10/02/2026"}, assignment_id=quiz).status_code, 400)
        self.assertEqual(self.place(shown, {"points": "ten"}, assignment_id=quiz).status_code, 400)
        self.assertEqual(self.place(shown, module={"id": "mod_gone"}, assignment_id=quiz).status_code, 400)


class AssignmentTests(ContentBase):
    def test_an_assignment_group_is_required_and_a_blank_one_is_never_made_in_canvas(self):
        """Canvas puts an assignment made without an Assignment Group in the course's FIRST group (Quiz 3 went to Checks for
        Understanding, 2026-10-02), so a save refuses a blank one and a push holds back one an older revision saved."""
        self.start()
        quiz = {"name": "Quiz 3", "points": "10", "grading_type": "points", "submission_types": ["on_paper"], "group": "  ", "published": False}
        view = self.client.get(self.base).json
        refused = self.client.post(self.base, json={"base_revision": view["revision"], "request_id": rid(),
                                   "content": {**view["content"], "assignments": [*view["content"]["assignments"], quiz]}}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400, refused.json)
        self.assertIn("Choose the Assignment Group of “Quiz 3”", refused.json["error"])
        original = model._assignments
        with mock.patch.object(model, "_assignments", lambda *a, **k: original(*a, **{**k, "require_group": False})):
            self.save(lambda content: content["assignments"].append(dict(quiz)))
        for course in (self.a, self.b):
            shown = self.course(self.preview(), course)
            self.assertNotIn("create_assignment", shown["counts"])
            self.assertIn(("Quiz 3", ["choose its Assignment Group (Canvas would put it in the course's first Assignment Group)"]),
                          [(x["label"], x["reasons"]) for x in shown["blocked"]])

    def quiz_pushed_unpublished(self):
        """Quiz 3 as the instructor's copy had it: put on Canvas unpublished in both courses."""
        self.start()
        self.save(lambda content: content["assignments"].append({"name": "Quiz 3", "points": "10", "grading_type": "points",
                                                                 "submission_types": ["on_paper"], "group": "Quiz Category", "published": False}))
        self.push()

    def canvas_quiz(self, course):
        return next(a for g in self.fake.groups[course] for a in g["assignments"] if a["name"] == "Quiz 3")

    def publish_in_canvas(self, *courses):
        for course in courses:
            self.canvas_quiz(course).update(published=True, workflow_state="published")

    def copy_quiz(self):
        return next(a for a in self.client.get(self.base).json["content"]["assignments"] if a["name"] == "Quiz 3")

    def verdicts(self, shown, course):
        plan = self.course(shown, course)
        return [e["verdict"] for e in plan["attention"] if e["label"] == "Quiz 3"], [o["op"] for o in plan["ops"] if o["label"] == "Quiz 3"]

    def follow(self):
        return self.app.extensions["canvas_content"].follow_published(self.term, f"canvas-published:test:{rid()}")

    def test_published_in_canvas_is_not_asked_about_and_replace_all_never_unpublishes_it(self):
        """PUBLISHED IN CANVAS STANDS (docs/decisions/canvas.md; 2026-11-03: Quiz 3, published in Canvas, asked about in each section)."""
        self.quiz_pushed_unpublished()
        self.publish_in_canvas(self.a, self.b)
        shown = self.preview()
        for course in (self.a, self.b):
            self.assertEqual(self.verdicts(shown, course), ([], []))
        everything = {course: {"replace": "all"} for course in (self.a, self.b)}
        for course in (self.a, self.b):
            self.assertEqual(self.verdicts(self.preview(everything), course), ([], []))
        self.push(everything)
        for course in (self.a, self.b):
            self.assertTrue(self.canvas_quiz(course)["published"])

    def test_the_copy_follows_when_every_course_has_published_it(self):
        self.quiz_pushed_unpublished()
        self.assertEqual(self.follow(), 0)  # nothing published: not even a revision
        self.publish_in_canvas(self.a)
        self.assertEqual(self.follow(), 0)  # one course: publishing the copy would publish the other on the next push
        self.assertFalse(self.copy_quiz()["published"])
        self.assertEqual(self.verdicts(self.preview(), self.a), ([], []))
        self.assertEqual(self.verdicts(self.preview(), self.b), ([], []))
        self.assertFalse(self.canvas_quiz(self.b)["published"])
        self.publish_in_canvas(self.b)
        revision = self.client.get(self.base).json["revision"]
        self.assertEqual(self.follow(), 1)
        view = self.client.get(self.base).json
        self.assertEqual(view["revision"], revision + 1)
        self.assertTrue(self.copy_quiz()["published"])
        self.assertEqual(self.follow(), 0)
        # Recorded as in step: a later edit in Faculty is a plain update that keeps it published, not a conflict.
        def rename(content):
            next(a for a in content["assignments"] if a["name"] == "Quiz 3")["points"] = "12"
        self.save(rename)
        shown = self.preview()
        for course in (self.a, self.b):
            self.assertEqual(self.verdicts(shown, course), ([], ["update_assignment"]))
        self.push()
        for course in (self.a, self.b):
            self.assertEqual((self.canvas_quiz(course)["published"], self.canvas_quiz(course)["points_possible"]), (True, 12.0))

    def test_what_published_in_canvas_does_not_cover_still_asks_or_updates(self):
        self.quiz_pushed_unpublished()
        # Something else changed in Canvas too: asked about, as ever.
        self.publish_in_canvas(self.a)
        self.canvas_quiz(self.a)["points_possible"] = 20.0
        self.assertEqual(self.verdicts(self.preview(), self.a), (["edited_in_canvas"], []))
        self.canvas_quiz(self.a).update(points_possible=10.0, published=False, workflow_state="unpublished")
        # Unpublished in Canvas after the copy published it: asked about, as ever.
        def publish(content):
            next(a for a in content["assignments"] if a["name"] == "Quiz 3")["published"] = True
        self.save(publish)
        self.push()
        self.canvas_quiz(self.b).update(published=False, workflow_state="unpublished")
        self.assertEqual(self.verdicts(self.preview(), self.b), (["edited_in_canvas"], []))
        self.publish_in_canvas(self.b)
        # Unpublished on purpose in Faculty: the copy changed since its push, so the push unpublishes it and the morning leaves it.
        def unpublish(content):
            next(a for a in content["assignments"] if a["name"] == "Quiz 3")["published"] = False
        self.save(unpublish)
        self.assertEqual(self.verdicts(self.preview(), self.a), ([], ["update_assignment"]))
        self.assertEqual(self.follow(), 0)
        self.assertFalse(self.copy_quiz()["published"])
        self.push()
        self.assertFalse(self.canvas_quiz(self.a)["published"])

    def test_assignments_are_copied_edited_once_and_pushed_to_every_course(self):
        started = self.start()
        homework = started["content"]["assignments"]
        self.assertEqual([(a["name"], a["points"], a["submission_types"]) for a in homework], [("Sect. 1.1 Homework", "10", ["on_paper"])])
        item = started["content"]["modules"][1]["items"][0]
        self.assertEqual(item["content"], {"assignment": homework[0]["id"]})  # the copy's own assignment, not a name
        def edit(content):
            hw = content["assignments"][0]
            hw.update(name="Section 1.1 Homework", points="12", due_at="2026-09-05T04:59:00Z", group="WebAssign Homework",
                      section_due={self.sections[508]: "2026-09-06T04:59:00Z"})
            content["assignments"].append({"name": "Exam II Grade", "points": "100", "grading_type": "points", "submission_types": ["none"],
                                           "group": "Semester Exams", "published": False, "description": f'<a href="/courses/{self.a}/pages/{self.ids[self.a]["office"]["url"]}">rules</a>'})
        self.save(edit)
        shown = self.preview()
        for course in (self.a, self.b):
            counts = self.course(shown, course)["counts"]
            self.assertEqual((counts.get("create_group"), counts.get("create_assignment"), counts.get("update_assignment")), (2, 1, 1), counts)
        self.push()
        for course in (self.a, self.b):
            rows = {a["name"]: a for g in self.fake.groups[course] for a in g["assignments"]}
            self.assertEqual(rows["Section 1.1 Homework"]["points_possible"], 12.0)
            groups = {g["id"]: g["name"] for g in self.fake.groups[course]}
            self.assertEqual(groups[rows["Section 1.1 Homework"]["assignment_group_id"]], "WebAssign Homework")
            self.assertEqual(groups[rows["Exam II Grade"]["assignment_group_id"]], "Semester Exams")
            self.assertFalse(rows["Exam II Grade"]["published"])
            self.assertIn(f"/courses/{course}/pages/{self.ids[course]['office']['url']}", rows["Exam II Grade"]["description"])
            # The module item still points at the (renamed) assignment.
            item = self.fake.modules[course][1]["items"][0]
            self.assertEqual(item["content_id"], rows["Section 1.1 Homework"]["id"])
        due = {course: next(a for g in self.fake.groups[course] for a in g["assignments"] if a["name"] == "Section 1.1 Homework")["due_at"] for course in (self.a, self.b)}
        self.assertEqual(due, {self.a: "2026-09-05T04:59:00Z", self.b: "2026-09-06T04:59:00Z"})  # 508's own date
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])

    def test_an_assignment_is_never_deleted_unless_ticked(self):
        self.fake.add_assignment(self.b, "Old quiz grade", 10)
        self.start()
        b = self.course(self.preview(), self.b)
        extra = next(e for e in b["extra"] if e["label"] == "Old quiz grade")
        self.assertEqual((extra["kind"], extra["remove"]), ("assignment", False))
        def edit(content):
            content["assignments"] = []
            for module in content["modules"]:
                module["items"] = [i for i in module["items"] if i["type"] != "Assignment"]
        self.save(edit)
        a = self.course(self.preview(), self.a)
        homework = next(e for e in a["extra"] if e["label"] == "Sect. 1.1 Homework")
        self.assertTrue(homework["was_faculty"])
        self.assertFalse(homework["remove"])  # removed from Faculty's copy, still never ticked by default: it holds grades


class RenderTests(ContentBase):
    def test_links_to_other_courses_of_the_term_only_are_rewritten(self):
        self.start()
        view = self.client.get(self.base).json
        service = self.app.extensions["canvas_content"]
        courses = service._read([self.a, self.b])
        with connect(self.app.config["DATABASE"]) as db:
            links = service._links(db, self.term)
        sections = {self.a: [self.sections[507]], self.b: [self.sections[508]]}
        matches = {cid: model.match(view["content"], c, links.get(cid, {}), sections[cid])[0] for cid, c in courses.items()}
        body = (f'<a href="https://canvas.example.edu/courses/{self.a}/assignments/{self.ids[self.a]["hw"]}">hw</a>'
                f'<a href="/courses/99999/files/1">elsewhere</a><a href="/courses/{self.a}/grades">grades</a>'
                f'<a href="/api/v1/courses/{self.a}/files/{self.ids[self.a]["banner"]}">api</a>')
        html, problems = model.render(body, self.b, courses, matches)
        self.assertEqual(problems, [])
        self.assertIn(f"https://canvas.example.edu/courses/{self.b}/assignments/{self.ids[self.b]['hw']}", html)
        self.assertIn("/courses/99999/files/1", html)  # not one of this term's courses: untouched
        self.assertIn(f"/courses/{self.b}/grades", html)
        self.assertIn(f"/api/v1/courses/{self.b}/files/{self.ids[self.b]['banner']}", html)


class PublishTests(ContentBase):
    """Canvas publishes a module's items with it (unless told the module alone) and a page or assignment with its module item
    (2026-10-04 review: a module renamed in Faculty would have published the unpublished exam information pages it holds)."""

    def setUp(self):
        super().setUp()
        for course in (self.a, self.b):
            exam = self.fake.add_page(course, "Information for Exam II (MUST BE EDITED)", "<p>Room TBA</p>", published=False)
            self.fake.add_module(course, "Week 9", items=[{"type": "SubHeader", "title": "Exam II"},
                                                          {"type": "Page", "title": exam["title"], "page_url": exam["url"], "published": False}])

    def exam_page(self, course):
        return next(p for p in self.fake.pages[course] if p["title"].startswith("Information for Exam II"))

    def module_puts(self):
        return [fields for method, path, fields in self.fake.writes if method == "PUT" and "/modules/" in path and "/items/" not in path
                and "position" not in fields]

    def test_a_module_edit_never_publishes_what_it_holds(self):
        self.start()
        self.save(lambda c: next(m for m in c["modules"] if m["name"] == "Week 9").update(name="Week 9 — Oct 19–25"))
        self.fake.writes.clear()
        self.push()
        for course in (self.a, self.b):
            self.assertEqual(self.fake.modules[course][-1]["name"], "Week 9 — Oct 19–25")
            self.assertFalse(self.exam_page(course)["published"])  # still hidden from students
        self.assertTrue(self.module_puts())
        self.assertTrue(all("published" not in fields for fields in self.module_puts()))  # unchanged: never sent
        # Unpublishing a module in Faculty unpublishes the module alone: the published page it holds stays published.
        self.save(lambda c: next(m for m in c["modules"] if m["name"] == "Course Materials").update(published=False))
        self.fake.writes.clear()
        self.push()
        self.assertEqual({(f["published"], f["skip_content_tags"]) for f in self.module_puts()}, {(False, True)})
        for course in (self.a, self.b):
            self.assertFalse(next(m for m in self.fake.modules[course] if m["name"] == "Course Materials")["published"])
            self.assertTrue(next(p for p in self.fake.pages[course] if p["title"] == "Office Hours")["published"])

    def test_a_page_and_its_module_items_are_published_together(self):
        self.start()
        def edit(content):
            week = next(m for m in content["modules"] if m["name"] == "Week 9")
            week["items"][1]["title"] = "Exam II: what to bring"
            week["items"][1]["published"] = True  # the item's box alone: the page's own flag decides
            content["pages"].append({"ref": "rules", "title": "Calculator Rules", "body": "<p>TI-84 only</p>", "published": False})
            week["items"].append({"type": "Page", "title": "Calculator Rules", "page": "rules", "published": True})
        saved = self.save(edit)
        week = next(m for m in saved["content"]["modules"] if m["name"] == "Week 9")
        self.assertEqual([i["published"] for i in week["items"][1:]], [False, False])  # as their pages are
        self.fake.writes.clear()
        self.push()
        item_writes = [fields for method, path, fields in self.fake.writes if "/items" in path and method in ("PUT", "POST")]
        self.assertTrue(item_writes)
        self.assertTrue(all("published" not in fields for fields in item_writes), item_writes)
        for course in (self.a, self.b):
            self.assertFalse(self.exam_page(course)["published"])
            self.assertFalse(next(p for p in self.fake.pages[course] if p["title"] == "Calculator Rules")["published"])
            self.assertEqual(self.fake.modules[course][-1]["items"][1]["title"], "Exam II: what to bring")
        # Publishing the page in Faculty publishes it, and Canvas shows its item published with it: nothing else to push.
        self.save(lambda c: next(p for p in c["pages"] if p["title"] == "Calculator Rules").update(published=True))
        self.push()
        for course in (self.a, self.b):
            self.assertTrue(next(p for p in self.fake.pages[course] if p["title"] == "Calculator Rules")["published"])
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])


class LinkTests(ContentBase):
    """Links to pages and assignments a push makes (2026-10-04 review)."""

    def test_a_page_linking_to_one_held_back_is_held_back_too(self):
        notes = next(k for k, v in self.fake.files[self.a].items() if v["display_name"] == "notes.pdf")
        review = self.fake.add_page(self.a, "Exam Review", f'<a href="/courses/{self.a}/files/{notes}/download">notes</a>')
        self.fake.add_page(self.a, "Start", f'<a href="/courses/{self.a}/pages/{review["url"]}">review</a>')
        del self.fake.files[self.b][next(k for k, v in self.fake.files[self.b].items() if v["display_name"] == "notes.pdf")]
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertNotIn("create_page", [op["op"] for op in b["ops"]])
        self.assertEqual(sorted((x["label"], x["reasons"][0]) for x in b["blocked"]),
                         [("Exam Review", "the file course files/Template/notes.pdf is not in this course"),
                          ("Start", "the page “Exam Review” is not in this course yet")])

    def test_a_page_made_before_what_it_links_to_is_written_again_once_that_exists(self):
        review = self.fake.add_page(self.a, "Exam Review", "<p>Chapters 1–3</p>")
        start = self.fake.add_page(self.a, "Start", f'<a href="/courses/{self.a}/pages/{review["url"]}">review</a>')
        pages = self.fake.pages[self.a]
        pages.insert(pages.index(review), pages.pop(pages.index(start)))  # Start comes first in the copy
        self.start()
        failing = {"on": True}
        real = __import__("canvas.content_service", fromlist=["Push"]).Push.create_page

        def create_page(push, op):
            if failing["on"] and push.cid == self.b and op["label"] == "Exam Review":
                raise __import__("canvas.web", fromlist=["CanvasError"]).CanvasError("Canvas answered 500.", status=500)
            return real(push, op)
        with mock.patch("canvas.content_service.Push.create_page", create_page):
            shown = self.preview()
            body = {"fingerprint": shown["fingerprint"], "choices": shown["choices"], "request_id": rid(), "confirmation": "push"}
            push = self.client.post(f"{self.base}/push", json=body, headers=HEADERS).json
            for _ in range(200):
                push = self.client.get(f"{self.base}/pushes/{push['id']}").json
                if push["status"] != "running":
                    break
                time.sleep(0.05)
        b = push["progress"]["courses"][self.b]
        self.assertEqual(b["failed"], 2, b)  # Exam Review could not be made; Start's link to it could not be written
        self.assertTrue(any(e["label"] == "Start" and "another section's course" in e["message"] for e in b["errors"]), b)
        made = next(p for p in self.fake.pages[self.b] if p["title"] == "Start")
        self.assertIn(f"/courses/{self.a}/pages/", made["body"])
        failing["on"] = False
        b = self.course(self.preview(), self.b)
        self.assertEqual(sorted(op["op"] for op in b["ops"] if op["op"].endswith("_page")), ["create_page", "update_page"])
        self.push()
        review_b = next(p for p in self.fake.pages[self.b] if p["title"] == "Exam Review")
        self.assertIn(f'/courses/{self.b}/pages/{review_b["url"]}', made["body"])
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])

    def test_an_assignment_linking_to_a_page_the_push_makes_is_written_then_linked(self):
        notes = self.fake.add_page(self.a, "Project Notes", "<p>Read first</p>")
        project = self.fake.add_assignment(self.a, "Project", group="Projects")
        self.fake.assignment(self.a, project)["description"] = f'<p><a href="/courses/{self.a}/pages/{notes["url"]}">notes</a></p>'
        self.start()
        b = self.course(self.preview(), self.b)
        self.assertEqual(b["blocked"], [])
        push = self.push()
        self.assertEqual(push["progress"]["courses"][self.b]["failed"], 0, push)
        made = next(a for g in self.fake.groups[self.b] for a in g["assignments"] if a["name"] == "Project")
        notes_b = next(p for p in self.fake.pages[self.b] if p["title"] == "Project Notes")
        self.assertIn(f'/courses/{self.b}/pages/{notes_b["url"]}', made["description"])
        self.assertEqual([c["ops"] for c in self.preview()["courses"]], [[], []])


class MoveTests(ContentBase):
    def test_an_item_found_only_by_its_title_in_another_module_moves_only_when_ticked(self):
        materials_b = self.ids[self.b]["first"]
        self.fake.add_item(self.b, materials_b, "ExternalUrl", "Zoom link", external_url="https://zoom.example/508")
        self.start()
        self.save(lambda c: next(m for m in c["modules"] if m["name"] == "Week 1")["items"].append(
            {"type": "ExternalUrl", "title": "Zoom link", "external_url": "https://zoom.example/office"}))
        b = self.course(self.preview(), self.b)
        self.assertEqual([(e["label"], e["verdict"]) for e in b["attention"]], [("Week 1 › Zoom link", "elsewhere")])
        self.assertFalse([op for op in b["ops"] if op["op"] in ("move_item", "update_item", "create_item")])
        self.push()
        zoom = [(m["name"], i["external_url"]) for m in self.fake.modules[self.b] for i in m["items"] if i["title"] == "Zoom link"]
        self.assertEqual(zoom, [("Course Materials", "https://zoom.example/508")])  # untouched
        key = b["attention"][0]["key"]
        self.push({self.b: {"replace": [key]}})
        zoom = [(m["name"], i["external_url"]) for m in self.fake.modules[self.b] for i in m["items"] if i["title"] == "Zoom link"]
        self.assertEqual(zoom, [("Week 1", "https://zoom.example/office")])

    def test_an_item_moved_and_pointed_elsewhere_is_replaced_not_moved_too(self):
        self.start()
        self.push()
        def edit(content):
            materials, week = content["modules"][0], content["modules"][1]
            item = next(i for i in materials["items"] if i["type"] == "Page")
            materials["items"].remove(item)
            item["page"] = next(p["id"] for p in content["pages"] if p["title"] == "Home")
            week["items"].append(item)
        self.save(edit)
        for course in self.preview()["courses"]:
            ops = [op["op"] for op in course["ops"]]
            self.assertIn("replace_item", ops)
            self.assertNotIn("move_item", ops)
        push = self.push()
        self.assertEqual(sum(c["failed"] for c in push["progress"]["courses"].values()), 0, push)


class StopTests(ContentBase):
    def test_a_restart_waits_for_a_push_and_none_starts_once_faculty_is_stopping(self):
        """canvas/stopping.py (2026-10-04 review: a restart cut pushes off; only messages were waited for)."""
        self.start()
        self.save(lambda c: c["modules"][0].update(name="Course Materials (read first)"))
        writes, release = self.service.writes, threading.Event()
        real = __import__("canvas.content_service", fromlist=["Push"]).Push.run

        def held(push):
            release.wait(10)
            return real(push)
        with mock.patch("canvas.content_service.Push.run", held):
            shown = self.preview()
            body = {"fingerprint": shown["fingerprint"], "choices": shown["choices"], "request_id": rid(), "confirmation": "push"}
            push = self.client.post(f"{self.base}/push", json=body, headers=HEADERS).json
            self.assertEqual(writes.busy(), [f"push:{push['id']}"])
            self.assertEqual(self.client.get(f"{self.base}/pushes/{push['id']}").json["status"], "running")
            release.set()
            self.assertTrue(writes.wait_idle(10))
        self.assertEqual(self.client.get(f"{self.base}/pushes/{push['id']}").json["status"], "done")
        writes.close()
        self.save(lambda c: c["modules"][0].update(name="Course Materials"))
        shown = self.preview()
        refused = self.client.post(f"{self.base}/push", json={"fingerprint": shown["fingerprint"], "choices": shown["choices"],
                                                              "request_id": rid(), "confirmation": "push"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 503, refused.json)
        self.assertIn("restarting", refused.json["error"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM canvas_content_pushes").fetchone()[0], 1)
