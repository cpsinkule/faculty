-- The Faculty assistant's action log (requirement, 2026-09-29). WORKSPACE-owned. Every write the assistant asked for is one immutable
-- `assistant_actions` row: `auto` writes it made itself (the categories the instructor chose), `approve` writes it prepared for
-- the instructor's click. What happened to it is an append-only list of `assistant_action_events` (the latest counts). Nothing
-- here replaces the owning module's own records: the write itself goes through the ordinary route with its own request ID,
-- receipts and checks. Conversations and their text are not kept.
CREATE TABLE assistant_actions (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL CHECK(length(session_id) BETWEEN 1 AND 64),
 engine TEXT NOT NULL CHECK(length(engine) BETWEEN 1 AND 40),
 tier TEXT NOT NULL CHECK(tier IN ('auto','approve')),
 endpoint TEXT NOT NULL,
 method TEXT NOT NULL CHECK(method IN ('POST','PUT','PATCH','DELETE')),
 path TEXT NOT NULL CHECK(substr(path,1,5)='/api/'),
 body_json TEXT NOT NULL CHECK(json_valid(body_json) AND json_type(body_json)='object'),
 summary TEXT NOT NULL CHECK(length(summary) <= 2000),
 created_at TEXT NOT NULL
);
CREATE INDEX assistant_actions_created ON assistant_actions(created_at);
CREATE TABLE assistant_action_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 action_id TEXT NOT NULL REFERENCES assistant_actions(id),
 kind TEXT NOT NULL CHECK(kind IN ('proposed','previewed','approved','declined','expired','succeeded','failed')),
 actor TEXT NOT NULL CHECK(actor IN ('assistant','instructor','faculty')),
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json) AND json_type(detail_json)='object'),
 created_at TEXT NOT NULL
);
CREATE INDEX assistant_action_events_action ON assistant_action_events(action_id, id);
CREATE TRIGGER assistant_actions_immutable BEFORE UPDATE ON assistant_actions BEGIN SELECT RAISE(ABORT,'Assistant actions are immutable'); END;
CREATE TRIGGER assistant_actions_retained BEFORE DELETE ON assistant_actions BEGIN SELECT RAISE(ABORT,'Assistant actions are retained'); END;
CREATE TRIGGER assistant_action_events_immutable BEFORE UPDATE ON assistant_action_events BEGIN SELECT RAISE(ABORT,'Assistant action events are append-only'); END;
CREATE TRIGGER assistant_action_events_retained BEFORE DELETE ON assistant_action_events BEGIN SELECT RAISE(ABORT,'Assistant action events are retained'); END;
