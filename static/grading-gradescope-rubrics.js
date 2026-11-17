import { choicesFold } from "./grading-rubric-choices.js";
import { plural } from "./ui.js";
import { mountNotFinal } from "./grading-recorded-rulings.js";
// Importing rubrics straight from Gradescope (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE"; requirement, 2026-10-24). Rubrics & Templates' alternative to uploading the export's CSVs: the
// server reads the chosen Gradescope assignment (every version of a set) and imports each ticked question as the CSV upload would.
// With "Also import the blank template and answer areas" (requirement, 2026-10-24) each version's Gradescope template becomes an
// Assignment Template and each question's Gradescope boxes its answer areas on it, still checked and confirmed as any.
// Nothing is written to Gradescope and nothing is approved here.

// Reading each version's rubric and outline, and preparing its template, can take a while.
const WAIT = 300000;
const base = plan => `/api/grading/gradescope/assignments/${encodeURIComponent(plan.id)}/rubrics`;

export function questionsMarkup(view, h) {
  const versions = view.versions || [];
  if (!versions.length) return view.reason ? `<p class="supporting-copy" role="status">${h.e(view.reason)}</p>` : "";
  return versions.map(version => `<section class="rubric-upload-version" data-gradescope-rubric-version="${h.e(version.letter)}">
    <h3>${version.letter ? `Version ${h.e(version.letter)}` : "Questions"}</h3>
    <p class="supporting-copy">${version.template?.ready ? `Blank template on Gradescope: ${h.e(plural(version.template.pages, "page"))}` : "No finished template on Gradescope"}</p>
    ${version.questions.length ? `<ul class="grading-gradescope-links">${version.questions.map(q => `<li>
      <label class="grading-check"><input type="checkbox" name="question" value="${h.e(q.id)}" ${q.importable && !q.in_faculty ? "checked" : ""} ${q.importable ? "" : "disabled"}>
      <strong>${h.e(q.title || `Question ${q.index}`)}</strong></label>
      <span>${h.e(q.points)} points · ${h.e(plural(q.items, "rubric item"))} · ${q.scoring === "negative" ? "negative" : "positive"} scoring · ${q.areas ? h.e(plural(q.areas, "answer box", "answer boxes")) : "no answer box"}${q.in_faculty ? " · already in Faculty" : ""}</span>
      ${q.reason ? `<p class="grading-warning" role="status">${h.e(q.reason)}</p>` : ""}${(q.warnings || []).map(w => `<p class="supporting-copy">${h.e(w)}</p>`).join("")}</li>`).join("")}</ul>`
      : '<p class="supporting-copy">This assignment has no questions on Gradescope yet.</p>'}</section>`).join("");
}

// A bubble-sheet exam's answer key (2026-10-25: "import the bubble sheet key from there as well"): each version's letters, with
// the points and scoring of a question that differs from the most usual ones.
export function keyMarkup(view, h, bubble = view.bubble) {
  const pairs = bubble.versions.flatMap(v => v.questions.map(q => `${q.points}|${q.scoring}`));
  const usual = pairs.sort((a, b) => pairs.filter(p => p === b).length - pairs.filter(p => p === a).length)[0] || "";
  const [points, scoring] = usual.split("|");
  const words = {exact: "exact", partial: "partial credit", either: "either/or"};
  return `${bubble.problems.length ? `<div class="callout grading-warning" data-tone="warning" role="status"><p><strong>Faculty cannot import this key:</strong></p><ul>${bubble.problems.map(p => `<li>${h.e(p)}</li>`).join("")}</ul></div>` : ""}
    ${points ? `<p class="supporting-copy">Most questions: ${h.e(plural(Number(points), "point"))}, ${h.e(words[scoring] || scoring)}.</p>` : ""}
    ${bubble.versions.map(v => `<section class="rubric-upload-version" data-gradescope-key-version="${h.e(v.letter)}"><h3>Version ${h.e(v.letter)}</h3>
      ${v.questions.length ? `<p class="bubble-key-compact">${v.questions.map(q => `${q.number} ${h.e(q.answer)}${`${q.points}|${q.scoring}` === usual ? "" : ` (${h.e(plural(Number(q.points), "point"))}, ${h.e(words[q.scoring] || q.scoring)})`}`).join(" · ")}</p>`
        : '<p class="supporting-copy">No answers on Gradescope for this version.</p>'}</section>`).join("")}
    <p class="supporting-copy">${view.key_revision ? `Importing replaces the saved answer key (revision ${h.e(view.key_revision)}) with this one; the earlier revision stays in its history.` : "Importing saves this as the answer key. Bubble sheets are scored against it on the Bubble sheets step."}</p>`;
}

export function importFromGradescope(plan, h, refresh, active, {key = false} = {}) {
  const documents = plan.rubric_upload_documents || plan.document_versions || [];
  const letters = plan.exam_versions?.letters || [];
  let view = null, generation = 0, requestId = "", changed = false, importing = false;
  const form = h.dialog(key ? "Import the answer key from Gradescope" : "Import rubrics from Gradescope",
    key ? "Faculty reads the bubble-sheet exam’s answer key on Gradescope and saves it as this assignment’s key. Nothing on Gradescope changes."
      : "Faculty reads the assignment’s rubric on Gradescope and imports each ticked question as an uploaded CSV would be. Nothing on Gradescope changes.",
    `<div data-gradescope-rubric-pickers></div>
     <div data-rubric-only ${key ? "hidden" : ""}>${documents.length > 1 ? h.select("Printed document", "document", [["", "Choose the printed document"], ...documents.map(d => [d.id, d.label])]) : ""}
     ${h.check("Also import the blank template and answer areas", "areas", true)}</div>
     <div data-gradescope-rubric-questions aria-live="polite"><p class="loading-state" role="status">Reading Gradescope…</p></div>
     <p class="supporting-copy" data-rubric-only ${key ? "hidden" : ""}>The rubrics are checked, clarified and approved later, in Key &amp; Rubric Finalization, once the answer areas are confirmed.</p>
     <div data-rubric-only data-rubric-choices-slot ${key ? "hidden" : ""}></div>
     <ul data-upload-results aria-live="polite"></ul>`,
    "Import", async (values, current, live) => submit(values, live));
  if (!key) mountNotFinal(form, h, plan.id);   // RECORDINGS "A RULING": a meeting's words that this rubric is not final
  const pickers = form.querySelector("[data-gradescope-rubric-pickers]"), listing = form.querySelector("[data-gradescope-rubric-questions]");
  const results = form.querySelector("[data-upload-results]"), button = form.querySelector('button[type="submit"]');
  const choices = key ? null : choicesFold(form.querySelector("[data-rubric-choices-slot]"), plan.id, h);
  const open = () => form.isConnected && form.closest("dialog")?.open;
  const controls = () => { button.disabled = importing || !view || !((view.versions || []).length || (view.bubble && !view.bubble.problems.length)); };

  function drawPickers() {
    // The coordinator's course is listed last and only read, for importing (GRADESCOPE "THE COORDINATOR'S COURSE").
    const courses = (view?.courses || []).map(c => [c.id, c.coordinator ? `${c.name || c.short} · coordinator’s course (read only)`
      : [c.short, c.term].filter(Boolean).join(" · ") || c.name]);
    // Bubble-sheet exams are listed after the written ones; from the Bubble sheets page, only they are.
    const assignments = (view?.assignments || []).filter(a => !key || a.bubble_sheet).map(a => [a.id, a.bubble_sheet ? `${a.title} (bubble sheet answer key)`
      : a.versions.length ? `${a.title} (Versions ${a.versions.join(", ")})` : a.title]);
    const plain = view?.versions?.length === 1 && !view.versions[0].letter;
    pickers.innerHTML = `${h.select("Gradescope course", "course_id", [["", "Choose the course"], ...courses], view?.course_id || "")}
      ${view?.course_id ? h.select("Gradescope assignment", "gradescope_assignment_id", [["", "Choose the assignment"], ...assignments], view.gradescope_assignment_id || "") : ""}
      ${plain && letters.length ? h.select("Which version these rubrics describe", "exam_version", [["", "Choose the version"], ...letters.map(l => [l, `Version ${l}`])]) : ""}`;
    // The assignment list belongs to the course it was read from: it waits while another course is read.
    pickers.querySelector('[name="course_id"]').addEventListener("change", event => {
      pickers.querySelector('[name="gradescope_assignment_id"]')?.setAttribute("disabled", "");
      void load({course_id: event.target.value});
    });
    pickers.querySelector('[name="gradescope_assignment_id"]')?.addEventListener("change", event =>
      void load({course_id: view.course_id, gradescope_assignment_id: event.target.value}));
  }

  async function load(asked = {}) {
    const token = ++generation;
    view = null; requestId = ""; controls();
    listing.innerHTML = '<p class="loading-state" role="status">Reading Gradescope…</p>';
    try {
      const result = await h.api(`${base(plan)}/preview`, "POST", asked, {}, WAIT);
      if (!open() || token !== generation) return;
      view = result; requestId = crypto.randomUUID();
      if (!result.configured) { pickers.innerHTML = ""; listing.innerHTML = `<p class="grading-warning" role="status">${h.e(result.reason)}</p>`; return; }
      drawPickers();
      form.querySelectorAll("[data-rubric-only]").forEach(node => { node.hidden = key || Boolean(result.bubble); });
      listing.innerHTML = result.bubble ? keyMarkup(result, h) : key && result.gradescope_assignment_id ? '<p class="supporting-copy">Choose a bubble-sheet exam.</p>'
        : questionsMarkup(result, h) + companionMarkup(result);
      // The exam's bubble-sheet half (2026-10-25: "shouldn't I be able to import them as part of exam 2?"): proposed by its title,
      // changed or left out here.
      listing.querySelector('[name="sheet_id"]')?.addEventListener("change", event =>
        void load({course_id: view.course_id, gradescope_assignment_id: view.gradescope_assignment_id, sheet_id: event.target.value}));
    } catch (cause) {
      if (open() && token === generation) listing.innerHTML = `<p role="alert">${h.e(cause.message)}</p><p class="supporting-copy">Nothing was imported. Choose again, or close and open this again.</p>`;
    } finally {
      if (token === generation && open()) controls();
    }
  }

  function companionMarkup(result) {
    const sheets = (result.assignments || []).filter(a => a.bubble_sheet);
    if (!result.versions?.length || !sheets.length) return "";
    return `<section class="rubric-upload-version" data-gradescope-companion><h3>Bubble sheet answer key</h3>
      ${h.select("Also import the answer key of", "sheet_id", [["", "No bubble-sheet exam"], ...sheets.map(a => [a.id, a.title])], result.sheet_id || "")}
      ${result.companion ? keyMarkup(result, h, result.companion) : ""}</section>`;
  }

  async function submit(values, live) {
    if (!view) throw new Error("Wait for Gradescope to be read.");
    if (view.bubble) return saveKey(live);
    const questions = values.getAll("question");
    if (!questions.length && !view.companion) throw new Error("Tick the questions to import.");
    if (questions.length) await choices?.save();
    if (view.companion?.problems.length) throw new Error(`The answer key of ${view.companion.title} cannot be imported: ${view.companion.problems[0]} Choose no bubble-sheet exam to import the rubrics alone.`);
    if (!documents.length) throw new Error("Add this assignment’s PDF in Create first.");
    const paper = documents.length === 1 ? documents[0].id : values.get("document");
    if (!paper) throw new Error("Choose the printed document these rubrics are for.");
    const plain = view.versions.length === 1 && !view.versions[0].letter;
    if (plain && letters.length && !values.get("exam_version")) throw new Error("Choose the version these rubrics describe.");
    importing = true; controls();
    try {
      // The same request ID for the same reading: importing again after an unanswered request repeats nothing.
      const result = await h.api(`${base(plan)}/import`, "POST", {request_id: requestId, course_id: view.course_id, gradescope_assignment_id: view.gradescope_assignment_id,
        questions, assignment_version_id: paper, areas: values.get("areas") === "on", sheet_id: view.sheet_id || "", base_revision: view.key_revision ?? null, ...(plain && values.get("exam_version") ? {exam_version: values.get("exam_version")} : {})}, {}, WAIT);
      if (result.results.some(r => r.status !== "failed") || result.key?.status === "imported") changed = true;
      if (!live()) return;
      results.replaceChildren(...result.results.map(r => {
        const row = document.createElement("li");
        const name = `${r.exam_version ? `Version ${r.exam_version} · ` : ""}${r.title}`;
        row.textContent = `${name}: ${r.status === "failed" ? r.error : r.status === "already_imported" ? "Already imported; existing work kept" : r.printed_number ? `Question ${r.printed_number} · ${r.max_points} points · imported${r.areas ? ` with ${plural(r.areas, "answer area")}` : ""}` : "Imported · printed number needs review"}`;
        return row;
      }), ...(result.notes || []).map(note => { const row = document.createElement("li"); row.textContent = note; return row; }),
        ...(result.key ? [Object.assign(document.createElement("li"), {textContent: `Answer key of ${result.key.title}: ${result.key.status === "imported"
          ? `imported (${Object.entries(result.key.versions).map(([letter, n]) => `Version ${letter}: ${plural(n, "question")}`).join(", ")})` : result.key.error}`})] : []));
      if (result.key?.status === "failed") throw new Error(`The rubrics were imported; the answer key was not (${result.key.error}).`);
      if (result.failed) throw new Error(`${result.results.length - result.failed} imported; ${result.failed} could not be. Imported questions are kept.`);
      if ((result.notes || []).length) throw new Error("Imported. Some answer areas were not; see why above.");
      h.close();
      h.toast(`${plural(result.imported, "rubric")} imported from Gradescope${result.templates?.length ? `, with ${plural(result.templates.length, "blank template")} and answer areas` : ""}${result.key ? ", and the bubble sheet answer key" : ""}.`);
    } finally {
      importing = false;
      if (open()) controls();
    }
  }

  async function saveKey(live) {
    if (view.bubble.problems.length) throw new Error(view.bubble.problems[0]);
    importing = true; controls();
    try {
      // The revision the dialog showed: a key saved meanwhile is never replaced (409), and the same request repeats nothing.
      const result = await h.api(`${base(plan)}/bubble-key`, "POST", {request_id: requestId, course_id: view.course_id,
        gradescope_assignment_id: view.gradescope_assignment_id, base_revision: view.key_revision ?? null}, {}, WAIT);
      changed = true;
      if (!live()) return;
      h.close();
      h.toast(`Answer key imported from Gradescope (${Object.entries(result.versions).map(([letter, n]) => `Version ${letter}: ${plural(n, "question")}`).join(", ")}).`);
    } finally {
      importing = false;
      if (open()) controls();
    }
  }

  document.querySelector("#dialog").addEventListener("close", () => { if (changed && active()) void refresh(); }, {once: true});
  void load();
  return form;
}
