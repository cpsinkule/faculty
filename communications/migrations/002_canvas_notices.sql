-- Canvas's message notification emails as a backup of the Canvas inbox (requirement, 2026-09-30). Canvas emails every message a student sends as "<their name> <notifications@instructure.com>" with the
-- course and section in the subject, so the address names nobody: such an email is linked to the ONE current student whose roster
-- name holds every word of the sender's name and who is in the section the subject names (`canvas_notice`), never otherwise.
--
-- SQLite cannot widen a CHECK in place, so the links table is rebuilt with every row it holds and the same indexes and triggers. The
-- implicit delete of DROP TABLE fires no trigger; nothing references the table.
CREATE TABLE comm_links_next (
 id TEXT PRIMARY KEY,
 message_id TEXT NOT NULL REFERENCES comm_messages(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 role TEXT NOT NULL CHECK(role IN ('author','to','cc','bcc','about')),
 matched_by TEXT NOT NULL CHECK(matched_by IN ('roster_address','faculty_send','canvas_sis','gradescope_sid','canvas_notice')),
 created_at TEXT NOT NULL,
 UNIQUE(message_id, enrollment_id, role)
);
INSERT INTO comm_links_next(id, message_id, enrollment_id, term_id, role, matched_by, created_at)
SELECT id, message_id, enrollment_id, term_id, role, matched_by, created_at FROM comm_links;
DROP TABLE comm_links;
ALTER TABLE comm_links_next RENAME TO comm_links;
CREATE INDEX comm_links_enrollment ON comm_links(enrollment_id, message_id);
CREATE INDEX comm_links_term ON comm_links(term_id, message_id);
CREATE TRIGGER comm_links_term_matches BEFORE INSERT ON comm_links
WHEN NEW.term_id != (SELECT term_id FROM student_enrollments WHERE id=NEW.enrollment_id)
BEGIN SELECT RAISE(ABORT,'A communication link belongs to the term of its enrollment'); END;
CREATE TRIGGER comm_links_immutable BEFORE UPDATE ON comm_links BEGIN SELECT RAISE(ABORT,'Communication links are append-only'); END;
CREATE TRIGGER comm_links_retained BEFORE DELETE ON comm_links BEGIN SELECT RAISE(ABORT,'Communication links are retained'); END;
