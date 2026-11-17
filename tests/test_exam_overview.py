"""The assessment lifecycle hub: a read-only projection keyed by (assignment, term), never storing or deciding anything."""
import json
import unittest
import uuid
from unittest.mock import patch

from course_storage import timestamp
from exam_workflow.overview import _next, _stage, assignment_stages
from rosters import connect
import test_exam_workflow as workflow_fixture
from test_exam_workflow import HEADERS


def states(result):
    return {stage["key"]: stage["state"] for stage in result["stages"]}


class ExamOverviewTests(unittest.TestCase):
    setUp = workflow_fixture.ExamWorkflowTests.setUp
    import_students = workflow_fixture.ExamWorkflowTests.import_students
    reload = workflow_fixture.ExamWorkflowTests.reload
    batch = workflow_fixture.ExamWorkflowTests.batch
    arrival = workflow_fixture.ExamWorkflowTests.arrival
    select = workflow_fixture.ExamWorkflowTests.select
    chart = workflow_fixture.ExamWorkflowTests.chart
    import_dr = workflow_fixture.ExamWorkflowTests.import_dr

    def overview(self, today=None):
        with patch("exam_workflow.overview.campus_today", return_value=today or "2026-09-01"):
            response = self.client.get(f"/api/exams/{self.event['id']}/overview")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def unattached_batch(self, assignment):
        """A ready print job of another assignment, not linked to any exam (the fixture's batch() links it to the exam)."""
        version_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, assignment["id"], version_id,
                "fixture/source.json", "a" * 64, "fixture/canonical.pdf", "b" * 64, 2, "{}", "faculty-page-v1", timestamp()))
        batch = self.documents.create_batch(version_id, self.section["id"], 1, str(uuid.uuid4()), students=[self.alice["enrollment_id"]])
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET status='ready',output_path='fixture/print.pdf',output_sha256=?,manifest_path='fixture/manifest.json',manifest_sha256=? WHERE id=?", ("c" * 64, "d" * 64, batch["id"]))
            db.execute("UPDATE page_stamps SET state='issued' WHERE printed_copy_id IN (SELECT id FROM printed_copies WHERE print_batch_id=?)", (batch["id"],))
        return self.documents.get_batch(batch["id"])

    def copy_of(self, batch, enrollment_id):
        return next(c for c in batch["copies"] if c["enrollment_id"] == enrollment_id)

    def delete_scan(self, item):
        with connect(self.path) as db:
            # The fixture never runs the worker: settle its inspection records so the ordinary delete is allowed.
            for table in ("document_decode_jobs", "scan_alias_ocr"):
                db.execute(f"UPDATE {table} SET status='complete' WHERE message_id=? AND status IN ('pending','running')", (item["pages"][0]["message_id"],))
            job = db.execute("SELECT id,version FROM inbox_jobs WHERE message_id=?", (item["pages"][0]["message_id"],)).fetchone()
        self.inbox.remove(job["id"], job["version"], "delete")

    def test_shared_stage_rules(self):
        ready = {"status": "ready", "superseded_at": None, "request_id": "r1"}
        self.assertEqual(assignment_stages(0, [], []), {"paper": "idle", "seating": "idle", "print": "idle"})
        # Printing by section without a chart settles seating; registration, superseded and cancelled jobs never count.
        self.assertEqual(assignment_stages(1, [], [ready]), {"paper": "done", "seating": "done", "print": "done"})
        ignored = [{"status": "failed", "superseded_at": None, "request_id": "unlabeled:run:section"},
                   {"status": "failed", "superseded_at": "2026-09-01", "request_id": "r2"},
                   {"status": "cancelled", "superseded_at": None, "request_id": "r3"}]
        self.assertEqual(assignment_stages(1, [], ignored), {"paper": "done", "seating": "idle", "print": "idle"})
        self.assertEqual(assignment_stages(1, [{"confirmed": True}, {"confirmed": False}], [ready, {**ready, "status": "failed", "request_id": "r4"}]),
                         {"paper": "done", "seating": "started", "print": "failed"})
        self.assertEqual(assignment_stages(1, [{"confirmed": True}], [{**ready, "status": "rendering"}])["print"], "started")

    def test_a_new_exam_starts_with_its_paper(self):
        result = self.overview()
        self.assertEqual(set(states(result).values()), {"idle"})
        self.assertEqual([s["key"] for s in result["stages"]],
                         ["paper", "seating", "notices", "print", "exam_day", "collected", "grading", "recorded"])
        self.assertEqual(result["next"]["title"], "Add the paper")
        self.assertEqual(result["next"]["href"], f"#documents?assignment={self.assignment['id']}&step=version")
        # With no print job yet, Print opens the creation step.
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "print")["href"], f"#documents?assignment={self.assignment['id']}&step=print")
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "exam_day")["detail"], "No date recorded")
        self.assertEqual((result["event_id"], result["assignment_id"], result["term_id"]), (self.event["id"], self.assignment["id"], self.term["id"]))
        self.assertIsNone(result["grading_plan_id"])

    def test_preparation_stages_readiness_and_next_step(self):
        batch = self.batch()
        with connect(self.path) as db:
            # A registration job that failed is no printing of this exam.
            db.execute("""INSERT INTO print_batches(id,assignment_version_id,section_id,term_id,quantity,status,creator,request_id,request_hash,configuration_json,created_at,updated_at)
                SELECT ?,assignment_version_id,section_id,term_id,1,'failed','test',?,'h','{}',?,? FROM print_batches WHERE id=?""",
                       (str(uuid.uuid4()), "unlabeled:run:" + self.section["id"], timestamp(), timestamp(), batch["id"]))
        result = self.overview("2026-09-01")
        self.assertEqual({k: v for k, v in states(result).items() if k in ("paper", "seating", "print")}, {"paper": "done", "seating": "done", "print": "done"})
        self.assertEqual(result["stages"][3]["counts"]["registration"], 1)
        # Existing print jobs open in the Print jobs list for this assignment (never a second Create print job), with their copies;
        # a section's job is printed by section, not as one stack. The registration job counts nowhere.
        printing = result["stages"][3]
        self.assertEqual((printing["href"], printing["detail"]), (f"#documents?jobs={self.assignment['id']}", "1 of 1 print job ready · 2 copies"))
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "seating")["detail"], "No seating chart · printed by section")
        self.assertEqual(next(item for item in result["readiness"] if item["key"] == "print")["href"], printing["href"])
        chart = self.chart()
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-10-01',exam_time='7:05 PM' WHERE id=?", (chart["id"],))
        result = self.overview("2026-09-01")
        self.assertEqual(states(result)["seating"], "started")
        self.assertEqual(result["phase"], "upcoming")
        self.assertEqual((result["dates"]["date"], result["dates"]["time"], result["dates"]["rooms"]), ("2026-10-01", "7:05 PM", ["HECC 209"]))
        # The card's date names the chart it comes from, where it is changed (Create assignment's Given on says so, 2026-10-02).
        row = next(r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == self.assignment["id"])
        self.assertEqual((row["exam_date"], row["exam_date_chart_id"]), ("2026-10-01", chart["id"]))
        self.assertEqual(result["next"]["title"], "Confirm the seating")
        self.assertEqual(result["next"]["href"], f"#seating/{chart['id']}")
        readiness = {item["key"]: item for item in result["readiness"]}
        self.assertFalse(readiness["charts"]["ok"])
        self.assertTrue(readiness["print"]["ok"])
        # After the exam date the checklist is gone and the preparation stages no longer lead.
        after = self.overview("2026-10-02")
        self.assertEqual((after["phase"], after["readiness"], states(after)["exam_day"]), ("past", [], "done"))
        self.assertEqual(after["next"]["title"], "Scan the papers")
        # The step opens the Inbox's Upload scanned assignment window (`#inbox?upload=1`, read once), not just the list.
        self.assertEqual((after["next"]["label"], after["next"]["href"]), ("Upload scans", "#inbox?upload=1"))

    def test_collection_grading_plan_and_deleted_scans(self):
        batch = self.batch()
        observations = self.arrival(self.copy_of(batch, self.alice["enrollment_id"]), [1, 2])
        result = self.overview()
        collected = next(s for s in result["stages"] if s["key"] == "collected")
        self.assertEqual((collected["state"], collected["counts"]["attention"]), ("started", 1))
        self.assertEqual(collected["href"], f"#exams/{self.event['id']}?tab=collection")
        self.assertEqual(result["next"]["title"], "Review the collected papers")
        self.assertEqual(states(result)["exam_day"], "done")
        # A deleted scan counts nowhere (PRESENT).
        self.delete_scan(observations)
        result = self.overview()
        self.assertEqual(states(result)["collected"], "idle")
        # A second arrival, reviewed into an assembly: one paper in, one student without a scan.
        second = self.arrival(self.copy_of(batch, self.alice["enrollment_id"]), [1, 2], suffix="again")
        self.select(self.copy_of(batch, self.alice["enrollment_id"]), [second])
        result = self.overview()
        collected = next(s for s in result["stages"] if s["key"] == "collected")
        self.assertEqual((collected["counts"]["received"], collected["counts"]["no_scan"]), (1, 1))
        self.assertEqual(result["next"]["title"], "Grade the papers")
        # With no grading plan the assessment is not on Grading yet: the step and the Grading tile lead to the Teaching Inbox.
        self.assertEqual((result["next"]["label"], result["next"]["href"]), ("Teaching Inbox", "#inbox"))
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "grading")["href"], "#inbox")
        # The assisted-grading plan that hangs off the exam is linked, and its entry says what it waits for.
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        self.assertEqual(self.reload()["grading_plan_id"], plan["id"])
        result = self.overview()
        self.assertEqual(result["grading_plan_id"], plan["id"])
        grading = next(s for s in result["stages"] if s["key"] == "grading")
        self.assertIn(grading["state"], ("idle", "started"))
        self.assertTrue(grading["href"].startswith("#inbox"))

    def test_papers_scanned_before_a_main_date_still_ahead(self):
        # DR or early makeup papers scanned before the main sitting: the exam day and its checklist stay ahead.
        batch = self.batch()
        chart = self.chart()
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-10-01',exam_time='7:05 PM' WHERE id=?", (chart["id"],))
        self.arrival(self.copy_of(batch, self.alice["enrollment_id"]), [1, 2])
        result = self.overview("2026-09-01")
        self.assertEqual((result["phase"], states(result)["exam_day"], states(result)["collected"]), ("upcoming", "started", "started"))
        self.assertTrue(result["readiness"])
        self.assertEqual(result["next"]["title"], "Confirm the seating")
        self.assertEqual(states(self.overview("2026-10-02"))["exam_day"], "done")

    def linked_quiz_scan(self, plan):
        """A scan the Inbox associated with the plan whose pages carry no label (a quiz printed without labels)."""
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "quiz.pdf", "content": b"%PDF-quiz-" + uuid.uuid4().bytes}],
            source="email", sender="scanner", original=b"retained email", idempotency_key=str(uuid.uuid4()), authenticated=True,
            instructor_review_required=True)
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_scan_links(message_id,assignment_id,source,request_id,request_sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (job["message_id"], plan["id"], "workspace", str(uuid.uuid4()), "e" * 64, timestamp()))
        return job

    def test_a_linked_scan_ends_preparation_without_printed_labels(self):
        # Quiz papers printed without labels: no print job, no seating chart, no label seen; the Inbox linked the scan to the plan.
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        row = next(r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == self.assignment["id"])
        self.assertFalse(row["collected"])
        job = self.linked_quiz_scan(plan)
        result = self.overview()
        self.assertEqual(result["phase"], "past")
        self.assertEqual(result["readiness"], [])
        self.assertEqual(states(result)["exam_day"], "done")
        self.assertEqual(result["next"]["stage"], "grading")
        self.assertNotIn(result["next"]["title"], ("Print the papers", "Add the paper", "Confirm the seating"))
        # The Assignments card agrees: collected, so its next thing is no longer a preparation stage.
        row = next(r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == self.assignment["id"])
        self.assertTrue(row["collected"])
        # A deleted scan counts nowhere (PRESENT): preparation is open again (the hub's grading read is reused for a while).
        with connect(self.path) as db:  # the fixture never runs the worker: settle its inspection so the ordinary delete is allowed
            for table in ("document_decode_jobs", "scan_alias_ocr"):
                db.execute(f"UPDATE {table} SET status='complete' WHERE message_id=? AND status IN ('pending','running')", (job["message_id"],))
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete")
        row = next(r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == self.assignment["id"])
        self.assertFalse(row["collected"])

    def test_archived_gradebook_columns_are_not_recorded(self):
        with connect(self.path) as db:
            db.execute("INSERT INTO gradebook_setups(id,term_id,revision,setup_json,sha256,request_id,request_sha256,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), self.term["id"], 1, json.dumps({"items": [{"id": "old", "assignment_id": self.assignment["id"], "archived": True}]}),
                        "e" * 64, "setup-1", "f" * 64, "test", timestamp()))
        recorded = next(s for s in self.overview()["stages"] if s["key"] == "recorded")
        self.assertEqual((recorded["state"], recorded["detail"]), ("idle", "No gradebook column yet"))
        # Recorded opens the gradebook on this assignment: its column, or the offer to add (or link) one.
        self.assertEqual(recorded["href"], f"#gradebook/{self.term['id']}?assignment={self.assignment['id']}")

    def test_a_rebuilt_paper_names_the_versions_its_current_jobs_print(self):
        self.batch()
        self.assertEqual(next(s for s in self.overview()["stages"] if s["key"] == "paper")["detail"], "1 version registered")
        # An earlier layout stays registered, its job superseded: the hub says what prints now, as the Assignments card does.
        old = self.unattached_batch(self.assignment)
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET superseded_at=? WHERE id=?", (timestamp(), old["id"]))
        self.assertEqual(next(s for s in self.overview()["stages"] if s["key"] == "paper")["detail"], "1 version printed · 2 registered")
        # A current job of another version still waiting to render has printed nothing yet: that version is registered, not printed.
        version_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, self.assignment["id"], version_id,
                "fixture/source.json", "a" * 64, "fixture/canonical.pdf", "b" * 64, 2, "{}", "faculty-page-v1", timestamp()))
        waiting = self.documents.create_batch(version_id, self.section["id"], 1, str(uuid.uuid4()), students=[self.alice["enrollment_id"]])
        self.assertNotEqual(self.documents.get_batch(waiting["id"])["status"], "ready")
        print_stage = next(s for s in self.overview()["stages"] if s["key"] == "print")
        self.assertEqual(print_stage["counts"]["current"], 2)
        self.assertEqual(next(s for s in self.overview()["stages"] if s["key"] == "paper")["detail"], "1 version printed · 3 registered")

    def test_recording_without_a_column_is_only_a_suggestion(self):
        done = {key: _stage(key, "done", "") for key in ("paper", "seating", "notices", "print", "exam_day", "collected", "grading")}
        stages = [*done.values(), _stage("recorded", "idle", "No gradebook column yet")]
        self.assertTrue(_next(stages, True, {"id": "e"})["soft"])
        stages[-1] = _stage("recorded", "started", "1 of 2 students recorded", counts={"columns": 1})
        self.assertNotIn("soft", _next(stages, True, {"id": "e"}))

    def test_grading_ends_with_a_current_certification(self):
        """The sixth grading stage, Certification and Export (requirement, 2026-09-26): every grade reviewed is not the end of grading
        until a current certification says so; certifying records the grades, so the next thing is to certify, never to pull."""
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        reviewed = {"plan_id": plan["id"], "entry": {"ready": True, "retained_grades": True}, "progress": {"review": {"completed": 4, "total": 4}, "grade": {"completed": 4}},
                    "next_tab": "export", "failed": 0, "decision_responses": 0, "version_decisions": 0}
        for certification, state, detail in (({"state": "none"}, "started", "All reviewed · ready to certify"),
                                             ({"state": "changed", "changed": ["grades"]}, "started", "Changed since certification"),
                                             ({"state": "current", "left_out": 0}, "done", "4 of 4 reviewed")):
            with patch("exam_workflow.overview.grading_summary", return_value={**reviewed, "certification": certification}), \
                    patch("exam_workflow.overview.GRADING_EVERY", 0):
                result = self.overview(today="2026-10-01")
            grading = next(s for s in result["stages"] if s["key"] == "grading")
            self.assertEqual((grading["state"], grading["detail"]), (state, detail), certification)
            if state == "started":
                self.assertEqual(grading["href"], f"#grading/{plan['id']}?tab=export")
                self.assertEqual((result["next"]["title"], result["next"]["href"]), ("Certify grades", f"#grading/{plan['id']}?tab=export"))
            else:
                self.assertNotEqual(result["next"]["title"], "Certify grades")
        # A plan read before the summary carried a certification reads as never certified.
        with patch("exam_workflow.overview.grading_summary", return_value=reviewed), patch("exam_workflow.overview.GRADING_EVERY", 0):
            self.assertEqual(next(s for s in self.overview(today="2026-10-01")["stages"] if s["key"] == "grading")["state"], "started")

    def test_all_reviewed_is_not_ready_to_certify_while_work_is_still_coming(self):
        """READY TO CERTIFY MEANS NOTHING IS STILL COMING (ASSIGNMENT-WORKSPACE, 2026-10-11): certification refuses while a linked scan
        waits for its Inbox checks or a grading task runs, so the stage names that and opens it; the next step is never Certify."""
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        reviewed = {"plan_id": plan["id"], "entry": {"ready": True, "retained_grades": True, "inbox_href": "#inbox"},
                    "progress": {"review": {"completed": 4, "total": 4}, "grade": {"completed": 4}},
                    "next_tab": "export", "failed": 0, "decision_responses": 0, "version_decisions": 0}
        late = [{"job_id": "cleared", "inbox_ready": True}, {"job_id": "late", "inbox_ready": False}]
        for coming, certification, detail, href, title in (
                ({"awaiting_review": 1, "scans": late}, "none", "4 of 4 reviewed · waiting for scan checks on 1 scan", "#inbox/job/late", "Finish the scan checks"),
                ({"awaiting_review": 1, "scans": late}, "changed", "4 of 4 reviewed · waiting for scan checks on 1 scan", "#inbox/job/late", "Finish the scan checks"),
                # A summary that names no scan still opens the Inbox.
                ({"awaiting_review": 2}, "none", "4 of 4 reviewed · waiting for scan checks on 2 scans", "#inbox", "Finish the scan checks"),
                ({"pending": 3}, "none", "4 of 4 reviewed · 3 grading tasks queued or running", f"#grading/{plan['id']}?tab=jobs", "Continue grading"),
                # The scan is said first: its papers are not in grading at all yet.
                ({"awaiting_review": 1, "pending": 3, "scans": late}, "none", "4 of 4 reviewed · waiting for scan checks on 1 scan", "#inbox/job/late", "Finish the scan checks")):
            with patch("exam_workflow.overview.grading_summary", return_value={**reviewed, **coming, "certification": {"state": certification}}), \
                    patch("exam_workflow.overview.GRADING_EVERY", 0):
                result = self.overview(today="2026-10-01")
            grading = next(s for s in result["stages"] if s["key"] == "grading")
            self.assertEqual((grading["state"], grading["detail"], grading["href"]), ("started", detail, href), coming)
            self.assertFalse(grading["counts"]["certify"], coming)
            self.assertEqual(grading["counts"].get("pending_scans"), coming.get("awaiting_review"), coming)
            self.assertEqual((result["next"]["title"], result["next"]["href"]), (title, href), coming)
        # Nothing coming: ready as before. A current certification is not reopened by this stage (the workspace's own view shows the
        # late scan, CERTIFIED-ARCHIVE).
        for coming, certification, state, detail in (({"awaiting_review": 0, "pending": 0, "scans": late[:1]}, "none", "started", "All reviewed · ready to certify"),
                                                     ({"awaiting_review": 1, "scans": late}, "current", "done", "4 of 4 reviewed")):
            with patch("exam_workflow.overview.grading_summary", return_value={**reviewed, **coming, "certification": {"state": certification}}), \
                    patch("exam_workflow.overview.GRADING_EVERY", 0):
                grading = next(s for s in self.overview(today="2026-10-01")["stages"] if s["key"] == "grading")
            self.assertEqual((grading["state"], grading["detail"]), (state, detail), coming)
            self.assertNotIn("pending_scans", grading["counts"])

    def test_only_dr_students_ahead_leave_recording_short_without_a_next_step(self):
        done = {key: _stage(key, "done", "") for key in ("paper", "seating", "notices", "print", "exam_day", "collected")}
        grading = _stage("grading", "done", "", counts={"certification": "current", "left_out": 2})
        waiting = _stage("recorded", "started", "338 of 340 students recorded · 2 left out until their DR exam",
                         counts={"columns": 1, "recorded": 338, "students": 340, "left_out": 2})
        self.assertIsNone(_next([*done.values(), grading, waiting], True, {"id": "e"}))
        short = _stage("recorded", "started", "337 of 340 students recorded", "#gradebook/t?assignment=a", counts={"columns": 1, "recorded": 337, "students": 340, "left_out": 2})
        # Certified: the grades are recorded already, so the step is the student certification left without a grade, never 'Pull'.
        settle = _next([*done.values(), grading, short], True, {"id": "e"})
        self.assertEqual((settle["title"], settle["label"], settle["href"]), ("Settle the students without a grade", "Gradebook", "#gradebook/t?assignment=a"))
        self.assertEqual(settle["detail"], "1 of 340 students has no grade; type the missing grades in the gradebook.")
        self.assertNotIn("soft", settle)
        # Students without a scanned paper are found on Collection first.
        unscanned = _stage("collected", "started", "338 of 340 papers · 2 without a scanned paper", "#exams/e?tab=collection", counts={"no_scan": 2})
        settle = _next([*[done[key] for key in ("paper", "seating", "notices", "print", "exam_day")], unscanned, grading, short], True, {"id": "e"})
        self.assertEqual((settle["title"], settle["label"], settle["href"]), ("Settle the students without a grade", "Collection", "#exams/e?tab=collection"))
        self.assertIn("2 have no scanned paper: find the paper, or record an absence or excuse.", settle["detail"])
        # Never certified: the grades still have to be pulled into the gradebook.
        uncertified = _stage("grading", "done", "", counts={"certification": "none"})
        self.assertEqual(_next([*done.values(), uncertified, short], True, {"id": "e"})["title"], "Record the grades")
        certify = _stage("grading", "started", "All reviewed · ready to certify", "#grading/p?tab=export", counts={"certify": True})
        self.assertEqual(_next([*done.values(), certify, waiting], True, {"id": "e"})["title"], "Certify grades")

    def test_assessment_without_an_exam_record_and_the_listing(self):
        other = self.documents.create_assignment(self.term["definition_id"], "quiz-2", "Quiz 2")
        batch = self.unattached_batch(other)
        response = self.client.get("/api/exams/lifecycle", query_string={"assignment_id": other["id"], "term_id": self.term["id"]})
        self.assertEqual(response.status_code, 200, response.json)
        result = response.json
        self.assertIsNone(result["event_id"])
        self.assertEqual((result["title"], result["sections"]), ("Quiz 2", [501]))
        self.assertEqual(states(result)["collected"], "idle")
        observations = self.arrival(self.copy_of(batch, self.alice["enrollment_id"]), [1, 2])
        seen = self.client.get("/api/exams/lifecycle", query_string={"assignment_id": other["id"], "term_id": self.term["id"]}).json
        self.assertEqual(next(s for s in seen["stages"] if s["key"] == "collected")["counts"]["seen"], 1)
        self.delete_scan(observations)
        gone = self.client.get("/api/exams/lifecycle", query_string={"assignment_id": other["id"], "term_id": self.term["id"]}).json
        self.assertEqual(next(s for s in gone["stages"] if s["key"] == "collected")["counts"]["seen"], 0)
        listing = self.client.get("/api/exams").json
        self.assertEqual([item["id"] for item in listing["exams"]], [self.event["id"]])
        self.assertEqual(listing["exams"][0]["summary"]["event_id"], self.event["id"])
        self.assertEqual([(a["assignment_id"], a["term_id"]) for a in listing["assessments"]], [(other["id"], self.term["id"])])
        self.assertEqual(self.client.get("/api/exams", query_string={"summary": "0"}).json, {"exams": listing["exams"] and [
            {k: v for k, v in item.items() if k != "summary"} for item in listing["exams"]]})
        self.assertEqual(self.client.get("/api/exams/lifecycle", query_string={"assignment_id": other["id"]}).status_code, 400)
        self.assertEqual(self.client.get("/api/exams/lifecycle", query_string={"assignment_id": "missing", "term_id": self.term["id"]}).status_code, 404)

    def test_read_only_and_free_of_student_identifiers(self):
        batch = self.batch()
        self.chart()
        self.arrival(self.copy_of(batch, self.alice["enrollment_id"]), [1, 2])
        with connect(self.path) as db:
            tables = {row[0]: db.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
                      for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        text = json.dumps([self.overview(), self.client.get("/api/exams").json])
        with connect(self.path) as db:
            after = {row[0]: db.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
                     for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, after)
        enrollments = [p["enrollment_id"] for p in self.reload()["participants"]]
        for secret in ["Example", "Alice", "001234567", "123456789", *enrollments]:
            self.assertNotIn(secret, text)

    def test_exam_record_routes_stay_private_and_writes_need_the_workspace(self):
        self.assertEqual(self.client.get("/api/exams/not-an-exam/overview").status_code, 404)
        # The hub adds no write route: POST to the read routes is not allowed.
        self.assertIn(self.client.post(f"/api/exams/{self.event['id']}/overview", json={}, headers=HEADERS).status_code, (403, 404, 405))

    def test_an_unreadable_grading_plan_never_fails_the_list_or_the_hub(self):
        """Review fix (2026-09-26): one roster payload that cannot be read (an archive drive not mounted) made GET /api/exams 503."""
        from course_storage import StorageError
        self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        with patch("exam_workflow.overview.grading_summary", side_effect=StorageError("The archive drive is not mounted.")), \
                self.assertLogs("exam_workflow.overview", "WARNING"):
            listing = self.client.get("/api/exams")
            hub = self.overview()
        self.assertEqual(listing.status_code, 200, listing.json)
        summary = next(item["summary"] for item in listing.json["exams"] if item["id"] == self.event["id"])
        grading = next(s for s in summary["stages"] if s["key"] == "grading")
        self.assertEqual(grading["state"], "idle")
        self.assertIn("could not be read", grading["detail"])
        self.assertIn("could not be read", next(s for s in hub["stages"] if s["key"] == "grading")["detail"])
        # A readable plan is read at most every GRADING_EVERY seconds per plan, not on every poll.
        with patch("exam_workflow.overview.grading_summary", wraps=__import__("exam_workflow.overview", fromlist=["x"]).grading_summary) as read:
            self.overview(); self.overview(); self.client.get("/api/exams")
        self.assertEqual(read.call_count, 1)

    def approve_quiz_draft(self, plan):
        """The record a quiz draft's approval leaves (grading/quiz_drafts.py): complete, naming the grading plan it set up."""
        draft, revision, now = str(uuid.uuid4()), str(uuid.uuid4()), timestamp()
        with connect(self.path) as db:
            db.execute("INSERT INTO quiz_drafts(id,definition_id,request_id,request_sha256,input_json,created_at) VALUES(?,?,?,?,?,?)",
                       (draft, self.term["definition_id"], str(uuid.uuid4()), "a" * 64, "{}", now))
            db.execute("""INSERT INTO quiz_draft_revisions(id,draft_id,number,request_id,request_sha256,input_json,profile_json,status,result_json,
                created_at,updated_at) VALUES(?,?,1,?,?,'{}','{}','complete','{}',?,?)""", (revision, draft, str(uuid.uuid4()), "b" * 64, now, now))
            db.execute("""INSERT INTO quiz_draft_approvals(id,draft_id,revision_id,term_id,request_id,request_sha256,input_json,status,result_json,actor,
                created_at,updated_at) VALUES(?,?,?,?,?,?,'{}','complete',?,'workspace',?,?)""",
                       (str(uuid.uuid4()), draft, revision, self.term["id"], str(uuid.uuid4()), "c" * 64, json.dumps({"plan_id": plan["id"]}), now, now))

    def test_a_drafted_quiz_is_not_given_until_a_scan_of_it_arrives(self):
        """Review fix (2026-10-02): a quiz set up by its draft's approval is on Grading before any paper exists; the hub read that as
        'Papers scanned', past, with no checklist. Printed, undated and unscanned, it is ready and asks for its scans once given."""
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        self.approve_quiz_draft(plan)
        self.batch()
        result = self.overview()
        self.assertEqual((result["phase"], states(result)["exam_day"], states(result)["collected"]), ("planning", "idle", "idle"))
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "exam_day")["detail"], "No date recorded")
        self.assertIn("print", [item["key"] for item in result["readiness"]])
        self.assertEqual({k: result["next"][k] for k in ("title", "label", "href", "stage")},
                         {"title": "Upload the scans once it is given", "label": "Upload scans", "href": "#inbox?upload=1", "stage": "collected"})
        grading = next(s for s in result["stages"] if s["key"] == "grading")
        self.assertEqual(grading["state"], "started")
        self.assertIn(grading["detail"], ("Rubrics ready · waiting for the papers", "Set up from the approved quiz · finish the rubrics"))
        # The Exams list says the same.
        listed = next(item["summary"] for item in self.client.get("/api/exams").json["exams"] if item["id"] == self.event["id"])
        self.assertEqual((listed["phase"], listed["next"]["title"]), ("planning", "Upload the scans once it is given"))
        # A scan the Inbox linked to the plan means the quiz was written: its Inbox checks are next (the new scan link is read at once).
        self.linked_quiz_scan(plan)
        result = self.overview()
        self.assertEqual((result["phase"], result["readiness"], states(result)["exam_day"]), ("past", [], "done"))
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "exam_day")["detail"], "Papers scanned · no date recorded")
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "grading")["detail"], "Waiting for scan checks on 1 scan")
        self.assertEqual(result["next"]["title"], "Finish the scan checks")

    def test_a_drafted_quizs_rubrics_wait_for_the_papers(self):
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        self.batch()
        summary = {"plan_id": plan["id"], "entry": {"ready": True, "set_up_by_quiz_draft": True, "cleared_scans": 0, "pending_scans": 0},
                   "progress": {"review": {"completed": 0, "total": 0}, "grade": {"completed": 0}}, "next_tab": "jobs", "failed": 0,
                   "decision_responses": 0, "version_decisions": 0, "certification": {"state": "none"}}
        with patch("exam_workflow.overview.grading_summary", return_value=summary):
            result = self.overview()
        self.assertEqual((result["phase"], states(result)["exam_day"]), ("planning", "idle"))
        self.assertIn("print", [item["key"] for item in result["readiness"]])
        grading = next(s for s in result["stages"] if s["key"] == "grading")
        self.assertEqual((grading["state"], grading["detail"], grading["href"]), ("started", "Rubrics ready · waiting for the papers", f"#grading/{plan['id']}?tab=jobs"))
        self.assertEqual(result["next"]["title"], "Upload the scans once it is given")
        # Once its date has passed with nothing scanned, its scans are still the next thing, not 'Continue grading'.
        done = {key: _stage(key, "done", "") for key in ("paper", "seating", "notices", "print", "exam_day")}
        waiting = [*done.values(), _stage("collected", "idle", "No papers scanned · 2 students expected", "#exams/e?tab=collection"), grading,
                   _stage("recorded", "idle", "No gradebook column yet")]
        self.assertEqual(_next(waiting, True, {"id": "e"})["title"], "Scan the papers")
        # A dated exam that is ready has no step: the hub says it is ready for the day.
        dated = [*[_stage(key, "done", "") for key in ("paper", "seating", "notices", "print")], _stage("exam_day", "started", "Thu, Oct 1"),
                 _stage("collected", "idle", ""), grading, _stage("recorded", "idle", "")]
        self.assertIsNone(_next(dated, False, {"id": "e"}))

    def test_grading_set_up_before_the_papers_is_on_grading_and_written_by_no_one_yet(self):
        """Set up grading now (requirement, 2026-10-02): the instructor's explicit action makes the plan before any paper, as a quiz's
        approval does; the hub, the Assignments card and Grading list it, and nothing reads it as papers written."""
        self.batch()
        result = self.overview()
        grading = next(s for s in result["stages"] if s["key"] == "grading")
        self.assertEqual((grading["state"], grading["detail"], grading["href"]), ("idle", "Not set up yet · set it up now, or associate a scan in the Teaching Inbox", "#inbox"))
        row = next(r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == self.assignment["id"])
        self.assertEqual((row["grading_entry_plan_id"], row["grading_plan_id"], row["has_grading_plan"]), (None, None, False))
        # The route: the exam record's plan, marked as set up before the papers. The fixture's version has no retained PDF: its
        # template is named as not imported, and the plan stands (step 1 imports it).
        created = self.client.post("/api/grading/assignments", json={"event_id": self.event["id"]}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        plan = created.json
        self.assertEqual((plan["entry"]["ready"], plan["entry"].get("set_up_before_papers"), plan["entry"].get("set_up_by_quiz_draft")), (True, True, None))
        self.assertEqual(plan["set_up"]["created"], True)
        self.assertEqual(len(plan["set_up"]["templates"]), 1)
        self.assertIn("error", plan["set_up"]["templates"][0])
        with connect(self.path) as db:
            event = db.execute("SELECT detail_json,actor FROM frq_events WHERE assignment_id=? AND kind='assignment_created'", (plan["id"],)).fetchone()
        self.assertEqual((json.loads(event["detail_json"]), event["actor"]), ({"event_id": self.event["id"], "before_papers": True}, "workspace"))
        # Asking again returns the same plan and makes nothing.
        again = self.client.post("/api/grading/assignments", json={"event_id": self.event["id"]}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["id"], again.json["set_up"]["created"]), (200, plan["id"], False))
        # Grading lists it; the card links it; the hub says what is left, and the exam is still ahead of its papers.
        listed = self.app.extensions["frq_grading"].overview(navigation=True)["assignments"]
        self.assertEqual([(p["id"], p["entry"].get("set_up_before_papers")) for p in listed], [(plan["id"], True)])
        row = next(r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == self.assignment["id"])
        self.assertEqual((row["grading_entry_plan_id"], row["grading_plan_id"], row["has_grading_plan"], row["collected"]), (plan["id"], None, True, False))
        result = self.overview()
        grading = next(s for s in result["stages"] if s["key"] == "grading")
        self.assertEqual((grading["state"], grading["detail"]), ("started", "Set up before the papers · finish the rubrics"))
        self.assertEqual((result["phase"], states(result)["exam_day"], states(result)["collected"]), ("planning", "idle", "idle"))
        self.assertIn("print", [item["key"] for item in result["readiness"]])
        self.assertEqual(result["next"]["title"], "Upload the scans once it is given")
        # A scan the Inbox linked to it means the papers were written: its Inbox checks are next.
        self.linked_quiz_scan(plan)
        result = self.overview()
        self.assertEqual((result["phase"], states(result)["exam_day"]), ("past", "done"))
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "grading")["detail"], "Waiting for scan checks on 1 scan")

    def test_setting_up_an_assessment_without_an_exam_record_makes_one(self):
        """{assignment_id, term_id}: the exam record is made by the Exams page's own creation, for the sections its print jobs name
        (else every section of the term), then reused; a plan an earlier scan made is returned as it is."""
        other = self.documents.create_assignment(self.term["definition_id"], "quiz-5", "Quiz 5")
        refused = self.client.post("/api/grading/assignments", json={"assignment_id": other["id"], "term_id": "nope"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400, refused.json)
        created = self.client.post("/api/grading/assignments", json={"assignment_id": other["id"], "term_id": self.term["id"]}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        with connect(self.path) as db:
            events = db.execute("SELECT id FROM exam_events WHERE assignment_id=? AND term_id=?", (other["id"], self.term["id"])).fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(created.json["event_id"], events[0]["id"])
            sections = {r[0] for r in db.execute("SELECT section_id FROM exam_event_sections WHERE event_id=?", (events[0]["id"],))}
        self.assertEqual(sections, {s["id"] for s in self.sections if s["term_id"] == self.term["id"]})  # nothing printed: every section
        self.assertEqual(created.json["set_up"]["templates"], [])  # no paper registered yet
        again = self.client.post("/api/grading/assignments", json={"assignment_id": other["id"], "term_id": self.term["id"]}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["id"]), (200, created.json["id"]))
        # An assessment printed for one section only: its exam record is for that section.
        third = self.documents.create_assignment(self.term["definition_id"], "quiz-6", "Quiz 6")
        self.unattached_batch(third)  # a job for Section 501, linked to no exam
        made = self.client.post("/api/grading/assignments", json={"assignment_id": third["id"], "term_id": self.term["id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        with connect(self.path) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT section_id FROM exam_event_sections WHERE event_id=?", (made.json["event_id"],))},
                             {self.section["id"]})
        # A plan the Inbox made from a scan is not 'set up before the papers': asking returns it, unchanged and not on Grading.
        scanned = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        same = self.client.post("/api/grading/assignments", json={"event_id": self.event["id"]}, headers=HEADERS)
        self.assertEqual((same.status_code, same.json["id"], same.json["entry"]["ready"]), (200, scanned["id"], False))
        self.assertNotIn("set_up_before_papers", same.json["entry"])

    def register_pdf(self, assignment, label, fixture="letter"):
        """A registered PDF version Set up grading now can import (the template conversion is scripted by the caller)."""
        from pathlib import Path
        fixtures = Path(__file__).resolve().parents[1] / "document_identity/fixtures"
        return self.documents.register_version(assignment["id"], label, (fixtures / f"{fixture}.pdf").read_bytes(),
                                               {"main.tex": (fixtures / f"{fixture}.tex").read_bytes()}, {"fixture": fixture})

    def test_asking_again_for_an_exam_record_that_has_a_plan_imports_nothing(self):
        """Review fix (2026-10-02): an exam record that already has a plan (here one a scan's association made) is returned as it is,
        and nothing is made: no blank template either, even for a registered PDF that has none (a stale page or a second click must
        not change step 1's template choice or run conversions)."""
        from test_assignment_templates import prepared
        scanned = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        version = self.register_pdf(self.assignment, "A")
        with patch("document_identity.template_conversion.convert_template", side_effect=prepared) as converted:
            again = self.client.post("/api/grading/assignments", json={"event_id": self.event["id"]}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["id"], again.json["set_up"]), (200, scanned["id"], {"created": False, "templates": []}))
        converted.assert_not_called()
        self.assertEqual([t for t in self.app.extensions["assignment_templates"].listing(self.assignment["id"])
                          if t["source_version_id"] == version["id"]], [])

    def test_set_up_imports_the_versions_printed_this_term(self):
        """Review fix (2026-10-02): once the term printed some of the assignment's versions, only those become blank templates (never
        another term's paper of the course-wide assignment); with nothing printed yet, every registered PDF version does. A failure
        of any kind names its version and the plan stands."""
        from test_assignment_templates import prepared
        quiz = self.documents.create_assignment(self.term["definition_id"], "quiz-7", "Quiz 7")
        printed, other = self.register_pdf(quiz, "A"), self.register_pdf(quiz, "B", "letter-corners")
        self.documents.create_batch(printed["id"], self.section["id"], 1, str(uuid.uuid4()), students=[self.alice["enrollment_id"]])
        with patch("document_identity.template_conversion.convert_template", side_effect=prepared):
            made = self.client.post("/api/grading/assignments", json={"assignment_id": quiz["id"], "term_id": self.term["id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        self.assertEqual([(t["version_id"], "template_id" in t) for t in made.json["set_up"]["templates"]], [(printed["id"], True)])
        self.assertNotIn(other["id"], [t["source_version_id"] for t in made.json["templates"]])
        # Nothing printed in the term: both versions; an unexpected failure (here an OSError) names the version, the plan is made.
        unprinted = self.documents.create_assignment(self.term["definition_id"], "quiz-8", "Quiz 8")
        first, second = self.register_pdf(unprinted, "A"), self.register_pdf(unprinted, "B", "letter-corners")
        calls = []

        def convert(files, entrypoint=None):
            calls.append(files)
            if len(calls) == 2:
                raise OSError("disk full")
            return prepared(files, entrypoint)
        with patch("document_identity.template_conversion.convert_template", side_effect=convert), self.assertLogs("grading.assignments", "ERROR"):
            made = self.client.post("/api/grading/assignments", json={"assignment_id": unprinted["id"], "term_id": self.term["id"]}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        templates = made.json["set_up"]["templates"]
        self.assertEqual([t["version_id"] for t in templates], [first["id"], second["id"]])
        self.assertIn("template_id", templates[0])
        self.assertEqual(templates[1]["error"], "Its blank template could not be prepared; prepare it in Rubrics & Templates.")
        self.assertTrue(made.json["entry"]["ready"])

    def test_a_printed_undated_exam_with_dr_bookings_asks_for_its_scans(self):
        """Review fix (2026-10-02): DR bookings start Exam day without giving the exam a main date, so the hub said only 'Nothing
        to do yet'. Printed, undated and unscanned, the next thing is its scans; a main date ahead keeps 'Ready for the day'."""
        self.import_dr()
        self.batch()
        result = self.overview()
        exam_day = next(s for s in result["stages"] if s["key"] == "exam_day")
        self.assertEqual((exam_day["state"], exam_day["counts"]["dr_bookings"], result["dates"]["date"]), ("started", 1, None))
        self.assertEqual((result["phase"], states(result)["collected"]), ("planning", "idle"))
        self.assertEqual({k: result["next"][k] for k in ("title", "label", "href", "stage")},
                         {"title": "Upload the scans once it is given", "label": "Upload scans", "href": "#inbox?upload=1", "stage": "collected"})
        listed = next(item["summary"] for item in self.client.get("/api/exams").json["exams"] if item["id"] == self.event["id"])
        self.assertEqual((listed["phase"], listed["next"]["title"]), ("planning", "Upload the scans once it is given"))
        # The same stages with a main date recorded: ready for the day, no step.
        stages = [*[_stage(key, "done", "") for key in ("paper", "seating", "notices", "print")],
                  _stage("exam_day", "started", "1 DR booking", counts={"dr_bookings": 1}), _stage("collected", "idle", ""),
                  _stage("grading", "idle", ""), _stage("recorded", "idle", "")]
        self.assertEqual(_next(stages, False, {"id": "e"}, dated=False)["title"], "Upload the scans once it is given")
        self.assertIsNone(_next(stages, False, {"id": "e"}, dated=True))

    def test_the_hub_reads_grading_again_after_the_instructors_own_action(self):
        """Review fix (2026-10-02): the hub reused its grading read for up to two minutes whatever happened to the plan, so a
        certification still read 'Certify the grades'. The read is reused only while the plan's version, newest audit event and
        newest scan link are unchanged."""
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        reviewed = {"plan_id": plan["id"], "entry": {"ready": True, "retained_grades": True}, "progress": {"review": {"completed": 4, "total": 4}, "grade": {"completed": 4}},
                    "next_tab": "export", "failed": 0, "decision_responses": 0, "version_decisions": 0, "certification": {"state": "none"}}
        current = [reviewed]
        with patch("exam_workflow.overview.grading_summary", side_effect=lambda *args: current[0]) as read:
            self.assertEqual(self.overview("2026-10-01")["next"]["title"], "Certify grades")
            self.overview("2026-10-01"); self.client.get("/api/exams")
            self.assertEqual(read.call_count, 1)
            current[0] = {**reviewed, "certification": {"state": "current", "left_out": 0}}
            with connect(self.path) as db:
                self.app.extensions["frq_grading"].event(db, plan["id"], "grades_certified", {"certification_id": "fixture"})
            result = self.overview("2026-10-01")
            self.assertEqual(read.call_count, 2)
        grading = next(s for s in result["stages"] if s["key"] == "grading")
        self.assertEqual((grading["state"], grading["counts"]["certification"]), ("done", "current"))
        self.assertNotEqual((result["next"] or {}).get("title"), "Certify grades")

    def test_a_certified_assessments_column_opens_without_the_pull_preview(self):
        with connect(self.path) as db:
            db.execute("INSERT INTO gradebook_setups(id,term_id,revision,setup_json,sha256,request_id,request_sha256,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), self.term["id"], 1, json.dumps({"items": [{"id": "exam-1", "assignment_id": self.assignment["id"]}]}),
                        "e" * 64, "setup-1", "f" * 64, "test", timestamp()))
        plan = self.app.extensions["frq_grading"].create_assignment({"event_id": self.event["id"]})
        reviewed = {"plan_id": plan["id"], "entry": {"ready": True, "retained_grades": True}, "progress": {"review": {"completed": 4, "total": 4}, "grade": {"completed": 4}},
                    "next_tab": "export", "failed": 0, "decision_responses": 0, "version_decisions": 0}
        base = f"#gradebook/{self.term['id']}?assignment={self.assignment['id']}"
        for certification, href in (({"state": "none"}, base + "&pull=1"), ({"state": "current", "left_out": 0}, base)):
            with patch("exam_workflow.overview.grading_summary", return_value={**reviewed, "certification": certification}), \
                    patch("exam_workflow.overview.GRADING_EVERY", 0):
                result = self.overview(today="2026-10-01")
            self.assertEqual(next(s for s in result["stages"] if s["key"] == "recorded")["href"], href, certification)


if __name__ == "__main__":
    unittest.main()
