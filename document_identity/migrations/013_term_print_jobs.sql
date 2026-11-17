-- Print jobs owned by the term (requirement, 2026-10-01) A job printed without a seating chart belongs to its course term
-- (`term_id`, course_terms.id) and names no section; a copy's section is its student's own roster section once the copy is named.
-- Seated and earlier jobs keep their section, and every job now records its term (for a section job, the section's term).
--
-- SQLite cannot drop a NOT NULL in place, so the table is rebuilt: run with foreign-key enforcement suspended for this one
-- transaction and every reference checked before commit (catalog_migrations.migration_transaction(rebuild=...)), applied after
-- the columns storage.initialize adds. No rename is used: triggers on other tables name print_batches and would not survive one.
-- Each row keeps its rowid, so a read without ORDER BY still meets the jobs in the order it did.
CREATE TEMP TABLE print_batches_carry AS SELECT b.rowid AS carried_rowid, b.*, s.term_id AS carried_term_id FROM print_batches b
 JOIN course_sections s ON s.id=b.section_id;
CREATE TEMP TABLE print_batches_carried (complete INTEGER NOT NULL CHECK(complete=1));
INSERT INTO print_batches_carried SELECT (SELECT COUNT(*) FROM print_batches_carry)=(SELECT COUNT(*) FROM print_batches);
DROP TABLE print_batches;
CREATE TABLE print_batches (
 id TEXT PRIMARY KEY NOT NULL, assignment_version_id TEXT NOT NULL REFERENCES assignment_versions(id),
 section_id TEXT REFERENCES course_sections(id), quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 500),
 status TEXT NOT NULL CHECK(status IN ('pending','rendering','validating','ready','failed','cancelled')),
 creator TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 configuration_json TEXT NOT NULL, output_path TEXT, output_sha256 TEXT, manifest_path TEXT, manifest_sha256 TEXT,
 reprint_of_batch_id TEXT REFERENCES print_batches(id), superseded_at TEXT,
 error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 seating_chart_id TEXT REFERENCES seating_charts(id) ON DELETE SET NULL, labels_path TEXT, labels_sha256 TEXT,
 seat_marks INTEGER NOT NULL DEFAULT 0 CHECK(seat_marks IN (0, 1)),
 label_mode TEXT NOT NULL DEFAULT 'every_page' CHECK(label_mode IN ('every_page', 'first_page')),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 CHECK(section_id IS NOT NULL OR seating_chart_id IS NULL)
);
INSERT INTO print_batches(rowid,id,assignment_version_id,section_id,quantity,status,creator,request_id,request_hash,configuration_json,
 output_path,output_sha256,manifest_path,manifest_sha256,reprint_of_batch_id,superseded_at,error,attempts,created_at,updated_at,
 seating_chart_id,labels_path,labels_sha256,seat_marks,label_mode,term_id)
 SELECT carried_rowid,id,assignment_version_id,section_id,quantity,status,creator,request_id,request_hash,configuration_json,
 output_path,output_sha256,manifest_path,manifest_sha256,reprint_of_batch_id,superseded_at,error,attempts,created_at,updated_at,
 seating_chart_id,labels_path,labels_sha256,seat_marks,label_mode,carried_term_id FROM print_batches_carry;
INSERT INTO print_batches_carried SELECT (SELECT COUNT(*) FROM print_batches_carry)=(SELECT COUNT(*) FROM print_batches);
DROP TABLE print_batches_carried;
DROP TABLE print_batches_carry;
CREATE INDEX document_print_queue ON print_batches(status,created_at);
-- The table's own triggers went with it: the same rules, with the term as the owner a section job shares with its section.
CREATE TRIGGER batch_scope BEFORE INSERT ON print_batches
 WHEN (SELECT a.definition_id FROM assignment_versions v JOIN assignments a ON a.id=v.assignment_id WHERE v.id=NEW.assignment_version_id)
 IS NOT (SELECT definition_id FROM course_terms WHERE id=NEW.term_id)
 OR (NEW.section_id IS NOT NULL AND NEW.term_id IS NOT (SELECT term_id FROM course_sections WHERE id=NEW.section_id))
 BEGIN SELECT RAISE(ABORT,'Assignment and section course mismatch'); END;
CREATE TRIGGER batch_reprint BEFORE INSERT ON print_batches
 WHEN NEW.reprint_of_batch_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM print_batches b
 WHERE b.id=NEW.reprint_of_batch_id AND b.section_id IS NEW.section_id AND b.term_id=NEW.term_id
 AND b.assignment_version_id=NEW.assignment_version_id)
 BEGIN SELECT RAISE(ABORT,'Reprint must retain version and section'); END;
CREATE TRIGGER batch_mapping_immutable BEFORE UPDATE OF assignment_version_id,section_id,term_id,quantity,request_id,request_hash,configuration_json,reprint_of_batch_id ON print_batches
 BEGIN SELECT RAISE(ABORT,'Batch mappings are immutable'); END;
CREATE TRIGGER issued_batch_immutable BEFORE UPDATE OF status,output_path,output_sha256,manifest_path,manifest_sha256 ON print_batches WHEN OLD.status='ready'
 BEGIN SELECT RAISE(ABORT,'Issued batch cannot be changed or cancelled'); END;
CREATE TRIGGER batch_ready BEFORE UPDATE OF status ON print_batches WHEN NEW.status='ready' AND (
 NEW.output_path IS NULL OR NEW.output_sha256 IS NULL OR NEW.manifest_path IS NULL OR NEW.manifest_sha256 IS NULL
 OR (SELECT COUNT(*) FROM printed_copies WHERE print_batch_id=NEW.id)!=NEW.quantity
 OR (SELECT COUNT(*) FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=NEW.id)
 !=NEW.quantity*(SELECT page_count FROM assignment_versions WHERE id=NEW.assignment_version_id))
 BEGIN SELECT RAISE(ABORT,'Incomplete print publication'); END;
-- A copy of a section job is named for a student on that section's roster, as before; a copy of a term job for a student on
-- the roster of any section of its term.
DROP TRIGGER copy_student_insert;
DROP TRIGGER copy_student_update;
CREATE TRIGGER copy_student_insert BEFORE INSERT ON printed_copies WHEN NEW.enrollment_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM print_batches b JOIN roster_memberships m ON m.enrollment_id=NEW.enrollment_id JOIN course_sections s ON s.id=m.section_id
 WHERE b.id=NEW.print_batch_id AND (m.section_id=b.section_id OR (b.section_id IS NULL AND s.term_id=b.term_id)))
 BEGIN SELECT RAISE(ABORT,'Student must belong to section roster'); END;
CREATE TRIGGER copy_student_update BEFORE UPDATE OF enrollment_id ON printed_copies WHEN NEW.enrollment_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM print_batches b JOIN roster_memberships m ON m.enrollment_id=NEW.enrollment_id JOIN course_sections s ON s.id=m.section_id
 WHERE b.id=NEW.print_batch_id AND (m.section_id=b.section_id OR (b.section_id IS NULL AND s.term_id=b.term_id)))
 BEGIN SELECT RAISE(ABORT,'Student must belong to section roster'); END;
-- The sections a job printed for the whole term is for (the ones ticked when it was made): references owned by that job, never
-- an identity of any copy. Name reading matches its copies against these rosters, and a scan of its paper is for these sections.
CREATE TABLE print_batch_sections (
 print_batch_id TEXT NOT NULL REFERENCES print_batches(id), section_id TEXT NOT NULL REFERENCES course_sections(id),
 PRIMARY KEY(print_batch_id, section_id)
);
CREATE TRIGGER print_batch_section_scope BEFORE INSERT ON print_batch_sections WHEN NOT EXISTS(
 SELECT 1 FROM print_batches b JOIN course_sections s ON s.term_id=b.term_id
 WHERE b.id=NEW.print_batch_id AND b.section_id IS NULL AND b.status='pending' AND s.id=NEW.section_id)
 BEGIN SELECT RAISE(ABORT,'A term print job lists sections of its own term when it is made'); END;
CREATE TRIGGER print_batch_section_immutable BEFORE UPDATE ON print_batch_sections
 BEGIN SELECT RAISE(ABORT,'Print job sections are immutable'); END;
CREATE TRIGGER print_batch_section_retained BEFORE DELETE ON print_batch_sections
 BEGIN SELECT RAISE(ABORT,'Print job sections are retained'); END;
