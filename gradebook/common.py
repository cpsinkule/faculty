"""Shared helpers of the gradebook: row keys, exact points, fingerprints and the one rule for what an import or a pull
may change in a cell. Every gradebook module imports these; none of them owns data.

Points are exact decimals, never floats: a value is parsed with `parse_points` and written back with `points_text`, which
keeps at most four decimal places and drops trailing zeros (4, 2.5, 8.3333). Percentages are shown with two decimals.
"""
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from canonical_json import digest_strict as digest, encoded_strict as encoded  # noqa: F401 - what the gradebook modules import from here

RULE = "gradebook-v1"
PLACES = Decimal("0.0001")  # The finest points a score or a column may carry.
MAX_POINTS = Decimal(100000)
STATUSES = ("scored", "excused", "missing", "cleared")
ROW = re.compile(r"[A-Za-z0-9-]{1,64}:[0-9]{10}")
DIGITS = re.compile(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")  # a minus reaches the range check and its message


def row_key(section_id, enrollment_id):
    """One student's place in a term's gradebook: their permanent section membership, `section_id:enrollment_id`."""
    return f"{section_id}:{enrollment_id}"


def split_row(value):
    if not isinstance(value, str) or not ROW.fullmatch(value):
        raise ValueError("Name a student of this term's roster.")
    section_id, enrollment_id = value.rsplit(":", 1)
    return section_id, enrollment_id


def parse_points(value, label="Points", *, maximum=MAX_POINTS, allow_blank=False, unit="points"):
    """Exact, non-negative points in ten-thousandths at most; returns the canonical text (or None for a blank when allowed).
    `unit` names what is counted in the messages (a weight or a percentage is not points)."""
    if allow_blank and (value is None or (isinstance(value, str) and not value.strip())):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a number{' of ' + unit if unit else ''}.")
    text = str(value).strip()
    # Plain ASCII digits and an optional decimal point: Decimal alone would also take "1_0", "1e2" and other scripts' digits.
    if not DIGITS.fullmatch(text):
        raise ValueError(f"{label} must be a number{' of ' + unit if unit else ''}.")
    try:
        points = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{label} must be a number{' of ' + unit if unit else ''}.") from None
    if not points.is_finite() or points < 0 or points > maximum or points != points.quantize(PLACES, ROUND_HALF_UP):
        raise ValueError(f"{label} must be between 0 and {maximum}{' ' + unit if unit else ''}, in ten-thousandths at most.")
    return points_text(points.copy_abs())  # "-0" is 0: one value, one text


def points_text(value):
    """Points as the instructor would write them: 4, 2.5, 8.3333."""
    text = format(Decimal(value).quantize(PLACES, ROUND_HALF_UP), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def percent_text(value, places=2):
    """A percentage (Decimal or Fraction-derived Decimal) with a fixed number of decimals, rounded half up; None stays None."""
    if value is None:
        return None
    return format(Decimal(value).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP), "f")


def new_id(prefix):
    """A server-made identifier for a category (`cat_`) or a column (`item_`); never reused within a term."""
    return f"{prefix}_{uuid.uuid4().hex}"


def same_value(current, proposed):
    """Whether a cell already says what an import or a pull proposes (status and exact points)."""
    if current is None or current["status"] == "cleared":
        return False
    if current["status"] != proposed["status"]:
        return False
    if proposed["status"] != "scored":
        return True
    return Decimal(current["points"]) == Decimal(proposed["points"])


def change_action(current, proposed, *, replace_hand=False, foreign=False):
    """The ONE rule for what an import or a pull does to a cell (`current` is the cell's latest revision or None):

    add      the cell is empty (no revision, or cleared by an import or pull);
    same     it already holds exactly this value: nothing is written;
    kept     the instructor entered its value by hand in Faculty (source `instructor`), or CLEARED it there, or `foreign` says
             another source wrote it (Import from iClicker or from Canvas: a value the other one, a file or a pull brought in,
             KEEPS OTHERS), and did not tick Replace: it stays. A cleared cell is a decision too (a mis-assigned paper's grade taken out): a
             later pull must not quietly put the grade back;
    update   anything else: an earlier import or pull is replaced by the newer value.
    """
    if current is None:
        return "add"
    if current["status"] == "cleared":
        return "kept" if current["source"] == "instructor" and not replace_hand else "add"
    if same_value(current, proposed):
        return "same"
    if (current["source"] == "instructor" or foreign) and not replace_hand:
        return "kept"
    return "update"
