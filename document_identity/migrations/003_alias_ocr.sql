-- Non-executing OCR suggestions belong to the source Inbox message/page.
CREATE TABLE IF NOT EXISTS scan_alias_ocr (
 scan_page_id TEXT PRIMARY KEY NOT NULL REFERENCES scan_pages(id),
 message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','skipped')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 artifact_id TEXT REFERENCES inbox_artifacts(id),
 result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_alias_ocr_queue ON scan_alias_ocr(status,created_at);
CREATE TRIGGER IF NOT EXISTS scan_alias_ocr_owner BEFORE INSERT ON scan_alias_ocr
 WHEN NEW.message_id IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.scan_page_id)
 BEGIN SELECT RAISE(ABORT,'OCR must retain its source Inbox owner'); END;
CREATE TRIGGER IF NOT EXISTS scan_alias_ocr_identity BEFORE UPDATE OF scan_page_id,message_id,created_at ON scan_alias_ocr
 BEGIN SELECT RAISE(ABORT,'OCR source identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_alias_ocr_artifact BEFORE UPDATE OF artifact_id ON scan_alias_ocr
 WHEN NEW.artifact_id IS NOT NULL AND NEW.message_id IS NOT (SELECT message_id FROM inbox_artifacts WHERE id=NEW.artifact_id)
 BEGIN SELECT RAISE(ABORT,'OCR artifact must retain its source Inbox owner'); END;
CREATE TRIGGER IF NOT EXISTS scan_alias_ocr_artifact_insert BEFORE INSERT ON scan_alias_ocr
 WHEN NEW.artifact_id IS NOT NULL AND NEW.message_id IS NOT (SELECT message_id FROM inbox_artifacts WHERE id=NEW.artifact_id)
 BEGIN SELECT RAISE(ABORT,'OCR artifact must retain its source Inbox owner'); END;
CREATE TRIGGER IF NOT EXISTS scan_alias_ocr_completed BEFORE UPDATE ON scan_alias_ocr WHEN OLD.status IN ('complete','skipped')
 BEGIN SELECT RAISE(ABORT,'Finished OCR observations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_alias_ocr_retained BEFORE DELETE ON scan_alias_ocr
 BEGIN SELECT RAISE(ABORT,'OCR observations are retained'); END;
