-- Routing belongs to the existing TERM-owned grading batch, one decision per
-- question revision. Its task holds the durable cloud call and immutable step.
CREATE TABLE frq_jev_routes (
 job_id TEXT NOT NULL REFERENCES frq_jobs(id),
 question_revision_id TEXT NOT NULL REFERENCES frq_question_revisions(id),
 task_id TEXT NOT NULL UNIQUE REFERENCES frq_tasks(id),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','cancelled')),
 not_before TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,question_revision_id)
);
CREATE INDEX frq_jev_routes_queue ON frq_jev_routes(status,not_before);
CREATE TRIGGER frq_jev_route_scope BEFORE INSERT ON frq_jev_routes
 WHEN NOT EXISTS(SELECT 1 FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id
   WHERE t.id=NEW.task_id AND t.job_id=NEW.job_id AND t.question_revision_id=NEW.question_revision_id
   AND json_extract(j.profile_json,'$.jev_routing_rule')='jev-routing-v1'
   AND json_extract(j.profile_json,'$.grading_workflow')='one-shot-v1')
 BEGIN SELECT RAISE(ABORT,'Routing must reference its own batch and question'); END;
CREATE TRIGGER frq_jev_route_identity BEFORE UPDATE ON frq_jev_routes
 WHEN NEW.job_id!=OLD.job_id OR NEW.question_revision_id!=OLD.question_revision_id OR NEW.task_id!=OLD.task_id
 OR OLD.status='complete'
 BEGIN SELECT RAISE(ABORT,'Completed routing and route identities are immutable'); END;
CREATE TRIGGER frq_jev_route_retained BEFORE DELETE ON frq_jev_routes
 BEGIN SELECT RAISE(ABORT,'Routing history is retained'); END;
