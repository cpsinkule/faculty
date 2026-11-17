"""Faculty's files read the way a page reads them: through the app's own GET route, in process, so every guard of the ordinary request
applies (AGENTS.md "Gmail in Faculty" MAIL-ATTACHMENTS, "Faculty assistant" FILES). Used by mail attachments (gmail_mail/attachments.py)
and the in-app file viewer (file_viewer/). A route is named by its path under /api/, never by bytes from a browser or a model; what the
assistant's FILE_BLOCKED names (sign-in windows, callbacks, access links, the workspace export, the app's shell) is never read, nor the
assistant's own routes other than its saved files, nor mail or viewer routes. Nothing is kept.
"""
import hashlib
import mimetypes
import re
from urllib.parse import unquote, urlsplit

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

MAX_ROUTE = 1000
MAX_NAME = 120
UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
ROUTE = re.compile(r"/api/[A-Za-z0-9/_.~%:=&?-]+")


def blocked(endpoint):
    """Routes never read as files: sign-in windows, callbacks, access links, the workspace export, the app's shell (the assistant's own
    FILE_BLOCKED list, one list for every file Faculty hands out), the assistant's routes other than its saved files, and the mail and
    viewer routes (data, or files made from another route)."""
    from assistant import policy
    return endpoint in policy.FILE_BLOCKED or (endpoint.startswith("assistant.") and endpoint not in policy.FILE_OWN) \
        or (endpoint.startswith(("gmail_mail.", "file_viewer.")) and endpoint != "gmail_mail.attachment")


def file_name(wanted, disposition, path, media):
    """The name the file has in the message: the one asked for, else the route's own, cleaned, with an extension that fits its type."""
    name = " ".join(str(wanted or "").split())
    if not name:
        found = re.search(r"filename\*=UTF-8''([^;]+)", disposition or "") or re.search(r'filename="?([^";]+)"?', disposition or "")
        name = unquote(found.group(1)) if found else urlsplit(path).path.rstrip("/").rsplit("/", 1)[-1]
    name = UNSAFE.sub("_", name).strip(" .") or "file"
    stem, dot, extension = name.rpartition(".")
    fits = mimetypes.guess_extension(media) if media and media != "application/octet-stream" else None
    if fits and mimetypes.guess_type(name)[0] != media:
        name = (stem if dot and mimetypes.guess_type(name)[0] else name) + fits
    return name[-MAX_NAME:] if len(name) > MAX_NAME else name


class AppFiles:
    """Reads a file the way a page does: through the app's own GET route, in process. `use` names what the file is read for in a
    refusal ("attached", "opened")."""

    def __init__(self, app, use="attached"):
        self.app, self.use = app, use

    def endpoint(self, path):
        """The Flask endpoint a path names, or None (nothing is read)."""
        try:
            return self.app.url_map.bind("localhost").match(urlsplit(str(path or "")).path, method="GET")[0]
        except (NotFound, MethodNotAllowed, RequestRedirect):
            return None

    def read(self, path, wanted=""):
        """(route as fetched, {name, type, size, sha256}, bytes). Raises ValueError naming what is wrong with this route."""
        if not isinstance(path, str) or not path or len(path) > MAX_ROUTE:
            raise ValueError("Name the file by the Faculty route that serves it (/api/...).")
        parts = urlsplit(path)
        if parts.scheme or parts.netloc or not parts.path.startswith("/api/") or ".." in parts.path or any(ord(c) < 33 for c in path):
            raise ValueError(f"“{path[:120]}” is not a Faculty file route (/api/...).")
        endpoint = self.endpoint(parts.path)
        if endpoint is None:
            raise ValueError(f"No Faculty file is served at {parts.path[:120]}.")
        if blocked(endpoint):
            raise ValueError(f"{parts.path[:120]} cannot be {self.use} (sign-in, access links, exports and mail never are).")
        route = parts.path + ("?" + parts.query if parts.query else "")
        if not ROUTE.fullmatch(route):  # the characters the assistant's card draws as a link (static/assistant.js safeRoute)
            raise ValueError(f"“{route[:120]}” has characters a file route does not use; percent-encode them.")
        response = self.app.test_client().get(route)
        try:
            content, media = response.get_data(), response.mimetype or "application/octet-stream"
            if response.status_code != 200:
                said = (response.get_json(silent=True) or {}).get("error") if response.is_json else ""
                raise ValueError(f"{parts.path[:120]} answered {response.status_code}" + (f": {said}" if said else "."))
            if media == "application/json" and endpoint not in ("assistant.artifact_file", "assistant.upload_file"):
                raise ValueError(f"{parts.path[:120]} answers with data, not a file.")
            disposition = response.headers.get("Content-Disposition", "")
        finally:
            response.close()
        name = file_name(wanted, disposition, parts.path, media)
        return route, {"name": name, "type": media, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}, content
