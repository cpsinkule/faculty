"""Bounded, provider-independent contracts. Model output is always untrusted."""
import functools
import itertools
import math
import re
import uuid
from decimal import Decimal, InvalidOperation

from canonical_json import digest_strict as digest, encoded_strict as encoded  # noqa: F401 - what the grading modules import from here


def uid():
    return str(uuid.uuid4())


def finite_decimal(value):
    """Decimal of a score or None; floats go through their shortest text so 0.1 + 0.2 is 0.3."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


# A quotation must be the student's characters, word for word. What the READER added to lay a block out is not the student's:
# math delimiters, alignment marks, array and text wrappers, grouping braces and spacing commands. A mark is one character however
# it is spelled: an operator (× for `\times`) and, since 2026-09-21, a fraction bar (`\frac{-8z}{-8}` quoted as `-8z/-8`).
_LAYOUT = re.compile(r"\\begin\{[a-z*]+\}(?:\{[^}]*\})?|\\end\{[a-z*]+\}|\\(?:left|right|displaystyle|text|mathrm|mathbf|operatorname|hline)(?![a-zA-Z])|\\[,;!:\[\]()]|\$|(?<!\\)[{}]")
# A cell of an array or an alignment, and the reader's wide spaces, set two things APART: `3 & 24` is two numbers, never 324.
_SPACING = re.compile(r"\\(?:quad|qquad)(?![a-zA-Z])|(?<!\\)\\ |&")
_SYMBOLS = {"×": "\\times", "⋅": "\\cdot", "·": "\\cdot", "≠": "\\neq", "≤": "\\leq", "≥": "\\geq", "−": "-", "÷": "\\div", "±": "\\pm", "→": "\\rightarrow",
            "⇒": "\\Rightarrow", "≈": "\\approx", "∞": "\\infty", "->": "\\rightarrow", "=>": "\\Rightarrow"}  # An arrow typed on a keyboard is the arrow.
RUN_ON = 4  # A handwritten sentence wraps: a quotation may continue through at most this many FOLLOWING retained lines.
_LABEL = re.compile(r"[A-Za-z]{2,25}:")  # "Function:" or "Dimensions:" printed before the blank and retained as a line of its own (spaces removed).
# A space is kept only between two word characters (letters, digits, `.` and `,`), where it may part two numbers; anywhere else it
# is layout. `\ue000` stands for a whole number set apart from the fraction after it (`23\frac{1}{8}` is 23 and 1/8, never 231/8).
_WORD = re.compile(r"[^\W_]|[.,]")
_GAP = re.compile(r"(?<![^\W_]|[.,]) | (?![^\W_]|[.,])")
_APART = "\ue000"


def _group_end(value, start):
    depth = 0
    for k in range(start, len(value)):
        if value[k] in "{}" and (k == 0 or value[k - 1] != "\\"):
            depth += 1 if value[k] == "{" else -1
            if depth == 0:
                return k + 1
    return None


def _fractions(value):
    """`\\frac{a}{b}` is the bar the student drew: `a/b`, whoever spells it. Bounded; a fraction that does not parse stays as written.
    A whole number written just before it stays apart from its numerator (the mixed number `23\\frac{1}{8}`)."""
    for _ in range(40):
        found = re.search(r"\\[dt]?frac\s*(?=\{)", value)
        top = _group_end(value, found.end()) if found else None
        gap = len(value[top:]) - len(value[top:].lstrip()) if top else 0
        bottom = _group_end(value, top + gap) if top and value[top + gap:top + gap + 1] == "{" else None
        if not bottom:
            break
        mixed = re.search(r"\d\s*$", value[:found.start()]) and re.fullmatch(r"\s*\d+\s*", value[found.end() + 1:top - 1] + value[top + gap + 1:bottom - 1])
        apart = _APART if mixed else ""
        value = value[:found.start()] + apart + "(" + value[found.end() + 1:top - 1] + ")/(" + value[top + gap + 1:bottom - 1] + ")" + value[bottom:]
    return value


def operand_before(value, at):
    """Whether what is written just before `value[at]` is something a parenthesis or a sign would be multiplied with or subtracted
    from: a digit, a letter, a closing bracket. The end of a command (`\\rightarrow`, `\\leq`) is no operand."""
    previous = value[at - 1:at] if at > 0 else ""
    if previous.isdigit() or previous in (")", "]"):
        return True
    return previous.isalpha() and not re.search(r"\\[a-zA-Z]+$", value[max(0, at - 40):at])


def whole_numbers(value, start, end):
    """Whether `value[start:end]` cuts no number out of a longer one. A number at its start is not the tail of another (`12` in
    `-12`, `125`, `0.12`, `1,012`) and one at its end is not the head of another (`12` in `125`, `12.5`, `12,500`). Where a
    subtraction stands before it (`5-12`), the number is whole."""
    head, tail = value[start:start + 1], value[end - 1:end]
    if head.isdigit() or head == ".":
        previous = value[start - 1:start] if start else ""
        if (previous.isdigit() or (previous == "." and (value[start - 2:start - 1].isdigit() or not operand_before(value, start - 1)))
                or (previous in ("-", "+") and not operand_before(value, start - 1))
                or (previous == "," and value[start - 2:start - 1].isdigit() and re.match(r"\d{3}(?!\d)", value[start:]))):
            return False
    if (tail.isdigit() or tail == ".") and re.match(r"\d|\.\d|,\d{3}(?!\d)", value[end:]):
        return False
    return True


def unbracketed(value, start, end, denominator=False):
    """Whether the parentheses `value[start]` and `value[end - 1]` can be left out without changing what is written. They stay where
    a number would join the one before it (`2(3)` is not 23), a sign would become a subtraction (`x(-3)` is not x-3), a power
    touches them (`(-2)^2` is not -2^2, `x^(2a)` is not x^2a), a number follows (`(1/2)3` is not 1/23) or, outside a denominator,
    a fraction bar comes before them (`4/(2x)` is not 4/2x). `denominator`: the parentheses stand right after a bar on purpose."""
    inner, previous, following = value[start + 1:end - 1], value[start - 1:start] if start else "", value[end:end + 1]
    if re.match(r"[\d.^]", following) or previous == "^" or (previous == "/" and not denominator):
        return False
    if inner[:1] in ("-", "+"):
        return not operand_before(value, start)
    return not ((previous.isdigit() or previous == ".") and re.match(r"[\d.]", inner))


def _bare(denominator=False):
    return lambda match: match[1] if unbracketed(match.string, match.start(), match.end(), denominator) else match[0]


def _unwrap(value):
    """One spelling of the bar: a plain numerator and a one-number or one-letter denominator need no parentheses. A PRODUCT in a
    denominator keeps them, as in `answers.normalize`: 27/(2x) is not (27/2)x, and a grader may not quote the one for the other.
    Nor are they left out where that changes the value (`unbracketed`, review 2026-09-22): `2(3)/4` is not 23/4 and `x(-3)/2` is
    not x-3/2. A number may carry its thousands comma: `\\frac{13,635}{1,010}` is quoted as 13,635/1,010."""
    value = re.sub(r"\((-?[\w.]+(?: [\w.]+)*|-?\d[\d.,]*)\)(?=/)", _bare(), value)
    value = re.sub(r"(?<=/)\((-?(?:\d[\d.,]*|[a-zA-Z]))\)", _bare(True), value)
    return re.sub(r"\((-?[\w.]+/-?(?:\d[\d.,]*|[a-zA-Z]))\)", _bare(), value)  # (27/2)x is the fraction 27/2 times x, written on one line.


def _characters(value):
    value = value.replace(_APART, "")
    for symbol, spelled in _SYMBOLS.items():
        value = value.replace(symbol, spelled)
    value = re.sub(r"\\(ne|le|ge)(?![a-zA-Z])", r"\\\1q", value.replace("\\$", "$"))
    value = re.sub(r"\\to(?![a-zA-Z])", r"\\rightarrow", _fractions(value))
    value = _GAP.sub("", re.sub(r"\s+", " ", _SPACING.sub(" ", _LAYOUT.sub("", value))).strip())
    return _GAP.sub("", _unwrap(value).replace(_APART, " "))


@functools.lru_cache(maxsize=1024)
def _text(line):
    """A retained line (one of its two spellings) as the quotation check reads it: its rows and elements in order, one per line.
    A quotation may run on from one to the next like a sentence, but never glues them: `x = 1` above `2y = 3` holds no `x = 12`."""
    return "\n".join(filter(None, (_characters(part) for part in re.split(r"\n|\\\\", line))))


def _rows(quotation, joined=False):
    """The rows of a quotation. `joined`: also where a grader joined rows its own way, with `;`, an elision (`...`), a typed arrow
    (`->`) or a line break spelled `\\n` before a letter (`\\nM=`, which a row break and `\\neq` share)."""
    breaks = r"\n|\\n|\\\\|(?<!\\);|\.{3}|…|->" if joined else r"\n|\\n(?![a-zA-Z])|\\\\"
    return [row for row in (_characters(part) for part in re.split(breaks, quotation)) if row]


@functools.lru_cache(maxsize=1024)
def _pattern(row):
    """Where a row of a quotation may be written: its characters in order. Between two digits a space in one must be a space (or a
    line break) in the other, since it parts two numbers; elsewhere the reader's spaces are layout. A quotation passes from one row or
    element of the line to the next only where it has a space itself, or where no word is cut in two (`x =` above `5`)."""
    out, previous, spaced = [], None, False
    for character in row:
        if character == " ":
            spaced = True
            continue
        if previous is not None:
            numbers = previous.isdigit() and character.isdigit()
            words = bool(_WORD.fullmatch(previous) and _WORD.fullmatch(character))
            out.append("[ \n]" if numbers and spaced else "" if numbers else "[ \n]?" if spaced else " ?" if words else "\n?")
        out.append(re.escape(character))
        previous, spaced = character, False
    return re.compile("(?=(" + "".join(out) + "))")


def _found(row, text):
    """Every (start, end) where `row` is written in `text`, a number never cut out of a longer one."""
    return [match.span(1) for match in _pattern(row).finditer(text) if whole_numbers(text, *match.span(1))]


def quotes(quotation, line):
    """Whether `quotation` is found word for word in the retained `line`.

    Measured on 2026-09-21: 72 quotations of one batch failed, and 23 of them were the student's exact characters. A reader
    retains a whole block of work as ONE line (a two-column layout, a LaTeX array), and a grader quotes some of its rows or one
    column; a reader writes `\\times` where a grader quotes ×. So every ROW of the quotation must be in the line, in the
    student's characters. Nothing else is forgiven: a description, a corrected value or text from another line still fails.

    A later batch (15 of 82 refused): the rows were the student's, joined the GRADER's way: `R(x)=64x; P(x)=R(x)-C(x)`, the
    matrix `[3 24; 45 9]`, `20(-2)+27y=95 ... -60=30x`, `x-8z=12 -> x=8t+12`. Read as written first; only then with those joins
    as row breaks. Every row must still be in the line, so a comma-composed pair (`46,206, 2790`) or a letter for an operator
    (`2 x 2`) fails as before.

    A row is found as WRITTEN, never pieced together (review, 2026-09-22): a number is never cut out of a longer one (`12` from
    `x = -12`, `x = 0.5` from `x = 0.55`), two rows or elements are never glued into one (`x = 25` from `x = 2` above `5x = 10`),
    two cells of a matrix never make one number (`[32 4; ...]` from `[3 & 24 ...]`), and the line's text and its LaTeX spelling
    are never mixed.
    """
    halves = [_text(half) for half in line.split("\x1f")]
    return any(rows and all(_found(row, text) for row in rows) for text in halves for rows in (_rows(quotation), _rows(quotation, True)))


def _runs(quotation, lines, ids, start):
    """The lines a quotation takes when it starts in `ids[start]` and RUNS ON: the named line from some point to its end, then
    every following line whole and in order, the last one as far as the quotation goes. Yields each way it does, as a list."""
    rows = _rows(quotation)
    if not rows:
        return
    sentence = _GAP.sub("", " ".join(rows))  # Where the grader broke its quotation, the sentence goes on.
    window = ids[start:start + 1 + RUN_ON]
    for half in (0, 1):  # A line may carry its text and its LaTeX (`\x1f` between): never a mixture.
        texts = [_text((lines[i].split("\x1f") + [""])[half]) for i in window]
        later = [k for k in range(1, len(texts)) if texts[k]]
        if not texts[0]:
            continue
        # A line of side work written between two lines of the sentence is passed over; a quotation that fits none of them fails.
        for last in later:
            earlier = [k for k in later if k < last]
            for size in range(len(earlier), -1, -1):
                for chosen in itertools.combinations(earlier, size):
                    joined, cuts = texts[0], []
                    for k in (*chosen, last):
                        cuts.append(len(joined))
                        joined += "\n" + texts[k]
                    if any(begin < cuts[0] and end > cuts[-1] + 1 for begin, end in _found(sentence, joined)):
                        yield [window[0], *(window[k] for k in chosen), window[last]]


def _run(quotation, lines, ids, start):
    """The lines of the first way a quotation runs on from `ids[start]`, or None when it does not."""
    return next(_runs(quotation, lines, ids, start), None)


def span(quotation, lines, line_id):
    """(the retained line a quotation is FOUND in, every line it takes), or None. `lines` is id -> text in transcript order.
    See `located`; the lines it takes are what `answers.final_answers` checks for work the student crossed out."""
    if not isinstance(line_id, str) or line_id not in lines or not _rows(quotation):
        return None
    ids = list(lines)
    named = ids.index(line_id)

    def here(at, quotation=quotation):
        return [ids[at]] if quotes(quotation, lines[ids[at]]) else _run(quotation, lines, ids, at)
    if taken := here(named):
        return line_id, taken
    for at in range(named - 1, max(named - RUN_ON, 0) - 1, -1):
        if taken := next((way for way in _runs(quotation, lines, ids, at) if line_id in way), None):
            return ids[at], taken
    label = _LABEL.fullmatch(_characters(lines[line_id].split("\x1f")[0]).replace(" ", ""))
    if named + 1 < len(ids):
        unlabelled = re.sub(r"^[\s$]*(?:\\\(\s*)?" + r"\s*".join(map(re.escape, label[0])) + r"\s*", "", quotation, flags=re.I) if label else quotation
        if taken := here(named + 1) or (unlabelled != quotation and here(named + 1, unlabelled)):
            return ids[named + 1], taken
    if named and not label and (taken := here(named - 1)):
        return ids[named - 1], taken
    return None


def located(quotation, lines, line_id):
    """The retained line a quotation is FOUND in, or None. `lines` is id -> text in transcript order.

    In the named line (`quotes`), or a quotation that RUNS ON from it. Measured on one batch: 36 of 38 refused final answers
    were sentences ("Not possible because ...") that the student wrote over two or three handwritten lines and every grader
    quoted as the one sentence it is. The quotation must START in the line, take it to its end and continue, character for
    character, through the `RUN_ON` following lines (a line of side work between two lines of the sentence is passed over).

    A grader may also name a NEIGHBOURING line (requirement, 2026-09-21; 17 of 82 refused quotations of the next batch were the
    student's exact characters one to three lines from the line named). Three shapes, and no others:
    - the run-on read backward: the quotation starts up to `RUN_ON` lines earlier and TAKES the named line (the last line of a
      wrapped sentence; the value in `decreases | by | 23 1/8 | units`);
    - a printed label: the named line is `Function:` and the writing is in the line that follows; the label may open the quotation;
    - the line directly after or before the one named.
    The quotation is kept under the line where it was found. Text anywhere else in the transcript, a paraphrase, a corrected value
    and writing the reader never retained still fail.
    """
    found = span(quotation, lines, line_id)
    return found[0] if found else None


def quoted(quotation, lines, line_id):
    """Whether a quotation is the student's characters at the line a grader named: see `located`."""
    return located(quotation, lines, line_id) is not None


def text(value, label="Text", limit=20000, blank=True):
    if not isinstance(value, str) or len(value) > limit or (not blank and not value.strip()):
        raise ValueError(f"{label} must be {'at most' if blank else '1–'} {limit} characters.")
    return value


def number(value, label="Number", minimum=0, maximum=100000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be a finite number from {minimum} to {maximum}.")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("Invalid identifier.")
    return value


def exam_letter(value, blank=True):
    """An exam version behind one label sheet: a single letter A to Z; '' is an exam without versions."""
    value = "" if value is None else value
    if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Z]", value)) or (not value and not blank):
        raise ValueError("Exam versions are single letters A to Z.")
    return value


def items(value, label, maximum=200):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list with at most {maximum} entries.")
    return value


def box(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("Evidence geometry must be [left, top, right, bottom] in crop-relative coordinates.")
    value = [number(v, "Coordinate", 0, 1) for v in value]
    if value[2] <= value[0] or value[3] <= value[1]:
        raise ValueError("Evidence rectangle must have positive area.")
    return value


def model_geometry(evidence):
    """Optional model geometry, with an audit marker that survives revalidation.

    Bad coordinates locate nothing: discard them without guessing a scale or changing the
    literal text. This applies only to model evidence; required geometry still uses `box`.
    The original provider reply remains in its step receipt.
    """
    discarded = evidence.get("bbox_discarded") is True
    try:
        rectangle = box(evidence.get("bbox"))
    except (ValueError, OverflowError):
        rectangle, discarded = None, True
    return {"bbox": rectangle, **({"bbox_discarded": True} if discarded else {})}


STATES = ("written", "crossed_out", "inserted", "unclear")
LAYOUT_KINDS = ("continuation", "side_calculation", "insertion", "replacement", "separate_work", "reading_order")
# Said of a transcript with nothing in it. Named (2026-10-11) so the review card's blank-answer cause holds it beside the reader's
# own blank-crop reason (`review_queue.reason_causes`); the words are unchanged.
NOTHING_TRANSCRIBED = "No visible work was transcribed; confirm a blank or unreadable response."


def struck_lines(value):
    """Lines the student crossed out entirely: never part of the solution, never the final answer."""
    return {line["id"] for line in value["lines"] if line["elements"] and all(el.get("state") == "crossed_out" for el in line["elements"])}


def quotable_lines(value, crossed_out=True):
    """What a quotation may be found in, id -> text in transcript order: each line as text and the same line in its LaTeX spelling
    (`\x1f` between; `quotes` never mixes them), its elements one per line. Seen on real papers: graders quote "x decreases by 168/11
    units." from a line whose elements are separate, or quote the LaTeX of a line retained as text. Without `crossed_out`, what the
    student struck through is left out: a final answer is never quoted from it (review, 2026-09-22)."""
    kept = lambda line: [el for el in line["elements"] if crossed_out or el.get("state") != "crossed_out"]
    return {line["id"]: "\n".join((el.get("text") or el.get("latex") or "") for el in kept(line)) + "\x1f" + "\n".join((el.get("latex") or el.get("text") or "") for el in kept(line))
            for line in value["lines"]}


def line_texts(value, skip=()):
    return [{"id": line["id"], "text": " ".join((el.get("text") or el.get("latex") or "") for el in line["elements"] if el.get("state") != "crossed_out").strip()}
            for line in value["lines"] if line["id"] not in skip]


def transcript(value):
    if not isinstance(value, dict):
        raise ValueError("Recognition must return a transcript object.")
    lines, seen = [], set()
    for line in items(value.get("lines", []), "Transcript lines", 500):
        if not isinstance(line, dict):
            raise ValueError("Invalid transcript line.")
        lid = identifier(line.get("id"))
        if lid in seen:
            raise ValueError("Transcript line IDs must be unique.")
        seen.add(lid)
        elements = []
        for el in items(line.get("elements", []), "Line elements", 200):
            if not isinstance(el, dict) or el.get("type") not in ("text", "math"):
                raise ValueError("Transcript elements must be text or math.")
            if type(el.get("crop", 0)) is not int:
                raise ValueError("Crop index must be an integer.")
            if el.get("state", "written") not in STATES:
                raise ValueError("A transcript element is written, crossed_out, inserted or unclear.")
            elements.append({"type": el["type"], "text": text(el.get("text", "")),
                "latex": text(el.get("latex", "")), **model_geometry(el),
                "confidence": number(el["confidence"], "Confidence", 0, 1) if el.get("confidence") is not None else None,
                "crop": int(number(el.get("crop", 0), "Crop index", 0, 99)),
                # Literal reconstruction keeps what was struck through, inserted or unreadable. Ordinary work carries no mark,
                # so readings retained before these marks existed are unchanged.
                **({"state": el["state"]} if el.get("state", "written") != "written" else {})})
        lines.append({"id": lid, "elements": elements})
    result = {"lines": lines, "plain_text": text(value.get("plain_text", "")), "latex": text(value.get("latex", "")),
        "confidence": number(value["confidence"], "Confidence", 0, 1) if value.get("confidence") is not None else None,
        "ambiguities": [text(v, "Ambiguity", 2000) for v in items(value.get("ambiguities", []), "Ambiguities")]}
    # Optional on historical readings. These describe the paper, never a repaired
    # solution; every reference must name literal work retained above.
    if "layout_relations" in value:
        relations, dropped = [], value.get("layout_notes_dropped", 0)
        if type(dropped) is not int or not 0 <= dropped <= 200:
            raise ValueError("Invalid count of dropped layout notes.")
        for link in items(value["layout_relations"], "Layout relationships", 200):
            if not isinstance(link, dict) or link.get("kind") not in LAYOUT_KINDS or type(link.get("uncertain")) is not bool:
                raise ValueError("Invalid layout relationship.")
            if (not isinstance(link.get("from_line"), str) or not isinstance(link.get("to_line"), str)
                    or link["from_line"] not in seen or link["to_line"] not in seen or link["from_line"] == link["to_line"]):
                # One note about the layout is no reason to refuse every line that was read: on real papers one reading in eight
                # was thrown away, asked again and billed again for a note such as "line-10 feeds line-3" beside nine good lines.
                # The note is dropped and counted. It is NOT a doubt: measured on 174 real readings (2026-09-20), a quarter carried
                # such a note, three quarters of them named a line past the end or linked a line to itself (the model misnumbering,
                # not skipping work), and those readings were as long as earlier readings of the same responses (median ratio 1.00).
                # Raised as a doubt it flagged a quarter of all responses and sent them to the additional reviewer for nothing.
                dropped += 1
                continue
            relations.append({key: link[key] for key in ("from_line", "to_line", "kind", "uncertain")})
            relations[-1]["description"] = text(link.get("description"), "Layout evidence", 2000, False)
        result["layout_relations"] = relations
        if dropped:
            result["layout_notes_dropped"] = dropped  # For the audit only; `reading_ambiguities` never reads it.
    if "reading_alternatives" in value:
        alternatives = []
        for alternative in items(value["reading_alternatives"], "Reading alternatives", 100):
            if not isinstance(alternative, dict) or not isinstance(alternative.get("line_id"), str) or alternative["line_id"] not in seen:
                raise ValueError("A reading alternative must name a retained line.")
            options = [text(v, "Possible reading", 2000, False) for v in items(alternative.get("options"), "Possible readings", 6)]
            if len(set(options)) < 2:
                raise ValueError("An uncertain reading must retain at least two different alternatives.")
            alternatives.append({"line_id": alternative["line_id"], "options": options,
                                 "description": text(alternative.get("description"), "Reading evidence", 2000, False)})
        result["reading_alternatives"] = alternatives
    # Once: a saved blank transcript comes back through here on review, and a second note made it look edited (2026-11-02: 827 of the 834
    # reviews marked `transcript_corrected` were blank answers nobody had changed).
    if not result["plain_text"] and not result["latex"] and not lines and NOTHING_TRANSCRIBED not in result["ambiguities"]:
        result["ambiguities"].append(NOTHING_TRANSCRIBED)
    if len(encoded(result)) > 500000:
        raise ValueError("Transcript exceeds the retained output limit.")
    return result


def reading_ambiguities(value):
    """Include structured doubt even when a model leaves its free-text list empty.

    Not a doubt about work the student crossed out entirely (`struck_lines`): struck work is never the solution and never the final
    answer, so how it reads decides nothing, and crossed-out work alone never sends a response to review. Measured 2026-09-22 on the
    trial: 7 of 62 reading alternatives in three batches were readings of wholly struck lines, each one a "handwriting is ambiguous"
    flag. The alternatives stay in the transcript. A doubt whether a mark IS struck is the reader's free-text ambiguity and stays."""
    struck = struck_lines(value) if value.get("lines") else set()
    return list(dict.fromkeys([*value.get("ambiguities", []),
        *[f"Uncertain layout from {r['from_line']} to {r['to_line']}: {r['description']}" for r in value.get("layout_relations", [])
          if r["uncertain"] and not {r["from_line"], r["to_line"]} <= struck],
        *[f"Uncertain reading of {r['line_id']}: {' or '.join(r['options'])}. {r['description']}" for r in value.get("reading_alternatives", [])
          if r["line_id"] not in struck]]))


def plain_transcript(content, *, confidence=None, ambiguities=None):
    return transcript({"plain_text": content, "latex": content, "confidence": confidence,
        "ambiguities": ambiguities or [], "lines": [{"id": f"line-{i+1}",
        "elements": [{"type": "math", "text": line, "latex": line}]} for i, line in enumerate(content.splitlines())]})
