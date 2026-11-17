// Adapt one version's rubrics to the other versions (requirement, 2026-09-25). The Rubric drafting and review model reads each other version's blank
// template, pairs its problems with the reference version's by what they ask, and rewrites every reference line for the paired problem,
// keeping its points. Each version's result is a draft like a drafted rubric, reviewed under that version's template (Review adapted
// rubrics) and kept per question by the instructor. Nothing is created here, and this dialog stores nothing.
import {readyForReview} from "./grading-guide.js";
import {hasDraft} from "./grading-rubric-edit.js";
import {plural} from "./ui.js";

const members = (plan, slot) => plan.questions.filter(q => q.assignment_version_id === slot.document_id && q.exam_version === slot.exam_version);

// The versions whose rubrics can be adapted (some question with a rubric) and the reference offered first: Version A when it has them.
export function adaptable(plan) {
  const slots = plan.template_setup?.slots || [];
  const sources = slots.filter(slot => members(plan, slot).some(q => q.rubric));
  if (slots.length < 2 || !sources.length) return null;
  return {slots, sources, first: sources.find(slot => slot.exam_version === "A") || sources[0]};
}

export function adaptRubrics(plan, h, refresh, active) {
  const choice = adaptable(plan);
  if (!choice) return;
  const drafting = `/api/grading/assignments/${plan.id}/rubric-drafts`, requests = new Map();
  const working = key => (plan.template_setup.generations || []).some(g => g.key === key && ["pending", "running"].includes(g.status));
  const targetMarkup = source => choice.slots.filter(slot => slot.key !== source.key).map((slot, i) => {
    const has = members(plan, slot).filter(q => q.rubric).length, busy = working(slot.key), none = !slot.options.length;
    return `<fieldset class="rubric-adapt-target" data-target="${h.e(slot.key)}"><legend>${h.e(slot.label)}</legend>
      <label class="grading-check"><input type="checkbox" name="target-${i}" value="${h.e(slot.key)}" ${busy || none ? "disabled" : has ? "" : "checked"}>Adapt ${h.e(source.label)}’s rubrics to ${h.e(slot.label)}</label>
      ${none ? `<p class="grading-warning">Upload ${h.e(slot.label)}’s blank template under Assignment Templates first: the model reads this version’s problems from it.</p>`
        : h.select(`Blank template for ${slot.label}`, `template-${i}`, [["", "Choose the blank template"], ...slot.options.map(t => [t.id, `${t.label} · ${plural(t.page_count, "page")}`])], slot.template_id || (slot.options.length === 1 ? slot.options[0].id : ""))}
      ${busy ? `<p class="supporting-copy">Rubrics for ${h.e(slot.label)} are being drafted or adapted now.</p>` : ""}
      ${has ? `<p class="supporting-copy">${h.e(slot.label)} already has ${plural(has, "question")} with a rubric. They are kept: only problems without a question can be created from the adaptation.</p>` : ""}</fieldset>`;
  }).join("");
  const notes = source => {
    const pending = members(plan, source).filter(q => q.rubric && (hasDraft(plan, q) || !readyForReview(plan, q))).length;
    return pending ? `<p class="grading-warning">${plural(pending, "rubric")} of ${h.e(source.label)} ${pending === 1 ? "is" : "are"} not confirmed for rubric review yet. The rubrics are adapted as they are saved now; later edits to ${h.e(source.label)} are not carried to the other versions, so finish editing first if you plan to.</p>` : "";
  };
  const form = h.dialog("Adapt rubrics to other versions", "Grade every version the same way.",
    `${choice.sources.length > 1 ? h.select("Adapt the rubrics of", "source", choice.sources.map(slot => [slot.key, slot.label]), choice.first.key) : ""}
    <p class="supporting-copy">Your Rubric drafting and review model reads each version’s blank template and pairs its problems with <span data-source-label>${h.e(choice.first.label)}</span>’s by what they ask, never by number alone, since versions may reorder problems. It then rewrites every line of the paired rubric for this version: the same lines, points and rules, with this version’s values and answers.</p>
    <p class="supporting-copy">Nothing is created yet. You compare each adapted rubric with the original line by line and choose which questions to create, as with drafted rubrics. Created questions go through the answer areas and the rubric finalization like any other.</p>
    <div data-source-notes>${notes(choice.first)}</div><div data-targets>${targetMarkup(choice.first)}</div>`,
    "Adapt rubrics", async (values, current, live) => {
      const source = choice.sources.find(slot => slot.key === (values.get("source") || choice.first.key));
      const chosen = [...current.querySelectorAll("[data-target]")].map((box, i) => ({key: box.dataset.target, ticked: values.has(`target-${i}`), template: values.get(`template-${i}`)}))
        .filter(target => target.ticked);
      if (!chosen.length) throw new Error("Tick at least one version to adapt the rubrics to.");
      const missing = chosen.filter(target => !target.template).map(target => choice.slots.find(slot => slot.key === target.key).label);
      if (missing.length) throw new Error(`Choose the blank template for ${missing.join(", ")}.`);
      const failures = [];
      for (const target of chosen) {
        const signature = `${source.key}|${target.key}|${target.template}`;
        if (!requests.has(signature)) requests.set(signature, crypto.randomUUID());
        try {
          await h.api(drafting, "POST", {key: target.key, template_id: target.template, adapt_from: source.key, request_id: requests.get(signature)});
        } catch (error) { failures.push(`${choice.slots.find(slot => slot.key === target.key).label}: ${error.message}`); }
      }
      const started = chosen.length - failures.length;
      if (failures.length) {
        if (started && active()) void refresh();
        throw new Error(`${started ? `${plural(started, "version")} started. ` : ""}${failures.join(" ")}`);
      }
      if (live()) {
        h.close();
        h.toast(`Adapting ${source.label}’s rubrics to ${plural(started, "version")}. Each version’s result appears under Assignment Templates.`);
      }
      if (active()) await refresh();
    });
  form.elements.source?.addEventListener("change", () => {
    const source = choice.sources.find(slot => slot.key === form.elements.source.value);
    form.querySelector("[data-source-label]").textContent = source.label;
    form.querySelector("[data-source-notes]").innerHTML = notes(source);
    form.querySelector("[data-targets]").innerHTML = targetMarkup(source);
  });
}
