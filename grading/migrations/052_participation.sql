-- GRADED FOR PARTICIPATION (requirement, 2026-11-01). Whether ONE assignment's grades count as full credit for everyone
-- with a paper, its rubric lines kept as feedback. Owned by the TERM grading plan (frq_assignments.id), like 049's choices; no
-- student is named. Append-only: the latest row of a plan stands; no row is "not graded for participation". Nothing here changes a
-- rubric, a review or a grade: what counts a final grade reads it (grading/participation.py).
CREATE TABLE frq_participation (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX frq_participation_plan ON frq_participation(assignment_id, created_at);
CREATE TRIGGER frq_participation_immutable BEFORE UPDATE ON frq_participation
 BEGIN SELECT RAISE(ABORT,'A participation setting is immutable: a change is a new row'); END;
CREATE TRIGGER frq_participation_retained BEFORE DELETE ON frq_participation
 BEGIN SELECT RAISE(ABORT,'Participation settings are retained'); END;
