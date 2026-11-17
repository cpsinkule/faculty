-- Ownership: question/solution/rubric revisions are course-wide assignment-version
-- resources. Plans, runs, responses and exports belong to an existing term exam.
-- Images remain immutable artifacts of the source Inbox owner, never SQLite blobs.
CREATE TABLE frq_assignments (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES exam_events(id),
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE frq_questions (
 id TEXT PRIMARY KEY, assignment_version_id TEXT NOT NULL REFERENCES assignment_versions(id),
 question_key TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(assignment_version_id,question_key)
);
CREATE TABLE frq_question_revisions (
 id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES frq_questions(id),
 revision INTEGER NOT NULL, content_json TEXT NOT NULL, sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(question_id,revision)
);
CREATE TABLE frq_rubric_revisions (
 id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES frq_questions(id),
 revision INTEGER NOT NULL, source_text TEXT NOT NULL, source_artifact_id TEXT REFERENCES inbox_artifacts(id),
 rules_json TEXT NOT NULL, sha256 TEXT NOT NULL, semantics_sha256 TEXT NOT NULL,
 compiler_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(question_id,revision)
);
CREATE TABLE frq_rubric_approvals (
 rubric_id TEXT PRIMARY KEY REFERENCES frq_rubric_revisions(id),
 sha256 TEXT NOT NULL, actor TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE frq_assignment_questions (
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id),
 question_revision_id TEXT NOT NULL REFERENCES frq_question_revisions(id),
 rubric_id TEXT REFERENCES frq_rubric_revisions(id), version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(assignment_id,question_id)
);
CREATE TABLE frq_responses (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id), section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 current_result_id TEXT, current_review_id TEXT, created_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id),
 UNIQUE(assignment_id,question_id,section_id,enrollment_id)
);
CREATE TABLE frq_jobs (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('local','hybrid','nebius')),
 operation TEXT NOT NULL CHECK(operation IN ('recognize','grade','rescore')),
 shadow INTEGER NOT NULL CHECK(shadow IN (0,1)),
 status TEXT NOT NULL CHECK(status IN ('pending','running','review_ready','failed','cancelled')),
 profile_json TEXT NOT NULL, estimated_cost REAL, runtime_seconds REAL NOT NULL DEFAULT 0,
 usage_json TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE frq_tasks (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES frq_jobs(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 question_revision_id TEXT NOT NULL REFERENCES frq_question_revisions(id),
 rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 source_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 reuse_result_id TEXT, human_transcript_json TEXT,
 stage TEXT NOT NULL CHECK(stage IN ('extracting','recognizing','reconciling','grading','scoring','complete')),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','cancelled')),
 attempt INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id,response_id)
);
CREATE TABLE frq_steps (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES frq_tasks(id),
 stage TEXT NOT NULL, attempt INTEGER NOT NULL, input_sha256 TEXT NOT NULL,
 output_json TEXT NOT NULL, output_sha256 TEXT NOT NULL, provider_json TEXT NOT NULL,
 runtime_seconds REAL NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,stage,attempt)
);
CREATE TABLE frq_results (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES frq_tasks(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 transcript_json TEXT NOT NULL, applications_json TEXT NOT NULL, interpretation_json TEXT NOT NULL,
 score_json TEXT NOT NULL, signals_json TEXT NOT NULL, reasons_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('proposed','ocr_review','grading_review','both')),
 created_at TEXT NOT NULL
);
CREATE TABLE frq_reviews (
 id TEXT PRIMARY KEY, response_id TEXT NOT NULL REFERENCES frq_responses(id),
 result_id TEXT REFERENCES frq_results(id), rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 source_sha256 TEXT NOT NULL, transcript_json TEXT NOT NULL, applications_json TEXT NOT NULL,
 score_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','approved','manual','unreadable','rejected')),
 comment TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE frq_exports (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 snapshot_json TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE frq_events (
 id INTEGER PRIMARY KEY, assignment_id TEXT REFERENCES frq_assignments(id),
 kind TEXT NOT NULL, detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE frq_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, settings_json TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX frq_tasks_queue ON frq_tasks(status,updated_at,created_at);
CREATE INDEX frq_results_response ON frq_results(response_id,created_at);
CREATE INDEX frq_reviews_response ON frq_reviews(response_id,created_at);
CREATE TRIGGER frq_pin_scope BEFORE INSERT ON frq_assignment_questions
 WHEN NOT EXISTS(SELECT 1 FROM frq_assignments p JOIN exam_events e ON e.id=p.event_id
 JOIN assignment_versions v ON v.assignment_id=e.assignment_id JOIN frq_questions q ON q.assignment_version_id=v.id
 WHERE p.id=NEW.assignment_id AND q.id=NEW.question_id)
 BEGIN SELECT RAISE(ABORT,'FRQ question belongs to another assignment'); END;
CREATE TRIGGER frq_question_revision_immutable BEFORE UPDATE ON frq_question_revisions BEGIN SELECT RAISE(ABORT,'Question revisions are immutable'); END;
CREATE TRIGGER frq_question_revision_retained BEFORE DELETE ON frq_question_revisions BEGIN SELECT RAISE(ABORT,'Question revisions are retained'); END;
CREATE TRIGGER frq_rubric_revision_immutable BEFORE UPDATE ON frq_rubric_revisions BEGIN SELECT RAISE(ABORT,'Rubric revisions are immutable'); END;
CREATE TRIGGER frq_rubric_revision_retained BEFORE DELETE ON frq_rubric_revisions BEGIN SELECT RAISE(ABORT,'Rubric revisions are retained'); END;
CREATE TRIGGER frq_approval_immutable BEFORE UPDATE ON frq_rubric_approvals BEGIN SELECT RAISE(ABORT,'Approvals are immutable'); END;
CREATE TRIGGER frq_approval_retained BEFORE DELETE ON frq_rubric_approvals BEGIN SELECT RAISE(ABORT,'Approvals are retained'); END;
CREATE TRIGGER frq_step_immutable BEFORE UPDATE ON frq_steps BEGIN SELECT RAISE(ABORT,'Inference receipts are immutable'); END;
CREATE TRIGGER frq_step_retained BEFORE DELETE ON frq_steps BEGIN SELECT RAISE(ABORT,'Inference receipts are retained'); END;
CREATE TRIGGER frq_result_immutable BEFORE UPDATE ON frq_results BEGIN SELECT RAISE(ABORT,'Proposals are immutable'); END;
CREATE TRIGGER frq_result_retained BEFORE DELETE ON frq_results BEGIN SELECT RAISE(ABORT,'Proposals are retained'); END;
CREATE TRIGGER frq_review_immutable BEFORE UPDATE ON frq_reviews BEGIN SELECT RAISE(ABORT,'Human decisions are immutable'); END;
CREATE TRIGGER frq_review_retained BEFORE DELETE ON frq_reviews BEGIN SELECT RAISE(ABORT,'Human decisions are retained'); END;
CREATE TRIGGER frq_export_immutable BEFORE UPDATE ON frq_exports BEGIN SELECT RAISE(ABORT,'Exports are immutable'); END;
CREATE TRIGGER frq_export_retained BEFORE DELETE ON frq_exports BEGIN SELECT RAISE(ABORT,'Exports are retained'); END;
CREATE TRIGGER frq_event_immutable BEFORE UPDATE ON frq_events BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
CREATE TRIGGER frq_event_retained BEFORE DELETE ON frq_events BEGIN SELECT RAISE(ABORT,'Audit events are retained'); END;
