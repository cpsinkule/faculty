"""JEV BLANK ANSWERS (requirement, 2026-10-15): a zero for an answer left blank stands without the
instructor's review when three things agree: the reader found every answer area empty and transcribed nothing, Jev looking at the
same crops says the problem was left blank at CUTOFF or above, and the proposed grade is 0 with nothing else holding it.

Measured that day on 272 graded answers (122 the reader found empty and the instructor confirmed one by one, 100 zeros with work
written, 50 with credit), every answer Jev scored .5 or above checked by eye with the contrast raised: at .9 and above, 79 calls,
every one an empty answer area (3 held erased pencil the instructor had scored 0); between .5 and .9, real marks (a circled number,
underlining, a few words). Applied to those 122, it clears 78 and leaves the rest, every multiple-choice answer with a mark among
them, for the instructor.

Jev sees only the answer areas (JEV "JEV PICTURES": Jev 1.14, pictures leave the server for this check). Work written elsewhere is
seen by neither reader. A failed, refused or uncertain check decides nothing: the answer waits as before. The receipt is the
ordinary Jev receipt (purpose `grading-blank-answer`, owned by the response), kept with the picture hashes only.
"""
from jev import client

RULE = "jev-blank-answers-v1"
PURPOSE = "grading-blank-answer"
CUTOFF = .8  # (GRADING "JEV BLANK ANSWERS")
MAX_CROPS = client.MAX_IMAGES
QUESTION = {"blank": {"type": "noul", "instructions": (
    "The images are the answer area(s) of one problem on a student's scanned math quiz or exam, cropped from the page; they include "
    "the printed problem text. Did the student leave this problem completely blank: no handwriting, marks, work or answer of their "
    "own anywhere in these images? Printed text, printed lines and scanner specks do not count as the student's writing; faint "
    "pencil, crossed-out work and a single number or word do.")}}
STATE = "Answer areas of one problem on a student's scanned paper."


def asked_about_the_empty_answer(signals):
    """The blocks that are only a grader asking for a look (GRADING "A GRADER'S REQUEST ON AN EMPTY ANSWER", requirement, 2026-10-26): the sentences saying a grader asked, and each grader's own request
    under its name, as the team keeps them (`committee`: "second: …"). On an answer whose every area was read empty and that nothing
    was transcribed from, a request can only be about that empty answer, which this check is for: on Exam 2 (2026-10-25) 19 of the 31
    blank zeros the instructor confirmed carried one, every one "there is no transcript line to quote". A grader's READING issue is not
    among them, nor a rubric doubt: those still hold the answer."""
    from . import risk as risk_policy
    asked = {risk_policy.GRADER_ASKED, risk_policy.SECOND_ASKED}
    for panel in signals.get("panels") or []:
        for role, opinion in (panel.get("opinions") or {}).items():
            for reason in (opinion or {}).get("review_reasons") or []:
                asked.update({f"{role}: {reason}", reason})
    return asked


# A WHOLE-PAGE ANSWER AREA (GRADING "A GRADER'S REQUEST ON AN EMPTY ANSWER", 2026-10-26). Alignment compares the page with the blank
# exam, so a page covered in writing scores low wherever it lies: every Exam 2 Question 23 page failed it (0.28 to 0.54 against the 0.55
# pass mark). An area this large is the page itself, so Jev is shown the whole scanned page instead of a crop that might be cut wrong;
# never a page with a privacy mask (a name box).
#
# AN UNALIGNED CROP (requirement, 2026-11-06) of any other area is shown to Jev as it was cut. A page left
# nearly empty aligns poorly too, though the shift it finds is right: 55 Quiz 6 and 7 answers held for nothing else scored 0.42 to
# 0.55. Shown the stored crop, Jev cleared 25 at .8, every one a correct zero (15 empty, 10 only an X or a tick over the problem),
# none with work; a crop 6% of the page taller did worse (18), since it takes in the answer above.
WHOLE_PAGE = .8


def whole_page(region):
    """Whether a task's region is a whole-page answer area Jev may see as its scanned page (`WHOLE_PAGE`)."""
    area = (region or {}).get("region") or {}
    return bool(area.get("width", 0) >= WHOLE_PAGE and area.get("height", 0) >= WHOLE_PAGE and not region.get("privacy_masks"))


def shown_whole(crops, regions):
    """The indices of the areas Jev sees as their whole scanned page: an unaligned crop of a whole-page area."""
    regions = list(regions or ())
    return [i for i, crop in enumerate(crops.get("crops") or [])
            if not crop["metadata"].get("registered") and i < len(regions) and whole_page(regions[i])]


def eligible(output, crops, regions=()):
    """Whether everything that holds this proposal follows from the empty reading, as the blank-answer gallery asks
    (`answer_groups.blank_answer`): every area read empty and nothing transcribed, a zero with a line ticked, no disagreement
    between graders, no rubric concern, and no hold but the empty reading, a grader's request about it
    (`asked_about_the_empty_answer`) or an unaligned crop's (shown as cut, or as its page, `shown_whole`). `regions`: the task's
    source regions."""
    from . import risk as risk_policy
    signals = output.get("signals") or {}
    verdict = signals.get("risk") or {}
    components = verdict.get("components") or {}
    reading = components.get("reading") or {}
    areas = crops.get("crops") or []
    blocks = verdict.get("blocks") or []
    try:
        zero = float((output.get("score") or {})["score"]) == 0
    except (KeyError, TypeError, ValueError):
        zero = False
    asked = asked_about_the_empty_answer(signals)
    if any(not crop["metadata"].get("registered") for crop in areas):
        asked.add(risk_policy.CROP_UNSURE)  # AN UNALIGNED CROP: Jev sees it as cut, or the whole page (`shown_whole`).
    # Held for grading too ("both") only by such a request: anything else that holds the grade keeps it with the instructor.
    state = output.get("state") == "ocr_review" or (output.get("state") == "both" and any(b in asked for b in blocks))
    return bool(state and areas and len(areas) <= MAX_CROPS
                and reading.get("blank_crops") == len(areas) and not (output.get("transcript") or {}).get("lines")
                and zero and not signals.get("empty_selection") and any(risk_policy.blank(b) for b in blocks)
                and all(risk_policy.blank(b) or b in asked for b in blocks)
                and not risk_policy.selection_disagreement(components) and not (signals.get("rubric_quality") or {}).get("reasons"))


def check(jev, response_id, pictures):
    """{rule, cutoff, status, noul, receipt}: Jev's answer for these crops (PNG or JPEG bytes each)."""
    answer = jev.ask(PURPOSE, response_id, STATE, QUESTION, RULE, images=[client.prepare_image(p) for p in pictures])
    found = {"rule": RULE, "cutoff": CUTOFF, "status": answer.get("status") or "failed", "receipt": answer.get("id")}
    if found["status"] == "complete":
        found["noul"] = answer["answers"]["blank"]["noul"]
    return found


def apply(output, verdict, found):
    """The proposal with Jev's answer on the record. Where Jev says blank at CUTOFF or above, the empty reading is settled the way a
    ruling settles what it was shown (`arbitration.apply`): the blocks it held stay on the record under `jev_blank`, and the zero
    goes to the ordinary acceptance gate. Anything else leaves the proposal exactly as it was, waiting for the instructor."""
    signals = output["signals"]
    signals["jev_blank"] = found
    if found.get("status") != "complete" or found.get("noul", 0) < CUTOFF:
        return output, verdict
    risk = signals["risk"]
    signals["jev_blank"] = {**found, "outcome": "blank", "settled": {"blocks": risk.get("blocks", []), "reasons": output["reasons"],
                                                                     "individual": bool(risk.get("individual")), "state": output["state"]}}
    signals["risk"] = {**risk, "blocks": [], "individual": False}
    verdict = {**verdict, "blocks": [], "individual": False}
    if signals.get("rubric_quality"):
        # The rubric finalization is projected again when the result is kept (`apply_quality`), from the state it saw before.
        signals["rubric_quality"] = {**signals["rubric_quality"], "base_state": "proposed"}
    output["reasons"], output["state"] = [], "proposed"
    return output, verdict
