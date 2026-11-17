"""Bubble sheet item analysis: per version, how each keyed question went; read only, and no student named."""
import csv
import io
import json
import random
import statistics
import unittest
import uuid

import test_bubble_keys as keys
from bubble_fixture import sheet
from grading.bubble_analysis import ANALYSIS, MINIMUM, analyse
from grading.bubble_keys import normalize_key, score_sheet
from rosters import connect
from test_bubble_sheets import HEADERS, KEY


def key_of(*versions, points="1", scoring="exact"):
    """A normalized key: each version a list of answers (question 1 first), or (answer, overrides) pairs."""
    listed = []
    for index, answers in enumerate(versions):
        questions = []
        for number, answer in enumerate(answers, 1):
            answer, extra = answer if isinstance(answer, tuple) else (answer, {})
            questions.append({"number": number, "answer": answer, **extra})
        listed.append({"letter": chr(65 + index), "questions": questions})
    return normalize_key({"defaults": {"points": points, "scoring": scoring}, "versions": listed})


def scored(key, sheet_id, answers, letter="A"):
    """A scored sheet as the Bubble sheets page scores it (`_bubble_scores`)."""
    version = next(v for v in key["versions"] if v["letter"] == letter)
    return {"sheet_id": sheet_id, "status": "scored", "version": letter, "answers": answers, **score_sheet(answers, version, key["defaults"])}


def rows(key, marks, letter="A"):
    return [scored(key, f"s{index:02}", answers, letter) for index, answers in enumerate(marks)]


class ItemAnalysisTests(unittest.TestCase):
    def test_a_miskeyed_question_is_named_by_the_letter_the_top_scorers_chose(self):
        # Twenty sheets whose first four answers rise with ability; question 5 is keyed B, but the stronger half marked C.
        key = key_of("ABCDB")
        marks = [[("ABCD"[q] if q < min(4, i // 4) else "E") for q in range(4)] + (["B"] if i < 10 else ["C"]) for i in range(20)]
        version, = analyse(key, rows(key, marks))
        self.assertEqual((version["letter"], version["sheets"], version["too_few"], version["group_size"]), ("A", 20, False, 6))
        fifth = version["questions"][4]
        self.assertEqual(fifth["flags"], [{"kind": "possible_miskey", "text": "More of the top scorers chose C than the key's B."}])
        choices = {c["choice"]: c for c in fifth["choices"]}
        self.assertEqual([(c["count"], c["upper"], c["lower"], c["key"]) for c in (choices["B"], choices["C"])], [(10, 0, 6, True), (10, 6, 0, False)])
        self.assertEqual((fifth["upper_pct"], fifth["lower_pct"], fifth["full"], fifth["full_pct"], fifth["difficulty"]), (0.0, 100.0, 10, 50.0, 0.5))
        self.assertEqual((choices["B"]["mean_pct"], choices["C"]["mean_pct"]), (36.0, 64.0))
        self.assertLess(fifth["discrimination"], -0.1)
        # The corrected point-biserial is the correlation with the rest of the score, as a plain float computation finds it.
        for question in version["questions"]:
            item = [1 if line["earned"] == "1" else 0 for line in (next(l for l in s["questions"] if l["number"] == question["number"]) for s in rows(key, marks))]
            rest = [int(s["earned"]) - x for s, x in zip(rows(key, marks), item)]
            self.assertAlmostEqual(question["discrimination"], round(statistics.correlation(item, rest), 3), places=9)
        self.assertEqual(version["questions"][3]["flags"], [{"kind": "hard", "text": "Sheets earned 20% of its points on average."}])
        self.assertEqual((version["mean_pct"], version["sd_pct"]), (50.0, 21.0))
        self.assertEqual(version["alpha"], 0.0)  # exactly: the fifth question pulls against the other four
        # Under Either/Or a key of several letters is named as the alternatives it is.
        either = key_of(["A", "B", "C", "D", ("AB", {"scoring": "either"})])
        self.assertEqual(analyse(either, rows(either, marks))[0]["questions"][4]["flags"][0]["text"], "More of the top scorers chose C than the key's A or B.")
        # Deterministic whatever the order the sheets come in, and no sheet is named.
        shuffled = rows(key, marks)
        random.Random(4).shuffle(shuffled)
        self.assertEqual(analyse(key, shuffled), [version])
        self.assertNotIn("s01", json.dumps(version))

    def test_negative_discrimination_without_a_rival_letter(self):
        # The top three split over B, C and D: no wrong letter beats the key there, yet the key's B went mostly to the weaker sheets.
        key = key_of("AAAB")
        ability = [0, 0, 1, 1, 1, 2, 2, 3, 3, 3]
        fourth = ["B", "B", "B", "B", "B", "B", "C", "B", "C", "D"]
        marks = [["A" if q < ability[i] else "E" for q in range(3)] + [fourth[i]] for i in range(10)]
        question = analyse(key, rows(key, marks))[0]["questions"][3]
        self.assertEqual([(c["choice"], c["upper"]) for c in question["choices"] if c["upper"]], [("B", 1), ("C", 1), ("D", 1)])
        item = [1 if mark == "B" else 0 for mark in fourth]
        self.assertAlmostEqual(question["discrimination"], round(statistics.correlation(item, ability), 3), places=9)
        self.assertLess(question["discrimination"], -0.1)
        self.assertEqual(question["flags"], [{"kind": "possible_miskey", "text": f"The top scorers did worse on it than the rest (discrimination {question['discrimination']})."}])

    def test_alpha_is_kr20_for_right_and_wrong_questions(self):
        # p = .7, .5, .3, so sum(pq) = .67; the totals 3 3 2 2 1 1 2 0 1 0 have variance 1.05 (over n):
        # alpha = 3/2 * (1 - .67/1.05) = .542857...
        pattern = ["111", "111", "110", "110", "100", "100", "101", "000", "010", "000"]
        key = key_of("ABC")
        marks = [["ABC"[q] if bit == "1" else "E" for q, bit in enumerate(bits)] for bits in pattern]
        version, = analyse(key, rows(key, marks))
        self.assertEqual(version["alpha"], 0.543)
        # Shares, not points: doubling one question's points changes nothing.
        heavy = key_of([("A", {"points": "2"}), "B", "C"])
        self.assertEqual(analyse(heavy, rows(heavy, marks))[0]["alpha"], 0.543)
        self.assertEqual([q["difficulty"] for q in version["questions"]], [0.7, 0.5, 0.3])
        self.assertEqual(version["questions"][2]["flags"], [])  # .3 is not below .30
        first = [int(bits[0]) for bits in pattern]
        rest = [int(bits[1]) + int(bits[2]) for bits in pattern]
        self.assertAlmostEqual(version["questions"][0]["discrimination"], round(statistics.correlation(first, rest), 3), places=9)

    def test_partial_scoring_blanks_several_letters_and_too_few_sheets(self):
        # Question 1 is keyed B and D under Partial for 2 points; question 2 was past what three sheets were read with.
        key = key_of([("BD", {"scoring": "partial", "points": "2"}), "A"])
        marks = [["B", "A"], ["BD"], ["BC", "C"], [""], ["D", "C"], ["BD", ""]]
        version, = analyse(key, rows(key, marks))
        self.assertEqual((version["sheets"], version["too_few"], version["group_size"], version["alpha"]), (6, True, None, None))
        self.assertEqual(version["mean_pct"], 38.9)  # totals 2 2 0 0 1 2 of 3
        self.assertAlmostEqual(version["sd_pct"], round(statistics.stdev([2, 2, 0, 0, 1, 2]) * 100 / 3, 1))
        first, second = version["questions"]
        self.assertEqual((first["answer"], first["scoring"], first["points"], first["difficulty"], first["full"], first["read"]), ("BD", "partial", "2", 0.5, 2, 6))
        self.assertEqual((first["discrimination"], first["upper_pct"], first["lower_pct"]), (None, None, None))
        counts = {c["choice"]: (c["count"], c["key"], c["upper"]) for c in first["choices"]}
        self.assertEqual(counts, {"A": (0, False, None), "B": (1, True, None), "C": (0, False, None), "D": (1, True, None), "E": (0, False, None),
                                  "blank": (1, False, None), "multiple": (3, False, None)})
        multiple = first["choices"][-1]
        self.assertEqual((multiple["common"], multiple["common_count"], multiple["mean_pct"]), ("BD", 2, 44.4))
        self.assertEqual(first["flags"], [])
        # Unread counts as blank. With so few sheets nothing is flagged, not even a question almost nobody got right.
        self.assertEqual((second["read"], second["difficulty"]), (4, 0.167))
        self.assertEqual({c["choice"]: c["count"] for c in second["choices"] if c["count"]}, {"A": 1, "C": 2, "blank": 3})
        self.assertEqual(second["flags"], [])
        many = key_of([("BD", {"scoring": "partial", "points": "2"}), "A"])
        second = analyse(many, rows(many, marks * 2))[0]["questions"][1]
        self.assertEqual(second["flags"][0], {"kind": "hard", "text": "Sheets earned 16.7% of its points on average."})
        self.assertEqual([f["kind"] for f in second["flags"]], ["hard", "weak"])  # the sheets that got it right have the average rest of the score

    def test_ties_at_the_group_boundary_are_ordered_by_sheet(self):
        # Every sheet totals 1: the bottom three are s00-s02 and the top three s07-s09, whatever order they arrive in.
        key = key_of("AB")
        marks = [["A", "C"] if i % 2 == 0 else ["C", "B"] for i in range(10)]
        sheets = rows(key, marks)
        random.Random(9).shuffle(sheets)
        version, = analyse(key, sheets)
        first = {c["choice"]: (c["count"], c["upper"], c["lower"]) for c in version["questions"][0]["choices"]}
        self.assertEqual((first["A"], first["C"]), ((5, 1, 2), (5, 2, 1)))
        self.assertEqual((version["questions"][0]["upper_pct"], version["questions"][0]["lower_pct"]), (33.3, 66.7))
        # The two questions trade off exactly, so neither question agrees with the rest of the score, and the total never varies.
        self.assertEqual((version["alpha"], version["sd_pct"]), (None, 0.0))
        self.assertEqual([q["discrimination"] for q in version["questions"]], [-1.0, -1.0])
        # Question 1: two of the top three chose C over the key's A. Question 2: no letter beats B there, but the discrimination does.
        self.assertEqual([[f["text"] for f in q["flags"]] for q in version["questions"]],
                         [["More of the top scorers chose C than the key's A."], ["The top scorers did worse on it than the rest (discrimination -1.0)."]])
        # Two sheets alike in every way: nothing varies and nothing is claimed.
        same = analyse(key, rows(key, [["A", "B"]] * 12))[0]
        self.assertEqual((same["alpha"], [q["discrimination"] for q in same["questions"]]), (None, [None, None]))
        self.assertEqual([[f["kind"] for f in q["flags"]] for q in same["questions"]], [["easy"], ["easy"]])

    def test_versions_are_analysed_apart_and_only_scored_sheets_count(self):
        # Version B scrambles the questions: its question 1 is A's question 2. Nothing pairs them.
        key = key_of("AB", "BA")
        sheets = rows(key, [["A", "B"]] * 11 + [["B", "B"]]) + [scored(key, f"b{i}", ["B", "A"], "B") for i in range(3)]
        sheets.append({**scored(key, "r1", ["C", "C"]), "status": "needs_review"})
        first, second = analyse(key, sheets)
        self.assertEqual([(v["letter"], v["sheets"], v["too_few"]) for v in (first, second)], [("A", 12, False), ("B", 3, True)])
        self.assertEqual([q["answer"] for q in second["questions"]], ["B", "A"])
        self.assertEqual({c["choice"]: c["count"] for c in second["questions"][0]["choices"] if c["count"]}, {"B": 3})
        self.assertEqual({c["choice"]: c["count"] for c in first["questions"][0]["choices"] if c["count"]}, {"A": 11, "B": 1})
        self.assertEqual(analyse(None, sheets), [])
        empty = analyse(key_of("AB", "BA"), [])[1]
        self.assertEqual((empty["sheets"], empty["mean_pct"], empty["questions"][0]["difficulty"], empty["questions"][0]["flags"]), (0, None, None, []))
        self.assertEqual((MINIMUM, ANALYSIS), (10, "item-analysis-v1"))


class ItemAnalysisRouteTests(unittest.TestCase):
    """Through the app: the page's own sheets, read only, and the Bubble sheets payload left as it was."""
    setUp, read, clear = keys.BubbleGradingTests.setUp, keys.BubbleGradingTests.read, keys.BubbleGradingTests.clear

    def test_the_analysis_and_its_csv_name_no_student(self):
        other = {**KEY, 1: "A"}
        message = self.read([sheet(KEY, "A"), sheet(other, "B"), sheet({**KEY, 3: "A"}, "A")],
                            [("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen")])
        self.clear(message)
        with connect(self.app.config["DATABASE"]) as db:
            plan_id = self.frq.scan_status(db, message)["plan"]["id"]
        base = f"/api/grading/assignments/{plan_id}/bubble-sheets"
        self.assertEqual(self.client.get(f"{base}/analysis").json["versions"], [])  # no key yet
        answers = lambda key: [{"number": n, "answer": key[n]} for n in range(1, 11)]
        saved = self.client.post(f"{base}/key", headers=HEADERS, json={"request_id": str(uuid.uuid4()), "base_revision": None,
                                 "defaults": {"points": "1", "scoring": "exact"}, "versions": [{"letter": "A", "questions": answers(KEY)}, {"letter": "B", "questions": answers(other)}]})
        self.assertEqual(saved.status_code, 201, saved.json)
        page = self.client.get(base).json
        # The page's payload is what it was: no analysis field was added to it or to its sheets.
        self.assertEqual(set(page), {"assignment_id", "key", "rule", "question_limit", "sheets", "stats", "waiting_scans", "counts", "read_questions",
                                     "fewest_read", "beyond_read", "suggested_versions"})
        self.assertEqual(set(page["sheets"][0]), {"sheet_id", "job_id", "page", "student", "section", "marked_version", "version", "status", "reason",
                                                  "earned", "possible", "wrong", "answers", "identified_by"})
        with connect(self.app.config["DATABASE"]) as db:
            events = db.execute("SELECT COUNT(*) FROM frq_events").fetchone()[0]
        response = self.client.get(f"{base}/analysis")
        self.assertEqual((response.status_code, response.headers["Cache-Control"]), (200, "no-store"))
        view = response.json
        self.assertEqual({k: view[k] for k in ("assignment_id", "rule", "scoring_rule", "minimum", "key_revision")},
                         {"assignment_id": plan_id, "rule": "item-analysis-v1", "scoring_rule": "bubble-scoring-v2", "minimum": 10, "key_revision": 1})
        self.assertEqual(view["counts"], page["counts"])
        self.assertEqual([(v["letter"], v["sheets"], v["too_few"]) for v in view["versions"]], [("A", 2, True), ("B", 1, True)])
        third = view["versions"][0]["questions"][2]
        self.assertEqual(({c["choice"]: c["count"] for c in third["choices"] if c["count"]}, third["full"]), ({KEY[3]: 1, "A": 1}, 1))
        text = response.get_data(as_text=True)
        for private in ("123456789", "Alice", "Nguyen", *[s["sheet_id"] for s in page["sheets"]]):
            self.assertNotIn(private, text)
        _, full = self.frq._bubble_view(plan_id)
        for enrollment in {s["enrollment_id"] for s in full["sheets"] if s["enrollment_id"]}:
            self.assertNotIn(enrollment, text)

        export = self.client.get(f"{base}/analysis.csv")
        self.assertEqual((export.status_code, export.mimetype, export.headers["Cache-Control"]), (200, "text/csv", "no-store"))
        self.assertIn("attachment", export.headers["Content-Disposition"])
        self.assertIn("Test 1 - bubble sheet item analysis.csv", export.headers["Content-Disposition"])
        table = list(csv.reader(io.StringIO(export.data.decode("utf-8-sig"))))
        self.assertEqual(table[0], ["Version", "Question", "Key", "Scoring", "Points", "Sheets", "Percent full", "Difficulty", "Discrimination",
                                    "Upper %", "Lower %", "A", "B", "C", "D", "E", "Blank", "Multiple", "Flags"])
        self.assertEqual(len(table), 21)
        self.assertEqual(table[3][:11], ["A", "3", KEY[3], "exact", "1", "2", "50.0", "0.5", "", "", ""])
        self.assertNotIn("Alice", export.get_data(as_text=True))
        # Reading changed nothing: no event, no review.
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events").fetchone()[0], events)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bubble_reviews").fetchone()[0], 0)
        self.assertEqual(self.client.get(base).json, page)


if __name__ == "__main__":
    unittest.main()
