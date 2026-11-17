import {rubricDisplay} from "./grading-rubric-display.js";
import {mathText} from "./grading-math.js";
import {modelLabel, modelComment, reviewReasonMarkup, applicationLabel} from "./grading-model-comments.js";
import {COMPUTE} from "./grading-jobs.js";

// Role keys in plain words for the models line (FRONTEND: no raw codes); an unknown role keeps its key. Endpoint names are records.
const ROLE_NAMES = {primary: "Primary grader", second: "Second grader", verifier: "Additional reviewer", reader: "Reader", arbiter: "Final arbitrator",
  flag_reviewer: "Flag reviewer", choice_reader: "Multiple-choice reader"};
// What the independent checks found about one proposal, in the order an instructor needs it: what disagrees,
// what the mathematics says, who objected, then who ran. Pure markup from the retained signals; nothing is decided here.
const LEVEL = {low: ["done", "Low risk"], elevated: ["todo", "Elevated risk"], high: ["attention", "High risk"]};

export function riskChip(signals, h) {
  if (signals?.rubric_quality?.reasons?.length) return `<span class="grading-chip grading-chip-attention">${signals.rubric_quality.pairs.length ? 'Rubric exclusivity conflict' : 'Rubric review required'}</span>`;
  const [key, label] = LEVEL[signals?.risk?.level] || ["waiting", "Not rated"];
  return `<span class="grading-chip grading-chip-${key}">${h.e(label)}</span>`;
}

const secondName = opinion => opinion?.input_mode === "images_and_transcript" ? "Second grader · images and transcript" : "Second grader · transcript only";
// The independent grader receives no primary decisions, whether it sees images or text.
export function secondOpinion(signals, itemId, h) {
  if (signals?.panels?.length) {
    return signals.panels.flatMap(panel => Object.entries(panel.opinions).flatMap(([role, opinion]) => {
      const comparison = role === 'verifier' ? Object.values(panel.decision.third_comparisons || {}).some(c => c.disagreements.includes(itemId)) : panel.decision.pair?.disagreements.includes(itemId);
      const item = opinion.applications?.find(a => a.rubric_item_id === itemId);
      if (!comparison || !item) return [];
      return [`<p class="supporting-copy">${h.e(panel.tier === 'cloud_standard' ? 'Cloud' : 'High Assurance')} · ${modelLabel(role, `${{primary: 'Primary', second: 'Secondary', verifier: 'Additional reviewer'}[role]} (${opinion.endpoint}):`, h)} ${applicationLabel(item.applied, h)}. ${modelComment(role, item.reason || '', h)}</p>`];
    })).join('');
  }
  const other = signals?.second?.applications?.find(a => a.rubric_item_id === itemId);
  if (!other || !signals.risk?.components?.second?.disagreements?.includes(itemId)) return "";
  return `<p class="grading-warning">${modelLabel("second", `${secondName(signals.second)} (${signals.second.lineage}):`, h)} ${applicationLabel(other.applied, h, other.applied ? "would apply" : "would not apply")} this line. ${modelComment("second", other.reason || "", h)}</p>`;
}

// What the instructor's narrow review policy (and direct reading) took the power to block from: every such check still RAN and
// is shown, apart from what holds the response back. `risk.waived` (risk.py) and the waived reading state (jobs.py `scored`).
const STATES = {ocr_review: "the reading would have waited for your review", both: "the reading and the grading would have waited for your review",
  grading_review: "the grading would have waited for your review"};
export function waivedMarkup(risk, h) {
  const waived = risk?.waived;
  if (!waived) return "";
  const policy = [...new Set([...(waived.blocks || []), ...(waived.reasons || []), ...(waived.review_state_reasons || [])])];
  const direct = [...(waived.direct_reading?.blocks || [])];
  const chosen = waived.choice_reader?.blocks || [];
  const state = waived.state && waived.state !== "proposed" ? STATES[waived.state] || `its state would have been ${waived.state}` : "";
  const list = rows => `<ul>${rows.map(row => `<li>${reviewReasonMarkup(row, h)}</li>`).join("")}</ul>`;
  const parts = [];
  if (policy.length || state) parts.push(`<p><strong>Waived by your review policy</strong>${state ? ` (${h.e(state)})` : ""}. These checks ran and are kept here; under your policy they do not hold the response back.</p>${policy.length ? list(policy) : ""}`);
  if (direct.length) parts.push(`<p><strong>Waived because the reader read the paper directly</strong>: no independent reading stood beside it, so these checks are recorded, not applied.</p>${list(direct)}`);
  if (chosen.length) parts.push(`<p><strong>Waived because the multiple-choice reader graded it</strong>: this question is multiple choice by its rubric, so the marked option alone decides, without OCR or a second grader.</p>${list(chosen)}`);
  return parts.length ? `<details data-waived-checks><summary>Waived checks (${h.e(policy.length + direct.length + chosen.length)})</summary>${parts.join("")}</details>` : "";
}

export function evidenceMarkup(value, h) {
  const signals = value.result?.signals;
  const display = rubricDisplay(value.question?.rubric?.rules);
  if (!signals?.risk) return "";
  const risk = signals.risk, c = risk.components, failed = (signals.symbolic?.checks || []).filter(check => check.result === "fail");
  const blocks = [...risk.blocks, ...(signals.rubric_quality?.reasons || [])], waived = waivedMarkup(risk, h);
  const passed = (signals.symbolic?.checks || []).filter(check => check.result === "pass").length;
  const rows = [];
  if (signals.jev_routing) {
    const r = signals.jev_routing;
    rows.push(`<li><strong>Jev Routing</strong>: difficulty ${h.e(r.difficulty)} of 5 → ${h.e(r.endpoint)} in One-shot mode. Jev assessed this problem’s ${r.transcription?.provider === "mathpix" ? "automatic Mathpix template transcription" : "transcription"} and approved rubric. Its score was ${h.e(Number(r.score).toFixed(2))} of 5, rounded to the nearest difficulty (halfway goes higher).</li>`);
  }
  if (signals.panels?.length) for (const panel of signals.panels) {
    const d = panel.decision, title = panel.tier === 'cloud_standard' ? 'Cloud team' : 'High Assurance team';
    const opinions = Object.entries(panel.opinions).map(([role, opinion]) => `<li>${modelLabel(role, `${{primary: 'Primary', second: 'Secondary', verifier: 'Additional reviewer'}[role]} · ${opinion.endpoint}:`, h)} ${opinion.failed ? `failed — ${h.e(opinion.error)}` : `selected ${h.e(opinion.applications.some(a => a.applied) ? `items ${display.list(opinion.applications.filter(a => a.applied).map(a => a.rubric_item_id))}` : 'no items')} · score ${h.e(opinion.score?.score ?? 'unscorable')}`}</li>`).join('');
    rows.push(`<li><strong>${title}: ${d.status === 'resolved' ? 'agreement reached' : 'unresolved'}</strong><p>${h.e(d.reason)}${d.third_required ? ` The additional reviewer ${d.third_independent ? 'graded independently without earlier decisions' : 'was shown both earlier assessments'}.` : signals.arbiter && signals.scope?.cloud_standard ? ' The disagreement went to the final arbitrator.' : d.status === 'resolved' ? ' No additional reviewer was needed.' : ' No additional reviewer was called.'}</p><details><summary>Model assessments</summary><ul>${opinions}</ul></details></li>`);
  }
  if (signals.rubric_quality?.pairs?.length) {
    const item = id => {
      const description = value.question?.rubric?.rules?.items.find(i => i.id === id)?.description;
      return `<strong>${h.e(display.label(id))}</strong>${description ? ` — ${mathText(description, h)}` : ''}`;
    };
    rows.push(`<li><strong>Confirmed rubric exclusions violated</strong><ul>${signals.rubric_quality.pairs.map(([a, b]) => `<li>${item(a)}<br>Cannot be selected together with ${item(b)}.</li>`).join('')}</ul>Both were selected. Review the response individually; the calculated score is unchanged.</li>`);
  }
  // Final Arbitrator Mode: what the chosen model decided about a flagged response, why, and its ruling on every flag it was shown.
  const arbiter = signals.arbiter;
  if (arbiter) {
    const decided = {adopted: `adopted the ${h.e(arbiter.adopted)} assessment as the accurate final grade`, revised: "found no earlier assessment accurate and posed its own rubric selections",
      unreliable: "found that no reliable grade can be determined, so this response stays with you", unsettled: "named a grade but a flag still stands, so this response stays with you",
      failed: signals.tag_team ? "could not complete, so the first assessment waits for your review" : "could not complete, so this response stays exactly as the grading team left it",
      not_asked: "was not called for this response"}[arbiter.outcome] || h.e(arbiter.outcome);
    const rulings = (arbiter.flags || []).map(flag => `<li>${flag.resolved ? "Resolved" : "<strong>Still stands</strong>"}: ${reviewReasonMarkup(flag.text, h)}${flag.note ? `<br><small>${modelComment(signals.tag_team ? "tag_team_reviewer" : "arbiter", flag.note, h)}</small>` : ""}</li>`).join("");
    const open = (arbiter.open_flags || []).map(text => `<li>${reviewReasonMarkup(text, h)}</li>`).join("");
    rows.push(`<li>${modelLabel("arbiter", signals.tag_team ? "Tag-Team reviewer" : "Final arbitrator", h)}${arbiter.rule === "final-arbitrator-v2" ? " (the additional reviewer)" : ""} (${h.e(arbiter.model || "model not recorded")}${arbiter.lineage ? `, ${h.e(arbiter.lineage)}` : ""}) ${decided}.${arbiter.reason ? ` ${arbiter.outcome === "not_asked" ? mathText(arbiter.reason, h) : modelComment("arbiter", arbiter.reason, h)}` : ""}${arbiter.standing_before && arbiter.outcome === "adopted" && arbiter.standing_before !== arbiter.adopted ? ` The team’s own proposal had been ${h.e(arbiter.standing_before)}.` : ""}${rulings ? `<details><summary>Its ruling on each flag (${(arbiter.flags || []).length})</summary><ul>${rulings}</ul></details>` : ""}${open ? `<br>Not settled by the ruling:<ul>${open}</ul>` : ""}</li>`);
  }
  const flagReview = signals.flag_review;
  if (flagReview) {
    const said = {finalized: 'upheld the unchanged standing grade', manual_review: 'found that the flags require manual review',
      unsettled: 'left an unresolved flag for manual review', failed: 'could not complete; manual review is required',
      not_asked: 'was not asked about this response'}[flagReview.outcome] || flagReview.outcome;
    const flags = (flagReview.flags || []).map(flag => `<li>${flag.resolved ? 'Resolved' : '<strong>Still stands</strong>'}: ${reviewReasonMarkup(flag.text, h)}<br><small>${modelComment('flag_reviewer', flag.note || '', h)}</small></li>`).join('');
    const model = flagReview.model || flagReview.outcome !== 'not_asked' ? ` (${h.e(flagReview.model || 'model not recorded')})` : '';  // Nothing was sent when it was not asked.
    rows.push(`<li>${flagReview.outcome === "not_asked" ? "<strong>Flag reviewer</strong>" : modelLabel("flag_reviewer", "Flag reviewer", h)}${model} ${h.e(said)}. ${flagReview.outcome === 'not_asked' ? mathText(flagReview.reason || '', h) : modelComment('flag_reviewer', flagReview.reason || '', h)}${flags ? `<details><summary>Review of each flag</summary><ul>${flags}</ul></details>` : ''}</li>`);
  }
  // The Jev tie-breaker, in the arbitrator's place: one calibrated yes/no per rubric line any model ticked. It never grades.
  const jev = signals.jev;
  if (jev) {
    const percent = value => value === null || value === undefined ? "not answered" : `${Math.round(value * 100)}%`;
    const said = {resolved: `broke the grading team’s tie: the ${h.e(jev.adopted || "")} assessment stands`, unbroken: "could not break the tie, so this response stays with you",
      failed: "could not complete, so this response stays exactly as the grading team left it", not_asked: "was not asked about this response"}[jev.outcome] || h.e(jev.outcome);
    const lines = display.ordered(jev.items || [], item => item.rubric_item_id).map(item => `<li><strong>${h.e(display.label(item.rubric_item_id))}</strong>: ${item.choice === "yes" ? applicationLabel(true, h, "applies") : item.choice === "no" ? applicationLabel(false, h, "does not apply") : item.probability === null || item.probability === undefined ? "not answered" : "<strong>not sure enough</strong>"} (${percent(item.probability)} that it applies). Ticked by ${h.e((item.ticked_by || []).join(", ") || "no complete assessment")}.</li>`).join("");
    rows.push(`<li>${modelLabel("jev", "Jev tie-breaker", h)} ${said}${jev.confidence ? ` (an answer counts at ${percent(jev.confidence)} or more, either way)` : ""}.${jev.reason ? ` ${jev.outcome === "unbroken" ? "The reason: " : ""}${jev.outcome === "not_asked" ? mathText(jev.reason, h) : modelComment("jev", jev.reason, h)}` : ""}${jev.outcome === "resolved" && jev.standing_before && jev.standing_before !== jev.adopted ? ` The team’s own proposal had been ${h.e(jev.standing_before)}.` : ""} It reads text only, poses no ticks of its own, and settles nothing but the disagreement.${lines ? `<details><summary>Its answer for each rubric line a model ticked (${jev.items.length})</summary><ul>${lines}</ul></details>` : ""}</li>`);
  }
  // The instructor's rule: the full-credit line excludes every other line. What the model also ticked stays on the record.
  // A PART's full-credit line is read the same way first (rule v3), and may then be one of the lines read as the question's.
  for (const part of signals.normalized?.parts || []) rows.push(`<li><strong>Read as the part’s full-credit line alone</strong>: the AI ticked ${h.e(display.label(part.line))} together with ${h.e(`items ${display.list(part.dropped)}`)} of the same part. ${h.e(display.label(part.line))} is worth its whole part and your finalization keeps it apart from every other line of that part, so only it is kept; the score is the same.</li>`);
  if (signals.normalized?.dropped?.length) rows.push(signals.normalized.route === "steps"
    ? `<li><strong>Read as the full-credit line</strong>: the AI ticked the step lines ${h.e(`items ${display.list(signals.normalized.dropped)}`)}, which add up to the question’s full value. By your rule that is the full-credit line ${h.e(display.label(signals.normalized.line))}, so only it is proposed; the score is the same.</li>`
    : `<li><strong>Read as the full-credit line alone</strong>: the AI ticked ${h.e(display.label(signals.normalized.line))} together with ${h.e(`items ${display.list(signals.normalized.dropped)}`)}. By your rule the full-credit line excludes every other line, so only it is proposed; the score is the same.</li>`);
  if (c.second && !signals.panels?.length) rows.push(`<li>${modelLabel("second", secondName(c.second), h)} (${h.e(c.second.lineage || "lineage not recorded")}${c.votes?.basis === "assessments" ? ", separate assessment" : c.second.independent ? ", independent of the primary grader" : ", counted as one vote under the recorded policy"}): ${c.second.agrees ? "agrees with every proposed line" : `disagrees on ${h.e(`items ${display.list(c.second.disagreements)}`)}${c.second.other_score === null ? "" : ` · it would score ${h.e(c.second.other_score)}`}`}.</li>`);
  else if (signals.second?.failed) rows.push(`<li>${modelLabel("second", "Second grader", h)} could not complete: ${h.e(signals.second.error)}</li>`);
  const original = c.escalation?.rubric_disagreement;
  if (original && c.second?.agrees) rows.push(`<li><strong>Original grading disagreement</strong>: the first two graders disagreed on ${h.e(`items ${display.list(original.disagreements)}`)}${original.same_score ? ', even though their total scores matched' : ''}. Later agreement from High Assurance does not clear this disagreement; review these selections individually.</li>`);
  if (signals.second?.reading_issues?.length) rows.push(`<li>${modelLabel("second", "Second grader’s reading concerns", h)}<ul>${signals.second.reading_issues.map(issue => `<li>${h.e(issue.kind)}${issue.line_ids.length ? ` (${h.e(issue.line_ids.join(", "))})` : ""}: ${modelComment("second", issue.description, h)}</li>`).join("")}</ul></li>`);
  if (failed.length || passed) rows.push(`<li><strong>Symbolic checks of the written work</strong>: ${failed.length ? `${failed.length} contradiction${failed.length === 1 ? "" : "s"}` : "no contradiction"}${passed ? `, ${passed} step${passed === 1 ? "" : "s"} verified` : ""}.${failed.length ? `<ul>${failed.map(check => `<li>${mathText(check.explanation, h)}</li>`).join("")}</ul>` : ""}</li>`);
  if (signals.verifier?.failed) rows.push(`<li>${modelLabel("verifier", "Adversarial reviewer", h)} could not complete: ${h.e(signals.verifier.error)}</li>`);
  else if (signals.verifier) rows.push(`<li>${modelLabel("verifier", "Adversarial reviewer", h)} (${h.e(signals.verifier.lineage)}): ${signals.verifier.objections.length ? `<ul>${signals.verifier.objections.map(o => `<li>${h.e(o.severity)}${o.rubric_item_id ? ` · ${h.e(display.label(o.rubric_item_id))}` : ""}: ${modelComment("verifier", o.claim, h)} <em>${modelComment("verifier", o.evidence, h)}</em></li>`).join("")}</ul>` : "no material contradiction found."}${signals.verifier.transcript_disagreements?.length ? `<p>Image differs from the transcript: ${modelComment("verifier", signals.verifier.transcript_disagreements.join("; "), h)}</p>` : ""}</li>`);
  const resolution = signals.reading_resolution;
  if (resolution?.resolved) rows.push(`<li><strong>OCR differences resolved</strong>: ${h.e(resolution.resolved)} of ${h.e(resolution.total)} settled by a clear image reading supported by a complete OCR candidate, or by your saved correction. Original readings remain in the audit.</li>`);
  if (c.rubric?.conditions?.length) rows.push(`<li><strong>Rubric conditions</strong><ul>${c.rubric.conditions.map(condition => `<li>${h.e(display.label(condition.item_id))}: ${h.e({enforced: "enforced by approved structured rules", inactive: "restriction belongs to an unselected item", needs_review: "needs your judgment"}[condition.state])}</li>`).join("")}</ul></li>`);
  if (c.ocr.disagreements) rows.push(`<li><strong>Unresolved reading differences</strong> in ${h.e(c.ocr.disagreements)} place${c.ocr.disagreements === 1 ? "" : "s"}; see the readings below.</li>`);
  if (signals.choice) rows.push(`<li><strong>Read by the multiple-choice reader</strong>: ${h.e(signals.choice.choice)} · ${signals.choice.orders_agree ? "both option orders" : "the option orders disagree"} · confidence ${h.e(Number(signals.choice.confidence).toFixed(2))}.</li>`);
  rows.push(`<li><strong>Final answer</strong>: ${h.e({match: "matches your key", mismatch: "does not match your key", unknown: "not checked against a key"}[c.answer.check])} · ${h.e({shown: "work shown", thin: "little work", none: "no work visible"}[c.answer.work] || c.answer.work)}${c.answer.readers_agree === false ? " · only one reading shows it" : ""}. A match is evidence, never credit.</li>`);
  const escalation = signals.escalation?.escalated ? `<p><strong>Sent once for an extra check</strong> (High Assurance), because: ${signals.escalation.reasons.map(reason => reviewReasonMarkup(reason, h)).join(" ")}</p>` : "";
  const models = Object.entries(signals.models || {}).map(([role, m]) => `${ROLE_NAMES[role] || role}: ${m.endpoint}${m.lineage ? ` (${m.lineage})` : ""}`).join(" · ");
  const acceptanceNote = signals.choice ? 'The multiple-choice reader grades a multiple-choice question (its rubric is the drafted two-line shape) from the marked option alone. A confident read with all applicable checks passing finalizes automatically; anything else is graded as written work.' : signals.tag_team ? 'Tag-Team grades in one shot, then its reviewer checks every assessment. The reviewer’s ruling and all applicable checks must pass to finalize automatically.' : signals.one_shot ? 'One-shot uses one model and no independent OCR or second grader. Clean grades finalize automatically; doubts require your review.' : signals.committee ? 'Team agreement with all checks passing finalizes the grade automatically. Unresolved decisions or failed checks require your review.' : 'A proposal still needs approval unless automatic acceptance was explicitly enabled for its exact scope.';
  return `<details class="grading-panel" data-independent-checks ><summary>Independent checks ${riskChip(signals, h)}${c.second?.disagreements?.length && signals.committee?.status !== "resolved" ? ` · ${c.second.disagreements.length} rubric disagreements` : ""}${failed.length ? ` · ${failed.length} mathematical contradictions` : ""}</summary><ul class="grading-rule-list">${rows.join("")}</ul>${escalation}${blocks.length ? `<details><summary>Why this would not be accepted unseen (${h.e(blocks.length)})</summary><ul>${blocks.map(b => `<li>${reviewReasonMarkup(b, h)}</li>`).join("")}</ul></details>` : waived ? '<p class="supporting-copy">Nothing your review policy keeps argues against this proposal. The checks it waives are listed below.</p>' : '<p class="supporting-copy">Nothing in the evidence argues against this proposal.</p>'}${waived}<p class="supporting-copy">Risk labels and model confidence are review signals, not measured probabilities that a grade is correct. ${acceptanceNote}</p>${models ? `<p class="supporting-copy">${h.e(COMPUTE[signals.tier] || signals.tier || "")} · ${h.e(models)} · ${c.votes?.basis === "assessments" ? "agreeing assessments" : "recorded family votes"}: ${h.e(c.votes.count)}.</p>` : ""}</details>`;
}
