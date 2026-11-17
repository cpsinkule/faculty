## Teaching Inbox

Rule ID: **INBOX**. Current binding module contract. Read root `AGENTS.md` first.

- ASSIGNMENT-WORKSPACE (requirement, 2026-10-03). Inbox remains the receiving area. An associated scan's existing working
  page also mounts in its assignment workspace. When the scan page observes a changed association it emits
  `faculty:scan-association-changed`; the shell resolves that scan's current assignment again without replacing its editor.
  Pending or failed context reads clear the former assignment navigation so an action cannot use a stale assignment; while a
  move between one assignment's pages resolves, that navigation is instead held inert (never followed), so the page does not jump.

- The sidebar opens **Current** (requirement, 2026-09-24: one list instead of Needs
  review/Queued/Running/Failed tabs): every unfinished scan (other submissions: SCANS AND SUBMISSIONS), failed first, then
  needs review, then running and queued, newest first in each; each tile says its own
  state, progress or recorded reason, and a failed job offers **Retry job** on its
  tile (a scan says **Retry scan**, in its '⋯ Scan actions' menu and on its page).
  **History** groups completed/reviewed and cancelled items using existing derived
  scan states; **Deleted** has its own tab. These are views, never a new retained
  status or archive operation. The API keeps its `needs_review`/`pending`/`running`/
  `failed` filters and adds `current`; links to the old tabs open Current.
  Unfiltered API listing stays backward compatible.
  The list page (UX overhaul, 2026-09-26) is one toolbar (the tabs, SCANS AND SUBMISSIONS, search with
  `data-page-search`, which also shows the pages and pictures nearest the words above the list, of every kind Faculty
  indexes, and has a "Search by a picture" button beside Search: PICTURE-SEARCH, 2026-10-10;
  a worker dot, a More menu with New submission, Use a submission
  code and Codes & API access), a status strip on Current ("N need you · N working ·
  N failed", the server's own `counts`), then the cards. A scan card's '⋯ Scan
  actions' menu holds Start over…, Retry scan (failed, or cancelled after its review) and Delete from
  Inbox…; **Cancel scan…** is only on the scan's page, behind a confirmation, since a
  cancelled scan not yet reviewed cannot be queued again. Submissions folds a run of like finished submissions
  (same action, scope and source, each within 60 s) into one group; an unfinished one keeps its own row. History draws finished
  scans as compact cards. SCANS OF ONE ASSIGNMENT ARE ONE ROW (2026-10-02: an exam's sixteen copier scans were sixteen
  cards): scans whose latest grading association names the same grading assignment fold into one disclosure, always in
  History and Archived and in Current once two or more share it, summed as "Exam 1 · 16 scans" with pages, labeled papers
  and chips for how many need you, are working and have nothing to do. Its title links to that assignment's Collect
  (`#assignments/<assignment_id>/<term_id>?area=collect`, ASSIGNMENT-WORKSPACE); the rest of the row opens and closes the
  group. Until the instructor opens or closes it, it opens
  whenever one of its scans needs them (also one first drawn closed while its scans were with the worker) and stays open;
  once they open or close it, their choice stands through polls and views (browser memory); a closed group draws no
  cards. Two groups with one title (two terms' "Exam 1") name their term. A view only: every scan keeps its own card and
  opens as before, from the additive `scan.assignment` ({plan_id, title, code, term, assignment_id, term_id}), `scan.pages` and `scan.papers`
  that only the Inbox's own list read (`files=input`) carries (`InboxService._scan_groups`, two reads per page of fifty;
  the default body is unchanged). PDFs dropped on the LIST
  open Upload with them chosen, and `#inbox?upload=1` opens Upload once; scan pages take no drops. A delete's toast offers
  Restore (the ordinary restore). The list polls every 4 s while anything listed is being worked on, every 15 s on an
  idle Current and every minute elsewhere, and at once on coming back to the tab, the window or the Inbox from another
  section (FRONTEND.md "Poll as often as the page changes"); a failed background read keeps the page with a "Couldn't
  refresh" notice, and a job route the server answers 404 says "This scan is no longer in the Inbox" ("This submission …"
  when nothing on the page said it was a scan) and is not read again until the route changes. A scan waiting on a worker
  that is not checking in (`unknown`, `stale` or `stalled` in the shell's overview read) says so above its card, as the
  list does. A job's page keeps Received and Sender under Submission details; its Job ID, Attempts, Storage, Audit history
  and Command & result details sit under one **Technical details** fold, whose history is drawn only once it is opened. A
  scan's state chip says the scan's own state (Needs you, Working, Nothing to do; a stored failed status says Failed), never
  the stored "Needs review"; before the scan's first read it waits hidden. Other submissions' chips: Needs you, Working
  (queued or running), Failed, Done, Cancelled. Inside the assignment shell (`#assignment-workspace[data-area]` set) a scan
  page leaves the header trail to the shell (Home › Assignments › <title> › Collect) and names its own Inbox trail again once
  the shell's context clears (`faculty:assignment-context`, ASSIGNMENT-WORKSPACE "ASSIGNMENT CONTEXT").
- SCANS AND SUBMISSIONS (requirement, 2026-10-18: approved the review's "the list you open after an exam is only paper";
  "make all of your suggested changes"). **Current**, **History** and **Archived** list scans only; every other submission
  (quiz upload, archived documents, roster sync, Accommodations imports) is under its own **Submissions** tab
  (`#inbox?status=submissions`), every status, unfinished first in Current's order then the rest newest first, with its tile
  Retry job, Review command and Review approval as before. **Deleted** holds both. Each tab's count and the status strip
  count what that tab lists; Current's strip adds "N other submission(s) need(s) you" linking to Submissions when any is
  failed or waits for review. Old links keep working (`#inbox?status=failed` etc. open Current). The list read takes three
  additive parameters: `kind` (`scan`, `command` or `all`; with it the body adds `kind_counts` {scan, command} of each kind's
  filed counts and `assignments`, the grading assignments the listed scans are filed under [{plan_id, title, code, term,
  assignment_id, term_id, scans}]), `needs=1` (only rows filed as needing the instructor) and `assignment=<plan_id>` (one
  grading assignment's scans, from each scan's latest association). A read without `kind` is byte-identical to before. Views
  only: nothing stored changes, `PRESENT` applies as before.
- LIST FILTERS (2026-10-18). On Current, **All scans / Needs you** (shared filter chips); on Current, History and Archived an
  assignment picker ("All assignments", then each assignment with its scan count). View state only, never stored. Search
  also matches the title of the grading assignment a scan is filed under (its latest association; the scan's own row).
- `teach_inbox` provides the common command protocol. Web/API/CLI/code/email
  adapters call `InboxService.submit`; ingestion never executes a processor.
- Only explicit registry entries in `protocol.ACTIONS` may dispatch through
  `handlers.HANDLERS`. Unimplemented/unknown actions stay `needs_review`. Do not
  implement future quiz/exam/grade processors merely because their names exist.
- `inbox_messages` is a retained payload owner with explicit course-definition,
  term, and section foreign keys. Its jobs/artifacts/events reference that owner.
  Unclassified/authentication-failed submissions are workspace-level audit
  records, staged on NVMe first. The owner-less workspace actions
  (`accommodations.sync`, `accommodations.exams.sync`, `aim.form_capture`: the AIM
  window's recorded form structures, ACCOMMODATIONS "AIM FORM CAPTURE") are
  workspace-level records too, and no submission code can be made for them. Corrections require explicit review and retain
  the original command.
- The instructor can DELETE a submission from the Inbox (`InboxService.remove`,
  `restore`; POST `/api/inbox/jobs/<id>/delete|restore`). It is a recorded,
  reversible tombstone (`inbox_deletions`), never an erasure: originals,
  artifacts, events and whatever a processor applied stay, so nothing is freed
  and provenance holds. It requires the workspace actor, the job's current
  `version` and `confirmation: "delete"` checked server-side (the dialog's
  warning and tick are not the control; the worker's own tombstone of a scan it separated by assignment is the one other,
  SEPARATING A SCAN), refuses queued/running jobs, scans the
  worker is still busy with, and scans whose pages are in a reviewed assembly
  (a recorded submission). A deleted message is listed only under the Deleted
  tab and must count NOWHERE: every query that looks across submissions applies
  `teach_inbox.service.PRESENT` (intake duplicate/changed checks, papers seen
  in a scan, assemblies, exam collection, grouping conflicts, label evidence,
  bubble sheet claims), and `ScanService.visibility_changed` recomputes the
  duplicate/conflict states of the other scans on delete and on restore. New
  cross-submission queries must apply `PRESENT` too.
- BATCHES SCANNED IN STACKS (requirement, 2026-10-24: "when using my home scanners, I can't scan an entire batch of exams. I
  have to break them down into stacks of 80-100 pages at a time"). Each stack is its own scan and nothing needs combining:
  paper labeled on every page is matched page by page by its own label, whichever file a page came in, so a paper split
  between two stacks becomes its student's reviewed paper once every scan holding its pages has passed review, in either order
  (`SequenceService._assemble_labeled`, and the worker's catch-up). While another scan holding some of its pages still waits
  for its review, the scan's page says so as information ("continues in another scan that has not passed its review yet"),
  never as a paper to save by hand. A page scanned again in a second stack is a changed observation (scanner bytes differ), so
  its paper waits for the per-copy choice; deleting the stack that went wrong and scanning it again avoids that.
  PAPER LABELED ON PAGE 1 ONLY (same day: "these exams only have a code on the bubblesheet and the first page of the exam
  though"): a paper whose label ends one stack and whose last sides start the next is grouped as one paper
  (`SequenceService._continuation`). Only between neighbouring scans from the same sender (the scan just before or after it
  among those still in the Inbox, both inspected, arriving within 12 hours), only when the last label of the first is a current
  page 1 label of paper labeled on page 1 only followed by fewer sides than the side count, and the second starts with exactly
  the sides it lacks before anything else (a label, a barcode, a bubble sheet), none set aside. The first scan's plan holds
  those sides as the paper's last ones, its run, packet and label stay the first scan's, and the second scan's plan leaves them
  out, so each stack groups by itself by the ordinary all-or-nothing rule (`auto_group` also tries the neighbouring stack each
  way once a scan is inspected). Migration 018 lets a packet's and a run's pages be pages of a LATER scan from the same sender,
  never of any other message. The paper is assembled only once every side's scan passed its review and is still in the Inbox
  (`assemble_reviewed` also takes the packets holding this scan's pages); undo clears the second scan's pages with the rest.
  Anything else at the top of a stack is no continuation: counts that do not add up, another scanner or paper printed
  without labels wait as before.
  BUBBLE SHEETS IN THE STACK (first home-scanned Exam 2 stack, 2026-10-24: each student's bubble sheet lay before or after a
  booklet, sometimes another student's, and in grey a sheet's empty back is not blank): a bubble sheet is no side of any paper.
  Sheets are the pages the detector recognized and every page whose Scantron label was read (`sequence.sheet_pages`); a sheet's
  back is the blank side after it, or, in an attachment with an even number of sides, the unlabeled side after a sheet on an odd
  side (`sheet_back`). The label-to-label distance (`cadence`, the suggested count) counts only the other sides, so a stack whose
  booklets add up groups by itself; a wrong back leaves a paper short, which holds it.
  A PAPER SCANNED IN TWO STACKS (requirement, 2026-10-24, "automatic"): a sticker-labeled paper whose label was also read in another
  scan still in the Inbox, where that copy is already a grouped paper of exactly as many sides, is a repeat
  (`SequenceService._repeat`): it needs nothing, the rest of its scan groups by the ordinary rule, its first page becomes
  `rejected` and the kept copy's `conflict` clears, and its sides are no stretch to look at. Pictures are not compared: measured
  on that night's pages, a re-scan and another student's page of the same exam could not be told apart. A STACK SCANNED AGAIN
  WHOLE (2026-10-25: two Exam 2 morning scans held only 52 booklets grouped the night before, plus 32 new bubble sheets): when
  every paper of a scan is a repeat, nothing is ready to group, but the run still sets the repeats aside, automatically and from
  the preview (`apply`, `_auto_group`); it used to refuse, leaving those papers in conflict and ungraded.
- COMBINING SCANS (requirement, 2026-09-24: a scanner run can come out as more than
  one PDF, or a late paper is scanned alone). Uploads stay one scan per file; the
  instructor turns on **Select scans to combine** on the Inbox list (Current or
  History, never Deleted), ticks scans and chooses **Combine into one scan**
  (`InboxService.combine_scans`, POST `/api/inbox/scans/combine` with `job_ids`,
  `request_id`, `confirmation: "combine"` checked server-side; workspace only). A
  NEW scan receives every source PDF byte for byte (read through `read_artifact`)
  as its own attachment, so each file is still read on its own
  and no paper spans two files; it is inspected afresh, needs review, and records
  a `scans_combined` event naming each source and file hash. Work done on the
  sources is not carried over. The sources are then deleted with the ordinary
  tombstone (reason names the new scan) and remain restorable; anything delete
  refuses (busy, queued/running, reviewed assembly, already deleted) refuses the
  combination before anything is made. The request ID keys the new scan
  (`combine:<request_id>`): repeating it finishes an interrupted combination.
- STARTING A SCAN OVER (requirement, 2026-09-24: "I want to be able to rerun the quiz upload I did today from the start"):
  **Start over** on a scan's page (`InboxService.start_over`, POST `/api/inbox/jobs/<id>/start-over` with `request_id`,
  `confirmation: "start over"` checked server-side and `keep`; workspace only) makes a NEW scan of the same PDFs, byte for byte,
  inspected afresh like any arrival, and deletes the old one with the ordinary tombstone (reason naming the new scan; restorable).
  Nothing done on the old scan is carried: its grouping, readings and registrations stay with it. With `keep` its CHOICES are
  (`ScanService.start_over_plan`, recorded in a `scan_started_over` event): its assignment and sections, and the search for papers
  without labels with the same sides, versions and automatic registration, applied once the new scan is inspected
  (`apply_start_over`, once, a `scan_start_over_applied` event with any reason it could not). Anything delete refuses refuses
  this first, including a scan whose first pages are still being read (`before_removal`). `restart:<request_id>` keys the new scan.
- SEPARATING A SCAN (requirement, 2026-11-10, of five Quiz 7 makeups and one Quiz 6 makeup scanned in one stack and held at
  "The labels name more than one assignment": "faculty needs the ability to separate scans into its various assignments (if there
  are more than one)"). A scan is filed under ONE assignment, so a scan whose current labels name more than one (or one assignment
  in two terms) could neither be filed nor pass its review. The worker now separates it by itself
  (`document_identity/separation.py`, `SeparationService.process_one`, the worker's turn after page inspection): for each
  assignment a NEW scan holds that assignment's pages, from the same sender, inspected afresh and waiting for its review like any
  arrival, so it is filed by its labels and goes on by the ordinary rules; the scan itself moves to Deleted with the ordinary
  reversible tombstone (actor `automatic`, the one tombstone the instructor does not make; reason "Separated by assignment: its
  pages are in a new scan for each of …"), as Combine and Start over replace a scan. Nothing after the Inbox changes.
  WHOSE PAGE IS WHOSE (`plan`): a page belongs to the assignment and term its own label names, a current label of a print job or
  a bubble sheet's Scantron label; a page without one stays with the labeled page before it in its file (a paper labeled on page
  1 only, a blank back, a sheet's back), and pages before a file's first label go with that label. Nothing is read from names
  or pictures. Each new scan keeps one PDF per file of the old one, named `<file> (<assignment>).pdf` (numbered `01 `, `02 `
  when there were several files, so their order holds); its pages are the old scan's retained single pages joined in the sandbox
  (`process.merge_pages`), nothing rendered or re-encoded: measured on the scan above, every page reads the same label with the
  same page content.
  A SCAN WITHOUT LABELS, BY ITS PRINTED TITLES (requirement, 2026-11-11, of five DR copies, three of Quiz 7 and two of Quiz 6,
  scanned in one stack with no label on any page and held at "its first pages point to more than one assignment": "can you
  use it on the scans that got held up in the inbox"). Only a scan with no label at all, filed under nothing, holding no bubble
  sheet, whose first pages the assignment finder read as more than one assignment (its `assignment_found` event: the templates'
  conflict, or Jev naming two). The worker reads the top of EVERY written page with the finder's own local read
  (`faculty_ocr.recognize_covers`, `READ` 8 pages a turn, each batch a `scan_titles_read` event; a busy reader is waited for).
  A FIRST page is one whose top prints both field words, Name and UIN; a first page that prints a title line only one
  current assignment's blank prints is that assignment's, and the pages after it are its paper. A later page's heading is
  never a title (measured on that scan: sheet 2 of each quiz, headed "Free Response", matched the one assignment whose blank
  begins with that line; with the first-page rule the five first pages read Quiz 7, Quiz 7, Quiz 7, Quiz 6, Quiz 6 and no
  other page read anything). TITLES PROPOSE, THE PAGE COUNTS DECIDE (as the instructor asked of Jev's proposals, 2026-10-26):
  every file must start with a titled first page, and every titled first page must be followed by exactly its assignment's
  sides per paper (`UnlabeledService.paper_sides`, the DR copy's before the blanks'); otherwise nothing is separated and the
  scan's "Choose the assignment" step says why. Titles that name one assignment change nothing. Each new scan, once inspected,
  is filed under its assignment by the ordinary automatic association (`SeparationService.apply`,
  `GradingService.associate_found`, a `scan_separation_applied` event), which looks for its first pages and registers the
  students it is confident of, as for any scan found without labels; a scan it could not file is the finder's like any other.
  While its titles are read the scan says "Faculty is reading the title printed on every page to separate them: N of M read".
  ONLY a scan still waiting for its review, fully inspected, with no printed label still being read and no page still being
  checked for a bubble sheet. NOT SEPARATED BY ITSELF, held for the instructor as before with its reason on the scan and what to
  do: a scan separated before (restored from Deleted: never twice); one whose pages are in a reviewed paper; one whose paper
  continues in another scan (BATCHES SCANNED IN STACKS); one grouped, identified, read for first pages, sorted, read as bubble
  sheets or with a page set aside by hand (a new scan would start without that work: Start over separates it); a file with no
  label at all; and a bubble sheet with no label of its own lying between two assignments' papers. What the worker did by
  itself (automatic grouping, photocopied stamps, names read) is done again on the new scans.
  ORDER, so no page is ever in two scans still in the Inbox: every new PDF is made and checked as an upload first; one
  transaction then records the plan (`scan_separating` on the scan) and tombstones it; each part is submitted under
  `separate:<message>:<n>` with a `scan_separated_from` event; `scan_separated` on the scan names the new scans. A worker
  stopped in between finishes from the recorded plan on its next turn. A part that cannot be made (a processing limit) changes
  nothing: it is recorded once (`scan_separation_failed`) and the scan stays held with that reason. While the worker has it, the
  scan says "Faculty is separating these pages into one scan for each" (a `wait` step, so it reads Working, not Needs you).
  No route, no button, no migration. KNOWN LIMITS: the new scans arrive now, so a paper labeled on page 1 only that runs from a
  separated stack into the next stack (or from the one before) is not joined by itself and waits on Papers; a scan without
  labels that the finder filed under ONE assignment (its sample of first pages saw no other) and whose later first-page read
  registers a paper under another (DOCUMENTS "JEV MIXED SCANS") has been read for first pages, so it is left to the instructor;
  a scan without labels whose papers have blank backs between them, or bubble sheets, is not separated by its titles.
- `InboxStorage` reuses the verified storage engine under `inbox/<message-id>/`.
  Course-wide messages remain on NVMe while any term of that code is current;
  narrower scopes use the current status of their own descendants. Originals and
  artifacts are immutable and checksum-checked. Never bypass lifecycle/owner locks.
- Inbox intake, review, and processor results write to NVMe. `staged` means a
  non-current/unclassified working copy awaiting the nightly backup; `active`
  means a current scope's working copy. Normal reconciliation must not publish
  Inbox HDD snapshots. The nightly coordinator backs up both states, verifies the
  final copy, then purges only non-current payloads. Archived Inbox writes restore
  to NVMe first. Never substitute an older snapshot for a missing staged copy.
- The live shared catalog and cross-process locks are on NVMe when deployment
  config has `catalog_on_nvme: true`. HDD catalog snapshots are nightly backups;
  the retained pre-migration HDD catalog is not live. Stop all Faculty processes
  before `manage_storage.py migrate-catalog`, since lock locations change too.
- Submissions deduplicate by source/principal/idempotency key and content digest.
  The worker owns a cross-process execution lock. Interrupted jobs require an
  explicit retry. Future handlers must make side effects idempotent and validate
  their own metadata; a durable receipt is written before completion is recorded.
- `roster.sync` passes the job ID as `request_id` to the roster service, preventing
  repeated effects after an interrupted attempt. Existing ten-digit IDs stay intact.
- Consequential actions need a separate instructor approval of the exact command
  hash. API keys/codes cannot approve; changes/retries invalidate approval. Failed
  sender authentication cannot be overridden by reviewing or approving a job.
- Cloudflare email delivery is configured for `inbox.example.com`. Authored
  commands still require signed delivery, an allowed sender, and a separate command
  secret. Scanner/personal intake uses a separate bounded policy and always starts
  in review; never authorize from From/Authentication-Results headers alone.
- Opaque one-time codes bind an available action and permanent scope, expire, and
  store only token hashes. Do not expose secrets in state/export/logs. QR payloads
  contain only the opaque token. Student identifiers are not submission tokens.
- The native worker is `faculty-inbox.service`; include Inbox payloads and queue
  metadata in the existing nightly backup and verified final archival procedure.
