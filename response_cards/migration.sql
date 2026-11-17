CREATE TABLE IF NOT EXISTS response_card_templates (
 id TEXT PRIMARY KEY, definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 name TEXT NOT NULL, schema_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS response_card_versions (
 id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES response_card_templates(id),
 number INTEGER NOT NULL, template_revision INTEGER NOT NULL, schema_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(template_id,number), UNIQUE(template_id,template_revision)
);
CREATE TABLE IF NOT EXISTS response_card_issues (
 id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES response_card_versions(id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 assignment_id TEXT REFERENCES assignments(id), seating_chart_id TEXT REFERENCES seating_charts(id) ON DELETE SET NULL,
 context_json TEXT NOT NULL, policy TEXT NOT NULL, due_at TEXT, expires_at TEXT,
 request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS response_card_recipients (
 id TEXT PRIMARY KEY, issue_id TEXT NOT NULL REFERENCES response_card_issues(id),
 section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 student_name TEXT NOT NULL, email TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
 snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
 publish_status TEXT NOT NULL DEFAULT 'pending', published_url TEXT,
 status TEXT NOT NULL DEFAULT 'issued', delivery_status TEXT NOT NULL DEFAULT 'not_sent',
 current_submission_id TEXT, remote_cursor INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(issue_id,section_id,enrollment_id),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE TABLE IF NOT EXISTS response_card_submissions (
 id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES response_card_recipients(id),
 request_id TEXT NOT NULL, submission_json TEXT NOT NULL, submitted_at TEXT NOT NULL,
 received_at TEXT NOT NULL, UNIQUE(card_id,request_id)
);
CREATE TABLE IF NOT EXISTS response_card_events (
 cursor INTEGER PRIMARY KEY, card_id TEXT NOT NULL REFERENCES response_card_recipients(id),
 event_json TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS response_card_sync (
 id INTEGER PRIMARY KEY CHECK(id=1), cursor INTEGER NOT NULL DEFAULT 0,
 remote_url TEXT NOT NULL DEFAULT '', synced_at TEXT, error TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO response_card_sync(id) VALUES(1);
CREATE TABLE IF NOT EXISTS response_card_controls (
 request_id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES response_card_recipients(id),
 action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, completed_at TEXT
);
-- Invalidation belongs to the retained card, whose issue and membership retain
-- the term/section owners. It is authoritative locally even before remote ACK.
CREATE TABLE IF NOT EXISTS response_card_invalidations (
 card_id TEXT PRIMARY KEY REFERENCES response_card_recipients(id),
 reason TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','complete')),
 error TEXT NOT NULL DEFAULT '', not_before INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS response_card_invalidations_pending ON response_card_invalidations(status,not_before);
CREATE INDEX IF NOT EXISTS response_cards_term ON response_card_issues(term_id,created_at);
CREATE INDEX IF NOT EXISTS response_cards_issue ON response_card_recipients(issue_id);
CREATE INDEX IF NOT EXISTS response_cards_history ON response_card_submissions(card_id,submitted_at);
CREATE TRIGGER IF NOT EXISTS response_template_owner BEFORE UPDATE OF id,definition_id ON response_card_templates
 BEGIN SELECT RAISE(ABORT,'Response Card template ownership is retained'); END;
CREATE TRIGGER IF NOT EXISTS response_version_scope BEFORE INSERT ON response_card_issues
 WHEN (SELECT t.definition_id FROM response_card_templates t JOIN response_card_versions v ON v.template_id=t.id WHERE v.id=NEW.version_id)
 IS NOT (SELECT definition_id FROM course_terms WHERE id=NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'Response Card version belongs to another course'); END;
CREATE TRIGGER IF NOT EXISTS response_recipient_scope BEFORE INSERT ON response_card_recipients
 WHEN (SELECT term_id FROM course_sections WHERE id=NEW.section_id)
 IS NOT (SELECT term_id FROM response_card_issues WHERE id=NEW.issue_id)
 BEGIN SELECT RAISE(ABORT,'Response Card recipient belongs to another term'); END;
CREATE TRIGGER IF NOT EXISTS response_recipient_snapshot BEFORE UPDATE OF id,issue_id,section_id,enrollment_id,token,snapshot_json,snapshot_hash,student_name,email ON response_card_recipients
 BEGIN SELECT RAISE(ABORT,'Issued Response Card snapshots are immutable'); END;
CREATE TABLE IF NOT EXISTS response_card_mail (
 id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES response_card_recipients(id),
 kind TEXT NOT NULL CHECK(kind IN ('invitation','confirmation')), submission_id TEXT NOT NULL DEFAULT '',
 recipient TEXT NOT NULL, subject TEXT NOT NULL, text_body TEXT NOT NULL, html_body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 not_before INTEGER NOT NULL DEFAULT 0,
 provider TEXT NOT NULL DEFAULT '', gmail_sender TEXT NOT NULL DEFAULT '',
 provider_receipt_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS response_card_mail_queue ON response_card_mail(status,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS response_card_mail_confirmation ON response_card_mail(card_id,submission_id) WHERE kind='confirmation';
