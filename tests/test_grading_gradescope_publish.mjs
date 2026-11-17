// Publishing grades on Gradescope (AGENTS.md "Sending grades to Gradescope", PUBLISHING GRADES; requirement, 2026-10-02): what the
// instructor reads before Faculty publishes anything, the last question, and the progress. The server decides; these check the page
// says it plainly, offers the choice only where it is one, and escapes what it shows.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import {tableMarkup, whenVisible} from "../static/ui.js";

globalThis.__ui = {tableMarkup, whenVisible};
const source = readFileSync(new URL("../static/grading-gradescope-publish.js", import.meta.url), "utf8")
  .replace(/^import \{runFormAction, tableMarkup, whenVisible\} from "\.\/ui\.js";$/m, "const runFormAction = () => {}; const {tableMarkup, whenVisible} = globalThis.__ui;")
  .replace(/^import \{patchKeepingDisclosures\} from "\.\/grading-jobs\.js";$/m, "const patchKeepingDisclosures = () => {};");
const {publishPreviewMarkup, publishRowsMarkup, publishConfirmMarkup, publicationMarkup, publishPanelMarkup, publicationLive, publishUnitWords, percent} =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const e = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const h = {e, check: (label, name) => `<label class="grading-check"><input type="checkbox" name="${e(name)}">${e(label)}</label>`,
  button: (key, label, extra = "") => `<button type="button" class="button" data-grading-action="${e(key)}" ${extra}>${e(label)}</button>`};

const row = (over = {}) => ({key: "written:s1", part: "written", section_id: "s1", section: 507, course_id: "1", course: "140-507", target_id: "9",
  title: "Quiz <3>", kind: "assignment_container", versions: [{letter: "A", published: false}, {letter: "B", published: false}], submissions: 78,
  graded: 100, published: false, partly: false, left_out: false, problem: "", warning: "", ...over});

test("Gradescope's graded share shows to one decimal and never reads as 100% or 0% when it is not", () => {
  assert.equal(percent(99.8547567175018), "99.9%");
  assert.equal(percent(99.96), "99.9%");
  assert.equal(percent(62.5), "62.5%");
  assert.equal(percent(100), "100%");
  assert.equal(percent(0.01), "0.1%");
  assert.equal(percent(0), "0%");
  // The server's rule (`gradescope_publish.percent`, Python round): an exact tie goes to the even tenth, anything else to the nearest.
  for (const [share, said] of [[6.25, "6.2"], [6.75, "6.8"], [1.25, "1.2"], [33.25, "33.2"], [0.25, "0.2"], [0.15, "0.1"], [0.95, "0.9"], [62.55, "62.5"]])
    assert.equal(percent(share), `${said}%`);
  assert.match(publishRowsMarkup({rows: [row({graded: 99.8547567175018})]}, h), /<td>99\.9%<\/td><\/tr>/);
});

test("the preview lists each section's assignment, offers a choice only where publishing is possible, and asks for the tick when ready", () => {
  const preview = {configured: true, ready: true, publish: 1, problems: [], running: null,
    warnings: ["These grades are not certified yet.", "2 final grades are not on Gradescope as Faculty has them"],
    rows: [row(), row({key: "written:s2", section: 508, published: true, title: "Quiz 3"}),
           row({key: "sheets:s1", part: "sheets", title: "", target_id: "", problem: "No Gradescope bubble-sheet exam is linked for this section yet: send its sheets first."}),
           row({key: "written:s3", section: 514, left_out: true, submissions: 0, graded: null}),
           row({key: "written:s4", section: 515, graded: 62.5, warning: "Gradescope shows it 62.5% graded: students would see the questions nobody graded there without a score."})]};
  const html = publishPreviewMarkup(preview, h);
  assert.match(html, /Quiz &lt;3&gt; <small>\(versions A, B\)<\/small>/);
  assert.match(html, /data-gradescope-publish-row="written:s1" checked/);
  assert.doesNotMatch(html, /data-gradescope-publish-row="written:s2"/);  // already published: nothing to choose
  assert.match(html, /<span class="chip" data-tone="success">Published<\/span>/);
  assert.doesNotMatch(html, /data-gradescope-publish-row="sheets:s1"/);
  assert.match(html, /No Gradescope bubble-sheet exam is linked for this section yet/);
  assert.match(html, /data-gradescope-publish-row="written:s3" (?!checked)/);  // left out: put back with a tick
  assert.match(html, /<span class="chip">Left out<\/span>/);
  assert.match(html, /62\.5% graded: students would see/);
  assert.match(html, /Before you publish, note:.*not certified yet.*not on Gradescope as Faculty has them/s);
  assert.match(html, /name="understood">I checked this\. Faculty publishes 1 Gradescope assignment; students then see their grades there\./);
  const blocked = publishPreviewMarkup({...preview, ready: false, problems: ["A send to Gradescope is still running for this assignment."]}, h);
  assert.match(blocked, /Before Faculty can publish:.*still running/s);
  assert.doesNotMatch(blocked, /name="understood"/);
  const nothing = publishPreviewMarkup({...preview, ready: false, publish: 0, rows: [row({published: true})]}, h);
  assert.match(nothing, /Nothing to publish/);
  assert.match(publishRowsMarkup({rows: []}, h), /No section of this assignment is linked to Gradescope yet/);
});

test("the last question names what is published, repeats the warnings and says nobody is emailed", () => {
  const html = publishConfirmMarkup({publish: 2, warnings: ["These grades are not certified yet."],
    rows: [row(), row({key: "sheets:s1", part: "sheets", title: "Exam <MC>", submissions: 1}), row({key: "written:s2", section: 508, published: true}), row({key: "written:s3", left_out: true})]}, h);
  assert.match(html, /Publish 2 assignments on Gradescope\?/);
  assert.match(html, /<strong>Section 507<\/strong> → Quiz &lt;3&gt; <span>\(Written grades, 78 submissions\)<\/span>/);
  assert.match(html, /→ Exam &lt;MC&gt; <span>\(Bubble sheets, 1 submission\)<\/span>/);
  assert.equal((html.match(/<li><strong>Section/g) || []).length, 2);  // never the published or left-out ones
  assert.match(html, /not certified yet/);
  assert.match(html, /Gradescope emails nobody for publishing\. Faculty never unpublishes/);
  assert.match(html, /data-publish-cancel>Cancel<\/button><button type="button" class="button danger" data-publish-confirm>Publish grades</);
});

test("progress names each assignment's state, and one that did not go says why with a Retry", () => {
  assert.equal(publicationMarkup(null, h), "");
  const running = {id: "p1", status: "running", created_at: "2026-10-02T15:00:00Z",
    units: [{part: "written", sections: ["507"], course: "140-507", title: "Quiz 3", status: "accepted", error: ""},
            {part: "sheets", sections: ["508", "509"], course: "140-508", title: "Exam <MC>", status: "failed", error: "Gradescope's Review Grades page has no Publish Grades form Faculty recognizes; nothing was sent."},
            {part: "written", sections: ["514"], course: "140-514", title: "Quiz 3", status: "present", error: "Its grades were already published on Gradescope; Faculty sent nothing."}]};
  assert.ok(publicationLive(running));
  const html = publicationMarkup(running, h);
  assert.match(html, /<h4>Publishing<\/h4>/);
  assert.match(html, /2 of 3 assignments published/);
  assert.match(html, /<strong>Section 507<\/strong> <span>Written grades → Quiz 3 \(140-507\)<\/span> <span data-gradescope-state="accepted">· published<\/span>/);
  assert.match(html, /<strong>Sections 508, 509<\/strong> <span>Bubble sheets → Exam &lt;MC&gt;/);
  assert.match(html, /· not published<\/span><p class="grading-warning" role="status">Gradescope's Review Grades page/);
  assert.doesNotMatch(html, /already published on Gradescope; Faculty sent nothing/);  // "was already published" says it
  assert.match(html, /data-grading-action="gradescope-publish-cancel"/);
  assert.match(html, /data-grading-action="gradescope-publish-retry"[^>]*>Retry \(1\)</);
  const done = publicationMarkup({...running, status: "complete", units: running.units.slice(0, 1)}, h);
  assert.match(done, /<h4>Published<\/h4>/);
  assert.doesNotMatch(done, /gradescope-publish-cancel|gradescope-publish-retry/);
  assert.equal(publishUnitWords.uncertain, "checking Gradescope");
});

test("the section points to the header's Publish only when Gradescope is set up, and says a publication is running", () => {
  // One place to publish (2026-10-18 review item 10): Certify's header holds the button; the fold's section names it in words.
  const ready = publishPanelMarkup({configured: true, publications: []}, h);
  assert.doesNotMatch(ready, /data-grading-action="gradescope-publish"/);
  assert.match(ready, /data-gradescope-publish-where>Publish with Publish grades on Gradescope… at the top of this page\./);
  assert.match(ready, /Gradescope emails nobody for publishing, and Faculty never unpublishes/);
  const busy = publishPanelMarkup({configured: true, publications: [{id: "p", status: "pending", units: []}]}, h);
  assert.match(busy, /id="gradescope-publish-busy"/);
  assert.match(busy, /Faculty is publishing\./);
  const off = publishPanelMarkup({configured: false, publications: []}, h);
  assert.doesNotMatch(off, /data-grading-action="gradescope-publish"/);
  assert.match(off, /not set up/);
});

test("a publication Faculty made by itself says after what", () => {
  // GRADESCOPE "AFTER CERTIFYING" (2026-10-12): the card names who published and why; the instructor's own says nothing more.
  const unit = {id: "u1", part: "written", sections: ["507"], course: "140-507", title: "Quiz 5", status: "accepted", error: ""};
  const base = {id: "p1", status: "complete", created_at: "2026-10-12T22:00:00Z", units: [unit]};
  assert.match(publicationMarkup({...base, plan: {automatic: {after: "certified", run: "r1"}}}, h), /data-gradescope-automatic>Published by Faculty by itself, after you certified\.<\/p>/);
  assert.match(publicationMarkup({...base, plan: {automatic: {after: "late", run: "r1"}}}, h), /Published by Faculty by itself, after it certified and sent a late paper\.<\/p>/);
  assert.doesNotMatch(publicationMarkup({...base, plan: {}}, h), /data-gradescope-automatic/);
});
