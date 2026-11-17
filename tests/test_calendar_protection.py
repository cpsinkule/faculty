"""Course edits retain visible coverage for each active custom calendar owner."""
import tempfile
import unittest
import uuid

from app import create_app
from rosters import connect
from test_storage import HEADERS, temporary_storage


class CalendarProtectionTests(unittest.TestCase):
    managed = False

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.storage = temporary_storage(self.temporary.name) if self.managed else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.temporary.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.service = self.app.extensions["calendar"]
        self.course = self.add_course()
        catalog = self.service.catalog()
        self.term = catalog["terms"][0]
        self.section = next(row for row in catalog["sections"] if row["number"] == 501)

    def add_course(self, **changes):
        response = self.client.post("/api/courses", json={"name": "Calendar course", "code": "MATH 140", "term": "Fall",
            "year": 2026, "sections": [501, 502], "multiple_sections": True, "is_current": True, **changes}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        if self.storage:
            self.storage.reconcile_all()
        return response.json

    def event(self, scope):
        data = {"request_id": str(uuid.uuid4()), "title": "Instructor event", "date": "2026-09-23", "scope": scope}
        if scope != "workspace":
            data["definition_id"] = self.term["definition_id"]
        if scope in ("term", "section"):
            data["term_id"] = self.term["id"]
        if scope == "section":
            data["section_id"] = self.section["id"]
        return self.service.create(data)

    def mutate(self, course=None, changes=None, *, status=200):
        course = course or self.course
        current = next(row for row in self.client.get("/api/state?include=core").json["courses"] if row["id"] == course["id"])
        response = self.client.open("/api/courses/" + current["id"], method="PATCH" if changes is not None else "DELETE",
                                    json={"version": current["version"], **(changes or {})}, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        if status == 409:
            self.assertIn("calendar event", response.json["error"])
        if self.storage and status in (200, 204):
            self.storage.reconcile_all()
        return response

    def assert_retained(self, event):
        saved = self.service.get(event["id"])
        for key in ("definition_id", "term_id", "section_id", "version", "date"):
            self.assertEqual(saved[key], event[key])

    def test_course_event_protects_code_and_final_delete_but_not_term_or_section(self):
        event = self.event("course")
        self.mutate(changes={"code": "MATH 151"}, status=409)
        self.mutate(status=409)
        self.mutate(changes={"year": 2027, "term": "Spring", "sections": [601], "multiple_sections": False})
        self.assert_retained(event)
        # A rejected code edit must roll back the definition reservation too.
        self.assertNotIn("MATH 151", [row["code"] for row in self.service.catalog()["courses"]])

    def test_term_event_protects_term_identity_but_not_unowned_sections(self):
        event = self.event("term")
        for changes in ({"year": 2027}, {"term": "Spring"}, {"code": "MATH 151"}):
            self.mutate(changes=changes, status=409)
        self.mutate(status=409)
        self.mutate(changes={"sections": [502], "multiple_sections": False, "name": "New display name"})
        self.assert_retained(event)

    def test_section_event_preserves_exact_section_and_parent_identity(self):
        event = self.event("section")
        for changes in ({"sections": [502], "multiple_sections": False}, {"year": 2027}, {"code": "MATH 151"}):
            self.mutate(changes=changes, status=409)
        self.mutate(status=409)
        self.mutate(changes={"sections": [501], "multiple_sections": False})
        self.assert_retained(event)

    def test_overlap_retains_coverage_until_the_final_matching_offering(self):
        event = self.event("section")
        duplicate = self.add_course(sections=[501], multiple_sections=False)
        self.mutate(status=204)
        self.assert_retained(event)
        self.mutate(duplicate, changes={"sections": [503]}, status=409)
        self.mutate(duplicate, status=409)

    def test_same_number_in_other_course_term_or_incomplete_overlap_does_not_cover(self):
        event = self.event("section")
        self.add_course(code="MATH 151", sections=[501], multiple_sections=False)
        self.add_course(term="Spring", sections=[501], multiple_sections=False)
        self.add_course(sections=[502], multiple_sections=False)
        self.mutate(changes={"sections": [502], "multiple_sections": False}, status=409)
        self.mutate(status=409)
        self.assert_retained(event)

    def test_archive_and_metadata_changes_preserve_event_owners(self):
        event = self.event("section")
        self.mutate(changes={"is_current": False, "name": "Archived course", "crn": "12345"})
        self.assert_retained(event)
        self.assertEqual(len(self.service.list("2026-09-01", "2026-09-30")), 1)
        self.mutate(changes={"is_current": True})
        self.assert_retained(event)

    def test_workspace_event_does_not_restrict_course_identity_or_deletion(self):
        event = self.event("workspace")
        self.mutate(changes={"code": "MATH 151", "year": 2027, "term": "Spring", "sections": [601], "multiple_sections": False})
        self.mutate(status=204)
        self.assert_retained(event)

    def test_soft_deleted_calendar_event_does_not_hold_course_visible(self):
        event = self.event("section")
        self.service.delete(event["id"], {"version": event["version"]})
        self.mutate(status=204)
        with connect(self.service.path) as db:
            saved = db.execute("SELECT * FROM calendar_events WHERE id=?", (event["id"],)).fetchone()
        self.assertTrue(saved["deleted_at"])
        self.assertEqual(saved["section_id"], event["section_id"])

    def test_explicit_event_scope_change_allows_offering_identity_change(self):
        event = self.event("section")
        saved = self.service.update(event["id"], {"version": event["version"], "scope": "workspace",
                                    "definition_id": None, "term_id": None, "section_id": None})
        self.mutate(changes={"code": "MATH 151", "year": 2027, "sections": [601], "multiple_sections": False})
        self.assert_retained(saved)

    def test_custom_term_name_is_part_of_scope_coverage(self):
        self.mutate(changes={"term": "Other", "term_other": "Intensive A"})
        self.term = next(row for row in self.service.catalog()["terms"] if row["term_label"] == "Intensive A 2026")
        self.section = next(row for row in self.service.catalog()["sections"] if row["term_id"] == self.term["id"] and row["number"] == 501)
        event = self.event("term")
        self.add_course(term="Other", term_other="Intensive B")
        self.mutate(changes={"term_other": "Intensive B"}, status=409)
        self.assert_retained(event)


class CalendarManagedProtectionTests(CalendarProtectionTests):
    managed = True
