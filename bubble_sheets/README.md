# Bubble sheet readings

Faculty reads Gradescope bubble sheets in a retained scan for the instructor's
**own records**: which bubbles are filled, which version is marked, and whose
sheet it is. Nothing in this reader holds an answer key, scores anything,
releases or emails anything, identifies paper, or clears the Inbox review gate.
The CSV exists so the instructor can compare it with Gradescope's responses export.

Scoring is separate (2026-09-25): **Grading → an assignment → Bubble sheets** holds
each version's answer key on the term's grading plan and scores these readings
against it the way Gradescope does (`grading/bubble_keys.py`; see GRADING.md,
"Bubble sheets: answer key and scores"). It only reads what is here: a reading, a
review or an identity never changes because of a key. The same page's **Item analysis**
(2026-09-30, `grading/bubble_analysis.py`) reads the scored sheets per version to point at a
question that may be miskeyed; it too changes nothing here.

## Using it

Open a scan in **Teaching Inbox**. Below Scanned page review, **Bubble sheets**
asks for the assignment, and optionally the question count, because a bubble sheet carries no Data Matrix label and the
scan alone cannot say whose sheets these are:

1. the **assignment**: the scan's own assignment, the one it was read for before, or the one whose labels were
   scanned most recently; with none of these, nothing is chosen for you ("Choose the assignment…"), and
2. nothing about sections: a sheet takes its section from the student it is
   matched to, so the sheets are matched against every section of that course in
   its current term (the most recent term when none is current), and the result
   names the sections the identified students turned out to be in;
3. how many **questions** the sheet has, or nothing: left empty, the count is
   **inferred from the sheets**. It is the question most sheets have their last
   mark on (students answer to the end far more often than they stop early or
   leave a stray mark further down: 239 of 244 real sheets ended on the last
   question), accepted when at least 60% of the marked sheets agree. When they
   do not, nothing is guessed: the read stops and asks you for the count. An
   upload of fewer than ten marked sheets, such as a few stragglers, also counts
   the other scans of the same assignment and teaching term that remain in the
   Inbox. Deleted scans and other terms do not supply evidence. The result says "17 questions,
   inferred: the last mark on 97% of sheets". Marks past the last question are
   ignored.

**Read bubble sheets** queues the read for the Inbox worker. About a second per
twenty pages reads the marks; reading the handwritten ID and name boxes takes
about forty seconds per sixty sheets on the GPU. The result lists every sheet as
**Read**, **Needs review** or **Reviewed**, with a filter, **Download readings
(CSV)**, and **Review all**.

## Sheets are recognized on arrival

As soon as a scan is inspected, every page without a Data Matrix label is
checked: a page that resembles the template and shows at least three of the four
corner positioning marks where an undistorted Letter sheet puts them (within 3%
of true size, either way up) is a bubble sheet. The stapled corner is cut before
scanning, so three marks is the normal case, not the exception. Scanned page
review then says **Recognized as bubble sheets from their positioning marks**
instead of "Not recognized", this panel says how many pages were recognized,
and those pages are skipped by printed-label OCR. Scans that arrived earlier are
caught up by the Inbox worker.

**When the resemblance misleads** (2026-09-25). One scan's sheets, printed a
little small (97.5%), resembled the template at only 0.22 to 0.25: one fell
just below 0.22 and was called "not a bubble sheet", and another resembled the
template's second page by a hair. Where the resemblance's proposal does not
hold, every other template page is now aligned, either way up, and the page is
a sheet only where at least three trusted marks sit within 3% of true size AND
the printed rings are clearly where they say (0.08, the level that already
settles what a page is). Detection and reading use the same rule, so such a
page is also read, as page 1 and the right way up. No other page has five
hundred rings in those places, and the rings also say which of the template's
two pages it is (both share their marker positions, not their grid). The rule
is `marks-4`: every page without a label is looked at once more under it, a
few minutes of worker time. `marks-5` (2026-10-24) first scales a page within
3% of Letter to Letter (`reader.letter`): the home scanners crop each page to
the paper's edge, and marks-4 passed every such page over. What marks-4 really
looked at is carried over unchanged; only the pages it passed over for their
size are looked at again.

Recognition is only the page type (`bubble_detections`, an immutable machine
reading per page and detector rule). It never names a student, an assignment
or a section, and it reads no marks: the association below stays your explicit
choice and stays flagged until you make it. On 3,909 real pages it took about 80
seconds, found 325 of 325 sheets, and mistook none of 3,584 exam pages for one
(their best resemblance was 0.13 against the 0.22 needed to be considered). Of
those sheets, 263 had only three marks left; 2 were warped enough that their
rings did not line up, so their read waits for you.

## Reading again keeps your reviews

**Read again** makes a new reading of every page (needed for a sheet that an
earlier version could not align). The latest review of each page, made on an
earlier read of the same assignment and question count, is carried over to the
new read, with its student while that student is still on the chosen rosters.

## Scantron labels

A seating chart's export can print one more label per student for their Scantron ("Also print a Scantron label").
A sheet carrying it is identified as that student by the label, whatever the handwriting says, unless the UIN
bubbled is exactly another student's; then it waits for you with both names. Scan intake treats the labeled sheet
as an unlabeled page, so it is never mistaken for a page of the booklet.

## Review all

When sheets need review, **Review all (N)** is the main button of the scan's
Bubble sheets page (`#inbox/job/<id>/sheets`, the only place this panel is
mounted, 2026-09-26). It opens the first unresolved sheet, and **Save and next**
brings up the following one, in page order, inside the same pop-up until none is
left. The cursor starts on the first thing in doubt, so an answer can be typed
and Enter pressed. **Skip** moves on without saving; **Stop** (or Esc) quits.
Every review is saved as it is made, so stopping loses nothing, and **Review
all** starts again from the first sheet that still needs review. A sheet is shown
once per pass, so two sheets recorded for the same student cannot loop; the
closing message says how many still wait. On a scan that also has the one-label
question open, **Review all** asks that first and then carries on with the
sheets.

## What is unsure goes to you

- **Is it a bubble sheet?** A 20-DPI picture of the page is compared with the
  template. At or above 0.35 it is a sheet, below 0.22 it is another page, and
  between them it is read, and flagged only when the bubbles are also just
  faintly where the positioning marks say (ring contrast under 0.08). The
  resemblance falls with bold marks and a full name box, not with scan quality:
  seven real sheets scored 0.25 to 0.34 while aligning at 0.09 to 0.18, so clear
  alignment settles what the page is. On 1,066 real pages, sheets scored 0.52 to
  0.65 (0.40 for one scanned in a different session) and exam pages at most 0.13.
- **Alignment.** Corner markers that match well place the sheet; three suffice,
  because the stapled corner is often torn. The printed bubble rings must then
  be where the markers say, or the sheet is reported as not aligned and nothing
  on it is read. Three markers leave no redundancy, and an error of four pixels
  already puts every thin printed ring off its bubble, so a fit the rings do not
  clearly confirm is refined on the rings themselves (five hundred registration
  marks, block by block, then one fit through all of them). Two real sheets that
  plainly aligned by eye went from 0.04 and 0.02 to 0.40. A fit the rings do
  confirm is left exactly as it was. (Recognizing a page as a sheet does not need the rings: a torn
  corner can leave a real sheet slightly warped, and it still waits here.)
- **Marks.** A bubble's darkness is compared with the same letter's empty
  bubbles on that sheet. Measured on 244 real sheets, a bubble left empty never
  rose above 0.081 (99.9% of them under 0.045, dust and smudges included), light
  but deliberate marks sat at 0.10 to 0.18, and ordinary ones at 0.19 and up. So
  at **0.14 or more a bubble is filled**, at 0.06 or less it is empty, and in
  between it is **unsure**, except that **a mark alone in its row counts from
  0.10**: with every other bubble of the row empty there is nothing to compare.
  The 101,260 bubbles in rows past the last question (empty on all 244 real
  sheets) read 0.000 on average with a standard deviation of 0.008; 99.99% were
  under 0.044 and the darkest was 0.062, with nothing between that and 0.10,
  where the lone marks students really made begin (0.105). The darker "empty"
  readings quoted elsewhere (to 0.085) sit beside a filled answer, as erasure
  ghosts. Those scans had no dust on the glass, so a lone mark between 0.06 and
  0.10 still waits for you. A faint mark with nothing filled beside it always
  waits for you: it may be dust on the glass or a student's light answer, and a
  blank is never turned into an answer by a speck. Beside one filled bubble, a
  faint mark under 40% of its darkness is the ghost of an erased answer or a
  smudge and is ignored, as is ink spilling (under 0.10) into the very next
  bubble. **A confident double mark is a grading matter, not a review matter**:
  bubbles filled about equally dark are recorded as handed in, shown as (BD),
  and not flagged (the real ones had a second mark 51% to 97% as dark as the
  first). A second bubble dark enough to count alone yet under 40% of the row's
  darkest is an answer erased and is ignored too (2026-10-25: on Exam 2 such
  marks were 22% to 39% as dark, and all 9 you reviewed kept the dark answer;
  one at 41% you kept as a double). **A blank is an answer** and is never reviewed, and **marks
  past the last question are ignored**. Every reading keeps its mark strengths, so these rules also decide
  readings made before them; nothing is read again.
- **Whose sheet.** Local OCR reads the ID and name boxes (draft text, never
  trusted). The crop follows the handwriting, not just the printed box: many
  students write on the rule or below it, and a crop of the printed row alone
  sliced such writing in half, which is why several real sheets stayed
  unidentified. The rows half a field above and below are searched for the band
  of ink that belongs to the box; the crop is never shorter than the printed box
  nor taller than 1.7 of it, so the next row's writing stays out. The review
  shows the same crop the OCR was given. **UIN first:** when the digits read contain exactly one roster UIN,
  the sheet is **matched**, unless the name read clearly belongs to someone else,
  which makes it a suggestion with that conflict stated. **Then the name:** with
  no exact UIN, each student gets a confidence from how close their UIN is to the
  digits read and how close their name is to the name read (55% and 45%; 90% of
  either alone). A student at 0.60 or more who leads the next by 0.05, with a UIN
  within two digits or a name at 0.8, is **suggested**. The lead only breaks
  near-ties, and when it does the sheet says so ("Two students are almost equally
  close…"); a wider lead used to keep a student shown at 73% from being
  identified at a 70% threshold. **At the threshold you
  set, 70% unless changed, the suggestion identifies the student without
  asking**: a digit misread beside the right name, or no digits at all beside an
  exact name nobody else comes near. The setting sits under the results
  (**Identify a student automatically at … % confidence or more**, 60 to 100); it
  applies to every read at once, since identification is derived from the
  retained match, and every change is kept. The table says "identified at 94%", the
  CSV's **Identified by** column says how every student was named (exact UIN;
  digits read, name read or both, with the percentage; or instructor), and the
  student sits locked with the confident reads, where it can still be unlocked
  and changed. Below the threshold the suggestion waits for you, preselected. A
  suggestion with anything arguing against it is never upgraded.

  **One sheet per student per assignment.** A student already recorded for a
  sheet (by exact UIN, by your review, or by a confident match) is not offered
  for another sheet that still needs review, in this scan or in any other scan of
  the same assignment: the suggestion is withheld, and in the picker such
  students sit apart under **Already matched to another sheet**, still
  selectable when a match has to be corrected. When nobody was identified, the
  picker starts on the closest student still free, with the confidence beside
  the name, rather than on nobody; it is recorded only when you save. A confident match gives way to an
  exact UIN or a review of the same student; two equal claims (two exact UINs,
  say) flag both sheets for you.

  This average is the instructor's choice. A probability model that weighed each
  digit by how much it distinguishes students (three UIN positions are the same
  for everyone on the real rosters, and most students have a classmate within
  two digits) was tried on the real reads and judged worse in use; it was
  removed. When the matching rule changes, the Inbox worker matches retained
  reads again from the text already read: no page is read again and no review
  changes.
  Anything else is **unmatched**, with the nearest students listed first in the
  picker. Two sheets recorded for one student are both flagged. On 82 real
  sheets: 73 matched by exact UIN (in all 73 the name read independently ranked
  the same student first), eight suggested, one unmatched, no student claimed
  twice. Without the local OCR runtime every sheet waits for a manual match.

**Review** opens only what is in doubt. Under **Needs your review** are the
student (unless the UIN matched exactly), the version (unless exactly one bubble
was filled) and each question with a mark in doubt. A blank is an answer
like any other: when every bubble of a row is clearly empty it is recorded as
blank and never sent to review (a faint mark in the row still is). Everything read
without doubt sits under a collapsed **Confident reads** section whose fields are
locked: opening it is for comparing (each question has **show**), and only
**Unlock these to edit** lets you replace a confident value on purpose. Saving
always records every value, confident ones exactly as read. The server reports
which parts are in doubt (`current.flags`), so the screen never infers it from
wording. It also never leaves you to find the spot on the page. Whatever is in
question is shown as the piece of the scan it came from, upright and enlarged,
next to the control that answers it: the **Name and ID boxes** above the student
picker (with the OCR text quoted), the **version row** above the version, and
the **bubble row of every flagged question** under its answer box. Any other
question has a **show** link for its row, and the whole page stays alongside.
Crops are cut on request from the retained raster with the alignment recomputed
(`GET /api/bubble-sheets/sheets/<id>/parts/<id|name|version|qN>`); nothing new is
stored.
**Click the answer on the snapshot.** Each question's snapshot (and the version
row's) has a click target over every bubble: click the bubble that counts and
the answer is set; click the only chosen bubble again for a blank; Shift-click
adds or removes a second bubble when two really are filled. The outline marks
what is chosen and leaves the pencil mark visible. The field beside it still
takes typing (letters A to E; empty for a blank; two letters for a double
mark), and the two follow each other. On a confident, locked read the targets
are inactive until you unlock it. A machine reading never changes: reviews are appended, and the latest is
the sheet's current value. Identification at the threshold you set (70% unless
changed) is derived from the retained match each time it is shown, so it also
applies to reads made before the rule, and it never writes a review in your name.

## Limits

- Only **Gradescope Bubble Sheet v2020.05.01**, page 1 (questions 1 to 100), from
  the 200-DPI Letter raster that scan inspection retains. The second page is
  reported, not read. `python -m bubble_sheets.derive_template template.pdf`
  rebuilds `templates/gradescope-2020-05-01.json` from the official PDF, which is
  recognised by SHA-256 and not kept in the repository; only measurements, the
  four marker images and two thumbnails are. A changed template needs a new
  profile and its own check against real scans.
- The thresholds come from one section's real scans at 300 DPI, read at 200. A
  different scanner, paper or pencil habit may need them revisited; the unsure
  band is where that would show first.
- No comparison with Gradescope is built yet: it needs a real **Download
  Responses** file to be written against.

## Ownership

`bubble_runs` belong to the existing source Inbox message and reference the
course-wide assignment, the term and the permanent sections chosen for the read.
`bubble_sheets` reference their existing scan page. `bubble_reviews` reference a
permanent `(section_id, enrollment_id)` roster membership. All are catalog
records in the nightly verified backup; rasters are read through the verified
Inbox storage manager and roster names and UINs under the roster lifecycle lock.
Roster UINs never appear in match results sent to the browser; the CSV, the
instructor's own download, includes them.

```sh
.venv/bin/python -m unittest discover -s tests -p test_bubble_sheets.py -v
LD_LIBRARY_PATH=$PWD/.tools/browser-libs/usr/lib/x86_64-linux-gnu .venv/bin/python tests/browser_bubble_sheets.py
```
