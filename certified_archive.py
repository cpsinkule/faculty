"""Certified assessments leave the everyday lists (requirement, 2026-09-27).

ARCHIVED IS A VIEW, never a retained status: an assignment of ANY kind (quiz, homework, worksheet, exam: a course-wide
`assignments.id` in one term) is archived while its term grading plan has a certification (`frq_certifications`, immutable; the latest counts, nothing uncertifies). Nothing is moved,
changed, hidden from its own page or deleted: each list folds that assessment's rows into its own "Archived" section. Its seating
charts and Response Cards no longer need the instructor, flagged or not (requirement, 2026-09-27): they count in no attention card,
badge or "needs attention" line, while their records, statuses and flags stay exactly as they are. What is still unfinished
elsewhere stays in view: a scan in Current and grading whose certification has changed.

NEW WORK STAYS OUT (requirement, 2026-09-27, for a late makeup): a row made AFTER the latest certification (a makeup's scan received
or associated, a label print job, a chart, a card issue, a seat update) is not archived and counts for attention as usual, until a
newer certification covers it. Timestamps are the catalog's UTC ISO text, compared as text. A read-only catalog projection: a few
indexed lookups, no roster payload, no lock.
"""

def _has(db, *tables):
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    return all(table in names for table in tables)


def certified(db):
    """{(assignment_id, term_id): {"plan_id", "certification_id", "certified_at"}} of every assessment with a certification."""
    if not _has(db, "frq_certifications", "frq_assignments", "exam_events"):
        return {}
    found = {}
    # A certification of just one changed grade (`amends_id`, a regrade request's Apply) keeps the cutoff of the newest ordinary one:
    # rows made since (a print job, a chart) stay as they were.
    for row in db.execute("""SELECT e.assignment_id,e.term_id,f.id plan_id,c.id certification_id,
            (SELECT o.created_at FROM frq_certifications o WHERE o.assignment_id=c.assignment_id AND o.amends_id IS NULL ORDER BY o.rowid DESC LIMIT 1) created_at
            FROM frq_certifications c JOIN frq_assignments f ON f.id=c.assignment_id JOIN exam_events e ON e.id=f.event_id
            WHERE c.rowid=(SELECT MAX(n.rowid) FROM frq_certifications n WHERE n.assignment_id=c.assignment_id)"""):
        found[(row["assignment_id"], row["term_id"])] = {"plan_id": row["plan_id"], "certification_id": row["certification_id"],
                                                          "certified_at": row["created_at"]}
    return found


def certified_messages(db, message_ids=None):
    """{message_id: archive facts} of scans whose latest association (`frq_scan_links`) is a certified plan."""
    if not _has(db, "frq_scan_links", "frq_certifications", "frq_assignments", "exam_events"):
        return {}
    plans = {facts["plan_id"]: {**facts, "assignment_id": key[0], "term_id": key[1]} for key, facts in certified(db).items()}
    if not plans:
        return {}
    found = {}
    for row in db.execute("""SELECT l.message_id,l.assignment_id,MAX(l.created_at,COALESCE(m.received_at,'')) made FROM frq_scan_links l
            LEFT JOIN inbox_messages m ON m.id=l.message_id
            WHERE l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id)"""):
        plan = plans.get(row["assignment_id"])
        if plan and (message_ids is None or row["message_id"] in message_ids) and covered(plan, row["made"]):
            found[row["message_id"]] = plan
    return found


def covered(entry, created_at):
    """Whether a row made at `created_at` is covered by the certification `entry`: made no later than it."""
    return bool(entry) and (not created_at or str(created_at) <= entry["certified_at"])


def pair_activity(db, assignment_id, term_id, plan_id):
    """The latest time anything of the assessment was made: a seating chart, a print job, a scan associated with its plan."""
    from teach_inbox.service import PRESENT  # Inbox imports archive helpers during startup.
    found = [row[0] for row in db.execute(f"""SELECT MAX(created_at) FROM seating_charts WHERE assignment_id=? AND term_id=?
        UNION ALL SELECT MAX(b.created_at) FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
          WHERE v.assignment_id=? AND b.term_id=?
        UNION ALL SELECT MAX(MAX(l.created_at,COALESCE(m.received_at,'')))
          FROM frq_scan_links l JOIN inbox_messages m ON m.id=l.message_id
          WHERE l.assignment_id=? AND {PRESENT.format('m.id')}
            AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id)""", (assignment_id, term_id, assignment_id, term_id, plan_id))
        if row[0]]
    return max(found) if found else None


def uncertified(db, assignment, term, created=None):
    """A SQL condition (column expressions in) that holds while the assessment has NO certification: attention counts use it,
    since a certified assessment no longer needs the instructor (requirement, 2026-09-27). A NULL assignment is uncertified,
    and so is a row made (`created`, a column expression) after the latest certification: new work needs the instructor."""
    if not _has(db, "frq_certifications", "frq_assignments", "exam_events"):
        return "1"
    return f"""NOT EXISTS(SELECT 1 FROM exam_events ce JOIN frq_assignments cf ON cf.event_id=ce.id JOIN frq_certifications cc ON cc.assignment_id=cf.id
        WHERE ce.assignment_id={assignment} AND ce.term_id={term}{f" AND cc.created_at>={created} AND cc.amends_id IS NULL" if created else ""})"""


def facts(entry, created_at=None):
    """What a list row carries when it is archived (when it was certified), or None: no certification, or the row was made after it."""
    return {"certified_at": entry["certified_at"], "certification_id": entry["certification_id"]} if covered(entry, created_at) else None
