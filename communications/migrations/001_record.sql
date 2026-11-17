-- The record of electronic communications between the instructor and students (requirement, 2026-09-30). AGENTS.md "Communications record".
--
-- OWNERS. A captured message (`comm_messages`, with its immutable `comm_revisions`) is WORKSPACE-owned: it is the instructor's own
-- correspondence, and one message may concern students of several terms. Each link to a student (`comm_links`) belongs to that
-- student's TERM ENROLLMENT (`student_enrollments.id`); the timeline of a student in a term is exactly its links. Kept originals (a
-- Gmail message as Gmail holds it, with its attachments; a file attached in Canvas) are files in verified storage: a `comm_bundles`
-- row is one storage owner (the files one capture kept, written at once and never changed; staged on NVMe, backed up by the nightly
-- coordinator, then kept on the HDD alone), and `comm_files` names each of its files with its checksum. Faculty's own records of mail
-- it sent (Response Card and seat-chart mail, Mail page sends) and students' Response Card answers are shown by reference, never
-- copied here. Everything is append-only and retained, except the storage columns the storage manager keeps and the capture state.
CREATE TABLE comm_bundles (
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 storage_status TEXT NOT NULL DEFAULT '',
 storage_snapshot TEXT NOT NULL DEFAULT '',
 storage_verified_at TEXT NOT NULL DEFAULT '',
 storage_initialized INTEGER NOT NULL DEFAULT 0,
 storage_hot_required INTEGER NOT NULL DEFAULT 0,
 storage_error TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER comm_bundles_identity BEFORE UPDATE OF id, created_at ON comm_bundles
BEGIN SELECT RAISE(ABORT,'A communication bundle keeps its identity'); END;
CREATE TRIGGER comm_bundles_retained BEFORE DELETE ON comm_bundles BEGIN SELECT RAISE(ABORT,'Communication bundles are retained'); END;

CREATE TABLE comm_files (
 id TEXT PRIMARY KEY,
 bundle_id TEXT NOT NULL REFERENCES comm_bundles(id),
 path TEXT NOT NULL CHECK(length(path) BETWEEN 1 AND 200),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 size INTEGER NOT NULL CHECK(size >= 0),
 content_type TEXT NOT NULL CHECK(length(content_type) <= 200),
 name TEXT NOT NULL DEFAULT '' CHECK(length(name) <= 300),
 created_at TEXT NOT NULL,
 UNIQUE(bundle_id, path)
);
CREATE INDEX comm_files_bundle ON comm_files(bundle_id);
CREATE TRIGGER comm_files_immutable BEFORE UPDATE ON comm_files BEGIN SELECT RAISE(ABORT,'Kept communication files are append-only'); END;
CREATE TRIGGER comm_files_retained BEFORE DELETE ON comm_files BEGIN SELECT RAISE(ABORT,'Kept communication files are retained'); END;

-- One message of one channel: a Gmail message (external_id = Gmail's message ID), a message of a Canvas conversation
-- ("<conversation>:<message>"), a comment on a Canvas submission ("<course>:<comment>"), or one side of a Gradescope regrade request
-- ("<course>:<request>:request" and "...:reply"). Its content is in revisions: a Canvas comment or a Gradescope reply edited later is
-- a new revision, and the latest counts.
CREATE TABLE comm_messages (
 id TEXT PRIMARY KEY,
 channel TEXT NOT NULL CHECK(channel IN ('gmail','canvas_message','canvas_comment','gradescope_regrade')),
 external_id TEXT NOT NULL CHECK(length(external_id) BETWEEN 1 AND 300),
 thread_key TEXT NOT NULL DEFAULT '' CHECK(length(thread_key) <= 300),
 captured_at TEXT NOT NULL,
 UNIQUE(channel, external_id)
);
CREATE INDEX comm_messages_thread ON comm_messages(channel, thread_key);
CREATE TRIGGER comm_messages_immutable BEFORE UPDATE ON comm_messages BEGIN SELECT RAISE(ABORT,'Captured messages are append-only'); END;
CREATE TRIGGER comm_messages_retained BEFORE DELETE ON comm_messages BEGIN SELECT RAISE(ABORT,'Captured messages are retained'); END;

CREATE TABLE comm_revisions (
 id TEXT PRIMARY KEY,
 message_id TEXT NOT NULL REFERENCES comm_messages(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 sent_at TEXT NOT NULL,
 author_role TEXT NOT NULL CHECK(author_role IN ('student','instructor','staff','other')),
 author_json TEXT NOT NULL CHECK(json_valid(author_json) AND json_type(author_json)='object'),
 recipients_json TEXT NOT NULL CHECK(json_valid(recipients_json) AND json_type(recipients_json)='array'),
 subject TEXT NOT NULL DEFAULT '' CHECK(length(subject) <= 1000),
 body_text TEXT NOT NULL CHECK(length(body_text) <= 200000),
 attachments_json TEXT NOT NULL CHECK(json_valid(attachments_json) AND json_type(attachments_json)='array'),
 context_json TEXT NOT NULL CHECK(json_valid(context_json) AND json_type(context_json)='object'),
 original_file_id TEXT REFERENCES comm_files(id),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
 captured_at TEXT NOT NULL,
 UNIQUE(message_id, revision)
);
CREATE INDEX comm_revisions_message ON comm_revisions(message_id, revision);
CREATE TRIGGER comm_revisions_next BEFORE INSERT ON comm_revisions
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM comm_revisions WHERE message_id=NEW.message_id), 0)
BEGIN SELECT RAISE(ABORT,'A message revision must follow the latest one'); END;
CREATE TRIGGER comm_revisions_immutable BEFORE UPDATE ON comm_revisions BEGIN SELECT RAISE(ABORT,'Message revisions are append-only'); END;
CREATE TRIGGER comm_revisions_retained BEFORE DELETE ON comm_revisions BEGIN SELECT RAISE(ABORT,'Message revisions are retained'); END;

-- Which students a message concerns, and how that was known: their roster address in the message (`roster_address`), Faculty's own
-- record of what it sent (`faculty_send`), their Canvas SIS ID or Gradescope SID equal to their UIN (`canvas_sis`, `gradescope_sid`).
-- `role`: the student wrote it (author), it went to them (to, cc, bcc), or it is about their work (about: a comment on their
-- submission, a reply to their regrade request). Never guessed; a link is never removed.
CREATE TABLE comm_links (
 id TEXT PRIMARY KEY,
 message_id TEXT NOT NULL REFERENCES comm_messages(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 role TEXT NOT NULL CHECK(role IN ('author','to','cc','bcc','about')),
 matched_by TEXT NOT NULL CHECK(matched_by IN ('roster_address','faculty_send','canvas_sis','gradescope_sid')),
 created_at TEXT NOT NULL,
 UNIQUE(message_id, enrollment_id, role)
);
CREATE INDEX comm_links_enrollment ON comm_links(enrollment_id, message_id);
CREATE INDEX comm_links_term ON comm_links(term_id, message_id);
CREATE TRIGGER comm_links_term_matches BEFORE INSERT ON comm_links
WHEN NEW.term_id != (SELECT term_id FROM student_enrollments WHERE id=NEW.enrollment_id)
BEGIN SELECT RAISE(ABORT,'A communication link belongs to the term of its enrollment'); END;
CREATE TRIGGER comm_links_immutable BEFORE UPDATE ON comm_links BEGIN SELECT RAISE(ABORT,'Communication links are append-only'); END;
CREATE TRIGGER comm_links_retained BEFORE DELETE ON comm_links BEGIN SELECT RAISE(ABORT,'Communication links are retained'); END;

-- Where each channel's capture stands (mutable runtime state, not a record): the last round, what it found or why it waits, when the
-- next round is due, and whether the instructor asked for one now.
CREATE TABLE comm_capture_state (
 channel TEXT PRIMARY KEY CHECK(channel IN ('gmail','canvas','gradescope')),
 status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle','ok','waiting','failed')),
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000),
 counts_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(counts_json) AND json_type(counts_json)='object'),
 last_round_at TEXT NOT NULL DEFAULT '',
 last_success_at TEXT NOT NULL DEFAULT '',
 next_due_at TEXT NOT NULL DEFAULT '',
 requested_at TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT ''
);
INSERT INTO comm_capture_state(channel) VALUES ('gmail'), ('canvas'), ('gradescope');
