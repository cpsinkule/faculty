-- A question bank for each course (requirement, 2026-10-01). Owner: the COURSE DEFINITION
-- (`course_definitions.id`): one bank serves every term and section of the course code. AGENTS.md "Question bank".
--
-- The OUTLINE is the bank's own tree of chapters, sections, topics and learning objectives (`question_bank_nodes`, identity and place;
-- `question_bank_node_revisions`, number, title, order and whether it is retired). It starts from the course's lecture notes and follows
-- them (`question_bank_outline_imports` records each edition read); the instructor renames, adds and retires entries; the tagging model
-- adds learning objectives. A QUESTION (`question_bank_questions`) is retained from an approved assessment or quiz (by reference to its
-- grading question), read out of a PDF the instructor chose, or written by the instructor; its content is a chain of revisions
-- (`question_bank_revisions`) and its place in the outline, difficulty and type a chain of classifications
-- (`question_bank_classifications`), by the model or by the instructor. Retiring hides a question until it is restored
-- (`question_bank_events`). Model work (tagging, reading a PDF) is a RUN (`question_bank_runs`, its questions in
-- `question_bank_run_questions`), carried out by the cloud dispatcher with ordinary cloud calls (grading migration 037). Uploaded PDFs live
-- in the definition's verified storage under `question-bank/uploads/<upload id>/`. Everything is append-only and retained, except a run's
-- status while it is carried out.
CREATE TABLE question_bank_nodes (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 parent_id TEXT REFERENCES question_bank_nodes(id),
 level TEXT NOT NULL CHECK(level IN ('chapter', 'section', 'topic', 'objective')),
 origin TEXT NOT NULL CHECK(origin IN ('notes', 'instructor', 'model')),
 origin_key TEXT CHECK(origin_key IS NULL OR length(origin_key) BETWEEN 1 AND 400),
 created_at TEXT NOT NULL,
 UNIQUE(definition_id, origin_key)
);
CREATE TABLE question_bank_node_revisions (
 node_id TEXT NOT NULL REFERENCES question_bank_nodes(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 number TEXT NOT NULL DEFAULT '' CHECK(length(number) <= 20),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 300),
 position INTEGER NOT NULL,
 retired INTEGER NOT NULL DEFAULT 0 CHECK(retired IN (0, 1)),
 origin TEXT NOT NULL CHECK(origin IN ('notes', 'instructor', 'model')),
 request_id TEXT UNIQUE CHECK(request_id IS NULL OR length(request_id) BETWEEN 1 AND 128),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(node_id, revision)
);
CREATE TABLE question_bank_outline_imports (
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 notes_id TEXT NOT NULL REFERENCES course_notes(id),
 summary_json TEXT NOT NULL CHECK(json_valid(summary_json) AND json_type(summary_json) = 'object'),
 created_at TEXT NOT NULL,
 PRIMARY KEY(definition_id, notes_id)
);
CREATE TABLE question_bank_uploads (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 source_name TEXT NOT NULL CHECK(length(source_name) BETWEEN 1 AND 200),
 source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
 source_size INTEGER NOT NULL CHECK(source_size > 0),
 path TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 128),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(definition_id, source_sha256)
);
CREATE TABLE question_bank_runs (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 kind TEXT NOT NULL CHECK(kind IN ('tag', 'extract')),
 upload_id TEXT REFERENCES question_bank_uploads(id),
 retry_of TEXT REFERENCES question_bank_runs(id),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 128),
 input_json TEXT NOT NULL CHECK(json_valid(input_json) AND json_type(input_json) = 'object'),
 profile_json TEXT NOT NULL CHECK(json_valid(profile_json) AND json_type(profile_json) = 'object'),
 actor TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'complete', 'failed')),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0, 1)),
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 CHECK((kind = 'extract') = (upload_id IS NOT NULL))
);
CREATE TABLE question_bank_questions (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 origin TEXT NOT NULL CHECK(origin IN ('assessment', 'quiz_draft', 'instructor', 'pdf')),
 origin_key TEXT NOT NULL CHECK(length(origin_key) BETWEEN 1 AND 200),
 frq_question_id TEXT REFERENCES frq_questions(id),
 upload_id TEXT REFERENCES question_bank_uploads(id),
 family TEXT NOT NULL DEFAULT '' CHECK(length(family) <= 200),
 source_json TEXT NOT NULL CHECK(json_valid(source_json) AND json_type(source_json) = 'object'),
 request_id TEXT UNIQUE CHECK(request_id IS NULL OR length(request_id) BETWEEN 1 AND 128),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(definition_id, origin_key),
 CHECK((origin IN ('assessment', 'quiz_draft')) = (frq_question_id IS NOT NULL)),
 CHECK((origin = 'pdf') = (upload_id IS NOT NULL))
);
CREATE TABLE question_bank_revisions (
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 content_json TEXT NOT NULL CHECK(json_valid(content_json) AND json_type(content_json) = 'object'),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 origin TEXT NOT NULL CHECK(origin IN ('source', 'instructor')),
 source_ref TEXT NOT NULL DEFAULT '' CHECK(length(source_ref) <= 200),
 request_id TEXT UNIQUE CHECK(request_id IS NULL OR length(request_id) BETWEEN 1 AND 128),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(question_id, revision)
);
CREATE TABLE question_bank_classifications (
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 content_revision INTEGER NOT NULL CHECK(content_revision >= 1),
 nodes_json TEXT NOT NULL CHECK(json_valid(nodes_json) AND json_type(nodes_json) = 'array'),
 difficulty TEXT NOT NULL CHECK(difficulty IN ('', 'easy', 'medium', 'hard')),
 types_json TEXT NOT NULL CHECK(json_valid(types_json) AND json_type(types_json) = 'array'),
 origin TEXT NOT NULL CHECK(origin IN ('model', 'instructor')),
 run_id TEXT REFERENCES question_bank_runs(id),
 reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 2000),
 request_id TEXT UNIQUE CHECK(request_id IS NULL OR length(request_id) BETWEEN 1 AND 128),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(question_id, revision),
 CHECK((origin = 'model') = (run_id IS NOT NULL))
);
CREATE TABLE question_bank_events (
 id TEXT PRIMARY KEY,
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 action TEXT NOT NULL CHECK(action IN ('retired', 'restored')),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 128),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE question_bank_run_questions (
 run_id TEXT NOT NULL REFERENCES question_bank_runs(id),
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 content_revision INTEGER NOT NULL CHECK(content_revision >= 1),
 PRIMARY KEY(run_id, question_id)
);
CREATE INDEX question_bank_nodes_course ON question_bank_nodes(definition_id, parent_id);
CREATE INDEX question_bank_uploads_course ON question_bank_uploads(definition_id, created_at);
CREATE INDEX question_bank_runs_queue ON question_bank_runs(status, not_before);
CREATE INDEX question_bank_runs_course ON question_bank_runs(definition_id, created_at);
CREATE INDEX question_bank_runs_upload ON question_bank_runs(upload_id, created_at);
CREATE INDEX question_bank_questions_course ON question_bank_questions(definition_id, created_at);
CREATE INDEX question_bank_questions_frq ON question_bank_questions(frq_question_id);
CREATE INDEX question_bank_events_question ON question_bank_events(question_id, created_at);
CREATE INDEX question_bank_run_questions_question ON question_bank_run_questions(question_id, content_revision);

-- An entry sits under an entry of its own course, one level up: a chapter at the top, a section at the top or under a chapter, a topic
-- under a section, a learning objective under a section or a topic.
CREATE TRIGGER question_bank_node_scope BEFORE INSERT ON question_bank_nodes
 WHEN (NEW.level = 'chapter' AND NEW.parent_id IS NOT NULL)
   OR (NEW.parent_id IS NOT NULL AND (SELECT definition_id FROM question_bank_nodes WHERE id = NEW.parent_id) IS NOT NEW.definition_id)
   OR (NEW.level = 'section' AND NEW.parent_id IS NOT NULL AND (SELECT level FROM question_bank_nodes WHERE id = NEW.parent_id) IS NOT 'chapter')
   OR (NEW.level = 'topic' AND (NEW.parent_id IS NULL OR (SELECT level FROM question_bank_nodes WHERE id = NEW.parent_id) IS NOT 'section'))
   OR (NEW.level = 'objective' AND (NEW.parent_id IS NULL OR (SELECT level FROM question_bank_nodes WHERE id = NEW.parent_id) NOT IN ('section', 'topic')))
 BEGIN SELECT RAISE(ABORT, 'An outline entry sits under an entry of its own course, one level up'); END;
CREATE TRIGGER question_bank_question_scope BEFORE INSERT ON question_bank_questions
 WHEN (NEW.upload_id IS NOT NULL AND (SELECT definition_id FROM question_bank_uploads WHERE id = NEW.upload_id) IS NOT NEW.definition_id)
   OR (NEW.frq_question_id IS NOT NULL AND (SELECT a.definition_id FROM frq_questions q JOIN assignment_versions v ON v.id = q.assignment_version_id
                                            JOIN assignments a ON a.id = v.assignment_id WHERE q.id = NEW.frq_question_id) IS NOT NEW.definition_id)
 BEGIN SELECT RAISE(ABORT, 'A bank question comes from its own course'); END;
CREATE TRIGGER question_bank_run_scope BEFORE INSERT ON question_bank_runs
 WHEN NEW.upload_id IS NOT NULL AND (SELECT definition_id FROM question_bank_uploads WHERE id = NEW.upload_id) IS NOT NEW.definition_id
 BEGIN SELECT RAISE(ABORT, 'A bank run reads its own course''s upload'); END;
CREATE TRIGGER question_bank_run_question_scope BEFORE INSERT ON question_bank_run_questions
 WHEN (SELECT definition_id FROM question_bank_runs WHERE id = NEW.run_id) IS NOT (SELECT definition_id FROM question_bank_questions WHERE id = NEW.question_id)
 BEGIN SELECT RAISE(ABORT, 'A bank run tags its own course''s questions'); END;

CREATE TRIGGER question_bank_nodes_immutable BEFORE UPDATE ON question_bank_nodes BEGIN SELECT RAISE(ABORT, 'The question bank outline is append-only'); END;
CREATE TRIGGER question_bank_nodes_retained BEFORE DELETE ON question_bank_nodes BEGIN SELECT RAISE(ABORT, 'The question bank outline is retained'); END;
CREATE TRIGGER question_bank_node_revisions_immutable BEFORE UPDATE ON question_bank_node_revisions BEGIN SELECT RAISE(ABORT, 'The question bank outline is append-only'); END;
CREATE TRIGGER question_bank_node_revisions_retained BEFORE DELETE ON question_bank_node_revisions BEGIN SELECT RAISE(ABORT, 'The question bank outline is retained'); END;
CREATE TRIGGER question_bank_outline_imports_immutable BEFORE UPDATE ON question_bank_outline_imports BEGIN SELECT RAISE(ABORT, 'Outline imports are append-only'); END;
CREATE TRIGGER question_bank_outline_imports_retained BEFORE DELETE ON question_bank_outline_imports BEGIN SELECT RAISE(ABORT, 'Outline imports are retained'); END;
CREATE TRIGGER question_bank_uploads_immutable BEFORE UPDATE ON question_bank_uploads BEGIN SELECT RAISE(ABORT, 'Question bank uploads are append-only'); END;
CREATE TRIGGER question_bank_uploads_retained BEFORE DELETE ON question_bank_uploads BEGIN SELECT RAISE(ABORT, 'Question bank uploads are retained'); END;
CREATE TRIGGER question_bank_questions_immutable BEFORE UPDATE ON question_bank_questions BEGIN SELECT RAISE(ABORT, 'Bank questions are append-only'); END;
CREATE TRIGGER question_bank_questions_retained BEFORE DELETE ON question_bank_questions BEGIN SELECT RAISE(ABORT, 'Bank questions are retained'); END;
CREATE TRIGGER question_bank_revisions_immutable BEFORE UPDATE ON question_bank_revisions BEGIN SELECT RAISE(ABORT, 'Bank question revisions are append-only'); END;
CREATE TRIGGER question_bank_revisions_retained BEFORE DELETE ON question_bank_revisions BEGIN SELECT RAISE(ABORT, 'Bank question revisions are retained'); END;
CREATE TRIGGER question_bank_classifications_immutable BEFORE UPDATE ON question_bank_classifications BEGIN SELECT RAISE(ABORT, 'Bank classifications are append-only'); END;
CREATE TRIGGER question_bank_classifications_retained BEFORE DELETE ON question_bank_classifications BEGIN SELECT RAISE(ABORT, 'Bank classifications are retained'); END;
CREATE TRIGGER question_bank_events_immutable BEFORE UPDATE ON question_bank_events BEGIN SELECT RAISE(ABORT, 'Bank events are append-only'); END;
CREATE TRIGGER question_bank_events_retained BEFORE DELETE ON question_bank_events BEGIN SELECT RAISE(ABORT, 'Bank events are retained'); END;
CREATE TRIGGER question_bank_run_questions_immutable BEFORE UPDATE ON question_bank_run_questions BEGIN SELECT RAISE(ABORT, 'Bank run questions are append-only'); END;
CREATE TRIGGER question_bank_run_questions_retained BEFORE DELETE ON question_bank_run_questions BEGIN SELECT RAISE(ABORT, 'Bank run questions are retained'); END;
CREATE TRIGGER question_bank_run_inputs_immutable BEFORE UPDATE OF id, definition_id, kind, upload_id, retry_of, request_id, input_json, profile_json, actor, created_at ON question_bank_runs
 BEGIN SELECT RAISE(ABORT, 'Bank run inputs are immutable'); END;
CREATE TRIGGER question_bank_run_result_immutable BEFORE UPDATE ON question_bank_runs
 WHEN OLD.status IN ('complete', 'failed') BEGIN SELECT RAISE(ABORT, 'Bank run results are immutable'); END;
CREATE TRIGGER question_bank_runs_retained BEFORE DELETE ON question_bank_runs BEGIN SELECT RAISE(ABORT, 'Bank runs are retained'); END;
