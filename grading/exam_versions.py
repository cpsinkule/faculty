"""Exam versions behind one label sheet: every paper is graded against its own version.

One stamps-only label sheet can serve several exam versions (A on white, B on yellow), so the
printed document version says nothing about which exam a student wrote. The instructor's rule:
the SEATING CHART is the source (decoded label -> printed copy -> student -> seat -> exam version).
A seated paper whose first page prints that same version is confirmed by it (FIRST PAGE, instructor
2026-10-25); any other paper is compared whole with each version's blank exam, and one with no seat whose pages and page 1 name
the same version is confirmed by both (NO SEAT, 2026-10-26). A paper that matches
another version, or none clearly, waits for the instructor; nothing here switches a version, and a
paper whose version is unresolved is never graded.
"""
import io
import json
import logging
import re
import time

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from exam_workflow.grading import GradingService as ManualGrading
from . import jev_versions, version_match
from .assignments import version
from .models import digest, encoded, exam_letter as letter, items, text, uid

GRADABLE = ("unversioned", "confirmed", "instructor")
PAPER_DOCUMENT = ManualGrading.PAPER_DOCUMENT  # the document a print job's papers count as (DR COPIES)


class OtherExamVersion(Conflict):
    """This paper is another exam version's; not an error and not listed as blocked work."""


class VersionUndecided(Conflict):
    """This paper's version is not settled (`status` says why). Blocked work like any other refusal, but the assignment can say how
    many papers wait for the instructor's version decision (2026-09-26: three makeup papers waited for one, unseen, while every
    grading step showed done)."""

    def __init__(self, description, status):
        super().__init__(description)
        self.status = status


DECIDE = ("mismatch", "unclear", "proposed", "unresolved")  # What waits for the instructor, not for a comparison still running.

# FIRST PAGE (requirement, 2026-10-25): a seated paper's page 1 is read by the local OCR where exams print their version, the title box the cover reader
# uses (`document_identity.unlabeled.TITLE_BOX`) and the footer ("Version A -- Page 1 of 14"), FIRST_PAGE_BATCH papers in one
# model load. Exactly one letter read, and the seat's, confirms the paper; anything else (another letter, two, none, a failed read)
# compares the whole paper as before. Lines that look like a seat label are skipped, since a label prints the SEAT's version.
FIRST_PAGE = "first-page-version-v1"
FIRST_PAGE_BOXES = ((.12, .17, .88, .36), (.10, .88, .90, 1.0))
FIRST_PAGE_BATCH = 24  # 48 crops of a 1700 px wide page stay under the reader's 30-megapixel image limit
NOT_NEEDED = "Not compared: page 1 prints the version the seating chart gave."
VERSION = r"version\s*[:#.\-]?\s*([A-Z])(?![A-Za-z])"  # as `document_identity.unlabeled.VERSION` reads a cover's version
LABEL_LINE = re.compile(r"seat|section|copy|·|•", re.I)
LOG = logging.getLogger(__name__)


def first_page_version(texts):
    """The one version letter printed in what was read on a first page, or "" for none or several."""
    letters = set()
    for text in texts:
        for line in str(text or "")[:2000].splitlines():
            if not LABEL_LINE.search(line):
                letters |= {found.upper() for found in re.findall(VERSION, line, re.I)}
    return next(iter(letters)) if len(letters) == 1 else ""


class ExamVersionsMixin:
    @staticmethod
    def plan_versions(db, aid):
        """Letters that have questions in this grading plan. Empty: an exam without versions, graded as before."""
        return sorted(r[0] for r in db.execute("""SELECT DISTINCT q.exam_version FROM frq_assignment_questions p
            JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=? AND q.exam_version!=''""", (aid,)))

    @staticmethod
    def chart_versions(db, plan):
        """Letters the seating charts of this exam and term handed out."""
        return sorted(r[0] for r in db.execute("""SELECT DISTINCT v.exam_version FROM seating_charts c
            JOIN seating_exam_versions v ON v.chart_id=c.id WHERE c.assignment_id=? AND c.term_id=?""",
            (plan["source_assignment_id"], plan["term_id"])))

    @staticmethod
    def declared_versions(db, plan):
        """Letters declared for a plan made from a scan (A.. up to the count). An empty list for 0 or 1, or when nothing was declared."""
        row = db.execute("SELECT versions_count FROM frq_declared_versions WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                         (plan["id"],)).fetchone()
        return list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:row[0]]) if row and row[0] >= 2 else []

    @staticmethod
    def added_versions(db, plan):
        """Letters the instructor added to this plan (2026-09-25: a makeup or another version no seating chart handed out), the latest
        row per letter counting. They only extend the list of versions: a paper's version is decided exactly as before."""
        state = {}
        for exam_version, added in db.execute("SELECT exam_version,added FROM frq_added_versions WHERE assignment_id=? ORDER BY created_at,rowid",
                                              (plan["id"],)):
            state[exam_version] = added
        return sorted(k for k, v in state.items() if v)

    def version_letters(self, db, plan):
        """Every letter this exam has in this term: handed out by a seating chart, declared for a plan made from a scan, or added."""
        return sorted(set(self.chart_versions(db, plan)) | set(self.declared_versions(db, plan)) | set(self.added_versions(db, plan)))

    def change_added_version(self, aid, data, added):
        """Add the next version letter to this term's plan before its rubrics exist, so its blank template can be chosen and its rubrics
        drafted with the others (requirement, 2026-09-25); or remove one the instructor added
        while it has no questions. Nothing else changes: seats, papers, templates and rubrics stay as they are."""
        chosen = letter(data.get("exam_version"), blank=False)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            plan = self.plan(db, aid)
            version(data, plan["version"])
            letters, own, graded = sorted(set(self.plan_versions(db, aid)) | set(self.version_letters(db, plan))), self.added_versions(db, plan), self.plan_versions(db, aid)
            if added and chosen in letters:
                pass  # Already a version of this assessment: nothing to record.
            elif added:
                if not letters:
                    raise ValueError("This assessment has no versions. Versions are added to an assessment that already has them.")
                following = chr(ord(max(letters)) + 1) if max(letters) < "Z" else None
                if chosen != following:
                    raise ValueError(f"Add Version {following} next." if following else "This assessment already has 26 versions.")
            else:
                if chosen not in own:
                    raise Conflict(f"Version {chosen} was not added here, so it cannot be removed here.")
                if chosen in graded:
                    raise Conflict(f"Version {chosen} has questions now, so it stays.")
                if db.execute("SELECT 1 FROM frq_rubric_generations WHERE assignment_id=? AND status IN ('pending','running') "
                              "AND COALESCE(json_extract(input_json,'$.kind'),'')!='key'", (aid,)).fetchone():  # An answer key reading drafts nothing.
                    raise Conflict("Rubrics are being drafted for this assessment. Wait for the draft, or cancel it, before removing a version.")
            if not (added and chosen in letters):
                db.execute("INSERT INTO frq_added_versions VALUES(?,?,?,?,?,?)", (uid(), aid, chosen, int(added), "workspace", timestamp()))
                self.event(db, aid, "exam_version_added" if added else "exam_version_removed", {"exam_version": chosen})
                self.touch(db, aid)
        return self.assignment(aid)

    @staticmethod
    def printed_version(db, revision_id):
        """The version read on the first page of a paper registered from a scan without labels: '' when none was read, None when
        the paper was not registered that way."""
        row = db.execute("""SELECT r.reading_json FROM assembly_revisions ar
            JOIN scan_unlabeled_registrations g ON g.printed_copy_id=ar.printed_copy_id AND g.basis!='superseded'
            JOIN scan_unlabeled_reads r ON r.run_id=g.run_id AND r.scan_page_id=g.scan_page_id
            WHERE ar.id=? ORDER BY g.created_at DESC LIMIT 1""", (revision_id,)).fetchone()
        return (json.loads(row[0]).get("version") or "") if row else None

    @staticmethod
    def printed_document_version(db, plan, revision_id, placed=None, documents=None):
        """The version a paper's own printed document IS (requirement, 2026-09-28: a drafted quiz prints each version as its own document,
        stamped and handed out unnamed). The stamps name the printed copy, and so the document it was printed from; when that document
        carries the questions of exactly ONE version letter of this plan, and that letter's questions all sit on it, the paper is that
        version. None otherwise, above all where one label sheet serves several versions (the seating chart's rule): nothing changes there.
        A paper on the DR center's copy of a version counts as that version's document (DR COPIES, `PAPER_DOCUMENT`).
        `placed` (the plan's letter and document pairs) and `documents` ({revision: document} of the exam's papers) are what a read
        over many papers already has."""
        if documents is not None and revision_id in documents:
            row = (documents[revision_id],)
        else:
            row = db.execute(f"""SELECT {PAPER_DOCUMENT} FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE ar.id=?""", (revision_id,)).fetchone()
        if not row:
            return None
        if placed is None:
            placed = db.execute("""SELECT DISTINCT q.exam_version,q.assignment_version_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                WHERE p.assignment_id=?""", (plan["id"],)).fetchall()
        on = {r[0] for r in placed if r[1] == row[0]}
        if len(on) != 1 or "" in on:
            return None
        letter = next(iter(on))
        return letter if {r[1] for r in placed if r[0] == letter} == {row[0]} else None

    @staticmethod
    def registered_version(db, revision_id):
        """{version, by_instructor} of a paper registered from a scan without labels: the version read on its first page, which the
        Inbox showed beside it, and whether the instructor's own Register click made that registration (an automatic one after a
        confident match did not). None when the paper was not registered that way."""
        row = db.execute("""SELECT r.reading_json,p.actor FROM assembly_revisions ar
            JOIN scan_unlabeled_registrations g ON g.printed_copy_id=ar.printed_copy_id AND g.basis!='superseded'
            JOIN scan_unlabeled_reads r ON r.run_id=g.run_id AND r.scan_page_id=g.scan_page_id
            LEFT JOIN scan_unlabeled_plans p ON p.run_id=g.run_id AND p.scan_page_id=g.scan_page_id
            WHERE ar.id=? ORDER BY g.created_at DESC LIMIT 1""", (revision_id,)).fetchone()
        return {"version": json.loads(row[0]).get("version") or "", "by_instructor": row[1] not in (None, "automatic")} if row else None

    @staticmethod
    def seat_version(db, plan, participant):
        """The instructor's rule: the version of the seat this student was given for this exam. None when no chart says."""
        found = sorted({r[0] for r in db.execute("""SELECT v.exam_version FROM seating_charts c
            JOIN seating_assignments a ON a.chart_id=c.id JOIN seating_exam_versions v ON v.chart_id=c.id AND v.seat_id=a.seat_id
            WHERE c.assignment_id=? AND c.term_id=? AND a.section_id=? AND a.enrollment_id=?""",
            (plan["source_assignment_id"], plan["term_id"], participant["section_id"], participant["enrollment_id"]))})
        return found[0] if len(found) == 1 else None

    def version_blanks(self, db, aid):
        """{letter: blank exam} where that version's questions agree on one chosen template."""
        chosen = {}
        for row in db.execute("""SELECT q.exam_version,r.content_json FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                JOIN frq_question_revisions r ON r.id=p.question_revision_id WHERE p.assignment_id=? AND q.exam_version!=''""", (aid,)):
            template = json.loads(row["content_json"]).get("template_id")
            if template:
                chosen.setdefault(row["exam_version"], set()).add(template)
        blanks = {}
        for version_letter, templates in chosen.items():
            if len(templates) == 1:
                row = db.execute("SELECT id,label,page_count,manifest_sha256 FROM assignment_templates WHERE id=?", (next(iter(templates)),)).fetchone()
                if row:
                    blanks[version_letter] = dict(row)
        return blanks

    def _version_input(self, db, plan, revision_id, blanks):
        revision = db.execute("SELECT selection_json,selection_sha256 FROM assembly_revisions WHERE id=?", (revision_id,)).fetchone()
        if not revision or not blanks:
            return None
        pages = [{"page_number": p["page_number"], "scan_page_id": p["scan_page_id"], "content_sha256": p["content_sha256"]}
                 for p in json.loads(revision["selection_json"])]
        return {"matcher": version_match.MATCHER, "assembly_revision_id": revision_id, "assembly_sha256": revision["selection_sha256"],
                "pages": sorted(pages, key=lambda p: p["page_number"]),
                "blanks": {k: {"template_id": v["id"], "manifest_sha256": v["manifest_sha256"], "page_count": v["page_count"]} for k, v in sorted(blanks.items())}}

    @staticmethod
    def _first_page_input(db, revision_id, expected):
        """What FIRST PAGE reads for a paper: its page 1 as reviewed, and the seat's letter it is to print (None for a paper with no
        seat, so its reading agrees with nothing and sets no comparison aside). None without a page 1."""
        row = db.execute("SELECT selection_json FROM assembly_revisions WHERE id=?", (revision_id,)).fetchone()
        page = next((p for p in json.loads(row["selection_json"]) if int(p["page_number"]) == 1), None) if row else None
        if not page:
            return None
        return {"matcher": FIRST_PAGE, "assembly_revision_id": revision_id, "expected": expected, "boxes": [list(box) for box in FIRST_PAGE_BOXES],
                "pages": [{"page_number": 1, "scan_page_id": page["scan_page_id"], "content_sha256": page["content_sha256"]}]}

    def _version_prefetch(self, db, plan, shared, registrations=True):
        """For a pass over every paper of an exam that asked for it (`shared["prefetch"] = {}`: discovery, `paper_versions`, a
        listing of every student's responses): each paper's printed document and its newest registration from a scan without
        labels, read once for the exam's print batches. A paper outside them, or whose newest registrations share their time, is
        left out and read alone exactly as before. `registrations=False` reads the documents alone (`paper_documents`)."""
        prefetch = shared.get("prefetch")
        if prefetch is None:
            return prefetch
        if "documents" not in prefetch:
            prefetch["documents"] = dict(db.execute(f"""SELECT ar.id,{PAPER_DOCUMENT} FROM exam_event_batches eb JOIN print_batches b ON b.id=eb.batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id
                JOIN printed_copies c ON c.print_batch_id=b.id JOIN assembly_revisions ar ON ar.printed_copy_id=c.id WHERE eb.event_id=?""", (plan["event_id"],)).fetchall())
        if not registrations or "registrations" in prefetch:
            return prefetch
        found = {}
        for row in db.execute("""SELECT ar.id,g.created_at,r.reading_json,p.actor FROM exam_event_batches eb
                JOIN printed_copies c ON c.print_batch_id=eb.batch_id JOIN assembly_revisions ar ON ar.printed_copy_id=c.id
                JOIN scan_unlabeled_registrations g ON g.printed_copy_id=ar.printed_copy_id AND g.basis!='superseded'
                JOIN scan_unlabeled_reads r ON r.run_id=g.run_id AND r.scan_page_id=g.scan_page_id
                LEFT JOIN scan_unlabeled_plans p ON p.run_id=g.run_id AND p.scan_page_id=g.scan_page_id WHERE eb.event_id=?""", (plan["event_id"],)):
            found.setdefault(row[0], []).append(row)
        registrations = prefetch["registrations"] = {}
        for revision in prefetch["documents"]:
            rows = found.get(revision, [])
            if not rows:
                registrations[revision] = None
            elif all(isinstance(row[1], str) for row in rows):
                newest = max(row[1] for row in rows)
                top = [row for row in rows if row[1] == newest]
                if len(top) == 1:  # `ORDER BY g.created_at DESC LIMIT 1` with no tie to break
                    registrations[revision] = top[0]
        return prefetch

    def paper_documents(self, db, plan, participants, shared):
        """[(participant, the document of their paper)] exactly as `manual._paper_version` answers for each participant alone, in
        two reads for all (performance pass, 2026-10-02): a selected paper of the exam's own print jobs from the read's prefetch,
        when the caller asked for one (any other selected paper is read alone), and for a student without a selected paper the one
        document their current copies of the exam's print jobs were printed from (None for none or several)."""
        revisions = [participant.get("collection", {}).get("assembly_revision_id") for participant in participants]
        prefetch = self._version_prefetch(db, plan, shared, registrations=False) if any(revisions) else None
        documents = prefetch["documents"] if prefetch else {}
        issued = None
        if not all(revisions):  # `_paper_version`'s query for one student, over every student of the exam
            issued = {}
            for enrollment, section, document in db.execute(f"""SELECT DISTINCT c.enrollment_id,b.section_id,{PAPER_DOCUMENT} FROM print_batches b
                    JOIN printed_copies c ON c.print_batch_id=b.id JOIN exam_event_batches eb ON eb.batch_id=b.id
                    JOIN assignment_versions v ON v.id=b.assignment_version_id
                    WHERE eb.event_id=? AND b.status='ready' AND b.superseded_at IS NULL""", (plan["event_id"],)):
                issued.setdefault(enrollment, []).append((section, document))
        result = []
        for participant, revision in zip(participants, revisions):
            if revision:
                document = documents[revision] if revision in documents else self.manual._paper_version(db, plan["event_id"], participant)
            else:
                copies = issued.get(participant["enrollment_id"], ()) if participant["enrollment_id"] is not None else ()  # NULL matches nothing
                found = {document for section, document in copies if section is None or section == participant["section_id"]}
                document = next(iter(found)) if len(found) == 1 else None
            result.append((participant, document))
        return result

    def paper_version(self, db, plan, participant, shared=None):
        """{status, version, seat, matched, reason}. Only `confirmed` and `instructor` (and an exam without versions) may be graded.
        `shared` is the caller's memo for ONE read, never across a write. From its second paper on, the plan's seats, version
        decisions and comparisons are read once for all of its papers; a single paper (a grading step's check) keeps its own indexed
        reads."""
        if "_exam_version" in participant:
            return participant["_exam_version"]
        shared = shared if shared is not None else {}
        shared["_version_reads"] = shared.get("_version_reads", 0) + 1
        many = shared["_version_reads"] > 1
        if "graded" not in shared:
            shared["graded"], shared["blanks"] = self.plan_versions(db, plan["id"]), self.version_blanks(db, plan["id"])
        graded, blanks = shared["graded"], shared["blanks"]

        def result(status, chosen=None, reason="", **extra):
            participant["_exam_version"] = {"status": status, "version": chosen, "reason": reason, **extra}
            return participant["_exam_version"]
        if not graded:
            return result("unversioned", "")
        member = (participant["section_id"], participant["enrollment_id"])
        if many:
            if "seats" not in shared:  # every seat's letter of the plan's charts, as `seat_version` reads one student's
                shared["seats"] = {}
                for row in db.execute("""SELECT a.section_id,a.enrollment_id,v.exam_version FROM seating_charts c
                        JOIN seating_assignments a ON a.chart_id=c.id JOIN seating_exam_versions v ON v.chart_id=c.id AND v.seat_id=a.seat_id
                        WHERE c.assignment_id=? AND c.term_id=?""", (plan["source_assignment_id"], plan["term_id"])):
                    shared["seats"].setdefault((row[0], row[1]), set()).add(row[2])
            letters = sorted(shared["seats"].get(member, ()))
            seat = letters[0] if len(letters) == 1 else None
        else:
            seat = self.seat_version(db, plan, participant)
        revision = participant.get("collection", {}).get("assembly_revision_id")
        if not revision:
            return result("no_paper", None, "No current reviewed paper.", seat=seat)
        named = " or ".join(f"Version {v}" for v in graded)
        if many:
            if "version_reviews" not in shared:  # the latest decision per paper: the last one in rowid order
                shared["version_reviews"] = {(row["section_id"], row["enrollment_id"], row["assembly_revision_id"]): row for row in db.execute(
                    "SELECT * FROM frq_version_reviews WHERE assignment_id=? ORDER BY rowid", (plan["id"],))}
            review = shared["version_reviews"].get((*member, revision))
        else:
            review = db.execute("""SELECT * FROM frq_version_reviews WHERE assignment_id=? AND section_id=? AND enrollment_id=? AND assembly_revision_id=?
                ORDER BY rowid DESC LIMIT 1""", (plan["id"], participant["section_id"], participant["enrollment_id"], revision)).fetchone()
        if review:
            return result("instructor", review["exam_version"], seat=seat, review_id=review["id"])
        prefetch = self._version_prefetch(db, plan, shared)
        if "printed" not in shared:
            shared["printed"] = {}
        if revision not in shared["printed"]:
            if "placed" not in shared:  # the plan's (letter, document) pairs: the same for every paper of this read
                shared["placed"] = db.execute("""SELECT DISTINCT q.exam_version,q.assignment_version_id FROM frq_assignment_questions p
                    JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=?""", (plan["id"],)).fetchall()
            shared["printed"][revision] = self.printed_document_version(db, plan, revision, shared["placed"], prefetch and prefetch["documents"])
        if shared["printed"][revision]:
            # Stamped on its own version's document: the stamps say which version this student wrote. No seat or comparison can.
            return result("confirmed", shared["printed"][revision], seat=seat, printed_document=True)
        if "declared" not in shared:
            shared["declared"] = self.declared_versions(db, plan)
        declared = shared["declared"]
        if "letters" not in shared:
            shared["letters"] = set(graded) | set(self.chart_versions(db, plan)) | set(declared) | set(self.added_versions(db, plan))
        # Registered in the Inbox (requirement, 2026-09-26): the instructor's Register click confirms the version the Inbox showed beside the
        # paper when it is one of this assignment's versions, even against a seat (a makeup student still on the room chart), unless
        # the blank exams clearly point to another version. An automatic registration confirms nothing. A plan with declared versions
        # and no seat keeps its own rule below, which takes the printed version without comparing any blank exam.
        known = prefetch["registrations"] if prefetch else {}  # as `registered_version` and `printed_version` read one paper's
        reading = lambda row: json.loads(row[2]).get("version") or ""
        if revision in known:
            row = known[revision]
            registered = {"version": reading(row), "by_instructor": row[3] not in (None, "automatic")} if row else None
        else:
            registered = self.registered_version(db, revision)
        inbox = registered["version"] if registered and registered["by_instructor"] and registered["version"] in shared["letters"] else None

        def from_inbox(**extra):
            return result("confirmed", inbox, printed=inbox, registered=True, seat=seat, **extra)
        if not seat and declared:
            # A plan made from a scan (requirement, 2026-09-24): no seating chart hands versions out, so the version read on the paper's
            # first page decides when it is one of the declared letters. No blank exam is needed; anything else waits for the instructor.
            if revision in known:
                printed = reading(known[revision]) if known[revision] else None
            else:
                printed = self.printed_version(db, revision)
            each, either = ", ".join(declared[:-1]) + " and " + declared[-1], ", ".join(declared[:-1]) + " or " + declared[-1]
            if printed in declared:
                return result("confirmed", printed, printed=printed)
            if printed:
                return result("unresolved", None, f"Version {printed} was read on this paper’s first page, but this assignment has Versions {each}. Confirm which version it is.", printed=printed)
            return result("unresolved", None, f"No version was read on this paper’s first page. Confirm whether it is Version {either}.", printed="")
        if many:
            if "version_checks" not in shared:  # the plan's comparisons, one per student and input (UNIQUE), as `find` reads one
                shared["version_checks"] = {(row["section_id"], row["enrollment_id"], row["input_sha256"]): row for row in db.execute(
                    "SELECT * FROM frq_version_checks WHERE assignment_id=?", (plan["id"],))}
            find = lambda value: shared["version_checks"].get((*member, digest(value)))
        else:
            find = lambda value: db.execute("SELECT * FROM frq_version_checks WHERE assignment_id=? AND section_id=? AND enrollment_id=? AND input_sha256=?",
                (plan["id"], participant["section_id"], participant["enrollment_id"], digest(value))).fetchone()
        value = self._version_input(db, plan, revision, blanks)
        check = find(value) if value else None
        if value and not check:
            # A decision an earlier matcher reached on these exact pages and blanks stands (`version_match.EARLIER`);
            # an earlier comparison that decided nothing is compared again under the current matcher.
            for matcher in version_match.EARLIER:
                earlier = find({**value, "matcher": matcher})
                if earlier and earlier["status"] == "complete" and json.loads(earlier["result_json"])["decision"]["matched"]:
                    check = earlier
                    break
        set_aside = bool(check) and check["status"] == "failed" and check["error"] == NOT_NEEDED
        settled = seat if seat in graded and inbox in (None, seat) else None
        page_read = {}

        def page_one():
            """(page 1's input, its reading row, the letter it read) for a seated paper, or one with no seat (expecting no letter),
            read only when asked."""
            if not page_read:
                first = self._first_page_input(db, revision, settled) if settled or not seat else None
                read = find(first) if first else None
                page_read["found"] = (first, read, json.loads(read["result_json"])["decision"]["matched"] if read and read["status"] == "complete" else None)
            return page_read["found"]
        def jev_alone(**extra):
            """JEV FALLBACK (requirement, 2026-10-26): a paper
            with no seat whose comparison failed or matched no version clearly takes the one letter Jev read on its page 1. None
            while nothing settles it: the OCR's reading alone, or Jev's split or "none", leaves the paper to the instructor."""
            first, read, letter_read = page_one()
            if first and (not read or read["status"] in ("pending", "running")):
                return result("checking", None, "This paper’s exam version is still being confirmed from its first page.", seat=seat,
                              **({} if read else {"unqueued": first, "assembly_revision_id": revision}))
            if letter_read in graded and json.loads(read["result_json"]).get("reader") == "jev":
                return result("confirmed", letter_read, first_page=True, jev_alone=True, **{"seat": seat, **extra})
            return None
        if settled and (not check or check["status"] in ("pending", "running") or set_aside):
            # FIRST PAGE: a comparison already decided stands; otherwise page 1 printing the seat's version confirms the paper.
            first, read, _ = page_one()
            if first and (not read or read["status"] in ("pending", "running")):
                return result("checking", None, "This paper’s exam version is still being confirmed from its first page.", seat=seat,
                              **({} if read else {"unqueued": first, "assembly_revision_id": revision}))
            if read and read["status"] == "complete" and json.loads(read["result_json"])["agrees"]:
                return result("confirmed", settled, seat=seat, first_page=True, **({"printed": inbox, "registered": True} if inbox else {}))
        if set_aside:
            # Page 1 no longer settles this paper (its seat or reading changed): the comparison set aside for it runs after all.
            return result("checking", None, "This paper’s exam version is still being confirmed against the blank exams.", seat=seat, requeue=check["id"])
        if seat and seat not in blanks and not inbox:
            return result("waiting_blank", None, f"This paper was seated for Version {seat}, which has no blank exam yet. Choose Version {seat}’s blank exam so the paper can be confirmed against it.", seat=seat)
        if not value:
            if inbox:
                return from_inbox()  # No blank exam to contradict what the instructor confirmed.
            return result("waiting_blank", None, "Choose each exam version’s blank exam so papers can be confirmed against them.", seat=seat)
        if not check or check["status"] in ("pending", "running"):
            # Reading never writes: validation runs inside other transactions. queue_version_checks inserts what `unqueued` names.
            return result("checking", None, "This paper’s exam version is still being confirmed against the blank exams.", seat=seat,
                          **({} if check else {"unqueued": value, "assembly_revision_id": revision}))
        if check["status"] == "failed":
            if inbox:
                return from_inbox(check_id=check["id"])  # A comparison that failed points to no other version.
            found = None if seat else jev_alone(check_id=check["id"])
            if found:
                return found
            return result("unresolved", None, f"This paper could not be compared with the blank exams ({check['error']}). Confirm whether it is {named}.", seat=seat, check_id=check["id"])
        decision = json.loads(check["result_json"])["decision"]
        matched, extra = decision["matched"], {"seat": seat, "matched": decision["matched"], "scores": decision["scores"], "check_id": check["id"]}
        if inbox and matched in (None, inbox):
            return from_inbox(matched=matched, scores=decision["scores"], check_id=check["id"])
        if inbox:
            return result("mismatch", None, f"Version {inbox} was read on this paper’s first page and confirmed when you registered it in the Inbox, but its pages match Version {matched}’s blank exam. Confirm which exam this student wrote; it is not graded until you do.", printed=inbox, **extra)
        if seat and matched == seat:
            return result("confirmed", seat, **extra)
        first, read, letter_read = page_one() if matched and matched != seat else (None, None, None)
        if first and (not read or read["status"] in ("pending", "running")):
            # Compared before FIRST PAGE, or a paper with no seat whose pages match a version: page 1 is read before the paper waits
            # for the instructor.
            return result("checking", None, "This paper’s exam version is still being confirmed from its first page.", seat=seat,
                          **({} if read else {"unqueued": first, "assembly_revision_id": revision}))
        if matched and matched == letter_read:
            # FIRST PAGE: page 1 prints another version than the seat's, or a paper has no seat, and the whole paper matches that
            # version's blank exam: the booklet the student wrote says which it is twice over (Exam 2: 4 of the first 24 papers
            # differed from their seats; NO SEAT, requirement, 2026-10-26: 12 papers with no seat matching A waited for a Decide click).
            return result("confirmed", matched, first_page=True, **extra)
        if seat and matched:
            return result("mismatch", None, f"The seating chart gave this paper Version {seat}, but its pages match Version {matched}’s blank exam. Confirm which exam this student wrote; it is not graded until you do.", **extra)
        if seat:
            return result("unclear", None, f"This paper was seated for Version {seat}, but its pages match neither {named.replace(' or ', ' nor ')} clearly. Confirm which exam this student wrote.", **extra)
        if matched:
            return result("proposed", None, f"No seating chart gives this paper an exam version. Its pages match Version {matched}’s blank exam; confirm it.", **extra)
        return jev_alone(**extra) or result("unresolved", None, f"No seating chart gives this paper an exam version and its pages match no blank exam clearly. Confirm whether it is {named}.", **extra)

    def queue_version_checks(self, db, plan, participants):
        """Insert the comparisons paper_version found missing. Callers hold the lifecycle lock and no open write transaction of their own."""
        queued = 0
        for participant in participants:
            found = participant.get("_exam_version") or {}
            if found.get("requeue"):
                queued += db.execute("UPDATE frq_version_checks SET status='pending',attempt=0,error='',updated_at=? WHERE id=? AND status='failed' AND error=?",
                                     (timestamp(), found["requeue"], NOT_NEEDED)).rowcount
            if found.get("unqueued"):
                value, now = found.pop("unqueued"), timestamp()
                queued += db.execute("INSERT OR IGNORE INTO frq_version_checks VALUES(?,?,?,?,?,?,?,'pending',0,NULL,'',?,?)", (uid(), plan["id"],
                    participant["section_id"], participant["enrollment_id"], found["assembly_revision_id"], digest(value), encoded(value), now, now)).rowcount
        if queued:
            self.inbox.wake.set()
        return queued

    def require_exam_version(self, db, plan, question, participant, shared=None):
        """The paper's letter for a versioned question, or a refusal. Questions without a version apply to every paper."""
        if not question.get("exam_version"):
            return ""
        found = self.paper_version(db, plan, participant, shared)
        if found["status"] not in GRADABLE:
            raise VersionUndecided(found["reason"], found["status"])
        if found["version"] != question["exam_version"]:
            raise OtherExamVersion(f"This paper is Version {found['version']}; this question belongs to Version {question['exam_version']}.")
        return found["version"]

    def process_version_one(self):
        """One worker turn: up to FIRST_PAGE_BATCH first pages read in one OCR model load, else one whole-paper comparison (CPU only,
        in the document sandbox). Pages are read through the verified Inbox owner."""
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                if db.execute("UPDATE frq_version_checks SET status='pending',updated_at=? WHERE status='running' AND attempt<2", (timestamp(),)).rowcount:
                    return True  # Deterministic local work: an interrupted comparison simply runs again, at most twice more.
                db.execute("UPDATE frq_version_checks SET status='failed',error='The comparison was interrupted repeatedly.',updated_at=? WHERE status='running'", (timestamp(),))
                # FIRST PAGE: a whole-paper comparison waiting for a paper whose latest page 1 reading printed its seat's version is set aside.
                db.execute("""UPDATE frq_version_checks SET status='failed',error=?,updated_at=? WHERE status='pending'
                    AND COALESCE(json_extract(input_json,'$.matcher'),'')!=? AND EXISTS(SELECT 1 FROM frq_version_checks f
                    WHERE f.assignment_id=frq_version_checks.assignment_id AND f.section_id=frq_version_checks.section_id
                    AND f.enrollment_id=frq_version_checks.enrollment_id AND f.assembly_revision_id=frq_version_checks.assembly_revision_id
                    AND f.status='complete' AND json_extract(f.input_json,'$.matcher')=? AND json_extract(f.result_json,'$.agrees')=1
                    AND NOT EXISTS(SELECT 1 FROM frq_version_checks g WHERE g.assignment_id=f.assignment_id AND g.section_id=f.section_id
                    AND g.enrollment_id=f.enrollment_id AND g.assembly_revision_id=f.assembly_revision_id AND g.rowid>f.rowid
                    AND json_extract(g.input_json,'$.matcher')=?))""", (NOT_NEEDED, timestamp(), FIRST_PAGE, FIRST_PAGE, FIRST_PAGE))
                firsts = [dict(r) for r in db.execute("""SELECT * FROM frq_version_checks WHERE status='pending' AND json_extract(input_json,'$.matcher')=?
                    ORDER BY updated_at,rowid LIMIT ?""", (FIRST_PAGE, FIRST_PAGE_BATCH))]
                if firsts:
                    rasters = {}
                    for check in firsts:
                        db.execute("UPDATE frq_version_checks SET status='running',attempt=attempt+1,updated_at=? WHERE id=?", (timestamp(), check["id"]))
                        page = json.loads(check["input_json"])["pages"][0]
                        found = db.execute("SELECT raster_artifact_id,content_sha256 FROM scan_pages WHERE id=?", (page["scan_page_id"],)).fetchone()
                        rasters[check["id"]] = found["raster_artifact_id"] if found and found["content_sha256"] == page["content_sha256"] else None
                elif self._discover_version_checks(db):
                    return True  # a paper's page 1 to read goes before any whole-paper comparison
            if firsts:
                return self._read_first_pages(firsts, rasters)
            with self.rosters.lock(), connect(self.path) as db:
                row = db.execute("SELECT * FROM frq_version_checks WHERE status='pending' ORDER BY updated_at,rowid LIMIT 1").fetchone()
                if not row:
                    return False
                check, value = dict(row), json.loads(row["input_json"])
                db.execute("UPDATE frq_version_checks SET status='running',attempt=attempt+1,updated_at=? WHERE id=?", (timestamp(), check["id"]))
                rasters = {}
                for page in value["pages"]:
                    found = db.execute("SELECT raster_artifact_id,content_sha256 FROM scan_pages WHERE id=?", (page["scan_page_id"],)).fetchone()
                    if found and found["content_sha256"] == page["content_sha256"]:
                        rasters[page["page_number"]] = found["raster_artifact_id"]
            try:
                if len(rasters) != len(value["pages"]):
                    raise ValueError("A reviewed page changed.")
                pages = {number: self.inbox.read_artifact(artifact)[1] for number, artifact in rasters.items()}
                blanks = {k: {n: self.templates.read(v["template_id"], "image", n)[0] for n in range(1, v["page_count"] + 1) if n in pages}
                          for k, v in value["blanks"].items()}
                measured = version_match.compare(pages, blanks)
                outcome = ("complete", encoded({**measured, "decision": version_match.decide(measured)}), "")
            except (ValueError, OSError, NotFound) as error:
                outcome = ("failed", None, str(error) if isinstance(error, ValueError) else "A page or blank exam could not be read")
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("UPDATE frq_version_checks SET status=?,result_json=?,error=?,updated_at=? WHERE id=?", (*outcome, timestamp(), check["id"]))
                if outcome[0] == "complete":
                    decision = json.loads(outcome[1])["decision"]
                    self.event(db, check["assignment_id"], "exam_version_compared", {"check_id": check["id"], "matched": decision["matched"], "scores": decision["scores"]}, "worker")
            self._version_discovery_at = time.monotonic() - 60  # NO SEAT: a paper with no seat has its page 1 queued on the next turn
            return True

    def _read_first_pages(self, checks, rasters):
        """FIRST PAGE: read the version printed on these papers' first pages and record what each says: Jev first (JEV PAGE ONE),
        then the local OCR in one model load for every page Jev did not settle."""
        from PIL import Image
        import faculty_ocr
        outcomes = {check["id"]: ("failed", None, "A reviewed page changed.") for check in checks if not rasters[check["id"]]}
        checks = [check for check in checks if rasters[check["id"]]]
        outcomes.update(self._jev_first_pages(checks, rasters))
        checks = [check for check in checks if check["id"] not in outcomes]
        try:
            if checks and not faculty_ocr.available():
                raise ValueError("Local OCR is not installed.")
            crops, boxes, top = [], [], 0
            for check in checks:
                with Image.open(io.BytesIO(self.inbox.read_artifact(rasters[check["id"]])[1])) as page:
                    page = page.convert("L")
                    for left, upper, right, lower in json.loads(check["input_json"])["boxes"]:
                        crop = page.crop((int(left * page.width), int(upper * page.height), int(right * page.width), int(lower * page.height)))
                        crops.append(crop)
                        boxes.append([0, top, crop.width, top + crop.height])
                        top += crop.height + 8
            reads = []
            if crops:
                composite = Image.new("L", (max(crop.width for crop in crops), top), 255)
                for crop, box in zip(crops, boxes):
                    composite.paste(crop, (0, box[1]))
                buffer = io.BytesIO()
                composite.save(buffer, "PNG")
                reads = faculty_ocr.recognize_fields(buffer.getvalue(), boxes)["reads"]
            each = len(FIRST_PAGE_BOXES)
            for index, check in enumerate(checks):
                texts = [str(read.get("text") or "")[:500] for read in reads[index * each:(index + 1) * each]]
                letter, expected = first_page_version(texts), json.loads(check["input_json"])["expected"]
                outcomes[check["id"]] = ("complete", encoded({"matcher": FIRST_PAGE, "texts": texts, "agrees": letter == expected,
                                                              "decision": {"matched": letter or None, "scores": {}}}), "")
        except faculty_ocr.OcrError as error:
            if faculty_ocr.BUSY in str(error):
                with self.rosters.lock(), connect(self.path) as db:  # The model is busy: these pages are read on a later turn.
                    db.executemany("UPDATE frq_version_checks SET status='pending',attempt=attempt-1,updated_at=? WHERE id=? AND status='running'",
                                   [(timestamp(), check["id"]) for check in checks])
                    db.executemany("UPDATE frq_version_checks SET status=?,result_json=?,error=?,updated_at=? WHERE id=? AND status='running'",
                                   [(*outcome, timestamp(), identifier) for identifier, outcome in outcomes.items()])
                return bool(outcomes)
            outcomes.update({check["id"]: ("failed", None, "Page 1 could not be read.") for check in checks})
        except (ValueError, OSError, NotFound) as error:
            LOG.warning("Reading first pages for their versions failed: %s", error)
            outcomes.update({check["id"]: ("failed", None, str(error) if isinstance(error, ValueError) else "Page 1 could not be read.") for check in checks})
        with self.rosters.lock(), connect(self.path) as db:
            # A failed read only sends its paper on to the whole-paper comparison (`paper_version`).
            db.executemany("UPDATE frq_version_checks SET status=?,result_json=?,error=?,updated_at=? WHERE id=? AND status='running'",
                           [(*outcome, timestamp(), identifier) for identifier, outcome in outcomes.items()])
        if any(outcome[0] != "complete" or not json.loads(outcome[1])["agrees"] for outcome in outcomes.values()):
            self._version_discovery_at = time.monotonic() - 60  # their whole-paper comparisons are queued on the next turn
        self.inbox.wake.set()
        return True

    def booklet_versions(self, db, assignment_id, term_id):
        """{(section, student): letter} of every paper of this exam and term whose version is settled (`confirmed` or the
        instructor's), for the bubble sheets (BUBBLE-SHEETS "THE BOOKLET'S VERSION"). A read: nothing is queued or decided.

        A booklet scanned again keeps its version (BUBBLE-SHEETS › A BOOKLET SCANNED AGAIN): while its new pages wait for assembly
        review the paper has no current revision, but its one active copy's latest assembled revision still says which booklet the
        student wrote. Exam 2, 2026-10-25: 164 booklets rescanned in the morning lost theirs, and 33 sheets went back to their seat's
        key, about 33 of 60 points lower on average."""
        found = {}
        for (plan_id,) in db.execute("""SELECT g.id FROM frq_assignments g JOIN exam_events e ON e.id=g.event_id
                WHERE e.assignment_id=? AND e.term_id=? ORDER BY g.created_at,g.id""", (assignment_id, term_id)).fetchall():
            if not self.plan_versions(db, plan_id):
                continue
            plan = self.plan(db, plan_id)
            _, participants = self.manual._context(db, plan["event_id"])
            shared = {"prefetch": {}}
            for participant in participants:
                collection = participant.get("collection", {})
                if not collection.get("assembly_revision_id"):
                    assembled = {copy["latest_revision_id"] for copy in collection.get("copies", []) if copy.get("active") and copy.get("latest_revision_id")}
                    if len(assembled) != 1:
                        continue
                    # A copy of the participant, so the paper's own (unassembled) state is never memoized under the earlier revision.
                    participant = {key: value for key, value in participant.items() if key != "_exam_version"}
                    participant["collection"] = {**collection, "assembly_revision_id": next(iter(assembled))}
                paper = self.paper_version(db, plan, participant, shared)
                if paper["status"] in ("confirmed", "instructor") and paper["version"]:
                    found[(participant["section_id"], participant["enrollment_id"])] = paper["version"]
        return found

    def _jev_first_pages(self, checks, rasters):
        """JEV PAGE ONE: {check id: outcome} for the first pages Jev read one version on; the others are left to the local OCR."""
        from jev import client
        jev = getattr(self, "jev", None)
        if not checks or not jev or not client.available():
            return {}
        with self.rosters.lock(), connect(self.path) as db:
            letters = {aid: self.plan_versions(db, aid) for aid in {check["assignment_id"] for check in checks}}
        outcomes = {}
        for check in checks:
            try:
                found = jev_versions.page_one(jev, check["id"], self.inbox.read_artifact(rasters[check["id"]])[1], letters[check["assignment_id"]])
            except Exception:
                LOG.exception("Jev could not read page 1 of version check %s; the local OCR reads it", check["id"])
                continue
            if found["letter"]:
                expected = json.loads(check["input_json"])["expected"]
                outcomes[check["id"]] = ("complete", encoded({"matcher": FIRST_PAGE, "reader": "jev", "receipt": found["receipt"],
                                                              "agrees": found["letter"] == expected, "decision": {"matched": found["letter"], "scores": {}}}), "")
        return outcomes

    @staticmethod
    def version_check_progress(db, aid):
        """Papers whose version is being confirmed (page 1 read, or the whole paper compared with the blank exams), as counts per
        student paper: left while any check of it is queued or running,
        otherwise done, and failed when its latest comparison failed. Comparisons are all queued in one moment, so counting papers
        (not a round by time) is what stays true when a changed paper or matcher queues some again."""
        # A comparison FIRST PAGE set aside is no paper's latest word, and a page 1 that could not be read only sends its paper on
        # to the whole-paper comparison, so neither counts as failed.
        rows = db.execute("""SELECT status,COALESCE(json_extract(input_json,'$.matcher'),'') FROM frq_version_checks c WHERE assignment_id=? AND error!=?
            AND rowid=(SELECT MAX(n.rowid) FROM frq_version_checks n WHERE n.assignment_id=c.assignment_id AND n.section_id=c.section_id
            AND n.enrollment_id=c.enrollment_id AND n.error!=?)""", (aid, NOT_NEEDED, NOT_NEEDED)).fetchall()
        left = db.execute("""SELECT COUNT(DISTINCT section_id||':'||enrollment_id) FROM frq_version_checks
            WHERE assignment_id=? AND status IN ('pending','running')""", (aid,)).fetchone()[0]
        failed = sum(row[0] == "failed" and row[1] != FIRST_PAGE for row in rows)
        return {"left": left, "done": len(rows) - left, "failed": failed, "total": len(rows)}

    def _discover_version_checks(self, db):
        """Queue comparisons for reviewed papers of plans that grade by exam version. Bounded, and at most once a minute."""
        if time.monotonic() - getattr(self, "_version_discovery_at", -60) < 60:
            return False
        self._version_discovery_at = time.monotonic()
        queued = False
        for row in db.execute("""SELECT DISTINCT p.assignment_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                WHERE q.exam_version!=''""").fetchall():
            plan = self.plan(db, row[0])
            _, participants = self.manual._context(db, plan["event_id"])
            shared = {"prefetch": {}}  # every paper of the exam: their documents and registrations in one read
            for participant in participants:
                if participant.get("collection", {}).get("assembly_revision_id"):
                    self.paper_version(db, plan, participant, shared)
            queued = bool(self.queue_version_checks(db, plan, participants)) or queued
        return queued

    @staticmethod
    def paper_pages(db, revision_id):
        """A reviewed paper's scanned pages in order, so the instructor sees the paper whose version they decide (2026-09-25).
        References only: the images are cut on request from the scan's retained pages."""
        row = db.execute("SELECT selection_json FROM assembly_revisions WHERE id=?", (revision_id,)).fetchone()
        pages = sorted(json.loads(row["selection_json"]), key=lambda page: page["page_number"]) if row else []
        scans = {found["id"]: found["message_id"] for found in db.execute(
            f"SELECT id,message_id FROM scan_pages WHERE id IN ({','.join('?' * len(pages))})", [page["scan_page_id"] for page in pages])} if pages else {}
        return [{"page_number": page["page_number"], "scan_page_id": page["scan_page_id"], "message_id": scans[page["scan_page_id"]]}
                for page in pages if page["scan_page_id"] in scans]

    def paper_versions(self, aid):
        """Per exam version: what it still needs, and the papers waiting for the instructor. For the setup guide and Jobs."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            graded, seated, blanks = self.plan_versions(db, aid), self.chart_versions(db, plan), self.version_blanks(db, aid)
            declared, added = self.declared_versions(db, plan), self.added_versions(db, plan)
            _, participants = self.manual._context(db, plan["event_id"])
            counts, waiting, shared = {}, [], {"prefetch": {}}  # every paper of the exam: their documents and registrations in one read
            for participant in participants:
                if not participant.get("collection", {}).get("assembly_revision_id"):
                    continue
                found = self.paper_version(db, plan, participant, shared)
                counts[found["status"]] = counts.get(found["status"], 0) + 1
                if found["status"] in DECIDE:
                    revision = participant["collection"]["assembly_revision_id"]
                    waiting.append({"section_id": participant["section_id"], "enrollment_id": participant["enrollment_id"], "name": participant.get("name", ""),
                        "assembly_revision_id": revision, "pages": self.paper_pages(db, revision),
                        **{k: v for k, v in found.items() if k not in ("version", "unqueued")}})
            self.queue_version_checks(db, plan, participants)
            return {"versions": sorted(set(graded) | set(seated) | set(declared) | set(added)), "graded": graded, "seated": seated, "declared": declared, "added": added,
                    "blanks": {k: {"template_id": v["id"], "label": v["label"]} for k, v in blanks.items()}, "counts": counts, "waiting": waiting}

    def confirm_paper_version(self, aid, data):
        """The instructor's own decision for one paper. It never moves a seat, a label or another paper."""
        chosen = letter(data.get("exam_version"), blank=False)
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            if chosen not in self.plan_versions(db, aid):
                raise ValueError(f"Version {chosen} has no questions in this assessment.")
            _, participants = self.manual._context(db, plan["event_id"])
            participant = self.manual._participant(participants, data.get("section_id"), data.get("enrollment_id"))
            revision = participant.get("collection", {}).get("assembly_revision_id")
            if not revision or revision != data.get("assembly_revision_id"):
                raise Conflict("This student’s reviewed paper changed. Reload before confirming its exam version.")
            found = self.paper_version(db, plan, participant)
            if data.get("confirm_version") is not True:
                raise ValueError("Confirm that you looked at this paper and that it is this exam version.")
            review_id = uid()
            db.execute("INSERT INTO frq_version_reviews VALUES(?,?,?,?,?,?,?,?,?,?)", (review_id, aid, participant["section_id"], participant["enrollment_id"],
                revision, chosen, found.get("check_id"), encoded({"seat": found.get("seat"), "matched": found.get("matched"), "scores": found.get("scores"),
                "status": found["status"], "note": text(data.get("note", ""), "Note", 4000)}), data.get("actor") if data.get("actor") == "agent" else "workspace", timestamp()))
            self.event(db, aid, "exam_version_confirmed", {"review_id": review_id, "exam_version": chosen, "seat": found.get("seat"), "matched": found.get("matched")})
            self.touch(db, aid)
        return self.paper_versions(aid)

    def assign_exam_version(self, aid, data):
        """Say ONCE which exam version questions imported before versions existed describe. Revisions, pins and approvals stay."""
        chosen = letter(data.get("exam_version"), blank=False)
        with self.rosters.lock(), connect(self.path) as db:
            version(data, self.plan(db, aid)["version"])
            ids = items(data.get("question_ids"), "Questions", 100)
            questions = [self.question(db, aid, qid) for qid in ids]
            if not questions or any(q["exam_version"] for q in questions):
                raise Conflict("Only questions without an exam version can be given one, once.")
            db.execute("BEGIN IMMEDIATE")
            for q in questions:
                clash = db.execute("""SELECT 1 FROM frq_assignment_questions p JOIN frq_questions o ON o.id=p.question_id JOIN frq_question_revisions r ON r.id=p.question_revision_id
                    WHERE p.assignment_id=? AND o.assignment_version_id=? AND o.exam_version=? AND json_extract(r.content_json,'$.printed_number')=? AND o.id!=?""",
                    (aid, q["assignment_version_id"], chosen, q["content"].get("printed_number") or "-", q["question_id"])).fetchone()
                if clash:
                    raise Conflict(f"Version {chosen} already has {q['content']['title']}.")
                db.execute("UPDATE frq_questions SET exam_version=? WHERE id=? AND exam_version=''", (chosen, q["question_id"]))
                db.execute("UPDATE frq_assignment_questions SET version=version+1 WHERE assignment_id=? AND question_id=?", (aid, q["question_id"]))
            self.event(db, aid, "exam_version_assigned", {"exam_version": chosen, "question_ids": ids})
            self.touch(db, aid)
        return self.assignment(aid)
