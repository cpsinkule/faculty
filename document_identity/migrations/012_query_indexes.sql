-- Lookup indexes for reads that ran once per page, paper or copy and each scanned a whole table (performance audit, 2026-09-29).
-- An index changes only how rows are found, never which rows: queries whose row order was visible without an ORDER BY had
-- today's order pinned in the same change (scans.assembly's other copies by id, unlabeled.apply's copy by copy number).
-- Every paper's exam version (grading/exam_versions.py printed_version, registered_version) joins its registration by copy.
CREATE INDEX IF NOT EXISTS scan_unlabeled_registrations_copy ON scan_unlabeled_registrations(printed_copy_id);
-- "Is this page in a reviewed assembly?", asked per scan page (sequence._rows, visibility_changed, unlabeled.apply).
CREATE INDEX IF NOT EXISTS assembly_pages_scan ON assembly_pages(scan_page_id);
-- A student's other copies of an assignment, asked per copy (scans.assembly, sequence._assemble_labeled, copy_names._waiting).
CREATE INDEX IF NOT EXISTS printed_copies_enrollment ON printed_copies(enrollment_id);
-- The student's chosen submission, asked per copy and participant (scans.assembly, exam_workflow's collection).
CREATE INDEX IF NOT EXISTS submission_choices_student ON submission_choices(enrollment_id,assignment_id,created_at);
