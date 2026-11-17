// Totals by student on the Absences page (static/absence-totals.js, ABSENCES "TOTAL ABSENCES" › ON THE ABSENCES PAGE): one group a
// section; A TABLE, NOT SENTENCES (requirement, 2026-11-12): the words once in the column headings, a
// row a name, its count, its bar of one block a day and its figures, and the students with no absence folded under their section.
import assert from "node:assert/strict";
import { barMarkup, daysMarkup, totalsMarkup } from "../static/absence-totals.js";

const escape = (value) => String(value ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const deps = { e: escape, emptyMarkup: (options) => `EMPTY:${options.title}` };
const day = (over) => ({ day: "Wed, Sep 2", state: "unrecorded", state_label: "No absence recorded", sources: [{ label: "iClicker 0 · Class 1 - Poll" }], ...over });
const student = (over) => ({ name: "Example, Alice", dropped: false, total: 0, excused: 0, not_excused: 0, pending: 0, days: [], ahead: [], ...over });
const section = (number, students) => ({ section_id: `s${number}`, section: number, term_id: "t1", code: "MATH 140", term_label: "Fall 2026", students });
const amy = student({ name: "<Amy>", total: 3, not_excused: 2, excused: 1, days: [day({}), day({ day: "Fri, Sep 4" }), day({ day: "Mon, Sep 7", state: "excused", state_label: "Excused" })] });
const totals = { sections: [section(507, [amy, student({ name: "Bo", total: 1, not_excused: 1, days: [day({})], dropped: true }), student({ name: "Cy" }),
  student({ name: "Di", ahead: [{ day: "Fri, Nov 20" }] })]), section(508, [student({ name: "Eli" })])] };

// A bar is one block a day, what counts against the student first, to the page's one scale; no inline style (the page's policy).
const bar = barMarkup(amy, 12);
assert.match(bar, /^<svg class="absence-totals-bar" viewBox="0 0 12 1" preserveAspectRatio="none" aria-hidden="true" focusable="false">/);
assert.deepEqual([...bar.matchAll(/<rect class="absence-bar-(\w+)" x="([\d.]+)"/g)].map((m) => `${m[1]}@${m[2]}`), ["not_excused@0.07", "not_excused@1.07", "excused@2.07"]);
assert.match(barMarkup(student({ total: 4, pending: 1, not_excused: 3 }), 2), /viewBox="0 0 4 1"[\s\S]*not_excused[\s\S]*absence-bar-pending" x="3.07"/);  // never wider than its box
assert.doesNotMatch(bar, /style=/);

const markup = totalsMarkup(totals, { course: "", search: "" }, deps);
assert.doesNotMatch(markup, /style=/);
// The words are said once, in each section's headings (hidden from a screen reader, which hears each figure with its words).
assert.equal((markup.match(/<div class="absence-totals-columns" aria-hidden="true"><span>Student<\/span><span>Absences<\/span><span><\/span><span data-state="not_excused">Not excused<\/span><span data-state="excused">Excused<\/span><\/div>/g) || []).length, 1);
assert.doesNotMatch(markup, /has-waiting|Waiting/);  // no day waits for a decision: no Waiting column
assert.match(markup, /<h3>Section 507<\/h3>\s*<small>2 of 4 students missed a class · 4 days missed<\/small>/);
// A row: the name as text, the count, the bar at the scale of the most days shown (never under 10), then each figure.
assert.match(markup, /<summary><span class="absence-totals-name">&lt;Amy&gt;<\/span>\s*<span class="absence-totals-count">3<span class="sr-only"> absences<\/span><\/span><svg class="absence-totals-bar" viewBox="0 0 10 1"[\s\S]*?<\/svg><span class="absence-totals-figure" data-state="not_excused">2<span class="sr-only"> not excused<\/span><\/span><span class="absence-totals-figure" data-state="excused">1<span class="sr-only"> excused<\/span><\/span><\/summary>/);
assert.match(markup, /Bo <small>Dropped<\/small><\/span>\s*<span class="absence-totals-count">1<span class="sr-only"> absence<\/span>[\s\S]*?<span class="absence-totals-figure" data-zero aria-hidden="true">–<\/span><\/summary>/);
// It opens to the days missed, as the student's card lists them.
assert.match(markup, /<div class="absence-totals-days"><ul class="absence-total-list"><li><span class="absence-total-day">Wed, Sep 2<\/span> <span class="chip" data-tone="neutral">No absence recorded<\/span>\s*<small>iClicker 0 · Class 1 - Poll<\/small><\/li>/);
assert.match(daysMarkup(amy, escape), /data-tone="success">Excused/);
assert.match(daysMarkup(student({ ahead: [{ day: "Mon, Nov 2" }] }), escape), /^<p class="field-hint">1 more day recorded ahead, not counted yet: Mon, Nov 2\.<\/p>$/);
// The students with no absence fold under their section, closed, with the days recorded ahead; a section of them alone has no table.
assert.match(markup, /<details class="disclosure absence-totals-rest"><summary><span>No absences<\/span><span class="disclosure-hint">2 students<\/span><\/summary>\s*<div class="disclosure-body"><ul class="absence-totals-none"><li>Cy<\/li><li>Di<small class="absence-totals-ahead">1 more day recorded ahead, not counted yet: Fri, Nov 20\.<\/small><\/li><\/ul>/);
assert.match(markup, /<h3>Section 508<\/h3>\s*<small>0 of 1 student missed a class · 0 days missed<\/small><\/div>\s*<details class="disclosure absence-totals-rest">/);
// Find a student narrows it and opens the fold, so a student searched for is never hidden; a section with nobody left is gone.
const found = totalsMarkup(totals, { course: "", search: "cy 507" }, deps);
assert.match(found, /<details class="disclosure absence-totals-rest" open>[\s\S]*<li>Cy<\/li><\/ul>/);
assert.doesNotMatch(found, /Section 508|absence-totals-columns|Amy/);
assert.equal(totalsMarkup(totals, { course: "", search: "nobody" }, deps), "EMPTY:No students match");
assert.equal(totalsMarkup({ sections: [] }, { course: "", search: "" }, deps), "EMPTY:No students yet");
assert.equal(totalsMarkup(totals, { course: "MATH 151", search: "" }, deps), "EMPTY:No students yet");
// THE TOP FIVE (requirement, 2026-11-12): a section shows its five students with the most days
// missed; the others who missed a class fold under them, closed, above the fold of those with none; five or fewer have no fold.
const many = { sections: [section(507, [9, 7, 7, 4, 3, 3, 2, 1].map((total, i) => student({ name: `S${i}`, total, not_excused: total, days: [] })).concat(student({ name: "None" })))] };
const capped = totalsMarkup(many, { course: "", search: "" }, deps);
const [shown, folded] = capped.split('<details class="disclosure absence-totals-more">');
assert.deepEqual([...shown.matchAll(/absence-totals-name">(S\d)/g)].map((m) => m[1]), ["S0", "S1", "S2", "S3", "S4"]);
assert.match(folded, /^<summary><span>3 more students<\/span><span class="disclosure-hint">3 absences or fewer<\/span><\/summary>\s*<div class="disclosure-body"><ul class="absence-totals-list"><li><details class="absence-totals-student">/);
assert.deepEqual([...folded.matchAll(/absence-totals-name">(S\d)/g)].map((m) => m[1]), ["S5", "S6", "S7"]);
assert.match(folded, /<\/ul><\/div><\/details>\s*<details class="disclosure absence-totals-rest"><summary><span>No absences<\/span>/);
assert.match(capped, /8 of 9 students missed a class · 36 days missed/);  // the section's line still counts everyone
assert.match(capped, /viewBox="0 0 10 1"/);
assert.doesNotMatch(markup, /absence-totals-more/);  // two students who missed a class: nothing to fold
const six = { sections: [section(507, [5, 4, 3, 2, 1, 1].map((total, i) => student({ name: `S${i}`, total, not_excused: total })))] };
assert.match(totalsMarkup(six, { course: "", search: "" }, deps), /<span>1 more student<\/span><span class="disclosure-hint">1 absence each<\/span>/);
// Find a student shows every match as a row: nobody searched for is behind a fold.
const searched = totalsMarkup(many, { course: "", search: "s" }, deps);
assert.doesNotMatch(searched, /absence-totals-more/);
assert.equal((searched.match(/class="absence-totals-student"/g) || []).length, 8);
// The Waiting column, and its orange figure, appear while a shown student has a day waiting for a decision.
const waiting = totalsMarkup({ sections: [section(507, [student({ name: "Flo", total: 2, pending: 1, not_excused: 1, days: [day({}), day({ state: "pending", state_label: "Waiting for your decision" })] })])] }, { course: "", search: "" }, deps);
assert.match(waiting, /<div class="absence-totals has-waiting">[\s\S]*<span data-state="not_excused">Not excused<\/span><span data-state="pending">Waiting<\/span><span data-state="excused">Excused<\/span>/);
assert.match(waiting, /data-state="pending">1<span class="sr-only"> waiting for your decision<\/span>[\s\S]*data-tone="warning">Waiting for your decision/);
// Two terms shown: each section's line names its course and term.
const two = totalsMarkup({ sections: [section(507, [amy]), { ...section(501, [amy]), term_id: "t0", term_label: "Spring 2026" }] }, { course: "", search: "" }, deps);
assert.match(two, /<small>MATH 140 · Spring 2026 · 1 of 1 student missed a class · 3 days missed<\/small>/);

console.log("absence totals frontend checks passed");
