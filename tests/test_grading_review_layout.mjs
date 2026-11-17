// ONE LINE PER CAUSE on the review card (requirement, 2026-10-11; GRADING "Continuous review workspace"). The response read with
// `?details=review` carries the sentences the desk lists, gathered by cause (`grading/review_queue.py reason_causes`); the callout
// (static/grading-review-layout.js) leads with one line a cause and keeps every sentence verbatim in the fold. A read without the key
// draws exactly what it drew before. Pure markup: no server, no storage.
import assert from "node:assert/strict";
import test from "node:test";

import { reviewReasons, decisionPrompt, reasonsMarkup, reasonCauses, reviewMarkup, flagSpotBox, flagSpotView } from "../static/grading-review-layout.js";
import { reasonRole } from "../static/grading-model-comments.js";
import {reviewDraftChanges, sameReviewRubric} from "../static/grading-review-desk.js";
import {helpers} from "../static/grading-ui.js";

test('GRADING KEEP CONFLICTED EDITS restores only changed fields under the same complete rubric', () => {
  assert.deepEqual(reviewDraftChanges({comment: '', item: ['A'], manual_score: '2'}, {comment: 'Correction', item: ['A'], manual_score: '2'}), ['comment']);
  const before = {question: {id: 'q', rubric: {id: 'r', rules: {items: [{id: 'A', points: 2}]}}}};
  assert.equal(sameReviewRubric(before, structuredClone(before)), true);
  const changed = structuredClone(before);
  changed.question.rubric.rules.items[0].points = 3;
  assert.equal(sameReviewRubric(before, changed), false);
  changed.question.rubric = null;
  assert.equal(sameReviewRubric(before, changed), false);
});

const e = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const h = {e};
const differs = "The primary and secondary graders differ on R2; review this response individually.";
const disagreeOn = "The primary grader and the second grader disagree on R2; review the rubric selections individually.";
const waiting = {question: {rubric: {rules: {items: [{id: "R1"}, {id: "R2"}]}}}, state: "grading_review",
  result: {reasons: [disagreeOn, "second: Is <R2> met?"], signals: {risk: {blocks: [differs, "The primary grader and the second grader disagree."]}}}};
const gathered = [{key: "disagreement", label: "Graders disagree", reasons: [{text: differs, role: ""}, {text: "The primary grader and the second grader disagree.", role: ""}, {text: disagreeOn, role: ""}]},
  {key: "grader", label: "Grader’s note", reasons: [{text: "second: Is <R2> met?", role: "second"}]}];

test("GRADING: the review card leads with one line a cause and keeps every recorded reason verbatim in the fold", () => {
  const value = {...waiting, reason_causes: gathered}, html = reasonsMarkup(value, h);
  assert.deepEqual(reasonCauses(value), gathered);
  // One line a cause: its label, its first sentence, and how many more it holds. Three sentences of one disagreement are one line.
  const lines = html.slice(html.indexOf("<dl"), html.indexOf("</dl>"));
  assert.deepEqual([...lines.matchAll(/<dt data-reason-cause="([^"]+)">([^<]+)<\/dt>/g)].map(found => [found[1], found[2]]), [["disagreement", "Graders disagree"], ["grader", "Grader’s note"]]);
  assert.equal(lines.split("<dd>").length - 1, 2);
  assert.ok(lines.includes(`${differs}</span> <span class="grading-reason-more">+2 more</span></dd>`));
  assert.ok(!lines.includes(disagreeOn), "a cause's further sentences are in the fold, not on its line");
  assert.ok(lines.includes('<strong class="grading-model-label" data-grading-role="second">second:</strong>') && lines.includes("Is &lt;R2&gt; met?"), "the author as recorded, the words escaped");
  assert.ok(!lines.includes("<li>") && !html.includes("grading-reasons-visible") && !html.includes("style="));
  // The fold: closed, counted, every listed sentence once and verbatim under its cause's label, authors kept.
  const fold = html.slice(html.indexOf("<details"));
  assert.match(fold, /^<details class="disclosure" data-reason-all><summary><span>All recorded reasons \(4\)<\/span><\/summary>/);
  assert.ok(!/<details[^>]*\bopen\b/.test(html));
  assert.deepEqual([...fold.matchAll(/<dt>([^<]+)<\/dt><dd><ul>(.*?)<\/ul><\/dd>/g)].map(found => [found[1], found[2].split("<li>").length - 1]), [["Graders disagree", 3], ["Grader’s note", 1]]);
  for (const reason of reviewReasons(value)) assert.equal(fold.split(`>${reason.replace(/^second:/, "").replace("<R2>", "&lt;R2&gt;")}</span>`).length - 1, 1, reason);
  assert.equal(fold.split("<li>").length - 1, reviewReasons(value).length);
  assert.ok(fold.includes('<li><strong class="grading-model-label" data-grading-role="second">second:</strong>'));
  // The prompt, the line shortcuts and the listed reasons are what they were.
  assert.ok(html.includes("<p>Do the conditions for Item 2 apply to this work?</p>") && html.includes('data-review-line="R2"') && html.includes("data-review-original"));
  assert.deepEqual([reviewReasons(value), decisionPrompt(value)], [reviewReasons(waiting), decisionPrompt(waiting)]);
  // A cause that holds more names who wrote under it; a long first sentence is shortened on the line and whole in the fold.
  const long = "primary: " + "x".repeat(400);
  const many = {...waiting, result: {reasons: [long, "second: Is <R2> met?", "Uncertain reading of line-4: 3x or 3π."], signals: {}},
    reason_causes: [{key: "grader", label: "Grader’s note", reasons: [{text: long, role: "primary"}, {text: "second: Is <R2> met?", role: "second"}]},
      {key: "reading", label: "Reading check", reasons: [{text: "Uncertain reading of line-4: 3x or 3π.", role: "reader"}]}]};
  const drawn = reasonsMarkup(many, h), above = drawn.slice(0, drawn.indexOf("</dl>"));
  assert.ok(drawn.includes('<span class="grading-reason-more">+1 more · <strong class="grading-model-label" data-grading-role="primary">Primary grader</strong>, <strong class="grading-model-label" data-grading-role="second">Secondary grader</strong></span>'));
  assert.ok(above.includes("x".repeat(338) + "…") && !above.includes("x".repeat(339)));
  assert.ok(drawn.slice(drawn.indexOf("<details")).includes("x".repeat(400)));
  assert.match(drawn, /All recorded reasons \(3\)/);
  // One sentence a cause: the line says it all, and the fold is still there with the same sentence.
  const single = {...waiting, result: {reasons: ["second: Is <R2> met?"], signals: {}}, reason_causes: [gathered[1]]};
  assert.ok(!reasonsMarkup(single, h).includes("grading-reason-more") && reasonsMarkup(single, h).includes("All recorded reasons (1)"));
});

test("GRADING: without the causes, or with causes that are not exactly the listed reasons, the card is the plain list it was", () => {
  const plain = '<aside class="callout grading-review-reasons" data-tone="warning" aria-label="Decision needed"><strong class="callout-title">Decision needed</strong><p>Do the conditions for Item 2 apply to this work?</p><ul class="grading-reasons-visible"><li><span class="grading-math" data-math>The primary and secondary graders differ on R2; review this response individually.</span></li><li><span class="grading-math" data-math>The primary grader and the second grader disagree.</span></li><li><span class="grading-math" data-math>The primary grader and the second grader disagree on R2; review the rubric selections individually.</span></li></ul><div class="grading-actions"><button type="button" class="text-button" data-review-line="R2">Inspect Item 2</button><button type="button" class="text-button" data-review-original>Inspect original scan</button></div><details class="disclosure"><summary><span>All recorded reasons (4)</span></summary><div class="disclosure-body"><ul><li><span class="grading-math" data-math>The primary and secondary graders differ on R2; review this response individually.</span></li><li><span class="grading-math" data-math>The primary grader and the second grader disagree.</span></li><li><span class="grading-math" data-math>The primary grader and the second grader disagree on R2; review the rubric selections individually.</span></li><li><strong class="grading-model-label" data-grading-role="second">second:</strong><span class="grading-model-comment" data-grading-role="second"><span class="grading-math" data-math> Is &lt;R2&gt; met?</span></span></li></ul></div></details></aside>';
  assert.equal(reasonsMarkup(waiting, h), plain);
  assert.equal(reasonCauses(waiting), null);
  // The server only gathers. Causes that miss a listed sentence, hold one twice, hold one the desk does not list, or have no label
  // or no sentence are not drawn: no recorded reason can go missing from the card.
  const without = gathered.map(cause => ({...cause, reasons: cause.reasons.filter(reason => reason.text !== disagreeOn)}));
  const twice = [gathered[0], {...gathered[1], reasons: [...gathered[1].reasons, gathered[0].reasons[0]]}];
  const stranger = [gathered[0], {...gathered[1], reasons: [{text: "second: something else", role: "second"}]}];
  for (const causes of [[], null, "causes", without, twice, stranger, [...gathered, {key: "other", label: "Other", reasons: []}], gathered.map(cause => ({...cause, label: ""}))])
    assert.equal(reasonsMarkup({...waiting, reason_causes: causes}, h), plain);
  // A hidden proposal shows nothing, whatever the read carried; nothing listed draws nothing.
  assert.equal(reasonsMarkup({...waiting, hidden: true, reason_causes: gathered}, h), "");
  assert.equal(reasonsMarkup({result: {signals: {}}, reason_causes: []}, h), "");
  // Three short reasons or fewer keep no fold, as before.
  const few = {...waiting, result: {reasons: ["Graders disagree"], signals: {}}};
  assert.ok(!reasonsMarkup(few, h).includes("<details") && reasonsMarkup(few, h).includes('<ul class="grading-reasons-visible"><li>'));
});

test("GRADING: a reason's author and the listed order are read the same on the card and by the server that gathers the causes", () => {
  // The SAME table as AUTHORS in tests/test_grading_review_queue.py (`review_queue.reason_author`): one text per recorded prefix.
  const authors = [["primary: note", "primary"], ["Primary grader: note", "primary"], ["One-shot grader (layout): note", "primary"], ["second: note", "second"],
    ["Secondary: note", "second"], ["Second grader: note", "second"], ["Second grader (transcription): note", "second"], ["Secondary grader: note", "second"],
    ["verifier: note", "verifier"], ["reviewer: note", "verifier"], ["Additional reviewer: note", "verifier"], ["Adversarial reviewer: note", "verifier"],
    ["arbiter: note", "arbiter"], ["The final arbitrator named a grade but left a flag standing: note", "arbiter"], ["Tag-Team reviewer: note", "arbiter"],
    ["flag_reviewer: note", "flag_reviewer"], ["The flag reviewer left a flag unresolved: note", "flag_reviewer"], ["Reader: note", "reader"],
    ["Uncertain reading of line-4: 3x or 3π.", "reader"], ["Uncertain layout from line-4 to line-1: the arrow may join them.", "reader"],
    ["Jev could not break the tie: note", "jev"], ["The flag reviewer: note", ""], ["primary note", ""], ["primary:note", ""], ["The graders disagree.", ""],
    ["The standing grade raised or left a flag it did not settle: note", ""]];
  assert.deepEqual(authors.map(([text]) => [text, reasonRole(text)]), authors);
  // The SAME Exam 1 sample as EXAM_ONE there (`review_queue.listed_reasons`): ten reasons, two repeated with a role key, eight listed.
  const exam = {result: {reasons: ["Uncertain layout from line-4 to line-1: the arrow may join them.", "Uncertain reading of line-4: 3x or 3π.", "Confirm side margin for R2_4.",
    "Whether a lone 80,000 satisfies R2_2.", "primary: Confirm side margin for R2_4.", "primary: Whether a lone 80,000 satisfies R2_2.",
    "The grader found the rubric's wording unclear for this response.", "The handwriting is ambiguous in places.",
    "The grader was uncertain about a rubric decision.", "second: The second grader doubts R2_1."],
    signals: {risk: {blocks: ["The grader found the rubric's wording unclear for this response.", "primary: Confirm side margin for R2_4.",
      "primary: Whether a lone 80,000 satisfies R2_2.", "The handwriting is ambiguous in places."]}}}};
  assert.deepEqual(reviewReasons(exam), ["The grader found the rubric's wording unclear for this response.", "The handwriting is ambiguous in places.",
    "The grader was uncertain about a rubric decision.", "primary: Confirm side margin for R2_4.", "primary: Whether a lone 80,000 satisfies R2_2.",
    "second: The second grader doubts R2_1.", "Uncertain layout from line-4 to line-1: the arrow may join them.", "Uncertain reading of line-4: 3x or 3π."]);
});

// 2026-10-18 (instructor, on a take-home answer finalized at full marks: "why does this say it needs review if nothing is wrong with
// it"): "verify boundary" under a crop asks for a look while the grade waits; a final grade no longer carries it.
test("GRADING: a crop says verify boundary only while its grade waits for review", () => {
  const any = new Proxy({e}, {get: (target, key) => target[key] ?? (() => "")});
  const crop = registered => ({metadata: {registered}, images: {original: {url: "/c.png"}, grayscale: {url: "/g"}, contrast: {url: "/c"}, ink: {url: "/i"}}});
  const value = {id: "r1", question: {content: {max_points: 6}, rubric: {rules: {items: [{id: "R1_1", points: 6, description: "Correct"}]}}},
    result: {applications: [{rubric_item_id: "R1_1", applied: true}], score: {score: 6}, signals: {}, reasons: [], transcript: {lines: []}},
    crops: [crop(false), crop(true)], recognition: {consensus: {}, candidates: []}, history: [],
    source: {regions: [{page: 1, raster_url: "/p1"}, {page: 1, raster_url: "/p1"}]}};
  const says = extra => /verify boundary/.test(reviewMarkup({...value, ...extra}, 0, any));
  assert.equal(says({}), true, "a waiting grade still asks");
  assert.equal(says({acceptance: {kind: "trial", active: true, final: false}}), true, "a trial acceptance is not final");
  assert.equal(says({acceptance: {kind: "automatic", active: true, final: false}}), true, "a held automatic grade is not final");
  assert.equal(says({acceptance: {kind: "automatic", active: false, final: true}}), true, "a suspended automatic grade is not final");
  assert.equal(says({acceptance: {kind: "automatic", active: true, final: true, basis: "flag_review"}}), false);
  assert.equal(says({review: {applications: [], score: {score: 6}, transcript: {lines: []}}}), false);
  assert.equal(/verify boundary/.test(reviewMarkup({...value, crops: [crop(true), crop(true)]}, 0, any)), false);
});

// 2026-11-14 (instructor, of a flag asking them to confirm what the student wrote: "is it possible for us to put a bbox around the
// specific spot that needs clarification?", then "make a crop of the boxed unsure part and put it up with the choices so that I can
// see the handwriting next to the choices"; GRADING FLAG REVIEW "THE SPOT TO LOOK AT"). The spots the flag review outlined are drawn
// on their own crops and shown enlarged beside the choices; a note without them is the note it always was.
test("GRADING THE SPOT TO LOOK AT: the flag review's spots are outlined on their crops and enlarged beside the choices", () => {
  const any = new Proxy({e}, {get: (target, key) => target[key] ?? (() => "")});
  const crop = url => ({metadata: {registered: true}, images: {original: {url}, grayscale: {url: "/g"}, contrast: {url: "/c"}, ink: {url: "/i"}}});
  const readings = [{reading: "the exponent is 9", lines: ["R1_1"], score: 3, max_score: 3}, {reading: "the exponent is 4", lines: [], score: 2.5, max_score: 3}];
  const value = {id: "r1", question: {content: {max_points: 3}, rubric: {rules: {items: [{id: "R1_1", points: 3, description: "Correct"}]}}},
    result: {applications: [{rubric_item_id: "R1_1", applied: true}], score: {score: 3}, signals: {}, reasons: ["Look at the paper"], transcript: {lines: []}},
    crops: [crop("/first.png"), crop("/second.png")], recognition: {consensus: {}, candidates: []}, history: [],
    source: {regions: [{page: 1, raster_url: "/p1"}, {page: 1, raster_url: "/p1"}]}};
  const note = {reason: "Look at the small exponent on 9.", readings, at: "2026-11-14T00:00:00+00:00"};
  const look = [{label: "exponent on <9>", crop: 1, bbox: [0.334, 0.812, 0.35, 0.845]}, {label: "", crop: 0, bbox: [0.1, 0.2, 0.3, 0.4]}];
  const plain = reviewMarkup({...value, flag_review: note}, 0, any), outlined = reviewMarkup({...value, flag_review: {...note, look}}, 0, any);
  assert.doesNotMatch(plain, /data-flag-spot|data-flag-view|grading-flag-choose/, "a note without spots is the note it was");
  const choices = '<p class="supporting-copy">Choose what the paper shows. Each choice ticks exactly its lines and approves.</p><div class="grading-actions">';
  assert.ok(plain.includes(`Look at the small exponent on 9.</p>${choices}<button type="button" class="button" data-flag-reading="0">`), "the plain note leads straight to its choices");
  // On the scan: each spot once, on its own crop, placed by the desk.
  const crops = outlined.split('<div class="grading-image" data-crop="').slice(1);
  assert.match(crops[0], /^0">.*<span class="grading-flag-spot" data-flag-spot="1" role="img" aria-label="Outlined to read: the marks to read"><\/span><\/div>/s);
  assert.match(crops[1], /^1">.*<span class="grading-flag-spot" data-flag-spot="0" role="img" aria-label="Outlined to read: exponent on &lt;9&gt;"><\/span><\/div>/s);
  assert.equal((outlined.match(/class="grading-flag-spot"/g) || []).length, 2, "each spot once, on its own crop");
  assert.doesNotMatch(outlined, /style=/, "the page allows no inline style: the desk places each outline and view");
  // Beside the choices: each spot's own crop picture, named, in the order the review named them, then the same choices.
  const view = (i, url, name) => `<figure class="grading-flag-view"><button type="button" class="grading-flag-view-open" data-flag-view="${i}" aria-label="Show on the scan: ${name}"><img src="${url}" alt="The student's writing at ${name}"><span class="grading-flag-view-mark"></span></button><figcaption>${name}</figcaption></figure>`;
  assert.ok(outlined.includes(`Look at the small exponent on 9.</p><div class="grading-flag-choose"><div class="grading-flag-views" data-flag-views>${view(0, "/second.png", "exponent on &lt;9&gt;")}${view(1, "/first.png", "the marks to read")}</div><div class="grading-flag-choices">${choices}<button type="button" class="button" data-flag-reading="0">`));
  // A spot on a crop the response does not have is not shown; a note with no reading choices still shows its spots.
  const off = reviewMarkup({...value, flag_review: {...note, look: [{label: "nowhere", crop: 5, bbox: [0.1, 0.2, 0.3, 0.4]}]}}, 0, any);
  assert.doesNotMatch(off, /data-flag-view|data-flag-spot=|grading-flag-choose/);
  assert.match(reviewMarkup({...value, flag_review: {...note, readings: [], look}}, 0, any), /data-flag-views>.*<\/div><div class="grading-flag-choices"><\/div><\/div><\/div>/s);
  // The outline: grown 1.2% of the crop each way, kept inside the crop.
  const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} is not ${b}`);
  const box = flagSpotBox([0.334, 0.812, 0.35, 0.845]);
  near(box.left, 0.322); near(box.top, 0.8); near(box.width, 0.04); near(box.height, 0.057);
  const edge = flagSpotBox([0, 0.995, 1, 1]);
  near(edge.left, 0); near(edge.top, 0.983); near(edge.width, 1); near(edge.height, 0.017);
  // The view: the box and as much again each way, at least 6% and at most 15% of the width, 4% to 10% of the height, inside the crop.
  const small = flagSpotView([0.334, 0.812, 0.35, 0.845]);
  near(small.left, 0.274); near(small.top, 0.772); near(small.width, 0.136); near(small.height, 0.113);
  const line = flagSpotView([0.05, 0.9, 0.95, 0.99]);
  near(line.left, 0); near(line.top, 0.81); near(line.width, 1); near(line.height, 0.19);
  const middling = flagSpotView([0.4, 0.4, 0.5, 0.46]);
  near(middling.left, 0.3); near(middling.top, 0.34); near(middling.width, 0.3); near(middling.height, 0.18);
});

// 2026-11-06 (instructor): a grade the flag review settled is worded as a flag-review grade on the card, as in the exports, never as
// accepted "under your benchmark report".
test("GRADING: a flag-resolution grade's card says it was finalized after flag review", () => {
  const any = new Proxy({e}, {get: (target, key) => target[key] ?? (() => "")});
  const value = {id: "r1", question: {content: {max_points: 6}, rubric: {rules: {items: [{id: "R1_1", points: 6, description: "Correct"}]}}},
    result: {applications: [{rubric_item_id: "R1_1", applied: true}], score: {score: 6}, signals: {}, reasons: [], transcript: {lines: []}},
    crops: [], recognition: {consensus: {}, candidates: []}, history: [], source: {regions: []}};
  const card = basis => reviewMarkup({...value, acceptance: {kind: "automatic", active: true, final: true, basis}}, 0, any);
  assert.match(card("flag_resolution"), /Finalized automatically after flag review/);
  assert.doesNotMatch(card("flag_resolution"), /benchmark report/);
  assert.match(card("choice_reader"), /Finalized automatically by the multiple-choice reader/);
});

// 2026-11-11 (instructor, of a grade Faculty approved for participation whose comment read "the answer was not looked at": "is this
// part seen by students?", then "Can we make sure it's clear which comments will be seen by students and which won't"; GRADING "WHO
// SEES A COMMENT"). The desk says it at each field; a field's accessible name stays its label alone.
test("GRADING WHO SEES A COMMENT: the review desk says who reads its comment and its adjustment", () => {
  const real = helpers({escape: e});
  const value = {id: "r1", current: true, question: {content: {max_points: 6}, rubric: {rules: {floor: 0, ceiling: 6, items: [{id: "R1_1", points: 6, description: "Correct"}]}}},
    result: {applications: [{rubric_item_id: "R1_1", applied: true}], score: {score: 6}, signals: {}, reasons: [], transcript: {lines: []}},
    review: {comment: "Check the units", applications: [{rubric_item_id: "R1_1", applied: true}], score: {score: 5, adjustments: [{points: -1, reason: "Units"}]}, transcript: {lines: []}},
    crops: [], recognition: {consensus: {}, candidates: []}, history: [], source: {regions: []}};
  const html = reviewMarkup(value, 0, real);
  assert.ok(html.includes('<label class="field">Comment / manual grade reason<small class="field-hint" data-seen-by="you">Only you see this</small><textarea name="comment" aria-label="Comment / manual grade reason"'));
  const adjust = html.slice(html.indexOf("<span>Adjust points</span>"), html.indexOf("<span>Manual grade</span>"));
  assert.ok(adjust.includes('aria-label="Adjustment reason"'));
  assert.ok(adjust.includes('<p class="supporting-copy" data-seen-by="adjustment">Students see the points on Gradescope, not the reason. Only a line added to every grade shows its wording there.</p>'));
  assert.equal(html.split("data-seen-by=").length - 1, 2, "nothing else on the desk goes to students or is marked");
});
