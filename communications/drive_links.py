"""Files a student linked from Google Drive (COMMUNICATIONS "LINKED DRIVE FILES"; requirement, 2026-10-11).

A student who attaches a file from Google Drive sends only its link: the message Gmail holds carries no such attachment. For a
message a student wrote, each Drive file linked in the words they wrote now (never in quoted history) is read through Faculty's one
Google connection and kept with the message's attachments, so it is a file they sent wherever those are used (the timeline, an
absence's documentation, a follow-up's preparation, picture search). A link alone is never documentation.

Reads only: files.get (the file's name, kind and size, then its bytes) and files.export (a Google Doc, Sheet, Slides or Drawing as
a PDF). Every call goes to https://www.googleapis.com/drive/v3/files only, follows no redirect and carries the connection's access
token; nothing in Drive is changed, shared, listed or searched. It needs the `drive.readonly` permission, asked for once
(`response_cards.gmail.DRIVE_SCOPES`); until then a linked file is listed as not kept and says so.

`DriveReader.http` is replaced by tests (tests/test_communications.py); nothing else reaches Google. NOT READ ON REAL GOOGLE DRIVE
while this was built (CHECKS: a check never reaches a real outside service): the first live fetch must be looked at.
"""
import base64
import json
import re
import string
import urllib.error
import urllib.parse
import urllib.request

from gmail_mail.message import clean_text
from response_cards.gmail import DRIVE_SCOPES, USER_AGENT, DeliveryError, NoRedirect, _describe, _reason

API = "https://www.googleapis.com/drive/v3/files"
HOST = "www.googleapis.com"
MAX_LINKS = 5        # the most linked files one message keeps
NAMELESS = "Google Drive file"
FILE_ID = r"[A-Za-z0-9_-]{15,100}"
# A file, or a Google Doc, Sheet, Slides or Drawing, by its ID. A folder, a form and a published (/d/e/...) page are not files.
FILE = re.compile(r"https://drive\.google\.com/(?:file/(?:u/\d+/)?d/|(?:open|uc)\?(?:[^\s<>\"'#]*&)?id=)(" + FILE_ID + r")"
                  r"|https://docs\.google\.com/(?:document|spreadsheets|presentation|drawings)/(?:u/\d+/)?d/(" + FILE_ID + r")")
# Google's own kinds that have no bytes of their own: these are read as a PDF.
EXPORTED = {"application/vnd.google-apps." + kind for kind in ("document", "spreadsheet", "presentation", "drawing")}
# TAMU's mail filter rewrites every link in mail from outside (Proofpoint URL Defense v3, as seen on the live record 2026-10-11):
# https://urldefense.com/v3/__<the link, some characters replaced by * or **X>__;<those characters, base64>!!<token>$
WRAPPED = re.compile(r"https://urldefense(?:\.proofpoint)?\.com/v3/__(?P<url>.+?)__;(?P<chars>[A-Za-z0-9_-]*)!![^\s<>\"']*")
TOKEN = re.compile(r"\*(\*.)?")
RUNS = {char: length for length, char in enumerate(string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_", 2)}
QUOTED = re.compile(r"\s*(On .{3,240} wrote:|-{2,}\s*Original Message|From: .+)\s*$", re.I)  # jev.send_checks.strip_quoted's rule
LABEL = re.compile(r"[^\n<>]{1,150}\.[A-Za-z0-9]{2,5}")

PERMISSION = "Faculty may not read your Google Drive yet"
WAITS = ("permission", "sharing", "google")


class NotKept(Exception):
    """Why a linked file was not kept, and what it waits on: "permission" (the instructor allows Faculty to read Drive, or sets
    Google up), "sharing" (the student shares the file), "google" (Drive answers again), or "" (nothing will change it)."""

    def __init__(self, reason, wait=""):
        super().__init__(reason)
        self.wait = wait


def unwrapped(text):
    """`text` with every link the mail filter rewrote put back as the link that was sent."""
    def restore(match):
        try:
            chars = base64.urlsafe_b64decode(match.group("chars") + "=" * (-len(match.group("chars")) % 4)).decode("utf-8", "replace")
        except ValueError:
            chars = ""
        position = 0

        def replaced(token):
            nonlocal position
            length = RUNS.get(token.group(1)[-1], 1) if token.group(1) else 1
            piece = chars[position:position + length]
            position += length
            return piece

        url = urllib.parse.unquote(TOKEN.sub(replaced, match.group("url")))
        return re.sub(r"^(https?:/)(?=[^/])", r"\1/", url)  # the filter sometimes writes one slash after the scheme
    return WRAPPED.sub(restore, str(text or ""))


def own_words(text):
    """The lines written now: quoted lines ("> ...") and everything from a reply header on are set aside (a reply carries the links
    of the message it answers, which may be the instructor's own file)."""
    kept = []
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        if QUOTED.match(line):
            break
        if not re.match(r"\s*>", line):
            kept.append(line)
    return kept


def linked(text, limit=MAX_LINKS):
    """[{id, name}] of the Drive files linked in the words a message's writer wrote now, in the order linked, each once, at most
    `limit`. `name` is the file name Gmail writes on the line above a Drive attachment's link, or '' when there is none."""
    lines = [unwrapped(line) for line in own_words(text)]
    found = {}
    for number, line in enumerate(lines):
        for match in FILE.finditer(line):
            ident = match.group(1) or match.group(2)
            above = next((prior.strip() for prior in reversed(lines[max(0, number - 2):number]) if prior.strip()), "")
            name = clean_text(above, 200) if LABEL.fullmatch(above) and "://" not in above else ""
            found[ident] = found.get(ident) or name
    return [{"id": ident, "name": name} for ident, name in list(found.items())[:limit]]


def drive_request(url, headers=None, timeout=60, limit=65536):
    """(status, bytes) of one GET that follows no redirect; at most `limit` + 1 bytes of an answer are read."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, method="GET")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            return response.status, response.read(limit + 1)
    except urllib.error.HTTPError as error:
        return error.code, error.read(65536)


class DriveReader:
    """One linked file at a time, as the connected account may read it. Reads only."""

    def __init__(self, connection):
        self.connection = connection  # response_cards.gmail.GmailConnection: the one Google connection
        self.http = drive_request

    def ready(self):
        """Faculty's Google connection exists and may read Drive files."""
        return bool(self.connection.granted(DRIVE_SCOPES))

    def _token(self, force=False):
        try:
            return self.connection.access_token(force=force)
        except DeliveryError as error:
            if "expired or was revoked" in str(error) or "not connected" in str(error):
                raise NotKept("Faculty's Google connection has expired or was revoked", "permission") from None
            raise NotKept("Google could not be reached", "google") from None

    def _get(self, path, params, limit=65536, timeout=60):
        url = API + path + "?" + urllib.parse.urlencode(params)
        if urllib.parse.urlsplit(url).hostname != HOST:
            raise NotKept("Faculty reads a linked file from Google Drive only")
        token, refreshed = self._token(), False
        while True:
            try:
                status, content = self.http(url, {"Authorization": f"Bearer {token}"}, timeout, limit)
            except OSError:
                raise NotKept("Google Drive could not be reached", "google") from None
            # A token made before the instructor allowed Drive does not carry the permission yet: a fresh one does.
            if (status == 401 or (status == 403 and b"insufficient" in content.lower())) and not refreshed:
                token, refreshed = self._token(force=True), True
                continue
            break
        if 200 <= status < 300:
            return content
        raise self.failure(status, content)

    def failure(self, status, content):
        try:
            payload = json.loads(content.decode("utf-8", "replace")) if content.strip() else {}
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        reason, said = _reason(payload), _describe(payload)
        account = self.connection.email or "your Google account"
        if status == 401 or reason == "authError":
            return NotKept("Faculty's Google connection has expired or was revoked", "permission")
        if reason in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used in project" in said or "is disabled" in said:
            project = self.connection.project
            where = "https://console.cloud.google.com/apis/library/drive.googleapis.com" + (f"?project={project}" if project else "")
            return NotKept(f"the Google Drive API is not turned on for Faculty's Google Cloud project (turn it on at {where})", "permission")
        if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT") or (status == 403 and "scope" in said.lower()):
            return NotKept(PERMISSION, "permission")
        if reason == "exportSizeLimitExceeded":
            return NotKept("too large for Google to turn into a PDF")
        if status == 429 or status >= 500 or reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded",
                                                         "downloadQuotaExceeded", "RESOURCE_EXHAUSTED"):
            return NotKept("Google Drive is not answering right now", "google")
        if 300 <= status < 400:
            return NotKept("Google Drive sent Faculty elsewhere for it, which Faculty does not follow", "google")
        if status in (403, 404):
            # Drive answers "not found" for a file the account may not see: the student shared it with nobody, or with another address.
            return NotKept(f"Google Drive does not show it to {account}: the student has not shared it with that account, or removed it", "sharing")
        return NotKept(f"Google Drive refused it ({said or status})"[:300], "google")

    def fetch(self, file_id, limit):
        """{name, type, content} of one linked file, at most `limit` bytes; anything else raises NotKept with the reason."""
        if not isinstance(file_id, str) or not re.fullmatch(FILE_ID, file_id):
            raise NotKept("that is not a Google Drive file")
        if not self.ready():
            raise NotKept(PERMISSION, "permission")
        try:
            info = json.loads(self._get(f"/{file_id}", {"fields": "id,name,mimeType,size", "supportsAllDrives": "true"}).decode("utf-8", "replace"))
        except ValueError:
            info = None
        if not isinstance(info, dict) or info.get("id") != file_id:
            raise NotKept("Google Drive did not describe the file", "google")
        kind = str(info.get("mimeType") or "")
        name = " ".join(clean_text(info.get("name"), 200).split()) or NAMELESS
        if kind in EXPORTED:
            content = self._get(f"/{file_id}/export", {"mimeType": "application/pdf"}, limit, 180)
            name, kind = name if name.lower().endswith(".pdf") else name + ".pdf", "application/pdf"
        elif kind.startswith("application/vnd.google-apps."):
            raise NotKept("it is a Google Drive folder, not a file" if kind.endswith(".folder") else "Google Drive holds it as something that is not a file")
        else:
            try:
                size = int(info.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size > limit:
                raise NotKept("larger than Faculty keeps")
            content = self._get(f"/{file_id}", {"alt": "media", "supportsAllDrives": "true"}, limit, 180)
        if len(content) > limit:
            raise NotKept("larger than Faculty keeps")
        if not content:
            raise NotKept("Google Drive returned an empty file", "google")
        return {"name": name, "type": (kind if re.fullmatch(r"[\w.+-]+/[\w.+-]+", kind) else "application/octet-stream")[:100], "content": content}
