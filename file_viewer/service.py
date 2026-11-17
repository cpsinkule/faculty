"""Faculty's in-app file viewer (requirement, 2026-10-01).

A read-only projection owned by the workspace: a file is named by the Faculty route that serves it and read through that route in
process (app_files.AppFiles), so every guard applies and nothing new becomes reachable. What the page shows is made from it here and
never stored:

  * a PDF as pictures of its pages, rendered in the document sandbox (operation `look`, four pages a call), never in this process
    and never opened as a document in the app's own pages (student files stay out of the app's origin; COMMUNICATIONS);
  * except a PDF Faculty made or the instructor uploaded (NATIVE_PDFS; requirement, 2026-10-01), which the browser's own PDF viewer shows inside the viewer, from `/api/viewer/pdf`: the one route
    the app lets its own pages frame (app.py), serving only such a PDF, checked by its first bytes;
  * a picture re-encoded (upright, PNG or JPEG, at most MAX_EDGE on its long side), so whatever type its route names, only a plain
    raster picture reaches the page; a HEIC photo (an iPhone's) is decoded in the document sandbox (operation `heif`) first;
  * a CSV (the gradebook's and bubble sheets' exports) as a table, at most TABLE_ROWS rows of TABLE_COLUMNS cells;
  * text (Markdown, JSON) as text, at most TEXT_CHARS, escaped by the page;
  * an email (.eml: the Inbox's original submission, a kept message of the communications record) as a mail program shows it: who,
    when, the subject, its text (HTML reduced to text) and its attachments' names and sizes, never its HTML;
  * anything else as its name and size, for Download.

Rendered pages and decoded HEIC photos are kept in process memory, keyed by the file's SHA-256 and page (immutable), at most
CACHE_BYTES together, oldest dropped first (PERFORMANCE: bounded, immutable rows only).
"""
from __future__ import annotations

import csv
import io
import mimetypes
import re
import threading
import weakref
from collections import OrderedDict

from werkzeug.exceptions import Conflict, NotFound

from app_files import AppFiles
from document_identity import process

BATCH = 4                      # pages one sandbox call renders (document_identity.media.LOOK_PAGES)
CACHE_BYTES = 96 * 1024 ** 2   # rendered pages kept in memory, all files together
MAX_EDGE = 3000                # a picture is scaled to fit this
MAX_PIXELS = 60_000_000
TEXT_CHARS = 200_000
SIGNATURES = ((b"%PDF-", "pdf"), (b"\x89PNG\r\n\x1a\n", "image"), (b"\xff\xd8\xff", "image"), (b"GIF87a", "image"), (b"GIF89a", "image"))
TEXT_TYPES = ("text/", "message/rfc822", "application/json", "application/csv", "application/x-tex")
DIGEST = re.compile(r"[0-9a-f]{64}")
EMAIL_HEADERS = ("From", "To", "Cc", "Date", "Subject")
# PDFs Faculty made, or the instructor uploaded, that the browser's own PDF viewer may show. Never a file from a student or an outside
# sender: the communications record, absence documentation and Inbox submissions keep the sandbox's pictures (COMMUNICATIONS).
NATIVE_PDFS = {
    "course_notes.original", "course_notes.part",          # the instructor's lecture notes and Faculty's split of them
    "question_bank.upload_file",                            # a PDF the instructor read into the question bank
    "documents.download", "documents.labels",               # print batches and their labels
    "documents.version_preview",                            # immutable, unstamped instructor canonical
    "documents.stack_download",                             # a stack's versions interleaved into one print file
    "documents.packet_download", "documents.assembly_download",  # exam-day packets; a student's paper as Faculty assembled it
    "seating.room_export", "seating_student_mail.pdf",      # seating charts Faculty drew
    "quiz_drafts.file", "quiz_drafts.stack",                # quizzes Faculty compiled and their print stacks
    "assistant.artifact_file",                              # files the assistant saved (plain-paragraph PDFs)
    "assignment_templates.pdf",                             # an assignment's blank template, as the instructor gave it
}
MAX_ATTACHMENTS_LISTED = 50
TABLE_ROWS, TABLE_COLUMNS, TABLE_CELL = 5000, 100, 1000


def kind_of(content, media):
    """"pdf" | "image" | "text" | "file": by the file's first bytes where they say, else by its media type (an SVG is never a picture)."""
    for signature, kind in SIGNATURES:
        if content.startswith(signature):
            return kind
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" or process.is_heif(content):
        return "image"
    media = (media or "").split(";")[0].strip().lower()
    if media.startswith(TEXT_TYPES) and media != "text/html" and "svg" not in media:
        return "text"
    return "file"


def table_view(content):
    """{rows: [[cell]], truncated}: a CSV's rows (the first is its header), each cell text, at most TABLE_ROWS by TABLE_COLUMNS."""
    text = content.decode("utf-8-sig", errors="replace")
    rows, truncated = [], False
    for row in csv.reader(io.StringIO(text, newline="")):
        if len(rows) >= TABLE_ROWS:
            truncated = True
            break
        if len(row) > TABLE_COLUMNS:
            truncated = True
        rows.append([cell[:TABLE_CELL] for cell in row[:TABLE_COLUMNS]])
    return {"rows": rows, "truncated": truncated}


def email_view(content):
    """{headers: [[name, value]], text, truncated, attachments: [{name, type, size}]} of an RFC 822 message. Header values are
    decoded and stripped of control characters; the text is the plain part, else the HTML part reduced to its text."""
    from email import policy
    from email.parser import BytesParser
    from gmail_mail.message import clean_text, html_text
    message = BytesParser(policy=policy.default).parsebytes(content)
    headers = []
    for name in EMAIL_HEADERS:
        try:
            value = message.get(name)
        except Exception:  # noqa: BLE001 - a malformed header is left out
            value = None
        if value:
            headers.append([name, " ".join(clean_text(str(value), 2000).split())])
    text = ""
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        try:
            text = body.get_content()
        except (LookupError, ValueError):  # an unknown charset: the bytes as UTF-8
            text = (body.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if body.get_content_type() == "text/html":
            text = html_text(text)
    text = clean_text(text, TEXT_CHARS + 1)
    files = []
    for part in message.iter_attachments():
        payload = part.get_payload(decode=True)
        files.append({"name": clean_text(part.get_filename() or "attachment", 200), "type": part.get_content_type(),
                      "size": len(payload) if isinstance(payload, bytes) else 0})
        if len(files) >= MAX_ATTACHMENTS_LISTED:
            break
    return {"headers": headers, "text": text[:TEXT_CHARS], "truncated": len(text) > TEXT_CHARS, "attachments": files}


class FileViewer:
    def __init__(self, app):
        self.files = AppFiles(app, use="opened")
        self._pages, self._size, self._totals = OrderedDict(), 0, OrderedDict()  # rendered pages; each PDF's page count by digest
        self._lock = threading.Lock()
        # PERFORMANCE: queued callers retain their batch lock through failures and cache publication. The weak map drops it
        # only when the last caller releases its reference, so a later request cannot start a second render beside a waiter.
        self._rendering = weakref.WeakValueDictionary()

    def _read(self, route):
        try:
            return self.files.read(route)
        except ValueError as problem:
            raise NotFound(str(problem)) from None

    # -- What the viewer shows ---------------------------------------------------------------------------------------------------
    def describe(self, route, native=False):
        """{route, name, kind, type, size, ...}: a PDF's page count and first page's size, a picture's size, a text's text. A PDF
        Faculty made says `native`; when the page will show it in the browser's own PDF viewer (`native`), it is not rendered here."""
        route, meta, content = self._read(route)
        media = meta["type"]
        if media == "application/octet-stream":  # a route that does not say: its file name does (the Inbox's original.eml)
            media = mimetypes.guess_type(meta["name"])[0] or media
        kind = kind_of(content, media)
        answer = {"route": route, "name": meta["name"], "kind": kind, "type": meta["type"], "size": meta["size"], "sha256": meta["sha256"]}
        if kind == "text" and media.split(";")[0].strip().lower() == "message/rfc822":
            try:
                answer.update(kind="email", **email_view(content))
                return answer
            except Exception:  # noqa: BLE001 - a message Python cannot read is shown as its text instead
                pass
        if kind == "pdf" and self.files.endpoint(route) in NATIVE_PDFS:
            answer["native"] = True
            if native:
                return answer
        if kind == "pdf":
            try:
                first, total = self._render(1, content, meta["sha256"])
                from PIL import Image
                answer.update(pages=total, page_size=list(Image.open(io.BytesIO(first)).size))
            except ValueError as problem:
                answer.update(kind="file", note=f"The pages could not be shown ({problem}). Download it to open it.")
        elif kind == "image":
            try:
                answer["picture_size"] = list(self._picture(content, meta["sha256"], size_only=True))
            except ValueError as problem:
                answer.update(kind="file", note=f"The picture could not be shown ({problem}). Download it to open it.")
        elif kind == "text" and (media.split(";")[0].strip().lower() in ("text/csv", "application/csv") or meta["name"].lower().endswith(".csv")):
            try:
                answer.update(kind="table", **table_view(content))
            except csv.Error:  # a cell past csv's field limit: shown as text, like any other text file
                text = content.decode("utf-8", errors="replace")
                answer.update(text=text[:TEXT_CHARS], truncated=len(text) > TEXT_CHARS)
        elif kind == "text":
            text = content.decode("utf-8", errors="replace")
            answer.update(text=text[:TEXT_CHARS], truncated=len(text) > TEXT_CHARS)
        return answer

    def page(self, route, number, digest=None):
        """(PNG bytes, page count) of page `number` of the PDF at `route`. With the `digest` the viewer opened (`describe`), a page
        already rendered is served from memory without reading the file again (a long PDF is read once per BATCH pages, not per
        page); otherwise the file is read through its route, must still have that digest (409: it changed since it was opened),
        and is rendered in the sandbox with the BATCH pages around the one asked for."""
        if type(number) is not int or number < 1:
            raise ValueError("Pages start at 1.")
        if digest is not None and not (isinstance(digest, str) and DIGEST.fullmatch(digest)):
            raise ValueError("That is not a file's digest.")
        if digest:
            with self._lock:
                found = self._pages.get((digest, number))
                if found:
                    self._pages.move_to_end((digest, number))
                    return found
                total = self._totals.get(digest)
            if total is not None and number > total:
                raise ValueError(f"The PDF has {total} page{'' if total == 1 else 's'}.")
        route, meta, content = self._read(route)
        if digest and meta["sha256"] != digest:
            raise Conflict("The file changed since it was opened. Open it again.")
        return self._render(number, content, meta["sha256"])

    def _render(self, number, content, digest):
        if kind_of(content, "") != "pdf":
            raise ValueError("This file is not a PDF.")
        first = (number - 1) // BATCH * BATCH + 1
        with self._lock:
            found = self._pages.get((digest, number))
            if found:
                self._pages.move_to_end((digest, number))
                return found
            total = self._totals.get(digest)
        if total is not None and number > total:
            raise ValueError(f"The PDF has {total} page{'' if total == 1 else 's'}.")
        with self._lock:
            busy = self._rendering.setdefault((digest, first), threading.Lock())
        with busy:  # one render of a batch at a time: pages of the same batch asked together wait for it
            with self._lock:
                found = self._pages.get((digest, number))
            if found:
                return found
            result = process.run("look", content, {"pages": list(range(first, first + BATCH))}, timeout=120)
            total = result["total"]
            found = None
            with self._lock:
                self._totals[digest] = total
                while len(self._totals) > 512:
                    self._totals.popitem(last=False)
                for item in result["pages"]:
                    value = (item["png"], total)
                    self._keep((digest, item["page"]), value)
                    if item["page"] == number:
                        found = value  # ASSISTANT VIEWER: eviction limits retention, never whether this request succeeds.
        if not found:
            raise ValueError(f"The PDF has {total} page{'' if total == 1 else 's'}.")
        return found

    def _keep(self, key, value):
        if key in self._pages:
            return
        self._pages[key] = value
        self._size += len(value[0])
        while self._size > CACHE_BYTES and self._pages:
            _, (png, _) = self._pages.popitem(last=False)
            self._size -= len(png)

    def pdf(self, route, digest=None):
        """(file name, bytes) of a PDF Faculty made, for the browser's own PDF viewer. Anything else is refused."""
        if self.files.endpoint(route) not in NATIVE_PDFS:
            raise NotFound("Only PDFs Faculty made open in the browser's PDF viewer.")
        route, meta, content = self._read(route)
        if kind_of(content, "") != "pdf":
            raise NotFound("This file is not a PDF.")
        if digest and meta["sha256"] != digest:
            raise Conflict("The file changed since it was opened. Open it again.")
        return meta["name"], content

    def picture(self, route):
        """(media type, bytes) of the picture at `route`: upright, flattened, scaled to fit MAX_EDGE, PNG or (when large) JPEG."""
        _, meta, content = self._read(route)
        if kind_of(content, meta["type"]) != "image":
            raise ValueError("This file is not a picture.")
        return self._picture(content, meta["sha256"])

    def _heif(self, content, digest):
        """(PNG bytes, size) of a HEIC photo: decoded in the document sandbox once per file, then kept with the rendered pages."""
        with self._lock:
            found = self._pages.get((digest, "heif"))
            if found:
                self._pages.move_to_end((digest, "heif"))
                return found
        found = process.heif_picture(content, MAX_EDGE)
        with self._lock:
            self._keep((digest, "heif"), found)
        return found

    def _picture(self, content, digest, size_only=False):
        from PIL import Image, ImageOps
        if process.is_heif(content):
            png, size = self._heif(content, digest)
            if size_only:
                return size
            content = png  # upright, flat and within MAX_EDGE already: re-encoded below like any PNG
        try:
            image = Image.open(io.BytesIO(content))
        except Exception:  # noqa: BLE001 - a picture Pillow cannot read is said, never an error page
            raise ValueError("it is not a picture this viewer reads") from None
        # MPO is a JPEG carrying a second picture (an iPhone photo's HDR gain map): its first picture is the photo.
        if image.format not in ("PNG", "JPEG", "MPO", "GIF", "WEBP"):
            raise ValueError("it is not a PNG, JPEG, GIF or WebP picture")
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("it is too large to show")
        if size_only:
            return image.size
        image.load()
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA", "P", "PA"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.getchannel("A"))
            image = flat
        elif image.mode not in ("L", "RGB"):
            image = image.convert("RGB")
        image.thumbnail((MAX_EDGE, MAX_EDGE))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        if buffer.tell() <= 2_500_000:
            return "image/png", buffer.getvalue()
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=88)
        return "image/jpeg", buffer.getvalue()
