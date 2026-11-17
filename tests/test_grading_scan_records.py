"""Scan records name each scan by its file, pages and papers: additive `workflow.scans` fields `filename`, `files` and `papers`,
read only, with every other field of a row unchanged (GRADING, Scan records)."""
import unittest

from rosters import connect
import test_frq_grading as fixture
import test_grading_intake as intake

# What a row carried before `filename`, `files` and `papers` were added; it keeps all of them, with the same values.
EARLIER = {"message_id", "job_id", "created_at", "instructor_review_required", "inspection_status", "pages", "inbox_ready"}


class ScanRecordFactsTests(unittest.TestCase):
    managed = False
    setUp = fixture.FrqWorkflowTests.setUp
    document_version = fixture.FrqWorkflowTests.document_version
    ready_batch = fixture.FrqWorkflowTests.ready_batch
    reviewed_source = fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = fixture.FrqWorkflowTests.configure_rubric
    inspected = intake.GradingIntakeTests.inspected

    def test_each_scan_row_names_its_file_and_the_papers_its_pages_were_matched_to(self):
        message = self.inspected()
        self.frq.sync_scan(message)
        [row] = self.frq.assignment(self.plan["id"])["workflow"]["scans"]
        self.assertEqual(set(row), EARLIER | {"filename", "files", "papers"})
        with connect(self.path) as db:
            filename = db.execute("SELECT filename FROM inbox_artifacts WHERE job_id=? AND role='input'", (row["job_id"],)).fetchone()[0]
            papers = db.execute("""SELECT COUNT(DISTINCT s.printed_copy_id) FROM scan_pages p JOIN page_stamps s ON s.id=p.page_stamp_id
                                   WHERE p.message_id=? AND p.status IN ('resolved','manual')""", (message,)).fetchone()[0]
            pages = db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (message,)).fetchone()[0]
        self.assertEqual((row["message_id"], row["filename"], row["files"], row["papers"], row["pages"]), (message, filename, 1, papers, pages))
        self.assertTrue(row["filename"])
        self.assertGreaterEqual(row["papers"], 1)
        # The Grading index carries the same rows.
        listed = next(p for p in self.frq.overview(include_health=False)["assignments"] if p["id"] == self.plan["id"])
        self.assertEqual(listed["workflow"]["scans"], [row])
        # A scan uploaded with several files is named by the first one uploaded, whatever its random id sorts as, and counts them all.
        with connect(self.path) as db:
            first = db.execute("SELECT * FROM inbox_artifacts WHERE job_id=? AND role='input'", (row["job_id"],)).fetchone()
            db.execute("INSERT INTO inbox_artifacts VALUES (?,?,?,?,?,?,?,?,?)", ("0" * 36, first["message_id"], first["job_id"], "second-upload.pdf",
                       f"inputs/{'0' * 36}.bin", "input", 1, "0" * 64, first["created_at"]))
        [row] = self.frq.assignment(self.plan["id"])["workflow"]["scans"]
        self.assertEqual((row["filename"], row["files"]), (filename, 2))
        # A page whose label did not resolve is no paper of this assignment.
        with connect(self.path) as db:
            db.execute("UPDATE scan_pages SET status='unknown' WHERE message_id=?", (message,))
        [row] = self.frq.assignment(self.plan["id"])["workflow"]["scans"]
        self.assertEqual(row["papers"], 0)


if __name__ == "__main__":
    unittest.main()
