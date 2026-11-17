import { exportFiles, stackFiles, isStampsOnly, downloadable } from "./print-files.js";
import { studentPicker } from "./student-picker.js";
import { calloutMarkup, plural, registerLeaveGuard, setBusy, wallDate } from "./ui.js";
import { createCanvasPlacement } from "./canvas-placement.js";
import { viewLink } from "./file-viewer.js";
import { attachPrefill, followRecord, openRows } from "./schedule-prefill.js";

// Create assignment: one window from the assignment's details through seating, the PDF version and printing.
//
// The window owns no data. The assignment stays course-wide; the sections ticked here are browser state that scopes
// the term-owned seating charts and section-owned print jobs the existing services create. Seating charts are made
// and confirmed by the seating module's own dialogs, so their checks, response cards and receipts are unchanged.

const STEPS = [["details", "Details"], ["seating", "Seating"], ["version", "Paper"], ["print", "Stamp and print"], ["canvas", "Canvas"]];
// The assignment key follows the title until the instructor edits it (it is fixed once the assignment exists).
export const keyFromTitle = (title) => String(title || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
  .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 160);

// ASSIGNMENT-WORKSPACE: one explicit handoff, with successful work retained even when a later
// step fails. The owning setup/import routes reuse their existing records on an explicit retry.
export async function prepareAssignmentGrading({ api, assignmentId, termId, sectionIds, plan = null, templates = false }) {
  const problems = [];
  try {
    let created = false;
    if (!plan) {
      plan = await api("/api/grading/assignments", "POST", {
        assignment_id: assignmentId, term_id: termId, section_ids: sectionIds,
      }, {}, 180000);
      for (const item of plan.set_up?.templates || []) {
        if (item.error) problems.push(`${item.label || "Blank template"}: ${item.error}`);
      }
      created = plan.set_up?.created !== false;
      // Initial setup already imports every applicable registered version. Do not retry a
      // failed import again within the same click; another explicit action can resume it.
    }
    if (templates && !created) {
      const setup = await api(`/api/grading/assignments/${encodeURIComponent(plan.id)}/templates`);
      const attempted = new Set();
      for (const slot of setup.slots || []) {
        if (!slot.can_import || slot.template_id || attempted.has(slot.document_id)) continue;
        attempted.add(slot.document_id);
        try {
          await api(`/api/grading/assignments/${encodeURIComponent(plan.id)}/templates/prepare`, "POST", { document_id: slot.document_id }, {}, 180000);
        } catch (cause) {
          problems.push(`${slot.label || "Blank template"}: ${cause.message}`);
        }
      }
    }
  } catch (cause) {
    problems.push(cause.message);
  }
  return { plan, problems };
}

/** What Set up grading now (requirement, 2026-10-02) says once the plan exists, wherever it was asked for (this window, the Exams
 * hub's Grading tile, the Assignments card's ⋯ menu): [message, toast options]. A version whose blank template could not be
 * prepared is named; a plan with no blank template at all says how to get one, since only a blank lets the assignment finder
 * match a scan without labels (a PDF registered later is prepared in Rubrics & Templates). */
export function setUpGradingNotice(title, plan) {
  const failed = (plan?.set_up?.templates || []).filter((item) => item.error);
  const labels = failed.map((item) => String(item.label || "").replace(/^version\s+/i, ""));
  const one = labels.length === 1;
  if (failed.length) {
    return [`Grading is set up for ${title}. ${one ? `Version ${labels[0]}` : `Versions ${labels.join(", ")}`} could not be prepared as ${one ? "a blank template: add it"
      : "blank templates: add them"} in Rubrics & Templates.`, { tone: "warning" }];
  }
  if (plan?.set_up?.created === false) return [`Grading was already set up for ${title}.`, { tone: "success" }];
  if (!(plan?.templates || []).length) {
    return [`Grading is set up for ${title}: add its rubrics now. It has no blank template yet: ${(plan?.document_versions || []).length
      ? "add one in Rubrics & Templates" : "register its PDF in Assignments, then prepare its blank template in Rubrics & Templates"}.`, { tone: "info" }];
  }
  return [`Grading is set up for ${title}: add its rubrics now. Its papers join once their scans clear the Teaching Inbox.`, { tone: "success" }];
}

const versionName = (label) => (/^version\b/i.test(label) ? label : `Version ${label}`);
// Several versions at once: "Versions A, B" (one keeps versionName's form).
const versionsName = (labels) => (labels.length === 1 ? versionName(labels[0]) : `Versions ${labels.map((label) => label.replace(/^version\s+/i, "")).join(", ")}`);

/**
 * ASK FIRST (DOCUMENTS, requirement, 2026-10-11): what Finish later asks, and then says, when new versions were typed on Paper and
 * not added: they are not saved with the assignment, and "saved" alone said nothing of them. `typed` holds their labels ("" for a
 * row with files and no label); with none typed nothing is asked and the notice is the one it always was. Pure.
 * @param {string} title @param {string[]} typed
 */
export function finishLaterWords(title, typed) {
  const rest = "Continue it from the Assignments list.";
  if (!typed.length) return { question: "", notice: `${title} is saved. ${rest}` };
  const many = typed.length > 1, named = typed.filter(Boolean);
  const name = named.length === typed.length ? versionsName(named) : `the new version${many ? "s" : ""}`;
  return { question: `Finish later without adding ${name}? What you typed for ${many ? "them" : "it"} is not saved. ${title} itself is saved.`,
    notice: `${title} is saved. ${name.charAt(0).toUpperCase()}${name.slice(1)} ${many ? "were" : "was"} not added. ${rest}` };
}

export function createAssignmentWizard(deps) {
  let generation = 0;
  const open = (method) => (...args) => {
    const run = ++generation;
    return createWizardSession({ ...deps, isCurrent: () => run === generation })[method](...args);
  };
  return { open: open("open"), openForAssignment: open("openForAssignment") };
}

// A window owns its state and request chain. Opening another one retires the previous session,
// including a catalog read or a multi-step creation that has not finished yet (FRONTEND).
function createWizardSession({ api: request, escape, toast: notify, showDialog, dialogHeader, getSeating, refresh, onChanged, openBatch, isCurrent }) {
  let state = null;
  let shownForm = null, handoff = false;
  const route = location.hash;
  const stopped = () => Object.assign(new Error(""), { stoppedWizard: true });
  const current = () => isCurrent() && location.hash === route && (!shownForm || handoff || (shownForm.isConnected && dialog().open));
  const api = async (...args) => {
    if (!current()) throw stopped();
    const result = await request(...args);
    if (!current()) throw stopped();
    return result;
  };
  const toast = (...args) => { if (isCurrent() && args[0]) notify(...args); };
  const display = (render) => async (...args) => {
    try { return await render(...args); } catch (cause) { if (!cause.stoppedWizard) throw cause; }
  };
  const showCanvas = display(renderCanvas), showVersion = display(renderVersion), showPrint = display(renderPrint);
  // The optional last step puts the assignment on Canvas (canvas-placement.js; requirement, 2026-09-30).
  const placement = createCanvasPlacement({ api, escape, toast, showDialog, dialogHeader });

  const dialog = () => document.querySelector("#dialog");
  // A stamps-only version is named by its own label ("Stamps only · 2 pages"), never "Version Stamps only …".
  const paperName = (version) => (!version ? "" : isStampsOnly(version) ? version.label : versionName(version.label));
  const byLabel = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true });
  const shortDay = (value) => new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const enc = encodeURIComponent;
  const sectionList = (numbers) => `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}`;

  function show(step, title, subtitle, body, footer) {
    if (!current()) throw stopped();
    if (!state.listening) {
      // The page lists what the window created, however the window ends.
      state.listening = true;
      // Leaving the page (Back, a link) closes this window with it; what was typed here and not yet saved asks first.
      const offLeave = registerLeaveGuard(() => {
        const host = dialog();
        if (!(state?.touched || unfinishedVersions().length) || !host.open || !host.querySelector("#assignment-wizard")) return null;
        if ((host.dataset.route || "") === location.hash.split("?")[0]) return null;
        return "Leave Create assignment? What you typed in this window is not saved.";
      });
      dialog().addEventListener("close", () => { offLeave(); onChanged(); }, { once: true });
    }
    keepPaperDraft();
    state.step = step;
    // The Canvas step is there only when Canvas is set up for this Faculty.
    const steps = STEPS.filter(([key]) => (key !== "canvas" || state.canvas) && (key !== 'seating' || state.requires || state.charts.length || step === 'seating'));
    const index = steps.findIndex(([key]) => key === step);
    // One numbered line; steps already passed are buttons that go back to them, like Back does.
    const trail = `<ol class="wizard-steps" aria-label="Steps">${steps.map(([key, label], position) => position < index
      ? `<li class="done"><button type="button" class="wizard-step-link" data-wizard-step="${key}" title="Go back to ${label}">${label}</button></li>`
      : `<li${key === step ? ' aria-current="step"' : ""}>${label}</li>`).join("")}</ol>`;
    showDialog(`<form id="assignment-wizard" novalidate>${dialogHeader(title, subtitle)}<div class="dialog-body">${trail}${body}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer">${footer}</footer></form>`);
    dialog().classList.remove("dialog-wide");
    const form = document.querySelector("#assignment-wizard");
    shownForm = form;
    handoff = false;
    form.addEventListener("input", () => { fail(form, ""); if (state) state.touched = true; });
    form.querySelectorAll("[data-wizard-step]").forEach((button) => {
      button.onclick = () => {
        if (form.dataset.saving === "true") return;
        const target = button.dataset.wizardStep;
        if (target === "details") showDetails();
        else if (target === "seating") showSeating();
        else if (target === "print") void showPrint().catch((cause) => fail(form, cause.message));
        else void showVersion().catch((cause) => fail(form, cause.message));
      };
    });
    return form;
  }

  // WHAT WAS TYPED ON PAPER STAYS (DOCUMENTS, requirement, 2026-10-11): the new-version rows of the Paper step (labels typed, files
  // chosen) are kept, as the elements they are, while the window shows another step, and Paper puts them back; Back and the step
  // line lost them before. Rows a Next registered are no draft: those versions are in the list. Called before every redraw.
  const versionLabel = (row) => row.querySelector('input[name="label"]').value.trim();
  const versionTyped = (row) => !row.dataset.registered && Boolean(versionLabel(row) || row.querySelector('input[name="files"]').files.length);
  function keepPaperDraft() {
    const form = document.querySelector("#assignment-wizard"), host = form?.querySelector("[data-new-versions]");
    if (!host || !state) return;  // Another step is showing: what Paper kept stays kept.
    const rows = [...host.children];
    state.paperDraft = rows.some(versionTyped) ? { rows, chosen: form.elements.choice.value === "new", source: form.elements.source.value } : null;
  }

  // The new versions typed with "Faculty prints it from LaTeX" chosen and not added yet, on the step or kept from it: their labels
  // ("" for a row with files and no label). They are not saved with the assignment, so Finish later and leaving the page ask first.
  function unfinishedVersions() {
    const form = document.querySelector("#assignment-wizard"), host = form?.querySelector("[data-new-versions]");
    const rows = host ? (form.elements.choice.value === "new" ? [...host.children] : []) : state?.paperDraft?.chosen ? state.paperDraft.rows : [];
    return rows.filter(versionTyped).map(versionLabel);
  }

  function fail(form, message) {
    if (!current() || !form.isConnected) return;
    const error = form.querySelector(".dialog-body > .form-error");
    error.textContent = message;
    error.hidden = !message;
  }

  async function perform(form, operation, { preserveDraft = false } = {}) {
    if (form.dataset.saving === "true") return;
    const buttons = [...form.querySelectorAll("button")].filter((button) => !button.disabled);
    fail(form, "");
    form.dataset.saving = "true";
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await operation();
      if (current() && state && !preserveDraft) state.touched = false;  // What was typed has been saved (or the step moved on).
    } catch (cause) {
      if (form.isConnected) fail(form, cause.message);
      else toast(cause.message, true);
    } finally {
      form.dataset.saving = "false";
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  // The assignment's catalog lifecycle row in the window's term (the Assignments card's own row).
  function lifecycleRow(test) {
    const rows = (state.documents.lifecycle || []).filter((item) => item.assignment_id === state.assignment?.id && test(item));
    return rows.find((item) => item.term_id === state.termId) || (!state.termId ? rows[0] : null) || null;
  }

  const workspaceHref = (area = "") => `#assignments/${enc(state.assignment.id)}/${enc(state.termId)}${area ? `?area=${area}` : ""}`;

  function finish(message) {
    const href = workspaceHref();
    const incomplete = state.gradingProblems?.length;
    dialog().close();
    state = null;
    location.hash = href;
    toast(incomplete ? `${message} Grading preparation still needs attention in Prepare.` : message, { tone: incomplete ? "warning" : "success" });
  }

  // Done on the last step. Sections without a chart whose paper has no print job yet are named (create-1), so a
  // version registered and never printed does not read as set up.
  function done() {
    const title = state.assignment.title;
    finish(state.unprinted ? `${title} is saved. ${state.unprinted}` : `${title} is set up. After it is given, upload the scans in Collect or the Teaching Inbox.`);
  }

  async function prepareGrading(templates = false) {
    const identity = `${state.assignment.id}/${state.termId}`;
    if (!templates && state.gradingIdentity === identity && state.gradingPlan) {
      return { plan: state.gradingPlan, problems: state.gradingProblems || [] };
    }
    const result = await prepareAssignmentGrading({ api, assignmentId: state.assignment.id, termId: state.termId,
      sectionIds: state.sectionIds, plan: state.gradingIdentity === identity ? state.gradingPlan : null, templates });
    Object.assign(state, { gradingIdentity: identity, gradingPlan: result.plan, gradingProblems: result.problems });
    return result;
  }

  // GRADING "GRADED FOR PARTICIPATION" (requirement, 2026-11-01): the box ticked on Details becomes the term grading plan's setting
  // once the plan exists. Left unticked nothing is written, so continuing an assignment never turns its setting off.
  async function recordParticipation() {
    const plan = state.gradingPlan;
    if (!state.participation || !plan?.id || state.participationRecorded === plan.id) return;
    try {
      await api(`/api/grading/assignments/${encodeURIComponent(plan.id)}/participation`, "PUT", { enabled: true });
      state.participationRecorded = plan.id;
    } catch (cause) {
      state.gradingProblems = [...(state.gradingProblems || []), `Graded for participation was not saved: ${cause.message}`];
    }
  }

  function gradingProblemsMarkup() {
    if (!state.gradingProblems?.length) return "";
    return `<div data-grading-problems>${calloutMarkup({ tone: "warning", title: "Your saved work is kept; grading preparation needs attention.",
      body: `${state.gradingProblems.join(" ")} Printing can continue.` })}<button type="button" class="button" data-retry-grading>Retry grading preparation</button></div>`;
  }

  function bindGradingRetry(form) {
    form.querySelector("[data-retry-grading]")?.addEventListener("click", () => {
      void perform(form, async () => {
        await prepareGrading(true);
        const host = form.querySelector("[data-grading-problems]");
        if (state.gradingProblems.length) {
          host.outerHTML = gradingProblemsMarkup();
          bindGradingRetry(form);
        } else {
          host.remove();
          toast("Grading preparation is ready to continue in Prepare.", { tone: "success" });
        }
      }, { preserveDraft: true });
    });
  }

  // 'Given on' (requirement, 2026-10-02): once the assignment exists, the term's exam record gets its main sitting on that date
  // through the Exams page's own routes (POST /api/exams when the term has no record yet, then its sittings route), so Home's
  // Coming up, the Calendar and the Exams page show it. A date the record already holds is kept; the one this window saved is
  // changed when the instructor goes back and types another, and taken off its sitting again when they clear the field.
  async function recordGivenOn() {
    const day = state.givenOn, done = state.givenOnRecorded;
    if (!state.termId) return;
    const same = done && done.assignmentId === state.assignment.id && done.termId === state.termId;
    if (!day) {
      if (same && done.sittingId && done.date) {
        const exam = await api(`/api/exams/${enc(done.examId)}`);
        const sitting = (exam.sittings || []).find((item) => item.id === done.sittingId);
        // Only the date this window saved: one changed elsewhere since stays.
        if (sitting && sitting.date === done.date) await api(`/api/exams/${enc(done.examId)}/sittings/${enc(sitting.id)}`, "PATCH", { version: sitting.version, date: "" });
        state.givenOnRecorded = { ...done, date: "" };
      }
      return;
    }
    if (same && done.date === day) return;
    const find = async () => (await api("/api/exams?summary=0")).exams.find((item) => item.assignment_id === state.assignment.id && item.term_id === state.termId);
    let listed = await find();
    if (!listed) {
      try {
        listed = await api("/api/exams", "POST", { term_id: state.termId, assignment_id: state.assignment.id, title: state.assignment.title, section_ids: state.sectionIds });
      } catch (cause) {
        if (cause.status !== 409 || !(listed = await find())) throw cause;  // another tab made the record meanwhile
      }
    }
    const exam = listed.sittings ? listed : await api(`/api/exams/${enc(listed.id)}`);
    const mains = (exam.sittings || []).filter((item) => item.kind === "main");
    const target = mains.find((item) => item.id === done?.sittingId) || (mains.some((item) => item.date) ? null : mains[0]);
    if (!target && mains.some((item) => item.date)) {
      const kept = mains.find((item) => item.date).date;
      toast(`${exam.title}’s Exams page already records ${wallDate(kept)}; that date is kept.`, { tone: "warning" });
      state.givenOnRecorded = { assignmentId: state.assignment.id, termId: state.termId, date: day, sittingId: "", examId: exam.id };
      return;
    }
    const saved = target
      ? await api(`/api/exams/${enc(exam.id)}/sittings/${enc(target.id)}`, "PATCH", { version: target.version, date: day })
      : await api(`/api/exams/${enc(exam.id)}/sittings`, "POST", { version: exam.version, label: exam.title, kind: "main", date: day, time: "", location: "", room_id: null });
    const sitting = target ? { id: target.id } : (saved.sittings || []).find((item) => item.kind === "main" && item.date === day);
    state.givenOnRecorded = { assignmentId: state.assignment.id, termId: state.termId, date: day, sittingId: sitting?.id || "", examId: exam.id };
  }

  // "From the schedule" (SCHEDULING "CLASS SCHEDULE"): the planned row chosen on Details follows the assignment once it exists.
  async function recordPlanned() {
    const key = `${state.plannedItem}:${state.assignment.id}`;
    if (!state.plannedItem || !state.termId || state.plannedLinked === key) return;
    if (await followRecord(api, state.termId, state.plannedItem, { assignment_id: state.assignment.id })) state.plannedLinked = key;
  }

  const chosenSections = () => state.sections.filter((item) => state.sectionIds.includes(item.id));
  const unseated = () => chosenSections().filter((item) => !state.charts.some((chart) => chart.section_ids.includes(item.id)));
  const subtitle = () => escape(`${state.assignment.code} · ${state.assignment.title}`);

  // ----- 5. Canvas (optional) -------------------------------------------------------------
  // The assignment into the term's Canvas copy, in a module; the push window then takes it to each section's course. Done
  // finishes the window whether or not it was put on Canvas (the card offers it again).
  async function renderCanvas() {
    const form = show("canvas", "Put on Canvas", subtitle(),
      `<p class="section-lead">Optional: make this assignment in every section’s Canvas course and place it in a module. Its card offers this later too.</p><div data-wizard-canvas></div>`,
      '<button type="button" class="button" data-back>Back</button><button type="submit" class="button" data-done>Done</button>');
    form.querySelector("[data-back]").onclick = () => { void showPrint().catch((cause) => fail(form, cause.message)); };
    // Only Done finishes: Enter in a Canvas field must not end the window.
    form.onsubmit = (event) => { event.preventDefault(); if (event.submitter?.hasAttribute("data-done")) done(); };
    const host = form.querySelector("[data-wizard-canvas]");
    if (!state.termId) {
      host.innerHTML = '<p class="field-hint">This course has no current term: Canvas assignments belong to one.</p>';
      return;
    }
    handoff = true;
    await placement.mount(host, { termId: state.termId, assignmentId: state.assignment.id, onLeave: () => { if (current()) { dialog().close(); state = null; } } });
  }

  // ----- opening ------------------------------------------------------------------------

  async function begin() {
    let documents, seating, canvas;
    try {
      [documents, seating, canvas] = await Promise.all([api("/api/documents/catalog"), api("/api/seating/catalog"),
        api("/api/canvas/status").then((found) => Boolean(found?.configured), () => false)]);
    } catch (cause) {
      toast(cause.message, true);
      return false;
    }
    if (!documents.definitions.length) {
      toast("Add a course first, then create its assignments.", true);
      return false;
    }
    state = { documents, sections: seating.sections, rooms: seating.rooms, canvas, definitionId: "", termId: "", key: "", title: "", sectionIds: null,
      locked: false, requires: false, generate: false, plan: null, naming: "", assignment: null, charts: [], version: { choice: "", id: "" },
      confirmed: {}, jobs: [], stackVersions: null, stack: null, unprinted: "", givenOn: "", givenOnRecorded: null, participation: false, participationRecorded: "" };
    return true;
  }

  async function open(prefill = {}) {
    if (!await begin()) return;
    if (prefill.chartId) return openForChart(prefill.chartId);
    if (prefill.assignment) {
      Object.assign(state, { definitionId: prefill.assignment.definition_id, key: prefill.assignment.assignment_key, title: prefill.assignment.title, locked: true });
      try {
        await attachExistingCharts(prefill.assignment);
      } catch (cause) {
        state = null;
        toast(cause.message, true);
        return;
      }
    } else if (state.documents.definitions.some((item) => item.id === prefill.definitionId)) state.definitionId = prefill.definitionId;
    showDetails();
  }

  // Continue: an assignment already seated in a current term starts with its own seating charts attached, so the window
  // never offers every section as unseated copies (which would issue duplicate stamps for an exam already printed). The
  // Seating step preselects the plan these charts make; nothing is created or moved until the instructor goes on.
  //
  // With `termId` (the Details step found that the typed key and title name an assignment that already exists), only that
  // term's charts are attached and the sections the instructor ticked are kept, with the charts' own sections added.
  async function attachExistingCharts(assignment, termId = "") {
    const current = new Set(state.sections.filter((item) => item.definition_id === assignment.definition_id && item.is_current).map((item) => item.term_id));
    const charts = (await api("/api/seating")).filter((item) => item.assignment_id === assignment.id && (termId ? item.term_id === termId : current.has(item.term_id)));
    if (!charts.length) return;
    const term = charts[0].term_id;  // The newest chart's term, when the course has several current terms.
    const plan = charts.filter((item) => item.term_id === term);
    const seated = new Set(plan.flatMap((item) => item.section_ids));
    const ticked = new Set(termId ? state.sectionIds || [] : []);
    Object.assign(state, { termId: term, requires: true, charts: plan, plan: null, naming: "",
      sectionIds: state.sections.filter((item) => item.term_id === term && (seated.has(item.id) || ticked.has(item.id))).map((item) => item.id) });
  }

  // A stage of an assignment card (#documents?assignment=<id>&step=<details|seating|version|print>): the window opens at
  // that step with the assignment's own charts attached, like Continue. Sections default to the charts' sections, else the
  // course's current term; the paper defaults as the Paper step would. Nothing is created until the instructor goes on.
  async function openForAssignment(assignment, step = "details", selectedTerm = "") {
    if (!await begin()) return;
    Object.assign(state, { definitionId: assignment.definition_id, key: assignment.assignment_key, title: assignment.title, locked: true });
    try {
      if (selectedTerm) state.termId = selectedTerm;
      await attachExistingCharts(assignment, selectedTerm);
      if (!state.sectionIds) {
        const current = state.sections.filter((item) => item.definition_id === assignment.definition_id && (selectedTerm ? item.term_id === selectedTerm : item.is_current));
        const termId = selectedTerm || current[0]?.term_id || "";
        Object.assign(state, { termId, sectionIds: current.filter((item) => item.term_id === termId).map((item) => item.id) });
      }
      if (step === "details" || !state.sectionIds.length) return showDetails();
      if (step === "seating") return showSeating();
      state.assignment = state.documents.assignments.find((item) => item.id === assignment.id) || assignment;
      if (step === "canvas" && state.canvas) return await showCanvas();
      if (step === "print") {
        state.version = { ...paperDefault(state.documents.versions.filter((item) => item.assignment_id === assignment.id)), auto: true };
        if (state.version.choice === "existing" || (state.version.choice === "stamps" && (state.version.id || state.charts.length))) return await showPrint();
      }
      await showVersion();
    } catch (cause) {
      state = null;
      toast(cause.message, true);
    }
  }

  // "Print exams for this room" on a chart page: the exam, its sections and its charts are already known.
  async function openForChart(chartId) {
    try {
      const chart = await api(`/api/seating/${encodeURIComponent(chartId)}`);
      const plan = (await api("/api/seating")).filter((item) => item.assignment_id === chart.assignment_id && item.term_id === chart.term_id);
      state.assignment = state.documents.assignments.find((item) => item.id === chart.assignment_id);
      Object.assign(state, { definitionId: state.assignment.definition_id, termId: chart.term_id, key: state.assignment.assignment_key, title: state.assignment.title,
        locked: true, requires: true, charts: plan, sectionIds: [...new Set(plan.flatMap((item) => item.section_ids))], fromChart: true });
      await showVersion();
    } catch (cause) {
      state = null;
      toast(cause.message, true);
    }
  }

  // ----- 1. details ---------------------------------------------------------------------

  function showDetails() {
    const form = show("details", state.locked ? "Continue an assignment" : "Create assignment", "Choose the course, name the assignment, and select its sections.",
      `<label class="field">Course<select name="definition_id" aria-label="Course" required ${state.locked ? "disabled" : ""}>${state.documents.definitions.map((item) => `<option value="${escape(item.id)}" ${item.id === state.definitionId ? "selected" : ""}>${escape(item.code)}</option>`).join("")}</select></label>
       <label class="field" data-term-field hidden>Term<select name="term_id" aria-label="Term"></select></label>
       <div class="detail-group"><label class="field">Assignment title<input name="title" required maxlength="240" placeholder="Exam 1" value="${escape(state.title)}" ${state.locked ? "disabled" : "autofocus"}></label>
       <details class="disclosure"><summary>Advanced: permanent assignment key</summary><label class="field">Assignment key<input name="key" required maxlength="160" placeholder="exam-1" value="${escape(state.key)}" ${state.locked ? "disabled" : ""}></label>
       <p class="supporting-copy">The key is generated from the title and never changes. The assignment is shared across this course’s terms.</p></details></div>
       <fieldset class="seating-sections"><legend>Sections this assignment is for</legend><p class="supporting-copy">This selection applies to seating and printing for this term.</p><div id="wizard-sections" class="detail-group"></div></fieldset>
       <div class="detail-group wizard-given-on" data-given-on></div><label class="checkbox-field"><input type="checkbox" name="needs_seating" ${state.requires ? 'checked' : ''}>Arrange assigned seats for this assessment</label>
       <label class="checkbox-field"><input type="checkbox" name="participation" ${state.participation ? 'checked' : ''}>Graded for participation</label><p class="field-hint">Graded as usual, then everyone who turns in a paper gets full credit; the rubric lines stay as their feedback.</p>`,
      '<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Next</button>');
    const termField = form.querySelector("[data-term-field]");
    // The key is derived from the title until the instructor types their own.
    let keyFollows = !state.locked && !state.assignment && (!state.key || state.key === keyFromTitle(state.title));
    form.elements.title.addEventListener("input", () => { if (keyFollows) form.elements.key.value = keyFromTitle(form.elements.title.value); });
    form.elements.key.addEventListener("input", () => { keyFollows = !form.elements.key.value || form.elements.key.value === keyFromTitle(form.elements.title.value); });
    function terms() {
      const found = new Map();
      state.sections.filter((item) => item.definition_id === form.elements.definition_id.value).forEach((item) => {
        if (!found.has(item.term_id)) found.set(item.term_id, `${item.term_label}${item.is_current ? "" : " (not current)"}`);
      });
      return found;
    }
    function drawSections() {
      const termId = form.elements.term_id.value;
      const items = state.sections.filter((item) => item.definition_id === form.elements.definition_id.value && item.term_id === termId);
      const keep = state.termId === termId && state.sectionIds ? state.sectionIds : null;
      form.querySelector("#wizard-sections").innerHTML = items.length
        ? items.map((item) => `<label class="checkbox-field"><input type="checkbox" name="section" value="${escape(item.id)}" ${!keep || keep.includes(item.id) ? "checked" : ""}>${escape(item.term_label)} · Section ${item.number}</label>`).join("")
        : '<p class="field-hint">This course has no saved sections yet. Add them from the course page first.</p>';
      drawGivenOn(termId);
    }
    // 'Given on' (requirement, 2026-10-02), optional and empty by default: a campus date kept exactly as typed (a date input's own
    // YYYY-MM-DD, never converted through a time zone). A date the term already records for this assignment is shown instead,
    // naming where it is changed: the seating chart whose date it is, else its Exams page (the main sitting). A sitting this window
    // dated stays editable here.
    function drawGivenOn(termId) {
      const host = form.querySelector("[data-given-on]");
      const typed = form.elements.given_on ? form.elements.given_on.value : state.givenOn;
      const existing = state.documents.assignments.find((item) => item.definition_id === form.elements.definition_id.value && item.assignment_key === form.elements.key.value.trim());
      const life = existing && (state.documents.lifecycle || []).find((item) => item.assignment_id === existing.id && item.term_id === termId);
      const recorded = life?.exam_date;
      const mine = Boolean(state.givenOnRecorded?.sittingId) && state.givenOnRecorded.assignmentId === existing?.id && state.givenOnRecorded.termId === termId;
      const where = life?.exam_date_chart_id ? "as its seating chart records. Change it on the chart." : "as its Exams page records. Change it there.";
      host.innerHTML = recorded && !mine
        ? `<p class="supporting-copy" data-given-recorded>Given on ${escape(wallDate(recorded))}, ${where}</p>`
        : `<label class="field">Given on (optional)<input type="date" name="given_on" value="${escape(typed || "")}"></label><p class="field-hint">The campus date it is given: it puts the assignment on Home’s Coming up, the Calendar and its Exams page. Leave it empty for an exam you seat; its seating chart records the date.</p>`;
      prefill = null;
      if (!(recorded && !mine) && !existing) void offerPlanned(termId);
    }
    // The term's planned rows no record follows yet, offered above Given on for a new assignment; the one chosen gives its date.
    let prefill = null;
    async function offerPlanned(termId) {
      const rows = await openRows(api, termId, "given");
      const input = form.elements.given_on;
      if (!rows.length || !input || !form.isConnected || form.elements.term_id.value !== termId || form.querySelector("[data-schedule-prefill]")) return;
      prefill = attachPrefill({ rows, before: input.closest("label"), dateInput: input, title: () => form.elements.title.value,
        sections: () => [...form.querySelectorAll('input[name="section"]:checked')].map((box) => box.value), escape,
        chosen: state.plannedTerm === termId ? state.plannedItem || "" : "" });
    }
    form.addEventListener("input", (event) => { if (event.target === form.elements.title || event.target.name === "section") prefill?.refresh(); });
    function drawTerms() {
      const found = terms();
      form.elements.term_id.innerHTML = [...found].map(([id, label]) => `<option value="${escape(id)}" ${id === state.termId ? "selected" : ""}>${escape(label)}</option>`).join("");
      termField.hidden = found.size < 2;
      drawSections();
    }
    form.elements.definition_id.onchange = drawTerms;
    form.elements.term_id.onchange = drawSections;
    drawTerms();
    form.onsubmit = (event) => {
      event.preventDefault();
      const key = form.elements.key.value.trim(), title = form.elements.title.value.trim();
      const sectionIds = [...form.querySelectorAll('input[name="section"]:checked')].map((input) => input.value);
      if (!key || !title) return fail(form, "Enter the assignment key and title.");
      if (!sectionIds.length) return fail(form, "Tick at least one section this assignment is for.");
      const givenOn = form.elements.given_on ? form.elements.given_on.value.trim() : "";
      if (givenOn && !/^\d{4}-\d{2}-\d{2}$/.test(givenOn)) return fail(form, "Enter the date it is given as YYYY-MM-DD, or leave it empty.");
      state.givenOn = givenOn;
      state.plannedItem = form.elements.schedule_item?.value || "";
      state.plannedTerm = form.elements.term_id.value;
      const definitionId = form.elements.definition_id.value;
      const clash = state.documents.assignments.find((item) => item.definition_id === definitionId && item.assignment_key === key && item.title !== title);
      if (clash) return fail(form, `The key ${key} already belongs to ${clash.title}. Use another key, or continue that assignment from the Assignments list.`);
      const changed = definitionId !== state.definitionId || form.elements.term_id.value !== state.termId || sectionIds.join() !== (state.sectionIds || []).join() || title !== state.title;
      Object.assign(state, { definitionId, termId: form.elements.term_id.value, key, title, sectionIds });
      state.requires = form.elements.needs_seating.checked;
      state.participation = form.elements.participation.checked;
      // An attached chart was checked against the earlier sections and title.
      if (changed) Object.assign(state, { plan: null, naming: "" });
      // The typed key and title name an assignment that already exists (a DR import creates one from the exam's name):
      // continue it like Continue does, with its seating charts attached, so a seated section is never offered as
      // unseated copies (duplicate stamps for an exam already seated).
      const same = !state.locked && state.documents.assignments.find((item) => item.definition_id === definitionId && item.assignment_key === key && item.title === title);
      if (!same) return state.requires ? showSeating() : advanceSeating(form, null);
      perform(form, async () => {
        await attachExistingCharts(same, state.termId);
        Object.assign(state, { locked: true, continuing: same.title });
        if (state.requires || state.charts.length) showSeating(); else await advanceSeating(form, same, true);
      });
    };
  }

  // ----- 2. seating ---------------------------------------------------------------------

  function showSeating() {
    const existing = state.documents.assignments.find((item) => item.definition_id === state.definitionId && item.assignment_key === state.key);
    const form = show("seating", "Seating chart", escape(`${state.title} · ${sectionList(chosenSections().map((item) => item.number))}`),
      `${state.continuing ? `<p class="callout" data-tone="info" data-continues>${escape(state.continuing)} already exists, so this continues it${state.charts.length ? " with its seating charts attached" : ""}.</p>` : ""}
       <label class="checkbox-field wizard-gate"><input type="checkbox" name="requires" ${state.requires ? "checked" : ""}>This assignment requires a seating chart</label>
       <fieldset class="wizard-indent detail-group" ${state.requires ? "" : "disabled"}>
         <button type="button" class="button" data-attach aria-expanded="false" aria-controls="wizard-attach">Attach existing seating chart</button>
         <div id="wizard-attach" hidden></div>
         <label class="checkbox-field"><input type="checkbox" name="generate" ${state.generate ? "checked" : ""}>Generate new seating charts</label>
         <p class="supporting-copy" data-generate-hint></p>
       </fieldset>`,
      '<button type="button" class="button" data-back>Back</button><button type="submit" class="button primary">Next</button>');
    const indent = form.querySelector(".wizard-indent"), panel = form.querySelector("#wizard-attach"), attach = form.querySelector("[data-attach]");
    const hint = form.querySelector("[data-generate-hint]");
    let plans = null;

    function sync() {
      const covered = state.plan && !state.plan.uncovered.length;
      form.elements.generate.disabled = !state.rooms.length || covered;
      if (form.elements.generate.disabled) form.elements.generate.checked = false;
      hint.textContent = !state.rooms.length ? "Add a room with its seating chart before generating seats."
        : covered ? "The attached seating chart already seats every ticked section."
        : state.plan ? `Next opens the seating chart window for ${sectionList(state.plan.uncovered)}, which the attached chart leaves out.`
        : "Next opens the seating chart window: choose a room for each ticked section.";
      attach.textContent = state.plan ? "Change attached seating chart" : "Attach existing seating chart";
    }

    function planMarkup(plan, index) {
      const rooms = plan.charts.map((chart) => `${chart.room_name} (${sectionList(chart.sections)}, ${chart.seated_count} seated${chart.confirmed_at ? ", confirmed" : ""}${chart.check_ok ? "" : ", roster check flagged"})`).join("; ");
      const notes = [];
      if (plan.extra.length) notes.push(`<li class="wizard-problem">Seats ${escape(sectionList(plan.extra))}, which ${plan.extra.length === 1 ? "is" : "are"} not ticked for this assignment. Go back and tick ${plan.extra.length === 1 ? "it" : "them"}, or choose another chart.</li>`);
      if (plan.uncovered.length) notes.push(`<li>Leaves out ${escape(sectionList(plan.uncovered))}. Generate new charts for ${plan.uncovered.length === 1 ? "it" : "them"} below, or print ${plan.uncovered.length === 1 ? "it" : "them"} without seats.</li>`);
      if (!plan.extra.length && !plan.uncovered.length) notes.push("<li>Seats exactly the ticked sections.</li>");
      notes.push(plan.name_conflict ? `<li>Named <strong>${escape(plan.title)}</strong>, not ${escape(state.title)}.</li>` : `<li>Same name as this assignment.</li>`);
      return `<label class="wizard-plan"><input type="radio" name="plan" value="${index}" ${plan.compatible ? "" : "disabled"} ${state.plan?.assignment_id === plan.assignment_id ? "checked" : ""}><span><strong>${escape(plan.title)}</strong> · ${escape(rooms)}<ul>${notes.join("")}</ul></span></label>`;
    }

    function namingMarkup() {
      const plan = state.plan;
      if (!plan || plan.assignment_id === existing?.id) return "";
      if (!plan.name_conflict) return `<p class="field-hint">This continues the seating chart's assignment, ${escape(plan.title)} (key ${escape(plan.assignment_key)}).</p>`;
      const blocked = plan.rename_blockers.length;
      return `<fieldset class="seating-sections wizard-naming"><legend>The names differ</legend><p class="field-hint">The seating chart belongs to <strong>${escape(plan.title)}</strong>, and you typed <strong>${escape(state.title)}</strong>. A DR testing export can name an exam differently from you.</p>
        <label class="checkbox-field"><input type="radio" name="naming" value="typed" ${blocked ? "disabled" : ""} ${state.naming === "typed" ? "checked" : ""}>Override with ${escape(state.title)}: the seating chart moves to this assignment, and DR bookings linked to ${escape(plan.title)} follow it</label>
        ${blocked ? `<p class="field-hint">Not available: ${escape(plan.rename_blockers.join("; "))}. Printed or sent material already carries the name ${escape(plan.title)}.</p>` : ""}
        <label class="checkbox-field"><input type="radio" name="naming" value="chart" ${state.naming === "chart" ? "checked" : ""}>Keep ${escape(plan.title)}: continue with the seating chart's assignment; ${escape(state.title)} is not created</label></fieldset>`;
    }

    function drawPanel() {
      panel.innerHTML = !plans.length ? '<p class="field-hint">No seating charts exist yet for this course and term.</p>'
        : `<div class="wizard-plans" role="radiogroup" aria-label="Existing seating charts">${plans.map(planMarkup).join("")}<label class="wizard-plan"><input type="radio" name="plan" value="" ${state.plan ? "" : "checked"}><span>None</span></label></div><div data-naming>${namingMarkup()}</div>`;
      sync();
    }

    async function loadPlans() {
      const query = `definition_id=${encodeURIComponent(state.definitionId)}&sections=${state.sectionIds.map(encodeURIComponent).join(",")}&title=${encodeURIComponent(state.title)}`;
      plans = (await api(`/api/seating/attachable?${query}`)).plans;
      // Keep the earlier choice, or the charts this window generated, only while the refreshed check still allows it.
      const mine = state.plan?.charts || state.charts;
      const kept = plans.find((plan) => plan.compatible && plan.charts.some((chart) => mine.some((old) => old.id === chart.id)));
      state.plan = kept || null;
      if (!kept?.name_conflict) state.naming = "";
    }

    async function openPanel() {
      setBusy(attach, true);
      try {
        await loadPlans();
        drawPanel();
        panel.hidden = false;
        attach.setAttribute("aria-expanded", "true");
      } catch (cause) {
        fail(form, cause.message);
      } finally {
        setBusy(attach, false);
      }
    }

    attach.onclick = () => { fail(form, ""); void openPanel(); };
    panel.addEventListener("change", (event) => {
      if (event.target.name === "plan") {
        state.plan = event.target.value === "" ? null : plans[Number(event.target.value)];
        state.naming = "";
        panel.querySelector("[data-naming]").innerHTML = namingMarkup();
        sync();
      }
      if (event.target.name === "naming") state.naming = event.target.value;
    });
    form.elements.requires.onchange = () => {
      state.requires = form.elements.requires.checked;
      indent.disabled = !state.requires;
      fail(form, "");
    };
    form.elements.generate.onchange = () => { state.generate = form.elements.generate.checked; };
    form.querySelector("[data-back]").onclick = showDetails;
    sync();
    if (state.plan || state.charts.length) void openPanel();

    form.onsubmit = (event) => {
      event.preventDefault();
      advanceSeating(form, existing, false, loadPlans);
    };
  }

  function advanceSeating(form, existing, nested = false, reloadPlans = null) {
      const plan = state.requires ? state.plan : null;
      const generate = state.requires && form.elements.generate.checked;
      if (state.requires && !plan && !generate) return fail(form, "Attach an existing seating chart or tick Generate new seating charts. Untick the first box if this assignment needs no seats.");
      if (plan?.name_conflict && plan.assignment_id !== existing?.id && !state.naming) return fail(form, "Choose which name this assignment and its seating chart use.");
      const operation = async () => {
        const adopt = plan && plan.assignment_id !== existing?.id && (!plan.name_conflict || state.naming === "chart");
        if (adopt) {
          state.assignment = (await api("/api/seating/catalog")).assignments.find((item) => item.id === plan.assignment_id);
          if (!state.assignment) throw new Error("That seating chart's assignment no longer exists.");
        } else {
          const created = await api("/api/documents/assignments", "POST", { definition_id: state.definitionId, key: state.key, title: state.title });
          state.assignment = { ...created, code: state.documents.definitions.find((item) => item.id === state.definitionId)?.code || "" };
          if (plan && plan.assignment_id !== created.id) {
            const moved = await api("/api/seating/attach", "POST", { assignment_id: created.id, chart_ids: plan.charts.map((chart) => chart.id) });
            toast(`Seating chart attached as ${created.title}.${moved.relinked_bookings ? ` ${plural(moved.relinked_bookings, "DR booking")} followed it.` : ""}`);
          }
        }
        // The assignment exists now: its key and title are fixed, as when continuing it (Details shows them locked).
        Object.assign(state, { key: state.assignment.assignment_key, title: state.assignment.title, generate, locked: true });
        await recordGivenOn();
        await recordPlanned();
        // ASSIGNMENT-WORKSPACE: this explicit creation also prepares the term's grading context.
        // The service reuses an existing plan; no rubric approval or scan gate is implied.
        await prepareGrading();
        await recordParticipation();
        state.charts = plan ? plan.charts.map((chart) => ({ ...chart })) : [];
        if (plan) {
          await reloadPlans?.();
          await refresh(true);
        }
        const toSeat = unseated().map((item) => item.id);
        if (!generate || !toSeat.length) return showVersion();
        handoff = true;
        await getSeating().openCreate("", { assignmentId: state.assignment.id, sectionIds: toSeat, onBack: showSeating,
          onCreated: (result) => {
            state.charts.push(...result.charts.map((chart) => ({ id: chart.id, room_name: chart.room_name, sections: chart.sections,
              section_ids: chart.section_details.map((item) => item.id), seated_count: chart.counts.seated, confirmed_at: "" })));
            state.generate = false;
            void showVersion();
          } });
      };
      return nested ? operation() : perform(form, operation);
  }

  // ----- 3. paper ---------------------------------------------------------------------------

  // The paper the step starts on: the latest registered version's kind, else stamps only for a seated exam, else LaTeX.
  function paperDefault(versions) {
    const latest = versions[versions.length - 1];
    if (!latest) return { choice: state.charts.length ? "stamps" : "new", id: "" };
    return isStampsOnly(latest) ? { choice: "stamps", id: latest.id } : { choice: "existing", id: latest.id };
  }

  // A version's print jobs in the window's term (never first-page registration) and the current ones among them. A version
  // whose jobs were all superseded or cancelled is retired: an earlier layout (a rebuilt quiz's first one), never offered
  // in a stack.
  function versionPrinting(id) {
    const jobs = state.documents.batches.filter((batch) => batch.assignment_version_id === id && batch.origin !== "registration" && (!state.termId || batch.term_id === state.termId));
    const live = jobs.filter((batch) => !batch.superseded_at && batch.status !== "cancelled");
    return { jobs, live, retired: jobs.length > 0 && !live.length };
  }

  // Three plain choices (docs P12, 2026-09-26): a PDF Faculty builds from LaTeX, paper printed elsewhere and labelled
  // (stamps only), or a PDF already registered. Stamps-only versions are listed only under their own choice. Stamps only
  // for a seated exam is chosen per chart when it is exported (pages per paper there); sections without a chart reuse a
  // stamps-only version an export already registered. The LaTeX choice registers one version or several in one pass
  // (Add another version, create-2): Stamp and print then offers them as one stack with the versions interleaved.
  async function renderVersion() {
    const prior = shownForm;
    state.documents = await api("/api/documents/catalog");
    if (shownForm !== prior) return;
    const versions = state.documents.versions.filter((item) => item.assignment_id === state.assignment.id);
    const pdfs = versions.filter((item) => !isStampsOnly(item)), stamps = versions.filter(isStampsOnly);
    const seated = state.charts.length > 0, loose = unseated().length > 0;
    const stampsAvailable = seated || stamps.length > 0;
    const valid = (version) => version.choice === "new" || (version.choice === "existing" && pdfs.some((item) => item.id === version.id))
      || (version.choice === "stamps" && stampsAvailable && (!version.id || stamps.some((item) => item.id === version.id)));
    // A default follows what changed (charts attached, a version registered); a choice the instructor made is kept.
    if (!state.version.choice || state.version.auto || !valid(state.version)) state.version = { ...paperDefault(versions), auto: true };
    // Grading lists an assignment once its plan holds responses, or once an approved quiz or Set up grading now set it up (create-5,
    // journey-7): the link goes there only then. Without any plan in the window's term, grading can be set up now, before the papers
    // come back (requirement, 2026-10-02); a plan an associated scan made waits for that scan's Inbox review, as the line says.
    const life = lifecycleRow((item) => item.grading_entry_plan_id || item.grading_plan_id);
    const planId = (state.gradingIdentity === `${state.assignment.id}/${state.termId}` && state.gradingPlan?.id)
      || (life ? life.grading_entry_plan_id || life.grading_plan_id : "");
    const canSetUp = !planId && Boolean(state.termId) && !lifecycleRow((item) => item.term_id === state.termId)?.has_grading_plan;
    // The demonstration fixtures are for the browser checks only (print-14): a version registered from one never goes away.
    const testing = document.documentElement.dataset.testing === "1";
    const existingLabels = new Set(versions.map((item) => item.label));
    const nextLetter = (taken) => Array.from({ length: 26 }, (_, index) => String.fromCharCode(65 + index)).find((letter) => !taken.has(letter)) || "";
    const versionRow = (first, value = "") => `<div class="wizard-new-version" data-new-version><label class="field">${first ? "New version label" : "Another version label"}<input name="label" maxlength="160" ${first ? `placeholder="${escape(nextLetter(existingLabels) || "A")}"` : `value="${escape(value)}"`}></label><label class="field" data-files>main.tex and assets<input type="file" name="files" multiple accept=".tex,.pdf,.png,.jpg,.jpeg"></label>${first ? "" : `<button type="button" class="text-button" data-remove-version aria-label="${escape(removeName(value))}">Remove</button>`}</div>`;
    // Each Remove names its own row's version, and follows the label as it is typed.
    const removeName = (label) => `Remove ${label.trim() ? versionName(label.trim()) : "this version"}`;
    // Registered PDFs, the ones printed this term first (missed:print[1]): "printed Oct 1", or "earlier layout" when a
    // version's jobs were superseded and none is current (a rebuilt quiz's first layout). Values and selection are unchanged.
    const printedNote = (item) => {
      const { jobs, live } = versionPrinting(item.id);
      if (live.length) return { rank: 0, note: ` · printed ${shortDay(live.reduce((latest, batch) => (batch.created_at > latest ? batch.created_at : latest), ""))}` };
      return { rank: 1, note: jobs.some((batch) => batch.superseded_at) ? " · earlier layout" : "" };
    };
    const listed = pdfs.map((item) => ({ item, ...printedNote(item) })).sort((a, b) => a.rank - b.rank);
    const radio = (value, label, disabled = false) => `<label class="checkbox-field wizard-gate"><input type="radio" name="choice" value="${value}" ${state.version.choice === value ? "checked" : ""} ${disabled ? "disabled" : ""}>${label}</label>`;
    const stampsChoice = !stampsAvailable
      ? '<p class="field-hint wizard-indent">Needs a seating chart of this assignment exported as stamps only first; it registers the labels version.</p>'
      : `<fieldset class="wizard-indent detail-group" data-for="stamps"><p class="field-hint">Peel-off labels, one per page of each paper, for papers you print or copy yourself.${seated ? " You choose the pages per paper when you export each seating chart." : ""}</p>
         ${loose && stamps.length ? `<label class="field">Labels for sections without a seating chart<select name="stamps_id" aria-label="Labels for sections without a seating chart">${seated ? '<option value="">None yet: export a seating chart first</option>' : ""}${stamps.map((item) => `<option value="${escape(item.id)}" ${item.id === state.version.id ? "selected" : ""}>${escape(item.label)}</option>`).join("")}</select></label>` : ""}</fieldset>`;
    const form = show("version", "Paper", subtitle(),
      `<p class="section-lead">How is this paper printed?</p>
       ${radio("new", "Faculty prints it from LaTeX")}
       <fieldset class="wizard-indent detail-group" data-for="new"><label class="field" data-source-field ${testing ? "" : "hidden"}>Source<select name="source" aria-label="Source"><option value="compile">Compile uploaded LaTeX</option>${testing ? '<optgroup label="Demonstration (testing)"><option value="letter">Letter demonstration fixture</option><option value="a4">A4 demonstration fixture</option></optgroup>' : ""}</select></label>
         <div class="wizard-new-versions" data-new-versions>${versionRow(true)}</div>
         <button type="button" class="text-button" data-add-version>Add another version</button>
         <p class="field-hint">The source must use the facultyassignment class. <a href="/api/documents/starter/main.tex" download>Download starter main.tex</a> · <a href="/api/documents/starter/facultyassignment.cls" download>Download facultyassignment.cls</a></p></fieldset>
       ${radio("stamps", "Printed elsewhere: label each paper (stamps only)", !stampsAvailable)}
       ${stampsChoice}
       ${pdfs.length ? `${radio("existing", "Use a registered PDF")}
       <fieldset class="wizard-indent detail-group" data-for="existing"><label class="field">Version<select name="version_id" aria-label="Version">${listed.map(({ item, note }) => `<option value="${escape(item.id)}" ${item.id === state.version.id ? "selected" : ""}>${escape(`${item.label} · ${plural(item.page_count, "page")}${note}`)}</option>`).join("")}</select></label><div data-version-preview></div><p class="field-hint">Preview is the unstamped paper. Use Stamp and print for copies with unique identities. Registered PDFs never change; a revised source needs a new version label.</p></fieldset>` : ""}
       ${gradingProblemsMarkup()}
       ${planId ? `<div class="wizard-row wizard-grading-link" data-grading-setup><span>Grading is prepared for this term. Continue with its rubrics, blank templates and answer areas.</span><button type="button" class="button" data-set-up-grading>Prepare questions</button></div>`
         : canSetUp ? '<div class="wizard-row wizard-grading-link" data-grading-setup><span>Grading can be prepared now, before the papers come back: rubrics, answer areas and the answer key.</span><button type="button" class="button" data-set-up-grading>Set up grading now</button></div>'
         : '<p class="wizard-grading-link">Rubrics, blank templates and answer areas are set up in Grading once the scanned papers pass Inbox review.</p>'}`,
      `${state.fromChart ? '<button type="button" class="button" data-action="close">Cancel</button>' : '<button type="button" class="button" data-back>Back</button>'}<button type="button" class="button" data-later>Finish later</button><button type="submit" class="button primary">Next</button>`);
    const rowsHost = form.querySelector("[data-new-versions]"), add = form.querySelector("[data-add-version]");
    if (state.paperDraft) {
      // The rows typed here before (their files are still chosen in them), under the choice and source they were typed with.
      rowsHost.replaceChildren(...state.paperDraft.rows);
      if ([...form.elements.source.options].some((option) => option.value === state.paperDraft.source)) form.elements.source.value = state.paperDraft.source;
      if (state.paperDraft.chosen) form.elements.choice.value = "new";
    }
    function sync() {
      const choice = form.elements.choice.value;
      // Only the chosen way shows its fields.
      form.querySelectorAll("fieldset[data-for]").forEach((fieldset) => { fieldset.disabled = fieldset.hidden = fieldset.dataset.for !== choice; });
      form.querySelectorAll("[data-files]").forEach((field) => { field.hidden = form.elements.source.value !== "compile"; });
      add.hidden = rowsHost.children.length >= 26;  // A stack interleaves at most 26 versions.
      const preview = form.querySelector('[data-version-preview]'), selected = form.elements.version_id?.value;
      if (preview && selected) {
        const version = pdfs.find(item => item.id === selected);
        preview.innerHTML = `<a class="button" ${viewLink('/api/documents/versions/' + encodeURIComponent(selected) + '/preview', 'Preview ' + (version?.label || 'paper'), escape)}>Preview selected version</a>`;
      }
    }
    form.addEventListener("change", sync);
    sync();
    // Another version takes the next letter that no registered version and no other row uses.
    add.onclick = () => {
      const taken = new Set([...existingLabels, ...[...rowsHost.querySelectorAll('input[name="label"]')].map((input) => input.value.trim() || input.placeholder)]);
      rowsHost.insertAdjacentHTML("beforeend", versionRow(false, nextLetter(taken)));
      state.touched = true;
      sync();
      rowsHost.lastElementChild.querySelector('input[name="label"]').focus();
    };
    rowsHost.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove-version]");
      if (!remove || form.dataset.saving === "true") return;
      remove.closest("[data-new-version]").remove();
      sync();
      add.focus();
    });
    rowsHost.addEventListener("input", (event) => {
      const remove = event.target.matches('input[name="label"]') && event.target.closest("[data-new-version]").querySelector("[data-remove-version]");
      if (remove) remove.setAttribute("aria-label", removeName(event.target.value));
    });
    // Set up grading now: a new version typed on this step is registered first, as Next would (its PDF becomes the blank template
    // grading uses); then the plan is made for the window's term and its sections, and Rubrics & Templates opens.
    form.querySelector("[data-set-up-grading]")?.addEventListener("click", () => {
      perform(form, async () => {
        const typed = form.elements.choice.value === "new" && [...form.querySelectorAll("[data-new-version]")]
          .some((row) => !row.dataset.registered && (row.querySelector('input[name="files"]')?.files.length || form.elements.source.value !== "compile"));
        if (typed) await registerVersions(form, existingLabels);
        const title = state.assignment.title;
        const { plan, problems } = await prepareGrading(true);
        if (!plan) {
          const existing = form.querySelector("[data-grading-problems]");
          if (existing) existing.outerHTML = gradingProblemsMarkup();
          else form.querySelector("[data-grading-setup]").insertAdjacentHTML("beforebegin", gradingProblemsMarkup());
          bindGradingRetry(form);
          return;
        }
        const href = workspaceHref("prepare");
        dialog().close();
        state = null;
        location.hash = href;
        if (problems.length) toast(`${title} is saved. Grading preparation needs attention: ${problems.join(" ")}`, { tone: "warning" });
        else toast(`${title} is saved. Continue its questions and grading preparation here.`, { tone: "success" });
      });
    });
    bindGradingRetry(form);
    form.querySelector("[data-back]")?.addEventListener("click", () => state.requires || state.charts.length ? showSeating() : showDetails());
    // ASK FIRST: Finish later asks, as leaving the page does, before a version typed here is left behind (`finishLaterWords`).
    form.querySelector("[data-later]").onclick = () => {
      if (form.dataset.saving === "true") return;
      const { question, notice } = finishLaterWords(state.assignment.title, unfinishedVersions());
      if (question && !window.confirm(question)) return;
      finish(notice);
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async () => {
        const choice = form.elements.choice.value;
        // Next on the paper the step showed keeps how it was chosen: the window's default (auto) and versions registered
        // together stay so; any other choice is the instructor's own, for that one version (create-2).
        const unchanged = choice === "existing" && state.version.choice === "existing" && form.elements.version_id.value === state.version.id;
        if (!unchanged) state.stackVersions = null;
        if (choice === "existing") state.version = unchanged ? state.version : { choice, id: form.elements.version_id.value };
        else if (choice === "stamps") state.version = { choice, id: form.elements.stamps_id?.value || (!seated && stamps.length ? stamps[stamps.length - 1].id : "") };
        else {
          const ids = await registerVersions(form, existingLabels);
          state.version = { choice: "existing", id: ids[ids.length - 1] };
          if (ids.length > 1) state.stackVersions = ids;
        }
        // A later PDF uses the same course-wide import as grading's Prepare template action.
        // Failed imports stay visible there and can be retried; printing remains independent.
        await prepareGrading(true);
        await showPrint();
      });
    };
  }

  // Registers each new version row in turn (one compile each). A row already registered by an earlier attempt keeps its
  // version and is not sent again, so a failure part way leaves the rows before it registered and marked.
  async function registerVersions(form, existingLabels) {
    const source = form.elements.source.value;
    const rows = [...form.querySelectorAll("[data-new-version]")].map((row) => ({ row, input: row.querySelector('input[name="label"]'),
      files: row.querySelector('input[name="files"]'), id: row.dataset.registered || "" }));
    rows.forEach((item) => { item.label = item.input.value.trim(); });
    const one = rows.length === 1;
    if (rows.some((item) => !item.label)) throw new Error(one ? "Enter a label for the new version." : "Enter a label for each new version.");
    if (new Set(rows.map((item) => item.label)).size !== rows.length) throw new Error("Give each new version its own label.");
    const taken = rows.find((item) => !item.id && existingLabels.has(item.label));
    if (taken) throw new Error(`${versionName(taken.label)} is already registered, and registered versions never change. Give the new one another label.`);
    const missing = source === "compile" && rows.find((item) => !item.id && !item.files.files.length);
    if (missing) throw new Error(one ? "Choose main.tex and any assets it needs." : `Choose main.tex and any assets for ${versionName(missing.label)}.`);
    for (const item of rows) {
      if (item.id) continue;
      let version;
      try {
        if (source === "compile") {
          const data = new FormData();
          data.set("assignment_id", state.assignment.id);
          data.set("label", item.label);
          [...item.files.files].forEach((file) => data.append("files", file));
          version = await api("/api/documents/versions/compile", "POST", data, {}, 120000);
        } else version = await api("/api/documents/versions/fixture", "POST", { assignment_id: state.assignment.id, label: item.label, paper: source });
      } catch (cause) {
        const made = rows.filter((row) => row.id).map((row) => row.label);
        throw new Error(made.length ? `${versionsName(made)} registered; ${versionName(item.label)} was not: ${cause.message}` : cause.message);
      }
      item.id = version.id;
      // Registered: the row keeps its label and files, and a later Next does not send it again.
      item.row.dataset.registered = version.id;
      item.input.readOnly = true;
      item.files.disabled = true;
      item.row.querySelector("[data-remove-version]")?.remove();
      item.row.insertAdjacentHTML("beforeend", `<p class="field-hint" data-registered-note>${escape(versionName(item.label))} is registered.</p>`);
    }
    toast(`${versionsName(rows.map((item) => item.label))} registered.`);
    return rows.map((item) => item.id);
  }

  // ----- 4. stamp and print -------------------------------------------------------------

  async function renderPrint() {
    const prior = shownForm;
    state.documents = await api("/api/documents/catalog");
    if (shownForm !== prior) return;
    if (state.version.choice === "stamps" && !state.version.id) {
      // A seating chart exported as stamps only in this window has registered the labels version; with exactly one, the
      // sections without a chart use it too (still only when Create print jobs is pressed).
      const stamps = state.documents.versions.filter((item) => item.assignment_id === state.assignment.id && isStampsOnly(item));
      if (stamps.length === 1) state.version.id = stamps[0].id;
    }
    const own = state.documents.versions.filter((item) => item.assignment_id === state.assignment.id);
    const byId = new Map(own.map((item) => [item.id, item]));
    const pdfs = own.filter((item) => !isStampsOnly(item));
    const version = byId.get(state.version.id);
    const stampsOnly = state.version.choice === "stamps";
    const loose = unseated();
    // Sections without a chart print ONE stack for the term (requirement, 2026-10-01): a job naming no section, any copy for any of their students. Their students, by name.
    const looseIds = new Set(loose.map((item) => item.id));
    const termRoster = loose.length ? await api(`/api/documents/terms/${enc(state.termId)}/enrollments`) : { enrollments: [], students: [] };
    const roster = termRoster.students?.length
      ? { students: termRoster.students.filter((item) => looseIds.has(item.section_id)), named: true }
      : { students: termRoster.enrollments.map((id) => ({ enrollment_id: id, name: id })), named: false };
    const looseCount = roster.named ? new Set(roster.students.map((item) => item.enrollment_id)).size : 0;
    // Charts confirmed before this window opened: what their latest export made, so it is printed rather than exported again.
    const earlier = {};
    await Promise.all(state.charts.filter((chart) => chart.confirmed_at && !state.confirmed[chart.id]).map(async (chart) => {
      earlier[chart.id] = exportsOf((await api(`/api/seating/${enc(chart.id)}`)).print_batches || []);
    }));
    if (shownForm !== prior) return;
    const chartRows = state.charts.map((chart) => {
      const done = state.confirmed[chart.id];
      const exports = earlier[chart.id] || [];
      const latest = exports[0];
      const files = latest ? exportFiles(chart.id, latest.batches) : "";
      const filesReady = latest && latest.batches.every(downloadable);
      const jobButtons = (done ? done.jobs.map((job) => ({ id: job.id, section_number: job.section_number })) : latest ? latest.batches : [])
        .map((job, index) => `<button type="button" class="button${!done && latest && !filesReady && index === 0 ? " primary" : ""}" data-job="${escape(job.id)}">Open Section ${job.section_number} job</button>`).join("");
      const status = done
        ? `Confirmed: ${done.jobs.length ? done.jobs.map((job) => `Section ${job.section_number} · ${plural(job.quantity, "copy", "copies")}`).join(", ") : "no print jobs"}.${done.mailMerge ? " Cards are published for Gmail mail merge." : ""}`
        : latest ? `${exportSummary(latest)}${exports.length > 1 ? ` · ${plural(exports.length - 1, "earlier export")} on the chart` : ""}`
        : chart.confirmed_at ? "Confirmed earlier. Exporting again creates new print jobs." : "Not confirmed yet.";
      const again = done || chart.confirmed_at;
      return `<div class="wizard-row" data-chart="${escape(chart.id)}"><div><strong>${escape(chart.room_name)}</strong> · ${escape(sectionList(chart.sections))} · ${chart.seated_count} seated<br><span class="field-hint" data-export-status>${escape(status)}</span></div><div class="wizard-row-actions">${done?.mailMerge ? '<button type="button" class="button" data-mail-merge>Gmail mail merge</button>' : ""}${files}${jobButtons}<a class="button" href="#seating/${enc(chart.id)}" data-review>Review chart</a><button type="button" class="button ${again ? "" : "primary"}" data-confirm>${again ? "Export again (new stamps)" : "Confirm and export"}</button></div>${again ? '<p class="field-hint wizard-row-note">Exporting again creates new print jobs with new stamps for every section; the earlier jobs stay valid until you supersede them.</p>' : ""}</div>`;
    }).join("");
    const looseNames = sectionList(loose.map((item) => item.number));

    // What these sections already have, version by version (create-1, print-3): the assignment's current jobs without a chart
    // (the term's stacks, or an earlier section's job; not registration, superseded or cancelled), grouped by export. A
    // job this window queued that the catalog's newest 100 do not list still counts.
    const current = state.documents.batches.filter((batch) => (batch.section_id ? looseIds.has(batch.section_id) && !batch.seating_chart_id : batch.term_id === state.termId)
      && byId.has(batch.assignment_version_id) && batch.origin !== "registration" && !batch.superseded_at && batch.status !== "cancelled");
    state.jobs.filter((job) => !state.documents.batches.some((batch) => batch.id === job.id)).forEach((job) => current.push({ id: job.id,
      assignment_version_id: job.version_id, quantity: job.quantity, created_at: job.created_at, export: job.group, section_id: null, status: "pending", superseded_at: null }));
    const entries = printedEntries(current);
    // A stack this window started and could not finish (a version's job failed) stays one stack: its made jobs are found
    // by its request prefix, and Create print jobs sends only the rest, with the same request IDs. A version of it printed
    // apart meanwhile (another tab) counts as done and is never sent again. The stack is the step's own while the version
    // on screen is one of its versions; it lasts only as long as this window.
    let apart = [];
    if (state.stack) {
      const stack = state.stack;
      current.filter((batch) => batch.export === stack.id).forEach((batch) => { stack.made[batch.assignment_version_id] = batch.id; });
      apart = stack.versions.filter((id) => !stack.made[id] && entries.some((entry) => entry.export !== stack.id && entry.versions.includes(id)));
      if (apart.includes(stack.failed)) stack.failure = "";  // The version that failed has been printed apart since.
      if (!Object.keys(stack.made).length || stack.chosen.every((id) => stack.made[id] || apart.includes(id))) state.stack = null;
    }
    const activeStack = state.stack && version && state.stack.versions.includes(version.id) ? state.stack : null;
    if (!activeStack) apart = [];
    const mine = (entry) => entry.jobs.every((batch) => state.jobs.some((job) => job.id === batch.id));
    const touches = (entry) => entry.versions.includes(state.version.id);
    const pending = entries.filter((entry) => entry.export === activeStack?.id);
    const settled = entries.filter((entry) => !pending.includes(entry));
    const queued = settled.filter((entry) => mine(entry) && touches(entry));
    const already = settled.filter((entry) => !mine(entry) && touches(entry));
    const others = settled.filter((entry) => !touches(entry));
    const labelsOf = (ids) => ids.map((id) => byId.get(id)?.label ?? "").sort(byLabel);
    const copiesOf = (entry) => entry.jobs.reduce((sum, batch) => sum + batch.quantity, 0);
    const sectionOf = (batch) => state.sections.find((item) => item.id === batch.section_id)?.number ?? "";
    const pagesOf = (ids) => {
      const counts = ids.map((id) => byId.get(id)?.page_count || 0);
      const low = Math.min(...counts), high = Math.max(...counts);
      return low === high ? `${plural(low, "page")} each` : `${low}–${high} pages`;
    };
    const stackBatches = (entry) => entry.jobs.map((batch) => ({ ...batch, version: { label: byId.get(batch.assignment_version_id)?.label ?? "" } }))
      .sort((a, b) => byLabel(a.version.label, b.version.label));
    const openFor = (entry) => entry.jobs.find((batch) => batch.assignment_version_id === state.version.id) || entry.jobs[0];
    const namesOf = (entry) => (entry.stack ? versionsName(labelsOf(entry.versions)) : paperName(byId.get(entry.versions[0])));
    const printedFact = (entry) => [shortDay(entry.created), entry.stack ? "one stack" : entry.jobs[0].section_id ? `Section ${sectionOf(entry.jobs[0])}` : "",
      namesOf(entry), plural(copiesOf(entry), "copy", "copies")].filter(Boolean).join(" · ");
    const otherFact = (entry) => [namesOf(entry), shortDay(entry.created), entry.stack ? "one stack" : entry.jobs[0].section_id ? `Section ${sectionOf(entry.jobs[0])}` : "",
      plural(copiesOf(entry), "copy", "copies")].filter(Boolean).join(" · ");

    // One stack, every version (create-2): one copies field per version, the students shared out (the first versions one
    // more, as a drafted quiz's approval does); one job per version with the same request prefix, so the Print page lists
    // them as one group whose Print PDF interleaves them. Its versions: those registered together in this window (Add
    // another version), else every PDF version that is not an earlier layout; none of them printed for these sections yet.
    // The stack is the step's own only for versions registered together, or when the paper is the window's own default
    // (Continue, a card's stage link). A version the instructor registered alone or picked prints alone first, the stack
    // folded under it: a revised version must never print beside the one it replaces by default.
    const together = (state.stackVersions || []).filter((id) => pdfs.some((item) => item.id === id));
    const stackIds = activeStack ? activeStack.versions
      : together.length > 1 ? together : pdfs.filter((item) => !versionPrinting(item.id).retired).map((item) => item.id);
    const stackPrinted = entries.some((entry) => entry.versions.some((id) => (together.length > 1 ? together.includes(id) : !isStampsOnly(byId.get(id)))));
    const offered = Boolean(loose.length && !stampsOnly && version && !isStampsOnly(version)
      && (activeStack || (stackIds.length > 1 && stackIds.length <= 26 && stackIds.includes(version.id) && !stackPrinted)));
    const stackMode = offered && Boolean(activeStack || together.length > 1 || state.version.auto);
    const stackFolded = offered && !stackMode;
    const outside = stackMode ? settled.filter((entry) => !entry.versions.some((id) => stackIds.includes(id))) : others;
    const apartEntries = stackMode ? settled.filter((entry) => !outside.includes(entry)) : [];
    const stackEntry = [...queued, ...already].find((entry) => entry.stack);
    const leadIds = stackMode ? stackIds : stackEntry ? stackEntry.versions : null;

    // An earlier stack offers its interleaved file beside its job (compact: the file, Other formats in its menu, the job).
    const alreadyMarkup = already.length ? `<p class="field-hint wizard-row-note" data-printed-before>Already printed: ${escape(already.map(printedFact).join("; "))}. Enter a number only to print more, with new stamps.</p><div class="wizard-row-actions wizard-printed-actions">${already.map((entry) => `${entry.stack ? stackFiles(stackBatches(entry), { compact: true }) : ""}<button type="button" class="button" data-open-job="${escape(openFor(entry).id)}">Open ${escape(shortDay(entry.created))} job</button>`).join("")}</div>` : "";
    const othersMarkup = outside.length ? `<p class="field-hint wizard-row-note" data-printed-other>Other versions printed: ${escape(outside.map(otherFact).join("; "))}.</p>` : "";
    const apartMarkup = apartEntries.length ? `<p class="field-hint wizard-row-note" data-printed-apart>Printed apart from this stack: ${escape(apartEntries.map(otherFact).join("; "))}.</p>` : "";
    // With several PDF versions the copies field says which one it prints (print-3 c); its accessible name stays.
    const several = pdfs.length > 1 && version && !isStampsOnly(version);
    const singleForm = `<div class="wizard-row" data-term-stack>${alreadyMarkup}<label class="field">${escape(looseNames)} · physical copies${several ? ` of ${escape(versionName(version.label))}` : ""}<input type="number" name="quantity" min="0" max="500" value="${already.length ? 0 : Math.min(500, looseCount)}" aria-label="Physical copies for ${escape(looseNames)}"></label>${several ? `<p class="field-hint wizard-row-note" data-version-only>Prints this version only.${entries.some((entry) => entry.stack) ? " To print more of every version, open the stack’s job and use Print more copies…" : ""}</p>` : ""}${looseCount ? `<label class="checkbox-field wizard-named"><input type="checkbox" name="named">One copy per student on the roster (named)</label><p class="field-hint wizard-named-hint">${plural(looseCount, "student")} in ${escape(looseNames)}; each copy is linked to one of them.</p>` : ""}<div class="wizard-row-actions wizard-create-job"><button type="button" class="button" data-create-jobs>Create print job</button></div><details class="disclosure" data-copy-by-copy><summary><span>Choose students copy by copy</span></summary><div class="disclosure-body wizard-students"></div></details></div>`;
    // The stack's own section; folded under the one-version form, the disclosure's summary names it instead of a heading.
    const stackSection = (folded) => {
      const made = activeStack?.made || {};
      const madeIds = stackIds.filter((id) => made[id]);
      const fields = stackIds.map((id, index) => {
        const name = paperName(byId.get(id));
        const job = made[id] && current.find((batch) => batch.id === made[id]);
        const away = apart.includes(id);
        const value = job ? job.quantity : away ? 0 : activeStack?.copies?.[id] ?? Math.min(500, Math.floor(looseCount / stackIds.length) + (index < looseCount % stackIds.length ? 1 : 0));
        const note = job ? "Queued" : away ? "Printed apart" : "";
        return `<label class="field">Copies of ${escape(name)}<input type="number" min="0" max="500" value="${escape(value)}" data-stack-version="${escape(id)}" aria-label="Copies of ${escape(name)}" ${note ? "disabled" : ""}>${note ? `<span class="field-hint">${note}</span>` : ""}</label>`;
      }).join("");
      const callout = madeIds.length ? `<div data-stack-made>${calloutMarkup({ tone: "warning", title: `${versionsName(labelsOf(madeIds))} ${madeIds.length === 1 ? "is" : "are"} queued; the rest of the stack is not.`,
        body: `${activeStack.failure ? `${activeStack.failure} ` : ""}Create print jobs queues the rest in the same stack. Keep this window open until it does: once it closes, the rest can only print apart from this stack.` })}</div>` : "";
      return `<section class="wizard-stack" data-stack-every-version ${folded ? 'aria-label="One stack, every version"' : 'aria-labelledby="wizard-stack-heading"'}>${folded ? "" : '<h4 class="wizard-subheading" id="wizard-stack-heading">One stack, every version</h4>'}<p class="supporting-copy">The versions alternate copy by copy, so every section gets all of them.</p>${callout}<div class="wizard-stack-copies">${fields}</div><p class="field-hint" data-stack-total></p><div class="wizard-row-actions"><button type="button" class="button" data-create-stack>Create print jobs</button></div></section>`;
    };
    // While a stack is part made, one version alone is not offered: its versions print in the stack, so none prints twice.
    const oneVersion = activeStack ? "" : `<details class="disclosure wizard-one-version" data-one-version><summary><span>Print one version only</span></summary><div class="disclosure-body"><div class="detail-group">${singleForm}</div></div></details>`;
    const foldedStack = !stackFolded ? "" : `<details class="disclosure wizard-one-version" data-stack-option><summary><span>Print every version as one stack</span></summary><div class="disclosure-body">${stackSection(true)}</div></details>`;
    const looseBody = !version ? `<p class="supporting-copy" data-loose-waiting>${stampsOnly ? "Export a seating chart as stamps only first: its labels version then prints these sections too." : "Register a PDF version to print copies for these sections."}</p>`
      : `<p class="supporting-copy">One stack for ${escape(looseNames)}: the stamps name no section, so any copy can go to any of their students. Copies are unassigned unless you link them to students.</p>${stackMode ? `${othersMarkup}${apartMarkup}${stackSection(false)}${oneVersion}` : `<div class="detail-group">${othersMarkup}${queued.length ? queued.map((entry) => jobMarkup(entry, { names: namesOf(entry), copies: copiesOf(entry), files: entry.stack ? stackFiles(stackBatches(entry), { compact: true }) : "", open: openFor(entry) })).join("") : singleForm}</div>${foldedStack}`}`;
    const lead = stampsOnly ? `Stamps only${version ? ` · ${escape(version.label.replace(/^Stamps only · /, ""))} per paper` : ""}: labels for papers printed elsewhere.`
      : leadIds ? `${escape(versionsName(labelsOf(leadIds)))} · ${pagesOf(leadIds)}.`
      : version ? `${escape(versionName(version.label))} · ${plural(version.page_count, "page")}.` : "No paper chosen.";
    const form = show("print", "Stamp and print", subtitle(),
      `<p class="section-lead">${lead}</p><p class="supporting-copy">Every copy gets its own Data Matrix page stamps. Print at actual size with the printer’s copy count set to <strong>1</strong>.</p>${gradingProblemsMarkup()}
       ${chartRows ? `<section aria-labelledby="wizard-seating-jobs"><h3 class="wizard-heading" id="wizard-seating-jobs">Seating charts</h3><p class="supporting-copy">Review a chart to adjust seats. Confirming creates one print job per section in seat order and offers the response cards.</p><div class="detail-group">${chartRows}</div></section>` : ""}
       ${loose.length ? `<section aria-labelledby="wizard-section-jobs"><h3 class="wizard-heading" id="wizard-section-jobs">${state.charts.length ? "Sections without a seating chart" : "Print jobs"}</h3>${looseBody}</section>` : ""}`,
      `<button type="button" class="button" data-back>Back</button><button type="submit" class="button primary">${state.canvas ? "Next" : "Done"}</button>`);
    bindGradingRetry(form);
    // Done names what is still unprinted for these sections (create-1).
    const missing = stackMode ? stackIds.filter((id) => !activeStack?.made[id] && !apart.includes(id)) : [];
    state.unprinted = !loose.length || !version ? ""
      : stackMode ? `${versionsName(labelsOf(missing))} ${missing.length === 1 ? "has" : "have"} no print job yet.`
      : queued.length || already.length ? ""
      : stampsOnly ? `The labels for ${looseNames} have no print job yet.` : `${versionName(version.label)} has no print job yet.`;
    // One primary: when the body holds the step's action (create jobs, confirm, the room files, a job to open), Done is
    // secondary; with sections still to create, Create print job(s) is that action. Done leads only when nothing is left.
    // When this version was printed already (its copies start at 0), the step's action is that print: the stack's file
    // when it is ready, else its job; Create print job is then for printing more.
    const create = form.querySelector("[data-create-jobs]"), stackCreate = form.querySelector("[data-create-stack]");
    const printed = form.querySelector(".wizard-printed-actions");
    const action = (stackMode && stackCreate) || (printed ? printed.querySelector(".print-files > a.button") || printed.querySelector("[data-open-job]") : create);
    if (action && !form.querySelector(".dialog-body .primary")) action.classList.add("primary");
    if (form.querySelector(".dialog-body .primary")) form.querySelector('.dialog-footer button[type="submit"]').classList.remove("primary");
    form.querySelector("[data-back]").onclick = () => { void showVersion(); };
    form.onsubmit = (event) => { event.preventDefault(); if (state.canvas) void showCanvas(); else done(); };
    // Enter in a field of the step never ends the window (the footer's submit is the form's default button): in a copies
    // field it presses that field's own Create print job or Create print jobs.
    form.querySelector(".dialog-body").addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !(event.target instanceof HTMLInputElement)) return;
      event.preventDefault();
      if (event.target.type !== "number") return;
      const button = event.target.closest("[data-stack-every-version], [data-term-stack]")?.querySelector("[data-create-stack], [data-create-jobs]");
      if (button && !button.disabled) button.click();
    });
    const leave = () => { dialog().close(); state = null; };
    form.querySelectorAll("[data-review]").forEach((link) => link.addEventListener("click", leave));
    form.querySelectorAll("[data-job]").forEach((button) => { button.onclick = () => { const id = button.dataset.job; state = null; openBatch(id); }; });
    form.querySelectorAll("[data-chart]").forEach((row) => {
      const chart = state.charts.find((item) => item.id === row.dataset.chart);
      row.querySelector("[data-confirm]").onclick = () => { handoff = true; return getSeating().confirmChart({ chartId: chart.id, versionId: state.version.id,
        mode: stampsOnly ? "stamps_only" : "print", onBack: () => { void showPrint(); },
        onDone: (result, info) => {
          chart.seated_count = result.counts?.seated ?? chart.seated_count;
          state.confirmed[chart.id] = { jobs: result.exported_batches || [], mailMerge: info.mailMerge, chart: result };
          void showPrint();
        } }); };
      const merge = row.querySelector("[data-mail-merge]");
      if (merge) merge.onclick = () => { const result = state.confirmed[chart.id].chart; state = null; void getSeating().openMailMerge(result); };
    });
    form.querySelectorAll("[data-term-stack]").forEach((row) => {
      const holder = row.querySelector(".wizard-students"), quantity = row.querySelector('input[name="quantity"]');
      const named = row.querySelector('input[name="named"]'), byCopy = row.querySelector("[data-copy-by-copy]");
      // The picker groups the students by their own section.
      const groups = roster.named ? loose.map((item) => ({ label: `Section ${item.number}`, options: roster.students.filter((student) => student.section_id === item.id)
        .map((student) => ({ value: student.enrollment_id, text: student.name })) })).filter((group) => group.options.length)
        : [{ label: "Roster", options: roster.students.map((student) => ({ value: student.enrollment_id, text: student.name })) }];
      const draw = () => {
        const count = Math.min(500, Math.max(0, Number(quantity.value) || 0));
        // The students already chosen stay with their copies when the number of copies changes (2026-10-11: a third copy cleared
        // the two already assigned); a copy added starts unassigned, and one taken away takes its choice with it.
        const chosen = [...holder.querySelectorAll('input[type="hidden"][name="student"]')].map((input) => input.value);
        const pickers = Array.from({ length: count }, (_, index) => studentPicker(escape, { id: `wizard-copy-term-${index + 1}`, name: "student",
          label: `Copy ${index + 1} student`, value: chosen[index] || "",
          groups: [{ label: "", options: [{ value: "", text: "Unassigned" }] }, ...groups] }));
        holder.innerHTML = pickers.map((picker, index) => `<div class="field wizard-copy">Copy ${index + 1}${picker.markup}</div>`).join("");
        pickers.forEach((picker) => picker.bind(holder));
      };
      byCopy.addEventListener("toggle", (event) => { if (event.target.open && !holder.children.length) draw(); });
      // Folded with choices made, the list follows the count too, so those choices are still the ones sent.
      quantity.onchange = () => { if (byCopy.open || holder.children.length) draw(); };
      if (named) named.onchange = () => {
        // Named: exactly one copy per roster student, so the count and the copy-by-copy choices follow the roster.
        quantity.disabled = named.checked;
        if (named.checked) { quantity.value = String(Math.min(500, looseCount)); byCopy.open = false; holder.innerHTML = ""; }
        byCopy.hidden = named.checked;
      };
    });
    // A queued job is remembered with its version and export (the request prefix), so this step shows it under its own version.
    const remember = (batch, versionId, quantity, group) => {
      if (!state.jobs.some((job) => job.id === batch.id)) state.jobs.push({ id: batch.id, label: looseNames, quantity, version_id: versionId,
        group, created_at: batch.created_at || new Date().toISOString() });
    };
    let requestId = crypto.randomUUID();
    form.addEventListener("change", (event) => { if (!event.target.closest("[data-stack-every-version]")) requestId = crypto.randomUUID(); });
    if (create) create.onclick = () => perform(form, async () => {
      const row = form.querySelector("[data-term-stack]");
      const quantity = Number(row.querySelector('input[name="quantity"]').value) || 0;
      if (quantity <= 0) throw new Error("Enter the number of physical copies.");
      const named = row.querySelector('input[name="named"]')?.checked;
      const order = [...new Set(roster.students.map((item) => item.enrollment_id))];
      if (named && order.length > 500) throw new Error("A named print job holds at most 500 copies.");
      const picked = [...row.querySelectorAll('input[type="hidden"][name="student"]')].map((input) => input.value || null);
      const students = named ? order : picked.length === quantity ? picked : Array(quantity).fill(null);
      const batch = await api("/api/documents/batches", "POST", { version_id: state.version.id, section_id: null, term_id: state.termId,
        section_ids: loose.map((item) => item.id), quantity: students.length, students, request_id: `${requestId}:term` });
      remember(batch, state.version.id, students.length, requestId);
      toast("Print job queued. The Inbox worker will validate its pages.");
      await showPrint();
    });
    // The stack's copies: a running total, and one job per version with copies, all under one request prefix.
    const stackFields = [...form.querySelectorAll("input[data-stack-version]")];
    const total = form.querySelector("[data-stack-total]");
    const countTotal = () => {
      if (!total) return;
      const copies = stackFields.reduce((sum, input) => sum + Math.max(0, Number(input.value) || 0), 0);
      total.textContent = `${plural(copies, "copy", "copies")} in all${looseCount ? ` for ${plural(looseCount, "student")} in ${looseNames}` : ""}.`;
    };
    countTotal();
    stackFields.forEach((input) => input.addEventListener("input", countTotal));
    if (stackCreate) stackCreate.onclick = () => perform(form, async () => {
      const copies = Object.fromEntries(stackFields.filter((input) => !input.disabled).map((input) => [input.dataset.stackVersion, Number(input.value)]));
      if (Object.values(copies).some((count) => !Number.isInteger(count) || count < 0 || count > 500)) throw new Error("Copies are whole numbers from 0 to 500 for each version.");
      // The stack being made keeps its ID (a retry after a failure sends the same request IDs); other versions start a new one.
      const stack = state.stack && state.stack.versions.join() === stackIds.join() ? state.stack : { id: crypto.randomUUID(), versions: stackIds, made: {} };
      const chosen = stack.versions.filter((id) => stack.made[id] || copies[id] > 0);
      if (!chosen.some((id) => !stack.made[id])) throw new Error("Enter the copies of at least one version.");
      Object.assign(stack, { chosen, copies: { ...stack.copies, ...copies }, failure: "" });
      state.stack = stack;
      let failure = null;
      for (const id of chosen) {
        if (stack.made[id]) continue;
        const count = copies[id];
        try {
          // One request ID per version, by its place in the stack: a retry sends the same IDs, so nothing is made twice.
          const batch = await api("/api/documents/batches", "POST", { version_id: id, section_id: null, term_id: state.termId, section_ids: loose.map((item) => item.id),
            quantity: count, students: Array(count).fill(null), request_id: `${stack.id}:${String.fromCharCode(65 + stack.versions.indexOf(id))}` });
          stack.made[id] = batch.id;
          remember(batch, id, count, stack.id);
        } catch (cause) {
          failure = { id, cause };
          break;
        }
      }
      if (!failure) {
        state.stack = null;
        toast(`${versionsName(labelsOf(chosen))}: print jobs queued as one stack. The Inbox worker will validate their pages.`);
        await showPrint();
        return;
      }
      // Nothing made: the error shows as any other. Part of the stack made: the step is drawn again to show it, with the
      // reason the rest was not made beside it.
      if (!chosen.some((id) => stack.made[id])) throw failure.cause;
      Object.assign(stack, { failed: failure.id, failure: `${paperName(byId.get(failure.id))} was not: ${failure.cause.message}` });
      await showPrint();
    });
    form.querySelectorAll("[data-open-job]").forEach((button) => { button.onclick = () => { const id = button.dataset.openJob; state = null; openBatch(id); }; });
  }

  // Print jobs without a chart by export (one Create print job click, one stack), newest first. An export holding several
  // versions is one stack; any other export's jobs stand alone, each with its own version and section.
  function printedEntries(batches) {
    const groups = new Map();
    batches.forEach((batch) => {
      const key = batch.export || batch.id;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(batch);
    });
    const entry = (key, jobs, stack) => ({ export: key, jobs, stack, versions: [...new Set(jobs.map((batch) => batch.assignment_version_id))],
      created: jobs.reduce((latest, batch) => (batch.created_at > latest ? batch.created_at : latest), "") });
    return [...groups].flatMap(([key, jobs]) => (new Set(jobs.map((batch) => batch.assignment_version_id)).size > 1 ? [entry(key, jobs, true)] : jobs.map((batch) => entry(key, [batch], false))))
      .sort((a, b) => b.created.localeCompare(a.created));
  }

  // A chart's print jobs by export (one confirmation = one request prefix), newest first.
  function exportsOf(batches) {
    const found = new Map();
    batches.forEach((batch) => {
      if (!found.has(batch.export)) found.set(batch.export, []);
      found.get(batch.export).push({ ...batch, stamps_only: isStampsOnly({ label: batch.version_label }) });
    });
    return [...found.values()].map((items) => ({ batches: items.sort((a, b) => a.section_number - b.section_number),
      created: items.reduce((latest, item) => item.created_at > latest ? item.created_at : latest, "") }))
      .sort((a, b) => b.created.localeCompare(a.created));
  }

  function exportSummary(group) {
    const items = group.batches;
    const copies = items.reduce((sum, item) => sum + item.quantity, 0);
    const status = items.some((item) => item.status === "failed") ? "Needs attention"
      : items.some((item) => ["pending", "rendering", "validating"].includes(item.status)) ? "Preparing"
      : items.every((item) => item.superseded_at) ? "Superseded"
      : items.every((item) => item.status === "cancelled") ? "Cancelled" : items.every(downloadable) ? "Ready" : "Partly ready";
    return `Exported ${shortDay(group.created)} · ${sectionList(items.map((item) => item.section_number))} · ${plural(copies, "copy", "copies")} · ${status}`;
  }

  // A stack this window queued for the sections without a chart, named by its version(s).
  function jobMarkup(entry, { names, copies, files, open }) {
    const label = state.jobs.find((job) => job.id === entry.jobs[0].id)?.label || "";
    return `<div class="wizard-row" data-queued-job><div><strong>${escape([names, label].filter(Boolean).join(" · "))}</strong> · one stack · ${plural(copies, "copy", "copies")} queued<br><span class="field-hint">${entry.stack ? "Its Print PDF alternates the versions copy by copy. " : ""}The Inbox worker stamps and validates the pages; open the job to download its PDF or labels.</span></div><div class="wizard-row-actions">${files}<button type="button" class="button" data-open-job="${escape(open.id)}">Open print job</button></div></div>`;
  }

  return { open, openForAssignment };
}
