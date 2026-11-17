-- Quiz makeup booking (ABSENCES "MAKEUP BOOKING"; requirement, 2026-10-04): a student who owes a quiz makeup for an excused absence
-- is sent a Response Card offering times (absences/makeup_slots.py); their pick seats them in that time's makeup sitting of the exam
-- record. Everything is append-only.
--
-- absence_makeup_settings: whether cards go out by themselves for a TERM (owner: course_terms.id), a revision per change, off until
-- the instructor turns it on.
-- absence_makeup_offers: the times one card offered (owner: the student's section membership on a term exam record, for one absence;
-- `attempt` counts the cards sent for that absence, "Send a new card" adding one). The card's issue names the offer by reference
-- (response_card_issues.context_json makeup_offer_id); the offer never copies the card.
-- absence_makeup_picks: what Faculty did with one submitted card (once per submission): the student seated in a makeup sitting, a
-- request for another time (their own words), or a pick it could not act on and why.
CREATE TABLE IF NOT EXISTS absence_makeup_settings (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 cards INTEGER NOT NULL CHECK(cards IN (0, 1)),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id, revision)
);
CREATE TRIGGER IF NOT EXISTS absence_makeup_settings_next BEFORE INSERT ON absence_makeup_settings
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM absence_makeup_settings WHERE term_id=NEW.term_id), 0)
BEGIN SELECT RAISE(ABORT,'A makeup booking setting must follow the latest one'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_settings_immutable BEFORE UPDATE ON absence_makeup_settings
BEGIN SELECT RAISE(ABORT,'Makeup booking settings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_settings_retained BEFORE DELETE ON absence_makeup_settings
BEGIN SELECT RAISE(ABORT,'Makeup booking settings are retained'); END;

CREATE TABLE IF NOT EXISTS absence_makeup_offers (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 event_id TEXT NOT NULL REFERENCES exam_events(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 absence_id TEXT NOT NULL REFERENCES absences(id),
 absence_revision INTEGER NOT NULL CHECK(absence_revision >= 1),
 attempt INTEGER NOT NULL CHECK(attempt >= 1),
 given_date TEXT NOT NULL CHECK(given_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
 due_date TEXT NOT NULL CHECK(due_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
 week TEXT NOT NULL CHECK(week GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
 options_json TEXT NOT NULL CHECK(json_valid(options_json) AND json_type(options_json)='array' AND json_array_length(options_json) BETWEEN 1 AND 40),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(event_id, section_id, enrollment_id, absence_id, attempt),
 FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
);
CREATE INDEX IF NOT EXISTS absence_makeup_offers_member ON absence_makeup_offers(event_id, section_id, enrollment_id);
CREATE TRIGGER IF NOT EXISTS absence_makeup_offers_next BEFORE INSERT ON absence_makeup_offers
WHEN NEW.attempt != 1 + COALESCE((SELECT MAX(attempt) FROM absence_makeup_offers WHERE event_id=NEW.event_id AND section_id=NEW.section_id
  AND enrollment_id=NEW.enrollment_id AND absence_id=NEW.absence_id), 0)
BEGIN SELECT RAISE(ABORT,'A makeup card must follow the latest one'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_offers_immutable BEFORE UPDATE ON absence_makeup_offers
BEGIN SELECT RAISE(ABORT,'Makeup offers are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_offers_retained BEFORE DELETE ON absence_makeup_offers
BEGIN SELECT RAISE(ABORT,'Makeup offers are retained'); END;

CREATE TABLE IF NOT EXISTS absence_makeup_picks (
 submission_id TEXT PRIMARY KEY,
 offer_id TEXT NOT NULL REFERENCES absence_makeup_offers(id),
 card_id TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('seated','other_time','stale','failed')),
 choice TEXT NOT NULL CHECK(length(choice) <= 4000),
 sitting_id TEXT,
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS absence_makeup_picks_offer ON absence_makeup_picks(offer_id);
CREATE TRIGGER IF NOT EXISTS absence_makeup_picks_immutable BEFORE UPDATE ON absence_makeup_picks
BEGIN SELECT RAISE(ABORT,'Makeup picks are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_picks_retained BEFORE DELETE ON absence_makeup_picks
BEGIN SELECT RAISE(ABORT,'Makeup picks are retained'); END;
