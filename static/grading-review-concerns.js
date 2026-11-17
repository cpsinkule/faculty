// GRADING: a question's shared rubric concerns, settled beside the review desk (requirement, 2026-10-03). The concern names rubric lines; the instructor clarifies those lines (and what they cannot be combined with) here, which
// records a confirmation of this one question (POST .../rubric-quality/clarify; every other question keeps its confirmed entry), and
// then sends only the waiting responses that raised a concern to be graded again, as an ordinary batch (preview, then jobs). Nothing
// here approves a response: the regraded ones come back to the review like any other proposal. When the finalization is not in a
// state this can clarify (not confirmed, a check waiting), it links to the rubric finalization as before.
import {rubricDisplay} from "./grading-rubric-display.js";
import {mathText, renderMath} from "./grading-math.js";
import {loadLinks, linkIndex, partners, passedOn, LINK_SEP} from "./grading-rubric-links.js";
import {notClarifiable, keptEntry, saveClarifications, lineLabel, siblingPreview} from "./grading-clarify.js";

const CERTIFIED = "These grades are certified. Changing this question's rule sends its automatic grades back to review, and grading responses again replaces decisions where every check passes. Continue? Certify again afterwards.";
const count = (n, noun) => `${Number(n).toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;
// An exclusion pair's canonical key: its two line ids in order, joined.
const exclusionKey = pair => [...pair].sort().join(LINK_SEP);

// Graded again the way the plan's latest batch graded: a Cloud batch is stored as mode "nebius" with its tier (grading/config.py),
// a local one by its mode. No batch yet, or a removed scheme, is Cloud.
export function gradingMode(plan) {
  const mode = job => job.mode === "nebius" ? (job.tier === "cloud_standard" ? "cloud_standard" : "") : ["local", "hybrid"].includes(job.mode) ? job.mode : "";
  const jobs = (plan.activity?.jobs || []).filter(job => mode(job) && job.operation !== "rescore");
  const latest = jobs.reduce((a, b) => (!a || String(b.created_at) > String(a.created_at) ? b : a), null);
  return latest ? mode(latest) : "cloud_standard";
}

/** The waiting responses of a question that raised a rubric concern (never a blind proposal: its reasons are not shown). */
export function concernResponses(entry) {
  return (entry?.groups || []).filter(g => g.kind !== "hidden").flatMap(g => g.members).filter(m => (m.reason_kinds || []).includes("rubric")).map(m => m.id);
}

/** Wires the clarify and regrade controls inside `host` (the concerns disclosure's body). */
export function bindConcerns(host, plan, entry, h, {active, certified, onRegraded}) {
  const editor = host.querySelector("[data-concern-editor]");
  const regrade = host.querySelector("[data-concern-regrade]");
  const finalization = `#grading/${plan.id}?tab=answer-key&focus=${encodeURIComponent(entry.question_id)}&open=quality`;
  let status = null;

  host.querySelector("[data-concern-clarify]")?.addEventListener("click", event => h.run(event.currentTarget, async () => {
    const [read, links] = await Promise.all([h.api(`/api/grading/assignments/${plan.id}/rubric-quality`), loadLinks(h, plan.id)]);
    status = read;
    if (!active()) return;
    const q = status.context?.questions.find(item => item.question_id === entry.question_id);
    const kept = keptEntry(status, entry.question_id);
    const why = notClarifiable(status) || (!kept ? "The rubric finalization does not cover this question's rubric, so clarify the rule there." : "");
    if (why) {
      editor.innerHTML = `<p class="grading-warning" role="status">${h.e(why)} <a href="${h.e(finalization)}">Open the rubric finalization</a></p>`;
      editor.hidden = false;
      return;
    }
    // The same line on the problem's other versions ("same" links), for the rule to go there too (2026-10-03).
    const index = linkIndex(links.sets), siblings = id => partners(index, q.question_id, id).filter(line => keptEntry(status, line.question_id))
      .map(line => ({...line, label: lineLabel(status, plan, line.question_id, line.item_id)}));
    editor.innerHTML = editorMarkup(q, kept, new Set((entry.rubric_doubts || []).flatMap(doubt => doubt.items)), h, siblings);
    editor.hidden = false;
    renderMath(editor);
    editor.querySelectorAll("[data-concern-pair]").forEach(box => box.addEventListener("change", () => {
      editor.querySelectorAll("[data-concern-pair]").forEach(other => {
        if (other.dataset.concernPair === box.dataset.concernPair) other.checked = box.checked;
      });
    }));
    const preview = area => {
      const slot = editor.querySelector(`[data-concern-siblings="${CSS.escape(area.dataset.concernLine)}"]`);
      if (!slot) return;
      const said = siblingPreview(kept.clarifications?.[area.dataset.concernLine] || "", area.value);
      slot.hidden = !said.text;
      slot.querySelector("[data-concern-sibling-preview]").textContent = said.text;
      slot.querySelectorAll("[data-concern-sibling]").forEach(box => { box.disabled = !said.ok; });
    };
    editor.querySelectorAll("[data-concern-line]").forEach(area => area.addEventListener("input", () => preview(area)));
    editor.querySelector("textarea")?.focus();
    editor.querySelector("[data-concern-save]").onclick = event => h.run(event.currentTarget, () => save(q, kept));
    editor.querySelector("[data-concern-cancel]").onclick = () => { editor.hidden = true; editor.innerHTML = ""; };
  }));

  // Only the named lines are edited; every other clarification and exclusion is sent as it stands. A ticked linked line of another
  // version gets only what was ADDED here, after its own rule (`passedOn`); a rewrite goes nowhere.
  async function save(q, kept) {
    const clarifications = {};
    editor.querySelectorAll("[data-concern-line]").forEach(area => { clarifications[area.dataset.concernLine] = area.value.trim(); });
    const shown = new Set([...editor.querySelectorAll("[data-concern-pair]")].map(box => box.dataset.concernPair));
    const pairs = (kept.pairs || []).filter(pair => !shown.has(exclusionKey(pair)));
    const selected = new Set([...editor.querySelectorAll("[data-concern-pair]:checked")].map(box => box.dataset.concernPair));
    selected.forEach(key => pairs.push(key.split(LINK_SEP)));
    const changes = new Map([[q.question_id, {clarifications, pairs}]]);
    editor.querySelectorAll("[data-concern-sibling]:checked:not(:disabled)").forEach(box => {
      const [line, qid, iid] = box.dataset.concernSibling.split(LINK_SEP), before = kept.clarifications?.[line] || "";
      const passed = passedOn(before, clarifications[line], keptEntry(status, qid)?.clarifications?.[iid] || "");
      if (passed.kind !== "add") return;
      const change = changes.get(qid) || {clarifications: {}};
      change.clarifications[iid] = passed.text;
      changes.set(qid, change);
    });
    const answer = await saveClarifications(h, plan, status, changes, certified);
    if (!answer || !active()) return;
    const suspended = answer.confirmation?.suspended || 0, others = changes.size - 1;
    h.toast(`Rule saved for this question${others ? ` and the same line on ${count(others, "other version")}` : ""}.${suspended ? ` ${count(suspended, "automatic grade")} went back to review.` : ""} Grade the affected responses again to apply it.`);
    editor.hidden = true;
    editor.innerHTML = "";
    regrade?.classList.add("primary");
    regrade?.focus();
  }

  regrade?.addEventListener("click", event => h.run(event.currentTarget, async () => {
    const ids = concernResponses(entry);
    if (!ids.length) return;
    if (certified && !window.confirm(CERTIFIED)) return;
    const command = {question_ids: [entry.question_id], response_ids: ids, selection: "all", mode: gradingMode(plan), operation: "grade"};
    const preview = await h.api(`/api/grading/assignments/${plan.id}/preview`, "POST", command);
    if (!active()) return;
    const sent = preview.eligible?.length || 0;
    if (!sent) { h.toast(`None of these responses can be graded again: ${[...new Set((preview.skipped || []).map(s => s.reason))].join(" ") || "nothing is eligible."}`, true); return; }
    const cost = preview.estimate?.estimated_cost;
    if (preview.skipped?.length && !window.confirm(`${count(preview.skipped.length, "response")} cannot be graded again (${[...new Set(preview.skipped.map(s => s.reason))].join(" ")}). Grade the other ${sent}?`)) return;
    await h.api(`/api/grading/assignments/${plan.id}/jobs`, "POST", {...command, fingerprint: preview.fingerprint, request_id: crypto.randomUUID()});
    if (!active()) return;
    h.toast(`${count(sent, "response")} sent to be graded again${cost ? ` (about $${Number(cost).toFixed(2)})` : ""}. They come back here with new proposals.`);
    await onRegraded();
  }));
}

/** The concerns' controls: Clarify the rule here (an editor drawn when opened) and Regrade the responses that raised them. */
export function concernActionsMarkup(entry, h) {
  const affected = concernResponses(entry).length;
  return `<div class="grading-actions grading-review-concern-actions"><button type="button" class="button" data-concern-clarify>Clarify the rule here</button>${affected ? `<button type="button" class="button" data-concern-regrade>Regrade ${h.e(count(affected, "affected response"))}</button>` : ""}</div><div class="grading-review-concern-editor" data-concern-editor hidden></div>`;
}

function editorMarkup(q, kept, named, h, siblings = () => []) {
  const display = rubricDisplay(q.rubric);
  const builtin = new Map((q.builtin || []).map(rule => [rule.item_id, rule.rule]));
  const pairs = new Set((kept.pairs || []).map(exclusionKey));
  // The lines the concerns name, else every line: a concern about "the rubric as a whole" names none.
  const lines = q.rubric.items;
  const others = item => lines.filter(other => other.id !== item.id && !builtin.has(other.id) && !builtin.has(item.id));
  const line = item => `<fieldset class="grading-review-concern-line"><legend><strong>${h.e(display.label(item.id))}</strong> · ${h.e(item.points)} pt${Math.abs(item.points) === 1 ? "" : "s"} · ${mathText(item.description, h)}</legend>
    <label class="field"><span>Clarification for the AI graders</span><textarea rows="2" maxlength="2000" data-concern-line="${h.e(item.id)}">${h.e(kept.clarifications?.[item.id] || "")}</textarea></label>
    ${siblings(item.id).length ? `<div class="grading-review-concern-siblings" data-concern-siblings="${h.e(item.id)}" hidden><p class="supporting-copy">Also on the same line of:</p>${siblings(item.id).map(line => `<label class="grading-check"><input type="checkbox" data-concern-sibling="${h.e([item.id, line.question_id, line.item_id].join(LINK_SEP))}" checked> ${h.e(line.label)}</label>`).join("")}<small class="supporting-copy" data-concern-sibling-preview></small></div>` : ""}
    ${others(item).length ? `<p class="supporting-copy">Cannot be combined with:</p><div class="grading-review-concern-pairs">${others(item).map(other => {
      const key = exclusionKey([item.id, other.id]);
      return `<label><input type="checkbox" data-concern-pair="${h.e(key)}" ${pairs.has(key) ? "checked" : ""}> ${h.e(display.label(other.id))}</label>`;
    }).join("")}</div>` : ""}</fieldset>`;
  const rules = builtin.size ? `<p class="supporting-copy">Built-in rules, always on: ${[...builtin].map(([id, rule]) => `${h.e(display.label(id))} is the ${rule === "full_credit" ? "full-credit" : "no-credit"} line and excludes every other line`).join("; ")}.</p>` : "";
  return `${rules}${lines.filter(item => !named.size || named.has(item.id)).map(line).join("")}<p class="supporting-copy">Saving records this question's rule in the rubric finalization; other questions keep theirs. Points and wording stay as they are.</p><div class="grading-actions"><button type="button" class="button primary" data-concern-save>Save the rule</button><button type="button" class="text-button" data-concern-cancel>Cancel</button></div>`;
}
