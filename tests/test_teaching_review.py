"""TEACHING-WORKFLOWS: historical evidence and read-only delivery on fake services."""
import json
import unittest

import test_grading_certification as fixture
from gradebook import canvas_posting
from rosters import connect


class Review(fixture.Certify):
    def test_certified_evidence_stays_pinned_and_delivery_reads_only(self):
        service = self.app.extensions['teaching_review']
        self.assertIsNone(service.insights(self.aid)['certification'])
        self.certify()
        before = self.counts()
        first = service.insights(self.aid)
        self.assertEqual(sum(q['n'] for q in first['questions']), 4)
        self.assertTrue(any(line['selected'] for q in first['questions'] for line in q['rubric_lines']))
        delivery = service.delivery(self.aid)
        self.assertEqual(delivery['certification']['state'], 'current')
        self.assertEqual(len(delivery['columns']), 1)
        self.assertEqual(delivery['columns'][0]['recorded_cells'], 2)
        self.assertEqual(delivery['columns'][0]['without_receipt'], 2)
        self.assertEqual(self.counts(), before)
        self.decide('q20', 'A', ['R3_3'])
        after = service.insights(self.aid)
        self.assertEqual(first['questions'], after['questions'])
        self.assertEqual(service.delivery(self.aid)['certification']['state'], 'changed')
        # PRESENT governs historical teaching evidence too: a removed source contributes no observations.
        with connect(self.path) as db:
            row = db.execute('SELECT source_json FROM frq_tasks LIMIT 1').fetchone()
            message_id = json.loads(row[0])['regions'][0]['message_id']
        # Seed the retained tombstone directly in this synthetic catalog. The current
        # removal UI refuses reviewed assemblies; historical tombstones must still be respected.
        with connect(self.path) as db:
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)",
                       (message_id, "workspace", "Synthetic retained deletion", "2026-10-03T00:00:00Z"))
        deleted = service.insights(self.aid)
        self.assertLess(sum(q['n'] for q in deleted['questions']), 4)
        self.assertGreater(deleted['excluded']['removed_or_unavailable_source'], 0)

    def test_delivery_carries_what_the_gradebook_last_read_of_each_columns_posting(self):
        """POSTED IN CANVAS (requirement, 2026-10-11): the delivery read gives each column the gradebook's own latest read of its
        posting in Canvas (`canvas_posting.marks`), None before any read; it reads the catalog only and writes nothing."""
        service = self.app.extensions['teaching_review']
        self.certify()
        first = service.delivery(self.aid)
        column, term = first['columns'][0]['id'], first['term_id']
        self.assertIsNone(first['columns'][0]['posting'])
        read = lambda course, graded, hidden: {'item_id': column, 'course_id': course, 'assignment_id': '9' + course, 'manual': True, 'graded': graded, 'hidden': hidden}
        with connect(self.path) as db:
            canvas_posting.record(db, term, [read('101', 20, 20), read('102', 12, 2)], 'sync', read_at='2026-10-10T11:00:00+00:00')
            # Another column's read is not this assessment's.
            canvas_posting.record(db, term, [{**read('101', 5, 5), 'item_id': 'another-column', 'assignment_id': '777'}], 'sync', read_at='2026-10-10T11:00:00+00:00')
        before = self.counts()
        with connect(self.path) as db:
            marks = canvas_posting.marks(db, term)
        hidden = service.delivery(self.aid)['columns'][0]['posting']
        self.assertEqual(hidden, marks[column])
        self.assertEqual((hidden['graded'], hidden['hidden'], hidden['courses'], hidden['manual'], hidden['read_at']), (32, 22, 2, True, '2026-10-10T11:00:00+00:00'))
        # A later read of one course replaces that course's count only: the latest read of each Canvas assignment counts.
        with connect(self.path) as db:
            canvas_posting.record(db, term, [read('101', 20, 0)], 'post', read_at='2026-10-11T11:00:00+00:00')
        posted = service.delivery(self.aid)['columns'][0]['posting']
        self.assertEqual((posted['graded'], posted['hidden'], posted['courses'], posted['read_at'], posted['source']), (32, 2, 2, '2026-10-11T11:00:00+00:00', 'post'))
        self.assertEqual(self.counts(), before)
        # The assignment's overview reads the same columns.
        with connect(self.path) as db:
            assignment = db.execute('''SELECT e.assignment_id FROM exam_events e JOIN frq_assignments f ON f.event_id=e.id WHERE f.id=?''', (self.aid,)).fetchone()[0]
        overview = self.client.get(f'/api/assignments/{assignment}/{term}')
        self.assertEqual(overview.status_code, 200, overview.json)
        self.assertEqual(overview.json['delivery']['columns'][0]['posting'], posted)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(Review(name) for name in Review.__dict__ if name.startswith('test_'))
