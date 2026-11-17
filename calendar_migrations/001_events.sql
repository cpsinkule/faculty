-- Custom calendar records live in the shared catalog. Their explicit owner is
-- the workspace, a course definition, a permanent term, or a permanent section.
-- Dates and times are campus-local wall-clock values, never UTC instants.
CREATE TABLE calendar_events (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes) <= 4000),
    location TEXT NOT NULL DEFAULT '' CHECK(length(location) <= 240),
    date TEXT NOT NULL,
    end_date TEXT NOT NULL CHECK(end_date >= date),
    time TEXT NOT NULL DEFAULT '',
    end_time TEXT NOT NULL DEFAULT '',
    all_day INTEGER NOT NULL CHECK(all_day IN (0, 1)),
    category TEXT NOT NULL CHECK(category IN ('event', 'deadline')),
    scope TEXT NOT NULL CHECK(scope IN ('workspace', 'course', 'term', 'section')),
    definition_id TEXT REFERENCES course_definitions(id),
    term_id TEXT REFERENCES course_terms(id),
    section_id TEXT REFERENCES course_sections(id),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    CHECK((scope = 'workspace' AND definition_id IS NULL AND term_id IS NULL AND section_id IS NULL)
       OR (scope = 'course' AND definition_id IS NOT NULL AND term_id IS NULL AND section_id IS NULL)
       OR (scope = 'term' AND definition_id IS NOT NULL AND term_id IS NOT NULL AND section_id IS NULL)
       OR (scope = 'section' AND definition_id IS NOT NULL AND term_id IS NOT NULL AND section_id IS NOT NULL)),
    CHECK((all_day = 1 AND time = '' AND end_time = '') OR (all_day = 0 AND time != ''))
);

CREATE INDEX calendar_events_range ON calendar_events(date, end_date) WHERE deleted_at IS NULL;
CREATE INDEX calendar_events_owner ON calendar_events(definition_id, term_id, section_id);

CREATE TRIGGER calendar_owner_insert BEFORE INSERT ON calendar_events
WHEN (NEW.term_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM course_terms WHERE id = NEW.term_id AND definition_id = NEW.definition_id AND deleted_at IS NULL
)) OR (NEW.section_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM course_sections WHERE id = NEW.section_id AND term_id = NEW.term_id
))
BEGIN SELECT RAISE(ABORT, 'Calendar owner hierarchy mismatch'); END;

CREATE TRIGGER calendar_owner_update BEFORE UPDATE OF definition_id, term_id, section_id ON calendar_events
WHEN (NEW.term_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM course_terms WHERE id = NEW.term_id AND definition_id = NEW.definition_id AND deleted_at IS NULL
)) OR (NEW.section_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM course_sections WHERE id = NEW.section_id AND term_id = NEW.term_id
))
BEGIN SELECT RAISE(ABORT, 'Calendar owner hierarchy mismatch'); END;
