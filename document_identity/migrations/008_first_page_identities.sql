-- Paper labeled on its first page only takes the identities of its other pages from the scan: the page 1 label, an even
-- label-to-label cadence and each side's position. A stamps-only version's page_count describes its label sheet, not the
-- exam behind it, so those identities exceed it and are issued after the job is ready. They are never printed and are
-- kept apart as symbology 'sequence'; every printed identity keeps the original bound. Assemblies, exam collection and
-- grading keep their one page mapping (page_stamps), so a page is still one copy and one page number everywhere.
DROP TRIGGER IF EXISTS stamp_page_bound;
CREATE TRIGGER stamp_page_bound BEFORE INSERT ON page_stamps WHEN NEW.symbology!='sequence' AND (NEW.page_number>(
 SELECT v.page_count FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
 JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE c.id=NEW.printed_copy_id)
 OR (SELECT b.status FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id WHERE c.id=NEW.printed_copy_id)!='pending')
 BEGIN SELECT RAISE(ABORT,'Invalid page allocation'); END;
-- A sequence identity only ever extends an issued, printed page 1 label of current stamps-only paper.
CREATE TRIGGER IF NOT EXISTS stamp_sequence_bound BEFORE INSERT ON page_stamps WHEN NEW.symbology='sequence' AND (
 NEW.page_number<2 OR NEW.page_number>200 OR NEW.state!='issued'
 OR NOT EXISTS(SELECT 1 FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
  JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE c.id=NEW.printed_copy_id
  AND b.status='ready' AND b.superseded_at IS NULL AND json_extract(v.compiler_json,'$.generator')='faculty-stamps-only')
 OR NOT EXISTS(SELECT 1 FROM page_stamps first WHERE first.printed_copy_id=NEW.printed_copy_id AND first.page_number=1
  AND first.state='issued' AND first.symbology!='sequence'))
 BEGIN SELECT RAISE(ABORT,'Sequence identities extend an issued first-page label'); END;
-- How a reviewed paper was assembled without a per-copy decision: which scan, run and check allowed it.
CREATE TABLE IF NOT EXISTS scan_sequence_assemblies (
 revision_id TEXT PRIMARY KEY NOT NULL REFERENCES assembly_revisions(id), packet_id TEXT NOT NULL REFERENCES scan_packets(id),
 message_id TEXT NOT NULL REFERENCES inbox_messages(id), sides INTEGER NOT NULL CHECK(sides>=1), pages INTEGER NOT NULL CHECK(pages>=1),
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_sequence_assemblies_message ON scan_sequence_assemblies(message_id);
CREATE TRIGGER IF NOT EXISTS scan_sequence_assembly_immutable BEFORE UPDATE ON scan_sequence_assemblies BEGIN SELECT RAISE(ABORT,'Assembly provenance is immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_sequence_assembly_retained BEFORE DELETE ON scan_sequence_assemblies BEGIN SELECT RAISE(ABORT,'Assembly provenance is retained'); END;
