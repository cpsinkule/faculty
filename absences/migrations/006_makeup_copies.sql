-- UNNAMED MAKEUP COPIES (ABSENCES "MAKEUP BOOKING" › COPIES; requirement, 2026-10-16). A booked student's makeup copy is printed for no student, as a quiz's are, and is
-- named from the Name and UIN written on it once scanned (DOCUMENTS, `copy_names`). The copy no longer says whom it was made for,
-- so this records it: which booked students of a sitting a makeup print job was made for, so none is printed twice.
--
-- absence_makeup_copies: one row per student a makeup print job (request `makeup-copies:`) was made for. Owner: the student's
-- enrollment (their makeup of that sitting); the job is referenced, never copied. Append-only.
CREATE TABLE IF NOT EXISTS absence_makeup_copies (
 sitting_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 print_batch_id TEXT NOT NULL REFERENCES print_batches(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(sitting_id, enrollment_id, print_batch_id)
);
CREATE INDEX IF NOT EXISTS absence_makeup_copies_batch ON absence_makeup_copies(print_batch_id);
CREATE TRIGGER IF NOT EXISTS absence_makeup_copies_immutable BEFORE UPDATE ON absence_makeup_copies
BEGIN SELECT RAISE(ABORT,'Makeup copy records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_copies_retained BEFORE DELETE ON absence_makeup_copies
BEGIN SELECT RAISE(ABORT,'Makeup copy records are retained'); END;
