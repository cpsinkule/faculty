"""Every grade saved for training (requirement, 2026-09-26; `grading/training.py`, migration 028)."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from werkzeug.exceptions import Conflict

from grading import config, evaluate, models, training
from rosters import connect
import test_exam_grading as exam_fixture
import test_frq_grading as frq_fixture
from test_grading_cloud import endpoint
from test_grading_pipeline import CORRECT, Pipeline


def saved(path):
    with connect(path) as db:
        return [{**dict(row), "example": json.loads(row["example_json"])} for row in db.execute("SELECT * FROM frq_training_examples ORDER BY rowid")]


def drain(service):
    turns = 0
    while service.capture_training_one():
        turns += 1
    return turns


def download(service, aid=None):
    stream, media, _ = service.training_export(aid)
    with stream:
        content = stream.read()
    return media, content


class InstructorDecisions(unittest.TestCase):
    managed = False
    setUp = frq_fixture.FrqWorkflowTests.setUp
    document_version = frq_fixture.FrqWorkflowTests.document_version
    ready_batch = frq_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = frq_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = frq_fixture.FrqWorkflowTests.configure_rubric
    queue = frq_fixture.FrqWorkflowTests.queue
    finish = frq_fixture.FrqWorkflowTests.finish
    approve = frq_fixture.FrqWorkflowTests.approve

    def review(self, response, status="approved", selected=("R1",), **extra):
        current = self.frq.response(response["id"])
        return self.frq.save_review(response["id"], {"version": current["version"], "result_id": current["current_result_id"], "status": status,
            "confirm_review": True, "selected_items": list(selected), **extra})

    def test_an_approval_is_saved_by_the_worker_with_its_crops_readings_rubric_and_scores(self):
        response = self.finish(self.queue()[0])
        self.review(response, "draft")
        self.assertEqual(drain(self.frq), 0, "A draft decides nothing and is not saved.")
        approved = self.approve(self.frq.response(response["id"]))
        self.assertTrue(self.frq.process_one())  # The worker saves it when its lane has nothing else to do.
        self.assertFalse(self.frq.process_one())
        [row] = saved(self.path)
        example = row["example"]
        self.assertEqual((row["label"], row["status"], row["decision_kind"], row["decision_id"]), ("instructor", "approved", "review", approved["review"]["id"]))
        self.assertEqual((row["rubric_id"], row["question_revision_id"]), (response["task"]["rubric_id"], response["task"]["question_revision_id"]))
        self.assertEqual(example["rule"], training.RULE)
        # The crops are references to the retained, checksum-verified images of the scan, never copies.
        self.assertEqual(example["crops"][0]["images"]["original"], {k: response["crops"][0]["images"]["original"][k] for k in ("artifact_id", "sha256")})
        self.assertTrue(example["crops"][0]["metadata"]["registered"])
        self.assertEqual([r["provider"] for r in example["readings"]], ["glm"])
        self.assertIn("3x - 8 = 10", json.dumps(example["proposal"]["transcript"]))
        self.assertEqual((example["proposal"]["score"]["score"], example["final"]["score"]["score"]), (9, 9))
        self.assertEqual(example["proposal"]["result_id"], response["current_result_id"])
        self.assertEqual(example["feedback"]["lines_changed"], {"added": [], "removed": []})
        self.assertEqual(example["question"]["rubric_finalization"], response["result"]["signals"]["scope"]["rubric_quality"])
        self.assertEqual(drain(self.frq), 0, "Each decision is saved once.")
        status = self.app.test_client().get(f"/api/grading/assignments/{self.plan['id']}/training", headers=exam_fixture.HEADERS).json
        self.assertEqual(status["assignment"], {"examples": 1, "instructor": 1, "automatic": 0, "responses": 1, "assignments": 1, "waiting": 0})

    def test_a_later_decision_supersedes_and_a_stale_grade_stays_an_example_of_its_rubric(self):
        response = self.finish(self.queue()[0])
        self.approve(response)
        self.review(response, selected=())  # The instructor changes their mind: no deduction.
        self.configure_rubric(.5)  # A changed rubric makes both grades stale for the grade exports ...
        with self.assertRaises(Conflict):
            self.frq.export(self.plan["id"], "csv")
        drain(self.frq)
        first, second = saved(self.path)  # ... but each stays an example of the rubric it was graded under.
        media, content = download(self.frq, self.plan["id"])
        self.assertEqual(media, "application/zip")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "training.zip"
            path.write_bytes(content)
            with zipfile.ZipFile(path) as archive:
                images = [n for n in archive.namelist() if n.startswith("images/")]
                self.assertEqual(images, [f"images/{first['example']['crops'][0]['images']['original']['sha256']}.png"], "One shared crop, stored once.")
                self.assertEqual(archive.getinfo(images[0]).compress_type, zipfile.ZIP_STORED)
            dataset = evaluate.load(path)
            self.assertEqual(dataset["format"], training.FORMAT)
            records = {r["example_id"]: r for r in dataset["records"]}
            self.assertEqual(records[first["id"]]["superseded_by"], second["id"])
            self.assertEqual((records[first["id"]]["standing"], records[second["id"]]["standing"]), (False, True))
            self.assertEqual(records[second["id"]]["final"]["score"]["score"], 10)
            self.assertEqual(records[second["id"]]["rubric"]["rules"]["items"][0]["points"], -1, "The rubric it was graded under, not today's.")
            self.assertEqual(records[second["id"]]["question"]["max_points"], 10)
            self.assertNotIn("name", json.dumps(dataset["records"][0]["assignment"]))
            self.assertFalse({"section_id", "enrollment_id"} & set(dataset["records"][0]))
            self.assertEqual([r["example_id"] for r in evaluate.labelled(dataset["records"])], [second["id"]])
            report = evaluate.report(path)
            self.assertEqual(report["responses"], 1)
            split = records[second["id"]]["split"]
            lines = Path(root) / "grader.jsonl"
            self.assertEqual(evaluate.finetune_export(path, lines, "grader", split)["examples"], 1)
            line = json.loads(lines.read_text())
            self.assertEqual((line["example_id"], line["label"], line["images"]), (second["id"], "instructor", images))
            self.assertEqual({a["rubric_item_id"]: a["applied"] for a in line["output"]["applications"]}, {"R1": False})
            reader = Path(root) / "reader.jsonl"
            evaluate.finetune_export(path, reader, "reader", split)
            self.assertEqual(json.loads(reader.read_text())["input"]["candidates"][0]["provider"], "glm")

    def test_examples_are_immutable_retained_and_only_ever_of_their_own_decision(self):
        response = self.finish(self.queue()[0])
        self.approve(response)
        drain(self.frq)
        [row] = saved(self.path)
        with connect(self.path) as db:
            for statement in ("UPDATE frq_training_examples SET status='manual'", "DELETE FROM frq_training_examples"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            values = [row[k] for k in ("id", "assignment_id", "response_id", "decision_kind", "decision_id", "result_id", "question_revision_id", "rubric_id",
                                       "source_sha256", "label", "status", "rule", "example_json", "sha256", "decided_at", "created_at")]
            for change in ({0: "other", 10: "manual"}, {0: "other", 3: "automatic", 9: "automatic", 10: "automatic"}, {0: "other", 4: "missing"}, {0: "other", 8: "0" * 64}):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO frq_training_examples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [change.get(i, v) for i, v in enumerate(values)])

    def test_downloads_are_read_only_files(self):
        client = self.app.test_client()
        self.assertEqual(client.get(f"/api/grading/assignments/{self.plan['id']}/training.zip", headers=exam_fixture.HEADERS).status_code, 409)
        self.approve(self.finish(self.queue()[0]))
        drain(self.frq)
        with connect(self.path) as db:
            before = db.execute("SELECT (SELECT COUNT(*) FROM frq_exports),(SELECT COUNT(*) FROM frq_events)").fetchone()
        for url in (f"/api/grading/assignments/{self.plan['id']}/training.zip", "/api/grading/training.zip"):
            with client.get(url, headers=exam_fixture.HEADERS) as reply:
                self.assertEqual((reply.status_code, reply.mimetype), (200, "application/zip"))
                self.assertIn("attachment", reply.headers["Content-Disposition"])
                with zipfile.ZipFile(__import__("io").BytesIO(reply.get_data())) as archive:
                    self.assertEqual(json.loads(archive.read("dataset.json"))["counts"]["examples"], 1)
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute("SELECT (SELECT COUNT(*) FROM frq_exports),(SELECT COUNT(*) FROM frq_events)").fetchone()), tuple(before))
        self.assertEqual(client.get("/api/grading/assignments/missing/training", headers=exam_fixture.HEADERS).status_code, 404)


class AutomaticDecisions(Pipeline):
    current_standard = True
    legacy_workflow = False

    def setUp(self):
        super().setUp()
        saved_settings = self.frq.settings()
        settings = saved_settings["settings"]
        settings["endpoints"].append(endpoint(self.host, "delta-vision"))
        settings["tiers"]["cloud_standard"].update(verifier="gamma-vision", flag_reviewer="delta-vision")
        self.frq.save_settings({"version": saved_settings["version"], "settings": settings})

    def test_an_automatic_grade_is_saved_marked_and_never_evaluation_truth(self):
        response, _ = self.grade(CORRECT)  # The worker's own turns grade, finalize and save it.
        self.assertEqual(response["acceptance"]["basis"], "team_consensus")
        [automatic] = saved(self.path)
        self.assertEqual((automatic["label"], automatic["status"], automatic["decision_id"]), ("automatic", "automatic", response["acceptance"]["id"]))
        self.assertEqual(automatic["example"]["basis"], "team_consensus")
        self.assertEqual(automatic["example"]["final"]["applications"], automatic["example"]["proposal"]["applications"])
        self.assertIn("grade:primary", automatic["example"]["proposal"]["steps"])
        self.approve(self.frq.response(response["id"]))  # The instructor looks at it afterwards.
        drain(self.frq)
        _, instructor = saved(self.path)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "training.zip"
            path.write_bytes(download(self.frq)[1])
            records = {r["example_id"]: r for r in evaluate.load(path)["records"]}
            self.assertEqual(records[automatic["id"]]["superseded_by"], instructor["id"])
            self.assertEqual([r["label"] for r in evaluate.labelled(records.values(), "all")], ["instructor"])
            self.assertEqual(evaluate.report(path)["responses"], 1)

    def test_standing_automatic_grades_are_used_only_when_asked_for(self):
        response, _ = self.grade(CORRECT)
        drain(self.frq)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "training.zip"
            path.write_bytes(download(self.frq, self.plan["id"])[1])
            [record] = evaluate.load(path)["records"]
            self.assertTrue(record["standing"] and record["active"])
            self.assertEqual(evaluate.labelled([record]), [])
            with self.assertRaises(KeyError):  # No instructor decision to measure against.
                evaluate.report(path)["profiles"]["retained_pipeline"]
            for labels, count in (("instructor", 0), ("all", 1)):
                output = Path(root) / f"{labels}.jsonl"
                self.assertEqual(evaluate.finetune_export(path, output, "grader", record["split"], labels)["examples"], count)
            self.assertEqual(json.loads((Path(root) / "all.jsonl").read_text())["label"], "automatic")


class TheSavedTranscriptionsStand(unittest.TestCase):
    """GRADING "THE SAVED TRANSCRIPTIONS STAND"."""

    def test_the_retired_recheck_setting_still_loads_and_is_dropped(self):
        self.assertNotIn("training_transcript_check", config.validate({"training_transcript_check": False}))
        self.assertNotIn("training_transcript_check", config.DEFAULTS)

    def test_a_blank_transcript_is_the_same_after_review(self):
        once = models.plain_transcript("")
        self.assertEqual(models.transcript(once), once, "A second note made every reviewed blank answer look edited.")
        self.assertEqual(once["ambiguities"], [models.NOTHING_TRANSCRIBED])
        self.assertEqual(training.written(once), "")

    def test_words_are_compared_with_what_was_struck_marked(self):
        line = {"id": "line-1", "elements": [{"type": "math", "text": "17", "latex": "17", "state": "crossed_out"}, {"type": "math", "text": "12", "latex": "12"}]}
        self.assertEqual(training.written({"lines": [line]}), "[crossed out: 17] 12")


if __name__ == "__main__":
    unittest.main()
