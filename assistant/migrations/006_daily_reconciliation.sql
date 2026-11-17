-- ASSISTANT: workspace-owned reconciliation rules now run at 05:00 campus time.
-- The worker schedules zero-valued rules at their next morning without checking off-hours.
-- Keep enablement, identity, receipts and readiness/watch schedules unchanged.
UPDATE assistant_workflows SET next_check=0 WHERE kind='reconciliation';
