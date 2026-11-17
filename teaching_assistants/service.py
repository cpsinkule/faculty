"""TA contacts (requirement, 2026-09-28; AGENTS.md "TA contacts"): each section's teaching assistants, kept on the section's course
page so the instructor can email them from there.

Owner: the SECTION (`course_sections.id`). A TA list is one immutable revision of the section's whole list
(`section_ta_contacts`, append-only, the highest revision counts, earlier ones retained). A TA who helps several sections is listed
on each: Copy from another section only fills the form, and the instructor's Save records this section's own revision. Nothing
propagates between sections or terms, and a section's permanent ID keeps its list through offering edits (like its Canvas link).

Faculty sends nothing: the page's links open the instructor's own mail program (`mailto:`). The list is catalog data, so the
nightly catalog backup keeps it; the generic workspace export leaves it out.

Lock order: reads are catalog-only and take no lock; a save takes the roster lifecycle lock, then ONE `BEGIN IMMEDIATE` checking
`base_revision` (409 when the list changed since the page read it) and the request ID (the same ID and list returns what it did;
another list is a 409).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect
from seating import term_label

MAX_CONTACTS = 20
LIMITS = {"name": 120, "email": 254, "phone": 40, "note": 300}
FIELDS = tuple(LIMITS)
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Addresses the page can put in a mailto: link as they are: no quoting, no percent signs, no list separators.
EMAIL = re.compile(r"[A-Za-z0-9._+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")
PHONE = re.compile(r"[0-9A-Za-z+().,/#:\- ]+")


def clean(contacts):
    """The list as it is stored: each TA's name, email, phone and note, whitespace collapsed; rows left wholly blank are dropped.
    Anything else that cannot be kept as typed is a ValueError naming the row."""
    if not isinstance(contacts, list):
        raise ValueError("Send the TAs as a list.")
    kept = []
    for number, contact in enumerate(contacts, 1):
        if not isinstance(contact, dict):
            raise ValueError(f"TA {number} is not a set of contact details.")
        values = {}
        for field in FIELDS:
            value = contact.get(field, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError(f"TA {number}'s {field} must be text.")
            value = value.strip() if field == "email" else " ".join(value.split())
            if CONTROL.search(value):
                raise ValueError(f"TA {number}'s {field} holds a character that cannot be kept.")
            if len(value) > LIMITS[field]:
                raise ValueError(f"TA {number}'s {field} is longer than {LIMITS[field]} characters.")
            values[field] = value
        if not any(values.values()):
            continue
        if not values["name"]:
            raise ValueError(f"Give TA {number} a name.")
        who = values["name"]
        if values["email"] and not EMAIL.fullmatch(values["email"]):
            raise ValueError(f"{who}'s email address does not look like one (name@tamu.edu).")
        if values["phone"] and (not PHONE.fullmatch(values["phone"]) or not re.search(r"\d", values["phone"])):
            raise ValueError(f"{who}'s phone number can hold digits, spaces and + ( ) - . / # , : only.")
        kept.append(values)
    if len(kept) > MAX_CONTACTS:
        raise ValueError(f"A section can list at most {MAX_CONTACTS} TAs.")
    seen = {}
    for values in kept:
        email = values["email"].casefold()
        if email and email in seen:
            raise ValueError(f"{seen[email]} and {values['name']} have the same email address.")
        seen[email] = values["name"]
    return kept


def _section(db, section_id):
    row = db.execute("""SELECT s.id,s.number,s.term_id,t.term,t.term_other,t.year,t.code,t.sections FROM course_sections s
        JOIN course_terms t ON t.id=s.term_id WHERE s.id=?""", (str(section_id),)).fetchone()
    if not row:
        raise NotFound("That section was not found.")
    return {"section_id": row["id"], "section": row["number"], "term_id": row["term_id"], "term_label": term_label(row),
            "code": row["code"] or "", "_sections": json.loads(row["sections"] or "[]")}


def _latest(db, section_id):
    return db.execute("SELECT * FROM section_ta_contacts WHERE section_id=? ORDER BY revision DESC LIMIT 1", (section_id,)).fetchone()


def contact_lists(db, section_ids):
    """Latest contacts for the requested sections in one read, without building Copy from choices."""
    return {row["section_id"]: json.loads(row["contacts_json"]) for row in db.execute("""
        SELECT c.section_id,c.contacts_json FROM section_ta_contacts c
        WHERE c.section_id IN (SELECT value FROM json_each(?))
        AND c.revision=(SELECT MAX(revision) FROM section_ta_contacts WHERE section_id=c.section_id)
        """, (json.dumps(list(section_ids)),))}


def _view(db, section):
    current = _latest(db, section["section_id"])
    others = []
    sections = db.execute("SELECT id,number FROM course_sections WHERE term_id=? AND id!=? ORDER BY number",
                          (section["term_id"], section["section_id"])).fetchall()
    lists = contact_lists(db, [row["id"] for row in sections])
    for row in sections:
        if row["number"] not in section["_sections"]:
            continue  # a number no offering of the term lists any more keeps its list, but is not offered to copy from
        contacts = lists.get(row["id"], [])
        if contacts:
            others.append({"section_id": row["id"], "section": row["number"], "contacts": contacts})
    return {**{k: v for k, v in section.items() if not k.startswith("_")},
            "revision": current["revision"] if current else 0,
            "contacts": json.loads(current["contacts_json"]) if current else [],
            "saved_at": current["created_at"] if current else None,
            "others": others}


class TeachingAssistantService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "teaching_assistants", migration.stem, migration.read_text(encoding="utf-8"))

    def section(self, section_id):
        """The section's TA list and the other sections of its term that have one (to copy from). Catalog only."""
        with connect(self.path) as db:
            return _view(db, _section(db, section_id))

    def save(self, section_id, data, actor="workspace"):
        """Records the section's TA list as its next revision. `base_revision` names the revision the page showed (0 for none);
        an identical list records nothing."""
        data = data if isinstance(data, dict) else {}
        contacts = clean(data.get("contacts"))
        base = data.get("base_revision")
        if type(base) is not int or base < 0:
            raise ValueError("Name the TA list the page showed as base_revision (0 for none).")
        request_id = valid_request_id(data.get("request_id"))
        encoded = json.dumps(contacts, ensure_ascii=False, separators=(",", ":"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            section = _section(db, section_id)
            done = db.execute("SELECT contacts_json FROM section_ta_contacts WHERE section_id=? AND request_id=?",
                              (section["section_id"], request_id)).fetchone()
            if done:
                if done["contacts_json"] != encoded:
                    raise Conflict("This save was already made with a different TA list. Close this window and open it again.")
                return _view(db, section)
            current = _latest(db, section["section_id"])
            if (current["revision"] if current else 0) != base:
                raise Conflict("This section's TA list was changed elsewhere. Close this window and open it again.")
            if (current["contacts_json"] if current else "[]") != encoded:
                db.execute("INSERT INTO section_ta_contacts VALUES(?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), section["section_id"], base + 1, encoded, request_id, actor, timestamp()))
            return _view(db, section)
