Faculty reads the Plaud intake's archive and asks Plaud nothing, 2026-11-15 (RECORDINGS "Plaud" › FACULTY ASKS PLAUD NOTHING, THE COMMAND, THE FILES IT READS, WHAT A ROUND ASKS, WHAT THE ROUND SAYS OF ITSELF, THE AUDIO IS THE ARCHIVE'S, and "KEPT ONLY ONCE FILED", "ASK AGAIN", "STORAGE"; STORAGE "recording bundle"; AGENTS OUTSIDE SERVICES; `docs/proposals/plaud-intake-plan.md` step 4; the instructor, of the whole design: "the brain should have everything and faculty just has the teaching relevant ones, but the brain has it as well"; of audio: "audio should be stored somewhere, just not needed in the app itself"; asked whether Faculty keeps its own copy of teaching audio as it does now: "Play from archive"; and for this step: "build step 4"): `recordings/intake.py` takes the place of `recordings/plaud.py` (deleted) and `tests/fake_intake.py` the place of `tests/fake_plaud.py` (deleted): a real archive folder in the test's own directory and an executable stand-in for the intake's command that answers `list`, `show`, `audio`, `status` and `check-now` in the real shapes and exit statuses and records every command run. `tests/test_recordings.py` (71 tests, `CaptureTests` run again as `StorageTests` on the verified storage engine; `IntakeTests` replaces `SignInTests` and `ReadOnlyTests`, which only made sense for Plaud's HTTP interface): only the five commands are ever run, exactly `status`, `list`, `show` for an ordinary round and `status`, `check-now`, `status`, `list` for one the instructor asked for (Check now, or filing by hand), never `round`, `copy` or `renew`, and never with a FACULTY_* variable in the command's environment; nothing under `recordings/` imports `urllib`, `http`, `socket` or `ssl`, `plaud.py` is gone and the command is run without a shell; a missing command, an unset one, one that answers garbage, one that hangs (given up after its time) and an archive that is not ready each make the round wait and say why, nothing is listed from an archive that is not ready and what Faculty kept stays; the sign-in to be made again is what the intake's `status` says (capture status `sign_in`, detail beginning "Sign in to Plaud again" with the intake's words, the attention count, `check-now` not asked) and the round still files and copies what the archive holds and is not counted a success; a listing of Plaud 29 minutes old is fine, 31 minutes old is `waiting` with the sentence naming the time and `systemctl --user status plaud-intake.timer`; a Check now the intake could not make is said and the round goes on; a recording the intake has not archived yet appears after `check-now` only; a path outside the archive, a wrong checksum and a symlink are each refused, said in the round's detail and the recording's, and nothing of the recording is kept; the file reader alone (relative path, `..`, a directory, a short or wrong sha256, a size limit, no archive named); nothing that waits or is not kept is ever `show`n or asked for its audio; both drop-ins name the intake's command and neither the old token file nor the old address appears anywhere in `recordings/`, the recordings tests or the drop-ins. In `CaptureTests`: a new recording's bundle holds the transcript, outline, marks and notes byte for byte as archived and NO AUDIO (no audio row, no audio file in the bundle folder), is "kept" while the archive holds its audio, and a later round asks only `status` and `list`; Plaud's notes rebuilt from the archive's form hash exactly as the notes already kept, are not kept again when unchanged and are kept as a second file when they change; the audio route serves the archive's file with ranges, checks it against the intake's checksum on every read (a file changed after the first read is refused with a sentence that says so and no contents), serves Faculty's own older file when the archive cannot give it (and prefers the archive when it can), and is 404 after Don't keep with the archive untouched; a recording with no audio in the archive says so, waits, and completes without copying anything when the archive has it; one the archive holds without a transcript or audio says which it lacks; a file Faculty cannot write here is storage not ready and never the intake's fault; a recording gone at Plaud but held by the archive is read and kept like any other and, asked about again, can be kept, where one the archive does not list stays not kept; and the reread schedule, the backlog, the windows, filing, Keep and Don't keep, erasure (rows, bundles, drives away, the nightly backup), Ask me again and the append-only tables are proved as before. With them `test_recordings_page` (17), and `test_recordings_reading`, `test_recordings_memos`, `test_recordings_classes` and `test_recordings_student_meetings` (88 together), moved onto the stand-in with their assertions kept and the audio no longer in a kept list; the six browser scripts (`recordings`, `recording_page`, `recording_reading`, `recording_student_meeting`, `recording_class`, `recording_memo`); and `tests/test_recordings.mjs` (the erase sentence, "Transcript kept" for a new recording and "Transcript and audio kept" for one whose own audio Faculty holds). BEHAVIOURS THE DESIGN CHANGED, and the tests that state them: a new recording's kept list has no `audio`; a recording the archive does not know is what a recording Plaud no longer had was; one Plaud no longer has but the archive holds is listed and can be asked about (the old test that it could not be is replaced); "Plaud could not be read" on the page now reads "Recordings are waiting"; a transcript that a round reads is the archive's newest, the earlier one stays in the archive. Run on isolated configuration and NVMe scratch with `/mnt/nvme/Teaching/tmp/recordings-keep/run_tests.py`, `node --test` on the recording `.mjs` files, `tsc --project tsconfig.frontend.json`, `manage_checks.py browser` for the six scripts, and `manage_checks.py quick`: all passed: the six browser scripts, the recording `.mjs` files (35 tests), the typecheck (clean), and `manage_checks.py quick` (JavaScript syntax, frontend unit tests, and the quick API and recovery tests, 681 seconds; its own typecheck step was skipped for want of a local compiler, so the typecheck was run by hand).
NOT CHECKED: the real intake command, the real archive and the real Plaud (a check never reaches them); the live restart onto the new drop-ins, which the instructor chooses (and `systemctl --user daemon-reload` for them); a real round, a real Check now (the intake's `check-now` takes up to twenty minutes to answer, and the stand-in answers at once), and a real recording's audio played from the archive in the instructor's browser; the full suite (67 minutes) was not run; the audio Faculty already holds was not removed, moved or rewritten and no tool for that was written.

The Plaud sign-in is the intake's, 2026-11-15 (RECORDINGS "Plaud" › READ ONLY, WITH NO EXCEPTION, THE SIGN-IN IS THE INTAKE'S and A SIGN-IN PLAUD REFUSES, OR ONE THAT HAS RUN OUT; AGENTS OUTSIDE SERVICES; `docs/proposals/plaud-intake-plan.md` step 3; the instructor, of one centralized Plaud handler: "Yes, plan it", then, asked when the sign-in should move to it: "Now"): Faculty no longer renews. `recordings/plaud.py` holds no POST, no write and no lock (`TokenFile` only reads; `renew`, `_due` and the renewal's address are gone), and both drop-ins name the intake's file. `tests/test_recordings.py`: `SignInTests` (7 tests, in place of `RenewalTests`: with a token a renewer would renew now, Plaud is only read, with the token the file holds, and the file and its folder are byte for byte as they were; a token Plaud refuses is sent once, the file read again, and capture stops with "Sign in to Plaud again" and asks nothing until the file changes; a token with under a minute left is never sent; the intake renewing between two reads is followed; a 401 to a token the intake has just written waits and is not the sign-in ending; a file others can read is not used; a missing file waits and is looked for again) and `ReadOnlyTests` (the module sends GET alone, in two places, and does not hold the renewal's address); with `test_recordings_page`, `test_recordings_reading`, `test_recordings_memos`, `test_recordings_classes` and `test_recordings_student_meetings` (170 tests) and `manage_checks.py quick` (3 of 3 groups, 565 Python tests; the frontend typecheck is skipped in a worktree and no frontend file changed), through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. The renewal itself is checked in the intake's own repository (`~/code/plaud-intake`, 74 tests against its own fake Plaud: both tokens replaced, a refused renewal never sent again until the file changes, any other answer tried again with the file untouched, three renewers at once making one request). No migration, no route, no frontend change.
NOT CHECKED WHEN THIS WAS WRITTEN: the full suite and the browser scripts were not run. The live cutover (the sign-in file moved to the intake, the intake's first real renewal, Faculty's first round on the intake's file) comes after this change lands and is not recorded here.

What the coordinator's mail calls for, and Word and Excel files read as their words, 2026-11-15 (ASSISTANT "A coordinator's follow-up" › WHAT THEIR MAIL CALLS FOR and WORD AND EXCEL FILES; the instructor: "look at my gmail and see previous emails from her and make sure the assistant has the ability to take actions on her emails since they are different than student emails"; the term's kept mail from the coordinator was read first, in Faculty's own record: 122 messages of theirs, 76 attachments of which 35 are Word files): `tests/test_assistant_files.py` (`test_a_word_or_excel_file_is_read_as_its_words_in_the_document_sandbox`, new, through the real sandbox on synthetic files: a .docx's paragraphs and table rows, an .xlsx's sheet as saved with its columns kept apart, a file served only as "a file" known by its name, another zip and a broken file not read, a shown file not read); `tests/test_coordinator_mail.py` (`test_the_assistant_reads_the_word_file_the_coordinator_attached`, new: a Word file kept with a coordinator's captured message is read by `fetch_files`); `tests/test_coordinator_followups.py` (two new: the instructions for what a coordinator usually sends are in the preparation's request; an answer about a student is proposed on that student's own follow-up as a card that says it is a change the coordinator's message calls for, is approved, and mail to the student stays refused). Run in a worktree on isolated configuration and NVMe scratch: `test_assistant_files`, `test_coordinator_followups`, `test_coordinator_mail`, `test_coordinators`, `test_followup_suggestions`, `test_assistant`, `test_course_notes` and `test_followup_redo` (222 tests) passed. The reader was also run by hand, in the sandbox, on two of the coordinator's real agendas and their one spreadsheet (read whole: 1,741, 1,478 and 12,680 characters); nothing of them was kept.
NOT CHECKED: the full suite and the browser scripts were not run (no page changed). No model has read the new instructions: what a real preparation does with a real agenda or ruling is still seen on the first live ones.

One coordinator's name, 2026-11-15 (COORDINATOR "ONE NAME"; RECORDINGS "The reading" › FACTS and the term's COORDINATOR; the instructor: "join the recordings coordinator name with the course card's"): `tests/test_recordings_reading.py` (`test_the_course_cards_coordinator_is_the_one_a_reading_is_told`, new: with a name kept in Recordings and a coordinator on the course card, a reading is told the card's and the recording's page answers it with `card` true; the Recordings name route answers 409 for that term; unticked, the name kept in Recordings stands again as it was recorded; `test_a_terms_coordinator_is_a_name_a_later_reading_is_told` unchanged for a term whose card names none). Run in a worktree on isolated configuration and NVMe scratch: `test_recordings_reading`, `test_recordings_page`, `test_recordings_student_meetings`, `test_recordings_memos`, `test_recordings_classes` and `test_coordinators` (113 tests) passed; JavaScript syntax and the frontend unit tests passed; the browser scripts recording reading, recording memo and coordinator passed. NOT CHECKED: the full suite and the other browser scripts; the window that says the name is the course card's was not opened in a browser (its script names a coordinator in Recordings on a course whose card names none).

A coordinated course and its coordinator's follow-ups, 2026-11-14 (COORDINATOR, binding from this change; COMMUNICATIONS "THE COORDINATOR'S MAIL"; TEACHING-WORKFLOWS "A COORDINATOR'S FOLLOW-UP"; ASSISTANT "A coordinator's follow-up"; JEV "NOT THE COORDINATOR'S"; the instructor: "I want to add an optional setting when a course is made, like a checkbox in the course creation card, that indicates if it is a coordinated course or not. The checkbox will have greyed out fields for the Name and email of the coordinator unless I check the box. Then, I want the coordinators emails to be included in the followup pipeline just like student's", "the agent knows it isn't a student but the coordinator"): `tests/test_coordinators.py`, new (8 tests: the term owns the answer as revisions, every offering of a term reads it, a ticked box needs a name and an address and a refused card saves nothing, the course records and the state read unchanged, when an address began); `tests/test_coordinator_mail.py`, new (9 tests, against the communications tests' fake Gmail: nothing kept until the course is coordinated, the coordinator's mail kept and linked to the term under its owner key with `coordinator_address`, a student's timeline never showing it, mail kept earlier linked without a read, the link rules of migration 003; and as follow-ups: mail from before the card named them is background, what comes after is listed, the instructor's reply answers it, tracking under the term, reopening, appointments and joins refused, a course unticked keeping what was tracked with no address); `tests/test_coordinator_followups.py`, new (8 tests on the suggestion tests' fixture with a scripted engine: the coordinator's instructions and facts and none of a student's, every step a card with DONE FOR YOU on, mail to the coordinator, the term's TAs and the instructor only, a meeting as a calendar event and an appointment refused when proposed, Waiting only beside a reply, an approved status saved under the term's key, the context read, a course no longer coordinated not prepared, a student's preparation as before); `tests/test_coordinator_followups.mjs`, new (3 tests of the page's wording, record route and joins); `tests/browser_coordinator.py`, new (the card's box greying the two fields out and back, the saved coordinator read back in Course settings and unticked again; a coordinator's follow-up on the Follow-up page with its name, "Coordinator's message", "Write to coordinator…" opening Mail's compose window to the card's address, no student record and no join). `tests/test_catalog_migrations.py`: the pre-ledger fixture drops `course_term_coordinators`, and the ledger lists `communications/003`, `course_coordinators/001` and `student_support/018` as applied with no table left behind and every foreign key holding. Run before landing in a worktree on isolated configuration and NVMe scratch: 33 related unit modules (804 tests: the follow-up, student support, communications, assistant, Jev, absences, Mail, migrations, design-language and TA modules) passed; the frontend unit tests (all `test_*.mjs`), JavaScript syntax and the typecheck passed; ten browser scripts passed (coordinator, followup suggestions, same matter, controls and redo, course pages, communications, teaching workflows, shell routes, home). The three migrations were rehearsed on a copy of the live catalog first (16,533 links, 334 follow-ups, 867 runs, 0 mutes and 4 written mails came through identical, no foreign key broken, 1.5 s) and are recorded on live since 23:52Z with the same counts.
NOT CHECKED, AND WHAT IS PROVEN ONLY ON FIXTURES: the full suite (over an hour) and the other browser scripts were not run. No real Gmail was searched for a coordinator's address: the first live capture round after a course is marked coordinated must be looked at (how many of the term's messages it keeps, and that mass mail to a list is found by its From). No model has read `COORDINATOR_PROMPT`: what a real preparation proposes for a real coordinator's message, and whether it leaves an announcement without a reply, is seen on the first live ones. A coordinator who coordinates two of the instructor's current courses is linked under one term only (COMMUNICATIONS): tested by the rule's code path alone, never with two real courses.

Makeup changes certify themselves, 2026-11-14 (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES" and its THE PAGE SAYS WHAT FACULTY IS ABOUT TO DO; PIPELINE; ABSENCES NO-GRADEBOOK second read, NO ANSWER THEN A 0, "A SCORE STANDS"; the instructor: "I thought we made it so that anything arriving after the first certify is automatically certified", then "Build both"): `tests/test_certification_automatic.py` (42 tests; new: a makeup that ended is given 0 by Faculty into its own cell and the plan reads current, with the page saying so beforehand and naming the 0 afterwards; a makeup recorded missed likewise; a student given 0 who is then owed a makeup is left out, the 0 cleared, and given 0 again when it ends; the 0 waits while a paper of the scans has no student, and the page promises none; it never replaces a score above 0, an excused or a missing mark; a makeup's 0 and a late paper go in one live certification; with the instructor's own uncertified change it is carried and never made twice; a left-out student who drops is no longer kept and nothing asks; the worker certifies a makeup's change that wrote no event of the assignment, and nothing with the switch off; CHANGED: a classmate owed a makeup since a certified 0 is now left out with the late paper and the plan reads current, where the markers were carried before), with `test_grading_certification`, `test_certified_archive`, `test_regrade_requests`, `test_regrade_panel`, `test_regrade_followup`, `test_regrade_mc`, `test_gradescope_after`, `test_gradescope_send`, `test_assistant_automation`, `test_makeup_booking`, `test_absence_makeups`, `test_grading_participation_approval` and `test_pipeline_improvements`, through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. Node: `tests/test_grading_certification.mjs` (new: the lead says what Faculty is about to do in place of "until you certify again", adds that other changes wait, Certify again… is the primary only when something waits for the instructor, the header and the fold name whom Faculty gave 0 and whom it left out), `tests/test_gradebook_changes.mjs` (Faculty's own certification is "a certification Faculty made by itself"), `tests/test_assignment_workspace.mjs`; the frontend typecheck. `manage_checks.py quick` 4 of 4 groups (565 Python tests; run before the review's three fixes, after which the suites named here were run again). Browser: `manage_checks.py browser --browser grading_certification` (1440 by 900, pictures looked at: the lead "Faculty does this by itself in a few minutes: gives … 0 (makeup ended).", Certify again… not the primary; afterwards "Faculty gave … 0 after their makeup ended by itself" and the fold's "Given 0: their makeup ended"). No migration. READ-ONLY ON LIVE before the restart (`mode=ro`): of the ten certified assignments of the current term none has a makeup that ended and none of their 178 certified zeros is owed a makeup now, so the rule took nothing at the restart; the worker's new cheap test read 1 to 3 ms an assignment; of that term's 17 certifications by Faculty 9 were carried, and 8 of the instructor's own re-certifications recorded only a makeup that ended or began or a drop. An independent review on Sonnet before the restart found no wrong write, loop or crash and four smaller points, three fixed (Faculty's 0 would have replaced an imported excused or missing mark; the page promised a 0 the scans warning then held; the worker's marker did not follow the exam record's sittings and participants) and one left as it is and written in the contract (an absence decided anywhere restarts every certified plan's five-minute settle). Restarted `faculty faculty-inbox` after the idle gate; two live Certify pages read back unchanged. NOT CHECKED: the full suite (over an hour) and the other browser scripts; no real makeup has ended on live since, so the first live 0 by Faculty is unproven.

A paper's last back, 2026-11-14 (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS" › A PAPER'S LAST BACK; SCANNER-INTAKE "A PAGE IN NO PAPER IS SET ASIDE, NEVER DROPPED"; the instructor, offered the back of a paper's last sheet that lies before the next paper's page 1 once copy 60 had settled live: "Yes, build it"): `tests/test_pages_by_place.py` (12 tests; new: such a back set aside with its reason, actor `automatic`, in the `pages_by_place` event and the done line, nothing left open, and never set aside again once taken back on Papers; on paper printed two-sided the side between two papers is left as it was), with `test_sequence`, `test_scan_stray_pages`, `test_scan_stacks`, `test_scan_separation`, `test_documents` and `test_grading_intake`, through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. Read-only on live (a `mode=ro` snapshot, deleted afterwards): of the kept scans the built rule sets aside 42 such backs, every one already set aside by the instructor by hand on 2026-11-11 (26 with some writing), and on the scans in the Inbox it changes nothing today.
A page between its own labels, 2026-11-14 (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS", GREY PAPER, NOTHING TO GROUP; SCANNER-INTAKE "A PAGE IN NO PAPER IS SET ASIDE, NEVER DROPPED"; PIPELINE; the instructor, of copy 60 of a late Take Home Assignment whose page 2 had both corner codes covered by writing, so Papers asked "Group the pages into papers": "I don't think that is the actual issue", then Yes to Faculty deciding by itself that such a side is its copy's page and Yes to setting aside the empty backs inside that copy's paper with a note naming it): `tests/test_pages_by_place.py` (copy 60's layout, one-sided paper scanned two-sided, gives page 2 by its place and sets aside the three backs inside papers with their reasons, actor `automatic`, one `pages_by_place` event, a done line on Papers, no grouping planned, nothing on a second pass; a back the instructor takes back is never set aside again; two sides that look printed for one missing page are named on Papers with action `set_aside`, never "check the sides per paper", and once the extra sheet is set aside the page is given; a page that did not arrive is said so; the last page of a file after its copy's last label; a side between two papers and a copy with a reviewed paper left as they were; paper whose later pages were printed bare stays with its first-page grouping until a later code of its job reads; a shuffled stack (the instructor: "I may scan them shuffled assuming that the data matrix will sort them out") is never placed and Papers says which page did not read, never a grouping; `media.ink_share` is what `blank_side` compares; 10 tests), the place rule watched failing with `PlaceService.apply` off and the old "check the sides per paper and preview the grouping" step coming back with the naming off; with `test_sequence`, `test_scan_stray_pages`, `test_scan_stacks`, `test_documents`, `test_scan_separation`, `test_unlabeled_exams`, `test_grading_scan_records`, `test_grading_intake`, `test_pipeline_improvements`, `test_pipeline_per_paper`, `test_inbox_scan_groups`, `test_exam_overview` and `test_grading_acceptance` (275 tests) and `manage_checks.py quick` (3/3), through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. The first run of those failed 11 grouping and separation tests whose paper has bare later pages after a page 1 label: the place rule had read them as unread codes, so a place now needs a later page's code read around it or of its print job. Read-only on live (a `mode=ro` snapshot of the catalog and the stored 200 DPI pictures, deleted afterwards): the built `PlaceService` on every scan in the Inbox decides exactly one paper, copy 60 of scan `3167c263` (side 3 its page 2, sides 4 and 6 set aside as backs, measured .00018 and .00011 against the blank cut of .0001); replaying the rule on all 102 kept scans, hiding each of the 308 codes that read in such a place put back 307 right, 0 wrong, 1 held, and of the 55 backs it would set aside the instructor had set aside 53 by hand on 2026-11-11 (the other 2 are copy 60's); with the paper-order check (a second snapshot, more scans by then) 29 of the 31 files holding paper labeled on every page are in paper order, the live result is the same and 305 hidden codes are put back right, 0 wrong. `GROUPING_RULE` v4 makes the worker look at every scan once more. No migration, no new route, no frontend change.
Names read after the inspection, 2026-11-14 (QUIZZES "NAMES ON UNNAMED STAMPED COPIES" › AFTER THE INSPECTION; instructor: "I notice we assign students between every document, would it be faster to leave the student assignment to the last step and all at once?", then "Yes, do it here" once measured on that morning's 28 turns: each name turn cost 7.7 s to start the reader plus 0.9 s a page, and 281 first pages took 29 turns, 3.7 min of starting alone): `copy_names._waiting` holds every first page of a scan whose inspection is pending or running, so its names are read up to 48 pages a model load once it ends. `test_term_print_jobs` (`test_names_wait_until_their_scans_inspection_ends`: a running scan's page is not read while the finished scan beside it is, running and pending both hold it, and it is read once its inspection ends, one load each), watched failing with the hold taken out; `test_quiz_drafts` (`test_photocopied_papers_split_before_their_names_are_read_are_named_the_same`, new: the worker's rotation reaches PHOTOCOPIED STAMPS before the name reader, and each copy is still named from its own paper; the photocopy test now also asserts no name is read while its scan is inspected); tests that read names on a scan the helper never finished inspecting now finish it first (`TermPrintJobTests.inspected`, two `test_quiz_drafts` scans); with `test_dr_copies`, `test_jev_papers`, `test_unlabeled_exams` (103 tests) and `manage_checks.py quick` (4/4), through `manage_checks.environment` on isolated NVMe scratch with scripted answers only. NOT MEASURED LIVE: no scan of unnamed stamped copies has arrived since; expected for that morning's pair, the Scan stage done in about 7 to 9 minutes instead of about 19, every copy named about 3 minutes sooner. No migration, no route, no frontend change. `faculty` and `faculty-inbox` restarted after the idle check, before the checks.

Name requests of a scan's pages side by side, 2026-11-14 (QUIZZES "NAMES ON UNNAMED STAMPED COPIES" › SIDE BY SIDE; instructor, asked after "check on why this step takes so long" of two copier scans (284 and 278 pages) whose Scan stages moved 20 pages per ~54 s worker turn, of which 13 s was page inspection, 14 s the OCR of the names and 20 s 32 Jev and Gemini requests one after another: "Yes, do it here"): `document_identity/copy_names.py` asks the requests of up to `PARALLEL` (4) pages at a time, each page's page check before its match, the rosters read first in the turn's thread. `test_term_print_jobs` (`test_the_name_requests_of_two_pages_are_asked_side_by_side_each_page_in_its_own_order`: two first pages' checks meet at a barrier that opens only while both are asked, each page's match follows its own check, each copy is named from its own page), watched failing with `PARALLEL` forced to 1 (the barrier breaks and no copy is named); with the rest of `test_term_print_jobs`, `test_dr_copies`, `test_jev_papers`, `test_quiz_drafts` and `test_unlabeled_exams` (101 tests), through `manage_checks.environment` on isolated NVMe scratch with scripted answers only. NOT MEASURED LIVE: no scan of unnamed stamped copies has arrived since; the expected turn is about 40 s instead of 54 s (8 pages' requests in two rounds of four, ~2.5 s a page). No migration, no route, no frontend change. `faculty` and `faculty-inbox` restarted after the idle check (no Jev request in flight, both scans inspected), before the checks.

A scan email's PDF may be as large as the email, 2026-11-14 (SCANNER-INTAKE "A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL", DOCUMENTS's adapter limits; instructor, after two B&W copier scans of that morning (284 and 278 pages, 11.1 and 10.2 MiB, DKIM, SPF and DMARC passing) were refused by the 10 MB attachment limit and listed as "Sender authentication failed": "Yes, and fix the cap", "the cap should be 25MB to match cloudflare limit"): `test_inbox` (`test_a_scanner_pdf_past_10_mb_is_kept_up_to_the_gateways_25_mb`: an 11 MiB PDF to the short scanner address is kept authenticated, in review, its bytes and original read back; the intake limit is 25 MiB and one byte more refused; an authored command keeps "at most 10 MB" word for word; `test_an_email_that_cannot_be_read_says_why_and_never_blames_the_sender`: a scanner email with two senders and a command email with an 11 MB file are each kept with their original, unauthenticated, cannot be reviewed or approved, and say what refused them; `test_scanner_checks_both_senders_alias_and_envelope_version` now asserts the error: a failed sender check still says "Sender authentication failed", a guessed address names no route), in both the fallback and managed-storage classes, with `test_inbox_scan_groups` and `test_request_limits` (87 tests), through `manage_checks.environment` on isolated NVMe scratch. The first run caught the submit step checking sizes a second time against 10 MB (the gateway would have answered 400); fixed and restarted before any email arrived. Recovery on live (the instructor's "Yes"): both PDFs taken byte-for-byte from the kept originals, no zero runs, and retained with `manage_inbox.py scan` (jobs 0f529f72, c0c9a007; sha256 read back equal); the two unclassified emails left in Submissions for the instructor. No migration, no new route. `faculty-email-gateway`, `faculty` and `faculty-inbox` restarted after the idle check, before the checks.
A score stands over the unanswered card's 0, 2026-11-14 (ABSENCES "MAKEUP BOOKING" › NO ANSWER, THEN A 0 › A SCORE STANDS, NO-GRADEBOOK's fourth READ; instructor: "we need to make sure that 0 doesn't overwrite a positive score for that assignment. for example, a student says they will be absent so a makeup card is sent, but they actually show up and take the assignment as normal, the card shouldn't overwrite that score"): `test_makeup_booking` (`test_a_0_is_never_recorded_over_a_score_and_is_taken_back_when_one_turns_up`: a student with 8 in the quiz's gradebook column gets no 0 and the row says why, a scored 0 holds nothing back, a 6 that turns up after Faculty's 0 takes it back once (`makeup-no-answer-scored:<offer>`, actor `automatic`) and the row returns as "Card withdrawn · its 0 was taken back", and the instructor's own "Missed makeup, record 0" over a score stands), watched failing with the score read off and, separately, with the take-back off; with `test_absence_makeups`, `test_absences`, `test_department_makeups`, `test_absence_totals`, `test_followup_suggestions`, `test_workspace_overview` and `manage_checks.py quick` (4/4), through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. Read-only on live: none of the 3 standing automatic 0s (Quiz 7 ×2, Quiz 5) has a score above 0, so nothing is taken back; of 14 owed rows none has one and none reads No answer, so no row's label changes today. No migration, no new route, nothing written to the gradebook. `faculty` and `faculty-inbox` restarted before the checks.
Only what is Faculty's is kept, 2026-11-14 (RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT", "Filing", "STORAGE", "The page, routes and the Assistant"; STORAGE "ERASING A RECORDING THAT IS NOT KEPT"; ASSISTANT "THE TIERS"; the instructor: "I want faculty to only keep recordings that deal with the courses/teaching. the ones that don't belong shouldn't be kept, if it is unsure then I should be asked to keep it or not", and their three choices the same day: "Nothing shown", "Erase it", "Home and sidebar too"). `tests/test_recordings.py` (47 tests, the capture ones run once more on the two-drive storage engine; in `manage_checks.py quick`), against the loopback stand-in of Plaud. NOT KEPT: `test_inside_an_entry_naming_no_course_of_facultys_it_is_not_kept_and_nothing_of_it_is_held` (no row in any table of the module; ONE row of `recording_not_kept`, whose columns have no place for a title, a start or a length; listed again it is passed over), `test_not_facultys_waits_while_google_cannot_vouch_and_an_away_entry_files_nothing` (unchanged in what waits; what Google then vouches for is not kept), `test_what_is_not_facultys_leaves_no_row_what_waits_is_never_fetched_and_ask_again_brings_both_back` (Don't keep of one that waits; Ask me again lists both again and each WAITS, the calendar deciding nothing for either; Keep fetches one and takes its marker away; `file` refuses Not Faculty's), `test_one_plaud_no_longer_lists_cannot_be_asked_about_and_stays_not_kept`, `test_ask_again_finds_one_made_long_before_it_was_decided_and_takes_up_nothing_else_that_old` (a recording of June not kept in November is found; one of May that Faculty never saw is not taken up; the status still says what a round lists). ERASED: `test_a_kept_recording_the_instructor_does_not_keep_is_erased_with_its_files_and_is_asked_about_no_more` (every row of it gone and no other recording's; its bundle's folder gone from every drive; its page and audio 404; the other recording whole and served; a repeated request ID answers as the first did; never fetched or asked about again; asked about again it waits and is not kept by the calendar that once filed it), `test_a_recording_that_is_leaving_is_hidden_at_once_and_erased_by_the_next_round_when_one_was_running` (pressed while a round holds the capture lock: listed nowhere, 404, not searched, not filed back; erased by the round it asked for), `test_a_recording_marked_not_facultys_before_this_rule_is_erased_by_the_first_round`, `test_a_recording_fetched_twice_is_erased_with_both_bundles_and_every_transcripts_lines`, `test_one_that_cannot_be_erased_stays_hidden_is_rolled_back_whole_and_does_not_hold_back_the_others` (the erasure's name is not left set; its rows are whole and retained; the next round erases it), `test_a_request_id_is_one_recordings`, `test_recordings_are_append_only` (a stray DELETE is refused on every table as before; while ANOTHER recording is named, this one's rows are still retained). STORAGE (two drives): `test_files_a_drive_still_holds_are_said_on_the_page_and_removed_when_it_is_back` (the HDD away: rows gone, the bundle named, the page told with the engine's reason; removed by the next round; nothing to prune and the next nightly backup complete), `test_what_is_on_the_drive_that_is_present_is_removed_at_once`, `test_nothing_is_removed_while_the_nightly_backup_runs`. ASKED: `test_home_asks_about_what_waits_for_keep_or_dont_keep_with_a_count_and_a_link_only` (`recordings:keep` on `/api/workspace/overview`: a count, a link, never a title), `test_filing_by_hand` (Don't keep and Ask me again are same-origin writes with a request ID). `tests/test_recordings_reading.py`: `test_a_read_recording_that_is_not_kept_is_erased_its_waiting_card_taken_back_and_what_was_remembered_stands` (readings, items, events and lines gone; the waiting card expired by Faculty with "This recording is no longer kept."; nothing on the calendar; the memory kept by Remember stands; it is not read again), `test_what_was_approved_out_of_a_recording_stands_when_it_is_erased`. `tests/test_recordings_page.py` (the search holds nothing of one that is not kept; Don't keep erases both transcripts' lines), `tests/test_recordings_memos.py` `test_a_recording_that_is_not_kept_never_surfaces` (hidden at the press though its vectors are still in the index; dropped at the worker's next turn; erased, nothing is left for a later turn; asked about again and kept, it is fetched and read anew). `tests/test_catalog_migrations.py`: `recordings/005_not_kept` is applied as new on a pre-ledger catalog; the twelve `*_retained` triggers are there, eleven of them guarded, and no erasure is named. `tests/test_recordings.mjs` (7), `tests/test_recording_page.mjs`, `tests/test_ui.mjs` (the rail's badge counts `recordings:keep`): a waiting row has ONE framed step, Keep…, and Don't keep as words; a kept row words only, Don't keep marked; the list has no fold and no row of what is not kept, only how many with Ask me again; files a drive still holds are said in orange, escaped. `tests/browser_recordings.py` (Chromium, 1440 by 900; pictures looked at): Keep or not first with the rail counting 2; the lecture of another course nowhere on the page, only "1 recording at Plaud is not kept"; Keep through the one dialog, whose kinds no longer hold Not Faculty's; Don't keep on a waiting row at the press, the rail's count gone at once; Don't keep on a kept row asks ("Faculty erases its audio, its transcript and what it read from it..."), Cancel erases nothing, Erase it removes the row and its folder; Ask me again brings all three back to wait, each saying so; Home's card. `tests/browser_recording_memo.py`: a training is kept through Keep…. DRY RUN ON A COPY OF THE LIVE CATALOG (2026-11-14; the live file opened read-only for the copy, the copy deleted after): 005 applied; a stray DELETE still refused; the two recordings marked Not Faculty's before the rule (both by the calendar, neither ever fetched) erased to one marker each (two rows less in `recordings`, `recording_revisions`, `recording_links`, `recording_fetch_state`; no bundle to remove); the nine other recordings unchanged; `foreign_key_check` clean, `integrity_check` ok. RAN 2026-11-14 in a worktree, in `manage_checks.py`'s isolated environment: `manage_checks.py quick` 3 of 3 groups (JavaScript syntax; frontend unit tests; 565 Python tests); `tests/tools/browser_suite.py` for `recordings`, `recording_page`, `recording_class`, `recording_memo`, `recording_reading`, `recording_student_meeting`, `navigation`, `home`, `palette`, `palette_route`, `shell_routes`, `shell_form`, `smoke`, `theme`: 14 of 14 (13 first time; `recording_memo` pressed the button renamed from File to Keep…, was updated and passes); `test_assistant`, `test_design_language`, `test_storage`, `test_assistant_memory`, `test_assistant_workflows`, `test_communications`, `test_faculty_embedding` (283 tests) passed; the frontend typecheck by hand, clean. An independent review on Sonnet before the commit found no serious defect and six smaller ones, all fixed; five have their test above, and the fifth listed here has none of its own (a round now reverts only the recordings it set out to find): Ask me again looked back only a month before the day of the decision; an erase during the nightly backup's first pass could have had the bundle copied back to the HDD; one failing erasure held back the others; a request ID was not tied to its recording; a round could have reverted a recording asked about while it ran; with a drive away nothing was removed and nothing said.
NOT CHECKED, AND WHAT STAYS AFTER AN ERASURE: the full suite (over an hour) and the other browser scripts were not run. No real recording was erased: the dry run above erased only two list rows on a copy, and the removal of files from both real drives is proven on temporary drives alone. The race with the nightly backup is closed by a lock and tested by holding that lock, not by a backup copying at that moment. An erasure does NOT reach: the Assistant's own record (a card made from the recording, taken back or approved, keeps the words it quoted, and a memo's run its title); catalog backups made before it, until `prune` retires them; anything at Plaud. A recording the calendar wrongly shows to be nothing of Faculty's is not kept without a word: the count and Ask me again are the only way back to it, and only while Plaud still holds it within two years.

Recordings in the sidebar, 2026-11-14 (RECORDINGS "The page, routes and the Assistant"; FRONTEND shell and page rules; the instructor, who did not find the page while it opened only from the palette and Workspace settings: "Yes, add it"): the rail has a fourteenth page, Recordings, in TODAY after Follow-up, with no attention badge (it has no Home card); the palette lists it among its Pages and no longer among its Actions; Workspace settings still links it. `tests/browser_navigation.py` (Chromium): fourteen pages in order with Recordings fifth, fifteen entries with Workspace settings, every entry on screen at 1440 by 900 and (new in the check) at 1536 by 864 with a rail that does not scroll, and the Recordings entry opening its page and marked as the page shown like every other. `tests/browser_recordings.py`: the entry says Recordings, carries no badge, opens the page and is marked while it is shown and not on Home; the palette lists Recordings once and opens it. RAN 2026-11-14 in a worktree, through `manage_checks.py`'s isolated environment: `tests/tools/browser_suite.py` for `navigation`, `recordings`, `palette`, `palette_route`, `shell_routes`, `shell_form`, `smoke`, `theme`, `home`, `recording_page`, `recording_class`, `recording_memo`, `recording_reading` and `recording_student_meeting`, 14 of 14 passed first time; `manage_checks.py quick` 3 of 3 groups (JavaScript syntax, frontend unit tests, 545 Python tests); the frontend typecheck by hand, clean. `navigation` was run once more after the 1536 by 864 size was added to it, and passed. NOT CHECKED: the full suite (over an hour) and every other browser script; a window shorter than 864 px scrolls the rail as before.

Recordings, step 6: memos, training, search by meaning, 2026-11-14 (RECORDINGS "Memos, training, search by meaning (step 6)", "The reading" › ON ITS OWN and FACTS, KNOWN LIMITS 14 to 19; ASSISTANT "A dictated request"; COURSE-NOTES "TRAINING RECORDINGS"; EMBEDDING "WHAT IS SENT", "USES"; the plan's three lines for its last step and the instructor's choices of 2026-11-12: read on its own, every item quotes the transcript, a memory only on Remember, no general task list): `tests/test_recordings_memos.py`, new (16 tests, in `manage_checks.py quick`), against the loopback stand-in of Plaud, a SCRIPTED engine for the memo's reading, a scripted TURN in the Assistant's engine for a dictated request's run, and a STAND-IN embedder. MEMOS: `test_a_memo_is_read_into_requests_that_rest_on_its_exact_words` (the calendar never files a memo; one request for the reading with its own instructions and no record of Faculty's among its facts; two requests and a rule kept, one item whose words were never said dropped; each request queued once as a preparation of kind `memo`, `memo:auto:<item>`; while the Assistant has them only the rule needs the instructor; a later round asks nothing again), `test_a_request_is_run_by_the_assistant_and_every_change_waits_as_a_card` (the run is told the memo's instructions, the exact words and the day, never "Trusted task"; a calendar entry, `auto` in chat, is a waiting card and nothing is on the calendar; Approve writes it through the Calendar's own route; a run that changes nothing leaves an answer and no card; Done settles), `test_two_voices_ask_nothing_until_the_instructor_presses` (no run; the page says why; their press queues that one request, a repeated request ID once, a second press while it is queued refused; a rule is not asked), `test_one_voice_named_as_somebody_else_is_not_sure_until_the_instructor_names_themself` (naming themself sends the requests without another reading; a voice Plaud left unnamed is taken as theirs; a name is the instructor's only when every word of it is theirs, so "Chris Wong" and "Dr. Example" are not), `test_what_faculty_asked_by_itself_is_withdrawn_when_it_is_no_longer_sure_and_a_press_stands` (the voice named as somebody else: the run Faculty queued is refused where it would start or be approved, and withdrawn; the run they pressed stays; withdrawn, a request can still be pressed), `test_with_the_assistant_off_a_queued_request_says_so_and_its_run_is_listed_with_its_memos_page`, `test_a_dismissed_request_has_its_run_and_cards_withdrawn` (its card can no longer be approved; one dismissed before its run never runs; opened again it is asked only by the press, which carries their sentence to the run), `test_filed_elsewhere_a_memos_runs_are_withdrawn_and_a_reread_asks_only_what_is_new` (a request the new reading still holds keeps its run and its card), `test_the_assistant_cannot_send_itself_a_request_and_a_memo_run_starts_no_other` (`recordings.ask` is `never`; the run's own try is refused, and so is another preparation; the preparation route does not take the kind), `test_past_the_first_few_requests_wait_for_the_press`. TRAINING: `test_a_training_is_filed_under_its_course_kept_and_listed_and_never_read` (one link, `course` with purpose `training`; kept, never read, its page says so; listed for its course, and `GET /api/recordings?course=` answers that course's trainings alone; its words found by the search; the same course as a meeting is one more revision of that link and is read; filed as a training again its reading is no longer shown), `test_only_a_course_link_is_a_training` (the migration's trigger). SEARCH BY MEANING: `test_what_was_said_is_found_by_describing_it` (each passage one text, as a document, words alone; a description sharing no word with the passage finds it; a passage a keyword result shows is not repeated; without `meaning` the answer has the four keys it always had; one recording alone; the index, its write-ahead log included, holds no word that was said and no name), `test_a_recording_that_is_not_facultys_never_surfaces` (one that needs filing is never read or sent; one filed as Not Faculty's after it was kept is not shown at once, though its vectors are still there, and they are dropped at the worker's next turn, as are those of one only partly read, while another recording still waits), `test_a_worker_that_is_off_down_or_lending_fails_nothing` (a lent card and a silent worker set nothing aside; a passage refused three times is set aside and the rest are embedded; with no worker named nothing is sent and the search says so), `test_passages_are_gathered_in_order_and_bounded`. `tests/test_recordings_reading.py` (33): `test_a_memo_is_not_read_yet` is now `test_a_training_is_kept_for_reference_and_not_read`, and the recording "filed elsewhere" where nothing is read is a training. `tests/test_catalog_migrations.py`: `recordings/004_training` is applied as new on a pre-ledger catalog, and adds its column in place. `tests/test_recording_memo.mjs`, new (7): the memo's two groups with only what needs the instructor in orange, the not-sure line and its press, each state of a request, the quiet refresh only while a request is with the Assistant, a training's page, "Nearest by meaning" with no score and at most four lines a passage, a course's trainings alone. `tests/browser_recording_memo.py`, new (Chromium, 1440 by 900): the memo's page, Approve writing the calendar entry, Ask again with a sentence, Done; the two-voice memo and its press (one run, no other); a training filed from the filing window, the course Home's line and `#recordings?course=`; a description finding the passage and opening the recording there. RAN 2026-11-14 in this worktree with `/mnt/nvme/Teaching/venv/bin/python`, through `manage_checks.py` and its isolated environment: `manage_checks.py quick` 3 of 3 groups (JavaScript syntax; 713 frontend unit tests; 545 Python tests, the five earlier Recordings modules and this one among them); `manage_checks.py browser --browser recording_memo --browser recordings --browser recording_page` all passed; and, in the same isolated environment, `test_catalog_migrations`, `test_assistant_workflows`, `test_assistant`, `test_assistant_workspace`, `test_faculty_embedding`, `test_picture_search`, `test_course_notes` and `test_assistant_past_work` (176 tests) passed. The full suite was not run (over an hour). An independent review on Sonnet before the commit found no way around the rules for who may ask the Assistant and ten smaller things; fixed from it: a request queued while the Assistant is off now says so and is not polled, a voice's name is the instructor's only when every word of it is, what Faculty asked by itself is withdrawn when it is no longer sure, the index drops what may not be shown at every turn, the test of the index's contents reads its write-ahead log, a course's trainings are listed past the 300 newest recordings, and the Assistant's own list links a memo run's page.
NOT CHECKED, AND WHAT IS PROVEN ONLY ON FIXTURES: no model has read `grading/prompts/recording_memo-v1.txt` and none has run a dictated request (`MEMO_PROMPT`): how a real memo is split, whether a misheard name is kept as said and then caught against the records, and how many cards a real run makes are seen only on the first live memo. The embedding worker was never reached: the ranking is a stand-in's (one axis a topic word), so whether a description finds the right passage among real transcripts, and how near an unrelated passage scores, is not known; there is no bar, by decision, until the first live index is measured. Not exercised: two processes (the Inbox worker queues after a reading, the web process dispatches; every check is one process), a run cut by a restart, the first fill of the index over the transcripts already kept, the Automation view's card of a `memo` run in a browser. The frontend typecheck was skipped (no local TypeScript compiler). `restart-check` counts a memo's run (it is a preparation) but still does not see a READING in flight: before a restart look for a readable recording whose reading is `waiting`.

Recordings, step 5: a recorded class, 2026-11-13 (RECORDINGS "Classes (step 5)", "The reading" › ON ITS OWN and FACTS, "What an item may become"; SCHEDULING "CLASS SCHEDULE: WHAT A RECORDING REACHED"; the instructor's choices of 2026-11-12: classes are included, filed on a section's class day and never to a student, read on their own): `tests/test_recordings_classes.py`, new (18 tests, in `manage_checks.py quick`), against `tests/fake_plaud.py` and a SCRIPTED engine in the Assistant's place (`ClassEngine`; no check asks a model, reaches Plaud or reads a real recording; the eleven-line class and everyone in it are invented). `ReadingTests`: a recording that began inside section 507's class meeting is filed on that class day and read in the same round by its own instructions (`recording_class`, `reading.CLASS_SCHEMA`, run version `class-v1`, `filed_as` the class day), told the recording's day, the instructor's speaker, the section, the course's notes sections by number and title and the TITLES of the section's work, and no date Faculty holds, no planned topic and no student; of ten answered items five are kept (two `covered`, three `announced`) with who spoke and when from the transcript, and five are dropped with the reason (a section the notes do not have; talk before class began; a day told to one student whose words hold his whole name, recorded with neither its sentence nor its quote; a task; a day told with no day); A CLASS RECORDING MAKES NO STUDENT LINK (its only link is `class_day`, it is on neither student's timeline), Remember refuses its items (400) and no card of any kind follows a day told to the class; filed by hand on a class day it is read the same and as a memo it is not; `reading.checked` alone keeps only a number the notes have, once (any well-formed number where Faculty holds no outline), an announced day with its clock time, work the facts did not list as about nothing, drops a whole name in a sentence and in an announced day's quote (never in a `covered` item's quote) and keeps a notes title spelled like a family name, and drops a class's kinds from a meeting; a meeting filed under the term is read exactly as before. `CoveredTests`: THE PLAN'S ACCEPTANCE: a day planned 5.6 to 5.8 and recorded through 5.7 is `differs` ("Not reached: 5.8."), counts once on the page, the row and the attention count, carries ONE waiting card on the class schedule's own route (`scheduling.class_plan_save`, engine `recordings`, its page the recording) whose body sets that SECTION's own topics for the two days against revision 1, and changes nothing until Approve; the section's class schedule read shows `recorded` on that day (reached, not reached, `differs`) and no other section's or day's does; Read again makes no second card; Approve moves 5.8 to Friday for that section alone, the day reads "Reached what was planned for this day." and the next round proposes nothing; a DECLINED move leaves the plan, is no longer orange (`left`), is not proposed again by a Read again or a round, and comes back only by the instructor's press (`/cards` `kind` `move`: once for a request ID, 409 while its card waits, 400 for another kind); with two sections' cards waiting, approving one leaves the other's Approve refused by the schedule's own preview (`stale`, nothing written), the next round takes it back and proposes it against the plan as it is, a further round leaves that card alone, and it is then approved; a section reached ahead of the plan and one reached on an earlier recorded day are shown and are no difference; a day with nothing planned is `unplanned` and one whose reading kept no section is `none`, neither orange, neither with a card (409 on the press); a dismissed section is no longer reached and the waiting card is replaced at once by one moving both, and a day left with nothing keeps a note saying where its topics went; filed elsewhere the card is taken back and the class day no longer shows it, and the last class day is orange with nothing offered and says why; two recordings of one class are one class day with ONE card and one count, both on the recording whose reading kept a section, and a re-read of the second replaces the day's card; a move the class schedule would refuse (the next class day already holds 8 topics) is never proposed, by a reading, by two further rounds or by the press (409 with the page's own reason), and is the instructor's to propose once there is room; a term no longer in Faculty fails neither the page nor the list's count. `AnnouncedTests`: Exam 3 told for Wednesday against its record's Thursday is under Needs you with both said and the record named; a planned homework due the day that was said agrees and asks nothing; "no class" on a day the section still meets differs; nothing is carded; Done settles one and correcting the record makes the other agree; a day of something Faculty holds no record of is shown with nothing claimed, and "class on Thursday" for a section that does not meet then differs. `tests/test_recordings_reading.py` (34 tests): what is not read yet is a memo alone. Node: `tests/test_recording_class.mjs`, new (3: the class's day with only an unreached planned section in orange and every other state plain, and why no move can be offered in the server's words; the move card's plain lines from the card's own body, this section's and no other's; a class's groups, no coordinator or student, two oranges and no third, a `covered` item's one control), and `tests/test_recordings.mjs` (the header says "from your recordings"). Browser, in the real shell: `tests/browser_recording_class.py`, new (`manage_checks.py browser --browser recording_class`, 1440 x 900: the list's "3 for you"; the page opens on the class's day with Planned, Reached and the orange "Not reached: 5.8 Logarithms."; the groups Needs you 2, What the class reached 2, Told the class 1 with no coordinator and no student; the waiting card with "Wed, Nov 11 5.6, 5.7" and "Fri, Nov 13 5.8"; the section's Class schedule page shows "Reached 5.6, 5.7" and the orange chip under that day's topics and the plan is still revision 1; Approve from the recording's page moves it and the orange goes; a section dismissed makes the day orange again, the instructor's press proposes the move, Decline leaves the plan and the Class schedule page, read again on coming back, says "plan left as it is" with no orange; Plaud heard only GETs). Run after an independent review (Sonnet, read only) whose five confirmed findings are fixed and tested here: a day told to the class was looked up unguarded, so a deleted term would have failed the page and every row's count; of two recordings on one class day only the first could raise the card, and the day's count sat on a recording that might have no reading; a move the schedule would refuse took the waiting card back and was tried again every round; an earlier card of the day could stay waiting beside a later one; the name check read the sentence alone and kept the dropped item's words. Run and passing on the tree as committed: `manage_checks.py quick` (529 tests) and `manage_checks.py browser --browser recording_class --browser recording_student_meeting --browser recording_reading --browser recording_page --browser recordings --browser class_schedule --browser assistant --browser theme --browser smoke`; the frontend typecheck by hand with the main checkout's compiler (a session worktree has none, so `manage_checks.py` skips it there).
NOT CHECKED, AND WHAT IS PROVEN ONLY ON FIXTURES: no model has read `grading/prompts/recording_class-v1.txt`, so whether a real reading of a 45-minute class stays at a handful of items, matches a section said as words ("five eight") to its number, and tells a section worked through from one only mentioned is seen on the first live readings (step 3's first live readings were about eight times too long). The two class recordings Faculty already keeps (2026-11-13, sections 507 and 508) were looked at read-only for their shape before the instructions were written and will be read by the first rounds after this lands. The live term's class schedule holds no topics on any day, so there the comparison is `unplanned` until topics are planned. Plaud's monthly transcription minutes against about 40 hours of class a month were not looked at.

Recordings, step 4: a student's recorded meeting, 2026-11-13 (RECORDINGS "Student meetings (step 4)", "SEVERAL STUDENTS IN ONE RECORDING", "AN APPOINTMENT IS SETTLED ALREADY" › RECORDED AS ONE THAT DID NOT HAPPEN; COMMUNICATIONS "FACULTY'S OWN RECORDS" › A RECORDED MEETING; TEACHING-WORKFLOWS "SEPARATE COMMITMENTS" › HEARD IN A RECORDED MEETING; STUDENT-NOTES "A NOTE OF A RECORDED MEETING"; ASSISTANT "SAID IN THEIR RECORDED MEETINGS", "WAITING AFTER THE REPLY" › ALREADY WAITING; instructor: "build 4"): `tests/test_recordings_student_meetings.py`, new (17 tests, in `manage_checks.py quick`), against `tests/fake_plaud.py` and the SCRIPTED engine of step 3 (no check asks a model, reaches Plaud or reads a real recording; the people are invented, and NO REAL STUDENT MEETING had been recorded when this was built). `ReadingTests`: a recording that began inside a student's appointment is read in the same round by its own instructions (`recording_student_meeting`, run version `student-meeting-v1`), told the roster's name and section and the appointment (day, times, purpose, how it is recorded) and nothing of the coordinator, the Canvas copy or the student's own records; a standing rule said in it is dropped by the quote check ("not a kind of item this meeting is read for"); its items are the student's, in the groups Needs you, They told you and They said they would, looked up in no record; filed by hand under a student it is read the same; the Assistant sets students and proposes a card only on a card. `TimelineTests`: ONE entry, "Meeting", on that student's timeline and no other's, dated when it began, with what the reading kept in plain lines and the recording's page, no message ID; a dismissed item leaves it; filed elsewhere it is gone; a follow-up preparation's facts carry her items, each with the exact words, at most so many, and the other student's none. `CardTests`: what was promised is ONE card (`student_support.create`, a new follow-up "Meeting on Tue, Nov 10", four commitments each naming the item it was heard in, a day that was said as its check back) and nothing is tracked until Approve; read again makes no second card; approved, the follow-up's tasks carry `heard`, the recording's page shows them as done and the list's count falls to none; on an OPEN follow-up the card is `student_support.revise` with its own commitment first, its revision and the messages it has seen, a follow-up changed meanwhile REFUSES the Approve, and the instructor's press proposes it again (the same press twice once, 409 while one waits); a tracked conversation already Waiting on the student is left Waiting and its card is approved (ALREADY WAITING), a student message that still needs a reply stays Needs reply, and the Assistant resolving the follow-up keeps where each was heard; one promise is never tracked twice (a second follow-up 409, the same item twice in one 400, an ordinary edit resending its commitments 200); the note card is `student_notes.create` dated the recording's day, saved only by Approve and never proposed again once saved; a declined card comes back only by the press, and a recording filed as a memo takes both waiting cards back; a commitment naming another student's item, or no item, is refused (400), and one that names none is saved without the key. `PartsTests`: each of two students needs a part, parts may not overlap, repeat a student, end before they start or after the recording; set, each link carries its part and the appointment it was filed by, the recording is read again told each one's lines, each item is the student's in whose part it was said, each timeline entry opens that part (`?t=…&to=…`) and holds none of the other's words, each student has their own two cards, a press names whose, and moving the line between them takes waiting cards back and makes them for the parts as they are; filed again by hand under one of two students, she keeps HER part, the other's words are nobody's and his waiting cards are taken back. `AppointmentTests`: an appointment later recorded as a no-show is marked on the page, the row and the attention count, nothing of it is changed, and correcting it takes the mark away; a recording inside a CANCELLED appointment is filed under that student only where nothing else was on the calendar (a meeting of the course at that time is filed under the term), and waits while Google's calendar cannot vouch for it. `tests/test_recordings.py` and `tests/test_recordings_reading.py` follow (the cancelled appointment of step 1's test now files and is marked; the attention count carries `appointments_off`; what is not read yet is a memo and a class). `tests/test_recording_student_meeting.mjs`, new (5 tests: who the meeting was with and its headings, the two cards with what they would do in plain lines and nothing offered twice, the orange of an appointment that did not happen on the page and in the list, parts and `stretched` with nothing of another part and never what the quote check left out, `parseOffset`, a Meeting's "Open the recording" only for Faculty's own recording page, and a task's "Said in your meeting"). `tests/browser_recording_student_meeting.py`, new (`manage_checks.py browser --browser recording_student_meeting`, 1440 x 900): the list's count, the page's student and groups, one orange count beside the cards, both cards approved from the page (the follow-up's four commitments and the note are written only then), the no-show's orange on the list and the page, the Students and parts window with the server's own refusal shown, two students each with their part and their own cards after it is read again, and his part alone at the address his timeline opens (its two lines, his one item, none of hers, then the whole recording again); Plaud heard only GETs. Run after an independent review whose findings are fixed and tested here (re-filing by hand kept no part; the Assistant's resolve dropped where a commitment was heard; two cards could be made in one instant): the quick suite, the follow-up, Assistant, follow-up page and timeline modules, and the browser checks `recording_student_meeting`, `recording_reading`, `recording_page`, `recordings`, `communications`, `student_notes` and `followup_desk`.

Recordings: a term's assessments include the course's undated assignments, 2026-11-13 (RECORDINGS "A RULING" › HELD BY NAME › THE TERM'S ASSESSMENTS, "The reading" › FACTS; instructor: "Yes, land and re-read", after which both meetings were read a third time under instructions version 3 and the rulings of the final exam carried the name "final exam", which the page said no assessment of the term was called: the course's "Final" had no date in the term yet): `tests/test_recordings_reading.py` (34 tests): `test_an_assignment_of_the_course_with_no_date_yet_is_among_the_terms_assessments`, new (an assignment "Final" of the course with no exam record is told to the reading as `a2` with no given date; a ruling called "final exam" shows "Final" and "Held for “Final”: matched by the name it was called by", and one given the id `a2` is kept under the assignment's own id; once the term gives it a date and a graded plan, both are offered at its rubric, the first saying its name).

Recordings: a ruling held by the name it was called by, 2026-11-13 (RECORDINGS "A RULING" › HELD BY NAME; GRADING "RULINGS SAID IN A RECORDING" › HELD BY NAME; instructor, of four rulings of the 2026-11-11 meeting that named no assessment and so would never have been offered at any rubric: "Yes, build that"): `tests/test_recordings_reading.py` (33 tests): `test_a_name_is_an_assessment_only_when_it_holds_every_word_of_its_title`, new (`reading.named`: "final exam", "the final" and "Final examination" are "Final"; "final exam review" and "final answer on question 20" are not; "quiz 2" is "Quiz 2" and never also "Quiz", "the quiz" is "Quiz", "MATH 140 Exam 13" is "Exam 13" and never "Exam 3"; "exam three" and "Exam III" are "Exam 3"; nothing of that name, no name or only the course's code answers none; the same title twice answers both; `reading._about` keeps the name of an unlisted assessment for `assessment` and `rubric` only, never beside a listed id, and never for a seating chart or a booking), and `test_a_ruling_of_an_assessment_the_reading_had_no_listing_for_is_held_by_the_name_it_was_called_by`, new (a ruling called "the third exam, Exam 3" with no id shows "Exam 3" and "Held for “Exam 3”: matched by the name it was called by" on the recording's page and is offered at Exam 3's rubric with `named`; one called "Financial Decision Paperwork" says no assessment of the term is called that and is offered nowhere; a ruling with neither id nor name stays a ruling about nothing; a task of an unlisted assessment is not held for a rubric; a plan the name is not refuses the ruling with 400 and the plan it is accepts it; an assessment of that name made AFTER the meeting is offered the ruling at its rubric with no new request to the engine, and Exam 3's rubric still lists only its own). `tests/test_grading_recorded_rulings.mjs` (a ruling held by a name says "Said of “<name>”" after Hear it, escaped; one with an id says nothing of the kind). The reading's instructions are version 3 (`grading/prompts/recording_reading-v3.txt`, `VERSIONS["recording_reading"] = 3`, `reading.VERSION = "reading-v3"`): no model has read them at the time of writing, and the four rulings carry a name only once their meeting is read again.

A reason nothing could excuse is proposed Not excused, 2026-11-13 (ASSISTANT "NOT EXCUSED WHEN NOTHING COULD EXCUSE IT", ABSENCES "RECORDED BY THE ASSISTANT"; instructor, of a follow-up card for a student missing class for a BIOL 111 exam on West Campus, left Waiting for your decision: "I had to manually change this to not excused. It seems like the agent defaults to waiting for me even if the absence is clearly not excused"; asked how far that reaches, they chose the reason itself and that it stays a card they press): the follow-up prompt and guide had said to propose no decision at all unless the absence was excused, so every absence came up Waiting until the deadline worker decided it. `test_followup_suggestions` (`test_an_absence_nothing_could_excuse_is_proposed_not_excused`, new: the card stands on Not excused with its three choices and no “suggested”, its decision note shown, nothing recorded until Approve, the same proposal held out of DONE FOR YOU as “a decision is the instructor’s”, each of Student Rule 7’s nine reasons and a death refused, files sent with the absence refused, a revision refused once a document stands, and the prompt’s own line; `test_an_excuse_needs_documentation_or_the_instructors_word`, where an illness is now refused Not excused too), both watched failing with `_not_excused_clear` removed; with the rest of `test_followup_suggestions` and `test_assistant_tool_guidance` (160 tests on the final tree), and `test_absences`, `test_followup_learning`, `test_followup_deadline_closes`, `test_followup_evidence_closes`, `test_followup_redo`, `test_followup_completion`, `test_absence_makeups`, `test_department_makeups` (193 tests), through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. No model was asked: what a preparation proposes from these facts is NOT TRIED. Read-only on live: the restart gate said idle. No migration, no new route, no frontend change. Restart `faculty` and `faculty-inbox` after the change.

Recordings, step 3, after the second live readings, 2026-11-13 (RECORDINGS "CHECKED AGAINST FACULTY" › NAMED LIKE IT, ONLY A MEETING COUNTS; instructor: "Yes, land and re-read", after which both kept meetings were read again under instructions version 2: 13 and 9 items for the instructor where the first readings had 46 and 30, and 56 and 13 items in all where they had 105 and 35): `tests/test_recordings_reading.py` (31 tests): `test_a_day_holds_the_thing_only_by_a_near_time_or_a_like_name`, new (with a homework deadline of the course on its day, something said to happen that day with no time is NOT already done: "Nothing named like this is on your calendar on Tue, Nov 17." and a card is offered; an entry named like it, "MATH 140 - Review sessions" for "MATH 140 review session", is the thing, the course's code naming nothing; "3" is no part of "5.3"; with neither a time nor a name the day's entries are shown, nothing is claimed and nothing offered), and `test_a_class_meeting_and_office_hours_are_never_the_meeting_that_was_called_off` now also holds a class event made by hand for one section, "MATH 140 · Section 508" (never listed as the meeting called off, never told to the reading as a calendar entry; `checks.class_meeting` is true only of a custom, section-owned entry whose title ends in "· Section N"). Dry run before landing, read only: the new rule against the live readings' eleven dated items and the live calendar feed, where the item of Nov 18 left "Already done" (the day held three due dates and a meeting, none named like it), Dec 1 listed one meeting where it had listed five entries (four of them that redefined day's class events), and the other nine answers did not change.

Recordings, step 3, after the first live readings, 2026-11-13 (RECORDINGS "FEWER, MERGED ITEMS", "CHECKED AGAINST FACULTY" › ONLY A MEETING COUNTS; instructor: "Yes" to taking step 3 live, where the two kept meetings were read by the Assistant's real connection in 5.5 and about 6 minutes: 105 and 35 items, the testing center's 14, 1 and 1 found, called-off meetings matched to the instructor's Google entries, one calendar card): `tests/test_recordings_reading.py` (30 tests): `test_a_class_meeting_and_office_hours_are_never_the_meeting_that_was_called_off`, new (with a class meeting and office hours of the term on the day, a called-off meeting lists the one Google meeting and neither of them; with no meeting left that day the item stays with the instructor and the page says the classes were not looked at; a meeting said for 3 pm is still not on the calendar although office hours run then); the cancelled-meeting test now reads "Nothing to remove: no meeting of this course is on your calendar"; the Canvas check says the two closest names when none holds every word and claims nothing, and `canvas_names` answers the modules, pages and assignments a reading is told ({} without a copy, as the first test now sees among the facts); the run's version is `reading-v2`. NOT CHECKED: instructions v2 (`grading/prompts/recording_reading-v2.txt`) have not been read by a model; whether they bring the count down is seen only on a Read again of the two meetings.

Recordings, step 3: the reading of coordinator and instructor meetings, 2026-11-13 (RECORDINGS "The reading (step 3)", "What an item may become", "WHAT CAME OUT OF IT"; ASSISTANT-MEMORY "SAID IN A RECORDING"; GRADING "RULINGS SAID IN A RECORDING"; CALENDAR "SAID IN A RECORDED MEETING"; ASSISTANT "THE TIERS"; instructor: "build step 3"): `tests/test_recordings_reading.py`, new (29 tests, in `manage_checks.py quick`), against `tests/fake_plaud.py` and a SCRIPTED engine in the Assistant's place (`Engine`: the reading's own `connection` seam and `frq.cloud_provider`, the one seam to any vendor, so no check asks a model), with a thirteen-line meeting of invented people. `ReadingTests`: a meeting filed under the term is read in the round that keeps it, by ONE request holding the instructions by name, the schema, the facts (the recording's day, the instructor's speaker found from the workspace's name, the term's sections, the course's calendar entries, Exam 3 under a short name with its given date) and the transcript's numbered lines, and never Plaud's summary; of twelve answered items ten are kept, each with who spoke and when taken from the transcript (a quote running into the next line of the same voice included), and two are recorded as dropped with the reason (words never said; talk before the meeting began); the run keeps the checksums of the instructions and of what was sent; a later round asks nothing again; no student's appointment and no other Google entry is among the facts; a recording filed under a student is not read and says so, and filed under the course it is; Read again is a new run made by the worker without a round (asked once for a repeated request ID), an item keeps its identity by its quote so what was dismissed stays dismissed under new wording, and an item the new reading lacks is gone; a transcript Plaud changed is read again by itself; an Assistant that is off makes no request and no run and says so, an engine that does not answer is tried after ten minutes, an hour, six hours and a day and after five tries only on Read again; an answer that is no reading is a `failed` run and one wrapped in a sentence and a fence is read; a recording with no transcript cannot be asked for (409); runs, items and what one run said of an item refuse UPDATE and DELETE; the request is `durable` false with no time limit for a hosted model; a quote must BEGIN in the line it names (one wholly in a later line, one said after its line, one not word for word and one joined from two places are dropped); a changed transcript that cannot be read (the Assistant off) keeps showing the earlier reading, says why, never says it is being read, and is read on the instructor's Read again; a Read again asked for while a reading is being made is made next; a reading that breaks inside Faculty is said on the page and tried again less often, never every round. `FoundTests` (CHECKED AGAINST FACULTY): on the real catalog with nothing in it each check answers plainly from the owning tables; on tables made from the catalog's own definitions and filled by the test, seating charts read as done only when every classroom chart was built after the last recorded drop (a testing center chart is not one), the testing center's bookings are a number by day (14, 1 and 1), the Canvas copy says whether a module of that name is published, the gradebook says which lowest scores it drops; a meeting called off lists the instructor's Google entry of that day, proposes nothing, leaves Google's rows as they were and reads as done once they removed it; meetings called off from now on list the rest of the meeting's own series. `BecomesTests`: a date the calendar lacks becomes ONE waiting Approve card (the Calendar's own create route, engine `recordings`, its page the recording at that moment), nothing is on the calendar until Approve, Read again makes no second card, and after Approve the item reads as on the calendar; a date of something a record owns is shown against the calendar and never offered; a declined card is proposed again only by the instructor's press (once for a request ID; 409 while one waits or nothing is missing); filed elsewhere, the waiting card is taken back, the recording is read again where it is filed and proposed again there, and filed back it is shown the reading it had; Remember keeps a `policy` as a memory with source `recording`, the instructor's edited words, the term's scope and the item's `remembered` event, never by itself, once (409 again, and 409 after Open again while that memory stands), only for a standing rule (400), refused for words naming a student on the roster (400, nothing kept, the item still open), and kept anew for the course once the instructor removed the first; the Assistant's tiers (`recordings.remember` never; item, read, card and coordinator Approve cards with request IDs); a ruling is listed for its graded assignment with its quote, speaker, time and link, with the "not final" words, accepted as an event that changes nothing else, refused for an item that is no ruling, and on a certified plan nothing is offered and a choice is refused (409); a term's coordinator is a name with revisions that a later reading is told; the list counts what each recording needs of the instructor and the count falls to zero as they settle. `tests/test_catalog_migrations.py` (10 tests): `test_the_memory_tables_rebuild_carries_every_revision_over_and_keeps_its_rules` (assistant 015 on a catalog built from 010 to 014 that already holds memories, an erased revision, a use and a draft reference: every row, the column order and the view read the same before and after, foreign keys are on and hold, `recording` is accepted, another source, a skipped revision number, UPDATE, DELETE and a use of a missing revision are refused, Erase still works), and the adoption test now expects `recordings/003_readings` and `assistant/015_memory_said_in_a_recording` applied as new. `tests/test_recordings.py` (two exact attention counts now carry `for_you`) and `tests/test_assistant.py` (the Assistant's migrations by name). Node: `tests/test_recording_items.mjs`, new (6: an item's sentence, quote, play control and what Faculty found, escaped; its controls by where it stands; a waiting card drawn by the Assistant's own card; what needs the instructor first and alone in orange with done and dropped folded; the reading's one line; when the page asks again), `tests/test_grading_recorded_rulings.mjs`, new (4: the rubric lines a ruling is offered on, a ruling's row, the window's section open while one waits and absent when nothing was said, the "not final" warning), `tests/test_recordings.mjs` (the row's orange count and the header). Browser, in the real shell: `tests/browser_recording_reading.py`, new (`manage_checks.py browser --browser recording_reading`: the list's "6 for you"; the page opens on Needs you 6 with the transcript folded and the only orange on that count and the waiting card; an item plays its quote from the kept Ogg audio; Done folds it away and Open again brings it back; Remember keeps the edited words with source `recording`; Approve on the card makes the calendar entry and the item reads as on the calendar; the coordinator is named; Read again waits, is made by the worker and shown by the quiet refresh with what was answered still answered; a moment in the address and a find unfold the transcript; Plaud was only ever read), and `tests/browser_grading_rubric_quality.py`, extended with route-mocked answers of `/api/recordings/rulings` (the "not final" warning at the top of Upload rubrics; in the finalization window a waiting ruling with its quote and link, refused without a line chosen, added to Version A Item 2's clarification field and recorded as accepted for that plan, the window closed without confirming and the next one as it was). Also run and passing: `browser --browser recordings --browser recording_page --browser grading_gradescope_rubrics --browser assistant_memory --browser home --browser smoke --browser theme`, and `manage_checks.py quick`. NOT CHECKED: no model read anything (the plan's acceptance against the by-hand dry run of the 2026-11-11 meeting waits for the first live readings); the Assistant's real connection was never asked by a check, so its answer's shape and length for a 71-minute transcript were untried at landing. BEFORE LANDING, on a read-only snapshot of the catalog (sqlite's backup API from a `mode=ro` source, on NVMe scratch, deleted afterwards; `/mnt/nvme/Teaching/tmp/recordings-step1/dryrun_015.py`, counts only): `assistant/015` applied in 0.03 s and the 119 revisions of 84 memories, the column order, the view, the 245 uses and the 20,121 draft references read identically before and after, foreign keys on and none broken, the three triggers and the index back, no table left over; `recordings/003` applied; and the checks' own code, read-only on the same snapshot, answered for the current term's Exam 3 as the by-hand dry run did (testing center 14, 1 and 1 by day; the Exam 3 module published in the Canvas copy; no drops set), with the seating charts now older than a drop recorded since.

Recordings, step 2: the recording page, 2026-11-13 (RECORDINGS "ONE RECORDING'S PAGE", "SEARCH OF WHAT WAS SAID", "THE LINES OF A KEPT TRANSCRIPT", SPEAKERS, ASSISTANT "THE TIERS"; instructor: "build step 2"): `tests/test_recordings_page.py` (17 tests, in `manage_checks.py quick`), against `tests/fake_plaud.py` with transcripts in the shape of Plaud's own (`start_time`, `end_time`, `speaker`, `original_speaker`, `content`): `TranscriptTests` (a transcript kept by a fetch is read into three lines in the same transaction and `GET /api/recordings/<id>` answers them with how far in, who spoke and the words, the speakers, the audio's type and size, and Plaud's summary and outline read from the kept files; `start_ms`/`end_ms` answer only the lines of that stretch and no notes; a bad stretch 400, an unknown recording 404; lines and read transcripts refuse UPDATE and DELETE; opening asks Plaud nothing; a recording with nothing kept and a transcript not in Plaud's shape each say so, the odd file kept; the shapes alone: blank and timeless segments left out, five things that are not a transcript unreadable, a search's words, a literal `%`, `_` and `\`, how far in as a clock; a transcript kept before its lines were read is "waiting", is not found, and is read by the next round from storage with only the list asked of Plaud, and is read even in a round that stops at the sign-in), `AudioTests` (the whole kept file with `audio/ogg`, no-store, nosniff, `inline` and Accept-Ranges and no sandbox; `bytes=0-99`, `bytes=1000-` and `bytes=-10` each 206 with their Content-Range; a range past the end 416; POST 405; an unknown recording and one with no audio kept 404; a kept file changed on disk 503, never served; `recordings.audio` in `GET_BLOCKED` and `FILE_BLOCKED`), `SearchTests` (every word in one line, whatever its case, newest recording first, each result with the recording, day, time, place, how far in, speaker, the line before and after and `#recordings/<id>?t=<ms>`; two words in different lines find nothing; `100%` finds its line while `1%0` and `BLOC_166` find nothing; one recording alone; a limit of 1 says there are more; six bad requests 400; every table's row count and the fake's request log unchanged by all of it; a recording that is Not Faculty's is not searched; `recordings.search` is `read`, and 403 from another origin; after Plaud changes a transcript the old word is gone, the new one found and the earlier file's lines still there; marked Not Faculty's the recording is searched no more and still opens), `SpeakerTests` (a name is a revision `instructor` with the request ID and `workspace`, what is kept carried forward, shown on the lines and in search; the same request again and the same name under a new request write nothing; another recording's request ID 409, an unknown speaker and 101 characters 400, no request ID 400, another origin 403, an unknown recording 404; a name is one recording's; when Plaud names the voice itself the name given here stays, and taken back Plaud's shows; the fake saw only GETs; `recordings.speakers` and `recordings.file` are `approve` with the request ID Faculty puts there; a recording filed under a student opens and is found with no student in the address), `AssistantTests` (through `faculty_write` and `faculty_get` with no model: the search is `done` at once with the speaker, the time and the words; a stretch is read; the audio is neither read nor fetched; naming a speaker and filing each wait for approval with nothing changed; the guide names the search and the page's address) and `StoragePageTests` (the transcript and audio tests again on the verified storage engine, the bundle archived to the HDD by the nightly backup first). `tests/test_catalog_migrations.py` adopts `recordings/002_transcript_lines` as new. `tests/test_recording_page.mjs` and `tests/test_recordings.mjs` (14 tests: a line's time is a button with its milliseconds, the speaker printed where the voice changes, the line being said found by its start, a find's words marked in escaped text, the speakers' controls, Plaud's notes folded and shown as text, what a search found with its link to the moment, a kept recording's name a link and an unkept one's not). `tests/browser_recording_page.py` (the real shell at 1440 x 900 with real Ogg Opus, `tests/fixtures/recordings/tone.ogg`, nine seconds of a synthetic tone served by the fake and kept by a round: the name on the list opens `#recordings/<id>`; pressing a line's time plays the kept file from 3.2 s, asked for in ranges (a 206), and the marked line moves on with what is said; no orange; a poll leaves the player the same element; Find marks two words and Enter goes there; a speaker is named in the dialog and the lines say so with the player's element and position unchanged and only GETs at the fake; Plaud's summary is folded, opens as text, and its topic plays from 6.1 s; back on the list the audio element is gone; "makeup room" finds one line, which opens the recording at `?t=6100` with that line marked and the audio set there, not playing; leaving for Home pauses what was playing; an unknown recording says so), with `--browser recordings --browser shell_routes --browser navigation --browser theme --browser home --browser smoke`, `test_assistant`, `test_design_language`, `test_web_assets`, `test_app`, both page modules type-checked under strict, and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch, against the fakes in `tests/`. Read-only outside Faculty, structure only and none of their words: the keys, types and counts of the two transcripts step 1 kept on the live machine (619 and 646 segments of `content`, `start_time`, `end_time` in milliseconds, `speaker`, `original_speaker`, `segment_uid`; outlines of `start_time`, `end_time`, `topic`) and their audio's codec (Ogg Opus, mono). NOT BUILT: a second copy of the audio in another format; the page says so where a browser cannot play Ogg. Restart `faculty` and `faculty-inbox` after the change: the first round then reads the two kept transcripts into lines.

Recordings, step 1: Intake, 2026-11-12 (RECORDINGS, binding from this change; instructor: "I have a plaud note pro, but I want to build an integration for it in the faculty app. I frequently record meetings with the coordinator, other faculty, students, etc and it would be nice to have faculty be able to use the recordings/transcriptions"; asked, they chose that Faculty keeps its own copy of the audio and that their own classes are included): `tests/fake_plaud.py` is a loopback stand-in of Plaud on two ports (the developer API: a renewal that answers a new access token and a new refresh token and refuses any older one, the list, one recording with a freshly signed audio link; and the content host, which takes a signature and no token), recording every request. `tests/test_recordings.py` (48 tests, in `manage_checks.py quick`): `RenewalTests` (a due renewal is the first request, the only POST, and both tokens are in the 0600 file before any read, which carries the new one; no token is in the catalog; a refused renewal leaves the file as it was, says "Sign in to Plaud again", counts in the attention, and three later rounds ask Plaud nothing until the instructor's new sign-in changes the file; a 503 says nothing about the sign-in and the next round renews; a 403, 404, 429 and 500 each wait and send the token in the file again, and only a 400 or 401 is a refusal; a 401 to a read straight after an accepted renewal waits with the renewed tokens kept; a 401 on a token that was not due renews once and asks again; a token file others can read is not used; two renewers at once renew once), `ReadOnlyTests` (a redirect is not followed; the module has one `method="POST"` and no other verb; outside the tests only `platform.plaud.ai` and the content host over https are asked, eight other links refused; `FACULTY_PLAUD_URL` anywhere but loopback is refused before anything is asked; Check now only marks a round wanted; the real Inbox worker loop starts the `recordings-capture` thread and files a recording), `CaptureTests` (a new recording is filed by the calendar and fetched within one round, the audio link of that round's answer asked for next and once, each kept file read back against its checksum, the start kept as the instant and shown on the campus clock; a failed download keeps the transcript and the next round asks Plaud again and uses the NEW link, never the lapsed one; a recording that needs filing or is Not Faculty's is never fetched over three rounds, nor after being marked Not Faculty's by hand, and is fetched in the round after it is filed as Faculty's by hand; a recording Plaud has not transcribed is asked about again after ten minutes, a transcript Plaud changed is a new revision and the audio is not fetched twice; the first round, the first of each campus day and Check now list thirty days and the others three, with the list request's `start_date` read back, a recording synced ten days late found, one that reached Plaud five days late found by Check now and not by an ordinary round, the line saying "since Mon, Oct 12", and `since()` reaching a day before a success forty days old; a kept recording is read again just after 1, 6, 24 and 72 hours and not just before each, then never over thirty more days (five reads in all, the audio once); Plaud failing on such a read leaves it kept, asks again ten minutes later and uses up no read, and Plaud no longer having it ends the reads with what was kept; one fetched and then marked Not Faculty's keeps its three files, says so, and is asked about no more; a temporary file that cannot be written (ENOSPC on write, and on creation) ends the round "storage is not ready" with no traceback and nothing blamed on Plaud for the recording, `download` raising `WriteFailed` and never a `PlaudError`; a block given only by link is read from the content host without the token; a renamed recording is a new revision and one with no start waits; every table refuses UPDATE and DELETE), `FilingTests` (each filing: a student's appointment with eight minutes of grace and not fifteen, not a cancelled one, the appointment by reference and none of its words; a class meeting of a Term schedule in use and a per-date class event, with no enrollment link; an entry naming the term by its title or by belonging to it, never an all-day one; Not Faculty's for another course and for an errand, each link's basis holding the entry's id, source, date and times and no title; while Google's calendar is unread or its copy unreadable both wait and say so, an "Unavailable" and an "Out of office" entry file nothing, and once Google vouches the first two are Not Faculty's and the away ones still wait; filing by hand under the student a recording is already filed under writes no link and keeps the appointment; two students' appointments, office hours with nobody booked and no entry each wait and say why, with Google's unread calendar said, and an entry that arrives later files it; by hand a student, a term, a course, a class day, Memo and Not Faculty's, each withdrawing the last, a repeated request ID changing nothing, its ID on another recording 409, no ID 400, an unknown recording 404, a cross-origin write 403, and the calendar never undoing a filing by hand) and `StorageTests` (every capture test again on the verified storage engine; the bundle is `staged` on NVMe as `recordings/<id>/data/{audio.ogg,notes.json,transcript.json}`, `archived` to the HDD by the nightly backup with the NVMe copy gone, read back checked, and a file changed on the HDD refused; a drive that is away pauses the fetch without blaming Plaud; an interrupted round leaves no record and its folder is swept once an hour old). `tests/test_catalog_migrations.py` adopts `recordings/001_recordings` as new. `tests/test_recordings.mjs` (the page's views: a filed recording named by what it is filed under and never by Plaud's title, a waiting one by its day with the reason and Plaud's title escaped, what is kept in a few words, a recording fetched and then marked Not Faculty's saying what it kept, the list's order with Not Faculty's folded under what Faculty holds of each and never "never fetched", the header's and the status line's words). `tests/browser_recordings.py` (the real shell at 1440 x 900 after one round against the fake: the orange is the header's chip and the Needs filing count alone, the first group above y = 450, a quiet poll keeps the row's element, File opens one dialog and the row moves without a redraw, the page says "Checking Plaud now…" until the worker has run, Check now asks Plaud nothing from the page, the folded group was never fetched, a kept recording marked Not Faculty's joins it saying what it kept, the palette opens the page; run five times after a race in the check itself was found, the closed dialog still holding the form of the filing before) and `tests/browser_shell_routes.py` (44 routes with `#recordings`: rail, header band, no unframed small capitals), with `--browser navigation --browser theme --browser home --browser smoke`, the frontend type check, and `manage_checks.py quick`, all through `manage_checks.environment` on isolated NVMe scratch, against the fakes in `tests/`. On an isolated copy (`DATA_DIR` on NVMe scratch, no storage configuration) the real `manage_inbox.py worker` process, pointed at the fake, renewed the sign-in, listed two recordings, filed the one inside a "MATH 140 instructors meeting" entry under the term and kept its transcript, notes and audio within two seconds, left the other in Needs filing unfetched, and wrote no token to its log. A review before landing (2026-11-12, nine findings sent on the instructor's "Yes, send them") is in the above: eight fixed with a test each, and one, that Plaud's `start_at` may be an upload time for imported audio, named as a known limit after its CLI's source showed no field that tells the two apart. The migration is unchanged. NOT READ ON REAL PLAUD THROUGH THIS CODE: no check reaches Plaud or a real recording, the live token file was not touched, and the live catalog was not opened (read-only, outside Faculty: that neither live drive has a `recordings` folder yet). Left for the live machine on the instructor's word: the two `plaud.conf` drop-ins, the sign-in moved to `~/.config/faculty/plaud-tokens.json` (0600) before it lapses on 2026-11-19 20:36 UTC, then restart `faculty` and `faculty-inbox` once the idle check passes and look at the first live rounds.

A card you send always offers two days, 2026-11-12 (ABSENCES "MAKEUP BOOKING" › A MISSED MAKEUP › ALWAYS TWO DAYS; instructor, of a student's fifth Quiz 5 card, made at 11:31 am three days before her due date, that offered only that afternoon and went unanswered: "why did it only send her same day times?", then "Yes, build that"): `test_makeup_booking` (`test_a_card_you_send_offers_two_days_even_when_only_one_is_left_before_the_due_date`: three days before the due date with only Thursday's meeting hours left, the card offers Thursday and the Tuesday after the due date and the due date moves to that Tuesday, on the row too; with nothing more in the days it looks ahead the one day is still offered and the due date stands; 38 tests), with `test_makeup_slots`, `test_absence_makeups`, `test_absences` and `test_department_makeups`, through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. Only `MakeupBooking.new_card` changed: the worker's first card, the dry run and a follow-up's times keep to the due date. No migration. Restarted faculty.service and faculty-inbox.service (idle gate first). Read-only on live: the row in question (Quiz 5, section 507) had one day with a time before its due date when its card was made (Thursday's meeting hours; Friday's office hours under an "Unavailable" entry; Monday one day past the due date); no card was sent by this change.

Totals by student as a table with a bar of one block a day, 2026-11-12 (ABSENCES "TOTAL ABSENCES" › ON THE ABSENCES PAGE › A TABLE, NOT SENTENCES, DESIGN-LANGUAGE §1.6, §1.7 and §1.13; instructor, of 340 lines each reading "12 absences" and "12 not excused": "this page is also unruly and not user friendly, it's just a wall of text"): `tests/test_absence_totals.mjs`, new (`static/absence-totals.js`, the view's markup moved out of `absences.js` unchanged in what it reads: the column headings once a section, hidden from a screen reader; a row's name as text, count, bar and figures, each figure with its words for a screen reader; `barMarkup`: one block a day, not excused then waiting then excused, at the page's one scale, never wider than its box, no inline style; the days a row opens to; the students with no absence folded under their section with days recorded ahead, the fold open while Find a student narrows the list; a section of them alone has no table; the Waiting column only while a shown student has a day waiting; the empty states; two terms named; THE TOP FIVE, instructor the same day, "only the top 5 for each section": five rows a section, the others who missed a class in a closed fold above the one of those with none, "3 more students", "3 absences or fewer", no fold for five or fewer, every match a row while searching, the section's line still counting everyone), with `tests/test_absences.mjs`, JavaScript syntax, the frontend unit tests and the frontend typecheck, and `manage_checks.py browser` for `absences` (a section's first row, its count "2 absences", closed then opened to its two days), `theme` and `shell_routes` (7/7 groups), on isolated NVMe scratch against the fakes in `tests/`. No Python, route, read, count or order changed. Read-only on live before and after the restart (idle gate first, faculty.service only): the same 340 students in four sections at 1440 and 1280 px, the page 14,793 px tall before and 2,051 px after (20 rows shown, five a section), a row opened to its days, a section's two folds opened, and a search showing every match with no fold.

The Makeups view on one grid, one framed step a row, 2026-11-12 (ABSENCES "ONE GRID, ONE ROW, ONE STEP", DESIGN-LANGUAGE §1.3 and §1.6; instructor, of each row's buttons stacked three deep in the Makeup column and every group's table at its own column widths: "this page is a bit unruly, I think we can improve the aesthetics and user friendliness at the same time"): `tests/test_absence_makeups.mjs` (the Makeup column has nothing to press; `actionsMarkup`: at most one framed action, Send a new card, Send booking card, Release, or Print PDF while the copy still waits to be printed, the rest text actions under it, Print PDF of a printed copy, Hold… or Change…, Missed makeup… last, and nothing for a row with nothing to press; a booked row's line without "Booked:"; the five shared columns in every group; Open on Exams as a go-link before Schedule makeup…), with JavaScript syntax, the frontend unit tests and the frontend typecheck, and `manage_checks.py browser` for `absences` (the booked line read by its own class), `theme` and `shell_routes` (6/6 groups), on isolated NVMe scratch against the fakes in `tests/`. No Python, route, label, state or count changed. Read-only on live before and after the restart (idle gate first, faculty.service only): the same 14 rows at 1440 px, the page 3441 px tall before and 2616 px after, nothing over an edge at 1280 px.

A request for another time taken up in Follow-up is no longer flagged on the Makeups view, 2026-11-12 (ABSENCES "MAKEUP BOOKING" › BACK TO FOLLOW-UP › TAKEN UP IN FOLLOW-UP; instructor, of a Quiz 8 row still flagged "Needs another time" two hours after they had approved the reply from its follow-up: "i thought we made the needs-another-time replies go to followups and I thought I handled this one in followup already"): `test_makeup_booking` (`test_another_time_taken_up_in_follow_up_is_no_longer_flagged_on_the_makeups_view`: flagged and on Home while its follow-up is Needs my reply; saved Waiting on student, the row unflagged with `followup` and "Waiting on student since <day>", Home's makeups card gone, the follow-up's own line unchanged; To do keeping the day; resolved with nothing answering the request, flagged again), watched failing with `_asked_taken` returning nothing; `tests/test_absence_makeups.mjs` (the chip "In Follow-up", neutral, its line, their words, the due date and Send a new card); with `test_absence_makeups`, `test_absences`, `test_department_makeups`, `test_makeup_drafts`, `test_followup_suggestions`, `test_student_support`, `test_workspace_overview` (290 tests) and `manage_checks.py quick` (4/4 groups), through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. Read-only on live after the restart (idle gate first): GET `/api/absences/makeups` shows the one such row (Quiz 8, section 508) unflagged, "Waiting on student since Thu, Nov 12", and Home's makeups card has nothing to count. No migration, no new route, nothing sent.

A certification graded for participation stands, 2026-11-12 (GRADING "GRADED FOR PARTICIPATION" › THE CERTIFICATION COUNTS THEM TOO; instructor, of the first Take Home Assignment reading "Grades changed since the certification" from the moment it was certified, asking the Assistant "what changed to cause the certification to revert?"): `test_grading_certification` (`test_a_certification_graded_for_participation_stands_until_a_grade_or_the_setting_changes`: current once certified, changed with the setting off, current with it on again, changed by other lines on a grade, current once certified again; `test_the_setting_turned_on_after_certifying_asks_to_certify_again`; `test_one_changed_grade_of_a_plan_graded_for_participation_is_certified_alone`: from 10 to 10, possible, amended, the cell left at 18, and a grade certified before the setting read as stored), all three watched failing before the change; with `test_certification_automatic`, `test_grading_participation`, `test_grading_participation_approval`, `test_regrade_followup`, `test_regrade_mc`, `test_regrade_panel`, `test_regrade_requests`, `test_gradebook_sources`, `test_gradebook_reconciliation`, `test_gradescope_after`, `test_gradescope_publish`, `test_gradescope_send`, `test_grading_intake`, `test_exam_overview`, `test_workspace_overview`, `test_grading_batch_adjustments`, `test_grading_restore_certified`, `test_certified_archive`, `test_grading_auto_grade` (533 tests) and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/` (the worktree has no `.tools` or `.venv`, so the main checkout's were named). Read-only on live (`mode=ro`, no app): the Take Home Assignment's one certification (2026-11-11) recorded the marker of its 1,435 grades as they count (931 lifted by the line), not as stored, so it reads current after the change without being certified again. No migration, no new route, no frontend change. Restart `faculty` and `faculty-inbox` after the change.

An unanswered booking card becomes a 0, 2026-11-12 (ABSENCES "MAKEUP BOOKING" › NO ANSWER, THEN A 0, amending MAKEUPS "MISSED"; instructor: "makeup cards that go to "no answer" status should wait one more day after that status for the student to reach out, if the student doesn't reach out then the assignment gets recorded as a 0 and then makeup card revoked and cleared from the makeup owed page"): `test_makeup_booking` (`test_an_unanswered_card_is_a_0_one_business_day_after_its_last_time_unless_they_write`: the row's label on each day, nothing while Gmail and Canvas are unread since the day ended, the 0 recorded as `makeup_missed` by `automatic`, the row gone, the card withdrawn, the absence window's label, a student who wrote left flagged, and the instructor's Undo never recorded over; `test_a_card_that_can_still_be_answered_or_never_went_out_is_never_a_0`), both watched failing with the step removed; with `test_absence_makeups`, `test_absences`, `test_department_makeups`, `test_absence_totals`, `test_followup_suggestions`, `test_workspace_overview` and `manage_checks.py quick` (JavaScript syntax, frontend unit tests), through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/`. Read-only on live: two rows read No answer (Quiz 7, sections 507 and 508; last time Nov 10, no message since the card), so both become a 0 on the first worker turn after campus midnight ending Nov 11 once Gmail and Canvas are read. No migration, no new route; one sentence of the Makeups turn-on window changed. Restart `faculty` and `faculty-inbox` after the change.

"I need another time" comes back to Follow-up, 2026-11-12 (ABSENCES "MAKEUP BOOKING" › BACK TO FOLLOW-UP, ASSISTANT MAKEUP TIMES › ANOTHER TIME; instructor, of a booking card answer flagged only on the Makeups view: "it should actually land back in followup for me to review, if not, it will just sit in the absence page unnoticed"): `test_makeup_booking` (`test_another_time_comes_back_as_a_follow_up_once_with_their_words`: one follow-up, Needs my reply, with their words, the card's days and the due date; the Follow-up page's line; the preparation queued by itself with the answer, the open times after the card's days and no "point them to it"; no facts for any other follow-up without a message; `test_another_time_answered_outside_its_follow_up_resolves_it`: seated by hand and a new card each resolve it with a line saying which; `test_another_time_follow_up_the_instructor_took_up_is_theirs_to_settle`: an answer recorded before the rule gets its follow-up, and one the instructor saved or whose step they approved is left alone), each watched failing with the app's wiring removed; with `test_absence_makeups`, `test_department_makeups`, `test_makeup_drafts`, `test_absences`, every `test_followup_*` module, `test_dr_requests`, `test_student_support`, `test_support_sweep`, `test_assistant`, `test_workspace_overview` and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch against the fakes in `tests/` (the worktree has no `.tools`, so `FACULTY_TEXLIVE_ROOT` named the main checkout's). No model was asked: what a preparation proposes from these facts is NOT TRIED. Read-only on the live Makeups view: one row reads Needs another time (Quiz 8, section 508), so one follow-up opens on the first worker turn. No migration, no new route, no frontend change. Restart `faculty` and `faculty-inbox` after the change.

The Memory page, 2026-11-10 (ASSISTANT-MEMORY "ONE PAGE", GRADING "RUBRIC MEMORIES"; instructor, of ticking "With every request" in the Assistant's Saved tab: "this is very cumbersome, we need a centralized setting page in the settings for the various memory functionalities across the app"; they chose the Assistant's memories inline, every course's rubric memories, on/off switches and tuning numbers, on its own page, the old places replaced with links): `test_assistant_memory` (`SettingsTests`: the switches on and the numbers measured until set, each refusal of a bad value or a stale version, the routes `never` for the Assistant, keeping from chat switched off asking nothing, a request told as many as allowed at the bar set, learning from corrections following its switch and starting again from when it was turned on), `test_rubric_memory` (learning by itself follows the switch, the instructor's own request does not), `test_catalog_migrations` (014 adopted), `test_assistant`, `test_followup_learning`, `test_followup_suggestions`, `test_assistant_workspace`, `test_assistant_workflows`, `test_grading_certification`, `tests/test_memory_settings.mjs` (a memory as one row with its tick, what it applies to and Remove; its words opened in place; whose words and how given; the filter and what leads; Replaced and removed; the switches and numbers), `tests/test_assistant_memory.mjs` and `tests/test_followup_suggestions.mjs` (the links to a memory's row), the frontend type check, `manage_checks.py quick`, and `manage_checks.py browser --browser assistant_memory --browser rubric_memories --browser followup_learning --browser shell_routes --browser assistant_workspace --browser course_pages --browser theme` (the page from the Saved tab's link; the tick, Applies to, Edit, Remove, Erase and Add on it; the switches and numbers; "reword it" from the drawer and from the floating chat; a course Home's line and the old course route landing on that course's rubric memories; no sideways scrolling at 1280), through `manage_checks.environment` on isolated NVMe scratch. `browser_frontend_lifecycle` fails on untouched main at its Canvas hub step (line 173), before the Saved tab part changed here, which passes with that step left out. Migration `assistant/014_memory_settings` (one workspace row). Restart `faculty` and `faculty-inbox` after the change. Landed 2026-11-12 (instructor: "do that, default limit to 8 sounds fine"): the page had waited unlanded in its session's worktree, was carried onto main at ea069c5a (`tests/browser_shell_routes.py`'s route list and this file merged by hand, the rest cleanly), and every check named here except `browser_frontend_lifecycle` was run again on that tree, with `test_department_makeups`.

The term's rules on a department makeup request card, 2026-11-12 (ABSENCES "DEPARTMENT MAKEUP EXAMS" > THE TERM'S RULES; instructor, with five request cards waiting once Exam 3 was seated: "can we make these default to the usual settings, for a given course/term the first one I fill out should be the default, the only thing that should change is the student info, exam number, and date throughout the semester"): `tests/test_department_makeups.py` (`test_the_rules_last_sent_in_the_term_are_the_next_exam_s_and_documentation_is_the_student_s_own`: after one accepted Exam 2 request an Exam 3 card is ready with the same time, Scantron, calculators and reset, documentation is the student's own and never the last request's, and an exam's own last request comes before the term's; `test_a_receipt_of_the_instructors_own_gives_a_later_exam_of_the_term_its_rules`: a receipt for a student of the term does, one for a UIN outside it does not; `test_a_request_sent_fills_in_the_cards_still_waiting`: two cards wait saying "Choose…", the first is filled out and sent, the other then shows the same rules with nothing left to choose and its Send request sends in one press), through `manage_checks.environment` on isolated NVMe scratch, against the fakes in `tests/`. Read-only on the live catalog (the rules of this term's requests and receipts, and when the five cards were made).

An email carries 25 MB of files, and files too large for one say so in the chat, 2026-11-12 (MAIL-ATTACHMENTS, ASSISTANT "ATTACH IN CHAT" > TOO LARGE FOR AN EMAIL; instructor, of 18.6 MB lecture notes attached for a follow-up's reply: "i uploaded the notes to the agent and asked it to attach them to this followup, but it seems to not have worked", then "land it, raise the attachment limit to 25"): `test_gmail_mail` (`test_the_most_files_a_message_carries_fit_gmails_upload`: the limit is 25,000,000 bytes, and that much with the longest text is 33.8 MB once encoded, inside the upload URI's 35 MB; the refusal over the limit as before), `test_request_limits` (a send carrying files from the computer takes 26 MiB, as a Canvas message does), `test_assistant_uploads` (`test_a_file_too_large_for_an_email_is_said_with_the_message`: the model's note names a file that cannot go by email and the 25 MB an email carries, says so of files too large together, says nothing of what fits, and the paperclip's limit equals Mail's), `node --test tests/test_assistant_attach.mjs tests/test_mail.mjs` (files too large together get one line under the list, a single file over the limit says so beside its size, the 18.6 MB notes say nothing now; the compose window's hint and refusal name 25 MB), with `test_canvas_messages`, `test_assistant`, `test_scheduled_sends`, `test_assistant_memory`, `test_canvas_files`, `test_picture_search`, the frontend type check, `manage_checks.py quick` and `manage_checks.py browser --browser assistant --browser mail_attach --browser mail_more --browser mail_followup` (two 13 MB files attached in the drawer say "Together these are too large for one email (25 MB at most).", nothing overflows, removing one takes the line away), through `manage_checks.environment` on isolated NVMe scratch. Read-only on the live catalog: the three preparations of that follow-up each ended at Mail's refusal ("The attachments come to 18.6 MB; Gmail takes at most 18 MB of files in one message"). NOT TRIED AGAINST GMAIL: whether Google accepts a message carrying more than 18 MB of files is known only from its published 25 MB attachment limit and its 35 MB upload; the first real send over 18 MB settles it, and a refusal sends nothing. Not changed: what a follow-up card shows of a reply that could not be prepared. No migration, no route. Restart `faculty` and `faculty-inbox` after the change; a file attached in the chat before the restart is attached again.

One tile per exam on Seating charts, 2026-11-12 (SEATING "One tile per exam"; instructor, of the list's tile for every room under each exam: "each exam's seating charts should be behind a tile for that exam instead of each tile per room on this page"): `tests/test_seating_state.mjs` (an exam's rooms, sections, sections still without a room and its DR and free-seat counts together; its state from every, some or no room confirmed and Held after its date; several rooms or a waiting section open the exam's page and one complete room its chart; the exam's address read back, and the list's, a chart's and a short address read as none), `test_seating` (`test_an_exams_page_of_rooms_opens_in_its_assignment`: `#seating/exam/<assignment>/<term>` resolves to the same assignment as a chart of that exam, and an exam with no chart, or a short address, to none), `manage_checks.py browser --browser seating_mix` (creating a mix opens the exam's page with a tile for each room; the list holds one tile per exam with its rooms, sections and counts; the tile opens the rooms, a room's chart leads back by "All rooms for Test 1"; an exam with a section still unseated says so on its tile and its page's Add rooms opens the window at that exam; an address for an exam with no chart says so), `--browser seating` (one room with every section seated: the tile is the exam's and opens the chart itself; a failed roster check reads "1 room needs attention" on it), `--browser seating_updates`, `--browser shell_routes` (the exam's page among 43 routes), `--browser theme`, `--browser cross_links`, `--browser assignment_wizard`, `--browser seating_creation`, `--browser seating_lifecycle`, `--browser badges`, the frontend typecheck and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch with synthetic rosters. Looked at once in screenshots of an isolated copy (the list with dated, flagged and archived exams; an exam's page; a chart's back link). No migration, no new route, no change to `GET /api/seating`; `/api/assignments/resolve` answers one more address. Restart `faculty` after the change, once the restart check is idle.

Gemini Decisions matches a first page to its student, 2026-11-12 (JEV "STUDENT MATCH PROVIDER", DOCUMENTS "JEV PAPERS" > GEMINI MATCHES THE STUDENT; instructor: "replace jev with gemini decisions for scan to students"): `tests/test_gemini_decisions.py` (Jev's questions translated for Google's Decisions API under plain keys, pictures as inline blocks, a Box refused unsent; answers back in Jev's shape with Jev's confidence formula and bounds; one POST to the fixed URL with the key in `x-goog-api-key`, a refusal or an uncertain outcome without host text or the key, nothing sent without the key; `paper-student` asks Gemini when its key is set and Jev without it or with `FACULTY_STUDENT_MATCH_PROVIDER=jev`, every other purpose keeps its provider; `identify` through the real `JevService` keeps the model on its receipts, enrollment IDs never names, says `reader`, repeats from receipts, and a Gemini failure decides nothing and never asks Jev; the Google key may be named only by a connection to Google's host), `test_unlabeled_exams` (a first page Gemini matched carries `reader` and its note begins "Gemini"; one Jev matched reads as before), `test_term_print_jobs` (a stamped copy's basis text names Gemini), `test_jev_papers`, `test_jev`, `test_openai_decisions`, `test_antigravity`, `test_grading_cloud`, `test_grading_fixes_providers`, `test_grading_removed_schemes`, `test_decisions_sandbox` and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch with scripted answers only. Measured once outside the checks, on the instructor's word, in the Decisions sandbox (`manage_decisions_sandbox.py students --modes google`, run `run-20261112-students`): `identify` itself replayed on 119 first pages the app had read, 118 named and none wrong, and nobody named on 30 of 30 pages whose own student was taken off the roster. Not exercised in a browser: the row's "Identified by Gemini" line. No migration or route; `GEMINI_API_KEY` comes from the services' `grading.env`. Restart `faculty` and `faculty-inbox` after the change, once the restart check is idle.

Choosing rooms in the New seating chart window, 2026-11-10 (SEATING "Choosing rooms"; instructor, of a mix's 53 ready rooms as one flat list: "I think we can make this room selection menu more user friendly"): `tests/test_seating_state.mjs` (rooms an earlier chart seated lead, the latest chart first, with its exam; a room that cannot be chosen never does; no charts, the old order) and `manage_checks.py browser --browser seating_mix` (a room used for Quiz 3 is the first row and leads each section's "Used before" group; Find a room takes words in any order, keeps a hidden room ticked and named, says when no room matches, and Enter creates nothing; the chosen rooms are totalled; the mix is created as before). Also run: `browser_seating`, `browser_seating_creation`, `browser_assignment_wizard`, `browser_seating_versions`, `test_design_language`.

Who sees a comment, 2026-11-11 (GRADING "WHO SEES A COMMENT"; instructor, of a grade Faculty approved for participation whose comment read "the answer was not looked at": "is this part seen by students?", then "Can we make sure it's clear which comments will be seen by students and which won't"): `tests/test_grading_review_layout.mjs` (the desk's comment reads "Only you see this" inside its label with the field's name unchanged, the Adjust points fold says students see the points and not the reason, nothing else is marked), `tests/test_grading_certification.mjs` (the wording of a line for every grade reads "Students see this on Gradescope" on Add and Edit and not on Remove; "Your comment" in the read-only grade reads "(only you see this)"), `tests/test_regrade_panel.mjs`, the frontend type check, and `manage_checks.py browser --browser grading_review_queue --browser grading_certification --browser regrades` (the desk's two marks in a browser, with a screenshot of the fold), through `manage_checks.environment` on isolated NVMe scratch. Not exercised in a browser: the line dialog's mark and the regrade reply's. Words only: nothing sent to Gradescope changes; no API, migration or route. Restart `faculty` after the change.

A late paper of a plan graded for participation certifies itself, 2026-11-11 (GRADING "THE WAITING GRADES APPROVE THEMSELVES" > A LATE PAPER, "LATE PAPERS CERTIFY THEMSELVES"; instructor, told that such a paper waited for their Certify: "i want late participation-only assignments to be auto certified as well"): `test_certification_automatic` (`test_a_late_paper_of_a_plan_graded_for_participation_certifies_itself_once_its_waiting_grades_are_approved`: a late paper nobody decided waits, Faculty approves its two waiting grades as graded, and the next look certifies it by actor `automatic`; skipped where the plan has a bubble-sheet key; `test_a_grade_is_cleared_unless_still_flagged_for_review` unchanged) and `test_grading_participation_approval` (`test_a_late_paper_settled_this_way_is_cleared_to_certify_itself`: a flagged grade Faculty approved is cleared, one still waiting is not), through `manage_checks.environment` on isolated NVMe scratch. This takes back the morning's rule that such a paper waited. Not exercised: its Gradescope send. No migration, no route. Restart `faculty-inbox` (and `faculty`) after the change.

The waiting grades of a plan graded for participation approve themselves, 2026-11-11 (GRADING "THE WAITING GRADES APPROVE THEMSELVES", PIPELINE; instructor: "I want it to still grade and flag things as normal so I can see that information, but then I want it to approve the responses automatically without fixing the flags since the student will get full credit anyway", and to the five points I read back, "you've understood everything correctly, build it"): `test_grading_participation_approval` (new, on the flag review's fixture against the fake host: a held grade waits while the setting is off and is approved once it is ticked, as an approved review of its own result by actor `automatic` with the result's transcript, ticked lines and score; the result and its flags are as they were; it is final by `final_decisions`, Certify's projection marks it and the "Graded for participation" note counts it; one `participation_approved` receipt; no flag review starts by itself and the offer answers `participation`; no training example, no graders' profile, no review signal, no instructor action or false alarm; nothing is approved while a batch runs or after a cancelled batch; a draft and an answer marked unreadable are left; an answer with no proposed score or whose lines do not add up is left; a late paper settled this way is not cleared), with `test_grading_participation`, `test_certification_automatic`, `test_grading_certification`, `test_grading_auto_flag_review`, `test_grading_auto_grade`, `test_grading_batch_adjustments`, `test_grading_training`, `test_training_capture_gate`, `test_grading_review_queue`, `test_grading_review_student`, `test_grading_review_policy`, `test_grading_review_rules`, `test_grading_review_failed`, `test_grading_flag_adjustments`, `test_grading_batch_resets`, `test_grading_acceptance`, `test_gradescope_send`, `test_pipeline_improvements` (its fair-turns stand-in names the new step), `test_pipeline_per_paper` and `test_grading_pipeline` (385 tests in one run, whose one error was that stand-in; it and the four modules not in that run, 48 tests, in a second), `tests/test_grading_run.mjs` (the offer's wording) and the frontend typecheck, through `manage_checks.environment` on isolated NVMe scratch. Replayed on a backup-API snapshot of the live catalog with the term's roster file (deleted afterwards): of the Take Home Assignment's 1,435 answers 105 waited for review (31 grading, 74 reading and grading); all 105 were approved in 0.2 s, 1,435 were then final with none skipped, a second pass approved nothing, and no flag review was requested. Not exercised: a browser look at the Review step, a late paper certified after an approval. No migration, no route. Restart `faculty` and `faculty-inbox` after the change.

Papers plans no grouping of papers labeled on every page, 2026-11-11 (DOCUMENTS "A STRAY SHEET" > NOTHING TO GROUP; instructor, of a take-home scan whose 90 papers were put together from their labels and whose Papers page offered "Group 74 papers" and held 16 more: "it says i need to group 74 pages"): `test_scan_stray_pages` (`test_a_scratch_page_after_complete_papers_and_a_blank_side_beside_it`: the labels still suggest a side count, and Papers gives no side count, nothing ready and no paper to look at), with `test_sequence`, `test_scan_stacks`, `test_inbox_scan_groups` and `test_pipeline_per_paper` (66 tests), and `manage_checks.py browser --browser scan_stray_pages`, through `manage_checks.environment` on isolated NVMe scratch. Read-only on the live catalog: `_labeled_throughout` is true of the four take-home scans and false of a scan labeled on first pages. The hint over Papers' pages in no paper no longer says they come before any labeled paper. No migration, no route. Restart `faculty` after the change.

The Papers stage of papers labeled on every page shows no grouping count, 2026-11-11 (DOCUMENTS "A STRAY SHEET"; instructor, of a take-home scan whose tile read "Papers 0% · 0/90" while its 90 papers were put together and in grading: "it's showing 90 papers"): `test_scan_stray_pages` (`test_a_scratch_page_after_complete_papers_and_a_blank_side_beside_it`: while stray pages wait on a scan whose papers carry a label on every page, the Papers stage is Needs you with no progress count), with `test_sequence` and `test_scan_stacks` (51 tests), through `manage_checks.environment` on isolated NVMe scratch. Not exercised: the Inbox tile in a browser. No migration, no route. Restart `faculty` after the change.

Empty sides on coloured paper and on the home scanner, 2026-11-11 (DOCUMENTS "GREY PAPER"; instructor, of the Take Home Assignment: "the scan inbox seems to be having issues with this one, likely because I scanned double sided when the assignment was printed as single sided so there are lots of blank pages", "this was actually printed on color paper, that's why it showed up as grey", "proceed with landing"): `test_documents` (`test_an_empty_side_scanned_in_grey_is_blank`: an empty grey side, one with the scanner's edge shadow and one with the front showing through are blank, a few written characters in pen or light pencil are not, white paper is judged as ever, and through `extract_scan` a blank grey side is read for a code once while a written one gets the second and faint looks; `test_empty_sides_of_a_scan_inspected_before_grey_paper_become_blank_sides`, in both the fallback and managed-storage classes: refused while the scan is being inspected, the dry run changes nothing, the write makes the empty back a blank side with one `blank_sides_read` event and leaves the labeled front, the written back and a side with another code as they were). Both fail with the blank test as it was. With `test_document_corners`, `test_scan_stacks`, `test_sequence`, `test_scan_stray_pages`, `test_unlabeled_exams`, `test_bubble_sheets`, `test_inbox_scan_groups`, `test_scan_separation`, `test_grading_intake`, `test_pipeline_per_paper`, `test_alias_ocr`, `test_assignment_finder`, `test_inbox` and `test_pipeline_improvements`, through `manage_checks.environment` on isolated NVMe scratch; the sandboxed `extract` reads a grey empty side as blank and a grey written one as not. Replayed read-only on every page picture kept that day (18,322; 140 more had no picture on NVMe): the white test names the same 293 blank sides; none of the 6,381 grey pages with a label or an identity is blank (least ink .0042 of the side, the cut .0001); of the four take-home scans' 864 sides without a label 763 are blank and 101 are left (3 one paper whose codes did not read, the rest backs with writing, ink soaked through or the front showing through). Inspecting 20 real pages took 13.6 s against 14.5 s before, so this does not make inspection faster. No migration, no route. Restart `faculty` and `faculty-inbox` after the change; the four scans inspected before it are measured again with `manage_inbox.py blank-sides MESSAGE_ID` (a dry run, then `--write`).

Separating a scan by assignment, 2026-11-11 (INBOX "SEPARATING A SCAN", PIPELINE scans, DOCUMENTS; instructor, of a stack of Quiz 7 and Quiz 6 makeups held at "The labels name more than one assignment": "faculty needs the ability to separate scans into its various assignments (if there are more than one)", then of a stack of DR copies with no labels held at "its first pages point to more than one assignment": "can you use it on the scans that got held up in the inbox"): `test_scan_separation` (11 tests. By labels: a scan of two assignments reads Working with what Faculty is doing, becomes one new scan for each with the same labels read from the same page content, each filed by its labels and through its review by itself with its papers assembled, the old scan under Deleted by actor `automatic` with its reason and never separated twice when restored; pages without a label stay with the labeled page before them; a bubble sheet follows its Scantron label and one without a label between two assignments is not guessed; a scan with a page set aside by hand is left with its reason and separates once started over; labels still being read are waited for; a part that cannot be made changes nothing and is said once; an interrupted separation finishes from its recorded plan. By printed titles: a scan without labels is read `READ` pages a turn, only a first page's title counts, the parts are filed under their assignments by the ordinary automatic association with their first-page search queued at each assignment's sides, and the finder has nothing left to decide; pages that do not add up to whole papers, titles naming one assignment, a busy reader and a reader that cannot read change nothing), `test_pipeline_per_paper` (the two-assignment hold now reads Working and still blocks the review), with `test_inbox`, `test_inbox_scan_groups`, `test_sequence`, `test_scan_stacks`, `test_scan_stray_pages`, `test_grading_intake`, `test_pipeline_improvements`, `test_documents`, `test_alias_ocr`, `test_assignment_finder` and `test_unlabeled_exams`, and `manage_checks.py browser --browser scan_separation` (new: the Working tile, one scan per assignment named by its file, the deleted scan's reason and Restore; no overflow at 1440 and 1280), through `manage_checks.environment` on isolated NVMe scratch. Replayed on isolated snapshots of the live catalog with only each scan's own files copied (deleted afterwards): the 12-page copier scan became "… (Quiz 7).pdf" (10 pages, 5 papers) and "… (Quiz 6).pdf" (2 pages, 1 paper), every page reading the same label with the same page content, both filed and reviewed by themselves; the 20-page scan without labels, read with the local reader, had first pages on 1, 5, 9 (Quiz 7) and 13, 17 (Quiz 6) and no title on any other page (before the first-page rule, sheet 2 of every paper read as Take Home Assignment by its "Free Response" heading and nothing was separated), and became 12 and 8 pages filed under Quiz 7 and Quiz 6 with their first-page searches queued at 4 sides. No migration, no route. Restart `faculty` and `faculty-inbox` after the change.

DR copy page codes, 2026-11-10 (GRADING "DR COPIES" > STAMPS, QUIZZES "ACCESSIBLE COPY" > PAGE CODES, DOCUMENTS, SCHEDULING "SEND TO AIM"; instructor: "Let us start adding the data matrix stamps to the accessible versions that get uploaded to AIM (note that they shouldn't be student specific because the DR center is likely to give them to the wrong students)"): `test_dr_copies` (`test_the_word_file_prints_page_codes_that_are_no_students_and_its_papers_are_named_from_the_paper`: the file handed out has one decorative picture per page anchored at two corners and differs from the bare copy in nothing else, comes back the same every time, and reads as copy 1's tokens; its stamped document, laid out as the 10 mm corner marks, and its job of 20 unnamed copies for the term, ready, a registration; two papers back on copy 1's codes: the second takes copy 2, each is named from its own paper with both corners whited out, the scan is filed by its labels, no step waits for the unused copies, and both papers are Version A's with whole-sheet areas; a print job of the copy is refused; a paper without codes still gets the copy without page codes. `test_a_file_whose_codes_cannot_be_checked_goes_out_as_it_always_did`: the bare file with a logged warning; the check passes the real file and refuses codes on the wrong pages), with `test_accessible_quiz`, `test_aim_send`, `test_dr_requests`, `test_dr_quizzes`, `test_documents`, `test_print_origins`, `test_exam_overview`, `test_term_print_jobs`, `test_grading_intake`, `test_unlabeled_exams`, `test_assignment_workspace`, `test_exam_workflow`, `test_grading_exam_versions`, `test_printer_send`, `test_sequence`, `test_scan_stacks`, `test_pipeline_improvements`, `test_grading_deskew`, `test_frq_providers` and `test_quiz_drafts` (445 tests), through `manage_checks.environment` on isolated NVMe scratch. One of them failed once and passes alone three times out of three: `test_documents.MediaContractTests.test_a_page_with_no_mark_at_300_dpi_is_read_once_more_at_250` draws a 7 mm mark of a random token, which now and then does not read at 250 DPI; `render_barcode` gives the same bytes as on main for the same token. Replayed on a backup-API snapshot of the live catalog in NVMe scratch, deleted afterwards: Quiz 7's stamped file was made in 9.8 s (document, job of 20 ready, check passed) and came back byte for byte in 0.1 s. Not exercised: Word opening and printing the stamped file (no Word on ml-box), a real scan of a stamped print. The full suite was not run again after this change. No migration. Restart after the change (faculty, and faculty-inbox for the worker).

DR copies, 2026-11-10 (GRADING "DR COPIES", DOCUMENTS "EXAMS PRINTED WITHOUT LABELS" › DR COPIES, QUIZZES "ACCESSIBLE COPY"; instructor: "I just scanned the DR quizzes, they likely need their own template in the assignment/grading pipeline to be graded correctly, but they can still use the same rubrics"): `test_dr_copies` (the copy's document, blank and sides, made once; two papers without labels registered under it and never a "Stamps only" version; each counts as Version A's by its document, alone and in a read over the exam; problem k is the whole of sheet k with sheet 1's name band masked, the version's own areas untouched; another version's question and the manual scores page refuse the paper; a paper with another number of sheets and questions not one to a sheet are refused; an assignment not drafted in Faculty has none; the worker makes the copy from an AIM upload receipt, once; a sheet is cut whole whatever its blank says and turned upright; the rendered copy loses only its page count), `test_pipeline_improvements` (the new step takes its turn), with `test_unlabeled_exams`, `test_grading_intake`, `test_grading_exam_versions`, `test_exam_workflow`, `test_gradebook_sources`, `test_grading_certification`, `test_accessible_quiz`, `test_grading_crops`, `test_grading_deskew`, `test_grading_templates`, `test_assignment_finder`, `test_grading_locations`, `test_auto_prepare` and `test_quiz_drafts`, through `manage_checks.environment` on isolated NVMe scratch (pandoc and the LibreOffice runtime on NVMe are used; no outside service). Replayed on a backup-API snapshot of the live catalog in NVMe scratch, deleted afterwards: the worker step made Quiz 6's and Quiz 7's copies (Version A, 4 sides each), each Word file's sha256 equal to its AIM upload receipt's. Measured on the first real scan, read-only: see GRADING "DR COPIES" › ITS OWN BLANK. `manage_checks.py full` ran 4,550 tests: 24 failures in six grading modules had one cause (their stand-ins for the crop step take no `sheet` argument; it is now passed only for a DR copy) and those modules pass since; the two left are not this change's (`test_scheduling_clock.test_one_campus_zone`: `static/scheduled-sends.js` names the campus zone since 0e1f951a; `test_template_conversion`'s LaTeX toolchain discovery needs `.tools/`, absent in a worktree). Not exercised against real services: the first-page reader and Jev on the real DR pages, and a real graded response. No migration. Restart after the change (faculty, and faculty-inbox for the worker).

Flaws found in the other memory systems, 2026-11-09 (PAST-WORK "Every result says how it ended"; ASSISTANT-MEMORY KEEPING "A whole name", LEARNED FROM AN EDIT, PICTURES, A FOLLOW-UP ASKS WITH THE STUDENT'S LAST MESSAGE; GRADING "RUBRIC MEMORIES"; instructor, of a review's table of them: "fix all of them"): `test_assistant_past_work` (`test_a_card_that_gave_way_to_another_is_not_one_the_instructor_declined`), `test_assistant_memory` (`test_a_name_is_found_without_its_accent_in_capitals_and_inside_an_address`, `test_a_students_message_never_goes_out_when_the_roster_cannot_be_read`, `test_a_replacement_takes_the_pictures_of_what_it_replaces_unless_it_brings_its_own`, a limit of term the writer adds refused), `test_rubric_memory` (`test_a_memory_still_refused_on_the_second_answer_is_left_out_and_the_rest_is_written`, `test_a_memory_dropped_while_a_pass_reads_is_not_revised_back`, `test_a_kept_retirement_is_listed_and_dropping_it_brings_the_memory_back`, `test_a_pass_reads_without_the_names_of_the_plans_own_term`, `test_a_resolution_reads_the_memories_as_they_stand_and_not_as_its_check_froze_them`, rows of a matrix read as no line ID), `test_followup_learning`, `test_followup_suggestions`, `test_grading_rubric_quality`, `test_student_notes`, `manage_checks.py quick`, the frontend type check, and `manage_checks.py browser --browser rubric_memories --browser followup_learning --browser assistant_memory --browser followup_suggestions` (the Retired fold and Bring back; `followup_learning`, which failed 6 of 8 runs before the listing asked `busy()` first, passed 6 of 6 after), through `manage_checks.environment` on isolated NVMe scratch. The name reader replayed on the live roster, counts only: 340 of 340 names still wholly taken out as "First Last" and "Last, First"; both halves of a hyphened family name written apart 335 before and 340 now; a given name in capitals 0 before and 338 now; `roster_check` answers the same for all 101 kept memories and invented requests; of 70 follow-up messages as a preparation masks them, 0 still hold an address (30 had one). No migration. Restart `faculty` and `faculty-inbox` after the change (rubric passes run in the Inbox worker).

Memories judged against the request, 2026-11-09 (ASSISTANT-MEMORY "WHAT A REQUEST IS TOLD", JEV "MEMORY"; instructor, of a chat about category weights given 39 memories about absences and regrades: "let's do your suggestion and have relevance checks"): `test_assistant_memory` (`test_every_memory_is_judged_against_the_request_and_only_what_bears_on_it_is_told`: every unmarked memory judged, eight to a request, read by the two messages typed before it, the marked one told unjudged, a use receipt, rule `-v2`, the nearest few when Jev does not answer; `test_at_most_a_few_are_told_the_most_useful_first_and_only_a_plain_countermand_sets_one_aside`; `test_the_mark_is_the_instructors_alone_and_a_replacement_keeps_it`; the budget of the marked ones; a follow-up's lookup past `JUDGED`), `test_followup_learning`, `test_followup_suggestions`, `test_followup_redo`, `test_assistant`, `test_assistant_workflows`, `test_assistant_tool_guidance`, `test_assistant_past_work`, `test_catalog_migrations` (013 adopted), `tests/test_assistant_memory.mjs` (the tick, its row hint, "with every request" under an answer), the frontend type check, `manage_checks.py quick`, and `manage_checks.py browser --browser assistant_memory --browser followup_suggestions --browser assistant_workspace`, through `manage_checks.environment` on isolated NVMe scratch. `browser_followup_learning` fails as often on untouched main (its toast waits on a page read that can miss `remembering`); not changed here. Measured read-only on the live catalog with Jev asked directly and no receipt (scratch `/mnt/nvme/Teaching/tmp/memory-relevance-replay`; the numbers are in the contract). Migration `assistant/013_memory_always` (one column, the Erase trigger remade to hold it). Restart after the change.

No makeup hold for a known return, 2026-11-09 (ABSENCES MAKEUP BOOKING HOLDS › A KNOWN RETURN, ASSISTANT DONE FOR YOU "MAKEUP HOLD"; instructor, of Jason Clark's Quiz 7 card held by a follow-up until his return and never sent: chose "Don't hold; send card at excusal"): `test_makeup_booking` (`test_the_assistant_never_holds_for_a_known_return_and_its_card_goes_out_at_excusal`: the assistant's hold refused 409 for a return the details or the reason name, an open-ended one held, the card out at excusal from the return day, the instructor's own hold allowed), `test_followup_suggestions` (`test_a_follow_up_never_holds_for_a_known_return`: refused while the model can drop it, nothing recorded, an uncertain return held), the rest of `test_makeup_booking`, `test_absence_makeups`, `test_absences`, `test_department_makeups`, `test_makeup_slots`, `test_assistant_tool_guidance`, `test_followup_suggestions`, `test_assistant`, `test_followup_completion`, `test_followup_redo` and `test_assistant_workspace` (355 tests) pass through `manage_checks.environment` on isolated NVMe scratch; `tests/*.mjs` (79 files) and the frontend type check pass. Replayed read-only on the live holds: of the five held, the two Quiz 7 holds (sections 515 and 514, returns Nov 9 and Nov 11) and an older one whose makeup is no longer owed name a return; the instructor's Quiz 3 hold and the open-ended Quiz 6 hold do not. No migration. Restart after the change.

What a "Same matter as" question refers to, 2026-11-08 (TEACHING-WORKFLOWS SAME MATTER › SEE WHAT IT MEANS; instructor: "I want to be able to see what it is referring to when suggesting to join"): Jev's one-click question on the Follow-up page now holds a closed "Read “…”" fold with that follow-up's captured messages, what it waited on, its date and notes, from the student record the review already reads; those messages leave "Other conversations". `tests/browser_followup_same_matter.py` (the fold, its messages and no repeat) and `browser_teaching_workflows` pass through `manage_checks.py browser` on isolated NVMe scratch; `tests/*.mjs` and the frontend type check pass. No API, migration or outside-service change. Restart after the change.

A day away goes on the calendar, 2026-11-08 (ASSISTANT-MEMORY "ONE DAY GOES ON THE CALENDAR", SCHEDULING "AWAY", ABSENCES "MAKEUP BOOKING" › AWAY; instructor, of "don't schedule any makeups for Friday office hours, I have a doctor's appointment at 3PM that day and won't return to school" kept as a term memory: "I feel like this is a one-off thing that is handled just by putting in a calendar entry"): `test_assistant_memory` (`test_a_note_for_one_day_goes_on_the_calendar`: the message at its live answers, lasting .56 and record .49, is not kept once `one_date` says one day, and "from now on" still is; the decision order), `test_scheduling_engine` (`test_time_away_is_its_own_kind_and_blocks_everything`: the titles read as away and those that are not, all-day and a student's appointment never), `test_makeup_slots` (`test_time_away_comes_out_of_office_hours_and_nothing_else_does`: a block gone, a block shortened to what is left, a remainder too short for the quiz, a meeting and an appointment over office hours leaving them whole), `test_followup_suggestions` (the office-hours facts name a block the calendar says they are away for), `tests/test_assistant_memory.mjs`, and `test_assistant_sweep`, `test_aim_send`, `test_assistant`, `test_assistant_workspace`, `test_dr_requests`, `test_dr_quizzes`, `test_followup_learning`, `test_makeup_booking`, `test_scheduling_service`, `test_scheduling_class_schedule`, `test_rubric_memory`, `test_followup_completion`, `test_student_support`, `test_google_calendar`, `test_memory_sandbox` (525 tests), `manage_checks.py quick` and the frontend type check pass through `manage_checks.environment` on isolated NVMe scratch. `test_scheduling_clock.test_one_campus_zone` fails on d8a4a2f1 too (`static/scheduled-sends.js` names the zone, since 0e1f951a). Read-only on the live calendar: two past custom events read as away ("Exam labeling — unavailable for new bookings", "Unavailable for appointments"), neither over office hours. No migration. Restart after the change.

An assignment goes to one student as their own copy, 2026-11-08 (QUIZZES "A COPY BY EMAIL", ASSISTANT DONE FOR YOU "A STUDENT'S OWN COPY", DOCUMENTS; instructor: "I want the agent to be able to attach assignments it has access to to emails, for instance there is a followup about the take home assignment"): `test_printer_send` (`StudentCopyTests`: a one-copy job named for the student, rendered at once, its route and title-only name; the same copy asked again and on a retried request, a classmate's own; a new copy once printed; an emailed copy refused by Print at Home while the class's job prints), `test_followup_suggestions` (`test_an_assignment_goes_to_the_student_as_their_own_copy`: made at once by a preparation and listed in Done for you without Undo; a blank, the class's job and a classmate's copy refused for mail to the student; a classmate's copy is a card; the reply with their copy is a card that sends it and records its route; a quiz dated tomorrow refused), the rest of `test_followup_suggestions`, `test_followup_completion`, `test_followup_redo`, `test_followup_commitments`, `test_assistant`, `test_assistant_workspace`, `test_mail_attachments`, `test_gmail_mail`, `test_print_email`, `test_term_print_jobs`, `test_documents`, `test_exam_workflow` and `test_quiz_drafts` (532 tests) pass through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

Messages sent later, 2026-11-07 (MAIL "MAIL-SEND-LATER", CANVAS MESSAGES SENT LATER; instructor: "I want to be able to schedule emails to go out to students at a pre-determined time (both through canvas and/or gmail)", then chose Mail's new messages and the Canvas page's messages to sections, recipients exactly as approved, up to two hours late then held, and the Assistant through its Approve card): `test_scheduled_sends` (campus time and its limits; a Mail message kept without sending, sent once at its time by the round under `later-<id>` with Mail's own receipts; a reply or a file from the computer refused; waiting while Gmail is disconnected, held after two hours with Home's card, Send now; 95 minutes late still goes; Cancel; a changed Faculty file holds it; a send Gmail refused stays on Home until Dismiss and is never sent again; the Assistant's Approve rules; a Canvas message sent to exactly the students approved though one was added since; waiting while signed out of Canvas), `test_catalog_migrations` (the new `scheduled_sends` namespace), `test_canvas_messages`, `test_canvas_message_attachments`, `test_gmail_mail`, `test_mail_attachments`, `test_dr_quiz_notices`, `test_request_limits`, `test_workspace_overview`, `test_app`, `test_assistant`, `test_assistant_workspace`, `test_canvas` and `test_response_card_mail` pass through `manage_checks.environment` on isolated NVMe scratch; `tests/*.mjs` (666 pass, with `test_scheduled_sends.mjs` and the Assistant's schedule cards) and the frontend type check pass; `tests/browser_mail_send_later.py` (new: Send later…, a time refused, Schedule sends nothing, the Scheduled tab, Cancel asks first), `browser_canvas_messages` and `browser_mail_more` pass. Not sent through real Gmail or Canvas: the first scheduled message is the check. Migration `scheduled_sends/001_scheduled` (new tables). Restart after the change (faculty: the web process runs the sender), once the restart check is idle.

DR quiz bookings by date, 2026-11-07 (ACCOMMODATIONS "QUIZ BOOKINGS BY DATE"; the instructor's Exams_20261107.xlsx stopped at "Row 21 conflicts with another booking for the same student, course, section and exam"): AIM books every quiz date as another "Quiz" row, and after Send to AIM added the Nov 13 and Dec 1 quizzes each DR quiz taker held two. A "Quiz" booking is now keyed by its date; any other conflicting duplicate still stops the import, its message naming both rows, the student, the exam and the columns that differ. `test_dr_exams` (`test_quiz_bookings_are_told_apart_by_date`: two quiz dates and a Test import as three bookings, both quiz dates link to the one assignment, a later export updates only its date; same-day quizzes and a Test on two dates are refused with the new message), `test_accommodations`, `test_aim_send`, `test_dr_quizzes`, `test_dr_quiz_notices`, `test_dr_quiz_takers`, `test_dr_requests` and `test_exam_links` (71 tests) and `manage_checks.py quick` pass through `manage_checks.environment` on isolated NVMe scratch. The live Exams_20261102.xlsx (kept in the Inbox) parses to the same 29 bookings, its two quizzes keyed by date. No migration; the two bookings made before keep their key "quiz". Restart after the change.

A Faculty assignment's column made from its Canvas assignment is linked to its grading, 2026-11-07 (CANVAS COLUMN ASSIGNMENTS › GRADED IN FACULTY, LINKED; instructor: "quiz 7 was drafted in faculty, why wasn't it automatically linked with that column when it was created", then "yes"): `test_canvas_content` (`test_a_column_made_for_a_faculty_assignment_on_canvas_is_linked_to_its_grading`: a Faculty assignment put on Canvas and pushed, a column the sync made for its Canvas assignment, linked once with the FRQ part, a name alone linking nothing), the rest of `test_canvas_content`, `test_canvas_section_content`, `test_canvas_cell`, `test_canvas`, `test_canvas_sync`, `test_canvas_nightly`, `test_canvas_posting`, `test_gradebook`, `test_gradebook_imports`, `test_gradebook_sources`, `test_gradebook_reconciliation` and `test_gradebook_compute` (267 tests) pass through `manage_checks.environment` on isolated NVMe scratch. Read-only on the live catalog with the new rule: only "Take Home Assignment" would be linked (its copy assignment names the Faculty assignment; no grades, no due date, nothing ever sent); Quiz 7's column was already linked by its certification at 13:51 UTC, and the Sync at 13:52 left every Quiz 7 student's Canvas value as certified (281 grades, 33 certified 0s, 14 cleared). No migration. Restart after the change (faculty; faculty-inbox for the morning sync), once the restart check is idle.

The needs-you chip no longer covers the page title, 2026-11-06 (FRONTEND, DESIGN-LANGUAGE §5; instructor, of Home's "Welcome back, Chris." under "9 things need you": "this needs fixing"): the muted line after the chip had squeezed the title's box (285 px of text in 173–238 px) and the chip drew over the overflow. Measured on live Home read only at 1100, 1280, 1500 and 1600 px with the new ledger.css and page-title-fit.js routed in (title at its full width, the rest ends in an ellipsis), and with a long title (steps to 22 px and wraps beside the chip). `tests/*.mjs` (662 pass), `test_design_language`, the frontend type check and `manage_checks.py browser --browser home --browser theme` pass on isolated NVMe scratch. No migration. Restart after the change.

A paper named by hand starts grading promptly, 2026-11-06 (PIPELINE "Grading"; instructor: "can we make it so papers that needed me to manually assign them to a student automatically get graded once I assign them?"): naming a copy (`copy_assignment` on a copy of the exam's print jobs) is now part of `_auto_grade_marker`, so the worker looks again within `LOOK_EVERY` (60 s) instead of the next 15-minute look, and the batch starts after `SETTLE` (120 s). Before the change such papers were already graded automatically, only later: on live, Quiz 7's three papers named at 22:55 UTC were in an automatic batch at 23:01, prompted by an unrelated flag-review batch. `test_grading_auto_grade` (`test_a_student_named_on_a_copy_brings_the_next_look_forward`, watched failing on the old marker; the other three unchanged) passes through `manage_checks.environment` on isolated NVMe scratch; the marker's new read takes about 0.005 ms on the live catalog. No migration. Restart after the change (faculty-inbox runs the worker), once the restart check is idle.

Faint photocopied codes, 2026-11-06 (DOCUMENTS "FAINT MARKS", QUIZZES "PHOTOCOPIED STAMPS" › A LATER PAGE OF A MOVED PAPER; instructor: "I made copies of some that I printed out", "yes proceed"): `test_documents` (`test_a_faint_mark_is_read_from_the_page_made_black_and_white`: only the thresholded 300 DPI drawing reads, the decoder records the threshold and locations stay on the 200 DPI page, and a page with no mark is drawn at both thresholds and keeps nothing; `test_photocopied_papers_whose_faint_codes_are_read_again_join_their_own_copies`, in both the fallback and managed-storage classes: the dry run changes nothing, the write keeps the original observation and records one `faint_marks_read` event, a copy assembled in the scan keeps its stamp, a later page joins the paper moved before it and a fully faint paper moves to its own copy, no conflict left), the rest of `test_documents`, `test_quiz_drafts` (photocopied and shared stamps unchanged), `test_document_corners`, `test_scan_stacks`, `test_sequence`, `test_scan_stray_pages`, `test_unlabeled_exams`, `test_term_print_jobs` and `test_bubble_sheets` pass through `manage_checks.environment` on isolated NVMe scratch. Rehearsed on a backup-API snapshot of the live catalog in NVMe scratch (the Quiz 7 scan's PDF read from the live Inbox read-only, naming stubbed for want of roster files; snapshot deleted afterwards): the dry run read all 139 pages with no code (each a stamp of the four photocopied copies), the write and one photocopy turn left 0 conflicting and 0 unidentified pages, 99 moves of which 27 joined, and all 572 pages formed 286 two-page papers, each on one copy, no copy on two. The faint look costs about 0.14 s more for each unmarked page. No migration. Restart after the change (faculty, faculty-inbox for the worker), then `manage_inbox.py faint-marks 5d407b0b-3126-4924-92ff-9c7cd1849608` (dry run) before `--write`.

Ten data-touching bug fixes, two app fixes and the OCR runtime on NVMe, 2026-11-06 (instructor: "fix all 4 things", after a read-only look at live data showed none of the ten changes existing data): late certification with a settled Gradescope MC correction (a late student with an open correction still waits), unlabeled plans of earlier runs, bubble-sheet OCR notes, the template-reading profile as an allow-list, flag-review and multiple-choice-reader export wording, the Follow-up desk's partial/started cards, the Email Worker's case-insensitive sender check (not deployed), the kept-file sandbox CSP, at most one learning objective per tagged question, and the Assistant's actor on four write routes; "Codes & API access" opening from the settings hub, and Close and Escape on the Mail compose dialog during a Review. Each fix's new test failed first; the groups ran every module touching what they changed (1,629 and 1,120 tests), `cloudflare/teach-inbox` `npm run check` and `npm test` (39), and the browser checks of their pages (cross_links and mail_attach now pass). The GLM-OCR runtime was installed fresh at /mnt/nvme/Teaching/runtime/glm-ocr (same pinned revision, byte-identical model files, identical packages; OCR of a fixture page identical but for its seconds) and the default moved there; `test_faculty_ocr` and `test_alias_ocr` pass. On the merged branch: Python syntax (914 files), browser JavaScript syntax, `tests/*.mjs` (662 pass), the frontend type check and pyflakes pass. After landing (6214e8ac) the full Python suite (4,508 tests) failed only `test_print_seating` once, and 153 of 157 browser checks passed; `browser_assignment_workspace` (its Canvas content wait), `browser_followup_learning` and `browser_howdy_import` did not. `test_print_seating` and `browser_assignment_workspace` also fail on f5ec38d9 (3 of 3 runs there, 1 of 3 on 6214e8ac): flaky, not these changes. No migration. Restart after the change. The Worker was deployed on the instructor's word ("deploy the worker"): `npm run check` and `npm test` (39) passed first, `npx wrangler deploy` uploaded version 168a1bdb with its queue, R2 bucket, INBOX_URL and 15-minute schedule, and both secrets (INTAKE_POLICIES, TEACH_INBOX_WEBHOOK_SECRET) are still set.

Latent-bug fixes from the debt sweep, 2026-11-06 (instructor: "the fixes change the behavior, but in an obviously good way"; "yes"; COURSE-NOTES, GRADING FLAG REVIEW and FRONTEND.md updated with them): 40 bugs fixed and the old failing checks repaired, one commit each, every new test watched failing on 00f7e5c1 first, through `manage_checks.environment` on isolated NVMe scratch. Each group ran every test module importing what it changed (grading 347 tests, Python A 1,159, Python B 347) and the browser checks of the pages it touched (35 in all). `test_google_calendar` and `browser_calendar_google` now build their -05:00/-06:00 offsets from America/Chicago; `browser_absences`, `browser_grading_review_blank` and `browser_teaching_workflows` were stale; `browser_cross_links` and `browser_mail_attach` still fail on app behaviour, left for the instructor. Stored question-bank text (2,923 fragments) was replayed read-only through the old and new `quiz_latex.fragment`: no outcome changes. On the merged branch: Python syntax (914 files), browser JavaScript syntax, `tests/*.mjs` (659 pass), the frontend type check and pyflakes pass. The faculty_ocr setup guard (lens-shared-helpers#10) is held back: its default runtime is on the root disk. After landing (495b191e) the full Python suite (4,492 tests) failed only `test_grading_flag_adjustments.Pipeline` (4 tests): its one-argument stand-in for `_resolution_rows` refused the flag-review fix's second argument. The fix was reverted on live at once (803032f4; no flag review ran meanwhile), then restored with the stand-in passing every argument through; the ten modules touching flag resolution (217 tests) pass. `tests/tools/browser_suite.py`: 154 of 157 pass; `browser_cross_links` and `browser_mail_attach` (app behaviour, left for the instructor) and `browser_howdy_import` (fails on c81586b6 too) do not. No migration. Restart after the change.

Technical-debt sweep, 2026-11-06 (instructor: "Refactor the codebase and eliminate technical debt"; every module contract, PERFORMANCE "byte-identical", STORAGE, CHECKS): 471 behavior-preserving cleanups in 69 commits, each confirmed by an independent verifier, fixed with its targeted tests and reviewed by a second agent, through `manage_checks.environment` on isolated NVMe scratch. On the wave-1 tree the full Python suite (4,549 tests in three parallel chunks) passed apart from two tests that fail identically on c81586b6: `test_google_calendar.PullTests.test_the_chosen_google_calendars_show_read_only_on_the_calendar` (its fixture hard-codes -05:00 and reads an hour early since daylight time ended) and `test_pipeline_improvements.FairTurns.test_all_five_productive_steps_get_a_turn` (its stub lacks `auto_full_credit_one`, called since 333a443f). On the final tree: `tests/*.mjs` (656 pass), the frontend type check, Python syntax (914 files) and browser JavaScript syntax pass; pyflakes finds no new issue (148 to 8); `tests/tools/browser_suite.py` runs 157 browser checks, 149 pass and the other 8 (absences, calendar_google, cross_links, followup_learning, grading_review_blank, howdy_import, mail_attach, teaching_workflows) fail on c81586b6 at the same point with the same message. A read-only snapshot of the live catalog replayed 246 GET routes under `bwrap --unshare-net` with the old and new code: every API body is byte-identical (6 time-dependent routes excluded after two old-code runs); only the page shell differs, by the 15 sidebar icon spans and the active dot that no medium shows (`.sidebar` is hidden in print). After landing, the full Python suite on 5da0c64e (4,471 tests in three parallel chunks) passed apart from the same two tests. No migration. Restart after the change (faculty, faculty-inbox for the Gradescope sender, faculty-email-gateway for the app it builds).

A makeup's rubric lines name what the original left, 2026-11-05 (GRADING "MAKEUP VERSIONS" › LEFTOVER VALUES; instructor: "it copies the numerics from the original versions that conflict with the makeup version's actual answers, is this an issue?"): `test_grading_rubric_adapt` (a formula and a rewritten value say nothing; a value only the original has is named, also in a line rewritten around it; a correct choice's letter is read against this version's answers), `test_makeup_drafts` (a revision with the old sentence stored is read again on the new rule, and stored results stay as they are), `test_grading_rubric_drafts`, `manage_checks.py browser --browser grading_rubric_adapt`, through `manage_checks.environment` on isolated NVMe scratch. Replayed read only on the live makeups. No migration. Restart after the change.

The Follow-up page loads quickly, 2026-11-05 (TEACHING-WORKFLOWS "RESOLVED WHEN OPENED", PERFORMANCE "ONE READ OF A LISTING'S CONVERSATIONS"; instructor: "the followup page seems to take a while to load"): `test_followup_suggestions` (the brief read, what it keeps, and a follow-up that stays in full), `test_student_support`, `tests/test_student_support.mjs`, `tests/test_followup_desk.mjs`, `tests/test_followup_suggestions.mjs`, `manage_checks.py browser --browser followup_suggestions --browser followup_resolved` (opening a resolved follow-up reads it once) and the frontend type check, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

No copies field on a makeup approval with booking cards on, 2026-11-05 (GRADING "MAKEUP VERSIONS" › NO COPIES TO ASK FOR; instructor: "If I need such a copy, can't I just print it out using what's already available in the print menus?"): `manage_checks.py browser --browser assignment_workspace` (no field and "prints no copy now" with cards on; "Copies to print" at the students owed without cards), `tests/test_absence_makeups.mjs` and the frontend type check, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

A makeup version waiting counts on the Assignments badge, 2026-11-05 (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED" › ON THE BADGE; instructor: "show on the notification badge"): `test_makeup_drafts` (one card per current term's draft, gone once approved or scrapped), `test_makeup_booking` (no longer in the Makeups card), `tests/test_home_navigation.mjs`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

A makeup version waiting shows on Assignments, 2026-11-05 (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED"; instructor: "it doesn't show quiz 7 needing anything"): `test_makeup_drafts` (one item from ready until approved or scrapped, none while drafting, its words on a failed draft),
`test_assignment_workspace` (it keeps a certified assignment in view), `tests/test_assignment_workspace.mjs`, `manage_checks.py browser --browser assignment_workspace` (the row opens the window once over Create), through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

No scanned paper asks nothing in the pipeline, 2026-11-05 (ASSIGNMENT-WORKSPACE "THE ZEROS ARE SAID AT CERTIFY"; instructor: "the notice of
0s being assigned in the certify page is enough"): `test_assignment_workspace` (a past assessment with a paper back and students with no scan
lists no `collection` item), `tests/test_assignment_workspace.mjs` (`no_scan` is not under "Needs you"; a flagged paper is),
`tests/test_home_navigation.mjs` (no Home row), `tests/test_grading_run.mjs` (the Grade step's callout names flagged papers only),
`manage_checks.py browser --browser assignment_workspace` and `--browser grading_layout`, through `manage_checks.environment` on isolated NVMe
scratch. No migration. Restart after the change.

Shared stamps, 2026-11-04 (QUIZZES "SHARED STAMPS"; instructor: "I need to know if they let their classmates copy the assignment"): `test_quiz_drafts` (the approval records the box; each stamp names both students; a deleted scan leaves the list; no box, no `shared_stamps`; Home's card until certified), `test_assignment_workspace`, `test_workspace_overview`, `tests/test_assignment_workspace.mjs`, `tests/test_quiz_drafts_ui.mjs` and the frontend type check, through `manage_checks.environment` on isolated NVMe scratch; the dialog's box looked at on the take-home's own draft, not submitted. No migration. Restart after the change.

The check keeps what was asked, 2026-11-04 (QUIZZES "THE CHECK KEEPS WHAT WAS ASKED"; instructor: "it looks like it's been going over the same things over and over"): `test_quiz_drafts`
(`asked` reaches the check and both kinds of fix round, oldest first, and is absent on a first draft; "What changed" keeps the fix's account; each stage is named from its first request), `test_makeup_drafts`, `test_grading_prompt_contracts`,
`tests/test_quiz_drafts_ui.mjs` and the frontend type check, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

A decided To do closes itself, 2026-11-04 (TEACHING-WORKFLOWS DECISION CLOSES; instructor: "yes", to a To do resolving itself with Undo once its absence is decided):
`test_followup_evidence_closes` (resolves with its work tasks and Undo puts both back; Jev unsure or anything else owed keeps it, asked once; a promised reply or an undecided
absence is not asked; which decisions count), with the follow-up modules, through `manage_checks.environment` on isolated NVMe scratch. Candidates replayed read-only on the live list. No migration. Restart after the change.

Waiting on the student stays there, 2026-11-04 (TEACHING-WORKFLOWS CHECK BACK and STILL WAITING; instructor: "some of them aren't actually waiting on me"):
`test_followup_evidence_closes` (the day a next step was set is not a check-back day), `test_followup_suggestions` (an acknowledgment keeps a waiting follow-up
waiting with no reply; a new question still needs one), through `manage_checks.environment` on isolated NVMe scratch. Replayed read-only on the live list. No
migration. Restart after the change.

A reply from its card, and a redo keeps the status, 2026-11-04 (TEACHING-WORKFLOWS "A REPLY FROM ITS CARD", ASSISTANT "A REDO KEEPS THE STATUS"; instructor, of Ashton Nolan's follow-up: "what is going on with this one"): `test_followup_suggestions`
(a reply its card sent in another thread answers the conversation until the student writes again; a redo with a reply and no status takes the replaced round's, one with its own takes none) with the follow-up, student-support, absence and communications modules, through `manage_checks.environment` on isolated NVMe
scratch. Replayed read-only on the live list: only Nolan's item changes; the read takes about 10 ms over the term. No migration. Restart after the change.

A decided step is not carried, 2026-11-04 (ASSISTANT "A DECIDED STEP IS NOT CARRIED"; instructor: "ive already decided those dates"): `test_followup_suggestions` (a changed decision turns a To do
that asks it into a card to close the follow-up, still for approval; what is left of real next steps; a day not decided leaves the step) with the follow-up modules, through
`manage_checks.environment` on isolated NVMe scratch; `tests/*.mjs`, the frontend type check and `browser_followup_decision.py` for the card's note. No migration. Restart after the change.
A busy Faculty is not an unreachable one, 2026-11-04 (FRONTEND "A RESTART NOBODY SEES" › A BUSY FACULTY IS NOT AN UNREACHABLE ONE; PERFORMANCE ONE READ OF THE
TRIAGE RECEIPTS; instructor: "got this error again after approving a followup"): `test_jev` (a listing never calls `lookup`, `newest` has one row per state), `test_app`
(a slow request in the journal), `test_student_support`, `test_followup_suggestions`, `tests/test_request.mjs` and the frontend type check, through
`manage_checks.environment` on isolated NVMe scratch. The two Follow-up reads hash byte-identical before and after on a snapshot of the live catalog (3.6 s to 1.6 s, 3.0 s to 0.95 s). No
migration. Restart after the change.

Published in Canvas stands, 2026-11-03 (CANVAS "PUBLISHED IN CANVAS STANDS"; instructor, a screenshot of Quiz 3 under Needs your decision): `test_canvas_content`
(published in Canvas asks nothing and Replace all never unpublishes it; the copy follows only when every course published it, recorded in step; other Canvas edits, a Canvas
unpublish and an unpublish made in Faculty ask or update as before), `test_webassign_nightly`, `test_canvas_nightly`, `test_canvas`, `test_canvas_sync` and `test_canvas_section_content`, through `manage_checks.environment` on
isolated NVMe scratch. Replayed read-only on the live preview. No migration. Restart after the change.

A restart nobody sees, 2026-11-03 (FRONTEND "A RESTART NOBODY SEES"; instructor: "im getting this error a lot", of "The server response could
not be read"): `test_web_server` (two real processes over one held socket: the old one finishes its answer, the request arriving between them waits
for the new one, a kept connection holds nothing up, an answer past the wait does not hold the stop; the socket stored, reused, replaced; no
systemd or a refusing one never stops the start), `tests/test_request.mjs` (a GET asks again, never a write, a refusal or its own timeout),
`test_canvas`, `test_canvas_sync`, `test_canvas_messages`, `test_app`, the frontend type check and `browser_canvas_posting.py`, through
`manage_checks.environment` on isolated NVMe scratch. A scratch user unit with `keep-port.conf`'s settings took three real `systemctl restart`s under
load: 2562 requests, none lost, the slowest 2.6 s; a stop closed the port and nothing started it again. No migration. Restart after the change;
that first restart still has the gap (the process running then left nothing with systemd), and the live second restart was probed through Tailscale.

A department makeup card once the exam is seated, 2026-11-03 (ABSENCES "DEPARTMENT MAKEUP EXAMS" › ONCE THE EXAM IS SEATED; instructor, of Jon
Bellamy's absence over an unseated Exam 3): `test_department_makeups` (a chart brings one To do follow-up with the card and nothing is sent; a
just-decided absence goes first; sending resolves it; a declined card is not offered again, an expired one is; nothing owed resolves it and withdraws
the card; a form that cannot be read opens nothing; a card in the student's own follow-up stands), `test_absences`, `test_absence_makeups`,
`test_absence_totals`, `test_makeup_booking`, `test_dr_requests`, `test_followup_suggestions`, `test_followup_connect`, `test_student_support`,
`test_app` and `test_inbox`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

Back to the original transcriptions, 2026-11-03 (GRADING "THE SAVED TRANSCRIPTIONS STAND"; instructor: "go back to the originals"):
`test_grading_training`, `test_catalog_migrations`, `test_grading_cloud`, `test_grading_pipeline`, `test_grading_teams`,
`test_frq_providers`, `test_assistant_coding`, `test_grading_certification`, `test_design_language`, `tests/*.mjs`, the frontend type check,
`browser_grading_settings.py` and `browser_grading_certification.py`, through `manage_checks.environment` on isolated NVMe scratch. No new
migration; 053 stays recorded with its 956 unused rows. Restart after the change.

A late paper after a classmate's drop, 2026-11-03 (GRADING "A DROP NEVER ASKS TO CERTIFY AGAIN"; instructor, asked of Quiz 6's makeups): `test_certification_automatic`
(a dropped classmate leaves the markers live and stays as certified; a later drop and one regrade ask nothing more; a classmate who moved
otherwise still carries them), `test_grading_certification`, `test_certified_archive`, `test_regrade_requests`, `test_regrade_mc`,
`test_regrade_followup`, `test_regrade_panel`, `test_gradescope_create` and `test_gradescope_after`, through `manage_checks.environment` on
isolated NVMe scratch. No migration. Restart after the change.

A follow-up's absence names class days, 2026-11-03 (ABSENCES "A FOLLOW-UP NAMES CLASS DAYS"; instructor, of a card for Sat, Oct 31 alone: "why is it asking me
to excuse a saturday"): `test_followup_suggestions` (a section meeting Monday, Wednesday and Friday: 10/26, 10/30 and 11/2 stand; a Saturday, a Sunday, a whole weekend and a
Thursday are refused with no card; a stretch is brought to its class days; an evening exam, a planned exam and the exam period stay; the instructor's dates and a card
already waiting pass; a revision leaving its dates alone passes) with the modules touching absences or follow-ups, through `manage_checks.environment` on isolated NVMe
scratch. Replayed read-only on the 8 open live cards. No migration. Restart after the change.

The follow-up says what a booking card does, 2026-11-03 (ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP; instructor: "it frequently says it
will send an email for them to pick a date, but I don't see anything on my end that shows that will happen"): `test_makeup_booking` (the
Excuse card's Then line and audience with the cards off and on, a changed decision, a hold; the follow-up line before, on the way, emailed,
booked), `test_followup_suggestions` (both reach the page's read), `test_followup_connect`, `test_student_support`, `test_design_language`,
`tests/*.mjs` (`makeupCardsMarkup`), the frontend type check and `browser_followup_decision.py`, through `manage_checks.environment` on
isolated NVMe scratch. No migration. Restart after the change.

A regrade after a classmate's drop, 2026-11-03 (GRADING "A DROP NEVER ASKS TO CERTIFY AGAIN"; instructor, of Edda's Quiz 6 regrade stopped at
Gradebook): `test_regrade_panel` (a dropped classmate certified 0 with no work and a question nobody has answered never stop the gradebook
step, and the dropped row stays as certified; another student's move still does), `test_grading_certification`,
`test_certification_automatic`, `test_certified_archive`, `test_regrade_requests`, `test_regrade_mc`, `test_regrade_followup`,
`test_gradescope_create` and `test_gradescope_after`, through `manage_checks.environment` on isolated NVMe scratch. Replayed first on a snapshot of the live catalog: one
cell written (6.5 to 7), the plan reads current, all 337 rows kept. No migration. Restart after the change.

Transcriptions checked against their crops, 2026-11-02 (GRADING "TRANSCRIPTIONS ARE CHECKED"; instructor: "check and correct the training
data so the model is trained with accurate information"): `test_grading_training` (three readings that agree stand and ask nothing more; a
changed digit needs both models and touches no grade or example; two that differ, or an unreadable answer, are left out of reader training;
what a failure had answered is replayed; only with the setting on and no batch running; a blank review is no longer marked edited) with
`test_catalog_migrations`, `test_grading_cloud`, `test_grading_pipeline`, `test_grading_teams`, `test_frq_grading`, `test_frq_providers`,
`test_grading_certification`, `test_design_language`, `test_assistant_coding` (a check in flight never holds a restart back), `tests/*.mjs`, the frontend type check, `browser_grading_settings.py` and `browser_grading_certification.py`, through `manage_checks.environment` on
isolated NVMe scratch. The rule was first replayed on 115 live answers through a read-only copy of their crops. Migration grading 053 (new
table, in the adoption test). Restart after the change.

The Assignments badge can be read on its page, 2026-11-02 (ASSIGNMENT-WORKSPACE "THE BADGE CAN BE READ ON THE INDEX", "A FINISHED
RUBRIC CHECK SAYS SO"; instructor: "what needs me in assignments, it isn't clear to me"): `test_assignment_workspace` (the label and
address of a finished check), `test_design_language`, `tests/*.mjs` (643, `rowDecisions`, `assignmentDecisionCards`), the frontend
type check and `browser_assignment_workspace.py` (the badge's cards lead the index, a card of another page stays out, none draws
nothing; a row not started has no orange block) with `browser_home.py`, on isolated NVMe scratch. No migration. Restart after the change.

DR upload card again, 2026-11-01 (SCHEDULING "DR REQUEST EMAILS" › AGAIN; instructor: "I got the push, but it never showed me the
numbers"): `test_dr_requests` (a send that ended without uploading, or was cut off with no end, gets a fresh card in the same
follow-up, at most three; one still signing in is waited for) with `test_aim_send` and `test_dr_quizzes`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

DR request emails open the upload card, 2026-11-01 (SCHEDULING "DR REQUEST EMAILS", and DR DATE › CERTIFIED WITH A DR DATE
AHEAD; instructor: "can we make it so the app can recognize those confirmation emails and then queue up the accessible upload as a
follow up"): `test_dr_requests` (the DR center's emails kept once, others and other senders not; one Follow-up with one Approve card
per student and quiz, none for an exam or a section without the quiz; Approve starts Send to AIM's send of that quiz with the saved
sign-in and answers its session; an accepted upload resolves the follow-up unless the instructor saved it), `test_dr_quizzes` (a
certified quiz stays while its DR date is ahead), with `test_aim_send`, `test_dr_quiz_notices`, `test_catalog_migrations`,
`test_app`, `test_assistant`, `test_department_makeups`, `test_followup_suggestions`, `test_student_support`,
`test_regrade_followup`, `tests/*.mjs`, the frontend type check and `browser_dr_requests.py` (Approve on the Follow-up page opens
"Upload to AIM" on the approved session, Duo's code shows, the end says the copy was uploaded), through `manage_checks.environment` on isolated NVMe scratch.
Migration scheduling 008 (two new tables, in the adoption test). Restart after the change.

Import from Howdy, 2026-11-01 (CANVAS "IMPORT FROM HOWDY"; instructor: "can we build a way to import the rosters directly from
howdy"): `test_howdy_import` (Howdy's rows as Howdy's CSV read by the roster import, a numeric UIN keeps nine digits, a Q drop
reads as one; course rows, term codes and pages recognized; the start route plans the course page's sections, keeps the guard and
refuses an "Other" term) with `test_aim_import`, `test_aim_send`, `test_aim_capture`, `test_rosters` and `test_canvas` (87 tests),
`tests/*.mjs` (642), the frontend type check, `browser_howdy_import.py` (the server's browser on its own Xvfb display, past a stand-in
Cloudflare check, the saved sign-in and a Duo push, to a stand-in Howdy whose API wants the page's Bearer token; two sections import
at once, the Q drop is dropped, the section Howdy does not list is named), `browser_rosters.py`, `browser_aim_import.py` and
`browser_canvas.py`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

Faster Assignments pages, 2026-11-01 (PERFORMANCE "THE ASSIGNMENTS PAGES"; instructor: "the assignment's page takes a long time to
load"): `test_assignment_workspace` (the assignment's read is the owner's workflow without `export`), with `test_teaching_review`,
`test_grading_certification`, `test_certification_automatic`, `test_grading_rubric_scope`, `test_grading_rubric_quality`,
`test_grading_sweep`, `test_regrade_requests`, `test_regrade_mc`, `test_regrade_panel`, `test_regrade_followup`,
`test_exam_overview`, `test_exam_workflow`, `test_grading_participation`, `test_gradescope_mc_corrections` and
`test_grading_rubric_burden` (355 tests), `tests/*.mjs` (642), the frontend type check and `browser_assignment_workspace.py` (the
receipts read after the overview, once per Refresh progress, none on the quiet refresh), through `manage_checks.environment` on
isolated NVMe scratch. Timed read only on a snapshot of the live catalog, old code against new: the list, Certify, delivery, Home and
Exams bodies identical (timestamps aside). No migration. Restart after the change.

DR notices close at midnight after their exam, 2026-11-01 (RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › DR NOTICES
CLOSE TOO; instructor: "yes, add that"): `test_response_cards` (`test_dr_notices_close_at_campus_midnight_after_their_exam_day`:
an early DR date waits for the chart's exam day, a late one for its own, an answered card keeps "verified", unanswered ones
"closed", a card without a DR date never closes), with `test_seating` (the chart's DR counts unchanged after the close),
`test_exam_workflow`, `test_exam_overview` and `test_response_card_mail` (115 tests), through `manage_checks.environment` on
isolated NVMe scratch. Dry run on the live catalog: 26 DR notice cards (17 verified, 3 needs review, 2 opened, 4 issued), every
one past its exam day, close at the first timer run. No migration. Restart after the change.

A lost Sync with Canvas answer, 2026-10-31 (CANVAS SYNC WITH CANVAS › A LOST ANSWER; instructor: "this is the second time I get
this error"): `test_canvas_sync` (the lookup unknown before a sync, working while it reads Canvas, then the sync's own answer, the
window's words in the journal; a foreign section refused) with `test_canvas`, `test_canvas_posting` and `test_canvas_nightly`,
through `manage_checks.environment` on isolated NVMe scratch; `browser_canvas_posting.py` (the second Sync's answer replaced by an
empty 502 after Faculty synced: the window still shows the post and "Sync finished", never "could not be read");
`tests/test_request.mjs`, `tests/test_gradebook_grid.mjs` and the frontend type check. No migration. Restart after the change.

A missed makeup records 0, 2026-10-31 (ABSENCES "MAKEUPS" › MISSED; instructor: "it won't let me clear her makeup owed, she
missed the makeup and missed it so she got a 0"): `test_absence_makeups` (`MissedMakeupTests`: owed no more on Home, the Makeups
view and Certify's `covering`, kept in the absence window with Undo, an undecided absence or a makeup not owed refused, a makeup
sitting without an absence closed too, nothing outside absences/ written; ZERO ONCE DUE counts the blank as 0), `test_grading_certification`
(`test_a_makeup_recorded_missed_receives_0_at_the_next_certify`: changed by `makeup_ended`, the 0's detail "Missed the makeup",
recorded at Certify), with `test_absences`, `test_absence_totals`, `test_makeup_booking`, `test_department_makeups`,
`test_certification_automatic`, `test_catalog_migrations`, `test_assistant`, `tests/test_absences.mjs` and the frontend type check,
through `manage_checks.environment` on isolated NVMe scratch. Migration absences 009 (new table, in the adoption test). Restart
after the change.

Absence totals on the Absences page, 2026-10-31 (ABSENCES "TOTAL ABSENCES" › ON THE ABSENCES PAGE; instructor: "add a tab in the
absences page to see the running totals of each student ... stratify by section"): `test_absence_totals` (by section, most days
first, each student's total the card's, no UIN), `tests/test_absences.mjs` (the view's address, split and ahead words), with
`test_absences` and the frontend type check, through `manage_checks.environment` on isolated NVMe scratch; `browser_absences.py`
(Totals by student: Section 507 first, a count opens to its two days, no decision chips) with its two date-bound makeup-row lines
left out as before. No migration. Restart after the change.

Only class dates count as absences, 2026-10-31 (ABSENCES "TOTAL ABSENCES" › CLASS DAYS ONLY; instructor: "no, only class dates"):
`test_absence_totals` (an exam day off the class days adds no day for an absence over it or its 0) with `test_absences`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

Take-home work leaves the absence count, 2026-10-31 (ABSENCES "TOTAL ABSENCES" › TAKE-HOME; instructor: "math experiments doesn't
count nor does quiz 1 because those were take homes"): `test_absence_totals` (an undated quiz 0 neither counted nor named, a dated
one counted) with `test_absences`, `tests/test_absences.mjs` and the frontend type check, through `manage_checks.environment` on
isolated NVMe scratch. No migration. Restart after the change.

Total absences on the student card, 2026-10-31 (ABSENCES "TOTAL ABSENCES"; instructor: "we should keep a total number of absences
on the students profile card"): `test_absence_totals` (iClicker 0s a day once, an answered poll that day means present; an absence
and a 0 on its day one day named by the decision, weekends out without class days, withdrawn out, a later day ahead; a quiz 0 on
the section's quiz day even with a makeup sitting elsewhere, missing counts, an undated quiz named; with class days only the days
the section met plus a quiz day), with `test_absences`, `test_iclicker`, `test_scheduling_class_schedule`, `test_scheduling_service`,
`test_absence_makeups`, `test_makeup_booking`, `test_department_makeups`, `tests/test_absences.mjs` and the frontend type check,
through `manage_checks.environment` on isolated NVMe scratch. `browser_absences.py` gained the card's total; it already failed
before this change at its makeup row (line 175, "Makeup sitting Fri, Sep 25", a date now past), so it was run with those two
date-bound lines left out. No migration. Restart after the change.

Stamped papers' names matched once, 2026-10-31 (QUIZZES "NAMES ON UNNAMED STAMPED COPIES"; instructor: "why is this moving so
slowly", a 582-page Quiz 6 scan): `test_quiz_drafts` (`test_the_name_written_on_an_unnamed_stamped_paper_names_its_copy_when_nothing_argues_against_it`:
the view drawn again matches nothing anew and is the same), with `test_makeup_drafts`, `test_unlabeled_exams`, `test_term_print_jobs`,
`test_bubble_sheets`, `test_scan_stacks` and `test_pipeline_per_paper`, through `manage_checks.environment` on isolated NVMe scratch
(a session worktree has no `.tools`: set `FACULTY_TEXLIVE_ROOT` to the checkout's, or every quiz draft fails to compile). Timed
read only on a snapshot of the live catalog: the scan's view 2.9 s, then 0.05 s. No migration. Restart after the change.

Gradescope wordings Faculty accepts, 2026-10-31 (GRADESCOPE "RENAMED ITEMS"; instructor: "do the small change"):
`test_gradescope_create` (`test_a_recorded_gradescope_wording_is_matched_and_nothing_else_changes`: a renamed item refused, its
wording read and recorded, then matched with no refusal; nothing written to Gradescope; no reason refused; recorded once; an item
worth something else never recorded; the route answers 409), with every `test_gradescope*` module and `test_catalog_migrations`
(267 tests), through `manage_checks.environment` on isolated NVMe scratch. Migration grading 051 (new table, in the adoption test).
Restart after the change.

The assignment finder stops waiting on alias reads that find nothing, 2026-10-31 (DOCUMENTS "A SCAN WITHOUT LABELS FINDS ITS
ASSIGNMENT" › ALIASES SAMPLED; instructor: "make that change as long as it doesn't cause any pathological behavior"):
`test_assignment_finder` (`test_a_scan_waits_for_alias_reads_only_until_a_sample_finds_no_printed_alias`: one read short of the
sample waits, the sample without an alias does not, a read that saw an alias waits for every read), with `test_unlabeled_exams`,
`test_alias_ocr`, `test_bubble_sheets`, `test_pipeline_per_paper`, `test_scan_stacks`, `test_grading_intake`, `test_exam_overview`
and `test_workspace_overview`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

Approvals side by side, 2026-10-31 (ASSISTANT "APPROVALS SIDE BY SIDE"; instructor: "see if there is a reliable way to parallelize
them so I can approve multiple followups at while others are finalizing"): `test_followup_parallel_approvals` (another student's
card and Approve all finish while one student's submission is held; one student's second approval waits its turn and is counted by
the restart check; a Decline during a mail card's preview stands and nothing is sent; lanes by student and route, never waited for
while another is held; an unchanged follow-up checked without the lifecycle lock). Three of its seven fail on the previous code
(the Approve all one waited out its 20 s hold before it asserted a time). With every `test_assistant*`, `test_followup*`,
`test_regrade*` module, `test_absence_makeups`, `test_makeup_booking`, `test_department_makeups`, `test_canvas_files`,
`test_grading_flag_resolution` and `test_student_support`, through `manage_checks.environment` on isolated NVMe scratch. No
migration.

Makeup times from the student's return, 2026-10-30 (ABSENCES "MAKEUP BOOKING" › FROM THEIR RETURN; instructor: "fix it so that the
times offered correspond to their return date, not the date of the absence"): `test_makeup_slots`
(`test_times_start_at_the_return_date_never_before_now`: a later return starts the times and the 30 days, a past one starts from
now, none changes nothing, a moved due date stands), `test_makeup_booking` (the dry run, the worker's card, the Makeups view's due
date and "Send a new card" keep to the return in the details; a past return offers from now; the clinic-note wording read), with
`test_department_makeups`, `test_absence_makeups`, `test_absences`, `test_makeup_length`, `test_makeup_drafts`, `test_quiz_drafts`,
`test_followup_suggestions`, `test_followup_redo` and `tests/test_absence_makeups.mjs`, through `manage_checks.environment` on
isolated NVMe scratch. No migration.

Rubric questions your own sources answer, 2026-10-29 (GRADING "FEWER RUBRIC QUESTIONS" › 2026-10-29; QUIZZES; review of the 103
questions asked since Oct 1, `/mnt/nvme/Teaching/reports/rubric-questions-review-2026-10-29.md`): `test_grading_rubric_questions`
(the reviewers v13/v9/v6/v11 and writers v10/v9/v10 add only their sections and fields; `edit_fields` keeps real changed lines only;
`topic` kept; a draft's fixed findings are no decision and one topic is one; an answered line's drafted comment rewritten; a points
change approved with one click is the instructor's edit and readies the rubric again; a draft's key fix resolved by its answers is
still made on the plan; the letters choice offered and sent only once chosen), `tests/test_grading_rubric_concerns.mjs` (one topic
across problems is one decision; a problem decided apart, a differing draft or a fixed finding stays out), `tests/test_quiz_drafts_ui.mjs`,
with `test_grading_rubric_burden`, `test_grading_rubric_quality`, `test_grading_rubric_edits`, `test_grading_rubric_versions`,
`test_grading_rubric_drafts`, `test_grading_rubric_adapt`, `test_grading_rubric_invalidation`, `test_grading_rubric_links`,
`test_grading_rubric_scope`, `test_grading_prompt_contracts`, `test_quiz_drafts`, `test_makeup_drafts`, `test_rubric_memory`,
`test_drafting_tools` and `test_auto_prepare`, every `tests/*.mjs`, the frontend typecheck and the browser checks
`grading_rubric_quality` (the edit offer), `quiz_drafts`, `grading_gradescope_rubrics` and `grading_rubric_edits` (which had failed on
main since 2026-10-24: the window opens on Decisions, so it now opens Rubric items first), through `manage_checks.environment` on
isolated NVMe scratch. No migration. Restart after the change, once the restart check is idle.

A part read as several values keeps its key, 2026-10-29 (GRADING "ONE PART, SEVERAL VALUES"; QUIZZES; Quiz 6's draft had no key
for Problem 4 on Versions B, C and D, "Label each part of the answer key once."): `test_quiz_drafts`
(`test_a_part_with_several_values_is_read_with_each_value_numbered`: "b", "B" kept as "b (1)", "B (2)" and asked once, blank labels
numbered, a label with the same answers twice and a blank beside lettered ones still refused), with `test_makeup_drafts`,
`test_grading_rubric_drafts`, `test_grading_rubric_versions`, `test_grading_prompt_contracts`, `test_auto_prepare`,
`test_grading_answers`, `test_grading_answer_groups`, `test_grading_answer_limits` and `test_drafting_tools`, through
`manage_checks.environment` on isolated NVMe scratch. Replayed read only on the live catalog: all nine answers refused so (Quiz 6 P4
B/C/D twice and A's first try; the Quiz 2 makeup's P3 E twice) are now read. No migration.
Approve all settles a lost answer, 2026-10-29 (REGRADES "APPROVE ALL" › A LOST ANSWER; the first live run stopped after the second of
16, which had gone through on the server): `tests/browser_regrade_all.py` (the first approval's answer dropped on the way with
`route.fetch()` then `route.abort()`: read back, never sent twice, the run goes on to 2 of 2), `test_regrade_followup` (the card's
read route, 404 for an unknown card), with `test_assistant`, `test_assistant_capabilities`, `test_followup_suggestions`, every
`tests/*.mjs`, the frontend typecheck and the browser checks regrade_edit and followup_suggestions, through
`manage_checks.environment` on isolated NVMe scratch. No migration.

Regrades tab and Approve all, 2026-10-29 (REGRADES "APPROVE ALL"; instructor: "Can we put regrade requests in their own tab in the
followup menu and then add an option to approve all"): `test_regrade_followup` (two new: keeps first lets the change after it go
through; a change approved first leaves the exam's keep stale, nothing submitted), `tests/test_followup_suggestions.mjs` (the tab's
filter, out of Active, `regradeApprovals` keeps first, approving and not-ready cards left out) and `tests/browser_regrade_all.py`
(the tab, the confirmation, the approvals' order, the change's Apply driven to the end), with `tests/test_student_support.mjs`
and the frontend typecheck, through `manage_checks.environment` on isolated NVMe scratch. No migration.

Class schedule changes checked before a card, 2026-10-28 (SCHEDULING "CLASS SCHEDULE: THE PLAN"; a follow-up's card adding a second
"In-class quiz" failed on Approve with "Two planned rows share a title"): `test_scheduling_class_schedule`
(`test_a_change_the_plan_would_refuse_never_becomes_a_card`: the clash refused by the preview with the title and date named and no
card made, a uniquely titled card showing its row and saved on Approve with the preview's fingerprint, the preview writing nothing,
a stale preview 409, another change's fingerprint refused), with `test_assistant`, `test_assistant_capabilities`,
`test_assistant_automation`, `test_assistant_outcomes`, `test_assistant_integration`, `test_followup_suggestions`,
`test_followup_redo`, `test_scheduling_service`, `test_scheduling_engine`, `test_dr_quizzes` and `test_aim_send`, through
`manage_checks.environment` on isolated NVMe scratch. No migration.

Sections alone start a quiz draft, 2026-10-28 (QUIZZES "NOTES SECTIONS FOR THE WRITER" › SECTIONS ARE MATERIAL ENOUGH; instructor:
"I shouldn't have to give additional information or files, it should automatically pull the relevant section's notes and question
bank questions for that section"): `test_quiz_drafts` (`test_the_notes_sections_chosen_for_a_draft_go_to_the_quiz_writer`: ticked
sections with no material or instructions drafted, nothing at all refused with what to give, an absent field read from the
instructions over the title), `tests/test_quiz_drafts_ui.mjs` (`namedSections`) and `tests/browser_quiz_drafts.py` (the boxes follow
the instructions until changed by hand), with `test_drafting_tools`, `test_course_notes`, `test_makeup_drafts`,
`test_grading_prompt_contracts`, `test_quiz_blueprints`, `test_accessible_quiz`, `test_question_bank` and `test_grading_sweep`,
through `manage_checks.environment` on isolated NVMe scratch. No migration.

MC answers labelled on Gradescope, 2026-10-28 (GRADESCOPE "MC LABELS"; instructor: "it say +0 A and the student chose A, but the
correct answer is actually B and they can't tell"): `test_gradescope_mc_labels` (the labels for exact, partial, either/or, blank and
every-answer questions; Faculty still reads a labelled item; Label answers renames every answer of the keyed versions and nothing
else, a person's item and an unkeyed version left alone, points/groups/positions unchanged, nothing graded, published or commented,
a second run renames nothing; a lost rename read back and never sent again, finished by Label again; a send queues a run that
waits ten minutes, an MC check labels answers met since, never after an unfinished run, and an exam linked before 2026-10-28T14:05Z
only after the instructor's own Label), `test_assistant`, `test_certification_automatic`, `test_regrade_requests`, `tests/test_grading_gradescope.mjs`
(the button and the run's state), with `test_catalog_migrations` (050 applied as new), `test_gradescope_mc_corrections`,
`test_gradescope_mc`, `test_gradescope_sheets`, `test_regrade_mc`, `test_gradescope_after`, `test_gradescope_create`,
`test_gradescope_create_sheets`, `test_gradescope_send` and `test_grading_certification`, through `manage_checks.environment` on
isolated NVMe scratch. Read only on the real site: Exam 2's four sections and three versions hold 558 answer items, every one
Faculty's key labels and whose points agree with it. Migration 050 applies at start; restart once the restart check is idle.

Lecture-notes sections for the quiz writer, 2026-10-28 (QUIZZES "NOTES SECTIONS FOR THE WRITER"; instructor: "can we add a menu
to the quiz drafting page that allows me to add sections from the notes and it will automatically know to pull those notes into
context when drafting?"): `test_quiz_drafts` (`test_the_notes_sections_chosen_for_a_draft_go_to_the_quiz_writer`: the options list
the notes' sections, unknown numbers refused, the chosen ones kept in the notes' order, sent to the writer on the first revision and
a revision, joined to the rubric writers' sections, a draft without them unchanged;
`test_bank_questions_of_the_chosen_sections_go_to_the_writer_and_a_reprint_is_refused`: questions under a chosen section or its
topic sent, retired and unchosen ones not, the per-section cap, a reprint refused and asked again), `tests/browser_quiz_drafts.py`
(the picker, by chapter, one ticked), with `test_drafting_tools`, `test_course_notes`,
`test_makeup_drafts`, `test_grading_prompt_contracts`, `test_quiz_blueprints`, `test_accessible_quiz` and
`tests/test_quiz_drafts_ui.mjs`, through `manage_checks.environment` on isolated NVMe scratch; the bank selection and the copy
check replayed read only on the live catalog (Quiz 5's Version D problems refused as reprints, its makeup not). No migration.

Regrade requests on multiple choice, 2026-10-28 (REGRADES "BUBBLE SHEETS"; GRADING "CERTIFY JUST THIS CHANGE" › ONE BUBBLE SHEET;
GRADESCOPE MC CORRECTIONS on Faculty's own adjustment; COMMUNICATIONS; instructor: "why arent MC regrades handled in faculty?", "extend
it"): `test_regrade_mc` (the capture reads a bubble-sheet version Gradescope keeps off its list; the request placed on the student's
sheet with its row's picture; keep corrects Gradescope through MC CORRECTIONS before the reply; a change saves a review of the sheet
and the key scores it; Faculty takes away its own adjustment; a decision names an answer, never lines; one student's sheet certified
by itself, two refused), `tests/test_regrade_panel.mjs` and `tests/test_followup_suggestions.mjs` (the row, the letters A to E,
keep is Faculty's reading), with `test_regrade_requests`, `test_regrade_panel`, `test_regrade_followup`, `test_gradescope_mc_corrections`,
`test_gradescope_mc`, `test_gradescope_sheets`, `test_communications`, `test_grading_certification`, `test_certification_automatic` and
`test_gradescope_after`, and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch. Read only on the
real site: 140-515 Exam 2 MC's versions are off the assignments list and their Regrade Requests pages read; 140-514 Version B Question 3
had an open request Faculty had never seen. No migration.

Rubric memories fitted to each question, 2026-10-28 (GRADING "RUBRIC MEMORIES" › FITTED TO EACH QUESTION, NEVER WRITTEN AGAIN, THE
COORDINATOR'S RUBRIC replaced; JEV "RUBRIC MEMORY FIT"; instructor: "can't we use a jev call when recalling rubric memories to ask if
it contradicts clarifications that I've provided or explicit contradictions from the rubric itself?", then "proceed"):
`test_rubric_memory` (the fit leaves a contradicted memory out of that question's request and keeps the stored input whole, every
memory left out sends no rules, no Jev / an uncertain receipt / no answer sends every memory, the fit state's instructor word, a
coordinator plan reads the rules and its pass is kept at once, a pass does not write again what was dropped), with
`test_grading_rubric_quality`, `test_grading_rubric_burden`, `test_grading_rubric_versions`, `test_grading_rubric_drafts`,
`test_grading_rubric_adapt`, `test_quiz_drafts`, `test_gradescope_rubric_import`, `test_gradescope_rubrics`,
`test_grading_certification`, `test_certification_automatic`, `test_grading_direct_read`, `test_grading_rubric_edits`,
`test_grading_rubric_invalidation`, `test_grading_rubric_links`, `test_grading_rubric_scope`, `test_jev` and `test_jev_papers` (423
tests) and `manage_checks.py quick`, through `manage_checks.environment` on isolated NVMe scratch (`FACULTY_TEXLIVE_ROOT` pointed at
the checkout's toolchain, which a worktree lacks). Replayed read only on the live catalog with Jev asked without receipts
(`/mnt/nvme/Teaching/tmp/rubric-memory-fit/`). No migration. Restart Faculty once the restart check shows `"idle": true`.

Questions nobody graded on Gradescope get Faculty's score, 2026-10-28 (GRADESCOPE MC CORRECTIONS › UNGRADED; instructor: "it
should work by defaulting to faculty's scores for ground truth, the MC compare is just to flag things for me to check manually"):
`test_gradescope_mc_corrections` (the answer Faculty read applied and read back, listed as "not graded → 4"; two filled bubbles
refused; `answer_letters`), with `test_gradescope_mc`, `test_gradescope_sheets`, `test_gradescope_after`, `test_gradescope_send`,
`test_gradescope_publish`, `test_certification_automatic` and `test_grading_certification` (366 tests), through
`manage_checks.environment` on isolated NVMe scratch; the fake's Download Grades says Ungraded while a question is. The real
grading pages of 140-515 Exam 2 Q7 and Q17 were read only (no evaluation, no item, items named by letters). No migration.

Gradescope's graded share to one decimal, 2026-10-28 (GRADESCOPE PUBLISHING GRADES warnings and "AFTER CERTIFYING" holds; instructor
saw Exam 2's 515 MC warning as "99.8547567175018%"): `test_gradescope_publish` (`test_the_graded_share_is_said_to_one_decimal_and_never_as_100_or_0`)
and `tests/test_grading_gradescope_publish.mjs` (the Graded there column), with `test_gradescope_after`, `test_grading_certification`,
`test_gradescope_mc`, `tests/test_grading_certification.mjs` and `tests/test_grading_gradescope.mjs`, through `manage_checks.environment`
on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

Send to AIM at the class's end plus 10 minutes, and after a quiz's approval, 2026-10-27 (SCHEDULING "SEND TO AIM" › DATES and
AFTER APPROVAL; instructor: "the quizzes should be scheduled for 10 minutes after the end of class for that student's section",
"This should ideally happen after a quiz is generated, not on some scheduled timer"): `test_aim_send`
(`test_the_time_is_ten_minutes_after_the_sections_class_ends_that_day`, and the send's posted hour and minute),
`tests/test_quiz_drafts_ui.mjs` (`aimMatches`) and `tests/test_dr_quizzes_page.mjs`, through `manage_checks.environment` on
isolated NVMe scratch. No migration.

Send to AIM, automatic parts, 2026-10-27 (SCHEDULING "SEND TO AIM" › RUNS, DUE ON HOME and the bookings import; instructor: "I
need this to be fairly automated. For all of the DR quiz takers, it needs to do what's necessary"): `test_aim_send`
(`AimSendAutomationTest`: Home's card counts the quizzes due and the writes waiting for Retry; the upload window of 5 to 3 business
days; the DR bookings import themselves and each send's run ends sent or stopped; the restart check waits for a send) and
`tests/test_dr_quizzes_page.mjs`, with `test_workspace_overview`, `test_dr_quiz_notices`, `test_aim_import`, `test_assistant` and
`test_catalog_migrations`, through `manage_checks.environment` on isolated NVMe scratch. Migration `scheduling/006_aim_send_runs`.

Send to AIM, 2026-10-27 (SCHEDULING "SEND TO AIM"; instructor: "it needs to schedule the quiz on the same date as the quiz is
scheduled for in Faculty and then upload the accessible version to the correct spot"): `test_aim_send` (AIM's own pages as saved,
scrubbed, in `tests/fixtures/aim`: the dates list, each section's form, Upcoming Exams and its count; a send adds each ready quiz as
a Quiz of 20 minutes with no time, marks it, uploads the accessible copy once per request and only to its own section's; a date
AIM already lists is only marked; an unconfirmed date or upload is held, settled by a later read or released by Retry; a refused
date fails; a page not as mapped stops before any post; the routes, the start against the fingerprint and the assistant policy)
and `tests/test_dr_quizzes_page.mjs` (the confirm, the summary, the receipts under the AIM chip), with `test_dr_quiz_notices`,
`test_dr_quizzes`, `test_aim_import`, `test_aim_capture`, `test_catalog_migrations`, `test_assistant`, `test_accessible_quiz`,
`test_app` and `test_scheduling_class_schedule`, through `manage_checks.environment` on isolated NVMe scratch. Migration
`scheduling/005_aim_sends`. Checks never sign in to AIM. Restart Faculty once the restart check shows `"idle": true`.

A rescanned bubble sheet is not skipped, 2026-10-27 (GRADESCOPE "BUBBLE SHEETS ARE UPLOAD ONLY" › A RESCAN IS NOT SKIPPED; instructor:
"what are all these skipped sheets when trying to send to gradescope exam 2"): `test_gradescope_sheets`
(`test_a_sheet_scanned_twice_is_counted_once_and_never_listed_as_skipped`) and `tests/test_grading_gradescope.mjs`, with
`test_gradescope_after`, `test_bubble_sheets`, `test_gradescope_create_sheets`, `test_certification_automatic` and `test_gradescope_mc`,
through `manage_checks.environment` on isolated NVMe scratch. Read only on live: Exam 2's 373 sheets are 324 to upload (324 students,
each once) and 49 repeats. No migration. Restart Faculty once the restart check shows `"idle": true`.

Accessible copy of a drafted quiz for the DR center, 2026-10-27 (QUIZZES "ACCESSIBLE COPY"; instructor: "it just needs to be screen
readable. I think WCAG 2.1 compliant"): `test_accessible_quiz` (a version as a Word file with Word equations, headings, title and
language, nothing hidden in it; no copy for a figure, raw LaTeX, unread math or unparsed text; tableaus, typed columns and blanks; the
term's read and the download) and `tests/test_dr_quizzes_page.mjs` (the Accessible copy column), with `test_quiz_drafts`,
`test_dr_quizzes`, `test_dr_quiz_notices`, `test_aim_capture`, `test_app` and `test_assistant`, through `manage_checks.environment` on
isolated NVMe scratch. Needs pandoc (default `/mnt/nvme/Teaching/tools/pandoc/pandoc-3.6.4/bin/pandoc`; the tests skip without it).
Built read only from every approved revision on the live catalog: Quizzes 4 and 5 (all versions) and both makeups convert; the
earlier Quiz 4 draft with a graph says it has a figure. No migration. Restart Faculty once the restart check shows `"idle": true`.

Agent memory review fixes, 2026-10-26 (ASSISTANT-MEMORY "A NARROWER OWNER NEVER REPLACES A WIDER ONE" › ONLY AN OWNER THE STATEMENT
SAYS and KEEPING's unsettled scope; GRADING "RUBRIC MEMORIES" › THE COORDINATOR'S RUBRIC and NOTHING ASKED TWICE's `EARLIER_ANSWERS`;
instructor: "fix all six"): `test_assistant_memory` (`test_an_unsettled_scope_is_kept_with_the_course_never_the_term`,
`test_a_correction_under_a_default_owner_replaces_the_wider_rule_and_takes_its_owner`), `test_rubric_memory`
(`test_a_plan_graded_to_the_coordinators_rubric_reads_no_rule_and_teaches_only_on_keep`), `test_grading_rubric_burden`
(`test_a_check_reads_the_latest_eighty_answers`), with `test_followup_learning`, `test_grading_rubric_quality`, `test_quiz_drafts`,
`test_grading_certification`, `test_gradescope_rubric_import`, `test_grading_uploads`, `test_memory_sandbox` and `test_assistant`,
through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

A paper with no seat is confirmed by its page 1, 2026-10-26 (GRADING "FIRST PAGE" › NO SEAT; instructor, of 12 Exam 2 papers with
no seat matching Version A 67-72% against B 44-49%: "why aren't the versions being assigned"): `test_grading_exam_versions`
(`test_without_a_seat_page_one_printing_the_matched_version_confirms_the_paper`, which fails without the fix,
`test_without_a_seat_page_one_naming_another_version_leaves_it_proposed`; JEV FALLBACK, instructor: "Why not ask Jev on the first page
as a fallback if this way fails": `test_without_a_seat_jev_on_page_one_decides_when_the_pages_match_no_version_clearly`,
`test_without_a_seat_a_split_jev_or_the_ocr_alone_leaves_an_unclear_paper_to_the_instructor`,
`test_without_a_seat_a_failed_comparison_takes_jevs_page_one`; two earlier tests now read page 1 before waiting) with
`test_grading_locations`, `test_frq_grading`, `test_grading_certification`, `test_gradescope_send`, `test_gradescope_after`,
`test_exam_grading`, `test_grading_intake`, `test_certification_automatic`, `test_pipeline_per_paper`, `test_bubble_sheets` and
`test_jev_papers`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check
shows `"idle": true`; discovery then reads those papers' first pages.

Jev proposes a scan's assignment, 2026-10-26 (DOCUMENTS "A SCAN WITHOUT LABELS FINDS ITS ASSIGNMENT" JEV PROPOSES; instructor: "have
Jev propose, and then if that assignment fits (ie the page counts line up) then it assigns that to the scan"): `test_assignment_finder`
(Jev's proposal chosen when the pages are whole papers, not when they are not or Jev reads two assignments; sheets whose question count
fits the answer key), with `test_unlabeled_exams`, `test_bubble_sheets` and `test_jev_papers` (78 tests), through `manage_checks.environment`
on isolated NVMe scratch. Replayed read only on the live catalog (files through the app's GET routes, Jev asked without receipts): seven
earlier scans, five chosen right, two nothing, none wrong; the last six Exam 2 sheet scans count 17 questions, as its key has. No migration.

Bubble sheets take the exam with an answer key, 2026-10-26 (DOCUMENTS "A SCAN WITHOUT LABELS FINDS ITS ASSIGNMENT"; instructor:
"why isnt this being autoassigned to exam 2"): `test_assignment_finder`
(`test_bubble_sheets_take_the_one_exam_on_the_calendar_with_an_answer_key`, which fails without the fix) with `test_bubble_sheets`
(45 tests), through `manage_checks.environment` on isolated NVMe scratch. Replayed read only on the live catalog: the 12 Exam 2 sheets
of 2026-10-26 16:42Z choose Exam 2 (before: nothing, five exams in the window). No migration.

A failed answer-area check settled by a confirmation is no failure on Home, 2026-10-26 (Exam 2's 16 failed template readings
stayed "Answer area checks: failed" after every area was confirmed): `test_workspace_overview`
(`test_a_failed_check_whose_areas_were_confirmed_is_no_failure`, which fails without the fix) with `test_grading_locations`, through
`manage_checks.environment` on isolated NVMe scratch. Replayed read only on the live catalog: the queue's 16 failed rows become 0.
No migration.

The bubble sheets go with the written send, 2026-10-26 (GRADESCOPE "THE BUBBLE SHEETS TOO"; instructor: "would it be bad to have the
send function send both?", then "yes"): `test_gradescope_send` (`test_the_bubble_sheets_go_with_the_send_first_and_once`) and
`tests/test_grading_gradescope.mjs` (the dialog's Bubble sheets part), with `test_gradescope_sheets`, `test_gradescope_after`,
`test_gradescope_mc` and `test_gradescope_create_sheets` (159 tests), through `manage_checks.environment` on isolated NVMe scratch,
and `manage_checks.py browser --browser grading_gradescope` (its send flow unticks the part once and sends it once). No migration.

An assignment of another kind is never proposed for its number alone, 2026-10-26 (GRADESCOPE "THE SAME KIND AND NUMBER"; instructor:
"Proceed", after Exam 2's send proposed Quiz 2): `test_gradescope_send`
(`test_an_assignment_of_another_kind_is_never_proposed_for_its_number_alone`), with `test_gradescope_sheets`, `test_gradescope_create`,
`test_gradescope_create_sheets`, `test_gradescope_after`, `test_gradescope_mc` and `test_gradescope_publish`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

The bubble-sheet exam made with the written one, 2026-10-26 (GRADESCOPE "THE BUBBLE-SHEET EXAM TOO"; instructor, of Create the
assignment on Gradescope for Exam 2: "is it going to create the MC too or just the FRQ?", then "go ahead and build it"):
`test_gradescope_create_sheets` (11 tests: the preview's own part and second name, both parts in one unit per course, only the bubble
sheets where the written one is linked, each part held only by its own problems, a one-version key, a name already there, never by
itself, lost answers read again, a key edited on Gradescope stops the unit) and `tests/test_grading_gradescope_create.mjs`, with
`test_gradescope_create`, `test_gradescope_send`, `test_gradescope_sheets`, `test_gradescope_after`, `test_gradescope_publish`,
`test_gradescope_rubric_import`, `test_gradescope_rubrics`, `test_gradescope_mc`, `test_gradescope_mc_corrections`, `test_bubble_keys`
and `test_grading_certification` (338 tests), through `manage_checks.environment` on isolated NVMe scratch. Rehearsed against the real
sandbox course only (guard.py), Faculty's own client and then its own sender. No migration. Restart Faculty once the restart check
shows `"idle": true`.

Fewer holds after the Exam 2 run, 2026-10-26 (GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS", "A SETTLED NOTE STAYS SETTLED",
"A GRADER'S REQUEST ON AN EMPTY ANSWER"; instructor: "build a b c and d"): 16 new `test_grading_fewer_holds` checks (offered
deductions only in a frozen batch, the scorer taking them off, refused decisions, a deduction disagreement, the narrowed schema, the
four new prompt versions as the old ones word for word plus three paragraphs, renamed notes settled by the flag review and the
arbitrator but never rubric wording beside the arbitrator, blank zeros with a grader's request or a whole-page area, and two through
a fake Cloud team: agreeing graders finalize 9.5 of 10, a split on the deduction goes to the arbitrator), then every
`test_grading_*` file with `test_jev_papers` and `test_claude_api_fallback` (71 files, 1,140 tests) and the 39 other files that
use grading (875 tests; `test_frq_providers` now names `grade-v8.txt`, and the four LaTeX files were rerun with the TeX Live
toolchain the worktree lacks), through `manage_checks.environment` on isolated NVMe scratch. Replayed read only on the live catalog: D settles exactly
the 16 renamed notes of the 19 Exam 2 grades the flag review settled that came back; A lets 25 of Exam 2's 32 held blank zeros reach
Jev (none did). No migration; no real model was asked.

Claude API fallback, 2026-10-25 (GRADING "CLAUDE API FALLBACK"): `test_claude_api_fallback` uses `fake_claude` to cover
subscription-first opt-in, same-model direct API retry, reset recovery, bundle cost sharing, unavailable keys, ordinary errors,
partial/unknown outcomes, API rate limits, missing cost and workspace headers for organization-level keys.
`browser_grading_subscription` checks the saved checkbox and editor.

Flag review applies approved extra deductions, 2026-10-25 (GRADING "FLAG REVIEW DEDUCTIONS"): 14 new
`test_grading_flag_adjustments` checks and 107 existing checks in `test_grading_flag_resolution`,
`test_grading_auto_flag_review`, `test_grading_arbitration`, `test_grading_fixes_scoring`, `test_grading_acceptance`
and `test_grading_fixes_decisions` passed through `manage_checks.environment` on isolated NVMe scratch, with
corrected synthetic fixtures rerun. Covers exact decimal adjustments through the carrier and acceptance gate,
invented/duplicate/omitted deductions, contradictory totals and explanations, floors, ordinary negative lines,
bonus wording, individual-response isolation, Opus-style deferral and legacy prompt/rule replay. Existing
reviews retain their rules; new reviews freeze v3 with its separate prompt. No migration or live grade rewrite.
The one-time `faculty-flag-deductions-activate` service waits for two idle checks before restarting web and Inbox;
it refuses activation if the tested production files changed in the meantime.

Flag review allows 100 concurrent question/version groups, 2026-10-25 (GRADING "FLAG REVIEW PARALLELISM"; instructor:
"allow up to 100 parallel requests for the flag review run"): `test_grading_flag_resolution` and
`test_grading_auto_flag_review` (34 tests) passed through `manage_checks.environment` on isolated NVMe scratch.
Only the per-run worker limit changes from 8 to 100; each question's chunks and full-page follow-ups still run in order.
No migration. Activate after the stopped review, only while `/api/assistant/restart-check` reports `"idle": true`.

Restore a flag review's original results, 2026-10-25 (GRADING "FLAG REVIEW RESET"; instructor: "reset this back to the 324
original flags"): `test_grading_flag_resolution`, `test_grading_auto_flag_review` and `test_assistant` (63 tests), plus the
new active-work/certification guard (1 test), and `tests/test_grading_run.mjs` (30 tests), through `manage_checks.environment`
on isolated NVMe scratch. Checks cover the exact original result and flag restoration, retained later results and model
receipts, no new model calls or automatic repeat review, stale previews, replayed request IDs, later instructor decisions,
busy/certified refusals, and the complete Assistant preview/Approve flow. No migration. Restart only while the idle gate is clear.

The spot a flag review leaves to be read is outlined on the crop, 2026-11-14 (GRADING FLAG REVIEW "THE SPOT TO LOOK AT";
instructor: "put a bbox around the specific spot that needs clarification"): `test_grading_flag_resolution` (33 tests; `Looks` and
five in `FlagResolution`) covers which spots are kept and that none is repaired, the look asked only for a response left with
reading choices and sent the note and readings alone, its call as one of the review's own, a failed or unusable look leaving the
note as it was, and an outline added after a finished review as an event the note reads (the review itself stays immutable).
`node --test tests/test_grading_review_layout.mjs` covers the outline on its own crop, the enlarged view of each spot beside the
choices (its own crop's picture, named, a note without spots unchanged), no inline style, the 1.2% margin and the part of the crop
a view shows; `browser_grading_review_queue.py` places the outline on the crop and in the full-size view and checks the view
beside the choices shows the right part of the scan, undistorted, and scrolls to its outline, and that at 1000 by 900, 1300 by
700 and 1500 by 1100 the paper and rubric keep their box under the whole note with the rubric's lines in it and the choices beside
the views (THE REST OF THE DESK KEEPS ITS SIZE), against synthetic data. Ten
related grading modules (248 tests), the frontend typecheck and `manage_checks.py quick` passed on isolated NVMe scratch, against
the fake host. No migration. Restart `faculty` and `faculty-inbox` after the change, once the restart check is idle.

Every reading choice is on the page, 2026-11-14 (GRADING FLAG REVIEW "EVERY CHOICE SHOWS"; instructor: "both of the options give
the same choice"): `browser_grading_review_queue.py` gives the note four readings and, on a 1300 by 700 window, finds no choice
outside the Decision needed box. It failed before the change (two of four scrolled out of the 30% cap). Styles only.

Flag review progress counts its actual requests, 2026-10-25 (GRADING "FLAG REVIEW PROGRESS"; instructor: "doesn't say 22/22 when
it's still working"): `test_grading_flag_resolution` and `test_grading_auto_flag_review` (30 tests), with
`tests/test_grading_run.mjs` (29 tests), through `manage_checks.environment` on isolated NVMe scratch. New regressions cover
29 completed replies plus seven active requests, full-page checks, repeated reads, usage-limit pauses and a recovered
request's old failure. `browser_grading_review_queue.py` passed against synthetic data: actual counts refresh while preserving
keyboard focus, the usage-limit wait names its retry time, finished grades still refresh their queue, and open rulings and
unchanged polls stay quiet. Inspected running and paused displays at 1440/1280 px. No migration or grading/retry change.
Activated by the one-time user service `faculty-flag-progress-restart-10dbc1ef` after the Sol review and its scoring finished:
two `/api/assistant/restart-check` reads reported `"idle": true` before web and Inbox restarted. The active review was not interrupted.

A rubric edited after a check is checked alone, 2026-10-25 (GRADING "A CHECK ASKS ONLY WHAT CHANGED"; instructor: "I just changed one
point value on the rubric and now it wants me to run a full rubric check again"): `test_grading_rubric_quality` (two new tests in
`QualityOnlyWhatChanged`; two pacing/retry tests now change every rubric to force a full re-check), with `test_grading_rubric_scope`,
`test_grading_rubric_edits`, `test_grading_rubric_versions`, `test_grading_rubric_drafts`, `test_quiz_drafts`, `test_rubric_memory`
and `test_grading_rubric_adapt` (186 tests) and `tests/test_grading_overflow.mjs`, through `manage_checks.environment` on isolated
NVMe scratch. The browser check was not run (instructor stopped it to land). Replayed read only on a snapshot of the live catalog:
Exam 2 would check only Version B Question 24 and carry the other 23 questions. No migration. Restart Faculty once the restart
check shows `"idle": true`.

A page with no mark at 300 DPI is read once more at 250, 2026-10-25 (DOCUMENTS SECOND LOOK; instructor: "it has a label though, why
wasn't the data matrix read?"): `test_documents` (`test_a_page_with_no_mark_at_300_dpi_is_read_once_more_at_250`, with the 300 DPI
test), with `test_document_corners`, `test_scan_stacks`, `test_bubble_sheets`, `test_sequence` and `test_scan_stray_pages` (159
tests), through `manage_checks.environment` on isolated NVMe scratch. Read only on live pages: three of the four Exam 2 sheets whose
Scantron label was not read now read it (each the student the instructor chose), and 298 booklet pages with no mark find nothing new.
No migration. Restart Faculty once the restart check shows `"idle": true`.

A sheet whose label was not read pairs by its handwriting, 2026-10-25 (BUBBLE-SHEETS "A SHEET SCANNED TWICE" › A REVIEWED COPY;
instructor: "why does it need me to select who it is when it has a label"): `test_bubble_sheets`
(`test_a_sheet_scanned_twice_is_one_sheet`: an unlabeled copy named by its UIN, with the marks of Alice's held copy, repeats it;
fails without the fix; `test_the_latest_review_counts_here_and_in_other_scans_exactly_as_the_earlier_loader_read_it` keeps a
confident Dylan with other marks yielding), with `test_bubble_keys`, `test_bubble_analysis`, `test_gradescope_sheets`,
`test_gradescope_mc`, `test_gradescope_mc_corrections`, `test_scan_stacks` and `test_grading_certification` (172 tests), through
`manage_checks.environment` on isolated NVMe scratch. Replayed read only on live: exactly Lyle Ensor's unlabeled Exam 2 copy
becomes a repeat; Exam 1 unchanged. No migration. Restart Faculty once the restart check shows `"idle": true`.

A reviewed copy of a sheet scanned twice is the sheet, 2026-10-25 (BUBBLE-SHEETS "A SHEET SCANNED TWICE" › A REVIEWED COPY;
instructor: "I reviewed this one, why is it still saying it needs review"): `test_bubble_sheets`
(`test_a_sheet_scanned_twice_is_one_sheet`: Alice's reviewed copy and a third, unlabeled copy of Carol's reviewed as her take the
labeled copies as repeats; fails without the fix), with `test_bubble_keys`, `test_bubble_analysis`, `test_gradescope_sheets`,
`test_gradescope_mc`, `test_gradescope_mc_corrections`, `test_scan_stacks` and `test_grading_certification` (172 tests), through
`manage_checks.environment` on isolated NVMe scratch. Replayed read only on live: exactly Eva Marsh's and Lena Maxwell's labeled
Exam 2 copies become repeats; no other sheet changes. No migration. Restart Faculty once the restart check shows `"idle": true`.

An erased bubble is not a doubt, 2026-10-25 (BUBBLE-SHEETS ERASED; instructor: "All of the MC marks that I've had to review for exam 2
seemed pretty obvious that the student erased and chose a different answer"): `test_bubble_sheets` (`reader.decide` on Exam 2's
real rows: 31% and 22% marks beside the answer are erased, 41% stays a double), with `test_bubble_keys`, `test_bubble_analysis`,
`test_gradescope_sheets`, `test_gradescope_mc`, `test_gradescope_mc_corrections` and `test_scan_stacks` (81 tests), through
`manage_checks.environment` on isolated NVMe scratch. Replayed read only on every live reading: the rule changes 11 Exam 2 questions
(9 already reviewed, each keeping the dark answer the instructor chose) and nothing on Exam 1. No migration. Restart Faculty once the
restart check shows `"idle": true`.

A stack scanned again whole is set aside by itself, 2026-10-25 (INBOX "BATCHES SCANNED IN STACKS" › A STACK SCANNED AGAIN WHOLE;
instructor: "keep the sheets"): `test_scan_stacks` (`test_a_stack_scanned_again_whole_is_set_aside_by_itself`, fails without the
fix), with `test_sequence`, `test_scan_stray_pages`, `test_pipeline_per_paper`, `test_grading_intake` and `test_inbox_scan_groups`
(106 tests), through `manage_checks.environment` on isolated NVMe scratch. Previewed read only on live: the two Exam 2 morning scans
plan 24 and 28 repeats and nothing else. No migration. Restart Faculty once the restart check shows `"idle": true`.

A rescanned bubble sheet is one sheet whatever its version bubble reads, 2026-10-25 (BUBBLE-SHEETS "A SHEET SCANNED TWICE" › THE
VERSION BUBBLE; instructor: "I thought the version is read from the booklet, so why is version getting hung up"):
`test_bubble_sheets` (`test_a_sheet_scanned_twice_is_one_sheet`: Carol's rescan reads her version bubble blank and still repeats),
with `test_bubble_keys`, `test_bubble_analysis`, `test_gradescope_sheets`, `test_gradescope_mc`, `test_gradescope_mc_corrections`
and `test_scan_stacks` (80 tests), through `manage_checks.environment` on isolated NVMe scratch. Replayed on a snapshot of the live
catalog: 18 rescanned Exam 2 sheets become repeats, their 18 first sheets are scored instead of waiting (52 waiting to 16), and no
other sheet changes. No migration. Restart Faculty once the restart check shows `"idle": true`.

A booklet scanned again keeps its version for the bubble sheets, 2026-10-25 (BUBBLE-SHEETS "VERSION FROM THE SEATING CHART" ›
A BOOKLET SCANNED AGAIN): `test_grading_exam_versions` (`test_a_booklet_scanned_again_keeps_its_version_for_the_bubble_sheet`: a
settled Version B booklet on an A seat, scanned again, waits for assembly and keeps B for its sheet), with `test_bubble_sheets`,
`test_bubble_keys`, `test_bubble_analysis`, `test_grading_certification`, `test_gradescope_sheets`, `test_gradescope_mc`,
`test_gradescope_mc_corrections`, `test_scan_stacks`, `test_pipeline_per_paper` and `test_frq_grading` (234 tests), through
`manage_checks.environment` on isolated NVMe scratch. Replayed on a snapshot of the live catalog: Exam 2's 33 sheets scored with
their seat's key while their rescanned booklets waited for assembly take their booklet's key, and the mean goes from 38.9 to 42.5 of
60; no other sheet changes. No migration. Restart Faculty once the restart check shows `"idle": true`.

Every view over all the scans reads each exam's booklets once, 2026-10-25 (PERFORMANCE "Memos last one read"; instructor: "have we
made sure this type of thing isn't going to happen in other situations?"): `test_bubble_sheets`
(`test_the_latest_review_counts_here_and_in_other_scans_exactly_as_the_earlier_loader_read_it`: the Inbox list and Home's Inbox
count read the booklets at most once; the Inbox list read them twice for two scans before), with `test_inbox`,
`test_inbox_scan_groups`, `test_workspace_overview`, `test_grading_intake`, `test_gradescope_sheets`, `test_gradescope_send`,
`test_grading_certification`, `test_certification_automatic`, `test_scan_stacks`, `test_pipeline_per_paper`,
`test_assignment_workspace`, `test_bubble_keys` and `test_grading_scan_records` (460 tests), through `manage_checks.environment` on
isolated NVMe scratch. Replayed on a snapshot of the live catalog: the Inbox list, its Needs review tab and Home's Inbox count are
byte-identical to before. No migration. Restart Faculty once the restart check shows `"idle": true`.

Assignments list reads each exam's booklets once, 2026-10-25 (PERFORMANCE "Memos last one read"; instructor: "why does it take so
long to load when clicking the assignments tab"): `test_bubble_sheets`
(`test_the_latest_review_counts_here_and_in_other_scans_exactly_as_the_earlier_loader_read_it`: one view's memo reads the booklets,
repeats and each other run's claims once), with `test_assignment_workspace`, `test_bubble_keys`, `test_grading_exam_versions`,
`test_grading_intake`, `test_exam_overview`, `test_scan_stacks`, `test_grading_scan_records`, `test_inbox_scan_groups`,
`test_gradescope_sheets`, `test_bubble_analysis`, `test_frq_grading`, `test_grading_certification`, `test_pipeline_per_paper` and
`test_exam_grading`, through `manage_checks.environment` on isolated NVMe scratch. Replayed on a snapshot of the live catalog: the
list, every assignment's overview and each plan's grading summary answer byte-identically to before (but their read times). No
migration. Restart Faculty once the restart check shows `"idle": true`.

Jev reads page 1 first, 2026-10-25 (GRADING "FIRST PAGE" › JEV PAGE ONE): `test_grading_exam_versions`
(`test_jev_reads_page_one_first_and_a_split_answer_falls_back`, `test_jev_naming_another_version_than_the_seat_still_compares_the_whole_paper`,
with the FIRST PAGE tests), `test_bubble_sheets`, `test_grading_certification`, `test_frq_grading` and `test_pipeline_per_paper`, through
`manage_checks.environment` on isolated NVMe scratch, with a stand-in for Jev. Measured read only against live Jev: Exam 2 312/312,
Exam 1 338/338 first pages right. No migration. Restart Faculty once the restart check shows `"idle": true`.

Bubble sheets take the booklet's version, 2026-10-25 (BUBBLE-SHEETS "VERSION FROM THE SEATING CHART" › THE BOOKLET'S VERSION):
`test_bubble_sheets` (`SeatVersionTests.test_the_booklets_settled_version_comes_before_the_seat`), `test_grading_exam_versions`
(`booklet_versions` in `test_page_one_naming_another_version_compares_the_whole_paper`), with `test_bubble_keys`,
`test_bubble_analysis`, `test_gradescope_mc`, `test_gradescope_mc_corrections`, `test_gradescope_sheets`,
`test_grading_certification` and `test_scan_stacks`, through `manage_checks.environment` on isolated NVMe scratch. No migration.
Restart Faculty once the restart check shows `"idle": true`.

Exam versions confirmed from page 1, 2026-10-25 (GRADING "FIRST PAGE"; instructor: "it should just read the first page of their
exam booklet to confirm because it literally says the version"): `test_grading_exam_versions`
(`test_page_one_printing_the_seats_version_confirms_the_paper_without_comparing_it`, `test_page_one_naming_another_version_compares_the_whole_paper`,
`test_a_whole_paper_comparison_already_waiting_is_set_aside_once_page_one_agrees`, `test_page_one_is_read_for_one_version_and_never_from_a_seat_label`,
`test_page_one_and_the_pages_disagreeing_with_each_other_wait_for_the_instructor`, `test_a_paper_compared_before_page_one_was_read_is_read_before_it_waits`),
`test_grading_locations` (`VersionCheckProgress`), with `test_frq_grading`, `test_grading_certification`, `test_gradescope_send`,
`test_gradescope_after`, `test_exam_grading`, `test_grading_intake`, `test_certification_automatic` and `test_pipeline_per_paper`
(417 tests), through `manage_checks.environment` on isolated NVMe scratch. Read only on the live Exam 2 scans: all 312 first pages
read one letter, 24 per model load in about 36 s (8 minutes in all); 244 match the seat and 68 do not, and every whole-paper
comparison checked against them (4 of 4) agreed with page 1. No migration. Restart Faculty once the restart check shows `"idle": true`.

The registrar's mark is a drop, 2026-10-25 (CANVAS "LEAVING THE SECTION" › THE REGISTRAR'S MARK IS A DROP): `test_rosters`
(`test_q_drops_and_withdrawals_are_dropped_by_the_import_and_absent_students_are_offered`: a Q drop and a withdrawal are dropped by
the import with their reasons, a left-out student is only offered, a restored student stays current through later imports),
`test_seating` and `test_response_cards` (the marked students are dropped and still get no seat or card), with `test_canvas`,
`test_dr_quizzes` and `test_scheduling_class_schedule`, and `manage_checks.py browser --browser rosters` (the roster says what the
import dropped), through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check
shows `"idle": true`.

Papers and sheets scanned twice, 2026-10-24 (INBOX "BATCHES SCANNED IN STACKS" › A PAPER SCANNED IN TWO STACKS, BUBBLE-SHEETS "A SHEET
SCANNED TWICE"; instructor: "automatic"): `test_scan_stacks` (`test_a_paper_scanned_in_two_stacks_is_kept_once_and_the_rest_group`),
`test_bubble_sheets` (`test_a_sheet_scanned_twice_is_one_sheet`), with `test_sequence`, `test_scan_stray_pages`,
`test_pipeline_per_paper`, `test_grading_intake`, `test_bubble_keys`, `test_bubble_analysis`, `test_gradescope_mc`,
`test_gradescope_mc_corrections` and `test_grading_certification`, through `manage_checks.environment` on isolated NVMe scratch.
Replayed on a copy of the live catalog: the 6:12 pm stack groups its 14 other papers and asks nothing; Pearson's and Blakely's
second sheets are repeats. No migration. Restart Faculty once the restart check shows `"idle": true`.

Clipped stickers and pages that need a look, 2026-10-24 (OCR "STICKER ALIASES"; INBOX; instructor: "there is no way to know what
to look at", "why didn't this get grouped as one paper"): `test_scan_stacks`
(`test_a_paper_whose_sticker_code_was_not_read_is_found_by_its_printed_alias`: the held paper's first page alone is read, the alias
identifies it and the stack groups), `test_sequence` (a conflicting page lists where else its label was read, and stops once that
scan is deleted), with `test_scan_stray_pages`, `test_alias_ocr`, `test_pipeline_per_paper`, `test_grading_intake` and
`test_documents`, plus `node --test tests/test_scan_flow.mjs` and the frontend typecheck, through `manage_checks.environment` on
isolated NVMe scratch. Live OCR on the three clipped stickers of the day read each alias exactly. No migration. Restart Faculty
once the restart check shows `"idle": true`.

Seat labels name the student's version and section, 2026-10-25 (DOCUMENTS "SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION"):
`test_print_seating` (`test_a_whole_room_prints_and_labels_in_seat_order_across_sections`: a chart with two versions, every room
label on Avery sheets and the label maker names its seat's version and its own section, never "Sections 507, 508";
`test_a_seat_label_names_its_version_and_keeps_a_table_seat_whole`), with `test_bubble_sheets`, `test_scan_stacks`, `test_sequence`
and `test_document_corners`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the
restart check shows `"idle": true`.

Grading entry read once, 2026-10-24 (PERFORMANCE; instructor: scan pages "aren't loading"): `grading_entry` reads the scans
holding a page of the exam's assembled papers in one subquery instead of once per Inbox message. Replayed on a copy of the live
catalog, every grading plan's rows are identical, order included, and the seven plans took 0.02 s instead of 41 s (the Exam 2
workspace read took 45 s and held a server thread, so scan pages timed out). `test_grading_intake`, `test_assignment_workspace`,
`test_exam_grading` and `test_pipeline_per_paper` through `manage_checks.environment` on isolated NVMe scratch. No migration.

Bubble-sheet versions from the seating chart, 2026-10-24 (BUBBLE-SHEETS "VERSION FROM THE SEATING CHART"; instructor: "Yes"):
`test_bubble_sheets` (`SeatVersionTests`: a labelled sheet with no, faint or a double version takes the chart's; a letter the chart
does not give is asked; UIN-matched sheets and students without exactly one chart version are as before), with `test_bubble_keys`,
`test_bubble_analysis`, `test_gradescope_mc`, `test_gradescope_mc_corrections` and `test_gradescope_sheets`, through
`manage_checks.environment` on isolated NVMe scratch. Replayed on a copy of the live catalog: 121 of the 128 Exam 2 sheets of
2026-10-24 come out clean (112 by the chart), 7 still need review (2 bubbled C against the chart, 4 faint answer marks, 1 faint
version the chart did not settle). No migration. Restart Faculty once the restart check shows `"idle": true`.

Home-scanned bubble sheets, 2026-10-24 (BUBBLE-SHEETS "HOME-SCANNED SHEETS" and "A SCAN ITS LABELS ASSOCIATED", INBOX "BATCHES
SCANNED IN STACKS" › BUBBLE SHEETS IN THE STACK): `test_bubble_sheets` (`test_a_sheet_a_home_scanner_cropped_to_the_paper_edge_reads_as_a_letter_one`,
`test_marks_5_keeps_what_marks_4_looked_at_and_looks_again_at_what_it_passed_over_for_its_size`), `test_scan_stacks`
(`test_bubble_sheets_beside_the_booklets_are_no_sides_of_any_paper`: Scantron-labeled and unlabeled sheets at the Epson's size
between the booklets, backs that read as written, one automatic run, each paper its own four sides) and `test_grading_intake`
(`test_bubble_sheets_on_a_scan_its_labels_associated_are_read_for_that_assignment`), with `test_sequence`, `test_scan_stray_pages`,
`test_assignment_finder`, `test_pipeline_per_paper` and the bubble-sheet suites, through `manage_checks.environment` on isolated NVMe
scratch. No migration: the detector is renamed `marks-5`, and `recovered-label-grouping-v2` has the worker look at every scan's
grouping once more (on 2026-10-24 only that day's four home-scanned stacks could group). Restart Faculty once the restart check
shows `"idle": true`.

Fewer rubric questions, 2026-10-24 (GRADING "FEWER RUBRIC QUESTIONS": bookkeeping fixed with Undo, nothing asked twice, rubric
choices before drafting, a window that shows only the decisions; QUIZZES "RUBRIC CHOICES"; instructor: "I actually like all of these
ideas, implement them"): `test_grading_rubric_burden` (what may be fixed and what stays a decision, a shared key fix per version, a key
fix made once and undone exactly, one that cannot be made asked, guidance fixes, earlier answers in the check's input and out of its
fingerprint, the memory pass after a finalization, choices saved, refused, read by the check and kept out of the fingerprint, and the
seven new prompt versions), `test_quiz_drafts` (`test_the_quick_rubric_choices_reach_the_writers_the_drafts_check_and_the_approved_plan`),
with `test_grading_rubric_quality`, `test_grading_rubric_edits`, `test_grading_rubric_links`, `test_grading_rubric_scope`,
`test_grading_rubric_drafts`, `test_grading_rubric_versions`, `test_grading_rubric_adapt`, `test_grading_rubric_invalidation`,
`test_rubric_memory`, `test_grading_prompt_contracts` and `test_catalog_migrations` (migration 049 applied as new), and
`node --test tests/test_grading_rubric_concerns.mjs tests/test_quiz_drafts_ui.mjs`, through `manage_checks.environment` on isolated
NVMe scratch. Browser: `grading_rubric_quality` (the window opens on Decisions with nothing to decide; the new tick), `quiz_drafts`,
`grading_rubric_drafts`. Migration 049 (`frq_rubric_choices`). Restart Faculty once the restart check shows `"idle": true`.

Whole-codebase refactor sweep, 2026-10-24 (PERFORMANCE, STORAGE, PIPELINE, FRONTEND and owning module contracts):
coverage and per-module results are recorded in `docs/refactor-sweep/`. Run `manage_checks.py full` for every synthetic Python
check, browser JavaScript parsing, Node regressions and the frontend typecheck. All runs use the manager's isolated
configuration and NVMe scratch; never point them at the live catalog or real outside services.
`manage_checks.py browser --browser course_lifecycle --browser seating_lifecycle --browser seating --browser seating_versions
--browser seating_updates` covers stale course/chart reads, restoring an entirely dropped roster, retry after a failed drop,
delayed saves keeping a replacement dialog, search focus through a seating refresh, and normal seating workflows.
`test_decisions_sandbox.StoreTests` includes interrupted-provider recovery and failure to sync the receipt log's directory;
`test_memory_sandbox` checks that a local budget refusal remains retryable. The full run also covers the added storage,
Inbox, grading, integration, picture-search and request-validation regressions. Frontend changes require a restart only after
`GET /api/assistant/restart-check` returns `"idle": true`.

A misspelt rubric filename names its question, 2026-10-25 (GRADING rubric-first setup): `test_grading_uploads`
(`test_a_misspelt_filename_still_names_the_question`: "8_Quesiton_25.csv" gives 25, "Points_4.csv" nothing), with
`test_grading_locations`, `test_gradescope_rubric_import` and `test_auto_prepare` (105 tests), through `manage_checks.environment`
on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

Boxes from Gradescope confirm themselves, 2026-10-25 (GRADING "ANSWER AREAS CONFIRM THEMSELVES" › BOXES FROM GRADESCOPE):
`test_auto_prepare` (`test_boxes_from_gradescope_confirm_themselves_though_the_reading_failed`: a failed check of imported boxes
suggests them and the worker confirms them, `manual_review` with its note; other failed checks still wait),
`test_gradescope_rubric_import` (a misspelt "Quesiton 25" gives 25; "Points 4" and "Part 2" give nothing), with
`test_grading_locations` and the Gradescope suites, through `manage_checks.environment` on isolated NVMe scratch. No migration.
Restart Faculty once the restart check shows `"idle": true`.

Exam 2 in one import and deductions in a positive rubric, 2026-10-25 (GRADESCOPE THE EXAM'S KEY COMES WITH ITS RUBRICS, GRADING
"DEDUCTIONS IN A POSITIVE RUBRIC"): `test_gradescope_rubric_import` (a written exam proposes its keyed bubble-sheet half; one import
saves the rubrics and the key, `sheet_id` "" leaves it out, a stale key fails alone; a -1 line imports and the send still matches
it), `test_gradescope_rubrics` (a positive rubric scores a checked -1 as Gradescope does, floor included; a deducting rubric still
refuses a bonus), with the grading suites that use positive rubrics (`test_quiz_drafts` needs `FACULTY_TEXLIVE_ROOT` outside the main
checkout), through `manage_checks.environment` on isolated NVMe scratch. Browser: `grading_gradescope_rubrics`. No migration.
Restart Faculty once the restart check shows `"idle": true`.

Bubble sheet keys from Gradescope, 2026-10-25 (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE" › THE BUBBLE SHEET KEY COMES TOO):
`test_gradescope_rubric_import` (a bubble-sheet exam is listed; its versions' keys read from the version set's Edit page and each
version's outline, "?" left out, saved as the plan's key revision with the most usual points and scoring as defaults; a stale
revision is refused; a scoring type Faculty does not score stops it; the coordinator's course is read with GETs only), with
`test_bubble_keys` and the Gradescope suites, through `manage_checks.environment` on isolated NVMe scratch. Browser:
`grading_gradescope_rubrics`, `bubble_keys`. No migration. Restart Faculty once the restart check shows `"idle": true`.

The coordinator's course in the rubric import, 2026-10-25 (GRADESCOPE "THE COORDINATOR'S COURSE"): `test_gradescope_rubric_import`
(`test_the_coordinator_course_is_read_only_for_the_import`: listed last and never proposed; importing from it reads only its
assignments, outline and rubric pages with GETs, no forbidden hit; the roster, `guard` and every read without `coordinator_read`
are still refused), with the Gradescope suites (whose fake still answers anything else there 403), through
`manage_checks.environment` on isolated NVMe scratch. Browser: `grading_gradescope_rubrics`. No migration. Restart Faculty once
the restart check shows `"idle": true`.

Template and answer areas from Gradescope, 2026-10-24 (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE" › THE TEMPLATE AND ANSWER
AREAS COME TOO): `test_gradescope_rubric_import` (each version's template is saved once as an Assignment Template and each
question's boxes become its answer areas on it; the check's context and `locations.combine` suggest them; a template of other pages
is left out with a note; `areas: false` imports the rubric alone; a template link outside Gradescope's uploads store is never read),
with `test_grading_locations`, `test_auto_prepare`, `test_assignment_templates` and the Gradescope suites, through
`manage_checks.environment` on isolated NVMe scratch. Browser: `grading_gradescope_rubrics`. No migration. Restart Faculty once the
restart check shows `"idle": true`.

Rubrics from Gradescope, 2026-10-24 (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE"): `test_gradescope_rubric_import` (against
FakeGradescope: the preview proposes the linked course and assignment and writes nothing; an import of a version set makes draft
questions whose export numbers the send's `map_question` matches; a repeat imports nothing; a question already in Faculty, one
without items and one whose points Faculty refuses fail alone with their reason; negative scoring imports deductions and still
matches; the coordinator's course is refused), with `test_gradescope_send`, `test_gradescope_create`, `test_gradescope_rubrics`,
`test_grading_uploads` and `test_grading_exam_versions`, through `manage_checks.environment` on isolated NVMe scratch. Browser:
`grading_gradescope_rubrics` (the dialog end to end against FakeGradescope) and `grading_uploads`. No migration. Restart Faculty
once the restart check shows `"idle": true`.

Sweep review fixes, 2026-10-24 (instructor: restore the Exams grading cache; PERFORMANCE, GRADEBOOK ZERO ONCE DUE, ASSISTANT,
SCHEDULING): `test_exam_overview` (the 120 s per-plan grading read is back, with its audit-marker invalidation),
`test_canvas_posting` (`test_an_assignment_with_no_current_students_keeps_the_last_read`: such a read records nothing, so a posted
column keeps imputing 0s), `test_student_support` (the follow-up list keeps its fields), `test_assistant*` (an actions poll takes
the approve lock only when something has expired), `test_memory_sandbox`, `test_makeup_booking`, through `manage_checks.environment`
on isolated NVMe scratch. Also (instructor: "fix them"): a question-bank, Response Card or Assistant write whose dialog closed
meanwhile still reloads and says what happened; Gradescope create reads its preview again after a pause in typing; an automatic
version add or after-certifying creation waits out a publication instead of recording it as a problem (with `test_gradescope_after`
and `test_gradescope_create` unchanged); a custom version-sort pattern may take 5 s (`test_version_sort`). Browser: `question_bank`,
`response_cards`, `assistant`, `grading_gradescope`, `documents`, `gradebook`, `exams`, `version_sort`. No migration. Restart
Faculty once the restart check shows `"idle": true`.

Shared connection and viewer fixes, 2026-10-24 (PERFORMANCE, ASSISTANT VIEWER): `test_connection_reuse` covers state-changing
statements and thread checks on reused shortcut cursors; `test_file_viewer.RenderCacheTests` covers a failed PDF render with
concurrent waiters and pages evicted while their batch is cached, including a page larger than the cache budget.
`test_file_viewer.mjs` covers malformed print-route encoding, and the frontend typecheck covers the viewer's constructor.
`test_followup_suggestions`'s remembered-note fixture now uses a general instruction for its successful case, consistent
with ASSISTANT-MEMORY "POINTED AT THE STUDENT ON SCREEN"; its student-specific refusal remains covered.
Run `python3 -B manage_checks.py full` on isolated NVMe scratch, and `python3 -B manage_checks.py browser --browser documents`
for the viewer's interaction check. No migration. Restart Faculty and the Inbox worker only after the restart check says
`"idle": true`.
Keep custom scratch paths short when grouping tests: the fake drafting CLI's Unix socket also lives beneath `TMPDIR`, and
long group directory names can exceed the operating system's socket-path limit.

No flags after the exam, 2026-10-23 (RESPONSE-CARDS "NO FLAGS AFTER THE EXAM"): `test_workspace_overview`
(`OverviewSeatAndCardTests`: a flagged card leaves Home and the attention strip once its chart's exam day is past and returns while
it is ahead; the issue page still counts it), with `test_response_cards`, `test_response_card_invalidations`, `test_exam_overview`,
`test_seating`, `test_seating_updates` and `test_makeup_booking`, through `manage_checks.environment` on isolated NVMe scratch. No
migration. Restart Faculty once the restart check shows `"idle": true`.

Closed cards keep their status, 2026-10-23 (RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › THEY KEEP THEIR STATUS):
`test_seating` (`test_confirmation_prepares_seat_and_dr_cards_from_the_review_window`: after the close at midnight, Bob's answered
card stays Completed, Carol's flagged DR card stays Answers need review, unanswered cards read No response, a card revoked earlier
stays red and is not closed again, and the chart's counts, list row and seat-update count are unchanged), with `test_seating*`,
`test_response_card*`, `test_workspace_overview`, `test_exam_overview` and `test_makeup_booking`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

Resolved with its tasks, 2026-10-22 (TEACHING-WORKFLOWS SEPARATE COMMITMENTS › RESOLVED WITH ITS TASKS): `test_followup_connect`
(`test_resolving_from_the_chat_completes_the_follow_ups_open_tasks_with_what_finished_them`: the chat's Resolved on a follow-up with
two open dependent tasks is refused without `done`, and with it completes both with that note in the card's own revision, which
then saves), with `test_followup_redo`, `test_assistant_captures`, `test_student_support` and `test_followup_suggestions`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

MC corrections, 2026-10-22 (GRADESCOPE "MC CORRECTIONS", THE MC CHECK, AFTER CERTIFYING): `test_gradescope_mc_corrections` (a
read finds Bob's Question 3 scored 0 on Gradescope and 4 by Faculty; the sender sets a +4 point adjustment with the applied item and
comment unchanged, reads it back, reads the scores again once, and lists it as a check to confirm; checking again writes nothing; a
hand adjustment on Gradescope, a replaced sheet and a lost answer are refused or failed, never resent, and only the instructor's
Check MC tries again), `test_gradescope_after` (`AfterSheetsCorrectedTests`: the run's read, the correction and the read after it,
then the section is published), `test_grading_gradescope.mjs` (the panel lists each correction), with `test_gradescope_mc`,
`test_gradescope_sheets`, `test_gradescope_send`, `test_gradescope_publish`, `test_gradescope_create`, `test_gradescope_rubrics`,
`test_grading_certification`, `test_certification_automatic`, `test_certified_archive`, `test_grading_restore_certified` and
`test_catalog_migrations` (migration 048 applied as new), through `manage_checks.environment` on isolated NVMe scratch, against
`tests/fake_gradescope.py` (bubble-sheet versions laid out with questions as the real one read 2026-10-22). Migration 048. Restart
Faculty once the restart check shows `"idle": true`.

A grade as certified, 2026-10-22 (REGRADES "APPLY" › A GRADE AS CERTIFIED): `test_regrade_panel`
(`test_keeping_a_grade_as_certified_is_the_reply_alone_whatever_else_changed`: another grade changed since certifying, and keeping
the grade is still the reply alone) and `test_regrade_followup` (`test_a_keep_card_after_a_student_moved_since_certifying_posts_the_reply_alone`:
a student added since certifying, and an approved Keep card finishes after the reply with no Gradebook stop), with
`test_grading_certification` and `test_absence_makeups`, through `manage_checks.environment` on isolated NVMe scratch. No
migration. Restart Faculty once the restart check shows `"idle": true`.

Answer areas and answer keys confirm themselves, 2026-10-22 (GRADING "ANSWER AREAS CONFIRM THEMSELVES", "ANSWER KEYS SAVE
THEMSELVES", PIPELINE): `test_auto_prepare` (a suggested area confirmed as suggested with actor `automatic` and the question then
grades; an area that needs review confirmed with its note; a failed check and a graded question left alone; the worker step
looks once the record changes; drafted rubrics go from suggested areas to saved keys and the rubric check without a click; an
uploaded rubric's key read and saved; a graded question keeps its proposal), `test_pipeline_improvements` (the fifth pipeline
step gets its turn), with the grading, quiz, makeup, workspace, Gradescope, certification and regrade modules, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

Stale follow-ups, 2026-10-22 (TEACHING-WORKFLOWS WAITING ON › EVIDENCE CLOSES, CHECK BACK): `test_followup_evidence_closes`
(a Waiting on student follow-up resolves with Undo once Jev reads an absence decided since as what it waits for; unsure, still
owed, from before it was set waiting, Not excused automatically, a newer student message or no Jev leave it waiting, and a kept
receipt is never asked again; the check-back day follows the latest day its next step names, else a week; back on it, it needs
attention, is never prepared by itself, and Wait a week dates it), with `test_student_support.mjs` (CHECK BACK: Still waiting,
Resolved and Wait a week), `test_followup_suggestions`, `test_followup_deadline_closes`, `test_student_support` and the
`test_assistant*` and `test_jev*` modules, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart
Faculty once the restart check shows `"idle": true`.

A redo while preparing, 2026-10-21 (ASSISTANT "REDO FROM CHAT" › A REDO WHILE PREPARING): `test_followup_redo`
(`test_cailyne_a_redo_while_the_follow_up_is_still_being_prepared_stops_it_and_runs_next`: a second chat redo while the first is
running stops it, withdraws its cards as replaced at your request, and queues the new run, whose prompt carries both changes), with
`test_followup_suggestions` and the `test_assistant*` modules, through `manage_checks.environment` on isolated NVMe scratch. No
migration. Restart Faculty once the restart check shows `"idle": true`.

Who moved, and a whole certify settles a regrade, 2026-10-21 (REGRADES "APPLY" › WHO MOVED, CERTIFIED WITH THE WHOLE
ASSIGNMENT): `test_regrade_panel` (`test_a_student_moved_since_certifying_is_named_and_certifying_the_whole_assignment_settles_the_step`:
an absence excused after certifying stops the gradebook step naming the student and their old and new standing; certifying the
whole assignment again reads the step as already done and the run finishes with the new value in the gradebook), with
`test_regrade_followup`, `test_grading_certification` and `test_absence_makeups`, through `manage_checks.environment` on isolated
NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

Rubric note cards, 2026-10-21 (TEACHING-WORKFLOWS "RUBRIC NOTE CARDS"): `test_regrade_followup`
(`test_a_rubric_note_card_says_the_line_the_added_words_and_the_grades_it_reopens`: titled by its question, the line by number,
only the added words on the summary, no Sha256 field, the automatic grades it sends back to review), with
`test_followup_suggestions`, `test_grading_rubric_edits`, `test_assistant_capabilities` and `test_regrade_panel`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

See their work on a regrade follow-up, 2026-10-21 (REGRADES "SEE THEIR WORK"): `test_regrade_followup`
(`test_see_their_work_names_the_answer_a_regrade_conversation_is_about`: the conversation key names the Return panel's answer,
an unknown key gives a reason, a non-regrade key is refused, nothing reaches Gradescope), `tests/browser_regrade_work.py` (the
popup's grade, applied lines, folded lines and crop, which enlarges; read-only; Close; an unplaced request says why), with
`test_student_support.mjs`, `test_followup_suggestions.mjs` and `tests/browser_regrade_edit.py`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

iClicker credit before the import, 2026-10-21 (ICLICKER "CREDIT BEFORE THE IMPORT", ASSISTANT "ICLICKER CREDIT BEFORE THE
IMPORT"): `test_iclicker` (`AfterClassTests`: a credit given during class filled over the after-class 0 with its note, kept by a
later import, a replay and a second credit for the day are the same credit, withdraw puts the 0 back; a credit after the import
fills at once; a future date, a bad date, no reason and an unlinked section refused; the follow-up facts give the route and the
credit), `test_catalog_migrations` (014 applied as new), with `test_gradebook`, `test_gradebook_imports`, `test_absences`,
`test_followup_suggestions`, the `test_assistant*` modules and `test_gradebook_changes.mjs`, through `manage_checks.environment`
on isolated NVMe scratch. Migration gradebook 014 (new table). Restart Faculty once the restart check shows `"idle": true`.

"She" on an open follow-up names its student, 2026-10-20 (ASSISTANT-MEMORY "POINTED AT THE STUDENT ON SCREEN"):
`test_assistant_memory` (`RuleTests.test_she_on_an_open_follow_up_names_its_student`: kept only as the rule written without
the student, nothing kept when its check refuses, a "she" with no follow-up open left to Jev; `DecisionTests` for
`decide_capture`/`decide_remembered` with `pointed`), with `test_memory_sandbox` and `test_followup_learning`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart check shows `"idle": true`.

Full credit nobody ticked on a negative rubric, 2026-10-19 (GRADING, `empty-selection-v2`): `test_grading_teams`
(`test_a_negative_rubric_grade_that_ticks_nothing_is_full_credit_nobody_ticked`: held with `EMPTY_FULL`, not v1's "has a line for
no answer"; still held when nothing else holds it), `test_grading_flag_resolution` (`EMPTY_FULL` is sent to the flag review),
`test_grading_review_queue`, with `test_grading_pipeline`, `test_grading_arbitration`, `test_grading_standard`,
`test_grading_auto_flag_review`, `test_grading_acceptance`, `test_grading_certification`, `test_grading_rubric_quality` and
`test_jev_papers`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty once the restart
check shows `"idle": true`.

Seven-millimetre corner codes, 2026-10-18 (DOCUMENTS "SEVEN-MILLIMETRE CORNER CODES"): `test_document_corners`
(`test_the_7mm_layout_packs_the_token_and_prints_no_alias`: two packed 20x20 marks per page, validated at 300 DPI, no alias
text, either corner alone, a scan read through `extract_scan`, a changed token refused; `test_labels_of_7mm_paper_keep_the_10mm_mark`
on Avery and DK roll media; `test_cover_reads_white_out_each_layouts_stamp_boxes`; the class default compiled, which needs
`FACULTY_TEXLIVE_ROOT`), with the stamping, scan, quiz, print and OCR modules (`test_documents`, `test_alias_ocr`,
`test_quiz_drafts`, `test_question_bank`, `test_print_seating`, `test_term_print_jobs`, `test_makeup_booking`, `test_sequence`,
`test_unlabeled_exams` and the rest that stamp or read pages) and `manage_checks.py browser --browser documents`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty and the Inbox worker once the restart check
shows `"idle": true`.

Scan codes read at 300 DPI, whole page only, 2026-10-18 (instructor): `test_documents`
(`test_scan_codes_are_read_at_300_dpi_and_located_on_the_200_dpi_page`: a 7 mm mark found, decoder dpi 300, the stored PNG still
200 DPI, the location in its pixels), with `test_document_corners`, `test_inbox_scan_groups`, `test_scan_stray_pages`,
`test_assignment_finder`, `test_alias_ocr` and `test_sequence`, through `manage_checks.environment` on isolated NVMe scratch.
On 1,946 real Inbox pages the 300 DPI read lost no code and found 2 more. No migration. Restart Faculty and the Inbox worker
once the restart check shows `"idle": true`.

A missed makeup rebooked, 2026-10-18 (ABSENCES "MAKEUP BOOKING" › A MISSED MAKEUP): `test_makeup_booking`
(`CardTests.test_a_missed_makeup_past_its_due_date_gets_a_new_card_and_a_later_due_date`: past the due date, "Send a new card"
offers the next two days, the due date moves to the card's last day, the missed sitting is let go, the card goes out and the pick
seats them), with `test_makeup_slots`, `test_absence_makeups`, `test_absences` and `test_followup_suggestions`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart Faculty and the Inbox worker once the restart check
shows `"idle": true`.

Named makeup copies with the name filled in, 2026-10-18 (ABSENCES "COPIES" › NAMED AGAIN, DOCUMENTS "NAME FILLED IN"):
`test_makeup_booking` (`CopyTests`: every copy named for the student it was made for, with a kept name; the printed page 1 shows
the name right of "Name" on its baseline), `test_catalog_migrations` (017 applied as new), with `test_documents`,
`test_document_corners`, `test_term_print_jobs`, `test_print_seating`, `test_print_origins`, `test_print_email`,
`test_printer_send`, `test_makeup_drafts`, `test_absences` and `test_absence_makeups`, through `manage_checks.environment` on
isolated NVMe scratch. Migration 017 (new table). Restart Faculty and the Inbox worker once the restart check shows `"idle": true`.

Booked makeups print at home, 2026-10-18 (ABSENCES "COPIES" › PRINTED AT HOME, DOCUMENTS "SEND TO PRINTER" › AUTOMATIC MAKEUP
PRINTS): `test_makeup_booking` (`CopyTests.test_a_ready_makeup_copy_prints_at_home_once`: waits for the approved version, the
build and a printer; a refusal tried again after PRINT_RETRY; accepted once), `test_absence_makeups.mjs` (the row's printed
lines), with `test_printer_send` and `test_absences`, through `manage_checks.environment` on isolated NVMe scratch with a fake
IPP printer. No migration. Restart Faculty and the Inbox worker after the change, once the restart check shows `"idle": true`.

Print at Home (send to printer), 2026-10-18 (DOCUMENTS "SEND TO PRINTER"): `test_printer_send` (a fake IPP printer on loopback: the options
read, one approved send per request ID printed once with its receipt, the workspace origin and current output digests, an
unconfigured or non-home-network printer never queued, a refused job failed and a dropped send unconfirmed, cancelled and never
resent, an interrupted send unconfirmed, a large file cut between copies, retained and immutable records), with
`test_catalog_migrations` (016 applied as new), `test_workspace_overview`, `test_print_email`
and `manage_checks.py browser --browser documents`, through `manage_checks.environment` on isolated NVMe scratch, which drops
`FACULTY_PRINTER_URI` so no check reaches the office printer. Migration 016 (new tables). Restart both Faculty and the Inbox
worker after the change, only once `curl -s 127.0.0.1:3000/api/assistant/restart-check` shows `"idle": true`.

Inbox and assignment pipeline, 2026-10-18 (instructor: "make all of your suggested changes"; INBOX "SCANS AND SUBMISSIONS",
"LIST FILTERS"; DOCUMENTS "ONE WAY TO GROUP", "ONE LABEL PER PAPER", "BLOCKERS YOU CAN ACT ON", STRAY SHEET; ASSIGNMENT-WORKSPACE
"One pipeline", "THE GRADING INDEX IS RETIRED", "BEFORE A SCAN CLEARS"; FRONTEND "ONE COUNT OF WHAT NEEDS YOU", "ONE ENTRY PER
ASSIGNMENT"; GRADING "ONE RETRY", "ONE PLACE TO SEND", PLAIN WORDS; GRADESCOPE "ONE PLACE TO PUBLISH"; TEACHING-WORKFLOWS "ONE
LINK PER PLACE"; EXAMS): node tests `test_assignment_workspace.mjs` (automationLine, missingPapers, waitingScans,
WAITS_FOR_PLAN), `test_assignment_flow.mjs`, `test_home.mjs`, `test_home_navigation.mjs`, `test_palette_assignments.mjs`,
`test_scan_flow.mjs`, `test_ui.mjs`, `test_grading_guide.mjs`, `test_grading_overview.mjs`, `test_grading_run.mjs`,
`test_grading_certification.mjs`, `test_grading_gradescope.mjs` and `test_grading_gradescope_publish.mjs` (`node --test`);
Python `test_inbox` (kind=command ordering, `kind_counts`, `needs`, kind=all, a read without `kind` unchanged, an unknown kind
refused), `test_inbox_scan_groups` (`assignment_id`/`term_id`, kind=scan, the assignment filter, needs, search by assignment
title, no kind fields without kind), `test_assignment_workspace` (`test_preparation_needs_you_only_when_it_waits_for_you`),
`test_workspace_overview`, `test_exam_overview`, `test_grading_intake`, `test_sequence` and `test_unlabeled_exams`, through
`manage_checks.environment` on isolated NVMe scratch; `manage_checks.py browser` with `inbox` (Submissions tab and counts,
Current's link to Submissions, the scan filters and their requests, the group header's Collect link, the Technical details
fold, "Cancel scan…", the scan chip words; the Accommodations option count is now 3), `assignment_workspace` (Readiness row
names, the automations line, I1's one-time upload, I2's waiting scans, the strip's Teaching review link), `scan_stray_pages`
(the stray sheet set aside on Papers), `sequence` (the one-label question answered inline on Papers, grouping by hand under
Other ways to group), `alias_ocr`, `bubble_sheets`, `bubble_keys`, `version_sort`, `home`, `navigation`, `badges`,
`cross_links`, `documents`, `exams`, the `grading*` checks (`grading`, `grading_index_poll`, `grading_intake`,
`grading_layout`, `grading_loading`, `grading_progress`, `grading_review_failed`, `grading_rubric_quality`,
`grading_settings`, `grading_setup`, `grading_setup_steps`, `grading_workflow`, `grading_certification`,
`grading_gradescope`, `grading_gradescope_after`, `grading_gradescope_publish`) and `teaching_workflows`, on synthetic data.
Default API bodies are unchanged (the Inbox's `kind`, `needs` and `assignment` are additive parameters). No migration. Restart
after the change, only once `curl -s 127.0.0.1:3000/api/assistant/restart-check` shows `"idle": true`.

The copy's column assignment covers each section Sync made it in (2026-10-17, CANVAS COLUMN ASSIGNMENTS "SECTIONS FOLLOW THE
COLUMN"): `test_canvas_sync` (`test_the_copy_s_assignment_covers_each_section_sync_made_it_in`) with `test_canvas_content`,
`test_canvas_nightly`, `test_canvas_section_content`, `test_canvas_posting`, `test_iclicker` and `test_webassign_nightly`, through
`manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change; the next gradebook write repairs
an existing copy.

WebAssign's available dates follow Canvas (2026-10-17, CANVAS "WEBASSIGN DUE DATES" AVAILABLE DATES): `test_webassign_nightly`
(`test_webassign_s_available_dates_follow_too_and_the_push_finds_nothing_to_ask`,
`test_an_available_date_changed_in_the_copy_and_not_pushed_is_kept`) with `test_canvas_content`, `test_canvas_nightly` and
`test_canvas_section_content`, through `manage_checks.environment` on isolated NVMe scratch. No migration. Restart after the change.

Assignment UX, 2026-10-17 (ASSIGNMENT-WORKSPACE, GRADING "KEEP CONFLICTED EDITS", TEACHING-WORKFLOWS):
`test_assignment_workspace.mjs`, `test_assignment_flow.mjs`, `test_grading_review_layout.mjs` and
`test_teaching_workflows.mjs` cover scoped navigation, destination marks, next-task labels, collection filters,
paper prerequisites, rubric compatibility and mixed posting times. `manage_checks.py browser` with
`assignment_workspace`, `grading_review`, `grading_setup_steps` and `teaching_workflows` checks retained conflicting
edits, explicit restoration/discard, the paper-first view, Return sub-tabs, collection selection/filtering/drafts,
version refusals, local retry and folded delivery details on synthetic data. Restart only after the idle gate below.

Restart check (2026-10-17, CANVAS "A RESTART WAITS FOR THE MORNING SYNC"; replaces the fixed 05:00–08:00 no-restart window):
`test_canvas_nightly` `test_a_running_morning_holds_a_restart` checks the morning registers with the Canvas writes while it reads
and leaves nothing registered after; `test_assistant_coding` checks `GET /api/assistant/restart-check` and Apply refusing while
it runs. Run with `test_canvas_sync` through `manage_checks.environment` on isolated NVMe scratch. No migration. Before any
restart: `curl -s 127.0.0.1:3000/api/assistant/restart-check` shows `"idle": true`.

A finalization's clarifications no longer keep a multiple-choice question from the reader (2026-10-17, GRADING
MULTIPLE-CHOICE READER): `test_grading_choice` (`test_a_finalization_clarification_does_not_keep_a_question_from_the_reader`),
through `manage_checks.environment` on isolated NVMe scratch. Restart after the change.

Multiple-choice reader asks the simple question (2026-10-16, GRADING MULTIPLE-CHOICE READER): `test_grading_choice` (the
question's wording) and `test_decisions_sandbox`, through `manage_checks.environment` on isolated NVMe scratch. Restart after the change.

DR quiz taker offered only with accommodations (2026-10-16, ACCOMMODATIONS "DR QUIZ TAKERS" OFFERED): `test_dr_quiz_takers`
(`test_the_box_is_offered_only_with_an_accommodation`) and `tests/test_dr_quiz_taker.mjs`, with `test_accommodations`,
`test_dr_quizzes` and `test_dr_quiz_notices`, through `manage_checks.environment` on isolated NVMe scratch. Restart after the change.

A restart waits for a seating Confirm (2026-10-15, SEATING "A RESTART WAITS"): `test_seating_mail_merge`
(`test_a_restart_waits_for_a_confirm_and_none_starts_while_stopping`) with `test_seating`, `test_canvas_messages`,
`test_seating_student_mail` and `test_response_card_invalidations`, through `manage_checks.environment` on isolated NVMe scratch.

Scantron labels (2026-10-15, DOCUMENTS and BUBBLE-SHEETS "SCANTRON LABELS"): `test_print_seating`
(`test_a_scantron_label_follows_each_copys_pages_on_the_labels_only`), `test_bubble_sheets` (a label matches unless an exact UIN
read is someone else's; a labeled sheet stays an unlabeled page and is identified by its label) and `test_catalog_migrations`;
run them through `manage_checks.environment` on isolated NVMe scratch with `test_bubble_keys`, `test_documents`, `test_sequence`,
`test_seating`, `test_unlabeled_exams`, `test_term_print_jobs` and `test_document_corners`; `browser_seating` sees the renamed
"Labeled pages per paper exam" field and the unticked Scantron option. Migration `document_identity/015_sheet_labels` (one new
table) applies at start; `matching.MATCHER` `average-5` rematches retained reads from their text. Restart after the change.
# Repeatable checks and catalog upgrades

Scantron labels print on their own button (2026-10-18, DOCUMENTS "SCANTRON LABELS"): `test_print_seating`
(`test_a_scantron_label_follows_each_copys_pages_on_the_labels_only`: room `page-labels` and `scantron-labels` on roll and sheet
stock carry exactly their tokens) with `test_seating`, `test_documents`, `test_term_print_jobs` and `test_bubble_sheets`, through
`manage_checks.environment` on isolated NVMe scratch; `node --test tests/test_print_files.mjs`, the frontend typecheck and
`manage_checks.py browser --browser seating`. No migration. Restart after the change.

Other paper of a flagged first page (2026-10-17, DOCUMENTS OTHER PAPER): `test_unlabeled_exams`
(`test_covers_are_read_matched_and_registered_as_their_students_paper`: the flag, the other paper and its page image) through
`manage_checks.environment` on isolated NVMe scratch. Read only; no migration. Restart after the frontend change.

Review focus layout (2026-10-17, GRADING "Continuous review workspace" FOCUS LAYOUT): `manage_checks.py browser` with
`grading_review_queue`, `grading_layout`, `grading_review_failed`, `grading_review_rules`, `grading_review_blank`, `regrades` and
`theme`, plus `node tests/test_grading_review_layout.mjs`. Layout only; no migration. Restart after the frontend change. The flag review's wording
(GRADING FLAG REVIEW "SAID AS WHAT IT DOES TO THE QUEUE"): `grading_review_blank` and `grading_review_queue`.

Unnamed makeup copies (2026-10-16, ABSENCES "COPIES" UNNAMED): `test_makeup_booking` (`CopyTests`: every copy printed for no
student, whom each job was made for in `absence_makeup_copies`, none printed twice) with `test_catalog_migrations`,
`test_absence_makeups`, `test_makeup_drafts` and `node tests/test_absence_makeups.mjs`, through `manage_checks.environment` on
isolated NVMe scratch. Migration `absences/006_makeup_copies` (one new table) applies at start; restart after.

Booked by hand (2026-10-16, ABSENCES "STATES" BOOKED BY HAND): `test_makeup_booking`
(`test_a_sitting_booked_by_hand_reads_booked_and_gets_a_copy`, `test_a_hand_set_sitting_you_printed_for_gets_no_second_copy`,
`test_another_time_seated_by_hand_is_settled`) with `test_absence_makeups`, `test_absences`, `test_makeup_drafts` and
`node tests/test_absence_makeups.mjs`, through `manage_checks.environment` on isolated NVMe scratch. No migration; restart after.

Canvas announcements (2026-10-15, CANVAS "ANNOUNCEMENTS", ASSISTANT "CANVAS ANNOUNCEMENT CARD"): `test_canvas_announcements` (one
post per linked course, a shared course once with `also`, unlinked sections, the fingerprint, no second post, a refusal, no answer
and a signed-out session recorded, the Assistant's card posting only on Approve) with `test_canvas_messages`, `test_assistant_outcomes`,
`test_assistant` and `test_catalog_migrations`, through `manage_checks.environment` on isolated NVMe scratch against `tests/fake_canvas.py`.
Migration `canvas/009_canvas_announcements` (three append-only tables) applies at start; restart after the change (frontend too).

Card reminders before the exam (2026-10-15, RESPONSE-CARDS "REMINDERS BEFORE THE EXAM"): `test_response_card_mail`
(`test_pending_seating_cards_get_reminders_three_two_and_one_day_before_the_exam`, and the legacy-copy index test now expecting
`response_card_mail_reminder`) with `test_response_cards`, `test_response_card_invalidations`, `test_seating`,
`test_seating_mail_merge`, `test_seating_student_mail`, `test_workspace_overview` and `test_catalog_migrations`. Migration
`response_cards/003_reminders` rebuilds `response_card_mail` to allow kind `reminder` (every row, column and index kept); it applies
at the first start, so stop the response-cards timer, restart `faculty faculty-inbox` together, then start the timer again.

Seating cards close after the exam (2026-10-15, RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM"): `test_response_cards`
(`test_seating_cards_close_at_campus_midnight_after_the_exam`) with `test_response_card_invalidations`, `test_response_card_mail`
and `test_seating`. No migration; restart after the change (the minute timer picks it up on its next run).

A settled version stays settled (2026-10-15, GRADING "A SETTLED VERSION STAYS SETTLED" and "GUIDANCE PUT BACK"): `test_grading_rubric_quality`
(`test_resolving_concerns_of_an_added_version_never_changes_the_settled_ones`) and `test_grading_rubric_scope`
(`test_a_finalization_that_puts_the_guidance_back_puts_back_its_grades`). No migration.
Makeup version on booking cards (2026-10-15): with cards on and the makeup sitting left at "None for now", ticked students stay on
their own booking cards and Approve no longer asks for a sitting; the window sends no student to place. `node --check
static/makeup-drafts.js` and the frontend typecheck; no migration; restart after the frontend change.
DR quiz notices (2026-10-15, SCHEDULING "DR QUIZ NOTICES"): `test_dr_quiz_notices` covers the "Prescheduled in AIM" mark and unmark
(key, readiness, fingerprint, request IDs), no email without a mark, one email per DR student with the agreed sender, subject and
lines, skipped students, uncertain and failed sends never resent and the explicit Retry, a moved quiz lapsing and its updated
notice, the sweep reaching a student flagged later and nothing more to one unticked (only DR quiz takers are told, never a DR
booking or Extra Time alone; `test_dr_quizzes` lists a section only for a flagged current student), and the routes, Assistant policy and worker wiring; Gmail is the fake in
`tests/test_gmail_mail.py`. Run it through `manage_checks.environment` on isolated NVMe scratch with `test_dr_quizzes`,
`test_gmail_mail`, `test_assistant` and `test_catalog_migrations`. Migration `scheduling/004_dr_quiz_notices` (two append-only
tables) applies at start; restart after the change, once the restart check is idle (no Inbox job, send or morning sync running).

DR quiz takers (2026-10-15, ACCOMMODATIONS "DR QUIZ TAKERS"): `test_dr_quiz_takers`, `test_followup_suggestions`
`test_a_dr_quiz_taker_card_waits_for_the_instructor` and `tests/test_dr_quiz_taker.mjs`. New migration `dr_quiz_takers/001_dr_quiz_takers`
(`dr_quiz_taker_changes`); restart after the change.

Leaving the section (2026-10-15, CANVAS "LEAVING THE SECTION"; their 10-15 rosters: one Q drop in 508, one W in 515):
`test_rosters` covers Q drops, withdrawals and students a later file leaves out offered in `leaving` and never dropped by the
import, the drop removing them from the list and a restored student not offered again; `manage_checks.py browser --browser
rosters` drops a Q-dropped student from the roster's callout. No migration; restart after the frontend change.
Import rosters suggests each file's section from its name (same day): `node --test tests/test_courses.mjs` covers
`sectionFromFileName` (exactly one of the course's sections among the name's digit runs, else none), and the rosters browser
check sees "section-508.csv" start on Section 508 and "other.csv" unchosen.
An Import rosters result names who is leaving and why, with "Review on the roster" (same day; they imported all four and saw no
flag, since the list was only on the roster page): `leavingText` in `tests/test_courses.mjs`, and the rosters browser check imports a
file leaving a student out and follows the link to the roster's callout.
Mixed rooms (2026-10-15, SEATING "Mixed rooms"): `test_seating` (shares, one seat per student across the rooms, DR on the
first chart, update/regenerate/move/delete within a mix) and `python3 manage_checks.py browser --browser seating_mix --keep`.
No seat or card for a Q drop or withdrawal (same day, CANVAS "NO SEAT OR CARD..."): `test_seating` (options, the check, an update
giving up a held seat with its reason, a restore seating them again) and `test_response_cards` (no card, not in the picker). The
new `roster_registrar_marks` table is inline in `initialize_rosters` like `roster_drops`; live marks for the 10-15 imports were filled
once from the saved rosters under the roster lock. Restart after the change.

This assignment comes first (2026-10-15, GRADING "RUBRIC MEMORIES"): prompts only (`rubric_quality` v11, `_versions` v7, `_added` v4,
`_resolve` v9, `quiz_rubric` v8). `test_grading_rubric_edits` pins the reviewer version; `test_rubric_memory`, `test_quiz_drafts` and
`test_grading_rubric_versions` check the standing rules still reach every writer and check, with the new sentence in the prompts.

Another student's card says why (2026-10-15, ASSISTANT WIDE SCOPE; Evan Gould's follow-up held Celia Lomas-Reyes's makeup
booking with the reason hidden under "Show the whole proposal"): `node --test tests/test_followup_suggestions.mjs` covers the
Assistant's note on the face of a card that changes another student's record, shown once. No migration; restart after the
frontend change.

Joined follow-up tracking (2026-10-15, TEACHING-WORKFLOWS SAME MATTER; Fiona Weaver's follow-up, saved under a Gmail thread
now read as part of a Canvas conversation, refused the chat's track_followup): `test_followup_connect` covers student_context
returning the follow-up saved under a joined conversation, by either key, and track_followup proposing against its revision.
No migration; restart only after the ordinary idle checks.

DR quizzes (2026-10-15, SCHEDULING "CLASS SCHEDULE: DR QUIZZES"): `test_dr_quizzes` covers the sections listed, the upcoming quiz
rows, their date and time by the most-specific-record rule and the class meeting, the AIM comparison, the fingerprint, the read
writing nothing and the Assistant's `dr_quizzes`; run it through `manage_checks.environment` on isolated NVMe scratch with
`test_scheduling_class_schedule`, `test_dr_exams`, `test_accommodations` and `test_makeup_length`. The page: `node --test
tests/test_dr_quizzes_page.mjs tests/test_accommodations_page.mjs`. No migration; restart after the frontend change.

Part caps (2026-10-15, GRADING "PART CAPS"): `test_part_caps` covers one cap per lettered part at its worth with whole-question
lines outside (their Quiz 3 problem 3: four Part B misses leave Part A's 0.5, `part_caps` 0.5 and a note; a grade no cap changed
has no `part_caps`); positive rubrics, one part and unknown or mismatched worths (uncapped, with the warning); a printed worth over
the writer's; a reworded line moving to the cap its wording names; malformed caps refused; a cap never making a condition
structured; the Gradescope send carrying the give-back in the point adjustment, and the receipt counting as the same content; the
writers' `parts` and the draft warning; quiz versions and adaptations capped, a makeup keeping its original's caps or none.
`test_grading_rubric_drafts` accepts a deducting two-part draft end to end, and `test_grading_rubric_display.mjs` the caps beside
the point range.

Compute settings in five sections (2026-10-15, FRONTEND.md's Compute settings; the instructor chose to port the 2026-10-03 layout,
"cluttered and hard to read", onto the current dialog). `manage_checks.py browser --browser grading_settings` reads Defaults
holding only the mode, its switches and its team, Other models after Defaults in the menu, a role named in "Next Cloud batch
uses" scrolling to its model and focusing it, and every section within the viewport at 1500 and 1280; `grading_gateway`,
`grading_hosts`, `grading_reasoning`, `grading_rubric_quality` and `grading_subscription` open Other models or Advanced where
their controls moved. `grading_gateway` also expects the Gemini subscription in every provider list but the coding agent's: it
failed on main at that line before this change. No migration; restart only after the ordinary idle checks.

Scope guard (2026-10-15, ASSISTANT-MEMORY "A NARROWER OWNER NEVER REPLACES A WIDER ONE"): `test_assistant_memory` covers a
term's statement Jev reads as a change kept beside the workspace's rule, a later change within the term replacing the term's
memory, a course's statement kept beside the workspace's rule, and a wider statement still replacing a narrower one; the
decision itself with `kept` and `memory_decisions.wider`. Run it through `manage_checks.environment` on isolated NVMe scratch
with `test_followup_learning`, `test_memory_sandbox` and `test_assistant`. No migration; the frontend is unchanged.

Rubric memories (2026-10-14, GRADING "RUBRIC MEMORIES"): `test_rubric_memory` covers what stands (only kept rows; a kept revision
replaces what it revises, a kept retirement takes it away, Drop of a kept revision brings back what it revised, the list as it stood
at a given moment, every table immutable); the rubric check sending the kept list as `standing_rules` while its confirmation and
`input_sha256` stay what they were; a learning pass reading the instructor's answers and the graders' doubts with the roster's names
and nine-digit numbers taken out, keeping what it writes at once (actor `automatic`, 2026-10-17 KEPT AT ONCE) and Drop taking one back, and the same evidence asking nothing again; an answer in the assignment's
own terms refused once with the reason; the strict reader (a rule needs one of the instructor's answers, sources of this pass only,
length caps, line IDs, version letters and typeset math refused, a revision of a memory that does not stand left out); a later pass
revising a kept memory, the revision kept at once and its Drop bringing back the earlier wording; responses of a deleted Inbox scan counting nowhere; Certify queueing one pass
and never failing without a model; and the three routes. `test_quiz_drafts` and `test_grading_rubric_versions` check that every
quiz-rubric and every-version writer is sent the kept list (never one only proposed), as it stood when the revision was asked for;
`test_catalog_migrations` adopts migration 047. The page: `manage_checks.py browser --browser rubric_memories`. Run the modules
through `manage_checks.environment` on isolated NVMe scratch. Migration `frq_grading/047_rubric_memories` uses the normal
lifecycle-locked startup migration and adds `frq_cloud_calls.rubric_memory_run_id`; restart only after the ordinary assistant,
Inbox, grading and Canvas idle checks (the restart check, morning sync included).

Memory review fixes (2026-10-14, ASSISTANT-MEMORY "KEEPING", "EVERY WAY IN", "SAID WHERE IT HAPPENED", "LEARNED FROM AN EDIT"):
`test_assistant_memory` covers a student's whole name found however the roster writes it (a middle name, an initial, a
two-letter part, a hyphened or several-word family name, either order), two letters never read alone, and the same reading
in what is taken out of a student's words and a draft's; a Save from the form keeping a memory's name, a move with the same
words not asked about while Jev is down, and a follow-up's name following its words; Undo of a memory made to stand again
or moved, and never of the instructor's own words; a correction compared where its follow-up is with two current terms; a
title or an aside ending no sentence; and a message refused while an answer runs leaving that turn's files alone.
`test_followup_learning` covers the follow-up's Undo of a rule that stands again, and a correction that could not be
claimed handed again and then never. Run both through `manage_checks.environment` on isolated NVMe scratch, with
`test_assistant`. No migration; the frontend is unchanged.

Refined memory (2026-10-13, ASSISTANT-MEMORY "DRAFT REFERENCES", PAST-WORK): `test_assistant_outcomes` covers local receipt
completion, partial Canvas pushes and messages, a stopped regrade, one-cell sends, approval ordering and confirmation selection
past unsettled candidates. `test_assistant_past_work` checks the same outcomes in recall, metrics and learning's SQL reader.
`test_assistant_memory` checks enrollment scope with two current terms and explicit instructions ahead of recent learned rules
at the budget. `test_followup_learning` and `test_assistant_workflows` check durable, deduplicated draft references and counts
by exact memory revision, including repeated edits. No prompt or student message is copied. The memory and follow-up frontend
tests cover the count wording and unfinished work staying out of the completed fold; the corresponding browser checks cover
the existing review controls. Run with `manage_checks.environment` and isolated NVMe scratch, then the browser runner.
Migration `assistant/012_draft_memories` uses the normal lifecycle-locked startup migration; no earlier migration changes.
Restart only after the ordinary assistant, Inbox, grading and Canvas idle checks (the restart check, morning sync included).
After a review of it (2026-10-13, ASSISTANT "STILL RUNNING IS NOT A PROBLEM"): `test_assistant_outcomes` also covers work
still running read as neither done nor a problem, a step cancelled before or after part of it was done, a Canvas message one
course did not confirm, a result too long to keep whole still naming its receipts, Sync with Canvas by its send, and a
receipt that cannot be read falling back to the recorded event (in the learning look's SQL reader too).
`test_assistant_workflows` checks that a step still running holds a plan's next stage without stopping it or holding its
rule; `test_followup_suggestions.mjs` that such a card is not flagged, a cancelled one folds away, and a mail Gmail did not
accept still says "Check delivery". Then (the same day, "do the recommended option and add the gradescope route"):
`test_assistant_outcomes` reads a Gradescope assignment being made through its creation and each course's unit (queued,
made, one course stopped, none made, retried, cancelled before and after one was made, and a long plan still naming it);
`test_followup_learning` counts a draft given a rule and still corrected that way on that rule alone (an edited card on its
own draft, a round asked for again on the draft before it), never an edit of the draft alone and never a rule kept as the
change was asked for, and starts the count again when the rule is reworded; `test_assistant_memory.mjs` reads the line on
the Memory page.

One student's grade on a Canvas quiz (2026-10-13, CANVAS ONE CELL "A CANVAS QUIZ'S GRADE"; the instructor: "I asked to update this
student's video quiz grade and it suggested a code change"). `test_canvas_cell` `QuizGradeTests`, on the loopback FakeCanvas with
a classic quiz the first sync marked `canvas` "quiz": the preview writing nothing and showing Canvas's 0 beside the 2 asked for;
the send setting that student's grade alone, answering `accepted` with the receipt, the cell left Canvas's and the next sync
reading the 2 back with nothing held or sent; a blank in Canvas taking a grade; the same request ID the same send (other content
a 409); a grade changed in Canvas after the preview a 409 with nothing written; Canvas's refusal answered `failed` and an update
Canvas took and had not applied `uncertain`; a Faculty column, a WebAssign column, a grade above the quiz's points, the grade
Canvas already shows and a send without its confirmation each refused, typed scores still refused, and ONE CELL's preview of
that column as before. The Assistant: one Approve card with the preview and no confirmation, the card's words, approval setting
the grade (`result.status` accepted), and a card made before Canvas changed answered stale with nothing set.
`test_assistant_integration` names its confirmation. NOT DONE: nothing was read from or set in the real Canvas (whether its bulk
update takes a grade on a real classic or New Quiz is known only from its documentation; the receipt read back says so either
way); no follow-up preparation was run with a scripted engine proposing this step before a reply; no page has a button for it.

A status step left behind (2026-10-13, ASSISTANT "A STEP LEFT BEHIND"; the instructor's screenshot of a "Save follow-up" card
answered "already tracked by follow-up … Change it with POST …"). `test_followup_suggestions`, scripted engine and Jev fake:
`test_the_draft_check_withdraws_the_scheduled_status_with_the_reply` has the draft check send a reply back once and finds the
first scheduled status withdrawn, the repaired one scheduled (no card), and one follow-up with the repaired next step after
the send; with the old loop it fails on the extra card, as live did.
`test_a_status_card_left_after_the_follow_up_was_saved_is_withdrawn_in_your_words` proposes two statuses in one preparation,
sends the reply, then approves the card: 409 in the instructor's words (no route, ID or revision), the card withdrawn, nothing
waiting, one follow-up. On the real records (read only): of 2 preparations the draft check sent back, 1 had a scheduled
status, and it is this card. NOT DONE: no browser check clicks the card; the page shows a withdrawn step as it always has.

Gradescope after certifying (2026-10-12, GRADESCOPE "AFTER CERTIFYING"; the instructor: "Can we also automate, in the same way,
uploading/publishing to gradescope after it has been initially certified", then "First Certify too", "Create it too" and "Hold
that section"). `test_gradescope_after`, against FakeGradescope on the certification fixture: the preview offering the tick and a
ticked Certify queuing one run in the certification's transaction (a `send` decision refused without it, an unticked Certify
unchanged, a malformed tick a 400, the switch off offering nothing and refusing the tick); Faculty sending a changed grade and
publishing with Gradescope's own form, read back, with the send, the publication, the page's `gradescope.after` and the
Automation tab's lines; a section Gradescope shows 62.5% graded held until the instructor's own Publish answers it; a declined
group sent nowhere and its changed grade holding the section; a section whose course is not linked getting nothing with why and
no write at all; a papers-only section left its grades and held; a run waiting for the instructor's send and stopping after six
hours; a step cut off after its send was recorded moving on without a second send; a finished run refusing every change. With
the section's course linked and no assignment there, Faculty makes it, sends both papers and publishes, and an assignment of that
name made by hand is never linked. A late paper's version published where its set already is (and the send's card no longer
saying it is not), a set not published left alone, a set published whole with nothing to do, a version Gradescope shows 80%
graded held with the older notice not repeated, and the switch off leaving the instructor's notice as before.
Bubble sheets, on the sheet and MC fixtures with a certification row written for the bubble part: a sheet Gradescope scored
differently from the key holding its section after the ONE MC check the run asks for, sheets scored as the key does published, a
comparison as new as the sheets not asked for again, scores that could not be read holding the section, sheets not there going up
first to the exam the section is linked to, and a section with no bubble-sheet exam chosen (or a declined part) getting nothing.
AFTER THE REVIEW OF 2026-10-12 (an independent read of the change): a late paper holding when another paper in the same
unpublished version has a grade that is not there as Faculty has it, and publishing nothing when its own version is already
published; sheets Gradescope has not scored read again (five reads) and then held, never published as agreeing, and published
when the second read finds them scored; a queued run stopping when the switch is turned off (nothing read) or the assignment
is certified again; a send seen while the publish plan was read making the run look again; a set with no graded share read
from its versions, and an assignment Gradescope says nothing about held; an assignment that may be this one under another title
never duplicated. The dialog keeps a group the last certification declined ticked "Do not send".
ON THE REAL GRADESCOPE (2026-10-12, requirement: "publish Version E for Quiz 4 in 515", through Faculty's own Publish
preview and Publish with the other sections left out): the set read "Quiz 4", 79 submissions, 100% graded, Versions A to D
published, E not, and the SET'S OWN ROW unpublished; one unit, accepted in three seconds, read back with all five versions
published. So Publish All Grades does publish a version added to a published set, the list does give a set its graded share,
and a late run's "its set is already published" must count a published VERSION (it read the set's own row and would never have
fired): fixed, with `test_a_set_gradescope_shows_unpublished_only_because_of_the_new_version_is_published`.
`test_certification_automatic`'s published-set test now ends with Faculty publishing. `node tests/test_grading_certification.mjs`
covers the dialog's tick (on by default, the unanswered group sent, "Do not send" kept, unticked exactly the old request and
markup, not offered nothing drawn), the page's lines (working, published, held with Publish grades…, notes, a refused
publication, a stop, escaped) and the certified band's sentence; the three `test_grading_gradescope*.mjs` cover the cards' "by
Faculty by itself, after you certified". `manage_checks.py browser --browser grading_gradescope_after` certifies with the tick in
a real page against the real service and FakeGradescope: the tick on, the group needing no answer, unticked the old question
back, the footer beside Certify, the request's body, the quiet line while Faculty works, the sender's turns sending the changed
grade and publishing, the page saying so without a reload, the open Gradescope fold's cards following it, then a held section's
line and its Publish grades… leading to the header's button with nothing published (1500 and 1280 wide).
`browser_grading_certification` unticks it and certifies as before. FakeGradescope only: nothing was made, sent or published on
the real Gradescope, and publishing a version added to a set that is already published was never tried there (the unit reads it
back and stops for Retry if Gradescope does not show it). NOT DONE: no test certifies a plan with BOTH parts through the dialog
and follows sheets, written grades and the MC check in one run; the Compute settings switch was not clicked in a browser
(`browser_grading_settings` only counts it).

Several follow-ups prepared again at once (2026-10-12, ASSISTANT "SEVERAL AT ONCE"; the instructor: "is the assistant able to
modify multiple followups at once, for instance if I say something like 'do X with all followups requesting Y'", then "no card,
build it"). `test_followup_redo` drives the chat's two tools with a scripted session. `open_followups`: both open follow-ups in
brief (roster name, IDs, title, state, the steps waiting, the last preparation's explanation), under a third of the page's own
read, `find` keeping the one that mentions its words and none when nothing does, a resolved follow-up left out, and the tool a
read. `redo_followups`: two follow-ups started at once with the same changes (a waiting step replaced as the instructor's
request, both runs queued and then prepared with "Changes requested"), each receipt named for its follow-up, the receipts one
group of the redo route whose first receipt the chat's line names, a plain Retry without changes, and no group for one
follow-up; a resolved follow-up refused in the redo's own words while the other starts, one named twice asked once, and an
answer that says so when none starts; a review-only chat's one group approved by Approve all; the tool-use check holding only
the follow-up it finds unasked for, with no card of the one it is unsure of; a follow-up past the task's last check
starting unchecked; more than 40, an unnamed follow-up, a stop during the first check and a follow-up preparation starting
none. `node tests/test_assistant_workspace.mjs` covers the
conversation's one line and one card for such a group (done and failed counted, each follow-up listed, the refusal's words, no
Approve all). `manage_checks.py browser --browser followup_several` asks the floating chat on the Follow-up page for the same
change to both follow-ups, with a scripted engine: one line and one card for them in the chat with nothing to approve, each
follow-up listed under Each change, and the follow-up beside the chat leaving its waiting step for "Waiting for the Assistant"
within seconds. No frontend file changed. NOT DONE: no real model chose follow-ups from `open_followups` or wrote the answer
naming them; the tools were not called on the live record.

The decision on an absence card is a dropdown (2026-10-12, ASSISTANT "DECISION ON THE CARD"; the instructor: "make it into a
dropdown menu that defaults to the model's suggestion, but allows me to change it from the dropdown"). `test_followup_suggestions`
`test_the_decision_on_an_absence_card_is_chosen_from_its_dropdown` covers the line's choices (no Withdrawn for a new absence, all
four on a revision), a choice saved as a new waiting card in the old one's place with nothing recorded, Excused chosen by the
instructor where the Assistant is refused it, a decision chosen again after another, what the Assistant suggested kept on the
card, its own decision note dropped, the refusals (the same decision, one not offered, a stale card, a revision naming no
decision, a card that is no absence), and Record absence recording the decision chosen.
`node tests/test_followup_suggestions.mjs` covers the dropdown's markup, "Your decision. The Assistant suggested …", and no
dropdown while a preparation runs, on a follow-up that changed or on a chat's step. `manage_checks.py browser --browser
followup_decision` chooses in a real page and in the Assistant's Changes tab, with a scripted engine: the dropdown on the
suggestion at the line's size, the choice kept through a read, focus on the new card's dropdown, a refused choice put back, and
the absence recorded as chosen. NOT DONE: no real model prepared the card; a revision's card was not opened in a browser.

The Canvas message card (2026-10-12, ASSISTANT "CANVAS MESSAGE CARD"; the instructor: "Can it simply says All Students In 507,
All Students in 508, etc"). `test_canvas_messages` covers each course's `roster` and `roster_reached` (a student Canvas does not
list, one whose enrollment is only invited, a dropped student, a combined course, someone only Canvas lists counted in
neither), the fingerprint unchanged by them, and the Assistant's card carrying the counts and a 7,000-character message whole.
`node tests/test_assistant.mjs` covers the card: "All students in 507 (85)", "82 of 84 students in 508", sections sharing a
course, no recipient's name and no cut-list note, the marked lines, the left-out fold open when a section's own student is in
it, a review from before the counts, nobody reached (Send off) and a refused preview drawn as the ordinary card.
`manage_checks.py browser --browser canvas_messages` has the Assistant prepare a message for both sections, reads the card in a
real drawer (a line per section, nobody who gets it named, the fold closed) and sends it from there, one request per course.
FakeCanvas only; no real Canvas message was made.

Group by default for the Assistant (2026-10-13, ASSISTANT "CANVAS MESSAGE CARD"; the instructor: "it again sent it to individual
recipients"). `test_canvas_messages` has the Assistant prepare a message with `individual: true`: unticked (the card's body, its
review, its fingerprint and the one group request Canvas gets) when the instructor's latest three messages do not ask, when
only an older message or an attached file's name does, and kept when their words do. `node tests/test_assistant.mjs` reads the
ticked card's warning and "Send 85 individual messages"; `manage_checks.py browser --browser canvas_messages` reads both cards
in a real drawer and declines them. NOT DONE: no real model was asked for a message to every section.

The wellbeing check is off (2026-10-12, JEV "WELLBEING OFF"; the instructor, told what it did besides flag a follow-up: "Off
entirely"). `test_jev_send_checks` `test_the_wellbeing_check_is_off_and_a_message_is_treated_like_any_other` covers the gate no
probability reaches for every model, the question still asked in the one triage request, a strong acknowledgment answered .99
on wellbeing filed as FYI with nothing pinned and no Home card, and a request answered .99 listed as needing a reply with no
concern on it and nothing counted. The five tests of what the check did (pinned, counted, never FYI, cleared only by review;
a review holding until the student writes again; carried across providers and outages; ending at a newer message; cleared by
resolving) pass with the gate put back at .30 (`concerns_on`), and fail without it. `test_jev`: a thanks answered .5 on
wellbeing leaves a resolved follow-up resolved and its mail settled, and reopens it at .30. Not a check: on the live app after
the restart, 0 of 47 follow-ups carried the flag (2 before). NOT DONE: no browser check; the page's banner, filter and Home
card are unchanged code that no follow-up reaches now.

The tool-use check's preflight is tuned (2026-10-12, ASSISTANT "TUNED"; the instructor, told it had let none of its 134 checks
through: "in a copy do some experiments and tweak it until we tune it correctly"). `test_assistant_tool_guidance` covers a change
made at once from intent .75 and context .5 and a card below either, with the preflight's own rule on its receipts; a follow-up
preparation's record checked against its own task (the changes asked for in that run, never its answers) and the follow-up
Faculty named, made at once from intent .3, a card below and still held at .1; and the check shown the read that names the
record the change names, as an excerpt around it, with fifteen later reads of Faculty's code left out and the request's own ID
naming nothing. Two older tests read the new evidence (`all_reads_this_turn`). Not a check, the experiments: a snapshot of the
catalog (read only, with the roster file and kept messages) served on localhost for the Assistant's own tool bridge alone, with
the real Assistant model and the real Jev, no Gmail, Canvas or Google configured, no worker, nothing approved. (1) Recorded
under the first check: 8 preflights in 7 real preparations (intent .07 to .38, context .18 to .48; 2 refused, after which the
model read Faculty's code and proposed again; none passed) and 7 in chat (4 redos, 2 calendar events the instructor plainly
asked for, 1 tracking; context .49 to .62, so both calendar events were cards). (2) Seven rounds of replays, 1,230 requests
to Jev (3.5 million input tokens, none failed), each recorded preflight asked again with other evidence and wording; the 68
records the live preparations proposed since 2026-10-09 (35 new tracking, 8 tracking changes, 21 absences, 4 makeup holds)
were added with their three opening reads made again in the copy. A truthful task alone raised intent to .16 to .64, the
covering reads alone raised context to .49 to .63, and saying what yes and no mean raised context to .91 to .96; a third
question (did the instructor say not to) and a preparation-only question were tried and dropped as noisier than intent. (3)
The copy's own code building every request, its scores deciding: of 83 real changes (76 preparation records, 7 chat) 81 at
once, 2 cards, none refused. Of 154 wrong ones built from them: nothing read, 0 of 49 at once (45 refused); only a question
asked, 0 of 14; told not to, 1 of 49; another student's change, 0 of 7 in chat and 7 of 42 in preparations, where
`followups._bounds` makes it a card whatever the check says. (4) End to end with the tuned code: six preparation records
scored intent .50 to .74 and context .94 to .98, none refused, four scheduled as Done for you and two cards by the follow-up's
own bounds; in chat four redos and both calendar events were made at once, and two questions changed nothing. NOT DONE: no
live run. The copy has no Gmail, so no preparation there proposed a reply beside its record. Absences and makeup holds were
replayed from the live proposals with rebuilt reads, not prepared end to end. In chat only a redo, a calendar event and
tracking were run: TA contacts, gradebook columns and joins were not. The told-not-to cases were made by adding one sentence
to the real instruction, and three of the five that scored highest contradicted an instruction that asked for the record.

A follow-up redo asked in the chat is never an Approve card because the tool-use check was unsure (2026-10-12, ASSISTANT REDO
FROM CHAT "STILL NO CARD"; the instructor: "it is redundant to ask me to approve the change I just requested").
`test_followup_redo` covers, with a fake decision service, a redo the check is unsure of starting at once and replacing the
waiting step, with nothing left for review in the chat, while a calendar event in the same turn still waits for Approve; the
same with the check unavailable and with the turn's checks spent; a redo the check finds clearly unasked for held, with
nothing recorded and the waiting step untouched; and a redo in a corrective pass starting at once while the pass's other change
is a card. All but the held one fail with the rule's `result_waits` off, the first two with the live cards' own reasons.
`test_assistant_tool_guidance` is unchanged and passes. Not a check: the live action log, read only, where all seven chat
redos since 2026-10-10 were Approve cards with that reason. NOT DONE: no redo has been asked of the real chat since the change.

Apply's own checks leave the task's copy as committed (2026-10-12, ASSISTANT "VERIFIED CODE APPLY"; the instructor: "an agent
editing files during verification must still be caught"). Since e2fcc77 (2026-10-08) Apply refuses a copy that differs from the
commit it checked, and its compile step (`python -m py_compile`, there since fa5a982) wrote a `__pycache__/` beside each changed
file whatever `-B` or `PYTHONDONTWRITEBYTECODE` said. The step now compiles in memory (`coding.COMPILES`) and writes nothing; the
comparison is unchanged, with no exception added for what a check leaves. `test_assistant_coding`
`test_applys_own_checks_leave_the_clone_as_committed_and_a_change_made_during_them_still_refuses` covers the compile step
leaving nothing in a copy that ignores nothing (on the old step it fails with `?? __pycache__/`), a file written into the copy
while the checks ran and a tracked file edited then each refused with `main` unmoved, and changed Python that does not compile
refused by its file and line with nothing left behind. The two tests that failed since e2fcc77
(`test_a_task_works_in_its_own_clone_and_apply_merges_pushes_and_restarts`,
`test_apply_reports_its_stage_while_it_runs_and_forgets_it_after`, both classes) pass again. They failed only when the module
ran by itself: `manage_checks.py` and Apply's checks set `PYTHONPYCACHEPREFIX`, which moved the folder to scratch, so the suites
passed throughout. Not a check: the old step run on a `git clone --shared` of the real repository left `__pycache__/` and
`assistant/__pycache__/`, both hidden by `.gitignore` line 2 (`git status --porcelain` empty), so no real Apply was refused for
this. NOT DONE: no real Apply was run with the new step.

The coding agent is unlocked (2026-10-11, ASSISTANT "UNLOCKED CODER"; the instructor: "unlock the coding agent, let it have full
capabilities like regular claude code"). `test_assistant_coding`
`test_the_claude_coder_has_claude_codes_tools_settings_and_plugins_and_its_copy_stays_sealed` covers the launch: nothing switches
the instructor's settings, plugins or skills off; the named tools (subagents, skills, web search, the repository's browser);
permission checks never bypassed and nothing that acts on their account named; the refusals in full; no server but the
repository's own `.mcp.json`; no kept session; an environment with no credentials; and Codex's sandbox and disabled features
unchanged. Not a check: the same launch run with the real Claude Code (2.1.296) on a throwaway repository in the coder's
environment (8 plugins and 95 skills loaded, the browser connected, a subagent and a web search ran, `git push` and `systemctl`
refused, no connector of the account present). NOT DONE: no coding task has run this way from Faculty itself, and no plugin has
driven one; the first task started from a card must be looked at. Two other tests of the module fail on `main` before and after
this change, in both classes (`test_a_task_works_in_its_own_clone_and_apply_merges_pushes_and_restarts`,
`test_apply_reports_its_stage_while_it_runs_and_forgets_it_after`: Apply's checks leave a `__pycache__/` that the tests' synthetic
repository does not ignore, so it answers "code changed during verification"; the real repository ignores it). Not fixed here;
fixed 2026-10-12 (the note above).

A phone scan's oversized page is drawn smaller, not refused (2026-10-11, ASSISTANT "A PAGE TOO LARGE FOR THAT IS DRAWN SMALLER,
NOT REFUSED"; the instructor, of a fetched doctor's note the preparation could not look at: "it still says waiting for my
decision"). `test_file_viewer` `test_a_phone_scans_oversized_page_is_drawn_smaller_not_refused_and_a_scans_own_pixels_still_are`
covers a one-page PDF of 2228 by 3193 points described and drawn by the viewer with its longer side 3300 px, in the page's own
shape and with what is on it; an ordinary page still 1275 by 1651 (as `main` draws it); and `render_pages` without `fit` still
refusing the page. Not a check: the real note drawn by the changed code in the document sandbox, read from the live app by its
own route and not kept (2304 by 3300, 7.6 million pixels, a 2.5 MB PNG, 1.3 s, not blank; its words were not read).

A file a student linked from Google Drive is fetched (2026-10-11, COMMUNICATIONS "LINKED DRIVE FILES", ASSISTANT "A LINKED FILE
COUNTS ONCE FETCHED"; the instructor: "fetch the file"). `test_communications` `DriveLinkTests` covers the mail filter's
rewritten links put back (replaced characters and runs), the three link shapes of the live record, a file found once by its ID
with the name Gmail wrote above it, and quoted history, a folder and a published page left alone; `DriveTests` covers a linked
file kept in the message's first revision after its own attachments, read back checked, and taken by an absence as its
documentation by its place; the instructor's own file and a quoted link never fetched, a Google Doc read as a PDF; a file
Faculty may not read yet listed with its reason and no request made, the Allow click asking Google for read-only Drive for the
account already connected, a file not shared tried once per worker run and fetched into the same place once shared; a message
kept before the rule getting its file in a later round, Drive not answering tried again each round with no revision for the
same answer; the Allow sign-in coming back to Follow-up and asking for a Gmail round at once, a refusal naming Google Drive; a
token made before Drive was allowed renewed once; the message kept whatever becomes of its linked file; and a file too large
never asked for its bytes. The fake Drive fails any request that is not a read of one file at
`www.googleapis.com/drive/v3/files`. `node tests/test_communications.mjs` covers the file's note on the timeline and the
follow-up. `manage_checks.py browser --browser communications --browser followup_drive` reads, in a real browser, the
timeline's Google Drive line and the file's "not fetched" note, the same note under a follow-up's student message, and the
Allow click on both going to Google's sign-in (a stub) asking for read-only Drive for the account connected; and, for a
message kept before Faculty read Drive (a link in its words, no file listed), the note and the click under the message and in
"Based on captured communications", with no second note where the file is listed (the instructor: "I dont see the option").
`linksDrive` is covered by `node tests/test_communications.mjs`. Not a check: the
link finder replayed on the live record, read only (4 students' messages with a Drive link: 2 their own file, 2 only quoting
one; the instructor's own 5 never taken). NOT DONE: no request was made to real Google Drive, so the first live fetch must be
looked at (the permission Google asks for, the Drive API being on for the project, the file's line on the message).

Every attachment a student sent, once (2026-10-11, PICTURE-SEARCH "EVERY ATTACHMENT A STUDENT SENT, ONCE"; the instructor: "I
want all attachments from students to be indexed"). `test_picture_search_sources`
`test_every_attachment_a_student_sent_is_found_once_where_it_was_first_sent` covers a photograph and a screenshot on a
student's message, the screenshot carried along by the instructor's reply and by the student's answer and still found once,
where it was first sent; a picture the instructor sent first and the student's answer carries back, not listed; an icon
under 8 KB, a clip and a file that was not kept, left out; and a file an absence holds, listed as that absence's alone in
every copy. A stand-in embedder. Not a check: read on the live record before and after (104 kept attachments on students'
messages; 49 listed before and 19 left out as "logos", none of them one; 58 listed after, all 58 in the index, 10 of them
new, nothing set aside).

Pictures at 560 pieces (2026-10-11, EMBEDDING "PICTURES AT 560 PIECES"; the instructor: "switch to 560"). `test_faculty_embedding`
`test_pictures_are_seen_at_560_pieces_and_a_worker_on_another_setting_is_not_taken` covers the setting and the mark picture
vectors are kept under, the worker's own sizing not undercutting the setting, the worker asking the model for it, `pieces`
going back with the vectors, and a worker on another setting refused with a kind of its own. `test_picture_search` covers a
worker still on the other setting (a wait each turn, nothing set aside, a search by a picture answering 503, the page read
once the worker is back), pictures made at another setting (all waiting again, never searched, kept until everything is in,
forgotten by the idle turn) and the index forgetting what other marks made while keeping these two. Stand-in encoders only.
Not a check: the changed worker code read four pictures on the real model in its jail (a Letter page sized 1,211 by 1,568
and cut into 520 pieces, 0.38 s each, 1,967 MiB at its peak of the 2,705 allowed, cosine .967 to .970 with the live
index's 280 vectors of the same three pages).

The button at the head of All responses waits for a late scan too (2026-10-11, GRADING "THE CERTIFY STEP WAITS FOR A LATE SCAN";
the instructor: "change the All responses 'Continue to certification' button too"). `node --test tests/test_assignment_flow.mjs`
covers `onwardLink` (certification with nothing coming, no workflow, a current certification or responses still to review; the
scan, with its reason as the title, for a late scan never certified, changed, and with responses still to review; several
scans) and `lateScan`, the one reading the step graph and the Review step's way on now share: `tests/test_grading_overview.mjs`
and the Review step's test pass unedited, word for word. `manage_checks.py browser --browser grading` reads the button with
the plan's read rewritten to a later scan (its words, link and title, after Refresh: a step reuses the plan's read for 10 s)
and again without it. No server change.

The Review step's way on waits for a late scan too (2026-10-11, GRADING "THE CERTIFY STEP WAITS FOR A LATE SCAN"; the
instructor: "align the Review step's 'Continue to certification' line too"). `node --test tests/test_assignment_flow.mjs` covers
`reviewNext` with everything reviewed and a later scan in the Inbox (never certified, changed, a summary without a
certification): the line's words, the button and the scan it opens; several scans and none named; "Continue to certification"
as before with nothing coming or a current certification; and a response to review, unfinished grading or nothing final still
coming first. `manage_checks.py browser --browser grading --browser grading_review` pass unedited (they click the head's plain
"Continue to certification", which stays); neither builds the late-scan state. No server change.

The quiz check is told that no page names its version (2026-10-11, QUIZZES "NO VERSION ON THE PAGE", "THE CHECK IS TOLD SO
TOO"; the instructor, shown the check's notes word for word: "add the sentence"; `quiz_check` v4). `test_grading_prompt_contracts`
`test_the_quiz_check_is_told_that_no_page_names_its_version_and_nothing_else_changes` covers v4 as v3 word for word with exactly
one sentence more, what that sentence says, and the versions still listed among what the check looks for; `test_quiz_drafts` and
`test_makeup_drafts` pass unchanged (the skip of unchanged problems keys on the prompt in use, so it is unaffected within a
run). Read by a real model the same day, on the instructor's word ("run it on a real model to confirm the remark is gone";
not a check): the three requests of the entry below, sent again under v4 on a fresh isolated copy (deleted afterwards;
`quizcheck.json` and `quizcheck-planted.json` beside `gate.py`, the v3 answers kept as `*-v3.json`). No answer's notes mention
the missing version letter, where all three did under v3. The full check (105 s) this time reported one layout issue on
problem 2 (the single 8.5 cm rule for "explain why it is the final tableau") that the v3 run had not: one run each, so not
attributable to the sentence. The check sent only the changed problem's pages (23 s) carried that issue and added none, and
the planted contradiction (32 s) was again the one issue reported.

The certify step waits for a late scan (2026-10-11, GRADING "THE CERTIFY STEP WAITS FOR A LATE SCAN"; the instructor: "align
the Grading card's certify wording too"). `node --test tests/test_grading_overview.mjs` covers the card for a reviewed plan with
a later scan in the Inbox, never certified and changed since certification: the last step still next, by its own name, place
and done rule, its status "Waiting for a scan", the "Next" line, button and link the scan's, the title still opening
Certification and Export, the card's markup without "certify the grades"; several scans and a summary naming none; certifying
next again with nothing coming; a current certification left alone; and a review still open coming first.
`tests/test_home.mjs` still holds Home's row back. The grading browser scenarios pass unedited; none of them builds this state,
so the card was not read in a browser with a late scan. No server change.

Posted in Canvas, and Home's certify row (2026-10-11, TEACHING-WORKFLOWS "POSTED IN CANVAS", FRONTEND's `grading:<plan>:certify`;
the instructor: "build the Canvas posted/hidden line and fix Home's certify row"). `test_teaching_review`
`test_delivery_carries_what_the_gradebook_last_read_of_each_columns_posting` covers the delivery read on a certified plan: no
posting before any read; after reads recorded by the gradebook's own `canvas_posting.record` for two Canvas courses, exactly
`canvas_posting.marks` for the column (another column's read left out); a later read of one course replacing that course
only; nothing written; and the assignment overview reading the same columns. `node --test tests/test_teaching_workflows.mjs
tests/test_home.mjs` cover `canvasPostedText` (not read, no grades, all shown, some hidden, all hidden, one grade, several
columns with one unread) and the certify row held back by a scan still in its Inbox checks, for a changed certification too,
and back once the scan is through. `manage_checks.py browser --browser assignment_workspace` reads Return's new line and its
gradebook link with nothing read; `--browser teaching_workflows --browser home` pass unedited. Synthetic catalog; Canvas is
never reached.

Why grading waits, and Canvas by situation (2026-10-11, ASSIGNMENT-WORKSPACE "WHY GRADING WAITS" and "DELIVERY NAMES CANVAS",
TEACHING-WORKFLOWS "EACH CANVAS SITUATION BY ITSELF"; the instructor: "build the stalled line and gaps 1 and 3").
`test_assignment_workspace` `test_the_overview_carries_why_grading_waits_only_while_responses_wait` covers the detail read on a
graded plan: no `auto_grade` while nothing waits, the owner's `auto_grade_state` exactly once responses wait (the quiet read
too), never in the list, the rest of the body unchanged, a cancelled last batch read as paused, and an unreadable state said
as nothing with the assignment still answered. `node --test tests/test_assignment_workspace.mjs tests/test_teaching_workflows.mjs`
cover `gradingWait` (each of the Grade step's sentences, which are the instructor's and link the step, the preparation case,
nothing said with nothing waiting or no state), `deliveryText` with Canvas (not sent, sent, to check, several columns, no
column as before) and `canvasReceipts` / `canvasSentText` (each count, their order, "Nothing sent"). `manage_checks.py browser
--browser assignment_workspace` reads Return's line with nothing sent and, with the overview's read rewritten to twelve
waiting responses and a column's receipts, the paused sentence in ink with Open Grade, the Delivery row, and the settle
sentence with no link. Synthetic catalog; Canvas is never reached.

Upload the scans, and what was typed on Paper (2026-10-11, ASSIGNMENT-WORKSPACE "UPLOAD THE SCANS" and "NOTHING SCANNED YET",
DOCUMENTS "WHAT WAS TYPED ON PAPER STAYS"; the instructor: "#2: ask first, #4 upload the scans"). `test_assignment_workspace`
`test_upload_the_scans_waits_for_you_once_the_date_has_passed_and_no_paper_is_back` covers the item on the catalog: absent for
an exam scored by hand, present once a plan exists and the main sitting's date has passed (the list the same), never beside
"Find or excuse", unmoved by a later makeup sitting, absent before the date and on the day, absent with every student absent,
and gone once a scan is linked. `node --test tests/test_assignment_workspace.mjs tests/test_assignment_wizard.mjs` cover
`papersBack`, no row deciding before a paper is back, the Collect tab's mark, and `finishLaterWords` (nothing asked with nothing
typed; one, several and unlabelled versions). `manage_checks.py browser --browser assignment_workspace` draws the two reads with
no paper back (the scans, the collection counts, the stage and the server's item replaced in the answers): the item on the
overview, the tab's mark, the lead, no orange count, Upload scans the one primary, "No papers scanned yet", both students
folded with their decisions and no form open. `--browser assignment_wizard` types a label, chooses a file and adds a second
version, leaves Paper by Back and by the step line and finds all three again under the same choice; declines Finish later's
question (its exact words) and stays; and, in another window, accepts it and reads the notice. Synthetic catalog; nothing
outside is reached.

The step graph follows the review (2026-10-11, FRONTEND "The step graph follows the review"; the graph read "Review 0% · 0/5"
beside a rail at 4 of 5 final until a reload): `manage_checks.py browser --browser grading_review_blank` reads the graph's
Review count with no reload after the gallery's confirmation (40% · 2/5) and after each Approve & next (60% · 3/5, 80% · 4/5),
each time beside the rail's own count. It holds the one plan read every change is followed by (held, never altered) to show that
the graph keeps its count until that read answers, that the graph's own node is patched rather than drawn again, and that what
the workspace shows, the button in focus and the address stay; a reload then reads the same count. `--browser grading_review`
reads it after an approval in All responses (0% · 0/2 to 50% · 1/2, the next response's desk left as drawn). Both fail without
the change. `node --test tests/test_grading_run.mjs` covers the follower with hand-held timers and reads: one read a second
after the last of several changes, a change during a read read for again with the older answer not drawn, a failed read, and
nothing read or drawn once the page is left. The checks use a synthetic plan of five responses. The plan read's cost was timed
once on the live app, read only, after the change went live (2026-10-11, Exam 1: 2,701 responses, 23 questions, fully reviewed
and certified, nothing queued or running, load average about 6 of 14 cores): the default-body plan read took 456, 445, 443, 504
and 910 ms (median 456 ms, 522 KB, 88 KB sent); with `include=activity` 578, 643 and 461 ms; the queue of one question, which
Review already reads after every decision, 137, 195, 137, 134 and 180 ms (median 137 ms); the whole queue 251, 659 and 253 ms.
With nothing waiting both queue reads were at their cheapest.

Signals that hold (2026-10-11, ASSIGNMENT-WORKSPACE "Signals that hold", DOCUMENTS "THE CHOICES STAY WITH THEIR COPIES"; the
instructor chose four findings of a pipeline review). `test_exam_overview`
`test_all_reviewed_is_not_ready_to_certify_while_work_is_still_coming` covers the grading stage with a scan waiting (its count,
the scan it opens, the Inbox when the summary names none, `counts.pending_scans`, the next step "Finish the Inbox checks"), with
tasks running, with both, with a changed certification, and unchanged with nothing coming or a current certification.
`test_assignment_workspace` covers the reviewed plan on the catalog (`test_certify_waits_for_a_late_scan_as_certification_itself_does`:
Certify grades listed and "ready to certify" said; a late scan linked, after which certification's own `inbox_pending` blocks,
the item is gone from the overview and the list, and the row opens that scan; the scan deleted in the Inbox, and both are back)
and `work_items` alone. `node --test tests/test_assignment_workspace.mjs` covers `decidesInRow` and the row found again by both
permanent IDs. `manage_checks.py browser --browser assignment_workspace` reads Collect with one paper missing: the collection
record's link, no primary, the caution once, the form open in the row without a fold; a settled row's fold staying open on the
same element through the 30 s refresh; with one row unsaved, another row saved twice (versions 2 and 3, never refused), its fold
closing and the unsaved row untouched; a refused save keeping its words; each discard reading its own row again while the other
stays unsaved. `--browser assignment_wizard` changes two copies to three and back with the list folded and reads the student
chosen for copy 1 each time, then in the job made. Synthetic catalog and scans; nothing outside is reached.

The switch for the rubric check while a quiz is drafted (2026-10-11, QUIZZES "THE SWITCH"; the instructor, told a first revision
takes about 7.5 minutes longer and that no switch existed: "build the switch"; workspace `draft_rubric_check`, on by default):
`test_quiz_drafts` `RubricQuestionsAtDraftReview.test_the_switch_off_leaves_the_check_to_the_approval_as_before` covers a
revision requested with it off ending with its keys and no check (no reviewer request, no `rubric_check` in its result or
summary, no warning, nothing to answer), the next revision with it on checking every problem, a revision with it off again
holding no check while the one before keeps its own, a finalization tick on it refused, and its approval queueing the plan's
check as before. `manage_checks.py browser --browser grading_settings` reads the switch in Compute settings beside the Rubric
drafting and review model, on by default, and saves it off. Scripted model host, real LaTeX compile.

The blank-answer gallery and the flag review that starts by itself, in a browser (2026-10-11, GRADING "Proposal gallery" and
FLAG REVIEW; the three review pieces of 50f6edc had backend and unit coverage only): `manage_checks.py browser --browser
grading_review_blank` puts five papers of one question through the direct-reading pipeline (two blank answers that may be
confirmed together, one blank on a crop that may be misaligned, one held grade from a batch two days old, one held grade just
graded) and reads everything from the server's own answers, no route replaced: "See the 3 possibly blank answers" and the
"Possibly blank" filter; the gallery asked with `only=blank`, each tile's two crops (as scanned, then high contrast) equal to
the API's, the misaligned one greyed with its reason and opened alone; the gallery's own tick, the button disabled before it,
Alt+A posting nothing, Set aside clearing the tick; the confirmation posting `only: "blank"` with exactly the two eligible
responses and each getting its own approved review with the gallery's comment; the row saying it starts by itself for the 1
just graded while Ask sends all 2; only that response labelled "With the flag review" and stepped past, the "1 response with
the flag review" state and Review them yourself; one read every 15 s while a review is due; and, after the worker's own turns
request the review, the row turning into its progress with nothing pressed and no POST from the page. Twelve one-line faults
served in place of the product scripts each failed it at the intended assertion. Not covered: a start that is refused or
switched off (its sentence is in `test_grading_run.mjs`), a review that runs to its rulings, and the gallery's paging.
ENTERING REVIEW (2026-10-11, GRADING FLAG REVIEW; the instructor, told Review could open first a response the flag review was
about to settle: "Step past it on entry"): the same check enters Review without naming a response while a review is ruling on
the only one that waits, and reads the "1 response with the flag review" state, no desk, and an address that no longer names
it; enters again with the flag review's first read held back (delayed, never replaced) until a click in the workspace, and
reads the response labelled and still open; and after a reload whose address names the response, reads it open. The check
fails at the first of these with the page script as it was before the change.

The quiz check's `unchanged` paragraph read by a real model (2026-10-11, on the instructor's word: "do 1, 2 and 3"; not a
check). On a fresh isolated copy of the catalog, with the Rubric drafting and review model, the step itself
(`_quiz_checked`, `quiz_check` v3) was run on Quiz 4's stored approved draft (3 problems, 4 versions, 2 pages each;
`/mnt/nvme/Teaching/tmp/streamline-gate/gate.py quizcheck` and `planted`, results `quizcheck.json` and `quizcheck-planted.json`
beside it): every page, 85 s, 8 pages and 2 example pages sent, no issue; problem 3 forgotten from what that check saw, 28 s,
only page 2 of each version sent, problems 1 and 2 named `unchanged`, no issue and none about a skipped problem, its notes
saying page 1 was not among the pictures; and, by the code's own path, Version A of problem 3 changed so its statement
contradicts its solution (4 pounds of flour against 2), 35 s, again only page 2, problems 1 and 2 `unchanged`, exactly one
issue: that contradiction, Version A, problem 3, kind correctness. Three requests, about 44,000, 33,000 and 33,000 prompt
tokens. The copy was deleted afterwards. The paper path's automatic steps are still unread by a real model.

The Follow-up page's Write to student… (2026-10-11, TEACHING-WORKFLOWS "ACTION DESK"; found while attributing three browser
checks that failed on `main` since the action desk landed): the desk removed the decision box's own Write to student… and the
row under the conversation still left it out for a follow-up that waits on the instructor with no step awaiting review, so such
a follow-up offered no way to write. The row now always holds it. `manage_checks.py browser --browser followup_same_matter`
is the check that fails without it (a new email that needs a reply, nothing suggested: Write to student… opens the reply in
the student's thread). The same run's other two failures were the checks': `followup_connect` and `followup_same_matter` now
expect a row and the heading titled by the next step with the subject under "Conversation:", and `teaching_workflows` opens
Office hours from the shared navigation row's button. The other six `followup_*` browser checks pass unchanged.

Accepting a draft readies it (2026-10-11, GRADING "ACCEPTING A DRAFT READIES IT"; the instructor, choosing from a measured list:
three reads of a drafted rubric become two): `test_grading_rubric_drafts` `AcceptingADraftReadiesIt` covers the confirmation
recorded as `draft-acceptance` only for the questions an acceptance created, with the drafted clarifications in its digest; an
uploaded CSV left unconfirmed; a comment and a changed line each reopening exactly their question; the same request again
writing nothing; an acceptance whose confirmation is refused still creating its questions; NOTHING read at acceptance and the
button still refused before an area is confirmed; the key read once its answer area is confirmed, one ordinary reading named
`area-confirmed:<confirmation>:<question>`, sent with one picture, the problem's text and the worked solution, never for a
rubric that proposes its key, an uploaded rubric or a rubric the instructor confirmed again themselves, and unused until saved;
the last answer area leaving the check unqueued while the reading it asked for is pending; `request_rubric_review_when_ready`
writing nothing for each wait and exactly one check, `draft-review:<plan>:<digest>`, with the saved key and the printed crop in
each question's reference, when the last thing arrives; and the plan read carrying `rubric_review_waits` exactly while such a
plan waits. `test_grading_rubric_versions` and `test_grading_rubric_adapt` check every version's acceptance and the adaptation
the same way; `test_quiz_drafts` and `test_makeup_drafts` pass unedited. `node --test tests/test_grading_guide.mjs` covers step
1 no longer asking for those confirmations, the toast's sentence and the Key & Rubric Finalization lead; `manage_checks.py
browser --browser grading_rubric_drafts --browser grading_rubric_adapt` read the toast, the step and that lead. Scripted model
host and reader; the check that starts by itself was never run against a real reviewer.

Rubric questions at draft review (2026-10-11, QUIZZES "THE RUBRIC CHECK IS MADE WHILE THE QUIZ IS DRAFTED" and "THE CHECK MADE
WHILE DRAFTING IS THE PLAN'S"; the instructor, choosing from a measured list) is covered by `test_quiz_drafts`
`RubricQuestionsAtDraftReview`: a revision ending with one request per problem that is the revision's own, sent the plan's
payload with no identity, its concerns in the result and summary; a problem that reads as before keeping its result unasked,
and no request when nothing changed; answers as a revision that runs only Resolve and changes nothing of the quiz, files,
rubrics or keys; a failed resolution leaving the revision it answered; an approval inserting the completed check under the
fingerprint a queued check computes (asserted by queueing one) and confirmed there by the ordinary tick; an approval without
keys, another PDF or changed content queueing as before; the ticked box recording the ordinary confirmation, after which the
batch gate takes every question; a tick with a decision open or a stale fingerprint refused; a tick that cannot be honoured
confirming nothing and saying so; a rebuild carrying the check, never the confirmation, and still rebuilt after a finalizing
approval; a failed check as a warning; a one-version quiz. `test_grading_rubric_quality` passes unchanged over the default host
and `CheckHosts` covers the seam. `node --test tests/test_quiz_drafts_ui.mjs` covers the lead's three forms, the box and the
page's line; `manage_checks.py browser --browser quiz_drafts` the decisions on the draft, the answer sent, the box and the
finalization recorded. Scripted model host, real LaTeX compile and sandbox crops. The one real-model run is not a check: on the same isolated copy
(2026-10-11) the draft-time check of Quiz 4's stored draft made 3 requests (64 s, 197 s and 442 s, in parallel) and ended
complete with 5 decisions, 88 clarifications, 80 exclusion pairs and no points overflow, the shape of that quiz's checks after
approval (4 to 6 decisions, 88 clarifications); a first revision of such a quiz therefore takes about 7.5 minutes longer, the
time the check took after approval before (`check.json` beside the script).

The answer key carried through a drafted quiz (2026-10-11, QUIZZES "THE KEY IS READ WHILE THE QUIZ IS DRAFTED" and "THE
APPROVAL SAVES THE KEYS IT SHOWED"; the instructor, choosing from a measured list: "Carry the key through") is covered by
`test_quiz_drafts` `AnswerKeysAreCarriedThrough`: one request per problem and version after the rubrics, each the revision's own
under its own step, carrying the worked solution, the writer's answers, the correct choice, the question's own rubric lines and
the printed page; the keys in the revision's result and summary; a carried rubric carrying its key with no request; a refused
or unreadable reading a warning and no key, read again by the next revision; an approval with `save_keys` creating each question
with its key in revision 1 and the queued rubric check's reference holding it; an approval without it saving none, the plan's
own reading then sending the stored worked solution; a rebuild keeping the keys and asking again only for the question still
without one. `test_makeup_drafts` checks a makeup the same way. `test_grading_rubric_drafts` `AnswerKeysFromTheBlankExam` covers
the plan's own reading (a request queued under v1 still that reading; an uploaded CSV's question still created without a key),
and `test_grading_prompt_contracts` that `answer_key_read` v2 is v1 plus one paragraph. `node --test tests/test_review_notes.mjs
tests/test_quiz_drafts_ui.mjs tests/test_absence_makeups.mjs` cover the key row, how each part was found, the counts and both
approval summaries; `manage_checks.py browser --browser quiz_drafts` the key rows, the lead and the `save_keys` the approval
sends. Scripted model host, real LaTeX compile. The one real-model run is not a check: on the instructor's word (2026-10-11,
"Real-model run first") the key step read Quiz 4's stored approved draft on an isolated copy of the catalog with the Rubric
drafting and review model (12 requests, about 17 s in parallel, every key read), and the key check replayed on the 541 retained
full-credit answers matched 261 with the new keys against 259 with the saved ones, all 259 kept, no mismatch, and no outcome that
raises a review reason changed over the batch's 915 results (`/mnt/nvme/Teaching/tmp/streamline-gate/gate.py`, `compare.json`).

One line per cause on the review card (2026-10-11, GRADING Continuous review workspace "ONE LINE PER CAUSE"; display only:
nothing about what is held or flagged changes): `test_grading_review_queue` covers the response read's opt-in `details=review`
(the default body byte for byte the same, nothing written, a hidden proposal's body the same whatever is asked, the causes
present once the instructor's own decision ends the hiding); the partition (every listed sentence once and verbatim, each cause
and the causes in the desk's order); that `risk.assess` and `recognition.review_state` hold no sentence without a name and that
every sentence they name has a cause, over seeded verdicts that reach each one (a new sentence fails until it is named and
placed); one cause said two or three times gathered once (the disagreement, a reading issue, a rubric doubt, a blank area); a
ruling's sentence as its author's note; and a sentence nobody named kept under `other`. `node --test
tests/test_grading_review_layout.mjs` covers the cause lines, "+N more" and the authors, the closed fold with every sentence
under its label, the plain list byte for byte without the causes or when they are not exactly the listed sentences, and nothing
for a hidden proposal. The modules whose sentences were named keep their own tests unchanged (`test_grading_risk`,
`test_grading_answers`, `test_grading_fixes_decisions`, `test_grading_pipeline`, `test_grading_rubric_quality`,
`test_grading_arbitration`, `test_grading_standard`). `manage_checks.py browser --browser grading_review_queue --browser
grading_loading --browser grading_review_rules --browser grading_reading_context` read the desk with `?details=review`. Fakes
only; no provider is reached.

The quiz check's skip of what it already saw (2026-10-11, QUIZZES "THE CHECK SKIPS WHAT IT SAW") is covered by
`test_quiz_drafts`: `UnchangedProblemsAreNotCheckedAgain` (a first revision's check request as before; a later revision asked
only about the changed problem, the other named `unchanged`, only its pages sent and the earlier finding carried to the writer;
no check at all when nothing printed changed, the carried finding shown when the fix cannot be laid out; a problem only
respaced or pushed down checked again while its rubric is kept; every page sent again after a finding about the whole quiz),
`CheckRecords` (what a fix leaves as seen) and the fix-round test (the problem a fix changed is asked about by the next
revision). `test_makeup_drafts` checks a makeup's later revisions the same way. Scripted model host, real LaTeX compile; the
v3 prompt's one added paragraph has not been read by a real model.

The flag review that starts by itself (2026-10-11, PIPELINE and GRADING FLAG REVIEW "IT ALSO STARTS BY ITSELF"; the instructor,
choosing from a measured list: "Flag review runs itself"): `test_grading_auto_flag_review` covers a finished batch followed once
(the look, the request once it settled with actor `automatic` and an `auto:` request ID, the review marked `automatic`, the
responses it names while it runs, its ruling finalized by the ordinary carrier batch, and that carrier batch never followed); a
grade a review left, and a stopped review, never sent again by itself while the button still sends every held grade; nothing
while the switch is off, while a batch is about to start by itself or after a cancelled batch; a batch that finished longer ago
than the window, or that only recalculated points, left to the button; a review that cannot start recorded once, also across a
restart, and started once the settings change; a decision made while it settles standing; and `only` naming the grades asked
for. `test_pipeline_improvements` holds the worker's step order, `node --test tests/test_grading_run.mjs` the row's sentence
(it starts by itself, for which grades, why it did not). Fake hosts only; the counts behind the decision (33 of 137 and 14 of
58 left) were read from the catalog, not replayed.

Blank answers in the gallery (2026-10-11, GRADING Proposal gallery "BLANK ANSWERS ARE APPROVED HERE TOO"; the instructor:
"Blank answers in one gallery"): `test_grading_proposal_gallery` covers the rule from a stored result (`BlankAnswerRule`: the
whole response empty, a partly empty one, something transcribed, credit proposed, an unregistered crop, graders that differ, no
line ticked, a result from before `registered` was kept) and, through the direct-reading pipeline with a reading that found
nothing (`BlankAnswers`), a zero the graders agree on approved from `only=blank` with both crops on its tile, its comment and
the event's count, and credit proposed for an empty area shown, greyed and refused. The replay behind the rule is not a
check: 93 of the 122 blank holds of the four graded assignments eligible, the six the instructor changed among them.

Certify without a final tick, and a confirmed answer area kept through a rubric re-import (2026-10-11, GRADING Certify and
"KEEPS A CONFIRMED ANSWER AREA"; both chosen as repeats of what was already confirmed): `node --test
tests/test_grading_certification.mjs` covers the answers still needed with no final tick, `tests/test_assignment_workspace.mjs`
one changed certification counted once on its tab, and `manage_checks.py browser --browser grading_certification` Certify
offered once the last group is answered, the footer naming where it records, and Enter on a tick cancelled.
`test_grading_locations` covers a new rubric invalidating a check nobody confirmed, a re-import with the same maximum
carrying the confirmed area by a receipt (no new check, the event's `location_carried_forward`, a batch no longer refused),
another maximum asked again, and a first rubric with nothing to carry; `test_grading_answer_groups`,
`test_grading_rubric_edits`, `test_grading_rubric_versions` and `test_grading_rubric_drafts` still pass over the one helper
(`carry_location`) their four carries now share. Synthetic catalogs only.

ACTION DESK (2026-10-11, TEACHING-WORKFLOWS): `test_followup_commitments` exercises independent tasks in existing
follow-up revisions, preserving tasks through edits and replies, completion notes, linked accepted Mail receipts,
prerequisites, cycles, student/source ownership, stale edits and exact retries. `test_followup_desk.mjs` covers the
work-before-reply projection, retained check backs, uncertain sends and separate task/conversation counts.
`manage_checks.py browser --browser followup_desk --keep` checks the Action desk and Tasks views, explicit
task completion with another task left open, the selected conversation, and 1440/1280px layouts. All records are
synthetic and external services are blocked. Run `test_student_support`, `test_followup_completion`,
`test_followup_suggestions`, and the existing followup_controls/navigation browser checks with changes here.

Confirmed by a later read, and one notice (2026-10-10, CANVAS NIGHTLY SYNC "CONFIRMED BY A LATER READ"; the instructor, shown a
morning sync whose five grades reached Canvas after Faculty stopped waiting: "make both changes"): `test_canvas_nightly`
(`ConfirmedLaterTests`) covers a morning grade Canvas takes and applies late (`fake_canvas.py` `hold_grades` /
`apply_held_grades`: an update whose Progress stays queued): uncertain with its post withheld and named by course, a later round
that reads and confirms nothing while Canvas still shows the old grade, then the grade confirmed by reading, the withheld post
carried out once with its own receipts, the View's `unconfirmed` and `posts_failed` cleared and the grade never sent twice; a
post with nothing left to show done without asking Canvas; a column no longer to be posted left alone; the follow-up stopping
once the instructor's own Sync is the last (whose read confirms too); the next morning's read confirming what the instructor's
Sync could not read back, with a later change then going by itself; a read Canvas refuses changing nothing; only the latest
receipt, the assignment read and the value as sent confirmed, once; and the count of posts that still need the instructor.
`node --test tests/test_gradebook_grid.mjs` covers the one notice (counted from `unconfirmed`, the morning's posts in the same
sentence, none once all are confirmed, the morning's own notice for posts only when the send was confirmed or another sync came
after). `manage_checks.py browser --browser canvas_posting` shows the single warning with one Sync with Canvas button after
such a morning, and none after the next round. Fake Canvas only.

Mail a follow-up has settled (2026-10-10, MAIL "MAIL-FOLLOW-UP"; the instructor: "when an email from a student is related to a
follow up, can we have it cleared from the inbox the same as those emails that have a reply"): `test_student_support` covers
`settled_mail` (a suggestion nothing tracks, each saved status with only Needs my reply unsettled, another student's
follow-up, a message the record never captured, a newer message reopening it, another conversation before and after it is
joined, the instructor's own message, nothing saved, a Canvas notice settled by its email, and only the asked student's
conversations read). `test_jev` covers a reopening message unsettled until a scripted reading calls it a thanks, and unsettled
again under a possible wellbeing concern. `test_gmail_mail` covers the Students list with a stand-in (the settled thread left
out and still in All mail, one question a Gmail page about each thread's last message, the scan reading on, a page without
students asking nothing) and with the real roster, record and follow-up service over the fake Gmail (listed before capture and
while only a suggestion, gone once Resolved, back for a new message before and after its capture, gone again once Waiting;
Gmail never written). `manage_checks.py browser --browser mail_followup` opens the Mail page over the same real services: the
student's thread listed, gone once its follow-up is Resolved with the page saying where such mail goes, still in All mail and
opening there, and listed again when the student writes. Synthetic catalogs only. The replay behind the rule is not a check: a
copy of the catalog on NVMe and the thread IDs the live list showed (all six settled, 66 ms;
`/mnt/nvme/Teaching/tmp/mail-followup-clear`, 2026-10-10).

Connect a message (2026-10-10, TEACHING-WORKFLOWS and ASSISTANT "CONNECT A MESSAGE"; the instructor: "can you connect the
response card I sent to Brenna to her followup?", "joins shouldn't be instructor only"): `test_student_support` covers a sent
card's email joined to a waiting follow-up by its message (the preview's exact answer and that it writes nothing, another
student's or an unknown message, a key and a message together, a message already in the conversation, the idempotent join,
the email as Your reply with the status and next step kept, and the student's answer in the card's thread reopening that
follow-up) and the tiers (join and Keep separate `auto` with a request ID, the preview `read`). `test_followup_connect`
drives the chat's `join_followup` and `separate_followup` with a scripted session: the card's message found in
`student_context`, the join recorded at once as the Assistant's action with its summary and Faculty's request ID, nothing
recorded for a refused or repeated join, `joined` in the student's case, the undo at once, a card instead in a review-only
chat, another conversation joined by its key, and a follow-up preparation refused both. No card for a redo (ASSISTANT "NO CARD
FOR A REDO"): `test_followup_redo` covers a redo that replaces waiting steps starting at once, and a review-only chat still
asking first. `manage_checks.py browser --browser followup_connect --keep` asks the floating chat on the Follow-up page to
connect a card's email (scripted engine): no card, the follow-up shows "Also in this follow-up", the email as Your reply and its
unchanged status within seconds, then separates it again (CSP checked). Synthetic catalogs only.

A follow-up's "Remember this" (2026-10-10, ASSISTANT-MEMORY "A FOLLOW-UP'S REMEMBER THIS"; the instructor: "make the
follow-up's Remember this do the same"): `test_assistant_memory` covers words that read on their own kept as typed with no
model asked; a fragment kept as the rule a scripted model wrote from that follow-up, with its receipt, the typed words in no
revision and Undo; a fragment kept as typed and marked when the model declines or fails or a check refuses; a rule that
names the student kept without them or not at all; what is about the one student (named or not), a record's fact and no
answer from Jev keeping nothing, each with its words; the page's answer saying what was kept; and the decision's gates.
`test_followup_suggestions` covers the note and the request carrying it. `node tests/test_assistant_workspace.mjs` covers
the toast for each outcome, its Undo, and the longer wait of a request that remembers; `node --test
tests/test_assistant_memory.mjs` the list's line. Scripted Jev and a scripted model only; the measurement is
`/mnt/nvme/Teaching/tmp/memory-rule-sandbox/remember.py`, invented text, sent only with `--live`.

Reworded more readily, and what was on screen (2026-10-10, ASSISTANT-MEMORY "THE REWRITE STEP"; the instructor: "Do 1 and
2"): `test_assistant_memory` covers a statement answering between the two bars kept without the mark and reworded, left
unmarked when the scripted model declines, and never sent at the bar or over it; and a fragment the scripted model
declines written from the follow-up on screen by the rule's writer with the rule's check as its receipt, Keep my words
after it, the follow-up never read under the rule's first wording, when their words resolve it or when none was open, and
the words left as typed and marked when the rule's check refuses, the model declines again or a typed number is lost.
Scripted Jev and a scripted model only. The measurement behind the bar and the routing is not a check and is never run by
one: `/mnt/nvme/Teaching/tmp/memory-situation-sandbox/measure.py` sends invented statements and invented mail only, and
only with `--live`.

Still being considered (2026-10-10, ASSISTANT-MEMORY "STILL BEING CONSIDERED"; the instructor: "the reword it option
didn't seem to do anything"): `test_assistant_memory` covers a view saying `remembering` with the memory being reworded
while the scripted model writes, the same before any note, nothing said to a conversation that sent nothing, the view as
it always was once the last note is in (reworded, left as typed, nothing kept), and the instructor's rewording in the
form reaching the chat whose note led there and no other (a move alone tells none). `node --test
tests/test_assistant_memory.mjs` covers the note while rewording, the links that name their memory and the instructor's
own wording without Undo. `manage_checks.py browser --browser assistant_memory` lets the scripted model answer 2.5 seconds
after the chat's answer: the note says "I'm rewording it", then shows the rewording, in the drawer and in the floating
chat; "reword it" opens that memory's form with the caret in it, the saved wording shows in the note, and the floating
chat is put away when it would cover the drawer.

Past work (2026-10-10, PAST-WORK): `test_assistant_past_work` covers recorded actions found by a described situation with a
stand-in embedder: the nearest few matters with what was carried out first and every outcome said (a declined proposal
nearest of all never leading), the actions of one student, thread or record as one result with the rest counted, an action
found by its summary or by its request's own words and never by an identifier, each action read once with one text a
request, an index that holds no words and is filled again when deleted, a first fill going on beside the request with what
is not read yet said, shared words standing in when the worker is off or silent, and `find_past_work` reading through its
own route (the log's route still refused, one GET route, nothing changed and no memory kept). Synthetic
actions only: how the real model ranks real records was looked at apart, read-only and in memory
(`/mnt/nvme/Teaching/tmp/past-work-replay`, 2026-10-10), and is not a check.

A rule said of one student's case (2026-10-10, ASSISTANT-MEMORY "A RULE SAID OF ONE STUDENT'S CASE"; the instructor: "Rule
only, rewritten"): `test_assistant_memory` covers a general rule typed about one student's follow-up kept as the rule a
scripted model wrote, with the typed words in no revision, the receipt of its check on the first revision, the list saying
whose words it is until the instructor rewords it, and the same rule said again changing nothing; nothing kept and the
chat saying so for each gate of the check, a decline, a failure, a student's name in the rule, a lost "this semester" and
a long message; a statement about that one student still refused with no rule written, and a word that is only also a
name still kept as typed; no picture kept with a rule; the follow-up on screen read for the writer and the check alone
(the student's two latest messages, cut short, names and UIN out, never the instructor's reply) and held by no other
request; and the decisions (naming a student and being about one are two gates; every gate of the rule check). `node --test
tests/test_assistant_memory.mjs` covers the three lines; `manage_checks.py browser --browser assistant_memory` types one in
a real drawer. Scripted Jev and a scripted model only. The measurement behind the gates is not a check and is never run by
one: `/mnt/nvme/Teaching/tmp/memory-rule-sandbox/measure.py` sends invented messages and invented mail only, and only with `--live`.

Memories given, the rewrite step and review findings 1, 2 and 4 (2026-10-10, ASSISTANT-MEMORY "MEMORIES GIVEN", "WITHDRAWN",
"THE REWRITE STEP", "EVERY WAY IN", "IN THE ORDER SENT"): `test_assistant_memory` covers what a request was given named by
reference with no words in a chat's events or a saved task, how each stands now in the conversation's view, and why one was
chosen; a removed, erased or reworded memory leaving a hosted conversation's history before its next message, and a model
process that held it ended and started again with the conversation's words (one restored with the same words left alone);
the form, the old preference routes and a follow-up's "Remember this" refusing a UIN, a whole name, a name Jev reads as a
student and a record's date, with nothing kept when Jev does not answer and each Save its own request; a message held up
never replacing the one sent after it, decided on the page and messages seen when it was sent; and the rewrite step with a
scripted model: taken with its receipt and the typed words as revision 1, refused for each gate, a decline, a failure, a
student's name, a lost number, a change meanwhile, and Keep my words with Undo after it. `node --test
tests/test_assistant_memory.mjs` covers the list under an answer in each state and the reworded note;
`manage_checks.py browser --browser assistant_memory` drives all of it in a real drawer. Scripted Jev and a scripted model
only: how Jev and a real model judge and write rewordings was measured apart, on invented sentences, in scratch
(`/mnt/nvme/Teaching/tmp/memory-rewrite-sandbox`, 2026-10-10), and is not a check.
Memory review fixes (2026-10-10, ASSISTANT-MEMORY "KEEPING"): `test_assistant_memory` covers the course a message names
deciding its owner before the page (a course Faculty lacks or two courses not kept and said so, a room never read as a
course, the decision told the named course), and a full store still taking a replacement while anything else at the limit is
said in the chat. `node --test tests/test_assistant_memory.mjs` covers the two lines.
A day they miss (2026-10-10, JEV "A DAY THEY MISS"): `test_followup_suggestions` covers an absence a preparation proposes on a
day the student wrote for a meeting, a due date or their return, and one stretched from the day missed to the meeting's day
(each a card naming the day when the scripted Jev doubts it), the day missed quoted in every place the student wrote it and
recorded by itself, no date of the record in any question, a span of days asked about once, and a bare weekday's `missed`
beside its `timing`. Run with `test_jev`, `test_jev_dates`, `test_absence_drafting`, `test_followup_completion` and
`test_decisions_sandbox` through `manage_checks.environment` on NVMe; scripted Jev only, so they do not measure how Jev
answers the question. That was measured once in the Decisions sandbox (`manage_decisions_sandbox.py copy`, then `real --checks
record_check --modes jev`; JEV "A DAY THEY MISS" has the numbers). Whole week only (JEV "WHOLE WEEK ONLY"): `test_jev_dates`
covers which weeks stand for an absence (Monday to Friday covered, the rest of the week, a part of a week, a weekend's rest of
the week, a day or span written beside a week); `test_followup_suggestions` covers a day or part of a week named only through
"next week" made a card before Jev is asked, the day written by itself still recorded, a whole week out recorded through the
week, and a weekday written beside a week read as the weekday with its `timing`. Every class day inside (JEV "EVERY CLASS DAY
INSIDE"): `test_followup_suggestions` covers a class day inside an absence the student never wrote (a card before Jev is
asked; every Monday to Friday without a Term schedule), the section's own days once its term uses a Term schedule, a day
inside written in a place of its own put to Jev (`missed_inside_1`) and held on its doubt, and the absence recorded when
every day is confirmed.

Memory pictures (2026-10-10, ASSISTANT-MEMORY "PICTURES"): `test_assistant_memory` covers a picture attached to a kept message
kept with it (a PDF left out, no file in any decision request, the kept copy served after the upload is gone, a row only ever
erased, Erase emptying the bytes), a memory of words told and listed exactly as before, a duplicate keeping nothing, an
attached picture bringing up the memory whose picture looks like it (and nothing when it is far, absent or unanswered), and
the whole path through the chat route with Undo and Erase. `test_picture_search_sources` covers a remembered picture found by
what was said about it and by a picture until its memory is removed or erased. `node --test tests/test_assistant_memory.mjs`
covers the thumbnails in the note and the list. `test_catalog_migrations` and `test_assistant` list `assistant/011_memory_pictures`.
Canvas files (2026-10-10, canvas FILES): `test_canvas_files` covers a file kept once per content and name and served only as a
download, a damaged kept file never served, the item refused unless it names a kept file under its own name in a folder of the
course's Files, the push putting the file in each course before its item (no session at the file service, receipts, nothing
uploaded twice), an unpublished item's file locked as it arrives and published with its item, a file only some sections get, a
corrected file replacing Faculty's own without a question, a file Canvas already has left alone (same size) or asked about (and
one replaced in Canvas since), two files of one name holding the item back, a refused upload (receipt, no item, the next push),
an upload Canvas never answered (uncertain, never sent twice), a course keeping a file unpublished, an existing item moved to a
file the push brings, a section's direct edit naming the term's kept file, the assistant's tiers, and the nightly backup
checking a kept file against its checksum. `manage_checks.py browser --browser canvas_files --keep` adds a file from the
window, saves, pushes to both courses, replaces it, is asked about a file replaced in Canvas, and adds an unpublished file on a
section's own page (`CANVAS_FILES_SCREENSHOTS=<folder on /mnt>` keeps pictures). Its `AssistantTests` (FROM THE ASSISTANT) cover the
outline without page bodies, a chat attachment placed at once through `faculty_write` (a draft: no card, no request to Canvas, actor
`assistant`), the push waiting on an Approve card and reaching FakeCanvas only once approved, a request answered once, the same
file refused twice in a module, a kept file placed by its ID, and every refusal (another Faculty file, a stale read, an unknown
module, a bad folder) keeping and saving nothing. Run with `test_canvas_content`,
`test_canvas_section_content` and `test_catalog_migrations` through `manage_checks.environment` on NVMe; FakeCanvas only.

Picture search (2026-10-10, PICTURE-SEARCH): `test_picture_search` covers one page a turn and one picture a request, a page
found by describing it (label, scan link, the image route serving it; no bytes, hash or name in a result), a deleted scan never
read or returned until restored, pages set aside and blank sides left out, archived scans waiting, a refused page set aside
while a silent worker sets nothing aside (and the three-tries rule), a file that cannot be read (a wait, then set aside, tried
again after a week), a page drawn again, entries of pages
that are gone, no embedding service (503 with the reason), bounds on the description, the Inbox worker's turn, the Assistant's
`find_pictures` showing nothing until `fetch_files`, the index's columns and file mode, the held matrix following the index,
`PRESENT` on every query, and both services' socket drop-ins. `test_picture_search_sources` covers the kinds added after
scans on each module's own fixtures: a printed page found by what is printed on it (versions as one result, a revised
question's words embedded again, the kinds filter), a bank picture found by its question until it is removed or its question
retired, one place for each kind with words, course notes pages drawn and read in the document sandbox (the current edition
only; the Assistant's `fetch_files` at that page), absence documentation found by look and never named (a PDF's pages; a
removed document), attachments on a student's message without logos or what an absence holds, pictures made flat PNGs, and
a file that is no picture set aside at once, and a look-alike filling a place after word matches only when it is near;
a page with a label and nothing else given no words (found by look until a question is stored for it), a page with nothing
on it never indexed in a printed copy, the notes or a student's file while the pages after it are still read, neither tried
again in a week nor counted unreadable, and what was worded another way read again.
`test_picture_search` also covers a scanned page found through the page it was printed from: one result a printed page
with its count of scans (a deleted scan not counted), a scan standing for its page when only scans are asked for, and
everything by look when no words match; and a search by a picture: by a file Faculty serves, by a dropped picture, by a
PDF's page, by a file attached in chat through `find_pictures(like=)`, nothing kept, a photo's route and a non-picture
refused. `test_assistant_memory` covers the attached-file note staying out of every memory request. `test_faculty_embedding` covers the mark on a refusal that is about the
picture. `node --test tests/test_picture_search.mjs` covers the strip's markup and which searches ask for pages.
`PICTURE_SEARCH_SCREENSHOTS=<folder on /mnt> manage_checks.py browser --browser picture_search --keep` draws pages on synthetic
data and drives the Inbox search: the strip, the viewer, a filename search with no strip, Clear search, the scan link, polls
keeping the strip's nodes, no page widening, CSP. A stand-in embedder throughout; run through `manage_checks.environment` on NVMe.

LEARNED FROM AN EDIT (ASSISTANT-MEMORY, 2026-10-12; it replaces ASSISTANT LEARNING's lesson review): `test_followup_learning`
covers which corrections are found (confirmed only, once, none from before the cutoff; an unchanged approval and a decline are
none), an edit of an edit, one correction a round asked on the page or in the chat, a reply sent from Mail, each comparison
(no name, number of the draft, grade, date or private note; a decision changed back and a grade-only edit teach nothing), a
correction becoming a memory once with its case saying so, each way one teaches nothing, a rule already kept, Jev not
answering, a correction left mid-way, the dispatcher's look (off in checks and with FACULTY_LEARNING_ENABLED=0, each case
handed once), what the follow-up says until it is taken back or reworded, a newer learned rule replacing an older one, and
the lesson routes gone. `LearnTests` in `test_assistant_memory` covers the keeping itself (both receipts, Undo, every code
check and gate, no answer, typed words never replaced, a full store, one transaction with the case's record, its turn on
the worker, what a request is told). The `.mjs` tests cover the toast (`test_assistant_workspace`), the follow-up's line
(`test_followup_suggestions`) and the Memory label (`test_assistant_memory`). Run the Python modules through
`manage_checks.environment` on isolated NVMe scratch, with `test_followup_suggestions`, `test_followup_completion`, `test_jev`,
`test_memory_sandbox`, `test_student_support` and `test_catalog_migrations`. `manage_checks.py browser --browser
followup_learning --browser followup_suggestions --browser followup_controls --browser followup_decision --browser
assistant_memory --keep` drives a reply edited and sent, the toast without a reload, the line, Change, Undo and the Term
policies dialog without its old tab, on synthetic data with a scripted writer and Jev. The gates' measurement is not a check:
it lives in scratch (`/mnt/nvme/Teaching/tmp/memory-edit-sandbox`) and sends invented text only, with `--live` said.
Still covered from the 2026-10-09 review: `test_followup_suggestions` covers both kept verifications, notes absent from every
Jev state but present in the prompt, a clock-free obligation fingerprint over three days with no facts built by a listing,
and an edit refused during a run; `test_followup_completion` covers Approve and Edit waiting and a warning on its own card;
the `.mjs` tests cover Before you send and no Edit while preparing.

One card for one change (2026-10-09, ASSISTANT-BATCHES ONE CARD FOR ONE CHANGE): `test_assistant_workspace` checks one
route grouping up to 200 cards, different routes refused past 20, a card moving between groups, Approve all with a receipt per
card, the groups listing (`route`, `states`, `pending`), Decline all leaving finished cards alone (instructor-only route) and
60 receipts continuing the task shortened, every card and error named.
`node tests/test_assistant_workspace.mjs` checks the one card drawn once, Leave out on waiting cards only and escaped summaries.
`manage_checks.py browser --browser assistant_workspace --keep` approves a two-card group from Changes and, in the floating
chat, 25 cards grouped in parts then as one: one card, one left out, Approve all for the other 24.

Changes beside the chat (2026-10-10, ASSISTANT "CHANGES BESIDE THE CHAT"): `node tests/test_assistant_workspace.mjs` checks
`conversationCards`: each card once (a group as its one card), what waits first then the finished ones latest first, the count of
changes that wait, one line a card in the conversation (escaped, its card's own chip, never an Approve), none for an action not
read, and no card drawn among the messages. The same browser check (`assistant_workspace`) covers the window: no panel before a
card, the card in the panel and one line in the conversation, the window wider by 440 px and whole in the browser window with
the conversation's width kept and the composer still in it, Hide and the two ways back (the line, which focuses its card, and
Show changes with its number), the right edge sizing the conversation beside the panel (the 360 px floor, the remembered width),
and the finished card staying with its line saying Done. It keeps `assistant-changes-panel.png` and
`assistant-changes-panel-done.png`. Run it with `assistant` and `assistant_capture`, which cover the window's other behaviour.

One conversation, real windows (2026-10-09, ASSISTANT "One conversation, real windows"): `node tests/test_assistant.mjs`
checks the message text (nested lists, quotes, rules, upright emphasis, math kept as its source with money left alone, number
columns, `tablesTSV`, `messageTime`) and the live step list. `node --test tests/test_assistant_chat.mjs` checks the one drawing
both surfaces use, the fold's time, Copy / Copy table / Ask again and where Ask again is offered, chips, starters, the composer's
markup, the pickers (where they open, what a choice carries) and `sendMessage` (one POST; a lost response settled by one read).
`tests/test_dom_patch.mjs` checks `data-patch-static`. `manage_checks.py browser --browser assistant --browser
assistant_capture --keep` drives a scripted model whose answers are held by gates. In the drawer: starters and the "/" list fill
the box without a model call; the live list and the clock; one queued message sent once and given back by Stop; Copy table on
the clipboard (the permission is granted to the test's browser context only); drawn math left alone by later polls; Edit; the
launcher's slate answer-ready mark; the click-only entrance; Ask again; Jump to latest; the drawer's width (drag, the 480 px
floor, arrow keys, kept across a reload, five tabs in view). In the floating window: resize by its corner (the 360 by 320
floor), remembered size and place, Minimise without ending the conversation, the round trip window to drawer to window with the
unsent words, and one queued message.

Redo from chat (2026-10-09, ASSISTANT "REDO FROM CHAT"): `test_followup_redo` replays Jared Zane's follow-up on a synthetic
catalog with fake Gmail and a scripted engine: the chat's Waiting on student refused until its reply is accepted, the chat's cards
listed with the follow-up, a redo that starts at once whether or not it replaces waiting steps (2026-10-10; a card only in a
review-only chat) and after the proposals stopped applying, the requested changes in the new preparation's prompt, Retry replacing only that conversation's chat cards,
an idempotent route and no redo from a follow-up preparation. `node --test tests/test_followup_suggestions.mjs` covers the chat
steps' markup (no Edit, no step number, Approving…). Run with `test_followup_suggestions`, `test_followup_completion`,
`test_assistant` and `test_student_support` through `manage_checks.environment` on NVMe. `manage_checks.py browser --browser
followup_redo --keep` shows a chat reply on a follow-up whose proposals no longer apply, at 1440 and 1280, and sends it from there
(fake Gmail, CSP checked).

Assistant tool-use guidance (2026-10-09): `test_assistant_tool_guidance` exercises finite routing, context and intent holds,
approval fallback, unchanged disabled behavior, uncertain-write deduplication, result coverage, loop limits, shared engine
hooks, one corrective pass, cancellation, ephemeral traces and check budgets. Run with the ordinary assistant, capture,
workflow, follow-up and Jev suites using `manage_checks.environment` on NVMe. No real provider is used; these are behavioral
checks rather than evidence of calibrated model accuracy. The existing verification UI renders a separate tool-use review.
Its 2026-10-09 review fixes are checked there too: an honest spent budget, approve-tier and error results that spare it,
per-message attempts, continuations that keep the instructor's goal, unheld preparation continuations, no DONE FOR YOU
records in the corrective pass, owner-guard text, no route or completion for a selected-area analysis, the first answer kept
when the corrective pass fails, announced waits, and Approve not waiting behind a slow fake preflight.

Automation visibility (2026-10-09): `test_assistant_automation` builds synthetic catalog rows (automatic certifications, sends,
makeup versions, morning syncs, automatic Assistant receipts, triage receipts) and checks Done by Faculty, the late-paper,
makeup-version and Student triage rows, rule rows' Prepare again target, Home's `assistant_changes` count and the review-only
message flag. `test_assistant_capabilities.mjs`, `test_assistant_preparations.mjs` and `test_home.mjs` cover the markup; the
`assistant_capabilities` browser check stubs rule rows with `waiting` and `held`, then drives Prepare again, Prepare now and the
Home card at 1440 and 1280 with CSP. Run through `manage_checks.environment` on NVMe; nothing reaches an outside service.

Assistant review fixes (2026-10-09): `test_assistant_capabilities` covers refused continuations (never recorded, never
blocking), the rule's lead time for a next stage, Stop cancelling a waiting stage, a stable readiness fingerprint while a
template is read, failed/replayed material imports and term owners, failed Canvas receipts and never-sent Gradescope grades.
`test_assistant_captures` checks the selection is sent once per model process and no empty hints block. Run them with
`test_assistant_workflows`, `test_assistant` and `test_catalog_migrations` (migration `assistant/008`) through
`manage_checks.environment`; `test_assistant_preparations.mjs` covers Cancel next stage.

Preparation review fixes (2026-10-09, second review): `test_assistant_workflows` covers, on real owner projections without a
patched observation where feasible, a stable fingerprint across a day change and later morning syncs, a moved exam date named
in the refusal, a 14-day "Once, now" reaching the model, a dispatch that reads with neither the approval nor the lifecycle lock,
withdrawn unanswered and failed-run cards, a staged next stage surviving its rule's check, an authoritative Stop, the unfinished
metric, held/waiting/last-outcome status, a finished term, the escalated replacing request, reads that never observe and one
observation per approved batch; its morning checks wait on another process's running post and on a retried morning.
`test_gradebook_reconciliation` covers columns kept off Canvas, changes waiting for the next Sync, `CanvasService.owner` and
the worker's daily read leaving a running post alone. Run them with `test_assistant_capabilities`, `test_assistant`,
`test_followup_suggestions`, `test_followup_completion`, `test_catalog_migrations` (migration `assistant/009`),
`test_canvas_posting`, `test_canvas_nightly` and `test_canvas_sync` through `manage_checks.environment` on NVMe.

Follow-through and conflict review (2026-10-09): `test_followup_suggestions` covers separate reply/work obligations,
changed-evidence receipts, bounded draft repair, retained warnings, record-write refusal during repair and review-only
recovery actions. Run it with `test_followup_completion`, `test_jev`, `test_jev_send_checks`, `test_assistant`,
`test_assistant_workspace`, `test_assistant_capabilities`, `test_canvas_content` and `test_canvas_section_content` through
`manage_checks.environment` on NVMe. `test_assistance_review.mjs` checks escaped comparisons, bounded context and labels.
`manage_checks.py browser --browser assistant_capabilities --browser canvas_content --browser assistance_review --keep`
exercises recovery, unsent text preservation, conflict copying without saving/pushing, the Assistant above an open dialog,
and desktop/CSP behavior, using only synthetic records and fake services.

Queue group of a To do (2026-10-11, requirement: "there are things in the 'waiting on students' section that are actually
waiting on me"): `node --test tests/test_followup_suggestions.mjs` covers a To do with work, a reply or both still owed, and a
Waiting on student follow-up a newer message reopened, each waiting on the instructor (`waitGroup` "you"); `tests/test_student_support.mjs` covers
`waitingOn`'s `on`.

What you did (2026-10-13, requirement: "a lot of these are 'waiting on me', but I have already approved the suggested
actions"): `node --test tests/test_followup_suggestions.mjs tests/test_student_support.mjs` covers the two groups
(`queueGroup`: steps, a question, a wellbeing flag or an unanswered message under To review; a To do, the instructor's open
tasks or their own unrecorded reply under Your to-dos; a row being approved keeps its group), the row's lines (`actedLine`,
`queueNext`), `todoSettles` and the note on a status held after an edited reply or a changed decision.
`test_student_support` covers a new message the instructor answered (saved status on every read, the badge, mail written
from the follow-up captured or seconds old, the student writing again) and `updated_by`; `test_followup_suggestions` covers
the approvals count (an edited card once, the newest preparation with an approval), the status held after a changed decision
on an absence card, and one preparation after a reply written outside the cards; `test_followup_deadline_closes` covers an
answered message still keeping a Waiting follow-up from closing. `manage_checks.py browser --browser followup_desk --browser
followup_decision --keep` checks the Your to-dos group and its row, Done and "Now waits on …" in the decision box, and the
held status card with its note. Synthetic records, scripted engines and a fake mailbox only.

Canvas conflict resolutions and error toasts (2026-10-09): `test_canvas_placement.mjs` checks that a pending resolution
replaces Push with "Add N texts to the draft" and "Save and preview again"/"Preview again", that a resolved row is never
replaced (even under Replace all), and that no static caller passes the unknown toast tone `error` (failures are persistent
danger toasts); `test_assistance_review.mjs` checks typed text and chosen rows in the comparison markup.
`browser_canvas_content` keeps typed text across a pairing's reload, chooses and sets aside a term resolution, resolves two
section conflicts without closing, previews again, sees a 409 push refusal stay as a danger toast with nothing written, and
adds one text to the draft with one toast. Run them with `test_canvas_content` and `test_canvas_section_content`.
The Canvas content pages (2026-10-11, canvas.md THE CONTENT PAGES): `browser_canvas_content` also holds that the bar is sticky,
that the course's tabs read All sections and each section and stay in Canvas, that one click acts once however often the page
was drawn (one Add item adds one item, of one line; Move up moves it one place and keeps the focus on it; its menu removes it),
that a push that went through is the bar's own line with each course to check, that the finished setup steps fold on the Canvas
page, and that the assignments' list scrolls in its own box with a find box.
OpenAI Decisions review fixes (2026-10-09): `test_jev` covers host failures (a 429 pauses triage with no failed receipt and one
request; the probe after the pause asks the same conversation as a new call; a timeout or a renamed model keeps its receipt and
pauses longer; Retry all failed asks each failed conversation once per request ID and stops at a host failure; SAME MATTER and
recovery run while Decisions is unavailable). `test_jev_send_checks` covers a wellbeing concern carried across a provider switch,
Decisions off, a failed re-read and a calm re-read until Mark reviewed or a newer message. `test_openai_decisions` covers the
host flags; `test_grading_cloud` a 401 receipt keeping only the error type and code; `test_grading_choice` the batch card's
multiple-choice aggregate; `test_decisions_sandbox` the TO DO THANKS v2 replay through `followup_checks.source`. JavaScript:
`test_jev.mjs`, `test_grading_run.mjs`. Run them through `manage_checks.environment` on NVMe; no provider is reached.

Hold bare Command to draw an area, then release it to send; two bare Command taps open empty floating chat.
`test_assistant_capture.mjs` checks timing, release/cancel callbacks, repeats, other modifiers, ordinary Command chords,
and input/grid/dialog scope. `browser_assistant_capture` checks that mouse-up does not send during a hold, empty holds,
Escape and blur cancel, double taps never capture or start a model turn, typed chat keeps its draft through a failed
request or changing tabs and transfers to
the drawer, and normal Command shortcuts remain intact. It retains the actual selection/PDF/unsent-grade checks below.

Floating chat beside page work (2026-10-09 review): `test_assistant_capture.mjs` adds the still-pointer hold and a slow chord
typed in the chat; `test_ui.mjs` covers the narrowed `holdCaptureFocus` (deferred blur once focus leaves) and toast tone
`error`; `test_assistant_captures` covers a selection joining an idle chat, replacement and refusals while busy or asking.
`browser_assistant_capture` saves a gradebook edit when another cell is clicked with the chat open, leaves Escape to a cell
editor, keeps the chat through a slow ⌘ chord, a cancelled hold over it and its own answer link, passes a ⌘-click after the hold
through (button and new-tab link), joins a selection to the chat, sends fresh page context per message, runs mail send checks
on the card in the Changes panel both ways its step can arrive (a later read, the turn held until the message's response is in;
and the message's own response, held until the turn has made the card: 2026-10-10, a turn quicker than that response left the
window with the step's line and no card), settles a lost `/messages` response without a second turn, retries a failed read,
keeps a finished selection chat on a hidden tab, and checks that toasts sit above a modal dialog and left of the drawer.

iClicker needs a note (2026-10-09, ASSISTANT ICLICKER NEEDS A NOTE, ONE ICLICKER FOR EVERY SECTION): `test_iclicker.AfterClassTests` checks the follow-up facts' header and that a section not read yet takes another section's session (expected) or finished import with none (none expected) that day. Attachments (2026-10-09, ASSISTANT ATTACH IN CHAT, canvas MESSAGES ATTACHMENTS): `test_assistant_uploads` keeps a chat file a day behind its route, names it to the model with the message, lets fetch_files and Mail read it, keeps bytes out of saved conversations and refuses the model's upload; `test_canvas_message_attachments` uploads Faculty and computer files once into the fake Canvas's conversation attachments with no cookie on the file service (201 and redirect-confirm answers), sends nothing when Canvas refuses a file, and needs a new review when a file changed; `test_assistant_attach.mjs` and `test_canvas_message.mjs` cover the markup; `browser_assistant` drives the paperclip and `browser_canvas_messages` attaches a computer file through to both courses' requests. Floating chat ONE CHAT and MOVABLE (2026-10-09): `browser_assistant_capture` drags the chat by its title bar (kept through an answer, a selection and a drag past the edge), sends a box to a selection chat with its unsent message kept and nothing asked, holds a box while the chat answers and sends it after, and moves the chat into a modal dialog a box was drawn in. Selected-area Assistant: `test_assistant_captures` verifies bounded text/record/image input, origin guards, read-only initial analysis, idempotency, transient source content, expired references and ordinary JSON compatibility. `test_assistant_capture.mjs` covers original-image coordinate mapping, object-fit letterboxing and exclusive shortcut opt-ins. Run `manage_checks.py browser --browser assistant_capture --browser assistant --browser assistant_capabilities --keep` on isolated NVMe scratch. The browser check uses actual DOM text, gradebook drafts, image pixels and rendered PDF pages; it fails if screen sharing is requested. It checks hidden/unselected text, source-image cropping, lost responses, draft preservation, PDF restoration and conversation transfer. `AREA_BROWSER=webkit` exercises the same test with an installed Playwright WebKit runtime; native macOS Safari qualification remains a device check.

Assignment flow (2026-10-09): `test_assignment_flow.mjs` covers truthful completion and prerequisite navigation; `test_print_files.mjs` covers separate PDF/remembered-stock actions. `test_documents.DocumentTests.test_version_preview_is_verified_canonical_without_allocating_paper` verifies canonical bytes, missing versions, tampering, no-store and no allocations. Run these with `test_assignment_workspace`, `test_teaching_review` and the file-viewer suites through `manage_checks.environment` on NVMe. Browser checks `assignment_workspace`, `assignment_wizard`, `documents`, `grading_review`, `grading_workflow` and `grading_certification` cover the entry menu, optional seating, preview, collection corrections, inline zoom, review decisions and explicit delivery controls using fakes.

Late papers certify themselves and makeup versions on Gradescope (`GRADING`, `GRADESCOPE`, 2026-10-09):
`test_certification_automatic.py` gives Carol (certified 0, or joined after the certification) a late paper, grades and decides it,
and checks the certification Faculty makes by itself: her cell only, live markers when nothing else changed, carried markers while
the instructor's own change waits, a paper still waiting for review or meeting a typed gradebook value waits, the worker's settle,
reasons recorded once, the switch, and her Gradescope send (papers and grades, papers only, nothing sent, a published assignment, an
instructor's send first) against `fake_gradescope`. `test_gradescope_create.AutomaticVersionTests` adds a settled makeup version to
the linked set once, never before it is settled, says once why it cannot, and leaves a plan never sent alone. Run both with
`test_grading_certification`, `test_gradescope_send` and `test_catalog_migrations` through `manage_checks.environment` on NVMe.

Late papers, review fixes (2026-10-09): `test_certification_automatic` adds a batch line ("Add a line to every grade…") that holds
a late paper until it is applied again (a line the bounds hold, a removed one or another section's holds nothing), a DR student and
a makeup decided after certifying (both live), the cheap test, and the worker's back-off on an exception, its next-turn look after
`changed` and its rest after a look that found nothing. `BubbleLateTests` grades two parts with a second section (Dan, 502): a
missing bubble sheet arriving, a section's line holding only that section, a second late paper after a carried certification and
the return to live, and another student's makeup decided later. Gradescope: the running send seen before the plan is read, the
student's cell and `status.automatic` saying waiting or skipped, `unpublished_version` on a set published but not its version;
`test_gradescope_create.AutomaticVersionTests` waits for a makeup version not added yet and lists a version Faculty could not add.
Node: `test_grading_certification.mjs`, `test_grading_gradescope.mjs`, `test_grading_gradescope_create.mjs`,
`test_gradebook_changes.mjs`; browser `grading_certification`.

Assistant workflow capabilities (2026-10-09): `test_assistant_capabilities` covers immediate requested analysis, real grading and
sitting routes, pinned rubric revisions, bounded record context, reviewed/idempotent material imports, no retry of uncertain
imports, pre-event readiness, phased approval and receipt continuation, stale-source guards and retained Canvas comparisons.
Run with the assistant, workflow, migration and follow-up suites through `manage_checks.environment`. Frontend tests
`test_assistant_capabilities.mjs` cover context and automation markup. Browser checks `assistant_capabilities`,
`assistant_workflows` and `assistant_workspace` cover selected gradebook records, unsaved-work hints, the Automation tab,
review controls, desktop widths and CSP. They use only synthetic owners and fake services.

Assistant integration and opt-in preparation (`ASSISTANT`, 2026-10-08): run
`test_assistant_integration`, `test_assistant_workflows`, `test_assistant`,
`test_assistant_workspace`, `test_assistant_coding`, `test_followup_completion`
and `test_followup_suggestions` through `manage_checks.environment` on NVMe.
They exercise real schedule approval/undo, exact confirmation metadata, complete
bounded JSON/pagination, roster-only lookup, plan identity/recipient expectations,
independent coding checks, Canvas/morning Apply refusal, opt-in transitions,
source changes, revocation, interrupted work and no automatic retries. Readiness
eligibility follows the owning lifecycle for undated work, past sittings and early
papers, refuses new past-assessment preparations and retires queued readiness work
when the assessment is given. Daily reconciliation checks cover 05:00 Central,
both daylight-saving transitions, restart/resume and migration scheduling,
once-per-day reads including failures, waiting for the morning Canvas sync,
and recorded held/uncertain grades. All use
synthetic owners and fakes. `node --test tests/test_assistant_preparations.mjs`
checks escaped control copy. `python3 manage_checks.py browser --browser
assistant_workflows --browser assistant_workspace --keep` verifies explicit
enable, pause/resume, review links, retained typing and CSP at 1440/1280 pixels.
Coding Apply records its own check receipt for the tested head after rebasing;
the model's report does not replace these checks.

Assistant core review fixes (`ASSISTANT`, 2026-10-09): `test_assistant_coding.py` `SelectionTest` checks that grading,
Inbox, scheduling, assistant and static changes select their own tests and families (synthetic folders and the real
`tests/`), and that a large family is capped by priority with the rest named as `not_run`; `CodingTest` refuses a
committed symbolic link at Apply and after a rebase, and reports Apply's in-memory stage only while it runs (since 2026-10-17 there
is no morning window; `test_canvas_writes_refuse_apply_and_show_in_the_restart_check` covers the restart check); `SourceLinkTest` keeps linked files out of
`read_source`/`search_source`. `test_assistant_integration.py` pages past a changed `generated_at`, refuses a changed node
or another pointer's fingerprint, and checks the partial-result notes. `test_followup_learning.py` finds the lesson review's
routes gone (2026-10-12). `node --test tests/test_assistant.mjs` covers the
Applying line and the review-only Prepare remaining setup buttons.

Follow-up tab navigation (`TEACHING-WORKFLOWS`, `FRONTEND`, `PERFORMANCE`, 2026-10-08):
`test_student_support.py` compares appointment response bytes and order with the full projection, including past-term
and enrollment reads, while refusing conversation and triage work on the optimized path. Run it with the other support
checks through `manage_checks.environment`. `python3 manage_checks.py browser --browser followup_navigation --browser
teaching_workflows --browser followup_suggestions --keep` checks immediate tab feedback with held responses, one read per
navigation, late responses after rapid switching or leaving the page, saves during a read, failures/retry, and visible-only
polls. The new browser script is workspace-owned synthetic verification; it creates only isolated enrollment fixtures.

Assistant workflow completion (`ASSISTANT`, `TEACHING-WORKFLOWS`, 2026-10-08):
`test_followup_completion.py` covers prerequisites before mail, failed work kept
visible after resolution, student-only reply matching, policy/record/calendar
freshness, expected own writes, shared student context, explicit typed replies,
one-shot watches and revocation, and repeated edits proposed without adoption.
Its 2026-10-08 review cases cover declined steps, declined staff notes, Canvas-posted
replies, a draft approved the next day, the plan's own makeup sitting, and WHAT CAN
MAKE A STEP WRONG: only a calendar change at a named or booked time, an arrived paper
or an absence decision refuses; policies, notes, grades and other records are
warnings on the review (`context_changes`), as `test_followup_suggestions.mjs` checks.
`tests/followup_plan_eval.py` scores complete plans for missing/unexpected steps,
unnecessary/missing questions, human edits, ordering and incorrect completion;
it accepts scripted or recorded runs and never calls a model itself. Run through
`manage_checks.environment` on NVMe alongside `test_followup_suggestions`,
`test_assistant`, `test_assistant_workspace`, `test_student_support` and
`test_followup_match`. `browser_followup_controls` covers watch preview/save,
pause/removal, prerequisite ordering and term policy editing. All use synthetic
owners and fake outside services.

Assignment consolidation (`ASSIGNMENT-WORKSPACE`) is covered by `test_assignment_workspace.py`:
read-only list/detail projections, legacy route resolution, owner counts, waiting scans,
deleted material, certified late work, unavailable summaries, and retained activity.
Run it through `manage_checks.environment` on NVMe or the full suite. The browser checks
`assignment_workspace`, `set_up_grading`, and `quiz_drafts` cover all six workspace areas (and `node --test
tests/test_assignment_workspace.mjs` the tabs' marks, the activity runs and the delivery words of ONE NAVIGATION),
preserved edits and focus during navigation/refresh, independent printing, resumable
creation/template handoffs, approved generated questions, and interleaved print stacks.
They use synthetic data and fake services. Run:
`python3 manage_checks.py browser --browser assignment_workspace --browser set_up_grading --browser quiz_drafts --keep`.

SAME MATTER (`TEACHING-WORKFLOWS`, `JEV`, 2026-10-07): `test_student_support.py` checks the student's answer in a thread written
from a follow-up (one follow-up, the second tracked row out of the list, Keep separate), the instructor's join and Keep separate
(refusals, idempotent requests, append-only decisions, NEVER policy); `test_followup_match.py` drives Jev's match with scripted
answers (a confident join with Keep separate under Done for you, an unsure one-click question, none, the 14-day window, another
student, a failed call never resent, the match before triage, a decision made meanwhile); `test_followup_suggestions.py` checks
a joined conversation's withdrawn cards and joined source, and TO DO THANKS (no question before the instructor wrote, nothing
owed resolves with Undo, owed stays To do, unsure offers Finished?); `test_jev.py` a thanks leaving a resolved follow-up
resolved. `node --test tests/test_student_support.mjs tests/test_followup_suggestions.mjs` checks Write in the thread, the
join choices and Done for you's Keep separate; `browser_followup_suggestions.py` drives Keep separate, the one-click Join and
Write in the thread.

Teaching follow-through (`TEACHING-WORKFLOWS`) is covered by `test_student_support.py`,
`test_teaching_review.py`, and `test_quiz_blueprints.py`: enrollment ownership,
revision conflicts, exact conversation reopening, neutral calendar events,
assistant approval boundaries, certified evidence with Inbox deletions excluded,
delivery receipts, and pinned blueprint constraints enforced during drafting.
The follow-up start-time reset also checks late historical captures, newer replies,
exact retries, stale previews, retained receipts, and untouched appointments.
Run them through `manage_checks.environment` on NVMe or the full suite. The Node
checks cover student search, filters, calendar grouping, and source-backed draft
requests. `python3 manage_checks.py browser --browser teaching_workflows --keep`
drives follow-ups, office-hours outcomes, student search, teaching review, delivery
comparison, and the blueprint form against synthetic data and fake services.

Run the everyday checks with one command from this checkout:

```sh
python3 manage_checks.py
```

Scan pipeline regressions: `test_bubble_sheets.py` checks question-count evidence
against deleted/restored scans and other terms. `test_pipeline_per_paper.py` follows
recovered aliases through discovery, grouping and assembly, with pending OCR,
uneven papers and instructor undos preserved. `test_sequence.py` checks bounded
inspection turns, progress in other queues, rotation between scans, and an
interrupted chunk requiring explicit retry while keeping retained pages unchanged.
Run these through `manage_checks.py full` or its isolated `environment` on NVMe.
`browser_scan_refresh.py` checks a scan's page when a read fails (Try again, a poll that carries on), that nothing is
read while the Inbox is hidden, and a failed inspection's card; `browser_sequence.py`, `browser_documents.py` and
`browser_alias_ocr.py` open the folded "All N pages" and assembled papers they check. `browser_sequence.py` also checks two
recovery forms typed at once while the page before them leaves "Pages that need a look" (each keeps its page and typing:
dom-patch.js keyed lists), a replaced print job's label listed while unlabeled pages wait for Papers, and a bubble sheet in a
labeled scan left to the Bubble sheets stage. `test_scan_stray_pages.py` covers a page set aside as part of no paper (the stray
sheet's step, eligibility, the lifecycle-then-owner lock order, request IDs, undo, a deleted scan, and blockers, Inbox filing and
certification's inbox_pending clearing) and a held paper grouped by hand (refusals, the stale fingerprint, identities and the
packet, undo and its refusal once the paper is reviewed, a copy already reviewed, a deleted scan, a blank duplex back past the
pages issued, `identify_grouped`, automatic grouping of the rest afterwards, and a later scan of the same copy with the first
scan in and out of the Inbox); `browser_scan_stray_pages.py` drives both dialogs at 1440 and 1280 px, including
the Scan page offering nothing outside the stray-page step and a ticked blank back (requirement, 2026-10-02).

The assistant workspace additions are covered by `test_assistant_workspace.py` (pending-card visibility, saved-context
retention, receipt continuation, questions, batch preflight, watches, artifact ownership and backup checksums), alongside
`test_assistant.py`, `test_assistant_files.py` and `test_assistant_coding.py`. The full suite includes them. Run
`python3 manage_checks.py browser --browser assistant --browser assistant_workspace` for the drawer, restored saved tasks,
inspectable sources, clarification answers, grouped approvals and file downloads. These browser checks use scripted engines
and synthetic data; no model connection or external message is used.
A fixture whose tests start an Assistant turn (a chat, or an absence draft) registers `test_assistant.settle` right after its
folder's own cleanup: a turn's thread still saves and reads the catalog after `busy` turns false, so waiting on `busy` alone
leaves it running when the folder is removed. `test_assistant.Base` does; so do the fixtures of `test_assistant_files`,
`test_absence_drafting`, `test_jev` and `test_followup_suggestions` (2026-10-10). A preparation's thread has done its work when its
owner's `busy()` is 0, which those tests wait for.

Absence email prefill (`ABSENCES` EMAIL-PREFILL) is covered by `test_absence_drafting.py`: fake Claude and Codex binaries run
through the actual assistant engine protocols, verifying the selected model/reasoning, disabled tools, source identity, date
context, validation and idempotent verified documentation. Run these with the `manage_checks.environment` on NVMe alongside
`test_absences` and `test_assistant`. `python3 manage_checks.py browser --browser absence_drafting --browser absences
--browser mail_student --browser assistant` checks the Mail action, editable prefill, explicit save, selected attachments,
blank unknowns and Cancel/late answers. No real mailbox or model is reached.

A term's MEETING HOURS (`TEACHING-WORKFLOWS`) are covered by `test_followup_suggestions.py`
(`test_meeting_hours_are_the_terms_own_offered_as_open_times_and_bound_a_meeting_made_by_itself`: validation, exact request IDs,
stale saves, the unchanged plain read, the open times a preparation lists, the bound on a meeting made by itself) and
`browser_followup_controls.py` (the Term policies window's Meeting hours: add, room carried to a new time, save).

A section's class schedule (2026-10-10, SCHEDULING "CLASS SCHEDULE"; the instructor: "For each section, I want to add a class
schedule to list due dates, given on dates, etc to allow the agent to reference when needed and other automations"):
`test_scheduling_class_schedule.py` covers the read (class days as the Calendar shows them before and after the term uses its
schedule, a day off, a day run on another day's schedule, the default and asked windows; a quiz, a regular exam with a room a
section and a final with a day a section, agreeing with the per-student `given_dates`; due dates from the gradebook honoring a
column's sections and from Faculty's Canvas copy for the section's course, an assignment only the copy holds), one case for
every disagreement rule marked and not marked (the column carrying the latest section's date, a due date apart on purpose,
certified work), the plan (rows with a section's own values, identities kept, request IDs, stale and identical saves,
everything it refuses, topics by date with a section's own and a notes outline), a planned row following its record (the
likely match, one record a row, the planned value only where the record has none, a final's own days against one Given on,
the seating chart winning, unlink), planned rows reaching no other read (the Calendar, `/api/state`, the gradebook, the Term
schedule and the exam record byte for byte), the course-edit protection, and the Assistant (the tool's default window read
whole, both writes as Approve cards made by the instructor's click). `test_quiz_drafts` covers an approval naming its planned
row; `test_followup_suggestions` the schedule's lines in a preparation and its own staleness key. `node --test
tests/test_class_schedule.mjs` covers topics as typed, weeks, a row's shared and own values, the scope line, and the pre-fill's
match and date. `manage_checks.py browser --browser class_schedule` opens the page from the section tab: today, the fold, a day
off, a quiz and due dates linking to their owners, a planned date given each section its own room, topics typed with Enter,
the orange mark on the one section it concerns with its links, the tabs between sections, 1280px and the CSP. Synthetic
catalogs only. The rehearsal behind the rules is not a check: a verified snapshot of the catalog on NVMe
(`/mnt/nvme/Teaching/tmp/class-schedule/rehearsal.py`, 2026-10-10; its figures are in SCHEDULING "CLASS SCHEDULE: DISAGREEMENTS").

SCHEDULING's one clock is covered by `test_scheduling_clock.py` (every reading in every mode, the old helper names answering the
same, the campus zone named only in scheduling/clock.py and calendar-feed.js) and `node tests/test_clock_cases.mjs` (calendar-feed.js reads the same fixture, `tests/fixtures/scheduling/clock_cases.json`).
Its one engine and feed are covered by `test_scheduling_engine.py` (every kind, the SHARING table with the room rule and DR exams
and due dates free, busy time over several days, open starts for a quiz's whole length, free stretches, a proposed time's check, and
the feed's order and Google failure, typed sitting and chart times and ranges, and the calendar's timed range). The Term schedule is covered by `test_scheduling_service.py` (revisions, request IDs, stale saves, stable entry
identities, everything a schedule refuses, the import from per-date events and meeting hours, the course-edit protection) and
`browser_term_schedule.py` (the course Home's Schedule row, Import, Seats a makeup, Save, edits keeping identities, days off, Use
with its dry run, the Calendar's Term schedule rows, Stop using, 1280px, CSP); `test_scheduling_service.py` also covers Use and Undo
(the dry run, the fingerprint, what the Calendar and Google show, availability from the schedule, nothing deleted), days off, and
meeting hours moving to the schedule (the legacy save refused, the window's read) with the appointment check. `test_canvas_office_hours.py` covers
the Canvas Office Hours draft (heading and pictures kept, the hours listed, a draft of a draft unchanged) and "Put in Faculty's copy"
(confirmation, stale revisions, the copy changed and FakeCanvas untouched). GOOGLE CHECKED is covered by `test_google_calendar.py`
(off, never read, read, 15 minutes later, the permission gone, not knowing), `test_followup_suggestions.py` (the meeting facts and
card note say so, an automatic appointment becomes a card), `test_makeup_booking.py` (a first card waits, says why and goes out on the
next turn) and the `.mjs` checks of the card's line and the waiting row; ALL-DAY ENTRIES by the same files (an all-day note takes no
time, marks no day, holds no card) and `node tests/test_followup_suggestions.mjs` (the card's warning lines, escaped).

Quiz makeups booked by card (`ABSENCES` MAKEUP BOOKING) are covered by `test_makeup_slots.py` (the times a card offers on
synthetic calendars: the next two days that have an open time, never before the quiz, today while a time is 3 hours away, a new
card's two days after the last card's, office hours only in the term's makeup rooms, only other makeups sharing a meeting-hours start,
the student's whole quiz length clear, a makeup's typed start, busy, too-soon, absent and past-due times left out), `test_makeup_length.py` (20 minutes times the largest granted
extra time, a shared sitting's longest student) and
`test_makeup_booking.py` (fake card service and mail relay, a fixed campus clock in 2036: the switch and its dry run, one card per
makeup with the student's own times, a pick seating the student once and shared sittings, another time flagged and a new card,
withdrawal when no longer owed, no-answer and no-paper flags on Home without names, a named copy as soon as a student books (days
before the sitting, a later booker's own job) and copies that wait for an approved makeup version, the automatic
makeup draft once). `node tests/test_absence_makeups.mjs` checks the Makeups view's switch, version line and booking states (a booked row's copy line, its link to the print job and Print PDF for a ready copy only), and the makeup draft
window's Makeups pieces; `node tests/test_latex_text.mjs` checks how a question's printed layout (bold, answer blanks, grids, columns) is read for display; `node tests/test_review_notes.mjs` checks the READABLE REVIEW both the makeup window and the quiz draft page use
(notes and warnings sorted by problem, a finding on several versions said once, first sentences, the printed crop of each problem,
folded prose with its labels and lists).

Student notes (`STUDENT-NOTES`) are covered by `test_student_notes.py` (enrollment owner, revisions, delete and restore, the
picker without UINs, the assistant as actor, the export left out) and `python3 manage_checks.py browser --browser student_notes`
(the roster window and gradebook record in place, Undo and the palette's student picker).

Set up grading now and the 'Given on' date (requirement, 2026-10-02) are covered by `test_exam_overview.py` (the plan before
any paper on Grading, the hub and the Assignments card; the route's two forms, idempotence with nothing imported for an
existing plan, the versions printed in the term imported and any failure named, and the exam record it makes),
`test_assignment_finder.py` (an unlabeled scan of such an assignment found by its imported blank template, the plan reused),
`test_quiz_drafts.py` (the approval's date as the main sitting, and an earlier date kept and recorded as kept), the Node
checks `test_assignment_wizard.mjs` and `test_quiz_drafts_ui.mjs` (the set-up notice; the approved page's date line) and
`python3 manage_checks.py browser --browser set_up_grading --browser quiz_drafts`: the wizard (a cleared date taken off its
sitting, a recorded date naming where it is changed), the Exams hub tile and the card's ⋯ menu, and both dates typed in a
browser on the campus clock late in the evening, kept as typed. Since 2026-10-10 `browser_set_up_grading` enters through
Create assignment's starting menu with seating left out (the grading handoff on leaving Details) and gives Quiz 12 the day
after the campus clock's today, never a fixed date: Home's Coming up lists nothing before the server's `today`.
`browser_grading_gradescope_publish` expects the header's Resolve N checks while its fixture's grading is unfinished.

The five `test_grading_fixes_*.py` files hold the regressions of the 2026-09-22 grading review: quotations
found as written, answer values, the closed grammar and crossed-out final answers (`_evidence`); blank crops,
instructor-only flags, flag-reviewer bars, spare labels and the narrow policy (`_decisions`); the dispatcher's
wake event, per-task retries, durable polls and the absent spending cap
(`_orchestration`); manual grades, review states, full-credit rules, exact rubric search, permissions,
benchmark statistics and stable splits (`_scoring`); unknown costs, gateway timeouts, schema refusals and
key/host binding (`_providers`). `tests/test_grading_evidence.mjs` checks the waived-checks card.
`python3 manage_checks.py browser --browser grading_preview` checks stale batch
settings, automatic preview refresh without submission, preserved choices, explicit
resubmission with a fresh request ID, and recovery from a failed preview refresh.
It also checks the compact dialog at desktop widths, visible
actions while scrolling, cost limits and unknown prices, point-only rescoring,
invalid sample limits, and blocked-response disclosures using synthetic previews.

`test_grading_standard.py` covers the replacement Cloud Standard rule: unchanged
main graders, disagreement-only arbitration, adopted/revised/unreliable outcomes,
arbitrator flags and failures bypassing the fourth model, fourth-model uphold/manual
review decisions, immutable standing ticks, invalid replies, required selectors,
frozen settings, costs and historical pipeline refusal. The older cloud fixtures
explicitly freeze the pre-replacement workflow for compatibility coverage.
`browser_grading_standard` checks fourth-model finalization and its retained flag reasons.
`browser_grading_settings` and `browser_grading_teams` check the fourth selector,
its saved draft, the removed controls, themes and responsive layout.

Jev Routing, One-shot, Tag-Team, High Assurance and the Jev tie-breaker were removed on
2026-09-23 with their tests; `test_grading_removed_schemes.py` checks that their batches
stay readable and cannot be started, resumed or retried.

`test_jev.py` and `test_jev.mjs` cover the JEV decision adapter: pinned host/model,
validated distributions, no redirects/repeated uncertain calls, durable exclusive
receipts, revision-bound FYI and request routing, preserved cutoffs/manual states,
absence source conflicts, campus-relative dates, Assistant evidence omissions and
contradictions, and escaped advisory UI. All responses are scripted fakes.
`python3 manage_checks.py browser --browser jev --keep` checks FYI filtering,
request shortcuts and Assistant evidence review in the desktop layout.
`test_jev_routing.py`, `test_palette_route.mjs` and `browser_palette_route.py` cover
Ctrl K routing (numbers kept in code, both-order agreement, the sentence never stored,
students never sent, Best match and Ask rows). `test_jev_canvas_match.py` covers the
Canvas import's Jev matches (agreed, confident, equal points; Import never asks; the
assistant's preview never asks). `test_jev_send_checks.py` covers urgency/wellbeing
(sorting, never FYI, Mark reviewed, Home counts only) and the send checks (dates,
weekdays, times, rooms, students named, unanswered sentences, attachments, Jev off). `test_jev_bank_tags.py` covers
Jev-first bank tagging (section then topic, a single topic decided in code, `placed` kept and only the objective from the
model, an objective outside the placement asked again, doubts and Jev off/failing tagged by the model as before).
JEV DATES (2026-10-08): `test_jev_dates.py` covers the days and times read in code (written dates, words for days, bare,
"last" and "next" weekdays, quoted replies, campus send day, year rollover both ways, both readings of a time without am/pm) and the
match and `timing` rules; `test_jev.py` the absence check (no date put to Jev, code's reading kept when Jev fails, a bare
weekday's `timing`); `test_followup_suggestions.py` the record check (a day not named, a weekday Jev places, "next" never
chosen, a meeting's day and time written in the conversation, nothing dated in Jev's request). Its two tests of a meeting made
by itself (this one and MEETING HOURS') hold the campus day at their message's, Oct 3, 2026 (`today_is`): a month and day with
no year is read against the send day, so on the real clock the day the student names would stop matching in June 2027.
JEV DATES and ABSENCE CHECK v3 (2026-10-09): `test_jev_dates.py` also covers "this <day>" already past in the sent week
(Wednesday, Saturday, Sunday) as a bare weekday and on its own day as definite, "next Monday" written on a Monday, capitalized
weekday abbreviations and the prose they must not match, spans of days (same month, into the next, and the forms that stay
single dates) and clock ranges across noon; `test_jev_send_checks.py` that SEND-CHECKS and JEV DATES agree on "this <day>";
`test_jev.py` one `timing` question per weekday (Monday past and Wednesday later both kept) and a bare weekday blanked with
both candidates when Jev is off, failed or uncertain; `test_absence_drafting.py` the drafted "Wednesday" blanked with Jev off.
OPENAI DECISIONS (2026-10-08): `test_openai_decisions.py` covers the shared client (Jev questions translated, answers in Jev's
shape with Jev's confidence, a wrong model, refusal, unknown option or out-of-bounds answer failing, one request to the fixed
host with no redirect, failures failed or uncertain without echoing the host, the text limit, the key and off switch);
`test_jev.py` triage on it (receipts with its model, its own gates, SAME MATTER on Jev, the status field, unavailable keeps the
attention rule). The gates themselves come from `python3 manage_decisions_sandbox.py triage-calibrate --run NAME` on a
read-only catalog copy (`decisions_sandbox/`, never imported by the app; it sends real messages to both providers).
ASSISTANT MEMORY (2026-10-10, requirement: "Automatic, with Undo"): `test_assistant_memory.py` covers a lasting chat message
kept as typed with its two Jev receipts; a passing message, quoted text and "nothing lasting" leaving nothing; a statement
naming a student refused (a UIN or whole name in code, a single name left to Jev with the matched word, a passing question
skipped); a date, room or grade not kept; a fact kept for the page's course or term and not guessed with two current terms;
a newer statement replacing and Undo restoring in one step; an exception beside its rule and a restatement changing nothing;
a fragment marked; nothing kept or replaced when Jev does not answer, also between the two requests; the gates and the kind
label; the instructor's own write, move, remove, restore and erase; revisions only added and one owner; the earlier
preferences carried once; the old routes and tool; what a request is told (preferences always, a term's facts only there,
replaced ones never), the cap with its search, receipts and outage fallback; the note joining the chat and Undo from it;
continuations never considered; and no tool reaching a memory route. `test_assistant_memory.mjs` covers the note's wording,
escaping, Undo once and the list's groups; `browser_assistant_memory.py` the drawer end to end (remember, replace, refuse a
student, Undo, the Saved tab's list, reword, move, remove, erase). Jev's answers are scripted; no provider is reached.
A follow-up's memories (2026-10-11, ASSISTANT-MEMORY "A FOLLOW-UP ASKS WITH THE STUDENT'S LAST MESSAGE"; the instructor: "use
the student's last message"): `test_assistant_memory` `GivenTests` covers `student_said` giving the student's latest message
alone (not her earlier one, not the instructor's reply), cut short with her name and UIN taken out, and nothing for no
message, another student or no conversation; a preparation reading it through its own follow-up while a chat has no such
request; the embedder asked with that message and never with the preparation's instruction; the relevance request ("allow jev
for follow ups") holding exactly the task sentence and that message, with no name, UIN, earlier message, private note or
instruction in it; the rule Jev finds useful kept and marked `chosen` although Jev reads the student's message as going
against it, where the same answers set it aside for a request the instructor typed; the nearest five marked `nearest` when
Jev does not answer; and the five most recent, with nothing asked, when there is no message or it cannot be read. A stand-in
embedder and a scripted Jev. No follow-up was prepared by a real model with it, the `use` gate was never measured on a
student's message, and live's course and term memories (96 characters) are far under the 3,000 at which any of this runs.
EMBEDDING (2026-10-10, requirement: "I want full embedding model"): `test_faculty_embedding.py` covers the worker's
protocol on a socket of its own with a stand-in encoder (the nearest text first, a kept text embedded once, the bounded cache,
the limits, a refusal or unreadable request answered and the worker still serving); the client (no worker, a slow worker
and an incomplete order all `EmbeddingUnavailable`; Faculty asking only when `FACULTY_EMBEDDING_SOCKET` names the socket);
the jail's argv (no network; nothing of the home folder or the checkout but this package, read-only, wherever the checkout
is; never either of them whole, the live catalog, the saved credentials or a folder holding one; no key variable; only the
socket's folder writable, beside the GPU's device nodes; no GPU device for a CPU worker); the model lock (no code or pickle,
a differing model refused); nothing importing a model at import time; and the
Assistant's memory taking the worker's order, falling back to keywords with a warning, and attached by the app only when the
service names it. No model is loaded, no GPU is touched and the live worker is never reached.
EMBEDDING "LENDS THE GPU" (2026-10-11, requirement: "build the hand-off"): `test_faculty_embedding.py` `LendingTests`, with
a stand-in card that records its moves, covers a lent worker refusing text, vectors and pictures (a refused picture never
marked as a bad one) while `status` says so, a second loan only moving the end, the card taken back once; a loan nobody
returns ending on the next request; a card still full watched until there is room, then left alone until a later request
looks again; a CPU worker and a loan out of bounds lending nothing; `Gpu` with a stand-in model and CUDA (no move tried
without room for the model, a failed move leaving the model whole in main memory); and `gpu_lent` asking no worker when no
socket is named, returning the card when the request raises, and never failing its caller. `test_frq_providers.py` covers
the order around a local model's request (which model is installed, lend, the request, take back), the same after a failed
request and for a local server that is not Ollama, the loan's length, nothing asked when no worker is named, and a cloud host
borrowing nothing. A request that waits ("I want follow-ups to wait on lent cards", 2026-10-11) is covered there too: held
while the card is lent with `status` still answered, answered as the card comes back, refused as a lent card when its wait
runs out, ending a loan nobody returned and waiting on through a card still full, costing nothing when nothing is lent, and
refused out of bounds; `test_assistant_memory` covers a follow-up's lookup asking to wait `LENT_WAIT` seconds and a chat's
asking for none. A lent worker's refusal is marked (`CardLent`), and `test_picture_search.py` covers picture indexing
waiting on it turn after turn with nothing counted against the page and nothing set aside, although the worker answers a
status request throughout. The real card is not a check: on 2026-10-11, with the live worker stopped and a worker of its own on a
socket of its own (the pinned model, the live code), a generated Letter page at 300 DPI went through GLM-OCR and
`LocalVlmProvider.locate`; with the worker grown to 2,484 MiB the request failed without the loan (HTTP 500, `cudaMalloc
failed: out of memory`) and passed with it in 15 s (qwen 8,382 MiB, the card 8,860 of 10,820 MiB, the worker 474 MiB while
lent; lend 0.7 to 1.1 s, take back 0.6 s), and text and picture vectors before and after were identical.
The installed-model smokes (`grading_local_smoke.py`, `grading_template_smoke.py`) name no worker and so borrow nothing: beside
the live worker their local model can run out of GPU memory, and the worker is stopped for them.
MEMORY SANDBOX (2026-10-10, requirement: "Sandbox it first"): `test_memory_sandbox.py` covers the invented case set (every
class at its minimum size, a family wholly fit or held out, a statement before what restates or changes it, a contradicting
case refused); the roster check's certain and single-name matches and the rule that leaves a single name to the model; the
decisions (lasting before everything, the kind label never a gate, an unsettled scope kept narrow, an exception never
replacing); the pipeline on scripted answers reaching exactly the expected store, in one request per step and in one request
per kept memory, and with each judgment sent to its own model; no quoted mail, scan or tool text in any request; another
term's fact never visible; both outages (nothing kept or replaced without the decision host, keywords in the embedder's
place); the sweep proposing gates with no forbidden outcome; reports holding ids and numbers only; the vector cache; run
folders on `/mnt`; `app.py` never importing the package and the parent never importing a model; a child's jail (nothing of the
home folder or the checkout but this package's `children` scripts, read-only, wherever the checkout is; never either of them
whole, the live catalog, a key file or a folder holding one; no key variable; only its run folder writable; no network unless
asked); weights refused when they do not match `model-lock.json`; and a live
run being explicit, counted and never mixed with scripted answers. No request leaves the machine and no model is loaded:
`memory_sandbox/` is never imported by the app, and its live commands (`manage_memory_sandbox.py ... --live --budget-calls N`)
send invented text only.
GRADING MULTIPLE-CHOICE READER (2026-10-08): `test_grading_choice.py` covers the rubric shapes read by choice, the automatic rule
from the rubric and printed options (written work and its undo, a confirmation of the first version for its own rubric), the
letter-to-line mapping in both rubric modes, the confident-read gate, the risk policy's named waivers, and end to end with a scripted
reader through the grading seam: a confident read with no saved key that finalizes as the grade (right and wrong letters), every
kind of unsure read graded as written work, the setting off, written work never read and frozen once graded, a saved key that
differs from the rubric holding a read back, and the cloud lane.

The runner uses the existing `.venv`, local Node/TypeScript, browser libraries,
and optional extracted TeX toolchain. It installs nothing and uses synthetic
catalogs and payloads. It removes inherited `FACULTY_*`, `NEBIUS_*`, `MATHPIX_*` configuration, mail
credentials and every cloud credential from child processes (aggregator, inference-host and
GPU-provider prefixes such as `OPENROUTER_*`, `MODAL_*`, `RUNPOD_*`, and anything ending in
`_API_KEY`, `_TOKEN` or `_SECRET`, because an endpoint names its own key variable), sets an
isolated fallback `DATA_DIR`, and never opens the deployed storage configuration. Explicit test fakes still exercise
email and external-service behavior without sending messages.

Temporary databases, payloads, caches, logs, screenshots, and print PDFs go below
`/mnt/nvme/Teaching/tmp/checks/`. Each run has a unique directory. Successful runs
are removed unless `--keep` is given; failed runs remain for inspection. Set
`FACULTY_CHECKS_SCRATCH` or `--scratch-root` to another absolute directory on a
`/mnt` drive. Root-disk scratch is refused. The check directory is disposable
runtime scratch, never a retained owner or part of nightly backups.

| Command | Coverage |
| --- | --- |
| `python3 manage_checks.py quick` | Python/JavaScript syntax, available frontend type checking, Node tests, core API/identity tests, schema migrations, and restoration exercise |
| `python3 manage_checks.py full` | Syntax/frontend checks and every Python unit/integration test, including restoration |
| `python3 manage_checks.py browser --keep` | Installed-browser preflight, syntax/frontend checks, Home, course pages, storage, and the one-appearance design browser check (`browser_theme`) |
| `python3 manage_checks.py browser --browser assignment_wizard --browser seating_versions --keep` | Selected browser workflows |
| `python3 manage_checks.py browser --browser calendar --browser calendar_resilience --keep` | Calendar events, scopes, desktop layouts, local times, and request recovery |
| `python3 manage_checks.py browser --browser assignment_templates --browser exams --browser assignment_wizard --keep` | Template uploads, page/area mapping, grading crops, and the assignment wizard |
| `python3 manage_checks.py browser --browser cross_links --browser badges --keep` | Every cross-link between one assessment's pages (Grading, Exams hub, seating chart, gradebook, Accommodations DR schedule, Home, Calendar, Response Cards, course documents, Settings rows), and that the Inbox badge equals Current's needs-you count while a deleted scan counts nowhere |
| `python3 manage_checks.py browser --browser exam_day --browser seating_print --browser seating_roster --keep` | The exam-day packet (AGENTS.md "Exam-day packet"): the Exams hub's link, proctors from the TA lists and typed in, instructions with Reset and Undo, a save and a stale save, the mailto-only Email proctors, `#seating/<chart>?packet=1` opened once, the re-checked preview, a PDF with the portrait cover, instructions and room roster before the seat chart in its own orientation and cleanup; with the chart and room roster print checks it shares `seating-print.js` with. `test_exam_day.py` (full) covers revisions, request IDs, stale saves, proctor checks, TA options, Start from, extras from current print jobs and the packet's fingerprint |
| `python3 manage_checks.py browser --browser aim_import --keep` | Import from AIM against a synthetic stand-in of TAMU's sign-in and AIM: NetID and password in Faculty's form (a wrong password comes back with the page's words), a timed-out Duo push sent again, the code shown in the window, "Is this your device?" and "Stay signed in?" answered no, the FERPA policy page continued, both exports in one review with Import both; an unrecognized step opening the live page and clicked through, a Duo passcode, the NetID remembered, Cancel discarding the browser, and a saved sign-in going straight to Duo with no form. `test_aim_import.py` (full) covers step classification, NetID forms, credential and Duo-answer checks that never repeat a password, form reading, the postback, every refused export, input checks and the routes; `test_aim_capture.py` (full) covers Capture AIM's form: what `form_structure` keeps and the student-data filter, `FORM_SCAN` in headless Chromium on a local `set_content` page reading no values, a capture session that never exports, Record into the Inbox, the captures list and the routes' guards (never a real AIM page) |
| `python3 manage_checks.py browser --all-browsers --keep` | Every `tests/browser_*.py` script |
| `python3 manage_checks.py browser --browser grading_teams --browser grading_settings --keep` | Conditional graders, automatic team finalization, retained assessment history, settings and responsive layouts |
| `python3 manage_checks.py restore` | Synthetic nightly-backup restoration and corruption rejection only |

**Shell, pages and cross-links (UX overhaul, 2026-09-26).** `quick` runs every `tests/test_*.mjs` (the
node glob) and the strict frontend typecheck, which now covers `page-header.js`, `palette.js`, `print-files.js`,
`calendar-feed.js` and `seating-state.js`. The node files hold the pure page logic: `test_ui.mjs` (the ui.js
helpers, toast specs, the shortcut registry including Alt+letter by `event.code` and grouped rows, the header
resolver, `attentionBadges` counting an Inbox card only when `derived`, palette storage that throws), `test_home.mjs`
(Needs you, grading rows, Coming up, the hero count, a health line that never calls an incomplete backup verified),
`test_calendar_feed.mjs`, `test_scan_flow.mjs`, `test_grading_overview.mjs`, `test_grading_run.mjs`,
`test_gradebook_grid.mjs`, `test_gradebook_changes.mjs` (the Changes page's labels and lines), `test_courses.mjs`, `test_accommodations_page.mjs` (in three time zones),
`test_seating_state.mjs`, `test_print_files.mjs`, `test_assignment_wizard.mjs` and `test_response_cards_ui.mjs` (the
answers CSV never holds a UIN field or an expected value). `test_web_assets.py` (quick) covers the archive of earlier
builds: an old build's lazy module resolves after a restart, HTML, non-public files, traversal, unknown builds and
tampered blobs are refused, eight builds are kept, and `/tmp` or `tmp/<web|worker>` is refused.
`test_workspace_overview.py` (quick) holds the Inbox card to Current's filing (deleted scans count nowhere, fresh
scans are Running), bounded overview calls, card mail per card and the grading counts equal to the workflow's; one card
per failure (no queue card beside a plan's failed-to-grade card or a scan's Inbox row), superseded failures counted
nowhere (drafts and answer-key readings each against their own kind), a plan's own card for its rubric-check failures,
finished rubric checks (agreeing with the Grading page's state through an answer-key save, a resolution that failed, a
line comment and a rubric edit) and answer areas waiting for a look,
certified finished scans counted as archived, one failed print job linked to itself, and a response unchanged by the
queue rules when nothing failed.
`test_exam_overview.py` (full) covers the exam hub's projection and `test_print_origins.py` the registration-job
prefix. Browser: `browser_navigation` (the twelve pages in order, the hero only on Home, the compact header and
trail, badges and '(N)', the sticky footer, every entry fitting 1440 x 900 and the page's own entry above the footer on
shorter windows, the highlighted entry (All courses is Courses) revealed from a rail scrolled away, the settings hub,
scroll and focus), `browser_home` (also the hero's Create assignment and Draft a
quiz, and In progress holding the place it will take while Grading is read: none without a current plan, and Coming up
unmoved when a plan's line arrives), `browser_smoke` (Home's "Needs you",
the course list, the settings guide), `browser_palette` (Ctrl K, recent places, entity jump, actions, the '?' sheet and
the 'Faculty was updated' callout when a lazy import fails), `browser_badges` (the Inbox badge and '(N)' equal
Current's needs-you count; a deleted scan leaves every badge and Home count until restored) and `browser_cross_links`
(every link between one assessment's pages, and each Settings row opening its control). `browser_theme` is the DESIGN-LANGUAGE check:
one appearance (also on a dark OS, flat), the white header band and its one-line Manrope 800 title beside the black rail,
framed buttons with the hard shadow, a framed dialog, the slate focus ring, seats, print and forced colours at 1440 and 1280
px; `browser_shell_routes` holds the shell (the black rail, no arc, the footer in view, the band's edges, rule and height) on
all 40 routes and 12 dialogs the design session reviewed, at 1440 × 900 and 1440 × 1100 (`tests/shell_checks.py` is
shared by both), and on each route (folds open) and dialog fails any unframed clickable drawn in small capitals
(`check_sentence_case`, DESIGN-LANGUAGE §1.12); `test_design_language.py` is the static half (removed tokens, theme
switches, phone widths, Ledger's tokens, no paper grain, `ledger.css` last, no paintings, fonts).

FRQ grading's `test_frq_grading.py` and `test_frq_providers.py` are included in full.
They cover permanent scopes, immutable revisions, deterministic scoring,
follow-through, point-only rescoring of human decisions, provider contracts,
a cloud batch that starts without any crop-approval pause, malformed output receipts, interrupted jobs, retries, source
changes during inference, queued rubric compilation/source reading, managed
backup, export and benchmark metrics. Synthetic image preparation also exercises
the actual Bubblewrap sandbox. No account keys or student data are used.
Run `python3 manage_checks.py browser --browser grading --keep` for navigation,
rubric approval, local batches, a Cloud Standard batch against the local fake host (estimate
before submission and no canary, progress, recorded cost, the optional look at what is sent),
endpoint probes and tier settings, evidence, corrections, score calculation, approval and exports
at desktop widths.

`test_grading_geometry.py` checks discarded optional model rectangles, retained
audit markers, unchanged literal evidence, and strict crop/quotation/required-geometry
validation. `test_grading_reading_context.py` also checks that a malformed rectangle
does not retry the reader and that its original reply remains in the step receipt.

`test_pipeline_improvements.py` covers dropped writing, sign/decimal/choice changes, per-crop and text/LaTeX preservation,
the bounded page-context round, failed context held for review, fair automatic worker turns, stable score-band sampling,
and image-before-grade audit requests. `test_pipeline_per_paper.py` also checks recovery after a transient assembly failure
without a page edit. `browser_grading_quality_audit.py` checks sample coverage, escaped readings and partial scan checks;
`browser_grading_settings.py` checks the opt-in context control. These are synthetic correctness checks, not model-accuracy measurements.

`test_grading_cloud.py` runs the hosted-endpoint layer against `tests/fake_openai.py`, a local fake
OpenAI-compatible host (chat completions with usage and an upstream name, the models list, the
OpenAI-style batch API and the job API of a dedicated GPU provider), over real HTTP on loopback:
several named endpoints of different lineages; images and schema-constrained output, and the
prompt contract plus backend validation where a host has none; a rate limit with `Retry-After`, a
server error and a rejected request as confirmed failures retried only after being recorded; the
attempt cap; no credits said plainly and never retried; a dropped connection and a read timeout
as unknown outcomes resent at most twice; a refused connection as confirmed; a batch job recovered
by its ID after a restart; cancellation of durable jobs; a superseded request's late result kept as
a receipt and never applied; parallel work within the endpoint's requests in flight; usage and cost
per request; the estimate (information only, no spending cap); no canary before a batch (removed 2026-09-28) while saved
canary settings still load; the capability probe;
key variables that can never leak another secret; the application with every provider down; and
the dedicated GPU provider behind the same interface. `test_grading_exam_versions.py` covers a
paper's version from its seat, confirmation against the blank exams (in the actual document
sandbox), mismatch/unresolved papers that are never graded or switched, identically named rubric
files per version, and groups and totals that never mix versions. `test_grading_symbolic.py`
covers the closed grammar (hostile text is never evaluated), equivalent forms in the answer-key
matcher without any tolerance, the goal's inconsistent response (`18/3` is not `8`; `3(8) - 8` is not
`10`), an arithmetic slip located where it happens, and ordinary algebra that is not a contradiction.
`test_grading_risk.py` covers every escalation signal, acceptance blocks, one vote per assessment
(one per lineage for batches frozen before `family-blind-v1`),
objections that block but do not route until measured, and errors a proposal already accounts for.
`test_grading_pipeline.py` sends synthetic responses through the real job runner against the fake
host with deliberately FOOLED graders: fully correct, inconsistent (flagged although both graders
proposed full credit), an arithmetic slip, a correct answer with unsupported work, a hard-to-read
symbol (readers disagree), a blank response and a crossed-out answer; grader disagreement (nothing
escalates since High Assurance was removed; `considered` records what once would), single-response regrading, and
the instructor rule that no match, agreement or symbolic result selects a rubric line or approves.
`test_grading_acceptance.py` covers trial acceptance without any report (never final, never exported,
no instructor decision written), what the policy refuses to accept, a stale request for the removed shadow mode, confirm and
overturn with the corrected lines and the signals that should have caught it, the blind share (hidden
until graded; a reveal counts as seen; the gate uses it), the gate refusing without a report or with
too little evidence, automatic acceptance that is scoped, audited, exported as `automatic` and gone
when its inputs change, a failed audit or a whole-assessment suspension suspending it, trial groups, and the
instructor's burden. `test_grading_benchmark.py` covers the bound (`0/30` allows 9.5%, `0/300` about
1%), reviews needed, intervals, paired comparisons, the error breakdown, the error rate among accepted
responses, one vote per assessment and student-level splits; `test_grading_evaluate.py` runs the whole
measurement loop (dataset export with provenance, `run-endpoint` against the fake host, comparison
with the baseline, fine-tune export). `browser_grading_cloud.py` covers the Exam versions panel,
Independent checks, trial-accepted review, the gate refusing in the browser, groups and settings at
desktop widths. `test_manage_checks.py` proves
that no live provider credential reaches a test process.
`test_grading_model_catalog.py` verifies read-only, on-demand model discovery,
metadata units/capabilities, base-family handling, bounded concurrent requests,
cache expiry, stale fallback, failure backoff, and structured provider routing.
`python3 manage_checks.py browser --browser grading_model_catalog --browser grading_settings --keep`
checks search, role filters, draft-only assignment, reuse of existing overrides,
versioned save, selection with shared or unknown families, offline/late replies and both responsive
themes, separate Browse buttons, recent-choice persistence/discard, and offline
selection of saved connections. `test_grading_recent_models.mjs` checks recency,
deduplication, the five-choice limit, preserving current selections and access to
custom connections. Catalogue tests use synthetic metadata and never contact OpenRouter.
Real provider smoke checks use explicit synthetic material separately; they are
not an assertion of recognition/grading accuracy on classroom scans.
`tests/grading_local_smoke.py --with-glm --with-compiler` checks the installed local
OCR, verifier and grader on a synthetic incorrect solution, with backend score
validation, and interprets a follow-through rubric without approving it.
Run it using `manage_checks.environment` and an NVMe scratch directory. It sends
no student data and never contacts Mathpix or Nebius.

`test_grading_locations.py` covers automatic discovery in either upload order,
independent printed-total matching, mandatory location confirmation, manual failure
resolution, shared page receipts, interrupted reads, explicit retries and stale
inputs in both storage modes. It also covers multi-page exam templates for single-page
stamps-only versions, incomplete student-source rejection, isolated question
warnings and missing local verifier configuration, one template choice covering
only questions without response areas, and shared page-read progress. It also
covers full-width areas ending at the next printed problem and side-by-side columns.
It follows a first-page label from scan to grader: positional identities, the
Inbox review's assembly, an eligible paper, and refusal when the paper and the blank
exam differ in pages. `test_sequence.py` covers those identities on stamps-only and
first-page print jobs, reuse after undo, blank backs, an exam that does not add up,
a copy assembled by hand, and the database bound on never-printed identities.
`test_template_conversion.py` refuses a DOCX reflowed away from Word's own page count.
`test_grading_answers.py` covers answer normalization, rubric roles, proposed keys and
every contradiction reason; `test_grading_answer_groups.py` runs two students through
the real job runner (same correct answer, with and without work) and group approval;
`browser_grading_groups.py` covers the key dialog and the contact sheet in a browser.
`test_grading_risk.py` covers the instructor's confidence-checks switch across the risk policy, the review state, OCR low confidence
and reading resolution (numbers flag nothing, named doubts still do, values still recorded); `test_grading_acceptance.py` checks that
off is part of the acceptance scope while on changes no scope, and that a stale request for the removed shadow mode and settings saved
with it are handled; `browser_grading_reasoning.py` also covers the switch keeping its threshold values through a save.
`test_grading_reasoning.py` and `test_grading_reasoning.mjs` hold the server's and the settings screen's reasoning rules to one
table of cases (`tests/fixtures/grading/reasoning_cases.json`), and cover fact validation, shipped facts for connections saved
earlier, per-role choices that fall back instead of refusing settings, frozen batches behaving as before, every wire form, the
estimate and the catalogue's `reasoning` block; `test_grading_cloud.py` sends a role's choice through a real batch and checks the
retained step and acceptance scope; `browser_grading_reasoning.py` covers the dropdown, the fixed statements, the explicit read
for an older connection without any fetch to draw the screen, saving per role, and the reset on another model.
`test_grading_batch_resets.py` runs batches through the real job runner and resets them:
unreviewed proposals withdrawn, an approved response and every retained record untouched,
the server-side confirmation, stale preview and idempotent request, reset responses as new
work counted once in the progress total, a rerun with other proposals, a restore that never
replaces newer work, and a refusal while the batch is still working;
`browser_grading_batch_reset.py` walks the Grade step's dialog, notice, rerun and restore.
`test_grading_review_queue.py` covers the Review navigation's read-only projection
(groups by the exact proposed lines, the workflow's own count, approvals emptying a
group while saved corrections stay, proposals hidden for a blind review never sorted by their
lines, changed work set apart, exam versions never sharing a group, opt-in reason categories and byte-compatible default responses);
`browser_grading_review_queue.py` covers direct entry (the Review step opens on a response; with nothing waiting, its next
step), continuous same-question review, explicit version boundaries, saved places, reason filters, rubric shortcuts, drafts,
read-only Later, completed disclosures, the unsaved-work guard, a concern clarified beside the desk and only its responses
graded again, the proposal gallery (no key approves, the tick clears, set-aside responses next) and desktop widths.
`test_grading_proposal_gallery.py` covers the gallery's eligibility, one review per approved response and the refusal of an
approval when anything changed; `test_grading_rubric_quality.py`'s `ClarifyInReview` covers one question clarified as a new
confirmation that keeps every other question's entry.
Both also cover batch-scoped review: pending regrades never borrow older flags,
superseded results stay out, approvals reduce the batch count, and scope survives
the batch button, question/group navigation, reloads and approval refreshes.
`browser_grading_layout.py` checks the Grade and Review layouts with sixteen
synthetic questions and multiple batches at desktop widths, including batch history and disclosure/focus preservation while polling, a step's content-only loading
and a keyboard Refresh at the page's end that keeps the step, its scroll and focus on Refresh.
`test_gradescope_rubrics.py` keeps descriptive wording out of conditional-rule
review while wording about other items or caps still requires it, and
`test_grading_review_rules.py` covers supported OCR resolution, unresolved/novel
readings, inactive versus ambiguous conditions, instructor-authored structured
rules, no-inference flag refresh, and current review measurements counted once.
`test_grading_acceptance.py` also excludes a blind acceptance revealed first from independent
gate evidence. `browser_grading_review_rules.py` exercises the evidence display,
unapproved rubric revision and correction report at desktop widths.
The existing
`tests/test_grading_guide.mjs` covers the setup guide's ordering and next step. `browser_grading_locations.py`
checks template selection before OCR, overlays, drawing and manual-review controls
under the app's CSP, at desktop widths. `browser_grading_area_confirm.py`
confirms a flagged question in its dialog (its required tick and refusal in the footer, in view), checks it is left out of
**Confirm N answer areas**, that the next question needing its area opens, and that the button saves the matching rest;
`test_grading_locations.py` holds confirming together to all-or-nothing on stale or flagged checks.
`tests/grading_template_smoke.py` separately exercises the installed local OCR and
layout model on a synthetic blank exam, checking matched and mismatched totals
without approving any location. Use isolated NVMe scratch and the worker execution
lock when running installed-model checks beside deployed services.

`test_grading_uploads.py` starts with an empty grading plan and covers automatic
question creation from all eight Gradescope export shapes, retained originals,
atomic publication/recovery, repeat uploads, version selection, review gates and
same-origin requests in both storage modes. Run `python3 manage_checks.py browser
--browser grading_uploads --browser grading_locations --browser grading --keep`
for multi-file upload, shared template OCR, explicit location confirmation,
partial failures and existing grading at desktop widths. `tests/grading_cloud_smoke.py` is the explicit LIVE smoke test (cents,
real scans from a copy of the preview, a forced confirmed failure and a forced unknown outcome in
front of the real endpoint); it refuses any data directory outside `/mnt/nvme/Teaching/tmp` and is
never part of these offline checks.

`test_gradescope_send.py` covers Sending grades to Gradescope offline against `tests/fake_gradescope.py`, a
loopback server (127.0.0.1, port 0) that serves the pages and answers the requests `grading/gradescope_web.py`
uses, in the shapes captured from the real site: sign-in form and session cookie, `/account` course boxes by term
(with a "Math 140 for Instructors" course that is never requested), membership rows, AssignmentsTable,
AssignmentOutline, AssignmentRubric (hierarchical group positions), SubmissionsManager with its upload form,
the submission viewer, a question's list of submissions with who last graded each (`question_submissions`),
SubmissionGrader and its JSON save (404 when not asked for JSON, yet saved). Faults
are scripted per request: a connection dropped before or after the write, an HTTP status with or without the
write, an expired session, question submissions made only after some reads, published exams, papers and grades
someone else put there. A real graded plan (two students on Versions A/B, Gradescope-export rubrics with
groups; also two sections in two courses, an automatic Cloud final, and course storage) is built once and
copied per test. The tests cover proposals, UIN matching, rubric mapping and score equality, the fingerprint
and request IDs, read-back of every write, uncertain outcomes settled only by reading, explicit Retry, cancel,
the one-send rule, the routes, that the export counts the same final grades, and replacing grades someone else made
there only with the instructor's choice, its confirmed count and exactly what the preview showed. No test reads a real sign-in
or reaches gradescope.com.

The browser suite requires already installed Playwright/Chromium and their system
libraries. The runner finds `.tools/browser-libs/usr/lib/x86_64-linux-gnu` and the
existing browser cache automatically. Missing browser prerequisites fail the
browser suite with a diagnostic. Missing Node/TypeScript skips those optional
checks with an explicit message; a compiler that is present and reports errors
fails the run. Full native document checks require the documented Bubblewrap and
TeX runtime; no unisolated processing fallback is enabled.

The restore exercise creates disposable synthetic HDD/NVMe directories, imports
a sample roster, retains a review-gated Inbox original, and takes the ordinary
coordinated nightly backup. It checks the catalog manifest and SQLite integrity,
uses SQLite's backup API to seed an empty replacement NVMe catalog, and asks the
existing storage managers to verify and restore their payloads under the normal
locks. It compares permanent roster identities and payload bytes, preserves the
Inbox review gate, and runs a new backup on the replacement. Separate cases damage
the synthetic catalog and payload backups and require rejection before restoring.
This exercises the recovery mechanics; it is not a command to restore production.

`test_storage.py` (full) covers the nightly backup's two steps: an unchanged working
copy keeps its verified snapshot, and that night's catalog backup records it. A changed
copy gets a new snapshot beside the old one. A damaged recorded snapshot is reported,
kept as it is and never reused. Data written between the unlocked preparation and the
locked capture is copied under the lock, and the prepared snapshot is never recorded.
HDD work runs with the lifecycle lock free while the capture holds it. A copy of a
changing source is discarded, and a copy never writes through an existing name. A
failed `syncfs` records no snapshot, and per-file fsync is the fallback.
`test_inbox.py` checks that an unchanged Inbox message keeps its snapshot through
its final purge. The retention tests check the catalog-backup rule (days, then weeks,
then months). They check that pruning every night ends where pruning once would. They
check that a snapshot a kept catalog backup records survives, and that only the
fingerprint of the plan that was shown deletes, never during a backup or after an
incomplete one. They check that an owner whose recorded snapshot is damaged is skipped,
and that an unreadable kept catalog backup stops the plan.

## Versioned schema changes

`catalog_migrations.py` provides a workspace-owned `schema_migrations` ledger in
the shared catalog (and therefore its existing nightly catalog backup). Rows record
the module namespace, version, SQL SHA-256, time, and whether a pre-ledger schema
was adopted. This metadata owns no teaching resources.

Document identity's numbered SQL files and Response Cards' bootstrap schema now
use the ledger. Their existing additive column/backfill compatibility checks are
retained. Other modules retain their existing initialization until individually
migrated; this does not pretend that their earlier upgrades have been audited.

Call the runner under the module's existing roster lifecycle lock:

```python
with rosters.lock(), connect(path) as db, migration_transaction(db):
    apply_sql(db, "module_name", "002_add_feature", sql)
```

`migration_transaction` acquires `BEGIN IMMEDIATE`. Schema changes, compatibility
repairs, and ledger records commit together or roll back together. SQL statements
are parsed using SQLite's statement-completeness parser, which handles trigger
bodies and quoted semicolons without `executescript`'s implicit commit. Migration
files cannot contain their own transaction control or connection pragmas. Starting
the helper inside an already open transaction is refused rather than committing
someone else's work.

On the first upgrade of an existing module, its current idempotent bootstrap SQL
is reconciled and recorded as **adopted**. There is no invented historical checksum
for untracked versions. Later starts skip recorded SQL after checking its checksum;
changed recorded files stop that module's startup with a diagnostic. Keep applied
SQL immutable and append a new version for future changes. Never reset the ledger
or rerun destructive SQL to bypass a checksum mismatch. The existing ownership,
retention, and locking rules continue to apply to every migration.

`test_grading_reading_context.py` covers retained layout links and alternative
readings, invalid references, OCR-versus-visual context, text-only compatibility,
vision second graders receiving crops without primary opinions, escalation to an
image-capable second grader, and reading concerns that block acceptance despite
matching scores. Synthetic contracts/transport checks do not establish accuracy
on real handwritten layouts. `browser_grading_reading_context.py` checks the
layout disclosure and image-aware second-grader evidence at desktop widths.

`test_grading_rubric_quality.py` exercises the mandatory rubric finalization with real
catalog/storage and a loopback fake model: one request per question (two are held in flight at once by a
barrier, no request carries another question, and a retry sends only the missing one), the printed problem
shown to a reviewer that reads images and the check going on without it, the unconditional batch gate, built-in exclusions
decided by rule (including the lines that must NOT match), complete version coverage, four-field
multipart uploads, selected reviewer and its reference material, strict item references,
non-transitive pairs, edited clarifications, one immutable confirmation that approves the rubrics,
stale rubric rejection, what the graders read versus the unchanged rubric, scores and exports,
individual review, human overrides, cancellation, retries and recovery without a second model
charge. Fixtures that grade call `frq_fixtures.finalize_rubrics` (a scripted reviewer that proposes
nothing; the built-in rules are never scripted). Cloud, acceptance, workflow and restore regressions
cover the shared paths. `browser_grading_rubric_quality.py` checks per-version upload
slots, the six steps, the required check, built-in rules shown and locked, an edited clarification,
reciprocal pair edits, one confirmation approving the rubrics, the original wording kept, model
settings, and visible confirmation controls at desktop widths. Run through
`manage_checks.py` or its isolated `environment` on NVMe; never use real models.
These tests verify the workflow, not the accuracy of a model's proposed exclusions.

`browser_grading_setup.py` checks assignment-card entry without a duplicate picker,
question search/filter/previous/next navigation, remembered selection on refresh,
an individual answer-key save that changes no other question, highlighted blank
exam areas, and all three setup layouts at desktop widths.
`browser_grading_setup_steps.py` (2026-10-02) checks that a finalized rubric's Save changes and Save all ask before reopening it
(closing sends nothing), that a changed line on an approved rubric already out of the finalization asks without the
finalization's claims or the Correct wording link, that Save changes waits for a change, that approved import notes fold, that a
key being read is watched without redrawing the step or wiping a typed search (and redraws once it finishes), that an unchanged
Answer areas poll keeps a card's nodes while a moved suggested area moves its highlight, that a first answer area can be drawn
where none was suggested, and that a plan without its PDF offers only the link to Assignments & printing (no upload, draft or
manual question); it saves screenshots at 1440 and 1280 px.
`browser_grading_rubric_quality.py` also switches questions with an unsaved
clarification and verifies the draft survives and every question is confirmed.
Linked versions (GRADING "LINKED VERSIONS", 2026-10-02): `test_grading_rubric_links.py` covers how links are put together
(seeds from rubrics drafted together, the reviewer replacing them, the instructor's decisions winning, "none" never undone by
another path, conflicts, what a check is told), one finding per problem kept as one row per version with one `shared` key
(only the versions it names, version notes, older findings still read, unknown versions refused), consistency lines,
reviewer links that cannot be used dropped rather than failing the check, and a resolution's `not_applicable`, on the
instructor's matrix example. `test_grading_rubric_quality.py` (`QualityAcrossVersions`) runs a check whose reviewer answers in
the v4 shape, saves link changes through the service, proves they change no fingerprint, standing or confirmation, checks the
migration 041 triggers, that the next check is told the instructor's links, and that a shared decision is resolved with the
links as they stand. Its `QualityOnlyWhatChanged` checks that a check asks only what does not stand (2026-10-05): a version
added after A and B were confirmed is asked beside them (`rubric_quality_added`, A and B settled with their confirmed guidance,
an echoed settled version ignored, findings on any version kept), confirming keeps A and B and suspends nothing, `settled` stays
out of the fingerprint, a plan with nothing changed or nothing standing is a full check, a plan without versions asks only its new
question, and a cancelled re-run leaves the finished check of the same rubrics (also on Home's `setup_waiting`) but not after the
rubrics change. `browser_grading_rubric_quality.py` ends with the added version's **Check Version C**, the window's "not asked
again" marks and the confirmation. `test_grading_rubric_concerns.mjs` and `test_grading_rubric_links.mjs` check grouping by `shared`,
deciding a version separately, copying only along "same" links and the values warning. `browser_grading_rubric_quality.py`
checks one decision for both versions with every version's lines at once, the one-panel problem, copying to the linked
version, mirrored exclusions both ways, and the linked line offered unticked by Correct wording. Links from the draft:
`test_grading_rubric_links.py` reads the writers' marks (`family_rubric`, `line_kinds`, `drafted_kinds` through edits) and starts
seeds from them; `test_grading_rubric_versions.py` and `test_quiz_drafts.py` check the marks reach each created rubric's
provenance and the plan's links; `browser_grading_rubric_drafts.py` shows and changes a line's mark in the all-versions review.
`test_question_bank.py` (`RetainedFromDraftedQuizzes`) also rebuilds an approved quiz, approves the rebuilt rubrics and checks that
the bank keeps one copy per problem and version from the approved LaTeX, with the rebuilt question's term and certified grade on it.
`browser_grading_navigation.py` checks one scoped assignment request per step,
on-demand batch history, fresh status after leaving and returning, keyboard focus kept on a followed page-bar link, and a
batch started elsewhere appearing on the open Grade step after the shell's overview read, with an opened Earlier batches,
focus on its summary and the scroll kept. `browser_grading_loading.py` checks the 10 s plan reuse between one assignment's
steps, that a write or Refresh reads again and that a plan read overtaken by a write is not reused;
`browser_grading_index_poll.py` the index's 60 s/15 s checks, kept focus and the "Couldn't refresh" notice of a failed check,
which wraps without sideways scrolling; `browser_grading_versions.py`
that recording a paper's version redraws the step, with the dialog's tick and refusal in its footer. Intake
regressions keep source reuse within a locked read and revalidate later requests.
Template checks cover bounded display previews, unchanged full-resolution pages,
private no-store responses, and source verification even when a preview is cached.

Model families are optional audit metadata (requirement, 2026-09-23). The catalog,
gateway, settings and fallback tests retain capability checks while permitting
same-family and unknown-family selections. Decision, team and risk regressions
cover same-family consensus, arbitration, flag review and assessment vote counts in
batches frozen with `family_rule: family-blind-v1` (every new batch), and that a batch
frozen before it keeps its family rules. `risk-rules-v15` changes the acceptance scope
without rewriting existing grades or receipts.

`test_grading_rubric_drafts.py` covers positive and negative generation, signed
point validation, mode-bound request IDs, retained pre-choice generations, explicit
acceptance, source provenance and ordinary scoring. It uses synthetic templates
and a local fake model; it verifies the ambiguity-check instruction is sent, not
that a real model always produces unambiguous rubrics. `browser_grading_rubric_drafts`
checks the generation menu, both scoring choices, edited deductions, added lines,
acceptance and desktop layouts.

The term gradebook (`gradebook/README.md`) has five test files. `test_gradebook_compute.py` covers
setup validation (server IDs, a new category chosen by its `ref`, refused invented IDs, archived rather than
removed scored columns, limits, one normal form per setup, links, the scale) and the exact computation (points
and weighted modes, renormalized weights, excused/missing/empty cells, extra credit, points drops checked
against every possible choice, ties, sections, letters with rounding and the floor, statistics, the weighted columns
without a category named in `uncounted`, a large term).
`test_gradebook_imports.py` covers Gradescope course and assignment exports and generic sheets (CSV and XLSX,
leading zeros, Points Possible rows, encodings), values and rounding, exact UIN matching with unmatched rows
listed, masked unreadable values, suggested and explicit mappings, change actions and the import fingerprint,
and that the roster parser is unchanged. `test_gradebook.py` runs the service through the API: idempotent
setup and score revisions, stale cells, content types, the CSV export as the only place with UINs, linking,
previewed and fingerprinted pulls and imports, offering protection, the migration ledger, the calendar deadline,
a section's own cells on request (`cells=section`, default bodies unchanged) and roster names through managed storage. These three are in `quick`. `test_gradebook_sources.py` (full suite)
reads real graded workspaces: FRQ, bubble sheet and Exams-page totals by each source's own rule, Exams-page
marks and DR bookings as reasons, the pure pull preview, a linked column's default points from the plan's own
maximum, and a catalog left byte for byte unchanged by every read.
`test_webassign_nightly.py` (full suite) covers the morning run's WebAssign import (a term no sync has written to) against
`fake_canvas.py` (`add_assignment(tool=…)`): the tool host deciding (Gradescope's tool is not WebAssign's), nothing before 5 am or
after 8 am campus time and one run a morning, only WebAssign's assignments read in (a new column in the assignment group's
category, marked WebAssign's), Canvas's missing flag not recorded, Canvas always winning (a typed score refused, a blank in Canvas
emptying the cell), an assignment without points waiting while the rest imports and the page no longer naming it once the
instructor's sync places it, a signed-out morning recorded, not retried within the hour and cleared by a newer sign-in, no request
on a morning with nothing new, a term that is not current left alone, the 10 pm import's last run shown until a morning runs,
Leave out (`canvas/skip`: recorded once, refused for another course, the next morning neither waiting nor imported, a column chosen
in Sync with Canvas bringing it back), due dates following Canvas for WebAssign's assignments and Canvas quizzes (the gradebook and
the copy), and Sync with Canvas never sending to a WebAssign column.
`test_canvas_nightly.py` (full suite) covers THE MORNING SYNC (canvas.md NIGHTLY SYNC) for a term a sync has written to: from 5 am,
once a morning, writing only to the courses written to before (a course whose first write is the instructor's left alone), its
request, snapshot and send (never shown interrupted) and the grid's `canvas.nightly`, no request on a morning with nothing to do,
held grades left held, a value whose last send is uncertain and which Canvas still does not show left for the instructor's own
Sync (counted `unsure`), an unpublished assignment published as the summary ticks it, a chosen post carried out, an uncertain post
never asked again by the morning, a signed-out morning retried hourly, a stopping Faculty starting none, Jev never asked, and
CONFIRMED BY A LATER READ (`ConfirmedLaterTests`, above).
`test_posting_policy.py` (full suite; pure) covers `gradebook/posting_policy.py`: the order (choice, always by hand, iClicker, the
category's due-date and certification rules, nothing), due today not past due, switching only by-hand columns Faculty sends, the
steps from a read (a student who left not counted, posted stays posted when grades are sent, a hide choice), what waits and how
the instructor's picks settle it, ticked courses only, a fingerprint that ignores counts, and `pending` from Faculty's last read.
`test_canvas_sync.py` (full suite) covers Sync with Canvas against `fake_canvas.py`: Start from Canvas (matches, new columns
limited to the sections whose courses have them, Canvas's grades taken into cells Faculty never filled, the grade as entered
before Canvas's late penalty, a grade both hold differently held, no UIN in the summary, a replay), a grade changed in Faculty sent
without a tick, a grade changed in Canvas held until Keep Faculty's or Take Canvas's, an excuse taken away with `"excuse": false`,
a grade emptied in Faculty taken out of Canvas (and one Faculty never knew never taken out), Canvas ignoring a clear, Canvas's own
form of a grade, a waiting submission, what cannot take grades, the summary checked again (409, nothing written), ticked sections
alone written, a column kept off Canvas, a restart refusing a sync, receipts when Canvas refuses or the read-back is cut off, the
courses sent at once with each course's assignments in order, a signed-out course stopping the others before their next
assignment, Canvas's throttle waited out once by the send alone (refused twice: failed), one
assignment never taking two columns, a missing assignment created once it has grades and never doubled, WebAssign's columns
mirroring Canvas and a browser save neither giving nor taking their mark, a Canvas quiz mirrored and read-only, a grade Faculty
never had filled from Canvas (one the instructor emptied held), the summary reading Canvas once and Sync reading again only what
it writes, and the pure rule (`agreed`, `choose`, `grade_data`).
`test_canvas_posting.py` (full suite) covers POSTING GRADES against `fake_canvas.py`'s GraphQL `postAssignmentGrades` /
`hideAssignmentGrades` / `setAssignmentPostPolicy`: a choice asking nothing of Canvas and shown pending with its reason, back to
the rule, request IDs and a stale page (409), the confirmation, a column kept off Canvas, the assistant's choice on an Approve
card; the next sync posting a chosen column with an uncertain-then-accepted receipt per course and the grid's mark, then hiding
it; posted staying posted while a held grade holds the post in its course; a post refused where its grades did not reach Canvas;
the category's due-date rule (due today not yet; the Changes page's words); a by-hand column's automatic assignment switched before
its grades (receipts) and never in a category with a rule; a refused switch sending nothing there; a choice after the summary
(409), the old ticks and routes refused; Canvas refusing (failed; the instructor's next sync asks again); an unfinished post whose
read-back is cut off (uncertain); a signed-out session; a restart waiting for the posts after a sync; ZERO ONCE DUE's POSTED; and a
student who left the Canvas course never counted as hidden.
`browser_canvas_posting.py` drives it in a browser: the heading's "N hidden" chip (a student who left not counted), the column
menu's Post choice putting "posts at sync" on the heading with nothing asked of Canvas, Sync's "Posting in Canvas" fold and warning,
a first write to one section's course then the next sync to every course, the chips gone, the menu offering the way back to the
category's rule, and Setup's "Posting in Canvas" cell (Automatically with both ticks; by hand when the category weighs 0).
`test_canvas_sync.py` `PublishTests` covers PUBLISH FROM SYNC: an unpublished assignment offered and, unticked, left alone and not
left in step; ticked, published first (uncertain-then-accepted receipt) and its grade sent, never twice for one request; Canvas
refusing (failed, nothing sent, offered again); Faculty's Canvas copy published too and the push finding it in step; one cell's
send still naming "not published"; the assistant refused. `browser_canvas_publish.py` drives the tick in a browser: ticked by
default, unticked named under Can't sync, then Sync publishing and sending.
`test_iclicker.py` `AfterClassTests` covers iClicker after class on a fixed clock with a Term schedule: nothing before ten minutes
after the class, today's session in and its non-answers recorded 0 (an earlier class's never), the day done, a section without an
iClicker course recorded once, an excused absence credited when the class ends, a running session looked at again, an empty
session making no column, no class day / day off / schedule not in use, iClicker failing tried again, and the follow-up facts. Its due dates (`DueDateTests`): a column taking Canvas's date and the latest
of its courses, a column made without one taking one, a date typed in Faculty kept, Gradescope's tool and other columns left
alone; Faculty's Canvas copy following one course's date (a section date) then every course's (the default again), the push then
asking nothing and the instructor's later edit an update for both courses, and a date changed in the copy but not pushed kept (the
push still asks). The tests count nights from a fixed date, never the real clock, where later nights follow.
`test_gradebook_grid.mjs` checks the grid's notice (`nightlyNotice`), `test_gradebook_changes.mjs` its Changes labels (the due
dates' too).
`python3 manage_checks.py browser --browser gradebook --keep` runs `tests/browser_gradebook.py` for the grid,
the setup page and the dialogs in a browser, with synthetic students only (among them: the uncounted-column notice naming only
the section's own columns, the grid measured again as the course page above it loads, the setup's term-wide score counts from
a section tab, and Find a column keeping a row categorized under Uncategorized in view).

**Certification and Export** (the sixth grading stage, 2026-09-26; GRADING.md "Certification and Export").
`test_grading_certification.py` (full suite, about 13 s) holds migration 029, the pure rules (`certification_rules`:
classification of every participant, statistics, flags, checks and digests) and the service and API on the graded
Gradescope workspace: the preview's gradebook plan equal to the certify's with zeros and a column to create, no payload
read inside the write transaction, a fingerprint that stays still while training capture runs, an automatic grade approved
unchanged reopening nothing, a column linked to some parts, a left-out student's earlier certified cell cleared, the
Gradescope "do not send" decisions and their prefill, idempotent replays, certify never writing `exam_participants`, a new
column's proposed and chosen category in a weighted gradebook (in no fingerprint or digest), the column checks without the
repeated options list (same digests and fingerprint as when they carried it), the one same-titled column as the default (two
of them, or archived and linked ones, leave a new column first; certifying records into it and keeps a typed value), the
absences beside the students about to receive 0 (an empty list when none, other rows untouched, nothing written, the
fingerprint and digests unchanged, `covers` from the main sitting, the section's seating chart or the student's own sitting,
never from a makeup's or DR exam's chart, and never on a withdrawn absence), and the Inbox's first pages waiting counted once.
`tests/test_grading_certification.mjs` (quick, node) holds the page's pure logic (stage completion, the status band,
tables, filters, the row folds counting their rows, the Certify dialog's acknowledgements, its column select (the same-named
column as the default, with its existing-column line, typed values or none), category and duplicate-name warning, the
warning for any column, existing or new, that would not count toward Overall, the absences beside the zeros (a withdrawn one
never marked),
the certified and multiple-choice-only views, the audit fold) and `python3 manage_checks.py browser --browser
grading_certification --keep` drives the page end to end on synthetic students: statistics, a question, a student, the
Gradescope fold reading nothing until opened and following a send that settles, the downloads enabled by a live read once a
grade is final, the column select defaulting to the same-named column (warned as not counting: its category has weight 0),
then a new column's category with the same-named column's warning, Check again keeping the column and category chosen, a
zero student's absences (one covering the exam's date marked) at 1440 and 1280 px, a
grade changed in the drawer, certifying with confirmed zeros, the gradebook's certified values, and "Changed since
certification" after a later change.
No check covers `?tab=export&open=gradescope` yet: the page opens the fold for `open: "gradescope"`, but the grading frame
does not pass `open` to the export tab until its own change lands.

## Parallel browser runs and design checks (`tests/tools/`)

Automatic follow-up preparation (`ASSISTANT-FOLLOWUP-SUGGESTIONS`, `TEACHING-WORKFLOWS`): `test_followup_suggestions.py` in the full
suite covers current/new/manual sources, cutoff and snooze suppression, revisions, default-body compatibility, exclusive claims,
interactive priority/busy, restart recovery, proposal-only enforcement, student/attachment ownership, grade/absence/control
refusals, stale previews and batch preflight, clarifications, explicit retries and fake Gmail accepted/uncertain receipts,
and Canvas push cards titled from their preview (one change, several across sections, a held-back object, a preview that failed).
`test_followup_suggestions.mjs` checks shared Send previews, escaping, superseded controls and uncertainty. Run
`python3 manage_checks.py browser --browser followup_suggestions --browser assistant --browser assistant_workspace --browser teaching_workflows --keep`
for both review locations, Approve/Send/Decline, revisions, clarification answers, preserved typing/focus/disclosures on quiet
refresh and refresh failure, desktop layouts and CSP. All engines and outside services are fakes on isolated NVMe scratch.
Rollout applies migration `student_support/004_suggestions` through the existing lifecycle-locked service migration mechanism;
restart web and Inbox only after Assistant answers (including background suggestions), Inbox jobs and grading/model work are idle.
The follow-up review fixes and controls (2026-10-03) add to `test_followup_suggestions.py`: an own reply captured by Gmail ID or
by text keeps the other cards (and the answered conversation listed as Replied), someone else's reply still withdraws them, an
own tracking change keeps them while a manual edit withdraws them, a failed start or lost claim never blocks the queue, a
withdrawn card may be proposed again but a declined one may not, instructions and decline notes reach later preparations,
Approve all stops after an unconfirmed send, Edit in Mail links only a matching send, pause/Prepare now/mute and the dispatch
order, the calendar beside a meeting card, legacy (v1) runs keep their comparison, and every control is a NEVER route.
Analysis first: a thank-you is never prepared before or after Jev files it under FYI, a new request waits for its analysis,
Jev's silence ends the wait after its limit, and Prepare now does not wait.
Wide scope: another student's record, an excuse decision, a gradebook setup change, a main sitting and a code change are
approval cards (none written), cards beyond the student say so, Canvas section messages and never routes stay refused, and
mail reaches only the student, their TAs and the instructor.
Term policies: a save is idempotent by request ID, refuses an edit of an older revision and over-long text, reaches the
preparation's prompt, marks older suggestions as prepared under changed policies, and a Retry applies the new text;
`browser_followup_controls.py` writes them in the Term policies window and reads them back.
Per-area policies (`student_support/007_policy_areas`): the same tests cover legacy preservation, byte-identical default
reads, separate modes/notes, mode-only revisions, idempotency/stale edits, total limits, malformed input, term ownership,
immutable storage and the prompt projection (editor questions are excluded). The browser switches areas and terms without
losing drafts, saves both terms, reopens the settings and checks 1440/1280 px layouts. Roll out through the same idle gate
and lifecycle-locked migration path as 004; existing policy revisions are not rewritten.
`test_followup_suggestions.mjs` covers the shared section's markup; `browser_followup_controls.py` drives chips, the calendar
note, Edit in Mail, the Suggestions ready filter, Send and approve all, pause and mute at desktop widths. Migration
`student_support/005_suggestion_controls` rolls out the same way as 004.
Follow-up simplification (2026-10-03): `test_followup_suggestions.mjs` also checks stable conversation selection through
tracking, exact student-message identity (including merged Canvas notices), folded explanations and deduplicated sources
with the evidence warning raised only by a confident contradiction (EVIDENCE WARNING, 2026-10-05; other verdicts stay in
Why). `browser_followup_controls.py` checks one selected review, switching between unrelated
conversations without losing unsent revision text, the original message and 1440/1280 px layouts. Existing Send, batch,
decline, revision, clarification, quiet-refresh, policies, pause and mute checks continue through their grouped controls.
Retry availability: `test_followup_suggestions.mjs` covers preparations with no policy revision/outdated-policy flag and
excludes queued, running and superseded states. `browser_followup_suggestions.py` retries a legacy preparation after a
policy save, verifies the latest guidance reaches the new prompt and that replacement proposals still require approval.
`test_followup_suggestions.py` also checks that a plain retry needs no revision notes, distinguishes superseded proposals
from actual instructor declines (including legacy event wording), uses latest policies and preserves completed receipts.
FOLLOW-UP CLARITY: `test_followup_suggestions.py` checks each waiting card's `effect` (absence, appointment for another
student, follow-up revision with old values, an excusing absence revision, an unnamed route) and that revising this student's
own follow-up or absence carries no wider-scope note; `test_followup_suggestions.mjs` covers the plan, step cards, scope and
certified notes, mail step headings and question-first order; `browser_followup_suggestions.py` checks them on the page.
FINISHED STEPS: `test_followup_suggestions.mjs` checks decided cards fold into one counted line only when nothing waits, that
failed and unconfirmed cards stay out of it, and that the evidence warning leaves with the last waiting card;
`browser_followup_suggestions.py` checks the fold after the last card is decided.
WAITING ON: `test_student_support.py` checks To do needs attention, the one-line next step (kept only when given, multi-line and
over-long refused, an unchanged save records nothing), `answered` only in the `suggestions=1` read and gone after a newer student
message; `test_followup_suggestions.py` checks a reply that promises something leaves To do with its next step once sent (not a
card), To do is never prepared automatically, and a status change that rewrites details stays a card; `test_student_support.mjs`
checks `waitingOn`, `waitingMarkup`, `answeredMessage` and the To do filter.
EDIT IN PLACE: `test_followup_suggestions.py` saves an edit (idempotent request ID, stale `seen`, non-mail and unchanged text
refused), checks the edited card stands in the draft's place, nothing is sent until Send, and Send sends the edited text;
`browser_followup_controls.py` opens the editor, checks Approve all waits, reaches Edit in Mail from it and saves an edit.
ONE ROUND: `test_followup_suggestions.py` checks an approved round settles with no further preparation, Waiting on student
proposed beside the reply (refused before it, submitted only after the send, Approve all mail-first), the own-reply flow in one
round, and a new makeup sitting seating the student (stale version and existing-sitting refusals).
ASSUME, DON'T ASK / REVIEW FLOW: `test_followup_suggestions.py` checks a card keeps a bounded `assumption`, the prompt's question
rule and the absences listed in the facts; `test_followup_suggestions.mjs` checks the "Assumed" markup, `reviewOrder` and
`nextReady`.
READABLE REVIEW: `test_followup_suggestions.mjs` checks the bold plan count, the evidence callout and its button, the guess
placed above the step's buttons with framed choices (closing full stop dropped) and Done for you under waiting steps / first
when quiet; `browser_followup_suggestions.py` checks every visible text action and arrow link in the review computes to
sentence case while the section labels stay in capitals.
DONE FOR YOU: `test_followup_suggestions.py` checks each bound and its card fallback (decision, dates, other student, overlap,
per-preparation cap, other conversation's file, meeting length/window/day), Done for you rows, Undo as the instructor (idempotent,
refused once changed), and that an undone kind becomes a card; `test_followup_suggestions.mjs` checks the Done for you markup.
`SuggestionsTests` turns `automatic` off unless a test is about it. SCHEDULED STATUS: the status runs after the reply's confirmed send and
never withdraws its run, Undo puts the previous revision back (also its own), Don't cancels it, and a round with no reply records
it at once; the mjs checks cover the scheduled row and the Active rule. JEV: the Done for you test covers Jev off, Jev doubting the
dates and Jev confirming (fake `jev.client.evaluate`); THANKS CLOSES: a question reply does not close, an acknowledgment closes
once, Undo reopens and blocks it from closing again. `test_followup_deadline_closes.py` (DEADLINE CLOSES): a follow-up Waiting on
student closes once, with Undo, when its conversation's absence is Not excused automatically; set waiting after the decision, a
newer student message, another of its absences still undecided, or an absence another conversation recorded leaves it waiting.

`python3 tests/tools/browser_suite.py --jobs 6 --retry-failed [name ...]` runs `tests/browser_*.py` in parallel, each in its
own scratch directory with `manage_checks.environment` (the same offline, credential-free setup as
`manage_checks.py browser`); all 76 take about six minutes, and `--retry-failed` tells timing races from real failures.
`browser_inbox.py`'s poll cadence step runs on a clock stopped before the page loads (`frozen_page`: `clock.install` then
`pause_at` on a browser context with no page yet) and waits for the page to finish each list read, not a fixed time; pausing a
loaded page's clock "50 ms from now" failed on a busy machine with "Cannot fast-forward to the past" (2026-10-11).
For frontend work, `PYTHON=.venv/bin/python tests/tools/serve_port.sh PORT` serves a seeded synthetic workspace from the
checkout's current `static/` (re-run it after CSS edits); `tests/tools/app_shots.py` screenshots its routes (one appearance, `--width`);
`tests/tools/style_diff.py --port PORT --base <git-ref> [--files a.css,b.css] [--media print]` loads each route twice (the
sheets swapped to their content at the ref through request interception, then as served) and lists every computed-style
difference per element, so a restyle can show exactly what it changed on screen and that print did not change.
Set `FACULTY_DEMO_SCRATCH` and a short `TMPDIR` on a /mnt drive (Chromium crashes with long TMPDIR paths).
Playwright MCP (the project's `.mcp.json`, instructor's choice 2026-10-04: "demo copy only") drives that demo server on
port 8765 and nothing else: start it first with `serve_port.sh 8765`. The server uses the Chromium already in
`~/.cache/ms-playwright` with the bundled browser libraries, an in-memory profile, a 1440 × 1000 viewport, screenshots in
`/mnt/nvme/Teaching/tmp/playwright-mcp` and its npm cache on /mnt. It allows only `127.0.0.1:8765` and blocks live
(`:3000`, the Tailscale address); that is a guard against a slip, not a security boundary, so never point it at live,
where a click can send real mail or publish grades. `PRODUCT.md` and `DESIGN.md` at the root give design tools
(Impeccable reads both) the product and Ledger; they summarise AGENTS.md and DESIGN-LANGUAGE, which win.

JEV PAPERS (2026-10-15): `test_jev_papers` covers Jev 1.14 picture checks (the images header, `assets`, box validation, small
pictures padded, a text check unchanged), the roster, name and UIN rounds (a second roster's first-name pick settled, one round
deciding when the other holds, a contradiction or no candidate deciding nothing, two students sharing a name) and receipts that
keep enrollment IDs and picture hashes, never names, UINs or pictures. `test_unlabeled_exams` covers first pages without the
printed Name and UIN found by Jev, the basis `jev` registered, a Jev/OCR disagreement held with its note, Jev's reason on an
unsettled page, and no Jev (the OCR reading stands). `test_catalog_migrations` applies document_identity 014 on a pre-ledger
catalog. Run them through `manage_checks.environment` on NVMe; no provider is reached.
JEV BLANK ANSWERS (2026-10-15): `test_jev_papers` (`BlankAnswers`) covers which held zeros are shown to Jev and the .9 cutoff;
`test_grading_direct_read` covers a blank answer Jev sees blank finalized on the basis `jev_blank`, one below the cutoff left
waiting, and an ineligible one never shown. The integration check uses the real eligibility and image check: the automatic
empty-transcript warning must not prevent Jev from seeing an otherwise eligible blank zero. Actual reading doubts and grades
with credit remain held and are never shown through this path (2026-10-26). Run through `manage_checks.environment` on isolated
NVMe scratch with a fake Jev; no live provider is reached.
JEV MIXED SCANS and stamped copies (2026-10-15): `test_unlabeled_exams` (`test_jev_sorts_a_mixed_scan_by_assignment`) covers papers
registered under the assignment Jev reads them as and a page read as none held; `test_jev_papers` (`WhichAssignment`) the both-orders
rule; `test_term_print_jobs` a stamped copy printed for no student named from Jev's reading when the OCR read no name.
DEPARTMENT MAKEUP EXAMS (2026-10-22, ABSENCES): `test_department_makeups` covers reading the department's form by its titles (a
required question Faculty does not know or a missing one stops it), the response's fields with instructor@tamu.edu as the recorded
email and the Other box for an extra-time length, a receipt read back, who owes a request (excused absence over an exam's seating-chart
day; not a quiz, an undecided absence or one that misses the day), the preview's answers and default session, Send only with
"submit" and the fingerprint, sent once (replay, refused, timed out and never resent), the makeup sitting on the exam record, rules
reused for the next student, Gmail receipts confirming a request and standing for the instructor's own, the Assistant policy, and the
Follow-up card with its dropdowns and Approve. Google Forms and Gmail are fakes in the module. Run it through
`manage_checks.environment` on isolated NVMe scratch with `test_absences`, `test_makeup_booking`, `test_followup_suggestions`,
`test_assistant*`, `test_teach_inbox` and `test_catalog_migrations` (migration `absences/007_department_makeups`); `node --check
static/followup-suggestions.js`. Restart after the change, once the restart check is idle.
Department makeup STUDENT NOTICE (2026-10-22, ABSENCES "DEPARTMENT MAKEUP EXAMS" › STUDENT NOTICE): `test_department_makeups`
(`NoticeTests`) covers the one email from the instructor's Gmail once Google accepts a request (recipient, subject, lines, no
Cc), a replay telling no one again, the worker sending one that could not go at once, and a lost answer never resent; Gmail is the
fake in `tests/test_gmail_mail.py`. Migration `absences/008_department_notices`; `test_catalog_migrations` adopts it.
Department makeup GOOGLE SIGN-IN (2026-10-22, ABSENCES "DEPARTMENT MAKEUP EXAMS" › GOOGLE SIGN-IN): `test_department_makeups`
(`GoogleSignInTests`) covers no request recorded or sent without the kept sign-in, its cookies on every form call and Google's
renewed ones kept, a 401 failing with "sign in again", the 0600 session file, renewals only for the same sign-in, Forget, the
window's step classes and the Assistant never driving it. No migration; `node --check static/aim-signin.js static/absences.js
static/absence-makeups.js` and the frontend typecheck. Restart after the change.
Restart check APPROVALS (2026-10-28, AGENTS.md CHECKS): `test_assistant` (`test_the_restart_check_waits_for_an_approval_being_carried_out`)
reads `GET /api/assistant/restart-check` from inside an approved card's route: `"busy": {"approvals": 1}`, idle once its answer is
recorded. The floating Assistant window's lost approve answer is in `tests/browser_assistant_workspace.py`. No migration. Restart
after the change, once the restart check is idle.
