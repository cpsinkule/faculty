"""Read-only Home and operations projections; this module owns no teaching data.

GET /api/workspace/overview returns generated_at, attention cards (key, kind, label, count, href, and for Home a one-line
detail and a verb), upcoming_exams (at most eight dated, current-term sittings), recent_scans (at most six present scans,
with the existing ScanService.overview as scan), current_scans (at most eight present scans the Inbox files under Current:
failed, needing the instructor, or with the worker; each with its overview, received time and page counts),
current_scans_total, finished_recent (present scans received in the last seven days that the Inbox files as Completed),
finished_recent_archived (those of them whose assessment's grades are certified, which the Inbox files under Archived), and
operations: worker heartbeat age/state, per-queue and total pending, running, failed and needs_review counts, oldest
waiting timestamps/ages, and a sanitized storage/verified-backup summary. Queue counts describe retained jobs, not students.
A failure counts only while it is still the latest word on its work (QUEUE_SPECS): a grading task while it is its response's
latest and its batch was not cancelled, a rubric check while it is its assignment's latest uncancelled check, a rubric draft
while it is the latest uncancelled draft of its template and version, an answer-key reading (same table, input kind 'key')
while it is the latest uncancelled reading of its question, a rubric preparation while it is the latest of its kind for its
question, an answer-area check while it is its question's latest, and a printed-label reading while its page still needs
recovery. A failure superseded by a later attempt, a cancelled batch or a recovery by hand is history, not work.

Home's attention list carries no queue card for failures that already have an owner card: grading tasks (each current plan's
"Inspect failed grading", counted the Grading page's way) and scan inspection (the scan's own row in the Inbox card);
both stay in the health table. Failures of the grading-prep queues (preparation, drafts, answer-key readings, rubric checks,
answer-area checks) of a current plan are that plan's own card, linking the step that shows them; other terms' keep the
queue-wide card. A grading-prep queue whose failed and review rows all belong to one current plan's step links that step as its
own href too, so the health table and the shell's notices open it.

Every count across submissions applies teach_inbox.service.PRESENT: a deleted submission counts nowhere. Scans are counted
the way the Inbox files them (InboxService.filed, `listed_as`), never by their stored status, which stays needs_review
for good: the Inbox attention card (marked `derived: true` for the shell's badge, with `scans`, `submissions` and
`submissions_failed`) and the Inbox row of the health table alike, so Home never shows two Inbox numbers. Grading's attention
cards are light catalog counts (responses waiting for review, responses that failed to grade), not the grading workflow
itself; the Grading page stays the authority and Home replaces them with its own figures once it has read them, for the
plans listed in `current_grading_plans` (grading plans of current terms, the same scope as the cards).

Only existing permanent term/section, Inbox and workspace catalog owners are read. No roster payloads, message originals,
filenames, mail envelopes or recipients, command parameters, credentials, student names or identifiers enter this
response. Dates/times of exam sittings remain local wall-clock strings.
"""
from datetime import datetime, timezone
import shutil
from scheduling.clock import CAMPUS_ZONE

from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from certified_archive import certified_messages, uncertified
from course_storage import StorageError, timestamp
from grading.models import digest
from response_cards.service import exam_over
from rosters import connect
from teach_inbox.service import PRESENT


QUEUE_SPECS = (
    # key, label, table, predicate, href, and the column naming the Inbox message a row belongs to (for PRESENT), if any.
    # Keep Inbox first: its review count has a dedicated Home card.
    ("inbox", "Inbox commands", "inbox_jobs", "1=1", "#inbox", "q.message_id"),
    # ASSIGNMENT-WORKSPACE: a bare #documents now opens Assignments; the print jobs list is its own address.
    ("printing", "Print preparation", "print_batches", "1=1", "#documents?tab=batches", None),
    ("print_mail", "Print email", "print_email_exports", "1=1", "#documents?tab=batches", None),
    ("printer", "Printer sends", "print_printer_sends", "1=1", "#documents?tab=batches", None),
    ("revocations", "Response card revocations", "response_card_invalidations", "1=1", "#response-cards", None),
    ("card_mail", "Response card email", "response_card_mail", "1=1", "#response-cards", None),
    ("chart_mail", "Seating chart email", "seating_student_mail", "1=1", "#seating", None),
    ("scans", "Scan inspection", "document_decode_jobs", "1=1", "#inbox", "q.message_id"),
    # A failed reading counts while its page still needs recovery (alias_ocr.py `eligible`): a page recovered by hand or grouped
    # since can no longer be retried, and a page the instructor set aside belongs to no paper, so the failure is history.
    ("alias_ocr", "Printed label recognition", "scan_alias_ocr",
     "q.status!='failed' OR EXISTS(SELECT 1 FROM scan_pages sp WHERE sp.id=q.scan_page_id AND sp.status='unreadable' AND sp.page_stamp_id IS NULL AND sp.review_state!='ignored')",
     "#inbox", "q.message_id"),
    ("version_sort", "Version sorting", "scan_version_sorts",
     "q.attempt=(SELECT MAX(r.attempt) FROM scan_version_sorts r WHERE r.message_id=q.message_id)", "#inbox", "q.message_id"),
    ("bubble_sheets", "Bubble sheet reading",
     "(SELECT message_id,status,created_at,ROW_NUMBER() OVER (PARTITION BY message_id ORDER BY created_at DESC,rowid DESC) summary_rank FROM bubble_runs)",
     "q.summary_rank=1", "#inbox", "q.message_id"),
    # The grading queues count a failure only while it is the latest word on its work; pending and running rows are unchanged.
    # A grading task: its response's latest (frq_tasks_response), in a batch that was not cancelled.
    ("frq_grading", "Grading batches", "frq_tasks",
     "q.status!='failed' OR (q.rowid=(SELECT MAX(n.rowid) FROM frq_tasks n WHERE n.response_id=q.response_id) "
     "AND NOT EXISTS(SELECT 1 FROM frq_jobs j WHERE j.id=q.job_id AND j.cancel_requested=1))", "#grading", None),
    # A preparation: the latest of its kind for its question, the Grading tile's own rule.
    ("frq_preparation", "Rubric preparation", "frq_preparations",
     "q.status!='failed' OR q.rowid=(SELECT MAX(n.rowid) FROM frq_preparations n WHERE n.assignment_id=q.assignment_id AND n.kind=q.kind "
     "AND json_extract(n.payload_json,'$.question_id') IS json_extract(q.payload_json,'$.question_id'))", "#grading?tab=rubrics", None),
    # A rubric check: its assignment's latest uncancelled one (a rerun inserts a new row).
    ("frq_rubric_quality", "Rubric checks", "frq_rubric_quality_checks",
     "q.cancelled=0 AND (q.status!='failed' OR q.rowid=(SELECT MAX(n.rowid) FROM frq_rubric_quality_checks n "
     "WHERE n.assignment_id=q.assignment_id AND n.cancelled=0))", "#grading?tab=rubrics", None),
    # The table holds rubric drafts and answer-key readings (input kind 'key'), which Grading reads apart (rubric_generations,
    # answer_key_readings): a draft counts while it is the latest uncancelled draft of its template and version, a reading while it
    # is the latest uncancelled reading of its question.
    ("frq_rubric_drafts", "Rubric drafts", "frq_rubric_generations",
     "q.cancelled=0 AND (q.status!='failed' OR q.rowid=(SELECT MAX(n.rowid) FROM frq_rubric_generations n "
     "WHERE n.assignment_id=q.assignment_id AND n.cancelled=0 AND CASE WHEN COALESCE(json_extract(q.input_json,'$.kind'),'')='key' "
     "THEN COALESCE(json_extract(n.input_json,'$.kind'),'')='key' AND json_extract(n.input_json,'$.question_id') IS json_extract(q.input_json,'$.question_id') "
     "ELSE COALESCE(json_extract(n.input_json,'$.kind'),'')!='key' AND n.document_id IS q.document_id AND n.exam_version IS q.exam_version END))",
     "#grading?tab=rubrics", None),
    # An answer-area check: its question's latest (a changed input inserts a new row). A failed one no longer counts once the
    # question's areas are confirmed for its current revision and rubric on the template the check read, as the Grading page then
    # says "confirmed": Exam 2's 16 failed template readings stayed a failure on Home after every area was confirmed (2026-10-26).
    ("frq_locations", "Answer area checks", "frq_location_checks",
     "(q.status!='failed' OR q.rowid=(SELECT MAX(n.rowid) FROM frq_location_checks n WHERE n.assignment_id=q.assignment_id "
     "AND n.question_id=q.question_id)) AND NOT (q.status='failed' AND EXISTS(SELECT 1 FROM frq_assignment_questions p "
     "JOIN frq_location_reviews r ON r.assignment_id=p.assignment_id AND r.question_id=p.question_id "
     "AND r.question_revision_id=p.question_revision_id AND r.rubric_id=p.rubric_id "
     "WHERE p.assignment_id=q.assignment_id AND p.question_id=q.question_id AND r.template_id IS json_extract(q.input_json,'$.template.id')))",
     "#grading?tab=rubrics", None),
)
# Failures that already have an owner card on Home (grading_attention's per-plan failures; a scan's own row in the Inbox card):
# their queue cards would count them twice. They stay in the health table.
OWNED_FAILURES = {"frq_grading", "scans"}
# The grading-prep queues name their assignment: a current plan's failures become its own card, on the step that shows them.
PLAN_STEPS = {
    "frq_preparation": "?tab=rubrics&focus=preparations",
    "frq_rubric_drafts": "?tab=rubrics&focus=templates",
    "frq_rubric_quality": "?tab=answer-key&open=quality",
    "frq_locations": "?tab=locations",
}
# Answer-key readings share the drafts' queue; Grading shows them on the key's step, so their cards are named and linked apart.
KEY_READING = "COALESCE(json_extract(q.input_json,'$.kind'),'')='key'"
KEY_READINGS = ("Answer key readings", "?tab=answer-key")
RUNNING = {"running", "rendering", "validating", "sending"}
REVIEW = {"needs_review", "uncertain", "unconfirmed"}
# The worker writes its heartbeat when a turn starts, so one long native operation (a PDF render, a page of OCR) can keep it quiet
# for a few minutes: running rows then say "busy". Past STALLED they are no evidence of progress, since a worker that died mid-turn
# leaves its running rows behind, and Home says the worker stopped responding instead (requirement, 2026-09-24).
ACTIVE, STALLED = 120, 600


def age_seconds(value, now):
    if not value:
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return max(0, int((now - instant).total_seconds()))
    except (TypeError, ValueError):
        return None


def queue_status(db, now):
    result = []
    for key, label, table, where, href, message in QUEUE_SPECS:
        # Identifiers and predicates are fixed source constants, never input. A deleted submission counts nowhere: its Inbox
        # command not at all, and work of other queues tied to it only while the worker still has it (queued or running),
        # never as a failure or a review waiting for the instructor.
        gone = f"NOT {PRESENT.format(message)}" if message else "0"
        if key == "printing":
            gone = printing_gone(db)
        rows = list(db.execute(f"SELECT status,{gone} gone,COUNT(*) n,MIN(created_at) oldest FROM {table} q WHERE {where} GROUP BY status,gone"))
        item = {"key": key, "label": label, "href": href, "pending": 0,
                "running": 0, "failed": 0, "needs_review": 0, "oldest_waiting_at": None}
        for row in rows:
            status = row["status"]
            bucket = "running" if status in RUNNING else "needs_review" if status in REVIEW else status
            if row["gone"] and (key == "inbox" or bucket not in ("pending", "running")):
                continue
            if bucket in ("pending", "running", "failed", "needs_review"):
                item[bucket] += row["n"]
            if status == "pending" and (item["oldest_waiting_at"] is None or row["oldest"] < item["oldest_waiting_at"]):
                item["oldest_waiting_at"] = row["oldest"]
        item["oldest_waiting_seconds"] = age_seconds(item["oldest_waiting_at"], now)
        result.append(item)
    # JEV: include actual in-flight compute in restart/Apply gates. No names or
    # source details reach Home; idle responses retain the existing queue shape.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jev_calls'").fetchone():
        count = db.execute("SELECT COUNT(*) FROM jev_calls WHERE status='running' AND julianday(created_at)>julianday('now','-2 minutes')").fetchone()[0]
        if count:
            result.append({'key': 'jev', 'label': 'Conversation and evidence checks', 'href': '#follow-ups',
                           'pending': 0, 'running': count, 'failed': 0, 'needs_review': 0,
                           'oldest_waiting_at': None, 'oldest_waiting_seconds': None})
    return result


def printing_gone(db):
    """A print job of an assessment whose grades are certified no longer needs the instructor (certified_archive.py): like a
    deleted submission's work, it counts only while the worker still has it, never as a failure. The job is unchanged."""
    return "NOT " + uncertified(db, "(SELECT v.assignment_id FROM assignment_versions v WHERE v.id=q.assignment_version_id)",
                                "q.term_id", "q.created_at")


def plan_queue_counts(db, queues, plans):
    """{queue key: {(plan id, answer-key reading): {"failed": n, "needs_review": n}}}: the grading-prep queues' failed and review
    rows of the current plans, under the same predicates as queue_status. Read only for a queue that has some."""
    found = {}
    if not plans:
        return found
    marks = ",".join("?" for _ in plans)
    busy = {queue["key"] for queue in queues if queue["failed"] or queue["needs_review"]}
    statuses = ("failed", *sorted(REVIEW))
    for key, _, table, where, _, _ in QUEUE_SPECS:
        if key not in PLAN_STEPS or key not in busy:
            continue
        reading = KEY_READING if key == "frq_rubric_drafts" else "0"
        for row in db.execute(f"""SELECT q.assignment_id,{reading} reading,q.status,COUNT(*) n FROM {table} q WHERE ({where})
                AND q.status IN ({",".join("?" for _ in statuses)}) AND q.assignment_id IN ({marks})
                GROUP BY q.assignment_id,reading,q.status""", (*statuses, *plans)):
            bucket = "failed" if row["status"] == "failed" else "needs_review"
            found.setdefault(key, {}).setdefault((row["assignment_id"], bool(row["reading"])), {"failed": 0, "needs_review": 0})[bucket] += row["n"]
    return found


def plan_step(key, plan, reading):
    """(label, href) of a current plan's step that shows this queue's work."""
    return KEY_READINGS[0] if reading else None, f"#grading/{plan}{KEY_READINGS[1] if reading else PLAN_STEPS[key]}"


def plan_queue_href(queue, per_plan):
    """A grading-prep queue whose failed and review rows all belong to one current plan's step links that step, in the health
    table and in the shell's notices as on Home (journey-6: '#grading?tab=rubrics' opened the Grading list); otherwise its own href."""
    groups = per_plan.get(queue["key"], {})
    if len(groups) == 1:
        (plan, reading), counts = next(iter(groups.items()))
        if counts["failed"] == queue["failed"] and counts["needs_review"] == queue["needs_review"]:
            return plan_step(queue["key"], plan, reading)[1]
    return queue["href"]


def queue_cards(queue, plans, per_plan, printing_failure=None):
    """Home's cards for one queue: a current plan's failures and reviews on its own step (PLAN_STEPS; an answer-key reading on the
    key's step, under its own name), the rest queue-wide."""
    cards, key = [], queue["key"]
    left = {"failed": queue["failed"], "needs_review": queue["needs_review"]}
    if key == "inbox" or key == "card_mail":
        return cards  # The Inbox's own work is in its card (failures included); Response Card email is counted per card.
    for (plan, reading), counts in sorted(per_plan.get(key, {}).items(), key=lambda item: (plans.get(item[0][0], ""), *item[0])):
        named, href = plan_step(key, plan, reading)
        title, label, suffix = plans.get(plan, "Assignment"), named or queue["label"], ":key" if reading else ""
        if counts["failed"]:
            left["failed"] -= counts["failed"]
            cards.append({"key": f"queue:{key}:failed:{plan}{suffix}", "kind": "error", "label": f"{label}: failed · {title}",
                          "count": counts["failed"], "href": href, "plan_id": plan,
                          "detail": f"{plural(counts['failed'], 'item')} failed. Open {label.lower()} to see why and retry.", "verb": "Open"})
        if counts["needs_review"]:
            left["needs_review"] -= counts["needs_review"]
            cards.append({"key": f"queue:{key}:review:{plan}{suffix}", "kind": "review", "label": f"{label}: waiting for your review · {title}",
                          "count": counts["needs_review"], "href": href, "plan_id": plan,
                          "detail": f"{plural(counts['needs_review'], 'item')} {'has' if counts['needs_review'] == 1 else 'have'} an unconfirmed outcome for you to check.",
                          "verb": "Open"})
    label = queue["label"]
    if left["failed"] > 0 and key not in OWNED_FAILURES:
        # One failed print job opens itself on the Assignments' print jobs list.
        href = (f"#documents?batch={printing_failure}" if printing_failure and left["failed"] == 1 else "#documents?tab=batches&status=attention") \
            if key == "printing" else queue["href"]
        cards.append({"key": f"queue:{key}:failed", "kind": "error", "label": f"{label}: failed",
                      "count": left["failed"], "href": href,
                      "detail": f"{plural(left['failed'], 'item')} failed. Open {label.lower()} to see why and retry.", "verb": "Open"})
    if left["needs_review"] > 0:
        cards.append({"key": f"queue:{key}:review", "kind": "review", "label": f"{label}: waiting for your review",
                      "count": left["needs_review"], "href": queue["href"],
                      "detail": f"{plural(left['needs_review'], 'item')} {'has' if left['needs_review'] == 1 else 'have'} an unconfirmed outcome for you to check.",
                      "verb": "Open"})
    return cards


def has_tables(db, *names):
    found = {row[0] for row in db.execute(f"SELECT name FROM sqlite_master WHERE name IN ({','.join('?' for _ in names)})", names)}
    return found == set(names)


def issue_href(issues, query=""):
    """One Response Card issue opens that issue (on the filter that shows what is counted, when there is one); several open
    the Response Cards page's attention view."""
    return f"#response-cards/issue/{next(iter(issues))}{query}" if len(issues) == 1 else "#response-cards?attention"


def plural(count, word, many=None):
    return f"{count:,} {word if count == 1 else many or word + 's'}"


def card_attention(db):
    """Response Card work for the instructor in current terms, counted per card, never per mail row: an invitation whose
    latest delivery is unconfirmed or failed while the card is still unopened, unanswered and not replaced; submission
    receipts that were not confirmed as delivered; and answers the card flagged for review. Counts only; no recipient."""
    if not has_tables(db, "response_card_mail", "response_card_recipients", "response_card_issues", "response_card_invalidations"):
        return []
    # A certified assessment's cards no longer need the instructor (certified_archive.py); the cards themselves are unchanged.
    current = f"""JOIN response_card_issues i ON i.id=r.issue_id JOIN course_terms t ON t.id=i.term_id
        WHERE t.is_current=1 AND t.deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id)
        AND {uncertified(db, "i.assignment_id", "i.term_id", "i.created_at")}"""
    groups = {}
    for row in db.execute(f"""SELECT r.issue_id,m.status,COUNT(*) n FROM response_card_mail m
            JOIN response_card_recipients r ON r.id=m.card_id {current}
            AND m.kind='invitation' AND m.status IN ('unconfirmed','failed') AND r.status='issued' AND r.current_submission_id IS NULL
            AND m.rowid=(SELECT MAX(n.rowid) FROM response_card_mail n WHERE n.card_id=m.card_id AND n.kind='invitation')
            GROUP BY r.issue_id,m.status"""):
        groups.setdefault(("invitation", row["status"]), {})[row["issue_id"]] = row["n"]
    for row in db.execute(f"""SELECT r.issue_id,COUNT(*) n FROM response_card_mail m
            JOIN response_card_recipients r ON r.id=m.card_id {current}
            AND m.kind='confirmation' AND m.status IN ('unconfirmed','failed') GROUP BY r.issue_id"""):
        groups.setdefault(("receipt", ""), {})[row["issue_id"]] = row["n"]
    flagged = [tuple(row) for row in db.execute(f"""SELECT r.id,r.issue_id FROM response_card_recipients r {current}
            AND r.status='needs_review' ORDER BY r.issue_id,r.id""")]
    over = exam_over(db, [card_id for card_id, _ in flagged])  # RESPONSE-CARDS "NO FLAGS AFTER THE EXAM"
    for card_id, issue_id in flagged:
        if card_id not in over:
            groups.setdefault(("flagged", ""), {}).setdefault(issue_id, 0)
            groups[("flagged", "")][issue_id] += 1
    # The issue page's own filters (response-cards.js): ?status=needs_review is Flagged, ?delivery=problem is Not delivered.
    specs = (
        (("flagged", ""), "cards_flagged", "review", "Flagged card answers",
         lambda n: f"{plural(n, 'answer')} did not match what the card expected. Look at {'it' if n == 1 else 'them'} before the exam.", "Review",
         "?status=needs_review"),
        (("invitation", "unconfirmed"), "cards_unconfirmed", "check", "Card invitations with unconfirmed delivery",
         lambda n: f"{plural(n, 'invitation')} may not have arrived and {'is' if n == 1 else 'are'} still unopened. Resend if needed.", "Open",
         "?delivery=problem"),
        (("invitation", "failed"), "cards_failed", "error", "Card invitations that failed to send",
         lambda n: f"{plural(n, 'invitation')} could not be sent and {'is' if n == 1 else 'are'} still unopened. Resend if needed.", "Open",
         "?delivery=problem"),
        (("receipt", ""), "cards_receipts", "check", "Submission receipts with unconfirmed delivery",
         lambda n: f"{plural(n, 'receipt')} for submitted answers may not have arrived.", "Open", ""),
    )
    cards = []
    for group, key, kind, label, detail, verb, query in specs:
        issues = groups.get(group)
        if issues:
            count = sum(issues.values())
            cards.append({"key": key, "kind": kind, "label": label, "count": count, "href": issue_href(issues, query),
                          "detail": detail(count), "verb": verb})
    return cards


def seat_update_attention(db):
    """Seated students of a current term's chart whose updated seat card has no accepted invitation with a provider receipt
    (the chart's orange 'Seat update not sent'). Per chart: its exam and room titles, and a count."""
    if not has_tables(db, "seating_card_updates", "seating_charts", "seating_assignments", "rooms", "response_card_recipients", "response_card_mail",
                      "response_card_invalidations"):
        return []
    cards = []
    for row in db.execute(f"""SELECT c.id,a.title,rm.name room,COUNT(*) n FROM seating_card_updates u
            JOIN seating_charts c ON c.id=u.chart_id JOIN course_terms t ON t.id=c.term_id
            JOIN assignments a ON a.id=c.assignment_id JOIN rooms rm ON rm.id=c.room_id
            JOIN seating_assignments s ON s.chart_id=u.chart_id AND s.enrollment_id=u.enrollment_id AND s.seat_id=u.seat_id
            WHERE t.is_current=1 AND t.deleted_at IS NULL AND u.seat_id IS NOT NULL AND {uncertified(db, "c.assignment_id", "c.term_id", "COALESCE(u.updated_at,u.created_at)")}
            AND NOT EXISTS(SELECT 1 FROM response_card_recipients r WHERE r.id=u.card_id
                AND (r.status!='revoked' OR EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id AND x.reason='exam_over'))
                AND EXISTS(SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='invitation' AND m.status='accepted'
                           AND COALESCE(m.provider_receipt_json,'')!=''))
            GROUP BY c.id ORDER BY a.title,rm.name,c.id"""):
        cards.append({"key": "seat_updates:" + row["id"], "kind": "review", "label": f"Seat updates not sent · {row['title']} · {row['room']}",
                      "count": row["n"], "href": "#seating/" + row["id"],
                      "detail": f"{plural(row['n'], 'student')} moved to another seat and {'has' if row['n'] == 1 else 'have'} no confirmed updated card yet.",
                      "verb": "Send updates"})
    return cards


def current_grading_plans(db):
    """{grading plan id: assignment title} for the grading plans of current terms: the scope of the grading cards, which Home
    also applies to the rows and cards it derives from Grading's own overview (that lists every term's plans)."""
    if not has_tables(db, "frq_assignments", "exam_events", "course_terms", "assignments"):
        return {}
    return {row["id"]: row["title"] for row in db.execute("""SELECT f.id,a.title FROM frq_assignments f
        JOIN exam_events e ON e.id=f.event_id JOIN course_terms t ON t.id=e.term_id JOIN assignments a ON a.id=e.assignment_id
        WHERE t.is_current=1 AND t.deleted_at IS NULL""")}


def makeup_version_cards(app, db, plan_titles):
    """One card per current term's makeup draft waiting on the instructor (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED";
    requirement, 2026-11-05). `assignment_work`: its address is the assignment's own
    (#assignments/...), not one of ASSIGNMENT_BADGES', and the badge counts it as one decision (`assignmentDecisionCards`)."""
    if "frq_grading" not in app.extensions or not plan_titles or not has_tables(db, "quiz_drafts", "quiz_draft_scraps", "quiz_draft_approvals"):
        return []
    from assignment_workspace import makeup_item
    cards = []
    for plan, makeup in sorted(app.extensions["frq_grading"].makeups_waiting(db).items()):
        if plan not in plan_titles:
            continue
        pair = db.execute("SELECT e.assignment_id,e.term_id FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id WHERE f.id=?", (plan,)).fetchone()
        item, ready = makeup_item(makeup, pair[0], pair[1]), makeup["state"] == "ready"
        cards.append({"key": f"makeup:{plan}", "kind": "review", "label": f"{item['label']} · {plan_titles[plan]}", "count": 1, "href": item["href"], "assignment_work": True,
                      "detail": "Drafted and waiting for you: students' copies are made once it is approved." if ready else "The draft failed, so there is nothing to approve yet.",
                      "verb": "Approve" if ready else "Open"})
    return cards


def failed_to_grade(db, plans):
    """{plan id: responses that failed to grade} counted as the Grading page counts them (`workflow_summary`'s `failed`): the
    latest attempt failed and left no proposal, under TODAY's pinned rubric and question (a failure under an earlier one is work
    to grade again), and the response is not new work for the next batch (`reset_ungraded`: a reset left it without a proposal,
    or no batch still standing tried it under the current pins, as when its batch was cancelled). One pass over the plans' tasks,
    no subquery per response (review fix, 2026-09-26: the badge kept counting failures the Grading page files as regrades)."""
    marks = ",".join("?" for _ in plans)
    tasks = db.execute(f"""SELECT j.assignment_id,t.response_id,t.rowid,t.status,j.cancel_requested,
            t.rubric_id IS p.rubric_id AND t.question_revision_id IS p.question_revision_id current,
            r.current_result_id IS NULL no_result,r.current_review_id IS NULL no_review
        FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id JOIN frq_responses r ON r.id=t.response_id
        JOIN frq_assignment_questions p ON p.assignment_id=r.assignment_id AND p.question_id=r.question_id
        WHERE j.assignment_id IN ({marks})""", tuple(plans)).fetchall()
    reset = {row[0] for row in db.execute(f"""SELECT DISTINCT x.response_id FROM frq_job_reset_responses x JOIN frq_responses r ON r.id=x.response_id
        WHERE r.assignment_id IN ({marks})""", tuple(plans))} if has_tables(db, "frq_job_reset_responses") else set()
    latest, busy, standing = {}, set(), set()
    for row in tasks:
        rid = row["response_id"]
        if rid not in latest or row["rowid"] > latest[rid]["rowid"]:
            latest[rid] = row
        if row["status"] in ("pending", "running"):
            busy.add(rid)
        if not row["cancel_requested"] and row["current"]:
            standing.add(rid)
    counts = {}
    for rid, row in latest.items():
        if row["status"] != "failed" or not row["no_result"] or not row["current"]:
            continue
        if row["no_review"] and rid not in busy and (rid in reset or rid not in standing):
            continue  # New work for the next batch, as the Grading page counts it.
        counts[row["assignment_id"]] = counts.get(row["assignment_id"], 0) + 1
    return counts


def setup_waiting(db, plans, grading=None):
    """({plan id: True} of plans whose rubric check finished and waits for the instructor, {plan id: questions} whose suggested
    answer areas wait for a look), as the Grading page decides them (rubric_quality_status 'needs_review' for a finished check;
    location_status 'needs_confirmation' or 'needs_review'), which stays the authority:
    - the plan's latest rubric check (or, when that is a resolution that failed or was cancelled, the check it answered; when it is
      another check that failed or was cancelled, the newest finished check of the same rubrics before it) is complete and not
      cancelled; it read exactly what a check would read today, compared as the check recorded it (the digest
      of `grading.rubric_quality_context`: a rubric edit, a line comment or a changed maximum makes it stale, while an answer key,
      a confirmed answer area or a chosen template, which a check does not read, leave it waiting); every question has a rubric;
      and the plan's standing confirmation does not cover that input. Only a plan whose latest check finished and was never
      confirmed is digested, so a read usually digests none. Without the grading service there is no finalization card;
    - a question's latest answer-area check is complete, was made for its current question and rubric revisions, and no
      confirmation of those revisions on that check's template exists."""
    finalization, areas = {}, {}
    marks = ",".join("?" for _ in plans)
    if grading is not None and has_tables(db, "frq_rubric_quality_checks", "frq_rubric_quality_reviews", "frq_rubric_quality_options"):
        # A resolution's own input is read only when it failed or was cancelled (then it is the check it answered that counts).
        latest = db.execute(f"""SELECT c.assignment_id,c.id,c.status,c.cancelled,c.input_sha256,
                CASE WHEN c.cancelled=1 OR c.status='failed' THEN json_extract(c.input_json,'$.resolves.check_id') END resolves
            FROM frq_rubric_quality_checks c WHERE c.rowid IN (SELECT MAX(n.rowid) FROM frq_rubric_quality_checks n
                WHERE n.assignment_id IN ({marks}) GROUP BY n.assignment_id)""", tuple(plans)).fetchall()
        for row in latest:
            check, plan = row, row["assignment_id"]
            if row["resolves"]:
                check = db.execute("SELECT id,status,cancelled,input_sha256 FROM frq_rubric_quality_checks WHERE id=?", (row["resolves"],)).fetchone() or row
            elif row["cancelled"] or row["status"] == "failed":  # A re-run that never finished leaves the finished check waiting (2026-10-05).
                check = db.execute("""SELECT id,status,cancelled,input_sha256 FROM frq_rubric_quality_checks WHERE assignment_id=? AND input_sha256=?
                    AND rowid<(SELECT rowid FROM frq_rubric_quality_checks WHERE id=?) AND status='complete' AND cancelled=0 ORDER BY rowid DESC LIMIT 1""",
                    (plan, row["input_sha256"], row["id"])).fetchone() or row
            if check["status"] != "complete" or check["cancelled"]:
                continue
            if db.execute("""SELECT 1 FROM frq_rubric_quality_options o JOIN frq_rubric_quality_reviews r ON r.id=o.review_id
                    WHERE o.assignment_id=? AND r.input_sha256=?""", (plan, check["input_sha256"])).fetchone():
                continue  # Confirmed (or, for a confirmation from before 2026-09-21, the Grading page's own business).
            try:
                context, missing = grading.rubric_quality_context(db, plan)
            except (HTTPException, LookupError, ValueError, TypeError):
                continue  # Home never fails over one plan's setup; the Grading page says what is wrong with it.
            if not missing and context["questions"] and digest(context) == check["input_sha256"]:
                finalization[plan] = True
    if has_tables(db, "frq_location_checks", "frq_location_reviews"):
        for row in db.execute(f"""SELECT p.assignment_id,COUNT(*) FROM frq_assignment_questions p
                JOIN frq_location_checks c ON c.rowid=(SELECT MAX(n.rowid) FROM frq_location_checks n WHERE n.assignment_id=p.assignment_id AND n.question_id=p.question_id)
                WHERE p.assignment_id IN ({marks}) AND p.rubric_id IS NOT NULL AND c.status='complete'
                AND json_extract(c.input_json,'$.rubric_id') IS p.rubric_id AND json_extract(c.input_json,'$.question_revision_id') IS p.question_revision_id
                AND NOT EXISTS(SELECT 1 FROM frq_location_reviews r WHERE r.assignment_id=p.assignment_id AND r.question_id=p.question_id
                    AND r.question_revision_id=p.question_revision_id AND r.rubric_id=p.rubric_id AND r.template_id IS json_extract(c.input_json,'$.template.id'))
                GROUP BY p.assignment_id""", tuple(plans)):
            areas[row[0]] = row[1]
    return finalization, areas


def grading_attention(db, plans, grading=None):
    """Light counts of grading work for the instructor in current terms (amendment 18: the shell's Grading badge must not
    depend on which pages were opened). Per grading plan: responses whose current proposal or decision waits for review
    under today's pinned rubric and question revisions (an active automatic acceptance is final; a decision under an
    earlier rubric gives way to a current proposal, as the grading listing says), responses that failed to grade, counted
    as the Grading page counts them (`failed_to_grade`), a finished rubric check waiting for review (the Grading page's own rule,
    read with `grading`, the grading service) and suggested answer areas waiting for a look (`setup_waiting`; grading-setup-5,
    2026-10-02: a check finished while the instructor was elsewhere raised nothing). Cards are `grading:<plan>:review|failed|finalization|areas`. These are catalog counts, not the grading workflow: a
    changed paper or a finalization held back by a newer rubric confirmation is only known to the Grading page, which stays the
    authority, and Home replaces these cards with its own rows once it has read it. Version decisions need the roster and the
    seating charts, so only the Grading page and Home (from it) count them. The caller passes the current plans
    (`current_grading_plans`)."""
    if not has_tables(db, "frq_assignments", "frq_responses", "frq_assignment_questions", "frq_results", "frq_reviews", "frq_tasks", "frq_jobs"):
        return []
    if not plans:
        return []
    marks = ",".join("?" for _ in plans)
    accepted = ("EXISTS(SELECT 1 FROM frq_all_acceptances acc WHERE acc.result_id=x.id AND acc.kind!='trial' AND acc.active)"
                if has_tables(db, "frq_all_acceptances") else "0")
    waiting = {row[0]: row[1] for row in db.execute(f"""SELECT r.assignment_id,SUM(CASE
            WHEN v.id IS NOT NULL AND v.rubric_id=p.rubric_id AND rt.question_revision_id=p.question_revision_id
                THEN v.status NOT IN ('approved','manual')
            WHEN x.id IS NOT NULL AND xt.rubric_id=p.rubric_id AND xt.question_revision_id=p.question_revision_id
                THEN NOT {accepted}
            ELSE 0 END)
        FROM frq_responses r JOIN frq_assignment_questions p ON p.assignment_id=r.assignment_id AND p.question_id=r.question_id
        LEFT JOIN frq_reviews v ON v.id=r.current_review_id LEFT JOIN frq_results rx ON rx.id=v.result_id LEFT JOIN frq_tasks rt ON rt.id=rx.task_id
        LEFT JOIN frq_results x ON x.id=r.current_result_id LEFT JOIN frq_tasks xt ON xt.id=x.task_id
        WHERE r.assignment_id IN ({marks}) GROUP BY r.assignment_id""", tuple(plans))}
    failed = failed_to_grade(db, plans)
    finalization, areas = setup_waiting(db, plans, grading)
    cards = []
    # ONE SET OF WORDS (2026-10-18): each card is named with its assignment's needs-you label (assignment_workspace.work_items: "Review
    # grades", "Inspect failed grading", "Check answer areas and templates"), the words Home, the tabs and the index use.
    for plan, title in sorted(plans.items(), key=lambda item: (item[1], item[0])):
        if waiting.get(plan):
            n = waiting[plan]
            cards.append({"key": f"grading:{plan}:review", "kind": "review", "label": f"Review grades · {title}",
                          "count": n, "href": f"#grading/{plan}?tab=review", "plan_id": plan,
                          "detail": f"{plural(n, 'graded response')} {'waits' if n == 1 else 'wait'} for your decision.", "verb": "Review"})
        if failed.get(plan):
            n = failed[plan]
            cards.append({"key": f"grading:{plan}:failed", "kind": "error", "label": f"Inspect failed grading · {title}",
                          "count": n, "href": f"#grading/{plan}?tab=review&open=failed", "plan_id": plan,
                          "detail": f"{plural(n, 'response')} {'has' if n == 1 else 'have'} no grade because the last attempt failed.", "verb": "Inspect"})
        if finalization.get(plan):
            cards.append({"key": f"grading:{plan}:finalization", "kind": "review", "label": f"Review the rubric check · {title}",
                          "count": 1, "href": f"#grading/{plan}?tab=answer-key&open=quality", "plan_id": plan,
                          "detail": "The rubric check finished. Review and approve it before grading.", "verb": "Review"})
        if areas.get(plan):
            n = areas[plan]
            cards.append({"key": f"grading:{plan}:areas", "kind": "review", "label": f"Check answer areas and templates · {title}",
                          "count": n, "href": f"#grading/{plan}?tab=locations", "plan_id": plan,
                          "detail": f"The suggested answer areas of {plural(n, 'question')} wait for your look.", "verb": "Check"})
    return cards


def storage_status(storage):
    empty_backup = {"status": "disabled", "started_at": None, "finished_at": None,
                    "verified_at": None, "error_count": 0}
    result = {"enabled": bool(storage), "state": "disabled", "pending_count": 0,
              "issue_count": 0, "backup": empty_backup, "disks": []}
    if storage is None:
        return result
    try:
        status = storage.status()
    except (StorageError, OSError, ValueError):
        # Do not leak filesystem paths or raw backup/owner errors on Home.
        result.update(state="unavailable", issue_count=1)
        result["backup"] = {**empty_backup, "status": "unavailable"}
        return result
    backup = status.get("last_backup", {})
    result.update(state="attention" if status.get("issues") else "ready",
                  pending_count=status.get("pending_count", 0), issue_count=len(status.get("issues", [])))
    result["backup"] = {"status": backup.get("status", "not_run"),
                        "started_at": backup.get("started_at"), "finished_at": backup.get("finished_at"),
                        # Only the complete coordinated run attests to both
                        # payload verification and a verified catalog snapshot.
                        "verified_at": backup.get("finished_at") if backup.get("status") == "complete" else None,
                        "error_count": len(backup.get("errors", []))}
    for label, path, check in (("Working storage", storage.nvme, storage.check_nvme),
                               ("Backup storage", storage.hdd, storage.check_hdd)):
        disk = {"label": label, "state": "unavailable", "total_bytes": None,
                "used_bytes": None, "free_bytes": None, "free_percent": None}
        try:
            check()  # Never report the root filesystem as an unmounted drive.
            usage = shutil.disk_usage(path)
            free_percent = round(100 * usage.free / usage.total, 1) if usage.total else 0
            disk.update(total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free,
                        free_percent=free_percent, state="low" if free_percent < 10 else "ready")
        except (StorageError, OSError):
            pass
        result["disks"].append(disk)
    return result


CURRENT = ("failed", "needs_review", "running", "pending")  # The Inbox's Current view, in its order.
NEEDS_YOU = ("failed", "needs_review")
CURRENT_SCANS, RECENT_SCANS, FINISHED_DAYS = 8, 6, 7


def inbox_summary(db, inbox, scans, now):
    """The Inbox as the Inbox files it (InboxService.filed): present submissions only, scans by their own summary.

    ScanService.overview runs for each present scan stored as needs_review (what decides its filing, and what the Inbox
    list computes on every poll), plus at most the few shown scans stored otherwise."""
    rows = [{**dict(row), "gone": False} for row in db.execute(f"""SELECT j.id,j.status,j.action,j.message_id,j.created_at
        FROM inbox_jobs j WHERE {PRESENT.format('j.message_id')} ORDER BY j.created_at DESC,j.id DESC""")]
    memo = {}  # this read's booklet versions, shared by every scan's overview (PERFORMANCE)
    filing = inbox.filed(db, rows, scans="review", memo=memo)
    cache = {identifier: overview for identifier, (_, overview) in filing.items() if overview is not None}

    def overview_of(row):
        if row["id"] not in cache:
            cache[row["id"]] = scans.overview(db, row["message_id"], memo=memo)
        return cache[row["id"]]

    scan_rows = [row for row in rows if row["action"] == "scan.receive"]
    listed = lambda row: filing[row["id"]][0]
    need = {"scans": sum(listed(row) in NEEDS_YOU for row in scan_rows),
            "submissions": sum(listed(row) in NEEDS_YOU for row in rows if row["action"] != "scan.receive")}
    recent = [{"id": row["id"], "message_id": row["message_id"], "created_at": row["created_at"], "status": row["status"],
               "href": "#inbox/job/" + row["id"], "scan": overview_of(row)} for row in scan_rows[:RECENT_SCANS]]
    current = sorted((row for row in scan_rows if listed(row) in CURRENT), key=lambda row: CURRENT.index(listed(row)))
    shown = []
    for row in current[:CURRENT_SCANS]:
        decode = db.execute("SELECT pages_total FROM document_decode_jobs WHERE message_id=?", (row["message_id"],)).fetchone()
        done = db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (row["message_id"],)).fetchone()[0]
        shown.append({"id": row["id"], "href": "#inbox/job/" + row["id"], "created_at": row["created_at"], "listed_as": listed(row),
                      "pages": {"done": done, "total": decode["pages_total"] if decode else None}, "scan": overview_of(row)})
    ages = {row["id"]: age_seconds(row["created_at"], now) for row in scan_rows}
    recent_done = [row for row in scan_rows if listed(row) == "completed" and ages[row["id"]] is not None
                   and ages[row["id"]] <= FINISHED_DAYS * 86400]
    finished = len(recent_done)
    # The Inbox lists a finished scan whose assessment's grades are certified under Archived, not History (certified_archive.py).
    archived = len(certified_messages(db, {row["message_id"] for row in recent_done})) if recent_done else 0
    need["submissions_failed"] = sum(listed(row) == "failed" for row in rows if row["action"] != "scan.receive")
    # The health table's Inbox row, filed the same way (review W2-HOME-D1): Review and Failed are exactly the attention card's
    # count, and Queued/Running are the present jobs the Inbox lists there (a scan whose next step is the worker's included).
    filed = {"pending": 0, "running": 0, "failed": 0, "needs_review": 0, "oldest_waiting_at": None}
    for row in rows:
        bucket = listed(row)
        if bucket in ("pending", "running", "failed", "needs_review"):
            filed[bucket] += 1
        if bucket == "pending" and (filed["oldest_waiting_at"] is None or row["created_at"] < filed["oldest_waiting_at"]):
            filed["oldest_waiting_at"] = row["created_at"]
    return need, recent, shown, len(current), finished, archived, filed


def overview(app):
    now = datetime.now(timezone.utc)
    # Exam dates are campus wall-clock values, just as in Response Card mail.
    # UTC midnight must not hide an exam still taking place this evening.
    campus_today = now.astimezone(CAMPUS_ZONE).date().isoformat()
    inbox = app.extensions["inbox"]
    scans = app.extensions["document_scans"]
    with connect(inbox.path) as db:
        db.execute("BEGIN")  # One read snapshot; never claim or execute work.
        queues = queue_status(db, now)
        need, recent, current, current_total, finished, archived, filed = inbox_summary(db, inbox, scans, now)
        stored = next(q for q in queues if q["key"] == "inbox")
        # Queued and running TOTALS stay the work the worker holds (stored statuses): a scan filed Running waits for work that is
        # already counted in its own queue (inspection, bubble reading ...), and must not look like a turn that stopped reporting.
        totals = {key: sum(q[key] for q in queues) for key in ("pending", "running")}
        stored.update(filed)
        stored["oldest_waiting_seconds"] = age_seconds(stored["oldest_waiting_at"], now)
        totals.update({key: sum(q[key] for q in queues) for key in ("failed", "needs_review")})
        row = db.execute("SELECT heartbeat_at FROM inbox_worker WHERE id=1").fetchone()
        heartbeat = row[0] if row else None
        age = age_seconds(heartbeat, now)
        worker = {"heartbeat_at": heartbeat, "age_seconds": age,
                  "state": "unknown" if age is None else "active" if age < ACTIVE else "stale" if not totals["running"]
                  else "busy" if age < STALLED else "stalled"}
        upcoming = []
        for row in db.execute("""SELECT e.id,e.title,t.code,t.term,t.term_other,t.year,
                s.id sitting_id,s.label,s.date,s.time,s.location
            FROM exam_sittings s JOIN exam_events e ON e.id=s.event_id
            JOIN course_terms t ON t.id=e.term_id
            WHERE t.is_current=1 AND t.deleted_at IS NULL AND s.date>=?
            ORDER BY s.date,s.time,t.code,e.title,s.id LIMIT 8""", (campus_today,)):
            item = dict(row)
            term_name = item["term_other"] if item["term"] == "Other" else item["term"]
            item["term_label"] = f"{term_name} {item['year']}"
            for field in ("term", "term_other", "year"):
                item.pop(field)
            item["href"] = "#exams/" + item["id"]
            upcoming.append(item)
        request_events = db.execute("""SELECT r.event_id,COUNT(*) FROM exam_requests r JOIN exam_events e ON e.id=r.event_id
            JOIN course_terms t ON t.id=e.term_id WHERE r.status IN ('open','in_review')
            AND t.is_current=1 AND t.deleted_at IS NULL GROUP BY r.event_id ORDER BY r.event_id""").fetchall()
        requests = sum(row[1] for row in request_events)
        # One exam's requests open on its Requests tab; several open the list of exam records (a bare #exams is Assignments).
        requests_href = f"#exams/{request_events[0][0]}?tab=requests" if len(request_events) == 1 else "#exams?view=list"
        plan_titles = current_grading_plans(db)
        grading = grading_attention(db, plan_titles, app.extensions.get("frq_grading"))
        grading_plans = sorted(plan_titles)
        per_plan = plan_queue_counts(db, queues, plan_titles)
        printing = next(q for q in queues if q["key"] == "printing")
        printing_failure = None
        if printing["failed"] == 1:
            row = db.execute(f"SELECT q.id FROM print_batches q WHERE q.status='failed' AND NOT ({printing_gone(db)}) LIMIT 2").fetchall()
            printing_failure = row[0]["id"] if len(row) == 1 else None
        cards = card_attention(db)
        seat_updates = seat_update_attention(db)
        absences = app.extensions["absences"].waiting(db) if "absences" in app.extensions else 0
        overdue = app.extensions["absences"].past_deadline_documented(db) if absences else 0
        makeups = app.extensions["absences"].makeups_attention(db) if "absences" in app.extensions else None
        # SCHEDULING "SEND TO AIM" (2026-10-27): DR quizzes a Send would act on now, and AIM writes waiting for Retry.
        from scheduling import aim_send, clock
        aim_due = aim_send.attention(db, clock.campus_today().isoformat())
        # QUIZZES "SHARED STAMPS" (2026-11-04): a stamp back on two papers, where the approval said each student uses their own copy.
        from document_identity import photocopies
        shared_stamps = photocopies.attention(db)
        makeup_versions = makeup_version_cards(app, db, plan_titles)
        # MAIL-SEND-LATER (2026-11-07): a message sent later that was held, or whose send failed or could not be confirmed.
        later = app.extensions["scheduled_sends"].attention(db) if "scheduled_sends" in app.extensions and has_tables(db, "scheduled_sends") else 0
        # ASSISTANT "HOME CARD" (2026-10-09): the Approve cards waiting in the Assistant's Changes, counted by the assistant's own rule.
        assistant_changes = 0
        if "assistant" in app.extensions:
            from assistant.automation import changes_waiting
            assistant_changes = changes_waiting(db)
    storage = storage_status(app.extensions.get("course_storage"))
    attention = []
    waiting = need["scans"] + need["submissions"]
    if waiting:
        # Counted the way the Inbox files its Current view (PRESENT, listed_as), so the sidebar badge may show it.
        parts = [plural(need["scans"], "scan") if need["scans"] else "", plural(need["submissions"], "submission") if need["submissions"] else ""]
        attention.append({"key": "inbox", "kind": "review", "label": "Scans and submissions that need you", "count": waiting, "href": "#inbox",
                          "derived": True, "scans": need["scans"], "submissions": need["submissions"],
                          "submissions_failed": need["submissions_failed"],
                          "detail": f"{' and '.join(part for part in parts if part)} in the Inbox need{'s' if waiting == 1 else ''} your review.",
                          "verb": "Open Inbox"})
    attention += grading
    if requests:
        attention.append({"key": "exam_requests", "kind": "review", "label": "Exam requests to review", "count": requests, "href": requests_href,
                          "detail": f"{plural(requests, 'student request')} about seats, schedules or notices {'is' if requests == 1 else 'are'} open.",
                          "verb": "Review"})
    attention += cards + seat_updates
    # Gradescope regrade requests are Follow-up conversations (REGRADES "FOLLOW-UP", requirement, 2026-10-07): its
    # Needs-reply card counts them; Home has no regrade card of its own.
    if "student_support" in app.extensions:
        support = app.extensions["student_support"].attention()
        for key, label, href, detail in (
            # JEV WELLBEING: a count and a link only; never a name, subject or message (FRONTEND "Home").
            ("wellbeing", "Possible wellbeing concerns", "#follow-ups?filter=wellbeing", "Student messages that may describe a wellbeing concern."),
            ("followup", "Student follow-up", "#follow-ups", "Captured student conversations, follow-ups due and suggestions waiting for you."),
        ):
            if support[key]:
                # NEEDS ME (2026-10-06): a wellbeing concern is a follow-up that needs attention, so the Student follow-up card
                # already counts it; `counted_in` keeps the sidebar badge, the tab title and Home's total from counting it twice.
                attention.append({"key": "support:" + key, "kind": "review", "label": label, "count": support[key],
                                  "href": href, "detail": detail, "verb": "Review",
                                  **({"counted_in": "support:followup"} if key == "wellbeing" else {})})
    # RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT" (requirement, 2026-11-14): the recordings Faculty could not tell belong to their teaching. A count and a
    # link only, never a title or a time; the answer is given on the Recordings page.
    if "recordings" in app.extensions:
        unsure = app.extensions["recordings"].unsure()
        if unsure:
            attention.append({"key": "recordings:keep", "kind": "review", "label": "Recordings to keep or not", "count": unsure,
                              "href": "#recordings",
                              "detail": f"Faculty could not tell whether {plural(unsure, 'recording')} {'belongs' if unsure == 1 else 'belong'} "
                                        f"to your teaching. Nothing of {'it' if unsure == 1 else 'them'} is fetched until you say.",
                              "verb": "Decide"})
    if aim_due:
        parts = [plural(aim_due["count"], "DR quiz", "DR quizzes") + " to send to AIM" if aim_due["count"] else "",
                 plural(aim_due["held"], "AIM write") + " waiting for Retry" if aim_due["held"] else ""]
        attention.append({"key": "aim_send", "kind": "review", "label": "Send to AIM" if aim_due["count"] else "AIM writes to check",
                          "count": aim_due["count"] + aim_due["held"], "href": aim_due["href"],
                          "detail": "; ".join(part for part in parts if part) + "." + (" The last send did not get through (Duo or AIM)."
                                                                                    if aim_due["waiting"] else ""),
                          "verb": "Send" if aim_due["count"] else "Check"})
    attention += makeup_versions
    if later:
        attention.append({"key": "scheduled_sends", "kind": "review", "label": "Scheduled messages that need you", "count": later,
                          "href": "#mail?scheduled=1",
                          "detail": f"{plural(later, 'message')} you scheduled {'was' if later == 1 else 'were'} not sent on time, or "
                                    "the send did not go through.", "verb": "Open"})
    if shared_stamps:
        from assignment_workspace import workspace_href
        attention.append({"key": "shared_stamps", "kind": "review", "label": "Stamps back on two papers", "count": shared_stamps["count"],
                          "href": workspace_href(shared_stamps["assignment"], shared_stamps["term"]) if shared_stamps["assignment"] else "#assignments",
                          "detail": "A copy's stamp came back on more than one student's paper, on an assignment each student must do on their own copy.",
                          "verb": "Check"})
    if absences:
        # ABSENCES DECISION DEADLINE: documented ones still undecided after the third business day lead the card (one card, so the
        # sidebar badge counts each absence once).
        late = (f"{plural(overdue, 'documented absence')} {'is' if overdue == 1 else 'are'} past the third business day after the "
                f"absence. ") if overdue else ""
        attention.append({"key": "absences", "kind": "review", "label": "Absences past their deadline" if overdue else "Absences waiting for your decision",
                          "count": absences, "href": "#absences?status=pending", **({"past_deadline": overdue} if overdue else {}),
                          "detail": late + f"{plural(absences, 'absence')} of current courses {'waits' if absences == 1 else 'wait'} for you to excuse or not.",
                          "verb": "Decide"})
    if makeups and "parts" not in makeups:
        # ABSENCES "MAKEUPS": an excused absence covers the day an assignment was given and no paper of it has come back.
        count = makeups["count"]
        attention.append({"key": "makeups", "kind": "review", "label": "Makeups owed", "count": count, "href": "#absences?view=makeups",
                          "detail": f"{plural(count, 'excused absence')} {'covers' if count == 1 else 'cover'} the day an assignment was given, "
                                    f"and no paper of it has come back.", "verb": "Plan"})
    elif makeups:
        # ABSENCES "MAKEUP BOOKING": quiz makeups book themselves by card; only what needs the instructor is counted (no names).
        parts = makeups["parts"]
        said = [text for key, text in (
            ("flags", plural(parts["flags"], "booking") + " to look at"),
            ("finalize", plural(parts["finalize"], "makeup version") + "'s rubrics to finalize" if parts["finalize"] == 1 else
             plural(parts["finalize"], "makeup version") + "' rubrics to finalize"),
            ("print", plural(parts["print"], "named copy", "named copies") + " to print"), ("owed", plural(parts["owed"], "makeup") + " owed")) if parts[key]]
        attention.append({"key": "makeups", "kind": "review", "label": "Makeups that need you", "count": makeups["count"],
                          "href": "#absences?view=makeups", "parts": parts, "detail": "; ".join(said) + ".", "verb": "Open"})
    if worker["state"] == "stalled":
        attention.append({"key": "worker", "kind": "check", "label": "Work may be stuck: the worker stopped responding",
                          "count": totals["running"] + totals["pending"], "href": "#overview?operations",
                          "detail": "Running work has not reported for over ten minutes. Check the worker service.", "verb": "Check health"})
    elif totals["pending"] and worker["state"] in ("unknown", "stale"):
        attention.append({"key": "worker", "kind": "check", "label": "Queued work is waiting for the worker",
                          "count": totals["pending"], "href": "#overview?operations",
                          "detail": "Nothing has picked up queued work recently. Check that the worker service is running.", "verb": "Check health"})
    for queue in queues:
        if queue["key"] in PLAN_STEPS:
            queue["href"] = plan_queue_href(queue, per_plan)
        attention += queue_cards(queue, plan_titles, per_plan, printing_failure)
    if storage["enabled"] and (storage["issue_count"] or storage["backup"]["status"] in ("failed", "interrupted", "unavailable", "not_run")):
        attention.append({"key": "storage", "kind": "error", "label": "Check storage and backup", "count": max(1, storage["issue_count"]),
                          "href": "#overview?operations", "detail": "The last coordinated backup is not verified, or storage reports a problem.",
                          "verb": "Check health"})
    if any(disk["state"] in ("low", "unavailable") for disk in storage["disks"]):
        attention.append({"key": "disk", "kind": "error", "label": "Check available disk space",
                          "count": sum(disk["state"] != "ready" for disk in storage["disks"]), "href": "#overview?operations",
                          "detail": "A drive is running low or cannot be reached.", "verb": "Check health"})
    return {"generated_at": timestamp(), "attention": attention, "upcoming_exams": upcoming,
            "recent_scans": recent, "current_scans": current, "current_scans_total": current_total, "finished_recent": finished,
            "finished_recent_archived": archived, "current_grading_plans": grading_plans,
            **({"assistant_changes": assistant_changes} if assistant_changes else {}),
            "operations": {"worker": worker, "queues": queues, "totals": totals, "storage": storage}}


def register(app):
    api = Blueprint("workspace_overview", __name__)

    @api.get("/api/workspace/overview")
    def get_overview():
        return jsonify(overview(app))

    app.register_blueprint(api)
