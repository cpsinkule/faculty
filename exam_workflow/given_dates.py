"""The day an assessment was given to each student (EXAMS "DATE GIVEN"): read from the exam record of a term assignment, catalog
only, campus wall-clock values compared as written and never converted.

The order (first written for the Certify dialog's absences beside zeros, GRADING "ABSENCES BESIDE ZEROS", 2026-10-02; shared since
2026-10-03 with ABSENCES "MAKEUPS"):
1. the student's own sitting on the Exams page when it has a date (a makeup, say);
2. else the earliest exam date of the assignment's seating charts naming their section, leaving out a DR exam's chart and a chart
   tied to a makeup or DR sitting;
3. else the event's first main sitting with a date.

`own_makeup=False` leaves out step 1 for a student whose own sitting is a MAKEUP sitting: the day the work was given to their
section, which a makeup scheduled later must not move (ABSENCES "MAKEUPS": scheduling a makeup never makes an absence stop covering
the day the work was given).

`term_given` / `section_given` are the same reading for a SECTION (steps 2 and 3; SCHEDULING "CLASS SCHEDULE", 2026-10-10), with
the time and place beside the day: a section's class schedule lists them, and the instructor's exams differ by section ("each
section has its own final exam time/date/place … regular exams are at the same time, but different places for each section")."""

# The first main sitting with a date is the one earliest by day, then time, then when it was made.
MAIN_ORDER = "date,time,created_at,id"


# A chart of the main sitting (or of none) gives its sections' date; a DR exam's chart or a makeup's never does.
CHART_FILTER = """ AND NOT EXISTS(SELECT 1 FROM exam_event_charts e JOIN exam_sittings s ON s.id=e.sitting_id
        WHERE e.chart_id=c.id AND s.kind!='main')"""


def given_dates(db, event_id, term_id, assignment_id, members, *, own_makeup=True):
    """{(section_id, enrollment_id): date or None} for the `members` named, from the exam record `event_id` of `assignment_id`
    (the assignment the record and its seating charts name) in `term_id`."""
    members = [(str(section), str(enrollment)) for section, enrollment in members]
    if not members:
        return {}
    kinds = "" if own_makeup else " AND s.kind!='makeup'"
    own = {(section, enrollment): date for section, enrollment, date in db.execute(f"""SELECT p.section_id,p.enrollment_id,s.date
        FROM exam_participants p JOIN exam_sittings s ON s.id=p.sitting_id AND s.event_id=p.event_id
        WHERE p.event_id=? AND COALESCE(s.date,'')!=''{kinds}""", (event_id,))}
    charts = dict(db.execute(f"""SELECT x.section_id,MIN(c.exam_date) FROM seating_charts c JOIN seating_chart_sections x ON x.chart_id=c.id
        WHERE c.term_id=? AND c.assignment_id=? AND c.exam_date!='' AND c.dr_exam_key=''{CHART_FILTER} GROUP BY x.section_id""",
                             (term_id, assignment_id)).fetchall())
    main = (db.execute(f"""SELECT date FROM exam_sittings WHERE event_id=? AND kind='main' AND COALESCE(date,'')!=''
        ORDER BY {MAIN_ORDER} LIMIT 1""", (event_id,)).fetchone() or [None])[0]
    return {key: own.get(key) or charts.get(key[0]) or main for key in members}


def term_given(db, term_id, assignment_id=None):
    """{assignment_id: {section_id: {date, time, place, source, source_id, event_id}}}: the day, time and place each assessment of
    a term is given to each SECTION its exam record lists or a seating chart names (`assignment_id` narrows it to one assessment).

    The day is steps 2 and 3 above (a student's own sitting is theirs alone). The time is the chart's that gave the day, else the
    main sitting's when they share it. The place is the room of the section's charts on that day (a chart without a date yet still
    says the room), else the sitting's own. `source`, `time_source` and `place_source` say which record gave each ('chart', the
    section's own, or 'sitting', the whole record's; `source_id` is the day's); a value nothing records is ''."""
    one, args = (" AND {}assignment_id=?", (term_id, assignment_id)) if assignment_id else ("", (term_id,))
    mains, listed, charts = {}, {}, {}
    events = dict(db.execute(f"SELECT assignment_id,id FROM exam_events WHERE term_id=?{one.format('')}", args).fetchall())
    if events:
        for row in db.execute(f"""SELECT s.event_id,s.id,s.date,s.time,s.location,COALESCE(r.name,'')
            FROM exam_sittings s JOIN exam_events e ON e.id=s.event_id LEFT JOIN rooms r ON r.id=s.room_id
            WHERE e.term_id=?{one.format('e.')} AND s.kind='main' AND COALESCE(s.date,'')!=''
            ORDER BY {",".join("s." + part for part in MAIN_ORDER.split(","))}""", args):
            mains.setdefault(row[0], row)
        for event_id, section in db.execute(f"""SELECT x.event_id,x.section_id FROM exam_event_sections x
            JOIN exam_events e ON e.id=x.event_id WHERE e.term_id=?{one.format('e.')}""", args):
            listed.setdefault(event_id, set()).add(section)
    for row in db.execute(f"""SELECT c.assignment_id,x.section_id,c.id,c.exam_date,c.exam_time,r.name FROM seating_charts c
        JOIN seating_chart_sections x ON x.chart_id=c.id JOIN rooms r ON r.id=c.room_id
        WHERE c.term_id=?{one.format('c.')} AND c.assignment_id IS NOT NULL AND c.dr_exam_key=''{CHART_FILTER}
        ORDER BY c.exam_date,c.created_at,c.id""", args):
        charts.setdefault((row[0], row[1]), []).append(row)
    found = {}
    for assignment in sorted(set(events) | {key[0] for key in charts}):
        event_id = events.get(assignment)
        main = mains.get(event_id)
        for section in sorted(set(listed.get(event_id, ())) | {key[1] for key in charts if key[0] == assignment}):
            mine = charts.get((assignment, section), [])
            dated = [chart for chart in mine if chart[3]]
            shared = main if main and (not dated or main[2] == min(chart[3] for chart in dated)) else None
            if dated:
                day = min(chart[3] for chart in dated)
                time = next((chart[4] for chart in dated if chart[3] == day and chart[4]), "")
                source, source_id = "chart", next(chart[2] for chart in dated if chart[3] == day)
            elif main:
                day, time, source, source_id = main[2], "", "sitting", main[1]
            else:
                day, time, source, source_id = "", "", "chart" if mine else "", mine[0][2] if mine else ""
            place = ", ".join(sorted({chart[5] for chart in mine if chart[3] in ("", day) and chart[5]}))
            times, places = ("chart" if time else ""), ("chart" if place else "")
            if not time and shared and shared[3]:
                time, times = shared[3], "sitting"
            if not place and shared and (shared[4] or shared[5]):
                place, places = shared[4] or shared[5], "sitting"
            found.setdefault(assignment, {})[section] = {"date": day, "time": time, "place": place, "source": source,
                                                         "time_source": times, "place_source": places,
                                                         "source_id": source_id, "event_id": event_id or ""}
    return found


def section_given(db, term_id, assignment_id, section_id):
    """One section's {date, time, place, source, source_id, event_id} for one assessment (`term_given`), or None."""
    return term_given(db, term_id, assignment_id).get(assignment_id, {}).get(section_id)
