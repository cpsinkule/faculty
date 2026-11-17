PRAGMA foreign_keys = ON;
CREATE TABLE public_cards (
  id TEXT PRIMARY KEY,
  issue_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  snapshot_json TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL,
  policy TEXT NOT NULL CHECK(policy IN ('once','until_due','unlimited','instructor_unlock')),
  due_at TEXT,
  expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'issued',
  revoked INTEGER NOT NULL DEFAULT 0,
  unlocked INTEGER NOT NULL DEFAULT 0,
  submission_count INTEGER NOT NULL DEFAULT 0,
  current_submission_id TEXT,
  draft_json TEXT,
  draft_updated_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE submissions (
  id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL REFERENCES public_cards(id),
  request_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  answers_json TEXT NOT NULL,
  verification_json TEXT NOT NULL,
  status TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  UNIQUE(card_id, request_id)
);
CREATE INDEX submissions_card ON submissions(card_id, submitted_at);
CREATE TABLE card_events (
  cursor INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL REFERENCES public_cards(id),
  type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE card_controls (
  card_id TEXT NOT NULL REFERENCES public_cards(id),
  request_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('revoke','unlock')),
  created_at TEXT NOT NULL,
  PRIMARY KEY(card_id, request_id)
);
CREATE TRIGGER card_created AFTER INSERT ON public_cards BEGIN
  INSERT INTO card_events(card_id,type,created_at,data_json)
  VALUES(NEW.id,'status',NEW.created_at,json_object('status',NEW.status,'submission_count',0));
END;
CREATE TRIGGER card_status AFTER UPDATE OF status ON public_cards WHEN NEW.status != OLD.status BEGIN
  INSERT INTO card_events(card_id,type,created_at,data_json)
  VALUES(NEW.id,'status',strftime('%Y-%m-%dT%H:%M:%fZ','now'),
    json_object('status',NEW.status,'submission_count',NEW.submission_count));
END;
CREATE TRIGGER submission_recorded AFTER INSERT ON submissions BEGIN
  UPDATE public_cards SET current_submission_id=NEW.id, submission_count=submission_count+1,
    status=NEW.status, unlocked=0, draft_json=NULL, draft_updated_at=NULL WHERE id=NEW.card_id;
  INSERT INTO card_events(card_id,type,created_at,data_json)
  VALUES(NEW.card_id,'submission',NEW.submitted_at,json_object('status',NEW.status,'submission',json_object(
    'id',NEW.id,'request_id',NEW.request_id,'answers',json(NEW.answers_json),
    'verification',json(NEW.verification_json),'status',NEW.status,'submitted_at',NEW.submitted_at)));
END;
CREATE TRIGGER submission_immutable_update BEFORE UPDATE ON submissions BEGIN
  SELECT RAISE(ABORT,'Submissions are immutable');
END;
CREATE TRIGGER submission_immutable_delete BEFORE DELETE ON submissions BEGIN
  SELECT RAISE(ABORT,'Submissions are immutable');
END;
CREATE TRIGGER snapshot_immutable BEFORE UPDATE OF id,issue_id,token_hash,snapshot_json,snapshot_hash,policy,due_at,expires_at ON public_cards BEGIN
  SELECT RAISE(ABORT,'Published snapshots are immutable');
END;
