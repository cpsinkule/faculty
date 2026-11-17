"""Roster identity, parser, ownership, and real disk-lifecycle regression checks."""
import csv
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from openpyxl import Workbook

from app import create_app
from course_storage import StorageError, inventory
from roster_import import parse_roster
from rosters import connect
from test_storage import HEADERS, temporary_storage

COLUMNS = ["Status", "Name", "UIN", "Reg\nStatus", "Major", "Class", "Degree\nCandidates", "Midterm\nRequired", "Midterm", "Final", "Updated\nFinal", "Email"]
STUDENTS = [
    ["", "Example, Alice", "001234567", "**Web Registered**", "MATH", "U1", "", "Y", "", "", "", "alice@example.test"],
    ["", "Example, Bob", "123456789", "Registered", "PHYS", "U2", "", "N", "B", "A", "", "bob@example.test"],
]


def csv_bytes(rows=STUDENTS, columns=COLUMNS):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerows([columns, *rows])
    return text.getvalue().encode("utf-8-sig")


def xlsx_bytes(rows=STUDENTS):
    workbook = Workbook()
    workbook.active.append(["\n" + name + "\n" for name in COLUMNS])
    for row in rows:
        workbook.active.append(row)
    # Exercise Excel's numeric UIN cells and leading-zero number formats.
    workbook.active["C2"] = 1234567
    workbook.active["C2"].number_format = "000000000"
    target = io.BytesIO()
    workbook.save(target)
    workbook.close()
    return target.getvalue()


class ParserTests(unittest.TestCase):
    def test_both_formats_preserve_all_fields_and_leading_zero_uin(self):
        csv_data = parse_roster(csv_bytes(), "roster.csv")
        xlsx_data = parse_roster(xlsx_bytes(), "roster.xlsx")
        self.assertEqual(csv_data["students"], xlsx_data["students"])
        self.assertEqual(csv_data["headers"], [" ".join(name.split()) for name in COLUMNS])
        self.assertEqual(csv_data["source_headers"], COLUMNS)
        self.assertEqual(csv_data["students"][0]["uin"], "001234567")
        self.assertEqual(csv_data["students"][1]["fields"]["Final"], "A")

    def test_bad_rows_duplicates_headers_formats_and_formulas_rejected(self):
        malformed = [
            (b"Name,Email\nPerson,person@example.test", "r.csv"),
            (csv_bytes([*STUDENTS, STUDENTS[0]]), "r.csv"),
            (csv_bytes([["", "Example", "1", *([""] * 9)]]), "r.csv"),
            (csv_bytes([], ["Name", "UIN", "Name"]), "r.csv"),
            (b"not excel", "r.xlsx"),
            (csv_bytes(), "r.xls"),
            (csv_bytes([]), "r.csv"),
        ]
        for content, name in malformed:
            with self.subTest(name=name, size=len(content)), self.assertRaises(ValueError):
                parse_roster(content, name)
        rows = [STUDENTS[0].copy()]
        rows[0][1] = '=HYPERLINK("https://example.test")'
        with self.assertRaisesRegex(ValueError, "formula"):
            parse_roster(xlsx_bytes(rows), "r.xlsx")


class RosterTests(unittest.TestCase):
    managed = False

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name) if self.managed else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        self.service = self.app.extensions["rosters"]

    def add(self, **overrides):
        response = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Fall", "year": 2026,
            "sections": [507, 508], "multiple_sections": True, "is_current": True, **overrides}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        if self.storage:
            self.storage.reconcile_all()
        record = response.json
        group = next(group for group in self.client.get("/api/state").json["course_groups"] if any(c["id"] == record["id"] for c in group["offerings"]))
        return record, group

    def route(self, group, section=507):
        return f"/api/course-groups/{group['id']}/sections/{section}/roster"

    def upload(self, group, rows=STUDENTS, section=507, excel=False):
        result = self.client.post(self.route(group, section), data={"file": (io.BytesIO(xlsx_bytes(rows) if excel else csv_bytes(rows)), "roster.xlsx" if excel else "roster.csv")}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def change(self, course, **values):
        current = next(row for row in self.client.get("/api/state").json["courses"] if row["id"] == course["id"])
        return self.client.patch(f"/api/courses/{course['id']}", json={"version": current["version"], **values}, headers=HEADERS)

    def test_identity_reimports_sections_terms_codes_and_restart(self):
        course, fall = self.add()
        first = self.upload(fall)
        identifiers = {student["uin"]: student["id"] for student in first["students"]}
        self.assertEqual(len(set(identifiers.values())), 2)
        for identifier in identifiers.values():
            self.assertRegex(identifier, r"^[1-9][0-9]{9}$")
        second = self.upload(fall, excel=True)
        self.assertEqual(second["students"], first["students"])
        self.assertEqual((second["last_import"]["added"], second["last_import"]["updated"]), (0, 2))
        self.assertEqual(self.upload(fall, section=508)["students"], first["students"])
        _, spring = self.add(term="Spring", is_current=False)
        later = self.upload(spring)
        _, other = self.add(code="PHYS 140")
        another = self.upload(other)
        for result in (later, another):
            for student in result["students"]:
                self.assertEqual(student["id"][:5], identifiers[student["uin"]][:5])
                self.assertNotEqual(student["id"], identifiers[student["uin"]])
        self.assertEqual(len({s["id"] for roster in (first, later, another) for s in roster["students"]}), 6)
        restarted = create_app(storage=self.storage) if self.storage else create_app(self.directory.name)
        restarted_client = restarted.test_client(); restarted_client.get("/").close()
        self.assertEqual(restarted_client.get(self.route(fall)).json["students"], first["students"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_identities").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_enrollments").fetchone()[0], 6)

    def test_upsert_preserves_absent_students_and_changes_all_export_fields(self):
        _, group = self.add()
        first = self.upload(group)
        row = STUDENTS[0].copy()
        row[1], row[10], row[11] = "Changed, Alice", "A+", "changed@example.test"
        updated = self.upload(group, rows=[row])
        self.assertEqual(updated["count"], 2)
        student = next(student for student in updated["students"] if student["uin"] == row[2])
        self.assertEqual(student["id"], first["students"][0]["id"])
        self.assertEqual(student["fields"]["Updated Final"], "A+")
        self.assertEqual(student["fields"]["Email"], "changed@example.test")

    def test_q_drops_and_withdrawals_are_dropped_by_the_import_and_absent_students_are_offered(self):
        """Howdy keeps such a student
        on the roster ("Q-Drop State Limited" / Final Q, "Withdraw After Census Date" / Final W). Since 2026-10-25 (a Q-dropped
        student the Assistant counted as current) the import drops them itself, saying so, while a student the latest file left
        out is only offered for the roster's own Drop. A restored student is never dropped or offered again."""
        _, group = self.add()
        carol = ["", "Example, Carol", "222333444", "**Web Registered**", "MATH", "U1", "", "N", "C", "", "", "carol@example.test"]
        dave = ["", "Example, Dave", "333444555", "**Registered**", "MATH", "U1", "", "N", "", "", "", "dave@example.test"]
        first = self.upload(group, rows=[*STUDENTS, carol, dave])
        self.assertNotIn("leaving", first)
        self.assertNotIn("missing", first["last_import"])
        self.assertNotIn("registrar_dropped", first["last_import"])
        q = STUDENTS[1].copy(); q[3], q[9] = "Q-Drop State Limited", "Q"
        w = carol.copy(); w[3], w[9] = "Withdraw After Census Date", "W"
        later = self.upload(group, rows=[STUDENTS[0], q, w])
        ids = {s["fields"]["Name"]: s["id"] for s in later["students"]}
        self.assertEqual(later["last_import"]["missing"], [ids["Example, Dave"]])
        self.assertEqual((later["count"], later["dropped_count"]), (2, 2))
        self.assertEqual([(row["name"], row["reason"]) for row in later["last_import"]["registrar_dropped"]],
                         [("Example, Bob", "Q drop"), ("Example, Carol", "Withdrawn")])
        reasons = {s["fields"]["Name"]: s["dropped"]["reason"] for s in later["students"] if "dropped" in s}
        self.assertEqual(reasons, {"Example, Bob": "Q drop on the registrar's roster", "Example, Carol": "Withdrawn on the registrar's roster"})
        self.assertEqual({s["fields"]["Name"]: s["dropped"]["source"] for s in later["students"] if "dropped" in s},
                         {"Example, Bob": "roster", "Example, Carol": "roster"})
        self.assertNotIn("still_dropped", later["last_import"])
        self.assertEqual([(row["name"], row["reason"][:26]) for row in later["leaving"]], [("Example, Dave", "Not on the roster imported")])
        # A restored withdrawal stays current through later imports that still mark it; the dropped Q drop stays dropped.
        restored = self.client.post(self.route(group) + "/restore", json={"enrollment_ids": [ids["Example, Carol"]], "request_id": "w-2"},
                                    headers=HEADERS).json
        self.assertEqual((restored["count"], restored["dropped_count"]), (3, 1))
        again = self.upload(group, rows=[STUDENTS[0], q, w])
        self.assertEqual((again["count"], again["dropped_count"]), (3, 1))
        self.assertNotIn("registrar_dropped", again["last_import"])
        self.assertNotIn("still_dropped", again["last_import"])
        self.assertEqual([row["name"] for row in again["leaving"]], ["Example, Dave"])
        self.assertEqual(self.client.get(self.route(group)).json["leaving"], again["leaving"])

    def test_collisions_retry_and_concurrent_imports_reuse_one_id(self):
        _, group = self.add()
        with patch("rosters.secrets.randbelow", side_effect=[12345, 12345, 12345, 12345, 54321]):
            first = self.upload(group)
        self.assertEqual([s["id"] for s in first["students"]], ["2234512345", "2234554321"])
        _, spring = self.add(term="Spring")
        def upload():
            with self.app.test_client() as client:
                client.get("/").close()
                return client.post(self.route(spring), data={"file": (io.BytesIO(csv_bytes()), "r.csv")}, headers=HEADERS)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: upload(), range(2)))
        self.assertTrue(all(result.status_code == 201 for result in results))
        self.assertEqual(results[0].json["students"], results[1].json["students"])

    def test_invalid_file_is_atomic_and_upload_protection_remains(self):
        _, group = self.add()
        first = self.upload(group)
        for content, headers, code in ((csv_bytes([*STUDENTS, STUDENTS[0]]), HEADERS, 400), (csv_bytes(), {}, 403), (csv_bytes(), {**HEADERS, "Origin": "https://elsewhere.test"}, 403)):
            result = self.client.post(self.route(group), data={"file": (io.BytesIO(content), "r.csv")}, headers=headers)
            self.assertEqual(result.status_code, code, result.json)
        self.assertEqual(self.client.get(self.route(group)).json, first)
        self.assertEqual(self.client.get(self.route(group, 999)).status_code, 404)
        self.assertEqual(self.client.post(self.route(group), json={}, headers=HEADERS).status_code, 415)
        self.assertNotIn("student_identities", self.client.get("/api/export").json)

    def test_owners_overlap_and_edits_cannot_detach_imports(self):
        first, group = self.add()
        initial = self.upload(group)
        other, again = self.add()
        self.assertEqual(self.client.get(self.route(again)).json, initial)
        response = self.change(first, code="MATH 240")
        self.assertEqual(response.status_code, 200, response.json)
        if self.storage:
            self.storage.reconcile_all()
        self.assertEqual(self.client.get(self.route(group)).json["students"], initial["students"])
        self.assertEqual(self.change(other, term="Spring").status_code, 409)
        self.assertEqual(self.change(other, sections=[508], multiple_sections=False).status_code, 409)
        self.assertEqual(self.change(other, name="New title", crn="12345").status_code, 200)
        if self.storage:
            self.storage.reconcile_all()
        self.assertEqual(self.client.get(self.route(group)).json["term_id"], initial["term_id"])


    def test_course_group_summary_counts_sections_from_the_catalog_only(self):
        _, fall = self.add()
        _, spring = self.add(term="Spring", is_current=False)
        self.upload(fall)
        self.upload(fall, rows=STUDENTS[:1], section=508)
        self.upload(spring, rows=STUDENTS[:1])
        response = self.client.get("/api/course-groups/summary")
        self.assertEqual(response.status_code, 200, response.json)
        groups = response.json["groups"]
        roster = self.client.get(self.route(fall)).json
        self.assertEqual(groups[fall["id"]]["term_id"], roster["term_id"])
        self.assertEqual(groups[fall["id"]]["sections"]["507"], {"section_id": roster["section_id"], "students": 2,
            "with_accommodations": 0, "dr_bookings": 0, "with_dr_bookings": 0})
        self.assertEqual(groups[fall["id"]]["sections"]["508"]["students"], 1)
        # Another term of the same code has its own owner and counts.
        self.assertNotEqual(groups[spring["id"]]["term_id"], groups[fall["id"]]["term_id"])
        self.assertEqual(groups[spring["id"]]["sections"]["507"]["students"], 1)
        self.assertEqual(groups[spring["id"]]["sections"]["508"]["students"], 0)
        # A section without a roster yet is listed with zero students.
        _, other = self.add(code="PHYS 140", sections=[1], multiple_sections=False)
        empty = self.client.get("/api/course-groups/summary").json["groups"][other["id"]]["sections"]["1"]
        self.assertEqual({key: value for key, value in empty.items() if key != "section_id"},
                         {"students": 0, "with_accommodations": 0, "dr_bookings": 0, "with_dr_bookings": 0})
        # The projection names no student: no UINs, private IDs or names.
        text = json.dumps(self.client.get("/api/course-groups/summary").json)
        for student in roster["students"]:
            self.assertNotIn(student["uin"], text)
            self.assertNotIn(student["id"], text)
        self.assertNotIn("Example", text)

    def test_summary_reads_term_and_section_owners_once_for_all_groups(self):
        # PERFORMANCE: catalog owner lookups are shared within this read, without caching across reads.
        import rosters
        groups = [self.add(year=2020 + index)[1] for index in range(6)]
        statements = []
        checkout = rosters._checkout

        def traced(path):
            db = checkout(path)
            db.set_trace_callback(statements.append)
            return db

        with patch("rosters._checkout", side_effect=traced):
            result = self.service.summary()["groups"]
        self.assertEqual(set(result), {group["id"] for group in groups})
        for group in result.values():
            self.assertEqual(set(group["sections"]), {"507", "508"})
            self.assertTrue(all(section["students"] == 0 for section in group["sections"].values()))
        self.assertEqual(sum("FROM course_terms" in statement for statement in statements), 1)
        self.assertEqual(sum("FROM course_sections" in statement for statement in statements), 1)

    def test_drop_and_restore_reject_non_string_identifiers_without_changing_the_roster(self):
        # IDENTITY: malformed JSON cannot turn an invalid enrollment list into a server error or a roster write.
        _, group = self.add()
        before = self.upload(group)
        for action in ("drop", "restore"):
            for ids in ([{}], [[]], [None], [before["students"][0]["id"], {}]):
                with self.subTest(action=action, ids=ids):
                    response = self.client.post(self.route(group) + "/" + action,
                        json={"enrollment_ids": ids, "request_id": "bad-list"}, headers=HEADERS)
                    self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get(self.route(group)).json, before)


class ManagedRosterTests(RosterTests):
    managed = True

    def test_missing_roster_cannot_be_replaced_or_accepted_as_final_backup(self):
        course, group = self.add()
        result = self.upload(group)
        manager = self.storage.term_storage
        self.storage.backup_all()
        previous = manager.row(result["term_id"])["storage_snapshot"]
        file = manager.hot_path(result["term_id"]) / "data" / "roster.json"
        file.rename(file.with_suffix(".missing"))
        self.assertEqual(self.client.get(self.route(group)).status_code, 503)
        response = self.client.post(self.route(group), data={"file": (io.BytesIO(csv_bytes()), "r.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(file.exists())
        self.assertEqual(self.change(course, is_current=False).status_code, 200)
        with self.assertLogs("course_storage", level="ERROR"):
            self.assertIn(result["term_id"], self.storage.reconcile_all())
        self.assertEqual(manager.row(result["term_id"])["storage_snapshot"], previous)
        self.assertTrue(file.parent.exists())

    def test_term_storage_nightly_final_backup_last_current_and_restore(self):
        first, group = self.add()
        other, _ = self.add()
        result = self.upload(group)
        manager = self.storage.term_storage
        identifier = result["term_id"]
        hot = manager.hot_path(identifier)
        inventory(hot / "data")
        self.assertEqual(self.storage.backup_all()["status"], "complete")
        self.assertTrue(hot.exists())
        self.assertEqual(self.change(first, is_current=False).status_code, 200)
        self.storage.reconcile_all()
        self.assertTrue(hot.exists(), "Another current entry still needs the shared term roster")
        self.assertEqual(self.change(other, is_current=False).status_code, 200)
        self.storage.reconcile_all()
        self.assertFalse(hot.exists())
        archived = manager.row(identifier)
        manager.verify_snapshot(identifier, archived["storage_snapshot"])
        self.assertEqual(self.client.get(self.route(group)).json["students"], result["students"])
        self.upload(group, rows=[STUDENTS[0]])
        self.assertFalse(hot.exists(), "Archived edits must stay on the HDD")
        self.assertEqual(self.change(first, is_current=True).status_code, 200)
        self.storage.reconcile_all()
        self.assertTrue(hot.exists())
        self.assertEqual(self.client.get(self.route(group)).json["students"], result["students"])

    def test_archive_verification_failure_retains_hot_roster_and_recovers(self):
        course, group = self.add()
        result = self.upload(group)
        manager = self.storage.term_storage
        hot = manager.hot_path(result["term_id"])
        expected = inventory(hot)
        self.assertEqual(self.change(course, is_current=False).status_code, 200)
        with patch.object(manager, "verify_snapshot", side_effect=StorageError("simulated verification failure")), self.assertLogs("course_storage", level="ERROR"):
            self.assertIn(result["term_id"], self.storage.reconcile_all())
        self.assertEqual(inventory(hot), expected)
        self.assertTrue(self.client.get("/api/state").json["storage"]["issues"])
        self.assertEqual(self.storage.reconcile_all(), [])
        self.assertFalse(hot.exists())
        self.assertEqual(self.client.get(self.route(group)).json["students"], result["students"])

    def test_failed_archived_publish_reserves_ids_but_retains_old_roster(self):
        _, group = self.add(is_current=False)
        initial = self.upload(group, rows=[STUDENTS[0]])
        manager = self.storage.term_storage
        with patch.object(manager, "snapshot", side_effect=StorageError("simulated full disk")):
            response = self.client.post(self.route(group), data={"file": (io.BytesIO(csv_bytes()), "r.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get(self.route(group)).json, initial)
        with connect(self.app.config["DATABASE"]) as db:
            reserved = db.execute("SELECT id FROM student_enrollments WHERE uin=?", (STUDENTS[1][2],)).fetchone()[0]
        retry = self.upload(group)
        self.assertEqual(retry["students"][1]["id"], reserved)


if __name__ == "__main__":
    unittest.main()
