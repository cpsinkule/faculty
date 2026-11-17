-- SCHEDULING DR QUIZ NOTICES (requirement, 2026-10-15, answers 1(a), 2(a), 3(a), 4(a), 6): the instructor's "Prescheduled in AIM"
-- mark on a DR quiz row (Job 2's accepted AIM write records the same mark), and the automatic email it starts to each DR student of
-- the section: one email per student listing the marked quizzes they were not yet told of. A mark lapses when the quiz's date or
-- start changes; marking it again sends one "updated" notice. Every notice is sent once: a failed or uncertain email waits for an
-- explicit Retry, never an automatic resend (AGENTS.md OUTSIDE SERVICES).
-- Owners: a mark is the course term's (course_terms.id), naming the term's planned row (scheduling_plan_items.id) and its section
-- (course_sections.id) by reference; a notice is the student's enrollment in that section (student_enrollments.id with
-- course_sections.id), naming the mark it told and the Mail page's send record (gmail_mail_sends.request_id) that carried it.
-- Append-only: the latest mark of a quiz counts, an unmark names no date.
CREATE TABLE dr_quiz_marks (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 item_id TEXT NOT NULL REFERENCES scheduling_plan_items(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 action TEXT NOT NULL CHECK(action IN ('mark','unmark')),
 date TEXT NOT NULL,
 start TEXT NOT NULL,
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, request_id),
 CHECK(CASE action WHEN 'mark' THEN length(date) = 10 AND length(start) = 5 ELSE date = '' AND start = '' END)
);
CREATE INDEX dr_quiz_marks_quiz ON dr_quiz_marks(term_id, item_id, section_id);
CREATE TRIGGER dr_quiz_marks_scope BEFORE INSERT ON dr_quiz_marks
 WHEN NOT EXISTS (SELECT 1 FROM scheduling_plan_items i WHERE i.id = NEW.item_id AND i.term_id = NEW.term_id)
   OR NOT EXISTS (SELECT 1 FROM course_sections s WHERE s.id = NEW.section_id AND s.term_id = NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a DR quiz mark names a planned row and a section of its own term'); END;
CREATE TRIGGER dr_quiz_marks_immutable BEFORE UPDATE ON dr_quiz_marks
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR quiz marks are immutable'); END;
CREATE TRIGGER dr_quiz_marks_retained BEFORE DELETE ON dr_quiz_marks
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR quiz marks are retained'); END;
CREATE TABLE dr_quiz_notices (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 item_id TEXT NOT NULL REFERENCES scheduling_plan_items(id),
 date TEXT NOT NULL CHECK(length(date) = 10),
 start TEXT NOT NULL CHECK(length(start) = 5),
 mark_id TEXT NOT NULL REFERENCES dr_quiz_marks(id),
 attempt INTEGER NOT NULL CHECK(attempt >= 1),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(enrollment_id, section_id, item_id, date, start, attempt)
);
CREATE INDEX dr_quiz_notices_request ON dr_quiz_notices(request_id);
CREATE TRIGGER dr_quiz_notices_scope BEFORE INSERT ON dr_quiz_notices
 WHEN NOT EXISTS (SELECT 1 FROM dr_quiz_marks m WHERE m.id = NEW.mark_id AND m.action = 'mark' AND m.term_id = NEW.term_id
                  AND m.item_id = NEW.item_id AND m.section_id = NEW.section_id AND m.date = NEW.date AND m.start = NEW.start)
   OR NOT EXISTS (SELECT 1 FROM student_enrollments e WHERE e.id = NEW.enrollment_id AND e.term_id = NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a DR quiz notice tells a mark of its own quiz to a student of its term'); END;
CREATE TRIGGER dr_quiz_notices_immutable BEFORE UPDATE ON dr_quiz_notices
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR quiz notices are immutable'); END;
CREATE TRIGGER dr_quiz_notices_retained BEFORE DELETE ON dr_quiz_notices
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR quiz notices are retained'); END;
