"""A page in no paper set aside, and a held paper grouped by hand (requirement, 2026-10-02).

"Set aside a page in no paper — for a stray unlabeled sheet (a TA's section header, a scratch page). Today it holds the scan
forever and blocks certification. The page stays kept and listed, with your reason; undo is possible." And "Group a held paper by
hand — for a paper whose sides don't add up (a student added a sheet). You tick its sides in order and group it."
"""
import io
import json
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from request_actor import ASSISTANT, ENVIRON_KEY
from rosters import connect
import test_documents as documents_fixture
import test_sequence as sequence_fixture
from test_sequence import BLANK, P2, P3
from test_storage import HEADERS


def written_sheet(text):
    """A written page with no label: a TA's section header sheet, or a student's scratch page."""
    mark = io.BytesIO()
    canvas = Canvas(mark, pagesize=(612, 792))
    canvas.setFont("Helvetica", 40)
    canvas.drawString(100, 500, text)
    canvas.save()
    return PdfReader(mark).pages[0]


class SetAsideTests(unittest.TestCase):
    """Paper labeled on every page, with a written sheet that carries no label on top of the stack."""
    managed = False
    setUp = documents_fixture.DocumentTests.setUp
    add_course = documents_fixture.DocumentTests.add_course
    version_for = documents_fixture.DocumentTests.version_for
    batch = documents_fixture.DocumentTests.batch
    ready = documents_fixture.DocumentTests.ready
    intake = documents_fixture.DocumentTests.intake

    def stray_scan(self, *, tail=()):
        """A section header sheet, then two copies labeled on every page; `tail` adds pages after them."""
        batch = self.ready(quantity=2)
        printed = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        writer = PdfWriter()
        writer.add_page(written_sheet("SECTION 507 - QUIZ"))
        for page in printed.pages:
            writer.add_page(page)
        for page in tail:
            writer.add_page(page) if page is not BLANK else writer.add_blank_page(612, 792)
        content = io.BytesIO()
        writer.write(content)
        job = self.intake(content.getvalue())
        while self.scans.process_one():
            pass
        return job

    def pages(self, message):
        return {page["source_page_index"]: page for page in self.scans.review_item(message)["pages"]}

    def steps(self, message):
        return [(step["kind"], step["tool"], step["text"]) for step in self.scans.review_item(message)["overview"]["steps"]]

    def set_aside(self, scan_page_id, **data):
        return self.client.post(f"/api/documents/scans/{scan_page_id}/set-aside", json=data, headers=HEADERS)

    def events(self, job, kind):
        with connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind=? ORDER BY rowid", (job["id"], kind))]

    def test_a_stray_sheet_says_it_is_in_no_paper_and_setting_it_aside_clears_the_scan(self):
        job = self.stray_scan()
        message = job["message_id"]
        pages = self.pages(message)
        header = pages[1]["id"]
        self.assertEqual((pages[1]["status"], pages[1]["review_state"]), ("unreadable", "candidate"))
        # The step says what the page is and what to do (no grouping can place it: every copy here has every page).
        stray = ("todo", "pages", "1 written page carries no label and belongs to no paper (page 1). If one is a student's page, "
                 "recover its label from its printed alias; otherwise set it aside.")
        self.assertIn(stray, self.steps(message))
        self.assertNotIn("exams", [tool for _, tool, _ in self.steps(message)])
        frq = self.app.extensions["frq_grading"]
        # It never held the review back (one page holds back only itself): the scan passes by itself, and stays filed for the page.
        frq._review_scan(message, None, actor="automatic")
        with frq.rosters.lock(), connect(self.path) as db:
            self.assertEqual(frq.scan_blockers(db, message), [stray[2]])
            row = dict(db.execute("SELECT id,status,action,message_id,0 gone FROM inbox_jobs WHERE message_id=?", (message,)).fetchone())
            self.assertEqual(self.inbox.filed(db, [row])[job["id"]][0], "needs_review")
            plan_id = frq.scan_status(db, message)["plan"]["id"]
        workflow = next(p for p in frq.overview(include_health=False)["assignments"] if p["id"] == plan_id)["workflow"]
        self.assertTrue(workflow["awaiting_review"])
        checks = self.app.extensions["certification"].view(plan_id).get("checks") or []
        self.assertEqual([check["id"] for check in checks if check["id"] == "inbox_pending"], ["inbox_pending"])

        # Refused: no reason, a labeled page, a blank side, a scan page that does not exist.
        self.assertEqual(self.set_aside(header).status_code, 400)
        self.assertEqual(self.set_aside(header, reason=" ").status_code, 400)
        self.assertEqual(self.set_aside(header, reason="x", undo="yes").status_code, 400)
        refused = self.set_aside(pages[2]["id"], reason="Section header sheet")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("has a label or an identity", refused.json["error"])
        self.assertEqual(self.set_aside("missing", reason="x").status_code, 404)
        self.assertEqual(self.set_aside(header, reason="x", undo=True).status_code, 409)  # not set aside yet

        # Set aside, under the lifecycle lock and then the scan's owner lock.
        held_order = []
        owner_lock = self.documents.files.owner_lock

        @contextmanager
        def watched(owner):
            held_order.append((owner, bool(getattr(self.documents.rosters._lock_state, "held", False))))
            with owner_lock(owner):
                yield
        with patch.object(self.documents.files, "owner_lock", side_effect=watched):
            done = self.set_aside(header, reason="Section header sheet", request_id="aside-1")
        self.assertEqual(done.status_code, 200, done.json)
        self.assertEqual(held_order, [(message, True)])
        self.assertEqual(done.json, {"scan_page_id": header, "message_id": message, "page": 1, "set_aside": True, "reason": "Section header sheet"})
        # Kept, listed with its reason, and no longer a page left to place: nothing is open on the scan any more.
        item = self.scans.review_item(message)
        self.assertEqual(self.pages(message)[1]["review_state"], "ignored")
        self.assertEqual([(entry["page"], entry["reason"], entry["actor"]) for entry in item["set_aside"]], [(1, "Section header sheet", "workspace")])
        self.assertEqual(self.scans.review_item(message, heavy=False)["set_aside"], item["set_aside"])
        self.assertEqual(item["overview"]["state"], "done")
        with frq.rosters.lock(), connect(self.path) as db:
            self.assertEqual(frq.scan_blockers(db, message), [])
            self.assertEqual(self.inbox.filed(db, [row])[job["id"]][0], "completed")
        workflow = next(p for p in frq.overview(include_health=False)["assignments"] if p["id"] == plan_id)["workflow"]
        self.assertFalse(workflow["awaiting_review"])
        checks = self.app.extensions["certification"].view(plan_id).get("checks") or []
        self.assertEqual([check for check in checks if check["id"] == "inbox_pending"], [])
        event, = self.events(job, "page_set_aside")
        self.assertEqual((event["actor"], event["scan_page_id"], event["page"], event["reason"], event["previous"], event["request_id"]),
                         ("workspace", header, 1, "Section header sheet", "candidate", "aside-1"))
        # The same request again changes nothing; another one is told the page is set aside already.
        again = self.set_aside(header, reason="Section header sheet", request_id="aside-1")
        self.assertEqual((again.status_code, again.json), (200, done.json))
        self.assertEqual(len(self.events(job, "page_set_aside")), 1)
        self.assertEqual(self.set_aside(header, reason="Scratch paper").status_code, 409)

        # Undo puts back the state it had, and the step with it; the assistant's own request is recorded as the assistant's.
        undone = self.client.post(f"/api/documents/scans/{header}/set-aside", json={"undo": True, "request_id": "aside-2"}, headers=HEADERS,
                                  environ_overrides={ENVIRON_KEY: ASSISTANT})
        self.assertEqual((undone.status_code, undone.json["set_aside"]), (200, False), undone.json)
        self.assertEqual(self.pages(message)[1]["review_state"], "candidate")
        self.assertNotIn("set_aside", self.scans.review_item(message))
        self.assertIn(stray, self.steps(message))
        event, = self.events(job, "page_set_aside_undone")
        self.assertEqual((event["actor"], event["restored"], event["request_id"]), (ASSISTANT, "candidate", "aside-2"))
        self.assertEqual(self.set_aside(header, undo=True, request_id="aside-2").status_code, 200)  # repeated: nothing more
        self.assertEqual(len(self.events(job, "page_set_aside_undone")), 1)

    def test_a_scan_deleted_from_the_inbox_keeps_its_pages_as_they_are(self):
        job = self.stray_scan()
        message = job["message_id"]
        header = self.pages(message)[1]["id"]
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete", "a duplicate upload")
        refused = self.set_aside(header, reason="Section header sheet")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("deleted from the Inbox", refused.json["error"])
        self.inbox.restore(job["id"])
        self.assertEqual(self.set_aside(header, reason="Section header sheet").status_code, 200)

    def test_a_scratch_page_after_complete_papers_and_a_blank_side_beside_it(self):
        job = self.stray_scan(tail=(written_sheet("scratch work"), BLANK))
        message = job["message_id"]
        pages = self.pages(message)
        self.assertEqual((pages[8]["status"], pages[9]["status"]), ("unreadable", "blank"))
        texts = [text for kind, tool, text in self.steps(message) if tool == "pages" and kind == "todo"]
        self.assertEqual(texts, ["2 written pages carry no label and belong to no paper (page 1, pages 8–9). If one is a student's page, "
                                 "recover its label from its printed alias; otherwise set it aside."])
        # The papers are labeled on every page and never grouped, so their stage counts no "papers grouped of first pages" while it
        # waits on the stray pages (take-home, 2026-11-11: "0/90" beside 90 papers put together; instructor: "it's showing 90 papers").
        papers = next(stage for stage in self.scans.review_item(message)["overview"]["stages"] if stage["key"] == "papers")
        self.assertEqual((papers["state"], papers["status"], papers["progress"]), ("started", "Needs you", None))
        # Nor does Papers plan a grouping of them at the side count their labels suggest ("it says i need to group 74 pages").
        self.assertIsNotNone(self.scans.review_item(message)["sequence"]["suggested_sides"])
        check = self.scans.papers_to_check(message)
        self.assertEqual((check["sides"], check["ready"], check["papers"]), (None, 0, []))
        refused = self.set_aside(pages[9]["id"], reason="Blank")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("is blank", refused.json["error"])
        self.assertEqual(self.set_aside(pages[1]["id"], reason="Section header sheet").status_code, 200)
        texts = [text for kind, tool, text in self.steps(message) if tool == "pages" and kind == "todo"]
        self.assertEqual(texts, ["1 written page carries no label and belongs to no paper (pages 8–9). If one is a student's page, "
                                 "recover its label from its printed alias; otherwise set it aside."])
        self.assertEqual(self.set_aside(pages[8]["id"], reason="Scratch paper").status_code, 200)
        self.assertEqual([step for step in self.steps(message) if step[1] == "pages"], [])
        self.assertEqual([(entry["page"], entry["reason"]) for entry in self.scans.review_item(message)["set_aside"]],
                         [(1, "Section header sheet"), (8, "Scratch paper")])


class StrayPagesInGroupingTests(unittest.TestCase):
    """Paper labeled on its first page only (a stamps-only label sheet): set-aside pages and the grouping."""
    setUp = sequence_fixture.SequenceTests.setUp
    submit = sequence_fixture.SequenceTests.submit
    post = sequence_fixture.SequenceTests.post
    review = sequence_fixture.SequenceTests.review

    def stamped_scan(self, *exams, before=()):
        """`exams` as in test_sequence (sides after each label); `before`: written sheets ahead of the first label."""
        catalog = self.documents.catalog()
        stamps = self.documents.register_stamp_pages(catalog["assignments"][0]["id"], 1)
        batch = self.documents.create_batch(stamps["id"], catalog["sections"][0]["id"], len(exams), str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        labels = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        writer = PdfWriter()
        for text in before:
            writer.add_page(written_sheet(text))
        for copy, sides in enumerate(exams):
            writer.add_page(labels.pages[copy])
            for side in sides:
                if side == BLANK:
                    writer.add_blank_page(612, 792)
                elif side.startswith("S"):
                    writer.add_page(written_sheet(f"scratch {side}"))
                else:
                    writer.add_page(self.plain.pages[int(side[1]) - 1])
        content = io.BytesIO()
        writer.write(content)
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "labels.pdf", "content": content.getvalue()}], source="cli", sender="test",
                                idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        self.assertTrue(self.scans.process_one())
        return self.documents.get_batch(batch["id"]), job

    def ids(self, message):
        return {page["source_page_index"]: page["id"] for page in self.scans.review_item(message)["pages"]}

    def test_a_header_sheet_set_aside_lets_the_scan_group_itself(self):
        # The header sheet before the first label held automatic grouping back (a stretch in no paper); set aside, it is none.
        batch, job = self.stamped_scan([P2, P3], [P2, P3], before=("SECTION 507",))
        message = job["message_id"]
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
        preview = self.post(message, "/preview", sides=3).json
        self.assertEqual((preview["counts"]["ready"], preview["counts"]["unlabeled"], [run["where"] for run in preview["runs"]]),
                         (2, 1, ["before the first label"]))
        check = self.client.get(f"/api/documents/messages/{message}/papers-to-check").json
        stray, = [paper for paper in check["papers"] if paper["kind"] == "pages"]
        self.assertEqual([(side["page"], side.get("can_set_aside")) for side in stray["pages"]], [(1, True)])
        header = self.ids(message)[1]
        response = self.client.post(f"/api/documents/scans/{header}/set-aside", json={"reason": "Section header sheet"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        preview = self.post(message, "/preview", sides=3).json
        self.assertEqual((preview["counts"]["ready"], preview["counts"]["unlabeled"], preview["runs"]), (2, 0, []))
        check = self.client.get(f"/api/documents/messages/{message}/papers-to-check").json
        self.assertEqual([paper["kind"] for paper in check["papers"]], [])
        self.assertEqual([(entry["page"], entry["reason"]) for entry in check["set_aside"]], [(1, "Section header sheet")])
        # The worker's catch-up sees the changed scan and applies the even cadence by itself, as on arrival.
        self.assertEqual(self.scans.sequence.auto_group(message), 3)
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"][0]["packets"], 2)

    def test_a_set_aside_page_is_never_given_a_place_in_a_paper(self):
        batch, job = self.stamped_scan([P2, P3], [P2, P3], [P2, P3])
        message = job["message_id"]
        run, = self.scans.review_item(message)["sequence"]["runs"]
        self.post(message, f"/{run['id']}/undo")
        page = self.ids(message)[6]  # copy 2's third side
        self.assertEqual(self.client.post(f"/api/documents/scans/{page}/set-aside", json={"reason": "Scratch paper"}, headers=HEADERS).status_code, 200)
        preview = self.post(message, "/preview", sides=3).json
        self.assertEqual(preview["counts"]["ready"], 2)
        self.assertEqual(preview["exams"][1]["problems"], ["Attachment page 6 was set aside as part of no paper, so its sides cannot be counted from here. "
                                                           "Choose its sides by hand."])


class HeldPaperTests(unittest.TestCase):
    """A paper whose sides do not add up, grouped by hand from the sides the instructor ticks."""
    setUp = sequence_fixture.SequenceTests.setUp
    submit = sequence_fixture.SequenceTests.submit
    post = sequence_fixture.SequenceTests.post
    review = sequence_fixture.SequenceTests.review
    stamped_scan = StrayPagesInGroupingTests.stamped_scan
    ids = StrayPagesInGroupingTests.ids

    def check(self, message):
        response = self.client.get(f"/api/documents/messages/{message}/papers-to-check")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def held(self, message):
        return next(paper for paper in self.check(message)["papers"] if paper["kind"] == "paper" and not paper["ready"])

    def group(self, message, paper, sides, **extra):
        data = {"first_scan_page_id": paper["pages"][0]["scan_page_id"] if paper["pages"][0]["first"] else None,
                "scan_page_ids": sides, "fingerprint": paper.get("choose", {}).get("fingerprint"), "reason": "A student added a sheet",
                **extra}
        return self.client.post(f"/api/documents/messages/{message}/papers/held", json=data, headers=HEADERS)

    def test_a_held_paper_is_grouped_from_the_sides_ticked_and_its_extra_sheet_set_aside(self):
        # Copy 2 holds a scratch sheet between its sides: five sides at four per paper, so it is held and nothing is guessed.
        batch, job = self.stamped_scan([P2, P3, P2], [P2, "S1", P3, P2], [P2, P3, P2])
        message = job["message_id"]
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual(self.post(message, sides=4, fingerprint=plan["fingerprint"]).status_code, 201)
        paper = self.held(message)
        self.assertEqual((paper["copy_number"], paper["first_page"], paper["last_page"]), (2, 5, 9))
        self.assertEqual((paper["choose"]["copy_number"], paper["choose"]["sides"], paper["choose"]["pages_issued"]), (2, 5, None))
        ids = [side["scan_page_id"] for side in paper["pages"]]
        # Refused: the first side is not its label, the order is not the scan's, a side of another paper, a stale look, no reason.
        self.assertEqual(self.group(message, paper, [ids[1], ids[0]]).status_code, 400)
        self.assertEqual(self.group(message, paper, [ids[0], ids[3], ids[2]]).status_code, 400)
        self.assertEqual(self.group(message, paper, [ids[0]]).status_code, 400)
        self.assertEqual(self.group(message, paper, [ids[0], self.ids(message)[2]]).status_code, 400)
        self.assertEqual(self.group(message, paper, [ids[0], ids[1]], reason="").status_code, 400)
        stale = self.group(message, paper, [ids[0], ids[1], ids[3], ids[4]], fingerprint="stale")
        self.assertEqual(stale.status_code, 409)
        self.assertIn("changed after it was shown", stale.json["error"])
        # Grouped: the n-th ticked side is page n; the scratch side stays as scanned, unidentified and retained. Every owner lock is
        # taken under the lifecycle lock, the scan's own among them.
        locked, owner_lock = [], self.documents.files.owner_lock

        @contextmanager
        def watched(owner):
            locked.append((owner, bool(getattr(self.documents.rosters._lock_state, "held", False))))
            with owner_lock(owner):
                yield
        with patch.object(self.documents.files, "owner_lock", side_effect=watched):
            done = self.group(message, paper, [ids[0], ids[1], ids[3], ids[4]], request_id="hand-1")
        self.assertEqual(done.status_code, 201, done.json)
        self.assertIn((message, True), locked)
        self.assertTrue(all(held for _, held in locked), locked)
        self.assertEqual((done.json["copy_number"], done.json["pages"], done.json["identified"], done.json["repeated"]), (2, 4, 3, False))
        again = self.group(message, paper, [ids[0], ids[1], ids[3], ids[4]], request_id="hand-1")
        self.assertEqual((again.status_code, again.json["run_id"], again.json["repeated"]), (200, done.json["run_id"], True))
        item = self.scans.review_item(message)
        rows = {page["source_page_index"]: page for page in item["pages"]}
        with connect(self.path) as db:
            stamps = {row["id"]: row["page_number"] for row in db.execute(
                "SELECT p.id,p.page_number FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=? AND c.copy_number=2", (batch["id"],))}
            events = [json.loads(row[0]) for row in db.execute("SELECT detail FROM inbox_events WHERE kind='held_paper_grouped'")]
        self.assertEqual([(rows[index]["status"], stamps.get(rows[index]["page_stamp_id"])) for index in (5, 6, 7, 8, 9)],
                         [("resolved", 1), ("manual", 2), ("unreadable", None), ("manual", 3), ("manual", 4)])
        self.assertEqual([rows[index]["packet"]["side"] if rows[index]["packet"] else None for index in (5, 6, 7, 8, 9)], [1, 2, None, 3, 4])
        event, = events
        self.assertEqual((event["actor"], event["copy"], [page["page"] for page in event["pages"]], event["left_as_scanned"], event["reason"], event["request_id"]),
                         ("workspace", 2, [5, 6, 8, 9], [7], "A student added a sheet", "hand-1"))
        # The run says so; the paper is no longer held; its side left as scanned is listed with it and can be set aside.
        hand = next(run for run in item["sequence"]["runs"] if run.get("origin") == "held_paper")
        self.assertEqual((hand["sides"], hand["copy_number"], hand["sides_chosen"], hand["reason"]), (4, 2, [5, 6, 8, 9], "A student added a sheet"))
        check = self.check(message)
        self.assertEqual([paper["kind"] for paper in check["papers"]], ["by_hand"])
        by_hand, = check["papers"]
        self.assertEqual(([(side["page"], side["side"]) for side in by_hand["pages"]], [(side["page"], side.get("can_set_aside")) for side in by_hand["left"]],
                          by_hand["run_id"], by_hand["reason"], by_hand["assembled"], check["grouped"]),
                         ([(5, 1), (6, 2), (8, 3), (9, 4)], [(7, True)], hand["id"], "A student added a sheet", False, 2))
        steps = [(step["kind"], step["text"]) for step in item["overview"]["steps"]]
        self.assertIn(("done", "2 papers grouped, 4 sides each; 1 paper grouped by hand."), steps)
        self.assertIn(("check", "1 page is in no grouped paper: page 7. Recover a label from its printed alias, or set aside a page that belongs to no paper."), steps)
        # Grouping the rest again (another preview at the same count) leaves the paper grouped by hand alone.
        again = self.post(message, "/preview", sides=4).json
        self.assertEqual((again["counts"]["ready"], again["counts"]["review"], again["counts"]["complete"]), (0, 0, 3))
        self.assertEqual(again["exams"][1]["problems"], [])
        # The scratch side set aside: nothing is left open, and the review makes each paper its student's reviewed paper.
        self.assertEqual(self.client.post(f"/api/documents/scans/{rows[7]['id']}/set-aside", json={"reason": "Scratch paper"}, headers=HEADERS).status_code, 200)
        self.assertEqual([step for step in self.scans.review_item(message)["overview"]["steps"]
                          if step["kind"] in ("todo", "check") and step["action"] != "grading_review"], [])
        self.review(job)
        reviewed = sorted(self.scans.review_item(message)["assemblies"], key=lambda assembly: assembly["copy_number"])
        self.assertEqual([len(assembly["revisions"]) for assembly in reviewed], [1, 1, 1])
        self.assertEqual([page["page_number"] for page in json.loads(reviewed[1]["revisions"][0]["selection_json"])], [1, 2, 3, 4])
        # A reviewed paper now: its grouping by hand stays (undo is refused, and says where the paper is changed), and Papers to
        # check says so instead of offering Undo. The papers the instructor's grouping made are counted apart from it.
        check = self.check(message)
        by_hand, = [paper for paper in check["papers"] if paper["kind"] == "by_hand"]
        self.assertEqual((by_hand["assembled"], check["grouped"]), (True, 2))
        refused = self.post(message, f"/{hand['id']}/undo")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Copy 2 is already a reviewed paper with these pages", refused.json["error"])
        self.assertIsNone(next(run for run in self.scans.review_item(message)["sequence"]["runs"] if run["id"] == hand["id"])["undone_at"])

    def test_undo_returns_the_paper_to_held_and_a_rescan_of_the_copy_follows_the_intake_rules(self):
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2, P3])
        message = job["message_id"]
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual(self.post(message, sides=4, fingerprint=plan["fingerprint"]).status_code, 201)
        paper = self.held(message)
        ids = [side["scan_page_id"] for side in paper["pages"]]
        done = self.group(message, paper, ids[:4])
        self.assertEqual(done.status_code, 201, done.json)
        # Undone like any grouping: its pages lose the identities it gave and the paper is held again, with the same look.
        undone = self.post(message, f"/{done.json['run_id']}/undo")
        self.assertEqual(undone.status_code, 200)
        self.assertEqual([page["status"] for page in undone.json["pages"][4:]], ["resolved", "unreadable", "unreadable", "unreadable", "unreadable"])
        again = self.held(message)
        self.assertEqual(again["choose"]["fingerprint"], paper["choose"]["fingerprint"])
        self.assertEqual(self.group(message, again, ids[:4]).status_code, 201)
        # A later scan of the same copy with different content on its page 2: the identity given by hand stands, and that page
        # conflicts by barcode intake's rule, so the later paper is held rather than grouped over it.
        labels = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        writer = PdfWriter()
        writer.add_page(labels.pages[1])
        writer.add_page(written_sheet("a different page 2"))
        writer.add_page(self.plain.pages[2])
        writer.add_page(self.plain.pages[1])
        later = self.submit(writer)
        plan = self.post(later, "/preview", sides=4).json
        self.assertIn("Page 2 of this copy was already identified on another scanned page with different content.", plan["exams"][0]["problems"])
        self.assertEqual(plan["counts"]["ready"], 0)
        # Once the first scan leaves the Inbox, it no longer counts (INBOX: PRESENT) and the later paper adds up on its own.
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete", "rescanned")
        plan = self.post(later, "/preview", sides=4).json
        self.assertEqual((plan["exams"][0]["problems"], plan["counts"]["ready"]), ([], 1))

    def test_only_a_held_paper_with_its_page_one_label_read_is_grouped_by_hand_and_never_over_a_grouped_page(self):
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2, P3], [P2, P3, P2])
        message = job["message_id"]
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual(self.post(message, sides=4, fingerprint=plan["fingerprint"]).status_code, 201)
        paper = self.held(message)
        ids = [side["scan_page_id"] for side in paper["pages"]]
        # A grouped paper is not held: grouping the first copy "by hand" is refused.
        first = self.ids(message)
        refused = self.client.post(f"/api/documents/messages/{message}/papers/held", json={
            "first_scan_page_id": first[1], "scan_page_ids": [first[1], first[2]], "fingerprint": paper["choose"]["fingerprint"],
            "reason": "x"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("no longer held", refused.json["error"])
        # A side set aside is never ticked; a stamps-only label sheet has no issued page count, so every side may be ticked.
        self.assertEqual(self.client.post(f"/api/documents/scans/{ids[4]}/set-aside", json={"reason": "Scratch paper"}, headers=HEADERS).status_code, 200)
        paper = self.held(message)
        self.assertEqual([side.get("set_aside", False) for side in paper["pages"]], [False, False, False, False, True])
        refused = self.group(message, paper, ids)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("set aside", refused.json["error"])
        self.assertEqual(self.group(message, paper, ids[:4]).status_code, 201)
        # The worker's identification of grouped papers ignores the hand-made packet's own side count.
        self.assertEqual(self.scans.sequence.identify_grouped(message, "automatic"), 0)


    def test_grouping_one_held_paper_by_hand_lets_the_papers_that_add_up_group_themselves(self):
        # Copy 2, in the middle, holds a scratch sheet: the labels are not evenly spaced, so nothing was grouped on arrival, and the
        # instructor has not pressed Group (PIPELINE: automatic grouping stays all-or-nothing).
        batch, job = self.stamped_scan([P2, P3], [P2, "S1", P3], [P2, P3])
        message = job["message_id"]
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
        self.assertIsNone(self.scans.sequence.auto_group(message))
        paper = self.held(message)
        ids = [side["scan_page_id"] for side in paper["pages"]]
        self.assertEqual(self.group(message, paper, [ids[0], ids[1], ids[3]]).status_code, 201)
        # The one paper holding the grouping back is settled by the instructor: the worker's catch-up groups the rest by itself, at
        # the distance the other labels keep (the paper grouped by hand is no evidence of it), and the hand-made paper stays as it is.
        self.assertEqual(self.scans.sequence.auto_group(message), 3)
        item = self.scans.review_item(message)
        runs = sorted(item["sequence"]["runs"], key=lambda run: run["created_at"])
        self.assertEqual([(run.get("origin"), run["automatic"], run["packets"]) for run in runs], [("held_paper", False, 1), (None, True, 2)])
        self.assertEqual([page["packet"]["side"] if page["packet"] else None for page in item["pages"]], [1, 2, 3, 1, 2, None, 3, 1, 2, 3])
        steps = [(step["kind"], step["text"]) for step in item["overview"]["steps"]]
        self.assertIn(("done", "2 papers grouped automatically, 3 sides each; 1 paper grouped by hand."), steps)
        self.assertIn(("check", "1 page is in no grouped paper: page 6. Recover a label from its printed alias, or set aside a page that belongs to no paper."), steps)
        check = self.check(message)
        self.assertEqual((check["grouped"], [paper["kind"] for paper in check["papers"]]), (2, ["by_hand"]))
        # Once settled, nothing groups it again: a second catch-up finds a run of the scan and leaves it.
        self.assertIsNone(self.scans.sequence.auto_group(message))

    def test_a_copy_with_a_reviewed_paper_or_a_scan_deleted_from_the_inbox_is_not_grouped_by_hand(self):
        batch, job = self.stamped_scan([P2, P3], [P2, "S1", P3], [P2, P3], before=("SECTION 507",))
        message = job["message_id"]
        check = self.check(message)
        paper = self.held(message)
        stray, = [item for item in check["papers"] if item["kind"] == "pages"]
        self.assertEqual(([side.get("can_set_aside") for side in stray["pages"]], paper["copy_number"], "choose" in paper), ([True], 2, True))
        ids = [side["scan_page_id"] for side in paper["pages"]]
        # Deleted from the Inbox, the scan offers neither, and grouping by hand is refused (INBOX: PRESENT).
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete", "a duplicate upload")
        check = self.check(message)
        gone = next(item for item in check["papers"] if item["kind"] == "paper" and not item["ready"])
        stray, = [item for item in check["papers"] if item["kind"] == "pages"]
        self.assertEqual((gone.get("choose"), gone["choose_refused"], [side.get("can_set_aside") for side in stray["pages"]]),
                         (None, "This scan was deleted from the Inbox. Restore it first.", [None]))
        refused = self.group(message, paper, [ids[0], ids[1], ids[3]])
        self.assertEqual(refused.status_code, 409)
        self.assertIn("deleted from the Inbox", refused.json["error"])
        self.inbox.restore(job["id"])
        self.assertEqual(self.held(message)["choose"]["fingerprint"], paper["choose"]["fingerprint"])
        # Approved as a partial paper on Review first: that reviewed paper is where copy 2 is changed now, never beside it here.
        copy_id = next(page["copy_id"] for page in self.scans.review_item(message)["pages"] if page["id"] == ids[0])
        self.scans.select_assembly(copy_id, [ids[0]], True, "Page 1 alone, for now")
        held = self.held(message)
        self.assertEqual((held.get("choose"), held["choose_refused"]), (None, "Copy 2 already has a reviewed paper: change it on Review."))
        refused = self.group(message, paper, [ids[0], ids[1], ids[3]])
        self.assertEqual(refused.status_code, 409)
        self.assertIn("already has a reviewed paper", refused.json["error"])


class HeldPaperFirstPageDuplexTests(unittest.TestCase):
    """A print job the instructor confirmed as labeled on the first page, printed two-sided: every paper ends on a blank back."""
    setUp = sequence_fixture.SequenceTests.setUp
    submit = sequence_fixture.SequenceTests.submit
    post = sequence_fixture.SequenceTests.post
    check = HeldPaperTests.check
    held = HeldPaperTests.held
    group = HeldPaperTests.group

    def test_a_blank_back_keeps_its_place_past_the_pages_issued_and_a_written_side_never_does(self):
        self.documents.set_label_mode(self.batch["id"], "first_page")
        writer = PdfWriter()
        for copy, sides in ((1, [P2, P3, BLANK]), (2, [P2, "S", P3, BLANK]), (3, [P2, P3, BLANK])):
            writer.add_page(self.printed.pages[(copy - 1) * 3])
            for side in sides:
                if side == BLANK:
                    writer.add_blank_page(612, 792)
                elif side == "S":
                    writer.add_page(written_sheet("scratch work"))
                else:
                    writer.add_page(self.plain.pages[int(side[1]) - 1])
        message = self.submit(writer)
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["identified"]), (2, 4))
        self.assertEqual(self.post(message, sides=4, fingerprint=plan["fingerprint"]).status_code, 201)
        paper = self.held(message)
        self.assertEqual((paper["copy_number"], paper["choose"]["pages_issued"], paper["choose"]["sides"]), (2, 3, 5))
        ids = [side["scan_page_id"] for side in paper["pages"]]  # attachment pages 5-9: its label, p2, scratch, p3, blank back
        # A written side past the 3 pages issued is refused, by its own position; the blank back, like the other papers', is not.
        refused = self.group(message, paper, ids[:4])
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Copy 2 was issued 3 pages, so attachment page 8, which is written, cannot be its page 4. Untick 1 side.", refused.json["error"])
        done = self.group(message, paper, [ids[0], ids[1], ids[3], ids[4]])
        self.assertEqual((done.status_code, done.json["pages"], done.json["identified"]), (201, 4, 2), done.json)
        rows = {page["source_page_index"]: page for page in self.scans.review_item(message)["pages"]}
        with connect(self.path) as db:
            stamps = {row["id"]: row["page_number"] for row in db.execute("SELECT id,page_number FROM page_stamps")}
        self.assertEqual([(rows[index]["status"], stamps.get(rows[index]["page_stamp_id"]), rows[index]["packet"]["side"] if rows[index]["packet"] else None)
                          for index in range(5, 10)],
                         [("resolved", 1, 1), ("manual", 2, 2), ("unreadable", None, None), ("manual", 3, 3), ("blank", None, 4)])


class HeldPaperLabeledOnEveryPageTests(unittest.TestCase):
    """A paper labeled on every page: a labeled side must be its own page, and no more sides than were issued."""
    setUp = sequence_fixture.SequenceTests.setUp
    scan = sequence_fixture.SequenceTests.scan
    submit = sequence_fixture.SequenceTests.submit
    post = sequence_fixture.SequenceTests.post
    check = HeldPaperTests.check
    held = HeldPaperTests.held
    group = HeldPaperTests.group

    def test_a_labeled_side_keeps_its_page_and_an_issued_paper_takes_no_more_pages_than_it_has(self):
        message = self.scan((1, ["L1", P2, P3]), (2, ["L1", P2, "p1", "L3"]))
        plan = self.post(message, "/preview", sides=3).json
        self.assertEqual(self.post(message, sides=3, fingerprint=plan["fingerprint"]).status_code, 201)
        paper = self.held(message)
        self.assertEqual((paper["copy_number"], paper["choose"]["pages_issued"]), (2, 3))
        ids = [side["scan_page_id"] for side in paper["pages"]]
        too_many = self.group(message, paper, ids)
        self.assertEqual(too_many.status_code, 409)
        self.assertIn("issued 3 pages", too_many.json["error"])
        wrong = self.group(message, paper, [ids[0], ids[3]])
        self.assertEqual(wrong.status_code, 409)
        self.assertIn("carries the label of page 3, so it cannot be page 2", wrong.json["error"])
        done = self.group(message, paper, [ids[0], ids[1], ids[3]])
        self.assertEqual((done.status_code, done.json["identified"]), (201, 1), done.json)


if __name__ == "__main__":
    unittest.main()
