-- Read paths only (performance audit, 2026-09-29); an index changes no row a query returns.
-- Which responses a result is current for: the batch card's resettable count, the job's states and every listing that joins a
-- response's current result (each scanned all of frq_responses per result).
CREATE INDEX IF NOT EXISTS frq_responses_current_result ON frq_responses(current_result_id);
-- A response's tasks: its latest task (MAX(rowid) per response, the batch-scoped review queue and the failed count) and the
-- worker's reusable crops (each scanned frq_tasks backward, so an older batch cost more with every later one).
CREATE INDEX IF NOT EXISTS frq_tasks_response ON frq_tasks(response_id);
-- An assignment's events of one kind (answer groups, batch lines, blind reveals, refusals): the table grows by one row per grade.
-- The rowid trails the key, so `ORDER BY id` still needs no sort.
CREATE INDEX IF NOT EXISTS frq_events_assignment_kind ON frq_events(assignment_id,kind);
-- The latest grade export of an assignment (progress.export_summary) is read from the index alone, never from the snapshot row.
CREATE INDEX IF NOT EXISTS frq_exports_latest ON frq_exports(assignment_id,kind,created_at,id);
