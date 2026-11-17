-- WEBASSIGN NIGHTLY (docs/decisions/canvas.md; requirement, 2026-10-04). One row per attempt of a
-- term's nightly Import from Canvas for the assignments WebAssign grades: the campus night it belongs to, what it did (`status`)
-- and, when it wrote, the import request it recorded. Owned by the TERM (course_terms.id); append-only, the latest row of a term's
-- night counts. `detail_json` holds counts, the titles of assignments waiting for the instructor's choice and a problem's words;
-- never a student's name, UIN or score.
CREATE TABLE gradebook_nightly_runs (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    source TEXT NOT NULL CHECK(source IN ('webassign')),
    night TEXT NOT NULL CHECK(night GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    status TEXT NOT NULL CHECK(status IN ('imported', 'unchanged', 'none', 'needs_choice', 'signed_out', 'failed')),
    detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
    request_ref TEXT REFERENCES gradebook_requests(id),
    created_at TEXT NOT NULL,
    CHECK((status = 'imported') = (request_ref IS NOT NULL))
);
CREATE INDEX gradebook_nightly_runs_term ON gradebook_nightly_runs(term_id, source, night, created_at);

CREATE TRIGGER gradebook_nightly_run_owner BEFORE INSERT ON gradebook_nightly_runs
WHEN NEW.request_ref IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Gradebook nightly run owner mismatch'); END;
CREATE TRIGGER gradebook_nightly_runs_immutable BEFORE UPDATE ON gradebook_nightly_runs
BEGIN SELECT RAISE(ABORT, 'Gradebook nightly runs are immutable'); END;
CREATE TRIGGER gradebook_nightly_runs_retained BEFORE DELETE ON gradebook_nightly_runs
BEGIN SELECT RAISE(ABORT, 'Gradebook nightly runs are retained'); END;
