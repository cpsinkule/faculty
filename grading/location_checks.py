"""Automatic bounded template reading, followed by explicit human confirmation."""
import json
import logging

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect

from . import config, locations
from .assignments import version
from .models import digest, encoded, text, uid
from .providers.base import ProviderError

LOG = logging.getLogger(__name__)
OCR_BATCH = 8   # blank pages read in one GLM-OCR model load (faculty_ocr.client.MAX_PAGES)
OCR_KEPT = 64   # readings kept for later turns; the oldest go first
READING_SETTINGS = ("local_verifier_model", "local_verifier_backend")  # the settings a template reading uses besides its timeout (see `_discover_location_check`)


class LocationChecksMixin:
    def location_context(self, db, aid, question, memo=None):
        """`memo`: a dict kept for ONE locked read or discovery pass over several questions (never across a write), holding what
        is the same for every question of a document."""
        plan = self.plan(db, aid)
        document = db.execute("SELECT page_count,compiler_json FROM assignment_versions WHERE id=?", (question["assignment_version_id"],)).fetchone()
        count = document["page_count"]
        stamps_only = json.loads(document["compiler_json"]).get("generator") == "faculty-stamps-only"
        all_templates = [dict(row) for row in db.execute("SELECT id,label,manifest_sha256,page_count FROM assignment_templates WHERE assignment_id=? ORDER BY id", (plan["source_assignment_id"],))]
        # A peel-off label's page count describes the labels, not the exam.
        # Ordinary printed exam versions still need a matching template size.
        templates = [t for t in all_templates if stamps_only or t["page_count"] == count]
        selected = question["content"].get("template_id")
        if not selected:
            selected = next((s['template_id'] for s in self.template_selections(db, aid)
                if s['document_id'] == question['assignment_version_id'] and s['exam_version'] == question['exam_version']), None)
        candidate = next((v for v in templates if v["id"] == selected), None) if selected else templates[0] if len(templates) == 1 else None
        filename = ""
        if question["rubric"] and question["rubric"]["source_artifact_id"]:
            row = db.execute("SELECT filename FROM inbox_artifacts WHERE id=?", (question["rubric"]["source_artifact_id"],)).fetchone()
            filename = row[0] if row else ""
        printed, basis = locations.hint(question, filename)
        issues = []
        if all_templates and not templates:
            issues.append(f"No uploaded template matches this printed document’s {count} pages. Choose the correct document version or upload a matching template.")
        if templates and not candidate:
            issues.append("Several templates fit this document. Choose the correct template before confirming its locations.")
        if not printed:
            issues.append("No printed question number is available. Set the number printed on the exam.")
        # The instructor's safety check for paper labeled on its first page: the scanned papers and the blank exam
        # must have the same number of pages. Say so when the template is chosen, not first when grading refuses.
        paper_sides = None
        if stamps_only and candidate:
            # Depends on the document alone, so a read over several questions asks once. CROSS JOIN starts from the document's
            # print batches rather than every packet page (performance audit, 2026-09-29); the joins and so the rows are the same.
            key = ("paper_sides", question["assignment_version_id"])
            if memo is not None and key in memo:
                usual = memo[key]
            else:
                usual = db.execute("""SELECT written,COUNT(*) n FROM (SELECT SUM(sp.status!='blank') written FROM print_batches b
                    CROSS JOIN printed_copies c ON c.print_batch_id=b.id CROSS JOIN scan_packets k ON k.printed_copy_id=c.id
                    JOIN scan_sequence_runs r ON r.id=k.run_id AND r.undone_at IS NULL
                    JOIN scan_packet_pages q ON q.packet_id=k.id JOIN scan_pages sp ON sp.id=q.scan_page_id
                    WHERE b.assignment_version_id=? GROUP BY k.id) GROUP BY written ORDER BY n DESC,written LIMIT 1""", (question["assignment_version_id"],)).fetchone()
                if memo is not None:
                    memo[key] = usual
            paper_sides = usual[0] if usual else None
            if paper_sides and paper_sides != candidate["page_count"]:
                issues.append(f"Your scanned papers have {paper_sides} written sides, but this blank exam has {candidate['page_count']} pages. Choose or upload the blank exam that matches the printed paper; papers cannot be graded against this one.")
        maximum = question["rubric"]["rules"]["ceiling"] if question["rubric"] else None
        if maximum is not None and maximum != question["content"]["max_points"]:
            issues.append("The rubric maximum differs from the configured question maximum.")
        # Areas read from Gradescope's outline (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE") are the check's suggestion; the key is
        # only present for them, so every other question's check input stays as it was.
        imported = question["content"]["regions"] if question["content"].get("regions_from") == "gradescope" else None
        return {**({"document_stamps_only": True} if stamps_only else {}), **({"paper_sides": paper_sides} if paper_sides else {}),
            **({"gradescope_regions": imported} if imported else {}),
            "reader": locations.READER, "areas": locations.AREAS, "notes": locations.NOTES, "question_revision_id": question["question_revision_id"],
            "rubric_id": question["rubric_id"], "rubric_maximum": maximum, "printed_number": printed,
            "number_source": basis, "template": candidate, "templates": templates if not candidate else [candidate],
            "has_templates": bool(all_templates), "document_page_count": count, "issues": issues}

    def carry_location(self, db, aid, qid, question_revision_id, rubric_id, reason, *, to_revision=None, to_rubric=None, now=None):
        """A confirmed answer area follows its question to a new question revision (`to_revision`) or rubric revision (`to_rubric`) by
        an append-only receipt naming the confirmation it carries and why. The CALLER vouches that nothing the confirmation covers
        changed (the template, pages, areas, printed number and the rubric maximum the printed points were compared with). Returns
        whether there was a confirmation to carry."""
        located = db.execute("""SELECT * FROM frq_location_reviews WHERE assignment_id=? AND question_id=? AND question_revision_id=? AND rubric_id IS ?
            ORDER BY rowid DESC LIMIT 1""", (aid, qid, question_revision_id, rubric_id)).fetchone()
        if not located:
            return False
        detail = {**json.loads(located["detail_json"]), "carried_forward_from": located["id"], "carried_forward_reason": reason}
        db.execute("INSERT INTO frq_location_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid(), located["check_id"], aid, qid, to_revision or located["question_revision_id"],
            to_rubric or located["rubric_id"], located["template_id"], located["manual_review"], encoded(detail), located["actor"], now or timestamp()))
        return True

    def location_status(self, db, aid, question, memo=None):
        context = self.location_context(db, aid, question, memo=memo)
        template = context['template']
        result = {"context": context, "input_sha256": digest(context), "id": None, "attempt": 0, "result": None,
                  'reading_progress': {'template_id': template['id'], 'completed': 0, 'total': template['page_count']} if template else None}
        if not question["rubric"]:
            return {**result, "status": "waiting_rubric", "label": "Upload this question’s rubric to verify its location and points."}
        if not context["has_templates"]:
            return {**result, "status": "waiting_template", "label": "Upload a blank template to check the printed question and point total."}
        confirmed = db.execute("SELECT id,template_id,manual_review,actor FROM frq_location_reviews WHERE assignment_id=? AND question_id=? AND question_revision_id=? AND rubric_id=? ORDER BY rowid DESC LIMIT 1",
            (aid, question["question_id"], question["question_revision_id"], question["rubric_id"])).fetchone()
        if confirmed and context["template"] and confirmed["template_id"] == context["template"]["id"]:
            # Explicit manual confirmation also completes this reference's inspection.
            result['reading_progress']['completed'] = template['page_count']
            # ANSWER AREAS CONFIRM THEMSELVES (2026-10-22): a confirmation the worker made says so (`auto_prepare`).
            label = ("Locations confirmed automatically" if confirmed["actor"] == "automatic" else
                     "Locations confirmed · points reviewed manually" if confirmed["manual_review"] else "Locations confirmed · printed points match")
            return {**result, "status": "confirmed", "label": label, "review_id": confirmed["id"]}
        row = db.execute("SELECT * FROM frq_location_checks WHERE assignment_id=? AND question_id=? AND input_sha256=?", (aid, question["question_id"], result["input_sha256"])).fetchone()
        if not row:
            if not self.template_authorized(db, aid, question, context['template']):
                return {**result, 'status': 'waiting_templates', 'label': 'Confirm the Assignment Templates step to start the answer-area checks.'}
            return {**result, "status": "pending", "label": "Template verification will run automatically on the worker."}
        check = self.decode_result(row)
        result.update(id=check["id"], attempt=check["attempt"], result=check["result"], error=check["error"])
        if template:
            done = db.execute('''SELECT COUNT(DISTINCT page) FROM frq_template_readings
                WHERE template_id=? AND reader_sha256=? AND attempt<=?
                AND COALESCE(json_extract(result_json,'$.error'),'')='' ''',
                (template['id'], digest(check['profile']), check['attempt'])).fetchone()[0]
            result['reading_progress']['completed'] = min(done, template['page_count'])
        if check["status"] == "complete":
            state = "needs_review" if check["result"]["needs_manual_review"] else "needs_confirmation"
            label = "Manual review required: " + check["result"]["issues"][0] if state == "needs_review" else "Printed points match. Confirm every suggested answer area."
        else:
            state = check["status"]
            label = check["error"] or "Reading the template and checking printed question totals…"
            template = check["input"]["template"]
            if not check["error"] and template:
                # Page receipts are shared, so every question of this template advances together.
                done = result['reading_progress']['completed']
                label = f"Reading the template and checking printed question totals: {done} of {template['page_count']} pages read…"
        return {**result, "status": state, "label": label}

    def require_location_review(self, db, aid, question, *, status=None, memo=None):
        if not question["content"]["regions"]:
            raise Conflict("Upload a blank template and confirm this imported question’s response locations before grading.")
        status = status if status is not None else self.location_status(db, aid, question, memo=memo)
        if status["status"] not in ("confirmed", "waiting_template"):
            raise Conflict("Confirm this question’s template locations and review any point-total failures before grading.")

    def _discover_location_check(self, db):
        # Bounded rotating discovery catches either upload order, including
        # templates uploaded through the shared manager rather than Grading.
        rows = db.execute("SELECT rowid,assignment_id,question_id FROM frq_assignment_questions WHERE rubric_id IS NOT NULL AND rowid>? ORDER BY rowid LIMIT 50", (getattr(self, "_location_cursor", 0),)).fetchall()
        self._location_cursor = rows[-1]["rowid"] if rows else 0
        if db.execute("SELECT COUNT(*) FROM frq_location_checks WHERE status IN ('pending','running')").fetchone()[0] >= 20:
            return False
        memo = {}  # this pass only: it returns right after its one write
        for row in rows:
            q = self.question(db, row["assignment_id"], row["question_id"])
            status = self.location_status(db, row["assignment_id"], q, memo=memo)
            if status["status"] != "pending" or status["id"]:
                continue
            settings = self.settings()["settings"]
            try:
                endpoint_sha = digest(config.endpoint("local_verifier").encode())
            except ValueError:
                endpoint_sha = None  # The queued check will fail visibly for review.
            # AN ALLOW-LIST (requirement, 2026-11-06): only what a template reading uses (`LocalVlmProvider`: the verifier's model and backend, and
            # the request timeout) makes the profile that keys the shared page receipts, so no other preference (endpoints, concurrency,
            # the assistant, coding or training switches, workspace switches) makes a second check on the same blank read it again.
            profile = {"settings": {**{key: settings[key] for key in READING_SETTINGS}, "timeout": min(settings["timeout"], 120)},
                "endpoint_sha256": endpoint_sha, "reader": locations.READER}
            now, check_id = timestamp(), uid()
            db.execute("INSERT INTO frq_location_checks VALUES(?,?,?,?,?,?,'pending',0,NULL,'',?,?)",
                (check_id, row["assignment_id"], row["question_id"], status["input_sha256"], encoded(status["context"]), encoded(profile), now, now))
            self.event(db, row["assignment_id"], "template_verification_queued", {"check_id": check_id, "question_id": row["question_id"]}, "automatic")
            return True
        return False

    @staticmethod
    def _reading(db, template_id, profile, page, attempt):
        # This attempt's own reading, else a page an earlier attempt read without error (requirement, 2026-09-25): a retry re-reads
        # only the pages that failed. It re-read the whole blank before, a minute a page, for the same deterministic answers.
        rows = db.execute("SELECT * FROM frq_template_readings WHERE template_id=? AND reader_sha256=? AND page=? AND attempt<=? ORDER BY attempt DESC",
                          (template_id, digest(profile), page, attempt))
        for row in rows:
            result = json.loads(row["result_json"])
            if not result.get("error") or row["attempt"] == attempt:
                return result
        return None

    def _template_ocr(self, template_id, page, image, settings, unread):
        """GLM-OCR of one blank page. A turn that finds no reading kept for its page reads the check's next unread pages with it in
        one model load (requirement, 2026-09-25: starting the model took about as long as reading a page) and keeps them, in this
        worker's memory only, for their own turns, so a turn still sends one page to the layout model. Each kept reading is used
        once, for exactly the page image it was read from; a restart just reads again. A failed batch reads this page alone."""
        kept = getattr(self, "_template_ocr_ahead", None)
        if kept is None:
            kept = self._template_ocr_ahead = {}
        key = (template_id, page, digest(image))
        if key in kept:
            return kept.pop(key)
        glm = self.provider("glm", settings)
        ahead = [p for p in unread if p != page][:OCR_BATCH - 1]
        if ahead and hasattr(glm, "recognize_many"):
            images = [image] + [self.templates.read(template_id, "image", p)[0] for p in ahead]
            try:
                reads = glm.recognize_many(images)
            except Exception:  # Only a way to save time: whatever stops it, this page is read alone as before.
                LOG.warning("Blank pages could not be read in one model load; reading page %s alone", page, exc_info=True)
                reads = None
            if reads:
                for p, other, read in zip(ahead, images[1:], reads[1:]):
                    kept[(template_id, p, digest(other))] = read
                while len(kept) > OCR_KEPT:
                    kept.pop(next(iter(kept)))
                return reads[0]
        return glm.recognize(image)

    def _read_location_page(self, context, profile, page, unread=()):
        endpoint = config.endpoint("local_verifier")
        if not endpoint:
            raise ProviderError("Local template verification is not configured. Configure the local verifier endpoint and model before retrying.")
        if profile["endpoint_sha256"] != digest(endpoint.encode()):
            raise ProviderError("The local reading endpoint changed. Retry this verification explicitly.")
        image, _ = self.templates.read(context["template"]["id"], "image", page)
        settings = profile["settings"]
        ocr = self._template_ocr(context["template"]["id"], page, image, settings, unread)
        if ocr["raw"].get("truncated"):
            raise ProviderError("The template OCR was truncated; inspect this page manually.", raw=ocr["raw"], metadata=ocr["metadata"])
        answer = self.provider("local_verifier", settings).locate({"ocr_text": ocr["transcript"]["plain_text"]}, [image])
        metadata = {"ocr": ocr, "layout": answer, "page_sha256": digest(image)}
        try:
            result = locations.page_result(answer["output"], ocr["transcript"]["plain_text"])
        except ValueError:
            raise ProviderError("The template reading returned invalid headings or geometry; inspect this page manually.", raw=metadata) from None
        return result, metadata

    def process_location_one(self):
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                if db.execute("UPDATE frq_location_checks SET status='failed',error='Template verification was interrupted. Review manually or retry explicitly.',updated_at=? WHERE status='running'", (timestamp(),)).rowcount:
                    return True
                self._location_discover_turn = not getattr(self, "_location_discover_turn", False)
                if self._location_discover_turn and self._discover_location_check(db):
                    return True
                # Discovery is a separate bounded turn; page reads rotate by
                # updated_at so long templates cannot monopolize the queue.
                row = db.execute("SELECT * FROM frq_location_checks WHERE status='pending' ORDER BY updated_at,rowid LIMIT 1").fetchone()
                if not row:
                    return self._discover_location_check(db)
                check = dict(row)
                context, profile = json.loads(check["input_json"]), json.loads(check["profile_json"])
                q = self.question(db, check["assignment_id"], check["question_id"])
                if digest(self.location_context(db, check["assignment_id"], q)) != check["input_sha256"]:
                    db.execute("UPDATE frq_location_checks SET status='stale',updated_at=? WHERE id=?", (timestamp(), check["id"]))
                    return True
                template = context["template"]
                pages = [(page, self._reading(db, template["id"], profile, page, check["attempt"])) for page in range(1, template["page_count"]+1)] if template else []
                unread = [page for page, result in pages if result is None]
                missing = unread[0] if unread else None
                failed = next((result["error"] for _, result in pages if result and result.get("error")), None)
                if missing is None or failed or not context["printed_number"]:
                    result = locations.combine(context, [(p, r) for p, r in pages if r])
                    db.execute("UPDATE frq_location_checks SET status=?,result_json=?,error=?,updated_at=? WHERE id=?", ("failed" if failed else "complete", encoded(result), failed or "", timestamp(), check["id"]))
                    self.event(db, check["assignment_id"], "template_verification_read", {"check_id": check["id"], "manual_review": result["needs_manual_review"]}, "worker")
                    return True
                db.execute("UPDATE frq_location_checks SET status='running',updated_at=? WHERE id=?", (timestamp(), check["id"]))
            try:
                result, metadata = self._read_location_page(context, profile, missing, unread)
            except Exception as error:
                message = str(error) if isinstance(error, ProviderError) else "The template page could not be read. Review it manually or retry explicitly."
                result = {"error": message}
                metadata = {"raw": error.raw, "metadata": error.metadata} if isinstance(error, ProviderError) else {}
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("INSERT INTO frq_template_readings VALUES(?,?,?,?,?,?,?,?)", (uid(), template["id"], digest(profile), missing, check["attempt"], encoded(result), encoded(metadata), timestamp()))
                q = self.question(db, check["assignment_id"], check["question_id"])
                stale = digest(self.location_context(db, check["assignment_id"], q)) != check["input_sha256"]
                db.execute("UPDATE frq_location_checks SET status=?,updated_at=? WHERE id=?", ("stale" if stale else "pending", timestamp(), check["id"]))
            return True

    def local_work(self, aid):
        """What the local worker is doing for this assignment, as counts (requirement, 2026-09-25): papers compared with the blank exams to confirm their versions, and the
        blank pages read for the answer areas. A view: it queues, decides and changes nothing, and names no student."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            versions = self.version_check_progress(db, aid)
            reading = {}
            for row in db.execute("SELECT input_json,profile_json,attempt FROM frq_location_checks WHERE assignment_id=? AND status IN ('pending','running')", (aid,)):
                template = json.loads(row["input_json"])["template"]
                if not template:
                    continue
                done = db.execute("""SELECT COUNT(DISTINCT page) FROM frq_template_readings WHERE template_id=? AND reader_sha256=? AND attempt<=?
                    AND COALESCE(json_extract(result_json,'$.error'),'')=''""", (template["id"], digest(json.loads(row["profile_json"])), row["attempt"])).fetchone()[0]
                entry = reading.setdefault(template["id"], {"label": template.get("label") or "the blank exam", "pages": template["page_count"], "read": 0, "questions": 0})
                entry["read"], entry["questions"] = max(entry["read"], min(done, template["page_count"])), entry["questions"] + 1
        templates = sorted(reading.values(), key=lambda entry: entry["label"])
        return {"versions": versions, "templates": templates, "active": bool(versions["left"] or templates)}

    def retry_location(self, aid, qid, data):
        # No worker execution lock (2026-09-25): the worker holds it through every page read, so a retry was refused while
        # any work ran, as "being backed up or moved". A retry only reopens a settled check; the lifecycle lock orders it.
        with self.rosters.lock(), connect(self.path) as db:
            q = self.question(db, aid, qid)
            status = self.location_status(db, aid, q)
            if status["id"] != data.get("check_id") or status["input_sha256"] != data.get("input_sha256"):
                raise Conflict("Only the current failed verification can be retried, at most twice.")
            self._retry_location(db, aid, status)
        self.inbox.wake.set()
        return self.assignment(aid)

    def retry_all_locations(self, aid, data):
        """One Retry for every answer area not ready yet (requirement, 2026-09-25): each listed question with neither a confirmed
        nor a suggested area, whose check failed with a retry left, is retried exactly as its own Retry would be,
        in one transaction. A stale check, or one that has an area by now, refuses the whole request; those are retried alone."""
        items = data.get("questions")
        if not isinstance(items, list) or not items or len(items) > 500 or not all(isinstance(item, dict) for item in items):
            raise ValueError("List the questions whose checks you retry.")
        if any(not isinstance(item.get("question_id"), str) for item in items):
            raise ValueError("List valid question IDs.")
        if len({item["question_id"] for item in items}) != len(items):
            raise ValueError("A question is listed twice.")
        with self.rosters.lock(), connect(self.path) as db:
            for item in items:
                q = self.question(db, aid, item.get("question_id"))
                status = self.location_status(db, aid, q)
                if status["id"] != item.get("check_id") or status["input_sha256"] != item.get("input_sha256"):
                    raise Conflict("A check changed since you looked at it. Refresh before retrying them all.")
                if q["content"]["regions"] or (status["result"] or {}).get("regions"):
                    raise Conflict("Only questions without an answer area are retried together. Retry the others one at a time.")
                self._retry_location(db, aid, status, together=True)
        self.inbox.wake.set()
        return self.assignment(aid)

    def _retry_location(self, db, aid, status, together=False):
        # Failed checks only (2026-09-25): a retry re-reads just the pages that failed, and a check that needs review has none, so
        # it would come back the same. Such a check is reviewed with Review answer area instead.
        if status["status"] != "failed" or status["attempt"] >= 2:
            raise Conflict("Only a failed verification can be retried, at most twice. A check that needs review reads the same pages; review it instead.")
        detail = {"check_id": status["id"], **({"retried_together": True} if together else {})}
        profile = json.loads(db.execute("SELECT profile_json FROM frq_location_checks WHERE id=?", (status["id"],)).fetchone()[0])
        try:
            current = digest(config.endpoint("local_verifier").encode())
        except ValueError:
            current = None
        if profile.get("endpoint_sha256") != current:
            # The local model was connected or moved since this check was queued, which is what its failure asks to be retried for
            # (2026-09-25; migration 023). Only the address is recorded anew, on this explicit retry; the event keeps the old one.
            detail.update(previous_endpoint_sha256=profile.get("endpoint_sha256"), endpoint_sha256=current)
            db.execute("UPDATE frq_location_checks SET status='pending',attempt=attempt+1,result_json=NULL,error='',profile_json=?,updated_at=? WHERE id=?",
                       (encoded({**profile, "endpoint_sha256": current}), timestamp(), status["id"]))
        else:
            db.execute("UPDATE frq_location_checks SET status='pending',attempt=attempt+1,result_json=NULL,error='',updated_at=? WHERE id=?", (timestamp(), status["id"]))
        self.event(db, aid, "template_verification_retry", detail)

    def select_location_template(self, aid, qid, data):
        """Explicitly choose a reference before OCR, without approving its areas."""
        with self.rosters.lock(), connect(self.path) as db:
            q = self.question(db, aid, qid)
            version(data, q["version"])
            plan = self.plan(db, aid)
            template = db.execute("SELECT * FROM assignment_templates WHERE id=? AND assignment_id=?",
                (data.get("template_id"), plan["source_assignment_id"])).fetchone()
            document = db.execute("SELECT page_count,compiler_json FROM assignment_versions WHERE id=?", (q["assignment_version_id"],)).fetchone()
            if not template or (json.loads(document["compiler_json"]).get("generator") != "faculty-stamps-only" and template["page_count"] != document["page_count"]):
                raise ValueError("Choose a compatible blank exam template of this assignment.")
            if data.get("apply_to_all") not in (None, True, False):
                raise ValueError("Choose whether this template applies to the other questions.")
            if template["id"] != q["content"].get("template_id") and any(area.get("privacy_masks") for area in q["content"]["regions"]) and data.get("review_masks") is not True:
                raise ValueError("Review the existing privacy masks before changing the template and clearing its answer areas.")
            chosen = [q]
            if data.get("apply_to_all") is True:
                # One decision for the printed version. Questions that already
                # have response areas keep their own template and confirmation.
                others = [self.question(db, aid, row[0]) for row in db.execute(
                    "SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? AND question_id!=? ORDER BY rowid", (aid, qid))]
                # Each exam version has its own blank exam, so the choice never reaches another version's questions.
                chosen += [o for o in others if o["assignment_version_id"] == q["assignment_version_id"] and o["exam_version"] == q["exam_version"] and not o["content"]["regions"]]
            changed = False
            for item in chosen:
                if template["id"] == item["content"].get("template_id"):
                    continue
                self._set_location_template(db, aid, item, template['id'], applied_from=qid if item is not q else None)
                changed = True
            if changed:
                self.touch(db, aid)
        self.inbox.wake.set()
        return self.assignment(aid)

    def _set_location_template(self, db, aid, item, template_id, *, keep_regions=False, applied_from=None):
        clean = {**item['content'], 'template_id': template_id, 'regions': item['content']['regions'] if keep_regions else []}
        if not keep_regions:
            clean.pop('regions_from', None)
        rid = self.new_question_revision(db, item['question_id'], clean)
        self.pin_question_revision(db, aid, item['question_id'], rid)
        self.event(db, aid, 'template_selected', {'question_id': item['question_id'], 'template_id': template_id, 'question_revision_id': rid,
            **({'applied_from_question_id': applied_from} if applied_from else {})})

    def confirm_location(self, aid, qid, data):
        with self.rosters.lock(), connect(self.path) as db:
            q = self.question(db, aid, qid)
            version(data, q["version"])
            status = self.location_status(db, aid, q)
            if status["id"] != data.get("check_id") or status["input_sha256"] != data.get("input_sha256") or status["status"] not in ("failed", "needs_review", "needs_confirmation"):
                raise Conflict("The verification or question changed. Refresh before confirming its locations.")
            self._confirm_location(db, aid, q, status, data)
        # ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): where they accepted this question's drafted rubric, its answer key is
        # now read from the blank assignment without being asked, with the picture of the area just confirmed, and the rubric check
        # of such a plan is asked for once nothing else waits (`rubric_generation.after_answer_areas`). It never fails this.
        self.after_answer_areas(aid, [qid])
        return self.assignment(aid)

    def confirm_all_locations(self, aid, data):
        """One click for every suggested answer area the instructor looked through (requirement, 2026-09-25): each listed
        question whose printed points MATCH is confirmed exactly as its preview showed it (the check's template, printed
        number and suggested areas), in one transaction. A check that needs manual review, a stale check or question, and
        areas that would change privacy masks refuse the whole request: those are confirmed one at a time in the review."""
        if data.get("confirm_locations") is not True:
            raise ValueError("Explicitly confirm every answer area, even when its points match.")
        items = data.get("questions")
        if not isinstance(items, list) or not items or len(items) > 500 or not all(isinstance(item, dict) for item in items):
            raise ValueError("List the questions whose answer areas you confirm.")
        if any(not isinstance(item.get("question_id"), str) for item in items):
            raise ValueError("List valid question IDs.")
        if len({item["question_id"] for item in items}) != len(items):
            raise ValueError("A question is listed twice.")
        with self.rosters.lock(), connect(self.path) as db:
            for item in items:
                q = self.question(db, aid, item.get("question_id"))
                status = self.location_status(db, aid, q)
                if q["version"] != item.get("version") or status["id"] != item.get("check_id") or status["input_sha256"] != item.get("input_sha256"):
                    raise Conflict("An answer area changed since you looked at it. Refresh before confirming them all.")
                if status["status"] != "needs_confirmation":
                    raise Conflict("Only answer areas whose printed points match are confirmed together. Review the others one at a time.")
                context, result = status["context"], status["result"] or {}
                if not context["template"]:
                    raise Conflict("Choose this question's Assignment Template before confirming its answer area.")
                regions = result.get("regions") or q["content"]["regions"]
                if regions != q["content"]["regions"] and any(r.get("privacy_masks") for r in q["content"]["regions"]):
                    raise Conflict("An answer area with privacy masks changes: review it on its own.")
                self._confirm_location(db, aid, q, status, {"confirm_locations": True, "template_id": context["template"]["id"],
                    "printed_number": context["printed_number"], "regions": regions, "notes": "", "confirmed_together": True})
        self.after_answer_areas(aid, [item["question_id"] for item in items])  # as after one confirmed answer area (`confirm_location`)
        return self.assignment(aid)

    def _confirm_location(self, db, aid, q, status, data, *, actor="workspace"):
        qid = q["question_id"]
        if data.get("confirm_locations") is not True:
            raise ValueError("Explicitly confirm every answer area, even when its points match.")
        template = next((t for t in status["context"]["templates"] if t["id"] == data.get("template_id")), None)
        if not template:
            raise ValueError("Choose a compatible blank exam template of this assignment.")
        printed = locations.printed_number(data.get("printed_number", ""))
        if not printed:
            raise ValueError("Enter the question number printed on the template.")
        manual = status["status"] != "needs_confirmation" or printed != status["context"]["printed_number"]
        note = text(data.get("notes", ""), "Manual review note", 4000, not manual)
        if manual and data.get("manual_review") is not True:
            raise ValueError("Inspect and acknowledge every failed point or location check before confirming.")
        # Reuse the existing multi-page geometry validation. No OCR reading
        # ever mutates a saved question, rubric, or shared template mapping.
        regions = locations.validate_regions(data.get("regions"), template["page_count"])
        if regions != q["content"]["regions"] and any(r.get("privacy_masks") for r in q["content"]["regions"]) and data.get("review_masks") is not True:
            raise ValueError("Changing answer areas requires reviewing the existing privacy masks.")
        clean = {**q["content"], "regions": regions, "template_id": template["id"], "printed_number": printed}
        if regions != q["content"]["regions"] or template["id"] != q["content"].get("template_id"):
            clean.pop("regions_from", None)
        if not clean.get("problem") and status["id"]:
            # Imported questions have no statement. The blank exam's own reading supplies it as grader context;
            # it stays editable with the answer key and never affects points, areas or approvals.
            check = self.decode_result(db.execute("SELECT * FROM frq_location_checks WHERE id=?", (status["id"],)).fetchone())
            for area in regions:
                reading = db.execute("SELECT provider_json FROM frq_template_readings WHERE template_id=? AND reader_sha256=? AND page=? ORDER BY attempt DESC LIMIT 1",
                                     (template["id"], digest(check["profile"]), area["page"])).fetchone()
                printed_text = json.loads(reading[0] or "{}").get("ocr", {}).get("transcript", {}).get("plain_text", "") if reading else ""
                clean["problem"] = locations.problem_text(printed_text, printed)
                if clean["problem"]:
                    break
        review_id, now = uid(), timestamp()
        rid = self.new_question_revision(db, qid, clean, now)
        self.pin_question_revision(db, aid, qid, rid)
        together = {"confirmed_together": True} if data.get("confirmed_together") else {}
        detail = {"regions": clean["regions"], "printed_number": printed, "notes": note, "result": status["result"], "error": status.get("error", ""), "attempt": status["attempt"], **together}
        db.execute("INSERT INTO frq_location_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)", (review_id, status["id"], aid, qid, rid, q["rubric_id"], template["id"], int(manual), encoded(detail), actor, now))
        self.touch(db, aid)
        self.event(db, aid, "template_locations_confirmed", {"review_id": review_id, "check_id": status["id"], "question_id": qid, "manual_review": manual, **together}, actor)
