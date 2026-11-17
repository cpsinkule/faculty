"""MULTIPLE-CHOICE READER (requirement, 2026-10-08).

For a question that is multiple choice (`resolved`: the drafted two-line rubric and its printed options), OpenAI Decisions
reads which printed option the student marked from the answer area's crop: one Choice over the options plus "none" and
"several", asked in both option orders in one request. When both orders name the same option at the frozen cutoff or more, code
compares it with the rubric's correct choice and ticks the rubric line: the instructor's exception to GRADING "A final answer is EVIDENCE,
NEVER A GRADE", for these questions alone. The read is ONE assessment; its two orders check position bias and never stand in for a
second grader. Anything else (orders that disagree, a lower confidence, none, several, a failed request) goes on to the ordinary
Cloud path: OCR or direct reading, the reader and the grading team.

Sandbox of 2026-10-07 (decisions_sandbox/checks/mc.py, the same request): 66 of 66 quiz answer areas read consistently with their
grade, including 20 flagged ones; median confidence .98, 5 below .8."""
import io
import re

from jev.service import agreed, both

RULE = "choice-reader-v1"
SETTING = "choice_reader"
NONE, SEVERAL = "none", "several"
LABELS = tuple(f"({c})" for c in "abcde")
CORRECT = re.compile(r"^\s*Correct choice:\s*\$*\s*\(([a-e])\)", re.I)
MAX_SIDE = 1600  # the sandbox's preparation: grayscale, long side at most 1600 px
# The cutoff for a confident read: both orders name the same option with the lower confidence at least this. The instructor's choice
# (2026-10-08) on the calibration over all 952 quiz multiple-choice answer areas (`manage_decisions_sandbox.py mc-calibrate`, the
# request of then): at .8 the reader grades 834 (88%), 23 of the 30 the instructor reviewed, and no grade differs from the current one;
# no cutoff from .5 to .95 made one differ. With ASK (2026-10-16, 954 areas) it grades 880, none differing. The rest are graded by the ordinary Cloud path, never left to the instructor.
CUTOFF = .8
STATE = ("The image is a student's answer area for one multiple-choice question on a printed quiz: the question, its lettered "
         "options, and the student's handwriting.")
ENDPOINT = {"name": "openai-decisions", "kind": "decisions", "model": "gpt-6-luna", "lineage": "openai", "modality": "vision",
            "max_inflight": 4, "per_minute": 0, "profile": "", "key_env": "OPENAI_API_KEY", "price_in": .10, "price_out": 0.0}


def enabled(settings):
    """Whether a batch froze the reader (`jobs.create_job`): a batch from before it, or with the setting off, never reads by choice."""
    return bool(settings.get(SETTING)) and settings.get("choice_reader_rule") == RULE


def lines(rubric):
    """{correct, wrong, mode, key} of a multiple-choice rubric exactly as drafted (GRADING "MULTIPLE CHOICE IS PASS/FAIL ON THE
    CHOICE ALONE"): two lines and nothing else, "Correct choice: (x)" for every point (positive) or worth 0 beside a line taking
    them all (deducting), and the other worth 0 (positive) or minus every point (deducting). Any other rubric is None."""
    items = (rubric or {}).get("items") or []
    if len(items) != 2 or (rubric or {}).get("groups") or any(i.get("group") or i.get("requires") or i.get("excludes") for i in items):
        return None
    marked = [(CORRECT.match(i.get("description") or ""), i) for i in items]
    correct = [(m, i) for m, i in marked if m]
    if len(correct) != 1:
        return None
    match, right = correct[0]
    wrong = next(i for _, i in marked if i is not right)
    mode = rubric.get("mode")
    if mode == "positive" and not (right["points"] > 0 and wrong["points"] == 0):
        return None
    if mode == "negative" and not (right["points"] == 0 and wrong["points"] < 0):
        return None
    if mode not in ("positive", "negative"):
        return None
    return {"correct": right["id"], "wrong": wrong["id"], "mode": mode, "key": f"({match.group(1).lower()})"}


def printed(problem):
    """The option labels the statement prints, in order: "(a)" to "(e)"."""
    text = problem if isinstance(problem, str) else str(problem or "")
    return [label for label in LABELS if label in text]


def propose(content, rubric, rubric_id):
    """What Faculty proposes for one grading question, or None: {options, correct, rubric_id, source}, from the rubric's shape and the
    options its statement prints (a drafted quiz's multiple-choice problem is drafted in exactly that shape). One answer area, at
    least two printed options, and the rubric's key among them."""
    shape = lines(rubric)
    if not shape or len(content.get("regions") or []) > 1:
        return None
    options = printed(content.get("problem"))
    if len(options) < 2 or shape["key"] not in options:
        return None
    return {"options": options, "correct": shape["key"], "rubric_id": rubric_id, "source": "rubric"}


def clean(value):
    """What the instructor saves on a question (`content.choice_grading`): {"written": true} to grade it as written work although its
    rubric is multiple choice, or None to go back to the automatic rule."""
    if value is None:
        return None
    if value != {"written": True}:
        raise ValueError('Choose written work ({"written": true}) or the automatic rule (null).')
    return {"written": True, "rule": RULE}


def resolved(content, rubric, rubric_id):
    """AUTOMATIC CONFIRMATION (requirement, 2026-10-08): the multiple-choice
    grading in force for a question's pinned revision and rubric, or None. A question the instructor set to written work stays so; a
    confirmation saved before this rule stands for its own rubric; anything else is the exact rule (`propose`), read from the pinned
    question and rubric themselves, so nothing is saved and no proposal goes stale."""
    saved = content.get("choice_grading")
    if isinstance(saved, dict) and saved.get("written"):
        return None
    if isinstance(saved, dict) and saved.get("correct"):
        return saved if saved.get("rubric_id") == rubric_id else None
    found = propose(content, rubric, rubric_id)
    return {**found, "automatic": True, "rule": RULE} if found else None


def applies(content, rubric, rubric_id, regions, settings):
    """Whether this task is read by choice: the batch froze the reader, the question is multiple choice for its pinned rubric
    (`resolved`) and it has one answer area. Its built-in rules and exclusive pairs fit a two-line rubric whichever line is ticked.
    A finalization's clarifications no longer hold it back (2026-10-17, Quiz 5: "I thought a decision model was supposed to
    grade the multiple choice"): the finalization writes one for every multiple-choice line, saying how a choice is marked, so
    the hold kept nearly every one from the reader since it was built (2026-10-08); the 954 areas the reader was calibrated on
    carried them, and none differed. A question the reader should not grade (a misprint, a thrown-out option) is set to written
    work at Key & Rubric Finalization."""
    return bool(enabled(settings) and regions == 1 and resolved(content, rubric, rubric_id))


def prepare(png):
    """The crop as the reader sees it: grayscale, long side at most MAX_SIDE, PNG."""
    from PIL import Image
    picture = Image.open(io.BytesIO(png)).convert("L")
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()


# The question asked (requirement, 2026-10-16). Replayed on the same 954 answer areas the longer wording was calibrated on: at CUTOFF Decisions grades 880
# (the longer wording 836), no answer differs from its current grade at any cutoff from .5 to .95, and no answer both wordings
# grade is read differently. The longer wording named "ticked" beside "circled", which a tick beside another option answered.
ASK = "Which answer choice did the student select?"


def questions(options):
    criteria = {o: o for o in options}
    criteria.update({NONE: "No answer choice is selected.", SEVERAL: "More than one answer choice is selected."})
    return both("marked", ASK, criteria)


def decide(answers, options, cutoff):
    """{choice, confidence, orders_agree, probabilities, confident}: the option both orders name with the lower of their
    confidences, the probabilities averaged over both orders, and whether it is a confident single option."""
    first, second = answers["marked"], answers["marked__reversed"]
    pick, sure = agreed(answers, "marked")
    mean = {o: round((first["probabilities"][o] + second["probabilities"][o]) / 2, 4) for o in first["probabilities"]}
    return {"choice": pick, "confidence": round(min(first["confidence"], second["confidence"]), 4), "orders_agree": pick is not None,
            "probabilities": mean, "confident": bool(pick in options and sure >= cutoff)}


def grade(read, grading, rubric, maximum, validate):
    """The `grading` step of a confident read, through the ordinary grade validation (`recognition.validate_grade`): the transcript
    is the read label alone, quoted as the final answer and as the evidence of the line it ticks."""
    shape = lines(rubric)
    letter, confidence = read["choice"], read["confidence"]
    right = letter == grading["correct"]
    transcript = {"lines": [{"id": "choice-1", "elements": [{"type": "text", "text": letter, "crop": 0, "confidence": confidence}]}],
                  "plain_text": letter, "latex": "", "confidence": confidence, "ambiguities": []}
    quote = [{"line_id": "choice-1", "student_text": letter, "crop": 0}]
    said = f"The multiple-choice reader read {letter} in both option orders (confidence {confidence:.2f}); the confirmed key is {grading['correct']}."
    raw = {"applications": [{"rubric_item_id": shape["correct"], "applied": right, "reason": said, "evidence": quote if right else [],
                             "confidence": confidence},
                            {"rubric_item_id": shape["wrong"], "applied": not right, "reason": said, "evidence": [] if right else quote,
                             "confidence": confidence}],
           "interpretation": {"meaning": f"The student marked {letter}.", "method": "", "work_consistent": "unclear", "attempts": 1,
                              "error_graph": [], "final_answers": [{"part": "", "text": letter, "line_id": "choice-1"}]},
           "confidence": confidence, "review_reasons": [], "rubric_ambiguities": [], "reading_issues": []}
    from .models import transcript as checked
    reading = checked(transcript)
    graded = validate(raw, reading, rubric, maximum, 1)
    return {**graded, "choice": {**summary(read), "transcript": reading, "key": grading["correct"], "options": grading["options"]}}


def summary(read):
    return {"rule": RULE, "model": ENDPOINT["model"], "choice": read["choice"], "confidence": read["confidence"],
            "orders_agree": read["orders_agree"], "probabilities": read["probabilities"], "confident": read["confident"]}


def committee(read):
    """The team record of a confident read, as a flag review settles one (`flag_resolution.amend`): resolved by the reader, with no
    grading team convened."""
    return {"status": "resolved", "team_status": "not_convened", "resolved_by": "choice_reader", "source": "choice_reader",
            "supporter": None, "third_required": False, "workflow": RULE,
            "reason": f"The multiple-choice reader read {read['choice']} in both option orders at confidence {read['confidence']:.2f}."}
