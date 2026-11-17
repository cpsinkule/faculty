"""Student notes (AGENTS.md "Student notes"): a note belongs to the student's term enrollment; its date and words are revisions saved
on a base revision with a request ID; a delete and a restore are revisions too, and nothing is erased; the picker lists each student
once with their sections and never a UIN; the assistant's writes are recorded as its own and wait for approval; notes stay out of the
generic export."""
import io
import os
import sqlite3
import tempfile
import unittest

from browser_course_pages import seed_course
from test_rosters import csv_bytes
from test_storage import HEADERS

from app import create_app
from assistant import policy
from rosters import connect


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        seed_course(self.client, sections=[507, 508])
        self.group = self.client.get("/api/state").json["course_groups"][0]["id"]
        for number in (507, 508):
            response = self.client.post(f"/api/course-groups/{self.group}/sections/{number}/roster",
                                        data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
            self.assertEqual(response.status_code, 201, response.json)
        with connect(self.app.config["DATABASE"]) as db:
            enrollments = {r["uin"]: r["id"] for r in db.execute("SELECT id, uin FROM student_enrollments")}
            self.term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
        self.alice, self.bob = enrollments["001234567"], enrollments["123456789"]

    def write(self, enrollment=None, request_id="n1", **fields):
        body = {"enrollment_id": enrollment or self.alice, "date": "2026-10-01",
                "text": "Scheduled one-on-one office hours today and didn't show.", "request_id": request_id, **fields}
        return self.client.post("/api/student-notes", json=body, headers=HEADERS)

    def change(self, note_id, base, request_id, action="", **fields):
        route = f"/api/student-notes/{note_id}" + (f"/{action}" if action else "")
        return self.client.post(route, json={"base_revision": base, "request_id": request_id, **fields}, headers=HEADERS)

    def notes(self, enrollment=None):
        return self.client.get(f"/api/student-notes/students/{enrollment or self.alice}").json["notes"]

    def revisions(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT note_id, revision, note_date, body, removed, actor FROM student_note_revisions ORDER BY rowid")]


class WriteTests(Base):
    def test_a_note_is_written_changed_and_listed_newest_day_first(self):
        self.assertEqual(self.notes(), [])
        created = self.write()
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual((created.json["code"], created.json["term_label"], created.json["enrollment_id"]), ("MATH 140", "Fall 2026", self.alice))
        first = created.json["notes"][0]
        self.assertEqual((first["id"], first["date"], first["revision"], first["edited"]), (created.json["note_id"], "2026-10-01", 1, False))
        self.assertEqual(self.write().json["note_id"], first["id"])  # a lost answer sent again records nothing more
        older = self.write(request_id="n2", date="2026-09-29", text="Came to office hours about the chain rule.\nWell prepared.")
        self.assertEqual([n["date"] for n in older.json["notes"]], ["2026-10-01", "2026-09-29"])
        self.assertEqual(older.json["notes"][1]["text"], "Came to office hours about the chain rule.\nWell prepared.")

        changed = self.change(first["id"], 1, "e1", text="Scheduled office hours at 2 pm and didn't show; emailed them.")
        self.assertEqual(changed.status_code, 200, changed.json)
        note = next(n for n in changed.json["notes"] if n["id"] == first["id"])
        self.assertEqual((note["revision"], note["edited"], changed.json["note_revision"]), (2, True, 2))
        self.assertEqual(self.change(first["id"], 1, "e1", text="ignored: the same request").json["note_revision"], 2)
        self.assertEqual(self.change(first["id"], 2, "e2", text=note["text"]).json["note_revision"], 2)  # identical: nothing recorded
        self.assertEqual(self.change(first["id"], 1, "e3", date="2026-09-30").status_code, 409)  # the page showed revision 1
        moved = self.change(first["id"], 2, "e4", date="2026-09-28").json
        self.assertEqual([n["date"] for n in moved["notes"]], ["2026-09-29", "2026-09-28"])
        back = self.change(first["id"], 3, "e5", date="2026-10-01", text="Scheduled one-on-one office hours today and didn't show.").json
        self.assertFalse(next(n for n in back["notes"] if n["id"] == first["id"])["edited"])  # back to its first words and day
        self.assertEqual(len(self.revisions()), 5)
        self.assertEqual(self.notes(self.bob), [])  # another student's notes are their own

    def test_delete_and_restore_are_revisions_and_nothing_is_erased(self):
        note = self.write().json["notes"][0]
        removed = self.change(note["id"], 1, "d1", action="remove")
        self.assertEqual((removed.status_code, removed.json["notes"], removed.json["note_revision"]), (200, [], 2))
        self.assertEqual(self.change(note["id"], 2, "x1", text="changed while deleted").status_code, 409)
        restored = self.change(note["id"], 2, "r1", action="restore").json
        self.assertEqual(([n["id"] for n in restored["notes"]], restored["note_revision"]), ([note["id"]], 3))
        self.assertEqual(self.change(note["id"], 3, "r2", action="restore").json["note_revision"], 3)  # already there: nothing recorded
        self.assertEqual([r["removed"] for r in self.revisions()], [0, 1, 0])
        with sqlite3.connect(self.app.config["DATABASE"]) as db:
            for statement in ("UPDATE student_notes SET actor='x'", "DELETE FROM student_notes",
                              "UPDATE student_note_revisions SET body='x'", "DELETE FROM student_note_revisions"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            with self.assertRaises(sqlite3.IntegrityError):  # a revision must follow the latest one
                db.execute("INSERT INTO student_note_revisions(id,note_id,revision,note_date,body,removed,request_id,actor,created_at) "
                           "VALUES ('x',?,9,'2026-10-01','x',0,'r9','workspace','now')", (note["id"],))

    def test_bad_notes_are_refused_before_anything_is_written(self):
        for fields in ({"text": ""}, {"text": "   \n "}, {"text": "x" * 5001}, {"text": "bell\x07"}, {"text": 5},
                       {"date": "2026-02-30"}, {"date": "Oct 1"}, {"date": None}, {"request_id": ""}, {"request_id": "has spaces"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.write(**fields).status_code, 400)
        self.assertEqual(self.write(enrollment="1999999999").status_code, 404)
        self.assertEqual(self.write(enrollment="not-a-student").status_code, 404)
        self.assertEqual(self.client.get("/api/student-notes/students/1999999999").status_code, 404)
        self.assertEqual(self.change("00000000-0000-0000-0000-000000000000", 1, "c1", text="x").status_code, 404)
        self.assertEqual(self.change("nope", 1, "c1", action="remove").status_code, 404)
        refused = self.client.post("/api/student-notes", json={"enrollment_id": self.alice, "date": "2026-10-01", "text": "x", "request_id": "h"})
        self.assertNotEqual(refused.status_code, 201)  # writes need the workspace's same-origin headers
        self.assertEqual(self.revisions(), [])
        note = self.write().json["notes"][0]
        self.assertEqual(self.client.post(f"/api/student-notes/{note['id']}", json={"request_id": "c2", "text": "y"}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.write(enrollment=self.bob).status_code, 409)  # request n1 already wrote Alice's note
        self.assertEqual(len(self.revisions()), 1)


class OwnerTests(Base):
    def test_a_note_belongs_to_the_term_enrollment_whichever_section_shows_it(self):
        note = self.write().json["notes"][0]
        with connect(self.app.config["DATABASE"]) as db:
            sections = {r["section_id"] for r in db.execute("SELECT section_id FROM roster_memberships WHERE enrollment_id=?", (self.alice,))}
            self.assertEqual(len(sections), 2)  # Alice is on both sections' rosters with one enrollment
            row = db.execute("SELECT term_id, enrollment_id FROM student_notes WHERE id=?", (note["id"],)).fetchone()
            self.assertEqual(tuple(row), (self.term, self.alice))
        with sqlite3.connect(self.app.config["DATABASE"]) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO student_notes VALUES ('x','another-term',?,'r','workspace','now')", (self.alice,))

    def test_students_to_choose_from_are_listed_once_with_their_sections_and_never_a_uin(self):
        students = self.client.get("/api/student-notes/students").json["groups"][0]["students"]
        self.assertEqual([(s["name"], s["sections"], s["dropped"]) for s in students],
                         [("Example, Alice", [507, 508], False), ("Example, Bob", [507, 508], False)])
        self.assertNotIn("001234567", str(students))
        drop = f"/api/course-groups/{self.group}/sections/%d/roster/drop"
        self.assertEqual(self.client.post(drop % 507, json={"enrollment_ids": [self.alice], "request_id": "drop1", "source": "roster"}, headers=HEADERS).status_code, 200)
        alice = self.client.get("/api/student-notes/students").json["groups"][0]["students"][0]
        self.assertFalse(alice["dropped"])  # still on Section 508
        self.client.post(drop % 508, json={"enrollment_ids": [self.alice], "request_id": "drop2", "source": "roster"}, headers=HEADERS)
        self.assertTrue(self.client.get("/api/student-notes/students").json["groups"][0]["students"][0]["dropped"])
        found = self.client.post("/api/student-notes/students/search", json={"digits": "0012345"}, headers=HEADERS).json["students"]
        self.assertEqual((found[0]["name"], found[0]["enrollment_id"], found[0]["section_number"]), ("Example, Alice", self.alice, "507, 508"))
        self.assertNotIn("001234567", str(found))
        self.assertEqual(self.client.post("/api/student-notes/students/search", json={"digits": 12}, headers=HEADERS).status_code, 400)


class BoundaryTests(Base):
    def test_the_assistants_writes_are_its_own_and_wait_for_approval(self):
        answer = self.client.post("/api/student-notes", json={"enrollment_id": self.alice, "date": "2026-10-01", "text": "From the assistant", "request_id": "a1"},
                                  headers=HEADERS, environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(answer.status_code, 201, answer.json)
        self.assertEqual(self.revisions()[0]["actor"], "assistant")
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT actor FROM student_notes").fetchone()[0], "assistant")
        self.write(request_id="w1")
        self.assertEqual(self.revisions()[1]["actor"], "workspace")
        for endpoint in ("student_notes.create", "student_notes.revise", "student_notes.change"):
            self.assertEqual(policy.base_rule(endpoint).tier, "approve", endpoint)
        self.assertEqual(policy.base_rule("student_notes.search").tier, "read")

    def test_notes_stay_out_of_the_generic_export_and_the_shell_state(self):
        self.write(text="Private: missed office hours")
        for route in ("/api/export", "/api/state"):
            answer = self.client.get(route)
            self.assertEqual(answer.status_code, 200, route)
            self.assertNotIn("missed office hours", answer.get_data(as_text=True), route)


if __name__ == "__main__":
    unittest.main()
