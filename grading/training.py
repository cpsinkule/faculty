"""Every grade saved for training (requirement, 2026-09-26).

Each grading DECISION becomes one immutable `frq_training_examples` row (migration 028) of the term grading plan's response. The worker
saves it within seconds of the decision (`capture_training_one`, whenever `JobsMixin.process_one` has no local work), and the first time, every
decision made before this existed:

- an instructor review that decides (approved, manual, unreadable or rejected; a draft decides nothing): label `instructor`;
- an automatic finalization (`frq_all_acceptances` kind `automatic`; a trial acceptance is a test): label `automatic`.

The example holds what that decision was about, as it was: the answer crops (references to the immutable, checksum-verified crop images
of the source scan, never copies), the readings of them, the AI's transcript, rubric lines, reasons and score, the final transcript,
lines and score, the instructor's feedback on a correction, and the models and prompts that produced the proposal. The question and
rubric revisions it was graded under are referenced; both are immutable course-wide records. Nothing here decides, finalizes, changes
or sends a grade: it is saved beside them, reads retained records only, and never takes the worker's execution lock.

A later decision never rewrites an example. One on the same response, rubric revision and paper SUPERSEDES the earlier one (the
export names it), while a grade that went stale when its rubric changed stays a valid example of the rubric it was graded under
(the grade exports leave it out). The rubric finalization's clarifications and exclusions stay internal by department policy: an
example names the confirmation it was graded under and never copies it. `training_export` writes the zip that
`manage_grading.py finetune-export` and `benchmark` read; automatic grades are marked, and never evaluation truth.
"""
import json
import logging
import sqlite3
import tempfile
import time
import zipfile

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect

from . import arbitration, participation, storage
from .benchmark import SPLIT_RULE, enrollment_split
from .models import digest, encoded, uid

LOG = logging.getLogger(__name__)
RULE = "training-example-v1"  # What an example holds; a change to it is a new rule, and saved examples keep theirs.
FORMAT = "faculty-frq-training-v1"  # The export's dataset.json.
BATCH = 100  # Decisions saved per worker turn: about a second of catalog reads, and one short write under the lifecycle lock.
DECIDING = ("approved", "manual", "unreadable", "rejected")
# The tables `_unsaved_decisions` reads (frq_all_acceptances is the four acceptance tables); a table added to that view goes here too.
HIGH_WATER = """SELECT (SELECT MAX(rowid) FROM frq_reviews),(SELECT MAX(rowid) FROM frq_acceptances),(SELECT MAX(rowid) FROM frq_team_acceptances),
    (SELECT MAX(rowid) FROM frq_one_shot_acceptances),(SELECT MAX(rowid) FROM frq_tag_team_acceptances),(SELECT MAX(rowid) FROM frq_training_examples)"""
NOTHING_FOR = 600  # seconds a search that found nothing is trusted while the high-water marks stay the same
STEP_FACTS = ("prompt", "endpoint", "requested_model", "reported_model", "reasoning", "rule")


def written(transcript):
    """A transcript's words as lines, what was struck marked as struck; never the notes beside them."""
    lines = []
    for line in (transcript or {}).get("lines") or []:
        parts = [(el.get("latex") or el.get("text") or "") for el in line.get("elements") or []]
        states = [el.get("state") for el in line.get("elements") or []]
        lines.append(" ".join(f"[crossed out: {part}]" if state == "crossed_out" else part for part, state in zip(parts, states)))
    return "\n".join(lines).strip()


class TrainingMixin:
    def capture_training_one(self, limit=BATCH):
        """Save the examples of up to `limit` decisions that have none yet; True when there were any. The reads take no lock (every row
        they read is immutable); the write takes the lifecycle lock and inserts each decision at most once (UNIQUE decision). A decision
        whose example cannot be made is logged and left for the next process start, never retried in a loop that holds the others back."""
        skipped = self.__dict__.setdefault("_training_skipped", set())
        with connect(self.path) as db:
            db.execute("BEGIN")  # One snapshot for every read.
            # Decisions and examples are append-only, so a search that found nothing stays right until one of their tables grows.
            # The high-water marks are read in the search's own snapshot, before it; ten minutes bring the search back regardless.
            marks = (tuple(db.execute(HIGH_WATER).fetchone()), len(skipped))
            nothing = self.__dict__.get("_training_nothing")
            if nothing and nothing[0] == marks and time.monotonic() < nothing[1]:
                return False
            decisions = self._unsaved_decisions(db, limit + len(skipped))
            self._training_nothing = None if decisions else (marks, time.monotonic() + NOTHING_FOR)
            pending = [d for d in decisions if d not in skipped][:limit]
            rows = []
            for kind, decision_id in pending:
                try:
                    rows.append(self._training_row(db, kind, decision_id))
                except Exception:
                    LOG.exception("Grading decision %s %s could not be saved for training", kind, decision_id)
                    skipped.add((kind, decision_id))
        if not rows:
            return False
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                try:
                    db.execute("INSERT OR IGNORE INTO frq_training_examples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                except sqlite3.IntegrityError:
                    LOG.exception("Grading decision %s %s was refused as a training example", row[3], row[4])
                    skipped.add((row[3], row[4]))
        return True  # Saved, or found saved by another turn, or refused and set aside: either way this turn did its work.

    @staticmethod
    def _unsaved_decisions(db, limit):
        """(kind, id) of decisions without an example, the instructor's first (they are what a model learns from), oldest first. A
        waiting grade Faculty approved on a plan graded for participation is nobody's judgment of the answer, so it is no example
        (GRADING "THE WAITING GRADES APPROVE THEMSELVES")."""
        reviews = db.execute(f"""SELECT 'review',v.id FROM frq_reviews v WHERE v.status IN ({','.join('?' * len(DECIDING))}) AND v.result_id IS NOT NULL
            AND NOT {participation.APPROVAL}
            AND NOT EXISTS(SELECT 1 FROM frq_training_examples e WHERE e.decision_kind='review' AND e.decision_id=v.id) ORDER BY v.rowid LIMIT ?""",
            (*DECIDING, participation.APPROVED, limit)).fetchall()
        automatic = db.execute("""SELECT 'automatic',a.id FROM frq_all_acceptances a WHERE a.kind='automatic'
            AND NOT EXISTS(SELECT 1 FROM frq_training_examples e WHERE e.decision_kind='automatic' AND e.decision_id=a.id)
            ORDER BY a.created_at LIMIT ?""", (max(0, limit - len(reviews)),)).fetchall()
        return [tuple(row) for row in [*reviews, *automatic]]

    def _saved_step(self, db, task, stage):
        try:
            return self.step(db, task, stage)
        except ValueError:
            return {}  # A component this result never had (a removed scheme's single call): nothing is invented for it.

    @staticmethod
    def _step_facts(db, task, seen=None):
        """{stage: model and prompt} of the steps behind a result, a reused earlier result's first; the latest attempt of a stage counts."""
        seen = seen if seen is not None else set()
        if task["id"] in seen:
            return {}
        seen.add(task["id"])
        facts = {}
        if task.get("reuse_result_id"):
            prior = db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (task["reuse_result_id"],)).fetchone()
            facts = TrainingMixin._step_facts(db, dict(prior), seen) if prior else {}
        for row in db.execute("SELECT stage,provider_json FROM frq_steps WHERE task_id=? ORDER BY attempt,rowid", (task["id"],)):
            provider = json.loads(row["provider_json"])
            if isinstance(provider, dict) and provider.get("prompt"):
                facts[row["stage"]] = {k: provider[k] for k in STEP_FACTS if provider.get(k) is not None}
        return facts

    def _training_row(self, db, kind, decision_id):
        """The catalog row of one decision's example, from retained records only."""
        if kind == "review":
            decision = self.decode_result(db.execute("SELECT * FROM frq_reviews WHERE id=?", (decision_id,)).fetchone())
            result = self.decode_result(db.execute("SELECT * FROM frq_results WHERE id=?", (decision["result_id"],)).fetchone())
            noted = db.execute("SELECT * FROM frq_review_feedback WHERE review_id=?", (decision_id,)).fetchone()
            label, status, basis = "instructor", decision["status"], None
            final = {k: decision[k] for k in ("transcript", "applications", "score", "comment", "actor")}
            feedback = {"reasoning_wrong": bool(noted["reasoning_wrong"]), "override_reason": noted["override_reason"],
                        "transcript_corrected": bool(noted["transcript_corrected"]), "lines_changed": json.loads(noted["lines_changed_json"])} if noted else None
        else:
            decision = dict(db.execute("SELECT id,result_id,basis,created_at FROM frq_all_acceptances WHERE id=? AND kind='automatic'", (decision_id,)).fetchone())
            result = self.decode_result(db.execute("SELECT * FROM frq_results WHERE id=?", (decision["result_id"],)).fetchone())
            label, status, basis, feedback = "automatic", "automatic", arbitration.basis(result["signals"], decision["basis"]), None
            final = {"transcript": result["transcript"], "applications": result["applications"], "score": result["score"], "comment": "", "actor": "automatic"}
        task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone())
        response = db.execute("""SELECT r.assignment_id,r.question_id,q.question_key,q.exam_version FROM frq_responses r
            JOIN frq_questions q ON q.id=r.question_id WHERE r.id=?""", (result["response_id"],)).fetchone()
        signals = result["signals"] or {}
        crops = [{"images": {name: {"artifact_id": ref["artifact_id"], "sha256": ref["sha256"]} for name, ref in (crop.get("images") or {}).items()},
                  "metadata": crop.get("metadata") or {}} for crop in self._saved_step(db, task, "extracting").get("crops", [])]
        readings = [{"provider": c.get("provider"), "transcript": c.get("transcript")} for c in self._saved_step(db, task, "recognizing").get("candidates", [])]
        example = {"rule": RULE, "label": label, "status": status, "basis": basis, "decided_at": decision["created_at"],
            "question": {"question_id": response["question_id"], "question_key": response["question_key"], "exam_version": response["exam_version"],
                         "question_revision_id": task["question_revision_id"], "rubric_id": task["rubric_id"],
                         # The confirmation whose clarifications the graders read: named, never copied (they stay internal).
                         "rubric_finalization": (signals.get("scope") or {}).get("rubric_quality")},
            "source_sha256": task["source_sha256"], "crops": crops, "readings": readings,
            "proposal": {"result_id": result["id"], "task_id": task["id"], "job_id": task["job_id"], "state": result["state"],
                         "transcript": result["transcript"], "applications": result["applications"], "interpretation": result["interpretation"],
                         "score": result["score"], "reasons": result["reasons"], "tier": signals.get("tier"), "models": signals.get("models"),
                         "scope_sha256": signals.get("scope_sha256"), "steps": self._step_facts(db, task)},
            "final": final, "feedback": feedback}
        return (uid(), response["assignment_id"], result["response_id"], kind, decision_id, result["id"], task["question_revision_id"], task["rubric_id"],
                task["source_sha256"], label, status, RULE, encoded(example), digest(example), decision["created_at"], timestamp())

    @staticmethod
    def _training_counts(db, aid=None):
        where, args = ("WHERE assignment_id=?", (aid,)) if aid else ("", ())
        saved = db.execute(f"""SELECT COUNT(*),COALESCE(SUM(label='instructor'),0),COALESCE(SUM(label='automatic'),0),COUNT(DISTINCT response_id),
            COUNT(DISTINCT assignment_id) FROM frq_training_examples {where}""", args).fetchone()
        plan = "AND r.assignment_id=?" if aid else ""
        waiting = db.execute(f"""SELECT COUNT(*) FROM frq_reviews v JOIN frq_responses r ON r.id=v.response_id
            WHERE v.status IN ({','.join('?' * len(DECIDING))}) AND v.result_id IS NOT NULL {plan} AND NOT {participation.APPROVAL}
            AND NOT EXISTS(SELECT 1 FROM frq_training_examples e WHERE e.decision_kind='review' AND e.decision_id=v.id)""",
            (*DECIDING, *args, participation.APPROVED)).fetchone()[0]
        waiting += db.execute(f"""SELECT COUNT(*) FROM frq_all_acceptances a WHERE a.kind='automatic' {'AND a.assignment_id=?' if aid else ''}
            AND NOT EXISTS(SELECT 1 FROM frq_training_examples e WHERE e.decision_kind='automatic' AND e.decision_id=a.id)""", args).fetchone()[0]
        return {"examples": saved[0], "instructor": saved[1], "automatic": saved[2], "responses": saved[3], "assignments": saved[4], "waiting": waiting}

    def training_status(self, aid):
        """How many of this assignment's (and every assignment's) decisions are saved for training, and how many wait for the worker."""
        with connect(self.path) as db:
            db.execute("BEGIN")
            self.plan(db, aid)
            return {"assignment": self._training_counts(db, aid), "workspace": self._training_counts(db), "rule": RULE}

    def training_export(self, aid=None):
        """(zip file, media type, name): every saved example of one assignment, or of all of them, as `dataset.json` (`FORMAT`) and each
        original crop image once, named by its checksum. It reads retained records, verifies every image, and writes and publishes nothing.

        Students are an opaque key of this export only, and each record carries the split of its student in EVERY export
        (`benchmark.enrollment_split`). Questions and rubric revisions are listed once and named by each record; `evaluate.load` fills
        them in. `standing` is false for a decision a later one superseded and for an automatic grade whose acceptance was suspended."""
        nonce = uid()
        with connect(self.path) as db:
            db.execute("BEGIN")
            if aid:
                self.plan(db, aid)
            rows = [dict(r) for r in db.execute(f"""SELECT e.*,r.section_id,r.enrollment_id FROM frq_training_examples e JOIN frq_responses r ON r.id=e.response_id
                {'WHERE e.assignment_id=?' if aid else ''} ORDER BY e.assignment_id,e.response_id,e.decided_at,e.rowid""", (aid,) if aid else ())]
            if not rows:
                raise Conflict("No graded response has been saved for training yet." + (" Graded responses are saved within a minute." if self._training_counts(db, aid)["waiting"] else ""))
            plans = {a: self.plan(db, a) for a in {row["assignment_id"] for row in rows}}
            active = {row[0]: bool(row[1]) for row in db.execute("SELECT id,active FROM frq_all_acceptances WHERE kind='automatic'")}
            questions = {row["id"]: {"question_id": row["question_id"], "revision": row["revision"], "sha256": row["sha256"], **json.loads(row["content_json"])}
                         for row in db.execute(f"SELECT * FROM frq_question_revisions WHERE id IN (SELECT question_revision_id FROM frq_training_examples {'WHERE assignment_id=?' if aid else ''})", (aid,) if aid else ())}
            rubrics = {row["id"]: {"id": row["id"], "question_id": row["question_id"], "revision": row["revision"], "sha256": row["sha256"], "rules": json.loads(row["rules_json"])}
                       for row in db.execute(f"SELECT * FROM frq_rubric_revisions WHERE id IN (SELECT rubric_id FROM frq_training_examples {'WHERE assignment_id=?' if aid else ''})", (aid,) if aid else ())}
        # The same response, rubric revision and paper decided again: the later decision is the one that counts. An automatic grade whose
        # acceptance is suspended counts for nothing, so it supersedes nothing either (2026-09-27: four approvals the instructor made on a
        # regraded grade were replaced by mistake and restored; the automatic grade saved meanwhile must not outrank them).
        latest = {}
        for row in reversed(rows):
            key = (row["response_id"], row["rubric_id"], row["source_sha256"])
            row["superseded_by"] = latest.get(key)
            if row["decision_kind"] != "automatic" or active.get(row["decision_id"], False):
                latest[key] = row["id"]
        stream = tempfile.TemporaryFile(prefix="faculty-training-")  # TMPDIR: the service's own scratch on NVMe.
        try:
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                records, stored, problems = [], set(), []
                for row in rows:
                    example, plan = json.loads(row["example_json"]), plans[row["assignment_id"]]
                    crops = []
                    for index, crop in enumerate(example["crops"]):
                        reference, path = crop["images"].get("original"), None
                        if reference:
                            path = f"images/{reference['sha256']}.png"
                            if path not in stored:
                                try:
                                    # Already compressed PNG: stored as it is. Every image is read through the verified storage manager.
                                    archive.writestr(path, storage.read(self.inbox, reference), compress_type=zipfile.ZIP_STORED)
                                    stored.add(path)
                                except Exception as error:
                                    problems.append({"example_id": row["id"], "crop": index, "error": getattr(error, "description", str(error))})
                                    path = None
                        crops.append({"image": path, "metadata": crop["metadata"]})
                    automatic = row["label"] == "automatic"
                    # What the words say: an unchanged blank answer was marked corrected until 2026-11-02 (`models.transcript`), and those rows stay.
                    feedback = {**example["feedback"], "transcript_corrected": written(example["final"]["transcript"]) != written(example["proposal"]["transcript"])} if example["feedback"] else None
                    records.append({"example_id": row["id"], "response_id": row["response_id"], "rule": row["rule"],
                        "assignment": {"id": row["assignment_id"], "title": plan["title"], "course": plan["code"], "term": plan["term_other"] or plan["term"], "year": plan["year"]},
                        "question_key": example["question"]["question_key"], "exam_version": example["question"]["exam_version"],
                        "question_revision_id": row["question_revision_id"], "rubric_id": row["rubric_id"], "rubric_finalization": example["question"]["rubric_finalization"],
                        # Opaque and stable within this export only; the split is the student's in every export.
                        "student": digest(f"{nonce}:{row['section_id']}:{row['enrollment_id']}")[:16], "split": enrollment_split(row["enrollment_id"]),
                        "label": row["label"], "status": row["status"], "basis": example["basis"], "decided_at": row["decided_at"],
                        "superseded_by": row["superseded_by"], "active": active.get(row["decision_id"], False) if automatic else None,
                        "standing": not row["superseded_by"] and (not automatic or active.get(row["decision_id"], False)),
                        "images": [c["image"] for c in crops if c["image"]], "crops": crops, "readings": example["readings"],
                        "transcript": example["final"]["transcript"], "proposal": example["proposal"], "final": example["final"], "feedback": feedback})
                archive.writestr("dataset.json", encoded({"format": FORMAT, "rule": RULE, "split_rule": SPLIT_RULE, "created_at": timestamp(),
                    "scope": {"assignment_id": aid} if aid else "workspace", "records": records, "questions": questions, "rubrics": rubrics, "problems": problems,
                    "counts": {"examples": len(records), "instructor": sum(r["label"] == "instructor" for r in records), "automatic": sum(r["label"] == "automatic" for r in records),
                               "standing": sum(r["standing"] for r in records), "images": len(stored)},
                    "note": "Every saved grading decision with its answer crops (images/, by checksum), the readings, the AI's transcript and rubric "
                            "decisions (`proposal`) and the final ones (`final`), under the question and rubric revision named by each record "
                            "(`questions`, `rubrics`). `label` says who decided: `instructor` (your review) or `automatic` (finalized by the grading "
                            "models; nobody looked at it, so it is never evaluation truth). `superseded_by` names a later decision on the same "
                            "response, rubric and paper; `standing` is false for those and for suspended automatic grades. Keep each student in the "
                            "record's `split`; never split by response. Handwriting may identify a student. The rubric finalization's clarifications "
                            "are internal and not included. Exported locally; nothing was published."}))
            stream.seek(0)
            return stream, "application/zip", "grading-training-data.zip" if not aid else f"grading-training-{aid[:8]}.zip"
        except BaseException:
            stream.close()
            raise
