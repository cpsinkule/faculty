"""A scan holding more than one assignment's papers is separated into one scan per assignment (INBOX "SEPARATING A SCAN").

Requirement, 2026-11-10, of five Quiz 7 makeups and one Quiz 6 makeup scanned in one stack and held at "The labels name more than one
assignment": "faculty needs the ability to separate scans into its various assignments (if there are more than one)". A scan is filed
under ONE assignment, and everything after that (its review, its grouping, grading, the Gradescope send) reads it so; such a scan could
neither be filed nor pass its review. Here the worker makes, for each assignment, a NEW scan holding that assignment's pages, inspected
afresh like any arrival, and moves the scan itself to Deleted with the ordinary reversible tombstone, as Combine and Start over
replace a scan (`InboxService.combine_scans`, `start_over`). Nothing downstream changes: each new scan holds one assignment and goes
on by itself.

WHOSE PAGE IS WHOSE (`plan`), BY LABELS. A page belongs to the assignment (and term) its own label names: a current label of a print
job, or the Scantron label of a bubble sheet. A page without one stays with the labeled page before it in its file (a paper labeled
on page 1 only, a blank back, a sheet's back), and the pages before a file's first label go with that label.

BY PRINTED TITLES (the same day, of five DR copies, three of Quiz 7 and two of Quiz 6, scanned in one stack with no label on any
page: "can you use it on the scans that got held up in the inbox"). Only a scan with no label at all, filed under nothing, holding no
bubble sheet, whose first pages the assignment finder read as more than one assignment (`AssignmentFinder`, its `assignment_found`
event). The worker reads the top of EVERY written page (`faculty_ocr.recognize_covers`, the finder's own band and words, `READ` pages
a turn, each batch a `scan_titles_read` event). A FIRST page is one whose top prints both field words, Name and UIN; a first page
that prints a title line only ONE current assignment's blank prints is that assignment's, and the pages after it are its paper. Titles propose and the page counts decide, as the instructor asked of Jev's
proposals (2026-10-26, "if that assignment fits (ie the page counts line up)"): every file must start with a titled page and every
titled page must be followed by exactly its assignment's sides per paper (`UnlabeledService.paper_sides`), or nothing is separated.
Each new scan is then filed under its assignment by the ordinary automatic association once it is inspected (`apply`,
`GradingService.associate_found`), which reads its first pages for the students as for any scan found without labels.

NOT SEPARATED BY ITSELF, and held for the instructor as before, each with its reason on the scan: a scan that already passed its
review; one separated before (restored from Deleted); one whose pages were grouped, identified, read or set aside by hand, whose
papers continue in another scan, or that was read for first pages, sorted or read as bubble sheets (a new scan starts without that
work); a file with no label at all; a bubble sheet with no label of its own lying between two assignments' papers; and, by titles,
pages that do not add up to whole papers. What the worker did by itself (automatic grouping, photocopied stamps, names read) is done
again on the new scans and holds nothing back.

ORDER, so that no page is ever in two scans still in the Inbox: every new PDF is made and checked first (the scan's retained
single-page PDFs joined in the sandbox, nothing rendered or re-encoded, `process.merge_pages`); then one transaction records the
plan (`scan_separating` on the scan) and tombstones it; then each part is submitted under its own idempotency key
(`separate:<message>:<n>`) with a `scan_separated_from` event; then `scan_separated` on the scan names the new ones. A worker that
stops in between finishes from the recorded plan on its next turn (`_unfinished`). A part that cannot be made (a processing limit)
is recorded once (`scan_separation_failed`) and the scan stays held with that reason; nothing was changed.
"""
import json
import logging
import re
import tempfile
import time
from pathlib import Path

from werkzeug.exceptions import Conflict, Forbidden, NotFound

import faculty_ocr
from faculty_ocr import BUSY, OcrError
from rosters import connect
from teach_inbox.protocol import validate_files
from teach_inbox.service import PRESENT, encoded, sha
from . import process
from .sequence import HELD_PAPER

LOG = logging.getLogger(__name__)
RULE = "separate-by-assignment-v1"
TITLE_RULE = "separate-by-title-v1"
TITLE_BAND = [.12, .17, .88, .36]  # the top of a page as the assignment finder reads it for its printed title
COVER_WORDS = ("name", "uin")      # the field words a first page prints at its top (the first-page reader's own two)
ADVICE = "Scan each assignment's papers on their own."


def listed(titles):
    return titles[0] if len(titles) == 1 else f"{', '.join(titles[:-1])} and {titles[-1]}"


class SeparationService:
    EVERY = 20  # seconds between looks at one scan still waiting for its review
    RETRY = 60  # seconds before an interrupted separation is tried again after a failure
    READ = 8    # pages whose printed title is read in one turn (one model load, as the finder reads)

    def __init__(self, scans):
        self.scans, self.documents = scans, scans.documents
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path
        self._seen, self._retry = {}, {}

    # ----- reading ------------------------------------------------------------------------

    # The assignment and term a current print job's label names, as `ScanService.recognition` counts current paper.
    SCOPES = """SELECT DISTINCT v.assignment_id,b.term_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                JOIN printed_copies c ON c.id=p.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE sp.message_id=? AND b.status='ready' AND b.superseded_at IS NULL"""

    def mixed(self, db, message_id):
        """Whether the scan's current labels name more than one assignment (or one assignment in two terms)."""
        return len(db.execute(self.SCOPES, (message_id,)).fetchall()) > 1

    BY_HAND = ("its pages were already grouped, identified, read or set aside by hand, and a new scan would start without that work",
               "Start it over to separate it without that work, or scan each assignment's papers on their own.")

    def _kept_back(self, db, message_id, job_id):
        """(reason, advice) when something recorded on the scan would be lost, or left behind, by new scans of its pages; else None."""
        values = {"m": message_id, "j": job_id, "held": HELD_PAPER}
        if db.execute("SELECT 1 FROM assembly_pages ap JOIN scan_pages sp ON sp.id=ap.scan_page_id WHERE sp.message_id=:m LIMIT 1", values).fetchone():
            return "pages of it are already in a reviewed paper", ADVICE
        # A paper that starts in one stack and ends in the next (INBOX "BATCHES SCANNED IN STACKS") belongs to both scans.
        if db.execute("""SELECT EXISTS(SELECT 1 FROM scan_packets k JOIN scan_packet_pages q ON q.packet_id=k.id JOIN scan_pages sp ON sp.id=q.scan_page_id
                                       WHERE k.message_id=:m AND sp.message_id!=:m)
                             OR EXISTS(SELECT 1 FROM scan_pages sp JOIN scan_packet_pages q ON q.scan_page_id=sp.id JOIN scan_packets k ON k.id=q.packet_id
                                       WHERE sp.message_id=:m AND k.message_id!=:m)""", values).fetchone()[0]:
            return "one of its papers continues in another scan", ADVICE
        by_hand = db.execute("""SELECT
            EXISTS(SELECT 1 FROM scan_sequence_runs r WHERE r.message_id=:m AND (r.actor!='automatic' OR r.undone_at IS NOT NULL
                   OR COALESCE(json_extract(r.summary_json,'$.origin'),'')=:held))
            OR EXISTS(SELECT 1 FROM scan_unlabeled_runs WHERE message_id=:m)
            OR EXISTS(SELECT 1 FROM scan_version_sorts WHERE message_id=:m)
            OR EXISTS(SELECT 1 FROM scan_pages WHERE message_id=:m AND status='unreadable' AND review_state='ignored')
            OR EXISTS(SELECT 1 FROM inbox_events WHERE job_id=:j AND kind='manual_page_recovery')""", values).fetchone()[0]
        if not by_hand and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bubble_runs'").fetchone():
            by_hand = db.execute("SELECT 1 FROM bubble_runs WHERE message_id=? LIMIT 1", (message_id,)).fetchone()
        return self.BY_HAND if by_hand else None

    def plan(self, db, message_id):
        """How the scan would be separated, or None when nothing says it holds more than one assignment.

        {mode, titles, parts, digest, reason, advice, waiting, reading}: `mode` is `labels` or `titles`; `parts` are [{assignment_id,
        term_id, title, pages, files: [{artifact_id, filename, source_filename, pages: [source page numbers]}]}] in the order each
        assignment first appears; `reason` says why the scan is not separated by itself (then nothing is done and the instructor
        is told, with `advice`); `waiting` that the worker is still reading its labels; `reading` ({done, total, unread}, by titles
        only) that printed titles are still to be read, with no parts yet. Derived only: nothing is written here."""
        row = db.execute(f"""SELECT m.authenticated,m.instructor_review_required required,j.id job_id,j.action,j.status,j.attempts,d.status decode,
                {PRESENT.format('m.id')} present FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id
                LEFT JOIN document_decode_jobs d ON d.message_id=m.id WHERE m.id=?""", (message_id,)).fetchone()
        if not row or row["action"] != "scan.receive" or not row["authenticated"] or row["decode"] != "complete":
            return None
        detector = self.scans.bubble_detector
        sheets = detector.sheet_pages(db, message_id) if detector else set()
        mode, problem, reading = "labels", None, None
        if self.mixed(db, message_id):
            pages = self._labeled(db, message_id)
        else:
            mode, titled = "titles", self._titled(db, message_id, row["job_id"], sheets)
            if not titled:
                return None
            pages, problem, reading = titled["pages"], titled["problem"], titled["reading"]
        reason, advice = None, ADVICE
        failed = db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind='scan_separation_failed' ORDER BY id DESC LIMIT 1", (row["job_id"],)).fetchone()
        failed = json.loads(failed[0]) if failed else {}
        if db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_separating' LIMIT 1", (row["job_id"],)).fetchone():
            reason, advice = "it was separated before, and the new scans hold its pages", "Delete this scan again, or those." if row["present"] else ""
        elif not row["present"]:
            reason, advice = "it is deleted from the Inbox", ""
        elif not row["required"] or row["status"] != "needs_review" or row["attempts"]:
            reason = "it already passed its review"
        elif kept := self._kept_back(db, message_id, row["job_id"]):
            reason, advice = kept
        waiting = bool(db.execute("SELECT 1 FROM scan_alias_ocr WHERE message_id=? AND status IN ('pending','running') LIMIT 1", (message_id,)).fetchone()
                       or (detector and detector._counts(db, message_id)["pending"]))
        if reading:
            # Titles still to be read: nothing to show for a scan that would be left alone anyway.
            return None if reason else {"mode": mode, "titles": [], "parts": [], "digest": None, "reason": None, "advice": advice,
                                        "waiting": waiting, "reading": reading, "job_id": row["job_id"]}
        files = {}
        for page in pages:
            files.setdefault(page["source_artifact_id"], []).append(page)
        many = len(files) > 1
        where = lambda number, page: f"page {page['source_page_index']}{f' of file {number}' if many else ''}"
        parts, unlabeled_files, between = {}, [], []
        for number, (artifact, rows) in enumerate(files.items(), 1):
            if not any(page["scope"] for page in rows):
                unlabeled_files.append(number)
                continue
            # The nearest labeled page before each page of the file, and the nearest after it.
            before, after, near = [], [None] * len(rows), None
            for page in rows:
                near = page["scope"] or near
                before.append(near)
            near = None
            for index in range(len(rows) - 1, -1, -1):
                near = rows[index]["scope"] or near
                after[index] = near
            for index, page in enumerate(rows):
                if not page["scope"] and page["id"] in sheets and before[index] and after[index] and before[index] != after[index]:
                    between.append(where(number, page))
                part = parts.setdefault(page["scope"] or before[index] or after[index], {})
                part.setdefault(artifact, {"number": number, "source_filename": rows[0]["filename"], "pages": []})["pages"].append(page["source_page_index"])
        names = self._names(db, list(parts))
        result = []
        for scope, sources in parts.items():
            entries = []
            for artifact, source in sources.items():
                # Several files keep their order in the new scan, which reads its files by name.
                stem = ("%02d " % source["number"] if many else "") + (Path(source["source_filename"]).stem[:120] or "scan")
                entries.append({"artifact_id": artifact, "source_filename": source["source_filename"], "pages": source["pages"],
                                "filename": f"{stem} ({names[scope]['file']}).pdf"})
            result.append({"assignment_id": scope[0], "term_id": scope[1], "title": names[scope]["title"], "files": entries,
                           "pages": sum(len(entry["pages"]) for entry in entries)})
        digest = sha(encoded([RULE if mode == "labels" else TITLE_RULE,
                              [[part["assignment_id"], part["term_id"], [[entry["artifact_id"], entry["pages"]] for entry in part["files"]]] for part in result]]).encode())
        if reason:
            pass
        elif problem:
            reason = problem
        elif unlabeled_files:
            reason = f"file {listed([str(number) for number in unlabeled_files])} carries no label, so nothing says whose pages it holds"
        elif between:
            reason = (f"the bubble sheet on {between[0]} carries no label of its own and lies between two assignments' papers" if len(between) == 1 else
                      f"{len(between)} bubble sheets carry no label of their own and lie between two assignments' papers ({listed(between[:6])})")
        elif failed.get("digest") == digest:
            reason = f"the new scans could not be made ({failed.get('reason', '').rstrip('.')})"
        return {"mode": mode, "titles": [part["title"] for part in result], "parts": result, "digest": digest, "reason": reason, "advice": advice,
                "waiting": waiting, "reading": None, "job_id": row["job_id"]}

    @staticmethod
    def _labeled(db, message_id):
        """The scan's pages in the order its files are read, each with the `scope` (assignment, term) its own label names, or None."""
        pages = [dict(page) for page in db.execute(
            """SELECT sp.id,sp.source_artifact_id,sp.source_page_index,sp.status,sp.page_stamp_id,sp.candidates_json,f.filename,
                      v.assignment_id,b.term_id,b.status batch_status,b.superseded_at
               FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
               LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id LEFT JOIN printed_copies c ON c.id=p.printed_copy_id
               LEFT JOIN print_batches b ON b.id=c.print_batch_id LEFT JOIN assignment_versions v ON v.id=b.assignment_version_id
               WHERE sp.message_id=? ORDER BY f.filename,sp.source_artifact_id,sp.source_page_index""", (message_id,))]
        for page in pages:
            page["scope"] = None
            if page["page_stamp_id"]:
                if page["batch_status"] == "ready" and not page["superseded_at"]:
                    page["scope"] = (page["assignment_id"], page["term_id"])
            elif page["status"] == "unreadable" and page["candidates_json"] not in ("", "[]"):
                # A bubble sheet's Scantron label (DOCUMENTS "SCANTRON LABELS") is no label of a paper, but it says whose sheet it is.
                for candidate in json.loads(page["candidates_json"]):
                    label = db.execute("""SELECT v.assignment_id,b.term_id FROM sheet_labels l JOIN printed_copies c ON c.id=l.printed_copy_id
                        JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                        WHERE l.token=? AND b.status='ready' AND b.superseded_at IS NULL""", (candidate.get("payload"),)).fetchone()
                    if label:
                        page["scope"] = (label[0], label[1])
                        break
        return pages

    # ----- a scan without labels: the titles printed on its first pages ---------------------------------------------------

    @staticmethod
    def _several(found):
        """Whether the assignment finder read the scan's first pages as more than one assignment (by its templates, or by Jev)."""
        papers = (found or {}).get("papers") or {}
        if (found or {}).get("chosen") or not papers:
            return False
        return bool(papers.get("conflict")) or len({vote.get("assignment_id") for vote in (papers.get("jev") or {}).get("votes", []) if vote.get("assignment_id")}) > 1

    @staticmethod
    def _readings(db, job_id):
        """{scan page: [assignments whose title it prints]} from the scan's `scan_titles_read` events."""
        readings = {}
        for (detail,) in db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind='scan_titles_read' ORDER BY id", (job_id,)):
            detail = json.loads(detail)
            if detail.get("rule") == TITLE_RULE:
                readings.update({page["page"]: page["titles"] for page in detail["pages"]})
        return readings

    def _titled(self, db, message_id, job_id, sheets):
        """A scan without labels as its printed titles divide it: {pages (each with its `scope`), problem, reading}, or None when it
        is not such a scan or its titles do not name more than one assignment."""
        if self.scans.grading is None or sheets:
            return None
        if db.execute("SELECT 1 FROM scan_pages WHERE message_id=? AND page_stamp_id IS NOT NULL LIMIT 1", (message_id,)).fetchone() \
                or db.execute("SELECT 1 FROM frq_scan_links WHERE message_id=? LIMIT 1", (message_id,)).fetchone():
            return None
        finder = self.scans.assignment_finder
        if not self._several(finder.latest(db, job_id)):
            return None
        if db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_separation_failed' AND json_extract(detail,'$.titles')=1 LIMIT 1", (job_id,)).fetchone():
            return None  # its titles could not be read
        pages = [dict(page, scope=None) for page in db.execute(
            """SELECT sp.id,sp.source_artifact_id,sp.source_page_index,sp.status,sp.page_artifact_id,sp.content_sha256,f.filename
               FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
               WHERE sp.message_id=? ORDER BY f.filename,sp.source_artifact_id,sp.source_page_index""", (message_id,))]
        written = [page for page in pages if page["status"] == "unreadable"]
        readings = self._readings(db, job_id)
        unread = [page for page in written if page["id"] not in readings]
        if unread:
            return {"pages": pages, "problem": None, "reading": {"done": len(written) - len(unread), "total": len(written), "unread": unread}}
        candidates = finder._candidates(db)
        problem, sides = None, {}
        for page in written:
            named = [aid for aid in readings[page["id"]] if aid in candidates]
            if len(named) > 1:
                problem = problem or f"page {page['source_page_index']} prints the titles of more than one assignment"
            elif named and len(candidates[named[0]]["terms"]) != 1:
                problem = problem or f"{candidates[named[0]]['title']}'s course has more than one current term"
            elif named:
                page["scope"] = (named[0], next(iter(candidates[named[0]]["terms"])))
                sides.setdefault(named[0], self.scans.unlabeled.paper_sides(db, named[0]))
        if len({page["scope"] for page in pages if page["scope"]}) < 2:
            return None
        # Titles propose, the page counts decide: every paper starts at a titled page and has its assignment's sides.
        files = {}
        for page in pages:
            files.setdefault(page["source_artifact_id"], []).append(page)
        for rows in files.values():
            starts = [index for index, page in enumerate(rows) if page["scope"]]
            for position, index in enumerate(starts):
                aid = rows[index]["scope"][0]
                length = (starts[position + 1] if position + 1 < len(starts) else len(rows)) - index
                if not sides[aid]:
                    problem = problem or f"{candidates[aid]['title']} has no blank paper to count its pages by"
                elif length != sides[aid]:
                    problem = problem or (f"its pages do not add up to whole papers: a paper of {candidates[aid]['title']} has {sides[aid]} "
                                          f"page{'' if sides[aid] == 1 else 's'}, and the one starting on page {rows[index]['source_page_index']} has {length}")
            if not starts or starts[0] != 0:
                problem = problem or f"page {rows[0]['source_page_index']} prints no assignment's title, so nothing says whose paper it starts"
        return {"pages": pages, "problem": problem, "reading": None}

    def _read_titles(self, plan):
        """Read the printed titles of the next `READ` pages and keep them as one `scan_titles_read` event. Returns whether any were
        read: a busy reader leaves them for a later turn."""
        pages = plan["reading"]["unread"][:self.READ]
        finder = self.scans.assignment_finder
        with connect(self.path) as db:
            candidates = {aid: item for aid, item in finder._candidates(db).items() if item["templates"]}
        # A title line counts for one assignment only when no other assignment's blank prints it too (as the finder counts it).
        lines = {aid: {line for template in item["templates"] for line in finder._template_titles(template)} for aid, item in candidates.items()}
        unique = {aid: {line for line in mine if not any(line in other for key, other in lines.items() if key != aid)} for aid, mine in lines.items()}
        contents = []
        for page in pages:
            _, content = self.inbox.read_artifact(page["page_artifact_id"])
            if sha(content) != page["content_sha256"]:
                raise ValueError("Retained page checksum differs.")
            contents.append(content)
        try:
            reads = faculty_ocr.recognize_covers(contents, TITLE_BAND, ["name", "UIN"], title_unless="(?s).")["pages"]  # the finder's own read
        except OcrError as error:
            if BUSY in str(error):
                return False
            # A reader that cannot read is not asked again for this scan: its assignment stays the instructor's choice, as before.
            LOG.exception("Reading the printed titles of scan pages for job %s failed", plan["job_id"])
            with self.rosters.lock(), connect(self.path) as db:
                self.inbox.event(db, plan["job_id"], "scan_separation_failed", encoded({"actor": "automatic", "titles": True, "reason": "Reading its printed titles failed."}))
            return False
        from .assignment_finder import normalized
        found = []
        for page, read in zip(pages, reads):
            # Only a first page's lines count: the top of a page that prints both field words, upright or turned. A later page's
            # heading is no title (2026-11-11: sheet 2 of a quiz, headed "Free Response", read as the one assignment whose blank
            # begins with that line).
            bands = [normalized(item.get("text") or "") for item in read["reads"] if item["kind"] == "band"]
            first = [text for text in bands if all(re.search(rf"\b{word}\b", text) for word in COVER_WORDS)]
            text = " \n ".join(first)
            found.append({"page": page["id"], "first": bool(first), "titles": sorted(aid for aid, mine in unique.items() if any(line in text for line in mine))})
        with self.rosters.lock(), connect(self.path) as db:
            self.inbox.event(db, plan["job_id"], "scan_titles_read", encoded({"rule": TITLE_RULE, "pages": found}))
        return True

    def apply(self, message_id):
        """Once a scan separated by its printed titles is inspected, file it under the assignment its first pages name, once: the
        ordinary automatic association (`GradingService.associate_found`), which then reads its first pages for the students. What
        was done, or why not, is a `scan_separation_applied` event; a scan left unfiled is the assignment finder's like any other."""
        grading = self.scans.grading
        with connect(self.path) as db:
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
            row = db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind='scan_separated_from' ORDER BY id LIMIT 1", (job["id"],)).fetchone() if job else None
            if not row or grading is None or db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_separation_applied'", (job["id"],)).fetchone():
                return False
            origin = json.loads(row["detail"])
        if origin.get("basis") != "titles":
            return False
        notes = []
        try:
            grading.associate_found(message_id, origin["assignment_id"], origin["term_id"])
        except (ValueError, Conflict, Forbidden, NotFound) as error:
            notes.append(str(error))
        with self.rosters.lock(), connect(self.path) as db:
            self.inbox.event(db, job["id"], "scan_separation_applied", encoded({"assignment_id": origin["assignment_id"], "term_id": origin["term_id"], "notes": notes}))
        return True

    @staticmethod
    def _names(db, scopes):
        """Each (assignment, term) by its title; two terms' "Exam 1" in one scan also name their term. `file` is the title as a file name carries it."""
        rows = {}
        for assignment_id, term_id in scopes:
            row = db.execute("""SELECT a.title,t.term,t.term_other,t.year FROM assignments a,course_terms t WHERE a.id=? AND t.id=?""", (assignment_id, term_id)).fetchone()
            rows[(assignment_id, term_id)] = (row["title"], f"{row['term_other'] if row['term'] == 'Other' else row['term']} {row['year']}")
        titles = [title for title, _ in rows.values()]
        names = {}
        for scope, (title, term) in rows.items():
            title = f"{title} · {term}" if titles.count(title) > 1 else title
            safe = "".join(char if char.isalnum() or char in " -_.,·'" else " " for char in title)
            names[scope] = {"title": title, "file": " ".join(safe.split())[:60] or "assignment"}
        return names

    # ----- the worker's turn ---------------------------------------------------------------

    def _candidates(self, db):
        """Inspected scans still waiting for their review, the oldest first: only such a scan is ever separated."""
        return [row[0] for row in db.execute(f"""SELECT m.id FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id
            JOIN document_decode_jobs d ON d.message_id=m.id WHERE m.instructor_review_required=1 AND m.authenticated=1
            AND j.action='scan.receive' AND j.status='needs_review' AND j.attempts=0 AND d.status='complete' AND {PRESENT.format('m.id')}
            ORDER BY m.created_at,m.id""")]

    @staticmethod
    def _unfinished(db):
        """Scans the worker tombstoned for a separation whose new scans were not all made: (job, message), the oldest first."""
        return [(row[0], row[1]) for row in db.execute("""SELECT j.id,j.message_id FROM inbox_deletions d JOIN inbox_jobs j ON j.message_id=d.message_id
            WHERE d.restored_at IS NULL AND d.actor='automatic'
              AND EXISTS(SELECT 1 FROM inbox_events e WHERE e.job_id=j.id AND e.kind='scan_separating')
              AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.job_id=j.id AND e.kind='scan_separated') ORDER BY d.id""")]

    def process_one(self):
        """Finish one interrupted separation, else separate one scan holding more than one assignment (or read its next printed titles)."""
        with self.inbox.worker_lock():
            now = time.monotonic()
            with self.rosters.lock(), connect(self.path) as db:
                unfinished, candidates = self._unfinished(db), self._candidates(db)
            for job_id, message_id in unfinished:
                if now < self._retry.get(message_id, 0):
                    continue
                try:
                    self._finish(job_id, message_id)
                    self._retry.pop(message_id, None)
                    return True
                except Exception:
                    # The scan is already in Deleted: its new scans must still be made, so this is tried again, never given up.
                    self._retry[message_id] = now + self.RETRY
                    LOG.exception("Scan %s is in Deleted but its separated scans are not all made yet; trying again", message_id)
            for gone in set(self._seen) - set(candidates):
                self._seen.pop(gone, None)
            for message_id in candidates:
                if now - self._seen.get(message_id, -self.EVERY) < self.EVERY:
                    continue
                self._seen[message_id] = now
                try:
                    done = self.separate(message_id)
                except Exception:
                    LOG.exception("Scan %s could not be separated by assignment; trying again", message_id)
                    self._seen[message_id] = now + self.RETRY
                    continue
                if done == "titles":
                    self._seen.pop(message_id, None)  # its next titles are read on the next turn
                if done:
                    return True
            return False

    def separate(self, message_id, actor="automatic"):
        """Separate one scan by assignment. Returns the new scans' job IDs, "titles" when printed titles were read instead (a scan
        without labels is read first), or None when there is nothing to do (yet)."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, message_id)
            if not plan or plan["reason"] or plan["waiting"]:
                return None
            sender = db.execute("SELECT sender FROM inbox_messages WHERE id=?", (message_id,)).fetchone()[0]
        if plan["reading"]:
            return "titles" if self._read_titles(plan) else None
        with tempfile.TemporaryDirectory(prefix="faculty-separate-") as temporary:
            # Every new PDF is made and checked before anything changes: a part that cannot be made leaves the scan as it was.
            try:
                for number, part in enumerate(plan["parts"], 1):
                    for index, file in enumerate(self._build(part)):
                        (Path(temporary) / f"{number}-{index}.pdf").write_bytes(file["content"])
            except (ValueError, Conflict) as error:
                with self.rosters.lock(), connect(self.path) as db:
                    self.inbox.event(db, plan["job_id"], "scan_separation_failed", encoded({"actor": actor, "digest": plan["digest"], "reason": str(error)}))
                return None
            record = {"actor": actor, "rule": RULE if plan["mode"] == "labels" else TITLE_RULE, "basis": plan["mode"], "digest": plan["digest"], "sender": sender, "parts": [
                {"key": f"separate:{message_id}:{number}", "assignment_id": part["assignment_id"], "term_id": part["term_id"], "title": part["title"],
                 "files": [{field: file[field] for field in ("artifact_id", "source_filename", "filename", "pages")} for file in part["files"]]}
                for number, part in enumerate(plan["parts"], 1)]}
            with self.rosters.lock(), self.documents.files.owner_lock(message_id), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                again = self.plan(db, message_id)
                if not again or again["reason"] or again["waiting"] or again["reading"] or again["digest"] != plan["digest"]:
                    return None  # it changed while its PDFs were made: looked at again on a later turn
                try:
                    self.scans.before_removal(db, message_id)
                except Conflict:
                    return None  # the worker is still reading it
                self.inbox.event(db, plan["job_id"], "scan_separating", encoded(record))
                self.inbox.tombstone(db, plan["job_id"], message_id, actor,
                                     f"Separated by assignment: its pages are in a new scan for each of {listed(plan['titles'])}."[:500])
            return self._finish(plan["job_id"], message_id, built=Path(temporary))

    def _build(self, part):
        """The part's PDFs, one per file of the scan that holds its pages: [{filename, content}], checked as an upload is."""
        files = []
        for file in part["files"]:
            with connect(self.path) as db:
                wanted = {row[0]: row[1] for row in db.execute(
                    "SELECT source_page_index,page_artifact_id FROM scan_pages WHERE source_artifact_id=?", (file["artifact_id"],))}
            # The retained single pages as inspection cut them, joined: nothing is rendered or re-encoded.
            contents = [self.inbox.read_artifact(wanted[index])[1] for index in file["pages"]]
            content = process.merge_pages(contents)
            if process.run("pages", content)["total"] != len(contents):
                raise ValueError("A separated scan did not come out with the pages it was given.")
            files.append({"filename": file["filename"], "content": content})
        validate_files(files, large_scans=True)
        return files

    def _finish(self, job_id, message_id, built=None):
        """Submit each part of the recorded plan that is not in the Inbox yet, and say on the scan what it became. `built`: the folder
        holding the PDFs `separate` just made; an interrupted separation makes them again from the scan's retained pages."""
        with connect(self.path) as db:
            record = json.loads(db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind='scan_separating' ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()[0])
        made = []
        for number, part in enumerate(record["parts"], 1):
            with connect(self.path) as db:
                existing = db.execute("SELECT j.id FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id WHERE m.dedupe_key=?",
                                      (encoded(["web", record["sender"], part["key"]]),)).fetchone()
            if existing:
                new = existing[0]
            else:
                files = ([{"filename": file["filename"], "content": (built / f"{number}-{index}.pdf").read_bytes()} for index, file in enumerate(part["files"])]
                         if built else self._build(part))
                new = self.inbox.submit({"action": "scan.receive"}, files, source="web", sender=record["sender"], idempotency_key=part["key"],
                                        instructor_review_required=True, large_scans=True)["id"]
            with self.rosters.lock(), connect(self.path) as db:
                if not db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_separated_from'", (new,)).fetchone():
                    self.inbox.event(db, new, "scan_separated_from", encoded({
                        "actor": record["actor"], "rule": record["rule"], "basis": record.get("basis", "labels"), "source": {"job_id": job_id, "message_id": message_id},
                        "assignment_id": part["assignment_id"], "term_id": part["term_id"], "title": part["title"],
                        "also": [other["title"] for other in record["parts"] if other is not part],
                        "files": [{field: file[field] for field in ("source_filename", "filename", "pages")} for file in part["files"]]}))
            made.append({"job_id": new, "title": part["title"], "pages": sum(len(file["pages"]) for file in part["files"])})
        with self.rosters.lock(), connect(self.path) as db:
            if not db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_separated'", (job_id,)).fetchone():
                self.inbox.event(db, job_id, "scan_separated", encoded({"actor": record["actor"], "rule": record["rule"], "scans": made}))
        return [item["job_id"] for item in made]
