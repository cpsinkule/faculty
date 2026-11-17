"""Reviewed corrections to the department's room charts, applied by `RoomService.apply_corrections`.

Each correction names its source: the department's room workbook (cell references), or the DR seat-issue notes that
came with the ILCB floor plans. Decisions confirmed by the instructor on 2026-10-07 (first made for the standalone
Seating Charts app, whose tools/room_corrections.py holds the same list):

- Accessible seats ("H", "H (moveable chair)", the ILCB plans' wheelchair-accessible seats) and wheelchair spaces
  ("Wheelchair Only") are kept free: blocked, with a note. The instructor unblocks one in the room when a student needs it.
- A seat number the chart prints twice is never assigned: both copies are blocked, since which one the furniture carries
  can't be told from the chart.
- ILSB 1105's chart has no row letters; its rows are lettered A–N from the front (the narrow end at the bottom of the sheet).
- Chairs reported short without a location are a room note (a warning), not a review that blocks seating.

HECC 105 is left as the catalog has it: its workbook mislabels row H as L, corrected on 2026-09-18 (AGENTS.md "Rooms and
seating charts").

A correction works on one room as a dict: `name`, `notes`, `grid_columns`, `landmarks`, `layout`, `review_required` and
`seats` (`label`, `display`, `row`, `column`, `row_label`, `number`, `broken`, `blocked`, `notes`). Seats are changed in
place, so a relabelled seat stays the same physical seat with the same identity. A room that is not as the department's
file imports raises KeyError or ValueError and is left alone.
"""

ACCESSIBLE = "Accessible seat (marked “{mark}” on the department chart); kept free for a student who needs it."
WHEELCHAIR = "Wheelchair space (marked “Wheelchair Only” on the department chart); kept free for a student who needs it."
REPEATED = "The department chart prints seat {number} twice{missing}; both copies are left unassigned. Check the label in the room before using it."
PLAN_WHEELCHAIR = "Source plan: Wheelchair accessible seat."
KEPT_FREE = "Kept free for a student who needs it."


def _seat(room, label):
    for seat in room["seats"]:
        if seat["label"] == label:
            return seat
    raise KeyError(f"{room['name']}: no seat {label!r}")


def _resolve(room, *starts):
    """Drop the review items that begin with any of these texts. One already gone (the chart reader now understands the
    mark, or the review was confirmed in Room settings) is not an error."""
    room["review_required"] = [item for item in room["review_required"] if not item.startswith(starts)]


def _note(room, text):
    if text not in room["notes"]:
        room["notes"] = " ".join(part for part in (room["notes"], text) if part)
    if len(room["notes"]) > 280:
        raise ValueError(f"{room['name']}: room notes longer than 280 characters")


def _annotate(seat, note):
    if note and note not in seat["notes"]:
        seat["notes"] = " ".join(part for part in (seat["notes"], note) if part)


def _block(room, label, note):
    seat = _seat(room, label)
    seat["blocked"] = True
    _annotate(seat, note)


def _relabel(room, old, new, number=None, display=None, note=""):
    seat = _seat(room, old)
    if any(other["label"].casefold() == new.casefold() for other in room["seats"] if other is not seat):
        raise ValueError(f"{room['name']}: {new} already exists")
    seat["label"] = new
    if number is not None:
        seat["number"] = number
    if display is not None:
        seat["display"] = display
    _annotate(seat, note)
    layout = room.get("layout") or {}
    if layout.get("seats") and old in layout["seats"]:
        layout["seats"][new] = layout["seats"].pop(old)


def _accessible(room, labels, mark="H"):
    for label in labels:
        _block(room, label, ACCESSIBLE.format(mark=mark))


def _workbook_accessible(*labels):
    def correct(room):
        _accessible(room, labels)
        _resolve(room, "Cell")
    return correct


def _plan_wheelchair(room):
    """The ILCB plans' wheelchair-accessible seats (transfer-arm and bariatric seats stay ordinary seats)."""
    for seat in room["seats"]:
        if seat["notes"].startswith(PLAN_WHEELCHAIR):
            seat["blocked"] = True
            _annotate(seat, KEPT_FREE)


def _bloc_102(room):
    _workbook_accessible("L294", "K262", "K280")(room)
    # Row K reads …252 253 254 253 254 255…; K263 is missing.
    for label in ("K253 · H2", "K253 · J2"):
        _block(room, label, REPEATED.format(number="K253", missing=" (row K also skips K263)"))
    for label in ("K254 · I2", "K254 · K2"):
        _block(room, label, REPEATED.format(number="K254", missing=" (row K also skips K263)"))
    _resolve(room, "Source cells H2, J2 repeat seat", "Source cells I2, K2 repeat seat")


def _bloc_128(room):
    _resolve(room, "Source also lists 3 extra chairs")
    _note(room, "The department chart lists 3 extra chairs in the back without positions; they are not on this plan.")


def _bsbe_115(room):
    # Typo: the front row reads …15 16 17 | 16 19 20…, so the 16 in P13 is seat 18.
    _relabel(room, "16 · K13", "16", 16, "16")
    _relabel(room, "16 · P13", "18", 18, "18", "Printed “16” on the department chart (cell P13), between 17 and 19.")
    _resolve(room, "Source cells K13, P13 repeat seat")
    # 266 ends row 2 (Z2) and starts row 1 (A1); 245 is missing.
    for label in ("266 · A1", "266 · Z2"):
        _block(room, label, REPEATED.format(number="266", missing=" (and skips 245)"))
    _resolve(room, "Source cells A1, Z2 repeat seat")


def _hecc_row_g(room):
    # Typo: row G reads 8 7 6 5 4 6 2 1 (cells E2–L2), so the second 6, in K2, is seat 3.
    _relabel(room, "G6 · H2", "G6", 6, "6")
    _relabel(room, "G6 · K2", "G3", 3, "3", "Printed “6” on the department chart (cell K2), between 4 and 2.")
    _resolve(room, "Source cells H2, K2 repeat seat")


def _hecc_204(room):
    # 56 ends the top row (K2) and starts the next column (G7).
    for label in ("56 · K2", "56 · G7"):
        _block(room, label, REPEATED.format(number="56", missing=""))
    _resolve(room, "Source cells K2, G7 repeat seat")


def _klct_115(room):
    for label in ("N1", "A4", "A5", "A12", "A13"):
        _block(room, label, WHEELCHAIR)
    _resolve(room, "Cell")


def _ilsb_1105(room):
    """No row letters on the chart: each row is numbered 1… in a left and a right section. Letter the rows A–N from the
    front (the bottom of the sheet) and label seats like "C-L5" / "C-R5"; add the letters down the left side."""
    rows = sorted({seat["row"] for seat in room["seats"]}, reverse=True)
    letters = dict(zip(rows, "ABCDEFGHIJKLMN"))
    if len(rows) != 14:
        raise ValueError(f"ILSB 1105: expected 14 seat rows, found {len(rows)}")
    sides = {}
    for row in rows:
        ordered = sorted((seat for seat in room["seats"] if seat["row"] == row), key=lambda seat: seat["column"])
        restart = next((index for index, seat in enumerate(ordered) if index and seat["number"] == 1), None)
        if restart is None:
            raise ValueError(f"ILSB 1105: row {letters[row]} has no right section starting at 1")
        sides.update({id(seat): "L" if index < restart else "R" for index, seat in enumerate(ordered)})
    for seat in room["seats"]:
        letter = letters[seat["row"]]
        seat["label"] = f"{letter}-{sides[id(seat)]}{seat['number']}"
        seat["row_label"] = letter
        seat["column"] += 1
    for landmark in room["landmarks"]:
        landmark["column"] += 1
    room["landmarks"] += [{"kind": "row", "text": letter, "row": row, "column": 1, "row_span": 1, "column_span": 1, "row_label": letter}
                          for row, letter in letters.items()]
    room["grid_columns"] += 1
    labels = [seat["label"].casefold() for seat in room["seats"]]
    if len(set(labels)) != len(labels):
        raise ValueError("ILSB 1105: relabelled seats are not unique")
    # Row A (front): left 8 and right 1 are "H (moveable chair)".
    _accessible(room, ["A-L8", "A-R1"], "H (moveable chair)")
    _resolve(room, "Cell", "Source cells")
    _note(room, "The department chart has no row letters: rows are lettered A–N from the front, with left (L) and right (R) sections.")


def _ilcb_111(room):
    _plan_wheelchair(room)
    # DR seat-issue notes, "Seat 404 on map is actually 405; 405 is actually 406": only row A's fourth section ends at 405,
    # so only row A can be relabelled without a clash (the other rows already have a 406).
    _relabel(room, "405A", "406A", 406, "406", "Map seat 405A (DR seat-issue notes: “Seat 405 on the map is actually 406”).")
    _relabel(room, "404A", "405A", 405, "405", "Map seat 404A (DR seat-issue notes: “Seat 404 on map is actually 405”).")
    _resolve(room, "Seat 404 on map is actually 405", "Seat 405 on the map is actually 406")


def _ilcb_226(room):
    _plan_wheelchair(room)
    seat = _seat(room, "16E")
    seat["broken"] = True
    _annotate(seat, "The row E table has only 7 chairs (DR seat-issue notes); this position is left out.")
    _resolve(room, "Only 7 chairs at the table in row E")


def _ilcb_short(count):
    def correct(room):
        _plan_wheelchair(room)
        _resolve(room, f"{count} chair")
        _note(room, f"DR seat-issue notes: {count} chair{'s' if count != 1 else ''} short in the room, location not recorded. "
                    "Check the room before seating it nearly full.")
    return correct


def _ilcb_tables(room):
    _plan_wheelchair(room)
    _resolve(room, "The source numbers tables")
    _note(room, "The plan numbers tables only; chairs are numbered clockwise from the top of the plan. "
                "Students can sit at any chair of their table.")


CORRECTIONS = {
    "BLOC 102": _bloc_102,
    "BLOC 128": _bloc_128,
    "BLOC 166": _workbook_accessible("F106", "F120"),
    "BLOC 169": _workbook_accessible("F106", "F120"),
    "BSBE 115": _bsbe_115,
    "HECC 200": _hecc_row_g,
    "HECC 203": _hecc_row_g,
    "HECC 204": _hecc_204,
    "HELD 107": _workbook_accessible("H11"),
    "HELD 111": _workbook_accessible("H11"),
    "HELD 113": _workbook_accessible("H11"),
    "ILCB 111": _ilcb_111,
    "ILCB 112": _ilcb_short(1),
    "ILCB 113": _plan_wheelchair,
    "ILCB 205": _plan_wheelchair,
    "ILCB 207": _ilcb_short(8),
    "ILCB 224": _plan_wheelchair,
    "ILCB 226": _ilcb_226,
    "ILCB 229": _ilcb_short(6),
    "ILCB 233": _ilcb_tables,
    "ILCB 237": _ilcb_tables,
    "ILSB 1105": _ilsb_1105,
    "KLCT 115": _klct_115,
    "MPHY 205": _workbook_accessible("M11"),
}
