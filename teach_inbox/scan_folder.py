"""The scanner folder (SCANNER-INTAKE "SCANNER FOLDER"): PDFs that a network scanner saves into a watched folder on this
server become Inbox scans, exactly as the instructor's own upload or `manage_inbox.py scan` does.

Each scanner saves into its own subfolder (`epson/`, `brother/`), whose name becomes the arrival's sender; a PDF at the top
level is accepted too. A file is taken only once it has stopped changing and ends like a complete PDF, so a scan still being
written over the network is never cut short. Once the Inbox has retained it (its durable receipt), the file is removed from
the folder; a file the Inbox refuses moves to `not-accepted/` beside a note saying why, and nothing is lost.
"""
import logging
import os
import time
from pathlib import Path

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from .protocol import MAX_SCAN_FILE
from .service import sha

LOG = logging.getLogger(__name__)
REFUSED = "not-accepted"
SETTLE_SECONDS = 10  # a file unchanged this long, across two looks, has been fully written
LOOK_EVERY = 3  # seconds between looks at the folder


def file_state(stat):
    """Identity and content changes, excluding access time changed by our own read."""
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class ScanFolder:
    def __init__(self, inbox, root, *, settle=SETTLE_SECONDS, look_every=LOOK_EVERY, clock=time.monotonic):
        self.inbox, self.root = inbox, Path(root)
        self.settle, self.look_every, self.clock = settle, look_every, clock
        self._seen = {}  # path -> (size, mtime_ns, first seen at this size and time)
        self._looked = None

    def candidates(self):
        """Files waiting in the folder (top level and one level of scanner subfolders), oldest first."""
        if not self.root.is_dir():
            return []
        found = []
        for entry in self.root.iterdir():
            if entry.name.startswith((".", "~")) or entry.name == REFUSED:
                continue
            if entry.is_dir() and not entry.is_symlink():
                found.extend(child for child in entry.iterdir()
                             if child.is_file() and not child.is_symlink() and not child.name.startswith((".", "~")))
            elif entry.is_file() and not entry.is_symlink():
                found.append(entry)
        return sorted(found, key=lambda path: (path.stat().st_mtime_ns, str(path)))

    def settled(self, path, now):
        stat = path.stat()
        key = (stat.st_size, stat.st_mtime_ns)
        previous = self._seen.get(path)
        if previous is None or previous[:2] != key:
            self._seen[path] = (*key, now)
            return False
        return stat.st_size > 0 and now - previous[2] >= self.settle

    def process_one(self):
        """Retain one settled file; True when something changed. Bounded: at most one file a turn."""
        now = self.clock()
        if self._looked is not None and now - self._looked < self.look_every:
            return False
        self._looked = now
        try:
            waiting = self.candidates()
        except OSError:
            LOG.exception("The scanner folder %s could not be read", self.root)
            return False
        present = set(waiting)
        self._seen = {path: seen for path, seen in self._seen.items() if path in present}
        for path in waiting:
            try:
                if self.settled(path, now) and self.retain(path):
                    return True
            except FileNotFoundError:
                self._seen.pop(path, None)  # removed by the scanner or someone else meanwhile
        return False

    def retain(self, path):
        scanner = path.parent.name if path.parent != self.root else ""
        before = path.stat()
        if before.st_size > MAX_SCAN_FILE:
            return self.refuse(path, "This file is larger than the 512 MB a scan may be.")
        content = path.read_bytes()
        if file_state(path.stat()) != file_state(before):
            self._seen.pop(path, None)
            return False
        if not content.startswith(b"%PDF-"):
            return self.refuse(path, "This is not a PDF. Set the scanner to save scans as PDF files.")
        if b"%%EOF" not in content[-2048:]:
            # A PDF still being written ends without its trailer; wait for it, unless it has been left like this.
            first = self._seen.get(path, (0, 0, self.clock()))[2]
            if self.clock() - first < 30 * 60:
                return False
            return self.refuse(path, "This PDF is incomplete: it never received its ending. Scan the pages again.")
        sender = f"scanner-folder/{scanner}" if scanner else "scanner-folder"
        try:
            job = self.inbox.submit({"action": "scan.receive"}, [{"filename": path.name, "content": content}], source="cli",
                                    sender=sender, idempotency_key=f"scan:{sha(content)}", instructor_review_required=True,
                                    large_scans=True)
        except Conflict:
            job = None  # the same bytes already arrived under another file name: the Inbox already has this scan
        except ValueError as error:
            return self.refuse(path, str(error))
        # SCANNER-INTAKE: remove only the copy whose bytes the Inbox retained. A scanner can reuse this name while
        # submission is in progress; leave that new arrival to settle and receive its own durable receipt.
        if file_state(path.stat()) == file_state(before):
            path.unlink()
        self._seen.pop(path, None)
        LOG.info("Scanner folder: %s retained as Inbox job %s", path.name, job["id"] if job else "(already received)")
        return True

    def refuse(self, path, reason):
        refused = self.root / REFUSED
        refused.mkdir(exist_ok=True)
        stem = f"{path.parent.name}-{path.name}" if path.parent != self.root else path.name
        target = refused / stem
        number = 1
        while target.exists():
            number += 1
            target = refused / f"{Path(stem).stem}-{number}{Path(stem).suffix}"
        os.replace(path, target)
        target.with_name(target.name + ".txt").write_text(f"{timestamp()}\n{reason}\n")
        self._seen.pop(path, None)
        LOG.warning("Scanner folder: %s not accepted: %s", path.name, reason)
        return True
