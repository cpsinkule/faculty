// Makeups owed on the Absences page (static/absence-makeups.js, ABSENCES "MAKEUPS"): rows grouped by assignment, newest given first,
// owed students before undecided ones; the Makeup column names a scheduled sitting, else what the Exams page records, else nothing.
import assert from "node:assert/strict";
import { actionsMarkup, bookingMarkup, bookingSwitchMarkup, cardButton, cellMarkup, holdButton, makeupGroups, makeupStatus, makeupsMarkup, missedButton, placedNote, printButton, versionAction, versionMarkup, workMarkup } from "../static/absence-makeups.js";
import { approveSummary, makeupProgress, openDraft, sittingText, studentStatus } from "../static/makeup-drafts.js";
import { viewableRoute } from "../static/file-viewer.js";

const escape = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const row = (over) => ({ event_id: "e1", title: "Quiz 3", plan_id: "p1", given_date: "2026-09-17", given_day: "Thu, Sep 17", state: "owed",
  state_label: "Makeup owed", name: "B", section: 507, dates: "Thu, Sep 17", status_label: "Excused", absence_id: "a1", makeup_versions: [], ...over });

const groups = makeupGroups([row({ name: "Zed", state: "if_excused", state_label: "Makeup owed if excused" }), row({ name: "Amy" }),
  row({ event_id: "e2", title: "Exam 1", given_date: "2026-09-24", given_day: "Thu, Sep 24", plan_id: null })]);
assert.deepEqual(groups.map((g) => g.title), ["Exam 1", "Quiz 3"]);  // newest given first
assert.deepEqual(groups[1].rows.map((r) => r.name), ["Amy", "Zed"]);  // owed before undecided

assert.equal(makeupStatus(row({ scheduled: { day: "Fri, Sep 25", time: "10:00", location: "BLOC 164", label: "Makeup" } })), "Makeup sitting Fri, Sep 25 · 10:00 AM · BLOC 164");
assert.equal(makeupStatus(row({ paper: "excused" })), "Marked excused on the Exams page");
// KEEP THEIR SITTING (2026-10-06): Schedule makeup says where a student already on a makeup sitting is, and nothing for one on none.
const onSitting = row({ scheduled: { sitting_id: "s1", day: "Fri, Sep 25", time: "14:00", location: "BLOC 164", label: "Makeup" } });
assert.equal(placedNote(onSitting, "s1"), "· already on this sitting");
assert.equal(placedNote(onSitting, "s2"), "· on Fri, Sep 25 · 2:00 PM · BLOC 164; tick to move them");
assert.equal(placedNote(onSitting, ""), "· on Fri, Sep 25 · 2:00 PM · BLOC 164; tick to move them");  // a new sitting
assert.equal(placedNote(row({}), "s1"), "");
assert.equal(makeupStatus(row({ paper: "no_scan" })), "");

const markup = makeupsMarkup([row({ name: "<b>Amy</b>" }), row({ event_id: "e2", title: "Exam 1", plan_id: null })], escape, () => "EMPTY");
assert.match(markup, /2 makeups owed/);
assert.match(markup, /&lt;b&gt;Amy&lt;\/b&gt;/);  // names are text
assert.equal((markup.match(/data-makeup-draft=/g) || []).length, 1);  // only a plan with grading drafts a makeup version
assert.equal(makeupsMarkup([], escape, () => "EMPTY"), "EMPTY");
// SCHEDULED WITHOUT AN ABSENCE (2026-10-19): counted apart, the name is text (no absence to open), the chip says scheduled.
const noAbsence = makeupsMarkup([row({}), row({ name: "Milad", absence_id: "", state: "scheduled", state_label: "Makeup scheduled", dates: "",
  status_label: "No absence", scheduled: { sitting_id: "s1", day: "Mon, Oct 19", time: "15:00", location: "BLOC 309", label: "Replacement" } })], escape, () => "EMPTY");
assert.match(noAbsence, /1 makeup owed · 1 scheduled without an absence</);
assert.match(noAbsence, /<td data-label="Student">Milad<\/td>/);
assert.match(noAbsence, /No absence[\s\S]*Makeup scheduled<\/span><small>Makeup sitting Mon, Oct 19 · 3:00 PM · BLOC 309/);
assert.equal(workMarkup([], escape), "");
assert.match(workMarkup([row({})], escape), /Work given during this absence[\s\S]*Quiz 3[\s\S]*given Thu, Sep 17[\s\S]*Makeup owed/);

// Quiz makeups booked by card (ABSENCES "MAKEUP BOOKING"): the switch, the version line, and each row's booking.
const switches = bookingSwitchMarkup({ t1: { cards: false, revision: 0, code: "MATH 140", term_label: "Fall 2026" } }, escape);
assert.match(switches, /Quiz makeups are booked by hand[\s\S]*MATH 140 · Fall 2026[\s\S]*data-makeup-cards-to="on"[\s\S]*Turn on…/);
assert.match(bookingSwitchMarkup({ t1: { cards: true, revision: 2 } }, escape), /book themselves[\s\S]*data-makeup-revision="2"[\s\S]*Turn off/);
assert.match(versionMarkup({ state: "ready", letter: "C" }, escape), /data-tone="warning">Makeup version C is ready for you to approve/);
assert.match(versionMarkup({ state: "approved", letter: "C", rubrics: "to_finalize" }, escape), /finalize its rubrics/);
assert.equal(versionMarkup({ state: "approved", letter: "C", rubrics: "final" }, escape), "");
assert.match(versionMarkup({ state: "none", problem: "No drafting model" }, escape), /Could not draft a makeup version: No drafting model/);
const booked = (over) => row({ section_id: "s1", enrollment_id: "1000000001", booking: { state: "sent", label: "Card sent Sat, Oct 4", flag: false, due_day: "Sun, Nov 2", ...over } });
assert.match(bookingMarkup(booked({}), escape), /data-tone="neutral">Card sent<\/span><small>Card sent Sat, Oct 4<\/small><small>Due by Sun, Nov 2<\/small>$/);
const other = bookingMarkup(booked({ state: "other_time", label: "Needs another time", flag: true, pick: { choice: "Only <Wednesdays>" } }), escape);
assert.match(other, /data-tone="warning">Needs another time[\s\S]*“Only &lt;Wednesdays&gt;”<\/small><small>Due by Sun, Nov 2<\/small>$/);
// ONE ROW, ONE STEP (requirement, 2026-11-12): the Makeup column has nothing to press; the row's
// actions are a column of their own, at most one framed (the step that moves the makeup on), the rest text actions under it.
assert.doesNotMatch(other, /<button|class="button"/);
const otherRow = booked({ state: "other_time", label: "Needs another time", flag: true });
assert.match(actionsMarkup(otherRow, escape), /^<div class="makeup-actions"><button type="button" class="button" data-makeup-card="e1" data-makeup-section="s1" data-makeup-enrollment="1000000001" data-makeup-absence="a1">Send a new card<\/button><span class="makeup-actions-more"><button type="button" class="text-button" data-missed-event="e1"[^>]*>Missed makeup…<\/button><\/span><\/div>$/);
assert.equal((actionsMarkup({ ...otherRow, hold: { held: false, revision: 0 } }, escape).match(/class="button"/g) || []).length, 1);
assert.equal(actionsMarkup(row({ state: "if_excused", state_label: "Makeup owed if excused" }), escape), "");  // nothing to press: no empty box
assert.equal(cardButton(booked({}), escape), "");  // a card just sent is not renewed
// TAKEN UP IN FOLLOW-UP (requirement, 2026-11-12): once you took its follow-up
// up, the row reads In Follow-up with where it stands there, unflagged; their words and Send a new card stay.
const taken = bookingMarkup(booked({ state: "other_time", label: "Needs another time", flag: false, pick: { choice: "Only <Wednesdays>" },
  followup: { id: "f1", state: "waiting", label: "Waiting on student since Thu, Nov 12" } }), escape);
assert.match(taken, /data-tone="neutral">In Follow-up<\/span><small>Waiting on student since Thu, Nov 12<\/small><small>“Only &lt;Wednesdays&gt;”<\/small><small>Due by Sun, Nov 2<\/small>$/);
assert.match(cardButton(booked({ state: "other_time", flag: false, followup: { id: "f1" } }), escape), /Send a new card/);
assert.doesNotMatch(taken, /Needs another time|data-tone="warning"/);
assert.match(bookingMarkup(booked({ state: "booked", label: "Booked: Tue, Oct 7 · 2:30 pm · BLOC 319", copy: { batch_id: "b" } }), escape),
  /data-tone="success">Booked<\/span><small class="makeup-when">Tue, Oct 7 · 2:30 pm · BLOC 319<\/small><small><a href="#documents\?batch=b">Their copy is ready to print<\/a><\/small>$/);  // the chip says Booked once
assert.doesNotMatch(bookingMarkup(booked({ state: "booked", label: "Booked: x" }), escape), /Due by/);
assert.equal(cardButton(booked({ state: "booked", label: "Booked: x" }), escape), "");
// COPIES (2026-10-12): the print job is made as soon as the student books, so the row links to it and says where it stands; while
// no makeup version is approved, it says the copy waits for one.
const seated = (over) => booked({ state: "booked", label: "Booked: x", ...over });
// Print PDF (same day: "have a button appear next to the line item here that brings up pdf to print it straight from this menu"):
// once the copy is ready, the job's PDF opens here in the app's viewer; a job with no file to open offers none. It is the row's
// framed step while the copy still waits to be printed, and a text action once it printed at home or is on its way there.
assert.match(bookingMarkup(seated({ copy: { batch_id: "b/1", status: "ready" } }), escape), /<small><a href="#documents\?batch=b%2F1">Their copy is ready to print<\/a><\/small>$/);
assert.match(printButton(seated({ copy: { batch_id: "b/1", status: "ready" } }), escape),
  /^<a class="button" data-makeup-print href="\/api\/documents\/batches\/b%2F1\/download" data-view-file="\/api\/documents\/batches\/b%2F1\/download" data-view-name="Quiz 3 makeup copy · B">Print PDF<\/a>$/);
assert.match(printButton(seated({ copy: { batch_id: "b", status: "ready", printed: "accepted" } }), escape), /^<a class="text-button" data-makeup-print /);
assert.match(printButton(seated({ copy: { batch_id: "b", status: "ready", printed: "sending" } }), escape), /^<a class="text-button" /);
for (const printed of ["uncertain", "failed"]) assert.match(printButton(seated({ copy: { batch_id: "b", status: "ready", printed } }), escape), /^<a class="button" /);
for (const status of ["pending", "failed", "superseded"]) assert.equal(printButton(seated({ copy: { batch_id: "b", status } }), escape), "");
assert.equal(printButton(booked({ copy: { batch_id: "b", status: "ready" } }), escape), "");  // not booked: no copy to print
assert.match(actionsMarkup(seated({ copy: { batch_id: "b", status: "ready", printed: "accepted" } }), escape),
  /^<div class="makeup-actions"><span class="makeup-actions-more"><a class="text-button" data-makeup-print [^>]*>Print PDF<\/a><button type="button" class="text-button" data-missed-event[^>]*>Missed makeup…<\/button><\/span><\/div>$/);
assert.ok(viewableRoute("/api/documents/batches/b%2F1/download"));
// AUTOMATIC MAKEUP PRINTS (2026-10-18): a ready copy goes to the home printer by itself; the line says how that went.
assert.match(bookingMarkup(seated({ copy: { batch_id: "b", status: "ready", printed: "accepted" } }), escape), />Their copy was printed at home<\/a><\/small>$/);
assert.match(bookingMarkup(seated({ copy: { batch_id: "b", status: "ready", printed: "uncertain" } }), escape), />Their copy may have printed: check the printer</);
assert.match(bookingMarkup(seated({ copy: { batch_id: "b", status: "pending" } }), escape), /href="#documents\?batch=b">Their copy is being made<\/a><\/small>$/);
assert.match(bookingMarkup(seated({ copy: { batch_id: "b", status: "failed" } }), escape), /href="#documents\?batch=b">Their copy could not be made<\/a><\/small>$/);
assert.match(bookingMarkup(seated({ copy: { batch_id: "b", status: "superseded" } }), escape), /href="#documents\?batch=b">Their copy’s print job was superseded<\/a><\/small>$/);
// MISSED (2026-10-31): "Missed makeup…" follows the row's other actions, on owed and scheduled rows only.
assert.match(actionsMarkup({ ...seated({ copy: { batch_id: "b", status: "ready" } }), name: "<Amy>" }, escape),
  /data-view-name="Quiz 3 makeup copy · &lt;Amy&gt;">Print PDF<\/a><span class="makeup-actions-more"><button type="button" class="text-button" data-missed-event="e1" data-missed-section="[^"]*" data-missed-enrollment="[^"]*">Missed makeup…<\/button><\/span><\/div>$/);
assert.match(missedButton(row({ section_id: "s1", enrollment_id: "1" }), escape), /^<button type="button" class="text-button" data-missed-event="e1" data-missed-section="s1" data-missed-enrollment="1">Missed makeup…<\/button>$/);
assert.match(missedButton(row({ state: "scheduled" }), escape), /Missed makeup…/);
assert.equal(missedButton(row({ state: "if_excused" }), escape), "");  // decide the absence first
assert.match(workMarkup([row({ state: "missed", state_label: "Missed makeup: 0", section_id: "s1", enrollment_id: "1" })], escape),
  /Missed makeup: 0<\/span> <button type="button" class="text-button" data-unmiss-event="e1" data-unmiss-section="s1" data-unmiss-enrollment="1">Undo<\/button>/);
assert.match(bookingMarkup(seated({}), escape, { state: "ready", letter: "E" }), /<small>Their copy is made once the makeup version is approved<\/small>$/);
assert.doesNotMatch(bookingMarkup(seated({}), escape, { state: "approved", letter: "E" }), /copy/);  // about to be made: nothing to wait for
assert.doesNotMatch(bookingMarkup(seated({}), escape), /copy/);
assert.doesNotMatch(bookingMarkup(booked({ copy: { batch_id: "b", status: "ready" } }), escape, { state: "ready" }), /copy/);  // not booked: no copy line
assert.match(makeupsMarkup([seated({})], escape, () => "EMPTY", { terms: {}, versions: { p1: { state: "drafting", letter: "E" } } }),
  /Booked<\/span><small class="makeup-when">x<\/small><small>Their copy is made once the makeup version is approved<\/small><\/td>\s*<td data-label="Actions" class="makeup-actions-cell"><div class="makeup-actions">/);
assert.match(bookingSwitchMarkup({ t1: { cards: true, revision: 1 } }, escape), /a print job with their copy is made at once, or as soon as you approve the makeup version\./);
// SCHEDULING "GOOGLE CHECKED": a card waiting for Google says why; it is no flag and has nothing to press.
const waits = bookingMarkup(booked({ state: "waiting", label: "Faculty has not read your Google Calendar since Mon, Oct 5 at 3:05 PM" }), escape);
assert.match(waits, /data-tone="neutral">Card waits<\/span><small>Faculty has not read your Google Calendar since Mon, Oct 5 at 3:05 PM<\/small>/);
assert.doesNotMatch(waits, /button/);
assert.match(cellMarkup(booked({ state: "manual", label: "" }), escape), /^<span class="chip" data-tone="warning">Makeup owed<\/span>$/);
assert.match(actionsMarkup(booked({ state: "manual", label: "" }), escape), /class="button" data-makeup-card="e1"[^>]*>Send booking card<\/button>/);
assert.match(cellMarkup(row({ booking: { state: "scheduled" }, scheduled: { day: "Fri, Sep 25", label: "Makeup" } }), escape), /Makeup owed[\s\S]*Makeup sitting Fri, Sep 25/);
assert.doesNotMatch(cellMarkup(row({}), escape) + actionsMarkup(row({}), escape), /Send/);  // an exam (no booking) reads as before
// MAKEUP HOLDS (requirement, 2026-10-06): Hold… on a quiz row until the student is on a sitting; a held row says what it waits on,
// with Release (and "Release and send card" while the term's cards are on) and Change…; never on an exam row.
assert.doesNotMatch(actionsMarkup(row({}), escape), /Hold…/);
const free = { held: false, revision: 2 };
assert.match(actionsMarkup({ ...booked({ cards: true }), hold: { held: false, revision: 0 } }, escape), /^<div class="makeup-actions"><span class="makeup-actions-more"><button type="button" class="text-button" data-makeup-hold="a1" data-makeup-hold-revision="0">Hold…<\/button><button[^>]*data-missed-event="e1"[^>]*>Missed makeup…<\/button><\/span><\/div>$/);
assert.match(actionsMarkup({ ...booked({ state: "manual", label: "" }), hold: free }, escape), /Send booking card[\s\S]*data-makeup-hold-revision="2">Hold…/);
assert.equal(holdButton({ ...booked({ state: "booked", label: "Booked: x" }), hold: free }, escape), "");
assert.equal(holdButton({ ...row({ scheduled: { day: "Fri, Sep 25", label: "Makeup" } }), hold: free }, escape), "");
const reason = { held: true, revision: 3, reason: "her <concussion> reassessment", check_back: "2026-10-07", check_back_day: "Wed, Oct 7" };
const heldRow = { ...booked({ state: "held", cards: true }), hold: reason };
assert.match(cellMarkup(heldRow, escape), /data-tone="neutral">Held<\/span><small>Waiting on her &lt;concussion&gt; reassessment<\/small><small>Check back Wed, Oct 7<\/small><small>Due by Sun, Nov 2<\/small>$/);
assert.match(actionsMarkup(heldRow, escape), /class="button" data-makeup-release="a1" data-makeup-hold-revision="3" data-makeup-release-sends="1">Release and send card<\/button><span class="makeup-actions-more"><button type="button" class="text-button" data-makeup-hold="a1" data-makeup-hold-revision="3">Change…<\/button><button[^>]*>Missed makeup…<\/button>/);
const checkBack = { ...booked({ state: "check_back", cards: false }), hold: reason };
assert.match(cellMarkup(checkBack, escape), /data-tone="warning">Check back<\/span>[\s\S]*Check back was Wed, Oct 7/);
assert.match(actionsMarkup(checkBack, escape), /data-makeup-release-sends="">Release</);
const undecided = row({ state: "if_excused", state_label: "Makeup owed if excused", hold: { held: true, revision: 1, reason: "" } });
assert.match(cellMarkup(undecided, escape), /Held<\/span><small>Until you release it<\/small><small>Makeup owed if excused<\/small>$/);
assert.match(actionsMarkup(undecided, escape), />Release<\/button><span class="makeup-actions-more"><button[^>]*>Change…<\/button><\/span><\/div>$/);
const withBooking = makeupsMarkup([booked({})], escape, () => "EMPTY", { terms: {}, versions: { p1: { state: "ready", letter: "C", draft_id: "d1" } } });
assert.match(withBooking, /ready for you to approve[\s\S]*Open makeup version…/);
// An approved makeup version is in the assignment's pipeline (2026-10-06): its way leads there, never to the window drafting the next one.
const approved = (rubrics) => makeupsMarkup([booked({})], escape, () => "EMPTY", { terms: {}, versions: { p1: { state: "approved", letter: "E", draft_id: "d1", rubrics } } });
assert.match(approved("final"), /<a class="button" href="#grading\/p1">Open makeup version →<\/a>/);
// ONE GRID (2026-11-12): every group's table has the same five columns, and the group's head leads to Exams by a go-link, so its
// framed actions are the two that act on the makeup.
assert.match(approved("final"), /<a class="link-arrow" href="#exams\/e1">Open on Exams<\/a>\s*<button type="button" class="button" data-makeup-schedule="e1">Schedule makeup…<\/button>/);
assert.equal((markup.match(/<colgroup><col class="makeup-col-student"><col class="makeup-col-section"><col class="makeup-col-absence"><col class="makeup-col-makeup"><col class="makeup-col-actions"><\/colgroup>/g) || []).length, 2);
assert.equal((markup.match(/<th scope="col"/g) || []).length, 10);
assert.match(makeupsMarkup([row({ makeup_versions: [{ letter: "E" }] })], escape, () => "EMPTY"), /<small>given Thu, Sep 17 · makeup version E ready<\/small>/);
assert.doesNotMatch(approved("final"), /data-makeup-draft=|Finalize rubrics/);
assert.match(approved("to_finalize"), /href="#grading\/p1\?tab=answer-key&amp;open=quality">Finalize rubrics<\/a>/);
assert.doesNotMatch(approved("to_finalize"), /data-makeup-draft=|Open makeup version/);
assert.match(versionAction({ plan_id: "p1", title: "Quiz 4" }, undefined, escape), /data-makeup-draft="p1"[^>]*>Draft makeup version…/);
assert.match(makeupsMarkup([], escape, () => "EMPTY", { terms: { t1: { cards: false, revision: 0 } } }), /Turn on…[\s\S]*EMPTY$/);

assert.equal(openDraft([{ id: "a", state: "approved" }, { id: "b", state: "scrapped" }, { id: "c", state: "ready" }]).id, "c");
assert.equal(openDraft([{ id: "a", state: "approved" }]), null);
assert.match(makeupProgress({ progress: { written: true, checked: true, rubrics: 1, problems: 3 } }), /1 of 3 problems/);

console.log("absence makeups frontend checks passed");

// The makeup draft window says what approving does as its form stands (2026-10-06): the version, the copies, where the ticked students
// go, and what still waits; a sitting's date reads as the campus date.
assert.equal(sittingText({ label: "Quiz 4 makeup", date: "2026-10-06", time: "After lecture", location: "BLOC 319" }), "Quiz 4 makeup · Tue, Oct 6 · After lecture · BLOC 319");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 2, sitting: { label: "Quiz 4 makeup", date: "2026-10-06" }, students: 2 }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints 2 unnamed copies, and places 2 students on Quiz 4 makeup · Tue, Oct 6. Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 1, sitting: { new: true, date: "" }, students: 1 }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints 1 unnamed copy, and places 1 student on a new makeup sitting. Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 3, sitting: { label: "Makeup", date: "2026-10-09" }, students: 0 }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints 3 unnamed copies, for Fri, Oct 9. Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 0, sitting: null, students: 0, booked: true }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints no copy now; students book their own time on their card, and each booked student gets a copy. Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 0, sitting: null, students: 0 }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints nothing. Finalize its rubrics in Key & Rubric Finalization before grading.");
// KEEP THEIR SITTING (2026-10-06): ticking a student who is on another makeup sitting says that approving moves them.
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 2, sitting: { label: "Makeup", date: "2026-10-06" }, students: 2, moving: 1 }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints 2 unnamed copies, and places 2 students on Tue, Oct 6, moving 1 off another makeup sitting. Finalize its rubrics in Key & Rubric Finalization before grading.");
// ANSWER KEYS (2026-10-11, "Carry the key through"): the summary says how many keys approving saves, the ones the window shows.
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 2, sitting: { label: "Makeup", date: "2026-10-06" }, students: 2, keys: { read: 4, missing: 0 } }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints 2 unnamed copies, and places 2 students on Tue, Oct 6. It saves the 4 answer keys shown. Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 0, sitting: null, students: 0, keys: { read: 1, missing: 2 } }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints nothing. It saves the answer key shown (2 could not be read). Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.equal(approveSummary({ letter: "E", title: "Quiz 4", copies: 0, sitting: null, students: 0, keys: { read: 0, missing: 3 } }),
  "Approving adds Version E to Quiz 4 in the same grading plan, prints nothing. No answer key could be read, so none is saved. Finalize its rubrics in Key & Rubric Finalization before grading.");
assert.match(makeupProgress({ progress: { written: true, checked: true, rubrics: 3, problems: 3, keys: 1 } }), /Reading each problem’s answer key/);
// Each sitting said once (2026-11-01): a booking card's label already carries its day and time, and a bare "Makeup" is not repeated.
assert.equal(sittingText({ label: "Makeup · Mon, Nov 2 · 3:00–5:00 pm", date: "2026-11-02", time: "15:00", location: "BLOC 309" }), "Mon, Nov 2 · 3:00–5:00 pm · BLOC 309");
assert.equal(sittingText({ label: "Makeup", date: "2026-11-03", time: "13:30", location: "BLOC 319" }), "Tue, Nov 3 · 1:30 PM · BLOC 319");
assert.equal(sittingText({ label: "Makeup" }), "Makeup");
// A student's row says one short thing: where they sit, their booking card, or that their absence waits for a decision.
const bookedRow = { state: "owed", booking: { state: "booked", label: "Booked: Mon, Nov 2 · 3:00 pm · BLOC 309" },
  scheduled: { sitting_id: "s1", label: "Makeup · Mon, Nov 2 · 3:00–5:00 pm", date: "2026-11-02", day: "Mon, Nov 2", time: "15:00", location: "BLOC 309" } };
assert.equal(studentStatus(bookedRow, "", false), "Booked Mon, Nov 2 · 3:00–5:00 pm · BLOC 309");
assert.equal(studentStatus(bookedRow, "s1", true), "Already on this sitting");
assert.equal(studentStatus(bookedRow, "s2", false), "Booked Mon, Nov 2 · 3:00–5:00 pm · BLOC 309");
assert.equal(studentStatus(bookedRow, "s2", true), "Moves here from Mon, Nov 2 · 3:00–5:00 pm · BLOC 309");
assert.equal(studentStatus(bookedRow, "new", true), "Moves to the new sitting from Mon, Nov 2 · 3:00–5:00 pm · BLOC 309");
assert.equal(studentStatus({ state: "owed", booking: { state: "sent", label: "Card sent Fri, Oct 30" } }, "", false), "Card sent Fri, Oct 30");
assert.equal(studentStatus({ state: "if_excused", state_label: "Makeup owed if excused" }, "", false), "Absence not decided yet");
assert.equal(studentStatus({ state: "owed", state_label: "Makeup owed" }, "s1", true), "Makeup owed");
assert.equal(studentStatus({ state: "owed", hold: { held: true, reason: "a doctor's note" }, booking: { state: "held", label: "a doctor's note" } }, "", false), "Held: waiting on a doctor's note");
console.log("makeup draft window checks passed");
