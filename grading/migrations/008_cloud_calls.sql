-- Cloud work as durable Faculty records. Faculty owns students and grades; a cloud provider is a
-- disposable execution layer. Everything here is term workflow metadata of the existing grading plan.
--
-- A job's compute tier. `mode` keeps its original values (the catalog CHECK cannot change): a tier
-- job is stored as mode 'nebius', the original name of the cloud mode, with its tier beside it.
ALTER TABLE frq_jobs ADD COLUMN tier TEXT NOT NULL DEFAULT '' CHECK(tier IN ('','cloud_standard','cloud_high_assurance'));
ALTER TABLE frq_jobs ADD COLUMN cost REAL NOT NULL DEFAULT 0;
ALTER TABLE frq_jobs ADD COLUMN notice TEXT NOT NULL DEFAULT '';
-- Which worker lane currently runs a task: '' the single local GPU worker, 'cloud' the parallel
-- cloud dispatcher. Each lane recovers only its own interrupted work.
ALTER TABLE frq_tasks ADD COLUMN lane TEXT NOT NULL DEFAULT '' CHECK(lane IN ('','cloud'));
ALTER TABLE frq_tasks ADD COLUMN not_before TEXT NOT NULL DEFAULT '';
-- One row per request that leaves Faculty, written BEFORE it leaves. The retry policy reads these:
-- a confirmed failure (the provider's own status is in confirmation_json) may be retried once that
-- row is recorded; an unknown outcome is recovered by provider_call_id where one exists, otherwise
-- resent at most `unknown_resends` times; a late result of a superseded call is kept and never applied.
CREATE TABLE frq_cloud_calls (
 id TEXT PRIMARY KEY, task_id TEXT REFERENCES frq_tasks(id), canary_item_id TEXT, probe_id TEXT,
 job_id TEXT REFERENCES frq_jobs(id), step TEXT NOT NULL, task_attempt INTEGER NOT NULL DEFAULT 0,
 role TEXT NOT NULL, tier TEXT NOT NULL, endpoint TEXT NOT NULL, provider TEXT NOT NULL,
 requested_model TEXT NOT NULL, actual_model TEXT, lineage TEXT NOT NULL DEFAULT '', upstream TEXT,
 resource_profile TEXT NOT NULL DEFAULT '', provider_call_id TEXT, request_sha256 TEXT NOT NULL,
 prompt TEXT NOT NULL DEFAULT '', prompt_sha256 TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL CHECK(state IN ('submitted','completed','failed','unknown','superseded','cancelled')),
 confirmation_json TEXT, error TEXT NOT NULL DEFAULT '', receipt_json TEXT,
 usage_json TEXT NOT NULL DEFAULT '{}', cost REAL, cost_basis TEXT NOT NULL DEFAULT '',
 submitted_at TEXT NOT NULL, finished_at TEXT, seconds REAL
);
CREATE INDEX frq_cloud_calls_task ON frq_cloud_calls(task_id,step);
CREATE INDEX frq_cloud_calls_state ON frq_cloud_calls(state,submitted_at);
CREATE INDEX frq_cloud_calls_job ON frq_cloud_calls(job_id);
-- A settled call never changes again, and no call is ever deleted: they are the audit of what left.
-- An unknown outcome may still be settled through its durable ID. A completed call whose component
-- already had a result becomes superseded, and a superseded call may receive its late result ONCE:
-- a receipt that is never applied.
CREATE TRIGGER frq_cloud_call_settled BEFORE UPDATE ON frq_cloud_calls
 WHEN OLD.state IN ('failed','cancelled') OR (OLD.state='completed' AND NEW.state!='superseded')
 OR (OLD.state='superseded' AND (NEW.state!='superseded' OR OLD.receipt_json IS NOT NULL))
 OR (OLD.state='unknown' AND NEW.state NOT IN ('completed','superseded','failed','cancelled'))
 BEGIN SELECT RAISE(ABORT,'A settled cloud call is immutable'); END;
CREATE TRIGGER frq_cloud_call_identity BEFORE UPDATE OF id,task_id,canary_item_id,probe_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,request_sha256,submitted_at ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud call inputs are immutable'); END;
CREATE TRIGGER frq_cloud_call_retained BEFORE DELETE ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud calls are retained'); END;
-- What an endpoint was seen to do: a small live probe, never an assumption from a catalog page.
CREATE TABLE frq_endpoint_probes (
 id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, endpoint_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 result_json TEXT, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX frq_endpoint_probes_endpoint ON frq_endpoint_probes(endpoint,created_at);
-- The canary: instructor-approved responses regraded on an endpoint before a batch, because a host can
-- change how it serves a model without notice. Items reference the approved review they are compared
-- with; a canary never becomes a response's current result.
CREATE TABLE frq_canary_runs (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES frq_jobs(id), endpoint TEXT NOT NULL, scope_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('skipped','pending','passed','failed')),
 baseline REAL, agreement REAL, detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(job_id,endpoint)
);
CREATE TABLE frq_canary_items (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES frq_canary_runs(id), response_id TEXT NOT NULL REFERENCES frq_responses(id),
 review_id TEXT NOT NULL REFERENCES frq_reviews(id), status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 expected_score REAL, score REAL, agrees INTEGER, error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 UNIQUE(run_id,response_id)
);
CREATE INDEX frq_canary_items_queue ON frq_canary_items(status,updated_at);
-- The agreement an endpoint showed the first time it graded a scope (exam version, question, rubric,
-- prompts, model). Later canaries are compared with it; it is recorded once and never moved.
CREATE TABLE frq_canary_baselines (
 scope_sha256 TEXT NOT NULL, endpoint TEXT NOT NULL, agreement REAL NOT NULL, responses INTEGER NOT NULL,
 run_id TEXT NOT NULL REFERENCES frq_canary_runs(id), created_at TEXT NOT NULL, PRIMARY KEY(scope_sha256,endpoint)
);
CREATE TRIGGER frq_canary_baseline_immutable BEFORE UPDATE ON frq_canary_baselines
 BEGIN SELECT RAISE(ABORT,'Canary baselines are immutable'); END;
