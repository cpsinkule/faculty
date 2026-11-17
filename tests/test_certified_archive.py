"""Certified assessments leave the everyday lists (certified_archive.py; requirement, 2026-09-27): a VIEW over the latest
certification, on the real graded workspace of `test_gradescope_send`. Nothing is moved, changed or deleted."""
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import unittest  # noqa: E402

import certified_archive  # noqa: E402
import test_gradescope_send as graded  # noqa: E402
from gradebook import sources  # noqa: E402
from rosters import connect  # noqa: E402
from test_storage import HEADERS  # noqa: E402


class ArchivedAfterCertification(graded.GradescopeCase):
    def setUp(self):
        super().setUp()
        original = sources._links

        def links(db, term_id, assignment_id=None):  # FRQ is the one part certified here, as in test_grading_certification
            found = original(db, term_id, assignment_id)
            for value in found.values():
                value["parts"] = [part for part in value["parts"] if part == "frq"]
            return found
        self.enterContext(patch("gradebook.sources._links", side_effect=links))

    def certify(self):
        route = f"/api/grading/assignments/{self.aid}/certification"
        preview = self.client.post(route + "/preview", json={}, headers=HEADERS).get_json()
        body = {"request_id": str(uuid.uuid4()), "fingerprint": preview["fingerprint"], "confirmation": "certify",
                "acknowledged": {c["id"]: c["digest"] for c in preview["confirm"] if c.get("requires") == "tick"},
                "gradescope": {("written" if c["id"] == "gradescope_written" else "sheets"): {"decision": "do_not_send", "reason": "Test."}
                               for c in preview["confirm"] if c.get("requires") == "decision"}, "gradebook": {}}
        answer = self.client.post(route, json=body, headers=HEADERS)
        self.assertEqual(answer.status_code, 201, answer.get_json())

    def snapshot(self):
        with connect(self.path) as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("inbox_jobs", "inbox_deletions", "seating_charts", "print_batches", "exam_events", "frq_assignments")}

    def test_a_certified_assessment_folds_into_archived_everywhere_and_nothing_changes(self):
        with connect(self.path) as db:
            pair = tuple(db.execute("""SELECT e.assignment_id,e.term_id FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id
                WHERE f.id=?""", (self.aid,)).fetchone())
            messages = {r[0] for r in db.execute("SELECT message_id FROM frq_scan_links WHERE assignment_id=?", (self.aid,))}
            self.assertEqual(certified_archive.certified(db), {})
        self.assertTrue(messages, "the graded workspace has an associated scan")
        history = self.client.get("/api/inbox/jobs?status=history").get_json()
        listed = [job for job in history["jobs"] if job["message_id"] in messages]
        self.assertTrue(listed and not any(job.get("archived") for job in listed))
        before = self.snapshot()

        self.certify()
        with connect(self.path) as db:
            archive = certified_archive.certified(db)
            self.assertEqual(set(archive), {pair})
            self.assertEqual(set(certified_archive.certified_messages(db)), messages)
        # The Inbox: the finished scan leaves History for Archived; a stored "completed" filter still lists it.
        inbox = lambda status: self.client.get(f"/api/inbox/jobs?status={status}").get_json()
        self.assertFalse([job for job in inbox("history")["jobs"] if job["message_id"] in messages])
        archived = [job for job in inbox("archived")["jobs"] if job["message_id"] in messages]
        self.assertEqual(len(archived), len(listed))
        self.assertTrue(all(job["archived"]["certified_at"] for job in archived))
        self.assertEqual(inbox("archived")["counts"]["archived"], len(archived))
        # Assignments & printing marks the certified assessment's card row and its print jobs.
        catalog = self.client.get("/api/documents/catalog").get_json()
        rows = [row for row in catalog["lifecycle"] if (row["assignment_id"], row["term_id"]) == pair]
        self.assertTrue(rows and all(row["archived"] for row in rows))
        # Seating charts and Response Card issues of the assessment carry the mark too (when it has any).
        for chart in self.client.get("/api/seating").get_json():
            self.assertEqual(bool(chart["archived"]), (chart["assignment_id"], chart["term_id"]) == pair)
        # Attention counts (Home's cards, the sidebar badges, the Response Cards strip) leave certified charts and cards out.
        with connect(self.path) as db:
            condition = certified_archive.uncertified(db, "?", "?")
            self.assertEqual(db.execute(f"SELECT {condition}", pair).fetchone()[0], 0)
            self.assertEqual(db.execute(f"SELECT {condition}", ("another", pair[1])).fetchone()[0], 1)
            self.assertEqual(db.execute(f"SELECT {condition}", (None, pair[1])).fetchone()[0], 1)
        # New work stays out (a late makeup): a row made after the certification is neither archived nor free of attention.
        entry = archive[pair]
        later, earlier = entry["certified_at"][:-6] + "9" + entry["certified_at"][-6:], "2000-01-01T00:00:00+00:00"
        self.assertIsNone(certified_archive.facts(entry, later))
        self.assertEqual(certified_archive.facts(entry, earlier)["certification_id"], entry["certification_id"])
        with connect(self.path) as db:
            timed = certified_archive.uncertified(db, "?", "?", "?")
            self.assertEqual(db.execute(f"SELECT {timed}", (*pair, earlier)).fetchone()[0], 0)
            self.assertEqual(db.execute(f"SELECT {timed}", (*pair, "2999-01-01T00:00:00+00:00")).fetchone()[0], 1)
            made = certified_archive.pair_activity(db, *pair, entry["plan_id"])
            self.assertTrue(made and made <= entry["certified_at"])
            # A makeup scan associated after the certification leaves the Archived tab for the usual lists.
            link = db.execute("SELECT * FROM frq_scan_links WHERE assignment_id=? ORDER BY id DESC LIMIT 1", (self.aid,)).fetchone()
            columns = [name for name in link.keys() if name != "id"]
            db.execute(f"INSERT INTO frq_scan_links({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       [("2999-01-01T00:00:00+00:00" if name == "created_at" else "makeup-association" if name == "request_id" else link[name]) for name in columns])
            db.commit()
            self.assertNotIn(link["message_id"], certified_archive.certified_messages(db))
            self.assertIsNone(certified_archive.facts(entry, certified_archive.pair_activity(db, *pair, entry["plan_id"])))
        self.assertFalse([job for job in inbox("archived")["jobs"] if job["message_id"] == link["message_id"]])
        # Nothing was moved, deleted or changed by archiving.
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
