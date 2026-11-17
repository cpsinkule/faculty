"""How far a scanned page is turned, read from its own printed lines, and the page turned upright (requirement, 2026-09-23).

Some quizzes arrive tilted 10-15 degrees. The aligner's own search (`extraction`, `version_match`) covers a few degrees
only, so such a page matched no blank exam and its answer areas were cut crooked, with pieces of the neighbouring
questions. Printed lines are sharpest in the page's row profile when horizontal, so the tilt is measured from the page
alone, with no template; measured on 60 real exam sides, upright pages read -0.7 to +0.3 degrees and the same sides
tilted 12 degrees read 11.7 to 12.7.

Runs inside the document sandbox. The retained scan never changes: a straightened page is a derived image, and what was
done is recorded with every crop and comparison. A page is straightened only when the tilt is at least `MINIMUM`
degrees and the reading is `CONFIDENT`; anything else is left exactly as scanned. The reading only PROPOSES: long straight
strokes (a line drawn across a graph) can mislead it with confidence, so wherever a blank exam exists the straightened page
competes with the page as scanned and is used only when it aligns better (`extraction`, `version_match`).

Sheets fed UPSIDE DOWN (requirement, 2026-09-25) are the other thing the scanner does: a half turn is outside every search
above, so such a page matched no blank exam and its answer areas were cut from the wrong part of the sheet. Every page is
therefore also tried turned half a turn (`upside_down`), which is exact (no resampling: the scanner's own pixels), and the
blank exam alone decides (`turned_wins`); the tilt reading cannot tell a half turn, and without a blank exam nothing is
turned. Quarter turns do not happen with this scanner and are not tried.

Paper printed at ANOTHER SIZE than its blank exam (requirement, 2026-09-25: add a scale search): Quiz 2 was printed a few
percent smaller than the PDF its blank came from, and the aligners searched rotation and shift only, so no page of it
aligned and its answer areas drifted down the page. `search` also tries the blank exam at other sizes, about its centre;
a page aligns at its blank's own size exactly as before unless another size is clearly better (`SIZE_GAIN`).
"""
import numpy as np
from PIL import Image

RULE = "row-profile-v1"
LIMIT = 20.0  # degrees searched either way
# Smaller tilts are left alone: the aligners' own search covers them, and those pages keep the scanner's own pixels.
MINIMUM = 1.0
# How much sharper the best angle's row profile is than the median angle's. Measured: every real exam side, upright or
# tilted, scored 13 or more; a blank or noisy page scores 0 and random strokes about 2. Below this, nothing is turned.
CONFIDENT = 6.0
WIDTH = 800  # the measurement's own resolution; the page itself is turned at full size
# The aligners search what is left after straightening in tenths of a degree within one degree: the estimate is off by a
# few tenths, and on a real page 0.2 degrees left over cost the aligner a tenth of its score (0.585 exact, 0.516 at 0.2).
FINE = tuple(round(step / 10, 1) for step in range(-10, 11))
TURN_RULE = "upside-down-v1"
# The lead a page turned half a turn needs over the same page as scanned, both aligned with the same blank exam, before it is
# used. Measured on 2026-09-25 with the crop aligner on the trial's real scans. Test 1 (160 answer pages, scanned blanks):
# turned over, a page lost 0.376 to 0.880, and the same pages turned half a turn gained exactly that back. Quiz 2 (a digital
# blank, which a scan does not match well): the 21 first pages and their 21 backs the first-page reader had read upside down
# gained 0.148 to 0.286 and 0.125 to 0.223; 40 upright first pages and 30 upright backs lost 0.174 to 0.360 and 0.041 to
# 0.254. This is about half the smallest real lead. No pass mark is required as well: every Quiz 2 page, either way up,
# stayed below it, because the printed quiz is a few percent smaller than its PDF, which the aligner does not search.
TURN_MARGIN = .06
SIZE_RULE = "size-search-v1"
# Sizes of the blank exam tried, as a fraction of its own: printed "to fit", or scaled by a printer or scanner.
SIZES = tuple(round(.86 + .02 * step, 2) for step in range(13))  # 0.86 to 1.10
FINER = (-.015, -.01, -.005, .005, .01, .015)
# How much better another size must align than the blank's own before it is used. Measured on 2026-09-25 on the trial's real
# scans. Test 1 (blanks scanned on the same scanner): no other size aligned better at all, on 80 pages against their own
# version's blank and 80 against the other version's, in the crop aligner and the version matcher alike. Quiz 2 (digital
# blanks from the PDF, 102 first pages and backs either way up): every page aligned best at 0.95 to 0.96 of the blank's size
# and gained 0.25 to 0.49 with its own version's blank (0.19 or more with another's), from about 0.32 to about 0.69; 97 of
# the 102 then passed the aligner's mark, none before. A quarter of the smallest real gain, so smaller printing differences
# than Quiz 2's are also caught, far above what a page at its blank's own size ever gained.
SIZE_GAIN = .05


def estimate(image):
    """(angle, confidence): the rotation (PIL's sense, counter-clockwise degrees) that makes the printed lines
    horizontal, to a tenth of a degree, and how decisively the page says so."""
    gray = image.convert("L")
    scale = WIDTH / max(gray.size)
    small = np.asarray(gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.BILINEAR), dtype=float)
    marks = Image.fromarray(((small < min(200, small.mean() - 25)) * 255).astype(np.uint8))  # dark marks only

    def sharpness(angle):
        rows = np.asarray(marks.rotate(float(angle), resample=Image.BILINEAR), dtype=float).sum(axis=1)
        return float(np.var(np.diff(rows)))
    coarse = {float(a): sharpness(a) for a in np.arange(-LIMIT, LIMIT + .01, 1.0)}
    best = max(coarse, key=coarse.get)
    fine = [a for a in np.arange(best - 1, best + 1.001, .1) if -LIMIT <= a <= LIMIT]
    angle = max(fine, key=sharpness)
    median = float(np.median(list(coarse.values())))
    confidence = coarse[best] / median if median > 0 else 0.0
    return round(float(angle), 1), round(confidence, 1)


def plan(image):
    """What to do with this page, as recorded with what it produced."""
    angle, confidence = estimate(image)
    return {"rule": RULE, "angle": angle, "confidence": confidence,
            "applied": bool(abs(angle) >= MINIMUM and confidence >= CONFIDENT)}


def straighten(image, angle, fill):
    """The page turned upright about its centre in the same frame, and a mask of the pixels the scanner captured
    (what was outside the scan is filled, and must not count as blank paper when comparing with a blank exam)."""
    turned = image.rotate(angle, resample=Image.BICUBIC, fillcolor=fill)
    captured = Image.new("L", image.size, 255).rotate(angle, resample=Image.NEAREST, fillcolor=0)
    return turned, captured


def upside_down(candidates):
    """The same (page, captured area) candidates turned half a turn about their centre. A half turn commutes with the
    straightening above (a line's tilt is the same either way up), so the tilt read from the page as scanned still holds."""
    half = Image.Transpose.ROTATE_180
    return [(page.transpose(half), None if captured is None else captured.transpose(half)) for page, captured in candidates]


def turned_wins(upright, turned):
    """Whether the page turned half a turn aligns clearly better with the blank exam than the page as scanned. A plain bool:
    the qualities are NumPy values, and JSON refuses `numpy.True_` (the sandbox then reports the whole crop as failed)."""
    return bool(upright is not None and turned is not None and turned - upright >= TURN_MARGIN)


def resized(image, size, fill):
    """The image resized by `size` about its centre, in its own frame: what grows past the edge is cut off, what shrinking
    uncovers is filled. At size 1 it is the very same image."""
    if size == 1:
        return image
    width, height, inverse = image.width, image.height, 1 / size
    return image.transform(image.size, Image.AFFINE, (inverse, 0, width / 2 * (1 - inverse), 0, inverse, height / 2 * (1 - inverse)),
                           resample=Image.BILINEAR, fillcolor=fill)


def search(score, angles):
    """The best alignment and the one at the blank's own size, each (quality, size, angle, *details), where
    `score(size, angle)` returns (quality, *details). First the blank at its own size over `angles`, exactly the earlier
    search; then coarse sizes at that angle, the angles again at the best of them, and finer sizes around it. When no coarse
    size comes near the gain the search stops there, so a page at its blank's own size costs a dozen more comparisons."""
    def best(pairs):
        found = None
        for size, angle in pairs:
            result = score(size, angle)
            if found is None or result[0] > found[0]:
                found = (result[0], size, angle, *result[1:])
        return found
    own = best((1, angle) for angle in angles)
    coarse = best((size, own[2]) for size in SIZES if size != 1)
    if coarse[0] - own[0] < SIZE_GAIN / 2:
        return own, own
    around = best((coarse[1], angle) for angle in angles)
    finer = best((round(around[1] + step, 3), around[2]) for step in FINER)
    found = finer if finer[0] > around[0] else around
    return (found if found[0] - own[0] >= SIZE_GAIN else own), own


def masked(ink, mask):
    """Zero-mean ink over the captured area only, zero outside it. With no mask this is exactly `ink - ink.mean()`."""
    if mask is None:
        return ink - ink.mean()
    inside = mask > 0
    if not inside.any():
        return np.zeros_like(ink)
    return np.where(inside, ink - ink[inside].mean(), 0.0)
