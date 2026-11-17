-- A suspension made by a quality check (canary) that was CANCELLED with its batch measured nothing (requirement, 2026-09-28: cancelling
-- Quiz 2's batch during its canary suspended 1,018 current automatic grades; asked, they chose to reinstate them). Suspensions stay
-- retained and immutable; a reinstatement is its own append-only row naming exactly the ONE suspension it undoes, owned like the
-- acceptance by the TERM grading plan's response. An automatic grade is active while every suspension of it has a reinstatement.
CREATE TABLE frq_team_acceptance_reinstatements (
 id TEXT PRIMARY KEY,
 event_id INTEGER NOT NULL UNIQUE REFERENCES frq_team_acceptance_events(id),
 acceptance_id TEXT NOT NULL REFERENCES frq_team_acceptances(id),
 reason TEXT NOT NULL, request_id TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_team_acceptance_reinstatements_acceptance ON frq_team_acceptance_reinstatements(acceptance_id);
CREATE TRIGGER frq_team_acceptance_reinstatement_names_its_suspension BEFORE INSERT ON frq_team_acceptance_reinstatements
 WHEN NOT EXISTS(SELECT 1 FROM frq_team_acceptance_events e WHERE e.id=NEW.event_id AND e.acceptance_id=NEW.acceptance_id AND e.kind='suspended')
 BEGIN SELECT RAISE(ABORT,'A reinstatement names one suspension of its own acceptance'); END;
CREATE TRIGGER frq_team_acceptance_reinstatement_immutable BEFORE UPDATE ON frq_team_acceptance_reinstatements BEGIN SELECT RAISE(ABORT,'Acceptance reinstatements are immutable'); END;
CREATE TRIGGER frq_team_acceptance_reinstatement_retained BEFORE DELETE ON frq_team_acceptance_reinstatements BEGIN SELECT RAISE(ABORT,'Acceptance reinstatements are retained'); END;

DROP VIEW frq_all_acceptances;
CREATE VIEW frq_all_acceptances AS
 SELECT a.*, 'benchmark' AS basis,
   CASE WHEN a.kind='trial' THEN 1 ELSE COALESCE((SELECT p.kind='enabled' FROM frq_acceptance_permission_events p
     WHERE p.permission_id=a.permission_id ORDER BY p.rowid DESC LIMIT 1),0) END AS active
 FROM frq_acceptances a
 UNION ALL
 SELECT a.id,a.result_id,a.response_id,a.assignment_id,a.question_id,'automatic',a.scope_sha256,a.scope_json,a.policy,
   0,0,NULL,a.created_at,'team_consensus',NOT EXISTS(SELECT 1 FROM frq_team_acceptance_events e WHERE e.acceptance_id=a.id
     AND NOT EXISTS(SELECT 1 FROM frq_team_acceptance_reinstatements x WHERE x.event_id=e.id))
 FROM frq_team_acceptances a
 UNION ALL
 SELECT a.id,a.result_id,a.response_id,a.assignment_id,a.question_id,'automatic',a.scope_sha256,a.scope_json,a.policy,
   0,0,NULL,a.created_at,'one_shot',NOT EXISTS(SELECT 1 FROM frq_one_shot_acceptance_events e WHERE e.acceptance_id=a.id)
 FROM frq_one_shot_acceptances a
 UNION ALL
 SELECT a.id,a.result_id,a.response_id,a.assignment_id,a.question_id,'automatic',a.scope_sha256,a.scope_json,a.policy,
   0,0,NULL,a.created_at,'tag_team',NOT EXISTS(SELECT 1 FROM frq_tag_team_acceptance_events e WHERE e.acceptance_id=a.id)
 FROM frq_tag_team_acceptances a;
