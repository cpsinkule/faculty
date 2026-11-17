"""Local Teaching Inbox command client and independent queue worker."""
import argparse
import getpass
import json
import logging
import signal
import threading
import uuid
from pathlib import Path

from app import create_app
from teach_inbox.protocol import MAX_FILE, parse_command
from teach_inbox.service import sha


# The worker's cloud lane runs up to 256 components at once (5,000 with Full concurrency), each holding a connection, catalog
# handles, a lock file or a sandbox's pipes. At the default soft limit of 1,024 open files a 2,169-response trial batch failed
# 1,011 responses in ten seconds with "Too many open files" (2026-09-24), while the hard limit allowed 1,048,576.
OPEN_FILES = 65536


def raise_open_files(wanted=OPEN_FILES):
    """Raise this process's soft limit on open files towards `wanted`, never past the hard limit; returns the limit now in force."""
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
    if soft != resource.RLIM_INFINITY and soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Isolated development workspace; otherwise use configured home-server storage.")
    commands = parser.add_subparsers(dest="operation", required=True)
    submit = commands.add_parser("submit", help="Retain a command and attachments, then queue it.")
    submit.add_argument("--command", type=Path, required=True, help="JSON or YAML command file.")
    submit.add_argument("--attach", type=Path, action="append", default=[])
    submit.add_argument("--idempotency-key", default=None)
    scan = commands.add_parser("scan", help="Retain scanned PDFs already on this server (up to 512 MB each) for page inspection and instructor review.")
    scan.add_argument("files", type=Path, nargs="+")
    worker = commands.add_parser("worker", help="Run the durable job worker.")
    worker.add_argument("--once", action="store_true")
    commands.add_parser("jobs", help="List recent jobs and worker status.")
    job = commands.add_parser("show", help="Show a job and its audit trail.")
    job.add_argument("id")
    key = commands.add_parser("api-key", help="Create an Inbox API key; prints its secret once.")
    key.add_argument("label")
    faint = commands.add_parser("faint-marks", help="Read again the pages of a scan inspected before FAINT MARKS that showed no code (DOCUMENTS); a dry run unless --write.")
    faint.add_argument("message_id")
    faint.add_argument("--write", action="store_true", help="Give each page whose code now reads that code's identity.")
    blank = commands.add_parser("blank-sides", help="Measure again the unlabeled sides of a scan inspected before GREY PAPER (DOCUMENTS); a dry run unless --write.")
    blank.add_argument("message_id")
    blank.add_argument("--write", action="store_true", help="Make each empty side a blank side.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.operation == "worker":
        limit = raise_open_files()
        logging.getLogger(__name__).info("Worker open-file limit: %s", limit)
    app = create_app(args.data_dir)
    inbox = app.extensions["inbox"]
    if args.operation == "worker":
        if args.once:
            print(json.dumps({"processed": inbox.work_once()}))
        else:
            stop = threading.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
            inbox.worker(stop)
        return 0
    if args.operation == "submit":
        raw = args.command.read_bytes()
        command = parse_command(raw.decode("utf-8-sig"))
        files = []
        for path in args.attach:
            with path.open("rb") as handle:
                files.append({"filename": path.name, "content": handle.read(MAX_FILE + 1)})
        result = inbox.submit(command, files, source="cli", sender=getpass.getuser(), original=raw,
                              idempotency_key=args.idempotency_key or str(uuid.uuid4()))
    elif args.operation == "scan":
        # Like the scanner address: no command text, and review stays required. One submission per file, keyed by
        # its content, so running the same import twice retains nothing new.
        result = []
        for path in args.files:
            content = path.read_bytes()
            job = inbox.submit({"action": "scan.receive"}, [{"filename": path.name, "content": content}], source="cli", sender=getpass.getuser(),
                               idempotency_key=f"scan:{sha(content)}", instructor_review_required=True, large_scans=True)
            result.append({"file": path.name, "job_id": job["id"], "message_id": job["message_id"], "status": job["status"]})
    elif args.operation == "show":
        result = inbox.get(args.id)
    elif args.operation == "api-key":
        result = inbox.create_api_key(args.label)
    elif args.operation == "faint-marks":
        result = app.extensions["document_scans"].read_faint_marks(args.message_id, write=args.write, actor=f"cli:{getpass.getuser()}")
    elif args.operation == "blank-sides":
        result = app.extensions["document_scans"].read_blank_sides(args.message_id, write=args.write, actor=f"cli:{getpass.getuser()}")
    else:
        result = inbox.listing()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
