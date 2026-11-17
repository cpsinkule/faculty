"""Gmail delivery for Response Cards: send as the instructor's own Google Workspace account.

The instructor connects once through OAuth 2.0 (authorization code with PKCE). Faculty keeps
the refresh token in a private file and sends each queued message through the Gmail API, so
students receive mail from the instructor's own address inside their Workspace, and neither
the Cloudflare relay nor its daily limit is involved. Response Cards asks only for gmail.send and
userinfo.email; the Mail page (gmail_mail/, requirement, 2026-09-28) adds gmail.readonly on top of them,
for the account already connected. Nothing here logs addresses or bodies.

The same connection carries Google Calendar sync (google_calendar/, requirement, 2026-09-28): the
Calendar page's Connect asks for CALENDAR_SCOPES on top of SCOPES, for the account already
connected, with `include_granted_scopes`, so either sign-in keeps what the other granted. One
connection means one refresh token: disconnecting here disconnects the calendar too.

Configuration, needed by both the web app and the Inbox worker:
  FACULTY_GMAIL_CLIENT_FILE  the OAuth client JSON downloaded from Google Cloud Console
  FACULTY_GMAIL_TOKEN_FILE   where Faculty keeps the connection (created with mode 0600)
  FACULTY_GMAIL_REDIRECT     the redirect URI registered with the client (default below)
Without both files configured, Gmail is inert and the relay is used.
"""
import base64
import hashlib
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from course_storage import timestamp

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = ("https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/userinfo.email")
# Calendar sync: write Faculty's events into the instructor's own calendar, and read their calendars to show on Faculty's.
CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.readonly")
# Mail page: read the mailbox (search, threads, a student's mail). Sending needs nothing more than SCOPES; nothing is modified.
MAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
# Mail page, Move to Trash (requirement, 2026-09-28): a second, wider grant asked for only when the instructor first uses it. Faculty
# uses it for nothing but moving a thread to Trash and back; it never deletes permanently.
MAIL_MANAGE_SCOPES = MAIL_SCOPES + ("https://www.googleapis.com/auth/gmail.modify",)
# Communications record, a file a student linked from Google Drive (requirement, 2026-10-11): read that one file.
# Google has no narrower permission that reads a file someone else shared; Faculty reads only files a student's message links.
DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)
# Where each sign-in returns to once Google sends the instructor back; the pending sign-in names one of these.
RETURNS = {"response-cards": "/#response-cards", "calendar": "/#calendar?google=1", "mail": "/#mail", "mail-manage": "/#mail",
           "drive": "/#follow-ups"}
EXTRA_SCOPES = {"calendar": CALENDAR_SCOPES, "mail": MAIL_SCOPES, "mail-manage": MAIL_MANAGE_SCOPES, "drive": DRIVE_SCOPES}
DEFAULT_REDIRECT = "http://127.0.0.1:3000/api/response-cards/gmail/callback"
PENDING_TTL = 900
LIMIT_PAUSE, RECONNECT_PAUSE = 300, 900
USER_AGENT = "Faculty-Response-Cards/1.0"


class DeliveryError(Exception):
    def __init__(self, message, uncertain=False, unavailable=False, retry_after=None):
        super().__init__(message)
        self.uncertain = uncertain      # The message may have gone out; it is never resent.
        self.unavailable = unavailable  # Nothing was sent: keep the message queued and pause.
        self.retry_after = retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_json(request, timeout, limit=65536):
    """Open a prepared request without following a redirect and return (status, parsed JSON object or {}). A body is read up to
    `limit` bytes, an HTTP error's up to 64 KiB; HTTP error statuses are returned, not raised."""
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw, status = response.read(limit), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(65536), error.code
    try:
        parsed = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def http_json(url, data=None, headers=None, timeout=30):
    """One HTTPS call. Returns (status, parsed JSON object or {}); HTTP error statuses are returned, not raised.

    Connection failures propagate as urllib or socket errors so callers can tell "never sent" apart.
    """
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    body = None
    if data is not None:
        if headers.get("Content-Type") == "application/json":
            body = json.dumps(data).encode()
        else:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    return open_json(request, timeout)


def write_private(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    temp = f"{path}.tmp"
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _reason(payload):
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        for item in error.get("errors") or []:
            if isinstance(item, dict) and isinstance(item.get("reason"), str):
                return item["reason"]
        if isinstance(error.get("status"), str):
            return error["status"]
    return ""


def _describe(payload):
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("status") or "")[:200]
    if isinstance(error, str):
        return f"{error}: {payload.get('error_description', '')}".strip(": ")[:200]
    return ""


def _timed_out(error):
    return isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout))


def never_sent(error):
    """Whether a failed request certainly never reached Google whole. urllib wraps in URLError only what fails while connecting
    or writing the request; a failure reading the answer (a reset, a closed connection) comes through bare, after Google may
    already have acted on it. A timeout is never certain either way."""
    return isinstance(error, urllib.error.URLError) and not _timed_out(error)


class GmailConnection:
    """The instructor's Gmail connection: the sign-in flow, token upkeep, and sending."""

    def __init__(self, client_file="", token_file="", redirect_uri=DEFAULT_REDIRECT, sender_name="Instructor"):
        self.client_file, self.token_file = client_file, token_file
        self.redirect_uri, self.sender_name = redirect_uri, sender_name
        self.http = http_json  # replaced by tests; never touches Google otherwise
        self._access = ("", 0.0)
        self._access_identity = None

    @classmethod
    def environment(cls, sender_name="Instructor"):
        return cls(os.environ.get("FACULTY_GMAIL_CLIENT_FILE", "").strip(), os.environ.get("FACULTY_GMAIL_TOKEN_FILE", "").strip(),
                   os.environ.get("FACULTY_GMAIL_REDIRECT", "").strip() or DEFAULT_REDIRECT, sender_name)

    # -- State ---------------------------------------------------------------
    def client(self):
        """(client_id, client_secret) from Google's downloaded client JSON, or None when it is not installed."""
        if not self.client_file:
            return None
        try:
            with open(self.client_file, encoding="utf-8") as handle:
                blob = json.load(handle)
        except (OSError, ValueError):
            return None
        entry = (blob.get("web") or blob.get("installed") or {}) if isinstance(blob, dict) else {}
        client_id, secret = entry.get("client_id"), entry.get("client_secret")
        if not isinstance(client_id, str) or not isinstance(secret, str) or not client_id or not secret:
            return None
        return client_id, secret

    def _state(self):
        if not self.token_file:
            return {}
        try:
            with open(self.token_file, encoding="utf-8") as handle:
                state = json.load(handle)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def configured(self):
        return bool(self.token_file) and self.client() is not None

    @property
    def connected(self):
        state = self._state()
        return self.configured and isinstance(state.get("refresh_token"), str) and bool(state.get("refresh_token")) and bool(state.get("email"))

    @property
    def email(self):
        return str(self._state().get("email") or "")

    def status(self):
        state = self._state()
        return {"configured": self.configured, "connected": self.connected, "email": state.get("email") or None,
                "connected_at": state.get("connected_at") or None}

    def granted(self, scopes):
        """True when the connection's recorded grant includes every one of `scopes` (as Google reported it at sign-in)."""
        recorded = self._state().get("scopes")
        return self.connected and isinstance(recorded, list) and set(scopes) <= set(recorded)

    @property
    def project(self):
        """The Google Cloud project named in the client file (for pointing the instructor at its console), or ''."""
        try:
            with open(self.client_file, encoding="utf-8") as handle:
                blob = json.load(handle)
        except (OSError, ValueError, TypeError):
            return ""
        entry = (blob.get("web") or blob.get("installed") or {}) if isinstance(blob, dict) else {}
        value = entry.get("project_id") if isinstance(entry, dict) else ""
        return value if isinstance(value, str) and value.replace("-", "").isalnum() else ""

    def pending_purpose(self):
        """Which page a sign-in still pending was started from ("response-cards", "calendar", "mail" or "drive")."""
        purpose = (self._state().get("pending") or {}).get("purpose")
        return purpose if purpose in RETURNS else "response-cards"

    # -- Sign-in ---------------------------------------------------------------
    def start(self, login_hint="", purpose="response-cards"):
        """Begin a sign-in: remember the PKCE verifier and state, and return the Google URL to open.

        `purpose` "calendar" also asks for CALENDAR_SCOPES and "mail" for MAIL_SCOPES; both must end signed in as the account already connected, if any.
        Every sign-in sends `include_granted_scopes`, so reconnecting Gmail keeps the calendar permission and vice versa."""
        client = self.client()
        if not client or not self.token_file:
            raise ValueError("Gmail is not set up on this server: install the Google client file first.")
        if purpose not in RETURNS:
            raise ValueError("Unknown Google sign-in purpose.")
        scopes = sorted(set(SCOPES) | set(EXTRA_SCOPES.get(purpose, ())))
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        pending = {"state": secrets.token_urlsafe(24), "verifier": verifier, "created": time.time(), "purpose": purpose, "scopes": scopes}
        state = self._state()
        state["pending"] = pending
        write_private(self.token_file, state)
        params = {"client_id": client[0], "redirect_uri": self.redirect_uri, "response_type": "code", "scope": " ".join(scopes),
                  "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": pending["state"],
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        if login_hint:
            params["login_hint"] = login_hint
        return AUTH_URL + "?" + urllib.parse.urlencode(params)

    def finish(self, state_value, code):
        """Complete a sign-in from Google's redirect; keeps the refresh token and the signed-in address."""
        client = self.client()
        stored = self._state()
        pending = stored.pop("pending", None) or {}
        if pending:
            write_private(self.token_file, stored)  # An authorization code is single-use either way.
        if not client or not pending or time.time() - float(pending.get("created", 0)) > PENDING_TTL:
            raise ValueError("This sign-in is no longer pending. Start again from Response Cards.")
        if not isinstance(state_value, str) or not secrets.compare_digest(state_value, str(pending.get("state", ""))):
            raise ValueError("The sign-in response did not match the pending request. Start again from Response Cards.")
        if not isinstance(code, str) or not code:
            raise ValueError("Google returned no authorization code.")
        try:
            status, payload = self.http(TOKEN_URL, {"code": code, "client_id": client[0], "client_secret": client[1], "redirect_uri": self.redirect_uri,
                                                   "grant_type": "authorization_code", "code_verifier": pending["verifier"]})
        except (urllib.error.URLError, OSError):
            raise ValueError("Google could not be reached to complete the sign-in.") from None
        if status != 200 or not isinstance(payload.get("access_token"), str):
            raise ValueError(f"Google refused the sign-in ({_describe(payload) or status}).")
        if not isinstance(payload.get("refresh_token"), str) or not payload["refresh_token"]:
            raise ValueError("Google did not issue a refresh token; start the sign-in again.")
        granted = set(str(payload.get("scope", "")).split())
        if not granted.issuperset(SCOPES):
            raise ValueError("The sign-in did not grant permission to send mail; start again and allow both permissions.")
        purpose = pending.get("purpose") if pending.get("purpose") in RETURNS else "response-cards"
        if not granted.issuperset(pending.get("scopes") or SCOPES):
            raise ValueError("The sign-in did not grant every permission asked for; start again from the page that opened it and allow them all.")
        try:
            status, info = self.http(USERINFO_URL, headers={"Authorization": f"Bearer {payload['access_token']}"})
        except (urllib.error.URLError, OSError):
            status, info = 0, {}
        email = info.get("email") if status == 200 else None
        if not isinstance(email, str) or "@" not in email:
            raise ValueError("Google did not report the signed-in address; start the sign-in again.")
        current = str(stored.get("email") or "")
        if purpose in EXTRA_SCOPES and stored.get("refresh_token") and current and current.casefold() != email.casefold():
            # One connection sends Response Card mail, syncs the calendar and reads the mailbox: adding either never switches the account.
            raise ValueError(f"Google signed in as {email}, but Faculty's Google connection is {current}. Connect it again "
                             f"signed in as {current}, or change the account in Response Cards → Email settings first.")
        stored.update({"refresh_token": payload["refresh_token"], "email": email, "scopes": sorted(granted), "connected_at": timestamp()})
        write_private(self.token_file, stored)
        self._access = (payload["access_token"], time.time() + int(payload.get("expires_in", 3600)))
        self._access_identity = self._connection_identity(client, stored)
        return {**self.status(), "purpose": purpose}

    def disconnect(self):
        """Revoke the grant at Google when possible and forget the connection locally."""
        token = self._state().get("refresh_token")
        if isinstance(token, str) and token:
            try:
                self.http(REVOKE_URL, {"token": token})
            except (urllib.error.URLError, OSError):
                pass  # The grant stays listed in the Google account's settings; the local copy is gone regardless.
        if self.token_file and os.path.exists(self.token_file):
            os.remove(self.token_file)
        self._access = ("", 0.0)
        self._access_identity = None
        return self.status()

    # -- Sending -----------------------------------------------------------------
    @staticmethod
    def _connection_identity(client, stored):
        return (client, stored.get("refresh_token"), str(stored.get("email") or "").casefold())

    def access_token(self, force=False, expected_email=None):
        client, stored = self.client(), self._state()
        if not client or not stored.get("refresh_token"):
            raise DeliveryError("Gmail is not connected. Connect it in Response Cards.", unavailable=True, retry_after=RECONNECT_PAUSE)
        identity = self._connection_identity(client, stored)
        if expected_email and identity[2] != expected_email.casefold():
            raise DeliveryError("Reconnect the Gmail account chosen for this message before sending.", unavailable=True, retry_after=RECONNECT_PAUSE)
        token, expires = self._access
        if token and self._access_identity == identity and not force and time.time() < expires - 60:
            return token
        try:
            status, payload = self.http(TOKEN_URL, {"client_id": client[0], "client_secret": client[1],
                                                   "refresh_token": stored["refresh_token"], "grant_type": "refresh_token"})
        except (urllib.error.URLError, OSError):
            raise DeliveryError("Google could not be reached; sending will retry.", unavailable=True, retry_after=LIMIT_PAUSE) from None
        if status == 200 and isinstance(payload.get("access_token"), str) and payload["access_token"]:
            if self._connection_identity(self.client(), self._state()) != identity:
                raise DeliveryError("The Gmail connection changed; queued mail will wait before trying again.",
                                    unavailable=True, retry_after=RECONNECT_PAUSE)
            self._access = (payload["access_token"], time.time() + int(payload.get("expires_in", 3600)))
            self._access_identity = identity
            return payload["access_token"]
        if status in (400, 401):
            raise DeliveryError("The Gmail connection has expired or was revoked. Reconnect Gmail in Response Cards.",
                                unavailable=True, retry_after=RECONNECT_PAUSE)
        raise DeliveryError("Google did not renew the Gmail connection; sending will retry.", unavailable=True, retry_after=LIMIT_PAUSE)

    def send(self, payload, expected_email=None):
        """Send one message envelope as the connected account. Returns the provider receipt."""
        sender = expected_email or self.email
        message = EmailMessage()
        message["From"] = formataddr((self.sender_name, sender))
        message["To"] = payload["recipient"]
        message["Subject"] = payload["subject"]
        if payload.get("reply_to") and payload["reply_to"].lower() != sender.lower():
            message["Reply-To"] = payload["reply_to"]
        message.set_content(payload["text"])
        if payload.get("html"):
            message.add_alternative(payload["html"], subtype="html")
        if "pdf_attachment" in payload:
            from document_identity.email_exports import MAX_PDF_BYTES
            content = payload["pdf_attachment"]
            if not isinstance(content, bytes) or not content.startswith(b"%PDF-") or len(content) > MAX_PDF_BYTES:
                raise ValueError("Invalid PDF attachment.")
            message.add_attachment(content, maintype="application", subtype="pdf", filename=payload.get("pdf_filename") or "seating-chart.pdf")
        body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
        token = self.access_token(expected_email=sender)
        status, result = 0, {}
        for attempt in range(2):
            try:
                status, result = self.http(SEND_URL, body, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=45)
            except (urllib.error.URLError, OSError) as error:
                # Only a request that never reached Google may go again by itself (AGENTS.md "OUTSIDE SERVICES").
                if never_sent(error):
                    raise DeliveryError("Google could not be reached; sending will retry.", unavailable=True, retry_after=LIMIT_PAUSE) from None
                raise DeliveryError("Delivery could not be confirmed.", uncertain=True) from None
            if status == 401 and attempt == 0:
                token = self.access_token(force=True, expected_email=sender)
                continue
            break
        if status == 200 and isinstance(result.get("id"), str) and result["id"]:
            return {"mail_id": payload["mail_id"], "status": "accepted", "message_id": result["id"][:1000]}
        raise self._failure(status, result)

    @staticmethod
    def _failure(status, result):
        reason = _reason(result)
        if reason == "dailyLimitExceeded":
            return DeliveryError("Gmail's daily sending limit was reached; queued mail resumes when it resets.", unavailable=True, retry_after=RECONNECT_PAUSE)
        if status == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RESOURCE_EXHAUSTED"):
            return DeliveryError("Gmail's sending rate limit was reached; sending will resume shortly.", unavailable=True, retry_after=LIMIT_PAUSE)
        if status == 401 or reason == "authError":
            return DeliveryError("The Gmail connection has expired or was revoked. Reconnect Gmail in Response Cards.", unavailable=True, retry_after=RECONNECT_PAUSE)
        if status == 403:
            return DeliveryError(f"Gmail refused to send ({reason or 'forbidden'}). Check the Google Workspace policy or reconnect Gmail.",
                                 unavailable=True, retry_after=RECONNECT_PAUSE)
        if status in (400, 404, 413, 422):
            return DeliveryError("Gmail rejected this message. Check the address.")
        return DeliveryError("Delivery could not be confirmed.", uncertain=True)
