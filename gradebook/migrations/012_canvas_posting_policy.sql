-- POSTING GRADES, AT EVERY SYNC (docs/decisions/canvas.md; requirement, 2026-10-08). A column's posting choice is the instructor's standing Post, Hide or back to its category's
-- rule ("rule"), recorded when they choose it from the column's menu (or approve the assistant's card) and carried out by every
-- Sync with Canvas from then on. Owned by the TERM (course_terms.id); append-only, the latest row of a column counts.
CREATE TABLE gradebook_canvas_posting_choices (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    choice TEXT NOT NULL CHECK(choice IN ('post', 'hide', 'rule')),
    request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 8 AND 128),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(term_id, request_id)
);
CREATE INDEX gradebook_canvas_posting_choices_item ON gradebook_canvas_posting_choices(term_id, item_id, created_at);
CREATE TRIGGER gradebook_canvas_posting_choices_immutable BEFORE UPDATE ON gradebook_canvas_posting_choices
BEGIN SELECT RAISE(ABORT, 'Canvas posting choices are immutable'); END;
CREATE TRIGGER gradebook_canvas_posting_choices_retained BEFORE DELETE ON gradebook_canvas_posting_choices
BEGIN SELECT RAISE(ABORT, 'Canvas posting choices are retained'); END;

-- MANUAL POSTING (requirement, 2026-10-08): before a sync sends the grades of a column whose category posts by hand, it sets that column's Canvas assignment to
-- post grades by hand where Canvas posts it automatically (GraphQL `setAssignmentPostPolicy`). Each switch is recorded uncertain
-- BEFORE Canvas is asked and settled by reading the assignment back, one immutable row per step, owned by its send (so by the TERM).
CREATE TABLE gradebook_canvas_policies (
    id TEXT PRIMARY KEY,
    send_id TEXT NOT NULL REFERENCES gradebook_canvas_sends(id),
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
    assignment_id TEXT NOT NULL CHECK(length(assignment_id) BETWEEN 1 AND 20 AND assignment_id NOT GLOB '*[^0-9]*'),
    state TEXT NOT NULL CHECK(state IN ('accepted', 'failed', 'uncertain')),
    canvas_json TEXT NOT NULL CHECK(json_valid(canvas_json)),
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_canvas_policies_item ON gradebook_canvas_policies(term_id, item_id, course_id, created_at);
CREATE TRIGGER gradebook_canvas_policies_owner BEFORE INSERT ON gradebook_canvas_policies
WHEN NOT EXISTS (SELECT 1 FROM gradebook_canvas_sends WHERE id = NEW.send_id AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Canvas posting policy receipt owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_policies_immutable BEFORE UPDATE ON gradebook_canvas_policies
BEGIN SELECT RAISE(ABORT, 'Canvas posting policy receipts are immutable'); END;
CREATE TRIGGER gradebook_canvas_policies_retained BEFORE DELETE ON gradebook_canvas_policies
BEGIN SELECT RAISE(ABORT, 'Canvas posting policy receipts are retained'); END;
