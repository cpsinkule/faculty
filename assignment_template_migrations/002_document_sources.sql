-- Templates remain course-wide. A generated reference explicitly names the
-- immutable assignment version whose canonical PDF supplied its pages.
ALTER TABLE assignment_templates ADD COLUMN source_version_id TEXT REFERENCES assignment_versions(id);
CREATE UNIQUE INDEX assignment_template_document_source ON assignment_templates(source_version_id)
 WHERE source_version_id IS NOT NULL;
