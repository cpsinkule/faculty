-- A template location check records the local reading model's address it was queued with, and a reading refuses to run when
-- the configured address differs ("Retry this verification explicitly"). A retry kept that recorded address, so after the model
-- was connected or moved no retry could ever read (requirement, 2026-09-25: 16 checks of a plan stuck that way). An explicit retry
-- of a FAILED check may now record the current address, and nothing else about the check: its inputs stay immutable, and the
-- retry's event keeps the address it replaced.
DROP TRIGGER frq_location_input_immutable;
CREATE TRIGGER frq_location_input_immutable BEFORE UPDATE OF assignment_id,question_id,input_sha256,input_json,profile_json ON frq_location_checks
 WHEN NEW.assignment_id!=OLD.assignment_id OR NEW.question_id!=OLD.question_id OR NEW.input_sha256!=OLD.input_sha256
  OR NEW.input_json!=OLD.input_json
  OR (NEW.profile_json!=OLD.profile_json AND NOT (OLD.status='failed' AND NEW.status='pending' AND NEW.attempt=OLD.attempt+1))
 BEGIN SELECT RAISE(ABORT,'Location check inputs are immutable'); END;
