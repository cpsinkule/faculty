-- Four states that are never conflated: an AI proposal (frq_results), an AI TRIAL acceptance (a test of the
-- risk policy: never final, never exported), an AI automatic acceptance (final without a look, only under
-- a retained report), and an instructor-confirmed score (frq_reviews, as always). Term workflow records of
-- the existing grading plan; every row is append-only.
CREATE TABLE frq_acceptance_permissions (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id), question_id TEXT NOT NULL REFERENCES frq_questions(id),
 scope_sha256 TEXT NOT NULL, report_id TEXT NOT NULL, error_limit REAL NOT NULL, audit_share REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_acceptance_permissions_scope ON frq_acceptance_permissions(scope_sha256);
CREATE TRIGGER frq_acceptance_permission_immutable BEFORE UPDATE ON frq_acceptance_permissions BEGIN SELECT RAISE(ABORT,'Acceptance permissions are immutable'); END;
CREATE TRIGGER frq_acceptance_permission_retained BEFORE DELETE ON frq_acceptance_permissions BEGIN SELECT RAISE(ABORT,'Acceptance permissions are retained'); END;
-- A permission is enabled, suspended (a failed audit or canary) or revoked by its LATEST event.
CREATE TABLE frq_acceptance_permission_events (
 id INTEGER PRIMARY KEY, permission_id TEXT NOT NULL REFERENCES frq_acceptance_permissions(id),
 kind TEXT NOT NULL CHECK(kind IN ('enabled','suspended','revoked')), reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_acceptance_permission_event_immutable BEFORE UPDATE ON frq_acceptance_permission_events BEGIN SELECT RAISE(ABORT,'Permission events are immutable'); END;
CREATE TRIGGER frq_acceptance_permission_event_retained BEFORE DELETE ON frq_acceptance_permission_events BEGIN SELECT RAISE(ABORT,'Permission events are retained'); END;
-- The retained evidence a permission rests on: the instructor's review of what the policy accepted, for one exact
-- scope (question revision, rubric, exam version, prompts, models, tier, risk policy).
CREATE TABLE frq_benchmark_reports (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id), question_id TEXT NOT NULL REFERENCES frq_questions(id),
 scope_sha256 TEXT NOT NULL, scope_json TEXT NOT NULL, report_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_benchmark_report_immutable BEFORE UPDATE ON frq_benchmark_reports BEGIN SELECT RAISE(ABORT,'Benchmark reports are immutable'); END;
CREATE TRIGGER frq_benchmark_report_retained BEFORE DELETE ON frq_benchmark_reports BEGIN SELECT RAISE(ABORT,'Benchmark reports are retained'); END;
-- One acceptance per result. `blind`: the AI's decision stays hidden until the instructor has graded.
-- `audit`: an automatically accepted response that always comes to the instructor.
CREATE TABLE frq_acceptances (
 id TEXT PRIMARY KEY, result_id TEXT NOT NULL, response_id TEXT NOT NULL REFERENCES frq_responses(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id), question_id TEXT NOT NULL REFERENCES frq_questions(id),
 kind TEXT NOT NULL CHECK(kind IN ('trial','automatic')), scope_sha256 TEXT NOT NULL, scope_json TEXT NOT NULL, policy TEXT NOT NULL,
 blind INTEGER NOT NULL CHECK(blind IN (0,1)), audit INTEGER NOT NULL CHECK(audit IN (0,1)),
 permission_id TEXT REFERENCES frq_acceptance_permissions(id), created_at TEXT NOT NULL,
 CHECK(kind='trial' OR permission_id IS NOT NULL)
);
CREATE UNIQUE INDEX frq_acceptances_result ON frq_acceptances(result_id);
CREATE INDEX frq_acceptances_question ON frq_acceptances(assignment_id,question_id,scope_sha256);
CREATE TRIGGER frq_acceptance_result_scope BEFORE INSERT ON frq_acceptances
 WHEN NOT EXISTS(SELECT 1 FROM frq_results x WHERE x.id=NEW.result_id AND x.response_id=NEW.response_id AND x.state='proposed')
 BEGIN SELECT RAISE(ABORT,'Only a current proposal of this response can be accepted'); END;
CREATE TRIGGER frq_acceptance_immutable BEFORE UPDATE ON frq_acceptances BEGIN SELECT RAISE(ABORT,'Acceptances are immutable'); END;
CREATE TRIGGER frq_acceptance_retained BEFORE DELETE ON frq_acceptances BEGIN SELECT RAISE(ABORT,'Acceptances are retained'); END;
-- The instructor looked: confirmed or overturned. The corrected rubric lines are in the ordinary review it
-- references; `missed_json` lists the signals that came closest to catching an overturned one.
CREATE TABLE frq_acceptance_reviews (
 id TEXT PRIMARY KEY, acceptance_id TEXT NOT NULL UNIQUE REFERENCES frq_acceptances(id), review_id TEXT NOT NULL REFERENCES frq_reviews(id),
 outcome TEXT NOT NULL CHECK(outcome IN ('confirmed','overturned')), blind INTEGER NOT NULL CHECK(blind IN (0,1)),
 missed_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TRIGGER frq_acceptance_review_immutable BEFORE UPDATE ON frq_acceptance_reviews BEGIN SELECT RAISE(ABORT,'Acceptance reviews are immutable'); END;
CREATE TRIGGER frq_acceptance_review_retained BEFORE DELETE ON frq_acceptance_reviews BEGIN SELECT RAISE(ABORT,'Acceptance reviews are retained'); END;
-- What an instructor correction teaches: kept beside the review, never instead of the AI's own output.
CREATE TABLE frq_review_feedback (
 review_id TEXT PRIMARY KEY REFERENCES frq_reviews(id), reasoning_wrong INTEGER NOT NULL CHECK(reasoning_wrong IN (0,1)),
 override_reason TEXT NOT NULL, transcript_corrected INTEGER NOT NULL CHECK(transcript_corrected IN (0,1)),
 lines_changed_json TEXT NOT NULL, provenance_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_review_feedback_immutable BEFORE UPDATE ON frq_review_feedback BEGIN SELECT RAISE(ABORT,'Review feedback is immutable'); END;
CREATE TRIGGER frq_review_feedback_retained BEFORE DELETE ON frq_review_feedback BEGIN SELECT RAISE(ABORT,'Review feedback is retained'); END;
