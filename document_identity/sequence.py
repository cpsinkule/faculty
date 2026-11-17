"""Identify unlabeled scan pages from the first-page label before them.

Some exams cannot be stamped before printing, so the instructor sticks one
Data Matrix label on the first page. With the number of scanned sides per exam
(the label-to-label distance, or the instructor's entry), this reads the scan in
order: each page-1 label starts an exam, and the label's position plus the
side count must agree before anything is identified.

- When the labels are evenly spaced (every label-to-label distance identical,
  the last exam as long as the rest, nothing before the first label, no exam to
  review), a newly inspected scan is grouped by that distance without being
  asked: the instructor's standing request. Anything less even waits for the
  instructor's preview, where the count can be entered and the reasons are shown.
- The label anchors the exam, and the count is its checksum. An exam is ready
  only when the next label (or the end of the attachment) arrives after
  exactly `sides` sides, or after a whole number of further exams' worth of
  sides (a following label that was not read). Short exams, stray extra sides,
  labels that are not page 1, and unusable labels are reported, never guessed.
- Blank sides count toward `sides` but are never identified; written sides take
  page numbers 1, 2, ... in scan order. An exam whose number of written sides
  differs from most others is reported too, since a faint page read as blank
  would shift every later page number.
- Every exam that adds up becomes an exam packet (`scan_packets`): the label's
  printed copy and its scanned sides in order, for the instructor's records and
  a per-student PDF.
- Paper labeled on its first page only (a stamps-only label sheet, or a print
  job the instructor confirmed as `first_page`) takes its other pages from the
  scan, by the instructor's decision: in an exam that adds up, side n is page n.
  Blank backs keep their place and take no identity. This happens in automatic
  and instructor runs alike, and for exams already grouped when the instructor
  confirms `first_page`. A stamps-only copy was issued page 1 alone, so its
  other identities are reserved then (`symbology='sequence'`, never printed).
  The rule is the cadence: an exam with any problem infers nothing.
- The instructor's Inbox review of the scan is the approval. It turns each such
  exam into that copy's reviewed paper (`assemble_reviewed`), which the exam
  collection and grading already understand. A copy with any assembly is never
  touched; anything duplicated, conflicting or out of position is left to the
  per-copy assembly review.
- Where the copy was issued further pages and its print job still expects a
  label on every page, the instructor's own preview also identifies those
  written sides: they reference the copy's existing issued page stamps with the
  same duplicate/changed-content rules as barcode intake, and status `manual`,
  since the instructor approved the exact previewed plan. `scan_sequence_runs`
  and `scan_sequence_pages` retain which label and position identified each.
  Automatic grouping never does this; it only groups. Barcode observations,
  reviewed assemblies and the Inbox review gate are never changed. Undoing a
  run retires its packets and clears its identities for pages not yet in an
  assembly.
- A paper held because its sides do not add up (a student added a sheet) can be
  grouped BY HAND (`group_held`, requirement, 2026-10-02): the instructor ticks its
  sides in order, and the n-th ticked side becomes page n of the copy its page 1
  label names. That is the instructor's explicit choice of its sides, never an
  inference: a run of its own (origin `held_paper`, undone like any run until its
  paper is a reviewed paper), one packet, and identities with the barcode intake's
  duplicate/changed-content rules. A blank side keeps its place past the pages
  issued; a written one never does. No plan groups or holds such a paper again
  until that run is undone, and it is no grouping of the scan: automatic grouping
  of the papers that add up still follows (`auto_group`). A copy that already has a
  reviewed paper is changed on Review instead.
  A page the instructor SET ASIDE as part of no paper (`ScanService.set_aside`,
  review state `ignored`) is never given a place in a paper: a stretch of pages
  in no paper made only of such pages (and blank sides) is no stretch at all, and
  a paper that would take one is held for the instructor.
- A batch scanned in stacks (INBOX "BATCHES SCANNED IN STACKS", 2026-10-24): when the
  last paper of one scan is short by exactly the sides the next scan from the same
  sender starts with, those sides finish it (`_continuation`), in this scan's run and
  packet; the next scan's plan leaves them out (`_continued`). Its paper is assembled
  once both scans passed their review.
- A print job issued with a label for every page whose scan shows one label per
  exam is offered to the instructor for conversion (`label_modes`): evenly
  spaced first-page labels, a clean plan, at least two exams of that job and no
  label of its other pages read on any scan. Confirming sets the job's
  `label_mode` to `first_page`, so nothing is expected on its other pages;
  declining is remembered for that scan. Both are audited and reversible.
"""
import json
import logging
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha
from .service import text_field

LOG = logging.getLogger(__name__)

MAX_SIDES = 200
ANCHORS = ("resolved", "manual")
GUARD_SPAN = 3  # papers that add up between one too long and one too short, held as possibly shifted (`_guard`)
HELD_PAPER = "held_paper"  # the origin of a run that grouped one held paper by hand (`group_held`)
STACK_GAP = timedelta(hours=12)  # the longest pause between two stacks of one batch (`_continuation`)


def stamps_only(row):
    """A label sheet: its page count describes the labels, never the exam stuck behind them."""
    return json.loads(row["compiler_json"] or "{}").get("generator") == "faculty-stamps-only"


def sheet_pages(db, message_id, detector):
    """The scan's bubble sheets: the pages the detector recognized, and every page whose Scantron label was read (DOCUMENTS
    "SCANTRON LABELS"), which says what the page is even where its corner marks were not found."""
    found = detector.sheet_pages(db, message_id) if detector else set()
    return found | {row[0] for row in db.execute(
        """SELECT sp.id FROM scan_pages sp WHERE sp.message_id=? AND sp.status='unreadable' AND json_valid(sp.candidates_json)
           AND EXISTS(SELECT 1 FROM json_each(sp.candidates_json) j JOIN sheet_labels l ON l.token=json_extract(j.value,'$.payload'))""",
        (message_id,))}


def sheet_back(before, row, sheets, duplex):
    """Whether `row` is the back of the bubble sheet scanned right before it. A blank side always is. In a two-sided scan (an even
    number of sides in the attachment) a sheet on an odd side is a front, and the unlabeled side after it is its back however much
    shows through: home scanners in grey read an empty back as written (2026-10-24, Exam 2 scanned in stacks with each student's
    bubble sheet beside their booklet). A wrong guess leaves a paper a side short, which holds it: its sides must still add up."""
    if not before or before["id"] not in sheets or row["id"] in sheets or before["source_artifact_id"] != row["source_artifact_id"] \
            or before["source_page_index"] + 1 != row["source_page_index"]:
        return False
    return row["status"] == "blank" or bool(duplex and before["source_page_index"] % 2 and row["status"] == "unreadable" and not row.get("page_stamp_id"))


def set_aside(row):
    """A page the instructor set aside as part of no paper (`ScanService.set_aside`): no label read, and the review state blank
    pages carry. Never a place in a paper, and never a stretch of pages in no paper on its own."""
    return row["status"] == "unreadable" and row["review_state"] == "ignored"


class SequenceService:
    def __init__(self, scans):
        self.scans, self.documents = scans, scans.documents
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path

    @staticmethod
    def _sides(value):
        if type(value) is not int or not 1 <= value <= MAX_SIDES:
            raise ValueError(f"Enter the scanned sides per paper, between 1 and {MAX_SIDES}.")
        return value

    # ----- reading ------------------------------------------------------------------------

    @staticmethod
    def _rows(db, message_id):
        return [dict(row) for row in db.execute(
            """SELECT sp.id,sp.message_id,sp.source_artifact_id,sp.source_page_index,sp.status,sp.review_state,sp.page_stamp_id,sp.content_sha256,sp.raster_artifact_id,
                      f.filename,p.page_number,p.state stamp_state,c.id copy_id,c.copy_number,c.enrollment_id,c.caption,
                      b.status batch_status,b.superseded_at,b.label_mode,v.page_count,v.compiler_json,v.label version_label,a.title,
                      EXISTS(SELECT 1 FROM assembly_pages ap WHERE ap.scan_page_id=sp.id) reviewed,
                      -- A first page found on paper printed without labels (its printed Name and UIN fields), student known or not,
                      -- unless the instructor's latest word on it is that it is not a first page: the chunk rule's evidence only.
                      EXISTS(SELECT 1 FROM scan_unlabeled_reads r WHERE r.scan_page_id=sp.id AND r.cover=1
                             AND r.run_id=(SELECT u.id FROM scan_unlabeled_runs u WHERE u.message_id=sp.message_id ORDER BY u.attempt DESC LIMIT 1)
                             AND COALESCE((SELECT w.decision FROM scan_unlabeled_reviews w WHERE w.run_id=r.run_id AND w.scan_page_id=sp.id
                                           ORDER BY w.id DESC LIMIT 1), '')!='not_cover') found_first
               FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
               LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id LEFT JOIN printed_copies c ON c.id=p.printed_copy_id
               LEFT JOIN print_batches b ON b.id=c.print_batch_id LEFT JOIN assignment_versions v ON v.id=b.assignment_version_id
               LEFT JOIN assignments a ON a.id=v.assignment_id
               WHERE sp.message_id=? ORDER BY f.filename,sp.source_artifact_id,sp.source_page_index""", (message_id,))]

    @staticmethod
    def _current(row):
        return row["batch_status"] == "ready" and not row["superseded_at"] and row["stamp_state"] == "issued"

    def _plan(self, db, message_id, sides, identify=True, turn=False, rows=None):
        """`identify=False` only groups: no page takes an identity, so how many sides are written does not matter.

        `turn` puts sheets scanned back side first in order (`_sheets`) in the instructor's own preview and apply. Every plan, the
        automatic runs' included, turns them where every chunk of the attachment passes (`_chunked`); a sheet an earlier run grouped
        that way is recognized either way. `rows`: the scan's `_rows`, when the caller read them already.
        """
        rows = self._rows(db, message_id) if rows is None else rows
        packets, held = self._packets(db, message_id)
        attachments = self._attachments(db, message_id, rows)
        # A batch scanned in stacks (INBOX "BATCHES SCANNED IN STACKS"): the pages that start this scan finish the last paper of the
        # stack before it, so they are that scan's to group; the pages that start the next stack finish this scan's last paper.
        previous = self._continued(db, message_id, sides, attachments)
        if previous:
            attachments[0] = [row for row in attachments[0] if row["id"] not in previous[1]]
        following = self._continuation(db, message_id, sides, attachments)
        exams, runs = [], []
        for number, pages in enumerate(attachments):
            ordered = self._sheets(pages, sides, turn, packets, self._chunked(pages, sides))
            self._attachment(ordered + (following[1] if following and number == len(attachments) - 1 else []), sides, exams, runs, identify)
        # Most exams share one count of written sides; an exam that differs may hide a faint page read as blank.
        written = Counter(exam["written"] for exam in exams if not exam["problems"] and exam["numbered"])
        if sum(written.values()) >= 3:
            usual, count = written.most_common(1)[0]
            if count * 2 > sum(written.values()):
                for exam in exams:
                    if not exam["problems"] and exam["numbered"] and exam["written"] != usual:
                        exam["problems"].append(f"It has {exam['written']} written side{'' if exam['written'] == 1 else 's'} where most papers have {usual}; a faint or blank page would shift the page numbers.")
        for exam in exams:
            existing = packets.get(exam["anchor_scan_page_id"])
            if existing and existing.get("by_hand"):
                # The instructor chose this paper's sides by hand (2026-10-02): it stays exactly as grouped until that run is undone.
                exam.update(problems=[], assign=[], members=[], packet_id=existing["id"], group=False, ready=False, by_hand=existing["pages"])
                continue
            repeat = None if exam["problems"] else self._repeat(db, message_id, exam)
            if repeat:
                exam.update(problems=[], assign=[], members=[], packet_id=None, group=False, ready=False, repeat_of=repeat)
                continue
            if exam["problems"]:
                exam["assign"] = []
            self._targets(db, exam)
            members = [item["scan_page_id"] for item in exam["members"]]
            exam["packet_id"] = existing["id"] if existing and existing["pages"] == members else None
            if not exam["problems"] and not exam["packet_id"] and any(held.get(page) for page in members):
                exam["problems"].append("Some of these pages were grouped differently by an earlier run; undo that run before grouping them again.")
            exam["group"] = not exam["problems"] and not exam["packet_id"]
            if exam["problems"]:
                exam["assign"] = []
            exam["ready"] = exam["group"] or bool(exam["assign"])
        frozen = [[exam["anchor_scan_page_id"], [item["scan_page_id"] for item in exam["members"]] if exam["group"] else [],
                   [[item["scan_page_id"], item["page_stamp_id"] or f"sequence:{item['page_number']}"] for item in exam["assign"]]] for exam in exams if exam["ready"]]
        repeats = [exam["anchor_scan_page_id"] for exam in exams if exam.get("repeat_of")]
        if repeats:  # only then, so every other plan's fingerprint is as before
            frozen.append(["repeats", repeats])
        return {"message_id": message_id, "sides": sides, "exams": exams, "runs": runs,
                **({"continued_from": previous[0], "continued_pages": len(previous[1])} if previous else {}),
                **({"continues_in": following[0], "continuation_rows": following[1]} if following else {}),
                "fingerprint": sha(encoded({"message_id": message_id, "sides": sides, "identify": identify, "plan": frozen,
                                            **({"turn": True} if turn else {})}).encode()),
                "counts": {"pages": len(rows), "labels": len(exams), "ready": sum(exam["ready"] for exam in exams),
                           "turned": sum(1 for exam in exams if exam.get("turned") and exam["ready"]),
                           "grouped": sum(exam["group"] for exam in exams),
                           "identified": sum(len(exam["assign"]) for exam in exams),
                           "complete": sum(1 for exam in exams if not exam["problems"] and not exam["ready"]),
                           "review": sum(1 for exam in exams if exam["problems"]),
                           **({"repeats": len(repeats)} if repeats else {}),
                           "unlabeled": sum(run["pages"] for run in runs)}}

    @staticmethod
    def _packets(db, message_id):
        """Packets of runs that were not undone: by label page, and which packet holds each scan page."""
        packets, held = {}, {}
        for row in db.execute("""SELECT k.id,k.anchor_scan_page_id,q.scan_page_id,json_extract(r.summary_json,'$.origin') origin FROM scan_packets k
                                 JOIN scan_sequence_runs r ON r.id=k.run_id JOIN scan_packet_pages q ON q.packet_id=k.id
                                 WHERE k.message_id=? AND r.undone_at IS NULL ORDER BY k.id,q.side""", (message_id,)):
            packet = packets.setdefault(row["anchor_scan_page_id"], {"id": row["id"], "pages": []})
            if row["origin"] == HELD_PAPER:
                packet["by_hand"] = True  # grouped by hand (`group_held`): its sides are the instructor's choice
            packet["pages"].append(row["scan_page_id"])
            held[row["scan_page_id"]] = row["id"]
        return packets, held

    @staticmethod
    def _neighbour(db, message_id, later):
        """The scan from the same sender (one scanner, the copier, the workspace's uploads) just after (`later`) or just before this
        one among those still in the Inbox, when its pages are all inspected and it arrived within `STACK_GAP`; else None."""
        me = db.execute("SELECT sender,received_at,created_at FROM inbox_messages WHERE id=?", (message_id,)).fetchone()
        if not me:
            return None
        order = "ASC" if later else "DESC"
        row = db.execute(f"""SELECT m.id,m.received_at,d.status FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id
            LEFT JOIN document_decode_jobs d ON d.message_id=m.id
            WHERE m.sender=? AND m.id!=? AND j.action='scan.receive' AND m.authenticated=1 AND {PRESENT.format('m.id')}
              AND (m.received_at,m.created_at,m.id) {'>' if later else '<'} (?,?,?)
            ORDER BY m.received_at {order},m.created_at {order},m.id {order} LIMIT 1""",
            (me["sender"], message_id, me["received_at"], me["created_at"], message_id)).fetchone()
        if not row or row["status"] != "complete":
            return None
        try:
            gap = abs(datetime.fromisoformat(row["received_at"]) - datetime.fromisoformat(me["received_at"]))
        except (TypeError, ValueError):
            return None
        return row["id"] if gap <= STACK_GAP else None

    def _continuation(self, db, message_id, sides, attachments):
        """(next scan, its leading pages) when they finish this scan's last paper (INBOX "BATCHES SCANNED IN STACKS", instructor
        2026-10-24: a batch too large for the home scanners' feeder is scanned in stacks of 80-100 pages, so a paper labeled on its
        first page only can start at the end of one stack and end at the top of the next). Only when nothing else is possible: the
        last label of this scan's last attachment is a current page 1 label of paper labeled on page 1 only, with nothing but
        unlabeled or blank sides after it, fewer than `sides` of them, and the next scan from the same sender starts with exactly the
        sides it lacks before anything else (a label, a barcode, a bubble sheet), none set aside. Those leading pages may already
        carry the identities an earlier grouping of this paper gave them. Else None. Nothing is decided here: the plan holds them
        as the paper's last sides, and every rule of an ordinary paper applies."""
        if not sides or not attachments or not attachments[-1]:
            return None
        pages = attachments[-1]
        # A side of this paper may already carry the identity its position gave it (an earlier grouping of it): its own later page.
        mine = lambda row, copy: row["status"] in ANCHORS and row["copy_id"] == copy and (row["page_number"] or 0) > 1
        anchor = next((index for index in range(len(pages) - 1, -1, -1)
                       if pages[index]["status"] in ANCHORS and pages[index]["copy_id"] and pages[index]["page_number"] == 1), None)
        if anchor is None:
            return None
        first, tail = pages[anchor], pages[anchor + 1:]
        if not (self._current(first) and (stamps_only(first) or first["label_mode"] == "first_page")) or first.get("sheet") \
                or any(row.get("sheet") or set_aside(row) or not (row["status"] in ("unreadable", "blank") and not row["page_stamp_id"]
                                                                   or mine(row, first["copy_id"])) for row in tail) \
                or len(tail) + 1 >= sides:
            return None
        following = self._neighbour(db, message_id, later=True)
        if not following:
            return None
        theirs = self._attachments(db, following)
        if not theirs:
            return None
        lead = []
        for row in theirs[0]:
            if row.get("sheet") or not (row["status"] in ("unreadable", "blank") and not row["page_stamp_id"] or mine(row, first["copy_id"])):
                break
            lead.append(row)
        if not lead or len(tail) + 1 + len(lead) != sides or any(set_aside(row) for row in lead) \
                or not any(row["status"] != "blank" for row in lead):
            return None
        return following, [dict(row, continuation=following) for row in lead]

    def _continued(self, db, message_id, sides, attachments):
        """(previous scan, ids of this scan's leading pages) when those pages finish the previous scan's last paper
        (`_continuation`); else None."""
        if not attachments or not attachments[0]:
            return None
        start = attachments[0][0]
        if start.get("sheet") or start["status"] in ANCHORS and start["page_number"] == 1 or start["status"] not in (*ANCHORS, "unreadable", "blank"):
            return None  # it starts with a paper of its own (or a sheet, or another barcode): nothing of the stack before it
        previous = self._neighbour(db, message_id, later=False)
        if not previous:
            return None
        found = self._continuation(db, previous, sides, self._attachments(db, previous))
        return (previous, {row["id"] for row in found[1]}) if found and found[0] == message_id else None

    def _attachments(self, db, message_id, rows=None):
        """The scan's pages by attachment, in order. A recognized bubble sheet belongs to no exam (2026-09-24: a scan held sheets
        between two stacks of exams): it and the blank side scanned right after it, its back, are marked `sheet`."""
        rows = self._rows(db, message_id) if rows is None else rows
        sheets = sheet_pages(db, message_id, self.scans.bubble_detector)
        attachments = {}
        for row in rows:
            attachments.setdefault(row["source_artifact_id"], []).append(row)
        for pages in attachments.values():
            for before, row in zip([None, *pages], pages):
                row["sheet"] = row["id"] in sheets or sheet_back(before, row, sheets, len(pages) % 2 == 0)
        return list(attachments.values())

    def _first_labels(self, db, message_id):
        """Every page 1 label in scan order, with its place among the sides that are not a bubble sheet or its back: a student's
        sheet scanned beside their booklet is no side of the paper, so the distance from label to label leaves it out."""
        labels = []
        for pages in self._attachments(db, message_id):
            place = 0
            for row in pages:
                if row["sheet"]:
                    continue
                place += 1
                if row["status"] in ANCHORS and row["copy_id"] and row["page_number"] == 1:
                    labels.append({"id": row["id"], "attachment": row["source_artifact_id"], "place": place, "page_count": row["page_count"]})
        return labels

    @staticmethod
    def _chunked(pages, sides):
        """Every chunk of `sides` pages from the attachment's first page holds exactly one first page: on its first side, or, on
        one-sheet papers, on its second with nothing else identified on its first (a sheet scanned back side first). No bubble sheet,
        no missing page. , which nothing but a person reading handwriting could undo.

        A first page is a page 1 identity, or a first page found on paper printed without labels whose student is not settled yet
        (`found_first`, 2026-09-24: five such pages of a 326-paper quiz made their chunks look empty, and five backward sheets waited
        for a click). It says which faces belong together, never whose they are."""
        first = lambda row: bool(row["status"] in ANCHORS and row["copy_id"] and row["page_number"] == 1 or row.get("found_first"))
        if sides < 2 or not pages or len(pages) % sides or [row["source_page_index"] for row in pages] != list(range(1, len(pages) + 1)):
            return False
        for start in range(0, len(pages), sides):
            chunk = pages[start:start + sides]
            at = [index for index, row in enumerate(chunk) if first(row)]
            if len(at) != 1 or any(row.get("sheet") for row in chunk):
                return False
            if at[0] == 0:
                continue
            front = chunk[0]
            if sides == 2 and (front["status"] in ("unreadable", "blank") or front["copy_id"] == chunk[1]["copy_id"] and front["page_number"] != 1):
                continue
            return False
        return True

    def _chunk_sides(self, db, message_id):
        """The side count at which every chunk of every attachment passes (`_chunked`), when the scan suggests one, else None."""
        sides = self.summary(db, message_id)["suggested_sides"]
        attachments = self._attachments(db, message_id)
        return sides if sides and 1 <= sides <= MAX_SIDES and all(self._chunked(pages, sides) for pages in attachments) else None

    @staticmethod
    def _sheets(pages, sides, turn, packets, chunked=False):
        """Two-sided scans (requirement, 2026-09-24: five quiz sheets went through the scanner back side first, and at two sides per
        paper their page 2s were counted as the previous student's). Every side of every sheet is scanned, so in an attachment with
        an even number of pages sides 1-2, 3-4 ... are the two faces of one sheet, and both belong to one paper. That alignment is
        trusted only when the scan shows it: no sheet holds two first pages, and at most a third of the first pages are on a back.

        A first page on the back of its sheet means the sheet was fed back side first. On one-sheet papers (2 sides) its front is
        that paper's page 2: with `turn` (the instructor's preview), or where an earlier run grouped it so, the two faces swap places
        and the paper reads first page, then front. On longer papers the order of the other sheets cannot be told, so the paper is
        only marked (`first_sheet_turned`) and left alone. Returns the pages in reading order; nothing else changes.

        `chunked` (every chunk of the attachment holds one first page, `_chunked`) is evidence enough of the alignment, however many
        sheets were fed back first, and turns them in every plan, an automatic run's too (requirement, 2026-09-24).
        """
        pages = [dict(row) for row in pages]
        first = lambda row: row["status"] in ANCHORS and row["copy_id"] and row["page_number"] == 1
        if sides % 2 or [row["source_page_index"] for row in pages] != list(range(1, len(pages) + 1)):
            return pages
        anchors = [row for row in pages if first(row)]
        backs = [row for row in anchors if row["source_page_index"] % 2 == 0]
        if not backs:
            return pages
        trusted = chunked or (len(pages) % 2 == 0 and len(backs) * 3 <= len(anchors)
                              and not any(first(pages[i]) and first(pages[i + 1]) for i in range(0, len(pages) - 1, 2)))
        for row in backs:
            i = row["source_page_index"] - 1
            front = pages[i - 1]
            # Its other face has no identity yet, or already carries this copy's (a turned sheet an earlier run grouped). Anything else
            # on the front (a label of another page, a bubble sheet) is not this paper's other face.
            own = front["copy_id"] == row["copy_id"] and front["page_number"] != 1
            fits = trusted and not front.get("sheet") and (front["status"] in ("unreadable", "blank") or own)
            if fits and sides > 2:
                row["first_sheet_turned"] = front["source_page_index"]
            elif fits and (turn or chunked or packets.get(row["id"], {}).get("pages", [])[:2] == [row["id"], front["id"]]):
                row["turned"] = front["source_page_index"]
                pages[i - 1], pages[i] = row, front
            else:
                # A first page on a back, not put in order: whatever is behind it was counted from half a sheet (2026-09-24: a stack
                # fed back side first gives every paper the next student's page 2, and each still adds up). Held, never counted.
                row["on_back"] = front["source_page_index"]
        return pages

    def _attachment(self, pages, sides, exams, runs, identify=True):
        """Split one attachment at its labels, then check every exam's length against the side count."""
        mine, theirs = len(exams), len(runs)
        self._split(pages, sides, exams, runs, identify)
        self._guard(exams[mine:], runs[theirs:], sides)

    @staticmethod
    def _guard(exams, runs, sides):
        """Papers that add up but may carry a neighbour's page (2026-09-24), held for the instructor, never grouped by count alone.

        A first page on the back of its sheet, not put in order (`_sheets`), whose other face lies in pages before any label or in a
        paper that itself adds up: a stack fed back side first gives every paper the next student's page 2, and each still adds up.
        Where that face is the tail of a paper already held for too many sides, the count is explained and nothing more is held.
        And a paper that adds up between one with too many sides and one with too few (either order, a few papers at most): two
        sheets fed back first in a row shift the paper between them by one side.
        """
        def owner(index):
            return next((exam for exam in exams if exam["first_page"] <= index <= exam["last_page"]), None)
        for exam in exams:
            if exam.get("on_back") and not (owner(exam["on_back"]) or {}).get("long"):
                exam["problems"].append(f"Its first page is on the back of its sheet, after page {exam['on_back']}: that sheet was scanned back side "
                                        "first, or a page was added or lost before it, so its sides cannot be counted from here. Check it before grouping.")
        items = sorted([(exam["first_page"], "long" if exam.get("long") else "short" if exam.get("short") else None, exam) for exam in exams] +
                       [(run["first_page"], "long", None) for run in runs if run.get("stray")], key=lambda item: item[0])
        last, between = None, []
        for _, kind, exam in items:
            if kind:
                # A few papers only (sheets out of order next to each other): holding every paper between a short one near the start
                # and a stray page at the end would hold a whole section, and a first page on a back is held on its own (`_sheets`).
                if last and kind != last and len(between) <= GUARD_SPAN:
                    for held in between:
                        held["problems"].append(f"It lies between a paper with more than {sides} sides and one with fewer, so one of its "
                                                "pages may be a neighbour's (a sheet out of order or scanned back side first). Check it before grouping.")
                last, between = kind, []
            elif not exam["problems"]:
                between.append(exam)

    def _split(self, pages, sides, exams, runs, identify=True):
        groups, current, loose = [], None, []

        def stray(rows, where, **kind):
            # Pages in no paper that the instructor set aside (with only blank sides beside them) are no stretch at all: they
            # neither hold the grouping back nor count as "more" for the guard (requirement, 2026-10-02).
            if any(set_aside(row) for row in rows) and all(set_aside(row) or row["status"] == "blank" for row in rows):
                return
            runs.append(self._run(rows, where, **kind))

        for row in pages:
            if row.get("sheet"):
                # A bubble sheet (or its blank back): the exam before it ends here, and the sheet joins no exam or run.
                if loose:
                    stray(loose, "before the first label", stray=True)
                    loose = []
                current = None
                continue
            labeled = row["status"] in ANCHORS and row["copy_id"]
            continues = labeled and current and current["rows"][0]["copy_id"] == row["copy_id"] and row["page_number"] != 1
            barcode = row["status"] not in ("blank", "unreadable")
            if (labeled or barcode) and not continues:
                if loose:
                    stray(loose, "before the first label", stray=True)
                    loose = []
                current = {"rows": [row]}
                groups.append(current)
            elif current:
                current["rows"].append(row)
            else:
                loose.append(row)
        if loose:
            stray(loose, "with no label in this attachment")
        for group in groups:
            first, members = group["rows"][0], group["rows"]
            if first["status"] not in ANCHORS:
                runs.append(self._run(members, f"after a barcode that is {first['status']}; the pages behind it are not identified"))
                continue
            exam = {"anchor_scan_page_id": first["id"], "copy_id": first["copy_id"], "copy_number": first["copy_number"],
                    "enrollment_id": first["enrollment_id"], "caption": first["caption"] or "", "title": first["title"],
                    "version_label": first["version_label"], "page_count": first["page_count"], "filename": first["filename"],
                    "first_page": first["source_page_index"], "problems": [], "assign": [], "members": [], "written": 0,
                    # Written sides take page numbers only on paper that still expects a label on every page.
                    "numbered": identify and first["label_mode"] == "every_page" and (first["page_count"] or 0) > 1,
                    # Paper labeled on page 1 only: a stamps-only label sheet, or a print job the instructor confirmed.
                    # Its other sides take their position as their page once this exam adds up; nothing is asked per page.
                    "stamps_only": stamps_only(first), "positional": stamps_only(first) or first["label_mode"] == "first_page"}
            if first.get("turned"):
                exam["turned"] = first["turned"]
            if first.get("on_back"):
                exam["on_back"] = first["on_back"]  # judged by `_guard`, which knows what the other face of its sheet belongs to
            if first.get("first_sheet_turned"):
                exam["problems"].append(f"Its first page is on the back of its sheet: that sheet was scanned back side first (page "
                                        f"{first['first_sheet_turned']} is its front), and the order of its other sheets cannot be told from the scan.")
            if not self._current(first):
                exam["problems"].append("Its label belongs to cancelled, unissued or superseded paper.")
            if first["page_number"] != 1:
                exam["problems"].append(f"Its label is the page {first['page_number']} label, not page 1, so it does not mark where this paper starts.")
            extra = len(members) - sides
            if extra < 0:
                exam["problems"].append(f"Only {len(members)} of {sides} sides arrive before the next label.")
                exam["short"] = True
            elif extra % sides:
                exam["problems"].append(f"{len(members)} sides arrive before the next label, which is not a whole number of {sides}-side papers.")
                exam["long"] = True
            elif extra:
                orphan = members[sides:]
                runs.append(self._run(orphan, f"after copy {first['copy_number']}: {extra // sides} paper{'' if extra == sides else 's'} whose label was not read. Record its printed alias on its first page, then preview again"))
                members = members[:sides]
            # A turned sheet reads out of scan order; the pages it spans are still a range. Sides from the next stack's scan
            # (`_continuation`) are in another file: the range is this scan's, and the paper says where it continues.
            own = [row for row in members if not row.get("continuation")] or members
            exam["first_page"] = min(row["source_page_index"] for row in own)
            exam["last_page"] = max(row["source_page_index"] for row in own)
            later = [row for row in members if row.get("continuation")]
            if later:
                exam["continues_in"] = {"message_id": later[0]["continuation"], "first_page": later[0]["source_page_index"],
                                        "last_page": later[-1]["source_page_index"], "sides": len(later)}
            exam["attachment"] = first["source_artifact_id"]
            exam["sides"] = len(members)
            exam["written"] = sum(row["status"] != "blank" for row in members)
            aside = [row["source_page_index"] for row in members if set_aside(row)]
            if aside and not exam["problems"]:
                # The instructor said such a page is part of no paper: counting it as a side would contradict them.
                exam["problems"].append(f"Attachment page{'s' if len(aside) > 1 else ''} {', '.join(map(str, aside))} {'was' if len(aside) == 1 else 'were'} "
                                        "set aside as part of no paper, so its sides cannot be counted from here. Choose its sides by hand.")
            exams.append(exam)
            if exam["problems"]:
                continue  # Where it starts or ends is unclear, so there is nothing further to check.
            number = 0
            for side, row in enumerate(members, start=1):
                exam["members"].append({"scan_page_id": row["id"], "source_page_index": row["source_page_index"], "side": side, "blank": row["status"] == "blank"})
                if row["status"] == "blank":
                    continue
                number += 1
                if exam["positional"]:
                    # The instructor's rule: an exam that adds up is in scan order, so side n is page n. Blank backs
                    # keep their place and no identity; nothing is inferred for an exam with any problem above.
                    if row["status"] in ANCHORS:
                        if row["page_number"] != side:
                            exam["problems"].append(f"Attachment page {row['source_page_index']} is labeled page {row['page_number']} but is side {side} of this paper.")
                    elif not exam["stamps_only"] and side > first["page_count"]:
                        exam["problems"].append(f"Side {side} is written, but this paper was issued {first['page_count']} page{'' if first['page_count'] == 1 else 's'}.")
                    else:
                        exam["assign"].append({"scan_page_id": row["id"], "source_page_index": row["source_page_index"], "side": side,
                                               "page_number": side, "content_sha256": row["content_sha256"]})
                elif row["status"] in ANCHORS:
                    if row["page_number"] != number:
                        exam["problems"].append(f"Attachment page {row['source_page_index']} is labeled page {row['page_number']} but is written side {number} of this paper.")
                # A one-page label identifies the paper, not its other pages: only pages the copy was issued take an identity.
                elif exam["numbered"] and number <= first["page_count"]:
                    exam["assign"].append({"scan_page_id": row["id"], "source_page_index": row["source_page_index"], "side": side,
                                           "page_number": number, "content_sha256": row["content_sha256"]})

    @staticmethod
    def _repeat(db, message_id, exam):
        """The same paper scanned twice (requirement, 2026-10-24): a sticker-labeled paper whose label was also read in
        another scan still in the Inbox, where that copy is already a grouped paper of exactly as many sides. A sticker is one
        sticker, so it is one paper: the copy grouped first is kept, and this one needs nothing. Comparing the pictures was measured
        and could not tell a re-scan from another student's page (handwriting is a small share of the ink), so it is not used.
        Returns where the kept copy is, or None."""
        if not exam["positional"]:
            return None
        anchor = db.execute("SELECT review_state FROM scan_pages WHERE id=?", (exam["anchor_scan_page_id"],)).fetchone()
        # A re-scan never repeats the page image byte for byte: only a first page with changed content (or one already set aside as
        # a repeat) is one. The same file arriving twice is the intake's `duplicate`, and its other pages decide as before.
        if not anchor or anchor[0] not in ("conflict", "rejected"):
            return None
        row = db.execute(f"""SELECT k.message_id,j.id job_id,k.sides,k.anchor_scan_page_id,(SELECT MIN(sp.source_page_index) FROM scan_packet_pages q
                    JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE q.packet_id=k.id) first_page
                FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id AND r.undone_at IS NULL JOIN inbox_jobs j ON j.message_id=k.message_id
                WHERE k.printed_copy_id=? AND k.message_id!=? AND {PRESENT.format('k.message_id')} ORDER BY k.created_at,k.id LIMIT 1""",
                         (exam["copy_id"], message_id)).fetchone()
        return {"message_id": row["message_id"], "job_id": row["job_id"], "first_page": row["first_page"], "anchor": row["anchor_scan_page_id"]} \
            if row and row["sides"] == exam["sides"] else None

    @staticmethod
    def _run(rows, where, stray=False):
        return {"filename": rows[0]["filename"], "attachment": rows[0]["source_artifact_id"], "first_page": rows[0]["source_page_index"],
                "last_page": rows[-1]["source_page_index"], "pages": len(rows), "written": sum(row["status"] != "blank" for row in rows),
                "where": where, **({"stray": True} if stray else {})}

    @staticmethod
    def _targets(db, exam):
        """Resolve each written side to the copy's issued page stamp; another page already holding it is a problem."""
        stamps = {row["page_number"]: dict(row) for row in db.execute("SELECT id,page_number,state FROM page_stamps WHERE printed_copy_id=?", (exam["copy_id"],))}
        for item in exam["assign"]:
            stamp = stamps.get(item["page_number"])
            if stamp is None and exam["stamps_only"]:
                item["page_stamp_id"], item["issue"] = None, True
                continue
            if stamp is None or stamp["state"] != "issued":
                exam["problems"].append(f"Page {item['page_number']} of this copy has no issued identity.")
                continue
            item["page_stamp_id"] = stamp["id"]
            other = db.execute(f"""SELECT source_page_index FROM scan_pages WHERE page_stamp_id=? AND id!=? AND content_sha256!=?
                                  AND review_state NOT IN ('rejected','ignored') AND {PRESENT.format('scan_pages.message_id')} LIMIT 1""", (stamp["id"], item["scan_page_id"], item["content_sha256"])).fetchone()
            if other:
                exam["problems"].append(f"Page {item['page_number']} of this copy was already identified on another scanned page with different content.")

    def preview(self, message_id, sides, turn=False):
        sides = self._sides(sides)
        with connect(self.path) as db:
            self.inbox.message(db, message_id)
            plan = self._plan(db, message_id, sides, turn=turn)
            plan.pop("continuation_rows", None)  # the next stack's pages themselves stay internal; `continues_in` names its scan
            return plan

    def summary(self, db, message_id):
        runs = []
        for row in db.execute("SELECT * FROM scan_sequence_runs WHERE message_id=? ORDER BY created_at DESC,id", (message_id,)):
            item = {**json.loads(row["summary_json"]), "id": row["id"], "sides": row["sides"], "created_at": row["created_at"], "undone_at": row["undone_at"],
                    "undo": json.loads(row["undo_json"]) if row["undo_json"] else None}
            runs.append(item)
        labels = self._first_labels(db, message_id)
        # The scan itself suggests the count: the usual distance between one page 1 label and the next, bubble sheets left out.
        gaps = Counter(b["place"] - a["place"] for a, b in zip(labels, labels[1:]) if a["attachment"] == b["attachment"])
        issued = {label["page_count"] for label in labels}
        suggested = gaps.most_common(1)[0][0] if gaps else next(iter(issued)) if len(issued) == 1 and min(issued) > 1 else None
        unidentified = db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=? AND status='unreadable' AND review_state!='ignored'", (message_id,)).fetchone()[0]
        packets = [dict(row) for row in db.execute(
            """SELECT k.id,k.sides,k.run_id,c.copy_number,c.caption,c.enrollment_id,a.title,v.label version_label,s.number section_number,
                      (SELECT MIN(sp.source_page_index) FROM scan_packet_pages q JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE q.packet_id=k.id) first_page,
                      (SELECT MAX(sp.source_page_index) FROM scan_packet_pages q JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE q.packet_id=k.id) last_page
               FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id JOIN printed_copies c ON c.id=k.printed_copy_id
               JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
               JOIN assignments a ON a.id=v.assignment_id LEFT JOIN course_sections s ON s.id=b.section_id
               WHERE k.message_id=? AND r.undone_at IS NULL ORDER BY first_page""", (message_id,))]
        return {"runs": runs, "packets": packets, "max_sides": MAX_SIDES, "suggested_sides": suggested,
                "labels": bool(labels), "unidentified": unidentified}

    # ----- writing ------------------------------------------------------------------------

    def _job(self, db, message_id):
        message = self.inbox.message(db, message_id)
        if not message["authenticated"]:
            raise Conflict("Sender authentication failed; its pages cannot be identified.")
        job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
        if not job or job["action"] != "scan.receive":
            raise ValueError("Pages are identified on a retained scan.receive submission.")
        return job

    def cadence(self, db, message_id):
        """The one distance between every page 1 label and the next, or None when the labels are not evenly spaced.

        Every gap in every attachment must be the same number of sides; one label
        alone has no cadence. Bubble sheets and their backs are no sides (`_first_labels`).
        Whether the pages after the last label and before the first fit that cadence is
        judged by the plan itself.
        """
        labels = self._first_labels(db, message_id)
        # A paper the instructor grouped by hand (2026-10-02) spans the sides it was held at: no evidence of the others' count.
        by_hand = {row[0] for row in db.execute(
            """SELECT k.anchor_scan_page_id FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id
               WHERE k.message_id=? AND r.undone_at IS NULL AND json_extract(r.summary_json,'$.origin')=?""", (message_id, HELD_PAPER))} if len(labels) > 1 else set()
        gaps = {b["place"] - a["place"] for a, b in zip(labels, labels[1:]) if a["attachment"] == b["attachment"] and a["id"] not in by_hand}
        return gaps.pop() if len(gaps) == 1 and 1 <= next(iter(gaps)) <= MAX_SIDES else None

    def auto_group(self, message_id):
        """Group a newly inspected scan by itself when its labels leave nothing to decide (instructor's standing request).

        Only when some pages carry no label, no run was ever made for this scan,
        every label-to-label distance is identical (or, where sheets scanned back
        side first break the spacing, every chunk passes at the usual distance,
        `_chunked`), and the plan at that distance has no exam to review and no page
        outside a labeled exam. Anything less even is left for the instructor's
        preview, which says what is off.

        A paper the instructor grouped by hand (`group_held`, 2026-10-02) is settled by them, not a grouping of the scan: its run
        does not count as one, its own label-to-label distance is left out of the cadence, and it counts as neither ready nor to
        review, so the papers that add up are grouped by themselves once the one held paper is (PIPELINE). Every other run, an
        undone one included, still means the instructor took the grouping over.
        """
        sides = self._auto_group(message_id)
        # A batch scanned in stacks (INBOX "BATCHES SCANNED IN STACKS"): this scan's first pages may finish the last paper of the
        # stack before it, and its last paper may end in the stack after it, inspected first. Either then adds up and is grouped by
        # the same rule. One stack each way only; a neighbour's failure is its own.
        for later in (False, True):
            try:
                with connect(self.path) as db:
                    neighbour = self._neighbour(db, message_id, later=later)
                if neighbour:
                    self._auto_group(neighbour)
            except Exception:
                LOG.exception("Grouping the stack %s scan %s again did not complete", "after" if later else "before", message_id)
        return sides

    def _auto_group(self, message_id):
        with connect(self.path) as db:
            if db.execute("SELECT 1 FROM scan_sequence_runs WHERE message_id=? AND COALESCE(json_extract(summary_json,'$.origin'),'')!=?",
                          (message_id, HELD_PAPER)).fetchone():
                return None
            if not db.execute("SELECT 1 FROM scan_pages WHERE message_id=? AND status='unreadable' AND review_state!='ignored'", (message_id,)).fetchone():
                return None  # A label on every page already identifies every page (a page set aside is in no paper).
            # Sheets scanned back side first break the even spacing; every chunk holding one first page still leaves nothing to decide.
            sides = self.cadence(db, message_id) or self._chunk_sides(db, message_id)
            plan = self._plan(db, message_id, sides, identify=False) if sides else None
            by_hand = sum(1 for exam in plan["exams"] if exam.get("by_hand")) if plan else 0
            held = not plan or plan["counts"]["review"] or plan["counts"]["unlabeled"] \
                or plan["counts"]["ready"] + by_hand + plan["counts"].get("repeats", 0) != plan["counts"]["labels"] \
                or not plan["counts"]["ready"] + plan["counts"].get("repeats", 0)
            # A paper whose label was not read breaks the even spacing: the usual distance shows where it starts.
            usual = self.summary(db, message_id)["suggested_sides"] if held and not plan else None
            probe = plan if held and plan else self._plan(db, message_id, usual, identify=False) if usual and 1 <= usual <= MAX_SIDES else None
        if held:
            if probe:
                self._read_missing_labels(message_id, probe)
            return None
        # Grouping only: whether the other pages should carry labels at all is the instructor's answer (`label_modes`).
        self.apply(message_id, sides, plan["fingerprint"], actor="automatic", identify=False)
        return sides

    def _read_missing_labels(self, message_id, plan):
        """Where the grouping holds whole papers whose page 1 label was not read (a run of pages in no paper, a whole number of papers
        long), their first pages are given to the sticker alias reader (`AliasOcrService.enqueue_first_pages`). An exact alias
        identifies the page like any label; the scan then groups by the same rule."""
        alias_ocr = self.scans.alias_ocr
        sides = plan["sides"]
        starts = [(run["attachment"], run["first_page"] + offset) for run in plan["runs"]
                  if run["pages"] and not run["pages"] % sides for offset in range(0, run["pages"], sides)]
        if not alias_ocr or not starts:
            return
        with connect(self.path) as db:
            ids = [row[0] for attachment, index in starts for row in db.execute(
                "SELECT id FROM scan_pages WHERE message_id=? AND source_artifact_id=? AND source_page_index=?", (message_id, attachment, index))]
        try:
            alias_ocr.enqueue_first_pages(message_id, ids)
        except Exception:
            LOG.exception("Queueing the sticker alias reads of scan %s did not complete", message_id)

    def label_modes(self, db, message_id):
        """How each print job on this scan is labeled, and whether the scan says a job has one label per exam.

        `offer` asks the instructor once: the job expects a label on every page of
        copies issued more than one, yet this scan's page 1 labels are evenly
        spaced, the plan at that distance leaves nothing to review and no page
        outside an exam, at least two exams of the job are here, and no label of
        its other pages was ever read on any scan. `can_convert` is the weaker
        rule for the instructor's own choice: nothing read contradicts it.
        """
        jobs = [dict(row) for row in db.execute(
            f"""SELECT b.id batch_id,b.label_mode,v.page_count,v.label version_label,a.title,s.number section_number,
                      COUNT(DISTINCT CASE WHEN p.page_number=1 THEN c.id END) labels,
                      EXISTS(SELECT 1 FROM scan_label_prompts k WHERE k.message_id=? AND k.batch_id=b.id) kept,
                      EXISTS(SELECT 1 FROM scan_pages o JOIN page_stamps op ON op.id=o.page_stamp_id JOIN printed_copies oc ON oc.id=op.printed_copy_id
                             WHERE oc.print_batch_id=b.id AND op.page_number>1 AND o.status IN ('resolved','manual') AND {PRESENT.format('o.message_id')}
                             AND NOT EXISTS(SELECT 1 FROM scan_sequence_pages q WHERE q.scan_page_id=o.id AND q.page_stamp_id=o.page_stamp_id)) other_labels
               FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
               JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
               JOIN assignments a ON a.id=v.assignment_id LEFT JOIN course_sections s ON s.id=b.section_id
               WHERE sp.message_id=? AND sp.status IN ('resolved','manual') AND v.page_count>1
               GROUP BY b.id ORDER BY a.title,v.label,s.number,b.id""", (message_id, message_id))]
        sides = self.cadence(db, message_id) if any(job["label_mode"] == "every_page" and not job["other_labels"] for job in jobs) else None
        counts = self._plan(db, message_id, sides, identify=False)["counts"] if sides else None
        even = bool(counts) and not counts["review"] and not counts["unlabeled"]
        for job in jobs:
            job["kept"], job["other_labels"] = bool(job["kept"]), bool(job["other_labels"])
            job["sides"] = sides if even else None
            job["can_convert"] = job["label_mode"] == "every_page" and not job["other_labels"] and job["labels"] >= 1
            job["offer"] = job["can_convert"] and even and job["labels"] >= 2 and not job["kept"]
        return jobs

    def set_label_mode(self, message_id, batch_ids, decision, actor="workspace"):
        """The instructor's answer for print jobs on this scan: `first_page`, `every_page`, or `keep` (decline the offer)."""
        if decision not in ("first_page", "every_page", "keep"):
            raise ValueError("Choose first-page labels, a label on every page, or keep the print job as it is.")
        if not isinstance(batch_ids, list) or not batch_ids or not all(isinstance(item, str) for item in batch_ids) or len(set(batch_ids)) != len(batch_ids):
            raise ValueError("Name the print jobs this applies to.")
        with self.rosters.lock():
            with connect(self.path) as db:
                job = self._job(db, message_id)
                jobs = {item["batch_id"]: item for item in self.label_modes(db, message_id)}
            for batch_id in batch_ids:
                found = jobs.get(batch_id)
                if not found:
                    raise NotFound("That print job has no label on this scan.")
                if decision == "first_page" and found["label_mode"] != "first_page" and not found["can_convert"]:
                    raise Conflict("A label from another page of this print job was read on a scan, so its paper is labeled beyond the first page.")
                if decision == "keep" and not found["offer"]:
                    raise Conflict("That question no longer applies to this scan. Reload its review.")
            for batch_id in batch_ids:
                found = jobs[batch_id]
                if decision != "keep":
                    self.documents.set_label_mode(batch_id, decision, actor, {"message_id": message_id, "labels": found["labels"], "sides": found["sides"]})
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    if decision != "first_page":  # Declined, or switched back: this scan does not ask again.
                        db.execute("INSERT OR IGNORE INTO scan_label_prompts VALUES(?,?,'keep',?,?)", (message_id, batch_id, actor, timestamp()))
                    self.inbox.event(db, job["id"], "label_mode_decision", encoded({
                        "actor": actor, "batch_id": batch_id, "decision": decision, "from": found["label_mode"], "labels": found["labels"],
                        "sides": found["sides"], "instructor_review_required": True}))
        if decision == "first_page":
            self.identify_grouped(message_id, actor, origin="label_mode")
        elif decision == "every_page":
            # Taking the answer back takes back what it alone identified; reviewed pages keep theirs (undo's own rule).
            with connect(self.path) as db:
                given = [row["id"] for row in db.execute("SELECT id,summary_json FROM scan_sequence_runs WHERE message_id=? AND undone_at IS NULL ORDER BY created_at DESC", (message_id,))
                         if json.loads(row["summary_json"]).get("origin") == "label_mode"]
            for run_id in given:
                self.undo(message_id, run_id, actor)
            if given:  # A run is undone whole; print jobs still labeled on the first page keep their pages.
                self.identify_grouped(message_id, actor, origin="label_mode")
        return self.scans.review_item(message_id)

    def apply(self, message_id, sides, fingerprint, actor="workspace", identify=True, origin=None, turn=False):
        sides = self._sides(sides)
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                self._job(db, message_id)
                planned = self._plan(db, message_id, sides, identify, turn)
                stamps = [item["page_stamp_id"] for exam in planned["exams"] for item in exam["assign"] if item["page_stamp_id"]]
                # Like barcode intake, a new identity can flag changed content on earlier messages. A paper finished by the next
                # stack's first pages (`_continuation`) writes that scan's pages too.
                owners = {message_id, *([planned["continues_in"]] if planned.get("continues_in") else [])}
                for start in range(0, len(stamps), 500):
                    chunk = stamps[start:start + 500]
                    owners.update(row[0] for row in db.execute(
                        f"SELECT DISTINCT message_id FROM scan_pages WHERE page_stamp_id IN ({','.join('?' * len(chunk))})", chunk))
            for owner in sorted(owners):
                locks.enter_context(self.documents.files.owner_lock(owner))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                job = self._job(db, message_id)
                plan = self._plan(db, message_id, sides, identify, turn)
                if plan["fingerprint"] != fingerprint:
                    raise Conflict("The scan or its labels changed after this preview. Review the new preview before identifying pages.")
                # A stack scanned again whole (Exam 2, 2026-10-25) holds only repeats: setting them aside is the run's work.
                if not plan["counts"]["ready"] and not plan["counts"].get("repeats"):
                    raise Conflict("Nothing in this preview can be grouped or identified.")
                run_id, when = str(uuid.uuid4()), timestamp()
                summary = {"exams": plan["counts"]["ready"], "packets": plan["counts"]["grouped"], "pages": plan["counts"]["identified"],
                           "review": plan["counts"]["review"], "unlabeled": plan["counts"]["unlabeled"], "automatic": actor == "automatic",
                           **({"repeats": plan["counts"]["repeats"]} if plan["counts"].get("repeats") else {}),
                           **({"turned": [exam["turned"] for exam in plan["exams"] if exam.get("turned") and exam["group"]]} if plan["counts"]["turned"] else {}),
                           **({"origin": origin} if origin else {})}
                db.execute("INSERT INTO scan_sequence_runs(id,message_id,sides,plan_sha256,summary_json,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                           (run_id, message_id, sides, plan["fingerprint"], encoded(summary), actor, when))
                for exam in plan["exams"]:
                    if exam.get("repeat_of"):
                        # The same paper scanned twice: the copy grouped from the other scan is kept, as a reviewed paper keeps its own.
                        db.execute("UPDATE scan_pages SET review_state='rejected',updated_at=? WHERE id=? AND review_state='conflict'",
                                   (when, exam["anchor_scan_page_id"]))
                        # The kept copy's first page conflicts with nothing any more unless a third, different page holds its label.
                        db.execute(f"""UPDATE scan_pages SET review_state='candidate',updated_at=? WHERE id=? AND review_state='conflict'
                                      AND NOT EXISTS(SELECT 1 FROM scan_pages o WHERE o.page_stamp_id=scan_pages.page_stamp_id AND o.id!=scan_pages.id
                                          AND o.review_state NOT IN ('rejected','duplicate') AND o.content_sha256!=scan_pages.content_sha256
                                          AND {PRESENT.format('o.message_id')})""", (when, exam["repeat_of"]["anchor"]))
                    if exam["group"]:
                        packet_id = str(uuid.uuid4())
                        db.execute("INSERT INTO scan_packets VALUES(?,?,?,?,?,?,?)",
                                   (packet_id, run_id, message_id, exam["anchor_scan_page_id"], exam["copy_id"], len(exam["members"]), when))
                        db.executemany("INSERT INTO scan_packet_pages VALUES(?,?,?)", [(packet_id, item["scan_page_id"], item["side"]) for item in exam["members"]])
                    for item in exam["assign"]:
                        if item.get("issue"):
                            item["page_stamp_id"] = self.documents.issue_sequence_stamp(db, exam["copy_id"], item["page_number"], when)
                        self._identify(db, item, when)
                        db.execute("INSERT INTO scan_sequence_pages VALUES(?,?,?,?,?,?)",
                                   (run_id, item["scan_page_id"], exam["anchor_scan_page_id"], item["page_stamp_id"], item["side"], when))
                self.inbox.event(db, job["id"], "sequence_page_identification", encoded({
                    "actor": actor, "run_id": run_id, "method": "first_page_label_and_side_count", "sides": sides, **summary,
                    "plan_sha256": plan["fingerprint"], "instructor_review_required": True}))
        return self.scans.review_item(message_id)

    def identify_grouped(self, message_id, actor, origin="inbox_review"):
        """Give exams already grouped on this scan the identities their first-page label now allows. Idempotent.

        Used when the instructor confirms a print job is labeled on the first page only, and again at the
        Inbox review for scans grouped before. Exams with any problem are left exactly as they are.
        """
        with connect(self.path) as db:
            # A paper grouped by hand has as many sides as the instructor ticked: that is no side count to plan the scan at.
            counts = [row[0] for row in db.execute("""SELECT DISTINCT k.sides FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id
                                                      WHERE k.message_id=? AND r.undone_at IS NULL
                                                        AND COALESCE(json_extract(r.summary_json,'$.origin'),'')!=? ORDER BY k.sides""", (message_id, HELD_PAPER))]
        identified = 0
        for sides in counts:
            with connect(self.path) as db:
                plan = self._plan(db, message_id, sides, identify=False)
            if plan["counts"]["identified"]:
                self.apply(message_id, sides, plan["fingerprint"], actor=actor, identify=False, origin=origin)
                identified += plan["counts"]["identified"]
        return identified

    def assemble_reviewed(self, message_id, actor="inbox-review", quiet=False):
        """The scan's Inbox review is the approval of its papers that are ready (the instructor's, or the worker's automatic pass).

        Every first-page-labeled exam that added up (label on side 1, even cadence, each written side identified
        as its position on that copy, nothing duplicated or in conflict) becomes that copy's reviewed paper, which
        the exam collection and grading already understand; so does every copy labeled on every page whose pages
        all arrived exactly once (`_assemble_labeled`). A copy that has any assembly is never touched, and
        anything less tidy stays with the existing per-copy assembly review. Whether the paper has as many pages
        as the blank exam is checked where the blank exam is known: when grading selects it.

        It runs again for papers made ready AFTER the review (requirement, 2026-09-28: a first page registered on
        Find students, or grouped on Papers to check, once the rest of its scan had gone on to grading): actor
        `automatic`, from the worker's catch-up (`GradingService.assemble_later_one`) and from registration.
        `quiet` records an Inbox event only when something was identified or assembled.
        """
        with connect(self.path) as db:
            message = self.inbox.message(db, message_id)
            if not message["authenticated"] or message["instructor_review_required"]:
                return {"identified": 0, "assembled": 0, "left": []}
        reason = ("First-page label, even label-to-label cadence, pages in scan order, and the instructor's Inbox review of this scan."
                  if actor == "inbox-review" else "First-page label, even label-to-label cadence and pages in scan order, grouped after this scan passed its Inbox review.")
        identified = self.identify_grouped(message_id, actor)
        with connect(self.path) as db:
            # This scan's papers, and a paper of the stack before it that this scan's first pages finish (`_continuation`).
            packets = [dict(row) for row in db.execute(
                """SELECT k.id,k.message_id,k.printed_copy_id,k.sides,c.copy_number,b.label_mode,b.status batch_status,b.superseded_at,v.compiler_json,
                          (SELECT COUNT(*) FROM assembly_revisions ar WHERE ar.printed_copy_id=k.printed_copy_id) revisions
                   FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id JOIN printed_copies c ON c.id=k.printed_copy_id
                   JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                   WHERE (k.message_id=? OR k.id IN (SELECT q.packet_id FROM scan_packet_pages q JOIN scan_pages sp ON sp.id=q.scan_page_id
                                                     WHERE sp.message_id=?)) AND r.undone_at IS NULL ORDER BY c.copy_number,k.id""",
                (message_id, message_id))]
            for packet in packets:
                # Every side's scan passed its review and is still in the Inbox: a paper split between two stacks waits for both.
                packet["pages"] = [dict(row) for row in db.execute(
                    f"""SELECT q.side,sp.id,sp.status,sp.review_state,p.printed_copy_id,p.page_number,m.instructor_review_required waiting,
                              {PRESENT.format('m.id')} present FROM scan_packet_pages q
                       JOIN scan_pages sp ON sp.id=q.scan_page_id JOIN inbox_messages m ON m.id=sp.message_id
                       LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id
                       WHERE q.packet_id=? ORDER BY q.side""", (packet["id"],))]
        assembled, left, failures = 0, [], []
        for packet in packets:
            if not (stamps_only(packet) or packet["label_mode"] == "first_page") or packet["revisions"]:
                continue
            written = [page for page in packet["pages"] if page["status"] != "blank"]
            # One written side is a whole paper when the packet holds every side of the paper and the rest are blank (instructor,
            # 2026-10-17: "why does it say so many responses can't be graded": 281 one-page take-homes with blank backs never assembled),
            # a paper of ONE side included (2026-10-18: two unlabeled one-sided take-homes, grouped one side each, never assembled).
            whole = len(written) > 1 or (len(written) == 1 and len(packet["pages"]) == packet["sides"])
            sound = packet["batch_status"] == "ready" and not packet["superseded_at"] and whole and all(
                page["status"] in ANCHORS and page["printed_copy_id"] == packet["printed_copy_id"] and page["page_number"] == page["side"]
                and page["review_state"] == "candidate" for page in written) and all(page["present"] and not page["waiting"] for page in packet["pages"])
            if not sound:
                left.append(packet["copy_number"])
                continue
            try:
                revision = self.scans.select_assembly(packet["printed_copy_id"], [page["id"] for page in written], False, reason, actor)
            except (ValueError, Conflict, Forbidden) as error:
                LOG.info("Paper %s of scan %s was left for the assembly review: %s", packet["copy_number"], message_id, error)
                left.append(packet["copy_number"])
                failures.append(packet["printed_copy_id"])
                continue
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("INSERT OR IGNORE INTO scan_sequence_assemblies VALUES(?,?,?,?,?,?,?)",
                           (revision["id"], packet["id"], packet["message_id"], packet["sides"], len(written), actor, timestamp()))
            assembled += 1
        labeled = self._assemble_labeled(message_id, actor, failures)
        if identified or assembled or labeled or (left and not quiet):
            with self.rosters.lock(), connect(self.path) as db:
                self.inbox.event(db, self._job(db, message_id)["id"], "first_page_papers_assembled", encoded({
                    "actor": actor, "identified_pages": identified, "assembled": assembled, "left_for_assembly_review": left,
                    **({"labeled_on_every_page": labeled} if labeled else {})}))
        return {"identified": identified, "assembled": assembled + labeled, "left": left, **({"retry": True} if failures else {})}

    def _assemble_labeled(self, message_id, actor, failures=None):
        """Copies labeled on EVERY page with a page on this scan, each of whose pages arrived exactly once (or as exact repeats of one
        content), become their student's reviewed paper (2026-09-28): each page's own label names its copy and page, so nothing is
        inferred. Never a copy that has any assembly, paper that is not current, a page in conflict, a page on a scan not yet
        reviewed, or a student with another copy of this assignment on any scan (a submission choice is theirs). A copy issued to no
        student is assembled like a first-page packet is: it is a paper once a student is assigned to it."""
        with connect(self.path) as db:
            copies = [row[0] for row in db.execute(
                """SELECT DISTINCT c.id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                   JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                   WHERE sp.message_id=? AND sp.status IN ('resolved','manual') AND b.label_mode='every_page' AND b.status='ready'
                     AND b.superseded_at IS NULL
                     AND COALESCE(json_extract(v.compiler_json,'$.generator'),'')!='faculty-stamps-only'
                     AND NOT EXISTS(SELECT 1 FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id)
                   ORDER BY c.id""", (message_id,))]
        assembled = 0
        for copy_id in copies:
            with connect(self.path) as db:
                paper = self.scans.assembly(copy_id, db)
                others = db.execute(f"""SELECT 1 FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                    JOIN page_stamps p ON p.printed_copy_id=c.id JOIN scan_pages sp ON sp.page_stamp_id=p.id
                    WHERE c.enrollment_id=? AND v.assignment_id=? AND c.id!=? AND sp.status IN ('resolved','manual')
                      AND {PRESENT.format('sp.message_id')} LIMIT 1""",
                    (paper["enrollment_id"], paper["assignment_id"], copy_id)).fetchone() if paper["enrollment_id"] else None
                if paper["missing"] or paper["conflicts"] or paper["other_reviewed_copies"] or paper["submission_choice_required"] or others:
                    continue
                chosen, sound = [], True
                for page in paper["pages"]:
                    accepted = [obs for obs in page["observations"] if obs["status"] in ANCHORS and obs["review_state"] not in ("rejected", "ignored")]
                    if not accepted or any(obs["review_state"] == "conflict" for obs in accepted) or len({obs["content_sha256"] for obs in accepted}) != 1:
                        sound = False
                        break
                    pick = next((obs for obs in accepted if obs["review_state"] == "candidate"), accepted[0])
                    source = db.execute(f"""SELECT m.authenticated,m.instructor_review_required FROM inbox_messages m WHERE m.id=?
                                            AND {PRESENT.format('m.id')}""", (pick["message_id"],)).fetchone()
                    if not source or not source["authenticated"] or source["instructor_review_required"]:
                        sound = False
                        break
                    chosen.append(pick["id"])
            if not sound or not chosen:
                continue
            try:
                self.scans.select_assembly(copy_id, chosen, False, "Every page's own label, each page arrived once, and this scan passed its Inbox review.", actor)
            except (ValueError, Conflict, Forbidden) as error:
                LOG.info("Copy %s of scan %s was left for the assembly review: %s", copy_id, message_id, error)
                if failures is not None:
                    failures.append(copy_id)
                continue
            assembled += 1
        return assembled

    @staticmethod
    def _identify(db, item, when):
        """The duplicate/changed-content rules of barcode intake; the page had no identity and is in no assembly."""
        from .scans import ScanService  # the one shared rule (ScanService._identity_state); scans imports this module itself
        stamp_id, content_hash = item["page_stamp_id"], item["content_sha256"]
        review_state, duplicate = ScanService._identity_state(db, item["scan_page_id"], stamp_id, content_hash, when)
        updated = db.execute("""UPDATE scan_pages SET page_stamp_id=?,status='manual',review_state=?,duplicate_of_scan_page_id=?,updated_at=?
                                WHERE id=? AND status='unreadable' AND page_stamp_id IS NULL""",
                             (stamp_id, review_state, duplicate, when, item["scan_page_id"])).rowcount
        if updated != 1:
            raise Conflict("A page changed while it was being identified. Review the new preview.")

    # ----- a held paper grouped by hand (requirement, 2026-10-02) -----------------------------------------------------------

    def plan_sides(self, db, message_id):
        """The side count Papers to check plans the scan at: the latest grouping's (a paper grouped by hand records the count it was
        held at), else the count the scan's labels suggest."""
        run = db.execute("SELECT sides FROM scan_sequence_runs WHERE message_id=? AND undone_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 1",
                         (message_id,)).fetchone()
        return run["sides"] if run else self.summary(db, message_id)["suggested_sides"]

    def held_choice(self, db, message_id, exam, span, held):
        """Whether a held paper can be grouped by hand, as (choice, None) or (None, why not). `span` is its scanned sides in scan order
        (the stretch from its label to the next one), `held` which packet holds each page. The choice's `fingerprint` covers what the
        instructor is shown of those sides, so a paper that changed after it was shown is never grouped from an old look. Never on a
        scan deleted from the Inbox (INBOX: PRESENT), nor for a copy that already has a reviewed paper: that paper is changed on
        Review, and pages grouped beside it would be left out of it unseen."""
        if not db.execute(f"SELECT {PRESENT.format('?')}", (message_id,)).fetchone()[0]:
            return None, "This scan was deleted from the Inbox. Restore it first."
        first = next((row for row in span if row["id"] == exam["anchor_scan_page_id"]), None)
        if first is None or first["status"] not in ANCHORS or not first["copy_id"] or first["page_number"] != 1:
            return None, "Its label is not a page 1 label, so it does not say where a paper starts."
        if not self._current(first):
            return None, "Its label belongs to cancelled, unissued or superseded paper."
        if any(held.get(row["id"]) for row in span):
            return None, "Some of its pages are in a grouped paper. Undo that grouping first."
        if db.execute("SELECT 1 FROM assembly_revisions WHERE printed_copy_id=? LIMIT 1", (first["copy_id"],)).fetchone():
            return None, f"Copy {first['copy_number']} already has a reviewed paper: change it on Review."
        fingerprint = sha(encoded({"message_id": message_id, "anchor": first["id"], "copy_id": first["copy_id"],
                                   "pages": [[row["id"], row["status"], row["page_stamp_id"], row["review_state"], row["content_sha256"]] for row in span]}).encode())
        return {"fingerprint": fingerprint, "copy_number": first["copy_number"], "sides": len(span),
                "pages_issued": None if stamps_only(first) else first["page_count"]}, None

    @staticmethod
    def _held_run(db, message_id, request_id):
        """The grouping by hand this request already made (the same `request_id` again), or None."""
        if not request_id:
            return None
        run = db.execute("""SELECT r.id,r.summary_json,k.id packet_id FROM scan_sequence_runs r LEFT JOIN scan_packets k ON k.run_id=r.id
                            WHERE r.message_id=? AND json_extract(r.summary_json,'$.origin')=? AND json_extract(r.summary_json,'$.request_id')=?""",
                         (message_id, HELD_PAPER, request_id)).fetchone()
        if not run:
            return None
        summary = json.loads(run["summary_json"])
        return {"run_id": run["id"], "packet_id": run["packet_id"], "copy_number": summary["copy_number"], "pages": len(summary["sides_chosen"]),
                "identified": summary["pages"], "repeated": True}

    def group_held(self, message_id, data, actor="workspace"):
        """Group one paper Papers to check holds, by hand (requirement, 2026-10-02). `scan_page_ids` are the ticked sides, its labeled first page first
        and the rest in scan order: the n-th becomes page n of the copy that label names. Blank sides keep their place and take no
        identity; a side labeled with its own page must be that page; unticked sides stay as scanned, retained and unidentified. It is
        the instructor's explicit choice, never an inference (DOCUMENTS "never guess"): it is checked against exactly what they were
        shown (`fingerprint`; 409 when the paper changed), under the lifecycle lock and every owner's lock the identities touch, and
        makes one run (origin `held_paper`, undone like any run), one packet, the identities by barcode intake's duplicate and
        changed-content rules, and a `held_paper_grouped` Inbox event with the reason. The same `request_id` again returns what it
        made."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        first, chosen, fingerprint, request_id = data.get("first_scan_page_id"), data.get("scan_page_ids"), data.get("fingerprint"), data.get("request_id")
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 200):
            raise ValueError("A request ID is 1–200 characters.")
        if not isinstance(first, str) or not isinstance(fingerprint, str):
            raise ValueError("Choose the held paper to group, as it was shown.")
        if not isinstance(chosen, list) or not all(isinstance(item, str) for item in chosen) or len(set(chosen)) != len(chosen) \
                or not chosen or chosen[0] != first or len(chosen) > MAX_SIDES:
            raise ValueError("Tick the paper's sides in order, its labeled first page first.")
        if len(chosen) < 2:
            raise ValueError("Tick at least one side besides its first page. To keep the first page alone, approve it as a partial paper on Review.")
        reason = text_field(data.get("reason"), "Reason", 1000)
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                self._job(db, message_id)
                done = self._held_run(db, message_id, request_id)
                if done:
                    return done
                # Identities of this copy's pages can flag changed content on other scans too: hold each of their owners first.
                owners = {message_id, *(row[0] for row in db.execute(
                    """SELECT DISTINCT sp.message_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                       WHERE p.printed_copy_id=(SELECT p1.printed_copy_id FROM scan_pages a JOIN page_stamps p1 ON p1.id=a.page_stamp_id WHERE a.id=?)""", (first,)))}
            for owner in sorted(owners):
                locks.enter_context(self.documents.files.owner_lock(owner))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                job = self._job(db, message_id)
                done = self._held_run(db, message_id, request_id)
                if done:
                    return done
                if not db.execute(f"SELECT {PRESENT.format('?')}", (message_id,)).fetchone()[0]:
                    raise Conflict("This scan was deleted from the Inbox. Restore it first.")
                rows = self._rows(db, message_id)
                sides = self.plan_sides(db, message_id)
                plan = self._plan(db, message_id, sides, turn=True, rows=rows) if sides else {"exams": []}
                exam = next((item for item in plan["exams"] if item["anchor_scan_page_id"] == first), None)
                if exam is None or exam.get("by_hand") or not exam["problems"]:
                    raise Conflict("That paper is no longer held for you. Reload Papers to check.")
                _, held = self._packets(db, message_id)
                at = {(row["source_artifact_id"], row["source_page_index"]): row for row in rows}
                span = [at[(exam["attachment"], index)] for index in range(exam["first_page"], exam["last_page"] + 1)]
                choice, refusal = self.held_choice(db, message_id, exam, span, held)
                if refusal:
                    raise Conflict(refusal)
                if choice["fingerprint"] != fingerprint:
                    raise Conflict("This paper changed after it was shown. Reload Papers to check and choose its sides again.")
                by_id = {row["id"]: row for row in span}
                if any(item not in by_id for item in chosen):
                    raise ValueError("Tick sides of this paper only.")
                anchor, rest = by_id[first], [by_id[item] for item in chosen[1:]]
                if [row["source_page_index"] for row in rest] != sorted(row["source_page_index"] for row in rest):
                    raise ValueError("Tick its sides in the order they were scanned.")
                copy, issued = anchor["copy_number"], choice["pages_issued"]
                # Only a written side takes a page: a blank side (a duplex back) keeps its place past the pages issued, as in any plan.
                last, row = max(((side, row) for side, row in enumerate([anchor, *rest], start=1) if row["status"] != "blank"), key=lambda item: item[0])
                if issued is not None and last > issued:
                    raise Conflict(f"Copy {copy} was issued {issued} page{'' if issued == 1 else 's'}, so attachment page {row['source_page_index']}, "
                                   f"which is written, cannot be its page {last}. Untick {last - issued} side{'' if last - issued == 1 else 's'}.")
                stamps = {row["page_number"]: dict(row) for row in db.execute("SELECT id,page_number,state FROM page_stamps WHERE printed_copy_id=?", (anchor["copy_id"],))}
                pages, items = [], []
                for side, row in enumerate([anchor, *rest], start=1):
                    pages.append({"scan_page_id": row["id"], "page": row["source_page_index"], "side": side})
                    where = f"Attachment page {row['source_page_index']}"
                    if side == 1 or row["status"] == "blank":
                        continue  # its label, or a blank side that keeps its place and takes no identity
                    if set_aside(row):
                        raise Conflict(f"{where} is set aside as part of no paper: leave it unticked, or undo setting it aside first.")
                    if row["status"] in ANCHORS:
                        if row["copy_id"] != anchor["copy_id"] or row["page_number"] != side:
                            raise Conflict(f"{where} carries the label of page {row['page_number']}, so it cannot be page {side} of copy {copy}.")
                        continue  # labeled with its own page already
                    if row["status"] != "unreadable" or row["page_stamp_id"]:
                        raise Conflict(f"{where} has a label that needs a look first.")
                    item = {"scan_page_id": row["id"], "source_page_index": row["source_page_index"], "side": side, "page_number": side,
                            "content_sha256": row["content_sha256"]}
                    stamp = stamps.get(side)
                    if stamp is None and choice["pages_issued"] is None:
                        item["issue"] = True  # a stamps-only copy was issued page 1 alone: the others are reserved (migration 008)
                    elif stamp is None or stamp["state"] != "issued":
                        raise Conflict(f"Page {side} of copy {copy} has no issued identity.")
                    else:
                        item["page_stamp_id"] = stamp["id"]
                        if db.execute(f"""SELECT 1 FROM scan_pages WHERE page_stamp_id=? AND id!=? AND content_sha256!=?
                                           AND review_state NOT IN ('rejected','ignored') AND {PRESENT.format('scan_pages.message_id')} LIMIT 1""",
                                      (stamp["id"], row["id"], row["content_sha256"])).fetchone():
                            raise Conflict(f"Page {side} of copy {copy} was already scanned with different content. Settle that paper on Review first.")
                    items.append(item)
                run_id, packet_id, when = str(uuid.uuid4()), str(uuid.uuid4()), timestamp()
                summary = {"exams": 1, "packets": 1, "pages": len(items), "review": 0, "unlabeled": 0, "automatic": False, "origin": HELD_PAPER,
                           "copy_number": copy, "sides_chosen": [page["page"] for page in pages], "sides_scanned": len(span), "reason": reason,
                           **({"request_id": request_id} if request_id else {})}
                db.execute("INSERT INTO scan_sequence_runs(id,message_id,sides,plan_sha256,summary_json,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                           (run_id, message_id, sides, choice["fingerprint"], encoded(summary), actor, when))
                db.execute("INSERT INTO scan_packets VALUES(?,?,?,?,?,?,?)", (packet_id, run_id, message_id, first, anchor["copy_id"], len(pages), when))
                db.executemany("INSERT INTO scan_packet_pages VALUES(?,?,?)", [(packet_id, page["scan_page_id"], page["side"]) for page in pages])
                for item in items:
                    if item.get("issue"):
                        item["page_stamp_id"] = self.documents.issue_sequence_stamp(db, anchor["copy_id"], item["page_number"], when)
                    self._identify(db, item, when)
                    db.execute("INSERT INTO scan_sequence_pages VALUES(?,?,?,?,?,?)", (run_id, item["scan_page_id"], first, item["page_stamp_id"], item["side"], when))
                self.inbox.event(db, job["id"], "held_paper_grouped", encoded({
                    "actor": actor, "run_id": run_id, "packet_id": packet_id, "copy": copy, "printed_copy_id": anchor["copy_id"],
                    "pages": [{"page": page["page"], "side": page["side"]} for page in pages], "identified": len(items),
                    "left_as_scanned": [row["source_page_index"] for row in span if row["id"] not in set(chosen)], "reason": reason,
                    **({"request_id": request_id} if request_id else {})}))
        return {"run_id": run_id, "packet_id": packet_id, "copy_number": copy, "pages": len(pages), "identified": len(items), "repeated": False}

    def packet_pdf(self, packet_id):
        """One student's paper as it was scanned, in order. Pages are read through the verified Inbox storage manager."""
        with connect(self.path) as db:
            packet = db.execute("""SELECT k.id,c.copy_number,a.title FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id
                                   JOIN printed_copies c ON c.id=k.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                                   JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN assignments a ON a.id=v.assignment_id
                                   WHERE k.id=? AND r.undone_at IS NULL""", (packet_id,)).fetchone()
            if not packet:
                raise NotFound("That grouped paper was not found.")
            artifacts = [row[0] for row in db.execute("SELECT sp.page_artifact_id FROM scan_packet_pages q JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE q.packet_id=? ORDER BY q.side", (packet_id,))]
        from . import process
        name = "".join(char if char.isalnum() or char in " -_" else "-" for char in packet["title"]).strip() or "exam"
        return process.merge_pages([self.inbox.read_artifact(artifact)[1] for artifact in artifacts]), f"{name} - copy {packet['copy_number']}.pdf"

    def undo(self, message_id, run_id, actor="workspace"):
        with connect(self.path) as db:
            # The run's pages may include the first pages of the next stack's scan (`_continuation`): their owner is locked too.
            owners = {message_id, *(row[0] for row in db.execute(
                "SELECT DISTINCT sp.message_id FROM scan_sequence_pages q JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE q.run_id=?", (run_id,)))}
        with self.rosters.lock(), ExitStack() as locks, connect(self.path) as db:
            for owner in sorted(owners):
                locks.enter_context(self.documents.files.owner_lock(owner))
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, message_id)
            run = db.execute("SELECT * FROM scan_sequence_runs WHERE id=? AND message_id=?", (run_id, message_id)).fetchone()
            if not run:
                raise NotFound("That identification run was not found.")
            if run["undone_at"]:
                raise Conflict("That run was already undone.")
            summary = json.loads(run["summary_json"] or "{}")
            if summary.get("origin") == HELD_PAPER and db.execute(
                    """SELECT 1 FROM scan_packets k JOIN scan_packet_pages q ON q.packet_id=k.id JOIN assembly_pages ap ON ap.scan_page_id=q.scan_page_id
                       WHERE k.run_id=? LIMIT 1""", (run_id,)).fetchone():
                # A paper grouped by hand that became a reviewed paper stays grouped: undoing it would retire the packet while the
                # reviewed paper keeps its pages, and hold for grouping a paper that is already reviewed (requirement, 2026-10-02).
                raise Conflict(f"Copy {summary.get('copy_number')} is already a reviewed paper with these pages, so grouping it by hand "
                               "cannot be undone here. Change that paper on Review.")
            when, cleared, kept = timestamp(), 0, []
            for row in db.execute(f"""SELECT q.scan_page_id,q.page_stamp_id,sp.source_page_index,sp.page_stamp_id held,sp.status,
                                            EXISTS(SELECT 1 FROM assembly_pages ap WHERE ap.scan_page_id=sp.id) reviewed,
                                            EXISTS(SELECT 1 FROM scan_pages d WHERE d.duplicate_of_scan_page_id=sp.id AND {PRESENT.format('d.message_id')}) repeated
                                     FROM scan_sequence_pages q JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE q.run_id=? ORDER BY sp.source_page_index""", (run_id,)).fetchall():
                if row["reviewed"]:
                    kept.append({"page": row["source_page_index"], "reason": "in a reviewed assembly"})
                elif row["held"] != row["page_stamp_id"] or row["status"] != "manual":
                    kept.append({"page": row["source_page_index"], "reason": "corrected since"})
                elif row["repeated"]:
                    kept.append({"page": row["source_page_index"], "reason": "a later scan repeats it"})
                else:
                    db.execute("UPDATE scan_pages SET page_stamp_id=NULL,status='unreadable',review_state='candidate',duplicate_of_scan_page_id=NULL,updated_at=? WHERE id=?",
                               (when, row["scan_page_id"]))
                    cleared += 1
            retired = db.execute("SELECT COUNT(*) FROM scan_packets WHERE run_id=?", (run_id,)).fetchone()[0]
            result = {"cleared": cleared, "kept": kept, "packets": retired}
            db.execute("UPDATE scan_sequence_runs SET undone_at=?,undo_json=? WHERE id=?", (when, encoded(result), run_id))
            self.inbox.event(db, job["id"], "sequence_page_identification_undone", encoded({"actor": actor, "run_id": run_id, **result}))
        return self.scans.review_item(message_id)
