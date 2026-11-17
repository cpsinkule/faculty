-- A shared rubric may derive from one exact immutable assignment-template map.
-- Term exams still pin their own rubric; applying a map never updates another pin.
CREATE TABLE exam_rubric_template_sources (
    rubric_id TEXT PRIMARY KEY REFERENCES exam_rubric_versions(id),
    template_revision_id TEXT NOT NULL REFERENCES assignment_template_revisions(id),
    template_sha256 TEXT NOT NULL CHECK(length(template_sha256) = 64),
    template_json TEXT NOT NULL
);
CREATE TRIGGER exam_rubric_template_scope BEFORE INSERT ON exam_rubric_template_sources
WHEN NOT EXISTS (
    SELECT 1 FROM exam_rubric_versions r
    JOIN assignment_versions v ON v.id=r.assignment_version_id
    JOIN assignment_template_revisions tr ON tr.id=NEW.template_revision_id
    JOIN assignment_templates t ON t.id=tr.template_id
    WHERE r.id=NEW.rubric_id AND t.assignment_id=r.assignment_id
      AND t.page_count=v.page_count AND tr.sha256=NEW.template_sha256
)
BEGIN SELECT RAISE(ABORT,'Rubric template must match the assignment and document page count'); END;
CREATE TRIGGER exam_rubric_template_immutable BEFORE UPDATE ON exam_rubric_template_sources
BEGIN SELECT RAISE(ABORT,'Rubric template provenance is immutable'); END;
CREATE TRIGGER exam_rubric_template_retained BEFORE DELETE ON exam_rubric_template_sources
BEGIN SELECT RAISE(ABORT,'Rubric template provenance is retained'); END;
