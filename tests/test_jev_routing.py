"""JEV ROUTING (docs/decisions/jev.md; requirement, 2026-10-03): a sentence typed in the Ctrl K palette matched by Jev to one of the
palette's own places. A scripted Jev only; nothing reaches TypeSafe."""
import os
import tempfile
import unittest
from unittest.mock import patch

from test_storage import HEADERS

from app import create_app
from jev import client, routing
from rosters import connect

GROUPS = {"Pages": [{"id": "0:0", "label": "Teaching Inbox", "detail": ""}, {"id": "0:1", "label": "Mail", "detail": ""}],
          "Grading": [{"id": "7:0", "label": "Quiz 3", "detail": "MATH 140 · Fall 2026 · Grading"},
                      {"id": "7:1", "label": "Quiz 4", "detail": "MATH 140 · Fall 2026 · Grading"},
                      {"id": "7:2", "label": "Exam 1", "detail": "MATH 140 · Fall 2026 · Grading"}],
          "Sections": [{"id": "3:0", "label": "MATH 140 · Fall 2026 · Section 507", "detail": ""},
                       {"id": "3:1", "label": "MATH 140 · Fall 2026 · Section 508", "detail": ""}]}


def scripted(place, step="overview", confidence=.95, reversed_place=None):
    """A fake `jev.client.evaluate`: the place option (\"kind: name\"), in both orders unless `reversed_place`, and the step."""
    def evaluate(state, questions):
        answers = {}
        for name, question in questions.items():
            pick = step if name == "step" else reversed_place if reversed_place and name.endswith("__reversed") else place
            pick = pick if pick in question["criteria"] else next(iter(question["criteria"]))
            answers[name] = {"type": "choice", "choice": pick, "confidence": confidence,
                             "probabilities": {option: (1.0 if option == pick else 0.0) for option in question["criteria"]}}
        return {"model": client.MODEL, "answers": answers, "usage": {"input_tokens": 200, "output_tokens": 10}}
    return evaluate


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        patch("jev.client.available", return_value=True).start()
        self.addCleanup(patch.stopall)

    def ask(self, query, groups=None, **extra):
        return self.client.post("/api/jev/route", json={"query": query, "groups": groups or GROUPS}, headers=HEADERS, **extra)

    def test_a_confident_guess_names_the_place_and_its_grading_step(self):
        with patch("jev.client.evaluate", side_effect=scripted("Grading: Quiz 3", "review")) as asked:
            answer = self.ask("show quiz 3 flags for 508")
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(answer.json["status"], "ok")
        self.assertTrue(answer.json["confident"])
        self.assertEqual(answer.json["picks"][0], {"id": "7:0", "confidence": .95, "tab": "review"})
        self.assertEqual(asked.call_count, 1)
        # Numbers are matched in code: Quiz 4, Exam 1 and Section 507 were never offered; places without numbers stay.
        offered = asked.call_args.args[1]["place"]["criteria"]
        self.assertEqual(set(offered), {"Grading: Quiz 3", "Sections: MATH 140 · Fall 2026 · Section 508", "Pages: Teaching Inbox",
                                        "Pages: Mail", routing.NONE})
        self.assertEqual(offered["Grading: Quiz 3"], routing.GROUPS["Grading"])

    def test_the_sentence_is_hashed_never_stored(self):
        with patch("jev.client.evaluate", side_effect=scripted("Pages: Mail")):
            self.ask("open my mail from Alice Example")
        with connect(self.app.config["DATABASE"]) as db:
            rows = db.execute("SELECT * FROM jev_calls WHERE purpose='palette-route'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertFalse(any("Alice" in " ".join(str(v) for v in tuple(row)) for row in rows))

    def test_a_question_disagreement_or_low_confidence_is_not_a_confident_guess(self):
        with patch("jev.client.evaluate", side_effect=scripted(routing.NONE)):
            self.assertEqual(self.ask("why did bob lose points on quiz 3").json, {"status": "not_navigation"})
        with patch("jev.client.evaluate", side_effect=scripted("Grading: Quiz 4", reversed_place="Pages: Mail")):
            answer = self.ask("take me to quiz four grading").json
        self.assertEqual(answer["status"], "unsure")
        with patch("jev.client.evaluate", side_effect=scripted("Pages: Mail", confidence=.5)):
            answer = self.ask("go to the mail page").json
        self.assertEqual((answer["status"], answer["confident"], answer["picks"][0]["id"]), ("ok", False, "0:1"))
        # A grading step is used only when it is likely; otherwise the assignment's overview.
        with patch("jev.client.evaluate", side_effect=scripted("Grading: Exam 1", "export", confidence=.65)):
            self.assertEqual(self.ask("exam 1 certification please").json["picks"][0]["tab"], "overview")

    def test_failures_are_reported_and_not_resent(self):
        with patch("jev.client.evaluate", side_effect=client.DecisionError("Jev could not complete this check (HTTP 500).")) as asked:
            self.assertEqual(self.ask("open the inbox page").json["status"], "failed")
            self.assertEqual(self.ask("open the inbox page").json["status"], "failed")
        self.assertEqual(asked.call_count, 1)
        with patch("jev.client.available", return_value=False):
            self.assertEqual(self.ask("open the inbox page").json, {"status": "unavailable"})

    def test_input_is_bounded(self):
        for body in ({"query": "inbox", "groups": GROUPS}, {"query": "open the inbox", "groups": {"Students": []}},
                     {"query": "open the inbox", "groups": {"Pages": [{"id": "x" * 41, "label": "Inbox"}]}},
                     {"query": "open the inbox", "groups": GROUPS, "extra": 1}):
            self.assertEqual(self.client.post("/api/jev/route", json=body, headers=HEADERS).status_code, 400, body)
        self.assertEqual(self.client.post("/api/jev/route", json={"query": "open the inbox", "groups": GROUPS}).status_code, 403)

    def test_candidates_keep_the_typed_numbers(self):
        self.assertEqual([i["label"] for i in routing.candidates("roster for 508", GROUPS["Sections"] + GROUPS["Pages"])],
                         ["MATH 140 · Fall 2026 · Section 508", "Teaching Inbox", "Mail"])
        self.assertEqual(len(routing.candidates("open the grading", GROUPS["Grading"])), 3)
        # Two places of one name are told apart by their detail.
        self.assertEqual(set(routing.options("quiz 3", {"Grading": GROUPS["Grading"][:1] * 2})),
                         {"Grading: Quiz 3 (MATH 140 · Fall 2026 · Grading)", "Grading: Quiz 3 (MATH 140 · Fall 2026 · Grading) *"})


if __name__ == "__main__":
    unittest.main()
