"""Files the instructor attaches in the Assistant chat (requirement, 2026-10-09).

OWNER: the workspace. A file is the instructor's, given to the Assistant for a while: it is kept as scratch in the service's TMPDIR
(NVMe, STORAGE), never in the catalog or a drive folder, for EXPIRY (the life of an Approve card, so a drafted email or Canvas message
that names it can still be approved), then removed; a restart keeps it. Each file has its own random ID and one Faculty route,
GET /api/assistant/uploads/<id>, so the Assistant looks at it with `fetch_files` and an email or Canvas message attaches it by that
route like any other Faculty file (MAIL-ATTACHMENTS; canvas MESSAGES). Only the instructor's page uploads (the route is the
Assistant's own, `never` for the model); nothing a model writes becomes a file here.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from werkzeug.exceptions import Gone

from app_files import file_name

EXPIRY = 24 * 3600            # seconds a file is kept (assistant/service.py EXPIRY: an Approve card's life)
MAX_FILE = 25_000_000         # bytes one file may hold
MAX_FILES = 10                # files one upload, and one message, may carry
MAX_KEPT = 300_000_000        # bytes kept at once; an upload beyond this is refused until older files expire
IDENT = re.compile(r"[0-9a-f]{32}")
MEDIA = re.compile(r"[a-z]+/[a-z0-9.+-]{1,100}")


def route(ident):
    return f"/api/assistant/uploads/{ident}"


class ChatUploads:
    def __init__(self, root=None):
        self.root = Path(root or Path(tempfile.gettempdir()) / "faculty-assistant-uploads")
        self._lock = threading.Lock()

    # --- storage -------------------------------------------------------------------------------------------------------------
    def _folder(self, ident):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise Gone("That attached file is not here any more. Attach it again.")
        return self.root / ident

    def _meta(self, folder):
        try:
            meta = json.loads((folder / "meta.json").read_text())
        except (OSError, ValueError):
            return None
        return meta if isinstance(meta, dict) and time.time() - float(meta.get("at", 0)) < EXPIRY else None

    def _purge(self):
        """Removes files past EXPIRY (and half-written ones); what is left, in bytes."""
        kept = 0
        if not self.root.is_dir():
            return 0
        for folder in self.root.iterdir():
            meta = self._meta(folder) if folder.is_dir() else None
            if meta is None:
                if folder.is_dir() and time.time() - folder.stat().st_mtime < 60 and not (folder / "meta.json").exists():
                    continue  # another upload is writing it now
                if folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
                else:
                    folder.unlink(missing_ok=True)
                continue
            kept += int(meta.get("size", 0))
        return kept

    def save(self, uploads):
        """[{id, name, type, size, sha256, route}] for files sent from the instructor's page: (filename, media type, bytes)."""
        uploads = list(uploads or ())
        if not uploads:
            raise ValueError("Choose a file to attach.")
        if len(uploads) > MAX_FILES:
            raise ValueError(f"Attach at most {MAX_FILES} files at once.")
        found = []
        for filename, media, content in uploads:
            if not content:
                raise ValueError(f"{str(filename)[:120] or 'A file'} is empty.")
            if len(content) > MAX_FILE:
                raise ValueError(f"{str(filename)[:120]} is larger than {MAX_FILE / 1e6:.0f} MB.")
            name = " ".join(str(filename or "").split()) or "file"
            kind = mimetypes.guess_type(name)[0] or (media if isinstance(media, str) and MEDIA.fullmatch(media) else "") or "application/octet-stream"
            found.append((file_name(name, "", "", kind), kind, content))
        with self._lock:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._purge() + sum(len(c) for _, _, c in found) > MAX_KEPT:
                raise ValueError("Too many attached files are kept right now. Try again later, or attach smaller files.")
            saved = []
            for name, kind, content in found:
                ident = uuid.uuid4().hex
                folder = self.root / ident
                folder.mkdir(mode=0o700)
                (folder / "content").write_bytes(content)
                meta = {"id": ident, "name": name, "type": kind, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                        "at": time.time()}
                temporary = folder / "meta.json.part"
                temporary.write_text(json.dumps(meta))
                os.replace(temporary, folder / "meta.json")  # the file counts only once whole
                saved.append(self.public(meta))
        return saved

    @staticmethod
    def public(meta):
        return {"id": meta["id"], "name": meta["name"], "type": meta["type"], "size": meta["size"], "sha256": meta["sha256"],
                "route": route(meta["id"])}

    def describe(self, ident):
        """The file's {id, name, type, size, sha256, route}; Gone once it expired."""
        meta = self._meta(self._folder(ident))
        if meta is None:
            raise Gone("That attached file is not here any more (files are kept for a day). Attach it again.")
        return self.public(meta)

    def read(self, ident):
        """(description, bytes) of a kept file; Gone once it expired."""
        folder = self._folder(ident)
        found = self.describe(ident)
        try:
            content = (folder / "content").read_bytes()
        except OSError:
            raise Gone("That attached file is not here any more. Attach it again.") from None
        if hashlib.sha256(content).hexdigest() != found["sha256"]:
            raise Gone("That attached file could not be read whole. Attach it again.")
        return found, content
