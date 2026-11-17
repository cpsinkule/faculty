"""Flag resolution (requirement, 2026-09-28), question by question and version by version, "in batches of 25, but it would receive the crops and
everything for a full picture". They chose, when asked: the model may uphold a grade or re-tick it; it rules on every flag but the ones
that are the instructor's alone; it proposes clarifications for them to confirm; one button runs the whole assignment.

A resolution belongs to the TERM grading plan (`frq_flag_resolutions`, migration 033). When requested it snapshots the plan's HELD grades
that a ruling could finalize (`candidates`): each response's current result from a Cloud team batch, unreviewed and not finalized, graded
under the rubric and question that stand, and held by nothing reserved to the instructor (`reserved`). The Rubric drafting and review
model then reads each question and version in chunks of `CHUNK` responses, with their crops, every earlier assessment (by label, never by
model), and the rulings already made on that question (`precedents`), so the same work gets the same decision.

ITS OWN PROMPT (v2; requirement, 2026-09-29). v1 was the arbitrator's prompt
batched: its three choices, its flag rules and its "leave it with the instructor whenever you are not sure", and it left 48 of 108 and 43 of 50
held grades with the instructor, most because the crop showed what the transcript lacked. `flag_resolution-v2` asks for definitive
judgments: every flag is answered, and a grade is left with the instructor only when the evidence itself is missing. Its answer says
`uphold`, `retick` or `instructor` (stored as the arbitrator's adopt / revise / unreliable, which `arbitration.validate` still checks), and
the instructor chose, when asked, two more powers:
- It may correct or add the transcript lines the crop clearly shows (`transcript_changes`: a misread digit or symbol, a circled printed
  choice the reader left out) and quote them. Each changed line is marked as the flag review's (`SOURCE`, with what it `saw` and, for a
  correction, the reader's `original` elements); the carrier batch scores the grade on that transcript (`amend`), and the result an
  earlier batch retained keeps the reader's.
- When writing that may decide credit runs past the crop's edge, it asks for the whole page (`needs_page`), and Faculty sends that
  response again with its scanned pages, the question's area outlined (`_page_views`), in one more request (a page round).

What a ruling can and cannot do:
- It finalizes NOTHING by itself. The responses it settled are scored again by an ordinary "Recalculate points" batch of the plan
  (`_carry`: one per source batch, its settings those the source batch froze plus `RULE`), whose scoring stage takes the ruling's grade
  (`carry`), runs every deterministic check again (`jobs.scored`), settles only the flags the model was SHOWN and marked resolved (`apply`)
  and goes through the ordinary acceptance gate. A flag the new grade raises, or one left standing, keeps the response with the instructor.
- It may settle what `risk.instructor_only` keeps from the arbitrator and the flag reviewer where it concerns the RUBRIC: unclear wording
  and a rubric condition. That is the instructor's decision of 2026-09-28, for this feature only. It never settles a possibly blank crop,
  a grade with no line ticked (`EMPTY_SELECTION`; a negative rubric's full credit nobody ticked, `EMPTY_FULL`, IS sent, and only a ruling
  that ticks a line clears it), a pair the team never compared, a selection that cannot be scored, or the rubric finalization: responses
  held by any of those are not sent (`reserved`).
- Its clarifications are proposals shown to the instructor. Confirming one in Key & Rubric Finalization changes the question's guidance,
  with everything that follows from that (the question's grades are graded again); nothing here writes one.
"""
import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from .scoring import score
from . import arbitration, config, flag_look, recognition, rubric_quality, storage, flag_adjustments
from . import risk as risk_rules
from .cloud import RetryLater, later
from .models import digest, encoded, identifier, items, text, uid
from .providers.base import ProviderError
from .rubric_quality import grader_rubric

RULE = "flag-resolution-v3"   # GRADING FLAG REVIEW DEDUCTIONS: authorized adjustments and a checked final score.
RULES = ("flag-resolution-v1", "flag-resolution-v2", RULE)
PROMPT = "flag_resolution"
ADJUSTED_PROMPT = "flag_resolution_adjustments"
# The answer's words for the arbitrator's decisions, which the ruling is stored and checked as (v1 answers already use the latter).
DECISIONS = {"uphold": "adopt", "retick": "revise", "instructor": "unreliable"}
CHANGES = 6                   # Transcript lines one ruling may correct or add.
SOURCE = "flag_review"        # How a line the flag review corrected or added is marked in the transcript.
PAGE_WIDTH = 2000             # Longest side of a page image in a page round (a 200-DPI letter page is 2200).
FLAG_TRANSCRIPT = "flag_review_transcript"  # The key `carry` hands the changed transcript to the scoring stage under.
CHUNK = 25                    # Responses per request (requirement, 2026-09-28).
MAX_IMAGES = 90               # Images per request: a chunk is cut short before it would carry more.
IMAGES = 18 * 1024**2         # Bytes of crops per request.
PRECEDENTS = 60               # Earlier rulings of the question shown beside each chunk.
PARALLEL = 100                # GRADING "FLAG REVIEW PARALLELISM": one request per question at a time, up to 100 questions.
TOKENS = 65536
BASIS = "flag_resolution"
# Flags only the instructor settles, even here. Rubric wording and conditions are NOT among them (the instructor's decision above).
_THEIRS = {risk_rules.NO_SECOND, risk_rules.NO_PRIMARY, risk_rules.INCOMPLETE, risk_rules.NOBODY_ELSE, risk_rules.SECOND_FAILED,
           risk_rules.ONE_LINEAGE, risk_rules.EMPTY_SELECTION, risk_rules.UNSCORABLE}


def reserved(text):
    """Whether this flag stays the instructor's whatever the flag review rules."""
    text = arbitration._same(text)
    return text in _THEIRS or risk_rules.blank(text)


def output_of(result):
    """A stored result in the shape `arbitration.flags` reads."""
    return {"signals": json.loads(result["signals_json"]), "reasons": json.loads(result["reasons_json"]),
            "score": json.loads(result["score_json"]), "state": result["state"]}


def enabled(settings):
    return settings.get("flag_resolution_rule") in RULES


def scope(settings):
    """What an acceptance scope says about a carrier batch: the rule, the model that ruled and the prompt it was asked."""
    if not enabled(settings):
        return None
    from .providers.local_vlm import prompt
    name = ADJUSTED_PROMPT if settings["flag_resolution_rule"] == RULE else PROMPT
    return {"rule": settings["flag_resolution_rule"], "model": settings.get("flag_resolution_model", ""), "prompt": prompt(name)[1]["prompt_sha256"],
            **({"settled_notes_rule": arbitration.SETTLED_RULE} if arbitration.settled_notes(settings) else {})}


def changes_of(raw, transcript, crops):
    """The transcript lines a ruling corrects or adds, checked, or ValueError. Each names an existing line (a correction) or a new one
    with the line it follows, the crop it was seen on and what the crop shows there; its elements are the reader's, without geometry."""
    found = raw.get("transcript_changes") or []
    if not isinstance(found, list) or len(found) > CHANGES:
        raise ValueError(f"A ruling may correct or add at most {CHANGES} transcript lines.")
    if found and (not transcript or not crops):
        raise ValueError("A transcript line can only be changed from a crop.")
    lines = {line["id"] for line in transcript["lines"]} if transcript else set()
    seen, changes = set(), []
    for change in found:
        if not isinstance(change, dict):
            raise ValueError("Invalid transcript change.")
        line = identifier(change.get("line_id"))
        if line in seen:
            raise ValueError("A transcript line was changed twice.")
        after = change.get("after") or ""
        if not isinstance(after, str):
            raise ValueError("An added transcript line must follow an existing line.")
        if line not in lines and after and after not in lines and after not in seen:
            raise ValueError("An added transcript line must follow an existing line.")
        crop = change.get("crop")
        if type(crop) is not int or not 0 <= crop < crops:
            raise ValueError("A transcript change names a crop that does not exist.")
        elements = []
        for el in items(change.get("elements"), "Changed line elements", 12):
            if not isinstance(el, dict) or el.get("type") not in ("math", "text") or el.get("state", "written") not in ("written", "crossed_out", "inserted"):
                raise ValueError("A changed line's elements are math or text, written, crossed out or inserted.")
            written, latex = text(el.get("text") or "", "Changed line text", 2000), text(el.get("latex") or "", "Changed line LaTeX", 2000)
            if not (written.strip() or latex.strip()):
                continue  # An empty element (e.g. an unreadable scribbled-out mark) carries nothing; `saw` describes it.
            elements.append({"type": el["type"], "text": written, "latex": latex, **({"state": el["state"]} if el.get("state", "written") != "written" else {})})
        if not elements:
            raise ValueError("A changed line has no elements.")
        seen.add(line)
        changes.append({"line_id": line, "kind": "correct" if line in lines else "add", "after": "" if line in lines else after, "crop": crop,
                        "saw": text(change.get("saw") or "", "What the crop shows", 600, False), "elements": elements})
    return changes


def amend(transcript, changes):
    """The transcript with a ruling's changes: each changed line marked as the flag review's, a correction keeping the reader's elements.
    The serialized text is changed row for row where it has one row per line, else written again from the lines."""
    result = copy.deepcopy(transcript)
    lines = result["lines"]
    rows = {key: (result.get(key) or "").split("\n") for key in ("plain_text", "latex")}
    aligned = all(len(value) == len(lines) for value in rows.values())
    serial = lambda elements, key: " ".join((el.get(key) or el.get("text") or "") for el in elements)
    for change in changes:
        elements = [{**el, "bbox": None, "crop": change["crop"], "confidence": None} for el in change["elements"]]
        line = {"id": change["line_id"], "elements": elements, "source": SOURCE, "saw": change["saw"]}
        ids = [value["id"] for value in lines]
        if change["kind"] == "correct":
            index = ids.index(change["line_id"])
            lines[index] = {**line, "original": lines[index]["elements"]}
        else:
            index = ids.index(change["after"]) + 1 if change["after"] else 0
            lines.insert(index, line)
        if aligned:
            for key, value in rows.items():
                value[index:index + (change["kind"] == "correct")] = [serial(elements, "text" if key == "plain_text" else "latex")]
    if not aligned:
        rows = {"plain_text": [serial(line["elements"], "text") for line in lines], "latex": [serial(line["elements"], "latex") for line in lines]}
    result["plain_text"], result["latex"] = "\n".join(rows["plain_text"]), "\n".join(rows["latex"])
    return result


def linked_clarifications(raw, linked):
    """A proposed clarification's versions for the same line of the problem's other versions (prompt v3, requirement, 2026-10-03: a rule
    settled on one version goes to the others, in their own values): [{question_id, version, line_id, clarification}], only for lines
    in `linked` ({line ID: [{question_id, version, line_id, description}]}), at most one per line. Proposals, like the clarification."""
    known = {(l["version"], l["line_id"]): l for l in linked.get(raw.get("line_id"), [])}
    found = {}
    for entry in raw.get("linked") or [] if isinstance(raw.get("linked"), list) else []:
        if not isinstance(entry, dict):
            continue
        line, text = known.get((str(entry.get("version") or ""), str(entry.get("line_id") or ""))), str(entry.get("clarification") or "").strip()[:1500]
        if line and text:
            found.setdefault((line["question_id"], line["line_id"]), {"question_id": line["question_id"], "version": line["version"],
                                                                       "line_id": line["line_id"], "clarification": text})
    return list(found.values())


def rulings_of(output, chunk, page_round=False, linked=None):
    """{ref: validated ruling or {"failed": why}} for one chunk, plus its clarifications and summary. A missing or malformed ruling fails
    only its own response; an answer without a list of rulings is refused whole (and asked once more). Outside a page round a ruling
    that asks for the whole page is {"needs_page": True, "reason"}: `_resolve_question` asks again for it with the page."""
    if not isinstance(output, dict) or not isinstance(output.get("rulings"), list):
        raise ValueError("The flag review answer has no list of rulings.")
    by_ref = {r["ref"]: r for r in chunk}
    found = {}
    for raw in output["rulings"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("ref"), str) or raw["ref"] not in by_ref or raw["ref"] in found:
            continue
        row = by_ref[raw["ref"]]
        try:
            # v2 answers in its own words (uphold / retick / instructor); a ruling is kept, checked and carried in the arbitrator's.
            raw = {**raw, "decision": DECISIONS.get(raw.get("decision"), raw.get("decision")), "adopt": raw.get("uphold", raw.get("adopt")) or ""}
            if raw.get("needs_page") is True and not page_round:
                found[raw["ref"]] = {"needs_page": True, "reason": str(raw.get("reason") or "")[:4000]}
                continue
            changes = changes_of(raw, row.get("transcript"), row.get("crop_count", len(row.get("images") or ()))) if raw["decision"] != "unreliable" else []
            amended = amend(row["transcript"], changes) if changes else None
            ruling = arbitration.validate(raw, row["labels"], row["flags"], (lambda value: row["graded"](value, amended)) if amended else row["graded"])
            if "deduction_rules" in row:
                flag_adjustments.validate(raw, row, ruling)
            notes = [raw.get(name, []) for name in ("review_reasons", "rubric_ambiguities", "reading_issues")]
            if any(not isinstance(items, list) for items in notes):
                raise ValueError("The ruling's concerns must follow the grading contract.")
            concerns = [str(n) for n in notes[0]] + [str(n) for n in notes[1]] + [str((i or {}).get("description", "")) for i in notes[2] if isinstance(i, dict)]
            if ruling["decision"] == "revise" and concerns:
                ruling["review_required"] = concerns[:6]  # A doubt it could not settle is the instructor's (`arbitration.outcome`).
            if changes:
                ruling["changes"] = changes
            if ruling["decision"] == "unreliable" and (readings := readings_of(raw, {i["id"] for i in row["rubric"].get("items", [])})):
                ruling["readings"] = readings
            if page_round:
                ruling["page_round"] = True
            found[raw["ref"]] = ruling
        except (ValueError, TypeError, KeyError, AttributeError) as failure:
            found[raw["ref"]] = {"failed": True, "error": str(failure)[:300]}
    for ref in by_ref:
        found.setdefault(ref, {"failed": True, "error": "No ruling was returned for this response."})
    known = {item["id"] for item in chunk[0]["rubric"].get("items", [])} if chunk else set()
    proposed = output.get("clarifications") or []
    if not isinstance(proposed, list):
        raise ValueError("The flag review clarifications must be a list.")
    clarifications = [{"line_id": c["line_id"], "clarification": str(c.get("clarification") or "")[:1500], "why": str(c.get("why") or "")[:600],
                       **({"linked": found_linked} if (found_linked := linked_clarifications(c, linked or {})) else {})}
                      for c in proposed if isinstance(c, dict) and isinstance(c.get("line_id"), str) and c["line_id"] in known and str(c.get("clarification") or "").strip()]
    return found, clarifications, str(output.get("summary") or "")[:1500]


def readings_of(raw, known):
    """The alternative grades a ruling left with the instructor names, one per possible reading of the deciding marks (prompt v3,
    requirement, 2026-10-03): [{reading, lines}], 2 to 4 of them, each a complete set of this rubric's line IDs,
    no two alike. They decide nothing: the review desk offers each as a choice the instructor makes. Anything malformed gives []."""
    found, seen = [], set()
    for entry in raw.get("readings") or [] if isinstance(raw.get("readings"), list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), list):
            return []
        lines = sorted({str(i) for i in entry["lines"]})
        text = str(entry.get("reading") or "").strip()[:300]
        if not text or not set(lines) <= known or tuple(lines) in seen:
            return []
        seen.add(tuple(lines))
        found.append({"reading": text, "lines": lines})
    return found if 2 <= len(found) <= 4 else []


def precedent(row, ruling, outcome):
    """What a later chunk reads of an earlier ruling: its decision, the lines of the grade it stood on, and why. Never an identifier."""
    if ruling.get("failed"):
        return None
    if ruling["decision"] == "adopt":
        lines = row["ticked"].get(ruling["adopt"], [])
    elif ruling["decision"] == "revise":
        lines = sorted(a["rubric_item_id"] for a in ruling["assessment"]["applications"] if a.get("applied"))
    else:
        lines = []
    return {"ref": row["ref"], "decision": {v: k for k, v in DECISIONS.items()}.get(ruling["decision"], ruling["decision"]), "outcome": outcome,
            "lines": lines, "reason": ruling.get("reason", "")[:500],
            "rulings": [{"flag": next((f["text"] for f in row["flags"] if f["id"] == k), k)[:200], "note": v.get("note", "")[:300]}
                        for k, v in ruling.get("rulings", {}).items()][:6],
            # What it read differently from the transcript, so that the same mark is read the same way on the next paper.
            **({"transcript_changes": [c["saw"][:200] for c in ruling["changes"]]} if ruling.get("changes") else {}),
            **({"page_round": True} if ruling.get("page_round") else {}),
            **({"score": ruling["scoring"]["expected_score"],
                "deductions": [{k: d[k] for k in ("id", "applied", "reason")} for d in ruling["scoring"]["deductions"]]}
               if ruling.get("scoring") else {})}


def carry(graded, ruling, record, existing, transcript=None):
    """The carrier batch's grading input for one response: the grade the ruling stands on, with the team's opinions it replaces.

    Like the arbitrator's own final step (`arbitration.run`): an adopted assessment is that grader's complete retained assessment, a revised
    one the model's own; the team's second opinion is set aside (the ruling decided between them), and the earlier arbitration and flag
    review move into this record, so neither is applied again over the new grade. `transcript`: the transcript as the ruling changed it
    (`amend`), which the scoring stage takes in place of the reader's (`FLAG_TRANSCRIPT`, read by `jobs`)."""
    opinions = copy.deepcopy(graded.get("opinions") or {})
    earlier = {name: opinions.pop(name) for name in ("arbiter", "flag_review") if opinions.get(name)}
    if ruling["decision"] == "adopt":
        final = copy.deepcopy(existing[ruling["adopt"]])
    else:
        final = {**copy.deepcopy(ruling["assessment"]), "endpoint": record.get("endpoint"), "model": record.get("model"), "lineage": record.get("lineage", ""),
                 "input_mode": "images_and_transcript", "saw_other_decisions": True}
    decision = copy.deepcopy(opinions.get("committee") or {})
    committee = {**decision, "status": "resolved", "team_status": decision.get("status", ""), "resolved_by": BASIS,
                 "source": ruling["adopt"] or BASIS, "supporter": BASIS,
                 "reason": (f"The flag review adopted the {ruling['adopt']} assessment as the accurate final grade." if ruling["decision"] == "adopt"
                            else "The flag review found no earlier assessment accurate and posed the final rubric selections.")}
    roles = {**opinions.get("roles", {}), BASIS: {"endpoint": record.get("endpoint"), "model": record.get("model"), "lineage": record.get("lineage", "")}}
    if ruling["decision"] == "revise":
        roles["primary"] = roles[BASIS]
    roles.pop("second", None)
    return {**final, "opinions": {**opinions, "second": None, "roles": roles, "committee": committee, BASIS: {**record, "earlier": earlier}},
            **({flag_adjustments.TRANSIENT: ruling["scoring"]["adjustments"]} if ruling.get("scoring") else {}),
            **({FLAG_TRANSCRIPT: transcript} if transcript else {})}


def amended(verified, graded):
    """The reading the carrier batch scores on: the transcript as the ruling changed it, taken off `graded`, else the reader's."""
    transcript = graded.pop(FLAG_TRANSCRIPT, None)
    return {**verified, "transcript": transcript} if transcript else verified


def apply(output, record, proposal=None):
    """The ruling over the grade as scored again: only flags that were shown AND marked resolved are settled, never a reserved one.
    `proposal`: the scored grade's own requests and doubts, under `arbitration.SETTLED_RULE` (`arbitration.renamed_notes`), else None."""
    output = copy.deepcopy(output)
    signals = output["signals"]
    signals[BASIS] = record
    resolved = {arbitration._same(flag["text"]) for flag in record["flags"] if flag.get("resolved") and not reserved(flag["text"])}
    standing = [t for t in arbitration.flags({**output, "signals": {**signals, "rubric_quality": {}}}) if t not in resolved]
    standing = arbitration.renamed_notes(standing, resolved, proposal, reserved)
    if (record.get("rule") == RULE and not record.get("scoring")) or flag_adjustments.mismatch(record.get("scoring"), output.get("score")):
        standing.append("GRADING: the flag review's required deductions or expected score did not survive scoring.")
    if standing or output.get("score") is None:
        signals[BASIS] = {**record, "outcome": "unsettled", "open_flags": standing}
        said = "The flag review's grade raised or left a flag it did not settle: " + "; ".join(standing[:3] or ["the grade cannot be scored"])
        output["reasons"] = list(dict.fromkeys([*output["reasons"], said]))
        signals["risk"] = {**signals["risk"], "individual": True, "blocks": list(dict.fromkeys([*signals["risk"].get("blocks", []), said]))}
        if signals.get("committee"):
            signals["committee"] = {**signals["committee"], "status": "unresolved", "reason": said}
        if output["state"] == "proposed":
            output["state"] = "grading_review"
        return output
    # Settled: what was flagged stays on the record with each ruling, and the grade goes to the ordinary acceptance gate.
    signals["risk"] = {**signals["risk"], "flag_resolved": {"blocks": signals["risk"].get("blocks", []), "reasons": output["reasons"],
                                                            "individual": bool(signals["risk"].get("individual")), "state": output["state"]},
                       "blocks": [], "individual": False}
    output["reasons"], output["state"] = [], "proposed"
    return output


class FlagResolutionMixin:
    def _flag_reset_plan(self, db, aid, rid):
        """GRADING FLAG REVIEW RESET: restore only this review's own unreviewed proposals, by reference."""
        self.plan(db, aid)
        row = db.execute("SELECT * FROM frq_flag_resolutions WHERE id=? AND assignment_id=?", (rid, aid)).fetchone()
        if not row:
            raise NotFound("Flag review not found.")
        if row["status"] != "complete":
            raise Conflict("GRADING: only a finished flag review can be reset.")
        if db.execute("SELECT 1 FROM frq_certifications WHERE assignment_id=? LIMIT 1", (aid,)).fetchone():
            raise Conflict("GRADING: a flag review of a certified assessment cannot be reset.")
        busy = bool(
            db.execute("SELECT 1 FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id WHERE j.assignment_id=? AND t.status IN ('pending','running') LIMIT 1", (aid,)).fetchone()
            or db.execute("SELECT 1 FROM frq_jobs WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone()
            or db.execute("SELECT 1 FROM frq_flag_resolutions WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone())
        snapshots = [r for q in json.loads(row["input_json"])["questions"] for r in q["responses"]]
        jobs = set(json.loads(row["result_json"] or "{}").get("jobs", []))
        members, counts, memo = [], {"restore": 0, "already_original": 0, "kept_reviewed": 0, "changed": 0}, {}
        for original in snapshots:
            current = db.execute("""SELECT r.version,r.current_result_id,r.current_review_id,t.job_id,
                p.rubric_id,p.question_revision_id FROM frq_responses r
                LEFT JOIN frq_results x ON x.id=r.current_result_id LEFT JOIN frq_tasks t ON t.id=x.task_id
                JOIN frq_assignment_questions p ON p.assignment_id=r.assignment_id AND p.question_id=r.question_id
                WHERE r.id=? AND r.assignment_id=?""", (original["response_id"], aid)).fetchone()
            source = db.execute("SELECT * FROM frq_tasks WHERE id=?", (original["task_id"],)).fetchone()
            source_current = bool(current and source and source["response_id"] == original["response_id"])
            if source_current:
                try:
                    self.validate_task(db, dict(source), memo=memo)  # Includes INBOX PRESENT and the exact reviewed source.
                except (Conflict, NotFound):
                    source_current = False
            kind = ("changed" if not source_current else
                    "kept_reviewed" if current["current_review_id"] else
                    "changed" if (source["rubric_id"], source["question_revision_id"]) != (current["rubric_id"], current["question_revision_id"]) else
                    "already_original" if current["current_result_id"] == original["result_id"] else
                    "restore" if current["job_id"] in jobs else "changed")
            counts[kind] += 1
            members.append({"response_id": original["response_id"], "original": original["result_id"], "kind": kind,
                            "version": current["version"] if current else None, "current": current["current_result_id"] if current else None})
        preview = {"assignment_id": aid, "resolution_id": rid, "responses": len(snapshots), "running": busy, **counts,
                   "fingerprint": digest([rid, busy, members]),
                   "note": "Restores the original grades and flags. Review history is kept; instructor decisions and newer work are left alone."}
        return preview, members

    def flag_reset_preview(self, aid, rid):
        with self.rosters.lock(), connect(self.path) as db:
            return self._flag_reset_plan(db, aid, rid)[0]

    def reset_flag_resolution(self, aid, rid, data):
        request_id = text(data.get("request_id"), "Request ID", 128, False)
        if data.get("confirmation") != "reset":
            raise ValueError("GRADING: confirm restoring the original grades and flags.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("""SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='flag_resolution_reset'
                AND json_extract(detail_json,'$.request_id')=? ORDER BY id DESC LIMIT 1""", (aid, request_id)).fetchone()
            if previous:
                receipt = json.loads(previous["detail_json"])
                if receipt["resolution_id"] != rid or receipt["fingerprint"] != data.get("fingerprint"):
                    raise Conflict("GRADING: this request ID was already used for another flag-review reset.")
                return {k: v for k, v in receipt.items() if k != "members"}
            preview, members = self._flag_reset_plan(db, aid, rid)
            if preview["running"]:
                raise Conflict("GRADING: wait until this assessment's grading and flag review finish before resetting it.")
            if data.get("fingerprint") != preview["fingerprint"]:
                raise Conflict("GRADING: these grades changed. Preview the reset again before confirming it.")
            if not preview["restore"]:
                raise Conflict("GRADING: this review has no current grades to restore.")
            restored = [m for m in members if m["kind"] == "restore"]
            for member in restored:
                changed = db.execute("""UPDATE frq_responses SET current_result_id=?,version=version+1
                    WHERE id=? AND version=? AND current_result_id=? AND current_review_id IS NULL""",
                    (member["original"], member["response_id"], member["version"], member["current"])).rowcount
                if changed != 1:
                    raise Conflict("GRADING: a grade changed during the reset. Nothing was restored.")
            receipt = {**preview, "restored": len(restored), "status": "complete", "request_id": request_id, "at": timestamp(), "members": restored}
            from request_actor import current
            self.event(db, aid, "flag_resolution_reset", receipt, current())
            self.touch(db, aid)
        return {k: v for k, v in receipt.items() if k != "members"}

    def _resolution_model(self):
        settings = self.settings()["settings"]
        endpoint = config.rubric_reviewer(settings)
        if not endpoint:
            raise ValueError("Choose a Rubric drafting and review model in Compute settings first: it runs the flag review.")
        if endpoint["modality"] != "vision":
            raise ValueError(f"{endpoint['name']} cannot read images, and the flag review rules with the crops. Choose a Rubric drafting and review model that reads images.")
        return {**endpoint, "max_tokens": max(endpoint.get("max_tokens") or 0, TOKENS)}, settings

    @staticmethod
    def _existing(db, task):
        """Every retained step of a response's grading, following a reused result back to the batch that graded it."""
        found, seen = {}, set()
        current = dict(task)
        while current and current["id"] not in seen:
            seen.add(current["id"])
            for row in db.execute("SELECT stage,output_json FROM frq_steps WHERE task_id=? ORDER BY attempt,rowid", (current["id"],)):
                found.setdefault(row[0], json.loads(row[1]))
            prior = current.get("reuse_result_id")
            current = dict(db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (prior,)).fetchone() or {}) if prior else None
        return found

    def candidates(self, db, aid):
        """The plan's held grades a ruling could finalize, per question: ({question_id: [row]}, {why left out: count}). The caller holds
        the lifecycle lock. Each row names its current result, never a student, for the model."""
        left = {}
        def leave(why):
            left[why] = left.get(why, 0) + 1
        by_question = {}
        rows = db.execute("""SELECT r.id response_id,r.question_id,r.current_result_id,x.*,t.id task_id,t.job_id,t.rubric_id task_rubric,
                t.question_revision_id task_revision,j.profile_json,j.tier FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id
                JOIN frq_tasks t ON t.id=x.task_id JOIN frq_jobs j ON j.id=t.job_id
                WHERE r.assignment_id=? AND r.current_review_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM frq_all_acceptances a WHERE a.result_id=x.id)
                AND NOT EXISTS(SELECT 1 FROM frq_tasks p WHERE p.response_id=r.id AND p.status IN ('pending','running'))
                ORDER BY r.question_id,r.rowid""", (aid,)).fetchall()
        for row in rows:
            output = output_of(row)
            if not arbitration.waits(output):
                continue
            settings = json.loads(row["profile_json"])
            if row["tier"] not in config.TIERS or settings.get("grading_workflow") != "grading-teams-v1":
                leave("graded outside a Cloud team batch"); continue
            flags = arbitration.flags(output)
            if any(reserved(f) for f in flags) or output["score"] is None:
                leave("held for something only you can decide (a possibly blank answer, no line ticked, an incomplete pair)"); continue
            if (output["signals"].get("rubric_quality") or {}).get("reasons"):
                leave("breaks your confirmed rubric finalization"); continue
            if not flags:
                leave("waiting without a flag to rule on"); continue
            task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (row["task_id"],)).fetchone())
            try:
                _, question, _ = self.validate_task(db, task)
            except (Conflict, NotFound, ValueError):
                leave("graded under an earlier paper, question or rubric (it is graded again instead)"); continue
            if question["rubric_id"] != row["task_rubric"]:
                leave("graded under an earlier paper, question or rubric (it is graded again instead)"); continue
            existing = self._existing(db, task)
            labels = arbitration.candidates(existing)
            if not labels:
                leave("no complete assessment to rule on"); continue
            standing = output["signals"].get("committee", {}).get("source") or ""
            standing = standing if standing in labels else next(iter(labels))
            by_question.setdefault(row["question_id"], []).append({
                "response_id": row["response_id"], "result_id": row["current_result_id"], "task_id": row["task_id"], "job_id": row["job_id"],
                "flags": [{"id": f"F{n}", "text": t} for n, t in enumerate(flags, 1)], "labels": sorted(labels),
                "standing": standing, "score": (output["score"] or {}).get("score")})
        return by_question, left

    def flag_resolution_preview(self, aid, found):
        """What a flag review would be sent now: counts per question and what is left out, and why. Writes nothing. `found`:
        `candidates`' answer, which the caller has already read (one read serves the preview and the automatic review's line)."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            by_question, left = found
            questions = []
            for qid, rows in by_question.items():
                q = self.question(db, aid, qid)
                number = str(q["content"].get("printed_number") or q["content"].get("number") or q["question_key"])
                questions.append({"question_id": qid, "label": f"{q['content'].get('title') or 'Question ' + number}" + (f" · Version {q['exam_version']}" if q["exam_version"] else ""),
                                  "responses": len(rows), "requests": -(-len(rows) // CHUNK)})
        endpoint = config.rubric_reviewer(self.settings()["settings"])
        return {"questions": questions, "responses": sum(q["responses"] for q in questions), "left_out": [{"why": k, "responses": v} for k, v in left.items()],
                "model": (endpoint or {}).get("name"), "chunk": CHUNK}

    def queue_flag_resolution(self, aid, data, *, actor="workspace", only=None):
        """Request a flag review of every held grade it may rule on, or, for the review that starts by itself (`auto_flag_review.py`),
        of those among the result IDs in `only` that still wait."""
        request_id = text(data.get("request_id"), "Request ID", 128, False)
        endpoint, settings = self._resolution_model()
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            prior = db.execute("SELECT * FROM frq_flag_resolutions WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["assignment_id"] != aid:
                    raise Conflict("This request ID already belongs to another flag review.")
                return self.flag_resolution_summary(db, prior)
            if db.execute("SELECT 1 FROM frq_flag_resolutions WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
                raise Conflict("A flag review of this assignment is already running. Wait for it, or stop it.")
            by_question, left = self.candidates(db, aid)
            if only is not None:
                by_question = {qid: kept for qid, rows in by_question.items() if (kept := [r for r in rows if r["result_id"] in only])}
            if not by_question:
                raise ValueError("No held grade of this assignment can be ruled on now.")
            questions = [{"question_id": qid, "responses": [{k: r[k] for k in ("response_id", "result_id", "task_id", "job_id")} for r in rows]}
                         for qid, rows in by_question.items()]
            rid, now = uid(), timestamp()
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO frq_flag_resolutions(id,assignment_id,request_id,request_sha256,input_json,profile_json,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'pending',?,?)""", (rid, aid, request_id, digest([aid, request_id, endpoint["name"]]),
                encoded({"rule": RULE, "questions": questions, "left_out": left, "settled_notes": True, **({"automatic": True} if actor == "automatic" else {})}),
                encoded({"endpoint": endpoint, "settings": settings}), now, now))
            self.event(db, aid, "flag_resolution_queued", {"resolution_id": rid, "model": endpoint["name"], "questions": len(questions),
                                                            "responses": sum(len(q["responses"]) for q in questions)}, actor)
            db.commit()
            summary = self.flag_resolution_summary(db, db.execute("SELECT * FROM frq_flag_resolutions WHERE id=?", (rid,)).fetchone())
        self.inbox.wake.set()
        return summary

    def cancel_flag_resolution(self, aid, rid):
        """Stop a waiting or running flag review. A request already answered keeps its receipt; nothing further is sent or applied."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE frq_flag_resolutions SET status='failed',cancelled=1,error='Stopped by the instructor.',updated_at=? WHERE id=? AND assignment_id=? AND status IN ('pending','running')",
                          (timestamp(), rid, aid)).rowcount:
                self.event(db, aid, "flag_resolution_cancelled", {"resolution_id": rid})
            db.commit()
        return self.flag_resolution(aid)

    def flag_resolution(self, aid):
        """The plan's latest flag review with its rulings, the earlier ones in brief, and what one would be sent now. Read only."""
        current = self.settings()
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            rows = db.execute("SELECT * FROM frq_flag_resolutions WHERE assignment_id=? ORDER BY rowid DESC LIMIT 6", (aid,)).fetchall()
            latest = self.flag_resolution_summary(db, rows[0]) if rows else None
            history = [self.flag_resolution_summary(db, row, brief=True) for row in rows[1:]]
            found = self.candidates(db, aid)
            # Whether the review starts by itself for these grades, and which (`auto_flag_review.py`, 2026-10-11).
            automatic = self.auto_flag_review_state(db, aid, current, found)
        return {"resolution": latest, "history": history, "preview": self.flag_resolution_preview(aid, found), "automatic": automatic}

    def flag_resolution_summary(self, db, row, *, brief=False):
        context, profile = json.loads(row["input_json"]), json.loads(row["profile_json"])
        # GRADING "FLAG REVIEW PROGRESS": count retained calls, not an estimate from response counts. Image limits, page
        # rounds and repaired answers can all add requests. Replayed receipts add no call and are never counted twice.
        calls = db.execute("SELECT step,state,confirmation_json FROM frq_cloud_calls WHERE flag_resolution_id=? ORDER BY rowid", (row["id"],)).fetchall()
        progress = {"requests": len(calls), "answered": sum(c["state"] == "completed" for c in calls),
                    "running": sum(c["state"] == "submitted" for c in calls)}
        if row["status"] == "pending" and row["not_before"]:
            latest = {c["step"]: c for c in calls}
            unsettled = [c for c in latest.values() if c["state"] in ("failed", "unknown")]
            confirmations = [json.loads(c["confirmation_json"] or "{}") for c in unsettled]
            kind = ("usage_limit" if any(c.get("usage_limit") for c in confirmations) else
                    "capacity" if any(c.get("status") in (429, 503, 529) for c in confirmations) else
                    "unknown" if any(c["state"] == "unknown" for c in unsettled) else "retry")
            progress["wait"] = {"kind": kind, "until": row["not_before"]}
        summary = {"id": row["id"], "status": row["status"], "cancelled": bool(row["cancelled"]), "error": row["error"], "created_at": row["created_at"],
                   "updated_at": row["updated_at"], "model": profile["endpoint"]["name"], "rule": context["rule"], "questions": len(context["questions"]),
                   "responses": sum(len(q["responses"]) for q in context["questions"]), "left_out": context.get("left_out") or {},
                   "progress": progress, **({"automatic": True} if context.get("automatic") else {}),
                   # The responses being ruled on, while it runs: the review workspace steps past them.
                   **({"response_ids": [r["response_id"] for q in context["questions"] for r in q["responses"]]} if row["status"] in ("pending", "running") else {})}
        reset = db.execute("""SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='flag_resolution_reset'
            AND json_extract(detail_json,'$.resolution_id')=? ORDER BY id DESC LIMIT 1""", (row["assignment_id"], row["id"])).fetchone()
        if reset:
            receipt = json.loads(reset["detail_json"])
            summary["reset"] = {k: receipt[k] for k in ("at", "restored", "already_original", "kept_reviewed", "changed")}
        result = json.loads(row["result_json"]) if row["result_json"] else None
        if result:
            summary["totals"] = result["totals"]
            summary["jobs"] = result.get("jobs", [])
            if not brief:
                # How each carried grade ended: finalized by the carrier batch, still held, or not yet scored.
                finals = {}
                for jid in result.get("jobs", []):
                    for r in db.execute("""SELECT t.response_id,t.status,x.state,EXISTS(SELECT 1 FROM frq_all_acceptances a WHERE a.result_id=x.id) accepted
                            FROM frq_tasks t LEFT JOIN frq_results x ON x.task_id=t.id WHERE t.job_id=?""", (jid,)):
                        finals[r[0]] = "finalized" if r[3] else "held" if r[2] else "failed" if r[1] == "failed" else "scoring"
                for q in result["questions"]:
                    for entry in q["responses"]:
                        if entry["response_id"] in finals:
                            entry["final"] = finals[entry["response_id"]]
                summary["finalized"] = sum(v == "finalized" for v in finals.values())
                summary["result"] = result
        return summary

    # Worker -------------------------------------------------------------------------------------------------------------------
    def _resolution_ask(self, rid, endpoint, settings, step, payload, images, check, prompt=PROMPT):
        """One request, asked once more when its answer is refused. What was answered before is replayed, never sent again."""
        refused = None
        for attempt in (0, 1):
            reply = self.cloud_call({"flag_resolution_id": rid, "step": step, "task_attempt": attempt}, "rubric_reviewer", endpoint,
                                    {"prompt": prompt, "payload": payload, "images": images, **({"reask": True} if attempt else {})}, settings)
            try:
                return check(reply["output"])
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    def _resolution_rows(self, snapshot, left_out=None):
        """Everything the model and the validator need for each snapshotted response, read again from its retained records. A response
        whose paper, question or rubric changed since the review was requested is left out (appended to `left_out` when given), as
        `candidates` leaves it out; the others keep their refs, numbered by place in the snapshot."""
        rows = []
        with connect(self.path) as db:
            for n, ref in enumerate(snapshot, 1):
                task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (ref["task_id"],)).fetchone())
                result = db.execute("SELECT * FROM frq_results WHERE id=?", (ref["result_id"],)).fetchone()
                job = dict(db.execute("SELECT * FROM frq_jobs WHERE id=?", (ref["job_id"],)).fetchone())
                settings = json.loads(job["profile_json"])
                try:
                    _, question, _ = self.validate_task(db, task)
                except (Conflict, NotFound):
                    if left_out is not None:
                        left_out.append({"response_id": ref["response_id"], "why": "graded under an earlier paper, question or rubric (it is graded again instead)"})
                    continue
                content, rubric = question["content"], question["rubric"]["rules"]
                guidance = self.rubric_guidance(db, settings.get("rubric_finalization"), question["question_id"], task["rubric_id"])
                existing = self._existing(db, task)
                labels = arbitration.candidates(existing)
                output = output_of(result)
                flags = [{"id": f"F{i}", "text": t} for i, t in enumerate(arbitration.flags(output), 1)]
                transcript = json.loads(result["transcript_json"])
                try:
                    crops = self.response_crops(db, task)["crops"]
                    pictures = [storage.read(self.inbox, crop["images"].get("original") or crop["images"]["cloud"]) for crop in crops]
                except (KeyError, TypeError, ValueError, NotFound, OSError):
                    crops, pictures = [], []
                standing = (output["signals"].get("committee") or {}).get("source") or ""
                # `amended`: the transcript as the ruling changed it (`amend`), which its own quotations are checked against.
                def graded(value, amended=None, transcript=transcript, rubric=rubric, content=content, count=max(1, len(pictures)), settings=settings, guidance=guidance):
                    checked = recognition.validate_grade(value, amended or transcript, rubric, content["max_points"], count)
                    if {a["rubric_item_id"] for a in checked["applications"]} != {item["id"] for item in rubric["items"]}:
                        raise ValueError("A final set of ticks must decide every rubric item.")
                    if settings.get("full_credit_rule") in rubric_quality.FULL_CREDIT_RULES:
                        checked = rubric_quality.full_credit_alone(checked, guidance, rubric, content["max_points"], settings["full_credit_rule"])
                    return checked
                rows.append({**ref, "ref": f"R{n}", "content": content, "rubric": rubric, "guidance": guidance, "transcript": transcript,
                             "existing": {label: existing[key] for label, key in labels.items()}, "labels": sorted(labels),
                             "ticked": {label: sorted(a["rubric_item_id"] for a in existing[key].get("applications", []) if a.get("applied")) for label, key in labels.items()},
                             "standing": standing if standing in labels else "", "flags": flags, "output": output, "images": pictures, "graded": graded,
                             "panels": output["signals"].get("panels") or [],
                             # Where each crop was cut from, for a page round (`_page_views`).
                             "regions": json.loads(task["source_json"]).get("regions") or [], "crop_meta": [crop.get("metadata") or {} for crop in crops]})
        return rows

    @staticmethod
    def _chunks(rows):
        """Up to `CHUNK` responses per request, cut short before the crops would pass `MAX_IMAGES` or `IMAGES` bytes."""
        chunks, current, count, size = [], [], 0, 0
        for row in rows:
            n, b = len(row["images"]), sum(map(len, row["images"]))
            if current and (len(current) >= CHUNK or count + n > MAX_IMAGES or size + b > IMAGES):
                chunks.append(current)
                current, count, size = [], 0, 0
            current.append(row)
            count, size = count + n, size + b
        return chunks + ([current] if current else [])

    @staticmethod
    def _model_row(row):
        """What the model reads of one response: never an identifier, a name, a section or which model graded. In a page round its
        `images` are its crops and `pages` the page images attached after them."""
        return {"ref": row["ref"], "transcript": row["transcript"], "standing": row["standing"],
                "assessments": {label: arbitration._public(opinion, blind=True) for label, opinion in row["existing"].items()},
                "decisions": [{k: (panel.get("decision") or {}).get(k) for k in ("status", "reason", "source", "supporter")} for panel in row["panels"]],
                "flags": row["flags"], "answer_checks": {row["standing"] or "standing": arbitration.answer_facts(row["output"])},
                "images": row.get("crop_count", len(row["images"])), **({"pages": row["pages"]} if "pages" in row else {})}

    def _scan_page(self, region):
        """The retained raster one crop was cut from, checked against the checksum its task recorded, or None."""
        try:
            _, scan = self.inbox.read_artifact(region["raster_artifact_id"])
        except (NotFound, OSError, ValueError):
            return None
        return scan if digest(scan) == region.get("raster_sha256") else None

    def _page_views(self, row):
        """For a page round: each scanned page a response's crops were cut from, turned and straightened exactly as its crop was, with every
        crop's area outlined in red. The retained raster is read through the Inbox storage and its checksum; nothing is stored. [] when a
        page cannot be shown (the response then stays with the instructor)."""
        from PIL import Image, ImageDraw
        from . import deskew
        pages, order = {}, []
        for index, region in enumerate(row.get("regions") or []):
            meta = (row.get("crop_meta") or [])[index] if index < len(row.get("crop_meta") or []) else {}
            bounds, tilt = meta.get("crop_bounds"), meta.get("deskew") or {}
            if not bounds or not region.get("raster_artifact_id"):
                return []
            key = (region["raster_artifact_id"], tilt.get("angle") if tilt.get("straightened") else None, bool((meta.get("orientation") or {}).get("turned")))
            if key not in pages:
                scan = self._scan_page(region)
                try:
                    image = Image.open(io.BytesIO(scan or b"")).convert("RGB")
                except (OSError, ValueError):
                    return []
                if key[1] is not None:  # the crop was cut from the page straightened (`extraction`), so its bounds are in that frame
                    image = deskew.straighten(image, key[1], (255, 255, 255))[0]
                if key[2]:
                    image = image.transpose(Image.Transpose.ROTATE_180)
                pages[key] = image
                order.append(key)
            ImageDraw.Draw(pages[key]).rectangle([int(v) for v in bounds], outline=(220, 30, 30), width=max(4, pages[key].width // 300))
        views = []
        for key in order:
            image = pages[key]
            image.thumbnail((PAGE_WIDTH, PAGE_WIDTH))
            buffer = io.BytesIO()
            image.quantize(colors=64).save(buffer, "PNG")  # a scanned page and a red outline need few colours: a fifth of the bytes
            views.append(buffer.getvalue())
        return views

    def _linked_lines(self, rid, qid):
        """{line ID: [{question_id, version, line_id, description}]}: the same line on the problem's other versions ("same" links only,
        LINKED VERSIONS), for the flag review to word a clarification for each of them too. {} without links."""
        with connect(self.path) as db:
            aid = db.execute("SELECT assignment_id FROM frq_flag_resolutions WHERE id=?", (rid,)).fetchone()[0]
            state = self.line_link_state(db, aid)
        by_id = {q["question_id"]: q for q in state["questions"]}
        found = {}
        for set_ in state["sets"]:
            if set_["kind"] != "same" or set_.get("conflict"):
                continue
            mine = [line["item_id"] for line in set_["lines"] if line["question_id"] == qid]
            if len(mine) != 1:
                continue
            for line in set_["lines"]:
                other = by_id.get(line["question_id"])
                if line["question_id"] == qid or not other:
                    continue
                item = next((i for i in other["rubric"]["items"] if i["id"] == line["item_id"]), None)
                if item:
                    found.setdefault(mine[0], []).append({"question_id": line["question_id"], "version": other.get("exam_version") or "",
                                                          "line_id": item["id"], "description": str(item.get("description") or "")[:600]})
        return found

    def _resolve_question(self, rid, endpoint, settings, snapshot, rule=None):
        """(entry, wait seconds or None) for one question and version: its chunks in order, each shown the rulings before it."""
        left_out = []
        rows = self._resolution_rows(snapshot, left_out)
        entry = {"question_id": snapshot[0].get("question_id"), "responses": [], "clarifications": [], "summaries": [], "error": ""}
        if left_out:
            entry["left_out"] = left_out
        if not rows:  # every response of this question changed: nothing to ask, and nothing to carry
            with connect(self.path) as db:
                aid = db.execute("SELECT assignment_id FROM frq_flag_resolutions WHERE id=?", (rid,)).fetchone()[0]
                content = self.question(db, aid, entry["question_id"])["content"]
            entry["line_text"] = {}
            entry["label"] = self._question_label(content, snapshot)
            return entry, None
        first = rows[0]
        content = first["content"]
        number = str(content.get("printed_number") or content.get("number") or "")
        precedents, clarified = [], {}
        question = {"title": content.get("title") or "", "printed_number": number, "points": content.get("max_points"),
                    "problem": content.get("problem") or "",
                    "solution": content.get("worked_solution") or content.get("instructor_solution") or content.get("coordinator_solution") or "",
                    "answer_key": [{"label": p.get("label", ""), "accept": list(p.get("accept", []))} for p in content.get("answer_key") or []]}
        rubric = grader_rubric(first["rubric"], first["guidance"])
        deductions = flag_adjustments.rules_of(rubric) if rule == RULE else None
        if deductions is not None:
            for row in rows:
                row["deduction_rules"] = deductions
        linked = self._linked_lines(rid, entry["question_id"])

        def ask(step, part, page_round=False):
            payload = {"question": question, "rubric": rubric, "answer_parts": [p["label"] for p in content.get("answer_key", [])] or [""],
                       **({"deduction_rules": deductions} if deductions is not None else {}),
                       **({"linked_lines": {iid: [{k: l[k] for k in ("version", "line_id", "description")} for l in lines] for iid, lines in linked.items()}}
                          if linked else {}),
                       "precedents": precedents[-PRECEDENTS:], "clarifications_so_far": list(clarified.values()),
                       **({"page_round": True} if page_round else {}), "responses": [self._model_row(r) for r in part]}
            images = [image for r in part for image in r["images"]]
            found, clarifications, summary = self._resolution_ask(rid, endpoint, settings, step, payload, images,
                                                                  lambda output: rulings_of(output, part, page_round, linked),
                                                                  prompt=ADJUSTED_PROMPT if rule == RULE else PROMPT)
            for c in clarifications:
                clarified[c["line_id"]] = c
            if summary:
                entry["summaries"].append(summary)
            return found
        try:
            for n, chunk in enumerate(self._chunks(rows)):
                found = ask(f"flags:{entry['question_id']}:{n}", chunk)
                # A page round for the responses whose deciding writing runs past the crop's edge: the same model, asked once more
                # with each one's whole page, its area outlined. What it says there is its ruling.
                paged = []
                for row in chunk:
                    if not found[row["ref"]].get("needs_page"):
                        continue
                    try:
                        views = self._page_views(row)
                    except (OSError, ValueError, TypeError):  # a page that cannot be drawn concerns this response alone
                        views = []
                    if views:
                        paged.append({**row, "images": row["images"] + views, "crop_count": len(row["images"]), "pages": len(views)})
                    else:
                        said = found[row["ref"]].get("reason") or ""
                        found[row["ref"]] = {"decision": "unreliable", "adopt": "", "rulings": {}, "confidence": None,
                                             "reason": f"{said} Its whole page could not be shown, so it stays with you.".strip()}
                for m, part in enumerate(self._chunks(paged)):
                    found.update(ask(f"flags:{entry['question_id']}:{n}:page:{m}", part, True))
                for row in chunk:
                    ruling = found[row["ref"]]
                    outcome = arbitration.outcome(ruling, row["flags"])
                    entry["responses"].append({"response_id": row["response_id"], "result_id": row["result_id"], "task_id": row["task_id"],
                                               "job_id": row["job_id"], "ref": row["ref"], "outcome": outcome, "ruling": ruling,
                                               "flags": [{**f, **(ruling.get("rulings", {}).get(f["id"]) or {"resolved": False, "note": ""})} for f in row["flags"]],
                                               "score_before": (row["output"]["score"] or {}).get("score"), "standing_before": row["standing"]})
                    seen = precedent(row, ruling, outcome)
                    if seen:
                        precedents.append(seen)
            self._looks(rid, endpoint, settings, entry, rows)
        except RetryLater as waiting:
            return None, waiting.seconds
        except (ProviderError, ValueError, TypeError, KeyError) as failure:
            entry["error"] = f"This question could not be reviewed: {str(failure)[:400]}"
        entry["clarifications"] = list(clarified.values())
        entry["line_text"] = {item["id"]: {"text": str(item.get("description") or "")[:200], "points": item.get("points")} for item in first["rubric"].get("items", [])}
        entry["label"] = self._question_label(content, snapshot)
        return entry, None

    def _looks(self, rid, endpoint, settings, entry, rows):
        """GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (`flag_look`): each response this question's rulings left with the instructor with
        reading choices is asked about once more, alone, for where the deciding marks are on its crops; the usable spots are kept on its
        ruling as `look`. One of the review's own calls each, replayed from its receipt. A look that fails or names nothing usable
        leaves its ruling as it was; only a wait (`RetryLater`) or a stop is passed on, as for a ruling."""
        pictures = {row["response_id"]: row["images"] for row in rows}
        waiting = [r for r in entry["responses"] if flag_look.wanted(r) and pictures.get(r["response_id"])]

        def ask(response):
            images = pictures[response["response_id"]]
            try:
                reply = self.cloud_call({"flag_resolution_id": rid, "step": f"look:{response['response_id']}", "task_attempt": 0}, "rubric_reviewer", endpoint,
                                        {"prompt": flag_look.PROMPT, "payload": flag_look.payload(response["ruling"]), "images": images}, settings)
                return flag_look.spots_of(reply["output"], len(images))
            except (ProviderError, ValueError, TypeError, KeyError, AttributeError):
                return []
        if waiting:
            with ThreadPoolExecutor(max_workers=min(flag_look.PARALLEL, len(waiting))) as pool:
                for response, spots in zip(waiting, list(pool.map(ask, waiting))):
                    if spots:
                        response["ruling"]["look"] = spots

    @staticmethod
    def _question_label(content, snapshot):
        number = str(content.get("printed_number") or content.get("number") or "")
        return (content.get("title") or f"Question {number}") + (f" · Version {snapshot[0]['exam_version']}" if snapshot[0].get("exam_version") else "")

    def _carry(self, db, resolution, entries, endpoint):
        """One "Recalculate points" batch per source batch for the responses whose ruling can stand, in the caller's transaction. Returns
        the batch IDs. A response whose current result moved since the snapshot, or that is now reviewed or busy, is left alone."""
        by_job, jobs = {}, []
        for entry in entries:
            for r in entry["responses"]:
                if r["outcome"] in ("adopted", "revised"):
                    by_job.setdefault(r["job_id"], []).append(r)
        now = timestamp()
        for source_id, members in by_job.items():
            source = dict(db.execute("SELECT * FROM frq_jobs WHERE id=?", (source_id,)).fetchone())
            settings = {**json.loads(source["profile_json"]), "flag_resolution_rule": json.loads(resolution["input_json"])["rule"], "flag_resolution_id": resolution["id"],
                        "flag_resolution_model": endpoint["name"],
                        # A review queued under the rule settles a renamed note (GRADING "A SETTLED NOTE STAYS SETTLED").
                        **({"settled_notes_rule": arbitration.SETTLED_RULE} if json.loads(resolution["input_json"]).get("settled_notes") else {})}
            ready = []
            for r in members:
                current = db.execute("SELECT current_result_id,current_review_id FROM frq_responses WHERE id=?", (r["response_id"],)).fetchone()
                busy = db.execute("SELECT 1 FROM frq_tasks WHERE response_id=? AND status IN ('pending','running')", (r["response_id"],)).fetchone()
                if current and current[0] == r["result_id"] and not current[1] and not busy:
                    ready.append(r)
                else:
                    r["outcome_note"] = "Its grade changed after the review was requested, so this ruling was not applied."
            if not ready:
                continue
            jid = uid()
            db.execute("""INSERT INTO frq_jobs(id,assignment_id,request_id,request_sha256,mode,operation,shadow,status,profile_json,estimated_cost,created_at,updated_at,tier)
                VALUES(?,?,?,?,?,'rescore',0,'pending',?,0,?,?,?)""", (jid, source["assignment_id"], f"flag-resolution:{resolution['id']}:{source_id}",
                digest([resolution["id"], source_id, sorted(r["response_id"] for r in ready)]), source["mode"], encoded(settings), now, now, source["tier"]))
            lane = self.next_lane({"mode": source["mode"], "operation": "rescore"}, settings, "scoring", (), 0)
            for r in ready:
                task = db.execute("SELECT * FROM frq_tasks WHERE id=?", (r["task_id"],)).fetchone()
                db.execute("""INSERT INTO frq_tasks(id,job_id,response_id,question_revision_id,rubric_id,source_json,source_sha256,
                    reuse_result_id,human_transcript_json,stage,status,created_at,updated_at,human_review_id,lane) VALUES(?,?,?,?,?,?,?,?,NULL,'scoring','pending',?,?,NULL,?)""",
                    (uid(), jid, r["response_id"], task["question_revision_id"], task["rubric_id"], task["source_json"], task["source_sha256"], r["result_id"], now, now, lane))
            self.event(db, source["assignment_id"], "job_queued", {"job_id": jid, "mode": source["mode"], "tier": source["tier"], "operation": "rescore",
                                                                   "flag_resolution_id": resolution["id"], "responses": len(ready)})
            jobs.append(jid)
        return jobs

    def run_flag_resolution(self, resolution):
        context, profile = json.loads(resolution["input_json"]), json.loads(resolution["profile_json"])
        endpoint, settings = profile["endpoint"], profile["settings"]
        result, error, status, after = None, "", "complete", ""
        try:
            with connect(self.path) as db:
                questions = []
                for q in context["questions"]:
                    version = db.execute("SELECT exam_version FROM frq_questions WHERE id=?", (q["question_id"],)).fetchone()
                    questions.append([{**r, "question_id": q["question_id"], "exam_version": version[0] if version else ""} for r in q["responses"]])
            with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(questions)))) as pool:
                outcomes = list(pool.map(lambda snapshot: self._resolve_question(resolution["id"], endpoint, settings, snapshot, context["rule"]), questions))
            waits = [seconds for _, seconds in outcomes if seconds is not None]
            if waits:
                status, after = "pending", later(min(waits))
            else:
                entries = [entry for entry, _ in outcomes]
                counts = {}
                for entry in entries:
                    for r in entry["responses"]:
                        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
                failed = [e["label"] for e in entries if e["error"]]
                totals = {"responses": sum(len(e["responses"]) for e in entries), "adopted": counts.get("adopted", 0), "revised": counts.get("revised", 0),
                          "unreliable": counts.get("unreliable", 0), "unsettled": counts.get("unsettled", 0), "failed": counts.get("failed", 0),
                          "clarifications": sum(len(e["clarifications"]) for e in entries), "questions_failed": len(failed)}
                result = {"rule": RULE, "totals": totals, "questions": entries}
                if failed and len(failed) == len(entries):
                    status, error, result = "failed", entries[0]["error"], None
        except RetryLater as waiting:
            status, after = "pending", later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError) as failure:
            status, error = "failed", f"The flag review could not be completed: {failure}"
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            still = db.execute("SELECT status FROM frq_flag_resolutions WHERE id=?", (resolution["id"],)).fetchone()
            if not still or still[0] != "running":
                db.commit()
                return  # Stopped by the instructor meanwhile: nothing is applied.
            if result:
                result["jobs"] = self._carry(db, resolution, result["questions"], endpoint)
            db.execute("UPDATE frq_flag_resolutions SET status=?,result_json=?,error=?,not_before=?,updated_at=? WHERE id=? AND status='running'",
                       (status, encoded(result) if result else None, error, after, timestamp(), resolution["id"]))
            if status == "complete":
                self.event(db, resolution["assignment_id"], "flag_resolution_ready", {"resolution_id": resolution["id"], **result["totals"], "jobs": result["jobs"]}, "worker")
            db.commit()
        if result and result.get("jobs"):
            self.inbox.wake.set()

    def flag_resolution_grade(self, task, job, graded):
        """The carrier batch's grading input for one response (`carry`), or `graded` unchanged when no ruling applies to it."""
        with connect(self.path) as db:
            entry = self.flag_ruling(db, job, task["response_id"])
            prior = db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (task["reuse_result_id"],)).fetchone()
            existing = self._existing(db, dict(prior)) if prior else {}
            # The transcript the ruling read and changed: that of the result it ruled on (`_carry` carries only a result still current).
            base = db.execute("SELECT transcript_json FROM frq_results WHERE id=?", (entry["result_id"],)).fetchone() if entry and entry["ruling"].get("changes") else None
        if not entry:
            return graded
        labels = arbitration.candidates(existing)
        ruling = entry["ruling"]
        if ruling["decision"] == "adopt" and ruling["adopt"] not in labels:
            return graded
        settings = json.loads(job["profile_json"])
        endpoint = settings.get("flag_resolution_model", "")
        record = {"rule": settings["flag_resolution_rule"], "resolution_id": settings.get("flag_resolution_id"), "outcome": entry["outcome"], "decision": ruling["decision"],
                  "adopted": ruling.get("adopt", ""), "reason": ruling.get("reason", ""), "flags": entry["flags"], "standing_before": entry.get("standing_before", ""),
                  "endpoint": endpoint, "model": endpoint, "lineage": "",
                  **({"transcript_changes": ruling["changes"]} if ruling.get("changes") else {}), **({"page_round": True} if ruling.get("page_round") else {})}
        if ruling.get("scoring"):
            record["scoring"] = ruling["scoring"]
        return carry(graded, ruling, record, {label: existing[key] for label, key in labels.items()},
                     amend(json.loads(base[0]), ruling["changes"]) if base else None)

    def flag_review_note(self, db, aid, response_id, result_id, rules, max_points):
        """What the plan's latest flag review left with the instructor about this response's CURRENT result, for the review desk:
        {reason, readings: [{reading, lines, score}], at}, or None. A reading whose lines no longer score under today's rubric is left
        out; fewer than two leave only the reason. `look` ([{label, crop, bbox}], `flag_look`) is there only when the review outlined
        the marks to read, so a note without one reads as it always did."""
        row = db.execute("""SELECT result_json,updated_at,id FROM frq_flag_resolutions WHERE assignment_id=? AND status='complete' AND result_json IS NOT NULL
            ORDER BY rowid DESC LIMIT 1""", (aid,)).fetchone()
        if not row or response_id not in row[0]:
            return None
        found = next((r for q in json.loads(row[0])["questions"] for r in q["responses"] if r["response_id"] == response_id), None)
        if not found or found["outcome"] != "unreliable" or found.get("result_id") != result_id:
            return None
        readings = []
        for reading in (found.get("ruling") or {}).get("readings") or []:
            try:
                points = score(rules, [{"rubric_item_id": i, "applied": True} for i in reading["lines"]], max_points)
            except ValueError:
                continue
            readings.append({**reading, "score": points["score"], "max_score": points["max_score"]})
        look = (found.get("ruling") or {}).get("look")
        if not look and flag_look.wanted(found):
            # A finished review's result is immutable (migration 033), so an outline added after it is an event naming the review
            # (GRADING "THE SPOT TO LOOK AT": the notes still waiting when the look was built). The review's own look comes first.
            for (detail,) in db.execute("""SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind=?
                    AND json_extract(detail_json,'$.resolution_id')=? ORDER BY id DESC""", (aid, flag_look.ADDED, row[2])):
                look = flag_look.spots_of({"spots": (json.loads(detail).get("looks") or {}).get(response_id)}, flag_look.CROPS)
                if look:
                    break
        return {"reason": str((found.get("ruling") or {}).get("reason") or ""), "readings": readings if len(readings) >= 2 else [], "at": row[1],
                **({"look": look} if look else {})}

    def flag_ruling(self, db, job, response_id):
        """The ruling a carrier batch applies to one response, or None (`carry`, from the scoring stage)."""
        settings = json.loads(job["profile_json"])
        row = db.execute("SELECT result_json FROM frq_flag_resolutions WHERE id=?", (settings.get("flag_resolution_id"),)).fetchone()
        if not row or not row[0]:
            return None
        for q in json.loads(row[0])["questions"]:
            for r in q["responses"]:
                if r["response_id"] == response_id and r["outcome"] in ("adopted", "revised"):
                    return r
        return None
