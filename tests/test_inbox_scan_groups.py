"""The Inbox list groups the scans of one assignment (teach_inbox/service.py `_scan_groups`, 2026-10-02): on the Inbox's own
read (`files=input`) each listed scan row gains the additive `scan.assignment` ({plan_id, title, code, term} of its latest
grading association, or None), `scan.pages` and `scan.papers`, read for the whole page at once. Nothing else in that body
changes, and the default body (no `files`) is exactly what it was. Runs on the real graded workspace of `test_gradescope_send`
(a section scan associated with its exam's grading assignment)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import unittest  # noqa: E402

import test_gradescope_send as graded  # noqa: E402
from rosters import connect  # noqa: E402
from teach_inbox.service import InboxService  # noqa: E402

ADDED = ("assignment", "pages", "papers")


class ScanGroupFields(graded.GradescopeCase):
    def listing(self, status):
        return self.client.get(f"/api/inbox/jobs?status={status}&files=input").get_json()

    def test_a_listed_scan_names_its_assignment_and_counts_its_pages_and_papers(self):
        with connect(self.path) as db:
            title, code, term, other, year = db.execute("""SELECT e.title,t.code,t.term,t.term_other,t.year FROM frq_assignments g
                JOIN exam_events e ON e.id=g.event_id JOIN course_terms t ON t.id=e.term_id WHERE g.id=?""", (self.aid,)).fetchone()
            linked = {row[0] for row in db.execute("SELECT message_id FROM frq_scan_links WHERE assignment_id=?", (self.aid,))}
            pair = tuple(db.execute("SELECT e.assignment_id,e.term_id FROM frq_assignments g JOIN exam_events e ON e.id=g.event_id WHERE g.id=?", (self.aid,)).fetchone())
            pages = {mid: db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (mid,)).fetchone()[0] for mid in linked}
            papers = {mid: db.execute("""SELECT COUNT(DISTINCT p.printed_copy_id) FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                                         WHERE sp.message_id=?""", (mid,)).fetchone()[0] for mid in linked}
        self.assertTrue(linked, "the graded workspace has an associated scan")
        scans = [job for status in ("current", "history") for job in self.listing(status)["jobs"] if job.get("scan")]
        found = [job for job in scans if job["message_id"] in linked]
        self.assertTrue(found)
        for job in found:
            # assignment_id and term_id (2026-10-18) let the group header open the assignment's Collect.
            self.assertEqual(job["scan"]["assignment"], {"plan_id": self.aid, "title": title, "code": code, "term": f"{other if term == 'Other' else term} {year}",
                                                         "assignment_id": pair[0], "term_id": pair[1]})
            self.assertRegex(job["scan"]["assignment"]["term"], r"^\S.* \d{4}$")
            self.assertEqual(job["scan"]["pages"], pages[job["message_id"]])
            self.assertEqual(job["scan"]["papers"], papers[job["message_id"]])
            self.assertGreater(job["scan"]["papers"], 0)
        # A scan with no association lists no assignment.
        for job in scans:
            if job["message_id"] not in linked:
                self.assertIsNone(job["scan"]["assignment"])

    def test_the_list_body_is_otherwise_unchanged(self):
        # The same listing with the grouping read left out is the listing with the three additive fields removed, key for key:
        # nothing else in a row, the counts or the paging moves.
        for status in ("current", "history", None):
            query = f"?files=input&status={status}" if status else "?files=input"
            grouped = self.client.get(f"/api/inbox/jobs{query}").get_json()
            with patch.object(InboxService, "_scan_groups", staticmethod(lambda db, message_ids: {})):
                plain = self.client.get(f"/api/inbox/jobs{query}").get_json()
            for job in grouped["jobs"]:
                if job.get("scan"):
                    self.assertTrue(set(ADDED) <= set(job["scan"]))
                    job["scan"] = {key: value for key, value in job["scan"].items() if key not in ADDED}
            self.assertEqual(grouped, plain)

    def test_the_default_body_carries_no_grouping_fields(self):
        # A read without files=input (the assistant, other services, older callers) never runs the grouping read and carries none
        # of its fields, so its body is what it was before the grouping.
        seen = 0
        for status in ("current", "history", None):
            query = f"?status={status}" if status else ""
            with patch.object(InboxService, "_scan_groups", side_effect=AssertionError("grouping read for a default list")):
                body = self.client.get(f"/api/inbox/jobs{query}")
            self.assertEqual(body.status_code, 200)
            for job in body.get_json()["jobs"]:
                if job.get("scan"):
                    seen += 1
                    self.assertFalse(set(ADDED) & set(job["scan"]))
        self.assertTrue(seen)

    def test_kinds_filters_and_assignment_search(self):
        # INBOX "SCANS AND SUBMISSIONS" (2026-10-18): kind keeps scans apart from other submissions, needs keeps only what waits for
        # the instructor, assignment keeps one grading assignment's scans, and search finds a scan by its assignment's title.
        get = lambda query: self.client.get(f"/api/inbox/jobs?files=input&{query}").get_json()
        with connect(self.path) as db:
            title, = db.execute("SELECT e.title FROM frq_assignments g JOIN exam_events e ON e.id=g.event_id WHERE g.id=?", (self.aid,)).fetchone()
            linked = {row[0] for row in db.execute("SELECT message_id FROM frq_scan_links WHERE assignment_id=?", (self.aid,))}
        everything = {job["id"] for status in ("current", "history") for job in get(f"status={status}")["jobs"]}
        scans = {job["id"] for status in ("current", "history") for job in get(f"status={status}&kind=scan")["jobs"]}
        commands = [job for job in get("kind=command")["jobs"]]
        self.assertTrue(scans)
        self.assertTrue(scans <= everything)
        self.assertTrue(all(job["action"] != "scan.receive" for job in commands))
        body = get("status=current&kind=scan")
        self.assertEqual(set(body["kind_counts"]), {"scan", "command"})
        self.assertEqual(body["counts"], body["kind_counts"]["scan"])
        self.assertTrue(all(job["action"] == "scan.receive" for job in body["jobs"]))
        # The assignment's choices name it with how many listed scans it has.
        choice, = [item for item in body["assignments"] if item["plan_id"] == self.aid] or [None]
        for status in ("current", "history"):
            found = [item for item in get(f"status={status}&kind=scan")["assignments"] if item["plan_id"] == self.aid]
            if found:
                choice = found[0]
        self.assertIsNotNone(choice)
        self.assertEqual(choice["title"], title)
        mine = [job for status in ("current", "history") for job in get(f"status={status}&kind=scan&assignment={self.aid}")["jobs"]]
        self.assertTrue(mine)
        self.assertTrue(all(job["message_id"] in linked for job in mine))
        self.assertEqual(get("status=history&kind=scan&assignment=no-such-plan")["jobs"], [])
        for job in get("status=current&kind=scan&needs=1")["jobs"]:
            self.assertEqual(job["listed_as"], "needs_review")
        found = {job["message_id"] for status in ("current", "history") for job in get(f"status={status}&kind=scan&q={title.upper()}")["jobs"]}
        self.assertTrue(linked & found)
        # A read without `kind` answers as before: the assignment's title is not one of its search fields.
        before = {job["message_id"] for status in ("current", "history") for job in get(f"status={status}&q={title.upper()}")["jobs"]}
        self.assertFalse(linked & before)
        self.assertEqual(self.client.get("/api/inbox/jobs?kind=paper").status_code, 400)

    def test_a_list_without_kind_carries_no_kind_fields(self):
        for query in ("", "?files=input", "?status=current&files=input"):
            body = self.client.get(f"/api/inbox/jobs{query}").get_json()
            self.assertFalse({"kind_counts", "assignments"} & set(body))

    def test_a_scan_without_pages_or_tables_reads_nothing_more(self):
        with connect(self.path) as db:
            self.assertEqual(InboxService._scan_groups(db, []), {})
            self.assertEqual(InboxService._scan_groups(db, ["no-such-message"]), {"no-such-message": {"assignment": None, "pages": 0, "papers": 0}})


if __name__ == "__main__":
    unittest.main()
