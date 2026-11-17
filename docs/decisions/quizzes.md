## Quizzes drafted by a model

Rule ID: **QUIZZES**. Current binding module contract. Read root `AGENTS.md` first.

2026-10-09 assistant materials: an approved material bridge may create a quiz draft from bounded generated text or an existing
course-wide artifact, through the normal multipart route with explicit term, versions and total points. It never approves the
draft, prints it or changes the existing draft ownership and review rules.

- RUBRIC CHOICES (requirement, 2026-10-24; GRADING "FEWER RUBRIC QUESTIONS"). The new draft form asks the course's few recurring
  rubric questions (work, rounding, spelling of required words, the "all incorrect" line), starting from the course's latest answers;
  the request keeps them (`rubric_choices`, named only where some were chosen), every rubric writer and the draft's rubric check read
  them as `assignment_choices`, and the approval records them as the new plan's. The draft's decisions list the reviewer's guidance
  fixes apart under "Fixed for you", each with "Ask me instead". Since 2026-10-29 (GRADING "FEWER RUBRIC QUESTIONS" › 2026-10-29) its
  key fixes too ("will also accept … once the quiz is approved", made on the plan at approval, even after an answers revision
  resolved them); "Ask me instead" is sent with the answers (`unfixed`); a fixed finding is no open decision in the page's counts or
  the approval's tick; decisions of one `topic` across problems are one; an `edit` the instructor approves asks for a revision making
  exactly that points change; and an answer that changed a line's clarification rewrites that line's drafted clarification. The
  choices include "Letters and notation".
- ASSIGNMENT WORKSPACE (requirement, 2026-10-03). Drafts retain their draft identities and approval controls. After approval,
  Open assignment follows the approval's permanent assignment and term into Assignments (ASSIGNMENT-WORKSPACE). The workspace's
  paper view retains the interleaved print stack, version-specific files, and superseded-stack checks from the printing service.

- The instructor asked (2026-09-28): "I want to be able to have a model draft quizzes and rubrics based on material I will provide.
  When the model drafts the quiz, I should be able to approve it or ask for changes and call the model again to make the changes. Once
  approved, the quiz and all of its versions should be sent to printing for stamping. It should use a latex template like the quizzes
  already done and the rubrics should automatically be associated with it in terms of the grading pipeline." Asked, they chose: the
  Rubric drafting and review model writes quizzes too (no setting of its own); the earlier quizzes' look is rebuilt, first from their
  PDFs, then from the instructor's own Chapter 3 Quiz source they gave the same day (its header, headings, list spacing, 0.52/0.46
  columns, LP scaffold, corner table and TikZ graph conventions are `facultyquiz.sty` and the writer's examples); the versions are printed UNNAMED (until 2026-10-01 each stamp's caption showed the version; see NO VERSION ON THE PAGE), and "the student name
  will have to be read" from the paper (no seating charts for quizzes yet); and approving a quiz also confirms its templates, its answer
  areas and that its rubrics are ready for the rubric review. `grading/quiz_drafts.py`, `grading/quiz_api.py`, migration 034,
  `document_identity/quiz_latex.py`, `document_identity/templates/facultyquiz.sty`, `static/quiz-drafts.js`.
- OWNERS. A draft (`quiz_drafts`) is COURSE-WIDE authoring owned by the course definition, naming the current term whose sections it is
  for (its title line prints that term); its material and every compiled file live in the definition's verified storage under
  `quiz-drafts/<draft>/`. Each model run is an immutable revision (`quiz_draft_revisions`, its calls ordinary cloud calls owned by
  `quiz_revision_id`); asking for changes adds one based on the revision shown. Approving is the instructor's decision for the draft's
  term (`quiz_draft_approvals`, `confirmation: "approve"` checked on the server, one live approval per draft and term). Everything is
  retained; nothing is created, printed or graded before approval.
- SCRAPPING A DRAFT (requirement, 2026-10-01: "there should be an option for me to scrap the draft if I don't like it"). A draft never
  approved may be scrapped from its page's ⋯ menu (POST `/api/quiz-drafts/<draft>/scrap`, `confirmation: "scrap"` checked on the server;
  `scrap_quiz_draft`). Drafts are retained, so scrapping is one immutable record beside the draft (grading migration 038,
  `quiz_draft_scraps`, owned by the draft, naming who asked: `request_actor`). A scrapped draft is no longer listed, its revisions and
  approvals are refused, and a revision still pending or running is stopped in the same transaction, so nothing further is sent. Its page
  still opens (tabs opened before keep working) and says it was scrapped. A draft whose approval was carried out or is being carried out
  is an assignment with print jobs and is never scrapped; what a failed approval already made stays. Its material, revisions, compiled
  files and receipts stay where they are; scrapping deletes nothing and is not undone from the page. The assistant's route policy does
  not name the route, so the assistant needs an Approve card for it.
- THE REVIEW (requirement, 2026-10-05: "do the quiz draft window next", after "walls of text are never helpful" of the makeup window;
  DESIGN-LANGUAGE principle 13; `static/review-notes.js`). A draft's page leads with the revision's facts, a needs-you count of what
  to check, what concerns the whole quiz (the page check's general issues, a lecture-notes warning) and the first sentence of each of
  the writer's paragraphs about the whole quiz, all of them in one fold; the chosen version's pages are thumbnails beside it with the
  PDF and LaTeX links. Under the version tabs, one row per problem: the problem as printed on the chosen version (an SVG crop that
  opens the page) beside its checks (a layout or check finding said once with the versions it is on, "Every version: …" when on all;
  rubric warnings; a rubric that could not be drafted), the first sentence of the writer's paragraph about it (one that starts
  "Problem N", "For Problem N" or "Problem N:") and of its rubric's notes, then folds for all the model's notes and for the rubric
  lines (points and the chosen version's wording) with the solution and answers, and the problem's changes box. Once approved the
  writer's notes on the whole quiz fold; the counts and marks stay. Nothing is asked of the model again.
  READ AT A GLANCE (requirement, 2026-10-06: "do the same for the quiz draft window", after the rubric finalization's). Beside each
  problem the answer of the version shown comes first ("Answer": the correct choice and its wording, or the final answers with their
  parts), so the key is checked without opening "Rubric and solution", which keeps the worked solution and the rubric lines; the
  notes are labelled "Quiz writer" and "Rubric writer". A note whose first sentence ends with a colon keeps the short list it
  introduces ("The answers on the four versions:" stood alone). The layout's room warning says what to check: "Every version: only
  2.9 cm left to write in. Enough room?" (the makeup window shares it). A problem's changes box opens on request ("Suggest a change
  to problem N"; open while it holds text), and Ask for changes names the problems' changes it also sends. The approval window's
  first line says what approving will do as the window stands ("Approving prints 3 copies of Versions A and B exactly as shown for
  Section 501, as one stack, and sets up its grading. Key & Rubric Finalization still waits for you."); its notes sit as hints under
  their fields. Display only: no route, stored field or approval rule changed.
- THE LAYOUT IS FACULTY'S. The model returns structured data only (`quiz_latex.clean`): per problem its kind (multiple choice or free
  response), points, parts, page and share of the page's space, and per VERSION the LaTeX fragments of its statement, left column,
  parts, choices and correct choice, TikZ figure (`\quizaxes` draws the quizzes' graph frame) and answer scaffold, with a worked solution
  and final answers. Faculty writes main.tex (`quiz_latex.render`) in `facultyassignment` (the stamp boxes stay empty; since 2026-10-01
  the class's two 10 mm corner codes, DOCUMENTS "TWO CORNER CODES BY DEFAULT", so the room left to write runs to the 30 mm bottom
  margin, `quiz_latex.BOTTOM`) with
  `facultyquiz.sty`, and every fragment is checked first (`quiz_latex.fragment`): no file access, macro definitions, page breaks,
  negative or starred space, page-size changes or comments (`\renewcommand{\arraystretch}{..}`, which the instructor's tables use, is
  the one redefinition allowed). `\quizaxes[grid]{xmax}{ymax}{xstep}{ystep}` and the `quizline`/`quizlabel` TikZ styles draw graphs as the
  instructor's quizzes do. `\$` in text is typeset from Computer Modern (the sandbox has no bitmap
  fonts). The compiler now accepts `.sty` sources; `.tools/texlive` gained `texlive-pictures` (pgf/TikZ).
- A REVISION (`run_quiz_revision`, the cloud dispatcher): one `quiz_write` request writes every version at once from the material (text
  per page; pictures of pages with little text), the chosen past quizzes' blank templates and the instructions, and when revising the
  previous quiz and the instructor's requested changes. Each version is compiled in the sandbox; compile errors and layout faults
  (`quiz_latex.layout`: a problem off its planned page, more pages than planned, content in the stamp box) go back to the writer, at
  most `FIX_ROUNDS` times. On a Claude or ChatGPT subscription connection the writer, and every fix round, may first compile its quiz
  exactly as Faculty will and look at the pages it printed (`compile_quiz`: `quiz_latex.clean`, then the same build, reporting each
  version's pages, where each problem landed, the room left to write and every fault; `look_at_pages`; grading.md "DRAFTING TOOLS"),
  may read the course's lecture notes and search its question bank (`lecture_notes`, `question_bank`, `quiz-writer-v3`; instructor,
  2026-10-01: "add them to the quiz writer"; uploaded material is still sent with the request), and
  each problem's rubric writer gets the rubric writers' course context; whatever Faculty's own compile still finds goes back as before.
  ANSWERS GIVE ONLY WHAT CHANGES (requirement, 2026-10-01, after Quiz 4's writer typed its four versions out three times in 8 minutes
  and its one check fix, a 4 mm gap on Version D, five times in 8 more): a `quiz_write` (v3) answer has a `base`, "none" for the whole
  quiz, "previous" for the request's previous quiz (a revision or a fix round) changed by its `edits`, or "compiled" (tools only)
  for the writer's last successful compile changed by its edits; each edit replaces one version's whole content of a problem and/or
  sets its page, space, tag or rubric feedback (`resolved`; kind, points, parts and which problems there are change only with the
  whole quiz). `compile_quiz` takes the same fields (`quiz-writer-v2`). A "compiled" answer is rebuilt from the call's receipt
  (`compiled_from`; every compile's arguments are kept whole, `tools.ARGUMENT_CHARS`), so a resumed revision replaying the call reads
  the very same quiz, and a refused answer asked again starts with nothing compiled. `facultyquiz.sty` v2 keeps a display's full
  space above and below after a short line, the cause of that Version D finding.
  One `quiz_check` request reads every printed page and reports what to fix (correctness, figures, layout,
  versions, wording); its findings go back to the writer ONCE, and what remains is shown to the instructor.
  THE CHECK KEEPS WHAT WAS ASKED (requirement, 2026-11-04, of a take-home's revision: "i asked for revisions, but it looks like it's
  been going over the same things over and over"; the revision was not looping, but its check, shown only the format and
  instructions, found the answer lines just asked for left too little work room and had the fix take them off four of five
  problems, while "What changed" still said every problem had them). `quiz_check` v5 and every fix round (`quiz_write` v6,
  `makeup_write` v2) get `asked`, all the changes requested on the way to the revision, oldest first (`asked_changes`, walking
  `based_on`; a first draft sends none). What it asks for stands: a shortage of room it causes is fixed by making room, never by
  undoing it. "What changed" keeps the writer's account and adds each fix's (`after_fix`). `asked` is not part of the printed keys,
  so the check still skips what it saw. The page names a stage from its first request, not its first answer, and says when the
  check's findings are with the writer (`fixing`); the rubric check had read as "Reading each problem's answer key".
  THE CHECK SKIPS WHAT IT SAW (requirement, 2026-10-11, choosing the quiz check's skip of unchanged problems among the small
  repeats to remove; what the check judges is unchanged). Each revision keeps in its result (`check.seen`, owned by the revision)
  the `printed_key` of every problem its check actually SAW (`quiz_drafts.printed_keys`), with the findings about it that still
  stand: its fragments as written on every version with their solution, final answers and correct choice; its number, kind, tag,
  points, parts, page and space; the heading above it and the problems sharing its page; each version's compiled box, room to
  write and area; and the frame every problem shares (the request, title, sections line, directions, points, page counts,
  Faculty's class and style, the example pages, the `quiz_check` prompt). `signature` is not that key: it leaves out spacing and
  figures, which the check judges. A fixed quiz is not checked again, so a problem the fix changed or moved has no seen key and
  its finding does not stand. A revision based on another asks the check (`quiz_check` v3, `unchanged`) only about the problems
  that revision's check did not see as now printed; the findings about the others are carried as they were and join this
  check's (back to the writer with them, shown when the fix does not hold). Pages holding only unchanged problems are left out,
  the quiz's data are sent whole, and every page is sent when the earlier check left a finding about the whole quiz (problem 0).
  Nothing printed changed: no check request. A revision recorded before 2026-10-11 has no seen keys and is checked in full. A
  makeup's revisions share this (GRADING "MAKEUP VERSIONS"). Then each problem's rubric
  is drafted for all its versions at once (`quiz_rubric`, the all-versions rubric writer's department rules verbatim, validated by
  `family_rubric`: shared points, a wording per version, drafted clarifications). A revision keeps an unchanged problem's rubric
  (`signature`) unless the feedback names its grading or the notes below differ from those it was drafted with (`notes_sha256`).
  THE KEY IS READ WHILE THE QUIZ IS DRAFTED (requirement, 2026-10-11, "Carry the key through"; measured that day: the drafting
  model worked each answer out three times, a question was still created without a key, a deducting rubric gave none to read,
  0 of 16 and 0 of 15 questions, and every key came from a reading asked for afterwards that was sent neither the solution nor
  the writer's answers; the writer's own `final_answers` are composite prose the matcher cannot compare, 0 of 21 questions,
  while the reader's keys matched 259 of 541 full-credit answers). After the rubrics, each problem's key is read once per version
  (`_quiz_keys`, step `quiz:key:P<n>:<letter>`, owned by the revision, in parallel) by the plan's own reader contract (GRADING
  `KEY_RULE` `answer-key-read-v2`), sent the question's title, printed number and points, that version's rubric lines exactly as
  its question will get them (`version_rules`) with the full-credit line, the problem as written, the worked solution, the
  writer's final answers and correct choice, and the printed page holding the problem; multiple-choice problems alike. The
  reading is kept with the problem's rubric (`rubrics[i].keys[letter]`: `{rule, key, basis, unreadable}`, `error` when it
  failed), so a rubric carried from the revision before carries its keys and nothing is asked; a version carried without a key is
  read again. A reading that fails or cannot read never fails the revision: no key for that problem and version, and a warning
  ("Version B: Problem 2 has no answer key: …"). A part read as several values under one letter is kept, numbered (GRADING
  "ONE PART, SEVERAL VALUES", 2026-10-29). The page shows each key under Answer ("Answer key"), how each part was found
  folded with the solution.
  THE RUBRIC CHECK IS MADE WHILE THE QUIZ IS DRAFTED (requirement, 2026-10-11, "Rubric questions at draft review", chosen from a
  measured list: the check ran only after approval, so its concerns waited for a second sitting; Quiz 4 needed 10 check runs;
  fresh checks of the same unchanged rubrics raised different concerns each time; every proposed clarification, 700 in 8
  confirmations, was confirmed as proposed; no Resolve round opened a concern on an untouched line). After the keys, a revision's
  last step runs the plan's own rubric check (GRADING `run_rubric_check`: the same prompts, rules, scheme, links and validation;
  a one-version quiz one question per request) on what approving WOULD create (`draft_check_context`: one question per problem
  and version under a virtual ID `draft:<n>:<letter>`, its rules by `version_rules`, the drafted clarifications as its comments,
  and as reference the problem as written, the worked solution and the key read for it; the reviewer is sent no ID). Its requests
  are the revision's own (`DraftCheckHost`, the check's step names under `quiz_revision_id`), each printed problem cropped from
  the revision's pages at the draft's areas. The revision keeps (`rubric_check`) the result, the reviewer's profile, per question
  the `content`, `reference` and `printed` digests (`check_digests`), each version's PDF SHA-256 and the check's fingerprint; its
  receipts go to the provider record. A check that fails is a warning, never a failed revision; a request waiting for its host
  makes the revision wait. A PROBLEM THAT READS AS BEFORE KEEPS ITS RESULT: where its rubric was carried and its questions'
  content and reference digests are what the based-on revision's check recorded (same reviewer), its entries, findings, links and
  answered decisions stand and nothing is asked; judged per problem, never per request, and pictures are not compared. ANSWERS
  ARE A REVISION THAT RUNS ONLY RESOLVE: POST `/api/quiz-drafts/<draft>/rubric-answers` (`based_on`, `check_sha256`, `responses`
  [{concern, response}]; `answer_quiz_rubric_check`) queues a revision whose quiz, rubrics, keys, page check and files are the
  shown revision's, referenced unchanged, and which runs only GRADING's Resolve from the same input (`resolves_input`); a
  resolution that fails fails that revision alone. The page says how many decisions are open, shows them in the finalization's
  decisions view read only, and under each rubric line the guidance the check holds. A makeup draft keeps its own flow. The
  assistant's route policy does not name the route, so the Assistant needs an Approve card for it. EVERY REVISION NOW PAYS FOR A
  CHECK (one reviewer request per changed problem), also a draft never approved; measured once on a real model, a first revision
  of three problems in four versions took about 7.5 minutes longer (CHECKS.md). THE SWITCH (requirement, 2026-10-11, told that
  time and that no switch existed: "build the switch"): workspace `draft_rubric_check`, on by default, Compute settings "Check the
  rubrics while a quiz is drafted" beside the Rubric drafting and review model. Off in the settings a revision was REQUESTED
  with (never the current ones: a revision that waits ends the way it was asked to), that last step is not taken: the revision
  holds no `rubric_check`, the reviewer is asked nothing, the page shows no decisions and no box, and the approval queues the
  plan's check exactly as before 2026-10-11. A check an earlier revision holds stays that revision's, and answers to its
  decisions are still a revision; a revision asked for with the switch on again checks every problem, since the one before
  holds no result to keep. Like the other workspace preferences it never enters a grading batch's scope.
  THE NOTES THE QUIZ COVERS GO TO EVERY RUBRIC WRITER (requirement, 2026-10-01: "always send the notes sections the quiz covers to the
  rubric writer, this is just to prevent the model from using a method the students don't know as a grading meter";
  `drafting_tools.quiz_notes`, `quiz_rubric` v3): the course's current lecture notes (COURSE-NOTES), each section the quiz itself
  names in its sections line or a problem's tag ("Sections 3.1–3.3" takes every section between; "Chapter 3" every section of it),
  as text read in the document sandbox (Word's doubled math letters halved), at most `NOTES_TEXT` characters, on every connection,
  tools or not, in `course_notes`. The writer builds every line that names a step or method on the method the notes teach and never
  on one they do not teach; which methods EARN credit is unchanged (any valid method unless the problem prescribes one). No notes,
  no matching section, an unreadable part or a cut is a warning on the revision; the sections sent are recorded (`rubric_notes`).
  NOTES SECTIONS FOR THE WRITER (requirement, 2026-10-28: "can we add a menu to the quiz drafting page that allows me to add
  sections from the notes and it will automatically know to pull those notes into context when drafting?"; asked first whether the
  writer read the notes, its records showed Quiz 4 never opened 3.1 though the instructions named it). The new-draft window lists
  the sections of the lecture notes the course page shows, by chapter ("Lecture notes to read", `notes_sections` in the options from
  `course_notes.service.current_sections`), none ticked. The ones ticked are part of the draft's request (`quiz_drafts`,
  course-wide, `notes_sections`, named only where some were chosen so a request without any keeps its fingerprint), kept by NUMBER
  in the notes' order (COURSE-NOTES "NAMED FROM OUTSIDE BY NUMBER"); a number the notes do not hold is refused. Every revision's
  writer request is sent their text (`course_notes`, `drafting_tools.chosen_notes`, at most `NOTES_TEXT` characters, read against
  the edition shown at that read), on every connection, tools or not; fix rounds are not sent it again. `quiz_write` v5 is v4 with
  one bullet: problems are built on what these sections teach, by their methods and notation, and unless the instructions say
  otherwise the quiz covers them. The rubric writers are sent the chosen sections with those the quiz prints (THE NOTES THE QUIZ
  COVERS). The revision records the sections the writer was sent (`writer_notes`, only on a draft that chose some); a section gone
  from the notes, a cut or an unreadable part is a warning on the revision. The `lecture_notes` tool stays for anything more.
  THE BANK'S QUESTIONS OF THOSE SECTIONS GO WITH THEM, NEVER A COPY (instructor, the same day: "can we have it do the same for the
  question bank ie pull questions from the question bank from those sections as context. We need to make sure it doesn't just copy
  those questions, it needs to change them slightly"): every revision's writer request also carries `bank_questions`
  (`drafting_tools.chosen_bank`, QUESTION-BANK read only): for each chosen section, up to `BANK_PER_SECTION` of the questions filed
  under it or a topic or objective under it (not retired; a question under two chosen sections goes with the first; one version
  of a problem printed in several, by its linked versions or family), those printed on the course's past assessments first, then
  medium, hard and easy, newest first, the kinds in turn, at most `BANK_TEXT` characters in all, with how many the bank holds
  there. `quiz_write` v5 says to use them as models and never copy one: a problem based on one has at least new numbers, with its
  setting or wording changed where natural. Faculty refuses a writer's answer in which any version of a problem reprints a bank
  question it was sent (`bank_copies`: the same numbers in the same order and words at least `COPY_LIKENESS` alike, LaTeX commands
  set aside) and asks once more with the reason (`_quiz_ask`); refused again, the revision fails saying so. Changed numbers are a
  changed problem. Replayed read only on the live catalog: Quiz 5's own Version D problems, sent back as bank questions, are all
  refused, and its Version E makeup, written from it, is not. The revision records how many were sent per section (`writer_bank`).
  SECTIONS ARE MATERIAL ENOUGH (instructor, the same day: "I shouldn't have to give additional information or files, it should
  automatically pull the relevant section's notes and question bank questions for that section"): a request with ticked sections
  and no material or instructions is drafted (refused with nothing at all: "Tick the lecture notes sections the quiz covers, or
  provide material or instructions for the model."). Until a box is changed by hand, the window ticks the sections the
  instructions name ("Sections 4.1–4.3", "3.4 and 4.2", "Chapter 5"), else those the title names ("Chapter 5 Quiz"), as you type
  (`namedSections`, the window's copy of `covered_sections`). The window always sends the field, so none ticked is none; a caller
  that sends no field at all (the assistant's material bridge) gets the sections its instructions, else its title, name.
  The request's "Instructions for the model" (renamed from "What should it cover?" on the
  instructor's word, 2026-10-01) go to the writer, the check and every rubric writer (`instructor_instructions`, `quiz_rubric` v2 on);
  revision feedback about a problem's grading is newer and wins where the two differ. The request also carries the quiz's FORMAT
  (requirement, 2026-10-01: "prefilled by default by the current format, i.e. one multiple choice, 3 free response, but I should be
  free to edit it"): the dialog starts from `FORMAT`, sent as `default_format`; an absent format is that default and an empty one is
  none. It is part of the draft's request (`quiz_drafts`, course-wide), goes to the writer and the check (`quiz_write`/`quiz_check`
  v2), and wins over the instructions where they disagree about the problems (said in the notes); revision feedback wins over both. The class writes each problem's start, body end and end to the build log
  (`\pdfsavepos`); its answer area runs from its heading to the next problem's heading or the page end across the sheet
  (`locations.AREAS`).
  GRADED FOR PARTICIPATION (requirement, 2026-11-01; GRADING "GRADED FOR PARTICIPATION"): the window's box "Graded for
  participation" sends `participation: "1"`, kept in the draft's request (`participation: true`, named only when ticked, so every
  other request keeps its fingerprint) and shown on its card and page. Ticking it sets Versions to 1 ("graded for participation
  assignments should default to only one version"), and unticking it back to 4, until a number is typed. The model writes the quiz
  as ever; the approval records the setting on the new plan with its rubric choices (not where the plan has a bubble-sheet key).
- APPROVAL (`run_quiz_approval`, step by step, every step reusing what an earlier attempt made, so a failed approval is approved
  again): the course-wide assignment; one printed version per quiz version labelled A, B ... from the very PDF approved (compiler
  `generator: faculty-quiz-draft`); the term's exam record and grading plan; every version's questions (`create_draft_question`,
  exam_version = the letter, document = that version's printed version, compiler `model-drafted-quiz-v1` with the draft's family so the
  rubric check groups the versions), the drafted clarifications as line comments; each version's template imported from its own PDF
  and confirmed (the instructor's approval acknowledges the digital-template warning); each question's answer area recorded as a
  completed check of the LaTeX layout (`faculty-quiz-layout-v1`) and confirmed, exactly as a confirmed OCR proposal is; every rubric
  confirmed for the rubric review and the review requested. The mandatory Key & Rubric Finalization still waits for the instructor.
  THE APPROVAL SAVES THE KEYS IT SHOWED (requirement, 2026-10-11): the draft's page sends `save_keys` (recorded in the approval's
  input; absent, it reads and checksums as before), and only then is each question created with the key read for its problem and
  version, in its first revision, before the rubric review is requested, so the review's reference carries every key
  (`questions.keys` in the progress says how many hold one). That approval is the instructor's save of the keys they saw; a key
  is evidence only and selects no line. An approval without `save_keys` (a tab opened before, an assistant's card) saves none,
  and the key stays a proposal until saved in Key & Rubric Finalization. A question an earlier failed attempt created keeps what
  it was created with.
  THE CHECK MADE WHILE DRAFTING IS THE PLAN'S WHERE IT IS PROVED THE SAME (requirement, 2026-10-11). Before requesting the review,
  the approval compares the plan's own check input with what the revision's check read: the plan would ask every question, they
  are exactly the created ones, and for each the `content` digest (what a confirmation covers of it, less its IDs), the
  `reference` digest (problem, solution, key), the registered PDF and the confirmed areas equal the recorded ones. Then the check
  is INSERTED complete under `quiz-review:<revision>` (`record_drafted_check`; no migration) with the input and `input_sha256` a
  queued check would have (`rubric_check_input`), its result under the plan's question and rubric IDs. Anything less (no
  `save_keys` where a key was read, a failed check, a question an earlier attempt created otherwise) queues the check as before.
  ONE TICK MAY BE THE FINALIZATION: the window offers one unticked box only for a finished check with no decision open and no
  points overflow; ticked, the request carries `finalization: {confirm: true, check_sha256}` (absent, the approval reads and
  checksums as before), accepted only for the check the revision holds with nothing open, and the worker calls the ordinary
  `confirm_rubric_quality` on the carried check. Not ticked, or not carried, Key & Rubric Finalization waits as before, and the
  progress says why a ticked finalization was not recorded (`review.carried.not_confirmed`). The window's first line says which
  of the three it will be. The plan is on Grading from the approval on (`grading_entry` `ready`, marked
  `set_up_by_quiz_draft`), so the rubrics can be finalized before any paper comes back: an EXTENSION of "scan association alone never
  puts a new assignment on Grading", for plans an approved quiz set up or that the instructor set up with Set up grading now (GRADING
  "SET UP GRADING NOW", requirement, 2026-10-02, `set_up_before_papers`), and no other. Last, one unnamed print job per VERSION owned by
  the term, naming no section (requirement, 2026-10-01: "I intend to mass print these out and not separate stacks for each section";
  DOCUMENTS TERM PRINT JOBS): the approval names the sections ticked and the copies of each version for all of them (default: their
  students shared by the versions), `create_batch` with `term_id` and those `section_ids`, with no caption (until 2026-10-01 "<title> · Version A · Copy k"),
  attached to the exam record. `DocumentService.section_stack` re-cuts the ready jobs
  into one PDF with the versions' copies interleaved (A1, B1, C1, D1, A2 ...), like the room file: nothing issued, the per-version
  files stay the record. It is what the quiz PRINTS (requirement, 2026-10-01: "I don't want versions per section, I want each section
  to get 4 versions so the students cant cheat off each other"): the Print page lists the approval's jobs as one group whose Print
  PDF is that stack (DOCUMENTS "ONE STACK, EVERY VERSION"), and the draft's page links the same file. An approval from before 2026-10-01 keeps its per-section jobs and stacks.
- GIVEN ON (requirement, 2026-10-02: an optional "'Given on' date" in the approval that puts the quiz on Home's Coming up, the
  Calendar and its Exams page). The approval dialog asks for it after the Sections, empty by default; the date input's own
  YYYY-MM-DD is sent as typed (a campus wall-clock date, never converted through a time zone) and checked on the server
  (`given_on`, absent when empty, so an approval without it reads and checksums as before). After the exam record and plan are
  made, the worker gives the record its main sitting on that date, labelled with the quiz's title, through the Exams page's own
  sittings path (`_quiz_given_on` → `ExamService.save_sitting`, what `POST /api/exams/<event>/sittings` calls); a main sitting
  already dated (the Exams page's, Create assignment's, or an earlier failed approval's) is kept, an undated one gets the
  date. That sitting is what Home's Coming up, the Calendar (and its automatic Google Calendar sync of Exams, CALENDAR), the
  Exams hub's phase and the bubble-sheet finder's calendar read; nothing else changes. The approval's progress records the
  date the record holds afterwards (`plan.given_on`, and `given_on_kept` when an earlier date was kept instead of the typed
  one); the draft's page says "Given on <date>" once approved, or that the record already had another date, which was kept,
  with a link to change it on its Exams page. FROM THE SCHEDULE (2026-10-10, SCHEDULING "CLASS SCHEDULE: PRE-FILL"): when the
  term's class schedule has planned rows no record follows yet, the dialog offers them above Given on, preselected by the
  title; choosing one fills an empty date and sends `planned_item` (absent otherwise, so other approvals read and checksum as
  before). It must be an open row of the term at approval; the worker links it to the new assignment after the exam record is
  made (`_follow_planned`, `plan.planned_linked`), and a link that cannot be made never fails the approval.
- NO VERSION ON THE PAGE (requirement, 2026-10-01: "I don't want the assignments to have this quiz#/version/copy caption on them. I
  also want any assignments which get stamped with data matrices to have [no] version mentioned anywhere on it (I don't want the
  students to be able to tell which version they had to tip of their friends)"). A drafted quiz prints no version: `facultyquiz.sty`
  v3's header is the title, the sections line and the points, its footer "Page 1 of 2"; its print jobs have no caption; and a printed
  field that names a version ("Version A", `quiz_latex.unversioned`: statements, choices, parts, figures, scaffolds, directions, tags,
  the sections line) is refused and goes back to the writer (`quiz_write` v4 says so). The stamps alone say which version a paper is.
  THE CHECK IS TOLD SO TOO (requirement, 2026-10-11, shown the notes word for word: "add the sentence"; `quiz_check` v4, v3 and
  one sentence): read by a real model, the check remarked in its notes each time that the pages print no version letter although
  the past quiz it imitates prints "Version A". It is now told that this is deliberate and never to be reported or remarked on.
  Nothing else it looks for changes: it still compares the versions (the same problems, comparable difficulty, no shared answers,
  the correct option's letter). A new prompt is part of what a check saw (`printed_keys`), so the first revision of any draft
  after the change checks every problem once more.
  Label-maker labels are not the page: they keep the version and everything else they printed for Exam 1 (instructor, the same day:
  "single labels that I print on a label maker should have everything they did with exam 1, those were very helpful").
- REBUILDING AN APPROVED QUIZ (requirement, 2026-10-01: "rebuild Quiz 4 with the corner codes and no version"). POST
  `/api/quiz-drafts/<draft>/approvals/<approval>/rebuild` (`confirmation: "rebuild"`, checked on the server; `rebuild_quiz_approval`)
  queues one retained `quiz_draft_rebuilds` record (grading migration 039, owned by the approval, one at a time, naming who asked);
  the cloud worker carries it out step by step (`run_quiz_rebuild`), every step reusing what an earlier attempt made. It compiles every
  version again from the APPROVED quiz with Faculty's current class and style (a layout fault, or PDFs identical to the current ones,
  stops it), registers each as a new printed version of the same assignment labelled with its letter and printing ("A2"; labels are
  unique and the earlier versions stay), takes the plan's questions on the earlier versions off the plan (their pins only, recorded in
  a `quiz_rebuild_questions_withdrawn` event; questions, revisions, rubrics, comments and checks stay; their pending answer-area checks
  become `stale`), sets the new versions up exactly as an approval does (`_set_up_quiz_plan`: questions with the drafted rubrics and
  clarifications, templates and the answer areas where each problem now prints, rubrics confirmed, the rubric review requested, one
  term print job per version with the approval's copies and sections), supersedes the earlier print jobs, creates the new questions with the keys the approval saved (`save_keys`), and asks again for
  an answer-key reading the instructor had asked for on the earlier questions, except for a new question that holds a key (a
  reading refuses a question with a key). A replaced version takes no template slot and no question
  again (`rubric_upload_documents`, `create_draft_question`: GRADING). A rebuild carries the approved revision's rubric check to the new questions where
  their content and reference digests equal the recorded ones (the PDFs are new and not compared), under
  `quiz-rebuild-review:<rebuild>`, and NEVER the confirmation: a rebuilt quiz waits for one tick in Key & Rubric Finalization.
  The events an approval-time confirmation wrote (`rubric_quality_confirmed`, `rubric_approved`) are the approval's own only when
  they name the confirmation in the approval's result (`finalization.review_id`); the same kinds for any other confirmation still
  refuse the rebuild. REFUSED once anything depends on the earlier setup: a copy back
  in a scan, a response, a certification, a Gradescope assignment or link, a rubric review or answer-key reading still running, or any
  grading event of the instructor's own (`REBUILD_AUTOMATIC` lists the automatic ones). The draft's page says how the latest rebuild
  went, and the approval's stack and links follow the rebuild (`_quiz_current`). The assistant's route policy does not name the route,
  so the assistant needs an Approve card for it.
- A MAKEUP VERSION of a given assignment (2026-10-03) is drafted as a quiz draft whose request names the grading plan; its rules are
  GRADING "MAKEUP VERSIONS". The quiz drafts list leaves makeup drafts out, and a makeup approval is never rebuilt.
- A PAPER'S STAMPS DECIDE ITS VERSION where its printed document carries the questions of exactly one version letter of the plan and
  that letter's questions all sit on it (`ExamVersionsMixin.printed_document_version`, checked right after an instructor's own decision).
  One label sheet serving several versions (Exam 1, Quizzes 2 and 3) is unchanged: the seating chart's rule still decides there.
- NAMES ON UNNAMED STAMPED COPIES (`document_identity/copy_names.py`, document_identity migration 011, the worker rotation): page 1 of a
  copy a quiz approval printed for no student (request ID `quiz:`), or of any job printed for the whole term with no section (TERM
  PRINT JOBS; other unnamed copies, such as an exam's extras, keep Change assignment), in a present scan, is read once per rule with
  the local OCR (the first-page band, several pages in one model load) and kept (`copy_name_reads`, immutable). On a copy stamped
  with the two corner codes (DOCUMENTS "TWO CORNER CODES BY DEFAULT") both stamp boxes, 2 mm to spare, are whited out first
  (`recognize_covers` `blank_corners`, `prepare.stamp_corners`; the reading records `stamp_corners_blanked`), since the top band
  holds a code either way up. It is matched against
  the copy's OWN section roster, or for a term job the rosters of every section the job lists, by the bubble sheets' rule at
  the instructor's threshold (each match remembered per reading and roster, `UnlabeledService._current_match`: the view is drawn
  on every worker turn, and matching every read copy again took 7 s a turn at 106 copies of a 582-page scan, 2026-10-31), and a
  confident match names the copy through the ordinary `assign_copy` (actor `automatic`, the event
  naming the reading): the rule of 2026-09-24 for first pages without labels, extended to stamped paper printed for no student (the
  instructor's answer above). Never for a student who already has a copy of the assignment in the term, nor for two papers read as one
  student; those, unreadable names and anything below the threshold wait on the scan's Students page, where the instructor names them
  with the same `assign_copy`. The scan's overview says so (`copy_names` steps, Students stage); a paper without its student holds back
  only itself. JEV (requirement, 2026-10-15: "yes" to Jev here too; DOCUMENTS "JEV PAPERS"): when Jev is set up, each first page read
  is also shown to Jev, which reads whose paper it is against the copy's own pool (dropped students left out), kept in the reading.
  Where the OCR's match is not confident, Jev's pick names the copy under the same holds (basis text "Jev's reading of the
  handwritten name and UIN"); where the two name different students the copy waits with a note, and Jev's reason shows on a copy it
  could not settle. Jev failing or not set up leaves the OCR reading as it was. Since 2026-11-12 the match itself is Gemini
  Decisions' (DOCUMENTS "JEV PAPERS" > GEMINI MATCHES THE STUDENT): the same pool, holds and rule, and the basis text and notes
  name Gemini for a match it gave ("Gemini's reading of the handwritten name and UIN").
  SIDE BY SIDE (requirement, 2026-11-14: "Yes, do it here", after two scans of that morning spent 20 s of every 54 s worker turn
  asking 32 name requests one after another, ~0.6 s each): the Jev and Gemini requests of different pages read in one turn are
  asked at most `PARALLEL` (4) pages at a time; one page's own requests (its page check, then its match) stay in order. The
  rosters are read first, in the turn's own thread; every request keeps its own receipt, and a page that fails leaves its OCR
  reading as before. Nothing else in the turn changes: the readings are kept together after every page has been asked.
  AFTER THE INSPECTION (requirement, 2026-11-14: "Yes, do it here", asked whether the students could be found all at once at the
  end; measured on that morning's 28 turns: each name turn cost 7.7 s to start the reader and 0.9 s a page, and 281 first pages
  took 29 turns, 3.7 min of starting alone): no first page of a scan whose inspection is pending or running is read
  (`_waiting`), so a scan's names are read once its inspection ends, up to `BATCH` (48) pages a model load. A scan whose
  inspection stopped is read as before, and pages of another scan that is done are not held by one still being inspected.
  Nothing downstream waited on the names meanwhile: a scan's assignment, grouping and grading already wait for a complete
  inspection.
- PHOTOCOPIED STAMPS (requirement, 2026-10-16: "yes" to Faculty handling a version's one printed copy photocopied for the class;
  `document_identity/photocopies.py`, the worker rotation before `copy_names`). A copy of a job NAMES ON UNNAMED STAMPED COPIES reads
  whose page 1 is on pages of different content in present scans is photocopied: its stamp is evidence of the version and the page
  only. The name reader names no student for it (its papers show as conflicts, "This copy's stamps are on more than one paper").
  Once a scan is fully inspected its papers of such a copy are found in scan order (a page 1 starts a paper; that copy's later page
  numbers join it, blank backs and unreadable pages skipped), the first paper of a copy no other scan shows stays on it, and every
  other paper moves page for page to the first copy of the same job that nobody is named on, no scan has shown and no review holds
  (page identity `manual`, the intake rule's review state, an Inbox event `photocopied_papers` naming each page's stamp before and
  after; the decoded codes are kept as read). Never a page in a reviewed assembly; a paper with no unused copy left waits for the
  instructor (the event says so). A name only the reader gave the source copy is cleared (actor `automatic`), and the readings
  already kept then name every copy under the holds above. Tests: `tests/test_quiz_drafts.py` (photocopied).
  A LATER PAGE OF A MOVED PAPER (requirement, 2026-11-06, Quiz 7, where a photocopy's page 1 code read and its page 2 code only after
  DOCUMENTS "FAINT MARKS"): a page of a copy whose papers this scan moved, which no paper of that copy takes up, joins the paper just
  before it in scan order when that paper's page 1 was moved off the same copy and lacks the page's number, and moves to that paper's
  copy (the move recorded `joined`; SHARED STAMPS counts that paper once). A copy whose paper the scan already assembled is kept by
  that paper, so no photocopy stays on it. Tests: `tests/test_quiz_drafts.py` (faint codes read again).
- SHARED STAMPS (requirement, 2026-11-04, of a take-home: "they need to be individually stamped, not associated with any particular
  student, but I need to know if they let their classmates copy the assignment because that is not allowed"). The approval dialog's
  box "Each student must use their own copy: report any stamp that comes back twice", unticked by default, sends `own_copies`, kept in the
  approval's input (owner: the approval, so the assignment in its term; absent, an approval reads and checksums as before). For that
  assignment PHOTOCOPIED STAMPS still moves each paper to its own copy and the papers go on being graded: a flag, never a hold.
  `photocopies.shared_stamps` reads the moves back (`photocopied_papers` events of scans still PRESENT, each paper as it is identified
  now): each copy whose stamp came back on more than one paper, naming each paper's student. The assignment's `shared_stamps`, present only
  when the box was ticked, draws "Shared stamps" on its Overview and Collect; Home's card "Stamps back on two papers" (`photocopies.attention`)
  counts them over current terms until the assignment is certified; the list stays. WHAT IT CANNOT DO, said on the page: it only sees a stamp
  come back twice. A photocopy handed in alone reads like the original, it cannot say who copied from whom, it looks at page 1 (the
  move's rule), and a paper scanned twice or a print job printed twice shows up too. Makeups do not offer the box.
- A COPY BY EMAIL (requirement, 2026-11-08: "I want the agent to be able to attach assignments it has access to to emails, for
  instance there is a followup about the take home assignment that was generated recently and the followup/agent should be able
  to attach it to the email reply it sends"; the take-home's approval had ticked SHARED STAMPS' box). An assignment reaches one
  student by email only as their own copy: `POST /api/documents/versions/<version_id>/student-copy` (`enrollment_id`, request ID;
  `DocumentService.student_copy`) makes a print job of one copy named for them, owned by their term and listing their current
  sections, marked by a `student_copy` document event and rendered at once (`build_now`: claimed here when the document worker
  is free, else waited for, up to a minute). It answers the PDF's route (`/api/documents/batches/<id>/download`) and a name of
  the assignment's title alone (never the version). A copy is handed out once: asked again, Faculty gives the student's standing
  copy of that version while it was never sent to a printer or by Export by email (a failed attempt aside), else makes a new one;
  once Mail has recorded a send attaching it (uncertain ones included), Print at Home and Export by email refuse it
  (`EMAILED_COPY`). Its stamp names the student, so a paper returned on it is theirs, and a classmate's photocopy of it comes back
  as a shared stamp. The Assistant makes one at once (`auto`, category "A student's own copy"); in a follow-up it is DONE FOR
  YOU record-keeping (ASSISTANT), up to three per preparation, for the follow-up's own student and an assignment of their
  course, listed "Made their own copy of …" with no Undo (it reaches the student only with a reply the instructor sends). Mail a
  follow-up proposes to the student attaches an assignment only as such a copy of theirs: a blank (`version_preview`), the
  class's job or a classmate's copy is refused, naming the route to use, and so is a copy of work still to be given to them (their
  section's day, EXAMS "DATE GIVEN", or a sitting of their own, today or later). The email stays an Approve card.
  Tests: `tests/test_printer_send.py` (`StudentCopyTests`), `tests/test_followup_suggestions.py` (their own copy).

- ASSESSMENT BLUEPRINTS (2026-10-03, TEACHING-WORKFLOWS). Optional ordered question slots in the new-draft form pin objective revisions, kinds, points and selected bank content revisions in the course-wide draft’s immutable input. The writer and compile tool reject violations, as does approval; every version keeps fixed questions exactly. The result shows structural coverage, while the instructor checks pedagogical alignment. Unconstrained drafts keep their existing input and response shape.

- MULTIPLE CHOICE IN GRADING (2026-10-08, GRADING MULTIPLE-CHOICE READER). A drafted multiple-choice problem is drafted with the
  two-line rubric that rule reads and prints its lettered options, so its grading question is multiple choice by itself (AUTOMATIC
  CONFIRMATION, the instructor's choice of 2026-10-08): with the reader on, a Cloud batch grades it from the marked option, unless the
  instructor set it to written work at Key & Rubric Finalization.
- ACCESSIBLE COPY (requirement, 2026-10-27: "If I have DR quiz takers, I must have an accessible version of the quiz uploaded to the
  AIMs portal ... it just needs to be screen readable. I think WCAG 2.1 compliant"; Word chosen for them, since screen readers read
  Word equations reliably and the 2023 TeX Live cannot tag math in a PDF). `document_identity/accessible_quiz.py` makes one version of
  an APPROVED drafted quiz (an approval whose request rule is `quiz-draft-v1`; a makeup's is left out) into a .docx from the same
  structured quiz its PDF was typeset from (`quiz_latex.clean`), never from the PDF: Faculty writes a plain LaTeX body (the sections
  line and total, the directions, a heading where the kind of problem changes, one heading per problem with its tag and points, the
  statement, table, lettered choices or parts and answer scaffold) and pandoc (`FACULTY_PANDOC`, default the 3.6.4 binary under
  `/mnt/nvme/Teaching/tools/pandoc`, run with `--sandbox` through stdin and stdout, so it reads and writes no file) makes the .docx:
  titled with the quiz's title line, language en-US, every equation a Word equation. A correct choice, solution, final answer or
  version is never in it, nor in its file name (`<title>-accessible.docx`; NO VERSION ON THE PAGE). Layout-only commands (spacing,
  alignment, the table stretch) are dropped; a tabular's column types become plain columns; a displayed array (a simplex tableau)
  becomes a table, one equation per cell, its first row the header when every cell of it names something; an answer blank
  (`\underline{\hspace{..}}`) reads "[blank]", inside the equation as its right side where an equation is left open for it ("$x=$"
  and a blank; requirement, 2026-10-27, "what happened here" of Word's "(x, y) = ¿"). ONE PROBLEM PER PAGE (requirement, 2026-10-31,
  of Quiz 6's copy having "no room for them to do the free response": "I think the better solution is for the DR quizzes just have 1
  problem per page"): each problem after the first starts a new page (a page break run, before its section heading where the kind
  of problem changes), so the rest of its page is the room to work. NO COPY rather than one a screen reader cannot read: a version with a figure (no alt
  text is written for one yet), LaTeX pandoc keeps raw, math it cannot make an equation of, or text it cannot parse, each with the
  reason. Nothing is stored: the copy is made again on each request. `GET /api/quiz-drafts/<draft>/approvals/<approval>/accessible/<letter>`
  (`quiz_drafts.accessible_copy`) downloads it (409 with the reason, 404 for no approval or version), and
  `GET /api/quiz-drafts/accessible-copies?term_id=` (`quiz_drafts.accessible_copies`) lists, per assignment of the term's approved
  quizzes, the first version that has a copy (else the first version and its reasons); what an approved revision's version gave
  is kept in a bounded process cache (PERFORMANCE: an approved revision never changes). The Accommodations page's DR quizzes tab
  reads the latter beside each term's DR quizzes and shows an Accessible copy column (a "Word file" link, "Not available" with
  the reason, "Not drafted in Faculty", or "No quiz yet"); the DR quizzes read itself is unchanged. The instructor's Send to AIM
  uploads it to the students' requests in AIM (SCHEDULING "SEND TO AIM"), which an approval made on the draft's page opens by
  itself when the term's DR quiz takers have the quiz (SCHEDULING "SEND TO AIM" › AFTER APPROVAL).
  GRADED ON ITS OWN PAGES (requirement, 2026-11-10): the DR center's print of this copy comes back with one problem per page; it
  is registered under the copy's own document and graded there with its version's rubrics (GRADING "DR COPIES").
  PAGE CODES (requirement, 2026-11-10: "start adding the data matrix stamps to the accessible versions that get uploaded to AIM
  ... they shouldn't be student specific"): the file `quiz_accessible_copy` hands out prints each page's own Data Matrix at two
  corners, decorative pictures a screen reader passes over, the same for every student (GRADING "DR COPIES" > STAMPS). The first
  request of a version registers that copy's document and its job of unnamed copies, so this GET writes once; the listing
  (`quiz_accessible_copies`) and the file's name are unchanged, and a file whose codes cannot be made goes out without them.
