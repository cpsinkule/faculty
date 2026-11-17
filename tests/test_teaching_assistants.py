"""TA contacts (AGENTS.md "TA contacts"): a section's list is immutable revisions of the whole list, saved with a base revision and
a request ID; contact details are checked so an address goes into a mailto: link as it is; Copy from offers the term's other listed
sections; nothing propagates, blocks an offering edit or reaches the generic export."""
import os
import sqlite3
import tempfile
import unittest

from browser_course_pages import seed_course
from test_storage import HEADERS

from app import create_app
from rosters import connect

JORDAN = {"name": "Jordan Lee", "email": "jlee@tamu.edu", "phone": "979-555-0100", "note": "Office hours Tue 2–4"}
SAM = {"name": "Sam Park", "email": "spark@tamu.edu", "phone": "", "note": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.course = seed_course(self.client, sections=[507, 508, 509])
        with connect(self.app.config["DATABASE"]) as db:
            self.sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}

    def route(self, number):
        return f"/api/teaching-assistants/sections/{self.sections[number]}"

    def save(self, number, contacts, base=0, request_id="r1"):
        return self.client.post(self.route(number), json={"contacts": contacts, "base_revision": base, "request_id": request_id}, headers=HEADERS)

    def rows(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT section_id,revision,request_id FROM section_ta_contacts ORDER BY rowid")]


class SaveTests(Base):
    def test_a_section_starts_empty_and_saves_revisions_of_its_whole_list(self):
        view = self.client.get(self.route(507)).json
        self.assertEqual((view["section"], view["code"], view["term_label"], view["revision"], view["contacts"], view["others"]),
                         (507, "MATH 140", "Fall 2026", 0, [], []))
        saved = self.save(507, [JORDAN])
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual((saved.json["revision"], saved.json["contacts"]), (1, [JORDAN]))
        second = self.save(507, [JORDAN, SAM], base=1, request_id="r2")
        self.assertEqual((second.json["revision"], [ta["name"] for ta in second.json["contacts"]]), (2, ["Jordan Lee", "Sam Park"]))
        cleared = self.save(507, [], base=2, request_id="r3")
        self.assertEqual((cleared.json["revision"], cleared.json["contacts"]), (3, []))
        self.assertEqual([r["revision"] for r in self.rows()], [1, 2, 3])  # earlier lists are retained

    def test_the_assistants_save_is_stored_as_the_assistants(self):
        """requirement, 2026-11-06 (assistant.md: records that name an actor say the assistant made them, request_actor.py)."""
        mine = self.save(507, [JORDAN], request_id="mine")
        self.assertEqual(mine.status_code, 200, mine.json)
        theirs = self.client.post(self.route(507), json={"contacts": [JORDAN, SAM], "base_revision": 1, "request_id": "theirs"},
                                  headers=HEADERS, environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(theirs.status_code, 200, theirs.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT request_id, actor FROM section_ta_contacts ORDER BY revision")],
                             [("mine", "workspace"), ("theirs", "assistant")])

    def test_a_repeated_request_returns_what_it_did_and_a_stale_page_is_refused(self):
        self.assertEqual(self.save(507, [JORDAN]).status_code, 200)
        again = self.save(507, [JORDAN])  # the same request ID and list, e.g. a lost answer sent again
        self.assertEqual((again.status_code, again.json["revision"]), (200, 1))
        other = self.save(507, [SAM])  # the same request ID with another list
        self.assertEqual(other.status_code, 409)
        stale = self.save(507, [SAM], base=0, request_id="r2")  # the page still showed no list
        self.assertEqual(stale.status_code, 409)
        self.assertIn("changed elsewhere", stale.json["error"])
        self.assertEqual(len(self.rows()), 1)

    def test_an_identical_list_records_nothing_and_blank_rows_are_dropped(self):
        self.assertEqual(self.save(507, [JORDAN, {"name": " ", "email": "", "phone": "", "note": ""}]).json["contacts"], [JORDAN])
        same = self.save(507, [{**JORDAN, "name": "  Jordan   Lee "}, {}], base=1, request_id="r2")
        self.assertEqual((same.status_code, same.json["revision"]), (200, 1))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.save(508, [], request_id="r3").json["revision"], 0)  # an empty list where there was none
        self.assertEqual(len(self.rows()), 1)

    def test_contact_details_are_checked_before_anything_is_written(self):
        cases = {
            "Give TA 1 a name.": [{"email": "x@tamu.edu"}],
            "does not look like one": [{"name": "A", "email": "a@tamu"}],
            "have the same email address": [JORDAN, {**SAM, "email": "JLee@TAMU.edu"}],
            "phone number can hold": [{"name": "A", "phone": "call me"}],
            "cannot be kept": [{"name": "A\u0007"}],
            "longer than 300": [{"name": "A", "note": "x" * 301}],
            "at most 20 TAs": [{"name": f"TA {n}"} for n in range(21)],
            "must be text": [{"name": 5}],
        }
        for message, contacts in cases.items():
            with self.subTest(message=message):
                answer = self.save(507, contacts)
                self.assertEqual(answer.status_code, 400, answer.json)
                self.assertIn(message, answer.json["error"])
        # Anything that could end a mailto: address list or start its query is refused, not escaped into another address.
        for address in ("a,b@tamu.edu", "a@tamu.edu?cc=x@y.z", "a b@tamu.edu", "a%40b@tamu.edu", "<a@tamu.edu>", "a;b@tamu.edu"):
            with self.subTest(address=address):
                self.assertEqual(self.save(507, [{"name": "A", "email": address}]).status_code, 400)
        for request in ({"contacts": "Jordan", "base_revision": 0, "request_id": "r"}, {"contacts": [], "base_revision": None, "request_id": "r"},
                        {"contacts": [], "base_revision": 0}, {"contacts": [], "base_revision": True, "request_id": "r"}):
            with self.subTest(request=request):
                self.assertEqual(self.client.post(self.route(507), json=request, headers=HEADERS).status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_an_unknown_section_is_a_404_and_writes_need_the_workspace_headers(self):
        self.assertEqual(self.client.get("/api/teaching-assistants/sections/nope").status_code, 404)
        self.assertEqual(self.client.post("/api/teaching-assistants/sections/nope", json={"contacts": [], "base_revision": 0, "request_id": "r"}, headers=HEADERS).status_code, 404)
        refused = self.client.post(self.route(507), json={"contacts": [JORDAN], "base_revision": 0, "request_id": "r"})
        self.assertNotEqual(refused.status_code, 200)
        self.assertEqual(self.rows(), [])

    def test_revisions_are_append_only_and_in_order(self):
        self.save(507, [JORDAN])
        with sqlite3.connect(self.app.config["DATABASE"]) as db:
            for statement in ("UPDATE section_ta_contacts SET contacts_json='[]'", "DELETE FROM section_ta_contacts"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            with self.assertRaises(sqlite3.IntegrityError):  # a revision must follow the latest one
                db.execute("INSERT INTO section_ta_contacts VALUES('x',?,3,'[]','r9','workspace','now')", (self.sections[507],))


class SectionTests(Base):
    def test_copy_from_offers_the_term_s_other_listed_sections_and_nothing_propagates(self):
        self.save(508, [SAM])
        view = self.client.get(self.route(507)).json
        self.assertEqual([(o["section"], [ta["name"] for ta in o["contacts"]]) for o in view["others"]], [(508, ["Sam Park"])])
        self.assertEqual(view["contacts"], [])  # offered to copy, never copied
        self.save(507, [JORDAN, SAM], request_id="r2")
        self.save(508, [{**SAM, "phone": "979-555-0111"}], base=1, request_id="r3")
        self.assertEqual(self.client.get(self.route(507)).json["contacts"][1]["phone"], "")  # 508's edit stays in 508

    def test_an_offering_edit_keeps_the_list_with_the_permanent_section_and_is_never_blocked(self):
        self.save(509, [JORDAN])
        changed = self.client.patch(f"/api/courses/{self.course['id']}", json={"version": self.course["version"], "sections": [507, 508]}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.client.get(self.route(507)).json["others"], [])  # 509 is no longer offered to copy from
        self.assertEqual(self.client.get(self.route(509)).json["contacts"], [JORDAN])  # its list is kept with its permanent ID

    def test_the_generic_workspace_export_leaves_the_list_out(self):
        self.save(507, [JORDAN])
        exported = self.client.get("/api/export")
        self.assertEqual(exported.status_code, 200)
        self.assertNotIn("jlee@tamu.edu", exported.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
