"""Durable cloud calls, the instructor's retry policy and the parallel cloud lane.

Every request that leaves Faculty is first a row in `frq_cloud_calls`. What happens after a failure is
decided by whether the failure is CONFIRMED (instructor's policy of 2026-09-19):

- confirmed failed (the provider answered with an error, reports the job failed, or refused the connection
  before anything was sent): retried automatically, but never before the confirmation is recorded with the
  attempt. The only ceiling is the attempt cap (`max_retries` per response and stage); then the response fails
  and waits for the instructor. A failure a retry cannot fix (no credits, a rejected key) stops at once.
- outcome unknown (sent, then the connection dropped, Faculty restarted or the wait timed out): recovered by
  the provider's durable call ID where one exists; otherwise resent at most `unknown_resends` times.
- a late result of a superseded call is kept as a receipt and never applied, so no response has two grades.

The single local GPU stays serial in the Inbox worker. Cloud requests, polling, result handling and crop
preparation for cloud batches run here in parallel, under their own cross-process lock; catalog writes keep
the existing lifecycle lock, and `frq_steps UNIQUE(task_id,stage,attempt)` makes every result idempotent.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import threading
import time

from werkzeug.exceptions import Conflict
from course_storage import StorageError, timestamp
from rosters import connect

from . import config
from .models import digest, encoded, uid
from .providers import cloud as providers
from .providers.base import Abort, Cancelled, ProviderError, abortable

LOG = logging.getLogger(__name__)
CLOUD_LOCK = "5d2f1c1e-8a53-5c0b-9f0e-6f2f0f7b6c11"
# How many components the dispatcher may run at once, across every batch (requirement, 2026-09-23: run a whole 240-problem test
# batch at once). Each component holds its thread for as long as its model takes, and while it waits for its connection's slot.
MAX_UNITS = 256
# With Full concurrency (a workspace setting) every component starts as soon as it is ready: the batch limit and each connection's
# own limit give way, bounded only by this, the largest batch Faculty accepts (`max_batch_responses`). Threads are made as needed.
FULL_UNITS = 5000
CAPACITY = (429, 503, 529)  # The host is busy, not wrong: wait as it asks; these never use up the attempt cap.
# Asking a host about a job it gave a durable ID can fail too. A host that is busy is waited for as it asks; one that no longer
# holds the job (404/410) or refuses the key (`fixable=False`) cannot tell us anything more, and any other confirmed or unknown
# failure to ask is tried this many times (about five minutes at one question per 15 seconds). Then the job's outcome is UNKNOWN
# and the request is resent only under the unknown-outcome policy (`unknown_resends`), never polled for ever.
POLL_FAILURES = 20
GONE = (404, 410)
# The kinds of run that own cloud calls, in the column order of `frq_cloud_calls`: (owner id, its table, what a refusal calls it). A
# response task (`task_id`) and an endpoint probe (`probe_id`) own calls too, but are not runs. `_inflight` and `_stop_cancelled` list
# their own, the cancellable kinds only: a reply draft and a rubric memory pass have no `cancelled` column.
RUN_OWNERS = (("rubric_check_id", "frq_rubric_quality_checks", "rubric review"), ("rubric_generation_id", "frq_rubric_generations", "rubric draft"),
              ("quality_audit_id", "frq_quality_audits", "quality audit"), ("flag_resolution_id", "frq_flag_resolutions", "flag review"),
              ("quiz_revision_id", "quiz_draft_revisions", "quiz draft"), ("bank_run_id", "question_bank_runs", "question bank run"),
              ("regrade_draft_id", "frq_regrade_drafts", "reply draft"), ("rubric_memory_run_id", "frq_rubric_memory_runs", "rubric memory pass"))
# The queued runs the dispatcher claims, puts back after a restart and fails when one stops unexpectedly: (kind, table, what a stopped
# run says), in the order they are claimed. After the rubric review, one per claim, a waiting rubric draft shares its turn (one per claim,
# within the capacity), as do a quality audit (it reads final grades and changes none), a flag review (it rules on held grades and writes
# only through an ordinary batch), a quiz draft (requirement, 2026-09-28: writing, compiling and checking a quiz, or carrying out its
# approval) and the rebuild of an approved quiz in the current page layout (QUIZZES "REBUILDING AN APPROVED QUIZ"). The question bank, reply
# drafts and rubric memory passes are claimed, put back and failed by their own services (`claim_runs`, `recover_runs`, `fail_run`).
RUN_TABLES = (("rubric_quality", "frq_rubric_quality_checks", "The rubric review stopped unexpectedly. Request a new review."),
              ("rubric_generation", "frq_rubric_generations", "The rubric draft stopped unexpectedly. Draft again."),
              ("quality_audit", "frq_quality_audits", "The quality audit stopped unexpectedly. Run it again."),
              ("flag_resolution", "frq_flag_resolutions", "The flag review stopped unexpectedly. Run it again."),
              ("quiz_revision", "quiz_draft_revisions", "The quiz draft stopped unexpectedly. Try again."),
              ("quiz_approval", "quiz_draft_approvals", "Carrying out the approval stopped unexpectedly. Approve again: every step reuses what was made."),
              ("quiz_rebuild", "quiz_draft_rebuilds", "The rebuild stopped unexpectedly. Rebuild again: every step reuses what was made."))
RUN_FAILURES = {kind: (table, text) for kind, table, text in RUN_TABLES}


class RetryLater(Exception):
    """Put the task back in the cloud queue; the confirmation (or the durable call ID) is already recorded."""
    def __init__(self, seconds, reason):
        super().__init__(reason)
        self.seconds, self.reason = seconds, reason


class Limiter:
    """Requests in flight per endpoint, halved on a rate limit and restored one step per twenty successes. `maximum` None is no
    limit at all (Full concurrency) until the host says it is busy: then the limit is half of what was in flight, and it grows back.

    `per_minute` also paces how many requests START in any sixty seconds, for a provider that counts requests rather than
    connections: eight Mathpix reads in flight at two seconds each are 240 a minute, which met the account's limit of 200.
    """
    def __init__(self, maximum, per_minute=0):
        self.maximum = self.limit = None if maximum is None else max(1, maximum)
        self.per_minute, self.started = max(0, int(per_minute or 0)), deque()
        self.active = self.successes = self.peak = 0
        self.changed = threading.Condition()

    def _paced(self, now):
        while self.started and now - self.started[0] >= 60:
            self.started.popleft()
        return bool(self.per_minute) and len(self.started) >= self.per_minute

    @contextmanager
    def slot(self, abort=None):
        with self.changed:
            while (self.limit is not None and self.active >= self.limit) or self._paced(time.monotonic()):
                if abort is not None:
                    abort.check()  # Cancelled while waiting for a slot: it never leaves.
                self.changed.wait(1)
            self.active += 1
            self.started.append(time.monotonic())
            self.peak = max(self.peak, self.active)
        try:
            yield
        finally:
            with self.changed:
                self.active -= 1
                self.changed.notify_all()

    def rate_limited(self):
        with self.changed:
            self.limit, self.successes = max(1, (self.active if self.limit is None else self.limit) // 2), 0

    def succeeded(self):
        with self.changed:
            self.successes += 1
            if self.successes >= 20 and self.limit is not None and (self.maximum is None or self.limit < self.maximum):
                self.limit, self.successes = self.limit + 1, 0
                self.changed.notify_all()


def endpoint_kind(provider):
    return getattr(provider, "kind", "") if provider is not None else ""


def later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class CloudMixin:
    @staticmethod
    def is_cloud(job):
        """The cloud modes: the original Nebius mode and the tiers, which are stored beside it."""
        return job["mode"] == "nebius"

    @staticmethod
    def compute(job):
        """The scheme a batch ran under: 'local', 'hybrid' or the Cloud tier, or the name of a removed scheme (`config.REMOVED_SCHEMES`)
        for a batch from before 2026-09-23, which stays readable and never runs again."""
        profile = job.get("profile") or json.loads(job.get("profile_json") or "{}")
        return config.historical_scheme(profile) or job.get("tier") or job["mode"]

    @contextmanager
    def cloud_lock(self):
        """One cloud dispatcher across processes. Holding it proves that calls still 'submitted' lost their process."""
        storage = self.inbox.storage
        if storage:
            with ExitStack() as held:
                try:
                    held.enter_context(storage.lock(CLOUD_LOCK, blocking=False))
                except StorageError as error:
                    # The storage manager says "busy" as a StorageError; any other storage failure stays what it is.
                    if isinstance(error.__cause__, BlockingIOError):
                        raise Conflict("Another cloud dispatcher is already running.") from error
                    raise
                yield
            return
        import fcntl
        descriptor = os.open(self.path.parent / ".frq-cloud.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Conflict("Another cloud dispatcher is already running.") from error
            yield
        finally:
            os.close(descriptor)

    def cloud_provider(self, endpoint, timeout=180):
        """The one seam between grading and any vendor; the scratch app and tests put fakes here."""
        return providers.provider(endpoint, timeout)

    def limiter(self, endpoint):
        # Full concurrency (read from the workspace each dispatcher round) lifts the connection's own limit; its per-minute pace stays.
        # A bundling API connection's limit counts requests, each carrying up to `bundle_size` responses, and every response waiting
        # to join one holds a slot here: so that many are let in. (A Claude subscription connection's `max_inflight` already counts them.)
        bundle = 1 if endpoint["kind"] in config.SUBSCRIPTION_KINDS or endpoint.get("batch_api") else max(1, int(endpoint.get("bundle_size") or 1))
        maximum = None if getattr(self, "_full_concurrency", False) else min(FULL_UNITS, endpoint["max_inflight"] * bundle)
        with self._cloud_guard:
            found = self._limiters.get(endpoint["name"])
            if not found or found.maximum != maximum or found.per_minute != int(endpoint.get("per_minute") or 0):
                found = self._limiters[endpoint["name"]] = Limiter(maximum, endpoint.get("per_minute"))
            return found

    # One request, under the retry policy -------------------------------------------------------------------------
    def cloud_call(self, owner, role, endpoint, request, settings):
        """owner: {task_id|probe_id|rubric_check_id|..., job_id, step, task_attempt, tier}. Returns the provider result or raises."""
        # A hosted model's request has NO time limit (requirement, 2026-09-23). It is not streamed, so its answer arrives only when the
        # model has finished writing: MiMo V2.6 Pro, arbitrating at high reasoning, wrote 35-45 tokens a second under load and its
        # rulings ran to 9,000 tokens, so every one past about 7,000 timed out at the workspace's 180 seconds, and so did each resend.
        # Keepalive notices a dead connection (`base.KEEPALIVE`); Mathpix, Jev and the dedicated GPU keep the workspace's timeout.
        provider = self.cloud_provider(endpoint, None if endpoint["kind"] in ("hosted", *config.SUBSCRIPTION_KINDS) else settings["timeout"])
        body_sha = digest({"prompt": request["prompt"], "payload": request["payload"], "images": [digest(i) for i in request.get("images") or []], "endpoint": endpoint,
                           # A drafting request with tools (`providers/tools.py`) is another request than the same one without them.
                           **({"tools": request["tools"].fingerprint} if request.get("tools") else {})})
        key = next((name, owner[name]) for name in ('task_id', *(column for column, _, _ in RUN_OWNERS), 'probe_id') if owner.get(name))
        with self.rosters.lock(), connect(self.path) as db:
            rows = [dict(r) for r in db.execute(f"SELECT * FROM frq_cloud_calls WHERE {key[0]}=? AND step=? AND task_attempt=? ORDER BY rowid", (key[1], owner["step"], owner["task_attempt"]))]
        # An owner asked in several requests that has to wait comes back whole: the answers it already bought are replayed from their
        # receipts, never bought again.
        if any(owner.get(column) for column, _, _ in RUN_OWNERS):
            for row in reversed(rows):
                if row['state'] == 'completed' and row['request_sha256'] == body_sha:
                    saved = json.loads(row['receipt_json'] or '{}').get('result')
                    if saved is not None:
                        return saved  # Settlement survives a restart before publishing the reading/routing/check.
        # 1. A durable call ID settles an interrupted request without sending anything again.
        for row in rows:
            if row["state"] in ("submitted", "unknown") and row["provider_call_id"] and not json.loads(row["receipt_json"] or "{}").get("abandoned"):
                found = self._recover(provider, row, endpoint, settings, owner, sum(r["state"] == "unknown" for r in rows))
                if found is not None:
                    return found
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            waiting = None
            for row in rows:
                if row["state"] == "submitted" and not row["provider_call_id"]:
                    # We hold the cloud lock and this task was handed to us again: the process that sent it is gone.
                    waited = (datetime.now(timezone.utc) - datetime.fromisoformat(row["submitted_at"])).total_seconds()
                    if waited < settings["timeout"]:
                        waiting = max(waiting or 0, settings["timeout"] - waited + 1)
                        continue
                    db.execute("UPDATE frq_cloud_calls SET state='unknown',error=?,finished_at=? WHERE id=?", ("Faculty restarted while this request was in flight.", timestamp(), row["id"]))
            # What is now known about interrupted requests is kept even when a check below refuses to send: left 'submitted', a
            # lost request would count as in flight for good.
            db.commit()
            if waiting is not None:
                raise RetryLater(waiting, "An interrupted request may still be running at the host; waiting for its own timeout before resending.")
            db.execute("BEGIN IMMEDIATE")
            rows = [dict(r) for r in db.execute(f"SELECT * FROM frq_cloud_calls WHERE {key[0]}=? AND step=? AND task_attempt=? ORDER BY rowid", (key[1], owner["step"], owner["task_attempt"]))]
            failures = sum(r["state"] == "failed" and json.loads(r["confirmation_json"] or "{}").get("status") not in CAPACITY for r in rows)
            # A subscription's usage limit is a known wait until its reset, not a host that stays busy: it never reaches the busy ceiling
            # (requirement, 2026-09-28), however many hours the reset takes.
            busy = sum(r["state"] == "failed" and not json.loads(r["confirmation_json"] or "{}").get("usage_limit") for r in rows) - failures
            unknowns = sum(r["state"] == "unknown" for r in rows)
            if failures >= max(1, settings["max_retries"]):
                raise ProviderError(f"This request failed {failures} times with a confirmed provider error ({rows[-1]['error']}). It stopped at the attempt cap; review it, then retry when ready.")
            if busy >= max(1, settings["max_retries"]) * 10:
                raise ProviderError("The host stayed rate limited or without capacity for every attempt. Retry when it has capacity again.")
            if unknowns > settings["unknown_resends"]:
                raise ProviderError(f"The outcome of this request is still unknown after {settings['unknown_resends']} automatic resends. Nothing was applied; retry it when ready.")
            if owner.get("job_id"):
                job = db.execute("SELECT cost,cancel_requested FROM frq_jobs WHERE id=?", (owner["job_id"],)).fetchone()
                if job["cancel_requested"]:
                    raise Cancelled("This batch was cancelled; nothing further was sent.")
                # No spending cap here (requirement, 2026-09-23): the OpenRouter and Vercel accounts carry the limits, and a request a
                # gateway refuses for credit (402) fails at once, never resent. Costs are still recorded on every call and the batch.
            for column, table, noun in RUN_OWNERS:
                if owner.get(column):
                    found = db.execute(f"SELECT status FROM {table} WHERE id=?", (owner[column],)).fetchone()
                    if not found or found["status"] != "running":
                        raise ProviderError(f"This {noun} is no longer running; nothing further was sent.")
            # At temperature 0 a request that ran away into repetition does so again (seen live: the same reading failed five times
            # the same way). An answer that was refused is therefore asked again with a little sampling; the receipt says so.
            stages = (owner["step"], "grading") if owner["step"] == "grade:primary" else (owner["step"],)
            refused = db.execute(f"SELECT COUNT(*) FROM frq_rejected_outputs WHERE task_id=? AND attempt=? AND stage IN ({','.join('?' * len(stages))})",
                                 (owner["task_id"], owner["task_attempt"], *stages)).fetchone()[0] if owner.get("task_id") else 0
            # A caller that asks again itself (the rubric check, a rubric draft) says so; a response task counts its refused answers.
            request = {**request, "reask": request.get("reask") or refused}
            if (endpoint["kind"] in config.SUBSCRIPTION_KINDS or (endpoint.get("bundle_size") or 1) > 1) and owner.get("task_id") and owner.get("job_id"):
                # A bundling connection's call carries several responses at once (`providers.bundles`), and ONLY responses to the same
                # question on the same exam version (requirement, 2026-09-27), of this batch and at this step. A question row belongs to
                # one version already; the version is named in the key as well, so two versions can never share a call. This row is
                # still this response's own.
                question = db.execute("""SELECT r.question_id,q.exam_version FROM frq_tasks t JOIN frq_responses r ON r.id=t.response_id
                    JOIN frq_questions q ON q.id=r.question_id WHERE t.id=?""", (owner["task_id"],)).fetchone()
                if question:
                    request = {**request, "bundle": {"key": [owner["job_id"], question[0], question[1], owner["step"]]}}
            call_id, started = uid(), time.monotonic()
            # The durable record exists before the request leaves.
            db.execute("""INSERT INTO frq_cloud_calls(id,task_id,probe_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,
                resource_profile,request_sha256,prompt,state,submitted_at,rubric_check_id,rubric_generation_id,quality_audit_id,flag_resolution_id,quiz_revision_id,bank_run_id,regrade_draft_id,rubric_memory_run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'submitted',?,?,?,?,?,?,?,?,?)""",
                (call_id, owner.get("task_id"), owner.get("probe_id"), owner.get("job_id"), owner["step"], owner["task_attempt"], role,
                 owner.get("tier", ""), endpoint["name"], provider.kind, endpoint["model"], endpoint["lineage"], endpoint["profile"], body_sha, request["prompt"], timestamp(), *[owner.get(column) for column, _, _ in RUN_OWNERS]))
        limiter, abort = self.limiter(endpoint), Abort()
        # While it is in flight, cancelling its owner closes its connection (`_stop_cancelled`, within a second or two).
        with self._cloud_guard:
            self._inflight[call_id] = {"abort": abort, **{key: owner.get(key) for key in ("job_id", "rubric_check_id", "rubric_generation_id", "quality_audit_id", "flag_resolution_id", "quiz_revision_id", "bank_run_id")}}
        try:
            with limiter.slot(abort), abortable(abort):
                outcome = provider.submit(request)
        except Cancelled as stopped:
            self._settle_cancelled(call_id, stopped, time.monotonic() - started, owner)
            raise
        except ProviderError as error:
            self._settle_failure(call_id, error, time.monotonic() - started, limiter, unknowns, settings, owner, provider)
            raise AssertionError("unreachable")  # _settle_failure always raises.
        finally:
            with self._cloud_guard:
                self._inflight.pop(call_id, None)
        if outcome["state"] == "submitted":
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("UPDATE frq_cloud_calls SET provider_call_id=?,receipt_json=? WHERE id=?", (outcome["call_id"], encoded({"metadata": outcome.get("metadata", {})}), call_id))
            raise RetryLater(5, "The provider accepted the job; its durable ID is recorded and it will be collected.")
        limiter.succeeded()
        return self._settle_success(call_id, provider, outcome["result"], time.monotonic() - started, owner)

    def _settle_success(self, call_id, provider, result, seconds, owner):
        usage = result["metadata"].get("usage") or {}
        cost, basis = provider.cost(usage)
        result['metadata'] = {**result['metadata'], 'cloud_call_id': call_id, 'cost': cost, 'cost_basis': basis, 'seconds': round(seconds, 3)}
        retain = bool(any(owner.get(column) for column, _, _ in RUN_OWNERS)
                      or result['metadata'].get('api_fallback'))  # GRADING CLAUDE API FALLBACK: keep the reason for a billable retry.
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE frq_cloud_calls SET state='completed',actual_model=?,upstream=?,usage_json=?,cost=?,cost_basis=?,prompt_sha256=?,finished_at=?,seconds=?,
                receipt_json=CASE WHEN ? THEN ? ELSE receipt_json END WHERE id=?""",
                (result["metadata"].get("reported_model"), result["metadata"].get("upstream"), encoded(usage), cost, basis, result["metadata"].get("prompt_sha256", ""), timestamp(), seconds,
                 retain, encoded({'result': result}) if retain else None, call_id))
            if owner.get("job_id") and cost:
                db.execute("UPDATE frq_jobs SET cost=cost+? WHERE id=?", (cost, owner["job_id"]))
        # Identifiers, timings and token counts only: never a payload, an image or a provider body.
        named = getattr(provider, "endpoint", None) if isinstance(getattr(provider, "endpoint", None), dict) else {}
        LOG.info("cloud call completed call=%s job=%s step=%s endpoint=%s model=%s upstream=%s prompt=%s seconds=%.2f tokens=%s cost=%s", call_id, owner.get("job_id"),
                 owner.get("step"), result["metadata"].get("endpoint") or named.get("name"), result["metadata"].get("reported_model") or named.get("model"), result["metadata"].get("upstream"),
                 result["metadata"].get("prompt"), seconds, usage.get("total_tokens"), cost)
        return result

    def _settle_cancelled(self, call_id, stopped, seconds, owner):
        """A request whose owner was cancelled: recorded as cancelled, never resent, and never applied."""
        note = ("Stopped when it was cancelled: the connection was closed while the model was answering. A host that supports "
                "cancellation stops the model there; another may still bill what it produced.") if stopped.sent else "Cancelled before it was sent."
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE frq_cloud_calls SET state='cancelled',error=?,finished_at=?,seconds=? WHERE id=? AND state='submitted'", (note, timestamp(), seconds, call_id))
        LOG.info("cloud call cancelled call=%s job=%s step=%s sent=%s seconds=%.2f", call_id, owner.get("job_id"), owner.get("step"), stopped.sent, seconds)

    def _stop_cancelled(self):
        """Close the connections of requests whose batch, rubric review or rubric draft was cancelled. The dispatcher asks about once
        a second; the cancel itself is a catalog write, often from another process (the web app), so this is how it is heard."""
        with self._cloud_guard:
            flying = dict(self._inflight)
        if not flying:
            return 0
        wanted = {key: {v[key] for v in flying.values() if v.get(key)} for key in ("job_id", "rubric_check_id", "rubric_generation_id", "quality_audit_id", "flag_resolution_id", "quiz_revision_id", "bank_run_id")}
        stopped = {}
        with connect(self.path) as db:
            for key, table, test in (("job_id", "frq_jobs", "cancel_requested=1"), ("rubric_check_id", "frq_rubric_quality_checks", "cancelled=1"),
                                     ("rubric_generation_id", "frq_rubric_generations", "cancelled=1"), ("quality_audit_id", "frq_quality_audits", "cancelled=1"),
                                     ("flag_resolution_id", "frq_flag_resolutions", "cancelled=1"), ("quiz_revision_id", "quiz_draft_revisions", "cancelled=1"),
                                     ("bank_run_id", "question_bank_runs", "cancelled=1")):
                ids = sorted(wanted[key])
                stopped[key] = {row[0] for row in db.execute(f"SELECT id FROM {table} WHERE {test} AND id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
        closed = 0
        for entry in flying.values():
            if any(entry.get(key) in stopped[key] for key in stopped):
                entry["abort"].stop("It was cancelled while this request was in flight; its connection was closed.")
                closed += 1
        return closed

    def _settle_failure(self, call_id, error, seconds, limiter, unknowns, settings, owner, provider=None):
        """Record what is known FIRST; only then may anything be retried."""
        confirmed = error.confirmed is not False
        # A host bills for an answer it returned even when that answer is refused here (malformed, truncated): the spending is real.
        usage = error.metadata.get("usage") if isinstance(error.metadata.get("usage"), dict) else {}
        cost, basis = provider.cost(usage) if provider is not None and usage else (None, "")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE frq_cloud_calls SET state=?,confirmation_json=?,error=?,receipt_json=?,usage_json=?,cost=?,cost_basis=?,actual_model=?,upstream=?,finished_at=?,seconds=? WHERE id=?", ("failed" if confirmed else "unknown",
                encoded(error.confirmation) if confirmed else None, str(error), encoded({"raw": error.raw, "metadata": error.metadata}) if error.raw is not None else None,
                encoded(usage), cost, basis, error.metadata.get("reported_model"), error.metadata.get("upstream"), timestamp(), seconds, call_id))
            if owner.get("job_id") and cost:
                db.execute("UPDATE frq_jobs SET cost=cost+? WHERE id=?", (cost, owner["job_id"]))
            if error.raw is not None and owner.get("task_id"):
                # A malformed or truncated answer never becomes a score; its private receipt joins the response's audit.
                db.execute("INSERT INTO frq_rejected_outputs VALUES(?,?,?,?,?,?,?)", (uid(), owner["task_id"], owner["step"], owner["task_attempt"], encoded(error.raw), encoded(error.metadata), timestamp()))
        LOG.warning("cloud call %s call=%s job=%s step=%s status=%s seconds=%.2f reason=%s", "failed (confirmed)" if confirmed else "outcome unknown", call_id,
                    owner.get("job_id"), owner.get("step"), error.status, seconds, error)
        if not confirmed:
            if unknowns + 1 > settings["unknown_resends"]:
                raise ProviderError(f"The outcome of this request is still unknown after {settings['unknown_resends']} automatic resends. Nothing was applied; retry it when ready.") from None
            raise RetryLater(1, "The outcome of the request is unknown; it is resent.") from None
        if not error.fixable:
            # No credits, a refused key, a missing key variable or an exhausted output budget: the same request fails the same way.
            # The receipt of a refused answer is already with its call and the response's rejected outputs, so it is not passed on to be asked again.
            hint = (" Retrying cannot fix this: check the endpoint’s key and credits on the server, then retry the batch."
                    if error.raw is None and error.status != 400 and not error.confirmation.get("stream_duration") else "")
            raise ProviderError(f"{error}{hint}", fixable=False, confirmation=error.confirmation) from None
        if error.status in CAPACITY:
            # A subscription's usage limit pauses its connection until the reset (`providers.agent_sdk`, `codex_sdk`); halving what may be in
            # flight as well would shrink its bundles to one response each for hundreds of calls after the reset.
            # A subscription connection's busy service is waited for too, without halving (its sessions already bound its calls).
            if not error.confirmation.get("usage_limit") and not (error.confirmation.get("capacity") and endpoint_kind(provider) in config.SUBSCRIPTION_KINDS):
                limiter.rate_limited()
            raise RetryLater(error.retry_after if error.retry_after is not None else 10, "The host is rate limited or out of capacity; waiting as it asked.") from None
        raise RetryLater(2, "A confirmed provider failure was recorded; the request is retried.") from None

    def _recover(self, provider, row, endpoint, settings, owner, unknowns):
        """Ask the provider about a call it gave a durable ID. Returns a result, None to send a new request, or raises RetryLater to keep waiting."""
        receipt = json.loads(row["receipt_json"] or "{}")
        try:
            found = provider.poll(row["provider_call_id"], receipt.get("metadata", {}))
        except ProviderError as error:
            if error.confirmed is None:
                raise
            if error.raw is not None:
                # The host finished the job and its answer was refused here (malformed, or cut off by its budget): a confirmed failure
                # with its receipt, exactly like a refused answer to a direct request, so the attempt cap and the budget rule hold.
                self._settle_failure(row["id"], error, 0, self.limiter(endpoint), unknowns, settings, owner, provider)
            if row["state"] == "unknown":
                return None  # Already settled as unknown and counted as such: asking was a chance to recover it, not a reason to wait.
            if error.status in CAPACITY:
                raise RetryLater(error.retry_after if error.retry_after is not None else 15, "The host is busy; a recorded job is asked about again as it asked.") from None
            asked = int(receipt.get("poll_failures", 0)) + 1
            if error.fixable and error.status not in GONE and asked < POLL_FAILURES:
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("UPDATE frq_cloud_calls SET receipt_json=? WHERE id=? AND state='submitted'", (encoded({**receipt, "poll_failures": asked}), row["id"]))
                raise RetryLater(15, "The provider could not be asked about a recorded job; it is asked again.") from None
            # The host no longer holds the job, refuses to say, or kept failing to answer: its ID can tell nothing more. What became
            # of the request is UNKNOWN (it may have run and been billed), so it is resent only under that policy, never polled for ever.
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("UPDATE frq_cloud_calls SET state='unknown',error=?,receipt_json=?,finished_at=? WHERE id=? AND state='submitted'",
                    (f"The provider could not say what became of this job ({error}). Its outcome is unknown, and the host may have billed it.",
                     encoded({**receipt, "poll_failures": asked, "abandoned": True}), timestamp(), row["id"]))
            return None
        if found["state"] == "submitted":
            raise RetryLater(10, "The provider is still working on a recorded job.")
        if found["state"] == "completed":
            return self._settle_success(row["id"], provider, found["result"], 0, owner)
        # A job the host failed, let expire or cancelled on its own is a CONFIRMED failure: it counts toward the attempt cap, so a host
        # that keeps cancelling cannot have it resent without end. (A batch the instructor cancelled is never claimed again.)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE frq_cloud_calls SET state='failed',confirmation_json=?,error=?,finished_at=? WHERE id=?",
                (encoded(found.get("confirmation", {})), f"The provider reports this job as {found['state']}.", timestamp(), row["id"]))
        return None

    def next_lane(self, job, settings, stage, keys, crops, choice_pending=False):
        """Who runs a task's next component: '' the single local GPU worker, 'cloud' the parallel dispatcher. A multiple-choice read
        (`choice_pending`, grading/choice.py) is a cloud request, never a GPU turn."""
        if not self.is_cloud(job):
            return ""
        if choice_pending:
            return "cloud"
        if stage == "recognizing" and settings["glm_enabled"] and job["operation"] == "recognize" and any(f"ocr:{i}:glm" not in keys for i in range(crops)):
            return ""  # GLM-OCR uses the local GPU, which stays serial.
        return "cloud"

    def process_cloud(self):
        """Run cloud work that is ready now, in parallel. Returns whether anything ran. Tests and `worker --once` call this inline."""
        try:
            with self.cloud_lock():
                return self._cloud_rounds(threading.Event())
        except Conflict:
            return False  # Another process (the service) holds the lane: its dispatcher does this work.

    def cloud_worker(self, stop):
        """The dispatcher beside the Inbox worker loop. It never takes the local GPU's execution lock."""
        self._cloud_thread = threading.current_thread()
        while not stop.is_set():
            try:
                with self.cloud_lock():
                    while not stop.is_set():
                        if not self._cloud_rounds(stop) and self.inbox.wake.wait(2):
                            # Every setter means "look again now", and nothing clears the event for us: this dispatcher is its
                            # only waiter. Left set, every later wait returned at once and rounds ran back to back, each taking
                            # the lifecycle lock and a write transaction. Cleared BEFORE the next round, so a wake-up set
                            # during that round still ends the following wait.
                            self.inbox.wake.clear()
            except Conflict:
                stop.wait(10)
            except Exception:
                LOG.exception("The cloud grading dispatcher stopped a round; it starts again")
                stop.wait(5)

    def _cloud_rounds(self, stop):
        worked, pool, running = False, ThreadPoolExecutor(max_workers=FULL_UNITS, thread_name_prefix="frq-cloud"), {}
        try:
            self._cloud_recover()
            while not stop.is_set():
                self._stop_cancelled()
                settings = self.settings()["settings"]
                self._full_concurrency = bool(settings.get("full_concurrency"))
                self._collect_superseded(settings)
                # A response waiting to join a bundle is a component too, so with bundling these limits count REQUESTS: they are
                # multiplied by the most responses per call any enabled connection takes, and 256 bundled requests can fill.
                bundle = max([1, *((e.get("bundle_size") or 1) for e in settings["endpoints"] if e["enabled"] and e["kind"] in ("hosted", *config.SUBSCRIPTION_KINDS))])
                capacity = (FULL_UNITS if self._full_concurrency else min(FULL_UNITS, MAX_UNITS * bundle, settings["concurrency"] * 2 * bundle)) - len(running)
                for unit in (self._cloud_claim(capacity) if capacity > 0 else []):
                    running[pool.submit(self._cloud_unit, *unit)] = unit
                    worked = True
                if not running:
                    break
                done, _ = wait(running, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    running.pop(future)
                    future.result()
            return worked
        finally:
            pool.shutdown(wait=True)

    def _collect_superseded(self, settings):
        """A few late results per round, kept as receipts. They are never applied: their component already has its result."""
        with self.rosters.lock(), connect(self.path) as db:
            rows = [dict(r) for r in db.execute("""SELECT c.*,j.profile_json FROM frq_cloud_calls c JOIN frq_jobs j ON j.id=c.job_id
                WHERE c.state='superseded' AND c.provider_call_id IS NOT NULL AND c.receipt_json IS NULL ORDER BY c.rowid LIMIT 4""")]
        for row in rows:
            try:
                found = self.cloud_provider(config.named_endpoint(json.loads(row["profile_json"]), row["endpoint"]), 30).poll(row["provider_call_id"], {})
            except (ProviderError, ValueError) as error:
                found = {"state": "unavailable", "error": str(error)}
            if found["state"] == "submitted":
                continue
            receipt = {"late_result": found["result"]["raw"], "applied": False} if found["state"] == "completed" else {"late_state": found["state"], "applied": False}
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("UPDATE frq_cloud_calls SET receipt_json=? WHERE id=? AND state='superseded' AND receipt_json IS NULL", (encoded(receipt), row["id"]))

    def _cloud_recover(self):
        """We hold the cloud lock: anything still 'running' in this lane lost its dispatcher. Its calls decide what happens next."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            # A cancelled batch is never claimed again. Putting its interrupted work back to 'pending' left it there for good,
            # with its requests 'submitted' for ever and the Grade step counting it as queued (seen 2026-09-20: 22 tasks and 19
            # open reviewer calls of two cancelled batches, after a restart minutes after each cancellation). Its work is
            # settled here instead: nothing is resent, and a request that had left keeps its row with an outcome nobody saw.
            stranded = db.execute("""SELECT t.id,t.job_id,j.assignment_id FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id
                WHERE t.lane='cloud' AND t.status IN ('running','pending') AND j.cancel_requested=1""").fetchall()
            for task in stranded:
                db.execute("UPDATE frq_tasks SET status='cancelled',updated_at=? WHERE id=?", (timestamp(), task["id"]))
                db.execute("UPDATE frq_cloud_calls SET state='unknown',error=?,finished_at=? WHERE task_id=? AND state='submitted'",
                    ("Faculty restarted while this request was in flight and its batch had been cancelled: nothing was sent again. Its outcome is unknown, and the host may have billed it.", timestamp(), task["id"]))
            # A request can also outlive its task: interrupted by a restart, put back to wait for its own timeout, and then
            # cancelled with its batch before it was ever collected (seen 2026-09-21: 18 requests 'submitted' for good).
            db.execute("UPDATE frq_cloud_calls SET state='unknown',error=?,finished_at=? WHERE state='submitted' AND task_id IN (SELECT id FROM frq_tasks WHERE status='cancelled')",
                ("Faculty restarted while this request was in flight and its batch had been cancelled: nothing was sent again. Its outcome is unknown, and the host may have billed it.", timestamp()))
            # And a request whose response had already failed or finished: nothing will ever collect it (seen 2026-09-24: out of file
            # handles, a component failed while recording its own request, and 10 requests stayed 'submitted' through the restart).
            db.execute("UPDATE frq_cloud_calls SET state='unknown',error=?,finished_at=? WHERE state='submitted' AND task_id IN (SELECT id FROM frq_tasks WHERE status IN ('failed','complete'))",
                ("Faculty restarted while this request was in flight and its response was no longer waiting for it: nothing was sent again. Its outcome is unknown, and the host may have billed it.", timestamp()))
            for jid, aid in {(task["job_id"], task["assignment_id"]) for task in stranded}:
                self.event(db, aid, "cancelled_work_settled", {"job_id": jid, "tasks": sum(task["job_id"] == jid for task in stranded)}, "worker")
                self._finish_job(db, jid)
            db.execute("UPDATE frq_tasks SET status='pending',updated_at=? WHERE status='running' AND lane='cloud'", (timestamp(),))
            db.execute("UPDATE frq_endpoint_probes SET status='pending',updated_at=? WHERE status='running'", (timestamp(),))
            for _, table, _ in RUN_TABLES:
                db.execute(f"UPDATE {table} SET status='pending',updated_at=? WHERE status='running'", (timestamp(),))
            if getattr(self, 'question_bank', None) is not None:
                self.question_bank.recover_runs(db)
            if getattr(self, 'regrade_drafts', None) is not None:
                self.regrade_drafts.recover_runs(db)
            if getattr(self, 'rubric_memory', None) is not None:
                self.rubric_memory.recover_runs(db)

    def _cloud_claim(self, capacity):
        now = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            units = []
            def claim_quality():
                for row in db.execute("SELECT * FROM frq_rubric_quality_checks WHERE status='pending' AND not_before<=? ORDER BY updated_at,rowid LIMIT 1", (now,)).fetchall():
                    db.execute("UPDATE frq_rubric_quality_checks SET status='running',updated_at=? WHERE id=?", (now, row['id']))
                    units.append(('rubric_quality', dict(row), None))
                # The rest of `RUN_TABLES` shares the turn: one of each per claim, within the capacity.
                for kind, table, _ in RUN_TABLES[1:]:
                    for row in db.execute(f"SELECT * FROM {table} WHERE status='pending' AND not_before<=? ORDER BY updated_at,rowid LIMIT ?", (now, int(capacity > len(units)))).fetchall():
                        db.execute(f"UPDATE {table} SET status='running',updated_at=? WHERE id=?", (now, row['id']))
                        units.append((kind, dict(row), None))
                # A course's question bank (AGENTS.md "Question bank"): tagging questions or reading a PDF's questions. One per claim.
                if getattr(self, 'question_bank', None) is not None:
                    units.extend(self.question_bank.claim_runs(db, now, int(capacity > len(units))))
                # A regrade request's reply drafted for the instructor to edit (REGRADES "DRAFT"). One per claim.
                if getattr(self, 'regrade_drafts', None) is not None:
                    units.extend(self.regrade_drafts.claim_runs(db, now, int(capacity > len(units))))
                # A pass that writes rubric memories from a certified assignment (GRADING "RUBRIC MEMORIES"). One per claim.
                if getattr(self, 'rubric_memory', None) is not None:
                    units.extend(self.rubric_memory.claim_runs(db, now, int(capacity > len(units))))
            # Reserve other capacity for responses; when only one slot opens, alternate priority.
            quality_first = capacity > 1 or getattr(self, '_quality_next', True)
            self._quality_next = not getattr(self, '_quality_next', True)
            if capacity > 0 and quality_first:
                claim_quality()
            for row in db.execute("SELECT * FROM frq_endpoint_probes WHERE status='pending' ORDER BY rowid LIMIT ?", (max(0, capacity - len(units)),)).fetchall():
                db.execute("UPDATE frq_endpoint_probes SET status='running',updated_at=? WHERE id=?", (now, row["id"]))
                units.append(("probe", dict(row), None))
            # Responses that are furthest along go first, so
            # finished proposals arrive from the first minutes and a cancelled batch leaves whole responses: seen live, stage by
            # stage across 650 papers meant that nothing could be reviewed for fifty minutes. Within one stage, the response with
            # more of its steps done goes first (2026-09-24): ordered by stage alone, a 2,169-response batch cut every crop before
            # any response finished cutting, because a response that had just cut one went to the back of the queue, and for
            # minutes nothing seemed to move. Among responses equally far along, one question's go together (2026-09-27), so a Claude
            # subscription connection finds them at the same step and bundles them (`providers.agent_sdk`); other hosts are unaffected.
            for row in db.execute("""SELECT t.* FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id WHERE t.status='pending' AND t.lane='cloud' AND j.cancel_requested=0
                    AND t.not_before<=?
                    ORDER BY CASE t.stage WHEN 'scoring' THEN 0 WHEN 'grading' THEN 1 WHEN 'reconciling' THEN 2 WHEN 'recognizing' THEN 3 ELSE 4 END,
                    (SELECT COUNT(*) FROM frq_steps s WHERE s.task_id=t.id) DESC,
                    (SELECT r.question_id FROM frq_responses r WHERE r.id=t.response_id),
                    t.updated_at,t.rowid LIMIT ?""", (now, max(0, capacity - len(units)))).fetchall():
                task = dict(row)
                job = dict(db.execute("SELECT * FROM frq_jobs WHERE id=?", (task["job_id"],)).fetchone())
                db.execute("UPDATE frq_tasks SET status='running',updated_at=? WHERE id=?", (now, task["id"]))
                db.execute("UPDATE frq_jobs SET status='running',updated_at=? WHERE id=? AND status='pending'", (now, job["id"]))
                units.append(("task", task, job))
            if capacity > len(units) and not quality_first:
                claim_quality()
            return units

    def _cloud_unit(self, kind, item, job):
        try:
            if kind == "task":
                self.run_claimed(item, job)
            elif kind == 'rubric_quality':
                self.run_rubric_quality(item)
            elif kind == 'rubric_generation':
                self.run_rubric_generation(item)
            elif kind == 'quality_audit':
                self.run_quality_audit(item)
            elif kind == 'flag_resolution':
                self.run_flag_resolution(item)
            elif kind == 'quiz_revision':
                self.run_quiz_revision(item)
            elif kind == 'quiz_approval':
                self.run_quiz_approval(item)
            elif kind == 'quiz_rebuild':
                self.run_quiz_rebuild(item)
            elif kind == 'bank_run':
                self.question_bank.run(item)
            elif kind == 'regrade_draft':
                self.regrade_drafts.run(item)
            elif kind == 'rubric_memory':
                self.rubric_memory.run(item)
            else:
                self.run_probe(item)
        except Cancelled:
            # Cancelled with its owner: the owner's own cancel already settled its status (a rubric review or draft), or it is
            # settled here. Nothing is retried and nothing is logged as a fault.
            with self.rosters.lock(), connect(self.path) as db:
                if kind == "task":
                    db.execute("UPDATE frq_tasks SET status='cancelled',updated_at=? WHERE id=? AND status IN ('running','pending')", (timestamp(), item["id"]))
                    self._finish_job(db, item["job_id"])
        except Exception:
            LOG.exception("A cloud grading component stopped unexpectedly")
            with self.rosters.lock(), connect(self.path) as db:
                if kind == "task":
                    db.execute("UPDATE frq_tasks SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'",
                               ("This component stopped unexpectedly. Its earlier components are retained; retry it when ready.", timestamp(), item["id"]))
                    self._finish_job(db, item["job_id"])
                elif kind in RUN_FAILURES:
                    table, text = RUN_FAILURES[kind]
                    db.execute(f"UPDATE {table} SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'", (text, timestamp(), item['id']))
                elif kind == 'bank_run':
                    self.question_bank.fail_run(db, item['id'], 'The question bank run stopped unexpectedly. Run it again.')
                elif kind == 'regrade_draft':
                    self.regrade_drafts.fail_run(db, item['id'], 'The reply draft stopped unexpectedly. Draft again.')
                elif kind == 'rubric_memory':
                    self.rubric_memory.fail_run(db, item['id'], 'The rubric memory pass stopped unexpectedly. Ask for it again.')
                else:
                    db.execute("UPDATE frq_endpoint_probes SET status='failed',error='The probe stopped unexpectedly.',updated_at=? WHERE id=?", (timestamp(), item["id"]))

    # Capability probe: what an endpoint was SEEN to do ----------------------------------------------------------------
    def probe_endpoint(self, name):
        settings = self.settings()["settings"]
        config.named_endpoint(settings, name)  # Refuses a connection that is not configured or is disabled.
        # A test is evidence about the SAVED connection, which is what `endpoints()` compares it with. The resolved connection also
        # carries request policy (the workspace's token-limit switch): fingerprinting that made every tested connection read "Not tested".
        saved = config.nebius_endpoint(settings) if name == "nebius" else next(e for e in settings["endpoints"] if e["name"] == name)
        with self.rosters.lock(), connect(self.path) as db:
            pending = db.execute("SELECT id FROM frq_endpoint_probes WHERE endpoint=? AND status IN ('pending','running')", (name,)).fetchone()
            probe_id = pending[0] if pending else uid()
            if not pending:
                db.execute("INSERT INTO frq_endpoint_probes VALUES(?,?,?,'pending',NULL,'',?,?)", (probe_id, name, digest(saved), timestamp(), timestamp()))
        self.inbox.wake.set()
        return self.endpoints()

    def run_probe(self, probe):
        """Two cents of evidence: is the model listed, does it answer in the schema, and does it read an image?"""
        settings = self.settings()["settings"]
        result, error = {"checked_at": timestamp()}, ""
        try:
            endpoint = config.named_endpoint(settings, probe["endpoint"])
            provider = self.cloud_provider(endpoint, min(settings["timeout"], 90))
            result.update(health=provider.health(), capabilities=provider.capabilities())
            image = probe_image() if endpoint["modality"] == "vision" else None
            schema = {"type": "object", "properties": {"sum": {"type": "integer"}, "digits_seen": {"type": "string"}}, "required": ["sum", "digits_seen"], "additionalProperties": False}
            answer = self.cloud_call({"probe_id": probe["id"], "step": "probe", "task_attempt": 0}, "probe", endpoint,
                {"prompt": "probe", "payload": {"task": "Add 17 and 25." + (" Also report the digits written in the image." if image else " Report an empty string for digits_seen.")},
                 "images": [image] if image else [], "schema": schema, "durable": False}, {**settings, "max_retries": 1, "unknown_resends": 0})
            output, metadata = answer["output"], answer["metadata"]
            result.update(structured_output=output.get("sum") == 42, image_input=None if image is None else "47" in str(output.get("digits_seen", "")),
                reported_model=metadata.get("reported_model"), upstream=metadata.get("upstream"), usage=metadata.get("usage"), cost=metadata.get("cost"),
                seconds=metadata.get("seconds"), reasoning_separated=metadata.get("reasoning_separated"))
            result["verified"] = bool(result["structured_output"] and result["image_input"] is not False)
        except RetryLater as waiting:
            error = str(waiting.reason)
        except (ProviderError, ValueError) as failure:
            error = str(failure)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE frq_endpoint_probes SET status=?,result_json=?,error=?,updated_at=? WHERE id=?", ("failed" if error else "complete", encoded(result), error, timestamp(), probe["id"]))

    def endpoints(self):
        """Configured endpoints with their capabilities and the latest thing each was seen to do. No key value is ever included."""
        settings = self.settings()["settings"]
        listing = []
        with connect(self.path) as db:
            for endpoint in [config.nebius_endpoint(settings), *settings["endpoints"]]:
                probe = db.execute("SELECT * FROM frq_endpoint_probes WHERE endpoint=? ORDER BY rowid DESC LIMIT 1", (endpoint["name"],)).fetchone()
                provider = self.cloud_provider(endpoint)
                current = bool(probe) and probe["endpoint_sha256"] == digest(endpoint)
                # A key bound to another host is never sent (`config.key_problem`): the connection is not usable, whatever the
                # environment holds, and the listing says why.
                refused = config.key_problem(endpoint)
                listing.append({"name": endpoint["name"], "capabilities": provider.capabilities(), "key_variable": endpoint["key_env"],
                    "key_present": bool(os.environ.get(endpoint["key_env"])) and not refused if endpoint["key_env"] else False,
                    "unusable_reason": refused, "enabled": endpoint["enabled"],
                    "probe": {"status": probe["status"], "current": current, "error": probe["error"], "result": json.loads(probe["result_json"]) if probe["result_json"] else None,
                              "updated_at": probe["updated_at"]} if probe else None,
                    "verification": "verified" if current and probe["status"] == "complete" and json.loads(probe["result_json"]).get("verified") else "unverified"})
        return {"endpoints": listing, "tiers": settings["tiers"], "default_tier": settings["default_tier"]}


def probe_image():
    """The digits 47 on white, drawn here so a probe never sends anyone's work."""
    import io
    from PIL import Image, ImageDraw
    image = Image.new("L", (160, 80), 255)
    draw = ImageDraw.Draw(image)
    for x, segments in ((30, ((0, 0, 0, 30), (0, 30, 30, 30), (30, 0, 30, 60))), (90, ((0, 0, 30, 0), (30, 0, 10, 60)))):
        for x0, y0, x1, y1 in segments:
            draw.line((x + x0, 10 + y0, x + x1, 10 + y1), fill=0, width=5)
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()
