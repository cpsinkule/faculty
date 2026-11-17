"""Rubrics → per-version references → acknowledged warning → answer-area checks."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from rosters import connect
import test_grading_uploads as upload_fixture
from test_assignment_templates import prepared
from document_identity.storage import publish
from teach_inbox.service import sha


class AssignmentTemplateWorkflow(unittest.TestCase):
    managed = False
    setUp = upload_fixture.RubricUploads.setUp
    document_version = upload_fixture.RubricUploads.document_version
    ready_batch = upload_fixture.RubricUploads.ready_batch
    reviewed_source = upload_fixture.RubricUploads.reviewed_source
    upload = upload_fixture.RubricUploads.upload
    uploaded_questions = upload_fixture.RubricUploads.uploaded_questions
    template = upload_fixture.RubricUploads.template

    def test_uploaded_reference_waits_for_warning_before_any_reading(self):
        self.upload()
        template = self.template()
        q = self.uploaded_questions()[0]
        self.assertEqual(q['location_check']['status'], 'waiting_templates')
        self.assertFalse(self.frq.process_location_one())
        self.assertEqual(self.reader.reads, 0)
        state = self.frq.assignment_templates(self.plan['id'])
        self.assertIn('same scanner', state['warning'])
        command = {'input_sha256': state['input_sha256'], 'selections': [{'key': state['slots'][0]['key'], 'template_id': template['id']}]}
        with self.assertRaisesRegex(ValueError, 'warning'):
            self.frq.confirm_assignment_templates(self.plan['id'], command)
        confirmed = self.frq.confirm_assignment_templates(self.plan['id'], {**command, 'acknowledge_warning': True})
        self.assertTrue(confirmed['confirmed'])
        self.assertEqual(self.uploaded_questions()[0]['rubric_id'], q['rubric_id'])
        self.assertEqual(self.uploaded_questions()[0]['content']['regions'], [])
        self.assertEqual(self.uploaded_questions()[0]['location_check']['status'], 'pending')
        for _ in range(15):
            self.frq.process_location_one()
        self.assertEqual(self.uploaded_questions()[0]['location_check']['status'], 'needs_confirmation')
        self.assertEqual(self.reader.reads, 2)

    def test_every_version_needs_its_own_explicit_selection(self):
        for letter in 'AB':
            self.upload(exam_version=letter)
        first = self.template()
        second = self.frq.templates.upload(self.assignment['id'], 'Version B', str(uuid.uuid4()), {'B.pdf': b'B'})
        state = self.frq.assignment_templates(self.plan['id'])
        self.assertEqual([s['exam_version'] for s in state['slots']], ['A', 'B'])
        choices = [{'key': s['key'], 'template_id': t['id']} for s, t in zip(state['slots'], (first, second))]
        with self.assertRaises(ValueError):
            self.frq.confirm_assignment_templates(self.plan['id'], {'input_sha256': state['input_sha256'], 'selections': choices[:1], 'acknowledge_warning': True})
        self.frq.confirm_assignment_templates(self.plan['id'], {'input_sha256': state['input_sha256'], 'selections': choices, 'acknowledge_warning': True})
        self.assertEqual([q['content']['template_id'] for q in self.uploaded_questions()], [first['id'], second['id']])
        self.assertTrue(all(q['location_check']['status'] == 'pending' for q in self.uploaded_questions()))

    def test_stale_rubrics_block_the_handoff_and_the_later_rubric_finalization_does_not(self):
        self.upload()
        template = self.template()
        state = self.frq.assignment_templates(self.plan['id'])
        self.upload('2_Question_19.csv')
        command = {'input_sha256': state['input_sha256'], 'selections': [{'key': state['slots'][0]['key'], 'template_id': template['id']}], 'acknowledge_warning': True}
        with self.assertRaises(Conflict):
            self.frq.confirm_assignment_templates(self.plan['id'], command)
        self.assertEqual(self.reader.reads, 0)
        # Templates share the first step with the uploads: the rubric finalization comes after the answer areas and never blocks them.
        self.assertEqual(self.frq.rubric_quality(self.plan['id'])['state'], 'needed')
        state = self.frq.assignment_templates(self.plan['id'])
        self.assertTrue(state['ready'])
        self.frq.confirm_assignment_templates(self.plan['id'], {**command, 'input_sha256': state['input_sha256'],
            'selections': [{'key': slot['key'], 'template_id': template['id']} for slot in state['slots']]})

    def test_generated_reference_uses_verified_saved_pdf_and_reuses_its_import(self):
        # Retain a real canonical through the managed owner API, while keeping
        # native conversion synthetic; the test never runs a PDF tool unsandboxed.
        pdf = b'Synthetic saved assignment PDF'
        document_id = str(uuid.uuid4())
        with self.documents.files.files('definition', self.assignment['definition_id'], write=True) as root:
            publish(root / f'{document_id}.pdf', pdf)
        with connect(self.path) as db:
            row = dict(db.execute('SELECT * FROM assignment_versions WHERE id=?', (self.version_id,)).fetchone())
            row.update(id=document_id, label='Generated B', canonical_path=f'{document_id}.pdf', canonical_sha256=sha(pdf))
            db.execute('INSERT INTO assignment_versions VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        self.ready_batch(document_id)
        self.upload(assignment_version_id=self.version_id)
        self.upload(assignment_version_id=document_id)
        with patch('document_identity.template_conversion.convert_template', side_effect=prepared) as conversion:
            first = self.frq.prepare_assignment_template(self.plan['id'], {'document_id': document_id})
            again = self.frq.prepare_assignment_template(self.plan['id'], {'document_id': document_id})
            self.assertEqual(conversion.call_count, 1)
            self.assertEqual(conversion.call_args.args[0], {'assignment.pdf': pdf})
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(first['source_version_id'], document_id)
        self.assertEqual(self.reader.reads, 0)

    def test_label_sheets_require_uploaded_templates(self):
        document_id = str(uuid.uuid4())
        with connect(self.path) as db:
            row = dict(db.execute('SELECT * FROM assignment_versions WHERE id=?', (self.version_id,)).fetchone())
            row.update(id=document_id, label='Labels', page_count=1, compiler_json=json.dumps({'generator': 'faculty-stamps-only'}))
            db.execute('INSERT INTO assignment_versions VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        self.ready_batch(document_id)
        self.upload(assignment_version_id=self.version_id)
        self.upload(assignment_version_id=document_id)
        slot = next(s for s in self.frq.assignment_templates(self.plan['id'])['slots'] if s['document_id'] == document_id)
        self.assertFalse(slot['can_import'])
        with self.assertRaisesRegex(ValueError, 'uploaded'):
            self.frq.prepare_assignment_template(self.plan['id'], {'document_id': document_id})
        with self.assertRaisesRegex(ValueError, 'Label sheets'):
            self.frq.templates.import_version(document_id)


class ManagedAssignmentTemplateWorkflow(AssignmentTemplateWorkflow):
    managed = True
