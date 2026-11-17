"""Private student PDF envelopes, Gmail MIME, and signed relay acknowledgements.

Every Google and relay transport is replaced in-process. These tests send no mail.
"""
import base64
import email
import email.policy
import hashlib
import hmac
import io
import json
from email.message import Message
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error
import uuid

from document_identity.email_exports import MAX_PDF_BYTES
from response_cards.attachments import PdfMailer
from response_cards.gmail import GmailConnection, SEND_URL, TOKEN_URL, write_private
from response_cards.mail import CardMailer, DeliveryError, MailConfig, NoRedirect, RelayRateLimit


PDF = b'%PDF-1.4\n% One private student chart fixture\n%%EOF\n'


class PdfMailerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client_file = Path(self.directory.name) / 'client.json'
        self.token_file = Path(self.directory.name) / 'token.json'
        write_private(str(self.client_file), {'web': {'client_id': 'test-client', 'client_secret': 'test-client-secret'}})
        self.connect_gmail()
        self.gmail = GmailConnection(str(self.client_file), str(self.token_file), sender_name='Instructor')
        self.google = mock.Mock(side_effect=self.google_response)
        self.gmail.http = self.google
        self.config = MailConfig('https://relay.example.test/message', 'local-test-secret-' * 3,
                                 reply_to='instructor@example.test', provider='resend')
        self.mailer = CardMailer(SimpleNamespace(rosters=None, path=':memory:'), self.config, gmail=self.gmail)
        self.delivery = PdfMailer(self.mailer)
        self.job = {'id': str(uuid.uuid4()), 'recipient': 'student@example.test',
                    'subject': 'Your exam seat · MATH 140',
                    'text_body': 'Hello,\n\nYour private seating chart is attached.',
                    'html_body': '<p>Hello,</p><p>Your private seating chart is attached.</p>',
                    'provider': 'resend', 'gmail_sender': '',
                    'output_sha256': hashlib.sha256(PDF).hexdigest()}
        # Fail closed if a test forgets to replace a network call locally.
        self.opener = self.enterContext(mock.patch('response_cards.attachments.urllib.request.build_opener'))
        self.opener.return_value.open.side_effect = AssertionError('Unexpected network attempt')

    def connect_gmail(self, email_address='instructor@example.test'):
        write_private(str(self.token_file), {'refresh_token': 'test-refresh-token', 'email': email_address})

    @staticmethod
    def google_response(url, data=None, headers=None, timeout=30):
        if url == TOKEN_URL:
            return 200, {'access_token': 'local-test-access', 'expires_in': 3600}
        if url == SEND_URL:
            return 200, {'id': 'gmail-test-receipt'}
        raise AssertionError(f'Unexpected Google route {url}')

    def gmail_job(self, **changes):
        return {**self.job, 'provider': 'gmail', 'gmail_sender': 'instructor@example.test', **changes}

    def accepted(self, **changes):
        return {'export_id': self.job['id'], 'attachment_sha256': self.job['output_sha256'],
                'status': 'accepted', 'message_id': 'relay-test-receipt', **changes}

    def response(self, payload, status=202):
        self.opener.return_value.open.side_effect = None
        response = self.opener.return_value.open.return_value.__enter__.return_value
        response.status = status
        response.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return response

    def deliver_error(self, error):
        self.opener.return_value.open.reset_mock()
        self.opener.return_value.open.side_effect = error
        with self.assertRaises(DeliveryError) as raised:
            self.delivery.deliver(self.job, PDF)
        self.assertEqual(self.opener.return_value.open.call_count, 1)
        self.google.assert_not_called()
        return raised.exception

    @staticmethod
    def http_error(body, code=429, retry_after='60'):
        headers = Message()
        if retry_after is not None:
            for value in retry_after if isinstance(retry_after, list) else [retry_after]:
                headers.add_header('Retry-After', value)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return urllib.error.HTTPError('https://relay.example.test/send', code, 'Rejected', headers, io.BytesIO(raw))

    def test_gmail_builds_exactly_one_pdf_for_one_recipient_with_pinned_sender(self):
        job = self.gmail_job(cc='unrelated@example.test', bcc='another@example.test',
                             recipients=['extra@example.test'], reply_to='ignored@example.test')
        receipt = self.delivery.deliver(job, PDF)
        self.assertEqual(receipt, {'mail_id': job['id'], 'status': 'accepted',
                                  'message_id': 'gmail-test-receipt', 'attachment_sha256': job['output_sha256']})
        sent = [call for call in self.google.call_args_list if call.args[0] == SEND_URL]
        self.assertEqual(len(sent), 1)
        self.assertEqual(set(sent[0].args[1]), {'raw'})
        self.assertEqual(sent[0].args[2]['Authorization'], 'Bearer local-test-access')
        message = email.message_from_bytes(base64.urlsafe_b64decode(sent[0].args[1]['raw']), policy=email.policy.default)
        self.assertEqual(message.get_content_type(), 'multipart/mixed')
        self.assertEqual(email.utils.getaddresses(message.get_all('To')), [('', job['recipient'])])
        self.assertEqual(email.utils.getaddresses(message.get_all('From')), [('Instructor', job['gmail_sender'])])
        self.assertIsNone(message.get('Cc'))
        self.assertIsNone(message.get('Bcc'))
        self.assertIsNone(message.get('Reply-To'))  # Connected sender already is the configured reply-to.
        self.assertEqual(message['Subject'], job['subject'])
        self.assertEqual(message.get_body(preferencelist=('plain',)).get_content().strip(), job['text_body'])
        self.assertEqual(message.get_body(preferencelist=('html',)).get_content().strip(), job['html_body'])
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_content_type(), 'application/pdf')
        self.assertEqual(attachments[0].get_filename(), 'seating-chart.pdf')
        self.assertEqual(attachments[0]['Content-Disposition'].content_disposition, 'attachment')
        self.assertEqual(attachments[0].get_payload(decode=True), PDF)
        self.opener.assert_not_called()

    def test_gmail_attachment_retains_configured_reply_to(self):
        self.config.reply_to = 'course-inbox@example.test'
        self.delivery.deliver(self.gmail_job(), PDF)
        raw = next(call.args[1]['raw'] for call in self.google.call_args_list if call.args[0] == SEND_URL)
        message = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)
        self.assertEqual(message['Reply-To'], self.config.reply_to)
        self.assertEqual(len(list(message.iter_attachments())), 1)

    def test_pinned_gmail_account_must_still_be_connected_and_never_falls_back(self):
        for other in ('other@example.test', None):
            with self.subTest(connection=other):
                if other is None:
                    self.token_file.unlink()
                else:
                    self.connect_gmail(other)
                with self.assertRaises(DeliveryError) as raised:
                    self.delivery.deliver(self.gmail_job(), PDF)
                self.assertTrue(raised.exception.unavailable)
                self.assertFalse(raised.exception.uncertain)
                self.google.assert_not_called()
                self.opener.assert_not_called()

    def test_missing_or_invalid_pinned_sender_is_rejected_before_gmail(self):
        for sender in ('', 'one@example.test,two@example.test', 'teacher@example.test\nBcc:other@example.test'):
            with self.subTest(sender=sender), self.assertRaises(ValueError):
                self.delivery.deliver(self.gmail_job(gmail_sender=sender), PDF)
        self.google.assert_not_called()
        self.opener.assert_not_called()

    def test_pdf_hash_signature_size_and_single_recipient_are_checked_before_transport(self):
        invalid = [({'output_sha256': '0' * 64}, PDF), ({}, b'not a PDF'), ({}, bytearray(PDF)),
                   ({}, '%PDF-not-bytes'), ({}, b'%PDF-' + b'x' * MAX_PDF_BYTES),
                   ({'recipient': 'one@example.test,two@example.test'}, PDF),
                   ({'recipient': 'student@example.test\r\nBcc: other@example.test'}, PDF),
                   ({'recipient': 'Student <student@example.test>'}, PDF),
                   ({'id': 'not-an-export-id'}, PDF), ({'id': 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'}, PDF)]
        for provider in ('resend', 'gmail'):
            for overrides, content in invalid:
                with self.subTest(provider=provider, overrides=overrides, content_type=type(content).__name__):
                    job = self.gmail_job() if provider == 'gmail' else self.job
                    with self.assertRaises(ValueError):
                        self.delivery.deliver({**job, **overrides}, content)
        self.google.assert_not_called()
        self.opener.assert_not_called()

    def test_direct_gmail_pdf_validation_precedes_any_token_request(self):
        payload = {'mail_id': self.job['id'], 'recipient': self.job['recipient'], 'subject': self.job['subject'],
                   'text': self.job['text_body'], 'html': self.job['html_body']}
        for attachment in (None, b'', 'not bytes', b'not a PDF', b'%PDF-' + b'x' * MAX_PDF_BYTES):
            with self.subTest(kind=type(attachment).__name__), self.assertRaises(ValueError):
                self.gmail.send({**payload, 'pdf_attachment': attachment}, expected_email='instructor@example.test')
        self.google.assert_not_called()

    def test_relay_uses_exact_signed_send_body_and_checks_matching_receipt(self):
        self.config.provider = 'gmail'  # The queued selection survives a new workspace default.
        response = self.response({**self.accepted(), 'provider_detail': 'must not be retained'})
        with mock.patch('response_cards.attachments.time.time', return_value=1750000000.8):
            receipt = self.delivery.deliver({**self.job, 'cc': 'ignored@example.test'}, PDF)
        self.assertEqual(receipt, self.accepted())
        self.opener.assert_called_once()
        self.assertIsInstance(self.opener.call_args.args[0], NoRedirect)
        self.opener.return_value.open.assert_called_once()
        request = self.opener.return_value.open.call_args.args[0]
        self.assertEqual(self.opener.return_value.open.call_args.kwargs, {'timeout': 45})
        self.assertEqual(request.full_url, 'https://relay.example.test/send')
        self.assertEqual(request.method, 'POST')
        payload = json.loads(request.data)
        self.assertEqual(set(payload), {'recipient', 'subject', 'text', 'html', 'export_id', 'filename', 'attachment_sha256', 'pdf_base64'})
        self.assertEqual(payload['recipient'], self.job['recipient'])
        self.assertEqual(payload['text'], self.job['text_body'])
        self.assertEqual(payload['html'], self.job['html_body'])
        self.assertEqual(payload['subject'], self.job['subject'])
        self.assertEqual(payload['export_id'], self.job['id'])
        self.assertEqual(payload['filename'], f"print-{self.job['id']}.pdf")
        self.assertEqual(payload['attachment_sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(base64.b64decode(payload['pdf_base64'], validate=True), PDF)
        self.assertEqual(request.get_header('X-faculty-timestamp'), '1750000000')
        expected_signature = hmac.new(self.config.secret.encode(), b'1750000000.' + request.data, hashlib.sha256).hexdigest()
        self.assertEqual(request.get_header('X-faculty-signature'), expected_signature)
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        response.read.assert_called_once_with(16385)
        self.google.assert_not_called()

    def test_malformed_or_mismatched_relay_receipts_are_uncertain_without_resend(self):
        bad = [self.accepted(attachment_sha256='0' * 64), self.accepted(export_id=str(uuid.uuid4())),
               self.accepted(status='queued'), self.accepted(message_id=''), self.accepted(message_id=12),
               self.accepted(message_id='x' * 1001), [], None, b'not JSON', b'x' * 16385]
        for payload in bad:
            with self.subTest(receipt=repr(payload)[:100]):
                self.opener.return_value.open.reset_mock()
                self.response(payload)
                with self.assertRaises(DeliveryError) as raised:
                    self.delivery.deliver(self.job, PDF)
                self.assertTrue(raised.exception.uncertain)
                self.assertFalse(raised.exception.unavailable)
                self.assertEqual(self.opener.return_value.open.call_count, 1)
        self.google.assert_not_called()

    def test_malformed_gmail_receipts_are_uncertain(self):
        good = {'mail_id': self.job['id'], 'status': 'accepted', 'message_id': 'gmail-receipt'}
        for receipt in (None, [], {}, {**good, 'mail_id': str(uuid.uuid4())}, {**good, 'status': 'queued'},
                        {**good, 'message_id': ''}, {**good, 'message_id': 'x' * 1001}):
            with self.subTest(receipt=repr(receipt)[:80]), mock.patch.object(self.gmail, 'send', return_value=receipt) as send:
                with self.assertRaises(DeliveryError) as raised:
                    self.delivery.deliver(self.gmail_job(), PDF)
                self.assertTrue(raised.exception.uncertain)
                self.assertFalse(raised.exception.unavailable)
                send.assert_called_once()
        self.google.assert_not_called()
        self.opener.assert_not_called()

    def test_only_known_relay_429_contract_authorizes_a_bounded_pause(self):
        for delay in (1, 60, 86400):
            with self.subTest(delay=delay):
                error = self.deliver_error(self.http_error({'error': 'rate_limited', 'retry_after_seconds': delay}, retry_after=str(delay)))
                self.assertIsInstance(error, RelayRateLimit)
                self.assertTrue(error.unavailable)
                self.assertFalse(error.uncertain)
                self.assertEqual(error.retry_after, delay)

    def test_unknown_429_is_uncertain_and_must_never_authorize_retry(self):
        exact = {'error': 'rate_limited', 'retry_after_seconds': 60}
        cases = [(b'not JSON', '60'), ([], '60'), ({'error': 'unknown'}, '60'),
                 ({**exact, 'accepted': True}, '60'), (exact, None), (exact, '61'), (exact, ['60', '60']),
                 (b'{"error":"unknown","error":"rate_limited","retry_after_seconds":60}', '60')]
        cases.extend(({**exact, 'retry_after_seconds': value}, str(value)) for value in (0, -1, 86401, True, 60.0, '60'))
        for body, header in cases:
            with self.subTest(body=repr(body), header=header):
                error = self.deliver_error(self.http_error(body, retry_after=header))
                self.assertNotIsInstance(error, RelayRateLimit)
                self.assertTrue(error.uncertain)
                self.assertFalse(error.unavailable)
                self.assertIsNone(error.retry_after)

    def test_definite_relay_rejections_and_uncertain_network_failures_never_retry(self):
        for code in (400, 401, 403, 404, 413):
            with self.subTest(code=code):
                error = self.deliver_error(self.http_error({'error': 'rejected'}, code=code))
                self.assertFalse(error.uncertain)
                self.assertFalse(error.unavailable)
        for failure in (self.http_error({}, code=500), self.http_error({}, code=503), self.http_error({}, code=302),
                        urllib.error.URLError('connection interrupted'), TimeoutError('timed out')):
            with self.subTest(failure=repr(failure)):
                error = self.deliver_error(failure)
                self.assertTrue(error.uncertain)
                self.assertFalse(error.unavailable)


if __name__ == '__main__':
    unittest.main()
