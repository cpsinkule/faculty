"""A page whose codes did not read, known by its place between its own copy's labels (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS").

Requirement, 2026-11-14, of a late take-home (copy 60: page 1, blank back, page 2 with both corner codes covered by the student's
writing, an empty back showing the front through, page 3, another such back) whose Papers stage asked them to "Group the pages into
papers": asked whether Faculty should decide by itself that such a side is its copy's page, and set aside the empty backs inside that
copy's paper with a note naming it, they answered Yes to both.
"""
import io
import json
import unittest

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from document_identity import media
from rosters import connect
import test_sequence as sequence_fixture
from test_sequence import BLANK
from test_storage import HEADERS

BACK, EXTRA = "back", "extra"


def back_page():
    """An empty back whose front shows through: a few faint strokes, more than a blank side holds and far less than a printed page."""
    mark = io.BytesIO()
    canvas = Canvas(mark, pagesize=(612, 792))
    canvas.setStrokeGray(.55)
    canvas.setLineWidth(.8)
    canvas.line(120, 520, 300, 520)
    canvas.line(140, 300, 260, 310)
    canvas.save()
    return PdfReader(mark).pages[0]


def extra_sheet():
    """A sheet of the student's own work slipped into the paper: as much ink as a printed page."""
    mark = io.BytesIO()
    canvas = Canvas(mark, pagesize=(612, 792))
    canvas.setFont("Helvetica", 28)
    for row in range(12):
        canvas.drawString(72, 700 - row * 50, "x = 3, so f(x) = 9 + 2x")
    canvas.save()
    return PdfReader(mark).pages[0]


class PagesByPlaceTests(unittest.TestCase):
    setUp = sequence_fixture.SequenceTests.setUp
    submit = sequence_fixture.SequenceTests.submit

    def scan(self, *exams):
        """Each exam is (copy, sides); a side is a labeled page ("L1"), its page with no codes ("p2"), BACK, EXTRA or BLANK."""
        writer = PdfWriter()
        for copy, sides in exams:
            for side in sides:
                if side == BLANK:
                    writer.add_blank_page(612, 792)
                elif side == BACK:
                    writer.add_page(back_page())
                elif side == EXTRA:
                    writer.add_page(extra_sheet())
                elif side.startswith("L"):
                    writer.add_page(self.printed.pages[(copy - 1) * 3 + int(side[1]) - 1])
                else:
                    writer.add_page(self.plain.pages[int(side[1]) - 1])
        message = self.submit(writer)
        while self.scans.process_one():
            pass
        return message

    def pages(self, message):
        with connect(self.path) as db:
            return {row["source_page_index"]: dict(row) for row in db.execute(
                """SELECT sp.id,sp.source_page_index,sp.status,sp.review_state,sp.raster_artifact_id,p.page_number,c.copy_number FROM scan_pages sp
                   LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id LEFT JOIN printed_copies c ON c.id=p.printed_copy_id WHERE sp.message_id=?""", (message,))}

    def steps(self, message):
        return [(step["kind"], step["tool"], step["action"], step["text"]) for step in self.scans.review_item(message)["overview"]["steps"]]

    def events(self, message, kind):
        with connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id WHERE j.message_id=? AND e.kind=? ORDER BY e.rowid", (message, kind))]

    def ink(self, message, side):
        return media.ink_share(Image.open(io.BytesIO(self.inbox.read_artifact(self.pages(message)[side]["raster_artifact_id"])[1])))

    def test_the_fixture_backs_are_neither_blank_nor_a_printed_page(self):
        message = self.scan((1, ["L1", BACK, "L2", BLANK]))
        self.assertTrue(media.BLANK_SHARE <= self.ink(message, 2) < media.PRINTED_SHARE, self.ink(message, 2))
        self.assertEqual(self.pages(message)[2]["status"], "unreadable")
        self.assertGreater(self.ink(message, 3), 2 * media.PRINTED_SHARE)
        self.assertEqual(self.pages(message)[4]["status"], "blank")

    def test_copy_60s_page_2_takes_its_page_by_its_place_and_its_empty_backs_are_set_aside(self):
        # One-sided paper scanned two-sided, as the take-home: copy 2 whole, then copy 1 with page 2's codes covered, last in the file.
        message = self.scan((2, ["L1", BLANK, "L2", BACK, "L3", BLANK]), (1, ["L1", BLANK, "p2", BACK, "L3", BACK]))
        pages = self.pages(message)
        self.assertEqual((pages[9]["status"], pages[9]["copy_number"], pages[9]["page_number"], pages[9]["review_state"]), ("manual", 1, 2, "candidate"))
        # The backs inside copy 2's paper and copy 1's, each set aside with a reason naming its paper; the blank sides stay blank.
        self.assertEqual({side: pages[side]["review_state"] for side in (4, 10, 12)}, {4: "ignored", 10: "ignored", 12: "ignored"})
        self.assertEqual([pages[side]["status"] for side in (2, 6, 8)], ["blank", "blank", "blank"])
        aside = {event["page"]: event for event in self.events(message, "page_set_aside")}
        self.assertEqual(sorted(aside), [4, 10, 12])
        self.assertEqual({event["actor"] for event in aside.values()}, {"automatic"})
        self.assertEqual(aside[10]["reason"], "A back of copy 1's paper (between its pages 1 and 3), with no printed page on it: set aside by Faculty "
                                              "(DOCUMENTS \"A PAGE BETWEEN ITS OWN LABELS\").")
        self.assertIn("copy 1's paper (after its page 3)", aside[12]["reason"])
        self.assertIn("copy 2's paper (between its pages 2 and 3)", aside[4]["reason"])
        record, = self.events(message, "pages_by_place")
        self.assertEqual([(item["page"], item["copy_number"], item["page_number"]) for item in record["identified"]], [(9, 1, 2)])
        self.assertEqual((record["actor"], record["rule"], sorted(item["page"] for item in record["set_aside"])), ("automatic", "pages-by-place-v1", [4, 10, 12]))
        # Nothing asks for a grouping: the Papers stage says what Faculty did, and Papers plans none.
        steps = self.steps(message)
        self.assertEqual([step for step in steps if step[0] in ("todo", "check") and step[1] in ("exams", "pages")], [])
        self.assertIn(("done", "exams", None, "By its place, page 9 is copy 1's page 2, between its pages 1 and 3 (its codes did not read). "
                                              "3 empty backs inside papers were set aside."), steps)
        papers = next(stage for stage in self.scans.review_item(message)["overview"]["stages"] if stage["key"] == "papers")
        self.assertEqual((papers["state"], papers["status"]), ("done", "Done"))
        self.assertEqual(self.scans.papers_to_check(message)["sides"], None)
        self.assertEqual([(entry["page"], entry["actor"]) for entry in self.scans.review_item(message)["set_aside"]],
                         [(4, "automatic"), (10, "automatic"), (12, "automatic")])
        # Copy 1 misses no page now, so its scan's review makes it a whole paper.
        with connect(self.path) as db:
            self.assertEqual(self.scans.assembly(self.copy_id(db, 1), db)["missing"], [])
        # Nothing more on another pass.
        self.assertEqual(self.scans.places.apply(message), {"identified": 0, "set_aside": 0, "held": 0})

    def copy_id(self, db, number):
        return db.execute("SELECT id FROM printed_copies WHERE print_batch_id=? AND copy_number=?", (self.batch["id"], number)).fetchone()[0]

    def test_a_back_the_instructor_takes_back_is_never_set_aside_again(self):
        message = self.scan((1, ["L1", BACK, "L2", BACK, "L3", BLANK]))
        back = self.pages(message)[2]["id"]
        undone = self.client.post(f"/api/documents/scans/{back}/set-aside", json={"undo": True}, headers=HEADERS)
        self.assertEqual((undone.status_code, undone.json["set_aside"]), (200, False), undone.json)
        self.assertEqual(self.scans.places.apply(message), {"identified": 0, "set_aside": 0, "held": 0})
        self.assertEqual(self.pages(message)[2]["review_state"], "candidate")
        # Then it is a written page in no paper, as any other, and the instructor sets it aside or recovers its label.
        texts = [text for kind, tool, _, text in self.steps(message) if kind == "todo" and tool == "pages"]
        self.assertEqual(texts, ["1 written page carries no label and belongs to no paper (page 2). If one is a student's page, "
                                 "recover its label from its printed alias; otherwise set it aside."])

    def test_two_sides_that_look_printed_for_one_missing_page_are_named_and_never_offered_as_a_grouping(self):
        # Two-sided printing (no backs to leave out): page 2 has no codes and the student slipped in a sheet of work.
        message = self.scan((1, ["L1", "p2", EXTRA, "L3"]), (2, ["L1", "L2", "L3"]))
        pages = self.pages(message)
        self.assertEqual((pages[2]["status"], pages[3]["status"]), ("unreadable", "unreadable"))
        self.assertEqual(self.events(message, "pages_by_place"), [])
        steps = self.steps(message)
        self.assertIn(("todo", "exams", "set_aside",
                       "Copy 1's page 2 did not read (its codes may be covered or smudged). Pages 2 and 3 lie between its pages 1 and 3, and 2 of "
                       "them look like a printed page, so Faculty could not tell which is its page 2. Set aside the side that is not, and Faculty "
                       "gives the rest that page by their place."), steps)
        self.assertFalse(any("sides per paper" in step[3] for step in steps), steps)
        self.assertEqual(self.scans.papers_to_check(message)["sides"], None)
        papers = next(stage for stage in self.scans.review_item(message)["overview"]["stages"] if stage["key"] == "papers")
        self.assertEqual((papers["status"], papers["progress"]), ("Needs you", None))  # no count of papers grouped: none is to be
        # The instructor sets the extra sheet aside; the scan changed, so the worker looks again and gives page 2 its place.
        done = self.client.post(f"/api/documents/scans/{pages[3]['id']}/set-aside", json={"reason": "The student's own sheet"}, headers=HEADERS)
        self.assertEqual(done.status_code, 200, done.json)
        self.assertEqual(self.scans.places.apply(message), {"identified": 1, "set_aside": 0, "held": 0})
        self.assertEqual((self.pages(message)[2]["copy_number"], self.pages(message)[2]["page_number"]), (1, 2))
        self.assertEqual([step for step in self.steps(message) if step[0] in ("todo", "check") and step[1] in ("exams", "pages")], [])

    def test_a_page_that_did_not_arrive_is_said_so(self):
        # Page 2's sheet is not in the stack: between pages 1 and 3 only an empty back. (A paper alone in its scan, as the late
        # take-home: with evenly spaced first pages the scan would be grouped by its labels, and that grouping's rules apply.)
        message = self.scan((1, ["L1", BLANK, BACK, BLANK, "L3", BLANK]))
        self.assertEqual(self.pages(message)[3]["review_state"], "candidate")
        texts = [text for kind, tool, _, text in self.steps(message) if kind == "todo" and tool == "exams"]
        self.assertEqual(texts, ["Copy 1's page 2 did not read or did not arrive: nothing between its pages 1 and 3 (page 3) looks like a printed "
                                 "page. Look at those sides; if the page is missing from the stack, the paper waits for it on Review."])

    def test_the_last_page_of_a_file_takes_its_place_after_its_copys_last_label(self):
        message = self.scan((1, ["L1", "L2", "L3"]), (2, ["L1", "L2", "p3"]))
        pages = self.pages(message)
        self.assertEqual((pages[6]["status"], pages[6]["copy_number"], pages[6]["page_number"]), ("manual", 2, 3))
        record, = self.events(message, "pages_by_place")
        self.assertEqual(record["identified"][0]["between"], "after its page 2")

    def test_a_side_between_two_papers_or_a_paper_already_reviewed_is_left_as_it_was(self):
        # Between copy 4's page 2 and copy 5's page 1 it may be either paper's: no place, so nothing changes there.
        across = self.scan((4, ["L1", "L2", "p3"]), (5, ["L1", "L2", "L3"]))
        self.assertEqual(self.events(across, "pages_by_place"), [])
        self.assertEqual(self.pages(across)[3]["status"], "unreadable")
        # A copy with a reviewed paper is changed on Review: a later scan of its missing page is never given a place.
        pages = self.pages(across)
        with connect(self.path) as db:
            copy = self.copy_id(db, 4)
        self.scans.select_assembly(copy, [pages[1]["id"], pages[2]["id"]], True, "Accepted without page 3.", "workspace")
        again = self.scan((4, ["L2", "p3"]))
        self.assertEqual(self.events(again, "pages_by_place"), [])
        self.assertEqual(self.pages(again)[2]["status"], "unreadable")

    def test_paper_whose_later_pages_carry_no_label_is_left_to_its_first_page_labels(self):
        # Only page 1 labels on any scan of the job: its other pages were printed bare, so this is a grouping's to place, not a code
        # that did not read (DOCUMENTS "ONE LABEL PER PAPER"): the scan groups itself by its first-page label, as before.
        message = self.scan((1, ["L1", "p2", "p3"]))
        self.assertEqual(self.events(message, "pages_by_place"), [])
        self.assertIn(("done", "exams", None, "1 paper grouped automatically, 3 sides each."), self.steps(message))
        # Once a later page's code of that job has read on a scan, its paper shows labels throughout: a later scan of a paper whose
        # later codes did not read takes them by their place, before any grouping.
        self.scan((2, ["L1", "L2", "L3"]))
        later = self.scan((3, ["L1", "p2", "p3"]))
        self.assertEqual([(self.pages(later)[side]["copy_number"], self.pages(later)[side]["page_number"]) for side in (2, 3)], [(3, 2), (3, 3)])
        self.assertEqual([item["between"] for item in self.events(later, "pages_by_place")[0]["identified"]], ["after its page 1"] * 2)

    def test_a_shuffled_stack_is_never_placed_and_says_which_page_did_not_read(self):
        # Copy 2's pages come 1, 3, 2: the file is not in paper order, so the side between copy 1's pages 1 and 3
        # may be anyone's and is not given copy 1's page 2; Papers says which page did not read and never offers a grouping.
        message = self.scan((1, ["L1", "p2", "L3"]), (2, ["L1", "L3", "L2"]))
        self.assertEqual(self.events(message, "pages_by_place"), [])
        self.assertEqual(self.pages(message)[2]["status"], "unreadable")
        steps = self.steps(message)
        self.assertIn(("todo", "pages", "set_aside", "1 page carries no code that could be read (page 2). This stack is not in paper order, so "
                                                     "Faculty does not guess whose it is; still missing a page: copy 1 (page 2). If one is a student's "
                                                     "page, recover its label from its printed alias; otherwise set it aside."), steps)
        self.assertFalse(any("sides per paper" in step[3] for step in steps), steps)
        self.assertEqual(self.scans.papers_to_check(message)["sides"], None)
        # In paper order the same pages are placed.
        ordered = self.scan((3, ["L1", "p2", "L3"]), (4, ["L1", "L2", "L3"]))
        self.assertEqual((self.pages(ordered)[2]["copy_number"], self.pages(ordered)[2]["page_number"]), (3, 2))

    def test_the_back_of_a_papers_last_sheet_before_the_next_paper_is_set_aside_too(self):
        # A PAPER'S LAST BACK (requirement, 2026-11-14): side 6 is the back of copy 2's page 3, before copy 1's page 1;
        # side 12, after copy 1's page 3 at the end of the scan, is that paper's place.
        message = self.scan((2, ["L1", BLANK, "L2", BLANK, "L3", BACK]), (1, ["L1", BLANK, "L2", BLANK, "L3", BACK]))
        pages = self.pages(message)
        self.assertEqual((pages[6]["review_state"], pages[12]["review_state"]), ("ignored", "ignored"))
        aside = {event["page"]: event for event in self.events(message, "page_set_aside")}
        self.assertEqual((aside[6]["actor"], aside[6]["reason"]), ("automatic", "A back of copy 2's paper (the back of its page 3), with no printed "
                                                                               "page on it: set aside by Faculty (DOCUMENTS \"A PAGE BETWEEN ITS OWN LABELS\")."))
        record, = self.events(message, "pages_by_place")
        self.assertEqual(sorted(item["page"] for item in record["set_aside"]), [6, 12])
        self.assertEqual([step for step in self.steps(message) if step[0] in ("todo", "check") and step[1] in ("exams", "pages")], [])
        self.assertIn(("done", "exams", None, "2 empty backs inside papers were set aside."), self.steps(message))
        # Taken back on Papers, it is never set aside again.
        undone = self.client.post(f"/api/documents/scans/{pages[6]['id']}/set-aside", json={"undo": True}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(self.scans.places.apply(message), {"identified": 0, "set_aside": 0, "held": 0})
        self.assertEqual(self.pages(message)[6]["review_state"], "candidate")

    def test_a_side_between_two_papers_printed_two_sided_is_left_as_it_was(self):
        # Labels on both sides of the sheets: no side is known to be a back, so the side between copy 2 and copy 1 stays for Papers.
        message = self.scan((2, ["L1", "L2", "L3", BACK]), (1, ["L1", "L2", "L3", BLANK]))
        self.assertEqual(self.pages(message)[4]["review_state"], "candidate")
        self.assertEqual(self.events(message, "pages_by_place"), [])

    def test_ink_share_is_what_the_blank_test_compares(self):
        blank = Image.new("L", (850, 1100), 255)
        self.assertEqual((media.ink_share(blank), media.blank_side(blank)), (0.0, True))
        grey = Image.new("L", (850, 1100), 170)  # coloured paper on the Epson
        self.assertTrue(media.blank_side(grey))
        for x in range(200, 500):
            grey.putpixel((x, 500), 60)  # a short stroke: neither blank nor a printed page
        self.assertTrue(media.BLANK_SHARE <= media.ink_share(grey) < media.PRINTED_SHARE)
        self.assertFalse(media.blank_side(grey))


if __name__ == "__main__":
    unittest.main()
