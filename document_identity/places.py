"""A page whose code did not read, known by its place between its own copy's labels (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS").

Requirement, 2026-11-14, of a late Take Home Assignment (copy 60, printed one-sided on coloured paper, scanned two-sided) whose
page 2 had both corner codes covered by the student's writing, so that the scan's Papers stage asked them to "Group the pages into
papers" (they: "I don't think that is the actual issue"; it "has happened before and the grouping button misleads"). Asked whether
Faculty should decide by itself that such a side is its copy's page, and set aside the empty backs inside that copy's paper with a
note naming it, they answered Yes to both.

On paper labeled on every page (not a stamps-only label sheet, current paper), in one file of a scan:
- A PLACE is the run of sides between two labeled pages of the same copy (page a, then a later page b, nothing but blank or unread
  sides between them), or after a copy's last labeled page in the file with nothing labeled after it. Its unread sides (no code,
  no identity, not set aside, in no grouped or reviewed paper, no bubble sheet, read by no first-page search) are that copy's.
  Only paper that shows labels on its later pages has places (a code of a page after page 1 read around it, or of that print job
  on any scan in the Inbox): paper whose later pages were printed bare is grouped by its first-page labels instead. And only a
  file scanned IN PAPER ORDER has places (`in_paper_order`; the instructor, the same day: "I may scan them shuffled assuming that
  the data matrix will sort them out"): in a shuffled stack nothing is placed, and Papers names the pages without a code.
- A PAPER'S LAST BACK (the same day, "Yes, build it"): in such a file of one-sided paper scanned two-sided, the side without a code
  right after a copy's labeled page, before the next copy's labels, is the back of that page's sheet and is set aside the same way
  (42 on the kept scans, every one set aside by the instructor by hand on 2026-11-11).
- The copy's MISSING pages there are a+1 .. b-1 (or a+1 .. its page count after its last label) that no scan still in the Inbox has
  read, each with an issued identity. A copy with a reviewed paper is changed on Review instead and is left alone.
- A side can CARRY A PRINTED PAGE when it holds at least `media.PRINTED_SHARE` ink (a tenth of the least printed page measured) and
  is no back of one-sided paper: in a file of an even number of sides whose labeled pages, at least two of them, all lie on odd
  sides (fronts), the even sides are backs.
- When the sides that can carry a printed page are exactly as many as the missing pages, each takes the next missing page, as a
  recovered label gives it (status `manual`, the intake's duplicate and changed-content rule), and the place's other unread sides
  are that paper's backs: each is set aside with a reason naming the paper (actor `automatic`, review state `ignored`, the
  Inbox event `page_set_aside` the instructor's own button writes, so Papers lists it with its Undo). A back the instructor took
  back (`page_set_aside_undone`) is never set aside again. Anything else is HELD: nothing is written, and the scan's Papers stage
  names the page that did not read and where it lies (`ScanService.overview`), never a grouping.
- Replayed on the 102 scans kept on 2026-11-14 (19,096 sides): it gives one page (copy 60's page 2) and sets aside two backs on
  the scans in the Inbox; with the backs the instructor had set aside counted as they were, 55 backs of which they had set aside 53
  by hand (the other 2 are copy 60's) and none became a page; hiding each of the 308 codes that read in such a place, it put back
  307 right, 0 wrong, and held 1 (its page was also read in another scan).

Each pass is one Inbox event `pages_by_place` naming every page given and every back set aside. Run by the Inbox worker after a
scan's inspection and again whenever the scan changes (`GradingService.process_scan_one`); a paper made whole after its scan's
review is assembled by the worker's catch-up (`assemble_later_one`).
"""
import io
import json
import logging
import threading
from collections import OrderedDict
from contextlib import ExitStack

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha
from . import media

LOG = logging.getLogger(__name__)

RULE = "pages-by-place-v1"
ANCHORS = ("resolved", "manual")
# Each side's ink by its retained picture, which never changes (PERFORMANCE: a bounded process-wide cache of immutable rows). Only
# sides in a place are measured, and a place that still misses a page is read again by every view of its scan.
_INKS, _INKS_LOCK, INKS_KEPT = OrderedDict(), threading.Lock(), 512


class PlaceService:
    def __init__(self, scans):
        self.scans = scans

    # ----- reading ------------------------------------------------------------------------

    def places(self, db, message_id, shuffled=None, last_backs=None):
        """The scan's places (read only, no pictures): [{copy_id, copy_number, file, after, before, sides, aside, missing, one_sided,
        key}]. `after`/`before`: the labeled sides around it ({side, page}; `before` None after a copy's last label); `sides`: its
        unread sides in order ({id, side, raster}); `missing`: [(page number, stamp id)]; `key`: what the place is made of, so a
        decision taken on it can be checked again under the locks.

        A file NOT IN PAPER ORDER has no places (`in_paper_order`; requirement, 2026-11-14). Given a list as `shuffled`, each such file holding paper that shows labels on its later
        pages and sides without a code is added to it: {file, files, first_side, last_side, sides, missing [(copy number, [pages])]}.

        A PAPER'S LAST BACK (requirement, 2026-11-14): given a list as `last_backs`, each side without a code that is
        the back of a copy's labeled page and lies before the next copy's labels, in a file of one-sided paper scanned two-sided and
        in paper order, is added to it: {id, side, copy_number, page}. It is the back of that page's own sheet, never a printed page."""
        # Most scans hold no paper labeled on every page (stamps-only exams), or no side without a code: one indexed look says so.
        if not db.execute("""SELECT 1 FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                             JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                             WHERE sp.message_id=? AND sp.status IN ('resolved','manual') AND b.label_mode='every_page'
                               AND COALESCE(json_extract(v.compiler_json,'$.generator'),'')!='faculty-stamps-only'
                               AND EXISTS(SELECT 1 FROM scan_pages u WHERE u.message_id=sp.message_id AND u.status='unreadable'
                                          AND u.page_stamp_id IS NULL AND u.review_state!='ignored') LIMIT 1""", (message_id,)).fetchone():
            return []
        sequence = self.scans.sequence
        rows = sequence._rows(db, message_id)
        if not any(row["status"] == "unreadable" and not row["page_stamp_id"] for row in rows):
            return []
        sheets_of = {row["id"]: row["sheet"] for pages in sequence._attachments(db, message_id, rows) for row in pages}
        grouped = {row[0] for row in db.execute("""SELECT q.scan_page_id FROM scan_packet_pages q JOIN scan_packets k ON k.id=q.packet_id
                   JOIN scan_sequence_runs n ON n.id=k.run_id WHERE k.message_id=? AND n.undone_at IS NULL""", (message_id,))}
        files = self.scans._file_numbers(db, message_id)
        printed_later = {}
        attachments = {}
        for row in rows:
            attachments.setdefault(row["source_artifact_id"], []).append(row)
        found = []
        for artifact, pages in attachments.items():
            position = {row["id"]: index for index, row in enumerate(pages)}
            labeled = [row for row in pages if row["status"] in ANCHORS and row["copy_id"] and not sheets_of.get(row["id"])]
            if not self.in_paper_order(labeled):
                if shuffled is not None:
                    self._shuffled(db, pages, labeled, sheets_of, grouped, files, artifact, printed_later, shuffled)
                continue
            # One-sided paper scanned two-sided: every labeled page on a front, and at least two of them to say so.
            ordered = [row["source_page_index"] for row in pages] == list(range(1, len(pages) + 1))
            one_sided = ordered and len(pages) % 2 == 0 and len(labeled) >= 2 and all(row["source_page_index"] % 2 for row in labeled)
            if one_sided and last_backs is not None:
                for a, b in zip(labeled, labeled[1:]):
                    back = pages[position[a["id"]] + 1]
                    if a["copy_id"] != b["copy_id"] and back is not b and back["status"] == "unreadable" and back["review_state"] != "ignored" \
                            and self._quiet(back, sheets_of, grouped) and self._usable(a) \
                            and (a["status"] == "resolved" and a["page_number"] > 1
                                 or printed_later.setdefault(a["copy_id"], self._prints_later_labels(db, a["copy_id"]))):
                        last_backs.append({"id": back["id"], "side": back["source_page_index"], "copy_number": a["copy_number"], "page": a["page_number"]})
            spans = [(a, b) for a, b in zip(labeled, labeled[1:]) if a["copy_id"] == b["copy_id"]]
            if labeled and labeled[-1] is not pages[-1]:
                spans.append((labeled[-1], None))
            for a, b in spans:
                if not all(self._usable(row) for row in (a, b) if row) or b is not None and (b["page_number"] or 0) <= (a["page_number"] or 0):
                    continue
                # Only paper that shows a label on its later pages: a code of a page after page 1 read around the place, or of that
                # print job on any scan. Paper with its later pages printed bare is grouped by its first-page labels instead.
                if not any(row and row["status"] == "resolved" and row["page_number"] > 1 for row in (a, b)) \
                        and not printed_later.setdefault(a["copy_id"], self._prints_later_labels(db, a["copy_id"])):
                    continue
                between = pages[position[a["id"]] + 1:position[b["id"]] if b is not None else len(pages)]
                if not between or any(not self._quiet(row, sheets_of, grouped) for row in between):
                    continue  # something else was read between them, or a side already belongs to a paper or a sheet
                sides = [row for row in between if row["status"] == "unreadable" and row["review_state"] != "ignored"]
                if not sides:
                    continue
                last = b["page_number"] if b is not None else (a["page_count"] or 0) + 1
                missing = self._missing(db, a["copy_id"], a["page_number"] + 1, last)
                if missing is None:
                    continue  # the copy has a reviewed paper, or a page without an issued identity
                found.append({"copy_id": a["copy_id"], "copy_number": a["copy_number"], "file": files.get(artifact, 1), "files": len(files),
                              "first_side": between[0]["source_page_index"], "last_side": between[-1]["source_page_index"],
                              "after": {"side": a["source_page_index"], "page": a["page_number"]},
                              "before": {"side": b["source_page_index"], "page": b["page_number"]} if b is not None else None,
                              "sides": [{"id": row["id"], "side": row["source_page_index"], "content_sha256": row["content_sha256"],
                                         "raster": row["raster_artifact_id"]} for row in sides],
                              "aside": [row["source_page_index"] for row in between if row["status"] == "unreadable" and row["review_state"] == "ignored"],
                              "missing": missing, "one_sided": one_sided,
                              "key": sha(encoded([a["id"], b["id"] if b is not None else None, [(row["id"], row["status"], row["review_state"])
                                                                                             for row in between], missing]).encode())})
        return found

    @staticmethod
    def in_paper_order(labeled):
        """Whether a file's labeled pages show it was scanned in paper order: each copy's labeled pages together, with no other copy's
        label among them, and in increasing page order. A shuffled stack (or a page scanned twice) fails at once."""
        seen, last = set(), None
        for row in labeled:
            if last is not None and row["copy_id"] == last["copy_id"]:
                if (row["page_number"] or 0) <= (last["page_number"] or 0):
                    return False
            elif row["copy_id"] in seen:
                return False
            seen.add(row["copy_id"])
            last = row
        return True

    def _shuffled(self, db, pages, labeled, sheets_of, grouped, files, artifact, printed_later, found):
        """A file not in paper order (`places`): its sides without a code, and the pages its copies still miss, when it holds paper
        that shows labels on its later pages (a grouping by a side count could only misplace them)."""
        usable = [row for row in labeled if self._usable(row)]
        if not usable or not any(row["status"] == "resolved" and row["page_number"] > 1 for row in usable) and not any(
                printed_later.setdefault(row["copy_id"], self._prints_later_labels(db, row["copy_id"])) for row in usable):
            return
        sides = [row["source_page_index"] for row in pages if row["status"] == "unreadable" and row["review_state"] != "ignored"
                 and self._quiet(row, sheets_of, grouped)]
        if not sides:
            return
        missing = []
        for copy_id, number, count in dict.fromkeys((row["copy_id"], row["copy_number"], row["page_count"] or 0) for row in usable):
            pages_missing = self._missing(db, copy_id, 1, count + 1)
            if pages_missing:
                missing.append((number, [page for page, _ in pages_missing]))
        found.append({"file": files.get(artifact, 1), "files": len(files), "first_side": pages[0]["source_page_index"],
                      "last_side": pages[-1]["source_page_index"], "sides": sides, "missing": sorted(missing)})

    @staticmethod
    def _usable(row):
        """A labeled page that can bound a place: current paper labeled on every page (not a stamps-only label sheet), not in conflict."""
        return (row["label_mode"] == "every_page" and not json.loads(row["compiler_json"] or "{}").get("generator") == "faculty-stamps-only"
                and row["batch_status"] == "ready" and not row["superseded_at"] and row["stamp_state"] == "issued"
                and row["review_state"] not in ("conflict", "rejected") and row["page_number"])

    @staticmethod
    def _prints_later_labels(db, copy_id):
        """Whether a code of a page after page 1 of this copy's print job was read on a scan in the Inbox."""
        return bool(db.execute(f"""SELECT 1 FROM printed_copies c JOIN printed_copies o ON o.print_batch_id=c.print_batch_id
                JOIN page_stamps p ON p.printed_copy_id=o.id JOIN scan_pages sp ON sp.page_stamp_id=p.id
                WHERE c.id=? AND p.page_number>1 AND sp.status='resolved' AND {PRESENT.format('sp.message_id')} LIMIT 1""", (copy_id,)).fetchone())

    @staticmethod
    def _quiet(row, sheets_of, grouped):
        """A side a place may hold: blank, or unread with no identity, in no grouped or reviewed paper, no bubble sheet and no first
        page found on paper printed without labels."""
        if row["status"] == "blank":
            return not sheets_of.get(row["id"])
        return (row["status"] == "unreadable" and not row["page_stamp_id"] and not sheets_of.get(row["id"]) and row["id"] not in grouped
                and not row["reviewed"] and not row["found_first"])

    @staticmethod
    def _missing(db, copy_id, first, last):
        """[(page, stamp id)] of the copy's pages first .. last-1 that no scan in the Inbox has read; None when the copy has a
        reviewed paper or one of those pages has no issued identity."""
        if db.execute("SELECT 1 FROM assembly_revisions WHERE printed_copy_id=? LIMIT 1", (copy_id,)).fetchone():
            return None
        read = {row[0] for row in db.execute(f"""SELECT DISTINCT p.page_number FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id
                    WHERE p.printed_copy_id=? AND sp.status IN ('resolved','manual') AND sp.review_state!='rejected'
                    AND {PRESENT.format('sp.message_id')}""", (copy_id,))}
        stamps = {row["page_number"]: row for row in db.execute("SELECT id,page_number,state FROM page_stamps WHERE printed_copy_id=?", (copy_id,))}
        missing = []
        for page in range(first, last):
            if page in read:
                continue
            stamp = stamps.get(page)
            if not stamp or stamp["state"] != "issued":
                return None
            missing.append((page, stamp["id"]))
        return missing

    def decide(self, db, message_id, place, inks):
        """(given [(side, page, stamp id)], backs [side], printed) for one place, `printed` being how many of its sides can carry a
        printed page; nothing is given or set aside unless that is exactly how many pages it misses. `inks`: `media.ink_share` by side."""
        printed = [side for side in place["sides"] if (not place["one_sided"] or side["side"] % 2) and inks[side["id"]] >= media.PRINTED_SHARE]
        if len(printed) != len(place["missing"]):
            return [], [], len(printed)
        undone = self._undone(db, message_id)
        given = [(side, page, stamp) for side, (page, stamp) in zip(printed, place["missing"])]
        backs = [side for side in place["sides"] if side not in printed and side["id"] not in undone]
        return given, backs, len(printed)

    @staticmethod
    def _undone(db, message_id):
        """The scan's pages the instructor took back after they were set aside: Faculty never sets them aside again."""
        return {json.loads(row[0]).get("scan_page_id") for row in db.execute(
            """SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id WHERE j.message_id=? AND e.kind='page_set_aside_undone'""", (message_id,))}

    def measure(self, places):
        """Each unread side's ink (`media.ink_share`) from its retained picture, by its picture's id (kept, bounded)."""
        from PIL import Image
        inks = {}
        for side in (side for place in places for side in place["sides"]):
            with _INKS_LOCK:
                value = _INKS.get(side["raster"])
                if value is not None:
                    _INKS.move_to_end(side["raster"])
            if value is None:
                value = media.ink_share(Image.open(io.BytesIO(self.scans.inbox.read_artifact(side["raster"])[1])))
                with _INKS_LOCK:
                    _INKS[side["raster"]] = value
                    while len(_INKS) > INKS_KEPT:
                        _INKS.popitem(last=False)
            inks[side["id"]] = value
        return inks

    # ----- writing ------------------------------------------------------------------------

    def apply(self, message_id, actor="automatic"):
        """Give each place's pages and set aside its backs where the rule decides (module docstring). Returns {identified, set_aside,
        held}. A place that changed between the reading of its pictures and the write is left for the next pass."""
        with connect(self.scans.path) as db:
            message = db.execute(f"""SELECT m.authenticated,{PRESENT.format('m.id')} present,j.action FROM inbox_messages m
                                     JOIN inbox_jobs j ON j.message_id=m.id WHERE m.id=?""", (message_id,)).fetchone()
            if not message or not message["authenticated"] or not message["present"] or message["action"] != "scan.receive":
                return {"identified": 0, "set_aside": 0, "held": 0}
            last_backs = []
            places = self.places(db, message_id, last_backs=last_backs)
            if not places and not last_backs:
                return {"identified": 0, "set_aside": 0, "held": 0}
        inks = self.measure(places)
        from .scans import ScanService  # the one intake rule for a page given an identity after intake; scans imports this module
        identified, aside, held = [], [], 0
        with self.scans.rosters.lock(), ExitStack() as locks:
            locks.enter_context(self.scans.documents.files.owner_lock(message_id))
            with connect(self.scans.path) as db:
                db.execute("BEGIN IMMEDIATE")
                last_backs = []
                now = {place["key"]: place for place in self.places(db, message_id, last_backs=last_backs)}
                job_id = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()[0]
                when = timestamp()
                for place in places:
                    if place["key"] not in now or any(side["id"] not in inks for side in place["sides"]):
                        continue  # changed meanwhile: the next pass reads it again
                    given, backs, printed = self.decide(db, message_id, place, inks)
                    if printed != len(place["missing"]):
                        held += 1
                        continue
                    where = self.where(place)
                    for side, page, stamp in given:
                        review_state, duplicate = ScanService._identity_state(db, side["id"], stamp, side["content_sha256"], when)
                        if db.execute("""UPDATE scan_pages SET page_stamp_id=?,status='manual',review_state=?,duplicate_of_scan_page_id=?,updated_at=?
                                         WHERE id=? AND status='unreadable' AND page_stamp_id IS NULL AND review_state='candidate'""",
                                      (stamp, review_state, duplicate, when, side["id"])).rowcount == 1:
                            identified.append({"scan_page_id": side["id"], "page": side["side"], "copy_number": place["copy_number"],
                                               "page_number": page, "between": where, "ink": round(inks[side["id"]], 6)})
                    for side in backs:
                        reason_text = (f"A back of copy {place['copy_number']}'s paper ({where}), with no printed page on it: set aside by "
                                       f"Faculty (DOCUMENTS \"A PAGE BETWEEN ITS OWN LABELS\").")
                        if db.execute("""UPDATE scan_pages SET review_state='ignored',updated_at=? WHERE id=? AND status='unreadable'
                                         AND page_stamp_id IS NULL AND review_state='candidate'""", (when, side["id"])).rowcount == 1:
                            self.scans.inbox.event(db, job_id, "page_set_aside", encoded({
                                "actor": actor, "kind": "page_set_aside", "scan_page_id": side["id"], "page": side["side"], "reason": reason_text,
                                "previous": "candidate", "by_place": RULE}))
                            aside.append({"scan_page_id": side["id"], "page": side["side"], "copy_number": place["copy_number"],
                                          "ink": round(inks[side["id"]], 6)})
                # A PAPER'S LAST BACK: read again under the locks, so a side changed meanwhile is simply not among them.
                undone = self._undone(db, message_id) if last_backs else set()
                for back in last_backs:
                    if back["id"] in undone:
                        continue
                    reason_text = (f"A back of copy {back['copy_number']}'s paper (the back of its page {back['page']}), with no printed page on it: "
                                   f"set aside by Faculty (DOCUMENTS \"A PAGE BETWEEN ITS OWN LABELS\").")
                    if db.execute("""UPDATE scan_pages SET review_state='ignored',updated_at=? WHERE id=? AND status='unreadable'
                                     AND page_stamp_id IS NULL AND review_state='candidate'""", (when, back["id"])).rowcount == 1:
                        self.scans.inbox.event(db, job_id, "page_set_aside", encoded({
                            "actor": actor, "kind": "page_set_aside", "scan_page_id": back["id"], "page": back["side"], "reason": reason_text,
                            "previous": "candidate", "by_place": RULE}))
                        aside.append({"scan_page_id": back["id"], "page": back["side"], "copy_number": back["copy_number"], "last_back": True})
                if identified or aside:
                    self.scans.inbox.event(db, job_id, "pages_by_place", encoded({"actor": actor, "rule": RULE, "identified": identified, "set_aside": aside}))
        return {"identified": len(identified), "set_aside": len(aside), "held": held}

    @staticmethod
    def where(place):
        """Where a place lies in its copy's paper: "between its pages 1 and 3", "after its page 3"."""
        return (f"between its pages {place['after']['page']} and {place['before']['page']}" if place["before"]
                else f"after its page {place['after']['page']}")

    def unread(self, db, message_id):
        """For the scan's overview (read only): the places that still miss a page, each {copy_number, file, sides, pending, text}.
        `pending`: the rule decides it and the worker has yet to write it; otherwise the text names the page that did not read, where
        it lies and why Faculty left it. A place the worker decided misses no page any more and is not listed."""
        shuffled = []
        places = [place for place in self.places(db, message_id, shuffled) if place["missing"]]
        found = []
        for item in shuffled:
            count, file = len(item["sides"]), f" of file {item['file']}" if item["files"] > 1 else ""
            listed = f"page {item['sides'][0]}" if count == 1 else f"pages {', '.join(map(str, item['sides'][:6]))}{' and more' if count > 6 else ''}"
            short = "; ".join(f"copy {number} (page{'' if len(missing) == 1 else 's'} {', '.join(map(str, missing))})" for number, missing in item["missing"][:6])
            found.append({"file": item["file"], "first_side": item["first_side"], "last_side": item["last_side"], "pending": False, "shuffled": True,
                          "text": f"{count} page{'' if count == 1 else 's'} {'carries' if count == 1 else 'carry'} no code that could be read ({listed}{file}). "
                                  f"This stack is not in paper order, so Faculty does not guess whose {'it is' if count == 1 else 'they are'}"
                                  + (f"; still missing a page: {short}{' and more' if len(item['missing']) > 6 else ''}" if short else "")
                                  + ". If one is a student's page, recover its label from its printed alias; otherwise set it aside."})
        if not places:
            return found
        inks = self.measure(places)
        for place in places:
            given, _, printed = self.decide(db, message_id, place, inks)
            pages = [page for page, _ in place["missing"]]
            named = f"page {pages[0]}" if len(pages) == 1 else f"pages {', '.join(map(str, pages[:-1]))} and {pages[-1]}"
            sides = [side["side"] for side in place["sides"]]
            listed = f"page {sides[0]}" if len(sides) == 1 else f"pages {', '.join(map(str, sides[:-1]))} and {sides[-1]}"
            file = f" of file {place['file']}" if place["files"] > 1 else ""
            where, copy = self.where(place), f"Copy {place['copy_number']}"
            decided = printed == len(place["missing"])
            if decided:
                to = [side["side"] for side, _, _ in given]
                text = (f"{copy}'s {named} did not read: Faculty is giving {'it' if len(pages) == 1 else 'them'} to "
                        f"{f'page {to[0]}' if len(to) == 1 else 'pages ' + ', '.join(map(str, to))}{file} by {'its' if len(to) == 1 else 'their'} place, {where}.")
            elif printed > len(pages):
                text = (f"{copy}'s {named} did not read (its codes may be covered or smudged). {listed[:1].upper() + listed[1:]}{file} lie "
                        f"{where}, and {printed} of them look like a printed page, so Faculty could not tell which is its {named}. Set aside "
                        f"the side{'' if printed - len(pages) == 1 else 's'} that {'is' if printed - len(pages) == 1 else 'are'} not, "
                        f"and Faculty gives the rest {'that page' if len(pages) == 1 else 'those pages'} by their place.")
            else:
                text = (f"{copy}'s {named} did not read or did not arrive: {'nothing' if not printed else f'only {printed} side'} {where} "
                        f"({listed}{file}) looks like a printed page. Look at those sides; if the page is missing from the stack, the paper "
                        f"waits for it on Review.")
            found.append({"copy_number": place["copy_number"], "file": place["file"], "first_side": place["first_side"],
                          "last_side": place["last_side"], "pending": decided, "text": text})
        return found

    def holds_every_unread_side(self, db, message_id):
        """Whether every side of the scan with no code read (not set aside, no bubble sheet) lies in a place: then no grouping by a side
        count can help, since each is a side of the copy whose labels lie around it (`ScanService.papers_to_check`)."""
        shuffled = []
        places = self.places(db, message_id, shuffled)
        if not places and not shuffled:
            return False
        mine = {side["id"] for place in places for side in place["sides"]}
        indexes = {(item["file"], side) for item in shuffled for side in item["sides"]}
        if indexes:
            files = self.scans._file_numbers(db, message_id)
            mine |= {row[0] for row in db.execute("SELECT id,source_artifact_id,source_page_index FROM scan_pages WHERE message_id=?", (message_id,))
                     if (files.get(row[1]), row[2]) in indexes}
        sheets = self.scans.bubble_detector.sheet_pages(db, message_id) if self.scans.bubble_detector else set()
        return all(row[0] in mine or row[0] in sheets for row in db.execute(
            "SELECT id FROM scan_pages WHERE message_id=? AND status='unreadable' AND page_stamp_id IS NULL AND review_state!='ignored'", (message_id,)))

    def done(self, db, message_id):
        """What the rule did on this scan and still stands, for the overview's done line: (pages given [{page, copy_number,
        page_number, between}], backs still set aside by it). Read from its `pages_by_place` events; one query when it never ran."""
        given, backs = [], 0
        for row in db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                 WHERE j.message_id=? AND e.kind='pages_by_place' ORDER BY e.rowid""", (message_id,)):
            detail = json.loads(row[0])
            for item in detail.get("identified", []):
                if db.execute("SELECT 1 FROM scan_pages WHERE id=? AND status='manual'", (item["scan_page_id"],)).fetchone():
                    given.append(item)
            backs += sum(1 for item in detail.get("set_aside", []) if db.execute(
                "SELECT 1 FROM scan_pages WHERE id=? AND status='unreadable' AND review_state='ignored'", (item["scan_page_id"],)).fetchone())
        return given, backs

