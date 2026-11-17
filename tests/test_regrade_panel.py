"""Regrade requests answered in one panel on the Return stage (REGRADES, requirement, 2026-10-07): the list names the student, a request's panel says where its grade is, a model
drafts the reply (with nothing identifying sent), and Apply carries a changed grade to Gradescope, posts the reply only once Gradescope
shows the new score, certifies just this change into the gradebook, and stops for the instructor on anything unexpected. Offline,
against the fake Gradescope and a scripted model host."""
import json
import threading
import unittest
import uuid
from unittest.mock import patch

from grading.gradescope_sync import GradescopeSender
from rosters import connect
from test_grading_certification import HEADERS, Certify
from test_grading_cloud import endpoint
from fake_openai import FakeHost, images_of, payload_of, prompt_name


def _with_a_question_nobody_answered(coverage_detail):
    """Quiz 6's version E: a question of the plan whose rubric Gradescope has no copy of, and no answer yet."""
    def read(*args, **kwargs):
        detail = coverage_detail(*args, **kwargs)
        return {**detail, "unsendable": {**detail["unsendable"], "question-of-a-makeup": "Not created on Gradescope."}}
    return staticmethod(read)


class PanelTests(Certify):
    def setUp(self):
        super().setUp()
        self.send()
        self.sender.drain()
        self.certify()
        self.record = self.app.extensions["communications"]
        self.record.gradescope_settings = self.settings
        self.request = self.fake.add_regrade(self.qsub("q20", "A", self.alice)["id"], "I redid this and got 15.27, Alice Example 123456789. Please look again.")
        with self.record.lock():
            self.assertEqual(self.record.round("gradescope")["status"], "ok")
        self.base = f"/api/grading/assignments/{self.aid}/regrade-requests"
        self.message = self.client.get(self.base).json["requests"][0]["id"]
        # Apply's own sender is the routed one; the fake's drained by the test's sender (the same catalog).
        self.app.extensions["regrade_panel"].sender.settings = self.settings

    def get(self, path=""):
        answer = self.client.get(self.base + path)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def post(self, path, body=None, status=200):
        answer = self.client.post(self.base + path, json=body or {}, headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def apply(self, reply, published=None, status=200):
        preview = self.post(f"/{self.message}/apply/preview", {"reply": reply})
        return preview, self.post(f"/{self.message}/apply", {"reply": reply, "fingerprint": preview["fingerprint"], "request_id": str(uuid.uuid4()),
                                                             "confirmation": "apply", "published": published or []}, status)

    def steps(self, view):
        return [(s["step"], s["status"]) for s in view["steps"]]

    # --- reading -------------------------------------------------------------------------------------------------------------------
    def test_the_return_list_names_the_student_and_the_panel_says_where_the_grade_is(self):
        listed = self.get("/panel")
        item = listed["requests"][0]
        self.assertEqual((item["student"]["name"], item["faculty"]), (self.name(self.row1), {"points": "4", "of": "10"}))
        self.assertNotIn("student", self.get()["requests"][0])  # the Review step's list stays anonymous
        detail = self.get(f"/{self.message}")
        where = detail["where"]
        self.assertEqual((detail["student"]["name"], detail["state"], detail["can_reply"]), (self.name(self.row1), "open", True))
        self.assertEqual((where["faculty"]["points"], where["gradescope"], where["certified"]["earned"], where["gradebook"]),
                         ("4", {"points": "4", "state": "sent"}, "10", {"value": "10", "source": "grading"}))
        self.assertIsNone(where["canvas"])

    # --- Apply ---------------------------------------------------------------------------------------------------------------------
    def test_a_changed_grade_goes_to_gradescope_then_the_reply_then_the_gradebook(self):
        self.decide("q20", "A", ["R3_3"])  # 4 -> 2
        preview, run = self.apply("You lost 2 points for the final answer.")
        self.assertEqual([(s["step"], s.get("from"), s.get("to")) for s in preview["steps"]],
                         [("gradescope", "4", "2"), ("reply", None, None), ("gradebook", "10", "8")])
        self.assertTrue(any("Canvas" in n for n in preview["notes"]))  # no Canvas course linked in this workspace
        self.assertEqual(self.steps(run), [("gradescope", "waiting"), ("reply", "pending"), ("gradebook", "pending")])
        self.assertEqual([r for r in self.fake.requests if r["method"] == "PUT"], [])  # no reply before the grade is there
        self.sender.drain()
        done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done"), ("gradebook", "done")])
        self.assertTrue(done["finished"])
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)
        self.assertEqual((self.request["staff_comment"], self.request["closed"]), ("You lost 2 points for the final answer.", True))
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.state()["state"], "current")
        self.assertEqual(self.get(f"/{self.message}")["state"], "answered")
        again = self.post(f"/applies/{run['id']}/continue")  # a repeated post writes nothing
        self.assertEqual(again["steps"], done["steps"])
        self.assertEqual(len([r for r in self.fake.requests if r["method"] == "PUT"]), 1)
        self.assertNothingForbidden()

    def test_keeping_the_grade_is_the_reply_alone(self):
        preview, run = self.apply("The full-credit line asks for the exact value.")
        self.assertEqual([s["step"] for s in preview["steps"]], ["reply"])
        self.assertEqual(self.steps(run), [("reply", "done")])
        self.assertTrue(run["finished"])
        self.assertEqual(self.request["staff_comment"], "The full-credit line asks for the exact value.")
        self.assertEqual(len(self.fake.saves()), 4)  # only the first send's grades

    def test_keeping_a_grade_as_certified_is_the_reply_alone_whatever_else_changed(self):
        # A GRADE AS CERTIFIED (requirement, 2026-10-22): another grade changed since certifying, yet the kept one is what the
        # gradebook holds, so no gradebook step stops the request.
        self.decide("q21", "B", ["R4_3"])
        self.assertEqual(self.state()["state"], "changed")
        preview, run = self.apply("The full-credit line asks for the exact value.")
        self.assertEqual([s["step"] for s in preview["steps"]], ["reply"])
        self.assertFalse(any("certify" in n for n in preview["notes"]))
        self.assertEqual(self.steps(run), [("reply", "done")])
        self.assertTrue(run["finished"])
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))

    def test_someone_else_s_grade_on_gradescope_stops_it_before_the_reply(self):
        self.decide("q20", "A", ["R3_3"])
        _, run = self.apply("Changed to 2.")
        self.fake.grade(self.qsub("q20", "A", self.alice)["id"], applied=[self.item("q20", "A", 1)])  # a TA graded it there meanwhile
        self.sender.drain()
        stopped = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(stopped), [("gradescope", "stopped"), ("reply", "pending"), ("gradebook", "pending")])
        self.assertIn("never overwrites", stopped["steps"][0]["detail"])
        self.assertEqual([r for r in self.fake.requests if r["method"] == "PUT"], [])
        self.assertFalse(self.request["closed"])
        self.assertEqual(self.state()["state"], "changed")

    def test_a_published_exam_is_written_only_with_its_confirmation(self):
        self.decide("q20", "A", ["R3_3"])
        self.fake.assignments[self.container]["published"] = True
        preview = self.post(f"/{self.message}/apply/preview", {"reply": "Changed to 2."})
        self.assertEqual(preview["steps"][0]["published"], ["Common Exam 1 - Partial Credit"])
        self.post(f"/{self.message}/apply", {"reply": "Changed to 2.", "fingerprint": preview["fingerprint"], "request_id": str(uuid.uuid4()),
                                             "confirmation": "apply"}, 400)
        _, run = self.apply("Changed to 2.", published=preview["published"])
        self.sender.drain()
        self.assertEqual(self.steps(self.post(f"/applies/{run['id']}/continue"))[0], ("gradescope", "done"))

    def test_another_changed_grade_never_stops_the_gradebook_step(self):
        # A REGRADE NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-11-16): the request's grade goes to its cell; the other change keeps
        # waiting for Certify, its cell as certified.
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "B", ["R4_3"])
        other = self.cells()[self.row2]
        preview, run = self.apply("Changed to 2.")
        gradebook = next(s for s in preview["steps"] if s["step"] == "gradebook")
        self.assertNotIn("problem", gradebook)
        self.assertFalse(any("ertify" in n for n in preview["notes"]))
        self.sender.drain()
        done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done)[:3], [("gradescope", "done"), ("reply", "done"), ("gradebook", "done")])
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.cells()[self.row2], other)
        self.assertEqual(self.state()["state"], "changed")

    def test_a_student_moved_since_certifying_never_stops_the_gradebook_step(self):
        # Requirement, 2026-11-16, of Quiz 8's regrade stopped at Gradebook because a classmate was now left out until their makeup
        # ("regrade requests shouldn't trigger recertification"): the grade goes to its cell, the classmate's row stays as certified
        # and waits for the automatic certification or Certify.
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.certify()  # Carol: 0 with no work
        self.decide("q20", "A", ["R3_3"])
        _, run = self.apply("Changed to 2.")
        self.app.extensions["absences"].create({"request_id": str(uuid.uuid4()), "section_id": self.section["id"], "enrollment_id": self.carol,
                                                "first_date": "2026-09-25", "last_date": "2026-09-25", "reason": "illness", "status": "excused"})
        self.sender.drain()
        done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done"), ("gradebook", "done")], done["steps"][2]["detail"])
        self.assertTrue(done["finished"])
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        with connect(self.path) as db:
            first, second = [dict(r) for r in db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 2", (self.aid,))][::-1]
            carol = lambda cid: db.execute("SELECT status,reason FROM frq_certification_students WHERE certification_id=? AND enrollment_id=?", (cid, self.carol)).fetchone()
            self.assertEqual(tuple(carol(second["id"])), tuple(carol(first["id"])))
        amends = json.loads(second["summary_json"])["amends"]
        self.assertEqual(amends["markers"], "carried")
        self.assertIn("is now left out until their makeup, was 0 with no work", " ".join(amends["moved"]))
        # Carol's makeup is then Faculty's to certify by itself (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES"), on top of the regrade.
        from grading.certification_automatic import LateCertification
        taken = LateCertification(self.service).run(self.aid)
        self.assertEqual((taken["state"], taken["changes"], taken["markers"]), ("done", {self.row3: "left_out"}, "live"), taken)
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def _faculty_carries_carol_behind_the_regrade(self):
        """Quiz 8, 2026-11-16: the regrade's grade is saved (and so uncertified) when Carol, certified 0, becomes owed a makeup, and
        Faculty certifies her change by itself with the markers carried, keeping the lists as the instructor confirmed them."""
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.certify()  # Carol: 0 with no work
        self.decide("q20", "A", ["R3_3"])
        absence = self.absence(self.carol, "2026-09-25", status="excused")
        from grading.certification_automatic import LateCertification
        taken = LateCertification(self.service).run(self.aid)
        self.assertEqual((taken["state"], taken["changes"], taken["markers"]), ("done", {self.row3: "left_out"}, "carried"), taken)
        return absence

    def confirmations_now(self):
        """{check: (stored digest, live digest)} of the student lists the instructor confirms."""
        with connect(self.path) as db:
            stored = json.loads(db.execute("SELECT confirmations_json FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1",
                                           (self.aid,)).fetchone()[0])
        live = {c["id"]: c["digest"] for c in self.service.view(self.aid)["checks"] if c["level"] == "confirm"}
        return {k: ((stored.get(k) or {}).get("digest"), live.get(k)) for k in ("zero_no_work", "makeup_ahead") if k in live or k in stored}

    def test_a_regrade_after_faculty_carried_a_makeup_is_live_with_the_lists_as_they_are(self):
        # A REGRADE CARRIES THE CHAIN (requirement, 2026-11-16): the
        # regrade's certification counts Carol as she stood before Faculty's own, so it reads live and keeps the lists as they are now.
        self._faculty_carries_carol_behind_the_regrade()
        _, run = self.apply("Changed to 2.")
        self.sender.drain()
        done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done"), ("gradebook", "done")], done["steps"][2]["detail"])
        with connect(self.path) as db:
            latest = db.execute("SELECT summary_json FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (self.aid,)).fetchone()
        self.assertEqual(json.loads(latest[0])["amends"]["markers"], "live")
        mismatch = {k: v for k, v in self.confirmations_now().items() if v[0] != v[1]}
        self.assertEqual(mismatch, {})
        self.assertEqual((self.state()["state"], self.cells()[self.row1]), ("current", ("scored", "8", "grading")))

    def test_faculty_follows_its_chain_through_a_regrade_that_carried_it(self):
        # The certification made on live at 18:19 before this fix: the regrade carried the chain and copied the old lists. Faculty's
        # next certification of its own (Carol's makeup ends: she is given 0) follows the chain through it and reads live again.
        absence = self._faculty_carries_carol_behind_the_regrade()
        from grading import certification_automatic
        with patch.object(certification_automatic, "carried_chain", lambda db, latest: (latest, latest, [])):
            _, run = self.apply("Changed to 2.")
            self.sender.drain()
            self.post(f"/applies/{run['id']}/continue")
        with connect(self.path) as db:
            latest = dict(db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (self.aid,)).fetchone())
            self.assertEqual(json.loads(latest["summary_json"])["amends"]["markers"], "carried")
            base, rows_base, pending = certification_automatic.carried_chain(db, latest)
            self.assertEqual((base["id"], pending), (latest["id"], [self.row3]))
            self.assertNotEqual(rows_base["id"], latest["id"])
        self.decide_absence(absence, "not_excused")
        taken = certification_automatic.LateCertification(self.service).run(self.aid)
        self.assertEqual((taken["state"], taken["changes"], taken["markers"]), ("done", {self.row3: "zero"}, "live"), taken)
        self.assertEqual({k: v for k, v in self.confirmations_now().items() if v[0] != v[1]}, {})
        self.assertEqual((self.state()["state"], self.cells()[self.row1]), ("current", ("scored", "8", "grading")))

    def test_a_classmates_drop_and_an_unsat_version_never_stop_the_gradebook_step(self):
        # GRADING "A DROP NEVER ASKS TO CERTIFY AGAIN" (requirement, 2026-11-03). The dropped student's certified 0 stays as certified.
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.certify()  # Carol: 0 with no work
        self.decide("q20", "A", ["R3_3"])
        _, run = self.apply("Changed to 2.")
        with self.frq.rosters.lock(), connect(self.path) as db:
            db.execute("""INSERT INTO roster_drops(id,section_id,enrollment_id,action,source,reason,request_id,actor,created_at)
                VALUES(?,?,?,'drop','roster','Q drop',?,'roster import','2026-09-27T09:00:00+00:00')""",
                       (str(uuid.uuid4()), self.section["id"], self.carol, str(uuid.uuid4())))
        self.sender.drain()
        with patch("grading.gradescope_sync.GradescopeSender.coverage_detail", _with_a_question_nobody_answered(GradescopeSender.coverage_detail)):
            done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done"), ("gradebook", "done")], done["steps"][2]["detail"])
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        with connect(self.path) as db:
            first, second = [r[0] for r in db.execute("SELECT id FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 2", (self.aid,))][::-1]
            rows = lambda cid: dict(db.execute("SELECT enrollment_id,status||':'||earned||':'||parts_json FROM frq_certification_students WHERE certification_id=?", (cid,)).fetchall())
            self.assertEqual(rows(second)[self.carol], rows(first)[self.carol])
            self.assertEqual(json.loads(db.execute("SELECT confirmations_json FROM frq_certifications WHERE id=?", (second,)).fetchone()[0])["zero_no_work"]["count"], 1)
        self.assertEqual(self.state()["state"], "current")

    def test_a_changed_preview_is_refused_and_nothing_is_written(self):
        self.decide("q20", "A", ["R3_3"])
        preview = self.post(f"/{self.message}/apply/preview", {"reply": "Changed to 2."})
        self.decide("q20", "A", ["R3_4"])
        self.post(f"/{self.message}/apply", {"reply": "Changed to 2.", "fingerprint": preview["fingerprint"], "request_id": str(uuid.uuid4()),
                                             "confirmation": "apply"}, 409)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_regrade_applies").fetchone()[0], 0)


class DropTests(unittest.TestCase):
    """A DROP NEVER STOPS IT, without a workspace: only a drop of a student certified 0 with no work is excused."""

    @staticmethod
    def state(zero_now):
        def record(enrollment, status="zero", reason="no_paper"):
            return {"section_id": "s", "enrollment_id": enrollment, "status": status, "reason": reason}
        return {"records": [record(e) for e in zero_now], "participants": [{"section_id": "s", "enrollment_id": "gone", "dropped": True}],
                "certified_rows": {f"s:{e}": record(e) for e in ("gone", "stays", "moved")},
                "students": [{"row": f"s:{e}", "status": "zero", "reason": "no_paper"} for e in zero_now]}

    def test_the_confirmation_holds_with_the_dropped_as_they_were_and_nothing_else(self):
        from grading import certification_rules as rules
        from grading.certification_change import _confirmation_holds, dropped_no_work, who_moved
        confirmed = {"digest": rules.members_digest([f"s:{e}|no_paper" for e in ("gone", "stays", "moved")])}
        settled = self.state(["stays", "moved"])
        self.assertEqual(set(dropped_no_work(settled)), {"s:gone"})
        check = {"id": "zero_no_work", "digest": rules.members_digest(["s:stays|no_paper", "s:moved|no_paper"])}
        self.assertTrue(_confirmation_holds(check, confirmed, settled, dropped_no_work(settled)))
        self.assertEqual(who_moved(settled), [])
        # "moved" left the list without being dropped: that is a change, and it is named.
        moved = self.state(["stays"])
        self.assertEqual(set(dropped_no_work(moved)), {"s:gone"})
        self.assertFalse(_confirmation_holds({**check, "digest": rules.members_digest(["s:stays|no_paper"])}, confirmed, moved, dropped_no_work(moved)))
        self.assertEqual(who_moved(moved), ["A student is now not in the assignment, was 0 with no work"])
        # Another confirmation's digest is never excused by a drop.
        self.assertFalse(_confirmation_holds({"id": "makeup_ahead", "digest": "x"}, {"digest": "y"}, settled, dropped_no_work(settled)))


class CanvasStepTests(PanelTests):
    """The Canvas step, with the one-cell send stubbed (this workspace links no Canvas course; tests/test_canvas_cell.py tests the send)."""

    def test_the_cell_goes_to_canvas_once_the_gradebook_holds_the_new_value(self):
        shown = {"canvas": {"status": "scored", "points": "10"}}
        sent = []

        def preview(svc, term_id, data):
            from gradebook.service import _cells
            with connect(self.path) as db:
                cell = (_cells(db, term_id).get(data["item_id"]) or {}).get(data["row"])
            faculty = {"status": cell["status"], "points": cell["points"]}
            same = faculty == shown["canvas"]
            return {"item_id": data["item_id"], "row": data["row"], "course": "MATH-140-501", "fingerprint": "f" * 64, "problems": [], "notes": [],
                    "action": "same" if same else "update", "canvas": shown["canvas"], "faculty": faculty, "ready": not same}

        def send(svc, term_id, data, actor="workspace"):
            sent.append(data)
            section, enrollment = data["row"].split(":")
            with connect(self.path) as db:
                db.execute("INSERT INTO gradebook_canvas_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("cell-send", term_id, section, data["request_id"], "x" * 64, data["fingerprint"], "[]", "done", "{}", actor, "2026-10-07T00:00:00Z", None))
                db.execute("""INSERT INTO gradebook_canvas_sent VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           ("receipt", "cell-send", term_id, section, enrollment, data["item_id"], None, "1001", "2001", "3001",
                            json.dumps({"status": "scored", "points": "8"}), json.dumps({"status": "scored", "points": "8"}), "accepted", "2026-10-07T00:00:01Z"))
            shown["canvas"] = {"status": "scored", "points": "8"}
            return {"send": {"id": "cell-send", "status": "done"}, "replay": False}
        self.enterContext(patch("grading.regrade_apply.canvas_cell.preview", side_effect=preview))
        self.enterContext(patch("grading.regrade_apply.canvas_cell.send", side_effect=send))
        self.decide("q20", "A", ["R3_3"])
        preview_, run = self.apply("Changed to 2.")
        self.assertEqual([(s["step"], s.get("from"), s.get("to")) for s in preview_["steps"]][-1], ("canvas", "10", "8"))
        self.sender.drain()
        done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done"), ("gradebook", "done"), ("canvas", "done")])
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.get(f"/{self.message}")["where"]["canvas"]["value"], "8")


class DraftTests(Certify):
    def setUp(self):
        super().setUp()
        self.send()
        self.sender.drain()
        self.certify()
        record = self.app.extensions["communications"]
        record.gradescope_settings = self.settings
        roster = self.name(self.row1)  # "Example, Alice" or "Example, Bob": the fixture's enrollment IDs are random
        self.fake.add_regrade(self.qsub("q20", "A", self.alice)["id"], f"{roster.split(', ')[1]} Example here (123456789): 15.27 should be right.")
        with record.lock():
            record.round("gradescope")
        self.base = f"/api/grading/assignments/{self.aid}/regrade-requests"
        self.message = self.client.get(self.base).json["requests"][0]["id"]
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        self.asked = []
        self.host.script = self.answer
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": [*current["settings"]["endpoints"], endpoint(self.host, "alpha-vision")],
                                                                             "rubric_reviewer": "alpha-vision", "max_retries": 3}})

    def answer(self, body, host):
        if prompt_name(body) != "regrade_reply":
            return ("status", 500, {}, json.dumps({"error": "not scripted"}))
        self.asked.append((payload_of(body), len(images_of(body))))
        return {"reply": "You lost 6 points because the final answer is not the exact value.", "assessment": "15.27 is the key rounded."}

    def test_two_panels_requesting_the_same_grade_queue_one_draft(self):
        drafts = self.app.extensions["regrade_panel"].drafts
        arrived = threading.Barrier(2)
        original = drafts._model
        outcomes, failures = [], []

        def model():
            found = original()
            arrived.wait(timeout=10)  # both callers passed the first same-input lookup
            return found

        def request():
            try:
                outcomes.append(drafts.request(self.aid, self.message))
            except Exception as error:
                failures.append(error)

        with patch.object(drafts, "_model", side_effect=model):
            threads = [threading.Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0]["id"], outcomes[1]["id"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_regrade_drafts").fetchone()[0], 1)
        self.assertEqual(self.asked, [])

    def test_the_draft_reads_the_grade_and_the_work_and_never_who_wrote_it(self):
        found = self.client.post(f"{self.base}/{self.message}/draft", json={}, headers=HEADERS).json
        self.assertEqual((found["status"], found["kind"]), ("pending", "keep"))
        again = self.client.post(f"{self.base}/{self.message}/draft", json={}, headers=HEADERS).json
        self.assertEqual(again["id"], found["id"])  # one draft per grade
        self.frq.process_cloud()
        draft = self.client.get(f"{self.base}/{self.message}").json["draft"]
        self.assertEqual(draft["status"], "complete", draft)
        first = self.name(self.row1).split(", ")[1]
        self.assertEqual(draft["reply"], f"Hi {first},\n\nYou lost 6 points because the final answer is not the exact value.")
        self.assertEqual(draft["assessment"], "15.27 is the key rounded.")
        payload, pictures = self.asked[0]
        text = json.dumps(payload)
        for secret in (*self.name(self.row1).split(", "), "123456789"):
            self.assertNotIn(secret, text)
        self.assertIn("[name]", payload["request"])
        self.assertEqual((payload["grade"], payload["earlier_score"], payload["pictures"]), ({"score": "4", "of": "10"}, None, pictures))
        self.assertTrue(any(line["applied"] for line in payload["rubric_lines"]))
        # A changed grade asks for a new draft, which says what it was.
        self.decide("q20", "A", ["R3_3"])
        changed = self.client.post(f"{self.base}/{self.message}/draft", json={}, headers=HEADERS).json
        self.assertNotEqual(changed["id"], found["id"])
        self.assertEqual(changed["kind"], "change")
        self.frq.process_cloud()
        self.assertEqual(self.asked[-1][0]["earlier_score"], "4")


def load_tests(loader, tests, pattern):
    """Only this module's own tests (the certification tests it builds on run in their own module)."""
    suite = unittest.TestSuite()
    for case in (PanelTests, DropTests, CanvasStepTests, DraftTests):
        for name in loader.getTestCaseNames(case):
            if name in case.__dict__:
                suite.addTest(case(name))
    return suite


if __name__ == "__main__":
    unittest.main()
