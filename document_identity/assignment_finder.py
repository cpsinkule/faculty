"""Which assignment a scan without labels belongs to, found from what is printed on it (requirement, 2026-09-27).

A scan whose pages carry no label waited for the instructor to name its assignment. The instructor asked that it be chosen
automatically when the evidence is strong, and chose the evidence:

- Papers printed without labels: the first pages must LOOK like page 1 of one assignment's blank template (Assignment Templates)
  AND carry a printed title line that only that assignment's blank template prints. Both, on several pages, and no page pointing
  at another assignment. Blanks printed with one header look alike, so between them the title decides (v3, below). The title is compared with the TEMPLATE's printed text, never with the assignment's name in Faculty:
  measured on the real scans that day, Quiz 2 prints "MATH 140 Fall 2026 Chapter 2 Quiz" and Test 1 "Common Exam I, September
  17th, Fall 2026", so neither name appears on any of 476 first pages, while the template's line appears on 335 of 338.
  Then the scan is associated with it and its first pages are read and confident students registered, as when an assignment is
  created from a scan.
- When the templates choose nothing (v4, requirement, 2026-10-26): Jev is asked which current assignment each of up to `JEV_PAGES` pages is from
  (`jev_papers.which_assignment`, both orders, as JEV MIXED SCANS asks), the scan's first page and those most like any blank. It
  proposes an assignment when no page is read as another one and enough pages are read as it; it is chosen only when the scan's
  paper pages are whole papers of that assignment's blank template length.
- Bubble sheets, which print nothing about their assignment: the one exam of a current term the calendar dates within
  `WINDOW` days up to the scan's arrival (its sittings, seating charts and linked DR bookings); when some of those have a
  bubble-sheet answer key, the one of them (2026-10-26), and only when the number of questions the sheets answer (the question
  most of them have their last mark on, as the read infers it) is its key's. Two, or none, and nothing is chosen.
  The sheets are then read for it, as when the instructor names it.

Each scan is looked at ONCE, after inspection, bubble detection and alias recovery have finished (or `ALIAS_SAMPLE` alias reads found
no printed alias, 2026-10-31), and only while nobody has
associated it, read its sheets or looked for its first pages. What was found, chosen or not, is an Inbox event
(`assignment_found`) with its evidence. The association is the ordinary one (actor `automatic`), which the instructor can change;
nothing here clears the review gate, identifies a page or grades.
"""
import io
import json
import logging
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timedelta
from scheduling.clock import CAMPUS_ZONE, ZONE_NAME, campus_today

from werkzeug.exceptions import Conflict, Forbidden, NotFound

import numpy

import faculty_ocr
from faculty_ocr import BUSY, OcrError
from rosters import connect
from bubble_sheets import reader as bubble_reader
from teach_inbox.service import PRESENT, encoded, sha

# v2 (same day): the first real test scan chose nothing. Its quiz was printed smaller than its blank and sat lower on the page, and v1
# compared the page at the blank's own size and place (0.30, under its 0.35). A page is now compared at SIZES and SHIFTS too.
# v3 (requirement, 2026-09-29): a makeup Quiz 3 paper chose nothing. Quizzes printed with one header look alike at this scale (0.55
# against Quiz 3's blank, 0.47 against Quiz 2's), so the LEAD the look needed over every other blank held on only 31 of Quiz 3's
# 309 real first pages and 8 of Quiz 2's 326. The printed title tells them apart: the stored first-page readings print "Chapter 3
# Quiz" on 309 of 309 and "Chapter 2 Quiz" on 326 of 326, never the other. So where a page prints the title of exactly one
# assignment and looks more like that assignment's blank than any other, at PAGE_SCORE, the title decides between look-alikes and
# no LEAD is needed (286 of 309 and 272 of 326 pass). A page that looks most like another blank still chooses nothing.
# v4 (requirement, 2026-10-26): Jev proposes the assignment of paper pages the templates leave undecided, chosen when the page counts fit;
# sheets need the question count of the exam's answer key.
RULE = "assignment-from-template-v4"
# Measured on the real first pages (2026-09-27), top half at 68x88, normalized, best over sizes and shifts: Quiz 2's 325 scored
# 0.36 to 0.85 against their own blank (5th percentile 0.47), 0.14 to 0.40 against Test 1's (median 0.32), leading by 0.15 or more
# at the 5th percentile; Test 1's 13 scored 0.55 or more, leading by 0.28 or more; 100 pages that are no first page (quiz backs,
# bubble sheets) never passed 0.36. A page votes for an assignment only at PAGE_SCORE and a LEAD over every other assignment.
PAGE_SCORE = .45
LEAD = .12
CANDIDATE = .40     # a page this like some template is worth reading (above every page that was no first page)
SIZES = (.88, .90, .92, .94, .96, .98, 1.0, 1.02, 1.04)   # the page's print size against its blank (Quiz 2 was printed at 96%)
SHIFTS = (-.04, -.02, 0, .02, .04)                         # where it sits, as fractions of the page
READ = 8            # pages whose printed title is read, in one model load
LOOKED = 120        # pages compared per scan, in scan order: enough to hold READ first pages of any assignment
AGREE = 3           # pages that must agree (fewer when the scan has fewer such pages)
TITLE_LINE = 12     # characters a printed line needs to name an assignment ("Math 140" names a course, not an assignment)
FIELD_WORDS = re.compile(r"\b(name|uin|instructor|section number|signature|version|directions)\b")
WINDOW = 14         # days before a bubble sheet scan's arrival in which its exam may be dated
JEV_PAGES = 6       # pages Jev is asked about when the templates choose nothing
# Alias reads done, none finding a printed alias, after which a scan is looked at without waiting for the rest (requirement, 2026-10-31:
# a 98-page scan of Exam 2 booklets and sheets waited on 60 reads of about 18 s each, and was linked to Exam 3 by hand meanwhile). On
# the live catalog that day, every alias any scan's reads found was found by its 1st or 2nd read; 27 scans' 590 reads found none.
ALIAS_SAMPLE = 8
SHEETS_READ = 40    # bubble sheets whose marks are read to count the questions they answer
TIME_ZONE = ZONE_NAME
TEMPLATE_TEXT_CACHE = 128
LOG = logging.getLogger(__name__)


def normalized(text):
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text).strip(" .,:;")


def title_lines(text):
    """The printed lines of a template's top that may name its assignment: long enough, and no field label or version line."""
    lines = []
    for line in (text or "").splitlines()[:12]:
        line = normalized(line)
        if len(line) >= TITLE_LINE and re.search(r"[a-z]", line) and not FIELD_WORDS.search(line):
            lines.append(line)
    return lines


class AssignmentFinder:
    def __init__(self, scans):
        self.scans = scans
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path
        self._template_text = OrderedDict()  # template id -> immutable first-page OCR, bounded per worker

    # ----- which scan -------------------------------------------------------------------------

    def _next(self, db):
        """One inspected scan nobody has given an assignment, whose labels and sheets are settled, and its aliases too: every alias read
        done, or `ALIAS_SAMPLE` of them done without one printed alias seen (a scan printed without labels prints no alias)."""
        detector = self.scans.bubble_detector
        for row in db.execute(f"""SELECT m.id,m.received_at,j.id job_id FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id
                JOIN document_decode_jobs d ON d.message_id=m.id
                WHERE j.action='scan.receive' AND m.authenticated=1 AND d.status='complete' AND {PRESENT.format('m.id')}
                AND NOT EXISTS(SELECT 1 FROM frq_scan_links l WHERE l.message_id=m.id)
                AND NOT EXISTS(SELECT 1 FROM bubble_runs b WHERE b.message_id=m.id)
                AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_runs u WHERE u.message_id=m.id)
                AND (NOT EXISTS(SELECT 1 FROM scan_alias_ocr a WHERE a.message_id=m.id AND a.status IN ('pending','running'))
                     OR ((SELECT COUNT(*) FROM scan_alias_ocr a WHERE a.message_id=m.id AND a.status='complete')>=?
                         AND NOT EXISTS(SELECT 1 FROM scan_alias_ocr a WHERE a.message_id=m.id AND a.status='complete'
                             AND (json_extract(a.result_json,'$.outcome')!='no_match' OR json_array_length(json_extract(a.result_json,'$.aliases'))>0))))
                AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.job_id=j.id AND e.kind='assignment_found' AND json_extract(e.detail,'$.rule')=?)
                ORDER BY m.received_at,m.id LIMIT 20""", (ALIAS_SAMPLE, RULE)):
            if detector and detector.detected(db, row["id"])["pending"]:
                continue  # Not every page was checked for being a bubble sheet yet.
            return dict(row)
        return None

    def process_one(self):
        return self._find_one() or self._read_linked_sheets()

    def _read_linked_sheets(self):
        """The bubble sheets of a scan whose labels already gave it an assignment are read for that assignment (2026-10-24: Exam 2
        scanned at home in stacks, each student's bubble sheet beside their booklet, so every stack waited for "Say whose bubble
        sheets these are"). The scan's labels associated it; no Scantron label on its sheets names another assignment. The read is
        the ordinary one the instructor would request (`BubbleSheetService.request`, actor `automatic`), once per scan: what was done
        or refused is a `linked_sheets_read` Inbox event, and the scan is not looked at again."""
        grading, detector = self.scans.grading, self.scans.bubble_detector
        if grading is None or detector is None:
            return False
        from .service import DocumentService
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                found = None
                for row in db.execute(f"""SELECT m.id,j.id job_id FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id
                        JOIN document_decode_jobs d ON d.message_id=m.id
                        WHERE j.action='scan.receive' AND m.authenticated=1 AND d.status='complete' AND {PRESENT.format('m.id')}
                        AND EXISTS(SELECT 1 FROM frq_scan_links l WHERE l.message_id=m.id)
                        AND EXISTS(SELECT 1 FROM bubble_detections x WHERE x.message_id=m.id AND x.kind='sheet')
                        AND NOT EXISTS(SELECT 1 FROM bubble_runs b WHERE b.message_id=m.id)
                        AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.job_id=j.id AND e.kind='linked_sheets_read')
                        ORDER BY m.received_at,m.id LIMIT 20""").fetchall():
                    if detector.detected(db, row["id"])["pending"]:
                        continue  # Not every page was checked for being a bubble sheet yet.
                    plan = grading.scan_status(db, row["id"], fingerprint=False)["plan"]
                    if plan:
                        found = (dict(row), plan)
                        break
                if not found:
                    return False
                scan, plan = found
                named = set()
                for (candidates,) in db.execute("SELECT candidates_json FROM scan_pages WHERE message_id=? AND candidates_json!='[]'", (scan["id"],)):
                    for item in json.loads(candidates or "[]"):
                        label = DocumentService.sheet_label(db, str(item.get("payload", "")))
                        if label:
                            named.add(label["assignment_id"])
            detail = {"assignment_id": plan["source_assignment_id"], "notes": []}
            if named - {plan["source_assignment_id"]}:
                detail["notes"].append("A Scantron label on these sheets names another assignment; choose whose sheets these are.")
            else:
                try:
                    detector.request(scan["id"], {"assignment_id": plan["source_assignment_id"]}, actor="automatic")
                except (ValueError, Conflict, Forbidden, NotFound) as error:
                    detail["notes"].append(str(error))
            with self.rosters.lock(), connect(self.path) as db:
                self.inbox.event(db, scan["job_id"], "linked_sheets_read", encoded({**detail, "read": not detail["notes"]}))
        return True

    def _find_one(self):
        grading = self.scans.grading
        if grading is None:
            return False
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                scan = self._next(db)
                if not scan:
                    return False
                recognition = self.scans.recognition(db, scan["id"])
                attached = [page for pages in self.scans.unlabeled._attachments(db, scan["id"]) for page in pages]
                papers = [page for page in attached if page["status"] == "unreadable"]
                sheets = (recognition["bubble_sheets"] or {}).get("sheets", 0)
                sheet_pages = [dict(row) for row in db.execute("""SELECT id,raster_artifact_id FROM scan_pages WHERE message_id=? AND id IN
                    (SELECT scan_page_id FROM bubble_detections WHERE message_id=? AND kind='sheet') ORDER BY source_artifact_id,source_page_index""",
                    (scan["id"], scan["id"]))] if sheets else []
                candidates = self._candidates(db)
            found = {"rule": RULE, "chosen": None, "notes": []}
            if recognition["papers"]:
                return self._record(scan, found, "Its printed labels say which assignment it is.")
            try:
                if papers:
                    found["papers"] = self._from_templates(papers, candidates)
                    ranked = found["papers"].pop("ranked", papers)
                    if not found["papers"]["chosen"] and not found["papers"].get("conflict"):
                        found["papers"] = self._from_jev(scan, found["papers"], [papers[0], *ranked], attached, candidates)
                if sheets:
                    questions = self._questions_answered(sheet_pages)
                    with connect(self.path) as db:
                        found["sheets"] = self._from_calendar(db, scan["received_at"], questions)
            except OcrError as error:
                if BUSY in str(error):
                    return False  # The reader is busy: this scan is looked at again on a later turn.
                LOG.exception("Finding the assignment of scan %s failed", scan["id"])
                return self._record(scan, found, "Reading its printed titles failed; choose its assignment.")
            except Exception:
                LOG.exception("Finding the assignment of scan %s failed", scan["id"])
                return self._record(scan, found, "Looking for its assignment failed; choose it.")
            picks = {part["chosen"]["assignment_id"]: part["chosen"] for part in (found.get("papers"), found.get("sheets")) if part and part.get("chosen")}
            if not picks:
                return self._record(scan, found, next((part["reason"] for part in (found.get("papers"), found.get("sheets")) if part), "Nothing to look at."))
            if len(picks) > 1:
                return self._record(scan, found, "Its papers and its bubble sheets point to different assignments; choose its assignment.")
            chosen = next(iter(picks.values()))
            found["chosen"] = chosen
            self._record(scan, found, None)
            self._apply(scan, chosen, papers=bool(found.get("papers", {}).get("chosen")), sheets=bool(found.get("sheets", {}).get("chosen")))
            return True

    def _record(self, scan, found, reason):
        if reason:
            found["reason"] = reason
        with self.rosters.lock(), connect(self.path) as db:
            self.inbox.event(db, scan["job_id"], "assignment_found", encoded(found))
        return True

    # ----- the evidence -----------------------------------------------------------------------

    def _candidates(self, db):
        """Assignments of courses with a current term, each with its current term and blank templates."""
        result = {}
        for row in db.execute("""SELECT a.id assignment_id,a.title,t.id term_id,x.id template_id FROM assignments a
                JOIN course_terms t ON t.definition_id=a.definition_id AND t.is_current=1 AND t.deleted_at IS NULL
                AND EXISTS(SELECT 1 FROM course_sections s WHERE s.term_id=t.id)
                LEFT JOIN assignment_templates x ON x.assignment_id=a.id ORDER BY a.id,t.id,x.created_at"""):
            item = result.setdefault(row["assignment_id"], {"assignment_id": row["assignment_id"], "title": row["title"], "terms": set(), "templates": []})
            item["terms"].add(row["term_id"])
            if row["template_id"] and row["template_id"] not in item["templates"]:
                item["templates"].append(row["template_id"])
        return result

    @staticmethod
    def _normal(values):
        values = values - values.mean()
        norm = numpy.linalg.norm(values)
        return (values / norm if norm else values).ravel()

    def _signature(self, content):
        """A blank's first page: its top half, small and normalized (the cover likeness of `unlabeled`)."""
        from PIL import Image
        return self._normal(numpy.asarray(Image.open(io.BytesIO(content)).convert("L").resize((68, 88)), dtype=float)[:44])

    def _views(self, content):
        """A scanned page as the same signature at every SIZE and SHIFT, upright and turned: one row per view."""
        from PIL import Image
        small = Image.open(io.BytesIO(content)).convert("L").resize((340, 440))
        rows = []
        for page in (small, small.rotate(180)):
            for size in SIZES:
                for dx in SHIFTS:
                    for dy in SHIFTS:
                        x, y, w, h = 170 + dx * 340, 220 + dy * 440, 340 * size, 440 * size
                        crop = page.transform((68, 88), Image.EXTENT, (x - w / 2, y - h / 2, x + w / 2, y + h / 2), fillcolor=255)
                        rows.append(self._normal(numpy.asarray(crop, dtype=float)[:44]))
        return numpy.stack(rows)

    def _template_titles(self, template_id):
        """The title lines printed at the top of a template's first page: its text layer, else read once by OCR."""
        templates = self.scans.grading.templates
        text = templates.read(template_id, "text", page=1)[0].decode("utf-8", "replace")
        if not text.strip():
            if template_id not in self._template_text:
                image = templates.read(template_id, "image", page=1)[0]
                read = faculty_ocr.recognize_covers([image], [.12, .17, .88, .36], ["name", "UIN"], title_unless="(?s).")["pages"][0]
                self._template_text[template_id] = "\n".join(item.get("text") or "" for item in read["reads"] if item["kind"] == "band" and item["rotation"] == 0)
                while len(self._template_text) > TEMPLATE_TEXT_CACHE:
                    self._template_text.popitem(last=False)
            self._template_text.move_to_end(template_id)
            text = self._template_text[template_id]
        return title_lines(text)

    def _from_templates(self, pages, candidates):
        """Page resemblance and printed title, per the module's rule. What was seen is returned as evidence either way."""
        templated = {aid: item for aid, item in candidates.items() if item["templates"]}
        if not templated:
            return {"chosen": None, "reason": "No current assignment has a blank template to compare its pages with.", "ranked": pages}
        templates = self.scans.grading.templates
        shapes = {aid: numpy.stack([self._signature(templates.read(tid, "image", page=1)[0]) for tid in item["templates"]]) for aid, item in templated.items()}
        lines = {aid: {line for tid in item["templates"] for line in self._template_titles(tid)} for aid, item in templated.items()}
        # A line counts for one assignment only when no other assignment's template prints it too.
        unique = {aid: {line for line in mine if not any(line in other for key, other in lines.items() if key != aid)} for aid, mine in lines.items()}
        scored = []
        for page in pages[:LOOKED]:
            _, content = self.inbox.read_artifact(page["raster_artifact_id"])
            views = self._views(content)
            scores = {aid: float((views @ items.T).max()) for aid, items in shapes.items()}
            scored.append((max(scores.values()), page, scores))
        scored.sort(key=lambda item: -item[0])
        ranked = [page for _, page, _ in scored]  # most like some blank first: the pages Jev is asked about when this chooses nothing
        chosen = [item for item in scored if item[0] >= CANDIDATE][:READ]
        if not chosen:
            return {"chosen": None, "reason": "Its pages do not look like any current assignment's blank template.", "pages": len(pages), "ranked": ranked}
        contents = []
        for _, page, _ in chosen:
            _, content = self.inbox.read_artifact(page["page_artifact_id"])
            if sha(content) != page["content_sha256"]:
                raise ValueError("Retained page checksum differs.")
            contents.append(content)
        reads = faculty_ocr.recognize_covers(contents, [.12, .17, .88, .36], ["name", "UIN"], title_unless="(?s).")["pages"]
        votes = []
        for (_, page, scores), read in zip(chosen, reads):
            text = normalized(" \n ".join(item.get("text") or "" for item in read["reads"] if item["kind"] == "band"))
            best = max(scores, key=scores.get)
            others = [value for key, value in scores.items() if key != best]
            titled = sorted(aid for aid, mine in unique.items() if any(line in text for line in mine))
            # The look needs its LEAD over every other blank unless the page prints the title of the assignment it looks most like (v3).
            look = best if scores[best] >= PAGE_SCORE and (scores[best] - max(others, default=0) >= LEAD or titled == [best]) else None
            votes.append({"page": page["id"], "look": look, "title": titled, "score": round(scores[best], 3)})
        named = {vote["look"] for vote in votes if vote["look"]} | {aid for vote in votes for aid in vote["title"]}
        agree = {aid: sum(vote["look"] == aid and vote["title"] == [aid] for vote in votes) for aid in named}
        evidence = {"ranked": ranked, "pages": len(pages), "read": len(votes), "votes": [{**vote, "look": templated[vote["look"]]["title"] if vote["look"] else None,
                                                                          "title": [templated[aid]["title"] for aid in vote["title"]]} for vote in votes]}
        if len(named) > 1:
            return {"chosen": None, "reason": "Its first pages point to more than one assignment; choose its assignment.", "conflict": True, **evidence}
        if not named:
            return {"chosen": None, "reason": "No page both looks like a blank template and prints its title.", **evidence}
        aid = next(iter(named))
        if agree[aid] < min(AGREE, len(votes)):
            return {"chosen": None, "reason": f"Only {agree[aid]} of {len(votes)} pages both look like {templated[aid]['title']}'s blank template and print its title.", **evidence}
        terms = templated[aid]["terms"]
        if len(terms) != 1:
            return {"chosen": None, "reason": f"{templated[aid]['title']}'s course has more than one current term; choose the term.", **evidence}
        return {"chosen": {"assignment_id": aid, "term_id": next(iter(terms)), "title": templated[aid]["title"], "basis": "template",
                           "agreeing": agree[aid]}, **evidence}

    def _jev(self):
        """The workspace's Jev receipts (JEV PAPERS), when Jev is set up; None: the templates' answer stands."""
        jev = self.scans.jev
        from jev import client
        return jev if jev and client.available() else None

    def blank_description(self, template_id):
        """How an assignment's blank paper begins, for Jev: its first page's text layer, else the title lines read from it."""
        text = self.scans.grading.templates.read(template_id, "text", page=1)[0].decode("utf-8", "replace")
        return text if text.strip() else " ".join(self._template_titles(template_id))

    def _from_jev(self, scan, papers, pages, attached, candidates):
        """v4: which assignment Jev reads the pages as, chosen only when the scan's paper pages (`attached`: every page a paper may
        be among, blank backs included) are whole papers of its blank template's length. `papers`: the templates' evidence, kept."""
        jev = self._jev()
        if not jev or not candidates:
            return papers
        from . import jev_papers
        options = []
        for item in sorted(candidates.values(), key=lambda a: (a["title"].casefold(), a["assignment_id"])):
            text = ""
            if item["templates"]:
                try:
                    text = self.blank_description(item["templates"][0])
                except Exception:  # noqa: BLE001 - an assignment whose blank cannot be read is still offered by its name
                    LOG.warning("The blank paper of assignment %s could not be read for Jev", item["assignment_id"], exc_info=True)
            options.append({"id": item["assignment_id"], "title": item["title"], "description": jev_papers.describe(text)})
        asked, seen = [], set()
        for page in pages:
            if page["id"] in seen or len(asked) >= JEV_PAGES:
                continue
            seen.add(page["id"])
            try:
                _, picture = self.inbox.read_artifact(page["raster_artifact_id"])
                answer = jev_papers.which_assignment(jev, page["id"], picture, options)
            except Exception:  # noqa: BLE001 - a page Jev could not be asked about is no vote
                LOG.exception("Jev could not read which assignment page %s of scan %s is", page["id"], scan["id"])
                answer = {"status": "failed", "assignment_id": None}
            asked.append({"page": page["id"], "status": answer.get("status"), "assignment_id": answer.get("assignment_id"),
                          "none": bool(answer.get("none")), "receipt": answer.get("receipt")})
        named = {item["assignment_id"] for item in asked if item["assignment_id"]}
        titles = {aid: candidates[aid]["title"] for aid in named if aid in candidates}
        evidence = {"asked": len(asked), "votes": [{**item, "assignment": titles.get(item["assignment_id"])} for item in asked]}

        def nothing(reason):
            return {**papers, "jev": {**evidence, "reason": reason}, "reason": f"{papers['reason']} {reason}"}

        if not named:
            return nothing(f"Jev read none of {len(asked)} pages as one of the current assignments.")
        if len(named) > 1:
            return nothing(f"Jev read its pages as more than one assignment ({', '.join(sorted(titles.values()))}); choose its assignment.")
        aid = next(iter(named))
        title, agreeing = candidates[aid]["title"], sum(item["assignment_id"] == aid for item in asked)
        with connect(self.path) as db:
            sides = self.scans.unlabeled.paper_sides(db, aid)
        evidence.update(proposed=title, agreeing=agreeing, pages=len(attached), sides=sides)
        if not sides:
            return nothing(f"Jev reads it as {title}, which has no blank template, so its page count cannot be checked; choose its assignment.")
        if len(attached) % sides:
            return nothing(f"Jev reads it as {title}, but its {len(attached)} pages are not whole papers of {sides} pages; choose its assignment.")
        needed = min(AGREE, len(attached) // sides, len(asked))
        if agreeing < needed:
            return nothing(f"Jev reads only {agreeing} of {len(asked)} pages as {title}; {needed} are needed.")
        terms = candidates[aid]["terms"]
        if len(terms) != 1:
            return nothing(f"Jev reads it as {title}, whose course has more than one current term; choose the term.")
        chosen = {"assignment_id": aid, "term_id": next(iter(terms)), "title": title, "basis": "jev", "agreeing": agreeing,
                  "pages": len(attached), "sides": sides}
        return {**papers, "jev": evidence, "chosen": chosen, "reason": None}

    def _questions_answered(self, pages):
        """How many questions the sheets answer: the question most of them have their last mark on (`reader.infer_question_count`,
        as the read infers it), or None when they do not say."""
        marks = []
        for page in pages[:SHEETS_READ]:
            try:
                _, content = self.inbox.read_artifact(page["raster_artifact_id"])
                raster = bubble_reader.letter(bubble_reader.decode(content))
                if raster.shape != tuple(reversed(bubble_reader.template()["pixels"])):
                    continue
                reading = bubble_reader.read_page(raster, 100)
            except Exception:  # noqa: BLE001 - a sheet that cannot be read says nothing about the count
                LOG.warning("Bubble sheet page %s could not be read for its question count", page["id"], exc_info=True)
                continue
            if reading.get("aligned"):
                marks.append(reading.get("last_marked"))
        try:
            return bubble_reader.infer_question_count(marks)[0]
        except bubble_reader.Undecided:
            return None

    def _from_calendar(self, db, received_at, questions=None):
        """The one exam of a current term dated in the WINDOW days up to the scan's arrival (campus dates)."""
        arrived = datetime.fromisoformat(received_at).astimezone(CAMPUS_ZONE).date() if received_at else campus_today()
        start, end = (arrived - timedelta(days=WINDOW)).isoformat(), arrived.isoformat()
        current = "t.is_current=1 AND t.deleted_at IS NULL"
        found = {}
        queries = [f"""SELECT e.assignment_id,e.term_id,a.title,s.date FROM exam_sittings s JOIN exam_events e ON e.id=s.event_id
                       JOIN assignments a ON a.id=e.assignment_id JOIN course_terms t ON t.id=e.term_id WHERE {current} AND s.date BETWEEN ? AND ?""",
                   f"""SELECT c.assignment_id,c.term_id,a.title,c.exam_date date FROM seating_charts c JOIN assignments a ON a.id=c.assignment_id
                       JOIN course_terms t ON t.id=c.term_id WHERE {current} AND c.exam_date BETWEEN ? AND ?"""]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dr_exam_assignment_links'").fetchone():
            queries.append(f"""SELECT l.assignment_id,t.id term_id,a.title,json_extract(v.snapshot_json,'$.date') date FROM dr_exam_bookings b
                       JOIN dr_exam_revisions v ON v.id=b.revision_id JOIN dr_exam_assignment_links l ON l.booking_id=b.id
                       JOIN assignments a ON a.id=l.assignment_id JOIN course_sections s ON s.id=b.section_id JOIN course_terms t ON t.id=s.term_id
                       WHERE {current} AND json_extract(v.snapshot_json,'$.date') BETWEEN ? AND ?""")
        for query in queries:
            for row in db.execute(query, (start, end)):
                item = found.setdefault((row["assignment_id"], row["term_id"]), {"assignment_id": row["assignment_id"], "term_id": row["term_id"],
                                                                                 "title": row["title"], "dates": set()})
                item["dates"].add(row["date"])
        exams = []
        for item in found.values():
            key = db.execute("""SELECT k.key_json FROM frq_bubble_keys k JOIN frq_assignments f ON f.id=k.assignment_id JOIN exam_events e ON e.id=f.event_id
                                WHERE e.assignment_id=? AND e.term_id=? ORDER BY k.revision DESC LIMIT 1""", (item["assignment_id"], item["term_id"])).fetchone()
            counts = sorted({len(version.get("questions") or []) for version in json.loads(key[0]).get("versions") or []}) if key else []
            exams.append({**item, "dates": sorted(item["dates"]), "answer_key": bool(key), "key_questions": counts})
        evidence = {"arrived": arrived.isoformat(), "window_days": WINDOW, "questions": questions,
                    "exams": [{"title": item["title"], "dates": item["dates"], "answer_key": item["answer_key"], "key_questions": item["key_questions"]}
                              for item in exams]}
        # Bubble sheets are read against an answer key: when an exam in the window has one, only those that do are theirs (2026-10-26:
        # Exam 2's sheets chose nothing because four quizzes without a key had makeup sittings in the same two weeks), and only the one
        # whose key has as many questions as the sheets answer (instructor, that day: "if that assignment fits ... then it assigns that").
        keyed = [item for item in exams if item["answer_key"]]
        if keyed:
            if questions is None:
                return {"chosen": None, "reason": "The sheets do not agree on how many questions they answer, so no answer key can be checked against them; choose their assignment.", **evidence}
            fitting = [item for item in keyed if item["key_questions"] == [questions]]
            if not fitting:
                return {"chosen": None, "reason": f"The sheets answer {questions} questions, and no answer key of an exam on the calendar in the two weeks before they arrived has {questions}; choose their assignment.", **evidence}
            exams = fitting
        if len(exams) != 1:
            return {"chosen": None, "reason": ("No exam of a current term is on the calendar in the two weeks before these bubble sheets arrived; choose their assignment."
                                               if not exams else "More than one exam with a bubble-sheet answer key is on the calendar in the two weeks before these bubble sheets arrived; choose their assignment."
                                               if keyed else "More than one exam is on the calendar in the two weeks before these bubble sheets arrived; choose their assignment."), **evidence}
        exam = exams[0]
        return {"chosen": {"assignment_id": exam["assignment_id"], "term_id": exam["term_id"], "title": exam["title"], "basis": "calendar",
                           "dates": exam["dates"], **({"answer_key": True, "questions": questions} if keyed else {})}, **evidence}

    # ----- acting on it -----------------------------------------------------------------------

    def _apply(self, scan, chosen, *, papers, sheets):
        """The ordinary association (actor `automatic`), then the ordinary first-page search or sheet read. A refusal is an event."""
        notes = []
        try:
            if papers:
                self.scans.grading.associate_found(scan["id"], chosen["assignment_id"], chosen["term_id"])
            if sheets:
                # The exam's own sections when the term has it (the automatic association adds none), else the read's default.
                with connect(self.path) as db:
                    event = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (chosen["term_id"], chosen["assignment_id"])).fetchone()
                    sections = [row[0] for row in db.execute("SELECT section_id FROM exam_event_sections WHERE event_id=? ORDER BY section_id", (event[0],))] if event else None
                self.scans.bubble_detector.request(scan["id"], {"assignment_id": chosen["assignment_id"], **({"section_ids": sections} if sections else {})},
                                                   actor="automatic")
        except (ValueError, Conflict, Forbidden, NotFound) as error:
            notes.append(str(error))
        if notes:
            with self.rosters.lock(), connect(self.path) as db:
                self.inbox.event(db, scan["job_id"], "assignment_found_not_applied", encoded({"assignment_id": chosen["assignment_id"], "notes": notes}))

    @staticmethod
    def latest(db, job_id):
        """What was found for a scan, for its overview: the latest `assignment_found` event, or None."""
        row = db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind='assignment_found' ORDER BY rowid DESC LIMIT 1", (job_id,)).fetchone()
        try:
            return json.loads(row[0]) if row else None
        except (TypeError, ValueError):
            return None
