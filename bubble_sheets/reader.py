"""Read one scanned page as a Gradescope bubble sheet: recognise it, align it, measure every bubble.

Pure functions over a 200-DPI greyscale raster (the raster Faculty's scan
inspection already retains). Nothing here knows about students, the catalog or
answer keys, and nothing is graded. Every decision has an explicit unsure state
so that anything short of clear goes to the instructor:

- a page is a sheet, not a sheet, or `unsure` from how well it matches a 20-DPI
  picture of the template (real sheets scored 0.52 to 0.65, exam pages at most
  0.13);
- alignment uses the corner markers that match well (a stapled corner is common)
  and is checked against the printed bubble rings themselves;
- a bubble is `filled`, `empty` or `unsure` from how much darker it is than the
  same letter's empty bubbles on that sheet (on 82 real sheets, 9 of 41,000
  bubbles fell between the two groups).
"""
import base64
import io
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

PROFILE = "gradescope-2020-05-01"
SHEET, NOT_SHEET = .35, .22          # template match at or above / below; between is unsure
MARKER = .6                          # a corner marker this well matched is trusted
RING = .04                           # printed rings this much darker than the paper beside them confirm the alignment
RING_SURE = .08                      # rings this clear settle what the page is, whatever the whole-page resemblance said
# Mark strength above the sheet's empty baseline. Measured on 244 real sheets: a bubble left empty never exceeded 0.081 (99.9%
# of them under 0.045, dust and smudges included), while deliberate but light marks sat at 0.10 to 0.18 and ordinary ones at
# 0.19 and up. At 0.14 a lightly filled bubble is an answer; between 0.06 and 0.14 it is `unsure` and the instructor looks,
# so a speck never becomes an answer and a blank never becomes one either.
FILLED, EMPTY = .14, .06
# A mark alone in its row, every other bubble at the empty level, needs no comparison and counts from here. Measured on the 101,260
# bubbles of rows past the last question, empty on all 244 real sheets: mean 0.000, standard deviation 0.008, 99.99% under 0.044, and
# the darkest 0.062 (two deliberate marks aside); nothing at all between 0.062 and 0.10, where the lone marks students really made
# begin (0.105). Darker readings of "empty" bubbles (up to 0.085) only occur beside a filled answer, as erasure ghosts, which this
# rule never sees. Those scans had no dust on the glass, so how dark a speck reads is not known: between 0.06 and 0.10 a lone mark
# stays in doubt.
LIGHT = .10
SPILL = .10                          # a heavy mark bleeds this much, at most, into the bubble right beside it
DOMINANT = .4                        # a mark under this share of the row's darkest is not its equal: an erased answer or its ghost
SEARCH = 90                          # pixels a marker may sit from its template position (11 mm at 200 DPI)
TRIMMED = .03                        # a scan cropped to the paper's own edge may differ from Letter by this much each way (`letter`)


def _image(encoded):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(encoded))).convert("L"), dtype=np.float32)


@lru_cache(maxsize=4)
def template(profile=PROFILE):
    data = json.loads((Path(__file__).parent / "templates" / f"{profile}.json").read_text())
    for page in data["pages"]:
        page["thumbnail"] = _unit(_image(page["thumbnail"]))
        for marker in page["markers"].values():
            marker["patch"] = _image(marker["patch"])
    return data


def decode(content):
    """A retained scan raster (image bytes) as the 8-bit greyscale array the functions here read."""
    return np.asarray(Image.open(io.BytesIO(content)).convert("L"))


def letter(raster, profile=PROFILE):
    """The page at the template's 200-DPI Letter size. Home scanners crop every page to the paper's own edge (2026-10-24: the
    Epson's pages came out 1675 to 1695 by 2195 to 2220 pixels, and no Brother test page was exact Letter), so a page within 3%
    of Letter each way is the whole sheet and is scaled to it; anything further off is returned as it is, and is no sheet."""
    width, height = template(profile)["pixels"]
    if raster.shape == (height, width) or not (abs(raster.shape[0] / height - 1) <= TRIMMED and abs(raster.shape[1] / width - 1) <= TRIMMED):
        return raster
    return np.asarray(Image.fromarray(raster).resize((width, height), Image.BICUBIC))


def _unit(picture):
    ink = 255.0 - picture
    ink = ink - ink.mean()
    return ink / (np.linalg.norm(ink) + 1e-6)


def _peak(window, patch):
    """Best normalised cross-correlation of `patch` inside `window`: score and the patch centre."""
    patch = patch - patch.mean()
    rows, columns = patch.shape
    window = window - window.mean()
    correlation = np.fft.irfft2(np.fft.rfft2(window) * np.conj(np.fft.rfft2(patch, window.shape)), window.shape)
    correlation = correlation[:window.shape[0] - rows + 1, :window.shape[1] - columns + 1]
    squares, sums = (np.pad(values.cumsum(0).cumsum(1), ((1, 0), (1, 0))) for values in (window ** 2, window))
    area = lambda table: table[rows:, columns:] - table[:-rows, columns:] - table[rows:, :-columns] + table[:-rows, :-columns]
    variance = np.maximum(area(squares) - area(sums) ** 2 / patch.size, 0) / patch.size
    # A marker is black on white. Flat paper cannot be one, and dividing by its tiny variance explodes.
    score = np.where(variance > 40.0 ** 2, correlation / (np.sqrt(variance * patch.size) * np.linalg.norm(patch) + 1e-6), 0.0)
    y, x = np.unravel_index(np.argmax(score), score.shape)
    return float(score[y, x]), x + columns / 2, y + rows / 2


def classify(raster, profile=PROFILE):
    """Which template page this is, which way up, and how sure: {"kind": sheet|unsure|other, "page", "rotation", "score"}."""
    data = template(profile)
    spectrum = np.fft.rfft2(_unit(np.asarray(Image.fromarray(raster).resize((170, 220), Image.BOX), dtype=np.float32)))
    best = {"kind": "other", "page": None, "rotation": 0, "score": 0.0}
    for page in data["pages"]:
        for rotation, reference in ((0, page["thumbnail"]), (180, page["thumbnail"][::-1, ::-1])):
            correlation = np.fft.irfft2(spectrum * np.conj(np.fft.rfft2(reference)), reference.shape)
            score = float(max(correlation[:7, :7].max(), correlation[-6:, -6:].max(), correlation[:7, -6:].max(), correlation[-6:, :7].max()))
            if score > best["score"]:
                best = {"kind": "other", "page": page["page"], "rotation": rotation, "score": round(score, 3)}
    best["kind"] = "sheet" if best["score"] >= SHEET else "unsure" if best["score"] >= NOT_SHEET else "other"
    return best


def _placed(aligned):
    """The fitted geometry stays within 3% of true size: the markers sit where an undistorted Letter sheet puts them."""
    stretch = np.linalg.svd(aligned["affine"][:2], compute_uv=False)
    return bool(stretch.max() <= 1.03 and stretch.min() >= .97)


def _trusted(aligned):
    return sum(score >= MARKER for score in aligned["markers"].values())


def settle(raster, proposal, profile=PROFILE):
    """When the resemblance's proposal does not hold, the positioning marks and the rings are asked on their own (instructor,
    2026-09-25: two plain sheets of one scan were missed). Every template page, either way up, other than the one proposed is
    aligned; the page is a sheet only where at least three trusted markers sit within 3% of true size AND the printed rings are
    clearly where they say (`RING_SURE`, the level that already settles what a page is). No other kind of page has five hundred
    rings in those places. Returns {"page", "rotation", "aligned"} or None.

    Measured on a scan whose sheets were printed a little small (97.5%): every sheet resembled the template at only 0.22 to 0.25,
    against 0.52 to 0.65 on the sheets the thresholds came from, so one fell just below 0.22 and another resembled the template's
    second page by a hair. Both had four trusted markers and rings at 0.10 and 0.18."""
    tried = (proposal["page"], proposal["rotation"]) if proposal["kind"] != "other" else None
    for page in template(profile)["pages"]:
        for rotation in (0, 180):
            if (page["page"], rotation) == tried:
                continue
            aligned = align(raster[::-1, ::-1] if rotation else raster, page)
            if aligned is not None and _placed(aligned) and _trusted(aligned) >= 3 and aligned["ring"] >= RING_SURE:
                return {"page": page["page"], "rotation": rotation, "aligned": aligned}
    return None


def detect(raster, profile=PROFILE):
    """Is this page a bubble sheet? Its resemblance to the template proposes; the corner positioning marks decide.

    `sheet` needs at least three trusted corner markers, either way up, that sit
    where an undistorted Letter sheet puts them (the fitted geometry stays within
    3% of true size). What a sheet is does not need the rings to line up: a torn,
    stapled corner leaves real sheets slightly warped, so `rings` only says
    whether a later read will align or wait for the instructor. A page that only
    resembles the template is `unsure`; anything else is `other`.
    """
    result = {"kind": "other", "page": None, "rotation": 0, "score": 0.0, "markers": 0, "ring": None}
    raster = letter(raster, profile)
    if raster.shape != tuple(reversed(template(profile)["pixels"])):
        return result
    result.update(classify(raster, profile))
    proposed = False
    if result["kind"] != "other":
        page = next(item for item in template(profile)["pages"] if item["page"] == result["page"])
        aligned = align(raster[::-1, ::-1] if result["rotation"] else raster, page)
        if aligned is not None:
            result.update(markers=_trusted(aligned), ring=aligned["ring"], rings=bool(aligned["ring"] >= RING))
            proposed = _placed(aligned) and result["markers"] >= 3
    # The marks say it is a sheet; the rings say which page. Where the proposal's rings clearly confirm it, it stands exactly as
    # before. Otherwise a page whose rings do is preferred (the two template pages share marker positions, not their grid), and a
    # proposal the marks alone support is still a sheet: a torn corner leaves real sheets warped.
    if proposed and result["ring"] >= RING_SURE:
        return {**result, "kind": "sheet"}
    settled = settle(raster, result, profile)
    if settled:
        aligned = settled["aligned"]
        return {**result, "kind": "sheet", "page": settled["page"], "rotation": settled["rotation"], "markers": _trusted(aligned),
                "ring": aligned["ring"], "rings": True, "settled_by": "marks and rings"}
    result["kind"] = "sheet" if proposed else "unsure" if result["kind"] != "other" else "other"
    return result


def field_box(upright, affine, box):
    """Where a handwriting field is on the scan: its printed place, moved up or down to the band of ink actually written there.

    Students rarely write in the middle of the row. Many write on the rule or
    below it, and a crop of the printed box alone slices such writing in half
    (several real sheets stayed unidentified for exactly that). The rows half a
    field above and below are searched for the run of inked rows that overlaps
    the printed box most; the crop follows it, never shorter than the printed box
    nor taller than 1.7 of it, so the neighbouring row's writing stays out.
    Printed rules (rows inked nearly edge to edge) are not writing.
    """
    left, top, right, bottom = box
    (x0, y0), (x1, y1) = np.array([left, top, 1.0]) @ affine, np.array([right, bottom, 1.0]) @ affine
    x0, x1, y0, y1 = int(max(0, min(x0, x1))), int(min(upright.shape[1], max(x0, x1))), int(max(0, min(y0, y1))), int(min(upright.shape[0], max(y0, y1)))
    height = y1 - y0
    if height < 20 or x1 - x0 < 40:
        return [x0, y0, x1, y1]
    first, last = max(0, y0 - height // 2), min(upright.shape[0], y1 + height // 2)
    strip = upright[first:last, x0:x1].astype(np.float32)
    dark = strip < np.percentile(strip, 90) - 60
    counts = dark.sum(1)
    written = (counts >= max(3, .006 * strip.shape[1])) & (counts < .6 * strip.shape[1])
    rows, runs, start = np.flatnonzero(written), [], None
    for row in rows:  # rows of ink no more than 6 px apart are one band of writing
        if start is None:
            start = previous = row
        elif row - previous > 6:
            runs.append((start, previous))
            start = row
        previous = row
    if start is not None:
        runs.append((start, previous))
    inside = lambda run: min(run[1] + first, y1) - max(run[0] + first, y0)
    runs = [run for run in runs if inside(run) > 0 and run[1] - run[0] >= 8]
    if not runs:
        return [x0, y0, x1, y1]
    band = max(runs, key=lambda run: (inside(run), counts[run[0]:run[1] + 1].sum()))
    new_top, new_bottom = band[0] + first - 8, band[1] + first + 8
    if new_bottom - new_top < height:  # never tighter than the printed box: grow around the writing
        grow = height - (new_bottom - new_top)
        new_top, new_bottom = new_top - grow // 2, new_bottom + grow - grow // 2
    if new_bottom - new_top > 1.7 * height:  # two rows' writing touching: keep to the part around the printed box
        centre = (max(new_top, y0) + min(new_bottom, y1)) // 2
        new_top, new_bottom = int(centre - .85 * height), int(centre + .85 * height)
    return [x0, int(max(0, new_top)), x1, int(min(upright.shape[0], new_bottom))]


def decide(values):
    """One row of five mark strengths: (`single`|`multiple`|`blank`|`unsure`, the filled letters, the letters in doubt).

    The one rule for a read and for every later look at a retained reading, which
    keeps its strengths. Ink from a heavy mark spilling into its neighbour is not
    a second mark, and neither is the ghost of an erased answer beside a bubble
    filled more than twice as dark. A faint mark with nothing filled beside it
    stays in doubt: it may be dust, or a student's light answer.

    Several bubbles filled about equally dark are what the student handed in: a
    matter for grading, recorded as `multiple` and not sent to review (the
    instructor's rule; on 244 real sheets the second mark of a double was 51% to
    97% as dark as the first). A bubble dark enough to count alone yet under 40% of
    the row's darkest is an answer erased (`erased-under-dominant`, instructor,
    2026-10-25: "All of the MC marks that I've had to review for exam 2 seemed pretty
    obvious that the student erased and chose a different answer"): on Exam 2 such
    marks were 22% to 39% as dark, and all 9 the instructor reviewed kept the dark
    answer alone, while one at 41% was kept as a double.
    """
    above = [choice for choice in range(5) if values[choice] > EMPTY]
    if len(above) == 1 and values[above[0]] >= LIGHT:
        return "single", "ABCDE"[above[0]], ""  # One light but deliberate mark and nothing else in the row: the answer.
    filled = [choice for choice in range(5) if values[choice] >= FILLED]
    darkest = max((values[choice] for choice in filled), default=0)
    filled = [choice for choice in filled if values[choice] >= DOMINANT * darkest]  # under 40% of the darkest: erased
    unsure = sorted(choice for choice in range(5) if EMPTY < values[choice] < FILLED and values[choice] >= DOMINANT * darkest
                    and not (values[choice] < SPILL and any(abs(choice - other) == 1 for other in filled)))
    state = "unsure" if unsure else "blank" if not filled else "single" if len(filled) == 1 else "multiple"
    return state, "".join("ABCDE"[choice] for choice in filled), "".join("ABCDE"[choice] for choice in unsure)


QUESTION_AGREEMENT = .6  # the question count is inferred only when this share of the sheets have their last mark on the same question


class Undecided(ValueError):
    """The sheets themselves do not say how many questions there are; the instructor has to."""


def infer_question_count(last_marked):
    """How many questions a stack of sheets has: the question most of them have their last mark on, when enough of them agree.

    Students answer to the end far more often than not, a few stop early, and a
    few leave a stray mark further down (on 244 real sheets: 239 ended on the
    last question, 2 one short, 3 well past it). The mode is therefore the
    count, provided at least 60% of the marked sheets share it. Below that the
    sheets are not saying anything clear, and nothing is guessed. Returns the
    count and the share of sheets that agree.
    """
    marks = [int(mark) for mark in last_marked if mark]
    if not marks:
        raise Undecided("No sheet in this scan has a mark, so the number of questions cannot be inferred. Enter it and read again.")
    tally = {}
    for mark in marks:
        tally[mark] = tally.get(mark, 0) + 1
    count, sheets = max(tally.items(), key=lambda item: (item[1], item[0]))
    share = sheets / len(marks)
    if share < QUESTION_AGREEMENT:
        raise Undecided(f"The sheets do not agree on a last question: the most common, question {count}, is the last mark on only {round(share * 100)}% of them, "
                        f"and {round(QUESTION_AGREEMENT * 100)}% is needed to infer the count. Enter the number of questions and read again.")
    return count, round(share, 3)


def trim(result, question_count):
    """A reading of all hundred rows, kept to the sheet's real questions: what lies past them is nobody's answer."""
    if not result.get("aligned"):
        return result
    answers = result["answers"][:question_count]
    questions = [reason for reason in map(question_reason, answers) if reason]
    return {**result, "answers": answers, "review": questions + [reason for reason in result["review"] if not reason.startswith("Question ")]}


def question_reason(item):
    """Why a question waits for the instructor, or None. A blank is an answer like any other, and so is a confident double mark."""
    if item["state"] == "unsure":
        return f"Question {item['question']}: a faint or erased mark at {', '.join(item['unsure'])}" + (f" beside {item['answer']}" if item["answer"] else "") + "."
    return None


def _refine(picture, centres, affine, ring, beside, paper):
    """A fit from three corner markers has no redundancy, and a few pixels' error puts every thin printed ring off its bubble.

    The rings themselves are five hundred registration marks: each block of the
    sheet finds the small shift that puts its rings back under the template, and
    one affine is fitted through all of them.
    """
    places = centres @ affine
    columns = np.digitize(centres[:, 0], np.quantile(centres[:, 0], [.25, .5, .75]))
    thirds = np.digitize(centres[:, 1], np.quantile(centres[:, 1], [1 / 3, 2 / 3]))
    ring_y, ring_x = (np.argwhere(ring) - 18).T
    beside_y, beside_x = (np.argwhere(beside) - 18).T
    height, width = picture.shape

    def contrast(points):
        points = np.rint(points).astype(int)
        if points[:, 0].min() < 30 or points[:, 1].min() < 30 or points[:, 0].max() > width - 30 or points[:, 1].max() > height - 30:
            return -1.0
        return float(np.mean(picture[points[:, 1, None] + beside_y, points[:, 0, None] + beside_x].mean(1)
                             - picture[points[:, 1, None] + ring_y, points[:, 0, None] + ring_x].mean(1)) / paper)

    target = places.copy()
    for block in np.unique(columns * 3 + thirds):
        members = np.flatnonzero(columns * 3 + thirds == block)
        best = max(((contrast(places[members] + [dx, dy]), dx, dy) for dy in range(-10, 11, 2) for dx in range(-10, 11, 2)), key=lambda item: item[0])
        best = max(((contrast(places[members] + [dx, dy]), dx, dy) for dy in range(best[2] - 1, best[2] + 2) for dx in range(best[1] - 1, best[1] + 2)), key=lambda item: item[0])
        target[members] += best[1:]
    refined, *_ = np.linalg.lstsq(centres, target, rcond=None)
    return refined


def align(raster, page):
    """Template-to-scan affine from the trusted corner markers, chosen and checked by the printed bubble rings."""
    picture = raster.astype(np.float32)
    names, found, scores = list(page["markers"]), [], []
    for name in names:
        (cx, cy), patch = page["markers"][name]["centre"], page["markers"][name]["patch"]
        left, top = int(max(0, cx - SEARCH - patch.shape[1] / 2)), int(max(0, cy - SEARCH - patch.shape[0] / 2))
        score, x, y = _peak(picture[top:int(cy + SEARCH + patch.shape[0] / 2), left:int(cx + SEARCH + patch.shape[1] / 2)], patch)
        found.append((left + x, top + y))
        scores.append(score)
    source = np.array([[*page["markers"][name]["centre"], 1.0] for name in names])
    target = np.array(found)
    trusted = [index for index, score in enumerate(scores) if score >= MARKER]
    candidates = ([trusted] if len(trusted) == 4 else []) + [list(triple) for triple in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)) if set(triple) <= set(trusted)]
    paper = float(np.percentile(picture[200:-200:8, 200:-200:8], 90))
    centres = np.array([[x, y, 1.0] for row in page["questions"].values() for x, y, _ in row] + [[x, y, 1.0] for x, y, _ in page["version"]])
    radius = float(np.mean([d for row in page["questions"].values() for _, _, d in row])) / 2
    yy, xx = np.mgrid[-18:19, -18:19]
    distance = np.sqrt(xx ** 2 + yy ** 2)
    ring, beside = np.abs(distance - radius) <= 1.5, (distance >= radius + 3) & (distance <= radius + 5.5)
    best = None
    def fitted(affine):
        places = np.rint(centres @ affine).astype(int)
        inside = (places[:, 0] > 20) & (places[:, 0] < picture.shape[1] - 20) & (places[:, 1] > 20) & (places[:, 1] < picture.shape[0] - 20)
        if not inside.all():
            return None, -1.0
        patches = picture[places[:, 1, None, None] + yy[None], places[:, 0, None, None] + xx[None]]
        return patches, float(np.mean(patches[:, beside].mean(1) - patches[:, ring].mean(1)) / paper)

    for subset in candidates:
        affine, *_ = np.linalg.lstsq(source[subset], target[subset], rcond=None)
        patches, contrast = fitted(affine)
        if patches is not None and (best is None or contrast > best["ring"]):
            best = {"affine": affine, "ring": round(contrast, 3), "patches": patches, "paper": paper, "radius": radius, "distance": distance,
                    "markers": {name: round(score, 2) for name, score in zip(names, scores)}, "used": [names[index] for index in subset],
                    "residual": round(float(np.sqrt(np.mean(np.sum((source[subset] @ affine - target[subset]) ** 2, axis=1)))), 2)}
    # Only a fit the rings do not clearly confirm is touched, so sheets that align as they always did read exactly as they always did.
    if best is not None and best["ring"] < RING_SURE:
        refined = _refine(picture, centres, best["affine"], ring, beside, paper)
        patches, contrast = fitted(refined)
        if patches is not None and contrast > best["ring"]:
            best.update(affine=refined, ring=round(contrast, 3), patches=patches, refined=True)
    return best


def _reading_alignment(raster, profile):
    """Use one orientation and alignment rule for readings and their review crops."""
    kind = classify(raster, profile)
    page = template(profile)["pages"][0]
    aligned = align(raster[::-1, ::-1] if kind["rotation"] else raster, page) if kind["kind"] != "other" and kind["page"] == 1 else None
    if aligned is None or aligned["ring"] < RING or not (_placed(aligned) and _trusted(aligned) >= 3):
        # The proposal did not hold: the marks and the rings on their own may still settle it (see `settle`), as detection does.
        settled = settle(raster, kind, profile)
        if settled:
            kind = {**kind, "kind": "sheet", "page": settled["page"], "rotation": settled["rotation"], "settled_by": "marks and rings"}
            aligned = settled["aligned"]
    return kind, aligned


def read_page(raster, question_count, profile=PROFILE):
    """Everything Faculty can say about one page, with reasons for anything the instructor should look at."""
    raster = letter(raster, profile)
    if raster.shape != tuple(reversed(template(profile)["pixels"])):
        raise ValueError("Bubble sheets are read from the 200-DPI Letter raster of a scan page.")
    kind, aligned = _reading_alignment(raster, profile)
    page = template(profile)["pages"][0]
    result = {"profile": profile, **kind, "review": []}
    if kind["kind"] == "other":
        return result
    if kind["page"] != 1:
        result["review"].append("This is the template's second page (questions 101 to 200), which is not read yet.")
        return result
    if kind["rotation"]:
        raster = raster[::-1, ::-1]
    if aligned is None or aligned["ring"] < RING:
        result["review"].append("The sheet could not be aligned: fewer than three corner markers were found, or the bubbles are not where the markers say.")
        result["aligned"] = False
        return result
    # The whole-page resemblance only proposes. Bold marks and a full name box lower it on the cleanest scans, so once the
    # positioning marks put the printed rings exactly where the scan shows them, a middling resemblance is no reason to look.
    if kind["kind"] == "unsure" and aligned["ring"] < RING_SURE:
        result["review"].append(f"This page only partly resembles the bubble sheet (match {kind['score']:.2f}), and its bubbles are only faintly where the markers say.")
    inner = aligned["distance"] <= aligned["radius"] * .62
    fills = np.clip((aligned["paper"] - aligned["patches"][:, inner]) / aligned["paper"], 0, 1).mean(1)
    grid = fills[:500].reshape(100, 5)
    # Each printed letter carries its own ink, and most bubbles on a sheet are empty: their median is that letter's empty level.
    baseline = np.median(grid, axis=0)
    strength = grid - baseline
    answers = []
    for index in range(100):
        state, filled, unsure = decide(strength[index])
        answers.append({"question": index + 1, "state": state, "answer": filled, "unsure": unsure,
                        "marks": {"ABCDE"[choice]: round(float(strength[index, choice]), 3) for choice in range(5)}})
    version_marks = fills[500:505] - float(np.median(baseline))
    version_state, version_filled, version_unsure = decide(version_marks)
    # A blank is an answer like any other, and marks past the last question are nobody's answer (the instructor's rules): neither
    # is sent to review. `last_marked` still tells the run when most sheets go further than the count that was entered.
    result["review"] += [reason for reason in map(question_reason, answers[:question_count]) if reason]
    if version_state != "single":
        result["review"].append({"blank": "No version bubble is filled.", "multiple": f"{len(version_filled)} version bubbles are filled.",
                                 "unsure": "The version bubble is faint or erased."}[version_state])
    boxes = {name: field_box(raster, aligned["affine"], box) for name, box in page["fields"].items()}
    result.update(aligned=True, alignment={**{key: aligned[key] for key in ("ring", "residual", "markers", "used")}, "refined": bool(aligned.get("refined"))},
                  answers=answers[:question_count], version={"state": version_state, "answer": version_filled, "unsure": version_unsure,
                                                              "marks": {"ABCDE"[choice]: round(float(version_marks[choice]), 3) for choice in range(5)}},
                  fields=boxes, last_marked=max((item["question"] for item in answers if item["state"] != "blank"), default=0))
    return result


def part_image(raster, part, profile=PROFILE, scale=2):
    """The piece of a sheet the instructor is asked about, upright and enlarged: `id`, `name`, `version` or `q<number>`.

    The alignment is recomputed from the retained raster (a twentieth of a
    second), so a crop shows exactly where the reading looked: the ID and name
    boxes with their printed labels, the version row, or one question's number
    and five bubbles with a sliver of the rows around it.
    """
    raster = letter(raster, profile)
    kind, aligned = _reading_alignment(raster, profile)
    if kind["kind"] == "other" or kind["page"] != 1:
        raise ValueError("This page was not read as the bubble sheet's first page.")
    upright = raster[::-1, ::-1] if kind["rotation"] else raster
    page = template(profile)["pages"][0]
    if aligned is None:
        raise ValueError("This sheet could not be aligned, so its parts cannot be located.")
    if part in page["fields"]:
        # The same band of writing the OCR was given, with the printed "Name" / "ID" label to its left for context.
        box = field_box(upright, aligned["affine"], page["fields"][part])
        label = np.array([page["fields"][part][0] - 70, page["fields"][part][1], 1.0]) @ aligned["affine"]
        return field_image(upright, 0, [int(max(0, min(box[0], label[0]))), box[1], box[2], box[3]], scale)
    else:
        row = page["version"] if part == "version" else page["questions"].get(part[1:]) if part[:1] == "q" else None
        if not row:
            raise ValueError("Choose the ID, the name, the version or a question of this sheet.")
        radius = row[0][2] / 2
        left, right = row[0][0] - (150 if part == "version" else 62), row[-1][0] + radius + 12
        top, bottom = row[0][1] - radius - 11, row[0][1] + radius + 11
    corners = np.array([[left, top, 1.0], [right, top, 1.0], [left, bottom, 1.0], [right, bottom, 1.0]]) @ aligned["affine"]
    box = [int(max(0, corners[:, 0].min())), int(max(0, corners[:, 1].min())),
           int(min(upright.shape[1], corners[:, 0].max())), int(min(upright.shape[0], corners[:, 1].max()))]
    return field_image(upright, 0, box, scale)


def choice_targets(profile=PROFILE):
    """Where each bubble sits inside the snapshots `part_image` makes, as percentages of the snapshot's width.

    For the click targets laid over a question's or the version row's snapshot:
    each letter's zone runs from halfway to its neighbour on either side. The
    snapshot is cut from template coordinates, so the zones are the same for
    every sheet; all hundred question rows share one layout to within 0.2%.
    """
    page = template(profile)["pages"][0]

    def zones(row, lead):
        radius = row[0][2] / 2
        left, right = row[0][0] - lead, row[-1][0] + radius + 12
        centres = [(x - left) / (right - left) * 100 for x, _, _ in row]
        half = (centres[1] - centres[0]) / 2
        return [{"letter": letter, "left": round(max(0.0, centre - half), 2), "width": round(min(100.0, centre + half) - max(0.0, centre - half), 2)}
                for letter, centre in zip("ABCDE", centres)]
    return {"question": zones(page["questions"]["1"], 62), "version": zones(page["version"], 150)}


def field_image(raster, rotation, box, scale=2):
    """The handwriting box of an aligned sheet, upright and enlarged for reading."""
    picture = Image.fromarray(raster[::-1, ::-1] if rotation else raster).crop(tuple(box))
    return picture.resize((max(1, picture.width * scale), max(1, picture.height * scale)), Image.LANCZOS)
