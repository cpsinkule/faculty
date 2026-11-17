"""One private PDF via the existing signed print relay or pinned Gmail account."""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit, urlunsplit

from document_identity.email_exports import MAX_PDF_BYTES
from .mail import DeliveryError, NoRedirect, RelayRateLimit, rate_limit_wait, valid_address


class PdfMailer:
    def __init__(self, mailer):
        self.mailer = mailer

    def deliver(self, job, content):
        mailer = self.mailer
        if (not isinstance(content, bytes) or not content.startswith(b'%PDF-') or len(content) > MAX_PDF_BYTES
                or hashlib.sha256(content).hexdigest() != job['output_sha256']):
            raise ValueError('The student chart PDF failed verification.')
        if not valid_address(job['recipient']) or str(uuid.UUID(job['id'])) != job['id']:
            raise ValueError('The student chart email envelope is invalid.')
        if job['provider'] == 'gmail' and not valid_address(job['gmail_sender']):
            raise ValueError('The student chart email needs its selected Gmail sender.')
        mailer._check_delivery_provider(job['provider'], job['gmail_sender'])
        message = {'recipient': job['recipient'], 'subject': job['subject'],
                   'text': job['text_body'], 'html': job['html_body']}
        if job['provider'] == 'gmail':
            receipt = mailer.gmail.send({**message, 'mail_id': job['id'], 'reply_to': mailer.config.reply_to,
                                         'pdf_attachment': content}, expected_email=job['gmail_sender'])
            if (not isinstance(receipt, dict) or receipt.get('mail_id') != job['id'] or receipt.get('status') != 'accepted'
                    or not isinstance(receipt.get('message_id'), str) or not 1 <= len(receipt['message_id']) <= 1000):
                raise DeliveryError('Delivery could not be confirmed.', uncertain=True)
            return {**receipt, 'attachment_sha256': job['output_sha256']}
        payload = {**message, 'export_id': job['id'], 'filename': f"print-{job['id']}.pdf",
                   'attachment_sha256': job['output_sha256'], 'pdf_base64': base64.b64encode(content).decode('ascii')}
        parts = urlsplit(mailer.config.url)
        url = urlunsplit((parts.scheme, parts.netloc, '/send', '', ''))
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(mailer.config.secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json', 'User-Agent': 'Faculty-Student-Charts/1.0',
            'X-Faculty-Timestamp': stamp, 'X-Faculty-Signature': signature}, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
                raw = response.read(16385)
                if response.status != 202 or len(raw) > 16384:
                    raise ValueError('Invalid delivery response')
                receipt = json.loads(raw)
                if (receipt.get('export_id') != job['id'] or receipt.get('attachment_sha256') != job['output_sha256']
                        or receipt.get('status') != 'accepted' or not isinstance(receipt.get('message_id'), str)
                        or not 1 <= len(receipt['message_id']) <= 1000):
                    raise ValueError('Invalid delivery acknowledgement')
                return {key: receipt[key] for key in ('export_id', 'attachment_sha256', 'status', 'message_id')}
        except urllib.error.HTTPError as error:
            if error.code == 429:
                delay = rate_limit_wait(error)
                if delay:
                    raise RelayRateLimit(delay) from None
            definite = error.code in (400, 401, 403, 404, 413)
            raise DeliveryError('The email service rejected this chart. Check the address or sending configuration.' if definite
                                else 'Delivery could not be confirmed.', uncertain=not definite) from None
        except DeliveryError:
            raise
        except Exception:
            raise DeliveryError('Delivery could not be confirmed.', uncertain=True) from None
