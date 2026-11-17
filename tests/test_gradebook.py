"""Term gradebook service and API: setup revisions, score revisions, idempotent writes, imports, pulls, the CSV export,
offering protection and the calendar deadline (gradebook/README.md)."""
import csv
import io
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from app import create_app
from gradebook import changes as gradebook_changes, service as gradebook_service
from gradebook.setup import SCALE, default_setup
from rosters import connect
from test_rosters import COLUMNS, STUDENTS, csv_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
CAROL = ["", "Example, Carol", "234567890", "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@example.test"]
UINS = ("001234567", "123456789", "234567890")


class GradebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                          "sections": [501, 502], "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        self.course = created.json
        state = self.client.get("/api/state").json
        self.group = state["course_groups"][0]["id"]
        self.definition = state["course_definitions"][0]["id"]
        for number, rows in ((501, STUDENTS), (502, [CAROL])):
            response = self.client.post(f"/api/course-groups/{self.group}/sections/{number}/roster",
                                        data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS)
            self.assertEqual(response.status_code, 201, response.json)
        self.term = self.client.get("/api/gradebook").json["terms"][0]
        self.base = f"/api/gradebook/terms/{self.term['id']}"
        self.sections = {section["number"]: section["id"] for section in self.term["sections"]}
        view = self.view()
        self.rows = {student["name"]: student["row"] for student in view["students"]}

    def view(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def post(self, path, payload, status=200):
        response = self.client.post(self.base + path, json=payload, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.json)
        return response.json

    def save(self, setup, base=None, status=200, request_id=None):
        return self.post("/setup", {"base_revision": base, "request_id": request_id or str(uuid.uuid4()), "setup": setup}, status)

    def two_columns(self):
        """Test 1 (100 points, every section) and a section-502 quiz (10 points)."""
        view = self.save({"categories": [{"name": "Exams", "weight": "100"}],
                          "items": [{"title": "Test 1", "points": "100"}, {"title": "Quiz", "points": "10", "sections": [self.sections[502]]}]})
        items = {item["title"]: item["id"] for item in view["setup"]["items"]}
        category = view["setup"]["categories"][0]["id"]
        view = self.save({"items": [{**item, "category_id": category} if item["title"] == "Test 1" else item for item in view["setup"]["items"]]},
                         base=view["setup"]["revision"])
        return view, items["Test 1"], items["Quiz"]

    def scores(self, changes, status=200, request_id=None):
        return self.post("/scores", {"request_id": request_id or str(uuid.uuid4()), "changes": changes}, status)

    def test_catalog_and_an_empty_view(self):
        self.assertEqual({key: self.term[key] for key in ("code", "name", "term_label", "is_current", "students", "items", "revision",
                                                          "graded_without_column")},
                         {"code": "MATH 140", "name": "Calculus", "term_label": "Fall 2026", "is_current": True, "students": 3,
                          "items": 0, "revision": None, "graded_without_column": 0})  # nothing graded in Faculty yet
        self.assertEqual(self.term["group_id"], self.group)  # `#gradebook?group=` resolves from the course page's own ID
        self.assertEqual([section["number"] for section in self.term["sections"]], [501, 502])
        response = self.client.get(self.base)
        view = response.json
        self.assertIsNone(view["setup"]["revision"])
        self.assertEqual(view["setup"]["items"], [])
        self.assertEqual([student["name"] for student in view["students"]], ["Example, Alice", "Example, Bob", "Example, Carol"])
        self.assertEqual(set(view["students"][0]), {"row", "section_id", "enrollment_id", "section", "name", "accommodations", "dr", "dropped"})
        self.assertTrue(all(student["row"] == f"{student['section_id']}:{student['enrollment_id']}" for student in view["students"]))
        self.assertEqual((view["cells"], view["requests"], view["links"]), ({}, [], {}))
        self.assertIn("students", view["results"])
        for uin in UINS:
            self.assertNotIn(uin, response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/gradebook/terms/missing").status_code, 404)

    def test_the_grading_scheme_is_the_terms_scale(self):
        """GRADEBOOK GRADING SCHEME: the term list carries each term's scale (the standard one until changed); the course Home's
        dialog saves the scale ALONE on the revision it was opened on, the rest of the setup kept, and every section's letters
        follow; a save on an older revision is refused."""
        self.assertEqual(self.term["scale"], [{"letter": letter, "min": minimum} for letter, minimum in SCALE])
        self.assertNotIn("scale", self.view()["term"])  # additive on the term list only; the View's term is unchanged
        view, test, quiz = self.two_columns()
        alice, carol = self.rows["Example, Alice"], self.rows["Example, Carol"]
        self.scores([{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "68"},
                     {"item_id": test, "row": carol, "base": None, "status": "scored", "points": "58"}])
        before = self.view()
        self.assertEqual((before["results"]["students"][alice]["letter"], before["results"]["students"][carol]["letter"]), ("D", "F"))
        canvas_like = [{"letter": "A", "min": "90"}, {"letter": "B", "min": "80"}, {"letter": "C", "min": "67"},
                       {"letter": "D", "min": "57"}, {"letter": "F", "min": "0"}]
        after = self.save({"scale": canvas_like}, base=before["setup"]["revision"])
        self.assertEqual(after["setup"]["revision"], before["setup"]["revision"] + 1)
        self.assertEqual(after["setup"]["scale"], canvas_like)
        self.assertEqual({key: after["setup"][key] for key in ("mode", "categories", "items", "ungraded", "letter_rounding")},
                         {key: before["setup"][key] for key in ("mode", "categories", "items", "ungraded", "letter_rounding")})
        self.assertEqual((after["results"]["students"][alice]["letter"], after["results"]["students"][carol]["letter"]), ("C", "D"))
        scoped = self.client.get(f"{self.base}?section={self.sections[502]}").json  # another section's tab: the same scheme
        self.assertEqual(scoped["setup"]["scale"], canvas_like)
        self.assertEqual(scoped["results"]["students"][carol]["letter"], "D")
        self.assertEqual(self.client.get("/api/gradebook").json["terms"][0]["scale"], canvas_like)
        self.save({"scale": [{"letter": "A", "min": "93"}, {"letter": "F", "min": "0"}]}, base=before["setup"]["revision"], status=409)
        self.assertEqual(self.client.get("/api/gradebook").json["terms"][0]["scale"], canvas_like)

    def test_a_section_view_may_carry_only_its_cells(self):
        """`?section=<id>&cells=section` (opt-in, PERFORMANCE "OPT-IN AND ADDITIVE API"): that section's cells, every column key
        kept, and each column's term-wide `cell_counts`; without it every body is as before."""
        view, test, quiz = self.two_columns()
        alice, carol = self.rows["Example, Alice"], self.rows["Example, Carol"]
        self.scores([{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "80"},
                     {"item_id": test, "row": carol, "base": None, "status": "scored", "points": "70"},
                     {"item_id": quiz, "row": carol, "base": None, "status": "scored", "points": "9"}])
        term = self.view()
        scoped = self.client.get(f"{self.base}?section={self.sections[501]}").json
        self.assertNotIn("cell_counts", term)
        self.assertNotIn("cell_counts", scoped)
        self.assertEqual(scoped["cells"], term["cells"])  # the default section View still carries every section's cells
        narrow = self.client.get(f"{self.base}?section={self.sections[501]}&cells=section")
        self.assertEqual(narrow.status_code, 200, narrow.json)
        narrow = narrow.json
        self.assertEqual(narrow["cells"], {test: {alice: term["cells"][test][alice]}, quiz: {}})
        self.assertEqual(narrow["cell_counts"], {test: 2, quiz: 1})  # term-wide: the setup is the term's
        self.assertEqual({key: value for key, value in narrow.items() if key not in ("cells", "cell_counts")},
                         {key: value for key, value in scoped.items() if key != "cells"})  # results and the rest are the section's already
        # Without a section there is nothing to narrow: the term's body as before. Any other value is refused.
        self.assertEqual(self.client.get(f"{self.base}?cells=section").json, term)
        self.assertEqual(self.client.get(f"{self.base}?section={self.sections[501]}&cells=all").status_code, 400)

    def test_a_column_without_a_category_is_named_while_it_counts_toward_nothing(self):
        view, test, quiz = self.two_columns()  # Test 1 in Exams; the quiz has no category
        self.assertNotIn("uncounted", view["results"])  # total points: Uncategorized counts
        setup = {key: value for key, value in view["setup"].items() if key not in ("revision", "created_at", "actor")}
        weighted = self.save({**setup, "mode": "weighted"}, base=view["setup"]["revision"])
        self.assertEqual(weighted["results"]["uncounted"], [quiz])
        self.assertEqual(self.view()["results"]["uncounted"], [quiz])
        setup = {key: value for key, value in weighted["setup"].items() if key not in ("revision", "created_at", "actor")}
        archived = self.save({**setup, "items": [{**item, "archived": item["id"] == quiz} for item in setup["items"]]}, base=weighted["setup"]["revision"])
        self.assertNotIn("uncounted", archived["results"])

    def test_setup_revisions_are_idempotent_checked_and_never_repeated(self):
        request_id = str(uuid.uuid4())
        setup = {"items": [{"title": "Homework 1", "points": "20", "due_date": "2026-10-01"}]}
        view = self.save(setup, request_id=request_id)
        self.assertEqual(view["setup"]["revision"], 1)
        self.assertTrue(view["setup"]["items"][0]["id"].startswith("item_"))
        # The same request again returns the view and records nothing; the same ID with other content is refused.
        self.assertEqual(self.save(setup, request_id=request_id)["setup"]["items"], view["setup"]["items"])
        self.save({"items": [{"title": "Homework 2", "points": "20"}]}, request_id=request_id, status=409)
        # A save made on an older revision is refused, and an identical save records nothing.
        self.save({"items": []}, base=None, status=409)
        stored = {key: value for key, value in view["setup"].items() if key not in ("revision", "created_at", "actor")}
        self.assertEqual(self.save(stored, base=1)["setup"]["revision"], 1)
        self.save({"mode": "curved"}, base=1, status=400)
        self.post("/setup", {"base_revision": "1", "request_id": "x", "setup": {}}, 400)
        self.post("/setup", {"base_revision": None, "request_id": "", "setup": {}}, 400)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_setups").fetchone()[0], 1)

    def test_setup_and_link_refuse_a_foreign_section_before_writing(self):
        """GRADEBOOK: an invalid section must not commit a shared setup and then fail while rendering its answer."""
        assignment = self.client.post("/api/documents/assignments", json={
            "definition_id": self.definition, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        for section in ("not-this-terms-section", [], {"id": self.sections[501]}):
            with self.subTest(section=section):
                self.post("/setup", {"base_revision": None, "request_id": "invalid-setup-section",
                                     "section_id": section, "setup": {"items": [{"title": "Quiz", "points": "10"}]}}, 400)
                self.post("/items/link", {"assignment_id": assignment["id"], "request_id": "invalid-link-section",
                                          "section_id": section}, 400)
                with connect(self.app.config["DATABASE"]) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_setups").fetchone()[0], 0)

    def test_scores_are_revisions_of_one_cell_written_idempotently(self):
        view, test, quiz = self.two_columns()
        alice, bob, carol = (self.rows[name] for name in ("Example, Alice", "Example, Bob", "Example, Carol"))
        request_id = str(uuid.uuid4())
        changes = [{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "87.50"},
                   {"item_id": test, "row": bob, "base": None, "status": "excused"},
                   {"item_id": test, "row": carol, "base": None, "status": "missing", "note": "  late  "},
                   {"item_id": quiz, "row": carol, "base": None, "status": "scored", "points": 9}]
        written = self.scores(changes, request_id=request_id)
        self.assertEqual(written["cells"][test][alice]["points"], "87.5")
        self.assertEqual(written["cells"][test][carol]["note"], "late")
        self.assertEqual(written["cells"][test][bob]["status"], "excused")
        self.assertEqual(written["results"]["students"][alice]["percent"], "87.50")
        # A replay returns the same revisions; the same request ID with other content is refused.
        self.assertEqual(self.scores(changes, request_id=request_id)["cells"], written["cells"])
        self.scores(changes[:1], request_id=request_id, status=409)
        # A stale base refuses the whole batch and names the stale cells.
        alice_cell = written["cells"][test][alice]["id"]
        stale = self.scores([{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "90"},
                             {"item_id": test, "row": bob, "base": written["cells"][test][bob]["id"], "status": "scored", "points": "70"}], 409)
        self.assertEqual(stale["stale"], [{"item_id": test, "row": alice}])
        self.assertIn("error", stale)
        # A change keeps the cell's note unless it sends one; an identical change is skipped and records nothing.
        updated = self.scores([{"item_id": test, "row": carol, "base": written["cells"][test][carol]["id"], "status": "scored", "points": "50"},
                               {"item_id": test, "row": bob, "base": written["cells"][test][bob]["id"], "status": "excused"}])
        self.assertEqual(list(updated["cells"][test]), [carol])
        self.assertEqual(updated["cells"][test][carol]["note"], "late")
        cleared = self.scores([{"item_id": test, "row": alice, "base": alice_cell, "status": "cleared"}])
        self.assertEqual(cleared["cells"][test][alice]["status"], "cleared")
        # The write brings the latest request log, so the page need not read the whole view again.
        self.assertEqual(cleared["requests"][0]["kind"], "scores")
        self.assertEqual(cleared["requests"][0]["summary"]["counts"]["cleared"], 1)
        view = self.view()
        self.assertIn(test, view["scored_items"])  # scores exist (one cleared): the column cannot be removed, only archived
        self.assertNotIn(alice, view["cells"][test])  # a cleared cell is empty in the view
        self.assertEqual(view["cells"][quiz][carol]["points"], "9")
        # The browser sees a cleared cell as empty: an empty base writes over it.
        self.scores([{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "88"}])
        history = self.client.get(self.base + "/history", query_string={"item": test, "row": alice}).json["revisions"]
        self.assertEqual([(entry["seq"], entry["status"], entry["points"]) for entry in history],
                         [(3, "scored", "88"), (2, "cleared", None), (1, "scored", "87.5")])
        self.assertEqual(history[0]["request"]["kind"], "scores")
        self.assertEqual(self.client.get(self.base + "/history", query_string={"item": test, "row": "bad"}).status_code, 400)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_requests").fetchone()[0], 4)
        # Checks on each change.
        for bad in ({"item_id": quiz, "row": alice, "base": None, "status": "scored", "points": "5"},  # section 501 has no quiz
                    {"item_id": test, "row": f"{self.sections[501]}:1999999999", "base": None, "status": "missing"},  # not a member
                    {"item_id": "item_unknown", "row": bob, "base": None, "status": "missing"},
                    {"item_id": test, "row": bob, "base": None, "status": "excused", "points": "3"},
                    {"item_id": test, "row": bob, "base": None, "status": "scored"},
                    {"item_id": test, "row": bob, "base": None, "status": "scored", "points": "-1"},
                    {"item_id": test, "row": bob, "base": None, "status": "graded"},
                    {"item_id": test, "row": bob, "base": None, "status": "missing", "note": "x" * 1001}):
            with self.subTest(bad=bad):
                self.scores([bad], 400)
        self.scores([changes[1], changes[1]], 400)
        self.scores([], 400)
        self.scores([{**changes[1], "row": f"{self.sections[502]}:{i:010d}"} for i in range(5001)], 400)
        # A column with scores cannot be removed from the setup (archive it instead).
        current = self.view()["setup"]
        self.save({"items": [item for item in current["items"] if item["id"] != test]}, base=current["revision"], status=400)
        with connect(self.app.config["DATABASE"]) as db:
            for statement in ("UPDATE gradebook_scores SET points='1'", "DELETE FROM gradebook_scores", "DELETE FROM gradebook_requests",
                              "UPDATE gradebook_setups SET actor='x'"):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(statement)

    def test_writes_need_their_content_types(self):
        self.assertEqual(self.client.post(self.base + "/scores", data={"x": "1"}, headers=HEADERS).status_code, 415)
        self.assertEqual(self.client.post(self.base + "/imports/preview", json={}, headers=HEADERS).status_code, 415)
        self.assertEqual(self.client.post(self.base + "/scores", json={"request_id": "x", "changes": []}).status_code, 403)

    def test_json_the_gradebook_cannot_store_is_a_readable_400(self):
        # Review fix: deep nesting was a 500 (RecursionError is not a ValueError); NaN and lone surrogates were 400s that
        # quoted Python ("Out of range float values are not JSON compliant", "'utf-8' codec can't encode").
        view, test, _ = self.two_columns()
        change = {"item_id": test, "row": self.rows["Example, Alice"], "base": None, "status": "missing"}
        for path, raw in (("/scores", b"[" * 100000 + b"]" * 100000),
                          ("/setup", b'{"base_revision": 2, "request_id": "n", "setup": {"categories": [{"name": "A", "weight": NaN}]}}'),
                          ("/scores", ('{"request_id": "s", "changes": [%s]}' % json_text({**change, "note": "PLACEHOLDER"})).replace("PLACEHOLDER", "\\ud800").encode())):
            with self.subTest(path=path, raw=raw[:40]):
                response = self.client.post(self.base + path, data=raw, headers={**HEADERS, "Content-Type": "application/json"})
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True)[:200])
                self.assertNotRegex(response.json["error"], "codec|compliant|recursion")
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_requests").fetchone()[0], 0)

    def test_an_import_from_a_section_tab_writes_only_that_section(self):
        """requirement, 2026-09-28: the gradebook lives in each section's tab; its imports cover only that section."""
        view = self.save({"items": [{"title": "Test 1", "points": "100"}]})
        test = view["setup"]["items"][0]["id"]
        content = ("UIN,Name,Test 1\r\n001234567,Alice,90\r\n" + CAROL[2] + ",Carol,70\r\n").encode()
        options = {"mapping": {"c2": {"action": "item", "item_id": test}}, "section_id": self.sections[502]}
        preview = self.import_file("/imports/preview", content, {"options": json_text(options)})
        self.assertEqual([(change["name"], change["section"]) for change in preview["changes"]], [("Example, Carol", 502)])
        self.assertEqual((preview["counts"]["other_section"], preview["unmatched"]), (1, []))
        applied = self.import_file("/imports", content, {"options": json_text(options), "fingerprint": preview["fingerprint"], "request_id": "sec-502"})
        self.assertEqual(set(applied["cells"][test]), {self.rows["Example, Carol"]})
        self.import_file("/imports/preview", content, {"options": json_text({**options, "section_id": "not-a-section"})}, 400)

    def test_an_import_does_not_take_a_request_id_a_setup_revision_holds(self):
        # Review fix: an import that creates columns records a setup revision under its request ID; one a setup save already
        # took failed on UNIQUE(term_id, request_id) and answered 503 "Check server storage".
        self.save({"items": [{"title": "Test 1", "points": "100"}]}, request_id="shared-id")
        content = b"UIN,Name,Quiz 2\r\n001234567,Alice,8\r\n"
        options = json_text({"mapping": {"c2": {"action": "new", "title": "Quiz 2", "points": "10"}}})
        preview = self.import_file("/imports/preview", content, {"options": options})
        refused = self.import_file("/imports", content, {"options": options, "fingerprint": preview["fingerprint"], "request_id": "shared-id"}, 409)
        self.assertIn("request ID", refused["error"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("gradebook_setups", "gradebook_requests", "gradebook_scores")],
                             [1, 0, 0])
        applied = self.import_file("/imports", content, {"options": options, "fingerprint": preview["fingerprint"], "request_id": "import-id"})
        self.assertEqual(applied["setup"]["revision"], 2)

    def changes(self, section=None, **params):
        response = self.client.get(self.base + "/changes", query_string={**({"section": self.sections[section]} if section else {}), **params})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_the_changes_page_lists_each_setup_revision_and_write_of_a_section(self):
        """(README "Changes"): newest first, a section's own, read-only."""
        view, test, quiz = self.two_columns()
        alice, bob, carol = (self.rows[name] for name in ("Example, Alice", "Example, Bob", "Example, Carol"))
        first = self.scores([{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "80"},
                             {"item_id": quiz, "row": carol, "base": None, "status": "scored", "points": "9"}])
        self.scores([{"item_id": test, "row": alice, "base": first["cells"][test][alice]["id"], "status": "scored", "points": "85"}])
        self.scores([{"item_id": test, "row": bob, "base": None, "status": "excused", "note": "Absence: flu"}], request_id="excused-1")
        content = b"UIN,Name,Quiz 2\r\n001234567,Alice,8\r\n"
        options = json_text({"mapping": {"c2": {"action": "new", "title": "Quiz 2", "points": "10"}}})
        preview = self.import_file("/imports/preview", content, {"options": options})
        self.import_file("/imports", content, {"options": options, "fingerprint": preview["fingerprint"], "request_id": "file-1"})
        tables = ("gradebook_setups", "gradebook_requests", "gradebook_scores")
        with connect(self.app.config["DATABASE"]) as db:
            before = [db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables]

        page = self.changes(501)
        self.assertIsNone(page["next"])
        entries = page["changes"]
        self.assertEqual([(entry["type"], entry.get("origin")) for entry in entries],
                         [("request", "file"), ("request", "typed"), ("request", "typed"), ("request", "typed"), ("setup", None), ("setup", None)])
        imported, excused, changed, typed, second, first_setup = entries
        # The import made a column: its setup revision comes with it and is not listed again on its own.
        self.assertEqual((imported["summary"]["filename"], imported["setup"]), ("scores.csv", {"revision": 3, "changes": ["Added column “Quiz 2” (10 points, Uncategorized)"]}))
        self.assertEqual(imported["cells"], {"here": 1, "added": 1, "changed": 0, "cleared": 0, "elsewhere": 0})
        self.assertEqual(excused["detail"], [{"item_id": test, "row": bob, "seq": 1, "before": None,
                                              "after": {"status": "excused", "points": None, "note": "Absence: flu"}}])
        self.assertEqual(([(cell["before"]["points"], cell["after"]["points"]) for cell in changed["detail"]], changed["cells"]),
                         ([("80", "85")], {"here": 1, "added": 0, "changed": 1, "cleared": 0, "elsewhere": 0}))
        # A write across sections lists only this section's cells and counts the others.
        self.assertEqual((typed["cells"]["here"], typed["cells"]["elsewhere"], typed["items"], [cell["row"] for cell in typed["detail"]]), (1, 1, [test], [alice]))
        self.assertEqual((second["revision"], second["changes"]), (2, ["“Test 1”: category Uncategorized → Exams"]))
        self.assertEqual((first_setup["revision"], first_setup["changes"]),
                         (1, ["Added category “Exams”", "Added column “Test 1” (100 points, Uncategorized)",
                              "Added column “Quiz” (10 points, Uncategorized, section 502)"]))
        # Section 502 sees its own write, the setup and the import's new column (shared by every section), not 501's scores.
        other = self.changes(502)["changes"]
        self.assertEqual([(entry["type"], entry.get("origin")) for entry in other], [("request", "file"), ("request", "typed"), ("setup", None), ("setup", None)])
        self.assertEqual(((other[0]["cells"]["here"], other[0]["cells"]["elsewhere"]), [cell["row"] for cell in other[1]["detail"]]), ((0, 1), [carol]))
        self.assertEqual(len(self.changes()["changes"]), 6)  # the whole term
        # Pages follow each other without a gap or a repeat.
        with patch.object(gradebook_service, "CHANGES_PAGE", 4):
            newest = self.changes(501)
            older = self.changes(501, before=newest["next"])
        self.assertEqual([entry["id"] for entry in newest["changes"] + older["changes"]], [entry["id"] for entry in entries])
        self.assertIsNone(older["next"])
        # A request too large to list with its page is read when it is opened, the section's cells only.
        with patch.object(gradebook_service, "INLINE_CELLS", 0):
            self.assertIsNone(next(entry for entry in self.changes()["changes"] if entry["id"] == typed["id"])["detail"])
        cells = self.client.get(f"{self.base}/changes/{typed['id']}").json["cells"]
        self.assertEqual([(cell["row"], cell["after"]["points"]) for cell in cells], [(alice, "80"), (carol, "9")])
        scoped = self.client.get(f"{self.base}/changes/{typed['id']}", query_string={"section": self.sections[502]}).json["cells"]
        self.assertEqual([cell["row"] for cell in scoped], [carol])
        for path, query, status in (("/changes", {"before": "not-a-cursor"}, 400), ("/changes", {"section": "other"}, 400),
                                    ("/changes/missing", {}, 404), (f"/changes/{typed['id']}", {"section": "other"}, 400)):
            with self.subTest(path=path, query=query):
                self.assertEqual(self.client.get(self.base + path, query_string=query).status_code, status)
        self.assertEqual(self.client.get("/api/gradebook/terms/missing/changes").status_code, 404)
        text = json_text(self.changes()) + json_text(cells)
        for uin in UINS:
            self.assertNotIn(uin, text)
        with connect(self.app.config["DATABASE"]) as db:  # reading the changes records nothing
            self.assertEqual([db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables], before)

    def test_the_assistant_is_recorded_as_the_author_of_its_edits(self):
        """(request_actor.py)."""
        from assistant.transport import Transport
        assistant = Transport(self.app)
        view, test, quiz = self.two_columns()
        alice = self.rows["Example, Alice"]
        status, written, _ = assistant.request("POST", self.base + "/scores", {"request_id": "by-assistant", "changes": [
            {"item_id": test, "row": alice, "base": None, "status": "scored", "points": "70"}]})
        self.assertEqual(status, 200, written)
        # Only the actor changes: an entered value is still hand-entered (source instructor), which imports and pulls keep.
        self.assertEqual((written["cells"][test][alice]["actor"], written["cells"][test][alice]["source"]), ("assistant", "instructor"))
        status, saved, _ = assistant.request("POST", self.base + "/setup", {"base_revision": view["setup"]["revision"], "request_id": "setup-by-assistant",
                                                                              "setup": {"items": [*view["setup"]["items"], {"title": "Quiz 2", "points": "5"}]}})
        self.assertEqual((status, saved["setup"]["actor"]), (200, "assistant"))
        # The instructor's own writes stay the workspace's, and no request header can claim to be the assistant.
        mine = self.client.post(self.base + "/scores", headers={**HEADERS, "faculty.actor": "assistant", "X-Faculty-Actor": "assistant"},
                                json={"request_id": "mine", "changes": [{"item_id": test, "row": alice, "base": written["cells"][test][alice]["id"],
                                                                         "status": "scored", "points": "72"}]})
        self.assertEqual(mine.json["cells"][test][alice]["actor"], "workspace")
        history = self.client.get(self.base + "/history", query_string={"item": test, "row": alice}).json["revisions"]
        self.assertEqual([entry["actor"] for entry in history], ["workspace", "assistant"])
        self.assertEqual([(entry["type"], entry["actor"]) for entry in self.changes(501)["changes"]],
                         [("request", "workspace"), ("setup", "assistant"), ("request", "assistant"), ("setup", "workspace"), ("setup", "workspace")])

    def test_the_changes_page_lists_a_pull_that_wrote_nothing_only_where_it_was_started(self):
        assignment = self.client.post("/api/documents/assignments", json={"definition_id": self.definition, "key": "quiz-1", "title": "Quiz 1"},
                                      headers=HEADERS).json
        item = self.post("/items/link", {"assignment_id": assignment["id"], "request_id": str(uuid.uuid4())})["item_id"]
        options = {"replace": [], "absent_as_missing": False, "section_id": self.sections[502]}
        preview = self.post(f"/items/{item}/pull/preview", options)
        self.post(f"/items/{item}/pull", {**options, "fingerprint": preview["fingerprint"], "request_id": str(uuid.uuid4())})
        pulled, linked = self.changes(502)["changes"]
        self.assertEqual((pulled["origin"], pulled["cells"]["here"], pulled["summary"]["counts"]["none"], pulled["summary"]["item_id"]), ("pull", 0, 1, item))
        self.assertEqual(linked["changes"], ["Added column “Quiz 1” (100 points, Uncategorized, pulls “Quiz 1” (written work))"])
        self.assertEqual([entry["type"] for entry in self.changes(501)["changes"]], ["setup"])  # started on 502's tab, wrote nothing

    def test_export_csv_is_the_only_place_with_uins(self):
        view = self.save({"categories": [{"name": "=Exams", "weight": "100"}], "items": [{"title": "=SUM(A1)", "points": "100"}]})
        category, item = view["setup"]["categories"][0]["id"], view["setup"]["items"][0]["id"]
        view = self.save({"items": [{**view["setup"]["items"][0], "category_id": category}]}, base=1)
        self.scores([{"item_id": item, "row": self.rows["Example, Alice"], "base": None, "status": "scored", "points": "90"},
                     {"item_id": item, "row": self.rows["Example, Bob"], "base": None, "status": "excused"},
                     {"item_id": item, "row": self.rows["Example, Carol"], "base": None, "status": "missing"}])
        response = self.client.get(self.base + "/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("MATH 140 Fall 2026 gradebook.csv", response.headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Name", "UIN", "Section", "'=SUM(A1) (100)", "'=Exams %", "Overall %", "Letter"])
        self.assertEqual(rows[1], ["Example, Alice", "001234567", "501", "90", "90.00", "90.00", "A"])
        self.assertEqual(rows[2][:4], ["Example, Bob", "123456789", "501", "EX"])
        self.assertEqual(rows[3][:4], ["Example, Carol", "234567890", "502", "MI"])
        section = self.client.get(self.base + "/export.csv", query_string={"section": self.sections[502]})
        self.assertEqual([row[0] for row in csv.reader(io.StringIO(section.data.decode("utf-8-sig")))], ["Name", "Example, Carol"])
        self.assertEqual(self.client.get(self.base + "/export.csv", query_string={"section": "other"}).status_code, 400)
        # JSON never carries a UIN, and the generic workspace export carries no gradebook.
        text = self.client.get(self.base).get_data(as_text=True) + self.client.get("/api/gradebook").get_data(as_text=True)
        for uin in UINS:
            self.assertNotIn(uin, text)
        export = self.client.get("/api/export").get_data(as_text=True)
        self.assertNotIn("gradebook", export)
        self.assertNotIn("SUM(A1)", export)

    def test_linking_an_assignment_adds_one_linked_column(self):
        assignment = self.client.post("/api/documents/assignments", json={"definition_id": self.definition, "key": "test-1", "title": "Test 1"},
                                      headers=HEADERS).json
        request_id = str(uuid.uuid4())
        linked = self.post("/items/link", {"assignment_id": assignment["id"], "request_id": request_id})
        item = next(entry for entry in linked["setup"]["items"] if entry["id"] == linked["item_id"])
        self.assertEqual((item["title"], item["points"], item["assignment_id"], item["link"]),
                         ("Test 1", "100", assignment["id"], {"kind": "grading", "parts": ["frq"]}))
        self.assertEqual(linked["setup"]["revision"], 1)
        self.assertEqual(self.post("/items/link", {"assignment_id": assignment["id"], "request_id": request_id})["item_id"], linked["item_id"])
        again = self.post("/items/link", {"assignment_id": assignment["id"], "request_id": str(uuid.uuid4())})
        self.assertEqual((again["item_id"], again["setup"]["revision"]), (linked["item_id"], 1))
        self.post("/items/link", {"assignment_id": assignment["id"], "request_id": request_id, "category_id": "cat_x"}, 409)
        self.post("/items/link", {"assignment_id": "missing", "request_id": str(uuid.uuid4())}, 404)
        entry = next(entry for entry in again["assignments"] if entry["id"] == assignment["id"])
        self.assertEqual((entry["title"], entry["key"], entry["items"], entry["plan_id"]), ("Test 1", "test-1", [linked["item_id"]], None))
        self.assertEqual(again["links"][linked["item_id"]]["last_pull"], None)
        self.assertIsNone(again["links"][linked["item_id"]]["grading_changed_at"])  # no grading of it in this term: nothing changed
        # No grading of it in this term says what it is worth: the column starts at 100 points and nothing is suggested.
        self.assertEqual((entry["maximum"], again["links"][linked["item_id"]]["maximum"]), (None, None))

    def test_a_pull_is_previewed_then_applied_with_its_fingerprint(self):
        assignment = self.client.post("/api/documents/assignments", json={"definition_id": self.definition, "key": "quiz-1", "title": "Quiz 1"},
                                      headers=HEADERS).json
        item = self.post("/items/link", {"assignment_id": assignment["id"], "request_id": str(uuid.uuid4())})["item_id"]
        preview = self.post(f"/items/{item}/pull/preview", {"replace": [], "absent_as_missing": False})
        self.assertEqual(len(preview["rows"]), 3)
        self.assertTrue(all(row["action"] == "none" for row in preview["rows"]))  # no exam in this term: nothing to pull
        self.assertTrue(preview["warnings"])
        request_id = str(uuid.uuid4())
        self.post(f"/items/{item}/pull", {"replace": [], "absent_as_missing": False, "fingerprint": "0" * 64, "request_id": request_id}, 409)
        body = {"replace": [], "absent_as_missing": False, "fingerprint": preview["fingerprint"], "request_id": request_id}
        view = self.post(f"/items/{item}/pull", body)
        self.assertEqual(view["links"][item]["last_pull"]["counts"]["none"], 3)
        self.assertEqual(self.post(f"/items/{item}/pull", body)["requests"], view["requests"])  # a replay records nothing more
        self.post(f"/items/{item}/pull/preview", {"absent_as_missing": "yes"}, 400)
        plain = self.save({"items": [*view["setup"]["items"], {"title": "Participation", "points": "5"}]}, base=view["setup"]["revision"])
        other = next(entry["id"] for entry in plain["setup"]["items"] if entry["title"] == "Participation")
        self.post(f"/items/{other}/pull/preview", {}, 400)
        self.post("/items/item_missing/pull/preview", {}, 404)

    def import_file(self, path, content, fields, status=200):
        response = self.client.post(self.base + path, data={"file": (io.BytesIO(content), "scores.csv"), **fields}, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.json)
        return response.json

    def test_an_import_previews_then_applies_with_a_new_column(self):
        view = self.save({"items": [{"title": "Test 1", "points": "100"}]})
        test = view["setup"]["items"][0]["id"]
        content = b"UIN,Name,Test 1,Quiz 2\r\n001234567,Alice,90,8\r\n123456789,Bob,EX,\r\n999999999,Nobody,5,5\r\n"
        suggested = self.import_file("/imports/preview", content, {})
        keys = {column["title"]: column["key"] for column in suggested["columns"]}
        self.assertEqual(suggested["unmatched"][0]["name"], "Nobody")
        options = {"mapping": {keys["Test 1"]: {"action": "item", "item_id": test},
                               keys["Quiz 2"]: {"action": "new", "title": "Quiz 2", "points": "10"}}, "replace": [], "blank_as_missing": False}
        preview = self.import_file("/imports/preview", content, {"options": json_text(options)})
        self.assertTrue(preview["ready"])
        text = json_text(preview)
        for uin in (*UINS, "999999999"):
            self.assertNotIn(uin, text)
        fields = {"options": json_text(options), "fingerprint": "0" * 64, "request_id": str(uuid.uuid4())}
        self.import_file("/imports", content, fields, 409)
        fields["fingerprint"] = preview["fingerprint"]
        applied = self.import_file("/imports", content, fields)
        self.assertEqual(applied["setup"]["revision"], 2)
        quiz = next(item["id"] for item in applied["setup"]["items"] if item["title"] == "Quiz 2")
        alice, bob = self.rows["Example, Alice"], self.rows["Example, Bob"]
        self.assertEqual((applied["cells"][test][alice]["points"], applied["cells"][test][alice]["source"]), ("90", "import"))
        self.assertEqual(applied["cells"][test][bob]["status"], "excused")
        self.assertEqual(applied["cells"][quiz][alice]["points"], "8")
        request = applied["requests"][0]
        self.assertEqual((request["kind"], request["summary"]["columns"]), ("import", {keys["Test 1"]: test, keys["Quiz 2"]: quiz}))
        # The same request again returns the view; nothing is written twice.
        self.assertEqual(self.import_file("/imports", content, fields)["setup"]["revision"], 2)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_scores").fetchone()[0], 3)
        self.import_file("/imports/preview", content, {"options": "{", "fingerprint": "x"}, 400)  # an unexpected field
        self.import_file("/imports/preview", content, {"options": "{"}, 400)

    def test_a_cell_cleared_by_hand_is_not_refilled_by_an_import(self):
        # A grade the instructor took out stays out until they tick Replace for that student (common.change_action).
        view = self.save({"items": [{"title": "Test 1", "points": "100"}]})
        test = view["setup"]["items"][0]["id"]
        alice = self.rows["Example, Alice"]
        typed = self.scores([{"item_id": test, "row": alice, "base": None, "status": "scored", "points": "70"}])
        self.scores([{"item_id": test, "row": alice, "base": typed["cells"][test][alice]["id"], "status": "cleared"}])
        content = b"UIN,Test 1\r\n001234567,90\r\n"
        mapping = {"c1": {"action": "item", "item_id": test}}
        options = {"mapping": mapping, "replace": [], "blank_as_missing": False}
        preview = self.import_file("/imports/preview", content, {"options": json_text(options)})
        change = next(change for change in preview["changes"] if change["row"] == alice)
        self.assertEqual((change["action"], change["current"]["status"]), ("kept", "cleared"))
        ticked = {**options, "replace": [alice]}
        preview = self.import_file("/imports/preview", content, {"options": json_text(ticked)})
        self.assertEqual(next(change["action"] for change in preview["changes"] if change["row"] == alice), "add")
        applied = self.import_file("/imports", content, {"options": json_text(ticked), "fingerprint": preview["fingerprint"],
                                                         "request_id": str(uuid.uuid4())})
        self.assertEqual((applied["cells"][test][alice]["points"], applied["cells"][test][alice]["source"]), ("90", "import"))

    def test_offering_edits_cannot_detach_a_gradebook(self):
        other = self.client.post("/api/courses", json={"name": "Physics", "code": "PHYS 201", "term": "Fall", "year": 2026,
                                                        "sections": [501], "is_current": True}, headers=HEADERS).json
        term = next(term for term in self.client.get("/api/gradebook").json["terms"] if term["code"] == "PHYS 201")
        response = self.client.post(f"/api/gradebook/terms/{term['id']}/setup", json={"base_revision": None, "request_id": "r",
                                    "setup": {"items": [{"title": "Lab 1", "points": "10"}]}}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        moved = self.client.patch(f"/api/courses/{other['id']}", json={"version": other["version"], "term": "Spring"}, headers=HEADERS)
        self.assertEqual(moved.status_code, 409)
        self.assertIn("gradebook", moved.json["error"])
        # Scores keep their section number: an offering without section 502 no longer covers them.
        view, test, quiz = self.two_columns()
        self.scores([{"item_id": quiz, "row": self.rows["Example, Carol"], "base": None, "status": "scored", "points": "9"}])
        with connect(self.app.config["DATABASE"]) as db:
            previous = dict(db.execute("SELECT * FROM courses WHERE id=?", (self.course["id"],)).fetchone())
            previous["sections"] = [501, 502]
            with self.assertRaisesRegex(Exception, "detach a gradebook"):
                gradebook_service.protect_references(db, previous, {**previous, "sections": [501]})
            gradebook_service.protect_references(db, previous, {**previous, "name": "Renamed"})

    def test_migration_ledger_and_calendar_deadline(self):
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT version,adoption FROM schema_migrations WHERE namespace='gradebook'")],
                             [("001_gradebook", "applied"), ("002_canvas_links", "applied"), ("003_canvas_sends", "applied"),
                              ("004_iclicker_links", "applied"), ("005_canvas_created", "applied"),
                              ("006_iclicker_sessions", "applied"), ("007_nightly_runs", "applied"), ("008_iclicker_runs", "applied"),
                              ("009_canvas_posts", "applied"), ("010_canvas_published", "applied"), ("011_canvas_snapshots", "applied"), ("012_canvas_posting_policy", "applied"), ("013_canvas_nightly_runs", "applied"), ("014_iclicker_credits", "applied")])
        self.save({"items": [{"title": "Homework 1", "points": "20", "due_date": "2026-10-01", "sections": [self.sections[502]]},
                             {"title": "Old", "points": "5", "due_date": "2026-10-02", "archived": True},
                             {"title": "Later", "points": "5", "due_date": "2026-12-02"}]})
        events = self.client.get("/api/calendar", query_string={"start": "2026-09-01", "end": "2026-10-31"}).json["events"]
        mine = [event for event in events if event["source"] == "gradebook"]
        self.assertEqual(len(mine), 1)
        event = mine[0]
        self.assertEqual((event["title"], event["date"], event["category"], event["sections"], event["editable"], event["term_label"]),
                         ("Homework 1 due", "2026-10-01", "deadline", [502], False, "Fall 2026"))
        self.assertTrue(event["href"].startswith(f"#gradebook/{self.term['id']}?item=item_"))


class ManagedGradebookTests(unittest.TestCase):
    """Names come from the verified term payload through the storage manager; accommodations are shown, never changed."""

    def test_names_and_accommodations_through_managed_storage(self):
        from test_accommodations import workbook
        from test_dr_exams import booking, workbook as dr_workbook
        from test_storage import temporary_storage
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        storage = temporary_storage(temp.name)
        app = create_app(storage=storage)
        client = app.test_client()
        created = client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [507, 508],
                                                     "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        storage.reconcile_all()
        group = client.get("/api/state").json["course_groups"][0]["id"]
        for number, rows in ((507, STUDENTS), (508, [CAROL])):
            self.assertEqual(client.post(f"/api/course-groups/{group}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")},
                                         headers=HEADERS).status_code, 201)

        def imported(path, content):
            preview = client.post(f"{path}/preview", data={"file": (io.BytesIO(content), "export.xlsx"), "term": "null"}, headers=HEADERS).json
            queued = client.post(f"{path}/import", data={"file": (io.BytesIO(content), "export.xlsx"), "term": "null",
                                 "preview_hash": preview["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
            self.assertEqual(queued.status_code, 202, queued.json)
            self.assertTrue(app.extensions["inbox"].process_one())

        # Accommodations and DR bookings in both sections: the View reads each section's, not only the first's.
        imported("/api/accommodations", workbook([["Example", "Alice", "001234567", "a@example.test", "MATH", "140", "507", "Read", "2026-09-13", "Yes", "Yes", ""],
                                                   ["Example", "Carol", "234567890", "c@example.test", "MATH", "140", "508", "Read", "2026-09-13", "Yes", "", ""]]))
        imported("/api/accommodations/exams", dr_workbook([booking(UIN="123456789", **{"Last Name": "Example", "First Name": "Bob"}),
                                                           booking(UIN="234567890", Section="508", **{"Last Name": "Example", "First Name": "Carol"})]))
        exam = next(item["id"] for item in client.get("/api/seating/catalog").json["assignments"] if item["title"] == "Exam 1")  # made for the bookings
        term = client.get("/api/gradebook").json["terms"][0]
        base = f"/api/gradebook/terms/{term['id']}"
        view = client.get(base).json
        self.assertEqual([(student["name"], student["section"], student["accommodations"], student["dr"]) for student in view["students"]],
                         [("Example, Alice", 507, ["Extra Time 1.50x", "Reduced Distraction Environment"], []), ("Example, Bob", 507, [], [exam]),
                          ("Example, Carol", 508, ["Extra Time 1.50x"], [exam])])
        for section in view["sections"]:
            scoped = client.get(f"{base}?section={section['id']}").json
            self.assertEqual(scoped["students"], [student for student in view["students"] if student["section_id"] == section["id"]])
        item = client.post(f"{base}/items/link", json={"assignment_id": exam, "request_id": str(uuid.uuid4())}, headers=HEADERS).json["item_id"]
        # Only the View shows accommodations: the export and a pull's preview (like the writes and imports sharing its context) do not read them.
        with patch.object(app.extensions["accommodations"], "attach", side_effect=AssertionError("accommodations read outside the View")), \
                patch.object(app.extensions["dr_exams"], "attach", side_effect=AssertionError("DR bookings read outside the View")):
            exported = client.get(f"{base}/export.csv").data.decode("utf-8-sig")
            preview = client.post(f"{base}/items/{item}/pull/preview", json={"replace": [], "absent_as_missing": False}, headers=HEADERS)
        self.assertIn('"Example, Alice",001234567,507', exported)
        self.assertEqual((preview.status_code, len(preview.json["rows"])), (200, 3))


class ChangeWordsTests(unittest.TestCase):
    """The Changes page's words (gradebook/changes.py): what made a request and what a setup revision changed."""

    def test_a_request_is_named_by_what_made_it(self):
        origin = gradebook_changes.origin
        self.assertEqual([origin("scores", "r1", {}), origin("scores", "absence:a:fill:1", {"source": "absence", "action": "fill"}),
                          origin("import", "r1", {"filename": "a.csv"}),
                          origin("import", "r1", {"source": "canvas"}), origin("import", "r1", {"source": "iclicker"}), origin("pull", "r1", {}),
                          origin("pull", "certify:r1", {"source": "certification"}), origin("pull", "r1", {"source": "certification"})],
                         ["typed", "absence", "file", "canvas", "iclicker", "pull", "certification", "pull"])

    def test_a_setup_revision_says_what_it_changed(self):
        def item(identifier, title, points, category, **extra):
            return {"id": identifier, "title": title, "category_id": category, "points": points, "due_date": "", "counts": True,
                    "extra_credit": False, "archived": False, "sections": [], "assignment_id": None, "link": None, "notes": "", **extra}
        before = {**default_setup(), "mode": "weighted", "ungraded": "exclude",
                  "categories": [{"id": "c1", "name": "Exams", "weight": "50", "drop_lowest": 0, "aggregate": "points"},
                                 {"id": "c2", "name": "Labs", "weight": "10", "drop_lowest": 0, "aggregate": "points"}],
                  "items": [item("i1", "Test 1", "100", "c1", due_date="2026-10-01"), item("i2", "Quiz", "10", "c1"), item("i3", "Lab", "5", "c2")]}
        after = {**before, "ungraded": "zero", "letter_rounding": 0,
                 "categories": [{"id": "c1", "name": "Exams", "weight": "60", "drop_lowest": 1, "aggregate": "points"},
                                {"id": "c3", "name": "Quizzes", "weight": "20", "drop_lowest": 0, "aggregate": "equal"}],
                 "items": [item("i2", "Quiz 1", "10", "c3"), item("i1", "Test 1", "110", "c1", due_date="2026-10-08", archived=True, sections=["s2"]),
                           item("i4", "Bonus", "0", None, extra_credit=True)],
                 "scale": [{"letter": letter, "min": "79" if letter == "B" else minimum} for letter, minimum in SCALE]}
        self.assertEqual(gradebook_changes.setup_changes(before, after, sections={"s2": 502}), [
            "Empty cells: empty cells left out → empty cells counted as zero",
            "Letters decided by the percentage rounded to a whole percent (was the exact percentage)",
            "“Exams”: weight 50 → 60; drops the lowest 0 → 1",
            "Added category “Quizzes” (weight 20, equal weight per column)",
            "Removed category “Labs”",
            "Renamed column “Quiz” to “Quiz 1”",
            "“Quiz 1”: category Exams → Quizzes",
            "“Test 1”: points 100 → 110; due Oct 1, 2026 → Oct 8, 2026; now for section 502; archived",
            "Added column “Bonus” (0 points, Uncategorized, extra credit)",
            "Removed column “Lab”",
            "Columns reordered",
            "Grading scheme: B 80 → 79"])
        self.assertEqual(gradebook_changes.setup_changes(after, after), [])
        # ZERO ONCE DUE: its words, and a first revision compared with the default in force when it was saved.
        self.assertEqual(gradebook_changes.setup_changes(before, {**before, "ungraded": "due"})[0],
                         "Empty cells: empty cells left out → empty cells counted as zero once due")
        first = {**default_setup(), "ungraded": "exclude"}
        self.assertEqual(gradebook_changes.setup_changes(None, first, made_at="2026-09-27T23:49:34.501919+00:00"), [])
        self.assertEqual(gradebook_changes.setup_changes(None, first, made_at="2026-10-07T01:00:00.000000+00:00"),
                         ["Empty cells: empty cells counted as zero once due → empty cells left out"])


def json_text(value):
    import json
    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
