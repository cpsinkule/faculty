"""Exam-day packets (requirement, 2026-09-30; AGENTS.md "Exam-day packet"): "One print per room: seat chart, room roster, DR-center
list, how many copies of each version plus extras, proctor instructions, and which TA proctors where."

Owner: the COURSE CODE + TERM/YEAR level (`course_terms.id`) for one course-wide assignment (`assignments.id`), keyed by that pair
because exam records are often made after the exam. A plan is one immutable revision (`exam_day_plans`, append-only, the highest
revision counts) holding the proctor instructions and each room's proctors, keyed by the id of one of the term's seating charts
for the assignment. Proctors are picked from the TA lists of the chart's sections (or the term's other sections) or typed in; the
plan keeps the name, email and phone it was given, so a later TA-list edit never changes it. "Start from" another plan's
instructions only fills the page's form: nothing propagates between assessments or terms.

The packet itself is a read (`packet`): the chart page prints it with the chart, and nothing here creates a print job, a file or a
delivery. Its room roster is `seating_roster.roster_snapshot`, the Print room roster's own read, with its checks and its UINs; no
other answer of this module names a UIN.

Lock order: the plan page's read is catalog-only and takes no lock; the packet takes the roster lifecycle lock for the chart's
verified roster read, as Print room roster does; a save takes the roster lifecycle lock, then ONE `BEGIN IMMEDIATE` checking the
request ID (the same ID and plan returns what it did; another plan is a 409) and `base_revision` (409 when the plan changed since the
page read it). An identical plan records nothing.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import encoded as _encoded
from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect
from seating import term_label
from seating_roster import roster_snapshot
from teaching_assistants.service import CONTROL, EMAIL, PHONE, contact_lists

MAX_INSTRUCTIONS = 8000
MAX_PROCTORS = 12
LIMITS = {"name": 120, "email": 254, "phone": 40}
# Tabs and line breaks are the instructions' own layout; any other control character cannot be printed.
TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Shown until the instructor saves a plan of their own (and offered again by Reset to default).
DEFAULT_INSTRUCTIONS = """Before the exam
- Arrive at least 15 minutes early. Check the room against the seating chart, and count the copies of each version and the extras against the cover page.
- Seat each student at the seat the chart gives them. Nobody sits anywhere else or changes seats.
- Check each student's photo ID against the room roster as they are seated.

Handing out the exam
- Give each student the exam version the seating chart marks for their seat.
- Students testing at the DR center take the exam there, not in this room. They are listed on the room roster for reference only.

During the exam
- Watch the whole room, keep it quiet, and announce the time remaining near the end.

Collecting the papers
- Collect every paper, including the unused extras and any spoiled copies, and count them against the cover page before you leave.

Problems
- A student who is not on the room roster, a wrong or missing version, a missing ID, or any incident: contact the instructor right away and write down what happened."""


def clean_instructions(value):
    """The instructions as stored: line breaks unified, trailing spaces of each line and blank lines at either end dropped."""
    if not isinstance(value, str):
        raise ValueError("Send the proctor instructions as text.")
    text = "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip("\n")
    if TEXT_CONTROL.search(text):
        raise ValueError("The proctor instructions hold a character that cannot be printed.")
    if len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"The proctor instructions are longer than {MAX_INSTRUCTIONS:,} characters.")
    return text


def clean_proctors(value):
    """Each room's proctors as stored: {chart_id: [{name, email, phone}]}, rooms without proctors left out. Whether each chart is a
    room of this exam is checked in the save's transaction."""
    if not isinstance(value, dict):
        raise ValueError("Send the proctors as an object of rooms.")
    kept = {}
    for chart_id, people in value.items():
        if not isinstance(chart_id, str) or not 1 <= len(chart_id) <= 100:
            raise ValueError("A room in this plan is not named by its seating chart.")
        if not isinstance(people, list):
            raise ValueError("Send each room's proctors as a list.")
        if len(people) > MAX_PROCTORS:
            raise ValueError(f"A room can have at most {MAX_PROCTORS} proctors.")
        room, seen = [], set()
        for number, person in enumerate(people, 1):
            if not isinstance(person, dict):
                raise ValueError(f"Proctor {number} is not a set of contact details.")
            entry = {}
            for field, limit in LIMITS.items():
                text = person.get(field) or ""
                if not isinstance(text, str):
                    raise ValueError(f"Proctor {number}'s {field} must be text.")
                text = text.strip() if field == "email" else " ".join(text.split())
                if CONTROL.search(text):
                    raise ValueError(f"Proctor {number}'s {field} holds a character that cannot be kept.")
                if len(text) > limit:
                    raise ValueError(f"Proctor {number}'s {field} is longer than {limit} characters.")
                entry[field] = text
            if not entry["name"]:
                raise ValueError(f"Give proctor {number} a name.")
            if entry["email"] and not EMAIL.fullmatch(entry["email"]):
                raise ValueError(f"{entry['name']}'s email address does not look like one (name@tamu.edu).")
            if entry["phone"] and (not PHONE.fullmatch(entry["phone"]) or not re.search(r"\d", entry["phone"])):
                raise ValueError(f"{entry['name']}'s phone number can hold digits, spaces and + ( ) - . / # , : only.")
            key = f"e:{entry['email'].casefold()}" if entry["email"] else f"n:{entry['name'].casefold()}"
            if key in seen:
                raise ValueError(f"{entry['name']} is listed twice for the same room.")
            seen.add(key)
            room.append(entry)
        if room:
            kept[chart_id] = room
    return kept


def _assessment(db, assignment_id, term_id):
    """The assignment and term of a plan: the assignment must belong to the term's course."""
    assignment = db.execute("""SELECT a.id,a.title,a.definition_id,d.code FROM assignments a JOIN course_definitions d ON d.id=a.definition_id
        WHERE a.id=?""", (str(assignment_id),)).fetchone()
    term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (str(term_id),)).fetchone()
    if not assignment or not term:
        raise NotFound("That assignment or term was not found.")
    if assignment["definition_id"] != term["definition_id"]:
        raise NotFound("That assignment belongs to another course than the term.")
    return {"assignment": {"id": assignment["id"], "title": assignment["title"], "code": assignment["code"]},
            "term": {"id": term["id"], "term_label": term_label(term), "is_current": bool(term["is_current"])},
            "_definition_id": term["definition_id"], "_listed": set(json.loads(term["sections"] or "[]"))}


def _latest(db, term_id, assignment_id):
    return db.execute("SELECT * FROM exam_day_plans WHERE term_id=? AND assignment_id=? ORDER BY revision DESC LIMIT 1",
                      (term_id, assignment_id)).fetchone()


def _charts(db, assignment_id, term_id):
    """The term's seating charts of the assignment: id -> room name."""
    return {row["id"]: row["name"] for row in db.execute("""SELECT c.id,r.name FROM seating_charts c JOIN rooms r ON r.id=c.room_id
        WHERE c.assignment_id=? AND c.term_id=?""", (assignment_id, term_id))}


def printing(db, chart_id):
    """What the chart's current print jobs (neither cancelled nor superseded) printed, section by section: copies, and extras (copies
    printed for no student). Per section the latest current job counts; a section with more than one current job is named in
    `duplicates`, never added up. None when the chart has no current print job: nothing is guessed."""
    rows = [dict(row) for row in db.execute("""SELECT b.id,b.status,b.quantity,b.created_at,s.number AS section_number,v.label AS version_label,
            (SELECT COUNT(*) FROM printed_copies c WHERE c.print_batch_id=b.id AND c.enrollment_id IS NULL) AS extras
        FROM print_batches b JOIN course_sections s ON s.id=b.section_id JOIN assignment_versions v ON v.id=b.assignment_version_id
        WHERE b.seating_chart_id=? AND b.status!='cancelled' AND b.superseded_at IS NULL
        ORDER BY b.created_at DESC, b.id""", (chart_id,))]
    if not rows:
        return None
    latest, duplicates = {}, {}
    for row in rows:
        if row["section_number"] in latest:
            duplicates[row["section_number"]] = duplicates.get(row["section_number"], 1) + 1
        else:
            latest[row["section_number"]] = row
    sections = [{"section_number": number, "copies": row["quantity"], "extras": row["extras"], "status": row["status"],
                 "version_label": row["version_label"], "created_at": row["created_at"]} for number, row in sorted(latest.items())]
    return {"sections": sections, "copies": sum(item["copies"] for item in sections), "extras": sum(item["extras"] for item in sections),
            "ready": all(item["status"] == "ready" for item in sections),
            "duplicates": [{"section_number": number, "jobs": count} for number, count in sorted(duplicates.items())]}


class ExamDayService:
    def __init__(self, rosters, seating):
        self.rosters, self.seating, self.path = rosters, seating, rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "exam_day", migration.stem, migration.read_text(encoding="utf-8"))

    # ----- the plan page ----------------------------------------------------------------------------------------------

    def plan(self, assignment_id, term_id):
        """The plan page: the saved plan (or the default instructions), each room with its counts, proctors and the TAs to pick
        from, and the course's other plans to start the instructions from. Catalog only."""
        with connect(self.path) as db:
            return self._view(db, _assessment(db, assignment_id, term_id))

    def _view(self, db, scope):
        assignment_id, term_id = scope["assignment"]["id"], scope["term"]["id"]
        current = _latest(db, term_id, assignment_id)
        stored = json.loads(current["proctors_json"]) if current else {}
        charts = [item for chart_id in _charts(db, assignment_id, term_id) for item in self.seating.list(db, chart_id)]
        charts.sort(key=lambda item: (item["exam_date"] or "9999", item["exam_time"] or "", item["room_name"].casefold(), item["id"]))
        tas = self._term_tas(db, term_id, scope["_listed"], {sid for item in charts for sid in item["section_ids"]})
        rooms = []
        for item in charts:
            sections = item["section_ids"]
            marks = ",".join("?" * len(sections)) or "''"
            current_members = {row[0] for row in db.execute(f"SELECT enrollment_id FROM roster_current WHERE section_id IN ({marks})", sections)}
            dr = self.seating.links.excluded(db, sections, assignment_id) & current_members
            lettered = sum(item["version_totals"].values())
            printed = printing(db, item["id"])
            rooms.append({"chart_id": item["id"], "room_name": item["room_name"], "exam_date": item["exam_date"], "exam_time": item["exam_time"],
                          "sections": item["sections"], "confirmed": bool(item["confirmed_at"]), "check_ok": item["check"]["ok"],
                          "seated": item["seated_count"], "dr_count": len(dr), "version_count": item["exam_version_count"],
                          "version_totals": item["version_totals"],
                          "unassigned_versions": max(0, item["seated_count"] - lettered) if item["exam_version_count"] else 0,
                          "copies": printed["copies"] if printed else None, "extras": printed["extras"] if printed else None,
                          "printing": printed, "proctors": stored.get(item["id"], []), "ta_options": self._options(tas, sections)})
        event = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (term_id, assignment_id)).fetchone()
        others = [{"assignment_id": row["assignment_id"], "term_id": row["term_id"], "title": row["title"], "term_label": term_label(row),
                   "instructions": row["instructions"]}
                  for row in db.execute("""SELECT p.assignment_id,p.term_id,p.instructions,a.title,t.term,t.term_other,t.year
                      FROM exam_day_plans p JOIN assignments a ON a.id=p.assignment_id JOIN course_terms t ON t.id=p.term_id
                      WHERE a.definition_id=? AND t.deleted_at IS NULL AND NOT (p.assignment_id=? AND p.term_id=?) AND p.instructions!=''
                      AND p.revision=(SELECT MAX(q.revision) FROM exam_day_plans q WHERE q.term_id=p.term_id AND q.assignment_id=p.assignment_id)
                      ORDER BY t.year DESC, t.created_at DESC, a.title COLLATE NOCASE""", (scope["_definition_id"], assignment_id, term_id))]
        return {"assignment": scope["assignment"], "term": scope["term"],
                "exam_href": f"#exams/{event['id']}" if event else f"#exams/assessment/{assignment_id}/{term_id}",
                "revision": current["revision"] if current else 0, "saved_at": current["created_at"] if current else None,
                "instructions": current["instructions"] if current else DEFAULT_INSTRUCTIONS,
                "default_instructions": DEFAULT_INSTRUCTIONS,
                "proctors": {room["chart_id"]: room["proctors"] for room in rooms if room["proctors"]},
                "rooms": rooms, "others": others}

    def _term_tas(self, db, term_id, listed, chart_sections):
        """Each section's TA list (teaching_assistants' own read): the sections a chart seats, and the term's other sections an
        offering still lists (the TA page's own Copy from rule)."""
        result = {}
        sections = db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number", (term_id,)).fetchall()
        contacts_by_section = contact_lists(db, [row["id"] for row in sections])
        for row in sections:
            if row["id"] in chart_sections or row["number"] in listed:
                contacts = contacts_by_section.get(row["id"], [])
                if contacts:
                    result[row["id"]] = (row["number"], contacts)
        return result

    @staticmethod
    def _options(tas, own_sections):
        """The TAs a room's proctors are picked from: those of the room's own sections first, then the term's other sections, each
        person once (by email, else by name) with every section that lists them."""
        options = {}
        for own in (True, False):
            for section_id, (number, contacts) in tas.items():
                if (section_id in own_sections) != own:
                    continue
                for ta in contacts:
                    key = f"e:{ta['email'].casefold()}" if ta.get("email") else f"n:{ta['name'].casefold()}"
                    entry = options.setdefault(key, {"name": ta["name"], "email": ta.get("email", ""), "phone": ta.get("phone", ""),
                                                     "sections": [], "own": own})
                    if number not in entry["sections"]:
                        entry["sections"].append(number)
        return sorted(options.values(), key=lambda item: (not item["own"], item["name"].casefold()))

    def save(self, assignment_id, term_id, data, actor="workspace"):
        """Records the plan as its next revision. `base_revision` names the revision the page showed (0 for none)."""
        data = data if isinstance(data, dict) else {}
        instructions = clean_instructions(data.get("instructions"))
        proctors = clean_proctors(data.get("proctors", {}))
        base = data.get("base_revision")
        if type(base) is not int or base < 0:
            raise ValueError("Name the plan the page showed as base_revision (0 for none).")
        request_id = valid_request_id(data.get("request_id"))
        encoded = _encoded(proctors)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            scope = _assessment(db, assignment_id, term_id)
            aid, tid = scope["assignment"]["id"], scope["term"]["id"]
            done = db.execute("SELECT instructions,proctors_json FROM exam_day_plans WHERE term_id=? AND assignment_id=? AND request_id=?",
                              (tid, aid, request_id)).fetchone()
            if done:
                if (done["instructions"], done["proctors_json"]) != (instructions, encoded):
                    raise Conflict("This save was already made with a different plan. Reload the page and save again.")
                return self._view(db, scope)
            current = _latest(db, tid, aid)
            if (current["revision"] if current else 0) != base:
                raise Conflict("This exam-day plan was changed elsewhere. Load the saved plan, then make your changes again.")
            charts = _charts(db, aid, tid)
            for chart_id in proctors:
                if chart_id not in charts:
                    if db.execute("SELECT 1 FROM seating_charts WHERE id=?", (chart_id,)).fetchone():
                        raise ValueError("A room in this plan is a seating chart of another exam or term.")
                    raise Conflict("A room in this plan no longer has a seating chart for this exam. Load the saved plan and save again.")
            if not current or (current["instructions"], current["proctors_json"]) != (instructions, encoded):
                db.execute("INSERT INTO exam_day_plans VALUES(?,?,?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), tid, aid, base + 1, instructions, encoded, request_id, actor, timestamp()))
            return self._view(db, scope)

    # ----- one room's packet -----------------------------------------------------------------------------------------

    def packet(self, chart_id):
        """Everything one room's printed packet holds besides the seat chart the chart page draws: the room roster and DR list
        (Print room roster's own read and checks), the copies of each exam version from the chart's saved letters, the extras and
        copies of its current print jobs, the plan's instructions and this room's proctors. `fingerprint` covers all of it, and
        `chart_version` lets the page see that the chart it shows is the one read here."""
        with self.rosters.lock(), connect(self.path) as db:
            chart = self.seating.get(chart_id, db)
            roster = roster_snapshot(self.seating, db, chart)
            plan = _latest(db, chart["term_id"], chart["assignment_id"])
            printed = printing(db, chart["id"])
        versions = chart["exam_versions"]
        warnings = []
        if versions["count"] and versions["unassigned"]:
            warnings.append(f"{len(versions['unassigned'])} seated student{'s have' if len(versions['unassigned']) != 1 else ' has'} "
                            "no exam version letter yet. Assign one on the chart before printing.")
        if versions["count"] and versions["conflicts"]:
            warnings.append(f"{len(versions['conflicts'])} pair{'s' if len(versions['conflicts']) != 1 else ''} of neighbours in a row "
                            "share a version letter.")
        if printed is None:
            warnings.append("No print jobs from this seating chart yet, so the extras are not known.")
        else:
            for item in printed["duplicates"]:
                warnings.append(f"Section {item['section_number']} has {item['jobs']} current print jobs from this chart; the latest is "
                                "counted. Supersede or cancel the ones that will not be used.")
            waiting = [str(item["section_number"]) for item in printed["sections"] if item["status"] != "ready"]
            if waiting:
                many = len(waiting) != 1
                warnings.append(f"The print job{'s' if many else ''} of Section{'s' if many else ''} {', '.join(waiting)} "
                                f"{'are' if many else 'is'} not ready yet.")
            named = printed["copies"] - printed["extras"]
            if named != len(roster["room_students"]):
                warnings.append(f"The print jobs hold {named} cop{'ies' if named != 1 else 'y'} for named students, but the room now seats "
                                f"{len(roster['room_students'])}. Check the seating or print again.")
        result = {"chart_id": chart["id"], "chart_version": chart["version"], "assignment_id": chart["assignment_id"], "term_id": chart["term_id"],
                  **{key: chart[key] for key in ("title", "code", "term_label", "room_name", "sections", "exam_date", "exam_time")},
                  "confirmed": bool(chart["confirmed_at"]), "seated": len(roster["room_students"]), "dr_count": len(roster["dr_students"]),
                  "versions": {"count": versions["count"], "counts": versions["counts"], "unassigned": len(versions["unassigned"]),
                               "conflicts": len(versions["conflicts"])} if versions["count"] else None,
                  "printing": printed, "plan_revision": plan["revision"] if plan else 0,
                  "instructions": plan["instructions"] if plan else DEFAULT_INSTRUCTIONS, "default_instructions": plan is None,
                  "proctors": json.loads(plan["proctors_json"]).get(chart["id"], []) if plan else [],
                  "plan_href": f"#exam-day/{chart['assignment_id']}/{chart['term_id']}", "roster": roster, "warnings": warnings}
        result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        return result
