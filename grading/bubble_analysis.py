"""Bubble sheet item analysis: how each keyed question went on the scored sheets, one version at a time.

The instructor asked on 2026-09-30: "For each question: how many got it right, whether the top scorers did better than the rest, and
which wrong choice was popular. This catches a miskeyed question before you certify." It is READ ONLY: derived on every read from
the sheets the Bubble sheets page scores (`BubbleKeysMixin._bubble_scores`: present scans that cleared Inbox review, each sheet's
current value, the latest key), it changes, decides and approves nothing. The instructor corrects a key in the key editor.

Versions are analysed apart: versions may scramble their questions, so question 7 of A is not question 7 of B. Per keyed question:
  difficulty      the mean share of its points earned (0 to 1); `full` (and `full_pct`) the sheets that earned all of them;
  discrimination  the corrected point-biserial: the correlation of what the question earned with the REST of the sheet's score
                  (its total without this question), so a question is not correlated with itself;
  upper, lower    the percent earning full points among the top and the bottom 27% of sheets by total (ceil(0.27 n) each, ties
                  ordered by sheet ID);
  choices         each letter marked alone, blanks (a question past what a sheet was read with is a blank) and several letters
                  (with the most common such mark), each with the average total percent of the sheets that chose it and how many
                  of the top and bottom groups did.
Per version: the mean total percent, its sample standard deviation (n - 1, as Certification's), and Cronbach's alpha over the
question shares earned/points (KR-20 for right/wrong questions), every keyed question worth points counted. Alpha, discrimination,
the groups and the flags need MINIMUM sheets; below it a version still counts choices and difficulty and says `too_few` (a flag
from a handful of sheets would call every question easy or hard).
Flags, in this order: `possible_miskey` (more of the top group chose one wrong letter alone than earned full points, or
discrimination below NEGATIVE), `hard` (difficulty below HARD), `easy` (above EASY) and `weak` (discrimination from 0 up to WEAK);
the last three only inform. Nothing here names a student: the result carries counts and shares.

The arithmetic is exact: scores are cents, so sums, variances and alpha are integers and fractions (a constant question has exactly
no variance), and only a square root is taken in Decimal. Figures are rounded half up: shares 3 decimals, percents 1.
"""
import math
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

from .bubble_keys import LETTERS, points_text

ANALYSIS = "item-analysis-v1"
MINIMUM = 10  # scored sheets of a version before alpha, discrimination, the groups and the flags mean anything
GROUP = Fraction(27, 100)  # the top and the bottom 27% of sheets by total
HARD, EASY = Fraction(30, 100), Fraction(95, 100)
WEAK, NEGATIVE = Decimal("0.15"), Decimal("-0.1")


def _cents(value):
    return int((Decimal(str(value)) * 100).to_integral_value())


def _spread(values):
    """n * sum(v^2) - (sum v)^2: n^2 times the population variance, exact for integers."""
    return len(values) * sum(v * v for v in values) - sum(values) ** 2


def _figure(value, places):
    """A Fraction or Decimal as a JSON number rounded half up (away from zero) to `places` decimals; None stays None."""
    if value is None:
        return None
    with localcontext() as context:
        context.prec = 40
        number = Decimal(value.numerator) / Decimal(value.denominator) if isinstance(value, Fraction) else Decimal(value)
        return float(number.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP))


def _correlation(xs, ys):
    """Pearson's r of two integer lists as a Decimal, or None when either does not vary."""
    sx, sy = _spread(xs), _spread(ys)
    if sx <= 0 or sy <= 0:
        return None
    covariance = len(xs) * sum(x * y for x, y in zip(xs, ys)) - sum(xs) * sum(ys)
    with localcontext() as context:
        context.prec = 40
        return Decimal(covariance) / (Decimal(sx) * Decimal(sy)).sqrt()


def _percent(share):
    """"22.3%" from a share 0..1 (a Fraction)."""
    text = format(Decimal(str(_figure(share * 100, 1))), "f")
    return (text[:-2] if text.endswith(".0") else text) + "%"


def _marked(value):
    """A sheet's mark as a choice: one letter, "blank", or "multiple"."""
    letters = "".join(sorted(set(value or "") & set(LETTERS)))
    return (letters or "blank", letters) if len(letters) < 2 else ("multiple", letters)


def analyse(key, sheets):
    """Per version of the key: the item analysis of its scored sheets (see the module notes). `sheets` as `_bubble_scores` gives
    them; only `status == "scored"` sheets count, each by its own version."""
    if not key:
        return []
    return [_version(version, key["defaults"], [s for s in sheets if s.get("status") == "scored" and s.get("version") == version["letter"]])
            for version in key["versions"]]


def _version(version, defaults, sheets):
    sheets = sorted(sheets, key=lambda sheet: (Decimal(str(sheet["earned"])), str(sheet["sheet_id"])))
    n = len(sheets)
    enough = n >= MINIMUM
    size = math.ceil(GROUP * n) if enough else None
    lower = set(range(size)) if enough else set()
    upper = set(range(n - size, n)) if enough else set()
    totals = [_cents(sheet["earned"]) for sheet in sheets]
    lines = [{line["number"]: line for line in sheet.get("questions") or []} for sheet in sheets]
    points = {q["number"]: _cents(q.get("points", defaults["points"])) for q in version["questions"]}
    possible = sum(points.values())
    groups = (upper, lower, size)
    questions = [_question(question, defaults, lines, totals, possible, groups, enough) for question in version["questions"]]

    # Cronbach's alpha over the shares earned/points. With L the least common multiple of the points (cents), L * sum(shares) is an
    # integer per sheet, and sum(var(share)) / var(sum of shares) = sum(spread(earned) * (L/p)^2) / spread(L * sum of shares).
    alpha = None
    items = [(points[q["number"]], [_cents(row[q["number"]]["earned"]) for row in lines]) for q in version["questions"] if points[q["number"]] > 0]
    if enough and len(items) >= 2:
        common = math.lcm(*(p for p, _ in items))
        scaled = [sum(earned[j] * (common // p) for p, earned in items) for j in range(n)]
        total_spread = _spread(scaled)
        if total_spread:
            ratio = Fraction(sum(_spread(earned) * (common // p) ** 2 for p, earned in items), total_spread)
            alpha = Fraction(len(items), len(items) - 1) * (1 - ratio)
    mean = Fraction(sum(totals), n * possible) * 100 if n and possible else None
    sd = None
    if n > 1 and possible:
        with localcontext() as context:
            context.prec = 40
            sd = (Decimal(_spread(totals)) / (n * (n - 1))).sqrt() * 100 / possible
    return {"letter": version["letter"], "sheets": n, "too_few": not enough, "group_size": size,
            "mean_pct": _figure(mean, 1), "sd_pct": _figure(sd, 1), "alpha": _figure(alpha, 3), "questions": questions}


def _question(question, defaults, lines, totals, possible, groups, enough):
    number, answer = question["number"], question["answer"]
    scoring = question.get("scoring", defaults["scoring"])
    points = _cents(question.get("points", defaults["points"]))
    upper, lower, size = groups
    rows = [row[number] for row in lines]
    earned = [_cents(line["earned"]) for line in rows]
    full = [cents >= points for cents in earned]
    n = len(rows)
    difficulty = Fraction(sum(earned), n * points) if n and points else None

    discrimination = None
    if enough and points and possible - points > 0:
        discrimination = _correlation(earned, [total - cents for total, cents in zip(totals, earned)])
    discrimination = None if discrimination is None else Decimal(str(_figure(discrimination, 3)))

    marks = [_marked(line["marked"]) for line in rows]
    choices = []
    for choice in (*LETTERS, "blank", "multiple"):
        chosen = [j for j, (kind, _) in enumerate(marks) if kind == choice]
        entry = {"choice": choice, "count": len(chosen), "key": choice in LETTERS and choice in answer,
                 "mean_pct": _figure(Fraction(sum(totals[j] for j in chosen), len(chosen) * possible) * 100, 1) if chosen and possible else None,
                 "upper": sum(1 for j in chosen if j in upper) if enough else None,
                 "lower": sum(1 for j in chosen if j in lower) if enough else None}
        if choice == "multiple":
            counts = {}
            for j in chosen:
                counts[marks[j][1]] = counts.get(marks[j][1], 0) + 1
            common = min(counts.items(), key=lambda pair: (-pair[1], pair[0])) if counts else ("", 0)
            entry.update(common=common[0], common_count=common[1])
        choices.append(entry)

    upper_full = sum(1 for j in upper if full[j])
    flags = []
    if enough:
        rivals = [c for c in choices if c["choice"] in LETTERS and c["choice"] not in answer and c["upper"] > upper_full]
        if rivals:
            rival = min(rivals, key=lambda c: (-c["upper"], c["choice"]))
            keyed = " or ".join(answer) if scoring == "either" and len(answer) > 1 else answer
            flags.append({"kind": "possible_miskey", "text": f"More of the top scorers chose {rival['choice']} than the key's {keyed}."})
        elif discrimination is not None and discrimination < NEGATIVE:
            flags.append({"kind": "possible_miskey", "text": f"The top scorers did worse on it than the rest (discrimination {discrimination})."})
    if enough and difficulty is not None and difficulty < HARD:
        flags.append({"kind": "hard", "text": f"Sheets earned {_percent(difficulty)} of its points on average."})
    if enough and difficulty is not None and difficulty > EASY:
        flags.append({"kind": "easy", "text": f"Sheets earned {_percent(difficulty)} of its points on average."})
    if discrimination is not None and 0 <= discrimination < WEAK:
        flags.append({"kind": "weak", "text": f"The top scorers did about as well on it as the rest (discrimination {discrimination})."})
    return {"number": number, "answer": answer, "scoring": scoring, "points": points_text(Decimal(points) / 100), "sheets": n,
            "read": sum(1 for line in rows if line.get("read")), "full": sum(full), "full_pct": _figure(Fraction(sum(full), n) * 100, 1) if n else None,
            "difficulty": _figure(difficulty, 3),
            "discrimination": None if discrimination is None else float(discrimination),
            "upper_pct": _figure(Fraction(upper_full, size) * 100, 1) if enough else None,
            "lower_pct": _figure(Fraction(sum(1 for j in lower if full[j]), size) * 100, 1) if enough else None,
            "choices": choices, "flags": flags}
