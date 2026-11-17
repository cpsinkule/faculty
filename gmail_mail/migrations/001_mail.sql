-- Mail sent from Faculty's Mail page (requirement, 2026-09-28). WORKSPACE-owned: the instructor's own
-- outgoing mail belongs to no course, term or section. Faculty keeps NO copy of anything it reads from Gmail; only what it sends is
-- recorded, so the instructor can see what was approved and what Google answered. Both tables are append-only.
--
-- `gmail_mail_sends` is the intent, written and committed BEFORE the request leaves; `gmail_mail_outcomes` says what came of it
-- (the latest counts). A send with no outcome was interrupted: it is shown as uncertain and never sent again.
CREATE TABLE gmail_mail_sends (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
 sender TEXT NOT NULL,
 to_json TEXT NOT NULL CHECK(json_valid(to_json) AND json_type(to_json)='array'),
 cc_json TEXT NOT NULL CHECK(json_valid(cc_json) AND json_type(cc_json)='array'),
 bcc_json TEXT NOT NULL CHECK(json_valid(bcc_json) AND json_type(bcc_json)='array'),
 subject TEXT NOT NULL,
 body_text TEXT NOT NULL,
 reply_thread_id TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE gmail_mail_outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 send_id TEXT NOT NULL REFERENCES gmail_mail_sends(id),
 status TEXT NOT NULL CHECK(status IN ('accepted','failed','uncertain')),
 message_id TEXT NOT NULL DEFAULT '',
 thread_id TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE INDEX gmail_mail_outcomes_send ON gmail_mail_outcomes(send_id, id);
CREATE TRIGGER gmail_mail_sends_immutable BEFORE UPDATE ON gmail_mail_sends BEGIN SELECT RAISE(ABORT,'Sent mail records are append-only'); END;
CREATE TRIGGER gmail_mail_sends_retained BEFORE DELETE ON gmail_mail_sends BEGIN SELECT RAISE(ABORT,'Sent mail records are retained'); END;
CREATE TRIGGER gmail_mail_outcomes_immutable BEFORE UPDATE ON gmail_mail_outcomes BEGIN SELECT RAISE(ABORT,'Sent mail outcomes are append-only'); END;
CREATE TRIGGER gmail_mail_outcomes_retained BEFORE DELETE ON gmail_mail_outcomes BEGIN SELECT RAISE(ABORT,'Sent mail outcomes are retained'); END;
