-- ICLICKER AFTER CLASS (docs/decisions/iclicker.md; requirement, 2026-10-06). One row per attempt of a section's
-- Import from iClicker after its class on a campus date (gradebook/iclicker_after_class.py): what it did (`status`), the sessions
-- iClicker had started that day (titles and IDs) with how many were still running, counts and a problem's words, never a student;
-- and, when it wrote, the import request it recorded. Owned by the SECTION (course_sections.id) of its term; append-only, the
-- latest row of a section's date counts.
CREATE TABLE gradebook_iclicker_runs (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    section_id TEXT NOT NULL REFERENCES course_sections(id),
    class_date TEXT NOT NULL CHECK(class_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    status TEXT NOT NULL CHECK(status IN ('imported', 'unchanged', 'needs_choice', 'not_linked', 'failed')),
    detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
    request_ref TEXT REFERENCES gradebook_requests(id),
    created_at TEXT NOT NULL,
    CHECK((status = 'imported') = (request_ref IS NOT NULL))
);
CREATE INDEX gradebook_iclicker_runs_section ON gradebook_iclicker_runs(section_id, class_date, created_at);
CREATE INDEX gradebook_iclicker_runs_term ON gradebook_iclicker_runs(term_id, class_date);

CREATE TRIGGER gradebook_iclicker_run_owner BEFORE INSERT ON gradebook_iclicker_runs
WHEN NOT EXISTS (SELECT 1 FROM course_sections WHERE id = NEW.section_id AND term_id = NEW.term_id)
  OR (NEW.request_ref IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id))
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker run owner mismatch'); END;
CREATE TRIGGER gradebook_iclicker_runs_immutable BEFORE UPDATE ON gradebook_iclicker_runs
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker runs are immutable'); END;
CREATE TRIGGER gradebook_iclicker_runs_retained BEFORE DELETE ON gradebook_iclicker_runs
BEGIN SELECT RAISE(ABORT, 'Gradebook iClicker runs are retained'); END;
