"""An immutable snapshot of public frontend files for one app build.

The shared build prefix also versions relative ES-module imports. Private data
and HTML are never included in the immutable asset cache. This build's text
sources go gzip-encoded, under their own ETag, to browsers that accept it.

Tabs opened before a restart keep working (AGENTS.md "Single-user access"): when
a build's page is first served, its public files are also kept in a small
content-addressed archive on disk, and an earlier build prefix is served from it,
so a module a page imports lazily still resolves in a tab that loaded an older
build. The archive holds only the same public sources (never HTML, API data or
teaching payloads), keeps the last KEEP builds, and lives on the workspace drive
(FACULTY_WEB_ASSET_CACHE, else the workspace's runtime or data folder), never in
the root disk's /tmp nor in the services' scratch folders, which are cleared at
each start. It is a runtime cache, not a storage owner: nothing in it is retained,
verified or backed up, and losing it only means an old tab is asked to reload.
"""

import fcntl
import gzip
import hashlib
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from flask import abort, request

# .jpg and .json are public asset types (added 2026-10-03 for the Magazine edition's plates, removed 2026-10-04; kept as types).
PUBLIC = {".js", ".css", ".svg", ".png", ".jpg", ".json", ".woff2"}
# Text sources sent gzip-encoded to browsers that accept it (only this build's; earlier builds come from the archive as stored).
COMPRESSIBLE = {".js", ".css", ".svg", ".json"}
KEEP = 8
BUILD = re.compile(r"[0-9a-f]{24}")
DIGEST = re.compile(r"[0-9a-f]{64}")
# A blob no kept build references is removed only once it is this old, so a build another process is still writing
# (its manifest comes last) never loses its files.
GRACE_SECONDS = 3600
log = logging.getLogger(__name__)


def forbidden(path):
    """The root disk's /tmp and the services' own scratch (…/tmp/web, …/tmp/worker, cleared at each start)."""
    parts = Path(os.path.abspath(path)).parts
    if parts[:2] == ("/", "tmp"):
        return True
    return any(parts[index] == "tmp" and parts[index + 1] in ("web", "worker") for index in range(len(parts) - 1))


def default_cache(app):
    """FACULTY_WEB_ASSET_CACHE, else the workspace's runtime folder (configured storage) or its data directory."""
    configured = os.environ.get("FACULTY_WEB_ASSET_CACHE", "").strip()
    if configured:
        return Path(configured)
    storage = app.extensions.get("course_storage")
    if storage is not None and getattr(storage, "nvme", None):
        return Path(storage.nvme) / "runtime" / "web-assets"
    database = app.config.get("DATABASE")
    return Path(database).parent / "web-assets" if database else None


class Archive:
    """builds/<version>.json maps each public path to the SHA-256 of its bytes, stored once in blobs/<sha256>."""

    def __init__(self, root):
        self.root = Path(root)
        self.builds = self.root / "builds"
        self.blobs = self.root / "blobs"

    @staticmethod
    def _write(path, content):
        # Every read checks the digest, so an interrupted write can only make an old tab reload.
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".part-")
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(content)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def save(self, version, files, keep=KEEP):
        """Record this build (blobs first, its manifest last), mark it newest, and keep only the newest `keep` builds."""
        self.builds.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest = {}
            for name, content in files.items():
                digest = hashlib.sha256(content).hexdigest()
                blob = self.blobs / digest
                if blob.exists():
                    os.utime(blob)
                else:
                    self._write(blob, content)
                manifest[name] = digest
            target = self.builds / f"{version}.json"
            if not target.exists():
                self._write(target, json.dumps(manifest, sort_keys=True).encode())
            os.utime(target)  # The build running now is the newest, even when it was archived before.
            self._prune(keep)

    def _prune(self, keep):
        builds = sorted(self.builds.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in builds[keep:]:
            stale.unlink(missing_ok=True)
        referenced = set()
        for manifest in builds[:keep]:
            try:
                entries = json.loads(manifest.read_text())
                if isinstance(entries, dict):
                    referenced.update(value for value in entries.values()
                                      if isinstance(value, str) and DIGEST.fullmatch(value))
            except (OSError, ValueError):
                continue
        cutoff = time.time() - GRACE_SECONDS
        for blob in self.blobs.iterdir():
            try:
                if blob.name not in referenced and blob.stat().st_mtime < cutoff:
                    blob.unlink()
            except OSError:
                continue

    def versions(self):
        return sorted(path.stem for path in self.builds.glob("*.json"))

    def read(self, version, filename):
        """The archived bytes of `filename` in build `version`, checked against their digest, or None."""
        if not BUILD.fullmatch(version or ""):
            return None
        try:
            manifest = json.loads((self.builds / f"{version}.json").read_text())
        except (OSError, ValueError):
            return None
        digest = manifest.get(filename) if isinstance(manifest, dict) else None
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            return None
        try:
            content = (self.blobs / digest).read_bytes()
        except OSError:
            return None
        return content if hashlib.sha256(content).hexdigest() == digest else None


def register(app, directory, cache=None):
    """Serve this build's public files under /assets/<build>/ and return index.html pointing at them.

    `cache` is the archive directory (tests pass one); None resolves the default once the app knows its workspace;
    False disables the archive (earlier builds then answer 404 and the page's 'Faculty was updated' notice offers a
    reload).
    """
    files = {
        file.relative_to(directory).as_posix(): file.read_bytes()
        for file in sorted(directory.rglob("*"))
        if file.is_file() and file.suffix in PUBLIC
    }
    etags = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode() + b"\0" + bytes.fromhex(etags[name]))
    version = digest.hexdigest()[:24]
    # Each file's gzip body, made on its first request rather than here: create_app also runs in the Inbox worker and in
    # every test. None when gzip saves less than a tenth. Two threads may compress one file at once; both get equal bytes.
    packed = {}
    prefix = f"/assets/{version}/"
    html = (directory / "index.html").read_text().replace("/static/", prefix)
    state = {"archive": None, "resolved": cache is False, "saved": cache is False, "lock": threading.Lock()}

    def archive():
        """The archive (None when it cannot be used). A failure only means earlier builds are not served."""
        if state["resolved"]:
            return state["archive"]
        with state["lock"]:
            if not state["resolved"]:
                root = Path(cache) if cache else default_cache(app)
                if root is None:
                    pass
                elif forbidden(root):
                    log.warning("Web asset archive %s is in temporary or service scratch space; earlier builds are not kept.", root)
                else:
                    state["archive"] = Archive(root)
                state["resolved"] = True
        return state["archive"]

    def save():
        """Keep this build once a page of it has been served: only then can a tab depend on it after a restart."""
        if state["saved"]:
            return
        with state["lock"]:
            if state["saved"]:
                return
            state["saved"] = True
        store = archive()
        if store is None:
            return
        try:
            store.save(version, files)
        except OSError as error:
            log.warning("Web asset archive %s is unavailable (%s); earlier builds are not kept.", store.root, error)
            state["archive"] = None

    app.extensions["web_assets"] = {"version": version, "prefix": prefix, "archive": archive, "save": save}

    @app.before_request
    def archive_served_build():
        if not state["saved"] and request.method == "GET" and request.path == "/":
            save()

    def gzipped(filename, content):
        if filename not in packed:
            body = gzip.compress(content, 6, mtime=0)
            packed[filename] = body if len(body) < 0.9 * len(content) else None
        return packed[filename]

    @app.get("/assets/<build>/<path:filename>", endpoint="versioned_asset")
    def asset(build, filename):
        encoded = None
        if build == version:
            content = files.get(filename)
            etag = etags.get(filename)
            if content is not None and Path(filename).suffix in COMPRESSIBLE and request.accept_encodings["gzip"] > 0:
                encoded = gzipped(filename, content)
        else:
            store = archive() if Path(filename).suffix in PUBLIC else None
            content = store.read(build, filename) if store else None
            etag = hashlib.sha256(content).hexdigest() if content is not None else None
        if content is None:
            abort(404, "This application asset is unavailable. Reload the workspace.")
        response = app.response_class(content if encoded is None else encoded, mimetype=mimetypes.guess_type(filename)[0])
        if encoded is None:
            response.set_etag(etag)
        else:
            # Its own ETag: a cache must never answer one encoding's validator with the other's bytes.
            response.headers["Content-Encoding"] = "gzip"
            response.set_etag(etag + "-gz")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.vary.add("Accept-Encoding")
        return response.make_conditional(request)

    return html
