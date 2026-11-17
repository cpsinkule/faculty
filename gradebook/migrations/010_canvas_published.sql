-- PUBLISH FROM SYNC (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-07). Sync's
-- summary offers each column whose Canvas assignment is not published a tick, "Publish in Canvas, then send"; the instructor's
-- Sync publishes each ticked one in each course it sends to, before its grades. Each publish is recorded uncertain BEFORE Canvas
-- is asked and settled by reading the assignment back, one immutable row per step, owned by its send (so by the TERM).
CREATE TABLE gradebook_canvas_published (
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
CREATE INDEX gradebook_canvas_published_send ON gradebook_canvas_published(send_id, course_id, created_at);
CREATE TRIGGER gradebook_canvas_published_owner BEFORE INSERT ON gradebook_canvas_published
WHEN NOT EXISTS (SELECT 1 FROM gradebook_canvas_sends WHERE id = NEW.send_id AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Canvas publish receipt owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_published_immutable BEFORE UPDATE ON gradebook_canvas_published
BEGIN SELECT RAISE(ABORT, 'Canvas publish receipts are immutable'); END;
CREATE TRIGGER gradebook_canvas_published_retained BEFORE DELETE ON gradebook_canvas_published
BEGIN SELECT RAISE(ABORT, 'Canvas publish receipts are retained'); END;
