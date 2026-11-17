"""DIFFERS FROM GRADING (requirement, 2026-10-07). GRADEBOOK "DIFFERS FROM GRADING"; README "Checking against grading".

For each live column linked to grading that grades were brought into (a pull, or a certification's write, recorded its request),
every student whose cell differs from what grading's final grades say NOW, by the pull's own rule (`sources.totals`, then
`sources.plan` against the cells with no Replace ticked):

  changed  the cell holds what a pull, a certification or an import recorded, or is empty, and grading's final grade differs
           (`add`, `update`);
  typed    a value typed in the gradebook, or a cell cleared there, that grading's grade differs from (`kept`): it stays until a
           pull or a certification is told to replace it.

A student grading has no final grade for (a paper being regraded, no work, a DR exam ahead, an absence) is never a difference: the
cell keeps what was recorded and nothing is guessed. Each column says how its cells are brought in line (`fix`): `certify` when
grading changed after the plan's latest certification (a pull reads the certification, so only certifying again records the
change), else `pull` (a certified plan's pull restores its certified values; a plan never certified is pulled live), and nothing
when only typed values differ.

Reading grading live takes seconds on a big exam (`final_decisions`), so it is read only where something could differ, decided from
catalog rows alone (no grading rule run):
  - a certified plan: the gradebook's hint (`grade_marker`, the View's `may_have_changed`) or, for an Exams-page part, a
    finalization or release after the certification. Otherwise grading still says what was certified, and the cells are compared
    with the certification itself (catalog rows, as a pull of it reads them);
  - a plan never certified: grading changed after a section's last pull, or a cell of the column holds a value typed or imported
    (what grading says of a value a pull kept is recorded nowhere).
Columns grades were never brought into (no pull and no certification for a section) are left out for that section: the Pull icon's
mark says so already.

Reads only. Holds the roster lifecycle lock for the verified roster payload and `sources.totals`, and reads on an open catalog
connection outside any transaction, as a pull preview does. The answer names only the columns that differ.
"""
import json

from rosters import connect

from . import setup as setups, sources
from .service import _cells, _setup

KINDS = {"add": "changed", "update": "changed", "kept": "typed"}


def _instant(value):
    return sources._instant(value) if value else None


def _pulls(db, term_id):
    """{item_id: {section_id or "": latest pull time}}: when grades were last brought into each column, term-wide ("") or for one
    section (a pull started from a section tab covers only that section). A certification's write is a term-wide pull request."""
    found = {}
    for row in db.execute("SELECT created_at,summary_json FROM gradebook_requests WHERE term_id=? AND kind='pull'", (term_id,)):
        summary = json.loads(row["summary_json"])
        if not summary.get("item_id"):
            continue
        at = _instant(row["created_at"])
        scope = found.setdefault(summary["item_id"], {})
        key = summary.get("section_id") or ""
        if at and (scope.get(key) is None or at > scope[key]):
            scope[key] = at
    return found


def _brought_in(pulls, section_id):
    """When grades were last brought into this section's rows of a column, or None."""
    times = [at for at in (pulls.get(""), pulls.get(section_id)) if at]
    return max(times) if times else None


def _totals(service, db, term_id, item, source, students, cells, pulls, sections):
    """(totals or None, fix): what the column's cells are compared with, or None when nothing can differ (see the module's doc)."""
    parts = item["link"]["parts"]
    certification = sources.latest_certification(db, source.get("plan_id")) if source.get("plan_id") else None
    rows = [{key: student[key] for key in ("row", "section_id", "enrollment_id", "section")} for student in students]
    if certification:
        hint = service._certification_hint(db, source, parts) or {}
        changed = bool(hint.get("may_have_changed"))
        if not changed and "exams" in parts:
            exams = _instant(sources.live_changed_at(db, source, ["exams"]))
            changed = bool(exams and exams > _instant(certification["created_at"]))
        if changed:
            return sources.totals(service.frq, db, term_id, item["assignment_id"], parts, rows=rows), "certify"
        ids = sources.certified_cells(db, term_id, item["id"])
        clear = {row for row, cell in cells.items() if cell["id"] in ids}
        return sources.certified(db, source["plan_id"], parts, clear=clear), "pull"
    live = _instant(sources.live_changed_at(db, source, parts))
    behind = bool(live) and any(_brought_in(pulls, section) < live for section in sections)
    by_hand = any(cell["source"] in ("instructor", "import") for cell in cells.values())
    if not behind and not by_hand:
        return None, "pull"
    return sources.totals(service.frq, db, term_id, item["assignment_id"], parts, rows=rows), "pull"


def _value(value):
    return {"status": value["status"], "points": value.get("points")} if value else None


def read(service, term_id, section_id=None):
    """`GET /api/gradebook/terms/<term>/grading-check[?section=<id>]`: {term_id, section_id, columns: {item_id: {fix, plan_id,
    certified_at, rows: [{row, section_id, kind, cell: {id, status, points, source} | null, grading: {status, points}}],
    elsewhere: {changed, typed}}}}. Grading is read term-wide (a certification is the term's), so a section's answer lists its own
    students and counts the rest under `elsewhere`; without a section every student is listed."""
    with service.rosters.lock():
        context = service._context(term_id)
        scoped = service._scoped(context, section_id)
        shown = {student["row"] for student in scoped["students"]}
        with connect(service.path) as db:
            setup, meta = _setup(db, term_id)
            cells = _cells(db, term_id)
            available = sources.available(db, term_id)
            pulls = _pulls(db, term_id)
            columns = {}
            for item in setup.get("items", []):
                if item.get("archived") or (item.get("link") or {}).get("kind") != "grading" or not item.get("assignment_id"):
                    continue
                if item.get("canvas") in setups.OWNED or (section_id and not setups.applies(item, section_id)):
                    continue
                source = available.get(item["assignment_id"]) or {}
                scope = pulls.get(item["id"]) or {}
                students = [student for student in context["students"] if setups.applies(item, student["section_id"])
                            and _brought_in(scope, student["section_id"])]
                if not source.get("event_id") or not students:
                    continue
                column = cells.get(item["id"], {})
                sections = sorted({student["section_id"] for student in students})
                totals, fix = _totals(service, db, term_id, item, source, students, column, scope, sections)
                if totals is None:
                    continue
                plan = sources.plan(item, totals, students=[{key: student[key] for key in ("row", "section_id", "section", "name")} for student in students],
                                    cells=column, options={"replace": set(), "absent_as_missing": False}, revision=meta["revision"])
                section_of = {student["row"]: student["section_id"] for student in students}
                rows, elsewhere = [], {"changed": 0, "typed": 0}
                for row in plan["rows"]:
                    kind = KINDS.get(row["action"])
                    if not kind:
                        continue
                    if row["row"] not in shown:
                        elsewhere[kind] += 1
                        continue
                    current = row["current"]
                    rows.append({"row": row["row"], "section_id": section_of[row["row"]], "kind": kind,
                                 "cell": {key: current[key] for key in ("id", "status", "points", "source")} if current else None,
                                 "grading": _value(row["proposed"])})
                if not rows and not any(elsewhere.values()):
                    continue
                certification = sources.latest_certification(db, source.get("plan_id")) if source.get("plan_id") else None
                columns[item["id"]] = {"fix": fix if any(entry["kind"] == "changed" for entry in rows) or elsewhere["changed"] else None,
                                       "plan_id": source.get("plan_id"), "certified_at": certification["created_at"] if certification else None,
                                       "rows": rows, **({"elsewhere": elsewhere} if section_id else {})}
    return {"term_id": term_id, "section_id": section_id or "", "columns": columns}
