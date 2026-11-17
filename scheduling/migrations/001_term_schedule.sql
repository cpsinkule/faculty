-- SCHEDULING TERM SCHEDULE (requirement, 2026-10-05): a course
-- term's dates and weekly entries (each section's class meetings, office hours, meeting hours). Term-owned (course_terms.id) immutable
-- revisions with exact request IDs; every weekly entry keeps one stable identity across revisions (a class meeting also names its
-- section, course_sections.id, which must be the term's). Nothing reads a revision until the term uses its schedule (stage 1d).
CREATE TABLE scheduling_entries (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 section_id TEXT REFERENCES course_sections(id),
 kind TEXT NOT NULL CHECK(kind IN ('class','office_hours','meeting_hours')),
 created_at TEXT NOT NULL,
 CHECK((kind = 'class') = (section_id IS NOT NULL))
);
CREATE TRIGGER scheduling_entries_section BEFORE INSERT ON scheduling_entries
 WHEN NEW.section_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM course_sections WHERE id=NEW.section_id AND term_id=NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a class meeting belongs to a section of its own term'); END;
CREATE TRIGGER scheduling_entries_immutable BEFORE UPDATE ON scheduling_entries
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: schedule entries keep their identity'); END;
CREATE TRIGGER scheduling_entries_retained BEFORE DELETE ON scheduling_entries
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: schedule entries are retained'); END;
CREATE TABLE scheduling_revisions (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 first_day TEXT NOT NULL CHECK(first_day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
 last_day TEXT NOT NULL CHECK(last_day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND last_day >= first_day),
 entries_json TEXT NOT NULL CHECK(json_valid(entries_json) AND json_type(entries_json) = 'array' AND length(entries_json) <= 40000),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id, revision),
 UNIQUE(term_id, request_id)
);
CREATE TRIGGER scheduling_revisions_next BEFORE INSERT ON scheduling_revisions
 WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM scheduling_revisions WHERE term_id=NEW.term_id), 0)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a schedule revision must follow the latest one'); END;
CREATE TRIGGER scheduling_revisions_entries BEFORE INSERT ON scheduling_revisions
 WHEN EXISTS (SELECT 1 FROM json_each(NEW.entries_json) j WHERE NOT EXISTS (
   SELECT 1 FROM scheduling_entries e WHERE e.id = json_extract(j.value, '$.entry') AND e.term_id = NEW.term_id))
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: every weekly entry of a revision is one of its term''s entries'); END;
CREATE TRIGGER scheduling_revisions_immutable BEFORE UPDATE ON scheduling_revisions
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: schedule revisions are immutable'); END;
CREATE TRIGGER scheduling_revisions_retained BEFORE DELETE ON scheduling_revisions
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: schedule revisions are retained'); END;
