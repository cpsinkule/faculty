-- MAKEUP HOLDS (ABSENCES "MAKEUP BOOKING"; requirement, 2026-10-06). Asked, they chose: a hold lasts until they release it (an optional check-back date flags it to them that day), one hold
-- covers every quiz makeup the absence owes, and the follow-up assistant may place one by itself, shown with Undo.
--
-- absence_makeup_holds: whether an absence's quiz makeups wait for the instructor: no booking card goes out (an unanswered one is
-- withdrawn) and no makeup time is offered while held. Owner: the absence (so its student's section membership), a revision per
-- change, append-only; an absence with no row is not held. `check_back` is the campus day the hold is flagged to the instructor.
CREATE TABLE IF NOT EXISTS absence_makeup_holds (
 absence_id TEXT NOT NULL REFERENCES absences(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 held INTEGER NOT NULL CHECK(held IN (0, 1)),
 reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 300),
 check_back TEXT CHECK(check_back IS NULL OR check_back GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(absence_id, revision)
);
CREATE TRIGGER IF NOT EXISTS absence_makeup_holds_next BEFORE INSERT ON absence_makeup_holds
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM absence_makeup_holds WHERE absence_id=NEW.absence_id), 0)
BEGIN SELECT RAISE(ABORT,'A makeup hold must follow the latest one'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_holds_immutable BEFORE UPDATE ON absence_makeup_holds
BEGIN SELECT RAISE(ABORT,'Makeup holds are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeup_holds_retained BEFORE DELETE ON absence_makeup_holds
BEGIN SELECT RAISE(ABORT,'Makeup holds are retained'); END;
