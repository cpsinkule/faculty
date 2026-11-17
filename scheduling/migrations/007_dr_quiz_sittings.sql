-- SCHEDULING "CLASS SCHEDULE: DR QUIZZES" › DR DATE (requirement, 2026-10-31): the day a section's DR quiz takers sit a class-schedule quiz at the DR center when that is not the class's own day.
-- The DR quizzes read, Send to AIM and the DR quiz notices then use it in place of the class day; the class schedule is unchanged.
-- Owner: the section (course_sections.id), naming its term's planned row (scheduling_plan_items.id) by reference. Append-only:
-- the latest row of a (row, section) counts, and an empty date clears it.
CREATE TABLE dr_quiz_sittings (
 id INTEGER PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 item_id TEXT NOT NULL REFERENCES scheduling_plan_items(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 date TEXT NOT NULL CHECK(date = '' OR length(date) = 10),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, request_id)
);
CREATE INDEX dr_quiz_sittings_row ON dr_quiz_sittings(term_id, item_id, section_id);
CREATE TRIGGER dr_quiz_sittings_scope BEFORE INSERT ON dr_quiz_sittings
 WHEN NOT EXISTS (SELECT 1 FROM scheduling_plan_items i WHERE i.id = NEW.item_id AND i.term_id = NEW.term_id)
   OR NOT EXISTS (SELECT 1 FROM course_sections s WHERE s.id = NEW.section_id AND s.term_id = NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a DR date names a planned row and a section of its own term'); END;
CREATE TRIGGER dr_quiz_sittings_immutable BEFORE UPDATE ON dr_quiz_sittings
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR dates are immutable'); END;
CREATE TRIGGER dr_quiz_sittings_retained BEFORE DELETE ON dr_quiz_sittings
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR dates are retained'); END;
