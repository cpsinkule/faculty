-- DEPARTMENT MAKEUP EXAMS › STUDENT NOTICE (ABSENCES; requirement, 2026-10-22).
--
-- absence_department_notices: the one email telling a student of the department makeup a request of Faculty's scheduled, recorded
-- BEFORE it is sent through the Mail page (`gmail_request_id` names its send; its status is that send's outcome). Owner: the request
-- (the student's enrollment). One per request, never sent again by itself. Append-only.
CREATE TABLE IF NOT EXISTS absence_department_notices (
 request_row_id TEXT PRIMARY KEY REFERENCES absence_department_requests(id),
 gmail_request_id TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS absence_department_notices_immutable BEFORE UPDATE ON absence_department_notices
BEGIN SELECT RAISE(ABORT,'Department makeup notices are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_department_notices_retained BEFORE DELETE ON absence_department_notices
BEGIN SELECT RAISE(ABORT,'Department makeup notices are retained'); END;
