-- Exam-day packets (requirement, 2026-09-30). Owned at the course code +
-- term/year level (course_terms.id) for one course-wide assignment (assignments.id): exam records are often made after the exam,
-- so a plan is keyed by (term, assignment), never by exam_events.id. Each row is one immutable revision of the whole plan: the
-- proctor instructions and each room's proctors, keyed by the id of one of the term's seating charts for the assignment (checked
-- by the service). The highest revision counts; earlier ones are retained. Printing a packet reads this and the chart; it
-- creates no print job, file or delivery.
CREATE TABLE exam_day_plans (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 assignment_id TEXT NOT NULL REFERENCES assignments(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 instructions TEXT NOT NULL CHECK(length(instructions) <= 8000),
 proctors_json TEXT NOT NULL CHECK(json_valid(proctors_json) AND json_type(proctors_json)='object'),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, assignment_id, revision),
 UNIQUE(term_id, assignment_id, request_id)
);
CREATE TRIGGER exam_day_plans_scope BEFORE INSERT ON exam_day_plans
WHEN (SELECT definition_id FROM assignments WHERE id=NEW.assignment_id) IS NOT (SELECT definition_id FROM course_terms WHERE id=NEW.term_id)
BEGIN SELECT RAISE(ABORT,'An exam-day plan belongs to an assignment of its term''s course'); END;
CREATE TRIGGER exam_day_plans_next BEFORE INSERT ON exam_day_plans
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM exam_day_plans WHERE term_id=NEW.term_id AND assignment_id=NEW.assignment_id), 0)
BEGIN SELECT RAISE(ABORT,'An exam-day plan revision must follow the latest one'); END;
CREATE TRIGGER exam_day_plans_immutable BEFORE UPDATE ON exam_day_plans BEGIN SELECT RAISE(ABORT,'Exam-day plans are append-only'); END;
CREATE TRIGGER exam_day_plans_retained BEFORE DELETE ON exam_day_plans BEGIN SELECT RAISE(ABORT,'Exam-day plans are retained'); END;
