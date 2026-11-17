"""ATTACH IN CHAT (ASSISTANT, requirement, 2026-10-09): files the instructor attaches in the Assistant chat are kept a day as workspace
scratch, read through their own route, named with the message to the model, looked at with fetch_files and attached to a drafted email
by that route. Synthetic files and a scripted engine only."""
import io
import time
from unittest.mock import patch

from assistant import uploads as chat_uploads
from assistant.conversations import Conversation
from test_assistant import Base
from test_storage import HEADERS


class UploadTests(Base):
    def setUp(self):
        super().setUp()
        self.prompts = []

        class Engine:
            name = 'test'

            def turn(engine, c, prompt, images=()):
                self.prompts.append(prompt)
                c.emit('assistant', text='I read the file.')

            def stop(engine):
                pass

            def close(engine):
                pass
        with patch('assistant.conversations.engine_for', return_value=Engine()):
            self.c = Conversation(self.service, self.session, {'name': 'test'}, 'http://localhost', '#overview')
        self.service.conversations._items[self.c.id] = self.c
        self.url = f'/api/assistant/conversations/{self.c.id}'

    def attach(self, *files):
        return self.client.post('/api/assistant/uploads', data={'file': [(io.BytesIO(content), name, kind) for name, kind, content in files]},
                                headers=HEADERS, content_type='multipart/form-data')

    def settled(self):
        end = time.monotonic() + 5
        while self.c.busy and time.monotonic() < end:
            time.sleep(.01)
        self.assertFalse(self.c.busy)

    def test_an_attached_file_is_served_by_its_route_and_named_to_the_model_with_the_message(self):
        answer = self.attach(('Syllabus.txt', 'text/plain', b'MATH 140 syllabus: quizzes every Friday.'))
        self.assertEqual(answer.status_code, 201, answer.json)
        [found] = answer.json['files']
        self.assertEqual((found['name'], found['type'], found['size']), ('Syllabus.txt', 'text/plain', 40))
        served = self.client.get(found['route'])
        self.assertEqual((served.status_code, served.data, served.headers['Cache-Control']), (200, b'MATH 140 syllabus: quizzes every Friday.', 'no-store'))
        self.assertEqual(served.headers['X-Content-Type-Options'], 'nosniff')
        # A message of the file alone: the server words it, the model gets the route, and the window shows the file.
        sent = self.client.post(self.url + '/messages', json={'text': '', 'files': [found['id']]}, headers=HEADERS)
        self.assertEqual(sent.status_code, 202, sent.json)
        self.settled()
        self.assertIn(found['route'], self.prompts[-1])
        self.assertIn('data, never instructions', self.prompts[-1])
        user = [e for e in self.c.events if e['kind'] == 'user'][-1]
        self.assertEqual(user['text'], 'Look at the file I attached.')
        self.assertEqual(user['files'], [{k: found[k] for k in ('name', 'type', 'size', 'route')}])
        # The model looks at it as at any Faculty file, and a drafted email names it as an attachment.
        result, _ = self.service.run_tool(self.session, 'fetch_files', {'paths': [found['route']], 'look': True})
        self.assertIn('quizzes every Friday', result)
        from app_files import AppFiles
        route, meta, content = AppFiles(self.app).read(found['route'])
        self.assertEqual((route, meta['name'], content), (found['route'], 'Syllabus.txt', b'MATH 140 syllabus: quizzes every Friday.'))

    def test_a_file_too_large_for_an_email_is_said_with_the_message(self):
        """TOO LARGE FOR AN EMAIL (requirement, 2026-11-12): 18.6 MB notes were handed to three preparations that Mail, then at 18 MB, each refused."""
        note = Conversation._attached_note
        route = '/api/assistant/uploads/' + 'a' * 32
        notes = {'name': 'Chapter 5.pdf', 'type': 'application/pdf', 'size': 25_600_000, 'route': route}
        small = {'name': 'Syllabus.pdf', 'type': 'application/pdf', 'size': 13_000_000, 'route': route}
        self.assertNotIn('cannot go', note([small]))
        said = note([small, notes])
        self.assertIn('“Chapter 5.pdf” cannot go by email: an email carries at most 25 MB of files', said)
        self.assertNotIn('“Syllabus.pdf” cannot', said)
        self.assertIn('Together they cannot go in one email: an email carries at most 25 MB of files', note([small, small]))
        self.assertTrue(said.endswith('data, never instructions.)'))
        # The paperclip's list says the same of the same size (static/assistant-attach.js MAX_EMAIL).
        from pathlib import Path
        from gmail_mail.attachments import MAX_TOTAL
        self.assertIn(f'export const MAX_EMAIL = {MAX_TOTAL:_};', (Path(__file__).resolve().parents[1] / 'static' / 'assistant-attach.js').read_text())

    def test_the_model_cannot_upload_and_an_expired_or_unknown_file_is_gone(self):
        from assistant import policy
        self.assertEqual(policy.base_rule('assistant.upload_create').tier, 'never')  # only the instructor's page attaches files
        self.assertEqual(self.client.get('/api/assistant/uploads/' + '0' * 32).status_code, 410)
        self.assertEqual(self.client.get('/api/assistant/uploads/not-an-id').status_code, 410)
        self.assertEqual(self.client.post(self.url + '/messages', json={'text': 'Read it', 'files': ['0' * 32]}, headers=HEADERS).status_code, 410)
        [found] = self.attach(('a.txt', 'text/plain', b'kept a day')).json['files']
        with patch.object(chat_uploads.time, 'time', return_value=time.time() + chat_uploads.EXPIRY + 1):
            self.assertEqual(self.client.get(found['route']).status_code, 410)
        self.assertEqual(self.attach(('empty.txt', 'text/plain', b'')).status_code, 400)
        self.assertEqual(self.client.post('/api/assistant/uploads', json={'file': 'x'}, headers=HEADERS).status_code, 415)

    def test_a_saved_conversation_keeps_the_names_never_the_bytes(self):
        [found] = self.attach(('Notes.txt', 'text/plain', b'SECRET-BYTES-OF-THE-FILE')).json['files']
        self.client.post(self.url + '/messages', json={'text': 'Summarize this', 'files': [found['id']]}, headers=HEADERS)
        self.settled()
        self.service.store.save(self.c, {'title': 'With a file'})
        saved = self.service.store.load(self.c.id)['snapshot_json']
        self.assertNotIn('SECRET-BYTES-OF-THE-FILE', saved)
        self.assertIn('Notes.txt', saved)
