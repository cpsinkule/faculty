-- QUICK CHOICES BEFORE DRAFTING (requirement, 2026-10-24). The instructor's answers, for ONE assignment, to a few recurring rubric questions (is work needed for full credit, do
-- rounded decimals count, do abbreviations of a required word count, when does an "all incorrect" line apply), given before its
-- rubrics are drafted. The rubric writers and the rubric check read them as the instructor's word on this assignment, so the check
-- does not ask them again. Owned by the TERM grading plan (frq_assignments.id). Append-only: the latest row of a plan is its choices;
-- a choice left out is "ask me". Nothing here changes a rubric, a confirmation or a grade.
CREATE TABLE frq_rubric_choices (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 choices_json TEXT NOT NULL CHECK(json_valid(choices_json)),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_choices_plan ON frq_rubric_choices(assignment_id, created_at);
CREATE TRIGGER frq_rubric_choices_immutable BEFORE UPDATE ON frq_rubric_choices
 BEGIN SELECT RAISE(ABORT,'Rubric choices are immutable: a change is a new row'); END;
CREATE TRIGGER frq_rubric_choices_retained BEFORE DELETE ON frq_rubric_choices
 BEGIN SELECT RAISE(ABORT,'Rubric choices are retained'); END;
