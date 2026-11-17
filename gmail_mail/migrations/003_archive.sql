-- Archive on the Mail page (requirement, 2026-09-30). Gmail's own
-- archive: the conversation loses its Inbox label and stays in All mail; Undo (or Move to Inbox) puts the label back. WORKSPACE-owned,
-- append-only and, like the Trash record, only what Faculty did: the thread ID, which way, the request ID and what Google answered. No
-- subject, sender or text of the mail is kept. Written after the call; a request ID already recorded returns its record and calls nothing.
CREATE TABLE gmail_mail_archive (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 thread_id TEXT NOT NULL CHECK(length(thread_id) BETWEEN 1 AND 32),
 action TEXT NOT NULL CHECK(action IN ('archive','unarchive')),
 status TEXT NOT NULL CHECK(status IN ('done','failed')),
 detail TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TRIGGER gmail_mail_archive_immutable BEFORE UPDATE ON gmail_mail_archive BEGIN SELECT RAISE(ABORT,'Archive records are append-only'); END;
CREATE TRIGGER gmail_mail_archive_retained BEFORE DELETE ON gmail_mail_archive BEGIN SELECT RAISE(ABORT,'Archive records are retained'); END;
