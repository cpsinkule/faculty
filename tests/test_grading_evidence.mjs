// The evidence panel shows what the instructor's review policy waived: every waived check still ran and is on the record,
// so a grade finalized under the narrow policy never reads as if nothing argued against it.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

// The panel imports the page's math renderer, which needs a browser; the rubric display has no imports and is inlined.
const display = readFileSync(new URL("../static/grading-rubric-display.js", import.meta.url), "utf8").replace(/^export /gm, "");
const comments = readFileSync(new URL("../static/grading-model-comments.js", import.meta.url), "utf8")
  .replace(/^import .*$/gm, "").replace(/^export /gm, "");
const source = readFileSync(new URL("../static/grading-evidence.js", import.meta.url), "utf8")
  .replace(/^import \{rubricDisplay\}.*$/m, display)
  .replace(/^import \{modelLabel.*$/m, comments)
  .replace(/^import \{mathText\}.*$/m, 'const mathText = (text, h) => h.e(text || "");')
  .replace(/^import \{COMPUTE\}.*$/m, 'const COMPUTE = {cloud_standard: "Cloud"};');  // the tier names live with the Grade step (grading-jobs.js)
const {evidenceMarkup, waivedMarkup, secondOpinion, proposalRole, proposalComment, reviewReasonMarkup} = await import(`data:text/javascript;base64,${Buffer.from(source + '\nexport {proposalRole, proposalComment, reviewReasonMarkup};').toString("base64")}`);
const h = {e: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")};

const value = risk => ({question: {rubric: {rules: {items: [{id: "R1", description: "Full credit"}]}}}, result: {signals: {risk: {
  level: "low", blocks: [], reasons: [], ...risk,
  components: {answer: {check: "match", work: "shown"}, ocr: {disagreements: 0}, votes: {count: 2}}}}}});

test("a narrow-policy grade lists the checks its policy waived instead of saying nothing argues against it", () => {
  const waived = {policy: "disagreement_and_work", blocks: ["The second grader requested instructor review."], reasons: ["Second grader: <look at the margin>"],
    state: "ocr_review", review_state_reasons: ["The reading has an unclear mark."], direct_reading: {rule: "direct-reading-v2", blocks: ["Only one reading stood beside the reader."]}};
  const html = evidenceMarkup(value({waived}), h);
  assert.match(html, /Waived by your review policy/);
  assert.match(html, /the reading would have waited for your review/);
  assert.match(html, /Waived because the reader read the paper directly/);
  for (const text of ["The second grader requested instructor review.", "The reading has an unclear mark.", "Only one reading stood beside the reader."]) assert.ok(html.includes(text), text);
  assert.match(html, /data-grading-role="second">Second grader:<\/strong><span class="grading-model-comment" data-grading-role="second"> &lt;look at the margin&gt;/);
  assert.ok(!html.includes("<look at"));  // Model text is escaped.
  assert.doesNotMatch(html, /Nothing in the evidence argues against this proposal/);
  assert.match(html, /Waived checks \(4\)/);
  assert.doesNotMatch(html, /style=/);  // The page's Content Security Policy forbids inline styles.
});

test("without waived checks the panel reads as before", () => {
  assert.match(evidenceMarkup(value({}), h), /Nothing in the evidence argues against this proposal/);
  assert.equal(waivedMarkup({blocks: []}, h), "");
  assert.equal(waivedMarkup({waived: {blocks: [], reasons: [], state: "proposed", review_state_reasons: []}}, h), "");
});

test("a team the additional reviewer settled does not advertise the pair's disagreement in the summary", () => {
  const settled = value({}), c = settled.result.signals.risk.components;
  c.second = {agrees: false, disagreements: ["R4"], independent: true, lineage: "beta"};
  assert.match(evidenceMarkup(settled, h), /1 rubric disagreements/);
  settled.result.signals.committee = {status: "resolved"};
  assert.doesNotMatch(evidenceMarkup(settled, h), /rubric disagreements/);
});

test("a flag reviewer that was deliberately not asked says so, with its escaped reason", () => {
  const skipped = value({});
  skipped.result.signals.flag_review = {outcome: "not_asked", reason: "The team settled every <flag> itself."};
  const html = evidenceMarkup(skipped, h);
  assert.match(html, /<strong>Flag reviewer<\/strong> was not asked about this response\. The team settled every &lt;flag&gt; itself\./);
  assert.doesNotMatch(html, /not_asked|model not recorded/);
});


test("new votes describe assessments while historical family counts remain labelled as recorded", () => {
  const response = value({}), signals = response.result.signals, c = signals.risk.components;
  signals.models = {primary: {endpoint: "first", lineage: "gpt"}, second: {endpoint: "second", lineage: "gpt"}};
  c.second = {agrees: true, disagreements: [], independent: false, lineage: "gpt"};
  c.votes = {count: 1, lineages: ["gpt"]};
  assert.match(evidenceMarkup(response, h), /recorded family votes: 1/);
  c.votes = {basis: "assessments", count: 2, lineages: ["gpt"]};
  delete c.second.independent;
  c.second.separate_assessment = true;
  const html = evidenceMarkup(response, h);
  assert.match(html, /agreeing assessments: 2/);
  assert.match(html, /separate assessment/);
  // Roles and the tier in plain words, never their keys; an unrecorded lineage leaves no empty brackets (grading-review-15).
  signals.tier = "cloud_standard";
  signals.models.reader = {endpoint: "reader-model", lineage: ""};
  const named = evidenceMarkup(response, h);
  assert.match(named, /Cloud · Primary grader: first \(gpt\) · Second grader: second \(gpt\) · Reader: reader-model ·/);
  assert.doesNotMatch(named, /cloud_standard|primary: first/);
  assert.doesNotMatch(html, /not a second vote|counted as one vote/);
});

test("Faculty flags stay neutral even when they mention models or contain a colon", () => {
  for (const flag of [
    "The primary grader and the second grader disagree on R2_3, R2_6; review the rubric selections individually.",
    "The primary and secondary graders differ on R2_3, R2_6; review this response individually.",
    "The standing grade raised a flag: full credit requires work.",
    "Full credit was proposed, but the rubric requires supporting work and little work is visible.",
  ]) assert.equal(reviewReasonMarkup(flag, h), flag);
});

test("each explicit model comment retains its role in review reasons", () => {
  for (const [role, prefix] of [["primary", "primary:"], ["second", "Second grader (layout):"],
    ["verifier", "verifier:"], ["arbiter", "The final arbitrator could not complete:"],
    ["flag_reviewer", "The flag reviewer left a flag unresolved:"], ["reader", "Uncertain reading of line 2:"]]) {
    const html = reviewReasonMarkup(`${prefix} <check> $x=2$`, h);
    assert.equal((html.match(new RegExp(`data-grading-role="${role}"`, "g")) || []).length, 2, html);
    assert.ok(html.includes("&lt;check&gt; $x=2$"));
    assert.doesNotMatch(html, /<check>|style=/);
  }
});

test("rubric comments follow the recorded author through adoption, revision and failed arbitration", () => {
  for (const [signals, role] of [
    [{}, "primary"], [{one_shot: {completed: true}}, "primary"],
    [{committee: {source: "standard.second"}, arbiter: {outcome: "adopted"}}, "second"],
    [{committee: {source: "high.reviewer"}}, "verifier"],
    [{committee: {source: "arbiter"}, arbiter: {outcome: "unsettled"}}, "arbiter"],
    [{committee: {source: "standard.primary_fallback"}, arbiter: {outcome: "failed"}}, "primary"],
    [{committee: {source: "second"}, flag_review: {outcome: "finalized"}}, "second"],
    [{tag_team: {completed: true}, arbiter: {decision: "adopt"}}, "primary"],
    [{tag_team: {completed: true}, arbiter: {decision: "revise"}}, "arbiter"],
    [{tag_team: {completed: false}, arbiter: {decision: "revise"}}, "primary"],
    [{committee: {source: "flag_resolution"}, flag_resolution: {outcome: "revised"}}, "flag_resolution"],
    [{committee: {source: "choice_reader"}}, "choice_reader"],
    [{committee: {source: "unknown"}}, ""],
  ]) {
    assert.equal(proposalRole(signals), role);
    const html = proposalComment(signals, "<reason>", h);
    if (role) assert.ok(html.includes(`data-grading-role="${role}"`));
    else assert.doesNotMatch(html, /data-grading-role/);
    assert.ok(html.includes("&lt;reason&gt;"));
  }
  // The author is named, never the generic "Model" (requirement, 2026-10-17).
  assert.match(proposalComment({committee: {source: "flag_resolution"}}, "r", h), />Flag review:</);
  assert.match(proposalComment({committee: {source: "choice_reader"}}, "r", h), />Multiple-choice reader:</);
});

test("rubric disagreement comments and per-flag rulings share their authors' roles", () => {
  const response = value({}), signals = response.result.signals;
  signals.panels = [{tier: "cloud_standard", decision: {pair: {disagreements: ["R1"]}, third_comparisons: {primary: {disagreements: ["R1"]}}},
    opinions: Object.fromEntries(["primary", "second", "verifier"].map(role => [role, {endpoint: "same-model", applications: [{rubric_item_id: "R1", applied: false, reason: `${role} <reason>`}]}]))}];
  const details = secondOpinion(signals, "R1", h);
  for (const role of ["primary", "second", "verifier"]) assert.ok(details.includes(`data-grading-role="${role}">${role} &lt;reason&gt;`));
  const flag = {text: "The graders disagree.", resolved: false, note: "Check the <margin>."};
  signals.arbiter = {outcome: "unsettled", reason: "Arbitrator explanation", flags: [flag]};
  signals.flag_review = {outcome: "manual_review", reason: "Flag review explanation", flags: [flag]};
  const html = evidenceMarkup(response, h);
  for (const role of ["arbiter", "flag_reviewer"]) assert.ok(html.includes(`data-grading-role="${role}">Check the &lt;margin&gt;.`));
  assert.match(html, /Still stands<\/strong>: The graders disagree\./);
});
