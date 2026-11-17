-- TA contacts (requirement, 2026-09-28). SECTION-owned (course_sections.id): each row is one immutable revision of the
-- section's whole TA list, the highest revision counts, earlier ones are retained. A TA who helps several sections is listed in each
-- (Copy from another section is the instructor's explicit click); nothing propagates between sections or terms.
CREATE TABLE section_ta_contacts (
 id TEXT PRIMARY KEY,
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 contacts_json TEXT NOT NULL CHECK(json_valid(contacts_json) AND json_type(contacts_json)='array' AND json_array_length(contacts_json) <= 20),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(section_id, revision),
 UNIQUE(section_id, request_id)
);
CREATE TRIGGER section_ta_contacts_next BEFORE INSERT ON section_ta_contacts
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM section_ta_contacts WHERE section_id=NEW.section_id), 0)
BEGIN SELECT RAISE(ABORT,'A TA list revision must follow the latest one'); END;
CREATE TRIGGER section_ta_contacts_immutable BEFORE UPDATE ON section_ta_contacts BEGIN SELECT RAISE(ABORT,'TA lists are append-only'); END;
CREATE TRIGGER section_ta_contacts_retained BEFORE DELETE ON section_ta_contacts BEGIN SELECT RAISE(ABORT,'TA lists are retained'); END;
