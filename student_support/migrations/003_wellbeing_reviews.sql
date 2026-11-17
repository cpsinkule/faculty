-- JEV WELLBEING / TEACHING-WORKFLOWS: the instructor's "reviewed" of one possible wellbeing concern. Owned by the student's
-- term enrollment; it names the exact Jev receipt (analysed conversation state) it clears, so a newer message flagged again shows.
CREATE TABLE student_support_wellbeing_reviews (
 request_id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 source_key TEXT NOT NULL,
 call_id TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_wellbeing_reviews_call ON student_support_wellbeing_reviews(call_id);
CREATE TRIGGER student_support_wellbeing_reviews_immutable BEFORE UPDATE ON student_support_wellbeing_reviews
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: wellbeing reviews are immutable'); END;
CREATE TRIGGER student_support_wellbeing_reviews_retained BEFORE DELETE ON student_support_wellbeing_reviews
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: wellbeing reviews are retained'); END;
