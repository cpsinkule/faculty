-- Canvas integration (requirement, 2026-09-27). A section's Canvas course is SECTION-owned (course_sections.id), set once on the
-- section's course page and changed only there: an append-only link, the latest row per section counts, earlier rows are retained.
-- Several sections may link the same Canvas course (a combined course). Nothing here copies a roster or a grade.
CREATE TABLE canvas_course_links (
 id TEXT PRIMARY KEY,
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 course_name TEXT NOT NULL,
 course_code TEXT NOT NULL,
 term TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_course_links_section ON canvas_course_links(section_id,created_at);
CREATE TRIGGER canvas_course_link_immutable BEFORE UPDATE ON canvas_course_links BEGIN SELECT RAISE(ABORT,'Canvas course links are append-only'); END;
CREATE TRIGGER canvas_course_link_retained BEFORE DELETE ON canvas_course_links BEGIN SELECT RAISE(ABORT,'Canvas course links are retained'); END;
