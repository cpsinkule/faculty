"""Version-pinned template maps and reviewed multi-page grading regions."""
import copy
import sqlite3
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
import uuid

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from assignment_templates import AssignmentTemplateService
from exam_workflow.grading import GradingService
from rosters import connect
from teach_inbox.service import encoded, sha
import test_exam_grading as fixtures


FULL = {"x": 0, "y": 0, "width": 1, "height": 1}


class ExamTemplateTests(unittest.TestCase):
    document_version = fixtures.ExamGradingTests.document_version
    ready_batch = fixtures.ExamGradingTests.ready_batch
    reviewed_source = fixtures.ExamGradingTests.reviewed_source
    participant = fixtures.ExamGradingTests.participant
    data = fixtures.ExamGradingTests.data
    mark = fixtures.ExamGradingTests.mark
    complete = fixtures.ExamGradingTests.complete

    def setUp(self):
        fixtures.ExamGradingTests.setUp(self)
        self.templates = self.app.extensions.get("assignment_templates") or AssignmentTemplateService(self.documents)

    def template(self, *, assignment_id=None, pages=2, questions=None, label=None):
        # Exercise real immutable publication and mapping services while keeping
        # native parsing separate: these bytes claim no physical qualification.
        converter = ModuleType("document_identity.template_conversion")
        converter.convert_template = Mock(return_value={
            "pdf": b"%PDF-synthetic-reference", "sources": {"reference.pdf": b"%PDF-synthetic-source"},
            "conversion": {"kind": "synthetic test fixture", "network": False},
            "pages": [{"number": number, "width_pt": 612, "height_pt": 792,
                       "width_px": 850, "height_px": 1100, "png": f"synthetic-page-{number}".encode(),
                       "text": f"Question text on page {number}"} for number in range(1, pages + 1)],
        })
        with patch.dict(sys.modules, {"document_identity.template_conversion": converter}):
            uploaded = self.templates.upload(assignment_id or self.assignment["id"], label or str(uuid.uuid4()),
                                             str(uuid.uuid4()), {"reference.pdf": b"%PDF-synthetic-source"})
        return self.templates.save_mapping(uploaded["id"], {"version": 0, "questions": questions or self.multi_questions()})

    def apply(self, template, *, version=1, event_id=None, document_id=None):
        return self.grading.apply_template(event_id or self.event["id"], {"version": version,
            "assignment_version_id": document_id or self.version_id, "template_revision_id": template["revision_id"]})

    def multi_questions(self):
        questions = copy.deepcopy(self.questions)
        questions[0]["regions"] = [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .4}},
                                    {"page": 2},
                                    {"page": 1, "region": {"x": 0, "y": .6, "width": 1, "height": .4}}]
        return questions

    def test_ordered_regions_span_pages_and_reuse_exact_verified_rasters(self):
        rubric = self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                                                           "questions": self.multi_questions()})
        question = rubric["questions"][0]
        self.assertEqual(question["page"], question["regions"][0]["page"])
        self.assertEqual(question["region"], question["regions"][0]["region"])
        self.assertEqual(question["regions"][1]["region"], FULL)
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1")
        regions = work["question_regions"]
        self.assertEqual([item["page"] for item in regions], [1, 2, 1])
        self.assertTrue(all(not item["missing"] for item in regions))
        self.assertFalse(work["page_missing"])
        self.assertEqual(regions[0]["scan_page_id"], regions[2]["scan_page_id"])
        self.assertEqual(regions[0]["raster_url"], regions[2]["raster_url"])
        self.assertNotEqual(regions[0]["raster_url"], regions[1]["raster_url"])
        for region in regions:
            self.assertRegex(region["raster_sha256"], r"^[0-9a-f]{64}$")
        result = self.client.get(f"/api/exams/{self.event['id']}/grading/work", query_string={
            "section_id": self.section["id"], "enrollment_id": self.enrollment, "question_id": "q1"})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["question_regions"], regions)

    def test_missing_later_page_requires_acknowledgment_even_when_first_page_exists(self):
        self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                                                  "questions": self.multi_questions()})
        other = self.ready_batch()
        _, partial = self.reviewed_source(pages=(1,), changed="template-partial", batch=other)
        self.scans.choose_submission(partial["id"], "Use the explicitly reviewed partial paper")
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1")
        self.assertEqual([item["missing"] for item in work["question_regions"]], [False, True, False])
        self.assertIsNone(work["question_regions"][1]["raster_url"])
        self.assertTrue(work["page_missing"])
        for acknowledgment in (None, False, 1, "true"):
            with self.subTest(acknowledgment=acknowledgment), self.assertRaises(Conflict):
                self.mark(acknowledge_missing=acknowledgment)
        self.mark(acknowledge_missing=True)
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM exam_mark_revisions WHERE question_id='q1'").fetchone()
        self.assertEqual(row["missing_acknowledged"], 1)
        self.assertEqual(row["assembly_revision_id"], partial["id"])

    def test_legacy_single_region_rubric_is_read_without_rewriting_immutable_questions(self):
        current = self.grading.details(self.event["id"])["rubric"]
        legacy = [{key: value for key, value in question.items() if key != "regions"} for question in current["questions"]]
        rubric_id = str(uuid.uuid4())
        digest = sha(encoded({"assignment_version_id": self.version_id, "questions": legacy}).encode())
        with connect(self.path) as db:
            db.execute("INSERT INTO exam_rubric_versions VALUES(?,?,?,?,?,?,?)", (rubric_id, self.assignment["id"],
                       self.version_id, 2, encoded(legacy), digest, timestamp()))
            db.execute("UPDATE exam_grading_rubrics SET rubric_id=? WHERE event_id=?", (rubric_id, self.event["id"]))
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1")
        self.assertEqual(len(work["question_regions"]), 1)
        self.assertEqual(work["question_regions"][0]["region"], legacy[0]["region"])
        self.assertIsNone(work["template_revision_id"])
        unchanged = self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                                                             "questions": legacy})
        self.assertEqual(unchanged["id"], rubric_id)
        with connect(self.path) as db:
            retained = db.execute("SELECT questions_json FROM exam_rubric_versions WHERE id=?", (rubric_id,)).fetchone()[0]
        self.assertEqual(retained, encoded(legacy))

    def test_regions_reject_invalid_pages_coordinates_and_excessive_cardinality(self):
        invalid = ([], None, {}, [{"page": 0}], [{"page": True}], [{"page": 3}],
                   [{"page": 1, "region": {"x": 0, "y": 0, "width": 0, "height": 1}}],
                   [{"page": 1, "region": {"x": 0, "y": .8, "width": 1, "height": .3}}],
                   [{"page": 1, "region": {"x": float("nan"), "y": 0, "width": 1, "height": 1}}],
                   [{"page": 1, "region": {"x": True, "y": 0, "width": 1, "height": 1}}],
                   [{"page": 1, "region": FULL, "scan_page_id": "cannot-assign-identity"}], [{"page": 1}] * 101)
        for regions in invalid:
            questions = copy.deepcopy(self.questions)
            questions[0]["regions"] = regions
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                                                          "questions": questions})
        questions = [{"id": f"q{n}", "label": f"Question {n}", "max_points": 1, "regions": [{"page": 1}] * 100} for n in range(11)]
        with self.assertRaisesRegex(ValueError, "1000"):
            GradingService._questions({"questions": questions}, 2)
        self.assertEqual(self.grading.details(self.event["id"])["version"], 1)

    def test_region_change_invalidates_old_open_mark_and_release(self):
        self.complete()
        release = self.grading.release(self.event["id"], self.data(request_id="before-multi-region"))
        before = self.data(question_id="q1", score=6, criterion_ids=["method"])
        self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                                                  "questions": self.multi_questions()})
        self.assertEqual(self.participant()["status"], "stale")
        with self.assertRaises(Conflict):
            self.grading.save_mark(self.event["id"], before)
        with self.assertRaises(Conflict):
            self.grading.release_snapshot(release["id"])

    def test_template_application_retains_exact_provenance_and_api_work_context(self):
        template = self.template()
        response = self.client.post(f"/api/exams/{self.event['id']}/grading/template", headers=fixtures.HEADERS,
            json={"version": 1, "assignment_version_id": self.version_id, "template_revision_id": template["revision_id"]})
        self.assertEqual(response.status_code, 200, response.json)
        rubric = response.json
        self.assertEqual(rubric["version"], 2)
        self.assertEqual(rubric["template_revision_id"], template["revision_id"])
        self.assertEqual(rubric["template"]["id"], template["id"])
        self.assertEqual(rubric["template"]["label"], template["label"])
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1")
        self.assertEqual(work["template_revision_id"], template["revision_id"])
        self.assertEqual(work["participant"]["template"], rubric["template"])
        self.assertEqual([region["page"] for region in work["question_regions"]], [1, 2, 1])
        repeated = self.apply(template, version=2)
        self.assertEqual(repeated["id"], rubric["id"])
        self.assertEqual(repeated["version"], 2)
        with connect(self.path) as db:
            retained = db.execute("SELECT * FROM exam_rubric_template_sources WHERE rubric_id=?", (rubric["id"],)).fetchone()
            self.assertEqual(retained["template_revision_id"], template["revision_id"])
            for query in ("UPDATE exam_rubric_template_sources SET template_sha256='bad'", "DELETE FROM exam_rubric_template_sources"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(query)
        restarted = GradingService(self.exams)
        self.assertEqual(restarted.details(self.event["id"])["rubric"], rubric)

    def test_identical_questions_from_different_templates_keep_distinct_provenance(self):
        first, second = self.template(label="Original reference"), self.template(label="Second reference")
        a = self.apply(first)
        b = self.apply(second, version=2)
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.assertEqual(a["questions"], b["questions"])
        self.assertEqual(b["template_revision_id"], second["revision_id"])
        with self.assertRaises(Conflict):
            self.apply(first, version=2)

    def test_manual_save_preserves_template_and_grades_until_questions_change(self):
        template = self.template()
        rubric = self.apply(template)
        self.complete()
        release = self.grading.release(self.event["id"], self.data(request_id="unchanged-template-release"))
        participant = self.participant()
        # Submit the normalized question fields without claiming template
        # provenance, exactly as the ordinary rubric editor does.
        unchanged = self.grading.configure(self.event["id"], {"version": 2, "assignment_version_id": self.version_id,
                                                              "questions": copy.deepcopy(rubric["questions"])})
        self.assertEqual(unchanged, rubric)
        self.assertEqual(unchanged["template_revision_id"], template["revision_id"])
        self.assertEqual(self.participant(), participant)
        self.assertEqual(self.grading.release_snapshot(release["id"]), release)

        changed = copy.deepcopy(rubric["questions"])
        changed[0]["regions"][0]["region"]["height"] = .3
        manual = self.grading.configure(self.event["id"], {"version": 2, "assignment_version_id": self.version_id,
                                                           "questions": changed})
        self.assertIsNone(manual["template_revision_id"])
        self.assertIsNone(manual["template"])
        self.assertNotEqual(manual["id"], rubric["id"])
        self.assertEqual(manual["version"], 3)
        self.assertEqual(self.participant()["status"], "stale")
        with self.assertRaises(Conflict):
            self.grading.release_snapshot(release["id"])
        with connect(self.path) as db:
            source = db.execute("SELECT template_revision_id FROM exam_rubric_template_sources WHERE rubric_id=?",
                                (rubric["id"],)).fetchone()
        self.assertEqual(source["template_revision_id"], template["revision_id"])

    def test_template_revisions_require_explicit_apply_and_invalidate_current_release(self):
        template = self.template()
        first = self.apply(template)
        self.complete()
        release = self.grading.release(self.event["id"], self.data(request_id="template-original-release"))
        old_form = self.data(question_id="q1", score=6, criterion_ids=["method"])
        questions = self.multi_questions()
        questions[0]["regions"][0]["region"]["height"] = .3
        revised = self.templates.save_mapping(template["id"], {"version": 1, "questions": questions})
        self.assertEqual(self.grading.details(self.event["id"])["rubric"]["id"], first["id"])
        self.assertEqual(self.grading.release_snapshot(release["id"])["total"], 10)
        self.apply(revised, version=2)
        self.assertEqual(self.participant()["status"], "stale")
        with self.assertRaises(Conflict):
            self.grading.save_mark(self.event["id"], old_form)
        with self.assertRaises(Conflict):
            self.grading.release_snapshot(release["id"])

    def test_apply_rejects_cross_assignment_page_count_and_client_provenance_spoof(self):
        other = self.documents.create_assignment(self.assignment["definition_id"], "different-assignment", "Different exam")
        wrong_owner = self.template(assignment_id=other["id"])
        short = self.template(pages=1, questions=[{"id": "q1", "label": "Question 1", "page": 1, "max_points": 10}])
        for template in (wrong_owner, short, {"revision_id": str(uuid.uuid4())}):
            with self.subTest(template=template["revision_id"]), self.assertRaises(ValueError):
                self.apply(template)
        template = self.template()
        for extra in ({"template_revision_id": template["revision_id"]}, {"template": {"id": template["id"]}},
                      {"template_id": template["id"]}):
            with self.assertRaises(ValueError):
                self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                                                          "questions": self.questions, **extra})
        with self.assertRaises(ValueError):
            self.grading.apply_template(self.event["id"], {"version": 1, "assignment_version_id": self.version_id,
                "template_revision_id": template["revision_id"], "questions": self.questions})
        self.assertEqual(self.grading.details(self.event["id"])["version"], 1)

    def test_template_pin_is_independent_for_other_term_and_document_version(self):
        template = self.template()
        first = self.apply(template)
        second_version = self.document_version("Makeup reference")
        variant = self.apply(template, version=0, document_id=second_version)
        self.assertNotEqual(variant["id"], first["id"])
        self.assertEqual(variant["assignment_version_id"], second_version)
        response = self.client.post("/api/courses", headers=fixtures.HEADERS, json={"code": "MATH 140", "name": "Calculus",
            "term": "Spring", "year": 2027, "sections": [501]})
        self.assertEqual(response.status_code, 201, response.json)
        with connect(self.path) as db:
            section = db.execute("SELECT s.id,s.term_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE t.year=2027").fetchone()
        later = self.exams.create({"term_id": section["term_id"], "assignment_id": self.assignment["id"], "section_ids": [section["id"]]})
        shared = self.apply(template, version=0, event_id=later["id"])
        self.assertEqual(shared["id"], first["id"])
        questions = self.multi_questions()
        questions[0]["label"] = "Revised problem wording"
        revised = self.templates.save_mapping(template["id"], {"version": 1, "questions": questions})
        self.apply(revised, version=1, event_id=later["id"])
        original = self.grading.details(self.event["id"], assignment_version_id=self.version_id)
        makeup = self.grading.details(self.event["id"], assignment_version_id=second_version)
        self.assertEqual(original["rubric"]["id"], first["id"])
        self.assertEqual(makeup["rubric"]["id"], variant["id"])

    def test_template_checksum_is_revalidated_before_applying_a_retained_revision(self):
        template = self.template()
        corrupted_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_template_revisions VALUES(?,?,?,?,?,?)",
                       (corrupted_id, template["id"], 2, encoded(template["questions"]), "0" * 64, timestamp()))
        with self.assertRaisesRegex(Conflict, "checksum"):
            self.apply({"revision_id": corrupted_id})
        self.assertEqual(self.grading.details(self.event["id"])["version"], 1)


if __name__ == "__main__":
    unittest.main()
