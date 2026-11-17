"""Reset a finished batch so its responses can be graded again with other models or settings.

Nothing is erased. Results, steps, cloud calls, costs, acceptances and events stay; a reset only clears the pointer from
a response to its current proposal, records which pointer it cleared, and can put it back while nothing newer exists.
An instructor's decision is never withdrawn: a response with any review, even a saved correction, keeps its proposal and
its decision. A response whose current proposal came from a later batch is not this batch's to reset, and one with work
under way is left alone. A reset response has no proposal, so it cannot be reviewed, accepted or exported until it is
graded again, and the next batch takes it as new work.
"""
import json

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from .models import digest, encoded, text, uid
from .progress import automatic_decision

ACTIVE = "EXISTS (SELECT 1 FROM frq_tasks a WHERE a.response_id=r.id AND a.status IN ('pending','running'))"
# A proposal newer than the one a reset withdrew, current or withdrawn in turn (reset A, B grades, reset B): restoring A's would put
# the older grade back in its place. `x` is the reset's member row.
NEWER = "EXISTS (SELECT 1 FROM frq_results n WHERE n.response_id=r.id AND n.rowid>(SELECT o.rowid FROM frq_results o WHERE o.id=x.result_id))"


class BatchResetsMixin:
    @staticmethod
    def reset_ungraded(db, aid):
        """Responses without a proposal that are new work for the next batch although they have a row: those a reset left without one,
        and those whose every batch was CANCELLED before proposing anything, since nothing retries a cancelled batch (2026-09-24: a
        2,169-response batch cancelled after running out of file handles offered neither a reset, having no proposal, nor a retry, nor
        new work), or whose only attempts in a batch still standing were made under an earlier rubric or question (a rubric edited
        while its batch ran: that batch refuses to retry them; requirement, 2026-09-25). One that failed in a batch still standing under
        the current rubric and question is that batch's to retry, and stays out. Never while any work is under way, and never once
        reviewed.

        Sets from a few indexed queries, not a subquery per response: frq_tasks has no index by response, and asked per response the
        cancelled-batch rule scanned the tasks of every batch for each of 2,600 responses, a second of every Grading page load."""
        waiting = {row[0] for row in db.execute("""SELECT id FROM frq_responses WHERE assignment_id=? AND current_result_id IS NULL
            AND current_review_id IS NULL""", (aid,))}
        if not waiting:
            return set()
        tasks = db.execute("""SELECT t.response_id,t.status,j.cancel_requested,t.rubric_id IS p.rubric_id AND t.question_revision_id IS p.question_revision_id
            FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id JOIN frq_responses r ON r.id=t.response_id
            JOIN frq_assignment_questions p ON p.assignment_id=r.assignment_id AND p.question_id=r.question_id
            WHERE j.assignment_id=?""", (aid,)).fetchall()
        busy = {r[0] for r in tasks if r[1] in ("pending", "running")}
        tried = {r[0] for r in tasks}
        standing = {r[0] for r in tasks if not r[2] and r[3]}
        reset = {row[0] for row in db.execute("""SELECT DISTINCT x.response_id FROM frq_job_reset_responses x JOIN frq_responses r ON r.id=x.response_id
            WHERE r.assignment_id=?""", (aid,))}
        return {rid for rid in waiting - busy if rid in reset or (rid in tried and rid not in standing)}

    @staticmethod
    def outdated(db, aid, responses):
        """Responses whose grade no longer applies, with no work under way: new work for the next batch (requirement, 2026-09-25).

        `responses` is the listing's derived state: `stale` means the current proposal, or the instructor's decision, was made under
        an earlier rubric, question or paper than today's. Such a grade cannot be approved, finalized or exported (`validate_task`),
        so it is not waiting for review; it waits to be graded again. Nothing is withdrawn or erased: the earlier grade and any
        decision stay in the history, and a decision is never replaced (a regraded response the instructor had decided waits for
        them again)."""
        busy = {row[0] for row in db.execute("""SELECT DISTINCT t.response_id FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id
            WHERE j.assignment_id=? AND t.status IN ('pending','running')""", (aid,))}
        return {r["id"] for r in responses if r["state"] == "stale" and r["id"] not in busy}

    @staticmethod
    def _reset_outlook(db, job):
        """Every response this batch worked on, sorted by what a reset would do with it. Writes nothing."""
        groups = {"withdraw": [], "reviewed": [], "later": [], "busy": [], "none": []}
        for row in db.execute(f"""SELECT r.id,r.version,r.current_result_id,r.current_review_id,{ACTIVE} busy,
                (SELECT t.job_id FROM frq_results x JOIN frq_tasks t ON t.id=x.task_id WHERE x.id=r.current_result_id) result_job
                FROM frq_responses r WHERE r.id IN (SELECT response_id FROM frq_tasks WHERE job_id=?) ORDER BY r.id""", (job["id"],)):
            kind = ("none" if not row["current_result_id"] else "reviewed" if row["current_review_id"] else "later" if row["result_job"] != job["id"]
                    else "busy" if row["busy"] else "withdraw")
            groups[kind].append({"id": row["id"], "version": row["version"], "result_id": row["current_result_id"]})
        return groups

    @staticmethod
    def reset_summary(db, jid):
        """The batch card's view of its latest reset, and whether a reset has anything to withdraw."""
        resettable = db.execute("""SELECT COUNT(*) FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id JOIN frq_tasks t ON t.id=x.task_id
            WHERE t.job_id=? AND r.current_review_id IS NULL""", (jid,)).fetchone()[0]
        row = db.execute("""SELECT s.*,v.created_at restored_at,v.detail_json restored_json FROM frq_job_resets s
            LEFT JOIN frq_job_reset_restores v ON v.reset_id=s.id WHERE s.job_id=? ORDER BY s.rowid DESC LIMIT 1""", (jid,)).fetchone()
        if not row:
            return {"resettable": resettable, "latest": None}
        waiting = db.execute(f"""SELECT COUNT(*) FROM frq_job_reset_responses x JOIN frq_responses r ON r.id=x.response_id
            WHERE x.reset_id=? AND r.current_result_id IS NULL AND NOT {ACTIVE} AND NOT {NEWER}""", (row["id"],)).fetchone()[0]
        detail = json.loads(row["detail_json"])
        return {"resettable": resettable, "latest": {"id": row["id"], "created_at": row["created_at"], "withdrawn": detail["withdrawn"], "kept_reviewed": detail["kept_reviewed"],
            "restored_at": row["restored_at"], "restored": json.loads(row["restored_json"])["restored"] if row["restored_json"] else None,
            # Still without a proposal and idle: what a restore can put back, and what the next batch takes as new.
            "ungraded": 0 if row["restored_at"] else waiting}}

    def reset_outlook(self, jid):
        with self.rosters.lock(), connect(self.path) as db:
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone()
            if not job:
                raise NotFound("Grading job not found.")
            groups = self._reset_outlook(db, dict(job))
            automatic = sum(bool(automatic_decision(db, member["result_id"])) for member in groups["withdraw"])
            trial = db.execute("""SELECT COUNT(*) FROM frq_acceptances a JOIN frq_responses r ON r.current_result_id=a.result_id JOIN frq_results x ON x.id=a.result_id
                JOIN frq_tasks t ON t.id=x.task_id WHERE t.job_id=? AND a.kind='trial' AND r.current_review_id IS NULL""", (jid,)).fetchone()[0]
        return {"job_id": jid, "status": job["status"], "running": job["status"] in ("pending", "running"),
            "withdraw": len(groups["withdraw"]), "automatic": automatic, "trial": trial, "kept_reviewed": len(groups["reviewed"]), "graded_later": len(groups["later"]),
            "busy": len(groups["busy"]), "without_proposal": len(groups["none"]), "cost": job["cost"],
            "fingerprint": digest({"job": jid, "status": job["status"], "groups": {kind: [(m["id"], m["version"]) for m in members] for kind, members in groups.items()}})}

    def reset_job(self, jid, data):
        """Withdraw this batch's unreviewed current proposals. Idempotent by request ID; refused when the preview is stale."""
        request_id = text(data.get("request_id"), "Request ID", 128, False)
        if data.get("confirmation") != "reset":
            raise ValueError("Confirm the reset. Nothing is erased, and reviewed responses keep their decisions.")
        command = digest({"job": jid, "fingerprint": data.get("fingerprint")})
        with self.rosters.lock():
            with connect(self.path) as db:
                previous = db.execute("SELECT * FROM frq_job_resets WHERE request_id=?", (request_id,)).fetchone()
                if previous:
                    if previous["request_sha256"] != command or previous["job_id"] != jid:
                        raise Conflict("This request ID was already used for a different reset.")
                    return self.job(jid)
            outlook = self.reset_outlook(jid)
            if outlook["running"]:
                raise Conflict("This batch is still working. Cancel its remaining work before resetting it.")
            if data.get("fingerprint") != outlook["fingerprint"]:
                raise Conflict("These responses changed. Look at the reset again before confirming it.")
            if not outlook["withdraw"]:
                raise Conflict("Nothing to reset: this batch has no unreviewed current proposal.")
            with connect(self.path) as db:
                job = dict(db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone())
                db.execute("BEGIN IMMEDIATE")
                members = self._reset_outlook(db, job)["withdraw"]
                reset_id, now = uid(), timestamp()
                detail = {"withdrawn": len(members), "kept_reviewed": outlook["kept_reviewed"], "graded_later": outlook["graded_later"], "busy": outlook["busy"],
                          "automatic": outlook["automatic"], "trial": outlook["trial"]}
                db.execute("INSERT INTO frq_job_resets VALUES(?,?,?,?,?,?,?,?)", (reset_id, jid, job["assignment_id"], request_id, command, encoded(detail), "workspace", now))
                for member in members:
                    db.execute("INSERT INTO frq_job_reset_responses VALUES(?,?,?)", (reset_id, member["id"], member["result_id"]))
                    changed = db.execute("UPDATE frq_responses SET current_result_id=NULL,version=version+1 WHERE id=? AND version=? AND current_result_id=? AND current_review_id IS NULL",
                                         (member["id"], member["version"], member["result_id"])).rowcount
                    if changed != 1:
                        raise Conflict("A response changed while the batch was being reset. Nothing was reset.")
                self.event(db, job["assignment_id"], "batch_reset", {"job_id": jid, "reset_id": reset_id, **detail})
                self.touch(db, job["assignment_id"])
        return self.job(jid)

    def restore_job_reset(self, jid, data):
        """Put back the proposals of this batch's latest reset wherever nothing newer exists and nothing is under way."""
        with self.rosters.lock(), connect(self.path) as db:
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone()
            if not job:
                raise NotFound("Grading job not found.")
            reset = db.execute("SELECT * FROM frq_job_resets WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (jid,)).fetchone()
            if not reset or (data.get("reset_id") and data["reset_id"] != reset["id"]):
                raise Conflict("This batch has no reset to restore, or a newer one exists. Reload the batch.")
            if db.execute("SELECT 1 FROM frq_job_reset_restores WHERE reset_id=?", (reset["id"],)).fetchone():
                return self.job(jid)  # Already restored: asking again changes nothing.
            db.execute("BEGIN IMMEDIATE")  # Committed when this block ends; the payload below is read afterwards.
            restored = db.execute(f"""UPDATE frq_responses AS r SET version=version+1,
                current_result_id=(SELECT x.result_id FROM frq_job_reset_responses x WHERE x.reset_id=? AND x.response_id=r.id)
                WHERE EXISTS (SELECT 1 FROM frq_job_reset_responses x WHERE x.reset_id=? AND x.response_id=r.id AND NOT {NEWER})
                AND r.current_result_id IS NULL AND NOT {ACTIVE}""", (reset["id"], reset["id"])).rowcount
            withdrawn = json.loads(reset["detail_json"])["withdrawn"]
            detail = {"restored": restored, "left_alone": withdrawn - restored}
            db.execute("INSERT INTO frq_job_reset_restores VALUES(?,?,?,?)", (reset["id"], encoded(detail), "workspace", timestamp()))
            self.event(db, job["assignment_id"], "batch_reset_restored", {"job_id": jid, "reset_id": reset["id"], **detail})
            self.touch(db, job["assignment_id"])
        return self.job(jid)
