"""Course homes group offerings without changing their identities or data."""

import copy
import tempfile
import unittest
import uuid

from app import create_app
from course_groups import group_courses

HEADERS = {"X-Teaching-Request": "1"}


def offering(**overrides):
    return {
        "id": str(uuid.uuid4()), "definition_id": "shared-definition", "name": "Calculus I",
        "code": "MATH 140", "term": "Fall", "term_other": "", "year": 2026,
        "is_current": True, "sections": [1], "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00", "deleted_at": None,
        "storage_status": "active", "storage_snapshot": "offering-specific-snapshot",
        **overrides,
    }


class CourseGroupTests(unittest.TestCase):
    def test_multi_section_offering_is_one_course_home(self):
        record = offering(sections=[4, 1, 3, 2])
        groups = group_courses([record])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["sections"], [1, 2, 3, 4])
        self.assertEqual(groups[0]["current_sections"], [1, 2, 3, 4])
        self.assertEqual(groups[0]["offerings"], [record])
        self.assertTrue(groups[0]["is_current"])
        self.assertTrue(groups[0]["all_current"])

    def test_separate_offerings_combine_sections_and_use_latest_name(self):
        first = offering(sections=[2, 4])
        second = offering(code="math-140", sections=[1, 2, 3], name="Calculus, revised title",
                          created_at="2026-02-01T00:00:00+00:00", updated_at="2026-02-02T00:00:00+00:00")
        groups = group_courses([first, second])
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["code"], "MATH 140")
        self.assertEqual(group["name"], second["name"])
        self.assertEqual(group["created_at"], first["created_at"])
        self.assertEqual(group["updated_at"], second["updated_at"])
        self.assertEqual(group["sections"], [1, 2, 3, 4])
        self.assertEqual(group["offerings"], [second, first])

    def test_terms_years_codes_and_custom_terms_are_distinct(self):
        records = [offering(), offering(term="Spring"), offering(year=2027),
                   offering(code="MATH 141"), offering(term="Other", term_other="Winter"),
                   offering(term="Other", term_other="May Session")]
        groups = group_courses(records)
        self.assertEqual(len(groups), 6)
        self.assertEqual(len({group["id"] for group in groups}), 6)
        # Stale custom text has no bearing on a standard term's identity.
        self.assertEqual(group_courses([records[0], offering(term_other="Winter")])[0]["sections"], [1])
        self.assertEqual(len(group_courses([records[0], offering(term_other="Winter")])), 1)

    def test_mixed_current_and_deleted_offerings(self):
        current = offering(sections=[1, 2])
        archived = offering(sections=[2, 3], is_current=False, storage_status="archived")
        deleted = offering(sections=[4], deleted_at="2026-03-01T00:00:00+00:00")
        group, = group_courses([current, archived, deleted])
        self.assertTrue(group["is_current"])
        self.assertFalse(group["all_current"])
        self.assertEqual(group["sections"], [1, 2, 3])
        self.assertEqual(group["current_sections"], [1, 2])
        self.assertEqual(len(group["offerings"]), 2)
        inactive, = group_courses([archived])
        self.assertFalse(inactive["is_current"])
        self.assertFalse(inactive["all_current"])
        self.assertEqual(inactive["current_sections"], [])
        self.assertEqual(group_courses([deleted]), [])

    def test_identity_is_stable_across_order_new_sections_and_metadata_changes(self):
        first, second = offering(), offering(sections=[2])
        baseline = group_courses([first, second])
        self.assertEqual(baseline, group_courses([second, first]))
        identifier = baseline[0]["id"]
        self.assertEqual(str(uuid.UUID(identifier)), identifier)
        self.assertEqual(group_courses([first])[0]["id"], identifier)
        changed = {**first, "name": "New title", "is_current": False, "sections": [7]}
        self.assertEqual(group_courses([changed])[0]["id"], identifier)
        self.assertNotEqual(group_courses([{**first, "term": "Spring"}])[0]["id"], identifier)

    def test_incomplete_legacy_records_remain_separate(self):
        for missing in ({"code": ""}, {"term": ""}, {"year": None}, {"term": "Other", "term_other": ""}):
            with self.subTest(missing=missing):
                records = [offering(**missing), offering(**missing)]
                groups = group_courses(records)
                self.assertEqual({group["id"] for group in groups}, {item["id"] for item in records})

    def test_projection_preserves_original_records_and_storage_references(self):
        records = [offering(sections=[1, 2]), offering(sections=[3], is_current=False)]
        original = copy.deepcopy(records)
        group, = group_courses(records)
        self.assertEqual(records, original)
        self.assertEqual({item["id"] for item in group["offerings"]}, {item["id"] for item in records})
        self.assertTrue(all(item["storage_snapshot"] == "offering-specific-snapshot" for item in group["offerings"]))
        group["offerings"][0]["sections"].append(99)
        self.assertEqual(records, original)

    def test_api_state_export_and_reload_include_groups_without_merging_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(directory).test_client()
            records = []
            for term, sections, current in (("Fall", [1, 2], True), ("Fall", [3, 4], False), ("Spring", [1], False)):
                response = client.post("/api/courses", json={
                    "name": "Calculus I", "code_prefix": "MATH", "code_number": "140",
                    "term": term, "year": 2026, "sections": sections,
                    "multiple_sections": len(sections) > 1, "is_current": current,
                }, headers=HEADERS)
                self.assertEqual(response.status_code, 201, response.json)
                records.append(response.json)
            state = client.get("/api/state").json
            self.assertEqual(len(state["courses"]), 3)
            self.assertEqual(len(state["course_groups"]), 2)
            self.assertEqual(sorted(state["courses"], key=lambda item: item["id"]), sorted(records, key=lambda item: item["id"]))
            fall = next(group for group in state["course_groups"] if group["term"] == "Fall")
            self.assertEqual(fall["sections"], [1, 2, 3, 4])
            self.assertEqual(fall["current_sections"], [1, 2])
            self.assertTrue(fall["is_current"])
            self.assertEqual(client.get("/api/export").json["course_groups"], state["course_groups"])
            self.assertEqual(create_app(directory).test_client().get("/api/state").json, state)


if __name__ == "__main__":
    unittest.main()
