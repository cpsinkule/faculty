"""COORDINATOR (docs/decisions/coordinator.md): whether a course is coordinated in a term, and who its coordinator is.

Owner: the course TERM (`course_terms.id`). `course_term_coordinators` holds immutable revisions of the term's whole answer
(append-only; the highest revision counts, earlier ones are retained). The course card writes it with the offering it saves, in
that save's own transaction; a saved offering entry (`courses`) owns nothing here, so every offering of a term shows the same
coordinator.

THE OWNER KEY. A coordinator's email comes to Follow-up as a student's does. Where Follow-up's records name whose conversation
it is by a student's enrollment ID, a coordinator's conversation names `coordinator:<term id>` instead (`owner_key`): the term
owns it. An enrollment ID is ten digits, so the two never collide, and anything that looks a student up by that key finds
nobody, which is the safe answer for a record that is no student's.

Faculty sends nothing here and reads no outside service. Catalog data: in the nightly catalog backup, not in the generic export.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect

OWNER = "coordinator:"
LIMITS = {"name": 120, "email": 254}
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# The form a TA's address takes (TA-CONTACTS): no quoting, no percent signs, no list separators.
EMAIL = re.compile(r"[A-Za-z0-9._+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")
NONE = {"coordinated": False, "name": "", "email": ""}


def owner_key(term_id):
    """What a coordinator's follow-up records carry where a student's carry the enrollment ID."""
    return OWNER + str(term_id)


def owner_term(owner):
    """The term ID of a coordinator's owner key, or None for anything else (a student's enrollment ID)."""
    return owner[len(OWNER):] if isinstance(owner, str) and owner.startswith(OWNER) and len(owner) > len(OWNER) else None


def clean(data):
    """The card's answer as it is stored: {coordinated, name, email}. A course that is not coordinated keeps no name or address;
    one that is needs both, the address in a plain name@domain form."""
    if not isinstance(data, dict) or set(data) - {"coordinated", "name", "email"}:
        raise ValueError("Send the coordinator as coordinated, name and email.")
    if not isinstance(data.get("coordinated"), bool):
        raise ValueError("Coordinated course must be true or false.")
    if not data["coordinated"]:
        return dict(NONE)
    values = {}
    for field, label in (("name", "name"), ("email", "email")):
        value = data.get(field, "")
        if not isinstance(value, str) or CONTROL.search(value):
            raise ValueError(f"The coordinator's {label} must be plain text.")
        value = value.strip() if field == "email" else " ".join(value.split())
        if len(value) > LIMITS[field]:
            raise ValueError(f"The coordinator's {label} is longer than {LIMITS[field]} characters.")
        values[field] = value
    if not values["name"]:
        raise ValueError("Enter the coordinator's name, or untick Coordinated course.")
    if not EMAIL.fullmatch(values["email"]):
        raise ValueError("Enter the coordinator's email address (name@tamu.edu), or untick Coordinated course.")
    return {"coordinated": True, **values}


def _table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='course_term_coordinators'").fetchone() is not None


def _latest(db, term_id):
    return db.execute("SELECT * FROM course_term_coordinators WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()


def view(db, term_id):
    """What the course card shows for a term: {term_id, coordinated, name, email, revision} (revision 0: never answered)."""
    row = _latest(db, term_id) if term_id and _table(db) else None
    if not row:
        return {"term_id": term_id, **NONE, "revision": 0}
    return {"term_id": term_id, "coordinated": bool(row["coordinated"]), "name": row["name"], "email": row["email"],
            "revision": row["revision"]}


def record(db, term_id, values, actor="workspace"):
    """Records the term's answer as its next revision, in the caller's transaction (under the roster lifecycle lock the course
    save already holds). The same answer as the latest one records nothing; a term never answered and still not coordinated
    records nothing either."""
    current = _latest(db, term_id)
    held = {"coordinated": bool(current["coordinated"]), "name": current["name"], "email": current["email"]} if current else NONE
    if values != held:
        db.execute("INSERT INTO course_term_coordinators VALUES (?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), term_id, (current["revision"] if current else 0) + 1, int(values["coordinated"]),
                    values["name"], values["email"], actor, timestamp()))
    return view(db, term_id)


def current(db, term_ids=None):
    """{term ID: {term_id, name, email, since}} for the current terms that are coordinated now (all of them, or those of
    `term_ids`). `since` is when this address became the term's coordinator: the first of the latest revisions in a row that
    are coordinated with it, however the name was spelled meanwhile. Catalog only."""
    if not _table(db):
        return {}
    found = {}
    rows = db.execute("""SELECT c.term_id,c.revision,c.coordinated,c.name,c.email,c.created_at FROM course_term_coordinators c
        JOIN course_terms t ON t.id=c.term_id WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY c.term_id,c.revision DESC""").fetchall()
    for row in rows:
        if term_ids is not None and row["term_id"] not in term_ids:
            continue
        entry = found.get(row["term_id"])
        if entry is None:
            found[row["term_id"]] = {"term_id": row["term_id"], "name": row["name"], "email": row["email"], "since": row["created_at"],
                                     "_open": bool(row["coordinated"])}
        elif entry["_open"]:
            if row["coordinated"] and row["email"].casefold() == entry["email"].casefold():
                entry["since"] = row["created_at"]
            else:
                entry["_open"] = False
    return {term: {key: value for key, value in entry.items() if key != "_open"}
            for term, entry in found.items() if entry["email"]}


def of_owner(db, owner):
    """The coordinator an owner key names now: {term_id, name, email, since, code, term_label} for a current, coordinated term,
    else None (not a coordinator's key, a term no longer current, or one no longer coordinated)."""
    term_id = owner_term(owner)
    if not term_id:
        return None
    entry = current(db, {term_id}).get(term_id)
    if not entry:
        return None
    from seating import term_label
    term = db.execute("SELECT id,code,term,term_other,year,sections FROM course_terms WHERE id=?", (term_id,)).fetchone()
    return {**entry, "code": term["code"] or "", "term_label": term_label(term)}


class CoordinatorService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "course_coordinators", migration.stem, migration.read_text(encoding="utf-8"))

    def offering(self, course_id):
        """The coordinator of the term a saved offering entry belongs to, for its Course settings card. Catalog only."""
        from course_storage import decode_course
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM courses WHERE id=? AND deleted_at IS NULL", (str(course_id),)).fetchone()
            if not row:
                from werkzeug.exceptions import NotFound
                raise NotFound("This course no longer exists.")
            return view(db, term_of(db, decode_course(row)))


def term_of(db, course):
    """The permanent term owner of an offering entry (`courses` row as `rosters.decode_course` reads it), or None when its code
    or term is not recognized yet."""
    from rosters import term_key
    if not course.get("definition_id") or not course.get("year") or not course.get("term"):
        return None
    row = db.execute("SELECT id FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?", term_key(course)).fetchone()
    return row["id"] if row else None
