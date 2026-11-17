"""Crops (2026-09-23): a regrade reuses the crops an earlier attempt cut from the same source, and a cloud reader is sent each
crop as scanned and then its contrast view, never the same image twice."""
import base64
import json
from unittest.mock import patch

from grading import extraction
from rosters import connect
from fake_openai import images_of, prompt_name
from frq_fixtures import page
import test_grading_cloud as cloud_fixture


class Crops(cloud_fixture.CloudCase):
    def setUp(self):
        super().setUp()
        scanned, contrast, other = page(), page(messy=True), page(template=True)
        self.images = {"original": scanned, "cloud": scanned, "contrast": contrast, "grayscale": other, "ink": other}
        self.version = extraction.VERSION
        self.cut = patch("grading.extraction.extract", side_effect=lambda *args, **kwargs: {"images": dict(self.images),
            "metadata": {"version": self.version, "registered": True, "crop_bounds": [0, 0, 100, 100], "privacy_masks": []}}).start()

    def crop_steps(self):
        with connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT output_json FROM frq_steps WHERE stage='crop:0' ORDER BY rowid")]

    def stored(self):
        with connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM inbox_artifacts WHERE path LIKE 'grading/%'").fetchone()[0]

    def test_a_cloud_reader_sees_each_crop_as_scanned_then_its_contrast_view(self):
        self.queue()
        self.run_all()
        [reader] = [r["body"] for r in self.host.requests if prompt_name(r["body"]) == "reconcile"]
        sent = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) for part in images_of(reader)]
        self.assertEqual(sent, [self.images["cloud"], self.images["contrast"]])
        self.assertNotEqual(sent[0], sent[1])

    def test_a_masked_crop_still_sends_only_its_masked_copy(self):
        self.cut.side_effect = lambda *args, **kwargs: {"images": dict(self.images),
            "metadata": {"version": self.version, "registered": True, "crop_bounds": [0, 0, 100, 100], "privacy_masks": [[0, 0, .5, .5]]}}
        self.queue()
        self.run_all()
        [reader] = [r["body"] for r in self.host.requests if prompt_name(r["body"]) == "reconcile"]
        sent = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) for part in images_of(reader)]
        self.assertEqual(sent, [self.images["cloud"], self.images["cloud"]])

    def test_a_regrade_references_the_crops_it_already_has_and_a_new_cut_is_never_mixed_with_an_old_one(self):
        first = self.queue()
        self.run_all()
        self.assertEqual(self.cut.call_count, 1)
        stored = self.stored()
        second = self.queue(selection="all")
        self.run_all()
        self.assertEqual(self.frq.job(second["id"])["tasks"][0]["status"], "complete")
        # Nothing was cut or stored again: the second attempt names the first attempt's images.
        self.assertEqual((self.cut.call_count, self.stored()), (1, stored))
        original, again = self.crop_steps()
        self.assertEqual(again["images"], original["images"])
        self.assertEqual(again["reused_from"], {"task_id": self.frq.job(first["id"])["tasks"][0]["id"], "stage": "crop:0"})
        # A different cut (a new extraction version) is never reused: the next attempt cuts its own.
        self.version = "frq-extraction-test-v2"
        with patch.object(extraction, "VERSION", "frq-extraction-test-v2"):
            third = self.queue(selection="all")
            self.run_all()
        self.assertEqual(self.frq.job(third["id"])["tasks"][0]["status"], "complete")
        self.assertEqual(self.cut.call_count, 2)
        self.assertNotIn("reused_from", self.crop_steps()[-1])
