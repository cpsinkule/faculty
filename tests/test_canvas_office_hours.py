"""The Canvas Office Hours page drafted from the Term schedule (SCHEDULING "CANVAS OFFICE HOURS", canvas/office_hours.py, stage 1f):
the draft keeps the page's own heading and closing images and lists the office and meeting hours; "Put in Faculty's copy" records a
revision of the copy only (Canvas is written only by the ordinary Push). Offline against the loopback FakeCanvas."""
import unittest

from test_canvas_content import ContentBase, rid
from test_storage import HEADERS

from canvas import office_hours as oh

ENTRIES = [
    {"kind": "office_hours", "weekdays": [0], "start": "15:00", "end": "17:00", "room": "BLOC 309"},
    {"kind": "office_hours", "weekdays": [1, 3], "start": "10:00", "end": "11:00", "room": "BLOC 319"},
    {"kind": "office_hours", "weekdays": [4], "start": "15:00", "end": "17:00", "room": "BLOC 306"},
    {"kind": "meeting_hours", "weekdays": [1, 3], "start": "13:00", "end": "17:00", "room": "BLOC 319"},
    {"kind": "class", "weekdays": [0, 2, 4], "start": "08:00", "end": "08:50", "room": "HELD 107"},
]


class DraftTests(unittest.TestCase):
    def test_the_draft_keeps_the_pages_heading_and_images_and_lists_the_hours(self):
        self.assertEqual(oh.lines(ENTRIES, "office_hours"), ["Mondays, 3:00–5:00 PM · BLOC 309", "Tuesdays and Thursdays, 10:00–11:00 AM · BLOC 319",
                                                             "Fridays, 3:00–5:00 PM · BLOC 306"])
        self.assertEqual(oh.lines(ENTRIES, "meeting_hours"), ["Tuesdays and Thursdays, 1:00–5:00 PM · BLOC 319"])
        body = ('<h2 style="background: #EAEAEA;"><span>Office Hours</span></h2>\n<h2>In-person</h2><ul><li>Mondays 3PM-5PM</li></ul>'
                '\n<p><span><img src="https://canvas.example/banner.png" alt=""></span></p>\n')
        draft = oh.draft(body, ENTRIES)
        self.assertTrue(draft.startswith('<h2 style="background: #EAEAEA;"><span>Office Hours</span></h2>'))
        self.assertTrue(draft.rstrip().endswith('<img src="https://canvas.example/banner.png" alt=""></span></p>'))
        self.assertNotIn("3PM-5PM", draft)
        self.assertIn("<li>Fridays, 3:00–5:00 PM · BLOC 306</li>", draft)
        self.assertIn("Or by appointment", draft)
        self.assertEqual(oh.text(oh.draft(draft, ENTRIES)), oh.text(draft))  # drafting a draft changes nothing
        self.assertIn("A time we agree on by email", oh.draft("<p>plain</p>", ENTRIES[:1]))
        self.assertTrue(oh.draft("<p>plain</p>", ENTRIES).startswith("<h2>Office Hours</h2>"))
        self.assertIsNone(oh.find({"pages": [{"title": "Home"}]}))


class CopyTests(ContentBase):
    def test_putting_the_draft_in_the_copy_changes_the_copy_only(self):
        self.start()
        url = f"{self.base}/office-hours"
        self.assertEqual(self.client.get(url).json["available"], False)  # no Term schedule yet
        schedule = self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={
            "request_id": rid(), "base_revision": 0, "first_day": "2026-08-24", "last_day": "2026-12-09",
            "entries": [{**e, "section_id": self.sections[507]} if e["kind"] == "class" else e for e in ENTRIES]})
        self.assertEqual(schedule.status_code, 200, schedule.json)
        found = self.client.get(url).json
        self.assertEqual((found["available"], found["differs"], found["title"]), (True, True, "Office Hours"))
        self.assertEqual(found["meeting"], ["Tuesdays and Thursdays, 1:00–5:00 PM · BLOC 319"])
        self.assertEqual(self.client.post(url, headers=HEADERS, json={"request_id": rid(), "base_revision": found["revision"]}).status_code, 400)
        stale = self.client.post(url, headers=HEADERS, json={"request_id": rid(), "base_revision": found["revision"] - 1, "confirmation": "put"})
        self.assertEqual(stale.status_code, 409)
        import copy
        before = copy.deepcopy(self.fake.pages)
        put = self.client.post(url, headers=HEADERS, json={"request_id": rid(), "base_revision": found["revision"], "confirmation": "put"})
        self.assertEqual(put.status_code, 200, put.json)
        after = self.client.get(url).json
        self.assertEqual((after["differs"], after["revision"]), (False, found["revision"] + 1))
        page = next(p for p in self.client.get(self.base).json["content"]["pages"] if p["title"] == "Office Hours")
        self.assertIn("Mondays, 3:00–5:00 PM · BLOC 309", page["body"])
        self.assertEqual(self.fake.pages, before)  # Canvas itself is untouched until a Push


if __name__ == "__main__":
    unittest.main()
