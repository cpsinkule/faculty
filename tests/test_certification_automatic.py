"""Late papers certify themselves (requirement, 2026-10-09; GRADING "LATE PAPERS CERTIFY THEMSELVES", GRADESCOPE "AUTOMATIC LATE-PAPER
SEND"), on the graded workspace of Certification and Export's tests: Alice and Bob certified, Carol (added to the roster after the exam)
certified 0 with no paper, then her paper arrives, is graded and decided, and Faculty certifies it by itself."""
import io
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grading import auto_certify  # noqa: E402
from grading import batch_adjustments  # noqa: E402
from grading.certification_automatic import LateCertification, cleared  # noqa: E402
from grading.models import digest  # noqa: E402
from rosters import connect  # noqa: E402
import test_grading_certification as certification_fixture  # noqa: E402

CAROL_UIN = "234567890"
DAN = ["", "Example, Dan", "345678901", "Registered", "MATH", "U1", "", "N", "", "", "", "dan@example.test"]
DR_BOOKING = {"booking_id": "b1", "exam_type": "Exam 1", "date": "2099-01-05", "start_time": "09:00", "end_time": "11:00", "length_minutes": 120, "campus": "Main"}


class LateTests(certification_fixture.Certify):
    def late(self):
        return LateCertification(self.service)

    def carol_paper(self, decide=True, lines=("R3_1",), other=("R4_1",), enrollment=None, section_id=None):
        """Carol's paper arrives after the certification: printed for her, scanned and reviewed, its version read (A), graded, and (with
        `decide`) the instructor's decisions on both of her questions. `enrollment` and `section_id`: another late student's."""
        enrollment, section_id = enrollment or self.carol, section_id or self.section["id"]
        batch = self.documents.create_batch(self.version_id, section_id, 1, str(uuid.uuid4()), students=[enrollment])
        with connect(self.path) as db:  # printed (as `ready_batch`, for any section)
            db.execute("UPDATE page_stamps SET state='issued' WHERE printed_copy_id IN (SELECT id FROM printed_copies WHERE print_batch_id=?)", (batch["id"],))
            db.execute("UPDATE print_batches SET status='ready',output_path='print.pdf',output_sha256=?,manifest_path='manifest.json',manifest_sha256=? WHERE id=?",
                       ("c" * 64, "d" * 64, batch["id"]))
        self.event = self.exams.attach_batch(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "batch_id": batch["id"]})
        self.reviewed_source(batch=self.documents.get_batch(batch["id"]), changed=f"late-{enrollment}")
        self.frq._version_discovery_at = -10 ** 6  # papers are looked for at most once a minute: a second late paper is looked for now
        for _ in range(10):
            if not self.frq.process_version_one():
                break
        waiting = next(w for w in self.frq.paper_versions(self.aid)["waiting"] if w["enrollment_id"] == enrollment)  # no seat
        self.frq.confirm_paper_version(self.aid, {**waiting, "exam_version": "A", "confirm_version": True})
        qids = [self.q[("q20", "A")], self.q[("q21", "A")]]
        command = {"request_id": str(uuid.uuid4()), "question_ids": qids, "mode": "local", "operation": "recognize", "selection": "new"}
        found = self.frq.preview(self.aid, command)
        self.assertEqual(len(found["eligible"]), 2, found["skipped"])
        job = self.frq.create_job(self.aid, {**command, "fingerprint": found["fingerprint"]})
        for _ in range(80):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        if decide:
            self.carol_decide("q20", list(lines), enrollment=enrollment)
            self.carol_decide("q21", list(other), enrollment=enrollment)

    def carol_response(self, key, enrollment=None):
        qid = self.q[(key, "A")]
        return self.frq.response(next(r["id"] for r in self.frq.responses(self.aid, qid) if r["enrollment_id"] == (enrollment or self.carol)))

    def carol_decide(self, key, lines, status="approved", enrollment=None):
        response = self.carol_response(key, enrollment)
        return self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": status,
                                                     "confirm_review": True, "selected_items": lines})

    def line(self, key="q20", section="all", points=1, reason="Misprint in part (c)"):
        """The instructor's "Add a line to every grade…" on a question of Version A: previewed, then applied."""
        url = f"/questions/{self.q[(key, 'A')]}/adjustments"
        found = self.api("post", f"{url}/preview", {"section_id": section, "points": points, "reason": reason})
        return self.api("post", url, {"section_id": section, "points": points, "reason": reason, "fingerprint": found["fingerprint"],
                                      "request_id": str(uuid.uuid4()), "confirmation": "adjust"}, status=201)

    def candidates(self):
        with connect(self.path) as db:
            return self.frq._late_candidates(db, self.aid)

    def certified(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_certifications ORDER BY rowid")]

    def certified_carol(self):
        """Alice and Bob certified with their grades, Carol with a confirmed 0."""
        self.add_carol()
        self.certify()
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))

    def drop(self, enrollment):
        with self.frq.rosters.lock(), connect(self.path) as db:
            db.execute("""INSERT INTO roster_drops(id,section_id,enrollment_id,action,source,reason,request_id,actor,created_at)
                VALUES(?,?,?,'drop','roster','Q drop',?,'roster import','2026-09-27T09:00:00+00:00')""",
                       (str(uuid.uuid4()), self.section["id"], enrollment, str(uuid.uuid4())))

    def join(self, *people):
        """More students join the roster after the exam, as `add_carol` does; their enrollments by name."""
        from test_rosters import csv_bytes, STUDENTS
        group = self.client.get("/api/state").get_json()["course_groups"][0]
        result = self.client.post(f"/api/course-groups/{group['id']}/sections/501/roster", data={"file": (io.BytesIO(csv_bytes([*STUDENTS, certification_fixture.CAROL, *people])), "roster.csv")},
                                  headers=certification_fixture.HEADERS)
        self.assertIn(result.status_code, (200, 201), result.get_json())
        self.exams.sync_roster(self.event["id"], {"version": self.exams.get(self.event["id"])["version"]})
        with connect(self.path) as db:
            known = {self.enrollment, self.second, self.carol}
            return [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? ORDER BY rowid", (self.section["id"],)) if r[0] not in known]

    # --- the rule's pieces -------------------------------------------------------------------------------------------------------
    def test_a_grade_is_cleared_unless_still_flagged_for_review(self):
        self.assertTrue(cleared({"flags": []}))
        self.assertTrue(cleared({"flags": ["waived"]}))  # a check the review policy waived is no flag
        self.assertTrue(cleared({"flags": ["arbitrator"], "settled": True}))
        self.assertTrue(cleared({"flags": ["reasons"], "flag_resolution": "revised"}))
        self.assertTrue(cleared({"flags": ["grading", "reasons"], "review_status": "approved"}))  # the instructor decided it
        self.assertFalse(cleared({"flags": ["reasons"]}))
        self.assertFalse(cleared({"flags": ["arbitrator"], "settled": False}))
        self.assertFalse(cleared({"flags": ["reasons"], "flag_resolution": "unreliable"}))

    # --- certified by itself ---------------------------------------------------------------------------------------------------------
    def test_a_late_paper_is_certified_by_itself_into_its_own_cell_and_the_plan_reads_current(self):
        self.certified_carol()
        alice, bob = self.cells()[self.row1], self.cells()[self.row2]
        self.carol_paper()
        self.assertEqual(self.state()["state"], "changed")
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))  # 10 + 8 (the first line of each rubric)
        self.assertEqual((self.cells()[self.row1], self.cells()[self.row2]), (alice, bob))
        self.assertEqual(self.state()["state"], "current")
        first, second = self.certified()
        self.assertEqual((second["actor"], second["supersedes_id"], second["amends_id"]), ("automatic", first["id"], None))
        summary = json.loads(second["summary_json"])
        self.assertEqual((summary["automatic"]["rows"], summary["automatic"]["markers"], summary["scored"], summary["zero"]), ([self.row3], "live", 3, 0))
        with connect(self.path) as db:
            rows = {f"{r['section_id']}:{r['enrollment_id']}": dict(r) for r in db.execute("SELECT * FROM frq_certification_students WHERE certification_id=?",
                                                                                              (second["id"],))}
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='grades_certified' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual((rows[self.row3]["status"], rows[self.row3]["earned"]), ("scored", "18"))
        self.assertEqual(event["automatic"], {"rows": [self.row3], "markers": "live"})
        self.assertNoUin(second)
        # The same late paper is never certified twice; nothing is left to do.
        again = self.late().run(self.aid)
        self.assertEqual((again["state"], again["late"]), ("waiting", []))
        self.assertEqual(len(self.certified()), 2)
        history = self.api("get", "")["history"]
        self.assertNotIn("amends", history[0])

    def test_a_late_paper_of_a_plan_graded_for_participation_certifies_itself_once_its_waiting_grades_are_approved(self):
        """requirement, 2026-11-11 (GRADING "THE WAITING GRADES APPROVE THEMSELVES"): "i want late participation-only assignments to
        be auto certified as well". Nobody decides the late paper's grades: Faculty approves them as graded, and it certifies itself."""
        from grading import participation
        with connect(self.path) as db:
            if participation.has_bubble_key(db, self.aid):
                self.skipTest("Graded for participation covers written questions only.")
        self.frq.set_participation(self.aid, {"enabled": True})
        self.certified_carol()
        self.carol_paper(decide=False)
        waiting = [self.carol_response(key) for key in ("q20", "q21")]
        self.assertEqual([r["review"] for r in waiting], [None, None])
        held = self.late().run(self.aid)
        self.assertEqual((held["state"], held["late"]), ("waiting", []), held)  # her grades still wait for a review
        self.assertEqual(self.frq.approve_waiting(self.aid), 2)
        self.assertEqual([self.carol_response(key)["review"]["comment"] for key in ("q20", "q21")], [participation.APPROVED] * 2)
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"]), ("done", [self.row3]), done)
        self.assertEqual(self.cells()[self.row3][0], "scored")
        self.assertEqual([c["actor"] for c in self.certified()], ["workspace", "automatic"])

    def test_a_classmates_drop_keeps_a_late_paper_s_certification_live_and_a_later_regrade_alone(self):
        """A DROP NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-11-03, asked of Quiz 6's makeups after Edda's regrade): a student certified
        0 with no work and dropped since neither carries the markers nor stops the next change, and stays as certified."""
        self.add_carol()
        erin, fay, gus = self.join(*[["", f"Example, {name}", uin, "Registered", "MATH", "U1", "", "N", "", "", "", f"{name.lower()}@example.test"]
                                     for name, uin in (("Erin", "345678901"), ("Fay", "456789012"), ("Gus", "567890123"))])
        self.send()
        self.sender.drain()
        self.certify()  # Carol, Erin, Fay and Gus: 0 with no work
        self.drop(fay)
        self.assertEqual(self.state()["state"], "current")
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        self.assertEqual(self.state()["state"], "current")
        self.sender.drain()
        first, second = self.certified()
        with connect(self.path) as db:
            rows = lambda cid: dict(db.execute("SELECT enrollment_id,status||':'||earned||':'||parts_json FROM frq_certification_students WHERE certification_id=?", (cid,)).fetchall())
            self.assertEqual(rows(second["id"])[fay], rows(first["id"])[fay])
        # A second drop, then one regrade: nothing but that grade asks anything.
        self.drop(erin)
        from grading.certification_change import OneChange
        change = OneChange(self.service)
        self.carol_decide("q20", ["R3_3"], enrollment=self.enrollment)
        rid = self.carol_response("q20", enrollment=self.enrollment)["id"]
        check = change.check(self.aid, rid)
        self.assertTrue(check["possible"], check)
        view = self.sender.preview(self.aid, {"only_responses": [rid]})
        self.sender.send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}, "only_responses": [rid]})
        self.sender.drain()
        # Carol's late paper is not on the fake Gradescope: where another student's copy stands never stops this one grade.
        made = change.amend(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "certify-change", "fingerprint": check["fingerprint"], "response_id": rid})
        self.assertEqual(made["gradebook"]["written"], 1)
        self.assertEqual(self.state()["state"], "current")
        self.assertEqual(self.cells()[f"{self.section['id']}:{gus}"][1], "0")  # Gus never moved

    def test_a_classmate_owed_a_makeup_since_is_left_out_with_the_late_paper_and_the_plan_reads_current(self):
        # Before 2026-11-14 only the drop was excused and Erin's makeup kept the plan changed; now Faculty leaves her out itself
        # (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES").
        self.add_carol()
        erin, fay = self.join(*[["", f"Example, {name}", uin, "Registered", "MATH", "U1", "", "N", "", "", "", f"{name.lower()}@example.test"]
                                for name, uin in (("Erin", "345678901"), ("Fay", "456789012"))])
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.certify()  # Carol, Erin and Fay: 0 with no work
        self.drop(fay)
        self.app.extensions["absences"].create({"request_id": str(uuid.uuid4()), "section_id": self.section["id"], "enrollment_id": erin,
                                                "first_date": "2026-09-25", "last_date": "2026-09-25", "reason": "illness", "status": "excused"})
        self.carol_paper()
        done = self.late().run(self.aid)
        erin_row = f"{self.section['id']}:{erin}"
        self.assertEqual((done["state"], done["late"], done["changes"], done["markers"]), ("done", sorted([self.row3, erin_row]), {erin_row: "left_out"}, "live"), done)
        self.assertEqual(self.state()["state"], "current")
        self.assertEqual((self.cells()[self.row3], self.cells()[erin_row][0]), (("scored", "18", "grading"), "cleared"))

    def test_a_student_who_joined_after_the_certification_is_certified_once_their_paper_is_graded(self):
        self.certify()  # Alice and Bob only
        self.add_carol()  # joins the roster and the exam afterwards: no certified row at all
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def test_the_instructor_s_own_uncertified_change_keeps_the_plan_changed(self):
        self.certified_carol()
        alice = self.cells()[self.row1]
        self.decide("q20", "A", ["R3_3"])  # the instructor changes Alice's grade and does not certify it
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["markers"]), ("done", "carried"), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))
        self.assertEqual(self.cells()[self.row1], alice)  # the instructor's change waits for their Certify
        self.assertEqual(self.state()["state"], "changed")
        first, second = self.certified()
        self.assertEqual((second["amends_id"], second["decisions_sha256"]), (first["id"], first["decisions_sha256"]))
        self.assertEqual(json.loads(second["summary_json"])["automatic"]["pending"], [self.row3])
        # Their Certify records Alice's change; Carol's grade stands as Faculty certified it.
        self.certify()
        self.assertEqual((self.cells()[self.row1][1], self.cells()[self.row3][1]), ("8", "18"))
        self.assertEqual(self.state()["state"], "current")

    def test_a_late_grade_still_waiting_for_review_waits_and_is_certified_once_decided(self):
        self.certified_carol()
        self.carol_paper(decide=False)
        waiting = self.late().run(self.aid)
        self.assertEqual((waiting["state"], [w["row"] for w in waiting["waiting"]]), ("waiting", [self.row3]))
        self.assertEqual(len(self.certified()), 1)
        self.carol_decide("q20", ["R3_1"])
        self.assertEqual(self.late().run(self.aid)["state"], "waiting")  # one question still waits
        self.carol_decide("q21", ["R4_1"])
        self.assertEqual(self.late().run(self.aid)["state"], "done")

    def test_a_value_the_instructor_typed_in_the_gradebook_stays_and_the_student_waits(self):
        self.certified_carol()
        item = self.setup()[1]["items"][0]["id"]
        cell = self.gradebook.view(self.term_id)["cells"][item][self.row3]
        self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": item, "row": self.row3, "base": cell["id"], "status": "scored", "points": "12"}]})
        self.carol_paper()
        found = self.late().run(self.aid)
        self.assertEqual(found["state"], "waiting")
        self.assertEqual(found["waiting"], [{"row": self.row3, "reason": "A value you typed in the gradebook stays"}])
        self.assertEqual(self.cells()[self.row3], ("scored", "12", "instructor"))

    def test_nothing_is_recorded_when_the_grades_move_while_they_are_read(self):
        self.certified_carol()
        self.carol_paper()
        marks = iter(["before", "after"])
        with patch.object(self.service, "_marks", side_effect=lambda db, plan: next(marks)):
            self.assertEqual(self.late().run(self.aid)["state"], "changed")
        self.assertEqual(len(self.certified()), 1)

    # --- 2026-10-09 review ----------------------------------------------------------------------------------------------------------
    def test_the_cheap_test_sees_a_late_student_s_responses_and_nothing_else(self):
        self.certified_carol()
        self.assertFalse(self.candidates())  # Alice and Bob were certified with their papers; nothing of Carol's has come
        self.carol_paper(decide=False)
        self.assertTrue(self.candidates())

    def test_a_late_paper_missing_a_line_added_to_every_grade_waits_for_the_instructor(self):
        # The misprint line reached the grades final when it was applied; Carol's paper came later, so Faculty does not certify it
        # (it would certify, post and send her at the lower score). It adds no line itself but a full-credit one (next test).
        self.certified_carol()
        self.assertEqual(self.line()["changed"], 1)  # Alice's 4 → 5 on Question 20
        self.certify()
        self.carol_paper(lines=("R3_2",))  # 4 on Question 20, 8 on Question 21
        found = self.late().run(self.aid)
        self.assertEqual((found["state"], found["waiting"]),
                         ("waiting", [{"row": self.row3, "reason": "A line you added to every grade of Question 20 is not on this paper; certify it yourself."}]))
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        self.assertEqual(len(self.certified()), 2)
        # The instructor adds the line again: it goes only where it is missing (Carol's), and her paper then certifies by itself.
        self.assertEqual(self.line()["changed"], 1)
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"]), ("done", [self.row3]), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "13", "grading"))

    def test_a_late_paper_gets_a_full_credit_line_by_itself_and_then_certifies(self):
        # GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS" (requirement, 2026-11-04).
        self.certified_carol()
        with connect(self.path) as db:
            worth = self.frq.question(db, self.aid, self.q[("q20", "A")])["content"]["max_points"]
        self.line(points=worth, reason="Not required")
        self.certify()
        self.carol_paper(lines=("R3_2",))  # 4 on Question 20, 8 on Question 21
        self.assertEqual(self.service.reach_full_credit(self.aid), 1)
        self.assertEqual(self.carol_response("q20")["review"]["score"]["score"], worth)
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"]), ("done", [self.row3]), done)
        self.assertEqual(self.cells()[self.row3], ("scored", f"{worth + 8:g}", "grading"))

    def test_a_removed_line_or_one_of_another_section_holds_nothing(self):
        self.certified_carol()
        self.line()
        with connect(self.path) as db:
            receipts = self.service._adjustment_receipts(db, self.aid)
        [standing] = batch_adjustments.standing_lines(receipts)
        self.assertEqual((standing["section_id"], standing["points"], standing["reason"]), ("all", 1, "Misprint in part (c)"))
        # An edit replaces the line it changed; a removal ends its chain.
        edited = {"batch_id": "b2", "action": "edit", "of": standing["batch_id"], "question_id": standing["question_id"], "section_id": "all", "points": 2, "reason": "Misprint"}
        self.assertEqual([(x["batch_id"], x["points"]) for x in batch_adjustments.standing_lines([*receipts, edited])], [("b2", 2)])
        self.assertEqual(batch_adjustments.standing_lines([*receipts, edited, {**edited, "batch_id": "b3", "action": "remove", "of": "b2"}]), [])
        self.certify()
        self.carol_paper(lines=("R3_2",))
        state = self.service._evaluate(self.aid, gradebook=False)
        other = [{**standing, "section_id": "another-section"}]
        found = LateCertification(self.service)._find(state, other)
        self.assertEqual((found["late"], found["waiting"]), ([self.row3], []))
        self.assertEqual(LateCertification(self.service)._find(state, [{**standing, "section_id": self.section["id"]}])["waiting"][0]["row"], self.row3)

    def test_a_line_the_question_s_bounds_would_hold_holds_nothing_up(self):
        self.certified_carol()
        self.line()
        self.certify()
        self.carol_paper()  # 10 of 10 on Question 20: the +1 cannot move it
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"]), ("done", [self.row3]), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))

    def test_a_student_left_out_until_their_dr_exam_is_certified_once_their_paper_is_graded(self):
        self.add_carol()
        self.enterContext(patch("grading.progress.dr_bookings", return_value=[{**DR_BOOKING, "section_id": self.section["id"], "enrollment_id": self.carol}]))
        preview = self.preview()
        self.assertIn("dr_ahead", [c["id"] for c in preview["confirm"]])
        self.certify(preview)
        with connect(self.path) as db:
            row = db.execute("SELECT status,reason FROM frq_certification_students WHERE enrollment_id=? ORDER BY rowid DESC LIMIT 1", (self.carol,)).fetchone()
        self.assertEqual(tuple(row), ("left_out", "dr_ahead"))
        self.carol_paper()
        self.assertTrue(self.candidates())
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def makeup_carol(self):
        """Carol's undecided absence covers the day the exam was given: certified as left out until her makeup (MAKEUP OWED)."""
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        absence = self.absence(self.carol, "2026-09-24", "2026-09-26")
        preview = self.preview()
        self.assertIn("makeup_ahead", [c["id"] for c in preview["confirm"]])
        self.certify(preview)
        return absence

    def test_a_makeup_decided_after_certifying_keeps_the_late_paper_s_certification_live(self):
        # The normal MAKEUP OWED flow: undecided at Certify, excused afterwards (that reopens nothing), then the makeup paper. Its
        # decision moved the makeup check's members; the rows are what the instructor confirmed, so it stays live.
        absence = self.makeup_carol()
        self.decide_absence(absence, "excused")
        self.assertEqual(self.state()["state"], "current")
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        self.assertEqual((self.state()["state"], self.certified()[-1]["amends_id"]), ("current", None))
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))

    # --- makeup changes (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES", requirement, 2026-11-14) -------------------------------------
    def carol_rows(self):
        with connect(self.path) as db:
            return [tuple(r) for r in db.execute("""SELECT status,reason,earned FROM frq_certification_students WHERE enrollment_id=? AND certification_id=
                (SELECT id FROM frq_certifications ORDER BY rowid DESC LIMIT 1)""", (self.carol,))]

    def test_a_makeup_that_ended_is_given_0_by_faculty_and_the_plan_reads_current(self):
        absence = self.makeup_carol()
        self.assertFalse(self.candidates())
        self.decide_absence(absence, "not_excused")
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["makeup_ended"]))
        self.assertTrue(self.candidates())
        ahead = self.service.view(self.aid)["automatic"]["next"]  # the page says so before Faculty does it
        self.assertEqual(([p["row"] for p in ahead["zero"]], ahead["papers"], ahead["left_out"], ahead["rest"]), ([self.row3], [], [], False))
        alice, bob = self.cells()[self.row1], self.cells()[self.row2]
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["changes"], done["markers"], done["gradescope"]), ("done", [self.row3], {self.row3: "zero"}, "live", 0), done)
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        self.assertEqual((self.cells()[self.row1], self.cells()[self.row2]), (alice, bob))
        latest = self.certified()[-1]
        self.assertEqual((self.state()["state"], latest["amends_id"], latest["actor"], self.carol_rows()), ("current", None, "automatic", [("zero", "no_paper", "0")]))
        self.assertEqual(json.loads(latest["summary_json"])["automatic"]["changes"], {self.row3: "zero"})
        view = self.service.view(self.aid)
        self.assertEqual([(p["row"], p.get("change")) for p in view["automatic"]["latest"]["students"]], [(self.row3, "zero")])
        self.assertNotIn("next", view["automatic"])
        self.assertFalse(self.candidates())
        self.assertEqual(self.late().run(self.aid)["state"], "waiting")
        self.assertEqual(len(self.certified()), 2)
        self.assertNoUin(latest)
        # Their own Certify afterwards finds nothing of Faculty's to undo.
        self.certify()
        self.assertEqual((self.cells()[self.row3], self.state()["state"]), (("scored", "0", "grading"), "current"))

    def test_a_makeup_recorded_missed_is_given_0_by_faculty(self):
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.absence(self.carol, "2026-09-24", "2026-09-26", status="excused")
        self.certify(self.preview())
        self.app.extensions["absences"].makeup_missed({"request_id": "missed-1", "missed": True, "event_id": self.event["id"],
                                                       "section_id": self.section["id"], "enrollment_id": self.carol})
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["changes"], done["markers"]), ("done", {self.row3: "zero"}, "live"), done)
        self.assertEqual((self.cells()[self.row3], self.state()["state"]), (("scored", "0", "grading"), "current"))

    def test_a_student_given_0_who_is_then_owed_a_makeup_is_left_out_by_faculty_and_given_0_again_when_it_ends(self):
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.certify()
        self.assertEqual((self.cells()[self.row3], self.candidates()), (("scored", "0", "grading"), False))
        absence = self.absence(self.carol, "2026-09-24", "2026-09-26")  # undecided: never 0 while it waits (MAKEUP OWED)
        self.assertTrue(self.candidates())
        ahead = self.service.view(self.aid)["automatic"]["next"]
        self.assertEqual(([p["row"] for p in ahead["left_out"]], ahead["zero"], ahead["rest"]), ([self.row3], [], False))
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["changes"], done["markers"]), ("done", [self.row3], {self.row3: "left_out"}, "live"), done)
        self.assertEqual((self.cells()[self.row3][0], self.carol_rows(), self.state()["state"], self.state()["left_out_makeup"]),
                         ("cleared", [("left_out", "makeup", None)], "current", 1))
        self.assertFalse(self.candidates())
        self.decide_absence(absence, "not_excused")
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["changes"], done["markers"]), ("done", {self.row3: "zero"}, "live"), done)
        self.assertEqual((self.cells()[self.row3], self.state()["state"]), (("scored", "0", "grading"), "current"))
        self.assertEqual(json.loads(self.certified()[-1]["summary_json"])["automatic"]["since_changes"], {self.row3: "zero"})

    def test_a_makeup_s_0_waits_while_a_paper_of_the_scans_has_no_student_yet(self):
        absence = self.makeup_carol()
        self.decide_absence(absence, "not_excused")
        read = self.service._read

        def unclaimed(db, aid, exact=False):
            state = read(db, aid, exact)
            state["collection"]["unassigned_received_count"] = 1  # it may be Carol's makeup paper
            return state
        with patch.object(self.service, "_read", unclaimed):
            done = self.late().run(self.aid)
            self.assertNotIn("next", self.service.view(self.aid)["automatic"])  # nor does the page promise it
        self.assertEqual((done["state"], done["late"], [w["row"] for w in done["waiting"]]), ("waiting", [], [self.row3]), done)
        self.assertIn("received paper without a student", done["waiting"][0]["reason"])
        self.assertEqual((len(self.certified()), self.state()["state"]), (1, "changed"))

    def test_a_makeup_s_0_never_replaces_a_score_above_0_in_the_gradebook(self):
        # ABSENCES "A SCORE STANDS" (requirement, 2026-11-14): a score that came in another way (here an import) is not written over.
        absence = self.makeup_carol()
        item = self.setup()[1]["items"][0]["id"]
        self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": item, "row": self.row3, "base": None, "status": "scored", "points": "12"}]})
        with connect(self.path) as db:  # the same value as an import's revision of the cell
            typed = dict(db.execute("SELECT * FROM gradebook_scores WHERE enrollment_id=? ORDER BY seq DESC LIMIT 1", (self.carol,)).fetchone())
            row = {**typed, "id": str(uuid.uuid4()), "seq": typed["seq"] + 1, "source": "import", "replaces_id": typed["id"], "points": "14"}
            db.execute(f"INSERT INTO gradebook_scores({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
        self.decide_absence(absence, "not_excused")
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"]), ("waiting", []), done)
        self.assertEqual(done["waiting"], [{"row": self.row3, "reason": "Not given 0: they have 14 for it in the gradebook; certify it yourself."}])
        self.assertEqual((self.cells()[self.row3], len(self.certified())), (("scored", "14", "import"), 1))
        from grading.certification_automatic import _holds  # nor an excused or missing mark; an empty, cleared or 0 cell holds nothing
        self.assertEqual([_holds(c) for c in (None, {"status": "cleared", "points": None}, {"status": "scored", "points": "0"}, {"status": "excused", "points": None})],
                         [None, None, None, "their gradebook cell is marked excused"])

    def test_a_makeup_s_0_and_a_late_paper_go_in_one_certification(self):
        self.add_carol()
        dan, = self.join(DAN)
        row4 = f"{self.section['id']}:{dan}"
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        absence = self.absence(self.carol, "2026-09-24", "2026-09-26", status="excused")
        self.certify(self.preview())  # Carol left out until her makeup, Dan 0 with no work
        self.carol_paper(enrollment=dan)
        self.decide_absence(absence, "not_excused")
        ahead = self.service.view(self.aid)["automatic"]["next"]
        self.assertEqual(([p["row"] for p in ahead["papers"]], [p["row"] for p in ahead["zero"]], ahead["rest"]), ([row4], [self.row3], False))
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["changes"], done["markers"]), ("done", sorted([self.row3, row4]), {self.row3: "zero"}, "live"), done)
        self.assertEqual((self.cells()[self.row3], self.cells()[row4], self.state()["state"]), (("scored", "0", "grading"), ("scored", "18", "grading"), "current"))
        students = self.service.view(self.aid)["automatic"]["latest"]["students"]
        self.assertEqual(sorted((p["row"], p.get("change")) for p in students), sorted([(self.row3, "zero"), (row4, None)]))

    def test_a_makeup_s_0_is_certified_while_the_instructor_s_own_change_still_waits(self):
        absence = self.makeup_carol()
        alice = self.cells()[self.row1]
        self.decide("q20", "A", ["R3_3"])  # the instructor changes Alice's grade and does not certify it
        self.decide_absence(absence, "not_excused")
        self.assertTrue(self.service.view(self.aid)["automatic"]["next"]["rest"])
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["changes"], done["markers"]), ("done", {self.row3: "zero"}, "carried"), done)
        self.assertEqual((self.cells()[self.row3], self.cells()[self.row1], self.state()["state"]), (("scored", "0", "grading"), alice, "changed"))
        self.assertEqual(self.late().run(self.aid)["state"], "waiting")  # never twice
        self.certify()
        self.assertEqual((self.cells()[self.row1][1], self.cells()[self.row3][1], self.state()["state"]), ("8", "0", "current"))

    def test_a_student_left_out_until_their_makeup_who_drops_is_no_longer_kept_and_nothing_asks(self):
        self.makeup_carol()
        self.drop(self.carol)
        self.assertTrue(self.candidates())
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["changes"], done["markers"]), ("done", [self.row3], {self.row3: "gone"}, "live"), done)
        self.assertEqual((self.carol_rows(), self.state()["state"]), ([], "current"))
        self.assertNotIn(self.row3, self.cells())
        self.assertEqual(self.service.view(self.aid)["automatic"]["latest"]["students"], [])
        self.assertFalse(self.candidates())

    def test_the_worker_certifies_a_makeup_s_change_that_writes_nothing_of_the_assignment(self):
        self.enterContext(patch.object(auto_certify, "SETTLE", 0))
        self.enterContext(patch.object(auto_certify, "LOOK_EVERY", 0))
        absence = self.makeup_carol()
        self.assertFalse(any(self.frq.auto_certify_one() for _ in range(3)))  # looked at: nothing of anyone's has come
        self.decide_absence(absence, "not_excused")  # no event of the assignment: the marker follows the absences
        self.assertTrue(any(self.frq.auto_certify_one() for _ in range(3)))
        self.assertEqual((self.cells()[self.row3], self.state()["state"], len(self.certified())), (("scored", "0", "grading"), "current", 2))
        self.assertFalse(any(self.frq.auto_certify_one() for _ in range(3)))
        settings = self.frq.settings()["settings"]
        absence = self.absence(self.carol, "2026-09-25")  # owed a makeup again: the switch off leaves it to the instructor
        with patch.object(self.frq, "settings", return_value={"settings": {**settings, "auto_certify": False}, "version": 0}):
            self.assertFalse(any(self.frq.auto_certify_one() for _ in range(3)))
        self.assertEqual(len(self.certified()), 2)

    def _mc_check(self, check_id, rows):
        """Gradescope's multiple-choice check `check_id` listed (or, with no rows, gone) as Certification reads the plan's checks."""
        from grading import certification_rules as rules
        original = rules._gradescope_checks

        def found(facts, href):
            out = original(facts, href)
            if rows:
                out.append(rules.check(check_id, "confirm", "Multiple choice check " + check_id, "d",
                                       rows=[{"row": r, "name": "x", "section": "", "detail": "Q3: Gradescope 0 -> 4"} for r in rows],
                                       members=[f"{r}:3:4" for r in rows], count=len(rows)))
            return out
        return patch.object(rules, "_gradescope_checks", found)

    def test_a_gradescope_mc_correction_settling_after_certifying_keeps_a_late_paper_live(self):
        # Requirement, 2026-11-06 (GRADING "LATE PAPERS CERTIFY THEMSELVES"): a live or settled correction of Gradescope's copy is not an
        # unconfirmed check; it demotes nothing, as "Multiple choice scored differently" never did.
        for check_id in ("gradescope_mc_differs", "gradescope_mc_corrected"):
            with self.subTest(check_id):
                self.setUp()
                self.add_carol()
                with self._mc_check(check_id, [self.row1]):
                    self.certify()
                self.carol_paper()
                done = self.late().run(self.aid)  # the check has settled: it is no longer listed
                self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
                self.assertEqual((self.state()["state"], self.certified()[-1]["amends_id"]), ("current", None))

    def test_a_gradescope_mc_correction_for_a_classmate_keeps_a_late_paper_live(self):
        for check_id in ("gradescope_mc_differs", "gradescope_mc_corrected"):
            with self.subTest(check_id):
                self.setUp()
                self.add_carol()
                self.certify()
                self.carol_paper()
                with self._mc_check(check_id, [self.row1]):
                    done = self.late().run(self.aid)
                    self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
                    self.assertEqual((self.state()["state"], self.certified()[-1]["amends_id"]), ("current", None))

    def test_a_late_student_whose_own_sheet_has_an_open_gradescope_mc_correction_still_waits(self):
        # Not widened (AGENTS.md SCOPE): the instructor looks at each corrected sheet before it is certified, sent or published.
        self.add_carol()
        self.certify()
        self.carol_paper()
        with self._mc_check("gradescope_mc_corrected", [self.row3]):
            done = self.late().run(self.aid)
        self.assertEqual(done["late"], [], done)
        self.assertEqual([w["row"] for w in done["waiting"]], [self.row3])
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))

    def test_the_worker_backs_off_a_failing_look_and_looks_again_after_a_change(self):
        self.enterContext(patch.object(auto_certify, "SETTLE", 0))
        self.enterContext(patch.object(auto_certify, "LOOK_EVERY", 0))
        self.certified_carol()
        self.carol_paper(decide=False)
        clock = {"now": 1000.0}
        self.enterContext(patch("grading.auto_certify.time.monotonic", side_effect=lambda: clock["now"]))
        outcomes = []

        def run(_self, aid, actor="automatic"):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def turn(seconds=1):
            clock["now"] += seconds
            return self.frq.auto_certify_one()
        self.enterContext(patch.object(LateCertification, "run", run))
        self.frq._auto_certify = {}  # grading Carol's paper ran worker turns on the real clock: a new worker process from here
        turn()  # the first look notes the activity
        outcomes.append(RuntimeError("the roster payload could not be verified"))
        with self.assertRaises(RuntimeError):
            turn()
        turn(60)
        turn(300)
        self.assertEqual(outcomes, [])  # backed off: not looked at again every minute
        outcomes.append({"state": "changed"})
        turn(auto_certify.RECHECK_EVERY)  # RECHECK_EVERY later it is looked at again
        self.assertEqual(outcomes, [])
        outcomes.append({"state": "waiting", "late": [], "waiting": []})
        turn()  # a look that met a write meanwhile recorded no look: the next turn reads again
        self.assertEqual(outcomes, [])
        turn(auto_certify.RECHECK_EVERY * 3)  # nothing of anyone's came: only new activity brings another look
        self.assertEqual(outcomes, [])

    # --- the worker's step -----------------------------------------------------------------------------------------------------------
    def test_the_worker_certifies_once_the_work_has_settled_and_says_once_why_a_paper_waits(self):
        self.enterContext(patch.object(auto_certify, "SETTLE", 0))
        self.enterContext(patch.object(auto_certify, "LOOK_EVERY", 0))
        self.enterContext(patch.object(auto_certify, "RECHECK_EVERY", 0))
        self.certified_carol()
        self.carol_paper(decide=False)

        def turns(n=3):
            return [self.frq.auto_certify_one() for _ in range(n)]

        def reasons():
            with connect(self.path) as db:
                return [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='automatic_certification_not_made' ORDER BY id")]
        self.assertFalse(any(turns()))
        self.assertEqual([[s["row"] for s in r["students"]] for r in reasons()], [[self.row3]])  # recorded once
        with connect(self.path) as db:
            state = self.frq.auto_certify_state(db, self.aid, {"auto_certify": True})
        self.assertEqual([w["row"] for w in state["waiting"]], [self.row3])
        self.carol_decide("q20", ["R3_1"])
        self.carol_decide("q21", ["R4_1"])
        self.frq._auto_certify = {}  # a new worker process: the settle starts over
        self.assertTrue(any(turns()))
        self.assertEqual(self.cells()[self.row3], ("scored", "18", "grading"))
        with connect(self.path) as db:
            self.assertEqual(self.frq.auto_certify_state(db, self.aid, {"auto_certify": True})["waiting"], [])
        self.assertFalse(any(turns()))
        self.assertEqual(len(self.certified()), 2)

    def test_the_switch_off_certifies_nothing(self):
        self.enterContext(patch.object(auto_certify, "SETTLE", 0))
        self.enterContext(patch.object(auto_certify, "LOOK_EVERY", 0))
        self.certified_carol()
        self.carol_paper()
        settings = self.frq.settings()["settings"]
        with patch.object(self.frq, "settings", return_value={"settings": {**settings, "auto_certify": False}, "version": 0}):
            self.assertFalse(any(self.frq.auto_certify_one() for _ in range(3)))
        self.assertEqual(len(self.certified()), 1)

    # --- Gradescope ---------------------------------------------------------------------------------------------------------------
    def carol_on_gradescope(self):
        self.carol_user = self.fake.add_member(self.course, "Carol Example", CAROL_UIN, "carol.example@example.invalid")

    def automatic_rows(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_automatic_sends ORDER BY rowid")]

    def test_a_late_paper_goes_to_gradescope_as_its_section_s_papers_and_grades_went(self):
        self.add_carol()
        self.carol_on_gradescope()
        self.send()
        self.sender.drain()
        self.certify()
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["gradescope"]), ("done", 1), done)
        [row] = self.automatic_rows()
        self.assertEqual((row["kind"], row["papers_only"], row["status"], row["actor"]), ("written", 0, "pending", "automatic"))
        self.sender.drain()
        [row] = self.automatic_rows()
        self.assertEqual(row["status"], "recorded", row["error"])
        with connect(self.path) as db:
            send = dict(db.execute("SELECT * FROM frq_gradescope_sends WHERE id=?", (row["send_id"],)).fetchone())
            uploads = [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_uploads WHERE send_id=?", (send["id"],))]
            grades = [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_grades WHERE send_id=?", (send["id"],))]
        self.assertEqual((send["actor"], send["status"], json.loads(send["plan_json"])["automatic"]["row"]), ("automatic", "complete", self.row3))
        self.assertEqual([(u["enrollment_id"], u["status"]) for u in uploads], [(self.carol, "accepted")])
        self.assertEqual(sorted(g["status"] for g in grades), ["accepted", "accepted"])
        self.assertGraded("q20", "A", self.carol_user, [1], score=10.0)
        self.assertNothingForbidden()

    def test_a_section_sent_papers_only_gets_the_late_paper_only(self):
        self.add_carol()
        self.carol_on_gradescope()
        self.send(self.sender.preview(self.aid, {"papers_only": True}), papers_only=True)
        self.sender.drain()
        self.certify()
        self.carol_paper()
        self.late().run(self.aid)
        [row] = self.automatic_rows()
        self.assertEqual((row["kind"], row["papers_only"]), ("written", 1))
        self.sender.drain()
        with connect(self.path) as db:
            send_id = self.automatic_rows()[0]["send_id"]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_uploads WHERE send_id=? AND status='accepted'", (send_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_grades WHERE send_id=?", (send_id,)).fetchone()[0], 0)

    def test_a_section_nothing_was_sent_for_sends_nothing_and_says_why(self):
        self.certified_carol()
        self.carol_paper()
        self.assertEqual(self.late().run(self.aid)["gradescope"], 0)
        self.assertEqual(self.automatic_rows(), [])
        latest = self.certified()[-1]
        self.assertEqual(json.loads(latest["gradescope_json"])["automatic"][self.row3]["written"]["why"], "This section has no Gradescope assignment linked for it.")

    def test_a_published_assignment_gets_the_late_paper_and_an_instructor_send_goes_first(self):
        self.add_carol()
        self.carol_on_gradescope()
        self.send()
        self.sender.drain()
        self.certify()
        self.fake.assignments[self.container]["published"] = True  # the instructor published the grades on Gradescope
        self.carol_paper()
        self.late().run(self.aid)
        # The instructor sends a changed grade of Alice's (her response, not Carol's: both are Version A): their send is still running.
        alice = self.frq.response(next(r["id"] for r in self.frq.responses(self.aid, self.q[("q20", "A")]) if r["enrollment_id"] == self.enrollment))
        self.frq.save_review(alice["id"], {"version": alice["version"], "result_id": alice["current_result_id"], "status": "approved",
                                           "confirm_review": True, "selected_items": ["R3_3"]})
        only = {"only_responses": [alice["id"]]}  # that one grade (a regrade's Apply), not Carol's paper
        view = self.sender.preview(self.aid, only)
        self.send(view, published=[p["container_id"] for p in view["published"]], **only)  # they confirm the published assignment
        [queued] = self.automatic_rows()
        # The running send is seen from the catalog first: nothing of the plan is read under the lifecycle lock while it waits.
        with patch.object(self.sender, "_faculty", wraps=self.sender._faculty) as faculty:
            self.sender._auto_unit({**queued, "kind": "automatic", "part": queued["kind"]})
        self.assertEqual(faculty.call_count, 0)
        [row] = self.automatic_rows()
        self.assertEqual(row["status"], "pending")
        self.assertIn("Another send", row["error"])
        # Certification and Export says so on Carol's row (the reason was recorded and never shown, 2026-10-09 review).
        carol = next(x for x in self.service.view(self.aid)["students"] if x["row"] == self.row3)
        self.assertEqual(carol["gradescope"]["automatic"], {"kind": "written", "status": "waiting",
                                                             "reason": "Waiting: Another send to Gradescope is running for this assignment."})
        self.assertNotIn("automatic", next(x for x in self.service.view(self.aid)["students"] if x["row"] == self.row1)["gradescope"])
        self.assertEqual(self.sender.status(self.aid)["automatic"]["sends"], [{"row": self.row3, "kind": "written", "status": "waiting",
                                                                               "reason": "Waiting: Another send to Gradescope is running for this assignment."}])
        self.sender.drain()  # the instructor's send goes first
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends WHERE status IN ('pending','running')").fetchone()[0], 0)
            db.execute("UPDATE frq_gradescope_automatic_sends SET not_before=NULL")  # its minute has passed
        self.sender.drain()
        [row] = self.automatic_rows()
        self.assertEqual(row["status"], "recorded", row["error"])
        with connect(self.path) as db:
            plan = json.loads(db.execute("SELECT plan_json FROM frq_gradescope_sends WHERE id=?", (row["send_id"],)).fetchone()[0])
        self.assertTrue(plan["published"] and plan["automatic"]["published"])
        # The set is published but its Version A is not (as a makeup version Faculty adds lands): the send records it, and Faculty
        # publishes that version by itself once the paper is there (GRADESCOPE "AFTER CERTIFYING", 2026-10-12; tests in
        # test_gradescope_after), so the page no longer asks the instructor for it.
        self.assertEqual(plan["automatic"]["unpublished_version"], "A")
        with connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_automatic_runs SET not_before=NULL WHERE status='pending'")  # its wait for the sends is over
        self.sender.drain()
        view = self.service.view(self.aid)
        self.assertNotIn("unpublished_versions", view["gradescope"])
        self.assertEqual((view["gradescope"]["after"][0]["kind"], view["gradescope"]["after"][0]["published"]), ("late", ["501"]))
        self.assertTrue(self.fake.assignments[self.versions["A"]]["published"])
        self.assertNotIn("automatic", next(x for x in view["students"] if x["row"] == self.row3)["gradescope"])  # recorded: its send says the rest
        self.assertGraded("q20", "A", self.carol_user, [1], score=10.0)
        self.assertEqual(self.fake.forbidden_hits, [])

    def test_a_late_paper_sent_to_a_set_published_whole_says_nothing_about_publishing(self):
        self.add_carol()
        self.carol_on_gradescope()
        self.send()
        self.sender.drain()
        self.certify()
        for assignment in [self.container, *self.versions.values()]:  # Publish All Grades: the set and every version
            self.fake.assignments[assignment]["published"] = True
        self.carol_paper()
        self.late().run(self.aid)
        self.sender.drain()
        [row] = self.automatic_rows()
        self.assertEqual(row["status"], "recorded", row["error"])
        with connect(self.path) as db:
            plan = json.loads(db.execute("SELECT plan_json FROM frq_gradescope_sends WHERE id=?", (row["send_id"],)).fetchone()[0])
        self.assertTrue(plan["automatic"]["published"])
        self.assertNotIn("unpublished_version", plan["automatic"])
        self.assertNotIn("unpublished_versions", self.service.view(self.aid)["gradescope"])
        self.assertNotIn("automatic", self.sender.status(self.aid))  # nothing waits or was skipped: the default answer as before
        self.assertNothingForbidden()

    def test_a_late_paper_not_sent_by_faculty_says_why_on_its_row(self):
        self.add_carol()  # never added to the Gradescope course's roster
        self.send()
        self.sender.drain()
        self.certify()
        self.carol_paper()
        self.late().run(self.aid)
        self.sender.drain()
        [row] = self.automatic_rows()
        self.assertEqual(row["status"], "skipped")
        carol = next(x for x in self.service.view(self.aid)["students"] if x["row"] == self.row3)
        self.assertEqual((carol["gradescope"]["automatic"]["status"], carol["gradescope"]["automatic"]["reason"]), ("skipped", row["error"]))
        self.assertEqual(self.sender.status(self.aid)["automatic"]["sends"][0]["status"], "skipped")


class BubbleLateTests(LateTests):
    """The same workspace graded in two parts, written answers and a bubble sheet (the sheets as Certification and Export reads them, a
    sheet arriving moving the bubble marker), with a second section: Dan, section 502, joins the exam with no paper before it is
    certified (2026-10-09 review: no late-paper test had a bubble part, a DR or makeup student, or more than one section)."""

    def setUp(self):
        super().setUp()
        self.parts = ("frq", "bubble")
        self.frq.save_bubble_key(self.aid, {"request_id": str(uuid.uuid4()), "base_revision": None, "defaults": {"points": "1", "scoring": "exact"},
                                            "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "A"}, {"number": 2, "answer": "B"}]},
                                                         {"letter": "B", "questions": [{"number": 1, "answer": "C"}, {"number": 2, "answer": "D"}]}]})
        self.bubble = {self.enrollment: ("A", "2", 501), self.second: ("B", "1", 501)}  # enrollment -> (version, earned, section)
        original = self.frq._bubble_view

        def view(aid):
            plan, real = original(aid)
            sheets = [{"sheet_id": f"s-{enrollment}", "job_id": "j", "page": i, "student": "", "section": section, "enrollment_id": enrollment, "uin": "",
                       "marked_version": letter, "version": letter, "status": "scored", "reason": "", "earned": earned, "possible": "2", "wrong": [], "answers": []}
                      for i, (enrollment, (letter, earned, section)) in enumerate(sorted(self.bubble.items()))]
            return plan, {"key": real["key"], "sheets": sheets, "stats": {}, "waiting_scans": [], "counts": {}, "beyond_read": [], "read_questions": 2}
        self.enterContext(patch.object(self.frq, "_bubble_view", side_effect=view))
        # The marker the sheets are read from: whom each sheet is recorded for and what it says (`without` leaves a late student's out).
        self.enterContext(patch("grading.progress.bubble_marker", side_effect=lambda db, plan, without=frozenset():
                                digest(sorted([e, *map(str, v)] for e, v in self.bubble.items() if e not in without))))
        self.add_dan()

    def add_dan(self):
        """Section 502 joins the course, Dan its roster, and the exam that section (as a print job for it would add it)."""
        import io
        from test_rosters import csv_bytes
        state = self.client.get("/api/state").get_json()
        course = next(c for c in state["courses"] if 501 in c["sections"])
        changed = self.client.patch(f"/api/courses/{course['id']}", json={"version": course["version"], "sections": [501, 502], "multiple_sections": True},
                                    headers=certification_fixture.HEADERS)
        self.assertEqual(changed.status_code, 200, changed.get_json())
        group = self.client.get("/api/state").get_json()["course_groups"][0]
        result = self.client.post(f"/api/course-groups/{group['id']}/sections/502/roster", data={"file": (io.BytesIO(csv_bytes([DAN])), "roster.csv")},
                                  headers=certification_fixture.HEADERS)
        self.assertIn(result.status_code, (200, 201), result.get_json())
        with self.rosters.lock(), connect(self.path) as db:
            self.section2 = db.execute("SELECT id FROM course_sections WHERE number=502 AND term_id=?", (self.term_id,)).fetchone()[0]
            self.dan = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=?", (self.section2,)).fetchone()[0]
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.exams.extend_for_print(db, self.term_id, self.assignment["id"], [self.section2], "test"), [self.section2])
        self.row4 = f"{self.section2}:{self.dan}"

    def test_a_missing_bubble_sheet_that_arrives_is_certified_by_itself(self):
        del self.bubble[self.enrollment]  # Alice handed in her paper; her bubble sheet is missing
        self.certify()
        with connect(self.path) as db:
            row = db.execute("SELECT status,reason,parts_json FROM frq_certification_students WHERE enrollment_id=? ORDER BY rowid DESC LIMIT 1",
                             (self.enrollment,)).fetchone()
        self.assertEqual((row["status"], row["reason"], json.loads(row["parts_json"])["frq"]["state"]), ("scored", "missing_part", "complete"))
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))
        # Her written answers were certified as her own work: they are no sign of a late paper (the cheap test was always true here).
        self.assertFalse(self.candidates())
        self.bubble[self.enrollment] = ("A", "2", 501)  # the sheet is found
        self.assertTrue(self.candidates())
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row1], "live"), done)
        self.assertEqual(self.cells()[self.row1], ("scored", "12", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def test_a_late_sheet_waits_when_the_written_part_certified_before_has_changed(self):
        del self.bubble[self.enrollment]  # Alice's written answers are certified as her own work; her bubble sheet is missing
        self.certify()
        self.assertEqual(self.line(section=self.section["id"])["changed"], 1)  # the instructor's line then moves her certified 4 to 5
        self.bubble[self.enrollment] = ("A", "2", 501)  # the sheet is found
        self.assertTrue(self.candidates())
        done = self.late().run(self.aid)
        # Certifying her by itself would also record the changed written part, which the instructor has not certified.
        self.assertEqual(done["late"], [], done)
        self.assertEqual(done["waiting"], [{"row": self.row1, "reason": "A part certified before has changed: certify the assignment again"}])
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))

    def test_a_section_s_line_holds_only_its_section_and_a_second_late_paper_after_a_carried_one_returns_to_live(self):
        self.add_carol()
        self.certify()  # Carol (501) and Dan (502) have no work: 0
        self.assertEqual((self.cells()[self.row3], self.cells()[self.row4]), (("scored", "0", "grading"), ("scored", "0", "grading")))
        self.assertEqual(self.line(section=self.section["id"])["changed"], 1)  # section 501 only: Alice's 4 → 5
        self.certify()
        self.carol_paper(lines=("R3_2",))
        self.carol_paper(lines=("R3_2",), enrollment=self.dan, section_id=self.section2)
        self.bubble.update({self.carol: ("A", "2", 501), self.dan: ("A", "1", 502)})
        done = self.late().run(self.aid)
        # Dan's section has no line: certified (4 + 8 + 1). Carol's has one her grade lacks: she waits, so the rest of the plan does
        # not read as certified (carried markers).
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row4], "carried"), done)
        self.assertEqual(done["waiting"], [{"row": self.row3, "reason": "A line you added to every grade of Question 20 is not on this paper; certify it yourself."}])
        self.assertEqual((self.cells()[self.row4], self.cells()[self.row3]), (("scored", "13", "grading"), ("scored", "0", "grading")))
        self.assertEqual(json.loads(self.certified()[-1]["summary_json"])["automatic"]["pending"], [self.row4])
        # The instructor adds the line for Carol; her paper then certifies by itself, and with nothing else changed the plan reads
        # certified again (live markers: nobody is pending).
        self.assertEqual(self.line(section=self.section["id"])["changed"], 1)
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        latest = self.certified()[-1]
        self.assertEqual((json.loads(latest["summary_json"])["automatic"]["pending"], latest["amends_id"]), ([], None))
        self.assertEqual(sorted(json.loads(latest["summary_json"])["automatic"]["since_instructor"]), sorted([self.row3, self.row4]))
        self.assertEqual(self.cells()[self.row3], ("scored", "15", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def test_another_student_s_makeup_decided_after_certifying_keeps_a_late_paper_live(self):
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        absence = self.app.extensions["absences"].create({"request_id": str(uuid.uuid4()), "section_id": self.section2, "enrollment_id": self.dan,
                                                          "first_date": "2026-09-25", "last_date": "2026-09-25", "reason": "illness", "status": "pending"})
        preview = self.preview()
        self.assertEqual([r["row"] for r in next(c for c in preview["confirm"] if c["id"] == "makeup_ahead")["rows"]], [self.row4])
        self.certify(preview)
        self.decide_absence(absence, "excused")  # Dan still owes the makeup: nothing reopens
        self.carol_paper()
        self.bubble[self.carol] = ("A", "2", 501)
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["late"], done["markers"]), ("done", [self.row3], "live"), done)
        self.assertEqual(self.state()["state"], "current")
        self.assertNotIn(self.row4, self.cells())  # Dan still waits for his makeup


# Only this module's own tests: the certification tests the fixture class carries run in their own module.
for _name in [n for n in dir(certification_fixture.Certify) if n.startswith("test_") and n not in LateTests.__dict__]:
    setattr(LateTests, _name, None)
for _name in [n for n in LateTests.__dict__ if n.startswith("test_") and n not in BubbleLateTests.__dict__]:
    setattr(BubbleLateTests, _name, None)  # the one-part tests run once, in LateTests


if __name__ == "__main__":
    unittest.main()
