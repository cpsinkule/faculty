"""The life of one assessment in one term, read from the records each stage's owner keeps (UX overhaul, 2026-09-26).

An assessment is a course-wide assignment used in one teaching term. Exam events are often created after the exam (scan
association in grading makes one), so the projection is keyed by (assignment_id, term_id); an exam event, when one exists,
only adds what it owns (sittings, collection, the grading plan hanging off it).

Paper -> Seating -> Notices -> Print -> Exam day -> Collected -> Grade -> Return (keys `grading`, `recorded`; the labels are the
assignment's tab words, 2026-10-18). Each stage names its owner's page.
Everything here READS, in one snapshot, and stores, decides, sends or clears nothing: no student name, UIN or enrollment
ID leaves this module, only counts, dates, room names and the owners' own record IDs for links. Scans are read only through
PRESENT (a deleted Inbox submission counts nowhere). Paper, Seating and Print follow the shared rules of amendment 15
(`assignment_stages`), so the Assignments cards and this hub never disagree.
"""
import json
import logging
import threading
import time
from datetime import date, datetime, timezone
from urllib.parse import quote
from scheduling.clock import CAMPUS_ZONE

from document_identity.origins import is_registration
from response_cards.service import delivery_problem
from rosters import connect
from seating import term_label
from seating_versions import occupied_rows
from teach_inbox.service import PRESENT


CAMPUS = CAMPUS_ZONE
STAGES = (("paper", "Paper"), ("seating", "Seating"), ("notices", "Notices"), ("print", "Print"),
          ("exam_day", "Exam day"), ("collected", "Collected"), ("grading", "Grade"), ("recorded", "Return"))
RESPONDED = ("submitted", "verified", "needs_review")
AWAITING = ("issued", "opened", "started")
# Response Card issues the Exams page itself prepares after the exam (student requests, their decisions, released feedback)
# are not pre-exam notices; their responses become requests on the exam's Requests tab.
COMMUNICATIONS = "SELECT issue_id FROM exam_communication_issues"
# The Grading stage repeats what the Grading page counts (`workflow_summary`, which reads the term's roster payload). The hub and
# the Exams list read it at most this often per plan, like Home's grading rows; the Grading page stays the authority.
GRADING_EVERY = 120
# The Teaching Inbox opens its Upload scanned assignment window once from this link (`#inbox?upload=1`), then drops the query.
UPLOAD = "#inbox?upload=1"
log = logging.getLogger(__name__)


def campus_today():
    """Today's date on the campus wall clock, like every exam date Faculty keeps."""
    return datetime.now(timezone.utc).astimezone(CAMPUS).date().isoformat()


def wall_date(iso):
    """'Thu, Oct 8' for a campus ISO date; anything unreadable as it came."""
    try:
        day = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{day:%a}, {day:%b} {day.day}"


def plural(count, word, many=None):
    return f"{count:,} {word if count == 1 else (many or word + 's')}"


def assignment_stages(versions, charts, batches):
    """Amendment 15's shared rules for one assignment in one term: {'paper'|'seating'|'print': 'idle'|'started'|'done'|'failed'}.

    versions: how many assignment versions are registered (real PDFs or stamps-only). charts: [{confirmed: bool}] of the term's
    seating charts for the assignment. batches: [{status, superseded_at, request_id}] of its section print batches in the term;
    registration jobs (`unlabeled:`), superseded and cancelled batches never count.
    """
    current = [b for b in batches if not is_registration(b["request_id"]) and not b["superseded_at"] and b["status"] != "cancelled"]
    paper = "done" if versions else "idle"
    if charts:
        confirmed = sum(1 for c in charts if c["confirmed"])
        seating = "done" if confirmed == len(charts) else "started"
    else:
        seating = "done" if current else "idle"
    if any(b["status"] == "failed" for b in current):
        printing = "failed"
    elif any(b["status"] in ("pending", "rendering", "validating") for b in current):
        printing = "started"
    elif any(b["status"] == "ready" for b in current):
        printing = "done"
    else:
        printing = "idle"
    return {"paper": paper, "seating": seating, "print": printing}


def _issue_counts(db, issue, now):
    """Counts of one Response Card issue's live cards, by the issue listing's own expiry rule (`_issue_record`)."""
    cutoff = issue["expires_at"] or (issue["due_at"] if issue["policy"] == "until_due" else None)
    expired = bool(cutoff) and datetime.fromisoformat(cutoff.replace("Z", "+00:00")) <= now
    counts = dict.fromkeys(("total", "responded", "flagged", "awaiting", "unpublished", "delivery_problem", "queued"), 0)
    for card in db.execute("""SELECT r.status,r.publish_status,r.delivery_status,r.current_submission_id,
            EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id) invalidated
            FROM response_card_recipients r WHERE r.issue_id=?""", (issue["id"],)):
        # Response Cards' own rule (its issue page and attention strip, and Home), on the stored status.
        counts["delivery_problem"] += delivery_problem(card, card["invalidated"])
        status = "expired" if expired and card["status"] in AWAITING else card["status"]
        if status in ("revoked", "expired"):
            continue
        counts["total"] += 1
        counts["responded"] += status in RESPONDED
        counts["flagged"] += status == "needs_review"
        counts["awaiting"] += status in AWAITING
        counts["unpublished"] += card["publish_status"] != "published"
        counts["queued"] += card["delivery_status"] == "queued"
    return counts


def _chart_versions(db, chart):
    """Occupied usable seats of a chart without an exam letter, and consecutive seats of one row with the same letter
    (seating_versions.snapshot's rule), read without any student record."""
    if not chart["exam_version_count"]:
        return {"missing": 0, "conflicts": 0}
    room = db.execute("SELECT layout_json FROM rooms WHERE id=?", (chart["room_id"],)).fetchone()
    seats = [{"id": s["id"], "label": s["label"], "row": s["grid_row"], "column": s["grid_column"], "row_label": s["row_label"],
              "group": s["group_label"], "seat_index": s["seat_index"]}
             for s in db.execute("SELECT * FROM room_seats WHERE room_id=?", (chart["room_id"],))]
    occupied = {row[0]: True for row in db.execute("""SELECT a.seat_id FROM seating_assignments a JOIN room_seats s ON s.id=a.seat_id
        WHERE a.chart_id=? AND s.broken=0 AND s.blocked=0""", (chart["id"],))}
    letters = {row[0]: row[1] for row in db.execute("SELECT seat_id,exam_version FROM seating_exam_versions WHERE chart_id=?", (chart["id"],))}
    allowed = {chr(65 + index) for index in range(chart["exam_version_count"])}
    missing = conflicts = 0
    for group in occupied_rows({"layout": json.loads(room[0] or "{}") if room else {}, "seats": seats}, occupied):
        previous = None
        for seat in group["seats"]:
            letter = letters.get(seat["id"]) if letters.get(seat["id"]) in allowed else None
            missing += letter is None
            if letter and chart["exam_version_count"] > 1 and previous == letter:
                conflicts += 1
            previous = letter
    return {"missing": missing, "conflicts": conflicts}


def _stage(key, state, detail, href=None, counts=None):
    label = dict(STAGES)[key]
    return {"key": key, "label": label, "state": state, "detail": detail, "href": href, "counts": counts or {}}


def lifecycle(db, assignment_id, term_id, *, event=None, collection=None, grading=None, today=None, seating):
    """One assessment's stages, next step, pre-exam readiness and dates, from one read-only catalog snapshot.

    event: the exam event row (dict) when one exists. collection: that event's `ExamService._collection` result (its `counts` and
    `unassigned_received_count` are used). grading: `grading_summary(...)` of the event's grading plan, or None. seating: the
    SeatingService, whose `_check` gives each chart's live issue counts (the same SQL comparison the chart page shows). today: a
    campus ISO date for tests.
    """
    today = today or campus_today()
    now = datetime.now(timezone.utc)
    assignment = db.execute("""SELECT a.id,a.title,a.definition_id,d.code FROM assignments a JOIN course_definitions d ON d.id=a.definition_id
        WHERE a.id=?""", (assignment_id,)).fetchone()
    term = db.execute("SELECT * FROM course_terms WHERE id=?", (term_id,)).fetchone()
    if not assignment or not term:
        return None
    versions = db.execute("SELECT COUNT(*) FROM assignment_versions WHERE assignment_id=?", (assignment_id,)).fetchone()[0]
    charts = [dict(row) for row in db.execute("""SELECT c.id,c.room_id,r.name room_name,c.exam_date,c.exam_time,c.confirmed_at,
        c.exam_version_count,c.version FROM seating_charts c JOIN rooms r ON r.id=c.room_id
        WHERE c.term_id=? AND c.assignment_id=? ORDER BY r.name,c.id""", (term_id, assignment_id))]
    batches = [dict(row) for row in db.execute("""SELECT b.id,b.status,b.superseded_at,b.request_id,b.seating_chart_id,b.section_id,
        b.quantity,b.assignment_version_id FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
        WHERE v.assignment_id=? AND b.term_id=? ORDER BY b.created_at,b.id""", (assignment_id, term_id))]
    current = [b for b in batches if not is_registration(b["request_id"]) and not b["superseded_at"] and b["status"] != "cancelled"]
    shared = assignment_stages(versions, [{"confirmed": bool(c["confirmed_at"])} for c in charts], batches)
    # Printed without a seating chart since document_identity migration 013 (DOCUMENTS 'TERM PRINT JOBS'): one term-owned job per
    # version, its sections listed by reference, so the versions are one stack for every section, never a section's own job.
    term_only = bool(current) and all(not b["section_id"] for b in current)

    # ---- seating: live checks and exam letters, per chart
    chart_issues = versions_missing = versions_conflicting = 0
    for chart in charts:
        sections = [row[0] for row in db.execute("SELECT section_id FROM seating_chart_sections WHERE chart_id=?", (chart["id"],))]
        check = seating._check(db, chart["id"], assignment_id, sections)
        chart["issues"] = sum(check["counts"].values())
        chart["seated"] = db.execute("SELECT COUNT(*) FROM seating_assignments WHERE chart_id=?", (chart["id"],)).fetchone()[0]
        chart.update(_chart_versions(db, chart))
        chart_issues += chart["issues"]
        versions_missing += chart["missing"]
        versions_conflicting += chart["conflicts"]
    confirmed = sum(1 for c in charts if c["confirmed_at"])
    first_open = next((c for c in charts if not c["confirmed_at"] or c["issues"]), charts[0] if charts else None)
    if charts:
        seating_detail = (f"{confirmed} of {plural(len(charts), 'chart')} confirmed" if confirmed < len(charts)
                          else f"{plural(len(charts), 'room')} confirmed · {sum(c['seated'] for c in charts):,} seated")
        if chart_issues:
            seating_detail += f" · {plural(chart_issues, 'issue')} to check"
    else:
        seating_detail = ("No seating chart yet" if shared["seating"] != "done"
                          else "No seating chart · printed as one stack" if term_only else "No seating chart · printed by section")
    seating_href = f"#seating/{first_open['id']}" if first_open else f"#documents?assignment={assignment_id}&step=seating"

    # ---- notices: this assessment's Response Card issues (seat and DR notices, custom issues), not the Exams page's own
    issues = [dict(row) for row in db.execute(f"""SELECT i.id,i.policy,i.due_at,i.expires_at,i.seating_chart_id,i.created_at
        FROM response_card_issues i WHERE i.term_id=? AND (i.assignment_id=? OR i.seating_chart_id IN
        (SELECT id FROM seating_charts WHERE term_id=? AND assignment_id=?)) AND i.id NOT IN ({COMMUNICATIONS})
        ORDER BY i.created_at,i.id""", (term_id, assignment_id, term_id, assignment_id))]
    notice = dict.fromkeys(("total", "responded", "flagged", "awaiting", "unpublished", "delivery_problem", "queued"), 0)
    attention_issue = None
    for issue in issues:
        issue["counts"] = _issue_counts(db, issue, now)
        for key, value in issue["counts"].items():
            notice[key] += value
        if attention_issue is None and (issue["counts"]["flagged"] or issue["counts"]["delivery_problem"]):
            attention_issue = issue
    notice["issues"] = len(issues)
    if not issues:
        notices = _stage("notices", "idle", "No notices issued", "#response-cards")
    else:
        detail = f"{notice['responded']:,} of {notice['total']:,} confirmed"
        if notice["flagged"]:
            detail += f" · {notice['flagged']:,} flagged"
        if notice["delivery_problem"]:
            detail += f" · {plural(notice['delivery_problem'], 'email')} not delivered"
        state = ("failed" if notice["delivery_problem"] else
                 "done" if notice["total"] and notice["responded"] == notice["total"] and not notice["flagged"] else "started")
        target = attention_issue or (issues[0] if len(issues) == 1 else None)
        href = (f"#response-cards/issue/{target['id']}" + ("?status=needs_review" if target["counts"]["flagged"] else "?delivery=problem"
                                                        if target["counts"]["delivery_problem"] else "")) if target else "#response-cards"
        notices = _stage("notices", state, detail, href, notice)

    # ---- print
    ready = sum(b["status"] == "ready" for b in current)
    failed = sum(b["status"] == "failed" for b in current)
    working = sum(b["status"] in ("pending", "rendering", "validating") for b in current)
    registration = sum(1 for b in batches if is_registration(b["request_id"]))
    if not current:
        print_detail = "No print jobs yet"
    else:
        print_detail = f"{ready} of {plural(len(current), 'print job')} ready · {plural(sum(b['quantity'] or 0 for b in current), 'copy', 'copies')}"
        if term_only and len({b["assignment_version_id"] for b in current}) > 1:
            print_detail += " · one stack"
        if failed:
            print_detail += f" · {failed:,} failed"
        elif working:
            print_detail += f" · {working:,} preparing"
    # Existing print jobs open in the Print jobs list, filtered to this assignment; only an assessment with none yet opens the
    # creation step (a second Create print job there would print another set with new stamps).
    printing = _stage("print", shared["print"], print_detail,
                      f"#documents?jobs={assignment_id}" if current else f"#documents?assignment={assignment_id}&step=print",
                      {"current": len(current), "ready": ready, "failed": failed, "working": working, "registration": registration})

    # ---- dates: seating charts' exam dates first, then the event's sittings (campus wall-clock values, never converted)
    sittings = [dict(row) for row in db.execute("SELECT id,label,kind,date,time,location FROM exam_sittings WHERE event_id=? ORDER BY date,time,id",
                                                (event["id"],))] if event else []
    chart_dates = sorted({(c["exam_date"], c["exam_time"]) for c in charts if c["exam_date"]})
    sitting_dates = sorted({(s["date"], s["time"]) for s in sittings if s["date"]})
    main = chart_dates[0] if chart_dates else next(((s["date"], s["time"]) for s in sittings if s["date"] and s["kind"] == "main"),
                                                    sitting_dates[0] if sitting_dates else None)
    all_days = sorted({d for d, _ in chart_dates} | {d for d, _ in sitting_dates})
    dates = {"date": main[0] if main else None, "time": main[1] if main else "", "last_date": all_days[-1] if all_days else None,
             "rooms": [c["room_name"] for c in charts], "sittings": [{k: s[k] for k in ("label", "kind", "date", "time", "location")} for s in sittings],
             "today": today}

    # ---- DR bookings linked to this assignment, in this term's sections
    bookings = {}
    for row in db.execute("""SELECT b.section_id,b.enrollment_id,r.snapshot_json FROM dr_exam_bookings b JOIN dr_exam_revisions r ON r.id=b.revision_id
            JOIN dr_exam_assignment_links l ON l.booking_id=b.id JOIN course_sections s ON s.id=b.section_id
            WHERE l.assignment_id=? AND s.term_id=?""", (assignment_id, term_id)):
        bookings.setdefault((row["section_id"], row["enrollment_id"]), []).append((json.loads(row["snapshot_json"]) or {}).get("date") or "")
    dr_total = sum(len(v) for v in bookings.values())
    dr_ambiguous = sum(1 for v in bookings.values() if len(v) > 1)
    dr_days = sorted({d for v in bookings.values() for d in v if d})

    # ---- collection (exam event) or scanned copies (no event yet)
    counts = (collection or {}).get("counts") or {}
    if event:
        total = sum(counts.values())
        expected = total - counts.get("absent", 0) - counts.get("excused", 0)
        received = counts.get("complete", 0) + counts.get("partial", 0)
        attention = counts.get("needs_review", 0) + counts.get("missing_pages", 0) + (collection or {}).get("unassigned_received_count", 0)
        no_scan = counts.get("no_scan", 0)
        observed = received + counts.get("needs_review", 0) + counts.get("missing_pages", 0)
        if not observed and not attention:
            collected = _stage("collected", "idle", f"No papers scanned · {plural(expected, 'student')} expected",
                               f"#exams/{event['id']}?tab=collection", {"expected": expected, "received": 0})
        else:
            detail = f"{received:,} of {expected:,} papers"
            if attention:
                detail += f" · {attention:,} to review"
            if no_scan:
                detail += f" · {no_scan:,} without a scanned paper"
            state = "done" if received and not attention and not no_scan else "started"
            collected = _stage("collected", state, detail, f"#exams/{event['id']}?tab=collection",
                               {"expected": expected, "received": received, "attention": attention, "no_scan": no_scan})
    else:
        seen = db.execute(f"""SELECT COUNT(DISTINCT c.id) FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
            JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE v.assignment_id=? AND b.term_id=? AND EXISTS(SELECT 1 FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id
            WHERE p.printed_copy_id=c.id AND {PRESENT.format('sp.message_id')})""", (assignment_id, term_id)).fetchone()[0]
        collected = _stage("collected", "started" if seen else "idle",
                           f"{plural(seen, 'printed copy', 'printed copies')} seen in scans" if seen else "No papers scanned yet",
                           "#inbox", {"seen": seen})
        observed = seen

    # ---- exam day (the date, and DR bookings)
    # The exam has happened once its last date has passed, or once papers are scanned unless its main date is still ahead: DR
    # and early makeup papers are often scanned before the main sitting, which keeps its checklist and its date until then.
    date_passed = bool(dates["last_date"] and dates["last_date"] < today)
    ahead = bool(dates["date"] and dates["date"] > today)
    happened = date_passed or (bool(observed) and not ahead)
    upcoming = bool(dates["date"] and dates["last_date"] >= today)
    day_bits = []
    if dates["date"]:
        day_bits.append(wall_date(dates["date"]) + (f" · {dates['time']}" if dates["time"] else ""))
    if dr_total:
        day_bits.append(plural(dr_total, "DR booking"))
    if dr_ambiguous:
        day_bits.append(f"{plural(dr_ambiguous, 'student')} with several bookings")
    dr_href = f"#accommodations?tab=exams&course={quote(assignment['code'], safe='')}&exam={quote(assignment['title'], safe='')}&when=all"
    # Exam day is done when its dates have passed; with no date recorded, when papers have been scanned.
    exam_done = date_passed or (not dates["date"] and bool(observed))
    exam_state = ("failed" if dr_ambiguous and not happened else "done" if exam_done
                  else "started" if dates["date"] or dr_total or observed else "idle")
    exam_day = _stage("exam_day", exam_state, " · ".join(day_bits) if day_bits else "No date recorded",
                      dr_href if dr_total else (f"#exams/{event['id']}?tab=sittings" if event else seating_href),
                      {"dr_bookings": dr_total, "dr_ambiguous": dr_ambiguous, "dr_dates": dr_days})

    # ---- grading (the event's grading plan)
    if grading and grading.get("unavailable"):
        # One plan whose progress could not be read (an unreadable roster payload, say) never takes the rest of the page with it.
        grading_stage = _stage("grading", "idle", "Grading progress could not be read here · open Grading",
                               f"#grading/{grading['plan_id']}", {"unavailable": 1})
    elif grading:
        entry = grading.get("entry") or {}
        progress = grading.get("progress") or {}
        review = progress.get("review") or {}
        grade = progress.get("grade") or {}
        decisions = grading.get("decision_responses") or 0
        plan_href = f"#grading/{grading['plan_id']}"
        if not entry.get("ready"):
            waiting = entry.get("pending_scans", 0)
            grading_stage = _stage("grading", "started" if waiting else "idle",
                                   f"Waiting for scan checks on {plural(waiting, 'scan')}" if waiting else "No scans cleared for grading yet",
                                   entry.get("inbox_href") or "#inbox", {"pending_scans": waiting})
        else:
            total = review.get("total", 0)
            done = review.get("completed", 0)
            detail = f"{done:,} of {total:,} reviewed" if total else "Set up grading"
            if grading.get("failed"):
                detail += f" · {grading['failed']:,} failed to grade"
            if decisions:
                detail += f" · {plural(grading.get('version_decisions', 0), 'paper')} need a version"
            state = ("failed" if grading.get("failed") else "done" if total and done == total and not decisions
                     else "started" if total or grade.get("completed") else "idle")
            href = f"{plan_href}?tab={grading.get('next_tab') or 'jobs'}"
            waiting = entry.get("pending_scans", 0)
            drafted = ((entry.get("set_up_by_quiz_draft") or entry.get("set_up_before_papers")) and not total and not entry.get("cleared_scans")
                       and not grade.get("completed") and not grading.get("failed"))
            if drafted:
                # A quiz set up by its draft's approval (2026-09-28), or a plan the instructor set up with Set up grading now (2026-10-02),
                # is on Grading before any paper exists: its rubrics are finished while the papers are still being written; a scan of it
                # waits for its Inbox checks as on any other plan.
                state = "started"
                if waiting:
                    detail, href = f"Waiting for scan checks on {plural(waiting, 'scan')}", entry.get("inbox_href") or "#inbox"
                else:
                    detail = ("Rubrics ready · waiting for the papers" if grading.get("next_tab") == "jobs"
                              else "Set up from the approved quiz · finish the rubrics" if entry.get("set_up_by_quiz_draft")
                              else "Set up before the papers · finish the rubrics")
            # Grading ends with its sixth stage, Certification and Export (requirement, 2026-09-26): every grade reviewed is not yet
            # done until a current certification says so; certifying records the grades in the term gradebook.
            certification = grading.get("certification") or {"state": "none"}
            certify = state == "done" and certification.get("state") != "current"
            # READY TO CERTIFY MEANS NOTHING IS STILL COMING (ASSIGNMENT-WORKSPACE, 2026-10-11): every grade reviewed is not ready while
            # a linked scan waits for its Inbox checks or a grading task runs. Certification refuses both (`inbox_pending`,
            # `grading_unfinished`), so the stage says what waits and opens it, and the next step is that one, never Certify.
            arriving = (grading.get("awaiting_review") or 0) if certify else 0
            running = (grading.get("pending") or 0) if certify else 0
            if arriving:
                certify, state, href = False, "started", _waiting_scan(grading, entry)
                detail += f" · waiting for scan checks on {plural(arriving, 'scan')}"
            elif running:
                certify, state, href = False, "started", f"{plan_href}?tab=jobs"
                detail += f" · {plural(running, 'grading task')} queued or running"
            elif certify:
                state, href = "started", f"{plan_href}?tab=export"
                detail = "Changed since certification" if certification.get("state") == "changed" else "All reviewed · ready to certify"
            grading_stage = _stage("grading", state, detail, href,
                                   {"reviewed": done, "total": total, "graded": grade.get("completed", 0), "failed": grading.get("failed", 0),
                                    "version_decisions": grading.get("version_decisions", 0), "certify": certify,
                                    "certification": certification.get("state"), "left_out": certification.get("left_out") or 0,
                                    "left_out_dr": certification.get("left_out_dr", certification.get("left_out")) or 0,
                                    **({"left_out_makeup": certification["left_out_makeup"]} if certification.get("left_out_makeup") else {}),
                                    **({"pending_scans": waiting} if drafted and waiting else {"pending_scans": arriving} if arriving else {})})
    else:
        # A plan is made when a scan is associated with the assessment in the Teaching Inbox, or before any paper by the instructor's
        # Set up grading now (2026-10-02; the hub's Grading tile offers it); until then Grading does not list it. The detail names
        # both ways; the link stays the Inbox's, since the button is on the hub itself.
        grading_stage = _stage("grading", "idle", "Not set up yet · set it up now, or associate a scan in the Teaching Inbox", "#inbox")

    # ---- recorded: the term gradebook's columns that reference this assignment
    setup = db.execute("SELECT setup_json FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
    columns = [item["id"] for item in (json.loads(setup[0]).get("items", []) if setup else []) if item.get("assignment_id") == assignment_id and not item.get("archived")]
    recorded_count = 0
    if columns:
        marks = ",".join("?" * len(columns))
        recorded_count = db.execute(f"""SELECT COUNT(DISTINCT g.section_id||':'||g.enrollment_id) FROM gradebook_scores g
            WHERE g.term_id=? AND g.item_id IN ({marks}) AND g.status!='cleared'
            AND g.seq=(SELECT MAX(n.seq) FROM gradebook_scores n WHERE n.term_id=g.term_id AND n.item_id=g.item_id
                       AND n.section_id=g.section_id AND n.enrollment_id=g.enrollment_id)""", (term_id, *columns)).fetchone()[0]
    roster = expected if event else db.execute(
        "SELECT COUNT(*) FROM roster_current m JOIN course_sections s ON s.id=m.section_id WHERE s.term_id=?", (term_id,)).fetchone()[0]
    if not columns:
        # The gradebook focuses the column that links this assignment or offers to add one (a same-titled column, say).
        recorded = _stage("recorded", "idle", "No gradebook column yet", f"#gradebook/{term_id}?assignment={assignment_id}")
    else:
        # A current certification leaves DR students whose exam is still ahead, and students owed a makeup (GRADING "MAKEUP OWED"),
        # unrecorded until they sit it: say so, never hide it. The empty one of two memberships of a student is recorded through the
        # other (review F6): it counts as recorded, never as left out.
        certified = grading_stage["counts"].get("certification") == "current"
        left_out = (grading_stage["counts"].get("left_out") or 0) if certified else 0
        left_out_dr = min(left_out, grading_stage["counts"].get("left_out_dr", left_out) or 0) if certified else 0
        left_out_makeup = min(left_out - left_out_dr, grading_stage["counts"].get("left_out_makeup") or 0) if certified else 0
        recorded_count += left_out - left_out_dr - left_out_makeup
        left_out = left_out_dr + left_out_makeup
        state = "done" if roster and recorded_count >= roster else "started" if recorded_count else "idle"
        waits = [f"{count:,} left out until their {what}" for count, what in ((left_out_dr, "DR exam"), (left_out_makeup, "makeup")) if count]
        detail = f"{recorded_count:,} of {plural(roster, 'student')} recorded" + ("".join(f" · {w}" for w in waits) if waits and state != "done" else "")
        # Certifying records the grades itself: a certified assessment's column opens without the pull preview.
        recorded = _stage("recorded", state, detail, f"#gradebook/{term_id}?assignment={assignment_id}" + ("" if certified else "&pull=1"),
                          {"columns": len(columns), "recorded": recorded_count, "students": roster, "left_out": left_out})

    # A rebuilt paper keeps its earlier layouts registered, but only the versions its current jobs print are the paper now (the
    # Assignments card names those: a rebuilt Quiz 4 is "PDF A2, B2, C2 +1", so its hub says "4 versions printed · 8 registered").
    # Only a ready job has printed its version: one still rendering, or failed, leaves the plain registered count.
    printed_versions = len({b["assignment_version_id"] for b in current if b["status"] == "ready"})
    paper_detail = ("No paper registered" if not versions
                    else f"{plural(printed_versions, 'version')} printed · {versions:,} registered" if 0 < printed_versions < versions
                    else plural(versions, "version") + " registered")
    paper = _stage("paper", shared["paper"], paper_detail,
                   f"#documents?assignment={assignment_id}&step=version", {"versions": versions})
    seating_stage = _stage("seating", shared["seating"], seating_detail, seating_href,
                           {"charts": len(charts), "confirmed": confirmed, "issues": chart_issues,
                            "versions_missing": versions_missing, "versions_conflicting": versions_conflicting})
    # A scan associated with the assessment (waiting for its Inbox checks, or cleared for grading) or a retained grade means the
    # papers were written even when no printed copy was seen in it (papers printed without labels, like a quiz): the preparation
    # checklist is over. A quiz set up by its draft's approval has a plan (and is "ready" on Grading) before any paper exists:
    # only a scan or a retained grade means papers were written (2026-10-02).
    entry = (grading or {}).get("entry") or {}
    linked = bool(grading and not grading.get("unavailable")
                  and (entry.get("pending_scans") or entry.get("cleared_scans") or entry.get("retained_grades")))
    if linked and not happened and not ahead:
        happened = True
        if exam_day["state"] != "done" and not dates["date"]:
            exam_day = _stage("exam_day", "done", exam_day["detail"] if day_bits else "Papers scanned · no date recorded",
                              exam_day["href"], exam_day["counts"])
    stages = [paper, seating_stage, notices, printing, exam_day, collected, grading_stage, recorded]

    # ---- readiness: only while the exam is ahead
    readiness = []
    if not happened:
        if charts:
            clean = confirmed == len(charts) and not chart_issues
            readiness.append({"key": "charts", "ok": clean, "href": seating_href,
                              "label": "Seating charts confirmed and clean" if clean else
                              f"{confirmed} of {plural(len(charts), 'chart')} confirmed" + (f" · {plural(chart_issues, 'issue')} to check" if chart_issues else "")})
        if any(c["exam_version_count"] for c in charts):
            ok = not versions_missing and not versions_conflicting
            readiness.append({"key": "versions", "ok": ok, "href": seating_href,
                              "label": "Exam versions assigned" if ok else " · ".join(filter(None, [
                                  f"{plural(versions_missing, 'seat')} without a version" if versions_missing else "",
                                  f"{plural(versions_conflicting, 'repeated letter')} in a row" if versions_conflicting else ""]))})
        ok = bool(current) and ready == len(current)
        readiness.append({"key": "print", "ok": ok, "href": printing["href"],
                          "label": "Print jobs ready" if ok else print_detail})
        if issues:
            ok = not notice["flagged"] and not notice["delivery_problem"] and not notice["unpublished"]
            parts = [f"{notice['flagged']:,} flagged" if notice["flagged"] else "",
                     f"{plural(notice['delivery_problem'], 'email')} not delivered" if notice["delivery_problem"] else "",
                     f"{notice['unpublished']:,} not published" if notice["unpublished"] else "",
                     f"{notice['awaiting']:,} awaiting a response" if notice["awaiting"] else ""]
            readiness.append({"key": "notices", "ok": ok, "href": notices["href"],
                              "label": "Notices delivered" + (f" · {notice['awaiting']:,} awaiting a response" if ok and notice["awaiting"] else "")
                              if ok else " · ".join(p for p in parts if p)})
        if dr_total:
            readiness.append({"key": "dr", "ok": not dr_ambiguous, "href": dr_href,
                              "label": f"{plural(dr_total, 'DR booking')} linked" if not dr_ambiguous
                              else f"{plural(dr_ambiguous, 'student')} with several linked DR bookings"})

    return {"assignment_id": assignment_id, "term_id": term_id, "event_id": event["id"] if event else None,
            "title": event["title"] if event else assignment["title"], "assignment_title": assignment["title"],
            "code": assignment["code"], "term_label": term_label(term), "is_current": bool(term["is_current"]),
            "grading_plan_id": grading["plan_id"] if grading else None,
            "stages": stages, "next": _next(stages, happened, event, bool(grading), dated=bool(dates["date"])),
            "readiness": readiness, "dates": dates, "phase": "upcoming" if upcoming and not happened else "past" if happened else "planning",
            "charts": [{"id": c["id"], "room_name": c["room_name"], "exam_date": c["exam_date"], "exam_time": c["exam_time"],
                        "confirmed": bool(c["confirmed_at"]), "issues": c["issues"], "seated": c["seated"]} for c in charts],
            "issues": [{"id": i["id"], "seating_chart_id": i["seating_chart_id"], "created_at": i["created_at"], "counts": i["counts"]} for i in issues]}


def _next(stages, happened, event, plan=True, dated=True):
    """The one next thing, from the stages alone: before the exam, the first unfinished preparation; after it (or once papers
    arrive before a main date still ahead), collection, grading and recording in turn. None when nothing is left to do, or when
    an exam with a main date is ready for its day; one with no main date (DR bookings or not) asks for its scans once it is given.
    `dated` is whether the main date is recorded.
    Recording is `soft` while no gradebook column references the assessment: a suggestion, never a pile of work."""
    by = {s["key"]: s for s in stages}
    def step(title, detail, label, stage, href=None):
        return {"title": title, "detail": detail, "label": label, "href": href or by[stage]["href"], "stage": stage}
    if not happened:
        if by["paper"]["state"] == "idle":
            return step("Add the paper", "Register a PDF or a stamps-only version in Assignments.", "Assignments", "paper")
        if by["seating"]["state"] == "started" or by["seating"]["counts"].get("issues"):
            return step("Confirm the seating", by["seating"]["detail"] + ".", "Seating chart", "seating")
        if by["print"]["state"] == "failed":
            return step("A print job failed", by["print"]["detail"] + ".", "Print jobs", "print")
        if by["notices"]["state"] == "failed" or by["notices"]["counts"].get("flagged"):
            return step("Check the notices", by["notices"]["detail"] + ".", "Response Cards", "notices")
        if by["print"]["state"] == "idle":
            return step("Print the papers", "Seat the exam or print by section, then print labelled copies.", "Assignments", "print")
        if by["exam_day"]["state"] == "failed":
            return step("Check the DR bookings", by["exam_day"]["detail"] + ".", "DR exam schedule", "exam_day")
        if by["print"]["state"] == "started":
            return step("Print jobs are being prepared", by["print"]["detail"] + ".", "Print jobs", "print")
        if by["collected"]["state"] == "idle":
            if dated:
                return None  # the main date is ahead: 'Ready for the day'
            # Printed and ready, no main date recorded and nothing scanned (a quiz given in class, or one with only DR bookings
            # linked): the next thing is its scans, never a bare 'Nothing to do yet'.
            return step("Upload the scans once it is given", "Everything is ready and no date is recorded. After the students write it, "
                        "upload the scanned papers to the Teaching Inbox.", "Upload scans", "collected", UPLOAD)
    if by["collected"]["counts"].get("attention"):
        return step("Review the collected papers", by["collected"]["detail"] + ".", "Collection", "collected")
    if not event:
        if by["collected"]["state"] != "idle":
            return step("Finish the scan in the Teaching Inbox", "Associate the scan with this assignment to start grading.", "Teaching Inbox", "collected")
        return step("Scan the papers", "Upload the scanned papers to the Teaching Inbox.", "Upload scans", "collected", UPLOAD)
    grading = by["grading"]
    if grading["counts"].get("pending_scans"):
        return step("Finish the scan checks", grading["detail"] + ".", "Teaching Inbox", "grading")
    if grading["state"] != "done":
        # Nothing collected and nothing to grade yet (a drafted quiz's plan is 'started' with its rubrics before any paper).
        counted = grading["counts"].get("total") or grading["counts"].get("graded") or grading["counts"].get("failed")
        if by["collected"]["state"] == "idle" and (grading["state"] == "idle" or not counted):
            return step("Scan the papers", "Upload the scanned papers to the Teaching Inbox.", "Upload scans", "collected", UPLOAD)
        if not plan:
            return step("Grade the papers", "Associate the scan with this assignment in the Teaching Inbox to grade it in Grading.", "Teaching Inbox", "grading", "#inbox")
        if grading["counts"].get("certify"):
            return step("Certify grades", "Check and certify the grades on Certify; certifying records them in the gradebook.", "Certify", "grading")
        return step("Continue grading", grading["detail"] + ".", "Grading", "grading")
    if by["recorded"]["state"] != "done":
        counts = by["recorded"]["counts"]
        if counts.get("left_out") and counts.get("recorded", 0) + counts["left_out"] >= counts.get("students", 0):
            return None  # only DR students whose exam is still ahead: the certification reopens once their date has passed
        if grading["counts"].get("certification") == "current" and counts.get("columns"):
            # Certifying recorded the grades (review, 2026-10-02): what is left is the students the certification gave no grade,
            # found on Collection when their paper was never scanned, else typed in the gradebook. Never 'Pull' again.
            students = counts.get("students", 0)
            missing = max(0, students - counts.get("recorded", 0) - counts.get("left_out", 0))
            if missing:
                no_scan = by["collected"]["counts"].get("no_scan", 0)
                detail = (f"{missing:,} of {plural(students, 'student')} {'has' if missing == 1 else 'have'} no grade"
                          + (f"; {no_scan:,} {'has' if no_scan == 1 else 'have'} no scanned paper: find the paper, or record an absence or excuse"
                             if no_scan else "; type the missing grades in the gradebook") + ".")
                return step("Settle the students without a grade", detail, "Collection" if no_scan else "Gradebook",
                            "collected" if no_scan else "recorded")
        result = step("Record the grades", "Pull this assignment's final grades into the term gradebook.", "Gradebook", "recorded")
        if not by["recorded"]["counts"].get("columns"):
            result["soft"] = True
        return result
    return None


def grading_summary(frq, db, plan_id):
    """What the Grading page itself counts for one plan (its entry, `workflow_summary` progress and next tab); the caller holds
    the roster lifecycle lock, as the grading overview does. Nothing is graded, reviewed or changed."""
    plan = frq.plan(db, plan_id)
    shared = {}  # this read's memo: the entry's scan overviews serve the workflow counts
    entry = frq.grading_entry(db, plan, shared=shared)
    result = {"plan_id": plan_id, "entry": entry}
    if entry.get("ready"):
        workflow = frq.workflow_summary(db, plan, shared=shared, export=False)  # its responses on this connection, from the same read of the participants
        result.update({key: workflow.get(key) for key in ("progress", "next_tab", "failed", "pending", "decision_responses",
                                                           "version_decisions", "review_responses", "new_responses", "awaiting_review",
                                                           "certification")})
        # Which linked scans still wait for their Inbox checks, for the stage's link (`_waiting_scan`); nothing else of a scan is kept.
        result["scans"] = [{"job_id": scan["job_id"], "inbox_ready": scan.get("inbox_ready")} for scan in workflow.get("scans") or []]
    return result


def _waiting_scan(grading, entry):
    """Where the first linked scan still waiting for its Inbox checks opens (certification's own `inbox_pending` link), else the Inbox."""
    scan = next((s for s in grading.get("scans") or [] if not s.get("inbox_ready")), None)
    return f"#inbox/job/{scan['job_id']}" if scan else entry.get("inbox_href") or "#inbox"


class ExamOverview:
    """The hub's reads. `frq` is a callable returning the assisted-grading service (or None), looked up at request time."""

    def __init__(self, exams, frq=lambda: None):
        self.exams, self.frq = exams, frq
        self.rosters, self.path = exams.rosters, exams.path
        self._grading, self._grading_lock = {}, threading.Lock()

    def _grading_summary(self, frq, db, plan_id):
        """`grading_summary`, reused for GRADING_EVERY seconds per plan while the plan's grading record is unchanged; a failure reads
        as 'could not be read', never as an error. The record's marker is the plan's version and its newest audit event and scan
        link (three indexed reads): certifying, a review, a retry or a new scan writes one of them, so the instructor's own action
        shows at once on the hub and the Exams list instead of up to two minutes later (review, 2026-10-02)."""
        now = time.monotonic()
        marker = tuple(db.execute("""SELECT f.version,(SELECT MAX(id) FROM frq_events WHERE assignment_id=f.id),
            (SELECT MAX(id) FROM frq_scan_links WHERE assignment_id=f.id) FROM frq_assignments f WHERE f.id=?""", (plan_id,)).fetchone() or ())
        with self._grading_lock:
            kept = self._grading.get(plan_id)
        if kept and kept[0] == marker and now - kept[1] < GRADING_EVERY:
            return kept[2]
        try:
            value = grading_summary(frq, db, plan_id)
        except Exception:  # noqa: BLE001 - a read-only projection: one plan's failure must not fail the list or the hub
            log.warning("Grading progress of plan %s could not be read for the exam overview.", plan_id, exc_info=True)
            return {"plan_id": plan_id, "unavailable": True}
        with self._grading_lock:
            self._grading[plan_id] = (marker, now, value)
        return value

    def _participants(self, db, event_id):
        # Collection needs only the memberships and their expectations: no roster payload, so no name is read.
        return [dict(row, name="") for row in db.execute("""SELECT p.*,s.number section_number FROM exam_participants p
            JOIN course_sections s ON s.id=p.section_id WHERE p.event_id=? ORDER BY s.number,p.enrollment_id""", (event_id,))]

    def _one(self, db, assignment_id, term_id, event_id=None, today=None):
        event = self.exams._event(db, event_id) if event_id else None
        collection = self.exams._collection(db, event, self._participants(db, event["id"])) if event else None
        grading = None
        frq = self.frq()
        plan = db.execute("SELECT id FROM frq_assignments WHERE event_id=?", (event["id"],)).fetchone() if event and frq else None
        if plan:
            grading = self._grading_summary(frq, db, plan[0])
        result = lifecycle(db, assignment_id, term_id, event=event, collection=collection, grading=grading, today=today,
                           seating=self.exams.seating)
        if result and event:
            result["sections"] = [s["number"] for s in self.exams._sections(db, event["id"])]
        elif result:
            # Term-owned print jobs (no seating chart, DOCUMENTS 'TERM PRINT JOBS') name no section of their own: their sections
            # are listed by reference in print_batch_sections.
            result["sections"] = sorted({row[0] for row in db.execute("""SELECT s.number FROM seating_chart_sections x JOIN seating_charts c ON c.id=x.chart_id
                JOIN course_sections s ON s.id=x.section_id WHERE c.term_id=? AND c.assignment_id=?
                UNION SELECT s.number FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
                JOIN course_sections s ON s.id=b.section_id WHERE v.assignment_id=? AND b.term_id=?
                UNION SELECT s.number FROM print_batch_sections x JOIN print_batches b ON b.id=x.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN course_sections s ON s.id=x.section_id
                WHERE v.assignment_id=? AND b.term_id=? AND b.status!='cancelled' AND b.request_id NOT LIKE 'unlabeled:%'""",
                (term_id, assignment_id, assignment_id, term_id, assignment_id, term_id))})
        return result

    def event(self, event_id, today=None):
        with self.rosters.lock(), connect(self.path) as db:
            event = self.exams._event(db, event_id)
            return self._one(db, event["assignment_id"], event["term_id"], event_id, today)

    def assessment(self, assignment_id, term_id, today=None):
        from werkzeug.exceptions import NotFound
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT id FROM exam_events WHERE assignment_id=? AND term_id=?", (assignment_id, term_id)).fetchone()
            result = self._one(db, assignment_id, term_id, row[0] if row else None, today)
        if result is None:
            raise NotFound("Assessment not found.")
        return result

    def listing(self, today=None):
        """Every exam event, and assessments of CURRENT terms that have seating charts, print jobs (not registration jobs) or a
        grading plan but no exam event yet, each with its lifecycle. One lock per assessment, never across the whole walk."""
        with connect(self.path) as db:
            events = [tuple(row) for row in db.execute("SELECT id,assignment_id,term_id FROM exam_events ORDER BY created_at DESC,id")]
            have = {(a, t) for _, a, t in events}
            candidates = [tuple(row) for row in db.execute("""SELECT c.assignment_id,c.term_id FROM seating_charts c JOIN course_terms t ON t.id=c.term_id
                    WHERE t.is_current=1 AND t.deleted_at IS NULL
                UNION SELECT v.assignment_id,b.term_id FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
                    JOIN course_terms t ON t.id=b.term_id
                    WHERE t.is_current=1 AND t.deleted_at IS NULL AND b.status!='cancelled' AND b.request_id NOT LIKE 'unlabeled:%'""")]
        items = []
        for event_id, assignment_id, term_id in events:
            items.append(self._listed(assignment_id, term_id, event_id, today))
        for assignment_id, term_id in sorted(set(candidates) - have):
            items.append(self._listed(assignment_id, term_id, None, today))
        return [item for item in items if item]

    def _listed(self, assignment_id, term_id, event_id, today):
        """One row of the listing; an assessment whose lifecycle cannot be read is listed without it (its own page says why)."""
        try:
            with self.rosters.lock(), connect(self.path) as db:
                return self._one(db, assignment_id, term_id, event_id, today)
        except Exception:  # noqa: BLE001 - see _grading_summary
            log.warning("The lifecycle of assignment %s in term %s could not be read for the Exams list.", assignment_id, term_id, exc_info=True)
            return None
