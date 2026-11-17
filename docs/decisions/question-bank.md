## Question bank

Rule ID: **QUESTION-BANK**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-10-01): "I want to have a question bank that retains questions for each course and ties them to things
  like sections/chapters/topics and other granularity levels". Asked, they chose: questions come from quizzes Faculty drafted and
  assignments once they are APPROVED, from past assessments already in Faculty, from questions they type or paste, and from a PDF a
  model reads for them; the outline starts from the lecture notes and stays editable; the other levels are LEARNING OBJECTIVES (below
  topics), DIFFICULTY and QUESTION TYPE; and the model TAGS AUTOMATICALLY (its tags apply at once; the instructor changes them later).
  `question_bank/` (content, service, runs, pictures, api, migrations `question_bank/001_bank`, `002_versions`, `003_pictures`), grading
  migration `037_question_bank_calls`,
  prompts `bank_tag-v1` to `-v3` and `bank_extract-v1`/`-v2`, `static/question-bank.js`, `static/question-bank.css`, `#course/<group>/home/questions`
  (the course Home's course-wide panel links it as "Question bank" with its counts), `tests/test_question_bank.py`,
  `tests/browser_question_bank.py`.
- OWNER: the COURSE DEFINITION (`course_definitions.id`). One bank serves every term and section of the code; nothing is copied into
  a term or a section. Every catalog row is append-only and retained (triggers): outline entries (`question_bank_nodes`, identity,
  level and parent; `question_bank_node_revisions`, number, title, order, retired), questions (`question_bank_questions`, identity and
  where they came from; `question_bank_revisions`, content), classifications (`question_bank_classifications`), retire/restore events,
  PDF uploads (files in the definition's verified storage under `question-bank/uploads/<upload id>/`, checked before a snapshot by
  `DocumentStorage.check_source`) and model runs (`question_bank_runs`, whose status alone changes while it is carried out). A
  question retained from an assessment REFERENCES its grading question (`frq_question_id`); its content is the bank's own revision, so
  an edit in the bank never changes what was graded, and an edit in grading never overwrites the instructor's edit in the bank.
- THE OUTLINE is a tree: chapter → section → topic → learning objective (a section may stand without a chapter; an objective sits
  under a section or a topic; enforced by trigger). The course's CURRENT lecture notes edition (COURSE-NOTES) is read into it once per
  edition (`question_bank_outline_imports`): each numbered section, the chapter printed above it, and its topics (the notes' bookmarks).
  An entry the notes made follows a later edition's title and number; one the instructor renamed, renumbered or retired keeps their
  revision; nothing is removed when an edition leaves an entry out. Entries are matched by `chapter:<n>`, `section:<n>` and
  `topic:<section>:<title>`. The instructor adds, renames and retires entries; a retired entry (and what is under it) is no longer
  offered for tags, and questions keep the tags they have. Entries never move to another parent: add one where it belongs instead.
- RETAINED QUESTIONS, automatically (requirement, 2026-10-01: "assignments that are graded should have their questions banked as well.
  unless they were already banked earlier/from being generated"): every graded grading question — one of whose rubrics the instructor
  ever approved in Key & Rubric Finalization, which grading requires (`frq_rubric_approvals`; while its rubric is being revised, its
  latest pinned revision) — any assignment, exam, quiz or homework — and every question of a
  drafted quiz whose approval was carried out (`quiz_draft_approvals` complete), the latter from the LaTeX the instructor approved
  (statement, parts with points, choices and the correct one, TikZ figure, final answers, solution; the quiz's printed section label
  travels to the tagger as a hint). An assessment's question is taken from its approved grading revision: its statement with the
  printed number, points marker and page footers set aside (`content.tidy_statement`), the answer key as final answers, the worked
  solution. A later APPROVED revision is followed while the bank's copy is unedited.
  A REBUILT QUIZ IS THE SAME QUESTIONS (requirement, 2026-10-02: Quiz 4's rebuild had been banked a second time as an assessment
  once its rubrics were approved, its multiple-choice options lost). A carried-out rebuild (QUIZZES "REBUILDING AN APPROVED QUIZ")
  prints the approval's problems again on new grading questions; each is an alias of the approval's question with the same problem
  and version (`_quiz_aliases`): never banked again, its certified grades count for the approval's copy (`statistics`) and the terms
  it was used in show on it (`_sources`). Duplicates banked before this are retired through Retire (history kept), not deleted.
  GRADED ON BUBBLE SHEETS: once a version's sheets were graded (the plan's latest answer key, and scored sheets in its item analysis;
  a version not graded yet is looked at again at most every 10 minutes), the bank reads that version's blank ONCE from the plan's
  confirmed grading template (its original PDF, else the normalised one, within the reader's limit; an extract run whose input names
  `bubbles`), and in the reading's completing transaction adds each KEYED problem it lacks: linked to its assignment, version and printed
  number, its correct choice from the key (A is the first choice; a thrown-out question, every answer accepted, gets a note). A problem
  already banked (retained from grading, or linked by the instructor, e.g. read from a PDF) is skipped; one the reading did not find is
  counted on the page (`bubbles_unread`) and waits for the instructor's own reading. Uploads are named by course and checksum. Approved questions without statement text (a
  rubric uploaded without its problem) wait and are counted on the page. Versions of one problem (same assignment and printed number)
  carry a `family` (assignment and printed number) used only to link a DRAFTED QUIZ's versions (below). Multiple-choice questions graded only
  on bubble sheets have no statement in Faculty: read them in from the blank exam's PDF.
- QUESTIONS THE INSTRUCTOR WRITES are saved at once (`content.clean` validates kind, number, statement, parts, choices, correct choice,
  figure, points, final answers, solution, notes). Tags given with them are the instructor's; without tags the model tags them.
- PDFs (`bank_extract`, rule `bank-extract-v1`): a PDF only (Word is told to Save As PDF), up to the document sandbox's own limit (`template_conversion.MAX_INPUT`, 25 MiB) and 100 pages, refused at upload; the same file in
  the same course is one upload. The document sandbox reads its pages (text and a picture of each, kept beside the upload), then six
  pages per request, with the next page's text; the model transcribes every problem that STARTS on those pages and never solves one
  or invents an answer (only what the PDF prints). On a Claude or ChatGPT subscription connection it may also open any other page of the
  PDF, or a region of one enlarged, as it needs it (`pdf_page`, from the kept page pictures and text; grading.md "DRAFTING TOOLS"). Nothing enters the bank until the instructor reviews what was found and adds the
  questions they choose (`accept`, each once, keyed by run and index). Found in the first WebAssign reading (2026-10-01, 24 PDFs): a
  "select all that apply" problem keys several choices, which a bank question cannot name, and was left out; it is now kept with its key
  as its final answer ("D, E") and a note. The reading's LaTeX is tidied for the page (`content.tidy_latex`): a "$(x, y) = $" blank, whose
  closing dollar follows a space, would show its dollar signs, so single dollar signs are paired in order and each body trimmed, and a
  thousands separator inside mathematics ("$\$8,100$", which KaTeX spaces as "8, 100") is written `{,}`; text outside mathematics and a
  point such as "(0,100)" are left alone.
- READING v2 (requirement, 2026-10-03: "implement the fixes", after a textbook's exercises, 99 pages, were read with 21 problems lost to an
  80-question cap, every section dropped and the graphs only described; prompt `bank_extract-v2`, rule `bank-extract-v2`). NEVER CUT
  SHORT: a request lists every problem that starts on its pages (at most 200 in one answer); when its answer says it could not
  (`complete` false), lists more, or outgrows its output budget, its pages are read again in two halves (down to one page) instead of
  asking the reader to stop early. THE PRINTED SECTION: each problem carries the section heading printed above it (its number, or a
  heading such as "Chapter 1 Review"; a heading inside a section such as "Mastery Practice" is not one), and a problem before any heading
  on its pages takes the section of the problem before it. It is kept in the question's source (`source.section`), its label ("Section
  2.1"), its family, and the review groups the problems by it (a section's box ticks all of it). When it names an outline entry by
  number (the section, or for "Chapter n …" the chapter), TAGGING places the question under it: Jev is not asked the section (only its
  topic, difficulty and types), a question printed in a chapter is placed by Jev only within that chapter, and the model (`bank_tag-v3`,
  `printed_in`) must name an entry under it or its answer is refused. SHARED INSTRUCTIONS are copied into each problem without the range
  they name ("Write the inequality shown by the graph.", not "For Exercises 7 - 10, ..."). FIGURES: each graph or picture a problem relies
  on comes with its page, a box and a description; the description stays a "Figure: ..." line of its notes (the tagger reads these lines
  as `figures`), and when the problem is added its box, widened by 1.2% of the page and trimmed to what is drawn, is cut from the kept
  page picture as one of its pictures, captioned with the description (a box under 2% of the page is not cut, and the problem says so).
  CHECKS: what to check in one problem (a value read off a figure, a symbol missing in the PDF, a heading that names other numbers) comes
  with that problem: a Check chip and an "only the problems to check" filter in the review, and "Check: ..." lines in its notes once added.
  The file's notes are only about pages as a whole, never that no answers are printed, that instructions were copied or that numbering
  starts again, and a note said twice is listed once. READ AGAIN ("Read again" in the review, `POST /uploads/<id>/read`): a new
  reading of a kept upload with today's reader, while none is under way; the earlier reading and every question added from it stay, and a
  problem added from it (the same page, printed number and place among that number's problems on the page) shows as added and is never
  added again. Up to 2000 problems are added at once.
- ADD ALL (requirement, 2026-10-03: "Can you put an approve all button, I don't want to read through thousands of imported questions unless
  I have a reason to"). A PDF whose reading found questions not in the bank yet offers "Add all" beside "Review and add": one confirmation
  (how many, the sections, the graphs saved as pictures, how many have something to check) adds every one of them as the review would,
  2000 per request. What to check stays in each question's notes ("Check: ..." lines); the bank's Show filter "Something to check" lists
  those questions and their cards carry a Check chip, so nothing needs reading unless there is a reason to.
  ALREADY IN THE BANK (the same day: Exam 1's and Quiz 2 and 3's blanks, read on 2026-10-01, offered Add all for problems the bank has from
  grading). A found problem counts as in the bank, is left out of Add all, cannot be ticked in the review ("In the bank as Quiz 2 ·
  Problem 1 · Version A") and is refused by `accept`, when it was added from this reading or an earlier one of the PDF, or when the PDF is
  an assessment's version and the bank holds that version's problem under the same printed number, or when its text (statement, parts and
  choices, LaTeX spacing and marks set aside, `content.text_key`) is word for word a bank question's from another source; a problem that
  relies on a figure is never matched by its words. The PDF is a version when the questions added from it are printed as one, or when at
  least two of its problems are word for word that version's under the same numbers and none of its other text-only problems is new to the
  bank or printed elsewhere (one in the bank without a version says nothing). Replayed on the live bank: the 55 problems of Exam 1, Quiz 2
  and Quiz 3 already banked were all found, none of a textbook's 626 exercises was; similar text (90%) was rejected because it took 15
  textbook exercises for WebAssign questions with other numbers. Each PDF's row says what was added from it and "all in the bank" or how
  many are not yet (`waiting`), and offers Add all only for those.
- TAGGING (`bank_tag`, rule `bank-tag-v1`), automatic: the outline entries a question belongs under (most specific first, at most 8
  from the model, 12 in all), at most one learning objective (reused, or written once under a section or topic; the same wording under
  the same entry is one objective), one difficulty (easy, medium, hard) and one to three question types (`content.TYPES`: computation,
  word problem, set up, graph, interpretation, concept); the format (multiple choice, free response) is the question's own. Fifteen
  questions per request, versions together. The instructor's tags stand: the automatic pass queues only questions with no tags or
  with the MODEL's tags of an older content revision whose text the model reads changed (`content.tagger_reads`: a revision of only
  the correct choice, notes other than their "Figure:" lines, number or figure source keeps them), and a model result never replaces tags the instructor gave after the run was
  asked for. Asked explicitly ("Ask the model to tag it again", "Tag them again"), the model's tags replace the instructor's.
  JEV FIRST, MODEL ON DOUBT (requirement, 2026-10-03: "bring Jev into the question bank tagging now"; asked, they chose "Jev first,
  model on doubt", Jev also rates difficulty, and the objective always comes from the drafting model; `question_bank/jev_tags.py`,
  prompt `bank_tag-v2`, docs/decisions/jev.md BANK-TAGS). In every tagging run, before the model's request, Jev places each question:
  one request for its section (both option orders), difficulty and types, then one for its topic among that section's topics (a
  section's only topic is decided in code). A question Jev is sure of (section and topic agreed at .8, difficulty confidence .5, at
  least one type at .5) carries `placed` into the model's request: the model gives those values and writes or reuses only its
  objective, which must sit under the placed section (else the answer is refused and asked once more); the applied tags are Jev's
  with the model's objective, the reason says so, and the run's result keeps the Jev receipts. Every other question, and every
  question while Jev is off or failing, is tagged by the model entirely, as before. Measured on 40 live questions: Jev placed 21-23,
  all with the model's section, 20/21 with its topic, 19/21 with its difficulty.
- AUTOMATIC WORK, without a click (PIPELINE): `process_one`, a turn of the Inbox worker's rotation, at most every 15 seconds, reads
  the catalog first and takes the lifecycle lock only when it has something to write: new notes edition → outline; newly approved
  questions → bank; untagged questions → tagging runs (only while a Rubric drafting and review model is chosen). Each content revision
  is tagged automatically ONCE: a failed or stopped run waits for the instructor's retry (nothing failed is retried by itself).
- MODEL RUNS are claimed by the grading cloud dispatcher in the rubric-preparation turn (one per claim, like quiz drafts), recovered
  after a restart, stoppable (`cancel_run`, the request in flight closed, nothing applied), and every request is an ordinary cloud call
  owned by its run (`frq_cloud_calls.bank_run_id`): receipts kept, answers already bought replayed, uncertain outcomes never resent past
  the workspace's limits. The model is the Rubric drafting and review model (no setting of its own), as for drafted quizzes. An answer
  the backend refuses is asked once more with the reason; a second refusal fails the run. Tags of a completed run are applied in the
  same transaction that completes it.
- THE PAGE: the outline (with counts: a question counts under every ancestor of its entries) filters the questions; search,
  difficulty, type (format or question type), source and status (in the bank, not tagged yet, tagged by the model, tagged by you,
  retired) filter in the browser, 40 at a time.
  COMPACT ROWS (requirement, 2026-10-05: "the question bank page is also completely overwhelming and not very user friendly"; asked,
  they come to find questions to reuse, see how they performed and check or fix tags, and chose one compact row per problem). A row:
  its source label (opens it), its version letters, only what needs a look (Check, Not tagged yet, Tagging…, a failed tagging), then
  one quiet line of facts (its sections by number with the full tags as the hint, difficulty, % earned, format, points, Edited); under
  it the statement as two lines of one paragraph (line breaks and display math set inline) and small picture thumbnails. Types, the
  objective and who tagged it are in the question's window. Find, difficulty, earned and order stay out; type, show, source and
  "one row per problem" fold under More filters (open while one is set, and as the instructor left it across redraws). The questions
  a reading asked to check are counted beside the result line ("N questions to check", the needs-you chip), one press from Show →
  Something to check. Adding is not why the bank is opened: Add a question and Read questions from a PDF are secondary actions and
  the page has no primary button. A question opens with its parts, choices, correct choice, final answers, solution,
  figure source, other versions and history; Edit, Change tags, Ask the model to tag it again, Retire/Restore. While the model works
  (or will within a worker turn) the page asks again every 5 seconds and redraws only when the answer changed. Retiring is reversible
  and offers Restore in its toast, so it asks no confirmation.
- ASSISTANT: `question_bank.upload` and `question_bank.add_picture` are `never` (file uploads); every other write is `approve` (the
  default); the PDF download is in `GET_BLOCKED` (a download, fetched as a file); a question's pictures and a PDF's page pictures
  (`question_bank.picture`, `question_bank.page_picture`) are in `GET_BLOCKED` but not `FILE_BLOCKED`, so `fetch_files` shows them when
  the instructor asks. Nothing here sends mail or writes to Canvas, Gradescope or Google.
- VERSIONS (requirement, 2026-10-01: "versions of the same question should be linked somehow"; migration `question_bank/002_versions`).
  Versions of one problem share a group (`question_bank_version_links`, the latest revision per question stands, NULL = in none). The
  same printed number is NOT the same problem: Exam 1's versions are scrambled (A #1 is B #9) and its Version C is mostly other
  problems. A drafted quiz's versions are linked from the quiz (`source`: one problem number, every version). Every other assessment's
  are linked by the model (a tagging run with `purpose` 'versions', prompt `bank_versions-v1`, rule `bank-versions-v1`): one request per
  assessment with all its questions (retained from grading, or linked to it by the instructor), groups of at most one question per
  version, a problem with no counterpart in none. Queued once per question (a failed run waits for the retry); a later question of the
  assessment queues another run that sees them all, and joins the group its counterparts are in. A rule replayed on the live data
  2026-10-01 (wording with numbers set aside) could not separate "same problem, other numbers" from "same template, other problem"
  (Quiz 3 #2's true versions scored 0.51, Exam 1 A21/C19, a profit vs a cost function, 0.60), so the model decides. The instructor
  links (`with`) and unlinks; their links stand, and neither the model nor a quiz changes them. The page shows one row per problem
  with a button per version (the instructor's switch, under More filters, shows every question instead).
- PRINTED AS (`question_bank_assessment_links`, revisions): a question read from a PDF (or written here) can name the assignment,
  version and printed number it is, chosen when its PDF's questions are added ("These pages are printed as") or later; its label then
  reads like a retained question's, and its grades and versions are found by it. A question retained from grading needs none (trigger).
- MEASURED DIFFICULTY (the instructor's choice, 2026-10-01: shown BESIDE the model's, which stays the model's reading of the text):
  `GET .../statistics`, read after the page is drawn and never stored: the share of its points students earned, averaged per student
  over every term that graded it, with the number of students, the terms and the source. Free response: the LATEST CERTIFICATION of each
  term's plan (`frq_certification_students.parts_json`), so a plan not certified yet adds nothing until it is. Bubble sheets: the plan's
  item analysis (`bubble_analysis`, Inbox-deleted scans excluded), matched by printed-as assignment, version and number; an item
  whose key accepts every bubble (a thrown-out question) measures nothing and is left out. The page sorts
  (hardest or easiest first) and filters by it.
- PICTURES (requirement, 2026-10-01: "we may need to add the ability to store accompanying artifacts with questions that have an
  associated graph or picture with it"; migration `question_bank/003_pictures`, `question_bank/pictures.py`). A question keeps the graphs,
  diagrams and pictures it relies on (`question_bank_pictures`): an image the instructor uploads (PNG, JPEG, GIF or WebP up to 10 MB and
  40 million pixels, opened to check it, kept as sent) or a box cut from a page of a PDF read for the SAME course's bank (from the page
  picture kept beside the upload, saved as PNG; the upload, page and region as shares of the page are recorded). Each file lives in the
  definition's verified storage under `question-bank/pictures/<picture id>/` (checked before a snapshot), written before the row that
  names it; the ID derives from the question and the request, so a retry finishes its own folder. A picture serves every revision of its
  question and never changes its content: a retained question keeps following its source, and the tagger's reading is unchanged. A
  caption (alt text, at most 1000 characters) is set when the picture is added; the reading's "Figure: ..." note is offered as the first
  caption. Removing is an event (`question_bank_picture_events`), reversible from the question's dialog (Restore under Pictures; a modal
  dialog keeps a toast's button out of reach), and listed in its history. Pictures and page pictures are served `no-store`. A standing picture of a question in the bank is embedded on this machine, with its caption and question as words, so it can be found by describing it (PICTURE-SEARCH); a PDF's page pictures are not. Cards show
  the standing pictures as thumbnails; the question's dialog shows them with "Cut from a PDF page…" (drag a box over the page; any PDF
  of the course whose pages were read, the question's own page first) and "Add a picture…".
