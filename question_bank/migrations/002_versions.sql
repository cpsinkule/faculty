-- Versions of one problem, linked (requirement, 2026-10-01), and where a
-- question that was not retained from grading was printed, so its grades can be found. AGENTS.md "Question bank".
--
-- `question_bank_assessment_links` says which course-wide assignment, version and printed number a question read out of a PDF (or written
-- in the bank) is; a question retained from grading needs none (its grading question says it). Each change is a new revision; the latest
-- stands, and a revision without an assignment removes the link.
-- `question_bank_version_links` puts a question in a group of versions of one problem (`group_id`, shared by the members) or in none
-- (NULL). The latest revision stands. A drafted quiz's versions are linked from the quiz itself (`source`), an assessment's by the model
-- (`model`, a tagging run whose `purpose` is 'versions'), and the instructor's links and unlinks (`instructor`) are never changed by
-- either. All rows are append-only and retained.
ALTER TABLE question_bank_runs ADD COLUMN purpose TEXT NOT NULL DEFAULT '' CHECK(purpose IN ('', 'versions'));
CREATE TRIGGER question_bank_run_purpose_immutable BEFORE UPDATE OF purpose ON question_bank_runs
 BEGIN SELECT RAISE(ABORT, 'Bank run inputs are immutable'); END;
CREATE TRIGGER question_bank_run_purpose_kind BEFORE INSERT ON question_bank_runs
 WHEN NEW.purpose = 'versions' AND NEW.kind != 'tag'
 BEGIN SELECT RAISE(ABORT, 'Linking versions is a tagging run'); END;

CREATE TABLE question_bank_assessment_links (
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 assignment_id TEXT REFERENCES assignments(id),
 exam_version TEXT NOT NULL DEFAULT '' CHECK(exam_version = '' OR (length(exam_version) = 1 AND exam_version BETWEEN 'A' AND 'Z')),
 number TEXT NOT NULL DEFAULT '' CHECK(length(number) <= 20),
 request_id TEXT UNIQUE CHECK(request_id IS NULL OR length(request_id) BETWEEN 1 AND 160),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(question_id, revision),
 CHECK(assignment_id IS NOT NULL OR (exam_version = '' AND number = ''))
);
CREATE TABLE question_bank_version_links (
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 group_id TEXT CHECK(group_id IS NULL OR length(group_id) BETWEEN 1 AND 64),
 origin TEXT NOT NULL CHECK(origin IN ('source', 'model', 'instructor')),
 run_id TEXT REFERENCES question_bank_runs(id),
 reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 1000),
 request_id TEXT CHECK(request_id IS NULL OR length(request_id) BETWEEN 1 AND 160),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(question_id, revision),
 CHECK((origin = 'model') = (run_id IS NOT NULL))
);
CREATE INDEX question_bank_assessment_links_assignment ON question_bank_assessment_links(assignment_id);
CREATE INDEX question_bank_version_links_group ON question_bank_version_links(group_id);
CREATE INDEX question_bank_version_links_request ON question_bank_version_links(request_id);

-- A question is printed on an assignment of its own course; a question retained from grading is printed where its grading question is.
CREATE TRIGGER question_bank_assessment_link_scope BEFORE INSERT ON question_bank_assessment_links
 WHEN (SELECT frq_question_id FROM question_bank_questions WHERE id = NEW.question_id) IS NOT NULL
   OR (NEW.assignment_id IS NOT NULL AND (SELECT definition_id FROM assignments WHERE id = NEW.assignment_id)
       IS NOT (SELECT definition_id FROM question_bank_questions WHERE id = NEW.question_id))
 BEGIN SELECT RAISE(ABORT, 'A question is linked to an assignment of its own course, unless grading already says where it was printed'); END;
-- The versions of one problem belong to one course.
CREATE TRIGGER question_bank_version_link_scope BEFORE INSERT ON question_bank_version_links
 WHEN NEW.group_id IS NOT NULL AND EXISTS (SELECT 1 FROM question_bank_version_links l JOIN question_bank_questions q ON q.id = l.question_id
   WHERE l.group_id = NEW.group_id AND q.definition_id IS NOT (SELECT definition_id FROM question_bank_questions WHERE id = NEW.question_id))
 BEGIN SELECT RAISE(ABORT, 'The versions of one problem belong to one course'); END;

CREATE TRIGGER question_bank_assessment_links_immutable BEFORE UPDATE ON question_bank_assessment_links BEGIN SELECT RAISE(ABORT, 'Assessment links are append-only'); END;
CREATE TRIGGER question_bank_assessment_links_retained BEFORE DELETE ON question_bank_assessment_links BEGIN SELECT RAISE(ABORT, 'Assessment links are retained'); END;
CREATE TRIGGER question_bank_version_links_immutable BEFORE UPDATE ON question_bank_version_links BEGIN SELECT RAISE(ABORT, 'Version links are append-only'); END;
CREATE TRIGGER question_bank_version_links_retained BEFORE DELETE ON question_bank_version_links BEGIN SELECT RAISE(ABORT, 'Version links are retained'); END;
