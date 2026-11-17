-- Move to Trash on the Mail page (requirement, 2026-09-28). WORKSPACE-owned,
-- append-only, and only what Faculty did: the thread ID, which way (trash or untrash), the request ID and what Google answered. No subject,
-- sender or text of the mail is kept. Trash is recoverable (Gmail keeps it 30 days) and repeating it changes nothing, so the record is
-- written after the call; a request ID that is already recorded returns its record and calls nothing.
CREATE TABLE gmail_mail_trash (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 thread_id TEXT NOT NULL CHECK(length(thread_id) BETWEEN 1 AND 32),
 action TEXT NOT NULL CHECK(action IN ('trash','untrash')),
 status TEXT NOT NULL CHECK(status IN ('done','failed')),
 detail TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TRIGGER gmail_mail_trash_immutable BEFORE UPDATE ON gmail_mail_trash BEGIN SELECT RAISE(ABORT,'Trash records are append-only'); END;
CREATE TRIGGER gmail_mail_trash_retained BEFORE DELETE ON gmail_mail_trash BEGIN SELECT RAISE(ABORT,'Trash records are retained'); END;
