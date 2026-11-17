"""Read-only progress for term-owned grading plans; no new retained state.

The landing cards need setup counts, not rubric text, answer keys or student data.
Export progress compares retained decision IDs, never a timestamp or an old batch.
Certification (the sixth stage, "Certification and Export", requirement, 2026-09-26) compares the latest certification's markers with
what the grades are now: response grade CONTENT, the bubble sheets and key, the participants and their papers. Catalog reads only;
nothing here writes, and it runs on Home's grading poll, the Assignments list and an assignment's own read, and the Exams hub's
grading summary, so each marker is a bounded SQL read.
"""
import json
import threading
from collections import OrderedDict
from decimal import Decimal
from functools import lru_cache

from teach_inbox.service import PRESENT
from .models import digest, finite_decimal

# A changed snapshot, marker or check rule gets a new name; a certification made under another rule reads as changed.
RULE = "certify-v1"
FINAL = ("approved", "manual", "auto_accepted")
# (catalog, export id) -> the export's (response, final, proposal) IDs. Exports are immutable and retained (frq_export_immutable,
# frq_export_retained), so a set read once never goes stale; a snapshot is megabytes of JSON to walk on every workflow read.
# About 1 MB each for an exam of 2,700 responses; least recently used first out, so a poll over more plans than the bound still
# keeps most of them.
_EXPORTED, _EXPORTED_LOCK, EXPORTED_MAX = OrderedDict(), threading.Lock(), 32


def preparation_counts(db, plan, questions):
    current = {q["question_id"]: q for q in questions}
    used = {q["rubric"]["source_artifact_id"] for q in questions if q["rubric"]}
    counts, seen = {"pending": 0, "failed": 0}, set()
    for row in db.execute("""SELECT kind,status,json_extract(payload_json,'$.question_id') question_id,
            json_extract(payload_json,'$.question_version') question_version,
            json_extract(payload_json,'$.source_artifact_id') artifact_id
            FROM frq_preparations WHERE assignment_id=? ORDER BY rowid DESC""", (plan["id"],)):
        key = (row["kind"], row["question_id"], row["artifact_id"])
        if key in seen:
            continue
        seen.add(key)
        if row["kind"] == "compile":
            question = current.get(row["question_id"])
            if not question or question["version"] != row["question_version"]:
                continue
        elif row["artifact_id"] in used or not db.execute(f"SELECT 1 FROM inbox_artifacts a WHERE a.id=? AND {PRESENT.format('a.message_id')}", (row["artifact_id"],)).fetchone():
            continue
        if row["status"] in ("pending", "running"):
            counts["pending"] += 1
        elif row["status"] == "failed":
            counts["failed"] += 1
    return counts


def setup_summary(service, db, plan, questions, checks=None, quality=None, memo=None):
    """`checks` {question_id: location status}, `quality` and `memo` are what the caller's read already has (`workflow_summary`)."""
    compact = []
    readiness = (plan.get('rubric_quality') or quality or service.rubric_quality_status(db, plan['id']))['readiness']
    for q in questions:
        check = q.get("location_check") or (checks or {}).get(q["question_id"]) or service.location_status(db, plan["id"], q, memo=memo)
        context = check["context"]
        compact.append({
            "question_id": q["question_id"], "question_key": q["question_key"], "exam_version": q["exam_version"],
            "content": {"title": q["content"]["title"]},
            "rubric": {"approved_at": q["rubric"]["approved_at"]} if q["rubric"] else None,
            "has_answer_key": bool(q["content"].get("answer_key")),
            # Confirmed for the rubric reviewer: its exact revision and comments (2026-09-24). None without a rubric.
            "review_ready": readiness[q["question_id"]]["ready"] if q["question_id"] in readiness else None,
            "location_check": {"status": check["status"], "label": check["label"], "reading_progress": check.get('reading_progress'), "context": {
                "printed_number": context.get("printed_number"),
                "template": {"id": context["template"]["id"]} if context["template"] else None,
                "templates": [{"id": t["id"]} for t in context["templates"]]}}})
    versions = plan.get("exam_versions")
    if versions is None:
        graded, seated, declared, added = (service.plan_versions(db, plan["id"]), service.chart_versions(db, plan), service.declared_versions(db, plan),
                                           service.added_versions(db, plan))
        versions = {"graded": graded, "seated": seated, "declared": declared, "added": added, "letters": sorted(set(graded) | set(seated) | set(declared) | set(added)),
                    "blanks": {k: {"label": v["label"]} for k, v in service.version_blanks(db, plan["id"]).items()}}
    templates = [{"id": r[0]} for r in db.execute("SELECT id FROM assignment_templates WHERE assignment_id=?", (plan["source_assignment_id"],))]
    return {"questions": compact, "templates": templates, "exam_versions": versions,
            "template_setup": plan.get('template_setup') or service.template_setup(db, plan['id']),
            "preparation": preparation_counts(db, plan, questions)}


# A read limited to the responses a drill-down shows: every lookup of its answer is by one of them, so the answers are the same.
ONLY = " AND {} IN (SELECT value FROM json_each(?))"


def only_args(only):
    return () if only is None else (json.dumps(sorted(only)),)


def automatic_decision(db, result_id):
    row = db.execute("""SELECT a.id FROM frq_all_acceptances a WHERE a.result_id=? AND a.kind='automatic' AND a.active=1""",
        (result_id,)).fetchone()
    return row[0] if row else None


def automatic_decisions(db, aid, only=None):
    """{result_id: acceptance_id} of every ACTIVE automatic acceptance of a current result of the plan: one read instead of one
    `automatic_decision` per response (the grading overview Home polls, and the Assignments list, read this for every plan on every
    poll). `only`: response IDs, for a caller that looks up those responses alone (a drill-down)."""
    # a.assignment_id=? adds nothing (the team acceptances' scope triggers and the benchmark acceptance's insert take the response's own
    # plan, which frq_response_owner keeps), but SQLite pushes it into every arm of the view instead of reading every plan's acceptances.
    return {r[0]: r[1] for r in db.execute(f"""SELECT a.result_id,a.id FROM frq_all_acceptances a JOIN frq_responses r ON r.current_result_id=a.result_id
        WHERE r.assignment_id=? AND a.assignment_id=? AND a.kind='automatic' AND a.active=1{ONLY.format('r.id') if only is not None else ''}""",
        (aid, aid, *only_args(only)))}


def response_counts(responses, automatic):
    counts = {}
    for response in responses:
        state = response["state"]
        if state == "auto_accepted" and not automatic.get(response["current_result_id"]):
            state = "grading_review"
        counts[state] = counts.get(state, 0) + 1
    return counts


def response_progress(db, aid, responses, counts, new):
    """Current response work, never the sum of historical batches or retries."""
    # CROSS JOIN keeps the plan's own batches first: frq_tasks_response would otherwise be walked whole to spare a sort.
    latest = {row['response_id']: row['status'] for row in db.execute('''
        SELECT t.response_id,t.status FROM frq_tasks t
        JOIN (SELECT t.response_id,MAX(t.rowid) last_row FROM frq_jobs j
              CROSS JOIN frq_tasks t ON t.job_id=j.id WHERE j.assignment_id=? GROUP BY t.response_id) n
          ON t.rowid=n.last_row''', (aid,))}
    active = {row[0] for row in db.execute('''SELECT DISTINCT t.response_id FROM frq_tasks t
        JOIN frq_jobs j ON j.id=t.job_id WHERE j.assignment_id=? AND t.status IN ('pending','running')''', (aid,))}
    # Unreviewed or missing arrivals are Inbox work, not grading work. Existing
    # responses still become stale when their previously reviewed source changes.
    total = len(responses) + new
    completed = sum(r['state'] not in ('pending', 'stale') and r['id'] not in active
                    and latest.get(r['id']) != 'failed' for r in responses)
    finalized = sum(counts.get(state, 0) for state in FINAL)
    return {'grade': {'completed': completed, 'total': total},
            'review': {'completed': finalized, 'total': total}}


def amount(value):
    """A score as exact text ("3.5", "0", "-1"), or None when there is none. Float scores are read through their shortest text."""
    number = finite_decimal(value)
    if number is None:
        return None
    if number == 0:
        return "0"
    number = number.normalize()
    return format(number.quantize(Decimal(1)) if number == number.to_integral() else number, "f")


@lru_cache(maxsize=32768)
def score_content(score_json):
    """(score, sorted selected, adjustments) of one stored score_json TEXT, as `amount` reads them. Keyed by the text itself, so an
    answer is exactly what parsing that text gives; callers take lists of their own from the tuples."""
    value = json.loads(score_json or "{}")
    return (amount(value.get("score")), tuple(sorted(value.get("selected") or [])),
            tuple(amount(a.get("points")) for a in value.get("adjustments") or []))


@lru_cache(maxsize=32768)
def returned_by_caps(score_json):
    """What part caps gave back in one stored score_json TEXT (GRADING "PART CAPS"), as `amount` reads it; None when nothing."""
    value = json.loads(score_json or "{}").get("part_caps")
    return amount(value) if value else None


def decision_rows(db, aid, responses, automatic=None, only=None):
    """{response_id: the grade that counts now}, in ONE catalog read: for a final response (`state` approved or manual: the current
    review; auto_accepted with an active automatic acceptance: that acceptance) its `kind` ('review'|'automatic'), `decision_id`,
    `result_id` (the result the decision was made on: a review's own result, not a newer proposal) and its CONTENT (`score`,
    `selected`, `adjustments`, from the review's or the result's score); any other response `kind` None with its `state`. `only`:
    the IDs of `responses` when they are a few of the plan's (a drill-down), to read those alone."""
    automatic = automatic_decisions(db, aid, only) if automatic is None else automatic
    stored = {r[0]: r for r in db.execute(f"""SELECT r.id,v.id review_id,v.result_id review_result,v.score_json review_score,x.score_json result_score
        FROM frq_responses r LEFT JOIN frq_reviews v ON v.id=r.current_review_id LEFT JOIN frq_results x ON x.id=r.current_result_id
        WHERE r.assignment_id=?{ONLY.format('r.id') if only is not None else ''}""", (aid, *only_args(only)))}
    rows = {}
    for response in responses:
        row, state = stored.get(response["id"]), response["state"]
        entry = {"response_id": response["id"], "question_id": response.get("question_id"), "section_id": response.get("section_id"),
                 "enrollment_id": response.get("enrollment_id"), "state": state, "kind": None, "decision_id": None, "result_id": None,
                 "score": None, "selected": [], "adjustments": []}
        score = None
        if row and state in ("approved", "manual") and row["review_id"]:
            entry.update(kind="review", decision_id=row["review_id"], result_id=row["review_result"])
            score = row["review_score"]
        elif row and state == "auto_accepted" and automatic.get(response["current_result_id"]):
            entry.update(kind="automatic", decision_id=automatic[response["current_result_id"]], result_id=response["current_result_id"])
            score = row["result_score"]
        elif state == "auto_accepted":
            entry["state"] = "grading_review"  # as `response_counts` counts it: no active acceptance, so it waits for review
        if entry["kind"]:
            points, selected, adjustments = score_content(score)
            entry.update(score=points, selected=list(selected), adjustments=list(adjustments))
            if returned_by_caps(score):
                entry["part_caps"] = returned_by_caps(score)
        rows[response["id"]] = entry
    return rows


def content(entry):
    """What a grade IS, never which record holds it (critique C7): approving an automatic grade unchanged, or a review that repeats
    it, keeps the same content, so neither reopens a certification or asks Gradescope for a resend."""
    if entry["kind"]:
        return ("graded", entry["score"], tuple(entry["selected"]), tuple(entry["adjustments"]))
    return ("open", entry["state"])


def decision_state(db, aid, responses, rows=None):
    """Every response of the plan as it counts for a grade, sorted by response id: ("r", response_id, *content). One read.
    On a plan graded for participation each final grade is read with its line, whichever rows the caller holds (counting a
    counted row changes nothing): a certification records this of the grades as they count, and whatever asks whether it still
    stands must read them the same way (GRADING "GRADED FOR PARTICIPATION" > THE CERTIFICATION COUNTS THEM TOO)."""
    from . import participation  # it imports this module
    rows = participation.counted_entries(db, aid, decision_rows(db, aid, responses) if rows is None else rows)
    return [("r", rid, *content(rows[rid])) for rid in sorted(rows)]


def export_summary(db, aid, responses, rows=None, catalog=None):
    """`catalog`: the catalog's path, when the caller has it, to read each export's IDs once per process (`_EXPORTED`). The snapshot
    is read only when every response has a final grade: otherwise nothing can be current."""
    # Dataset exports are training material, not a grade export. Inspect only the
    # latest grade snapshot, extracting IDs in SQL rather than sending its payload.
    latest = db.execute("SELECT id,created_at FROM frq_exports WHERE assignment_id=? AND kind!='dataset' ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()
    if not latest:
        return {"current": False, "created_at": None}
    rows = decision_rows(db, aid, responses) if rows is None else rows
    current = set()
    for entry in rows.values():
        # A new machine proposal does not replace an instructor's retained decision; export follows the result that decision reviewed.
        if not entry["kind"]:
            return {"current": False, "created_at": latest["created_at"]}
        current.add((entry["response_id"], entry["decision_id"], entry["result_id"]))
    if not current:
        return {"current": False, "created_at": latest["created_at"]}
    key, exported = (catalog, latest["id"]), None
    if catalog:
        with _EXPORTED_LOCK:
            exported = _EXPORTED.get(key)
            if exported is not None:
                _EXPORTED.move_to_end(key)
    if exported is None:
        exported = frozenset(tuple(row) for row in db.execute("""SELECT json_extract(r.value,'$.response_id'),
            json_extract(r.value,'$.final.id'),json_extract(r.value,'$.proposal.id')
            FROM frq_exports e,json_each(e.snapshot_json,'$.records') r WHERE e.id=?""", (latest["id"],)))
        if catalog:
            with _EXPORTED_LOCK:
                _EXPORTED[key] = exported
                _EXPORTED.move_to_end(key)
                while len(_EXPORTED) > EXPORTED_MAX:
                    _EXPORTED.popitem(last=False)
    return {"current": current == exported, "created_at": latest["created_at"]}


def _table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone())


def bubble_marker(db, plan, without=frozenset()):
    """What every bubble-sheet score of the plan is read from, as a digest (SQL and light Python only): the latest answer key, the
    instructor's identification threshold (its VALUE: re-saving it, or a setting that changes nothing, reopens nothing), and for each
    sheet of the latest complete read of every PRESENT scan of this assignment and term (the `_bubble_runs` rule) whether its scan
    cleared Inbox review, whom it is recorded for (the latest review, else the match under today's matcher at the threshold; a
    re-match that names the same student changes nothing) and the latest review's content. Machine readings are immutable; a changed
    reader rule reaches them through code, not the catalog, and is not a marker. `without` (enrollment IDs; LATE PAPERS CERTIFY
    THEMSELVES): a run whose every sheet is recorded for one of them is left out, as it was before their late sheets came."""
    from .bubble_keys import BubbleKeysMixin
    from bubble_sheets import matching
    key = db.execute("SELECT id FROM frq_bubble_keys WHERE assignment_id=? ORDER BY revision DESC LIMIT 1", (plan["id"],)).fetchone()
    threshold = None
    if _table(db, "bubble_settings"):
        found = db.execute("SELECT auto_identify FROM bubble_settings ORDER BY id DESC LIMIT 1").fetchone()
        threshold = found[0] if found else None
    threshold = matching.AUTO_IDENTIFY if threshold is None else threshold
    runs = []
    for run in BubbleKeysMixin._bubble_runs(db, plan):
        sheets = []
        for row in db.execute("""SELECT b.id,COALESCE(m.match_json,b.match_json) match_json,v.section_id,v.enrollment_id,v.answers_json,v.version,v.id review_id
                FROM bubble_sheets b LEFT JOIN bubble_matches m ON m.sheet_id=b.id AND m.matcher=?
                LEFT JOIN bubble_reviews v ON v.id=(SELECT MAX(n.id) FROM bubble_reviews n WHERE n.sheet_id=b.id)
                WHERE b.run_id=? ORDER BY b.id""", (matching.MATCHER, run["id"])):
            if row["review_id"] is not None:
                sheets.append((row["id"], "review", row["section_id"], row["enrollment_id"], row["answers_json"], row["version"]))
                continue
            try:
                match = json.loads(row["match_json"] or "{}")
                who = match.get("enrollment_id") if matching.identified({"state": match.get("state"), "confidence": match.get("confidence") or 0,
                                                                        "notes": match.get("notes") or []}, threshold) else None
            except (ValueError, TypeError, AttributeError):
                who = None
            sheets.append((row["id"], "read", who))
        if without and sheets and all(sheet[3 if sheet[1] == "review" else 2] in without for sheet in sheets):
            continue
        runs.append((run["message_id"], bool(run["instructor_review_required"]), run["id"], sheets))
    return digest({"key": key[0] if key else None, "threshold": round(float(threshold), 4), "runs": runs})


def dr_bookings(db, plan):
    """The DR-center bookings linked to the plan's course-wide assignment for memberships of its exam's sections, with the booking
    fields a page may show (never `snapshot_json` whole: it carries the raw export row). Campus wall-clock dates, as DR keeps them."""
    if not (_table(db, "dr_exam_assignment_links") and _table(db, "dr_exam_bookings")):
        return []
    return [dict(r) for r in db.execute("""SELECT b.id booking_id,b.section_id,b.enrollment_id,
            json_extract(v.snapshot_json,'$.exam_type') exam_type,json_extract(v.snapshot_json,'$.date') date,
            json_extract(v.snapshot_json,'$.start_time') start_time,json_extract(v.snapshot_json,'$.end_time') end_time,
            json_extract(v.snapshot_json,'$.length_minutes') length_minutes,json_extract(v.snapshot_json,'$.campus') campus
        FROM dr_exam_bookings b JOIN dr_exam_revisions v ON v.id=b.revision_id JOIN dr_exam_assignment_links l ON l.booking_id=b.id
        JOIN exam_event_sections x ON x.section_id=b.section_id AND x.event_id=?
        WHERE l.assignment_id=? ORDER BY b.section_id,b.enrollment_id,date,b.id""", (plan["event_id"], plan["source_assignment_id"]))]


def roster_additions(db, event_id):
    """Memberships of the exam's sections that are not participants of it (added to the roster after the exam was made)."""
    return [tuple(r) for r in db.execute("""SELECT m.section_id,m.enrollment_id FROM roster_current m
        JOIN exam_event_sections x ON x.section_id=m.section_id WHERE x.event_id=? AND NOT EXISTS(SELECT 1 FROM exam_participants p
        WHERE p.event_id=x.event_id AND p.section_id=m.section_id AND p.enrollment_id=m.enrollment_id) ORDER BY m.section_id,m.enrollment_id""", (event_id,))]


def participants_marker(db, plan, collection_students, parts=(), override=None, dropped_as_of=None):
    """Who the certified grades are for and what paper each has, as a digest: every participant's expectation, each one's collected
    paper (its status and reviewed assembly: a `no_scan` student whose paper arrives must reopen the step even before it is graded),
    the roster additions not yet in the exam, the DR bookings linked to the assignment with their dates (a booking linked after
    certifying turns a confirmed zero into a DR student), and, when the Exams page's grading is certified, its finalizations and
    releases. `collection_students` is `_collection(...)["students"]`, which `workflow_summary` already reads. `override`
    ({(section_id, enrollment_id): expectation, or None to leave the participant out}; LATE PAPERS CERTIFY THEMSELVES): the marker
    as it was before late papers came, the caller reverting those students' papers in `collection_students` itself."""
    participants = [tuple(r) for r in db.execute("""SELECT section_id,enrollment_id,expectation FROM exam_participants WHERE event_id=?
        ORDER BY section_id,enrollment_id""", (plan["event_id"],))]
    if override:
        participants = [(s, e, override.get((s, e), x)) for s, e, x in participants if override.get((s, e), x) is not None]
    papers = sorted((s["section_id"], s["enrollment_id"], s.get("status"), s.get("assembly_revision_id")) for s in collection_students or [])
    dr = [(b["section_id"], b["enrollment_id"], b["booking_id"], b["date"]) for b in dr_bookings(db, plan)]
    exams = None
    if "exams" in parts and _table(db, "exam_grade_finalizations"):
        exams = [db.execute(f"SELECT MAX(rowid) FROM {table} WHERE event_id=?", (plan["event_id"],)).fetchone()[0]
                 for table in ("exam_grade_finalizations", "exam_grade_releases")]
    # A DROP NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-10-23):
    # dropped participants are no longer in the digest. From 2026-09-27 until then they were named whenever there were any, so
    # `dropped_as_of` (a certification's created_at) rebuilds that older digest from the drops recorded by then
    # (`participants_changed`).
    dropped = [] if dropped_as_of is None else [tuple(r) for r in db.execute("""SELECT d.section_id,d.enrollment_id FROM roster_drops d
        JOIN exam_participants p ON p.section_id=d.section_id AND p.enrollment_id=d.enrollment_id
        WHERE p.event_id=? AND d.action='drop' AND d.rowid=(SELECT MAX(x.rowid) FROM roster_drops x WHERE x.section_id=d.section_id
            AND x.enrollment_id=d.enrollment_id AND x.created_at<=?) ORDER BY 1,2""", (plan["event_id"], dropped_as_of))]
    return digest({"participants": participants, "papers": papers, "additions": len(roster_additions(db, plan["event_id"])), "dr": dr, "exams": exams,
                   **({"dropped": dropped} if dropped else {})})


def participants_changed(db, plan, collection_students, parts, certification, override=None):
    """Whether who the certification's grades are for, or their papers, changed since: its recorded participants marker matches
    neither the marker now nor (A DROP NEVER ASKS TO CERTIFY AGAIN) the older one that named the drops recorded by its time."""
    recorded = certification["participants_sha256"]
    if participants_marker(db, plan, collection_students, parts, override=override) == recorded:
        return False
    return participants_marker(db, plan, collection_students, parts, override=override, dropped_as_of=certification["created_at"]) != recorded


def grade_marker(db, plan_id, parts):
    """A cheap, catalog-only digest of what a certified plan's grades are read from, for the GRADEBOOK's hint that grades may have
    changed since a certification (review F4): each response's STORED grade content (the current approved or manual review's
    score, else an active automatic acceptance's, else open with its review status), the plan's pinned question and rubric
    revisions (a rubric change makes grades stale), the participants and their expectations, the DR bookings linked with their
    dates, and the bubble marker when the bubble sheets were certified. No roster payload, no grading rule run: it is a hint, never
    the exact state, which only Certification and Export computes. Approving an automatic grade unchanged moves nothing."""
    row = db.execute("""SELECT a.id,a.event_id,e.assignment_id source_assignment_id,e.term_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id
        WHERE a.id=?""", (plan_id,)).fetchone()
    if not row:
        return None
    plan = dict(row)
    automatic = automatic_decisions(db, plan_id)
    grades = []
    for r in db.execute("""SELECT r.id,r.current_result_id,v.status,v.score_json review_score,x.score_json result_score FROM frq_responses r
            LEFT JOIN frq_reviews v ON v.id=r.current_review_id LEFT JOIN frq_results x ON x.id=r.current_result_id
            WHERE r.assignment_id=? ORDER BY r.id""", (plan_id,)):
        score = r["review_score"] if r["status"] in ("approved", "manual") else r["result_score"] if automatic.get(r["current_result_id"]) else None
        if score is None:
            grades.append((r["id"], "open", r["status"] or ""))
            continue
        points, selected, adjustments = score_content(score)
        grades.append((r["id"], "graded", points, list(selected), list(adjustments)))
    pins = [tuple(r) for r in db.execute("""SELECT question_id,question_revision_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=?
        ORDER BY question_id""", (plan_id,))]
    participants = [tuple(r) for r in db.execute("""SELECT section_id,enrollment_id,expectation FROM exam_participants WHERE event_id=?
        ORDER BY section_id,enrollment_id""", (plan["event_id"],))]
    dr = [(b["section_id"], b["enrollment_id"], b["booking_id"], b["date"]) for b in dr_bookings(db, plan)]
    return digest({"grades": grades, "pins": pins, "participants": participants, "dr": dr,
                   "bubble": bubble_marker(db, plan) if "bubble" in (parts or ()) else None})


def latest_certification(db, aid):
    if not _table(db, "frq_certifications"):
        return None
    row = db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()
    return dict(row) if row else None


def dr_passed(db, certification_id, today):
    """A student left out until a DR exam whose (latest) date is now past: the certification must be made again."""
    return bool(db.execute("""SELECT 1 FROM frq_certification_students WHERE certification_id=? AND status='left_out' AND reason='dr_ahead'
        AND dr_date<? LIMIT 1""", (certification_id, today)).fetchone())


def makeup_ended(db, plan, certification_id):
    """A student left out until their makeup (GRADING CERTIFICATION "MAKEUP OWED") whose absence no longer covers the day it was given:
    marked not excused, withdrawn or moved. The certification must be made again, and gives them 0 (a makeup paper that comes back
    moves the participants marker instead)."""
    waiting = [tuple(r) for r in db.execute("""SELECT section_id,enrollment_id FROM frq_certification_students WHERE certification_id=?
        AND status='left_out' AND reason='makeup'""", (certification_id,))]
    if not waiting:
        return False
    from absences import makeups
    row = db.execute("SELECT term_id,assignment_id FROM exam_events WHERE id=?", (plan["event_id"],)).fetchone()
    if not row:
        return False
    return bool(set(waiting) - set(makeups.covering(db, row[0], plan["event_id"], row[1], waiting)))


def certification_summary(db, plan, responses, workflow, collection_students, today=None, rows=None):
    """`workflow.certification`: whether the plan's latest certification still says what its grades are. A pure catalog read (the
    caller holds the lifecycle lock, as `workflow_summary`'s callers do); no service handle, so Home's grading overview, the
    Assignments list and the Exams hub read it as they read the rest of the workflow. `workflow` supplies the counts of new work (`new_responses`,
    `decision_responses`, `awaiting_review`, `pending`). `state` is none, current or changed; `changed` names why."""
    latest = latest_certification(db, plan["id"])
    if not latest:
        return {"state": "none", "id": None, "created_at": None, "changed": [], "gradebook": None, "left_out": 0, "left_out_dr": 0}
    if today is None:
        from exam_workflow.overview import campus_today
        today = campus_today()
    parts = json.loads(latest["parts_json"])
    changed = []
    if latest["rule"] != RULE:
        changed.append("rule")
    if digest(decision_state(db, plan["id"], responses, rows)) != latest["decisions_sha256"]:
        changed.append("grades")
    if bubble_marker(db, plan) != latest["bubble_sha256"]:
        changed.append("bubble_sheets")
    if participants_changed(db, plan, collection_students, parts, latest):
        changed.append("students")
    if dr_passed(db, latest["id"], today):
        changed.append("dr_date_passed")
    if makeup_ended(db, plan, latest["id"]):
        changed.append("makeup_ended")
    if any(workflow.get(key) for key in ("new_responses", "decision_responses", "awaiting_review", "pending")):
        changed.append("new_work")
    try:
        book = json.loads(latest["gradebook_json"] or "{}")
        summary = json.loads(latest["summary_json"] or "{}")
    except ValueError:
        book, summary = {}, {}
    return {"state": "changed" if changed else "current", "id": latest["id"], "created_at": latest["created_at"], "changed": changed,
            "left_out": summary.get("left_out", 0), "left_out_dr": summary.get("left_out_dr", summary.get("left_out", 0)),
            **({"left_out_makeup": summary["left_out_makeup"]} if summary.get("left_out_makeup") else {}),
            "gradebook": {key: book.get(key) for key in ("item_id", "title", "written", "kept")} if book else None}
