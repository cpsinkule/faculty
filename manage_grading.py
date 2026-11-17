#!/usr/bin/env python3
"""Measure grading on a local, instructor-verified FRQ dataset. It reads the dataset file only, never a catalog.

    manage_grading.py benchmark DATASET [--predictions FILE ...] [--baseline qwen3-vl-32b] [--split test] [--output REPORT]
        every retained profile and every prediction file against the instructor's approved work: reading and grading
        metrics with 95% intervals, the error breakdown, and each challenger against the baseline run of the same role
        and transcript source on the SAME responses (McNemar's exact test; a failed attempt counts as wrong).
    manage_grading.py run-endpoint DATASET --endpoint NAME --output FILE [--role primary|second] [--transcript proposal|human] [--split test]
        the same responses through ONE configured hosted endpoint (its key comes from the variable the endpoint names).
        One variable at a time: the model, or the transcript it is given. Costs cents per hundred responses.
        Anything tuned on these corrections is evaluated with --split test: students it never saw.
    manage_grading.py finetune-export DATASET --output FILE [--target grader|reader] [--split train|calibration|test] [--labels instructor|all]
        graded work as supervised examples for one STUDENT-level split: your decisions, and with --labels all the standing
        automatic grades too (each line says which). It trains nothing.
    manage_grading.py splits DATASET
        how many students land in each split.

Export the dataset from Export -> Saved for training -> What the files hold -> "Model comparison set", or download every graded
response from Export -> Saved for training (one assignment or all of them). Benchmarks read only your own decisions from either.
"""
import argparse
import json
from pathlib import Path
import sys

from grading import benchmark as statistics, config, evaluate


def benchmark(path, predictions=(), baseline=None, split=None):
    return evaluate.report(path, predictions, baseline, split)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["benchmark", "run-endpoint", "finetune-export", "splits"])
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, help="Create a new file; existing files are never overwritten.")
    parser.add_argument("--predictions", type=Path, nargs="*", default=[], help="Prediction files made by run-endpoint.")
    parser.add_argument("--baseline", default="qwen3-vl-32b", help="The profile, endpoint or model every challenger is compared with (exact name).")
    parser.add_argument("--endpoint", help="Name of a hosted endpoint (from --settings, else the built-in defaults).")
    parser.add_argument("--settings", type=Path, help="A JSON file with grading settings (the `settings` object of /api/grading/settings).")
    parser.add_argument("--role", choices=["primary", "second"], default="primary")
    parser.add_argument("--transcript", choices=["proposal", "human"], default="proposal")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--target", choices=["grader", "reader"], default="grader")
    parser.add_argument("--labels", choices=["instructor", "all"], default="instructor",
                        help="finetune-export: your decisions only, or also the standing automatic grades of a training download.")
    parser.add_argument("--split", choices=[name for name, _ in statistics.SPLITS],
                        help="One student-level split. finetune-export writes `train` unless told otherwise; benchmark and run-endpoint read every split unless told.")
    args = parser.parse_args()
    if args.command == "benchmark":
        result = benchmark(args.dataset, args.predictions, args.baseline, args.split)
    elif args.command == "splits":
        students = {row["student"]: statistics.record_split(row) for row in evaluate.load(args.dataset)["records"] if row.get("student")}
        result = {"students": len(students), **{name: sum(split == name for split in students.values()) for name, _ in statistics.SPLITS}}
    elif args.command == "finetune-export":
        if not args.output:
            parser.error("finetune-export needs --output")
        result = evaluate.finetune_export(args.dataset, args.output, args.target, args.split or "train", args.labels)
        print(json.dumps(result, indent=2))
        return
    else:
        if not args.endpoint or not args.output:
            parser.error("run-endpoint needs --endpoint and --output")
        settings = config.validate(json.loads(args.settings.read_text()) if args.settings else {})
        endpoint = config.named_endpoint(settings, args.endpoint)
        result = evaluate.run_endpoint(args.dataset, endpoint, role=args.role, transcript=args.transcript, limit=args.limit, workers=args.workers, timeout=settings["timeout"],
            split=args.split, progress=lambda done, total, prediction: print(f"\r{done}/{total}", end="", file=sys.stderr, flush=True))
        print(file=sys.stderr)
        failed = sum("failed" in p for p in result["predictions"].values())
        cost = sum(p.get("cost") or 0 for p in result["predictions"].values())
        print(f"{len(result['predictions'])} responses on {endpoint['name']} ({endpoint['model']}): {failed} failed, ${cost:.4f} recorded.", file=sys.stderr)
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        with args.output.open("x") as output:
            output.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
