## Certified assessments are archived (a view)

Rule ID: **CERTIFIED-ARCHIVE**. Current binding module contract. Read root `AGENTS.md` first.
ASSIGNMENT-WORKSPACE (2026-10-03): the unified list uses this same archival view; since 2026-10-09 (INDEX TABS) its tab for it is
"Certified", a current term's assignment out of it is "Pending", and its "Archived" tab means an earlier term, not this view. Scan
activity in `pair_activity` considers only PRESENT messages and their latest association,
using the later of receipt and association times; a deleted or reassigned scan cannot
make an unrelated assignment current. No record is moved or deleted by archiving.

- Requirement, 2026-09-27: "When the grades have been certified for an assignment, that should clear it out of the other
  menus/pages (eg seating charts get archived, scan records archived, etc) not deleted, but moved out of the immediate workspace".
  `certified_archive.py` is the ONE rule: an assignment of ANY kind (quiz, homework, worksheet, exam; instructor: "it should apply
  to any assignment, not just exams (of course, certain things only apply to exams)") is archived, in one term, while its term
  grading plan has a certification (`frq_certifications`, latest counts; catalog only, no lock; every plan has its term
  `exam_events` record, whatever the kind). ARCHIVED IS A VIEW: no stored status, nothing moved, changed or deleted, every record
  still opens by its link. Each list folds the assignment's rows into its own closed "Archived ·
  grades certified" section: Inbox (an Archived tab holding FINISHED scans whose latest association is that plan; History no longer
  lists them; `status=completed` still does), Seating charts, Assignments (a card when EVERY current term of the
  assignment is certified; print jobs under the Status filter "Archived"), Exams, Grading (its index, retired on 2026-10-18,
  ASSIGNMENT-WORKSPACE; only while `workflow.certification` is
  `current` and no batch runs: a changed certification comes back, since it must be certified again) and Response Cards issues.
  CERTIFIED GRADES RESOLVE A FLAGGED CHART OR CARD (instructor, the same day: "a flagged seating chart is considered resolved if
  the grades are certified, the record should not be changed, ie the statuses or anything else, but it no longer needs my
  attention by that point. same for cards"): a certified assessment's seating charts and Response Card issues fold into Archived
  flagged or not, and count in no attention card, sidebar badge, tab-title count or "needs attention" line
  (`certified_archive.uncertified` in `workspace_overview.card_attention`/`seat_update_attention` and `ResponseCardService.attention`;
  `needsAttention` in seating-state.js). PRINT JOBS the same (instructor, the same day: "same rule for print jobs"): a certified
  assessment's jobs, failed ones included, sit only under the Status filter "Archived", leave the page's attention line, and a
  failed one is no workspace-health failure (`queue_status` "printing" counts it only while the worker still has it). Their
  records, checks, statuses, flags and pages are unchanged. Still in place whatever the certification: scans in Current.
  NEW WORK STAYS OUT (instructor, the same day, for a late makeup after certifying): a row made AFTER the latest certification (a
  scan received or associated, a print job, a seating chart, a card issue, a seat update) is neither archived nor dropped from
  attention (`certified_archive.facts`/`covered`, `uncertified(..., created)`, `pair_activity` for an Assignments card; an exam's
  seating group archives only when every chart does) until a newer certification covers it. A certification of just one changed
  grade (`amends_id`, a regrade request's Apply; REGRADES, 2026-10-07) covers nothing new: the cutoff stays the newest ordinary
  certification's (`certified`, `uncertified`). So does a certification Faculty made of late papers while other changes still
  wait for the instructor (`amends_id` set, GRADING "LATE PAPERS CERTIFY THEMSELVES"); one that leaves the plan current is
  ordinary and covers what existed then. An OPEN Gradescope regrade request keeps its assignment in view on Assignments
  until it is answered or handled (it is work after certification, on Return › Regrade requests). A makeup version waiting to be approved does the same until it is approved or scrapped (ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED"). Grading and Exams already come back
  because the certification is `changed`. Home already drops certified plans; the Calendar, the Gradebook and
  the exam hub are not filtered. The course pages' work list (`courses.js workRows`, course Home and section tabs) leaves a chart
  or card issue with its `archived` fact out of its row and links "N archived" to the Seating charts or Response Cards page (its
  Exams and Grading rows come from reads without that fact and are unchanged). A new list of assessment-scoped rows should use
  this module the same way.
