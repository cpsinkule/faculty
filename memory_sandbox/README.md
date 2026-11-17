# Memory sandbox

The instructor asked on 2026-10-10 for a long-term memory for the Assistant, and chose to sandbox it first: Jev-Mem and a flat
in-Faculty pipeline, measured on invented cases, with nothing integrated until the numbers have been read. This package is
workspace tooling: `app.py` never imports it, it adds no route, page, migration or setting, and nothing it does reaches a record.

## What it compares

- **Today.** Preferences as they are: only what the instructor explicitly asks to keep, as typed, all of it pasted into every
  request (`assistant/store.py`, `assistant/conversations.py`).
- **The flat pipeline** (`pipeline.Flat`). When the instructor types something: one `capture` request, the nearest kept
  memories, one `consolidate` request, then save, skip, refuse or replace. When a later request is read: the nearest kept
  memories, one `relevance` request, the few that pass. Run with Jev and with OpenAI Decisions, and with EmbeddingGemma 2 (768
  and 256 dimensions), MiniLM and no embedder at all.
- **Jev-Mem** at its pinned commit, as shipped and with its admission step on, each also with EmbeddingGemma 2 (`jevmem.py`).

The sandbox has no generative model. The memory's wording is therefore bracketed: `raw` keeps the instructor's sentence as
typed, `gold` keeps a hand-written standalone sentence. The gap between the two says whether a rewrite step is needed.

## Every kept memory at once (`whole.py`, 2026-10-13)

The instructor, after SubQ Snap joined the Decisions sandbox: "with the context so large in snapq could we test having it
decide which memories are relevant by giving all of them at once and it ranking them on relevance". `manage_memory_sandbox.py
whole` measures that on the same 80 later requests, on the store a perfect write path leaves, at its ordinary size (169 to 193
memories in view) and padded with a thousand neutral rows:

- **rank**: one request to Snap, a Choice over every memory in view (254 to a Choice, both option orders), the memories
  ordered by the probability each gets;
- **rank, then jev / snap / snap by text**: the first eight of that ranking (joined with keyword hits, as today) put to the
  relevance request, answered by Jev, by Snap, or by Snap with each memory's text quoted in its own question;
- **rank alone**: a memory is given when its probability is at a floor fitted on the fit families and above "none of these";
- **each**: a yes/no for every memory with the whole store in the state.

Measured (run `whole-20261013`; invented text only, to SubQ and TypeSafe; "today" is the embedder eg2-768 then Jev, from
`run-20261010`):

| | needed in the first 8 (110) | narrow / broad | given right / arguable / wrong | given for the 10 that need none |
|---|---|---|---|---|
| today | 77 | 63 of 73 / 9 of 31 | 72 / 18 / 3 | 1 |
| snap ranks, alone (floor .2) | 97 | 72 of 73 / 19 of 31 | 72 / 4 / 4 | 4 |
| snap ranks, jev picks | 97 | the same | 86 / 34 / 22 | 4 |
| snap ranks, snap picks | 97 | the same | 90 / 50 / 45 | 8 |
| snap ranks, snap picks by text | 97 | the same | 85 / 61 / 89 | 9 |
| snap, a yes/no for each | 17 | 15 of 73 / 2 of 31 | 12 / 8 / 326 | 5 |

- The ranking is a better search than the embedder and holds on the padded store (95 in the first 8, today 77); what it
  gives alone is about what today's design gives, in one request of about 8,500 tokens and half a second (69,000 tokens and
  about a second padded).
- Snap is poor at the yes/no relevance question in every form: with the whole list in view it called a median of 147 of 193
  memories relevant.
- Four padded rankings at once met SubQ's rate limit (17 of 80 refused, HTTP 429); `--retry NAME --workers 1` asked those
  again, as separately identified requests. Nothing is resent without that.
- Not measured: real kept memories. Every kept memory would leave the machine on every request, where today the search is
  local and Jev is shown eight.

## The cases (`cases/*.json`)

All invented: two courses, three terms, sixteen made-up students. Every label is written with its case.

- 241 instructor messages: lasting preferences, course facts and term facts; passing requests; restatements; updates that must
  replace; narrower exceptions that must not; statements naming a student; ordinary sentences that use a roster name as a
  word ("Grant extensions…"); instructions that appear only in quoted mail, a scan or tool output; student text pasted into the
  instructor's own message; record-like facts a calendar or gradebook already owns.
- 80 later requests, each with the memories it should use, the arguable ones, and the ones it must not.
- 150 neutral memories, 20 past-work summaries, 30 follow-up lessons with 35 student requests.

A statement, its restatements, its update and its exceptions form a `family`; a family is wholly `fit` or wholly `test`.

## Data and safety

- **Scripted unless `--live`.** Without `--live` every answer comes from the cases' own labels and no request leaves the
  machine. A live command needs a run name and `--budget-calls`; it prints its upper bound first and stops at the budget. A
  run folder holds scripted answers or live ones, never both.
- **What a live run sends.** Invented text only, to TypeSafe (Jev) and OpenAI (Decisions), and for `whole` to SubQ (Snap). No state ever holds a case's
  quoted mail, scan or tool text: `pipeline.Flat.write` does not read it, and a check asserts it.
- **Run folders.** `/mnt/nvme/Teaching/tmp/memory-sandbox/<run>/` (0700): `calls.jsonl` (hashes, questions, answers, usage,
  timing; never the state), `results-components.jsonl`, `stories.jsonl`, `probe.json`, and the reports (ids and numbers only).
  A rerun reads `calls.jsonl` and recovers unfinished `pending-calls` intents as
  uncertain, resending nothing. Requests refused locally by the call budget remain
  unsent and may run after that budget is increased.
- **Runtime.** `/mnt/nvme/Teaching/runtime/memory-sandbox/`: its own venv, model weights pinned by revision and SHA-256
  (`model-lock.json`), the Jev-Mem checkout at its pinned commit (read-only), and the vector cache. `fetch --download` is the
  only step that downloads. The parent process never imports a model.
- **Children.** Every model runs in a bubblewrap jail with no network, no home folder, no Faculty checkout beyond this package's
  `children` scripts and no catalog; only its run folder is writable. The live Jev-Mem child alone gets network and
  `TYPESAFE_API_KEY`, and no other key.
- **Credentials.** `safety.scrub_environment` (the Decisions sandbox's) strips every deployment setting and credential except
  the keys in `decisions_sandbox.safety.KEYS` (TypeSafe, OpenAI, SubQ, OpenRouter) that the server's key files hold, read from
  those files and never printed. This sandbox uses the first three (Jev, Decisions, Snap); the parent process holds them, and
  of the jailed children only the live Jev-Mem child sees one, TypeSafe's (see Children).

## Order of a run

    manage_memory_sandbox.py cases
    manage_memory_sandbox.py fetch --download
    manage_memory_sandbox.py audit      --run NAME     # read audit.txt before anything of Jev-Mem runs
    manage_memory_sandbox.py probe      --run NAME
    manage_memory_sandbox.py embed      --run NAME
    manage_memory_sandbox.py components --run NAME --live --budget-calls N
    manage_memory_sandbox.py sweep      --run NAME
    manage_memory_sandbox.py stories    --run NAME --live --budget-calls N [--gates proposed]
    manage_memory_sandbox.py lessons    --run NAME
    manage_memory_sandbox.py jevmem     --run NAME --live --budget-calls N
    manage_memory_sandbox.py report     --run NAME

## Reading the results

- `report.md`: each decision on its own (AUC, accuracy by threshold, agreement between the two models and the two option orders).
- `calibration-memory.md`: the gates `sweep` proposes, as counts on the fit and held-out families. The rule: no forbidden
  outcome first (a passing remark kept, a student's statement kept, a memory wrongly replaced or dropped), then the most
  wanted outcomes, ties to the stricter gate. The gates are the instructor's to choose.
- `memory-report.md`: every arm end to end.

Two results are true by construction and are not findings: no arm keeps an instruction that appears only in quoted text,
because that text is never passed; and Jev-Mem as shipped keeps every observation and never replaces one.
