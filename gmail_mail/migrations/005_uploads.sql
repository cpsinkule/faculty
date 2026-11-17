-- Files from the instructor's computer attached in the Mail page's compose window (requirement, 2026-10-01: an Attach button in the
-- compose window, from the computer and from Faculty). WORKSPACE-owned like the send they belong to, append-only, and written in the
-- same transaction as the send's intent, before the request leaves. The file is never stored: each row keeps its name in the message,
-- media type, size and SHA-256. `position` is its place among ALL the message's attachments; the files from Faculty take the other
-- positions in gmail_mail_send_attachments.
CREATE TABLE gmail_mail_send_uploads (
 send_id TEXT NOT NULL REFERENCES gmail_mail_sends(id),
 position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 99),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 200),
 size INTEGER NOT NULL CHECK(size >= 0),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 PRIMARY KEY(send_id, position)
);
CREATE TRIGGER gmail_mail_send_uploads_immutable BEFORE UPDATE ON gmail_mail_send_uploads BEGIN SELECT RAISE(ABORT,'Sent mail upload records are append-only'); END;
CREATE TRIGGER gmail_mail_send_uploads_retained BEFORE DELETE ON gmail_mail_send_uploads BEGIN SELECT RAISE(ABORT,'Sent mail upload records are retained'); END;
