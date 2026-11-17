-- Canvas assignments Faculty created while sending (README.md "Sending to Canvas"; requirement, 2026-09-28).
-- One immutable row per assignment an approved send created in a Canvas course for a gradebook column, owned by the TERM through
-- that send: how the next send and Import from Canvas know the assignment is the column's. `request_json` holds what was asked
-- for (name, points, group) and whether Faculty only found it after an answer was lost (`adopted`).
CREATE TABLE gradebook_canvas_created (
    id TEXT PRIMARY KEY,
    send_id TEXT NOT NULL REFERENCES gradebook_canvas_sends(id),
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
    assignment_id TEXT NOT NULL CHECK(length(assignment_id) BETWEEN 1 AND 20 AND assignment_id NOT GLOB '*[^0-9]*'),
    request_json TEXT NOT NULL CHECK(json_valid(request_json)),
    created_at TEXT NOT NULL,
    UNIQUE(course_id, assignment_id)
);
CREATE INDEX gradebook_canvas_created_term ON gradebook_canvas_created(term_id, course_id, item_id);

CREATE TRIGGER gradebook_canvas_created_owner BEFORE INSERT ON gradebook_canvas_created
WHEN NOT EXISTS (SELECT 1 FROM gradebook_canvas_sends WHERE id = NEW.send_id AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Created Canvas assignment owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_created_immutable BEFORE UPDATE ON gradebook_canvas_created
BEGIN SELECT RAISE(ABORT, 'Created Canvas assignments are immutable'); END;
CREATE TRIGGER gradebook_canvas_created_retained BEFORE DELETE ON gradebook_canvas_created
BEGIN SELECT RAISE(ABORT, 'Created Canvas assignments are retained'); END;
