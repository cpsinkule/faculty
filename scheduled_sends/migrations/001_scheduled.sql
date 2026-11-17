-- Messages sent later (requirement, 2026-11-07). WORKSPACE-owned, like every Mail page and Canvas
-- message send. A row is the message exactly as the instructor approved it (the send's own fields, its fingerprint and, for Canvas,
-- the reviewed recipients), recorded when it is scheduled; each change of state is an event, the latest counting: `sent` (the send
-- was made; its ordinary receipts, gmail_mail_sends or canvas_messages, are under the request ID `send_request_id` names, and say
-- what came of it), `held` (not sent; it waits on Needs you), `cancelled`, `acknowledged` (the instructor has seen a send that failed
-- or could not be confirmed). Append-only, retained.
CREATE TABLE scheduled_sends (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 channel TEXT NOT NULL CHECK(channel IN ('mail','canvas')),
 send_at TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 request_json TEXT NOT NULL,
 plan_json TEXT NOT NULL DEFAULT '{}',
 subject TEXT NOT NULL,
 audience TEXT NOT NULL,
 recipients INTEGER NOT NULL CHECK(recipients >= 0),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX scheduled_sends_at ON scheduled_sends(send_at);
CREATE TABLE scheduled_send_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scheduled_id TEXT NOT NULL REFERENCES scheduled_sends(id),
 kind TEXT NOT NULL CHECK(kind IN ('sent','held','cancelled','acknowledged')),
 send_request_id TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX scheduled_send_events_of ON scheduled_send_events(scheduled_id, id);
CREATE TRIGGER scheduled_sends_immutable BEFORE UPDATE ON scheduled_sends BEGIN SELECT RAISE(ABORT,'scheduled messages are append-only'); END;
CREATE TRIGGER scheduled_sends_retained BEFORE DELETE ON scheduled_sends BEGIN SELECT RAISE(ABORT,'scheduled messages are retained'); END;
CREATE TRIGGER scheduled_send_events_immutable BEFORE UPDATE ON scheduled_send_events BEGIN SELECT RAISE(ABORT,'scheduled message events are append-only'); END;
CREATE TRIGGER scheduled_send_events_retained BEFORE DELETE ON scheduled_send_events BEGIN SELECT RAISE(ABORT,'scheduled message events are retained'); END;
