# Jev decisions

The binding contract is [JEV](../docs/decisions/jev.md). This workspace-owned
adapter calls TypeSafe's decision API, never a chat or grading endpoint. It reads
`TYPESAFE_API_KEY` from the services' existing configured environment. Credentials
are never returned or retained. `FACULTY_JEV_ENABLED=0` disables these integrations.
Text checks are pinned to `jev-1.13.0` (`client.MODEL`); a check that looks at scanned
pictures asks `jev-1.14.0` (`client.IMAGE_MODEL`, JEV PICTURES). Whose paper a first page is asks Google's Gemini Decisions
(`gemini_decisions.py`, `GEMINI_API_KEY`; JEV STUDENT MATCH PROVIDER) when its key is configured, and Jev otherwise.

Calls retain input fingerprints and question definitions, answers, usage, and
outcomes in the backed-up catalog, without copying email bodies or Assistant
source text. An exact call is reused; failed, interrupted, and uncertain calls
are not resent automatically. The follow-up page offers an explicit retry and Retry all failed.
A host-level failure is flagged in the receipt (`host`, `refused`; never host text): triage
pauses on it, and asks a refused conversation again after the pause (JEV HOST FAILURES).
`JevService.triage_status()` gives a page the triage provider, model, paused line and failed count.

The Inbox worker classifies one current conversation per fair worker turn. Pages
only read results. The cutoff, exact conversation identities, source revisions,
and instructor overrides control applicability. Absence and Assistant checks
run inside their existing busy conversation, before the answer is released.
All checks use scripted/fake responses in the `tests/test_jev*.py` modules (`test_jev`,
`test_jev_dates`, `test_jev_send_checks`, `test_jev_routing`, `test_jev_canvas_match`,
`test_jev_papers`, `test_jev_bank_tags`).

Further uses (2026-10-03): `routing.py` (Ctrl K palette), `matching.py` (Canvas
import), `send_checks.py` (notes beside a message's review), `dates.py` (the days and clock
times a message names, read in code), `checks.py` (the advisory absence and Assistant checks and
the relative-date hints triage reads) and the urgency and wellbeing questions of the follow-up
triage. See the contract's additions section.

Tool-use guidance (`assistant/tool_guidance.py`) runs inside the current assistant turn, across all in-app engines and opted-in
MCP sessions. It suggests existing tools, checks proposed action intent/context, reviews selected results, and checks completion.
The ordinary route policy remains authoritative. Seven checks at most; a failed check is never automatically resent.
