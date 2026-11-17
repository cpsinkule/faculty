-- BATCHES SCANNED IN STACKS (INBOX, requirement, 2026-10-24). Each stack is its own Inbox message, so a paper labeled on its first page only can start at
-- the end of one stack and end at the top of the next. Its run, its packet and its page 1 label stay the message that holds the
-- label; a side of it may now also be a page of a LATER scan from the same sender (the next stack), never of any other message.
DROP TRIGGER IF EXISTS scan_sequence_page_owner;
CREATE TRIGGER scan_sequence_page_owner BEFORE INSERT ON scan_sequence_pages
 WHEN (SELECT message_id FROM scan_sequence_runs WHERE id=NEW.run_id) IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.anchor_scan_page_id)
 OR ((SELECT message_id FROM scan_sequence_runs WHERE id=NEW.run_id) IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.scan_page_id)
  AND NOT EXISTS(SELECT 1 FROM scan_sequence_runs r JOIN inbox_messages mine ON mine.id=r.message_id
   JOIN scan_pages sp ON sp.id=NEW.scan_page_id JOIN inbox_messages later ON later.id=sp.message_id
   WHERE r.id=NEW.run_id AND later.sender=mine.sender
   AND (later.received_at,later.created_at,later.id)>(mine.received_at,mine.created_at,mine.id)))
 BEGIN SELECT RAISE(ABORT,'Sequence pages must belong to their run''s Inbox message or the next stack from its sender'); END;
DROP TRIGGER IF EXISTS scan_packet_page_owner;
CREATE TRIGGER scan_packet_page_owner BEFORE INSERT ON scan_packet_pages
 WHEN (SELECT message_id FROM scan_packets WHERE id=NEW.packet_id) IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.scan_page_id)
 AND NOT EXISTS(SELECT 1 FROM scan_packets k JOIN inbox_messages mine ON mine.id=k.message_id
  JOIN scan_pages sp ON sp.id=NEW.scan_page_id JOIN inbox_messages later ON later.id=sp.message_id
  WHERE k.id=NEW.packet_id AND later.sender=mine.sender
  AND (later.received_at,later.created_at,later.id)>(mine.received_at,mine.created_at,mine.id))
 BEGIN SELECT RAISE(ABORT,'Exam packet pages must belong to the packet''s Inbox message or the next stack from its sender'); END;
