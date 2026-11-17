-- Files attached to mail Faculty sends (requirement, 2026-10-01). WORKSPACE-owned like the send they belong to, append-only, and written in the same transaction as the send's
-- intent, before the request leaves. Each row is a REFERENCE to a file as it was sent: the Faculty route that served it, its name in
-- the message, its media type, size and SHA-256. The file itself stays with its owner and is never copied here.
CREATE TABLE gmail_mail_send_attachments (
 send_id TEXT NOT NULL REFERENCES gmail_mail_sends(id),
 position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 99),
 route TEXT NOT NULL CHECK(length(route) BETWEEN 6 AND 1000 AND substr(route, 1, 5) = '/api/'),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 200),
 size INTEGER NOT NULL CHECK(size >= 0),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 PRIMARY KEY(send_id, position)
);
CREATE TRIGGER gmail_mail_send_attachments_immutable BEFORE UPDATE ON gmail_mail_send_attachments BEGIN SELECT RAISE(ABORT,'Sent mail attachment records are append-only'); END;
CREATE TRIGGER gmail_mail_send_attachments_retained BEFORE DELETE ON gmail_mail_send_attachments BEGIN SELECT RAISE(ABORT,'Sent mail attachment records are retained'); END;
