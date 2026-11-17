# Current module decisions

`AGENTS.md` links every binding contract and defines the cross-cutting invariants.
Each file has a stable rule ID. The detailed module contracts were moved intact from
AGENTS.md; quotations and dates in these files are decision provenance, not expired rules.
The assistant contract records the explicitly authorized 2026-10-01 amendments.

Add or revise the current contract when behavior changes. Put abandoned alternatives,
chronological investigation and superseded full snapshots under `docs/history/`, clearly
marked non-binding. Never make an agent infer current policy from a historical snapshot.
Keep root AGENTS.md small; use its index and bounded `read_docs`/`read_source` reads.
