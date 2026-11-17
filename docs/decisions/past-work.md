## Past work

Rule ID: **PAST-WORK**. Current binding module contract. Read root `AGENTS.md` first, then ASSISTANT, ASSISTANT-MEMORY and
EMBEDDING.

- The instructor (2026-10-10), of a proposal to "Add past-work recall. Let the assistant find 'how we handled this
  before,' with links to the original work and confirmed outcomes": "what do you think about using embedding model to
  recall relevant ones"; then **"do the read-only replay for past-work recall"**; and, shown what it returned on their own
  records and offered "a read tool for the Assistant that returns carried-out actions first, grouped by matter, each with
  its outcome and a link, and that is never stored as memory": **"build it"**.
  `assistant/past_work.py`, `GET /api/assistant/past-work`, the tool `find_past_work` (`assistant/tools.py`,
  `assistant/guide.md`), `tests/test_assistant_past_work.py`.
- **WHAT IT IS.** A read. The Assistant's recorded actions (`assistant_actions`: what it proposed or did through Faculty's
  routes, each with the events that say how it ended) are found by describing a situation, so it can answer "how did we
  handle this before" from the record. It changes nothing, proposes nothing and decides nothing.
- **OWNER.** The actions are ASSISTANT's and are never altered. What this module owns is workspace computing: one SQLite
  file under `<nvme>/runtime/past-work-index/` (mode 0600 in a 0700 folder; `FACULTY_PAST_WORK_INDEX_DIR` moves it;
  STORAGE: never the root disk), a rebuildable cache that is not teaching data, is in no backup and owns no catalog table
  or migration. A row is an action's id, the model and revision, a checksum, and two vectors. No words, no name and no
  student is in it; deleting the file loses nothing, and the next search fills it again. What an action says and how it
  ended are always read from the catalog.
- **WHAT IS READ.** Each action is two texts: its summary, and its summary with the words of its own request
  (`own_words`: the request's sentences and a few short labels such as a title, a subject or a status; never an
  identifier). Both are embedded once by the local worker (EMBEDDING: one text of at most 1,500 characters a request). An
  action is immutable, so its vectors never go stale. Actions not read yet are read beside a search, in a thread of their
  own: a search waits 2 seconds for them, answers from what is read, and says how many are not (`not_read_yet`).
- **FINDING** (`PastWork.find`). The question is embedded; the 40 nearest actions by the better of their two texts are
  gathered by matter; the nearest few matters (5, at most 10) are returned, those where something was carried out first.
  - **A matter** is the student an action is about (`enrollment_id`), else the mail thread it answers, else the one
    address it was sent to, else the record its route names (one response, one absence, one follow-up); an action with
    none of these stands alone. So a reply's declined drafts, the reply that was sent, and a change that failed and was
    tried again are one result. What was carried out leads its matter; two more of its actions are listed and the rest
    counted by how they ended.
  - **Every result says how it ended**: carried out, declined by the instructor, expired without an answer, failed, or
    waiting, started but not confirmed, uncertain, partly carried out, or cancelled. Only "carried out" was done.
    **A card that gave way to another is not one the instructor declined** (requirement, 2026-11-09, of a review: "fix
    all of them"; of 552 cards on live whose last event read `declined`, 472 had been prepared again at their request
    and 58 edited by them, and each was told to the Assistant as "what they refused"; of 322 `expired`, 235 had been
    withdrawn because the conversation changed): its last event says what replaced it, and the result says "replaced by
    the instructor's edited version", "replaced by the reply the instructor sent from Mail", "replaced by a new draft the
    instructor asked for" or "withdrawn because the conversation changed" (`past_work.REPLACED`). None of these was done.
    `assistant/outcomes.py` reads the route's operation result, not merely the outer successful HTTP event. It reads
    later linked local receipts for sends, content pushes, regrade steps and the making of a Gradescope assignment,
    without polling outside services or resuming work. An absence's own status (such as `excused`) remains a record field. Learning, metrics and action
    completion use this same reader (requirement, 2026-10-13: "Implement these refined changes"). An unconfirmed
    action teaches nothing and cannot establish completion. "Cancelled" is said only when nothing was done; a regrade
    or a Canvas push cancelled, stopped or failed after part of it was done is "partly carried out", and a Canvas
    message one course did not confirm is "outcome uncertain" (ASSISTANT "STILL RUNNING IS NOT A PROBLEM"). Among the nearest few, what was carried out comes first; nothing farther off is
    brought forward for having been done.
  - A result carries the action's summary, the campus day, its outcome, the route it used, at most 400 characters of the
    request's own words, and the page that shows it when the action recorded one.
- **CANDIDATES, NEVER A VERDICT.** It returns the nearest, and the nearest is not always alike: on the replay, the best
  score for a question with nothing like it in the record (.72 to .80) was as high as for one with a true precedent (.75
  to .81). So there is no cutoff, no score is returned, and the tool and the primer tell the Assistant to read the
  results and to say plainly when none is the same situation.
- **WHO MAY READ.** The Assistant, through `find_past_work`: `assistant.past_work` joins `policy.GET_OWN` on the
  instructor's "build it". It opens no record the Assistant could not already read (its `catalog_sql` tool reads the
  catalog's tables, the action log among them, row by row); what is new is finding by situation. The action log's own
  route (`/api/assistant/actions`) and every card route stay the instructor's. The instructor has no page for past work;
  it was not asked for.
- **NOT A MEMORY, NOT AN INSTRUCTION.** Nothing found here is kept as a memory (ASSISTANT-MEMORY keeps only what the
  instructor typed), and past work never stands as a rule: what the instructor says now, what they asked to be
  remembered and the term's rules come first, and the Assistant reads the current record before acting on what it finds.
  Preparations and follow-ups are not told to consult it; it is used when the instructor asks how something was handled.
- **STUDENTS.** Actions name students and quote mail. Their texts go to the embedding worker on this machine and nowhere
  else (EMBEDDING "WHAT IS SENT" is amended for this); no decision service is asked anything to find past work. What a
  search returns reaches the Assistant's model as any other read of Faculty's records does.
- **WITHOUT THE WORKER.** When the embedding service is not set up or does not answer, the actions sharing words with
  the question are returned in the same shape (`matched_by: "words"`); nothing waits and nothing fails.
- **MEASURED** (2026-10-10, read-only on the live records: the catalog opened read-only, everything held in memory,
  student names masked in what was printed; 749 actions from September 29 to October 10). Sixteen questions were asked,
  three of them with nothing to find. By the search as built, every first result was something carried out, and for
  each of the 13 questions with an answer a fitting one was among the first three, by my reading. Before gathering and
  ordering, of the 96 nearest actions 55 were carried out, 27 declined, 11 expired and 3 waiting, and only 7 of the 16
  first results were carried out. The better of the two texts put a kept-grade regrade decision second where the
  request's words alone put it eighteenth ("denied" is recorded as "keep"). A question takes 34 ms to embed; the first
  fill of 749 actions took 49 seconds. Another model revision needs this looked at again.
- **NOT BUILT.** Narrowing by term, date or kind; a search box for the instructor. (Follow-up lessons on the same search were
  withdrawn on 2026-10-12: what a correction teaches is a kept memory, ASSISTANT-MEMORY "LEARNED FROM AN EDIT", and nothing
  found here becomes one.)
- **CHECKS.** Synthetic actions and a stand-in embedder only: what was carried out first and every outcome said; one
  matter one result; found by the summary or by the request's words; an action read once and an index with no words in
  it, filled again when deleted; a first fill going on beside the request; shared words standing in; and the tool
  reading through its own route while the log's route stays refused.
