// GRADING: one rubric rule settled on several versions at once (requirement, 2026-10-03: a rule settled on one version goes to the same
// line of the others, in their own values, each confirmed by the instructor). Shared by the review workspace's concern editor and the
// flag review's proposed clarifications. Saving is one POST .../rubric-quality/clarify for every question it touches: one new
// confirmation that keeps every other question's entry. Nothing here approves a response or grades anything again.
import {rubricDisplay} from "./grading-rubric-display.js";
import {mathText, renderMath} from "./grading-math.js";
import {passedOn, namesValues} from "./grading-rubric-links.js";
import {versionTitle} from "./grading-guide.js";

const CERTIFIED = "These grades are certified. Changing these rules sends the questions' automatic grades back to review. Continue? Certify again afterwards.";

/** Why this plan's finalization cannot be clarified from here ("" when it can). */
export function notClarifiable(status) {
  const check = status?.check;
  if (status?.state !== "confirmed" || !status.review) return "The rubric finalization is not confirmed for the current rubrics, so clarify the rule there.";
  if (check && (["pending", "running"].includes(check.status) || check.id !== status.review.check_id)) return "A newer rubric check waits for you, so clarify the rule in the rubric finalization.";
  return "";
}

/** The confirmed entry of a question in `status`, or null. */
export const keptEntry = (status, qid) => {
  const q = status?.context?.questions.find(item => item.question_id === qid);
  return q ? status.review?.policy.questions.find(row => row.question_id === qid && row.rubric_id === q.rubric_id) || null : null;
};

/**
 * Saves `changes`: Map question_id -> {clarifications: {line: text}, pairs?}. Lines not named keep their confirmed text; a question
 * without `pairs` keeps its confirmed pairs. Returns the server's answer, or null when the instructor declined the certified warning.
 */
export async function saveClarifications(h, plan, status, changes, certified = false) {
  const questions = [];
  for (const [qid, change] of changes) {
    const q = status.context.questions.find(item => item.question_id === qid), kept = keptEntry(status, qid);
    if (!q || !kept) throw new Error("A question changed since the finalization was read. Refresh and try again.");
    questions.push({question_id: qid, rubric_id: q.rubric_id, clarifications: {...(kept.clarifications || {}), ...change.clarifications}, pairs: change.pairs || kept.pairs || []});
  }
  if (!questions.length) throw new Error("Choose at least one version.");
  if (certified && !window.confirm(CERTIFIED)) return null;
  return h.api(`/api/grading/assignments/${plan.id}/rubric-quality/clarify`, "POST", {version: status.version, review_id: status.review.id, questions, confirm_review: true});
}

/** A version's line, for labels: "Version B · Question 3 · Item 11". */
export function lineLabel(status, plan, qid, line) {
  const q = plan.questions.find(item => item.question_id === qid), context = status.context.questions.find(item => item.question_id === qid);
  return `${q ? versionTitle(q) : context?.title || "Question"} · ${rubricDisplay(context?.rubric).label(line)}`;
}

/**
 * A flag review's proposed clarification, added to the finalization for its own version and, when it worded them, the same line of
 * the other versions: one text box per version, prefilled with that line's confirmed clarification followed by the proposal, each
 * ticked; the instructor edits and saves them together.
 */
export async function adoptProposal(plan, h, source, {active = () => true, certified = false, onSaved = async () => {}} = {}) {
  const status = await h.api(`/api/grading/assignments/${plan.id}/rubric-quality`);
  if (!active()) return;
  const why = notClarifiable(status);
  if (why) { h.toast(why, true); return; }
  const rows = [{question_id: source.question_id, line_id: source.line_id, proposal: source.clarification},
    ...(source.linked || []).map(other => ({question_id: other.question_id, line_id: other.line_id, proposal: other.clarification}))]
    .filter(row => keptEntry(status, row.question_id));
  const field = (row, index) => {
    const kept = keptEntry(status, row.question_id).clarifications?.[row.line_id] || "";
    const text = passedOn("", row.proposal, kept).text;
    const line = status.context.questions.find(item => item.question_id === row.question_id).rubric.items.find(item => item.id === row.line_id);
    return `<fieldset class="grading-clarify-version"><legend><label class="grading-check"><input type="checkbox" name="version" value="${index}" checked> ${h.e(lineLabel(status, plan, row.question_id, row.line_id))}</label></legend>
      <p class="supporting-copy">${line ? mathText(line.description, h) : ""}</p>
      <label class="field"><span>Clarification for the AI graders</span><textarea name="text:${index}" rows="4" maxlength="2000">${h.e(text)}</textarea></label></fieldset>`;
  };
  const form = h.dialog("Add this rule to the rubric", rows.length > 1 ? `The same line on ${rows.length} versions, each in its own values. Untick a version to leave it as it is.` : "Edit it, then save.",
    `${rows.map(field).join("")}<p class="supporting-copy">Each box starts with the line's confirmed clarification followed by the proposal. Saving records these questions' rules in the rubric finalization; other questions keep theirs, and their automatic grades go back to review. Nothing is graded again until you ask.</p>`,
    "Save the rule", async (values, _, live) => {
      const changes = new Map();
      for (const index of values.getAll("version")) {
        const row = rows[Number(index)], text = String(values.get(`text:${index}`) || "").trim();
        const change = changes.get(row.question_id) || {clarifications: {}};
        change.clarifications[row.line_id] = text;
        changes.set(row.question_id, change);
      }
      const answer = await saveClarifications(h, plan, status, changes, certified);
      if (!answer || !live()) return;
      h.close();
      const suspended = answer.confirmation?.suspended || 0;
      h.toast(`Rule saved on ${changes.size} version${changes.size === 1 ? "" : "s"}.${suspended ? ` ${suspended} automatic grade${suspended === 1 ? "" : "s"} went back to review.` : ""} Grade the affected responses again from the review.`);
      await onSaved(answer);
    });
  if (form) renderMath(form);
}

/** Under an edited line in the concern editor: what goes to each linked version's line (the added text only, after its own rule). */
export function siblingPreview(before, after) {
  const passed = passedOn(before, after, "");
  return passed.kind === "rewrite" ? {ok: false, text: "You rewrote this rule, so nothing goes to the other versions: clarify them yourself."}
    : passed.kind === "none" ? {ok: false, text: ""}
    : {ok: true, text: `Adds “${passed.added}” after each ticked version’s own rule.${namesValues(passed.added) ? " It names values: check they hold on each version." : ""}`};
}
