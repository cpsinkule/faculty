"""Where a section print batch came from: the one shared rule (UX overhaul amendment 15, 2026-09-26).

The Assignments cards and the exam hub both derive an assignment's Paper/Seating/Print stages from the same print
batches, so they must agree on which batches count. A batch whose request ID starts with ``unlabeled:`` was issued by
first-page registration of papers printed without labels (``document_identity/unlabeled.py``): never printed, never a
print job the instructor made, so it never counts toward printing. A batch made by confirming a seating chart references
that chart; every other batch is an ordinary section print job. This module only classifies; it reads nothing.
"""

REGISTRATION_PREFIX = "unlabeled:"

REGISTRATION = "registration"
SEATING = "seating"
SECTION = "section"


def batch_origin(request_id, seating_chart_id):
    """Return 'registration', 'seating' or 'section' for one print batch.

    Registration wins over a chart reference: a registration job is never a printing, whatever it points to.
    """
    if isinstance(request_id, str) and request_id.startswith(REGISTRATION_PREFIX):
        return REGISTRATION
    if seating_chart_id:
        return SEATING
    return SECTION


def is_registration(request_id):
    """True for batches issued by first-page registration (never printed, never counted as printing)."""
    return batch_origin(request_id, None) == REGISTRATION
