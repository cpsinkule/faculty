import {evidenceMarkup, secondOpinion} from "./grading-evidence.js";
import {mathText, transcriptMarkup} from "./grading-math.js";
import {cropButton} from "./grading-crops.js";
import {rubricDisplay} from "./grading-rubric-display.js";
import {modelComment, modelLabel, proposalComment, proposalRole, reviewReasonMarkup, reasonRole} from "./grading-model-comments.js";
import {viewLink} from "./file-viewer.js";
import {seenByMarkup} from "./ui.js";

// Why a response waits for the instructor, in one place under its heading (grun:P2), in the order a busy instructor can act on it
// (grading-review-3: on Exam 1 the reader's notes filled the three places shown while Faculty's own summaries folded): the confirmed
// rubric finalization's reasons; Faculty's own summaries among what would not be accepted unseen ("The grader found the rubric's
// wording unclear…"); Faculty's other notes; the graders' notes by role (the blocks carry some, "primary: …"); the reader's notes about
// single lines. Authorship is read from the text's recorded prefix (grading-model-comments.js). Each text once: a copy that only adds a
// role key is the same reason, and the role-named copy is kept so authorship stays visible. Texts stay verbatim in Recorded reasons;
// an actionable prompt leads. Independent checks below keeps the full audit. A proposal hidden for a blind review shows nothing.
// The callout shows them one line a cause where the read gathered them (`reasonCauses`, below); this list and its order are unchanged.
const ROLE_KEY = /^(?:primary|second|secondary|verifier|reviewer|arbiter|flag_reviewer):\s*/i;
export function reviewReasons(value) {
  if (value.hidden || !value.result) return [];
  const signals = value.result.signals || {};
  const clean = list => (list || []).map(reason => String(reason ?? "").trim()).filter(Boolean);
  const listed = [...clean(signals.rubric_quality?.reasons).map(text => ["finalization", text]), ...clean(signals.risk?.blocks).map(text => ["block", text]),
    ...clean(value.result.reasons).map(text => ["note", text])];
  const kept = new Map();  // the text without a role key -> {sources, text, order}
  listed.forEach(([source, text], order) => {
    const key = text.replace(ROLE_KEY, ""), seen = kept.get(key);
    if (!seen) kept.set(key, {sources: new Set([source]), text, order});
    else { seen.sources.add(source); if (ROLE_KEY.test(text) && !ROLE_KEY.test(seen.text)) seen.text = text; }
  });
  const tier = entry => {
    if (entry.sources.has("finalization")) return 0;
    const role = reasonRole(entry.text);
    return role === "reader" ? 4 : role ? 3 : entry.sources.has("block") ? 1 : 2;
  };
  return [...kept.values()].sort((a, b) => tier(a) - tier(b) || a.order - b.order).map(entry => entry.text);
}

export function decisionPrompt(value) {
  const reasons = reviewReasons(value);
  if (!reasons.length) return null;
  const items = value.question?.rubric?.rules?.items || [];
  const text = reasons.join(" ");
  // GRADING: use exact retained line references, never infer an evidence location or an alternative reading.
  const named = items.filter(item => {
    const id = item.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(/^\d+$/.test(item.id) ? `(?:item|line)\\s+${id}(?!\\w)` : `(?<!\\w)${id}(?!\\w)`, "i").test(text);
  }).map(item => item.id);
  const display = rubricDisplay(value.question?.rubric?.rules);
  const prompt = named.length ? `Do ${named.length === 1 ? "the conditions for " + display.label(named[0]) : "the conditions for Items " + display.list(named, ", ")} apply to this work?`
    : ["ocr_review", "both"].includes(value.state || value.result?.state) ? "What does the flagged part of the original scan say?"
    : "Does the proposed grade satisfy the rubric and the flagged evidence?";
  return {prompt, items: named};
}

// What the flag review left with the instructor (GRADING FLAG REVIEW, prompt v3, 2026-10-03): the mark to look at and, when it named
// them, one button per possible reading with the grade it gives. A button ticks exactly those lines and approves: the instructor's own
// decision, made by looking at the paper.
export function flagReadingsMarkup(value, h) {
  const note = value.flag_review;
  if (!note) return "";
  // GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (requirement, 2026-11-14): each outlined spot is shown enlarged beside the choices, the
  // same scan picture cut to the spot and what surrounds it (placed by the desk, `flagSpotView`), named under it. The view is a
  // button that scrolls to its outline on the scan. A note without spots is drawn exactly as before.
  const display = rubricDisplay(value.question?.rubric?.rules);
  const choice = (reading, index) => `<button type="button" class="button" data-flag-reading="${index}"><span>${h.e(reading.reading)}</span> <strong>${h.e(reading.score)} / ${h.e(reading.max_score)}</strong> <small>${h.e(reading.lines.length ? display.list(reading.lines, ", ") : "no line")}</small></button>`;
  const label = spot => h.e(spot.label || "the marks to read");
  const views = (note.look || []).map((spot, i) => {
    const url = value.crops?.[spot.crop]?.images?.original?.url;
    return !url ? "" : `<figure class="grading-flag-view"><button type="button" class="grading-flag-view-open" data-flag-view="${i}" aria-label="Show on the scan: ${label(spot)}"><img src="${h.e(url)}" alt="The student's writing at ${label(spot)}"><span class="grading-flag-view-mark"></span></button><figcaption>${label(spot)}</figcaption></figure>`;
  }).join("");
  const choices = note.readings.length ? `<p class="supporting-copy">Choose what the paper shows. Each choice ticks exactly its lines and approves.</p><div class="grading-actions">${note.readings.map(choice).join("")}</div>` : "";
  return `<div class="grading-flag-readings" data-flag-readings><p><strong>Look at the paper:</strong> ${h.e(note.reason)}</p>${views ? `<div class="grading-flag-choose"><div class="grading-flag-views" data-flag-views>${views}</div><div class="grading-flag-choices">${choices}</div></div>` : choices}</div>`;
}

// GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (requirement, 2026-11-14): where the flag review's outline of a spot sits on its crop, as
// fractions of the crop. The model's box is grown a little each way (measured that day: 5 of 42 boxes sat on the neighbouring mark),
// kept inside the crop, and never filled, so the marks under it read as they were scanned.
const SPOT_MARGIN = 0.012;
export function flagSpotBox(bbox) {
  const [left, top, right, bottom] = bbox;
  const x = Math.max(0, left - SPOT_MARGIN), y = Math.max(0, top - SPOT_MARGIN);
  return {left: x, top: y, width: Math.min(1, right + SPOT_MARGIN) - x, height: Math.min(1, bottom + SPOT_MARGIN) - y};
}

// The part of its crop a spot's enlarged view shows beside the choices, as fractions of the crop: the box and what surrounds it (as
// much again each way, at least 6% and at most 15% of the crop's width, 4% to 10% of its height), kept inside the crop.
export function flagSpotView(bbox) {
  const [left, top, right, bottom] = bbox;
  const padX = Math.min(0.15, Math.max(0.06, right - left)), padY = Math.min(0.10, Math.max(0.04, bottom - top));
  const x = Math.max(0, left - padX), y = Math.max(0, top - padY);
  return {left: x, top: y, width: Math.min(1, right + padX) - x, height: Math.min(1, bottom + padY) - y};
}

// The outlines of one crop. Placed by the desk (`flagSpotBox`): the page allows no inline style.
export function flagSpotsMarkup(value, crop, h) {
  return (value.flag_review?.look || []).map((spot, i) => spot.crop !== crop ? "" :
    `<span class="grading-flag-spot" data-flag-spot="${i}" role="img" aria-label="Outlined to read: ${h.e(spot.label || "the marks to read")}"></span>`).join("");
}

// ONE LINE PER CAUSE (requirement, 2026-10-11; GRADING "Continuous review workspace"). One cause usually stood in the list two or three
// times in different words (the graders' disagreement as Faculty's flag, as the sentence naming the lines and as the team's own reason),
// and three shown sentences were often one cause. A response read with `?details=review` carries `reason_causes`: the sentences
// `reviewReasons` lists, gathered by cause (never for a hidden proposal). The callout then leads with one line a cause (its label, its
// first sentence, and "+N more" with the authors when it holds more) and keeps every sentence verbatim, under the same labels and with
// its author, in All recorded reasons. The server only gathers: its causes are drawn only while they hold exactly the listed
// sentences, each once. Anything else (a read without the key, a sentence it does not have) draws the plain list as before, so
// no recorded reason can go missing from the card.
export function reasonCauses(value, reasons = reviewReasons(value)) {
  const causes = Array.isArray(value.reason_causes) ? value.reason_causes : [];
  const texts = causes.flatMap(cause => (cause.reasons || []).map(reason => reason.text));
  return causes.length && causes.every(cause => cause.label && cause.reasons?.length) && texts.length === reasons.length
    && new Set(texts).size === texts.length && reasons.every(reason => texts.includes(reason)) ? causes : null;
}

const shortReason = reason => reason.length > 350 ? reason.slice(0, 347) + '…' : reason;
function causesMarkup(causes, total, h) {
  const line = cause => {
    const [lead, ...more] = cause.reasons, authors = [...new Set(cause.reasons.map(reason => reasonRole(reason.text)).filter(Boolean))];
    return `<dt data-reason-cause="${h.e(cause.key)}">${h.e(cause.label)}</dt><dd>${reviewReasonMarkup(shortReason(lead.text), h)}${more.length ? ` <span class="grading-reason-more">+${more.length} more${authors.length ? ` · ${authors.map(role => modelLabel(role, "", h)).join(", ")}` : ""}</span>` : ""}</dd>`;
  };
  const group = cause => `<dt>${h.e(cause.label)}</dt><dd><ul>${cause.reasons.map(reason => `<li>${reviewReasonMarkup(reason.text, h)}</li>`).join("")}</ul></dd>`;
  return {lines: `<dl class="review-said grading-reason-causes" data-reason-causes>${causes.map(line).join("")}</dl>`,
    all: `<details class="disclosure" data-reason-all><summary><span>All recorded reasons (${total})</span></summary><div class="disclosure-body"><dl class="review-said">${causes.map(group).join("")}</dl></div></details>`};
}

export function reasonsMarkup(value, h) {
  const reasons = reviewReasons(value);
  if (!reasons.length) return "";
  const decision = decisionPrompt(value), display = rubricDisplay(value.question?.rubric?.rules);
  const item = reason => `<li>${reviewReasonMarkup(reason, h)}</li>`;
  const causes = reasonCauses(value, reasons), drawn = causes && causesMarkup(causes, reasons.length, h);
  const short = reasons.slice(0, 3).map(shortReason);
  return `<aside class="callout grading-review-reasons" data-tone="warning" aria-label="Decision needed"><strong class="callout-title">Decision needed</strong>${flagReadingsMarkup(value, h)}<p>${h.e(decision.prompt)}</p>${drawn ? drawn.lines : `<ul class="grading-reasons-visible">${short.map(item).join("")}</ul>`}<div class="grading-actions">${decision.items.map(id => `<button type="button" class="text-button" data-review-line="${h.e(id)}">Inspect ${h.e(display.label(id))}</button>`).join("")}<button type="button" class="text-button" data-review-original>Inspect original scan</button></div>${drawn ? drawn.all : reasons.length > 3 || reasons.some(reason => reason.length > 350) ? `<details class="disclosure"><summary><span>All recorded reasons (${reasons.length})</span></summary><div class="disclosure-body"><ul>${reasons.map(item).join("")}</ul></div></details>` : ''}</aside>`;
}

// GRADING "WHO SEES A COMMENT": Gradescope gets an adjustment's points with the grade; its reason goes only as a batch line's wording
// (`batch_adjustments.comment_text`).
const ADJUSTMENT_SEEN = "Students see the points on Gradescope, not the reason. Only a line added to every grade shows its wording there.";

// A key's hint is shown on its button but is not part of the button's name (aria-keyshortcuts carries it).
const keyed = (action, label, keys) => `<button type="button" class="button" data-grading-action="${action}" aria-keyshortcuts="${keys}">${label}<kbd class="grading-key-hint" aria-hidden="true">${keys.replace("+", " ")}</kbd></button>`;

export function reviewMarkup(value, index, h, groupSize = null) {
  const q = value.question.content, rubric = value.question.rubric.rules;
  const display = rubricDisplay(rubric);
  const chosen = value.review || value.result, applications = chosen.applications || [], score = chosen.score?.score;
  const formId = `review-${value.id}`;
  // "verify boundary" asks you to check an uncertain template alignment while the grade waits for you. A final grade (yours, or
  // finalized automatically after every flag, the alignment one included, was settled) no longer asks it (2026-10-18).
  const settled = Boolean(value.review) || (value.acceptance && value.acceptance.kind !== "trial" && value.acceptance.active !== false && value.acceptance.final !== false);
  const crops = value.crops.map((crop, i) => `<figure><div class="grading-image" data-crop="${i}">${cropButton(crop.images.original.url, `original response crop ${i + 1}`, h)}<span class="grading-highlight" hidden></span>${flagSpotsMarkup(value, i, h)}</div><figcaption>Region ${i + 1}${crop.metadata.registered || settled ? "" : " · verify boundary"}</figcaption><details><summary>Enhanced views and source</summary><div class="grading-image-variants">${["grayscale", "contrast", "ink"].map(key => `<figure>${cropButton(crop.images[key].url, `${key} response view`, h)}<figcaption>${h.e(key)}</figcaption></figure>`).join("")}</div><a ${viewLink(value.source.regions[i].raster_url, `Original page ${value.source.regions[i].page}`, h.e)}>Open full original page</a>${value.source.template_id ? ` · <a ${viewLink(`/api/assignment-templates/${value.source.template_id}/pages/${value.source.regions[i].page}.png`, `Blank exam page ${value.source.regions[i].page}`, h.e)}>Open the blank exam’s page ${h.e(value.source.regions[i].page)}</a>` : ""}</details></figure>`).join("");
  const items = rubric.items.map(item => {
    const proposal = applications.find(a => a.rubric_item_id === item.id);
    const other = value.hidden || value.review ? "" : secondOpinion(value.result.signals, item.id, h);
    const historical = value.result.signals?.committee?.status === 'resolved';
    const explanation = proposal?.reason || proposal?.evidence?.length || proposal?.ambiguity || other;
    const explanationLabel = `${value.review ? "Decision details" : "AI explanation"}${other ? historical ? " · grading history" : " · graders disagree" : ""}${proposal?.ambiguity ? " · ambiguity to review" : ""}`;
    const labelId = `${formId}-item-${item.id}`;
    const row = `<span class="grading-rubric-number" aria-label="${h.e(display.label(item.id))}">${h.e(display.number(item.id))}.</span><span class="grading-rubric-item-text" id="${h.e(labelId)}"><span class="grading-rubric-points">${h.e(item.points)} point${Math.abs(item.points) === 1 ? "" : "s"}</span>${mathText(item.description, h)}</span>`;
    // The checkbox is a sibling of the native disclosure: neither the rubric
    // heading nor the expanded explanation is a checkbox label or click proxy.
    return `<div class="grading-rubric-choice${other && !historical ? " grading-rubric-disputed" : ""}"><input type="checkbox" name="item" value="${h.e(item.id)}" aria-label="Apply ${h.e(display.label(item.id))}" aria-describedby="${h.e(labelId)}" ${proposal?.applied ? "checked" : ""}>${explanation ? `<details class="grading-item-reason"><summary class="grading-rubric-row" title="${h.e(explanationLabel)}" aria-label="${h.e(explanationLabel)} for ${h.e(display.label(item.id))}: ${h.e(item.description)}">${row}<span class="grading-rubric-toggle" aria-hidden="true"></span></summary>${proposal?.reason ? `<p>${value.review ? mathText(proposal.reason, h) : proposalComment(value.result.signals, proposal.reason, h)}</p>` : ""}${proposal?.ambiguity ? `<p class="grading-warning">Ambiguous: ${value.review ? mathText(proposal.ambiguity, h) : modelComment(proposalRole(value.result.signals), proposal.ambiguity, h)}</p>` : ""}${other}${proposal?.evidence?.map((ev, n) => `<button type="button" class="text-button" data-evidence-item="${h.e(item.id)}" data-evidence-index="${n}">${mathText(ev.student_text, h)} ↗</button>`).join("") || ""}</details>` : `<div class="grading-rubric-row">${row}</div>`}</div>`;
  }).join("");
  const heading = groupSize === null ? `<h3 tabindex="-1" data-review-heading>${h.e(q.title)} · Response ${index + 1}</h3>` : `<div class="grading-response-title"><h3 tabindex="-1" data-review-heading>${h.e(q.title)}</h3><p class="supporting-copy"><span data-review-progress>${index + 1} of ${groupSize} in this group</span></p></div>`;
  return `<section class="grading-review-desk" aria-label="Response review"><div class="grading-heading grading-response-heading">${heading}<div class="grading-actions">${keyed("previous", "← Previous", "Alt+P")}${keyed("next", "Next →", "Alt+N")}</div></div>
    ${reasonsMarkup(value, h)}
    ${!value.current ? `<p class="grading-warning" role="alert">${h.e(value.error || "This proposal is not current.")}</p>` : ""}
    ${value.earlier_review ? `<p class="grading-warning" role="status" data-earlier-review>You decided this response under an earlier rubric, question or paper. That decision stays in its history but no longer counts. Review this new proposal.</p>` : ""}
    ${value.acceptance && !value.review ? `<p class="grading-warning" role="status">${value.acceptance.active === false ? 'Automatic finalization is suspended. This response needs your review.' : value.acceptance.final === false && value.acceptance.kind !== 'trial' ? 'Held: this question’s rubric changed since its finalization was confirmed (or the confirmation changed its guidance), so this grade is not final until you confirm the finalization again or review it.' : value.acceptance.basis === 'jev_tiebreak' ? 'Finalized automatically after a tie-breaking check settled the graders’ disagreement: its answer for every rubric line any grader ticked equals one grader’s complete assessment, which stands, and all checks passed (details are under the AI’s checks below). You can inspect or change this grade.' : value.acceptance.basis === 'jev_blank' ? 'Finalized automatically as left blank: the reader found every answer area empty and a second check of the same areas said the problem was left blank. You can inspect or change this grade.' : value.acceptance.basis === 'flag_review' ? 'Finalized automatically after the flag reviewer found sufficient support for the unchanged standing grade. Its reasons are under the AI’s checks below. You can inspect or change this grade.' : value.acceptance.basis === 'final_arbitrator' ? 'Finalized automatically by the final reviewer: the graders left this response flagged, and the final reviewer ruled on every flag (its ruling is under the AI’s checks below). You can inspect or change this grade.' : value.acceptance.basis === 'tag_team' ? 'Finalized automatically by two models in turn: the first graded the images, the second adopted or revised its assessment, and all applicable checks passed. You can inspect or change this grade.' : value.result?.signals?.jev_routing ? `Finalized automatically by the single model chosen for this problem’s difficulty (difficulty ${h.e(value.result.signals.jev_routing.difficulty)} of 5 · ${h.e(value.result.signals.jev_routing.endpoint)}). All applicable checks passed. You can inspect or change this grade.` : value.acceptance.basis === 'one_shot' ? 'Finalized automatically by your single grading model after all applicable checks passed. You can inspect or change this grade.' : value.acceptance.basis === 'choice_reader' ? `Finalized automatically by the multiple-choice reader: it read ${h.e(value.result?.signals?.choice?.choice || 'the marked option')} in both option orders, the question is multiple choice by its rubric, and all applicable checks passed. You can inspect or change this grade.` : value.acceptance.basis === 'flag_resolution' ? 'Finalized automatically after flag review: the flag review of the question ruled on every flag it was shown, and all checks passed (its ruling is under the AI’s checks below). You can inspect or change this grade.' : value.acceptance.basis === 'team_consensus' ? 'Finalized automatically: the grading team reached agreement and all checks passed. You can inspect or change this grade.' : value.acceptance.kind === "trial" ? "Trial-accepted: this is a test, not a final grade. Your review confirms or overturns it." : "Accepted automatically under your benchmark report. Overturning it suspends automatic acceptance for this question."}</p>` : ""}
    ${value.hidden ? `<p class="grading-warning">Blind review: grade this response yourself first. Revealing the AI decision now makes this review count as seen, not blind.</p>${h.button("reveal", "Show proposal for comparison")}` : ""}
    <div class="grading-evidence-layout"><section class="grading-evidence" tabindex="0" aria-label="Student work and reading"><h4>Student work <small>Click a crop to enlarge</small></h4>${crops}
      <section class="grading-transcript"><h4>Transcription</h4><div class="grading-transcript-preview" data-transcript-preview>${transcriptMarkup(chosen.transcript, h)}</div><details class="disclosure" data-transcript-editor><summary><span>Edit transcription</span></summary>${h.area("Visible student work — preserve written errors", "transcript", chosen.transcript.plain_text, `form="${h.e(formId)}"`)}<p class="supporting-copy">Save as draft, then choose Regrade with Cloud under Ask for another opinion to grade the corrected reading. The original AI reading is retained.</p></details></section>
      <details class="disclosure grading-solution"><summary><span>Question and solution</span></summary><h4>Problem</h4><div class="grading-source">${mathText(q.problem, h)}</div>${["expected_answer", "coordinator_solution", "instructor_solution", "worked_solution", "acceptable_methods"].filter(key => q[key]).map(key => `<h4>${h.e(key.replaceAll("_", " "))}</h4><div class="grading-source">${mathText(q[key], h)}</div>`).join("")}</details>${value.hidden ? "" : evidenceMarkup(value, h)}<details class="grading-panel grading-review-audit"><summary>Audit and recognition details</summary><h4>OCR disagreements</h4><pre class="grading-source">${h.e(JSON.stringify(value.recognition.consensus, null, 2))}</pre><h4>Independent OCR readings</h4>${value.recognition.candidates.map(c => `<strong>${h.e(c.provider)}</strong><div class="grading-source">${transcriptMarkup(c.transcript, h)}</div>`).join("")}<h4>Separate quality signals</h4><pre class="grading-source">${h.e(JSON.stringify(value.result.signals || {hidden: "Hidden until your blind review"}, null, 2))}</pre><p>Human decisions retained: ${value.history.length}</p>${value.review && value.review.result_id !== value.current_result_id ? '<p class="grading-warning">A new model proposal is available. Your earlier decision is retained.</p>' : ""}</details></section>
      <form id="${h.e(formId)}" class="grading-review-form grading-form"><header class="grading-rubric-heading"><h4 data-rubric-heading>${value.review ? "Rubric · your grade" : "Rubric · proposed grade"}</h4><output class="grading-score" data-score>${score === undefined || score === null ? "Select items to calculate a score" : `${h.e(score)} / ${h.e(q.max_points)}`}</output></header><div class="grading-review-scroll" role="region" tabindex="0" aria-label="Rubric and review details">
      <div class="grading-rubric-items">${items}</div>
      <details class="disclosure grading-review-notes" ${(value.review?.comment || chosen.score?.adjustments?.length) ? "open" : ""}><summary><span>Notes, adjustments and manual grade</span></summary>${h.area("Comment / manual grade reason", "comment", value.review?.comment || "", "", seenByMarkup("you"))}
      <details class="disclosure" ${(chosen.score?.adjustments?.length || 0) ? "open" : ""}><summary><span>Adjust points</span></summary>${h.field("Adjustment points", "adjustment", chosen.score?.adjustments?.reduce((sum, a) => sum + a.points, 0) || 0, "number", 'step="0.01"')}${h.field("Adjustment reason", "adjustment_reason", chosen.score?.adjustments?.map(a => a.reason).join("; ") || "")}<p class="supporting-copy" data-seen-by="adjustment">${ADJUSTMENT_SEEN}</p></details>
      <details class="disclosure"><summary><span>Manual grade</span></summary>${h.field("Manual score", "manual_score", score ?? 0, "number", `min="${rubric.floor}" max="${rubric.ceiling}" step="0.01"`)}${h.button("manual", "Record manual grade")}</details>${h.check("The model’s reasoning was wrong (kept with your decision for later evaluation and training)", "reasoning_wrong")}</details>

      <details class="disclosure"><summary><span>Cannot approve this response?</span></summary><div class="grading-actions">${h.button("unreadable", "Mark unreadable")}${h.button("rejected", "Reject proposal")}</div></details>
      <details class="disclosure"><summary><span>Ask for another opinion</span></summary><p class="supporting-copy">Your saved decision is kept.</p><div class="grading-actions">${h.button("regrade-standard", "Regrade with Cloud")}${h.button("regrade-read", "Read this response again")}</div></details><p class="grading-review-keys">Keys: <kbd>1</kbd>–<kbd>9</kbd> tick a rubric line (<kbd>0</kbd> then a digit for 10–19) · <kbd>Alt A</kbd> approve · <kbd>Alt N</kbd> next · <kbd>Alt P</kbd> previous · <kbd>?</kbd> all shortcuts</p></div><div class="grading-review-decision">${h.error}<div class="grading-decision-score">Grade to approve: <output data-decision-score>${score === undefined || score === null ? "Select rubric items" : `${h.e(score)} / ${h.e(q.max_points)}`}</output></div><div class="grading-actions"><button class="button primary" type="submit" aria-keyshortcuts="Alt+A" ${value.current ? "" : "disabled"}>Approve grade<kbd class="grading-key-hint" aria-hidden="true">Alt A</kbd></button>${h.button("draft", "Save as draft")}</div></div></form></div>
    </section>`;
}
