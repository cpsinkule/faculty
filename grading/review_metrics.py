"""Read-only measurements of flags on current instructor-reviewed term responses.

One current final decision per response; separate frozen profiles and blind/seen
samples. This does not tune thresholds, train a model or grant acceptance.
"""
import json
from . import participation
from .models import digest
from .risk import selection_disagreement


def signal_report(db, aid):
    revealed = {json.loads(row[0]).get('result_id') for row in db.execute(
        "SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='blind_revealed'", (aid,))}
    rows = db.execute(f"""SELECT x.id result_id,x.state,x.signals_json,x.reasons_json,x.transcript_json proposed_text,
        x.applications_json proposed_items,x.score_json proposed_score,v.transcript_json reviewed_text,
        v.applications_json reviewed_items,v.score_json reviewed_score,v.status,
        f.reasoning_wrong,j.profile_json,j.tier,j.mode,t.question_revision_id,t.rubric_id,q.question_key,q.exam_version,
        ar.blind accepted_blind
        FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id
        JOIN frq_reviews v ON v.id=r.current_review_id AND v.result_id=x.id AND v.status IN ('approved','manual') AND NOT {participation.APPROVAL}
        JOIN frq_tasks t ON t.id=x.task_id JOIN frq_jobs j ON j.id=t.job_id JOIN frq_questions q ON q.id=r.question_id
        LEFT JOIN frq_review_feedback f ON f.review_id=v.id
        LEFT JOIN frq_all_acceptances a ON a.result_id=x.id
        LEFT JOIN frq_all_acceptance_reviews ar ON ar.acceptance_id=a.id AND ar.review_id=v.id
        WHERE r.assignment_id=? ORDER BY q.question_key,x.rowid""", (participation.APPROVED, aid)).fetchall()
    groups = {}
    selected = lambda raw: sorted(a["rubric_item_id"] for a in json.loads(raw) if a["applied"])
    for row in rows:
        signals = json.loads(row["signals_json"])
        risk = signals.get("risk") or {}
        # A blind acceptance revealed before the decision cannot count as independent.
        blind = bool(row["accepted_blind"] and row['result_id'] not in revealed)
        key = digest([row["question_revision_id"], row["rubric_id"], row["profile_json"], risk.get("policy", "legacy"), blind])
        g = groups.setdefault(key, {"scope": key[:10], "question": row["question_key"], "exam_version": row["exam_version"],
            "tier": signals.get("tier") or row["tier"] or row["mode"], "profile": digest(row["profile_json"])[:10],
            "policy": risk.get("policy", "legacy"), "sample": "independent" if blind else "proposal visible or independence unconfirmed",
            "reviewed": 0, "changed": 0, "flags": {}, "confidence": {}})
        before_score = json.loads(row["proposed_score"]) if row["proposed_score"] else None
        after_score = json.loads(row["reviewed_score"]) if row["reviewed_score"] else None
        changed = bool(row["status"] != "approved" or row["reasoning_wrong"]
                       or selected(row["proposed_items"]) != selected(row["reviewed_items"])
                       or json.loads(row["proposed_text"]) != json.loads(row["reviewed_text"])
                       or before_score != after_score)
        g["reviewed"] += 1
        g["changed"] += changed
        c = risk.get("components") or {}
        confidence = (c.get("primary") or {}).get("confidence", signals.get("grading_confidence"))
        bucket = "Not recorded" if confidence is None else "Below 0.85" if confidence < .85 else "0.85–0.89" if confidence < .9 else "0.90–0.94" if confidence < .95 else "0.95–1.00"
        cell = g["confidence"].setdefault(bucket, {"reviewed": 0, "changed": 0})
        cell["reviewed"] += 1
        cell["changed"] += changed
        # Category-level counts avoid grouping by model-authored prose or exposing student text.
        labels = set()
        if row["state"] in ("ocr_review", "both"):
            labels.add("Reading needs review")
        if row["state"] in ("grading_review", "both"):
            labels.add("Grading needs review")
        if (c.get("ocr") or {}).get("disagreements"):
            labels.add("Unresolved OCR disagreement")
        if (c.get("rubric") or {}).get("exceptions"):
            labels.add("Rubric condition needs judgment")
        disagreement = selection_disagreement(c)
        if disagreement:
            labels.add("Graders disagree on rubric selections")
            if not disagreement.get("same_score", False):
                labels.add("Graders disagree on score")
        if (c.get("symbolic") or {}).get("fail") and (c.get("answer") or {}).get("full_credit"):
            labels.add("Full credit conflicts with checked mathematics")
        if (c.get("verifier") or {}).get("material"):
            labels.add("Additional reviewer objects")
        if not labels:
            labels.add("No listed review flag")
        for label in labels:
            cell = g["flags"].setdefault(label, {"reviewed": 0, "changed": 0})
            cell["reviewed"] += 1
            cell["changed"] += changed
    return {"reviewed": len(rows), "groups": list(groups.values()),
            "note": "Current reviewed responses only, counted once. Changed includes corrections to text, rubric selections, points or reasoning. Unchanged approval does not prove that a flag was unnecessary. Categories overlap; samples can be selected and small. These observations do not calibrate model confidence or change review thresholds."}
