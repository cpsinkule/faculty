-- Email exports are section-owned through their retained print batch. The PDF
-- remains the original batch artifact; recipients are workspace configuration.
CREATE TABLE IF NOT EXISTS print_email_exports (
 id TEXT PRIMARY KEY NOT NULL, batch_id TEXT NOT NULL REFERENCES print_batches(id),
 recipient TEXT NOT NULL, output_sha256 TEXT NOT NULL, command_sha256 TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE, approved_by TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','sending','accepted','failed','uncertain')),
 attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 provider_receipt_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS print_email_queue ON print_email_exports(status,created_at);
CREATE TRIGGER IF NOT EXISTS print_email_mapping_immutable
 BEFORE UPDATE OF id,batch_id,recipient,output_sha256,command_sha256,request_id,approved_by,created_at ON print_email_exports
 BEGIN SELECT RAISE(ABORT,'Approved email export is immutable'); END;
CREATE TRIGGER IF NOT EXISTS print_email_receipt_immutable BEFORE UPDATE OF provider_receipt_json ON print_email_exports
 WHEN OLD.provider_receipt_json IS NOT NULL
 BEGIN SELECT RAISE(ABORT,'Email delivery receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS print_email_retained BEFORE DELETE ON print_email_exports
 BEGIN SELECT RAISE(ABORT,'Email export history is retained'); END;
