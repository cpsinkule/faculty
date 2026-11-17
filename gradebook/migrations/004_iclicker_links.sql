-- iClicker imports (README.md, "Importing from iClicker"; requirement, 2026-09-28). Where an iClicker polling session's scores go in
-- a term's gradebook, confirmed once and then remembered by the session's iClicker ID: one row per change, the latest per
-- (term, course, session) counts, earlier rows are retained. Owned by the TERM (course_terms.id), like the setup it names columns
-- of. `item_id` NULL is "skip this session". The iClicker course is the one a section of the term links (iclicker_course_links);
-- iClicker's course and session IDs are its own UUID-like identifiers.
CREATE TABLE gradebook_iclicker_links (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 64 AND course_id NOT GLOB '*[^0-9A-Za-z-]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) BETWEEN 1 AND 64 AND session_id NOT GLOB '*[^0-9A-Za-z-]*'),
    item_id TEXT CHECK(item_id IS NULL OR length(item_id) BETWEEN 1 AND 100),
    request_ref TEXT NOT NULL REFERENCES gradebook_requests(id),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_iclicker_links_term ON gradebook_iclicker_links(term_id, course_id, session_id, created_at);

CREATE TRIGGER gradebook_iclicker_link_owner BEFORE INSERT ON gradebook_iclicker_links
WHEN NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker link owner mismatch'); END;
CREATE TRIGGER gradebook_iclicker_links_immutable BEFORE UPDATE ON gradebook_iclicker_links
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker links are immutable'); END;
CREATE TRIGGER gradebook_iclicker_links_retained BEFORE DELETE ON gradebook_iclicker_links
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker links are retained'); END;
