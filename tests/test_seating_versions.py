"""Saved exam letters: physical row order, ownership, edits, and seat changes."""

import json
from collections import Counter
from pathlib import Path
import unittest
from unittest.mock import patch

from app import create_app
from rosters import connect
from seating_versions import automatic_versions, occupied_rows, snapshot
import test_seating as fixtures
import test_seating_mail_merge as card_fixtures
from test_rooms import chart_bytes

HEADERS = fixtures.HEADERS


class ExamVersionTests(unittest.TestCase):
    setUp = fixtures.SeatingTests.setUp
    import_dr = fixtures.SeatingTests.import_dr
    create = fixtures.SeatingTests.create
    by_name = fixtures.SeatingTests.by_name

    def tiny(self, **overrides):
        response = self.create(room_id=self.small["id"], section_ids=[self.sections[508]["id"]], **overrides)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def assign(self, chart, count=2, **overrides):
        return self.client.post(f"/api/seating/{chart['id']}/exam-versions",
                                json={"version": chart["version"], "count": count, **overrides}, headers=HEADERS)

    def edit(self, chart, seat_id, **values):
        return self.client.post(f"/api/seating/{chart['id']}/exam-versions/edit",
                                json={"version": chart["version"], "seat_id": seat_id, **values}, headers=HEADERS)

    def saved(self, chart):
        return {seat["id"]: seat["exam_version"] for seat in chart["room"]["seats"] if seat["exam_version"] is not None}

    def test_optional_versions_are_absent_until_explicit_assignment(self):
        chart = self.tiny()
        self.assertIsNone(chart["exam_version_count"])
        self.assertIs(chart["allow_unequal_versions"], False)
        self.assertEqual(chart["exam_versions"], {"count": None, "rows": [], "counts": [], "total": 0,
                                                  "unassigned": [], "conflicts": [], "ready": False})
        self.assertTrue(all(student["exam_version"] is None for student in chart["students"]))
        self.assertEqual(self.saved(chart), {})
        assigned = self.assign(chart)
        self.assertEqual(assigned.status_code, 200, assigned.json)
        chart = assigned.json
        rows = chart["exam_versions"]["rows"]
        self.assertEqual([(s["seat"], s["version"]) for s in rows[0]["students"]], [("A1", "A"), ("A3", "B")])
        self.assertEqual(chart["exam_versions"]["counts"], [{"version": "A", "count": 1}, {"version": "B", "count": 1}])
        self.assertTrue(chart["exam_versions"]["ready"])
        self.assertEqual(chart["events"][0]["kind"], "exam_versions_assigned")
        # Repeating the exact requested assignment adds no event or revision.
        same = self.assign(chart).json
        self.assertEqual((same["version"], same["events"]), (chart["version"], chart["events"]))

    def test_creation_assigns_only_eligible_students_and_survives_restart(self):
        chart = self.create(exam_version_count=3).json
        self.assertEqual(chart["exam_version_count"], 3)
        self.assertEqual(chart["exam_versions"]["total"], 7)
        self.assertEqual([row["count"] for row in chart["exam_versions"]["counts"]], [3, 2, 2])
        self.assertTrue(all(student["exam_version"] is None for student in chart["students"] if student["status"] == "dr_testing"))
        for row in chart["exam_versions"]["rows"]:
            letters = [student["version"] for student in row["students"]]
            self.assertTrue(all(first != second for first, second in zip(letters, letters[1:])))
        restarted = create_app(self.directory.name).test_client().get(f"/api/seating/{chart['id']}").json
        self.assertEqual(restarted["exam_versions"], chart["exam_versions"])
        self.assertEqual(self.saved(restarted), self.saved(chart))

    def test_legacy_catalog_upgrade_keeps_existing_charts_and_seat_assignments(self):
        chart = self.tiny()
        with connect(self.app.extensions["seating"].path) as db:
            db.execute("DROP TABLE seating_exam_versions")
            db.execute("ALTER TABLE seating_charts DROP COLUMN exam_version_count")
            db.execute("ALTER TABLE seating_charts DROP COLUMN allow_unequal_versions")
        restarted = create_app(self.directory.name)
        client = restarted.test_client()
        retained = client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(retained["students"], chart["students"])
        self.assertIsNone(retained["exam_version_count"])
        self.assertIs(retained["allow_unequal_versions"], False)
        assigned = client.post(f"/api/seating/{chart['id']}/exam-versions",
                               json={"version": retained["version"], "count": 2}, headers=HEADERS)
        self.assertEqual(assigned.status_code, 200, assigned.json)
        self.assertTrue(assigned.json["exam_versions"]["ready"])

    def test_room_replacement_preserves_matching_seat_letters_and_removes_missing_seats(self):
        chart = self.tiny(exam_version_count=2)
        original = self.saved(chart)
        service = self.app.extensions["rooms"]
        replaced = service.replace_chart(self.small["id"], self.small["version"],
                                         chart_bytes({"A1": "A3", "B1": "A1", "C1": "A2", "A2": "B1"}), "moved.xlsx")
        retained = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(self.saved(retained), original)
        self.assertEqual([(student["seat"], student["version"]) for student in retained["exam_versions"]["rows"][0]["students"]],
                         [("A3", "B"), ("A1", "A")])
        removed = next(seat["id"] for seat in replaced["seats"] if seat["label"] == "A3")
        service.replace_chart(self.small["id"], replaced["version"],
                              chart_bytes({"B1": "A1", "C1": "A2", "A2": "B1"}), "removed.xlsx")
        changed = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertEqual(self.saved(changed), {key: value for key, value in original.items() if key != removed})
        self.assertFalse(changed["exam_versions"]["ready"])
        self.assertEqual(changed["exam_version_count"], 2)
        with connect(self.app.extensions["seating"].path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_exam_versions WHERE seat_id=?", (removed,)).fetchone()[0], 0)

    def test_real_hecc_table_row_follows_sheet_order_across_empty_positions(self):
        if not (Path(__file__).parent / "fixtures" / "rooms" / "HECC 105.xlsx").exists():
            self.skipTest("the department's seating chart HECC 105.xlsx is not in this copy")
        occupied = ["K Table 1 · 1", "K Table 1 · 3", "K1", "K Table 2 · 2", "K Table 2 · 4"]
        seats = {seat["label"]: seat["id"] for seat in self.hall["seats"]}
        selected = {seats[label]: None for label in occupied}
        rows = occupied_rows(self.hall, selected)
        self.assertEqual([row["label"] for row in rows], ["Row K"])
        self.assertEqual([seat["label"] for seat in rows[0]["seats"]], occupied)
        assigned = automatic_versions(self.hall, selected, 2)
        self.assertEqual([assigned[seats[label]] for label in occupied], ["A", "B", "A", "B", "A"])

    def test_invalid_counts_do_not_create_or_mutate_charts(self):
        for value in (0, 27, True, 2.5, "2", [], {}):
            result = self.create(exam_version_count=value)
            self.assertEqual(result.status_code, 400, result.json)
            self.assertEqual(self.client.get("/api/seating").json, [])
        chart = self.tiny(exam_version_count=None)
        for value in (None, 0, 27, False, "2"):
            self.assertEqual(self.assign(chart, value).status_code, 400)
        self.assertEqual(self.client.get(f"/api/seating/{chart['id']}").json["version"], chart["version"])

    def test_unequal_preference_requires_bool_and_persists_when_assignment_is_deferred(self):
        for value in (None, 0, 1, "false", "true", [], {}):
            result = self.create(allow_unequal_versions=value)
            self.assertEqual(result.status_code, 400, result.json)
            self.assertEqual(self.client.get("/api/seating").json, [])
        chart = self.tiny(allow_unequal_versions=True)
        self.assertIs(chart["allow_unequal_versions"], True)
        self.assertIsNone(chart["exam_version_count"])
        for value in (None, 0, 1, "true"):
            result = self.assign(chart, allow_unequal_versions=value)
            self.assertEqual(result.status_code, 400, result.json)
        assigned = self.assign(chart, allow_unequal_versions=True).json
        self.assertIs(assigned["allow_unequal_versions"], True)
        self.assertIs(assigned["events"][0]["detail"]["allow_unequal_versions"], True)
        same = self.assign(assigned, allow_unequal_versions=True).json
        self.assertEqual(same["version"], assigned["version"])
        # The policy is saved even when one row gives no optimization to make.
        balanced = self.assign(assigned).json
        self.assertIs(balanced["allow_unequal_versions"], False)
        self.assertEqual(self.saved(balanced), self.saved(assigned))
        self.assertEqual(balanced["version"], assigned["version"] + 1)

    def test_unequal_mode_created_with_versions_survives_restart_moves_and_regeneration(self):
        chart = self.tiny(exam_version_count=2, allow_unequal_versions=True)
        restarted = create_app(self.directory.name).test_client().get(f"/api/seating/{chart['id']}").json
        self.assertIs(restarted["allow_unequal_versions"], True)
        first, second = chart["exam_versions"]["rows"][0]["students"]
        moved = self.client.post(f"/api/seating/{chart['id']}/move", json={"version": chart["version"],
                                 "enrollment_id": first["enrollment_id"], "seat_id": second["seat_id"]}, headers=HEADERS).json
        for mode in ("update", "regenerate"):
            moved = self.client.post(f"/api/seating/{chart['id']}/{mode}", json={"version": moved["version"]}, headers=HEADERS).json
            self.assertIs(moved["allow_unequal_versions"], True)
            self.assertEqual(self.saved(moved), self.saved(chart))

    def test_version_edits_swap_or_set_without_moving_students_or_touching_cards(self):
        chart = self.tiny(exam_version_count=2)
        students = chart["exam_versions"]["rows"][0]["students"]
        first, second = [student["seat_id"] for student in students]
        before = {student["enrollment_id"]: student["seat_id"] for student in chart["students"]}
        cards = self.app.extensions["response_cards"]
        with patch.object(cards, "invalidate_cards") as invalidation, patch.object(cards, "reconcile_invalidations") as revoke:
            result = self.edit(chart, first, target_seat_id=second)
            self.assertEqual(result.status_code, 200, result.json)
            swapped = result.json
            self.assertEqual(self.saved(swapped), {first: "B", second: "A"})
            self.assertEqual({student["enrollment_id"]: student["seat_id"] for student in swapped["students"]}, before)
            self.assertEqual(swapped["events"][0]["kind"], "exam_version_changed")
            conflict = self.edit(swapped, second, exam_version="B").json
            self.assertEqual(conflict["exam_versions"]["conflicts"], [{"row": "Row 1", "seats": ["A1", "A3"], "version": "B"}])
            self.assertTrue(conflict["exam_versions"]["ready"])
            unchanged = self.edit(conflict, second, exam_version="B").json
            self.assertEqual((unchanged["version"], unchanged["events"]), (conflict["version"], conflict["events"]))
            unchanged = self.edit(conflict, first, target_seat_id=second).json
            self.assertEqual(unchanged["version"], conflict["version"])
            invalidation.assert_not_called()
            revoke.assert_not_called()
        rebuilt = self.assign(conflict, 1).json
        self.assertEqual(set(self.saved(rebuilt).values()), {"A"})
        self.assertEqual(rebuilt["exam_versions"]["conflicts"], [])
        self.assertEqual(rebuilt["exam_versions"]["counts"], [{"version": "A", "count": 2}])

    def test_edits_reject_stale_invalid_unoccupied_and_foreign_seats(self):
        chart = self.tiny()
        occupied = next(seat for seat in chart["room"]["seats"] if seat["student"])
        self.assertEqual(self.edit(chart, occupied["id"], exam_version="A").status_code, 409)
        assigned = self.assign(chart).json
        self.assertEqual(self.assign(chart).status_code, 409)
        self.assertEqual(self.edit(chart, occupied["id"], exam_version="A").status_code, 409)
        unoccupied = next(seat["id"] for seat in assigned["room"]["seats"] if not seat["student"] and not seat["broken"])
        for seat_id, change in ((occupied["id"], {"exam_version": "C"}), (occupied["id"], {"exam_version": "a"}),
                                (occupied["id"], {}), (occupied["id"], {"exam_version": "A", "target_seat_id": occupied["id"]}),
                                (occupied["id"], {"target_seat_id": unoccupied}), (unoccupied, {"exam_version": "A"}),
                                (self.hall["seats"][0]["id"], {"exam_version": "A"})):
            result = self.edit(assigned, seat_id, **change)
            self.assertEqual(result.status_code, 400, result.json)
        unchanged = self.client.get(f"/api/seating/{assigned['id']}").json
        self.assertEqual((unchanged["version"], unchanged["exam_versions"]), (assigned["version"], assigned["exam_versions"]))

    def test_moves_keep_letters_on_seats_and_report_new_unassigned_seat(self):
        chart = self.tiny(exam_version_count=2)
        first, second = chart["exam_versions"]["rows"][0]["students"]
        moved = self.client.post(f"/api/seating/{chart['id']}/move", json={"version": chart["version"],
                                 "enrollment_id": first["enrollment_id"], "seat_id": second["seat_id"]}, headers=HEADERS).json
        self.assertEqual(self.saved(moved), self.saved(chart))
        self.assertEqual(next(student for student in moved["students"] if student["enrollment_id"] == first["enrollment_id"])["exam_version"], "B")
        empty = next(seat for seat in moved["room"]["seats"] if seat["label"] == "A2")
        moved = self.client.post(f"/api/seating/{chart['id']}/move", json={"version": moved["version"],
                                 "enrollment_id": first["enrollment_id"], "seat_id": empty["id"]}, headers=HEADERS).json
        self.assertEqual(self.saved(moved), self.saved(chart))
        self.assertEqual(moved["exam_versions"]["unassigned"], ["A2"])
        self.assertFalse(moved["exam_versions"]["ready"])
        fixed = self.edit(moved, empty["id"], exam_version="B").json
        self.assertTrue(fixed["exam_versions"]["ready"])
        for mode in ("update", "regenerate"):
            changed = self.client.post(f"/api/seating/{chart['id']}/{mode}", json={"version": fixed["version"]}, headers=HEADERS).json
            self.assertEqual(self.saved(changed), self.saved(fixed))
            fixed = changed

    def test_new_dr_exclusion_retains_seat_letters_and_flags_new_consecutive_conflict(self):
        chart = self.tiny(assignment_id=self.quiz["id"], exam_version_count=2)
        middle = next(student for student in chart["students"] if student["seat"] == "A2")
        self.import_dr([fixtures.booking(UIN=middle["uin"], Section="508", **{"Exam Type": "Quiz 3"})])
        changed = self.client.post(f"/api/seating/{chart['id']}/update", json={"version": chart["version"]}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        changed = changed.json
        self.assertEqual(self.saved(changed), self.saved(chart))
        self.assertEqual(changed["exam_versions"]["conflicts"], [{"row": "Row 1", "seats": ["A1", "A3"], "version": "A"}])
        self.assertTrue(changed["exam_versions"]["ready"])
        self.assertIsNone(next(student for student in changed["students"] if student["enrollment_id"] == middle["enrollment_id"])["exam_version"])

    def test_version_routes_require_same_origin_write_guard(self):
        chart = self.tiny(exam_version_count=2)
        for suffix, payload in (("exam-versions", {"count": 3}), ("exam-versions/edit", {
                "seat_id": next(iter(self.saved(chart))), "exam_version": "B"})):
            response = self.client.post(f"/api/seating/{chart['id']}/{suffix}", json={"version": chart["version"], **payload})
            self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(self.client.get(f"/api/seating/{chart['id']}").json["exam_versions"], chart["exam_versions"])

    def test_changed_room_and_roster_block_version_mutation(self):
        chart = self.tiny(exam_version_count=2)
        seat = next(seat for seat in chart["room"]["seats"] if seat["student"])
        self.client.post(f"/api/rooms/{self.small['id']}/seats", json={"version": self.small["version"],
                         "seat_ids": [seat["id"]], "blocked": True}, headers=HEADERS)
        self.assertEqual(self.assign(chart).status_code, 409)
        self.assertEqual(self.edit(chart, seat["id"], exam_version="B").status_code, 409)
        changed = self.client.get(f"/api/seating/{chart['id']}").json
        self.assertFalse(changed["exam_versions"]["ready"])
        self.assertEqual(changed["version"], chart["version"])

    def test_multi_room_creation_balances_versions_and_rolls_back_as_one_transaction(self):
        data = {"assignment_id": self.assignment["id"], "exam_version_count": 3, "allocations": [
            {"room_id": self.hall["id"], "section_ids": [self.sections[508]["id"]]},
            {"room_id": self.small["id"], "section_ids": [self.sections[507]["id"]]}]}
        result = self.client.post("/api/seating/plan", json=data, headers=HEADERS)
        self.assertEqual(result.status_code, 409, result.json)
        with connect(self.app.extensions["seating"].path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_exam_versions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_charts").fetchone()[0], 0)
        data["allocations"][0]["section_ids"], data["allocations"][1]["section_ids"] = data["allocations"][1]["section_ids"], data["allocations"][0]["section_ids"]
        result = self.client.post("/api/seating/plan", json=data, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        counts = {letter: 0 for letter in "ABC"}
        for chart in result.json["charts"]:
            self.assertTrue(chart["exam_versions"]["ready"])
            chart_counts = [item["count"] for item in chart["exam_versions"]["counts"]]
            self.assertLessEqual(max(chart_counts) - min(chart_counts), 1)
            self.assertIs(chart["allow_unequal_versions"], False)
            for item in chart["exam_versions"]["counts"]:
                counts[item["version"]] += item["count"]
        self.assertEqual(counts, {"A": 3, "B": 2, "C": 2})

    def test_chart_delete_cascades_letters_without_deleting_room(self):
        chart = self.tiny(exam_version_count=2)
        deleted = self.client.delete(f"/api/seating/{chart['id']}", json={"version": chart["version"]}, headers=HEADERS)
        self.assertEqual(deleted.status_code, 204, deleted.json)
        with connect(self.app.extensions["seating"].path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_exam_versions").fetchone()[0], 0)
        self.assertEqual(self.client.get(f"/api/rooms/{self.small['id']}").status_code, 200)


class ExamVersionCardIsolationTests(unittest.TestCase):
    setUp = card_fixtures.SeatingMailMergeTests.setUp
    import_dr = fixtures.SeatingTests.import_dr
    create = fixtures.SeatingTests.create
    confirm = card_fixtures.SeatingMailMergeTests.confirm

    def test_version_assignment_and_edits_leave_published_cards_and_queued_mail_untouched(self):
        with connect(self.path) as db:
            issues = [row[0] for row in db.execute("SELECT id FROM response_card_issues")]
        for issue_id in issues:
            self.cards.mail.queue_invitations(issue_id)
        tables = ("seating_assignments", "response_card_recipients", "response_card_mail",
                  "response_card_invalidations", "seating_card_updates")
        def records():
            with connect(self.path) as db:
                return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in tables}
        original = records()
        self.assertTrue(original["response_card_recipients"])
        self.assertTrue(original["response_card_mail"])
        remote_calls = list(self.cards.remote.calls)
        assigned = self.client.post(self.route + "/exam-versions", json={"version": self.chart["version"], "count": 2,
                                   "allow_unequal_versions": True}, headers=HEADERS)
        self.assertEqual(assigned.status_code, 200, assigned.json)
        chart = assigned.json
        first = next(student for student in chart["students"] if student["exam_version"] == "A")
        second = next(student for student in chart["students"] if student["exam_version"] == "B")
        swapped = self.client.post(self.route + "/exam-versions/edit", json={"version": chart["version"],
                                  "seat_id": first["seat_id"], "target_seat_id": second["seat_id"]}, headers=HEADERS)
        self.assertEqual(swapped.status_code, 200, swapped.json)
        changed = self.client.post(self.route + "/exam-versions/edit", json={"version": swapped.json["version"],
                                  "seat_id": first["seat_id"], "exam_version": "A"}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(records(), original)
        self.assertEqual(self.cards.remote.calls, remote_calls)
        self.assertEqual(self.relay.sent, [])


class ExamVersionOrderingTests(unittest.TestCase):
    def profile(self, filename):
        path = Path(__file__).resolve().parents[1] / "room_pdf_sources" / filename
        if not path.exists():
            self.skipTest("the public copy leaves out the reviewed ILCB plans")
        data = json.loads(path.read_text())
        return {"layout": data["layout"], "seats": [{**seat, "id": seat["label"]} for seat in data["seats"]]}

    def test_reviewed_floorplans_use_source_row_letters_and_natural_seat_order(self):
        cases = [("ilcb-111.json", ["103C", "104C", "107C", "108C", "110C", "201C", "301C", "401C"], ["Row C"]),
                 ("ilcb-207.json", ["A1", "B1", "B4", "C1", "D1", "E1", "F1"], [f"Row {letter}" for letter in "ABCDEF"]),
                 ("ilcb-229.json", ["1A · 1", "1B · 2", "2A · 1", "2A · 8", "2D · 8", "3A · 1"], ["Row 1", "Row 2", "Row 3"]),
                 ("ilcb-233.json", ["Table 1 · 2", "Table 2 · 1", "Table 2 · 5", "Table 10 · 1"], ["Table 1", "Table 2", "Table 10"])]
        for filename, occupied, labels in cases:
            with self.subTest(filename=filename):
                room = self.profile(filename)
                occupants = dict.fromkeys(reversed(occupied))
                rows = occupied_rows(room, occupants)
                self.assertEqual([row["label"] for row in rows], labels)
                self.assertEqual([seat["id"] for row in rows for seat in row["seats"]], occupied)
                assigned = automatic_versions(room, occupants, 3)
                self.assertEqual([assigned[key] for key in occupied], ["ABC"[index % 3] for index in range(len(occupied))])

    def test_workbook_tables_and_empty_aisles_do_not_restart_a_row(self):
        seats = [{"id": label, "label": label, "row": 1, "column": column, "row_label": "K", "seat_index": index}
                 for label, column, index in [("K Table 1 · 1", 2, 1), ("K Table 1 · 3", 2, 3), ("K1", 8, None),
                                              ("K Table 2 · 2", 12, 2), ("K Table 2 · 4", 12, 4)]]
        room = {"layout": {}, "seats": list(reversed(seats))}
        occupants = {seat["id"]: None for seat in seats}
        assigned = automatic_versions(room, occupants, 2)
        self.assertEqual(list(assigned.values()), ["A", "B", "A", "B", "A"])
        roster = [{"seat_id": seat["id"], "enrollment_id": str(index), "name": str(index), "section_number": 1, "status": "seated"}
                  for index, seat in enumerate(seats)]
        assigned[seats[1]["id"]] = "A"
        result = snapshot(room, roster, 2, assigned, True)
        self.assertEqual(result["conflicts"], [{"row": "Row K", "seats": [seats[0]["id"], seats[1]["id"]], "version": "A"},
                                               {"row": "Row K", "seats": [seats[1]["id"], seats[2]["id"]], "version": "A"}])

    def test_161_students_have_balanced_default_totals_for_every_supported_count(self):
        room = {"seats": [{"id": str(index), "label": f"{index // 17 + 1}-{index % 17 + 1}",
                           "row": index // 17 + 1, "column": index % 17 + 1} for index in range(161)]}
        occupants = dict.fromkeys(seat["id"] for seat in room["seats"])
        for count in range(1, 27):
            assigned = automatic_versions(room, occupants, count)
            totals = Counter(assigned.values())
            if count == 2:
                self.assertEqual(totals, {"A": 81, "B": 80})
            values = [totals[chr(65 + index)] for index in range(count)]
            self.assertLessEqual(max(values) - min(values), 1)
            for row in occupied_rows(room, occupants):
                letters = [assigned[seat["id"]] for seat in row["seats"]]
                if count > 1:
                    self.assertTrue(all(a != b for a, b in zip(letters, letters[1:])))

    def test_relaxed_mode_reduces_nearby_row_repeats_with_unequal_totals(self):
        # Rows overlap at columns 3 and 5. The balanced cycle matches versions
        # there; relaxing the quota removes both near repeats, leaving only
        # the more distant diagonal pairs at either end of the two rows.
        seats = [{"id": f"{row}{column}", "label": f"{row}{column}", "row_label": row,
                  "row": index, "column": column}
                 for index, (row, columns) in enumerate((("A", (1, 3, 5)), ("B", (3, 5, 7))), 1) for column in columns]
        room = {"seats": seats}
        occupied = dict.fromkeys(seat["id"] for seat in seats)
        balanced = automatic_versions(room, occupied, 2)
        relaxed = automatic_versions(room, occupied, 2, allow_unequal_versions=True)
        self.assertEqual(Counter(balanced.values()), {"A": 3, "B": 3})
        self.assertEqual(Counter(relaxed.values()), {"A": 2, "B": 4})
        for column in (3, 5):
            self.assertEqual(balanced[f"A{column}"], balanced[f"B{column}"])
            self.assertNotEqual(relaxed[f"A{column}"], relaxed[f"B{column}"])
        # Squared distances are 1 for the overlapping pairs and 5 for each
        # end pair: the weighted same-version objective drops from 2 to 0.4.
        pairs = [("A1", "B3", 0.2), ("A3", "B3", 1), ("A5", "B5", 1), ("A5", "B7", 0.2)]
        penalty = lambda assignment: sum(weight for a, b, weight in pairs if assignment[a] == assignment[b])
        self.assertAlmostEqual(penalty(balanced), 2)
        self.assertAlmostEqual(penalty(relaxed), 0.4)
        reordered = {"seats": list(reversed(seats))}
        self.assertEqual(automatic_versions(reordered, dict(reversed(list(occupied.items()))), 2, allow_unequal_versions=True), relaxed)
        for row in occupied_rows(room, occupied):
            values = [relaxed[seat["id"]] for seat in row["seats"]]
            self.assertTrue(all(a != b for a, b in zip(values, values[1:])))

    def test_relaxed_floorplans_use_physical_boxes_and_keep_hard_row_constraint(self):
        # Grid columns deliberately line up while retained floorplan boxes do
        # not; the optimizer must use the source PDF's actual seat centers.
        seats = [{"id": f"{row}{index}", "label": f"{row}{index}", "row_label": row, "row": row_index, "column": index}
                 for row_index, row in enumerate("AB", 1) for index in (1, 2, 3)]
        boxes = {seat["label"]: {"x": (seat["column"] - 1) * 2 + (2 if seat["row_label"] == "B" else 0),
                                 "y": seat["row"], "width": 1, "height": 1} for seat in seats}
        room = {"seats": seats, "layout": {"kind": "floorplan", "seats": boxes}}
        occupied = dict.fromkeys(seat["id"] for seat in seats)
        assigned = automatic_versions(room, occupied, 2, allow_unequal_versions=True)
        self.assertEqual(Counter(assigned.values()), {"A": 2, "B": 4})
        self.assertNotEqual(assigned["A2"], assigned["B1"])
        self.assertNotEqual(assigned["A3"], assigned["B2"])
        # Reviewed real geometry also keeps the row guarantee for 2–26 letters.
        physical = self.profile("ilcb-207.json")
        occupied = dict.fromkeys(seat["id"] for seat in physical["seats"])
        for count in (1, 2, 3, 5, 26):
            assigned = automatic_versions(physical, occupied, count, allow_unequal_versions=True)
            self.assertEqual(set(assigned), set(occupied))
            for row in occupied_rows(physical, occupied):
                letters = [assigned[seat["id"]] for seat in row["seats"]]
                if count > 1:
                    self.assertTrue(all(a != b for a, b in zip(letters, letters[1:])))


if __name__ == "__main__":
    unittest.main()
