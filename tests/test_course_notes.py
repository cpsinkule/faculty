"""Course notes (AGENTS.md "Course notes"): lecture notes uploaded on the course page are split into their sections at once, from the
PDF's bookmarks and the headings that open its pages, both kept and compared; each part is its own retained PDF in the course
definition's storage, served inline under its section's name. Editions are append-only: the same file is one edition, a newer
upload is shown first, Remove hides an edition until Restore, and nothing is deleted."""
import io
import os
import sqlite3
import tempfile
import unittest

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from browser_course_pages import seed_course
from test_storage import HEADERS

from app import create_app
from assistant import policy
from course_notes.sections import LISTED, RULE, heading, plan
from rosters import connect

RUNNING = "© Test Notes for Mathematics for Business"


def notes_pdf(pages, outline, title="Test Lecture Notes"):
    """A notes PDF: `pages` are lists of text lines drawn top down (a running header and page number above each from page 3),
    `outline` is [(level, title, page)] in order."""
    target = io.BytesIO()
    page = canvas.Canvas(target, pagesize=(612, 792), invariant=1)  # the same pages make the same bytes
    page.setTitle(title)
    marks = {}
    for index, (level, text, number) in enumerate(outline):
        marks.setdefault(number, []).append((f"b{index}", level, text))
    for number, lines in enumerate(pages, 1):
        y = 740
        if number >= 3:
            page.drawString(72, y, f"{number - 2} {RUNNING}" if number % 2 else f"{RUNNING} {number - 2}")
            y -= 24
        for line in lines:
            page.drawString(72, y, line)
            y -= 18
        for key, level, text in marks.get(number, []):
            page.bookmarkPage(key)
            page.addOutlineEntry(text, key, level=level)
        page.showPage()
    page.save()
    return target.getvalue()


# Like the instructor's notes: front matter, a chapter title above each chapter's first section, 2.1 without a bookmark (its topic
# nested under 1.2 in the outline, as the real notes nest 5.1's under 4.4) and one section title wrapped onto a second line.
PAGES = [
    ["Lecture Notes for Mathematics"],
    ["How to Use these Lecture Notes", "Each section begins with its objectives."],
    ["Matrices", "1.1 Basic Matrix Operations", "Objectives: In this section, you will learn basic matrix algebra."],
    ["Adding and Subtracting Matrices", "Example 3 Add the matrices."],
    ["1.2 Matrix Multiplication and Its Applications to Real", "Scenarios", "Objectives: In this section, you will multiply."],
    ["Functions", "2.1 Relations and Functions", "Objectives: In this section, you will learn about relations."],
    ["Using Function Notation", "Example 2 Evaluate f(3)."],
    ["2.2 Polynomial Functions", "Objectives: In this section, you will learn about polynomials."],
]
OUTLINE = [(0, "How to Use these Lecture Notes", 2), (0, "1.1 Basic Matrix Operations", 3), (1, "Defining a Matrix", 3),
           (1, "Adding and Subtracting Matrices", 4), (0, "1.2 Matrix Multiplication and Its Applications to Real Scenarios", 5),
           (1, "Writing Interval Notation", 6), (1, "Using Function Notation", 7), (0, "2.2 Polynomial Functions", 8)]


def evidence(pages, toc=(), running=True):
    """What the sandbox reads, built by hand: each page's first lines (after a running header) and its heading-like lines."""
    text = []
    for number, lines in enumerate(pages, 1):
        shown = ([f"{RUNNING} {number}"] if running else []) + list(lines)
        text.append({"page": number, "chars": sum(map(len, shown)), "lines": shown[:12], "headings": [line for line in shown if LISTED.match(line)]})
    return {"pages": len(pages), "title": "Notes", "toc": [{"level": level, "title": title, "page": page} for level, title, page in toc], "text": text}


class RuleTests(unittest.TestCase):
    def test_bookmarks_and_page_headings_are_both_kept_and_a_missing_bookmark_is_found_from_its_heading(self):
        result = plan(evidence([["Title page"], *PAGES[2:]], [(t[0], t[1], t[2] - 1) for t in OUTLINE[1:]]))
        parts = [(p["kind"], p["number"], p["title"], p["first_page"], p["last_page"], p["found_by"]) for p in result["parts"]]
        self.assertEqual(parts, [("front", "", "Front matter", 1, 1, "front"),
                                 ("section", "1.1", "Basic Matrix Operations", 2, 3, "both"),
                                 ("section", "1.2", "Matrix Multiplication and Its Applications to Real Scenarios", 4, 4, "both"),
                                 ("section", "2.1", "Relations and Functions", 5, 6, "heading"),
                                 ("section", "2.2", "Polynomial Functions", 7, 7, "both")])
        self.assertEqual([p["chapter_title"] for p in result["parts"][1:]], ["Matrices", "Matrices", "Functions", "Functions"])
        # A topic belongs to the part holding its page, whatever the outline nested it under.
        self.assertEqual([t["title"] for t in result["parts"][3]["topics"]], ["Writing Interval Notation", "Using Function Notation"])
        self.assertEqual(result["notes"], ["2.1 was found from its heading on page 5; the PDF has no bookmark for it."])
        self.assertEqual(result["rule"], RULE)

    def test_a_wrapped_heading_takes_its_second_line_when_no_bookmark_names_it(self):
        result = plan(evidence([["Front"], ["1.1 Setting Up and Solving Systems of Linear Equations", "Using Matrices", "Objectives: Upon completion"],
                                ["1.2 Graphs", "Objectives: Upon completion you will be able to:"]]))
        self.assertEqual([p["title"] for p in result["parts"][1:]], ["Setting Up and Solving Systems of Linear Equations Using Matrices", "Graphs"])

    def test_a_contents_page_and_lines_below_the_top_open_no_section(self):
        pages = [["Contents", "1.1 Limits ........ 3", "1.2 Continuity 5", "2.1 Derivatives 7"],
                 ["Preface"], ["1.1 Limits", "Objectives: limits"], ["Example 1", "Some text", "More", "Yet more", "Still more", "1.2 Continuity"],
                 ["1.2 Continuity", "Objectives: continuity"]]
        result = plan(evidence(pages, running=False))
        self.assertEqual([(p["number"], p["first_page"], p["last_page"]) for p in result["parts"]], [("", 1, 2), ("1.1", 3, 4), ("1.2", 5, 5)])

    def test_no_sections_keeps_the_notes_whole(self):
        result = plan(evidence([["Chapter one"], ["Some text"], ["More text"]], running=False))
        self.assertEqual([(p["kind"], p["title"], p["first_page"], p["last_page"]) for p in result["parts"]], [("front", "Whole notes", 1, 3)])
        self.assertIn("kept whole", result["notes"][0])

    def test_a_section_beginning_below_the_top_of_a_page_shares_that_page(self):
        pages = [["1.1 Sets", "Objectives: sets"], ["Example", "text", "text", "text", "text", "text", "1.2 Venn Diagrams", "Objectives"], ["More Venn"]]
        result = plan(evidence(pages, [(0, "1.1 Sets", 1), (0, "1.2 Venn Diagrams", 2)], running=False))
        self.assertEqual([(p["number"], p["first_page"], p["last_page"], p["found_by"]) for p in result["parts"]], [("1.1", 1, 2, "both"), ("1.2", 2, 3, "bookmark")])

    def test_a_bookmark_and_heading_that_disagree_are_named_and_order_decides(self):
        pages = [["1.1 Sets", "Objectives"], ["text"], ["1.2 Venn Diagrams", "Objectives"], ["text"], ["1.3 Counting", "Objectives"]]
        result = plan(evidence(pages, [(0, "1.1 Sets", 1), (0, "1.2 Venn Diagrams", 4), (0, "1.3 Counting", 5)], running=False))
        self.assertEqual([(p["number"], p["first_page"]) for p in result["parts"]], [("1.1", 1), ("1.2", 3), ("1.3", 5)])
        self.assertIn("1.2: its heading is on page 3, its bookmark points to page 4; the heading's page is used.", result["notes"])
        # A section whose only page breaks the order is left out and named, never moved by guessing.
        backwards = plan(evidence([["1.1 Sets", "x"], ["1.3 Counting", "x"], ["1.2 Venn Diagrams", "x"]], running=False))
        kept = [p["number"] for p in backwards["parts"]]
        self.assertEqual(len(kept), 2)
        self.assertEqual([p["first_page"] for p in backwards["parts"]], sorted(p["first_page"] for p in backwards["parts"]))
        left_out = ({"1.1", "1.2", "1.3"} - set(kept)).pop()
        self.assertTrue(any(note.startswith(f"{left_out} was left out") for note in backwards["notes"]), backwards["notes"])

    def test_headings_are_read_narrowly(self):
        self.assertEqual(heading("Section 3.2: Graphing Inequalities"), (3, 2, "Graphing Inequalities"))
        for line in ("1.5 liters of water", "3.14", "2.3 Systems ..... 12", "Example 2.3 Solve", "12.5% of the class"):
            self.assertIsNone(heading(line), line)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.definition = seed_course(self.client)["definition_id"]
        self.route = f"/api/course-notes/definitions/{self.definition}"

    def upload(self, content, name="notes.pdf", request_id="u1"):
        return self.client.post(self.route, data={"file": (io.BytesIO(content), name), "request_id": request_id}, headers=HEADERS)

    def test_an_upload_is_split_into_its_sections_and_each_part_opens_as_its_own_pdf(self):
        answer = self.upload(notes_pdf(PAGES, OUTLINE))
        self.assertEqual(answer.status_code, 200, answer.json)
        current = answer.json["current"]
        self.assertEqual((current["title"], current["pages"], current["sections"]), ("Test Lecture Notes", 8, 4))
        self.assertEqual([(p["number"], p["first_page"], p["last_page"], p["found_by"]) for p in current["parts"]],
                         [("", 1, 2, "front"), ("1.1", 3, 4, "both"), ("1.2", 5, 5, "both"), ("2.1", 6, 7, "heading"), ("2.2", 8, 8, "both")])
        self.assertEqual([p["chapter_title"] for p in current["parts"]], ["", "Matrices", "Matrices", "Functions", "Functions"])
        self.assertEqual(current["notes"], ["2.1 was found from its heading on page 6; the PDF has no bookmark for it."])
        part = current["parts"][3]
        self.assertTrue(part["url"].endswith("/parts/4/2-1-relations-and-functions.pdf"), part["url"])
        opened = self.client.get(part["url"])
        self.assertEqual((opened.status_code, opened.mimetype, opened.headers["Cache-Control"]), (200, "application/pdf", "no-store"))
        self.assertEqual(opened.headers["Content-Disposition"], 'inline; filename="2.1 Relations and Functions.pdf"')
        reader = PdfReader(io.BytesIO(opened.data))
        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(reader.metadata.title, "2.1 Relations and Functions")
        self.assertEqual([(item.title, reader.get_destination_page_number(item)) for item in reader.outline],
                         [("Writing Interval Notation", 0), ("Using Function Notation", 1)])
        self.assertIn("Relations and Functions", reader.pages[0].extract_text())
        download = self.client.get(part["url"] + "?download=1")
        self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))
        whole = self.client.get(current["original_url"])
        self.assertEqual((whole.status_code, len(PdfReader(io.BytesIO(whole.data)).pages)), (200, 8))
        # The course Home's line reads only the counts.
        summary = self.client.get(self.route + "?summary=1").json
        self.assertEqual(summary["current"], {key: current[key] for key in ("id", "title", "source_name", "pages", "sections", "uploaded_at")})

    def test_the_assistants_upload_remove_and_restore_are_stored_as_the_assistants(self):
        """requirement, 2026-11-06 (assistant.md: records that name an actor say the assistant made them, request_actor.py)."""
        assistant = {"headers": HEADERS, "environ_overrides": {"faculty.actor": "assistant"}}
        mine = self.upload(notes_pdf(PAGES, OUTLINE)).json["current"]
        theirs = self.client.post(self.route, data={"file": (io.BytesIO(notes_pdf(PAGES[:5], OUTLINE[:5], title="Shorter Notes")), "notes.pdf"), "request_id": "a1"}, **assistant)
        self.assertEqual(theirs.status_code, 200, theirs.json)
        edition = theirs.json["current"]["id"]
        removed = self.client.post(f"/api/course-notes/{edition}/remove", json={"request_id": "a2", "confirmation": "remove"}, **assistant)
        self.assertEqual(removed.status_code, 200, removed.json)
        restored = self.client.post(f"/api/course-notes/{edition}/restore", json={"request_id": "a3"}, **assistant)
        self.assertEqual(restored.status_code, 200, restored.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT id, actor FROM course_notes ORDER BY rowid")], [(mine["id"], "workspace"), (edition, "assistant")])
            self.assertEqual([tuple(r) for r in db.execute("SELECT request_id, action, actor FROM course_notes_events ORDER BY rowid")],
                             [("u1", "uploaded", "workspace"), ("a1", "uploaded", "assistant"), ("a2", "removed", "assistant"), ("a3", "restored", "assistant")])

    def test_the_same_file_is_one_edition_and_a_request_id_answers_for_one_file(self):
        first = self.upload(notes_pdf(PAGES, OUTLINE))
        repeated = self.upload(notes_pdf(PAGES, OUTLINE))
        self.assertEqual((repeated.status_code, repeated.json["uploaded"]["repeated"]), (200, True))
        other = self.upload(notes_pdf(PAGES[:5], OUTLINE[:5]))
        self.assertEqual(other.status_code, 409)
        again = self.upload(notes_pdf(PAGES, OUTLINE), request_id="u2")
        self.assertEqual((again.json["uploaded"]["again"], again.json["uploaded"]["id"]), (True, first.json["current"]["id"]))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM course_notes").fetchone()[0], 1)
            self.assertEqual([r[0] for r in db.execute("SELECT action FROM course_notes_events ORDER BY rowid")], ["uploaded", "uploaded"])

    def test_upload_request_id_cannot_be_reused_for_another_course_definition(self):
        # COURSE-NOTES: an edition belongs to exactly one course definition, even with identical PDF bytes.
        content = notes_pdf(PAGES, OUTLINE)
        original = self.upload(content)
        other = seed_course(self.client, code="MATH 141", crn="14101")["definition_id"]
        route = f"/api/course-notes/definitions/{other}"
        refused = self.client.post(route, data={"file": (io.BytesIO(content), "notes.pdf"), "request_id": "u1"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 409)
        self.assertIsNone(self.client.get(route).json["current"])
        accepted = self.client.post(route, data={"file": (io.BytesIO(content), "notes.pdf"), "request_id": "other-course"}, headers=HEADERS)
        self.assertEqual(accepted.status_code, 200, accepted.json)
        self.assertNotEqual(accepted.json["current"]["id"], original.json["current"]["id"])

    def test_a_newer_upload_is_shown_first_and_remove_and_restore_only_hide(self):
        first = self.upload(notes_pdf(PAGES, OUTLINE)).json["current"]
        second = self.upload(notes_pdf(PAGES[:5], OUTLINE[:5], title="Shorter Notes"), request_id="u2").json
        self.assertEqual((second["current"]["title"], [e["id"] for e in second["earlier"]]), ("Shorter Notes", [first["id"]]))

        def outline():
            """The sections a reader outside the module resolves a notes-section number against (`current_sections`)."""
            from course_notes.service import current_sections
            from rosters import connect
            with connect(self.app.config["DATABASE"]) as db:
                return current_sections(db, self.definition)

        def shown(view):
            return {p["number"]: {"title": p["title"], "chapter": p["chapter"], "chapter_title": p["chapter_title"], "url": p["url"]}
                    for p in view["current"]["parts"] if p["kind"] == "section"}

        self.assertEqual(outline(), shown(second))  # by number, from the edition the course page shows
        self.assertEqual(sorted(outline()), ["1.1", "1.2"])
        refused = self.client.post(f"/api/course-notes/{second['current']['id']}/remove", json={"request_id": "r1"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400)  # the confirmation is checked on the server
        removed = self.client.post(f"/api/course-notes/{second['current']['id']}/remove", json={"request_id": "r1", "confirmation": "remove"}, headers=HEADERS).json
        self.assertEqual((removed["current"]["id"], [e["title"] for e in removed["removed"]]), (first["id"], ["Shorter Notes"]))
        self.assertEqual((outline(), sorted(outline())), (shown(removed), ["1.1", "1.2", "2.1", "2.2"]))
        twice = self.client.post(f"/api/course-notes/{second['current']['id']}/remove", json={"request_id": "r2", "confirmation": "remove"}, headers=HEADERS)
        self.assertEqual(twice.status_code, 409)
        # A removed edition's files still open; restoring puts it back where its upload stands.
        self.assertEqual(self.client.get(removed["removed"][0]["parts"][1]["url"]).status_code, 200)
        restored = self.client.post(f"/api/course-notes/{second['current']['id']}/restore", json={"request_id": "r3"}, headers=HEADERS).json
        self.assertEqual((restored["current"]["title"], restored["removed"]), ("Shorter Notes", []))
        self.assertEqual(outline(), shown(restored))
        # Uploading the first file again makes it the one shown.
        back = self.upload(notes_pdf(PAGES, OUTLINE), request_id="u3").json
        self.assertEqual(back["current"]["id"], first["id"])
        self.assertEqual(outline(), shown(back))

    def test_uploading_a_removed_edition_again_shows_it_again(self):
        first = self.upload(notes_pdf(PAGES, OUTLINE)).json["current"]
        removed = self.client.post(f"/api/course-notes/{first['id']}/remove", json={"request_id": "r1", "confirmation": "remove"}, headers=HEADERS).json
        self.assertEqual((removed["current"], [e["id"] for e in removed["removed"]]), (None, [first["id"]]))
        back = self.upload(notes_pdf(PAGES, OUTLINE), request_id="u2").json
        self.assertEqual((back["uploaded"]["again"], back["current"]["id"], back["removed"]), (True, first["id"], []))
        from course_notes.service import current_sections
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(sorted(current_sections(db, self.definition)), ["1.1", "1.2", "2.1", "2.2"])
        replay = self.upload(notes_pdf(PAGES, OUTLINE), request_id="u2").json  # the same request is not a second restoration
        self.assertEqual((replay["uploaded"]["repeated"], replay["current"]["id"]), (True, first["id"]))
        # A later Remove still hides it, and the event record is still append-only.
        again = self.client.post(f"/api/course-notes/{first['id']}/remove", json={"request_id": "r2", "confirmation": "remove"}, headers=HEADERS).json
        self.assertEqual(again["current"], None)

    def test_only_a_pdf_is_taken_and_word_files_are_told_how_to_save_one(self):
        word = self.upload(b"PK\x03\x04 word", name="Notes.docx")
        self.assertEqual(word.status_code, 400)
        self.assertIn("Save As", word.json["error"])
        self.assertEqual(self.upload(b"hello", name="notes.txt").json["error"], "Upload the notes as a PDF.")
        self.assertEqual(self.client.post(self.route, json={"request_id": "x"}, headers=HEADERS).status_code, 415)
        self.assertEqual(self.client.get("/api/course-notes/definitions/nope").status_code, 404)

    def test_records_are_append_only_and_retained_files_are_checked(self):
        current = self.upload(notes_pdf(PAGES, OUTLINE)).json["current"]
        with connect(self.app.config["DATABASE"]) as db:
            for statement in ("UPDATE course_notes SET title='x'", "DELETE FROM course_notes_parts", "DELETE FROM course_notes_events"):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(statement)
            path = db.execute("SELECT path FROM course_notes_parts WHERE notes_id=? AND position=2", (current["id"],)).fetchone()[0]
        stored = os.path.join(self.temp.name, "data", "definitions", self.definition, "data", path)
        with open(stored, "ab") as handle:
            handle.write(b"tampered")
        service = self.app.extensions["course_notes"]
        with self.assertRaisesRegex(Exception, "checksum"):
            service.read(current["id"], 2)

    def test_the_assistant_never_uploads_and_asks_before_removing(self):
        self.assertEqual(policy.base_rule("course_notes.upload").tier, "never")
        remove = policy.base_rule("course_notes.remove")
        self.assertEqual((remove.tier, remove.confirmation), ("approve", "remove"))
        self.assertEqual(policy.base_rule("course_notes.restore").tier, "approve")


if __name__ == "__main__":
    unittest.main()
