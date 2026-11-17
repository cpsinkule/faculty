"""Immutable layout contracts, retained on versions, batches and page stamps.

Versions belong to the course definition; issued placements belong to the
section's print batch. New profiles never change an existing issued placement.
"""
from copy import deepcopy

MM = 72 / 25.4
PROFILE = "faculty-page-v1"
CORNERS_PROFILE = "faculty-corners-10mm-v1"
LAYOUT = {"id": PROFILE, "box_mm": 25, "right_mm": 10, "bottom_mm": 10,
          "barcode_mm": 20, "alias_mm": 5, "quiet_modules": 1, "module_mm": 20 / 26,
          "symbol_modules": 24, "symbology": "DataMatrix"}
CORNERS_LAYOUT = {"id": CORNERS_PROFILE, "box_mm": 15, "margin_mm": 10,
                  "barcode_mm": 10, "alias_mm": 5, "quiet_modules": 1,
                  "module_mm": 10 / 26, "symbol_modules": 24, "symbology": "DataMatrix",
                  "corners": ["top_right", "bottom_left"], "marks_per_page": 2}
# The default since 2026-10-18 (instructor: "I want all stamping to be this size matrix now"; "get rid of" the printed alias):
# the token's 16 bytes behind a 0x01 byte, packed as raw bytes, fit a 20x20 symbol, drawn 7 mm with its 1-module quiet zone
# (22 modules) in an 8 mm box 10 mm from the edges, with no recovery alias printed. Printed tests read it 22/22 on both home
# scanners where today's 24x24 text code at 7 mm read 18/22 and 13/22. Peel-off labels keep the 10 mm corner mark.
SMALL_CORNERS_PROFILE = "faculty-corners-7mm-v1"
SMALL_CORNERS_LAYOUT = {"id": SMALL_CORNERS_PROFILE, "box_mm": 8, "margin_mm": 10, "barcode_mm": 7, "alias_mm": 0,
                        "quiet_modules": 1, "module_mm": 7 / 22, "symbol_modules": 20, "symbology": "DataMatrix",
                        "encoding": "bytes-v1", "corners": ["top_right", "bottom_left"], "marks_per_page": 2,
                        "label_profile": CORNERS_PROFILE, "check_dpi": 300}
PROFILES = {PROFILE: LAYOUT, CORNERS_PROFILE: CORNERS_LAYOUT, SMALL_CORNERS_PROFILE: SMALL_CORNERS_LAYOUT}


def get_layout(profile):
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError("Unsupported physical layout profile.")
    return deepcopy(PROFILES[profile])


def stamp_boxes(width, height, profile):
    """Reserved boxes in PDF points, from the bottom-left page origin."""
    layout = get_layout(profile)
    box = layout["box_mm"] * MM
    if profile == PROFILE:
        return [(width - 35 * MM, 10 * MM, box)]
    margin = layout["margin_mm"] * MM
    return [(width - margin - box, height - margin - box, box), (margin, margin, box)]


SEAT_MARK_FONT, SEAT_MARK_SIZE = "Helvetica-Bold", 13


def seat_mark_positions(width, height, profile):
    """Where an optional visible seat label goes: beside each stamp box, its top on the box's top edge.

    Returns (x, baseline, align) per box, in PDF points. The label stays in the
    class's reserved band, 2 mm clear of the box, so the mark and its quiet zone
    are untouched. The single default box gets the label on its left; the corners
    profile puts it left of the top-right box and right of the bottom-left box.
    """
    positions = []
    for index, (x, y, box) in enumerate(stamp_boxes(width, height, profile)):
        baseline = y + box - 0.72 * SEAT_MARK_SIZE
        if profile == PROFILE or index == 0:
            positions.append((x - 2 * MM, baseline, "right"))
        else:
            positions.append((x + box + 2 * MM, baseline, "left"))
    return positions


def barcode_boxes(width, height, profile):
    """A mark sits above its alias band, or centred in a box that prints no alias."""
    layout = get_layout(profile)
    size = layout["barcode_mm"] * MM
    return [(x + (box - size) / 2, y + (layout["alias_mm"] * MM if layout["alias_mm"] else (box - size) / 2), size)
            for x, y, box in stamp_boxes(width, height, profile)]


def label_profile(profile):
    """The layout a peel-off label is drawn with: labels keep their size when a page layout's mark shrinks."""
    return get_layout(profile).get("label_profile", profile)
