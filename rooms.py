"""Campus rooms and their seating charts.

A room is a workspace-level physical resource: it belongs to no course code,
term, or section. Its seats keep a stable identity by label across chart
re-uploads, so seat conditions (broken, left-handed) and any future references
survive a corrected workbook. Charts are stored in the shared catalog only; the
uploaded workbook itself is not retained.
"""

import hashlib
import json
import uuid
from functools import lru_cache, partial

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from request_limits import json_object
from room_corrections import CORRECTIONS
from room_import import MAX_UPLOAD, parse_seating_chart
from room_sources import MAX_ISSUES, apply_issues, issue_document, validate_layout
from rosters import connect

CONDITIONS = ("broken", "left_handed", "blocked")
STALE = "This room changed on another device. Reload it to get the latest version."
# A room's room_imports row with this filename is the receipt of its reviewed corrections (AGENTS.md "Rooms and seating charts").
CORRECTIONS_LOG = "Reviewed room corrections"
CORRECTIONS_REVIEWED = "2026-10-07"


SCHEMA = """
    CREATE TABLE IF NOT EXISTS rooms (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
        sheet TEXT NOT NULL DEFAULT '', grid_rows INTEGER NOT NULL DEFAULT 0, grid_columns INTEGER NOT NULL DEFAULT 0,
        landmarks TEXT NOT NULL DEFAULT '[]', source_filename TEXT NOT NULL DEFAULT '',
        source_sha256 TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS rooms_name ON rooms(name COLLATE NOCASE);
    CREATE TABLE IF NOT EXISTS room_seats (
        id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id),
        label TEXT NOT NULL, display TEXT NOT NULL DEFAULT '',
        grid_row INTEGER NOT NULL CHECK(grid_row >= 1), grid_column INTEGER NOT NULL CHECK(grid_column >= 1),
        row_span INTEGER NOT NULL DEFAULT 1 CHECK(row_span >= 1), column_span INTEGER NOT NULL DEFAULT 1 CHECK(column_span >= 1),
        row_label TEXT NOT NULL DEFAULT '', seat_number INTEGER,
        group_label TEXT NOT NULL DEFAULT '', group_size INTEGER, seat_index INTEGER,
        broken INTEGER NOT NULL DEFAULT 0 CHECK(broken IN (0, 1)),
        left_handed INTEGER NOT NULL DEFAULT 0 CHECK(left_handed IN (0, 1)),
        blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0, 1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(room_id, label COLLATE NOCASE)
    );
    CREATE INDEX IF NOT EXISTS room_seats_room ON room_seats(room_id, grid_row, grid_column);
    CREATE TABLE IF NOT EXISTS room_imports (
        id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id),
        filename TEXT NOT NULL, sha256 TEXT NOT NULL, sheet TEXT NOT NULL,
        seat_count INTEGER NOT NULL, added INTEGER NOT NULL, removed INTEGER NOT NULL,
        kept INTEGER NOT NULL, imported_at TEXT NOT NULL,
        chart_broken INTEGER NOT NULL DEFAULT 0, warnings TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS room_imports_room ON room_imports(room_id, imported_at);
"""


def initialize_rooms(path):
    with connect(path) as db:
        # The first seat schema stored one unlabeled cell per seat. Rebuild it so
        # seats carry their row letter, table membership, spans, and display text.
        legacy = {row["name"] for row in db.execute("PRAGMA table_info(room_seats)")}
        if legacy and "seat_index" not in legacy:
            db.execute("ALTER TABLE room_seats RENAME TO room_seats_v1")
        db.executescript(SCHEMA)
        if legacy and "seat_index" not in legacy:
            db.execute("""INSERT INTO room_seats (id, room_id, label, display, grid_row, grid_column, broken, left_handed, created_at, updated_at)
                          SELECT id, room_id, label, label, grid_row, grid_column, broken, left_handed, created_at, updated_at FROM room_seats_v1""")
            db.execute("DROP TABLE room_seats_v1")
        seats = {row["name"] for row in db.execute("PRAGMA table_info(room_seats)")}
        if "blocked" not in seats:
            # Blocked: usable furniture the instructor does not want assigned.
            db.execute("ALTER TABLE room_seats ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0, 1))")
        if "notes" not in seats:
            db.execute("ALTER TABLE room_seats ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        room_columns = {row["name"] for row in db.execute("PRAGMA table_info(rooms)")}
        for column, definition in (("layout_json", "TEXT NOT NULL DEFAULT '{}'"), ("review_required", "TEXT NOT NULL DEFAULT '[]'")):
            if column not in room_columns:
                db.execute(f"ALTER TABLE rooms ADD COLUMN {column} {definition}")
        imports = {row["name"] for row in db.execute("PRAGMA table_info(room_imports)")}
        for column, definition in (("chart_broken", "INTEGER NOT NULL DEFAULT 0"), ("warnings", "TEXT NOT NULL DEFAULT '[]'"),
                                   ("issue_source", "TEXT NOT NULL DEFAULT '{}'")):
            if column not in imports:
                db.execute(f"ALTER TABLE room_imports ADD COLUMN {column} {definition}")


def clean(value, label, maximum, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = " ".join(value.split()) if label == "Room name" else value.strip()
    if required and not value:
        raise ValueError(f"{label} is required.")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return value


def safe_filename(name):
    name = " ".join((name or "").replace("\\", "/").rsplit("/", 1)[-1].split())
    return name[:200] or "seating-chart.xlsx"


def seat_runs(seats):
    """Physical runs of seats: touching seats along one row, or the seats of one table."""
    rows, tables = {}, {}
    for seat in seats:
        if seat.get("group"):
            tables.setdefault(seat["group"], []).append(seat)
        else:
            rows.setdefault(seat["row"], []).append(seat)
    runs = []
    for row_seats in rows.values():
        ordered = sorted(row_seats, key=lambda seat: seat["column"])
        run = [ordered[0]]
        for previous, seat in zip(ordered, ordered[1:]):
            if previous["column"] + (previous.get("column_span") or 1) == seat["column"]:
                run.append(seat)
            else:
                runs.append(("row", run))
                run = [seat]
        runs.append(("row", run))
    for members in tables.values():
        runs.append(("table", members))
    return runs


def seat_neighbors(seats):
    """Seat id -> ids of seats directly beside it (same row, touching) or at the same table."""
    neighbors = {seat["id"]: set() for seat in seats}
    for kind, run in seat_runs(seats):
        pairs = zip(run, run[1:]) if kind == "row" else ((a, b) for a in run for b in run if a is not b)
        for a, b in pairs:
            neighbors[a["id"]].add(b["id"])
            neighbors[b["id"]].add(a["id"])
    return neighbors


def spaced_capacity(seats):
    """How many of these seats can be used with an empty seat between neighbours (one per table)."""
    return sum(1 if kind == "table" else (len(run) + 1) // 2 for kind, run in seat_runs(seats))


@lru_cache(maxsize=256)
def _spaced_count(geometry):
    """`spaced_capacity` of seats given as (row, column, column_span, group), in their order: exactly what `seat_runs` reads, so
    the answer is the same, and a room whose usable seats did not change is not counted again (Rooms pages poll the list)."""
    return spaced_capacity([{"row": row, "column": column, "column_span": span, "group": group} for row, column, span, group in geometry])


def summary(row, detailed=True):
    result = dict(row)
    result["landmarks"] = json.loads(result.pop("landmarks"))
    result["layout"] = json.loads(result.pop("layout_json", "{}"))
    if not detailed and result["layout"]:
        result["layout"] = {key: result["layout"][key] for key in ("kind", "width", "height")}
    result["review_required"] = json.loads(result.get("review_required", "[]"))
    return result


def stored_seat(seat):
    """What a correction may change of a `room_seats` row, to compare with `corrected_seat`."""
    return (seat["label"], seat["display"], seat["grid_row"], seat["grid_column"], seat["row_label"], seat["seat_number"],
            bool(seat["broken"]), bool(seat["blocked"]), seat["notes"])


def corrected_seat(seat):
    return (seat["label"], seat["display"], seat["row"], seat["column"], seat["row_label"], seat["number"],
            seat["broken"], seat["blocked"], seat["notes"])


class RoomService:
    def __init__(self, path):
        self.path = path
        initialize_rooms(path)

    def list(self, db=None):
        if db is None:
            with connect(self.path) as db:
                return self.list(db)
        rows = db.execute("""SELECT r.*, COUNT(s.id) AS seat_count,
                                    COALESCE(SUM(s.broken), 0) AS broken_count,
                                    COALESCE(SUM(s.left_handed), 0) AS left_handed_count,
                                    COALESCE(SUM(s.blocked), 0) AS blocked_count,
                                    COALESCE(SUM(CASE WHEN s.broken = 0 AND s.blocked = 0 THEN 1 ELSE 0 END), 0) AS usable_count
                             FROM rooms r LEFT JOIN room_seats s ON s.room_id = r.id
                             GROUP BY r.id ORDER BY r.name COLLATE NOCASE, r.id""").fetchall()
        usable = {}
        for seat in db.execute("""SELECT room_id, id, grid_row AS row, grid_column AS "column", column_span, group_label AS "group"
                                  FROM room_seats WHERE broken = 0 AND blocked = 0"""):
            usable.setdefault(seat["room_id"], []).append((seat["row"], seat["column"], seat["column_span"], seat["group"]))
        return [{**summary(row, detailed=False), "spaced_count": _spaced_count(tuple(usable.get(row["id"], ())))} for row in rows]

    def get(self, room_id, db=None, seats=True):
        if db is None:
            with connect(self.path) as db:
                return self.get(room_id, db, seats)
        row = db.execute("""SELECT r.*, COUNT(s.id) AS seat_count,
                                   COALESCE(SUM(s.broken), 0) AS broken_count,
                                   COALESCE(SUM(s.left_handed), 0) AS left_handed_count,
                                   COALESCE(SUM(s.blocked), 0) AS blocked_count,
                                   COALESCE(SUM(CASE WHEN s.broken = 0 AND s.blocked = 0 THEN 1 ELSE 0 END), 0) AS usable_count
                            FROM rooms r LEFT JOIN room_seats s ON s.room_id = r.id
                            WHERE r.id = ? GROUP BY r.id""", (room_id,)).fetchone()
        if row is None:
            raise NotFound("This room no longer exists.")
        result = summary(row)
        result["spaced_count"] = spaced_capacity([dict(seat) | {"row": seat["grid_row"], "column": seat["grid_column"], "group": seat["group_label"]}
                                                  for seat in db.execute("SELECT * FROM room_seats WHERE room_id = ? AND broken = 0 AND blocked = 0", (room_id,))])
        if seats:
            result["seats"] = [
                {"id": seat["id"], "label": seat["label"], "display": seat["display"] or seat["label"],
                 "row": seat["grid_row"], "column": seat["grid_column"], "row_span": seat["row_span"], "column_span": seat["column_span"],
                 "row_label": seat["row_label"], "number": seat["seat_number"], "group": seat["group_label"],
                 "group_size": seat["group_size"], "seat_index": seat["seat_index"],
                 "broken": bool(seat["broken"]), "left_handed": bool(seat["left_handed"]), "blocked": bool(seat["blocked"]),
                 "notes": seat["notes"], "updated_at": seat["updated_at"]}
                for seat in db.execute("SELECT * FROM room_seats WHERE room_id = ? ORDER BY grid_row, grid_column, seat_index, label", (room_id,))
            ]
            result["imports"] = [{**dict(item), "warnings": json.loads(item["warnings"] or "[]"),
                                  "issue_source": json.loads(item["issue_source"] or "{}")} for item in db.execute(
                "SELECT * FROM room_imports WHERE room_id = ? ORDER BY imported_at DESC, id LIMIT 20", (room_id,))]
        return result

    def _check_version(self, db, room_id, version):
        row = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if row is None:
            raise NotFound("This room no longer exists.")
        if type(version) is not int or version != row["version"]:
            raise Conflict(STALE)
        return row

    def _apply_chart(self, db, room_id, chart, filename, content, when):
        layout = validate_layout(chart.get("layout"), chart["seats"])
        existing = {seat["label"].casefold(): dict(seat) for seat in
                    db.execute("SELECT * FROM room_seats WHERE room_id = ?", (room_id,))}
        kept, chart_broken = 0, 0
        for seat in chart["seats"]:
            previous = existing.pop(seat["label"].casefold(), None)
            chart_broken += bool(seat["broken"])
            fields = (seat["label"], seat["display"], seat["row"], seat["column"], seat["row_span"], seat["column_span"],
                      seat["row_label"], seat["number"], seat["group"], seat["group_size"], seat["seat_index"])
            blocked = int(bool(seat.get("blocked")))
            if previous:
                kept += 1
                # A chart's own marks (broken, left-handed, accessible seats kept free) add conditions; they never clear yours.
                db.execute("""UPDATE room_seats SET label=?, display=?, grid_row=?, grid_column=?, row_span=?, column_span=?,
                              row_label=?, seat_number=?, group_label=?, group_size=?, seat_index=?,
                              broken=MAX(broken, ?), left_handed=MAX(left_handed, ?), blocked=MAX(blocked, ?), updated_at=? WHERE id=?""",
                           (*fields, int(seat["broken"]), int(seat["left_handed"]), blocked, when, previous["id"]))
            else:
                db.execute("""INSERT INTO room_seats (id, room_id, label, display, grid_row, grid_column, row_span, column_span,
                              row_label, seat_number, group_label, group_size, seat_index, broken, left_handed, blocked, created_at, updated_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (str(uuid.uuid4()), room_id, *fields, int(seat["broken"]), int(seat["left_handed"]), blocked, when, when))
            if seat.get("notes"):
                db.execute("UPDATE room_seats SET notes=? WHERE room_id=? AND label=? COLLATE NOCASE",
                           (seat["notes"], room_id, seat["label"]))
        for previous in existing.values():
            db.execute("DELETE FROM room_seats WHERE id = ?", (previous["id"],))
        digest = hashlib.sha256(content).hexdigest()
        old_review = json.loads(db.execute("SELECT review_required FROM rooms WHERE id=?", (room_id,)).fetchone()[0])
        review = list(dict.fromkeys([*old_review, *chart.get("review_required", [])]))
        db.execute("""UPDATE rooms SET sheet=?, grid_rows=?, grid_columns=?, landmarks=?, source_filename=?,
                      source_sha256=?, imported_at=?, updated_at=?, layout_json=?, review_required=?, version=version+1 WHERE id=?""",
                   (chart["sheet"], chart["rows"], chart["columns"], json.dumps(chart["landmarks"]),
                    filename, digest, when, when, json.dumps(layout), json.dumps(review), room_id))
        db.execute("""INSERT INTO room_imports (id, room_id, filename, sha256, sheet, seat_count, added, removed, kept, imported_at, chart_broken, warnings, issue_source)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (str(uuid.uuid4()), room_id, filename, digest, chart["sheet"], len(chart["seats"]),
                    len(chart["seats"]) - kept, len(existing), kept, when, chart_broken, json.dumps(chart["warnings"]), json.dumps(chart.get("issue_source", {}))))

    @staticmethod
    def parse_chart(content, filename, room_name, *, issues_content=None, issues_filename=None, allow_ambiguous=False):
        if filename.lower().endswith(".pdf"):
            from room_pdf_import import parse_pdf_chart
            chart = parse_pdf_chart(content, filename)
        else:
            chart = parse_seating_chart(content, filename, allow_ambiguous=allow_ambiguous)
        if issues_content is not None:
            source = issue_document(issues_content, safe_filename(issues_filename))
            apply_issues(chart, source, room_name)
        validate_layout(chart.get("layout"), chart["seats"])
        return chart

    def create(self, name, notes, content, filename, *, issues_content=None, issues_filename=None, allow_ambiguous=False):
        name, notes = clean(name, "Room name", 80, True), clean(notes, "Notes", 280)
        filename = safe_filename(filename)
        chart = self.parse_chart(content, filename, name, issues_content=issues_content, issues_filename=issues_filename,
                                 allow_ambiguous=allow_ambiguous)
        room_id, when = str(uuid.uuid4()), timestamp()
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM rooms WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
                raise Conflict("A room with that name already exists. Open it to replace its seating chart.")
            db.execute("INSERT INTO rooms (id, name, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                       (room_id, name, notes, when, when))
            self._apply_chart(db, room_id, chart, filename, content, when)
            return {**self.get(room_id, db), "warnings": chart["warnings"]}

    def import_many(self, items):
        """Administrative room intake: prevalidate everything, then add atomically.

        Existing identical sources are left entirely untouched. A changed source
        for an existing room needs the normal version-checked replacement flow.
        """
        prepared = []
        names = set()
        for item in items:
            name = clean(item["name"], "Room name", 80, True)
            if name.casefold() in names:
                raise ValueError(f"The intake contains more than one chart for {name}.")
            names.add(name.casefold())
            filename = safe_filename(item["filename"])
            chart = self.parse_chart(item["content"], filename, name, issues_content=item.get("issues_content"),
                                     issues_filename=item.get("issues_filename"), allow_ambiguous=True)
            prepared.append((name, filename, item["content"], chart))
        result = {"added": [], "unchanged": []}
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for name, filename, content, chart in prepared:
                existing = db.execute("SELECT * FROM rooms WHERE name=? COLLATE NOCASE", (name,)).fetchone()
                if existing:
                    digest = hashlib.sha256(content).hexdigest()
                    if existing["source_sha256"] != digest:
                        raise Conflict(f"{name} already exists with a different source. Use Replace seating chart to review that change.")
                    issue_source = chart.get("issue_source")
                    if issue_source:
                        sources = [json.loads(row[0]) for row in db.execute("SELECT issue_source FROM room_imports WHERE room_id=?", (existing["id"],))]
                        if not any(source.get("sha256") == issue_source["sha256"] for source in sources):
                            raise Conflict(f"{name} has new seat-issue notes. Use Replace seating chart to apply them with a version check.")
                    result["unchanged"].append({"id": existing["id"], "name": name})
                    continue
                identifier, when = str(uuid.uuid4()), timestamp()
                db.execute("INSERT INTO rooms (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (identifier, name, when, when))
                self._apply_chart(db, identifier, chart, filename, content, when)
                result["added"].append({"id": identifier, "name": name, "seat_count": len(chart["seats"]),
                                        "review_required": chart.get("review_required", []), "warnings": chart["warnings"]})
        return result

    def replace_chart(self, room_id, version, content, filename, *, issues_content=None, issues_filename=None, allow_ambiguous=False):
        filename = safe_filename(filename)
        room = self.get(room_id, seats=False)
        chart = self.parse_chart(content, filename, room["name"], issues_content=issues_content, issues_filename=issues_filename,
                                 allow_ambiguous=allow_ambiguous)
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_version(db, room_id, version)
            self._apply_chart(db, room_id, chart, filename, content, timestamp())
            return {**self.get(room_id, db), "warnings": chart["warnings"]}

    def update(self, room_id, version, data):
        if "resolve_review" in data and type(data["resolve_review"]) is not bool:
            raise ValueError("Room review confirmation must be true or false.")
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_version(db, room_id, version)
            name = clean(data.get("name", row["name"]), "Room name", 80, True)
            notes = clean(data.get("notes", row["notes"]), "Notes", 280)
            other = db.execute("SELECT 1 FROM rooms WHERE name = ? COLLATE NOCASE AND id != ?", (name, room_id)).fetchone()
            if other:
                raise Conflict("Another room already uses that name.")
            db.execute("UPDATE rooms SET name=?, notes=?, updated_at=?, version=version+1 WHERE id=?",
                       (name, notes, timestamp(), room_id))
            if data.get("resolve_review") and json.loads(row["review_required"]):
                # Retain the exact acknowledged issues in the existing import log.
                seats = db.execute("SELECT COUNT(*) FROM room_seats WHERE room_id=?", (room_id,)).fetchone()[0]
                db.execute("""INSERT INTO room_imports (id, room_id, filename, sha256, sheet, seat_count, added, removed, kept,
                              imported_at, warnings) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                           (str(uuid.uuid4()), room_id, "Instructor room review", row["source_sha256"], row["sheet"],
                            seats, seats, timestamp(),
                            json.dumps(["Instructor confirmed seat labels and chair locations: " + text for text in json.loads(row["review_required"])])))
                db.execute("UPDATE rooms SET review_required='[]' WHERE id=?", (room_id,))
            return self.get(room_id, db)

    def apply_corrections(self, apply=False):
        """The reviewed corrections in room_corrections.py, shown (the default) or saved in one transaction.

        A corrected seat keeps its `room_seats.id`: a relabel names the same physical seat. Each room is corrected once,
        with a `room_imports` receipt, so a seat the instructor later unblocks stays unblocked. A room that a seating
        chart uses, or that is not as the department's file imports, is left alone and its reason returned.
        """
        result = {"applied": bool(apply), "corrected": [], "already": [], "skipped": [], "missing": []}
        with connect(self.path) as db:
            if apply:
                db.execute("BEGIN IMMEDIATE")
            charts = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seating_charts'").fetchone())
            for name, correct in CORRECTIONS.items():
                row = db.execute("SELECT * FROM rooms WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
                if row is None:
                    result["missing"].append(name)
                    continue
                if db.execute("SELECT 1 FROM room_imports WHERE room_id = ? AND filename = ?", (row["id"], CORRECTIONS_LOG)).fetchone():
                    result["already"].append(name)
                    continue
                if charts and db.execute("SELECT 1 FROM seating_charts WHERE room_id = ?", (row["id"],)).fetchone():
                    result["skipped"].append({"name": name, "reason": "A seating chart uses this room."})
                    continue
                before = {seat["id"]: dict(seat) for seat in db.execute("SELECT * FROM room_seats WHERE room_id = ?", (row["id"],))}
                room = {"name": name, "notes": row["notes"], "grid_columns": row["grid_columns"], "landmarks": json.loads(row["landmarks"]),
                        "layout": json.loads(row["layout_json"] or "{}"), "review_required": json.loads(row["review_required"] or "[]"),
                        "seats": [{"id": seat["id"], "label": seat["label"], "display": seat["display"], "row": seat["grid_row"],
                                   "column": seat["grid_column"], "row_label": seat["row_label"], "number": seat["seat_number"],
                                   "broken": bool(seat["broken"]), "blocked": bool(seat["blocked"]), "notes": seat["notes"]}
                                  for seat in sorted(before.values(), key=lambda seat: (seat["grid_row"], seat["grid_column"], seat["seat_index"] or 0, seat["label"]))]}
                try:
                    correct(room)
                    layout = validate_layout(room["layout"], room["seats"])
                except (KeyError, ValueError) as error:
                    result["skipped"].append({"name": name, "reason": str(error.args[0])})
                    continue
                changed = [seat for seat in room["seats"] if corrected_seat(seat) != stored_seat(before[seat["id"]])]
                relabelled = [(before[seat["id"]]["label"], seat["label"]) for seat in changed if seat["label"] != before[seat["id"]]["label"]]
                kept_free = sorted(seat["label"] for seat in changed if seat["blocked"] and not before[seat["id"]]["blocked"])
                unusable = sorted(seat["label"] for seat in changed if seat["broken"] and not before[seat["id"]]["broken"])
                settled = [item for item in json.loads(row["review_required"] or "[]") if item not in room["review_required"]]
                log = [f"Kept free (blocked, with a note on the seat): {', '.join(kept_free)}."] if kept_free else []
                if len(relabelled) > 20:
                    log.append(f"All {len(relabelled)} seats relabelled, for example {relabelled[0][0]} → {relabelled[0][1]}; same physical seats and IDs.")
                elif relabelled:
                    log.append(f"Relabelled {', '.join(f'{old} → {new}' for old, new in relabelled)}; same physical seats and IDs.")
                if unusable:
                    log.append(f"Marked unusable (broken): {', '.join(unusable)}.")
                if room["notes"] != row["notes"]:
                    log.append(f"Room note: {room['notes']}")
                log += [f"Review settled by the reviewed corrections of {CORRECTIONS_REVIEWED}: {item}" for item in settled]
                result["corrected"].append({"name": name, "kept_free": kept_free, "relabelled": len(relabelled), "unusable": unusable,
                                            "notes": room["notes"] if room["notes"] != row["notes"] else "", "review_settled": len(settled),
                                            "review_left": room["review_required"]})
                if not apply:
                    continue
                when = timestamp()
                # Labels are unique within a room, and ILCB 111's row A moves 404A to 405A while 405A moves on: park them first.
                for seat in changed:
                    if seat["label"] != before[seat["id"]]["label"]:
                        db.execute("UPDATE room_seats SET label = ? WHERE id = ?", (f"correcting {seat['id']}", seat["id"]))
                for seat in changed:
                    db.execute("""UPDATE room_seats SET label=?, display=?, grid_row=?, grid_column=?, row_label=?, seat_number=?,
                                  broken=?, blocked=?, notes=?, updated_at=? WHERE id=?""",
                               (seat["label"], seat["display"], seat["row"], seat["column"], seat["row_label"], seat["number"],
                                int(seat["broken"]), int(seat["blocked"]), seat["notes"], when, seat["id"]))
                db.execute("""UPDATE rooms SET notes=?, grid_columns=?, landmarks=?, layout_json=?, review_required=?, updated_at=?,
                              version=version+1 WHERE id=?""",
                           (room["notes"], room["grid_columns"], json.dumps(room["landmarks"]), json.dumps(layout),
                            json.dumps(room["review_required"]), when, row["id"]))
                db.execute("""INSERT INTO room_imports (id, room_id, filename, sha256, sheet, seat_count, added, removed, kept,
                              imported_at, warnings) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                           (str(uuid.uuid4()), row["id"], CORRECTIONS_LOG, row["source_sha256"], row["sheet"], len(before), len(before),
                            when, json.dumps(log)))
        return result

    def delete(self, room_id, version):
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_version(db, room_id, version)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_sittings'").fetchone() and db.execute(
                    "SELECT 1 FROM exam_sittings WHERE room_id=?", (room_id,)).fetchone():
                raise Conflict("This room is referenced by an exam sitting. Change the sitting's room before deleting it.")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seating_charts'").fetchone():
                used = db.execute("SELECT COUNT(*) FROM seating_charts WHERE room_id = ?", (room_id,)).fetchone()[0]
                if used:
                    raise Conflict(f"This room is used by {used} seating chart{'s' if used != 1 else ''}. Delete those charts first.")
            db.execute("DELETE FROM room_imports WHERE room_id = ?", (room_id,))
            db.execute("DELETE FROM room_seats WHERE room_id = ?", (room_id,))
            db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))

    def set_conditions(self, room_id, version, seat_ids, conditions):
        if not isinstance(seat_ids, list) or not seat_ids or len(seat_ids) > 2000:
            raise ValueError("Select at least one seat.")
        if any(not isinstance(seat_id, str) for seat_id in seat_ids):
            raise ValueError("Seat identifiers must be text.")
        if len(set(seat_ids)) != len(seat_ids):
            raise ValueError("Select at least one seat.")
        if not isinstance(conditions, dict) or not conditions or set(conditions) - set(CONDITIONS):
            raise ValueError("Choose the broken, left-handed, or blocked condition to change.")
        if any(not isinstance(value, bool) for value in conditions.values()):
            raise ValueError("Seat conditions must be true or false.")
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_version(db, room_id, version)
            placeholders = ",".join("?" * len(seat_ids))
            found = {row["id"] for row in db.execute(
                f"SELECT id FROM room_seats WHERE room_id = ? AND id IN ({placeholders})", (room_id, *seat_ids))}
            if found != set(seat_ids):
                raise Conflict("Some selected seats are no longer in this room. Reload the seating chart.")
            when = timestamp()
            assignments = ", ".join(f"{name}=?" for name in conditions)
            db.execute(f"UPDATE room_seats SET {assignments}, updated_at=? WHERE room_id = ? AND id IN ({placeholders})",
                       (*(int(value) for value in conditions.values()), when, room_id, *seat_ids))
            db.execute("UPDATE rooms SET updated_at=?, version=version+1 WHERE id=?", (when, room_id))
            return self.get(room_id, db)

    def export(self):
        with connect(self.path) as db:
            return [self.get(room["id"], db) for room in self.list(db)]


def register(app, service):
    # Single-instructor workspace: like courses and rosters, these routes rely on
    # the app-wide same-origin write checks rather than a browser session cookie.
    api = Blueprint("rooms", __name__, url_prefix="/api/rooms")

    body = partial(json_object, "The request must be a JSON object.")

    def upload():
        files = request.files.getlist("file")
        issues = request.files.getlist("issues_file")
        if len(files) != 1 or set(request.files) - {"file", "issues_file"} or len(issues) > 1 or not files[0].filename:
            raise ValueError("Choose one Excel (.xlsx) or ILCB PDF seating chart, with an optional Word seat-issue document.")
        options = {}
        if issues and issues[0].filename:
            options = {"issues_content": issues[0].read(MAX_ISSUES + 1), "issues_filename": issues[0].filename}
        return files[0].read(MAX_UPLOAD + 1), files[0].filename, options

    @api.get("")
    def index():
        return jsonify(service.list())

    @api.post("")
    def create():
        content, filename, options = upload()
        room = service.create(request.form.get("name"), request.form.get("notes"), content, filename, **options)
        return jsonify(room), 201

    @api.get("/<room_id>")
    def detail(room_id):
        return jsonify(service.get(room_id))

    @api.patch("/<room_id>")
    def update(room_id):
        data = body()
        return jsonify(service.update(room_id, data.get("version"), data))

    @api.delete("/<room_id>")
    def delete(room_id):
        service.delete(room_id, body().get("version"))
        return "", 204

    @api.post("/<room_id>/chart")
    def replace_chart(room_id):
        content, filename, options = upload()
        version = request.form.get("version", "")
        return jsonify(service.replace_chart(room_id, int(version) if version.isdigit() else None, content, filename, **options))

    @api.post("/<room_id>/seats")
    def seats(room_id):
        data = body()
        conditions = {name: data[name] for name in CONDITIONS if name in data}
        return jsonify(service.set_conditions(room_id, data.get("version"), data.get("seat_ids"), conditions))

    app.register_blueprint(api)
