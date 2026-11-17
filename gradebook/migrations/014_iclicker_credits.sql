-- ICLICKER CREDIT BEFORE THE IMPORT (docs/decisions/iclicker.md; requirement, 2026-10-21). The instructor's credit for one student's iClicker polls of one class day, given before (or
-- after) that day's sessions are imported: every poll of the section's iClicker course started that campus date gets the column's
-- full points once it has a column (gradebook/iclicker_credit.py). One row per revision of a credit (`credit_id`): `waiting` gives
-- it, `withdrawn` takes it back. Owned by the student's SECTION MEMBERSHIP (section_id, enrollment_id) of its term; append-only,
-- the latest row of a credit counts.
CREATE TABLE gradebook_iclicker_credits (
    id TEXT PRIMARY KEY,
    credit_id TEXT NOT NULL,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    section_id TEXT NOT NULL,
    enrollment_id TEXT NOT NULL,
    class_date TEXT NOT NULL CHECK(class_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    state TEXT NOT NULL CHECK(state IN ('waiting', 'withdrawn')),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 200),
    request_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(term_id, request_id),
    FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
);
CREATE INDEX gradebook_iclicker_credits_member ON gradebook_iclicker_credits(term_id, section_id, enrollment_id, class_date, created_at);
CREATE INDEX gradebook_iclicker_credits_credit ON gradebook_iclicker_credits(credit_id, created_at);

CREATE TRIGGER gradebook_iclicker_credit_owner BEFORE INSERT ON gradebook_iclicker_credits
WHEN NOT EXISTS (SELECT 1 FROM course_sections WHERE id = NEW.section_id AND term_id = NEW.term_id)
  OR EXISTS (SELECT 1 FROM gradebook_iclicker_credits WHERE credit_id = NEW.credit_id
             AND (term_id != NEW.term_id OR section_id != NEW.section_id OR enrollment_id != NEW.enrollment_id OR class_date != NEW.class_date))
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker credit owner mismatch'); END;
CREATE TRIGGER gradebook_iclicker_credits_immutable BEFORE UPDATE ON gradebook_iclicker_credits
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker credits are immutable'); END;
CREATE TRIGGER gradebook_iclicker_credits_retained BEFORE DELETE ON gradebook_iclicker_credits
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker credits are retained'); END;
