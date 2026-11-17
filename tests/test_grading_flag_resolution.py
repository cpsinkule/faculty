"""Flag resolution (grading/flag_resolution.py): the rubric model rules on a question's held grades, 25 at a time with their crops, and
the grades it settles are scored again by an ordinary batch. Against the local fake host: no live inference."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from grading import flag_look, flag_resolution as fr, risk, schemas
from grading.providers.local_vlm import VERSIONS, prompt
from rosters import connect
from fake_openai import arbitrate_answer, flag_look_answer, flag_resolution_answer, grade_answer, images_of, payload_of, prompt_name
import test_grading_arbitration as arbitration_fixture
from test_grading_pipeline import CORRECT


class Rules(unittest.TestCase):
    def test_only_the_instructors_own_flags_are_reserved_and_rubric_doubts_are_not(self):
        for text in (risk.NO_SECOND, risk.EMPTY_SELECTION, risk.UNSCORABLE, risk.BLANK_PREFIX + " crop 1; check whether it was left blank.",
                     "primary: " + risk.NOBODY_ELSE):
            self.assertTrue(fr.reserved(text), text)
        # A negative rubric's full credit nobody ticked is sent (empty-selection-v2, 2026-10-19): a ruling may tick a line.
        for text in (risk.WORDING, risk.CONDITION, risk.EMPTY_FULL, "Rubric condition R7: Nonnegativity missing", "The handwriting is ambiguous in places.",
                     "The primary grader and the second grader disagree on R1, R2; review the rubric selections individually."):
            self.assertFalse(fr.reserved(text), text)

    def test_a_chunk_holds_at_most_25_responses_and_is_cut_short_by_its_images(self):
        rows = [{"images": [b"x"]} for _ in range(60)]
        self.assertEqual([len(c) for c in fr.FlagResolutionMixin._chunks(rows)], [25, 25, 10])
        heavy = [{"images": [b"x"] * 40} for _ in range(5)]
        self.assertEqual([len(c) for c in fr.FlagResolutionMixin._chunks(heavy)], [2, 2, 1])

    def test_a_missing_or_malformed_ruling_fails_only_its_own_response(self):
        rubric = {"items": [{"id": "R1"}, {"id": "R2"}]}
        row = lambda ref: {"ref": ref, "labels": ["standard.primary"], "flags": [{"id": "F1", "text": "x"}], "graded": lambda v: v, "rubric": rubric}
        found, clarifications, _ = fr.rulings_of({"rulings": [
            {"ref": "R1", "decision": "adopt", "adopt": "standard.primary", "flag_rulings": [{"flag": "F1", "resolved": True, "note": "ok"}]},
            {"ref": "R2", "decision": "adopt", "adopt": "standard.nobody", "flag_rulings": []},
            {"ref": "R9", "decision": "adopt", "adopt": "standard.primary"}],
            "clarifications": [{"line_id": "R1", "clarification": "Count C ≥ 0.", "why": "R1"}, {"line_id": "R77", "clarification": "unknown line"}]},
            [row("R1"), row("R2"), row("R3")])
        self.assertEqual(found["R1"]["decision"], "adopt")
        self.assertTrue(found["R2"]["failed"] and found["R3"]["failed"])
        self.assertNotIn("R9", found)
        self.assertEqual([c["line_id"] for c in clarifications], ["R1"])
        with self.assertRaises(ValueError):
            fr.rulings_of({"summary": "no rulings"}, [row("R1")])


class Readings(unittest.TestCase):
    def test_readings_are_two_to_four_complete_distinct_sets_of_known_lines_or_none(self):
        known = {"R1", "R2", "R3"}
        two = [{"reading": "reads 4", "lines": ["R2", "R1"]}, {"reading": "reads -14", "lines": ["R1", "R2", "R3"]}]
        self.assertEqual(fr.readings_of({"readings": two}, known), [{"reading": "reads 4", "lines": ["R1", "R2"]}, {"reading": "reads -14", "lines": ["R1", "R2", "R3"]}])
        self.assertEqual(fr.readings_of({"readings": two[:1]}, known), [])                      # one reading is no choice
        self.assertEqual(fr.readings_of({"readings": two + [{"reading": "x", "lines": ["R9"]}]}, known), [])  # an unknown line
        self.assertEqual(fr.readings_of({"readings": [two[0], {**two[0], "reading": "same"}]}, known), [])    # two alike
        self.assertEqual(fr.readings_of({"readings": [{"reading": "", "lines": []}, two[1]]}, known), [])  # a reading says what it is
        self.assertEqual(fr.readings_of({}, known), [])
        self.assertEqual(fr.readings_of({"readings": "two"}, known), [])


class Looks(unittest.TestCase):
    """GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (requirement, 2026-11-14): what a look's answer may outline."""

    def test_a_spot_is_kept_only_on_a_crop_that_exists_with_a_box_inside_it_and_is_never_repaired(self):
        good = {"label": "  exponent   on 9 ", "crop": 1, "bbox": [.2, .3, .25, .36]}
        self.assertEqual(flag_look.spots_of({"spots": [good]}, 2), [{"label": "exponent on 9", "crop": 1, "bbox": [.2, .3, .25, .36]}])
        for bad in ({**good, "crop": 2}, {**good, "crop": -1}, {**good, "crop": "0"}, {**good, "crop": True}, {**good, "bbox": [.25, .3, .2, .36]},
                    {**good, "bbox": [.2, .3, .2, .36]}, {**good, "bbox": [200, 300, 250, 360]}, {**good, "bbox": [.2, .3, .25]}, {**good, "bbox": None},
                    {**good, "bbox": "left"}, "spot", None):
            self.assertEqual(flag_look.spots_of({"spots": [bad]}, 2), [], bad)
        self.assertEqual(flag_look.spots_of({"spots": [{"bbox": [0, 0, 1, 1]}]}, 1), [{"label": "", "crop": 0, "bbox": [0, 0, 1, 1]}])  # one crop: crop 0
        self.assertEqual([s["label"] for s in flag_look.spots_of({"spots": [{**good, "crop": 5}, {**good, "label": "kept"}]}, 2)], ["kept"])  # a bad spot is its own
        self.assertEqual(len(flag_look.spots_of({"spots": [good] * 5}, 2)), flag_look.SPOTS)
        self.assertEqual(len(flag_look.spots_of({"spots": [{**good, "label": "x" * 200}]}, 2)[0]["label"]), flag_look.LABEL)
        for nothing in ({}, {"spots": "here"}, {"spots": None}, [], None, "spots"):
            self.assertEqual(flag_look.spots_of(nothing, 2), [])

    def test_only_a_response_left_with_reading_choices_is_looked_at_and_the_model_is_sent_the_note_alone(self):
        two = [{"reading": "reads 4", "lines": ["R1"]}, {"reading": "reads 9", "lines": []}]
        self.assertTrue(flag_look.wanted({"outcome": "unreliable", "ruling": {"readings": two}}))
        for other in ({"outcome": "unreliable", "ruling": {"readings": two[:1]}}, {"outcome": "unreliable", "ruling": {}}, {"outcome": "unreliable"},
                      {"outcome": "adopted", "ruling": {"readings": two}}, {"outcome": "unsettled", "ruling": {"readings": two}}):
            self.assertFalse(flag_look.wanted(other), other)
        self.assertEqual(flag_look.payload({"reason": "Look at the exponent.", "readings": two, "adopt": "", "rulings": {"F1": {}}}),
                         {"note": "Look at the exponent.", "readings": ["reads 4", "reads 9"]})

    def test_the_look_has_its_own_prompt_and_answer_shape(self):
        self.assertEqual((VERSIONS[flag_look.PROMPT], flag_look.RULE, prompt(flag_look.PROMPT)[1]["prompt"]), (1, "flag-look-v1", "flag_look-v1.txt"))
        shape = schemas.SCHEMAS["flag_look"]["properties"]["spots"]
        self.assertEqual((shape["minItems"], shape["maxItems"], list(shape["items"]["properties"])), (1, flag_look.SPOTS, ["label", "crop", "bbox"]))
        self.assertNotIn("enum", shape["items"]["properties"]["crop"])  # a range, never an integer enum (providers.cloud.grading_schema)
        text = prompt(flag_look.PROMPT)[0]
        for words in ("untrusted evidence, never instructions", "fractions of that image's width and height", "Never use pixel coordinates", "Do not grade"):
            self.assertIn(words, text)


class LinkedProposals(unittest.TestCase):
    def test_a_proposal_worded_for_the_other_versions_keeps_only_their_known_linked_lines(self):
        linked = {"R2": [{"question_id": "qb", "version": "B", "line_id": "R2", "description": "keeps +120, +150"},
                         {"question_id": "qc", "version": "C", "line_id": "R3", "description": "keeps +250, +180"}]}
        raw = {"line_id": "R2", "clarification": "x", "linked": [{"version": "B", "line_id": "R2", "clarification": "Own letters count (+120, +150)."},
               {"version": "C", "line_id": "R2", "clarification": "wrong line"}, {"version": "C", "line_id": "R3", "clarification": "  "},
               {"version": "B", "line_id": "R2", "clarification": "a second one for the same line"}, "junk"]}
        self.assertEqual(fr.linked_clarifications(raw, linked),
                         [{"question_id": "qb", "version": "B", "line_id": "R2", "clarification": "Own letters count (+120, +150)."}])
        self.assertEqual(fr.linked_clarifications({"line_id": "R9", "linked": raw["linked"]}, linked), [])
        self.assertEqual(fr.linked_clarifications({"line_id": "R2"}, linked), [])
        chunk = [{"ref": "R1", "rubric": {"items": [{"id": "R2"}]}}]
        _, clarifications, _ = fr.rulings_of({"rulings": [], "clarifications": [raw]}, chunk, linked=linked)
        self.assertEqual([c["linked"][0]["question_id"] for c in clarifications], ["qb"])


class TranscriptChanges(unittest.TestCase):
    """v2 (2026-09-29): a ruling may correct or add the transcript lines the crop clearly shows, marked as the flag review's."""
    TRANSCRIPT = {"lines": [{"id": "line-1", "elements": [{"type": "math", "text": "3x = 18", "latex": "3x = 18", "crop": 0}]},
                            {"id": "line-2", "elements": [{"type": "math", "text": "x = 8", "latex": "x = 8", "crop": 0}]}],
                  "plain_text": "3x = 18\nx = 8", "latex": "3x = 18\nx = 8", "confidence": .9, "ambiguities": []}

    @staticmethod
    def change(line_id, text, after="", crop=0, state="written"):
        return {"line_id": line_id, "after": after, "crop": crop, "saw": "Seen on the crop.", "elements": [{"type": "math", "text": text, "latex": text, "state": state}]}

    def test_a_correction_keeps_the_readers_line_and_an_addition_goes_where_it_is_written(self):
        changes = fr.changes_of({"transcript_changes": [self.change("line-2", "x = 6"), self.change("fr-1", "(b)", after="line-1")]}, self.TRANSCRIPT, 1)
        self.assertEqual([(c["line_id"], c["kind"]) for c in changes], [("line-2", "correct"), ("fr-1", "add")])
        amended = fr.amend(self.TRANSCRIPT, changes)
        self.assertEqual([line["id"] for line in amended["lines"]], ["line-1", "fr-1", "line-2"])
        corrected = amended["lines"][2]
        self.assertEqual((corrected["source"], corrected["elements"][0]["text"], corrected["original"][0]["text"]), (fr.SOURCE, "x = 6", "x = 8"))
        self.assertEqual(amended["plain_text"], "3x = 18\n(b)\nx = 6")
        self.assertEqual(self.TRANSCRIPT["lines"][1]["elements"][0]["text"], "x = 8")  # the reader's transcript is never changed in place

    def test_a_change_must_name_a_real_crop_a_known_place_and_marks_the_student_made(self):
        for bad in ([self.change("fr-1", "(b)", after="line-9")], [self.change("line-1", "3x = 18", crop=1)],
                    [self.change("line-1", "3x = 18"), self.change("line-1", "3x = 18")], [self.change("line-1", "3x = 18", state="unclear")],
                    [self.change("line-1", " ")], [self.change(f"fr-{n}", "x") for n in range(fr.CHANGES + 1)]):
            with self.subTest(bad=bad[0]), self.assertRaises(ValueError):
                fr.changes_of({"transcript_changes": bad}, self.TRANSCRIPT, 1)
        with self.assertRaises(ValueError):  # nothing is changed without a crop to read it from
            fr.changes_of({"transcript_changes": [self.change("line-1", "3x = 18")]}, self.TRANSCRIPT, 0)

    def test_an_empty_element_is_dropped_not_refused(self):
        # Quiz 4, 2026-10-03: a scribbled-out sign sent as an empty crossed-out element refused the whole ruling.
        change = self.change("line-1", "3x")
        change["elements"] += [{"type": "math", "text": "", "latex": "", "state": "crossed_out"}, {"type": "math", "text": "= 18", "latex": "= 18", "state": "inserted"}]
        changes = fr.changes_of({"transcript_changes": [change]}, self.TRANSCRIPT, 1)
        self.assertEqual([(el["text"], el.get("state", "written")) for el in changes[0]["elements"]], [("3x", "written"), ("= 18", "inserted")])
        self.assertEqual(fr.amend(self.TRANSCRIPT, changes)["plain_text"], "3x = 18\nx = 8")

    def test_the_answer_speaks_its_own_words_and_a_page_is_asked_for_only_outside_a_page_round(self):
        row = {"ref": "R1", "labels": ["standard.primary"], "flags": [{"id": "F1", "text": "x"}], "graded": lambda v, amended=None: v,
               "rubric": {"items": [{"id": "R1"}]}, "transcript": self.TRANSCRIPT, "images": [b"crop"]}
        ruled = {"ref": "R1", "decision": "uphold", "uphold": "standard.primary", "needs_page": False, "transcript_changes": [],
                 "flag_rulings": [{"flag": "F1", "resolved": True, "note": "ok"}]}
        found, _, _ = fr.rulings_of({"rulings": [ruled]}, [row])
        self.assertEqual((found["R1"]["decision"], found["R1"]["adopt"]), ("adopt", "standard.primary"))
        found, _, _ = fr.rulings_of({"rulings": [{**ruled, "decision": "instructor", "uphold": "", "needs_page": True, "reason": "Cut off at the top."}]}, [row])
        self.assertEqual(found["R1"], {"needs_page": True, "reason": "Cut off at the top."})
        found, _, _ = fr.rulings_of({"rulings": [{**ruled, "needs_page": True}]}, [row], page_round=True)
        self.assertEqual((found["R1"]["decision"], found["R1"]["page_round"]), ("adopt", True))
        found, _, _ = fr.rulings_of({"rulings": [{**ruled, "transcript_changes": [self.change("fr-1", "x", after="line-9")]}]}, [row])
        self.assertTrue(found["R1"]["failed"])  # a line added after one that does not exist fails that ruling alone
        found, _, _ = fr.rulings_of({"rulings": [{**ruled, "transcript_changes": [self.change("line-2", "x = 6")]}]}, [row])
        self.assertEqual(found["R1"]["changes"][0]["kind"], "correct")


class FlagResolution(arbitration_fixture.FinalArbitrator):
    """A split team whose arbitrator leaves the response with the instructor, then a flag review of the question."""

    def held(self, team=None, primary_doubt=None):
        def ruling(payload):
            return arbitrate_answer(payload, "unreliable")
        def script(name, body):
            if name == "arbitrate":
                return ruling(payload_of(body))
            answer = self.opinions(**(self.SPLIT if team is None else team))(name, body)
            if primary_doubt and name == "grade" and isinstance(answer, dict):
                answer = {**answer, "rubric_ambiguities": [primary_doubt]}
            return answer
        response, _ = self.grade(CORRECT, script=script)
        self.assertNotEqual(response["result"]["state"], "proposed")
        self.assertFalse(response.get("acceptance"))
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "rubric_reviewer": "gamma-vision"}})
        return response

    def resolve(self, answer=None, look=None):
        self.host.script = lambda body, host: ((answer or flag_resolution_answer)(payload_of(body)) if prompt_name(body) == "flag_resolution"
                                               else (look or flag_look_answer)(payload_of(body)) if prompt_name(body) == "flag_look" else grade_answer(payload_of(body)))
        queued = self.frq.queue_flag_resolution(self.plan["id"], {"request_id": str(uuid.uuid4())})
        for _ in range(10):
            self.frq.process_cloud()
            with connect(self.path) as db:
                db.execute("UPDATE frq_flag_resolutions SET not_before='' WHERE status='pending'")
        self.run_all()
        return queued, self.frq.flag_resolution(self.plan["id"])["resolution"]

    def sent(self):
        return [r for r in self.host.requests if prompt_name(r["body"]) == "flag_resolution"]

    def progress_call(self, db, rid, step, state, confirmation=None):
        db.execute("""INSERT INTO frq_cloud_calls(id,flag_resolution_id,step,role,tier,endpoint,provider,requested_model,
            request_sha256,state,submitted_at,confirmation_json) VALUES(?,?,?,'rubric_reviewer','','gamma-vision','fake','fake',?,?,?,?)""",
            (str(uuid.uuid4()), rid, step, str(uuid.uuid4()), state, "2026-10-25T22:00:00+00:00", json.dumps(confirmation or {})))

    def test_progress_counts_real_replies_and_active_requests_beyond_the_estimate(self):
        """GRADING FLAG REVIEW PROGRESS: image splits and page rounds never cap the counter at an estimate."""
        self.held()
        queued = self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "progress"})
        self.assertEqual(queued["progress"], {"requests": 0, "answered": 0, "running": 0})
        with connect(self.path) as db:
            for index in range(25):
                self.progress_call(db, queued["id"], f"flags:q:{index}", "completed")
            for index in range(4):
                self.progress_call(db, queued["id"], f"flags:q:{index}:page:0", "completed")
            for index in range(7):
                self.progress_call(db, queued["id"], f"flags:q:{25 + index}", "submitted")
            db.execute("UPDATE frq_flag_resolutions SET status='running' WHERE id=?", (queued["id"],))
        expected = {"requests": 36, "answered": 29, "running": 7}
        for _ in range(2):  # A poll reuses the same receipts; it neither adds requests nor changes grades.
            current = self.frq.flag_resolution(self.plan["id"])["resolution"]
            self.assertEqual(current["progress"], expected)
            self.assertEqual(current["status"], "running")
            self.assertNotIn("result", current)

    def test_progress_names_a_usage_limit_wait_and_discards_a_recovered_wait(self):
        """GRADING FLAG REVIEW PROGRESS: a pause stays distinct from completion, and a saved reply ends its old wait."""
        self.held()
        queued = self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "progress-wait"})
        until = "2099-10-26T02:20:00+00:00"
        with connect(self.path) as db:
            self.progress_call(db, queued["id"], "flags:q:0", "completed")
            self.progress_call(db, queued["id"], "flags:q:0:page:0", "failed", {"status": 429, "usage_limit": True})
            db.execute("UPDATE frq_flag_resolutions SET not_before=? WHERE id=?", (until, queued["id"]))
        progress = self.frq.flag_resolution(self.plan["id"])["resolution"]["progress"]
        self.assertEqual(progress, {"requests": 2, "answered": 1, "running": 0, "wait": {"kind": "usage_limit", "until": until}})
        with connect(self.path) as db:
            self.progress_call(db, queued["id"], "flags:q:0:page:0", "completed")
            self.progress_call(db, queued["id"], "flags:q:1", "failed", {"status": 529})
        progress = self.frq.flag_resolution(self.plan["id"])["resolution"]["progress"]
        self.assertEqual(progress, {"requests": 4, "answered": 2, "running": 0, "wait": {"kind": "capacity", "until": until}})
        with connect(self.path) as db:
            db.execute("UPDATE frq_flag_resolutions SET status='running' WHERE id=?", (queued["id"],))
        self.assertNotIn("wait", self.frq.flag_resolution(self.plan["id"])["resolution"]["progress"])

    def test_reset_restores_the_original_flags_without_new_calls_or_erasing_history(self):
        """GRADING FLAG REVIEW RESET: the original result is current again, never a regrade or an erased ruling."""
        before = self.held()
        _, done = self.resolve()
        aid, rid = self.plan["id"], done["id"]
        newer = self.frq.response(before["id"])["current_result_id"]
        calls = len(self.sent())
        preview = self.frq.flag_reset_preview(aid, rid)
        self.assertEqual((preview["responses"], preview["restore"], preview["already_original"]), (1, 1, 0))
        from werkzeug.exceptions import Conflict
        with self.assertRaises(Conflict):
            self.frq.reset_flag_resolution(aid, rid, {"request_id": "stale", "confirmation": "reset", "fingerprint": "old"})
        command = {"request_id": "reset-flags", "confirmation": "reset", "fingerprint": preview["fingerprint"]}
        receipt = self.frq.reset_flag_resolution(aid, rid, command)
        self.assertEqual(receipt["restored"], 1)
        self.assertEqual(self.frq.reset_flag_resolution(aid, rid, command), receipt)
        restored = self.frq.response(before["id"])
        self.assertEqual(restored["current_result_id"], before["current_result_id"])
        self.assertEqual(restored["result"]["reasons"], before["result"]["reasons"])
        self.assertFalse(restored.get("acceptance"))
        self.assertEqual(len(self.sent()), calls)
        with connect(self.path) as db:
            self.assertTrue(db.execute("SELECT 1 FROM frq_results WHERE id=?", (newer,)).fetchone())
            self.assertEqual(self.frq._flag_review_fresh(db, aid), [])  # PIPELINE NEVER A GRADE TWICE.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='flag_resolution_reset'").fetchone()[0], 1)
        state = self.frq.flag_resolution(aid)
        self.assertEqual((state["resolution"]["status"], state["resolution"]["reset"]["restored"], state["preview"]["responses"]), ("complete", 1, 1))

    def test_reset_preserves_a_later_instructor_decision_and_refuses_a_stale_preview(self):
        """GRADING FLAG REVIEW RESET never replaces a decision made after its preview."""
        before = self.held()
        _, done = self.resolve()
        aid, rid = self.plan["id"], done["id"]
        preview = self.frq.flag_reset_preview(aid, rid)
        value = self.frq.response(before["id"])
        self.frq.save_review(before["id"], {"version": value["version"], "result_id": value["current_result_id"],
            "status": "approved", "confirm_review": True, "selected_items": ["R1"]})
        from werkzeug.exceptions import Conflict
        with self.assertRaises(Conflict):
            self.frq.reset_flag_resolution(aid, rid, {"request_id": "changed", "confirmation": "reset", "fingerprint": preview["fingerprint"]})
        protected = self.frq.flag_reset_preview(aid, rid)
        self.assertEqual((protected["restore"], protected["kept_reviewed"]), (0, 1))
        self.assertEqual(self.frq.response(before["id"])["current_result_id"], value["current_result_id"])

    def test_reset_requires_confirmation_and_never_crosses_active_work_or_certification(self):
        """GRADING FLAG REVIEW RESET: explicit confirmation, no in-flight work, and no certified assessment."""
        self.held()
        _, done = self.resolve()
        aid, rid = self.plan["id"], done["id"]
        preview = self.frq.flag_reset_preview(aid, rid)
        command = {"request_id": "guarded-reset", "fingerprint": preview["fingerprint"]}
        with self.assertRaises(ValueError):
            self.frq.reset_flag_resolution(aid, rid, command)
        command["confirmation"] = "reset"
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET status='pending' WHERE id=?", (done["jobs"][0],))
        from werkzeug.exceptions import Conflict
        with self.assertRaises(Conflict):
            self.frq.reset_flag_resolution(aid, rid, command)
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET status='review_ready' WHERE id=?", (done["jobs"][0],))
            db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,
                parts_json,decisions_sha256,bubble_sha256,participants_sha256,snapshot_sha256,summary_json,checks_json,
                confirmations_json,gradescope_json,gradebook_json,actor,created_at)
                VALUES(?,?,?,'test','test','test','[]','test','test','test','test','{}','[]','{}','{}','{}','test','2026-10-25')""",
                (str(uuid.uuid4()), aid, str(uuid.uuid4())))
        with self.assertRaisesRegex(Conflict, "certified"):
            self.frq.reset_flag_resolution(aid, rid, command)

    def test_reset_through_the_assistant_requires_the_instructors_approval(self):
        """ASSISTANT / GRADING FLAG REVIEW RESET: preparing the concrete reset changes no grade."""
        before = self.held()
        _, done = self.resolve()
        value = self.frq.response(before["id"])
        assistant = self.app.extensions["assistant"]
        session = assistant.open_session("test")
        try:
            raw, event = assistant.run_tool(session, "faculty_write", {"method": "POST",
                "path": f"/api/grading/assignments/{self.plan['id']}/flag-resolution/{done['id']}/reset", "body": {},
                "summary": "Restore the original flagged grades"})
            proposed = json.loads(raw)
            self.assertTrue(event["ok"], proposed)
            self.assertEqual(proposed["outcome"], "waiting_for_approval")
            self.assertEqual(self.frq.response(before["id"])["current_result_id"], value["current_result_id"])
            card = assistant.actions(pending_only=True)["pending"][0]
            self.assertEqual(card["preview"]["data"]["restore"], 1)
            result = assistant.approve(card["id"], card["seen"])
            self.assertEqual(result["action"]["status"], "succeeded", result)
            self.assertEqual(self.frq.response(before["id"])["current_result_id"], before["current_result_id"])
        finally:
            assistant.close_session(session["id"])

    def test_an_adopted_grade_is_scored_again_by_an_ordinary_batch_and_finalized(self):
        before = self.held()
        preview = self.frq.flag_resolution(self.plan["id"])["preview"]
        self.assertEqual((preview["responses"], len(preview["questions"])), (1, 1))
        queued, done = self.resolve(lambda p: flag_resolution_answer(p, adopt="standard.second"))
        self.assertEqual((queued["status"], done["status"], done["totals"]["adopted"]), ("pending", "complete", 1))
        request, = self.sent()
        payload = payload_of(request["body"])
        self.assertEqual([r["ref"] for r in payload["responses"]], ["R1"])
        self.assertNotIn(before["id"], json.dumps(payload))  # The model reads refs, never identifiers,
        self.assertNotIn("fixture/", json.dumps(payload["responses"][0]["assessments"]))  # and never which model graded.
        self.assertEqual(len(images_of(request["body"])), payload["responses"][0]["images"])
        self.assertTrue(payload["responses"][0]["flags"])
        # The ruling finalized nothing by itself: an ordinary "Recalculate points" batch scored the grade again.
        job, = done["jobs"]
        carrier = self.frq.job(job)
        self.assertEqual((carrier["operation"], carrier["status"]), ("rescore", "review_ready"))
        after = self.frq.response(before["id"])
        self.assertNotEqual(after["result"]["id"], before["result"]["id"])
        self.assert_finalized(after, "flag_resolution")
        # The export says how it was finalized (requirement, 2026-11-06): after flag review, not as grading-team agreement.
        exported = json.loads(self.frq.export(self.plan["id"])[0])["records"][0]["final"]
        self.assertEqual(exported["basis"], "flag_resolution")
        self.assertTrue(exported["comment"].startswith("Finalized automatically after flag review:"), exported["comment"])
        self.assertNotIn("grading-team agreement", exported["comment"])
        self.assertEqual(self.proposal(after), ["R1"])  # standard.second's ticks, the one complete assessment adopted.
        record = after["result"]["signals"]["flag_resolution"]
        self.assertEqual((record["outcome"], record["adopted"], record["earlier"]["arbiter"]["outcome"]), ("adopted", "standard.second", "unreliable"))
        self.assertEqual(after["result"]["signals"]["scope"]["flag_resolution"]["rule"], fr.RULE)
        self.assertEqual(done["finalized"], 1)
        with connect(self.path) as db:  # The earlier proposal is retained, unchanged.
            self.assertEqual(db.execute("SELECT state FROM frq_results WHERE id=?", (before["result"]["id"],)).fetchone()[0], before["result"]["state"])

    def test_recalculating_points_keeps_the_ruling_a_grade_stands_on(self):
        """RECALCULATING KEEPS A FLAG RULING (2026-10-26): Exam 2's Q24 lost 17 rulings to a "Recalculate points" batch."""
        before = self.held()
        self.resolve(lambda p: flag_resolution_answer(p, adopt="standard.second"))
        settled = self.frq.response(before["id"])
        self.assertEqual(self.proposal(settled), ["R1"])
        for _ in range(2):  # Twice: a second recalculation follows the first one back to the ruling.
            self.queue(operation="rescore", response_ids=[before["id"]])
            self.run_all()
            after = self.frq.response(before["id"])
            self.assertNotEqual(after["result"]["id"], settled["result"]["id"])
            self.assertEqual(self.proposal(after), ["R1"])  # standard.second's ticks, as ruled, not the primary's.
            self.assertEqual(after["result"]["signals"]["flag_resolution"]["adopted"], "standard.second")
            self.assert_finalized(after, "flag_resolution")
            settled = after

    def test_a_revised_grade_must_decide_every_line_and_stands_as_its_own_assessment(self):
        before = self.held()
        _, done = self.resolve(lambda p: flag_resolution_answer(p, "revise", chosen=("R2",)))
        self.assertEqual(done["totals"]["revised"], 1)
        after = self.frq.response(before["id"])
        self.assert_finalized(after, "flag_resolution")
        self.assertEqual(self.proposal(after), ["R2"])

    def test_unreliable_or_a_flag_left_standing_keeps_the_response_with_the_instructor(self):
        before = self.held()
        _, done = self.resolve(lambda p: flag_resolution_answer(p, "unreliable"))
        self.assertEqual((done["totals"]["unreliable"], done["jobs"]), (1, []))
        self.assertEqual(self.frq.response(before["id"])["result"]["id"], before["result"]["id"])
        _, done = self.resolve(lambda p: flag_resolution_answer(p, resolve=False))
        self.assertEqual((done["totals"]["unsettled"], done["jobs"]), (1, []))

    def test_a_response_left_with_the_instructor_names_its_readings_and_the_grade_each_gives(self):
        """Prompt v3 (requirement, 2026-10-03): the mark to look at, and one choice per reading for the review desk. It decides nothing."""
        before = self.held()
        self.assertNotIn("flag_review", self.frq.response(before["id"]))
        def answer(p):
            out = flag_resolution_answer(p, "unreliable")
            for ruling in out["rulings"]:
                ruling["reason"] = "Look at the digit on the answer line: 6 or 8."
                ruling["readings"] = [{"reading": "the digit is 6", "lines": ["R1"]}, {"reading": "the digit is 8", "lines": ["R2"]}]
            return out
        _, done = self.resolve(answer)
        self.assertEqual(done["totals"]["unreliable"], 1)
        note = self.frq.response(before["id"])["flag_review"]
        self.assertEqual(note["reason"], "Look at the digit on the answer line: 6 or 8.")
        self.assertEqual([(r["reading"], r["lines"]) for r in note["readings"]], [("the digit is 6", ["R1"]), ("the digit is 8", ["R2"])])
        self.assertTrue(all(r["max_score"] and r["score"] is not None for r in note["readings"]))
        self.assertEqual(self.frq.response(before["id"])["result"]["id"], before["result"]["id"])  # nothing was decided or changed
        # Once the instructor decides, the note is gone.
        value = self.frq.response(before["id"])
        self.frq.save_review(before["id"], {"version": value["version"], "result_id": value["current_result_id"], "status": "approved",
                                            "confirm_review": True, "selected_items": note["readings"][0]["lines"]})
        self.assertNotIn("flag_review", self.frq.response(before["id"]))

    def left_with_readings(self, p):
        out = flag_resolution_answer(p, "unreliable")
        for ruling in out["rulings"]:
            ruling["reason"] = "Look at the digit on the answer line: 6 or 8."
            ruling["readings"] = [{"reading": "the digit is 6", "lines": ["R1"]}, {"reading": "the digit is 8", "lines": ["R2"]}]
        return out

    def looks(self):
        return [r for r in self.host.requests if prompt_name(r["body"]) == "flag_look"]

    def test_a_response_left_with_reading_choices_has_the_marks_outlined_on_its_crop(self):
        """GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (requirement, 2026-11-14): one more request for that response alone, by the review's own model, kept on the ruling and shown in the note."""
        before = self.held()
        queued, done = self.resolve(self.left_with_readings)
        self.assertEqual((done["status"], done["totals"]["unreliable"], done["jobs"]), ("complete", 1, []))
        [asked] = self.looks()
        self.assertEqual(payload_of(asked["body"]), {"note": "Look at the digit on the answer line: 6 or 8.", "readings": ["the digit is 6", "the digit is 8"]})
        self.assertEqual(len(images_of(asked["body"])), len(before["crops"]))  # its own crops and nothing else
        self.assertNotIn(before["id"], json.dumps(asked["body"]))             # never an identifier
        value = self.frq.response(before["id"])
        self.assertEqual(value["flag_review"]["look"], [{"label": "the digit on the answer line", "crop": 0, "bbox": [.2, .3, .3, .4]}])
        self.assertEqual((value["result"]["id"], value["version"]), (before["result"]["id"], before["version"]))  # an outline decides nothing
        with connect(self.path) as db:
            calls = [tuple(r) for r in db.execute("SELECT step,state,role,prompt FROM frq_cloud_calls WHERE flag_resolution_id=? AND prompt='flag_look'", (queued["id"],))]
        self.assertEqual(calls, [(f"look:{before['id']}", "completed", "rubric_reviewer", "flag_look")])  # one of the review's own calls
        # The readings still approve as they did, and the note goes with the decision.
        self.frq.save_review(before["id"], {"version": value["version"], "result_id": value["current_result_id"], "status": "approved",
                                            "confirm_review": True, "selected_items": value["flag_review"]["readings"][0]["lines"]})
        self.assertNotIn("flag_review", self.frq.response(before["id"]))

    def test_a_look_that_names_nothing_usable_or_fails_leaves_the_note_as_it_was(self):
        before = self.held()
        for look in (lambda p: flag_look_answer(p, [{"label": "off the crop", "crop": 7, "bbox": [.2, .3, .3, .4]}, {"label": "no area", "crop": 0, "bbox": [.3, .3, .2, .4]}]),
                     lambda p: {"spots": []}, lambda p: grade_answer(p) if p.get("rubric") else {"somewhere": "else"},
                     lambda p: ("status", 400, {}, json.dumps({"error": {"message": "refused"}}))):
            self.host.requests.clear()
            _, done = self.resolve(self.left_with_readings, look)
            self.assertEqual((done["status"], done["totals"]["unreliable"], done["error"]), ("complete", 1, ""))  # never fails the review
            self.assertTrue(self.looks())
            note = self.frq.response(before["id"])["flag_review"]
            self.assertNotIn("look", note)
            self.assertEqual((note["reason"], len(note["readings"])), ("Look at the digit on the answer line: 6 or 8.", 2))

    def test_an_outline_added_after_a_finished_review_is_an_event_that_its_note_reads(self):
        """A finished review's result is immutable (migration 033): the notes that still waited when the look was built got theirs by a
        `flag_looks_added` event naming the review. The review's own look comes first; another review's event says nothing here."""
        before = self.held()
        queued, _ = self.resolve(self.left_with_readings, lambda p: {"spots": []})
        self.assertNotIn("look", self.frq.response(before["id"])["flag_review"])
        spot = {"label": "exponent on 9", "crop": 0, "bbox": [.33, .81, .35, .85]}
        with connect(self.path) as db:
            with self.assertRaises(Exception):  # the finished review itself cannot be changed
                db.execute("UPDATE frq_flag_resolutions SET result_json=result_json WHERE id=?", (queued["id"],))
            self.frq.event(db, self.plan["id"], flag_look.ADDED, {"resolution_id": "another-review", "looks": {before["id"]: [spot]}}, "maintenance")
            self.frq.event(db, self.plan["id"], flag_look.ADDED, {"resolution_id": queued["id"], "looks": {"another-response": [spot]}}, "maintenance")
        self.assertNotIn("look", self.frq.response(before["id"])["flag_review"])
        with connect(self.path) as db:
            self.frq.event(db, self.plan["id"], flag_look.ADDED, {"resolution_id": queued["id"], "looks": {before["id"]: [spot, {"label": "bad", "crop": 0, "bbox": [.5, .5, .4, .6]}]}}, "maintenance")
        self.assertEqual(self.frq.response(before["id"])["flag_review"]["look"], [spot])
        # A later review that outlines the marks itself is read from its own ruling.
        queued, _ = self.resolve(self.left_with_readings)
        self.assertEqual(self.frq.response(before["id"])["flag_review"]["look"][0]["label"], "the digit on the answer line")

    def test_no_look_is_asked_for_a_settled_grade_or_a_note_without_reading_choices(self):
        before = self.held()
        self.resolve(lambda p: flag_resolution_answer(p, "unreliable"))  # left with the instructor, no readings named
        self.assertEqual(self.looks(), [])
        self.assertNotIn("look", self.frq.response(before["id"])["flag_review"])
        self.resolve()  # upheld: nothing waits for a look
        self.assertEqual(self.looks(), [])

    def test_unclear_rubric_wording_is_settled_here_though_no_other_model_may_settle_it(self):
        """The instructor's decision of 2026-09-28: rubric doubts are the flag review's to settle, for this feature only."""
        before = self.held(team={}, primary_doubt="R3 does not say whether a bare answer on the answer line counts as no work.")
        self.assertIn(risk.WORDING, before["result"]["signals"]["risk"]["blocks"])
        _, done = self.resolve()
        after = self.frq.response(before["id"])
        self.assert_finalized(after, "flag_resolution")
        wording = [f for f in after["result"]["signals"]["flag_resolution"]["flags"] if f["text"] == risk.WORDING]
        self.assertTrue(wording and wording[0]["resolved"])

    def test_clarifications_are_proposals_that_change_nothing(self):
        self.held()
        before = self.frq.assignment(self.plan["id"])
        _, done = self.resolve(lambda p: flag_resolution_answer(p, clarifications=[("R1", "Count x = 6 on the answer line as the answer.")]))
        question, = done["result"]["questions"]
        self.assertEqual([c["line_id"] for c in question["clarifications"]], ["R1"])
        self.assertEqual(done["totals"]["clarifications"], 1)
        self.assertEqual(self.frq.assignment(self.plan["id"])["questions"][0]["rubric_id"], before["questions"][0]["rubric_id"])

    def test_one_review_at_a_time_a_repeat_returns_it_and_a_stop_sends_and_applies_nothing(self):
        self.held()
        first = self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "flags-1"})
        self.assertEqual(self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "flags-1"})["id"], first["id"])
        from werkzeug.exceptions import Conflict
        with self.assertRaises(Conflict):
            self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "flags-2"})
        stopped = self.frq.cancel_flag_resolution(self.plan["id"], first["id"])["resolution"]
        self.assertEqual((stopped["status"], stopped["cancelled"]), ("failed", True))
        self.frq.process_cloud()
        self.assertEqual(self.sent(), [])

    def test_a_retick_may_quote_a_line_the_crop_shows_and_the_reader_left_out(self):
        """v2: the transcript the carrier batch scores on is the reader's with the ruling's line added, marked as the flag review's."""
        before = self.held()
        def added(r):
            return [{"line_id": "fr-1", "after": r["transcript"]["lines"][-1]["id"], "crop": 0, "saw": "x = 6 is written again in a box below the work.",
                     "elements": [{"type": "math", "text": "x = 6", "latex": "x = 6", "state": "written"}]}]
        _, done = self.resolve(lambda p: flag_resolution_answer(p, "revise", chosen=("R1",), changes=added))
        self.assertEqual(done["totals"]["revised"], 1, done)
        after = self.frq.response(before["id"])
        self.assert_finalized(after, "flag_resolution")
        self.assertEqual(self.proposal(after), ["R1"])
        line = after["result"]["transcript"]["lines"][-1]
        self.assertEqual((line["id"], line["source"], line["saw"]), ("fr-1", fr.SOURCE, "x = 6 is written again in a box below the work."))
        evidence = next(a for a in after["result"]["applications"] if a["applied"])["evidence"]
        self.assertEqual(evidence[0]["line_id"], "fr-1")
        self.assertEqual(after["result"]["signals"]["flag_resolution"]["transcript_changes"][0]["line_id"], "fr-1")
        # The result the ruling was made on keeps the reader's transcript.
        self.assertNotIn("fr-1", [l["id"] for l in before["result"]["transcript"]["lines"]])

    def test_a_change_the_crop_cannot_carry_fails_only_that_ruling(self):
        before = self.held()
        wrong = lambda r: [{"line_id": "fr-1", "after": "", "crop": 7, "saw": "Nothing.", "elements": [{"type": "math", "text": "x = 6", "latex": "x = 6", "state": "written"}]}]
        _, done = self.resolve(lambda p: flag_resolution_answer(p, "revise", chosen=("R1",), changes=wrong))
        self.assertEqual((done["totals"]["failed"], done["jobs"]), (1, []))
        self.assertEqual(self.frq.response(before["id"])["result"]["id"], before["result"]["id"])

    def test_work_past_the_crop_is_ruled_on_again_with_the_whole_page_outlined(self):
        before = self.held()
        from unittest.mock import patch
        from frq_fixtures import page
        # The fixture's scan holds no real raster; in service the page is read through the Inbox storage and its checksum (`_scan_page`).
        patch.object(self.frq, "_scan_page", return_value=page()).start()
        def answer(p):
            if p.get("page_round"):
                return flag_resolution_answer(p)
            return flag_resolution_answer(p, "unreliable", needs_page=True)
        _, done = self.resolve(answer)
        first, second = self.sent()
        self.assertEqual(done["progress"], {"requests": 2, "answered": 2, "running": 0})
        self.assertNotIn("page_round", payload_of(first["body"]))
        paged = payload_of(second["body"])
        response, = paged["responses"]
        self.assertEqual((paged["page_round"], response["pages"]), (True, 1))
        images = images_of(second["body"])
        self.assertEqual(len(images), response["images"] + response["pages"])
        import base64, io
        from PIL import Image
        page = Image.open(io.BytesIO(base64.b64decode(images[-1]["image_url"]["url"].split(",", 1)[1]))).convert("RGB")
        self.assertIn((220, 30, 30), {color for _, color in page.getcolors(1 << 16)})  # the question's area, outlined in red
        self.assertEqual(done["totals"]["adopted"], 1)
        question, = done["result"]["questions"]
        self.assertTrue(question["responses"][0]["ruling"]["page_round"])
        after = self.frq.response(before["id"])
        self.assert_finalized(after, "flag_resolution")
        self.assertTrue(after["result"]["signals"]["flag_resolution"]["page_round"])

    def test_a_finalized_grade_is_not_sent_again(self):
        self.held()
        self.resolve()
        with self.assertRaises(ValueError):
            self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "again"})

    def two_held(self):
        """A second student's paper graded in the same batch, both held by the split team: (first, second) response IDs."""
        with connect(self.path) as db:
            other = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=other), changed="second")
        self.held()
        with connect(self.path) as db:
            found = {r["enrollment_id"]: r["id"] for r in db.execute("SELECT id,enrollment_id FROM frq_responses WHERE assignment_id=?", (self.plan["id"],))}
        self.assertEqual(len(found), 2)
        return found[self.enrollment], found[other]

    def change_paper(self, *response_ids):
        """The reviewed paper of these responses changed after they were graded: `validate_task` refuses them as it does then ("The
        reviewed paper changed after this was graded"). Task inputs are immutable, so the refusal is made here, the rest read as ever."""
        real = self.frq.validate_task
        def validate(db, task, **more):
            if task["response_id"] in response_ids:
                raise Conflict("The reviewed paper changed after this was graded. The next batch grades it again.")
            return real(db, task, **more)
        patcher = patch.object(self.frq, "validate_task", side_effect=validate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_queued(self, answer=None):
        self.host.script = lambda body, host: (answer or flag_resolution_answer)(payload_of(body)) if prompt_name(body) == "flag_resolution" else grade_answer(payload_of(body))
        for _ in range(10):
            self.frq.process_cloud()
            with connect(self.path) as db:
                db.execute("UPDATE frq_flag_resolutions SET not_before='' WHERE status='pending'")
        self.run_all()
        return self.frq.flag_resolution(self.plan["id"])["resolution"]

    def test_a_response_that_changed_after_the_review_was_queued_is_left_out_and_the_rest_is_ruled_on(self):
        """PIPELINE: a flag holds back only its own item. One changed paper must not fail the whole review as "stopped unexpectedly"."""
        first, second = self.two_held()
        self.assertEqual(self.frq.flag_resolution(self.plan["id"])["preview"]["responses"], 2)
        queued = self.frq.queue_flag_resolution(self.plan["id"], {"request_id": str(uuid.uuid4())})
        self.assertEqual(queued["responses"], 2)
        self.change_paper(second)
        done = self.run_queued(lambda p: flag_resolution_answer(p, adopt="standard.second"))
        self.assertEqual((done["status"], done["error"]), ("complete", ""))
        self.assertEqual((done["totals"]["responses"], done["totals"]["adopted"], done["totals"]["questions_failed"]), (1, 1, 0))
        entry, = done["result"]["questions"]
        self.assertEqual([r["response_id"] for r in entry["responses"]], [first])
        self.assertEqual([r["response_id"] for r in entry["left_out"]], [second])
        self.assertEqual(len(done["jobs"]), 1)
        request, = self.sent()
        self.assertEqual([r["ref"] for r in payload_of(request["body"])["responses"]], ["R1"])  # the left-out response was never sent
        self.assertEqual(self.frq.response(first)["result"]["signals"]["flag_resolution"]["outcome"], "adopted")
        self.assertNotIn("flag_resolution", self.frq.response(second)["result"]["signals"])

    def test_a_review_whose_every_response_changed_completes_with_nothing_ruled_on(self):
        first, second = self.two_held()
        self.frq.queue_flag_resolution(self.plan["id"], {"request_id": str(uuid.uuid4())})
        self.change_paper(first, second)
        done = self.run_queued()
        self.assertEqual((done["status"], done["error"], done["jobs"]), ("complete", "", []))
        self.assertEqual((done["totals"]["responses"], done["totals"]["questions_failed"]), (0, 0))
        entry, = done["result"]["questions"]
        self.assertEqual((entry["responses"], sorted(r["response_id"] for r in entry["left_out"]), bool(entry["label"])), ([], sorted([first, second]), True))
        self.assertEqual(self.sent(), [])


def load_tests(loader, tests, pattern):
    """Only this file's own tests: the arbitrator fixture's cases run in their own file."""
    suite = unittest.TestSuite()
    for case in (Rules, Readings, LinkedProposals, TranscriptChanges, FlagResolution):
        for name in loader.getTestCaseNames(case):
            if name in vars(case):
                suite.addTest(case(name))
    return suite
