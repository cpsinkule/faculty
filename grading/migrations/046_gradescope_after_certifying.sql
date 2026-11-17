-- Gradescope after certifying (requirement, 2026-10-12). One row per certification that asks the Gradescope sender to bring Gradescope up to date by itself:
-- 'certified' (the instructor's Certify with "Send to Gradescope and publish the grades" ticked: make what is missing, send the
-- bubble sheets and the written papers and grades, publish) or 'late' (a late paper Faculty certified and sent: publish the version
-- it went to where its set is already published). Written in the certification's own transaction, so nothing is lost between
-- certifying and Gradescope. Owned by the TERM grading plan (frq_assignments.id) and its certification.
-- `plan_json`: what the certification asked (each part's decision; a late run's rows). `step` is the next thing to look at; every
-- step records what it did in `outcome_json` (the creation, send or publication it recorded as an ordinary one with actor
-- 'automatic', or why not: section numbers and reasons only, no name or UIN) and moves on. A row is a state machine: pending,
-- working (claimed for one look), then pending again or done; a finished row never changes; nothing is deleted.
CREATE TABLE frq_gradescope_automatic_runs (
 id TEXT PRIMARY KEY,
 certification_id TEXT NOT NULL UNIQUE REFERENCES frq_certifications(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 kind TEXT NOT NULL CHECK(kind IN ('certified','late')),
 plan_json TEXT NOT NULL,
 step TEXT NOT NULL CHECK(step IN ('create','sheets','written','publish','done')),
 status TEXT NOT NULL CHECK(status IN ('pending','working','done')),
 outcome_json TEXT NOT NULL DEFAULT '{}',
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 not_before TEXT,
 error TEXT NOT NULL DEFAULT '' CHECK(length(error) <= 2000),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 CHECK((status = 'done') = (step = 'done'))
);
CREATE INDEX frq_gradescope_automatic_runs_plan ON frq_gradescope_automatic_runs(assignment_id, created_at);
CREATE INDEX frq_gradescope_automatic_runs_open ON frq_gradescope_automatic_runs(status, not_before) WHERE status IN ('pending','working');
CREATE TRIGGER frq_gradescope_automatic_run_identity BEFORE UPDATE OF id,certification_id,assignment_id,kind,plan_json,actor,created_at
 ON frq_gradescope_automatic_runs
BEGIN SELECT RAISE(ABORT,'An automatic Gradescope run never changes what it was asked'); END;
CREATE TRIGGER frq_gradescope_automatic_run_settled BEFORE UPDATE ON frq_gradescope_automatic_runs
WHEN OLD.status = 'done'
BEGIN SELECT RAISE(ABORT,'A finished automatic Gradescope run never changes'); END;
CREATE TRIGGER frq_gradescope_automatic_run_retained BEFORE DELETE ON frq_gradescope_automatic_runs
BEGIN SELECT RAISE(ABORT,'Automatic Gradescope runs are retained'); END;
