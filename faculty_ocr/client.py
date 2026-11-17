"""Reusable OCR calls; results are transient, unapproved text, with no catalog writes."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .config import MAX_INPUT_BYTES, MAX_TOKENS, MODEL_ID, MODEL_REVISION, TASKS, runtime_root

BANDS = ("top", "bottom", "full")


class OcrError(RuntimeError):
    pass


BUSY = "Another local OCR task is running"  # callers leave their pages queued when an OcrError says this


def _manifest(root, verify=False):
    try:
        record = json.loads((root / "model-manifest.json").read_text())
        if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
            raise ValueError("invalid manifest")
        model = root / "model" / MODEL_REVISION
        if record["model_id"] != MODEL_ID or record["revision"] != MODEL_REVISION:
            raise ValueError("wrong model")
        for name, info in record["files"].items():
            if Path(name).name != name:
                raise ValueError("invalid manifest path")
            path = model / name
            if path.stat().st_size != info["size"]:
                raise ValueError("file size differs")
            if verify:
                with path.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != info["sha256"]:
                        raise ValueError("checksum differs")
        if "model.safetensors" not in record["files"]:
            raise ValueError("missing weights")
        return record, model
    except (OSError, KeyError, ValueError, TypeError) as cause:
        raise OcrError("OCR model is missing or invalid. Run python -m faculty_ocr.setup.") from cause


@contextmanager
def _exclusive(root):
    with (root / "inference.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as cause:
            raise OcrError(f"{BUSY}; try again after it completes.") from cause
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _gpu_command(root, scratch, record, model):
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--proc", "/proc", "--ro-bind", "/sys", "/sys", "--dev", "/dev",
        "--size", str(128 * 1024**2), "--tmpfs", "/tmp",
        "--ro-bind", str(Path(__file__).parent), "/app/faculty_ocr",
        "--ro-bind", str(root / "venv"), "/venv", "--ro-bind", str(model), "/model",
        "--bind", str(scratch), "/work", "--chdir", "/app"]
    base = Path(record["python_base"]).resolve()
    if not base.is_relative_to("/usr"):
        command += ["--ro-bind", str(base), str(base)]
    for path in sorted(Path("/dev").glob("nvidia*")):
        command += ["--dev-bind", str(path), str(path)]
    return command + ["--", "/venv/bin/python", "-m", "faculty_ocr.worker"]


ENVIRONMENT = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1",
               "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false",
               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "2", "HF_HOME": "/tmp/huggingface",
               "XDG_CACHE_HOME": "/tmp/cache"}


def _start(command):
    """Start an isolated process without waiting for it (the GPU reader loads its model while pages are prepared)."""
    try:
        return subprocess.Popen(command, env=ENVIRONMENT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as cause:
        raise OcrError("The isolated OCR runtime could not start. Check Bubblewrap and the OCR installation.") from cause


def _wait(process, timeout):
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as cause:
        process.kill()
        process.wait()
        raise OcrError("OCR exceeded its time limit; no result was accepted.") from cause
    if code:
        raise OcrError("The isolated OCR process failed or exceeded its resource limits.")


def _run(command, timeout):
    env = ENVIRONMENT
    try:
        result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as cause:
        raise OcrError("OCR exceeded its time limit; no result was accepted.") from cause
    except OSError as cause:
        raise OcrError("The isolated OCR runtime could not start. Check Bubblewrap and the OCR installation.") from cause
    if result.returncode:
        raise OcrError("The isolated OCR process failed or exceeded its resource limits.")


def _prepare_command(folder):
    """The sandboxed page-preparation step over `folder`, with this package mounted at /app/faculty_ocr."""
    from document_identity.process import sandbox_command
    command = sandbox_command(folder, ["/venv/bin/python", "-m", "faculty_ocr.prepare"])
    delimiter = command.index("--")
    command[delimiter:delimiter] = ["--ro-bind", str(Path(__file__).parent), "/app/faculty_ocr"]
    return command


def _reap(processes):
    """Kill and collect every started process still running (a failed batch must release them all)."""
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait()


def _result(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as cause:
        raise OcrError("The isolated OCR process did not return a valid result.") from cause
    if not isinstance(data, dict):
        raise OcrError("The isolated OCR process did not return a valid result.")
    if data.get("error"):
        raise OcrError(data["error"] + (": " + data["error_type"] if data.get("error_type") else ""))
    return data


def available():
    """True when the pinned model is installed; checks sizes only and runs nothing."""
    try:
        _manifest(runtime_root())
    except OcrError:
        return False
    return True


def status(*, verify_model=False):
    root = runtime_root()
    record, model = _manifest(root, verify_model)
    with _exclusive(root), tempfile.TemporaryDirectory(prefix="faculty-ocr-status-") as temp:
        scratch = Path(temp)
        (scratch / "request.json").write_text(json.dumps({"operation": "status"}))
        _run(_gpu_command(root, scratch, record, model), 45)
        return {**_result(scratch / "result.json"), "checksums_verified": verify_model}


def recognize(content, *, task="text", page=1, max_new_tokens=2048, device="auto", timeout=300):
    return _recognize(content, task=task, page=page, max_new_tokens=max_new_tokens, device=device, timeout=timeout)


def recognize_aliases(content):
    """Read both compact-layout label regions at four rotations; never look up identities."""
    return _recognize(content, task="text", page=1, max_new_tokens=64, device="cuda", timeout=120, operation="aliases")


def recognize_bands(content, *, band="top"):
    """Read one page band (or the page) at two rotations; the caller matches printed labels."""
    if band not in BANDS:
        raise ValueError("Choose the top band, bottom band, or the full page.")
    return _recognize(content, task="text", page=1, max_new_tokens=128, device="cuda", timeout=180,
                      operation="bands", band=band)


MAX_FIELDS = 64


def recognize_fields(content, boxes):
    """Read caller-chosen boxes of one image in a single model load (handwriting boxes of aligned bubble sheets).

    `boxes` are pixel rectangles [left, top, right, bottom] inside the image. The
    reader gets pixels and the fixed text prompt only; what a read means, and
    whether it matches anyone, is the caller's business.
    """
    if (not isinstance(boxes, list) or not 1 <= len(boxes) <= MAX_FIELDS
            or any(not isinstance(box, list) or len(box) != 4 or any(type(value) is not int or value < 0 for value in box)
                   or box[2] <= box[0] or box[3] <= box[1] for box in boxes)):
        raise ValueError(f"Provide 1 to {MAX_FIELDS} pixel boxes as [left, top, right, bottom].")
    return _recognize(content, task="text", page=1, max_new_tokens=48, device="cuda", timeout=420, operation="fields", boxes=boxes)


MAX_COVERS = 64
PREPARERS = 4       # sandboxes rendering first pages at once for one batch


def recognize_covers(pages, title_box, cover_words, title_unless=None, blank_corners=None):
    """Read the headers of several first pages in one model load (papers printed without labels).

    `pages` are single-page PDFs or images; `title_box` is a normalized [left, top, right, bottom] box. Each page's top band and
    title box are read upright, and again turned 180 degrees only where the upright band lacks one of `cover_words` (whole
    words, any case). The title box is read only for a page whose band has every cover word and does not match the regular
    expression `title_unless`. Crops of one kind are read as a batch. The reader gets pixels and the fixed text prompt only;
    what a read means is the caller's business. `blank_corners` (one per page) marks copies stamped with the two corner codes,
    whose stamp boxes are whited out before reading (`prepare.covers`): true for the 15 mm boxes of the 10 mm codes, "small" for
    the 8 mm boxes of the 7 mm codes (2026-10-18), false for none.
    """
    if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_COVERS
            or any(not isinstance(page, bytes) or not 0 < len(page) <= MAX_INPUT_BYTES for page in pages)):
        raise ValueError(f"Provide 1 to {MAX_COVERS} nonempty pages of at most 25 MiB each.")
    if (not isinstance(title_box, (list, tuple)) or len(title_box) != 4 or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in title_box)
            or title_box[2] <= title_box[0] or title_box[3] <= title_box[1]):
        raise ValueError("The title box is a normalized [left, top, right, bottom] box.")
    if not isinstance(cover_words, (list, tuple)) or not 1 <= len(cover_words) <= 8 or any(not isinstance(w, str) or not 0 < len(w) <= 40 for w in cover_words):
        raise ValueError("Provide 1 to 8 cover words.")
    if title_unless is not None and (not isinstance(title_unless, str) or not 0 < len(title_unless) <= 200):
        raise ValueError("title_unless is a regular expression of at most 200 characters.")
    blank_corners = [False] * len(pages) if blank_corners is None else blank_corners
    if not isinstance(blank_corners, list) or len(blank_corners) != len(pages) or any(type(value) is not bool and value != "small" for value in blank_corners):
        raise ValueError("blank_corners names every page true, \"small\" or false.")
    root = runtime_root()
    record, model = _manifest(root)
    with (_exclusive(root), tempfile.TemporaryDirectory(prefix="faculty-ocr-") as temp,
          tempfile.TemporaryDirectory(prefix="faculty-ocr-gpu-") as gpu_temp):
        scratch, work = Path(temp), Path(gpu_temp)
        request = {"task": "text", "page": 1, "max_new_tokens": 128, "device": "cuda", "operation": "covers", "inputs": len(pages),
                   "title_box": [float(v) for v in title_box], "title_tokens": 48, "cover_words": list(cover_words),
                   "is_pdf": pages[0].startswith(b"%PDF-"), "title_unless": title_unless, "batch_size": 8}
        (work / "request.json").write_text(json.dumps(request))
        # The GPU process imports its libraries and loads the model while the pages are prepared in a separate folder; it
        # starts reading when pages.json appears. Only the normalized crops ever reach its folder, never the raw pages.
        gpu, groups = _start(_gpu_command(root, work, record, model)), []
        try:
            # Rendering is the slow part of preparing: up to PREPARERS bounded sandboxes render their share of the pages at once,
            # so the crops are usually ready before the model is.
            size = -(-len(pages) // min(PREPARERS, len(pages)))
            for first in range(0, len(pages), size):
                folder = scratch / f"group-{first}"
                folder.mkdir()
                for index, page in enumerate(pages[first:first + size]):
                    (folder / f"input-{index}").write_bytes(page)
                (folder / "request.json").write_text(json.dumps({**request, "inputs": len(pages[first:first + size]), "first": first,
                                                                 "blank_corners": blank_corners[first:first + size]}))
                groups.append((folder, _start(_prepare_command(folder))))
            prepared_pages = []
            for folder, process in groups:
                _wait(process, 120)
                if (folder / "error.json").exists():
                    _result(folder / "error.json")
                prepared = _result(folder / "prepared.json")
                for page in prepared["pages"]:
                    for region in page["regions"]:
                        os.replace(folder / region["name"], work / region["name"])
                prepared_pages += prepared["pages"]
            prepared["pages"] = prepared_pages
            (work / "pages.tmp").write_text(json.dumps(prepared["pages"]))
            os.replace(work / "pages.tmp", work / "pages.json")
            _wait(gpu, 600)
        except BaseException:
            _reap([gpu, *(process for _, process in groups)])
            raise
        result = _result(work / "result.json")
        if len(result.get("pages", [])) != len(pages):
            raise OcrError("The isolated OCR process did not read every page.")
        for read, page in zip(result["pages"], pages):
            read["source_sha256"] = hashlib.sha256(page).hexdigest()
        return {**result, "crop_profile": prepared["crop_profile"], "pdf_render_dpi": prepared["pdf_render_dpi"]}


MAX_PAGES = 8  # whole pages in one model load; eight pages of the longest output stay inside the child's CPU limit


def recognize_pages(pages, *, max_new_tokens=2048):
    """Read several whole pages in one model load (requirement, 2026-09-25: a blank exam's pages for its answer-area check, where
    starting the model for every page took about as long as reading it).

    Each page is prepared by the same sandbox step as `recognize` and read the same way, one after another with the same prompt
    and greedy decoding, so each result is what `recognize` returns for that page; only the model's start-up is shared. The GPU
    process loads the model while the pages are prepared. Results are transient, like every other call here.
    """
    if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES
            or any(not isinstance(page, bytes) or not 0 < len(page) <= MAX_INPUT_BYTES for page in pages)):
        raise ValueError(f"Provide 1 to {MAX_PAGES} nonempty pages of at most 25 MiB each.")
    if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= MAX_TOKENS:
        raise ValueError("Output length must be between 1 and 4096 tokens.")
    root = runtime_root()
    record, model = _manifest(root)
    single = {"task": "text", "page": 1, "max_new_tokens": max_new_tokens, "device": "auto", "operation": "read"}
    with (_exclusive(root), tempfile.TemporaryDirectory(prefix="faculty-ocr-") as temp,
          tempfile.TemporaryDirectory(prefix="faculty-ocr-gpu-") as gpu_temp):
        scratch, work = Path(temp), Path(gpu_temp)
        (work / "request.json").write_text(json.dumps({**single, "operation": "pages", "inputs": len(pages)}))
        gpu, running, prepared = _start(_gpu_command(root, work, record, model)), [], []
        try:
            commands = []
            for index, page in enumerate(pages):
                folder = scratch / f"page-{index}"
                folder.mkdir()
                (folder / "input").write_bytes(page)
                (folder / "request.json").write_text(json.dumps({**single, "is_pdf": page.startswith(b"%PDF-")}))
                commands.append((folder, _prepare_command(folder)))
            for first in range(0, len(commands), PREPARERS):
                running = []
                for folder, command in commands[first:first + PREPARERS]:
                    running.append((folder, _start(command)))
                for folder, process in running:
                    _wait(process, 70)
                    if (folder / "error.json").exists():
                        _result(folder / "error.json")
                    prepared.append(_result(folder / "prepared.json"))
                    # The GPU process receives only the normalized image, never the raw page.
                    os.replace(folder / "page.png", work / f"page-{len(prepared) - 1}.png")
            (work / "pages.tmp").write_text(json.dumps(len(prepared)))
            os.replace(work / "pages.tmp", work / "pages.json")
            _wait(gpu, 600)
        except BaseException:
            _reap([gpu, *(process for _, process in running)])
            raise
        result = _result(work / "result.json")
        reads = result.pop("pages", None)
        if not isinstance(reads, list) or len(reads) != len(pages):
            raise OcrError("The isolated OCR process did not read every page.")
        return [{**result, **read, **info, "page": 1, "source_sha256": hashlib.sha256(page).hexdigest(),
                 "batch": {"pages": len(pages), "index": index}}
                for index, (read, info, page) in enumerate(zip(reads, prepared, pages))]


def _recognize(content, *, task, page, max_new_tokens, device, timeout, operation="read", **extra):
    """Read one image/PDF page. Caller owns any later persistence and instructor review."""
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_INPUT_BYTES:
        raise ValueError("Provide nonempty image/PDF bytes, at most 25 MiB.")
    if not isinstance(task, str) or task not in TASKS or device not in ("auto", "cpu", "cuda"):
        raise ValueError("Unsupported OCR task or device.")
    if type(page) is not int or not 1 <= page <= 100:
        raise ValueError("Page must be an integer between 1 and 100.")
    if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= MAX_TOKENS:
        raise ValueError("Output length must be between 1 and 4096 tokens.")
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("Timeout must be between 1 and 600 seconds.")
    root = runtime_root()
    record, model = _manifest(root)
    with _exclusive(root), tempfile.TemporaryDirectory(prefix="faculty-ocr-") as temp:
        scratch = Path(temp)
        (scratch / "input").write_bytes(content)
        request = {"task": task, "page": page, "max_new_tokens": max_new_tokens, "device": device, "operation": operation,
                   "is_pdf": content.startswith(b"%PDF-"), **extra}
        (scratch / "request.json").write_text(json.dumps(request))
        _run(_prepare_command(scratch), 70)
        if (scratch / "error.json").exists():
            _result(scratch / "error.json")
        prepared = _result(scratch / "prepared.json")
        if "regions" in prepared:
            request["regions"] = prepared["regions"]
            (scratch / "request.json").write_text(json.dumps(request))
        # The GPU process receives only the normalized image, never the raw PDF.
        (scratch / "input").unlink()
        _run(_gpu_command(root, scratch, record, model), timeout)
        return {**_result(scratch / "result.json"), **prepared, "page": page,
                "source_sha256": hashlib.sha256(content).hexdigest()}


def recognize_artifact(inbox, artifact_id, **options):
    """Read a retained artifact through Inbox's locks/checksums; never write its owner."""
    filename, content = inbox.read_artifact(artifact_id)
    return {**recognize(content, **options), "source_artifact_id": artifact_id, "source_filename": filename}
