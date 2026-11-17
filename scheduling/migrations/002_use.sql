-- SCHEDULING USE THIS SCHEDULE (stage 1d, 2026-10-05; the instructor chose to see every difference listed and confirm, with Undo): a
-- term starts showing its Term schedule on the Calendar and Google, and the per-date calendar events that matched it one for one
-- stop showing. Append-only receipts of each Use and Undo (with the dry run's fingerprint) and, per Use, the events it hides: a view,
-- never a change to calendar_events. A term uses its schedule while its latest receipt is a Use.
CREATE TABLE scheduling_uses (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 action TEXT NOT NULL CHECK(action IN ('use','undo')),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 fingerprint TEXT NOT NULL,
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL,
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, request_id)
);
CREATE TRIGGER scheduling_uses_immutable BEFORE UPDATE ON scheduling_uses
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: use receipts are immutable'); END;
CREATE TRIGGER scheduling_uses_retained BEFORE DELETE ON scheduling_uses
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: use receipts are retained'); END;
CREATE TABLE scheduling_replaced_events (
 use_id TEXT NOT NULL REFERENCES scheduling_uses(id),
 calendar_event_id TEXT NOT NULL,
 calendar_event_version INTEGER NOT NULL,
 entry_id TEXT NOT NULL REFERENCES scheduling_entries(id),
 date TEXT NOT NULL,
 PRIMARY KEY(use_id, calendar_event_id)
);
CREATE TRIGGER scheduling_replaced_events_immutable BEFORE UPDATE ON scheduling_replaced_events
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: replaced-event marks are immutable'); END;
CREATE TRIGGER scheduling_replaced_events_retained BEFORE DELETE ON scheduling_replaced_events
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: replaced-event marks are retained'); END;
-- DAYS OFF (stage 1d rehearsal, 2026-10-05: the term's per-date events skip Labor Day, Thanksgiving and a day run on another day's
-- schedule, which a weekly schedule alone would fill): each revision's dates without its weekly entries.
ALTER TABLE scheduling_revisions ADD COLUMN days_off_json TEXT NOT NULL DEFAULT '[]'
 CHECK(json_valid(days_off_json) AND json_type(days_off_json) = 'array' AND length(days_off_json) <= 2000);
