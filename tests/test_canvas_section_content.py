"""One section's Canvas course edited directly (canvas/README.md "Editing one section directly"), offline against the loopback
FakeCanvas: what the page opens on, the preview's verdicts for the instructor's edits and for edits made in Canvas meanwhile, the
approved push and its receipt, and the term's copy, which a direct edit never changes."""
import copy
import time

from test_canvas import Seeded
from test_canvas_content import ContentBase, rid
from test_storage import HEADERS

from rosters import connect


class SectionContentTests(ContentBase):
    def direct_route(self, number=508):
        return f"/api/canvas/sections/{self.sections[number]}/content"

    def load(self, number=508):
        answer = self.client.get(self.direct_route(number))
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def preview(self, opened, draft, choices=None, number=508, status=200):
        answer = self.client.post(f"{self.direct_route(number)}/preview", json={"draft": draft, "base": opened["base"], "choices": choices or {}}, headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def push_direct(self, opened, draft, choices=None, number=508):
        shown = self.preview(opened, draft, choices, number)
        answer = self.client.post(f"{self.direct_route(number)}/push", json={"draft": draft, "base": opened["base"], "choices": shown["choices"],
                                  "fingerprint": shown["fingerprint"], "request_id": rid(), "confirmation": "push"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        push = answer.json
        for _ in range(200):
            push = self.client.get(f"{self.direct_route(number)}/pushes/{push['id']}").json
            if push["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(push["status"], "done", push)
        return push

    def rows(self, table):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]

    def test_malformed_drafts_get_validation_errors_before_planning(self):
        """CANVAS: malformed JSON values cannot bypass validation by failing the stale-ID scan first."""
        opened = self.load()
        for field in ("pages", "assignments", "modules"):
            for value in (None, {}, 7, "invalid", [{"id": []}], [{"id": {}}]):
                with self.subTest(field=field, value=value):
                    self.preview(opened, {**opened["content"], field: value}, status=400)
        for kind, field in (("Page", "page"), ("Assignment", "content")):
            for invalid in ([], {"invalid": True}):
                draft = copy.deepcopy(opened["content"])
                item = next(i for m in draft["modules"] for i in m["items"] if i["type"] == kind)
                item[field] = invalid if kind == "Page" else {"assignment": invalid}
                with self.subTest(kind=kind, invalid=invalid):
                    self.preview(opened, draft, status=400)
        self.assertEqual(self.rows("canvas_section_pushes"), [])
        self.assertEqual(self.fake.writes, [])

    def test_one_course_is_edited_and_pushed_without_the_terms_copy(self):
        self.fake.requests.clear()
        opened = self.load()
        self.assertFalse(any(r["method"] != "GET" for r in self.fake.requests))
        self.assertTrue(all(f"/courses/{self.b}/" in r["path"] for r in self.fake.requests if "/courses/" in r["path"]))
        content = opened["content"]
        week = self.ids[self.b]["week"]
        self.assertEqual([m["id"] for m in content["modules"]], [f"mod_c{self.ids[self.b]['first']}", f"mod_c{week}"])
        self.assertEqual((opened["course"]["course_id"], opened["managed"], opened["skipped"]), (self.b, [], []))
        self.assertEqual(self.load()["content"], content)  # the same keys every time
        # Unchanged: nothing to do.
        self.assertEqual(self.preview(opened, content)["courses"][0]["ops"], [])
        draft = copy.deepcopy(content)
        draft["modules"][1]["name"] = "Week 1 (508)"
        draft["modules"][1]["items"].append({"type": "SubHeader", "title": "Bring a calculator", "indent": 0, "published": True})
        next(p for p in draft["pages"] if p["title"] == "Office Hours")["body"] = "<p>BLOC 309, 508 only</p>"
        draft["modules"][0]["items"].reverse()
        shown = self.preview(opened, draft)["courses"][0]
        self.assertEqual({op: shown["counts"].get(op) for op in ("update_module", "create_item", "update_page", "order_items")},
                         {"update_module": 1, "create_item": 1, "update_page": 1, "order_items": 1}, shown["counts"])
        self.assertEqual(shown["attention"], [])
        push = self.push_direct(opened, draft)
        self.assertEqual(push["progress"]["courses"][self.b]["failed"], 0, push)
        modules = self.fake.modules[self.b]
        self.assertEqual(modules[1]["name"], "Week 1 (508)")
        self.assertEqual(modules[1]["items"][-1]["title"], "Bring a calculator")
        self.assertEqual([i["title"] for i in modules[0]["items"]], ["Textbook", "Office Hours", "Syllabus"])
        self.assertEqual(next(p for p in self.fake.pages[self.b] if p["title"] == "Office Hours")["body"], "<p>BLOC 309, 508 only</p>")
        self.assertEqual(self.fake.modules[self.a][1]["name"], "Week 1")  # the other section is untouched
        self.assertEqual(self.rows("canvas_content_links"), [])  # the term's copy records nothing of it
        self.assertEqual(self.rows("canvas_content_revisions"), [])
        [row] = self.rows("canvas_section_pushes")
        self.assertEqual((row["section_id"], row["course_id"], row["status"]), (self.sections[508], self.b, "done"))
        # Read back: nothing is left to do.
        again = self.load()
        self.assertEqual(self.preview(again, again["content"])["courses"][0]["ops"], [])

    def test_an_edit_made_in_canvas_since_the_page_was_opened_is_asked_about(self):
        opened = self.load()
        office = next(p for p in self.fake.pages[self.b] if p["title"] == "Office Hours")
        office["body"] = "<p>Moved to BLOC 400</p>"  # someone edits it in Canvas meanwhile
        draft = copy.deepcopy(opened["content"])
        draft["modules"][0]["name"] = "Materials"
        shown = self.preview(opened, draft)["courses"][0]
        self.assertEqual([op["op"] for op in shown["ops"]], ["update_module"])
        self.assertEqual([(e["label"], e["verdict"]) for e in shown["attention"]], [("Office Hours", "edited_in_canvas")])
        # Edited on both sides: replaced only when ticked.
        next(p for p in draft["pages"] if p["title"] == "Office Hours")["body"] = "<p>BLOC 319</p>"
        shown = self.preview(opened, draft)["courses"][0]
        self.assertEqual(([op["op"] for op in shown["ops"]], shown["attention"][0]["verdict"]), (["update_module"], "conflict"))
        self.push_direct(opened, draft, {self.b: {"replace": [shown["attention"][0]["key"]]}})
        self.assertEqual(office["body"], "<p>BLOC 319</p>")

    def test_removals_offer_what_was_removed_and_never_delete_a_page_unticked(self):
        opened = self.load()
        draft = copy.deepcopy(opened["content"])
        draft["modules"][0]["items"] = [i for i in draft["modules"][0]["items"] if i["type"] != "ExternalUrl"]
        draft["modules"][1]["items"] = []
        draft["pages"] = [p for p in draft["pages"] if p["title"] != "Home"]
        shown = self.preview(opened, draft)["courses"][0]
        extra = {e["label"]: e for e in shown["extra"]}
        self.assertTrue(extra["Course Materials › Textbook"]["remove"])
        self.assertTrue(extra["Week 1 › Chapter 1 Quiz"]["remove"])
        self.assertFalse(extra["Home"]["remove"])  # a page is deleted only when ticked
        self.push_direct(opened, draft)
        self.assertEqual([i["title"] for i in self.fake.modules[self.b][0]["items"]], ["Syllabus", "Office Hours"])
        self.assertEqual(self.fake.modules[self.b][1]["items"], [])
        self.assertTrue(any(p["title"] == "Home" for p in self.fake.pages[self.b]))
        self.assertTrue(self.fake.quizzes[self.b])  # removing an item keeps what it points to

    def test_an_item_pointing_at_one_of_two_quizzes_of_one_name_is_not_held_back(self):
        self.fake.add_quiz(self.b, "Chapter 1 Quiz")  # a second quiz of the same name
        opened = self.load()
        shown = self.preview(opened, opened["content"])["courses"][0]
        self.assertEqual((shown["blocked"], shown["ops"]), ([], []))
        draft = copy.deepcopy(opened["content"])
        quiz = next(i for i in draft["modules"][1]["items"] if i["type"] == "Quiz")
        quiz["indent"] = 1
        shown = self.preview(opened, draft)["courses"][0]
        self.assertEqual(([op["op"] for op in shown["ops"]], shown["blocked"]), (["update_item"], []))

    def test_the_terms_copy_asks_about_a_direct_edit_and_is_never_changed_by_it(self):
        self.start()
        links_before = len(self.rows("canvas_content_links"))
        opened = self.load()
        self.assertTrue(opened["managed"])  # the copy manages what it matched in 508
        draft = copy.deepcopy(opened["content"])
        next(p for p in draft["pages"] if p["title"] == "Office Hours")["body"] = "<p>Section 508's own hours</p>"
        self.push_direct(opened, draft)
        self.assertEqual(len(self.rows("canvas_content_links")), links_before)
        term = self.course(ContentBase.preview(self), self.b)
        self.assertEqual([(e["label"], e["verdict"]) for e in term["attention"]], [("Office Hours", "edited_in_canvas")])
        self.assertEqual(term["ops"], [])  # not replaced unless ticked

    def test_the_push_is_the_approved_plan(self):
        opened = self.load()
        draft = copy.deepcopy(opened["content"])
        draft["modules"][0]["name"] = "Materials"
        shown = self.preview(opened, draft)
        body = {"draft": draft, "base": opened["base"], "choices": shown["choices"], "fingerprint": shown["fingerprint"], "request_id": rid()}
        self.assertEqual(self.client.post(f"{self.direct_route()}/push", json=body, headers=HEADERS).status_code, 400)  # not confirmed
        self.assertEqual(self.client.post(f"{self.direct_route()}/push", json={**body, "confirmation": "push"}).status_code, 403)  # the workspace guard
        self.fake.modules[self.b][1]["name"] = "Changed in Canvas"
        answer = self.client.post(f"{self.direct_route()}/push", json={**body, "confirmation": "push"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(self.fake.writes, [])
        # Something edited here that Canvas deleted meanwhile: open the page again.
        del self.fake.modules[self.b][0]
        self.preview(opened, draft, status=409)
        # A tampered base is refused.
        answer = self.client.post(f"{self.direct_route()}/preview", json={"draft": draft, "base": {"module:x": {"sent": "a", "canvas": "b"}}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400)


class UnlinkedTests(Seeded):
    def test_a_section_without_a_canvas_course_cannot_be_opened(self):
        answer = self.client.get(f"/api/canvas/sections/{self.sections[507]}/content")
        self.assertEqual(answer.status_code, 409, answer.json)
