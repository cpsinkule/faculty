-- Google Calendar sync (requirement, 2026-09-28). WORKSPACE-owned: the connection is Faculty's one Google connection (response_cards/gmail.py) and
-- none of these rows belongs to a course, term or section.

-- The instructor's choices, append-only: the highest revision counts, earlier ones are retained.
CREATE TABLE google_calendar_settings (
 id TEXT PRIMARY KEY,
 revision INTEGER NOT NULL UNIQUE CHECK(revision >= 1),
 enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
 send_json TEXT NOT NULL CHECK(json_valid(send_json) AND json_type(send_json)='array'),
 calendars_json TEXT NOT NULL CHECK(json_valid(calendars_json) AND json_type(calendars_json)='array' AND json_array_length(calendars_json) <= 50),
 link_base TEXT NOT NULL DEFAULT '' CHECK(length(link_base) <= 200),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TRIGGER google_calendar_settings_next BEFORE INSERT ON google_calendar_settings
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM google_calendar_settings), 0)
BEGIN SELECT RAISE(ABORT,'A Google Calendar settings revision must follow the latest one'); END;
CREATE TRIGGER google_calendar_settings_immutable BEFORE UPDATE ON google_calendar_settings BEGIN SELECT RAISE(ABORT,'Google Calendar settings are append-only'); END;
CREATE TRIGGER google_calendar_settings_retained BEFORE DELETE ON google_calendar_settings BEGIN SELECT RAISE(ABORT,'Google Calendar settings are retained'); END;

-- What Faculty put in each account's main calendar: one row per Faculty event (a group of the Calendar page, or one custom
-- event), its Google event ID and the content last accepted by Google. A sync ledger, updated as Google answers; it is how
-- Faculty knows which Google events are its own, so it never changes or deletes one it did not make.
CREATE TABLE google_calendar_pushed (
 account TEXT NOT NULL,
 key TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('exam', 'seating', 'dr', 'deadline', 'custom')),
 google_id TEXT NOT NULL CHECK(length(google_id) BETWEEN 5 AND 1024),
 generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
 date TEXT NOT NULL,
 summary TEXT NOT NULL DEFAULT '',
 content_sha256 TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '',
 pushed_at TEXT,
 removed_at TEXT,
 PRIMARY KEY(account, key),
 UNIQUE(account, google_id)
);

-- The instructor's Google events Faculty shows on its Calendar: a copy of the chosen calendars over the sync window, replaced
-- calendar by calendar on every read. Never Faculty's own events, and never exported with the workspace.
CREATE TABLE google_calendar_events (
 account TEXT NOT NULL,
 calendar_id TEXT NOT NULL,
 event_id TEXT NOT NULL,
 calendar_name TEXT NOT NULL DEFAULT '',
 title TEXT NOT NULL,
 date TEXT NOT NULL,
 end_date TEXT NOT NULL CHECK(end_date >= date),
 time TEXT NOT NULL DEFAULT '',
 end_time TEXT NOT NULL DEFAULT '',
 all_day INTEGER NOT NULL CHECK(all_day IN (0, 1)),
 location TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '',
 link TEXT NOT NULL DEFAULT '',
 read_at TEXT NOT NULL,
 PRIMARY KEY(account, calendar_id, event_id)
);
CREATE INDEX google_calendar_events_range ON google_calendar_events(date, end_date);

-- Where the sync stands (last read, last sent, the reason it last stopped, a pause): one row per name, replaced as it goes.
CREATE TABLE google_calendar_state (
 name TEXT PRIMARY KEY,
 value_json TEXT NOT NULL CHECK(json_valid(value_json)),
 updated_at TEXT NOT NULL
);
