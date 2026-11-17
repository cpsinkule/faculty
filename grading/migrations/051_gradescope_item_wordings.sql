-- RENAMED ITEMS (requirement, 2026-10-31). A grade for a question Faculty made on Gradescope is matched by its creation receipt
-- (`map_created`), which refuses an item worded neither as it was sent nor as Faculty's line is now. One row is one wording of one
-- item Faculty made, read on Gradescope when the instructor recorded it, that the match accepts as well; Faculty's rubric (and so
-- every grade on it) is left as it is. Owned by the TERM grading plan (frq_assignments.id), like 030's receipts; no student is
-- named. Append-only: a later wording is a new row; nothing is changed or deleted.
CREATE TABLE frq_gradescope_item_wordings (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id),
 receipt_id TEXT NOT NULL REFERENCES frq_gradescope_created_questions(id),
 item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 gradescope_item_id TEXT NOT NULL CHECK(length(gradescope_item_id) BETWEEN 1 AND 20 AND gradescope_item_id NOT GLOB '*[^0-9]*'),
 description TEXT NOT NULL CHECK(length(description) BETWEEN 1 AND 4000),
 reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(receipt_id,item_id,description)
);
CREATE INDEX frq_gradescope_item_wordings_plan ON frq_gradescope_item_wordings(assignment_id,question_id);
CREATE TRIGGER frq_gradescope_item_wording_immutable BEFORE UPDATE ON frq_gradescope_item_wordings
BEGIN SELECT RAISE(ABORT,'A recorded Gradescope wording never changes'); END;
CREATE TRIGGER frq_gradescope_item_wording_retained BEFORE DELETE ON frq_gradescope_item_wordings
BEGIN SELECT RAISE(ABORT,'Recorded Gradescope wordings are retained'); END;
