-- JEV PAPERS (requirement, 2026-10-15): a first page Jev reads as one roster student is identified on the basis 'jev', beside an exact
-- UIN, a confident match and the instructor's own choice. SQLite cannot widen a CHECK in place, so the registration requests are
-- rebuilt (catalog_migrations.migration_transaction(rebuild=...)): every row keeps its values and rowid; no other table references
-- this one, and its index and triggers are made again.
CREATE TEMP TABLE scan_unlabeled_plans_carry AS SELECT rowid AS carried_rowid, * FROM scan_unlabeled_plans;
CREATE TEMP TABLE scan_unlabeled_plans_carried (complete INTEGER NOT NULL CHECK(complete=1));
INSERT INTO scan_unlabeled_plans_carried SELECT (SELECT COUNT(*) FROM scan_unlabeled_plans_carry)=(SELECT COUNT(*) FROM scan_unlabeled_plans);
DROP TABLE scan_unlabeled_plans;
CREATE TABLE scan_unlabeled_plans (
 run_id TEXT NOT NULL REFERENCES scan_unlabeled_runs(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 enrollment_id TEXT NOT NULL, section_id TEXT NOT NULL REFERENCES course_sections(id),
 basis TEXT NOT NULL CHECK(basis IN ('uin','confidence','review','jev')), printed_copy_id TEXT REFERENCES printed_copies(id),
 print_batch_id TEXT REFERENCES print_batches(id), request_id TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,scan_page_id), CHECK((printed_copy_id IS NULL)!=(print_batch_id IS NULL)),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
INSERT INTO scan_unlabeled_plans(rowid,run_id,scan_page_id,enrollment_id,section_id,basis,printed_copy_id,print_batch_id,request_id,actor,created_at)
 SELECT carried_rowid,run_id,scan_page_id,enrollment_id,section_id,basis,printed_copy_id,print_batch_id,request_id,actor,created_at
 FROM scan_unlabeled_plans_carry;
INSERT INTO scan_unlabeled_plans_carried SELECT (SELECT COUNT(*) FROM scan_unlabeled_plans_carry)=(SELECT COUNT(*) FROM scan_unlabeled_plans);
DROP TABLE scan_unlabeled_plans_carried;
DROP TABLE scan_unlabeled_plans_carry;
CREATE INDEX scan_unlabeled_plans_batch ON scan_unlabeled_plans(print_batch_id);
CREATE TRIGGER scan_unlabeled_plan_immutable BEFORE UPDATE ON scan_unlabeled_plans BEGIN SELECT RAISE(ABORT,'Registration requests are immutable'); END;
CREATE TRIGGER scan_unlabeled_plan_retained BEFORE DELETE ON scan_unlabeled_plans BEGIN SELECT RAISE(ABORT,'Registration requests are retained'); END;
