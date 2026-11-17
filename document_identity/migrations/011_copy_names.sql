-- Names read on stamped copies printed for no student (requirement, 2026-09-28). The worker reads the Name and UIN written at the top of such a copy's first page, once per
-- scanned page and rule, and keeps the reading here, immutable. Who a copy is for stays the copy's own `enrollment_id`, set through the
-- ordinary `assign_copy`: by the worker only for a confident match nothing contradicts (the rule for first pages without labels), else by
-- the instructor, with the `copy_assignment` event naming the reading.
CREATE TABLE copy_name_reads (
 id TEXT PRIMARY KEY,
 scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 printed_copy_id TEXT NOT NULL REFERENCES printed_copies(id),
 rule TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('complete','failed')),
 reading_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(scan_page_id,rule)
);
CREATE INDEX copy_name_reads_copy ON copy_name_reads(printed_copy_id);
CREATE TRIGGER copy_name_read_immutable BEFORE UPDATE ON copy_name_reads BEGIN SELECT RAISE(ABORT,'Copy name readings are immutable'); END;
CREATE TRIGGER copy_name_read_retained BEFORE DELETE ON copy_name_reads BEGIN SELECT RAISE(ABORT,'Copy name readings are retained'); END;
