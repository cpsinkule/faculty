"""How long a quiz makeup takes, for scheduling (ABSENCES "MAKEUP BOOKING" LENGTH; requirement, 2026-10-05).

The multiplier is the accommodation column's own name ("Extra Time 1.50x", "Extra Time 1.75x"; the value says whether it is granted, by
the ACCOMMODATIONS rule `AccommodationService.active`); a student with several takes the largest. Read only, from the student's current
accommodation record for that section (ACCOMMODATIONS: never another course's, never a name)."""
from __future__ import annotations

import json
import math
import re

from .makeup_slots import QUIZ_MINUTES

EXTRA = re.compile(r"\bextra\s+time\s+(\d+(?:\.\d+)?)\s*x\b", re.I)
INACTIVE = ("", "no", "false", "0", "n")  # AccommodationService.active


def multiplier(values):
    """The largest granted "Extra Time N.NNx" in a snapshot's values, else 1."""
    found = [float(m.group(1)) for name, value in (values or {}).items()
             if str(value).strip().casefold() not in INACTIVE and (m := EXTRA.search(str(name)))]
    return max([m for m in found if 1 <= m <= 4] or [1.0])


def minutes_for(factor):
    return math.ceil(QUIZ_MINUTES * factor)


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def student_minutes(db, section_id, enrollment_id):
    """One student's quiz length in that section: 20 minutes times their extra time (20, 30, 35 ...)."""
    if not section_id or not enrollment_id or not _table(db, "student_accommodations"):
        return QUIZ_MINUTES
    row = db.execute("""SELECT r.snapshot_json FROM student_accommodations a JOIN accommodation_revisions r ON r.id=a.revision_id
        WHERE a.section_id=? AND a.enrollment_id=?""", (section_id, enrollment_id)).fetchone()
    return minutes_for(multiplier(json.loads(row[0]).get("values"))) if row else QUIZ_MINUTES


def sitting_minutes(db, sitting_id):
    """A makeup sitting's length: its longest participant's quiz length (students share a sitting), 20 minutes with none."""
    if not _table(db, "exam_participants"):
        return QUIZ_MINUTES
    rows = db.execute("SELECT section_id, enrollment_id FROM exam_participants WHERE sitting_id=?", (sitting_id,)).fetchall()
    return max([student_minutes(db, row[0], row[1]) for row in rows] or [QUIZ_MINUTES])
