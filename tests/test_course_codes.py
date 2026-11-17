"""Course definitions associate offerings by code without merging their data."""

import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import create_app

HEADERS = {"X-Teaching-Request": "1"}


class CourseCodeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()

    def add(self, **overrides):
        return self.client.post("/api/courses", json={
            "name": "Calculus I", "code_prefix": "MATH", "code_number": "101",
            "term": "Fall", "year": 2026, "sections": [1], **overrides,
        }, headers=HEADERS)

    def state(self):
        return self.client.get("/api/state").json

    def test_same_code_has_one_definition_across_terms_sections_names_and_current_flags(self):
        first = self.add().json
        second = self.add(code_prefix=" math ", name="Calculus, new title", term="Spring", year=2027,
                          sections=[2, 3], multiple_sections=True, is_current=False).json
        self.assertEqual(first["code"], "MATH 101")
        self.assertEqual(first["definition_id"], second["definition_id"])
        self.assertNotEqual(first["id"], second["id"])
        different = self.add(code_number="102").json
        self.assertNotEqual(first["definition_id"], different["definition_id"])
        other_prefix = self.add(code_prefix="PHYS").json
        self.assertNotEqual(first["definition_id"], other_prefix["definition_id"])
        self.assertEqual(len(self.state()["courses"]), 4)
        self.assertEqual(len(self.state()["course_definitions"]), 3)
        restarted = create_app(self.directory.name).test_client().get("/api/state").json
        self.assertEqual(restarted, self.state())

    def test_concurrent_offerings_resolve_the_same_definition(self):
        def save(section):
            with self.app.test_client() as client:
                response = client.post("/api/courses", json={"name": "Calculus", "code_prefix": "MATH",
                    "code_number": "101", "term": "Fall", "year": 2026, "sections": [section]}, headers=HEADERS)
                self.assertEqual(response.status_code, 201)
                return response.json
        with ThreadPoolExecutor(max_workers=2) as pool:
            courses = list(pool.map(save, (1, 2)))
        self.assertEqual(courses[0]["definition_id"], courses[1]["definition_id"])
        self.assertEqual(len(self.state()["course_definitions"]), 1)

    def test_prefixes_and_shared_identity_survive_removal_of_every_offering(self):
        self.assertEqual(self.state()["course_prefixes"], ["MATH"])
        saved = self.add(code_prefix="phys", code_number="0201").json
        self.assertEqual(saved["code"], "PHYS 0201")
        self.assertEqual(saved["code_number"], "0201")
        self.client.delete(f"/api/courses/{saved['id']}", json={"version": saved["version"]}, headers=HEADERS)
        state = create_app(self.directory.name).test_client().get("/api/state").json
        self.assertEqual(state["courses"], [])
        self.assertEqual(state["course_prefixes"], ["MATH", "PHYS"])
        repeated = self.add(code_prefix="PHYS", code_number="0201").json
        self.assertEqual(repeated["definition_id"], saved["definition_id"])
        export = self.client.get("/api/export").json
        self.assertEqual(export["course_definitions"], self.state()["course_definitions"])

    def test_changing_code_relinks_only_the_edited_offering(self):
        first, second = self.add().json, self.add(year=2027).json
        changed = self.client.patch(f"/api/courses/{first['id']}", json={
            "version": 1, "code_number": "102", "definition_id": "untrusted-client-id",
        }, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(changed.json["code"], "MATH 102")
        self.assertNotEqual(changed.json["definition_id"], first["definition_id"])
        self.assertNotEqual(changed.json["definition_id"], "untrusted-client-id")
        unchanged = next(item for item in self.state()["courses"] if item["id"] == second["id"])
        self.assertEqual(unchanged, second)
        # Existing combined-code clients can also relink an offering.
        back = self.client.patch(f"/api/courses/{first['id']}", json={"version": 2, "code": "math101"}, headers=HEADERS)
        self.assertEqual(back.status_code, 200, back.json)
        self.assertEqual(back.json["definition_id"], first["definition_id"])

    def test_required_components_and_strict_numeric_suffix_validation(self):
        before = self.state()
        for values in ({"code_prefix": ""}, {"code_prefix": None}, {"code_prefix": "M4TH"},
                       {"code_prefix": "../../"}, {"code_prefix": "A" * 21},
                       {"code_number": ""}, {"code_number": None}, {"code_number": True},
                       {"code_number": 1.5}, {"code_number": "1e3"}, {"code_number": "+101"},
                       {"code_number": "1.0"}, {"code_number": "-101"}, {"code_number": "101A"},
                       {"code_number": "1" * 20}, {"code": "PHYS 101"}):
            with self.subTest(values=values):
                self.assertEqual(self.add(**values).status_code, 400)
        for missing in ("code_prefix", "code_number"):
            data = {"name": "Calculus", "code_prefix": "MATH", "code_number": "101", "term": "Fall", "year": 2026, "sections": [1]}
            del data[missing]
            self.assertEqual(self.client.post("/api/courses", json=data, headers=HEADERS).status_code, 400)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.add(code_number=101).status_code, 201)
        self.assertEqual(self.add(code_number="0").status_code, 201)

    def test_legacy_codes_migrate_without_merging_offerings_or_guessing_unrecognized_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "faculty.sqlite3"
            connection = sqlite3.connect(path)
            with connection as db:
                db.execute("""CREATE TABLE courses (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    code TEXT NOT NULL, term TEXT NOT NULL, is_current INTEGER NOT NULL,
                    version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                identifiers = []
                for code in (" math101 ", "MATH-101", "MATH 0101", "BIO 101A", ""):
                    identifier = str(uuid.uuid4())
                    identifiers.append(identifier)
                    db.execute("INSERT INTO courses VALUES (?, 'Existing course', ?, 'Fall 2026', 1, 4, 'created', 'updated')", (identifier, code))
            connection.close()
            migrated = create_app(directory).test_client().get("/api/state").json
            courses = {item["id"]: item for item in migrated["courses"]}
            self.assertEqual(set(courses), set(identifiers))
            self.assertEqual(courses[identifiers[0]]["definition_id"], courses[identifiers[1]]["definition_id"])
            self.assertEqual(courses[identifiers[0]]["code"], "MATH 101")
            self.assertEqual(courses[identifiers[0]]["version"], 6)
            self.assertNotEqual(courses[identifiers[0]]["definition_id"], courses[identifiers[2]]["definition_id"])
            self.assertEqual(courses[identifiers[3]]["code"], "BIO 101A")
            self.assertIsNone(courses[identifiers[3]]["definition_id"])
            self.assertEqual(courses[identifiers[3]]["code_number"], "")
            self.assertIsNone(courses[identifiers[4]]["definition_id"])
            self.assertEqual(create_app(directory).test_client().get("/api/state").json, migrated)


if __name__ == "__main__":
    unittest.main()
