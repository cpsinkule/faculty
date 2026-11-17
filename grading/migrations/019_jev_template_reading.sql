-- New term-owned batches read their immutable course template crops with Mathpix.
-- Existing v1 jobs retain their frozen saved-transcription behavior and receipts.
DROP TRIGGER frq_jev_route_scope;
CREATE TRIGGER frq_jev_route_scope BEFORE INSERT ON frq_jev_routes
 WHEN NOT EXISTS(SELECT 1 FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id
   WHERE t.id=NEW.task_id AND t.job_id=NEW.job_id AND t.question_revision_id=NEW.question_revision_id
   AND json_extract(j.profile_json,'$.jev_routing_rule') IN ('jev-routing-v1','jev-routing-v2')
   AND json_extract(j.profile_json,'$.grading_workflow')='one-shot-v1')
 BEGIN SELECT RAISE(ABORT,'Routing must reference its own batch and question'); END;
