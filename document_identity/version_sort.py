"""Instructor-requested sorting of scanned pages by a printed version label.

Quizzes printed without Data Matrix marks carry only a header such as
"Version A". On request, each nonblank page's header band is read by the local
OCR runtime, a label is matched exactly against the chosen pattern, unlabeled
sides are attached to the labeled page before them, and one PDF per version
(plus an Unsorted PDF) is retained on the source Inbox message.

OCR text is an untrusted draft. Sorting never identifies printed paper, never
clears the Inbox instructor-review gate and never dispatches an action. Work is
queued durably: the Inbox worker reads one page per turn, at most one scan is
live per message, and the queue refuses new requests once it is full.
"""
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import ExitStack

from werkzeug.exceptions import Conflict, Forbidden

from course_storage import timestamp
import faculty_ocr
from faculty_ocr import BUSY, OcrError, recognize_bands
from rosters import connect
from teach_inbox.service import encoded, sha
from . import process
from .service import text_field
from .storage import publish

PRESETS = {
    "version-letter": {"label": "Version A, B, C…", "pattern": r"version\s*[:#.\-]?\s*([A-Z])(?![A-Za-z])", "prefix": "Version "},
    "version-number": {"label": "Version 1, 2, 3…", "pattern": r"version\s*[:#.\-]?\s*([0-9]{1,2})(?![0-9])", "prefix": "Version "},
    "form-letter": {"label": "Form A, B, C…", "pattern": r"form\s*[:#.\-]?\s*([A-Z])(?![A-Za-z])", "prefix": "Form "},
    "form-number": {"label": "Form 1, 2, 3…", "pattern": r"form\s*[:#.\-]?\s*([0-9]{1,2})(?![0-9])", "prefix": "Form "},
}
BANDS = {"top": "Top quarter of the page", "bottom": "Bottom quarter of the page", "full": "Whole page (slower)"}
MAX_QUEUED = 10          # live requests across all scans before new ones are refused
MAX_TEXT = 2000          # OCR characters examined per band
MAX_PAGES_PER_QUIZ = 20
MIN_ORIENTATION_TEXT = 24   # readable characters a rotation needs before it can decide orientation on its own
ORIENTATION_RATIO = 3       # and how much more readable than the opposite rotation
PATTERN_SECONDS = 5  # a fresh interpreter under OCR or GPU load can take most of a second to start; runaway patterns still stop
PATTERN_WORKER = "import json,re,sys; p,t=json.load(sys.stdin); print(json.dumps([m.group(1) for m in re.finditer(p,t,re.IGNORECASE)]))"


def settings(value):
    """Validate the instructor's sorting choices; the pattern needs one capture group."""
    if not isinstance(value, dict):
        raise ValueError("Supply the version sorting settings.")
    preset = value.get("preset", "version-letter")
    if preset == "custom":
        pattern, prefix = text_field(value.get("pattern"), "Label pattern", 200), ""
    elif isinstance(preset, str) and preset in PRESETS:
        pattern, prefix = PRESETS[preset]["pattern"], PRESETS[preset]["prefix"]
    else:
        raise ValueError("Choose a label preset or a custom pattern.")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as error:
        raise ValueError("The label pattern is not a valid regular expression.") from error
    if compiled.groups != 1:
        raise ValueError("The label pattern needs exactly one capture group around the version.")
    pages = value.get("pages_per_quiz", 2)
    if type(pages) is not int or not 1 <= pages <= MAX_PAGES_PER_QUIZ:
        raise ValueError(f"Pages per quiz must be a whole number between 1 and {MAX_PAGES_PER_QUIZ}.")
    band = value.get("band", "top")
    if not isinstance(band, str) or band not in BANDS:
        raise ValueError("Choose the top band, bottom band, or the full page.")
    auto_rotate = value.get("auto_rotate", True)
    if type(auto_rotate) is not bool:
        raise ValueError("Auto-rotate must be true or false.")
    return {"preset": preset, "pattern": pattern, "prefix": prefix, "pages_per_quiz": pages, "band": band, "auto_rotate": auto_rotate}


def labels_in(text, chosen):
    """Exact pattern matches only: no case repair, character substitution or guessing."""
    found = set()
    text = (text or "")[:MAX_TEXT]
    if chosen.get("preset") == "custom":
        # A custom expression can backtrack indefinitely even on bounded OCR text. Keep the same Python regexp
        # semantics in a disposable process; a timeout must release the shared Inbox worker for other queues.
        try:
            result = subprocess.run([sys.executable, "-I", "-B", "-c", PATTERN_WORKER], input=json.dumps([chosen["pattern"], text]),
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env={"PATH": os.defpath}, timeout=PATTERN_SECONDS, check=True)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
            raise ValueError("The custom label pattern exceeded its matching limit. Simplify it before sorting again.") from error
        matches = json.loads(result.stdout)
    else:
        matches = (match.group(1) for match in re.finditer(chosen["pattern"], text, re.IGNORECASE))
    for match in matches:
        label = (match or "").strip().upper()
        if label and re.fullmatch(r"[A-Z0-9][A-Z0-9 .\-]{0,19}", label):
            found.add(label)
    return found


def decide(reads, chosen):
    """One page's outcome from its band readings."""
    labels = set()
    for read in reads:
        labels |= labels_in(read.get("text"), chosen)
    if not labels:
        return "none", None
    if len(labels) > 1:
        return "conflict", None
    return "label", labels.pop()


def label_rotation(reads, label, chosen):
    """The rotation whose reading showed the label; None when unclear (none or several)."""
    rotations = {read.get("rotation") for read in reads if label in labels_in(read.get("text"), chosen)}
    return rotations.pop() if len(rotations) == 1 and None not in rotations else None


def text_orientation(reads):
    """Which rotation read clearly more text; None unless one side is decisively more readable."""
    counts = {}
    for read in reads:
        text = read.get("text") or ""
        counts[read.get("rotation")] = counts.get(read.get("rotation"), 0) + sum(1 for char in text if char.isalnum())
    upright, inverted = counts.get(0, 0), counts.get(180, 0)
    best, other = max(upright, inverted), min(upright, inverted)
    if best < MIN_ORIENTATION_TEXT or best < ORIENTATION_RATIO * other:
        return None
    return 0 if upright >= inverted else 180


def assign(pages, pages_per_quiz, auto_rotate=True):
    """Attach unlabeled sides to the labeled page before them, in scan order, and decide orientation.

    A labeled page starts a quiz; the next `pages_per_quiz - 1` unlabeled pages
    (including blank backs) belong to it. Anything else stays unsorted. A sheet
    fed upside down shows its label in the 180-degree reading, so that page and
    the sides that follow it are turned; a page with no front to follow is turned
    only when one rotation read decisively more text.
    """
    current, remaining, current_rotation = None, 0, 0
    for page in pages:
        text_rotation = page.get("text_rotation")
        if page["outcome"] == "label":
            current, remaining = page["label"], pages_per_quiz - 1
            current_rotation = page.get("label_rotation") or 0
            page.update(assigned=current, reason="Printed label read on this page.", rotation=current_rotation,
                        orientation="label" if page.get("label_rotation") is not None else "unknown")
        elif page["outcome"] == "conflict":
            current, remaining = None, 0
            page.update(assigned=None, reason="Different labels were read on this page; check it by hand.",
                        rotation=text_rotation or 0, orientation="text" if text_rotation is not None else "unknown")
        elif current and remaining > 0:
            remaining -= 1
            page.update(assigned=current, reason="Blank side following the labeled page." if page["outcome"] == "blank"
                        else "No label read; follows the labeled page before it.", rotation=current_rotation, orientation="quiz")
        else:
            page.update(assigned=None, reason="No label read and no labeled page precedes it within the pages per quiz.",
                        rotation=text_rotation or 0, orientation="text" if text_rotation is not None else "unknown")
        if not auto_rotate:
            page.update(rotation=0, orientation="off")
    return pages


def output_name(label):
    key = re.sub(r"[^A-Z0-9]+", "-", label.upper()).strip("-") or "LABEL"
    return f"version-{key}"


class VersionSortService:
    def __init__(self, scans):
        self.scans = scans
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path

    @staticmethod
    def runtime_available():
        return faculty_ocr.available()

    def _source(self, db, message_id):
        message = self.inbox.message(db, message_id)
        job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
        if not message["authenticated"] or not job or job["action"] != "scan.receive":
            raise Forbidden("Version sorting requires authenticated scanner intake.")
        return message, job

    def _pages(self, db, message_id):
        return [dict(r) for r in db.execute(
            "SELECT id,status,source_artifact_id,source_page_index,page_artifact_id,content_sha256 FROM scan_pages "
            "WHERE message_id=? ORDER BY source_artifact_id,source_page_index", (message_id,))]

    # Requests -----------------------------------------------------------

    def request(self, message_id, value, actor="workspace"):
        chosen = settings(value)
        if not self.runtime_available():
            raise Conflict("Local OCR is not installed on this server, so version sorting is unavailable.")
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            message, job = self._source(db, message_id)
            decode = db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
            pages = self._pages(db, message_id)
            if not decode or decode["status"] != "complete" or not pages:
                raise Conflict("Inspect the retained pages before sorting them by version.")
            if db.execute("SELECT 1 FROM scan_version_sorts WHERE message_id=? AND status IN ('pending','running')", (message_id,)).fetchone():
                raise Conflict("This scan is already queued for version sorting. Wait for it to finish before requesting another.")
            live = db.execute("SELECT COUNT(*) FROM scan_version_sorts WHERE status IN ('pending','running')").fetchone()[0]
            if live >= MAX_QUEUED:
                raise Conflict(f"The version sorting queue is full ({MAX_QUEUED} scans waiting). Try again after the current jobs finish.")
            attempt = db.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM scan_version_sorts WHERE message_id=?", (message_id,)).fetchone()[0]
            now = timestamp()
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO scan_version_sorts(message_id,attempt,status,settings_json,actor,pages_total,created_at,updated_at) VALUES(?,?,'pending',?,?,?,?,?)",
                       (message_id, attempt, encoded(chosen), actor, len(pages), now, now))
            for page in pages:
                blank = page["status"] == "blank"
                db.execute("INSERT INTO scan_version_reads(message_id,attempt,scan_page_id,status,outcome,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (message_id, attempt, page["id"], "complete" if blank else "pending", "blank" if blank else "", now, now))
            self.inbox.event(db, job["id"], "version_sort_requested", encoded({"actor": actor, "attempt": attempt, "pages": len(pages),
                "settings": chosen, "note": "OCR readings are drafts for sorting only; instructor review remains required."}))
        self.inbox.wake.set()
        return self.summary(message_id)

    def retry(self, message_id, actor="workspace"):
        """Resume the latest failed attempt: finished readings are kept, failed ones are re-read."""
        if not self.runtime_available():
            raise Conflict("Local OCR is not installed on this server, so version sorting is unavailable.")
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            _, job = self._source(db, message_id)
            latest = db.execute("SELECT * FROM scan_version_sorts WHERE message_id=? ORDER BY attempt DESC LIMIT 1", (message_id,)).fetchone()
            if not latest or latest["status"] != "failed":
                raise Conflict("Only a failed version sort can be retried.")
            now = timestamp()
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE scan_version_reads SET status='pending',error='',updated_at=? WHERE message_id=? AND attempt=? AND status='failed'",
                       (now, message_id, latest["attempt"]))
            db.execute("UPDATE scan_version_sorts SET status='pending',error='',updated_at=? WHERE message_id=? AND attempt=? AND status='failed'",
                       (now, message_id, latest["attempt"]))
            self.inbox.event(db, job["id"], "version_sort_retry", f"{actor} resumed version sorting attempt {latest['attempt']}; instructor review remains required.")
        self.inbox.wake.set()
        return self.summary(message_id)

    def summary(self, message_id):
        """Latest attempt, its progress and queue position, for the review screen."""
        with connect(self.path) as db:
            latest = db.execute("SELECT * FROM scan_version_sorts WHERE message_id=? ORDER BY attempt DESC LIMIT 1", (message_id,)).fetchone()
            live = db.execute("SELECT COUNT(*) FROM scan_version_sorts WHERE status IN ('pending','running')").fetchone()[0]
            result = {"latest": None, "queue": {"live": live, "capacity": MAX_QUEUED, "position": None},
                      "presets": [{"key": key, "label": preset["label"]} for key, preset in PRESETS.items()],
                      "bands": [{"key": key, "label": label} for key, label in BANDS.items()],
                      "max_pages_per_quiz": MAX_PAGES_PER_QUIZ, "runtime_available": self.runtime_available()}
            if latest:
                latest = dict(latest)
                latest["settings"] = json.loads(latest.pop("settings_json"))
                latest["result"] = json.loads(latest.pop("result_json"))
                counts = {row[0]: row[1] for row in db.execute(
                    "SELECT status,COUNT(*) FROM scan_version_reads WHERE message_id=? AND attempt=? GROUP BY status",
                    (message_id, latest["attempt"]))}
                latest["reads"] = {"complete": counts.get("complete", 0), "pending": counts.get("pending", 0),
                                   "running": counts.get("running", 0), "failed": counts.get("failed", 0)}
                if latest["status"] in ("pending", "running"):
                    ahead = db.execute("SELECT COUNT(*) FROM scan_version_sorts WHERE status IN ('pending','running') AND (created_at<? OR (created_at=? AND message_id<?))",
                                       (latest["created_at"], latest["created_at"], message_id)).fetchone()[0]
                    result["queue"]["position"] = ahead + 1
                result["latest"] = latest
            result["can_request"] = result["runtime_available"] and live < MAX_QUEUED and not (latest and latest["status"] in ("pending", "running"))
            result["can_retry"] = result["runtime_available"] and bool(latest and latest["status"] == "failed")
        return result

    # Worker -------------------------------------------------------------

    def process_one(self):
        """One page reading (or one finalization) per worker turn, under the shared execution lock."""
        with self.inbox.worker_lock():
            with self.rosters.lock(), ExitStack() as locks:
                with connect(self.path) as db:
                    owners = [r[0] for r in db.execute("SELECT DISTINCT message_id FROM scan_version_sorts WHERE status IN ('pending','running') ORDER BY message_id")]
                for owner in owners:
                    locks.enter_context(self.scans.documents.files.owner_lock(owner))
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    now = timestamp()
                    for row in db.execute("SELECT DISTINCT message_id,attempt FROM scan_version_reads WHERE status='running'").fetchall():
                        # Interrupted mid-page: keep finished readings, require an explicit retry for the rest.
                        db.execute("UPDATE scan_version_reads SET status='failed',error='Sorting worker interrupted; explicit retry required.',updated_at=? WHERE message_id=? AND attempt=? AND status='running'",
                                   (now, row["message_id"], row["attempt"]))
                        db.execute("UPDATE scan_version_sorts SET status='failed',error='Sorting was interrupted. Retry to continue with the remaining pages.',updated_at=? WHERE message_id=? AND attempt=? AND status IN ('pending','running')",
                                   (now, row["message_id"], row["attempt"]))
                        job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (row["message_id"],)).fetchone()
                        self.inbox.event(db, job["id"], "version_sort_failed", "Sorting was interrupted. Retry to continue with the remaining pages.")
                    sort = db.execute("SELECT * FROM scan_version_sorts WHERE status IN ('pending','running') ORDER BY created_at,message_id LIMIT 1").fetchone()
                    if not sort:
                        return False
                    sort = dict(sort)
                    read = db.execute("""SELECT r.scan_page_id FROM scan_version_reads r JOIN scan_pages sp ON sp.id=r.scan_page_id
                        WHERE r.message_id=? AND r.attempt=? AND r.status='pending' ORDER BY sp.source_artifact_id,sp.source_page_index LIMIT 1""",
                                      (sort["message_id"], sort["attempt"])).fetchone()
                    scan_id = read["scan_page_id"] if read else None
                    if scan_id:
                        db.execute("UPDATE scan_version_reads SET status='running',updated_at=? WHERE message_id=? AND attempt=? AND scan_page_id=?",
                                   (now, sort["message_id"], sort["attempt"], scan_id))
                    db.execute("UPDATE scan_version_sorts SET status='running',updated_at=? WHERE message_id=? AND attempt=? AND status='pending'",
                               (now, sort["message_id"], sort["attempt"]))
            if scan_id:
                try:
                    self._read(sort, scan_id)
                except OcrError as error:
                    if BUSY in str(error):
                        # Another explicit OCR call holds the model; leave the page queued and back off.
                        self._release(sort, scan_id)
                        return False
                    self._fail(sort, "Reading the printed label failed or exceeded its limits. Retry to continue with the remaining pages.", scan_id)
                except Exception:
                    self._fail(sort, "Reading the printed label failed or exceeded its limits. Retry to continue with the remaining pages.", scan_id)
                return True
            try:
                self._finalize(sort)
            except Exception:
                self._fail(sort, "Building the sorted PDFs failed. Retry, or request a new sort if the failure repeats.")
            return True

    def _release(self, sort, scan_id):
        with self.rosters.lock(), self.scans.documents.files.owner_lock(sort["message_id"]), connect(self.path) as db:
            db.execute("UPDATE scan_version_reads SET status='pending',updated_at=? WHERE message_id=? AND attempt=? AND scan_page_id=? AND status='running'",
                       (timestamp(), sort["message_id"], sort["attempt"], scan_id))

    def _fail(self, sort, error, scan_id=None):
        with self.rosters.lock(), self.scans.documents.files.owner_lock(sort["message_id"]), connect(self.path) as db:
            now = timestamp()
            db.execute("BEGIN IMMEDIATE")
            if scan_id:
                db.execute("UPDATE scan_version_reads SET status='failed',error=?,updated_at=? WHERE message_id=? AND attempt=? AND scan_page_id=? AND status='running'",
                           (error, now, sort["message_id"], sort["attempt"], scan_id))
            db.execute("UPDATE scan_version_sorts SET status='failed',error=?,updated_at=? WHERE message_id=? AND attempt=? AND status='running'",
                       (error, now, sort["message_id"], sort["attempt"]))
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (sort["message_id"],)).fetchone()
            self.inbox.event(db, job["id"], "version_sort_failed", error)

    def _read(self, sort, scan_id):
        message_id, chosen = sort["message_id"], json.loads(sort["settings_json"])
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            self._source(db, message_id)
            page = dict(db.execute("SELECT * FROM scan_pages WHERE id=?", (scan_id,)).fetchone())
        _, content = self.inbox.read_artifact(page["page_artifact_id"])
        if sha(content) != page["content_sha256"]:
            raise ValueError("Retained page checksum differs.")
        result = recognize_bands(content, band=chosen["band"])
        if result["source_sha256"] != page["content_sha256"]:
            raise ValueError("OCR source checksum differs.")
        outcome, label = decide(result["reads"], chosen)
        reading = {"reads": [{"rotation": read.get("rotation"), "band": read.get("band"), "text": (read.get("text") or "")[:MAX_TEXT],
                              "truncated": bool(read.get("truncated"))} for read in result["reads"]],
                   "model_id": result.get("model_id"), "revision": result.get("revision"), "device": result.get("device"),
                   "seconds": result.get("seconds"), "crop_profile": result.get("crop_profile"), "source_sha256": result["source_sha256"]}
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("UPDATE scan_version_reads SET status='complete',outcome=?,label=?,reading_json=?,error='',updated_at=? WHERE message_id=? AND attempt=? AND scan_page_id=? AND status='running'",
                              (outcome, label, encoded(reading), timestamp(), message_id, sort["attempt"], scan_id)).rowcount:
                raise ValueError("The page reading is no longer running.")
            remaining = db.execute("SELECT COUNT(*) FROM scan_version_reads WHERE message_id=? AND attempt=? AND status!='complete'",
                                   (message_id, sort["attempt"])).fetchone()[0]
        if not remaining:
            self._finalize(sort)

    def _finalize(self, sort):
        """Assign pages, merge the retained single-page PDFs, and commit outputs with the completion."""
        message_id, attempt, chosen = sort["message_id"], sort["attempt"], json.loads(sort["settings_json"])
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            message, job = self._source(db, message_id)
            current = db.execute("SELECT status FROM scan_version_sorts WHERE message_id=? AND attempt=?", (message_id, attempt)).fetchone()
            if not current or current["status"] != "running":
                raise ValueError("The version sort is no longer running.")
            reads = [dict(r) for r in db.execute("""SELECT r.*,sp.source_artifact_id,sp.source_page_index,sp.page_artifact_id,sp.content_sha256
                FROM scan_version_reads r JOIN scan_pages sp ON sp.id=r.scan_page_id WHERE r.message_id=? AND r.attempt=?
                ORDER BY sp.source_artifact_id,sp.source_page_index""", (message_id, attempt))]
        if not reads or any(r["status"] != "complete" for r in reads):
            raise ValueError("Every page must be read before sorting.")
        pages = []
        for r in reads:
            page_reads = json.loads(r["reading_json"]).get("reads", [])
            pages.append({"scan_page_id": r["scan_page_id"], "attachment_page": r["source_page_index"], "source_artifact_id": r["source_artifact_id"],
                          "page_artifact_id": r["page_artifact_id"], "content_sha256": r["content_sha256"], "outcome": r["outcome"],
                          "label": r["label"], "reads": page_reads,
                          "label_rotation": label_rotation(page_reads, r["label"], chosen) if r["outcome"] == "label" else None,
                          "text_rotation": text_orientation(page_reads) if r["outcome"] != "blank" else None})
        pages = assign(pages, chosen["pages_per_quiz"], chosen.get("auto_rotate", True))
        groups, unsorted = {}, []
        for page in pages:
            (groups.setdefault(page["assigned"], []) if page["assigned"] else unsorted).append(page)
        ordered = sorted(groups)
        # Merge through the bounded parser using the retained, checksum-verified single-page PDFs.
        outputs, files, names = [], {}, set()
        for key in ordered + ([None] if unsorted else []):
            members = groups[key] if key else unsorted
            contents = []
            for page in members:
                _, content = self.inbox.read_artifact(page["page_artifact_id"])
                if sha(content) != page["content_sha256"]:
                    raise ValueError("Retained page checksum differs.")
                contents.append((content, page["rotation"]))
            pdf = process.merge_pages(contents)
            name = output_name(key) if key else "unsorted"
            base, number = name, 2
            while name in names:
                name = f"{base}-{number}"
                number += 1
            names.add(name)
            artifact_id = str(uuid.uuid5(uuid.UUID(message_id), f"version-sort:{attempt}:{name}"))
            files[artifact_id] = (f"version-sort/{attempt}/{name}.pdf", f"sorted-{name}-attempt-{attempt}.pdf", pdf)
            outputs.append({"key": key, "label": f"{chosen['prefix']}{key}" if key else "Unsorted", "artifact_id": artifact_id,
                            "filename": files[artifact_id][1], "pages": len(members),
                            "labeled_pages": sum(1 for page in members if page["outcome"] == "label"),
                            "rotated_pages": sum(1 for page in members if page["rotation"])})
        report_id = str(uuid.uuid5(uuid.UUID(message_id), f"version-sort:{attempt}:report"))
        report = {"message_id": message_id, "attempt": attempt, "settings": chosen, "created_at": timestamp(),
                  "pages": [{key: page[key] for key in ("scan_page_id", "attachment_page", "source_artifact_id", "content_sha256",
                                                       "outcome", "label", "assigned", "reason", "rotation", "orientation", "reads")} for page in pages],
                  "outputs": outputs, "instructor_review_required": bool(message["instructor_review_required"]),
                  "note": "OCR readings are drafts. The sorted PDFs are working copies; no page identity, review gate or action changed."}
        payload = encoded(report).encode()
        files[report_id] = (f"version-sort/{attempt}/report.json", f"version-sort-report-attempt-{attempt}.json", payload)
        summary = {"outputs": outputs, "report_artifact_id": report_id, "unsorted_pages": len(unsorted),
                   "rotated_pages": sum(1 for page in pages if page["rotation"]),
                   "pages": [{key: page[key] for key in ("scan_page_id", "attachment_page", "outcome", "label", "assigned", "reason", "rotation", "orientation")} for page in pages]}
        with self.rosters.lock():
            with self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
                message, job = self._source(db, message_id)
                current = db.execute("SELECT status FROM scan_version_sorts WHERE message_id=? AND attempt=?", (message_id, attempt)).fetchone()
                if not current or current["status"] != "running":
                    raise ValueError("The version sort is no longer running.")
            # The verified files context takes this owner's lock itself; keep only the lifecycle lock here.
            with self.inbox.files(message, write=True) as root:
                for relative, _, content in files.values():
                    publish(root / relative, content)
                with connect(self.path) as db:
                    now = timestamp()
                    db.execute("BEGIN IMMEDIATE")
                    for artifact_id, (relative, filename, content) in files.items():
                        db.execute("INSERT INTO inbox_artifacts VALUES(?,?,?,?,?,?,?,?,?)",
                                   (artifact_id, message_id, job["id"], filename, relative, "output", len(content), sha(content), now))
                    if not db.execute("UPDATE scan_version_sorts SET status='complete',result_json=?,error='',updated_at=? WHERE message_id=? AND attempt=? AND status='running'",
                                      (encoded(summary), now, message_id, attempt)).rowcount:
                        raise ValueError("The version sort is no longer running.")
                    self.inbox.event(db, job["id"], "version_sort_complete", encoded({"attempt": attempt, "report_artifact_id": report_id,
                        "outputs": [{"label": o["label"], "pages": o["pages"], "artifact_id": o["artifact_id"]} for o in outputs],
                        "unsorted_pages": len(unsorted), "rotated_pages": summary["rotated_pages"],
                        "instructor_review_required": bool(message["instructor_review_required"])}))

