-- SEND TO PRINTER (requirement, 2026-10-18).
-- A send prints existing, verified print files on the office printer over IPP: one job's PDF, a stack's interleaved PDF, or a
-- seating chart's whole-room PDF. Owner: the print jobs it prints, by reference (print_printer_send_batches); the printer's address
-- is workspace configuration. Each row is the instructor's one approval of exactly those jobs' outputs, sides and printer.
CREATE TABLE print_printer_sends (
 id TEXT PRIMARY KEY NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('job','stack','room')),
 seating_chart_id TEXT, printer_uri TEXT NOT NULL,
 sides TEXT NOT NULL CHECK(sides IN ('one-sided','two-sided-long-edge')),
 page_count INTEGER NOT NULL CHECK(page_count > 0),
 command_sha256 TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, approved_by TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','sending','accepted','failed','uncertain')),
 attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 printer_receipt_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK((kind = 'room') = (seating_chart_id IS NOT NULL))
);
CREATE TABLE print_printer_send_batches (
 send_id TEXT NOT NULL REFERENCES print_printer_sends(id), batch_id TEXT NOT NULL REFERENCES print_batches(id),
 output_sha256 TEXT NOT NULL, position INTEGER NOT NULL,
 PRIMARY KEY(send_id, batch_id), UNIQUE(send_id, position)
);
CREATE INDEX print_printer_send_queue ON print_printer_sends(status, created_at);
CREATE INDEX print_printer_send_batch ON print_printer_send_batches(batch_id);
CREATE TRIGGER print_printer_send_immutable
 BEFORE UPDATE OF id,kind,seating_chart_id,printer_uri,sides,page_count,command_sha256,request_id,approved_by,created_at ON print_printer_sends
 BEGIN SELECT RAISE(ABORT,'An approved printer send is immutable'); END;
CREATE TRIGGER print_printer_receipt_immutable BEFORE UPDATE OF printer_receipt_json ON print_printer_sends
 WHEN OLD.printer_receipt_json IS NOT NULL
 BEGIN SELECT RAISE(ABORT,'A printer receipt is immutable'); END;
CREATE TRIGGER print_printer_send_retained BEFORE DELETE ON print_printer_sends
 BEGIN SELECT RAISE(ABORT,'Printer send history is retained'); END;
CREATE TRIGGER print_printer_send_batch_immutable BEFORE UPDATE ON print_printer_send_batches
 BEGIN SELECT RAISE(ABORT,'An approved printer send is immutable'); END;
CREATE TRIGGER print_printer_send_batch_retained BEFORE DELETE ON print_printer_send_batches
 BEGIN SELECT RAISE(ABORT,'Printer send history is retained'); END;
