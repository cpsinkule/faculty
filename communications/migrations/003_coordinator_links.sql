-- THE COORDINATOR'S MAIL (COMMUNICATIONS; COORDINATOR; requirement, 2026-11-14). Mail from or to the coordinator of a coordinated course's current term is kept
-- in the record like a student's, and its link belongs to that TERM (`course_terms.id`): where a student's link names the
-- enrollment, the coordinator's names the owner key `coordinator:<term id>` (`course_coordinators.service.owner_key`), known
-- from the coordinator's address on the term's record (`coordinator_address`). An enrollment ID is ten digits, so the two never
-- collide, and a read by a student's enrollment never returns the coordinator's mail.
--
-- SQLite cannot drop a column's reference or widen a CHECK in place, so the links table is made again with every row it holds
-- and the same indexes and triggers (as 002 did). The implicit delete of DROP TABLE fires no trigger; nothing references the
-- table. What the dropped reference to `student_enrollments` enforced is now the trigger's: a link that is not the
-- coordinator's must name an enrollment of its term.
CREATE TABLE comm_links_next (
 id TEXT PRIMARY KEY,
 message_id TEXT NOT NULL REFERENCES comm_messages(id),
 enrollment_id TEXT NOT NULL,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 role TEXT NOT NULL CHECK(role IN ('author','to','cc','bcc','about')),
 matched_by TEXT NOT NULL CHECK(matched_by IN ('roster_address','faculty_send','canvas_sis','gradescope_sid','canvas_notice','coordinator_address')),
 created_at TEXT NOT NULL,
 UNIQUE(message_id, enrollment_id, role),
 -- The coordinator's link, and only it, carries the term's owner key.
 CHECK((matched_by='coordinator_address') = (enrollment_id = 'coordinator:' || term_id))
);
INSERT INTO comm_links_next(id, message_id, enrollment_id, term_id, role, matched_by, created_at)
SELECT id, message_id, enrollment_id, term_id, role, matched_by, created_at FROM comm_links;
DROP TABLE comm_links;
ALTER TABLE comm_links_next RENAME TO comm_links;
CREATE INDEX comm_links_enrollment ON comm_links(enrollment_id, message_id);
CREATE INDEX comm_links_term ON comm_links(term_id, message_id);
CREATE TRIGGER comm_links_term_matches BEFORE INSERT ON comm_links
WHEN NEW.matched_by != 'coordinator_address'
 AND NEW.term_id IS NOT (SELECT term_id FROM student_enrollments WHERE id=NEW.enrollment_id)
BEGIN SELECT RAISE(ABORT,'A communication link belongs to the term of its enrollment'); END;
CREATE TRIGGER comm_links_immutable BEFORE UPDATE ON comm_links BEGIN SELECT RAISE(ABORT,'Communication links are append-only'); END;
CREATE TRIGGER comm_links_retained BEFORE DELETE ON comm_links BEGIN SELECT RAISE(ABORT,'Communication links are retained'); END;
