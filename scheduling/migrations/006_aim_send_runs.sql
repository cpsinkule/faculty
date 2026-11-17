-- SCHEDULING "SEND TO AIM" › AUTOMATIC (requirement, 2026-10-27): every Send to AIM, the instructor's click or Faculty's own weekday start, and how it ended. An
-- automatic send is started at most once a campus day per term; one that ended without sending (Duo not approved, no saved sign-in,
-- a page not as mapped) puts "AIM send waiting for you" on Home until a later send of the term ends sent.
-- Owner: the course term (course_terms.id). Append-only: a run, then at most one end.
CREATE TABLE aim_send_runs (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 100),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 trigger TEXT NOT NULL CHECK(trigger IN ('instructor','automatic')),
 day TEXT NOT NULL CHECK(length(day) = 10),
 quizzes INTEGER NOT NULL CHECK(quizzes >= 0),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX aim_send_runs_term ON aim_send_runs(term_id, trigger, day);
CREATE TRIGGER aim_send_runs_immutable BEFORE UPDATE ON aim_send_runs
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM sends are immutable'); END;
CREATE TRIGGER aim_send_runs_retained BEFORE DELETE ON aim_send_runs
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM sends are retained'); END;
-- `sent`: it signed in and did what it could (each write's own outcome is in aim_send_outcomes); `not_signed_in`: Duo or the
-- sign-in needed the instructor and an automatic send does not wait; `stopped`: a page not as mapped or a failure; `closed`: the
-- window closed, or the browser ended, first.
CREATE TABLE aim_send_run_ends (
 run_id TEXT PRIMARY KEY REFERENCES aim_send_runs(id),
 state TEXT NOT NULL CHECK(state IN ('sent','not_signed_in','stopped','closed')),
 detail TEXT NOT NULL CHECK(length(detail) <= 500),
 created_at TEXT NOT NULL
);
CREATE TRIGGER aim_send_run_ends_immutable BEFORE UPDATE ON aim_send_run_ends
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM send ends are immutable'); END;
CREATE TRIGGER aim_send_run_ends_retained BEFORE DELETE ON aim_send_run_ends
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM send ends are retained'); END;
