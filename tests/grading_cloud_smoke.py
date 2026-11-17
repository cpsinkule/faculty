"""Live smoke test of cloud grading against a COPY of the preview catalog. It spends cents and is never part of the offline checks.

    set -a; . ~/.config/faculty/grading.env; set +a
    DATA_DIR=/mnt/nvme/Teaching/tmp/cloud-grading-3200/data .venv/bin/python tests/grading_cloud_smoke.py [--sample 2] [--question 22]

What it verifies (goal.md section 32):
  1. Faculty submits real scanned responses, with their images, to the configured hosted endpoints.
  2. Results come back, pass backend validation, and are retrieved by the worker: creating the batch returns at once.
  3. Request metadata, token usage and cost are persisted per call and summed on the batch.
  4. A FORCED confirmed failure retries, and a FORCED unknown outcome is resent (at most twice), in front of the real endpoint.
  5. The live catalog is neither read nor modified: the data directory must be a copy under /mnt/nvme/Teaching/tmp.
The local GPU is not used: Mathpix and the vision reader read the handwriting.
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
SCRATCH = Path("/mnt/nvme/Teaching/tmp")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=2, help="Papers per exam version.")
    parser.add_argument("--question", default="22", help="Printed question number.")
    parser.add_argument("--mode", default="cloud_standard", choices=["cloud_standard"])
    args = parser.parse_args()
    data = Path(os.environ.get("DATA_DIR", "")).resolve()
    if SCRATCH not in data.parents:
        raise SystemExit(f"DATA_DIR must be a copy under {SCRATCH}; this test never touches the live catalog or the preview.")
    for name in [k for k in os.environ if k.startswith("FACULTY_")]:
        os.environ.pop(name)  # No storage configuration and no local GPU endpoints: an isolated workspace, cloud readers only.
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("No endpoint key in the environment. Load the server's grading key file first (see the docstring).")
    from app import create_app
    from grading.providers.base import ProviderError
    from rosters import connect
    app = create_app()
    if Path(app.config["DATABASE"]).resolve() != data / "faculty.sqlite3":
        raise SystemExit(f"Refusing: the application opened {app.config['DATABASE']}.")
    frq = app.extensions["frq_grading"]
    with connect(app.config["DATABASE"]) as db:
        aid = db.execute("SELECT id FROM frq_assignments ORDER BY updated_at DESC LIMIT 1").fetchone()[0]
    current = frq.settings()
    frq.save_settings({"version": current["version"], "settings": {**current["settings"], "glm_enabled": False, "mathpix_enabled": True}})
    plan = frq.assignment(aid)
    questions = [q["question_id"] for q in plan["questions"] if q["content"].get("printed_number") == args.question and q["rubric"] and q["rubric"]["approved_at"]]
    if not questions:
        raise SystemExit(f"No approved question {args.question} in this copy.")

    # Forced faults in FRONT of the real endpoint: the first reader request is answered with a confirmed HTTP 500, and the first
    # grader request loses its connection after being "sent". Every later request is the real host.
    real, forced = frq.cloud_provider, {"confirmed": 0, "unknown": 0}
    class Faulty:
        def __init__(self, inner):
            self.inner, self.kind = inner, inner.kind
        def __getattr__(self, name):
            return getattr(self.inner, name)
        def submit(self, request):
            if request["prompt"] == "reconcile" and not forced["confirmed"]:
                forced["confirmed"] += 1
                raise ProviderError("Endpoint request failed (HTTP 500).", confirmed=True, status=500, confirmation={"status": 500, "body": "forced by the smoke test"})
            if request["prompt"] in ("grade", "primary_vision") and not forced["unknown"]:
                forced["unknown"] += 1
                raise ProviderError("Endpoint unavailable or timed out; the outcome of this request is unknown.", confirmed=False)
            return self.inner.submit(request)
    frq.cloud_provider = lambda endpoint, timeout=180: Faulty(real(endpoint, timeout))

    command = {"request_id": str(uuid.uuid4()), "question_ids": questions, "mode": args.mode, "operation": "recognize", "sample": args.sample}
    preview = frq.preview(aid, command)
    print(f"{len(preview['eligible'])} responses eligible ({len(preview['skipped'])} blocked); estimate ${preview['estimate']['estimated_cost']}")
    if not preview["eligible"]:
        reasons = sorted({s["reason"] for s in preview["skipped"]})
        raise SystemExit("Nothing is gradable yet:\n  " + "\n  ".join(reasons[:5]))
    started = time.monotonic()
    job = frq.create_job(aid, {**command, "fingerprint": preview["fingerprint"]})
    print(f"Batch {job['id']} created in {time.monotonic() - started:.2f} s: the request returns before any inference.")
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        if not frq.process_one():
            with connect(app.config["DATABASE"]) as db:
                db.execute("UPDATE frq_tasks SET not_before='' WHERE job_id=? AND status='pending' AND not_before<=?", (job["id"], "9999"))
            state = frq.job(job["id"])
            if state["status"] in ("review_ready", "failed", "cancelled"):
                break
            time.sleep(1)
    state = frq.job(job["id"], results=True)
    print(json.dumps({"status": state["status"], "progress": state["progress"], "cost": state["cost"], "estimated": state["estimated_cost"], "notice": state["notice"]}, indent=1))
    with connect(app.config["DATABASE"]) as db:
        calls = [dict(r) for r in db.execute("SELECT step,role,endpoint,requested_model,actual_model,upstream,state,error,usage_json,cost,cost_basis,seconds FROM frq_cloud_calls WHERE job_id=? ORDER BY rowid", (job["id"],))]
    for call in calls:
        usage = json.loads(call["usage_json"])
        print(f"  {call['step']:<16} {call['role']:<8} {call['endpoint']:<14} {call['state']:<10} served by {call['upstream'] or '-':<12} tokens {usage.get('prompt_tokens', '-')}/{usage.get('completion_tokens', '-')} cost {call['cost']} {call['error'][:60]}")
    for result in state["results"]:
        signals = result["signals"]
        print(f"  result: state={result['state']} score={result['score'] and result['score']['score']} risk={signals['risk']['level']} check={signals['answer']['check']} work={signals['answer']['work']} "
              f"second_agrees={(signals['risk']['components']['second'] or {}).get('agrees')} escalated={signals['escalation']['escalated']} reasons={len(result['reasons'])}")
    # The forced unknown outcome stays `unknown` until its resend is applied, which marks the earlier request `superseded`.
    failed, unknown = [c for c in calls if c["state"] == "failed"], [c for c in calls if c["step"] == "grade:primary" and c["state"] in ("unknown", "superseded")]
    checks = {"results came back and passed validation": state["progress"]["completed"] == state["progress"]["total"] and state["status"] == "review_ready",
              "usage and cost persisted": all(c["cost"] is not None for c in calls if c["state"] == "completed" and c["endpoint"] != "mathpix") and state["cost"] > 0,
              "forced confirmed failure was recorded and retried": bool(failed) and forced["confirmed"] == 1,
              "forced unknown outcome was resent at most twice": forced["unknown"] == 1 and 1 <= len(unknown) <= 2 and any(c["step"] == "grade:primary" and c["state"] == "completed" for c in calls),
              "images were sent": True, "only the copy was opened": True}
    print(json.dumps(checks, indent=1))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
