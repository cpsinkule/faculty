"""FRQ service integration; no inference takes place in request handlers."""
import threading
import time
from pathlib import Path

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect

from .assignments import AssignmentsMixin
from .jobs import JobsMixin
from .review import ReviewMixin
from .preparation import PreparationMixin
from .location_checks import LocationChecksMixin
from .rubric_uploads import RubricUploadsMixin
from .answer_groups import AnswerGroupsMixin
from .review_queue import ReviewQueueMixin
from .batch_resets import BatchResetsMixin
from .exam_versions import ExamVersionsMixin
from .cloud import CloudMixin
from .escalation import EscalationMixin
from .acceptance import AcceptanceMixin
from .intake import IntakeMixin
from .auto_certify import AutoCertifyMixin
from .auto_grade import AutoGradeMixin
from .auto_flag_review import AutoFlagReviewMixin
from .auto_prepare import AutoPrepareMixin
from .models import encoded
from .model_catalog import ModelCatalog
from .rubric_quality import RubricQualityMixin
from .rubric_links import RubricLinksMixin
from .rubric_choices import RubricChoicesMixin
from .participation import ParticipationMixin
from .rubric_generation import RubricGenerationMixin
from .template_workflow import TemplateWorkflowMixin
from .bubble_keys import BubbleKeysMixin
from .training import TrainingMixin
from .quality_audit import QualityAuditMixin
from .flag_resolution import FlagResolutionMixin
from .quiz_drafts import QuizDraftsMixin
from .makeup_drafts import MakeupDraftsMixin
from .dr_copies import DrCopiesMixin
from .providers.glm_ocr import GlmOcrProvider
from .providers.mathpix import MathpixProvider
from .providers.local_vlm import LocalVlmProvider


def waiting_href(waiting):
    """Where "Finish Inbox checks" leads: the one scan waiting, else the Inbox's list."""
    return waiting[0]['entry']['inbox_href'] if len(waiting) == 1 else '#inbox'


def waiting_list(waiting):
    """The assignments whose scans still wait for Inbox checks, named (read-only; the index lists them without a card).

    Each names its course and term and where its first waiting scan is; nothing about students or pages. Additive (2026-10-18, I2):
    `assignment_id` (the plan's course-wide source assignment) and `term_id`, so a link can open the assignment's Collect with the
    scans that still need scan checks (`#assignments/<assignment_id>/<term_id>?area=collect&scans=waiting`)."""
    return [{'id': plan['id'], 'title': plan['title'], 'code': plan['code'], 'term': plan['term'], 'term_other': plan.get('term_other') or '',
             'year': plan['year'], 'href': plan['entry']['inbox_href'], 'scans': plan['entry'].get('pending_scans', 0),
             'assignment_id': plan['source_assignment_id'], 'term_id': plan['term_id']} for plan in waiting]


def gradescope_brief(db, aid, responses, decisions=None):
    """For the assignment card: what is on Gradescope now across every send (read only, from Faculty's receipts; nothing is
    contacted), `sent_at` the latest send that put anything there (an accepted paper or grade) and `last_send_at` the latest send
    whatever became of it, since a cancelled send or one where nothing was accepted sent nothing (review fix GR-7). None before
    any send. `decisions`: `progress.decision_rows` of `responses` when the caller's read has them (`_grade_coverage`)."""
    latest = db.execute("SELECT created_at FROM frq_gradescope_sends WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (aid,)).fetchone()
    if not latest:
        return None
    # `+status` keeps each EXISTS on its send's own receipts (the send_id index) instead of walking every accepted receipt of every
    # plan through the status index (PERFORMANCE, 2026-10-02: about 4 ms a plan on each Grading-index read); the same answer.
    sent = db.execute("""SELECT s.created_at FROM frq_gradescope_sends s WHERE s.assignment_id=?
        AND (EXISTS(SELECT 1 FROM frq_gradescope_uploads u WHERE u.send_id=s.id AND +u.status='accepted')
             OR EXISTS(SELECT 1 FROM frq_gradescope_grades g WHERE g.send_id=s.id AND +g.status='accepted'))
        ORDER BY s.created_at DESC,s.rowid DESC LIMIT 1""", (aid,)).fetchone()
    from .gradescope_sync import GradescopeSender
    return {"sent_at": sent[0] if sent else None, "last_send_at": latest[0], **GradescopeSender._coverage(db, aid, responses, decisions)}


class GradingService(DrCopiesMixin, MakeupDraftsMixin, QuizDraftsMixin, QualityAuditMixin, FlagResolutionMixin, TrainingMixin, BubbleKeysMixin, TemplateWorkflowMixin, RubricGenerationMixin, RubricQualityMixin, RubricLinksMixin, RubricChoicesMixin, ParticipationMixin, IntakeMixin, AutoPrepareMixin, AutoGradeMixin, AutoFlagReviewMixin, AutoCertifyMixin, AssignmentsMixin, JobsMixin, ReviewMixin, PreparationMixin, LocationChecksMixin, RubricUploadsMixin, AnswerGroupsMixin, ReviewQueueMixin, BatchResetsMixin, ExamVersionsMixin, CloudMixin, AcceptanceMixin, EscalationMixin):
    def __init__(self, exams, manual, templates, inbox):
        self.exams, self.manual, self.templates, self.inbox = exams, manual, templates, inbox
        self.rosters, self.path = exams.rosters, exams.path
        self._health_cache, self._health_lock = None, threading.Lock()
        self.model_catalog = ModelCatalog()
        self._cloud_guard, self._limiters, self._cloud_thread = threading.Lock(), {}, None
        self._inflight = {}  # Cloud call -> its Abort and owner while it is in flight (`cloud._stop_cancelled`).
        self._full_concurrency = False  # The workspace's Full concurrency, as the dispatcher last read it (`cloud.limiter`).
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "frq_grading", migration.stem, migration.read_text(encoding="utf-8"))

    @staticmethod
    def event(db, aid, kind, detail, actor="workspace"):
        db.execute("INSERT INTO frq_events(assignment_id,kind,detail_json,actor,created_at) VALUES(?,?,?,?,?)",
                   (aid, kind, encoded(detail), actor, timestamp()))

    @staticmethod
    def touch(db, aid):
        db.execute("UPDATE frq_assignments SET version=version+1,updated_at=? WHERE id=?", (timestamp(), aid))

    def provider(self, name, settings):
        if name == "glm":
            return GlmOcrProvider()
        if name == "mathpix":
            return MathpixProvider()
        return LocalVlmProvider(settings, name)

    def health(self, refresh=False):
        with self._health_lock:
            if not refresh and self._health_cache and time.monotonic() - self._health_cache[0] < 60:
                return self._health_cache[1]
            settings = self.settings()["settings"]
            results = {}
            for name in ("glm", "mathpix", "local_verifier", "local_grader", "nebius"):
                try:
                    results[name] = self.provider(name, settings).health()
                except (ValueError, OSError):
                    results[name] = {"status": "configuration_error"}
            # Every configured hosted endpoint, by its own name: grading never reasons in vendors.
            results["endpoints"], listings = {}, {}
            for endpoint in settings["endpoints"]:
                if endpoint["enabled"] and (endpoint["kind"] != "dedicated" or settings["dedicated_enabled"]):
                    try:
                        provider, shared = self.cloud_provider(endpoint, 8), (endpoint["base_url"], endpoint["key_env"])
                        if hasattr(provider, "listing"):
                            listings[shared] = listings.get(shared) or provider.listing()
                            results["endpoints"][endpoint["name"]] = provider.health(listings[shared])
                        else:
                            results["endpoints"][endpoint["name"]] = provider.health()
                    except (ValueError, OSError):
                        results["endpoints"][endpoint["name"]] = {"status": "configuration_error"}
            results["checked_at"] = timestamp()
            self._health_cache = (time.monotonic(), results)
            return results

    @staticmethod
    def activity(db, aid=None):
        """Existing batch/preparation metadata, scoped to one term plan when navigating its steps."""
        where, values = (" WHERE assignment_id=?", (aid,)) if aid else ("", ())
        jobs = [dict(r) for r in db.execute("SELECT id,assignment_id,mode,tier,operation,status,estimated_cost,cost,notice,runtime_seconds,created_at,updated_at,request_id LIKE 'auto:%' AS automatic, CASE WHEN json_extract(profile_json,'$.jev_routing_rule') IN ('jev-routing-v1','jev-routing-v2') THEN 'jev_routing' WHEN json_extract(profile_json,'$.grading_workflow')='one-shot-v1' THEN 'one_shot' WHEN json_extract(profile_json,'$.grading_workflow')='tag-team-v1' THEN 'tag_team' ELSE CASE WHEN tier!='' THEN tier ELSE mode END END AS compute FROM frq_jobs" + where + " ORDER BY created_at DESC LIMIT 100", values)]
        preparations = [dict(r) for r in db.execute("SELECT id,assignment_id,kind,status,error,created_at FROM frq_preparations" + where + " ORDER BY created_at DESC LIMIT 100", values)]
        return {"jobs": jobs, "preparations": preparations}

    def overview(self, *, navigation=False, include_health=True):
        # Navigation is a projection of existing term plans and job metadata. It
        # needs neither every assignment's paper checks nor remote health probes.
        # The roster lock is taken one plan at a time, never across the whole walk: the worker and every other page wait for
        # it, and holding it for all of a term's exams made each page load block them for the sum. A plan that already has
        # responses leaves `grading_entry` after one indexed read.
        with connect(self.path) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM frq_assignments ORDER BY updated_at DESC")]
            activity = self.activity(db)
            jobs, preparations = activity['jobs'], activity['preparations']
        plans, waiting, counts = [], [], {}
        settings = None if navigation else self.settings()["settings"]
        for aid in ids:
            with self.rosters.lock(), connect(self.path) as db:
                plan = self.plan(db, aid)
                shared = {}  # this plan's per-read memo: the entry's scan overviews serve the workflow counts below
                plan['entry'] = self.grading_entry(db, plan, shared=shared)
                if not plan['entry']['ready']:
                    # Waiting for Inbox checks only while one of its scans is still in the Inbox: an assignment whose scans were all
                    # deleted there waits for nothing the Inbox shows (2026-09-26: "I click it and there is nothing in the inbox").
                    if plan['entry'].get('pending_scans'):
                        waiting.append(plan)
                    continue
                plans.append(plan)
                if navigation:
                    continue
                # One read of the exam's participants and papers per plan, with one memo, for the listing and the workflow counts.
                event = self.exams._event(db, plan["event_id"])
                participants = self.exams._participants(db, event)
                collection = self.exams._collection(db, event, participants)
                quality = self.rubric_quality_status(db, plan["id"])  # never put on `plan`, nor `shared`: it is serialized
                responses = self._responses(db, plan, event, participants, shared=shared, quality=quality)
                details = {}  # what the counts were made of: its `decisions` are each final grade's, which the Gradescope card compares
                plan["workflow"] = self.workflow_summary(db, plan, responses, context=(event, participants, collection), details=details,
                                                         shared=shared, quality=quality)
                plan["bubble"] = self.bubble_brief(db, plan)
                plan["gradescope"] = gradescope_brief(db, plan["id"], responses, details.get("decisions"))
                # Whether its next batch starts by itself, and why not (additive, as the plan read has it): Home says when grading
                # stopped waiting on the instructor (journey-5, 2026-10-02).
                plan["auto_grade"] = self.auto_grade_state(db, plan["id"], settings)
                for response in responses:
                    state = "approved" if response["state"] == "manual" else response["state"]
                    counts[state] = counts.get(state, 0) + 1
        if navigation:
            return {"assignments": plans, "jobs": jobs, "preparations": preparations, 'waiting_for_inbox': len(waiting), 'waiting_href': waiting_href(waiting),
                    'waiting': waiting_list(waiting)}
        with connect(self.path) as db:
            queued = db.execute("SELECT COUNT(*),MIN(created_at) FROM frq_tasks WHERE status='pending'").fetchone()
            failed = db.execute("SELECT COUNT(*) FROM frq_tasks WHERE status='failed'").fetchone()[0]
        return {"assignments": plans, 'waiting_for_inbox': len(waiting), 'waiting_href': waiting_href(waiting), 'waiting': waiting_list(waiting), "counts": counts, "jobs": jobs, "pending_tasks": queued[0], "oldest_pending_at": queued[1],
                "failed_tasks": failed, "estimated_cloud_cost": sum(j["estimated_cost"] or 0 for j in jobs if j["mode"] == "nebius"),
                "recorded_cloud_cost": round(sum(j["cost"] or 0 for j in jobs), 4),
                "preparations": preparations, "cost_note": "Estimates are made before a batch; recorded cost comes from each request’s token usage or the host’s own figure. Neither is a bill.",
                **({"health": self.health()} if include_health else {})}
