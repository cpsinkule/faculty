"""ACCOMMODATIONS "DR QUIZ TAKERS" (requirement, 2026-10-15): one per-student flag owned by the term enrollment, kept as append-only
changes with who and when; the write sets a value with a request ID; the Accommodations overview carries it; the assistant's write is
its own and an Approve card; the flag stays out of the generic export and the shell state."""
import io
import json
import sqlite3
import uuid

from test_accommodations import workbook
from test_storage import HEADERS
from test_student_notes import Base

from assistant import policy
from rosters import connect

STATE_KEYS = {"on", "revision", "set_at", "set_by"}


class DrQuizTakerTests(Base):
    def route(self, enrollment=None):
        return f"/api/accommodations/dr-quiz-takers/{enrollment or self.alice}"

    def read(self, enrollment=None):
        return self.client.get(self.route(enrollment))

    def save(self, on=True, request_id="r1", enrollment=None):
        return self.client.post(self.route(enrollment), json={"on": on, "request_id": request_id}, headers=HEADERS)

    def changes(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM dr_quiz_taker_changes ORDER BY enrollment_id, revision")]

    def overview(self):
        answer = self.client.get("/api/accommodations/overview")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json["students"]

    def import_accommodations(self):
        content = workbook([["Example", "Alice", "001234567", "", "MATH", "140", "507", "Read", "2026-09-13", "Yes", "", ""],
                            ["Example", "Alice", "001234567", "", "MATH", "140", "508", "Read", "2026-09-13", "Yes", "", ""]])
        preview = self.client.post("/api/accommodations/preview", data={"file": (io.BytesIO(content), "export.xlsx"), "term": json.dumps(None)},
                                   headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        job = self.client.post("/api/accommodations/import", data={"file": (io.BytesIO(content), "export.xlsx"), "term": json.dumps(None),
            "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(job.status_code, 202, job.json)
        inbox = self.app.extensions["inbox"]
        self.assertTrue(inbox.process_one())
        self.assertEqual(inbox.get(job.json["id"])["status"], "completed")

    def test_unmarked_by_default_and_marked_with_who_and_when(self):
        fresh = self.read()
        self.assertEqual(fresh.status_code, 200, fresh.json)
        self.assertEqual((fresh.json["enrollment_id"], fresh.json["term_id"]), (self.alice, self.term))
        self.assertEqual(fresh.json["dr_quiz_taker"], {"on": False, "revision": 0, "set_at": None, "set_by": None})

        marked = self.save(True, "r1")
        self.assertEqual(marked.status_code, 200, marked.json)
        self.assertEqual((marked.json["enrollment_id"], marked.json["term_id"]), (self.alice, self.term))
        state = marked.json["dr_quiz_taker"]
        self.assertEqual(set(state), STATE_KEYS)
        self.assertEqual((state["on"], state["revision"], state["set_by"]), (True, 1, "workspace"))
        self.assertIsInstance(state["set_at"], str)
        self.assertRegex(state["set_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        self.assertEqual(self.read().json, marked.json)  # a GET agrees

        unmarked = self.save(False, "r2")
        self.assertEqual(unmarked.status_code, 200, unmarked.json)
        self.assertEqual((unmarked.json["dr_quiz_taker"]["on"], unmarked.json["dr_quiz_taker"]["revision"]), (False, 2))
        self.assertEqual(self.read().json["dr_quiz_taker"], unmarked.json["dr_quiz_taker"])
        self.assertEqual(self.read(self.bob).json["dr_quiz_taker"]["revision"], 0)  # another student's flag is their own
        rows = self.changes()
        self.assertEqual([(r["revision"], r["taker"], r["request_id"], r["actor"]) for r in rows],
                         [(1, 1, "r1", "workspace"), (2, 0, "r2", "workspace")])

    def test_a_request_id_is_kept_and_nothing_is_erased(self):
        first = self.save(True, "r1").json
        again = self.save(True, "r1")
        self.assertEqual((again.status_code, again.json), (200, first))  # a lost answer asked again records nothing
        self.assertEqual(self.save(False, "r1").status_code, 409)  # the same request ID asking for another value
        same = self.save(True, "r3")
        self.assertEqual((same.status_code, same.json["dr_quiz_taker"]), (200, first["dr_quiz_taker"]))  # set, not toggle
        self.assertEqual(len(self.changes()), 1)

        for body in ({"request_id": "b1"}, {"on": "yes", "request_id": "b2"}, {"on": 1, "request_id": "b3"}, {"on": None, "request_id": "b4"},
                     {"on": False, "request_id": "b5", "note": "extra"}, {"on": False}, {"on": False, "request_id": ""},
                     {"on": False, "request_id": "has spaces"}, {"on": False, "request_id": "x" * 101}, {"on": False, "request_id": 5}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.route(), json=body, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post(self.route(), data="not json", headers={**HEADERS, "Content-Type": "application/json"}).status_code, 400)
        self.assertEqual(len(self.changes()), 1)
        self.assertTrue(self.read().json["dr_quiz_taker"]["on"])

        for unknown in ("1999999999", "not-a-student", "0123456789"):
            with self.subTest(unknown=unknown):
                self.assertEqual(self.read(unknown).status_code, 404)
                self.assertEqual(self.save(False, "u-" + unknown, enrollment=unknown).status_code, 404)
        refused = self.client.post(self.route(), json={"on": False, "request_id": "h1"})
        self.assertNotEqual(refused.status_code, 200)  # writes need the workspace's same-origin headers
        self.assertEqual(len(self.changes()), 1)

        with sqlite3.connect(self.app.config["DATABASE"]) as db:
            for statement in ("UPDATE dr_quiz_taker_changes SET taker=0", "UPDATE dr_quiz_taker_changes SET actor='x'",
                              "DELETE FROM dr_quiz_taker_changes"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            with self.assertRaises(sqlite3.IntegrityError):  # a revision must follow the latest one
                db.execute("INSERT INTO dr_quiz_taker_changes(id,term_id,enrollment_id,revision,taker,request_id,actor,created_at) "
                           "VALUES ('x',?,?,9,0,'r9','workspace','2026-10-15T12:00:00Z')", (self.term, self.alice))
        self.assertEqual(len(self.changes()), 1)

    def test_the_flag_belongs_to_the_term_enrollment(self):
        self.import_accommodations()
        rows = [s for s in self.overview() if s["id"] == self.alice]
        self.assertEqual(len(rows), 2)  # Alice on rosters 507 and 508, one enrollment
        self.assertEqual(self.save(True, "r1").status_code, 200)
        rows = [s for s in self.overview() if s["id"] == self.alice]
        self.assertEqual(sorted(s["section"] for s in rows), [507, 508])
        self.assertEqual(rows[0]["dr_quiz_taker"], rows[1]["dr_quiz_taker"])
        self.assertEqual((rows[0]["dr_quiz_taker"]["on"], rows[0]["dr_quiz_taker"]["revision"]), (True, 1))
        with connect(self.app.config["DATABASE"]) as db:
            sections = {r["section_id"] for r in db.execute("SELECT section_id FROM roster_memberships WHERE enrollment_id=?", (self.alice,))}
            self.assertEqual(len(sections), 2)
            row = db.execute("SELECT term_id, enrollment_id FROM dr_quiz_taker_changes").fetchone()
            self.assertEqual(tuple(row), (self.term, self.alice))
        with sqlite3.connect(self.app.config["DATABASE"]) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO dr_quiz_taker_changes(id,term_id,enrollment_id,revision,taker,request_id,actor,created_at) "
                       "VALUES ('x','another-term',?,2,0,'r2','workspace','2026-10-15T12:00:00Z')", (self.alice,))
        self.assertEqual(len(self.changes()), 1)

    def test_the_overview_carries_the_flag(self):
        self.import_accommodations()
        before = self.overview()
        self.assertTrue(before)
        for student in before:
            self.assertEqual(student["dr_quiz_taker"], {"on": False, "revision": 0, "set_at": None, "set_by": None})
        self.save(True, "r1")
        after = self.overview()
        self.assertEqual([(s["id"], s["section"]) for s in after], [(s["id"], s["section"]) for s in before])  # the same students
        for student in after:
            expected = student["id"] == self.alice
            self.assertEqual(student["dr_quiz_taker"]["on"], expected)
            self.assertEqual(set(student["dr_quiz_taker"]), STATE_KEYS)
        strip = lambda rows: [{k: v for k, v in s.items() if k != "dr_quiz_taker"} for s in rows]
        self.assertEqual(strip(after), strip(before))  # nothing else in a row changes

    def test_the_box_is_offered_only_with_an_accommodation(self):
        # 
        self.assertFalse(self.read().json["offered"])
        self.assertEqual(self.save(True, "r1").status_code, 200)  # one marked earlier keeps the flag (and the box, to untick it)
        self.assertEqual((self.read().json["offered"], self.read().json["dr_quiz_taker"]["on"]), (False, True))
        self.import_accommodations()
        self.assertTrue(self.read().json["offered"])
        self.assertFalse(self.read(self.bob).json["offered"])
        self.assertTrue(all(s["dr_quiz_taker_offered"] for s in self.overview() if s["id"] == self.alice))

    def test_the_assistants_write_is_its_own_and_waits_for_approval(self):
        answer = self.client.post(self.route(), json={"on": True, "request_id": "a1"}, headers=HEADERS,
                                  environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(answer.json["dr_quiz_taker"]["set_by"], "assistant")
        self.assertEqual(self.changes()[0]["actor"], "assistant")
        self.assertEqual(self.save(False, "w1").json["dr_quiz_taker"]["set_by"], "workspace")
        rule = policy.base_rule("dr_quiz_takers.save")
        self.assertEqual((rule.tier, rule.request_id), ("approve", True))
        self.assertIn("dr_quiz_takers.save", policy.REQUEST_IDS)
        self.assertNotIn(policy.base_rule("dr_quiz_takers.student").tier, ("auto", "never"))

    def test_flags_stay_out_of_the_generic_export(self):
        self.assertEqual(self.save(True, "r1").status_code, 200)
        for route in ("/api/export", "/api/state"):
            answer = self.client.get(route)
            self.assertEqual(answer.status_code, 200, route)
            text = answer.get_data(as_text=True)
            for marker in ("dr_quiz_taker", "dr-quiz-taker"):
                self.assertNotIn(marker, text, route)


if __name__ == "__main__":
    import unittest
    unittest.main()
