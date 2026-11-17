"""The Gmail API as Faculty uses it: search threads, read one, and send one message, as the connected account. Every call goes to
https://gmail.googleapis.com only, follows no redirect and carries the access token of Faculty's one Google connection
(response_cards/gmail.py). The only changes ever made to the mailbox are the instructor's explicit ones: moving a thread to Trash and back,
and archiving it (taking off its Inbox label) and back. Never a permanent delete, no other label and no read marks; nothing here logs
addresses, subjects or bodies.

`GmailMailClient.http` is replaced by tests (tests/test_gmail_mail.py); nothing else reaches Google.
"""
import base64
import functools
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor

from google_calendar.client import http_request
from response_cards.gmail import DeliveryError, _describe, _reason, never_sent

API = "https://gmail.googleapis.com/gmail/v1/users/me"
UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me"
HOST = "gmail.googleapis.com"
# A message up to this size goes as `raw` in a JSON body (about 4.7 MB once encoded; such a request takes at most 5 MB). A larger one,
# which only attachments make, goes to the upload URI as message/rfc822 (35 MB there; attachments.MAX_TOTAL, 25 MB of files, is about 34 MB once encoded).
JSON_RAW_MAX = 3_500_000
# What a read of Gmail may take in: its largest message (25 MB) as base64 inside JSON, as an attachment arrives. Calendar and every
# other Google read keep google_calendar.client.MAX_BODY.
READ_LIMIT = 48 * 1024 * 1024
PAGE = 20
WORKERS = 6          # threads.get costs 10 quota units and Gmail allows about 250 a second per user: never a whole page at once
                     # (2026-09-29, live mailbox: a cold Students scan of 60 reads took 5.1 s at 2, 2.3 s at 6, never rate-limited)
BACKOFF = (1.0, 2.0, 4.0, 8.0)  # a rate-limited read waits this long before each further try
CACHE_MAX = 2000
RATE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RESOURCE_EXHAUSTED")
SUMMARY_HEADERS = ("From", "To", "Cc", "Subject", "Date")
THREAD_ID = "0123456789abcdef"
PART = re.compile(r"[0-9]{1,4}(\.[0-9]{1,4}){0,6}")
ATTACHMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,2000}")


class MailError(Exception):
    """A call Google did not carry out. `uncertain` means a send may have been done (never assumed either way)."""

    def __init__(self, message, status=0, uncertain=False):
        super().__init__(message)
        self.status, self.uncertain = status, uncertain


def valid_id(value):
    """Gmail's thread and message IDs are lowercase hex; anything else never reaches a URL."""
    return isinstance(value, str) and 1 <= len(value) <= 32 and all(char in THREAD_ID for char in value)


def _timed_out(error):
    return isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout))


class GmailMailClient:
    def __init__(self, connection):
        self.connection = connection  # response_cards.gmail.GmailConnection: the one Google connection
        self.http = functools.partial(http_request, limit=READ_LIMIT)
        # Summaries of threads already read, by (thread ID, history ID): a thread that has not changed is not fetched again, which
        # keeps a refresh to one cheap listing. Process memory only; never written anywhere.
        self._summaries, self._lock = {}, threading.Lock()

    # -- The call ------------------------------------------------------------------------------------------------------
    def _token(self, force=False):
        try:
            return self.connection.access_token(force=force)
        except DeliveryError as error:
            if "expired or was revoked" in str(error) or "not connected" in str(error):
                raise MailError("Faculty's Google connection has expired or was revoked. Connect Gmail again from Mail.") from None
            raise MailError("Google could not be reached; try again in a moment.") from None

    def call(self, method, path, params=None, body=None, timeout=30, upload=""):
        """(status, payload) of a call Google answered with 2xx; anything else raises MailError. `upload` is the Content-Type of a
        bytes body sent to the upload URI."""
        url = (UPLOAD if upload else API) + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        if urllib.parse.urlsplit(url).hostname != HOST:
            raise MailError("Refusing to send a mail request anywhere but Google.")
        token = self._token()
        refreshed, waits = False, list(BACKOFF)
        while True:
            try:
                status, payload = self.http(method, url, body, {"Authorization": f"Bearer {token}", **({"Content-Type": upload} if upload else {})}, timeout)
            except (urllib.error.URLError, OSError) as error:
                # A POST that may have reached Google (a timeout, or a connection lost while it answered) is uncertain.
                if _timed_out(error):
                    said = "Google did not answer in time."
                elif never_sent(error):
                    said = "Google could not be reached; try again in a moment."
                else:
                    said = "The connection to Google closed before it answered."
                raise MailError(said, uncertain=method == "POST" and not never_sent(error)) from None
            if status == 401 and not refreshed:
                token, refreshed = self._token(force=True), True
                continue
            # A read Gmail rate-limited was not done: wait a moment and ask again (a send is never repeated this way).
            if method == "GET" and waits and (status == 429 or (status == 403 and _reason(payload) in RATE_REASONS)):
                time.sleep(waits.pop(0))
                continue
            break
        if 200 <= status < 300:
            return status, payload
        raise self.failure(status, payload, method)

    def failure(self, status, payload, method="GET"):
        reason, said = _reason(payload), _describe(payload)
        project = self.connection.project
        if status == 401 or reason == "authError":
            return MailError("Faculty's Google connection has expired or was revoked. Connect Gmail again from Mail.", status)
        if reason in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used in project" in said or "is disabled" in said:
            where = f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={project}" if project else \
                "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
            return MailError(f"The Gmail API is not turned on for Faculty's Google Cloud project. Turn it on at {where}.", status)
        if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT") or (status == 403 and "scope" in said.lower()):
            return MailError("Faculty's Google connection lacks a permission this needs. Connect Gmail again from Mail.", status)
        if status == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded", "RESOURCE_EXHAUSTED"):
            return MailError("Gmail asked Faculty to slow down; try again in a few minutes.", status)
        if status == 403:
            return MailError(f"Gmail refused the request ({reason or 'forbidden'}). Check the Google Workspace policy or connect Gmail again.", status)
        if status == 404:
            return MailError("Gmail has no such thread.", status)
        if status >= 500:
            # A send answered 5xx may have been made: never assumed either way.
            return MailError("Gmail is not answering right now." if method == "GET" else "Delivery could not be confirmed.", status, uncertain=method == "POST")
        return MailError(f"Gmail refused this request ({said or status}).", status)

    # -- Reading --------------------------------------------------------------------------------------------------------
    def threads(self, query="", page_token="", limit=PAGE):
        """One page of threads matching a Gmail search: ([thread with metadata], next page token or '')."""
        params = {"maxResults": limit, **({"q": query} if query else {}), **({"pageToken": page_token} if page_token else {})}
        _, listing = self.call("GET", "/threads", params)
        ids = [item["id"] for item in listing.get("threads") or [] if isinstance(item, dict) and valid_id(item.get("id"))]

        versions = {item["id"]: str(item.get("historyId") or "") for item in listing.get("threads") or [] if isinstance(item, dict) and valid_id(item.get("id"))}

        def one(thread_id):
            key = (thread_id, versions.get(thread_id, ""))
            with self._lock:
                if key[1] and key in self._summaries:
                    return self._summaries[key]
            _, payload = self.call("GET", f"/threads/{thread_id}", {"format": "metadata", "metadataHeaders": list(SUMMARY_HEADERS)})
            if key[1]:
                with self._lock:
                    if len(self._summaries) >= CACHE_MAX:
                        self._summaries.clear()
                    self._summaries[key] = payload
            return payload

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            found = list(pool.map(one, ids))
        return found, str(listing.get("nextPageToken") or "")

    def thread(self, thread_id):
        if not valid_id(thread_id):
            raise ValueError("That is not a Gmail thread.")
        _, payload = self.call("GET", f"/threads/{thread_id}", {"format": "full"})
        return payload

    # -- Trash and archive ---------------------------------------------------------------------------------------------
    def trash(self, thread_id, undo=False):
        """Moves a whole thread to Trash (or back). Gmail keeps trashed mail for 30 days; nothing here deletes permanently."""
        if not valid_id(thread_id):
            raise ValueError("That is not a Gmail thread.")
        self.call("POST", f"/threads/{thread_id}/{'untrash' if undo else 'trash'}", None, {}, timeout=30)
        self._forget(thread_id)

    def archive(self, thread_id, undo=False):
        """Archives a whole thread, as Gmail's Archive does: every message loses the Inbox label and stays in All mail. Undo puts
        the label back. No other label is ever added or removed."""
        if not valid_id(thread_id):
            raise ValueError("That is not a Gmail thread.")
        self.call("POST", f"/threads/{thread_id}/modify", None, {"addLabelIds" if undo else "removeLabelIds": ["INBOX"]}, timeout=30)
        self._forget(thread_id)

    def attachment(self, message_id, part_id):
        """(file name, media type, bytes) of one attached file of a message, by the part's ID (what `read_thread` lists). Fetched
        when the instructor opens it; nothing is kept."""
        if not valid_id(message_id) or not PART.fullmatch(str(part_id or "")):
            raise ValueError("That is not an attachment of a Gmail message.")
        _, message = self.call("GET", f"/messages/{message_id}", {"format": "full"})
        found = None

        def walk(node):
            nonlocal found
            if isinstance(node, dict) and found is None:
                if str(node.get("partId") or "") == part_id and node.get("filename"):
                    found = node
                for child in node.get("parts") or []:
                    walk(child)
        walk(message.get("payload"))
        if found is None:
            raise ValueError("That message has no such attachment.")
        body = found.get("body") or {}
        data = body.get("data")
        if not data and body.get("attachmentId"):
            if not ATTACHMENT_ID.fullmatch(str(body["attachmentId"])):
                raise MailError("Gmail named the attachment oddly; open it in Gmail.")
            _, payload = self.call("GET", f"/messages/{message_id}/attachments/{body['attachmentId']}", timeout=60)
            data = payload.get("data")
        if not isinstance(data, str):
            raise MailError("Gmail did not send the attachment; open it in Gmail.")
        content = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        return str(found.get("filename")), str(found.get("mimeType") or "application/octet-stream"), content

    def _forget(self, thread_id):
        with self._lock:
            for key in [k for k in self._summaries if k[0] == thread_id]:
                del self._summaries[key]

    # -- Sending -------------------------------------------------------------------------------------------------------
    def send(self, raw, thread_id=""):
        """Send one raw RFC 822 message. Returns Google's answer ({id, threadId}). A timeout or a 5xx is `uncertain`. A message larger
        than JSON_RAW_MAX (files attached) is a multipart upload: the thread as JSON metadata, then the message itself."""
        if len(raw) <= JSON_RAW_MAX:
            body = {"raw": base64.urlsafe_b64encode(raw).decode(), **({"threadId": thread_id} if thread_id else {})}
            _, payload = self.call("POST", "/messages/send", None, body, timeout=45)
        else:
            boundary = "faculty-" + uuid.uuid4().hex
            if boundary.encode() in raw:
                raise MailError("The message could not be packed for Gmail; nothing was sent.")
            meta = json.dumps({"threadId": thread_id} if thread_id else {})
            body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                    f"--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n").encode() + raw + f"\r\n--{boundary}--\r\n".encode()
            _, payload = self.call("POST", "/messages/send", {"uploadType": "multipart"}, body, timeout=180,
                                   upload=f"multipart/related; boundary={boundary}")
        if not valid_id(payload.get("id")):
            raise MailError("Delivery could not be confirmed.", uncertain=True)
        return payload
