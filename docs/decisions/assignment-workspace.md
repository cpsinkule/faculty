# One assignment workspace

Rule ID: **ASSIGNMENT-WORKSPACE**. Instructor-approved implementation, 2026-10-03.

## One pipeline (requirement, 2026-10-18)

The instructor approved every recommendation of the 2026-10-18 Inbox and assignment pipeline review ("make all of your
suggested changes"). Workspace-owned browser projections only; no new teaching records, tables, outside writes or automatic
permissions.

- UPLOAD IN PLACE (I1). `#assignments/<a>/<t>?area=collect&upload=1` opens Collect's own Upload scans once; the address drops
  `upload` first, so Back, a reload or the quiet refresh never opens it again. Every Upload scans for a plan with a source
  assignment links there; `#inbox?upload=1` only when no assignment is known.
- SCANS IN THEIR CHECKS (I2). Collect lists this assignment's scans still in their scan checks (`inbox_ready` false in the
  grading summary the overview already reads; no new read), each "Finish the scan checks" opening the scan page of its first
  open check inside the shell. `&scans=waiting` brings that list into view once per arrival (it says so when none waits). The
  "All scans" fold stays. The Inbox's group header links the other way (INBOX "SCANS OF ONE ASSIGNMENT ARE ONE ROW").
- ASSIGNMENT CONTEXT (I4). The shell dispatches `faculty:assignment-context` on window ({active, assignment_id?, term_id?})
  whenever its context appears, changes or clears; `#assignment-workspace[data-area]` is set exactly while an assignment is on
  screen (it is removed when the context clears, including a scan page outside any assignment). A scan page inside the shell
  carries the shell's crumbs (Home › Assignments › <title> › Collect) and the sidebar highlights Assignments (FRONTEND).
- PREPARATION NEEDS YOU ONLY WHEN IT WAITS FOR YOU (refines CRITIQUE ROUND's needs-you list). "Finish grading preparation" is
  not listed while the pipeline works it (a rubric preparation queued or running, the rubric check pending or running, a blank
  template being read: Home's rule, FRONTEND), nor beside "Check answer areas and templates" once every question has its rubric
  (the answer areas are the step; finalization follows). A failed preparation still needs you.
- THE BADGE CAN BE READ ON THE INDEX (requirement, 2026-11-02, of the Assignments badge's 1: "what needs me in assignments, it isn't
  clear to me"; the page showed four orange rows, none named as the badge's card). The index opens with Needs you: the very cards the
  badge counts (`assignmentDecisionCards`), each with its count, label, reason and link, on every tab and whatever the filters;
  nothing is drawn when none waits. A row whose assignment has not been started (no paper yet and only `paper`, `preparation` or
  `ready:` items, `rowDecisions`) names its next step in muted words: no orange block, no "more decisions", and it no longer sorts
  ahead as waiting. Inside the assignment those steps stay listed. Decisions with no card of their own (Certify grades, a scan
  check) keep the row's orange block as before; the badge's count is unchanged.
- A FINISHED RUBRIC CHECK SAYS SO (2026-11-02). While the rubric check waits for review (`rubric_quality.state` `needs_review`), the
  `preparation` item reads "Review the rubric check" and opens `?tab=answer-key&open=quality`, the words and address of Home's card
  and the badge, in place of "Finish grading preparation" (its key, and so the Prepare tab's mark, are unchanged).
- THE PAPER LEADS. The `paper` needs-you item comes first, so the index row names the missing paper before any grading item
  (extends PREPARE STARTS WITH ITS PREREQUISITE).
- A MAKEUP VERSION WAITING TO BE APPROVED (requirement, 2026-11-05, of the Makeups view's "Makeup version E is ready for you to approve":
  "when I go to the assignments page, it doesn't show quiz 7 needing anything"). A makeup draft of the plan waiting on the instructor
  (`makeups_waiting`, GRADING "MAKEUP VERSIONS", read once per request; ready, or failed with nothing to approve; not while the model
  drafts it, once approved or scrapped) is one needs-you item (`makeup`, "Approve makeup version E" or "Try again: makeup draft E", no
  count), on the index row and the overview alike; it is Create's, whose tab carries it. Its address (`?area=create&makeup=1`) opens the
  draft window once, as `upload=1` does, the address dropping the request first; Create shows the same words as a button in view
  (Draft makeup version… stays in Assignment tools otherwise), and closing the window reads the assignment again. Scrapping it there
  ends the item. A certified assignment comes back into Pending until then (CERTIFIED-ARCHIVE). The Makeups view is unchanged.
  ON THE BADGE (the same day: "can we have the makeup versions needing approval show on the notification badge on the assignments item on the
  sidebar menu"). In a current term each is one attention card (`makeup:<plan>`, count 1, "Approve makeup version E · Quiz 7"), so it counts
  once on the Assignments badge (`assignment_work`: its address is the assignment's, not a badge route's), the tab title, Home, and the
  index's Needs you. It left the "Makeups that need you" card (ABSENCES "MAKEUP BOOKING" › HOME), and so the Absences badge, or it would count twice.
- THE ZEROS ARE SAID AT CERTIFY (requirement, 2026-11-05, of Collect's orange "44 without a scanned paper": "can we take this warning
  out of the assignment pipeline? I think the notice of 0s being assigned in the certify page is enough"; replaces MISSING PAPERS
  NEED YOU and SETTLED ZEROS STAY SETTLED). A student with no scanned paper asks nothing of the instructor anywhere in the pipeline:
  no needs-you item or tab mark (the `collection` item is gone), no Home row (`grading:<plan>:missing` is gone), no orange block in
  Collect's lead, no "Needs you" on their row in Collect's list, nothing in the Grade step's callout, and neither a chip nor "Resolve
  N collection issues" on Scan records. Certify's confirmation ("These N students receive 0", GRADING) is where it is said; the one 0 Faculty certifies by itself, of a
  makeup that ended, is named in Certify's header and its "Certified by Faculty" fold (GRADING "MAKEUP CHANGES CERTIFY THEMSELVES"). Collect's lead
  keeps the count in plain words, the Papers filter still has "No scanned paper", and an absence or excuse can still be recorded in
  a student's row. Flagged papers (issues, in review, incomplete, missing pages) are unchanged. The exam record's own Collection tab
  and the Exams page's stage line and steps are not the pipeline, and stay.
- ONE COUNT OF MISSING PAPERS (amends COLLECT'S ACTION IS IN THE ROW's "the lead counts"): "without
  a scanned paper" is the record's students with no scan at all (`no_scan`), counted only once any paper of it is back
  (complete, incomplete, missing pages or in review). Incomplete papers (partial, missing pages) are not in that count;
  Collect's lead says them apart ("· M incomplete"). Collect's lead uses this count (`missingPapers` in
  `static/assignment-workspace.js`), in plain words (THE ZEROS ARE SAID AT CERTIFY).
  A student with an incomplete paper still has the decision open in their row (`decidesInRow` unchanged).
- WHAT RUNS BY ITSELF (review item 9). Readiness ends with one muted line naming the automations on in Compute settings ("Runs
  by itself: grading, flag review, late papers certify, Gradescope after Certify", or "Nothing runs by itself: you start each
  step") and links `#grading?settings=1` (I5). From `GET /api/grading/settings`, read when the overview opens or Refresh
  progress is pressed, never by the quiet refresh; a failed read leaves the line out. Only with a grading plan.
- BEFORE GRADING IS PREPARED (review item 12). Without a plan, Prepare offers Prepare grading (after Add paper in Create, the
  primary, while the paper is missing); Grade, Certify and Return each say in one quiet line what they wait for (Grade: Open
  Prepare, Enter scores by hand; Return: Gradebook) instead of repeating Prepare's panel.
- THE EXAM RECORD INSIDE THE SHELL (I6, review item 13). Inside an assignment the exam record's lifecycle graph and "Next
  step" callout (`data-exam-lifecycle`) are hidden and its tab row (`data-exam-subtabs`) is DESIGN-LANGUAGE §4 sub-tabs.
  Outside the shell nothing changes (EXAMS).
- WORDS (review item 2). Stages are the tab names everywhere: Create, Prepare, Collect, Grade, Certify, Return (Review is a step
  of Grade; the needs-you labels "Review grades" and "Certify grades"). Readiness rows: Paper, Print, Collect, Grade, Certify,
  Return (were Collected, Grading, Certification, Delivery). "scan checks" is the Inbox's work on a scan (never "Inbox checks"
  or "Inbox review"). A scan's link to its assignment reads "Assignment: <title>". No "Assignments & printing": inside an
  assignment "Create", outside "Assignments". Collect's paper filter "Needs you".

## Recovery and compact collection (requirement, 2026-10-17)

The instructor approved all eight findings of the assignment UX review, in order, and chose a compact student list
with one selected student's details for Collect. These are workspace-owned browser projections and temporary drafts;
there are no new teaching records, routes, tables, outside writes or automatic permissions.

- GRADING "KEEP CONFLICTED EDITS" retains a refused review's edits for explicit comparison, application or discard.
- PREPARE STARTS WITH ITS PREREQUISITE. Without its registered paper, each Prepare view leads with Add paper in Create,
  using the existing paper editor for this assignment and term. Downstream setup instructions are folded; empty counters,
  unusable controls and the Assistant preparation action are omitted until the paper exists. Printing stays independent.
  BEFORE A SCAN CLEARS (2026-10-18, pipeline review item 12, approved). A plan whose scans have not cleared their scan
  checks yet (`entry.ready` false) opens its steps like a plan set up before papers: rubrics, templates and answer areas
  come from the paper and its blank templates, never from scans, so they can be prepared now. One line above the steps says
  what waits ("Waiting for scan checks on N scans", or none cleared yet) and links "Finish the scan checks" (Collect's
  waiting scans, I2) or Upload scans (I1). Only grading needs papers, and the Grade step says so. No server gate changed:
  "Finish Inbox review first" was a browser-only panel.
- Teaching review for a particular plan remains inside Return, with the same title, tabs and hidden assessment picker.
  The unscoped Teaching review page remains standalone. Existing scoped links keep working.
- A failed collection read provides Try again on Collect itself. Existing displayed data and unsaved drafts survive.
- Attention marks count readiness decisions on the tab their existing destination opens, including seating on Prepare.
- COMPACT COLLECT replaces the layouts in COLLECT'S ACTION IS IN THE ROW, ONE ROW AT A TIME and NOTHING SCANNED YET
  below. The left list is searchable by name/section and filterable by permanent section and paper status; with a paper
  flagged it initially shows exceptions, otherwise all students (no scanned paper is no exception, THE ZEROS ARE SAID AT CERTIFY). One selected student's paper, scan links and expectation editor
  are at the right. Selection does not jump on refresh or save; filtering it out keeps its details with an explanatory line.
  Temporary drafts use both permanent membership IDs and keep the version originally edited. Switching students, filtering,
  failed saves and quiet refresh keep them. Save/discard affects only its student; discard rereads before clearing anything.
  The central leave guard covers all drafts. Show counts and mark students with unsaved work. Before any papers return,
  nobody is called individually missing; known absences/excuses can still be explicitly recorded. Missing papers never
  imply an absence, excuse or grade. Every save uses the existing EXAMS version-checked route.
- Return keeps its six delivery facts visible and folds repeated certification, Canvas and Gradescope details. Mixed-age
  Canvas posting facts show their read-time range, and column details show each column's posting time (TEACHING-WORKFLOWS).
- Index rows name the first existing needs-you action and its count/unit, with remaining decisions counted separately.
  Unavailable reads keep their warning; certified destinations, archive rules and all default API bodies stay unchanged.

## Workflow clarity (instructor-approved, 2026-10-09)

The instructor requested all eight improvements from the assignment-pipeline review. Workspace-owned browser projections
show waiting, queued/running, locally recorded and published work separately. An empty review queue is not evidence that
certification is ready, and a local gradebook column is not evidence of publication. Keep destination receipts dated and
unknown outcomes explicit. Readiness and delivery controls open the existing owning actions; Send and Publish remain explicit.
Create assignment is the shared starting menu for paper, a model draft or an existing assignment's exam arrangements. Derive
the immutable key by default under advanced options, and omit the seating step unless requested or an existing chart requires
it. The Prepare checklist and its requested Assistant preparation reuse existing owner reads and approval rules; that request is
review-only (server-enforced `review_only`, 2026-10-09 review), so every change it prepares is an Approve card.
Collect shows the existing exam's student collection and scan exceptions together, with explicit, version-checked expectation
changes through the exam route; no missing paper is automatically marked absent or excused. This browser integration owns
no teaching data. The review desk keeps reasons visible and offers inline image zoom with browser-session preferences;
no image bytes or evidence coordinates change. Print controls name Paper PDF and Labels separately and keep stamp identity
warnings. Existing quiet refresh, unsaved-work guards, stable identities and per-item pipeline gates continue to apply.

## Signals that hold (requirement, 2026-10-11)

Of a second assignment-pipeline review's six findings the instructor chose four ("build 5, 6, 1 and 3 in your order"), then the
other two with their own answers ("#2: ask first, #4 upload the scans"), then two of its further ideas ("build the stalled line and
gaps 1 and 3"). DOCUMENTS has "THE CHOICES STAY WITH THEIR COPIES" and "WHAT WAS TYPED ON PAPER STAYS"; the rest are here.

- READY TO CERTIFY MEANS NOTHING IS STILL COMING. The `certify` needs-you item and the lifecycle's "All reviewed · ready to
  certify" appear only when every response is reviewed AND no linked scan waits for its scan checks (`awaiting_review`) AND no
  grading task is queued or running (`pending`): certification itself refuses both (`inbox_pending`, `grading_unfinished`), and a
  late or DR scan after a reviewed first batch is routine. Until then the grading row keeps its count and says what waits ("4 of
  4 reviewed · waiting for scan checks on 1 scan", opening Collect's waiting scans, I2; "… · 3 grading tasks queued or running", opening Grade),
  reads under way on the overview, and the next step is that one ("Finish the scan checks"). Only counts the grading summary
  already carries decide it: the certification view is never read for the overview (PERFORMANCE; it was ~1 s of the read), so
  Certify's own page still lists every other blocker. A changed certification still marks the Certify tab; a current one is not
  reopened by this (CERTIFIED-ARCHIVE shows the late scan). The Exams hub reads the same stage. Home's "Certify grades"
  row (FRONTEND; it follows the grading page's step graph, not this stage) waits the same way since the instructor's "fix Home's
  certify row" the same day: not while a linked scan is still in its scan checks. The grading step graph and
  every "Next" line drawn from it say the same since "align the Grading card's certify wording too" (GRADING "THE CERTIFY STEP
  WAITS FOR A LATE SCAN").
- COLLECT'S ACTION IS IN THE ROW (replaces ONE NAVIGATION's primary "Find or excuse the N without a paper", which ac655a9 removed
  on 2026-10-09 when the student list moved onto Collect, leaving no action and hiding the collection record's link exactly when
  papers were missing). A student still expected whose paper is missing or incomplete (`decidesInRow`: those without a scanned
  paper and those with an incomplete one, counted apart since ONE COUNT OF MISSING PAPERS) has Expectation, Reason and Save open in their own row; every other row keeps them folded
  under "Record an expectation". The caution (a missing paper alone is not an absence or an excuse, and recording one changes no
  grade) is said once above the list, never in each row. The exam record's collection is linked whether or not a paper is
  missing. No button repeats the list, so Collect has no primary while papers are missing (DESIGN-LANGUAGE: at most one, and
  each row's Save is its own); Upload scans keeps its rule (primary only while no paper is missing and no scan is linked).
- ONE ROW AT A TIME. Saving or discarding one student's row draws that row again from the server's answer (their new version,
  status and saved words) where it stands, and leaves every other row and what is typed in it alone; the whole list is drawn
  again once nothing in it is unsaved. So a row saved while another is unsaved can be saved again (its version was stale and the
  save refused), and a discarded row is never shown with its unsaved words marked as saved: it stays unsaved, and guarded, until
  the server's copy has replaced it, and a failed read keeps what was typed. A saved row's fold closes; a discarded one stays
  open on what was read again. The list is the collection review's own (`data-patch-static` on its host): the 30 s refresh
  patches its rows in place, so a row opened and not yet typed in stays open (the refresh emptied and rebuilt the list before).
- UPLOAD THE SCANS (the instructor's answer to the review's "an unscanned exam misses its missing-paper item": "#4 upload the
  scans"). Once the assessment's own date has passed (the lifecycle's `dates.date`, the main sitting's or the chart's, before
  campus today; a later makeup or DR sitting does not put it off), with grading set up for it (a plan), papers expected, nothing
  collected and no scan linked, and while its grades are not certified, uploading the scans is one needs-you item (`upload`,
  "Upload the scans", Collect's address, no count); Collect's tab carries it. It is never "Find or excuse" for the whole class:
  with nothing scanned every student would be missing. An assessment scored by hand (no plan) never carries it, and a scan linked and
  still in its scan checks ends it (its own check is what waits).
- NOTHING SCANNED YET. Until a paper of the exam is back (`papersBack`: any student complete, incomplete, missing pages or in
  review), nobody is "without a scanned paper" one by one, before the date as after it. Collect's lead is the Readiness row's own
  words ("No papers scanned · N students expected"), with no orange count; Upload scans is its one primary while no scan is
  linked; the list reads "No papers scanned yet", with every student folded under "Students · N" and no row opening its decision
  (a student already known to be absent or excused can still be recorded there; a flagged student stays above the fold).
  COLLECT'S ACTION IS IN THE ROW begins with the first paper back.
- WHY GRADING WAITS (requirement, 2026-10-11: "build the stalled line and gaps 1 and 3"). Beside "N responses ready for grading"
  the overview says, in the Grade step's own words (`autoGradeLine`), that they start by themselves (the settle time, or their
  own batch after the one running), or why they do not: automatic grading is off, the last batch was cancelled or reset, or a
  batch could not start (the worker's recorded reason). Those three are the instructor's, as on Home: the line reads in ink
  behind the needs-you mark and links the Grade step ("Open Grade"), where a batch is started; it is a line, not a needs-you
  item. While grading preparation is listed under Needs you no start is promised ("A question still in preparation holds its
  responses; the others start by themselves"). The detail read carries the owner's `auto_grade` (`auto_grade_state`, three
  indexed reads) only while responses wait; otherwise the body is unchanged, and the list never has it.
- DELIVERY NAMES CANVAS (the same instruction). With a gradebook column the overview's Return row (Delivery until 2026-10-18) says "not sent to Canvas",
  "sent to Canvas", or "sent to Canvas, N to check" between the column and Gradescope; N is everything Return's own line lists
  apart from accepted (TEACHING-WORKFLOWS "EACH CANVAS SITUATION BY ITSELF", one reading: `canvasReceipts`). Without a column it
  reads as before. From the delivery receipts the overview already reads; nothing more is read.

- Assignments replaces the Assignments & printing, Exams and Grading navigation entries. The
  opening view is Needs you and progress; Create, Prepare, Collect, Grade, Certify and Return
  are places to work, not sequential gates. Existing working routes remain usable inside the
  assignment shell. Inbox remains the receiving area for unassociated material.
- LEDGER (2026-10-04, requirement's "go with Ledger", DESIGN-LANGUAGE §1.2, §1.5): on Needs you and progress each decision
  waiting is a framed row with its count in the orange needs-you block; Readiness marks each row by its stage's state (done a
  green tick, under way an amber dot, failed a red block, not started a dash with the words muted); on the index an assignment
  with decisions waiting carries the orange block and the others' summaries read muted. Presentation only: no read changes.
- ONE NAVIGATION (requirement, 2026-10-05: "each page/submenu of the Assignment page/pipeline is not very intuitive or user
  friendly"; they approved the plan: one navigation, stage state on the tabs, each tab leading with its next step, plain words;
  DESIGN-LANGUAGE §1.4, §1.8). Inside an assignment its tabs are the only navigation. Each tab carries its mark (`stageMarks`, from
  the overview's read: on a working page the shell reads `/api/assignments/<a>/<t>?delivery=0` in the background, at most every
  15 s per assignment; no API changed): needs you, the orange block with how many needs-you items open there; done, a slate tick;
  failed, the red block; nothing while not started or under way (the mark is a hidden child, so a tab's name stays its word). A
  mounted grading page shows neither its link bar (Scan records and Bubble sheets are Collect's buttons; the exam record and the
  gradebook at this assignment are the overview's Sittings, students and makeups and Gradebook; Gradescope is Return's and
  Certify's) nor its "Step N of 6" line; its step graph shows only the tab's own steps, known by the tab each opens, unnumbered
  (Prepare: Rubrics & Templates, Answer areas, Key & Rubric Finalization; Grade: Grade, Review; none on Certify); its refresh and
  Compute settings are two icons at the end of the tab row (settings are not navigation); its "Next step" is one line.
  Return keeps Teaching review and Grade delivery as underlined sub-tabs and drops its assessment picker; its receipts read
  "Faculty's records of what it sent" and Refresh is "Check again". The overview has no links repeating the tabs, says "Nothing
  waits for you here" / "Nothing is running" plainly, gives Return in words ("Not in a gradebook column yet · not published on
  Gradescope"; with a column, Canvas between the two, 2026-10-11) and says a run of the same activity once ("Gradescope created ×4", `activityRuns`). Collect leads with the
  students who have a complete paper and those without one; those without one are decided in their own rows, with the exam
  record's collection, Upload scans, Scan records and Bubble sheets above them (2026-10-11, "COLLECT'S ACTION IS IN THE ROW";
  until 2026-10-09 one primary, "Find or excuse the N without a paper", opened the exam record's collection). Create leads with
  the versions its current print jobs print (a rebuilt quiz's A2–D2), the replaced ones folded under Earlier versions, and its
  primary is chosen by state (Add or revise paper with no paper, Print copies before anything is printed, none after).
- CRITIQUE ROUND (2026-10-05, requirement: "Use the impeccable and kowaleski skills on these pages and follow their guidance";
  Impeccable critique 26/40, then fixes; they chose to fix all of it and answered the four questions):
  - MISSING PAPERS NEED YOU (replaced 2026-11-05 by THE ZEROS ARE SAID AT CERTIFY; "Yes, once the quiz was given"): after the assessment happened (lifecycle `phase` past) and while its
    grades are not certified, the students of the record with no scanned paper are one needs-you item (`collection`, "Find or
    excuse the students without a scanned paper", their count, Collect's address; with no paper back at all the item is
    "Upload the scans" instead, 2026-10-11); Collect's tab carries it, and Home lists
    `grading:<plan>:missing` once papers of it came back (FRONTEND). Missing papers are worded "without a scanned paper"
    everywhere; the needs-you item for certifying is named as its tab ("Certify grades"); the lifecycle's grading row reads
    "All reviewed · ready to certify" (both only once nothing is still coming, 2026-10-11).
  - THE MARKS AGREE AND STAY PUT: Readiness's Grade row is done when the Grade tab is (unless the row itself says a scan still
    waits for its scan checks, 2026-10-11); every tab keeps the mark's slot (tabs 8 px
    padding) and the row keeps 88 px at its end for a grading page's two icons, so the tabs never move and fit one row at 1180 px;
    a mark's words are the tab's description (`aria-describedby`); a working page re-reads the marks every 30 s (one read per
    15 s at most; never hidden or under a dialog), so a decision clears its mark without a move.
  - THE OVERVIEW ("Yes"): one column led by Needs you; Running now only while something runs; Readiness (Paper, Print,
    Collect, Grade, Certify, Return; Recorded merged into Return; the tab words since 2026-10-18), each state also said in words for a screen
    reader, with its links in one row; Recent activity folded at the end.
  - A FINISHED TAB NEVER PUSHES THE NEXT ONE: inside an assignment a grading page's "Next step" is one quiet line with a go-link
    (no ink button, no orange rule; the next tab carries the mark), and Grade's missing-paper notice keeps its title and links
    without the paragraph (Collect is where that work is). Since 2026-10-18 (review item 11) inside an assignment a finished
    step's "Continue to …" (the step header's, Answer areas', Key & Rubric Finalization's, Scan records' and All responses'
    "Continue to Certify") is a quiet go-link, not a button (grading.css); outside the shell it stays a button.
  - SUB-TABS: Return's Teaching review / Grade delivery / Regrade requests are DESIGN-LANGUAGE §4 sub-tabs inside an assignment
    (Regrade requests since 2026-10-07, REGRADES: `?view=regrades` on the Grade delivery address, its open count the needs-you
    block, and the Return stage's mark counts the open requests, `needs_you` key `regrades`). An open request keeps a certified
    assignment out of the archive until it is answered (CERTIFIED-ARCHIVE "NEW WORK STAYS OUT").
- OWNERS: `assignment_workspace.py`, its private read APIs and browser shell are workspace-owned
  projections, with no retained workflow state. An instance references a course-wide assignment
  and its permanent course term. Unapproved drafts retain their existing course-wide identities.
  All writes remain in their owning services. No ownership, identity or submission is copied.
- Compose the owners' lifecycle, scan, grading, certification and delivery summaries. Keep
  denominators separate, apply PRESENT to submission reads, and use CERTIFIED-ARCHIVE. Missing
  or failed reads are unavailable, not complete. GET never creates a plan or advances work.
- Explicit creation prepares or reuses the term grading plan alongside the paper. Registered
  versions use the existing template import; failures keep completed work and offer explicit
  retry. Printing does not depend on completed rubrics. Generated drafts retain their existing
  approval and resumable handoff. Question preparation presents wording, solution, rubric and
  answer areas together, with existing version links and confirmation controls.
  Create retains the approved quiz/source link, renaming, Given on, makeup drafting and Canvas
  placement controls. Return retains context through linked gradebook columns and their existing
  course/section redirects. Changes to a scan's association update the shell without replacing its editor.
  Successful gradebook view reads or saves that change the addressed column's assignment emit
  `faculty:gradebook-association-changed`; the shell re-resolves that route and clears unlinked
  context without replacing the owner editor. Neither association hook adds periodic reads.
- INDEX ORDER (usability audit, 2026-10-04: the list came in the order of internal IDs): the browser orders the index with a
  decision waiting first, then by the paper's exam or Given-on date, then by title as read (Exam 2 before Exam 10); archived
  instances last in the same order. The list API is unchanged.
- INDEX TABS (requirement, 2026-10-09: "instead of having the dropdown menu for status, have tabs at the top "Pending",
  "Certified", "Archived""). The index opens on three framed tabs above its filters, in place of the Status select; each counts
  what it would list under the same search, Work and Course (an inventory figure, never the needs-you block). PENDING: a current
  term's assignments from creation until their grades are certified, unapproved quiz drafts first; a certified one comes back
  while its certification no longer holds ("any certified assignment that has a change to it should go back to the pending
  category until it has been recertified"): CERTIFIED-ARCHIVE's view (`archived` null: the certification changed, work made
  since, an open regrade request), the row saying "Changed since certification" or "Certified · new work since". CERTIFIED: a
  current term's assignments that view holds, each with the day it was certified. ARCHIVED ("reserved for assignments from
  previous terms, stratified by course"): every assignment of a term that is not current, whatever its state, one ruled region
  per course (its code the heading, by code as read), latest term first, each row naming only its term. Browser-only
  (`indexTab`, `archiveGroups`, `rowStatus`); the list API is unchanged. `?status=pending|certified|archived` opens a tab;
  the select's former `current` is Pending and `all` (only a past term's course page sent it; it now sends `archived`) Archived.
  A CERTIFIED ROW OPENS ITS GRADES (instructor, the same day: "When an assignment is certified and not pending, when I click on
  it from the assignment's page, it should direct me to the certification page with all of the grades"): a row holding that view
  (Certified, or certified under Archived) links `#grading/<plan>?tab=export&open=students`, Certify
  on its Students table with every student shown (`rowHref`); a row in Pending still opens Needs you & progress.
- PIPELINE, source invalidation and all-or-nothing automatic grouping remain unchanged.
  Certification, local gradebook recording, external sending and publication stay distinct.
  New work after certification stays visible. No assistant automatic permission is added.
- Old links, default APIs, quiet refresh, unsaved-work guards and desktop DESIGN-LANGUAGE remain
  supported. Tests use CHECKS' isolated NVMe environment and fake outside services.
- Requirement, 2026-10-03 (review fixes): moving between an assignment's pages keeps the owner editor
  mounted, the shell's navigation in place but inert, and the assignment's header until the new address
  resolves; nothing is moved out and back. Bare `#documents`, `#exams` and `#grading` open Assignments,
  so links that need the full lists use `#exams?view=list` (every exam record, New exam) and
  `#documents?tab=batches[&status=attention]` (every print job); Assignments links both. Home's exam
  requests open the one exam's Requests tab, or that list. The palette's New exam opens over Assignments.
- The overview's quiet refresh keeps the delivery receipts it read on opening (`?delivery=0`, PERFORMANCE); opening it,
  Refresh progress and Try again read them again. Since 2026-11-01 (PERFORMANCE "THE ASSIGNMENTS PAGES") the page is drawn from
  `?delivery=0` and the Return line ("Reading the receipts…" until then) fills in from Return's own read; a failed read says
  "Could not be read" and "Some checks could not be read", as before. The assignment's read no longer carries `workflow.export`.
  Coming back to the list draws its last read until the new one patches it.
- HEADER AND PRINT (2026-10-03, after 4507e55). Inside the shell the page title is the
  assignment's and the trail ends in its area; an owner's context line that extends the shell's
  `<code> · <term>` (a chart's sections, a scan's file, an exam's sections) stays as the context
  line, and the owner's object stays named on its page (a chart's room in its status card, a
  scan's headline on its card and in the browser tab). The shell's navigation, notice and
  summary are screen-only: `seating.css` never prints them, so a seating chart, its packet and
  its tiles print exactly as before. No subtitle repeats the area (the tabs name it) or the assignment's
  title, so every area opens with the same header and the tabs never move (requirement, 2026-10-04, the
  Assignments review: they sat 61 px lower on four areas); an owner's own subtitle sentence stays.
  The list's rows are one link each, the assignment's serif title (the whole row opens it).
- THE GRADING INDEX IS RETIRED (requirement, 2026-10-18: pipeline review P3 and I7, "make all of your suggested changes";
  it replaces the 2026-10-03 rule that kept `#grading?view=list` as a second index). Assignments lists every assignment;
  `#grading?view=list`, and every `#grading` address without an assignment, opens Assignments in its place (the address is
  replaced, no history entry; the grading overview `/api/grading` is no longer read or polled by the browser for it).
  `#grading?settings=1` (and the old `#grading?tab=settings`) opens Compute settings over Assignments. Compute availability
  and costs are Compute settings' sixth section, "Availability and costs": `/api/grading/health` and the navigation read's
  recorded batch costs, read when that section is opened and on Check again, never on a timer. The assignments whose scans
  wait for their scan checks are named on Collect (I2) and the Inbox. Assignments' strip links All exam records, All print
  jobs and Teaching review (the unscoped page, which left the sidebar, FRONTEND); Home's All grading and its
  several-not-started link open `#assignments?filter=grading`. Default APIs are unchanged; the overview's `waiting` entries
  gain additive `assignment_id` and `term_id`.
- An owner's own address changes on one resolved path (a grading step, Review's question and response
  written into the address) keep the shell's context, navigation and the owner's header without a new
  read; documents, gradebook and course links resolve by their query and are read again. A render whose
  view lags the address, between Assignments and a working page or between two working pages, waits for
  the address's own render.
