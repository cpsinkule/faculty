"""Exact term grades: every student's category and overall percentages, letters, and column statistics (README "Computation").

Pure: it reads a normalized setup (`setup.normalize`), the term's students and each cell's LATEST revision, and returns the
README's `Results`. Nothing here is stored; the service computes on every view and after every write, so a changed weight,
drop or scale reaches every student at once and no stale total can exist anywhere.

Arithmetic is exact. Points are whole ten-thousandths (`common.PLACES`, the finest a score or a column may carry), so every
sum is an integer sum and ratios are compared by cross-multiplication; a percentage becomes a Fraction only at the end and is
rounded half up exactly once, for display. Never floats: 89.995 must give the same text and the same letter in every view
and every export.

Choices the README leaves open, kept here so every view agrees:
- A category with no counted entry at all reports percent, earned and possible as None. One whose entries are all extra
  credit has earned points but no possible points: percent None, earned its extra points, possible "0". In points mode those
  extra points still add to the student's total (a "Bonus" category of extra-credit columns); in weighted mode a category
  without a result is left out, extra points included.
- A student's `earned`/`possible` are the points-mode sums after drops (Uncategorized included) in both modes, None when
  nothing of theirs counts yet.
- Drops in a `points` category maximize the category's own result, extra-credit points included in the numerator.
- A replacement column (`replaces_lowest`) never counts on its own: a SCORED replacement whose percentage is higher than the
  student's lowest regular percentage in its category (ties: the later column) gives that column its percentage, before drops.
  Missing, empty or excused, it does nothing, whatever `ungraded` says: taking it is optional.
- The last scale entry is the floor: a percentage below every minimum still gets the last letter, so a student with a
  percentage always has a letter.
- In weighted mode a counted, not archived column without a known category counts toward nothing (Uncategorized has no
  weight): `uncounted` lists those column ids in the setup's order, only when there is one, so other results keep their shape.
- `ungraded: "due"` (GRADEBOOK "ZERO ONCE DUE", `gradebook/blanks.py`): an empty cell of a column in `blanks`' due set counts 0
  unless `blanks` says it waits (a makeup owed, a DR booking, a paper not graded yet). Without `blanks` nothing is due. `blanks`
  in the results (only under "due", so the other policies keep their shape) names each such cell: {row: {item: "zero" or the
  reason it waits}}; `due_posted` lists the columns due only because their grades are posted in Canvas, only when there is one.
"""
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from math import lcm

from .common import percent_text, points_text

UNIT = 10000  # Ten-thousandths of a point: `parse_points` writes nothing finer.
EMPTY = ((), (), ())


@lru_cache(maxsize=1 << 16)
def units(value):
    """Points (canonical text, or a number) as whole ten-thousandths. Scores repeat a lot, so the parse is cached."""
    scaled = Decimal(str(value)) * UNIT
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError(f"{value} is not a number of points in ten-thousandths.")
    return int(scaled)


def rounded(value, places):
    """A Fraction rounded half up (away from zero) to `places` decimals, as an exact Decimal: no float or 28-digit context."""
    numerator, denominator = abs(value.numerator) * 10 ** places, value.denominator
    whole = (2 * numerator + denominator) // (2 * denominator)
    return Decimal(-whole if value < 0 else whole).scaleb(-places)


def _percent(value):
    return None if value is None else percent_text(rounded(value, 2))


def _shown(value, rounding):
    """A student's overall percentage, shown beside their letter. When the exact percentage decides the letter (`rounding`
    None) it is cut, never rounded, to two decimals: 89.995 shows 89.99 beside its B, not 90.00. With a rounded letter, and
    everywhere else (categories, class means), percentages are rounded half up."""
    if value is None or rounding is not None:
        return _percent(value)
    return percent_text(Decimal(value.numerator * 100 // value.denominator).scaleb(-2))


def _points(value):
    """Points as text from whole ten-thousandths (an int) or a Fraction of them (a mean or a median)."""
    if value is None:
        return None
    return points_text(rounded(Fraction(value, UNIT), 4)) if isinstance(value, Fraction) else points_text(Decimal(value).scaleb(-4))


def letter_for(ladder, percent, rounding):
    """The first letter (highest minimum first) whose minimum the percentage reaches, the percentage first rounded half up
    to `rounding` decimals when the setup asks; below every minimum, the last letter (the floor)."""
    if percent is None:
        return None
    value = percent if rounding is None else Fraction(rounded(percent, rounding))
    for letter, minimum in ladder:
        if value >= minimum:
            return letter
    return ladder[-1][0]


def compute(setup, students, cells, blanks=None):
    """`students`: [{"row", "section_id"}]; `cells`: {item_id: {row: latest revision}} (status scored, excused, missing or
    cleared; a cleared cell is an empty one); `blanks`: `blanks.read`'s (due column IDs, {(item, row): reason it waits}[, the due
    columns due only because their grades are posted]), read only under `ungraded: "due"`. Returns the README's `Results`."""
    zero_blank = setup["ungraded"] == "zero"
    once_due = setup["ungraded"] == "due"
    due, waits, *by_posting = blanks if once_due and blanks else (frozenset(), {})
    shown = {}  # under "due": {row: {item: "zero" or the reason the empty cell waits}}
    categories = setup["categories"]
    known = {category["id"] for category in categories}
    items = setup["items"]
    groups_of = [item["category_id"] if item["category_id"] in known else None for item in items]
    # Per category: its drops and aggregation, its weight, and (for equal weight) a common denominator of its columns' points,
    # so that every column percentage is an integer over it and drops are ranked without a Fraction per cell.
    policies, commons = {None: (0, "points", 0)}, {None: 1}
    for category in categories:
        worth = [units(item["points"]) for item, group in zip(items, groups_of) if group == category["id"] and units(item["points"])]
        policies[category["id"]] = (category["drop_lowest"], category["aggregate"], units(category["weight"]))
        commons[category["id"]] = lcm(*worth) if worth else 1
    columns, stats = [], {}
    for index, (item, group) in enumerate(zip(items, groups_of)):
        possible = units(item["points"])
        # The column's share of its category's common denominator: its percentage is earned * share / common * 100.
        share = commons[group] // possible if possible else 0
        columns.append((index, item["id"], possible, share, frozenset(item["sections"]), item["extra_credit"],
                        item["counts"] and not item["archived"], group, cells.get(item["id"]) or {}, bool(item.get("replaces_lowest"))))
        stats[item["id"]] = [0, 0, 0, 0, []]  # scored, excused, missing, empty, scored points
    order = [category["id"] for category in categories] + [None]
    weighted = setup["mode"] == "weighted"
    ladder = [(entry["letter"], Fraction(Decimal(entry["min"]))) for entry in setup["scale"]]
    letters = {entry["letter"]: 0 for entry in setup["scale"]}
    category_percents = {category["id"]: [] for category in categories}
    overall, results = [], {}

    for student in students:
        row, section = student["row"], student["section_id"]
        groups = {}
        for index, item_id, possible, share, only, extra, counted, group, column_cells, replacing in columns:
            if only and section not in only:
                continue
            cell = column_cells.get(row)
            status = cell["status"] if cell else "cleared"
            tally = stats[item_id]
            if status == "scored":
                earned = units(cell["points"])
                tally[0] += 1
                tally[4].append(earned)
            elif status == "excused":
                tally[1] += 1
                continue
            elif status == "missing":
                tally[2] += 1
                earned = None if extra else 0  # A missing extra-credit column adds nothing.
            elif status == "cleared":
                tally[3] += 1
                if item_id in due:
                    reason = waits.get((item_id, row))
                    shown.setdefault(row, {})[item_id] = reason or "zero"
                    earned = None if reason else 0
                else:
                    earned = 0 if zero_blank and not extra else None
            else:
                raise ValueError(f"A score has an unknown status: {status}.")
            if replacing:
                if counted and status == "scored":
                    groups.setdefault(group, ([], [], []))[2].append((index, earned, possible, share))
            elif counted and earned is not None:
                groups.setdefault(group, ([], [], []))[1 if extra else 0].append((index, earned, possible, share))

        total_earned = total_possible = weight_total = 0
        weighted_sum, counted_any, per_category = Fraction(0), False, {}
        for key in order:
            regular, extras, replacements = groups.get(key, EMPTY)
            regular, replaced = _replace(regular, replacements)
            if not regular and not extras:
                if key is not None:
                    per_category[key] = {"percent": None, "earned": None, "possible": None, "dropped": []}
                continue
            drop, aggregate, weight = policies[key]
            percent, earned, possible, dropped = _group(regular, extras, drop, aggregate, commons[key])
            counted_any = True
            total_earned += earned
            total_possible += possible
            if key is None:
                continue  # Uncategorized counts in points mode only and has no row of its own.
            per_category[key] = {"percent": _percent(percent), "earned": _points(earned), "possible": _points(possible),
                                 "dropped": [columns[index][1] for index in dropped]}
            if replaced:  # Only when a replacement was used, so results without one keep their shape.
                per_category[key]["replaced"] = [{"item": columns[low][1], "by": columns[by][1]} for low, by in replaced]
            if percent is not None:
                category_percents[key].append(percent)
                if weight:
                    weighted_sum += weight * percent
                    weight_total += weight
        if weighted:
            percent = weighted_sum / weight_total if weight_total else None
        else:
            percent = Fraction(100 * total_earned, total_possible) if total_possible else None
        letter = letter_for(ladder, percent, setup["letter_rounding"])
        if percent is not None:
            overall.append(percent)
            letters[letter] += 1
        results[row] = {"percent": _shown(percent, setup["letter_rounding"]), "letter": letter, "earned": _points(total_earned) if counted_any else None,
                        "possible": _points(total_possible) if counted_any else None, "categories": per_category}

    computed = {"students": results,
                "items": {column[1]: _item_stats(stats[column[1]], column[2]) for column in columns},
                "categories": {key: {"mean_percent": _percent(_mean(values)), "students": len(values)} for key, values in category_percents.items()},
                "overall": {"mean_percent": _percent(_mean(overall)), "students": len(overall), "letters": letters}}
    # A counted column of no category is left out of every weighted overall: named, so the grid can say so (additive, only when one is).
    uncounted = [item["id"] for item, group in zip(items, groups_of) if weighted and group is None and item["counts"] and not item["archived"]]
    if uncounted:
        computed["uncounted"] = uncounted
    if once_due:
        computed["blanks"] = shown
        if by_posting and by_posting[0]:
            computed["due_posted"] = [item["id"] for item in items if item["id"] in by_posting[0]]  # the setup's order
    return computed


def _replace(regular, replacements):
    """The category's regular entries with the replacement applied: (entries, [(replaced index, replacement index)]). The
    lowest percentage (ties: the later column) takes the replacement's percentage of its own points when that is higher.
    Setup allows one replacement per category; `replacements` holds its entry when it is scored and counted."""
    if not replacements or not regular:
        return regular, []
    by, earned, possible, _share = replacements[0]
    low = min(regular, key=lambda entry: (Fraction(entry[1], entry[2]), -entry[0]))
    if earned * low[2] <= low[1] * possible:
        return regular, []
    value = Fraction(earned * low[2], possible)
    value = value.numerator if value.denominator == 1 else value
    return [(entry[0], value, entry[2], entry[3]) if entry is low else entry for entry in regular], [(low[0], by)]


def _group(regular, extras, drop, aggregate, common):
    """One category of one student: (percent Fraction or None, earned, possible, dropped column indices in column order).
    `regular` and `extras` are (column index, earned, possible, share) in column order; every regular entry has possible
    points, and `share` is the column's part of the category's common denominator `common`."""
    bonus = sum(entry[1] for entry in extras)
    count = min(drop, len(regular) - 1)  # Never the last one.
    dropped = []
    if count > 0:
        gone = {entry[0] for entry in (_drop_points(regular, bonus, count) if aggregate == "points" else _drop_equal(regular, count))}
        regular = [entry for entry in regular if entry[0] not in gone]
        dropped = sorted(gone)
    earned = bonus + sum(entry[1] for entry in regular)
    possible = sum(entry[2] for entry in regular)
    if not possible:
        return None, earned, possible, dropped
    if aggregate == "points":
        return Fraction(100 * earned, possible), earned, possible, dropped
    # Equal weight: the mean of the kept columns' percentages, plus each extra-credit column's percentage of its own points
    # over the same count. An extra-credit column worth 0 points has no percentage (share 0) and adds nothing here.
    shares = sum(entry[1] * entry[3] for entry in regular) + sum(entry[1] * entry[3] for entry in extras)
    return Fraction(100 * shares, common * len(regular)), earned, possible, dropped


def _drop_equal(entries, count):
    """The `count` lowest percentages (earned * share ranks them, the category's denominator being common); among equal
    percentages the later column goes first."""
    return sorted(entries, key=lambda entry: (entry[1] * entry[3], -entry[0]))[:count]


def _drop_points(entries, bonus, count):
    """The `count` entries whose removal leaves the highest (bonus + Σ earned) / Σ possible, found exactly (Dinkelbach).

    Dropping the lowest percentages is NOT this: of 9/10, 0/1 and 40/100, dropping the 0/1 leaves 49/110 (44.5%) while
    dropping the 40/100 leaves 9/11 (81.8%). For a ratio q the kept set maximizing Σ(earned - q·possible) is simply the
    entries with the largest earned - q·possible; its own ratio is at least q and equals q only at the optimum. Starting from
    the ratio of all entries (never above the optimum), each round raises q strictly until it stops, and there are finitely
    many sets. At the optimum the ranking keeps an optimal set, and entries tied there keep the earlier column."""
    keep = len(entries) - count
    top, bottom = bonus + sum(entry[1] for entry in entries), sum(entry[2] for entry in entries)
    while True:
        ranked = sorted(entries, key=lambda entry: (entry[1] * bottom - top * entry[2], -entry[0]), reverse=True)
        kept = ranked[:keep]
        new_top, new_bottom = bonus + sum(entry[1] for entry in kept), sum(entry[2] for entry in kept)
        if new_top * bottom <= top * new_bottom:
            return ranked[keep:]
        top, bottom = new_top, new_bottom


def _mean(values):
    return sum(values, Fraction(0)) / len(values) if values else None


def _item_stats(tally, possible):
    scored, excused, missing, empty, points = tally
    result = {"scored": scored, "excused": excused, "missing": missing, "empty": empty,
              "mean": None, "median": None, "low": None, "high": None, "mean_percent": None}
    if points:
        ordered, middle = sorted(points), len(points) // 2
        median = ordered[middle] if len(ordered) % 2 else Fraction(ordered[middle - 1] + ordered[middle], 2)
        total = sum(ordered)
        result.update(mean=_points(Fraction(total, len(ordered))), median=_points(median), low=_points(ordered[0]),
                      high=_points(ordered[-1]), mean_percent=_percent(Fraction(100 * total, len(ordered) * possible)) if possible else None)
    return result
