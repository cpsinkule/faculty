// Rubrics of EVERY version drafted at once (requirement, 2026-09-25). The model writes each problem once for all its versions: one
// list of lines, each with one point value and its wording per version, so the versions cannot drift apart in the kinds of lines they
// use. Review shows every version of a problem side by side; nothing becomes a question until the instructor keeps it, and every
// rubric is still checked and approved in Key & Rubric Finalization.
import {mathText, renderMath} from "./grading-math.js";
import {afterAcceptance} from "./grading-rubric-drafts.js";
import {choicesMarkup, readChoices} from "./grading-rubric-choices.js";
import {plural} from "./ui.js";

const scoring = mode => mode === "negative" ? "Negative scoring: start at full credit and deduct points for errors or missing work."
  : "Positive scoring: start at 0 and add credit for correct work.";

// The one draft block of the Assignment Templates panel: the latest draft of every version, and the one button.
// Without a registered paper there is no version slot and nothing to draft from (grading-setup-15): one sentence, no button.
export function allDraftMarkup(state, generation, h, busy) {
  const status = generation?.status, working = ["pending", "running"].includes(status), count = state.slots.length, many = count > 1;
  const progress = generation?.progress, noPaper = !count && !generation;
  const line = noPaper ? "Add the assignment PDF first: drafting reads each version’s blank template."
    : !generation ? (many ? `Draft the rubrics of all ${count} versions together from their blank templates.`
      : "Draft a question and rubric for each problem from the blank template.")
    : working ? (progress?.total != null ? `Drafting rubrics: ${progress.completed} of ${plural(progress.total, "problem")} written${many ? " for every version" : ""}. You can keep working meanwhile.`
      : progress?.surveyed < progress?.versions ? `Drafting rubrics: reading the templates’ pages (${progress.surveyed} of ${plural(progress.versions, "version")}). You can keep working meanwhile.`
      : many ? "Drafting rubrics: matching each problem with its copies on the other versions. You can keep working meanwhile." : "Drafting rubrics: reading the template’s pages. You can keep working meanwhile.")
    : status === "complete" ? `${plural(generation.counts.drafted, "problem")} drafted${generation.versions.length > 1 ? ` for all ${generation.versions.length} versions` : ""}${generation.counts.failed ? `, ${generation.counts.failed} could not be` : ""}${generation.created.length ? ` · ${generation.created.length} question${generation.created.length === 1 ? "" : "s"} created` : ""}.`
    : status === "cancelled" ? "Drafting was cancelled." : generation.error || "Drafting failed.";
  const actions = [
    status === "complete" ? h.button("review-all-drafts", generation.created.length ? "Review drafts" : "Review and create questions", `data-generation="${h.e(generation.id)}"`) : "",
    working ? h.button("cancel-all-drafts", "Cancel drafting", `data-generation="${h.e(generation.id)}"`) : "",
    !working && !noPaper ? h.button("draft-all", generation ? "Draft again" : many ? "Draft rubrics for all versions" : "Draft rubrics from the template", busy ? "disabled" : "") : "",
  ].join("");
  // Before any draft: one sentence, and how it works one click away.
  const how = generation || noPaper ? "" : `<details class="grading-tools"><summary>How this works</summary><p class="supporting-copy">${many
    ? "Your Rubric drafting and review model reads every version’s blank template and writes each problem’s lines for all versions at once, so each problem gets the same lines on every version."
    : "Your Rubric drafting and review model reads the blank template, whatever the assignment, and drafts a question and rubric for each problem on it."} You review and edit every draft before any question is created.</p></details>`;
  return `<div class="grading-rubric-draft grading-rubric-draft-all"><h4>Draft rubrics with AI</h4><p class="supporting-copy" ${status === "failed" ? 'role="alert"' : 'role="status"'}>${h.e(line)}</p>${how}${generation ? `<p class="supporting-copy" data-draft-mode>${h.e(scoring(generation.mode))}</p>` : ""}<div class="grading-actions">${actions}</div></div>`;
}

// The request: one scoring mode, the blank template chosen for each version in the panel, and the instructor's quick rubric choices
// (RUBRIC CHOICES, 2026-10-24; `choices`: the plan's read of GET .../rubric-choices, or null where it could not be read).
export function draftAllDialog(state, selected, previous, h, send, choices = null) {
  const missing = state.slots.filter(slot => !selected(slot));
  const option = (slot) => slot.options.find(t => t.id === selected(slot));
  const form = h.dialog(state.slots.length > 1 ? "Draft rubrics for all versions" : "Generate rubrics", state.slots.length > 1 ? `${state.slots.length} versions, drafted together` : state.slots[0]?.label || "",
    `${h.select("Scoring", "mode", [["positive", "Positive scoring"], ["negative", "Negative scoring"]], previous?.mode === "negative" ? "negative" : "positive")}
    <p class="supporting-copy"><strong>Positive:</strong> start at 0 and add credit for correct work.<br><strong>Negative:</strong> start at full credit and deduct points for errors or missing work.</p>
    <ul class="grading-rule-list">${state.slots.map(slot => `<li><strong>${h.e(slot.label)}</strong><span class="${selected(slot) ? "" : "grading-warning"}">${h.e(option(slot) ? `${option(slot).label} · ${plural(option(slot).page_count, "page")}` : "No blank template chosen. Choose or upload one in its slot below.")}</span></li>`).join("")}</ul>
    <p class="supporting-copy">${state.slots.length > 1 ? "The model reads every version’s blank template, matches each problem with its copies on the other versions by what they ask, and writes each problem’s rubric for all versions at once: one list of lines with one point value each and each version’s own values and answers." : "The model reads the chosen blank template and drafts each problem’s rubric."} You can edit every draft before creating questions.</p>
    ${choices ? `<details class="grading-tools rubric-choices-fold" open><summary>Quick choices for the rubrics</summary><p class="supporting-copy">The drafts follow your answers, and the rubric check stops asking about them. “Ask me” leaves a question to the check.${!choices.saved && Object.keys(choices.course || {}).length ? " Started from your answers on this course’s last assignment." : ""}</p>${choicesMarkup(choices.options, choices.saved ? choices.choices : choices.course, h.e)}</details>` : ""}`,
    state.slots.length > 1 ? "Draft all versions" : "Generate rubrics", async (values, node, live) => {
      if (missing.length) throw new Error(`Choose a blank template for ${missing.map(slot => slot.label).join(", ")} first.`);
      await send(values.get("mode"), state.slots.map(slot => ({key: slot.key, template_id: selected(slot)})), choices ? readChoices(node, choices.options) : null);
      if (live()) h.close();
    });
  return form;
}

// Each line's clarification for the graders, per version (2026-09-28): not shown in Gradescope, saved as that version's line comment
// when its question is created, and carried by the rubric review into the clarifications you confirm. Folded: most lines need none.
function lineRow(line, n, i, members, h, mode) {
  const bound = mode === "negative" ? 'max="0"' : 'min="0"';
  const written = members.filter(m => line.clarifications?.[m.version]).length;
  return `<li class="rubric-family-line" data-line><label class="field rubric-draft-points">Points<input name="points-${i}-${n}" aria-label="Problem ${i + 1} line ${n + 1} points" type="number" ${bound} step="any" value="${h.e(line.points)}"></label>
    <div class="rubric-family-wordings">${members.map((m, j) => `<label class="field">${h.e(m.version)}<textarea name="line-${i}-${n}-${j}" data-wording aria-label="Problem ${i + 1} line ${n + 1} ${h.e(m.version)}" rows="2">${h.e(line.descriptions?.[m.version] ?? "")}</textarea><span class="rubric-draft-preview" data-preview>${mathText(line.descriptions?.[m.version] ?? "", h)}</span></label>`).join("")}</div>
    ${members.length > 1 ? linkSelect(line, n, i, h) : ""}<details class="rubric-draft-clarifications"><summary>Clarifications for the graders${written ? ` · ${written} written` : " · none"} (not shown in Gradescope)</summary>
    <div class="rubric-family-wordings">${members.map((m, j) => `<label class="field">${h.e(m.version)}<textarea name="clar-${i}-${n}-${j}" data-clarification aria-label="Problem ${i + 1} line ${n + 1} ${h.e(m.version)} clarification" rows="2">${h.e(line.clarifications?.[m.version] ?? "")}</textarea></label>`).join("")}</div></details></li>`;
}

// LINKED VERSIONS (requirement, 2026-10-02): whether the line is the same on every version (one clarification fits them all, with each
// version's own values) or the same step with another outcome per version. The writer proposes it; it is where the links between the
// versions' lines start, until the rubric review or the instructor decides otherwise. It never changes wording or points.
function linkSelect(line, n, i, h) {
  const options = [["same", "The same on each version"], ["related", "Related: a different outcome per version"], ["", "Not marked"]];
  const current = ["same", "related"].includes(line.link) ? line.link : "";
  return `<label class="field rubric-draft-link">Across the versions<select name="link-${i}-${n}" data-link aria-label="Problem ${i + 1} line ${n + 1} across the versions">${options.map(([value, label]) => `<option value="${value}" ${value === current ? "selected" : ""}>${h.e(label)}</option>`).join("")}</select></label>`;
}

// `found(version, number)`: {question, created} when that version's question already exists (created from this draft or before), else null.
function problemMarkup(p, i, found, h, mode) {
  const members = p.versions;
  const where = members.map(m => `${m.version} ${m.number}`).join(" · ");
  return `<fieldset class="rubric-draft rubric-family" data-family="${h.e(p.id)}" data-index="${i}"><legend>Problem ${i + 1} · ${h.e(p.points)} points · ${h.e(where)}</legend>
    ${p.warnings.map(text => `<p class="grading-warning">${h.e(text)}</p>`).join("")}
    ${members.flatMap(m => m.warnings.map(text => `<p class="grading-warning">${h.e(m.version)}: ${h.e(text)}</p>`)).join("")}
    ${p.notes ? `<p class="supporting-copy"><strong>Model’s note:</strong> ${mathText(p.notes, h)}</p>` : ""}
    <div class="rubric-draft-facts">${h.field("Question points", `max-${i}`, p.points, "number", 'min="0" step="any"')}</div>
    <div class="rubric-family-versions">${members.map((m, j) => {
      const exists = found(m.version, m.number), q = exists?.question;
      if (!exists) return `<section class="rubric-family-version" data-version="${h.e(m.version)}"><h5>${h.e(m.version)}</h5>
        <label class="grading-check"><input type="checkbox" name="keep-${i}-${j}" checked>Create for ${h.e(m.version)}</label>
        ${h.field(`${m.version} question title`, `title-${i}-${j}`, m.title || `Question ${m.number}`)}${h.field(`${m.version} question number`, `number-${i}-${j}`, m.number, "text", 'inputmode="numeric"')}</section>`;
      // A question that already exists is never replaced as a whole: only its rubric, and only when its box is ticked (instructor,
      // 2026-09-27), one by one or with Select all. Its number names the question, so it cannot be edited here.
      const worth = q && Number(q.content.max_points) !== Number(p.points)
        ? `<p class="grading-warning">It is worth ${h.e(q.content.max_points)} points; set Question points above to ${h.e(q.content.max_points)} to replace its rubric.</p>` : "";
      return `<section class="rubric-family-version" data-version="${h.e(m.version)}"><h5>${h.e(m.version)}</h5>
        <p class="grading-warning">Question ${h.e(m.number)} already exists for ${h.e(m.version)}${exists.created && !q ? " (created from this draft)" : ""}.</p>
        ${q ? `<label class="grading-check"><input type="checkbox" name="replace-${i}-${j}" data-replaces="${h.e(q.rubric_id || "")}">Replace its current rubric with this draft</label>${worth}
        <p class="supporting-copy">Only the rubric changes; the question, its answer key and its answer area stay. Its grades become out of date and wait to be graded again, its line comments are cleared, and its rubric review and finalization are asked for again.</p>` : ""}
        ${h.field(`${m.version} question number`, `number-${i}-${j}`, m.number, "text", 'inputmode="numeric" readonly')}</section>`;
    }).join("")}</div>
    <p class="supporting-copy" data-draft-mode>${scoring(mode)} Each line has one point value on every version${members.length > 1 ? "; only its wording differs, with each version’s own values" : ""}. Clear a wording to leave that line out of that version, or every wording of a line to remove it.</p>
    <ol class="rubric-family-lines" data-lines>${p.items.map((line, n) => lineRow(line, n, i, members, h, mode)).join("")}</ol>
    <button type="button" class="button" data-add-line="${i}">Add a line</button>
    ${members.map((m, j) => `<details><summary>${h.e(m.version)} · problem as read from the template</summary>${h.area(`${m.version} problem statement`, `statement-${i}-${j}`, m.statement)}</details>
    <details><summary>${h.e(m.version)} · worked solution${m.final_answers.length ? ` · ${h.e(m.final_answers.map(a => (a.part ? `(${a.part}) ` : "") + a.answer).join("; "))}` : ""}</summary>${h.area(`${m.version} worked solution`, `solution-${i}-${j}`, m.solution)}</details>`).join("")}</fieldset>`;
}

export async function reviewVersionDrafts(plan, gid, h, refresh, live) {
  const base = `/api/grading/assignments/${plan.id}/rubric-drafts/${gid}`;
  const generation = await h.api(base);
  if (!live()) return;
  const mode = generation.mode || "positive", problems = generation.result.problems, failed = generation.result.failed;
  const slots = new Map(generation.versions.map(v => [v.name, v]));
  const existing = new Map(plan.questions.map(q => [`${q.assignment_version_id}:${q.exam_version}:${q.content.printed_number || ""}`, q]));
  const created = new Set(generation.created);
  const found = (name, number) => {
    const question = existing.get(`${slots.get(name)?.key}:${number}`) || null;
    const made = created.has(`${problems.find(p => p.versions.some(m => m.version === name && m.number === number))?.id}:${name}`);
    return question || made ? {question, created: made} : null;
  };
  const replacing = problems.some(p => p.versions.some(m => found(m.version, m.number)?.question));
  const requestId = crypto.randomUUID();
  let changed = false, syncAll = () => {};
  const many = generation.versions.length > 1;
  const form = h.dialog(many ? "Drafted rubrics · all versions" : `Drafted rubrics · ${generation.versions[0]?.label || "this assignment"}`,
    `Drafted by ${generation.model}${many ? `, every version at once` : ""}. Nothing is ${replacing ? "created or replaced" : "created"} until you choose ${replacing ? "it below" : "Create"}.`,
    `<div class="rubric-drafts rubric-family-drafts">${generation.result.summary ? `<p>${h.e(generation.result.summary)}</p>` : ""}${generation.result.notes.map(text => `<p class="grading-warning">${mathText(text, h)}</p>`).join("")}
      ${failed.map(f => `<p class="grading-warning" role="alert">${h.e(f.label)} could not be drafted: ${h.e(f.error)}</p>`).join("")}
      <p class="supporting-copy">Check each problem against the printed ${many ? "versions" : "assignment"}: the numbers, the points and every line. Creating a question confirms its rubric for the rubric review, so check it here: the answer areas, the answer key and the rubric finalization follow.</p>
      <label class="grading-check"><input type="checkbox" data-select-all>Select all</label>
      ${problems.map((p, i) => problemMarkup(p, i, found, h, mode)).join("")}<ul data-draft-results aria-live="polite"></ul></div>`,
    replacing ? "Create or replace selected" : "Create selected questions", async (values, current, active) => {
      const ticked = (i, j) => values.has(`keep-${i}-${j}`) || values.has(`replace-${i}-${j}`);
      const questions = problems.map((p, i) => ({p, i})).filter(({p, i}) => p.versions.some((_, j) => ticked(i, j))).map(({p, i}) => ({
        draft: p.id, mode, max_points: Number(values.get(`max-${i}`)),
        items: [...current.querySelectorAll(`[data-index="${i}"] [data-line]`)].map(row => ({points: Number(row.querySelector("input").value),
          descriptions: Object.fromEntries(p.versions.map((m, j) => [m.version, row.querySelectorAll("[data-wording]")[j].value])),
          clarifications: Object.fromEntries(p.versions.map((m, j) => [m.version, row.querySelectorAll("[data-clarification]")[j]?.value ?? ""])),
          ...(row.querySelector("[data-link]")?.value ? {link: row.querySelector("[data-link]").value} : {})})),
        versions: p.versions.map((m, j) => ({version: m.version, keep: values.has(`keep-${i}-${j}`), printed_number: String(values.get(`number-${i}-${j}`) ?? "").trim(),
          ...(values.has(`replace-${i}-${j}`) ? {replace: true, replaces: current.querySelector(`[name="replace-${i}-${j}"]`).dataset.replaces} : {}),
          title: String(values.get(`title-${i}-${j}`) ?? "").trim(), statement: values.get(`statement-${i}-${j}`), solution: values.get(`solution-${i}-${j}`)}))}));
      if (!questions.length) throw new Error(replacing ? "Tick at least one version to create or to replace." : "Choose at least one question to create.");
      const result = await h.api(`${base}/accept`, "POST", {request_id: requestId, questions});
      for (const r of result.results) {
        if (r.status === "failed" || !r.version) continue;
        changed = true;
        const i = problems.findIndex(p => p.id === r.draft), j = problems[i].versions.findIndex(m => m.version === r.version);
        const box = current.querySelector(`[name="keep-${i}-${j}"]`) || current.querySelector(`[name="replace-${i}-${j}"]`);
        if (box) { box.checked = false; box.disabled = true; }
      }
      syncAll();
      current.querySelector("[data-draft-results]").replaceChildren(...result.results.map(r => {
        const row = document.createElement("li");
        row.textContent = `${r.version ? `${r.version}, ` : ""}question ${r.printed_number || r.draft}: ${{failed: r.error, already_imported: "already created; existing work kept",
          replaced: "rubric replaced", already_replaced: "rubric already replaced", unchanged: "already has exactly this rubric; nothing changed"}[r.status] || "created"}`;
        return row;
      }));
      const failures = result.results.filter(r => r.status === "failed").length;
      const madeCount = result.results.filter(r => ["imported", "already_imported"].includes(r.status)).length;
      const swapped = result.results.filter(r => ["replaced", "already_replaced"].includes(r.status)).length;
      const done = [madeCount ? `${plural(madeCount, "question")} created` : "", swapped ? `${plural(swapped, "rubric")} replaced` : ""].filter(Boolean).join(", ") || "Nothing changed";
      if (failures) throw new Error(`${done}; ${failures} not done. Correct them and choose again; what was done is kept.`);
      // A created question is confirmed for review by this acceptance and its check follows by itself; a replaced rubric is asked again.
      if (active()) { h.close(); h.toast(`${done}. ${swapped ? "Next, confirm the new rubrics for review, then the finalization." : "Next, confirm the blank templates."}${afterAcceptance(result)}`); }  // Closing refreshes the page.
    });
  // Select all (requirement, 2026-09-27): ticks or unticks every version box still open, to create and to
  // replace alike; each box still works on its own, and a mixed selection shows as partly ticked. It has no name, so it is never sent.
  const all = form.querySelector("[data-select-all]");
  const boxes = () => [...form.querySelectorAll('input[type="checkbox"][name^="keep-"], input[type="checkbox"][name^="replace-"]')].filter(box => !box.disabled);
  syncAll = () => {
    const open = boxes(), ticked = open.filter(box => box.checked).length;
    all.checked = open.length > 0 && ticked === open.length;
    all.indeterminate = ticked > 0 && ticked < open.length;
    all.disabled = !open.length;
  };
  all.addEventListener("change", () => { for (const box of boxes()) box.checked = all.checked; syncAll(); });
  form.addEventListener("change", event => { if (event.target.matches('[name^="keep-"], [name^="replace-"]')) syncAll(); });
  syncAll();
  form.querySelectorAll("[data-add-line]").forEach(button => button.addEventListener("click", () => {
    const i = Number(button.dataset.addLine), list = form.querySelector(`[data-index="${i}"] [data-lines]`);
    list.insertAdjacentHTML("beforeend", lineRow({points: 0, descriptions: {}, clarifications: {}}, list.children.length, i, problems[i].versions, h, mode));
    list.lastElementChild.querySelector("[data-wording]").focus();
  }));
  form.addEventListener("input", event => {
    if (!event.target.matches("[data-wording]") || !event.target.closest("[data-line]")) return;
    const preview = event.target.parentElement.querySelector("[data-preview]");
    preview.innerHTML = mathText(event.target.value, h);
    renderMath(preview);
  });
  document.querySelector("#dialog").addEventListener("close", () => { if (changed && live()) void refresh(); }, {once: true});
}
