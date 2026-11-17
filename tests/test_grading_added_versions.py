"""A version added before its rubrics exist (requirement, 2026-09-25): it gets its own blank-template slot and is drafted with the others, and it changes nothing about how a paper's version is
decided. Synthetic template pages and a loopback model host."""
import unittest
import uuid

from werkzeug.exceptions import Conflict
from rosters import connect
import test_grading_rubric_versions as versions_fixture


class AddedVersions(unittest.TestCase):
    managed = False
    Versions = versions_fixture.RubricVersions
    Drafts = Versions.Drafts
    document_version, ready_batch, reviewed_source = Drafts.document_version, Drafts.ready_batch, Drafts.reviewed_source
    upload, uploaded_questions, template = Drafts.upload, Drafts.uploaded_questions, Drafts.template
    configure, calls = Drafts.configure, Drafts.calls
    setUp, answer, slots, draft, keep = Versions.setUp, Versions.answer, Versions.slots, Versions.draft, staticmethod(Versions.keep)

    def change(self, letter, added=True, **extra):
        plan = self.frq.assignment(self.plan['id'])
        return self.frq.change_added_version(self.plan['id'], {'version': plan['version'], 'exam_version': letter, **extra}, added)

    def rows(self):
        with connect(self.path) as db:
            return db.execute('SELECT exam_version,added FROM frq_added_versions ORDER BY rowid').fetchall()

    def test_an_added_version_has_its_own_template_slot_and_is_drafted_with_the_others(self):
        self.assertEqual([s['label'] for s in self.slots()], ['Version A', 'Version B'])
        with self.assertRaisesRegex(ValueError, 'Add Version C next'):
            self.change('D')  # Only the next letter, so no gap can appear.
        plan = self.frq.assignment(self.plan['id'])
        with self.assertRaises(Conflict):
            self.frq.change_added_version(self.plan['id'], {'version': plan['version'] - 1, 'exam_version': 'C'}, True)
        versions = self.change('C')['exam_versions']
        self.assertEqual((versions['letters'], versions['added'], versions['declared']), (['A', 'B', 'C'], ['C'], ['A', 'B']))
        self.change('C')  # Already a version: nothing more is recorded.
        self.assertEqual([tuple(r) for r in self.rows()], [('C', 1)])
        self.assertEqual([s['label'] for s in self.slots()], ['Version A', 'Version B', 'Version C'])
        # The instructor can take it back while it has no questions; the record stays.
        self.assertEqual(self.change('C', added=False)['exam_versions']['letters'], ['A', 'B'])
        self.assertEqual([s['label'] for s in self.slots()], ['Version A', 'Version B'])
        with self.assertRaisesRegex(Conflict, 'cannot be removed here'):
            self.change('B', added=False)  # Declared, not added here.
        self.change('C')
        self.assertEqual([tuple(r) for r in self.rows()], [('C', 1), ('C', 0), ('C', 1)])
        with connect(self.path) as db:
            with self.assertRaises(Exception):
                db.execute('DELETE FROM frq_added_versions')
        # One draft writes every problem for all three versions, and keeping it creates Version C's questions.
        generation = self.draft()
        self.assertEqual([v['name'] for v in generation['versions']], ['Version A', 'Version B', 'Version C'])
        problem = generation['result']['problems'][0]
        self.assertEqual([m['version'] for m in problem['versions']], ['Version A', 'Version B', 'Version C'])
        self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()), 'questions': [self.keep(problem)]})
        self.assertEqual(sorted(q['exam_version'] for q in self.uploaded_questions()), ['A', 'B', 'C'])
        with self.assertRaisesRegex(Conflict, 'has questions now'):
            self.change('C', added=False)
        self.assertEqual(self.frq.assignment(self.plan['id'])['exam_versions']['graded'], ['A', 'B', 'C'])


if __name__ == '__main__':
    unittest.main()
