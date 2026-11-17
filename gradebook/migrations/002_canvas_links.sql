-- Canvas imports (README.md, "Importing from Canvas"; requirement, 2026-09-27). Where a Canvas assignment's scores go in a term's
-- gradebook, confirmed once and then remembered by the Canvas assignment's ID: one row per change, the latest per
-- (term, course, assignment) counts, earlier rows are retained. Owned by the TERM (course_terms.id), like the setup it names
-- columns of. `item_id` NULL is "skip this assignment". The Canvas course is the one a section of the term links
-- (canvas_course_links); each course has its own assignment IDs, so a column may be named by one assignment per course.
CREATE TABLE gradebook_canvas_links (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
    assignment_id TEXT NOT NULL CHECK(length(assignment_id) BETWEEN 1 AND 20 AND assignment_id NOT GLOB '*[^0-9]*'),
    item_id TEXT CHECK(item_id IS NULL OR length(item_id) BETWEEN 1 AND 100),
    request_ref TEXT NOT NULL REFERENCES gradebook_requests(id),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_canvas_links_term ON gradebook_canvas_links(term_id, course_id, assignment_id, created_at);

CREATE TRIGGER gradebook_canvas_link_owner BEFORE INSERT ON gradebook_canvas_links
WHEN NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Gradebook Canvas link owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_links_immutable BEFORE UPDATE ON gradebook_canvas_links
BEGIN SELECT RAISE(ABORT, 'Gradebook Canvas links are immutable'); END;
CREATE TRIGGER gradebook_canvas_links_retained BEFORE DELETE ON gradebook_canvas_links
BEGIN SELECT RAISE(ABORT, 'Gradebook Canvas links are retained'); END;
