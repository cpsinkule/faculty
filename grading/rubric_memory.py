"""Rubric memories (GRADING "RUBRIC MEMORIES"; requirement, 2026-10-14).

Asked why a drafted rubric still needs review, the instructor proposed "a sort of memory system for the rubric drafter that helps it
avoid issues in the past", turned down memories found by question similarity ("the assignments naturally progress to different
material"), and chose "the flags of the grading models/rubric reviewer" made into "general memories that apply to a broad class of
problems". After a dry run on the four graded assignments and a re-draft of Quiz 4 with and without them (the draft followed 1 of 6 of
their rulings without, 5 of 6 with), they said "Build it".

ONE LEARNING PASS PER CERTIFIED PLAN (`queue`): after the instructor's own Certify (`after_certified`), or on their request for the
course's certified assignments (`learn`), one request to the Rubric drafting and review model reads that plan's evidence (`evidence`):
the questions its rubric checks raised and the answers the instructor typed to them (in Key & Rubric Finalization and at a quiz's
draft review), the doubts AI graders wrote about rubric lines, and the lines two graders ticked differently. No student is named:
roster names and nine-digit numbers are taken out of every sentence, and responses whose scan was deleted in the Inbox count nowhere.
It is an ordinary cloud call owned by its row (`frq_cloud_calls.rubric_memory_run_id`), claimed by the grading cloud dispatcher like a
regrade draft (`claim_runs`, `run`, `recover_runs`, `fail_run`); a course's runs go one at a time, oldest first, so each reads what
the one before it proposed.

A MEMORY IS KEPT AS IT IS WRITTEN (GRADING "KEPT AT ONCE", 2026-10-17). The model proposes new rules and watches and revisions of standing ones
(narrowed, widened, a watch answered, a retirement); each is a new immutable row, which the instructor may drop or keep on the course's
Rubric memories page. `standing(db, definition, at)` is what a rubric writer or the rubric check reads: every kept memory no kept revision replaces,
as it stood at `at` (a draft or check reads the list as it stood when it was asked for, so a retry sends what it sent).

The memories are guidance for drafting and checking only: they never change a rubric, a confirmation, a clarification already
confirmed, or a grade, and they are in no confirmation's fingerprint.
"""
from __future__ import annotations

import json
import re

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT

from . import config
from .models import digest, encoded, uid

RULE = "rubric-memory-v1"
STEP = "rubric_memory"
PROMPT = "rubric_memory"
TOKENS = 16384
MAX_NEW = 8
LIMITS = {"rule": 450, "covers": 300, "not_when": 450, "why": 400}
# What a payload carries at most, so one assignment's evidence stays one request.
CAPS = {"rulings": 60, "reviewer_questions": 40, "grader_doubts": 30, "grader_disagreements": 20, "doubts_per_line": 5}
NINE = re.compile(r"(?<!\d)\d{9}(?!\d)")
# A memory written in this assignment's own terms is no general memory: a line ID, a version letter, typeset math.
SPECIFIC = re.compile(r"\bR\d+(?:_\d+)?\b|\b[Vv]ersions? (?!I\b)[A-Z]\b|\$\$")
# What looks like a line ID and is none (2026-11-09 review: this course teaches row operations, and "swap rows R1 and R2" or
# "R2 - 2R1" was refused as naming a rubric line, as "the version I wrote" was for a version letter): rows named after the
# word, and rows joined by an operation or an arrow. These are read out of a sentence before it is looked at.
# A hyphen joins two rows only with a space on each side: "R2-R4" is a range of lines.
ROWS = re.compile(r"\b[Rr]ows?\s+R\d+(?:\s*(?:,|and|or|with|to)\s*R\d+)*"
                  r"|(?:(?<![A-Za-z_])(?:\d+\s*)?R\d+(?:\s*(?:<->|<-|->|[+*/×·=↔→←⇄⟷])\s*|\s+[-−–]\s+))+(?:\d+\s*)?R\d+\b")

MEMORY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rule", "covers", "not_when", "kind", "sources"], "properties": {
    "rule": {"type": "string"}, "covers": {"type": "string"}, "not_when": {"type": "string"}, "kind": {"type": "string", "enum": ["rule", "watch"]},
    "sources": {"type": "array", "items": {"type": "string"}}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["new", "revisions", "not_general"], "properties": {
    "new": {"type": "array", "items": MEMORY_SCHEMA},
    "revisions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["id", "action", "why", "rule", "covers", "not_when", "kind", "sources"],
                                             "properties": {"id": {"type": "string"}, "why": {"type": "string"},
                                                            "action": {"type": "string", "enum": ["confirm", "narrow", "widen", "answer", "retire"]},
                                                            **MEMORY_SCHEMA["properties"]}}},
    "not_general": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["source", "why"],
                                               "properties": {"source": {"type": "string"}, "why": {"type": "string"}}}}}}

cut = lambda value, n: (value if len(value) <= n else value[:n].rsplit(" ", 1)[0] + " […]") if isinstance(value, str) else ""
norm = lambda value: " ".join(str(value or "").split())


def label(number):
    return f"M{number}"


# -- What stands ---------------------------------------------------------------------------------------------------------------------
def rows(db, definition_id, at=None):
    """Every memory row of a course, each with its state as the instructor's decisions up to `at` leave it: 'kept', 'dropped' or
    'waiting' (no decision yet)."""
    found = []
    for row in db.execute("SELECT * FROM frq_rubric_memories WHERE definition_id=? AND (? IS NULL OR created_at<=?) ORDER BY number,created_at,rowid",
                          (definition_id, at, at)):
        latest = db.execute("""SELECT decision,created_at FROM frq_rubric_memory_decisions WHERE memory_id=? AND (? IS NULL OR created_at<=?)
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (row["id"], at, at)).fetchone()
        found.append({**dict(row), "sources": json.loads(row["sources_json"]), "state": {"keep": "kept", "drop": "dropped"}.get(latest[0] if latest else "", "waiting"),
                      "decided_at": latest[1] if latest else None})
    return found


def standing(db, definition_id, at=None):
    """The memories a rubric writer and the rubric check read: kept, no retirement, and not replaced by a kept revision, each once by
    number, as the decisions up to `at` leave them."""
    every = rows(db, definition_id, at)
    replaced = {row["replaces_id"] for row in every if row["state"] == "kept" and row["replaces_id"]}
    return [row for row in every if row["state"] == "kept" and row["action"] != "retire" and row["id"] not in replaced]


def given(memories):
    """A memory as a model reads it."""
    return [{"id": label(m["number"]), "kind": m["kind"], "rule": m["rule"], "covers": m["covers"], "not_when": m["not_when"]} for m in memories]


def standing_rules(db, definition_id, at=None):
    """`standing_rules` of a writer's or reviewer's payload ([] when the course has none, and the payload then names none: a request
    of a course without memories is exactly the request it always was)."""
    return given(standing(db, definition_id, at)) if definition_id else []


def evidence_digest(evidence):
    """What makes two readings of a plan's evidence the same for learning: its rulings and questions, and which lines graders doubted."""
    content = {k: evidence[k] for k in ("rulings", "reviewer_questions")} | {"lines": sorted(d["line"] for d in evidence["grader_doubts"])}
    if evidence.get("grader_disagreements"):
        content["disagreements"] = sorted((d["problem"], d["line"]) for d in evidence["grader_disagreements"])
    return digest(content)


def plan_rules(db, aid):
    """`standing_rules` for one plan's checks and drafts. A plan graded to the coordinator's rubric reads them too (2026-10-28): what
    its rubric or the instructor's word on it says otherwise is left out question by question at the check (`memory_fit`)."""
    return standing_rules(db, definition_of_plan(db, aid))


# -- Does a memory fit this question (GRADING "RUBRIC MEMORIES" FITTED TO EACH QUESTION, 2026-10-28) ------------------------------------
FIT_RULE = "rubric-memory-fit-v1"
FIT_PURPOSE = "rubric-memory-fit"
FIT_CUTOFF = .7
FIT_CHUNK = 8
FIT_PARALLEL = 8
SAME_PURPOSE = "rubric-memory-dropped"
SAME_RULE = "rubric-memory-dropped-v1"
SAME_CUTOFF = .7


def question_label(question):
    return (f"Version {question['exam_version']} · " if question.get("exam_version") else "") + question["title"]


def fit_state(context, question):
    """What Jev reads about one question: its rubric lines, the instructor's clarifications of them, and the instructor's word on
    this assignment that is about it (answers to its rubric questions, the quiz draft review's, their quick rubric choices)."""
    name = question_label(question)
    near = {name, f"Problem {question.get('printed_number')}", "At the quiz draft review"}
    lines = [{k: item[k] for k in ("id", "description", "points", "notes", "exceptions") if item.get(k) not in ("", None)}
             for item in question["rubric"]["items"]]
    answers = [{"asked": a["asked"], "answered": a["answered"]} for a in context.get("earlier_answers") or []
               if not a.get("about") or near & set(a["about"])]
    # A resolution's own answers, the ones it is about to send (`resolves`), are the instructor's word on this question as well.
    answers += [{"asked": c.get("decision") or c.get("title") or c.get("message") or "", "answered": c["response"]}
                for c in (context.get("resolves") or {}).get("concerns") or []
                if c.get("response") and question["question_id"] in (c.get("question_ids") or [c.get("question_id")])]
    return {"question": {"question": name, "max_points": question.get("max_points"), "rubric_lines": lines,
                         "clarifications": question.get("comments") or {}},
            "instructor_answers": answers, **({"assignment_choices": context["assignment_choices"]} if context.get("assignment_choices") else {})}


def fit_questions(count):
    questions = {}
    for i in range(count):
        questions[f"rubric_{i}"] = {"type": "noul", "instructions": (
            f"Does `memories[{i}]` (a standing grading rule for the whole course), applied to a case this question can actually have, "
            "contradict what `question.rubric_lines` or `question.clarifications` explicitly say? Answer yes only for a real conflict: "
            "following the memory would award or withhold credit differently from what the rubric or a clarification says for that same "
            "case. A memory about cases this question cannot have, or one the rubric is silent on, is no contradiction.")}
        questions[f"instructor_{i}"] = {"type": "noul", "instructions": (
            f"Does `memories[{i}]`, applied to a case this question can actually have, contradict an answer in `instructor_answers` "
            "(what the instructor ruled about this question) or `assignment_choices`? Answer yes only when following the memory would "
            "grade that same case differently from the instructor's word. No instructor answers, or answers about other cases, is no "
            "contradiction.")}
    return questions


def memory_fit(jev, owner, context):
    """{question_id: [memory IDs]}: the standing memories that contradict a question's rubric lines, its clarifications or the
    instructor's word about it, Jev at FIT_CUTOFF or above on either question, left out of that question's requests
    (`rubric_quality.rules_for`). Asked once per question for every FIT_CHUNK memories, owned by the check (`owner`), so a later
    turn of the same check reads the receipts it already has. A failed, refused or uncertain receipt leaves those memories in, as
    they were before 2026-10-28; without Jev nothing is left out."""
    rules = context.get("standing_rules") or []
    if not jev or not rules:
        return {}
    jobs = [(q, rules[start:start + FIT_CHUNK]) for q in context.get("questions") or [] for start in range(0, len(rules), FIT_CHUNK)]

    def ask(job):
        question, chunk = job
        state = {**fit_state(context, question), "memories": [{k: m[k] for k in ("kind", "rule", "covers", "not_when")} for m in chunk]}
        try:
            answer = jev.ask(FIT_PURPOSE, owner, state, fit_questions(len(chunk)), FIT_RULE)
        except Exception:  # a check never fails for its memories
            return question, []
        if answer.get("status") != "complete":
            return question, []
        found = answer.get("answers") or {}
        return question, [m["id"] for i, m in enumerate(chunk)
                          if max((found.get(f"{kind}_{i}") or {}).get("noul") or 0 for kind in ("rubric", "instructor")) >= FIT_CUTOFF]
    from concurrent.futures import ThreadPoolExecutor
    left = {}
    with ThreadPoolExecutor(max_workers=FIT_PARALLEL) as pool:
        for question, ids in pool.map(ask, jobs):
            if ids:
                left.setdefault(question["question_id"], []).extend(ids)
    return left


def not_dropped(jev, owner, created, dropped):
    """The written memories that do not say what a memory the instructor dropped says (2026-10-28: Exam 2's pass wrote again the
    Q23 ruling they dropped two days before, since a pass is shown only what stands). One Jev question per pair, SAME_CUTOFF or
    above refuses the new one; without Jev, or on a receipt that is not complete, it is written as before."""
    if not jev or not dropped or not created:
        return created, []
    kept, refused = [], []
    for n, memory in enumerate(created):
        state = {"new_memory": {k: memory[k] for k in ("kind", "rule", "covers", "not_when")},
                 "dropped": [{k: d[k] for k in ("kind", "rule", "covers", "not_when")} for d in dropped]}
        # Measured 2026-10-28 on the course's 70 memories against its two dropped ones: Exam 2's re-written Q23 answer .83, every
        # other memory .24 or below (a wording that said "a narrower or wider case is not the same" gave the re-written one .57).
        questions = {f"same_{i}": {"type": "noul", "instructions": (
            f"Is `new_memory` essentially the same grading rule as `dropped[{i}]` (it would grade the same responses the same way), "
            "even in other words or with small differences in its examples?")} for i in range(len(dropped))}
        try:
            answer = jev.ask(SAME_PURPOSE, owner, state, questions, SAME_RULE, attempt=str(n))
        except Exception:
            answer = {}
        found = answer.get("answers") or {} if answer.get("status") == "complete" else {}
        same = [label(dropped[i]["number"]) for i in range(len(dropped)) if ((found.get(f"same_{i}") or {}).get("noul") or 0) >= SAME_CUTOFF]
        (refused if same else kept).append({**memory, "same_as": same} if same else memory)
    return kept, refused


def definition_of_plan(db, aid):
    row = db.execute("""SELECT d.definition_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id JOIN assignments d ON d.id=e.assignment_id
        WHERE a.id=?""", (aid,)).fetchone()
    return row[0] if row else None


# -- Reading an answer ---------------------------------------------------------------------------------------------------------------
def checked_text(value, name, where, blank=True):
    if not isinstance(value, str):
        raise ValueError(f"{where}: `{name}` must be text.")
    value = norm(value)
    if not value and not blank:
        raise ValueError(f"{where}: `{name}` is empty.")
    if len(value) > LIMITS[name]:
        raise ValueError(f"{where}: `{name}` is {len(value)} characters; at most {LIMITS[name]} (about {LIMITS[name] // 7} words).")
    specific = None if name == "why" else SPECIFIC.search(ROWS.sub(" ", value))
    if specific:
        raise ValueError(f"{where}: `{name}` names this assignment's own line, version or typeset math ({specific.group(0)!r}); a memory is general.")
    return value


def read_memories(output, known, rulings, revisable, again=False):
    """The model's answer, or ValueError with what to fix (asked once more with the reason). `known`: every evidence ID of the
    payload; `rulings`: the IDs of the instructor's answers; `revisable`: {"M4": row} of the standing memories. `again`: the
    answer to that second asking. One memory of it that is still refused is left out and named under `refused`, and the rest
    is written: one sentence the model would not reword no longer loses the whole pass (2026-11-09 review). An answer that is
    no answer (not the three lists, too many new memories) still fails."""
    if not isinstance(output, dict) or not all(isinstance(output.get(k), list) for k in ("new", "revisions", "not_general")):
        raise ValueError("Return a JSON object with the lists new, revisions and not_general.")
    if len(output["new"]) > MAX_NEW:
        raise ValueError(f"At most {MAX_NEW} new memories for one assignment; {len(output['new'])} were written.")

    def sources(value, where):
        requested = value.get("sources") or []
        if not isinstance(requested, list) or any(not isinstance(source, str) for source in requested):
            raise ValueError(f"{where}: `sources` must be a list of evidence identifiers.")
        return list(dict.fromkeys(source for source in requested if source in known))

    def memory(value, where, kind=None):
        if not isinstance(value, dict):
            raise ValueError(f"{where} is not an object.")
        kind = kind or value.get("kind")
        if kind not in ("rule", "watch"):
            raise ValueError(f"{where}: `kind` is rule or watch.")
        selected = sources(value, where)
        if kind == "rule" and not any(s in rulings for s in selected):
            raise ValueError(f"{where} is a rule that names none of the instructor's answers (`rulings`) among its sources; a rule rests on one.")
        if not selected:
            raise ValueError(f"{where} names no evidence of this assignment among its sources.")
        return {"kind": kind, "rule": checked_text(value.get("rule"), "rule", where, False), "covers": checked_text(value.get("covers"), "covers", where, False),
                "not_when": checked_text(value.get("not_when", ""), "not_when", where), "sources": selected}
    new, refused = [], []
    for n, value in enumerate(output["new"], 1):
        try:
            new.append(memory(value, f"New memory {n}"))
        except ValueError as failure:
            if not again:
                raise
            refused.append(str(failure)[:300])
    revisions, confirmed, ignored, seen = [], [], [], set()
    for n, value in enumerate(output["revisions"], 1):
        if not isinstance(value, dict) or value.get("action") not in ("confirm", "narrow", "widen", "answer", "retire"):
            raise ValueError(f"Revision {n} needs an action: confirm, narrow, widen, answer or retire.")
        mid = value.get("id")
        if not isinstance(mid, str) or mid in seen:
            raise ValueError(f"Revision {n} must name a memory once by its identifier.")
        seen.add(mid)
        target = revisable.get(mid)
        if target is None:  # a waiting or unknown memory is never revised: left out, and said so in the result
            ignored.append(str(value.get("id"))[:20])
            continue
        why = checked_text(value.get("why", ""), "why", f"Revision {n}")
        if value["action"] == "confirm":
            confirmed.append({"id": value["id"], "why": why, "sources": sources(value, f"Revision {n}")})
            continue
        if value["action"] == "retire":
            selected = sources(value, f"Revision {n}")
            if not any(s in rulings for s in selected):
                raise ValueError(f"Revision {n} retires {value['id']} without one of the instructor's answers among its sources.")
            revisions.append({"replaces": target, "action": "retire", "why": why, "kind": target["kind"], "rule": target["rule"], "covers": target["covers"],
                              "not_when": target["not_when"], "sources": selected})
            continue
        kind = "rule" if value["action"] == "answer" else value.get("kind")
        try:
            revisions.append({"replaces": target, "action": value["action"], "why": why, **memory(value, f"Revision {n} ({value['id']})", kind)})
        except ValueError as failure:
            if not again:
                raise
            refused.append(str(failure)[:300])
    not_general = [{"source": row["source"], "why": norm(row.get("why"))[:200]} for row in output["not_general"]
                   if isinstance(row, dict) and isinstance(row.get("source"), str) and row["source"] in rulings]
    return {"new": new, "revisions": revisions, "confirmed": confirmed, "ignored": ignored, "not_general": not_general,
            **({"refused": refused} if refused else {})}


# -- The evidence ----------------------------------------------------------------------------------------------------------------------
def scrubbed(value, names):
    """A sentence with every student's name and any nine-digit number taken out (`assistant.memory.nameless`)."""
    from assistant.memory import nameless
    return nameless(NINE.sub("[number]", norm(value)), names) if names else NINE.sub("[number]", norm(value))


class RubricMemory:
    def __init__(self, frq):
        self.frq = frq
        self.rosters, self.path = frq.rosters, frq.path
        # The names ("Family, Given") of the current rosters and of the roster of the plan's own term, set where the app is built:
        # every sentence is read without them.
        self.names = lambda term_id=None: []
        # ASSISTANT-MEMORY "ONE PAGE": the instructor's switch for learning by itself (after Certify, after rubrics are approved),
        # set where the app is built. Learn from certified assignments is their own request and does not read it.
        self.learns = lambda: True

    # -- The dispatcher's side (grading/cloud.py) --------------------------------------------------------------------------------
    @staticmethod
    def claim_runs(db, now, limit):
        """At most `limit` waiting runs, the oldest of each course, and none of a course whose run is already under way: each run
        reads what the one before it proposed."""
        units = []
        if limit <= 0:
            return units
        busy = {row[0] for row in db.execute("SELECT DISTINCT definition_id FROM frq_rubric_memory_runs WHERE status='running'")}
        for row in db.execute("""SELECT * FROM frq_rubric_memory_runs r WHERE status='pending' AND COALESCE(not_before,'')<=?
                AND NOT EXISTS(SELECT 1 FROM frq_rubric_memory_runs o WHERE o.definition_id=r.definition_id AND o.status='pending' AND o.rowid<r.rowid)
                ORDER BY updated_at,rowid""", (now,)).fetchall():
            if len(units) >= limit:
                break
            if row["definition_id"] in busy:
                continue
            busy.add(row["definition_id"])
            db.execute("UPDATE frq_rubric_memory_runs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?", (now, row["id"]))
            units.append(("rubric_memory", dict(row), None))
        return units

    @staticmethod
    def recover_runs(db):
        db.execute("UPDATE frq_rubric_memory_runs SET status='pending', updated_at=? WHERE status='running'", (timestamp(),))

    @staticmethod
    def fail_run(db, run_id, error):
        db.execute("UPDATE frq_rubric_memory_runs SET status='failed', error=?, updated_at=? WHERE id=? AND status='running'", (str(error)[:1000], timestamp(), run_id))

    def run(self, task):
        """One pass: the plan's evidence read now (the roster's names taken out), the memories as they stand with those waiting for a
        decision, and one request. Evidence a finished pass of this plan already read asks nothing again."""
        from .cloud import RetryLater, later
        from .providers.base import ProviderError
        profile = json.loads(task["profile_json"])
        status, found, error, after, result = "complete", None, "", None, None
        try:
            with connect(self.path) as db:
                title, evidence = self.evidence(db, task["assignment_id"])
                every = rows(db, task["definition_id"])
                kept = standing(db, task["definition_id"])
                read = evidence_digest(evidence)
                same = next((row["id"] for row in db.execute("""SELECT id,result_json FROM frq_rubric_memory_runs WHERE assignment_id=? AND status='complete'
                        AND id!=? ORDER BY rowid DESC""", (task["assignment_id"], task["id"])) if (json.loads(row["result_json"] or "{}").get("evidence_sha256") == read)), None)
            if same or not any(evidence.get(kind) for kind in ("rulings", "reviewer_questions", "grader_doubts", "grader_disagreements")):
                result = {"rule": RULE, "title": title, "evidence_sha256": read, "created": [], **({"same_as": same} if same else {"nothing": True})}
            else:
                waiting = [row for row in every if row["state"] == "waiting" and row["action"] != "retire"]
                memories = [*given(kept), *[{**m, "waiting": True} for m in given(waiting)]]
                known = {row["id"] for key in ("rulings", "reviewer_questions", "grader_doubts", "grader_disagreements") for row in evidence[key]}
                rulings = {row["id"] for row in evidence["rulings"]}
                revisable = {label(row["number"]): row for row in kept}
                found = self._ask(task, profile["endpoint"], profile["settings"], {**evidence, "memories": memories},
                                  lambda output, again=False: read_memories(output, known, rulings, revisable, again))
                result = {"rule": RULE, "title": title, "evidence_sha256": read, "evidence": evidence, "memories_given": memories}
                # NEVER WRITTEN AGAIN (2026-10-28): what says what a dropped memory says is not written.
                dropped = [row for row in every if row["state"] == "dropped"]
                jev = getattr(self.frq, "jev", None)
                found["new"], again = not_dropped(jev, task["id"], found["new"], dropped)
                found["revisions"], again_revised = not_dropped(jev, task["id"] + ":revisions", found["revisions"], dropped)
                if again or again_revised:
                    result["dropped_again"] = [{k: m[k] for k in ("kind", "rule", "same_as")} | ({"revises": label(m["replaces"]["number"])} if m.get("replaces") else {})
                                               for m in again + again_revised]
        except RetryLater as waiting_for:
            status, after = "pending", later(waiting_for.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound, OSError) as failure:
            status, error = "failed", f"No memories could be written from this assignment: {failure}"[:1000]
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            created = []
            if found:
                now = timestamp()
                # What stood when the pass began was read a minute or more ago, before the model's answer. A memory the instructor
                # dropped meanwhile is not revised: its revision, kept at once, would put back what they had just taken out
                # (2026-11-09 review). It is left out and named under `stale`.
                still = {row["id"] for row in standing(db, task["definition_id"])}
                stale = [label(revision["replaces"]["number"]) for revision in found["revisions"] if revision["replaces"]["id"] not in still]
                found["revisions"] = [revision for revision in found["revisions"] if revision["replaces"]["id"] in still]
                number = (db.execute("SELECT MAX(number) FROM frq_rubric_memories WHERE definition_id=?", (task["definition_id"],)).fetchone()[0] or 0)
                for memory in found["new"]:
                    number += 1
                    created.append(self._insert(db, task, number, "new", None, memory, "", now))
                for revision in found["revisions"]:
                    created.append(self._insert(db, task, revision["replaces"]["number"], revision["action"], revision["replaces"]["id"], revision, revision["why"], now))
                # Kept at once (requirement, 2026-10-17); Drop takes one back.
                # From a plan graded to the coordinator's rubric too (2026-10-28): the check leaves out, question by question, what
                # contradicts a rubric or the instructor's word (`memory_fit`).
                for mid in created:
                    db.execute("INSERT INTO frq_rubric_memory_decisions(id,memory_id,decision,actor,created_at) VALUES(?,?,?,?,?)",
                               (uid(), mid, "keep", "automatic", now))
                result = {**result, "created": created, "confirmed": found["confirmed"], "ignored": found["ignored"], "not_general": found["not_general"],
                          **({"refused": found["refused"]} if found.get("refused") else {}), **({"stale": stale} if stale else {})}
            db.execute("UPDATE frq_rubric_memory_runs SET status=?, result_json=?, error=?, not_before=?, updated_at=? WHERE id=? AND status='running'",
                       (status, encoded(result) if result and status == "complete" else None, error, after, timestamp(), task["id"]))
            db.commit()

    @staticmethod
    def _insert(db, task, number, action, replaces, memory, why, now):
        mid = uid()
        db.execute("""INSERT INTO frq_rubric_memories(id,definition_id,number,kind,action,replaces_id,rule,covers,not_when,why,sources_json,run_id,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (mid, task["definition_id"], number, memory["kind"], action, replaces, memory["rule"], memory["covers"], memory["not_when"], why,
                    encoded({"assignment_id": task["assignment_id"], "ids": memory["sources"]}), task["id"], now))
        return mid

    def _ask(self, task, endpoint, settings, payload, read):
        refused = None
        for attempt in (0, 1):
            body = payload if not attempt else {**payload, "refused": f"Your previous answer was refused: {refused}"}
            reply = self.frq.cloud_call({"rubric_memory_run_id": task["id"], "step": STEP, "task_attempt": attempt}, "rubric_reviewer", endpoint,
                                        {"prompt": PROMPT, "payload": body, "images": [], "schema": SCHEMA, **({"reask": True} if attempt else {})}, settings)
            try:
                return read(reply["output"], bool(attempt))
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    # -- The evidence of one plan ----------------------------------------------------------------------------------------------
    def evidence(self, db, aid):
        """(label, evidence) of a TERM grading plan: what its rubric checks asked and the instructor answered, where its graders
        stopped and disagreed. Reads only; nothing in it names a student."""
        head = db.execute("SELECT e.title, e.term_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id WHERE a.id=?", (aid,)).fetchone()
        if not head:
            raise NotFound("No grading plan has that ID.")
        # The plan's own term too: a pass over an assignment of a term that is over would read that term's students in (2026-11-09 review).
        names = self.names(head["term_id"]) or []
        checks = [(r["id"], json.loads(r["input_json"]), json.loads(r["result_json"])) for r in db.execute(
            "SELECT id,input_json,result_json FROM frq_rubric_quality_checks WHERE assignment_id=? AND status='complete' AND cancelled=0 ORDER BY created_at,rowid", (aid,))]
        questions, families = {}, []
        for _, given_input, result in checks:
            for q in given_input.get("questions") or []:
                questions[q["question_id"]] = q
            if not given_input.get("resolves") and result.get("families"):
                families = result["families"]
        ordered = sorted([f for f in families if f and f[0] in questions], key=lambda f: (printed(questions[f[0]]), f[0]))
        family_of = {qid: f"P{n}" for n, family in enumerate(ordered, 1) for qid in family}
        by_title = {}
        for family in ordered:
            heads = {questions[qid].get("title") for qid in family}
            if len(heads) == 1:
                by_title.setdefault(heads.pop(), []).append(family_of[family[0]])
        for qid in sorted(questions, key=lambda qid: (printed(questions[qid]), qid)):
            if qid not in family_of:
                keys = by_title.get(questions[qid].get("title"), [])
                if len(keys) == 1:
                    family_of[qid] = keys[0]
                else:
                    ordered.append([qid])
                    family_of[qid] = f"P{len(ordered)}"
        lines_of = {qid: {i["id"]: i for i in (q.get("rubric") or {}).get("items") or []} for qid, q in questions.items()}

        def line(qid, iid, table=lines_of):
            item = table.get(qid, {}).get(iid)
            return f"{iid} ({item['points']:+g}): {cut(norm(item.get('description')), 260)}" if item else str(iid)

        def named_lines(concern):
            qids, ids = concern.get("question_ids") or [concern.get("question_id")], concern.get("item_ids") or []
            pairs = [(qid, iid) for qid, many in ids.items() for iid in many] if isinstance(ids, dict) else [(qids[0], iid) for iid in ids if isinstance(iid, str)]
            return [line(qid, iid) for qid, iid in pairs][:6]
        problems = []
        for family in ordered:
            q = questions[family[0]]
            problems.append({"problem": family_of[family[0]], "versions": sorted(questions[x].get("exam_version") or "" for x in family),
                             "title": q.get("title") or "", "points": q.get("max_points"), "scoring": (q.get("rubric") or {}).get("mode"),
                             "statement": cut(scrubbed((q.get("reference") or {}).get("problem") or "", names), 600),
                             "lines": [line(family[0], i["id"]) for i in (q.get("rubric") or {}).get("items") or []]})
        rulings, seen = [], set()

        def ruling(problem, lines, asked, answer):
            answer = scrubbed(answer, names)
            if answer and (problem, answer.casefold()) not in seen and len(rulings) < CAPS["rulings"]:
                seen.add((problem, answer.casefold()))
                rulings.append({"id": f"R{len(rulings) + 1}", "problem": problem, "lines": lines, "reviewer_asked": cut(scrubbed(asked, names), 900),
                                "instructor_answered": cut(answer, 1500)})
        for _, given_input, _ in checks:  # answered in Key & Rubric Finalization's Resolve concerns
            for concern in (given_input.get("resolves") or {}).get("concerns", []):
                qid = (concern.get("question_ids") or [None])[0]
                ruling(family_of.get(qid, ""), named_lines(concern), concern.get("decision") or concern.get("message"), concern.get("response"))
        for problem, lines, asked, answer in self._draft_answers(db, aid):  # answered at a quiz's draft review
            ruling(problem, lines, asked, answer)
        asked_rows, seen = [], set()
        for cid, given_input, result in checks:
            if given_input.get("resolves"):
                continue
            for concern in [{**e, "question_ids": [e["question_id"]]} for e in result.get("suspected_errors") or []] + list(result.get("consistency_issues") or []):
                qid = (concern.get("question_ids") or [None])[0]
                key = (family_of.get(qid), norm(concern.get("title") or concern.get("message"))[:60].casefold())
                if key in seen or len(asked_rows) >= CAPS["reviewer_questions"]:
                    continue
                seen.add(key)
                asked_rows.append({"id": f"A{len(asked_rows) + 1}", "problem": family_of.get(qid, ""), "lines": named_lines(concern), "title": norm(concern.get("title")),
                                   "asked": cut(scrubbed(concern.get("decision") or concern.get("message"), names), 700)})
        doubts, disagreements = self._graders(db, aid, family_of, names)
        evidence = {"assignment": head[0], "problems": problems, "rulings": rulings, "reviewer_questions": asked_rows,
                    "grader_doubts": doubts, "grader_disagreements": disagreements}
        return head[0], json.loads(encoded(evidence))

    def _draft_answers(self, db, aid):
        """(problem, lines, asked, answer) of every answer the instructor typed to a rubric decision at the draft review of the quiz this
        plan was approved from (QUIZZES "RUBRIC QUESTIONS AT DRAFT REVIEW"). Problems are keyed by their number, as the plan's are."""
        from .rubric_quality import concern_list
        found = []
        for approval in db.execute("SELECT draft_id,result_json FROM quiz_draft_approvals WHERE status='complete'"):
            if aid not in (approval["result_json"] or ""):
                continue
            for revision in db.execute("SELECT input_json,based_on FROM quiz_draft_revisions WHERE draft_id=? AND status='complete' ORDER BY number", (approval["draft_id"],)):
                asked = (json.loads(revision["input_json"]) or {}).get("rubric_answers")
                if not asked or not revision["based_on"]:
                    continue
                earlier = db.execute("SELECT result_json FROM quiz_draft_revisions WHERE id=?", (revision["based_on"],)).fetchone()
                shown = ((json.loads(earlier[0] or "{}") if earlier else {}).get("rubric_check") or {})
                if shown.get("status") != "complete":
                    continue
                concerns = concern_list(shown["result"])
                rubrics = {entry.get("problem"): entry.get("rubric") or {} for entry in (json.loads(earlier[0] or "{}").get("rubrics") or [])}
                for row in asked.get("responses") or []:
                    if not isinstance(row, dict) or not isinstance(row.get("concern"), int) or not 0 <= row["concern"] < len(concerns):
                        continue
                    concern = concerns[row["concern"]]
                    qid = (concern.get("question_ids") or [""])[0]
                    number = qid.split(":")[1] if qid.startswith("draft:") and qid.count(":") == 2 else ""
                    letter = qid.rsplit(":", 1)[-1] if number else ""
                    items = (rubrics.get(int(number)) if number.isdigit() else None) or {}
                    ids = concern.get("item_ids") or []
                    wanted = [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [i for many in ids.values() for i in many]
                    lines = []
                    for n, item in enumerate(items.get("items") or [], 1):
                        if f"R{n}" in wanted:
                            lines.append(f"R{n} ({item['points']:+g}): {cut(norm((item.get('descriptions') or {}).get(letter) or ''), 260)}")
                    found.append((f"P{number}" if number else "", lines[:6], concern.get("decision") or concern.get("message"), row.get("response")))
        return found

    def _graders(self, db, aid, family_of, names):
        """Where AI graders stopped: their rubric doubts by rubric line, and the lines the first two graders ticked differently, from the
        plan's real (not shadow) batches, counting only responses every page of which is still in the Inbox (PRESENT)."""
        present = PRESENT.format("json_extract(g.value,'$.message_id')")
        steps = {}
        for r in db.execute(f"""SELECT s.task_id,s.stage,s.output_json,q.question_id qid,t.rubric_id FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id
                JOIN frq_jobs j ON j.id=t.job_id JOIN frq_question_revisions q ON q.id=t.question_revision_id
                WHERE j.assignment_id=? AND j.shadow=0 AND s.stage IN ('grade:primary','grade:second')
                AND NOT EXISTS(SELECT 1 FROM json_each(t.source_json,'$.regions') g WHERE NOT ({present})) ORDER BY s.attempt,s.rowid""", (aid,)):
            steps[(r["task_id"], r["stage"])] = (r["qid"], r["rubric_id"], json.loads(r["output_json"]))
        rubrics = {}

        def line(rubric_id, iid):
            if rubric_id not in rubrics:
                row = db.execute("SELECT rules_json FROM frq_rubric_revisions WHERE id=?", (rubric_id,)).fetchone()
                rubrics[rubric_id] = {i["id"]: i for i in json.loads(row[0])["items"]} if row else {}
            item = rubrics[rubric_id].get(iid)
            return f"{iid} ({item['points']:+g}): {cut(norm(item.get('description')), 260)}" if item else (iid or "(no line named)")
        said, tasks = {}, {}
        for (task, _), (qid, rubric_id, output) in steps.items():
            for sentence in output.get("rubric_ambiguities") or []:
                sentence = scrubbed(sentence, names)
                found = re.match(r"\W*(R\d+(?:_\d+)?)", sentence)
                key = (family_of.get(qid, ""), line(rubric_id, found.group(1) if found else ""))
                said.setdefault(key, [])
                tasks.setdefault(key, set()).add(task)
                if sentence and sentence.casefold() not in {s.casefold() for s in said[key]}:
                    said[key].append(sentence)
        doubts = []
        for key in sorted(said, key=lambda key: -len(tasks[key]))[:CAPS["grader_doubts"]]:
            doubts.append({"id": f"D{len(doubts) + 1}", "problem": key[0], "line": key[1], "responses": len(tasks[key]),
                           "doubts": [cut(s, 420) for s in sorted(said[key], key=len)[:CAPS["doubts_per_line"]]]})
        both, differ = {}, {}
        ticked = lambda output: {a["rubric_item_id"] for a in output.get("applications") or [] if a.get("applied")}
        for (task, stage), (qid, rubric_id, first) in steps.items():
            if stage != "grade:primary" or (task, "grade:second") not in steps:
                continue
            both[(qid, rubric_id)] = both.get((qid, rubric_id), 0) + 1
            for iid in ticked(first) ^ ticked(steps[(task, "grade:second")][2]):
                differ[(qid, rubric_id, iid)] = differ.get((qid, rubric_id, iid), 0) + 1
        disagreements = []
        for (qid, rubric_id, iid), k in sorted(differ.items(), key=lambda kv: -kv[1] / both[kv[0][:2]]):
            n = both[(qid, rubric_id)]
            if k >= 5 and k / n >= .05 and len(disagreements) < CAPS["grader_disagreements"]:
                disagreements.append({"id": f"G{len(disagreements) + 1}", "problem": family_of.get(qid, ""), "line": line(rubric_id, iid),
                                      "graded_by_two": n, "ticked_differently": k})
        return doubts, disagreements

    # -- Asking for a pass ---------------------------------------------------------------------------------------------------------
    def _model(self):
        settings = self.frq.settings()["settings"]
        endpoint = config.rubric_reviewer(settings)
        if not endpoint:
            raise ValueError("Choose a Rubric drafting and review model in Compute settings first: it writes the memories.")
        return {**endpoint, "max_tokens": max(endpoint.get("max_tokens") or 0, TOKENS)}, settings

    def queue(self, aid, trigger, actor="workspace", after=None):
        """Queues one pass over a plan's evidence, unless one is already waiting or running for it. The evidence is read when the pass
        runs (`run`), so a Certify only records this row. Returns the run's ID, or None when one was already waiting."""
        endpoint, settings = self._model()
        now = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            definition = definition_of_plan(db, aid)
            if not definition:
                raise NotFound("No grading plan has that ID.")
            if db.execute("SELECT 1 FROM frq_rubric_memory_runs WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
                return None
            # `after`: "finalization" for a pass Faculty asked for itself once the instructor approved rubrics after answering their
            # questions (GRADING "NOTHING ASKED TWICE"); the table's trigger then says "instructor", the act it followed.
            rid, given_input = uid(), {"rule": RULE, "assignment_id": aid, "trigger": trigger, **({"after": after} if after else {})}
            db.execute("""INSERT INTO frq_rubric_memory_runs(id,definition_id,assignment_id,trigger,input_sha256,input_json,profile_json,status,actor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'pending',?,?,?)""",
                       (rid, definition, aid, trigger, digest(given_input), encoded(given_input), encoded({"endpoint": endpoint, "settings": settings}), actor, now, now))
            db.commit()
        wake = getattr(getattr(self.frq, "inbox", None), "wake", None)
        if wake is not None:
            wake.set()
        return rid

    def after_certified(self, aid):
        """The instructor's own Certify of a plan: one pass over its evidence. Never a reason for the certification to fail: without a
        Rubric drafting and review model nothing is queued, and a pass that could not be queued is offered again on the course's Rubric
        memories page, which lists the certified assignments no pass has read."""
        import logging
        try:
            if not self.learns():
                return None
            return self.queue(aid, "certified")
        except ValueError:  # no rubric model chosen: nothing to ask
            return None
        except Exception:  # noqa: BLE001 - the certification stands whatever happens here
            logging.getLogger(__name__).exception("no rubric memory pass could be queued after certifying %s", aid)
            return None

    def after_finalization(self, aid):
        """NOTHING ASKED TWICE ACROSS ASSIGNMENTS (requirement, 2026-10-24): once the instructor approves a plan's rubrics after answering
        rubric questions no pass of this plan has read (in Resolve concerns, or at the draft review of the quiz it came from), one pass
        reads them, so the next assignment's draft and check know the rulings without waiting for this one's Certify. Never a reason
        for the approval to fail; without a Rubric drafting and review model nothing is queued."""
        import logging
        try:
            if not self.learns():
                return None
            with connect(self.path) as db:
                latest = db.execute("SELECT MAX(created_at) FROM frq_rubric_memory_runs WHERE assignment_id=?", (aid,)).fetchone()[0]
                answered = any((concern.get("response") or "").strip() for (given,) in db.execute(
                    """SELECT input_json FROM frq_rubric_quality_checks WHERE assignment_id=? AND status='complete' AND cancelled=0
                    AND json_extract(input_json,'$.resolves') IS NOT NULL AND (? IS NULL OR created_at>?)""", (aid, latest, latest))
                    for concern in (json.loads(given).get("resolves") or {}).get("concerns") or [])
                if not answered and not latest:
                    answered = any((answer or "").strip() for *_, answer in self._draft_answers(db, aid))
            return self.queue(aid, "instructor", "automatic", after="finalization") if answered else None
        except ValueError:  # no rubric model chosen: nothing to ask
            return None
        except Exception:  # noqa: BLE001 - the approval stands whatever happens here
            logging.getLogger(__name__).exception("no rubric memory pass could be queued after finalizing %s", aid)
            return None

    def certified_plans(self, db, definition_id):
        """The course's certified plans (any term), oldest certification first, each with its term and whether a pass read it."""
        found = []
        for row in db.execute("""SELECT a.id, e.title, TRIM(COALESCE(NULLIF(t.term,'Other'),t.term_other,'') || ' ' || COALESCE(t.year,'')) term,
                MIN(c.created_at) certified FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id
                JOIN assignments d ON d.id=e.assignment_id JOIN frq_certifications c ON c.assignment_id=a.id AND c.amends_id IS NULL
                LEFT JOIN course_terms t ON t.id=e.term_id WHERE d.definition_id=? GROUP BY a.id ORDER BY certified""", (definition_id,)):
            run = db.execute("SELECT status FROM frq_rubric_memory_runs WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
            found.append({"assignment_id": row["id"], "title": row["title"], "term": row["term"] or "", "certified_at": row["certified"], "learned": run[0] if run else None})
        return found

    def learn(self, definition_id, data, actor="workspace"):
        """The instructor's request: one pass for each named certified plan of the course (all not yet read when none is named), in the
        order they were certified."""
        with connect(self.path) as db:
            if not db.execute("SELECT 1 FROM course_definitions WHERE id=?", (definition_id,)).fetchone():
                raise NotFound("No course has that ID.")
            plans = self.certified_plans(db, definition_id)
        wanted = data.get("assignment_ids")
        if wanted is not None and (not isinstance(wanted, list) or not all(isinstance(a, str) for a in wanted)):
            raise ValueError("assignment_ids is a list of grading plan IDs.")
        chosen = [p for p in plans if (p["assignment_id"] in wanted if wanted is not None else p["learned"] in (None, "failed"))]
        if wanted is not None and len(chosen) != len(set(wanted)):
            raise Conflict("Only this course's certified assignments can be learned from.")
        queued = [rid for rid in (self.queue(p["assignment_id"], "instructor", actor) for p in chosen) if rid]
        return {**self.view(definition_id), "queued": len(queued)}

    # -- The page ------------------------------------------------------------------------------------------------------------------
    def view(self, definition_id):
        with connect(self.path) as db:
            if not db.execute("SELECT 1 FROM course_definitions WHERE id=?", (definition_id,)).fetchone():
                raise NotFound("No course has that ID.")
            every = rows(db, definition_id)
            kept = {row["id"] for row in standing(db, definition_id)}
            by_id = {row["id"]: row for row in every}
            titles = {row[0]: row[1] for row in db.execute("SELECT a.id, e.title FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id")}
            runs = [dict(r) for r in db.execute("""SELECT id,assignment_id,trigger,json_extract(input_json,'$.after') after,status,error,result_json,created_at,updated_at FROM frq_rubric_memory_runs
                WHERE definition_id=? ORDER BY rowid DESC LIMIT 12""", (definition_id,))]
            plans = self.certified_plans(db, definition_id)
        endpoint = config.rubric_reviewer(self.frq.settings()["settings"])

        def shown(row):
            source = row["sources"]
            ids = source.get("ids") or []
            replaced = by_id.get(row["replaces_id"]) if row["replaces_id"] else None
            return {"id": row["id"], "label": label(row["number"]), "kind": row["kind"], "action": row["action"], "rule": row["rule"], "covers": row["covers"],
                    "not_when": row["not_when"], "why": row["why"], "state": row["state"], "standing": row["id"] in kept, "created_at": row["created_at"],
                    "decided_at": row["decided_at"], "from": titles.get(source.get("assignment_id"), ""),
                    "evidence": {kind: sum(1 for i in ids if i.startswith(prefix)) for kind, prefix in (("answers", "R"), ("questions", "A"), ("doubts", "D"), ("disagreements", "G"))},
                    **({"replaces": {"id": replaced["id"], "kind": replaced["kind"], "rule": replaced["rule"], "covers": replaced["covers"], "not_when": replaced["not_when"]}} if replaced else {})}
        waiting = [shown(row) for row in every if row["state"] == "waiting"]
        standing_rows = [shown(row) for row in every if row["id"] in kept]
        dropped = sorted((shown(row) for row in every if row["state"] == "dropped"), key=lambda row: row["decided_at"] or "", reverse=True)
        # A kept retirement took its memory out of Kept, and was itself in no list (2026-11-09 review: nothing on the page could
        # drop it). Each is listed with what it retired while that memory is away; the instructor's Drop of it brings it back.
        retired = [shown(row) for row in every if row["state"] == "kept" and row["action"] == "retire" and row["replaces_id"] not in kept]
        return {"definition_id": definition_id, "rule": RULE, "model": endpoint["name"] if endpoint else None,
                "waiting": waiting, "standing": standing_rows, "dropped": dropped[:20], **({"retired": retired} if retired else {}),
                "runs": [{"id": r["id"], "assignment": titles.get(r["assignment_id"], ""), "trigger": r["trigger"], **({"after": r["after"]} if r["after"] else {}),
                          "status": r["status"], "error": r["error"],
                          "created": len((json.loads(r["result_json"]) or {}).get("created") or []) if r["result_json"] else 0,
                          "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in runs],
                "certified": plans}

    def decide(self, definition_id, data, actor="workspace"):
        """The instructor's Keep and Drop: `decisions` [{memory_id, decision: keep|drop}], each about a memory of this course. A later
        decision about the same memory replaces the earlier one (Drop takes a kept memory back; Keep brings a dropped one back)."""
        decisions = data.get("decisions")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 100:
            raise ValueError("decisions is a list of 1–100 {memory_id, decision}.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            mine = {row[0] for row in db.execute("SELECT id FROM frq_rubric_memories WHERE definition_id=?", (definition_id,))}
            now = timestamp()
            for row in decisions:
                if not isinstance(row, dict) or row.get("decision") not in ("keep", "drop") or not isinstance(row.get("memory_id"), str) or row["memory_id"] not in mine:
                    raise ValueError("Each decision names a memory of this course and is keep or drop.")
                db.execute("INSERT INTO frq_rubric_memory_decisions(id,memory_id,decision,actor,created_at) VALUES(?,?,?,?,?)",
                           (uid(), row["memory_id"], row["decision"], actor, now))
            db.commit()
        return self.view(definition_id)


def printed(question):
    """A question's printed number for ordering (problems without one come last)."""
    found = re.sub(r"\D", "", str(question.get("printed_number") or ""))
    return int(found) if found else 10**6
