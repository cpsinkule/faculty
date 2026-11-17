"""The spot to look at (requirement, 2026-11-14). GRADING FLAG REVIEW "THE SPOT TO LOOK AT".

A flag review leaves a response with the instructor when marks that decide credit can be read two ways, and names the readings
(`flag_resolution.readings_of`). Its note says where to look in words only: "the small exponent on 9, just above the boxed x=7". Before
the review finishes, the same model is asked once more, for that response alone (its crops, the note and the readings), where those
marks are: one to three spots, each a short label and a box on one crop. The review desk outlines them on the scan.

MEASURED 2026-11-14 on the 177 saved notes with reading choices (`/mnt/nvme/Teaching/tmp/flag-box-trial/`), asked this way of
claude-opus-5-5: all 177 answered, 4.7 s each, 41 with two or three spots. By eye on 42 of them the boxes were on the marks in 36, on
the neighbouring mark in 5 and on the wrong place in 1. Jev 1.14's Box, given the same note, was wrong in 9 of the 42 and gives one
box only, so it is not used. No stored box could stand in: since direct reading the transcript carries almost none.

What it is and is not:
- An outline, never evidence. It decides nothing, changes no grade, transcript or flag, and is not shown to any model.
- Asked only for a response left with the instructor WITH reading choices, by the review that left it, as one of that review's own
  cloud calls (step `look:<response id>`, replayed from its receipt like the rest).
- A look that fails, or whose every box is unusable, leaves the note as it was: words and choices, no outline. It never fails the
  review or the response. Only a wait (a usage limit) is passed on, as for a ruling.
- A finished review's result is immutable (migration 033). The notes that still waited when this was built (requirement, 2026-11-14)
  were given theirs by one counted `flag_looks_added` event naming the review, which `flag_review_note` reads where the ruling has none.
"""
from .models import box

RULE = "flag-look-v1"
PROMPT = "flag_look"
SPOTS = 3        # Spots one look may name.
LABEL = 80       # Characters kept of a spot's label.
PARALLEL = 4     # Looks of one question asked at once.
ADDED = "flag_looks_added"  # The event that gives a finished review's waiting notes their outlines: {resolution_id, looks: {response: spots}}.
CROPS = 64       # Crops a response may have, where a stored look is read back without them (the desk draws a spot only on a crop it has).


def wanted(response):
    """Whether this ruled response gets a look: left with the instructor, with reading choices."""
    return response.get("outcome") == "unreliable" and len((response.get("ruling") or {}).get("readings") or []) >= 2


def payload(ruling):
    return {"note": str(ruling.get("reason") or "")[:2000], "readings": [str(r["reading"])[:300] for r in ruling["readings"]]}


def spots_of(raw, crops):
    """The usable spots of a look's answer: [{label, crop, bbox}], at most `SPOTS`, each on a crop that exists with a rectangle of
    positive area inside it. A spot that is not is dropped, never repaired; anything malformed gives []."""
    found = []
    for entry in raw.get("spots") or [] if isinstance(raw, dict) and isinstance(raw.get("spots"), list) else []:
        if not isinstance(entry, dict):
            continue
        crop = entry.get("crop", 0)
        try:
            rectangle = box(entry.get("bbox"))
        except (ValueError, TypeError, OverflowError):
            continue
        if rectangle is None or type(crop) is not int or not 0 <= crop < crops:
            continue
        found.append({"label": " ".join(str(entry.get("label") or "").split())[:LABEL], "crop": crop, "bbox": rectangle})
    return found[:SPOTS]
