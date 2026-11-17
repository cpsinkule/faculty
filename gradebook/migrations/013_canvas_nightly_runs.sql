-- THE MORNING SYNC (docs/decisions/canvas.md NIGHTLY SYNC; requirement, 2026-10-08). One row per attempt of a
-- term's morning run: the campus date it ran on, what it did (`kind` "sync", a Sync with Canvas of the courses Faculty has written
-- to before; "import", the WebAssign and Canvas-quiz scores alone, for a term whose first sync is still the instructor's), how it
-- ended (`status`) and, when it wrote, the import request it recorded and the send it started. Owned by the TERM
-- (course_terms.id); append-only, the latest row of a term's morning counts. `detail_json` holds counts, the titles of assignments
-- waiting for the instructor's choice and a problem's words; never a student's name, UIN or score. Rows of 007 stay as history.
CREATE TABLE gradebook_canvas_nightly_runs (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    night TEXT NOT NULL CHECK(night GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    kind TEXT NOT NULL CHECK(kind IN ('sync', 'import')),
    status TEXT NOT NULL CHECK(status IN ('synced', 'imported', 'unchanged', 'none', 'needs_choice', 'signed_out', 'failed')),
    detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
    request_ref TEXT REFERENCES gradebook_requests(id),
    send_ref TEXT REFERENCES gradebook_canvas_sends(id),
    created_at TEXT NOT NULL,
    CHECK((status IN ('synced', 'imported')) = (request_ref IS NOT NULL)),
    CHECK(send_ref IS NULL OR status = 'synced')
);
CREATE INDEX gradebook_canvas_nightly_runs_term ON gradebook_canvas_nightly_runs(term_id, night, created_at);

CREATE TRIGGER gradebook_canvas_nightly_run_owner BEFORE INSERT ON gradebook_canvas_nightly_runs
WHEN (NEW.request_ref IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id))
  OR (NEW.send_ref IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gradebook_canvas_sends WHERE id = NEW.send_ref AND term_id = NEW.term_id))
BEGIN SELECT RAISE(ABORT, 'Gradebook morning sync run owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_nightly_runs_immutable BEFORE UPDATE ON gradebook_canvas_nightly_runs
BEGIN SELECT RAISE(ABORT, 'Gradebook morning sync runs are immutable'); END;
CREATE TRIGGER gradebook_canvas_nightly_runs_retained BEFORE DELETE ON gradebook_canvas_nightly_runs
BEGIN SELECT RAISE(ABORT, 'Gradebook morning sync runs are retained'); END;
