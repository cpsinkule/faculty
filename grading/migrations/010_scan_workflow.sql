-- A scan arrival references a term-owned grading plan. Originals retain their
-- Inbox owner. Association revisions are immutable; the latest one is current.
CREATE TABLE frq_scan_links (
 id INTEGER PRIMARY KEY,
 message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 source TEXT NOT NULL CHECK(source IN ('automatic','workspace')),
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX frq_scan_links_message ON frq_scan_links(message_id,id DESC);
CREATE INDEX frq_scan_links_assignment ON frq_scan_links(assignment_id);
CREATE TRIGGER frq_scan_link_immutable BEFORE UPDATE ON frq_scan_links
 BEGIN SELECT RAISE(ABORT,'Scan association revisions are immutable'); END;
CREATE TRIGGER frq_scan_link_retained BEFORE DELETE ON frq_scan_links
 BEGIN SELECT RAISE(ABORT,'Scan association revisions are retained'); END;
-- Discovery cursors are operational metadata, not decisions or authorizations.
CREATE TABLE frq_scan_checks (message_id TEXT PRIMARY KEY REFERENCES inbox_messages(id), fingerprint TEXT NOT NULL);
