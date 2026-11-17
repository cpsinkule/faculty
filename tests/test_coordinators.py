"""COORDINATOR (docs/decisions/coordinator.md): the course card's Coordinated course answer is owned by the course's TERM as
immutable revisions; a course that is not coordinated keeps no name or address; every offering of a term reads the same answer;
the course records and the state read are unchanged; nothing reaches the generic export."""
import os
import tempfile
import unittest

from browser_course_pages import seed_course
from test_storage import HEADERS

from app import create_app
from course_coordinators import service as coordinators
from rosters import connect

LEE = {"coordinated": True, "name": "Dr. Jordan Lee", "email": "jlee@tamu.edu"}
NOT = {"coordinated": False, "name": "", "email": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()

    def rows(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("""SELECT term_id,revision,coordinated,name,email,actor FROM course_term_coordinators
                                                   ORDER BY rowid""")]

    def term(self, code="MATH 140"):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute("SELECT id FROM course_terms WHERE code=? ORDER BY year LIMIT 1", (code,)).fetchone()["id"]

    def card(self, course):
        return self.client.get(f"/api/courses/{course['id']}/coordinator").json

    def patch(self, course, **data):
        version = self.client.get("/api/state").json["courses"]
        version = next(c["version"] for c in version if c["id"] == course["id"])
        return self.client.patch(f"/api/courses/{course['id']}", json={"version": version, **data}, headers=HEADERS)


class CardTests(Base):
    def test_a_course_made_without_the_box_is_not_coordinated_and_records_nothing(self):
        plain = seed_course(self.client, sections=[507, 508])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.card(plain), {"term_id": self.term(), **NOT, "revision": 0})
        # Unticked on the card: still nothing to record.
        other = seed_course(self.client, code="MATH 151", sections=[1, 2], coordinator=NOT)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.card(other)["coordinated"], False)

    def test_a_coordinated_course_keeps_its_coordinator_for_the_term(self):
        course = seed_course(self.client, sections=[507, 508], coordinator={**LEE, "name": "  Dr.  Jordan   Lee ", "email": " jlee@tamu.edu "})
        self.assertEqual(self.rows(), [{"term_id": self.term(), "revision": 1, "coordinated": 1, "name": "Dr. Jordan Lee",
                                        "email": "jlee@tamu.edu", "actor": "workspace"}])
        self.assertEqual(self.card(course), {"term_id": self.term(), **LEE, "revision": 1})
        # The course record and the state read say nothing new (PERFORMANCE: default responses unchanged).
        self.assertNotIn("coordinator", course)
        self.assertNotIn("coordinator", self.client.get("/api/state").json["courses"][0])

    def test_every_offering_of_a_term_reads_the_same_coordinator_and_another_term_its_own(self):
        first = seed_course(self.client, sections=[507, 508], coordinator=LEE)
        second = seed_course(self.client, sections=[514, 515])
        spring = seed_course(self.client, term="Spring", year=2027, sections=[501, 502])
        self.assertEqual(self.card(second), self.card(first))
        self.assertEqual(self.card(spring)["coordinated"], False)
        self.assertEqual(len(self.rows()), 1)

    def test_saving_the_card_again_records_only_a_change(self):
        course = seed_course(self.client, sections=[507, 508], coordinator=LEE)
        self.assertEqual(self.patch(course, coordinator=LEE).status_code, 200)
        self.assertEqual(self.patch(course, name="Calculus One").status_code, 200)   # no coordinator sent: left as it is
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.patch(course, coordinator={**LEE, "email": "jordan.lee@tamu.edu"}).status_code, 200)
        self.assertEqual(self.patch(course, coordinator=NOT).status_code, 200)
        self.assertEqual([(r["revision"], r["coordinated"], r["email"]) for r in self.rows()],
                         [(1, 1, "jlee@tamu.edu"), (2, 1, "jordan.lee@tamu.edu"), (3, 0, "")])
        self.assertEqual(self.card(course), {"term_id": self.term(), **NOT, "revision": 3})

    def test_a_ticked_box_needs_a_name_and_an_address_and_a_refused_card_saves_nothing(self):
        for bad, said in (({**LEE, "name": " "}, "name"), ({**LEE, "email": "jlee"}, "email"), ({**LEE, "email": "a@b.edu, c@d.edu"}, "email"),
                          ({"coordinated": "yes"}, "true or false"), ({**LEE, "phone": "1"}, "coordinated, name and email"),
                          ("Dr. Lee", "coordinated, name and email")):
            with self.subTest(bad=bad):
                response = self.client.post("/api/courses", json={"name": "Calculus I", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                                  "sections": [507], "coordinator": bad}, headers=HEADERS)
                self.assertEqual(response.status_code, 400)
                self.assertIn(said, response.json["error"])
        self.assertEqual(self.client.get("/api/state").json["courses"], [])
        self.assertEqual(self.rows(), [])

    def test_revisions_are_kept_as_recorded_and_stay_out_of_the_export(self):
        seed_course(self.client, sections=[507, 508], coordinator=LEE)
        with connect(self.app.config["DATABASE"]) as db:
            for statement in ("UPDATE course_term_coordinators SET name='Other'", "DELETE FROM course_term_coordinators",
                              "INSERT INTO course_term_coordinators VALUES('x','%s',5,1,'A','a@tamu.edu','workspace','2026-11-14')" % self.term(),
                              "INSERT INTO course_term_coordinators VALUES('y','%s',2,1,'','','workspace','2026-11-14')" % self.term()):
                with self.subTest(statement=statement[:40]), self.assertRaises(Exception):
                    db.execute(statement)
        self.assertNotIn("jlee@tamu.edu", self.client.get("/api/export").get_data(as_text=True))


class CurrentTests(Base):
    def test_the_current_coordinators_and_when_each_address_began(self):
        course = seed_course(self.client, sections=[507, 508], coordinator=LEE)
        seed_course(self.client, code="MATH 151", sections=[1, 2])
        term = self.term()
        with connect(self.app.config["DATABASE"]) as db:
            first = coordinators.current(db)
            self.assertEqual(list(first), [term])
            self.assertEqual((first[term]["name"], first[term]["email"]), ("Dr. Jordan Lee", "jlee@tamu.edu"))
            self.assertEqual(coordinators.of_owner(db, coordinators.owner_key(term))["code"], "MATH 140")
            self.assertIsNone(coordinators.of_owner(db, "9000000306"))
        # A corrected name keeps when the address began; a new address begins again; unticking ends it.
        self.patch(course, coordinator={**LEE, "name": "Jordan Lee"})
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(coordinators.current(db)[term], {**first[term], "name": "Jordan Lee"})
        self.patch(course, coordinator={**LEE, "email": "JLEE@tamu.edu"})
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(coordinators.current(db)[term]["since"], first[term]["since"])
        self.patch(course, coordinator=NOT)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(coordinators.current(db), {})
            self.assertIsNone(coordinators.of_owner(db, coordinators.owner_key(term)))

    def test_an_owner_key_is_never_an_enrollment_id(self):
        self.assertEqual(coordinators.owner_term(coordinators.owner_key("t-1")), "t-1")
        for other in ("9000000306", "", None, "coordinator:", 5):
            self.assertIsNone(coordinators.owner_term(other))


if __name__ == "__main__":
    unittest.main()
