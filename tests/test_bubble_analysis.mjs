import assert from "node:assert/strict";
import test from "node:test";

import { analysisMarkup, analysisSignature, choiceRows, discriminationText, miskeyed, percentText, reliabilityText, versionLead } from "../static/grading-bubble-analysis.js";

// Bubble sheet item analysis (read only): the panel's wording and markup come from the server's analysis; nothing is decided here.
const e = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

const choice = (letter, count, extra = {}) => ({choice: letter, count, key: false, mean_pct: count ? 60 : null, upper: 1, lower: 0, ...extra});
const question = (number, extra = {}) => ({number, answer: "B", scoring: "exact", points: "1", sheets: 20, read: 20, full: 10, full_pct: 50, difficulty: .5,
  discrimination: .42, upper_pct: 83.3, lower_pct: 16.7, flags: [],
  choices: [choice("A", 2), choice("B", 10, {key: true}), choice("C", 8), choice("D", 0), choice("E", 0), choice("blank", 0), {...choice("multiple", 0), common: "", common_count: 0}], ...extra});
const version = (letter, questions, extra = {}) => ({letter, sheets: 20, too_few: false, group_size: 6, mean_pct: 71.25, sd_pct: 12.34, alpha: .812, questions, ...extra});

test("the analysis is read again only when the saved key or a scored sheet changes", () => {
  const sheets = [{sheet_id: "a", status: "scored", version: "A", earned: "8", answers: ["B", "C"]}, {sheet_id: "b", status: "needs_review", version: "A", answers: ["A"]}];
  const view = {key: {revision: 2}, sheets};
  assert.equal(analysisSignature(view), analysisSignature({key: {revision: 2}, sheets: sheets.map(s => ({...s, student: "Someone else"}))}));
  assert.notEqual(analysisSignature(view), analysisSignature({...view, key: {revision: 3}}));
  assert.notEqual(analysisSignature(view), analysisSignature({...view, sheets: [{...sheets[0], answers: ["B", "D"]}, sheets[1]]}));
  // A sheet still in review is not analysed, so its marks change nothing.
  assert.equal(analysisSignature(view), analysisSignature({...view, sheets: [sheets[0], {...sheets[1], answers: ["E"]}]}));
  assert.match(analysisSignature({key: null, sheets: []}), /^none:0:/);
});

test("figures read as percents and a signed discrimination, a dash when there is none", () => {
  assert.deepEqual([percentText(50), percentText(78.5), percentText(33.333), percentText(null)], ["50%", "78.5%", "33.3%", "—"]);
  assert.deepEqual([discriminationText(.4234), discriminationText(-.849), discriminationText(0), discriminationText(null)], ["0.42", "−0.85", "0.00", "—"]);
});

test("the lead names the sheets, the average and what the reliability means, or why it waits", () => {
  assert.equal(versionLead(version("A", [])), "20 sheets · average 71.3% (SD 12.3) · reliability α 0.81: the questions rank students consistently");
  assert.equal(versionLead(version("A", [], {sheets: 4, too_few: true, alpha: null, mean_pct: 50, sd_pct: 10})), "4 sheets · average 50% (SD 10) · reliability and discrimination need at least 10 scored sheets");
  assert.equal(versionLead(version("A", [], {sheets: 1, too_few: true, alpha: null, sd_pct: null, mean_pct: 90})), "1 sheet · average 90% · reliability and discrimination need at least 10 scored sheets");
  assert.equal(versionLead(version("A", [], {alpha: null})), "20 sheets · average 71.3% (SD 12.3) · reliability is not computed: the totals do not vary");
  assert.equal(versionLead(version("B", [], {sheets: 0})), "No scored sheets for this version yet.");
  assert.deepEqual([.85, .75, .6, .2, -.3].map(reliabilityText).map(text => text.split(" ").slice(0, 4).join(" ")),
    ["the questions rank students", "the questions rank students", "the questions agree only", "the questions agree little", "the questions agree little"]);
});

test("possibly miskeyed questions are listed version by version; choices show every letter and only marked blanks and several", () => {
  const flag = {kind: "possible_miskey", text: "More of the top scorers chose C than the key's B."};
  const analysis = {minimum: 10, versions: [version("A", [question(1), question(4, {flags: [flag, {kind: "weak", text: "…"}]})]), version("B", [question(2, {flags: [flag]})])]};
  assert.deepEqual(miskeyed(analysis).map(m => `${m.letter}${m.number}`), ["A4", "B2"]);
  assert.deepEqual(miskeyed({versions: []}), []);
  const rows = choiceRows(question(1, {choices: [...question(1).choices.slice(0, 5), choice("blank", 2), {...choice("multiple", 0), common: "", common_count: 0}]}));
  assert.deepEqual(rows.map(r => [r.label, r.share]), [["A", .1], ["B", .5], ["C", .4], ["D", 0], ["E", 0], ["Blank", .1]]);
});

test("the panel escapes what it shows, links each question to the key and draws bars without style attributes", () => {
  const flagged = question(7, {answer: "B", flags: [{kind: "possible_miskey", text: "More of the top scorers chose <C> than the key's B."}, {kind: "hard", text: "Sheets earned 20% of its points on average."}]});
  const partial = question(8, {scoring: "partial", answer: "BD", full_pct: 40, difficulty: .6, discrimination: null, upper_pct: null, lower_pct: null});
  const one = analysisMarkup({minimum: 10, versions: [version("A", [question(1), flagged, partial])]}, e, "/api/grading/assignments/p1/bubble-sheets");
  assert.match(one, /href="\/api\/grading\/assignments\/p1\/bubble-sheets\/analysis\.csv" data-view-file="\/api\/grading\/assignments\/p1\/bubble-sheets\/analysis\.csv" data-view-name="Item analysis">Item analysis \(CSV\)</);  // opens as a table (VIEWER)
  assert.match(one, /<strong class="callout-title">Possibly miskeyed<\/strong><p>Check the key of this question before you certify\.<\/p>/);
  assert.match(one, /data-analysis-look="A:7">Question 7<\/button> More of the top scorers chose &lt;C&gt; than the key's B\./);
  assert.match(one, /<span class="chip" data-tone="warning" title="[^"]+">Possible miskey<\/span><span class="chip" data-tone="neutral" title="[^"]+">Hard<\/span>/);
  assert.match(one, /<li class="bubble-choice" data-key title="B \(the key\): 10 of 20 sheets · averaging 60% overall · 1 of the top 6, 0 of the bottom 6">/);
  assert.match(one, /data-share="0\.4"/);
  assert.match(one, /40% <small>avg 60%<\/small>/);  // partial credit: the average share is shown beside full credit
  assert.match(one, /<td>BD <small>Partial<\/small><\/td>/);
  assert.doesNotMatch(one, /style=/);
  assert.doesNotMatch(one, /<h4>Version A<\/h4>/);  // one version needs no heading
  const two = analysisMarkup({minimum: 10, versions: [version("A", [question(1)]), version("B", [], {sheets: 0})]}, e, "/x");
  assert.match(two, /<h4>Version A<\/h4>[\s\S]*<h4>Version B<\/h4>/);
  assert.match(two, /No scored sheets for this version yet\./);
  assert.doesNotMatch(two, /callout-title/);
});
