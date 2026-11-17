"""Which exam version is this paper? A whole-paper comparison with each version's blank exam.

The seating chart is the source of a paper's exam version; this comparison only confirms
it. One page is not enough, because some pages are identical in every version, so every
reviewed page is compared with the same page of each blank exam and the paper as a whole
decides. Handwriting is extra ink on every candidate alike. Nothing here identifies a
student, selects a rubric line or switches a version: a disagreement is a review reason.
"""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile

from document_identity.process import sandbox_command

# v2 (2026-09-23): a page turned by at least `deskew.MINIMUM` degrees is also compared straightened, on the part the scanner
# captured, and scores the better of the two; every other page scores exactly as under v1. A paper an earlier matcher
# DECIDED keeps that decision (`exam_versions.paper_version`): v1 matched no page tilted more than about 2 degrees clearly,
# so straightening changes nothing it decided. Undecided papers are compared again.
# v3 (2026-09-25): each page is also compared turned half a turn. Which way up a page is gets decided ONCE, against the blank
# it resembles most (`deskew.turned_wins`), and every version is then scored that way up; every page not turned over scores
# exactly as under v2. Earlier matchers matched no upside-down page, so what they decided stands.
# v4 (2026-09-25): each blank exam is also tried at other sizes (`deskew.search`), for paper printed smaller or larger than
# its blank; a page that aligns best at its blank's own size scores exactly as under v3.
MATCHER = "whole-paper-v4"
EARLIER = ("whole-paper-v3", "whole-paper-v2", "whole-paper-v1")
# Measured on the instructor's Test 1 scans on 2026-09-19 (325 papers, versions A and B, 12 pages each) against
# the scanned 200 dpi blanks; GRADING.md records the distributions. A paper's own version scored a mean page
# quality of 0.667 to 0.910 (median 0.839) and the other version 0.458 to 0.604, because shared headers and the
# identical cover page match both. The lead of the right version was 0.209 to 0.31, and the comparison agreed
# with the seating chart on all 325. PASS is the aligner's own pass mark. MARGIN is the lead the best version
# needs over the runner-up (about half the smallest lead observed). ALONE is the mark for an exam with one blank
# so far, where no lead exists: it sits between the two distributions, so another version's paper cannot pass.
PASS, MARGIN, ALONE = .55, .12, .635
LIMIT = 30 * 1024**2


def compare(pages, templates):
    """pages: {page_number: png}; templates: {letter: {page_number: png}}. Returns per-version page qualities."""
    if not pages or not templates or len(pages) > 60 or len(templates) > 26:
        raise ValueError("Exam version comparison needs a reviewed paper and at least one blank exam.")
    if any(not 0 < len(v) <= LIMIT for v in pages.values()) or any(not 0 < len(v) <= LIMIT for t in templates.values() for v in t.values()):
        raise ValueError("A page image exceeds the comparison limit.")
    with tempfile.TemporaryDirectory(prefix="faculty-frq-version-") as temporary:
        root = Path(temporary)
        request = {"pages": [], "templates": {}}
        for number, content in pages.items():
            (root / f"scan-{number}").write_bytes(content)
            request["pages"].append(number)
        for letter, sheets in templates.items():
            request["templates"][letter] = sorted(sheets)
            for number, content in sheets.items():
                (root / f"blank-{letter}-{number}").write_bytes(content)
        (root / "request.json").write_text(json.dumps(request))
        command = sandbox_command(root, ["/venv/bin/python", "-m", "grading.version_match", "/work"])
        index = command.index("--")
        command[index:index] = ["--ro-bind", str(Path(__file__).parent.resolve()), "/app/grading"]
        try:
            result = subprocess.run(command, cwd=Path(__file__).parent.parent,
                env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
        except subprocess.TimeoutExpired:
            raise ValueError("Exam version comparison exceeded its time limit.") from None
        if result.returncode or not (root / "result.json").is_file():
            raise ValueError("Exam version comparison failed in the document sandbox.")
        data = json.loads((root / "result.json").read_text())
        if "error" in data:
            raise ValueError(data["error"])
        return data


def decide(result):
    """best version, its lead, and whether that is clear. One blank exam alone can only pass or fail its own mark."""
    scores = {letter: value["mean"] for letter, value in result["versions"].items() if value["mean"] is not None}
    if not scores:
        return {"matched": None, "clear": False, "scores": {}, "margin": None}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else None
    clear = ranked[0][1] >= (ALONE if margin is None else PASS) and (margin is None or margin >= MARGIN)
    return {"matched": ranked[0][0] if clear else None, "best": ranked[0][0], "clear": clear,
            "scores": {k: round(v, 4) for k, v in scores.items()}, "margin": round(margin, 4) if margin is not None else None}


def _candidates(observed):
    """The page as scanned and, when it is clearly turned, the same page straightened with the area the scanner captured."""
    from grading import deskew
    tilt = deskew.plan(observed)
    return [(observed, None)] + ([deskew.straighten(observed, tilt["angle"], 255)] if tilt["applied"] else [])


def _quality(observed, blank):
    """The aligner's own similarity (extraction._prepare): peak normalized correlation within ±5% shift, ±2°, and the
    blank's size (`deskew.search`). `observed` is one page or its `_candidates`; the best of them counts."""
    import numpy as np
    from PIL import Image
    from grading import deskew
    candidates = observed if isinstance(observed, list) else [(observed, None)]
    best = 0.0
    for page, captured in candidates:
        size = (256, max(64, round(page.height * 256 / page.width)))
        mask = None if captured is None else np.asarray(captured.resize(size, Image.NEAREST)) > 0
        ink = deskew.masked((255 - np.asarray(page.resize(size), dtype=float)) / 255, mask)
        fft, norm = np.fft.fft2(ink), np.linalg.norm(ink)
        limit = max(1, round(size[0] * .05))
        sheet, sizes = blank.resize(size), {}

        def score(scale, angle):
            if scale not in sizes:
                sizes[scale] = deskew.resized(sheet, scale, 255)
            moving = deskew.masked((255 - np.asarray(sizes[scale].rotate(angle, fillcolor=255), dtype=float)) / 255, mask)
            corr = np.fft.ifft2(fft * np.conj(np.fft.fft2(moving))).real
            window = np.concatenate((corr[:limit+1], corr[-limit:]), axis=0)
            window = np.concatenate((window[:, :limit+1], window[:, -limit:]), axis=1)
            return (float(window.max()) / max(1e-9, norm * np.linalg.norm(moving)),)
        best = max(best, deskew.search(score, (-2, -1, 0, 1, 2) if captured is None else deskew.FINE)[0][0])
    return best


def _compare(root):
    """Runs only in the sandbox."""
    import io
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 30_000_000

    def load(path):
        image = Image.open(io.BytesIO(path.read_bytes()))
        if image.width * image.height > 30_000_000:
            raise ValueError("Image is too large.")
        image.load()
        return image.convert("L")

    from grading import deskew
    request = json.loads((root / "request.json").read_text())
    scans = {number: _candidates(load(root / f"scan-{number}")) for number in request["pages"]}
    over = {number: deskew.upside_down(page) for number, page in scans.items()}
    # Every blank is loaded once, one at a time; each page's quality either way up is kept.
    scores = {}
    for letter, numbers in request["templates"].items():
        for number in numbers:
            if number in scans:
                blank = load(root / f"blank-{letter}-{number}")
                scores[letter, number] = (_quality(scans[number], blank), _quality(over[number], blank))
    turned = sorted(number for number in scans if deskew.turned_wins(
        max((up for (_, n), (up, _) in scores.items() if n == number), default=None),
        max((down for (_, n), (_, down) in scores.items() if n == number), default=None)))
    versions = {}
    for letter, numbers in request["templates"].items():
        pages = {str(number): round(scores[letter, number][number in turned], 4) for number in numbers if number in scans}
        versions[letter] = {"pages": pages, "mean": sum(pages.values()) / len(pages) if pages else None}
    return {"matcher": MATCHER, "versions": versions, "turned": turned, "orientation_rule": deskew.TURN_RULE}


def main(root):
    resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (200, 200))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16*1024**2, 16*1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        result = _compare(root)
    except Exception:
        result = {"error": "The paper or a blank exam could not be safely compared."}
    (root / "result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
