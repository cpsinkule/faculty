"""Calendar projection over retained teaching owners and custom calendar events.

GET /api/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD includes both dates (at most
366 days). It returns today's campus date, time_zone, events and the custom
service's permanent course/term/section catalog. Every event has a source-prefixed
id, title, date/end_date, time/end_time, all_day, location, notes, owner references,
code, term_label, sections, href, editable, version and time_basis. Custom events
also have custom_id, scope and category. DR rows add booking_count, never student
identities or booking identifiers. Gradebook rows are column due dates (category deadline). Dates and times remain native wall-clock text.
Google rows (source `google`, category event) are the instructor's own Google Calendar events, read only, from
`google_calendar` (its copy of the chosen calendars; none until the sync is connected), with `calendar` naming their calendar and
`href` their Google link; `google` in the response says whether they are shown, from which account and over which dates.
Exam, seating and DR rows also carry `assignment_id` (the course-wide assessment, when known) and `exam_href`, the
assessment's Exams hub: `#exams/<event>` when the term has an exam record for it, else `#exams/assessment/<assignment>/<term>`.
A DR row's `href` opens the DR exam schedule filtered to its course and exam (`#accommodations?tab=exams&course=…&exam=…&when=all`);
it names no student.

This module owns no data. Source rows read only the shared catalog; source exam
and seating schedules are edited in their existing workflows. Custom POST/PUT
delegate to CalendarService, and DELETE requires the event's version in JSON.
"""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urlencode, quote
from scheduling.clock import CAMPUS_ZONE, ZONE_NAME

from flask import Blueprint, current_app, jsonify, request

from request_limits import json_object
from rosters import connect
from seating import term_label


TIME_ZONE = ZONE_NAME  # SCHEDULING "ONE CLOCK": the campus zone, once


def date_range(start, end):
    """Reject ambiguous dates and cap the inclusive query span."""
    values = []
    for value in (start, end):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Supply start and end dates as YYYY-MM-DD.")
        try:
            values.append(date.fromisoformat(value))
        except ValueError as cause:
            raise ValueError("Supply valid start and end dates.") from cause
    if not 0 <= (values[1] - values[0]).days < 366:
        raise ValueError("Choose an inclusive date range of 1 to 366 days.")
    return start, end


def source_event(source, row, *, title, sections, href):
    return {"id": f"{source}:{row['id']}", "source": source, "title": title,
            "date": row["date"], "end_date": row["date"], "time": row["time"],
            "end_time": "", "all_day": not bool(row["time"]),
            "location": row["location"], "notes": "", "definition_id": row["definition_id"],
            "term_id": row["term_id"], "section_id": None, "code": row["code"],
            "term_label": term_label(row), "sections": sections, "href": href,
            "editable": False, "version": None, "time_basis": "campus"}


def custom_event(row):
    """One wire shape for mutation results and custom events in the feed."""
    return {"id": f"custom:{row['id']}", "custom_id": row["id"], "source": "custom",
            **{key: row[key] for key in ("title", "date", "end_date", "time", "end_time", "all_day",
                                        "location", "notes", "definition_id", "term_id", "section_id",
                                        "code", "term_label", "version", "scope", "category")},
            "sections": [row["section_number"]] if row.get("section_number") is not None else [],
            "href": "#calendar", "editable": True, "time_basis": "campus"}


def dr_schedule_href(code, exam):
    """The DR exam schedule on the Accommodations page, filtered to one course and exam (no student fields)."""
    return "#accommodations?" + urlencode({"tab": "exams", "course": code or "", "exam": exam or "", "when": "all"}, quote_via=quote)


def exam_hub(db, term_id, assignment_id, cache):
    """An assessment's Exams hub: its exam record when the term has one, else the assessment route (read only)."""
    if not assignment_id:
        return None
    key = (term_id, assignment_id)
    if key not in cache:
        row = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", key).fetchone()
        cache[key] = f"#exams/{row[0]}" if row else f"#exams/assessment/{assignment_id}/{term_id}"
    return cache[key]


def typed_range(event):
    """A sitting or seating chart whose time was typed as a range ("9:00 AM–10:15 AM", SCHEDULING stage 1g) shows as that start and
    end, so the Calendar and Google show a timed event; any other time stays as written."""
    from scheduling.clock import hhmm, parse_clock, parse_span
    text = event.get("time") or ""
    found = parse_span(text) if parse_clock(text, "any") is None else None
    if found and found[1] is not None and not event.get("end_time"):
        event.update(time=hhmm(found[0]), end_time=hhmm(found[1]), all_day=False)
    return event


def source_events(db, start, end):
    events, hubs, sections_by_event = [], {}, {}
    links = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dr_exam_assignment_links'").fetchone()
    for row in db.execute("""SELECT s.id,s.date,s.time,s.location,s.label,s.kind,e.title,e.id event_id,e.assignment_id,
            t.id term_id,t.definition_id,t.code,t.term,t.term_other,t.year
        FROM exam_sittings s JOIN exam_events e ON e.id=s.event_id
        JOIN course_terms t ON t.id=e.term_id
        WHERE t.deleted_at IS NULL AND s.date BETWEEN ? AND ?""", (start, end)):
        # PERFORMANCE: every sitting of an exam shares this ordered section list for this read.
        if row["event_id"] not in sections_by_event:
            sections_by_event[row["event_id"]] = [item[0] for item in db.execute("""SELECT s.number FROM exam_event_sections es
                JOIN course_sections s ON s.id=es.section_id WHERE es.event_id=? ORDER BY s.number""", (row["event_id"],))]
        sections = sections_by_event[row["event_id"]]
        title = row["title"] + (f" · {row['label']}" if row["label"] and row["label"] != row["title"] else "")
        event = typed_range(source_event("exam", row, title=title, sections=sections, href="#exams/" + row["event_id"]))
        event.update(assignment_id=row["assignment_id"], exam_href="#exams/" + row["event_id"])
        if row["kind"] == "makeup":
            # MAKEUP LENGTH (ABSENCES, CALENDAR; 2026-10-05): a makeup sitting lasts its longest student's quiz (20 minutes times
            # their extra time), so it is marked and given that end; other sittings are unchanged.
            from absences.makeup_length import sitting_minutes
            from scheduling.clock import hhmm, parse_clock
            minutes, begins = sitting_minutes(db, row["id"]), parse_clock(row["time"], "hhmm")
            event.update(makeup=True, minutes=minutes)
            if begins is not None and not event.get("end_time"):  # a typed range keeps its own end
                event["end_time"] = hhmm(min(begins + minutes, 24 * 60 - 1))
        events.append(event)
    for row in db.execute("""SELECT c.id,c.exam_date date,c.exam_time time,r.name location,a.title,a.id assignment_id,
            t.id term_id,t.definition_id,t.code,t.term,t.term_other,t.year
        FROM seating_charts c JOIN assignments a ON a.id=c.assignment_id
        JOIN rooms r ON r.id=c.room_id JOIN course_terms t ON t.id=c.term_id
        WHERE t.deleted_at IS NULL AND c.exam_date BETWEEN ? AND ?
        AND NOT EXISTS (SELECT 1 FROM exam_event_charts link
            JOIN exam_sittings sitting ON sitting.id=link.sitting_id
            WHERE link.chart_id=c.id AND sitting.date=c.exam_date AND sitting.time=c.exam_time)""", (start, end)):
        sections = [item[0] for item in db.execute("""SELECT s.number FROM seating_chart_sections cs
            JOIN course_sections s ON s.id=cs.section_id WHERE cs.chart_id=? ORDER BY s.number""", (row["id"],))]
        event = typed_range(source_event("seating", row, title=row["title"], sections=sections, href="#seating/" + row["id"]))
        event.update(assignment_id=row["assignment_id"], exam_href=exam_hub(db, row["term_id"], row["assignment_id"], hubs))
        events.append(event)
    # Read only the current revision pointer. Aggregation never selects names,
    # UINs, enrollment/booking IDs, request dates, or original source fields.
    for row in db.execute("""SELECT t.definition_id,t.id term_id,t.code,t.term,t.term_other,t.year,
            s.id section_id,s.number,b.exam_key,MIN(json_extract(v.snapshot_json,'$.exam_type')) title,
            json_extract(v.snapshot_json,'$.date') date,
            json_extract(v.snapshot_json,'$.start_time') time,
            json_extract(v.snapshot_json,'$.end_time') end_time,
            json_extract(v.snapshot_json,'$.campus') location,COUNT(*) booking_count,
            """ + ("MIN(l.assignment_id)" if links else "NULL") + """ assignment_id
        FROM dr_exam_bookings b JOIN dr_exam_revisions v ON v.id=b.revision_id""" + (
            " LEFT JOIN dr_exam_assignment_links l ON l.booking_id=b.id" if links else "") + """
        JOIN course_sections s ON s.id=b.section_id JOIN course_terms t ON t.id=s.term_id
        WHERE t.deleted_at IS NULL AND json_extract(v.snapshot_json,'$.date') BETWEEN ? AND ?
        GROUP BY t.definition_id,t.id,s.id,b.exam_key,
            json_extract(v.snapshot_json,'$.date'),json_extract(v.snapshot_json,'$.start_time'),
            json_extract(v.snapshot_json,'$.end_time'),json_extract(v.snapshot_json,'$.campus')""", (start, end)):
        group = dict(row)
        key = [group[field] for field in ("definition_id", "term_id", "section_id", "exam_key", "date", "time", "end_time", "location")]
        assignment = group.pop("assignment_id")
        group["id"] = sha256(json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        event = source_event("dr", group, title=group["title"], sections=[group["number"]],
                             href=dr_schedule_href(group["code"], group["title"]))
        event.update(section_id=group["section_id"], end_time=group["end_time"],
                     booking_count=group["booking_count"], time_basis="dr_center",
                     assignment_id=assignment, exam_href=exam_hub(db, group["term_id"], assignment, hubs))
        events.append(event)
    # A gradebook column's due date: a read-only deadline of its term, edited in the gradebook's column settings.
    from gradebook.service import calendar_events as gradebook_deadlines
    events.extend(gradebook_deadlines(db, start, end))
    from student_support.service import calendar_events as office_hours
    events.extend(office_hours(db, start, end))
    return events


def register(app, service):
    api = Blueprint("workspace_calendar", __name__, url_prefix="/api/calendar")

    body = json_object

    @api.get("")
    def listing():
        start, end = date_range(request.args.get("start"), request.args.get("end"))
        from scheduling.feed import assemble
        # The instructor's Google events (google_calendar/): Faculty's copy of the chosen calendars, read only; none until connected.
        google = current_app.extensions.get("google_calendar")
        with connect(service.path) as db:
            db.execute("BEGIN")
            events = assemble(db, start, end, calendar=service, google=google or None)
        support = current_app.extensions.get("student_support")
        if support is not None:  # CALENDAR OFFICE HOURS: each appointment names its student (roster files, read after the transaction)
            support.name_appointments(events)
        events.sort(key=lambda event: (event["date"], event["time"], event["title"].casefold(), event["id"]))
        return jsonify(today=datetime.now(timezone.utc).astimezone(CAMPUS_ZONE).date().isoformat(),
                       time_zone=TIME_ZONE, events=events, catalog=service.catalog(),
                       google=google.summary() if google else None)

    @api.post("/events")
    def create():
        return jsonify(custom_event(service.create(body()))), 201

    @api.put("/events/<event_id>")
    def update(event_id):
        return jsonify(custom_event(service.update(event_id, body())))

    @api.delete("/events/<event_id>")
    def delete(event_id):
        row = service.delete(event_id, body())
        return jsonify(id=f"custom:{row['id']}", custom_id=row["id"], deleted=True, version=row["version"])

    app.register_blueprint(api)
