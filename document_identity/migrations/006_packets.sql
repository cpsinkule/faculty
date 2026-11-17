-- An exam packet is one labeled paper as it was scanned: the label's printed copy and the scan pages that
-- followed it, in order. It records which pages belong to which student's paper without claiming a page
-- identity for pages the copy was never issued (a first-page label exported for one page). Packets belong
-- to the identification run that made them and to its source Inbox message; undoing the run retires them.
CREATE TABLE IF NOT EXISTS scan_packets (
 id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES scan_sequence_runs(id), message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 anchor_scan_page_id TEXT NOT NULL REFERENCES scan_pages(id), printed_copy_id TEXT NOT NULL REFERENCES printed_copies(id),
 sides INTEGER NOT NULL CHECK(sides>=1), created_at TEXT NOT NULL, UNIQUE(run_id,anchor_scan_page_id)
);
CREATE INDEX IF NOT EXISTS scan_packets_message ON scan_packets(message_id);
CREATE INDEX IF NOT EXISTS scan_packets_copy ON scan_packets(printed_copy_id);
CREATE TABLE IF NOT EXISTS scan_packet_pages (
 packet_id TEXT NOT NULL REFERENCES scan_packets(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 side INTEGER NOT NULL CHECK(side>=1), PRIMARY KEY(packet_id,side), UNIQUE(packet_id,scan_page_id)
);
CREATE INDEX IF NOT EXISTS scan_packet_pages_scan ON scan_packet_pages(scan_page_id);
CREATE TRIGGER IF NOT EXISTS scan_packet_owner BEFORE INSERT ON scan_packets
 WHEN NEW.message_id IS NOT (SELECT message_id FROM scan_sequence_runs WHERE id=NEW.run_id)
 OR NEW.message_id IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.anchor_scan_page_id)
 BEGIN SELECT RAISE(ABORT,'Exam packets must belong to their run''s Inbox message'); END;
CREATE TRIGGER IF NOT EXISTS scan_packet_page_owner BEFORE INSERT ON scan_packet_pages
 WHEN (SELECT message_id FROM scan_packets WHERE id=NEW.packet_id) IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.scan_page_id)
 BEGIN SELECT RAISE(ABORT,'Exam packet pages must belong to the packet''s Inbox message'); END;
CREATE TRIGGER IF NOT EXISTS scan_packet_immutable BEFORE UPDATE ON scan_packets BEGIN SELECT RAISE(ABORT,'Exam packets are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_packet_retained BEFORE DELETE ON scan_packets BEGIN SELECT RAISE(ABORT,'Exam packets are retained'); END;
CREATE TRIGGER IF NOT EXISTS scan_packet_page_immutable BEFORE UPDATE ON scan_packet_pages BEGIN SELECT RAISE(ABORT,'Exam packet pages are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_packet_page_retained BEFORE DELETE ON scan_packet_pages BEGIN SELECT RAISE(ABORT,'Exam packet pages are retained'); END;
