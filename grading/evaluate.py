"""The Phase 0 measurement loop on an exported, instructor-graded dataset. Offline except for the endpoint it is asked to try.

    1. grade a sample with the current pipeline and review it (blind reviews of trial acceptances are the independent labels);
    2. Export -> Saved for training -> What the files hold -> "Model comparison set";
    3. manage_grading.py benchmark DATASET                      the error breakdown and every retained profile;
    4. manage_grading.py run-endpoint DATASET --endpoint NAME    the SAME responses through one hosted endpoint,
       one variable changed at a time (the model, the transcript it is given);
    5. manage_grading.py benchmark DATASET --predictions a.json b.json --baseline qwen3-vl-32b
       every comparison against the default primary grader, with a confidence interval on the same responses;
    6. evaluate anything trained on these corrections with --split test only: the students it never saw.

It never opens a catalog: the dataset file is its only input, and keys come from the environment variable each
endpoint names. It never trains and never writes a grade anywhere; predictions are files for the benchmark.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import zipfile

from . import benchmark, recognition
from .metrics import report as reading_report
from .models import encoded
from .providers import cloud as providers
from .providers.base import ProviderError

LIMIT = 256 * 1024**2  # A whole workspace's training data lists every graded response (about 10 KB each, images apart).
TRAINING = "faculty-frq-training-v1"  # `training.FORMAT`: the "Saved for training" download.


def load(path):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            item = archive.getinfo("dataset.json")
            if item.file_size > LIMIT:
                raise ValueError("Dataset manifest exceeds the benchmark limit.")
            dataset = json.loads(archive.read(item))
    elif path.stat().st_size > LIMIT:
        raise ValueError("Dataset exceeds the benchmark limit.")
    else:
        dataset = json.loads(path.read_text())
    if dataset.get("format") == TRAINING:
        # Training data lists each question and rubric revision once; a record names the ones it was graded under.
        for record in dataset["records"]:
            record.setdefault("question", dataset["questions"][record["question_revision_id"]])
            record.setdefault("rubric", dataset["rubrics"][record["rubric_id"]])
    return dataset


def labelled(records, labels="instructor", one_per_response=False):
    """The records whose final grade may be learned from or measured against, in their order.

    An evaluation dataset holds the instructor's current decisions only. Training data holds every decision: those a later decision on
    the same response, rubric and paper superseded are never used; automatic grades (`label` automatic, which nobody looked at) are used
    only with `labels="all"`, and only while their acceptance stands, so they are never evaluation truth; and only a decision with a
    score (approved, manual or automatic) teaches a grade. `one_per_response` keeps a response's latest, for comparisons keyed by response."""
    kept = []
    for record in records:
        automatic = record.get("label", "instructor") == "automatic"
        if record.get("superseded_by") or (automatic and (labels != "all" or record.get("standing") is False)):
            continue
        if record.get("status", "approved") not in ("approved", "manual", "automatic") or not (record.get("final") or {}).get("score"):
            continue
        kept.append(record)
    if one_per_response:
        latest = {record["response_id"]: record for record in kept}
        kept = [record for record in kept if latest[record["response_id"]] is record]
    return kept


def payload(record, transcript):
    content = record["question"]
    rules = record["rubric"].get("rules", record["rubric"])
    return {"response_id": record["response_id"], "transcript": transcript, "rubric": rules, "problem": content.get("problem", ""),
        "solution": {k: content.get(k, "") for k in ("coordinator_solution", "instructor_solution", "worked_solution", "expected_answer", "acceptable_methods")},
        "answer_parts": [part["label"] for part in content.get("answer_key", [])] or [""], "calibration_examples": content.get("examples", [])}


def in_split(records, split):
    """The records of one student-level split (`benchmark.record_split`), or all of them."""
    return [r for r in records if split is None or benchmark.record_split(r) == split]


def run_endpoint(dataset_path, endpoint, *, role="primary", transcript="proposal", limit=None, workers=8, timeout=180, progress=None, split=None):
    """Grade every record (of one student-level split, if given) on one endpoint. transcript: `proposal` (the retained machine reading:
    end to end) or `human` (the instructor's verified reading: grading alone). Returns {response_id: prediction} in the shape the benchmark reads."""
    dataset = load(dataset_path)
    records = in_split(labelled(dataset["records"], one_per_response=True), split)
    records = records[:limit] if limit else records
    provider = providers.provider(endpoint, timeout)
    archive = zipfile.ZipFile(dataset_path) if str(dataset_path).lower().endswith(".zip") else None

    def one(record):
        reading = record["proposal"]["transcript"] if transcript == "proposal" else record["final"]["transcript"]
        images = [archive.read(name) for name in record.get("images", [])] if archive and endpoint["modality"] == "vision" else []
        prompt = "grade" if role == "primary" else "second_vision" if images else "transcript_grade"
        started, last = time.monotonic(), None
        for _ in range(3):
            try:
                result = provider.submit({"prompt": prompt, "payload": payload(record, reading), "images": images, "durable": False})["result"]
                rules = record["rubric"].get("rules", record["rubric"])
                graded = recognition.validate_grade(result["output"], reading, rules, record["question"]["max_points"], max(1, len(images)))
                cost, basis = provider.cost(result["metadata"].get("usage") or {})
                return record["response_id"], {"transcript": reading, "applications": graded["applications"], "score": graded["score"], "interpretation": graded["interpretation"],
                    "state": "grading_review" if graded["review_reasons"] or graded["reading_issues"] else "proposed", "reading_issues": graded["reading_issues"],
                    "input_mode": "images_and_transcript" if images else "transcript_only", "seconds": round(time.monotonic() - started, 2), "cost": cost, "cost_basis": basis,
                    "usage": result["metadata"].get("usage"), "model": result["metadata"].get("reported_model"), "upstream": result["metadata"].get("upstream")}
            except ProviderError as error:
                last = str(error)
                # PIPELINE: an uncertain accepted request needs an explicit retry, including an offline comparison run.
                if error.confirmed is False or error.fixable is False:
                    break
            except (ValueError, TypeError, KeyError, AttributeError):
                last = "The answer failed its output contract."
        return record["response_id"], {"transcript": reading, "failed": last, "malformed": True, "seconds": round(time.monotonic() - started, 2)}

    found = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, endpoint["max_inflight"]))) as pool:
            for index, (response_id, prediction) in enumerate(pool.map(one, records), 1):
                found[response_id] = prediction
                if progress:
                    progress(index, len(records), prediction)
    finally:
        if archive:
            archive.close()
    return {"format": "faculty-frq-predictions-v1", "endpoint": endpoint["name"], "model": endpoint["model"], "lineage": endpoint["lineage"], "role": role,
            "transcript": transcript, "split": split, "predictions": found}


def retained_profile(name):
    """What a retained pipeline profile is (`ReviewMixin.comparisons`): `<compute> / <model> / <settings> / grader` grades end to end
    from its own machine reading; its reading-only candidates (`... only`, `verifier`, `same-call reading`) grade nothing."""
    parts = name.split(" / ")
    if len(parts) >= 4 and parts[-1] == "grader":
        return {"endpoint": parts[1], "model": parts[1], "role": "primary", "transcript": "proposal"}
    if name == "retained_pipeline":
        return {"endpoint": name, "model": name, "role": "primary", "transcript": "proposal"}
    return None


def report(dataset_path, prediction_files=(), baseline=None, split=None):
    """Every profile's reading and grading metrics, the error breakdown of the retained pipeline, and each challenger against the baseline.

    The baseline is named exactly (its profile, endpoint or model), and each challenger is compared with the baseline run of the SAME
    role and transcript source: a grader given the instructor's reading is never measured against one given the machine's. With
    `split`, only that student-level split is read."""
    dataset = load(dataset_path)
    rows = in_split(labelled(dataset["records"], one_per_response=True), split)
    groups, lineages, about = {}, {}, {}
    for row in rows:
        candidates = dict(row.get("predictions") or {"retained_pipeline": row["proposal"]})
        rubric = row["rubric"].get("rules", row["rubric"])
        extra = {"would_accept": row.get("would_accept"), "ocr_disagreements": row.get("ocr_disagreements"), "unregistered": row.get("unregistered"),
                 "rubric_ambiguities": (row.get("proposal") or {}).get("signals", {}).get("rubric_ambiguities")}
        for profile, proposed in candidates.items():
            about.setdefault(profile, retained_profile(profile))
            groups.setdefault(profile, []).append({"expected": row["final"], "proposed": proposed, "rubric": rubric, "response_id": row["response_id"], **extra})
    records, predicted = {row["response_id"]: row for row in rows}, set()
    for name in prediction_files:
        value = json.loads(Path(name).read_text())
        profile = f"{value['endpoint']} ({value['role']}, {value['transcript']} transcript)"
        predicted.add(profile)
        lineages[profile] = value.get("lineage", profile)
        about[profile] = {"endpoint": value["endpoint"], "model": value.get("model"), "role": value["role"], "transcript": value["transcript"]}
        for response_id, proposed in value["predictions"].items():
            row = records.get(response_id)
            if row:
                groups.setdefault(profile, []).append({"expected": row["final"], "proposed": proposed if "applications" in proposed else {"transcript": proposed["transcript"]},
                    "rubric": row["rubric"].get("rules", row["rubric"]), "response_id": response_id, "malformed": proposed.get("malformed"),
                    "failed": bool(proposed.get("failed") or proposed.get("malformed")), "seconds": proposed.get("seconds"), "cost": proposed.get("cost")})
    profiles = {profile: {**reading_report(rows), "grading": benchmark.grading(rows), "errors": benchmark.breakdown(rows)} for profile, rows in groups.items()}
    graders = {profile: facts for profile, facts in about.items() if facts}
    source = lambda profile: (graders[profile]["role"], graders[profile]["transcript"])

    def named(candidates):
        """The baseline among profiles of one source: its exact profile name, else a run-endpoint file of that endpoint or model
        (the controlled run), else a retained pipeline profile of that model. Never a prefix, never the first of several."""
        for tier in ((lambda p: p == baseline), (lambda p: p in predicted and baseline in (graders[p]["endpoint"], graders[p]["model"])),
                     (lambda p: p not in predicted and baseline in (graders[p]["endpoint"], graders[p]["model"]))):
            found = [p for p in candidates if tier(p)]
            if found:
                return found
        return []
    baselines, comparisons, unmatched = {}, {}, {}
    for profile in graders:
        matches = named([other for other in graders if source(other) == source(profile)]) if baseline else []
        if profile in matches or not baseline:
            continue
        label = f"{source(profile)[0]}, {source(profile)[1]} transcript"
        if len(matches) != 1:
            unmatched[profile] = (f"No {baseline} run with the same role and transcript source ({label}) to compare with." if not matches
                                  else f"{len(matches)} {baseline} runs share this role and transcript source ({label}); name one profile exactly.")
            continue
        chosen = baselines[label] = matches[0]
        (base, base_failed), (mine, mine_failed) = benchmark.verdicts(groups[chosen]), benchmark.verdicts(groups[profile])
        comparisons[profile] = {**benchmark.paired(base, mine, base_failed, mine_failed), "baseline": chosen}
    students = {}
    for row in rows:
        if row.get("student"):
            students.setdefault(benchmark.record_split(row), set()).add(row["student"])
    return {"format": "faculty-frq-benchmark-v3", "responses": len(rows), "split": split, "profiles": profiles, "baselines": baselines,
            "against_baseline": comparisons, "not_compared": unmatched,
            "student_splits": {name: len(members) for name, members in students.items()},
            "note": "Agreement with the instructor's approved work on this reviewed sample; intervals are 95%, and a paired comparison uses McNemar's exact test. "
                    "A failed or unscorable attempt counts as a wrong answer. Keep the default when a challenger is within noise."}


def finetune_export(dataset_path, output, target="grader", split="train", labels="instructor"):
    """Graded work as supervised examples (chat JSONL with image paths), for ONE student-level split. It trains nothing.

    target `reader`: crops -> the final transcript. target `grader`: crops + transcript + rubric -> the final rubric decisions.
    labels `instructor` (default): your decisions only. `all` adds the standing automatic grades, which teach a model to agree with
    the grading models rather than with you; each line says its `label`. See `labelled`.
    Evaluate every fine-tune on the held-out `test` split before it grades anything, and give it the lineage of its base model.
    """
    dataset, written = load(dataset_path), 0
    with Path(output).open("x") as stream:
        for row in labelled(dataset["records"], labels):
            if benchmark.record_split(row) != split:
                continue
            final = row["final"]
            if target == "reader":
                readings = row["readings"] if "readings" in row else (row.get("provenance") or {}).get("ocr", [])
                example = {"images": row.get("images", []), "prompt": "reconcile", "input": {"response_id": row["response_id"], "candidates": readings},
                           "output": {"transcript": final["transcript"], "visual_confidence": 1.0}}
            else:
                decided = {a["rubric_item_id"]: a["applied"] for a in final["applications"]}
                example = {"images": row.get("images", []), "prompt": "grade", "input": payload(row, final["transcript"]),
                           "output": {"applications": [{"rubric_item_id": item, "applied": applied} for item, applied in decided.items()], "instructor_comment": final.get("comment", "")}}
            stream.write(encoded({"response_id": row["response_id"], **({"example_id": row["example_id"]} if row.get("example_id") else {}), "student": row.get("student"),
                                  "split": split, "exam_version": row.get("exam_version", ""), "label": row.get("label", "instructor"), **example}) + "\n")
            written += 1
    return {"target": target, "split": split, "labels": labels, "examples": written, "output": str(output)}
