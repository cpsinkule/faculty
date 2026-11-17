"""Stamped papers photocopied from one printed copy: each paper of a scan gets its own unused copy of the same print job.

Requirement, 2026-10-16: Quiz 5 was printed one copy per version and that copy was
photocopied for the class, so every paper of a version carries copy 1's stamps, and Faculty, which tells papers apart by their
stamps, held every page as a conflict and named copy 1 for the first student whose name it read. Here the stamp is evidence of the
version and the page only; whose paper it is comes from the name written on page 1 (`copy_names`), as for any copy printed for no
student.

A copy is PHOTOCOPIED when its page 1 is on two or more pages of scans still in the Inbox (PRESENT) with different content, and its
print job is one `copy_names` reads (a quiz approval's `quiz:` job, or a job printed for the whole term with no section). Once a
scan is fully inspected, its papers of such copies are found in scan order: a page 1 starts a paper, and the following pages of
that copy (other page numbers, blank backs and unreadable pages skipped over) join it until another stamped paper begins. The first
such paper of a copy no other scan shows stays on it; each other paper is moved to the first copy of the same print job that no
scan has shown, nobody is named on and no review holds (the copies the job printed and nobody used), page for page: the page's
identity becomes that copy's stamp for the same page number (`status` manual, as an alias or a first-page position gives one), its
review state is the intake rule's (`ScanService._identity_state`), and the move is an Inbox event naming each page's stamp before
and after. A LATER PAGE OF A MOVED PAPER (requirement, 2026-11-06, Quiz 7: a photocopy's page 1 code read while its page 2 code was
too faint until FAINT MARKS read it): a page of a copy this rule moved papers from, which no paper of that copy takes up, joins the
paper just before it in scan order when that paper's page 1 was moved off the same copy and lacks the page's number, and moves to
that paper's new copy, its move recorded `joined`. The decoded codes themselves stay as they were read. A page in a reviewed
assembly is never moved, and a paper is never moved when the job has no unused copy left (the event says so; it waits on the scan's
pages for the instructor). Afterwards a source copy loses a name only `copy_names` gave it (read from any one of its papers), and
the scan's readings name it and the new copies under their usual holds. Nothing here groups, grades or clears the scan's review.

SHARED STAMPS (requirement, 2026-11-04). Where the approval said each student must use their
own copy (`own_copies`), the same move is the sign of copying: `shared_stamps` reads back from the moves which papers came back on one
stamp and whose they now are. It only reads, and the papers go on being graded. A photocopy that comes back alone cannot be told apart.
"""
import json
import logging

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT, encoded

RULE = "photocopies-v1"
LOG = logging.getLogger(__name__)
# Print jobs whose copies are named from the paper (`copy_names._waiting`): only their copies are ever moved.
UNNAMED_JOB = "(b.request_id LIKE 'quiz:%' OR b.section_id IS NULL)"


def own_copies(db, assignment_id=None):
    """(assignment, term) pairs whose quiz approval said each student must use their own copy (QUIZZES "SHARED STAMPS"), or only `assignment_id`'s."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quiz_draft_approvals'").fetchone():
        return set()
    return {(row[0], row[1]) for row in db.execute("""SELECT json_extract(result_json,'$.assignment_id'),term_id FROM quiz_draft_approvals
            WHERE json_extract(input_json,'$.own_copies')=1 AND json_extract(result_json,'$.assignment_id') IS NOT NULL
              AND (?1 IS NULL OR json_extract(result_json,'$.assignment_id')=?1)""", (assignment_id,))}


def shared_stamps(db, assignment_id, term_id):
    """Each printed copy of this assignment and term whose stamps came back on more than one paper, the oldest first:
    [{copy_number, version, papers: [{enrollment_id, copy_number, job_id}]}], read from the moves (`split`). Only papers still in the Inbox count
    (PRESENT, so a stamp whose other paper was deleted is no longer shared), each as its pages are identified now. A paper with no
    unused copy to move to stays on the shared stamp, with no student of its own: `enrollment_id` None."""
    found, kept_back = {}, set()
    for row in db.execute(f"""SELECT e.detail,j.id job_id FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
            WHERE e.kind='photocopied_papers' AND {PRESENT.format('j.message_id')} ORDER BY e.id"""):
        detail = json.loads(row["detail"])
        for paper in detail.get("moved", []):
            if paper.get("joined"):
                continue  # a later page of a paper already moved (A LATER PAGE OF A MOVED PAPER): that paper is counted once
            found.setdefault(paper["from_copy_id"], []).append((paper["pages"][0]["scan_page_id"], row["job_id"], False))
        for paper in detail.get("held", []):
            found.setdefault(paper["printed_copy_id"], []).append((paper["scan_page_ids"][0], row["job_id"], True))
            kept_back.update(paper["scan_page_ids"])
    stamps = []
    for source, moved in found.items():
        copy = db.execute("""SELECT c.copy_number,v.label version FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE c.id=? AND v.assignment_id=? AND b.term_id=?""",
                          (source, assignment_id, term_id)).fetchone()
        if not copy:
            continue
        papers, seen = [], set()
        for page, job, held in sorted(moved, key=lambda paper: paper[2]):  # a paper held once and moved later is a moved paper
            here = db.execute(f"""SELECT c.id,c.copy_number,c.enrollment_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                    JOIN printed_copies c ON c.id=p.printed_copy_id WHERE sp.id=? AND sp.review_state NOT IN ('rejected','ignored')
                      AND {PRESENT.format('sp.message_id')}""", (page,)).fetchone()
            if here and page not in seen:
                seen.add(page)
                papers.append({"enrollment_id": None if held else here["enrollment_id"], "copy_number": here["copy_number"], "job_id": job})
        # The paper that kept the stamp.
        first = db.execute(f"""SELECT c.copy_number,c.enrollment_id,j.id job_id FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id
                JOIN printed_copies c ON c.id=p.printed_copy_id JOIN inbox_jobs j ON j.message_id=sp.message_id
                WHERE p.printed_copy_id=? AND sp.review_state NOT IN ('rejected','ignored') AND sp.id NOT IN ({",".join("?" * len(kept_back)) or "''"})
                  AND {PRESENT.format('sp.message_id')} ORDER BY p.page_number,sp.rowid LIMIT 1""", (source, *sorted(kept_back))).fetchone()
        if first:
            papers.insert(0, dict(first))
        if len(papers) > 1:
            stamps.append({"copy_number": copy["copy_number"], "version": copy["version"], "papers": papers})
    return stamps


def attention(db):
    """Home's "Shared stamps" card over the current terms: {count, assignment, term} of the assignments not certified yet, or None. Certifying
    ends it (the list stays on the assignment); an instructor who has seen a stamp has no other way to clear it, so it never outlives the work."""
    from certified_archive import certified
    pairs = own_copies(db)
    if not pairs:
        return None
    done = certified(db)
    counts = []
    for assignment, term in sorted(pairs):
        if (assignment, term) not in done and db.execute("SELECT 1 FROM course_terms WHERE id=? AND is_current=1 AND deleted_at IS NULL", (term,)).fetchone():
            count = len(shared_stamps(db, assignment, term))
            if count:
                counts.append((assignment, term, count))
    if not counts:
        return None
    return {"count": sum(c[2] for c in counts), "assignment": counts[0][0] if len(counts) == 1 else None, "term": counts[0][1] if len(counts) == 1 else None}


class PhotocopyService:
    def __init__(self, scans):
        self.scans, self.documents, self.inbox, self.rosters, self.path = scans, scans.documents, scans.inbox, scans.rosters, scans.path

    @staticmethod
    def _moved(db, message_id):
        """{copy a paper of this scan was moved to: copy it was moved from}, read from the scan's `photocopied_papers` events."""
        moved = {}
        for (detail,) in db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                       WHERE j.message_id=? AND e.kind='photocopied_papers' ORDER BY e.id""", (message_id,)):
            for paper in json.loads(detail).get("moved", []):
                moved[paper["to_copy_id"]] = paper["from_copy_id"]
        return moved

    @staticmethod
    def _shared(db):
        """Copies whose page 1 is on pages of different content in present scans: {copy_id}."""
        return {row[0] for row in db.execute(f"""SELECT p.printed_copy_id FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id CROSS JOIN scan_pages sp ON sp.page_stamp_id=p.id
                WHERE p.page_number=1 AND {UNNAMED_JOB} AND sp.status IN ('resolved','manual') AND sp.review_state NOT IN ('rejected','ignored')
                  AND {PRESENT.format('sp.message_id')}
                GROUP BY p.printed_copy_id HAVING COUNT(DISTINCT sp.content_sha256)>1""")}

    def _waiting(self, db):
        """Fully inspected present scans holding a page of a photocopied copy that no reviewed assembly holds, or a conflicting later
        page of a copy the scan's papers were moved off (A LATER PAGE OF A MOVED PAPER): the oldest first."""
        shared = self._shared(db)
        found = []
        if shared:
            marks = ",".join("?" * len(shared))
            found = [row[0] for row in db.execute(f"""SELECT sp.message_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                    JOIN document_decode_jobs d ON d.message_id=sp.message_id
                    WHERE p.printed_copy_id IN ({marks}) AND d.status='complete' AND {PRESENT.format('sp.message_id')}
                      AND sp.review_state NOT IN ('rejected','ignored')
                      AND NOT EXISTS(SELECT 1 FROM assembly_pages a WHERE a.scan_page_id=sp.id)
                    GROUP BY sp.message_id ORDER BY MIN(sp.rowid)""", sorted(shared))]
        later = [row[0] for row in db.execute(f"""SELECT sp.message_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                JOIN inbox_jobs j ON j.message_id=sp.message_id JOIN inbox_events e ON e.job_id=j.id AND e.kind='photocopied_papers'
                JOIN document_decode_jobs d ON d.message_id=sp.message_id
                WHERE p.page_number>1 AND sp.review_state='conflict' AND d.status='complete' AND {PRESENT.format('sp.message_id')}
                  AND NOT EXISTS(SELECT 1 FROM assembly_pages a WHERE a.scan_page_id=sp.id)
                  AND EXISTS(SELECT 1 FROM json_each(e.detail,'$.moved') m WHERE json_extract(m.value,'$.from_copy_id')=p.printed_copy_id)
                GROUP BY sp.message_id ORDER BY MIN(sp.rowid)""")]
        return found + [message for message in later if message not in found]

    def process_one(self):
        """Split one scan's photocopied papers. Returns whether anything changed."""
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                waiting = self._waiting(db)
            for message_id in waiting:
                if self.split(message_id):
                    return True
            return False

    @staticmethod
    def papers(pages, shared, moved=None):
        """[(copy_id, [page rows], target)] in scan order: a page 1 of a shared copy starts a paper (target None); that copy's later
        pages join it. A page 1 of a copy in `moved` ({moved to: moved from}) starts a paper of the copy it was moved off whose
        later pages join it to move to that page 1's copy (`target`, A LATER PAGE OF A MOVED PAPER); the page 1 itself stays."""
        found, current, moved = [], None, moved or {}
        for page in pages:
            copy = page["printed_copy_id"]
            if copy is None or page["review_state"] in ("ignored", "rejected", "duplicate"):
                continue  # a blank back, an unreadable page or a page scanned twice leaves the paper open
            if copy in shared and page["page_number"] == 1:
                current = (copy, [page], None, {1})
                found.append(current)
            elif page["page_number"] == 1 and copy in moved:
                current = (moved[copy], [], copy, {1})
                found.append(current)
            elif current and copy == current[0] and page["page_number"] not in current[3]:
                current[1].append(page)
                current[3].add(page["page_number"])
            else:
                current = None
        return [(copy, rows, target) for copy, rows, target, _ in found if rows and not any(row["assembled"] for row in rows)]

    @staticmethod
    def _unused(db, batch_id, numbers, taken):
        """The first copy of the job nobody is named on, no scan has shown and no review holds, stamped on every page number asked."""
        for row in db.execute(f"""SELECT c.id FROM printed_copies c WHERE c.print_batch_id=? AND c.enrollment_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id
                    WHERE p.printed_copy_id=c.id AND {PRESENT.format('sp.message_id')})
                AND NOT EXISTS(SELECT 1 FROM assembly_revisions r WHERE r.printed_copy_id=c.id)
                ORDER BY c.copy_number""", (batch_id,)):
            if row[0] in taken:
                continue
            stamps = {r[0]: r[1] for r in db.execute("SELECT page_number,id FROM page_stamps WHERE printed_copy_id=? AND state='issued' AND symbology!='sequence'",
                                                     (row[0],))}
            if all(number in stamps for number in numbers):
                return row[0], stamps
        return None, {}

    def split(self, message_id):
        moved, held, sources, renamed = [], [], set(), set()
        with self.rosters.lock(), self.documents.files.owner_lock(message_id), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            shared, moved_to = self._shared(db), self._moved(db, message_id)
            if not shared and not moved_to:
                return False
            pages = [dict(row) for row in db.execute(f"""SELECT sp.id,sp.page_stamp_id,sp.content_sha256,sp.review_state,p.printed_copy_id,p.page_number,
                    c.print_batch_id,EXISTS(SELECT 1 FROM assembly_pages a WHERE a.scan_page_id=sp.id) assembled
                    FROM scan_pages sp LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id LEFT JOIN printed_copies c ON c.id=p.printed_copy_id
                    WHERE sp.message_id=? AND {PRESENT.format('sp.message_id')} ORDER BY sp.source_artifact_id,sp.source_page_index""", (message_id,))]
            taken, kept, when = set(), set(), timestamp()
            for copy, rows, joined in self.papers(pages, shared, moved_to):
                if joined:
                    # A LATER PAGE OF A MOVED PAPER: its page 1 already has its own copy, so its pages go to that copy's stamps.
                    stamps = {r[0]: r[1] for r in db.execute("SELECT page_number,id FROM page_stamps WHERE printed_copy_id=? AND state='issued' AND symbology!='sequence'",
                                                             (joined,))}
                    if not all(row["page_number"] in stamps for row in rows):
                        held.append({"printed_copy_id": copy, "scan_page_ids": [row["id"] for row in rows]})
                        continue
                    target = joined
                # The first paper of a copy no other scan shows stays on that copy: one unused copy fewer is needed. A copy whose paper
                # this scan already assembled has been kept by that paper.
                elif copy not in kept and not any(page["assembled"] and page["printed_copy_id"] == copy for page in pages) \
                        and not db.execute(f"""SELECT 1 FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id
                        WHERE p.printed_copy_id=? AND sp.message_id!=? AND sp.review_state NOT IN ('rejected','ignored')
                          AND {PRESENT.format('sp.message_id')} LIMIT 1""", (copy, message_id)).fetchone():
                    kept.add(copy)
                    continue
                else:
                    target, stamps = self._unused(db, rows[0]["print_batch_id"], [row["page_number"] for row in rows], taken)
                    if not target:
                        held.append({"printed_copy_id": copy, "scan_page_ids": [row["id"] for row in rows]})
                        continue
                    taken.add(target)
                    renamed.add(copy)  # a joined page leaves the source's name as its page 1's move did
                sources.add(copy)
                for row in rows:
                    stamp = stamps[row["page_number"]]
                    state, duplicate = self.scans._identity_state(db, row["id"], stamp, row["content_sha256"], when)
                    db.execute("UPDATE scan_pages SET page_stamp_id=?,status='manual',review_state=?,duplicate_of_scan_page_id=?,updated_at=? WHERE id=?",
                               (stamp, state, duplicate, when, row["id"]))
                moved.append({"from_copy_id": copy, "to_copy_id": target, **({"joined": True} if joined else {}),
                              "pages": [{"scan_page_id": row["id"], "page_number": row["page_number"], "previous_stamp_id": row["page_stamp_id"],
                                         "stamp_id": stamps[row["page_number"]]} for row in rows]})
            if not moved and not held:
                return False
            # What the source copies still show: a page left behind is no longer in conflict with the papers moved off its stamp.
            for copy in sources:
                for stamp, in db.execute("SELECT id FROM page_stamps WHERE printed_copy_id=?", (copy,)).fetchall():
                    left = db.execute(f"""SELECT id,content_sha256 FROM scan_pages WHERE page_stamp_id=? AND review_state IN ('conflict','candidate')
                                          AND {PRESENT.format('scan_pages.message_id')} ORDER BY created_at,id""", (stamp,)).fetchall()
                    if len({row["content_sha256"] for row in left}) == 1:
                        db.execute("UPDATE scan_pages SET review_state='candidate',updated_at=? WHERE id=? AND review_state='conflict'", (when, left[0]["id"]))
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
            # Only a paper with a copy to move to is recorded as held once: the event is the note the scan's page shows.
            if moved or not db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='photocopied_papers'", (job["id"],)).fetchone():
                self.inbox.event(db, job["id"], "photocopied_papers", encoded({"rule": RULE, "moved": moved, "held": held, "notes": [
                    f"{len(held)} photocopied paper(s) found no unused copy of their print job; choose their pages by hand."] if held else []}))
            unnamed = []
            for copy in renamed:
                row = db.execute("""SELECT c.enrollment_id,(SELECT e.actor FROM document_events e WHERE e.copy_id=c.id AND e.kind='copy_assignment'
                        ORDER BY e.rowid DESC LIMIT 1) actor FROM printed_copies c WHERE c.id=?""", (copy,)).fetchone()
                if row["enrollment_id"] and row["actor"] == "automatic":
                    unnamed.append(copy)
        for copy in unnamed:
            try:
                self.documents.assign_copy(copy, None, "Its stamps were photocopied: each paper moved to its own copy, named from the paper.",
                                           actor="automatic")
            except Exception:  # noqa: BLE001 - the name stays for the instructor to change; the papers moved regardless
                LOG.exception("Clearing the name read for photocopied copy %s failed", copy)
        if moved:
            self.scans.copy_names.name_confident(message_id)
            if self.scans.grading:
                try:
                    self.scans.grading.sync_scan(message_id)
                except Exception:  # noqa: BLE001 - the grading catch-up tries again
                    LOG.exception("Linking scan %s after its photocopied papers moved failed", message_id)
        return bool(moved)
