// Rubrics drafted from a version's blank template by the rubric model that also reviews them: the alternative to uploading
// Gradescope CSVs, for any assignment (exam, quiz, homework, worksheet, lab or project handout).
// A draft is only a proposal. Nothing becomes a question until the instructor keeps it here, edited or not, and every rubric is
// still checked and approved in Key & Rubric Finalization like an uploaded one.
// An adaptation (another version's rubrics rewritten for this version, 2026-09-25) is reviewed here too, each line beside the line it
// adapts; what is kept keeps that line's ID, points and rules.
import {mathText, renderMath} from "./grading-math.js";
import {plural} from "./ui.js";

const scoring = mode => mode === 'negative' ? 'Negative scoring: start at full credit and deduct points for errors or missing work.'
  : 'Positive scoring: start at 0 and add credit for correct work.';
// The version an adaptation was made from, or "" for a draft read from the template alone.
const adaptedFrom = generation => generation?.kind === 'adapt' ? generation.source?.label || 'the other version' : '';
// ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11: they read the same drafted rubric three times, to create it, to confirm it for
// review and to finalize it). Creating a question from a draft is its confirmation for the rubric review, its answer key is read
// without being asked once its answer area is confirmed, and the rubric check starts by itself once the plan is ready. What the
// toast adds, from the acceptance's own answer: `review` is "" when the check was started, else the one sentence of what it waits
// for (which says where a key is read once its area is confirmed); absent when nothing was created. Shared with the review of every
// version at once (`grading-rubric-versions.js`).
export const afterAcceptance = result => typeof result?.review !== "string" ? "" : ` ${result.review || "The rubric check started by itself."}`;

// The block of one version's template slot for ITS OWN earlier draft or adaptation: nothing when it has none. Since every version is
// drafted at once from one button (2026-09-25, `grading-rubric-versions.js`), a slot offers no draft of its own. `template` is the
// blank template chosen there now.
export function draftMarkup(generation, template, h) {
  if (!generation) return "";
  const status = generation.status, working = ["pending", "running"].includes(status);
  const adapted = adaptedFrom(generation), progress = generation.progress;
  const from = generation.template_id !== template ? ` It was ${adapted ? "adapted" : "drafted"} from another template than the one chosen now.` : "";
  const line = working && adapted ? (progress?.paired ? `Adapting ${adapted}’s rubrics: ${progress.completed} of ${plural(progress.total || 0, "question")} written. You can keep working meanwhile.`
      : `Adapting ${adapted}’s rubrics: reading this version’s template and pairing its problems with ${adapted}’s. You can keep working meanwhile.`)
    : working ? (progress?.total ? `Drafting rubrics: ${progress.completed} of ${plural(progress.total, "problem")} written. You can keep working meanwhile.` : "Drafting rubrics: reading the template’s pages. You can keep working meanwhile.")
    : status === "complete" ? `${plural(generation.counts.drafted, "question")} ${adapted ? `adapted from ${adapted}` : "drafted"}${generation.counts.failed ? `, ${generation.counts.failed} could not be` : ""}${generation.created.length ? ` · ${generation.created.length} created` : ""}.${from}`
    : status === "cancelled" ? `${adapted ? "Adapting" : "Drafting"} was cancelled.` : generation.error || `${adapted ? "Adapting" : "Drafting"} failed.`;
  const actions = [
    status === "complete" ? h.button("review-drafts", adapted ? (generation.created.length ? "Review adapted rubrics" : "Review and create adapted questions") : generation.created.length ? "Review drafts" : "Review and create questions", `data-generation="${h.e(generation.id)}"`) : "",
    working ? h.button("cancel-drafts", adapted ? "Cancel adapting" : "Cancel drafting", `data-generation="${h.e(generation.id)}"`) : "",
  ].join("");
  const mode = adapted ? `Each question keeps the scoring of ${adapted}’s rubric.` : scoring(generation.mode);
  return `<div class="grading-rubric-draft"><h5>${adapted ? "Adapted rubrics" : "Earlier draft of this version"}</h5><p class="supporting-copy" ${status === "failed" ? 'role="alert"' : 'role="status"'}>${h.e(line)}</p><p class="supporting-copy" data-draft-mode>${h.e(mode)}</p>${actions ? `<div class="grading-actions">${actions}</div>` : ""}</div>`;
}

// `reference` is the line an adapted line was written from (another version's), shown beside it; a drafted line has none.
// A drafted line (never an adapted one) also has its clarification for the graders: not shown in Gradescope, saved as the line's
// comment when the question is created, and carried by the rubric review into the clarifications you confirm (2026-09-28).
function lineRow(line, index, q, h, mode, reference = null, from = "") {
  const original = reference ? `<p class="rubric-draft-reference" data-reference><strong>${h.e(from)}, ${h.e(reference.id)} · ${h.e(reference.points)} point${Math.abs(reference.points) === 1 ? "" : "s"}:</strong> ${mathText(reference.description, h)}${reference.comment ? `<br><em>Your comment there:</em> ${h.e(reference.comment)}` : ""}</p>` : "";
  const note = reference || from ? "" : `<label class="field rubric-draft-clarification">Clarification for the graders (not shown in Gradescope)<textarea name="clar-${q}-${index}" data-clarification aria-label="Question draft ${h.e(q)} line ${index + 1} clarification" rows="2">${h.e(line.clarification || "")}</textarea></label>`;
  return `<li class="rubric-draft-line" data-line ${reference ? `data-source="${h.e(reference.id)}"` : ""}><label class="field">Line ${index + 1}<textarea name="line-${q}-${index}" data-wording aria-label="Question draft ${h.e(q)} line ${index + 1}" rows="2">${h.e(line.description)}</textarea></label><label class="field rubric-draft-points">Points<input name="points-${q}-${index}" aria-label="Question draft ${h.e(q)} line ${index + 1} points" type="number" ${mode === 'negative' ? 'max="0"' : 'min="0"'} step="any" value="${h.e(line.points)}"></label><p class="rubric-draft-preview" data-preview>${mathText(line.description, h)}</p>${note}${original}</li>`;
}

// One drafted question: keep it or not, its number, title, points, lines, statement and solution, all editable.
function questionMarkup(q, i, existing, h, mode, from = "", stale = false) {
  const taken = existing.has(q.number);
  const named = q.title || `Question ${q.number}`;
  const references = new Map((q.reference_items || []).map(line => [line.id, line]));
  return `<fieldset class="rubric-draft" data-draft="${h.e(q.number)}" data-index="${i}"><legend>${h.e(named)} · ${h.e(q.points)} points${from ? ` · from ${h.e(from)}’s ${h.e(q.source.title)}` : ""}</legend>
    ${taken ? `<p class="grading-warning">Question ${h.e(q.number)} already exists for this version and is not replaced. Use its Import revision control to change its rubric.</p>` : ""}
    ${stale ? `<p class="grading-warning" role="alert">${h.e(from)}’s rubric for ${h.e(q.source.title)} changed after this was adapted. Adapt the rubrics again to use the current one.</p>` : ""}
    ${from ? `<p class="supporting-copy">Paired with ${h.e(from)}’s ${h.e(q.source.title)}${q.source.reason ? `: ${h.e(q.source.reason)}` : "."}</p>` : ""}
    <label class="grading-check"><input type="checkbox" name="keep-${i}" ${taken || stale ? "disabled" : "checked"}>Create this question</label>
    ${q.warnings.map(text => `<p class="grading-warning">${h.e(text)}</p>`).join("")}${q.notes ? `<p class="supporting-copy"><strong>Model’s note:</strong> ${mathText(q.notes, h)}</p>` : ""}
    <div class="rubric-draft-facts">${h.field("Question title", `title-${i}`, named)}${h.field("Question number", `number-${i}`, q.number, "text", 'inputmode="numeric"')}${h.field("Question points", `max-${i}`, q.points, "number", 'min="0" step="any"')}</div>
    ${q.label ? `<p class="supporting-copy">Printed heading: ${h.e(q.label)}</p>` : ""}
    <p class="supporting-copy" data-draft-mode>${scoring(mode)}${from ? ` Each line keeps the ID, points and rules of the ${h.e(from)} line shown under it, so both versions are graded alike.` : ""} Clear a line’s wording to remove it.</p>
    <ol class="rubric-draft-lines" data-lines>${q.items.map((line, n) => lineRow(line, n, i, h, mode, references.get(line.id) || null, from)).join("")}</ol>
    <button type="button" class="button" data-add-line="${i}">Add a line</button>
    <details><summary>Problem as read from the template</summary>${h.area("Problem statement", `statement-${i}`, q.statement)}</details>
    <details><summary>Worked solution${q.final_answers.length ? ` · ${h.e(q.final_answers.map(a => (a.part ? `(${a.part}) ` : "") + a.answer).join("; "))}` : ""}</summary>${h.area("Worked solution", `solution-${i}`, q.solution)}<p class="supporting-copy">Kept with the question as its worked solution, for you and the graders. The answer key is confirmed separately in Key &amp; Rubric Finalization.</p></details></fieldset>`;
}

export async function reviewDrafts(plan, gid, h, refresh, live) {
  const base = `/api/grading/assignments/${plan.id}/rubric-drafts/${gid}`;
  const generation = await h.api(base);
  if (!live()) return;
  const mode = generation.mode || 'positive', from = adaptedFrom(generation), stale = new Set(generation.stale || []);
  const drafted = generation.result.questions, failed = generation.result.failed;
  const commented = Boolean(from) && drafted.some(q => (q.reference_items || []).some(line => line.comment));
  const existing = new Set(plan.questions.filter(q => q.assignment_version_id === generation.document_id && q.exam_version === generation.exam_version)
    .map(q => String(q.content.printed_number || "")).filter(Boolean));
  const slot = plan.template_setup?.slots.find(s => s.key === generation.key);
  const requestId = crypto.randomUUID(), created = new Set(generation.created);
  let changed = false;
  const label = slot?.label || (generation.exam_version ? `Version ${generation.exam_version}` : "this assignment");
  const form = h.dialog(`${from ? "Adapted" : "Drafted"} rubrics · ${label}`, from ? `Adapted from ${from} by ${generation.model}. Nothing is created until you choose Create.` : `Drafted by ${generation.model}. Nothing is created until you choose Create.`,
    `<div class="rubric-drafts">${generation.result.summary ? `<p>${h.e(generation.result.summary)}</p>` : ""}${generation.result.notes.map(text => `<p class="grading-warning">${mathText(text, h)}</p>`).join("")}
      ${failed.map(f => `<p class="grading-warning" role="alert">Problem ${h.e(f.number)} could not be ${from ? "adapted" : "drafted"}: ${h.e(f.error)}</p>`).join("")}
      <p class="supporting-copy">${from ? `Each problem of ${h.e(label)} was paired with the ${h.e(from)} question that asks the same task, and every line of that rubric was rewritten for this version. Compare each line with the ${h.e(from)} line under it, and check the question’s number and points against the printed assignment.`
        : "Check each question against the printed assignment: its number, its points and every line. Problems printed without whole numbers are numbered by their position."} Creating a question confirms its rubric for the rubric review, so check it here: the answer areas, the answer key and the rubric finalization follow.</p>
      ${commented ? h.check(`Copy my comments on ${from}’s rubric lines to the same lines of the questions I create`, "copy_comments", true) : ""}
      ${drafted.map((q, i) => questionMarkup(q, i, new Set([...existing, ...created]), h, q.mode || mode, from, stale.has(q.number))).join("")}<ul data-draft-results aria-live="polite"></ul></div>`,
    "Create selected questions", async (values, current, active) => {
      const chosen = drafted.map((q, i) => ({q, i})).filter(({i}) => values.has(`keep-${i}`));
      if (!chosen.length) throw new Error("Choose at least one drafted question to create.");
      const questions = chosen.map(({q, i}) => ({draft: q.number, title: String(values.get(`title-${i}`)).trim(), printed_number: String(values.get(`number-${i}`)).trim(), max_points: Number(values.get(`max-${i}`)),
        mode: q.mode || mode, statement: values.get(`statement-${i}`), solution: values.get(`solution-${i}`),
        items: [...current.querySelectorAll(`[data-index="${i}"] [data-line]`)].map(row => ({description: row.querySelector("[data-wording]").value, points: Number(row.querySelector("input").value),
          ...(from ? {source: row.dataset.source || ""} : {clarification: row.querySelector("[data-clarification]")?.value ?? ""})}))}));
      const result = await h.api(`${base}/accept`, "POST", {request_id: requestId, questions, ...(commented ? {copy_comments: values.has("copy_comments")} : {})});
      const outcome = new Map(result.results.map(r => [r.draft, r]));
      for (const {q, i} of chosen) {
        const done = outcome.get(q.number);
        if (done && done.status !== "failed") {
          changed = true;
          const box = current.querySelector(`[name="keep-${i}"]`);
          box.checked = false; box.disabled = true;
        }
      }
      current.querySelector("[data-draft-results]").replaceChildren(...result.results.map(r => {
        const row = document.createElement("li");
        row.textContent = `Question ${r.printed_number || r.draft}: ${r.status === "failed" ? r.error : r.status === "already_imported" ? "already created; existing work kept" : "created"}`;
        return row;
      }));
      const failures = result.results.filter(r => r.status === "failed").length;
      if (failures) throw new Error(`${result.results.length - failures} created; ${failures} not created. Correct them and choose Create again; created questions are kept.`);
      if (active()) { h.close(); h.toast(`${plural(result.results.length, "question")} created. Next, confirm the blank templates.${afterAcceptance(result)}`); }  // Closing refreshes the page.
    });
  form.querySelectorAll("[data-add-line]").forEach(button => button.addEventListener("click", () => {
    const i = Number(button.dataset.addLine), list = form.querySelector(`[data-index="${i}"] [data-lines]`);
    list.insertAdjacentHTML("beforeend", lineRow({description: "", points: 0}, list.children.length, i, h, drafted[i].mode || mode, null, from));
    list.lastElementChild.querySelector("[data-wording]").focus();
  }));
  form.addEventListener("input", event => {
    const row = event.target.closest("[data-line]");
    if (!row || !event.target.matches("[data-wording]")) return;
    const preview = row.querySelector("[data-preview]");
    preview.innerHTML = mathText(event.target.value, h);
    renderMath(preview);
  });
  document.querySelector("#dialog").addEventListener("close", () => { if (changed && live()) void refresh(); }, {once: true});
}
