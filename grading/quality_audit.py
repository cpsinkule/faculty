"""Final Quality Audit (requirement, 2026-09-27).

An audit belongs to the TERM grading plan (`frq_quality_audits`, migration 031). When requested it snapshots the plan's FINAL grades
(`ReviewMixin.final_decisions`, the export's own rule): for each response its lines, score, final answers, an excerpt of its transcript
and who decided it. It never changes, approves or finalizes anything: the instructor acts on a finding with the ordinary review editor
or a batch adjustment. Three layers, cheapest first:

1. Free checks (`audit_checks`, no model): responses with the same final answer but different rubric lines, one question's versions
   scoring far apart, a section scoring far from its question's others.
2. A text pass per question and version: the rubric model reads compact rows (about 500-1,000 tokens each, never the scans) and reports
   what looks inconsistent (`quality_audit` prompt). A question with more than `CHUNK` responses is read in chunks, each with the same
   instructor-reviewed `anchors`, so every chunk is judged by one standard. Each request is a cloud call owned by the audit, replayed
   from its receipt after a wait or a restart, never bought twice.
3. An image-first sample of up to three automatic grades per question/version, spread across score bands. It is read before the
   saved work and grade are supplied, including when the text pass finds nothing. This is diagnostic sampling, not an accuracy estimate.
4. A scan pass for what it found: each finding (at most `MAX_CONFIRM` per question) goes back with the crops of the responses it names
   (`quality_audit_confirm` prompt), and says whether it holds.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from . import answers, config, storage
from .cloud import RetryLater, later
from .models import digest, encoded, text, uid
from .providers.base import ProviderError
from .rubric_quality import grader_rubric, linked_groups

RULE = "quality-audit-v2"
SAMPLE = 3            # Automatic grades per question/version, spread over zero, partial and full credit.
CHUNK = 60             # Responses per text request: about 60,000 tokens of rows.
ANCHORS = 6            # Instructor-reviewed rows shown beside each chunk, one per distinct score.
EXCERPT = 700          # Characters of each response's transcript in its row.
MAX_CONFIRM = 8        # Findings per question checked against the scans.
CONFIRM_REFS = 10      # Responses per scan check.
IMAGES = 18 * 1024**2  # Bytes of crops per scan check.
PARALLEL = 16
AUDIT_TOKENS = 32768
SEVERITY = {"high": 0, "medium": 1, "low": 2}
# Free checks: measured gaps worth the instructor's look, not verdicts.
VERSION_GAP, SECTION_GAP, MIN_N = .15, .20, 5


def plain(transcript):
    transcript = transcript or {}
    return str(transcript.get("plain_text") or "\n".join(str(line.get("text", "")) for line in transcript.get("lines") or []))


def fraction(row, points):
    return float(Decimal(str(row["score"])) / Decimal(str(points))) if row.get("score") is not None and points else None


# A final answer that is only a MARK - the circle, slash or tick a reader wrote for a printed choice the student marked, or a circle read
# as the letter O - says nothing about WHICH answer was given, so it groups no responses (requirement, 2026-09-29).
MARK_ONLY = re.compile(r"[○◯◦°•●/\\|✓✔✗✘()\[\]]+|[oO]")


def telling(answer):
    """Whether a reported final answer says what the answer was, not only that something was marked."""
    return bool(str(answer).strip()) and not MARK_ONLY.fullmatch(re.sub(r"\s+", "", str(answer)))


def audit_checks(questions):
    """The free checks over the snapshot: [{kind, question_id?, title, detail, responses?}]."""
    found = []
    for q in questions:
        groups = {}
        for r in q["responses"]:
            key = tuple(sorted({answers.normalize(a) for a in r["final_answers"] if telling(a)}))
            if key:
                groups.setdefault(key, []).append(r)
        for members in groups.values():
            if len(members) > 1 and len({tuple(m["lines"]) for m in members}) > 1:
                scores = sorted({m["score"] for m in members if m["score"] is not None})
                found.append({"kind": "same_answer", "question_id": q["question_id"], "title": q["label"],
                              "detail": f"{len(members)} responses give the final answer “{', '.join(members[0]['final_answers'])[:120]}” but received "
                                        f"{len({tuple(m['lines']) for m in members})} different sets of rubric lines" + (f" (scores {', '.join(map(str, scores))})." if len(scores) > 1 else "."),
                              "responses": [ref_view(m) for m in members]})
        overall = [f for f in (fraction(r, q["points"]) for r in q["responses"]) if f is not None]
        sections = {}
        for r in q["responses"]:
            f = fraction(r, q["points"])
            if f is not None:
                sections.setdefault(r["section_id"], []).append(f)
        if len(overall) >= MIN_N and len(sections) > 1:
            mean = sum(overall) / len(overall)
            for section, values in sections.items():
                if len(values) >= MIN_N and abs(sum(values) / len(values) - mean) >= SECTION_GAP:
                    found.append({"kind": "section_gap", "question_id": q["question_id"], "title": q["label"], "section_id": section,
                                  "detail": f"One section averages {sum(values) / len(values):.0%} of the points on this question ({len(values)} responses), "
                                            f"against {mean:.0%} across all sections."})
    by_family = {}
    for q in questions:
        values = [f for f in (fraction(r, q["points"]) for r in q["responses"]) if f is not None]
        if q["exam_version"] and q.get("family") and len(values) >= MIN_N:
            by_family.setdefault(q["family"], []).append((q, sum(values) / len(values), len(values)))
    for versions in by_family.values():
        if len(versions) > 1:
            high, low = max(versions, key=lambda v: v[1]), min(versions, key=lambda v: v[1])
            if high[1] - low[1] >= VERSION_GAP:
                found.append({"kind": "version_gap", "question_id": low[0]["question_id"], "title": low[0]["label"],
                              "detail": "Versions score far apart on this question: " + ", ".join(f"Version {q['exam_version']} {mean:.0%} ({n})" for q, mean, n in
                                                                                             sorted(versions, key=lambda v: v[0]["exam_version"]))
                                        + ". A harder version, or one graded more strictly."})
    return found


def image_sample(question):
    """Reproducible within one question/version, with each score band represented before filling spare places.

    The term-owned audit snapshots the selected refs. No model or automatic grade becomes an evaluation label.
    """
    bands = {}
    for row in question["responses"]:
        if row["decided_by"] != "automatic":
            continue
        score = fraction(row, question["points"])
        band = "unknown" if score is None else "zero" if score <= 0 else "full" if score >= 1 else "partial"
        bands.setdefault(band, []).append(row)
    for members in bands.values():
        members.sort(key=lambda r: digest([RULE, question["question_id"], r["response_id"]]))
    chosen = []
    while len(chosen) < SAMPLE and any(bands.values()):
        for band in ("zero", "partial", "full", "unknown"):
            if bands.get(band) and len(chosen) < SAMPLE:
                chosen.append(bands[band].pop(0)["ref"])
    return chosen


def blind_readings(output, refs, known):
    if not isinstance(output, dict) or not isinstance(output.get("readings"), list):
        raise ValueError("The image sample returned no readings.")
    readings, seen = [], set()
    for row in output["readings"]:
        if not isinstance(row, dict) or row.get("ref") not in refs or row["ref"] in seen:
            raise ValueError("The image sample must read each supplied response once.")
        lines = row.get("lines")
        if not isinstance(lines, list) or any(not isinstance(line, str) or line not in known for line in lines) or len(set(lines)) != len(lines):
            raise ValueError("The image sample names invalid rubric lines.")
        if type(row.get("uncertain")) is not bool:
            raise ValueError("The image sample must say whether its reading is uncertain.")
        readings.append({"ref": row["ref"], "lines": lines, "work": text(row.get("work"), "Sample reading", 12000),
                         "reason": text(row.get("reason"), "Sample reason", 4000), "uncertain": row["uncertain"]})
        seen.add(row["ref"])
    if seen != refs:
        raise ValueError("The image sample left a response unread.")
    return readings


def ref_view(row):
    return {"ref": row.get("ref"), "response_id": row["response_id"], "row": f"{row['section_id']}:{row['enrollment_id']}", "score": row["score"],
            "lines": row["lines"], "decided_by": row["decided_by"]}


def with_row(suggestion, by_ref):
    """A suggestion with the response id and row of the response it names, so the page can say whose it is."""
    return {**suggestion, **{k: v for k, v in ref_view(by_ref[suggestion["ref"]]).items() if k in ("response_id", "row")}}


def model_row(row):
    """What the model reads of one response: never an identifier, a name or a section."""
    return {"ref": row["ref"], "lines": row["lines"], "score": row["score"], "final_answers": row["final_answers"], "work": row["work"],
            "work_truncated": row["work_truncated"], "decided_by": row["decided_by"]}


def suggestions_of(output, refs, known):
    found = []
    for item in output.get("suggestions") or []:
        if isinstance(item, dict) and item.get("ref") in refs:
            add = [i for i in item.get("add") or [] if i in known]
            remove = [i for i in item.get("remove") or [] if i in known]
            if add or remove:
                found.append({"ref": item["ref"], "add": add, "remove": remove, "note": str(item.get("note") or "")[:500]})
    return found


def findings_of(output, chunk_refs, anchor_refs, known):
    """The text pass's findings, kept only where they name responses of this chunk; unknown refs and lines are dropped, never guessed."""
    if not isinstance(output, dict) or not isinstance(output.get("findings"), list):
        raise ValueError("The audit answer has no list of findings.")
    kept = []
    for item in output["findings"]:
        if not isinstance(item, dict) or item.get("kind") not in ("inconsistent", "misapplied", "outlier"):
            continue
        refs = [r for r in dict.fromkeys(item.get("refs") or []) if r in chunk_refs or r in anchor_refs]
        if not any(r in chunk_refs for r in refs):
            continue  # About anchors alone, or about responses it was not given.
        kept.append({"kind": item["kind"], "severity": item.get("severity") if item.get("severity") in SEVERITY else "low",
                     "refs": refs, "lines": [i for i in item.get("lines") or [] if i in known],
                     "explanation": str(item.get("explanation") or "")[:4000], "suggestions": suggestions_of(item, set(refs), known)})
    return str(output.get("summary") or "")[:1000], kept


def confirmation_of(output, refs, known):
    if not isinstance(output, dict) or output.get("verdict") not in ("confirmed", "partly", "not_confirmed"):
        raise ValueError("The scan check has no verdict.")
    return {"verdict": output["verdict"], "explanation": str(output.get("explanation") or "")[:4000],
            "suggestions": [] if output["verdict"] == "not_confirmed" else suggestions_of(output, set(refs), known)}


class QualityAuditMixin:
    def _audit_model(self):
        settings = self.settings()["settings"]
        endpoint = config.rubric_reviewer(settings)
        if not endpoint:
            raise ValueError("Choose a Rubric drafting and review model in Compute settings first: it runs the quality audit.")
        return {**endpoint, "max_tokens": max(endpoint["max_tokens"], AUDIT_TOKENS)}, settings

    def _audit_snapshot(self, db, aid):
        """The plan's final grades as they stand, per question and version. The caller holds the lifecycle lock."""
        _, decisions, _ = self.final_decisions(db, aid)
        standing = self.rubric_quality_status(db, aid).get("standing") or {}
        made = {qid: pair[0] for qid, pair in self.question_origins(db, aid).items()}
        by_question = {}
        for decision in decisions:
            by_question.setdefault(decision["row"]["question_id"], []).append(decision)
        questions = []
        for (qid,) in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,)).fetchall():
            chosen = by_question.get(qid)
            if not chosen:
                continue
            q = self.question(db, aid, qid)
            content, rules = q["content"], (q["rubric"] or {}).get("rules") or {"items": []}
            number = str(content.get("printed_number") or content.get("number") or q["question_key"])
            rows = []
            for decision in chosen:
                final, row = decision["final"], decision["row"]
                stored = db.execute("SELECT interpretation_json,transcript_json FROM frq_results WHERE id=?", (row["result_id"],)).fetchone()
                interpretation = json.loads(stored["interpretation_json"] or "{}") if stored else {}
                work = plain(final.get("transcript") or (json.loads(stored["transcript_json"] or "{}") if stored else {}))
                rows.append({"response_id": row["id"], "section_id": row["section_id"], "enrollment_id": row["enrollment_id"], "task_id": decision["task"]["id"],
                             "review_id": row["current_review_id"], "result_id": row["current_result_id"],
                             "decided_by": "instructor" if decision["kind"] == "review" else "automatic",
                             "lines": sorted(a["rubric_item_id"] for a in final.get("applications") or [] if a.get("applied")),
                             "score": (final.get("score") or {}).get("score"),
                             "final_answers": [str(f.get("text", ""))[:200] for f in (interpretation.get("final_answers") or [])][:6],
                             "work": work[:EXCERPT], "work_truncated": len(work) > EXCERPT})
            # A stable order that keeps similar work together: by final answer, then score. The refs follow it.
            rows.sort(key=lambda r: (tuple(answers.normalize(a) for a in r["final_answers"]), str(r["score"]), r["response_id"]))
            for i, row in enumerate(rows, 1):
                row["ref"] = f"R{i}"
            label = f"{content.get('title') or 'Question ' + number}" + (f" · Version {q['exam_version']}" if q["exam_version"] else "")
            questions.append({"question_id": qid, "label": label, "printed_number": number, "exam_version": q["exam_version"],
                              "points": content.get("max_points"), "rubric_id": q["rubric_id"], "origins": made.get(qid, []),
                              "question": {"title": content.get("title") or "", "printed_number": number, "exam_version": q["exam_version"],
                                           "points": content.get("max_points"), "problem": content.get("problem") or "",
                                           "solution": content.get("worked_solution") or content.get("instructor_solution") or content.get("coordinator_solution") or "",
                                           "answer_key": content.get("answer_key") or []},
                              "rubric": grader_rubric(rules, (standing.get(qid) or {}).get("entry")), "responses": rows})
        # Reuse the grouping from each standing finalization; never join versions by their printed question number.
        by_id = {q["question_id"]: q for q in questions}
        for review in sorted({entry["review_id"] for entry in standing.values()}):
            record = db.execute("""SELECT c.result_json FROM frq_rubric_quality_reviews r
                JOIN frq_rubric_quality_checks c ON c.id=r.check_id WHERE r.id=? AND r.assignment_id=?""", (review, aid)).fetchone()
            for index, family in enumerate((json.loads(record[0] or "{}") if record else {}).get("families", [])):
                for qid in family:
                    if qid in by_id and (standing.get(qid) or {}).get("review_id") == review:
                        by_id[qid]["origins"].append(["family", f"review:{review}", str(index)])
        for group in linked_groups(questions):
            for qid in group:
                by_id[qid]["family"] = digest(sorted(group))
        for q in questions:
            q["image_sample"] = image_sample(q)
        return questions

    def queue_quality_audit(self, aid, data):
        request_id = text(data.get("request_id"), "Request ID", 128, False)
        endpoint, settings = self._audit_model()
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            prior = db.execute("SELECT * FROM frq_quality_audits WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["assignment_id"] != aid:
                    raise Conflict("This request ID already belongs to another audit.")
                return self.quality_audit_summary(db, prior)
            if db.execute("SELECT 1 FROM frq_quality_audits WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
                raise Conflict("A quality audit of this assignment is already running. Wait for it, or stop it.")
            questions = self._audit_snapshot(db, aid)
            if not questions:
                raise ValueError("No grade of this assignment is final yet: there is nothing to audit.")
            audit_id, now = uid(), timestamp()
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO frq_quality_audits(id,assignment_id,request_id,request_sha256,input_json,profile_json,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'pending',?,?)""", (audit_id, aid, request_id, digest([aid, request_id, endpoint["name"]]),
                encoded({"rule": RULE, "questions": questions}), encoded({"endpoint": endpoint, "settings": settings}), now, now))
            self.event(db, aid, "quality_audit_queued", {"audit_id": audit_id, "model": endpoint["name"], "questions": len(questions),
                                                          "responses": sum(len(q["responses"]) for q in questions)})
            db.commit()
            summary = self.quality_audit_summary(db, db.execute("SELECT * FROM frq_quality_audits WHERE id=?", (audit_id,)).fetchone())
        self.inbox.wake.set()
        return summary

    def cancel_quality_audit(self, aid, audit_id):
        """Stop a waiting or running audit. A request already sent keeps its receipt; nothing further is sent."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE frq_quality_audits SET status='failed',cancelled=1,error='Stopped by the instructor.',updated_at=? WHERE id=? AND assignment_id=? AND status IN ('pending','running')",
                          (timestamp(), audit_id, aid)).rowcount:
                self.event(db, aid, "quality_audit_cancelled", {"audit_id": audit_id})
            db.commit()
        return self.quality_audit(aid)

    def quality_audit(self, aid):
        """The plan's latest audit with its findings, and the earlier ones in brief. Read only."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            rows = db.execute("SELECT * FROM frq_quality_audits WHERE assignment_id=? ORDER BY rowid DESC LIMIT 6", (aid,)).fetchall()
            latest = self.quality_audit_summary(db, rows[0], changed=True) if rows else None
            return {"audit": latest, "history": [self.quality_audit_summary(db, row, brief=True) for row in rows[1:]],
                    "model": (config.rubric_reviewer(self.settings()["settings"]) or {}).get("name")}

    def quality_audit_summary(self, db, row, *, brief=False, changed=False):
        context, profile = json.loads(row["input_json"]), json.loads(row["profile_json"])
        requests = sum(-(-len(q["responses"]) // CHUNK) for q in context["questions"])
        done = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE quality_audit_id=? AND state='completed' AND step NOT LIKE '%:confirm:%' AND step NOT LIKE '%:sample:%'", (row["id"],)).fetchone()[0]
        checked = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE quality_audit_id=? AND state='completed' AND step LIKE '%:confirm:%'", (row["id"],)).fetchone()[0]
        sampled = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE quality_audit_id=? AND state='completed' AND step LIKE '%:sample:read'", (row["id"],)).fetchone()[0]
        summary = {"id": row["id"], "status": row["status"], "cancelled": bool(row["cancelled"]), "error": row["error"], "created_at": row["created_at"],
                   "updated_at": row["updated_at"], "model": profile["endpoint"]["name"], "rule": context["rule"], "questions": len(context["questions"]),
                   "responses": sum(len(q["responses"]) for q in context["questions"]), "progress": {"requests": requests, "read": min(done, requests), "checked": checked}}
        if context["rule"] == RULE:
            summary["progress"].update(sample_requests=sum(bool(q.get("image_sample")) for q in context["questions"]), samples_read=sampled)
        result = json.loads(row["result_json"]) if row["result_json"] else None
        if result:
            summary["totals"] = result["totals"]
        if brief or not result:
            return summary
        if changed:
            # Which responses' final grade moved since the snapshot: a finding about them may already be settled.
            now = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id,current_review_id,current_result_id FROM frq_responses WHERE assignment_id=?", (row["assignment_id"],))}
            before = {r["response_id"]: (r["review_id"], r["result_id"]) for q in context["questions"] for r in q["responses"]}
            moved = {rid for rid, pair in before.items() if now.get(rid) != pair}
            for q in result["questions"]:
                for finding in q["findings"]:
                    finding["changed"] = sorted(r["response_id"] for r in finding["refs"] if r["response_id"] in moved)
                if q.get("image_sample"):
                    q["image_sample"]["changed"] = sorted(r["response_id"] for r in q["image_sample"].get("refs", []) if r["response_id"] in moved)
        return {**summary, "result": result}

    # Worker -------------------------------------------------------------------------------------------------------------------
    def _audit_ask(self, task, endpoint, settings, step, prompt, payload, images, check):
        """One request, asked once more when its answer is refused. What was answered before is replayed, never sent again."""
        refused = None
        for attempt in (0, 1):
            reply = self.cloud_call({"quality_audit_id": task["id"], "step": step, "task_attempt": attempt}, "rubric_reviewer", endpoint,
                                    {"prompt": prompt, "payload": payload, "images": images, **({"reask": True} if attempt else {})}, settings)
            try:
                return check(reply["output"]), reply["metadata"].get("cloud_call_id")
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    def _audit_crops(self, rows):
        """(images, per ref) of the responses a finding names: each response's crops as scanned, within `IMAGES`."""
        images, counts, total = [], [], 0
        with connect(self.path) as db:
            for row in rows:
                try:
                    task = db.execute("SELECT * FROM frq_tasks WHERE id=?", (row["task_id"],)).fetchone()
                    crops = self.response_crops(db, dict(task) if task else {"id": row["task_id"]})["crops"]
                    pictures = [storage.read(self.inbox, crop["images"].get("original") or crop["images"]["cloud"]) for crop in crops]
                except (KeyError, TypeError, ValueError, NotFound, OSError):
                    pictures = []
                if total + sum(map(len, pictures)) > IMAGES:
                    break
                images.extend(pictures)
                counts.append({"ref": row["ref"], "images": len(pictures),
                    **({"page_context": "Red rectangles mark the original answer areas. Read those and only visibly connected continuation; ignore other answers and identifying text."}
                       if pictures and any(c["metadata"].get("context_page") for c in crops) else {})})
                total += sum(map(len, pictures))
        return images, counts

    def _audit_question(self, task, endpoint, settings, q):
        """(entry, wait seconds or None) for one question and version: its text pass, then its scan checks."""
        known = {item["id"] for item in q["rubric"].get("items", [])}
        rows, by_ref = q["responses"], {r["ref"]: r for r in q["responses"]}
        anchors_all = []
        for row in rows:
            if row["decided_by"] == "instructor" and row["score"] not in {a["score"] for a in anchors_all}:
                anchors_all.append(row)
        anchors_all = anchors_all[:ANCHORS * 2]
        entry = {"question_id": q["question_id"], "label": q["label"], "exam_version": q["exam_version"], "responses": len(rows),
                 "summaries": [], "findings": [], "error": "",
                 # Each line's wording and points, so the page can say what a suggestion means.
                 "line_text": {item["id"]: {"text": str(item.get("description") or "")[:200], "points": item.get("points")} for item in q["rubric"].get("items", [])}}
        try:
            if q.get("image_sample"):
                # The reader gets images and the approved rubric, but no transcript, answer or saved grade. Its blind
                # assessment is retained before any comparison with that grade, even when the ordinary text audit finds nothing.
                selected = [by_ref[ref] for ref in q["image_sample"]]
                sample = entry["image_sample"] = {"selected": len(selected), "read": 0, "refs": [ref_view(r) for r in selected], "error": ""}
                try:
                    if endpoint["modality"] != "vision":
                        raise ValueError("The audit model cannot read images.")
                    images, counts = self._audit_crops(selected)
                    shown = [by_ref[c["ref"]] for c in counts if c["images"]]
                    if len(shown) != len(selected):
                        raise ValueError("The selected scans could not all be shown within the image limit.")
                    refs = {r["ref"] for r in shown}
                    blind, _ = self._audit_ask(task, endpoint, settings, f"audit:{q['question_id']}:sample:read", "quality_audit_blind",
                        {"question": q["question"], "rubric": q["rubric"], "images_per_ref": counts}, images,
                        lambda output: blind_readings(output, refs, known))
                    sample.update(read=len(blind), readings=blind)
                    (summary, findings), _ = self._audit_ask(task, endpoint, settings, f"audit:{q['question_id']}:sample:compare", "quality_audit_sample",
                        {"question": q["question"], "rubric": q["rubric"], "blind_readings": blind, "responses": [model_row(r) for r in shown]}, [],
                        lambda output: findings_of(output, refs, set(), known))
                    sample["summary"] = summary
                    entry["findings"].extend({**finding, "source": "image_sample"} for finding in findings)
                except (ProviderError, ValueError, TypeError, KeyError) as failure:
                    sample["error"] = f"The image sample could not be completed: {str(failure)[:300]}"
            for start in range(0, len(rows), CHUNK):
                chunk = rows[start:start + CHUNK]
                refs = {r["ref"] for r in chunk}
                anchors = [a for a in anchors_all if a["ref"] not in refs][:ANCHORS] if len(rows) > CHUNK else []
                payload = {"question": q["question"], "rubric": q["rubric"], "responses": [model_row(r) for r in chunk],
                           **({"anchors": [model_row(a) for a in anchors]} if anchors else {})}
                (summary, found), _ = self._audit_ask(task, endpoint, settings, f"audit:{q['question_id']}:{start // CHUNK}", "quality_audit", payload, [],
                                                      lambda output: findings_of(output, refs, {a["ref"] for a in anchors}, known))
                if summary:
                    entry["summaries"].append(summary)
                entry["findings"].extend(found)
            entry["findings"].sort(key=lambda f: SEVERITY[f["severity"]])
            for n, finding in enumerate(entry["findings"]):
                if n >= MAX_CONFIRM:
                    finding["confirm"] = {"skipped": f"Only the first {MAX_CONFIRM} findings of a question are checked against the scans."}
                    continue
                if endpoint["modality"] != "vision":
                    finding["confirm"] = {"skipped": f"{endpoint['name']} cannot read images, so this finding was not checked against the scans."}
                    continue
                named = [by_ref[r] for r in finding["refs"][:CONFIRM_REFS]]
                images, counts = self._audit_crops(named)
                if not images:
                    finding["confirm"] = {"skipped": "The scans of these responses could not be read."}
                    continue
                shown = [by_ref[c["ref"]] for c in counts if c["images"]]
                payload = {"question": q["question"], "rubric": q["rubric"],
                           "finding": {k: finding[k] for k in ("kind", "severity", "refs", "lines", "explanation", "suggestions")},
                           "responses": [model_row(r) for r in shown], "images_per_ref": counts}
                try:
                    finding["confirm"], _ = self._audit_ask(task, endpoint, settings, f"audit:{q['question_id']}:confirm:{n}", "quality_audit_confirm", payload, images,
                                                            lambda output: confirmation_of(output, {r["ref"] for r in shown}, known))
                    finding["confirm"]["shown_refs"] = [c["ref"] for c in counts if c["images"]]
                except (ProviderError, ValueError) as failure:
                    finding["confirm"] = {"skipped": f"The scan check could not be completed: {str(failure)[:300]}"}
        except RetryLater as waiting:
            return None, waiting.seconds
        except (ProviderError, ValueError, TypeError, KeyError) as failure:
            entry["error"] = f"This question could not be audited: {str(failure)[:400]}"
        for finding in entry["findings"]:
            finding["refs"] = [ref_view(by_ref[r]) for r in finding["refs"]]
            finding["suggestions"] = [with_row(s, by_ref) for s in finding["suggestions"]]
            confirm = finding.get("confirm") or {}
            if confirm.get("suggestions"):
                confirm["suggestions"] = [with_row(s, by_ref) for s in confirm["suggestions"]]
        return entry, None

    def run_quality_audit(self, task):
        context, profile = json.loads(task["input_json"]), json.loads(task["profile_json"])
        endpoint, settings = profile["endpoint"], profile["settings"]
        result, error, status, after = None, "", "complete", ""
        try:
            questions = context["questions"]
            with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(questions)))) as pool:
                outcomes = list(pool.map(lambda q: self._audit_question(task, endpoint, settings, q), questions))
            waits = [seconds for _, seconds in outcomes if seconds is not None]
            if waits:
                status, after = "pending", later(min(waits))
            else:
                entries = [entry for entry, _ in outcomes]
                checks = audit_checks(questions)
                findings = [f for e in entries for f in e["findings"]]
                confirmed = sum((f.get("confirm") or {}).get("verdict") in ("confirmed", "partly") for f in findings)
                refuted = sum((f.get("confirm") or {}).get("verdict") == "not_confirmed" for f in findings)
                failed = [e["label"] for e in entries if e["error"]]
                totals = {"findings": len(findings), "confirmed": confirmed, "not_confirmed": refuted, "checks": len(checks),
                          "high": sum(f["severity"] == "high" for f in findings), "questions_failed": len(failed)}
                result = {"rule": context["rule"], "totals": totals, "checks": checks, "questions": entries,
                          "summary": (f"{len(findings)} finding{'' if len(findings) == 1 else 's'} across {len(entries)} question{'' if len(entries) == 1 else 's'}"
                                      f"; {confirmed} held up against the scans, {refuted} did not. {len(checks)} automatic check{'' if len(checks) == 1 else 's'} to look at."
                                      + (f" {len(failed)} question{'' if len(failed) == 1 else 's'} could not be audited." if failed else ""))}
                if context["rule"] == RULE:
                    samples = [e["image_sample"] for e in entries if e.get("image_sample")]
                    totals.update(sample_selected=sum(s["selected"] for s in samples), sample_read=sum(s["read"] for s in samples),
                                  sample_failed=sum(bool(s["error"]) for s in samples),
                                  versions_unmapped=sum(bool(q["exam_version"] and not q.get("family")) for q in questions))
                    result["summary"] += f" {totals['sample_read']} of {totals['sample_selected']} sampled automatic grades read from images before their recorded grades were shown."
                    if totals["sample_failed"]:
                        result["summary"] += f" {totals['sample_failed']} image sample(s) could not be completed."
                    if totals["versions_unmapped"]:
                        result["summary"] += f" {totals['versions_unmapped']} versioned question(s) have no confirmed counterpart and were not compared across versions."
                if failed and len(failed) == len(entries):
                    status, error, result = "failed", entries[0]["error"], None
        except RetryLater as waiting:
            status, after = "pending", later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError) as failure:
            status, error = "failed", f"The audit could not be completed: {failure}"
        with self.rosters.lock(), connect(self.path) as db:
            published = db.execute("UPDATE frq_quality_audits SET status=?,result_json=?,error=?,not_before=?,updated_at=? WHERE id=? AND status='running'",
                                   (status, encoded(result) if result else None, error, after, timestamp(), task["id"]))
            if status == "complete" and published.rowcount:
                self.event(db, task["assignment_id"], "quality_audit_ready", {"audit_id": task["id"], **(result or {}).get("totals", {})}, "worker")
