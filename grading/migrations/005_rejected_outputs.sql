-- Private failed-output receipts: never include them in health or ordinary logs.
CREATE TABLE frq_rejected_outputs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES frq_tasks(id),
 stage TEXT NOT NULL, attempt INTEGER NOT NULL, raw_json TEXT NOT NULL,
 provider_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_rejected_output_immutable BEFORE UPDATE ON frq_rejected_outputs
 BEGIN SELECT RAISE(ABORT,'Rejected inference outputs are immutable'); END;
CREATE TRIGGER frq_rejected_output_retained BEFORE DELETE ON frq_rejected_outputs
 BEGIN SELECT RAISE(ABORT,'Rejected inference outputs are retained'); END;
