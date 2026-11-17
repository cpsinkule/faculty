// @ts-nocheck
/* Quizzes drafted by a model from the instructor's material (requirement, 2026-09-28; grading/quiz_drafts.py).

   Assignments & printing offers "Draft a quiz with a model"; each draft has its own page, #documents/quiz/<id>: the printed pages of
   every version as Faculty compiled them, each problem with its rubric (every version's wording, shared points), the solutions, and
   two ways on: ask for changes (the model makes a new revision) or approve, which the worker carries out (assignment, printed
   versions, grading setup, one unnamed print job per version printed for the term as one stack, naming no section: instructor,
   2026-10-01). A draft never approved may be scrapped from its ⋯ menu (requirement, 2026-10-01): it leaves the list, and its page
   says so. Each problem shows the answer key read for it while it was drafted, and the approval sent from this page (`save_keys`)
   saves those keys with the questions (requirement, 2026-10-11). Since the same day ("Rubric questions at
   draft review") a revision ends with the rubric check Key & Rubric Finalization would run: its decisions are answered here (the
   finalization's own decisions view, read only: grading-rubric-concerns.js), each rubric line shows the guidance proposed for the
   graders, and the approval window offers one unticked box that records the finalization with the approval. This module stores
   nothing itself: every write is an ordinary request the server checks. */
import { mountBlueprint, blueprintMarkup } from "./quiz-blueprint.js";
import { mathText, renderMath } from "./grading-math.js";
import { helpers } from "./grading-ui.js";
import { mountRubricConcerns } from "./grading-rubric-concerns.js";
import { choicesMarkup, readChoices } from "./grading-rubric-choices.js";
import { actionMenuMarkup, bindActionMenu, calloutMarkup, disclosureMarkup, emptyMarkup, registerLeaveGuard, scrollBehavior, wallDate, whenVisible, setBusy, captureDialogIntent, dialogSession, plural } from "./ui.js";
import { createCanvasPlacement } from "./canvas-placement.js";
import { viewLink } from "./file-viewer.js";
import { attachPrefill, openRows } from "./schedule-prefill.js";
import { openAimSend } from "./aim-signin.js";
import { answerMarkup, checksMarkup, countMarkup, cropMarkup, keyBasisMarkup, keyCounts, keyReadMarkup, problemCrop, proseMarkup, reviewNotes, saidMarkup, wholeMarkup } from "./review-notes.js";

// An approved quiz being rebuilt in the current page layout is listed while it rebuilds (QUIZZES "REBUILDING AN APPROVED QUIZ").
const STATE_NAMES = { drafting: "Drafting", ready: "Ready for you", failed: "Needs attention", approving: "Approving", approved: "Approved", rebuilding: "Rebuilding", scrapped: "Scrapped" };
const STATE_TONES = { drafting: "info", ready: "success", failed: "danger", approving: "info", approved: "neutral", rebuilding: "info", scrapped: "neutral" };
const listed = (names) => (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : String(names[0] ?? ""));

/** The approval window's first line (2026-10-06): what approving will do as the window stands, in plain text: what it prints, for
 * whom, and what still waits. Since 2026-10-11 ("Carry the key through") it also says how many answer keys approving saves: the
 * keys the draft shows beside its problems (`keys`: review-notes.js `keyCounts`; null for a revision that read none, which reads
 * as before). And since the same day ("Rubric questions at draft review") what it leaves in Key & Rubric Finalization, in three
 * forms (`rubric`; null for a revision with no finished rubric check, which reads as before): the finalization recorded with the
 * approval (the box ticked), the check carried there with only the confirmation left, or everything still to do there.
 * @param {{copies: number, versions: string[], sections: string[], keys?: {read: number, missing: number}|null,
 *   rubric?: {open: number, finalize: boolean}|null}} window */
export function approvalLead({ copies, versions, sections, keys = null, rubric = null }) {
  const where = sections.length ? ` for ${sections.length === 1 ? "Section" : "Sections"} ${listed(sections)}` : "";
  const prints = `Approving prints ${plural(copies, "copy", "copies")} of ${versions.length > 1 ? `Versions ${listed(versions)}` : "the quiz"} exactly as shown${where}, as one stack`;
  const waits = !rubric ? "Key & Rubric Finalization still waits for you."
    : rubric.finalize ? "It also records the rubric finalization: nothing is left to do in Key & Rubric Finalization."
    : `The rubric check made here goes with it${rubric.open ? `, with ${plural(rubric.open, "decision")} still open` : ""}: Key & Rubric Finalization waits only for your confirmation.`;
  if (!keys) return `${prints}, and sets up its grading. ${waits}`;
  if (!keys.read) return `${prints}, and sets up its grading. No answer key could be read, so none is saved. ${waits}`;
  return `${prints}, sets up its grading and saves the ${keys.read === 1 ? "answer key" : `${keys.read} answer keys`} shown${keys.missing ? ` (${keys.missing} could not be read)` : ""}. ${waits}`;
}

/** The approval window's one unticked box (requirement, 2026-10-11): what ticking it confirms, in
 * the numbers of the rubric check shown (`summary.rubric_check`: the clarifications and exclusions proposed, the decisions the
 * instructor answered). Offered only for a finished check with nothing open; the server checks that again.
 * @param {{clarifications: number, exclusions: number, answered: number}} counts */
export function finalizeLabel(counts) {
  const guidance = [plural(counts.clarifications, "clarification"), plural(counts.exclusions, "exclusion"),
    ...(counts.answered ? [`your ${counts.answered === 1 ? "answer to 1 decision" : `answers to ${counts.answered} decisions`}`] : [])];
  return `Also confirm the rubric guidance as shown (${guidance.join(", ")}). This is the rubric finalization.`;
}

/** The draft page's one line about a revision's rubric check: how many decisions are open, or that nothing needs one, with what is
 * proposed for the graders. Empty for a revision that ran no check; a check that failed is one of the revision's marked warnings.
 * @param {any} counts `summary.rubric_check` */
export function rubricCheckLead(counts) {
  if (!counts || counts.status !== "complete") return "";
  const open = counts.decisions + counts.overflow;
  if (open) return `${plural(open, "rubric decision")} open. Answer ${open === 1 ? "it" : "them"} below, or later in Key & Rubric Finalization.`;
  return `No rubric decision is open. Proposed for the graders: ${plural(counts.clarifications, "clarification")} and ${plural(counts.exclusions, "exclusion")}${
    counts.answered ? `, with your ${counts.answered === 1 ? "answer" : `${counts.answered} answers`} applied` : ""}.`;
}

// The notes sections a title or instructions name ("Sections 4.1–4.3", "Section 3.2", "3.1, 3.2 and 3.4", "Chapter 3"), a range
// taking every section between its ends: grading/drafting_tools.py `covered_sections`, which the server applies when none are sent.
export function namedSections(sections, texts) {
  const named = new Set(), ranges = [], chapters = new Set();
  const key = (a, b) => Number(a) * 100 + Number(b);
  for (const text of texts) {
    const said = String(text || "");
    for (const m of said.matchAll(/(?<![\d.])(\d{1,2})\.(\d{1,2})\s*(?:-{1,3}|–|—|to|through|thru)\s*(\d{1,2})\.(\d{1,2})(?![\d.])/gi)) ranges.push([key(m[1], m[2]), key(m[3], m[4])]);
    for (const m of said.matchAll(/(?<![\d.])(\d{1,2})\.(\d{1,2})(?![\d.])/g)) named.add(key(m[1], m[2]));
    for (const m of said.matchAll(/\bchapters?\s+(\d{1,2}(?:\s*(?:,|and|&|-|–)\s*\d{1,2})*)/gi)) {
      for (const n of m[1].match(/\d+/g)) chapters.add(Number(n));
      for (const [, a, b] of m[1].matchAll(/(\d+)\s*[-–]\s*(\d+)/g)) for (let n = Number(a); n <= Number(b); n += 1) chapters.add(n);
    }
  }
  return sections.filter((s) => {
    const m = /^(\d{1,2})\.(\d{1,2})$/.exec(String(s.number));
    if (!m) return false;
    const k = key(m[1], m[2]);
    return named.has(k) || chapters.has(Number(m[1])) || ranges.some(([lo, hi]) => lo <= k && k <= hi);
  }).map((s) => s.number);
}

/** The approved quiz's 'Given on' line (requirement, 2026-10-02), from what the approval did (its progress), never the typed date
 * alone: the exam record's main date once saved, or the earlier date its Exams page already held and kept (that page changes it). */
export function givenOnMarkup(approval, escape) {
  const typed = approval?.input?.given_on;
  if (!typed) return "";
  const plan = approval.progress?.plan || {};
  const event = plan.event_id || approval.result?.event_id;
  if (plan.given_on_kept && plan.given_on) {
    const change = event ? `<a href="#exams/${escape(encodeURIComponent(event))}">Change it on its Exams page</a>` : "Change it on its Exams page";
    return ` Its exam record already had ${escape(wallDate(plan.given_on))}, so that date was kept instead of ${escape(wallDate(typed))}. ${change}.`;
  }
  return ` Given on ${escape(wallDate(plan.given_on || typed))}: it is on Home’s Coming up and the Calendar.`;
}

/** SEND TO AIM AFTER APPROVAL (SCHEDULING "SEND TO AIM", requirement, 2026-10-27): the term's ready DR quizzes (GET …/aim-send) this approved quiz is, by its assignment
 *  or, for a class-schedule row not linked to it, by its title. */
export function aimMatches(plan, assignmentId, title) {
  const named = String(title || "").trim().toLowerCase();
  return (plan?.quizzes || []).filter((quiz) => (assignmentId && quiz.assignment_id === assignmentId) || (named && String(quiz.title).trim().toLowerCase() === named));
}

export function createQuizDrafts({ api, escape, toast, showDialog, dialogHeader, header, onChanged }) {
  // "Put it on Canvas" once approved (canvas-placement.js): the quiz's assignment into the term's Canvas copy, its total as points.
  const placement = createCanvasPlacement({ api, escape, toast, showDialog, dialogHeader });
  // The grading pages' own markup helpers (grading-ui.js): the rubric check's decisions are drawn by the finalization's component.
  const h = helpers({ api, escape, showDialog, dialogHeader, toast });
  const dialog = () => document.querySelector("#dialog");
  const base = (id) => `/api/quiz-drafts/${encodeURIComponent(id)}`;
  // `answers`: the instructor's unsent answers to the rubric check's decisions, by concern number, kept in memory across redraws
  // (a version tab, a refresh) and dropped once sent or when another check is shown (`checked`: its fingerprint); `split`: the
  // rows they chose to decide apart.
  const fresh = (id) => ({ id, draft: null, version: "A", revision: null, timer: null, comments: {}, feedback: "", answers: new Map(), split: new Set(), checked: "" });
  let page = fresh(null);
  let reading = 0;

  // ---------- the Assignments tab's list ----------
  function cards(drafts) {
    const shown = drafts.filter((d) => d.state !== "approved");
    if (!shown.length) return "";
    return `<section class="quiz-draft-strip" aria-labelledby="quiz-draft-strip-title"><h2 id="quiz-draft-strip-title">Quiz drafts</h2><ul>${shown.map((d) => {
      const revision = d.revisions[d.revisions.length - 1];
      return `<li><a class="quiz-draft-card" href="#documents/quiz/${escape(d.id)}"><span class="quiz-draft-card-title">${escape(d.title)}</span>
        <span class="chip" data-tone="${STATE_TONES[d.state]}">${STATE_NAMES[d.state]}</span>
        <span class="quiz-draft-card-facts">${escape(d.course)} · ${escape(d.term)} · ${plural(d.versions.length, "version")} · ${d.total} points${d.participation ? " · graded for participation" : ""}${revision ? ` · revision ${revision.number}` : ""}</span></a></li>`;
    }).join("")}</ul></section>`;
  }

  async function load() {
    return (await api("/api/quiz-drafts")).drafts;
  }

  // ---------- a new draft ----------
  async function openCreate(definitionId) {
    const intent = captureDialogIntent(dialog());
    const options = await api(`/api/quiz-drafts/options${definitionId ? `?definition_id=${encodeURIComponent(definitionId)}` : ""}`);
    if (!intent()) return;
    if (!options.courses.length) { toast("No course has a current term to draft a quiz for.", true); return; }
    if (!options.model) {
      showDialog(`<div>${dialogHeader("Draft a quiz with a model")}<div class="dialog-body">${calloutMarkup({ tone: "warning", title: "Choose a model first",
        body: `${escape(options.model_problem)} Quizzes are written by the Rubric drafting and review model.` })}</div><footer class="dialog-footer"><a class="button primary" href="#grading?settings=1">Open Compute settings</a></footer></div>`);
      dialog().querySelector('a[href="#grading?settings=1"]').onclick = () => dialog().close();
      return;
    }
    let touched = false;
    const requestId = crypto.randomUUID();
    const courseOptions = options.courses.map((c) => `<option value="${escape(c.term_id)}">${escape(c.code)} · ${escape(c.term)}</option>`).join("");
    showDialog(`<form id="quiz-draft-create" class="quiz-draft-form" novalidate>${dialogHeader("Draft a quiz with a model", "The model writes every version at once in your quiz layout, with rubrics. Nothing is printed or graded until you approve it.")}
      <div class="dialog-body">
        <label class="field">Course<select name="term_id" required>${courseOptions}</select></label>
        <div class="quiz-draft-row"><label class="field quiz-draft-grow">Title<input name="title" required maxlength="120" placeholder="Chapter 4 Quiz" autocomplete="off" autofocus></label>
          <label class="field">Points<input name="total" type="number" min="0.5" max="100" step="0.5" value="10" required></label>
          <label class="field">Versions<input name="versions" type="number" min="1" max="${options.max_versions}" step="1" value="4" required></label>
          <label class="field">Scoring<select name="mode"><option value="negative" selected>Negative</option><option value="positive">Positive</option></select></label></div>
        <label class="checkbox-field"><input type="checkbox" name="participation"> Graded for participation</label>
        <p class="field-hint">Graded as usual, then everyone who turns in a paper gets full credit; the rubric lines stay as their feedback. One version unless you choose more.</p>
        <label class="field">Format<textarea name="format" rows="2" maxlength="1000">${escape(options.default_format)}</textarea><span class="field-hint">The kinds and number of problems. Empty leaves it to your instructions and past quizzes.</span></label>
        <label class="field">Instructions for the model<textarea name="instructions" rows="5" maxlength="8000" placeholder="Sections 4.1–4.3, like the Chapter 3 Quiz: 2 points for multiple choice, 3, 2 and 3 for free response. No credit for an answer without work."></textarea></label>
        <label class="field">Material<input name="files" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.txt,.md,.tex"><span class="field-hint">Notes, textbook sections, homework or earlier quizzes: PDF, pictures, text or LaTeX, up to 12 files and 60 MB.</span></label>
        <fieldset class="quiz-draft-examples"><legend>Lecture notes to read</legend><div data-notes-sections></div></fieldset>
        <fieldset class="quiz-draft-examples"><legend>Assessment blueprint</legend><div data-blueprint-editor></div></fieldset>
        <fieldset class="quiz-draft-examples"><legend>Imitate these past quizzes</legend><div data-examples></div></fieldset>
        ${options.rubric_choice_options?.length ? `<details class="quiz-draft-examples rubric-choices-fold" open><summary>Quick choices for the rubrics</summary><p class="field-hint">The rubrics follow your answers, and the rubric check stops asking about them. “Ask me” leaves a question to the check.</p><div data-rubric-choices-host></div></details>` : ""}
        <p class="quiz-draft-model">Written by ${escape(options.model)}, the Rubric drafting and review model in Compute settings.</p>
        <p class="form-error" role="alert" hidden></p>
      </div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Draft the quiz</button></footer></form>`);
    const form = dialog().querySelector("#quiz-draft-create");
    const active = dialogSession(form, dialog());
    const offLeave = registerLeaveGuard(() => (touched && dialog().open && dialog().querySelector("#quiz-draft-create") ? "Leave Draft a quiz? What you typed is not saved." : null));
    dialog().addEventListener("close", offLeave, { once: true });
    const course = () => options.courses.find((c) => c.term_id === form.elements.term_id.value);
    const blueprint = mountBlueprint(form.querySelector("[data-blueprint-editor]"), {api, escape, course, changed: () => { touched = true; }});
    const drawExamples = () => {
      const c = course();
      form.querySelector("[data-examples]").innerHTML = c.templates.length
        ? c.templates.slice(0, 12).map((t) => `<label class="checkbox-field"><input type="checkbox" name="examples" value="${escape(t.template_id)}"${c.suggested_examples.includes(t.template_id) ? " checked" : ""}> ${escape(t.assignment_title)} · ${escape(t.label)}</label>`).join("")
        : `<p class="field-hint">This course has no blank templates yet; the model follows the layout alone.</p>`;
    };
    // NOTES SECTIONS (2026-10-28): the sections of the course's lecture notes, by chapter; the model is sent the text of those ticked
    // and bank questions filed under them (QUIZZES "NOTES SECTIONS FOR THE WRITER").
    const drawNotes = () => {
      const sections = course().notes_sections || [];
      const chapters = [];
      for (const s of sections) {
        const key = s.chapter || s.number.split(".")[0];
        if (chapters.at(-1)?.key !== key) chapters.push({ key, title: s.chapter_title, sections: [] });
        chapters.at(-1).sections.push(s);
      }
      form.querySelector("[data-notes-sections]").innerHTML = sections.length
        ? `<p class="field-hint">The model reads each section you tick and a few of its question bank questions, which it changes, never copies. The rubrics follow the notes’ methods. Sections your title or instructions name are ticked for you.</p>${chapters.map((c) => `<div class="quiz-draft-notes-pick"><span class="quiz-draft-muted">Chapter ${escape(String(c.key))}${c.title ? ` · ${escape(c.title)}` : ""}</span>
            ${c.sections.map((s) => `<label class="checkbox-field"><input type="checkbox" name="notes_sections" value="${escape(s.number)}"> ${escape(s.number)} ${escape(s.title)}</label>`).join("")}</div>`).join("")}`
        : `<p class="field-hint">This course has no lecture notes in Faculty. Upload them on the course page to choose sections here.</p>`;
      notesTouched = false;
      tickNamed();
    };
    // Until a box is ticked or cleared by hand, the boxes follow the sections the instructions name, else those the title names.
    let notesTouched = false;
    const tickNamed = () => {
      if (notesTouched) return;
      const sections = course().notes_sections || [];
      const named = new Set(namedSections(sections, [form.elements.instructions.value]).length
        ? namedSections(sections, [form.elements.instructions.value]) : namedSections(sections, [form.elements.title.value]));
      for (const box of form.querySelectorAll('input[name="notes_sections"]')) box.checked = named.has(box.value);
    };
    // RUBRIC CHOICES (2026-10-24): each course starts from its latest answers.
    const drawChoices = () => {
      const host = form.querySelector("[data-rubric-choices-host]");
      if (host) host.innerHTML = choicesMarkup(options.rubric_choice_options, course().rubric_choices || {}, escape);
    };
    const preferred = options.courses.find((c) => c.definition_id === definitionId);
    if (preferred) form.elements.term_id.value = preferred.term_id;
    drawExamples();
    drawNotes();
    drawChoices();
    form.elements.term_id.onchange = () => { drawExamples(); drawNotes(); drawChoices(); blueprint.reset(); };
    // GRADED FOR PARTICIPATION (requirement, 2026-11-01):
    // ticking it sets one version until the instructor types their own number.
    let versionsTyped = false;
    form.addEventListener("input", (event) => {
      touched = true;
      if (event.target.name === "versions") versionsTyped = true;
      if (event.target.name === "title" || event.target.name === "instructions") tickNamed();
    });
    form.elements.participation.addEventListener("change", () => {
      if (!versionsTyped) form.elements.versions.value = form.elements.participation.checked ? "1" : "4";
    });
    form.addEventListener("change", (event) => {
      if (event.target.name === "notes_sections") notesTouched = true;
      if (event.target.name !== "examples") return;
      const ticked = [...form.querySelectorAll('input[name="examples"]:checked')];
      if (ticked.length > 2) { event.target.checked = false; fail(form, "Choose at most two past quizzes to imitate."); }
    });
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (form.dataset.saving === "true") return;
      if (!form.elements.title.value.trim()) { fail(form, "Give the quiz a title."); return; }
      let slots;
      try { slots = blueprint.value(); } catch (cause) { fail(form, cause.message); return; }
      const data = new FormData();
      if (slots) data.append("blueprint", JSON.stringify(slots));
      data.append("request_id", requestId);
      for (const name of ["term_id", "title", "total", "versions", "mode", "format", "instructions"]) data.append(name, form.elements[name].value);
      if (form.elements.participation.checked) data.append("participation", "1");
      data.append("examples", [...form.querySelectorAll('input[name="examples"]:checked')].map((input) => input.value).join(","));
      const notes = [...form.querySelectorAll('input[name="notes_sections"]:checked')].map((input) => input.value);
      data.append("notes_sections", notes.join(","));  // always sent: none ticked is none, never the server's reading of the instructions
      if (options.rubric_choice_options?.length) data.append("rubric_choices", JSON.stringify(readChoices(form, options.rubric_choice_options)));
      for (const file of form.elements.files.files) data.append("files", file, file.name);
      await busy(form, async () => {
        const draft = await api("/api/quiz-drafts", "POST", data, {}, 300000);
        touched = false;
        if (!active()) return;
        dialog().close();
        toast("The model is drafting the quiz. This page shows it when it is ready.");
        location.hash = `#documents/quiz/${draft.id}`;
      });
    };
  }

  function fail(form, message) {
    const error = form.querySelector(".form-error");
    error.textContent = message;
    error.hidden = !message;
  }

  async function busy(form, operation) {
    if (form.dataset.saving === "true") return;
    const buttons = [...form.querySelectorAll("button")].filter((button) => !button.disabled);
    fail(form, "");
    form.dataset.saving = "true";
    buttons.forEach((button) => { button.disabled = true; });
    try { await operation(); } catch (cause) { if (form.isConnected) fail(form, cause.message); else toast(cause.message, true); }
    finally { form.dataset.saving = "false"; buttons.forEach((button) => { button.disabled = false; }); }
  }

  // ---------- a draft's page ----------
  function stop() { clearTimeout(page.timer); page.timer = null; }

  async function renderPage(root, id, force = false) {
    if (location.hash.split("?")[0] !== `#documents/quiz/${id}`) return;
    stop();
    if (page.id !== id) page = fresh(id);
    const run = ++reading;
    const draft = await api(base(id));
    if (run !== reading || location.hash.split("?")[0] !== `#documents/quiz/${id}`) return;
    const changed = force || !page.draft || JSON.stringify(stateKey(page.draft)) !== JSON.stringify(stateKey(draft));
    const justApproved = page.draft?.state === "approving" && draft.approved?.status === "complete";
    page.draft = draft;
    if (justApproved) void sendToAim(draft);
    if (!draft.versions.includes(page.version)) page.version = draft.versions[0];
    setHeader(draft);
    if (changed) draw(root, draft);
    else drawStatus(root, draft);
    // A hidden tab reads once it is shown again, and only while this draft is still the page shown.
    if (live(draft)) poll(root, id, 4000);
  }

  /** The quiz was approved while its page was open: when the term's DR quiz takers have it on their DR quizzes, Send to AIM opens
   *  at once (the instructor is here to read Duo's code): its date and time, the students' emails, the bookings import. */
  async function sendToAim(draft) {
    const approval = draft.approved;
    if (!approval?.term_id) return;
    const route = `/api/scheduling/terms/${encodeURIComponent(approval.term_id)}/aim-send`;
    let plan;
    try { plan = await api(route); } catch { return; }  // no DR quizzes read: Home's Send to AIM card stays the way
    if (!aimMatches(plan, approval.result?.assignment_id, draft.title).length) return;
    toast(`${draft.title} has DR quiz takers: sending it to AIM. Approve the Duo push.`);
    void openAimSend({ api, showDialog, dialogHeader, escape,
      start: { route, body: { request_id: crypto.randomUUID(), fingerprint: plan.fingerprint } },
      onDone: (status) => {
        document.dispatchEvent(new CustomEvent("accommodations-imported"));
        const stopped = (status.sent || []).some((line) => line.did === "stopped" || line.did.endsWith("_failed") || line.did.endsWith("_uncertain"));
        toast(stopped ? "Sent to AIM, with something to check on Accommodations › DR quizzes." : `${draft.title} is in AIM; its DR quiz takers are emailed to register.`, stopped);
      },
      onExported: () => toast("Sent to AIM. Open Accommodations to import the DR bookings.", true) });
  }

  const live = (draft) => draft.state === "drafting" || draft.state === "approving" || draft.state === "rebuilding";
  function poll(root, id, delay) {
    clearTimeout(page.timer);
    page.timer = setTimeout(() => whenVisible(() => {
      if (location.hash.split("?")[0] === `#documents/quiz/${id}`) renderPage(root, id).catch((e) => { if (!readFailed(root, id, e)) toast(e.message, true); });
    }), delay);
  }

  // A read of the draft shown failed: its page stays, with one notice above its status, and a live draft is read again in 15 s
  // (the next good read rewrites the status). False when this draft's page is not drawn (the caller shows a way back).
  function readFailed(root, id, error) {
    const host = root.querySelector("[data-quiz-status]");
    if (page.id !== id || !page.draft || !host) return false;
    host.innerHTML = calloutMarkup({ tone: "warning", className: "quiz-draft-stale", title: "Couldn't refresh. Showing the last loaded results.", body: error?.message || "" })
      + statusMarkup(page.draft);
    bindStatus(root, page.draft);
    if (live(page.draft)) poll(root, id, 15000);
    return true;
  }

  const stateKey = (draft) => [draft.state, draft.latest_complete, draft.live, draft.revisions.length, draft.approved?.status];

  function setHeader(draft) {
    const ready = draft.state === "ready" && draft.latest_complete;
    // An approved draft follows its permanent assignment and term references.
    const approval = draft.approved?.status === "complete" ? draft.approved : null;
    const workspace = approval?.result?.assignment_id && approval.term_id
      ? `#assignments/${encodeURIComponent(approval.result.assignment_id)}/${encodeURIComponent(approval.term_id)}` : null;
    header?.set({ route: location.hash, title: draft.title, context: `${draft.course} · ${draft.term} · Quiz draft`,
      subtitle: draft.state === "approved" ? "Approved: its print jobs and grading are set up." : draft.scrapped ? "Scrapped: it is no longer listed, revised or approved."
        : "Every version as it will print, with its rubrics. Approve it, or ask the model for changes.",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Assignments", href: "#assignments" }, { label: draft.title }],
      action: ready ? { label: "Approve and print…", icon: "print", run: () => openApproval(draft).catch((e) => toast(e.message, true)) }
        : workspace ? { label: "Open assignment", icon: "arrow-right", run: () => { location.hash = workspace; } } : null,
      secondary: ready ? [{ label: "Ask for changes", run: askForChanges }] : [] });
  }

  // The changes form sits below every page and problem: the header's Ask for changes brings it into view, ready to type.
  function askForChanges() {
    const form = document.querySelector("#quiz-draft-root [data-quiz-changes]");
    if (!form) return;
    form.scrollIntoView({ block: "start", behavior: scrollBehavior() });
    form.elements.feedback?.focus({ preventScroll: true });
  }

  function latest(draft) {
    return draft.revisions.find((r) => r.id === draft.latest_complete) || null;
  }

  function progressText(revision) {
    const p = revision.progress || {};
    // A revision that only sends the answers to the rubric check's decisions writes nothing (2026-10-11).
    if (p.answers) return "The rubric reviewer is applying your answers to its decisions…";
    if (!p.written) return "Writing every version of the quiz from your material…";
    if (!p.checked) return p.fixes ? "Fixing what did not compile or fit on its page…" : "Compiling the versions and checking the printed pages…";
    if (p.fixing) return "Fixing what the check of the printed pages found…";
    if (p.rubric_check) return "Checking the rubrics, as Key & Rubric Finalization would…";  // the last step (2026-10-11)
    if (p.keys) return "Reading each problem’s answer key from its solution…";  // after the rubrics (2026-10-11)
    if (p.problems) return `Drafting rubrics: ${Math.min(p.rubrics, p.problems)} of ${p.problems} problems…`;
    return "Drafting the rubrics…";
  }

  function statusMarkup(draft) {
    const live = draft.revisions.find((r) => r.id === draft.live);
    const last = draft.revisions[draft.revisions.length - 1];
    if (draft.scrapped) return calloutMarkup({ tone: "neutral", title: "This draft was scrapped.",
      body: `On ${new Date(draft.scrapped.created_at).toLocaleString()}. It is no longer listed, revised or approved.`, action: { label: "Back to Assignments", href: "#assignments" } });
    if (draft.approved) return approvalMarkup(draft, draft.approved);
    if (live) return `<div class="callout quiz-draft-status" data-tone="info" role="status"><div><strong>Revision ${live.number}</strong>: ${escape(progressText(live))}
      <span class="quiz-draft-muted">${live.rubric_answers ? "The quiz itself does not change; this page updates by itself." : "A quiz takes about ten minutes; this page updates by itself."}</span></div><button type="button" class="button" data-quiz-stop="${escape(live.id)}">Stop</button></div>`;
    if (last && last.status !== "complete") {
      return `<div class="callout quiz-draft-status" data-tone="danger" role="alert"><div><strong>Revision ${last.number} ${last.status === "cancelled" ? "was stopped" : "could not be finished"}.</strong> ${escape(last.error || "")}</div>
        <button type="button" class="button" data-quiz-retry="${escape(last.id)}">Try again</button></div>`;
    }
    return "";
  }

  function approvalMarkup(draft, approval) {
    const p = approval.progress || {};
    const steps = [["versions", "Assignment and printed versions"], ["plan", "Grading plan"], ["questions", "Questions and rubrics"],
      ["areas", "Templates and answer areas confirmed"], ["review", "Rubrics confirmed for review"], ["print", "Print jobs queued"]];
    const list = `<ol class="quiz-draft-steps">${steps.map(([key, label]) => `<li data-done="${p[key] ? "true" : "false"}">${escape(label)}${key === "print" && p.print ? ` · ${plural(p.print.batches, "job")}` : ""}${key === "questions" && p.questions ? ` · ${p.questions.count}${p.questions.keys ? ` · ${plural(p.questions.keys, "answer key")} saved` : ""}` : ""}</li>`).join("")}</ol>`;
    if (approval.status === "failed") {
      return `<div class="callout quiz-draft-status" data-tone="danger" role="alert"><div><strong>Carrying out the approval stopped.</strong> ${escape(approval.error)} ${list}</div>
        <button type="button" class="button" data-quiz-reapprove>Approve again</button></div>`;
    }
    if (approval.status !== "complete") return `<div class="callout quiz-draft-status" data-tone="info" role="status"><div><strong>Approved.</strong> Faculty is setting it up: ${list}</div></div>`;
    const result = approval.result;
    // A rebuild (QUIZZES "REBUILDING AN APPROVED QUIZ") prints the approved quiz again in the current page layout; the latest one says how it went.
    const rebuild = (approval.rebuilds || []).at(-1);
    const rebuilt = !rebuild ? ""
      : rebuild.status === "failed" ? `<p class="quiz-draft-muted" role="alert" data-quiz-rebuild="failed"><strong>Rebuilding it in the current page layout stopped.</strong> ${escape(rebuild.error)}</p>`
      : rebuild.status !== "complete" ? `<p class="quiz-draft-muted" role="status" data-quiz-rebuild="working">Rebuilding it in the current page layout: new printed versions, grading setup and print jobs…</p>`
      : `<p class="quiz-draft-muted" data-quiz-rebuild="complete">Rebuilt ${escape(new Date(rebuild.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }))} in the current page layout (two corner codes, no version printed); the earlier print jobs are superseded.</p>`;
    // Before 2026-10-01 an approval printed one job per section and version; since then one per version for the whole term.
    const sections = [...new Map(result.batches.filter((b) => b.section_id).map((b) => [b.section_id, b])).values()];
    const covered = (approval.input?.sections || []).map((s) => s.number);
    // Said as it stands whatever the review has done since: the approval only asked for it.
    // Since 2026-10-11 the rubric check made while the quiz was drafted may have been carried to the plan, and the finalization
    // recorded with the approval (its ticked box); a rebuild's new questions always wait for the confirmation again.
    const carried = p.review?.carried || {}, rebuiltSince = (approval.rebuilds || []).some((r) => r.status === "complete");
    const unrecorded = carried.not_confirmed ? ` The rubric finalization you ticked was not recorded: ${escape(carried.not_confirmed)}` : "";
    const review = result.finalization && !rebuiltSince ? "Its rubric finalization was recorded with the approval: nothing waits in Key &amp; Rubric Finalization."
      : result.rubric_check ? `The rubric check made while it was drafted is in Key &amp; Rubric Finalization, waiting for your confirmation.${unrecorded}`
      : result.rubric_review === "requested" ? `Its rubric review was requested on approval; see and confirm it in Key &amp; Rubric Finalization.${unrecorded}`
      : `Request the rubric review in Key &amp; Rubric Finalization (${escape(result.rubric_review)}).`;
    // The answer keys the approval saved with its questions (2026-10-11): the keys this page showed beside the problems.
    const saved = p.questions?.keys ? ` ${p.questions.keys === 1 ? "Its answer key was" : `Its ${p.questions.keys} answer keys were`} saved with the questions.` : "";
    // This assignment's print jobs on the Print jobs tab (a job's window offers the interleaved stack first).
    const jobs = result.assignment_id ? `#documents?jobs=${encodeURIComponent(result.assignment_id)}`
      : result.batches?.length ? `#documents?batch=${encodeURIComponent(result.batches[0].batch_id)}` : "#documents";
    const printed = sections.length ? `for ${plural(sections.length, "section")}` : `printed as one stack${covered.length ? ` for ${covered.length === 1 ? "Section" : "Sections"} ${escape(covered.join(", "))}` : ""}`;
    // The approval's 'Given on' date (2026-10-02): the exam record's main sitting, or the earlier date it kept.
    const given = givenOnMarkup(approval, escape);
    return `<div class="callout quiz-draft-status" data-tone="success"><div><strong>Approved and sent to printing.</strong> ${plural(result.batches.length, "print job")} ${printed}; only the stamps say which version a copy is.${given} ${review}${saved}${rebuilt}
      <ul class="quiz-draft-links"><li><a href="#grading/${escape(result.plan_id)}?tab=answer-key">Open Key &amp; Rubric Finalization</a></li><li><a href="${escape(jobs)}">Open print jobs</a></li>
      ${result.assignment_id ? `<li><button type="button" class="text-button" data-quiz-canvas data-term="${escape(approval.term_id)}" data-assignment="${escape(result.assignment_id)}">Put it on Canvas…</button></li>` : ""}
      ${sections.length ? sections.map((b) => `<li><a ${viewLink(`${base(draft.id)}/approvals/${approval.id}/stack/${b.section_id}`, `Section ${b.section_number} print stack`, escape)}>Section ${escape(b.section_number)}: every version interleaved (PDF)</a></li>`).join("")
        : `<li><a ${viewLink(`${base(draft.id)}/approvals/${approval.id}/stack`, "Print stack", escape)}>Every version interleaved (PDF)</a></li>`}</ul>
      <span class="quiz-draft-muted">The interleaved file is ready once every version's print job is; hand the stack out from the top and neighbouring students get different versions.</span></div></div>`;
  }

  function drawStatus(root, draft) {
    const host = root.querySelector("[data-quiz-status]");
    if (host) { host.innerHTML = statusMarkup(draft); bindStatus(root, draft); }
  }

  function draw(root, draft) {
    const revision = latest(draft);
    const result = revision?.result;
    root.innerHTML = `<div class="quiz-draft-page"><div class="quiz-draft-top"><div data-quiz-status>${statusMarkup(draft)}</div>${menuMarkup(draft)}</div>
      ${draft.scrapped ? "" : result ? quizMarkup(draft, revision, result) : draft.live ? "" : emptyMarkup({ title: "No finished revision yet", body: "Try again, or draft the quiz anew from Assignments." })}
      ${blueprintMarkup(draft.blueprint, result?.blueprint_coverage, escape)}${historyMarkup(draft)}</div>`;
    bindStatus(root, draft);
    bindActionMenu(root);
    root.querySelector("[data-quiz-scrap]")?.addEventListener("click", () => openScrap(draft));
    if (result) bindQuiz(root, draft, revision, result);
    renderMath(root);
  }

  // Scrapping is for a draft never approved: an approved quiz is an assignment with print jobs (the server refuses it too).
  function menuMarkup(draft) {
    if (draft.scrapped || draft.approved) return "";
    return actionMenuMarkup({ id: "quiz-draft-actions", label: "Draft actions", items: [{ label: "Scrap this draft…", danger: true, data: { quizScrap: "" } }] });
  }

  function openScrap(draft) {
    const live = draft.revisions.find((r) => r.id === draft.live);
    const made = draft.approvals.some((a) => a.status === "failed" && a.progress?.versions);
    showDialog(`<form id="quiz-draft-scrap" class="quiz-draft-form" novalidate>${dialogHeader("Scrap this draft?", escape(`${draft.title} · ${draft.course} · ${draft.term}`))}
      <div class="dialog-body"><p>It leaves Assignments and can no longer be revised or approved. This can't be undone.</p>
        ${live ? `<p>The model's revision ${live.number}, still in progress, is stopped.</p>` : ""}
        ${made ? "<p>The assignment its stopped approval already made stays in Assignments.</p>" : ""}
        <p class="quiz-draft-muted">Faculty keeps its material and revisions in the course's records, as it keeps every draft.</p>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Keep the draft</button><button type="submit" class="button danger">Scrap draft</button></footer></form>`);
    const form = dialog().querySelector("#quiz-draft-scrap");
    const active = dialogSession(form, dialog());
    form.onsubmit = async (event) => {
      event.preventDefault();
      await busy(form, async () => {
        await api(`${base(draft.id)}/scrap`, "POST", { confirmation: "scrap" });
        if (!active()) return;
        dialog().close();
        toast(`Scrapped ${draft.title}.`);
        onChanged?.();
        location.hash = "#assignments";  // ASSIGNMENT-WORKSPACE: the list that held the draft
      });
    };
  }

  function historyMarkup(draft) {
    if (draft.revisions.length < 2) return "";
    return `<details class="quiz-draft-history"><summary>Revisions (${draft.revisions.length})</summary><ol>${draft.revisions.map((r) => `<li><strong>Revision ${r.number}</strong> · ${escape(r.status)} · ${escape(new Date(r.created_at).toLocaleString())}${r.rubric_answers ? " · your answers to the rubric check’s decisions; the quiz is unchanged" : ""}${r.feedback ? `<blockquote>${escape(r.feedback)}</blockquote>` : ""}${r.error ? `<p class="quiz-draft-muted">${escape(r.error)}</p>` : ""}</li>`).join("")}</ol></details>`;
  }

  // READABLE REVIEW (requirement, 2026-10-05): what needs a look is counted and marked under its problem, what the model said shows as first sentences beside the
  // problem as printed (the version chosen), and the full notes, rubric lines and solution fold (review-notes.js).
  function quizMarkup(draft, revision, result) {
    const quiz = result.quiz;
    const letter = page.version;
    const version = result.versions[letter];
    const src = (n) => `${base(draft.id)}/revisions/${encodeURIComponent(revision.id)}/versions/${letter}/pages/${n}.png`;
    // A page opens larger in the file viewer at that page (requirement, 2026-10-01): the browser's own PDF viewer where it has one, zoomable; else the page as a picture.
    const pdf = `${base(draft.id)}/revisions/${encodeURIComponent(revision.id)}/versions/${letter}/pdf`;
    const tabs = quiz.versions.length > 1 ? `<div class="quiz-draft-tabs" role="tablist" aria-label="Versions">${quiz.versions.map((v) => `<button type="button" role="tab" aria-selected="${v === letter}" data-quiz-version="${v}">Version ${v}</button>`).join("")}</div>` : "";
    const facts = `${plural(quiz.problems.length, "problem")} · ${quiz.total} points · ${plural(quiz.versions.length, "version")} · ${plural(version.page_count, "page")} · ${result.mode === "negative" ? "negative scoring" : "positive scoring"}${draft.participation ? " · graded for participation" : ""}`;
    const notes = reviewNotes(result);
    const approved = Boolean(draft.approved);
    // Once approved the review is done: the count and the marks stay, the model's notes on the whole quiz fold.
    const whole = approved ? (notes.whole.notes.length ? disclosureMarkup({ label: "The model's notes", body: proseMarkup(notes.whole.notes.join("\n\n"), h), className: "quiz-draft-notes review-fold" }) : "")
      : wholeMarkup(notes.whole.notes, h);
    // The rubric check's one line (2026-10-11): how many decisions are open, or what is proposed with none open. Once approved the
    // check is the grading plan's, in Key & Rubric Finalization.
    const counts = approved ? null : revision.summary?.rubric_check, rubricLead = rubricCheckLead(counts);
    const rubricOpen = rubricLead ? counts.decisions + counts.overflow : 0;
    return `<section class="quiz-draft-revision" aria-labelledby="quiz-draft-heading">
      <div class="quiz-draft-summary"><div><div class="quiz-draft-heading"><h2 id="quiz-draft-heading">Revision ${revision.number}</h2><p class="quiz-draft-muted">${escape(facts)}</p></div>
        ${countMarkup(notes, quiz.problems.map((p) => p.number), escape)}${checksMarkup(notes.whole.checks, h)}
        ${rubricLead ? `<p class="review-count" data-rubric-lead>${rubricOpen ? `<span class="chip" data-tone="warning">${rubricOpen} to decide</span>` : ""}${escape(rubricLead)}</p>` : ""}
        ${result.changes ? `<div class="review-changed"><h5>What changed in revision ${revision.number}</h5>${proseMarkup(result.changes, h)}</div>` : ""}${whole}</div>
        <div class="quiz-draft-side"><div class="quiz-draft-pages">${version.pages.map((p) => `<figure><a class="quiz-draft-page-open" ${viewLink(pdf, `Version ${letter}`, escape, p.number)} aria-label="Open Version ${letter}, page ${p.number} larger"><img src="${src(p.number)}" alt="Version ${letter}, page ${p.number}" loading="lazy" width="${p.width_px}" height="${p.height_px}"></a><figcaption>Page ${p.number}</figcaption></figure>`).join("")}</div>
          <div class="quiz-draft-files"><a ${viewLink(pdf, `Version ${letter}`, escape)}>Open Version ${letter} (PDF)</a>
            <a href="${base(draft.id)}/revisions/${encodeURIComponent(revision.id)}/versions/${letter}/tex?download=1">LaTeX source</a></div></div></div>
      ${tabs}
      <div class="quiz-draft-problems review-problems">${quiz.problems.map((problem) => problemMarkup(result, problem, letter, approved, notes, { version, pdf, src })).join("")}</div>
      ${approved ? "" : rubricCheckMarkup(result)}${approved ? "" : changesMarkup(result)}</section>`;
  }

  // RUBRIC QUESTIONS AT DRAFT REVIEW (requirement, 2026-10-11). The rubric check Key & Rubric Finalization runs is a revision's last
  // step now, so its decisions are answered here, in one sitting with the quiz: the finalization's own decisions view (read only),
  // one button that sends every answer to the reviewer (a revision that changes nothing of the quiz), and what earlier answers
  // settled. The guidance it proposes for the graders shows under each rubric line of its problem (`guidanceMarkup`).
  function rubricCheckMarkup(result) {
    const check = result.rubric_check;
    if (check?.status !== "complete") return "";
    const found = check.result, concerns = (found.suspected_errors?.length || 0) + (found.consistency_issues?.length || 0);
    const titles = new Map((check.view || []).map((q) => [q.question_id, `${q.exam_version ? `Version ${q.exam_version} · ` : ""}${q.title}`]));
    // A question whose lines still add up to more than it is worth keeps the finalization open; its alternatives are ticked there.
    const over = (found.unresolved || []).map((row) => `${titles.get(row.question_id) || "A question"}: its lines can still be selected together for more than it is worth. Tick its alternatives in Key & Rubric Finalization after approving, or ask for its rubric to change.`);
    const resolved = found.resolution?.resolved || [];
    const answered = resolved.map((row) => `<li>${mathText(row.title || row.message, h)}${row.comment ? `<small class="quiz-draft-clarification">Your answer: ${escape(row.comment)}</small>` : ""}
      <small class="quiz-draft-clarification">${row.without_comment ? "Settled by the reviewer: " : ""}${mathText(row.how || "", h)}</small></li>`).join("");
    return `<section class="quiz-draft-changes" data-rubric-check aria-labelledby="quiz-rubric-check-title"><h3 id="quiz-rubric-check-title">Rubric check</h3>
      <p class="quiz-draft-muted">The check Key &amp; Rubric Finalization runs, made here${check.model ? ` by ${escape(check.model)}` : ""}. What it proposes guides the AI graders only: the rubric’s wording and points stay as drafted.</p>
      ${checksMarkup(over, h)}
      ${concerns && check.view ? `<form data-rubric-answers novalidate><div data-quality-concerns></div>
        <section class="quality-respond" data-quality-respond><div class="grading-actions"><button type="submit" class="button">Send my answers to the reviewer</button></div>
          <p class="supporting-copy" data-resolve-scope></p></section><p class="form-error" role="alert" hidden></p></form>`
        : concerns ? `<p>${plural(concerns, "decision")} open: answer ${concerns === 1 ? "it" : "them"} in Key &amp; Rubric Finalization after approving.</p>`
        : `<p class="quiz-draft-muted" data-rubric-clear>Nothing needs a decision.</p>`}
      ${resolved.length ? disclosureMarkup({ label: "Answered decisions", hint: String(resolved.length), body: `<ul>${answered}</ul>`, className: "review-fold" }) : ""}</section>`;
  }

  /** The guidance the rubric check holds for one rubric line of the version shown (2026-10-11): its clarification, and the lines it
   * may never be selected with (by their wording: the page numbers no lines). Empty without a finished check; the drafted
   * clarification shows then, as before. */
  function guidanceMarkup(check, number, letter, id, line) {
    const qid = `draft:${number}:${letter}`;
    const entry = check?.status === "complete" ? check.result.questions.find((q) => q.question_id === qid) : null;
    if (!entry) return line.clarifications?.[letter] ? `<small class="quiz-draft-clarification">${mathText(line.clarifications[letter], h)}</small>` : "";
    const question = (check.view || []).find((q) => q.question_id === qid), rule = (entry.builtin || []).find((row) => row.item_id === id);
    const wording = (other) => question?.rubric.items.find((item) => item.id === other)?.description || other;
    const apart = (entry.pairs || []).filter((pair) => pair.includes(id)).map((pair) => pair.find((other) => other !== id));
    const note = entry.clarifications?.[id];
    return `${note ? `<small class="quiz-draft-clarification" data-guidance="clarification"><strong>For the graders:</strong> ${mathText(note, h)}</small>` : ""}${
      rule ? `<small class="quiz-draft-clarification" data-guidance="rule">${rule.rule === "full_credit" ? "Full-credit" : "No-credit"} line: never selected with another line.</small>`
        : apart.length ? `<small class="quiz-draft-clarification" data-guidance="exclusions">Never selected with: ${apart.map((other) => mathText(wording(other), h)).join("; ")}</small>` : ""}`;
  }

  function problemMarkup(result, problem, letter, approved, notes, { version, pdf, src }) {
    const content = problem.content[letter];
    const drafted = result.rubrics.find((r) => r.problem === problem.number) || {};
    const rubric = drafted.rubric;
    const member = rubric?.versions.find((v) => v.version === letter);
    const about = notes.problems[problem.number] || { checks: [], writer: "", rubric: "" };
    const kind = problem.kind === "multiple_choice" ? "Multiple choice" : "Free response";
    const crop = problemCrop(version.layout?.problems, problem.number);
    const picture = cropMarkup({ crop, page: crop && version.pages.find((p) => p.number === crop.page), pdf, name: `Version ${letter}`, src: crop ? src(crop.page) : "", number: problem.number, escape });
    const said = [rubric?.versions.length > 1 ? `Version ${letter}'s wording; the points are the same on every version.` : "", drafted.carried_from ? "Kept from the revision before." : ""].filter(Boolean).join(" ");
    // Under each line, the guidance the rubric check holds for it on the version shown (2026-10-11): what the approval's box
    // confirms "as shown"; without a finished check (or once approved) the drafted clarification, as before.
    const lines = rubric ? `${said ? `<p class="quiz-draft-muted">${escape(said)}</p>` : ""}<ol class="review-lines quiz-draft-rubric" data-ids="false">${rubric.items.map((line, index) => `<li><span class="review-line-points">${line.points}</span>
      <div>${mathText(line.descriptions[letter] || "", h)}${guidanceMarkup(approved ? null : result.rubric_check, problem.number, letter, `R${index + 1}`, line)}</div></li>`).join("")}</ol>` : "";
    const answers = (content.final_answers || []).map((a) => `<li>${a.part ? `(${escape(a.part)}) ` : ""}${mathText(a.answer, h)}</li>`).join("");
    const choice = problem.kind === "multiple_choice" ? `<p><strong>Correct choice:</strong> (${escape(content.correct_choice)})</p>` : "";
    // The answer key read for the version shown while the quiz was drafted (2026-10-11, "Carry the key through"): what approving
    // saves with this problem's question, beside the writer's own answer; how each part was found folds with the solution.
    const reading = drafted.keys?.[letter];
    const solution = `<h5>Solution and answers, Version ${letter}</h5>${choice}${answers ? `<ul>${answers}</ul>` : ""}<div class="quiz-draft-solution-text">${mathText(content.solution || member?.solution || "", h)}</div>${keyBasisMarkup(reading, h)}`;
    // The answer of the version shown, beside the problem as printed (2026-10-06: it waited inside "Rubric and solution"), so the key
    // is checked without opening anything; the worked solution and the rubric lines still fold.
    const key = answerMarkup(problem, content, h);
    const comment = page.comments[problem.number] || "";
    return `<article class="quiz-draft-problem review-problem${picture ? "" : " review-problem-plain"}" data-problem="${problem.number}">${picture}<div class="review-problem-notes">
      <h4>Problem ${problem.number} <span>${kind} · ${problem.points} ${problem.points === 1 ? "point" : "points"}${problem.tag ? ` · ${escape(problem.tag)}` : ""} · page ${problem.page}</span></h4>
      ${checksMarkup(about.checks, h)}
      ${saidMarkup([["Quiz writer", about.writer], ["Rubric writer", about.rubric]], h, [["Answer", key], ["Answer key", keyReadMarkup(reading, h)]])}
      ${rubric ? disclosureMarkup({ label: "Rubric and solution", hint: plural(rubric.items.length, "line"), body: lines + solution, className: "review-fold" })
        : calloutMarkup({ tone: "warning", title: "No rubric yet", body: escape(drafted.error || "Ask for changes to draft it again.") })}
      ${approved ? "" : `<div class="quiz-draft-comment"><button type="button" class="text-button" data-comment-open="${problem.number}"${comment ? " hidden" : ""}>Suggest a change to problem ${problem.number}</button>
        <label class="field"${comment ? "" : " hidden"}>Changes to problem ${problem.number}<textarea rows="2" maxlength="2000" data-problem-comment="${problem.number}" placeholder="Its wording, numbers, figure, space or grading">${escape(comment)}</textarea></label></div>`}</div></article>`;
  }

  function changesMarkup(result) {
    return `<form class="quiz-draft-changes" data-quiz-changes novalidate><h3>Ask for changes</h3>
      <p class="quiz-draft-muted">The model makes a new revision of every version with your changes and keeps the rest as it is.</p>
      <p class="quiz-draft-comment-scope" data-comment-scope hidden></p>
      <label class="field">What should change?<textarea name="feedback" rows="4" maxlength="6000" placeholder="Make problem 3 harder; use different contexts in Versions C and D; give the table in problem 4 five rows.">${escape(page.feedback)}</textarea></label>
      <label class="field">Scoring<select name="mode"><option value="negative"${result.mode === "negative" ? " selected" : ""}>Negative</option><option value="positive"${result.mode === "positive" ? " selected" : ""}>Positive</option></select></label>
      <p class="form-error" role="alert" hidden></p><div class="quiz-draft-actions"><button type="submit" class="button">Ask the model for these changes</button></div></form>`;
  }

  function feedbackText(form) {
    const general = form.elements.feedback.value.trim();
    const problems = Object.entries(page.comments).filter(([, text]) => text.trim()).map(([number, text]) => `Problem ${number}: ${text.trim()}`);
    return [general, ...problems].filter(Boolean).join("\n");
  }

  function bindStatus(root, draft) {
    root.querySelectorAll("[data-quiz-stop]").forEach((button) => {
      button.onclick = async () => {
        setBusy(button, true);
        try { await api(`${base(draft.id)}/revisions/${encodeURIComponent(button.dataset.quizStop)}/cancel`, "POST", {}); await renderPage(root, draft.id, true); }
        catch (cause) { toast(cause.message, true); setBusy(button, false); }
      };
    });
    root.querySelectorAll("[data-quiz-retry]").forEach((button) => {
      button.onclick = async () => {
        const failed = draft.revisions.find((r) => r.id === button.dataset.quizRetry);
        setBusy(button, true);
        try {
          // Answers to the rubric check's decisions are sent again as they were, about the check still shown (2026-10-11).
          if (failed.rubric_answers) await api(`${base(draft.id)}/rubric-answers`, "POST", { request_id: crypto.randomUUID(), based_on: failed.based_on,
            check_sha256: latest(draft)?.result?.rubric_check?.sha256, responses: failed.rubric_answers });
          else await api(`${base(draft.id)}/revisions`, "POST", { request_id: crypto.randomUUID(), based_on: failed.based_on || null, feedback: failed.feedback || "", mode: failed.mode });
          await renderPage(root, draft.id, true);
        } catch (cause) { toast(cause.message, true); setBusy(button, false); }
      };
    });
    root.querySelectorAll("[data-quiz-canvas]").forEach((button) => {
      button.onclick = () => { void placement.open({ termId: button.dataset.term, assignmentId: button.dataset.assignment, points: String(draft.total ?? ""), title: draft.title }); };
    });
    root.querySelectorAll("[data-quiz-reapprove]").forEach((button) => {
      button.onclick = () => openApproval(draft).catch((e) => toast(e.message, true));
    });
  }

  function bindQuiz(root, draft, revision, result) {
    root.querySelectorAll("[data-quiz-version]").forEach((button) => {
      button.onclick = () => { collect(root); page.version = button.dataset.quizVersion; draw(root, page.draft); };
    });
    // A problem's change box opens on request; the form below says which problems' changes it sends with it.
    const scope = () => {
      const line = root.querySelector("[data-comment-scope]");
      if (!line) return;
      const numbers = Object.entries(page.comments).filter(([, text]) => text.trim()).map(([number]) => number).sort((a, b) => a - b);
      line.hidden = !numbers.length;
      line.textContent = numbers.length ? `Also sends your changes to ${numbers.length === 1 ? "problem" : "problems"} ${numbers.length > 1 ? `${numbers.slice(0, -1).join(", ")} and ${numbers.at(-1)}` : numbers[0]}.` : "";
    };
    root.querySelectorAll("[data-comment-open]").forEach((button) => {
      button.onclick = () => {
        const label = button.parentElement.querySelector("label");
        button.hidden = true; label.hidden = false;
        label.querySelector("textarea").focus();
      };
    });
    root.querySelectorAll("[data-problem-comment]").forEach((area) => {
      area.oninput = () => { page.comments[area.dataset.problemComment] = area.value; scope(); };
    });
    scope();
    bindRubricCheck(root, draft, revision, result);
    const form = root.querySelector("[data-quiz-changes]");
    if (!form) return;
    const owner = page;
    form.elements.feedback.oninput = () => { page.feedback = form.elements.feedback.value; };
    form.onsubmit = async (event) => {
      event.preventDefault();
      const feedback = feedbackText(form);
      if (!feedback && form.elements.mode.value === result.mode) { fail(form, "Say what should change."); return; }
      await busy(form, async () => {
        await api(`${base(draft.id)}/revisions`, "POST", { request_id: crypto.randomUUID(), based_on: revision.id, feedback, mode: form.elements.mode.value });
        if (page !== owner) return;
        page.comments = {};
        page.feedback = "";
        toast("The model is making the changes. This page shows the new revision when it is ready.");
        await renderPage(root, draft.id, true);
      });
    };
  }

  function collect(root) {
    root.querySelectorAll("[data-problem-comment]").forEach((area) => { page.comments[area.dataset.problemComment] = area.value; });
    const feedback = root.querySelector("[data-quiz-changes] textarea[name=feedback]");
    if (feedback) page.feedback = feedback.value;
    // The unsent answers to the rubric check's decisions, by concern number (each decision's rows hold the same text).
    root.querySelectorAll("[data-rubric-answers] [data-concern-comment]").forEach((area) => {
      const concern = Number(area.dataset.concernComment);
      if (area.value.trim()) page.answers.set(concern, area.value); else page.answers.delete(concern);
    });
  }

  // The rubric check's decisions, in the finalization's own decisions view (grading-rubric-concerns.js), read only: an answer drafts
  // feedback, nothing is edited or approved here. One request sends every answer: a revision that runs only the reviewer's Resolve.
  function bindRubricCheck(root, draft, revision, result) {
    const form = root.querySelector("[data-rubric-answers]"), check = result.rubric_check;
    if (!form) return;
    if (page.checked !== check.sha256) { page.answers = new Map(); page.split = new Set(); page.unfixed = new Set(); page.checked = check.sha256; }
    // BOOKKEEPING IS FIXED, NOT ASKED (2026-10-24): a finding the reviewer settled with its one right answer is listed apart; "Ask me
    // instead" makes it a decision here, and the answers say so (`unfixed`). A key fix is listed apart too (2026-10-29: Quiz 6's
    // only open question was one): the draft's keys are the draft's until approval, when Faculty makes it on the plan.
    const handled = new Set((check.result.suspected_errors || []).flatMap((issue, n) => ["guidance", "key"].includes(issue.fix?.kind) && !page.unfixed.has(n) ? [n] : []));
    const entries = new Map(check.result.questions.map((q) => [q.question_id, q]));
    // The list of decisions is the one way to move between them here: the component's menu is for its dialog's narrow layout.
    const oneList = () => form.querySelector(".quality-decision-rail > .field")?.setAttribute("hidden", "");
    mountRubricConcerns(form.querySelector("[data-quality-concerns]"), check.result, check.view, h, {
      retry: page.answers, split: page.split, readOnly: true, links: { sets: check.result.line_links || [] }, onRedraw: oneList, handled, pendingKeys: true,
      // AN EDIT ONLY THE INSTRUCTOR CAN MAKE (2026-10-29): here a rubric's points are the draft's, so approving the change asks
      // for a revision that makes exactly it.
      applyEdit: async (_, lines) => {
        const feedback = `Change only these rubric points, as I approved from the rubric check: ${lines.map((line) => `${line.label} from ${line.from} to ${line.points} points`).join("; ")}. Keep everything else as it is.`;
        await api(`${base(draft.id)}/revisions`, "POST", { request_id: crypto.randomUUID(), based_on: revision.id, feedback, mode: result.mode });
        toast("Asked for a revision that makes this change.");
        await renderPage(root, draft.id, true);
      },
      onFixUndone: (_, rest) => { for (const n of handled) if (!rest.has(n)) page.unfixed.add(n); },
      guidance: (qid) => entries.get(qid), setClarification() {}, questionLabel: (q) => `${q.exam_version ? `Version ${q.exam_version} · ` : ""}${q.title}`,
      // "draft:<problem>:<version>": the problem's own rubric lines, on that version, with the guidance under each.
      editLabel: "Show its rubric lines", editQuestion: (qid) => showRubric(root, qid) });
    oneList();
    const requestId = crypto.randomUUID();  // one per drawn form: the same answers sent again are the same revision
    form.onsubmit = async (event) => {
      event.preventDefault();
      collect(root);
      const responses = [...page.answers].filter(([, text]) => text.trim()).map(([concern, text]) => ({ concern, response: text.trim() })).sort((a, b) => a.concern - b.concern);
      await busy(form, async () => {
        const unfixed = [...page.unfixed].filter((n) => check.result.suspected_errors?.[n]?.fix).sort((a, b) => a - b);
        await api(`${base(draft.id)}/rubric-answers`, "POST", { request_id: requestId, based_on: revision.id, check_sha256: check.sha256, responses, ...(unfixed.length ? { unfixed } : {}) });
        toast("The reviewer is applying your answers. This page shows the result when it is ready.");
        await renderPage(root, draft.id, true);
      });
    };
  }

  function showRubric(root, qid) {
    const [, number, letter] = String(qid).split(":");
    if (letter && letter !== page.version && page.draft.versions.includes(letter)) { collect(root); page.version = letter; draw(root, page.draft); }
    const fold = root.querySelector(`[data-problem="${CSS.escape(number)}"] details.review-fold:has(.quiz-draft-rubric)`);
    if (!fold) return;
    fold.open = true;
    fold.scrollIntoView({ block: "center", behavior: scrollBehavior() });
  }

  // ---------- approving ----------
  async function openApproval(draft) {
    const intent = captureDialogIntent(dialog());
    const options = await api(`${base(draft.id)}/approval`);
    if (!intent()) return;
    const revision = latest(draft);
    if (!revision) return;
    // The answer keys this revision shows beside its problems, which this approval saves (2026-10-11); null when it read none.
    const keys = keyCounts(revision.result);
    // The rubric check this revision ended with (2026-10-11, "Rubric questions at draft review"): finished, it goes to the grading
    // plan with the approval; with no decision open, one unticked box offers to record the rubric finalization with it.
    const check = revision.result?.rubric_check, checked = revision.summary?.rubric_check;
    const carried = checked?.status === "complete" && Boolean(check?.sha256), open = carried ? checked.decisions + checked.overflow : 0;
    // The term's planned rows no record follows yet (SCHEDULING "CLASS SCHEDULE"): the one this quiz stands for gives its date.
    const planned = await openRows(api, options.term.id, "given");
    if (!intent()) return;
    const requestId = crypto.randomUUID();
    // One stack for the ticked sections (requirement, 2026-10-01): any copy can go to a student of any of them.
    const ticks = options.sections.map((s) => `<label class="checkbox-field"><input type="checkbox" name="section" value="${escape(s.id)}" data-students="${s.students}" data-number="${escape(s.number)}" checked> Section ${escape(s.number)}<span class="quiz-draft-muted"> · ${plural(s.students, "student")}</span></label>`).join("");
    const copies = options.versions.map((v) => `<td><input type="number" min="0" max="500" step="1" value="${options.copies[v]}" aria-label="Copies of Version ${v}" data-copies="${v}"></td>`).join("");
    showDialog(`<form id="quiz-draft-approve" class="quiz-draft-form" novalidate>${dialogHeader("Approve and send to printing", escape(`${draft.title} · revision ${revision.number} · ${options.term.label}`))}
      <div class="dialog-body">
        <p class="quiz-draft-lead" data-approve-summary></p>
        ${disclosureMarkup({ label: "What approving sets up", body: `<p>${escape(`${draft.title} becomes an assignment, and every version’s questions and rubrics, its templates and its answer areas (taken from where each problem is printed) are confirmed, and the rubrics confirmed for the rubric review.${keys?.read ? " Each question is saved with the answer key shown beside its problem; edit one in Key & Rubric Finalization." : ""}${carried ? " The rubric check made on this page becomes the grading plan’s check, so it is not run again." : ""}`)}</p>` })}
        <div class="quiz-draft-row"><label class="field quiz-draft-grow">Assignment title<input name="title" required maxlength="240" value="${escape(options.assignment.title)}"></label>
          <label class="field">Key<input name="key" required maxlength="160" value="${escape(options.assignment.key)}"></label></div>
        <fieldset class="quiz-draft-sections"><legend>Sections</legend>${ticks}</fieldset>
        <div class="quiz-draft-given"><label class="field">Given on (optional)<input type="date" name="given_on" aria-describedby="quiz-given-hint"></label>
          <p class="field-hint" id="quiz-given-hint">The campus date the students write it: it puts the quiz on Home’s Coming up, the Calendar and its Exams page.</p></div>
        <table class="quiz-draft-copies"><caption>Copies of each version</caption><thead><tr>${options.versions.map((v) => `<th scope="col">Version ${v}</th>`).join("")}</tr></thead><tbody><tr>${copies}</tr></tbody></table>
        <p class="quiz-draft-muted"><span data-copy-total></span> One unnamed stack for any student of the ticked sections: nothing printed says the version, and the name and UIN written on page 1 say whose paper it is.</p>
        <label class="checkbox-field"><input type="checkbox" name="own_copies" aria-describedby="quiz-own-copies-hint"> Each student must use their own copy: report any stamp that comes back twice</label>
        <p class="field-hint" id="quiz-own-copies-hint">The papers are still graded. This only catches copying when both papers come back.</p>
        <label class="checkbox-field"><input type="checkbox" name="confirm"> I checked every version and its rubrics.</label>
        ${carried && !open ? `<label class="checkbox-field" data-finalize><input type="checkbox" name="finalize"> ${escape(finalizeLabel(checked))}</label>` : ""}
        <p class="form-error" role="alert" hidden></p>
      </div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Approve and send to printing</button></footer></form>`);
    const form = dialog().querySelector("#quiz-draft-approve");
    const active = dialogSession(form, dialog());
    const ticked = () => [...form.querySelectorAll("input[name=section]:checked")];
    const counts = () => Object.fromEntries([...form.querySelectorAll("[data-copies]")].map((input) => [input.dataset.copies, Number(input.value)]));
    // The lead says what approving will do as the window stands (2026-10-06): what it prints, for whom, and what still waits.
    const total = () => {
      const n = Object.values(counts()).reduce((a, b) => a + (Number.isInteger(b) ? b : 0), 0);
      form.querySelector("[data-copy-total]").textContent = `${plural(n, "copy", "copies")} in all.`;
      form.querySelector("[data-approve-summary]").textContent = approvalLead({ copies: n, versions: options.versions, sections: ticked().map((input) => input.dataset.number), keys,
        rubric: carried ? { open, finalize: Boolean(form.elements.finalize?.checked) } : null });
    };
    const prefill = attachPrefill({ rows: planned, before: form.querySelector(".quiz-draft-given"), dateInput: form.elements.given_on,
      title: () => form.elements.title.value, sections: () => ticked().map((input) => input.value), escape });
    // Until a count is typed, the copies follow the ticked sections: their students shared by the versions, the first ones one more.
    let typed = false;
    const share = () => {
      if (typed) return;
      const students = ticked().reduce((sum, input) => sum + Number(input.dataset.students), 0), inputs = [...form.querySelectorAll("[data-copies]")];
      inputs.forEach((input, index) => { input.value = String(Math.floor(students / inputs.length) + (index < students % inputs.length ? 1 : 0)); });
    };
    total();
    form.addEventListener("input", (event) => { if (event.target.matches("[data-copies]")) typed = true; else share(); total(); prefill.refresh(); });
    // A refusal for the missing tick goes once the tick is given.
    form.elements.confirm.addEventListener("change", () => { if (form.elements.confirm.checked) fail(form, ""); });
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (!form.elements.confirm.checked) { fail(form, "Tick that you checked every version and its rubrics."); return; }
      const chosen = ticked().map((input) => input.value), copies = counts();
      if (!chosen.length) { fail(form, "Tick the sections this quiz is for."); return; }
      if (Object.values(copies).some((n) => !Number.isInteger(n) || n < 0 || n > 500)) { fail(form, "Copies are whole numbers from 0 to 500."); return; }
      // 'Given on' (requirement, 2026-10-02): the date input's own YYYY-MM-DD, sent as typed (never through a time zone); empty sends nothing.
      const givenOn = form.elements.given_on.value.trim();
      if (givenOn && !/^\d{4}-\d{2}-\d{2}$/.test(givenOn)) { fail(form, "Enter the date it is given as YYYY-MM-DD, or leave it empty."); return; }
      await busy(form, async () => {
        await api(`${base(draft.id)}/approve`, "POST", { request_id: requestId, revision_id: revision.id, confirmation: "approve",
          title: form.elements.title.value.trim(), key: form.elements.key.value.trim(), sections: chosen, copies, ...(givenOn ? { given_on: givenOn } : {}),
          ...(prefill.chosen() ? { planned_item: prefill.chosen() } : {}),
          ...(form.elements.own_copies.checked ? { own_copies: true } : {}),  // QUIZZES "SHARED STAMPS"
          // The box ticked: this approval is also the instructor's confirmation of the rubric check shown (2026-10-11).
          ...(form.elements.finalize?.checked ? { finalization: { confirm: true, check_sha256: check.sha256 } } : {}),
          // This page showed each problem's answer key, so this approval is the instructor's save of them (2026-10-11); a tab opened
          // before the page did sends nothing and no key is saved.
          save_keys: true });
        if (!active()) return;
        dialog().close();
        toast("Approved. Faculty is setting up the assignment, its grading and the print jobs.");
        onChanged?.();
        const root = document.querySelector("#quiz-draft-root");
        if (root) await renderPage(root, draft.id, true);
      });
    };
  }

  return { load, cards, openCreate, renderPage, readFailed, stop };
}
