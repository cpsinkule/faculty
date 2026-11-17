"""Final Quality Audit (grading/quality_audit.py): a read of a plan's final grades by the rubric model, on the fake host."""
import json
import unittest
import uuid

from werkzeug.exceptions import Conflict

from grading import quality_audit as qa
from rosters import connect
from fake_openai import default_answer, payload_of, prompt_name, images_of
from test_grading_cloud import ApprovedCase, CloudCase, endpoint


def row(ref, answers, lines, score, section="s1", decided="automatic", rid=None):
    return {"ref": ref, "response_id": rid or ref, "section_id": section, "enrollment_id": ref, "final_answers": answers, "lines": lines, "score": score,
            "decided_by": decided}


class Checks(unittest.TestCase):
    def test_the_same_final_answer_with_different_lines_is_listed(self):
        q = {"question_id": "q", "label": "Q1", "printed_number": "1", "exam_version": "", "points": 4,
             "responses": [row("R1", ["x = 7"], ["R1"], 4), row("R2", ["x=7"], ["R2", "R3"], 3), row("R3", ["x = 9"], ["R4"], 0)]}
        found = qa.audit_checks([q])
        self.assertEqual([f["kind"] for f in found], ["same_answer"])
        self.assertEqual({r["response_id"] for r in found[0]["responses"]}, {"R1", "R2"})

    def test_a_marked_choice_read_as_a_bare_circle_groups_nothing(self):
        """2026-09-29: papers that circled DIFFERENT choices, each read as "○" (or the letter O), are not the same final answer."""
        q = {"question_id": "q", "label": "Q1", "printed_number": "1", "exam_version": "", "points": 2,
             "responses": [row("R1", ["○"], ["R1"], 2), row("R2", ["○"], ["R2"], 0), row("R3", ["O"], ["R2"], 0), row("R4", [" / "], ["R1"], 2),
                           row("R5", ["(c)"], ["R1"], 2), row("R6", ["(c)"], ["R2"], 0)]}
        found = qa.audit_checks([q])
        self.assertEqual([{r["response_id"] for r in f["responses"]} for f in found], [{"R5", "R6"}])  # a real choice still counts
        self.assertTrue(qa.telling("0") and qa.telling("x = 0") and qa.telling("(a)"))
        self.assertFalse(qa.telling("○") or qa.telling("o") or qa.telling("( ○ )") or qa.telling(""))

    def test_versions_and_sections_scoring_far_apart_are_listed(self):
        a = [row(f"A{i}", [], ["R1"], 4, section="s1" if i < 5 else "s2") for i in range(10)]
        a[5:] = [row(f"A{i}", [], ["R1"], 1, section="s2") for i in range(5, 10)]
        b = [row(f"B{i}", [], ["R1"], 1) for i in range(6)]
        found = qa.audit_checks([{"question_id": "qa", "family": "shared", "label": "Q1 · Version A", "printed_number": "1", "exam_version": "A", "points": 4, "responses": a},
                                 {"question_id": "qb", "family": "shared", "label": "Q5 · Version B", "printed_number": "5", "exam_version": "B", "points": 4, "responses": b}])
        kinds = sorted(f["kind"] for f in found)
        self.assertIn("version_gap", kinds)
        self.assertEqual(kinds.count("section_gap"), 2)

    def test_findings_keep_only_the_responses_they_were_given_and_never_an_anchor_alone(self):
        output = {"summary": "s", "findings": [
            {"kind": "inconsistent", "severity": "high", "refs": ["R1", "R2", "R99"], "lines": ["L1", "Lx"], "explanation": "e",
             "suggestions": [{"ref": "R2", "add": ["L1"], "remove": ["Lx"], "note": "n"}, {"ref": "R99", "add": ["L1"], "remove": [], "note": ""}]},
            {"kind": "outlier", "severity": "low", "refs": ["A1"], "lines": [], "explanation": "anchor only", "suggestions": []},
            {"kind": "made_up", "severity": "high", "refs": ["R1"], "lines": [], "explanation": "", "suggestions": []}]}
        _, kept = qa.findings_of(output, {"R1", "R2"}, {"A1"}, {"L1", "L2"})
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0]["refs"], kept[0]["lines"]), (["R1", "R2"], ["L1"]))
        self.assertEqual(kept[0]["suggestions"], [{"ref": "R2", "add": ["L1"], "remove": [], "note": "n"}])
        with self.assertRaises(ValueError):
            qa.findings_of({"summary": "no list"}, {"R1"}, set(), set())


class AuditRuns(CloudCase):
    approved = ApprovedCase.approved  # Approved grades through the ordinary path.

    def setUp(self):
        super().setUp()
        self.approved(2)
        self.configure([endpoint(self.host, "alpha-vision")], rubric_reviewer="alpha-vision")

    def audit(self):
        return self.frq.queue_quality_audit(self.plan["id"], {"request_id": str(uuid.uuid4())})

    def finish(self):
        for _ in range(10):
            self.frq.process_cloud()
            with connect(self.path) as db:
                db.execute("UPDATE frq_quality_audits SET not_before='' WHERE status='pending'")
        return self.frq.quality_audit(self.plan["id"])["audit"]

    def test_an_audit_reads_the_final_grades_and_changes_none_of_them(self):
        with connect(self.path) as db:
            before = db.execute("SELECT id,current_review_id,current_result_id FROM frq_responses ORDER BY id").fetchall()
        queued = self.audit()
        self.assertEqual((queued["status"], queued["questions"], queued["responses"]), ("pending", 1, 2))
        done = self.finish()
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["result"]["totals"]["findings"], 0)
        calls = self.calls(f"quality_audit_id='{queued['id']}'")
        self.assertEqual([c["step"] for c in calls], [f"audit:{self.qid}:0"])
        audit_request = next(r for r in self.host.requests if prompt_name(r["body"]) == "quality_audit")
        sent = payload_of(audit_request["body"])
        self.assertEqual(len(sent["responses"]), 2)
        self.assertNotIn("response_id", json.dumps(sent))  # The model reads refs, never identifiers.
        self.assertEqual(images_of(audit_request["body"]), [])  # The text pass sends no scans.
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT id,current_review_id,current_result_id FROM frq_responses ORDER BY id").fetchall(), before)

    def test_a_finding_is_checked_against_the_scans_of_the_responses_it_names(self):
        def script(body, host):
            if prompt_name(body) == "quality_audit":
                refs = [r["ref"] for r in payload_of(body)["responses"]]
                return {"summary": "One pair differs.", "findings": [{"kind": "inconsistent", "severity": "high", "refs": refs[:2], "lines": ["R1"],
                        "explanation": "Same work, different lines.", "suggestions": [{"ref": refs[1], "add": ["R1"], "remove": [], "note": "match R1"}]}]}
            if prompt_name(body) == "quality_audit_confirm":
                host.confirm = (payload_of(body), len(images_of(body)))
                return {"verdict": "partly", "explanation": "Seen.", "suggestions": [{"ref": payload_of(body)["responses"][0]["ref"], "add": ["R1"], "remove": [], "note": ""}]}
            return default_answer(body)
        self.host.script = script
        queued = self.audit()
        done = self.finish()
        finding = done["result"]["questions"][0]["findings"][0]
        self.assertEqual((finding["kind"], finding["confirm"]["verdict"]), ("inconsistent", "partly"))
        payload, images = self.host.confirm
        self.assertEqual((len(payload["responses"]), images), (2, 2))  # One crop per named response, in order.
        self.assertTrue(all(r["response_id"] and ":" in r["row"] for r in finding["refs"]))
        for suggestion in (finding["suggestions"][0], finding["confirm"]["suggestions"][0]):  # The page can say whose each suggestion is.
            self.assertTrue(suggestion["response_id"] and ":" in suggestion["row"])
        self.assertEqual(done["result"]["totals"]["confirmed"], 1)
        self.assertEqual(finding["changed"], [])
        self.assertEqual(len(self.calls(f"quality_audit_id='{queued['id']}'")), 2)

    def test_one_audit_at_a_time_a_repeated_request_returns_it_and_a_stop_sends_nothing_further(self):
        first = self.frq.queue_quality_audit(self.plan["id"], {"request_id": "audit-1"})
        self.assertEqual(self.frq.queue_quality_audit(self.plan["id"], {"request_id": "audit-1"})["id"], first["id"])
        with self.assertRaises(Conflict):
            self.audit()
        stopped = self.frq.cancel_quality_audit(self.plan["id"], first["id"])["audit"]
        self.assertEqual((stopped["status"], stopped["cancelled"]), ("failed", True))
        self.frq.process_cloud()
        self.assertEqual(self.calls(f"quality_audit_id='{first['id']}'"), [])


class NothingToAudit(CloudCase):
    def test_without_final_grades_there_is_nothing_to_audit(self):
        self.configure([endpoint(self.host, "alpha-vision")], rubric_reviewer="alpha-vision")
        with self.assertRaises(ValueError):
            self.frq.queue_quality_audit(self.plan["id"], {"request_id": "x"})
