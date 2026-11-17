"""Durable per-response stages in the existing fair Inbox worker rotation."""
import json
import logging
import os
import threading
import time

from werkzeug.exceptions import Conflict, Forbidden, NotFound
from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT

from . import answers, choice, committee, config, consensus, direct_read, dr_copies, extraction, page_recovery, recognition, rubric_quality, standard, storage
from .cloud import RetryLater, later
from .exam_versions import OtherExamVersion
from .models import digest, encoded, number, text, transcript, uid
from .providers.base import Cancelled, ProviderError
from .review_rules import VERSION as REVIEW_RULES, current_condition_reasons
from .scoring import score

# Answer areas are cut in the document sandbox, CPU work beside model calls that mostly wait. A whole batch at once
# (`cloud.MAX_UNITS`) would otherwise start one sandbox per response together; this keeps them to the machine's cores.
CUTTING = threading.BoundedSemaphore(max(2, (os.cpu_count() or 4) - 2))
# The catalog work around each component (its checks, its record) is CPU work in this process, bounded like the cutting. Unbounded,
# a 2,600-response batch under Full concurrency ran it all at once (2026-09-24): 2,400 threads contended for the interpreter lock,
# the one holding the lifecycle lock to record a result waited behind them, and 2 responses finished cutting in five minutes.
# Model requests are not bounded by it: a component waits for its model outside.
CHECKING = threading.BoundedSemaphore(max(4, os.cpu_count() or 4))

LOG = logging.getLogger(__name__)
STAGES = ("extracting", "recognizing", "reconciling", "grading", "scoring", "complete")
# Tokens one response is assumed to use per role (input, output), for the estimate shown BEFORE a batch is submitted.
# A reasoning model is assumed to write three times the output. Receipts record what was actually used.
TOKENS = {"reader": (3500, 900), "primary": (5000, 1300), "second": (3500, 1200), "verifier": (4500, 1200), "flag_reviewer": (14000, 1200)}
# Cloud Standard's direct reading adds one free-text request per answer area BEFORE the reader's reconcile call above: one crop
# image and a short instruction in, that area's handwriting as plain text out.
READ = (1500, 500)
# The same assumption by how much thinking the role asked for; thinking is billed as output.
THINKING = {"off": 1, "minimal": 1.5, "low": 2, "on": 3, "medium": 3, "high": 5, "xhigh": 7, "max": 8}


def thinking_factor(endpoint):
    choice = endpoint.get("reasoning_choice")
    return THINKING[choice] if choice in THINKING else 3 if endpoint["reasoning"] else 1


def unpriced(endpoint):
    """A connection whose calls have no knowable cost: no configured price, and a host that bills (anything but loopback, which is
    fully local). Like the built-in Nebius mode, a batch that uses one needs the instructor's conservative cost per response:
    its calls record no cost, so without that figure the batch would run as if every call were free."""
    from urllib.parse import urlsplit
    from .providers.cloud import LOOPBACK
    if endpoint.get("kind") in config.SUBSCRIPTION_KINDS:
        return False  # A Claude, ChatGPT or Gemini subscription: its calls cost nothing beyond the plan, which is known, not unknown.
    return not endpoint.get("price_in") and not endpoint.get("price_out") and urlsplit(endpoint.get("base_url") or "").hostname not in LOOPBACK


class JobsMixin:
    @staticmethod
    def needs_cloud(job):
        return (job["mode"] == "nebius" and job["operation"] != "rescore") or (job["mode"] == "hybrid" and job["operation"] == "recognize")

    def cloud_preview(self, jid):
        """An optional look at the exact crops a cloud batch sends. Nothing waits for it (instructor decision of 2026-09-19)."""
        with self.rosters.lock(), connect(self.path) as db:
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone()
            if not job or not self.needs_cloud(job):
                raise Conflict("This job does not send response crops to a cloud service.")
            manifest = []
            ready = True
            for row in db.execute("SELECT * FROM frq_tasks WHERE job_id=? ORDER BY id", (jid,)):
                task = dict(row)
                self.validate_task(db, task)
                try:
                    crops = self.step(db, task, "extracting")["crops"]
                except ValueError:
                    ready = False
                    continue
                manifest.append({"response_id": task["response_id"], "source_sha256": task["source_sha256"],
                    "images": [c["images"]["cloud"] for c in crops]})
            return {"ready": ready, "manifest": manifest, "sha256": digest(manifest)}

    def estimate(self, settings, mode, responses, crops=1):
        """What a cloud batch is expected to cost, shown before it is submitted. The instructor's own figure wins where one is set.
        `crops`: answer areas per response on average, which only direct reading's one request per area depends on."""
        if mode not in config.TIERS:
            return {"responses": responses, "estimated_cost": 0, "basis": "No cloud inference in this mode.", "per_response": 0, "roles": {}}
        roles = {role: e for role, e in config.roles(settings, mode).items() if e}
        per_role = {}
        # The arbitrator is the team's additional reviewer: it is not bought for an ordinary tie-break, and when it is called it
        # reads every assessment and flag beside the original inputs, so its input is the largest.
        from . import arbitration
        arbitrating = standard.enabled(settings) or not committee.enabled(settings) or arbitration.enabled(settings)
        for role, e in roles.items():
            input_tokens = (14000 if arbitrating else 8000) if role == "verifier" else TOKENS["primary"][0] if role == "second" and e["modality"] == "vision" else TOKENS[role][0]
            per_role[role] = {"endpoint": e["name"], "model": e["model"], "lineage": e["lineage"],
                "reasoning": e.get("reasoning_choice"),
                "cost": (input_tokens * e["price_in"] + TOKENS[role][1] * thinking_factor(e) * e["price_out"]) / 1e6}
        if direct_read.enabled(settings, mode) and roles.get("reader"):
            # The reader reads every answer area once in free text before its reconcile call: one more request per crop.
            e = roles["reader"]
            per_role["direct_reading"] = {"endpoint": e["name"], "model": e["model"], "lineage": e["lineage"], "reasoning": e.get("reasoning_choice"), "requests": crops,
                "cost": crops * (READ[0] * e["price_in"] + READ[1] * thinking_factor(e) * e["price_out"]) / 1e6}
        priced = sum(v["cost"] for v in per_role.values())
        rate = settings["cost_per_response"] or priced
        # A connection without configured prices (a manual one left at its defaults): without the instructor's own figure the
        # batch's cost is unknown, not zero.
        missing = sorted({e["name"] for e in roles.values() if unpriced(e)})
        known = bool(settings["cost_per_response"]) or not missing
        return {"responses": responses, "estimated_cost": round(responses * rate, 4) if known else None, "per_response": round(rate, 6) if known else None, "roles": per_role,
            "basis": "Your configured conservative cost per response." if settings["cost_per_response"] else
                f"No prices are configured for {', '.join(missing)}, so this batch's cost is unknown. Set a conservative cost per response in Compute settings, or its prices." if not known else "Assumed tokens and configured prices, including the disagreement arbitrator and flag reviewer. Conditional calls are skipped when unnecessary. Mathpix is billed separately.",
            "model": (roles.get("primary") or {}).get("model"), "concurrency": settings["concurrency"]}

    @staticmethod
    def crops_per_response(members):
        """Answer areas per response on average, from each member's source (a preview member or a task's `source_json`)."""
        regions = [len((m["source"] if "source" in m else json.loads(m["source_json"]))["regions"]) for m in members]
        return sum(regions) / len(regions) if regions else 1

    def source(self, db, plan, question, participant, event, shared=None):
        # A multi-question read holds the lifecycle lock throughout. Validate a
        # paper once within that read, then validate each question's own regions.
        # No result survives the request or bypasses save/worker revalidation.
        shared = shared if shared is not None else {}
        sources = shared.setdefault("paper_sources", {})
        # A pass over every paper (the caller's `prefetch` opt-in: the Grading index, an assignment read, the review queue, the batch
        # preview) reads every assembly of the exam and their pages' Inbox state once (PERFORMANCE, 2026-10-02); each paper's checks
        # are the same, and a paper or page not found that way is read alone as before.
        whole = shared.get("prefetch") is not None
        key = (event["id"], participant["section_id"], participant["enrollment_id"],
               participant["version"], participant.get("collection", {}).get("assembly_revision_id"), question["assignment_version_id"])
        if key not in sources:
            # DR COPIES: the DR center's copy of this question's version is that version's paper (`source["dr_copy"]` then names the copy).
            rubric = {"assignment_version_id": question["assignment_version_id"], "dr_copies": True}
            sources[key] = self.manual._source(db, event, participant, rubric, memo=shared.setdefault("assemblies", {})) if whole \
                else self.manual._source(db, event, participant, rubric)
        source = sources[key]
        # A versioned question reads only papers of its own exam version: seat first, confirmed against the blank exams.
        exam_version = self.require_exam_version(db, plan, question, participant, shared)
        pages = {page["page_number"]: page for page in source["pages"]}
        copy = source.get("dr_copy")
        if copy:
            # DR COPIES: one problem to a sheet, so this question's answer area is its own sheet of the copy, whatever areas the
            # version's printed pages have; the blank is the one made from the copy's Word file.
            areas, template_id = self.dr_copy_areas(db, plan, question, copy, shared), copy["template_id"]
            if not self._pages_complete(db, pages, copy["pages"]):
                raise Conflict(f"Grading needs every page of the DR copy. This paper’s reviewed scan has {len(pages)} page{'' if len(pages) == 1 else 's'}, but the DR copy has {copy['pages']}. Review the scan in the Inbox so its pages are identified.")
        else:
            areas, template_id = question["content"]["regions"], question["content"]["template_id"]
        if not copy and question["content"]["template_id"]:
            # The same blank/document pair was being read for every response.
            # These caches live only in this locked read, including missing pages.
            templates = shared.setdefault('source_templates', {})
            template_key = (question['assignment_version_id'], question['content']['template_id'])
            if template_key not in templates:
                template = db.execute("SELECT page_count FROM assignment_templates WHERE id=?", (template_key[1],)).fetchone()
                document = db.execute("SELECT compiler_json FROM assignment_versions WHERE id=?", (template_key[0],)).fetchone()
                templates[template_key] = (template[0], json.loads(document[0]).get('generator') == 'faculty-stamps-only')
            page_count, stamps_only = templates[template_key]
            if stamps_only and not self._pages_complete(db, pages, page_count):
                raise Conflict(f"Grading needs every reviewed exam page. This paper’s reviewed scan has {len(pages)} page{'' if len(pages) == 1 else 's'}, but the chosen blank exam has {page_count}. Review the scan in the Inbox so its pages are identified, or choose the blank exam that matches the printed paper.")
        regions = []
        retained_pages = shared.setdefault('source_pages', {})
        unread = [page['scan_page_id'] for page in source["pages"] if page['scan_page_id'] not in retained_pages]
        if unread and whole and event["id"] not in shared.setdefault('source_pages_exams', set()):
            # Every assembled page of the exam in one read, each with whether its scan is PRESENT.
            shared['source_pages_exams'].add(event["id"])
            for row in db.execute(f"""SELECT sp.id,sp.message_id,sp.raster_artifact_id,{PRESENT.format('sp.message_id')}
                    FROM exam_event_batches eb JOIN printed_copies c ON c.print_batch_id=eb.batch_id JOIN assembly_revisions ar ON ar.printed_copy_id=c.id
                    JOIN assembly_pages ap ON ap.revision_id=ar.id JOIN scan_pages sp ON sp.id=ap.scan_page_id WHERE eb.event_id=?""", (event["id"],)):
                retained_pages.setdefault(row[0], {"message_id": row[1], "raster_artifact_id": row[2]} if row[3] else None)
            unread = [page_id for page_id in unread if page_id not in retained_pages]
        if unread:  # every page of this paper in one read: a page of a deleted scan is kept as None, as its own read gave
            found = {row[0]: row for row in db.execute(f"""SELECT sp.id,sp.message_id,sp.raster_artifact_id FROM scan_pages sp
                WHERE sp.id IN ({','.join('?' * len(unread))}) AND {PRESENT.format('sp.message_id')}""", unread)}
            for page_id in unread:
                row = found.get(page_id)
                retained_pages[page_id] = {"message_id": row[1], "raster_artifact_id": row[2]} if row else None
        for region in areas:
            page = pages.get(region["page"])
            if not page:
                raise Conflict("A question response page is missing from this reviewed paper.")
            retained = retained_pages[page['scan_page_id']]
            if not retained:
                raise Conflict("A source scan was removed from the Inbox.")
            regions.append({**region, **page, **dict(retained)})
        return {"assembly_revision_id": source["id"], "assembly_sha256": source["selection_sha256"],
            "assignment_version_id": question["assignment_version_id"], "regions": regions,
            "participant_version": participant["version"], "template_id": template_id,
            **({"exam_version": exam_version} if exam_version else {}),
            **({"dr_copy": {"assignment_version_id": source["assignment_version_id"], "rule": dr_copies.RULE}} if copy else {})}

    EMPTY_INK = .005  # dark share of an empty back with an edge line or staple shadow: measured .0000-.0009; written sides .024 and more
    _empty_sides = {}  # scan page id -> empty (rasters are immutable); bounded below

    def _pages_complete(self, db, pages, page_count):
        """Every page of the blank exam, and past its last page only empty sides (EMPTY BACKS)."""
        expected = set(range(1, page_count + 1))
        return set(pages) == expected or (expected < set(pages) and all(
            self._empty_side(db, pages[number].get("scan_page_id")) for number in set(pages) - expected))

    def _empty_side(self, db, scan_page_id):
        """EMPTY BACKS (2026-10-17: 38 one-page take-homes held at grading as "2 pages"): a side past the blank exam's last page that
        the scanner did not call blank, but that carries no writing (less than EMPTY_INK of it dark, 4% margins left out), is no page
        of the paper, so the paper is graded from the pages the blank has."""
        if not scan_page_id:
            return False
        if scan_page_id not in self._empty_sides:
            import io
            import numpy as np
            from PIL import Image
            row = db.execute("SELECT raster_artifact_id FROM scan_pages WHERE id=?", (scan_page_id,)).fetchone()
            if not row:
                return False
            image = Image.open(io.BytesIO(self.inbox.read_artifact(row[0])[1])).convert("L")
            width, height = image.size
            inside = np.asarray(image.crop((int(width * .04), int(height * .04), int(width * .96), int(height * .96))))
            if len(self._empty_sides) >= 4096:
                self._empty_sides.clear()
            self._empty_sides[scan_page_id] = bool(np.mean(inside < 140) < self.EMPTY_INK)
        return self._empty_sides[scan_page_id]

    def _batch_settings(self):
        """The workspace settings a batch is fingerprinted and frozen with: preview() fingerprints them and create_job() freezes them
        into the job's profile, so both read the same set, without the preferences that are no part of grading."""
        settings = self.settings()["settings"]
        settings.pop("recent_models", None)  # UI history does not invalidate a grading preview, and stays out of frozen grading/benchmark profiles.
        settings.pop('rubric_reviewer', None)  # Rubric preparation is separate from response grading.
        settings.pop('rubric_reviewer_reasoning', None)
        settings.pop('assistant_model', None)  # The Faculty assistant's model is no part of grading.
        settings.pop('assistant_reasoning', None)
        settings.pop('coding_model', None)  # Nor is the coding agent's (assistant/coding.py).
        settings.pop('coding_reasoning', None)
        settings.pop("auto_grade", None)  # Who starts a batch is no part of it.
        settings.pop("auto_flag_review", None)  # Nor who asks for its flag review.
        settings.pop("auto_certify", None)  # Nor who certifies late papers.
        settings.pop("auto_gradescope", None)  # Nor who sends to Gradescope and publishes there.
        settings.pop("draft_rubric_check", None)  # Nor when a drafted quiz's rubrics are checked.
        return settings

    def preview(self, aid, data):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        if data.get("mode") is not None and not isinstance(data["mode"], str):
            raise ValueError("Choose a supported compute mode.")
        qids = data.get("question_ids")
        if (not isinstance(qids, list) or not qids or len(qids) > 100 or any(not isinstance(qid, str) for qid in qids)
                or len(qids) != len(set(qids))):
            raise ValueError("Choose distinct questions to grade.")
        if config.removed(data.get("mode")):
            raise ValueError(f"{config.REMOVED_SCHEMES[data['mode']]} was removed on 2026-09-23. Choose Cloud or a local mode for a new batch.")
        settings = self._batch_settings()
        selection = data.get("selection", "all")
        if selection not in ("new", "all"):
            raise ValueError("Choose new responses or all eligible responses.")
        operation = data.get("operation", "recognize")
        if operation not in ("recognize", "grade", "rescore"):
            raise ValueError("Choose reading and grading, grading again, or recalculating points.")
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            quality = self.rubric_quality_status(db, aid)
            event, participants = self.manual._context(db, plan["event_id"])
            questions = [self.question(db, aid, qid) for qid in qids]
            # The rubric finalization gates every batch, question by question (requirement, 2026-09-28: when one question's rubric
            # changes, the others keep grading): a question whose finalization does not stand is left out and named, and a batch
            # with no question left is refused as before.
            finalization, held = self.batch_finalization(quality, questions)
            if not finalization:
                raise Conflict(f"Finish the rubric finalization first: {len(quality['unresolved'])} question{'s' if len(quality['unresolved']) > 1 else ''} still allow more points than they are worth, so some of their lines must be marked as alternatives."
                               if quality.get('unresolved') else 'Finish the rubric finalization first: run the rubric check, then review and confirm its clarifications and exclusions.')
            questions = [q for q in questions if q["question_id"] not in {item["question_id"] for item in held}]
            selected_responses = data.get("response_ids")
            if selected_responses is not None and (not isinstance(selected_responses, list) or not selected_responses):
                raise ValueError("Choose response IDs or omit the response filter.")
            eligible, skipped, shared, existing_count = [], [], {}, 0
            again = self.reset_ungraded(db, aid)  # A reset left these without a proposal: they are new work, not started work.
            if selection == "new":
                # Graded under an earlier rubric, question or paper: graded again. Listed on this read with its memo; the participants
                # are copies, so the versions the listing works out are never the ones queue_version_checks reads below.
                again |= self.outdated(db, aid, self._responses(db, plan, event, [dict(p) for p in participants], shared=shared, quality=quality))
            # The plan's responses in one read (one per question and student: UNIQUE), and each student's printed document once.
            stored = {(r["question_id"], r["section_id"], r["enrollment_id"]): r for r in db.execute("SELECT * FROM frq_responses WHERE assignment_id=?", (aid,))}
            documents = {}
            for q in questions:
                if not q["rubric"] or not q["rubric"]["approved_at"]:
                    raise Conflict("Approve each selected question's rubric before grading.")
                self.require_location_review(db, aid, q, memo=shared)
                for p in participants:
                    member = (p["section_id"], p["enrollment_id"])
                    response = stored.get((q["question_id"], *member))
                    if selected_responses is not None and (not response or response["id"] not in selected_responses):
                        continue
                    if selection == "new" and response and response["id"] not in again:
                        existing_count += 1
                        continue
                    if member not in documents:
                        documents[member] = self.manual._paper_version(db, plan["event_id"], p)
                    if documents[member] != q["assignment_version_id"]:
                        continue
                    try:
                        source = self.source(db, plan, q, p, event, shared)
                    except OtherExamVersion:
                        continue  # Another exam version's paper; that version's own question grades it.
                    except (ValueError, Conflict, Forbidden) as error:
                        skipped.append({"question_id": q["question_id"], "section_id": p["section_id"], "enrollment_id": p["enrollment_id"],
                            "reason": getattr(error, "description", str(error))})
                        continue
                    if operation != "recognize":
                        # Grading again and recalculating reuse a retained reading. A response without one (it failed, or was never
                        # read) is named here instead of refusing the whole batch: seen live, one failed paper among 322 did that.
                        prior = db.execute("""SELECT t.source_sha256 FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id
                            JOIN frq_tasks t ON t.id=x.task_id WHERE r.id=?""", (response["id"],)).fetchone() if response else None
                        if not prior or prior["source_sha256"] != digest(source):
                            skipped.append({"question_id": q["question_id"], "section_id": p["section_id"], "enrollment_id": p["enrollment_id"],
                                "reason": "Its scanned pages or answer area changed since it was read. Read it again with Read handwriting and grade." if prior
                                else "Nothing is retained to reuse: this response was never read, or its reading failed. Use Read handwriting and grade for it."})
                            continue
                    eligible.append({"question_id": q["question_id"], "question_revision_id": q["question_revision_id"],
                        "rubric_id": q["rubric_id"], "section_id": p["section_id"], "enrollment_id": p["enrollment_id"],
                        "response_id": response["id"] if response else None, "response_version": response["version"] if response else 0,
                        "source": source, "source_sha256": digest(source)})
            sample = data.get("sample")
            if sample is not None:
                # Measure before grading a whole exam: a stable pseudo-random sample of each question's eligible papers, the same
                # students for every question of their exam version, so a sample can be graded, reviewed and compared.
                if type(sample) is not int or not 1 <= sample <= 5000:
                    raise ValueError("A sample is a whole number of papers per question.")
                chosen = {}
                for member in sorted(eligible, key=lambda m: digest(f"sample:{m['section_id']}:{m['enrollment_id']}")):
                    chosen.setdefault(member["question_id"], []).append(member)
                eligible = [m for members in chosen.values() for m in members[:sample]]
            self.queue_version_checks(db, plan, participants)
            # The confirmed finalization is what the graders will read, so a newer confirmation makes this preview stale.
            fingerprint = digest({"assignment_id": aid, "version": plan["version"], "eligible": eligible, "settings": settings, "selection": selection,
                                  "rubric_finalization": finalization})
        mode = data.get("mode") if data.get("mode") in config.MODES else "nebius"
        try:
            estimate = self.estimate(settings, mode, len(eligible), self.crops_per_response(eligible))
        except ValueError as error:
            estimate = {"responses": len(eligible), "estimated_cost": None, "basis": str(error), "roles": {}}
        return {"fingerprint": fingerprint, "eligible": eligible, "skipped": skipped, "estimate": estimate, "existing_count": existing_count, "selection": selection,
            "rubric_finalization": finalization, "held_questions": held}

    def create_job(self, aid, data, actor="workspace"):
        """`actor` is `automatic` for a batch the worker starts by itself (`auto_grade.py`); its request is keyed `auto:`."""
        request_id = text(data.get("request_id"), "Request ID", 128, False)
        command_hash = digest(data)
        with self.rosters.lock():
            with connect(self.path) as db:
                previous = db.execute("SELECT * FROM frq_jobs WHERE request_id=?", (request_id,)).fetchone()
                if previous:
                    if previous["request_sha256"] != command_hash or previous["assignment_id"] != aid:
                        raise Conflict("This request ID was already used for a different grading command.")
                    return self.job(previous["id"])
            preview = self.preview(aid, data)
            if data.get("fingerprint") != preview["fingerprint"]:
                raise Conflict("Sources or grading settings changed. Review a fresh batch preview.")
            settings = self._batch_settings()
            # A batch keeps the finalization it was submitted under: its graders read that exact confirmation to the end.
            settings["rubric_finalization"] = preview["rubric_finalization"]
            mode, operation = data.get("mode", "local"), data.get("operation", "recognize")
            if mode not in config.MODES or operation not in ("recognize", "grade", "rescore"):
                raise ValueError("Choose a supported compute mode and recomputation stage.")
            # A tier is stored beside the original cloud mode, whose catalog values cannot change.
            tier, mode = (mode, "nebius") if mode in config.TIERS else ("", mode)
            if tier:
                settings["grading_workflow"] = committee.WORKFLOW
                settings["third_rule"] = committee.THIRD_RULE
                if direct_read.enabled(settings, tier):
                    # How an empty reading is treated travels with the batch, so one submitted earlier finishes as it ran.
                    settings["direct_reading_rule"] = direct_read.RULE
                    if settings.get(page_recovery.SETTING):
                        settings["reading_page_rule"] = page_recovery.RULE
            settings["full_credit_rule"] = rubric_quality.FULL_CREDIT_RULE  # Every mode: a ticked full-credit line is read as that line alone.
            from . import risk as risk_policy
            settings["empty_selection_rule"] = risk_policy.EMPTY_SELECTION_RULE  # Every mode: a grade that ticks nothing waits for the instructor.
            from .acceptance import REVIEW_RULE
            settings["review_rule"] = REVIEW_RULE  # Every mode: a regrade that passes every check replaces the instructor's earlier decision.
            # Every mode: model families decide nothing (requirement, 2026-09-23). A family may be left blank, so no new batch may
            # ask which family graded; batches frozen before this key keep the family rules they ran with.
            settings["family_rule"] = standard.FAMILY_RULE
            if tier:
                standard.freeze(settings)  # The Cloud tier's rules, its arbitrator and flag reviewer, travel with the batch.
            if tier and settings.get(choice.SETTING):
                # GRADING MULTIPLE-CHOICE READER: its rule and cutoff travel with the batch, so one submitted earlier finishes as it ran.
                settings["choice_reader_rule"], settings["choice_cutoff"] = choice.RULE, choice.CUTOFF
            if not preview["eligible"] or len(preview["eligible"]) > settings["max_batch_responses"]:
                raise ValueError("The batch must contain responses and fit the configured batch limit.")
            if operation == "recognize" and mode == "local" and not settings["glm_enabled"]:
                raise ValueError("Fully local recognition requires GLM-OCR.")
            if operation == "recognize" and mode == "hybrid" and not settings["mathpix_enabled"]:
                raise ValueError("Enable Mathpix to use hybrid recognition.")
            if operation == "recognize" and mode == "nebius" and not direct_read.enabled(settings, tier) and not settings["glm_enabled"] and not settings["mathpix_enabled"]:
                raise ValueError("Enable at least one OCR provider.")
            cloud = self.needs_cloud({"mode": mode, "operation": operation})
            compute = tier or mode
            estimate = self.estimate(settings, compute, len(preview["eligible"]),
                                     self.crops_per_response(preview["eligible"]))["estimated_cost"] if cloud and mode == "nebius" else 0
            # The estimate is information only (requirement, 2026-09-23): Faculty has no spending cap of its own; the OpenRouter and
            # Vercel accounts carry the limits. An unknown estimate (a connection without prices) is recorded as unknown.
            required = () if tier else ("local_verifier", "local_grader")
            if operation == "rescore":
                required = ()
            if tier and operation != "rescore":
                if standard.enabled(settings):
                    standard.require_models(settings)
                config.roles(settings, tier)  # Every role of the tier must name a configured, enabled endpoint.
                first_roles = committee.initial_roles(settings, tier)
                if not first_roles.get("primary") or not first_roles.get("reader"):
                    raise ValueError("Choose the Cloud handwriting reader and primary grader before starting a cloud batch.")
            settings["endpoint_fingerprints"] = {name: digest(config.endpoint(name).encode()) for name in required}
            jid, now = uid(), timestamp()
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if mode == "nebius" and db.execute("SELECT COUNT(*) FROM frq_jobs WHERE mode='nebius' AND status IN ('pending','running')").fetchone()[0] >= settings["max_cloud_jobs"]:
                    raise Conflict("The maximum number of cloud batches is already queued.")
                db.execute("""INSERT INTO frq_jobs(id,assignment_id,request_id,request_sha256,mode,operation,shadow,status,profile_json,estimated_cost,created_at,updated_at,tier)
                    VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?)""", (jid, aid, request_id, command_hash, mode, operation, 0, encoded(settings), estimate, now, now, tier))
                first = "scoring" if operation == "rescore" else "grading" if operation == "grade" else "extracting"
                lane = self.next_lane({"mode": mode, "operation": operation}, settings, first, (), 0)
                for member in preview["eligible"]:
                    rid = member["response_id"] or uid()
                    if not member["response_id"]:
                        db.execute("INSERT INTO frq_responses VALUES(?,?,?,?,?,0,NULL,NULL,?)", (rid, aid, member["question_id"], member["section_id"], member["enrollment_id"], now))
                    if db.execute("SELECT 1 FROM frq_tasks WHERE response_id=? AND status IN ('pending','running')", (rid,)).fetchone():
                        raise Conflict("A selected response already has pending work.")
                    prior = db.execute("""SELECT x.*,t.source_sha256,t.rubric_id,t.question_revision_id FROM frq_responses r
                        JOIN frq_results x ON x.id=r.current_result_id JOIN frq_tasks t ON t.id=x.task_id WHERE r.id=?""", (rid,)).fetchone()
                    human = db.execute("SELECT v.* FROM frq_responses r JOIN frq_reviews v ON v.id=r.current_review_id WHERE r.id=?", (rid,)).fetchone()
                    if operation != "recognize":
                        if not prior or prior["source_sha256"] != member["source_sha256"]:
                            raise Conflict("Recognition cannot be reused after source or response-region changes.")
                        if operation == "rescore":
                            def question_meaning(revision_id):
                                content = json.loads(db.execute("SELECT content_json FROM frq_question_revisions WHERE id=?", (revision_id,)).fetchone()[0])
                                return {key: value for key, value in content.items() if key != "max_points"}
                            current_meaning = question_meaning(member["question_revision_id"])
                            if question_meaning(prior["question_revision_id"]) != current_meaning:
                                raise Conflict("Question or solution changed. Rerun grading while retaining recognition.")
                            if human:
                                human_question = db.execute("SELECT t.question_revision_id FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (human["result_id"],)).fetchone()
                                if not human_question or question_meaning(human_question[0]) != current_meaning:
                                    raise Conflict("The instructor's saved selections use an earlier question or solution. Review the current proposal before recalculating those selections.")
                            old, new = self.rubric(db, prior["rubric_id"]), self.rubric(db, member["rubric_id"])
                            if old["semantics_sha256"] != new["semantics_sha256"]:
                                raise Conflict("Rubric meaning changed. Rerun grading while retaining recognition.")
                            if human and self.rubric(db, human["rubric_id"])["semantics_sha256"] != new["semantics_sha256"]:
                                raise Conflict("The instructor's saved selections use different rubric rules. Review a new grading proposal before recalculating those selections.")
                    db.execute("""INSERT INTO frq_tasks(id,job_id,response_id,question_revision_id,rubric_id,source_json,source_sha256,
                        reuse_result_id,human_transcript_json,stage,status,created_at,updated_at,human_review_id,lane) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)""",
                        (uid(), jid, rid, member["question_revision_id"], member["rubric_id"], encoded(member["source"]), member["source_sha256"],
                         prior["id"] if prior and operation != "recognize" else None,
                         human["transcript_json"] if human and human["source_sha256"] == member["source_sha256"] and operation != "recognize" else None,
                         first, now, now,
                         human["id"] if human and human["source_sha256"] == member["source_sha256"] and operation != "recognize" else None, lane))
                self.event(db, aid, "job_queued", {"job_id": jid, "mode": mode, "tier": tier, "operation": operation, "fingerprint": preview["fingerprint"],
                    "estimated_cost": estimate, **({"held_questions": preview["held_questions"]} if preview["held_questions"] else {})}, actor)
            self.inbox.wake.set()
        return self.job(jid)

    def regrade_response(self, rid, data):
        """Send ONE response through Cloud Standard on the retained reading, or read it once more.

        An ordinary batch of one, with its own request ID, estimate, cap, calls and receipts. It creates a new proposal, which
        replaces the instructor's earlier decision only where every check passes it (`acceptance.REVIEW_RULE`): asking for it is theirs.
        """
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_responses WHERE id=?", (rid,)).fetchone()
        if not row:
            raise NotFound("Response not found.")
        command = {"request_id": text(data.get("request_id"), "Request ID", 128, False), "question_ids": [row["question_id"]], "response_ids": [rid],
                   "mode": data.get("mode", "cloud_standard"), "operation": data.get("operation", "grade")}
        with self.rosters.lock():
            with connect(self.path) as db:
                previous = db.execute("SELECT * FROM frq_jobs WHERE request_id=?", (command["request_id"],)).fetchone()
                if previous:
                    members = [r[0] for r in db.execute("SELECT response_id FROM frq_tasks WHERE job_id=?", (previous["id"],))]
                    if (previous["assignment_id"] != row["assignment_id"] or members != [rid]
                            or previous["operation"] != command["operation"] or self.compute(dict(previous)) != command["mode"]):
                        raise Conflict("This request ID was already used for a different grading command.")
            if previous:
                return self.job(previous["id"], task_limit=200)
            command["fingerprint"] = self.preview(row["assignment_id"], command)["fingerprint"]
            return self.create_job(row["assignment_id"], command)

    def job(self, jid, results=False, task_limit=None):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                raise NotFound("Grading job not found.")
            result = dict(row)
            result["profile"] = json.loads(result.pop("profile_json"))
            result["usage"] = json.loads(result.pop("usage_json"))
            result["compute"] = self.compute(result)
            result["automatic"] = (result["request_id"] or "").startswith("auto:")  # Started by the worker (`auto_grade.py`).
            total = db.execute("SELECT COUNT(*) FROM frq_tasks WHERE job_id=?", (jid,)).fetchone()[0]
            # An exam-sized batch is thousands of responses: list them all only when asked, otherwise the ones needing attention.
            listed = "" if task_limit is None or total <= task_limit else " AND t.status IN ('failed','running')"
            # Each task names its response's question (never its student), so a failure can be told apart on a large batch.
            result["tasks"] = [dict(row) for row in db.execute(f"""SELECT t.id,t.response_id,r.question_id,t.stage,t.status,t.attempt,t.error,t.created_at,t.updated_at
                FROM frq_tasks t LEFT JOIN frq_responses r ON r.id=t.response_id WHERE t.job_id=?{listed} ORDER BY t.rowid LIMIT ?""", (jid, task_limit or -1))]
            result["tasks_listed"] = "all" if not listed else "attention"
            if results:
                result["results"] = [self.decode_result(r) for r in db.execute("SELECT x.* FROM frq_results x JOIN frq_tasks t ON t.id=x.task_id WHERE t.job_id=?", (jid,))]
            by_status = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM frq_tasks WHERE job_id=? GROUP BY status", (jid,))}
            # Count current proposals still awaiting a decision, not immutable flags that were already reviewed,
            # reset or replaced by another batch. Keep polling catalog-only; never read each paper for this card.
            # A proposal made under an earlier rubric or question waits to be graded again, not for review (2026-09-25), and a
            # decision made under an earlier rubric than this proposal's no longer decides it.
            states = {r[0]: r[1] for r in db.execute("""SELECT COALESCE(v.status,
                CASE WHEN x.state='proposed' AND a.kind='automatic' AND a.active=1 THEN 'auto_accepted'
                     ELSE x.state END) state,COUNT(*) FROM frq_results x
                JOIN frq_tasks t ON t.id=x.task_id JOIN frq_responses r ON r.current_result_id=x.id
                JOIN frq_assignment_questions p ON p.assignment_id=r.assignment_id AND p.question_id=r.question_id
                LEFT JOIN frq_reviews v ON v.id=r.current_review_id AND v.rubric_id=t.rubric_id
                LEFT JOIN frq_all_acceptances a ON a.result_id=x.id
                WHERE t.job_id=? AND t.rubric_id IS p.rubric_id AND t.question_revision_id IS p.question_revision_id GROUP BY 1""", (jid,))}
            calls = {r[0]: r[1] for r in db.execute("SELECT state,COUNT(*) FROM frq_cloud_calls WHERE job_id=? GROUP BY state", (jid,))}
            result["progress"] = {"completed": by_status.get("complete", 0), "total": total, "queued": by_status.get("pending", 0), "running": by_status.get("running", 0),
                "failed": by_status.get("failed", 0), "cancelled": by_status.get("cancelled", 0),
                "stages": {r[0]: r[1] for r in db.execute("SELECT stage,COUNT(*) FROM frq_tasks WHERE job_id=? AND status IN ('pending','running') GROUP BY stage", (jid,))},
                "proposed": states.get("proposed", 0), "awaiting_review": sum(n for state, n in states.items() if state not in ("approved", "manual", "auto_accepted")),
                "escalated": db.execute("SELECT COUNT(DISTINCT t.id) FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id WHERE t.job_id=? AND s.stage IN ('grade:check','grade:decision') AND json_extract(s.output_json,'$.escalation.escalated')", (jid,)).fetchone()[0],
                "calls": calls, "in_flight": calls.get("submitted", 0), "retried": calls.get("failed", 0) + calls.get("unknown", 0)}
            # A failed arbitration or flag review never fails its response (it keeps the team's grade and waits for the instructor), so
            # the batch looked finished while 93 arbitrations of trial batch 964f1940 had all been refused (2026-09-24). Said here, with
            # the host's reasons, counting only responses for which that step never succeeded.
            reviewers = {}
            for stage, key in (("grade:arbiter", "arbitrations"), ("grade:flag-review", "flag_reviews")):
                reasons = [{"reason": row[0] or "No reason was recorded.", "responses": row[1]} for row in db.execute(
                    """SELECT json_extract(s.output_json,'$.error'),COUNT(DISTINCT s.task_id) FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id
                       WHERE t.job_id=? AND s.stage=? AND json_extract(s.output_json,'$.failed')
                         AND NOT EXISTS(SELECT 1 FROM frq_steps o WHERE o.task_id=s.task_id AND o.stage=s.stage AND NOT COALESCE(json_extract(o.output_json,'$.failed'),0))
                       GROUP BY 1 ORDER BY 2 DESC LIMIT 5""", (jid, stage))]
                if reasons:
                    reviewers[key] = {"responses": sum(item["responses"] for item in reasons), "reasons": reasons}
            result["progress"]["reviewers_failed"] = reviewers
            reads = self.choice_reads(db, jid)
            if reads:  # Only a batch that read by choice carries it: every other job reads byte for byte as before.
                result["progress"]["choice_reads"] = reads
            result["reset"] = self.reset_summary(db, jid)
            # The canary was removed on 2026-09-28 (instructor: "I don't see myself ever using it"); an earlier batch still shows its runs.
            result["canary"] = [dict(r) for r in db.execute("SELECT endpoint,status,baseline,agreement,detail_json FROM frq_canary_runs WHERE job_id=?", (jid,))]
            for run in result["canary"]:
                run["detail"] = json.loads(run.pop("detail_json"))
            return result

    @staticmethod
    def choice_reads(db, jid):
        """GRADING MULTIPLE-CHOICE READER HEALTH (2026-10-09, from the review of OpenAI Decisions: a rejected key or an outage quietly
        sent every multiple-choice response down the ordinary path). Catalog only, over the batch's steps: responses graded by the
        read (`by_choice`) and those graded as written work after it (`written`), why (`reasons`: several, none, unsure, failed,
        alignment; only those that occurred) and the failed reads' leading errors (`failures`, as `reviewers_failed` lists them).
        {} when the batch read nothing by choice."""
        by_choice = db.execute("""SELECT COUNT(DISTINCT s.task_id) FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id
            WHERE t.job_id=? AND s.stage='grading' AND json_extract(s.output_json,'$.choice.rule') IS NOT NULL""", (jid,)).fetchone()[0]
        # A response's latest read decides its reason; one a later read graded by choice is not written work.
        latest = """FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id WHERE t.job_id=? AND s.stage='choice'
            AND s.attempt=(SELECT MAX(o.attempt) FROM frq_steps o WHERE o.task_id=s.task_id AND o.stage='choice')
            AND NOT EXISTS (SELECT 1 FROM frq_steps g WHERE g.task_id=s.task_id AND g.stage='grading' AND json_extract(g.output_json,'$.choice.rule') IS NOT NULL)"""
        reasons = {row[0]: row[1] for row in db.execute(f"""SELECT CASE WHEN json_extract(s.output_json,'$.failed') THEN 'failed'
            WHEN json_extract(s.output_json,'$.skipped') IS NOT NULL THEN 'alignment'
            WHEN json_extract(s.output_json,'$.choice')='{choice.SEVERAL}' THEN 'several'
            WHEN json_extract(s.output_json,'$.choice')='{choice.NONE}' THEN 'none' ELSE 'unsure' END reason,COUNT(DISTINCT s.task_id) {latest}
            GROUP BY 1""", (jid,))}
        if not by_choice and not reasons:
            return {}
        failures = [{"reason": row[0] or "No reason was recorded.", "responses": row[1]} for row in db.execute(
            f"SELECT json_extract(s.output_json,'$.error'),COUNT(DISTINCT s.task_id) {latest} AND json_extract(s.output_json,'$.failed') GROUP BY 1 ORDER BY 2 DESC,1 LIMIT 3",
            (jid,))] if reasons.get("failed") else []
        order = ("several", "none", "unsure", "failed", "alignment")
        return {"by_choice": by_choice, "written": sum(reasons.values()), "reasons": {key: reasons[key] for key in order if reasons.get(key)},
                **({"failures": failures} if failures else {})}

    @staticmethod
    def decode_result(row):
        return {k.removesuffix("_json"): json.loads(v) if k.endswith("_json") and v is not None else v for k, v in dict(row).items()}

    def cancel_job(self, jid, seen=None):
        """Cancel what is left of a batch. `seen` is the status the batch card showed when its Cancel was clicked."""
        with self.rosters.lock(), connect(self.path) as db:
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone()
            if not job:
                raise NotFound("Grading job not found.")
            # A click that lands after the batch finished (a settled card stops polling) must not turn it into a cancelled one: that
            # paused automatic grading and took away the Retry of its failures (2026-10-02). A batch that finished cleanly has nothing
            # to cancel. One that finished with failures still cancels when asked directly (its failures then become new work), but
            # not from a card that showed it still working: that click was meant for work that is no longer there. One already
            # cancelled answers as before.
            if not job["cancel_requested"] and (job["status"] == "review_ready" or (job["status"] == "failed" and seen in ("pending", "running"))):
                raise Conflict("This batch finished before it could be cancelled: nothing is left to cancel. "
                               + ("Its failed responses keep their Retry." if job["status"] == "failed" else "Its responses are on Review."))
            db.execute("UPDATE frq_jobs SET cancel_requested=1,status='cancelled',updated_at=? WHERE id=?", (timestamp(), jid))
            # A pending task has nobody waiting for its answer. A request of it that a restart interrupted will never be collected:
            # it is settled with the task, as an outcome nobody saw, unless the provider holds it under a durable ID (cancelled below).
            db.execute("""UPDATE frq_cloud_calls SET state='unknown',error='Cancelled with its batch after a restart had interrupted it. Its outcome is unknown, and the host may have billed it.',finished_at=?
                WHERE job_id=? AND state='submitted' AND provider_call_id IS NULL AND task_id IN (SELECT id FROM frq_tasks WHERE job_id=? AND status='pending')""", (timestamp(), jid, jid))
            db.execute("UPDATE frq_tasks SET status='cancelled',updated_at=? WHERE job_id=? AND status='pending'", (timestamp(), jid))
            # A Jev Routing batch (removed 2026-09-23) settles its retained routes with it.
            db.execute("UPDATE frq_jev_routes SET status='cancelled',updated_at=? WHERE job_id=? AND status IN ('pending','running','failed')", (timestamp(), jid))
            self.event(db, job["assignment_id"], "job_cancelled", {"job_id": jid, "note": "Requests in flight are stopped within a few seconds: their connections are "
                "closed, which stops the model at a host that supports cancellation; another may still bill what it produced. Receipts remain in the audit."})
            durable = [dict(r) for r in db.execute("SELECT * FROM frq_cloud_calls WHERE job_id=? AND state IN ('submitted','unknown') AND provider_call_id IS NOT NULL", (jid,))]
            settings = json.loads(job["profile_json"])
        for call in durable:
            # Cancellation where the provider permits it; a job it cannot cancel finishes and its receipt is kept.
            try:
                outcome = self.cloud_provider(config.named_endpoint(settings, call["endpoint"]), 30).cancel(call["provider_call_id"])
            except (ProviderError, ValueError):
                continue
            if outcome.get("state") == "cancelled":
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("UPDATE frq_cloud_calls SET state='cancelled',error='Cancelled with its batch.',finished_at=? WHERE id=? AND state IN ('submitted','unknown')", (timestamp(), call["id"]))
        return self.job(jid)

    def retry_job(self, jid):
        # Not under the worker's execution lock, which a busy worker holds (see retry_location); the cloud lane never took it.
        with self.rosters.lock(), connect(self.path) as db:
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone()
            if not job:
                raise NotFound("Grading job not found.")
            settings = json.loads(job["profile_json"])
            if reason := config.removed(self.compute(dict(job))) or config.removed_option(settings):
                raise Conflict(reason)
            failed = [dict(t) for t in db.execute("SELECT * FROM frq_tasks WHERE job_id=? AND status='failed'", (jid,))]
            if not failed or job["cancel_requested"]:
                raise Conflict("Only failed components of an uncancelled job can be retried.")
            # Each failed response is retried on its own merits, and one that cannot be is named instead of holding back the rest.
            # A response another batch has taken up since is never retried here: a late result of this older batch would replace
            # that newer proposal (retain_result points the response at whatever finished last), perhaps a finalized one.
            eligible, skipped = [], []
            for task in failed:
                later, busy = db.execute("""SELECT
                    EXISTS(SELECT 1 FROM frq_results x JOIN frq_tasks t ON t.id=x.task_id JOIN frq_jobs j ON j.id=t.job_id
                           WHERE x.response_id=? AND t.job_id!=? AND j.created_at>?),
                    EXISTS(SELECT 1 FROM frq_tasks a WHERE a.response_id=? AND a.status IN ('pending','running'))""",
                    (task["response_id"], jid, job["created_at"], task["response_id"])).fetchone()
                reason = ("It reached this batch's configured retry limit." if task["attempt"] >= settings["max_retries"]
                          else "Another batch is working on this response now." if busy
                          else "A later batch has graded this response; retrying it here would replace that newer proposal." if later else "")
                if not reason:
                    try:
                        self.validate_task(db, task)
                    except (Conflict, Forbidden, NotFound, ValueError) as changed:
                        reason = getattr(changed, "description", str(changed))
                if reason:
                    skipped.append({"task_id": task["id"], "response_id": task["response_id"], "reason": reason})
                else:
                    eligible.append(task)
            if not eligible:
                counts = {}
                for entry in skipped:
                    counts[entry["reason"]] = counts.get(entry["reason"], 0) + 1
                raise Conflict("No failed response of this batch can be retried. " + " ".join(f"{reason} ({n})" if n > 1 else reason for reason, n in counts.items()))
            estimate = None
            if job["mode"] == "nebius" and self.needs_cloud(job):
                rate = self.estimate(settings, self.compute(dict(job)), 1, crops=self.crops_per_response(eligible))["per_response"] or 0
                estimate = (job["estimated_cost"] or 0) + len(eligible) * rate
            db.execute("BEGIN IMMEDIATE")
            if estimate is not None:
                db.execute("UPDATE frq_jobs SET estimated_cost=? WHERE id=?", (estimate, jid))
            for task in eligible:
                # A task left at 'complete' by a context change during scoring (before that failed it at its own stage) scores again.
                db.execute("""UPDATE frq_tasks SET status='pending',attempt=attempt+1,error='',not_before='',stage=CASE WHEN stage='complete' THEN 'scoring' ELSE stage END,
                    updated_at=? WHERE id=? AND status='failed'""", (timestamp(), task["id"]))
            db.execute("UPDATE frq_jobs SET status='pending',updated_at=? WHERE id=?", (timestamp(), jid))
            self.event(db, job["assignment_id"], "job_retry", {"job_id": jid, "components": len(eligible),
                **({"skipped": [{"task_id": s["task_id"], "reason": s["reason"]} for s in skipped]} if skipped else {})})
        self.inbox.wake.set()
        return {**self.job(jid), "retry": {"retried": len(eligible), "skipped": skipped}}

    def reusable_crop(self, task, key):
        """This crop as an earlier attempt at the SAME response already cut it from the very same source, or None.

        Every batch used to cut each crop again and store its five images again: on the trial (2026-09-23) 33,670 stored crop images
        held 5,004 distinct ones. A task's source fingerprint covers the scanned page (by checksum), the answer area, its masks and
        the template, so an earlier task with the same fingerprint, cut by the same extraction, produced these very images. They are
        referenced, not copied: immutable artifacts of the same scan, read through the same checksum-verified storage."""
        with connect(self.path) as db:
            rows = db.execute("""SELECT s.task_id,s.output_json FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id
                WHERE t.response_id=? AND t.source_sha256=? AND t.id!=? AND s.stage=? ORDER BY s.rowid DESC LIMIT 5""",
                (task["response_id"], task["source_sha256"], task["id"], key)).fetchall()
        for row in rows:
            crop = json.loads(row["output_json"])
            if crop.get("metadata", {}).get("version") == extraction.VERSION and set(extraction.KINDS) <= set(crop.get("images") or {}):
                return {"images": crop["images"], "metadata": crop["metadata"],
                        "reused_from": crop.get("reused_from") or {"task_id": row["task_id"], "stage": key}}
        return None

    def validate_task(self, db, task, *, memo=None):
        """`memo`: a dict kept for ONE read over many tasks (`final_decisions`), never across a write. What depends only on the plan,
        question, event or participant is then read once, and only a read that succeeded is kept; every check of the task still runs,
        in this order. Without it (the worker, the dispatcher, a save) every read is made afresh."""
        def once(key, read):
            if memo is None:
                return read()
            if key not in memo:
                memo[key] = read()
            return memo[key]
        row = db.execute("SELECT * FROM frq_responses WHERE id=?", (task["response_id"],)).fetchone()
        plan = once(("plan", row["assignment_id"]), lambda: self.plan(db, row["assignment_id"]))
        question = once(("question", plan["id"], row["question_id"]), lambda: self.question(db, plan["id"], row["question_id"]))
        if question["question_revision_id"] != task["question_revision_id"] or question["rubric_id"] != task["rubric_id"] or not question["rubric"]["approved_at"]:
            raise Conflict("The question or rubric changed after this was graded. The next batch grades it again.")
        if memo is None:
            self.require_location_review(db, plan["id"], question)
        else:  # the no-regions refusal still comes first; the status is read once per question, its document's sides once
            status = once(("location", plan["id"], question["question_id"]), lambda: self.location_status(db, plan["id"], question, memo=memo)) \
                if question["content"]["regions"] else None
            self.require_location_review(db, plan["id"], question, status=status)
        # This student's own paper only, from the catalog (2026-09-24): no roster file, no name and no other student's copies. It
        # needs no lifecycle lock, and it gives exactly the collection the whole-exam view gives this student.
        event = once(("event", plan["event_id"]), lambda: self.exams._event(db, plan["event_id"]))
        participant = once(("participant", event["id"], row["section_id"], row["enrollment_id"]),
                           lambda: self.exams._participant_paper(db, event, row["section_id"], row["enrollment_id"]))
        if not participant:
            raise NotFound("This student is not a participant in this exam and section.")
        source = self.source(db, plan, question, participant, event, None if memo is None else memo.setdefault("shared", {}))
        if digest(source) != task["source_sha256"]:
            raise Conflict("The reviewed paper changed after this was graded. The next batch grades it again.")
        return plan, question, participant

    @staticmethod
    def step(db, task, stage):
        row = db.execute("SELECT output_json FROM frq_steps WHERE task_id=? AND stage=? ORDER BY attempt DESC,rowid DESC LIMIT 1", (task["id"], stage)).fetchone()
        if not row and stage in ("recognizing", "reconciling"):
            receipt = db.execute("SELECT output_json FROM frq_steps WHERE task_id=? AND stage='grading' ORDER BY attempt DESC,rowid DESC LIMIT 1", (task["id"],)).fetchone()
            graded = json.loads(receipt[0]) if receipt else {}
            if (graded.get("one_shot") or {}).get("workflow") == config.ONE_SHOT_WORKFLOW:
                # A One-shot result (removed 2026-09-23) reused by a later batch: projected from its ONE retained call, with no
                # fabricated OCR or reader steps and never a manufactured reader agreement.
                return ({"candidates": [], "consensus": consensus.align([]), "workflow": config.ONE_SHOT_WORKFLOW} if stage == "recognizing"
                        else {**graded["one_shot"], "source": "same_request_as_grade"})
            if (graded.get("choice") or {}).get("rule") == choice.RULE:
                # A multiple-choice read (grading/choice.py): no OCR and no reader were asked. Its one reading is the read label, with
                # the read's own confidence; there is no independent reading and none is claimed.
                return ({"candidates": [], "consensus": consensus.align([]), "workflow": choice.RULE} if stage == "recognizing"
                        else {"transcript": graded["choice"]["transcript"], "visual_confidence": graded["choice"]["confidence"],
                              "source": "choice_reader"})
        if not row and task.get("reuse_result_id"):
            prior = db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (task["reuse_result_id"],)).fetchone()
            if prior:
                return JobsMixin.step(db, dict(prior), stage)
        if not row:
            raise ValueError("A required prior grading component is missing.")
        return json.loads(row[0])

    @staticmethod
    def ruling_carrier(db, task):
        """(carrier task, carrier job) of the flag review whose ruling the result `task` reuses stands on, or None: the reused result is
        followed back through "Recalculate points" batches only, to a carrier batch (`flag_resolution.enabled`) that applied a ruling."""
        from . import flag_resolution
        seen, prior = set(), task.get("reuse_result_id")
        while prior and prior not in seen:
            seen.add(prior)
            row = db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (prior,)).fetchone()
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (row["job_id"],)).fetchone() if row else None
            if not job or job["operation"] != "rescore":
                return None
            if flag_resolution.enabled(json.loads(job["profile_json"])):
                return dict(row), dict(job)
            prior = row["reuse_result_id"]
        return None

    def response_crops(self, db, task, verified=None):
        crops = self.step(db, task, "extracting")
        if verified is None:
            try:
                verified = self.step(db, task, "reconciling")
            except ValueError:
                verified = {}
        return page_recovery.crops_for(crops, verified)

    def _finish_job(self, db, jid):
        previous = db.execute("SELECT status,mode,assignment_id FROM frq_jobs WHERE id=?", (jid,)).fetchone()
        statuses = [r[0] for r in db.execute("SELECT status FROM frq_tasks WHERE job_id=?", (jid,))]
        cancelled = db.execute("SELECT cancel_requested FROM frq_jobs WHERE id=?", (jid,)).fetchone()[0]
        state = "cancelled" if cancelled else "running" if any(s in ("running", "pending") for s in statuses) else "failed" if "failed" in statuses else "review_ready"
        db.execute("UPDATE frq_jobs SET status=?,updated_at=? WHERE id=?", (state, timestamp(), jid))
        if state in ("review_ready", "failed", "cancelled") and previous["status"] != state:
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            for row in db.execute("SELECT s.provider_json FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id WHERE t.job_id=?", (jid,)):
                measured = json.loads(row[0]).get("usage", {})
                if isinstance(measured, dict):
                    for key in usage:
                        if type(measured.get(key)) is int and measured[key] >= 0:
                            usage[key] += measured[key]
            db.execute("UPDATE frq_jobs SET usage_json=? WHERE id=?", (encoded(usage), jid))
            if previous["mode"] == "nebius":
                # Inline transport creates no provider files to retain/delete.
                # Keep an honest cleanup receipt instead of asserting log erasure.
                self.event(db, previous["assignment_id"], "cloud_cleanup", {"job_id":jid,
                    "status":"no_remote_files", "retention":"store:false; provider operational logs follow the account agreement"}, "worker")

    @staticmethod
    def _guarded(step):
        try:
            return step()
        except Exception:
            LOG.exception("Automatic pipeline step %s failed; it is tried again on a later turn", step.__name__)
            return False

    def process_one(self):
        # Rotate rubric preparation, template verification and response work. Exam-version confirmation shares the
        # template slot, the two taking turns to go first, so neither can starve the other.
        self._background_turn = (getattr(self, "_background_turn", 0) + 1) % 3
        if self._background_turn == 1 and self.process_preparation_one():
            return True
        if self._background_turn == 2:
            # The scan-to-grade pipeline's automatic steps (2026-09-28): a scan's review, papers made ready after it, then a batch.
            # A step that fails is logged and skipped for this turn, so it can never keep the version and answer-area checks waiting.
            # Late papers certify themselves last (LATE PAPERS CERTIFY THEMSELVES): once a certified assignment's late work is graded.
            # The flag review follows a finished batch by itself (2026-10-11), before late papers are looked at: its rulings can clear one.
            # Answer areas and answer keys confirm themselves (2026-10-22, `auto_prepare.py`) before a batch is looked for: a confirmed
            # area is what lets a question grade. A full-credit line reaches new answers before late papers are looked at
            # (GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS", `auto_certify.py`).
            # A DR copy is made once its Word file went up to AIM (DR COPIES, `dr_copies.py`), before its papers come back.
            # On a plan graded for participation the grades that wait for review are approved as they are, in the flag review's place
            # (GRADING "THE WAITING GRADES APPROVE THEMSELVES", `participation.py`).
            steps = (self.process_scan_one, self.auto_review_one, self.assemble_later_one, self.auto_prepare_one, self.auto_grade_one,
                     self.auto_flag_review_one, self.auto_participation_one, self.auto_full_credit_one, self.auto_certify_one, self.dr_copy_one)
            start = getattr(self, "_pipeline_turn", 0)
            scan_progress = False
            for offset in range(len(steps)):
                index = (start + offset) % len(steps)
                # Even a productive scan queue must give review, assembly and batch creation their own turns.
                # An unavailable step does not consume the other steps' opportunities.
                self._pipeline_turn = (index + 1) % len(steps)
                if self._guarded(steps[index]):
                    scan_progress = True
                    break
            self._version_first = not getattr(self, "_version_first", True)
            order = (self.process_version_one, self.process_location_one) if self._version_first else (self.process_location_one, self.process_version_one)
            if order[0]() or order[1]() or scan_progress:
                return True
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                # Holding the execution lock proves earlier running work of THIS lane was interrupted. The parallel
                # cloud lane recovers its own work under its own lock, by the retry policy instead of a failure.
                interrupted = db.execute("SELECT DISTINCT job_id FROM frq_tasks WHERE status='running' AND lane=''").fetchall()
                if interrupted:
                    db.execute("UPDATE frq_tasks SET status='failed',error='Worker interrupted; explicitly retry the unfinished component.',updated_at=? WHERE status='running' AND lane=''", (timestamp(),))
                    for row in interrupted:
                        self._finish_job(db, row[0])
                    return True
                row = db.execute("""SELECT t.* FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id
                    WHERE t.status='pending' AND t.lane='' AND j.cancel_requested=0 ORDER BY t.updated_at,t.rowid LIMIT 1""").fetchone()
                task = dict(row) if row else None
                if task:
                    job = dict(db.execute("SELECT * FROM frq_jobs WHERE id=?", (task["job_id"],)).fetchone())
                    db.execute("UPDATE frq_tasks SET status='running',updated_at=? WHERE id=?", (timestamp(), task["id"]))
                    db.execute("UPDATE frq_jobs SET status='running',updated_at=? WHERE id=?", (timestamp(), job["id"]))
            if task:
                self.run_claimed(task, job)
                return True
        # Without the dispatcher thread (tests, `worker --once`) cloud work still advances, one drained round per turn.
        thread = getattr(self, "_cloud_thread", None)
        if not (thread and thread.is_alive()) and self.process_cloud():
            return True
        # When this lane has nothing else to do (cloud batches run in their own thread): save graded work for training
        # (`training.py`). Catalog work only, no execution lock, and one short write under the lifecycle lock.
        return self.capture_training_one()

    def run_claimed(self, task, job):
        """One component of one response, already marked running by its lane. Both lanes record results the same way."""
        settings = json.loads(job["profile_json"])
        # The check reads the catalog alone (`validate_task`), so it takes no lifecycle lock; recording a failure is a write and does.
        # The result is checked again under the lock, in the transaction that records it.
        with CHECKING:
            try:
                if reason := config.removed(self.compute(job)) or config.removed_option(settings):
                    raise Conflict(reason)
                with connect(self.path) as db:
                    db.execute("BEGIN")  # One snapshot for every read of the check.
                    self.validate_task(db, task)
            except (ValueError, Conflict, Forbidden) as error:
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("UPDATE frq_tasks SET status='failed',error=?,updated_at=? WHERE id=?", (getattr(error, "description", str(error)), timestamp(), task["id"]))
                    self._finish_job(db, job["id"])
                return
        started = time.monotonic()
        try:
            stage_name, output, metadata, advance = self.execute_stage(task, job)
            duration = time.monotonic() - started
            with CHECKING, self.rosters.lock(), connect(self.path) as db:
                # Verified owner reads precede the write transaction.
                context_error, validated = "", None
                try:
                    validated = self.validate_task(db, task)
                except (Conflict, Forbidden, ValueError) as error:
                    context_error = getattr(error, "description", str(error))
                db.execute("BEGIN IMMEDIATE")
                # UNIQUE(task_id,stage,attempt): the first valid result of a component is the one applied. A second one can
                # only come from a request that was superseded; it is kept with its call and changes nothing.
                applied = db.execute("INSERT OR IGNORE INTO frq_steps VALUES(?,?,?,?,?,?,?,?,?,?)", (uid(), task["id"], stage_name, task["attempt"],
                    digest({"source": task["source_sha256"], "question": task["question_revision_id"], "rubric": task["rubric_id"], "profile": job["profile_json"]}),
                    encoded(output), digest(output), encoded(metadata), duration, timestamp())).rowcount
                if not applied:
                    for call_id in filter(None, [metadata.get("cloud_call_id"), *(m.get("cloud_call_id") for m in metadata.get("calls", []))]):
                        db.execute("UPDATE frq_cloud_calls SET state='superseded',error='Superseded: another request for this component was applied first.',receipt_json=? WHERE id=? AND state='completed'",
                                   (encoded({"late_result": output, "applied": False}), call_id))
                # Any other request still open for this component was superseded by the one just applied (or by an earlier
                # one): its late result, if a provider still holds one, is collected as a receipt and never applied.
                db.execute("UPDATE frq_cloud_calls SET state='superseded',error='Superseded: another request for this component was applied first.',finished_at=? WHERE task_id=? AND step=? AND state IN ('submitted','unknown')",
                           (timestamp(), task["id"], stage_name))
                if applied and stage_name in ("grade:check", "grade:decision") and output["escalation"]["escalated"]:
                    # Why a response was given more compute is part of its history, and of the batch's progress line.
                    self.event(db, job["assignment_id"], "response_escalated", {"task_id": task["id"], "job_id": job["id"], "to": output["escalation"]["to"],
                        "reasons": output["escalation"]["reasons"]}, "worker")
                    LOG.info("response escalated task=%s job=%s reasons=%s", task["id"], job["id"], len(output["escalation"]["reasons"]))
                cancelled = db.execute("SELECT cancel_requested FROM frq_jobs WHERE id=?", (job["id"],)).fetchone()[0]
                if applied and not cancelled and not context_error and stage_name == "scoring":
                    # Re-evaluate under the publication lock if the instructor changed the quality policy during this turn.
                    from .rubric_quality import apply_quality
                    quality = self.rubric_quality_status(db, job['assignment_id'])
                    qid = db.execute('SELECT question_id FROM frq_responses WHERE id=?', (task['response_id'],)).fetchone()[0]
                    output = apply_quality(output, quality, qid, task['rubric_id'])
                    self.retain_result(db, task, output, job, settings)
                # A stage name skips ahead: a confident multiple-choice read is the grade itself (`choice_read`), straight to scoring.
                next_stage = advance if isinstance(advance, str) else STAGES[STAGES.index(task["stage"])+1] if advance else task["stage"]
                if context_error and next_stage == "complete":
                    # The context changed while it was scoring, so no result was retained: it stays failed AT scoring, where a retry
                    # (which revalidates the context first) scores it again. Failed at 'complete' it could never be retried.
                    next_stage = task["stage"]
                status = "cancelled" if cancelled else "failed" if context_error else "complete" if next_stage == "complete" else "pending"
                keys = {r[0] for r in db.execute("SELECT stage FROM frq_steps WHERE task_id=?", (task["id"],))}
                pending = bool(validated) and next_stage == "recognizing" and self.choice_pending(db, task, job, settings, validated[1], keys)
                lane = self.next_lane(job, settings, next_stage, keys, len(json.loads(task["source_json"])["regions"]), choice_pending=pending)
                db.execute("UPDATE frq_tasks SET stage=?,status=?,error=?,lane=?,not_before='',updated_at=? WHERE id=?", (next_stage, status, context_error, lane, timestamp(), task["id"]))
                db.execute("UPDATE frq_jobs SET runtime_seconds=runtime_seconds+? WHERE id=?", (duration, job["id"]))
                self._finish_job(db, job["id"])
        except Cancelled:
            # Its batch was cancelled while this component waited for or ran a request: the request is settled as cancelled
            # (`cloud_call`), and the component ends with its batch, never as a failure and never retried.
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("UPDATE frq_tasks SET status='cancelled',updated_at=? WHERE id=? AND status='running'", (timestamp(), task["id"]))
                self._finish_job(db, job["id"])
        except RetryLater as waiting:
            # Cloud policy: the confirmation or the durable call ID is already recorded, so the component simply runs again.
            with self.rosters.lock(), connect(self.path) as db:
                cancelled = db.execute("SELECT cancel_requested FROM frq_jobs WHERE id=?", (job["id"],)).fetchone()[0]
                db.execute("UPDATE frq_tasks SET status=?,not_before=?,updated_at=? WHERE id=?", ("cancelled" if cancelled else "pending", later(waiting.seconds), timestamp(), task["id"]))
                self._finish_job(db, job["id"])
        except Exception as error:
            # Provider/parser exception text may contain student content. Only
            # explicitly safe diagnostics leave the worker.
            safe = str(error) if isinstance(error, ProviderError) else error.description if isinstance(error, (Conflict, Forbidden)) else "This component failed validation or processing. Its successful earlier components are retained; inspect and retry explicitly."
            with self.rosters.lock(), connect(self.path) as db:
                rejected = isinstance(error, ProviderError) and error.raw is not None
                if rejected:
                    db.execute("INSERT INTO frq_rejected_outputs VALUES(?,?,?,?,?,?,?)", (uid(), task["id"], task["stage"], task["attempt"], encoded(error.raw), encoded(error.metadata), timestamp()))
                # A malformed or truncated answer is a confirmed failure with its receipt retained. In the cloud lane it is
                # asked again, a bounded number of times; it never becomes a score, and then it waits for the instructor.
                again = rejected and task["lane"] == "cloud" and db.execute("SELECT COUNT(*) FROM frq_rejected_outputs WHERE task_id=? AND stage=? AND attempt=?",
                    (task["id"], task["stage"], task["attempt"])).fetchone()[0] < min(3, settings["max_retries"])
                if again:
                    db.execute("UPDATE frq_tasks SET status='pending',not_before=?,updated_at=? WHERE id=?", (later(1), timestamp(), task["id"]))
                else:
                    db.execute("UPDATE frq_tasks SET status='failed',error=?,updated_at=? WHERE id=?", (safe, timestamp(), task["id"]))
                self._finish_job(db, job["id"])
                self.event(db, job["assignment_id"], "component_retried" if again else "component_failed", {"task_id": task["id"], "stage": task["stage"], "error": safe}, "worker")

    def execute_stage(self, task, job):
        settings = json.loads(job["profile_json"])
        with CHECKING, connect(self.path) as db:  # Catalog reads only, in one snapshot: no lifecycle lock (see `validate_task`).
            db.execute("BEGIN")
            plan, question, participant = self.validate_task(db, task)
            content, rubric = question["content"], question["rubric"]["rules"]
            # The instructor's internal clarifications and exclusions travel with the problem to every grader of this batch.
            # `rubric` itself stays the approved original: it validates and scores, and it is what the record shows.
            content = {**content, "rubric_guidance": self.rubric_guidance(db, settings.get("rubric_finalization"), question["question_id"], task["rubric_id"])}
            source = json.loads(task["source_json"])
            existing = {r["stage"]: json.loads(r["output_json"]) for r in db.execute("SELECT * FROM frq_steps WHERE task_id=? ORDER BY rowid", (task["id"],))}
        stage, cloud = task["stage"], self.is_cloud(job)
        owner = {"task_id": task["id"], "job_id": job["id"], "task_attempt": task["attempt"], "tier": job.get("tier", "")}
        if stage == "extracting":
            for index, region in enumerate(source["regions"]):
                key = f"crop:{index}"
                if key in existing:
                    continue
                reused = self.reusable_crop(task, key)
                if reused:
                    return key, reused, {"processor": extraction.VERSION, "reused_from": reused["reused_from"]}, False
                _, scan = self.inbox.read_artifact(region["raster_artifact_id"])
                if digest(scan) != region["raster_sha256"]:
                    raise ProviderError("The source raster failed its checksum verification.")
                template = self.templates.read(source["template_id"], "image", region["page"])[0] if source["template_id"] else None
                with CUTTING:  # CPU work in the document sandbox: bounded apart from the model calls around it.
                    # DR COPIES: the area is the sheet itself, so it is cut whole; the blank only says which way up the sheet is.
                    # Every other crop is asked for exactly as before.
                    prepared = extraction.extract(scan, region["region"], template, region["privacy_masks"], **({"sheet": True} if source.get("dr_copy") else {}))
                return key, storage.retain(self.inbox, region["message_id"], task["id"], index, prepared), {"processor": extraction.VERSION}, False
            return stage, {"crops": [existing[f"crop:{i}"] for i in range(len(source["regions"]))]}, {"processor": extraction.VERSION}, True
        with connect(self.path) as db:
            crops = self.step(db, task, "extracting")
        if stage == "recognizing" and "choice" not in existing and cloud and job["operation"] == "recognize" \
                and choice.applies(content, rubric, task["rubric_id"], len(source["regions"]), settings):
            return self.choice_read(task, settings, owner, content, rubric, crops)
        if stage == "recognizing":
            # Cloud Standard reading the paper itself: no OCR at all, one free-text pass by the tier's own reader instead
            # (`grading/direct_read.py`). It is combined below exactly like an OCR reading, and hidden from every check that
            # asks for an INDEPENDENT one, because it is the same model that is about to read the image again.
            direct = direct_read.enabled(settings, job.get("tier", ""))
            providers = [direct_read.PROVIDER] if direct else (["glm"] if settings["glm_enabled"] else []) + (["mathpix"] if settings["mathpix_enabled"] and job["mode"] != "local" else [])
            # Every local reading first: GLM-OCR needs the single local GPU, while a cloud batch's Mathpix reads run in parallel.
            for provider in providers:
                for index, crop in enumerate(crops["crops"]):
                    key = f"ocr:{index}:{provider}"
                    if key in existing:
                        continue
                    image = storage.read(self.inbox, crop["images"]["cloud" if provider in ("mathpix", direct_read.PROVIDER) else "original"])
                    if provider == direct_read.PROVIDER:
                        result = direct_read.read(self.cloud_call, committee.initial_roles(settings, self.compute(job))["reader"],
                                                  image, settings, {**owner, "step": key})
                    elif provider == "mathpix" and cloud:
                        from .providers.cloud import MathpixEndpoint
                        reader = {**MathpixEndpoint.ENDPOINT, "per_minute": settings.get("mathpix_per_minute", config.DEFAULTS["mathpix_per_minute"])}
                        result = self.cloud_call({**owner, "step": key}, "reader", reader, {"prompt": "mathpix", "payload": {}, "images": [image]}, settings)["output"]
                    else:
                        result = self.provider(provider, settings).recognize(image)
                    result["transcript"] = transcript(result["transcript"])
                    for line in result["transcript"]["lines"]:
                        line["id"] = f"crop-{index}-" + line["id"]
                        for el in line["elements"]:
                            el["crop"] = index
                    return key, result, result.get("metadata", {}), False
            candidates = []
            for provider in providers:
                values = [existing[f"ocr:{i}:{provider}"]["transcript"] for i in range(len(crops["crops"]))]
                known = [v["confidence"] for v in values if v["confidence"] is not None]
                combined = transcript({"plain_text": "\n".join(v["plain_text"] for v in values), "latex": "\n".join(v["latex"] for v in values),
                    "lines": [line for v in values for line in v["lines"]], "ambiguities": [a for v in values for a in v["ambiguities"]],
                    "confidence": min(known) if len(known) == len(values) else None})
                # Mathpix reports two numbers. `confidence` is the chance that a WHOLE multi-line block is perfect, which is low for almost any
                # handwriting; `confidence_rate` is its per-symbol quality, which is what separates a misread from a clean reading.
                rates = [(existing[f"ocr:{i}:{provider}"].get("raw") or {}).get("confidence_rate") for i in range(len(crops["crops"]))]
                rated = all(isinstance(r, (int, float)) and not isinstance(r, bool) for r in rates) and bool(rates)
                # Crops the reader found nothing written on travel with the candidate: the review state asks about them.
                empty = direct_read.blank([existing[f"ocr:{i}:{provider}"] for i in range(len(crops["crops"]))])
                candidates.append({"provider": provider, "transcript": combined, **({"confidence_rate": min(rates)} if rated else {}),
                                   **({"blank_crops": empty} if empty else {})})
            # The alignment is between INDEPENDENT readings. The reader's own free-text pass is not one, so with direct
            # reading there is nothing to align and the consensus says so (no providers, no agreement) instead of claiming one.
            return stage, {"candidates": candidates, "consensus": consensus.align(direct_read.independent(candidates)),
                           **({"direct_reading": direct_read.rule(settings)} if direct else {})}, {"alignment": "structural-tex-v1"}, True
        with connect(self.path) as db:
            reading = self.step(db, task, "recognizing")
        kind = "nebius" if cloud else "local_verifier" if stage == "reconciling" else "local_grader"
        if stage != "scoring" and kind in settings["endpoint_fingerprints"] and settings["endpoint_fingerprints"][kind] != digest(config.endpoint(kind).encode()):
            raise ProviderError("The inference endpoint changed after submission. Create a new batch to use it.")
        # A job's frozen profile names its endpoints, so a batch keeps the models it was submitted with.
        roles = committee.initial_roles(settings, self.compute(job)) if cloud and stage != "scoring" else {}

        def infer(name, role, payload, pictures):
            if cloud:
                return self.cloud_call({**owner, "step": stage}, role, roles[role], {"prompt": name, "payload": payload, "images": pictures}, settings)
            return getattr(self.provider(kind, settings), "verify" if name == "reconcile" else name)(payload, pictures)
        images = [] if stage == "scoring" else [storage.read(self.inbox, crop["images"]["cloud" if kind == "nebius" else "original"]) for crop in crops["crops"]]
        if sum(map(len, images)) > 25 * 1024**2:
            raise ProviderError("Question crops exceed the inference image limit. Use smaller question regions.")
        if stage == "reconciling":
            # The reader sees each crop twice: as scanned, then enhanced. A cloud batch used to send its cloud copy in both places, the
            # same image twice, because it was the only copy privacy masks were applied to; the instructor lifted those constraints on
            # 2026-09-19. A crop that still carries masks sends its masked copy there as before; every other one its contrast view.
            enhanced = [storage.read(self.inbox, crop["images"]["cloud" if kind == "nebius" and crop["metadata"].get("privacy_masks") else "contrast"])
                        for crop in crops["crops"]]
            payload = {"response_id": task["response_id"], "candidates": reading["candidates"], "disagreements": reading["consensus"]["disagreements"],
                "image_order": "Original crops in order, then enhanced crops in the same order."}
            spare = config.fallback(settings, self.compute(job), "reader") if cloud else None
            reader = roles.get("reader")
            prompt_name = direct_read.reconcile_prompt(settings) if cloud and direct_read.enabled(settings, self.compute(job)) else "reconcile"
            if cloud and page_recovery.enabled(settings):
                prompt_name = page_recovery.PROMPT
            try:
                result = infer(prompt_name, "reader", payload, images + enhanced)
            except RetryLater:
                raise  # The ordinary wait-and-retry of a busy host: the reader has not given up yet.
            except ProviderError:
                if not spare:
                    raise
                # The reader has exhausted its own attempts (confirmed failures, or a host that stayed rate limited). It is asked
                # once more with the spare, on its own step and budget. Nothing is switched: the next response starts again with
                # the configured reader, because this step belongs to one task.
                result = self.cloud_call({**owner, "step": stage + ":fallback"}, "reader", spare,
                                         {"prompt": prompt_name, "payload": payload, "images": images + enhanced}, settings)
                result["metadata"] = {**result["metadata"], "fallback_for": roles["reader"]["name"]}
                reader = spare

            def accepted(result):
                try:
                    if not isinstance(result["output"], dict):
                        raise ValueError("Invalid reading")
                    # These fields authorize evidence retained by Faculty, never by a model's JSON response.
                    value = {k: v for k, v in result["output"].items() if k not in ("context_recovery", "first_reading_check")}
                    value["transcript"] = transcript(value.get("transcript"))
                    number(value.get("visual_confidence"), "Visual confidence", 0, 1)
                    if any(el["crop"] >= len(crops["crops"]) for line in value["transcript"]["lines"] for el in line["elements"]):
                        raise ValueError("Unavailable crop")
                except (ValueError, TypeError, AttributeError):
                    raise ProviderError("Visual verification failed its output contract. No reading was accepted.", raw=result["raw"], metadata=result["metadata"]) from None
                return value
            value = accepted(result)
            if cloud and page_recovery.enabled(settings):
                result, value, reading = page_recovery.recover(self, task, owner, settings, reader, reading, crops, payload, result, value, accepted)
                recovery = value.get("context_recovery")
                if recovery and recovery.get("status") == "recovered":
                    payload = {**payload, "candidates": reading["candidates"], "page_round": True,
                               "context_crops": [r["crop"] for r in recovery["requests"]]}
                    images = [storage.read(self.inbox, crop["images"]["cloud"]) for crop in recovery["crops"]]
                    enhanced = [storage.read(self.inbox, crop["images"]["contrast"]) for crop in recovery["crops"]]
            first = direct_read.first_reading(reading["candidates"]) if cloud and direct_read.keeps_numbers(settings) else None
            if first is not None and direct_read.keeps_literal(settings):
                changes = direct_read.literal_changes(reading["candidates"], value["transcript"])
                check = {"rule": direct_read.rule(settings), "changes": changes}
                if changes:
                    try:
                        again = self.cloud_call({**owner, "step": stage + ":first-reading"}, "reader", reader,
                            {"prompt": prompt_name, "payload": {**payload, "literal_changes": changes}, "images": images + enhanced, "reask": True}, settings)
                        recovery = value.get("context_recovery")
                        result, value = again, accepted(again)
                        if recovery:
                            value["context_recovery"] = recovery
                        changes = direct_read.literal_changes(reading["candidates"], value["transcript"])
                        check.update(asked_again=True, remaining=changes)
                    except RetryLater:
                        raise
                    except ProviderError as failure:
                        check.update(asked_again=False, error=str(failure)[:300])
                    if changes:
                        value["transcript"]["ambiguities"].append(direct_read.literal_doubt(changes))
                value["first_reading_check"] = check
            elif first is not None:
                # The arrangement of the reader's own free reading keeps its numbers (direct-reading-v3): a number it wrote that the
                # free reading does not have is asked about once more, and if it stays the response waits with both readings named.
                new, missing = direct_read.introduced(first, value["transcript"])
                check = {"rule": direct_read.rule(settings), "introduced": new, "missing": missing}
                if new:
                    try:
                        again = self.cloud_call({**owner, "step": stage + ":first-reading"}, "reader", reader,
                                                {"prompt": "reconcile", "payload": {**payload, "introduced_numbers": new}, "images": images + enhanced, "reask": True}, settings)
                        result, value = again, accepted(again)
                        new, missing = direct_read.introduced(first, value["transcript"])
                        check.update(asked_again=True, still=new, still_missing=missing)
                    except RetryLater:
                        raise
                    except ProviderError as failure:
                        check.update(asked_again=False, error=str(failure)[:300])
                    if new:
                        value["transcript"]["ambiguities"] = [*value["transcript"]["ambiguities"], direct_read.doubt(new, missing)]
                value["first_reading_check"] = check
            if (value.get("context_recovery") or {}).get("error"):
                error = value["context_recovery"]["error"]
                if error not in value["transcript"]["ambiguities"]:
                    value["transcript"]["ambiguities"].append(error)
            elif (value.get("context_recovery") or {}).get("status") == "recovered" and value.get("page_requests"):
                error = "The whole-page reading still needs more context; inspect the original paper."
                if error not in value["transcript"]["ambiguities"]:
                    value["transcript"]["ambiguities"].append(error)
            return stage, {**value, "provider_response": result["raw"]}, result["metadata"], True
        with connect(self.path) as db:
            verified = self.step(db, task, "reconciling")
        crops = page_recovery.crops_for(crops, verified)
        if stage == "grading" and verified.get("context_recovery", {}).get("status") == "recovered":
            images = [storage.read(self.inbox, crop["images"]["cloud" if cloud else "original"]) for crop in crops["crops"]]
        if task["human_transcript_json"]:
            verified = {**verified, "transcript": json.loads(task["human_transcript_json"]), "human_corrected": True}
        if stage == "grading" and cloud:
            # Independent opinions and selective escalation: one retained component per turn, the proposal last.
            return self.cloud_grading_unit(task, job, settings, owner, roles, existing, content, rubric, reading, verified, crops, images)
        if stage == "grading":
            from .reading_context import for_graders
            result = infer("grade", "primary", self.grade_payload(task["response_id"], content, rubric, verified["transcript"], for_graders(reading, verified, crops, settings)), images)
            try:
                graded = recognition.validate_grade(result["output"], verified["transcript"], rubric, content["max_points"], len(images))
                if settings.get("full_credit_rule") in rubric_quality.FULL_CREDIT_RULES:
                    graded = rubric_quality.full_credit_alone(graded, content.get("rubric_guidance"), rubric, content["max_points"], settings["full_credit_rule"])
            except (ValueError, TypeError, AttributeError, KeyError):
                raise ProviderError("Rubric application failed its output contract. No grade was accepted.", raw=result["raw"], metadata=result["metadata"]) from None
            return stage, {**graded, "provider_response": result["raw"]}, result["metadata"], True
        with connect(self.path) as db:
            graded = self.step(db, task, "grading")
        if job["operation"] == "rescore":
            from . import grader_deductions
            # The grade's own approved deductions (GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS"); [] for a grade that decided none.
            adjustments, human = grader_deductions.adjustments(graded), None
            if task.get("human_review_id"):
                with connect(self.path) as db:
                    human = db.execute("SELECT * FROM frq_reviews WHERE id=?", (task["human_review_id"],)).fetchone()
                if human:
                    graded["applications"] = [{"confidence": 1, **a} for a in json.loads(human["applications_json"])]
                    saved_score = json.loads(human["score_json"])
                    adjustments = saved_score["adjustments"] if saved_score else []
            from . import flag_resolution
            if flag_resolution.enabled(settings):
                # A flag review's carrier batch (grading/flag_resolution.py): the grade its ruling stands on, scored and checked again below,
                # on the transcript as the ruling corrected it where the crop showed what the reader had missed or misread.
                graded = self.flag_resolution_grade(task, job, graded)
                verified = flag_resolution.amended(verified, graded)
                from .flag_adjustments import TRANSIENT
                # The ruling's own deductions (flag-resolution-v3), else those of the assessment it stands on.
                adjustments = graded.pop(TRANSIENT, adjustments if human else grader_deductions.adjustments(graded))
            elif not human:
                # RECALCULATING KEEPS A FLAG RULING (2026-10-26): a grade that stood on a flag review's ruling is recalculated on that
                # ruling, re-applied by its own carrier batch exactly as it first ran, never on the earlier assessment the ruling replaced.
                # Seen live on Exam 2's Q24: 17 settled grades went back to their first grader's lines when one point value changed.
                with connect(self.path) as db:
                    carrier = self.ruling_carrier(db, task)
                if carrier:
                    graded = self.flag_resolution_grade(*carrier, graded)
                    verified = flag_resolution.amended(verified, graded)
                    from .flag_adjustments import TRANSIENT
                    adjustments = graded.pop(TRANSIENT, grader_deductions.adjustments(graded))
                    ruled = (graded.get("opinions") or {}).get(flag_resolution.BASIS) or {}
                    if ruled.get("scoring"):
                        # Only the points changed: the score the ruling expects is its own lines and deductions on today's points.
                        try:
                            expected = score(rubric, graded["applications"], content["max_points"], adjustments)["score"]
                        except ValueError:
                            expected = None  # Its lines no longer score: the scoring below says so and the grade waits.
                        if expected is not None:
                            ruled["scoring"] = {**ruled["scoring"], "expected_score": expected}
            try:
                graded["score"] = score(rubric, graded["applications"], content["max_points"], adjustments)
            except ValueError:
                graded["score"] = None
                graded["review_reasons"].append("Stored applications conflict with the revised scoring rules.")
        output, verdict = self.scored(task, job, settings, question, content, rubric, reading, verified, crops, graded)
        return stage, output, {"scorer": "decimal-rules-v1", "review_rules": REVIEW_RULES, "risk_policy": verdict["policy"]}, True

    def choice_pending(self, db, task, job, settings, question, keys):
        """GRADING MULTIPLE-CHOICE READER: a task whose next component is the multiple-choice read runs on the cloud lane, never under
        the single GPU worker's lock (it reads the question as `execute_stage` will)."""
        if "choice" in keys or job["operation"] != "recognize" or not self.is_cloud(job) or not choice.enabled(settings):
            return False
        content = {**question["content"], "rubric_guidance": self.rubric_guidance(db, settings.get("rubric_finalization"),
                                                                                  question["question_id"], task["rubric_id"])}
        return choice.applies(content, question["rubric"]["rules"], task["rubric_id"], len(json.loads(task["source_json"])["regions"]), settings)

    def choice_read(self, task, settings, owner, content, rubric, crops):
        """GRADING MULTIPLE-CHOICE READER (grading/choice.py): one Decisions request for the answer area. A confident read IS this
        task's grade: the `grading` step, straight to scoring, where every deterministic check still runs. Anything else (unsure,
        none, several, a failed request) is kept as the `choice` step and the ordinary path reads and grades the response."""
        grading = choice.resolved(content, rubric, task["rubric_id"])
        if not crops["crops"][0]["metadata"].get("registered"):
            # The crop may not show the whole answer area: the ordinary path reads it, and its alignment check asks you as ever.
            return "choice", {"rule": choice.RULE, "skipped": "The answer area's alignment is uncertain."}, {"workflow": choice.RULE}, False
        image = choice.prepare(storage.read(self.inbox, crops["crops"][0]["images"]["original"]))
        request = {"prompt": "choice", "payload": {"state": choice.STATE, "questions": choice.questions(grading["options"])}, "images": [image]}
        try:
            result = self.cloud_call({**owner, "step": "choice"}, "choice_reader", choice.ENDPOINT, request, settings)
        except ProviderError as failure:
            return "choice", {"rule": choice.RULE, "failed": True, "error": str(failure)[:300]}, {"workflow": choice.RULE}, False
        read = choice.decide(result["output"]["answers"], grading["options"], settings.get("choice_cutoff", choice.CUTOFF))
        if read["confident"]:
            try:
                graded = choice.grade(read, grading, rubric, content["max_points"], recognition.validate_grade)
            except (ValueError, TypeError, KeyError) as error:
                read = {**read, "confident": False, "error": f"The read could not be applied to the rubric ({error})."[:300]}
            else:
                graded["opinions"] = {"roles": {"choice_reader": {"endpoint": choice.ENDPOINT["name"], "model": choice.ENDPOINT["model"],
                                                                   "lineage": choice.ENDPOINT["lineage"]}},
                                      "committee": choice.committee(read), "escalation": {"escalated": False, "depth": 0, "reasons": []}}
                return "grading", graded, {**result["metadata"], "workflow": choice.RULE}, "scoring"
        return ("choice", {**choice.summary(read), **({"error": read["error"]} if read.get("error") else {})},
                {**result["metadata"], "workflow": choice.RULE}, False)

    def scored(self, task, job, settings, question, content, rubric, reading, verified, crops, graded, scope=True):
        """One proposal through every deterministic check: what the `scoring` stage retains, and what Final Arbitrator Mode asks
        BEFORE that stage to learn whether a response would wait for the instructor (`scope=False`: no acceptance scope is needed
        to know that). One method, so the question and the recorded answer can never drift apart."""
        # Reassess conditions against the actual selections and current approved rules; refresh only server-generated
        # condition reasons, preserving model-authored doubts. Recalculating points does this without inference.
        graded["review_reasons"] = current_condition_reasons(graded["review_reasons"], rubric, graded["applications"], graded.get("condition_review_reasons", []))
        from . import arbitration
        # The grade's own requests and doubts, before the checks add theirs: what a ruling's renamed note stands for (GRADING "A SETTLED
        # NOTE STAYS SETTLED"), only in a batch that froze the rule.
        own = ({"review_reasons": list(graded["review_reasons"]), "rubric_ambiguities": list(graded.get("rubric_ambiguities") or [])}
               if arbitration.settled_notes(settings) else None)
        # Deterministic facts about the final answer, symbolic checks of the written work, every independent opinion and the
        # rule-based risk policy. They group, order and flag; none of them selects, adds or removes a rubric line.
        opinions = graded.get("opinions") or {}
        if graded.get("choice") and not content.get("answer_key"):
            # AUTOMATIC CONFIRMATION: a multiple-choice read with no saved key is checked against the rubric's own correct choice,
            # the line the instructor approved, as a key saved from the rubric would be.
            content = {**content, "answer_key": [{"label": "", "accept": [graded["choice"]["key"]]}]}
        found, applied, contradictions, checks, parts, verdict = self.evidence(task, settings, content, rubric, reading, verified, crops, graded,
            opinions.get("second"), opinions.get("verifier"), opinions.get("roles"), opinions.get("escalation"), opinions.get("committee"))
        graded = {**graded, "review_reasons": graded["review_reasons"] + contradictions + verdict["reasons"]}
        state, signals, reasons = recognition.review_state(reading, verified, graded, crops, settings)
        from . import risk as risk_policy
        if risk_policy.review_policy(settings) == "disagreement_and_work":
            # Only what the verdict kept may hold the response. The reading and the graders' own doubts were computed and stay
            # on the record (`waived`) and on the review card; they no longer decide the state. The rubric finalization is
            # applied after this, by `apply_quality`, and is never waived here. A blank crop is kept by the verdict (direct-reading-v2):
            # it is a reading matter, so the response waits in `ocr_review` with the reader's own reason, as under every check.
            blank = [b for b in verdict["blocks"] if risk_policy.blank(b)]
            verdict["waived"] = {**verdict.get("waived", {}), "state": state,
                                 "review_state_reasons": [r for r in reasons if r not in verdict["reasons"] and not risk_policy.blank(r)]}
            state = ("both" if blank and len(blank) < len(verdict["blocks"]) else "ocr_review" if blank
                     else "grading_review" if verdict["blocks"] else "proposed")
            reasons = list(dict.fromkeys([*verdict["reasons"], *blank]))
        signals["answer"] = {**found, "group": answers.signature(task["question_revision_id"], found, applied)}
        # Component signals are persisted, not only the verdict, so a calibration model can later replace the rules.
        with self.rosters.lock(), connect(self.path) as db:
            quality = self.rubric_quality_status(db, job['assignment_id'])
        if scope:
            scope, scope_sha = self.acceptance_scope(task, job, settings, question)
            if 'rubric_quality' not in scope:  # A batch from before finalizations were frozen names the current one, as it did.
                scope['rubric_quality'] = quality['review']['id'] if quality['review'] else 'unconfirmed'
                scope_sha = digest(scope)
            if graded.get("choice"):  # A grade the multiple-choice reader made is told apart from a team's by its scope.
                scope["choice_reader"] = {"rule": choice.RULE, "model": choice.ENDPOINT["model"], "cutoff": settings.get("choice_cutoff")}
                scope_sha = digest(scope)
            signals.update(scope=scope, scope_sha256=scope_sha)
        if graded.get("normalized"):
            signals["normalized"] = graded["normalized"]  # What the model ticked beside the full-credit line, for the review card.
        if graded.get("choice"):
            signals["choice"] = {k: v for k, v in graded["choice"].items() if k != "transcript"}  # The read, for the review card.
        signals.update(risk={**verdict, "components": parts}, symbolic={"checks": checks[:40]}, tier=self.compute(job), models=opinions.get("roles") or {},
                       escalation=opinions.get("escalation") or {"escalated": False, "depth": 0, "reasons": []})
        for name in ("second", "verifier", "standard_primary", "committee", "panels"):
            if opinions.get(name):
                signals[name] = opinions[name]
        from .rubric_quality import apply_quality
        output = {"transcript": verified["transcript"], "applications": graded["applications"], "interpretation": graded["interpretation"],
            "score": graded["score"], "signals": signals, "reasons": reasons, "state": state}
        if opinions.get("arbiter"):
            # The arbitrator's ruling settles only the flags it was shown and resolved, BEFORE the rubric finalization is projected
            # over the final ticks, which no model can settle.
            output = arbitration.apply(output, opinions["arbiter"], own)
        elif opinions.get("flag_review"):
            output = standard.apply(output, opinions["flag_review"])
        from . import flag_resolution
        if opinions.get(flag_resolution.BASIS):
            # The flag review's ruling, like the arbitrator's: it settles only flags it was shown and resolved, before the finalization.
            output = flag_resolution.apply(output, opinions[flag_resolution.BASIS], own)
        output = apply_quality(output, quality, question['question_id'], task['rubric_id'])
        # Last, after any ruling and the rubric finalization, so no model settles it: a grade that ticks no line at all.
        zero = risk_policy.empty_selection(settings, output["applications"], rubric)
        if zero:
            output = risk_policy.hold_empty(output, zero, settings, rubric)
            reason = risk_policy.empty_reason(settings, rubric)
            verdict = {**verdict, "individual": True, "level": "high", "blocks": list(dict.fromkeys([*verdict["blocks"], reason]))}
        if scope:
            output, verdict = self.jev_blank(task, output, verdict, crops)
        return output, verdict

    def jev_blank(self, task, output, verdict, crops):
        """JEV BLANK ANSWERS (grading/jev_blank.py): a zero held only because the reader found every area empty is shown to Jev; where
        Jev too says the problem was left blank, the zero goes to the ordinary acceptance gate. Last of all, so nothing settles it after."""
        from . import jev_blank
        jev = getattr(self, "jev", None)
        regions = json.loads(task["source_json"]).get("regions") or []
        if not jev or not jev_blank.eligible(output, crops, regions):
            return output, verdict
        from jev import client
        if not client.available():
            return output, verdict
        whole = jev_blank.shown_whole(crops, regions)
        try:
            pictures = [self._scan_page(regions[i]) if i in whole else storage.read(self.inbox, crop["images"]["original"])
                        for i, crop in enumerate(crops["crops"])]
            if any(picture is None for picture in pictures):
                raise ValueError("A scanned page failed its checksum.")
            found = {**jev_blank.check(jev, task["response_id"], pictures), **({"whole_pages": whole} if whole else {})}
        except Exception:
            LOG.exception("Jev could not look at the answer areas of response %s; it waits for the instructor", task["response_id"])
            found = {"rule": jev_blank.RULE, "cutoff": jev_blank.CUTOFF, "status": "failed"}
        return jev_blank.apply(output, verdict, found)

    @staticmethod
    def grade_payload(response_id, content, rubric, current_transcript, reading_context=None):
        """What a grader receives: the fixed transcript with the problem, solution and rubric. Never a name or a roster field."""
        from .rubric_quality import grader_rubric
        return {"response_id": response_id, "transcript": current_transcript, "rubric": grader_rubric(rubric, content.get("rubric_guidance")),
            "problem": content["problem"], "solution": {k: content[k] for k in ("coordinator_solution", "instructor_solution", "worked_solution", "expected_answer", "acceptable_methods")},
            # Only the part labels: which answers to report. Whether one matches the key is decided by the backend.
            "answer_parts": [part["label"] for part in content.get("answer_key", [])] or [""],
            "calibration_examples": content["examples"], **({"reading_context": reading_context} if reading_context is not None else {})}

    def retain_result(self, db, task, output, job=None, settings=None):
        rid = uid()
        db.execute("INSERT INTO frq_results VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid, task["id"], task["response_id"],
            encoded(output["transcript"]), encoded(output["applications"]), encoded(output["interpretation"]), encoded(output["score"]),
            encoded(output["signals"]), encoded(output["reasons"]), output["state"], timestamp()))
        db.execute("UPDATE frq_responses SET current_result_id=?,version=version+1 WHERE id=?", (rid, task["response_id"]))
        if job and output["signals"].get("scope_sha256"):
            # Trial (a test, never final) or, under a retained report for this exact scope, automatic. Otherwise it stays a proposal.
            self.accept_result(db, task, job, settings, rid, output)
        # current_review_id is left as it is here: only `accept_result`, in a batch frozen with `acceptance.REVIEW_RULE` whose new grade
        # every check passed, moves it (the instructor started that regrade). Export validates its exact result and rubric.
