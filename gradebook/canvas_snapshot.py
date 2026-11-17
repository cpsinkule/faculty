"""CANVAS AS SYNC FOUND IT (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-08). Every Sync with Canvas keeps
the Canvas it planned both halves from: the summary's read of every linked course, with the assignments Sync read again before
writing laid over it (`canvas_export.overlay`), so it is exactly what each value sent, filled, taken or held was compared with.
What Sync then sent is its send's receipts (`gradebook_canvas_sent`): together, Canvas before and after.

- WHAT IS KEPT, per Canvas course: its assignment groups {id: name}, its assignments as read, its student enrollments and every
  submission (score, entered score, excused, missing, late, workflow state, posted). A student's SIS ID (TAMU: the UIN) is NOT
  kept; in its place `row`, the Faculty membership it matches by Sync's own rule (the Canvas course linked to the student's
  section, the SIS ID equal to the UIN; a current membership before a dropped one; null when none does). `read_again` names the
  assignments Sync read again.
- HOW: compact JSON with sorted keys, gzip-compressed, built with no lock held and recorded as ONE row of
  `gradebook_canvas_snapshots` (migration `gradebook/011`) in the sync's own transaction, owned by the TERM through the sync's
  `import` request. `size` and `sha256` are the JSON's; `load` checks both.
- Nothing decides anything from it: Sync's summary, plan, answer and send are unchanged, and no route or page shows it.
"""
import gzip
import hashlib
import json
import uuid

from course_storage import timestamp

FORMAT = "canvas-read/1"
LEVEL = 6  # gzip level: level 9 takes about twice as long for a few percent less


def _number(value):
    """Canvas's numeric IDs in numeric order (text, so an unexpected one still sorts)."""
    return str(value).zfill(20)


def build(canvas, students, links, read_again=None):
    """The copy of `canvas` (`canvas_export.from_read`'s shape, after `overlay`) a sync keeps: {data (gzip), size, sha256, courses,
    submissions}. `students` the term's roster rows ({row, section_id, uin, dropped}); `links` {section_id: {course_id, ...}};
    `read_again` {course: the assignment IDs Sync read again}."""
    rows = {}
    for student in students:
        course = (links.get(student["section_id"]) or {}).get("course_id")
        if not course or not student.get("uin"):
            continue
        key, dropped = (course, student["uin"]), bool(student.get("dropped"))
        if key not in rows or (rows[key][1] and not dropped):
            rows[key] = (student["row"], dropped)
    courses, count = {}, 0
    for course in sorted(canvas, key=_number):
        data = canvas[course]
        submissions = sorted(({**value, "assignment_id": assignment, "user_id": user} for (assignment, user), value in data["submissions"].items()),
                             key=lambda s: (_number(s["assignment_id"]), _number(s["user_id"])))
        count += len(submissions)
        courses[course] = {"groups": data["groups"], "assignments": data["assignments"], "submissions": submissions,
                           "students": [{**{k: v for k, v in s.items() if k != "sis"}, "row": rows.get((course, s.get("sis") or ""), (None,))[0]}
                                        for s in data["students"]],
                           "read_again": sorted((read_again or {}).get(course, ()), key=_number)}
    text = json.dumps({"format": FORMAT, "courses": courses}, sort_keys=True, separators=(",", ":"), default=str).encode()
    return {"data": gzip.compress(text, compresslevel=LEVEL, mtime=0), "size": len(text), "sha256": hashlib.sha256(text).hexdigest(),
            "courses": len(courses), "submissions": count}


def record(db, term_id, request_ref, built):
    """Keeps a built copy for the sync's `import` request (inside the sync's transaction)."""
    db.execute("INSERT INTO gradebook_canvas_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), term_id, request_ref, FORMAT, built["courses"], built["submissions"], built["size"], built["sha256"],
                built["data"], timestamp()))


def load(db, term_id, request_id):
    """What Canvas held when the term's sync `request_id` was recorded (None when that sync kept no copy), checked against its
    size and hash."""
    row = db.execute("""SELECT s.size,s.sha256,s.data FROM gradebook_canvas_snapshots s JOIN gradebook_requests r ON r.id = s.request_ref
        WHERE r.term_id=? AND r.request_id=?""", (term_id, request_id)).fetchone()
    if row is None:
        return None
    text = gzip.decompress(row["data"])
    if len(text) != row["size"] or hashlib.sha256(text).hexdigest() != row["sha256"]:
        raise ValueError("The Canvas copy kept with this sync does not match its hash.")
    return json.loads(text)
