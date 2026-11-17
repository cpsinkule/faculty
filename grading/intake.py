"""Scan arrivals reference a term exam; assignment definitions remain course-wide.

What this module holds for a scan: its association with an assignment (`frq_scan_links`, written by `_scan_link` for the instructor's
choice, for the labels' own assignment in `sync_scan`, and for a found assignment in `associate_found`), the catch-up pass that groups
retained scans (`process_scan_one`), the instructor's and the worker's review of a scan (`_review_scan`, `auto_review_one`), the later
assembly of papers made ready after the review (`assemble_later_one`), and the assignment's grading entry and workflow summary.
Linking itself identifies no page or student, but a scan created with a new title or found by the assignment finder starts the
unlabeled-paper read once the link is saved (`_read_unlabeled`), and students identified with confidence are registered there.
All writes hold the roster lifecycle lock and retain association history.
"""
import json
import logging
import time

from werkzeug.exceptions import Conflict, Forbidden
from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT
from document_identity.scans import paper_sections
from .exam_versions import DECIDE, OtherExamVersion, VersionUndecided
from .models import digest, text, uid
from .progress import (automatic_decisions, certification_summary, decision_rows, export_summary, response_counts, response_progress,
                       setup_summary)

LOG = logging.getLogger(__name__)
GROUPING_RULE = "recovered-label-grouping-v4"  # a new name makes the worker look at every scan's grouping once more (`process_scan_one`); v3: repeats (2026-10-25); v4: pages by their place (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS", 2026-11-14)


class IntakeMixin:
    def scan_status(self, db, message_id, fingerprint=True):
        message = self.inbox.message(db, message_id)
        job = db.execute("SELECT id,version,action,parameters_json FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
        if not job or job["action"] != "scan.receive":
            raise ValueError("Choose a scan in the Inbox.")
        link = db.execute("SELECT * FROM frq_scan_links WHERE message_id=? ORDER BY id DESC LIMIT 1", (message_id,)).fetchone()
        plan = self.plan(db, link["assignment_id"]) if link else None
        if plan:
            plan["section_ids"] = [s["id"] for s in self.exams._sections(db, plan["event_id"])]
        decode = db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
        pages = [tuple(row) for row in db.execute("SELECT id,page_stamp_id,status,review_state,updated_at FROM scan_pages WHERE message_id=? ORDER BY id", (message_id,))] if fingerprint else []
        bubble = db.execute("SELECT id,assignment_id,term_id FROM bubble_runs WHERE message_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (message_id,)).fetchone() if fingerprint else None
        return {"plan": plan, "source": link["source"] if link else None, "job_id": job["id"], "job_version": job["version"],
                "review_required": bool(message["instructor_review_required"]), "inspected": bool(decode and decode[0] == "complete"),
                "pages": len(pages), "fingerprint": digest([dict(link) if link else None, message["version"], job["version"], decode[0] if decode else None, pages, tuple(bubble) if bubble else None]) if fingerprint else None,
                "href": f"#grading/{plan['id']}" if plan else None}

    def scan_context(self, message_id):
        with self.rosters.lock(), connect(self.path) as db:
            # One recognition for both: the overview would otherwise read every label of the scan a second time.
            result = {**self.scan_status(db, message_id), "recognition": self.exams.scans.recognition(db, message_id)}
            overview = self.exams.scans.overview(db, message_id, recognition=result["recognition"])
            result['blockers'] = self.scan_blockers(db, message_id, overview=overview)
            result['review_blockers'] = self.review_blockers(db, message_id, overview=overview)
            # Reviewed, its papers are in grading; what is still flagged in the Inbox waits there on its own (2026-09-28).
            result['ready'] = bool(result['plan'] and not result['review_required'])
            if not result['ready']:
                result['href'] = f"#inbox/job/{result['job_id']}"
            return result

    def scan_blockers(self, db, message_id, overview=None):
        """Everything still open on the scan: its own steps and every flagged paper, sheet or page. A scan with any is not finished
        in the Inbox (`inbox_ready`, certification's "Papers may still be in the Inbox"), whether or not it passed review."""
        overview = overview or self.exams.scans.overview(db, message_id)
        blockers = [s['text'] for s in overview['steps'] if s['kind'] in ('todo', 'check', 'wait') and s.get('action') != 'grading_review']
        if overview['inspection'] != 'complete' and not blockers:
            blockers.append('Finish inspecting this scan in the Inbox.')
        return blockers

    def review_blockers(self, db, message_id, overview=None):
        """What holds the scan's REVIEW back: the steps for the whole scan (`holds: scan`: its inspection, its assignment, labels naming
        two assignments) and the worker's own work on it (`wait`). A flagged paper, sheet or page holds back only itself (instructor,
        2026-09-28: "every paper/response etc automatically proceeds to the next step unless it gets flagged for some reason needing
        manual review"): the review passes with it still open, the rest goes on to grading, and it joins them once settled."""
        overview = overview or self.exams.scans.overview(db, message_id)
        blockers = [s['text'] for s in overview['steps'] if s.get('action') != 'grading_review'
                    and (s['kind'] == 'wait' or s['kind'] in ('todo', 'check') and s.get('holds') == 'scan')]
        if overview['inspection'] != 'complete' and not blockers:
            blockers.append('Finish inspecting this scan in the Inbox.')
        return blockers

    def grading_entry(self, db, plan, shared=None):
        """An assignment enters grading once one of its scans passed review; later arrivals do not hide prior work. A reviewed scan
        with papers still flagged in the Inbox counts as cleared (its other papers are in grading) and as pending (those wait).
        `shared`: the caller's per-read memo; the scan overviews read here are kept in it for `workflow_summary` in the same read."""
        if db.execute('SELECT 1 FROM frq_responses WHERE assignment_id=? LIMIT 1', (plan['id'],)).fetchone():
            return {'ready': True, 'retained_grades': True, 'inbox_href': '#inbox'}
        # The scans holding a page of this exam's assembled papers are read once, not once per Inbox message (2026-10-24: with 245
        # papers of Exam 2 assembled from home-scanned stacks, the correlated form took 45 s and held a server thread so long that
        # scan pages could not load).
        rows = db.execute(f'''SELECT DISTINCT m.id,m.instructor_review_required,j.id job_id FROM inbox_messages m
            JOIN inbox_jobs j ON j.message_id=m.id WHERE m.authenticated=1 AND {PRESENT.format('m.id')}
            AND (EXISTS(SELECT 1 FROM frq_scan_links l WHERE l.message_id=m.id AND l.assignment_id=?
                AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=m.id))
              OR m.id IN (SELECT sp.message_id FROM exam_event_batches eb JOIN printed_copies c ON c.print_batch_id=eb.batch_id
                JOIN assembly_revisions ar ON ar.printed_copy_id=c.id JOIN json_each(ar.selection_json) chosen
                JOIN scan_pages sp ON sp.id=json_extract(chosen.value,'$.scan_page_id') WHERE eb.event_id=?))''',
            (plan['id'], plan['event_id'])).fetchall()
        cleared, pending = [], []
        shared = {} if shared is None else shared
        overviews = shared.setdefault('scan_overviews', {})
        for row in rows:
            overview = overviews.get(row['id']) or self.exams.scans.overview(db, row['id'], memo=shared.setdefault('booklets', {}))
            overviews[row['id']] = overview
            # Older reviewed assemblies already have an explicit assignment via
            # the attached print batch, even before scan-link records existed.
            linked = self.scan_status(db, row['id'], fingerprint=False)['plan']
            if linked and linked['id'] != plan['id']:
                continue
            blockers = [s for s in overview['steps'] if s['kind'] in ('todo','check','wait') and s.get('action') != 'grading_review'
                        and not (s.get('action') == 'grading_associate' and not linked)]
            if not row['instructor_review_required'] and overview['inspection'] == 'complete':
                cleared.append(row['job_id'])
            if row['instructor_review_required'] or overview['inspection'] != 'complete' or blockers:
                pending.append(row['job_id'])
        # A quiz drafted and approved in Faculty (2026-09-28) has its grading set up by that approval, before any paper exists: it is on
        # Grading from then on, so its rubrics can be finalized while the papers are still being written. So is a plan the instructor made
        # with Set up grading now (2026-10-02: `set_up_grading`, its `assignment_created` event marked `before_papers`; one indexed read).
        # Every other plan waits for a scan.
        drafted = bool(db.execute("SELECT 1 FROM quiz_draft_approvals WHERE status='complete' AND json_extract(result_json,'$.plan_id')=? LIMIT 1",
                                  (plan['id'],)).fetchone())
        early = not drafted and bool(db.execute("""SELECT 1 FROM frq_events WHERE assignment_id=? AND kind='assignment_created'
            AND json_extract(detail_json,'$.before_papers')=1 LIMIT 1""", (plan['id'],)).fetchone())
        return {'ready': bool(cleared) or drafted or early, 'cleared_scans': len(cleared), 'pending_scans': len(pending),
                'inbox_href': f'#inbox/job/{pending[0]}' if pending else '#inbox', **({'set_up_by_quiz_draft': True} if drafted else {}),
                **({'set_up_before_papers': True} if early else {})}

    def _scan_allowed(self, db, message_id):
        message = self.inbox.message(db, message_id)
        if not message["authenticated"]:
            raise Forbidden("Authentication failures cannot be associated or reviewed.")
        if not db.execute(f"SELECT 1 FROM inbox_messages m WHERE m.id=? AND {PRESENT.format('m.id')}", (message_id,)).fetchone():
            raise Conflict("Restore this scan in the Inbox before associating it.")
        state = self.scan_status(db, message_id)
        if not state["inspected"] or not state["pages"]:
            raise Conflict("Wait for page inspection to finish before associating this scan.")
        return state

    def _scan_link(self, db, message_id, data, source):
        state = self._scan_allowed(db, message_id)
        if data.get("fingerprint") != state["fingerprint"]:
            raise Conflict("This scan changed. Reopen it before choosing its assignment.")
        recognition = self.exams.scans.recognition(db, message_id)
        if recognition["verdict"] == "mixed":
            raise Conflict("Resolve labels naming more than one assignment, or of no current paper, in the scan checks before associating this scan.")
        term_id, assignment_id = data.get("term_id"), data.get("assignment_id")
        term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone()
        if not term:
            raise ValueError("Choose a course and teaching term.")
        # Labels of paper that is not current are flags on their own pages (2026-09-28): they neither choose nor refuse the assignment,
        # and their print jobs never join the exam.
        papers = recognition.get("current_papers", recognition["papers"])
        if papers and any(p["term_id"] != term_id or p["assignment_id"] != assignment_id for p in papers):
            raise Conflict("The selected assignment does not match this scan's printed labels.")
        # An assignment made from a scan carries no labels (requirement, 2026-09-24): its versions are declared here, and the scan's
        # papers are found and read by OCR at the scanned sides per paper (`associate_scan` starts that once this link is saved).
        count, sides = data.get("version_count"), data.get("sides")
        if (count is not None or sides is not None) and not data.get("new_title"):
            raise ValueError("The number of versions and the sides per paper are given when an assignment is created from a scan.")
        if count is not None and (type(count) is not int or not 0 <= count <= 26):
            raise ValueError("Enter the number of versions, from 0 to 26 (0 when every paper is the same exam).")
        if sides is not None and (type(sides) is not int or not 1 <= sides <= 200):
            raise ValueError("Enter the scanned sides per paper, between 1 and 200.")
        if data.get("new_title"):
            if assignment_id or papers:
                raise ValueError("Choose an existing assignment or create one, not both.")
            assignment_id = self.exams.documents._create_assignment(db, term["definition_id"],
                "scan-" + data["request_id"], text(data["new_title"], "Assignment title", 240, False))["id"]
        definition = db.execute("SELECT definition_id FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not definition or definition[0] != term["definition_id"]:
            raise ValueError("Choose an assignment belonging to this course.")
        sections = data.get("section_ids")
        if not isinstance(sections, list) or not sections or any(not isinstance(s, str) for s in sections) or len(set(sections)) != len(sections):
            raise ValueError("Choose distinct sections for this assessment.")
        for section in sections:
            if not db.execute("SELECT 1 FROM course_sections WHERE id=? AND term_id=?", (section, term_id)).fetchone():
                raise ValueError("Every section must belong to the chosen term.")
        if not {section for p in papers for section in paper_sections(p)}.issubset(sections):
            raise ValueError("Include every section identified in this scan.")
        existing = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (term_id, assignment_id)).fetchone()
        if existing:
            event_id = existing[0]
            current = {s["id"] for s in self.exams._sections(db, event_id)}
            extra = set(sections) - current
            if extra:
                if source == "automatic":
                    raise Conflict("This scan includes sections outside the assessment. Confirm its sections first.")
                db.executemany("INSERT INTO exam_event_sections VALUES(?,?)", [(event_id, s) for s in sorted(extra)])
                self.exams._add_members(db, event_id)
                self.exams._touch(db, event_id)
                self.exams._history(db, event_id, "scan_sections_added", {"message_id": message_id, "section_ids": sorted(extra)})
        else:
            event_id = self.exams._create(db, {"term_id": term_id, "assignment_id": assignment_id, "section_ids": sections})
        for paper in papers:
            old = db.execute("SELECT event_id FROM exam_event_batches WHERE batch_id=?", (paper["batch_id"],)).fetchone()
            if old and old[0] != event_id:
                raise Conflict("A printed batch already belongs to another exam.")
            if not old:
                db.execute("INSERT INTO exam_event_batches VALUES(?,?,NULL,?)", (event_id, paper["batch_id"], timestamp()))
                self.exams._touch(db, event_id)
                self.exams._history(db, event_id, "scan_batch_attached", {"message_id": message_id, "batch_id": paper["batch_id"], "actor": source})
        row = db.execute("SELECT id FROM frq_assignments WHERE event_id=?", (event_id,)).fetchone()
        aid = row[0] if row else uid()
        if not row:
            db.execute("INSERT INTO frq_assignments VALUES(?,?,1,?,?)", (aid, event_id, timestamp(), timestamp()))
            self.event(db, aid, "assignment_created", {"event_id": event_id}, source)
        if data.get("new_title") and count is not None:
            db.execute("INSERT INTO frq_declared_versions VALUES(?,?,?,?,?,?)", (uid(), aid, count, data["request_id"], source, timestamp()))
            self.event(db, aid, "versions_declared", {"message_id": message_id, "versions_count": count}, source)
        db.execute("INSERT INTO frq_scan_links(message_id,assignment_id,source,request_id,request_sha256,created_at) VALUES(?,?,?,?,?,?)",
                   (message_id, aid, source, data["request_id"], digest(data), timestamp()))
        self.event(db, aid, "scan_associated", {"message_id": message_id, "previous_assignment_id": state["plan"]["id"] if state["plan"] else None}, source)
        self.touch(db, aid)
        self.inbox.event(db, state["job_id"], "grading_associated", json.dumps({"assignment_id": aid, "actor": source}))
        return self.scan_status(db, message_id)

    def associate_scan(self, message_id, data):
        text(data.get("request_id"), "Request ID", 128, False)
        text(data.get("term_id"), "Teaching term", 128, False)
        if data.get("assignment_id") is not None:
            text(data["assignment_id"], "Assignment", 128)
        with self.rosters.lock():
            with connect(self.path) as db:
                old = db.execute("SELECT * FROM frq_scan_links WHERE request_id=?", (data["request_id"],)).fetchone()
                if old:
                    if old["message_id"] != message_id or old["request_sha256"] != digest(data):
                        raise Conflict("This request ID was used for a different association.")
                    self._scan_allowed(db, message_id)
                    return self.scan_status(db, message_id)
                term = db.execute("SELECT definition_id FROM course_terms WHERE id=?", (data.get("term_id"),)).fetchone()
                if not term:
                    raise ValueError("Choose a course and teaching term.")
            with self.exams.documents.files.owner_lock(term[0]), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                state = self._scan_link(db, message_id, data, "workspace")
            if data.get("new_title") and data.get("sides"):
                self._read_unlabeled(message_id, state["plan"], data["sides"])
                with connect(self.path) as db:
                    state = self.scan_status(db, message_id)
            return state

    def associate_found(self, message_id, assignment_id, term_id):
        """Associate a scan without labels with the assignment its first pages were found to be (`document_identity.assignment_finder`,
        requirement, 2026-09-27), as the instructor's own association would, actor `automatic`: the exam's sections when the term has
        its exam already, else every section of the term. Then its first pages are read and confident students registered, at the
        sides of the assignment's blank template, as for an assignment created from a scan. Nothing is done for a scan someone
        associated meanwhile."""
        try:
            self.dr_copy(assignment_id, term_id)  # DR COPIES: a quiz's papers without labels are counted at its DR copy's sides
        except (ValueError, Conflict):
            pass  # the copy could not be made: its papers say why when they are registered
        with self.rosters.lock():
            with connect(self.path) as db:
                term = db.execute("SELECT definition_id FROM course_terms WHERE id=?", (term_id,)).fetchone()
                if not term:
                    raise ValueError("That teaching term no longer exists.")
            with self.exams.documents.files.owner_lock(term[0]), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                state = self._scan_allowed(db, message_id)
                if state["plan"]:
                    return None
                event = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (term_id, assignment_id)).fetchone()
                sections = [s["id"] for s in self.exams._sections(db, event[0])] if event else \
                    [row[0] for row in db.execute("SELECT id FROM course_sections WHERE term_id=? ORDER BY number", (term_id,))]
                data = {"term_id": term_id, "assignment_id": assignment_id, "section_ids": sections, "fingerprint": state["fingerprint"],
                        "request_id": f"found:{message_id}"}
                state = self._scan_link(db, message_id, data, "automatic")
                sides = self.exams.scans.unlabeled.paper_sides(db, assignment_id)
            if sides:
                self._read_unlabeled(message_id, state["plan"], sides)
            with connect(self.path) as db:
                return self.scan_status(db, message_id)

    def _read_unlabeled(self, message_id, plan, sides):
        """After the link is saved: find the papers of a scan without labels, read who wrote them and which declared version they are,
        and register those identified with confidence (requirement, 2026-09-24). A scan with nothing to read, or a request refused, is
        noted on the scan; the association stands and the scan's own tool can be asked again."""
        unlabeled = getattr(self.exams.scans, "unlabeled", None)
        if unlabeled is None:
            return
        with connect(self.path) as db:
            letters = "".join(self.declared_versions(db, plan))
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
            pending = any(unlabeled._attachments(db, message_id))
            copied = bool(self.dr_copies(db, plan["source_assignment_id"]))
        try:
            if not pending:
                raise ValueError("This scan has no unlabeled pages that are not bubble sheets.")
            # The document its papers are registered under (as registering would make it), so every version's rubrics can be uploaded
            # while the first pages are still being read. A quiz with a DR copy has that document already (DR COPIES).
            if not copied:
                self.exams.documents.register_stamp_pages(plan["source_assignment_id"], 1)
            unlabeled.request(message_id, {"assignment_id": plan["source_assignment_id"], "sides": sides}, versions=letters, register_confident=True)
        except (ValueError, Conflict) as error:
            with connect(self.path) as db:
                self.inbox.event(db, job["id"], "unlabeled_exams_not_started", json.dumps({"reason": str(error)}))

    def sync_scan(self, message_id):
        """Reuse current printed identities or an instructor's explicit sheet-read scope."""
        with self.rosters.lock(), connect(self.path) as db:
            state = self.scan_status(db, message_id)
            if not state["inspected"]:
                return False
            r = self.exams.scans.recognition(db, message_id)
            current = r.get("current_papers", r["papers"])
            sections = sorted({section for p in current for section in paper_sections(p)})
            if r["verdict"] == "recognized":
                p = current[0]
            elif r["verdict"] == "bubble_sheets":
                # Reuse the scope explicitly chosen for a read, never guess from a sheet.
                run = db.execute("SELECT id,assignment_id,term_id FROM bubble_runs WHERE message_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (message_id,)).fetchone()
                if not run or state["plan"]:
                    return False
                p = dict(run)
                sections = [s[0] for s in db.execute("SELECT section_id FROM bubble_run_sections WHERE run_id=?", (run["id"],))]
            else:
                return False
            if state["plan"]:
                plan = state["plan"]
                if plan["term_id"] != p["term_id"] or plan["source_assignment_id"] != p["assignment_id"]:
                    return False
                if all(db.execute("SELECT 1 FROM exam_event_batches WHERE event_id=? AND batch_id=?", (plan["event_id"], p["batch_id"])).fetchone() for p in current):
                    return False
            data = {"term_id": p["term_id"], "assignment_id": p["assignment_id"],
                    "section_ids": sections,
                    "fingerprint": state["fingerprint"], "request_id": "auto:" + message_id + ":" + state["fingerprint"][:20]}
            try:
                db.execute("SAVEPOINT scan_link")
                self._scan_link(db, message_id, data, "automatic")
            except (ValueError, Conflict, Forbidden):
                db.execute("ROLLBACK TO scan_link")
                return False
            finally:
                db.execute("RELEASE scan_link")
            return True

    def process_scan_one(self):
        """Catch up retained scans and recovered labels, one changed scan per turn."""
        with self.inbox.worker_lock(), self.rosters.lock():
            with connect(self.path) as db:
                rows = db.execute(f"""SELECT d.message_id,d.rowid cursor FROM document_decode_jobs d JOIN inbox_messages m ON m.id=d.message_id
                    JOIN inbox_jobs j ON j.message_id=m.id WHERE d.status='complete' AND j.action='scan.receive' AND m.authenticated=1
                    AND {PRESENT.format('m.id')}
                    AND d.rowid>? ORDER BY d.rowid LIMIT 50""", (getattr(self, "_scan_cursor", 0),)).fetchall()
                for row in rows:
                    self._scan_cursor = row["cursor"]
                    state = self.scan_status(db, row[0])
                    # PIPELINE: revisit old cursors once so already-recovered labels also reach grouping. v2 (2026-10-24): bubble sheets
                    # and their backs are no sides of a paper (INBOX "BATCHES SCANNED IN STACKS"), so scans held for that look again.
                    fingerprint = digest([GROUPING_RULE, state["fingerprint"]])
                    previous = db.execute("SELECT fingerprint FROM frq_scan_checks WHERE message_id=?", (row[0],)).fetchone()
                    if previous and previous[0] == fingerprint:
                        continue
                    # OCR / DOCUMENTS: wait for the remaining labels before inferring a cadence. Do not acknowledge this scan yet.
                    if db.execute("SELECT 1 FROM scan_alias_ocr WHERE message_id=? AND status IN ('pending','running') LIMIT 1", (row[0],)).fetchone():
                        continue
                    # Nor before every page was checked for being a bubble sheet: a sheet is no side of a paper.
                    detector = getattr(self.exams.scans, "bubble_detector", None)
                    if detector and detector._counts(db, row[0])["pending"]:
                        continue
                    message_id = row[0]
                    break
                else:
                    if not rows:
                        self._scan_cursor = 0
                    return False
            self.sync_scan(message_id)
            try:  # DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS": again whenever the scan changed (a back the instructor set aside, say)
                self.exams.scans.places.apply(message_id)
            except Exception:
                LOG.exception("Pages by their place on scan %s did not complete", message_id)
            self.exams.scans.sequence.auto_group(message_id)  # DOCUMENTS: unchanged all-or-nothing rule; a prior run/undo is never repeated.
            with connect(self.path) as db:
                fingerprint = digest([GROUPING_RULE, self.scan_status(db, message_id)["fingerprint"]])
                db.execute("INSERT INTO frq_scan_checks VALUES(?,?) ON CONFLICT(message_id) DO UPDATE SET fingerprint=excluded.fingerprint", (message_id, fingerprint))
        return True

    def review_scan(self, message_id, data):
        if data.get("confirm_review") is not True:
            raise ValueError("Confirm that you have reviewed this scan.")
        return self._review_scan(message_id, data.get("fingerprint"))

    def _review_scan(self, message_id, fingerprint, actor="workspace"):
        """Clear the scan's review. `fingerprint` None (the worker's automatic pass) reads the scan as it is now; every other
        check is the instructor's review's own, under the same lock. Only the steps for the whole scan hold it back
        (`review_blockers`); what is still flagged on single papers, sheets or pages is named in a `review_exceptions` Inbox event
        and stays open in the Inbox, while the rest of the scan goes on (requirement, 2026-09-28)."""
        with self.rosters.lock():
            with connect(self.path) as db:
                state = self._scan_allowed(db, message_id)
                if not state["plan"] or (fingerprint is not None and state["fingerprint"] != fingerprint):
                    raise Conflict("This scan changed. Refresh its assignment and review it again.")
                r = self.exams.scans.recognition(db, message_id)
                overview = self.exams.scans.overview(db, message_id, recognition=r)
                blockers = self.review_blockers(db, message_id, overview=overview)
                if blockers:
                    raise Conflict('Finish the steps for the whole scan before its review: ' + ' '.join(blockers))
                plan = state["plan"]
                if r["verdict"] == "mixed" or any(p["assignment_id"] != plan["source_assignment_id"] or p["term_id"] != plan["term_id"]
                                                  for p in r.get("current_papers", r["papers"])):
                    raise Conflict("The labels conflict with this assignment. Resolve them before reviewing.")
                command = json.loads(db.execute("SELECT parameters_json FROM inbox_jobs WHERE id=?", (state["job_id"],)).fetchone()[0])
                command["target"] = {"level": "term", "id": plan["term_id"]}
                left = self.scan_blockers(db, message_id, overview=overview) if state["review_required"] else []
            if state["review_required"]:
                self.inbox.transition(state["job_id"], "review", state["job_version"], command, actor=actor)
                if left:
                    with connect(self.path) as db:
                        self.inbox.event(db, state["job_id"], "review_exceptions", json.dumps({"actor": actor, "left": left[:20], "count": len(left)}))
            return self.scan_context(message_id)

    AUTO_REVIEW_EVERY = 20  # seconds between looks at one scan that still has something to check

    def auto_review_one(self):
        """A scan passes its Review stage by itself (requirement, 2026-09-27), and papers flagged on it no
        longer hold the rest back (2026-09-28: "every paper/response etc automatically proceeds to the next step unless it gets
        flagged"). Only when nothing about the WHOLE scan is open (`review_blockers`: an assignment, every inspection finished, no
        label naming another assignment, none of the worker's own reading still under way) and no background reading is shown in
        progress. It is the ordinary review (`_review_scan`, actor `automatic`), with the same checks, the same assembly of the
        papers that are ready and the same `reviewed` Inbox event naming who cleared it; the flagged papers stay in the Inbox."""
        now, seen = time.monotonic(), getattr(self, "_auto_review_seen", {})
        self._auto_review_seen = seen
        with self.rosters.lock(), connect(self.path) as db:
            rows = [row[0] for row in db.execute(f"""SELECT m.id FROM inbox_messages m JOIN inbox_jobs j ON j.message_id=m.id
                JOIN document_decode_jobs d ON d.message_id=m.id WHERE m.instructor_review_required=1 AND m.authenticated=1
                AND j.action='scan.receive' AND j.status='needs_review' AND j.attempts=0 AND d.status='complete'
                AND EXISTS(SELECT 1 FROM frq_scan_links l WHERE l.message_id=m.id) AND {PRESENT.format('m.id')}
                ORDER BY m.created_at""")]
            ready, memo = None, {}  # memo: this read's booklet versions, shared by every scan looked at
            for message_id in rows:
                if now - seen.get(message_id, -self.AUTO_REVIEW_EVERY) < self.AUTO_REVIEW_EVERY:
                    continue
                seen[message_id] = now
                overview = self.exams.scans.overview(db, message_id, memo=memo)
                steps = overview["steps"]
                if (overview["inspection"] == "complete" and overview["verdict"] != "mixed"
                        and any(s.get("action") == "grading_review" and s["kind"] == "todo" for s in steps)
                        and not any(s.get("progress") for s in steps) and not self.review_blockers(db, message_id, overview=overview)):
                    ready = message_id
                    break
            for gone in set(seen) - set(rows):
                seen.pop(gone, None)
        if not ready:
            return False
        try:
            self._review_scan(ready, None, actor="automatic")
        except (ValueError, Conflict, Forbidden) as error:
            LOG.info("Scan %s was not reviewed automatically: %s", ready, error)
            return False
        return True

    ASSEMBLE_RECHECK = 900  # Eligibility also depends on other scans, submission choices and restored sources.
    ASSEMBLE_RETRY = 20

    def assemble_later_one(self):
        """Papers made ready AFTER their scan passed its review follow the rest of it to grading (requirement, 2026-09-28): a first page
        registered on Find students or grouped on Papers to check, or a copy labeled on every page whose last page arrived in a later
        scan. The ordinary assembly of the review (`SequenceService.assemble_reviewed`, actor `automatic`), for one reviewed scan per
        turn whose unassembled papers changed since it was last looked at; anything it leaves stays with the per-copy assembly review."""
        seen = getattr(self, "_assemble_seen", None)
        if seen is None:
            seen = self._assemble_seen = {}
        retries = getattr(self, "_assemble_retries", None)
        if retries is None:
            retries = self._assemble_retries = {}
        now = time.monotonic()
        with self.rosters.lock(), connect(self.path) as db:
            present = PRESENT.format("m.id")
            waiting = {}
            for row in db.execute(f"""SELECT k.message_id,k.id FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id
                    JOIN inbox_messages m ON m.id=k.message_id JOIN printed_copies c ON c.id=k.printed_copy_id
                    JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                    WHERE r.undone_at IS NULL AND m.instructor_review_required=0 AND m.authenticated=1 AND {present}
                      AND b.status='ready' AND b.superseded_at IS NULL
                      AND (b.label_mode='first_page' OR json_extract(v.compiler_json,'$.generator')='faculty-stamps-only')
                      AND NOT EXISTS(SELECT 1 FROM assembly_revisions ar WHERE ar.printed_copy_id=k.printed_copy_id)"""):
                waiting.setdefault(row[0], []).append(row[1])
            for row in db.execute(f"""SELECT DISTINCT sp.message_id,c.id FROM scan_pages sp JOIN inbox_messages m ON m.id=sp.message_id
                    JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                    JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                    WHERE sp.status IN ('resolved','manual') AND m.instructor_review_required=0 AND m.authenticated=1 AND {present}
                      AND b.label_mode='every_page' AND b.status='ready' AND b.superseded_at IS NULL
                      AND COALESCE(json_extract(v.compiler_json,'$.generator'),'')!='faculty-stamps-only'
                      AND NOT EXISTS(SELECT 1 FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id)"""):
                waiting.setdefault(row[0], []).append(row[1])
            for gone in set(seen) - set(waiting):
                seen.pop(gone, None)
            for gone in set(retries) - set(waiting):
                retries.pop(gone, None)
            due = None
            for message_id, items in sorted(waiting.items()):
                # What is waiting, and what the scan's pages say about it: a page identified or corrected since changes this.
                marker = digest([sorted(items), db.execute("SELECT MAX(updated_at) FROM scan_pages WHERE message_id=?", (message_id,)).fetchone()[0]])
                previous = seen.get(message_id)
                retry = retries.get(message_id)
                if retry and retry[0] == marker and now < retry[1]:
                    continue
                if retry or not previous or previous[0] != marker or now - previous[1] >= self.ASSEMBLE_RECHECK:
                    due = message_id
                    break
        if not due:
            return False
        try:
            result = self.exams.scans.sequence.assemble_reviewed(due, "automatic", quiet=True)
        except Exception as error:
            # A failed attempt is not a completed eligibility check. Leave its marker unacknowledged.
            count = retries[due][2] + 1 if due in retries and retries[due][0] == marker else 1
            retries[due] = (marker, now + min(300, self.ASSEMBLE_RETRY * 2 ** min(count - 1, 4)), count)
            LOG.info("Papers of reviewed scan %s were not assembled automatically: %s", due, error)
            return False
        if result.get("retry"):
            count = retries[due][2] + 1 if due in retries and retries[due][0] == marker else 1
            retries[due] = (marker, now + min(300, self.ASSEMBLE_RETRY * 2 ** min(count - 1, 4)), count)
        else:
            seen[due] = (marker, now)
            retries.pop(due, None)
        return bool(result["assembled"])

    def workflow_summary(self, db, plan, responses=None, context=None, details=None, shared=None, quality=None, export=True):
        """The plan's workflow counts. `context` = (event, participants, collection) when the caller already read them (the
        participants with their `collection`; Certification and Export reads them once per page), else they are read here.
        `details`, when a dict, receives what the counts were made of, per student (Certification and Export names who waits):
        `new` {(section_id, enrollment_id): count} of expected questions not graded yet, `blocked` {key: [reason]} of questions
        whose paper cannot be graded, `undecided` {key: paper version} waiting for the instructor's version decision, `failed`
        {response_id} whose latest task failed, `again` {response_id} to grade again, `decisions` (`decision_rows`), `event`,
        `participants`, `collection`. `shared` is the caller's per-read memo (`source`, `paper_version`, the listing's questions) and
        `quality` its rubric finalization status, when it already has them. `export=False` leaves `export` out for a caller that
        discards it (the Assignments list, Home and Exams' `grading_summary`): it reads the latest export's whole snapshot, about 3 s
        over the term's exports whenever `_EXPORTED` is cold, as after every restart (PERFORMANCE, 2026-11-01). Reads only; the caller
        holds the lifecycle lock."""
        # One read of the exam's participants and papers, one memo and one finalization status for the listing and the counts below
        # (performance audit, 2026-09-29: each paper's source and version were read twice per plan).
        shared = {} if shared is None else shared
        if context is None:
            event = self.exams._event(db, plan["event_id"])
            participants = self.exams._participants(db, event)
            collection = self.exams._collection(db, event, participants)
        else:
            event, participants, collection = context
        quality = plan.get('rubric_quality') or quality or self.rubric_quality_status(db, plan['id'])
        if responses is None:
            responses = self._responses(db, plan, event, participants, shared=shared, quality=quality)
        # `filename` (the scan's first uploaded file, in upload order), `files` (how many it uploaded) and `papers` (the printed
        # copies its pages were matched to, labels read or registered) name each row of Scan records; additive, read only. The
        # files are found by the Inbox's own input path (`inputs/<id>.bin`, teach_inbox.service.submit) on the (message_id, path)
        # index: a scan job holds thousands of output artifacts (0.2 ms instead of 5 ms for Exam 1's 18 scans on the live copy).
        # Inputs share one created_at and random ids, so upload order is their insertion order (rowid).
        scans = [dict(r) for r in db.execute(f"""SELECT l.message_id,j.id job_id,m.created_at,m.instructor_review_required,
            (SELECT status FROM document_decode_jobs d WHERE d.message_id=m.id) inspection_status,
            (SELECT COUNT(*) FROM scan_pages p WHERE p.message_id=m.id) pages,
            (SELECT a.filename FROM inbox_artifacts a WHERE a.message_id=m.id AND a.path>='inputs/' AND a.path<'inputs0' AND a.role='input'
             ORDER BY a.rowid LIMIT 1) filename,
            (SELECT COUNT(*) FROM inbox_artifacts a WHERE a.message_id=m.id AND a.path>='inputs/' AND a.path<'inputs0' AND a.role='input') files,
            (SELECT COUNT(DISTINCT s.printed_copy_id) FROM scan_pages p JOIN page_stamps s ON s.id=p.page_stamp_id
             WHERE p.message_id=m.id AND p.status IN ('resolved','manual')) papers
            FROM frq_scan_links l JOIN inbox_messages m ON m.id=l.message_id JOIN inbox_jobs j ON j.message_id=m.id
            WHERE l.assignment_id=? AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id)
            AND {PRESENT.format('m.id')} ORDER BY l.id DESC""", (plan["id"],))]
        for scan in scans:
            # The memo keeps each overview read here, so a caller in the same read (the Assignments list) does not read it again.
            scan['inbox_ready'] = False
            if not scan['instructor_review_required']:
                overviews = shared.setdefault('scan_overviews', {})
                overview = overviews[scan['message_id']] = overviews.get(scan['message_id']) or self.exams.scans.overview(
                    db, scan['message_id'], memo=shared.setdefault('booklets', {}))  # each exam's booklet versions read once
                scan['inbox_ready'] = not self.scan_blockers(db, scan['message_id'], overview=overview)
        automatic = automatic_decisions(db, plan["id"])  # one read, shared by the counts, the export and the certification
        counts = response_counts(responses, automatic)
        pending = db.execute("SELECT COUNT(*) FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id WHERE j.assignment_id=? AND t.status IN ('pending','running')", (plan["id"],)).fetchone()[0]
        failed = {row[0] for row in db.execute("""SELECT t.response_id FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id WHERE j.assignment_id=?
            AND t.status='failed' AND t.rowid=(SELECT MAX(n.rowid) FROM frq_tasks n WHERE n.response_id=t.response_id)""", (plan["id"],))}
        per_student = details if isinstance(details, dict) else None
        if per_student is not None:
            per_student.update(new={}, blocked={}, undecided={}, event=event, participants=participants, collection=collection)
        questions = plan.get("questions")
        if questions is None:  # each read once in this read: the listing keeps those it read in the memo
            known = shared.get("read_questions") or {}
            questions = [known.get(q[0]) or self.question(db, plan["id"], q[0])
                         for q in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=?", (plan["id"],))]
        new, blocked, configured, regrade, undecided, per_version, checks = 0, 0, 0, 0, {}, {}, {}
        again = self.reset_ungraded(db, plan["id"])  # A batch reset left these without a proposal: new work for the next batch.
        again |= self.outdated(db, plan["id"], responses)  # Graded under an earlier rubric, question or paper: graded again.
        if per_student is not None:
            per_student.update(failed=set(failed - again), again=set(again))
        failed = len(failed - again)  # A failure under an earlier rubric is no batch's to retry: it is new work.
        withdrawn = {(r["question_id"], r["section_id"], r["enrollment_id"]) for r in responses if r["id"] in again}
        existing = {(r["question_id"], r["section_id"], r["enrollment_id"]) for r in responses if r["id"] not in again}
        paper_versions = None
        for q in questions:
            if not q["rubric"] or not q["rubric"]["approved_at"]:
                continue
            try:
                # Assignment reads already computed this under the same lifecycle
                # lock. Writes/worker calls still validate from the catalog.
                status = q.get('location_check')
                if status is None and q["content"]["regions"]:
                    status = checks[q["question_id"]] = self.location_status(db, plan["id"], q, memo=shared)  # setup_summary reuses it
                self.require_location_review(db, plan["id"], q, status=status)
            except (ValueError, Conflict):
                continue
            configured += 1
            per_version[q["exam_version"]] = per_version.get(q["exam_version"], 0) + 1
            if paper_versions is None:
                paper_versions = self.paper_documents(db, plan, participants, shared)
            for p, paper_version in paper_versions:
                if (q["question_id"], p["section_id"], p["enrollment_id"]) in existing:
                    continue
                if paper_version != q["assignment_version_id"]:
                    continue
                key = (p["section_id"], p["enrollment_id"])
                try:
                    self.source(db, plan, q, p, event, shared)
                    new += 1
                    regrade += (q["question_id"], p["section_id"], p["enrollment_id"]) in withdrawn
                    if per_student is not None:
                        per_student["new"][key] = per_student["new"].get(key, 0) + 1
                except OtherExamVersion:
                    pass
                except VersionUndecided as error:
                    blocked += 1
                    if error.status in DECIDE:
                        undecided[key] = p.get("_exam_version") or {}
                    if per_student is not None:
                        per_student["blocked"].setdefault(key, []).append(str(getattr(error, "description", error)))
                except (ValueError, Conflict, Forbidden) as error:
                    blocked += 1
                    if per_student is not None:
                        per_student["blocked"].setdefault(key, []).append(str(getattr(error, "description", error)))
        # Grading work that waits for the instructor's version decision (2026-09-26). An undecided paper is refused by every version's
        # questions, so it counts once, as the questions of the version it most likely is (the blank exam it matched, the version
        # printed on it, its seat), else of the smallest version.
        deciding = sum(per_version.get(found.get("matched") or found.get("printed") or found.get("seat"))
                       or min(per_version.values()) for found in undecided.values())
        awaiting = sum(not s['inbox_ready'] for s in scans)
        # A stale grade cannot be approved: it waits to be graded again (counted in `new_responses`), not for review.
        review = sum(n for state, n in counts.items() if state not in ("approved", "manual", "auto_accepted", "pending", "stale"))
        setup_ready = bool(configured)
        if quality['state'] != 'confirmed':
            setup_ready = False
        finished = sum(counts.get(s, 0) for s in ("approved", "manual", "auto_accepted"))
        next_tab = "jobs" if new or pending or failed else "review" if review else "rubrics" if not setup_ready else "export" if finished else "jobs"
        if not questions or any(not q['rubric'] for q in questions):
            next_tab = 'rubrics'
        elif quality['state'] != 'confirmed' and not (pending or failed):
            next_tab = 'answer-key'  # The rubric finalization shares the answer key's step and gates every new batch.
        decisions = decision_rows(db, plan["id"], responses, automatic)
        if per_student is not None:
            per_student.update(undecided=dict(undecided), decisions=decisions, scans=scans)
        # The sixth stage is done only while a CURRENT certification exists (requirement, 2026-09-26); `export` stays for old readers.
        certification = certification_summary(db, plan, responses, {"new_responses": new, "decision_responses": deciding, "awaiting_review": awaiting,
                                                                    "pending": pending}, collection["students"], rows=decisions)
        return {"scans": scans, "awaiting_review": awaiting, "responses": counts, "pending": pending, "failed": failed, "new_responses": new, "regrade_responses": regrade,
                # A reset response already has its row; responses waiting for a version decision are unfinished grading work.
                "progress": response_progress(db, plan['id'], responses, counts, new - regrade + deciding),
                "blocked_responses": blocked, "version_decisions": len(undecided), "decision_responses": deciding, "review_responses": review, "collection": collection["counts"], "next_tab": next_tab,
                "configured_questions": configured, "setup": {**setup_summary(self, db, plan, questions, checks=checks, quality=quality, memo=shared), 'rubric_quality': quality},
                **({"export": export_summary(db, plan["id"], responses, decisions, catalog=str(self.path))} if export else {}),
                "certification": certification}
