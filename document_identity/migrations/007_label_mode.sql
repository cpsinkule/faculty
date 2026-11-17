-- A print job's paper normally carries a Data Matrix on every page (print_batches.label_mode 'every_page', added in
-- storage.initialize). When the instructor labels only the first page of each exam, the job is 'first_page': nothing
-- is expected on its other pages. This remembers a scan where the instructor chose to keep expecting every page, so the
-- question is asked once per scan and print job, not on every visit.
CREATE TABLE IF NOT EXISTS scan_label_prompts (
 message_id TEXT NOT NULL REFERENCES inbox_messages(id), batch_id TEXT NOT NULL REFERENCES print_batches(id),
 decision TEXT NOT NULL CHECK(decision IN ('keep')), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(message_id,batch_id)
);
