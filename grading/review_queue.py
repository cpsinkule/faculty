"""The Review step's navigation: each question's waiting responses, gathered by the rubric lines the AI proposed.

A view of retained records only. Nothing here grades, approves, reveals or stores anything, and no pile
names a student. Exam versions are separate questions with their own rubrics, so a pile never mixes them.
A proposal hidden by a blind acceptance is never sorted by its lines: the pile it landed in
would show them before the instructor's own decision.

Rubric doubts are gathered per QUESTION (requirement, 2026-09-21): unclear rubric wording is one thing for the instructor
to settle in the rubric finalization, not one issue per student. Gathering changes nothing about what flags a response.

Responses whose LATEST attempt failed are listed too, per batch (requirement, 2026-09-23; GRADING "Failed to grade"): such a
response has no proposal, so nothing above can show it. Whichever batch made that attempt lists it, so a newer batch (an automatic
one for late papers, a one-response regrade, a flag review's carrier) never hides an older batch's failure (2026-10-02; an agent's
2026-09-23 narrowing to the newest batch left them on no page). A response a cancelled or reset batch left without a proposal is
new work, not a failure (`reset_ungraded`), as the workflow and Home count it. A retry is the batch's own action, `retry_job`;
reasons and questions are listed, never students.
"""
import json
import re

from rosters import connect
from werkzeug.exceptions import NotFound

from . import answers, arbitration, recognition, risk, standard
from .models import NOTHING_TRANSCRIBED, digest, reading_ambiguities
from .review import result_facts
from .review_rules import CONDITION_PREFIX, LEGACY_CONDITION
from .rubric_quality import REFUSED_LINE, REFUSED_SUM, builtin_exclusions
from .scoring import score

FINAL = ("approved", "manual", "auto_accepted")
REASON = 400  # Characters of a failure's recorded reason shown in the list.
FLAGGED = ("grading_review", "ocr_review", "both")
ORDER = {"proposal": 0, "hidden": 1}
DOUBTS = 12  # Shown per question; the most frequent first.

# ONE LINE PER CAUSE ON THE REVIEW CARD (requirement, 2026-10-11). A held response's card listed every recorded reason sentence, and one
# cause usually stood there two or three times in different words: the graders' disagreement as `risk.DISAGREE`, as the sentence naming
# the lines and as the team's own reason; a grader's reading issue as its block, its note and the team's `role: …` copy; a blank answer
# area as the reader's reason and as "No visible work was transcribed…". The card now leads with one line a cause and keeps every
# sentence verbatim, with its author, under All recorded reasons (`reason_causes`). Display only: what is held, flagged and said is
# unchanged. The causes and the label the card shows for each; the card keeps the order the sentences already have.
CAUSES = {"finalization": "Rubric finalization", "blank": "Blank answer area", "disagreement": "Graders disagree", "reading": "Reading check",
          "rubric": "Rubric concern", "checks": "Grading check", "grader": "Grader’s note", "other": "Other"}
# Which cause a sentence Faculty says itself belongs to, by its NAME in the module that says it, never by matching its words: a
# sentence reworded there is still found, and a new one nobody placed here is `other` (and fails `test_grading_review_queue`). A required
# assessment that is missing, failed or not independent is a check that did not pass, not a disagreement.
_SAID = {
    "reading": (risk.CHOICE_UNSURE, risk.READERS_DIFFER, risk.READER_UNSURE, risk.NO_OCR, risk.OCR_APART, risk.HANDWRITING, risk.CROP_UNSURE, risk.SECOND_READING,
                risk.IMAGE_DIFFERS, risk.ONE_READING, recognition.OCR_DISAGREE, recognition.VISUAL, recognition.OCR_UNSURE, recognition.BOUNDARIES, answers.ONE_READING),
    "rubric": (risk.WORDING, risk.CONDITION, LEGACY_CONDITION),
    "disagreement": (risk.DISAGREE, risk.OBJECTION, risk.OBJECTION_WHY),
    "checks": (risk.UNSCORABLE, risk.EMPTY_SELECTION, risk.EMPTY_FULL, risk.KEY_DIFFERS, risk.KEY_UNREAD, risk.NO_KEY, risk.WORK_BLOCK, risk.INCONSISTENT, risk.INCONSISTENT_WHY,
               risk.SYMBOLIC_FAILS, risk.ANSWER_INCONSISTENT, risk.CONTRADICTS, risk.MALFORMED, risk.MATH_ERROR, risk.NO_SECOND, risk.NO_PRIMARY, risk.INCOMPLETE,
               risk.NOBODY_ELSE, risk.SECOND_FAILED, risk.ONE_LINEAGE, answers.KEY_MISMATCH, answers.LITTLE_WORK, answers.NOT_FULL, answers.NO_WORK_LINE,
               answers.ADDS_UP, recognition.FOLLOW_THROUGH, REFUSED_LINE, REFUSED_SUM),
    "grader": (risk.ATTEMPTS, risk.GRADER_UNSURE, risk.GRADER_ASKED, risk.SECOND_ASKED, recognition.UNDECIDED, recognition.ERROR_LINE, recognition.APPLICATION,
               answers.STRUCK_LINE, answers.STRUCK_WORK)}
# The same for a sentence that goes on to name lines, a grader's words or the flags left standing: by the words it begins with.
_BEGINS = {"reading": (risk.SECOND_READING_PREFIX, risk.ONE_SHOT_PREFIX), "rubric": (CONDITION_PREFIX,),
           "disagreement": (arbitration.DISAGREE_ON, arbitration.TEAM_DIFFERS), "checks": (risk.SYMBOLIC_PREFIX,),
           "grader": (arbitration.UNSETTLED, standard.UNSETTLED)}
# How the review desk reads a reason's author and tells a copy that only adds a role key (`ROLE_KEY`, grading-review-layout.js;
# `PREFIXES`, grading-model-comments.js). Kept word for word with them: `listed_reasons` must list what the desk lists, in its
# order, and the desk draws the causes only while it does (test_grading_review_layout.mjs and test_grading_review_queue read one sample).
_ROLE_KEY = re.compile(r"^(?:primary|second|secondary|verifier|reviewer|arbiter|flag_reviewer):\s*", re.I)
_AUTHORS = tuple((role, re.compile(pattern + r":(?=\s|$)", re.I)) for role, pattern in (
    ("primary", r"^(?:primary|Primary grader|One-shot grader(?: \([^:\n]+\))?)"),
    ("second", r"^(?:second|secondary|Second grader(?: \([^:\n]+\))?|Secondary grader)"),
    ("verifier", r"^(?:verifier|reviewer|Additional reviewer|Adversarial reviewer)"),
    ("arbiter", r"^(?:arbiter|The final arbitrator[^:\n]+|Tag-Team reviewer)"),
    ("flag_reviewer", r"^(?:flag_reviewer|The flag reviewer [^:\n]+)"),
    ("reader", r"^(?:Reader|Uncertain reading of [^:\n]+|Uncertain layout from [^:\n]+)"),
    ("jev", r"^Jev could not break the tie")))
# A ruling whose own grade was scored again and left a flag standing wrote its sentence over the team's reason (`arbitration.apply`,
# `flag_resolution.apply`); it records the flags it left (`open_flags`) under one of these.
_RULINGS = ("arbiter", "flag_review", "flag_resolution")


def reason_author(text):
    """Who wrote a review reason, as the desk reads it from the recorded prefix ("primary:", "Uncertain reading of …:"), or ""."""
    return next((role for role, pattern in _AUTHORS if pattern.match(text)), "")


def listed_reasons(result):
    """The sentences the review desk lists for why a response waits, in the desk's order (`reviewReasons`, grading-review-layout.js):
    the finalization's reasons, what would not be accepted unseen and the notes, each text once (a copy that only adds a role key is
    the same reason, and the copy naming its role is kept); Faculty's own first, then the graders' by role, the reader's last."""
    signals = result.get("signals") or {}
    clean = lambda values: [text for text in (str(value).strip() for value in values or [] if value is not None) if text]
    sources = (("finalization", (signals.get("rubric_quality") or {}).get("reasons")), ("block", (signals.get("risk") or {}).get("blocks")), ("note", result.get("reasons")))
    kept = {}
    for order, (source, text) in enumerate((source, text) for source, values in sources for text in clean(values)):
        seen = kept.setdefault(_ROLE_KEY.sub("", text, count=1), {"sources": set(), "text": text, "order": order})
        seen["sources"].add(source)
        if _ROLE_KEY.match(text) and not _ROLE_KEY.match(seen["text"]):
            seen["text"] = text
    def tier(entry):
        role = reason_author(entry["text"])
        return 0 if "finalization" in entry["sources"] else 4 if role == "reader" else 3 if role else 1 if "block" in entry["sources"] else 2
    return [entry["text"] for entry in sorted(kept.values(), key=lambda entry: (tier(entry), entry["order"]))]


def reason_causes(result, rubric=None, maximum=None):
    """`listed_reasons` gathered by cause: [{key, label, reasons: [{text, role}]}], every listed sentence in exactly one cause, verbatim
    and in the desk's order, the causes in the order of their first sentence. `role` is the author the desk shows ("" for Faculty's own).

    A cause is read from what the result retains and from the sentences' names (`_SAID`, `_BEGINS`): the finalization's own reasons;
    the transcript's reading notes; each retained assessment's rubric doubts and reading issues, whichever role key a rule put in
    front of them; the scorer's own refusal of these ticks; the team's reason, a disagreement only where two assessments were compared
    and differ. What a grader, the arbitrator or a flag reviewer wrote in its own words (under its role, or among a retained
    assessment's own review reasons) is `grader`. Anything else is `other`: a sentence is never dropped, reworded or guessed at.
    For a proposal that is not hidden only (the caller's rule): what a grader doubted says which lines it weighed."""
    signals = result.get("signals") or {}
    words = lambda values: {str(value).strip() for value in values or [] if isinstance(value, str)} - {""}
    checks = (signals.get("risk") or {}).get("components") or {}
    opinions = [o for o in (signals, signals.get("second"), signals.get("standard_primary"), checks.get("second"), checks.get("one_shot"),
                            *(o for panel in signals.get("panels") or [] for o in (panel.get("opinions") or {}).values())) if isinstance(o, dict)]
    finalization = words((signals.get("rubric_quality") or {}).get("reasons"))
    doubts = {text for opinion in opinions for text in words(opinion.get("rubric_ambiguities"))}
    notes = words(reading_ambiguities(result.get("transcript") or {})) | {text for opinion in opinions for text in words(
        issue.get("description") for issue in opinion.get("reading_issues") or [] if isinstance(issue, dict))}
    asked = {text for opinion in opinions for text in words(opinion.get("review_reasons"))}
    team = signals.get("committee") or {}
    unscorable = None
    if result.get("score") is None and rubric:
        try:
            score(rubric, result.get("applications") or [], maximum)
        except ValueError as error:
            unscorable = str(error)  # The words `recognition.validate_grade` kept as the reason these ticks have no score.
        except (ArithmeticError, KeyError, TypeError):
            pass

    def cause(text, role):
        # The sentence without the grader's name a rule put in front of it: one flag, whoever is named (`arbitration._same`).
        inner = arbitration._same(_ROLE_KEY.sub("", text, count=1))
        if text in finalization:
            return "finalization"
        if risk.blank(inner) or inner == NOTHING_TRANSCRIBED:
            return "blank"
        if inner in notes:
            return "reading"
        if inner in doubts:
            return "rubric"
        for key, said in _SAID.items():
            if inner in said or text.startswith(_BEGINS.get(key, ())) or inner.startswith(_BEGINS.get(key, ())):
                return key
        if inner == unscorable:
            return "checks"
        if team.get("status") == "unresolved" and inner == str(team.get("reason") or "").strip():
            if any("open_flags" in (signals.get(name) or {}) for name in _RULINGS):
                return "grader"
            return "disagreement" if risk.selection_disagreement({"committee": team}) else "checks"
        if recognition.citation_audit(inner):
            return "grader"  # Faculty's check of a grader's quotation: about that grader's evidence.
        return "reading" if role == "reader" else "grader" if role or inner in asked else "other"

    found = {}
    for text in listed_reasons(result):
        role = reason_author(text)
        found.setdefault(cause(text, role), []).append({"text": text, "role": role})
    return [{"key": key, "label": CAUSES[key], "reasons": reasons} for key, reasons in found.items()]


def doubt_lines(text, lines):
    """Which rubric lines a grader's doubt is about: the lines it names. Only for gathering what is shown together.

    Wording varies from response to response (of 160 doubts measured on 2026-09-20 none was repeated three times), but a doubt
    about "R2_1 versus R2_4" names them whatever else it says. A doubt that names no line is about the rubric as a whole."""
    named = {m for m in re.findall(r"(?<!\w)(?:" + "|".join(sorted(map(re.escape, lines), key=len, reverse=True)) + r")(?!\w)", text)} if lines else set()
    number = lambda line: [int(n) for n in re.findall(r"\d+", line)]
    return sorted(named, key=lambda line: (number(line), line))


def score_summary(scores, max_points):
    """A question's current final grades in four numbers, for sanity-checking the grading (grun:P17): how many, their mean, and the
    share at full credit and at zero. Read only; each grade's own maximum (its scorer's `max_score`) decides full credit. None
    without a final grade."""
    if not scores:
        return None
    ceiling = lambda top: float(top) if top not in (None, "") else float(max_points) if max_points not in (None, "") else None
    full = sum(1 for score, top in scores if ceiling(top) is not None and score >= ceiling(top) > 0)
    zero = sum(1 for score, _ in scores if score == 0)
    return {"count": len(scores), "mean": round(sum(score for score, _ in scores) / len(scores), 2),
            "max": ceiling(scores[0][1]), "full": round(full / len(scores), 4), "zero": round(zero / len(scores), 4)}


class ReviewQueueMixin:
    def review_queue(self, aid, job_id=None, question_id=None, *, review_details=False):
        """Waiting means what the workflow counts as responses to review: a proposal or decision that is not final.

        `question_id` limits the queue to that question (the review desk refreshing one group after each approval): its entry is
        exactly the one the whole queue has, but the other questions are left out and the totals (`waiting`, `other_waiting`,
        `failed`) count that question's responses only. Anything that names the next question needs the whole queue. A question
        this plan does not pin is not found, like an unknown batch, never an empty queue that would read as a finished group."""
        with self.rosters.lock():
            rows = self.responses(aid, question_id)
            with connect(self.path) as db:
                if question_id is not None and not db.execute("SELECT 1 FROM frq_assignment_questions WHERE assignment_id=? AND question_id=?",
                                                              (aid, question_id)).fetchone():
                    raise NotFound("Question not found in this grading assignment.")
                selected_batch = None
                other_waiting = 0
                if job_id:
                    selected_batch = db.execute("SELECT id,created_at FROM frq_jobs WHERE id=? AND assignment_id=?", (job_id, aid)).fetchone()
                    if not selected_batch:
                        raise NotFound("Grading batch not found in this assessment.")
                    selected_batch = dict(selected_batch)
                    # A task in this batch does not make an earlier proposal its result. In particular, a regrade
                    # still running must not carry its old flags into this batch's review queue.
                    current = {r[0] for r in db.execute("""SELECT r.id FROM frq_responses r
                        JOIN frq_results x ON x.id=r.current_result_id JOIN frq_tasks t ON t.id=x.task_id
                        WHERE r.assignment_id=? AND t.job_id=?""", (aid, job_id))}
                    pending = {r[0] for r in db.execute("""SELECT t.response_id FROM frq_tasks t
                        WHERE t.job_id=? AND t.rowid=(SELECT MAX(n.rowid) FROM frq_tasks n WHERE n.response_id=t.response_id)""", (job_id,))}
                    other_waiting = sum(r["id"] not in current and r["state"] not in (*FINAL, "pending", "stale") for r in rows)
                    rows = [r for r in rows if r["id"] in current or (r["state"] == "pending" and r["id"] in pending)]
                # The doubts of the assessment that became the proposal and of the one that supports it, without reading whole receipts.
                # The instructor's decided scores, for the per-question summary of final grades (read only).
                decided = {row[0]: row[1] for row in db.execute("""SELECT r.id,v.score_json FROM frq_responses r JOIN frq_reviews v ON v.id=r.current_review_id
                    WHERE r.assignment_id=?""", (aid,))}
                # Each response's current result by id (the join keeps only pointers to a result that exists); its lines, score and
                # doubts are the retained result's own facts (`result_facts`).
                current = {row[0]: row[1] for row in db.execute("""SELECT r.id,x.id FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id
                    WHERE r.assignment_id=?""", (aid,))}
                facts = result_facts(db, str(self.path), current.values())
                proposals = {rid: facts[xid] for rid, xid in current.items() if xid in facts}
                # Each response's LATEST attempt, when it failed, in whichever batch made it: a newer attempt supersedes an older
                # failure, and a newer batch of other responses hides nothing. A batch's own queue (`job_id`) keeps its batch only.
                # CROSS JOIN keeps the plan's own batches first (frq_tasks_response would otherwise be walked whole).
                latest = {row["response_id"]: dict(row) for row in db.execute("""SELECT t.response_id,t.job_id,t.stage,t.error,t.attempt,t.rubric_id,t.question_revision_id,
                    j.created_at job_created,j.cancel_requested,j.mode,j.tier,j.profile_json FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id
                    JOIN (SELECT t2.response_id,MAX(t2.rowid) last FROM frq_jobs j2 CROSS JOIN frq_tasks t2 ON t2.job_id=j2.id
                          WHERE j2.assignment_id=? GROUP BY t2.response_id) n ON t.rowid=n.last WHERE t.status='failed'
                    AND (? IS NULL OR t.job_id=?)""", (aid, job_id, job_id))}
                # New work for the next batch, not a failure: what a reset or a cancelled batch left without a proposal (the workflow's
                # and Home's `failed` leave these out too). Read only when something failed.
                again = self.reset_ungraded(db, aid) if latest else set()
                batches = {}
                questions = {}
                for row in rows:
                    entry = questions.get(row["question_id"])
                    if not entry:
                        asked = self.question(db, aid, row["question_id"])
                        rubric = asked["rubric"]
                        entry = questions[row["question_id"]] = {"question_id": row["question_id"], "responses": 0, "approved": 0, "automatic": 0, "not_graded": 0, "waiting": 0, "failed": 0,
                            "regrade": 0, "flagged": 0, "groups": {}, "doubts": {}, "doubted": 0, "pinned": (asked["rubric_id"], asked["question_revision_id"]),
                            "final_scores": [], "max_points": asked["content"].get("max_points"),
                            # The lines that exclude every other line by rule: a doubt about stacking one of them is already settled.
                            "ruled": {r["item_id"]: r["rule"] for r in builtin_exclusions(rubric["rules"], asked["content"]["max_points"])} if rubric else {},
                            "lines": {item["id"]: item for item in rubric["rules"]["items"]} if rubric else {}}
                    entry["responses"] += 1
                    failed = latest.get(row["id"]) if row["state"] == "pending" else None
                    if row["state"] == "stale" or (failed and (failed["rubric_id"], failed["question_revision_id"]) != entry["pinned"]):
                        # Graded, or tried, under an earlier rubric, question or paper (2026-09-25): nothing to approve or retry.
                        # The next batch grades it again as new work (`outdated`, `reset_ungraded`).
                        entry["regrade"] += 1
                        continue
                    if failed and row["id"] in again:
                        failed = None  # Its batch was cancelled or reset: new work, counted as not graded yet.
                    if failed:
                        entry["failed"] += 1
                        batch = batches.get(failed["job_id"])
                        if not batch:
                            profile = json.loads(failed["profile_json"])
                            batch = batches[failed["job_id"]] = {"job_id": failed["job_id"], "created_at": failed["job_created"], "count": 0,
                                "compute": self.compute({**failed, "profile": profile}), "cancelled": bool(failed["cancel_requested"]),
                                "at_limit": 0, "reasons": {}, "questions": {}}
                        batch["count"] += 1
                        batch["at_limit"] += failed["attempt"] >= json.loads(failed["profile_json"]).get("max_retries", 3)
                        reason = (failed["error"] or "No reason was recorded.").strip()[:REASON]
                        batch["reasons"][reason] = batch["reasons"].get(reason, 0) + 1
                        batch["questions"][row["question_id"]] = batch["questions"].get(row["question_id"], 0) + 1
                        continue
                    if row["state"] in FINAL or row["state"] == "pending":
                        entry["approved" if row["state"] in FINAL else "not_graded"] += 1
                        # Finalized automatically: the grades a spot-check samples (a count; the page asks for the responses itself).
                        entry["automatic"] += row["state"] == "auto_accepted"
                        if row["state"] in FINAL:
                            written = decided.get(row["id"]) if row["state"] != "auto_accepted" else (proposals.get(row["id"]) or {"score_json": None})["score_json"]
                            scored = json.loads(written) if written else None
                            if isinstance(scored, dict) and scored.get("score") is not None:
                                entry["final_scores"].append((float(scored["score"]), scored.get("max_score")))
                        continue
                    proposal = proposals.get(row["id"])
                    applied = []
                    if not proposal:
                        entry["not_graded"] += 1  # Nothing proposed, so nothing to review.
                        continue
                    if not row["review_status"] and row["blind"]:
                        kind = "hidden"
                    else:
                        kind, applied = "proposal", sorted(proposal["applied"])
                    key = f"lines-{digest(applied)[:12]}" if kind == "proposal" else kind
                    group = entry["groups"].setdefault(key, {"id": key, "kind": kind, "applied": applied, "scores": set(), "size": 0, "flagged": 0, "states": {}, "members": [],
                        "items": [{"id": i, "points": entry["lines"][i]["points"], "description": entry["lines"][i]["description"]} for i in applied if i in entry["lines"]]})
                    if kind == "proposal" and proposal["score_json"]:
                        group["scores"].add((json.loads(proposal["score_json"]) or {}).get("score"))
                    flagged = row["state"] in FLAGGED
                    group["size"] += 1
                    group["flagged"] += flagged
                    group["states"][row["state"]] = group["states"].get(row["state"], 0) + 1
                    member = {"id": row["id"], "state": row["state"], "question_key": row["question_key"]}
                    if review_details:
                        # GRADING/PERFORMANCE: opt-in navigation facts of this term-owned response. Hidden work reveals
                        # no proposal-derived reason; the default API body and the standing review policy are unchanged.
                        reasons = []
                        if kind == "hidden":
                            reasons.append("blind")
                        else:
                            if any(json.loads(proposal[field] or "[]") for field in ("doubts", "second_doubts")):
                                reasons.append("rubric")
                            if row["state"] in ("ocr_review", "both"):
                                reasons.append("reading")
                                if proposal.get("blank"):  # The reader found no handwriting: the gallery lists these together (2026-10-11).
                                    reasons.append("blank")
                            if row["state"] in ("grading_review", "both"):
                                reasons.append("grading")
                        member["reason_kinds"] = reasons or ["decision"]
                    group["members"].append(member)
                    entry["waiting"] += 1
                    entry["flagged"] += flagged
                    if kind == "proposal":  # Never for a hidden proposal: what a grader doubted says which lines it weighed.
                        written = [text for field in ("doubts", "second_doubts") for text in json.loads(proposal[field] or "[]") if isinstance(text, str)]
                        entry["doubted"] += bool(written)
                        keyed = {}
                        for text in written:
                            named = doubt_lines(text, entry["lines"])
                            # "Does the full-credit line stack with R5_2, R5_7, ...?" names another set of lines on every
                            # response (measured on the real exam: five doubts, five sets). It is one doubt, about that line.
                            whole = next((line for line in named if line in entry["ruled"]), None) if len(named) > 1 else None
                            keyed.setdefault((whole,) if whole else tuple(named), []).append(text)
                        for key, texts in keyed.items():
                            doubt = entry["doubts"].setdefault(key, {"items": list(key), "responses": 0, "examples": [],
                                "settled": entry["ruled"].get(key[0]) if len(key) == 1 and any(len(doubt_lines(t, entry["lines"])) > 1 for t in texts) else None})
                            doubt["responses"] += 1
                            doubt["examples"].extend(text[:600] for text in texts if len(doubt["examples"]) < 2 and text[:600] not in doubt["examples"])
        for entry in questions.values():
            del entry["lines"], entry["ruled"], entry["pinned"]
            entry["scores"] = score_summary(entry.pop("final_scores"), entry.pop("max_points"))
            # A doubt about the rubric's wording is one question about the problem, however many responses raised it.
            entry["rubric_doubts"] = sorted(entry.pop("doubts").values(), key=lambda d: (-d["responses"], d["items"]))[:DOUBTS]
            # Largest first: one sitting removes the most work. Hidden and changed work follows the piles that show their lines.
            entry["groups"] = sorted(entry["groups"].values(), key=lambda g: (ORDER[g["kind"]], -g["size"], g["id"]))
            for group in entry["groups"]:
                group["scores"] = sorted(s for s in group["scores"] if s is not None)
        failed = sorted(({**batch, "reasons": [{"text": text, "count": n} for text, n in sorted(batch["reasons"].items(), key=lambda r: (-r[1], r[0]))],
                          "questions": [{"question_id": qid, "count": n} for qid, n in batch["questions"].items()]} for batch in batches.values()),
                        key=lambda batch: batch["created_at"], reverse=True)
        # A scoped answer names its question: the review desk reads the next step from an answer only when it is the whole queue.
        return {"questions": list(questions.values()), "waiting": sum(q["waiting"] for q in questions.values()),
                "batch": selected_batch, "other_waiting": other_waiting,
                "failed": {"total": sum(batch["count"] for batch in failed), "batches": failed},
                **({"question_id": question_id} if question_id is not None else {})}
