"""Excused absences (AGENTS.md "Excused absences"): an absence belongs to one section membership; its facts and the instructor's
decision are revisions saved on a base revision with a request ID; documentation is an upload kept in the section's verified storage
or a reference to an attachment kept in the student's communications record; an absence decided excused changes no column but the
iClicker sessions within its dates (ICLICKER-CREDIT, tested with the fake iClicker in test_iclicker.py) and no exam record, and there
is no route that excuses covered work."""
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import os
import tempfile
import unittest

from browser_course_pages import seed_course
from test_rosters import csv_bytes
from test_storage import HEADERS

from app import create_app
from rosters import connect

PDF = b"%PDF-1.4\n% a doctor's note\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        course = seed_course(self.client, sections=[507, 508])
        state = self.client.get("/api/state").json
        self.group = state["course_groups"][0]["id"]
        for number in (507, 508):
            route = f"/api/course-groups/{self.group}/sections/{number}/roster"
            response = self.client.post(route, data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
            self.assertEqual(response.status_code, 201, response.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}
            self.term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
            self.alice = db.execute("SELECT m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id "
                                    "WHERE e.uin='001234567' AND m.section_id=?", (self.sections[507],)).fetchone()[0]
        self.definition = course["definition_id"]

    def record(self, request_id="a1", **facts):
        body = {"section_id": self.sections[507], "enrollment_id": self.alice, "first_date": "2026-09-17", "last_date": "2026-09-18",
                "reason": "illness", "details": "Flu", "request_id": request_id, **facts}
        return self.client.post("/api/absences", json=body, headers=HEADERS)

    def revise(self, absence, request_id, **facts):
        return self.client.post(f"/api/absences/{absence['id']}", json={"base_revision": absence["revision"], "request_id": request_id, **facts},
                                headers=HEADERS)

    def setup_gradebook(self, items):
        response = self.client.post(f"/api/gradebook/terms/{self.term}/setup",
                                    json={"base_revision": None, "request_id": "gb-setup", "setup": {"items": items}}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return {item["title"]: item["id"] for item in response.json["setup"]["items"]}


class RecordTests(Base):
    def test_an_absence_is_recorded_revised_and_listed_with_the_students_name(self):
        created = self.record()
        self.assertEqual(created.status_code, 201, created.json)
        absence = created.json
        self.assertEqual((absence["name"], absence["section"], absence["status"], absence["revision"], absence["dates"]),
                         ("Example, Alice", 507, "pending", 1, "Thu, Sep 17 – Fri, Sep 18"))
        again = self.record()  # a lost answer sent again
        self.assertEqual(again.json["id"], absence["id"])
        decided = self.revise(absence, "d1", status="excused", decision_note="Doctor's note received")
        self.assertEqual((decided.json["status"], decided.json["revision"]), ("excused", 2))
        same = self.revise(decided.json, "d2", status="excused")
        self.assertEqual(same.json["revision"], 2)  # identical: nothing recorded
        stale = self.revise(absence, "d3", status="not_excused")
        self.assertEqual(stale.status_code, 409)
        listed = self.client.get("/api/absences").json
        self.assertEqual([(a["name"], a["status"]) for a in listed["absences"]], [("Example, Alice", "excused")])
        self.assertEqual(listed["counts"]["excused"], 1)
        card = self.client.get(f"/api/absences/students/{self.sections[507]}/{self.alice}").json
        self.assertEqual([a["id"] for a in card["absences"]], [absence["id"]])
        self.assertNotIn("uin", str(listed).lower().replace("uin_", ""))

    def test_bad_facts_are_refused_and_the_rows_are_append_only(self):
        self.assertEqual(self.record(first_date="2026-09-18", last_date="2026-09-17").status_code, 400)
        self.assertEqual(self.record(request_id="b2", reason="vacation").status_code, 400)
        self.assertEqual(self.record(request_id="b3", first_date="2026-02-30").status_code, 400)
        absence = self.record(request_id="b4").json
        with connect(self.app.config["DATABASE"]) as db:
            with self.assertRaises(Exception):
                db.execute("UPDATE absence_revisions SET status='excused' WHERE absence_id=?", (absence["id"],))
            with self.assertRaises(Exception):
                db.execute("DELETE FROM absences WHERE id=?", (absence["id"],))

    def test_students_to_choose_from_and_a_uin_search_name_no_uin(self):
        groups = self.client.get("/api/absences/students").json["groups"]
        self.assertEqual({(s["name"], s["section"]) for s in groups[0]["students"]},
                         {("Example, Alice", 507), ("Example, Bob", 507), ("Example, Alice", 508), ("Example, Bob", 508)})
        found = self.client.post("/api/absences/students/search", json={"digits": "0012345"}, headers=HEADERS).json["students"]
        self.assertTrue(found and found[0]["name"] == "Example, Alice")
        self.assertNotIn("001234567", str(found))


class DocumentTests(Base):
    def test_an_upload_is_kept_checked_removed_and_restored(self):
        absence = self.record().json
        upload = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                  data={"file": (io.BytesIO(PDF), "note.pdf"), "request_id": "u1"})
        self.assertEqual(upload.status_code, 200, upload.json)
        document = upload.json["documents"][0]
        self.assertEqual((document["name"], document["content_type"], document["removed"]), ("note.pdf", "application/pdf", False))
        again = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                 data={"file": (io.BytesIO(PDF), "note.pdf"), "request_id": "u1"})
        self.assertEqual(len(again.json["documents"]), 1)
        got = self.client.get(document["url"])
        self.assertEqual((got.status_code, got.data), (200, PDF))
        self.assertIn("attachment", got.headers["Content-Disposition"])
        self.assertEqual(got.headers["X-Content-Type-Options"], "nosniff")
        removed = self.client.post(f"{document['url']}/remove", json={"request_id": "r1"}, headers=HEADERS).json
        self.assertTrue(removed["documents"][0]["removed"])
        self.assertEqual(removed["documents"][0]["id"], document["id"])
        self.assertEqual(self.client.get("/api/absences").json["absences"][0]["documents"], 0)
        restored = self.client.post(f"{document['url']}/restore", json={"request_id": "r2"}, headers=HEADERS).json
        self.assertFalse(restored["documents"][0]["removed"])

    def test_competing_uploads_with_one_request_id_have_one_winner(self):
        # ABSENCES: an idempotency collision after the initial read is still a 409, never a catalog error.
        from werkzeug.exceptions import Conflict
        absence = self.record().json
        service = self.app.extensions["absences"]
        original = service._absence
        entered = threading.local()
        barrier = threading.Barrier(2)

        def read(db, ident):
            result = original(db, ident)
            if not getattr(entered, "initial", False):
                entered.initial = True
                barrier.wait(timeout=10)
            return result

        def upload(content):
            try:
                service.upload(absence["id"], content, "note.pdf", "competing")
                return 200
            except Conflict:
                return 409

        with patch.object(service, "_absence", side_effect=read), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(upload, content) for content in (PDF, PDF + b"different")]
            self.assertEqual(sorted(future.result(timeout=30) for future in futures), [200, 409])
        self.assertEqual(len(service.get(absence["id"])["documents"]), 1)

    def test_only_documents_and_photos_are_accepted(self):
        absence = self.record().json
        refused = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                   data={"file": (io.BytesIO(b"<html>"), "note.pdf"), "request_id": "u1"})
        self.assertEqual(refused.status_code, 400)

    def test_a_message_attachment_must_belong_to_the_student(self):
        absence = self.record().json
        refused = self.client.post(f"/api/absences/{absence['id']}/documents/message", json={"message_id": "nope", "index": 0, "request_id": "m1"},
                                   headers=HEADERS)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("not in this student's communications record", refused.json["error"])


class DeadlineTests(Base):
    """DECISION DEADLINE (requirement, 2026-10-04): an undecided absence becomes not excused after the end of its documentation
    deadline; asked, they chose that one with documentation keeps waiting for them and is brought to their attention. Since
    2026-10-05 ("fix the holidays and the too strict documentation deadline") that is Rule 7.3.1.1's third business day after its
    last day, university holidays skipped (SCHEDULING BUSINESS DAYS)."""

    def test_the_deadline_is_the_day_after_the_third_business_day_and_holidays_are_not_business_days(self):
        from absences.service import decision_deadline, documentation_due, notice_due
        self.assertEqual(decision_deadline("2026-10-02"), "2026-10-08")  # Fri: Mon, Tue, Wed, so from Thu
        self.assertEqual(decision_deadline("2026-10-05"), "2026-10-09")  # Mon: Tue, Wed, Thu, so from Fri
        self.assertEqual(decision_deadline("2026-10-08"), "2026-10-14")  # Thu: Fri, Mon, Tue, so from Wed
        self.assertEqual(decision_deadline("2026-10-03"), "2026-10-08")  # Sat: Mon, Tue, Wed, so from Thu
        self.assertEqual(documentation_due("2026-11-24"), "2026-12-01")  # Tue: Wed, (Thanksgiving Thu, Fri), Mon, Tue
        self.assertEqual(documentation_due("2026-09-04"), "2026-09-10")  # Fri: (Labor Day), Tue, Wed, Thu
        self.assertEqual(notice_due("2026-10-02"), "2026-10-06")  # Rule 7.1.1: the second business day
        self.assertEqual(notice_due("2026-11-25"), "2026-12-01")  # Wed: (Thanksgiving Thu, Fri), Mon, Tue
        self.assertEqual(notice_due("2026-12-22"), "2027-01-04")  # Tue: Wed 23, (Winter Break Dec 24 – Jan 1), Mon Jan 4

    def test_an_undocumented_undecided_absence_becomes_not_excused_once_and_a_documented_one_is_brought_to_you(self):
        service = self.app.extensions["absences"]
        dates = {"first_date": "2026-09-24", "last_date": "2026-09-25"}  # Fri: the deadline is Thu, Oct 1
        plain, documented = self.record("plain", **dates).json, self.record("documented", **dates).json
        decided = self.record("decided", status="excused", **dates).json
        upload = self.client.post(f"/api/absences/{documented['id']}/documents", headers=HEADERS,
                                  data={"file": (io.BytesIO(PDF), "note.pdf"), "request_id": "doc"})
        self.assertEqual(upload.status_code, 200, upload.json)
        self.assertFalse(service.expire_one(today="2026-09-30"))  # Wed: still the third business day
        self.assertTrue(service.expire_one(today="2026-10-01"))
        self.assertFalse(service.expire_one(today="2026-10-01"))  # nothing else is due: the documented one waits for you
        flipped = self.client.get(f"/api/absences/{plain['id']}").json
        self.assertEqual(flipped["status"], "not_excused")
        self.assertTrue(flipped["decision_note"].startswith("Not excused automatically: no documentation and no decision by the end of "
                                                            "Wed, Sep 30, the third business day after the absence."), flipped["decision_note"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT actor FROM absence_revisions WHERE absence_id=? ORDER BY revision DESC LIMIT 1",
                                        (plain["id"],)).fetchone()[0], "automatic")
        self.assertEqual(self.client.get(f"/api/absences/{documented['id']}").json["status"], "pending")
        self.assertEqual(self.client.get(f"/api/absences/{decided['id']}").json["status"], "excused")
        listed = {item["id"]: item for item in self.client.get("/api/absences").json["absences"]}
        self.assertEqual((listed[documented["id"]]["past_deadline"], listed[plain["id"]]["past_deadline"]), (True, False))
        # Home's absences card leads with it (one card: the sidebar badge counts each absence once).
        card = next(item for item in self.client.get("/api/workspace/overview").json["attention"] if item["key"] == "absences")
        self.assertEqual((card["label"], card["count"], card["past_deadline"]), ("Absences past their deadline", 1, 1))
        self.assertTrue(card["detail"].startswith("1 documented absence is past the third business day after the absence."), card["detail"])
        # Reopened by the instructor, an absence the worker decided stays theirs.
        current = self.client.get(f"/api/absences/{plain['id']}").json
        self.assertEqual(self.revise(current, "reopen", status="pending").status_code, 200)
        self.assertFalse(service.expire_one(today="2026-10-30"))
        self.assertEqual(self.client.get(f"/api/absences/{plain['id']}").json["status"], "pending")


class DeathTests(Base):
    """DEATHS WAIT (requirement, 2026-10-05): an absence for a death, whatever
    its reason, waits for the instructor past its documentation deadline; the fact is carried to later revisions."""

    def test_an_undocumented_death_past_its_deadline_waits_for_you(self):
        service = self.app.extensions["absences"]
        dates = {"first_date": "2026-10-05", "last_date": "2026-10-06"}  # documentation due Fri, Oct 9
        death = self.record("death", reason="other", details="A death in her family; arrangements not known", death=True, **dates)
        self.assertEqual(death.status_code, 201, death.json)
        self.assertTrue(death.json["death"])
        self.assertEqual(self.record("bad", death="yes", **dates).status_code, 400)
        self.assertFalse(service.expire_one(today="2026-10-10"))
        self.assertEqual(self.client.get(f"/api/absences/{death.json['id']}").json["status"], "pending")
        noted = self.revise(death.json, "note", details="Funeral Friday")  # a later revision keeps it
        self.assertTrue(noted.json["death"])
        self.assertFalse(service.expire_one(today="2026-10-20"))
        cleared = self.revise(noted.json, "not-a-death", death=False)  # not a death after all: the deadline applies again
        self.assertFalse(cleared.json["death"])
        self.assertTrue(service.expire_one(today="2026-10-20"))
        self.assertEqual(self.client.get(f"/api/absences/{death.json['id']}").json["status"], "not_excused")


class NoticeTests(Base):
    """RULE 7 NOTICE (requirement, 2026-10-05): an absence names the student's message that
    first told the instructor, and reads whether it came before the absence, after it began but by the end of the second business
    day after its last day, or late, on the campus wall clock."""

    def message(self, ident, at, author="student", enrollment=None):
        from communications.record import record
        with self.app.extensions["rosters"].lock(), connect(self.app.config["DATABASE"]) as db:
            made = record(db, "gmail", ident, {"sent_at": at, "author_role": author, "author": {"name": "Alice"}, "recipients": [],
                                               "subject": "Absence", "body_text": "I will miss class.", "attachments": [], "context": {"thread_id": "t1"}},
                          [(enrollment or self.alice, self.term, "author" if author == "student" else "to", "roster_address")], "t1")
            db.commit()
        return made["message_id"]

    def test_the_timing_reads_the_campus_day_against_rule_7(self):
        from absences.service import notice_timing
        timing = lambda at: notice_timing("2026-10-05", "2026-10-06", at)["timing"]
        self.assertEqual(timing("2026-10-04T23:30:00Z"), "advance")  # Sun 6:30 PM
        self.assertEqual(timing("2026-10-05T04:30:00Z"), "advance")  # UTC's Mon is Sun 11:30 PM on campus
        self.assertEqual(timing("2026-10-05T13:00:00Z"), "after")  # Mon 8 AM, the absence's first day
        self.assertEqual(timing("2026-10-08T22:00:00Z"), "after")  # Thu 5 PM, the second business day after Tue
        self.assertEqual(timing("2026-10-09T05:30:00Z"), "late")  # Fri 12:30 AM
        self.assertEqual(notice_timing("2026-10-05", "2026-10-06", None), {"timing": "none", "label": "No notice on record", "due": "2026-10-08"})

    def test_an_absence_names_the_students_own_message_and_the_list_says_when_it_came(self):
        early = self.message("g-early", "2026-09-16T15:00:00Z")
        late = self.message("g-late", "2026-09-24T15:00:00Z")  # Thu: after Tue, Sep 22, the second business day after Fri
        mine = self.message("g-mine", "2026-09-16T16:00:00Z", author="instructor")
        with connect(self.app.config["DATABASE"]) as db:
            other = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? LIMIT 1",
                               (self.sections[507], self.alice)).fetchone()[0]
        theirs = self.message("g-other", "2026-09-16T15:00:00Z", enrollment=other)
        for refused, says in ((mine, "the student wrote"), (theirs, "not in this student's communications record"), ("not-a-message", "not in this")):
            answer = self.record(f"r-{refused[:8]}", notice_message_id=refused)
            self.assertEqual(answer.status_code, 400, answer.json)
            self.assertIn(says, answer.json["error"])
        created = self.record("with-notice", notice_message_id=early)
        self.assertEqual(created.status_code, 201, created.json)
        absence = created.json
        self.assertEqual((absence["notice_message_id"], absence["notice"]["timing"], absence["notice"]["due"], absence["documentation_due"]),
                         (early, "advance", "2026-09-22", "2026-09-23"))
        self.assertEqual(absence["notice"]["at"], "2026-09-16T15:00:00Z")
        changed = self.revise(absence, "later", notice_message_id=late)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual((changed.json["revision"], changed.json["notice"]["timing"]), (2, "late"))
        decided = self.revise(changed.json, "decide", status="excused")  # a later revision keeps its notice
        listed = {item["id"]: item for item in self.client.get("/api/absences").json["absences"]}
        self.assertEqual((listed[absence["id"]]["notice_message_id"], listed[absence["id"]]["notice"]["timing"]), (late, "late"))
        cleared = self.revise(decided.json, "clear", notice_message_id="")
        self.assertEqual((cleared.json["notice_message_id"], cleared.json["notice"]["timing"]), ("", "none"))
        self.assertEqual(self.record("without").json["notice"]["timing"], "none")


class GradebookTests(Base):
    def test_an_excused_absence_leaves_other_columns_and_the_exam_records_alone(self):
        """(ABSENCES NO-GRADEBOOK), then
        the same day: "There is one way an excused absence should interact with the gradebook: iClicker assignments"
        (ICLICKER-CREDIT): a column dated within the absence that is not an iClicker session is never written."""
        self.setup_gradebook([{"title": "Quiz 3", "points": "10", "due_date": "2026-09-17"}])
        docs = self.app.extensions["documents"]
        assignment = docs.create_assignment(self.definition, "exam-1", "Exam 1")
        exams = self.app.extensions["exams"]
        event = exams.create({"term_id": self.term, "assignment_id": assignment["id"], "section_ids": [self.sections[507], self.sections[508]]})
        exams.save_sitting(event["id"], {"version": event["version"], "label": "Main", "date": "2026-09-18", "time": "7 PM", "kind": "main"})
        before = [(p["section_id"], p["enrollment_id"], p["expectation"], p["version"]) for p in exams.get(event["id"])["participants"]]

        absence = self.revise(self.record().json, "d1", status="excused", decision_note="Doctor's note received").json
        self.assertEqual(absence["status"], "excused")
        self.assertNotIn("applications", absence)
        self.assertEqual((absence["iclicker"], "iclicker_credit" in absence), ([], False))
        self.assertEqual(self.client.get(f"/api/gradebook/terms/{self.term}").json["cells"], {})
        self.assertEqual([(p["section_id"], p["enrollment_id"], p["expectation"], p["version"]) for p in exams.get(event["id"])["participants"]], before)
        self.assertEqual(self.client.get(f"/api/absences/{absence['id']}/covered").status_code, 404)
        gone = self.client.post(f"/api/absences/{absence['id']}/excuse", headers=HEADERS,
                                json={"fingerprint": "0" * 64, "items": ["gradebook:x"], "request_id": "x1", "confirmation": "excuse"})
        self.assertIn(gone.status_code, (404, 405))
        self.assertNotIn("absences.excuse", self.app.view_functions)


class StorageTests(Base):
    def test_uploaded_documents_are_part_of_the_sections_verified_files(self):
        absence = self.record().json
        self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS, data={"file": (io.BytesIO(PDF), "note.pdf"), "request_id": "u1"})
        with connect(self.app.config["DATABASE"]) as db:
            row = db.execute("SELECT path, sha256 FROM absence_documents").fetchone()
        self.assertTrue(row["path"].startswith(f"absences/{absence['id']}/"))


if __name__ == "__main__":
    unittest.main()
