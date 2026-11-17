-- Instructor policy, 2026-09-20: resolved grading teams with all checks passing
-- finalize automatically, without a benchmark prerequisite. These append-only
-- receipts belong to the existing TERM grading plan/response. Legacy benchmark
-- permissions and their evidence remain unchanged; never fabricate a benchmark.
CREATE TABLE frq_team_acceptances (
 id TEXT PRIMARY KEY, result_id TEXT NOT NULL UNIQUE REFERENCES frq_results(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id), question_id TEXT NOT NULL REFERENCES frq_questions(id),
 scope_sha256 TEXT NOT NULL, scope_json TEXT NOT NULL, policy TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_team_acceptances_scope ON frq_team_acceptances(assignment_id,question_id,scope_sha256);
CREATE TRIGGER frq_team_acceptance_scope BEFORE INSERT ON frq_team_acceptances
 WHEN NOT EXISTS(SELECT 1 FROM frq_results x JOIN frq_responses r ON r.id=x.response_id
   JOIN frq_tasks t ON t.id=x.task_id JOIN frq_jobs j ON j.id=t.job_id
   WHERE x.id=NEW.result_id AND r.id=NEW.response_id AND r.assignment_id=NEW.assignment_id
   AND r.question_id=NEW.question_id AND r.current_result_id=x.id AND r.current_review_id IS NULL
   AND x.state='proposed' AND j.shadow=0
   AND json_extract(j.profile_json,'$.grading_workflow')='grading-teams-v1'
   AND json_extract(x.signals_json,'$.committee.status')='resolved'
   AND json_array_length(x.signals_json,'$.risk.blocks')=0
   AND json_extract(x.signals_json,'$.risk.individual')=0)
 OR EXISTS(SELECT 1 FROM frq_acceptances WHERE result_id=NEW.result_id)
 BEGIN SELECT RAISE(ABORT,'Only a current unreviewed resolved team result with passing checks can finalize'); END;
CREATE TRIGGER frq_team_acceptance_immutable BEFORE UPDATE ON frq_team_acceptances BEGIN SELECT RAISE(ABORT,'Team acceptances are immutable'); END;
CREATE TRIGGER frq_team_acceptance_retained BEFORE DELETE ON frq_team_acceptances BEGIN SELECT RAISE(ABORT,'Team acceptances are retained'); END;
CREATE TABLE frq_team_acceptance_reviews (
 id TEXT PRIMARY KEY, acceptance_id TEXT NOT NULL UNIQUE REFERENCES frq_team_acceptances(id),
 review_id TEXT NOT NULL REFERENCES frq_reviews(id), outcome TEXT NOT NULL CHECK(outcome IN ('confirmed','overturned')),
 blind INTEGER NOT NULL CHECK(blind=0), missed_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TRIGGER frq_team_acceptance_review_immutable BEFORE UPDATE ON frq_team_acceptance_reviews BEGIN SELECT RAISE(ABORT,'Team acceptance reviews are immutable'); END;
CREATE TRIGGER frq_team_acceptance_review_retained BEFORE DELETE ON frq_team_acceptance_reviews BEGIN SELECT RAISE(ABORT,'Team acceptance reviews are retained'); END;
CREATE TABLE frq_team_acceptance_events (
 id INTEGER PRIMARY KEY, acceptance_id TEXT NOT NULL REFERENCES frq_team_acceptances(id),
 kind TEXT NOT NULL CHECK(kind='suspended'), reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_team_acceptance_events_acceptance ON frq_team_acceptance_events(acceptance_id);
CREATE TRIGGER frq_team_acceptance_event_immutable BEFORE UPDATE ON frq_team_acceptance_events BEGIN SELECT RAISE(ABORT,'Team acceptance events are immutable'); END;
CREATE TRIGGER frq_team_acceptance_event_retained BEFORE DELETE ON frq_team_acceptance_events BEGIN SELECT RAISE(ABORT,'Team acceptance events are retained'); END;
-- Shared read projections only; writes always name the retained receipt's table.
CREATE VIEW frq_all_acceptances AS
 SELECT a.*, 'benchmark' AS basis,
   CASE WHEN a.kind='trial' THEN 1 ELSE COALESCE((SELECT p.kind='enabled' FROM frq_acceptance_permission_events p
     WHERE p.permission_id=a.permission_id ORDER BY p.rowid DESC LIMIT 1),0) END AS active
 FROM frq_acceptances a
 UNION ALL
 SELECT a.id,a.result_id,a.response_id,a.assignment_id,a.question_id,'automatic',a.scope_sha256,a.scope_json,a.policy,
   0,0,NULL,a.created_at,'team_consensus',NOT EXISTS(SELECT 1 FROM frq_team_acceptance_events e WHERE e.acceptance_id=a.id)
 FROM frq_team_acceptances a;
CREATE VIEW frq_all_acceptance_reviews AS
 SELECT * FROM frq_acceptance_reviews UNION ALL SELECT * FROM frq_team_acceptance_reviews;
