"""The answer key, and responses an instructor can judge together.

Nothing here grades. A group only gathers current, unreviewed proposals that show the
same final answer, the same amount of visible work and the same proposed rubric lines,
so the instructor can look at all of them on one screen and record one decision each.
"""
import json

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect

from . import answers, risk
from .assignments import version
from .models import digest, identifier, items, text
from .rubric_quality import quality_flags

OPEN = ("proposed", "grading_review", "ocr_review", "both", "trial_accepted")
SOLUTION = ("problem", "expected_answer", "worked_solution")


def _review_members(data, maximum):
    """Validate the entire selection before any per-response review can be saved (GRADING)."""
    chosen = items(data.get("responses"), "Responses to approve", maximum)
    if not chosen or data.get("confirm_review") is not True or data.get("inspected_each") is not True:
        raise ValueError("Look at every response you approve, then confirm that you did.")
    if any(not isinstance(command, dict) for command in chosen):
        raise ValueError("Name each response once.")
    ids = [identifier(command.get("id")) for command in chosen]
    if len(set(ids)) != len(ids):
        raise ValueError("Name each response once.")
    return chosen


class AnswerGroupsMixin:
    def save_answer_key(self, aid, data, *, actor="workspace"):
        """One save for every question. Only key and solution text change, so nothing the instructor approved is reset.

        The rubric pin and its approval stay, and a confirmed location is carried to the new question
        revision with an append-only receipt, because no page, area or template changed. Proposals
        already made against the old revision become stale, as for any changed solution.
        """
        entries = items(data.get("questions"), "Answer key questions", 100)
        if not entries:
            raise ValueError("Enter an answer key for at least one question.")
        with self.rosters.lock(), connect(self.path) as db:
            version(data, self.plan(db, aid)["version"])
            prepared = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Invalid answer key entry.")
                q = self.question(db, aid, entry.get("question_id"))
                version(entry, q["version"])
                clean = {**q["content"], "answer_key": answers.clean_key(entry.get("answer_key")),
                         **{name: text(entry.get(name, q["content"].get(name, "")), name, 20000) for name in SOLUTION}}
                if "choice_grading" in entry:
                    clean = self.choice_grading(db, aid, q, clean, entry["choice_grading"])
                if digest(clean) != digest({"answer_key": [], **q["content"]}):
                    prepared.append((q, clean))
            db.execute("BEGIN IMMEDIATE")
            for q, clean in prepared:
                qid, now = q["question_id"], timestamp()
                rid = self.new_question_revision(db, qid, clean, now)
                self.pin_question_revision(db, aid, qid, rid)
                confirmed = self.carry_location(db, aid, qid, q["question_revision_id"], q["rubric_id"],
                    "Only the answer key and solution text changed; the template, pages and areas are the same.", to_revision=rid, now=now)
                self.event(db, aid, "answer_key_saved", {"question_id": qid, "question_revision_id": rid, "parts": len(clean["answer_key"]),
                                                         "location_carried_forward": bool(confirmed)}, actor)
            if prepared:
                self.touch(db, aid)
        # ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): the saved key may be the last thing the rubric check of accepted
        # drafts waited for, so that its snapshot of each problem carries the key (`rubric_generation.request_rubric_review_when_ready`).
        # It never fails the save, and a plan no acceptance readied is left exactly as it was.
        self.rubric_review_follows(aid)
        return self.assignment(aid)

    @staticmethod
    def graded_yet(db, aid, question_id):
        """Whether any response to the question has a grade or a decision: its multiple-choice setting is then left as it is."""
        return bool(db.execute("""SELECT 1 FROM frq_responses WHERE assignment_id=? AND question_id=?
            AND (current_result_id IS NOT NULL OR current_review_id IS NOT NULL) LIMIT 1""", (aid, question_id)).fetchone())

    def choice_grading(self, db, aid, q, clean, value):
        """GRADING MULTIPLE-CHOICE READER, AUTOMATIC CONFIRMATION (requirement, 2026-10-08): a multiple-choice question is graded by choice
        by the exact rule, with nothing saved; the instructor may set it to written work ({"written": true}) or back to the rule (null),
        in the question's new revision, before it has grades and never after, since a new revision sends its grades back as new work."""
        from . import choice
        saved = choice.clean(value)
        if saved == (q["content"].get("choice_grading") or None):
            return clean
        if self.graded_yet(db, aid, q["question_id"]):
            raise ValueError("Written work or grading by choice is chosen before a question is graded; this one already has grades.")
        clean = {key: item for key, item in clean.items() if key != "choice_grading"}
        if saved:
            clean["choice_grading"] = saved
        return clean

    def answer_groups(self, aid, question_id):
        """Current, unreviewed proposals, gathered by their answer facts. No names, no scores decided."""
        open_rows = [row for row in self.responses(aid, question_id) if row["state"] in OPEN and not row["review_status"]]
        groups = {}
        with self.rosters.lock(), connect(self.path) as db:
            question = self.question(db, aid, question_id)
            labels = {item["id"]: item for item in question["rubric"]["rules"]["items"]} if question["rubric"] else {}
            individual = blind = 0
            quality = self.rubric_quality_status(db, aid)
            for row in open_rows:
                if row["state"] == "trial_accepted" and row["blind"]:
                    blind += 1  # A contact sheet shows the proposal; a blind acceptance is graded alone, before the AI's decision is shown.
                    continue
                result = db.execute("SELECT * FROM frq_results WHERE id=?", (row["current_result_id"],)).fetchone()
                task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone())
                if task["status"] != "complete":
                    continue
                applied = sorted(a["rubric_item_id"] for a in json.loads(result["applications_json"]) if a["applied"])
                signals = json.loads(result["signals_json"])
                found, verdict = signals.get("answer"), signals.get("risk") or {}
                if verdict.get("individual") or risk.selection_disagreement(verdict.get("components") or {}) or quality_flags(quality, question_id, task['rubric_id'], json.loads(result['applications_json']))['reasons']:
                    individual += 1  # Graders that disagree, or mathematics that contradicts the proposal: reviewed one by one, never on a contact sheet.
                    continue
                votes = (verdict.get("components") or {}).get("votes", {}).get("count", 1)
                # A group never mixes proposals that a separate assessment agreed with and proposals nobody checked.
                key = (found["group"] if found else "unread:" + digest(applied)[:12]) + (":checked" if votes > 1 else "") + (":trial" if row["state"] == "trial_accepted" else "")
                group = groups.setdefault(key, {"id": key, "answers": [a["text"] for a in found["answers"]] if found else [],
                    "check": found["check"] if found else "unknown", "work": found["work"] if found else "unknown",
                    "readers_agree": found["readers_agree"] if found else False, "has_key": bool(found and found["key"]),
                    "work_required": bool(found and found["work_required"]), "applied": applied,
                    "applied_items": [{"id": i, "points": labels[i]["points"], "description": labels[i]["description"]} for i in applied if i in labels],
                    "score": json.loads(result["score_json"]), "independent_votes": votes, "levels": {}, "members": [],
                    # Trial-accepted: the policy passed every rule. Your one look at each confirms or overturns it, and that record is the benchmark.
                    "accepted": "trial" if row["state"] == "trial_accepted" else None,
                    "relied_on": verdict.get("components") if row["state"] == "trial_accepted" else None})
                group["levels"][verdict.get("level", "unrated")] = group["levels"].get(verdict.get("level", "unrated"), 0) + 1
                crops = self.step(db, task, "extracting")["crops"]
                group["members"].append({"id": row["id"], "version": row["version"], "result_id": row["current_result_id"], "state": row["state"],
                    "reasons": json.loads(result["reasons_json"]), "work_lines": found["work_lines"] if found else None,
                    "risk": verdict.get("level"), "escalated": bool((signals.get("escalation") or {}).get("escalated")),
                    "images": [crop["images"]["original"]["url"] for crop in crops]})
        # Largest first: one look removes the most work. Among equals, the groups with nothing flagged come first.
        ordered = sorted(groups.values(), key=lambda g: (-len(g["members"]), sum(m["state"] != "proposed" for m in g["members"]), g["id"]))
        for group in ordered:
            group["size"] = len(group["members"])
            group["flagged"] = sum(member["state"] != "proposed" for member in group["members"])
        return {"question_id": question_id, "groups": ordered, "responses": sum(g["size"] for g in ordered), "individual": individual, "blind_waiting": blind,
                "rubric": [{"id": i["id"], "points": i["points"], "description": i["description"]} for i in labels.values()],
                "note": "A group shares one final answer, one amount of visible work and one proposal. Approve only what you looked at."}

    def approve_group(self, aid, data):
        """The instructor's own decision for responses they inspected together; each keeps its own review receipt."""
        chosen = _review_members(data, 500)
        selected = [identifier(value) for value in items(data.get("selected_items", []), "Rubric selections")]
        with self.rosters.lock():
            listing = self.answer_groups(aid, data.get("question_id"))
            group = next((g for g in listing["groups"] if g["id"] == data.get("group")), None)
            if not group:
                raise Conflict("This group changed. Reload the answer groups before approving.")
            members = {member["id"]: member for member in group["members"]}
            for command in chosen:
                member = members.get(command.get("id"))
                if not member or command.get("version") != member["version"]:
                    raise Conflict("A response left this group or changed. Reload the answer groups before approving.")
            changed = sorted(selected) != group["applied"]
            comment = ("Approved with its answer group; the instructor chose these rubric lines for the group." if changed
                       else "Approved with its answer group after inspecting each response.")
            saved = [self.save_review(command["id"], {"version": command["version"], "result_id": members[command["id"]]["result_id"], "status": "approved",
                "confirm_review": True, "selected_items": selected, "comment": comment}) for command in chosen]
            with connect(self.path) as db:
                self.event(db, aid, "answer_group_approved", {"question_id": data.get("question_id"), "group": group["id"], "approved": len(saved),
                    "of": group["size"], "selection_changed": changed, "check": group["check"], "work": group["work"]})
        return {"approved": len(saved), "remaining": group["size"] - len(saved)}

    # PROPOSAL GALLERY (requirement, 2026-10-03). One group of the review queue, keyed by the exact rubric
    # lines the AI proposed, shown response by response with its crops; the instructor takes out what they want to look at alone and
    # approves the rest with those lines, each response with its own review. The queue decides the group and its members, so the
    # gallery and the workspace never disagree. Only plain proposals and proposals held by a grading check alone may be approved
    # together: a reading to check, a rubric concern, graders that disagree or a finalization flag are each reviewed alone in the desk.
    GALLERY_STATES = ("proposed", "grading_review")
    # BLANK ANSWERS (requirement, 2026-10-11). 122 of the 429 held grades of the four graded assignments
    # were "the reader found no handwriting", each confirmed alone in the desk; the instructor changed 6, all on one quiz where the
    # reader had missed real writing. So nothing here confirms a blank: a gallery only lets them look at every such crop of a group
    # together and approve the zeros they looked at. A response held for a blank answer may be approved in a gallery when everything
    # that holds it follows from the empty reading: every one of its areas was read as empty and nothing was transcribed, its crops
    # are registered, the proposal is a zero with a line ticked, the graders agree, and neither a rubric concern nor the finalization
    # flags it. Credit proposed beside an empty area, a partly empty response, a crop that may be misaligned or a disagreement is
    # opened alone, as before. Replayed on those 122: 93 may be approved together, the 6 changed ones among them (each crop shown).
    BLANK_STATES = ("ocr_review", "both")

    @staticmethod
    def blank_answer(result, crops):
        """(held for a blank answer, everything else about it follows from the empty reading), from the stored result alone."""
        signals = json.loads(result["signals_json"])
        components = (signals.get("risk") or {}).get("components") or {}
        reading = components.get("reading") or {}
        if not reading.get("blank_crops"):
            return False, False
        whole = reading["blank_crops"] == crops and not (json.loads(result["transcript_json"] or "null") or {}).get("lines")
        try:
            zero = float((json.loads(result["score_json"] or "null") or {})["score"]) == 0 and not signals.get("empty_selection")
        except (KeyError, TypeError, ValueError):
            zero = False
        return True, bool(whole and zero and reading.get("registered") and not risk.selection_disagreement(components))

    def proposal_gallery(self, aid, question_id, group_id, job_id=None, only=None):
        if only not in (None, "", "blank"):
            raise ValueError("Unknown gallery filter.")
        with self.rosters.lock():
            queue = self.review_queue(aid, job_id or None, question_id, review_details=True)
            entry = next((q for q in queue["questions"] if q["question_id"] == question_id), None)
            group = next((g for g in (entry or {}).get("groups", []) if g["id"] == group_id), None)
            if not group or group["kind"] != "proposal":
                raise Conflict("This group changed, or its proposals are hidden for a blind review. Reload the review.")
            rows = {row["id"]: row for row in self.responses(aid, question_id)}
            members = []
            with connect(self.path) as db:
                quality = self.rubric_quality_status(db, aid)
                for member in group["members"]:
                    row, kinds = rows.get(member["id"]), set(member.get("reason_kinds") or [])
                    item = {"id": member["id"], "version": row["version"] if row else None, "result_id": row["current_result_id"] if row else None,
                            "state": member["state"], "reason_kinds": sorted(kinds), "reasons": [], "images": [], "eligible": False, "why": ""}
                    result = db.execute("SELECT * FROM frq_results WHERE id=?", (row["current_result_id"],)).fetchone() if row and row["current_result_id"] else None
                    task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone()) if result else None
                    blank = clear = False
                    if result and task and task["status"] == "complete":
                        item["reasons"] = json.loads(result["reasons_json"])
                        crops = self.response_crops(db, task)["crops"]
                        item["images"] = [crop["images"]["original"]["url"] for crop in crops]
                        verdict = json.loads(result["signals_json"]).get("risk") or {}
                        flagged = quality_flags(quality, question_id, task["rubric_id"], json.loads(result["applications_json"]))["reasons"]
                        alone = verdict.get("individual") or risk.selection_disagreement(verdict.get("components") or {}) or flagged
                        blank, clear = self.blank_answer(result, len(crops))
                        if blank:
                            # Faint pencil shows in the contrast crop where the original looks empty: both are on the tile.
                            item["blank"], item["contrast"] = True, [(crop["images"].get("contrast") or crop["images"]["original"])["url"] for crop in crops]
                    else:
                        alone = flagged = True
                    # A response whose reading waits is still waiting (it was told "no longer waiting" until 2026-10-11).
                    item["why"] = ("It is no longer waiting for review." if not row or not result or row["state"] not in (*self.GALLERY_STATES, *self.BLANK_STATES)
                        else "You started a decision on it: finish it in the review desk." if row["review_status"]
                        else "It raised a rubric concern: settle the rule first." if "rubric" in kinds
                        else "" if blank and clear and not flagged
                        else "The reader found no handwriting on it, but more than that holds it: open it alone." if blank
                        else "Its reading needs checking against the transcription." if "reading" in kinds or row["state"] not in self.GALLERY_STATES
                        else "The graders disagreed, or the rubric finalization flags it." if alone else "")
                    item["eligible"] = not item["why"]
                    if only != "blank" or blank:
                        members.append(item)
        return {"question_id": question_id, "group": group["id"], "applied": group["applied"], "items": group["items"], "members": members,
                "eligible": sum(m["eligible"] for m in members), **({"only": "blank"} if only == "blank" else {})}

    def approve_proposal_group(self, aid, data):
        """Approve responses the instructor looked at in a proposal gallery, with the group's own proposed lines; each response gets its
        own review. Everything is checked again first and nothing is saved when anything changed: the group's lines, a response's
        version or current result, or whether it may still be approved together."""
        chosen = _review_members(data, 100)
        applied = [identifier(value) for value in items(data.get("applied", []), "Rubric selections")]
        with self.rosters.lock():
            gallery = self.proposal_gallery(aid, data.get("question_id"), data.get("group"), data.get("job_id") or None, data.get("only") or None)
            if sorted(applied) != sorted(gallery["applied"]):
                raise Conflict("This group's proposed lines changed. Reload the gallery before approving.")
            members = {m["id"]: m for m in gallery["members"]}
            for command in chosen:
                member = members.get(command.get("id"))
                if not member or not member["eligible"] or command.get("version") != member["version"] or command.get("result_id") != member["result_id"]:
                    raise Conflict("A response left this group or changed. Reload the gallery before approving.")
            saved = [self.save_review(command["id"], {"version": command["version"], "result_id": command["result_id"], "status": "approved",
                "confirm_review": True, "selected_items": gallery["applied"],
                "comment": "Confirmed blank in its gallery after looking at each crop." if members[command["id"]].get("blank")
                           else "Approved in its proposed-line gallery after looking at each response."})
                for command in chosen]
            blank = sum(bool(members[c["id"]].get("blank")) for c in chosen)
            with connect(self.path) as db:
                self.event(db, aid, "proposal_group_approved", {"question_id": gallery["question_id"], "group": gallery["group"], "approved": len(saved),
                    "of": len(gallery["members"]), "flagged": sum(members[c["id"]]["state"] != "proposed" for c in chosen), **({"blank": blank} if blank else {})})
        return {"approved": len(saved), "remaining": len(gallery["members"]) - len(saved)}
