-- LINKED VERSIONS (requirement, 2026-10-02): which rubric lines of one problem correspond across the versions of the TERM grading
-- plan, as the instructor decided it. One row links (or, with kind 'none', unlinks) two lines of two different questions of the
-- plan: 'same' lines credit the same criterion with the same kind of answer, 'related' lines the same step with a different
-- outcome or structure on each version. Rows are append-only; the latest row of a pair of lines is its current decision and wins
-- over the reviewer's links and the shared line IDs of rubrics drafted together. Lines are named by item ID (not by rubric
-- revision), so a decision survives the instructor's line edits; one naming a line that no longer exists is ignored. Links are an
-- aid to reviewing and editing: never a rubric, guidance, score or part of what a check or confirmation covers.
CREATE TABLE frq_rubric_line_links (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id), item_id TEXT NOT NULL,
 other_question_id TEXT NOT NULL REFERENCES frq_questions(id), other_item_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('same','related','none')), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 CHECK(question_id < other_question_id)
);
CREATE INDEX frq_rubric_line_links_plan ON frq_rubric_line_links(assignment_id,created_at);
CREATE TRIGGER frq_rubric_line_link_scope BEFORE INSERT ON frq_rubric_line_links
 WHEN NOT EXISTS(SELECT 1 FROM frq_assignment_questions WHERE assignment_id=NEW.assignment_id AND question_id=NEW.question_id)
   OR NOT EXISTS(SELECT 1 FROM frq_assignment_questions WHERE assignment_id=NEW.assignment_id AND question_id=NEW.other_question_id)
 BEGIN SELECT RAISE(ABORT,'A rubric line link must join lines of two questions of its grading plan'); END;
CREATE TRIGGER frq_rubric_line_link_immutable BEFORE UPDATE ON frq_rubric_line_links
 BEGIN SELECT RAISE(ABORT,'Rubric line links are append-only'); END;
CREATE TRIGGER frq_rubric_line_link_retained BEFORE DELETE ON frq_rubric_line_links
 BEGIN SELECT RAISE(ABORT,'Rubric line links are retained'); END;
