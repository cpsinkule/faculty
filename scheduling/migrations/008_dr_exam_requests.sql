-- SCHEDULING "DR REQUEST EMAILS" (requirement, 2026-11-01): the DR Testing Center's
-- emails that a student requested to sit an exam or quiz at the Testing Center ("[DR] New Exam Request") or that it approved one
-- ("[DR] Testing Request Approved"), read from the instructor's Gmail, each kept once with what it says of the request: the UIN,
-- course, section, type, day, start and whether it was late; never the student's name.
-- Owner: the student's enrollment in the section (student_enrollments.id with course_sections.id) when the UIN and section are a
-- current student's; both empty otherwise (kept so the email is not read again). Append-only.
CREATE TABLE dr_exam_requests (
 gmail_message_id TEXT PRIMARY KEY CHECK(length(gmail_message_id) BETWEEN 1 AND 32),
 received_at TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('new','approved')),
 uin TEXT NOT NULL CHECK(length(uin) = 9),
 code TEXT NOT NULL CHECK(length(code) <= 20),
 section TEXT NOT NULL CHECK(length(section) <= 10),
 exam_type TEXT NOT NULL CHECK(length(exam_type) <= 60),
 date TEXT NOT NULL CHECK(length(date) = 10),
 start TEXT NOT NULL CHECK(start = '' OR length(start) = 5),
 late INTEGER NOT NULL CHECK(late IN (0,1)),
 enrollment_id TEXT REFERENCES student_enrollments(id),
 section_id TEXT REFERENCES course_sections(id),
 created_at TEXT NOT NULL,
 CHECK((enrollment_id IS NULL) = (section_id IS NULL))
);
CREATE INDEX dr_exam_requests_student ON dr_exam_requests(section_id, date, enrollment_id);
CREATE TRIGGER dr_exam_requests_immutable BEFORE UPDATE ON dr_exam_requests
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR request emails are immutable'); END;
CREATE TRIGGER dr_exam_requests_retained BEFORE DELETE ON dr_exam_requests
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR request emails are retained'); END;
-- The Follow-up Faculty opened for a kept request of a DR quiz: the student's tracked follow-up and the run holding its upload card,
-- one per student, planned row, section and day (whichever of the request's emails came first). Owner: that follow-up
-- (student_support_items.id), naming the request, the planned row and the run by reference.
CREATE TABLE dr_exam_request_followups (
 gmail_message_id TEXT PRIMARY KEY REFERENCES dr_exam_requests(gmail_message_id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 item_id TEXT NOT NULL REFERENCES scheduling_plan_items(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 date TEXT NOT NULL CHECK(length(date) = 10),
 followup_id TEXT NOT NULL,
 run_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(item_id, section_id, enrollment_id, date)
);
CREATE TRIGGER dr_exam_request_followups_immutable BEFORE UPDATE ON dr_exam_request_followups
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR request follow-ups are immutable'); END;
CREATE TRIGGER dr_exam_request_followups_retained BEFORE DELETE ON dr_exam_request_followups
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: DR request follow-ups are retained'); END;
