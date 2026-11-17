-- CANVAS AS SYNC FOUND IT (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-08). Every Sync with Canvas keeps what Canvas held when it was recorded: the read both halves were planned from (the
-- summary's read, with the assignments Sync read again laid over it) of every linked course, its assignment groups, assignments,
-- students (Faculty's membership in place of the SIS ID; no UIN) and every submission, as gzip-compressed JSON
-- (`gradebook/canvas_snapshot.py`). One per sync, owned by the TERM through the sync's `import` request and recorded in the
-- sync's own transaction. Immutable and retained, like the request it belongs to.
CREATE TABLE gradebook_canvas_snapshots (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    request_ref TEXT NOT NULL UNIQUE REFERENCES gradebook_requests(id),
    format TEXT NOT NULL CHECK(format = 'canvas-read/1'),
    courses INTEGER NOT NULL CHECK(courses >= 0),
    submissions INTEGER NOT NULL CHECK(submissions >= 0),
    size INTEGER NOT NULL CHECK(size > 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    data BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_canvas_snapshots_term ON gradebook_canvas_snapshots(term_id, created_at);
CREATE TRIGGER gradebook_canvas_snapshot_owner BEFORE INSERT ON gradebook_canvas_snapshots
WHEN NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id AND kind = 'import')
BEGIN SELECT RAISE(ABORT, 'Canvas snapshot owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_snapshots_immutable BEFORE UPDATE ON gradebook_canvas_snapshots
BEGIN SELECT RAISE(ABORT, 'Canvas snapshots are immutable'); END;
CREATE TRIGGER gradebook_canvas_snapshots_retained BEFORE DELETE ON gradebook_canvas_snapshots
BEGIN SELECT RAISE(ABORT, 'Canvas snapshots are retained'); END;
