-- iClicker session dates (README.md "Importing from iClicker"; docs/decisions/absences.md ICLICKER-CREDIT, requirement, 2026-10-01).
-- The campus date a polling session started on, as an import of the term read it from iClicker: a row when an import first reads
-- the session or reads another date for it; the latest per (term, course, session) counts, earlier rows are retained. Owned by the
-- TERM (course_terms.id), like gradebook_iclicker_links, whose rows say which column the session's scores go to.
CREATE TABLE gradebook_iclicker_sessions (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 64 AND course_id NOT GLOB '*[^0-9A-Za-z-]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) BETWEEN 1 AND 64 AND session_id NOT GLOB '*[^0-9A-Za-z-]*'),
    session_date TEXT NOT NULL CHECK(session_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    request_ref TEXT NOT NULL REFERENCES gradebook_requests(id),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_iclicker_sessions_term ON gradebook_iclicker_sessions(term_id, course_id, session_id, created_at);

CREATE TRIGGER gradebook_iclicker_session_owner BEFORE INSERT ON gradebook_iclicker_sessions
WHEN NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker session owner mismatch'); END;
CREATE TRIGGER gradebook_iclicker_sessions_immutable BEFORE UPDATE ON gradebook_iclicker_sessions
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker session dates are immutable'); END;
CREATE TRIGGER gradebook_iclicker_sessions_retained BEFORE DELETE ON gradebook_iclicker_sessions
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker session dates are retained'); END;
