"""Source notes, physical geometry, and explicit room-review lifecycle."""
import copy
import hashlib
import io
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

from werkzeug.exceptions import Conflict

from app import create_app
from room_import import parse_seating_chart
from room_sources import apply_issues, issue_document, validate_layout
from tests.test_rooms import chart_bytes


def docx_bytes(paragraphs=(), xml=None):
    if xml is None:
        body = "".join(f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs)
        xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return result.getvalue()


class RoomSourceTests(unittest.TestCase):
    def test_document_extraction_retains_exact_source_and_digest(self):
        content = docx_bytes(["  ILCB 111 Seat Issues ", "", "A1 missing desk", "Several chairs missing; position unknown"])
        result = issue_document(content, "ILCB 111.docx")
        self.assertEqual(result["paragraphs"], ["ILCB 111 Seat Issues", "A1 missing desk", "Several chairs missing; position unknown"])
        self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["filename"], "ILCB 111.docx")

    def test_document_limits_and_entity_rejection_are_upload_errors(self):
        entity = ('<!DOCTYPE w:document [<!ENTITY nope "unsafe">]>'
                  '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:p><w:r><w:t>&nope;</w:t></w:r></w:p></w:document>')
        for content, filename in ((b"not a zip", "notes.docx"), (b"", "notes.docx"),
                                  (docx_bytes(["note"]), "notes.pdf"), (docx_bytes(xml=entity), "notes.docx"),
                                  (docx_bytes(["note"] * 501), "notes.docx"), (docx_bytes(xml="<bad>"), "notes.docx")):
            with self.subTest(filename=filename, size=len(content)), self.assertRaises(ValueError):
                issue_document(content, filename)

    def test_only_exact_unusable_seat_notes_change_conditions(self):
        chart = parse_seating_chart(chart_bytes({"A1": "A1", "B1": "A2", "C1": "A3"}), "room.xlsx")
        source = issue_document(docx_bytes(["ILCB 111 Seat Issues", "A1 missing desk", "A2 chair and desk missing",
                                          "A3 number should be A4", "Two chairs missing in back"]), "issues.docx")
        apply_issues(chart, source, "ILCB 111")
        self.assertEqual([s["label"] for s in chart["seats"] if s["broken"]], ["A1", "A2"])
        self.assertEqual(chart["seats"][0]["notes"], "A1 missing desk")
        self.assertEqual(chart["review_required"], ["A3 number should be A4", "Two chairs missing in back"])
        self.assertEqual(chart["issue_source"], source)
        self.assertEqual(len(chart["warnings"]), 4)
        self.assertEqual([s["label"] for s in chart["seats"]], ["A1", "A2", "A3"])

    def test_mismatched_room_rejects_before_any_seat_is_changed(self):
        chart = parse_seating_chart(chart_bytes({"A1": "A1"}), "room.xlsx")
        before = copy.deepcopy(chart)
        source = issue_document(docx_bytes(["ILCB 112 Seat Issues", "A1 broken desk"]), "issues.docx")
        with self.assertRaisesRegex(ValueError, "different room"):
            apply_issues(chart, source, "ILCB 111")
        self.assertEqual(chart, before)

    def test_unknown_exact_seat_stays_in_review_without_affecting_nearby_labels(self):
        chart = parse_seating_chart(chart_bytes({"A1": "A11", "B1": "B1"}), "room.xlsx")
        source = issue_document(docx_bytes(["A1 broken desk"]), "issues.docx")
        apply_issues(chart, source, "ILCB 111")
        self.assertFalse(any(s["broken"] for s in chart["seats"]))
        self.assertEqual(chart["review_required"], ["A1 broken desk"])

    def test_layout_requires_finite_bounded_geometry_and_exact_seat_coverage(self):
        layout = {"kind": "floorplan", "width": 200, "height": 100,
                  "seats": {"A1": {"x": 20, "y": 25, "width": 10, "height": 10, "rotation": 90}},
                  "features": [{"kind": "path", "d": "M0 0 L100 100 Z"}, {"kind": "text", "x": 0, "y": 0, "text": "Front"}]}
        seats = [{"label": "A1"}]
        self.assertEqual(validate_layout(layout, seats), layout)
        invalid = []
        for key, value in (("width", 0), ("height", float("inf")), ("width", True)):
            item = copy.deepcopy(layout)
            item[key] = value
            invalid.append(item)
        for key, value in (("x", float("nan")), ("y", 100001), ("width", -1), ("rotation", 361)):
            item = copy.deepcopy(layout)
            item["seats"]["A1"][key] = value
            invalid.append(item)
        item = copy.deepcopy(layout)
        item["seats"] = {"A2": item["seats"].pop("A1")}
        invalid.append(item)
        item = copy.deepcopy(layout)
        item["features"][0]["d"] = '<script>alert(1)</script>'
        invalid.append(item)
        for index, item in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_layout(item, seats)


class RoomSourceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.rooms = self.app.extensions["rooms"]

    def test_unresolved_room_blocks_exam_generation_and_later_updates(self):
        client = self.app.test_client()
        headers = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
        response = client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                    "sections": [1]}, headers=headers)
        self.assertEqual(response.status_code, 201, response.json)
        state = client.get("/api/state").json
        group = state["course_groups"][0]
        definition_id = state["course_definitions"][0]["id"]
        response = client.post(f"/api/course-groups/{group['id']}/sections/1/roster",
                               data={"file": (io.BytesIO(b"UIN,Name\n001234567,Example Student\n"), "roster.csv")}, headers=headers)
        self.assertEqual(response.status_code, 201, response.json)
        assignment = client.post("/api/documents/assignments", json={"definition_id": definition_id, "key": "exam", "title": "Exam"}, headers=headers).json
        section_id = client.get("/api/seating/catalog").json["sections"][0]["id"]
        content = chart_bytes({"A1": "A1", "B1": "A2"})
        issues = docx_bytes(["Two chairs missing in back"])
        room = self.rooms.create("ILCB 111", "", content, "room.xlsx", issues_content=issues, issues_filename="issues.docx")
        payload = {"assignment_id": assignment["id"], "section_ids": [section_id], "room_id": room["id"]}
        blocked = client.post("/api/seating", json=payload, headers=headers)
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertIn("needs a room review", blocked.json["error"])
        self.assertEqual(client.get("/api/seating").json, [])
        resolved = self.rooms.update(room["id"], room["version"], {"resolve_review": True})
        created = client.post("/api/seating", json=payload, headers=headers)
        self.assertEqual(created.status_code, 201, created.json)
        chart = created.json
        self.rooms.replace_chart(room["id"], resolved["version"], content, "room.xlsx", issues_content=issues, issues_filename="issues.docx")
        current = client.get(f"/api/seating/{chart['id']}").json
        self.assertFalse(current["check"]["ok"])
        self.assertEqual(current["room"]["review_required"], ["Two chairs missing in back"])
        self.assertIn("Room review: Two chairs missing in back", current["issues"])
        blocked = client.post(f"/api/seating/{chart['id']}/update", json={"version": current["version"]}, headers=headers)
        self.assertEqual(blocked.status_code, 409, blocked.json)

    def test_bulk_retry_preserves_existing_identity_conditions_and_resolved_review(self):
        item = {"name": "ILCB 111", "filename": "room.xlsx", "content": chart_bytes({"A1": "A1", "B1": "A2"}),
                "issues_content": docx_bytes(["Two chairs missing in back"]), "issues_filename": "issues.docx"}
        result = self.rooms.import_many([item])
        room_id = result["added"][0]["id"]
        room = self.rooms.get(room_id)
        room = self.rooms.set_conditions(room_id, room["version"], [room["seats"][0]["id"]], {"broken": True, "blocked": True})
        before = self.rooms.update(room_id, room["version"], {"notes": "Checked in person", "resolve_review": True})
        retried = self.rooms.import_many([{**item, "name": "ilcb 111"}])
        self.assertEqual(retried["added"], [])
        self.assertEqual(retried["unchanged"][0]["id"], room_id)
        self.assertEqual(self.rooms.get(room_id), before)

    def test_bulk_conflicts_roll_back_earlier_rooms_and_issue_changes(self):
        original = {"name": "Existing", "filename": "room.xlsx", "content": chart_bytes({"A1": "A1"})}
        self.rooms.import_many([original])
        before = self.rooms.list()
        addition = {"name": "New", "filename": "new.xlsx", "content": chart_bytes({"A1": "B1"})}
        conflicts = [{**original, "content": chart_bytes({"A1": "A2"})},
                     {**original, "issues_content": docx_bytes(["A1 broken desk"]), "issues_filename": "issues.docx"}]
        for conflict in conflicts:
            with self.subTest(issue_change="issues_content" in conflict), self.assertRaises(Conflict):
                self.rooms.import_many([addition, conflict])
            self.assertEqual(self.rooms.list(), before)

    def test_bulk_parse_failure_and_duplicate_room_names_write_nothing(self):
        first = {"name": "Room", "filename": "room.xlsx", "content": chart_bytes({"A1": "A1"})}
        for bad in ({"name": "Bad", "filename": "bad.xlsx", "content": b"invalid"}, {**first, "name": "room"}):
            with self.subTest(name=bad["name"]), self.assertRaises(ValueError):
                self.rooms.import_many([first, bad])
            self.assertEqual(self.rooms.list(), [])

    def test_replace_preserves_identity_conditions_notes_and_unresolved_sources(self):
        issues = docx_bytes(["ILCB 111 Seat Issues", "A1 broken desk", "Two chairs missing in back"])
        room = self.rooms.create("ILCB 111", "", chart_bytes({"A1": "A1", "B1": "A2"}), "room.xlsx",
                                 issues_content=issues, issues_filename="issues.docx")
        original = {seat["label"]: seat for seat in room["seats"]}
        room = self.rooms.set_conditions(room["id"], room["version"], [original["A2"]["id"]], {"left_handed": True, "blocked": True})
        replacement = self.rooms.replace_chart(room["id"], room["version"], chart_bytes({"C3": "A2", "D4": "A1", "E4": "A3"}), "revised.xlsx")
        seats = {seat["label"]: seat for seat in replacement["seats"]}
        self.assertEqual(seats["A1"]["id"], original["A1"]["id"])
        self.assertEqual(seats["A2"]["id"], original["A2"]["id"])
        self.assertTrue(seats["A1"]["broken"])
        self.assertEqual(seats["A1"]["notes"], "A1 broken desk")
        self.assertTrue(seats["A2"]["blocked"] and seats["A2"]["left_handed"])
        self.assertEqual(replacement["review_required"], ["Two chairs missing in back"])
        self.assertNotIn(seats["A3"]["id"], {seat["id"] for seat in original.values()})
        retained = next(item for item in replacement["imports"] if item["issue_source"])
        self.assertEqual(retained["issue_source"]["sha256"], hashlib.sha256(issues).hexdigest())

    def test_review_resolution_checks_version_and_retains_acknowledged_reasons(self):
        room = self.rooms.create("ILCB 111", "", chart_bytes({"A1": "A1"}), "room.xlsx",
                                 issues_content=docx_bytes(["ILCB 111 Seat Issues", "Two chairs missing in back"]), issues_filename="issues.docx")
        reason = room["review_required"][0]
        current = self.rooms.update(room["id"], room["version"], {"notes": "Check furniture"})
        with self.assertRaises(Conflict):
            self.rooms.update(room["id"], room["version"], {"resolve_review": True})
        self.assertEqual(self.rooms.get(room["id"])["review_required"], [reason])
        with self.assertRaises(ValueError):
            self.rooms.update(room["id"], current["version"], {"resolve_review": "yes"})
        resolved = self.rooms.update(room["id"], current["version"], {"resolve_review": True})
        self.assertEqual(resolved["review_required"], [])
        self.assertEqual(resolved["version"], current["version"] + 1)
        review = next(item for item in resolved["imports"] if item["filename"] == "Instructor room review")
        self.assertIn(reason, review["warnings"][0])
        self.assertEqual(review["sha256"], room["source_sha256"])
        self.assertEqual((review["added"], review["removed"], review["kept"]), (0, 0, 1))
