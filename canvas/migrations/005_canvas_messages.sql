-- Messages to students through Canvas's inbox (requirement, 2026-10-01). WORKSPACE-owned, like Mail's sends: the instructor's own
-- outgoing correspondence, which may go to sections of several terms. It names the sections it was sent to by reference
-- (course_sections.id) and copies nothing of theirs but who Canvas was asked to deliver to.
--
-- `canvas_messages` is the approved message; `canvas_message_deliveries` is one Canvas course's share of it (one request to
-- Canvas each): both are written and committed BEFORE any request leaves. `canvas_message_outcomes` says what Canvas answered for
-- a delivery (the latest counts). A delivery with no outcome was interrupted: it is shown as uncertain and never sent again.
-- Everything here is append-only and retained.
CREATE TABLE canvas_messages (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
 subject TEXT NOT NULL CHECK(length(subject) BETWEEN 1 AND 255),
 body_text TEXT NOT NULL CHECK(length(body_text) >= 1),
 sections_json TEXT NOT NULL CHECK(json_valid(sections_json) AND json_type(sections_json)='array'),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_messages_created ON canvas_messages(created_at);
CREATE TABLE canvas_message_deliveries (
 id TEXT PRIMARY KEY,
 message_id TEXT NOT NULL REFERENCES canvas_messages(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 course_name TEXT NOT NULL,
 sections_json TEXT NOT NULL CHECK(json_valid(sections_json) AND json_type(sections_json)='array'),
 recipients_json TEXT NOT NULL CHECK(json_valid(recipients_json) AND json_type(recipients_json)='array'),
 mode TEXT NOT NULL CHECK(mode IN ('sync','async')),
 created_at TEXT NOT NULL,
 UNIQUE(message_id, course_id)
);
CREATE TABLE canvas_message_outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 delivery_id TEXT NOT NULL REFERENCES canvas_message_deliveries(id),
 status TEXT NOT NULL CHECK(status IN ('accepted','failed','uncertain')),
 http_status INTEGER,
 conversations INTEGER,
 batch_id TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_message_outcomes_delivery ON canvas_message_outcomes(delivery_id, id);
CREATE TRIGGER canvas_messages_immutable BEFORE UPDATE ON canvas_messages BEGIN SELECT RAISE(ABORT,'Canvas messages are append-only'); END;
CREATE TRIGGER canvas_messages_retained BEFORE DELETE ON canvas_messages BEGIN SELECT RAISE(ABORT,'Canvas messages are retained'); END;
CREATE TRIGGER canvas_message_deliveries_immutable BEFORE UPDATE ON canvas_message_deliveries BEGIN SELECT RAISE(ABORT,'Canvas message deliveries are append-only'); END;
CREATE TRIGGER canvas_message_deliveries_retained BEFORE DELETE ON canvas_message_deliveries BEGIN SELECT RAISE(ABORT,'Canvas message deliveries are retained'); END;
CREATE TRIGGER canvas_message_outcomes_immutable BEFORE UPDATE ON canvas_message_outcomes BEGIN SELECT RAISE(ABORT,'Canvas message outcomes are append-only'); END;
CREATE TRIGGER canvas_message_outcomes_retained BEFORE DELETE ON canvas_message_outcomes BEGIN SELECT RAISE(ABORT,'Canvas message outcomes are retained'); END;
