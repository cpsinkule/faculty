-- RESPONSE-CARDS "REMINDERS BEFORE THE EXAM" (requirement, 2026-10-15): a card's mail may also be a reminder. SQLite cannot widen a
-- CHECK in place, so the table is rebuilt with every row, column and index kept, and a reminder is keyed by its days-before number.
CREATE TABLE response_card_mail_rebuilt (
 id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES response_card_recipients(id),
 kind TEXT NOT NULL CHECK(kind IN ('invitation','confirmation','reminder')), submission_id TEXT NOT NULL DEFAULT '',
 recipient TEXT NOT NULL, subject TEXT NOT NULL, text_body TEXT NOT NULL, html_body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 not_before INTEGER NOT NULL DEFAULT 0,
 provider_receipt_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 provider TEXT NOT NULL DEFAULT '', gmail_sender TEXT NOT NULL DEFAULT ''
);
INSERT INTO response_card_mail_rebuilt(id,card_id,kind,submission_id,recipient,subject,text_body,html_body,status,error,attempts,not_before,
 provider_receipt_json,created_at,updated_at,provider,gmail_sender)
 SELECT id,card_id,kind,submission_id,recipient,subject,text_body,html_body,status,error,attempts,not_before,
 provider_receipt_json,created_at,updated_at,provider,gmail_sender FROM response_card_mail;
DROP TABLE response_card_mail;
ALTER TABLE response_card_mail_rebuilt RENAME TO response_card_mail;
CREATE INDEX response_card_mail_queue ON response_card_mail(status,created_at);
CREATE UNIQUE INDEX response_card_mail_confirmation ON response_card_mail(card_id,submission_id) WHERE kind='confirmation';
CREATE INDEX response_card_mail_card ON response_card_mail(card_id, created_at);
CREATE UNIQUE INDEX response_card_mail_reminder ON response_card_mail(card_id,submission_id) WHERE kind='reminder';
