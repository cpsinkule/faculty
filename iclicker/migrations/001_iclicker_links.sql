-- iClicker (requirement, 2026-09-28). A section's iClicker course is SECTION-owned (course_sections.id), chosen once in the section's
-- Import from iClicker and changed only there: an append-only link, the latest row per section counts, earlier rows are retained.
-- iClicker course IDs are its own UUID-like identifiers. Nothing here copies a roster or a score.
CREATE TABLE iclicker_course_links (
 id TEXT PRIMARY KEY,
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 64 AND course_id NOT GLOB '*[^0-9A-Za-z-]*'),
 course_name TEXT NOT NULL,
 term TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX iclicker_course_links_section ON iclicker_course_links(section_id,created_at);
CREATE TRIGGER iclicker_course_link_immutable BEFORE UPDATE ON iclicker_course_links BEGIN SELECT RAISE(ABORT,'iClicker course links are append-only'); END;
CREATE TRIGGER iclicker_course_link_retained BEFORE DELETE ON iclicker_course_links BEGIN SELECT RAISE(ABORT,'iClicker course links are retained'); END;
