-- Templates and immutable problem maps belong to the course-wide assignment.
-- Their original files, normalized reference and page images share its verified
-- course-definition storage; they do not qualify a document for printing.
CREATE TABLE assignment_templates (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
 label TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
 page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 100),
 source_format TEXT NOT NULL, source_names_json TEXT NOT NULL,
 manifest_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(assignment_id,label)
);
CREATE TABLE assignment_template_revisions (
 id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES assignment_templates(id),
 revision INTEGER NOT NULL CHECK(revision>0), questions_json TEXT NOT NULL,
 sha256 TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(template_id,revision)
);
CREATE INDEX assignment_templates_assignment ON assignment_templates(assignment_id,created_at);
CREATE TRIGGER assignment_template_immutable BEFORE UPDATE ON assignment_templates
 BEGIN SELECT RAISE(ABORT,'Uploaded templates are immutable'); END;
CREATE TRIGGER assignment_template_retained BEFORE DELETE ON assignment_templates
 BEGIN SELECT RAISE(ABORT,'Uploaded templates are retained'); END;
CREATE TRIGGER assignment_template_revision_immutable BEFORE UPDATE ON assignment_template_revisions
 BEGIN SELECT RAISE(ABORT,'Template mappings are immutable revisions'); END;
CREATE TRIGGER assignment_template_revision_retained BEFORE DELETE ON assignment_template_revisions
 BEGIN SELECT RAISE(ABORT,'Template mappings are retained'); END;
