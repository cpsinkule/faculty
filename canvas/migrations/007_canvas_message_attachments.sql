-- Files with a Canvas message (requirement, 2026-10-09). WORKSPACE-owned, like the message. Canvas takes a message's files only
-- from the sender's own "conversation attachments" folder, so each file is uploaded there (never to a course's files) before the
-- message goes, once for every course of the message.
--
-- `canvas_message_attachments` is what the instructor approved, written with the message BEFORE any request leaves: each file's
-- name, type, size and SHA-256, and where it came from (the Faculty route it was read through, or "computer"). The bytes are never
-- kept. `canvas_message_uploads` is Canvas's answer to one file's upload (the latest counts): `uploaded` with the Canvas file's ID,
-- `failed`, or `uncertain` (no answer: the file may be in that folder, but no message names it). Append-only and retained.
CREATE TABLE canvas_message_attachments (
 message_id TEXT NOT NULL REFERENCES canvas_messages(id),
 position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 9),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 255),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 3 AND 200),
 size INTEGER NOT NULL CHECK(size > 0),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 source TEXT NOT NULL CHECK(source = 'computer' OR substr(source, 1, 5) = '/api/'),
 PRIMARY KEY(message_id, position)
);
CREATE TABLE canvas_message_uploads (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 message_id TEXT NOT NULL,
 position INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('uploaded','failed','uncertain')),
 canvas_file_id TEXT NOT NULL DEFAULT '' CHECK(canvas_file_id NOT GLOB '*[^0-9]*'),
 http_status INTEGER,
 detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 FOREIGN KEY(message_id, position) REFERENCES canvas_message_attachments(message_id, position)
);
CREATE INDEX canvas_message_uploads_file ON canvas_message_uploads(message_id, position, id);
CREATE TRIGGER canvas_message_attachments_immutable BEFORE UPDATE ON canvas_message_attachments BEGIN SELECT RAISE(ABORT,'Canvas message attachments are append-only'); END;
CREATE TRIGGER canvas_message_attachments_retained BEFORE DELETE ON canvas_message_attachments BEGIN SELECT RAISE(ABORT,'Canvas message attachments are retained'); END;
CREATE TRIGGER canvas_message_uploads_immutable BEFORE UPDATE ON canvas_message_uploads BEGIN SELECT RAISE(ABORT,'Canvas message uploads are append-only'); END;
CREATE TRIGGER canvas_message_uploads_retained BEFORE DELETE ON canvas_message_uploads BEGIN SELECT RAISE(ABORT,'Canvas message uploads are retained'); END;
