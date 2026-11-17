"""Explicit local OCR commands; plain text/JSON only, no live Faculty mutations."""
import argparse
import json
import os
from pathlib import Path
import sys

from .client import OcrError, recognize, status
from .config import MAX_INPUT_BYTES, ROOT, TASKS


def _local_path(path):
    """Managed teaching payloads must go through their storage manager instead."""
    path = path.expanduser().resolve()
    config_path = Path(os.environ.get("FACULTY_STORAGE_CONFIG", ROOT / ".faculty-storage.json"))
    roots = [Path("/mnt/nvme/Teaching"), Path("/mnt/bulkdata2/Teaching")]
    if config_path.is_file():
        config = json.loads(config_path.read_text())
        roots += [Path(config[key]).resolve() for key in ("nvme_root", "hdd_root") if config.get(key)]
    if any(path.is_relative_to(root) for root in roots):
        raise ValueError("Managed teaching files must use recognize_artifact and the storage manager, not a direct drive path.")
    return path


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("status")
    check.add_argument("--verify-model", action="store_true")
    read = commands.add_parser("read")
    read.add_argument("input", type=Path, help="Local image or PDF; use recognize_artifact for managed Inbox files")
    read.add_argument("--task", choices=TASKS, default="text")
    read.add_argument("--page", type=int, default=1, help="One-based PDF page; one page per invocation")
    read.add_argument("--max-new-tokens", type=int, default=2048)
    read.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    read.add_argument("--timeout", type=int, default=300, help="Inference limit in seconds, at most 600")
    read.add_argument("--json", action="store_true", help="Include model, input hash and truncation metadata")
    read.add_argument("--output", type=Path, help="Create a new local result file; never overwrite an existing file")
    args = parser.parse_args()
    try:
        if args.command == "status":
            print(json.dumps(status(verify_model=args.verify_model), indent=2))
            return
        args.input = _local_path(args.input)
        if args.output:
            args.output = _local_path(args.output)
        if args.output and args.output.exists():
            raise ValueError("The output file already exists. Choose a new filename.")
        with args.input.open("rb") as source:
            content = source.read(MAX_INPUT_BYTES + 1)
        result = recognize(content, task=args.task, page=args.page,
                           max_new_tokens=args.max_new_tokens, device=args.device, timeout=args.timeout)
        text = json.dumps(result, indent=2, ensure_ascii=False) if args.json else result["text"]
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(text + "\n")
        else:
            print(text)
        if result["truncated"]:
            print("OCR reached the token limit; this result may be incomplete.", file=sys.stderr)
            return 2
    except (OcrError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
