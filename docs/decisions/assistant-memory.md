## Assistant memory

Rule ID: **ASSISTANT-MEMORY**. Current binding module contract. Read root `AGENTS.md` first, then ASSISTANT and JEV.

- The instructor asked (2026-10-10) for a long-term memory for the Assistant and chose, asked directly: saving **"Automatic,
  with Undo"**; conflicts **"Newer replaces, old kept in history"**; the first build remembers preferences and standing rules,
  course and term facts, past work and follow-up lessons; and to **"Sandbox it first"**. After the sandbox
  (`memory_sandbox/`, run-20261010: 241 invented messages, 22,152 requests) they chose: replacing **"readier"**; **"jev
  alone"**; wording **"middle path"** (their sentence as typed at once, a rewrite only when it cannot stand alone);
  **"I want full embedding model"** (EmbeddingGemma 2, on the GPU once it is fixed); record-like facts **"never keep them"**.
  This contract is the first stage: the store, keeping from chat, Undo, and what a request is told. Then (2026-10-10), of
  five proposed improvements and an outside review's five findings: **"do 1 and 2, then fix findings 1 and 2"**: MEMORIES
  GIVEN with WITHDRAWN (the review's finding 4), THE REWRITE STEP, EVERY WAY IN (finding 1) and IN THE ORDER SENT (finding 2).
  Then (2026-10-10), of a rule they typed about one student's follow-up that was refused whole for the name in it: **"Rule
  only, rewritten"** (A RULE SAID OF ONE STUDENT'S CASE); and, once that was live: **"make the follow-up's Remember this do
  the same"** (A FOLLOW-UP'S "REMEMBER THIS"). Then (2026-10-12): **"when I edit the recommended suggestions of the followup
  agent, it compares the changes to what was there and makes some sort of memory from it"**, **"Kept at once, with Undo"**
  (LEARNED FROM AN EDIT). Then (2026-11-09), of a chat about category weights that was given 39 memories about absences and
  regrades: **"let's do your suggestion and have relevance checks"** (WHAT A REQUEST IS TOLD). Then (2026-11-10), of where the
  "With every request" tick was: **"we need a centralized setting page in the settings for the various memory functionalities
  across the app"** (ONE PAGE).
  `assistant/memory.py`, `assistant/memory_decisions.py`, `assistant/followup_learning.py`, migrations `assistant/010_memory`,
  `assistant/011_memory_pictures`, `assistant/013_memory_always` and `assistant/014_memory_settings`, `static/memory-settings.js`,
  `grading/prompts/memory_rewrite-v1.txt`, `grading/prompts/memory_rule-v2.txt`,
  `grading/prompts/memory_edit-v1.txt`, `static/assistant-memory.js`,
  `tests/test_assistant_memory.py`, `tests/test_followup_learning.py`, `tests/test_assistant_memory.mjs`,
  `tests/browser_assistant_memory.py`, `tests/browser_followup_learning.py`.
- **OWNER.** A memory belongs to exactly one of: the workspace (a preference or standing rule for all the instructor's
  teaching), a course (`course_definitions.id`: every term it is taught) or a term (`course_terms.id`). Never a section, and
  never a student: what is true of one student belongs in STUDENT-NOTES, and a statement about one is refused here. No
  memory's words name a student: a general rule typed about one student's case is kept only as the rule, written without
  them (A RULE SAID OF ONE STUDENT'S CASE). A term's memory ends with its term; a new term starts with the workspace's and
  the course's only.
- **ONLY THE INSTRUCTOR'S OWN TYPED WORDS.** A memory is kept from a chat message the instructor typed
  (`POST /api/assistant/conversations/<id>/messages`), from the Memory form, or from a follow-up's "Remember this". Never from
  mail, a scan, OCR, a tool result, a file, a selected area, a continuation, a fixed prompt, a preparation, or a model's words
  (PICTURES below is one exception: a picture attached to a kept message is kept with it, and decides nothing. THE REWRITE
  STEP is another: a rewording of the instructor's own statement, taken only when it says what they typed and no more. A
  RULE SAID OF ONE STUDENT'S CASE is the third: the instructor's rule written without the student, by their choice.
  LEARNED FROM AN EDIT is the fourth, and the widest, by their choice: a rule the Assistant's model writes from a
  correction they made of a follow-up's card and approved. The correction is theirs (their edit, their choice, their
  request, their approval), but the Assistant's draft and the student's message are read to see it, a decision chosen from a
  dropdown is no typed word at all, and a request made in the chat is held in the chat's wording of it.
  SAID IN A RECORDING is the fifth, by their choice (2026-11-13, RECORDINGS "A MEMORY"): a standing rule said aloud in a
  recorded meeting, kept only by the instructor's own press of Remember on that recording's page, in words they may edit
  first).
  No request about a memory holds any of those: only the message, the instructor's two earlier messages, the page's course
  and term in words, and memories already kept (a rule's writer and its check alone are also told what the follow-up on
  screen is about: A RULE SAID OF ONE STUDENT'S CASE; and so is a rewording their own words could not resolve: THE
  REWRITE STEP, "What was on screen"). Each message is as typed: the note of the files attached to it (their names
  and routes) is left out of the earlier messages and of the request a memory is chosen for (2026-10-10: the transcript
  keeps a message with that note, and a file's name can name a student). The chat model has no tool that writes, changes or removes a memory (every
  `assistant.memory*` route is `never`; ASSISTANT-APPROVALS stands as written), so nothing it reads can plant one.
- **KEEPING** (`AssistantMemory.capture`, beside the turn and never holding it). One Jev request on the message
  (`assistant-memory-capture-v2`), then one against the five nearest kept memories (`assistant-memory-consolidate-v1`):
  - not lasting, not the instructor's own position (quoted or pasted text), or nothing lasting in either order: nothing is
    kept and nothing is said;
  - it is about one student: refused, and the chat says so ("Not remembered: it is about one student. Their student notes
    are the place for that."). Whether a student is named and whether what is lasting is about that student are two
    questions (`names_student`, `about_student`). A UIN or a student's whole name names one in code, whatever Jev says; a
    given or family name alone goes to Jev with the matched word, so "Grant extensions…" is kept as typed and "Volkov's
    emails…" is not. **A whole name** (`memory.spelled`, read the same way wherever a name is looked for or taken out:
    `roster_check`, `nameless`, `followup_learning.mask`) is a given name beside a family name, either first, with the
    names between or without them: "Petra Rosen", "Rosen, Petra" and "Petra A. Rosen" for the roster's "Rosen, Petra
    Anjali", and a hyphened family name by either half. In any case, unless one of the two has two letters, which is
    read only as the roster writes it ("to my" is never To, My). Two letters alone are never read as a name ("Do not…",
    "An absence…"), and one letter is an initial. (2026-10-14, requirement of a review's findings: "fix 1, 3 and 5,
    and carry 2, 6, 7, 9 along". Read on the live roster, "First Last" was a whole name in code for 177 of 340 current
    students, the rest left to Jev with the matched words, because a middle name of three letters or more left only
    its own pairings; and a student whose name has a two-letter part was not looked for at all.) A letter is read with or
    without its accent, whichever way the roster has it ("Ana Garcia" for "García, Ana"); a given or family name alone is
    read as the roster writes it or all in capitals ("PETRA"), still never in small letters; and both halves of a hyphened
    family name written apart are one whole name (2026-11-09, of a review: "fix all of them"). **Addresses go too**
    (`memory.nameless`, wherever a student's or the instructor's words leave with the names out of them): an address names
    its owner as surely as their name, and of 70 follow-ups' latest messages as a preparation sent them to Jev, 13 still
    held one that was not the instructor's;
  - **POINTED AT THE STUDENT ON SCREEN** (requirement, 2026-10-20: **"both"**, of "I don't like the last part about
    letting me know because she isn't excused just because she feels bad (don't tell her that, im just letting you know)",
    typed on a student's follow-up and kept as typed under Everything because Jev found no name in it: reword that memory,
    and count "she/he" on an open follow-up as naming the student). With a follow-up on screen (the page's record hint,
    as `open_item` reads it) a "she", "he" or another form of them (`memory.POINTED`) names that student in code, whatever
    `names_student` answers, so the statement is about the student (refused) or a rule said of their case (written
    without them, or not kept). A follow-up's "Remember this" reads it the same way (`decide_remembered(pointed=…)`).
    With no follow-up open a "she" is Jev's to judge, as before;
  - it names a student but states a general rule: never kept as typed; kept as the rule written without the student, or
    not at all (A RULE SAID OF ONE STUDENT'S CASE);
  - a date, time, room, grade or deadline of the kind a calendar or gradebook records: not kept, and the chat says where
    it belongs. The record is the truth; a copy would go stale;
  - **ONE DAY GOES ON THE CALENDAR** (requirement, 2026-11-08, of "don't schedule any makeups for Friday office hours, I
    have a doctor's appointment at 3PM that day and won't return to school", kept for the term at lasting .56 and record .49:
    "was this an appropriate thing to remember, I feel like this is a one-off thing that is handled just by putting in a
    calendar entry"): a statement that holds only for one date, day or occasion (`one_date`, capture v3, gate .5; read after the
    student and record questions) is not kept, and the chat says it belongs on the calendar, where it ends with the day, and
    that a timed event titled "Away" or "Unavailable" keeps makeups out of the office hours it covers (SCHEDULING "AWAY"). The
    chat's own answer adds that event (assistant/guide.md). The Memory form and a follow-up's "Remember this" do not read it:
    what the instructor writes to be kept is theirs to say;
  - its course or term cannot be told (no page, record hint or single current term names it): not kept, and the chat says so;
  - **the course the words name decides, before the page** (requirement, 2026-10-10, of a review that found "In MATH
    142 …" typed on MATH 140's page kept for MATH 140: "fix 3 and 5"). One course Faculty has, named by its code and alone:
    the memory is that course's (and its term's, when it has one current term), and the decision request is told that
    course. A code of a subject Faculty teaches that is not one of its courses, or more than one course named: not kept
    for any course or term, and the chat says so (the code, when Faculty does not have it). Only codes of a subject Faculty
    teaches are read as courses, so a room ("BLOC 319") is never taken for one; a general preference that mentions a
    course is still the workspace's;
  - it repeats a kept memory: nothing changes;
  - it changes a kept memory: the new one is kept and the old one is `superseded`, in one transaction. An exception or a
    narrower case is kept beside its rule and replaces nothing. **A NARROWER OWNER NEVER REPLACES A WIDER ONE**
    (`memory_decisions.wider`, in code whatever Jev answers): a term's statement never retires a course's or the
    workspace's memory, nor a course's the workspace's; it is kept beside it, the narrower one holds where it applies (what
    a request is told says so), and the wider rule holds again when the term is over. Within one owner, and from a wider
    owner to a narrower one, a change replaces as before. (requirement, 2026-10-15, of the 2026-10-14 review's finding 4,
    asked directly: **"Scope guard"**. "This semester, three versions" could retire "Make four versions of every quiz", which
    stayed retired once the term was over; the sandbox's 20 updates are each of their rule's own owner, and no case tried
    a term's statement against a wider rule.) **Only an owner the statement says** (requirement, 2026-10-26: "fix all
    six", of a review that found their own "students need to confirm the relation and provide documentation…", filed under
    the term by default, kept beside the older "do not ask … the relationship" it replaced at .93): the guard holds when
    the capture request settled the narrower owner (`memory_decisions.settled`). A statement kept under a default owner
    replaces a wider memory as any change does, and is kept under that memory's owner, never narrower than what it corrects;
  - otherwise it is kept, as typed, under the owner the two orders agree on; when they do not settle it, a fact with the
    course and a preference with the workspace, never the term (the same review: 7 of the 8 term memories had been filed
    there by the old default, "a fact with its term", while Jev leaned to the course in both orders, and none said "this
    semester"; they would have ended with Fall 2026).
  When Jev does not answer, nothing is kept and nothing is replaced. A message is never asked about twice (JEV: no resends).
  - **IN THE ORDER SENT** (requirement, 2026-10-10: "fix findings 1 and 2"; the review held up "three versions", sent
    "two versions", and the late "three" replaced it; a message considered late also read the conversation as it had
    become, so an earlier statement took a later message's course). What a message's keeping is decided on is read as the
    message is sent (`AssistantMemory.seen`: the owners, the course and term the instructor is in, the page's heading, their
    two messages before it), and messages are considered one at a time, in the order they were sent, by one worker
    (`capture_later`, `_consider`). A later message is never decided before an earlier one has been kept or let go, so the
    newer statement is always the one that replaces. A message waiting its turn holds no answer up.
  **At most 500 memories stand at once** (`CURRENT_LIMIT`; removed, replaced and erased ones do not count). A replacement
  takes the place of the memory it replaces and is never refused for the limit; anything else at the limit is not kept, and
  the chat says so (the same review: the limit was checked before the old one was marked replaced, and the failure was
  only logged). The Memory form refuses with the same words.
- **EVERY WAY IN** (requirement, 2026-10-10: "fix findings 1 and 2"; the review saved a UIN with an accommodation and
  an exam deadline through the Memory form, and both entered later prompts). Words the instructor writes to be kept (the
  Memory form and the old preference routes; a follow-up's "Remember this" is held to the same two things by its own rule,
  A FOLLOW-UP'S "REMEMBER THIS") are held to the two things a memory never holds,
  exactly as a chat message is (`AssistantMemory._checked`): a UIN or a student's whole name is refused in code; then the
  capture request's own questions at their own gates refuse a statement that names a student or a date, time, room, grade
  or deadline a record owns. Whether the words are lasting is the instructor's to say there, so those gates are not read.
  A rule written from a correction is held to the same two things by the same request before it is kept (LEARNED FROM AN
  EDIT). The page shows why in place ("It names a student, so it is not kept here…"). **No answer is no keeping**: when Jev does
  not answer or is off, nothing is saved and the form says to try again; each Save is the instructor's own action and its
  own request, never resent by itself. New words are checked again on an edit; a move to another owner with the same
  words is not asked about. **The form sends no name**, so a Save from it keeps the memory's name, and a follow-up's
  name ("Follow-up drafts: …", its own first words) follows its new words; a name changes only where one is sent (the
  old preference routes), an empty one clearing it (2026-10-14: a Save from the form blanked the name, so a move was
  asked about, and refused while Jev was down). The receipt is the revision's `capture_call`.
- **GATES** (`memory_decisions.GATES`, for `jev-1.13.0`): the ones the sandbox proposed with no passing remark, quoted text or
  student statement kept on fit or held-out cases; `replaces` .8 with `narrows` under .5 is the instructor's "readier"
  setting (every update replaced on the pair cases, one of six exceptions wrongly replacing its rule). `conflicts` is .9
  since 2026-11-09 (WHAT A REQUEST IS TOLD, "Set aside only by a plain countermand"). A different model
  needs its own gates measured before it is used; these purposes stay on Jev whatever provider triage uses.
- **SAID WHERE IT HAPPENED.** Every keeping, refusal and replacement leaves one line in the chat that caused it:
  "Remembered: “…” Undo", with what it replaced and whom it is for. Nothing is kept silently. **Undo** removes the memory and
  restores what it replaced in one step. It takes back the Assistant's own keeping for as long as the words that stand
  are words it kept or reworded: made to stand again (by the Undo of what replaced it, or Restore), moved to another
  owner, or back as typed ("Keep my words"), a memory is still that, so an Undo a page offers is one that works
  (2026-10-14: the Follow-up page's Undo of a rule that stood again answered "This memory changed since it was kept").
  Words the instructor wrote are Remove's. A rule learned from a correction is said on the Follow-up page, once as a toast
  and for as long as it stands on its follow-up (LEARNED FROM AN EDIT, "Said"). A statement that may not read on its own is kept and says so, until THE REWRITE
  STEP rewords it.
- **STILL BEING CONSIDERED** (requirement, 2026-10-10: "the reword it option didn't seem to do anything". On live the
  Assistant had reworded that memory 5 seconds after keeping it; the answer was in, the chat had stopped reading, and the
  note still offered "reword it", a link that only opened Saved at its top). A message's keeping is decided beside its
  answer and can end after it, so:
  - **The chat reads on.** A conversation's view says `remembering` while one of its messages waits or is being
    considered, with the memory being reworded (`AssistantMemory.considering`: `{rewording: id or ''}`). It is gone only
    after the message's last note is in the chat, so a view without it holds every note; a conversation with nothing
    being considered answers as it always did. The drawer and the floating chat both read until it is gone.
  - **The note says what is happening.** While its memory is being reworded a note reads "It may not read well on its
    own; I'm rewording it." and offers no form. "reword it" stands only on words the Assistant left as typed (it
    declined, the check refused, or "Keep my words").
  - **A memory's link opens that memory.** "reword it", Change under "Memories given" and "Add it in Memory" open the
    Memory page (ONE PAGE) at that memory's own row, opened for its words with the caret in them (`#memory?memory=<id>`,
    `new` for the form that adds one; one that no longer stands: under "Replaced and removed"). The floating chat is in
    the top layer, so it is put away when its link opens the page behind it.
  - **Their rewording reaches the note.** When the instructor rewords a memory in the form, every open chat whose note
    says it was kept is told (`_renote`: a `reworded` note with their words, actor `instructor`) and is read once: the
    note shows their words and "You reworded it in Memory.", and no longer offers Undo, which was only ever the
    Assistant's own keeping to take back. A move to another owner with the same words tells no chat.
- **A RULE SAID OF ONE STUDENT'S CASE** (requirement, 2026-10-10, of "in Kenna's followup, it shows as waiting for
  my decision, but this type of absence is not excused", which the chat answered "Not remembered: it names a student";
  asked what to keep when a message states a general rule but mentions a student: **"Rule only, rewritten"**, described
  to them as "The Assistant writes the rule as one sentence without the student, reading what the follow-up is about to
  fill in 'this type of absence'. The chat shows exactly what it kept, with Undo. Anything true of only that one student
  is still refused."). `AssistantMemory._rule`, inside `capture`, by the same worker and in the order sent:
  - **When.** The capture request says the message is lasting and the instructor's own, a student is named (in code or by
    `names_student`), and what is lasting is not about that student (`about_student` under `GATES['about']`, .5: on
    invented messages 18 such rules answered .05 to .45 and 40 statements about one student .58 to .96).
  - **Never as typed.** The typed words name a student, so they are kept in no revision, shown in no list and offered
    by no "Keep my words". What is kept is the rule, as the memory's first revision (actor `assistant`, reason `kept`,
    `standalone` 1), or nothing. Pictures attached to that message are not kept with it.
  - **Who writes it.** The Assistant's own model (the connection chosen in Compute settings, with the least thinking it
    offers; prompt `memory_rule-v2`), given the message, the instructor's two messages before it, and `open_item`. It is
    told to leave the student out entirely, to take the decision from the instructor's words alone, to take from
    `open_item` only the name of the kind of case their words point at, to add nothing, drop nothing and go no wider,
    to keep the instructor's own wording of the rule where it reads on its own, to keep "you" as an instruction to the
    Assistant, and to decline when the statement is only about that student or nothing says what kind of case is
    meant. A message over 600 characters is not written from.
  - **`open_item`: what the follow-up on screen is about** (`AssistantMemory._open_item`). When the page's record hint
    names a follow-up (`enrollment_id` and `source_key`, read as the message is sent), the two latest messages that
    student wrote in that conversation, subject and text, 1,500 characters each, with every current student's name, any
    nine-digit number and every address taken out (`memory.nameless`). Never the instructor's replies, notes, grades or any other
    record. Nothing was on screen, or it cannot be read: `open_item` is empty, and the rule is written from the
    instructor's words or not at all. This is the one place a request about a memory holds something the instructor did
    not type: it goes to the Assistant's model and to Jev (both already read student mail, in the chat and in triage),
    and it decides nothing: the capture request, which decides whether anything is kept, never sees it.
  - **What is taken.** In code, a rule that holds a current student's name or a nine-digit number, or has lost a limit of
    term or year the instructor typed ("this semester"), is refused before any request (`memory.limited`; the day and
    the numbers of the one case are meant to go, so `carried` is not used here). Then one Jev request
    (`assistant-memory-rule-v2`, eight questions; its state is the message, the two before it, `open_item` and the
    rule): it adds, it is wider, it drops, it is about another kind of case, it says something else or only what
    `open_item` says, it cannot be read alone, it names or picks out a person, or the instructor's statement was about
    one student only. Every gate must agree (`memory_decisions.RULE_GATES`). Then the rule, not the typed message, is
    compared with the kept memories as any statement is (the same, a change, or new).
  - **Said.** Kept: "Remembered …: “…” I wrote this from your message, without the student." with Undo; the Saved list
    and "Memories given" say "written by the Assistant" until the instructor rewords it (`ruled`: the first revision's
    `consolidate_call` is the rule check's receipt and its words still stand). Not kept (the model declined or failed,
    a check refused, Jev did not answer): "Not remembered: it mentions a student, and I could not write it as a rule
    without them. Add it in Memory if it should be kept." Nothing is asked twice.
  - **The form is as it was.** Words the instructor writes by hand in Memory (EVERY WAY IN) are still refused when they
    name a student: there is nothing to rewrite, and they can leave the name out themselves. A follow-up's "Remember
    this" has its own rule, below.
  - **Measured** (2026-10-10, scratch `/mnt/nvme/Teaching/tmp/memory-rule-sandbox`, invented messages and invented student
    mail only; 492 Jev requests and 31 model requests). Capture with the new question, on the memory sandbox's 241
    messages and 31 new ones: 126 of 131 lasting statements still kept as typed, 0 of 70 passing or quoted, 0 of 30
    student statements (the same counts as before the question was added); of 18 general rules said of one student's
    case 17 came out general (one read as quoted text and skipped); of 10 new statements about one student none did. The
    rule check, on hand-written rules: fitted on ten messages (10 good rules, 57 to refuse) and held to eight more
    (8 good, 25 to refuse): 17 of 18 good rules taken, and none of the 82 to refuse (naming the student, wider, added
    to, dropped from, about another kind of case, the student's request in place of the instructor's decision, a
    detail that picks out the one case, a rule made of one student's case, a guess). Three questions were sharpened
    after the first answers, which had taken "Travel is never a reason…" for "Club travel…" and "…move an exam" for
    "…move a quiz". The Assistant's model (about 5 seconds a rule) wrote all 18 rules, declined all 10 statements about
    one student and all 3 that nothing resolved; 16 of its 18 were taken, and the two refused had each added something
    the instructor did not say. A different model on either side needs this measured again before it is used.
    **Measured again the same day for `-v2` of the check and of the prompt** (with A FOLLOW-UP'S "REMEMBER THIS"; the
    numbers are there): the same 17 of 18 good rules taken and none of the 82 to refuse. `RULE_GATES['adds']` rose
    from .45 to .6 with it.
- **A FOLLOW-UP'S "REMEMBER THIS"** (requirement, 2026-10-10, once A RULE SAID OF ONE STUDENT'S CASE was live: "make
  the follow-up's Remember this do the same"). What the instructor types on one student's follow-up (Ask for changes, or
  why they declined a card) with "Remember this for future drafts" ticked (`AssistantMemory.remember`, from
  `FollowupSuggestions._remember`, inside the request that carries it). The capture request as before (channel
  `followup`), then (`memory_decisions.decide_remembered`; whether the words are lasting is theirs to say):
  - **about that one student** (`about_student` at its gate, whether or not a name is found: the page is one student's, so
    "this one" and "she" point at them): not kept, "It is about one student, so it is not kept here. Their student
    notes are the place for that." A date, time, room or grade a record owns: not kept, as before. No answer from Jev:
    nothing is kept and the page says to try again;
  - **names a student and states a general rule**: never kept as typed. The rule is written without the student
    exactly as for a chat message (`_rule`: the same writer, code checks and Jev check), and `open_item` is this
    follow-up's own two latest student messages, known from the run and not from a page hint. No rule taken: nothing
    is kept, and the page says so;
  - **names no one and reads on its own** (the consolidate request's `standalone`, asked with no kept memory beside it,
    at its gate; no answer counts as reading alone): kept as typed, as it always was;
  - **names no one and leans on the follow-up** ("This type of absence is not excused."): the rule is written from it
    the same way. No rule taken (the model declined or failed, a check refused): kept as typed, as before, and marked
    as not reading on its own.
  Everything else is as it was: the memory is the workspace's ("Follow-up drafts: …" is its name), its source is
  `followup`, nothing is compared with or replaces a kept memory, and the note or the change the instructor asked for is
  saved whatever becomes of the remembering. A rule is the memory's first revision (actor `assistant`, the rule check's
  receipt in `consolidate_call`, `ruled` in its view); what was typed is in no memory revision (it stays where it was
  typed, on the follow-up's run or note). **Said on the page**, in a toast: a rule is quoted, "Remembered for future
  drafts: “…” I wrote this from what you typed, without the student.", with **Undo** (the chat's own
  `POST …/memory/<id>/undo`); words kept as typed say so, and say when they may not read on their own; a refusal gives
  its reason. A request that remembers waits up to 90 seconds for its answer (`REMEMBER_WAIT`): the model writes for
  about 5 seconds. **Measured** (2026-10-10, the same scratch, invented text typed on invented mail, nobody named;
  with the earlier sets asked again under the `-v2` wordings: 732 Jev requests and 129 model requests in all).
  Sorting: of 8 fragments, six answered under the `standalone` gate (.19 to .39) and go to be written, and two over it
  (.52, .55: "I don't accept these late without documentation.") are kept as typed; 6 sentences that read alone were
  all kept as typed (.68 to .92); 4 unnamed statements about the one student were all refused (`about_student` .60 to
  .81; of 211 earlier messages not about a student, the six answering .5 or more were each about one student's item
  and none a general statement). The rule check, on hand-written rules: 7 of 8 good ones taken, and 21 of 24 that must
  be refused. **The three it took were wider readings** ("Missing a quiz is never excused…" for "This is never
  excused…" typed on a mail about oversleeping): when the instructor's words name no kind of case, the check cannot
  always tell the kind the mail shows from a wider one. The Assistant's model wrote the narrow reading for all 8, all
  were taken, and it declined all 4 about the one student. On the messages that name a student, asked again: 17 of 18
  hand-written rules taken and none of the 82 to refuse, as before; the model wrote 18, declined the 10 and the 3 as
  before, and 16 were taken (one refused had added "for an absence due to illness"; the other was a correct rule the
  check read as having dropped "this semester"). What is kept is always shown, with Undo.
- **SAID IN A RECORDING** (2026-11-13, RECORDINGS step 3; the instructor's 2026-11-12 choice that Faculty reads their recorded
  meetings, and the plan's "Remember" they asked to be built: "build step 3"). Faculty's reading of a meeting's transcript
  may find a `policy`: a standing rule for the course that someone said. It is shown on the recording's page as one sentence
  with the exact words it rests on, and is NOT a memory. It becomes one only when the instructor presses Remember there
  (`POST /api/recordings/<id>/items/<item>/remember`, `recordings/readings.py` `remember`): the sentence is put before them
  to edit, and what they leave is written through `AssistantMemory.write` exactly as the Memory form writes (EVERY WAY IN:
  the roster check, then the decision host's own questions; a statement that names a student or holds a record's date, room
  or grade is refused, and no answer is no keeping). Its `source` is `recording` (migration
  `assistant/015_memory_said_in_a_recording`: the table is made again with that one more value, every revision carried over
  as it is, foreign keys suspended for that transaction and checked before it commits, `assistant/service.py` `REBUILDS`), its
  actor the instructor, its owner the recording's TERM unless they widen it to the course or everything. The recording and
  the quote are its provenance and stay with the recording's item, by reference (`recording_item_events` `remembered` names
  the memory); the Memory page says "said in a recording". Never by itself: no reading, round or card keeps one, and the
  Assistant cannot (`recordings.remember` is `never`, as every `assistant.memory*` route is). What is said in a recording is
  otherwise never a memory's source: a transcript is evidence, and Plaud mishears.
- **LEARNED FROM AN EDIT** (requirement, 2026-10-12: "Can we tweak the memory system so that when I edit the recommended
  suggestions of the followup agent, it compares the changes to what was there and makes some sort of memory from it".
  Asked which of their corrections should teach, they chose all three offered: **"Reply text I edit"**, **"A decision I
  change"** and **"Changes I ask for"**; how a rule a correction shows is kept: **"Kept at once, with Undo"**; and what
  becomes of the older "Learn from feedback" lessons: **"Replace them"**. Told then that none of their requested changes
  had been typed in the page's own box (19 rounds were asked for in the Assistant chat and 3 by an "Instead" choice), they
  approved a plan in which a round prepared again at their request teaches by any of the three ways).
  `assistant/followup_learning.py` (finding and comparing), `AssistantMemory.learn` (writing, checking, keeping).
  - **What a correction is.** One of: a card the instructor edited (a reply's subject or text, a regrade decision's reply, an
    absence card's Decision), approved as they left it; a reply they sent from Mail in a draft's place, once Gmail accepted
    it; a round prepared again at their request (`reason` `revise` with an instruction: Ask for changes, the chat's redo, an
    "Instead" choice), once a card of it is approved: one correction a round, whatever its cards. Always against the
    Assistant's own first card (an edit of an edit goes back to it). **Only a confirmed outcome**: an edit never sent, a
    declined card, an unchanged approval, a failed or uncertain result teach nothing and get no case. **Only from
    `LEARNS_FROM` on** (the instant this went live): nothing of before is kept by itself.
  - **Found** (`FollowupLearning.discover`, by the follow-up dispatcher's own look, `turn`: woken by an approval or a Mail
    replacement, else once a minute; no model, no Jev). Each correction found gets a case in
    `student_support_learning_cases` (its enrollment's, through its run: references to immutable actions, runs and
    sends, no words) and is handed to memory's one worker (`AssistantMemory.later`), so what it keeps is compared with
    the kept memories in order with chat messages (IN THE ORDER SENT) and no preparation waits for it. A case is
    considered once: its last event is `remembered` (with the memory), `known`, `nothing` (with why), `failed` or
    `interrupted`. One left mid-way by a restart keeps nothing and is not asked about again. One that could not be
    claimed at all (the catalog busy) was asked of no one: it stays queued and is handed again at the next look. The pause on automatic
    suggestions does not hold it: it follows the instructor's own approvals, as a chat memory follows what they type.
    `FACULTY_LEARNING_ENABLED=0` turns it off; synthetic checks never run it by themselves.
  - **Compared, in code** (`FollowupLearning.comparison`, a pure read). A reply: the sentences taken out and put in, and
    the draft and the approved text, each cut short. A decision: the one suggested, the one chosen, the absence's recorded
    reason and whether documents were attached. A round: what was asked, and for up to three of its cards (reply, absence
    decision, follow-up status) what waited before and what was approved. And `open_item`: the two latest messages the
    student wrote among those the round was prepared from, 1,500 characters each. **Taken out**: from the Assistant's
    words, every roster name, contact, record ID and number; from the instructor's own sentences and the student's,
    every current student's name and nine-digit number. **Never in it**: a grade, rubric line or adjustment; a date, time
    or recipient; an absence's details or note; a private student note; any ID. **Ends here** with nothing: before and
    after the same (a decision changed back, a regrade's grade alone), a decision to Withdrawn or left waiting, or with
    no student message to say what kind of absence it is, a request over 2,000 characters.
  - **Who writes it.** The Assistant's own model, as for a rule (prompt `memory_edit-v1`), given the comparison and
    `open_item` and nothing else. It is told to take the rule only from the instructor's own correction, never from what
    the draft said and they left alone and never from `open_item`, which says only what kind of case this is; to go no
    wider than that kind; to add no reason for a decision they chose without giving one; to leave out the student and
    every particular of the one case; to take from a request only what the instructor wants whenever that kind of case
    comes up, and nothing about this reply, this follow-up, its steps or its status; and that most corrections show no
    lasting rule: it declines for a corrected fact (a date, time, place, name, amount, grade, link), for what fits only
    this student, for what is only about this piece of work, for one problem of one assignment, and for a decision whose
    kind of absence the message does not make clear. One rule at most a correction.
  - **What is taken.** In code, before any request: no current student's name or nine-digit number; no mark of something
    taken out of the draft; no number and no limit of term or year but those the instructor wrote themselves (a limit the
    writer adds is refused as one it drops is, `memory.unlimited`: until 2026-11-09 only the loss was looked for, and a
    writer's own "this semester" on a rule kept for every term was left to Jev's `adds`); and from a
    round asked for again, one sentence only (what was asked is often passed on at length, and instructions for that one
    piece of work come out as a second sentence; a title or an aside ends none: "Dr.", "Prof.", "e.g."). Then one
    Jev request (`assistant-memory-edit-v1`, nine questions and a tenth for a decision; its state is the comparison,
    `open_item` and the rule), one question for each way a rule read out of a change goes wrong: it is not the
    instructor's own correction (`shown`); it holds a part the correction does not (`adds`); it reaches to every reply,
    a larger kind or past a limit they gave (`wider`); it is a particular day, room, amount or name, or a habit made of
    one (`fact`); it makes a habit of one occasion (`once`); it speaks of the one piece of work, or of one problem of
    one assignment (`task`); it is a rule for grading (`grades`); it only repeats what the draft said and they kept
    (`kept`); it cannot be read alone; it names or points at a person. Of a changed
    decision, where they typed nothing, also whether the student's message plainly gives the reason for the absence and
    the rule is about that kind (`kind`). Every gate must agree (`memory_decisions.EDIT_GATES`). Then
    EVERY WAY IN, by the capture request as for words written to be kept: it names no student and is no date, time,
    room or grade a record owns. Then it is compared with the nearest kept memories as any statement is, those
    standing where its follow-up is (the workspace's and, from the case's enrollment, its course's and its term's,
    however many terms are current; an enrollment that is gone never falls back to another term): the same
    changes nothing (`known`); a change replaces **only a rule learned this way**. Against words the instructor typed
    (or reworded themselves since) a rule read out of one correction is not kept, and their words stand. No answer from
    the model or from Jev at any step keeps nothing, and nothing is asked twice.
  - **Kept** as the workspace's, the one owner in scope of every follow-up's preparation whatever term it is for, named as
    "Remember this" names its own ("Follow-up drafts: …"), source `followup`. Its first revision is the Assistant's
    (actor `assistant`, reason `kept`), so **Undo** takes it back and restores what it replaced; `capture_call` is
    the standing check's receipt and `consolidate_call` the edit check's, which is how a view knows it was `learned`
    (as `ruled` is known) for as long as its words are still the Assistant's. The memory and its case's `remembered`
    event are one transaction. A request is told such a memory with `how_kept` beside its words: written by the
    Assistant from the instructor's correction, not their own words.
  - **Said** (SAID WHERE IT HAPPENED). The Follow-up page's `suggestions=1` read carries, on the follow-up it was learned
    from, `learned` (what was kept, from which kind of correction, what it replaced, whether it still stands), and
    `suggestion_settings.remembering` while a correction is being considered, so the page reads again within seconds.
    The read asks whether one is being considered before it reads what was learned (2026-11-09 review: asked after, a
    correction considered while the listing was put together was in neither, and the page waited half a minute; its
    browser check failed more often than it passed).
    Once, as a toast: "Remembered from your edit: “…”" ("from your decision", "from the change you asked for"), with what
    it replaced and **Undo**; which were said is remembered in the browser, a convenience. And on the follow-up, under its
    steps, for as long as it stands: "Remembered for later drafts · From your edit: “…”" with Undo and Change (its form
    in Memory); "Taken back" once undone; gone once the instructor rewords it, when it is theirs. The Saved list and
    "Memories given" say "written by the Assistant from your correction". A correction that teaches nothing says
    nothing: most do not, and a line for each would be noise.
  - **What it never does.** It changes no permission, tier or approval: a later draft is told the rule and every card
    still waits for the instructor. It never lets the Assistant propose Excused without evidence (ASSISTANT "EXCUSE
    NEEDS EVIDENCE" reads no memory). It never becomes a term policy (ASSISTANT "LEARN FROM EDITS" is as it was).
  - **Replaces** the lesson review of 2026-10-08 (ASSISTANT "Learning from the instructor's corrections"): its tables
    stay and its cases table is this rule's queue; no lesson is made, shown or used.
  - **Measured** (2026-10-13, scratch `/mnt/nvme/Teaching/tmp/memory-edit-sandbox`: `cases.py`, `measure.py`, `report.py`,
    `fit.py`). **On invented corrections and invented student mail** (632 Jev requests and 68 model requests): 20
    corrections that teach (8 edited replies, one of them a regrade's; 5 changed decisions; 7 rounds asked for again, two
    of them at length in the chat's words), each with a hand-written rule that must be taken and four that must be
    refused (wider; added to; the one case restated; what the draft said or the opposite decision; instructions for the
    one piece of work), and 16 that teach nothing (a fixed day and room, a one-off arrangement, wording with nothing to
    say why, a decision the message does not explain, a request about this piece of work or one quiz problem), each with
    a rule made of it anyway. **The questions are the fifth wording.** The first asked whether the correction was
    explained by the one case and whether the rule was wider than the student's one message: it took 3 of 18 good rules
    ("ask for documentation plainly" read as wider than one sick student's missed quiz) and counted the kind of absence,
    which a decision takes from the student's message, as added. One question for each way of going wrong, as for the
    rule's check, is what separates them. **The check, on the hand-written rules**, gates fitted on every other case and
    held to the rest: 10 of 11 good rules taken and 2 of 52 to refuse on the fitted half; 7 of 9 and 2 of 44 on the
    held-out half. Of the four it took, three were **a rule made of one occasion and worded as a habit** ("Use a
    student's preferred name in the greeting", from a request about one student's name): this check tells those least
    well (`once` answers .13 to .55 for good rules and .17 to .70 for those), and **what stops them is the writer**. The
    fourth added a length the instructor never gave ("under three sentences", `adds` .38 at a gate of .45). **The
    Assistant's model** (about 5 seconds a rule) declined all 16 corrections that teach nothing, and wrote 19 rules for
    the 20 that teach (it declined the regrade's reply), of which 18 were taken; the one refused was a good rule
    ("Grant homework extensions under 48 hours without asking for documentation", `once` .55). EVERY WAY IN refused none
    of the good rules. **On the instructor's own past corrections**, a dry run on their word ("Run the dry run"; the live
    catalog read-only, names out, nothing kept; 54 model requests and 66 Jev requests over two runs): of 27 confirmed
    corrections (12 edited replies, 15 rounds asked for again), the first run kept 11 rules. All 5 from edited replies
    read right; of the 6 from rounds, two should not have been kept: one about a single quiz problem, and one that
    carried "Preserve the rest of the proposed handling; this reply revision is not an instruction to …". Both came
    from the chat's long wording of the request. After that run the writer was told to leave out everything about the
    one piece of work and never to make a rule of one problem of one assignment, the check was given `task`, and a
    round's rule was held to one sentence in code. The second run kept 10 (6 from edits, 4 from rounds), with neither of
    those two and no rule that carries such instructions. Not measured: a real changed Decision, of which none had been
    approved when this was written. A different model on either side needs this measured again before it is used.
- **THE REWRITE STEP** (requirement, 2026-10-10: wording "middle path"; then, of "Make fragmented memories understandable
  … retaining your original words and checking that the rewrite adds nothing": "do 1 and 2"). A statement kept from a chat
  message that Jev says cannot be read on its own ("Three from now on, not four.") is kept as typed at once, then reworded
  (`AssistantMemory.reword`, by the same worker, after the note is in the chat):
  - **Who writes it.** The Assistant's own model (the connection chosen in Compute settings, with the least thinking it
    offers; prompt `memory_rewrite-v1`), given the message and the instructor's two messages before it, as typed, and
    nothing else. It is told to name what a pronoun or a bare number refers to from the instructor's own words, to name
    the kind of the one item in progress and nothing wider, to add nothing and drop nothing, and to decline when their
    messages do not say what is meant. A statement over 600 characters is not reworded.
  - **What is taken.** One Jev request (`assistant-memory-rewrite-v1`) judges the rewording against what was typed, a
    question for each way it can go wrong: it adds, it is wider, it drops, it is about another thing, it says something
    else, it still cannot be read alone, it names a person. Every gate must agree (`memory_decisions.REWRITE_GATES`). In
    code, a rewording that holds a current student's name or has lost a number or a limit of time the instructor typed
    ("this term") is refused before any request (`memory.carried`). Anything else (the model declines or fails, Jev does
    not answer, the memory changed meanwhile) leaves the words as typed, marked as before. Nothing is asked twice.
  - **Their words stay.** The rewording is a new revision (actor `assistant`, reason `edited`, the check's receipt in
    `consolidate_call`); what they typed is the first revision. The chat's note changes in place to the rewording with
    "You typed “…”", **Keep my words** (one step back to what was typed, `POST …/memory/<id>/as-typed`) and Undo; the
    Saved list and "Memories given" say "reworded by the Assistant".
  - **Measured** (2026-10-10, scratch `memory-rewrite-sandbox`, invented sentences only; live has no kept memory and no
    kept chat to measure on, so the instructor's own messages could not be used). Of 93 hand-written rewordings that must
    be refused (added, widened, dropped, changed, guessed, naming a person) none was taken: Jev refused 92 and the code
    check the one that had dropped "this term". Of those, 39 were in two sets the gates were not fitted to. The
    Assistant's model (gpt-6.1-sol, about 5 seconds a statement) declined 10 of 14 statements its messages could not
    resolve and wrote 43 rewordings; none added, widened or changed what was typed by my reading, and 27 were taken. The
    check refuses some good rewordings ("Print rosters without photos from here on." was one): those stay as typed.
    A different model on either side needs this measured again before it is used.
  - **Reworded more readily** (requirement, 2026-10-10: "Do 1 and 2", of "Reword more often": their own statement had
    answered .49 against the .5 at which a memory is marked). A kept chat statement whose `standalone` answer is under
    `memory_decisions.REWORD_UNDER` (.7) is sent to be reworded. From .5 up to it the memory is **not** marked as not
    reading on its own: the note is as for any kept statement, it changes only if a rewording is taken, and nothing asks
    the instructor to reword a sentence that reads. `GATES['standalone']` (the mark, and a follow-up's "Remember this")
    is as it was. A rule written without a student is never reworded.
  - **What was on screen** (the same instruction, of "Tell the writer what was on screen. This relaxes 'only your typed
    words'"). Their own words come first, exactly as above. Only when the model **declines** (their messages do not say
    what the statement refers to: "Shorter. Two or three sentences is plenty for these.") and a follow-up was on screen
    as they typed is the rewording written as a rule said of one student's case is: the same writer told `open_item`,
    the same code checks and the same Jev check (`_rule`), and here also `carried`. It is a rewording like any other:
    a new revision with that check's receipt, "You typed “…”", Keep my words and Undo. A rewording the words-only check
    refused is not tried again this way, and no follow-up on screen leaves a decline where it was. **Only under the
    rule's second wording** (`memory_decisions.reads_screen`, `ON_SCREEN_FROM`: the `-v2` writer and check of A
    FOLLOW-UP'S "REMEMBER THIS"), with which it was measured; under the first, one of nine fragments came out as a
    decision about the student's kind of absence that the instructor had not stated as a rule, and was taken.
  - **Measured for both** (2026-10-10, scratch `memory-situation-sandbox`, invented statements and invented mail only,
    nobody named; 299 Jev requests and 93 model requests). *The bar*, on 216 statements labelled by hand: of 74 that
    lean on the conversation, 60 answer under .5, 68 under .6 and 73 under .7; of 142 that read alone, 3, 8 and 17 (31
    under .75). Those 17 were sent on: two were reworded, each by leaving out a passing remark before the standing
    sentence, and the rest stayed as typed (five returned unchanged, seven declined, three refused by the check); told
    an unrelated follow-up as well, none took anything from it. *On screen*, on nine fragments typed in the chat about
    how to answer a student's mail: from the instructor's words alone none was reworded (eight declined, one refused);
    told the follow-up, seven were, each about the right kind of case and saying what was typed, three naming the case
    more narrowly than they need to ("replies to office-hours inquiries" for "these"). The rule's check on hand-written
    rewordings of the same fragments took 9 of 12 good ones and 3 of 31 that must be refused (one narrower than was
    said, one wider, one about the mail on screen instead of the instructor's earlier message), which is why their own
    words come first: three fragments that lean on an earlier message, typed with an unrelated follow-up on screen,
    never reach the follow-up (two reworded correctly from their words, one refused). Of eleven fragments nothing
    resolves, typed with an unrelated follow-up on screen, the writer declined ten and wrote one from the mail ("No more
    than two of those per week." beside a regrade request became "No more than two quiz regrade requests are allowed per
    week.", and was taken): a reading of what was on screen that only the instructor can judge, shown in the note with
    their own words one step away.
- **HISTORY.** Revisions are only added (`assistant_memory_revisions`: triggers refuse any other change): an edit, a move to
  another owner, a replacement, Remove, Restore and Undo each add one. **Erase** alone blanks the words of every revision
  and empties every picture kept with the memory, for something that should never have been kept; the memory then stops
  listing and its receipts remain (that a picture was kept, its size and checksum, never its bytes). Nothing prunes memories
  automatically.
- **PICTURES** (requirement, 2026-10-10). Asked what a remembered picture may be: **"Any picture I attach"**. Told that
  nothing in Faculty can tell a student's picture from their own (a file attached in chat has no origin), shown what could go
  wrong, and shown a check by OpenAI Decisions that refused 31 of 32 real scanned pages and none of 28 course-material
  pictures: **"Just implement without the guard for now. I will fix it if needed later."** So:
  - **What is kept.** When a memory is kept from a chat message, the pictures among the files attached to that message are
    kept with it (`assistant_memory_pictures`, at most four; a PDF or other file is not a picture and is left out). A message
    of files alone has no words and leaves nothing. The decision to keep is about the typed words alone, exactly as before:
    no Jev request holds a file, a file's name or its route, and a statement that names a student keeps no picture: one
    about that student is refused with its pictures, and a rule said of their case is kept without them.
  - **Nothing checks what a picture shows.** No guard, by the instructor's decision above: not its origin, not its likeness
    to a student's file, not a model's look. Keeping student material out rests on the instructor, on the line in the chat
    with the picture and Undo, and on Erase. A guard added later is a change to this rule, made with the instructor.
  - **Owner and storage.** A picture belongs to its memory through every revision, and so to the memory's owner (the
    workspace, a course or a term). **A replacement takes its pictures**: a memory that replaces a pictured one and brings
    none of its own is kept with copies of them (2026-11-09 review: said again with one more detail and no picture, a
    statement left its picture on a memory that no longer stands, where no request and no search finds it); one that
    brings its own keeps its own alone, and Undo leaves the earlier memory the pictures it always had. The bytes are a copy made for keeping (the attached file is scratch for a day): upright,
    flat, no larger than 1568 pixels, a PNG or JPEG of at most 2 MB, held in the catalog as a student photo is (STORAGE:
    catalog data in the nightly catalog backup, no payload file). A row is only ever erased (triggers refuse any other
    change and any delete). Served `no-store` at `GET /api/assistant/memory-pictures/<id>` until erased.
  - **Told by route, never shown unasked.** A request is told a memory's pictures as routes beside its words, with one line
    saying what they are: the instructor's own reference, never the work of a student being asked about and never evidence
    of what a student wrote. The picture itself reaches the model only if it fetches it (`fetch_files`; the route is in
    `policy.FILE_OWN`), which the chat's steps show. A memory with no picture is told and listed byte for byte as before.
  - **A picture finds memories.** The first picture attached to a message is compared on this machine with the kept
    pictures (PICTURE-SEARCH `like`, the `memory` source alone, waited for 2 seconds at most): a memory in scope whose
    picture is at least 0.92 like it is told with that request and marked so. Picture against picture on the live index,
    copies of one page scored .91 to .999 and other pages .77 to .92. No worker, a slow answer or no kept picture brings up
    nothing; nothing waits and nothing fails. Kept pictures are also found by PICTURE-SEARCH (source `memory`), by what the
    instructor said about them and by their look, while their memory stands.
- **MEMORIES GIVEN** (requirement, 2026-10-10, of "Show what influenced an answer … with correction and removal
  controls": "do 1 and 2"). Faculty knows which memories a request was given, never which of them shaped the answer, so
  the words are "given". Each request leaves one `memories` event naming what it was told by reference alone (`id`,
  `revision`, and why (`how`): `always` for one marked "With every request", `chosen` by the relevance check, `nearest`
  when that did not answer, `picture`): no memory's words are copied into a chat's events or a saved task. The conversation's view
  carries how each of those stands now (`memories`, read again only when a memory changed), so under the answer, inside
  the closed "How it got this · N memories", each reads as it is: its words, what it applies to, where it came from, when
  it was kept, its revision, "reworded by the Assistant", and "changed", "removed", "replaced" or "taken back since this
  answer". **Change** opens that memory's row on the Memory page; **Remove** removes it and **Restore** brings it back, in place. One erased since shows
  no words. A request given none says nothing, and its view is as it always was.
- **WITHDRAWN** (the same instruction; the review's finding 4: "After both Remove and Erase, my fake hosted engine still
  received the deleted instruction"). Every message carries the list that stands, but a model keeps the earlier lists.
  So before a message is sent, when a memory this conversation was told no longer stands as it was told (removed, taken
  back, replaced, erased or reworded; restored with the same words, it stands), what the model keeps of the earlier lists
  goes (`Conversation._refresh_memory`): Faculty's own history of the hosted loop loses them, and a model process that
  holds its own context (Claude, Codex) is ended and starts again with the conversation's words, as it does after
  resting; a selected area is given to it again in full. The model's own earlier answers stay as they were written.
- **WHAT A REQUEST IS TOLD** (`AssistantMemory.note`, in `Conversation.context_note`; requirement, 2026-11-09, of a chat
  that asked "do you have access to the assignment category weights?" and was given 39 memories about absences, regrades and
  iClicker replies: "check the memory system to see if this is by design or an unintended consequence", then **"let's do
  your suggestion and have relevance checks"**). Until then the workspace's memories went with every request, up to 6,000
  characters: a rule of 2026-10-10, when they were a handful of preferences. By that chat 55 stood (9,476 characters), 32
  of them rules learned from follow-up corrections, so the budget was full of case rules, and age and length decided which.
  Now **no memory is told for being in scope alone**:
  - **With every request** (`always`, migration 013; the tick on a memory's row on the Memory page, the instructor's alone). The few memories
    marked so go with every request made where they are in scope, up to 6,000 characters: their own instructions first, as
    kept, then rules learned from corrections, most recently revised first (provenance, not the actor on the last
    revision, tells these apart); one past the budget is judged like any other. The mark is part of what a revision
    says: changing it adds a revision and asks Jev nothing, Erase leaves it, a new memory starts without it, a Save that
    sends none (the old preference routes, a follow-up's "Remember this") leaves it as it was, and a memory that replaces
    a marked one takes the mark with its place. It exists because a way of writing is named by no request: "avoid
    em-dashes" answered .50 to .61 on requests that draft, at a gate of .6.
  - **Everything else in scope is judged** (`_chosen`): the workspace's, the course's and the term's alike. Every one while
    there are at most 64 (`JUDGED`; past that the 64 that NEAREST finds), eight to a Jev request
    (`assistant-memory-relevance-v2`, `CHUNK`, the number the check was measured with), all asked at once and waited for
    2 seconds by a chat and 10 by a preparation, which nobody watches and which loses every rule it needed when the check
    is missed. At most eight are told (`GIVEN`), the most useful first, each with a use receipt (`assistant_memory_uses`,
    naming its own request). The embedding worker no longer chooses what is judged while the memories are few: its
    nearest 8 held 53% of what the check found useful, its nearest 32 held 88%.
  - **Read by the two messages before it.** A chat request's state holds `earlier_instructor_messages`, the instructor's
    two messages before this one, as typed (the note of attached files left out, as for keeping), and the question says
    to read them only to tell what the request refers to. "can you draft these scenarios in the reply to her…", the third
    message of that chat, drew 27 of 64 memories read alone and 2 read with the two before it; "draft the reply" after a
    message about a sick student drew that case's five rules in place of seven unrelated ones; a request about something
    new after an earlier subject (four tried) drew nothing of the earlier one. A preparation's is empty.
  - **Set aside only by a plain countermand.** A chat's memory is set aside when the instructor's own request conflicts
    with it, at `conflicts` .9 (it was .5). On real memories and 20 invented requests, 8 that told the Assistant to do
    otherwise this time answered .73 to .97 (7 of them .91 or more), and 12 that only described a case the memory is about
    answered .06 to .82, two of them over .5: "she's asking for a regrade, the key is right" set aside "don't justify the
    answer by saying the answer key said so", the rule that request most needed. While the workspace's memories went with
    every request this could withhold only a course's or a term's fact. A reworded question separated them no better.
    One countermand in eight now passes with its memory, which the note already ranks under what they say now.
  - **No answer.** When no relevance request answers, the nearest eight are told unjudged and marked `nearest`; when some
    answer, what they judged is chosen from and the rest is left out. With nothing to judge by (a follow-up whose student
    wrote nothing) the eight most recent are told and nothing is asked of anyone.
  - **Measured** (2026-11-09, scratch `/mnt/nvme/Teaching/tmp/memory-relevance-replay`: the live catalog read-only, Jev
    asked directly with no receipt, about 2,000 requests; the 64 memories then kept, the latest student message of the 70
    most recent follow-ups with names out exactly as a preparation sends it, the three messages of the chat above, and
    36 invented chat requests). As built: a follow-up is told 0 to 8 memories, 3 at the median and about 400 characters,
    18 of the 70 none (each had been given 38 to 41); one drew more than eight. The chat's three messages are told 0, 2
    and 1 (they were given 39, 39 and 42). Of the invented requests, 17 are told nothing (every one that looks something
    up, prints, posts or schedules, but "put a makeup for exam 2 on the calendar…", which drew "shouldn't offer makeup
    opportunities until it's known that the absence is excused" at .75); one that states its case ("he says his iclicker
    didn't register his answers today, write back") is told that case's five rules at .61 to .93; "draft the reply" with
    nothing before it is told one. **Not measured:** which rules a draft needed has no record, so what is counted is the
    check's own judgement, read by eye on 48 requests; a general rule about tone ("Be a little gentler when mentioning
    that an absence isn't excused") is told for 20 of the 109 requests and answered just under the gate (.54 to .59) for
    some other absence cases. A different model needs
    this measured again.
  Another term's memory is never told. A memory never overrides the tool policy, a current record, or what the instructor
  says now, and the note says so. It is not evidence for the answer's check.
- **ONE PAGE** (requirement, 2026-11-10, told the tick was in the Assistant's Saved tab, inside each memory's own fold:
  "this is very cumbersome, we need a centralized setting page in the settings for the various memory functionalities across
  the app". Asked what it should hold, they chose all four offered: **"Assistant memories, inline"**, **"Rubric memories,
  all courses"**, **"On/off switches"** and **"Tuning numbers"**; where: **"Its own page"**; and of the places memories were
  managed before: **"Replace with links"**). `#memory`, titled Memory, opened from Workspace settings ("Settings elsewhere");
  `static/memory-settings.js`, `AssistantMemory.settings` / `configure`, `GET` and `POST /api/assistant/memory-settings`
  (the Assistant's own routes: policy `never`), table `memory_settings` (migration 014; owner: the workspace, one row).
  - **How Faculty learns: three switches**, each saved as it is changed. *Keep what I type in chat* off: a chat message
    is not considered at all (no Jev request, no note); what the instructor writes to be kept on the page, and a
    follow-up's "Remember this", are still kept. *Learn from my corrections of follow-up cards* off: no correction is
    looked for or considered (`FollowupLearning.active`); turned on again, learning starts from that instant
    (`learning_since`, `FollowupLearning.since`), so what was corrected while it was off is not learned from afterwards.
    *Learn rubric memories by itself* off: no pass is queued after a Certify or after rubrics are approved
    (`RubricMemory.learns`); "Learn from certified assignments" is the instructor's own request and still works.
  - **How much a request is told: three numbers**, each with its measured value beside it and "Use the measured values":
    the most memories one request is told (`given`, 1 to 20, measured 8), how strongly a memory must bear on a request
    (`use_gate`, .3 to .9, measured .6: the relevance check's gate for `use`), and the room for "With every request"
    memories in characters (`always_chars`, 0 to 20,000, measured 6,000). A number left at its measured value is stored
    as none of the instructor's own, so it follows the measured one. The other gates and limits are not offered: they
    were fitted together (GATES) and none was asked for.
  - **The Assistant's memories, one row each**: its words, how it was kept and given, and on the row itself "With every
    request" and what it applies to, which save as they change and ask Jev nothing; Edit opens its words in place (new
    words are checked as before, EVERY WAY IN); Remove, with Undo in its toast. What goes with every request leads the
    list; a filter shows those, the instructor's own, or the ones the Assistant wrote. "Add a memory", and "Replaced and
    removed" with Restore and Erase (asked twice in place), are as they were in the Saved tab.
  - **Rubric memories, every course**: the course's list as GRADING "RUBRIC MEMORIES" describes it (Keep, Drop, Bring
    back, Learn from certified assignments), one course at a time, chosen on the page when there is more than one.
  - **The places before are links**: the Saved tab says how many memories are kept and links to the page; a course
    Home's "Rubric memories" line counts them as before and opens the page on that course (`#memory?course=<id>`), and
    the old `#course/<group>/home/rubric-memories` lands there too.
  The settings change no permission, tier or approval, and nothing a setting turns off is done later in its place.
- **DRAFT REFERENCES** (requirement, 2026-10-13: "Implement these refined changes"). Each follow-up and workflow
  preparation records the exact memory IDs, revisions and selection method supplied in its opening context. Repeated
  delivery of a revision to the same run counts once. `assistant_draft_memories` belongs to the existing run: the
  enrollment through a follow-up run, or the workspace through a workflow preparation. It references the existing
  memory revisions and copies no words, prompts or student messages. It is durable without saving a conversation.
  The Memory view and revision history report how many drafts received that revision and, when it has happened, in how
  many of them the instructor still made the correction the rule is about: "This revision was given to 9 drafts. You
  still corrected 2 of them this same way." (requirement, 2026-10-13: **"do the recommended option"**, told that the
  first count shown, the drafts they had edited or replaced, read the same under every memory because every draft is
  given nearly all of them: 17 of 18 memories said "8 drafts, 0 edited" on its first evening). A draft counts for a
  memory when a learning case ended `known` with that memory (LEARNED FROM AN EDIT: the rule its correction shows was
  already kept) and the draft it corrected had been given that revision: the case's own run for an edited card or a
  reply sent from Mail, the run before it for a round asked for again. So it is about that rule and no other, a
  reworded rule starts again, and what came up under its old words stays in its history. A correction of a draft that
  was never given the rule is not counted: a change asked for in the chat is often kept as a memory there, and the
  round prepared again is then found "already kept" by its own request (the first case on live was this, and a count
  by time alone had charged it to the rule). Nothing is said when none has come up. The edited-or-replaced count is no
  longer made or shown. These are observations, never a success rate or proof that a
  rule helped. No exposure is inferred for older drafts. Undo and the existing learning queue/retry semantics are
  unchanged. The references are written beside the draft and never in its way
  (`AssistantMemory.given_to`; requirement, 2026-10-13, of a review of this change: "Please fix"): one that cannot be
  written is logged and the draft goes on, and a later turn of the same run writes only what is new.
- **A COORDINATOR'S FOLLOW-UP** (2026-11-14, ASSISTANT "A coordinator's follow-up"): its preparation is told memories by the
  rule below; its course and term are the term its owner key names, and it asks with the coordinator's last message in that
  conversation, in place of a student's. Nothing is learned from a correction of its cards.
- **A FOLLOW-UP ASKS WITH THE STUDENT'S LAST MESSAGE** (requirement, 2026-10-11: "use the student's last message";
  `SuggestionConversation.memory_request`, `AssistantMemory.student_said`). A follow-up's preparation is told memories by
  the same rule as a chat. Its course and term come from the run's student enrollment, even with several current terms;
  an absent or deleted owner never falls back to another current term (2026-10-13 refined changes). Its own request is
  its instruction: the same 12,000 characters for every follow-up, of which
  the embedding worker reads 8,000. Past the 3,000 characters every follow-up would have been given the same eight
  memories, and one Jev request would have held that whole instruction, the instructor's private notes with it. A
  preparation looks for its memories instead with the latest message the student wrote in the follow-up's conversation:
  subject and text, 1,500 characters, the roster's names, UINs and every address taken out, read as the preparation
  starts (a roster that cannot be read is a message that cannot be read: it never goes out with its names in it). The local
  embedding worker and the keyword rule are asked with that message alone. Then (the instructor, the same day: "allow jev
  for follow ups") the relevance requests a chat makes choose among them (WHAT A REQUEST IS TOLD): their state holds the
  preparation's task in one sentence (`FOLLOWUP_TASK`) and that message, and nothing else of the follow-up. What a student
  writes is never the instructor's word: a chat's memory is set aside when the instructor's own request conflicts with it,
  and a follow-up's never is, since a student arguing against a rule is when the rule is needed (`relevant(...,
  conflicts=False)`). When Jev does not answer, the nearest eight are given, marked `nearest`. When the student wrote nothing
  there, or it cannot be read, the eight most recent are given and nothing is asked of anyone. The gate for `use` was
  fitted on requests the instructor typed; on 70 real follow-ups (2026-11-09, WHAT A REQUEST IS TOLD, "Measured") what it
  passes was read and counted, against no record of what each draft needed.
- **A FOLLOW-UP WAITS FOR A LENT GPU** (requirement, 2026-10-11: "I want follow-ups to wait on lent cards"). While the
  local model that reads a blank exam's answer areas has the GPU (EMBEDDING "LENDS THE GPU": about 17 seconds a page), the
  embedding worker refuses lookups and a chat takes keywords at once. A follow-up's preparation, which nobody is watching
  and which takes minutes, instead has its lookup held by the worker until the card is back, for up to 60 seconds
  (`LENT_WAIT`); it is answered between two loans. Past that, or when the worker is down rather than lent, keywords stand
  in as before. Nothing else waits: not a chat, not past-work recall, not a search.
- **PREFERENCES** are the workspace's memories. `store.memories()`, `store.memory()`, `/api/assistant/memories` and the
  `saved_preferences` tool keep their names and shapes. The earlier `assistant_memories` table stays for a process still on
  the earlier code; its rows are carried in once at start.
- **NEAREST** (`AssistantMemory._nearest`). The kept memories nearest a statement or a request come from the local
  embedding worker (EMBEDDING: EmbeddingGemma 2), joined with the keyword rule's first two picks so a shared word is never
  lost. When the worker is not configured or does not answer, keywords and the most recent stand in; nothing waits and
  nothing fails.
- **NOT BUILT YET:** Past-work recall is built as a read of its own, never a memory (PAST-WORK, 2026-10-10). Follow-up
  lessons on the same search were withdrawn with the lesson review (2026-10-12): what a correction teaches is a memory
  (LEARNED FROM AN EDIT), and a change the instructor asks for is considered once its round is approved. A decline's note
  teaches only with "Remember this" ticked. More than one rule from one correction is not built. External MCP sessions get
  no memory. The outside
  review's five findings are all answered: 3 and 5 under KEEPING, 1 by EVERY WAY IN, 2 by IN THE ORDER SENT, 4 by WITHDRAWN.
- **CHECKS.** Synthetic catalogs and scripted Jev answers only: the decisions; keeping, refusing, replacing and Undo; owners
  and scope; history and Erase; the old preference routes; what a request is told (every unmarked memory judged, eight to a
  request, read by the two messages before it; only what bears on it told, the most useful first and at most eight; a
  marked one told unjudged; the mark kept by a Save that sends none and by a replacement, and a revision's alone to change),
  the cap and the outage fallbacks; that no tool reaches a memory route; and the drawer in a real browser. And ONE PAGE: the
  switches on and the numbers measured until set, each refusal of a bad value and of a stale version, keeping from chat
  switched off asking nothing, a request told as many as allowed at the bar set, learning from corrections following its
  switch and starting again from when it was turned on, rubric learning by itself following its switch while the
  instructor's own request does not; the page's markup (`tests/test_memory_settings.mjs`), and the page in a real browser
  (`browser_assistant_memory`, `browser_rubric_memories`). Also: what the form and a follow-up refuse and
  that no answer keeps nothing; messages held up and considered in order from what was seen when sent; the rewrite step
  with a scripted model (taken, each refusal, a decline, a failure, a student's name, a change meanwhile, Keep my
  words); what a request was given by reference and as it stands; and a removed, erased or reworded memory leaving an
  open conversation's history or ending its model process. And a rule said of one student's case: kept as the rule with
  the typed words in no revision, each refusal keeping nothing and saying so, a statement about the student still
  refused with no rule written, no picture kept, and the follow-up on screen told to the writer and the check without
  names. And the rewrite step's two additions: a statement between the two bars reworded without being marked, none at the
  bar or over it; a fragment the model declines written from the follow-up on screen with the rule's check, only under
  the rule's second wording, never when their words resolve it or no follow-up was open, and each refusal leaving the
  words as typed. And STILL BEING CONSIDERED: a view saying a message is being considered until its last note is in and which
  memory is being reworded, and the instructor's own rewording reaching the chat whose note led to the form.
  And a follow-up's "Remember this": words that read alone kept as typed, a fragment kept as the rule written
  from that follow-up (and as typed, marked, when no rule is taken), a named rule kept without the student or not at
  all, what is about the one student or a record's fact refused, no answer keeping nothing, the page told what was
  kept, and Undo.
  And a rule learned from a correction (`tests/test_followup_learning.py`, `LearnTests` in `tests/test_assistant_memory.py`,
  `manage_checks.py browser --browser followup_learning`): only a confirmed correction found, once, and none from before
  the cutoff; an edit of an edit against the Assistant's first draft; one correction a round, asked on the page or in the
  chat; a reply sent from Mail; each comparison holding no name, number of the draft, grade, date or private note; a
  decision changed back and a grade-only edit teaching nothing; the rule kept as the Assistant's with both receipts and
  Undo; each code check and each gate refusing; no answer keeping nothing and asked once; a rule already kept; a learned
  rule replaced by a newer one and typed words never; a full store; the memory and its case's record as one transaction;
  a correction taking its turn behind earlier messages; what a request is told; the page read carrying it and the default
  read unchanged; the toast, the line, Undo and the Memory label in a real browser; and the lesson routes gone.
