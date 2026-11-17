"""The Phase 0 measurement loop: export instructor-graded work, run the same responses through an endpoint, compare with intervals."""
import json
import tempfile
import unittest
from pathlib import Path

from grading import evaluate
from grading.providers.local_vlm import VERSIONS
from manage_grading import benchmark
import test_grading_acceptance as acceptance
import test_grading_cloud as cloud_fixture
from fake_openai import grade_answer, payload_of, prompt_name
from test_grading_pipeline import CORRECT


class MeasurementLoop(acceptance.AcceptanceCase):
    def test_the_same_responses_run_through_another_endpoint_and_are_compared_on_the_instructors_labels(self):
        self.configure_acceptance(blind_share=0.0)
        response, _ = self.grade(CORRECT)
        self.review(response["id"], ["R1"], comment="Checked against the work.")
        with tempfile.TemporaryDirectory() as root:
            dataset = Path(root) / "dataset.zip"
            stream, _, _ = self.frq.export(self.plan["id"], "dataset")
            dataset.write_bytes(stream.read())
            stream.close()
            record = evaluate.load(dataset)["records"][0]
            # What a correction needs to become evaluation or training data later, with the AI's own output kept beside the instructor's.
            self.assertEqual((record["would_accept"], record["exam_version"], len(record["student"])), (True, "", 16))
            self.assertEqual(record["provenance"]["prompts"]["grade:primary"], f"grade-v{VERSIONS['grade']}.txt")
            self.assertEqual(record["provenance"]["feedback"]["override_reason"], "Checked against the work.")
            self.assertEqual(record["provenance"]["models"]["second"]["lineage"], "beta")
            self.assertTrue(record["provenance"]["ocr"] and record["provenance"]["risk"]["components"] and record["proposal"]["applications"] and record["final"]["applications"])
            # One variable changed: another endpoint grades the SAME responses. The first agrees with the instructor, the challenger does not.
            self.host.requests.clear()
            self.host.script = lambda body, host: grade_answer(payload_of(body), {"R1"})
            agrees = evaluate.run_endpoint(dataset, cloud_fixture.endpoint(self.host, "alpha-vision", price_in=1.0, price_out=1.0))
            self.host.script = lambda body, host: grade_answer(payload_of(body), {"R2"})
            # The same transcript source as the baseline run: a grader given the instructor's reading is never compared with one given the machine's.
            differs = evaluate.run_endpoint(dataset, cloud_fixture.endpoint(self.host, "gamma-vision"))
            self.assertEqual([prompt_name(r["body"]) for r in self.host.requests], ["grade", "grade"])
            self.assertTrue(all(any(part["type"] == "image_url" for part in r["body"]["messages"][1]["content"]) for r in self.host.requests))  # The image stays the source of truth.
            first = next(iter(agrees["predictions"].values()))
            self.assertEqual((first["score"]["score"], first["state"], first["cost"] > 0), (10, "proposed", True))
            files = []
            for name, value in (("a.json", agrees), ("b.json", differs)):
                files.append(Path(root) / name)
                files[-1].write_text(json.dumps(value))
            report = benchmark(dataset, files, baseline="alpha-vision")
            self.assertEqual(report["format"], "faculty-frq-benchmark-v3")
            challenger = report["against_baseline"]["gamma-vision (primary, proposal transcript)"]
            self.assertEqual(challenger["baseline"], "alpha-vision (primary, proposal transcript)")
            self.assertEqual((challenger["responses"], challenger["baseline_agreement"], challenger["challenger_agreement"]), (1, 1.0, 0.0))
            retained = next(v for k, v in report["profiles"].items() if k.startswith("cloud_standard / alpha-vision") and k.endswith("grader"))
            self.assertEqual((retained["grading"]["exact_score_agreement"], retained["grading"]["accepted"], retained["grading"]["accepted_wrong"], retained["errors"]["errors"]), (1.0, 1, 0, 0))
            self.assertEqual(report["profiles"]["gamma-vision (primary, proposal transcript)"]["errors"]["wrong_rubric_line"], 1)
            self.assertEqual(sum(report["student_splits"].values()), 1)
            # Corrections as supervised examples, one student-level split at a time. Nothing is trained here.
            counts = {split: evaluate.finetune_export(dataset, Path(root) / f"{split}.jsonl", "grader", split)["examples"] for split in ("train", "calibration", "test")}
            self.assertEqual(sum(counts.values()), 1)
            written = next(Path(root) / f"{split}.jsonl" for split, n in counts.items() if n)
            example = json.loads(written.read_text().splitlines()[0])
            self.assertEqual([a for a in example["output"]["applications"] if a["applied"]], [{"rubric_item_id": "R1", "applied": True}])
            with self.assertRaises(FileExistsError):
                evaluate.finetune_export(dataset, written, "grader", "train")


if __name__ == "__main__":
    unittest.main()
