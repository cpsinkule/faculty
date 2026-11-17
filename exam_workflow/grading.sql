-- Rubrics belong to course-wide assignments and immutable document versions.
-- An exam explicitly pins a rubric; editing one never changes another term.
CREATE TABLE IF NOT EXISTS exam_rubric_versions (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
 assignment_version_id TEXT NOT NULL REFERENCES assignment_versions(id),
 revision INTEGER NOT NULL, questions_json TEXT NOT NULL, sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(assignment_id,revision)
);
CREATE TABLE IF NOT EXISTS exam_grading_rubrics (
 event_id TEXT NOT NULL REFERENCES exam_events(id),
 assignment_version_id TEXT NOT NULL REFERENCES assignment_versions(id),
 rubric_id TEXT NOT NULL REFERENCES exam_rubric_versions(id), version INTEGER NOT NULL,
 PRIMARY KEY(event_id,assignment_version_id)
);
-- Grades belong to a term exam and an exact permanent section membership.
CREATE TABLE IF NOT EXISTS exam_grade_states (
 event_id TEXT NOT NULL REFERENCES exam_events(id), section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 finalized_id TEXT, released_id TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(event_id,section_id,enrollment_id),
 FOREIGN KEY(event_id,section_id,enrollment_id) REFERENCES exam_participants(event_id,section_id,enrollment_id),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE TABLE IF NOT EXISTS exam_mark_revisions (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 question_id TEXT NOT NULL, revision INTEGER NOT NULL,
 rubric_id TEXT NOT NULL REFERENCES exam_rubric_versions(id),
 assembly_revision_id TEXT NOT NULL REFERENCES assembly_revisions(id), assembly_sha256 TEXT NOT NULL,
 score REAL NOT NULL CHECK(score>=0), feedback TEXT NOT NULL,
 criterion_ids_json TEXT NOT NULL, missing_acknowledged INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,section_id,enrollment_id) REFERENCES exam_grade_states(event_id,section_id,enrollment_id),
 UNIQUE(event_id,section_id,enrollment_id,question_id,revision)
);
CREATE TABLE IF NOT EXISTS exam_grade_finalizations (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 rubric_id TEXT NOT NULL REFERENCES exam_rubric_versions(id),
 assembly_revision_id TEXT NOT NULL REFERENCES assembly_revisions(id), assembly_sha256 TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,section_id,enrollment_id) REFERENCES exam_grade_states(event_id,section_id,enrollment_id)
);
CREATE TABLE IF NOT EXISTS exam_grade_releases (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 finalization_id TEXT NOT NULL REFERENCES exam_grade_finalizations(id),
 request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,section_id,enrollment_id) REFERENCES exam_grade_states(event_id,section_id,enrollment_id)
);
CREATE TABLE IF NOT EXISTS exam_grading_events (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES exam_events(id),
 section_id TEXT, enrollment_id TEXT, kind TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS exam_mark_lookup ON exam_mark_revisions(event_id,section_id,enrollment_id,question_id,revision DESC);
CREATE TRIGGER IF NOT EXISTS exam_rubric_document_scope BEFORE INSERT ON exam_rubric_versions
 WHEN (SELECT assignment_id FROM assignment_versions WHERE id=NEW.assignment_version_id) IS NOT NEW.assignment_id
 BEGIN SELECT RAISE(ABORT,'Rubric document version belongs to another assignment'); END;
CREATE TRIGGER IF NOT EXISTS exam_grading_rubric_scope_insert BEFORE INSERT ON exam_grading_rubrics
 WHEN NOT EXISTS (SELECT 1 FROM exam_rubric_versions r JOIN exam_events e ON e.id=NEW.event_id
 WHERE r.id=NEW.rubric_id AND r.assignment_id=e.assignment_id AND r.assignment_version_id=NEW.assignment_version_id)
 BEGIN SELECT RAISE(ABORT,'Grading rubric belongs to another assignment or document version'); END;
CREATE TRIGGER IF NOT EXISTS exam_grading_rubric_scope_update BEFORE UPDATE ON exam_grading_rubrics
 WHEN NEW.event_id!=OLD.event_id OR NEW.assignment_version_id!=OLD.assignment_version_id OR NOT EXISTS
 (SELECT 1 FROM exam_rubric_versions r JOIN exam_events e ON e.id=NEW.event_id
 WHERE r.id=NEW.rubric_id AND r.assignment_id=e.assignment_id AND r.assignment_version_id=NEW.assignment_version_id)
 BEGIN SELECT RAISE(ABORT,'Grading rubric belongs to another assignment or document version'); END;
CREATE TRIGGER IF NOT EXISTS exam_rubric_immutable BEFORE UPDATE ON exam_rubric_versions BEGIN SELECT RAISE(ABORT,'Rubric revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_rubric_retained BEFORE DELETE ON exam_rubric_versions BEGIN SELECT RAISE(ABORT,'Rubric revisions are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_mark_immutable BEFORE UPDATE ON exam_mark_revisions BEGIN SELECT RAISE(ABORT,'Mark revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_mark_retained BEFORE DELETE ON exam_mark_revisions BEGIN SELECT RAISE(ABORT,'Mark revisions are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_finalization_immutable BEFORE UPDATE ON exam_grade_finalizations BEGIN SELECT RAISE(ABORT,'Finalizations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_finalization_retained BEFORE DELETE ON exam_grade_finalizations BEGIN SELECT RAISE(ABORT,'Finalizations are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_release_immutable BEFORE UPDATE ON exam_grade_releases BEGIN SELECT RAISE(ABORT,'Feedback releases are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_release_retained BEFORE DELETE ON exam_grade_releases BEGIN SELECT RAISE(ABORT,'Feedback releases are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_grading_event_immutable BEFORE UPDATE ON exam_grading_events BEGIN SELECT RAISE(ABORT,'Grading events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_grading_event_retained BEFORE DELETE ON exam_grading_events BEGIN SELECT RAISE(ABORT,'Grading events are retained'); END;
