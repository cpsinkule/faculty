-- Pages identified by their position after a first-page label, on an explicit instructor request.
-- Runs and their pages are audit records of the existing source Inbox message and its scan pages.
CREATE TABLE IF NOT EXISTS scan_sequence_runs (
 id TEXT PRIMARY KEY NOT NULL, message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 sides INTEGER NOT NULL CHECK(sides BETWEEN 1 AND 200), plan_sha256 TEXT NOT NULL, summary_json TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT, undo_json TEXT
);
CREATE INDEX IF NOT EXISTS scan_sequence_runs_message ON scan_sequence_runs(message_id,created_at);
CREATE TABLE IF NOT EXISTS scan_sequence_pages (
 run_id TEXT NOT NULL REFERENCES scan_sequence_runs(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 anchor_scan_page_id TEXT NOT NULL REFERENCES scan_pages(id), page_stamp_id TEXT NOT NULL REFERENCES page_stamps(id),
 side INTEGER NOT NULL CHECK(side>=2), created_at TEXT NOT NULL, PRIMARY KEY(run_id,scan_page_id)
);
CREATE INDEX IF NOT EXISTS scan_sequence_pages_scan ON scan_sequence_pages(scan_page_id);
CREATE TRIGGER IF NOT EXISTS scan_sequence_page_owner BEFORE INSERT ON scan_sequence_pages
 WHEN (SELECT message_id FROM scan_sequence_runs WHERE id=NEW.run_id) IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.scan_page_id)
 OR (SELECT message_id FROM scan_sequence_runs WHERE id=NEW.run_id) IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.anchor_scan_page_id)
 BEGIN SELECT RAISE(ABORT,'Sequence pages must belong to their run''s Inbox message'); END;
CREATE TRIGGER IF NOT EXISTS scan_sequence_page_immutable BEFORE UPDATE ON scan_sequence_pages BEGIN SELECT RAISE(ABORT,'Sequence audit pages are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_sequence_page_retained BEFORE DELETE ON scan_sequence_pages BEGIN SELECT RAISE(ABORT,'Sequence audit pages are retained'); END;
CREATE TRIGGER IF NOT EXISTS scan_sequence_run_retained BEFORE DELETE ON scan_sequence_runs BEGIN SELECT RAISE(ABORT,'Sequence runs are retained'); END;
CREATE TRIGGER IF NOT EXISTS scan_sequence_run_immutable BEFORE UPDATE OF id,message_id,sides,plan_sha256,summary_json,actor,created_at ON scan_sequence_runs
 BEGIN SELECT RAISE(ABORT,'Sequence runs are immutable apart from their undo record'); END;
