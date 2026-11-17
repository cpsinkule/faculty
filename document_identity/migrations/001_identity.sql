CREATE TABLE IF NOT EXISTS assignments (
 id TEXT PRIMARY KEY NOT NULL, definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 assignment_key TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(definition_id,assignment_key)
);
CREATE TABLE IF NOT EXISTS assignment_versions (
 id TEXT PRIMARY KEY NOT NULL, assignment_id TEXT NOT NULL REFERENCES assignments(id),
 label TEXT NOT NULL, source_path TEXT NOT NULL, source_manifest_sha256 TEXT NOT NULL,
 canonical_path TEXT NOT NULL, canonical_sha256 TEXT NOT NULL, page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 100),
 compiler_json TEXT NOT NULL, layout_profile TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(assignment_id,label)
);
CREATE TABLE IF NOT EXISTS print_batches (
 id TEXT PRIMARY KEY NOT NULL, assignment_version_id TEXT NOT NULL REFERENCES assignment_versions(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id), quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 500),
 status TEXT NOT NULL CHECK(status IN ('pending','rendering','validating','ready','failed','cancelled')),
 creator TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 configuration_json TEXT NOT NULL, output_path TEXT, output_sha256 TEXT, manifest_path TEXT, manifest_sha256 TEXT,
 reprint_of_batch_id TEXT REFERENCES print_batches(id), superseded_at TEXT,
 error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS printed_copies (
 id TEXT PRIMARY KEY NOT NULL, print_batch_id TEXT NOT NULL REFERENCES print_batches(id),
 copy_number INTEGER NOT NULL CHECK(copy_number>=1), enrollment_id TEXT REFERENCES student_enrollments(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(print_batch_id,copy_number)
);
CREATE TABLE IF NOT EXISTS page_stamps (
 id TEXT PRIMARY KEY NOT NULL, printed_copy_id TEXT NOT NULL REFERENCES printed_copies(id),
 page_number INTEGER NOT NULL CHECK(page_number>=1), token TEXT NOT NULL UNIQUE
 CHECK(length(token)=29 AND substr(token,1,3)='T1-' AND substr(token,4,1) BETWEEN '0' AND '7'
 AND substr(token,4) NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
 token_format INTEGER NOT NULL DEFAULT 1 CHECK(token_format=1), symbology TEXT NOT NULL,
 recovery_alias TEXT UNIQUE CHECK(recovery_alias IS NULL OR (length(recovery_alias)=13 AND substr(recovery_alias,1,3)='A1-'
 AND substr(recovery_alias,4) NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*')),
 placement_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('allocated','issued','cancelled','superseded')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(printed_copy_id,page_number)
);
CREATE TABLE IF NOT EXISTS document_events (
 id INTEGER PRIMARY KEY, assignment_id TEXT REFERENCES assignments(id), batch_id TEXT REFERENCES print_batches(id),
 copy_id TEXT REFERENCES printed_copies(id), actor TEXT NOT NULL, kind TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_decode_jobs (
 message_id TEXT PRIMARY KEY REFERENCES inbox_messages(id),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_pages (
 id TEXT PRIMARY KEY NOT NULL, message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 source_artifact_id TEXT NOT NULL REFERENCES inbox_artifacts(id), source_page_index INTEGER NOT NULL CHECK(source_page_index>=1),
 page_stamp_id TEXT REFERENCES page_stamps(id), candidates_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('resolved','unknown','malformed','ambiguous','unreadable','blank','cancelled','superseded','unissued','manual')),
 page_artifact_id TEXT NOT NULL REFERENCES inbox_artifacts(id), raster_artifact_id TEXT NOT NULL REFERENCES inbox_artifacts(id),
 content_sha256 TEXT NOT NULL, decoder_json TEXT NOT NULL,
 duplicate_of_scan_page_id TEXT REFERENCES scan_pages(id),
 review_state TEXT NOT NULL CHECK(review_state IN ('candidate','conflict','duplicate','selected','rejected','ignored')),
 received_at TEXT NOT NULL, processed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(source_artifact_id,source_page_index)
);
CREATE TABLE IF NOT EXISTS assembly_revisions (
 id TEXT PRIMARY KEY NOT NULL, printed_copy_id TEXT NOT NULL REFERENCES printed_copies(id), revision INTEGER NOT NULL,
 selection_json TEXT NOT NULL, selection_sha256 TEXT NOT NULL, partial INTEGER NOT NULL CHECK(partial IN (0,1)),
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(printed_copy_id,revision)
);
CREATE TABLE IF NOT EXISTS submission_choices (
 id TEXT PRIMARY KEY NOT NULL, enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 assignment_id TEXT NOT NULL REFERENCES assignments(id), assembly_revision_id TEXT NOT NULL REFERENCES assembly_revisions(id),
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assembly_pages (
 revision_id TEXT NOT NULL REFERENCES assembly_revisions(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 page_number INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(revision_id,page_number), UNIQUE(revision_id,scan_page_id)
);
CREATE INDEX IF NOT EXISTS document_print_queue ON print_batches(status,created_at);
CREATE INDEX IF NOT EXISTS scan_stamp ON scan_pages(page_stamp_id,created_at);
CREATE INDEX IF NOT EXISTS scan_message ON scan_pages(message_id);
CREATE TRIGGER IF NOT EXISTS assignment_immutable BEFORE UPDATE ON assignments BEGIN SELECT RAISE(ABORT,'Assignments are retained identities'); END;
CREATE TRIGGER IF NOT EXISTS version_immutable BEFORE UPDATE ON assignment_versions BEGIN SELECT RAISE(ABORT,'Assignment versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS version_retained BEFORE DELETE ON assignment_versions BEGIN SELECT RAISE(ABORT,'Assignment versions are retained'); END;
CREATE TRIGGER IF NOT EXISTS batch_scope BEFORE INSERT ON print_batches
 WHEN (SELECT a.definition_id FROM assignment_versions v JOIN assignments a ON a.id=v.assignment_id WHERE v.id=NEW.assignment_version_id)
 IS NOT (SELECT t.definition_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE s.id=NEW.section_id)
 BEGIN SELECT RAISE(ABORT,'Assignment and section course mismatch'); END;
CREATE TRIGGER IF NOT EXISTS batch_reprint BEFORE INSERT ON print_batches
 WHEN NEW.reprint_of_batch_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM print_batches b
 WHERE b.id=NEW.reprint_of_batch_id AND b.section_id=NEW.section_id AND b.assignment_version_id=NEW.assignment_version_id)
 BEGIN SELECT RAISE(ABORT,'Reprint must retain version and section'); END;
CREATE TRIGGER IF NOT EXISTS batch_mapping_immutable BEFORE UPDATE OF assignment_version_id,section_id,quantity,request_id,request_hash,configuration_json,reprint_of_batch_id ON print_batches
 BEGIN SELECT RAISE(ABORT,'Batch mappings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS issued_batch_immutable BEFORE UPDATE OF status,output_path,output_sha256,manifest_path,manifest_sha256 ON print_batches WHEN OLD.status='ready'
 BEGIN SELECT RAISE(ABORT,'Issued batch cannot be changed or cancelled'); END;
CREATE TRIGGER IF NOT EXISTS batch_ready BEFORE UPDATE OF status ON print_batches WHEN NEW.status='ready' AND (
 NEW.output_path IS NULL OR NEW.output_sha256 IS NULL OR NEW.manifest_path IS NULL OR NEW.manifest_sha256 IS NULL
 OR (SELECT COUNT(*) FROM printed_copies WHERE print_batch_id=NEW.id)!=NEW.quantity
 OR (SELECT COUNT(*) FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=NEW.id)
 !=NEW.quantity*(SELECT page_count FROM assignment_versions WHERE id=NEW.assignment_version_id))
 BEGIN SELECT RAISE(ABORT,'Incomplete print publication'); END;
CREATE TRIGGER IF NOT EXISTS copy_number_bound BEFORE INSERT ON printed_copies
 WHEN NEW.copy_number>(SELECT quantity FROM print_batches WHERE id=NEW.print_batch_id)
 OR (SELECT status FROM print_batches WHERE id=NEW.print_batch_id)!='pending'
 BEGIN SELECT RAISE(ABORT,'Invalid copy allocation'); END;
CREATE TRIGGER IF NOT EXISTS copy_student_insert BEFORE INSERT ON printed_copies WHEN NEW.enrollment_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM roster_memberships m JOIN print_batches b ON b.section_id=m.section_id
 WHERE b.id=NEW.print_batch_id AND m.enrollment_id=NEW.enrollment_id)
 BEGIN SELECT RAISE(ABORT,'Student must belong to section roster'); END;
CREATE TRIGGER IF NOT EXISTS copy_student_update BEFORE UPDATE OF enrollment_id ON printed_copies WHEN NEW.enrollment_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM roster_memberships m JOIN print_batches b ON b.section_id=m.section_id
 WHERE b.id=NEW.print_batch_id AND m.enrollment_id=NEW.enrollment_id)
 BEGIN SELECT RAISE(ABORT,'Student must belong to section roster'); END;
CREATE TRIGGER IF NOT EXISTS copy_mapping_immutable BEFORE UPDATE OF id,print_batch_id,copy_number ON printed_copies
 BEGIN SELECT RAISE(ABORT,'Copy mappings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stamp_page_bound BEFORE INSERT ON page_stamps WHEN NEW.page_number>(
 SELECT v.page_count FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
 JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE c.id=NEW.printed_copy_id)
 OR (SELECT b.status FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id WHERE c.id=NEW.printed_copy_id)!='pending'
 BEGIN SELECT RAISE(ABORT,'Invalid page allocation'); END;
CREATE TRIGGER IF NOT EXISTS stamp_mapping_immutable BEFORE UPDATE OF id,printed_copy_id,page_number,token,token_format,symbology,recovery_alias,placement_json ON page_stamps
 BEGIN SELECT RAISE(ABORT,'Page mappings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS stamp_retained BEFORE DELETE ON page_stamps BEGIN SELECT RAISE(ABORT,'Tokens cannot be recycled'); END;
CREATE TRIGGER IF NOT EXISTS scan_artifact_owner BEFORE INSERT ON scan_pages WHEN
 NEW.message_id IS NOT (SELECT message_id FROM inbox_artifacts WHERE id=NEW.source_artifact_id)
 OR NEW.message_id IS NOT (SELECT message_id FROM inbox_artifacts WHERE id=NEW.page_artifact_id)
 OR NEW.message_id IS NOT (SELECT message_id FROM inbox_artifacts WHERE id=NEW.raster_artifact_id)
 BEGIN SELECT RAISE(ABORT,'Scan artifacts must belong to their Inbox message'); END;
CREATE TRIGGER IF NOT EXISTS scan_payload_immutable BEFORE UPDATE OF id,message_id,source_artifact_id,source_page_index,candidates_json,
 page_artifact_id,raster_artifact_id,content_sha256,decoder_json,received_at,processed_at,created_at ON scan_pages
 BEGIN SELECT RAISE(ABORT,'Original scan observations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS selected_scan_identity_immutable BEFORE UPDATE OF page_stamp_id ON scan_pages
 WHEN EXISTS(SELECT 1 FROM assembly_pages WHERE scan_page_id=OLD.id)
 BEGIN SELECT RAISE(ABORT,'Reviewed observations retain their identity'); END;
CREATE TRIGGER IF NOT EXISTS assembly_page_scope BEFORE INSERT ON assembly_pages
 WHEN (SELECT printed_copy_id FROM assembly_revisions WHERE id=NEW.revision_id) IS NOT
 (SELECT p.printed_copy_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.id=NEW.scan_page_id)
 OR NEW.page_number IS NOT (SELECT p.page_number FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.id=NEW.scan_page_id)
 BEGIN SELECT RAISE(ABORT,'Assembly pages must retain one printed copy and page mapping'); END;
CREATE TRIGGER IF NOT EXISTS assembly_immutable BEFORE UPDATE ON assembly_revisions BEGIN SELECT RAISE(ABORT,'Assembly revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS assembly_retained BEFORE DELETE ON assembly_revisions BEGIN SELECT RAISE(ABORT,'Assembly revisions are retained'); END;
CREATE TRIGGER IF NOT EXISTS assembly_page_immutable BEFORE UPDATE ON assembly_pages BEGIN SELECT RAISE(ABORT,'Assembly selections are immutable'); END;
CREATE TRIGGER IF NOT EXISTS assembly_page_retained BEFORE DELETE ON assembly_pages BEGIN SELECT RAISE(ABORT,'Assembly selections are retained'); END;
CREATE TRIGGER IF NOT EXISTS document_event_immutable BEFORE UPDATE ON document_events BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS document_event_retained BEFORE DELETE ON document_events BEGIN SELECT RAISE(ABORT,'Audit events are retained'); END;
CREATE TRIGGER IF NOT EXISTS submission_choice_scope BEFORE INSERT ON submission_choices WHEN NOT EXISTS(
 SELECT 1 FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
 JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
 WHERE ar.id=NEW.assembly_revision_id AND c.enrollment_id=NEW.enrollment_id AND v.assignment_id=NEW.assignment_id)
 BEGIN SELECT RAISE(ABORT,'Submission choice does not match enrollment and assignment'); END;
