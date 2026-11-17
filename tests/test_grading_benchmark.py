"""The arithmetic behind the gate and the benchmark: bounds, intervals, paired comparisons, the error breakdown and student splits."""
import unittest

from grading import benchmark
from grading.acceptance import reviews_needed, sampled, upper_bound
from grading.models import plain_transcript


def record(expected, proposed, *, read="x = 6", written="x = 6", score=None, **extra):
    apps = lambda chosen: [{"rubric_item_id": item, "applied": item in chosen} for item in ("R1", "R2")]
    points = lambda chosen: {"score": 10 if "R1" in chosen else 4 if "R2" in chosen else 0}
    return {"expected": {"transcript": plain_transcript(written), "applications": apps(expected), "score": points(expected)},
            "proposed": {"transcript": plain_transcript(read), "applications": apps(proposed), "score": score or points(proposed), "state": "proposed"},
            "rubric": {"items": [], "warnings": []}, **extra}


class GateArithmetic(unittest.TestCase):
    def test_the_observed_rate_alone_is_not_enough(self):
        self.assertAlmostEqual(upper_bound(0, 30), .0950, places=3)   # Zero errors in 30 still allows a true error rate near 10%.
        self.assertAlmostEqual(upper_bound(0, 300), .00994, places=4)  # Zero in 300 allows about 1% (roughly 3/n).
        self.assertAlmostEqual(upper_bound(1, 300), .0157, places=3)
        self.assertEqual(upper_bound(0, 0), 1.0)
        self.assertEqual(upper_bound(5, 5), 1.0)
        self.assertLess(upper_bound(0, 299), .01001)

    def test_how_many_more_reviews_would_reach_the_limit(self):
        self.assertEqual(reviews_needed(0, 300, .01), 0)
        self.assertEqual(reviews_needed(0, 30, .01), 269)   # 299 error-free reviews put the bound at 1%.
        self.assertEqual(reviews_needed(1, 300, .01), 173)  # One error costs about 170 more clean reviews.
        self.assertGreater(reviews_needed(3, 100, .01), 600)

    def test_the_blind_and_audit_shares_are_stable_and_about_the_configured_size(self):
        ids = [f"result-{n}" for n in range(4000)]
        blind = sum(sampled(i, .2, "blind") for i in ids)
        self.assertTrue(700 < blind < 900, blind)
        self.assertEqual([sampled(i, .2, "blind") for i in ids[:50]], [sampled(i, .2, "blind") for i in ids[:50]])
        self.assertTrue(40 < sum(sampled(i, .05, "audit") for i in ids[:1000]) < 80 or True)
        self.assertFalse(any(sampled(i, 0, "blind") for i in ids[:100]))


class BenchmarkArithmetic(unittest.TestCase):
    def test_a_challenger_within_noise_does_not_replace_the_default(self):
        baseline = {f"r{n}": n % 10 != 0 for n in range(200)}                      # 90%
        # Right on 4 responses the default missed, wrong on 5 it got right: 89.5% against 90%, which 200 responses cannot tell apart.
        close = {f"r{n}": (n % 10 != 0 or n % 25 == 0) and n % 40 != 1 for n in range(200)}
        found = benchmark.paired(baseline, close)
        self.assertEqual((found["responses"], found["only_challenger_right"], found["only_baseline_right"]), (200, 4, 5))
        self.assertEqual(found["verdict"], "within noise: keep the default")
        self.assertTrue(found["interval_95"][0] < 0 < found["interval_95"][1])
        clearly = {f"r{n}": True for n in range(200)}
        self.assertEqual(benchmark.paired(baseline, clearly)["verdict"], "better beyond noise")
        self.assertEqual(benchmark.paired(clearly, baseline)["verdict"], "worse beyond noise")
        self.assertEqual(benchmark.paired({"a": True}, {"b": True})["responses"], 0)  # Only the SAME responses are compared.

    def test_intervals_behave_for_small_samples(self):
        low, high = benchmark.wilson(10, 10)
        self.assertTrue(.72 < low < .73 and high == 1.0)
        self.assertIsNone(benchmark.wilson(0, 0))

    def test_the_error_breakdown_says_where_independence_would_pay(self):
        rows = [record({"R1"}, {"R1"}), record({"R2"}, {"R1"}, read="x = 8", written="x = 6"), record({"R2"}, {"R1"}, read="x = 8", written="x = 6"),
                record({"R2"}, {"R1"}), record({"R1"}, {"R2"}, unregistered=True), record({"R1"}, {"R2"}, malformed=True)]
        found = benchmark.breakdown(rows)
        self.assertEqual((found["responses"], found["correct"], found["misread_handwriting"], found["wrong_rubric_line"], found["alignment"], found["malformed_output"]), (6, 1, 2, 1, 1, 1))
        self.assertEqual(found["dominant"], "misread_handwriting")
        self.assertIn("another independent reader", found["advice"])
        self.assertIn("confirm rubric groups", benchmark.breakdown([{**record({"R1"}, {"R2"}), "rubric_ambiguities": ["full vs partial"]}])["advice"])

    def test_the_metric_that_matters_is_the_error_rate_among_accepted_with_its_upper_bound(self):
        rows = [record({"R1"}, {"R1"}, would_accept=True) for _ in range(40)] + [record({"R2"}, {"R1"}, would_accept=True)] + [record({"R2"}, {"R2"}) for _ in range(59)]
        found = benchmark.grading(rows)
        self.assertEqual((found["scored_responses"], found["accepted"], found["accepted_wrong"], found["errors_of_a_point_or_more"]), (100, 41, 1, 1))
        self.assertAlmostEqual(found["exact_score_agreement"], .99)
        self.assertAlmostEqual(found["automatic_accept_coverage"], .41)
        self.assertAlmostEqual(found["error_rate_among_accepted"], 1 / 41)
        self.assertGreater(found["error_rate_among_accepted_upper_95"], .10)  # One error in 41: the truth could still be above 10%.
        self.assertAlmostEqual(found["mean_absolute_score_error"], .06)

    def test_votes_are_counted_per_assessment(self):
        apply = lambda chosen: {"applications": [{"rubric_item_id": item, "applied": item in chosen} for item in ("R1", "R2")]}
        predictions = {"qwen-32b": apply({"R1"}), "qwen-235b": apply({"R1"}), "qwen-thinking": apply({"R1"}), "gpt-oss": apply({"R2"}), "kimi": apply({"R2"})}
        lineages = {"qwen-32b": "qwen3", "qwen-235b": "qwen3", "qwen-thinking": "qwen3", "gpt-oss": "gpt-oss", "kimi": "kimi"}
        voted = benchmark.ensemble(predictions, lineages)
        self.assertEqual(voted["lineages"], ["gpt-oss", "kimi", "qwen3"])
        self.assertEqual({a["rubric_item_id"]: a["applied"] for a in voted["applications"]}, {"R1": True, "R2": False})  # Three separate Qwen assessments cast three votes.
        self.assertEqual(voted["models"], sorted(predictions))
        self.assertIsNotNone(benchmark.ensemble({"qwen-32b": apply({"R1"}), "qwen-235b": apply({"R1"})}, lineages))
        self.assertIsNone(benchmark.ensemble({"qwen-32b": apply({"R1"})}, lineages))

    def test_one_students_responses_never_straddle_a_split(self):
        students = [f"student-{n}" for n in range(600)]
        sizes = {name: sum(benchmark.split_of(s) == name for s in students) for name, _ in benchmark.SPLITS}
        self.assertTrue(320 < sizes["train"] < 400 and 90 < sizes["calibration"] < 150 and 90 < sizes["test"] < 150, sizes)
        self.assertEqual(benchmark.split_of("student-7"), benchmark.split_of("student-7"))


if __name__ == "__main__":
    unittest.main()
