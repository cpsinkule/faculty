"""Cloud Standard reads the paper itself, with no OCR draft beside it (requirement, 2026-09-21).

WHY. Measured that day on 34 hard crops of Test 1 and 12 readings verified by eye against the images. Under `reconcile-v6`
with the Mathpix draft, BOTH cloud readers rewrote a student's handwritten $40,206 as the $46,206 the PRINTED question
gives - the mis-copy their whole solution was built on, and the one thing a transcription must never repair. Three things
did not fix it, each measured on the same crops: saying it outright in the prompt (qwen and Gemini scored identically,
still 46,206 six times), removing the OCR candidate (identical again), and a smaller contract with the plain wording
(qwen 6 of 12, BELOW its 7 with OCR). What fixed it was the reader answering in PLAIN TEXT: qwen3-vl-32b 7 of 12 -> 10,
Gemini 8 -> 12, and both then wrote the five 40,206s and the one 46,206 exactly where the student did. Printed-text
leakage fell with it (qwen: 15 responses of 32 -> 3).

SO THIS MODE KEEPS BOTH. The reader reads once in free text (the `read` prompt), and the ordinary `reconcile` call then renders
that reading into the contract the rest of the loop needs - line IDs, crossed_out/inserted/unclear states, ambiguities,
layout relations - with the free reading as its only candidate. Measured on Gemini with Mathpix still beside it: 11 of
12, the digits right, the struck block still marked crossed out.

HONESTY. The free pass is the SAME model on the SAME image, so it is never independent evidence and must never answer a
check that asks whether a second reader stood beside the vision reader. `independent()` is the one seam: it hides this
candidate from those checks, `risk.assess` then finds no independent reading and WAIVES that block by name (recorded in
`signals.risk.waived`, never silently), and `answers.readers_agree` is None rather than False, because with no second
reader there is nothing two readers could differ on. Nothing manufactures agreement.

The mode is part of the acceptance scope, so a permission earned with OCR on does not carry into it, and the frozen
profile owns it: a batch finishes the way it was submitted.

THE ARRANGEMENT KEEPS THE FIRST READING'S NUMBERS (v3, requirement, 2026-09-29). The reconcile
call still had the image and leave to overrule the free reading as "a draft like any other", and it did what the free reading was
introduced to stop: on Quiz 2 and Quiz 3, where it swapped one number for another and one of them is a value the rubric states, it
moved TOWARD the rubric's value 25 times and away 5 (Muse Spark 1.3). Under `reconcile-v8` a free reading is the model's own and
is only arranged, never re-read; and `introduced` checks it: a number the transcript writes that the free reading does not have is
asked about once more (`introduced_numbers`), and if it stays, the transcript keeps it with a reading doubt naming both, so the
response waits for the instructor or the flag review, which can correct the line from the crop.
"""
import re
from collections import Counter

from .models import _characters, plain_transcript

SETTING = "direct_reading"
# v2 (requirement, 2026-09-22): a crop with nothing written on it is a BLANK ANSWER for the instructor to see, not a failed
# reading. Measured on the trial's first real batch: 8 of 240 responses died at the reading, and the retained reasoning of
# every one says the model examined the image, found only printed text and chose to answer nothing ("If there is no
# handwritten work, what is the expected output? ... The most accurate representation is, well, nothing"). The earlier
# readings of those same papers held 0 to 4 lines: they are the unanswered questions of the exam.
LEGACY_RULE = "direct-reading-v1"
RULE = "direct-reading-v4"   # Literal content, per crop, including omissions and whether writing was struck.
PROVIDER = "direct"          # The candidate's provider name. Never an OCR provider, and never independent evidence.
PROMPT = "read"              # grading/prompts/read-v3.txt: plain text out, no JSON contract.
TIER = "cloud_standard"


def enabled(settings, tier=""):
    """Cloud Standard only (the one cloud tier), as asked."""
    return bool(settings.get(SETTING)) and tier == TIER


def rule(settings):
    """The rule this batch runs under: the one its profile froze, else the current one.

    No batch can be frozen on v1, because the key did not exist while v1 did; and v1's answer to an empty reading was a
    failed component, never a grade, so nothing an older batch produced depends on it. A batch whose blank crops failed
    can therefore be retried under v2 and finish, which is the point. The key is frozen from here on so that a later rule
    can leave v2 batches alone.
    """
    return settings.get("direct_reading_rule") or RULE


def blank(readings):
    """Which of a response's crops the reader found nothing written on, by index: the reason it goes to review."""
    return [index for index, reading in enumerate(readings) if ((reading or {}).get("raw") or {}).get("empty")]


def independent(candidates):
    """The readings that count as evidence BESIDE the vision reader. The reader's own first pass is not one of them."""
    return [c for c in candidates if c.get("provider") != PROVIDER]


def waived(components):
    """Whether the checks about a second reader are the ones this mode removed, so they are recorded rather than answered."""
    return bool((components.get("ocr") or {}).get("direct"))


def read(call, endpoint, image, settings, owner):
    """One crop, read in free text. Shaped exactly like an OCR provider's `recognize`, so the stage combines it unchanged."""
    result = call({**owner}, "reader", endpoint, {"prompt": PROMPT, "payload": {}, "images": [image]}, settings)
    text = (result["output"] or {}).get("text", "")
    if not str(text).strip():
        # The empty reading is KEPT and marked. It goes on exactly like any other, so the reconcile call looks at the image
        # and decides what is on it; the mark becomes a reading reason, which sends the response to the instructor
        # (`ocr_review`) and keeps it out of automatic finalization. Nothing here decides that the page is blank.
        return {"provider": PROVIDER, "transcript": plain_transcript(""), "raw": {"rule": rule(settings), "empty": True},
                "metadata": {**result.get("metadata", {}), "rule": rule(settings), "prompt": PROMPT, "empty": True}}
    return {"provider": PROVIDER, "transcript": plain_transcript(str(text)), "raw": {"rule": rule(settings)},
            "metadata": {**result.get("metadata", {}), "rule": rule(settings), "prompt": PROMPT}}


def keeps_numbers(settings):
    """Whether this batch checks that the arranged transcript keeps the free reading's numbers (v3 and later)."""
    return rule(settings) not in (LEGACY_RULE, "direct-reading-v2")


def keeps_literal(settings):
    return rule(settings) == "direct-reading-v4"


def reconcile_prompt(settings):
    return "reconcile_literal" if keeps_literal(settings) else "reconcile"


def _marked(value):
    """The free reader's three markers, with balanced brackets (a matrix can occur inside a struck span)."""
    start = 0
    marker = re.compile(r"\[(crossed out:|marked:|illegible\])")
    while found := marker.search(value, start):
        yield value[start:found.start()], "written"
        if found[1] == "illegible]":
            yield "", "unclear"
            start = found.end()
            continue
        end, depth = found.end(), 1
        while end < len(value) and depth:
            depth += (value[end] == "[") - (value[end] == "]")
            end += 1
        if depth:  # Malformed markers are literal text, never permission to discard the rest of a reading.
            yield value[found.start():], "written"
            return
        yield value[found.end():end - 1], "crossed_out" if found[1] == "crossed out:" else "written"
        start = end
    yield value[start:], "written"


def _literal(parts):
    """The quotation check's spelling normalization, with boundaries kept between numbers/words. No algebra.

    Adjacent elements may split a written line. Struck writing is excluded on BOTH sides: changing its spelling is not a
    grading concern, but resurrecting it, or striking active writing, changes this sequence. An illegible mark stays unknown.
    """
    text, tokens = [], []
    def flush():
        value = _characters(" ".join(text))
        tokens.extend(re.findall(r"\\[A-Za-z]+|[^\W\d_]+|\d+(?:\.\d+)?|[^\s]", value))
        text.clear()
    for value, state in parts:
        if state == "crossed_out":
            flush()
        elif state == "unclear" and not value.strip():
            flush()
            tokens.append("[illegible]")
        else:
            text.append(value)
    flush()
    return tokens


def literal_changes(candidates, arranged):
    """Material differences between the free reading and each crop's arrangement. Compare both retained spellings.

    Line IDs, line breaks, harmless TeX spelling and geometry may change; literal order, signs, punctuation, choices and
    active/struck status may not. The returned excerpts are private response evidence, never log text.
    """
    before = {}
    for candidate in candidates:
        if candidate.get("provider") != PROVIDER:
            continue
        for line in candidate["transcript"].get("lines", []):
            for el in line["elements"]:
                before.setdefault(el.get("crop", 0), []).extend(_marked(el.get("text") or el.get("latex") or ""))
        for crop in candidate.get("blank_crops", []):
            before.setdefault(crop, [])
    after = {}
    for line in arranged["lines"]:
        for el in line["elements"]:
            after.setdefault(el.get("crop", 0), []).append(el)
    changes = []
    for crop in sorted(before.keys() | after.keys()):
        expected = _literal(before.get(crop, []))
        seen = set()
        for form, other in (("text", "latex"), ("latex", "text")):
            actual = _literal((el.get(form) or el.get(other) or "", el.get("state", "written")) for el in after.get(crop, []))
            if actual != expected and tuple(actual) not in seen:
                changes.append({"crop": crop, "form": form, "first": " ".join(expected)[:600], "arranged": " ".join(actual)[:600]})
                seen.add(tuple(actual))
    return changes


def literal_doubt(changes):
    crops = ", ".join(str(crop + 1) for crop in sorted({change["crop"] for change in changes}))
    return f"The arranged transcript changes or omits literal writing from the reader's first pass in answer area(s) {crops}; compare both readings with the scan."


def first_reading(candidates):
    """The free reading of a response's crops (the reader's own first pass), or None when this reading had none."""
    found = [c for c in candidates if c.get("provider") == PROVIDER]
    return "\n".join(c["transcript"].get("plain_text", "") for c in found) if found else None


_DIGITS = re.compile(r"\d+")


def _written(transcript):
    return "\n".join(" ".join(el.get("text") or el.get("latex") or "" for el in line["elements"]) for line in transcript.get("lines", []))


def _joins(runs):
    """What two or three consecutive digit runs read as when written together (17,600 as 17600; 11.3334 as 113334)."""
    return {"".join(runs[i:i + size]) for size in (2, 3) for i in range(len(runs) - size + 1)}


def _parts(runs, wholes):
    """The runs that are consecutive pieces of one of `wholes` written apart (17600 written 17,600)."""
    found = Counter()
    for size in (2, 3):
        for i in range(len(runs) - size + 1):
            if "".join(runs[i:i + size]) in wholes:
                found.update(runs[i:i + size])
    return found


def _extra(runs, other, joined, parts):
    """The runs of `runs` beyond those of `other`, in order, less those that only join or split `other`'s runs."""
    extra, found = Counter(runs) - Counter(other), []
    for run in runs:
        if extra[run] <= 0:
            continue
        extra[run] -= 1
        if parts[run] > 0:
            parts[run] -= 1
        elif run not in joined:
            found.append(run)
    return found


def introduced(first, transcript):
    """(numbers the transcript writes that the free reading does not have, numbers of the free reading it leaves out), each in
    reading order. Digit runs are compared as a multiset, so a line moved or an element split changes nothing, and a run that only
    joins or splits the free reading's runs (a separator added or dropped) is no new number. [illegible] is no number either."""
    before, after = _DIGITS.findall(first.replace("[illegible]", " ")), _DIGITS.findall(_written(transcript))
    gained, lost = set(Counter(after) - Counter(before)), set(Counter(before) - Counter(after))
    return (_extra(after, before, _joins(before), _parts(after, lost)),
            _extra(before, after, _joins(after), _parts(before, gained)))


def doubt(new, missing):
    """The reading doubt a number the transcript kept against the free reading raises: both readings, for the instructor."""
    return (f"The reader's transcript writes {', '.join(new)} where its own first reading of the crop has "
            f"{', '.join(missing) if missing else 'no such number'}; check these numbers against the crop.")
