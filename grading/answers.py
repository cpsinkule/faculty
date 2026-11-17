"""Deterministic facts about a student's final answer: evidence for review, never a grade.

A matching answer cannot award credit here. The coordinator's rubric pays for the
work, and often has its own line for a correct answer without it, so these facts
only (1) sort responses into groups an instructor can inspect together, keeping
thin work apart from shown work, and (2) raise a review reason when a proposal
contradicts them. No model is involved and nothing is solved or repaired.
"""
import re
from fractions import Fraction

from . import symbolic
from .models import digest, items, operand_before, span, text, unbracketed, whole_numbers

UNITS = re.compile(r"(?:units?|dollars?)$")
NO_WORK = re.compile(r"\bno work\b|\bwithout (?:any )?work\b|\bno (?:supporting )?justification\b|\bnot justified\b|\bunsupported\b", re.I)
NEEDS_WORK = re.compile(r"\bwork to justify\b|\bjustif(?:y|ied|ication)\b|\bsupport(?:ed|ing)? (?:by|with) work\b|\bshow(?:n|s|ing)? (?:all )?work\b", re.I)
# What `final_answers` and `reasons` say, by name (requirement, 2026-10-11: the review card shows one line per cause, and
# `review_queue.reason_causes` tells the causes apart by these names, never by matching words). Naming only: the words are unchanged.
UNQUOTED = "A reported final answer does not quote the retained transcript."
STRUCK_LINE = "A reported final answer is on a line the student crossed out."
STRUCK_WORK = "A reported final answer quotes work the student crossed out."
KEY_MISMATCH = "Credit for a correct answer was proposed, but the final answer does not match the answer key."
ONE_READING = "The final answer matches the key in one reading only; confirm what is written."
LITTLE_WORK = "Full credit was proposed with little visible work; check that the work justifies the answer."
NOT_FULL = "The final answer matches the key and work is shown, but full credit was not proposed; check the selection."
NO_WORK_LINE = "The no-work line was proposed although work is visible; check the selection."
ADDS_UP = "Partial-credit lines add up to full marks, yet the full-credit line was not proposed and the final answer does not match the key; check for lines that exclude each other."


# A whole number and a proper fraction beside it, with nothing that binds tighter on either side: `23 1/8` or `23\frac{1}{8}`.
MIXED = re.compile(r"(?<![\w.)\]}^/*×·\\])(\d{1,15})(?:[ \t]+(\d{1,15})\s*/\s*(\d{1,15})|\s*\\[dt]?frac\s*\{\s*(\d{1,15})\s*\}\s*\{\s*(\d{1,15})\s*\})"
                   r"(?![\w.(\[{^/]|\\[a-zA-Z]|,\d|\s*(?:[(\[{^/*×·]|\\(?:times|cdot|[dt]?frac)(?![a-zA-Z]))|\s+(?:[a-zA-Z](?![a-zA-Z])|\d))")
NUMBER = re.compile(r"[+-]?(?:(?:\d{1,40}(?:\.\d{0,40})?|\.\d{1,40})(?:e[+-]?\d{1,2})?|\d{1,40}/\d{1,40})")


def _mixed(value):
    """A mixed number is ONE number: `23 1/8` is 185/8, never 231/8 (review, 2026-09-22: that spelling said a correct 23 1/8 was
    another value than 185/8 and the same as 28.875). Only a whole number, a space (or the bar written `\\frac`) and a proper fraction
    with nothing that binds tighter on either side; anything else stays apart and is a value this check cannot read."""
    def exact(match):
        whole, top, bottom = int(match[1]), int(match[2] or match[4]), int(match[3] or match[5])
        return str(whole + Fraction(top, bottom)) if 0 < top < bottom else match[0]
    return MIXED.sub(exact, value)


def _bar(match):
    """`\\frac{a}{b}`: a lone fraction of a plain numerator and a one-number or one-letter denominator is one pair of parentheses,
    so `\\frac{43}{2}x` is spelled like a typed (43/2)x; anything else keeps both pairs, and a product in a denominator its own."""
    top, bottom = match[1].strip(), match[2].strip()
    return f"({top}/{bottom})" if re.fullmatch(r"-?[\w.]+", top) and re.fullmatch(r"-?(?:\d[\d.]*|[a-zA-Z])", bottom) else f"({match[1]})/({match[2]})"


def normalize(value):
    """One comparable spelling of what was written. Conservative: unknown notation simply never matches."""
    value = str(value or "")
    value = symbolic.strip_thousands(value)  # 40,000 is one number
    value = _mixed(symbolic.unwrap_text(value))
    for _ in range(4):  # Nested fractions are rare; a bounded pass keeps this linear.
        value = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", _bar, value)
    value = re.sub(r"\\(?:times|cdot)|[×·*]", "x", value)
    value = re.sub(r"\\\$", "", value)  # An escaped currency sign is a currency sign, not a command.
    value = re.sub(r"\\(?:left|right|,|;|!|:|quad|qquad| )|\\\\|\$|\\\(|\\\)|\\\[|\\\]", "", value)
    value = value.replace("−", "-").replace("–", "-").replace("{", "(").replace("}", ")").casefold()
    value = re.sub(r"^\s*[a-z][a-z ]{1,24}:\s*(?=\S)", "", value)  # "Function:" or "Answer:" printed before the answer line is a label
    # Whitespace is layout, except between two digits: `1 500` is not 1500 and `3 1/2x` is no number at all (review, 2026-09-22).
    value = re.sub(r"\s+", lambda m: " " if value[m.start() - 1:m.start()].isdigit() and value[m.end():m.end() + 1].isdigit() else "", value)
    body, tail = re.fullmatch(r"(.*?)([.;,…]*)", value, re.S).groups()
    value = UNITS.sub("", value if ".." in tail or "…" in tail else body)  # A full stop ends a sentence; `0.333...` is not 0.333.
    if "/" in value:
        # (168)/(11) is 168/11 and (4z)/4 is 4z/4, but a product in a DENOMINATOR keeps its parentheses: 27/(2x) is not (27/2)x.
        # Both were one spelling until 2026-09-21, when a reviewer caught by eye a transcript that read the one for the other.
        # A parenthesis also stays wherever leaving it out could change the value (`models.unbracketed`, review 2026-09-22):
        # `2(3)/4` is not 23/4, `x(-3)/2` is not x-3/2 and `(-2)^2/4` is not -2^2/4. A letter after a lone fraction is its
        # product, as `(27/2)x` always was: `\\frac{43}{2}x` is spelled 43/2x like the typed one.
        keep = lambda denominator=False: lambda m: m[1] if unbracketed(m.string, m.start(), m.end(), denominator) else m[0]
        value = re.sub(r"\((-?[\w.]+)\)", keep(), value)
        value = re.sub(r"(?<=/)\((-?(?:\d[\d.]*|[a-z]))\)", keep(True), value)
        value = re.sub(r"\((-?[\w.]+/-?(?:\d[\d.]*|[a-z]))\)", keep(), value)
    return value


def _number(value):
    """A plain decimal or fraction, exactly. Nothing else: `1e99999999` would build a number of a hundred million digits."""
    if not NUMBER.fullmatch(value):
        return None
    try:
        return Fraction(value.lstrip("+"))
    except (ValueError, ZeroDivisionError):
        return None


def _value(value):
    """The right-hand side of a short label such as x=, y= or C(x)=; notation is the rubric's business, not this check's."""
    match = re.fullmatch(r"[a-z][\w()]{0,5}=(.+)", value)
    return match[1] if match else value


def _written_value(value):
    """The same right-hand side as `_value`, taken from the text as written so the symbolic parser sees real notation."""
    match = re.fullmatch(r"\s*\$*\s*[A-Za-z][\w()]{0,5}\s*=\s*(.+?)\s*\$*\s*", str(value or ""), re.S)
    return match[1] if match else str(value or "")


def _tuple(written):
    """The components of an ordered pair or triple AS WRITTEN, or None.

    Measured 2026-09-21: every full-credit answer to one version of a question was `unknown` to its key, because the closed grammar
    reads an expression and `(20 - 4t, 18 + 9t, t)` is three. Only a whole answer that IS the tuple: an optional `(x, y, z) =`
    before it, and after it nothing but the parameter's clause in words ("where t is any real number"), which has no number and
    no second tuple in it. Two tuples, words before the tuple, a product `(x-3)(x+2)` and a matrix are not read.
    """
    value = symbolic.unwrap_text(str(written or ""))
    value = re.sub(r"\\(?:left|right)(?![a-zA-Z])|\$|\\\(|\\\)|\\\[|\\\]", "", value)
    value = symbolic.strip_thousands(value)  # 40,000 is one number
    value = re.sub(r"^\s*\(?\s*[A-Za-z](?:\s*,\s*[A-Za-z])*\s*\)?\s*=\s*(?=\()", "", value).strip()
    if not value.startswith("("):
        return None
    depth, parts, start = 0, [], 1
    for at, character in enumerate(value):
        depth += character == "("
        depth -= character == ")"
        if depth == 1 and character == ",":
            parts, start = parts + [value[start:at]], at + 1
        if depth == 0:
            parts, rest = parts + [value[start:at]], value[at + 1:].strip(" \t\n.,;")
            break
    else:
        return None
    if len(parts) < 2 or not all(part.strip() for part in parts) or re.search(r"[\d()]", rest):
        return None
    return [part.strip() for part in parts]


def equivalent(left, right, checks=None):
    """Same answer? Spelling first, exact rationals next, and last the same VALUE in another form, decided symbolically.

    `21.5(x-3270)+79134` and `21.5x+8829` are one answer, and a real rubric lists both. Nothing is solved and no
    tolerance is used: a form the closed grammar does not certainly parse simply does not match. `checks` collects the
    symbolic records for the audit. Whether the NOTATION earns credit stays the rubric's business: groups keep exact notation.
    """
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return False
    if a == b or _value(a) == _value(b):
        return True
    pair = _tuple(left), _tuple(right)
    if all(pair):
        # An ordered pair or triple is its components, each compared as any answer is: `(20-4t, 18+9t, t)` is `(-4t+20, 9t+18, t)`.
        return len(pair[0]) == len(pair[1]) and all(equivalent(one, other, checks) for one, other in zip(*pair))
    x, y = _number(_value(a)), _number(_value(b))
    if x is not None and x == y:
        return True
    if checks is None or x is not None and y is not None:
        return False  # Two plain numbers that differ need no algebra.
    found = symbolic.equivalent(_written_value(left), _written_value(right))
    checks.append(found)
    return found["result"] == "pass"


def worded(written, accepted):
    """A key in words ("not possible") is matched by an answer that BEGINS with those words and goes on to give its reason.

    Measured 2026-09-21 on a question that asks to "write not possible and explain why": every correct answer was a sentence, none
    was the two words alone, and each was `unknown` to the key. Words only, whole words, from the first word: "possible" is not
    matched by "not possible", and an answer that merely contains the words somewhere is not read. The reason itself is the
    rubric's business.
    """
    words = re.findall(r"[a-z]+", str(accepted or "").casefold())
    if not words or re.search(r"[\d\\=^_{}]", str(accepted)) or sum(map(len, words)) < 3:
        return False
    # The reader's `\text{...}` around a sentence is its layout, not the student's first word (2026-09-21: `\text{not possible, ...}`
    # began with "text" and was unknown to a key it answers).
    written = symbolic.unwrap_text(str(written or ""))
    return bool(re.match(r"\W*" + r"\W+".join(map(re.escape, words)) + r"(?![a-z])", written.casefold()))


def compare(left, right, checks=None):
    """same | different | unknown. `different` means the student's answer was UNDERSTOOD and is another value.

    Seen on real papers: "When y decreases by 19 units, x decreases by 152/11." and "C(x) = $13.5 per item * x + $8541" are
    answers this check cannot read. Calling them a mismatch accused correct work of contradicting the key; they are unknown.
    """
    if equivalent(left, right, checks) or worded(left, right):
        return "same"
    pair = _tuple(left), _tuple(right)
    if all(pair):
        # Another value only for tuples of NUMBERS, every one of them understood: `(40000, 260000)` for `(40000, 280000)`. A
        # parametric answer is never accused: `(12+8z, 15-5z, z)` names the parameter differently, and `(t, -5t/8+45/2, t/8-3/2)`
        # is the key's `(8t+12, -5t+15, t)` with x as the parameter, which no comparison of components can tell. A pair is never
        # compared with a triple.
        numeric = not re.search(r"[a-zA-Z]", re.sub(r"\\[a-zA-Z]+", "", " ".join(pair[0] + pair[1])))
        found = [compare(one, other, checks) for one, other in zip(*pair)] if numeric and len(pair[0]) == len(pair[1]) else ["unknown"]
        return "unknown" if "unknown" in found else "different"
    a, b = _value(normalize(left)), _value(normalize(right))
    if not a or not b:
        return "unknown"
    if _number(a) is not None and _number(b) is not None:
        return "different"
    if all(re.fullmatch(r"\W*[A-Za-z]+\W*", _written_value(v)) for v in (left, right)):
        return "different"  # Two plain words, such as increases and decreases. A sentence is not a word: it stays unknown.
    if checks is not None:
        found = next((c for c in reversed(checks) if c.get("input") == {"left": _written_value(left), "right": _written_value(right)}), None) or symbolic.equivalent(_written_value(left), _written_value(right))
        if found["result"] == "fail":
            return "different"  # Both sides certainly parsed, and they are not the same expression.
    return "unknown"


def clean_key(value):
    """[{label, accept:[...]}]: every part must be answered; any accepted spelling of a part will do."""
    parts = []
    for part in items(value or [], "Answer key parts", 12):
        if not isinstance(part, dict):
            raise ValueError("Each answer key part needs a label and its accepted answers.")
        accept = [text(v, "Accepted answer", 500).strip() for v in items(part.get("accept", []), "Accepted answers", 20)]
        accept = [v for v in dict.fromkeys(accept) if v]
        if not accept:
            raise ValueError("Give at least one accepted answer for each part, or remove the part.")
        parts.append({"label": text(part.get("label", ""), "Part label", 40).strip(), "accept": accept})
    return parts


def rubric_roles(rubric):
    """Which approved lines are full credit, a correct answer without work, and nothing; and whether work is required."""
    positive = rubric.get("mode") == "positive"
    ceiling = rubric.get("ceiling")
    full = [i for i in rubric["items"] if positive and i["points"] == ceiling and i["points"] > 0]
    no_work = [i for i in rubric["items"] if NO_WORK.search(i["description"]) and i["points"] < (ceiling or 0)]
    zero = [i for i in rubric["items"] if i["points"] == 0]
    steps = [i for i in rubric["items"] if positive and 0 < i["points"] < (ceiling or 0) and i not in no_work]
    full_item = full[0] if len(full) == 1 else None
    return {"full": full_item["id"] if full_item else None, "no_work": no_work[0]["id"] if len(no_work) == 1 else None,
            "zero": zero[0]["id"] if len(zero) == 1 else None, "steps": [i["id"] for i in steps],
            # The coordinator pays for work when a line rewards the answer without it, or full credit asks for it.
            "work_required": bool(no_work) or bool(full_item and NEEDS_WORK.search(full_item["description"])),
            # The lettered parts its lines pay for ("Part A: ..."): a key that answers only some of them says nothing about the rest.
            "parts": sorted({m[1] for i in rubric["items"] for m in re.finditer(r"\bpart\s+([a-h])\b", i["description"].casefold())})}


# A full-credit line's lettered parts ("a. $$3 \\times 2$$, b. Not possible because ..."), found in its text outside the math only, and
# where its explanation begins: math there ("because the columns of $$G$$ ...", "where $$t$$ is any real number") is no answer.
_PART_MARK = re.compile(r"(?:^|(?<=[\s,;:]))\(?([a-h])[.)]\s")
_EXPLAINS = re.compile(r"\b(?:because|since|where)\b|\b(?:and|with)\s+work\b|\bto\s+justify\b", re.I)
_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)


def _answers_in(text, lettered):
    """The accepted answers of one stretch of a full-credit line, or None when it cannot be read: its math before any explanation, a
    part written as "Not possible" as those words, never an unfinished `M=` (the matrix follows in words). A lettered part takes
    alternatives joined by "or" only; the whole line also takes answers joined by "and" or "," as labelled parts (`x=6 and y=-4`)."""
    masked = _MATH.sub(lambda m: "\0" * len(m[0]), text)
    cut = _EXPLAINS.search(masked)
    text, masked = (text[:cut.start()], masked[:cut.start()]) if cut else (text, masked)
    found = list(_MATH.finditer(text))
    if any(m[1].strip().endswith("=") for m in found):
        return None
    if not found:
        return [{"label": "", "accept": ["Not possible"]}] if re.match(r"\W*not\s+possible\b", masked, re.I) else None
    between = [text[a.end():b.start()].casefold() for a, b in zip(found, found[1:])]
    answers = [m[1].strip() for m in found]
    if between and all(re.search(r"\band\b|,", gap) and not re.search(r"\bor\b", gap) for gap in between):
        if lettered:
            return None
        return [{"label": (re.match(r"\s*([A-Za-z][\w()]{0,5})\s*=", a) or ["", ""])[1], "accept": [a]} for a in answers]
    if lettered and not all(re.fullmatch(r"\W*or\W*", gap) for gap in between):
        return None
    return [{"label": "", "accept": answers}]


def key_from_rubric(rubric):
    """A proposal for the instructor to confirm, read from the full-credit line's own $$...$$ answers. Never used unconfirmed.

    Lettered parts ("a. ..., b. ...") become parts labelled by their letter, and a part written "Not possible because ..." is the
    words "Not possible". Every part must be readable, or nothing is proposed (requirement, 2026-09-25: Confirm all saves what is
    shown, so a misread part must not be shown as the key)."""
    role = rubric_roles(rubric)["full"]
    if not role:
        return []
    description = next(i["description"] for i in rubric["items"] if i["id"] == role)
    masked = _MATH.sub(lambda m: "\0" * len(m[0]), description)
    marks = list(_PART_MARK.finditer(masked))
    letters = [m[1].casefold() for m in marks]
    if len(marks) >= 2 and letters == [chr(ord("a") + n) for n in range(len(marks))]:
        parts = []
        for mark, end in zip(marks, [m.start() for m in marks[1:]] + [len(description)]):
            read = _answers_in(description[mark.end():end], True)
            if not read:
                return []
            parts.append({"label": mark[1].casefold(), "accept": read[0]["accept"]})
        return parts
    return _answers_in(description, False) or []


def final_answers(raw, lines, struck=(), unstruck=None):
    """Keep only answers quoted from a retained transcript line; the rest become a review reason. `unstruck`: the same lines without
    what the student crossed out (`models.quotable_lines(..., crossed_out=False)`), where a final answer must be found as well."""
    kept, reasons = [], []
    for entry in items(raw or [], "Final answers", 12):
        if not isinstance(entry, dict):
            raise ValueError("Invalid final answer.")
        quote, line_id = text(entry.get("text", ""), "Final answer", 1000).strip(), entry.get("line_id")
        if not quote:
            continue
        found = span(quote, lines, line_id)
        if found is None:
            reasons.append(UNQUOTED)
            continue
        if {line_id, *found[1]} & set(struck):
            # Crossed-out work is never treated as the final answer without evidence that the student meant it: neither the line the
            # grader named, the neighbouring line where the quotation was found, nor a line a sentence runs on through (`x =`, a
            # struck `7`, then `5` holds no `x = 7`).
            reasons.append(STRUCK_LINE)
            continue
        if unstruck is not None and (found := span(quote, unstruck, line_id)) is None:
            # Struck through inside a line that is not struck as a whole (`x = 7` crossed out beside `x = 5`, review 2026-09-22).
            reasons.append(STRUCK_WORK)
            continue
        kept.append({"part": text(entry.get("part", ""), "Answer part", 40).strip(), "text": quote, "line_id": found[0], **({"named_line_id": line_id} if found[0] != line_id else {})})
    return kept, reasons


def _decimals(value):
    """6130.10 is 6130.1 and 20.0 is 20, whichever reader wrote the zeros."""
    return re.sub(r"(\d)\.0+(?!\d)", r"\1", re.sub(r"(\.\d*?[1-9])0+(?!\d)", r"\1", value))


def _compared(written, normalized):
    """What an independent reading must also show: the whole value, or only the numbers of a sentence, a matrix layout or a value
    written with a word. Words and markup are each reader's own."""
    plain = re.sub(r"\\[a-zA-Z]+", " ", _mixed(written))
    words = re.findall(r"[A-Za-z]{3,}", plain)
    numbers = re.findall(r"\d+(?:\.\d+)?", symbolic.strip_thousands(plain))
    if re.search(r"\\begin|&|\\\\", written) or len(words) >= 3 or (words and numbers):
        return numbers
    value = _value(normalized)
    return [value] if value.strip("_") else []  # An empty blank is no answer.


def _shows(reading, token):
    """Whether another reading shows `token` whole: never a part of a longer number (`12` in `125` or `0.12`), a number with a sign
    the token does not have (`12` in `x=-12`), or a signed number the reading subtracts (`-4` in `3-4`)."""
    at = reading.find(token)
    while at >= 0:
        if not (token[:1] in "+-" and operand_before(reading, at)) and whole_numbers(reading, at, at + len(token)):
            return True
        at = reading.find(token, at + 1)
    return False


def facts(answers, transcript, key, candidates=(), problem="", symbolic_forms=True):
    """check: match | mismatch | unknown. work: none | thin | shown, counted from handwritten lines beside the answer."""
    normalized = [normalize(a["text"]) for a in answers]
    check, checks = "unknown", [] if symbolic_forms else None
    if key and answers:
        remaining = list(answers)
        check = "match"
        # An answer reported for part b never answers the key's part a (a key in words, "not possible", fits either). Only where
        # the grader's label IS another part's label: any other labelling is no evidence of anything and is matched as before.
        label = lambda value: re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
        labels = {label(part.get("label")) for part in key} - {""}
        for part in key:
            verdicts = {id(a): [compare(a["text"], accepted, checks) for accepted in part["accept"]] for a in remaining
                        if label(a.get("part")) == label(part.get("label")) or label(a.get("part")) not in labels}
            hit = next((a for a in remaining if "same" in verdicts.get(id(a), ())), None)
            if hit is not None:
                remaining.remove(hit)
                continue
            # A mismatch only when every remaining answer was understood and is another value; unknown notation never matches and never accuses.
            # A part nobody answered is a mismatch as before.
            check = "mismatch" if all(set(found) == {"different"} for found in verdicts.values()) else "unknown"
            break
    # An independent reader must show the same answer, or the match rests on one reading. Measured 2026-09-21 (32 such flags in one
    # batch): 9 were responses with NO final answer at all, where there is nothing two readers could differ on (None, which flags
    # nothing), and one reader's `(43/2)x` is the other's `\frac{43}{2}x`. A parenthesis that changes the value stays: `27/(2x)`
    # is not `(27/2)x`, and that difference was a real misreading the same day.
    #
    # What two readers can differ on is a VALUE (next batch, 36 flags: 15 only because a grader had reported a sentence, "Not
    # possible because ...", or the part (a) matrix as a final answer). No second reader spells a handwritten sentence or lays a
    # matrix out the same way, so those, and a value written with a word ("42/item x - 19000"), are compared by their NUMBERS:
    # every number must be in the other reading. With nothing to compare there is nothing two readers could differ on (None).
    # 6130.10 is 6130.1. The real differences stay: `9t+10` for `9t+18`, `+18829` for `+8829`.
    #
    # A value is found in the other reading only as a WHOLE (review, 2026-09-22): `x = 12` is not shown by `x=-12`, `x=125`, or by
    # `x = 17` beside `C = 120`. The other reading keeps apart the numbers its rows and spaces keep apart.
    others = [_decimals(normalize(c["transcript"]["plain_text"] + "\n" + c["transcript"].get("latex", ""))) for c in candidates]
    compared = [token for a, n in zip(answers, normalized) for token in _compared(a["text"], n)]
    # With no reading to compare against - one-shot, or Cloud Standard reading the paper itself - two readers cannot differ,
    # so this is None (nothing known), never False (a real difference). False would flag every response of such a batch.
    agree = None if not compared or not others else all(all(_shows(other, _decimals(token)) for token in compared) for other in others)
    printed = normalize(problem)
    answer_lines = {a["line_id"] for a in answers}
    work = 0
    for line in transcript["lines"]:
        content = normalize("".join((el.get("text") or el.get("latex") or "") for el in line["elements"]))
        if line["id"] in answer_lines or len(re.sub(r"\W", "", content)) < 2 or (printed and content in printed):
            continue
        work += 1
    return {"answers": [{**a, "normalized": n} for a, n in zip(answers, normalized)], "check": check, "readers_agree": agree,
            "work_lines": work, "work": "none" if work == 0 else "thin" if work < 3 else "shown",
            # How a match in another form was decided: retained for the audit, never a reason for credit.
            **({"symbolic": [c for c in checks if c["result"] != "fail"][:12]} if checks else {})}


def partial_key(key, roles):
    """Whether the key answers only some of the lettered parts the rubric pays for (a key for part b beside a rubric with parts a and b).
    Only where both speak in part letters; a key labelled x and y, or not at all, is taken as the whole answer as before."""
    labels = {str(part.get("label", "")).strip().casefold() for part in key or []}
    return bool(labels) and all(re.fullmatch(r"[a-h]", label) for label in labels) and bool(set(roles.get("parts", [])) - labels)


def reasons(found, roles, applied, has_key, key=()):
    """Contradictions between a proposal and these facts. Each one sends the response to the instructor."""
    result = []
    full, no_work = roles["full"] in applied, roles["no_work"] in applied
    if has_key and found["check"] == "mismatch" and (full or no_work):
        result.append(KEY_MISMATCH)
    if found["check"] == "match" and found["readers_agree"] is False:
        result.append(ONE_READING)
    if full and roles["work_required"] and found["work"] != "shown":
        result.append(LITTLE_WORK)
    # Measured 2026-09-21: 10 of 13 such flags were one question whose key answers part b only. The students had lost their points
    # in part a, which that key says nothing about: correct partial credit, accused by a rule that took the key for the whole answer.
    if found["check"] == "match" and found["work"] == "shown" and roles["full"] and not full and not partial_key(key, roles):
        result.append(NOT_FULL)
    if no_work and found["work"] == "shown":
        result.append(NO_WORK_LINE)
    if roles["full"] and not full and found.get("full_credit") and found["check"] != "match":
        # Seen on real papers: lines that exclude each other ("slope calculated with an error", "slope inverted") were ticked together
        # and added up to full marks for a wrong answer. A Gradescope export does not say which lines exclude each other.
        result.append(ADDS_UP)
    return result


def signature(question_id, found, applied):
    """Responses an instructor can judge together: same answer, same check, same amount of work, same proposed lines."""
    return digest({"question": question_id, "answers": sorted(a["normalized"] for a in found["answers"]), "check": found["check"],
                   "work": found["work"], "applied": sorted(applied)})[:16]
