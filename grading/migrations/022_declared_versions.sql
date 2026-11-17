-- How many exam versions the instructor declared for a term's grading plan (requirement, 2026-09-24). An assignment made from a scan
-- has no seating chart to hand versions out, so its versions are named when it is created: letters A.. up to the count are what the
-- version read on each paper's first page may be, and that reading decides a paper's version. 0 or 1 is one version with no version
-- handling. Append-only; the latest declaration counts.
CREATE TABLE frq_declared_versions (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 versions_count INTEGER NOT NULL CHECK(versions_count BETWEEN 0 AND 26), request_id TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_declared_versions_plan ON frq_declared_versions(assignment_id,created_at);
CREATE TRIGGER frq_declared_version_immutable BEFORE UPDATE ON frq_declared_versions
 BEGIN SELECT RAISE(ABORT,'Declared exam versions are append-only'); END;
CREATE TRIGGER frq_declared_version_retained BEFORE DELETE ON frq_declared_versions
 BEGIN SELECT RAISE(ABORT,'Declared exam versions are retained'); END;
