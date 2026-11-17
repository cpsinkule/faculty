-- Instructor-requested sorting of scan pages by a printed version label. Requests,
-- per-page readings and output files belong to the source Inbox message. Sorting
-- never identifies paper, clears the review gate or executes an action.
CREATE TABLE IF NOT EXISTS scan_version_sorts (
 message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 attempt INTEGER NOT NULL CHECK(attempt>=1),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 settings_json TEXT NOT NULL, actor TEXT NOT NULL,
 pages_total INTEGER NOT NULL DEFAULT 0 CHECK(pages_total>=0),
 result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(message_id,attempt)
);
CREATE INDEX IF NOT EXISTS scan_version_sort_queue ON scan_version_sorts(status,created_at);
-- One live request per scan: the queue never holds duplicates for one message.
CREATE UNIQUE INDEX IF NOT EXISTS scan_version_sort_active ON scan_version_sorts(message_id) WHERE status IN ('pending','running');
CREATE TABLE IF NOT EXISTS scan_version_reads (
 message_id TEXT NOT NULL, attempt INTEGER NOT NULL,
 scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 outcome TEXT NOT NULL DEFAULT '' CHECK(outcome IN ('','label','none','conflict','blank')),
 label TEXT, reading_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(message_id,attempt,scan_page_id),
 FOREIGN KEY(message_id,attempt) REFERENCES scan_version_sorts(message_id,attempt)
);
CREATE INDEX IF NOT EXISTS scan_version_read_queue ON scan_version_reads(message_id,attempt,status);
CREATE TRIGGER IF NOT EXISTS scan_version_read_owner BEFORE INSERT ON scan_version_reads
 WHEN NEW.message_id IS NOT (SELECT message_id FROM scan_pages WHERE id=NEW.scan_page_id)
 BEGIN SELECT RAISE(ABORT,'Version readings must retain their source Inbox owner'); END;
CREATE TRIGGER IF NOT EXISTS scan_version_sort_identity BEFORE UPDATE OF message_id,attempt,settings_json,actor,pages_total,created_at ON scan_version_sorts
 BEGIN SELECT RAISE(ABORT,'Version sort requests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_version_sort_completed BEFORE UPDATE ON scan_version_sorts WHEN OLD.status='complete'
 BEGIN SELECT RAISE(ABORT,'Finished version sorts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_version_sort_retained BEFORE DELETE ON scan_version_sorts
 BEGIN SELECT RAISE(ABORT,'Version sort requests are retained'); END;
CREATE TRIGGER IF NOT EXISTS scan_version_read_identity BEFORE UPDATE OF message_id,attempt,scan_page_id,created_at ON scan_version_reads
 BEGIN SELECT RAISE(ABORT,'Version reading identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_version_read_completed BEFORE UPDATE ON scan_version_reads WHEN OLD.status='complete'
 BEGIN SELECT RAISE(ABORT,'Finished version readings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_version_read_retained BEFORE DELETE ON scan_version_reads
 BEGIN SELECT RAISE(ABORT,'Version readings are retained'); END;
