/* TEACHING-WORKFLOWS: optional structured authoring, retained in the quiz draft. */
export function mountBlueprint(host, {api, escape: e, course, changed}) {
  let bank = null, slots = [], enabled = false, loading = false, error = "", generation = 0;
  const objectives = () => (bank?.outline || []).filter(n => !n.retired && n.level === "objective");
  const questions = () => (bank?.questions || []).filter(q => !q.retired);
  function draw() {
    host.innerHTML = `<label class="checkbox-field"><input type="checkbox" data-blueprint-enable ${enabled ? "checked" : ""}> Use an assessment blueprint</label>
      ${enabled ? `<p class="field-hint">Each slot sets one objective, question type and points for every version. Fixed questions keep the selected bank revision. Check the actual objective alignment when reviewing the draft.</p>
      ${error ? `<p class="form-error" role="alert">${e(error)}</p><button type="button" class="button" data-blueprint-retry>Retry</button>` : loading ? '<p role="status">Reading the question bank…</p>' : !objectives().length ? `<p>Add learning objectives in the <a href="#current-courses">course’s question bank</a> first.</p>` : `
      <ol class="blueprint-slots">${slots.map((slot, index) => `<li data-blueprint-slot="${index}"><div class="blueprint-fields">
      <label class="field">Objective<select data-blueprint-field="objective_id">${objectives().map(n => `<option value="${e(n.id)}" ${slot.objective_id === n.id ? "selected" : ""}>${e(n.title)}</option>`).join("")}</select></label>
      <label class="field">Question<select data-blueprint-field="question_id"><option value="">Write a new question</option>${questions().map(q => `<option value="${e(q.id)}" ${slot.question_id === q.id ? "selected" : ""}>${e((q.source_label || q.content.statement).slice(0, 110))} · revision ${q.revision}</option>`).join("")}</select></label>
      <label class="field">Type<select data-blueprint-field="kind" ${slot.question_id ? "disabled" : ""}><option value="free_response" ${slot.kind === "free_response" ? "selected" : ""}>Free response</option><option value="multiple_choice" ${slot.kind === "multiple_choice" ? "selected" : ""}>Multiple choice</option></select></label>
      <label class="field">Points<input data-blueprint-field="points" type="number" min="0.5" max="100" step="0.5" value="${e(String(slot.points))}" ${slot.question_id ? "disabled" : ""}></label>
      <button type="button" class="text-button" data-blueprint-remove="${index}" aria-label="Remove question ${index + 1}">Remove</button></div></li>`).join("")}</ol>
      <button type="button" class="button" data-blueprint-add ${slots.length >= 20 ? "disabled" : ""}>Add question</button>
      <p class="field-hint" data-blueprint-total>Blueprint total: ${slots.reduce((n, slot) => n + Number(slot.points || 0), 0)} points. Set the quiz’s total to match.</p>`}` : ""}`;
  }
  async function load() {
    const mine = ++generation;
    loading = true; error = ""; draw();
    try {
      const next = await api(`/api/question-bank/definitions/${encodeURIComponent(course().definition_id)}`);
      if (mine !== generation || !host.isConnected) return;
      bank = next;
      if (!slots.length && objectives().length) slots.push({objective_id: objectives()[0].id, kind: "free_response", points: 5, question_id: ""});
    } catch (cause) { if (mine === generation) error = cause.message; }
    finally { if (mine === generation) { loading = false; draw(); } }
  }
  host.addEventListener("change", event => {
    const target = event.target;
    if (target.matches("[data-blueprint-enable]")) {
      enabled = target.checked; changed();
      if (enabled && !bank) void load(); else draw();
      return;
    }
    const row = target.closest("[data-blueprint-slot]");
    if (!row || !target.dataset.blueprintField) return;
    const slot = slots[Number(row.dataset.blueprintSlot)];
    slot[target.dataset.blueprintField] = target.value;
    if (target.dataset.blueprintField === "question_id" && slot.question_id) {
      const q = questions().find(q => q.id === slot.question_id);
      slot.kind = q.content.kind; slot.points = q.content.points ?? "";
    }
    changed();
    if (target.dataset.blueprintField === "question_id") draw();
    else host.querySelector("[data-blueprint-total]").textContent = `Blueprint total: ${slots.reduce((n,s) => n + Number(s.points || 0),0)} points. Set the quiz’s total to match.`;
  });
  host.addEventListener("click", event => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.hasAttribute("data-blueprint-retry")) { void load(); return; }
    if (target.hasAttribute("data-blueprint-add")) slots.push({objective_id: objectives()[0].id, kind: "free_response", points: 5, question_id: ""});
    else if (target.hasAttribute("data-blueprint-remove")) slots.splice(Number(target.dataset.blueprintRemove), 1);
    else return;
    changed(); draw();
  });
  draw();
  return {
    reset() { generation++; bank = null; slots = []; if (enabled) void load(); },
    value() {
      if (!enabled) return null;
      if (loading || error || !bank) throw new Error("Wait for the question bank to load before drafting.");
      if (!slots.length) throw new Error("Add at least one blueprint question.");
      return slots.map(slot => ({...slot, points: Number(slot.points),
        objective_revision: objectives().find(n => n.id === slot.objective_id)?.revision,
        ...(slot.question_id ? {question_revision: questions().find(q => q.id === slot.question_id)?.revision} : {})}));
    }
  };
}

export function blueprintMarkup(blueprint, coverage, e) {
  if (!blueprint) return "";
  return `<details class="quiz-blueprint-summary"><summary>Assessment blueprint · ${blueprint.slots.length} questions</summary>
    <p>All versions use these slots. Structural checks verify type, points and fixed content; review the problem’s alignment with its objective.</p>
    <ol>${blueprint.slots.map(s => `<li><strong>${e(s.objective)}</strong> · ${s.points} points · ${e(s.kind.replaceAll("_", " "))}${s.fixed ? ` · fixed bank revision ${s.fixed.revision}` : " · newly written"}</li>`).join("")}</ol>
    ${coverage?.length ? `<p>Structure checked for versions ${e(coverage[0].versions.join(", "))}.</p>` : ""}
    <p class="field-hint">This blueprint is retained with the draft. To change its constraints, create a new draft.</p></details>`;
}
